"""A 路线的可行性 + 收益：`cv2` 到底能不能用上硬解，用上了快多少、像素一样不一样。

计划书 hw-decode 计划 §5.2：
「A：一行改动，先试能不能用上 D3D11；能用就量，不能用记下来。」这就是那个探针。

## 为什么要单独量，而不是直接改 run_ocr2

三件事必须分开看，混在一起就说不清收益是谁给的：

1. **能不能用上**。OpenCV 5.0.0 是预编译包，`CAP_PROP_HW_ACCELERATION` 设了不报错，
   但**设了不等于用上了**——回读这个属性才知道谈成了没有。所以这里回读并打出来。
2. **快多少**。按管线的真实读法计时：非采样帧 `grab()`、采样帧 `read()`。
   全量 `read()` 量出来的比值和管线没关系（管线 29/30 的帧只 grab）。
3. **像素一样不一样**。硬解走 D3D11VA / NVDEC 自己的 YUV→BGR，舍入与 swscale
   基本不会一致。计划书原来写「A 应当逐字节相同」——**那是写错的**，
   判据要拆成「采样帧号严格相同」+「像素差单独报」两层。
   像素差会不会改 det 的框，由 obs 层的多重集对账兜底，不在这里判。

## 墙钟怎么读

按 docs/dev-guide/verification.md「墙钟：按差幅分档读」：差幅 ≤ 5% 要换时间点复现，意外的大幅变慢先怀疑机器上有别的负载。
所以默认 `--repeat 2` 交替跑（不是先跑完一个模式再跑另一个），把慢漂摊平。

用法：
    python dev_tools/probe_hwdecode.py <video> --seconds 60
    python dev_tools/probe_hwdecode.py <video> --seconds 60 --modes none,any,d3d11 --pixels
"""
from __future__ import annotations

import argparse
import time

import cv2
import numpy as np

# OpenCV 的加速枚举。`ANY` = 让它自己挑；Windows 上通常落到 D3D11VA。
MODES: dict[str, tuple[int, int]] = {
    # 名字: (apiPreference, VIDEO_ACCELERATION_*)
    "none": (cv2.CAP_FFMPEG, cv2.VIDEO_ACCELERATION_NONE),
    "any": (cv2.CAP_FFMPEG, cv2.VIDEO_ACCELERATION_ANY),
    "d3d11": (cv2.CAP_FFMPEG, cv2.VIDEO_ACCELERATION_D3D11),
    "mfx": (cv2.CAP_FFMPEG, cv2.VIDEO_ACCELERATION_MFX),
    # 这个 build 也有 Media Foundation 后端，它自己就带硬解路径，一起试一把
    "msmf": (cv2.CAP_MSMF, cv2.VIDEO_ACCELERATION_ANY),
    "msmf-none": (cv2.CAP_MSMF, cv2.VIDEO_ACCELERATION_NONE),
}


def open_cap(video: str, mode: str) -> cv2.VideoCapture:
    api, accel = MODES[mode]
    return cv2.VideoCapture(video, api, [cv2.CAP_PROP_HW_ACCELERATION, accel])


def decode(video: str, mode: str, n_src: int, stride: int, keep: bool,
           start_idx: int = 0) -> tuple[float, int, list[int], float, list[np.ndarray]]:
    """按管线的读法走一遍：非采样帧 grab、采样帧 read。

    返回 (墙钟, 推进的源帧数, 每个采样帧的 pts(µs), 回读到的 accel 值, 采样帧)。

    **pts 是必收的，不是可选的**：不同后端"读到第几帧"不一定一致，
    不核对时刻就比像素，比的可能根本不是同一帧（那正是 max|Δ|=255 的样子）。

    `start_idx`（2026-09-10 加）：不给就量文件**前** n_src 帧，而同一个文件里
    "跑哪一段"的方差比"跑多长"大十倍（实测 input5 头 60 s 解码 1.24 s，
    14843 s 那个真窗口 3.34 s）。这里的两侧都是 `cv2`、都从计时器外面打开容器，
    **比值本身是对称的**；`--start` 修的是"表里的素材名和实际量的段落对不上"。
    """
    cap = open_cap(video, mode)
    if not cap.isOpened():
        raise SystemExit(f"[{mode}] 打不开 {video}")
    got_accel = cap.get(cv2.CAP_PROP_HW_ACCELERATION)
    if start_idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_idx)
    frames: list[np.ndarray] = []
    pts: list[int] = []
    t0 = time.perf_counter()
    idx = start_idx
    while idx < start_idx + n_src:
        if idx % stride:
            if not cap.grab():
                break
        else:
            ok, frame = cap.read()
            if not ok:
                break
            pts.append(int(round(cap.get(cv2.CAP_PROP_POS_MSEC) * 1000)))
            if keep:
                frames.append(frame)
        idx += 1
    wall = time.perf_counter() - t0
    cap.release()
    return wall, idx - start_idx, pts, got_accel, frames


