"""**采样帧和辅助帧环里的同一个键，是不是同一张画面**（2026-09-20 Codex 复审 P1 的契约测试；frame-pts 计划改成按时间戳）。

病（复审第 1 条，已复现）：主路按 PTS 选帧、辅助路按"实际交付的第几帧"编号，一遇到缺口就错位——回抠拿着同一个号从环里取到的是
另一张画面。现在两路的身份都是**这一帧自己的 pts**（`t_us`，`framegrid.frame_key`），这里核"同一个 t_us 同一张画面"。

量法：合成视频每帧一块均匀灰度（灰度 = 20 + 帧序号 × 3），10 fps、60 帧、删掉一段帧且保留原 pts（缺口），像素直接说出"这是第几帧"，
帧序号 × 100 ms 就是它的 t_us。走生产路径 `framesource.make_source`（带辅助流），辅助流推进一个只记录的假帧环。

    python dev_tools/pyrun.py dev_tools/aux_identity_check.py [--hwaccel cuda]

判据：①每个采样帧的画面 == 它的 t_us；②环里每个键的画面 == 那个 t_us；③所以同一个键必同画面；
④环里的键 / 采样帧正好是 Python 按时间桶（`framegrid`）在源文件的 pts 上模拟出的那一批（缺口里的源里没有）。
几种情形都要过：跨缺口、非零起点、辅助流隔帧、采样 / 辅助帧率不整除素材帧率。
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
from flowocr.extract.framegrid import AuxClock, Axis, Clock, SampleClock, Window  # noqa: E402
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

    def push(self, key: int, frame: np.ndarray, cells=None) -> None:
        self.overwrites += key in self.got
        self.got[key] = who(frame)

    def close(self) -> None:
        pass


SRC = [i for i in range(N) if not GAP[0] <= i <= GAP[1]]
"""源文件里有的帧（帧序号；pts = 帧序号 / 10 s）。"""


def simulate(clock: AuxClock, win: Window) -> tuple[list[int], list[int]]:
    """按时间桶在源文件的 pts 上模拟两路该选的帧（帧序号）：每格（采样桶 / 辅助桶与采样桶）的第一帧，限在窗口里。"""
    axis = Axis((1, int(FPS)))                     # 合成视频的 start_time 是 0
    s_want, a_want, ps, pa = [], [], None, None
    for i in SRC:
        sc, ac = clock.cells(i, axis)
        if win.contains(sc):
            if sc != ps:
                s_want.append(i)
            if (sc, ac) != pa:
                a_want.append(i)
        ps, pa = sc, (sc, ac)
    return s_want, a_want


def case(name: str, start: float, clock: AuxClock) -> int:
    rec = Recorder()
    st = framesource.probe_stream(str(VID))
    win = Window.of(clock.sample, start, 0)
    src = framesource.make_source(str(VID), stream=st, window=win, width=64, height=64, pix="bgr24",
                                  hwaccel=a.hwaccel or None, aux={"sink": rec, "clock": clock.as_list(), "scale": 0.5})
    samples = [(f.key, who(f.image)) for f in src]
    inner = getattr(src, "inner", src)
    if inner.aux is not None:
        inner.aux.th.join(timeout=10)
    idx = lambda key: round(key / 100_000)                        # noqa: E731   t_us -> 帧序号（10 fps）
    bad_s = [(k, w) for k, w in samples if w != idx(k)]
    bad_a = [(k, w) for k, w in rec.got.items() if w != idx(k)]
    shared = [k for k, _ in samples if k in rec.got]
    bad_x = [(k, rec.got[k]) for k in shared if rec.got[k] != idx(k)]
    dup_s = len(samples) - len({k for k, _ in samples})
    s_want, a_want = simulate(clock, win)
    bad_g = sorted({idx(k) for k in rec.got} ^ set(a_want)) + sorted({idx(k) for k, _ in samples} ^ set(s_want))
    ok = not (bad_s or bad_a or bad_x or dup_s or bad_g or rec.overwrites or getattr(inner.aux, "misaligned", ""))
    if bad_g:
        print(f"      环里的键 / 采样帧和按时间桶模拟的对不上（对称差前几个）：{bad_g[:8]}")
    if dup_s or rec.overwrites or getattr(inner.aux, "misaligned", ""):
        print(f"      采样重号 {dup_s}、帧环覆盖 {rec.overwrites}、辅路报错 {getattr(inner.aux, 'misaligned', '')!r}")
    print(f"  {name:<30} 采样 {len(samples)} 帧（键≠画面 {len(bad_s)}）｜环 {len(rec.got)} 帧（键≠画面 {len(bad_a)}）"
          f"｜同键 {len(shared)}（不同画面 {len(bad_x)}）  {'✅' if ok else '❌'}")
    for lst, what in ((bad_s, "采样"), (bad_a, "环"), (bad_x, "同键")):
        if lst:
            print(f"      {what}前几个（t_us, 画面）：{lst[:5]}")
    return 0 if ok else 1


def main() -> int:
    if not VID.exists():
        synth()
    st = framesource.probe_stream(str(VID))
    print(f"{VID}：10 fps、60 帧、删掉第 {GAP[0]}–{GAP[1]} 帧（pts 2.4 s -> 3.2 s）"
          f"{'、硬解 ' + a.hwaccel if a.hwaccel else '、软解'}；容器报 名义 {st.nominal} / 平均 {float(st.avg or 0):.6f}"
          f"（只作参考帧率 {st.ref_fps:.4g}，不决定帧的身份）")
    s2 = SampleClock(2)
    bad = 0
    bad += case("跨缺口，从 0 起", 0.0, AuxClock(Clock(10), s2))
    bad += case("跨缺口，非零起点（1.0 s）", 1.0, AuxClock(Clock(10), s2))
    bad += case("跨缺口，辅助 2 fps（隔 5 帧）", 0.0, AuxClock(Clock(2), s2))
    bad += case("跨缺口，辅助 20/3 fps、采样嵌套", 0.0, AuxClock(Clock("20/3"), s2))
    bad += case("跨缺口，采样 20/7 fps、辅助每帧", 0.0, AuxClock(Clock(10), SampleClock("20/7")))
    bad += case("跨缺口，采样 20/7、辅助 6 fps", 0.0, AuxClock(Clock(6), SampleClock("20/7")))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
