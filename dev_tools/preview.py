"""叠加字幕的预览视频：阶段 2 的产物 -> 预设出叠加 ASS -> 截原视频的一段，字幕烧进去或作为字幕轨，可混入另下的音轨。

    python dev_tools/preview.py out/v/v-tracks.json --preset default --out tmp/v-default.mp4
    python dev_tools/preview.py out/v/v-matched.json --preset dev --opt lang=cn \\
        --audio v.audio.webm --audio-offset 9.972 --out tmp/v-cn-dev.mp4
    python dev_tools/preview.py out/v/v-tracks.json --preset dev --mode ass --out tmp/v-dev.mkv   # 字幕轨，播放器里开关
    python dev_tools/preview.py tmp/x.ass --video v.mp4 --start 0 --duration 0 --out tmp/x.mp4   # 现成的 ASS、整段

- **两种出法，二选一**（owner 2026-09-25：两样都给，播放器会把外挂的 ASS 叠在烧好的字上）：
  - `--mode burn`（默认）：字烧进画面，出 `.mp4`；ASS 只在临时目录里，不留在输出旁边。
  - `--mode ass`：画面不烧字，出 `.mkv`，把平移到片段时间轴的 ASS 作为字幕轨封进去（默认开）。
    字幕轨在所有事件的起止处切成同起同止的段（`slice_ass`）：VLC 3 碰上时间重叠的 ASS 事件会冻画面、漏画台词。
    两种出法是同一份 ASS、同一个时间窗；烧录由 ffmpeg 的 libass 画，字幕轨由播放器自己的渲染器画，样子可能有差别。
- **产物**：`*-tracks.json` / `*-matched.json` 走 `flowocr.output.render` 同一套预设（`--preset`、`--opt` 同它）；
  也可以直接给一份 `.ass`（原片时间轴）。
- **视频**默认从产物的 provenance 找（matched -> 它的 tracks -> tracks 记的 `video`）；`--video` 可改。
- **回抠**：建轨默认（`--refine auto`）已用 obs 证据结算回抠。没回抠的 tracks（`--refine off`、obs 没证据）只有采样级的打字机判定，
  一行 1 秒内打完的素材（鸣潮、yuka）不会逐字显出，这时会提示。
- **时间窗**：`--start auto`（默认）挑 ASS 事件开始得最多的 `--duration` 秒；`--duration 0` 是从 `--start` 到片尾。
  事件多不等于有看头：满屏 UI 的菜单（每个按钮一条）常常比剧情对话密，要看对话就先翻主轨、用 `--start` 指定。
  要拿几份预览对比（同一段、不同预设 / 语言），给同一个 `--start`。
- **音轨**：只有视频流的素材可以另给 `--audio`。`--audio-offset` = 视频 0 秒落在音轨里的第几秒（音轨先开始为正）。
  分开截的音轨常常和视频不同起点，**给之前先量**（在音轨里定位一段精确截取的音频），别猜。
- **坑**：`-ss` 放在 `-i` 前面会把时间轴归零，`subtitles` 滤镜就画成 0 秒那一刻的字（每一帧看着都正常）。
  烧录用 `-copyts` 保留原时间轴烧字，烧完再把视频、音频的时间轴归零；字幕轨则先把 ASS 平移到片段时间轴。
- 只用 CPU（libx264），不占 GPU。
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from flowocr import extensions
from flowocr.artifacts import matchedio, tracksio
from flowocr.output import render
from flowocr.provenance import local

ASS_TIME = re.compile(r"^Dialogue:\s*[^,]*,(\d+):(\d+):(\d+)\.(\d+),")
# 事件内部相对事件起点的时刻：逐字显出的 \t(t1,t2,…)、在动的框的 \move(…,t1,t2)、淡入淡出 \fad / \fade
TAG_T = re.compile(r"\\t\((\d+),(\d+),")
TAG_MOVE = re.compile(r"\\move\(([^,()]+),([^,()]+),([^,()]+),([^,()]+),(\d+),(\d+)\)")
TAG_FADE = re.compile(r"\\fade?\((-?\d+(?:,-?\d+)*)\)")          # \fad(入,出) 和七参数 \fade(a1,a2,a3,t1,t2,t3,t4)


def ass_seconds(h: str, m: str, s: str, cs: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def ass_clock(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_starts(ass: Path) -> list[float]:
    """ASS 里每条 Dialogue 的开始秒数。"""
    out = []
    for line in ass.read_text(encoding="utf-8-sig").splitlines():
        m = ASS_TIME.match(line)
        if m:
            out.append(ass_seconds(*m.groups()))
    return out


def densest_window(starts: list[float], duration: float, total: float) -> float:
    """开始时刻落在窗里最多的那个窗的起点（按整秒挪）；同样多取最早的。整场几万条也是秒级（二分）。"""
    ss = sorted(starts)
    best, best_n = 0.0, -1
    for t in range(0, max(1, int(total - duration) + 1)):
        n = bisect.bisect_left(ss, t + duration) - bisect.bisect_left(ss, t)
        if n > best_n:
            best, best_n = float(t), n
    return best


def _shift_t(m: re.Match, cut: int) -> str:
    """\\t(t1,t2,…) 往前扣 cut 毫秒。libass 把 t2 == 0 当成"到事件结束"——已经完成的变换扣完成了 (0,0)
    就会变成拖满整条的慢变化（打字机已显出的字慢慢淡入），所以夹成 (0,1)：一开场就到位。"""
    a, b = max(0, int(m[1]) - cut), max(1, int(m[2]) - cut)
    return f"\\t({min(a, b - 1)},{b},"


def _shift_move(m: re.Match, cut: int) -> str:
    """\\move(x1,y1,x2,y2,t1,t2) 往前扣 cut 毫秒：已经走过的那段按比例插出新起点；走完了就是 \\pos 终点。
    （t1、t2 都 ≤ 0 在 libass 里是"整条事件都在走"，不能直接扣成那样。）"""
    x1, y1, x2, y2 = (float(v) for v in m.groups()[:4])
    t1, t2 = int(m[5]), int(m[6])
    if cut >= t2:
        return f"\\pos({x2:g},{y2:g})"
    if cut > t1:
        k = (cut - t1) / (t2 - t1)
        x1, y1, t1 = x1 + (x2 - x1) * k, y1 + (y2 - y1) * k, cut
    return f"\\move({x1:g},{y1:g},{x2:g},{y2:g},{t1 - cut},{t2 - cut})"


def _fade_curve(args: list[int], dur_ms: int) -> tuple[int, ...]:
    """\\fad / \\fade → libass 的透明度曲线 (a1,a2,a3,t1,t2,t3,t4)（相对事件起点的毫秒；透明度 255 全透明 → 0 不透明）。
    淡入淡出重叠（事件比淡入 + 淡出还短）时 libass 的曲线在 t2 处跳变，七参数表达不了它的切段：先规整成淡出从淡入结束处开始
    （叠加 ASS 的淡入淡出由 `flowocr.output.script.effect_times` 判，淡出从全字出现之后才开始，构造上不重叠）。"""
    if len(args) == 2:
        a1, a2, a3, t1, t2, t3, t4 = 255, 0, 255, 0, args[0], dur_ms - args[1], dur_ms
    else:
        a1, a2, a3, t1, t2, t3, t4 = args
    t3 = max(t3, t2)
    return a1, a2, a3, t1, t2, t3, max(t4, t3)


def fade_alpha(curve: tuple[int, ...], t: float) -> float:
    """曲线在事件内时刻 t（毫秒）的透明度，照 libass 的 `interpolate_alpha`。"""
    a1, a2, a3, t1, t2, t3, t4 = curve
    if t < t1:
        return a1
    if t < t2:
        return a1 + (a2 - a1) * (t - t1) / (t2 - t1)
    if t < t3:
        return a2
    if t < t4:
        return a2 + (a3 - a2) * (t - t3) / (t4 - t3)
    return a3


def _cut_fade(m: re.Match, cut: int, seg_ms: int, dur_ms: int) -> str:
    """原事件（长 dur_ms）里从 cut 毫秒起、长 seg_ms 的一段：七参数 \\fade 写出**同一条透明度曲线在这一段上的样子**——
    切点落在淡入 / 淡出中间就从当时的透明度接着淡，淡出落在段外就停在段尾那一刻的透明度（窗尾截掉的不会凭空淡出）。
    平移（`shift_ass`）和切段（`slice_ass`）都走这里，而且认自己写出的 \\fade，所以平移后再切段也是同一条曲线。"""
    args = [int(v) for v in m[1].split(",")]
    if len(args) not in (2, 7):
        return m[0]
    if cut <= 0 and seg_ms >= dur_ms:
        return m[0]
    c = _fade_curve(args, dur_ms)
    u = [min(max(tk - cut, 0), seg_ms) for tk in c[3:]]
    b1, b2, b3 = (round(fade_alpha(c, cut + u[i])) for i in (0, 1, 3))
    return f"\\fade({b1},{b2},{b3},{u[0]},{u[1]},{u[2]},{u[3]})"


def shift_ass(text: str, start: float, duration: float) -> str:
    """把原片时间轴的 ASS 平移到 [start, start + duration) 这一段的时间轴：窗外的事件丢掉；
    跨在窗口起点上的事件起点截到 0，事件内部相对起点的时刻（\\t / \\move）跟着往前扣，
    已经完成的变换一开场就到位、走到一半的移动从半路接着走（`_shift_t` / `_shift_move`）；淡入淡出按同一条透明度曲线截（`_cut_fade`）。"""
    out = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            out.append(line)
            continue
        head, rest = line.split(":", 1)
        f = rest.split(",", 9)
        t0 = ass_seconds(*re.match(r"\s*(\d+):(\d+):(\d+)\.(\d+)", f[1]).groups()) - start
        t1 = ass_seconds(*re.match(r"\s*(\d+):(\d+):(\d+)\.(\d+)", f[2]).groups()) - start
        if t1 <= 0 or t0 >= duration:
            continue
        cut = max(0, int(round(-t0 * 1000)))    # 起点截掉的毫秒
        a, b = max(0.0, t0), min(t1, duration)
        body = f[9]
        if cut:
            body = TAG_T.sub(lambda m: _shift_t(m, cut), body)
            body = TAG_MOVE.sub(lambda m: _shift_move(m, cut), body)
        seg, dur = int(round((b - a) * 1000)), int(round((t1 - t0) * 1000))
        body = TAG_FADE.sub(lambda m: _cut_fade(m, cut, seg, dur), body)
        f[1], f[2], f[9] = ass_clock(a), ass_clock(b), body
        out.append(head + ":" + ",".join(f))
    return "\n".join(out) + "\n"


def slice_ass(text: str) -> str:
    """在全部事件的起止时刻切开时间轴：每条事件切成首尾相接的几段，任一时刻在屏的事件都同起同止。
    **为 VLC 3**（2026-09-25 实测）：它把 mkv 里的每条 ASS 事件当成一张字幕画面，时间上重叠的就画不对——
    早开始、晚结束的一条（贯穿全片的常驻 UI）把画面冻在它开始那一刻，后来的台词漏画；同起同止的一组画得对。
    段内相对起点的时刻照 `shift_ass` 的规矩扣（`_shift_t` / `_shift_move`；淡入淡出按同一条透明度曲线截，`_cut_fade`）。
    按 libass 画的播放器（mpv、ffmpeg 的 subtitles 滤镜）上，切开前后画出来一样（守卫按 libass 的透明度算式逐毫秒核
    原文 / 平移 / 切段 / 平移后切段四种）。已知不等价：走到一半的 \\t（不是打字机那种 1 毫秒的）切开后从段首重新变。"""
    head, events = [], []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            head.append(line)
            continue
        f = line.split(":", 1)[1].split(",", 9)
        s = ass_seconds(*re.match(r"\s*(\d+):(\d+):(\d+)\.(\d+)", f[1]).groups())
        e = ass_seconds(*re.match(r"\s*(\d+):(\d+):(\d+)\.(\d+)", f[2]).groups())
        events.append((s, e, f))
    cuts = sorted({round(t, 2) for s, e, _ in events for t in (s, e)})
    out = []
    for s, e, f in events:
        lo, hi = bisect.bisect_right(cuts, s), bisect.bisect_left(cuts, e)
        bounds = [s, *cuts[lo:hi], e]
        for a, b in zip(bounds, bounds[1:]):
            cut = int(round((a - s) * 1000))
            body = f[9]
            if cut:
                body = TAG_T.sub(lambda m: _shift_t(m, cut), body)
                body = TAG_MOVE.sub(lambda m: _shift_move(m, cut), body)
            seg, dur = int(round((b - a) * 1000)), int(round((e - s) * 1000))
            body = TAG_FADE.sub(lambda m: _cut_fade(m, cut, seg, dur), body)
            g = list(f)
            g[1], g[2], g[9] = ass_clock(a), ass_clock(b), body
            out.append("Dialogue:" + ",".join(g))
    return "\n".join(head + out) + "\n"


def tracks_of(doc: dict) -> dict | None:
    """产物对应的 tracks：tracks 就是它自己，matched 按 provenance 找回来。"""
    if doc.get("schema") == matchedio.SCHEMA:
        tp = local(matchedio.tracks_path(doc))
        if not tp.is_file():
            return None
        doc = json.loads(tp.read_text(encoding="utf-8"))
    return doc if doc.get("schema") == tracksio.SCHEMA else None


def video_of(tracks: dict | None) -> Path | None:
    """tracks provenance 里记的原视频。"""
    v = ((tracks or {}).get("provenance") or {}).get("video")
    return local(v) if v else None


def probe(path: Path) -> tuple[float, float, bool]:
    """(时长, 容器起始时间戳, 有没有音轨)。起始时间戳不为 0 的容器（TS 一类）上，OCR 记的是绝对 pts，
    而 `-ss` 的位置从容器起点算：截取位置要减掉它，ASS 的时间轴才对得上。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,start_time:stream=codec_type",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    fmt = info.get("format") or {}
    return (float(fmt.get("duration") or 0), float(fmt.get("start_time") or 0),
            any(s.get("codec_type") == "audio" for s in info.get("streams") or []))


