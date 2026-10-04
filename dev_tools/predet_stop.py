"""predet 的**提前停止判据**——只有这一份实现。

放在 `tools/` 而不是留在 paddleocr 实验里的 `predet_scan.py` 里，是因为它是一条
**纯几何的判据**，而那个文件顶层 import cv2 和 paddleocr，
共享层的自检（`tests/test_guards.py`，跑在 miniconda 的 python 上）**够不着它**。
判据够不着 = 测不了，这个项目在"判据抄两份"上已经栽过一次。

判据本身（methodology-audit-4 报告 C8 修正过一次）：

* 上一档和这一档的 top-N 区域**互相**匹配得上，才算稳定；
* 分母用**两边的较大值**。原来除以较小值，于是五个互不相交的区域缩成只剩一个
  也判 True——**区域集合缩水被当成了"稳定"**。

⚠ 它回答的是"预算够不够、可以停了吗"，**不回答"全屏文字是不是都找到了"**：
top-N 稳定不能证明罕见位置已经发现，也没有约束没采到的区域。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.analyze.build_tracks import iou  # noqa: E402  几何原语只有一份


def stable(prev: list[dict], cur: list[dict], top: int = 5, thr: float = 0.8,
           frac: float = 0.8) -> bool:
    """上一档和这一档的主要区域是不是同一批。"""
    if not prev:
        return False
    a, b = prev[:top], cur[:top]
    if not a or not b:
        return False
    fwd = sum(1 for x in a if max((iou(x["rect"], y["rect"]) for y in b), default=0) >= thr)
    back = sum(1 for y in b if max((iou(y["rect"], x["rect"]) for x in a), default=0) >= thr)
    return min(fwd, back) >= max(len(a), len(b)) * frac
