"""读 `run_ocr2 --timeline` 的各级时间线：谁在什么时候堵谁（decode-buffer §8.17）。

    python dev_tools/timeline_report.py tmp/tl/gi-s1.tl.jsonl [--series 10]

同目录下的 `<路径>.edge.jsonl` / `<路径>.decode.jsonl`（子进程写的）自动并进来。时刻都是 `perf_counter`
（Windows 上是系统级的 QPC，各进程同一根轴；文件头的锚点用来核对）。

`STAGE` 只有各段总和；这里按 0.1 ms 栅格化每个线程的区间，然后**顺着等待链往下问**：
主线程等 det 的时候 det 线程在干嘛？det 线程等帧的时候取帧线程在干嘛？取帧线程等管道的时候，是辅助流把帧环堵满了
（同一个 ffmpeg 停）、还是解码器本身没出帧？主线程等 rec / 等回抠的时候，那边是在忙还是闲着？
只统计第一帧到最后一帧之间（起动和收尾另算）。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

import numpy as np

BIN = 0.0001
"""栅格 0.1 ms、两端四舍五入（2026-09-23 改）：原来 1 ms 且起点向下、终点向上取整，一个几微秒的区间也记满一格——
辅助流那种每 5 分钟 1.8 万个短区间被放大十来倍（逐个相加 6.1 s 的 `aux_pts_wait` 报成 16.5 s，decode-buffer §8.22）。"""


def load(path: Path) -> list[dict]:
    """主文件 + 同名子进程文件的全部事件，每条带上 `role`。"""
    out = []
    for p in [path, *sorted(path.parent.glob(path.name + ".*.jsonl"))]:
        lines = p.read_text(encoding="utf-8").splitlines()
        role = json.loads(lines[0])["_meta"]["role"]
        for ln in lines[1:]:
            e = json.loads(ln)
            e["role"] = role
            out.append(e)
    return out


class Raster:
    """(角色, 线程, 种类) -> 布尔栅格（0.1 ms 一格），只覆盖 [t_lo, t_hi)。"""

    def __init__(self, ev: list[dict], t_lo: float, t_hi: float) -> None:
        self.t_lo, self.n = t_lo, int(np.ceil((t_hi - t_lo) / BIN))
        self.m: dict[tuple, np.ndarray] = {}
        for e in ev:
            if e["t1"] <= e["t0"]:
                continue
            a = max(0, int(round((e["t0"] - t_lo) / BIN)))
            b = min(self.n, int(round((e["t1"] - t_lo) / BIN)))
            if b <= a:
                continue
            key = (e["role"], e["th"], e["k"])
            arr = self.m.get(key)
            if arr is None:
                arr = self.m[key] = np.zeros(self.n, bool)
            arr[a:b] = True

    def kind(self, k: str, role: str | None = None, th: str | None = None, not_th: str | None = None) -> np.ndarray:
        """某种区间在所有匹配线程上的并集。"""
        out = np.zeros(self.n, bool)
        for (r, t, kk), arr in self.m.items():
            if kk == k and (role is None or r == role) and (th is None or t == th) and (not_th is None or t != not_th):
                out |= arr
        return out


def sec(a: np.ndarray) -> float:
    return float(a.sum()) * BIN


def split(label: str, cond: np.ndarray, parts: list[tuple[str, np.ndarray]]) -> None:
    """`cond` 为真的那些时刻里，各 `parts` 占多少（按顺序互斥：前面的先认领）；剩下的记"其它"。"""
    tot = sec(cond)
    if tot <= 0:
        print(f"  {label}：0 s")
        return
    left = cond.copy()
    cells = []
    for name, arr in parts:
        hit = left & arr
        cells.append(f"{name} {sec(hit):5.1f} s（{sec(hit) / tot:4.0%}）")
        left &= ~arr
    cells.append(f"其它 {sec(left):5.1f} s（{sec(left) / tot:4.0%}）")
    print(f"  {label} {tot:5.1f} s：" + "、".join(cells))


def server_split(ev: list[dict], t_lo: float, t_hi: float) -> None:
    """ORT 服务那份（`ort` 角色）按请求累加，和客户端这边等推理的时间对账：差的就是服务端请求之外的（IPC、排队、收发）。
    区间按请求逐个相加（两路 rec 并发时会重叠，所以不栅格化）。**先选请求、再收它的各段**：请求（`ort_req`）起点落在主循环里的才算，
    取 session / 前处理 / 等闸 / 推理 / 写输出按"同一线程上落在哪个请求里"归过去，跨窗口边的请求整个进或整个不进。
    客户端那边按自己的起点筛：两边的请求数对不上时（窗口边上的请求一边算了、一边没算），"服务端请求之外"含这份集合差，
    不能全归到 IPC / 排队上，报告里会标出来。"""
    inside = [e for e in ev if t_lo <= e["t0"] < t_hi]
    reqs: dict = {}                              # 服务线程 -> [(起, 止, 算不算)]，按起点排
    for e in ev:
        if e["role"] == "ort" and e["k"] == "ort_req":
            reqs.setdefault(e["th"], []).append((e["t0"], e["t1"], t_lo <= e["t0"] < t_hi))
    for v in reqs.values():
        v.sort()

    def counted(e) -> bool:
        v = reqs.get(e["th"], [])
        i = bisect.bisect_right(v, (e["t0"] + 1e-6, float("inf"), True)) - 1
        return i >= 0 and e["t1"] <= v[i][1] + 1e-6 and v[i][2]
    srv = [e for e in ev if e["role"] == "ort" and e["k"].startswith("ort_") and counted(e)]
    if not srv:
        return
    model = lambda e: e["a"][0] if isinstance(e["a"], list) else e["a"]
    print("\n推理服务（按请求累加，秒）：")
    for name, client_kind in (("rec", "rec_infer"), ("det", "det_infer")):
        tot = {k: sum(e["t1"] - e["t0"] for e in srv if e["k"] == k and model(e) == name)
               for k in ("ort_req", "ort_sess", "ort_pre", "ort_wait", "ort_run", "ort_out")}
        n = sum(1 for e in srv if e["k"] == "ort_req" and model(e) == name)
        if not n:
            continue
        cli_ev = [e for e in inside if e["k"] == client_kind and e["role"] == "main"]
        cli = sum(e["t1"] - e["t0"] for e in cli_ev)
        rest = tot["ort_req"] - tot["ort_sess"] - tot["ort_pre"] - tot["ort_wait"] - tot["ort_run"] - tot["ort_out"]
        print(f"  {name} {n} 次：客户端等 {cli:6.2f}，服务端请求 {tot['ort_req']:6.2f} = 取 session {tot['ort_sess']:5.2f} + 前处理 {tot['ort_pre']:5.2f}"
              f" + 准入 / 等闸 {tot['ort_wait']:5.2f} + 推理 {tot['ort_run']:6.2f} + 写输出 {tot['ort_out']:5.2f} + 其它 {rest:5.2f}；服务端请求之外 {cli - tot['ort_req']:5.2f}"
              + ("" if len(cli_ev) == n else f"（客户端 {len(cli_ev)} 次、服务端 {n} 次，差值含窗口边上的请求）"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("timeline", type=Path)
    ap.add_argument("--series", type=float, default=0, help="再按这么多秒一格打时间序列（看突发 / 相位）")
    args = ap.parse_args()

    ev = load(args.timeline)
    frames = [e["t0"] for e in ev if e["k"] == "frame" and e["role"] == "main"]
    if not frames:
        print("时间线里没有 frame 标记（主循环没跑？）", file=sys.stderr)
        return 1
    t_lo, t_hi = min(frames), max(frames)
    R = Raster(ev, t_lo, t_hi)
    wall = t_hi - t_lo
    print(f"主循环 {len(frames)} 帧、{wall:.1f} s（第一帧到最后一帧；起动 / 收尾不在内）\n")

    # 起动 / 收尾：run_ocr2 在各段分界打的 boot 标记（起表之前的 Python 起动、监督者那一层不在这里）
    boot = sorted([(e["t0"], e["a"]) for e in ev if e["k"] == "boot" and e["role"] == "main"]
                  + [(t_lo, "第一帧"), (t_hi, "最后一帧")])
    if len(boot) > 2:
        print("起动 / 收尾（各段到下一个分界的秒数）：")
        print("  " + "、".join(f"{a} -> {b} {t1 - t0:.2f}" for (t0, a), (t1, b) in zip(boot, boot[1:])))
        print()

    print("各线程账（秒，同一线程同种区间取并集）：")
    rows: dict[tuple, float] = {}
    for (r, t, k), arr in R.m.items():
        rows[(r, t, k)] = sec(arr)
    for (r, t, k), v in sorted(rows.items()):
        if v >= 0.05:
            print(f"  {r:5} {t:22} {k:16} {v:7.2f}")

    main_th = dict(role="main", th="MainThread")
    w_det = R.kind("wait_det", **main_th)
    w_rec = R.kind("rec_wait", **main_th)
    w_edge = R.kind("wait_batch", role="main")
    decide = R.kind("decide", **main_th)
    commit = R.kind("commit", **main_th)
    rec_sub = R.kind("rec", **main_th)
    print("\n主线程（判决 / 提交 / 等）：")
    split("第一帧到最后一帧", np.ones(R.n, bool),
          [("等 det", w_det), ("等 rec 结算", w_rec), ("等回抠", w_edge),
           ("判决", decide), ("提交（不含等回抠）", commit), ("切裁剪 + 提交 rec", rec_sub)])

    det = R.kind("det")
    wf = R.kind("wait_frames")
    det_put = R.kind("det_put")
    print("\n归因链：")
    split("主线程等 det 时，det 线程", w_det, [("在算 det", det), ("在等帧", wf), ("往队列放（满）", det_put)])

    pipe = R.kind("pipe_read", not_th="MainThread")
    to_bgr = R.kind("to_bgr", not_th="MainThread")
    pput = R.kind("prefetch_put")
    split("det 线程等帧时，取帧线程", wf, [("等管道（ffmpeg 没出帧）", pipe), ("nv12 -> BGR", to_bgr), ("往预取队列放", pput)])

    ring = R.kind("ring_blocked")
    aux = R.kind("aux_read")
    split("取帧线程等管道时，辅助流", pipe, [("帧环满、推不进去（同一个 ffmpeg 停）", ring), ("也在等 ffmpeg", aux)])

    e_busy = R.kind("edge_batch")
    e_frame = R.kind("edge_wait_frame")
    e_idle = R.kind("edge_idle")
    split("帧环满时，回抠工作线程", ring, [("在等辅助帧", e_frame), ("在算", e_busy), ("闲着等链事件（主线程没发）", e_idle)])

    infer = R.kind("rec_infer")
    split("主线程等 rec 结算时，rec 池", w_rec, [("在推理", infer)])
    split("主线程等回抠时，回抠工作线程", w_edge, [("在等辅助帧", e_frame), ("在算", e_busy), ("闲着", e_idle)])

    server_split(ev, t_lo, t_hi)

    if args.series:
        step = int(args.series / BIN)
        cols = [("等det", w_det), ("等rec", w_rec), ("等回抠", w_edge), ("det算", det), ("det等帧", wf),
                ("等管道", pipe), ("环满", ring), ("rec推理", infer), ("回抠算", e_busy)]
        print(f"\n时间序列（每 {args.series:g} s 一格，各状态占这一格的比例）：")
        print("    t(s) " + " ".join(f"{n:>7}" for n, _ in cols))
        for a in range(0, R.n, step):
            b = min(R.n, a + step)
            print(f"  {a * BIN:6.0f} " + " ".join(f"{arr[a:b].mean():7.0%}" for _, arr in cols))
    return 0


if __name__ == "__main__":
    sys.exit(main())
