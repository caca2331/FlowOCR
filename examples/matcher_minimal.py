"""最小匹配器（可复制改）：不改聚类规则，把每条主轨 cue 的文本查一张自己的对照表，命中就换成表里的原文。

入口两个（见 `flowocr.extensions` 文件头）：
* `cluster_patch(context, options) -> dict`：给默认聚类器（build_tracks）的参数覆盖，键是参数名（如 `region_time_gate`）。
  不需要就返回空字典，或干脆不写这个函数。
* `match(document, context, options) -> dict`：`document` 是 `*-tracks.json` 的 dict，返回带匹配结果的产物。
  这里返回的是一份最小的记录表（`cues` 每条一个 dict，`ref` 为命中的原文或 None），格式随你；
  内置的 `gametext` 返回的是 `scriptmatch.SCHEMA` 那种完整产物。

`options["table"]` 是一份 JSON 文件路径：`{"ocr 读到的文本": "原文", ...}`。
"""
from __future__ import annotations

import json
from pathlib import Path

from flowocr.artifacts import tracksio


def cluster_patch(context: dict, options: dict | None = None) -> dict:
    return {}


def match(document: dict, context: dict, options: dict | None = None) -> dict:
    o = dict(options or {})
    table = json.loads(Path(o["table"]).read_text(encoding="utf-8")) if o.get("table") else {}
    tr = tracksio.main_track(document)
    cues = []
    for cue in (tr["cues"] if tr else []):
        text = "\n".join(e["text"] for e in tracksio.cue_lines(document, tr, cue))
        cues.append({"start": cue["t_start"] / 1e6, "end": cue["t_end"] / 1e6,
                     "raw": text, "ref": table.get(text)})
    return {"schema": "example-matched/1", "stats": {"cues": len(cues), "hit": sum(1 for c in cues if c["ref"])},
            "cues": cues}
