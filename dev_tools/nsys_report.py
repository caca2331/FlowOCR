"""读 dev_tools/nsys_server.sh 的产物，拆 ORT 推理服务在 GPU 上的时间。<前缀> 是不带扩展名的产物路径（如 tmp/meas/nsys/w4）。

    python dev_tools/nsys_report.py overview <前缀> [<前缀> ...]   # 核时间、det / rec 重叠、整卡 GPU 指标、rec 的核排队
    python dev_tools/nsys_report.py runs <前缀>                    # 按服务端时间线的真实 Run 边界拆：自己的核 / 阻塞拷贝 / 发核 / 空当
    python dev_tools/nsys_report.py stalls <前缀>                  # Run 里 ≥1 ms 的停顿按线程调度状态拆（要 NSYS_ADMIN=1 采的）
    python dev_tools/nsys_report.py server-cmd <端口> [run_ocr2 参数...]   # 给 nsys_server.sh 用：按单 worker 默认拼独立服务的命令行

共同口径：
- 线程分 rec / det：发过 argmax 核（rec 模型输出头）的线程是 rec，其余发过核的是 det。
- 窗口：overview 取第一个到最后一个 det 核（主循环；服务预热的 rec 核不算）；runs / stalls 取主循环第一帧到最后一帧。
- 对齐（runs / stalls）：服务端时间线是 perf_counter（QPC 秒），nsys 是会话起点之后的 ns。先按两边的 epoch 锚点粗对齐，再拿
  "每次 Run 结束前最后一次阻塞的 D2H 拷贝"精对齐（每个 Run 末尾 ±20 ms 内最近的 D2H 结束时刻，差值取中位）；
  Run 落在哪个 nsys 线程 = 末尾那次 D2H 所在线程（0.2 ms 内投票取多数）。
- 冷 / 热：这次请求的取 session（ort_sess）> 5 ms 算冷（刚建了 session，第一次 Run 里还有 cuDNN 选算法）。
- 整卡 GPU 指标含服务之外的活（硬解 ffmpeg 的缩放核、桌面），按"服务没核 / 只有 det / 只有 rec / 两者同时"分开读。
"""
import bisect
import collections
import json
import os
import sqlite3
import sys

import numpy as np

BIN = 10_000          # overview 的 GPU 指标栅格：10 µs（ns）
MIN_GAP = 1_000_000   # stalls：≥1 ms 的空当才算停顿
READY = {42, 44, 51, 45, 49}   # 换出原因是这些时线程可运行、只是没被调度（QuantumEnd / Preempted / DeferredPreempt / YieldExecution / ReadyToRun）
ALERT = 50                     # AlertByThreadId：用户态锁 / 条件变量的等待


def merge(iv):
    out = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def inter(a, b):
    i = j = 0
    out = []
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if s < e:
            out.append([s, e])
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def minus(a, b):
    out, j = [], 0
    for s, e in a:
        cur = s
        while j < len(b) and b[j][1] <= cur:
            j += 1
        k = j
        while k < len(b) and b[k][0] < e:
            if b[k][0] > cur:
                out.append([cur, b[k][0]])
            cur = max(cur, b[k][1])
            k += 1
        if cur < e:
            out.append([cur, e])
    return out


def clip(iv, s, e):
    return [[max(a, s), min(b, e)] for a, b in iv if b > s and a < e]


L = lambda iv: sum(e - s for s, e in iv) / 1e9


def kernels(c):
    """[(start, end, 发起线程, 核名)]，以及 rec 线程集合。"""
    k = c.execute("""select k.start, k.end, r.globalTid, s.value, r.end from CUPTI_ACTIVITY_KIND_KERNEL k
                     join CUPTI_ACTIVITY_KIND_RUNTIME r on r.correlationId = k.correlationId
                     join StringIds s on s.id = k.shortName""").fetchall()
    rec = {t for _, _, t, n, _ in k if n.startswith("arg_min_max")}
    if k and not rec:                                # 按 CUDA EP 的核名认 rec 线程；TensorRT 的核名不同，认不出就会把所有核算成 det
        sys.exit("没认出 rec 线程（没有 arg_min_max 核）：服务走了 TensorRT？nsys_server.sh 默认加 --backend cuda，别覆盖成 auto / trt")
    return k, rec


