"""素材根（开发驱动 / 探针用的视频放在哪）：`FLOWOCR_ARCHIVE`，没设就读数据根下的 `data/archive-root.txt`（一行路径）。

素材在各人机器上的位置不同，不写进版本库；`data/` 整个只留本地。"""
from __future__ import annotations

import os
from pathlib import Path

from flowocr import paths


def archive_root() -> Path:
    configured = os.environ.get("FLOWOCR_ARCHIVE", "").strip()
    if configured:
        return Path(configured).expanduser()
    f = paths.data_root() / "data" / "archive-root.txt"
    if f.is_file() and f.read_text(encoding="utf-8").strip():
        return Path(f.read_text(encoding="utf-8").strip()).expanduser()
    raise SystemExit("不知道素材在哪：设 FLOWOCR_ARCHIVE，或在数据根的 data/archive-root.txt 里写一行路径")
