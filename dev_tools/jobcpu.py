"""跑一条命令，记它**连同全部子孙进程**的 CPU 秒和提交内存峰值，写一个 JSON 旁注（2026-09-25）。

    python dev_tools/jobcpu.py <旁注.cpu.json> -- <命令…>

为什么要它：墙钟 A/B 之外，owner 要"资源占用也一起报"（开发指南「A/B 的规矩」），而管线的 CPU 花在好几个进程里——
监督者、worker、ORT 服务（线程池自旋就在这里）、只解码进程、回抠进程、ffmpeg。只看 worker 自己的 `process_time` 会漏掉大头，
事后按进程名求和又会漏掉跑完就退的子进程。Windows 的 Job Object 记账（`JobObjectBasicAccountingInformation`）
把**已经退出的**成员也算进去：这里先把自己放进一个 Job，子进程生来就在里面（监督者再建的 Job 是嵌套的，照样计进来），
命令结束后读总账、减掉本进程自己那一点。

旁注字段：`cpu_sec`（用户 + 内核，不含本进程）、`user_sec` / `kernel_sec`、`wall_sec`（含起动，和 `_meta.wall_sec` 不是一个口径）、
`processes`（Job 里一共起过几个进程）、`peak_commit_mb`（Job 里**同一时刻**提交内存之和的峰值，`PeakJobMemoryUsed`）；
有 `nvidia-smi` 时另记**整卡**显存：`gpu_mem_base_mb`（命令起之前）、`gpu_mem_peak_mb`、`gpu_mem_delta_mb`（峰值减起前，0.25 s 采一次）——
WDDM 下看不到单进程显存，所以是整卡读数，桌面和别的程序的波动也在里面，只宜比同一批里的臂。采样进程在 Job 之外起，不算进 CPU 账。
不是 Windows、或 Job 建不起来时照样跑命令，旁注写 `{"error": …}`，退出码照传。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _job():
    """建一个 Job 并把本进程放进去；返回 (kernel32, 句柄, 查账函数) 或抛 OSError。"""
    import ctypes
    from ctypes import wintypes

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC_LIMIT), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class ACC(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    h = k32.CreateJobObjectW(None, None)
    if not h or not k32.AssignProcessToJobObject(h, k32.GetCurrentProcess()):
        raise OSError(ctypes.get_last_error(), "建不起 Job / 放不进去")

    def account() -> dict:
        acc, ext = ACC(), EXT()
        ok1 = k32.QueryInformationJobObject(wintypes.HANDLE(h), 1, ctypes.byref(acc), ctypes.sizeof(acc), None)
        ok2 = k32.QueryInformationJobObject(wintypes.HANDLE(h), 9, ctypes.byref(ext), ctypes.sizeof(ext), None)
        if not (ok1 and ok2):
            raise OSError(ctypes.get_last_error(), "查不到 Job 的账")
        return {"user_sec": acc.TotalUserTime / 1e7, "kernel_sec": acc.TotalKernelTime / 1e7,
                "processes": int(acc.TotalProcesses), "peak_commit_mb": round(ext.PeakJobMemoryUsed / (1 << 20), 1)}

    return account


class _GpuMem:
    """后台跑 `nvidia-smi -lms 250` 记整卡显存（MiB）；没有 nvidia-smi 就什么都不记。"""

    def __init__(self) -> None:
        import threading
        self.vals: list[int] = []
        try:
            self.p = subprocess.Popen(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-lms", "250"],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            self.p = None
            return
        self.th = threading.Thread(target=self._read, daemon=True)
        self.th.start()
        t0 = time.time()
        while not self.vals and time.time() - t0 < 3 and self.p.poll() is None:
            time.sleep(0.05)
        self.base = self.vals[0] if self.vals else None

    def _read(self) -> None:
        for ln in self.p.stdout:
            try:
                self.vals.append(int(ln.split(",")[0].strip()))   # 多卡时只看第一张
            except ValueError:
                pass

    def stop(self) -> dict:
        if self.p is None or self.base is None:
            return {}
        self.p.terminate()
        self.th.join(2)
        peak = max(self.vals)
        return {"gpu_mem_base_mb": self.base, "gpu_mem_peak_mb": peak, "gpu_mem_delta_mb": peak - self.base}


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    out, cmd = Path(argv[0]), argv[2:]
    gpu = _GpuMem()                                       # 先于 Job 起：采样进程不进 Job、不算 CPU
    try:
        account = _job() if os.name == "nt" else None
        err = "" if account else "不是 Windows：没有 Job 记账"
    except OSError as exc:
        account, err = None, f"Job 记账不可用：{exc}"
    t0 = time.perf_counter()
    try:
        rc = subprocess.call(cmd)
    except OSError as exc:                                # 命令本身起不来：旁注写原因，退出码 127（同 shell）
        out.write_text(json.dumps({"error": f"起不来：{exc}", "cmd": cmd}, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[jobcpu] 起不来：{exc}", file=sys.stderr)
        return 127
    wall = time.perf_counter() - t0
    if account is None:
        rec = {"error": err}
    else:
        rec = account()
        own = os.times()                                  # 本进程自己也在 Job 里：减掉
        rec["user_sec"] = round(rec["user_sec"] - own.user, 2)
        rec["kernel_sec"] = round(rec["kernel_sec"] - own.system, 2)
        rec["cpu_sec"] = round(rec["user_sec"] + rec["kernel_sec"], 2)
        rec["processes"] -= 1
    rec.update(gpu.stop())
    rec.update(wall_sec=round(wall, 2), rc=rc, cmd=cmd)
    out.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
