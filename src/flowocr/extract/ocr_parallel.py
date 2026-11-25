"""`run_ocr2 --workers N` 里**不碰模型的那部分**：切段、给子进程改参数、把各段的 `_meta` 合成一份。

放共享层是为了守卫够得着（audit-4 C8）。依据（fps-and-rec-budget 报告）：单进程时 GPU
大半时间在等主线程的 CPU 活，N 个进程各跑一段能把空档填上——3 个 worker 在 quick 三段上 −26%~−47%。

**切点在目标点上**（frame-pts 计划）：`framegrid.Window.split` 按桶号等分，子进程的 `--start` / `--end` 直接给 `T_k`；
每段的起点都是桶边界，所以各段取到的帧和单进程是同一批（窗口那条论证，见 `Window.seek_sec`）。切点不经帧号换算，
原来"平均帧率偏离名义帧率就拒开"的漂移门随之删掉。代价之一：每段开头没有"上一帧"，复用链从头开始、
强制刷新换了相位——所以 `workers` 是**产物参数**（进 `_meta.config`），两种跑法的产物不混用。

拼的时候逐个切点核（`seam_problems`）：下一段首帧的桶号必须大于上一段末帧的桶号（否则重叠）；中间隔了空桶时问源文件那几个桶里有没有帧，
有 = 漏帧，没有 = 素材缺口放行。
"""
from __future__ import annotations

from flowocr.extract import ocr_complete

SUM_KEYS = ("advanced_us", "requested_us", "sampled_frames", "boxes", "rec_calls",
            "rec_predict_calls", "reused_boxes", "dropped_dense", "dropped_tiny", "lost_rec", "cells_skipped")
"""各段相加就是整体的计数。"""

DICT_SUM_KEYS = ("reuse_v2", "reuse_v2_sec", "edge", "stage_sec", "rec_spec", "rec_vertical")
"""各段里是"计数字典"的键，逐键相加进整体（数值型的键才加；bool 不算）。"""

DICT_MAX_KEYS = frozenset({"cache_peak", "aux_last_key"})
"""这些键**不是计数，是峰值 / 位置**：取各段的最大值，不然 `--reuse-cache-mb 8` 的三个 worker
会被加成 21 MB（2026-09-17 审计时从产物里发现的口径错）；`aux_last_key` 是辅助流最后一帧的 `t_us`。"""


def strip_opts(argv: list[str], names: set[str]) -> list[str]:
    """从 argv 里去掉这些**带值**的选项（`--x v` 和 `--x=v` 两种写法都认），其余原样保留。"""
    out, skip = [], False
    for tok in argv:
        if skip:
            skip = False
            continue
        name = tok.split("=", 1)[0]
        if name in names:
            skip = "=" not in tok
            continue
        out.append(tok)
    return out


def seam_problems(metas: list[dict], gap_has_frames=None) -> list[str]:
    """逐个切点核首末采样帧的桶号（`_meta.cell_first` / `cell_last`）：下一段首帧的桶号不大于上一段末帧的 = **重叠**；
    中间隔了空桶时问 `gap_has_frames(上一段末帧的桶, 下一段首帧的桶)`（源文件里这两个桶之间有没有帧）：
    有 = **漏帧**（PTS 仍递增，只有这里能拦），没有 = 素材缺口放行，探不出来（None）也报。没给 `gap_has_frames` 时空桶一律报。
    原来"首尾间隔恰好一个采样间隔"的判法在 VFR 上会误报。
    已知的保守处：某个中间段整段落在素材缺口里、一帧都没交时桶号缺失，照样报（整体判未完成，单进程会判完成）——要盖住整整一段的缺口，罕见、而且报得出来。"""
    out = []
    for k in range(len(metas) - 1):
        a, b = metas[k].get("cell_last"), metas[k + 1].get("cell_first")
        if a is None or b is None:
            out.append(f"切点 {k}：段首 / 段尾的桶号缺失")
            continue
        if b <= a:
            out.append(f"切点 {k}：重叠（上一段末帧在桶 {a}、下一段首帧在桶 {b}）")
        elif b > a + 1:
            got = gap_has_frames(a, b) if gap_has_frames is not None else None
            if got is None:
                out.append(f"切点 {k}：桶 {a} 和 {b} 之间有空桶，探不出源里有没有帧")
            elif got:
                out.append(f"切点 {k}：桶 {a} 和 {b} 之间源里有帧、两段都没交（漏帧）")
    return out


