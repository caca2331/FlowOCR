"""内置预设：把**匹配产物**（`*-matched.json`）拍平成一份 SRT——匹配上的出剧本原文，没匹配上原样留 OCR。

和 `scriptmatch --srt` 写的**逐字节相同**（同一份 `cues`、同一个 `matchedio.body`、同一个 `srtio` 写出；守卫钉着）。
⚠ SRT 是**拍平投影，只出主 ref**：覆盖（ref ∪ extra）比这里出现的多，量覆盖看匹配产物本身（matcher 计划）。

options：
* `lang`：`jp`（默认）/ `cn`，正文取哪种语言；
* `name`：文件名（默认 `<matched 的 stem 去掉 -matched>-<lang>.srt`）。
"""
from __future__ import annotations

from pathlib import Path

from flowocr.artifacts import matchedio, srtio

ACCEPTS = (matchedio.SCHEMA,)
"""这个预设吃哪几种阶段 2 产物（`flowocr.output.render` 按 `document["schema"]` 核）。"""


def render(document: dict, output_dir, options: dict | None = None) -> list[Path]:
    o = dict(options or {})
    matchedio.validate(document)
    lang = str(o.get("lang", "jp"))
    if lang not in ("jp", "cn"):
        raise ValueError(f"lang 只能是 jp / cn：{lang!r}")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(matchedio.tracks_path(document) or "matched").stem.removesuffix("-tracks")
    p = out / str(o.get("name") or f"{stem}-{lang}.srt")
    srtio.write_srt_blocks(p, ((int(r["start"] * 1e6), int(r["end"] * 1e6), matchedio.body(r, lang))
                               for r in document["cues"]))
    return [p]
