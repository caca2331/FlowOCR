"""守卫共用的工具：路径、`check` / `raises`、合成样本。各领域模块 `from guards._common import *`。"""
from __future__ import annotations

import importlib
import subprocess
import sys
import inspect
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev_tools"))
sys.path.insert(0, str(ROOT / "src"))     # 正式包（tools/ 里的 shim 自己也会把它放进来；这里显式给守卫用）

from flowocr.artifacts import evalkit  # noqa: E402
from flowocr.artifacts import srtio  # noqa: E402

FAILED: list[str] = []


def high_quality_default(fn):
    """装饰守卫：执行期间不给 `--quality` 按 `high` 解析。这些守卫造的假产物是辅助流 60 fps 的旧默认产物（精度档位之前），
    测的是复用判据的**别的维度**（参数、窗口、采样网格、范围）；2026-10-05 默认翻成 `default` 档（辅助流 30）之后，
    不钉的话它们全部因为辅助流网格对不上而判重建，测不到原来要测的东西。档位本身的复用口径在 `t_quality_tiers`。
    只对同一进程内的解析有效：守卫里起的子进程不受它影响，照样按 `default` 档。"""
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        from flowocr.extract import ocr_args
        old = ocr_args.DEFAULT_QUALITY
        ocr_args.DEFAULT_QUALITY = "high"
        try:
            return fn(*a, **k)
        finally:
            ocr_args.DEFAULT_QUALITY = old
    return run


FIXED_PAD = 14


def fixed_pad(fn):
    """装饰守卫：执行期间底板边距钉成 `FIXED_PAD` 像素、不随字号变（`layout.plate_pad`）。这些守卫测的是底板的几何
    （按哪些框、怎么劈开、跟着字长），期望值按一个常数边距写；边距随字号变的口径在 `t_plate_pad`。"""
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        from flowocr.output import layout
        old = layout.PLATE_PAD_PX, layout.PLATE_PAD_EM
        layout.PLATE_PAD_PX, layout.PLATE_PAD_EM = FIXED_PAD, 0.0
        try:
            return fn(*a, **k)
        finally:
            layout.PLATE_PAD_PX, layout.PLATE_PAD_EM = old
    return run


def raises(fn, exc: type[BaseException] = Exception) -> bool:
    """`fn()` 抛不抛。**"守卫真的会响"和"正常路径没坏"是两件事**，两边都要测。"""
    try:
        fn()
    except exc:
        return True
    return False


def fake_stream(fps: int = 60, duration: float | None = None, width: int = 4, height: int = 4):
    """假视频流的元数据：时间基 1/fps（整数 pts 就是帧序号）、名义 = 平均帧率 = fps。"""
    from fractions import Fraction

    from flowocr.extract.framesource import StreamInfo
    return StreamInfo(tb=(1, fps), nominal=Fraction(fps), avg=Fraction(fps), duration=duration, width=width, height=height)


def window2(a: int = 0, b: int | None = None, fps: float = 2):
    """采样 `fps`（默认 2）的窗口：采样桶号 `[a, b)`（`framegrid.Window`）。"""
    from flowocr.extract.framegrid import SampleClock, Window
    return Window(SampleClock(fps), a, b)


def fake_frame(pts: int, cell: int, fps: int = 60, image=None):
    """取帧层交的一帧（`framesource.Frame`）：时间基 1/fps 上的整数 pts。"""
    from flowocr.extract.framegrid import frame_key
    from flowocr.extract.framesource import Frame
    t = pts / fps
    return Frame(frame_key(t), t, image, cell, pts)


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def fake_widths(pairs, known=None):
    """守卫里不起 ffmpeg 量字宽：每个字宽 1（字号 1 时）。"""
    return {**(known or {}), **{(f, ln): 1.0 * len(ln) for f, t in pairs for ln in t.split("\n")}}


