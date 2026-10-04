"""按时间对齐两份 SRT，比文本差异。

用途是 A/B：同一段素材换引擎、换画质、换参数之后，文本到底动了多少、动在哪。
**这不是 CER**——没有人工 gold，只有"两份产出彼此有多像"。要 CER 得另外标注。

用法：
    python dev_tools/compare_text.py out/zh-sub/A.srt out/zh-sub/B.srt
"""
from __future__ import annotations

import argparse
import sys
import statistics
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts.evalkit import require_nonzero   # noqa: E402

from compare_timing import read_srt


def norm(s: str) -> str:
    return "".join(s.split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", help="基准（例如原画质 / 主引擎）")
    ap.add_argument("b", help="对比对象（例如低画质 / 备选引擎）")
    ap.add_argument("--show", type=int, default=12, help="最多列出多少条差异")
    ap.add_argument("--thr", type=float, default=0.999, help="相似度低于此值才算有差异")
    args = ap.parse_args()

    A = read_srt(Path(args.a))
    B = read_srt(Path(args.b))
    sims: list[float] = []
    diffs: list[tuple[float, float, str, str]] = []
    missing = 0
    for a in A:
        overlaps = [(max(0.0, min(a[1], b[1]) - max(a[0], b[0])), b) for b in B]
        best_ov, best = max(overlaps, default=(0.0, None), key=lambda x: x[0])
        if best is None or best_ov <= 0:
            missing += 1
            sims.append(0.0)
            diffs.append((a[0], a[1], a[2], "(B 里没有对应条目)"))
            continue
        sim = SequenceMatcher(None, norm(a[2]), norm(best[2])).ratio()
        sims.append(sim)
        if sim < args.thr:
            diffs.append((a[0], a[1], a[2], best[2]))

    require_nonzero(len(A), "A 侧条目", "解析出 0 条，多半是格式或路径不对")
    require_nonzero(len(B), "B 侧条目")
    print(f"A={Path(args.a).name}  {len(A)} 条")
    print(f"B={Path(args.b).name}  {len(B)} 条")
    if sims:
        exact = sum(s >= 0.999 for s in sims)
        print(f"逐条文本相似度：中位 {statistics.median(sims):.3f}  均值 {statistics.mean(sims):.3f}  "
              f"完全一致 {exact}/{len(sims)}  B 无对应 {missing}")
    print()
    for s, e, ta, tb in diffs[: args.show]:
        print(f"  {s:6.2f}-{e:6.2f}")
        print(f"     A: {ta[:90]}")
        print(f"     B: {tb[:90]}")
    if len(diffs) > args.show:
        print(f"  ... 还有 {len(diffs) - args.show} 条差异")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
