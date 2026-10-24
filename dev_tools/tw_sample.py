"""**和臂无关的打字机真值**：按随机时刻找下一次上屏，逐帧摆出来给人 / 模型标首字、全字。

现有的打字机真值（`data/gt/typewriter-*.json`）是从 fps 2 的产物里抽"恰好采到打字中途"的句子，量得了回抠、量不了采样网格。
这里的抽样**不看任何产物**：

1. `plan`：在窗口里按种子均匀抽时刻 t，每个 t 摆一张粗图（对话带的裁切，t 起每 0.5 s 一格、共 12 s），
   标注的人在粗图上找 **t 之后第一句开始打字的那一格**（没有就记 none）；
2. `fine`：在那一格前后按**源帧率逐帧**摆细图（每格标 pts），标注的人读出首字出现、全字出现两帧的 pts；
3. 标注写进 `data/gt/twfree-<份>.json`（`split = "free"`、`labeled_by`、抽样用的 `seed` / `t_draw`），
   `typewriter_eval` 和 `tw_bench` 照常读（`tw_bench` 的 `twfree` 那份）。

"随机时刻之后的第一句"偏向前面空档长的句子（长度偏差），但和任何采样网格的相位无关——这正是要量网格时需要的性质。

    python dev_tools/pyrun.py dev_tools/tw_sample.py plan <视频> --band 66,91 --n 10 --seed 1 [--start 秒 --end 秒] --outdir tmp/x
    python dev_tools/pyrun.py dev_tools/tw_sample.py fine <视频> --band 66,91 --at 245.75 [--before 0.3 --after 1.2] --outdir tmp/x
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np


def band_of(frame, band: tuple[float, float], xband: tuple[float, float] = (0.0, 100.0)):
    h, w = frame.shape[:2]
    return frame[int(h * band[0] / 100):int(h * band[1] / 100), int(w * xband[0] / 100):int(w * xband[1] / 100)]


def read_span(video: str, t0: float, t1: float, every: float = 0.0):
    """[t0, t1) 里的帧（pts 秒, 帧）。`every` > 0 时每隔这么久取一帧（取 pts 不早于格点的第一帧），否则逐帧。
    先 seek 到 t0 前 2 s 再顺序读，pts 取 `CAP_PROP_POS_MSEC`（读之后，对齐 ffprobe 的 pts_time）。"""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"打不开 {video}")
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t0 - 2.0) * 1000)
    out, nxt = [], t0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        if t < t0 - 1e-6:
            continue
        if t >= t1:
            break
        if every <= 0 or t >= nxt - 1e-6:
            out.append((t, fr))
            while every > 0 and nxt <= t + 1e-6:     # 源里有缺口时格点一次跳过几格，别连着收
                nxt += every
    cap.release()
    return out


def sheet(frames, band, xband, cols: int, width: int) -> np.ndarray:
    """逐格竖着拼成 cols 列，每格左上角写 pts（秒，3 位小数）。"""
    tiles = []
    for t, fr in frames:
        b = band_of(fr, band, xband)
        s = width / b.shape[1]
        b = cv2.resize(b, (width, max(1, int(b.shape[0] * s))))
        cv2.rectangle(b, (0, 0), (150, 24), (0, 0, 0), -1)
        cv2.putText(b, f"{t:.3f}", (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
        tiles.append(b)
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    return np.vstack(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("plan", "fine"))
    ap.add_argument("video")
    ap.add_argument("--band", default="66,91", help="对话带的上下沿（画面高的百分比）")
    ap.add_argument("--xband", default="0,100", help="对话带的左右沿（画面宽的百分比）")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0, help="0 = 到片尾")
    ap.add_argument("--span", type=float, default=12.0, help="plan：粗图从 t 起看多久")
    ap.add_argument("--step", type=float, default=0.5, help="plan：粗图每格隔多久")
    ap.add_argument("--at", type=float, help="fine：粗图上标出的那一格的 pts")
    ap.add_argument("--before", type=float, default=0.3)
    ap.add_argument("--after", type=float, default=1.2)
    a = ap.parse_args()
    band = tuple(float(x) for x in a.band.split(","))
    xband = tuple(float(x) for x in a.xband.split(","))
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "plan":
        cap = cv2.VideoCapture(a.video)
        dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1e-6)
        cap.release()
        hi = (a.end or dur) - a.span
        rnd = random.Random(a.seed)
        ts = sorted(round(rnd.uniform(a.start, hi), 3) for _ in range(a.n))
        man = {"video": a.video, "band": band, "xband": xband, "seed": a.seed, "window": [a.start, a.end or dur],
               "span": a.span, "step": a.step, "draws": []}
        for k, t in enumerate(ts):
            fr = read_span(a.video, t, t + a.span, a.step)
            p = out / f"plan-{k:02d}-{t:.3f}.jpg"
            cv2.imwrite(str(p), sheet(fr, band, xband, 3, 640), [cv2.IMWRITE_JPEG_QUALITY, 85])
            man["draws"].append({"k": k, "t_draw": t, "sheet": p.name})
            print(p)
        (out / "plan.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        if a.at is None:
            raise SystemExit("fine 要 --at")
        fr = read_span(a.video, a.at - a.before, a.at + a.after)
        p = out / f"fine-{a.at:.3f}.jpg"
        cv2.imwrite(str(p), sheet(fr, band, xband, 4, 480), [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(p, f"{len(fr)} 帧")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
