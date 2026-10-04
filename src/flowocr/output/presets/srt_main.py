"""内置预设：**只取主轨**的一份 SRT（project-structure："SRT 默认可以只取主轨"）。

从 `*-tracks.json` 投影（`export.export_srt`），和 `build_tracks` 写在产物旁的那份主轨 SRT **逐字节相同**——
同一份 cue 表、同一份 `ui_lines`，导出器不重新判断（docs/architecture/artifacts.md）。

options：
* `start`：cue 起点口径，`first`（默认，首字出现）/ `full`（全字出现）；
* `name`：文件名（默认用 provenance 里的 `main_srt`，即 build_tracks 写的那个名字）。
"""
from __future__ import annotations

from pathlib import Path

from flowocr.artifacts import tracksio
from flowocr.output import export

ACCEPTS = (tracksio.SCHEMA,)
"""这个预设吃哪几种阶段 2 产物（`flowocr.output.render` 按 `document["schema"]` 核）。"""


def render(document: dict, output_dir, options: dict | None = None) -> list[Path]:
    o = dict(options or {})
    tid = document["provenance"].get("main_track") or ""
    if not tid:
        raise ValueError("这份产物没有主轨（provenance.main_track 为空）——挑不出主轨时用别的预设或自己写一个")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = [out / name for name, _n in export.export_srt(document, out, only=tid, start=o.get("start", "first"))]
    if o.get("name"):
        target = out / str(o["name"])
        written[0].replace(target)
        written = [target]
    return written
