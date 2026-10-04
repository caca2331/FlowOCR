"""最小输出预设（可复制改）：每条区域轨各写一份 SRT，文件名前面加个前缀。

用法：
    python -m flowocr.output.render out/x/x-tracks.json --preset examples/preset_minimal.py --outdir tmp/x --opt prefix=my-

入口就一个函数 `render(document, output_dir, options) -> list[Path]`（见 `flowocr.extensions` 文件头）。
`document` 是 `*-tracks.json` 读进来的 dict：`document["tracks"]` 每条轨带 `id` / `kind` / `label` / `srt` / `cues`，
遍历轨**用白名单 `kind == "region"`**（名牌轨和区域轨装的是同一批事件，黑名单会写两遍）。
要自由发挥就自己拼字幕文本；要偷懒就用现成的投影：SRT 是 `flowocr.output.export.export_srt`，
叠加字幕是 `flowocr.output.script.render_script`（写可编辑的字幕稿）+ `flowocr.typeset.core.typeset_file`（生成最终 ASS）；
只想换个样子，往字幕稿 Effect 字段的 `fo:…` 里加自己的参数、写一个扩展效果（`examples/fx_minimal.py`）通常就够了。
"""
from __future__ import annotations

from pathlib import Path

from flowocr.artifacts import srtio, tracksio


def render(document: dict, output_dir, options: dict | None = None) -> list[Path]:
    o = dict(options or {})
    prefix = str(o.get("prefix", "region-"))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for tr in document["tracks"]:
        if tr.get("kind") != "region":
            continue
        blocks = []
        for cue in tr["cues"]:
            lines = tracksio.cue_lines(document, tr, cue)     # 已按这条轨的 ui_lines 剔过常驻 UI
            if lines:
                blocks.append((cue["t_start"], cue["t_end"], [e["text"] for e in lines]))
        p = out / f"{prefix}{tr['id']}-{tr['label']}.srt"
        srtio.write_srt_blocks(p, blocks)
        written.append(p)
    return written
