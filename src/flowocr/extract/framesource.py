"""采样帧从哪来：ffmpeg 子进程只送采样帧（`FfmpegSource`），可选在外面叠预取（`PrefetchSource`）。

**判据和纯逻辑放在这里，是为了让 `tests/test_guards.py` 够得着**——
和 `flowocr.extract.ocr_complete`、`flowocr.extract.ptsclock` 同一个理由（audit-4 C8）。
`cv2` 只在类里按需 import，所以这个模块在没装 cv2 的解释器上也能被守卫导入。

## 帧身份 = 这一帧自己的 pts（frame-pts 计划，2026-11-23）

`t_us = round(pts_time × 1e6) − S`（`framegrid.frame_key`，唯一换算处；S 是容器的 `format.start_time`，经 `framegrid.Axis` 减）：
采样帧、辅助流帧环的键、obs 行、回抠证据里的时刻都是它。主路、辅路同一帧的 `pts_time` 是同一个字符串，所以是同一个数。取哪些帧按**时间桶**（`flowocr.extract.framegrid`）：
每个非空桶取第一帧，桶号在解析 showinfo 时就由整数 pts 算好（Python 逐位复刻 ffmpeg 的双精度），和 `t_us`、画面一起往下传，
接缝去重、空桶计数、拼缝核对都只用它，不从 `t_us` 反推。键必须严格递增（同一 pts 出现两次的坏文件当场报错）。

## FfmpegSource 的命令为什么长成这样

1. **`select` 而不是 `fps`**：`fps` 是补帧 / 丢帧滤镜（转恒定帧率），pts 缺口处会**复制帧填上**，f3/f4 上凭空造观测。`select` 只挑不造。
2. **`-fps_mode passthrough`**：否则 muxer 那层还会再补一次帧。
3. **nv12 而不是 bgr24**：管道带宽减半；NV12→BGR 用 OpenCV 自己那套转换。
4. **pts 从 `showinfo` 的 stderr 逐帧读**：管道里是裸像素、没有时间戳。
5. **读到 EOF**：窗口尾靠输入端 `-t` 加 `select` 的范围收口，不按预计帧数（VFR 上预计不了）。
6. **默认走 `-hwaccel cuda`**（2026-09-20，owner 定；理由是 **CPU 不是墙钟**，docs/architecture/defaults.md §1.8）。
   * NVDEC 在**有前摇的 av1**（首包 pts 为负）上少交付头 1~3 帧：`-ignore_editlist 1` + 整数刻度 `setpts` 平移回原时间轴（`av1_preroll_ticks`）；
   * 辅助流跟着硬解一起走：辅路在显存里 `scale_cuda` 缩完再 `hwdownload`；
   * 开跑前试解（`hwaccel_blocker`）硬解和软解的 pts 列表逐个比，不一致就整段改软解；中途按时间桶核对（`FfmpegSource` 的 ① ②）。
"""
from __future__ import annotations

import json
import math
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from queue import Queue
from typing import Any, NamedTuple

from flowocr.extract import timeline as _tl   # --timeline（decode-buffer）；只有标准库
from flowocr.extract.framegrid import AuxClock, Axis, SampleClock, Window, rate_of

QUEUE_SENTINEL = object()
"""预取队列的结束标记。**不能用 None**——None 是合法的"这一帧没解出来"。"""

BLOCK_PAD_SEC = 0.1
"""解码分片的块按整数 pts `[lo, hi)` 收口时，输入端 `-t` 在块终点之后多读这么久：块终点是下一个关键帧，
开放 GOP 的前导帧（pts < 关键帧、解码序在它后面）也要由这一块解出来；多读的帧被 `select` 的 pts 范围挡掉。
（`-t` 截的是 pts，解码器按 pts 顺序吐帧，所以 pts 在块终点之前的帧都在截点之前出来。）"""

TB_CHECK_FRAMES = 3
"""开流时核对前几帧 `pts × 时间基` 与 showinfo 的 `pts_time` 到 µs 一致（滤镜链路的时间基 showinfo 不打印，方案取流的时间基）。"""


class Frame(NamedTuple):
    """取帧层交出的一帧：身份 `key`（= `t_us`）、相对秒 `t`（= `key / 1e6`）、BGR 画面、采样桶号、整数 pts（绝对，流的时间基）。
    桶号和整数 pts 随帧走过分片的块、解码进程、合流（接缝去重、硬解核对用），下游只看前三个。"""
    key: int
    t: float
    image: Any
    cell: int
    pts: int


SHOWINFO_RE = re.compile(r"\[showinfo@(main|aux) @ [^]]*\] n: *(\d+) +pts: *(-?\d+) +pts_time:(-?[0-9.]+(?:e[-+]?\d+)?)")
r"""`showinfo@main` / `showinfo@aux` 那一行：哪一路 + 它自己的帧计数 `n` + 整数 `pts` + `pts_time`。
滤镜图里两路各挂一个**命名实例**，stderr 的前缀就是实例名，所以同一个进程里两路分得开（2026-09-20）。

⚠ **不锚行首**（2026-09-20 晚踩的）：ffmpeg 的进度行 `frame=… \r` **用回车结尾、不换行**，紧跟着的 showinfo 行就粘在它后面。
命令里加了 `-nostats`（从源头关掉进度行）、drain 按 `\r` 也切行，**再加上 `n` 逐帧核对**：丢行当场报错而不是静默错位。"""


def parse_showinfo(line: str) -> tuple[str, int, int, float] | None:
    """一行 stderr -> (`main` | `aux`, 帧计数 n, 整数 pts, pts 秒)；不是那种行就 None。**纯函数，守卫直接测它。**"""
    m = SHOWINFO_RE.search(line)
    return (m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4))) if m else None


def check_tb(pts: int, t_sec: float, axis: Axis) -> str | None:
    """`pts × 时间基` 与 showinfo 打印的 `pts_time`（都是绝对的）到 µs 一致没有（不一致返回原因）。
    滤镜链路的时间基 showinfo 不打印，桶号按流的时间基算——链路里换了时间基的话桶号就算错了，必须当场停。"""
    t = axis.pts_abs(pts)
    if abs(t * 1e6 - t_sec * 1e6) > 1.0:
        return (f"pts {pts} × 时间基 {axis.tb[0]}/{axis.tb[1]} = {t:.6f} s，showinfo 说 {t_sec:.6f} s"
                f"（滤镜链路的时间基和流的不同，桶号会算错）")
    return None


# ---------- 流的元数据 ----------