def render_overlay(path: Path, items, doc: dict, look: str = "dev", typewriter: bool = True, fade: bool = True,
                   quiet: bool = True, **look_changes):
    """叠加两步走一遍：阶段 3 写字幕稿（`path` 旁边的 `.script.ass`），阶段 4 生成 `path`。返回阶段 3 的计数。
    `look_changes` 覆盖阶段 4 预设的字段（比如 `label=False`）；底板取色默认关（要读视频）。字宽用 `fake_widths`。"""
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr.output import export, script
    from flowocr.typeset import assfile, core
    path = Path(path)
    draft = path.with_name(path.stem + ".script.ass")
    with patch.object(export, "ink_widths", fake_widths):
        n, _widths = script.export_script(draft, items, doc, typewriter, fade)
        ts = core.Typesetter(replace(core.LOOKS[look], **{"plate_color": "off", **look_changes}))   # 底板取色要视频，另有守卫
        d = assfile.loads(draft.read_text(encoding="utf-8"))
        ts.run(d)
    path.write_text(d.dumps(), encoding="utf-8")
    return n


SRT_OK = """1
00:00:01,000 --> 00:00:02,500
第一条
第二行

2
00:00:03,000 --> 00:00:04,000
3

"""


def _shard_behaviour_checks(dsh, fsx) -> None:
    """`ShardedSource` 的**行为**（2026-09-24 两份审计：原来只测了三条 ValueError，而它是默认开的路径）。
    假 `FfmpegSource`：60 fps、时间基 1/60（整数 pts 就是帧序号），采样 2 fps（采样桶 = pts // 30）；按块交 `[lo, hi)` 里每个新桶的第一帧
    （块首帧看不到上一块，按新桶交——接缝去重靠合流），源缺的 `missing` 没有、解码器吞的 `eaten` 不交；辅助流逐帧推进 sink（辅助桶 60 fps）；
    源里有没有包问假的 `PacketIndex`（"源里有 = 不在 missing"）。窗口 4 块 × 60 帧。"""
    import threading as _th
    import time
    from types import SimpleNamespace as NS
    from unittest import mock

    import numpy as np

    from flowocr.extract.framegrid import frame_key

    def key(i):
        return frame_key(i / 60)

    class Fake:
        missing: set = set()
        eaten: set = set()
        aux_cut: dict = {}          # 块起点 -> 辅助流推到哪一帧为止（模拟半路断）
        fail_at: dict = {}          # 块起点 -> 交到这一帧时抛的异常（模拟块内硬解失败）
        lead: dict = {}             # 块起点 -> 块首多交的一帧（模拟和上一块同桶的块首帧，接缝去重）
        cancel_fails = False        # 失败块的 cancel 自己也出错（模拟清理超时）
        delay = 0.0

        def __init__(self, video, **kw):
            self.kw = kw
            self.stopped_early, self.stderr_tail, self.preroll_ticks = False, [], 0
            self.aux = NS(join=lambda t=None: None, th=NS(is_alive=lambda: False), misaligned="", refused="") if kw["aux"] else None

        def __iter__(self):
            w = self.kw["window"]
            lo, hi = self.kw["block"]
            lo = w.a * 30 if lo is None else lo
            hi = (w.b * 30 if w.b is not None else 1 << 30) if hi is None else hi
            sink = self.kw["aux"]["sink"] if self.kw["aux"] else None
            cut = Fake.aux_cut.get(lo, hi)
            prev = None
            for i in range(lo, hi):
                if i in Fake.missing:
                    continue
                if sink is not None and i < cut:
                    sink.push(key(i), np.zeros(4, np.uint8), (i // 30, i))
                if i - lo == 30 and lo in Fake.fail_at:
                    raise Fake.fail_at[lo]
                if i in Fake.eaten:
                    continue
                if i // 30 != prev:
                    prev = i // 30
                    if Fake.delay:
                        time.sleep(Fake.delay)
                    yield fsx.Frame(key(i), i / 60, np.zeros(8, np.uint8), i // 30, i)
            if sink is not None:
                sink.close()

        def close(self):
            pass

        def cancel(self):
            if Fake.cancel_fails:
                raise RuntimeError("假的：辅助流取消后读线程未退出")

    class Packets:
        fails = False
        probes = 0

        def between(self, lo_sec, hi_sec):
            if Packets.fails:
                return None
            hi = 1 << 30 if hi_sec is None else hi_sec
            return [i for i in range(0, 4000) if i not in Fake.missing and lo_sec <= i / 60 < hi]

    class Sock:
        def __init__(self, fail_after=None):
            self.n, self.fail_after = 0, fail_after

        def settimeout(self, t):
            pass

        def sendall(self, b):
            if self.fail_after is not None and self.n >= self.fail_after:
                raise ConnectionResetError("假的：回抠进程断了")
            self.n += 1

        def close(self):
            pass

    def blocks_of(n):
        return [(None if k == 0 else k * 60, None if k == n - 1 else (k + 1) * 60) for k in range(n)]

    def run(nblocks=4, lanes=("hw", "hw", "hw"), aux=True, sock=None, conn_err=None, budget_mb=1024, blocks=None,
            timeout=10.0):
        """跑一遍，返回 (交出的 pts, 转发的 pts 项, 源, 抛的异常, 卡没卡住)。"""
        pts: list = []
        bl = blocks if blocks is not None else blocks_of(nblocks)
        remote = NS(url="tcp://127.0.0.1:1", pts_put=pts.append, abort=lambda: None) if aux else None
        s = dsh.ShardedSource("v", lanes=list(lanes), blocks=bl, stream=fake_stream(), window=window2(0, nblocks * 2),
                              width=4, height=4, hwaccel="cuda", aux_remote=remote, aux_clock=[60, 1, 2, 1],
                              budget_mb=budget_mb, packets=Packets())
        out, err = [], []

        def consume():
            try:
                for f in s:
                    out.append(f.pts)
            except BaseException as exc:          # noqa: BLE001
                err.append(exc)

        class Writer:
            def __init__(self, remote):
                self.remote = remote
                self.sock = sock or Sock()
            def open(self):
                if conn_err is not None:
                    raise conn_err
                return self
            def send(self, frame, n, info):
                self.sock.sendall(frame)
                self.remote.pts_put((n, *info))
            def close(self):
                self.remote.pts_put(dsh.framesource.QUEUE_SENTINEL)
                self.sock.close()
            def abort(self):
                self.sock.close()
        cc = mock.patch.object(dsh, "TcpAux", Writer)
        with mock.patch.object(dsh.framesource, "FfmpegSource", Fake), cc:
            th = _th.Thread(target=consume, daemon=True)
            th.start()
            th.join(timeout)
        stuck = th.is_alive()
        if stuck:
            s.close()
        return out, pts, s, (err[0] if err else None), stuck

    def reset():
        Fake.missing, Fake.eaten, Fake.aux_cut, Fake.delay, Packets.fails = set(), set(), {}, 0.0, False
        Fake.fail_at, Fake.cancel_fails = {}, False

    reset()
    out, pts, s, e, stuck = run()
    keys = [p[1] for p in pts if p is not dsh.framesource.QUEUE_SENTINEL]
    check("ShardedSource 正常路径：4 块 3 路，采样帧全交（按时间顺序）、辅助流 240 帧按时间顺序转发（桶号随帧走）、末尾有 pts 结束标记；"
          "推进量 = 最后一个采样帧所在桶的结束 − 起点",
          not stuck and e is None and out == list(range(0, 240, 30)) and keys == [key(i) for i in range(240)]
          and all(len(p) == 4 for p in pts[:-1]) and pts and pts[-1] is dsh.framesource.QUEUE_SENTINEL
          and s.cells_skipped == 0 and s.advanced_us == 4_000_000 and (s.cell_first, s.cell_last) == (0, 7),
          (stuck, e, out, len(keys)))

    reset()
    Fake.eaten = set(range(0, 60))
    out, pts, s, e, stuck = run()
    check("ShardedSource：**首块整块没交**、源里有 -> 硬解那路抛 HwaccelMismatch（Codex 审计 P1：原来 complete=True、缺格 0）",
          not stuck and isinstance(e, fsx.HwaccelMismatch), (stuck, e, out))
    out, pts, s, e, stuck = run(lanes=("sw", "sw"))
    check("ShardedSource：首块没交、软解路 -> RuntimeError（不当成素材缺口放过去）",
          not stuck and isinstance(e, RuntimeError) and not isinstance(e, fsx.HwaccelMismatch), (stuck, e))
    Packets.fails = True
    out, pts, s, e, stuck = run(lanes=("sw", "sw"))
    check("ShardedSource：软解路块尾核对、**源探不出来** -> 放行、每块记一笔 probe_unknown（2026-09-24 审核：探失败 ≠ 真吞帧）",
          not stuck and e is None and s.probe_unknown == 4, (stuck, e, s.probe_unknown))
    out, pts, s, e, stuck = run()
    check("ShardedSource：硬解路块尾、源探不出来 -> 保守当吞帧（整段退软解）",
          not stuck and isinstance(e, fsx.HwaccelMismatch), (stuck, e))

    reset()
    Fake.missing = set(range(0, 60))
    out, pts, s, e, stuck = run()
    check("ShardedSource：首块整块是**素材缺口**（源里也没有）-> 放行，空桶记 2 个",
          not stuck and e is None and out == [60, 90, 120, 150, 180, 210] and s.cells_skipped == 2, (stuck, e, out, s.cells_skipped))

    reset()
    Fake.missing = set(range(150, 180))          # 第 2 块后半整个桶缺（块首的关键帧 120 在）：空桶在合流处只数一次
    out, pts, s, e, stuck = run()
    check("ShardedSource：块里的缺口空桶**只数一次**（合流处统一数；块尾核对不数）",
          not stuck and e is None and out == [0, 30, 60, 90, 120, 180, 210] and s.cells_skipped == 1, (e, out, s.cells_skipped))

    reset()
    Fake.eaten = set(range(210, 240))
    out, pts, s, e, stuck = run()
    check("ShardedSource：**末块尾**源里有却没交 -> 照样抛",
          not stuck and isinstance(e, fsx.HwaccelMismatch), (stuck, e, out))
    reset()
    Fake.missing = set(range(210, 240))
    out, pts, s, e, stuck = run()
    check("ShardedSource：末块尾是素材缺口 -> 放行、不数空桶（同单路：窗口尾归覆盖率那道门）",
          not stuck and e is None and out[-1] == 180 and s.cells_skipped == 0 and s.advanced_us == 3_500_000,
          (e, out, s.cells_skipped))

    # 接缝去重：块首帧和合流里上一帧同桶就丢（块边界不在桶边界上时，下一块的第一帧 ffmpeg 会当新桶选中）
    reset()
    out, pts, s, e, stuck = run(blocks=[(None, 45), (45, 105), (105, None)], nblocks=2)
    fwd = [p[1] for p in pts if p is not dsh.framesource.QUEUE_SENTINEL]
    check("接缝去重：块边界在桶中间（45、105）时块首帧和上一块同桶 -> 丢掉；采样帧和整片跑同一批，辅助流也不重复（按辅助桶 + 采样桶成对判）",
          not stuck and e is None and out == [0, 30, 60, 90] and s.seam_dropped == 2 and fwd == [key(i) for i in range(120)],
          (e, out, s.seam_dropped, len(fwd)))
    check("接缝去重的判据：同格才丢（采样流比桶号，辅助流比 (采样桶, 辅助桶)）；合流里还没有帧时不丢",
          dsh.seam_drop(3, 3) and not dsh.seam_drop(3, 4) and not dsh.seam_drop(None, 0)
          and dsh.seam_drop((1, 40), (1, 40)) and not dsh.seam_drop((1, 40), (1, 41)) and not dsh.seam_drop((1, 40), (2, 40)))
    reset()
    out, pts, s, e, stuck = run(blocks=[(None, 10), (10, 20), (20, None)], nblocks=1)
    check("接缝去重：一块整个落在一个桶里时去重之后一帧不交，同样正确",
          not stuck and e is None and out == [0, 30] and s.seam_dropped == 2, (e, out, s.seam_dropped))

    for nb in (3, 4):
        reset()
        out, pts, s, e, stuck = run(nblocks=nb, conn_err=OSError("假的：连不上"))
        check(f"ShardedSource：辅助流转发**连不上**（{nb} 块、3 路）-> 及时抛，不静默少辅助流、不卡住（Codex 审计 P1）",
              not stuck and isinstance(e, RuntimeError) and "连不上" in str(e), (nb, stuck, e))
        out, pts, s, e, stuck = run(nblocks=nb, sock=Sock(fail_after=70))
        check(f"ShardedSource：辅助流**写到一半断了**（{nb} 块）-> 抛 RuntimeError、不伪造正常结束标记",
              not stuck and isinstance(e, RuntimeError) and "转发失败" in str(e)
              and dsh.framesource.QUEUE_SENTINEL not in pts, (nb, stuck, e))

    reset()
    Fake.aux_cut = {60: 80}
    out, pts, s, e, stuck = run()
    check("ShardedSource：某一块的辅助流半路断了（没盖到这块最后一个采样帧）-> 这一块的错，不当成读完了",
          not stuck and isinstance(e, RuntimeError) and "半路断了" in str(e), (stuck, e))

    reset()
    Fake.delay = 0.001
    out, pts, s, e, stuck = run(nblocks=6, budget_mb=0.00002)   # 20 字节：几乎一有东西就超
    check("ShardedSource：字节预算小到几乎为 0 也不互等（有消费者在等的块永远放行），帧照样按序交全",
          not stuck and e is None and out == list(range(0, 360, 30)) and s.peak_mb * (1 << 20) < 6 * 256, (stuck, e, out, s.peak_mb))   # 全部 6 块共 1,536 字节：从没攒满过

    # 主流交到块最后可能的那个桶之后，辅助流不再按预算挡（审核第三轮：块尾辅助帧不是预取，挡着它只会让 ffmpeg 卡在写上）。
    # 直接测 `_admit`：预算 0、没人在等
    s0 = dsh.ShardedSource("v", lanes=["hw"], blocks=[(None, 60)], stream=fake_stream(), window=window2(0, 2),
                           width=4, height=4, hwaccel="cuda", budget_mb=0)
    b0 = dsh._Block(s0, 0, "hw", None, 60)
    s0._buffered = 10

    def try_admit(last_cell):
        b0.last_main = fake_frame(last_cell * 30, last_cell)
        def admit():
            with s0._cv:
                try:
                    s0._admit(b0, 5)
                except RuntimeError:
                    assert s0._closed
        th = _th.Thread(target=admit, daemon=True)
        th.start()
        th.join(1.0)
        blocked = th.is_alive()
        if blocked:
            with s0._cv:
                s0._closed = True
                s0._cv.notify_all()
            th.join(2)
            s0._closed = False
        return blocked
    check("ShardedSource._admit：主流还没到块尾时按预算挡；主流交到块里最后可能的那个桶（块终点前一刻所在的桶）之后放行",
          b0.max_cell == 1 and try_admit(0) and not try_admit(1))

    # 预取块失败 / 辅助读端被取消时，被预算挡着的辅助写入要当场退出（否则 cancel 的 join 超时，清理错误盖掉硬解失败）
    def parked_exits(kill):
        b1 = dsh._Block(s0, 1, "hw", None, 60)
        b1.last_main = fake_frame(0, 0)
        s0._buffered = 10
        res = []
        def admit():
            with s0._cv:
                try:
                    s0._admit(b1, 5, aux=True)
                    res.append("admitted")
                except RuntimeError:
                    res.append("refused")
        th = _th.Thread(target=admit, daemon=True)
        th.start()
        th.join(0.3)
        parked = th.is_alive()
        kill(b1)
        th.join(1.0)
        return parked and not th.is_alive() and res == ["refused"]

    def fail_block(b1):
        with s0._cv:
            b1.err = RuntimeError("假的：块失败")
            s0._cv.notify_all()
    check("ShardedSource._admit：被预算挡着的辅助帧在块失败或辅助读端取消时当场退出，不等预算",
          parked_exits(fail_block) and parked_exits(lambda b1: b1.close()))

    # 契约（效率分支复审）：**真实的** `FfmpegSource.cancel -> AuxReader.abort -> _Block.close` 叫得醒被预算挡在 `_admit` 里的辅助读线程。
    # 替身只有 ffmpeg 那一头（本机 socket 往 AuxReader 写像素 + 它的 pts 项）和 FfmpegSource 的进程状态，不用 GPU、不起 ffmpeg
    import socket as _so
    from queue import Queue as _Q

    from flowocr.extract import framesource as _fsr
    b2 = dsh._Block(s0, 2, "hw", None, 60)
    b2.last_main = fake_frame(0, 0)
    s0._buffered = 10
    _pq = _Q()
    for _k in range(8):
        _pq.put((_k, key(_k), 0, _k))
    rd = _fsr.AuxReader(width=4, height=4, sink=b2, pts_q=_pq).start()
    feed = _so.create_connection(("127.0.0.1", int(rd.url.rsplit(":", 1)[1])))
    feed.sendall(bytes(16 * 8))
    t_park = time.monotonic()
    while not b2.throttled and time.monotonic() - t_park < 3:
        time.sleep(0.01)
    parked2 = b2.throttled and rd.th.is_alive()
    src_ = NS(_proc_lock=_th.Lock(), _cancelled=False, proc=None, aux=rd, close=lambda: None)
    t_cancel = time.monotonic()
    try:
        _fsr.FfmpegSource.cancel(src_)
        cancel_err = None
    except RuntimeError as exc:
        cancel_err = exc
    dt_cancel = time.monotonic() - t_cancel
    feed.close()
    check("契约：真实 FfmpegSource.cancel -> AuxReader.abort -> _Block.close 在 5 s 内叫醒被预算挡在 _admit 里的辅助读线程；"
          "拒收记进 `refused`、不记成 pts 对不上",
          parked2 and cancel_err is None and not rd.th.is_alive() and dt_cancel < 5
          and rd.refused and not rd.misaligned and rd.sent == 0,
          (parked2, cancel_err, rd.th.is_alive(), round(dt_cancel, 2), rd.refused, rd.misaligned, rd.sent))

    reset()
    Fake.fail_at = {120: dsh.framesource.HwaccelMismatch("假的：第 2 块中途硬解交付错")}
    Fake.cancel_fails = True
    out, pts, s, e, stuck = run()
    try:
        s.close()
        later = None
    except RuntimeError as exc:
        later = exc
    # 第 2 块先交帧 120、交到帧 150 才抛。消费者赶在抛之前读到第 2 块就会先拿到 120（机器忙时会这样），同单路 FfmpegSource
    # 交过几帧再抛 HwaccelMismatch：worker 退出、整段软解重跑，已交的帧作废。所以按块序 = 第 0、1 块交全、第 2 块只交失败点之前的帧
    check("ShardedSource：预取块硬解失败且清理也出错 -> 消费者仍按块序拿到 HwaccelMismatch（才会走软解重跑），清理错误由之后的 close() 报",
          not stuck and isinstance(e, dsh.framesource.HwaccelMismatch)
          and out[:4] == list(range(0, 120, 30)) and out[4:] in ([], [120])
          and later is not None and "清理失败" in str(later), (stuck, e, out, later))

    reset()

    def slow_plan():
        for blk in blocks_of(4):
            time.sleep(0.01)
            yield blk
    out, pts, s, e, stuck = run(blocks=slow_plan())
    check("ShardedSource：块表边探边给（可迭代对象 + 规划线程）-> 结果同整张表",
          not stuck and e is None and out == list(range(0, 240, 30)) and len(s.blocks) == 4, (stuck, e, out))
    out, pts, s, e, stuck = run(blocks=iter([(None, 60), (90, None)]))
    check("ShardedSource：块表不连续 -> 规划失败、抛出来（不往下解）",
          not stuck and isinstance(e, RuntimeError) and "不连续" in str(e), (stuck, e))
    reset()

    # 硬解路每块交出的第一帧（owner 2026-11-23）：块里的 ① ② 看不到它真正的上一帧，合流处补一道（纯函数）
    from flowocr.extract.framegrid import Axis
    w = window2(0, 8)
    TB = Axis((1, 60))
    pk = Packets()
    check("block_first_eaten：关键帧是新桶、交出的第一帧却不是关键帧 -> 关键帧被吞（NVDEC 少交块头）；关键帧就是第一帧 -> 没事",
          dsh.block_first_eaten(w, TB, 120, 121, 4, 3, 60.0, pk) == [120]
          and dsh.block_first_eaten(w, TB, 120, 120, 4, 3, 60.0, pk) == [])
    check("block_first_eaten：关键帧和上一帧同桶（它不该交）、第一帧是下一桶的第一帧 -> 没事；下一桶的第一帧被吞、交出的晚了 -> 问源、抛",
          dsh.block_first_eaten(w, TB, 100, 120, 4, 3, 60.0, pk) == []
          and dsh.block_first_eaten(w, TB, 100, 150, 5, 3, 60.0, pk) == list(range(120, 150)))
    Fake.missing = set(range(120, 150))
    check("block_first_eaten：隔开的那个桶是素材缺口（源里没有）-> 放行；探不出来 -> None（硬解路按吞帧算）",
          dsh.block_first_eaten(w, TB, 100, 150, 5, 3, 60.0, pk) == [])
    Packets.fails = True
    check("block_first_eaten：探不出来返回 None", dsh.block_first_eaten(w, TB, 100, 150, 5, 3, 60.0, pk) is None)
    reset()
    Fake.eaten = {120}
    out, pts, s, e, stuck = run()
    check("ShardedSource：硬解路块头的关键帧被吞（同桶下一帧顶上、只晚一帧）-> 合流处抛 HwaccelMismatch",
          not stuck and isinstance(e, fsx.HwaccelMismatch) and "第一帧" in str(e), (stuck, e, out))
    out, pts, s, e, stuck = run(lanes=("sw", "sw"))
    check("同样的形状在软解路上不加这道（软解不吞帧），照常交出", not stuck and e is None and 121 in out, (e, out))
    reset()


COMMON = "ありがとうございました本当に"
"""合成语料里的常用句：放进 5 个单元（> max_units 3），查询撞上它不许拿来圈单元。"""


def synthetic_gamescript(d: Path) -> dict:
    """检索层的合成语料（audit-6 §3：守卫原来唯独没盖检索层，而分母就是那一层定的）。

    A：一段带分支的对话——a1 / a2 是两个选项之后的回复，obs 只读到 a1；a4 在最后一个锚点之后；
       中间夹一条常用句。B：一段 obs 从没读到的对话。C：绝区零式的变体组（c0 / c1 两种说法，
       obs 读到的那一截同时包含于两行）；c3 读在画面中间（不在字幕带）。D1–D4：只装着那条常用句。
       返回 gamescript.build 的结果。"""
    import json as _json
    from flowocr.analyze import gamescript as GS

    def L(key, text, **kw):
        return GS.Line(key=key, kind="Talk", role="NPC", speaker=None, raw=text, cn=None, **kw)
    units = [
        GS.Unit("A", None, [L("a0", "最初の台詞はここから始まる"),
                            L("as", "うん、そうだね"),          # 短行：只能靠 short_anchors 锚
                            L("a1", "選択肢Aの後の返事ですよ", memberships=[("g", "A")]),
                            L("a2", "選択肢Bの後の返事ですよ", memberships=[("g", "B")]),
                            L("ac", COMMON),
                            L("a3", "最後にみんなで帰りましょう"),
                            L("a4", "そして誰もいなくなったのでした")]),
        GS.Unit("B", None, [L("b0", "この対話は一度も画面に出ていない"),
                            L("b1", "だから台本にも入らないはずです")]),
        GS.Unit("C", None, [L("c0", "リンさん、ビリーさん、聞こえるかしら", variant="C#v0"),
                            L("c1", "アキラさん、ビリーさん、聞こえるかしら", variant="C#v0"),
                            L("c2", "いいわね、では実戦訓練に移行するわ"),
                            L("c3", "次の目的地はルミナスクエアです")]),
        *[GS.Unit(f"D{i}", None, [L(f"d{i}", COMMON), L(f"d{i}x", f"別の場所の台詞その{i}番目です")])
          for i in range(1, 5)],
    ]
    obs = d / "syn.jsonl"
    # (文字, 秒, 框中心 y / 画面高)：字幕带在 0.85，c3 读在画面中间（选项 / 居中旁白那种）
    rows = [("最初の台詞はここから始まる", 10, .85), ("うん、そうだね", 11, .4),
            ("選択肢Aの後の返事ですよ", 12, .85), (COMMON, 14, .3),
            ("最後にみんなで帰りましょう", 16, .86), ("ビリーさん、聞こえるかしら", 30, .84),
            ("いいわね、では実戦訓練に移行するわ", 32, .85), ("次の目的地はルミナスクエアです", 34, .4)]
    obs.write_text("\n".join([_json.dumps({"_meta": {"height": 1000}})]
                             + [_json.dumps({"text": t, "t_us": s * 1_000_000,
                                             "box": [100, y * 1000 - 10, 900, y * 1000 + 10]},
                                            ensure_ascii=False) for t, s, y in rows]) + "\n",
                   encoding="utf-8")
    saved = dict(GS.LOADERS)
    GS.LOADERS["syn"] = lambda: units
    try:
        from contextlib import redirect_stdout
        import io
        with redirect_stdout(io.StringIO()):
            return GS.build("syn", obs, qmin=8, min_contain=0.85, max_units=3, min_anchors=2,
                            solo_len=20)
    finally:
        GS.LOADERS.clear()
        GS.LOADERS.update(saved)


def _mini_bundle(d: Path, game: str = "genshin", tables=("quests", "reminders"),
                 langs=(("ja", "jp"), ("zh-Hans", "chs")), compress: bool = True, n: int = 3) -> Path:
    """按 `gtd-bundle/1` 手搭一个迷你包目录（清单、两种哈希、指纹都按协议算）。返回目录。"""
    import hashlib
    import json as _json
    import zstandard
    from flowocr.analyze import gtdbundle as GB
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for t in tables:
        for code, suf in langs:
            body = "".join(_json.dumps({"id": i, "text": f"{t}-{code}-{i}"}, ensure_ascii=False) + "\n"
                           for i in range(n)).encode("utf-8")
            name = f"{t}_{suf}.jsonl" + (".zst" if compress else "")
            stored = zstandard.ZstdCompressor(level=3).compress(body) if compress else body
            (d / name).write_bytes(stored)
            files.append({"path": name, "table": t, "lang": code, "rows": n, "bytes": len(stored),
                          "sha256": hashlib.sha256(stored).hexdigest(),
                          "content_sha256": hashlib.sha256(body).hexdigest()})
    rep = b'{"hard_failures": []}\n'
    (d / "report.json").write_bytes(rep)
    files.append({"path": "report.json", "bytes": len(rep), "sha256": hashlib.sha256(rep).hexdigest()})
    m = {"schema": GB.SCHEMA, "game": game, "fingerprint": GB.fingerprint_of(files), "rev": 1,
         "upstream": {"repo": "r", "commit": "c", "version": "1.0", "integrity_checked": False},
         "extractor": {"subproject": game, "git_head": "h", "dirty": False},
         "langs": {code: suf for code, suf in langs}, "base_lang": "zh-Hans",
         "tables": {t: {"rows": n, "contract": f"{game}.{t}/1"} for t in tables}, "files": files}
    (d / "manifest.json").write_text(_json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return d


def _zip_bundle(src: Path, dst: Path, game: str = "genshin", tamper: str | None = None) -> Path:
    """把迷你包目录装进 `<game>/` 前缀的 zip；`tamper` 给了就把那个成员的最后一个字节改掉（清单不动）。"""
    import zipfile
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w") as zf:
        for p in sorted(src.iterdir()):
            data = p.read_bytes()
            if p.name == tamper:
                data = data[:-1] + bytes([data[-1] ^ 1])
            zf.writestr(f"{game}/{p.name}", data)
    return dst


__all__ = ['ROOT', 'FAILED', 'evalkit', 'srtio', 'importlib', 'subprocess', 'sys', 'inspect', 'tempfile', 'Path', 'raises', 'fake_stream', 'window2', 'fake_frame', 'check', 'high_quality_default', 'FIXED_PAD', 'fixed_pad', 'fake_widths', 'render_overlay', 'SRT_OK', '_shard_behaviour_checks', 'COMMON', 'synthetic_gamescript', '_mini_bundle', '_zip_bundle']
