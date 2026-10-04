"""rec 的"打包"方式里**不碰模型的那部分**：近宽分组、异步池挑批、CTC 贪心解码。

放共享层是为了守卫够得着（audit-4 C8）：管线（`predict_bucketed`）和守卫用的是同一份。
只依赖 numpy。

**近宽分桶**（fps-and-rec-budget 报告）：目标宽相差不超过 `ratio` 的裁剪批在一起，批内会被补到最大宽——
补的零不多，文本变化应该有限，但批能更大。`ratio = 0` 就是现有的同宽分桶。
（当时还探过**横向拼接**——几个裁剪拼成一条长图一次送、按横坐标把字符分回各段；它的排布和切回函数只服务探针，
2026-10 随 Paddle 探针一起删了。）
"""
from __future__ import annotations

import numpy as np

from flowocr.extract.recprep import REC_MAX_W, rec_target_w  # noqa: F401  张量宽的公式只有一份（recprep 文件头）


def predict_bucketed(rec, crops: list, cap: int, ratio: float = 0.0) -> tuple[list, int]:
    """**分桶**送 rec（`run_ocr2 --rec-bucket` / `--rec-near`）：目标宽相差不超过 `ratio` 的裁剪一批，
    每批至多 `cap` 个。`ratio = 0` 是**同宽**分桶：`ToBatch.__pad_imgs` 无事可做，
    每个样本的输入张量和逐框送时逐位相同（实测 1373/1373）。

    `ratio > 0` 时批内小的那些会被**右侧补零到批内最大宽**（`__pad_imgs` 干的），所以**产物会变**——
    它换来的是批真的能凑起来：q-hsr-s2 五分钟、`--rec-window 8` 之下 2,537 个裁剪按同宽并成
    **799** 批（平均 3.18），近宽 5% 并成 **639** 批（平均 3.97）——`--rec-window` 和它是一对。

    返回 `(结果, predict 调用次数)`。**结果长度恒等于 `crops`，缺的位置放 `None`，绝不压缩**：
    某批少返回一条时，压缩会让后面所有框配到前一个框的文本上——而且看不出来。
    `rec` 只要有 `predict(list, batch_size=n)`，守卫拿假对象就能测。
    """
    out: list = [None] * len(crops)
    widths = [rec_target_w(*c.shape[:2]) for c in crops]
    # 后端自己会把张量宽收到少数几个形状上时（`OrtRec.shape_w`），**按形状宽分桶**、近宽合并不再生效：
    # 同形状的本来就该同批，不同形状的并了后端也得拆开。批维不影响读数（`probe_rec_pad.py` B 组：547 条差 0~1 条）
    shape_w = getattr(rec, "shape_w", None)
    if shape_w is not None:
        widths, ratio = [shape_w(w) for w in widths], 0.0
    groups = group_near(widths, ratio, cap)
    for g in groups:
        for i, r in zip(g, rec.predict([crops[i] for i in g], batch_size=len(g))):
            out[i] = r
    return out, len(groups)


def pick_batch(widths: list[int], ratio: float, cap: int,
               prio: set[int] | frozenset[int] = frozenset()) -> list[int]:
    """从**此刻排着的**请求里挑出下一批（异步消费者用，`src/flowocr/extract/recpool.py`）。

    判据是"**有多少取多少**"（owner 2026-09-19 定的形态）：先按近宽分组（组内封顶 `cap`），
    **取最大的那一组就发**，绝不为了凑满一桶而等——两个桶分别有 2 条和 5 条时，取那 5 条开始，
    而不是等第一个桶长到 cap。

    `prio` 是**卡着落盘水位的那些请求**（主线程正等最老那一帧）：给了就改取"优先项最多的那一组"。
    优先项本来就和别的请求一起参与分组，所以这一批仍然是满的——**插队不等于把批打小**。

    返回 `widths` 的下标列表（组内按宽升序）；`widths` 为空时返回空表。
    """
    groups = group_near(widths, ratio, cap)
    if not groups:
        return []
    if prio:
        return max(groups, key=lambda g: (sum(i in prio for i in g), len(g)))
    return max(groups, key=len)


def group_near(widths: list[int], ratio: float, cap: int) -> list[list[int]]:
    """按目标宽分组：排序后贪心，组内最大宽 ≤ 最小宽 × (1 + ratio)，每组至多 `cap` 个。

    返回下标列表的列表（组内按宽升序）。`ratio = 0` 退化成**同宽**分桶。
    """
    if cap < 1:
        raise ValueError("cap 至少是 1")
    order = sorted(range(len(widths)), key=lambda i: (widths[i], i))
    groups: list[list[int]] = []
    for i in order:
        if groups and len(groups[-1]) < cap and widths[i] <= widths[groups[-1][0]] * (1 + ratio):
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def ctc_greedy(idx: np.ndarray, prob: np.ndarray, characters: list[str],
               blank: int = 0) -> tuple[str, float]:
    """和 PaddleX `BaseRecLabelDecode.decode(is_remove_duplicate=True)` 同一套规矩：
    相邻重复合并、去 blank；分数 = 留下的那些时间步的最大概率的均值，一个都没留下时为 0。"""
    if len(idx) == 0:
        return "", 0.0
    keep = np.ones(len(idx), dtype=bool)
    keep[1:] = idx[1:] != idx[:-1]
    keep &= idx != blank
    text = "".join(characters[i] for i in idx[keep])
    conf = prob[keep]
    return text, float(np.mean(conf)) if len(conf) else 0.0
