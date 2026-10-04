"""进程内解码的候选探针（decode-buffer §8.9 / §9.5.2 "帧留在原处不搬"的前置，2026-09-22）。

候选：PyAV 软解 / PyAV 硬解（帧下传，核像素）/ PyAV 硬解帧留显存（`is_hw_owned`，量速度）/
PyNvVideoCodec（主机内存输出核像素、显存输出量速度）。基准：ffmpeg 命令行软解（生产路径的时间轴：带 edit list、`-copyts`），
`framemd5` **只取视频流**（`-map 0:v:0`；混进音轨会用音频的 pts 覆盖视频帧号，Codex 复审 P2）逐帧给 pts + nv12 的 md5。

PyAV 的 av1 默认挑 `libdav1d`（纯软解，配 hwaccel 就报 "no stream is compatible"），硬解臂改用 FFmpeg 原生的 `av1` 解码器；
有前摇的 av1 从文件头起解照命令行那套修（`-ignore_editlist` + 按整数刻度平移，`framesource.av1_preroll_ticks`，判据只有一份）。

每个候选报：
  交付   解出的帧的 pts 集合对基准：少了几帧（首个）、多了几帧（首个）——帧号 = round(t × 名义帧率)
  像素   2 fps 格点帧（帧号 % 30 == 0）nv12 逐字节比 md5：不同几帧（留显存的臂不核）
  速度   整窗解完（每帧都解）、只把格点帧转成 nv12 数组：墙钟、进程 CPU（process_time，含解码线程）

    <inproc-decode 实验的 venv>/Scripts/python.exe dev_tools/inproc_decode_probe.py <video> --start 0 --end 300 [--fps 60]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr.extract.framesource import av1_preroll_ticks  # noqa: E402   前摇的判据只有一份

STRIDE = 30


def ref_framemd5(video: str, start: float, end: float, fps: float) -> dict[int, str]:
    """ffmpeg 命令行软解：帧号 -> nv12 的 md5（全帧率，窗口内，**只有视频流**）。"""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.6f}", "-copyts"]
    if end > 0:
        cmd += ["-to", f"{end:.6f}"]
    cmd += ["-i", video, "-map", "0:v:0", "-vf", r"select='gte(t\,0)',format=nv12", "-fps_mode", "passthrough",
            "-f", "framemd5", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"基准 ffmpeg 失败（{res.returncode}）：{res.stderr[-300:]}")
    return parse_framemd5(res.stdout, fps)


def parse_framemd5(text: str, fps: float) -> dict[int, str]:
    """`framemd5` 输出 -> {帧号: md5}。只认第 0 路（`-map 0:v:0` 之后就是那路视频）；帧号重复直接报错（不许静默覆盖）。"""
    tb = None
    got: dict[int, str] = {}
    for line in text.splitlines():
        if line.startswith("#tb 0:"):
            a, b = line.split(":")[1].strip().split("/")
            tb = int(a) / int(b)
        if line.startswith("#") or not line.strip():
            continue
        p = [x.strip() for x in line.split(",")]
        if p[0] != "0":
            continue
        if tb is None:
            raise SystemExit("framemd5 没有第 0 路的时间基")
        i = round(int(p[2]) * tb * fps)
        if i in got:
            raise SystemExit(f"framemd5 帧号 {i} 重复（时间基 / 帧率对不上？）")
        got[i] = p[5]
    return got


def md5_nv12(a: np.ndarray) -> str:
    return hashlib.md5(np.ascontiguousarray(a).tobytes()).hexdigest()


def pyav_frames(video: str, hw: bool, hw_owned: bool):
    """-> (帧迭代器, 时间基, 平移刻度)。硬解时 av1 换原生解码器；有前摇的 av1 从文件头起解走平移修法。"""
    import av
    shift = av1_preroll_ticks(video) if hw else 0
    cont = av.open(video, options={"ignore_editlist": "1"} if shift else {})
    st = cont.streams.video[0]
    tb = st.time_base
    if not hw:
        st.thread_type = "AUTO"
        return cont, cont.decode(st), tb, shift
    acc = av.codec.hwaccel.HWAccel(device_type="cuda", allow_software_fallback=False, is_hw_owned=hw_owned)
    if st.codec_context.codec.name != "libdav1d":
        cont.close()
        cont = av.open(video, hwaccel=acc, options={"ignore_editlist": "1"} if shift else {})
        st = cont.streams.video[0]
        return cont, cont.decode(st), tb, shift
    # av1：libdav1d 不支持 hwaccel，手动建原生 av1 解码器、自己喂包
    cc = av.codec.CodecContext.create("av1", "r", hwaccel=acc)
    cc.extradata = st.codec_context.extradata
    cc.width, cc.height = st.codec_context.width, st.codec_context.height
    cc.pix_fmt = st.codec_context.pix_fmt
    cc.open()

    def gen():
        for pkt in cont.demux(st):
            if pkt.size:
                yield from cc.decode(pkt)
        yield from cc.decode(None)
    return cont, gen(), tb, shift


def run_pyav(video: str, start: float, end: float, fps: float, hw: bool, check: bool,
             hw_owned: bool = False) -> dict:
    c0, t0 = time.process_time(), time.perf_counter()
    cont, frames, tb, shift = pyav_frames(video, hw, hw_owned)
    if start > 0 and hw and cont.streams.video[0].codec_context.codec.name == "libdav1d":
        cont.close()     # 手动喂包的原生 av1 解码器没接 seek（要冲解码器）；探针只测它从文件头起
        raise RuntimeError("PyAV 硬解的 av1 手动喂包路径没接 seek，这一臂只测从文件头起")
    if shift and start > 0:
        cont.close()
        raise RuntimeError("前摇平移只配从文件头起解（同 framesource.build_ffmpeg_cmd 的约束）")
    if start > 0:
        st = cont.streams.video[0]
        cont.seek(int(start / st.time_base), stream=st, backward=True)
    ids, md5, n_conv = [], {}, 0
    for fr in frames:
        if fr.pts is None:
            continue
        t = float((fr.pts - shift) * tb)
        if t < max(start, 0.0) - 1e-6:
            continue
        if end > 0 and t >= end:
            break
        i = round(t * fps)
        ids.append(i)
        if i % STRIDE == 0:
            n_conv += 1
            if not hw_owned:
                a = fr.reformat(format="nv12").to_ndarray()
                if check:
                    md5[i] = md5_nv12(a)
    cont.close()
    return {"ids": ids, "md5": md5, "wall": time.perf_counter() - t0, "cpu": time.process_time() - c0,
            "conv": n_conv, "shift": shift}


def vote_shift(raw: list[tuple[int, str | None]], by_md5: dict[str, list[int]]) -> int:
    """像素定偏移：每一帧对"所有同 md5 的基准帧号 − 它自己的帧号"各投一票，取票最多的；平票取绝对值小的。
    **静止画面里同一个 md5 会出现在很多帧上**——只记最后一次出现（`{md5: 帧号}`）会让偏移落到最后那个位置（Codex 复审 P2）。"""
    votes: Counter = Counter()
    for i, m in raw:
        for k in by_md5.get(m, ()):
            votes[k - i] += 1
    if not votes:
        return 0
    return max(votes, key=lambda d: (votes[d], -abs(d)))


def time_base(video: str) -> float:
    import av
    with av.open(video) as c:
        return float(c.streams.video[0].time_base)


def run_nvc(video: str, start: float, end: float, fps: float, device: bool, check: bool,
            ref: dict | None = None, shift: int | None = None) -> dict:
    """PyNvVideoCodec：`getPTS()` 是时间基刻度、**不经过 edit list**（wuwa-s2 首帧 −256 = −1 帧，ffmpeg 那边是 0）。
    帧号偏移不猜：主机内存那一臂拿前 120 帧的像素去对基准，量出来（`shift`）；显存那一臂沿用它。"""
    import PyNvVideoCodec as nvc
    tb = time_base(video)
    c0, t0 = time.process_time(), time.perf_counter()
    d = nvc.SimpleDecoder(video, gpu_id=0, use_device_memory=device)
    n_total = d.get_stream_metadata().num_frames
    if start > 0:
        d.seek_to_index(d.get_index_from_time_in_seconds(start))
    raw = []                      # (未平移的帧号, md5 或 None)
    n_conv = 0
    by_md5: dict[str, list[int]] = defaultdict(list)
    for k, v in (ref or {}).items():
        by_md5[v].append(k)

    def settle_shift() -> int:
        return vote_shift([(i, m) for i, m in raw[:120]], by_md5)

    while len(raw) < n_total:
        batch = d.get_batch_frames(64)
        if not batch:
            break
        stop = False
        for f in batch:
            i = round(f.getPTS() * tb * fps)
            if shift is None and len(raw) >= 120 and ref is not None:
                shift = settle_shift()
            s = shift if shift is not None else 0
            # 没有基准、也没有前一臂给的偏移（单独跑显存臂）时按 0 收口：最多多解一两帧，不会解完整片（Codex 复审 P2）
            if end > 0 and (i + s) / fps >= end and (shift is not None or ref is None):
                stop = True
                break
            m = None
            if check and not device and (shift is None or (i + s) % STRIDE == 0):
                m = md5_nv12(np.from_dlpack(f))
            if (i + s) % STRIDE == 0:
                n_conv += 1
            raw.append((i, m))
        if stop:
            break
    wall, cpu = time.perf_counter() - t0, time.process_time() - c0
    if shift is None and ref is not None:
        shift = settle_shift()
    shift = shift or 0
    if end > 0:
        raw = [(i, m) for i, m in raw if (i + shift) / fps < end]
    ids = [i + shift for i, _ in raw]
    md5 = {i + shift: m for i, m in raw if m is not None and (i + shift) % STRIDE == 0}
    dup = len(ids) - len(set(ids))
    return {"ids": ids, "md5": md5, "wall": wall, "cpu": cpu, "conv": n_conv, "shift": shift,
            "raw_head": [i for i, _ in raw[:3]], "decoded": len(ids), "dup_pts": dup}


def report(name: str, ref: dict, r: dict, start: float, end: float, fps: float) -> dict:
    lo, hi = round(start * fps), round(end * fps) if end > 0 else max(ref)
    want = {i for i in ref if lo <= i < hi}
    got = set(r["ids"])
    miss, extra = sorted(want - got), sorted(got - want)
    bad = [i for i, m in r["md5"].items() if ref.get(i) != m]
    row = {"arm": name, "delivered": len(got), "want": len(want), "miss": len(miss), "miss_head": miss[:5],
           "extra": len(extra), "extra_head": extra[:5], "md5_checked": len(r["md5"]), "md5_bad": len(bad),
           "bad_head": bad[:5], "wall": round(r["wall"], 2), "cpu": round(r["cpu"], 2), "conv": r["conv"]}
    for k in ("shift", "raw_head", "decoded", "dup_pts"):
        if k in r:
            row[k] = r[k]
    print(json.dumps(row, ensure_ascii=False))
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0)
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--arms", default="pyav-sw,pyav-hw,pyav-hwdev,nvc-host,nvc-dev")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    t0 = time.perf_counter()
    ref = ref_framemd5(a.video, a.start, a.end, a.fps)
    print(f"基准（ffmpeg 软解 framemd5）{len(ref)} 帧，{time.perf_counter() - t0:.1f} s")
    rows = []
    shift_seen = None
    for arm in a.arms.split(","):
        try:
            if arm == "pyav-sw":
                r = run_pyav(a.video, a.start, a.end, a.fps, hw=False, check=True)
            elif arm == "pyav-hw":
                r = run_pyav(a.video, a.start, a.end, a.fps, hw=True, check=True)
            elif arm == "pyav-hwdev":
                r = run_pyav(a.video, a.start, a.end, a.fps, hw=True, check=False, hw_owned=True)
            elif arm == "nvc-host":
                r = run_nvc(a.video, a.start, a.end, a.fps, device=False, check=True, ref=ref)
                shift_seen = r["shift"]
            elif arm == "nvc-dev":
                r = run_nvc(a.video, a.start, a.end, a.fps, device=True, check=False, shift=shift_seen)
            else:
                raise SystemExit(f"不认识的臂 {arm}")
        except Exception as e:                                   # noqa: BLE001
            print(json.dumps({"arm": arm, "error": f"{type(e).__name__}: {e}"[:300]}, ensure_ascii=False))
            continue
        rows.append(report(arm, ref, r, a.start, a.end, a.fps))
    if a.json:
        with open(a.json, "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps({"video": a.video, "start": a.start, "end": a.end, **row}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
