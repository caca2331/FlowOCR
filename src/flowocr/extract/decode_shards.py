"""解码分片（`run_ocr2 --decode-shards`，decode-buffer）：几路 ffmpeg 交替解**相邻的时间块**，按时间顺序交帧。

为什么：硬解之后每一帧（60 fps 的辅助流）都要 ffmpeg 自己的 CUDA 上下文跑一次 `scale_cuda` 核函数；只解码实测负载下一路慢 2 倍、
3 路 −55%，管线里 quick 六段 −4.9%。**病因是推测**（Windows 上不同进程的 CUDA 上下文分时、小核排在 det / rec 后面），
按上下文的 GPU 时间线没量过；分片有效是实测。

形状（原型 a1-mask-reuse 实验里的 `shard_proto.py` 量过）：

* **块**：窗口按约 `block_sec` 切，块边界对齐**关键帧**（`ffprobe` 只读包头的整数 pts，**边探边切**——整片 f4 探完要 41 s，起流不能等它），
  所以每块从关键帧起解（精确 seek 到关键帧前半毫秒；带 B 帧的流 ffmpeg 自己会从前一个关键帧起解再剪掉，这份多解和一路解码时的 seek 一样）。第一块从窗口起点走普通路径（文件头有前摇的 av1 要 `-ignore_editlist` 平移，§8.6）；
  剩下不到半块时不再切（末块并进上一块，免得为几帧起一个 ffmpeg）。**尾部按路数均分**：剩下的不到"路数 × 块长"时，
  块长缩成"剩下的 / 路数"（每切一块重算一次，所以越切越短；不短于 `TAIL_MIN_SEC`），免得最后一块只有一路在解、其余路空着；窗口不到两块长时也照样切开。
  每块一个 `FfmpegSource(block=(lo, hi))`：身份核对（showinfo 的 `n` / 时间基 / 键唯一 / 桶号前进 / 硬解的 ① ②）**原样逐块做**；
  块和块之间不重不漏靠**整数 pts** 区间 `[lo, hi)`（块的边界是关键帧，不是桶边界）。
* **接缝去重**（`seam_drop`）：块的第一帧看不到它真正的上一帧（在上一块里），ffmpeg 会把它当新桶选中。合流时它和**合流里上一个交出的帧**
  落在同一格（采样流：同一采样桶；辅助流：同一辅助桶且同一采样桶）就丢掉。它的上一帧和它同格时，这一格的第一帧在前面的块里、已经交出去了，
  于是合流里上一个交出的帧就在这一格；不同格时上一个交出的帧也不在这一格。一块整个落在一个桶里时，它去重之后一帧不交，同样正确。
  合流处还核键严格递增、数空桶（`cells_skipped`，只数一次），并对硬解路每块交出的第一帧补一道核对（`block_first_eaten`）。
* **几路"谁空谁领下一块"**：每路各自选硬解（`hw`）或软解（`sw`，CPU 那一路）；快慢自动配平。
* **按序、边解边交**：同时在解 / 解完没交完的块最多 `ahead` 个（默认 = 路数 + 1：尾部均分后某一路先解完时不用干等在途名额，多出的那一块受字节预算管）；当前那一块**解出一帧就交一帧**。
  采样帧按块序从 `__iter__` 交出，辅助流由转发线程按块序写给回抠进程（它看到的和一路 ffmpeg 直连时一样：一条 TCP 流 + 逐帧 pts）。
* **内存按字节封顶**（2026-09-24 Codex 审计 P2：块数有界不等于内存有界——长 GOP 能切出 150 s 的块、4K 上一块 20 s 就是 2 GB）：
  **没有消费者在等**的块，各路往里放东西前先看总缓冲过没过 `budget_mb`，过了就停着（ffmpeg 跟着阻塞在管道上）。
  **有消费者在等的块永远放行**——于是不会两路互等：等着的那一路总有东西来，它拿走之后缓冲就降下去。
  代价：超预算之后预取变浅，几路并行的好处打折；上限约是预算 + 消费者等待时顺带解出来的那几帧。
* **每块收尾核一次这一块的尾巴**：块自己核了"第一帧 -> 最后一帧"之间（①②），块尾（合流里最后一个交出的帧 -> 块终点）由这里核，
  探测区间截在块终点——整块一帧没交（Codex 审计 P1：首块空时原来整段放过去、`complete` 照样为真）也落在这一步。问 `PacketIndex`：
  源里有、桶号在最后交出的那一格之后的包 = 解码出错（硬解那路抛 `HwaccelMismatch` -> 整段改软解重跑；软解那路抛 RuntimeError）；
  源里没有 = 素材缺口、放行；探不出来：硬解那路保守当吞帧，软解那路放行、记 `probe_unknown`。
* **失败一处、全停**：块自己的错按块序抛（同单路：失败块在失败点之前已排好的帧交不交、交几帧看时序，下游整段作废重跑）；辅助流转发自己的错（连不上 / 写不进 / pts 送不出）和规划失败进**共享的失败状态**，
  唤醒所有等待方、停掉在解的 ffmpeg，由 `__iter__` 抛出（原来转发的错被吞掉：块数 ≤ 路数时无声少了辅助流，多一块就永远卡住）。
  辅助流少交（半路断了 / 不被预算挡着却 60 s 没前进 / pts 对不上）也是这一块的错，不当成"读完了"。

没 GPU 时由调用方把各路都换成 `sw`（owner：每个操作都要有能跑的 CPU 版，不求调度一样）。
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Iterable, Iterator

from flowocr.extract import framesource
from flowocr.extract import timeline as _tl
from flowocr.extract.aux_transport import TcpAux
from flowocr.extract.framegrid import Axis, Window

TAIL_MIN_SEC = 10.0      # 尾部均分时块长的下限：再短，每块起 ffmpeg + 从前一个关键帧预解的开销（约 0.3 s）就不划算

BUDGET_MB = 1024
"""纯预取（没有消费者在等的块）最多攒这么多字节。1080p60、采样 2 fps + 0.35 辅助流约 28 MB / 秒素材，即约 37 s。"""


class KeyframeStream:
    """相对时刻在 `[t0, t1)` 里的关键帧的**整数 pts**（绝对），**边读边给**（ffprobe 只读包头，不解码）。只认递增的（解码序里偶有回头的不算）。
    读整数 pts、不读打印出来的 `pts_time`：后者舍入过，按它截块可能把关键帧自己排除出去。`t1 = None` 读到文件尾。
    `close()` 杀掉 ffprobe；读完后 `rc` 是它的退出码。"""

    def __init__(self, video: str, t0: float, t1: float | None, axis: Axis) -> None:
        import tempfile

        self.video, self.t0, self.t1, self.axis = video, t0, t1, axis
        self.rc: int | None = None
        self.err = tempfile.TemporaryFile()           # stderr 落临时文件（管道不读会满；DEVNULL 会把出错原因丢掉）
        self.proc = subprocess.Popen(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", framesource.probe_interval(axis.abs_sec(t0), None if t1 is None else axis.abs_sec(t1)),
             "-show_entries", "packet=pts,flags", "-of", "csv=p=0", video],
            stdout=subprocess.PIPE, stderr=self.err, text=True)

    def __iter__(self) -> Iterator[int]:
        last = None
        for ln in self.proc.stdout:                 # type: ignore[union-attr]
            parts = ln.strip().split(",")
            if len(parts) < 2 or "K" not in parts[1] or not parts[0].lstrip("-").isdigit():
                continue
            p = int(parts[0])
            t = self.axis.sec(p)
            if self.t0 <= t and (self.t1 is None or t < self.t1) and (last is None or p > last):
                last = p
                yield p
        self.rc = self.proc.wait()

    def stderr_tail(self) -> str:
        self.err.seek(0)
        return self.err.read().decode("utf-8", "replace").strip()[-300:]

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


Block = tuple[int | None, int | None]
"""一块 `(lo, hi)`：整数 pts 区间 `[lo, hi)`；`lo = None` = 窗口的第一块（从窗口起点走普通 seek），`hi = None` = 窗口的最后一块（到窗口尾）。"""


def iter_blocks(kfs: Iterable[int], t0: float, t1: float | None, axis: Axis,
                block_sec: float, lanes: int = 1) -> Iterator[Block]:
    """块表，关键帧（整数 pts）来一个判一个（**纯函数**，守卫直接测）：第一块从窗口起点 `t0` 起（`lo = None`），
    之后在"上一块起点 + 块长"之后的第一个关键帧处切——**切完剩下的不到半块就不切**（末块并进上一块）。`t1` 是窗口尾（秒；None = 不知道，
    不做尾部均分）。块长平时是 block_sec；剩下的不到 `lanes` 块时缩成"剩下的 / lanes"（每块重算，300 s 窗口两路是 60×4 + 30 + 16 + 14；
    不短于 `TAIL_MIN_SEC`，block_sec 不到它的两倍时不短于半块），先解完的那一路接着领下一小块；`lanes=1` 时就是 block_sec。"""
    end = float("inf") if t1 is None else t1
    floor = min(TAIL_MIN_SEC, block_sec / 2)
    lo_t, lo = t0, None
    for kf in kfs:
        k = axis.sec(kf)
        want = min(block_sec, max(floor, (end - lo_t) / lanes))
        if lo_t + want <= k and k + max(want / 2, floor) <= end:
            yield lo, kf
            lo_t, lo = k, kf
    yield lo, None


def plan_blocks(video: str, window: Window, axis: Axis, block_sec: float, *, t1: float | None = None,
                kfs: list[int] | None = None, lanes: int = 1) -> list[Block]:
    """整张块表（给了 `kfs` 就不探）。管线里用 `BlockPlan`（边探边给）。`t1`：窗口尾的秒数（不设上限的窗口给容器时长）。"""
    t0 = window.start_sec
    t1 = window.end_sec if window.end_sec is not None else t1
    if kfs is None:
        ks = KeyframeStream(video, t0, t1, axis)
        try:
            return list(iter_blocks(ks, t0, t1, axis, block_sec, lanes))
        finally:
            ks.close()
    return list(iter_blocks(kfs, t0, t1, axis, block_sec, lanes))


class BlockPlan:
    """管线用的块表：ffprobe 边探关键帧、边吐块（`ShardedSource` 的规划线程读它）。`close()` 停掉 ffprobe。"""

    def __init__(self, video: str, window: Window, axis: Axis, block_sec: float, lanes: int = 1,
                 t1: float | None = None) -> None:
        t0 = window.start_sec
        t1 = window.end_sec if window.end_sec is not None else t1
        self.ks = KeyframeStream(video, t0, t1, axis)
        self.args = (t0, t1, axis, block_sec, lanes)
        self.error = ""                              # 非空 = ffprobe 出错、后面没切开的部分由一路整段解（进 `_meta.decode_shards.plan_error`）

    def __iter__(self) -> Iterator[Block]:
        yield from iter_blocks(self.ks, *self.args)
        if self.ks.rc:
            self.error = f"ffprobe 探关键帧退出码 {self.ks.rc}：{self.ks.stderr_tail()}"
            print(f"[解码分片] ⚠ {self.error}——后面没切开的部分由一路整段解", flush=True)

    def close(self) -> None:
        self.ks.close()


def seam_drop(prev_cells, cells) -> bool:
    """接缝去重（**纯函数**，守卫直接测）：块的这一帧和**合流里上一个交出的帧**落在同一格就丢。
    采样流的"格"是采样桶号；辅助流是 `(采样桶号, 辅助桶号)`。见文件头"接缝去重"。"""
    return prev_cells is not None and cells == prev_cells


def block_first_eaten(window: Window, axis: Axis, lo: int, f_pts: int, f_cell: int,
                      after_cell: int, ref_fps: float, packets) -> list[int] | None:
    """硬解路一块**交出的第一帧** f 前面有没有被吞的帧（frame-pts 计划第 6 节，owner 2026-11-23 定）。块里的 ① ② 看不到 f 真正的上一帧，
    这一道在合流处补上：`m` 是合流里上一个交出的帧（桶号 `after_cell`），`lo` 是这一块的关键帧（整数 pts，关键帧表里有，确知源里有这个包）。
    * 关键帧所在的桶比 m 新、f 却不是关键帧 -> 关键帧自己被吞了（NVDEC"少交头几帧"在每块开头的样子；它是那一格的第一帧，必须交出）。
    * 否则 f 之前隔了空桶，或 f 比 `max(桶起点, 关键帧)` 晚过 1.5 个参考帧间隔 -> 问源 `[lo, f)` 里有没有桶号比 m 新的包。
    返回被吞的包（空 = 没问题）；探不出来 None（硬解路按吞帧算）。f 和 m 同格时调用方已经去重掉了，不会走到这里。"""
    clock = window.clock
    lo_cell = clock.cell(lo, axis)
    if f_pts == lo:
        return []
    if lo_cell > after_cell:
        return [lo]
    lo_t, f_t = axis.sec(lo), axis.sec(f_pts)
    late = f_t - max(clock.cell_start(f_cell), lo_t) > 1.5 / max(ref_fps, 1e-6)
    if f_cell <= max(after_cell, lo_cell) + 1 and not late:
        return []
    return framesource.eaten(packets.between(lo_t, f_t), clock, axis, after_pts=lo - 1, before_pts=f_pts,
                             after_cell=after_cell, window=window)


def _nbytes(item) -> int:
    return getattr(item[2], "nbytes", 0) if len(item) > 2 else 0


class _Block:
    """一块的交付状态：两路各自按到达顺序追加，`done` 之后不再长；读的人按下标往后拿（`ShardedSource` 的条件变量通知）。"""

    def __init__(self, owner: "ShardedSource", k: int, kind: str, lo: int | None = None, hi: int | None = None) -> None:
        self.owner, self.k, self.kind, self.lo, self.hi = owner, k, kind, lo, hi
        self.main: list = []
        self.aux: list = []
        self.main_done = self.aux_done = False
        self.read_main = self.read_aux = False
        self.err: BaseException | None = None
        self.src = None
        self.nbytes = 0                 # 这一块还攒着没被拿走的字节
        self.waiting = 0                # 正在等这一块的消费者数（> 0 时各路往里放不受预算限制）
        self.last_main: framesource.Frame | None = None
        self.last_aux: int | None = None     # 最后推进来的辅助帧的 t_us
        self.throttled = False          # 有一路正被字节预算挡着（它不前进是在等下游，不算卡住）
        w, axis = owner.window, owner.axis
        # 这一块主流最后可能交出的桶：块终点（下一个关键帧）前一刻所在的桶，或窗口的最后一个桶
        self.max_cell = (w.clock.cell(hi - 1, axis) if hi is not None else (w.b - 1 if w.b is not None else None))

    # `AuxReader` 的 sink 接口：辅助流一帧一帧推进来
    def push(self, key, frame, cells) -> None:
        o = self.owner
        with o._cv:
            o._admit(self, getattr(frame, "nbytes", 0), aux=True)
            self.aux.append((key, frame, cells))
            self.last_aux = key
            o._cv.notify_all()

    def close(self) -> None:
        with self.owner._cv:
            self.aux_done = True
            self.owner._cv.notify_all()


class ShardedSource:
    """和 `FfmpegSource` 同接口的取帧层：`__iter__` 按时间顺序吐 `Frame`，结束后 `stopped_early` / `advanced_us` /
    `cells_skipped` / `stderr_tail` 有效。`aux_remote`（回抠进程的 `url` + `pts_put`）给了就按块序转发辅助流。
    `blocks`：块表（列表），或边探边给的可迭代对象（`BlockPlan`，有 `close` 的话收摊时调）。"""

    def __init__(self, video: str, *, lanes: list[str], blocks: Iterable[Block], stream: framesource.StreamInfo,
                 window: Window, width: int, height: int, pix: str = "nv12", hwaccel: str | None = None,
                 aux_remote=None, aux_clock: list[int] | None = None, aux_scale: float = 0.35, ahead: int | None = None,
                 budget_mb: float = BUDGET_MB, packets: framesource.PacketIndex | None = None) -> None:
        if not lanes or any(k not in ("hw", "sw") for k in lanes):
            raise ValueError(f"解码分片的路只认 hw / sw：{lanes}")
        if "hw" in lanes and not hwaccel:
            raise ValueError("有 hw 路却没给 hwaccel（没 GPU 时调用方要把各路换成 sw）")
        if aux_remote is not None and not aux_clock:
            raise ValueError("转发辅助流要给辅助流的时间桶（aux_clock）")
        self.video, self.lanes, self.stream, self.window = video, lanes, stream, window
        self.axis = stream.axis
        self.W, self.H, self.pix, self.hw = width, height, pix, hwaccel
        self.aux_clock = aux_clock
        self.aux_remote, self.aux_scale = aux_remote, aux_scale
        self.ahead = max(1, ahead or len(lanes) + 1)
        self.budget = int(budget_mb * (1 << 20))
        self.hwaccel = hwaccel if "hw" in lanes else None
        self.packets = packets or framesource.PacketIndex(video, self.axis)    # 各路和块尾核对共用一份（按段缓存）
        self.preroll_ticks = 0
        self.aux = None                                  # 辅助流不挂在这边（转发给回抠进程）
        self.stopped_early = False
        self.advanced_us = 0
        self.cells_skipped = 0
        self.cell_first: int | None = None               # 首末采样帧的桶号（`--workers` 拼缝核对）
        self.cell_last: int | None = None
        self.seam_dropped = 0                            # 接缝去重丢掉的采样帧（报表用）
        self.stderr_tail: list[str] = []
        self.lane_blocks = [0] * len(lanes)
        self.lane_sec = [0.0] * len(lanes)
        self.peak_mb = 0.0                               # 缓冲的峰值（报表用）
        self.probe_unknown = 0                           # 软解路块尾探不出来、放行了的次数
        self._cv = threading.Condition()
        self._close_lock = threading.Lock()
        if isinstance(blocks, list):
            self.blocks, self._plan, self._planned = list(blocks), None, True
        else:
            self.blocks, self._plan, self._planned = [], blocks, False
        self._blk: dict[int, _Block] = {}                # 块号 -> 交付状态（领到时建，两路都读完时删）
        self._next = 0
        self._sem = threading.Semaphore(self.ahead)
        self._closed = False
        self._fail: BaseException | None = None          # 共享的失败状态（转发 / 规划的错；块自己的错在 `_Block.err`）
        self._buffered = 0
        self._live: dict[int, framesource.FfmpegSource] = {}
        self._aux_writer = None                         # 登记在 open / 网络 IO 之前，取消可立即找到它
        self._threads: list[threading.Thread] = []
        self._cleanup_errors: list[str] = []

    @property
    def plan_error(self) -> str:
        return getattr(self._plan, "error", "")

    def describe(self) -> str:
        n = f"{len(self.blocks)} 块" if self._planned else "边探关键帧边切块"
        return (f"解码分片：{n}、{len(self.lanes)} 路（{'/'.join(self.lanes)}）、最多 {self.ahead} 块在途、"
                f"预取封顶 {self.budget >> 20} MB、边解边交" + ("、辅助流按块序转发给回抠进程" if self.aux_remote is not None else ""))

    # ---------- 共享状态（调用方持锁的标 `# 持锁`） ----------
    def _block_ready(self, k: int) -> bool:
        """块 k 规划出来了没有（持锁；等规划线程）。规划完了还没有 / 收摊 / 已失败 = False。"""
        while k >= len(self.blocks) and not self._planned:
            if self._closed or self._fail is not None:
                return False
            self._cv.wait(0.5)
        return k < len(self.blocks) and not self._closed and self._fail is None

    def _admit(self, b: _Block, n: int, aux: bool = False) -> None:
        """往块 b 里放 n 字节之前（持锁）：没人在等 b、而总缓冲会过预算，就等。
        **主流已经交到这块最后可能的那个桶**时不挡（2026-09-24 审核第三轮）：块尾这几帧辅助帧（不到一个采样间隔）不是预取，
        挡着它们只会让 ffmpeg 卡在写上。"""
        def main_eof() -> bool:                     # 每轮重算：等的时候主流可能正好交到块尾（审计 2026-09-25，Codex P1）
            return b.last_main is not None and b.max_cell is not None and b.last_main.cell >= b.max_cell
        def dead() -> bool:                         # 这一块已失败，或辅助读端已被取消：不再等预算，让写入方退出
            return b.err is not None or (aux and b.aux_done)
        while (self._buffered + n > self.budget and b.waiting == 0 and not main_eof()
               and not self._closed and self._fail is None and not dead()):
            b.throttled = True
            self._cv.wait(0.5)
        b.throttled = False
        if self._closed or self._fail is not None:
            raise framesource.SinkClosed("解码分片已收摊，不再接纳像素")
        if dead():
            raise framesource.SinkClosed(f"第 {b.k} 块已失败或取消，不再接纳像素")
        self._buffered += n
        b.nbytes += n
        self.peak_mb = max(self.peak_mb, self._buffered / (1 << 20))

    def _set_fail(self, exc: BaseException) -> None:
        with self._cv:
            if self._fail is None:
                self._fail = exc
            self._cv.notify_all()
        self._stop_live()

    def _abort_aux(self) -> None:
        with self._cv:
            writer = self._aux_writer
        target = writer if writer is not None else self.aux_remote
        if target is not None:
            target.abort()

    def _stop_live(self) -> None:
        try:
            self._abort_aux()
        except BaseException as exc:                    # 仍须取消所有源；错误由主路 / close 交回调用方
            self._remember_cleanup(f"辅助写端取消失败：{exc!r}")
        with self._cv:
            sources = list(self._live.values())
        for src in sources:
            try:
                src.cancel()
            except BaseException as exc:
                self._remember_cleanup(f"解码源取消失败：{exc!r}")

    def _remember_cleanup(self, message: str) -> None:
        """清理错误只登记、由 close() 交回调用方；不升格为共享失败，免得盖掉按块序该抛的根因（例如硬解失败）。"""
        with self._cv:
            if message not in self._cleanup_errors:
                self._cleanup_errors.append(message)
            self._cv.notify_all()

    # ---------- 规划 ----------
    def _planner(self) -> None:
        """把边探边给的块表搬进 `self.blocks`，顺带核"首尾相接、盖满窗口"（不满足就是规划错，不往下解）。"""
        try:
            prev_hi, first = None, True
            for lo, hi in self._plan:                    # type: ignore[union-attr]
                if (first and lo is not None) or (not first and (lo is None or lo != prev_hi)) or \
                        (lo is not None and hi is not None and hi <= lo):
                    raise RuntimeError(f"块表不连续：上一块到 {prev_hi}，这一块 [{lo}, {hi})")
                if not first and prev_hi is None:
                    raise RuntimeError(f"块表在窗口尾之后还有块：[{lo}, {hi})")
                prev_hi, first = hi, False
                with self._cv:
                    if self._closed or self._fail is not None:
                        return
                    self.blocks.append((lo, hi))
                    self._cv.notify_all()
            if first or prev_hi is not None:
                raise RuntimeError(f"块表没盖到窗口尾（最后一块到 pts {prev_hi}）")
        except BaseException as exc:                    # noqa: BLE001
            self._set_fail(RuntimeError(f"解码分片的块规划失败：{exc!r}"))
        finally:
            with self._cv:
                self._planned = True
                self._cv.notify_all()

    # ---------- 各路 ----------
    def _lane(self, i: int, kind: str) -> None:
        while True:
            self._sem.acquire()
            with self._cv:
                k = self._next
                self._next += 1                          # 先占号再等规划：几路不会领到同一块
                if not self._block_ready(k):
                    self._sem.release()
                    return
                lo, hi = self.blocks[k]
                b = self._blk[k] = _Block(self, k, kind, lo, hi)
                if self.aux_remote is None:
                    b.aux_done = b.read_aux = True
                self._cv.notify_all()
            t0 = time.perf_counter()
            try:
                src = framesource.FfmpegSource(
                    self.video, stream=self.stream, window=self.window, width=self.W, height=self.H, pix=self.pix,
                    hwaccel=self.hw if kind == "hw" else None, block=(lo, hi),
                    preroll_ticks=None if lo is None else 0, packets=self.packets,
                    aux=({"sink": b, "clock": self.aux_clock, "scale": self.aux_scale}
                         if self.aux_remote is not None else None))
                b.src = src
                with self._cv:                           # 登记与取消共享锁；cancel 还阻止登记后才启动的 ffmpeg
                    cancelled = self._closed or self._fail is not None
                    if not cancelled:
                        self._live[i] = src
                if cancelled:
                    src.cancel()
                    raise RuntimeError(f"第 {k} 块没开解：已经收摊")
                for item in src:
                    with self._cv:
                        self._admit(b, _nbytes(item))
                        b.main.append(item)
                        b.last_main = item
                        self._cv.notify_all()
                if self.aux_remote is not None and src.aux is not None:
                    self._join_aux(k, b, src.aux)
                    if src.aux.refused:
                        raise RuntimeError(f"第 {k} 块的辅助流写入被拒：{src.aux.refused}")
                    if src.aux.misaligned:
                        raise RuntimeError(f"第 {k} 块的辅助流 pts 对不上：{src.aux.misaligned}")
                    if b.last_main is not None and (b.last_aux is None or b.last_aux < b.last_main.key):
                        # 同一个 ffmpeg 出两路、采样帧一定在辅助流里：辅助流至少要盖到这一块最后一个采样帧
                        raise RuntimeError(f"第 {k} 块的辅助流只到 {b.last_aux} µs、采样流到了 {b.last_main.key} µs（辅助流半路断了）")
            except BaseException as exc:                # noqa: BLE001  交给读的人按块序抛
                with self._cv:                          # 叫醒被预算挡在 _admit 里的辅助读线程，下面的 cancel 才 join 得到它
                    b.err = exc
                    self._cv.notify_all()
            finally:
                try:
                    if b.err is not None and b.src is not None:
                        b.src.cancel()
                except BaseException as exc:
                    self._remember_cleanup(f"第 {k} 块失败后的清理失败：{exc!r}")
                finally:
                    with self._cv:
                        self._live.pop(i, None)
            dt = _tl.span("shard_block", t0, a=k) - t0
            with self._cv:
                self.lane_blocks[i] += 1
                self.lane_sec[i] += dt
                b.main_done = True
                if b.err is not None:
                    b.aux_done = True
                self._cv.notify_all()

    STALL_SEC = 60.0

    def _join_aux(self, k: int, b: _Block, aux) -> None:
        """等这一块的辅助流收完（ffmpeg 这时已经退了，只剩 socket 里的）。**不拿固定超时当正确性信号**（2026-09-24 审核）：
        被字节预算挡着是在等下游（回抠进程 / 管线慢，CPU 冒烟时一块能等几分钟），只有**不被挡、又 STALL_SEC 秒没前进**才算卡住。"""
        last, t_last = b.last_aux, time.perf_counter()
        while aux.th.is_alive():
            aux.join(1.0)
            if self._closed or self._fail is not None:
                return
            now = time.perf_counter()
            if b.last_aux != last or b.throttled:
                last, t_last = b.last_aux, now
            elif now - t_last > self.STALL_SEC:
                raise RuntimeError(f"第 {k} 块的辅助流 {self.STALL_SEC:.0f} s 没前进（ffmpeg 已退，停在 {b.last_aux} µs）")

    def _items(self, k: int, which: str):
        """块 k 的一路（`main` / `aux`）按到达顺序往外拿，拿过的位置置空（不留内存）；块出错 / 共享失败就抛。"""
        t0 = time.perf_counter()
        with self._cv:
            while k not in self._blk:
                if self._fail is not None:
                    raise self._fail
                if self._closed:
                    return
                self._cv.wait(0.5)
            b = self._blk[k]
        _tl.span("shard_wait", t0, a=k)
        seq = b.main if which == "main" else b.aux
        j = 0
        while True:
            with self._cv:
                while True:
                    if self._fail is not None:           # 先报根因：共享失败会停掉在解的 ffmpeg，那几块跟着出的错是次生的
                        raise self._fail
                    if self._closed:
                        return
                    if b.err is not None:
                        raise b.err
                    if j < len(seq):
                        item, seq[j] = seq[j], None
                        n = _nbytes(item) if which == "main" else getattr(item[1], "nbytes", 0)
                        self._buffered -= n
                        b.nbytes -= n
                        self._cv.notify_all()            # 缓冲降了：被预算挡着的那几路可以接着放
                        break
                    if b.main_done if which == "main" else b.aux_done:
                        return
                    b.waiting += 1
                    self._cv.notify_all()           # 被预算挡着的那一路：有人在等你了（不等它 0.5 s 轮询）
                    self._cv.wait(0.5)
                    b.waiting -= 1
            j += 1
            yield item

    def _release(self, k: int, which: str) -> None:
        """块 k 的一路读完了；两路都读完就把块删掉、放出一个名额给下一块。"""
        with self._cv:
            b = self._blk.get(k)
            if b is None:
                return
            if which == "main":
                b.read_main = True
            else:
                b.read_aux = True
            if b.read_main and b.read_aux:
                self._buffered -= b.nbytes
                del self._blk[k]
                self._sem.release()
                self._cv.notify_all()

    # ---------- 辅助流转发（按块序） ----------
    def _forward(self) -> None:
        n, k = 0, 0
        w = None
        drained = False
        prev_cells, prev_key = None, None
        try:
            try:
                w = self.aux_remote if hasattr(self.aux_remote, "open") else TcpAux(self.aux_remote)
                with self._cv:
                    self._aux_writer = w
                    cancelled = self._closed or self._fail is not None
                if cancelled:
                    return
                w.open()
            except OSError as exc:                       # 含连接中途被 abort（`OwnedSocket.connect` 转成 OSError）
                with self._cv:
                    cancelled = self._closed or self._fail is not None
                if not cancelled:
                    self._set_fail(RuntimeError(f"辅助流转发连不上回抠进程：{exc!r}"))
                return
            while True:
                with self._cv:
                    if not self._block_ready(k):
                        drained = not self._closed and self._fail is None and self._planned and k >= len(self.blocks)
                        return
                it = self._items(k, "aux")
                while True:
                    try:
                        key, frame, cells = next(it)
                    except StopIteration:
                        break
                    except BaseException:               # noqa: BLE001  块自己的错 / 已经失败：主路按块序抛同一个
                        return
                    if seam_drop(prev_cells, cells):     # 接缝去重：同一辅助桶且同一采样桶的帧前面的块已经交过
                        continue
                    if prev_key is not None and key <= prev_key:
                        self._set_fail(RuntimeError(f"辅助流合流的键不递增：第 {k} 块的 {key} µs 不大于上一帧 {prev_key} µs（块边界重叠）"))
                        return
                    try:
                        w.send(frame, n, (key, *cells))
                    except BaseException as exc:        # noqa: BLE001  转发自己的错：主路不知道，必须进共享失败状态
                        self._set_fail(RuntimeError(f"辅助流转发失败（第 {k} 块、第 {n} 帧）：{exc!r}"))
                        return
                    prev_cells, prev_key = cells, key
                    n += 1
                self._release(k, "aux")
                k += 1
        finally:
            if w is not None:
                try:
                    if drained:
                        w.close()
                    else:
                        w.abort()
                except BaseException as exc:            # noqa: BLE001  正常尾标记失败也必须拒绝完成
                    self._set_fail(RuntimeError(f"辅助流结束交付失败：{exc!r}"))

    # ---------- 块尾核对 ----------
    def _check_tail(self, k: int, last: framesource.Frame | None) -> None:
        """块 k 的"合流里最后一个交出的帧 -> 块终点"之间（整块没交就是整块）有没有该交没交的帧：源里桶号在 `last` 那一格之后、
        pts 在块里的包。探测区间截在块终点（下一个关键帧）；块自己核过它交出的那几帧之间，这里不重核。"""
        lo, hi = self.blocks[k]
        b = self._blk[k]
        w, axis, clock = self.window, self.axis, self.window.clock
        after_cell = last.cell if last is not None else w.a - 1
        after_pts = last.pts if last is not None else None
        if lo is not None and (after_pts is None or after_pts < lo):
            after_pts = lo - 1
        t0 = axis.sec(after_pts) if after_pts is not None else w.seek_sec()
        t1 = axis.sec(hi) if hi is not None else w.end_sec
        if t1 is not None and clock.cell_start(after_cell + 1) >= t1:
            return                                       # 块终点之前没有新桶开始，没有可漏的
        got = framesource.eaten(self.packets.between(t0, t1), clock, axis, after_pts=after_pts, before_pts=hi,
                                after_cell=after_cell, window=w)
        if got is None:                                  # 探不出来：硬解路保守（当吞帧、整段退软解），软解路放行 + 记一笔
            if b.kind == "sw":
                self.probe_unknown += 1
                return
            got = []
            what = f"第 {k} 块（hw）块尾探不出源里有没有帧（按吞帧算）"
        else:
            if not got:
                return
            what = (f"第 {k} 块（{b.kind}）源里有、却没交付的帧 {len(got)} 个（首个 pts {got[0]} = {axis.sec(got[0]):.6f} s；"
                    f"合流交到 {last.key if last else '—'} µs，块是 pts [{lo}, {hi})）")
        if b.kind == "hw":
            raise framesource.HwaccelMismatch(f"解码分片：{what}")
        raise RuntimeError(f"解码分片：{what}。ffmpeg 最后几行：{list(b.src.stderr_tail) if b.src else []}")

    # ---------- 消费者 ----------
    def __iter__(self):
        ths = [threading.Thread(target=self._lane, args=(i, k), daemon=True, name=f"shard-{i}-{k}")
               for i, k in enumerate(self.lanes)]
        if self._plan is not None:
            ths.append(threading.Thread(target=self._planner, daemon=True, name="shard-plan"))
        fwd = None
        if self.aux_remote is not None:
            fwd = threading.Thread(target=self._forward, daemon=True, name="shard-aux-fwd")
            ths.append(fwd)
        with self._cv:
            if self._closed:
                return
            self._threads = ths
            for th in ths:
                th.start()
        w, axis = self.window, self.axis
        last: framesource.Frame | None = None            # 合流里上一个交出的帧
        try:
            k = 0
            while True:
                with self._cv:
                    if not self._block_ready(k):
                        break
                lo, _hi = self.blocks[k]
                first = True
                for f in self._items(k, "main"):
                    if last is not None and seam_drop(last.cell, f.cell):
                        self.seam_dropped += 1           # 接缝去重：这一格的第一帧前面的块已经交过
                        first = False
                        continue
                    if last is not None and (f.key <= last.key or f.cell < last.cell):
                        raise RuntimeError(f"解码分片合流的键不递增：第 {k} 块的 {f.key} µs（桶 {f.cell}）不在上一帧 "
                                           f"{last.key} µs（桶 {last.cell}）之后（块边界重叠）")
                    if first and lo is not None and self._blk[k].kind == "hw":
                        bad = block_first_eaten(w, axis, lo, f.pts, f.cell, last.cell if last else w.a - 1,
                                                self.stream.ref_fps, self.packets)
                        if bad is None or bad:
                            what = ("探不出源里有没有帧（按吞帧算）" if bad is None else
                                    f"源里有、却没交付的帧 {len(bad)} 个（首个 pts {bad[0]}；关键帧 pts {lo}）")
                            raise framesource.HwaccelMismatch(f"解码分片：第 {k} 块（hw）交出的第一帧 {f.key} µs 之前{what}")
                    first = False
                    self.cells_skipped += f.cell - (last.cell if last is not None else w.a - 1) - 1
                    last = f
                    self.advanced_us = w.span_us(f.cell + 1)
                    if self.cell_first is None:
                        self.cell_first = f.cell
                    self.cell_last = f.cell
                    yield f
                with self._cv:
                    if self._fail is not None:
                        raise self._fail
                    if self._closed:
                        break
                    is_last = not self._block_ready(k + 1)
                    if self._fail is not None:
                        raise self._fail
                self._check_tail(k, last)
                src = self._blk[k].src
                if is_last:                                  # 同单路：读到头之前断了
                    self.stopped_early = src.stopped_early
                self.stderr_tail = (self.stderr_tail + list(src.stderr_tail))[-8:]
                if k == 0:
                    self.preroll_ticks = src.preroll_ticks
                self._release(k, "main")
                k += 1
            with self._cv:
                if self._fail is not None:
                    raise self._fail
            if fwd is not None:
                # 辅助流转发完再收摊（回抠进程要读到 EOF 和 pts 的结束标记）；转发线程自己出错会进共享失败状态
                while fwd.is_alive():
                    fwd.join(1.0)
                if self._fail is not None:
                    raise self._fail
        except BaseException:
            # 已有异常在传播（含消费者提前 close 的 GeneratorExit）：只收摊，清理错误留给调用方之后的 close() 报，
            # 不能拿它替换原异常——硬解失败要原样交到 decode_proc，才会发 "hw" 走软解重跑
            with self._close_lock:
                self._close()
            raise
        self.close()

    def close(self) -> None:
        """收摊：不再领新块，正在解的那几块的 ffmpeg 收掉，边探的 ffprobe 停掉。"""
        # recv 与主路可以同时取消；后来的 close 必须等前一次清理登记完错误，
        # 不能仅凭源已从 _live 移除就宣告成功。工作线程不调用 close。
        with self._close_lock:
            self._close()
        if self._cleanup_errors:
            raise RuntimeError("解码分片清理失败：" + "；".join(self._cleanup_errors)) from self._fail

    def _close(self) -> None:
        with self._cv:
            self._closed = True
            for b in self._blk.values():
                b.main.clear()
                b.aux.clear()
                b.nbytes = 0
            self._buffered = 0
            self._cv.notify_all()
        self._stop_live()
        closer = getattr(self._plan, "close", None)
        if closer is not None:
            try:
                closer()
            except BaseException as exc:
                self._remember_cleanup(f"块规划器取消失败：{exc!r}")
        for _ in range(len(self.lanes) + 1):
            self._sem.release()
        deadline = time.monotonic() + 10
        for th in self._threads:
            if th is not threading.current_thread():
                th.join(max(0, deadline - time.monotonic()))
        if any(th.is_alive() and th is not threading.current_thread() for th in self._threads):
            self._remember_cleanup("解码分片取消后仍有工作线程未退出")
