"""两份 ASS 用 libass（ffmpeg 的 subtitles 滤镜）逐帧真渲、比像素，报出有差异的帧——改 ASS 的时间 / 效果标签时的验收工具。

    python dev_tools/ass_framediff.py a.ass b.ass                       # 只渲两份事件不同的时段（各自前后多 0.5 s）
    python dev_tools/ass_framediff.py a.ass b.ass --start 12 --end 20   # 指定时段（秒，ASS 的时间轴）
    python dev_tools/ass_framediff.py a.ass b.ass --dump tmp/fd          # 每段差异的第一帧存成 A | B | 差异 三联图
    python dev_tools/ass_framediff.py --selftest                         # 两次回归的形状：修之前的写法要报差异，现在的写法要逐帧相同

**为什么要它**（2026-09-28 复盘）：09-26（预览切段时淡入跨切点从全透明重来）和 09-27（阶段 4 分段后 `\\t` 的结束时刻减到正好 0，
libass 把 0 读成"到事件结尾"）两次回归，守卫都只断言标签字符串，把画错的结果钉成了"对"。标签字符串说不清 libass 怎么画，
所以改 `\\t` / `\\fad` / `\\fade` / `\\move` 或把一条事件切成几段时，**以逐帧对照为准**：拿"改之前的写法"或"不切的一整条"当参照，
新写法渲出来要逐帧相同（或差异只出现在说得清的帧上）。字符串守卫只钉写法。

- 画布 = 第一份 ASS 的 `PlayResX × PlayResY`（`--size` 可改），纯色底（`--bg`）；两份在同一画布、同一帧时刻上渲。
- 差异像素 = 任一通道差 > `--thr`（默认 8，滤掉抗锯齿的末位抖动）。
- 帧时刻按 `--fps` 等距取。**事件的起止正好落在采样时刻上时**，两种写法可以合法地差这一帧（libass 的事件区间是左闭右开，
  切段的端点又经过 ASS 的 1/100 秒取整）；判读时先看差异帧是不是都贴在某个事件端点上。
- 默认只渲两份 ASS 的 `Dialogue` 行（按整行文本比）不同的那些事件的时段；完全相同的两份直接报相同、不渲。
- 退出码：0 = 逐帧相同，1 = 有差异帧，2 = 用法或渲染出错。只用 CPU。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

DIALOGUE = re.compile(r"^Dialogue:\s*[^,]*,(\d+):(\d+):(\d+)\.(\d+),(\d+):(\d+):(\d+)\.(\d+),")
PAD = 0.5


def sec(h, m, s, cs) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def events(text: str) -> list[tuple[float, float, str]]:
    out = []
    for line in text.splitlines():
        m = DIALOGUE.match(line)
        if m:
            out.append((sec(*m.groups()[:4]), sec(*m.groups()[4:]), line.strip()))
    return out


def play_res(text: str) -> tuple[int, int]:
    def get(k: str, d: int) -> int:
        m = re.search(rf"^{k}:\s*(\d+)", text, re.M)
        return int(m[1]) if m else d
    return get("PlayResX", 384), get("PlayResY", 288)


def merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def changed_spans(ta: str, tb: str) -> list[tuple[float, float]]:
    """两份 ASS 里只在一边出现的 Dialogue 行（整行文本比）的时段，前后各加 `PAD` 秒、并起来。
    样式、`[Script Info]` 不同而事件全同的时候也要渲——这时取全部事件的时段。"""
    ea, eb = events(ta), events(tb)
    sa, sb = {e[2] for e in ea}, {e[2] for e in eb}
    diff = [e for e in ea if e[2] not in sb] + [e for e in eb if e[2] not in sa]
    if not diff and ta.split("[Events]")[0] != tb.split("[Events]")[0]:
        diff = ea + eb
    return merge([(max(0.0, a - PAD), b + PAD) for a, b, _ in diff])


def frames(ass: Path, size: tuple[int, int], t0: float, t1: float, fps: float, bg: str):
    """在 [t0, t1) 上按 fps 渲，逐帧给 (时刻, RGB 数组)。帧的 pts 平移到 t0 起，subtitles 滤镜按 pts 取字。"""
    W, H = size
    vf = f"setpts=PTS+{t0}/TB,subtitles={ass.name}"
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"color=c={bg}:s={W}x{H}:r={fps}:d={t1 - t0}",
           "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ass.parent)
    n, k = W * H * 3, 0
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield t0 + k / fps, np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            k += 1
    finally:
        p.stdout.close()
        err = p.stderr.read().decode(errors="replace")
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg 渲 {ass.name} 失败：{err.strip()[:300]}")


def compare(a: Path, b: Path, spans: list[tuple[float, float]] | None = None, fps: float = 25.0, thr: int = 8,
            size: tuple[int, int] | None = None, bg: str = "0x808080", dump: Path | None = None) -> list[tuple[float, int]]:
    """逐帧比，返回有差异的帧 `[(时刻, 差异像素数)]`。`spans=None` = 自动挑两份事件不同的时段。"""
    ta, tb = (p.read_text(encoding="utf-8-sig") for p in (a, b))
    size = size or play_res(ta)
    spans = changed_spans(ta, tb) if spans is None else spans
    out: list[tuple[float, int]] = []
    with tempfile.TemporaryDirectory(prefix="ass-framediff-") as d:
        pa, pb = Path(d) / "a.ass", Path(d) / "b.ass"       # 拷进同一个目录：subtitles 滤镜的路径转义在 Windows 上很脆
        shutil.copyfile(a, pa)
        shutil.copyfile(b, pb)
        for t0, t1 in spans:
            first = True
            for (t, fa), (_, fb) in zip(frames(pa, size, t0, t1, fps, bg), frames(pb, size, t0, t1, fps, bg)):
                mask = (np.abs(fa.astype(np.int16) - fb.astype(np.int16)).max(axis=2) > thr)
                npx = int(mask.sum())
                if npx:
                    out.append((t, npx))
                    if dump is not None and (first or out[-2][0] < t - 1.5 / fps):
                        save_triptych(dump, t, fa, fb, mask)
                    first = False
    return out


def save_triptych(dump: Path, t: float, fa, fb, mask) -> None:
    from PIL import Image
    dump.mkdir(parents=True, exist_ok=True)
    m = np.zeros_like(fa)
    m[mask] = (255, 0, 255)
    Image.fromarray(np.concatenate([fa, fb, m], axis=1)).save(dump / f"diff-{t:010.3f}.png")


def ranges(diffs: list[tuple[float, int]], fps: float) -> list[tuple[float, float, int, int]]:
    """相邻的差异帧并成一段：(起, 止, 帧数, 单帧最多像素)。"""
    out: list[list] = []
    for t, n in diffs:
        if out and t - out[-1][1] <= 1.5 / fps:
            out[-1][1], out[-1][2], out[-1][3] = t, out[-1][2] + 1, max(out[-1][3], n)
        else:
            out.append([t, t, 1, n])
    return [tuple(x) for x in out]


def report(diffs, spans, fps) -> None:
    n_frames = sum(round((b - a) * fps) for a, b in spans)
    print(f"渲了 {len(spans)} 个时段、约 {n_frames} 帧（{fps:g} fps）；有差异的帧 {len(diffs)}")
    for a, b, k, mx in ranges(diffs, fps):
        print(f"  {a:9.3f} – {b:9.3f} s  {k:4d} 帧  单帧最多 {mx} 像素")


# ---- 自检：两次回归的形状 ------------------------------------------------------------------------------------------
W, H = 640, 160


def doc(events_: list[tuple[float, float, str]]) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from flowocr.output import export
    st = f"Style: s,{export.FONT_CN},40,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1"
    clock = lambda t: f"{int(t // 3600)}:{int(t // 60 % 60):02d}:{int(t % 60):02d}.{round(t * 100) % 100:02d}"  # noqa: E731
    ev = [f"Dialogue: 0,{clock(a)},{clock(b)},s,,0,0,0,,{body}" for a, b, body in events_]
    return export.ass_doc(W, H, [st], ev)


def selftest_cases() -> list[tuple[str, str, str, bool]]:
    """(名字, 参照 ASS, 被验 ASS, 该不该逐帧相同)。时刻都错开采样网格（起点 0.01 s、fps 40）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    import preview
    from flowocr.typeset import core
    pos = "\\an5\\pos(320,60)"
    cases = []

    # 09-26：预览切段（slice_ass），切点落在淡入中间。修之前的写法 `\fad(淡入 − 切点, 0)` 在后一段从全透明重来
    whole = doc([(0.01, 3.01, "{%s\\fad(800,400)}淡入被切开的这一行" % pos), (0.31, 2.01, "{\\an5\\pos(320,120)}切点")])
    cases.append(("09-26 切段·现在的 slice_ass vs 不切", whole, preview.slice_ass(whole), True))
    def old_cut(m: re.Match, cut: int, seg: int, dur: int) -> str:    # 2a7fb51 之前的写法
        fin, fout = (int(v) for v in m[1].split(","))
        return f"\\fad({max(0, fin - cut)},{fout if cut + seg >= dur else 0})"
    real = preview._cut_fade
    preview._cut_fade = old_cut
    try:
        old = preview.slice_ass(whole)
    finally:
        preview._cut_fade = real
    cases.append(("09-26 切段·修之前的 \\fad(淡入−切点) vs 不切", whole, old, False))

    # 09-27：阶段 4 把一行按轨迹切成几段（core.shift_times）。\t 的结束时刻减到正好 0 时 libass 读成"到段尾"
    src = "\\t(0,1000,\\1c&H0000FF&)"                 # 一个 tag 块的内容（不带花括号），shift_times 收的就是它
    whole2 = doc([(0.01, 3.01, "{%s%s}变色在第一段里就变完" % (pos, src))])
    cuts = [(0, 1000), (1000, 2000), (2000, 3000)]

    def segs(shift) -> str:
        return doc([(0.01 + a / 1000, 0.01 + b / 1000, "{%s%s}变色在第一段里就变完" % (pos, shift(src, a, 3000)))
                    for a, b in cuts])

    def old_shift(block: str, a: int, dur: int) -> str:        # 502cac8 之前：直接减，不换掉已变完的 \t
        m = re.fullmatch(r"\\t\((\d+),(\d+),(.*)\)", block)
        return "\\t(%d,%d,%s)" % (int(m[1]) - a, int(m[2]) - a, m[3])
    cases.append(("09-27 \\t·现在的 shift_times 切三段 vs 不切", whole2, segs(core.shift_times), True))
    cases.append(("09-27 \\t·修之前（结束时刻减成 0）切三段 vs 不切", whole2, segs(old_shift), False))
    return cases


