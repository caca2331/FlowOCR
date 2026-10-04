"""B 路线：ffmpeg 子进程只把采样帧送进管道，量它多快、丢不丢帧、像素差多少。

计划书 hw-decode 计划 §2 的 B。
A 路线（`cv2` 硬解）已否（hw-decode-results 报告 §1），
av1 的收益全压在这里。

## 判据顺序和 `probe_hwdecode.py` 一样，先完整性再墙钟

**快而丢帧是最坏的结果**——A 就是这么否掉的（av1 快 10.4×，但纯顺序 read 就丢帧，
下游一行不会报错）。所以这里也先对 pts、再看墙钟、最后才比像素。

## 两侧的账要对称（2026-09-10 改，**改之前三处不对称**）

复审时发现这个探针把 ffmpeg 一侧的固定开销算进了比值，cv2 一侧的没算：

1. **启动开销**。`cv2_baseline` 的计时器起在 `VideoCapture(...)` **之后**
   （实测打开 `input5.mp4` 要 0.336 s，白送）；`run_pipe` 起在 `Popen` 处，
   ffmpeg 的进程启动 + 输入探测 + seek（实测 0.22–0.45 s）全在账内。
   现在**两侧的计时器都起在打开解码器之前**，并单独报「首帧」这一列；
   比值默认按**稳态**（扣掉首帧）算，因为真实管线里这笔钱一次运行只付一次，
   整片上摊到 0——而表是拿来给整片做决定的。
2. **NV12→BGR 的 `cvtColor` 原来只在 `--pixels` 的第 0 遍做**，而比值取最快那一遍，
   于是这笔真实管线每帧都付的钱（120 帧 0.12 s）从没进过账。现在**总是转换**，
   只有留不留帧才看 `keep`。
3. **`--start` 原来没有**，于是量的一律是文件**前 60 秒**，而报告里的行名写的却是
   `q-yuka-f5-dialogue` 这种**窗口** id。实测同一个文件里
   "跑哪一段"的方差比"跑多长"大十倍（input5 头 60 s 解码 1.24 s，
   14843 s 那个真窗口 3.34 s，**2.5×**）。现在必须给 `--start`，
   报告里也把量的区间打出来。

## 命令为什么长成这样

命令**不在这里拼**——走 `framesource.build_ffmpeg_cmd`，和生产路径同一份。
原来这里自带一份 `build_cmd`，而那一份**没有 `-copyts`**：给探针加 `--start`
会原样复活「窗口时间轴整体平移几个小时、没有任何一层报错」那个坑。

规则（三条都在 `framesource` 的文件头）：`select` 而不是 `fps`（`fps` 是补帧滤镜，
pts 缺口处会复制帧填上）、`-fps_mode passthrough`、cuda 那条带
`-hwaccel_output_format cuda` + `hwdownload`（丢弃发生在下载之前）。

时间戳从 `showinfo` 的 stderr 逐帧读（`pts_time`），和管道上的帧按序配对——
管道里是裸像素，**没有时间戳**，不读 stderr 就只能靠帧号推，那又回到平均帧率那个坑。

用法：
    python dev_tools/probe_ffmpeg_decode.py <video> --start 14843 --seconds 60
    python dev_tools/probe_ffmpeg_decode.py <video> --start 0 --seconds 60 --pixels
"""
from __future__ import annotations

import argparse
import subprocess
import threading
import time

import cv2
import numpy as np

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.extract import framesource