def pixel_delta(a: list[np.ndarray], b: list[np.ndarray]) -> str:
    """两批帧逐帧比。**前提是调用方已核对过 pts**——否则比的不是同一帧。

    除 `max|Δ|` 外还报 `mean|Δ|`：色彩转换的舍入差是**满屏一两级**
    （占比高、均值低），而"比错了帧"是**局部差很多**（占比可能不高、均值高）。
    只看 max 分不开这两件事。
    """
    if not a or not b:
        return "（没有可比的帧）"
    n = min(len(a), len(b))
    worst = 0
    diff_frac: list[float] = []
    means: list[float] = []
    for x, y in zip(a[:n], b[:n]):
        if x.shape != y.shape:
            return f"⚠ 尺寸不同：{x.shape} vs {y.shape}"
        d = cv2.absdiff(x, y)
        worst = max(worst, int(d.max()))
        diff_frac.append(float((d > 0).mean()))
        means.append(float(d.mean()))
    med = sorted(diff_frac)[len(diff_frac) // 2]
    mean_med = sorted(means)[len(means) // 2]
    return (f"比了 {n} 帧：max|Δ| = {worst}，mean|Δ| = {mean_med:.2f}，"
            f"有差的像素占比中位 {med:.1%}"
            + ("（**逐字节相同**）" if worst == 0 else ""))


def integrity(video: str, mode: str, n: int = 400,
              start_idx: int = 0) -> list[int]:
    """纯顺序 `read()` 前 n 帧的 pts（µs）。

    为什么要有这一项：硬解可能**快而丢帧**——这类坏产物不会让任何下游报错。
    只走 grab/read 那条路看不出是丢帧还是 `grab()` 语义不同，纯顺序读一遍就分开了。

    ⚠ **判据 2026-09-10 改过。** 原来是"在自己这条 pts 序列里找跳号"，
    n 还只有 60。那样读出来的结论是「首帧没交付，之后每隔几帧丢一帧」——
    **是错的**：跟软解的序列逐个对照才看得出，丢帧只发生在**开头十来帧**，
    之后 3,590 帧完全同步（累计错位封顶在 3）。所以现在返回原始 pts，
    由 `delivery_report` 拿它和基准做集合差 + 累计错位曲线。
    """
    cap = open_cap(video, mode)
    if start_idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_idx)
    pts: list[int] = []
    for _ in range(n):
        ok, _f = cap.read()
        if not ok:
            break
        pts.append(int(round(cap.get(cv2.CAP_PROP_POS_MSEC) * 1000)))
    cap.release()
    return pts


def delivery_report(base: list[int], other: list[int]) -> str:
    """硬解相对软解**少交付了哪些帧**，以及累计错位在哪里封顶。

    三种结果读法不同：
    - 一个都不少 → 这条流上硬解的交付是完整的；
    - 少几帧但**累计错位很快封顶** → 启动/seek 之后的首帧交付问题，
      后面是同步的（实测 YouTube 的 av1：0–3 帧，全在开头十来帧内）；
    - 错位一路涨 → 真的在持续丢帧，那才是"不能用"。
    """
    if not base or not other:
        return "⚠ 一侧一帧都没读到"
    miss = [p for p in base if p not in set(other)]
    idx = {p: i for i, p in enumerate(base)}
    ks = [idx[p] - i for i, p in enumerate(other) if p in idx]
    if not miss:
        return f"{len(other)} 帧，**与基准逐帧相同**"
    k_end = ks[-1] if ks else -1
    where = [i for i, p in enumerate(base) if p in set(miss)]
    tail_free = (not where or where[-1] < 20)
    kind = ("开头的首帧交付问题（丢的都在前 20 帧内，之后同步）" if tail_free
            else "**持续丢帧**")
    return (f"{len(other)} 帧，⚠ **少交付 {len(miss)} 帧**（{kind}）；"
            f"累计错位末尾 k={k_end}；丢的 pts(ms)="
            f"{[round(p/1000, 3) for p in miss[:6]]}")


def pts_report(base_name: str, base: list[int], name: str, other: list[int]) -> str:
    """采样时刻对不对得上——**这一层要求严格相等**，是比像素的前提。"""
    if base == other:
        return f"{len(other)} 个采样时刻与 {base_name} **完全相同**"
    n = min(len(base), len(other))
    first = next((i for i in range(n) if base[i] != other[i]), n)
    d = [(other[i] - base[i]) / 1e6 for i in range(n)]
    return (f"⚠ 采样时刻对不上：{len(other)} vs {len(base)} 个，"
            f"第一处不同在第 {first} 个（差 "
            f"{d[first] if first < n else float('nan'):+.4f}s），"
            f"最大差 {max(d, key=abs):+.4f}s"
            if n else "⚠ 一个采样时刻都没有")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0,
                    help="从第几秒开始量。**报数时要和 data/samples.json 的窗口对上**——"
                         "不给就是量文件头，而同一个文件里不同段落的解码成本差 2.5×")
    ap.add_argument("--seconds", type=float, default=60.0, help="解码多少秒的素材")
    ap.add_argument("--fps", type=float, default=2.0, help="采样率，决定 grab/read 的比例")
    ap.add_argument("--modes", default="none,any,d3d11,msmf",
                    help=f"逗号分隔，可选：{','.join(MODES)}")
    ap.add_argument("--repeat", type=int, default=2,
                    help="每个模式跑几遍，**交替跑**（不是跑完一个再跑下一个），"
                         "把机器的慢漂摊平。差幅 ≤5%% 要换时间点复现")
    ap.add_argument("--integrity-frames", type=int, default=400,
                    help="帧完整性那一层读多少帧。**默认 400，别调到 100 以下**："
                         "丢帧集中在开头十来帧，窗口太短会把"
                         "『开头丢 3 帧、之后同步』误读成『一直在丢』（2026-09-10 踩过）")
    ap.add_argument("--pixels", action="store_true",
                    help="顺带比像素：把采样帧留在内存里和 `none` 逐帧比。"
                         "**吃内存**（1080p 每帧 6 MB），只在短片段上开")
    a = ap.parse_args()

    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    bad = [m for m in modes if m not in MODES]
    if bad:
        ap.error(f"不认识的模式：{bad}；可选 {list(MODES)}")

    probe = cv2.VideoCapture(a.video)
    if not probe.isOpened():
        raise SystemExit(f"打不开 {a.video}")
    src_fps = probe.get(cv2.CAP_PROP_FPS) or 60.0
    probe.release()
    stride = max(1, round(src_fps / a.fps))
    n_src = int(round(a.seconds * src_fps))
    # 起点对齐到 stride 的整数倍——和 `run_ocr2` 一样
    start_idx = int(round(a.start * src_fps / stride)) * stride
    print(f"{a.video}\n  src_fps={src_fps:.3f}  stride={stride}  "
          f"**量的区间 {start_idx / src_fps:.1f}–{start_idx / src_fps + a.seconds:.1f} s**"
          f"（帧 {start_idx} 起，解码 {n_src} 源帧，其中采样 {n_src // stride} 帧）\n")

    walls: dict[str, list[float]] = {m: [] for m in modes}
    accel: dict[str, float] = {}
    sampled: dict[str, int] = {}
    pts_of: dict[str, list[int]] = {}
    kept: dict[str, list[np.ndarray]] = {}
    for r in range(a.repeat):
        for m in modes:
            keep = a.pixels and r == 0
            wall, adv, pts, got, frames = decode(a.video, m, n_src, stride, keep,
                                                 start_idx)
            walls[m].append(wall)
            accel[m] = got
            sampled[m] = min(sampled.get(m, len(pts)), len(pts))
            pts_of.setdefault(m, pts)
            if keep:
                kept[m] = frames
            print(f"  [第 {r+1} 遍] {m:<10} {wall:6.2f} s  "
                  f"（推进 {adv} 帧、采样 {len(pts)} 帧）", flush=True)
    print()

    # **解码不出帧的模式不许参与比较。** 差点在 av1 上栽一次：MSMF 后端
    # 开得起来、`read()` 直接失败（"codec not found"），推进 0 帧却只花 1.3 s，
    # 表里就成了「比软解快 23.59×」。这个项目栽过三次的都是这个形状——
    # 口径先于实现：**没解出同样多的帧，那个墙钟不是同一件事的墙钟**。
    want_sampled = sampled[modes[0]]
    ok_modes = [m for m in modes if sampled[m] == want_sampled and want_sampled > 0]
    dead = [m for m in modes if m not in ok_modes]

    base = min(walls[modes[0]])
    print(f"{'模式':<12}{'最快':>8}{'中位':>8}{'相对 ' + modes[0]:>14}   回读到的 accel")
    for m in modes:
        w = sorted(walls[m])
        best, med = w[0], w[len(w) // 2]
        # **回读的值才算数**：设了不等于用上了。0=NONE 1=ANY 2=D3D11 3=VAAPI 4=MFX
        want = MODES[m][1]
        note = f"{accel[m]:.0f}"
        if want != cv2.VIDEO_ACCELERATION_NONE and accel[m] == cv2.VIDEO_ACCELERATION_NONE:
            note += "  ⚠ 要了硬解但回读是 NONE = **没用上**"
        if m in dead:
            print(f"{m:<12}{best:8.2f}{med:8.2f}{'——':>13}   {note}"
                  f"  ⚠ 只采到 {sampled[m]}/{want_sampled} 帧，**不作数**")
        else:
            print(f"{m:<12}{best:8.2f}{med:8.2f}{base / best:13.2f}×   {note}")
    if dead:
        print(f"\n⚠ 这些模式解不出帧，倍数一栏留空：{'、'.join(dead)}"
              f"\n  （它们的墙钟看着最快，因为根本没解码——别把它读成收益）")

    # 第零层：**帧完整性**。快而丢帧是最坏的一种结果——下游一行都不会报错。
    # **和基准的序列逐个对照**，不是在自己序列里找跳号（后者会把
    # "开头丢 3 帧" 误读成 "每隔几帧丢一帧"，2026-09-10 踩过）。
    print(f"\n帧完整性（纯顺序 read {a.integrity_frames} 帧，"
          f"和 {modes[0]} 的 pts 序列逐个对照）：")
    base_pts = integrity(a.video, modes[0], a.integrity_frames, start_idx)
    print(f"  {modes[0]:<10} {len(base_pts)} 帧（基准）")
    for m in modes[1:]:
        if m not in ok_modes:
            continue
        print(f"  {m:<10} "
              + delivery_report(base_pts,
                                integrity(a.video, m, a.integrity_frames, start_idx)))

    # 第一层：采样时刻。**它是比像素的前提**，所以永远打印，不受 --pixels 控制。
    print("\n采样时刻对账（对 " + modes[0] + "，这一层要求严格相等）：")
    for m in modes[1:]:
        if m in ok_modes:
            print(f"  {m:<10} {pts_report(modes[0], pts_of[modes[0]], m, pts_of[m])}")

    if a.pixels and modes[0] in kept:
        print("\n像素对账（对 " + modes[0] + "）：")
        for m in modes[1:]:
            if m in kept and m in ok_modes:
                same_grid = pts_of[m] == pts_of[modes[0]]
                note = "" if same_grid else "  ⚠ 采样时刻都对不上，这个像素差没有意义"
                print(f"  {m:<10} {pixel_delta(kept[modes[0]], kept[m])}{note}")
    print("\n墙钟按差幅分档读：>5% 大概率真提升（最好换时间点复现），"
          "\n≤5% 换时间点再跑一遍，意外的大幅变慢先怀疑机器上有别的负载。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
