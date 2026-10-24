"""输出预设的例子（可复制改）：和 `default_srt` 同一批条目，换一条文字规则——一条 cue 的几行拼成一行。

用法：
    python -m flowocr.output.render out/x/x-tracks.json --preset examples/preset_srt_oneline.py --outdir tmp/x

`default_srt` 的条目和 `default` 的字幕稿是同一批（`flowocr.output.script.build_items`），`script.srt_blocks` 把它们投成 SRT 的块；
`text` 参数是"一个条目 → 几行字"的规则，换掉它就能改 SRT 的样子（拼成一行、加说话人前缀……），条目怎么挑不用重写。
匹配产物上画哪些按显示协议（`--opt show=…` / `hide=…`，同内置预设；`build_items` 里算），同一句的同时回显 / 记录面板回显默认不出，
`--opt echo=keep` 放回来（同 `default_srt`）。
"""
from __future__ import annotations

from pathlib import Path

from flowocr import placeholders
from flowocr.artifacts import matchedio, srtio, tracksio
from flowocr.output import script


def one_line(it: script.Item) -> list[str]:
    """几行拼成一行：交界处有一边是全角字（中日韩）就直接相连，否则空一格。"""
    out = ""
    for ln in script.srt_text(it):
        out = tracksio.join_row_text(out, ln)
    return [out] if out else []


def render(document: dict, output_dir, options: dict | None = None) -> list[Path]:
    echo = str((options or {}).get("echo", "drop"))
    matchedio.echo_kept({}, echo)                    # 先核取值（同内置预设）
    marks = placeholders.Marks(document.get("fills"), str((options or {}).get("lang", "jp")))   # 剧本的占位标成 [`编号:值]，SRT 里由人改
    items, _doc, base, _note, _src = script.build_items(document, options, align=False, marks=marks)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{base}-oneline.srt"
    blocks = script.srt_blocks(items, text=one_line, echo=echo)
    srtio.write_srt_blocks(p, blocks)
    marks.say("SRT ", srt=True, texts=[ln for b in blocks for ln in b[2]])
    return [p]
