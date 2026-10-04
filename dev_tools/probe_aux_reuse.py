"""**辅助流的帧间连续性，能不能把"白读"和"真换字"分开**（decode-buffer §8.3，只读 obs + 解一遍视频，不跑 OCR）。

背景：quick 六段上，过 conf 门的真 rec 里 21%~61% 读出来和同一处上一次**逐字相同**（白读）——
复用判据（`reuse_v2._gate`）拿两个采样点、全分辨率、整框相关系数比，背景一动 / 框宽一抖 / 含数字就不敢放行。
而同一次解码里本来就有一路**全帧率**的低分辨率灰度辅助流（`--refine-scale 0.35`），两个采样点之间的每一帧都在。
这里问：拿那一路在两个采样点之间逐帧算的信号，能不能在**不放过真换字**的前提下省掉白读。

框对 = 相邻两个采样点上同一位置（IoU ≥ 0.5）的两条 obs，后一条的类别：

    same      后一条是真 rec、文本和前一条逐字相同（白读，想省的）
    changed   后一条是真 rec、文本不同（相似度 < 0.8）——**必须继续读的**
    near      真 rec、文本相近（0.8 ≤ 相似度 < 1）：OCR 抖动和一字之差混在一起，单独列
    reused    后一条是沿用（现在的门放行了）——参照：现行判据认为"没变"的样子

每个框对在辅助流上算（框按 scale 缩放、外扩 1 px；两个采样点之间含端点共 stride + 1 帧）：

    end_ncc   两端点的相关系数（= 现行像素门搬到低分辨率上）
    end_mad   两端点的平均绝对差 / 255
    step_mad  相邻两帧平均绝对差的最大值 / 255 —— 中间有没有**突变**
    step_ncc  相邻两帧相关系数的最小值

输出各类别在几个门上的放行率（放行 = 判"没变"、省 rec），以及 changed 被放行的逐条清单（这些就是错）。

    python dev_tools/probe_aux_reuse.py out/samples/q-gi-s2.jsonl --list tmp/auxreuse/gi-s2.tsv
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr.analyze import build_tracks as bt  # noqa: E402

PTS_RE = re.compile(r"n:\s*(\d+).*?pts_time:\s*(-?[\d.]+)")
KINDS = ("same", "near", "changed", "reused")
SIGNALS = ("end_ncc", "end_mad", "step_mad", "step_ncc")


def label(prev: dict, cur: dict) -> str:
    if cur.get("reused"):
        return "reused"
    if cur["text"] == prev["text"]:
        return "same"
    r = SequenceMatcher(None, cur["text"], prev["text"]).ratio()
    return "near" if r >= 0.8 else "changed"


def pairs_of(obs: list[dict], min_conf: float, stride: int) -> dict[int, list[tuple[dict, dict, str]]]:
    """按**后一个采样点**的帧号分组：{f_{k+1}: [(前, 后, 类别)]}。两条都要过 conf 门。

    **只配相邻的两个采样格**（`b - a == stride`）：像素是在 [b − stride, b] 上量的，隔了格（中间那格是空帧 / 全在 conf 门下）
    还去配，文字比的是 0 -> 60、像素只量 30 -> 60，前半段的变化就被记成"文字变了、像素没变"（2026-09-22 Codex 审计 P2 第 3 条）。"""
    by_f: dict[int, list[dict]] = defaultdict(list)
    for o in obs:
        if o["conf"] >= min_conf:
            by_f[o["frame"]].append(o)
    frames = sorted(by_f)
    out: dict[int, list] = {}
    for a, b in zip(frames, frames[1:]):
        if b - a != stride:
            continue
        got = []
        for o in by_f[b]:
            best = max(by_f[a], key=lambda p: bt.iou(p["box"], o["box"]))
            if bt.iou(best["box"], o["box"]) >= 0.5:
                got.append((best, o, label(best, o)))
        if got:
            out[b] = got
    return out


def ncc(x: np.ndarray, y: np.ndarray) -> float:
    x = x.astype(np.float32).ravel()
    y = y.astype(np.float32).ravel()
    x -= x.mean()
    y -= y.mean()
    d = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / d) if d > 1e-6 else 1.0     # 两块都平 = 没变


def decode(video: str, start: float, end: float, scale: float, rate: float):
    """软解、缩到 scale、灰度、全帧率；逐帧 yield (帧号, 帧)。帧号 = round(pts × rate)，和 obs 的 `frame` 同一套。"""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", video], capture_output=True, text=True)
    W, H = (int(v) for v in probe.stdout.strip().split(","))
    Ws, Hs = int(W * scale / 2) * 2, int(H * scale / 2) * 2
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info"]
    if start > 0:
        cmd += ["-ss", f"{start:.6f}", "-copyts"]
    if end > 0:
        # 输入端的 -to（绝对时间）。**别用输出端的 -t**：-copyts 下输出时间戳从 start 起算，
        # `-t 300` 会把所有帧当成超时丢掉，一个字节都不出（yuka 窗口实测）
        cmd += ["-to", f"{end:.6f}"]
    cmd += ["-i", video]
    cmd += ["-vf", f"scale={Ws}:{Hs},format=gray,showinfo", "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=Ws * Hs * 4)
    pts: list[float] = []

    import threading

    def drain() -> None:
        for raw in p.stderr:                       # type: ignore[union-attr]
            m = PTS_RE.search(raw.decode("utf-8", "replace"))
            if m and "showinfo" in raw.decode("utf-8", "replace"):
                pts.append(float(m.group(2)))
    th = threading.Thread(target=drain, daemon=True)
    th.start()
    k = 0
    try:
        while True:
            buf = p.stdout.read(Ws * Hs)           # type: ignore[union-attr]
            if len(buf) < Ws * Hs:
                break
            while len(pts) <= k and th.is_alive():   # showinfo 的行可能比像素晚到一点
                th.join(0.01)
            if len(pts) <= k:
                raise SystemExit(f"第 {k} 帧没有 pts（showinfo 行丢了），不编帧号")
            yield round(pts[k] * rate), np.frombuffer(buf, np.uint8).reshape(Hs, Ws), (W, H)
            k += 1
    finally:
        p.stdout.close()                           # type: ignore[union-attr]
        p.wait()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obs", help="2 fps 默认臂的 obs")
    ap.add_argument("--scale", type=float, default=None, help="辅助流缩放（默认取 obs 的 refine_scale）")
    ap.add_argument("--list", default=None, help="逐框对的信号（TSV），给调门 / 摆帧用")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()

    lines = Path(a.obs).read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])["_meta"]
    obs = [json.loads(x) for x in lines[1:] if x.strip()]
    if meta.get("timebase") != "pts" or not meta.get("complete"):
        raise SystemExit("obs 缺 timebase=pts 或 complete")
    min_conf = bt.make_parser().parse_args(["x", "--outdir", "-"]).min_conf
    scale = a.scale or meta["config"]["refine_scale"]
    o = max(obs, key=lambda x: x["t_us"])
    rate = o["frame"] / (o["t_us"] / 1e6)         # 帧号的计法（obs 自己的 frame / t 反推）
    from flowocr.extract.framegrid import meta_stride
    stride = meta_stride(meta)
    by_end = pairs_of(obs, min_conf, stride)

    rows = []
    # 每个区间 [f1 - stride, f1] 各自一份状态；一帧可以同时是上一个区间的终点和下一个区间的起点
    by_start = defaultdict(list)
    for f1 in by_end:
        by_start[f1 - stride].append(f1)
    open_: dict[int, dict[int, list]] = {}      # f1 -> {j: [首帧裁剪, 上一帧裁剪, step_mad, step_ncc]}
    seen_frames = 0
    last = max(by_end) if by_end else -1
    for fid, frame, (W, H) in decode(meta["video"], meta.get("start_sec") or 0.0, meta.get("end_sec") or 0.0,
                                     scale, rate):
        seen_frames += 1
        sx, sy = frame.shape[1] / W, frame.shape[0] / H

        def crop_of(pc):
            p, c = pc[0], pc[1]
            b = [min(p["box"][0], c["box"][0]), min(p["box"][1], c["box"][1]),
                 max(p["box"][2], c["box"][2]), max(p["box"][3], c["box"][3])]
            x0, y0 = max(0, int(b[0] * sx) - 1), max(0, int(b[1] * sy) - 1)
            x1, y1 = min(frame.shape[1], int(b[2] * sx) + 2), min(frame.shape[0], int(b[3] * sy) + 2)
            return b, frame[y0:y1, x0:x1].copy()

        for f1 in list(open_):
            if f1 < fid:                          # 终点没解到（源缺帧）：这一组作废
                del open_[f1]
                continue
            st = open_[f1]
            for j, pc in enumerate(by_end[f1]):
                b, crop = crop_of(pc)
                first, prev, smad, sncc = st[j]
                smad = max(smad, float(np.abs(crop.astype(np.int16) - prev).mean()) / 255)
                sncc = min(sncc, ncc(crop, prev))
                st[j] = [first, crop, smad, sncc]
                if fid == f1:
                    rows.append({"t": fid / rate, "kind": pc[2], "prev": pc[0]["text"], "cur": pc[1]["text"],
                                 "digit": any(ch.isdigit() for ch in pc[0]["text"] + pc[1]["text"]),
                                 "box": b, "end_ncc": ncc(crop, first),
                                 "end_mad": float(np.abs(crop.astype(np.int16) - first).mean()) / 255,
                                 "step_mad": smad, "step_ncc": sncc})
            if fid == f1:
                del open_[f1]
        for f1 in by_start.get(fid, ()):          # 这一帧是这些区间的起点
            open_[f1] = {}
            for j, pc in enumerate(by_end[f1]):
                _, crop = crop_of(pc)
                open_[f1][j] = [crop, crop, 0.0, 1.0]
        if fid >= last and not open_:
            break
    missing = sum(len(v) for v in by_end.values()) - len(rows)

    tag = a.tag or Path(a.obs).stem
    n = defaultdict(int)
    for r in rows:
        n[r["kind"]] += 1
    print(f"== {tag}：辅助流 {scale}，解了 {seen_frames} 帧；框对 {len(rows)}（{dict(n)}），起止帧没解到作废 {missing}")
    # 门：step_mad ≤ T 且 end_mad ≤ T（中间没突变、首尾也没漂）——一个旋钮，先看能不能分开
    print(f"{'门 (step_mad≤T 且 end_mad≤T)':30s}" + "".join(f"{k:>16s}" for k in KINDS) + "   放行的 changed")
    for T in (0.005, 0.01, 0.02, 0.03, 0.05):
        cells, bad = [], []
        for k in KINDS:
            ks = [r for r in rows if r["kind"] == k]
            ok = [r for r in ks if r["step_mad"] <= T and r["end_mad"] <= T]
            cells.append(f"{len(ok)}/{len(ks)}")
            if k == "changed":
                bad = ok
        print(f"{'T = ' + str(T):30s}" + "".join(f"{c:>16s}" for c in cells)
              + "   " + "; ".join(f"{r['prev']}->{r['cur']}" for r in bad[:4]) + (" …" if len(bad) > 4 else ""))
    if a.list:
        out = Path(a.list)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            fh.write("tag\tt\tkind\tdigit\tend_ncc\tend_mad\tstep_mad\tstep_ncc\tbox\tprev\tcur\n")
            for r in rows:
                fh.write("\t".join([tag, f"{r['t']:.3f}", r["kind"], str(int(r["digit"]))]
                                   + [f"{r[s]:.4f}" for s in SIGNALS]
                                   + [",".join(map(str, r["box"])), r["prev"].replace("\t", " "),
                                      r["cur"].replace("\t", " ")]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
