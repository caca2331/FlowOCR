"""**硬解中途出错 -> 整段改软解重跑时，第一趟的整棵进程树真的先退干净了吗**（Codex 复审第三 / 四轮的契约测试）。

病一（第三轮）：第一版在 `except HwaccelMismatch` 里**同步**起软解子进程，两套推理资源同时在卡上。
病二（第四轮）：改成外层监督之后，监督者只 `wait()` 了 worker 本身，Job 句柄一直开着——
`KILL_ON_JOB_CLOSE` 要等监督者最后退出才触发，**worker 起的孙进程（ORT 服务 / ffmpeg）可能活得比它长**，
第二趟照样和它们同时占卡。现在每趟结束都 `TerminateJobObject` + 等 Job 清零；Job 不可用时 psutil 兜底（按 ppid 求闭包）。

这个探针在**操作系统层面**核，而且要能"看见"孙进程：
* `FLOWOCR_FAULT_HWACCEL_AT=8`：硬解交付第 8 帧时注入一次故障；
* `FLOWOCR_FAULT_ORPHAN=<文件>`：故障那一刻 worker **故意留一个不收的孙进程**（模拟不听话的模型服务），pid 写进文件；
* 每 0.1 s 扫一遍进程树（psutil），第一趟 worker 活着时把它的**整棵树**（ORT 服务、ffmpeg……）都记进"第一趟"，
  再加上那个孤儿；每一轮都核它们还活没活（按 create_time 防 pid 复用）。

判据：①"第一趟的整棵树"最后一次被看见，早于第二趟 worker 第一次出现；②孤儿在第二趟开始之前就没了；
③产物 `complete`、`hwaccel_effective ''`、`hwaccel_fallback` 写着注入的故障；④监督者退出码 0。

    python dev_tools/hw_retry_check.py            # Job Object 那条路
    python dev_tools/hw_retry_check.py --no-job   # 假装 Job 建不起来：psutil 兜底那条路
    python dev_tools/hw_retry_check.py --extra "--decode-shards 3 --decode-block 5"   # 故障打在解码分片的某一块里
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "dev_tools"))
sys.path.insert(0, str(CODE / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr import paths  # noqa: E402
from archive import archive_root  # noqa: E402
os.chdir(paths.data_root())

ap = argparse.ArgumentParser()
ap.add_argument("--no-job", action="store_true", help="假装 Job Object 建不起来（FLOWOCR_FAULT_NO_JOB）")
ap.add_argument("--extra", default="", help="原样追加给 run_ocr2 的参数（如 `--decode-shards 3`：故障打在分片子进程里的某一块）")
a = ap.parse_args()

VIDEO = str(archive_root() / "gamestream/slices/wuwa-s2.mp4")     # h264：硬解试解过得了，故障才打得到中途
OUT = Path("tmp/hwretry/out.jsonl")
ORPHAN = Path("tmp/hwretry/orphan.pid")
OUT.parent.mkdir(parents=True, exist_ok=True)
PY = str(CODE / ".venv/Scripts/python.exe")   # 开发 venv（2026-09-22 起驱动脚本只认它；旧的 旧的 paddleocr 实验 venv 已不用）


def alive(pid: int, ct: float) -> bool:
    try:
        q = psutil.Process(pid)
        return abs(q.create_time() - ct) < 1e-3 and q.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def main() -> int:
    for f in (OUT, ORPHAN):
        f.unlink(missing_ok=True)
    env = dict(os.environ, FLOWOCR_FAULT_HWACCEL_AT="8", FLOWOCR_FAULT_ORPHAN=str(ORPHAN.resolve()))
    env.pop("FLOWOCR_OCR_WORKER", None)                          # 要从监督者那一层起
    if a.no_job:
        env["FLOWOCR_FAULT_NO_JOB"] = "1"
    cmd = [PY, "-m", "flowocr.extract.run_ocr2", VIDEO, "--out", str(OUT),   # 2026-09-22 起的正式入口（旧脚本路径已删）
           "--start", "0", "--end", "20", "--progress-every", "100000"] + a.extra.split()
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    sup = psutil.Process(p.pid)
    lines: list[str] = []
    th = threading.Thread(target=lambda: lines.extend(p.stdout), daemon=True)
    th.start()
    t0 = time.perf_counter()
    trip1: dict[int, float] = {}          # 第一趟整棵树：pid -> create_time
    trip1_last = -1.0                     # 第一趟里任何一个进程最后一次被看见
    trip2_first = None                    # 第二趟 worker 第一次出现
    orphan_last = -1.0
    orphan = None
    while p.poll() is None:
        now = time.perf_counter() - t0
        try:
            kids = sup.children(recursive=True)
        except psutil.Error:
            kids = []
        for c in kids:
            try:
                cl = c.cmdline()
                # worker 2026-09-22 起是 `-m flowocr.extract.run_ocr2` 起的（正规化阶段 B 第二刀），命令行里没有 run_ocr2.py 了
                if ("run_ocr2.py" in " ".join(cl) or "flowocr.extract.run_ocr2" in cl) and c.environ().get("FLOWOCR_OCR_WORKER"):
                    if cl[-2:] == ["--hwaccel", ""]:
                        trip2_first = now if trip2_first is None else trip2_first
                    else:
                        trip1.setdefault(c.pid, c.create_time())
                        for g in c.children(recursive=True):     # 第一趟 worker 的整棵树
                            try:
                                trip1.setdefault(g.pid, g.create_time())
                            except psutil.Error:
                                pass
            except psutil.Error:
                pass
        if orphan is None and ORPHAN.exists():
            try:
                pid = int(ORPHAN.read_text())
                orphan = (pid, psutil.Process(pid).create_time())
                trip1.setdefault(*orphan)
            except (ValueError, psutil.Error):
                pass
        if any(alive(pid, ct) for pid, ct in trip1.items()):
            trip1_last = now
        if orphan and alive(*orphan):
            orphan_last = now
        time.sleep(0.1)
    th.join(timeout=5)
    rc = p.returncode
    orphan_alive_at_end = bool(orphan and alive(*orphan))
    if orphan_alive_at_end:
        psutil.Process(orphan[0]).kill()
    meta = json.loads(OUT.read_text(encoding="utf-8").splitlines()[0])["_meta"] if OUT.exists() else {}
    before = trip2_first is not None and trip1_last < trip2_first
    print(f"[{'psutil 兜底' if a.no_job else 'Job Object'}] 监督者退出码 {rc}；第一趟整棵树 {len(trip1)} 个进程"
          f"（含孤儿 {orphan[0] if orphan else '没起来'}），最后一次看见 {trip1_last:.1f}s；"
          f"孤儿最后一次看见 {orphan_last:.1f}s；第二趟 worker 第一次出现 "
          f"{'—' if trip2_first is None else f'{trip2_first:.1f}s'}；**第一趟整棵树先于第二趟退干净：{'是' if before else '否'}**")
    print(f"产物：complete {meta.get('complete')}、hwaccel_effective {meta.get('hwaccel_effective')!r}、"
          f"hwaccel_fallback {meta.get('hwaccel_fallback', '')[:50]!r}；结束时孤儿还活着：{orphan_alive_at_end}")
    ok = (rc == 0 and orphan is not None and before and orphan_last < trip2_first and not orphan_alive_at_end
          and meta.get("complete") and meta.get("hwaccel_effective") == ""
          and "注入的故障" in meta.get("hwaccel_fallback", ""))
    print("✅" if ok else "❌ 不满足")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
