"""我们比参照多出来的条目，是真内容还是幽灵？

用途 2（字幕/剧本匹配）的准确性重心是**不漏条、不出幽灵条目**，
CER 反而次要（产品方向记录）。这个工具把"多出来的条目"挑出来，
按时长分档统计，并抽样出帧号供人工核验——它只负责挑，判断留给看帧的人。

用法：
    python dev_tools/extra_entries.py 参照.srt 我们的.srt --sample 8
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）

from flowocr.artifacts import srtio
from flowocr.artifacts.evalkit import denom, require_nonzero


def read_srt(path: Path) -> list[tuple[float, float, str]]:
    """口径与拼行分隔符都来自 srtio；这里保持历史上的 `" / "`。"""
    return [(c.start, c.end, c.text(" / ")) for c in srtio.read_srt(path)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("ours")
    ap.add_argument("--sample", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-dur", type=float, default=0.0, help="只看时长不低于这个的多出条目")
    args = ap.parse_args()

    ref = read_srt(Path(args.ref))
    ours = read_srt(Path(args.ours))
    extra = []
    for s, e, t in ours:
        if not any(min(e, b) - max(s, a) > 0 for a, b, _ in ref):
            extra.append((s, e, t))

    require_nonzero(len(ref), "参照条目", "参照文件解析出 0 条，多半是格式或路径不对")
    require_nonzero(len(ours), "被测条目")
    print(f"参照 {len(ref)} 条，我们 {len(ours)} 条，"
          f"**与参照零重叠的多出条目 {denom(len(extra), len(ours))}**")
    durs = [e - s for s, e, _ in extra]
    if durs:
        print(f"多出条目时长：中位 {statistics.median(durs):.2f}s  "
              f"最短 {min(durs):.2f}s  最长 {max(durs):.2f}s")
        bands = [(0, 0.6), (0.6, 2), (2, 10), (10, 1e9)]
        for lo, hi in bands:
            n = sum(1 for d in durs if lo <= d < hi)
            print(f"   {lo:>4.1f}–{hi if hi < 1e9 else float('inf'):<5.1f}s : {n:>5} 条"
                  f"  ({n/len(durs):>5.1%})")
    # 有文字内容的才值得抽样看——空文本条目是另一类问题
    cand = [x for x in extra if x[1] - x[0] >= args.min_dur and x[2].strip()]
    random.Random(args.seed).shuffle(cand)
    print(f"\n抽样 {min(args.sample, len(cand))} 条供核验（时长 ≥ {args.min_dur}s）：")
    for s, e, t in cand[:args.sample]:
        print(f"  t={(s+e)/2:>9.2f}s  [{s:.2f}–{e:.2f}]  {t[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
