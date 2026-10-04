"""ONNX Runtime 的**常驻 worker**：独立进程，靠共享内存和调用方交换张量。
用哪个解释器起它由 `ortclient.server_python()` 定（`FLOWOCR_ORT_PYTHON` > 当前解释器自己装了 onnxruntime > 旧的 onnxrt 实验 venv）。

一个进程可以同时服务**多个模型**（`--model 名字=路径` 可给多次）：管线里 det 和 rec 都走它，
省一份显存、也少一层 CUDA 上下文切换。

为什么推理在单独的进程里：最早是为了不和 Paddle 同进程（两个推理运行时各自加载 cuDNN / cuBLAS，版本互相踩）；
2026-10 运行时去掉 Paddle 之后照旧——管线进程的 GIL 不被推理和前处理抢，一个服务同时接 det 和几个 rec 在途。
代价是每次调用一次进程间往返，**实测 0.12~0.33 ms = 2%~3%**（一次性探针 `probe_ort_rec_ipc.py`）。

**每个形状一个 session**（`--per-shape`，默认开）：ORT 的 CUDA EP **每次形状变化**都要重新规划，
**不是只在第一次见到的时候**——同宽连打 4.6 ms、8 个宽轮转 **81 ms**，预热过也还是 80 ms。
每个形状各给一个 session 之后回到 5.2 ms。代价是每个 session 一份权重（rec fp32 73 MB）和一份自己的 arena，`--share-mem` 让它们共用（约 155 MB → 50 MB）。
所以调用方**必须**把形状收敛到少数几个（rec 那边是把张量宽取整到 320 的倍数 + 批取到 2 的幂）。

协议（够用就好，不做通用 RPC）：
* 调用方建一块共享内存，输入张量原样写进前半段（float32、C 序）；
* 一行 JSON 进来：`{"model": 名字, "shape": [...]}`；或者 rec 送**原样的 uint8 裁剪**（依次紧排）：
  `{"model": 名字, "crops": [[h, w, 3], …], "grid": 320, "batch": k}`，前处理在这边做（`recprep.batch`，握手里 `pre` 为真才能这么送）；
* 结果按顺序写进后半段（`--in-bytes` 之后），回一行
  `{"outs": [[dtype, shape], …], "infer_ms": …}`——调用方按这个自己切；
* `{"bye": 1}` 退出。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import threading
import time
from multiprocessing import shared_memory

import numpy as np
import onnxruntime as ort


def norm_device(d: str) -> str:
    """`cpu` / `gpu` / `gpu:N` -> `cpu` / `gpu:N`（`gpu` 就是 0 号卡）。这个文件可能跑在别的解释器里、不 import flowocr，
    所以口径在这里自己写一份；和 `ocr_args.ort_device` 的输出是同一种字符串，握手按它逐字比。"""
    d = (d or "").strip().lower()
    if d == "cpu":
        return "cpu"
    if d == "gpu":
        return "gpu:0"
    if d.startswith("gpu:") and d[4:].isdigit():
        return f"gpu:{int(d[4:])}"
    raise SystemExit(f"--device 只认 cpu / gpu / gpu:N：{d!r}")


def load_recprep():
    """按**文件路径**加载同目录的 `recprep.py`（rec 的前处理），不经过 `flowocr.extract` 的包初始化
    （这个进程可能跑在别的解释器里，也可能没有 flowocr）。没有它或没有 cv2 就返回 None，
    客户端照旧自己做前处理、送张量（握手里的 `pre` 报这件事）。"""
    import importlib.util
    from pathlib import Path
    p = Path(__file__).with_name("recprep.py")
    try:
        import cv2  # noqa: F401
        spec = importlib.util.spec_from_file_location("_flowocr_recprep", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:                     # noqa: BLE001
        print(json.dumps({"note": f"前处理不在服务端做：{exc!r}"}), file=sys.stderr, flush=True)
        return None


def gpu_initializers(path: str, device_id: int, keep: list) -> dict:
    """模型里的大权重（float32、≥1024 个元素）各拷一份到**我们自己 cudaMalloc 的**显存，按名字给出 OrtValue。

    同一份 OrtValue 经 `SessionOptions.add_initializer` 交给同一个模型的每个 session，**权重在显存里只有一份**
    （decode-buffer：每个 session 固定约 155 MB，其中 73 MB 是权重）。ORT 只收**用户持有**的缓冲
    （它自己 `ortvalue_from_numpy(..., "cuda")` 分的会被拒），所以走 cudart + DLPack；
    这些缓冲和进程同生共死（`keep` 握着，deleter 为空，进程退出时随 CUDA 上下文一起释放）。
    没有 `onnx` 包（worker 起在别的解释器里）就返回空表、不共享，行为和以前一样。
    """
    try:
        import onnx
        from onnx import numpy_helper
    except ImportError:
        print(json.dumps({"note": "没有 onnx 包，权重不共享"}), file=sys.stderr, flush=True)
        return {}
    import ctypes

    class DLDevice(ctypes.Structure):
        _fields_ = [("device_type", ctypes.c_int32), ("device_id", ctypes.c_int32)]

    class DLDataType(ctypes.Structure):
        _fields_ = [("code", ctypes.c_uint8), ("bits", ctypes.c_uint8), ("lanes", ctypes.c_uint16)]

    class DLTensor(ctypes.Structure):
        _fields_ = [("data", ctypes.c_void_p), ("device", DLDevice), ("ndim", ctypes.c_int32),
                    ("dtype", DLDataType), ("shape", ctypes.POINTER(ctypes.c_int64)),
                    ("strides", ctypes.POINTER(ctypes.c_int64)), ("byte_offset", ctypes.c_uint64)]

    class DLManagedTensor(ctypes.Structure):
        _fields_ = [("dl_tensor", DLTensor), ("manager_ctx", ctypes.c_void_p), ("deleter", ctypes.c_void_p)]

    cudart = ctypes.CDLL("cudart64_13.dll" if sys.platform == "win32" else "libcudart.so.13")
    if cudart.cudaSetDevice(device_id):
        raise RuntimeError("cudaSetDevice 失败")
    new_capsule = ctypes.pythonapi.PyCapsule_New
    new_capsule.restype, new_capsule.argtypes = ctypes.py_object, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    out = {}
    for t in onnx.load(path).graph.initializer:
        arr = numpy_helper.to_array(t)
        if arr.dtype != np.float32 or arr.size < 1024:
            continue
        arr = np.ascontiguousarray(arr)
        ptr = ctypes.c_void_p()
        if cudart.cudaMalloc(ctypes.byref(ptr), ctypes.c_size_t(arr.nbytes)) or \
                cudart.cudaMemcpy(ptr, arr.ctypes.data_as(ctypes.c_void_p), ctypes.c_size_t(arr.nbytes), 1):   # 1 = 主机到设备
            raise RuntimeError(f"权重 {t.name} 拷不上显存")
        shape = (ctypes.c_int64 * arr.ndim)(*arr.shape)
        mt = DLManagedTensor(DLTensor(ptr, DLDevice(2, device_id), arr.ndim, DLDataType(2, 32, 1), shape, None, 0), None, None)
        cap = new_capsule(ctypes.addressof(mt), b"dltensor", None)          # 2 = kDLCUDA；DLDataType(2, 32, 1) = float32
        val = ort.OrtValue(ort.capi._pybind_state.OrtValue.from_dlpack(cap, False))
        keep += [ptr, shape, mt, cap, val]
        out[t.name] = val
    return out


GRAPH_CAPTURE_RUNS = 4
"""建 CUDA Graph session 时连跑几次 `run_with_iobinding`：ORT 的 CUDA EP 要先有几次常规推理才捕图（本机 ORT 1.29 上跑 1 次不够），
多跑一两次宁多勿少——每个形状只付一次，每次几毫秒。"""


GRAPH_EXCLUSIVE_REPLAYS = 8
"""图 session 建好之后的前几次回放仍拿**独占**闸，之后才共享。经验阈值（2026-09-26，ORT 1.29）：建图时连跑 4 次、甚至 12 次之后
直接共享回放照崩（两段 × 三遍全崩），前 2 次独占也崩，前 8 次独占两段 × 三遍全过——真正的捕获落在建好之后头几次调用里，
和建图时跑几次无关，原理没弄清。只在开 `--ort-cuda-graph` 时起作用。"""


class _CaptureGate:
    """读写闸：很多个 `shared()` 可以同时持有，`exclusive()` 独占——等在途的共享的都退出才进，
    **排上队之后新来的共享请求先等**（写优先：服务里两条连接的请求源源不断，不然捕图会被饿死）。CUDA Graph 捕图用（见 main 里 `gate`）。"""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._readers = 0
        self._writing = False
        self._writers_waiting = 0

    @contextlib.contextmanager
    def shared(self):
        with self._cv:
            while self._writing or self._writers_waiting:
                self._cv.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cv:
                self._readers -= 1
                if not self._readers:
                    self._cv.notify_all()

    @contextlib.contextmanager
    def exclusive(self):
        with self._cv:
            self._writers_waiting += 1
            while self._writing or self._readers:
                self._cv.wait()
            self._writers_waiting -= 1
            self._writing = True
        try:
            yield
        finally:
            with self._cv:
                self._writing = False
                self._cv.notify_all()


def serve_socket(a, hello: dict, infer, background: bool = False) -> int:
    """在 127.0.0.1 上等 N 个 worker 接进来，**共用一份模型和显存**。

    `background=True`（管道模式的 `--side-listen`）：accept 循环放后台线程、返回端口，由调用方写进自己的 ready 行；
    进程的生死仍由管道那条决定（stdin 读到 bye / EOF 主循环退出，后台线程是 daemon）。

    每个客户端一个线程；锁在 `infer` 里（2026-09-23 起）：只有池子的账一把（CUDA Graph session 另各一把），
    请求可以并发、同形状也行（原来一把全局锁，多 worker 的请求排成一队，decode-buffer）。客户端各带自己的共享内存（hello 里给名字和输入区大小），
    所以**张量不经过 socket**——socket 上只走一行 JSON。

    **握手要核能力**（2026-09-20 审计七）：客户端在 hello 里报 `want`（它要哪几个模型），
    服务端核对之后回 `{"ok":1,"models":[…]}`，少哪个就回 `{"ok":0,"missing":[…]}` 让它**自己去 spawn**。
    以前不核：`FLOWOCR_ORT_ADDR` 是进程级的，`OrtClient` 见到就接上去而不看自己要什么，
    于是 `--workers N` 时 det 的请求会打进只加载了 rec 的服务里。

    **父进程没了就退出**（`--exit-on-stdin-eof`）：这个进程握着显存，而
    `while True: accept()` 自己不会停——Ctrl-C 或驱动中途异常时会留一个吃着 GPU 的孤儿。

    ⚠ **判据是"stdin 读到 EOF"，不是"拿 pid 探活"**（2026-09-20 实测）：
    第一版写的是每 2 秒 `os.kill(parent_pid, 0)`，冒烟直接不过——
    Windows 上只要**还有谁握着那个进程的句柄**（父进程自己的 `Popen` 对象就算），
    进程都退出了 `OpenProcess` 照样成功、一个异常都不抛，于是看门狗永远不响。
    （顺带：signal 0 在 Windows 上确实只探活、不会误杀，这点验过。）
    stdin 那条没有这个问题：管道的写端跟着父进程的句柄表一起关，**EOF 是内核给的**。
    调用方要 `stdin=subprocess.PIPE` 并且一直不关它。
    """
    import os
    import socket
    import threading

    served = sorted(hello.get("models") or ())

    def watch_stdin() -> None:
        try:
            sys.stdin.buffer.read()      # 父进程活着就一直阻塞在这
        except Exception:
            pass
        os._exit(0)                      # 握着 CUDA 上下文，别走正常析构

    if a.exit_on_stdin_eof and not background:     # 旁路模式下 stdin 就是管道本身，由主循环读
        threading.Thread(target=watch_stdin, daemon=True).start()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", max(a.listen, 0)))
    srv.listen(16)
    port = srv.getsockname()[1]

    def client(conn: "socket.socket") -> None:
        f = conn.makefile("rwb")
        shm = None
        try:
            h = json.loads(f.readline() or "{}").get("hello") or {}
            missing = [m for m in (h.get("want") or ()) if m not in served]
            try:                                      # 卡号也要对上：gpu:1 的客户端不能接到 0 号卡的服务上
                wrong_dev = bool(h.get("device")) and norm_device(h["device"]) != hello.get("device")
            except SystemExit:
                wrong_dev = True
            if missing or wrong_dev:
                # **不服务它**：客户端会退回自己 spawn 一个带全套模型、对的设备的 worker
                f.write((json.dumps({"ok": 0, "missing": missing, "models": served,
                                     "device": hello.get("device")}) + "\n").encode())
                f.flush()
                return
            shm = shared_memory.SharedMemory(name=h["shm"])
            buf, in_bytes = shm.buf, int(h["in_bytes"])
            f.write((json.dumps({"ok": 1, "models": served, "provider": hello.get("provider"), "pre": hello.get("pre"),
                                 "device": hello.get("device"), "ort_version": hello.get("ort_version")}) + "\n").encode())
            f.flush()
            while True:
                line = f.readline()
                if not line:
                    break
                req = json.loads(line)
                if req.get("bye"):
                    break
                rep = infer(req, buf, in_bytes)  # 锁在 infer 里面：池子的账一把、每个 session 一把（不再一把全局锁）
                f.write((json.dumps(rep) + "\n").encode())
                f.flush()
        except Exception as e:                   # 一个客户端崩了不该带走服务
            try:
                f.write((json.dumps({"error": repr(e)}) + "\n").encode())
                f.flush()
            except Exception:
                pass
        finally:
            if shm is not None:
                shm.close()
            conn.close()

    def accept_loop() -> None:
        while True:
            conn, _ = srv.accept()
            # **关 Nagle**（2026-09-22，decode-buffer）：每次往返是"一行很短的 JSON 过去、一行很短的 JSON 回来"，
            # 正是 Nagle + 延迟确认最伤的形状（共享服务和管道模式的旁路连接都走这里）
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=client, args=(conn,), daemon=True).start()

    if background:
        threading.Thread(target=accept_loop, daemon=True, name="side-listen").start()
        return port
    print(json.dumps({**hello, "port": port}), flush=True)
    accept_loop()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", required=True, metavar="名字=路径")
    ap.add_argument("--shm", default="", help="共享内存的名字（调用方建、调用方销毁）；"
                                              "`--listen` 模式下由每个客户端在 hello 里给")
    ap.add_argument("--in-bytes", type=int, default=0, help="输入区字节数（输出从这之后开始）")
    ap.add_argument("--listen", type=int, default=-1, metavar="PORT",
                    help="**多客户端模式**：在 127.0.0.1 的这个端口上等（0 = 随便挑一个，"
                         "挑到的端口写在 ready 那行里）。N 个 worker 共用一个服务进程 = "
                         "**显存只有一份**——这是 `--workers` 能重新放宽的前提（owner 09-19）")
    ap.add_argument("--exit-on-stdin-eof", action="store_true",
                    help="`--listen` 模式下**读到 stdin 的 EOF 就自己退出**——"
                         "调用方要 `stdin=subprocess.PIPE` 并一直不关它，父进程一没，管道写端就关。"
                         "服务进程握着显存而 accept 循环自己不会停，不盯着就会留孤儿（审计七）。"
                         "⚠ **别改回拿 pid 探活**：Windows 上只要还有谁握着句柄，"
                         "进程退出了 `os.kill(pid, 0)` 照样成功——第一版就是这么挂的。"
                         "⚠ 也**别在 stdin 是 DEVNULL 时给这个参数**：那会立刻读到 EOF、当场退出")
    ap.add_argument("--device", default="gpu:0",
                    help="gpu:N = 必须用 N 号卡的 CUDA EP（这份 onnxruntime 没有 CUDA EP、或建 session 回落到 CPU，都退出码 3，不静默跑 CPU）；"
                         "`gpu` 等于 `gpu:0`；cpu = 只用 CPUExecutionProvider（NVIDIA 包里也是）。"
                         "由调用方按 run_ocr2 的 --device 传（Codex 审计 P1；卡号贯通是复审 P2：原来 gpu:1 到这里就成了 0 号卡）")
    ap.add_argument("--side-listen", action="store_true",
                    help="管道模式下**另开一个 127.0.0.1 端口**（写在 ready 行的 `port` 里），让同一个调用方再接几条连接进来、"
                         "同时有几个请求在途（`run_ocr2 --rec-inflight`，decode-buffer）。连接的握手和共享服务一样")
    ap.add_argument("--per-shape", type=int, default=1, help="1 = 每个形状一个 session（见文件头）")
    ap.add_argument("--share-mem", type=int, default=0,
                    help="1 = 同一个模型的 session 之间**共用显存**：①所有 session 共用一个进程级 CUDA arena"
                         "（`session.use_env_allocators`；默认每个 session 各自一份、各自涨到自己形状的激活峰值），"
                         "②权重在显存里只放一份（`gpu_initializers`）。15 个形状 4.8 GB → 1.5 GB、输出逐位相同（decode-buffer）。"
                         "`--gpu-mem-mb` 在这里才真是 arena 的总上限")
    ap.add_argument("--cuda-graph-batch", type=int, default=0,
                    help="批 ≤ 这个数、宽 ≤ --cuda-graph-max-w 的形状用 **CUDA Graph** 回放（0 = 关）。小批次的推理是 kernel 启动受限的："
                         "单独量批 1 × 320 宽 4.2 -> 2.2 ms、输出逐位相同（decode-buffer）。图 session 用**自己的** arena"
                         "（和共用 arena 放在一起会踩显存），所以只给激活小的形状")
    ap.add_argument("--cuda-graph-max-w", type=int, default=960, help="见 --cuda-graph-batch")
    ap.add_argument("--gpu-mem-mb", type=int, default=6144,
                    help="CUDA EP 的显存上限（MB，0 = 不限）。**owner 2026-09-19 定的配额是整机 8 GB**，"
                         "这里留 6 GB 给推理、剩下给桌面和别的负载。**超了 ORT 会当场报 OOM，而不是把卡吃光**——"
                         "那正是要的：每形状一个 session 时显存是按形状数线性涨的，探针一不小心就 24 个 session。")
    ap.add_argument("--warm", default="",
                    help="起动后在后台预热的形状：`模型=批x宽,批x宽,…`（rec 的由管线按 recort.warm_shapes 生成）。每个新形状第一次要建 session 约 0.1 s + 首次 run 0.04~0.4 s，一趟 5 分钟段 18~26 个形状 = 4~6 s 压在 rec 的关键路径上；预热放在管线自己起动（建 det、探视频）的那几秒里做。同一个模型、同一份输入形状，不改读数")
    ap.add_argument("--threads", type=int, default=0,
                    help="每个 session 的 intra-op 线程数（0 = ORT 默认：物理核数）。CPU 路径上 det 和 rec 的 session 同时在跑、"
                         "各自一池物理核数的线程会互相挤（2026-09-25 量的）")
    ap.add_argument("--spin", type=int, default=1,
                    help="0 = 关掉 intra-op 线程池的自旋等待（`session.intra_op.allow_spinning`）。CPU 路径上几个 session 的线程池"
                         "轮空时自旋会把管线进程的 Python 线程挤饿（主线程提交段 4 → 16 s，2026-09-25）")
    ap.add_argument("--max-sessions", type=int, default=12,
                    help="per-shape 池子里最多留几个 session（0 = 不限）。超了**按最久没用的淘汰**："
                         "形状多的时候（探针扫宽 × 批）池子会线性吃显存，实测 24 个 session 吃到 15 GB。")
    ap.add_argument("--cudnn-conv-search", default="EXHAUSTIVE", choices=("EXHAUSTIVE", "HEURISTIC", "DEFAULT"),
                    help="CUDA EP 的 `cudnn_conv_algo_search`，默认同 ORT 自己的默认（EXHAUSTIVE）。⚠ **别拿 DEFAULT 当修法**"
                         "（2026-09-22 nightly 踩的）：ORT 1.26（CUDA 12 构建）+ cuDNN 9.9 在 sm_120（RTX 50 系）上，"
                         "EXHAUSTIVE / HEURISTIC 对 **fp16** 卷积报 `No valid engine configs for ConvFwd`；DEFAULT 能跑，"
                         "但它挑的是慢算法——rec 一批 360 ms，EXHAUSTIVE / HEURISTIC 是 15 ms，quick 六段墙钟 10 倍。"
                         "1.26 上 fp16 模型在这张卡上就是不可用，fp32 模型三种都正常；ORT 1.29（CUDA 13 构建）全都正常。")
    a = ap.parse_args()

    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()                 # 不调它 CUDA EP 的 DLL 找不到、建 session 直接失败（CPU 包上是空操作）
    models = dict(m.split("=", 1) for m in a.model)
    # **设备由调用方说了算**（2026-09-22，Codex 审计 P1）：原来按"这份 onnxruntime 有没有 CUDA EP"自己挑，
    # 于是 NVIDIA 包里 `run_ocr2 --device cpu` 时 rec 照样上 GPU；CPU 包里要 GPU 却静默跑 CPU。
    # 现在：gpu 就必须用上 CUDA EP（没有 / 回落都退出码 3），cpu 就只给 CPU EP；hello 里报出实际用的
    a.device = norm_device(a.device)
    has_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
    cuda_ok = a.device != "cpu"
    if cuda_ok and not has_cuda:
        print(json.dumps({"error": f"要 GPU，但这份 onnxruntime（{ort.__version__}）没有 CUDAExecutionProvider"
                                   f"（有的是 {ort.get_available_providers()}）；CPU 包请给 --device cpu"}), flush=True)
        return 3
    # 显存配额（owner 2026-09-19）：CUDA EP 的 arena 上限。给 0 就是老行为（不限）
    cuda_opts = {"cudnn_conv_algo_search": a.cudnn_conv_search}
    if cuda_ok:
        cuda_opts["device_id"] = int(a.device.split(":")[1])     # 卡号跟着 --device 走（多卡机器上别让 ORT 默认落 0 号卡）
    if a.gpu_mem_mb:
        cuda_opts["gpu_mem_limit"] = a.gpu_mem_mb * 1024 * 1024
    prov_opts = [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"] if cuda_ok else ["CPUExecutionProvider"]

    share = bool(a.share_mem) and cuda_ok
    if share:
        mi = ort.OrtMemoryInfo("Cuda", ort.OrtAllocatorType.ORT_ARENA_ALLOCATOR, cuda_opts["device_id"], ort.OrtMemType.DEFAULT)
        arena = ort.OrtArenaCfg({"max_mem": a.gpu_mem_mb * 1024 * 1024} if a.gpu_mem_mb else {})
        ort.create_and_register_allocator_v2("CUDAExecutionProvider", mi,
                                             {k: str(v) for k, v in cuda_opts.items() if k != "gpu_mem_limit"}, arena)
    keep: list = []
    inits: dict = {}                             # 模型路径 -> 共享的显存权重（普通 session 和 CUDA Graph session 共用一份）
    opts: dict = {}

    def options(path: str, graph: bool = False):
        """每个模型一份 SessionOptions（共享的权重按名字挂上去，不能串到别的模型）。
        `graph`：CUDA Graph session 用的那份——**不接共用 arena**（图录的是固定显存地址，共用 arena 会把它用过的块分给别的 session：
        交替回放 200 次类别错 81 次；各自 arena 0 次，decode-buffer），权重照旧共享。"""
        if (path, graph) not in opts:
            so = ort.SessionOptions()
            if a.threads > 0:
                so.intra_op_num_threads = a.threads
            if not a.spin:
                so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            if share:
                if not graph:
                    so.add_session_config_entry("session.use_env_allocators", "1")
                if path not in inits:
                    inits[path] = gpu_initializers(path, cuda_opts["device_id"], keep)
                for name, val in inits[path].items():
                    so.add_initializer(name, val)
            opts[(path, graph)] = so
        return opts[(path, graph)]

    def make(path: str, graph: bool = False):
        prov = ([("CUDAExecutionProvider", {**cuda_opts, "enable_cuda_graph": "1"}), "CPUExecutionProvider"]
                if graph else prov_opts)
        s = ort.InferenceSession(path, sess_options=options(path, graph), providers=prov)
        if cuda_ok:
            # ORT 的 python 封装在**第一次 run 抛错时会把 session 重建成 CPU 的再试一次**（`_fallback_providers`）——
            # 和上面"有 CUDA EP 却没用上就退出码 3"是同一条规矩：错就报错，不静默换后端
            s.disable_fallback()
        return s

    base = {}
    prov_used = ""
    for name, path in models.items():
        s = make(path)
        prov = s.get_providers()[0]
        if cuda_ok and "CUDA" not in prov:
            print(json.dumps({"error": f"{name} 的 provider 回落到 {prov}（这份 onnxruntime 有 CUDA EP 却没用上）"}), flush=True)
            return 3
        prov_used = prov
        base[name] = s
    pool: dict = {}                              # (模型, 形状) -> (session, 推理锁：只有 CUDA Graph session 有，普通的是 nullcontext)
    pool_lock = threading.Lock()
    n_built = n_evicted = 0
    build_sec = 0.0
    rp = load_recprep()
    hello = {"ready": 1, "provider": prov_used, "device": a.device, "ort_version": ort.__version__,
             "models": {n: [i.name for i in s.get_inputs()] for n, s in base.items()}, "pre": rp is not None}

    class Graph:
        """一个形状的 CUDA Graph session：输入输出绑在**固定地址**的显存 OrtValue 上，每次只拷新输入进去再回放。"""

        def __init__(self, name: str, x: np.ndarray) -> None:
            ref = base[name].run(None, {base[name].get_inputs()[0].name: x})    # 拿输出形状（动态 session 跑一次）
            self.sess = make(models[name], graph=True)
            self.xin = ort.OrtValue.ortvalue_from_numpy(x, "cuda", cuda_opts["device_id"])
            self.outv = [ort.OrtValue.ortvalue_from_shape_and_type(list(r.shape), r.dtype, "cuda", cuda_opts["device_id"])
                         for r in ref]
            self.ob = self.sess.io_binding()
            self.ob.bind_ortvalue_input(self.sess.get_inputs()[0].name, self.xin)
            for o, v in zip(self.sess.get_outputs(), self.outv):
                self.ob.bind_ortvalue_output(o.name, v)
            # 捕获**不在第一次 run**：ORT 的 CUDA EP 先跑几次常规推理（预热）才真正捕图。只跑一次的话，捕获会落到之后某次
            # "回放"里——那时只拿着共享闸，撞上别的并发推理就是 `operation not permitted when stream is capturing`（2026-09-26 查出）。
            # 所以建图的时候（持独占闸）多跑几次，捕完再交出去，之后的调用都是纯回放
            for _ in range(GRAPH_CAPTURE_RUNS):
                self.sess.run_with_iobinding(self.ob)

        n_replays = 0

        def replay_gate(self):
            """回放拿哪道闸：前 `GRAPH_EXCLUSIVE_REPLAYS` 次独占（捕获可能还没真正发生），之后共享。"""
            self.n_replays += 1
            return gate.exclusive() if self.n_replays <= GRAPH_EXCLUSIVE_REPLAYS else gate.shared()

        def run(self, x: np.ndarray) -> list[np.ndarray]:
            self.xin.update_inplace(x)
            self.sess.run_with_iobinding(self.ob)
            return [v.numpy() for v in self.outv]

    def graph_ok(shape) -> bool:
        return bool(cuda_ok and a.per_shape and a.cuda_graph_batch and shape[0] <= a.cuda_graph_batch
                    and shape[-1] <= a.cuda_graph_max_w)

    # **捕图要独占这个进程的 CUDA**：ORT 捕图用全局捕获模式，捕的时候别的线程碰 CUDA（别的 session 推理、建 session、预热）
    # 就是 `operation not permitted when stream is capturing`。2026-09-25 以为只是 D1（det 和图 session 同服务），
    # 09-26 det 单起服务后照样崩；再二分：只有同时关掉 `--ort-warm` 的预热线程、`--rec-inflight` 降到 1 才捕得成——病根是服务里的并发，不是 det。
    # 有了下面这道闸，det 留在同一个服务里也捕得成（gi-s2 60 s 全默认 + `--ort-cuda-graph 4`，文本和框与默认臂相同），D1 不用拆。
    # 批 ≤ N、宽 ≤ 960 的 det 形状也会被捕（形状判据不分模型）
    # 所以开捕图时加一道读写闸：普通推理 / 建普通 session 拿共享的，建图和图 session 的**前 `GRAPH_EXCLUSIVE_REPLAYS` 次回放**拿独占的（建图时连跑 `GRAPH_CAPTURE_RUNS` 次，
    # 可全默认配置下真正的捕获仍落在之后头几次回放里——回放一律共享照崩；一律独占能跑但墙钟 +10%~+16%，2026-09-26 实测），独占的排上队之后新的共享请求先等（不让在途请求把捕图饿死）。
    # 丢掉的 session 也在闸里释放（见 `dead`）。没开捕图时闸是空操作，默认路径不多付一分钱
    gate = _CaptureGate() if cuda_ok and a.per_shape and a.cuda_graph_batch else None

    def shared():
        return gate.shared() if gate is not None else contextlib.nullcontext()

    def build(name: str, shape: tuple, x: np.ndarray):
        """这个形状的 session：图 session 在独占闸里捕，普通的在共享闸里建。"""
        if graph_ok(shape):
            with gate.exclusive():
                return Graph(name, np.ascontiguousarray(x))
        with shared():
            return make(models[name])

    # **丢掉的 session 也要在闸里释放**：析构里有 cudaFree（Graph 还有固定地址的输入输出），捕图时别的线程这么一放就是
    # 捕图失败（901 `operation failed due to a previous error during capture`）。两个线程同时建同一个形状时后到的那份、
    # 预热建完发现池里已经有了的那份、LRU 淘汰的那份——原来都在闸外随手丢，启动时预热线程和请求线程最容易撞上（只在开预热时崩就是这个）。
    # 淘汰的先放进 `dead`，推理时在共享闸里清；本线程手上的引用在共享闸里置空（`with shared(): made = None`——
    # 引用是调用方自己的局部变量，交给别的函数去 del 放不掉它）
    dead: list = []

    def reap() -> None:
        """（持共享闸时调）真正释放淘汰下来的 session。"""
        while dead:
            try:
                dead.pop()
            except IndexError:
                break
    n_warmed = 0

    def insert(key, made) -> tuple:
        """（持 pool_lock）放进池子：满了先按 LRU 淘汰一个——**淘汰在插入时做**，不在建之前（建在锁外，建的时候池子可能已经变了）。"""
        nonlocal n_evicted
        if a.max_sessions and len(pool) >= a.max_sessions:
            # dict 有序：最久没用的排在最前（正在别的线程里跑的那个由它自己握着引用，跑完在闸里放掉）；淘汰的进 `dead`，在闸里释放
            dead.append(pool.pop(next(iter(pool))))
            n_evicted += 1
        pool[key] = (made, threading.Lock() if isinstance(made, Graph) else contextlib.nullcontext())
        return pool[key]

    def get_session(name: str, shape: tuple, x: np.ndarray) -> tuple:
        """这个形状的 session：池子里有就用（挪到 LRU 末尾）；没有就**在锁外建**、锁内双查再放（2026-09-24：原来建在锁里，
        一个新形状的 0.1 s 会挡住另一个在途请求查池子）。两个线程同时建同一个形状时后到的那份丢掉。"""
        nonlocal n_built, build_sec
        key = (name, shape)
        with pool_lock:
            if key in pool:
                pool[key] = pool.pop(key)           # 用过就挪到末尾（LRU）
                return pool[key]
        t_b = time.perf_counter()
        made = build(name, shape, x)
        with pool_lock:
            # **建了几个 / 淘汰了几次**（2026-09-19 复审第 2 条）：只有 `build_sec` 分不开
            # "每个新形状一次性的规划"（整片上摊薄）和"LRU 反复淘汰重建"（整片上持续付），
            # 而这两种对整片的预测是相反的。`evicted > 0` 就是后者在发生。
            build_sec += time.perf_counter() - t_b
            n_built += 1
            if key not in pool:
                return insert(key, made)
            pool[key] = pool.pop(key)
            hit = pool[key]
        with shared():                              # 别的线程先建好放进去了：自己这份在闸里放掉（出了 pool_lock 再放）
            made = None
        return hit

    def warm(spec: str) -> None:
        """后台预热（`--warm`）：逐个形状建 session + 用零输入跑一次（cuDNN 选算法在第一次 run 里），放进池子。
        池子里已经有的（真请求先到了）跳过；**只填空位、不淘汰**——预热不该把真在用的形状挤出去。出错只记一笔，不影响服务。"""
        nonlocal n_warmed
        try:
            name, _, shapes = spec.partition("=")
            if name not in models:
                return
            h = int(base[name].get_inputs()[0].shape[2]) if isinstance(base[name].get_inputs()[0].shape[2], int) else 48
            for bw in shapes.split(","):
                b, w = (int(v) for v in bw.lower().split("x"))
                shape = (b, 3, h, w)
                key = (name, shape)
                with pool_lock:
                    if key in pool or (a.max_sessions and len(pool) >= a.max_sessions):
                        continue
                x = np.zeros(shape, np.float32)
                made = build(name, shape, x)
                if not isinstance(made, Graph):
                    with shared():
                        made.run(None, {made.get_inputs()[0].name: x})
                with pool_lock:
                    kept = key not in pool and not (a.max_sessions and len(pool) >= a.max_sessions)
                    if kept:
                        insert(key, made)
                        n_warmed += 1
                if not kept:
                    with shared():                  # 真请求先建好了：预热这份在闸里放掉
                        made = None
        except Exception as exc:                     # noqa: BLE001
            print(f"[ort_server] 预热中断：{exc!r}", file=sys.stderr, flush=True)

    def infer(req: dict, buf, in_bytes: int) -> dict:
        """一次推理。**这段是两种模式共用的**——别抄第二份。"""
        name = req["model"]
        pre_ms = 0.0
        if "crops" in req:
            # **送的是原样的 uint8 裁剪**（2026-09-23，decode-buffer）：前处理在这个进程里做，不抢管线进程的 GIL
            t_p = time.perf_counter()
            crops, off = [], 0
            for h, w, c in req["crops"]:
                nb = h * w * c
                crops.append(np.ndarray((h, w, c), dtype=np.uint8, buffer=buf[off: off + nb]))
                off += nb
            x = rp.batch(crops, int(req["grid"]), int(req["batch"]))
            pre_ms = (time.perf_counter() - t_p) * 1000
        else:
            n = int(np.prod(req["shape"]))
            x = np.ndarray(tuple(req["shape"]), dtype=np.float32, buffer=buf[: n * 4])
        shape = tuple(x.shape)
        # **锁只罩着池子的账**（查 / 建 / LRU），推理本身不加锁（2026-09-23，decode-buffer）：
        # 原来共享服务一把全局锁罩住整次推理，三个 worker 的请求排成一队——wuwa-s2 上每段 rec 等 38~42 s、推理只有 11~14 s。
        # ORT 的 `InferenceSession.run` 本身允许多线程同时调，**同一个形状两个在途**也能叠起来（进程内实测批 1~16 吞吐 +22%~+38%，
        # 定文本的 argmax 逐位相同；`prob` 只在批 1 上差 ≤5e-4——批 1 的 kernel 串行两遍也会差，不是并发引起的）。
        # 只有 **CUDA Graph** session 仍逐个串行：图回放用的是固定地址的输入 / 输出缓冲
        if a.per_shape:
            sess, run_lock = get_session(name, shape, x)
        else:
            sess, run_lock = base[name], contextlib.nullcontext()
        with pool_lock:
            counters = {"built": n_built, "evicted": n_evicted, "build_ms": build_sec * 1000, "warmed": n_warmed}
        with run_lock, (sess.replay_gate() if isinstance(sess, Graph) else shared()):
            t0 = time.perf_counter()
            if isinstance(sess, Graph):
                outs = sess.run(np.ascontiguousarray(x))
            else:
                outs = sess.run(None, {sess.get_inputs()[0].name: x})
            dt = time.perf_counter() - t0
            sess = None                             # 跑的时候被淘汰了的话，最后一个引用在闸里放掉
            reap()
        off = in_bytes
        meta = []
        for o in outs:
            o = np.ascontiguousarray(o)
            np.ndarray(o.shape, dtype=o.dtype, buffer=buf[off: off + o.nbytes])[...] = o
            meta.append([o.dtype.str, list(o.shape)])
            off += o.nbytes
        return {"outs": meta, "infer_ms": dt * 1000, "shape": list(shape), "pre_ms": pre_ms, **counters}

    if a.warm and a.per_shape:
        for spec in a.warm.split(";"):
            threading.Thread(target=warm, args=(spec,), daemon=True, name="warm").start()
    if a.listen >= 0:
        return serve_socket(a, hello, infer)

    shm = shared_memory.SharedMemory(name=a.shm)
    buf = shm.buf
    if a.side_listen:                            # 管道之外再开旁路连接（同一个调用方的第 2..N 个在途请求）
        hello = {**hello, "port": serve_socket(a, hello, infer, background=True)}
    print(json.dumps(hello), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("bye"):
            break
        try:
            rep_ = infer(req, buf, a.in_bytes)
        except Exception as e:                   # noqa: BLE001  报给调用方（它会 raise），别带着 traceback 死在 DEVNULL 的 stderr 里
            rep_ = {"error": repr(e)[:2000]}
        print(json.dumps(rep_), flush=True)
    shm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
