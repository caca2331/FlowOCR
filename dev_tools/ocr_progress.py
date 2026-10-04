r"""从 OCR 产物本身读进度，而不是从日志读。

存在的理由（2026-09-05）：`input1-4` 的整片 OCR 在后台跑，日志两个小时没动，
看上去像是断了。**其实没断**——罪魁是驱动脚本里的 `... 2>&1 | grep -v "Warning\|..."`：
grep 的输出是文件时走**块缓冲**（4 KB），进度行攒不满一块就不落盘。
复现（本目录下就能跑）：

    ( for i in $(seq 1 5); do echo "line $i"; sleep 0.2; done | grep -v NOPE > a.log ) &
    sleep 0.7; stat -c%s a.log      # -> 0
    # 加 --line-buffered 后同一时刻 -> 28

所以：**判断后台活没活，看产物，不看日志。** 日志是经过管道的二手信息，
产物是一手的。这跟项目里"把每一层的产物分别数一遍"是同一条原则。

顺带一条：读**正在被写**的文件大小用 bash 的 `stat`。PowerShell 的目录枚举
（`Get-ChildItem <目录>`）在这次事故里对一个已经 77 MB 的文件报了 `0 字节 / 13:29`，
之后又自己恢复正常——没能稳定复现，机制存疑，但既然踩过就绕开。

用法：
    python dev_tools/ocr_progress.py out/yuka/i2-full.jsonl
    python dev_tools/ocr_progress.py out/yuka/*.jsonl
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

TAIL_BYTES = 65536


def _duration_sec(video: str) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return None


def _last_frame(path: Path) -> int | None:
    """从文件尾部往回找最后一条带 `frame` 的记录。

    不整文件读：产物是几十上百 MB，而且**正在被写**——最后一行很可能是半行。
    半行直接丢掉，不要试图补全。
    """
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - TAIL_BYTES))
        chunk = fh.read()
    for ln in reversed(chunk.split(b"\n")):
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            continue          # 半行 / 非 JSON
        if "frame" in rec:
            return int(rec["frame"])
    return None


def report(path: Path) -> None:
    if not path.exists():
        print(f"{path.name}: 还没开始")
        return
    size = path.stat().st_size
    age = time.time() - path.stat().st_mtime
    with path.open("r", encoding="utf-8") as fh:
        head = fh.readline()
    try:
        meta = json.loads(head).get("_meta", {})
    except Exception:
        meta = {}
    video, fps = meta.get("video"), meta.get("src_fps")
    frame = _last_frame(path)

    bits = [f"{path.name}: {size/1e6:.1f} MB"]
    if frame is not None and fps:
        done = frame / fps
        total = _duration_sec(video) if video else None
        if total:
            pct = 100 * done / total
            # 速率按"从文件第一次落盘到现在"估，比日志里的滚动估计粗，但够用
            bits.append(f"{done/60:.0f}/{total/60:.0f} min = {pct:.1f}%")
        else:
            bits.append(f"已处理 {done/60:.0f} min")
    # mtime 才是"活没活"的判据；日志不动不代表进程不动
    bits.append(f"{age:.0f}s 前还在写" if age < 120 else f"**{age/60:.0f} 分钟没动过**")
    print("  ".join(bits))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    for a in args:
        report(Path(a))