def selftest(fps: float = 40.0) -> int:
    bad = 0
    with tempfile.TemporaryDirectory(prefix="ass-framediff-self-") as d:
        for k, (name, ref, got, same) in enumerate(selftest_cases()):
            pa, pb = Path(d) / f"{k}a.ass", Path(d) / f"{k}b.ass"
            pa.write_text(ref, encoding="utf-8")
            pb.write_text(got, encoding="utf-8")
            diffs = compare(pa, pb, [(0.0, 3.2)], fps=fps)
            ok = (not diffs) == same
            bad += not ok
            span = f"，差异在 {diffs[0][0]:.3f}–{diffs[-1][0]:.3f} s" if diffs else ""
            print(f"  {'PASS' if ok else 'FAIL'}  {name}：差异帧 {len(diffs)}/{round(3.2 * fps)}{span}"
                  f"（应当{'逐帧相同' if same else '报出差异'}）")
    print("自检通过" if not bad else f"**自检 {bad} 项没过**")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", nargs="?")
    ap.add_argument("b", nargs="?")
    ap.add_argument("--start", type=float, help="时段起点（秒）；不给 = 自动挑两份事件不同的时段")
    ap.add_argument("--end", type=float)
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--thr", type=int, default=8, help="任一通道差超过它才算差异像素")
    ap.add_argument("--size", default="", help="画布 WxH；默认第一份的 PlayResX x PlayResY")
    ap.add_argument("--bg", default="0x808080")
    ap.add_argument("--dump", default="", help="每段差异的第一帧存成 A | B | 差异 三联图到这个目录")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.a and a.b):
        ap.error("要两份 ASS（或 --selftest）")
    if (a.start is None) != (a.end is None):
        ap.error("--start 和 --end 要一起给")
    size = tuple(int(v) for v in a.size.lower().split("x")) if a.size else None
    spans = [(a.start, a.end)] if a.start is not None else None
    try:
        if spans is None:
            spans = changed_spans(*(Path(p).read_text(encoding="utf-8-sig") for p in (a.a, a.b)))
            if not spans:
                print("两份 ASS 的样式头和全部 Dialogue 行逐行相同：不用渲")
                return 0
        diffs = compare(Path(a.a), Path(a.b), spans, a.fps, a.thr, size, a.bg, Path(a.dump) if a.dump else None)
    except (RuntimeError, OSError) as e:
        print(f"出错：{e}", file=sys.stderr)
        return 2
    report(diffs, spans, a.fps)
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
