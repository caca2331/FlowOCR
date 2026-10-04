"""最小扩展效果（可复制改）：给带 `frame` 参数的源行，沿原文框外画一圈空心框。

用法：在字幕稿（`*.script.ass`）某行 Effect 字段的 `fo:…` 里加一项 `;frame`（或 `;frame=0000FF`，BBGGRR 的框色），然后
    python -m flowocr.typeset tmp/x/x-main.script.ass --fx examples/fx_minimal.py

一个效果模块要有 `NAME`（它在 `fo` 里的参数名）和两个挂点里至少一个（见 `flowocr.typeset.fx` 的文件头）：
* `elements(line, value)`：第 2 步，读排好版的 `line`（`flowocr.typeset.core.Line`：原文框 `rows`、字号、底板、起止…），返回要加的元素；
* `tags(line, value, element)`：第 4 步，改每个元素的 tag。
元素的位置按条目框左上角排，在动的字会和底板、正文一起按轨迹走。
"""
from __future__ import annotations

from flowocr.typeset.core import Element

NAME = "frame"
PAD = 10


def elements(line, value):
    if not line.rows:
        return []
    color = value if isinstance(value, str) else "FFFFFF"
    x0, y0 = min(r[0] for r in line.rows) - PAD, line.rows[0][1] - PAD
    w, h = max(r[2] for r in line.rows) + PAD - x0, line.rows[-1][3] + PAD - y0
    return [Element(0, x0, y0, "\\an7", "\\p1\\bord2\\shad0\\1a&HFF&\\3c&H%s&" % color,
                    "m 0 0 l %.0f 0 %.0f %.0f 0 %.0f{\\p0}" % (w, w, h, h), "frame")]