def run_pipe(video: str, mode: str, stride: int, start_sec: float, pix: str,
             n_out: int, W: int, H: int,
             keep: bool) -> tuple[float, float, list[int], list[np.ndarray], str]:
    """跑一遍管道，返回 (总墙钟, 首帧用时, 每帧 pts(µs), 帧, ffmpeg 的最后几行 stderr)。

    **计时器起在 `Popen` 之前**，ffmpeg 的启动全算进"首帧"那一项里。
    """
    nbytes = W * H * 3 if pix == "bgr24" else W * H * 3 // 2
    cmd = framesource.build_ffmpeg_cmd(
        video, grid=framesource.TimeGrid.every(stride), start_sec=start_sec, n_out=n_out, pix=pix,
        hwaccel="cuda" if mode == "cuda" else None)
    pts: list[int] = []
    tail: list[str] = []

    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=nbytes * 2)

    def drain() -> None:
        for raw in proc.stderr:                      # type: ignore[union-attr]
            line = raw.decode("utf-8", "replace")
            t = framesource.parse_pts_line(line)
            if t is not None:
                pts.append(int(round(t * 1e6)))
            else:
                tail.append(line.rstrip())
                del tail[:-6]

    th = threading.Thread(target=drain, daemon=True)
    th.start()

    frames: list[np.ndarray] = []
    n = 0
    t_first = 0.0
    while True:
        buf = proc.stdout.read(nbytes)               # type: ignore[union-attr]
        if len(buf) < nbytes:
            break
        if not n:
            t_first = time.perf_counter() - t0
        n += 1
        # **转换总是做**：真实管线（`framesource.FfmpegSource`）每帧都付这笔钱，
        # 只在 `--pixels` 时做的话，比值里就永远缺这一项。
        a = np.frombuffer(buf, np.uint8)
        frame = (a.reshape(H, W, 3).copy() if pix == "bgr24"
                 else cv2.cvtColor(a.reshape(H * 3 // 2, W), cv2.COLOR_YUV2BGR_NV12))
        if keep:
            frames.append(frame)
    proc.stdout.close()                              # type: ignore[union-attr]
    proc.wait()
    wall = time.perf_counter() - t0
    th.join(timeout=5)
    # **收到的帧数和 showinfo 报的 pts 数必须一样**，否则后面按序配对就是错的
    if len(pts) != n:
        tail.append(f"⚠ 收到 {n} 帧，但 showinfo 报了 {len(pts)} 个 pts")
    return wall, t_first, pts[:n], frames, "\n    ".join(tail[-4:])


def cv2_baseline(video: str, stride: int, start_idx: int, n_src: int,
                 keep: bool) -> tuple[float, float, list[int], list[np.ndarray]]:
    """基准：现在管线用的读法（29 帧 grab + 1 帧 read，软解）。

    **计时器起在 `VideoCapture(...)` 之前**（打开容器 + seek 实测 0.3–0.5 s，
    和 ffmpeg 的进程启动是同一类开销，只算一边就是把账做偏）。
    """
    pts: list[int] = []
    frames: list[np.ndarray] = []
    t0 = time.perf_counter()
    cap = cv2.VideoCapture(video, cv2.CAP_FFMPEG,
                           [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE])
    if start_idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_idx)
    t_first = 0.0
    for i in range(start_idx, start_idx + n_src):
        if i % stride:
            if not cap.grab():
                break
        else:
            ok, f = cap.read()
            if not ok:
                break
            if not pts:
                t_first = time.perf_counter() - t0
            pts.append(int(round(cap.get(cv2.CAP_PROP_POS_MSEC) * 1000)))
            if keep:
                frames.append(f)
    wall = time.perf_counter() - t0
    cap.release()
    return wall, t_first, pts, frames


def cmp_pts(base: list[int], other: list[int]) -> str:
    if base == other:
        return f"{len(other)} 个 pts **与基准完全相同**"
    n = min(len(base), len(other))
    if not n:
        return f"⚠ 只拿到 {len(other)} 个 pts（基准 {len(base)}）"
    i = next((k for k in range(n) if base[k] != other[k]), n)
    worst = max(((other[k] - base[k]) / 1e6 for k in range(n)), key=abs)
    return (f"⚠ 对不上：{len(other)} vs {len(base)} 个，"
            f"第一处不同在第 {i} 个，最大差 {worst:+.4f}s")


def cmp_pixels(a: list[np.ndarray], b: list[np.ndarray]) -> str:
    if not a or not b:
        return "（没留帧）"
    n = min(len(a), len(b))
    worst, fr, mn = 0, [], []
    for x, y in zip(a[:n], b[:n]):
        if x.shape != y.shape:
            return f"⚠ 尺寸不同 {x.shape} vs {y.shape}"
        d = cv2.absdiff(x, y)
        worst = max(worst, int(d.max()))
        fr.append(float((d > 0).mean()))
        mn.append(float(d.mean()))
    return (f"比了 {n} 帧：max|Δ| = {worst}，mean|Δ| = {sorted(mn)[n//2]:.2f}，"
            f"有差的像素占比中位 {sorted(fr)[n//2]:.1%}"
            + ("（**逐字节相同**）" if worst == 0 else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0,
                    help="从第几秒开始量。**必须显式给**，因为同一个文件里不同段落的"
                         "解码成本能差 2.5×，量文件头得到的数不代表那个窗口")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--modes", default="sw,cuda")
    ap.add_argument("--pix", default="bgr24", choices=["bgr24", "nv12"],
                    help="管道上的像素格式。nv12 省一半带宽，BGR 转换挪到 Python 侧")
    ap.add_argument("--repeat", type=int, default=2, help="交替跑几遍，摊平机器的慢漂")
    ap.add_argument("--pixels", action="store_true", help="留帧比像素（吃内存）")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit(f"打不开 {a.video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    stride = max(1, round(src_fps / a.fps))
    n_src = int(round(a.seconds * src_fps))
    # 窗口起点对齐到 stride 的整数倍——和 `run_ocr2` 一样，两条路才落在同一批帧上
    start_idx = int(round(a.start * src_fps / stride)) * stride
    start_sec = start_idx / src_fps
    n_out = max(1, n_src // stride)
    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    print(f"{a.video}\n  {W}x{H} src_fps={src_fps:.3f} stride={stride} "
          f"pix={a.pix}  **量的区间 {start_sec:.1f}–{start_sec + a.seconds:.1f} s**"
          f"（帧 {start_idx} 起，解码 {n_src} 源帧、送出 {n_out} 帧）\n")

    walls: dict[str, list[float]] = {"cv2-sw": []}
    firsts: dict[str, list[float]] = {"cv2-sw": []}
    pts_of: dict[str, list[int]] = {}
    kept: dict[str, list[np.ndarray]] = {}
    for m in modes:
        walls[f"ffmpeg-{m}"] = []
        firsts[f"ffmpeg-{m}"] = []

    for r in range(a.repeat):
        keep = a.pixels and r == 0
        w, f1, p, f = cv2_baseline(a.video, stride, start_idx, n_src, keep)
        walls["cv2-sw"].append(w)
        firsts["cv2-sw"].append(f1)
        pts_of.setdefault("cv2-sw", p)
        if keep:
            kept["cv2-sw"] = f
        print(f"  [第 {r+1} 遍] {'cv2-sw':<12} {w:6.2f} s（首帧 {f1:4.2f}，{len(p)} 帧）",
              flush=True)
        for m in modes:
            w, f1, p, f, tail = run_pipe(a.video, m, stride, start_sec, a.pix,
                                         n_out, W, H, keep)
            walls[f"ffmpeg-{m}"].append(w)
            firsts[f"ffmpeg-{m}"].append(f1)
            pts_of.setdefault(f"ffmpeg-{m}", p)
            if keep:
                kept[f"ffmpeg-{m}"] = f
            print(f"  [第 {r+1} 遍] {'ffmpeg-' + m:<12} {w:6.2f} s"
                  f"（首帧 {f1:4.2f}，{len(p)} 帧）"
                  + (f"\n    {tail}" if tail else ""), flush=True)
    print()

    want = len(pts_of["cv2-sw"])

    def steady(name: str) -> float:
        """稳态用时 = 最快那一遍的 (总 − 首帧)。首帧那笔钱一次运行只付一次。"""
        k = min(range(len(walls[name])), key=lambda i: walls[name][i])
        return walls[name][k] - firsts[name][k]

    base_total, base_steady = min(walls["cv2-sw"]), steady("cv2-sw")
    print(f"{'路线':<14}{'最快':>8}{'首帧':>8}{'稳态':>8}"
          f"{'相对(稳态)':>13}{'相对(总)':>11}")
    for name, ws in walls.items():
        ok = len(pts_of[name]) == want and want > 0
        s = steady(name)
        if ok and s > 0:
            r_steady, r_total = f"{base_steady / s:12.2f}×", f"{base_total / min(ws):10.2f}×"
        else:
            r_steady, r_total = f"{'——':>12}", f"{'——':>10}"
        note = "" if ok else f"  ⚠ 只拿到 {len(pts_of[name])}/{want} 帧，**不作数**"
        print(f"{name:<14}{min(ws):8.2f}{min(firsts[name]):8.2f}{s:8.2f}"
              f"{r_steady}{r_total}{note}")
    print("  「相对(稳态)」是扣掉首帧（打开容器 / 拉起子进程 / seek）之后的比值——"
          "整片上那笔钱摊到 0，\n  所以拿它做决定；「相对(总)」是 "
          f"{a.seconds:.0f} 秒这一档实际感受到的。")

    print("\n采样时刻（对 cv2-sw，这一层要求严格相等）：")
    for name in walls:
        if name != "cv2-sw":
            print(f"  {name:<14}{cmp_pts(pts_of['cv2-sw'], pts_of[name])}")

    if a.pixels and "cv2-sw" in kept:
        print("\n像素（对 cv2-sw）：")
        for name in walls:
            if name != "cv2-sw" and name in kept:
                same = pts_of[name] == pts_of["cv2-sw"]
                print(f"  {name:<14}{cmp_pixels(kept['cv2-sw'], kept[name])}"
                      + ("" if same else "  ⚠ 采样时刻都对不上，这个像素差没意义"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
