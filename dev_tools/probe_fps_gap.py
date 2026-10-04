"""**采样缺口有多大**：拿高采样率的参考臂，逐事件去 2 fps 的 obs 里找（decode-buffer 计划 §8.1 第 1 步）。

只在 obs 层量，不经过聚类 / 匹配器——问的是"这一处的文字，2 fps 的采样读到没有、读全没有"。

**事件 = 参考臂的 run**（`build_runs`，参数取 build_tracks 的默认；断档容忍按**时间**换算，
参考臂 stride 10 时 `gap_frames` 是 2 fps 的 3 倍，否则一次漏检就把一条长文字切成碎片、碎片落进 `between`）。
每条 run 按位置（参考框包含 2 fps 框的比例 + 时间）去对照臂找，**只落一格**：

    covered         存在期内某个 2 fps 采样点上，同位置读到了（基本）同一段文本
    partial         读到的是参考文本的真子串 / 前缀——**半截打字机**
    differs         同位置有框，但文本对不上
    sampled_missed  存在期内有 2 fps 采样点，同位置没框（det 漏 / 复用没续上 / 两臂各自的抖动）
    fragment        存在期内没有 2 fps 采样点，但紧邻的采样点上同位置有同一段文本（或它的超串）——
                    参考臂自己断了档 / 打字机的中间态，不是缺口
    between_busy    存在期内没有 2 fps 采样点，紧邻的采样点上同位置**有框、文本不同**——
                    多半是同一位置上的读数抖动（乱码每帧不同、连不成 run），也可能是换句之间的一闪
    between         存在期内没有 2 fps 采样点，紧邻的采样点上同位置**没框**——**间隔内闪现**的候选
    edge            存在期内没有 2 fps 采样点，而紧邻的采样点落在**窗口外**（窗首 / 窗尾）——没有对照可比
    moved           sampled_missed / between / between_busy 里，同位置没对上、但**紧邻 ±1 个 2 fps 间隔**内
                    对照臂在画面**别处**读到了同一段文本（内容 >= 3 字）——随镜头移动的世界空间标签、
                    滚动条，位置每帧都变，参考臂连不成 run。窗口只开到紧邻采样点、要求 >= 3 字，
                    不是"这段文本在别处出现过没有"那种全局搜（归因规矩）
    noise_spot      between / between_busy 里落在**噪点位**上的：参考臂在同一位置还有 >= SPOT_MIN 条
                    互不相同的短 run（<= 2 次观测）。典型是画面角落的 logo / 纹理每帧被读成不同的乱码
                    （yuka f1 左下角 `Duinini` / `Bsm y`），2 fps 那边同一处多半在 conf 门下。
                    只改标签不删，清单里照列

**参考臂不是真值**：`between` / `partial` 的有内容档要逐条摆帧核实才算数，所以这里把它们逐条列出来。
存在期取参考臂**观测到的**首末帧（闭区间），不外推半个间隔。

    python dev_tools/probe_fps_gap.py out/samples/q-gi-s2-fps6.jsonl out/samples/q-gi-s2.jsonl --list tmp/gap/gi-s2.tsv
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr.analyze import build_tracks as bt  # noqa: E402
from flowocr.artifacts import evalkit  # noqa: E402

CATS = ("covered", "partial", "differs", "sampled_missed", "fragment", "between_busy", "between", "edge", "moved", "noise_spot")
SPOT_MIN = 4
CONTAIN = 0.6
"""两个框里**小的那个**落在大的那个（外扩后）里的比例。按小框算、两个方向都认：
半截打字机的 2 fps 框只是参考框的左段，参考臂打字机中间态的框又只是 2 fps 整行框的左段——IoU 都会很低。"""


def load(path: Path) -> tuple[dict, list[dict]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])["_meta"]
    if meta.get("timebase") != "pts" or not meta.get("complete"):
        raise SystemExit(f"{path}: 缺 timebase=pts 或 complete（没跑完的 obs 不能当证据）")
    return meta, [json.loads(x) for x in lines[1:] if x.strip()]


def residue(obs: list[dict], stride: int, name: str) -> int:
    """采样格的相位：所有 obs 的 frame 对 stride 同余，否则不是一张格子。"""
    rs = {o["frame"] % stride for o in obs}
    if len(rs) != 1:
        raise SystemExit(f"{name} 的 frame 对 stride {stride} 不同余：{sorted(rs)[:5]}")
    return rs.pop()


def contain(inner: list[float], outer: list[float], pad: float) -> float:
    """inner 的面积里落在 outer（四周外扩 pad 像素）里的比例。"""
    x0, y0, x1, y1 = outer[0] - pad, outer[1] - pad, outer[2] + pad, outer[3] + pad
    ix0, iy0 = max(inner[0], x0), max(inner[1], y0)
    ix1, iy1 = min(inner[2], x1), min(inner[3], y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return (ix1 - ix0) * (iy1 - iy0) / area if area > 0 else 0.0


SMALL_KANA = str.maketrans("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ", "あいうえおつやゆよわアイウエオツヤユヨワカケ")
"""小写假名折成大写：OCR 常把促音 / 拗音读成大字（`ちょっと` / `ちょつと`），这不是缺字。"""
KEEP = set("っッーゃゅょャュョぁぃぅぇぉァィゥェォ")
"""`evalkit._PUNCT_CHARS` 里把 `っ` / `ッ` / `ー` 当成标点（它数的是"内容字"），这把尺要的是"字有没有读全"，这几个得留着。"""


def norm(t: str) -> str:
    """只留内容字（小写假名折成大写、促音 / 长音保留）；没有内容字的（纯标点）留去空白的原文。"""
    c = "".join(ch for ch in t if (ch in KEEP or ch not in evalkit._PUNCT_CHARS) and not ch.isspace())
    return c.translate(SMALL_KANA) or "".join(t.split())


def compare(a_text: str, b_text: str) -> str:
    """对照臂读数 a 相对参考文本 b：covered / partial / differs。

    **先判"短了"再判"像"**（2026-09-22 Codex 审计 P2 第 4 条）：上一版为了躲 `っ` / `つ` 的抖动改成"整体相似度 ≥ 0.85 就算读全"，
    于是 `今日は晴れですが明日は` 对 `今日は晴れですが明日は雨` 被判成 covered——**少了句尾恰恰是半截打字机的样子**，尺子对它失明。
    现在 `っ` / `つ` 在归一化里就解决（折小写、不删促音），比参考短且像它的前缀一律 partial（进人工清单），误差偏向多核、不偏向漏。"""
    a, b = norm(a_text), norm(b_text)
    if not a or not b:
        return "differs"
    if a == b or b in a:
        return "covered"
    if len(a) < len(b) and (a in b or SequenceMatcher(None, a, b[:len(a)]).ratio() >= 0.8):
        return "partial"
    if SequenceMatcher(None, a, b).ratio() >= 0.85:
        return "covered"
    return "differs"


def midstate(ref_text: str, a_text: str) -> bool:
    """参考文本像不像对照读数的**打字机中间态**：近似前缀（OCR 在中间态上常丢一两个字，
    `何ミステ` 对 `何せミステリマニア…`，所以不要求严格子串）。"""
    r, a = norm(ref_text), norm(a_text)
    if not r or len(r) >= len(a):
        return False
    return SequenceMatcher(None, r, a[:len(r) + 1]).ratio() >= 0.75


def a_text_at(a_by_frame: dict[int, list[dict]], f: int, box: list[float], pad: float) -> str | None:
    """对照臂在帧 f、参考框里读到的文本（det 把一行切成几段时按 x 拼回去）；没框返回 None。"""
    hits = [o for o in a_by_frame.get(f, ())
            if max(contain(o["box"], box, pad), contain(box, o["box"], pad)) >= CONTAIN]
    if not hits:
        return None
    hits.sort(key=lambda o: (round(o["box"][1] / 8), o["box"][0]))
    return "".join(o["text"] for o in hits)


def box_at(track: list[tuple[int, list[float]]], f: int) -> list[float]:
    """`track` 是按帧排好的 (frame, box)；取 f 时刻（或之前最近）的框，早于首点取首点。"""
    cur = track[0][1]
    for g, b in track:
        if g > f:
            break
        cur = b
    return cur


def classify(run_frames: list[int], track: list[tuple[int, list[float]]], text: str,
             a_by_frame: dict[int, list[dict]], a_stride: int, a_phase: int,
             a_span: tuple[int, int] | None = None) -> tuple[str, str]:
    """一条参考 run 落哪一格，外加对照臂最好的那次读数（给清单用）。

    `track`：静止的 run 只有一个点（并集框）；动过的 run 是位置历史，逐采样点取当时的框。
    `a_span`：对照臂实际采样的首末帧；落在它外面的格点不存在。
    """
    f0, f1 = min(run_frames), max(run_frames)
    first = f0 + (a_phase - f0) % a_stride
    grid = list(range(first, f1 + 1, a_stride))
    order = {"covered": 0, "partial": 1, "differs": 2}

    def look(f: int) -> str | None:
        b = box_at(track, f)
        return a_text_at(a_by_frame, f, b, 0.5 * (b[3] - b[1]))
    if not grid and a_span and (first > a_span[1] or first - a_stride < a_span[0]):
        return "edge", ""
    if not grid:
        near = [t for t in (look(first - a_stride), look(first)) if t is not None]   # 紧邻的两个采样点
        for t in near:
            if compare(t, text) == "covered" or midstate(text, t):
                return "fragment", t
        return ("between_busy", " | ".join(near)) if near else ("between", "")
    best, best_t = "sampled_missed", ""
    for f in grid:
        t = look(f)
        if t is None:
            continue
        c = compare(t, text)
        if best == "sampled_missed" or order[c] < order[best]:
            best, best_t = c, t
    return best, best_t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref", help="参考臂（高采样率）的 obs")
    ap.add_argument("base", help="对照臂（当前默认 2 fps）的 obs，同一段素材、同一窗口")
    ap.add_argument("--list", default=None, help="非 covered 的逐条清单（TSV）")
    ap.add_argument("--tag", default=None, help="报表里这一段的名字（默认取参考 obs 的文件名）")
    a = ap.parse_args()

    rmeta, robs = load(Path(a.ref))
    bmeta, bobs = load(Path(a.base))
    for k in ("video", "start_sec", "end_sec"):
        if rmeta.get(k) != bmeta.get(k):
            raise SystemExit(f"两臂的 {k} 不同：{rmeta.get(k)!r} / {bmeta.get(k)!r}")
    from flowocr.extract.framegrid import meta_stride
    rs, bs = meta_stride(rmeta), meta_stride(bmeta)
    if bs % rs:
        raise SystemExit(f"采样格不嵌套：参考 stride {rs} 不整除对照 stride {bs}")
    d = bt.make_parser().parse_args([a.ref, "--outdir", "-"]).__dict__   # 只取默认值
    robs = [o for o in robs if o["conf"] >= d["min_conf"]]
    bobs = [o for o in bobs if o["conf"] >= d["min_conf"]]
    if not robs or not bobs:
        raise SystemExit("有一臂没有满足置信度的观测")
    rp, bp = residue(robs, rs, "参考臂"), residue(bobs, bs, "对照臂")
    if bp % rs != rp:
        raise SystemExit(f"采样格相位不嵌套：参考 {rp} mod {rs}，对照 {bp} mod {bs}")

    gap = d["gap_frames"] * bs // rs      # 断档容忍按时间换算
    frame_us = int(round(1e6 / rmeta["sample_fps"]))
    runs = bt.build_runs(robs, frame_us, d["iou"], d["sim"], gap,
                         grow_typewriter=not d["no_typewriter"], text_pick=d["text_pick"],
                         tail_max=d["tail_max"], vote_independent=d["vote_independent"],
                         prefix_min=d["prefix_min"], typewriter_min_chars=d["typewriter_min_chars"])
    frame_of = {o["t_us"]: o["frame"] for o in robs}
    # 对照臂实际采样的首末帧：meta 的首末 PTS 按帧号的计法换算（帧号 = round(t × id_rate)，拿一条 obs 反推 id_rate）
    o = max(bobs, key=lambda x: x["t_us"])
    rate = o["frame"] / (o["t_us"] / 1e6)
    a_span = (round(bmeta["pts_first_sec"] * rate), round(bmeta["pts_last_sec"] * rate))
    a_by_frame: dict[int, list[dict]] = {}
    for o in bobs:
        a_by_frame.setdefault(o["frame"], []).append(o)

    short = [r for r in runs if r.n_obs <= 2]

    def spot(r) -> bool:
        """同一位置上还有 >= SPOT_MIN 条和它文本不同的短 run。"""
        n = 0
        for o in short:
            if o is r or compare(o.text, r.text) == "covered":
                continue
            if max(contain(o.box, r.box, 0), contain(r.box, o.box, 0)) >= CONTAIN:
                n += 1
                if n >= SPOT_MIN:
                    return True
        return False

    def moved(frames: list[int], text: str) -> bool:
        """紧邻 ±1 个对照间隔内，对照臂在任意位置读到同一段文本（>= 3 个内容字）。"""
        if evalkit.content_len(text) < 3:
            return False
        lo, hi = min(frames) - bs, max(frames) + bs
        f = lo + (bp - lo) % bs
        while f <= hi:
            if any(compare(o["text"], text) == "covered" for o in a_by_frame.get(f, ())):
                return True
            f += bs
        return False

    tag = a.tag or Path(a.ref).stem
    table: Counter = Counter()
    rows = []
    for r in runs:
        frames = [frame_of[t] for t in r.times]
        track = ([(frame_of[t], b) for t, b in r.boxes] if r.moving
                 else [(frames[0], r.box)])
        cat, got = classify(frames, track, r.text, a_by_frame, bs, bp, a_span)
        if cat in ("between", "between_busy", "sampled_missed") and moved(frames, r.text):
            cat = "moved"
        elif cat in ("between", "between_busy") and spot(r):
            cat = "noise_spot"
        tier = evalkit.triviality(r.text)
        table[cat, tier] += 1
        if cat != "covered":
            dur = (max(frames) - min(frames)) / rmeta["src_fps"]
            rows.append((tag, cat, tier, f"{min(r.times) / 1e6:.3f}", f"{dur:.2f}", r.n_obs,
                         ",".join(str(int(v)) for v in r.box), r.text, got))

    print(f"== {tag}：参考 stride {rs}（{rmeta['sample_fps']:.3g} fps）对照 stride {bs}，"
          f"参考 run {len(runs)} 条，断档容忍 {gap} 个参考间隔")
    print(f"{'':16s}" + "".join(f"{c:>16s}" for c in evalkit.CLASSES) + f"{'合计':>8s}")
    for c in CATS:
        n = [table[c, t] for t in evalkit.CLASSES]
        print(f"{c:16s}" + "".join(f"{x:16d}" for x in n) + f"{sum(n):8d}")
    tot = [sum(table[c, t] for c in CATS) for t in evalkit.CLASSES]
    print(f"{'合计':14s}" + "".join(f"{x:16d}" for x in tot) + f"{sum(tot):8d}")
    gapcats = ("partial", "between", "between_busy")
    content = evalkit.CLASSES[2]
    multi = sum(1 for row in rows if row[1] in gapcats and row[2] == content and row[5] >= 2)
    print(f"有内容档的缺口候选（{' / '.join(gapcats)}）：{sum(table[c, content] for c in gapcats)} 条，"
          f"其中参考臂看到 >= 2 次（在屏 >= 1 个参考间隔）的 {multi} 条")
    if a.list:
        out = Path(a.list)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            fh.write("tag\tcat\ttier\tt_sec\tdur_sec\tn_obs\tbox\tref_text\tbase_text\n")
            for row in rows:
                fh.write("\t".join(str(x).replace("\t", " ") for x in row) + "\n")
        print(f"清单 {len(rows)} 条 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
