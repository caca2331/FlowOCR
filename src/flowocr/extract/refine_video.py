"""回抠的任务单：每条 run 一份 job，阶段 2 往上挂证据、结算（`flowocr.analyze.refine_boundaries`）。

像素测量只在阶段 1 在线做（`run_ocr2 --refine-fused` -> `edge_refine`，证据写进 obs 行的 `edge`）；开视频按帧号区间开窗的离线测量
（`refine_ffmpeg` / `refine_sequential` / `measure`）frame-pts 计划删了：产物流程只走 obs 里的在线证据，在线证据的质量由打字机真值把关。
"""
from __future__ import annotations


def make_job(run: dict, frame_us: int) -> dict:
    """一条 run 的回抠任务：证据（`ev_on` / `ev_off`）、OCR 中途读数（`ocr`）由 `refine_boundaries` 往上挂，`measured` 由证据重建，
    `apply` 结算后写 `refined` / `moved` / `d_start` / `d_end`。`frame_us` 是名义采样间隔（µs）。"""
    return {"run": run, "box": run["box"], "frame_us": frame_us, "ocr": [],
            "refined": False, "moved": False, "d_start": 0.0, "d_end": 0.0}
