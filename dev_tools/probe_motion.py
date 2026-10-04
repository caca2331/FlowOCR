"""文字**怎么动**：位移事件序列，不是三个汇总数。

来历：text-motion 计划第 1 步（owner 2026-09-09 brainstorm 收敛的方案）。
两个细节推翻了"把形态列成几类"这条路：**步进滚动可以完全没有重复部分**（刷得太快的
弹幕墙），**平滑滚动可以极快、可以非匀速**。所以形态是一个连续参数空间上的位置，
用两个无量纲量描述**一次位移事件**（owner 指示 7：不许钉死速度类常数）：

    step / pitch      每步位移 ÷ 行距。1 = 逐行；≥ 区域高 = 整页替换（无重复）
    interval / Δ      两步之间的间隔 ÷ 采样间隔。→1 = 平滑；不齐 = 非匀速

**这个探针只读 obs，不重跑 OCR、不动默认路径。** 它回答的是
"yuka 评论墙 / gi 阅读材料 / 片尾表 / 双语带在这张图上是不是各占一角"——
图不分角，方案后面几步就不做。**分不分角由 owner 看图定，不由某个阈值定。**

    python dev_tools/probe_motion.py out/gamestream/gi-s2.jsonl
    python dev_tools/probe_motion.py out/yuka/i1-full.jsonl --out tmp/motion-f1.tsv

⚠ 一条已知的边界：`estimate_shift` 要**同一个位移至少 3 票、且赢得干净**才认，
所以**只有一行在动**的 ticker 在 run 合并这一层根本配不上，会碎成一帧一条 run。
探针如实报这件事（`碎片率`那一列），别把它读成"这段素材没有滚动"。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.analyze import build_tracks as bt  # noqa: E402
from flowocr.artifacts import evalkit  # noqa: E402


def steps_of(run: bt.Run) -> list[tuple[int, float, float, int]]:
    """一条 run 的位移事件：`(t_us, dx, dy, 距上一次位移的间隔 µs)`。

    位置历史只有**动过**的 run 才留全（`Run.boxes`），所以静止的 run 这里是空的。
    位移按框中心算，不按左上角——横向滚动时框宽会随着截断变化。
    """
    out = []
    prev_c, last_move_t = None, None
    for t, b in run.boxes:
        c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        if prev_c is not None and (c[0] != prev_c[0] or c[1] != prev_c[1]):
            # **间隔是"上一次位移到这一次位移"**，不是"离上一条记录多久"
            # （第二轮复审）：轨迹里现在还有静止段的收尾点，拿它当基准会把
            # `interval/Δ` 一律压成 1，步进滚动就被读成平滑滚动了。
            # 第一次位移没有"上一次"，从这条 run 出现算起。
            base = last_move_t if last_move_t is not None else run.boxes[0][0]
            out.append((t, c[0] - prev_c[0], c[1] - prev_c[1], t - base))
            last_move_t = t
        prev_c = c
    return out


def pitch_of(runs: list[bt.Run]) -> float:
    """行距。**用同一时刻并存的两行之间的 cy 差**（真行距）；
    并存的行不够时退回字高中位数，并把退回这件事告诉调用方（返回 0 表示退回）。"""
    by_t: dict[int, list[float]] = {}
    for r in runs:
        by_t.setdefault(r.t_start, []).append(r.cy)
    gaps = []
    for ys in by_t.values():
        ys.sort()
        gaps += [b - a for a, b in zip(ys, ys[1:]) if b - a > 1]
    # 两条 gap 就够：三行同时上屏只给得出两条，而这正是最该量的那种区域。
    return st.median(gaps) if len(gaps) >= 2 else 0.0


def reuse_ratio(run: bt.Run) -> float:
    return sum(run.reused) / len(run.reused) if run.reused else 0.0


def conf_trend(run: bt.Run) -> float:
    """末段均值 − 首段均值。**淡出**会是负的（`fade` 原语的第一手证据）。"""
    c = run.confs
    if len(c) < 4:
        return 0.0
    k = max(1, len(c) // 4)
    return sum(c[-k:]) / k - sum(c[:k]) / k


def scatter(points: list[tuple[float, float]], w: int = 46, h: int = 14) -> list[str]:
    """`(step/pitch, interval/Δ)` 的 ASCII 散点。**不做聚类、不下结论**——
    这张图是给人看的（text-motion 计划 §7：分不分角由 owner 定）。
    两轴都取 log2，跨度太大（0.1 到几十）线性画不下。"""
    import math

    if not points:
        return ["（没有位移事件）"]
    xs = [math.log2(max(x, 1e-3)) for x, _ in points]
    ys = [math.log2(max(y, 1e-3)) for _, y in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    grid = [[0] * w for _ in range(h)]
    for x, y in zip(xs, ys):
        cx = int((x - x0) / (x1 - x0) * (w - 1)) if x1 > x0 else 0
        cy = int((y - y0) / (y1 - y0) * (h - 1)) if y1 > y0 else 0
        grid[h - 1 - cy][cx] += 1
    chars = " .:oO@"
    rows = ["|" + "".join(chars[min(len(chars) - 1, n and 1 + int(min(n, 40) ** .4))]
                          for n in row) + "|" for row in grid]
    return ([f"  interval/Δ 上界 2^{y1:.1f}"] + rows
            + [f"  下界 2^{y0:.1f}；横轴 step/pitch 从 2^{x0:.1f} 到 2^{x1:.1f}"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obs", help="run_ocr2 产的 *.jsonl（**只读**，不重跑 OCR）")
    ap.add_argument("--top", type=int, default=6, help="打印前几个区域（按 run 数）")
    ap.add_argument("--events", type=int, default=8, help="每个区域打印几条位移事件")
    ap.add_argument("--window-sec", type=float, default=60.0)
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--out", default="", help="把全部位移事件写成 TSV（给 owner 画图）")
    a = ap.parse_args(argv)

    lines = Path(a.obs).read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])["_meta"]
    obs = [json.loads(x) for x in lines[1:] if x.strip()]
    obs = [o for o in obs if o["conf"] >= a.min_conf]
    evalkit.require_nonzero(len(obs), "满足置信度的观测")
    W, H = meta.get("width") or 1920, meta.get("height") or 1080
    frame_us = int(round(1e6 / meta["sample_fps"]))
    runs = bt.build_runs(obs, frame_us, 0.35, 0.55, 1)
    dur_us = max(o["t_us"] for o in obs) + frame_us
    window_us = int(a.window_sec * 1e6)
    _, win_regions = bt.cluster_windowed(runs, W, H, window_us)
    groups = bt.stitch_slots(runs, win_regions, W, H) if window_us > 0 else []
    region_runs: list[list[bt.Run]] = []
    for sl in groups:
        ids = [i for w in sl for i in win_regions[w]["runs"]]
        region_runs.append([runs[i] for i in ids])
    region_runs.sort(key=len, reverse=True)

    print(f"{Path(a.obs).name}：{len(obs)} obs -> {len(runs)} runs -> "
          f"{len(region_runs)} 个区域（{W}x{H}，采样 {meta['sample_fps']:.2f} fps，"
          f"Δ={frame_us/1000:.0f} ms，{dur_us/1e6:.0f}s）")
    rows: list[tuple] = []
    all_points: list[tuple[float, float]] = []
    for ri, rs in enumerate(region_runs[:a.top]):
        moving = [r for r in rs if r.moving]
        pitch = pitch_of(rs)
        pitch_note = ""
        if pitch <= 0:
            pitch = st.median([r.box[3] - r.box[1] for r in rs]) or 1.0
            pitch_note = "（**没有并存的行，行距退回字高中位**）"
        # 一帧一条的碎片：`estimate_shift` 要 3 票，单行滚动配不上，会碎成这样
        frag = sum(1 for r in rs if r.n_obs == 1) / max(1, len(rs))
        print(f"\n区域 #{ri}  runs={len(rs)}  会动的={len(moving)}  "
              f"行距≈{pitch:.0f}px{pitch_note}  一帧一条的占 {frag:.0%}")
        pts = []
        shown = 0
        for r in moving:
            for t, dx, dy, dt in steps_of(r):
                step = (dx * dx + dy * dy) ** .5 / pitch
                iv = dt / frame_us
                pts.append((step, iv))
                rows.append((ri, t, round(dx, 1), round(dy, 1), round(step, 3),
                             round(iv, 3), round(reuse_ratio(r), 2),
                             round(conf_trend(r), 3), r.text[:24]))
                if shown < a.events:
                    print(f"   t={t/1e6:8.2f}s  dx={dx:+7.1f} dy={dy:+7.1f}  "
                          f"step/pitch={step:6.2f}  interval/Δ={iv:5.2f}  "
                          f"沿用={reuse_ratio(r):.0%}  conf趋势={conf_trend(r):+.2f}  "
                          f"{r.text[:20]!r}")
                    shown += 1
        all_points += pts
        if pts:
            print(f"   —— 本区域 {len(pts)} 次位移；**别把它压成一个平均值**，"
                  f"形态是连续谱（text-motion 计划 §2）")
    print("\n(step/pitch, interval/Δ) 的散点（两轴 log2，全部区域）：")
    for line in scatter(all_points):
        print("  " + line)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write("region\tt_us\tdx\tdy\tstep_over_pitch\tinterval_over_delta\t"
                     "reuse\tconf_trend\ttext\n")
            for r in rows:
                fh.write("\t".join(str(x) for x in r) + "\n")
        print(f"\n全部 {len(rows)} 次位移 -> {a.out}")
    if not all_points:
        print("\n**一次位移都没认到。** 先看上面的『一帧一条』那列："
              "`estimate_shift` 要同一个位移至少 3 票，单行滚动本来就配不上——"
              "这不等于这段素材里没有滚动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
