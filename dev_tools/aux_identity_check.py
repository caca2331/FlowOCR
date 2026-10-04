"""**采样帧和辅助帧环里的同号帧，是不是同一张画面**（2026-09-20，Codex 复审 P1 的契约测试）。

病（复审第 1 条，已复现）：`--frame-select pts` 之后主路按 PTS 选帧，采样结果却仍按
`start_idx + n*stride` 编号；辅助路按**实际交付的第几帧**编号（`AuxReader` 推 `start_idx + k*step`）。
流完好时两者一致；**一遇到缺口就错位**——回抠拿着"第 25 帧"的号，从环里取到的是另一张画面，
算出来的边界是错画面上的边界，而**时间锚只能改时间标签，修不了画面对应关系**。

量法同 `sample_grid_check.py`：合成视频每帧一块均匀灰度（灰度 = 帧号 × 4），
像素直接说出"这是第几帧"。走生产路径 `framesource.make_source`（带辅助流），
辅助流推进一个只记录的假帧环。

    python dev_tools/pyrun.py dev_tools/aux_identity_check.py

判据：①每个采样帧的**画面帧号** == 它报的 `idx`；②环里每个键 k 的画面帧号 == k；
③所以同号必同画面；④环里的键正好是辅助流网格上的帧、采样帧正好是采样网格上的帧（缺口里的除外）。四种情形都要过：跨缺口、非零起点、
等距隔帧、不整除的时间网格（2026-09-24，`flowocr.extract.framegrid`）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "dev_tools"))
sys.path.insert(0, str(CODE / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.extract import framesource  # noqa: E402
from flowocr.extract.framegrid import AuxGrid, TimeGrid  # noqa: E402
from flowocr import paths  # noqa: E402
os.chdir(paths.data_root())

ap = argparse.ArgumentParser()
ap.add_argument("--hwaccel", default="", help="给 cuda 就连硬解一起验（要 GPU）")
a = ap.parse_args()

FPS, N = 10.0, 60
Y0, DY = 20, 3                     # 亮度 = 20 + 帧号 × 3：避开 YUV 有限范围 16 以下那段（会被压成 0、分不出帧）
GAP = (25, 31)                     # 删掉的帧（含两端）——复审里用的就是这一段
OUT = Path("tmp/gapid")
OUT.mkdir(parents=True, exist_ok=True)
VID = OUT / "gap.mp4"


def synth() -> None:
    """10 fps、60 帧、灰度 = 帧号 × 4，删掉 25–31 帧并**保留原 pts**（`-fps_mode passthrough`）。"""
    vf = rf"select='not(between(n\,{GAP[0]}\,{GAP[1]}))'"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"nullsrc=s=64x64:r={FPS:g}:d={N / FPS:g},geq=lum='{Y0}+N*{DY}':cb=128:cr=128",
                    "-vf", vf, "-fps_mode", "passthrough", "-c:v", "libx264", "-crf", "0",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(VID)], check=True)


def who(frame: np.ndarray) -> int | None:
    """像素说它是第几帧。⚠ 转 RGB / 灰度时有限范围的 Y 会被拉成全范围 `(Y-16)*255/219`，
    要先反算回 Y——第一版没反算，缺口之前号和画面就"对不上"，那是探针自己错了（2026-09-20）。"""
    g = frame if frame.ndim == 2 else frame[:, :, 0]
    v = float(np.median(g[g.shape[0] // 4: g.shape[0] * 3 // 4, g.shape[1] // 4: g.shape[1] * 3 // 4]))
    y = v * 219 / 255 + 16
    i = round((y - Y0) / DY)
    return i if abs(y - (Y0 + i * DY)) <= 1.2 else None


class Recorder:
    """只记录的假帧环：`AuxReader` 只用 push / close 两个方法。**同一个键推两次 = 覆盖**，单独数出来。"""

    def __init__(self) -> None:
        self.got: dict[int, int | None] = {}
        self.overwrites = 0

    def push(self, idx: int, frame: np.ndarray) -> None:
        self.overwrites += idx in self.got
        self.got[idx] = who(frame)

    def close(self) -> None:
        pass


def production_rate() -> tuple[float, float, float]:
    """**和 run_ocr2 同一个推导**（复审第三轮：原来这里写死 10 fps，而生产代码用的是容器报的帧率——
    这份视频的平均帧率是 8.83，按它取整辅助流会撞出 6 个重复键）。返回 (生产用的, 名义, 平均)。"""
    import cv2
    from flowocr.extract import ocr_parallel
    cap = cv2.VideoCapture(str(VID))
    avg = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    nominal = ocr_parallel.probe_rates(str(VID))[0]
    return framesource.id_rate(nominal, avg), nominal, avg


def case(name: str, start_idx: int, grid: AuxGrid) -> int:
    rec = Recorder()
    fps = production_rate()[0]
    src = framesource.make_source(
        str(VID), backend="ffmpeg", grid=grid.sample, start_idx=start_idx, end_idx=N,
        src_fps=fps, width=64, height=64, pix="bgr24", hwaccel=a.hwaccel or None,
        select_by="pts", aux={"sink": rec, "grid": grid.as_list(), "scale": 0.5})
    samples = [(idx, who(f), t) for idx, t, f in src]
    inner = getattr(src, "inner", src)
    if inner.aux is not None:
        inner.aux.th.join(timeout=10)
    bad_s = [(i, w) for i, w, _ in samples if w != i]
    bad_a = [(k, w) for k, w in rec.got.items() if w != k]
    # 同号同画面：采样帧的号在环里存在时，环里那张必须是同一帧
    shared = [i for i, _, _ in samples if i in rec.got]
    bad_x = [(i, rec.got[i]) for i in shared if rec.got[i] != i]
    dup_s = len(samples) - len({i for i, _, _ in samples})
    # 环里的键 = 网格上的帧（缺口里那几帧源里没有）
    want = {i for i in grid.frames(start_idx, N - 1) if not GAP[0] <= i <= GAP[1]}
    bad_g = sorted(set(rec.got) ^ want)
    # 采样帧 = 采样网格上的帧（缺口里的除外）
    bad_g += sorted({i for i, _, _ in samples} ^ {i for i in grid.sample.frames(start_idx, N - 1) if not GAP[0] <= i <= GAP[1]})
    ok = not (bad_s or bad_a or bad_x or dup_s or bad_g or rec.overwrites or getattr(inner.aux, "misaligned", ""))
    if bad_g:
        print(f"      环里的键 / 采样帧和网格对不上（对称差前几个）：{bad_g[:8]}")
    if dup_s or rec.overwrites or getattr(inner.aux, "misaligned", ""):
        print(f"      采样重号 {dup_s}、帧环覆盖 {rec.overwrites}、辅路报错 {getattr(inner.aux, 'misaligned', '')!r}")
    print(f"  {name:<28} 采样 {len(samples)} 帧（号≠画面 {len(bad_s)}）｜环 {len(rec.got)} 帧（号≠画面 {len(bad_a)}）"
          f"｜同号 {len(shared)}（不同画面 {len(bad_x)}）  {'✅' if ok else '❌'}")
    for lst, what in ((bad_s, "采样"), (bad_a, "环"), (bad_x, "同号")):
        if lst:
            print(f"      {what}前几个（号, 画面）：{lst[:5]}")
    return 0 if ok else 1


def main() -> int:
    if not VID.exists():
        synth()
    use, nominal, avg = production_rate()
    print(f"{VID}：10 fps、60 帧、删掉第 {GAP[0]}–{GAP[1]} 帧（pts 2.4 s -> 3.2 s）"
          f"{'、硬解 ' + a.hwaccel if a.hwaccel else '、软解'}；容器报 名义 {nominal} / 平均 {avg:.6f}，"
          f"**生产代码用 {use}**")
    bad = 0
    every5 = TimeGrid.every(5)
    bad += case("跨缺口，从 0 起", 0, AuxGrid(TimeGrid(1, 1), every5))
    bad += case("跨缺口，非零起点（第 10 帧）", 10, AuxGrid(TimeGrid(1, 1), every5))
    bad += case("跨缺口，辅助隔 5 帧（等距）", 0, AuxGrid(every5, every5))
    bad += case("跨缺口，辅助不整除 2/3 + 嵌套", 0, AuxGrid(TimeGrid(2, 3), every5))
    bad += case("跨缺口，采样不整除 2/7、辅助每帧", 0, AuxGrid(TimeGrid(1, 1), TimeGrid(2, 7)))
    bad += case("跨缺口，采样 2/7、辅助 3/5（都不整除）", 0, AuxGrid(TimeGrid(3, 5), TimeGrid(2, 7)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
