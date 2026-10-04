"""两份 obs 的**文本**对不对得上（分数 / 时间允许不同）——换 rec 后端时的产物对账。

`obs_identical.py` 要的是逐字节相同（`--det-prefetch` 那种"只改什么时候算"的改动）。
换推理后端不是那种：fp16 会让 `conf` 动（实测最大 0.09），而 `conf` 又参与复用判据的门，
于是**行数和 rec 次数都会差一点**。所以这份工具按 (帧, 框) 配对，只比文本，并把分数差的分布打出来。

⚠ **配对和计数走 `tools/ocr_ab_report.compare_obs`，这里不写第二份**（2026-09-20）：
那边的报表以前在"框序列不同"时把 `None` 当 0 加进汇总，印出"文本改 0（0.000%）"，
而同一批产物在这里量出来是 97.6%~98.1% 相同——**两份实现各说各话，正是这个项目栽过的形状**。
现在两边同一个函数，这一段里 `q-hsr-s2` 154 / `q-gi-s1` 218 两边逐数相同。

usage: python dev_tools/obs_textdiff.py <a.jsonl> <b.jsonl> [--show 6]
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev_tools"))
from ocr_ab_report import compare_obs  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("a")
ap.add_argument("b")
ap.add_argument("--show", type=int, default=6)
a = ap.parse_args()


def rows(p: str) -> dict:
    out = {}
    with open(p, encoding="utf-8") as fh:
        first = fh.readline()
        if '"_meta"' not in first:
            fh.seek(0)
        for ln in fh:
            o = json.loads(ln)
            out[(o["frame"], tuple(o["box"]))] = o
    return out


ra, rb = rows(a.a), rows(a.b)
common = ra.keys() & rb.keys()
# **计数走共享实现**（见文件头）：这里只负责读文件、打分布和列样例
c = compare_obs(list(ra.values()), list(rb.values()))
print(f"{Path(a.a).name}: {c['rows_a']} 行 / {Path(a.b).name}: {c['rows_b']} 行；"
      f"(帧, 框) 都对得上的 {c['cmp_rows']}（各自独有 {c['only_a']} / {c['only_b']}）")
print(f"共有那批里**文本相同** {c['cmp_rows'] - c['text_diff']}/{c['cmp_rows']} = "
      f"{(c['cmp_rows'] - c['text_diff']) / max(1, c['cmp_rows']):.4%}")
dc = [abs((ra[k].get("conf") or 0) - (rb[k].get("conf") or 0)) for k in common]
if dc:
    print(f"conf 差：中位 {st.median(dc):.4f}、P99 {sorted(dc)[int(len(dc) * 0.99)]:.4f}、最大 {max(dc):.4f}")
    print(f"**跨过 conf 0.5 那道门的行 {c['gate_cross']}**（下游按这个门筛，所以它才是要看的数）")
bad = [k for k in common if ra[k].get("text") != rb[k].get("text")]
for k in bad[:a.show]:
    print(f"   ⚠ 帧 {k[0]} 框 {k[1]}：A『{ra[k].get('text')}』 B『{rb[k].get('text')}』")
