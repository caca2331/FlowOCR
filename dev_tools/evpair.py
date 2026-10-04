"""两条臂的**事件配对判据**——放共享层是为了"只有一份"（2026-09-19 复审）。

`arm_events.py` 和 `text_direction.py` 原来各写了一份：前者**全局按 IoU 排序后独占分配**，
后者**逐个事件按时间重叠贪心**——后者的结果还和遍历顺序有关。两份判据会慢慢走样，
而它们回答的是同一个问题："A 的这个事件和 B 的哪个是同一处文字？"

判据是**几何 + 时间，不看文本**（非循环）：`IoU ≥ iou` 且时间有重叠；
候选按 (IoU, 重叠时长) 从大到小独占分配——同一处可能有好几段，先配最像的那一对。
"""
from __future__ import annotations


def iou(a: list[int], b: list[int]) -> float:
    """两个框的交并比。"""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    i = iw * ih
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def overlap(e: dict, f: dict) -> float:
    """两个事件在时间上重叠多久（µs，负数表示不重叠）。"""
    return min(e["t_end"], f["t_end"]) - max(e["t_start"], f["t_start"])


def pair_events(ea: list[dict], eb: list[dict], iou_thr: float = 0.7) -> tuple[dict, dict]:
    """返回 (pa, pb)：`pa[i] = j` 表示 A 的第 i 个事件配上了 B 的第 j 个，`pb` 是反向。

    **和遍历顺序无关**：先把所有候选按 (IoU, 重叠时长) 排序，再独占分配。
    （`text_direction` 那一份原来是"逐个 A 事件挑当前最优"，换个事件顺序结果就可能不同。）
    """
    cand = []
    for i, e in enumerate(ea):
        for j, f in enumerate(eb):
            if overlap(e, f) > 0:
                v = iou(e["box"], f["box"])
                if v >= iou_thr:
                    cand.append((v, overlap(e, f), i, j))
    cand.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
    pa: dict[int, int] = {}
    pb: dict[int, int] = {}
    for _v, _o, i, j in cand:
        if i not in pa and j not in pb:
            pa[i], pb[j] = j, i
    return pa, pb
