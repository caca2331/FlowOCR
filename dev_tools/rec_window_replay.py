"""窗口延迟读的**因果重放**：不再拿 obs 近似，而是回放 `run_ocr2 --trace` 记下的真实因果。

来历（2026-09-18 审计第 5 条）：`rec_window_sim.py` 是从 **obs** 推的，三处近似都被点出来了——
**用框坐标当链的身份**、**看不出一行是被哪道门拦下的**、**文本已经是回补之后的**。
`--trace` 把这些直接记了下来（逐框：链 uid / 链接类型 / 拦它的门 / 相关系数 / 手里的文本含不含数字 /
最终读没读，外加回补的读和真实张量宽），于是可以**按因果顺序重放**：

* **屏障判得准了**：一个框的判决"依赖不依赖手里的文本"是**可判定的**——
  只有相关系数落在两个阈值之间（`--reuse-corr` 0.8 ~ `--reuse-corr-digit` 0.98）时，
  数字性才决定门；`corr ≥ 0.98` 一律沿用、`corr < 0.8` 一律真读、`refresh` / `gate_dw` /
  没链 / 缓存满这几档**根本不看文本**。旧模拟把这些全算成"要屏障"，所以提前结算偏多。
* **链身份用 uid**，不再用框坐标（位移匹配 / 跨空档接链不会再算错）。
* **回补的读单独记**：它是回溯的，窗口设计里会在结算时一起发出——所以它**能进批**，旧模拟没算这一笔。

两种策略同前：**A′**（遇到"依赖待定文本"就地结算，判决与今天逐字相同）、**A**（假设数字性不变、
批回来校验，罕见回滚）。A 这一列顺带把**真实的翻转次数**数出来（同一条链前后两次 `digit` 不同），
不再拿 0.5% 那个估计值。

usage: python dev_tools/rec_window_replay.py <trace.jsonl…> [--windows 4,8,16] [--ratios 0,0.05]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "dev_tools"))
sys.path.insert(0, str(CODE / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr import paths  # noqa: E402
os.chdir(paths.data_root())
from flowocr.extract import recpack  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("traces", nargs="+")
ap.add_argument("--windows", default="4,8,16")
ap.add_argument("--ratios", default="0,0.05")
ap.add_argument("--cap", type=int, default=32)
a = ap.parse_args()

# 实测的宽 × 批代价曲面（Paddle，ms/裁剪；`rec_batch_curve.py` / `rec_near_curve.py`）
SURF = {320: {1: 8.702, 2: 5.397, 4: 3.430, 8: 2.502, 16: 2.351, 32: 2.132},
        400: {1: 9.477, 2: 6.157, 4: 4.054, 8: 2.978, 16: 2.679, 32: 2.658},
        480: {1: 9.367, 2: 6.031, 4: 4.095, 8: 3.211, 16: 3.076, 32: 3.064},
        640: {1: 10.589, 2: 7.177, 4: 4.766, 8: 4.096, 16: 4.200, 32: 4.216},
        800: {1: 10.799, 2: 7.967, 4: 5.604, 8: 5.102, 16: 5.285, 32: 5.198},
        960: {1: 11.342, 2: 8.313, 4: 6.565, 8: 6.270, 16: 6.110, 32: 6.345}}


def cost(width: int, batch: int) -> float:
    ws = sorted(SURF)
    b = min(SURF[ws[0]], key=lambda x: abs(x - batch))
    lo = max([w for w in ws if w <= width], default=ws[0])
    hi = min([w for w in ws if w >= width], default=ws[-1])
    if lo == hi:
        return SURF[lo][b]
    f = (width - lo) / (hi - lo)
    return SURF[lo][b] * (1 - f) + SURF[hi][b] * f


def load(p: str):
    meta, per = {}, defaultdict(list)
    with open(p, encoding="utf-8") as fh:
        for ln in fh:
            o = json.loads(ln)
            if "_meta" in o:
                meta = o["_meta"]
                continue
            per[o["seq"]].append(o)
    return meta, [(s, per[s]) for s in sorted(per)]


class Batches:
    """结算一次 = 把排队的裁剪按（近）宽分桶送一批批。"""

    def __init__(self, ratio: float) -> None:
        self.ratio, self.ms, self.crops, self.buckets, self.settles = ratio, 0.0, 0, 0, 0

    def settle(self, q: list[int]) -> None:
        if not q:
            return
        for g in recpack.group_near(q, self.ratio, a.cap):
            w = max(q[i] for i in g)
            self.ms += len(g) * cost(w, len(g))
            self.crops += len(g)
            self.buckets += 1
        self.settles += 1
        q.clear()

    def eq(self) -> float:
        return self.ms / max(1, self.crops)


print(f"{'段':<16}{'窗口':>5}{'策略':>6}{'近宽':>6}{'结算':>6}{'平均窗长':>9}{'平均批':>8}"
      f"{'等效 ms/裁剪':>13}{'对逐帧':>9}{'屏障/回滚':>11}")
for tp in a.traces:
    meta, frames = load(tp)
    name = meta.get("tag", Path(tp).stem)[:15]
    lo = meta.get("reuse_corr") or 0.8
    hi = meta.get("reuse_corr_digit") or 0.98
    # 基准：今天的逐帧分桶（回补那几次也是逐次单框，所以各算一次）
    base = Batches(0.0)
    for _s, rows in frames:
        q = [r["tw"] for r in rows if r["read"] and r["gate"] != "recover"]
        base.settle(q)
        for r in rows:                              # 回补：一次一个框，单独结算
            if r["gate"] == "recover":
                for _ in range(r.get("n_reads", 1)):
                    base.settle([r["tw"]])
    b_eq = base.eq()
    print(f"{name:<16}{1:>5}{'逐帧':>6}{'0%':>6}{base.settles:>6}{1.0:>9.2f}"
          f"{base.crops / max(1, base.buckets):>8.2f}{b_eq:>13.2f}{'—':>9}{'—':>11}")
    # 真实的数字性翻转次数（A 那条路要回滚的就是这些）
    last_digit: dict[int, bool] = {}
    flips = 0
    for _s, rows in frames:
        for r in rows:
            c = r.get("chain")
            if c is None or r.get("digit") is None:
                continue
            if c in last_digit and last_digit[c] != r["digit"]:
                flips += 1
            last_digit[c] = r["digit"]
    for W in [int(x) for x in a.windows.split(",")]:
        for ratio in [float(x) for x in a.ratios.split(",")]:
            for mode in ("A'", "A", "B"):
                bt = Batches(ratio)
                q: list[int] = []
                pending: set = set()      # 这一窗里被读过、文本还没回来的链 uid
                wlen = wsum = barrier = extra = 0
                for _s, rows in frames:
                    for r in rows:
                        c = r.get("chain")
                        # **判决依赖不依赖待定的文本**：只有相关系数落在两门之间才依赖（见文件头）
                        amb = (c in pending and r.get("corr") is not None and lo <= r["corr"] < hi)
                        if mode == "A'" and amb:
                            barrier += 1
                            bt.settle(q)
                            pending.clear()
                            wsum += wlen
                            wlen = 0
                        read = r["read"]
                        if mode == "B" and amb and not read:
                            # **B：文本待定时一律按严格门（0.98）判**——判决不再依赖未知文本，
                            # 于是不用屏障也不用回滚；代价是这个框改成真读（**产物会变，方向是"更新"**）
                            read, extra = True, extra + 1
                        if read:
                            for _ in range(r.get("n_reads", 1) if r["gate"] == "recover" else 1):
                                q.append(r["tw"])
                            if c is not None and r["gate"] != "recover":
                                pending.add(c)
                    wlen += 1
                    if wlen >= W:
                        bt.settle(q)
                        pending.clear()
                        wsum += wlen
                        wlen = 0
                bt.settle(q)
                note = (f"屏障 {barrier}" if mode == "A'" else
                        f"回滚 ≤{flips}" if mode == "A" else f"多读 {extra}")
                print(f"{name:<16}{W:>5}{mode:>6}{f'{ratio:.0%}':>6}{bt.settles:>6}"
                      f"{wsum / max(1, bt.settles):>9.2f}{bt.crops / max(1, bt.buckets):>8.2f}"
                      f"{bt.eq():>13.2f}{f'{bt.eq() / b_eq - 1:+.1%}':>9}{note:>11}")
print("\n和 `rec_window_sim.py`（拿 obs 近似的那份）比：链身份用 uid、屏障只在「真的依赖待定文本」时才竖、"
      "回补的读也进批。**这一份才是可引用的**。")
