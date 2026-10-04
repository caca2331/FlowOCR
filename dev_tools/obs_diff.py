"""两份观测产物差在哪：采样时刻 → 框 → 文本，逐层收窄。

给「换了取帧方式 / 换了模型档 / 改了裁剪」这类**本该等价**的改动做对账。
计划书 hw-decode 计划 §3.3 要的就是这张表。

## 为什么分三层，而不是只报一个"一致率"

**上一层不过，下一层的数就没有意义**——这是本仓库反复栽的形状
（`probe_hwdecode.py` 那次：只比像素会看到 max|Δ|=255 而误判成"色彩不对"，
真相是采样时刻错开、根本不是同一帧）。所以：

1. **采样格子**：`_meta.sampled_frames`（真正的判据）。这一层不等就往下没得比。
   ⚠ **2026-09-10 修**：原来这一层比的是"观测行里出现过的 `t_us` 集合"，
   而一个采样帧**一个框都没检出**时不写任何观测行，它的时刻根本不在集合里。
   于是两臂采样格子明明都是 2,400 帧，只因为一边多两帧空帧，
   就被报成"⚠ 采样时刻对不上"并宣布**下面两层没意义**——那句宣布是错的。
   现在采样格子看 `_meta`，"一边出框、一边没出框"归到第 2 层（那本来就是检测差异）。
2. **框**：同一时刻的框数、以及按 IoU 配对上的比例。
3. **文本**：配对上的框里，文本一样的比例；外加全局文本多重集的一致率
   （多重集这一层不看框配没配上，是"读出来的东西整体一不一样"）。

## 一致率怎么算

多重集一致率 = `2 × |A ∩ B| / (|A| + |B|)`（Sørensen–Dice），
两边都空时定义为 1.0。**不用「A 里有多少在 B 里」**——那个对"B 多出一堆"免疫。

用法：
    python dev_tools/obs_diff.py 甲.jsonl 乙.jsonl
    python dev_tools/obs_diff.py 甲.jsonl 乙.jsonl --iou 0.7 --show 10
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def load(path: str) -> tuple[dict, list[dict]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit(f"{path} 是空的")
    meta = json.loads(lines[0]).get("_meta")
    if meta is None:
        raise SystemExit(f"{path} 第一行不是 _meta")
    return meta, [json.loads(x) for x in lines[1:] if x.strip()]


def iou(a: list[int], b: list[int]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / ((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter)


def grid_verdict(ma: dict, mb: dict, ta: list[int], tb: list[int]) -> tuple[bool, list[str]]:
    """第 1 层：**采样格子一不一样**。纯函数，守卫直接测。

    `ta` / `tb` 是**观测行里出现过的** `t_us`（升序）——它们只覆盖"至少出了一个框"
    的帧，所以**不能拿它当采样格子**。格子看 `_meta.sampled_frames`。
    两个 `_meta` 都没有那个键时（旧产物）才退回比时刻集合，并明说这是退化判据。
    """
    sa, sb = ma.get("sampled_frames"), mb.get("sampled_frames")
    only_a, only_b = sorted(set(ta) - set(tb)), sorted(set(tb) - set(ta))
    if sa is None or sb is None:
        same = ta == tb
        return same, [f"[1] 采样格子：⚠ `_meta.sampled_frames` 缺失，"
                      f"**退回比『有框的时刻』**（甲 {len(ta)} 个、乙 {len(tb)} 个）—— "
                      + ("看着相同" if same else "⚠ 对不上，但也可能只是空帧不同")]
    same = sa == sb
    out = [f"[1] 采样格子（`_meta.sampled_frames`）：甲 {sa} 帧、乙 {sb} 帧 —— "
           + ("**完全相同**" if same else "⚠ **对不上**")]
    out.append(f"    有框的时刻：甲 {len(ta)} 个、乙 {len(tb)} 个"
               f"（一个框都没检出的空帧：甲 {sa - len(ta)}、乙 {sb - len(tb)}）")
    if only_a or only_b:
        out.append(f"    **一边出框、一边没出框**的时刻：只在甲 {len(only_a)} 个、"
                   f"只在乙 {len(only_b)} 个——这是**第 2 层的检测差异**，不是格子对不上")
        if only_a:
            out.append(f"      甲独有（秒）：{[round(t/1e6, 3) for t in only_a[:5]]}")
        if only_b:
            out.append(f"      乙独有（秒）：{[round(t/1e6, 3) for t in only_b[:5]]}")
    if not same:
        out.append("    ⚠ **这一层不过，下面两层的数没有意义**——比的不是同一批帧。")
    return same, out


def dice(a: Counter, b: Counter) -> float:
    """多重集一致率。两边都空 = 1.0（没东西可差）。"""
    tot = sum(a.values()) + sum(b.values())
    return 1.0 if not tot else 2 * sum((a & b).values()) / tot


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--iou", type=float, default=0.5, help="框配对的 IoU 门槛")
    ap.add_argument("--show", type=int, default=6, help="最多举几个文本不同的例子")
    args = ap.parse_args()

    ma, ra = load(args.a)
    mb, rb = load(args.b)
    print(f"甲 {args.a}\n乙 {args.b}\n")

    # `rec_model` / `rec_batch_size` 只在 2026-10 去掉 Paddle 之前的产物里有，不比（缺键按 None 读，不报错）
    keys = ("decoder", "pipe_pix", "prefetch", "timebase", "det_model", "reuse", "sample_fps", "complete", "git_head")
    diff_meta = [(k, ma.get(k), mb.get(k)) for k in keys if ma.get(k) != mb.get(k)]
    print("_meta 不同的键：" + ("无" if not diff_meta else ""))
    for k, x, y in diff_meta:
        print(f"  {k}: {x!r} -> {y!r}")

    # ---- 第 1 层：采样格子 ----
    ta = sorted({o["t_us"] for o in ra})
    tb = sorted({o["t_us"] for o in rb})
    same_grid, lines = grid_verdict(ma, mb, ta, tb)
    print()
    for ln in lines:
        print(ln)

    # ---- 第 2 层：框 ----
    ba: dict[int, list[dict]] = defaultdict(list)
    bb: dict[int, list[dict]] = defaultdict(list)
    for o in ra:
        ba[o["t_us"]].append(o)
    for o in rb:
        bb[o["t_us"]].append(o)
    shared = sorted(set(ba) & set(bb))
    n_a = sum(len(ba[t]) for t in shared)
    n_b = sum(len(bb[t]) for t in shared)
    same_count = sum(1 for t in shared if len(ba[t]) == len(bb[t]))
    matched = 0
    text_same = 0
    examples: list[str] = []
    for t in shared:
        used: set[int] = set()
        for oa in ba[t]:
            best, bj = 0.0, -1
            for j, ob in enumerate(bb[t]):
                if j in used:
                    continue
                v = iou(oa["box"], ob["box"])
                if v > best:
                    best, bj = v, j
            if best >= args.iou and bj >= 0:
                used.add(bj)
                matched += 1
                if oa["text"] == bb[t][bj]["text"]:
                    text_same += 1
                elif len(examples) < args.show:
                    examples.append(f"    {t/1e6:9.3f}s  {oa['text']!r} -> "
                                    f"{bb[t][bj]['text']!r}")
    print(f"\n[2] 框（共有的 {len(shared)} 个时刻）：甲 {n_a} 个、乙 {n_b} 个")
    print(f"    框数逐时刻相同：{same_count}/{len(shared)} "
          f"= {same_count/max(1,len(shared)):.1%}")
    print(f"    IoU ≥ {args.iou} 配对上：{matched} "
          f"= 甲的 {matched/max(1,n_a):.1%}、乙的 {matched/max(1,n_b):.1%}")

    # ---- 第 3 层：文本 ----
    print(f"\n[3] 文本：配对上的框里一样的 {text_same}/{matched} "
          f"= {text_same/max(1,matched):.2%}")
    ca = Counter(o["text"] for o in ra)
    cb = Counter(o["text"] for o in rb)
    print(f"    全局文本多重集一致率（Dice）：{dice(ca, cb):.2%}"
          f"（甲 {sum(ca.values())} 条、乙 {sum(cb.values())} 条）")
    if examples:
        print("    读得不一样的例子：")
        print("\n".join(examples))

    if not same_grid:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