# ---------------------------------------------------------------- overview
def overview(prefix):
    c = sqlite3.connect(f"file:{prefix}.sqlite?mode=ro", uri=True)
    k, rec_tids = kernels(c)
    det = [(s, e) for s, e, t, _, _ in k if t not in rec_tids]
    t0, t1 = min(s for s, _ in det), max(e for _, e in det)
    rec = [(s, e) for s, e, t, _, _ in k if t in rec_tids and s >= t0 and e <= t1]
    mem = list(c.execute("select start, end from CUPTI_ACTIVITY_KIND_MEMCPY where start >= ? and end <= ?", (t0, t1)))
    W = (t1 - t0) / 1e9
    ud, ur, um = merge(det), merge(rec), merge(mem)
    meta = json.loads(open(f"{prefix}.jsonl", encoding="utf-8").readline())["_meta"]
    rp, o = meta["rec_pool"], meta["ort"]
    infer = lambda key: sum(x["infer_sec"] for x in (o[key] if isinstance(o[key], list) else [o[key]]))
    rec_infer = infer("rec") + (infer("rec_side") if "rec_side" in o else 0)
    print(f"== {os.path.basename(prefix)}：墙钟 {meta['wall_sec']} s，窗口（主循环）{W:.2f} s；rec 调用 {rp['calls']}、裁剪 {rp['sent']}、"
          f"平均批 {rp['avg_batch']}；det 调用 {o['det']['calls']}")
    ua = merge(det + rec)
    print(f"  服务内有核在跑（精确并集）{L(ua):6.2f} s（{L(ua) / W:4.0%}）：det 核 {L(ud):6.2f} s、rec 核 {L(ur):6.2f} s、"
          f"两者同时 {L(inter(ud, ur)):5.2f} s；拷贝 {L(um):4.2f} s")
    print(f"  核时间逐个相加：det {sum(e - s for s, e in det) / 1e9:6.2f} s、rec {sum(e - s for s, e in rec) / 1e9:6.2f} s"
          f"（rec 每裁剪 {sum(e - s for s, e in rec) / 1e6 / rp['sent']:.2f} ms）")
    print(f"  客户端看到的推理时间（服务端 infer_sec）：det {infer('det'):6.2f} s、rec 两路合计 {rec_infer:6.2f} s")
    # rec 的核排队：已经发出（发核 API 返回）、还没开始的时间，其中 det 的核正在跑的部分
    for t in sorted(rec_tids, key=lambda t: -sum(1 for x in k if x[2] == t)):
        own = [(s, e) for s, e, tt, _, _ in k if tt == t and s >= t0 and e <= t1]
        if not own:
            continue
        pend = clip(merge([(ra, s) for s, e, tt, _, ra in k if tt == t and s > ra]), t0, t1)
        print(f"  rec 线程 {t % 100000}：核 {len(own)} 个、并集 {L(merge(own)):5.2f} s；已发出未开始 {L(pend):5.2f} s，"
              f"其中 det 的核在跑 {L(inter(pend, ud)):5.2f} s")
    n = (t1 - t0) // BIN + 1

    def mask(iv):
        m = np.zeros(n, bool)
        for s, e in iv:
            a, b = max(0, (s - t0) // BIN), min(n, (e - t0) // BIN + 1)
            if b > a:
                m[a:b] = True
        return m

    md, mr = mask(det), mask(rec)
    anyk = md | mr
    names = dict(c.execute("select metricId, metricName from TARGET_INFO_GPU_METRICS"))
    for mid, name in names.items():
        if name not in ("GR Active [Throughput %]", "SMs Active [Throughput %]", "SM Issue [Throughput %]",
                        "Compute Warps in Flight [Throughput %]", "Tensor Active [Throughput %]"):
            continue
        rows = np.array(c.execute("select timestamp, value from GPU_METRICS where metricId = ? and timestamp between ? and ?", (mid, t0, t1)).fetchall())
        if not len(rows):
            continue
        idx = np.clip((rows[:, 0] - t0) // BIN, 0, n - 1).astype(int)
        v = rows[:, 1].astype(float)
        sel = lambda m: f"{v[m[idx]].mean():5.1f}%" if m[idx].any() else "  -  "
        print(f"  {name:<40} 整窗 {v.mean():5.1f}%  | 服务没核 {sel(~anyk)}  只有 det {sel(md & ~mr)}  只有 rec {sel(mr & ~md)}  两者同时 {sel(md & mr)}")


# ---------------------------------------------------------------- runs / stalls 共用：按服务端时间线对齐
def jl(p):
    lines = open(p, encoding="utf-8").read().splitlines()
    return json.loads(lines[0])["_meta"], [json.loads(x) for x in lines[1:]]


class Aligned:
    """主循环里的每次 Run（服务端时间线）对到 nsys 的线程和时刻上。"""

    def __init__(self, prefix, full=False):
        m_ort, ort = jl(f"{prefix}.tl.jsonl.ort.jsonl")
        m_main, main = jl(f"{prefix}.tl.jsonl")
        self.c = c = sqlite3.connect(f"file:{prefix}.sqlite?mode=ro", uri=True)
        S = c.execute("select utcEpochNs from TARGET_INFO_SESSION_START_TIME").fetchone()[0]
        A = m_ort["anchor"][0] - m_ort["anchor"][1]                    # epoch - perf
        self._ns = lambda perf, d=0: int((perf + A) * 1e9 - S) + d
        frames = [e["t0"] for e in main if e["k"] == "frame"]
        if full:
            begins = [e['t0'] for e in main if e['k'] == 'work_begin']
            ends = [e['t0'] for e in main if e['k'] == 'work_end']
            if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
                raise ValueError('full window requires one valid work_begin/work_end pair')
            frames = [begins[0], ends[0]]
        A_main = m_main["anchor"][0] - m_main["anchor"][1]
        lo, hi = min(frames) + A_main - A, max(frames) + A_main - A   # 换到服务端的 perf 轴
        self.strs = dict(c.execute("select id, value from StringIds"))
        kind = dict(c.execute("select correlationId, copyKind from CUPTI_ACTIVITY_KIND_MEMCPY"))
        self.by = collections.defaultdict(list)                       # 线程 -> [(start, end, API 名)]，按开始时刻排
        d2h = []
        for s0, e0, t, nid, cid in c.execute("select start, end, globalTid, nameId, correlationId from CUPTI_ACTIVITY_KIND_RUNTIME order by start"):
            nm = self.strs[nid].split("_v")[0]
            self.by[t].append((s0, e0, nm))
            if nm.startswith("cudaMemcpy") and kind.get(cid) == 2:
                d2h.append((e0, t))
        d2h.sort()
        d2h_t = [x for x, _ in d2h]
        self.runs = [e for e in ort if e["k"] == "ort_run" and lo <= e["t0"] < hi]
        self.sess = collections.defaultdict(list)                     # 服务端线程名 -> [(取 session 结束时刻, 用时)]
        for e in ort:
            if e["k"] == "ort_sess":
                self.sess[e["th"]].append((e["t1"], e["t1"] - e["t0"]))
        for v in self.sess.values():
            v.sort()
        diffs = []
        for e in self.runs:
            r = self._ns(e["t1"])
            i = bisect.bisect_left(d2h_t, r - 20_000_000)
            best = None
            while i < len(d2h_t) and d2h_t[i] <= r + 20_000_000:
                if best is None or abs(d2h_t[i] - r) < abs(best):
                    best = d2h_t[i] - r
                i += 1
            if best is not None:
                diffs.append(best)
        self.delta = int(np.median(diffs))
        self.diffs = diffs
        self.votes = collections.defaultdict(collections.Counter)
        for e in self.runs:
            e_ns = self.ns(e["t1"])
            i = bisect.bisect_left(d2h_t, e_ns - 200_000)
            while i < len(d2h) and d2h[i][0] <= e_ns + 200_000:
                self.votes[e["th"]][d2h[i][1]] += 1
                i += 1
        self.th_map = {th: v.most_common(1)[0][0] for th, v in self.votes.items()}

    def ns(self, perf):
        return self._ns(perf, self.delta)

    def cold(self, e):
        pre = self.sess.get(e["th"], [])
        k = bisect.bisect_right(pre, (e["t0"] + 1e-9, 1e9)) - 1
        return k >= 0 and abs(pre[k][0] - e["t0"]) < 0.01 and pre[k][1] > 0.005

    def calls(self, t, s_ns, e_ns):
        return [r for r in self.by[t] if r[0] >= s_ns and r[1] <= e_ns]


def runs(prefix):
    a = Aligned(prefix)
    d = np.array(a.diffs)
    print(f"{os.path.basename(prefix)}：主循环里 {len(a.runs)} 次 Run；粗对齐后末次 D2H 相对 Run 结束的偏差中位 {a.delta / 1e6:+.3f} ms"
          f"（四分位 {np.percentile(d, 25) / 1e6:+.3f} / {np.percentile(d, 75) / 1e6:+.3f} ms），按它精对齐")
    print("  线程对应（服务端线程名 -> nsys 线程，票数 / 该线程 Run 数）：" + "、".join(
        f"{th} -> {t % 100000}（{a.votes[th][t]} / {sum(1 for e in a.runs if e['th'] == th)}）" for th, t in sorted(a.th_map.items())))
    k, rec_tids = kernels(a.c)
    kby = collections.defaultdict(list)
    for s0, e0, t, _, _ in k:
        kby[t].append((s0, e0))
    km = {t: merge(v) for t, v in kby.items()}
    det_tids = [t for t in km if t not in rec_tids]
    det_k = merge([x for t in det_tids for x in kby[t]])
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    unmatched = 0
    for e in a.runs:
        t = a.th_map.get(e["th"])
        if t is None:
            unmatched += 1
            continue
        s_ns, e_ns = a.ns(e["t0"]), a.ns(e["t1"])
        g = agg[(e["a"][0], "冷" if a.cold(e) else "热")]
        g["n"] += 1
        g["run"] += (e_ns - s_ns) / 1e9
        rows = a.calls(t, s_ns, e_ns)
        cp = merge([[x, y] for x, y, nm in rows if nm.startswith(("cudaMemcpy", "cudaStreamSynchronize", "cudaDeviceSynchronize"))])
        g["block"] += L(cp)
        g["launch"] += sum(y - x for x, y, nm in rows if "aunch" in nm) / 1e9
        g["api"] += sum(y - x for x, y, _ in rows) / 1e9
        own = clip(km.get(t, []), s_ns, e_ns)
        g["own"] += L(own)
        others = merge([x for tt in km if tt != t and (tt not in det_tids or t in det_tids) for x in clip(km[tt], s_ns, e_ns)])
        g["blk_det"] += L(inter(cp, merge(clip(det_k, s_ns, e_ns)))) if t not in det_tids else 0.0
        g["blk_own"] += L(inter(cp, merge(own)))
        g["blk_oth"] += L(inter(cp, others))
        edges = [s_ns] + [x for p, q, _ in rows for x in (p, q)] + [e_ns]
        gaps = [edges[j + 1] - edges[j] for j in range(0, len(edges) - 1, 2)]
        g["head"] += (gaps[0] if gaps else 0) / 1e9      # Run 开始到第一次 CUDA 调用（ORT 在 CPU 上的准备）
        for x in gaps:
            band = "10" if x >= 10_000_000 else "1" if x >= 1_000_000 else "0"
            g["gap" + band] += x / 1e9
            g["n" + band] += 1
    print(f"  没对上线程的 Run：{unmatched}\n")
    for (model, temp), g in sorted(agg.items()):
        n, run = g["n"], g["run"]
        pct = lambda x: f"{x:6.2f} s（{x / run:4.0%}）"
        print(f"{model} {temp} {int(n)} 次，Run 合计 {run:6.2f} s（平均 {run / n * 1000:5.1f} ms）")
        print(f"  自己的核（并集）{pct(g['own'])}")
        print(f"  CUDA 调用里 {pct(g['api'])}：其中阻塞拷贝 / 同步 {g['block']:5.2f} s（期间 GPU 上：自己的核 {g['blk_own']:4.2f}、det 的核 {g['blk_det']:4.2f}、"
              f"另一路的核 {g['blk_oth']:4.2f} s），发核 {g['launch']:5.2f} s")
        print(f"  CUDA 调用之外 {pct(run - g['api'])}：Run 开始到第一次 CUDA 调用 {g['head']:5.2f} s；空当 <1 ms {g['gap0']:5.2f} s、"
              f"1~10 ms {g['gap1']:5.2f} s（{int(g['n1'])} 次）、≥10 ms {g['gap10']:5.2f} s（{int(g['n10'])} 次）")


def stalls(prefix):
    a = Aligned(prefix)
    c, strs = a.c, a.strs
    print(f"{os.path.basename(prefix)}：{len(a.runs)} 次 Run，对齐偏差中位 {a.delta / 1e6:+.3f} ms；线程 "
          + ", ".join(f"{k}->{v % 100000}" for k, v in sorted(a.th_map.items())))
    if not c.execute("select name from sqlite_master where name = 'SCHED_EVENTS'").fetchone():
        sys.exit("这份 sqlite 没有线程调度记录：用 NSYS_ADMIN=1 重采（管理员终端）")
    gaps = collections.defaultdict(list)       # 模型 -> [(线程, 起, 止, 前一个调用, 后一个调用)]，只算热的 Run
    for e in a.runs:
        t = a.th_map.get(e["th"])
        if t is None or a.cold(e):
            continue
        s_ns, e_ns = a.ns(e["t0"]), a.ns(e["t1"])
        pts = [(s_ns, "Run 开始")] + [p for x, y, nm in a.calls(t, s_ns, e_ns) for p in ((x, nm), (y, nm))] + [(e_ns, "Run 结束")]
        for j in range(0, len(pts) - 1, 2):
            (x, nx), (y, ny) = pts[j], pts[j + 1]
            if y - x >= MIN_GAP:
                gaps[e["a"][0]].append((t, x, y, nx, ny))
    tids = {g[0] for v in gaps.values() for g in v}
    tl = ",".join(map(str, tids))
    sched = collections.defaultdict(list)
    for st, inn, ts, tb, t in c.execute(f"select start, isSchedIn, threadState, threadBlock, globalTid from SCHED_EVENTS where globalTid in ({tl}) order by start"):
        sched[t].append((st, inn, ts, tb))
    blk_name = dict(c.execute("select id, name from ENUM_SCHEDULING_THREAD_BLOCK"))
    samples = collections.defaultdict(list)
    for i, st, t in c.execute(f"select id, start, globalTid from COMPOSITE_EVENTS where globalTid in ({tl})"):
        samples[t].append((st, i))
    for v in samples.values():
        v.sort()

    def off_cpu(t, x, y):
        """[x, y) 里这个线程：在 CPU 上的时间、不在时按换出原因分的时间。"""
        ev = sched[t]
        i = bisect.bisect_right(ev, (x, 2, 99, 99)) - 1
        state_in = ev[i][1] == 1 if i >= 0 else True
        reason = None if state_in or i < 0 else ev[i][3]
        cur, run, off = x, 0, collections.Counter()
        j = i + 1
        while True:
            nxt = ev[j][0] if j < len(ev) else y
            end = min(nxt, y)
            if end > cur:
                if state_in:
                    run += end - cur
                else:
                    off["Ready" if reason in READY else blk_name.get(reason, f"?{reason}")] += end - cur
            if nxt >= y or j >= len(ev):
                break
            state_in = ev[j][1] == 1
            reason = None if state_in else ev[j][3]
            cur = nxt
            j += 1
        return run, off

    leaf = {}
    for model, gs in sorted(gaps.items()):
        tot = sum(y - x for _, x, y, _, _ in gs)
        run_t, off_t, where, sym = 0, collections.Counter(), collections.Counter(), collections.Counter()
        for t, x, y, nx, ny in gs:
            r, o = off_cpu(t, x, y)
            run_t += r
            off_t.update(o)
            where[(nx, ny)] += y - x
            ss = samples.get(t, [])
            for _, sid in ss[bisect.bisect_left(ss, (x, -1)):bisect.bisect_left(ss, (y, -1))]:
                sym[sid] += 1
        ids = list(sym)
        for k0 in range(0, len(ids), 900):
            q = ",".join(map(str, ids[k0:k0 + 900]))
            for sid, s_, m_ in c.execute(f"select id, symbol, module from SAMPLING_CALLCHAINS where stackDepth = 0 and id in ({q})"):
                leaf[sid] = (strs.get(s_, "?"), strs.get(m_, "?").split("\\")[-1])
        fn, mod = collections.Counter(), collections.Counter()
        for sid, n in sym.items():
            s_, m_ = leaf.get(sid, ("?", "?"))
            fn[(m_, s_[:70])] += n
            mod[m_] += n
        big = [g for g in gs if g[2] - g[1] >= 10_000_000]
        print(f"\n{model}：≥1 ms 的停顿 {len(gs)} 次、{tot / 1e9:.2f} s（其中 ≥10 ms {len(big)} 次、{sum(y - x for _, x, y, _, _ in big) / 1e9:.2f} s）")
        print(f"  线程在 CPU 上 {run_t / 1e9:5.2f} s（{run_t / tot:4.0%}）；不在 CPU 上："
              + "、".join(f"{k} {v / 1e9:.2f} s（{v / tot:.0%}）" for k, v in off_t.most_common(6)))
        print("  停顿前后各是哪个 CUDA 调用（按时长）：" + "；".join(f"{p} → {q} {v / 1e9:.2f} s" for (p, q), v in where.most_common(4)))
        n_s = sum(sym.values()) or 1
        print(f"  在 CPU 上时的采样 {sum(sym.values())} 个，模块：" + "、".join(f"{m} {v / n_s:.0%}" for m, v in mod.most_common(6)))
        for (m_, s_), v in fn.most_common(10):
            print(f"    {v / n_s:5.1%}  {m_:<28} {s_}")

    # 谁叫醒的：rec 线程从 ≥1 ms 的 AlertByThreadId 等待里换回 CPU 的那一刻（往前 200 µs），服务进程里哪些线程在 CPU 上、在哪个 CUDA 调用里
    pid = next(iter(tids)) >> 24
    names = {t: strs.get(n, "?") for n, t in c.execute("select nameId, globalTid from ThreadNames")}
    allsched = collections.defaultdict(list)
    for st, inn, t in c.execute(f"select start, isSchedIn, globalTid from SCHED_EVENTS where globalTid >= {pid << 24} and globalTid < {(pid + 1) << 24} order by start"):
        allsched[t].append((st, inn))

    def on_cpu(t, x, y):
        ev = allsched.get(t, [])
        i = bisect.bisect_right(ev, (x, 2)) - 1
        if i >= 0 and ev[i][1] == 1:
            return True
        j = bisect.bisect_left(ev, (x, -1))
        return j < len(ev) and ev[j][0] < y                     # 窗口里有换入换出也算在过 CPU

    def api_at(t, x):
        rows = a.by.get(t, [])
        i = bisect.bisect_right(rows, (x, 1 << 62, "")) - 1
        return rows[i][2] if i >= 0 and rows[i][1] >= x else "（不在 CUDA 调用里）"

    wake, n_w, lens = collections.Counter(), 0, []
    for t, x, y, _, _ in gaps.get("rec", []):
        ev = sched[t]
        i = bisect.bisect_left(ev, (x, -1))
        while i < len(ev) and ev[i][0] < y:
            if ev[i][1] == 0 and ev[i][3] == ALERT:
                j = i + 1
                while j < len(ev) and ev[j][1] == 0:
                    j += 1
                if j < len(ev):
                    lens.append((ev[j][0] - ev[i][0]) / 1e6)
                    if ev[j][0] - ev[i][0] >= 1_000_000:
                        st = ev[j][0]
                        n_w += 1
                        for u in allsched:
                            if u != t and on_cpu(u, st - 200_000, st):
                                wake[(names.get(u, "") + f"#{u % 100000}", api_at(u, st))] += 1
                i = j
            else:
                i += 1
    print(f"\nrec 线程从 ≥1 ms 的 AlertByThreadId 等待里醒来 {n_w} 次；醒来前 200 µs 服务进程里在 CPU 上的线程（次数 / 醒来次数）：")
    for (u, api), v in wake.most_common(15):
        print(f"  {v / max(n_w, 1):5.0%}  {u:<40} {api}")
    if lens:
        lens = np.array(lens)
        print(f"AlertByThreadId 等待 {len(lens)} 段：中位 {np.median(lens):.3f} ms、p90 {np.percentile(lens, 90):.2f} ms、"
              f"≥1 ms 的 {(lens >= 1).sum()} 段合计 {lens[lens >= 1].sum():.0f} ms、<1 ms 合计 {lens[lens < 1].sum():.0f} ms")


def server_cmd(port, extra):
    """一行一个参数打到 stdout（nsys_server.sh 逐行读成命令）；准备模型时的提示（首次下载 / 生成派生图）改打到 stderr，不混进命令。
    设备跟着 run_ocr2 的 `--device` 走（只认 GPU：auto / gpu 当 0 号卡），客户端握手时才认得出这是它要的服务。"""
    import contextlib

    from flowocr.extract import ocr_args, ortclient
    from flowocr.extract.run_ocr2 import onnx_path, ort_device, ort_server_args
    args = ocr_args.parse_args(["x.mp4", "--out", "x.jsonl", "--quality", "high", *extra])   # 和 nsys_server.sh 给 run_ocr2 的一样
    dev = "gpu" if args.device in ("auto", "gpu") else args.device
    if not dev.startswith("gpu"):
        sys.exit(f"nsys 只采 GPU 上的服务（--device {args.device}）")
    with contextlib.redirect_stdout(sys.stderr):
        rec, det = onnx_path(args, "rec"), onnx_path(args, "det")
    cmd = [os.path.abspath(ortclient.server_python()), str(ortclient.SERVER), "--listen", port,
           "--model", f"rec={rec}", "--model", f"det={det}",
           "--max-sessions", str(args.ort_max_sessions), "--device", ort_device(dev)] + ort_server_args(args, dev)
    print("\n".join(cmd))


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("overview", "runs", "stalls", "server-cmd"):
        sys.exit(__doc__)
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "server-cmd":
        server_cmd(rest[0], rest[1:])
    elif cmd == "overview":
        for p in rest:
            overview(p)
    else:
        {"runs": runs, "stalls": stalls}[cmd](rest[0])
