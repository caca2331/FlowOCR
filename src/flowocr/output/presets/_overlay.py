"""三个叠加预设（`default` / `default_all` / `dev`）的共同实现：阶段 3 写字幕稿、阶段 4 接着生成最终 ASS，两份都留下。

| 预设 | 阶段 3（留哪些） | 阶段 4（长什么样） |
| --- | --- | --- |
| `default` | `keep=main`：tracks 主轨 + 名牌轨 / matched 的 body、extra、name | `default`：底板 α 0x20 |
| `default_all` | `keep=all`：全部区域轨 / 全部层 + 没叠译文的原文 | `default` |
| `dev` | `keep=all`，另写 `why` | `dev`：底板 α 0x80、UI 黄字、非主轨浅蓝、区域号 |

options 按键分给两个阶段：`keep` `why` `lang` `layers` `tracks` 给阶段 3（`flowocr.output.script.render_script`），
`plate` `plate_alpha` `unknown` 给阶段 4（`flowocr.typeset.core`），`typewriter` `fade` 两边都给
（阶段 3 的 off 是不判、不写，阶段 4 的 off 是不画；`typewriter=off` 时阶段 3 照原来的规矩改判淡入）。
`name` 是最终 ASS 的文件名，字幕稿在它旁边、`.ass` 换成 `.script.ass`。
阶段 3 量好的字宽直接交给阶段 4，不再起一遍 ffmpeg。返回 `[最终 ASS, 字幕稿]`（`dev_tools/preview.py`、`scriptmatch --ass` 靠这个顺序和后缀）。
"""
from __future__ import annotations

from pathlib import Path

from flowocr.output import script
from flowocr.typeset import core

PRESETS = {"default": ("main", "off", "default"), "default_all": ("all", "off", "default"), "dev": ("all", "on", "dev")}
STAGE3 = ("keep", "why", "lang", "layers", "tracks", "typewriter", "fade")
STAGE4 = ("plate", "plate_alpha", "unknown", "typewriter", "fade")


def render(document: dict, output_dir, options: dict | None, preset: str) -> list[Path]:
    o = dict(options or {})
    keep, why, look = PRESETS[preset]
    o3 = {"keep": keep, "why": why, **{k: v for k, v in o.items() if k in STAGE3}}
    o4 = {k: v for k, v in o.items() if k in STAGE4}
    core.look_of(look, o4)                          # 选项写错了在写字幕稿之前就报
    final = None
    if o.get("name"):
        name = str(o["name"])
        o3["name"] = (name[:-4] if name.endswith(".ass") else name) + ".script.ass"
        final = Path(output_dir) / name             # 最终文件就叫给的名字（不以 .ass 结尾也不改）
    draft, _n, _items, widths = script.render_script(document, output_dir, o3, suffix=preset)
    final, _ts = core.typeset_file(draft, final, look, o4, widths=widths)
    return [final, draft]
