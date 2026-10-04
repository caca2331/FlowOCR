""""两行文本差在哪一档"——**这个判据只有一份**，`arm_vs_truth.py` 和 `rec_engine_accept.py` 都 import 它。

从 `arm_vs_truth.py` 里搬出来的（2026-09-18），一个字没改。搬的理由是那边的文件头写的那件事：
这几栏的**名字**和**筛掉了多少**是结论的一部分（2026-09-17 审计：报一个 0 的时候要连筛子一起报），
而第二个调用方（换 rec 引擎的臂验收）如果自己抄一份，两处的门迟早会飘——
这个项目在"同一条规则抄两份"上栽过（methodology-audit 报告：5 个工具各写各的 SRT 解析）。

⚠ `SequenceMatcher.ratio()` **不对称**（`2349PTS` / `452408PTS` 两个方向是 0.5 和 0.625，
正好跨过 0.6 这道门），所以参数顺序要和 `sim_policy` 一致：**new = 真值 / 屏幕，old = 手里的**。
这一栏本来就只当筛子用，别拿它当分数。
"""
from __future__ import annotations

import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "dev_tools"))
sys.path.insert(0, str(CODE / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr import paths  # noqa: E402
os.chdir(paths.data_root())
from flowocr.artifacts import evalkit  # noqa: E402
from flowocr.analyze import gamescript as GS  # noqa: E402


def diff(new, old, conf_gate: float = 0.9) -> str:
    """"" = 没差 / 被门筛掉，"tw" = 前缀（打字机），"sub" = 严重不一致，"near" = 不同但相似度 >= 0.6，
    "lowconf" / "short" = 被 conf / 长度门筛掉但文本确实不同（**筛掉的也要数**）。"""
    x, y = GS.cnorm(new["text"]), GS.cnorm(old["text"])
    if x == y:
        return ""
    if min(new["conf"], old["conf"]) < conf_gate:
        return "lowconf"
    if min(evalkit.content_len(new["text"]), evalkit.content_len(old["text"])) < 3:
        return "short"
    if x.startswith(y) or y.startswith(x):
        return "tw"
    return "sub" if SequenceMatcher(None, x, y).ratio() < 0.6 else "near"
