"""内置预设 `default`（**生产默认**，owner 2026-09-24；`render` 不给 `--preset` 时就是它）：只留主轨的叠加字幕。

* tracks 产物：主轨（和 `srt_main` 同一个行集，常驻 UI 按主轨自己的判定剔掉）+ 名牌轨；
* matched 产物：`body` / `extra` / `name` 三层——认领上剧本的台词，不论在不在主轨（`--feed nonoise` 多出来的正是主轨外的台词）；
* 样式：底板 `rows`、近黑、α 0x20；对齐按 `regions[].align`，效果检出即开。

写两份：字幕稿 `<tag>[-<lang>]-default.script.ass`（阶段 3，能在 Aegisub 里编辑）和最终的 `<tag>[-<lang>]-default.ass`（阶段 4）。
选项和实现见 `flowocr.output.presets._overlay`；方案见 script-fx 计划。
"""
from __future__ import annotations

from pathlib import Path

from flowocr.artifacts import matchedio, tracksio
from flowocr.output.presets import _overlay

ACCEPTS = (tracksio.SCHEMA, matchedio.SCHEMA)


def render(document: dict, output_dir, options: dict | None = None) -> list[Path]:
    return _overlay.render(document, output_dir, options, "default")
