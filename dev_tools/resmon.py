"""按进程的资源画像（Windows）：Job 里的每个进程各自的私有工作集、工作集、独占 / 共享显存，0.25 s 采一次（2026-10-05，resource 计划第 1 步）。

`jobcpu.py` 起命令时顺带开它，汇总写进旁注的 `res`，逐次采样写 `<旁注主名>.res.jsonl`。

* **归属按 Job 的进程表**（`JobObjectBasicProcessIdList`），不按命令行猜：机器上别的 python 不会混进来（2026-10-05 整片那次按命令行认，
  认不出的"other"后段涨到 1.16 GB、说不清是不是我们的）。角色名（`ort` / `decode` / `edge` / `ffmpeg` / `py`）只是读数时的标签，从命令行认。
* **计数器用常驻的 PDH 查询**（不是每个样本起一次 pwsh）：`\\GPU Process Memory(*)\\Dedicated Usage` / `Shared Usage`、
  `\\Process(*)\\Working Set - Private` / `Working Set` / `ID Process`。分片下每个 ffmpeg 只活 1–2 s，起一次 pwsh 就要 1.5–2 s，原来全漏。
  `Process(*)` 的实例名是"进程名#N"，按同一次查询里的 `ID Process` 对 pid。
* **显存上界**（`gpu_bound_mb`）：0.25 s 也可能漏掉短命的 ffmpeg，所以验收用上界 = 非 ffmpeg 进程同一时刻之和的峰值
  + 同时在世的 ffmpeg 最大数 × 单个 ffmpeg 见到过的最大显存（`ffmpeg_unit_mb`，可用 `RESMON_FFMPEG_UNIT_MB` 指定实测值）。
* `gpu_shared_peak_mb`：flowocr 各进程共享显存之和的峰值——"8 GB 卡"实测里它涨了就是溢到了系统内存（CUDA 系统内存回退）。

* **内存闸**（2026-10-05：一次 4 组测试把整机内存吃满、险些卡死）：这棵树私有工作集之和超过 `RESMON_PRIV_LIMIT_MB`（默认 10000）
  就杀掉 Job 里除 jobcpu 自己以外的全部进程，汇总记 `killed`（原因），命令以非零退出。不看整机可用内存：别的程序闲着的页能被换出和压缩（owner）。
  按物理内存设闸、不按提交量：CUDA 一趟正常就有约 10 GB 提交（WDDM 下显存分配也记提交），拿提交量卡会误杀。

开销：每拍约 9 ms CPU（4 Hz 下约一个核的 3.5%），记在 jobcpu 自己账上、从 CPU 秒里减掉，但它和被测命令抢同一台机器——A/B 两臂一样。

不是 Windows、或 PDH 起不来时 `ResMon(...)` 构造就抛，jobcpu 接住任何异常、旁注的 `res` 记一条 `error`、照样跑命令。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import time
from ctypes import wintypes
from pathlib import Path

_PDH_MORE_DATA = 0x800007D2
_FMT_LARGE = 0x00000400
_FMT_NOCAP100 = 0x00008000


class _Value(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]


class Pdh:
    """一个常驻的 PDH 查询：`read()` 返回 {pid: {"priv": B, "ws": B, "ded": B, "sh": B}}。"""

    PATHS = {"ded": r"\GPU Process Memory(*)\Dedicated Usage", "sh": r"\GPU Process Memory(*)\Shared Usage",
             "priv": r"\Process(*)\Working Set - Private", "ws": r"\Process(*)\Working Set", "pid": r"\Process(*)\ID Process"}

    def __init__(self) -> None:
        self.pdh = ctypes.WinDLL("pdh")
        for fn in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData", "PdhGetFormattedCounterArrayW", "PdhCloseQuery"):
            getattr(self.pdh, fn).restype = wintypes.DWORD      # 状态码是无符号的（PDH_MORE_DATA = 0x800007D2）
        self.q = wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.q)):
            raise OSError("PdhOpenQueryW 失败")
        self.c = {}
        self.misaligned = 0
        for k, p in self.PATHS.items():
            h = wintypes.HANDLE()
            st = self.pdh.PdhAddEnglishCounterW(self.q, p, None, ctypes.byref(h))
            if st:
                if k in ("ded", "sh"):           # 没有 WDDM GPU 计数器（老系统 / 没卡）：只记内存
                    continue
                raise OSError(f"PdhAddEnglishCounterW {p} 失败：{st:#x}")
            self.c[k] = h

    def _array(self, h) -> list[tuple[str, int]]:
        size, n = wintypes.DWORD(0), wintypes.DWORD(0)
        st = self.pdh.PdhGetFormattedCounterArrayW(h, _FMT_LARGE | _FMT_NOCAP100, ctypes.byref(size), ctypes.byref(n), None)
        if st != _PDH_MORE_DATA:
            return []
        buf = (ctypes.c_byte * size.value)()
        st = self.pdh.PdhGetFormattedCounterArrayW(h, _FMT_LARGE | _FMT_NOCAP100, ctypes.byref(size), ctypes.byref(n), buf)
        if st:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(_Item))
        return [(items[i].szName, int(items[i].FmtValue.largeValue)) for i in range(n.value)
                if items[i].FmtValue.CStatus in (0, 1)]      # PDH_CSTATUS_VALID_DATA / NEW_DATA

    def read(self) -> dict[int, dict[str, int]]:
        if self.pdh.PdhCollectQueryData(self.q):
            return {}
        out: dict[int, dict[str, int]] = {}
        pids = self._array(self.c["pid"])
        for k in ("priv", "ws"):
            vals = self._array(self.c[k])
            if len(vals) != len(pids) or any(a[0] != b[0] for a, b in zip(pids, vals)):
                self.misaligned += 1              # 同一次取数里实例对不齐（实测没见过）：这一拍的内存不要，记一笔
                continue
            for (nm, pid), (nm2, v) in zip(pids, vals):
                if nm == nm2 and pid:
                    out.setdefault(pid, {})[k] = v
        for k in ("ded", "sh"):
            if k not in self.c:
                continue
            for nm, v in self._array(self.c[k]):
                m = re.match(r"pid_(\d+)_", nm or "")
                if m:
                    d = out.setdefault(int(m.group(1)), {})
                    d[k] = d.get(k, 0) + v
        return out

    def close(self) -> None:
        self.pdh.PdhCloseQuery(self.q)


def job_pids(k32, job) -> list[int]:
    """Job 里现存进程的 pid（`JobObjectBasicProcessIdList`）。"""
    class L(ctypes.Structure):
        _fields_ = [("n_assigned", wintypes.DWORD), ("n_list", wintypes.DWORD), ("ids", ctypes.c_size_t * 4096)]
    lst = L()
    if not k32.QueryInformationJobObject(wintypes.HANDLE(job), 3, ctypes.byref(lst), ctypes.sizeof(lst), None):
        return []
    return [int(lst.ids[i]) for i in range(lst.n_list)]


def role_of(cmd: str) -> str:
    c = cmd.lower()
    if "ffmpeg" in c.split(" ", 1)[0]:
        return "ffmpeg"
    for k, r in (("ort_server", "ort"), ("decode_proc", "decode"), ("edge_proc", "edge")):
        if k in c:
            return r
    return "py"


class ResMon:
    """后台线程按 Job 的进程表采样；`stop()` 返回汇总 dict。"""

    def __init__(self, k32, job, series: Path | None, every: float = 0.25) -> None:
        self.k32, self.job, self.series, self.every = k32, job, series, every
        self.pdh = Pdh()
        self.me = os.getpid()
        self.roles: dict[int, str] = {}
        self.stop_ev = threading.Event()
        self.s = {"samples": 0, "priv_peak_mb": 0.0, "gpu_peak_mb": 0.0, "gpu_nonff_peak_mb": 0.0, "gpu_shared_peak_mb": 0.0,
                  "ffmpeg_max_n": 0, "ffmpeg_unit_mb": 0.0, "roles": {}}
        self.procs: dict[int, list] = {}         # pid -> [角色, 命令行摘要, 私有工作集峰值, 显存峰值]：同一角色好几个进程时（多组的 rec / det 两个服务）分得开
        try:
            self.fh = series.open("w", encoding="utf-8") if series else None
        except OSError:
            self.pdh.close()
            raise
        self.error = ""
        self.killed = ""
        self.priv_limit = float(os.environ.get("RESMON_PRIV_LIMIT_MB") or 10000)
        self.th = threading.Thread(target=self._loop, daemon=True, name="resmon")
        self.t0 = time.perf_counter()
        self.th.start()

    def _role(self, pid: int) -> str:
        if pid not in self.roles:
            try:
                import psutil
                self.roles[pid] = role_of(" ".join(psutil.Process(pid).cmdline()))
            except Exception:                     # 进程已退 / 拿不到命令行：按 py 记
                self.roles[pid] = "py"
        return self.roles[pid]

    def _kill(self, pids: list[int], why: str, quiet: bool = False) -> None:
        """越线：杀掉这棵树（jobcpu 自己也在 Job 里，所以不用 TerminateJobObject）。"""
        self.killed = why
        try:
            import psutil
            for p in pids:
                try:
                    psutil.Process(p).kill()
                except Exception:
                    pass
        except Exception:
            pass
        if not quiet:
            import sys
            print(f"[resmon] **内存闸**：{why}，已杀掉被测的进程树", file=sys.stderr, flush=True)

    def _brief(self, pid: int) -> str:
        """命令行里认得出用途的那一截（ORT 服务带哪几个模型、组名），读数时分进程用。"""
        try:
            import psutil
            cmd = psutil.Process(pid).cmdline()
        except Exception:
            return ""
        keep = [a for a in cmd if a.startswith(("rec=", "det=")) or a.endswith(".jsonl")]
        return " ".join(keep)[:120]

    def _loop(self) -> None:
        try:
            self._sample()
        except Exception as exc:                  # 采样出错不拖垮被测命令，但汇总里要看得出它半路停了
            self.error = f"{type(exc).__name__}: {exc}"

    def _sample(self) -> None:
        mb = 1 / (1 << 20)
        s = self.s
        while not self.stop_ev.wait(self.every):
            pids = [p for p in job_pids(self.k32, self.job) if p != self.me]
            live = set(pids)                      # 退出的进程从角色缓存里删掉：Windows 会复用 pid（整片要起几百个 ffmpeg）
            self.roles = {p: r for p, r in self.roles.items() if p in live}
            data = self.pdh.read()
            agg: dict[str, list[float]] = {}
            tot_priv = tot_gpu = nonff = shared = 0.0
            nff = 0
            for p in pids:
                d = data.get(p)
                if not d:
                    continue
                r = self._role(p)
                priv, ws, ded, sh = (d.get(k, 0) * mb for k in ("priv", "ws", "ded", "sh"))
                pp = self.procs.get(p)
                if pp is None:
                    pp = self.procs[p] = [r, self._brief(p), 0.0, 0.0]
                pp[2], pp[3] = max(pp[2], priv), max(pp[3], ded)
                a = agg.setdefault(r, [0.0, 0.0, 0.0, 0.0, 0])
                a[0] += priv; a[1] += ws; a[2] += ded; a[3] += sh; a[4] += 1
                tot_priv += priv; tot_gpu += ded; shared += sh
                if r == "ffmpeg":
                    nff += 1
                    s["ffmpeg_unit_mb"] = max(s["ffmpeg_unit_mb"], ded)
                else:
                    nonff += ded
            s["samples"] += 1
            if self.killed:                       # 越线之后每拍都再扫：父进程后来又起的也杀
                self._kill(pids, self.killed, quiet=True)
            elif tot_priv > self.priv_limit:
                self._kill(pids, f"私有工作集 {tot_priv:.0f} MB 超过 {self.priv_limit:.0f} MB")
            s["priv_peak_mb"] = max(s["priv_peak_mb"], tot_priv)
            s["gpu_peak_mb"] = max(s["gpu_peak_mb"], tot_gpu)
            s["gpu_nonff_peak_mb"] = max(s["gpu_nonff_peak_mb"], nonff)
            s["gpu_shared_peak_mb"] = max(s["gpu_shared_peak_mb"], shared)
            s["ffmpeg_max_n"] = max(s["ffmpeg_max_n"], nff)
            for r, a in agg.items():
                pk = s["roles"].setdefault(r, {"priv_mb": 0.0, "ws_mb": 0.0, "gpu_mb": 0.0, "gpu_shared_mb": 0.0, "n": 0})
                for i, k in enumerate(("priv_mb", "ws_mb", "gpu_mb", "gpu_shared_mb", "n")):
                    pk[k] = max(pk[k], a[i])
            if self.fh:
                self.fh.write(json.dumps({"t": round(time.perf_counter() - self.t0, 2), "priv": round(tot_priv), "gpu": round(tot_gpu),
                                          "roles": {r: [round(x) for x in a] for r, a in agg.items()}}) + "\n")

    def stop(self) -> dict:
        self.stop_ev.set()
        self.th.join(5)
        if not self.th.is_alive():                # 线程还在读就别关查询（下一拍会撞上已关的句柄）
            self.pdh.close()
        else:
            self.error = self.error or "采样线程 5 s 内没停"
        if self.fh:
            self.fh.close()
        s = self.s
        unit = float(os.environ.get("RESMON_FFMPEG_UNIT_MB") or s["ffmpeg_unit_mb"])
        s["gpu_bound_mb"] = s["gpu_nonff_peak_mb"] + s["ffmpeg_max_n"] * unit
        s["misaligned"] = self.pdh.misaligned
        top = sorted(self.procs.items(), key=lambda kv: -(kv[1][2] + kv[1][3]))[:12]
        s["procs"] = [{"pid": p, "role": v[0], "what": v[1], "priv_mb": round(v[2], 1), "gpu_mb": round(v[3], 1)} for p, v in top]
        if self.error:
            s["error"] = self.error
        if self.killed:
            s["killed"] = self.killed
        rnd = lambda v: round(v, 1) if isinstance(v, float) else v  # noqa: E731
        return {k: ({r: {kk: rnd(vv) for kk, vv in d.items()} for r, d in v.items()} if k == "roles" else v if k == "procs" else rnd(v))
                for k, v in s.items()}
