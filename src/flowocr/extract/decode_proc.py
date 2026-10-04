"""只解码的子进程（`--decode-shards`，decode-buffer）：`decode_shards.ShardedSource` 跑在这里，采样帧经共享内存槽交给管线进程
（det 在那边），辅助流按块序直接转给回抠进程。**不 import 推理库**（起动约 1 s，和管线进程建模型并行）。

帧怎么过去：这边建一块共享内存、切成若干槽（BGR 整帧），写进空槽后发 `("f", 帧号, 时刻, 槽)`；管线进程**拷出来**（整帧 6 MB，约 1 ms）
就发 `("r", 槽)` 还回去——那边没有任何东西会指着槽（"以后谁存了一个切片"会静默写坏，所以先拷；零拷贝没量过）。
槽满了这边就停（背压一路传回各路 ffmpeg），槽数按 det 的前瞻给（`slots_for`），帧环容量也要把它算进在途（`edge_refine.ring_in_flight`）。

起动分两段：管线进程一解析完参数就 `DecodeProxy()`（只起进程、不等）；算好取帧参数后 `start(cfg)`，这边起各路 ffmpeg、回 `("hello", …)`。
流结束发 `("end", 取帧层的属性)`。故障按 `childproc` 的约定：硬解交付不对 `("hw", 原因)`，别的 `("err", 回溯)`；管线进程断开（EOF）就收摊退出。

2026-09-24 以前这里是 `front_proc`，还有"解码 + det"（`--front-proc`）和"只做 det"（`--det-proc`）两种模式——量过稳态更慢 / ±0%，
owner 定删掉（decode-buffer）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from queue import Queue

from flowocr.extract import childproc


def slots_for(det_batch: int, det_prefetch: int) -> int:
    """共享内存槽几个：盖住 det 一次要的批和它的前瞻，再留几个给管线进程慢半拍。"""
    return max(8, det_batch * (1 + det_prefetch) + 4)


def aux_remote(acfg: dict):
    """一个回抠进程的辅助流去处：像素连 `url`、逐帧 pts 经它的 `pts_addr` 直接送过去（不绕管线进程）。"""
    from multiprocessing.connection import Client
    from types import SimpleNamespace

    from flowocr.extract import framesource

    port2, key2 = acfg["pts_addr"]
    feed = Client(("127.0.0.1", int(port2)), authkey=bytes.fromhex(key2))
    flock = threading.Lock()

    def put(item) -> None:
        with flock:
            feed.send(("pe",) if item is framesource.QUEUE_SENTINEL else ("p", item))

    return SimpleNamespace(url=acfg["url"], pts_put=put)


def make_source(cfg: dict, remote):
    """按管线进程发来的取帧参数建分片取帧层；`remote` 是辅助流去处（`aux_remote` / `AuxFanout` / None）。"""
    from flowocr.extract import decode_shards

    kw = cfg["source_kw"]
    a = cfg.get("aux") or {}
    return decode_shards.ShardedSource(
        cfg["video"], lanes=cfg["lanes"], grid=kw["grid"], src_fps=kw["src_fps"], width=kw["width"],
        height=kw["height"], pix=kw["pix"], select_by=kw["select_by"], hwaccel=kw["hwaccel"],
        start_idx=kw["start_idx"], end_idx=kw["end_idx"],
        blocks=decode_shards.BlockPlan(cfg["video"], kw["start_idx"], kw["end_idx"], kw["src_fps"], cfg["block_sec"]),
        aux_remote=remote, aux_grid=a.get("grid"), aux_scale=a.get("scale", 0.35))


def end_stats(source, slot_wait: float) -> dict:
    return {"stopped_early": source.stopped_early, "advanced": source.advanced,
            "grid_off": dict(source.grid_off), "grid_skipped": source.grid_skipped,
            "stderr_tail": list(source.stderr_tail), "preroll": source.preroll_ticks,
            "slot_wait": round(slot_wait, 2),
            "shards": {"lanes": source.lanes, "blocks": len(source.blocks), "lane_blocks": source.lane_blocks,
                       "lane_sec": [round(x, 2) for x in source.lane_sec], "peak_mb": round(source.peak_mb),
                       **({"probe_unknown": source.probe_unknown} if source.probe_unknown else {}),
                       **({"plan_error": source.plan_error} if source.plan_error else {})}}


def serve() -> int:
    from multiprocessing import shared_memory

    import numpy as np

    from flowocr.extract import framesource
    from flowocr.extract import timeline as tl

    conn, send = childproc.connect()
    tl.start_from_env("decode")                      # --timeline：这个进程写 `<路径>.decode.jsonl`
    try:
        msg = conn.recv()
    except (EOFError, OSError):
        return 0
    if msg[0] != "go":                               # "x"：管线进程起不来 / 不用了
        return 0
    cfg = msg[1]
    shm = None
    stop = threading.Event()
    free: Queue = Queue()
    listening = False
    try:
        source = make_source(cfg, aux_remote(cfg["aux"]) if cfg.get("aux") else None)
        H, W, n = cfg["H"], cfg["W"], cfg["slots"]
        nb = H * W * 3
        shm = shared_memory.SharedMemory(create=True, size=nb * n)
        for i in range(n):
            free.put(i)

        def recv_loop() -> None:
            try:
                while True:
                    m = conn.recv()
                    if m[0] == "r":
                        free.put(m[1])
                    else:                                # "x"：管线进程要收摊
                        break
            except (EOFError, OSError):
                pass
            stop.set()
            free.put(None)

        threading.Thread(target=recv_loop, daemon=True, name="decode-recv").start()
        listening = True
        send(("hello", {"shm": shm.name, "slots": n, "describe": source.describe()}))
        slot_wait = 0.0
        for idx, t_sec, frame in source:
            t0 = time.perf_counter()
            slot = free.get()
            slot_wait += time.perf_counter() - t0
            if slot is None or stop.is_set():
                break
            np.ndarray((H, W, 3), np.uint8, buffer=shm.buf[slot * nb:(slot + 1) * nb])[...] = frame
            send(("f", idx, t_sec, slot))
        source.close()
        tl.dump()                                    # 在回 end 之前：管线进程收到就收摊了
        send(("end", end_stats(source, slot_wait)))
    except BaseException as exc:                     # noqa: BLE001  一律交回管线进程，由它决定怎么退
        try:
            if isinstance(exc, framesource.HwaccelMismatch):
                send(("hw", str(exc)))
            else:
                send(("err", traceback.format_exc()))
        except OSError:
            pass
        if not listening:                            # 收消息的线程还没起：没人会置 stop，等就是白等
            stop.set()
    # 管线进程拷完最后一帧、发 "x"（或断开）之前不能拆共享内存。**出错时也要等它**（2026-09-24 审核）：
    # hello 已发、它还没附上共享内存时这边先拆，Windows 上段就没了，它附时 FileNotFoundError 会盖掉管道里的 "hw"——软解重跑就丢了
    if shm is not None:
        stop.wait(120)
        shm.close()
        shm.unlink()
    try:
        conn.close()
    except OSError:
        pass
    return 0


class DecodeProxy:
    """管线进程这边：取帧层的接口——迭代吐 `(帧号, 时刻, BGR 帧)`；流结束后 `stopped_early` / `advanced` / `grid_off` /
    `grid_skipped` / `stderr_tail` / `preroll_ticks` / `shards` 从子进程带回来。构造只起进程、不等；`start(cfg)` 开流。"""

    def __init__(self, link=None) -> None:
        self.child = link or childproc.Child("flowocr.extract.decode_proc", "解码进程")
        self.shm = None
        self.aux = None                                  # 辅助流收在回抠进程里（--edge-proc）
        self.stopped_early = False
        self.advanced = 0
        self.grid_off: dict = {}
        self.grid_skipped = 0
        self.stderr_tail: list = []
        self.preroll_ticks = 0
        self.shards: dict | None = None                  # 各路领了几块、各花多久、预取峰值
        self.wait_start_sec = 0.0
        self.wait_sec = 0.0
        self._describe = ""

    def start(self, cfg: dict) -> None:
        """开流：`cfg` 是取帧参数。各路 ffmpeg 在子进程里起，回 hello 之后才返回。"""
        from multiprocessing import shared_memory

        t0 = time.perf_counter()
        self.child.send(("go", cfg))
        msg = self.child.recv_checked()
        if msg[0] != "hello":
            raise RuntimeError(f"解码进程起不来：{msg}")
        h = msg[1]
        self.wait_start_sec = time.perf_counter() - t0
        self.shm = shared_memory.SharedMemory(name=h["shm"])
        self.H, self.W = cfg["H"], cfg["W"]
        self.nb = self.H * self.W * 3
        self._describe = h["describe"]

    def describe(self) -> str:
        return f"解码在独立进程（--decode-shards）：{self._describe}"

    def __iter__(self):
        import numpy as np

        while True:
            t0 = time.perf_counter()
            msg = self.child.recv_checked()              # "hw" / "err" 在这里抛
            self.wait_sec += time.perf_counter() - t0
            if msg[0] == "f":
                _, idx, t_sec, slot = msg
                frame = np.ndarray((self.H, self.W, 3), np.uint8,
                                   buffer=self.shm.buf[slot * self.nb:(slot + 1) * self.nb]).copy()
                self.child.send(("r", slot))
                yield idx, t_sec, frame
            elif msg[0] == "end":
                a = msg[1]
                self.stopped_early, self.advanced = a["stopped_early"], a["advanced"]
                self.grid_off = {int(k): v for k, v in a["grid_off"].items()}
                self.grid_skipped, self.stderr_tail = a["grid_skipped"], a["stderr_tail"]
                self.preroll_ticks, self.shards = a["preroll"], a["shards"]
                return
            else:
                raise RuntimeError(f"解码进程发来不认识的消息：{msg}")

    def close(self) -> None:
        """收摊（开流前后都能调）。"""
        self.child.close()
        if self.shm is not None:
            try:
                self.shm.close()
            except (OSError, BufferError):
                pass
            self.shm = None


# ---------------------------------------------------------------------------------------------------------------
# 广播模式（`run_groups --share-decode`，ocr-regions 计划）：**一个**解码进程给多组供帧
# ---------------------------------------------------------------------------------------------------------------

HUB_ENV = "FLOWOCR_DECODE_HUB"
"""子进程（各组的管线进程）从这里认出"去接广播解码"：`端口:口令hex`。"""
HUB_KEY_ENV = "FLOWOCR_DECODE_HUB_KEY"
HUB_REASON_ENV = "FLOWOCR_DECODE_HUB_REASON"
"""广播解码进程硬解交付不对时把原因写到这个文件（`run_groups` 读了传给整批软解重跑，重跑的 obs 记 `_meta.hwaccel_fallback`）。"""


def connect_decoder() -> "DecodeProxy":
    """管线进程要一个只解码的来源：有广播解码进程（`HUB_ENV`）就接上去当一个订阅者，否则自己起一个子进程。"""
    hub = os.environ.get(HUB_ENV)
    return DecodeProxy(HubLink(hub) if hub else None)


class HubLink:
    """管线进程接广播解码进程的一条连接（`DecodeProxy` 用的 `send` / `recv_checked` / `close` 三件套，同 `childproc.Child`）。"""

    def __init__(self, addr: str) -> None:
        from multiprocessing.connection import Client

        port, key = addr.split(":")
        try:
            self.conn = Client(("127.0.0.1", int(port)), authkey=bytes.fromhex(key))
        except OSError as exc:
            raise RuntimeError(f"接不上广播解码进程（{exc!r}）：多半是别的组没起来、run_groups 已经把它收了") from None
        self._lock = threading.Lock()
        self.proc = None
        self._closed = False

    def send(self, msg) -> None:
        with self._lock:
            self.conn.send(msg)

    def recv_checked(self):
        try:
            msg = self.conn.recv()
        except (EOFError, OSError) as exc:
            raise RuntimeError(f"广播解码进程断开了（{exc!r}）：它自己出错、或 run_groups 因为有一组没起来把它收了") from None
        return childproc.check(msg, "广播解码进程")

    def close(self, wait: float = 0.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.send(("x",))
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass


class AuxFanout:
    """一份辅助流转给**多个**回抠进程（各组一个）：每个订阅者一条**独立的有界队列** + 一个发送线程（审核：别让一个发送线程按顺序
    sendall 给 N 个——一个回抠进程环满会把别的组一起堵住）。队列满时 `send` 阻塞（背压回到分片的转发线程）——有界、不丢；
    慢的那组仍会拖住大家（它的帧总得送到），但多了 `depth` 帧的余量。某个订阅者的连接断了：它的队列不再收，别的组照常。
    内存上限约 depth × 帧字节 × 订阅者数（0.35 辅助流 672×378 约 0.25 MB / 帧：512 帧 ≈ 每组 +128 MB），
    **在分片的 1 GB 预取预算之外**。"""

    def __init__(self, remotes: list, depth: int = 512) -> None:
        from queue import Queue

        self.remotes = remotes
        self.qs = [Queue(depth) for _ in remotes]
        self.dead = [False] * len(remotes)
        self.errors: list[str] = []
        self.ths = [threading.Thread(target=self._pump, args=(i,), daemon=True, name=f"aux-fan-{i}") for i in range(len(remotes))]

    def open(self) -> "AuxFanout":
        for th in self.ths:
            th.start()
        return self

    def _pump(self, i: int) -> None:
        from flowocr.extract import decode_shards, framesource

        w = None
        try:
            w = decode_shards.TcpAux(self.remotes[i])
            while True:
                item = self.qs[i].get()
                if item is None:
                    return
                w.send(*item)
        except Exception as exc:                        # noqa: BLE001  这一组的回抠进程没了：它自己的管线会报，别的组照常
            self.dead[i] = True
            self.errors.append(f"订阅者 {i}：{exc!r}")
            while self.qs[i].get() is not None:         # 排空，免得 send 卡在这条满队列上
                pass
        finally:
            if w is not None:
                w.close()
            else:
                try:
                    self.remotes[i].pts_put(framesource.QUEUE_SENTINEL)
                except Exception:                       # noqa: BLE001
                    pass

    def send(self, frame, n: int, t_sec: float) -> None:
        buf = bytes(memoryview(frame).cast("B"))        # 一份字节，几条队列共用（不可变）
        for i, q in enumerate(self.qs):
            if not self.dead[i]:
                q.put((buf, n, t_sec))

    def close(self) -> None:
        for q in self.qs:
            q.put(None)
        for th in self.ths:
            th.join(60)


def hub_serve(n_subs: int, accept_timeout: float = 120.0) -> int:
    """广播解码：等 `n_subs` 个订阅者连上、各发 `("go", cfg)`（或 `("x",)` = 这一组不用了），核对取帧参数一致，
    然后只起**一个**分片取帧层；采样帧写进一个共享内存环、同一个槽号发给每个活着的订阅者，**每个订阅者都还了**这个槽才回收
    （订阅者断开 = 它手里的槽全还、之后不再算它）；辅助流经 `AuxFanout` 转给各组的回抠进程。出错按 `childproc` 的约定发给所有订阅者。"""
    from multiprocessing import shared_memory
    from multiprocessing.connection import Listener
    from queue import Empty, Queue

    import numpy as np

    from flowocr.extract import framesource

    key = bytes.fromhex(os.environ.pop(HUB_KEY_ENV))
    lst = Listener(("127.0.0.1", 0), authkey=key)
    print(json.dumps({"port": lst.address[1]}), flush=True)
    subs: list = []                                   # [(conn, cfg)]
    got = threading.Condition()

    def hear(c) -> None:                              # 每个连接一个线程收 go / x：一个连上却迟迟不发的组不挡后面的连接
        try:
            m = c.recv()
        except (EOFError, OSError):
            m = ("x",)
        with got:
            subs.append((c, m[1] if m[0] == "go" else None))
            got.notify_all()

    def accept_all() -> None:
        for _ in range(n_subs):
            try:
                c = lst.accept()
            except OSError:
                return
            threading.Thread(target=hear, args=(c,), daemon=True).start()

    threading.Thread(target=accept_all, daemon=True).start()
    # 等满 n_subs 个"go / x"。**有组起不来**由 run_groups 发现（它看着子进程）并收掉这个进程；这里的超时只是兜底
    deadline = time.perf_counter() + accept_timeout
    with got:
        while len(subs) < n_subs and time.perf_counter() < deadline:
            got.wait(1.0)
    subs = list(subs)
    live = [(c, cfg) for c, cfg in subs if cfg is not None]
    if not live:
        return 0

    def tell_all(msg) -> None:
        for c, _ in live:
            try:
                c.send(msg)
            except OSError:
                pass

    shm = None
    stop = threading.Event()
    try:
        if len(subs) < n_subs:
            raise RuntimeError(f"广播解码：{accept_timeout:.0f} s 里只等到 {len(subs)} / {n_subs} 个订阅者")
        cfg0 = live[0][1]
        same = ("video", "source_kw", "lanes", "block_sec", "H", "W")
        bad = [k for k in same for _, c in live if c.get(k) != cfg0.get(k)]
        # 辅助流也只有一路：各组的取帧网格 / 缩放要相同（广播的是同一个 ffmpeg 的辅路）
        bad += [f"aux.{k}" for k in ("grid", "scale") for _, c in live
                if (c.get("aux") or {}).get(k) != (cfg0.get("aux") or {}).get(k)]
        if bad or any(bool(c.get("aux")) != bool(cfg0.get("aux")) for _, c in live):
            raise RuntimeError(f"广播解码：各组的取帧参数不一致（{sorted(set(bad))}）——同一个视频、同一个窗口、同一组解码旋钮才能共用")
        fan = AuxFanout([aux_remote(c["aux"]) for _, c in live]) if cfg0.get("aux") else None
        source = make_source(cfg0, fan)
        H, W = cfg0["H"], cfg0["W"]
        n = max(c["slots"] for _, c in live)
        nb = H * W * 3
        shm = shared_memory.SharedMemory(create=True, size=nb * n)
        free: Queue = Queue()
        for i in range(n):
            free.put(i)
        lock = threading.Lock()
        ref = [0] * n
        held: list[set] = [set() for _ in live]
        alive = [True] * len(live)

        def release(i: int, slot: int) -> None:
            with lock:
                if slot in held[i]:
                    held[i].discard(slot)
                    ref[slot] -= 1
                    if ref[slot] == 0:
                        free.put(slot)

        def recv_loop(i: int) -> None:
            c = live[i][0]
            try:
                while True:
                    m = c.recv()
                    if m[0] == "r":
                        release(i, m[1])
                    else:                               # "x"：这一组收摊了
                        break
            except (EOFError, OSError):
                pass
            with lock:
                alive[i] = False
                mine = list(held[i])
            for slot in mine:
                release(i, slot)
            if not any(alive):
                stop.set()
                free.put(None)

        for i in range(len(live)):
            threading.Thread(target=recv_loop, args=(i,), daemon=True, name=f"hub-recv-{i}").start()
        tell_all(("hello", {"shm": shm.name, "slots": n, "describe": source.describe() + f"、广播给 {len(live)} 组"}))
        print(json.dumps({"started": len(live)}), flush=True)   # run_groups 看到它才不再"有组退出就收掉广播进程"
        slot_wait = 0.0
        for idx, t_sec, frame in source:
            t0 = time.perf_counter()
            while True:
                try:
                    slot = free.get(timeout=1.0)
                    break
                except Empty:
                    if stop.is_set():
                        slot = None
                        break
            slot_wait += time.perf_counter() - t0
            if slot is None or stop.is_set():
                break
            np.ndarray((H, W, 3), np.uint8, buffer=shm.buf[slot * nb:(slot + 1) * nb])[...] = frame
            with lock:
                who = [i for i in range(len(live)) if alive[i]]
                ref[slot] = len(who)
                for i in who:
                    held[i].add(slot)
            for i in who:
                try:
                    live[i][0].send(("f", idx, t_sec, slot))
                except OSError:
                    release(i, slot)
        source.close()
        st = end_stats(source, slot_wait)
        st["shards"]["hub"] = len(live)
        if fan is not None and fan.errors:
            st["shards"]["fanout_errors"] = fan.errors[:4]
        tell_all(("end", st))
    except BaseException as exc:                        # noqa: BLE001
        if isinstance(exc, framesource.HwaccelMismatch) and os.environ.get(HUB_REASON_ENV):
            try:
                Path(os.environ[HUB_REASON_ENV]).write_text(str(exc), encoding="utf-8")
            except OSError:
                pass
        tell_all(("hw", str(exc)) if isinstance(exc, framesource.HwaccelMismatch) else ("err", traceback.format_exc()))
    # 各组拷完最后一帧、告别（或断开）之前不能拆共享内存（300 s 只是兜底：正常路径是各组 close -> "x" -> 全走了 -> stop；
    # Windows 上 unlink 是空操作、各组还开着的映射照样有效，所以这不是正确性上限）
    if shm is not None:
        stop.wait(300)
        shm.close()
        shm.unlink()
    for c, _ in subs:
        try:
            c.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--hub":
        sys.exit(hub_serve(int(sys.argv[2])))
    sys.exit(serve())