@dataclass(frozen=True)
class StreamInfo:
    """一路视频流的元数据（`probe_stream`）：时间基、名义 / 平均帧率、参考帧率、时长、画面尺寸、编码。"""
    tb: tuple[int, int]
    nominal: Fraction | None
    avg: Fraction | None
    duration: float | None
    width: int
    height: int
    codec: str = ""
    origin_us: int = 0
    """容器的 `format.start_time`（整数 µs）：`t_us`、桶、窗口都在减过它的时间上（`framegrid.Axis`）。素材库里几乎全是 0。"""

    @property
    def axis(self) -> Axis:
        return Axis(self.tb, self.origin_us)

    @property
    def ref_fps(self) -> float:
        """参考帧率（`ref_rate`）：下游拿它算"一个源帧多长"的容差，不决定任何帧的身份。"""
        return ref_rate(self.nominal, self.avg)


def ref_rate(nominal, avg) -> float:
    """**参考帧率**：名义帧率（`r_frame_rate`）在 `[平均 × 0.999, 平均 × 2]` 里用名义，否则用平均帧率。**纯函数，守卫直接测它。**

    只决定"一个源帧多长"的容差（`_meta.src_fps`，下游 `native_frame_us` 等）和硬解核对②的门，**不决定任何帧的身份**。
    丢帧只会让平均帧率**低于**名义帧率（f3 / f4 平均 59.978 / 59.987，照旧取 60）；名义帧率比平均高出一倍以上就不信它
    （片段 4：名义 47.952、平均 23.907，取平均）。判错的后果只是容差差一点，不会撞号、丢帧或判未完成。"""
    nom = float(nominal) if nominal else 0.0
    a = float(avg) if avg else 0.0
    if nom and a * 0.999 <= nom <= a * 2:
        return nom
    return a or nom


def probe_stream(video: str) -> StreamInfo:
    """ffprobe 读第一路视频流的元数据。时长先取 `stream=duration`、没有再取 `format=duration`（webm / mkv 的流常没有）。"""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=codec_name,width,height,time_base,r_frame_rate,avg_frame_rate,duration:format=duration,start_time",
                              "-of", "json", video], capture_output=True, text=True, timeout=60)
        js = json.loads(out.stdout or "{}")
        st = (js.get("streams") or [None])[0]
    except (OSError, subprocess.SubprocessError, ValueError):
        st = None
    if not st:
        raise SystemExit(f"无法打开 {video}（ffprobe 读不出视频流）")

    def dur(x) -> float | None:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None
    tb = st.get("time_base") or ""
    if "/" not in tb:
        raise SystemExit(f"{video} 的视频流没有时间基（ffprobe 给的是 {tb!r}）")
    tbt = tuple(int(x) for x in tb.split("/"))
    origin_us = round(_num((js.get("format") or {}).get("start_time")) * 1_000_000)
    duration = dur(st.get("duration"))
    if duration is None:
        duration = dur((js.get("format") or {}).get("duration"))
        if duration is not None and origin_us:
            duration = _tail_end_sec(video, tbt, origin_us, duration) or duration
    return StreamInfo(tb=tbt, nominal=rate_of(st.get("r_frame_rate")), avg=rate_of(st.get("avg_frame_rate")), duration=duration,
                      width=int(st.get("width") or 0), height=int(st.get("height") or 0), codec=st.get("codec_name") or "",
                      origin_us=origin_us)