CHILD_ARGS = ["--workers", "1", "--decode-shards", "0"]
"""`--workers N` 的子进程**追加**的参数（排在父进程 argv 之后，同名的后者生效）。
解码分片和多 worker 互斥（`ocr_args.resolve_split`），可子进程只看得见 `--workers 1`、会按默认再开几路——
`--workers 3` 就成了 6~9 个解码上下文（2026-09-24 审计）。所以由父进程显式关掉；它们在 SCHEDULING 里，不改复用判据。"""


def merge_metas(metas: list[dict], base: dict, *, wall: float, cuts_sec: list[float],
                gap_has_frames=None) -> dict:
    """各段的 `_meta` → 整体的 `_meta`。`base` 是父进程按同一组参数建的那份（video / config / 模型…）。

    **完成**按**整个窗口**判，和单进程同一把尺：
    - 覆盖率：推进到的 = 前面各段的请求时长（切点之间整段）+ 末段推进到的，即"末段最后一个采样帧所在桶的结束 − 窗口起点"，
      对请求时长之和过 `ocr_complete.is_complete`。**不逐段要求覆盖**：中间某段末尾紧跟素材缺口时它自己的覆盖率会不到 98%，
      单进程跑同一窗口却算完成；`end=0` 时容器多报的那一截也全压在最后一段上（审查 2026-09-10）。
      中间段提前断了、末尾漏了帧，由切点核对问源文件拦下（源里有帧 = 漏帧）；
    - 没有坏 PTS、没有一段的辅助流没交够（`aux_incomplete`），切点处不重叠、不漏帧（`seam_problems`，`gap_has_frames` 问源文件）。
    """
    m = dict(base)
    for k in SUM_KEYS:
        m[k] = sum(x.get(k, 0) or 0 for x in metas)
    for k in DICT_SUM_KEYS:                          # 各段的计数字典逐键相加（reuse_v2 / edge 的统计）
        parts = [x[k] for x in metas if isinstance(x.get(k), dict)]
        if parts:
            def agg(kk: str, parts=parts):
                vals = [d[kk] for d in parts if isinstance(d.get(kk), (int, float))]
                # **峰值不能相加**（2026-09-17）：三个 worker 各自的 8 MB 上限被加成 21 MB，
                # 看着像"上限没守住"，其实是口径错了。峰值取最大，其余相加
                if kk in DICT_MAX_KEYS:
                    return round(max(vals), 2) if any(isinstance(v, float) for v in vals) else max(vals)
                return (round(sum(float(v) for v in vals), 2) if any(isinstance(v, float) for v in vals)
                        else sum(int(v) for v in vals))
            m[k] = {kk: agg(kk)
                    for kk in dict.fromkeys(kk for d in parts for kk in d if isinstance(d.get(kk), (int, float)))}
    # 实际用了几个 worker：退回单进程时 run_ocr2 自己写 1，并行跑完由这里写段数——
    # 不写的话并行产物里这个键干脆没有，读的人分不出"三个 worker"和"旧产物"（2026-09-17）
    m["workers_effective"] = len(metas)
    splits = [x["proc_split"] for x in metas if "proc_split" in x]   # 拆进程的生效值（各段一样就记一份，不一样逐段列）
    if splits:
        m["proc_split"] = splits[0] if all(sp == splits[0] for sp in splits) else splits
    # 硬解的生效值（2026-09-20 整体检查时补的）：父进程早早 `return run_parallel`、**不做**试解，
    # 而这里又是从父进程那份 `base` 起步——不写的话各段 `hwaccel_effective` 全丢，
    # 读的人分不出"真走了硬解"和"每段都退回了软解"（和上面 workers_effective 同一个形状）。
    # 各段可能不一致（有前摇的 av1 只有**第一段**从文件开头起解，后面几段 seek 到非零位置都没事）
    effs = [x.get("hwaccel_effective") for x in metas if "hwaccel_effective" in x]
    if effs:
        m["hwaccel_effective"] = (effs[0] if len(set(effs)) == 1
                                  else "mixed:" + ",".join(e or "soft" for e in effs))
    # 推理设备的生效值 / 用的模型文件同一个形状（2026-09-22 接手自查）：父进程不建模型、`base` 里没有，不抄就全丢。
    # `--device auto` 由各段各自解析（各自试推理），可能不一致——不一致时逐段列出来
    for key in ("device_resolved", "device_fallback", "device_effective", "models", "det_batch", "backend"):   # det_batch：auto 也是各段按设备解析的；backend：各段接同一个共享服务，照理相同，不同时照样逐段列
        vals = [x.get(key) for x in metas if key in x]
        if vals:
            m[key] = vals[0] if all(v == vals[0] for v in vals) else {"mixed": vals}
    # TRT 按次退回 CUDA EP 的计数（`_meta.ort.<模型>.trt_oop / trt_err`）是服务端的累计数：按各段实际连的服务（`server`）分组，
    # 同一个服务取最大（共享服务的回信每段都是整份，相加会重复算），不同服务相加（`--no-ort-share`、接外部地址失败后自己起的）
    per_srv: dict = {}
    trt_ort: dict = {}
    for i, x in enumerate(metas):
        for tag, st in (x.get("ort") or {}).items():
            if not isinstance(st, dict):
                continue
            got = trt_ort.setdefault(tag, {})
            srv = per_srv.setdefault((tag, st.get("server") or f"seg{i}"), {})
            for k in ("trt_oop", "trt_err"):
                if st.get(k):
                    srv[k] = max(srv.get(k, 0), st[k])
            if st.get("trt_err_first") and "trt_err_first" not in got:
                got["trt_err_first"] = st["trt_err_first"]
    for (tag, _), srv in per_srv.items():
        for k, v in srv.items():
            trt_ort[tag][k] = trt_ort[tag].get(k, 0) + v
    if any(trt_ort.values()):
        m["ort"] = {**(m.get("ort") or {}), **{t: v for t, v in trt_ort.items() if v}}
    seams = seam_problems(metas, gap_has_frames)
    bad = next((x["bad_pts"] for x in metas if x.get("bad_pts")), None)
    overlap = [s for s in seams if "重叠" in s]
    if overlap and bad is None:
        bad = "段间 PTS 不递增：" + "；".join(overlap)
    m["advanced_us"] = sum(x.get("requested_us", 0) or 0 for x in metas[:-1]) + (metas[-1].get("advanced_us", 0) or 0)
    aux_bad = next((x["aux_incomplete"] for x in metas if x.get("aux_incomplete")), None)
    if aux_bad:
        m["aux_incomplete"] = aux_bad
    complete = (ocr_complete.is_complete(m["advanced_us"], m["requested_us"])
                and bad is None and aux_bad is None and not seams)
    firsts = [x.get("pts_first_sec") for x in metas]
    lasts = [x.get("pts_last_sec") for x in metas]
    m.update({
        "complete": complete,
        "stopped_early": any(x.get("stopped_early") for x in metas),
        "bad_pts": bad,
        "seam_problems": seams,
        "reuse_rate": round(m["reused_boxes"] / max(1, m["boxes"]), 4),
        "wall_sec": round(wall, 2),
        "sec_per_frame": round(wall / max(m["sampled_frames"], 1), 3),
        "start_sec": metas[0].get("start_sec"),
        "end_sec": metas[-1].get("end_sec"),
        "timebase": "pts",
        "pts_first_sec": firsts[0],
        "pts_last_sec": lasts[-1],
        "cell_first": metas[0].get("cell_first"),
        "cell_last": metas[-1].get("cell_last"),
        "cuts_sec": cuts_sec,
        "part_wall_sec": [x.get("wall_sec") for x in metas],
    })
    span = (lasts[-1] - firsts[0]) if firsts[0] is not None and lasts[-1] is not None else 0.0
    m["realtime_x"] = round(span / wall, 2) if wall else None
    return m