def render_ass(src: Path, preset: str, opts: dict, outdir: Path) -> Path:
    """跑预设，返回它写出的那份 ASS。叠加预设写两份（字幕稿 `*.script.ass` + 最终 ASS），预览最终那份；
    只写字幕稿的（`--preset script`）预览字幕稿本身（看的是 Aegisub 里打开的样子）。"""
    mod = extensions.load(preset, "preset")
    doc = render.load_doc(src)
    render.check_accepts(mod, doc, preset)
    asses = [Path(p) for p in mod.render(doc, outdir, opts) if str(p).endswith(".ass")]
    finals = [p for p in asses if not p.name.endswith(".script.ass")] or asses
    if len(finals) != 1:
        raise SystemExit(f"预设 {preset} 应当写出恰好一份最终 ASS，写出的是 {asses}——预览只用叠加 ASS")
    return finals[0]


def cut_clip(video: Path, ass: Path, out: Path, start: float, duration: float,
             audio: Path | None, offset: float, crf: int, mode: str) -> None:
    """截 [start, start + duration)（ASS 的时间轴）：burn 把 ASS 烧进画面；ass 把平移后的 ASS 封成字幕轨。
    声音：给了 `--audio` 用它，没给而原片自己有音轨就带原片的。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    _, t0, has_audio = probe(video)
    seek = max(0.0, start - t0)          # `-ss` 从容器起点算
    with tempfile.TemporaryDirectory() as d:
        # 滤镜语法里 Windows 盘符的冒号、括号都要转义：拷成固定名字、在临时目录里用相对名
        cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-stats_period", "10"]
        if mode == "burn":
            shutil.copyfile(ass, Path(d) / "overlay.ass")
            cmd += ["-copyts"]
            graph = "[0:v]subtitles=overlay.ass,setpts=PTS-STARTPTS[v]"
        else:
            shifted = shift_ass(ass.read_text(encoding="utf-8-sig"), seek + t0, duration)
            (Path(d) / "overlay.ass").write_text(slice_ass(shifted), encoding="utf-8")   # 切段：见 slice_ass（VLC 3）
            graph = "[0:v]null[v]"
        cmd += ["-ss", f"{seek:.3f}", "-i", str(video.resolve())]
        maps = ["-map", "[v]"]
        n_in = 1
        if audio:
            a0 = seek + offset
            if a0 < 0:
                raise SystemExit(f"音轨从视频的 {-offset:.3f} 秒才开始，窗起点 {start:.3f} 秒之前没有音频：把 --start 挪后")
            cmd += ["-ss", f"{a0:.3f}", "-i", str(audio.resolve())]
            graph += f";[{n_in}:a]asetpts=PTS-STARTPTS[a]"
            maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
            n_in += 1
        elif has_audio:                  # burn 开了 -copyts，不能直接 -map 0:a，也要归零
            graph += ";[0:a]asetpts=PTS-STARTPTS[a]"
            maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
        if mode == "ass":
            cmd += ["-i", "overlay.ass"]
            maps += ["-map", f"{n_in}:s", "-c:s", "ass", "-disposition:s:0", "default"]
        cmd += ["-filter_complex", graph, *maps]
        if duration > 0:
            cmd += ["-t", f"{duration:.3f}"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]
        if mode == "burn":
            cmd += ["-movflags", "+faststart"]
        cmd += [str(out.resolve())]
        r = subprocess.run(cmd, cwd=d)
        if r.returncode != 0:
            raise SystemExit(f"ffmpeg 失败（退出码 {r.returncode}）：{' '.join(cmd)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="*-tracks.json / *-matched.json（跑预设），或一份现成的 .ass（原片时间轴）")
    ap.add_argument("--out", required=True, help="输出：burn 是 .mp4，ass 是 .mkv")
    ap.add_argument("--mode", choices=("burn", "ass"), default="burn",
                    help="burn = 字烧进画面（默认）；ass = 不烧，ASS 作为字幕轨封进 mkv")
    ap.add_argument("--preset", default="default", help="同 flowocr.output.render（src 是 .ass 时不用）")
    ap.add_argument("--opt", action="append", default=[], metavar="K=V", help="给预设的选项，可多次")
    ap.add_argument("--video", default="", help="原视频；默认按产物 provenance 找")
    ap.add_argument("--start", default="auto", help="窗起点（秒），auto = ASS 事件最密的窗（默认）")
    ap.add_argument("--duration", type=float, default=120.0, help="窗长（秒，默认 120）；0 = 到片尾")
    ap.add_argument("--audio", default="", help="另下的音轨（素材只有视频流时）")
    ap.add_argument("--audio-offset", type=float, default=0.0,
                    help="视频 0 秒落在音轨里的第几秒（音轨先开始为正）；给之前先量，别猜")
    ap.add_argument("--crf", type=int, default=20, help="libx264 的 crf（默认 20）")
    a = ap.parse_args(argv)

    src, out = Path(a.src), Path(a.out)
    want = ".mp4" if a.mode == "burn" else ".mkv"
    if out.suffix.lower() != want:
        ap.error(f"--mode {a.mode} 出 {want}：{out}")
    opts = {}
    for kv in a.opt:
        if "=" not in kv:
            ap.error(f"--opt 要 K=V：{kv}")
        k, v = kv.split("=", 1)
        opts[k] = v
    with tempfile.TemporaryDirectory() as work:
        if src.suffix.lower() == ".ass":
            ass, tracks = src, None
        else:
            ass = render_ass(src, a.preset, opts, Path(work))      # 不写在输出旁边：免得播放器再外挂一遍
            tracks = tracks_of(json.loads(src.read_text(encoding="utf-8")))
            if tracks is not None and "text_effect" not in tracks:
                print("[preview] 注意：这份 tracks 没回抠（--refine off 或 obs 没证据）：打字机只按采样级的门判，"
                      "一行 1 秒内打完的素材不会逐字显出；要效果就用默认设置重跑阶段 1、再建轨")
        video = Path(a.video) if a.video else video_of(tracks)
        if not video or not video.is_file():
            raise SystemExit(f"找不到原视频（{video}）：用 --video 指定")
        total = probe(video)[0]
        starts = ass_starts(ass)
        if a.start == "auto":
            start = densest_window(starts, a.duration if a.duration > 0 else total, total)
        else:
            start = float(a.start)
        dur = a.duration if a.duration > 0 else total - start
        n = sum(1 for s in starts if start <= s < start + dur)
        print(f"[preview] {video.name} {start:.1f}–{start + dur:.1f} s（--start {start:g}），"
              f"窗里开始的 ASS 事件 {n} 条（全片 {len(starts)} 条，底板和字各算一条）；{a.mode}")
        cut_clip(video, ass, out, start, dur, Path(a.audio) if a.audio else None, a.audio_offset, a.crf, a.mode)
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