def _tail_end_sec(video: str, tb: tuple[int, int], origin_us: int, fmt_dur: float) -> float | None:
    """容器起始时间 S ≠ 0、流又没有自己的时长时，`format=duration` 的口径随容器变：mp4 报长度，mkv / webm 报结束的**绝对**时刻
    （S = 2 s、长 4 s 的 mkv 报 6 s，实测）——拿它当长度，`--end 0` 的要求就多出 S、跑完也判未完成。这里读文件尾的包，
    取"最后一个包的 pts + 它的时长 − S"（相对秒，`t_us` 那把尺）。结尾的绝对时刻两种口径下分别是 `fmt_dur` 和 `fmt_dur + S`，
    所以从两者较早的那个之前 10 s 读起（S 为负时也盖得住）。
    读不出来返回 None（调用方退回 `format=duration`）。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts,duration",
                            "-read_intervals", probe_interval(min(fmt_dur, fmt_dur + origin_us / 1e6) - 10.0, None), "-of", "csv=p=0", video],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    ends = []
    for line in r.stdout.split():
        f = line.strip(",").split(",")
        if f and f[0].lstrip("-").isdigit():
            d = int(f[1]) if len(f) > 1 and f[1].isdigit() else 0
            ends.append(int(f[0]) + d)
    if r.returncode != 0 or not ends:
        return None
    end = max(ends) * tb[0] / tb[1] - origin_us / 1e6
    return end if end > 0 else None


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


# ---------- 源文件里有没有包（硬解核对 / 分片块尾 / 拼缝） ----------

def probe_interval(t0: float, t1: float | None) -> str:
    """`ffprobe -read_intervals` 的区间串（**绝对**秒：ffprobe 不按 `start_time` 平移）。**起点 ≤ 0 时写成"从头读到 t1"**：首包 pts 为负的文件（gi-s2 −5.4 s）上
    `-read_intervals 0%1` **一个包都读不出来**（实测；`0.5%1.5` 却会退回前面的关键帧、照常读到）。`t1 = None` 读到文件尾。纯函数。"""
    end = "" if t1 is None else f"{t1:.6f}"
    return f"{t0:.6f}%{end}" if t0 > 0 else f"%{end}"


class PacketIndex:
    """源文件的包 pts（按 `SEG_SEC` 一段批量取、缓存）：给硬解核对 / 分片块尾 / 拼缝判"没交付的那段时间源里有没有帧"。
    问的区间、分段都是**相对**秒（`t_us` 那把尺），交出的包 pts 是绝对的；读 ffprobe 时经 `Axis.abs_sec` 换回绝对秒。
    源里有 = 解码器吞了（NVDEC 在前摇里少交付就是这个形状）；源里没有 = 合法缺口（f3/f4、VFR 的稀处）。
    **探不出来返回 None**（ffprobe 失败 / 超时 / 一个包都没读到），由调用方按自己的代价定怎么算（硬解：当"源里有"、退回软解；
    分片的软解路：放行 + 记一笔）——⚠ 不能读成"源里没有 = 合法缺口"。"""

    SEG_SEC = 60.0

    def __init__(self, video: str, axis: Axis) -> None:
        self.video, self.axis = video, axis
        self._seg: dict[int, list[int] | None] = {}
        self._lock = threading.Lock()
        self.probes = 0

    def _read(self, t0: float, t1: float | None) -> list[int] | None:
        """相对秒 `[t0, t1]` 附近的包 pts（ffprobe 按绝对秒读）。"""
        self.probes += 1
        ax = self.axis
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts",
                                "-read_intervals", probe_interval(ax.abs_sec(t0), None if t1 is None else ax.abs_sec(t1)),
                                "-of", "csv=p=0", self.video],
                               capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            # 报错码非零时 stdout 是空的——**不能**读成"源里没有"（第一版就这么写反过，守卫钉住）
            return None
        got = []
        for tok in r.stdout.split():
            tok = tok.strip(",")
            if tok.lstrip("-").isdigit():
                got.append(int(tok))
        return got

    def _segment(self, s: int) -> list[int] | None:
        with self._lock:
            if s in self._seg:
                return self._seg[s]
        lo, hi = s * self.SEG_SEC, (s + 1) * self.SEG_SEC
        raw = self._read(lo, hi + 1.0)              # 多读 1 s：解码序在段尾之后、pts 在段里的包（B 帧）
        got = None if raw is None else sorted({p for p in raw if lo <= self.axis.sec(p) < hi})
        with self._lock:
            self._seg[s] = got
        return got

    def between(self, lo_sec: float, hi_sec: float | None) -> list[int] | None:
        """相对时刻在 `[lo_sec, hi_sec)` 里的包 pts（绝对，升序）；`hi_sec = None` 读到文件尾（不缓存）。探不出来 None。"""
        if hi_sec is None:
            raw = self._read(lo_sec, None)
            if not raw:
                return None
            return sorted({p for p in raw if self.axis.sec(p) >= lo_sec})
        if hi_sec <= lo_sec:
            return []
        out: list[int] = []
        for s in range(math.floor(lo_sec / self.SEG_SEC), math.floor(hi_sec / self.SEG_SEC) + 1):
            seg = self._segment(s)
            if seg is None:
                return None
            out.extend(p for p in seg if lo_sec <= self.axis.sec(p) < hi_sec)
        if not out and not any(self._seg.get(s) for s in range(math.floor(lo_sec / self.SEG_SEC),
                                                                 math.floor(hi_sec / self.SEG_SEC) + 1)):
            # 盖住这段的几个整段一个包都没读到 = 探不出来（或者整段都在文件外），不是"源里没有"
            return None
        return out


def eaten(pkts: list[int] | None, clock: SampleClock, axis: Axis, *, after_pts: int | None,
          before_pts: int | None, after_cell: int, window: Window | None = None) -> list[int] | None:
    """`pkts`（源里的包 pts）里**该交却没交**的：pts 在 `(after_pts, before_pts)` 里、桶号大于 `after_cell`（上一个交出的帧所在桶；
    窗口起点之前是 `a − 1`）、在窗口里。这些包所在的桶要么整个被跳过（①），要么比交出的那一帧更早（②）——都该由它们交。
    `None` 端不设界。探不出来（`pkts is None`）原样返回 None。**纯函数，守卫直接测。**"""
    if pkts is None:
        return None
    out = []
    for p in pkts:
        if (after_pts is not None and p <= after_pts) or (before_pts is not None and p >= before_pts):
            continue
        c = clock.cell(p, axis)
        if c > after_cell and (window is None or window.contains(c)):
            out.append(p)
    return out


FAULT_ENV = "FLOWOCR_FAULT_HWACCEL_AT"
"""**只给测试用**：硬解那条路在交付第 N 帧时假装交付不对（`HwaccelMismatch`），验证整段改软解重跑那条路。"""


class HwaccelMismatch(RuntimeError):
    """硬解交付的帧和源里的包对不上（**不是**素材自己的缺口）。`run_ocr2` 见到它就整段改软解重跑一次。"""


class SinkClosed(RuntimeError):
    """辅助流的 sink 拒收像素（块已失败 / 取消、分片已收摊）。`AuxReader` 把它记进 `refused`，不记成"pts 和帧对不上"。"""


PREROLL_PROBE_PACKETS = 64
"""`av1_preroll_ticks` 核"没有帧重排"时看前多少个包（pts == dts）。"""


def av1_preroll_ticks(video: str) -> int:
    """**有前摇的 av1 从文件头起解时，NVDEC 少交付 1~3 帧的修法**要平移的量（时间基的整数刻度）；不适用返回 0。

    病因（2026-09-22，decode-buffer / hw-decode-results）：YouTube 那批 av1 的 mp4 带 edit list，
    首包 pts 为负（−2.6 ~ −5.4 s 的前摇），ffmpeg 按 edit list 丢掉前摇帧时 NVDEC 少交付头几帧
    （`gi-s2` 连 pts 0.000 都没有）。`-ignore_editlist 1` 让前摇帧照常交付，这时 NVDEC 一帧不丢、逐字节同软解；
    再在滤镜图最前面 `setpts=PTS-刻度` 平移回原来的时间轴，负 pts 的前摇被 `select=gte(t,0)` 挡掉。
    4 条带前摇的 av1 × 1501 帧：时间戳和带 edit list 的软解逐帧相同、像素逐字节相同。

    **只认 av1、只认没有帧重排的流**：刻度 = 同一个首包带 / 不带 edit list 的 pts 之差，这只在 pts == dts 时等于
    edit list 的偏移。带 B 帧的 h264 / HEVC 上实测对不上（时间戳全对、画面全错），而它们本来也不少交付，所以不碰。
    刻度必须是整数：按秒换算会在 1/15360 这类时间基上差一刻。
    """
    def probe(*extra: str) -> list[tuple[int, int]]:
        cmd = (["ffprobe", "-v", "error", *extra, "-select_streams", "v:0", "-show_entries", "packet=pts,dts",
                "-read_intervals", f"%+#{PREROLL_PROBE_PACKETS}", "-of", "csv=p=0", video])
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        except Exception:                                   # noqa: BLE001
            return []
        rows = []
        for line in out.splitlines():
            p = line.strip().split(",")
            if len(p) >= 2 and p[0].lstrip("-").isdigit() and p[1].lstrip("-").isdigit():
                rows.append((int(p[0]), int(p[1])))
        return rows

    try:
        codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name", "-of", "csv=p=0", video],
                               capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:                                       # noqa: BLE001
        return 0
    if codec != "av1":
        return 0
    edit = probe()
    if not edit or edit[0][0] >= 0 or any(p != d for p, d in edit):
        return 0
    raw = probe("-ignore_editlist", "1")
    if not raw:
        return 0
    return raw[0][0] - edit[0][0]


def select_range(window: Window, axis: Axis, block: tuple[int | None, int | None] | None = None) -> str:
    """`select` 里的范围（逗号已转义）：相对时刻为负的帧（前摇）挡掉；窗口两端是采样桶号的条件；解码分片的块按**整数 pts** `[lo, hi)`
    （块的边界是关键帧，不是桶边界：按桶号截会让 `[kf 所在桶的起点, kf)` 哪一块都不选）。块的哪一端是 None 就用窗口那一端。"""
    t = axis.t_expr("t")
    e = window.clock.expr(t)
    lo, hi = block if block is not None else (None, None)
    parts = [rf"gte({t}\,0)", rf"gte({e}\,{window.a})" if lo is None else rf"gte(pts\,{lo})"]
    if hi is not None:
        parts.append(rf"lt(pts\,{hi})")
    elif window.b is not None:
        parts.append(rf"lt({e}\,{window.b})")
    return "*".join(parts)


def build_ffmpeg_cmd(video: str, *, window: Window, axis: Axis, pix: str = "nv12",
                     hwaccel: str | None = None, aux: dict | None = None, preroll_ticks: int = 0,
                     block: tuple[int | None, int | None] | None = None) -> list[str]:
    """只送采样帧的那条命令。**纯函数，守卫直接测它。**

    * 选帧：`select='<范围>*<采样桶换了>'`（`select_range`、`SampleClock.changed`）；`prev_t` 是上一个**输入**帧，
      被范围、负 pts 前摇挡掉的帧照样参与"上一帧"的判断。
    * **一律 `-copyts`**：交出的是容器的绝对 pts，和 ffprobe 读的包 pts（硬解核对、块尾、拼缝都问它）是同一把尺；
      桶号和窗口范围在减过 S 的时间上算（`Axis.t_expr`），`t_us` 也减 S（`Axis.key`）。不带它时 `-ss` 把输出时间戳**重新归零**
      （一整份产物的时间轴平移几个小时而**没有任何一层会报错**）。`-ss` 的位置本身就是相对 `start_time` 的（ffmpeg 自己加上 S，
      st2.mp4 上实测：S = 2 s 时 `-ss 1` 交出的第一帧在绝对 3 s 之后），所以直接给相对秒。
    * 窗口（`block` 为 None，或块的下界是 None = 窗口的第一块）：`-ss` 放在 `-i` 前面、精确 seek 到 `Window.seek_sec`（≤ 0 就从文件头读）；
      输入端 `-t` 读到窗口尾（`Window.read_sec`，有前摇时再加前摇的秒数：从文件头起解、带 `-ignore_editlist` 时 `-t` 从首个负 pts 起算）。
    * 解码分片的块 `block = (lo, hi)`（整数 pts；`decode_shards`）：`lo` 不是 None 就精确 seek 到那个关键帧前半毫秒、从它起解；
      块终点 `hi` 给了时 `-t` 读到它之后 `BLOCK_PAD_SEC`。
    * `hwaccel`（**生产默认 `cuda`**）：`-hwaccel X -hwaccel_output_format X`，滤镜链里插 `hwdownload,format=nv12`——丢弃发生在 hwdownload 之前。
    * `aux`（两遍合一遍，flowocr.extract.edge_refine）：`{"clock": AuxClock.as_list(), "scale", "url", "w", "h"}` 给了就**同一次解码分两路**
      （`split`）：采样帧照旧走 stdout，辅助流（`AuxClock` 的条件、缩到 scale、灰度）走 `url`（本机 TCP）。两路各挂一个 showinfo。
    * `preroll_ticks`（`av1_preroll_ticks` 给的）> 0：`-ignore_editlist 1` + 滤镜图最前面（辅路 `split` 之前）`setpts=PTS-刻度`；只配从文件头起解。
    """
    if pix not in ("nv12", "bgr24"):
        raise ValueError(f"不支持的像素格式 {pix!r}")
    lo, hi = block if block is not None else (None, None)
    seek = window.seek_sec() if lo is None else 0.0
    if preroll_ticks and (lo is not None or seek > 0):
        raise ValueError("preroll_ticks 只配从文件头起解（seek 到非零位置本来就不少交付）")
    # `-nostats`：进度行 `frame=… \r` 不换行，会把紧跟着的 showinfo 行粘住（见 SHOWINFO_RE 的说明）
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-nostdin"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel, "-hwaccel_output_format", hwaccel]
    if preroll_ticks:
        cmd += ["-ignore_editlist", "1"]
    pre_sec = preroll_ticks * axis.tb[0] / axis.tb[1] if preroll_ticks else 0.0
    if lo is not None:
        # 精确 seek 到关键帧前半毫秒（换算成秒的舍入不会把关键帧自己剪掉）：输入端 `-t` 从这个 seek 点算。
        # ⚠ 不能用 `-noaccurate_seek`：那时 `-t` 从**解出来的第一帧**算，而带 B 帧的流 ffmpeg 会往回多退 3/23 s、
        # 从前一个关键帧起解，块尾就被提前截掉（合成 VFR 上实测少了块尾那个桶）。两种 seek 解的帧一样多，精确 seek 只是把关键帧之前的丢掉
        seek = axis.sec(lo) - 0.0005
        dur = (axis.sec(hi) - seek + BLOCK_PAD_SEC) if hi is not None else window.read_sec(seek)
        cmd += ["-ss", f"{seek:.6f}"] + (["-t", f"{dur:.6f}"] if dur is not None else [])
    else:
        dur = (axis.sec(hi) - seek + BLOCK_PAD_SEC) if hi is not None else window.read_sec(seek)
        if dur is not None:
            dur += pre_sec
        if seek > 0:
            cmd += ["-ss", f"{seek:.6f}"]
        if dur is not None:
            cmd += ["-t", f"{dur:.6f}"]
    cmd += ["-copyts", "-i", video]
    rng = select_range(window, axis, block)
    chain = [f"select='{rng}*{window.clock.changed(axis)}'"]
    if hwaccel:
        chain += ["hwdownload", "format=nv12"]
    if pix == "bgr24":
        chain.append("format=bgr24")
    chain.append("showinfo@main=checksum=0")   # 命名实例：两路的 pts 从 stderr 前缀分得开（parse_showinfo）
    if aux:
        ac = AuxClock.from_list(aux["clock"])
        if ac.sample != window.clock or not (0 < aux["scale"] <= 1.0):
            raise ValueError(f"辅助流参数不对：{aux}")
        # **辅路和主路用同一套帧身份**：同一个范围、同一个"上一帧"规则；采样桶换了也选，所以采样帧一定在辅助流里
        b = [f"select='{rng}*{ac.changed(axis)}'"]
        if hwaccel:
            # **辅助流跟着硬解一起走**（2026-09-20）：在**显存里**缩到小图再下传。
            # ⚠ `scale_cuda` 不吃 `trunc(iw*r/2)*2` 这种表达式，所以宽高由调用方算好传进来。
            # ⚠ 缩放器和 CPU 那条（swscale）不是同一套：`edge` 数值会差，所以软硬解的 obs 互不复用
            if aux["scale"] < 1.0:
                if not (aux.get("w") and aux.get("h")):
                    raise ValueError("硬解的辅路要 aux['w'] / aux['h']：scale_cuda 不吃 trunc(iw*r/2)*2 这种表达式")
                b.append(f"scale_cuda={aux['w']}:{aux['h']}")
            b += ["hwdownload", "format=nv12"]
        elif aux["scale"] < 1.0:
            b.append(f"scale=trunc(iw*{aux['scale']}/2)*2:trunc(ih*{aux['scale']}/2)*2")
        b.append("format=gray")
        b.append("showinfo@aux=checksum=0")
        head = f"setpts=PTS-{preroll_ticks}," if preroll_ticks else ""
        fc = f"[0:v]{head}split=2[a][b];[a]{','.join(chain)}[sa];[b]{','.join(b)}[sb]"
        return cmd + ["-filter_complex", fc,
                      "-map", "[sa]", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", pix, "pipe:1",
                      "-map", "[sb]", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", aux["url"]]
    if preroll_ticks:
        chain.insert(0, f"setpts=PTS-{preroll_ticks}")
    return cmd + ["-vf", ",".join(chain), "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", pix, "-"]


PROBE_CELLS = 4
"""硬解试解解几个采样桶。"""


def hwaccel_blocker(video: str, *, stream: StreamInfo, window: Window, width: int, height: int,
                    hwaccel: str, probe: int = PROBE_CELLS) -> str | None:
    """硬解在这段素材上开不开得了：试解窗口起点之后 `probe` 个采样桶，**硬解和软解交出的 pts 列表逐个比**，不一致就返回原因（开得了返回 None）。

    为什么不管有没有前摇都比（frame-pts 计划）：时间桶下被吞的帧会由同一桶的下一帧顶上、只晚一个源帧，判"偏离"查不出来；
    已知的"NVDEC 少交头 1–3 帧"由这一条兜住。代价是多一次软解试解（从窗口起点前的关键帧解到约 4 个采样桶之后）。
    有前摇的 av1 走平移修法时**再比一道像素**：刻度算错时时间戳照样都对，只有像素看得出来（NVDEC 对软解逐字节相同）。"""
    b = window.a + probe if window.b is None else min(window.b, window.a + probe)
    w = Window(window.clock, window.a, b)

    def run(hw: str | None) -> tuple[list, int]:
        src = FfmpegSource(video, stream=stream, window=w, width=width, height=height, pix="nv12", hwaccel=hw)
        got = []
        try:
            for f in src:
                got.append((f.key, f.image))
        finally:
            src.close()
        return got, src.preroll_ticks

    try:
        got, preroll = run(hwaccel)
    except RuntimeError as e:
        return str(e).split("。")[0]
    except Exception as e:                                        # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    want, _ = run(None)
    a, b_ = [t for t, _ in got], [t for t, _ in want]
    if a != b_:
        first = next((i for i, (x, y) in enumerate(zip(a, b_)) if x != y), min(len(a), len(b_)))
        return (f"试解的采样帧和软解对不上：硬解 {len(a)} 帧、软解 {len(b_)} 帧，第 {first} 帧起不同"
                f"（硬解 {a[first] if first < len(a) else '—'} µs / 软解 {b_[first] if first < len(b_) else '—'} µs）")
    if preroll:
        import numpy as np
        bad = [x[0] for x, y in zip(got, want) if not np.array_equal(x[1], y[1])]
        if bad:
            return (f"有前摇的 av1 平移修法（{preroll} 刻度）和软解对不上：{len(bad)}/{len(got)} 帧画面不同"
                    f"（首个 {bad[0]} µs）")
    return None


class _Forward:
    """把辅路 pts 转发到别的进程（`EdgeProxy.pts_put`）：和本地 `Queue` 同一个 `put` 接口，drain 线程不用分两套写法。"""
    __slots__ = ("put",)

    def __init__(self, put) -> None:
        self.put = put


class AuxReader:
    """同一个 ffmpeg 的第二路输出（本机 TCP）-> 灰度帧 -> `sink.push(t_us, 帧, (采样桶号, 辅助桶号))`（edge_refine.FrameRing，满了阻塞）。
    pts 队列里的每一项是 `(showinfo 的 n, t_us, 采样桶号, 辅助桶号)`——桶号在解析 showinfo 时就算好了（`FfmpegSource` 的 drain 线程），
    跨进程（`--edge-proc` / 解码分片的转发）也原样带着。先 listen 再起 ffmpeg，ffmpeg 起来就 connect；读到 EOF 或出错都 `sink.close()`。"""

    def __init__(self, *, width: int, height: int, sink, pts_q: Queue) -> None:
        self.Ws, self.Hs, self.sink, self.pts_q = width, height, sink, pts_q
        self.misaligned = ""          # 非空 = 辅路 pts 和帧对不上、提前停了（原因写这里，run_ocr2 记进 _meta.edge）
        self.refused = ""             # 非空 = sink 拒收（`SinkClosed`：下游已失败 / 取消），提前停了
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.url = f"tcp://127.0.0.1:{self.srv.getsockname()[1]}"
        self.sent = 0
        self.last_key: int | None = None   # 最后推进 sink 的 t_us：辅助流盖到哪儿（`ocr_complete.aux_shortfall` 拿它对采样流的最后一帧）
        self.cpu_sec = 0.0
        self.stopped_early = False
        self.th = threading.Thread(target=self._run, daemon=True, name="aux-reader")
        self._life = threading.Lock()
        self._cancelled = threading.Event()
        self._conn = None

    def start(self) -> "AuxReader":
        self.th.start()
        return self

    def _run(self) -> None:
        import numpy as np

        nbytes = self.Ws * self.Hs
        conn = f = None
        c0 = time.thread_time()
        try:
            self.srv.settimeout(0.2)
            deadline = time.monotonic() + 60
            while not self._cancelled.is_set():
                try:
                    conn, _ = self.srv.accept()
                    break
                except socket.timeout:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("辅助流 60 s 内未连接")
            with self._life:
                if self._cancelled.is_set():
                    return
                self._conn = conn
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
            f = conn.makefile("rb")
            prev_key, prev_cells = None, None
            k = 0
            while True:                                    # 读到 EOF（VFR 上预计不了帧数）
                t_rd = time.perf_counter()
                buf = f.read(nbytes)
                _tl.span("aux_read", t_rd, a=k)
                if len(buf) < nbytes:
                    self.stopped_early = len(buf) > 0      # 半帧 = 断了；0 字节 = 正常的 EOF
                    break
                t_q = time.perf_counter()
                got = self.pts_q.get()
                _tl.span("aux_pts_wait", t_q, a=k)       # 像素到了、pts 还没到（pts 从管线进程的 stderr 线程转来，decode-buffer）
                if got is QUEUE_SENTINEL:
                    self.stopped_early = True              # 像素来了、pts 没来：宁可停，也不编一个时刻
                    break
                sn, key, sc, ac = got
                if sn != k:
                    # 同采样流：丢了一行 pts，之后每一帧都会拿到下一帧的时刻、整体错开（2026-09-20 实测过）。
                    # 停下辅助流（回抠退回采样级）比拿错画面算边界强
                    self.stopped_early = True
                    self.misaligned = f"第 {k} 帧字节配到了 showinfo 的第 {sn} 行"
                    break
                if prev_key is not None and key <= prev_key:
                    # 帧身份必须唯一：同一 pts 出现两次 = 坏文件，帧环里后一帧会覆盖前一帧
                    self.stopped_early = True
                    self.misaligned = f"第 {k} 帧 t_us {key} 不大于上一帧 {prev_key}（帧身份不唯一）"
                    break
                if prev_cells is not None and (sc < prev_cells[0] or ac < prev_cells[1] or (sc, ac) == prev_cells):
                    # ffmpeg 每格只交第一帧：格不前进 = Python 复刻的桶号和 ffmpeg 的不一致
                    self.stopped_early = True
                    self.misaligned = f"第 {k} 帧的桶 {(sc, ac)} 没在上一帧 {prev_cells} 之后（桶号和 ffmpeg 的选帧不一致）"
                    break
                prev_key, prev_cells = key, (sc, ac)
                self.sink.push(key, np.frombuffer(buf, np.uint8).reshape(self.Hs, self.Ws), (sc, ac))
                self.sent += 1
                self.last_key = key
                k += 1
        except SinkClosed as exc:
            self.stopped_early = True
            self.refused = str(exc)
        except (OSError, RuntimeError) as exc:
            self.stopped_early = True
            if not self._cancelled.is_set() and isinstance(exc, RuntimeError):
                self.misaligned = repr(exc)
        finally:
            self.cpu_sec = time.thread_time() - c0
            self.sink.close()
            with self._life:
                self._conn = None
            if f is not None:
                f.close()
            for s in (conn, self.srv):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass

    def join(self, timeout: float = 30.0) -> None:
        self.th.join(timeout)

    def abort(self) -> None:
        """取消接入、像素、PTS 和 sink 等待；读线程负责最终关闭连接。"""
        with self._life:
            self._cancelled.set()
            if self._conn is not None:
                try:
                    self._conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.pts_q.put(QUEUE_SENTINEL)
        self.sink.close()


class FfmpegSource:
    """ffmpeg 子进程只把采样帧送进管道；跳过的帧在子进程里解完就丢。产出 `Frame`。

    **和推理天然并行**：解码在另一个进程里，Python 这边只等管道。
    `block = (lo, hi)`（整数 pts，解码分片的一块，`decode_shards`）：只取 pts 在 `[lo, hi)` 的帧，`lo` 给了就从那个关键帧起解；
    哪一端是 None 就用窗口的那一端。没给 = 整个窗口。
    """

    def __init__(self, video: str, *, stream: StreamInfo, window: Window, width: int, height: int,
                 pix: str = "nv12", aux: dict | None = None, hwaccel: str | None = None,
                 preroll_ticks: int | None = None, block: tuple[int | None, int | None] | None = None,
                 packets: PacketIndex | None = None) -> None:
        import cv2

        self._cv2 = cv2
        self.stream, self.window, self.block = stream, window, block
        self.axis = stream.axis
        self.hwaccel = hwaccel or None
        lo = block[0] if block is not None else None
        # 有前摇的 av1 从文件头起硬解：平移修法（`av1_preroll_ticks`）。None = 按条件自动判；显式给 0 = 不修
        if preroll_ticks is None:
            preroll_ticks = (av1_preroll_ticks(video)
                             if self.hwaccel and lo is None and window.seek_sec() == 0 else 0)
        self.preroll_ticks = preroll_ticks
        self.video = video
        self.W, self.H, self.pix = width, height, pix
        self.stopped_early = False
        self.advanced_us = 0
        """推进到哪：最后一个采样帧所在桶的结束 − 窗口起点（µs；`ocr_complete.is_complete` 比）。"""
        self.cells_skipped = 0
        """相邻采样帧之间的空桶数（窗口起点算作第一帧的"上一帧"；素材缺口和 VFR 的稀处都会有）。
        块（`lo` 给了）的第一帧不数（它看不到真正的上一帧，合流那边统一数一次）。"""
        self.cell_first: int | None = None              # 首末采样帧的桶号（`--workers` 拼缝核对）
        self.cell_last: int | None = None
        self.packets = packets
        self.proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._cancelled = False
        self.stderr_tail: list[str] = []
        import os as _os
        self.fault_at = int(_os.environ.get(FAULT_ENV, "-1") or -1)
        # 两遍合一遍：`aux = {"sink" | "remote", "clock", "scale"}`，同一次解码分出第二路（灰度、缩、按辅助桶取帧）进 sink
        self.aux_cfg = aux
        self.aux: AuxReader | None = None

    def describe(self) -> str:
        s = f"ffmpeg 子进程（select + {self.pix} 管道，跳过的帧不进 Python；{self.window.clock.describe()}）"
        if self.aux_cfg:
            s += (f" + 同一次解码分出辅助流（scale {self.aux_cfg['scale']}、"
                  f"{AuxClock.from_list(self.aux_cfg['clock']).describe()}、灰度、本机 TCP）")
        return s

    @property
    def frame_bytes(self) -> int:
        return self.W * self.H * 3 if self.pix == "bgr24" else self.W * self.H * 3 // 2

    def _check_hw(self, prev: tuple[int, int] | None, pts: int, cell: int, t_sec: float) -> None:
        """硬解这条路交的是不是**源里有的、桶里第一个**那一帧（frame-pts 计划第 6 节；`t_sec` 是相对秒，和桶、`PacketIndex` 同一把尺）：
        ① 两个相邻采样帧之间隔了空桶 -> 问源文件那些桶里有没有包；② 采样帧比它的桶起点晚了超过 1.5 个参考帧间隔 -> 问 `[桶起点, 这一帧)`。
        有 = 解码器吞了帧（`HwaccelMismatch`）；没有 = 素材缺口放行；探不出来按"源里有"算（宁可错退回软解，也不放过真吞帧）。
        块的第一帧（`lo` 给了）不在这里核：它看不到真正的上一帧，由合流那边核（`decode_shards.ShardedSource`）。"""
        w, clock = self.window, self.window.clock
        if prev is None:
            if self.block is not None and self.block[0] is not None:
                return
            after_pts, after_cell, lo_sec = None, w.a - 1, w.seek_sec()
        else:
            after_pts, after_cell = prev
            lo_sec = self.axis.sec(after_pts)
        late = t_sec - clock.cell_start(cell) > 1.5 / max(self.stream.ref_fps, 1e-6)
        if cell <= after_cell + 1 and not late:
            return
        if self.packets is None:
            self.packets = PacketIndex(self.video, self.axis)
        got = eaten(self.packets.between(lo_sec, t_sec), clock, self.axis, after_pts=after_pts, before_pts=pts,
                    after_cell=after_cell, window=w)
        if got is None or got:
            self.close()
            what = ("探不出源里有没有帧（按吞帧算）" if got is None else
                    f"源里有、却没交付的帧 {len(got)} 个（首个 pts {got[0]} = {self.axis.sec(got[0]):.6f} s）")
            raise HwaccelMismatch(f"--hwaccel {self.hwaccel} 交付不对：交出 {t_sec:.6f} s（桶 {cell}）之前{what}。"
                                  f"病因和量法见 hw-decode-results 报告")

    def __iter__(self):
        cv2 = self._cv2
        clock, w, axis = self.window.clock, self.window, self.axis
        with self._proc_lock:
            if self._cancelled:
                return
            aux = None
            aux_clock = None
            if self.aux_cfg:
                c = self.aux_cfg
                Ws, Hs = ((int(self.W * c["scale"] / 2) * 2, int(self.H * c["scale"] / 2) * 2) if c["scale"] < 1.0
                          else (self.W, self.H))
                aux_clock = AuxClock.from_list(c["clock"])
                if c.get("remote") is not None:
                    # 辅助流收在**另一个进程**里（`--edge-proc`，edge_proc.EdgeProxy）：ffmpeg 直接 connect 过去，
                    # 这边只把 stderr 里的辅路 pts（连同算好的桶号）逐行转发（`pts_put`）
                    url = c["remote"].url
                else:
                    self.aux = AuxReader(width=Ws, height=Hs, sink=c["sink"], pts_q=Queue()).start()
                    url = self.aux.url
                # `w`/`h` 是给 `scale_cuda` 的（硬解那条路），表达式它不吃；软解那条仍按比例算，两边同一组数
                aux = {"clock": c["clock"], "scale": c["scale"], "url": url, "w": Ws, "h": Hs}
            cmd = build_ffmpeg_cmd(self.video, window=w, axis=axis, pix=self.pix, aux=aux, hwaccel=self.hwaccel,
                                   preroll_ticks=self.preroll_ticks, block=self.block)
            nbytes = self.frame_bytes
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=nbytes * 2)
            proc = self.proc
            stderr = proc.stderr
        pts_q: Queue = Queue()

        aux_q = self.aux.pts_q if self.aux is not None else None
        if self.aux_cfg and self.aux_cfg.get("remote") is not None:
            aux_q = _Forward(self.aux_cfg["remote"].pts_put)

        drain_errors = []

        def drain() -> None:
            # **stderr 必须一直读**：不读满了管道就死锁，而 pts 也在里面。
            # 两路的 showinfo 是命名实例，按前缀分流：主路 -> pts_q、辅路 -> AuxReader 的队列（桶号在这里就算好）
            try:
                for raw in stderr:                        # type: ignore[union-attr]
                    for line in raw.decode("utf-8", "replace").split("\r"):   # 进度行用 \r 结尾，别让它粘住下一行
                        got = parse_showinfo(line)
                        if got is not None:
                            which, sn, pts, t = got
                            if which == "aux" and aux_q is not None:
                                aux_q.put((sn, axis.key(t), *aux_clock.cells(pts, axis)))
                            else:
                                pts_q.put((sn, pts, t))
                        elif "pts_time" not in line and line.strip():
                            self.stderr_tail.append(line.rstrip())
                            del self.stderr_tail[:-8]
            except BaseException as exc:
                drain_errors.append(exc)
            finally:
                pts_q.put(QUEUE_SENTINEL)
                if aux_q is not None:
                    try:
                        aux_q.put(QUEUE_SENTINEL)
                    except BaseException as exc:
                        drain_errors.append(exc)

        th = threading.Thread(target=drain, daemon=True)
        th.start()

        import numpy as np

        n = 0
        prev: tuple[int, int] | None = None              # 上一个交出的 (pts, 桶号)
        prev_key: int | None = None
        eof = False
        while True:
            t_rd = time.perf_counter()
            try:
                if self._cancelled:
                    break
                buf = proc.stdout.read(nbytes)       # type: ignore[union-attr]
            except (OSError, ValueError):
                if not self._cancelled:
                    raise
                break
            _tl.span("pipe_read", t_rd, a=n)
            if len(buf) < nbytes:
                self.stopped_early = len(buf) > 0    # 半帧 = 断了；0 字节是 EOF（是不是 ffmpeg 出错退的，收尾时看退出码）
                eof = len(buf) == 0
                break
            t_q = time.perf_counter()
            got = pts_q.get()
            _tl.span("pts_wait", t_q, a=n)            # 像素到了、pts 还没到（stderr 线程跟不上时在这里等，decode-buffer）
            if got is QUEUE_SENTINEL:
                # 像素来了、pts 没来 = showinfo 没挂上或者被截断。**宁可报错也不能编一个时间戳。**
                self.stopped_early = True
                break
            sn, pts, t = got
            if sn != n:
                # showinfo 自己的帧计数和"这是第几帧字节"对不上 = 丢了（或多了）一行 pts，
                # 再往下每一帧都会拿到别人的时间戳。**当场炸，不静默错开**
                self.close()
                raise RuntimeError(f"采样流的 pts 行和帧对不上：第 {n} 帧字节配到了 showinfo 的第 {sn} 行"
                                   f"（stderr 丢行 / 多行），不能再往下配了")
            if n < TB_CHECK_FRAMES:
                bad = check_tb(pts, t, axis)
                if bad:
                    self.close()
                    raise RuntimeError(f"采样流第 {n} 帧：{bad}")
            key, cell = axis.key(t), clock.cell(pts, axis)
            if prev_key is not None and key <= prev_key:
                # **帧身份必须唯一**：同一 pts 出现两次 = 坏文件，再往下同号不同画面。当场炸，不静默覆盖
                self.close()
                raise RuntimeError(f"采样帧的身份不唯一：t_us {key} 不大于上一帧 {prev_key}（这份文件有重复的 pts）")
            if (prev is not None and cell <= prev[1]) or cell < w.a:
                # ffmpeg 每个桶只交第一帧：桶号不前进 = Python 复刻的桶号和 ffmpeg 的选帧不一致
                self.close()
                raise RuntimeError(f"采样帧的桶号 {cell} 没在上一帧（{prev[1] if prev else w.a - 1}）之后：Python 复刻的桶号和 ffmpeg 的选帧不一致")
            if not w.contains(cell):
                break                                # 窗口尾按桶号截（select 的范围本来已经挡掉了）
            a = np.frombuffer(buf, np.uint8)
            t_cv = time.perf_counter()
            frame = (a.reshape(self.H, self.W, 3).copy() if self.pix == "bgr24"
                     else cv2.cvtColor(a.reshape(self.H * 3 // 2, self.W), cv2.COLOR_YUV2BGR_NV12))
            _tl.span("to_bgr", t_cv, a=n)
            if self.hwaccel:
                self._check_hw(prev, pts, cell, axis.sec(pts))
            if prev is not None:
                self.cells_skipped += cell - prev[1] - 1
            elif self.block is None or self.block[0] is None:
                self.cells_skipped += cell - w.a
            n += 1
            prev, prev_key = (pts, cell), key
            if self.hwaccel and n == self.fault_at:
                self.close()
                raise HwaccelMismatch(f"（{FAULT_ENV} 注入的故障，第 {n} 帧）")
            # **推进量认桶**：缺口里那几个桶没有帧，按"送了几帧 × 间隔"数会把"跳过的时间"算成"没跑完"
            self.advanced_us = w.span_us(cell + 1)
            if self.cell_first is None:
                self.cell_first = cell
            self.cell_last = cell
            yield Frame(key, key / 1e6, frame, cell, pts)
        # 顺序不能反：drain 那个线程要读到 EOF 才结束，而 EOF 只有在 ffmpeg 退出
        # 之后才来，ffmpeg 又要等我们关掉 stdout。所以先 close 再 join。
        self.close()
        th.join(timeout=5)
        if stderr is not None and not stderr.closed:
            stderr.close()              # 线程已经退了，这时候关才安全
        if eof and proc.returncode not in (0, None) and not self._cancelled:
            self.stopped_early = True    # 读到 EOF 但 ffmpeg 是出错退的（覆盖率那道门会判它没跑完）
        if drain_errors and not self._cancelled:
            raise RuntimeError(f"解码 stderr / 辅助 PTS 转发失败：{drain_errors[0]!r}")

    def close(self) -> None:
        """**子进程一定要收掉。** 正常跑完是关管道让它自己退；

        但消费者中途 `break`（比如时间戳判据不过）时 ffmpeg 还在往管道里写，
        关了 stdout 它会拿到 EPIPE 自己退——万一没退就硬杀。
        漏一个 ffmpeg 在后台读着几个小时的素材，是长任务里最难查的那种问题。
        """
        # 句柄一直登记到 wait 完成：取消仍可 kill 唤醒并发的管道 close，
        # 后来的 close 等第一个回收者完成，不能把“被取走”当成“已退出”。
        with self._close_lock:
            with self._proc_lock:
                proc = self.proc
            if proc is None:
                return
            try:
                try:
                    if proc.stdout and not proc.stdout.closed:
                        proc.stdout.close()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            finally:
                with self._proc_lock:
                    if self.proc is proc and proc.poll() is not None:
                        self.proc = None

    def cancel(self) -> None:
        """不可逆取消：阻止迟到启动，先杀子进程唤醒读，再回收管道。"""
        with self._proc_lock:
            self._cancelled = True
            proc = self.proc
            if proc is not None and proc.poll() is None:
                proc.kill()
            aux = self.aux
        if aux is not None:
            aux.abort()
        self.close()
        if aux is not None and aux.th is not threading.current_thread():
            aux.join(5)
            if aux.th.is_alive():
                raise RuntimeError("辅助流取消后读线程未退出")


class PrefetchSource:
    """C：把解码挪到后台线程，主线程只做 det / rec。

    **有界队列**（`depth × 帧大小`，1080p 每帧 6 MB），**严格保序**——
    单个生产者顺序放、单个消费者顺序取，复用判断依赖的"上一帧"仍然成立。
    """

    def __init__(self, inner, depth: int = 4) -> None:
        if depth < 1:
            raise ValueError(f"队列深度必须 ≥ 1，给的是 {depth}")
        self.inner, self.depth = inner, depth
        self.error: BaseException | None = None
        self._q: Queue = Queue(maxsize=depth)
        self._th: threading.Thread | None = None
        self._stop = False

    def describe(self) -> str:
        return f"{self.inner.describe()} + 预取队列（深度 {self.depth}）"

    @property
    def stopped_early(self) -> bool:
        return self.inner.stopped_early

    @property
    def advanced_us(self) -> int:
        return self.inner.advanced_us

    @property
    def cells_skipped(self) -> int:
        return getattr(self.inner, "cells_skipped", 0)

    @property
    def cell_first(self) -> int | None:
        return self.inner.cell_first

    @property
    def cell_last(self) -> int | None:
        return self.inner.cell_last

    def __iter__(self):
        # **每次迭代换一个新队列**：留着上一轮的残渣，第二次迭代会先吐出旧帧。
        q = self._q = Queue(maxsize=self.depth)
        self._stop = False

        def produce() -> None:
            try:
                for item in self.inner:
                    if self._stop:      # close() 立的旗：别再往下解码了
                        break
                    t0 = time.perf_counter()
                    q.put(item)
                    _tl.span("prefetch_put", t0, a=item[0])
            except BaseException as exc:              # noqa: BLE001
                # **生产者的异常不能吞**：吞了就变成"提前没帧了"，
                # 而那和"正常读到头"在下游看起来一模一样。
                self.error = exc
            finally:
                q.put(QUEUE_SENTINEL)

        th = self._th = threading.Thread(target=produce, daemon=True)
        th.start()
        while True:
            item = q.get()
            if item is QUEUE_SENTINEL:
                break
            yield item
        th.join(timeout=10)
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        """**先解开生产者、等它退干净，再关里层。**

        消费者中途 `break` 时（`run_ocr2` 的 `bad_pts` 就是这么跳出来的），生产者多半正卡在 `q.put`（队列满了）。
        要做三件事，顺序不能换：1. 排空队列——把卡在 `put` 上的生产者放出来；2. 立起停止旗，让它下一轮就退；
        3. **join**——少了它，`inner.close()` 会和还活着的生产者抢同一个资源。排空要和 join 交替做，因为生产者被放出来之后还会再放一个。
        """
        self._stop = True
        th = self._th
        deadline = time.monotonic() + 10.0
        while th is not None and th.is_alive() and time.monotonic() < deadline:
            self._drain()
            th.join(timeout=0.05)
        self._drain()
        self.inner.close()

    def _drain(self) -> None:
        """把队列腾空。队列有界，所以这个循环有上界。"""
        while True:
            try:
                self._q.get_nowait()
            except Exception:                         # noqa: BLE001  空了就算
                return


def make_source(video: str, *, stream: StreamInfo, window: Window, width: int, height: int,
                prefetch: int = 0, pix: str = "nv12", aux: dict | None = None, hwaccel: str | None = None):
    """一路 ffmpeg 解码，需要的话再包一层预取。`aux` 给了就同一次解码分两路（两遍合一遍的第二路输出）。"""
    src = FfmpegSource(video, stream=stream, window=window, width=width, height=height, pix=pix, aux=aux, hwaccel=hwaccel)
    return PrefetchSource(src, prefetch) if prefetch else src
