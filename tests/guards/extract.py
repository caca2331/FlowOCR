"""提取层（run_ocr2、解码、推理服务、复用判据、帧网格）的守卫。"""
from __future__ import annotations

import importlib  # noqa: F401
import inspect  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

from guards._common import *  # noqa: F401,F403


def t_reuse_v2() -> None:
    """复用判据第二版（src/flowocr/extract/reuse_v2.py）的两条不变量，都是 2026-09-17 审计的最小复现：

    ① **承诺了沿用就必须能回补**：缓存预留和放行是一个原子动作。第一版同一帧的几个框各自看同一个余额、
       又都不预留，卡在上限上时会双双获准沿用，commit 里第二个的裁剪放不下就被丢掉——行是 reused、
       却回补不了，错文本留在产物里。
    ② **回补改了过去，链事件只能往前发**：那一段的在线回抠证据要作废（stale），不能拿晚一个采样点的窗口
       去写产物的时刻。"""
    print("reuse_v2（缓存预留 / 回补与边界事件）")
    import io
    import random
    import types

    import numpy as np

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.extract import ocr_args
    from flowocr.extract.reuse_v2 import ReuseV2

    def make(cap_mb, rec_text="BBB"):
        args = ocr_args.parse_args(["dummy.mp4", "--out", "x.jsonl", "--no-refine-fused",
                                    "--reuse-cache-mb", str(cap_mb)])
        return ReuseV2(args, lambda a, b: 1.0, lambda a, b: (0, 0), lambda crop: (rec_text, 0.99))

    def step(v, seq, boxes, texts, crop_hw=(10, 10)):
        dec, todo, assign = v.decide(seq, np.zeros((40, 200), np.uint8), boxes)
        dec = list(dec) + [(i, texts[i], 0.99, 0) for i in todo]
        polys = [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]] for b in boxes]
        v.commit(seq, seq * 30, seq * 500_000, boxes, polys, dec, set(todo), assign,
                 lambda i: np.zeros((crop_hw[0], crop_hw[1], 3), np.uint8), {}, False, io.StringIO())
        return todo

    # ① 只剩 400 B、同一帧两个各 300 B 的裁剪：**不能两个都放行**
    v = make(400 / (1 << 20))
    boxes = [[0, 0, 10, 10], [20, 0, 30, 10]]
    step(v, 0, boxes, ["AAA", "AAA"])
    todo = step(v, 1, boxes, ["BBB", "BBB"])
    reused_rows = [r for r in v.buf[1] if r.get("reused")]
    cached_rows = [p[1] for c in v.live for p in c.pending]
    check("**缓存卡在上限上时，沿用的行必须都进了缓存**（预留和放行是一个原子动作）",
          v.stats["cache_overrun"] == 0 and all(r in cached_rows for r in reused_rows)
          and len(todo) == 1 and v.stats["cache_forced"] == 1, (todo, len(reused_rows), len(cached_rows)))
    v.a.reuse_corr = v.a.reuse_corr_digit = 1.1   # 下一帧一律真读（未证实的链走严门，两道门都要抬）：发现变化，回补历史
    step(v, 2, boxes, ["BBB", "BBB"])
    check("于是**历史行全都补对了**（第一版会留下一条 'AAA'）",
          [r["text"] for r in v.buf[1]] == ["BBB", "BBB"], [r["text"] for r in v.buf[1]])

    # 预算的账要处处对得上：随机序列跑 200 帧，cache 恒等于"活链已缓存 + 本帧预留"，且不越上限
    v = make(0.02)
    random.seed(7)
    bad = 0
    for seq in range(200):
        bs = []
        for _ in range(random.randint(0, 4)):
            x, y = random.choice([0, 30, 60, 90, 120]), random.choice([0, 20])
            bs.append([x, y, x + random.choice([20, 24, 40]), y + 18])
        step(v, seq, bs, {i: random.choice(["AAA", "BBB", "CCC"]) for i in range(len(bs))},
             crop_hw=(18, 30))
        # ⚠ **记账要对着"真东西"核，不能两个记账数互比**（2026-09-19 实测）：拆 commit 那次我手工切了
        # `chain.pending` 却没退 `cache_bytes`，**两边一起错**——这一行原来的判据照样通过，而缓存虚高
        # 让 `cache_forced` 多触发、判决跟着变（8,026 行里 14 行产物不同）。所以再核一条：
        # `cache_bytes` 必须等于那条链 `pending` 里裁剪的实际字节数。
        real = sum(int(pnd[2].nbytes) for c in v.live for pnd in c.pending)
        book = sum(c.cache_bytes for c in v.live)
        if (v.cache != book + sum(v.reserved.values()) or v.cache > v.cache_cap or book != real):
            bad += 1
    check("缓存的账逐帧对得上、峰值不越上限（随机 200 帧）",
          bad == 0 and v.stats["cache_overrun"] == 0 and v.stats["cache_peak"] <= v.cache_cap,
          (bad, v.stats["cache_peak"], v.cache_cap))

    # ② 回补把变化点前移 -> 那条链这一批的边界证据要作废
    class Sink:
        def __init__(self):
            self.seen = []

        def sample(self, seq, idx, t_us, items, ended, stale=()):
            self.seen.append((seq, [(it[3], it[4]) for it in items], set(stale)))

        def settled_by(self, seq):
            return seq

        def wait_batch(self, seq):
            pass

    v = make(8.0)
    v.edge = Sink()
    one = [[0, 0, 10, 10]]
    step(v, 0, one, ["AAA"])
    step(v, 1, one, ["AAA"])                   # 沿用（corr 1.0）
    v.a.reuse_corr = v.a.reuse_corr_digit = 1.1
    step(v, 2, one, ["BBB"])                   # 真读 BBB，回补发现第 1 帧其实已经是 BBB
    texts = [rs[0]["text"] for _, rs in sorted(v.buf.items())]
    check("**回补前移了变化点 -> 这一批的链事件带 stale**（否则在线回抠会按晚一个采样点的窗写时刻）",
          texts == ["AAA", "BBB", "BBB"] and v.stats["edge_stale"] == 1
          and v.edge.seen[-1][2] == {v.live[0].uid}, (texts, v.stats["edge_stale"], v.edge.seen))
    check("没改动历史的真读不标 stale（作废只发生在真的改写了过去的时候）",
          v.edge.seen[0][2] == set() and v.edge.seen[1][2] == set())

    # `_crop` 存 uint8 取 float32：和存 float32 整帧**逐位相同**（省四倍内存的前提）
    g8 = (np.arange(40 * 200, dtype=np.uint8) % 251).reshape(40, 200)
    c8 = ReuseV2._crop(g8, [10, 5, 60, 30])
    c32 = ReuseV2._crop(g8.astype(np.float32), [10, 5, 60, 30])
    check("灰度整帧存 uint8、取裁剪时转 float32：和存 float32 逐位相同",
          c8.dtype == np.float32 and np.array_equal(c8, c32))

    # ③ `--record-corr` 在 v2 路径上**不许静默空转**（2026-09-18 发现它就是这样）：
    #    `corr_of` 原来只有老的 prev 路径会填，而 v2 早就是默认——旋钮给了、产物里一个 corr 都没有，
    #    于是"按相关系数分析复用"的探针拿到的是空表。它是 NON_PRODUCT，不进复用判据，但必须真写出来。
    v = make(8.0)
    one = [[0, 0, 10, 10]]
    fh = io.StringIO()
    dec, todo, assign = v.decide(0, np.zeros((40, 200), np.uint8), one)
    v.commit(0, 0, 0, one, [[[0, 0], [10, 0], [10, 10], [0, 10]]],
             list(dec) + [(i, "AAA", 0.99, 0) for i in todo], set(todo), assign,
             lambda i: np.zeros((10, 10, 3), np.uint8), dict(v.last_corr), True, fh)
    dec, todo, assign = v.decide(1, np.zeros((40, 200), np.uint8), one)
    v.commit(1, 30, 500_000, one, [[[0, 0], [10, 0], [10, 10], [0, 10]]],
             list(dec) + [(i, "AAA", 0.99, 0) for i in todo], set(todo), assign,
             lambda i: np.zeros((10, 10, 3), np.uint8), dict(v.last_corr), True, fh)
    check("`--record-corr` 在 reuse_v2 路径上真的写出 `corr`（v2 的相关系数在 decide 里算，要取 last_corr）",
          "corr" in v.buf[1][0], v.buf[1][0])


def t_ocr_complete() -> None:
    print("ocr_complete.is_complete")
    from flowocr.extract import ocr_complete as oc

    from flowocr.extract.framegrid import AuxGrid, TimeGrid

    def sf(e, last, step):
        return oc.aux_shortfall(e, last, AuxGrid(TimeGrid.every(step), TimeGrid.every(30)))
    check("aux_shortfall：辅助流 pts 对不上 -> 判未完成；没盖到采样流最后一帧 -> 判未完成；"
          "**有缺口素材上的 aux_stopped_early 不算**（单路每一臂都是 True）；没用辅助流 / 旧产物没 last_key 不判",
          sf({"aux_sent": 5, "aux_misaligned": "第 3 帧…"}, 100, 1) is not None
          and sf({"aux_sent": 5, "aux_last_key": 80}, 100, 1) is not None
          and sf({"aux_sent": 5, "aux_last_key": None}, 100, 1) is not None
          and sf({"aux_sent": 5, "aux_last_key": 100, "aux_stopped_early": True}, 100, 1) is None
          and sf({"aux_sent": 5, "aux_last_key": 98}, 100, 3) is None
          and sf({"aux_sent": 5, "aux_last_key": 97}, 100, 3) is not None
          and sf({"on": 3}, 100, 1) is None and sf({"aux_sent": 5}, 100, 1) is None and sf(None, 100, 1) is None)
    _ng = AuxGrid(TimeGrid(5, 12), TimeGrid.every(30))   # 60 fps 给 25：采样帧嵌套，最后一个辅助帧至少要到采样流的最后一帧
    check("aux_shortfall：不等距网格要盖到采样流最后一帧（嵌套，采样帧一定在辅助流里）",
          oc.aux_shortfall({"aux_sent": 5, "aux_last_key": 90}, 90, _ng) is None
          and oc.aux_shortfall({"aux_sent": 5, "aux_last_key": _ng.prev(90)}, 90, _ng) is not None)
    _ro2 = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    check("run_ocr2：辅助流没交够一票否决 complete（接线）",
          "and bad_pts is None and aux_bad is None" in _ro2 and "ocr_complete.aux_shortfall(" in _ro2)
    from flowocr.extract import timeline as _tlm
    _old = (_tlm._ev, _tlm._path, _tlm._role, _tlm._dropped, _tlm.MAX_EVENTS)
    try:
        _tlm._ev, _tlm._dropped, _tlm.MAX_EVENTS = [], 0, 3
        for _ in range(5):
            _tlm.mark("x")
        check("timeline：事件封顶，超了丢掉并记数（整片开时间线不会把内存吃光）", len(_tlm._ev) == 3 and _tlm._dropped == 2)
    finally:
        _tlm._ev, _tlm._path, _tlm._role, _tlm._dropped, _tlm.MAX_EVENTS = _old

    check("请求 36,361 帧、实际推进 36,001（容器元数据多报 1%）算跑完",
          oc.is_complete(36001, 36361))
    check("**解码器读到头也会提前返回，不能因此判未完成**——"
          "九段切片就是这么每次跑完都自称未完成的",
          oc.is_complete(36001, 36361) is True)
    check("中途真断了（40%）判未完成", not oc.is_complete(14000, 36361))
    check("请求远超片长（0.4%）判未完成", not oc.is_complete(601, 144600))
    check("刚好卡在容差上算跑完", oc.is_complete(98, 100))
    check("差一点点就不算", not oc.is_complete(97, 100))
    check("want 为 0 时不除零", oc.is_complete(1, 0))

    # 产物侧的复用判据（audit-5 §3）：三个驱动脚本以前各自内联一份，
    # `run_full_ocr.sh` 那份根本没读 `_meta`、只看 `[ -s ]`。
    import json

    def obs(d: Path, name: str, meta: dict | None, body: str = "") -> Path:
        p = d / name
        p.write_text(("" if meta is None else json.dumps({"_meta": meta}) + "\n") + body,
                     encoding="utf-8")
        return p

    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        from flowocr.extract import framesource as _fs
        DEC = _fs.DEFAULT_DECODER
        ok_meta = {"complete": True, "timebase": "pts", "decoder": DEC, "src_fps": 60.0, "stride": 30,
                   "start_sec": 100.0, "end_sec": 400.0}
        done = obs(dd, "done.jsonl", ok_meta)
        half = obs(dd, "half.jsonl", {"complete": False, "timebase": "pts", "decoder": DEC,
                                      "start_sec": 100.0, "end_sec": 400.0},
                   '{"frame": 1}\n' * 50)
        # 旧口径的产物：`t_us = idx / src_fps`，`_meta` 里根本没有 timebase 这个键。
        # 它的 `complete` 也是 true——**这正是它危险的地方**。
        old = obs(dd, "old.jsonl", {"complete": True,
                                    "start_sec": 100.0, "end_sec": 400.0})
        check("跑完了就能复用", oc.obs_reusable(done)[0])
        check("**旧时间轴的产物不能复用**（`complete` 也是 true，"
              "不拦的话 sampleset 会一份都不重跑，代表性片段集停在错的时间轴上）",
              not oc.obs_reusable(old)[0])
        check("拦下来时要说得出是时间轴的事", "pts" in oc.obs_reusable(old)[1])
        check("将来换了别的时间轴口径同样拦",
              not oc.obs_reusable(obs(dd, "idx.jsonl",
                                      {"complete": True, "timebase": "index",
                                       "decoder": DEC}))[0])
        # **两种解码器的产物不混用**（owner 2026-09-10）：默认一改，磁盘上旧默认产的
        # 那批 `complete` 仍是 true、`timebase` 也是 pts——不拦就会被驱动脚本跳过，
        # 于是同一批产物里两种解码器混着，而且不报错。
        other = "cv2" if DEC != "cv2" else "ffmpeg"
        mixed = obs(dd, "mixed.jsonl", {**ok_meta, "decoder": other})
        check("**不是当前默认解码器产的观测不能复用**（不混用）",
              not oc.obs_reusable(mixed)[0])
        check("拦下来时要说得出是解码器的事", "解码器" in oc.obs_reusable(mixed)[1])
        check("没记 decoder 的旧产物也拦（它们本来也没有 timebase）",
              not oc.obs_reusable(obs(dd, "nodec.jsonl",
                                      {"complete": True, "timebase": "pts"}))[0])
        check("**半截产物不能复用**（非空，`[ -s ]` 会放过它）", not oc.obs_reusable(half)[0])
        check("不能复用时要说得出原因", "complete" in oc.obs_reusable(half)[1])
        check("文件不在也不抛，只是不能复用",
              not oc.obs_reusable(dd / "nope.jsonl")[0])
        check("空文件不抛", not oc.obs_reusable(obs(dd, "empty.jsonl", None))[0])
        check("CLI 退出码：能复用 0", oc._main([str(done)]) == 0)
        check("CLI 退出码：不能复用 1", oc._main([str(half)]) == 1)
        # 读的一侧（gametext_eval）只问写完没有：整场 OCR 跑着的时候观测已经非空
        check("**半截产物不算写完**（整场 OCR 跑着时 eval 不能读它）",
              not oc.obs_finished(half)[0] and oc._main(["--finished", str(half)]) == 1)
        check("旧时间轴 / 旧解码器的产物照读（文本检索不看时间）",
              oc.obs_finished(old)[0] and oc.obs_finished(mixed)[0]
              and oc._main(["--finished", str(old)]) == 0)
        check("--finished 不抛：文件不在、空文件",
              not oc.obs_finished(dd / "nope.jsonl")[0]
              and not oc.obs_finished(obs(dd, "empty2.jsonl", None))[0])
        check("窗口只有一种判法：没有 start/end 容差那条路了（审查 2026-09-10）",
              not hasattr(oc, "WINDOW_TOL_SEC")
              and list(inspect.signature(oc.obs_reusable).parameters) == ["path", "argv"])

        # 参数这一维（2026-09-10）：A/B 两条臂只差几个旋钮时，文件名一样就会
        # **静默复用另一条臂的产物**，而且更好看。`head2head.sh` 的
        # `same_build_args` 早为 build_tracks 堵过同一个洞，OCR 这级当时没堵。
        # 比的是**解析后的生效值**（`_meta.config`），不是命令行字面：
        # 字面比不出"默认值翻了"（审查 2026-09-10，tools/ocr_args.py 文件头）。
        from flowocr.extract import ocr_args

        vid = str(dd / "v.mp4")
        arm = [vid, "--out", "a.jsonl", "--rec-bucket", "8"]
        armed = obs(dd, "armed.jsonl", {**ok_meta, "config": ocr_args.config_of(arm)})
        check("参数对得上就能复用", oc.obs_reusable(armed, argv=arm)[0])
        check("**换了旋钮的产物不能复用**（A/B 静默串臂）",
              not oc.obs_reusable(armed, argv=[vid, "--out", "a.jsonl"])[0])
        check("参数对不上要说出是哪个键",
              "rec_bucket" in oc.obs_reusable(armed, argv=[vid, "--out", "a.jsonl"])[1])
        # 这一条是字面比较**做不到**的：命令行一字不差，产物却是旧默认值产的
        stale = obs(dd, "stale.jsonl",
                    {**ok_meta, "config": {**ocr_args.config_of(arm), "refresh_every": 3}})
        check("**命令行一字不差、但产物是旧默认值产的 → 不能复用**（字面比不出来的那种）",
              not oc.obs_reusable(stale, argv=arm)[0])
        check("旋钮顺序不同、生效值相同 → 能复用（比的是值，不是字面）",
              oc.obs_reusable(armed, argv=[vid, "--rec-bucket", "8", "--out", "a.jsonl"])[0])
        check("`--out` / `--progress-every` 不算产物参数",
              oc.obs_reusable(armed, argv=arm[:2] + ["b.jsonl", "--rec-bucket", "8",
                                                     "--progress-every", "5"])[0])
        check("同一个视频换成相对 / 绝对写法不算两条臂",
              ocr_args.config_of(["v.mp4", "--out", "x"])["video"]
              == str(Path("v.mp4").resolve()))
        # record_corr 是有意的唯一例外（2026-09-14，综合清单 X3）：只多写 obs 行上的 `corr` 字段、不改判决 / 文本 / 框，
        # 算进配置的代价是全部现成 obs 重跑；"开和不开去掉 corr 后逐行相同"在 quick 六段上核过（try-list-results 报告 X3）
        # refine_ring 只管帧环装多少帧（什么时候解码）：容量有下限 max(5, strides)，只能更大，不会少量出证据
        # ⚠ `refine_aux` **2026-09-17 挪出这个集合**（审计）：noop 根本不产 edge、nvdec/cuvid 产的 edge 不一样，
        #    config 相等就会让空臂的 obs 被当默认产物复用（见 ocr_args.OPT_IN_FUSED 那段）
        # `trace` 同 `record_corr` 的理由（2026-09-18）：只把复用判据的因果**另写一个文件**，
        # 不改判决 / 文本 / 框——进配置就是全部现成 obs 重跑，而它连 obs 都不碰
        check("不产物的参数只收这五个（输出去哪、打印多勤、只多写 corr 字段、帧环大小、另写一份因果轨迹）",
              ocr_args.NON_PRODUCT == {"out", "progress_every", "record_corr", "refine_ring", "timeline", "trace"},
              sorted(ocr_args.NON_PRODUCT))
        # owner 2026-09-16：三个提速旋钮进默认（docs/architecture/defaults.md §4.0）。上面那几条臂因此改用 --rec-bucket 16
        dflt = ocr_args.config_of([vid, "--out", "x"])
        # ⚠ `--workers` **2026-09-19 被 owner 改回 1**："默认 worker 改到 1，不容易出问题；
        #    确认显存时可传 3；等做完 worker 间的资源共享可以重新放宽要求。"
        #    3 个 worker 各开一套模型、显存按份数线性涨，而跑它的机器往往同时是日常用机（开发机 8 GB 配额）。
        #    它**改产物**（切点之后差几行闪烁的 UI，defaults.md §4.0.2），所以在复用判据里，
        #    翻默认 = **磁盘上所有现成 obs 的 config 都对不上、驱动会一律重建**。
        check("默认 rec_bucket 16 / det_batch 4 / **workers 1**（rec_bucket owner 09-21 从 32 降到 16；workers 09-19 改回 1）",
              (dflt["rec_bucket"], dflt["det_batch"], dflt["workers"]) == (16, 4, 1), dflt)
        off = ocr_args.config_of([vid, "--out", "x", "--no-reuse-v2"])
        check("**`--reuse-v2` 是默认**（owner 09-17）：那组参数默认就在 config 里，`--no-reuse-v2` / `--no-reuse` 时一个不留（OPT_IN）",
              ocr_args.OPT_IN <= set(dflt) and not (set(off) & ocr_args.OPT_IN)
              and not (set(ocr_args.config_of([vid, "--out", "x", "--no-reuse"])) & ocr_args.OPT_IN))
        check("**`--reuse-corr` / `--refresh-every` 的默认跟着复用判据走**：v2 时 0.8 / 32，旧判据 0.98 / 4（旧判据上 0.8 会放过换字）；"
              "config 里记的是生效值、没有 None",
              (dflt["reuse_corr"], dflt["refresh_every"]) == (0.8, 32) and (off["reuse_corr"], off["refresh_every"]) == (0.98, 4)
              and ocr_args.config_of([vid, "--out", "x", "--reuse-corr", "0.9"])["reuse_corr"] == 0.9
              and all(v is not None for k, v in dflt.items() if k != "predet"))
        check("**只有 argv、没有 config 的产物一律重建**（上一版就是这么记的）",
              not oc.obs_reusable(obs(dd, "argvonly.jsonl", {**ok_meta, "argv": arm}),
                                  argv=arm)[0])
        check("没有 config 字段时说得出原因",
              "config" in oc.obs_reusable(done, argv=arm)[1])
        check("不传 argv 时不检查这一维", oc.obs_reusable(armed)[0])
        try:
            oc.obs_reusable(armed, argv=[vid, "--out", "a.jsonl", "--rec-bukcet", "32"])
            raised = False
        except SystemExit:
            raised = True
        check("**驱动拼错参数要在判据这一步就炸**，不能静默判「不能复用」再去跑", raised)
        check("CLI 也认参数",
              oc._main([str(armed), "--argv", *arm]) == 0
              and oc._main([str(armed), "--argv", vid, "--out", "a.jsonl"]) == 1)
        # 窗口就在生效配置里：换了窗口 = 换了参数
        win = [vid, "--out", "w.jsonl", "--start", "100", "--end", "400"]
        windowed = obs(dd, "win.jsonl", {**ok_meta, "config": ocr_args.config_of(win)})
        check("同一个窗口能复用", oc.obs_reusable(windowed, argv=win)[0])
        check("**换了窗口的旧产物不能复用**（窗口在配置里比）",
              not oc.obs_reusable(windowed, argv=[vid, "--out", "w.jsonl", "--start", "700",
                                                  "--end", "900"])[0])
        check("窗口写法不同、值相同 → 能复用（100 和 100.0 是同一个值）",
              oc.obs_reusable(windowed, argv=[vid, "--out", "w.jsonl", "--start", "100.0",
                                              "--end", "400.0"])[0])
        check("**timebase 那道门独立于参数**（旧时间轴的产物参数对上了也不行）",
              not oc.obs_reusable(obs(dd, "oldcfg.jsonl",
                                      {"complete": True, "config": ocr_args.config_of(win)}),
                                  argv=win)[0])
        src = (ROOT / "src" / "flowocr" / "extract" / "run_ocr2.py").read_text(encoding="utf-8")
        check("run_ocr2 用的是共享层那一个解析器、而且过 resolve（默认值跟着别的参数走的那几个，两边解析出同一组生效值）",
              "ocr_args.parse_args()" in src and "build_parser()" not in src
              and "add_argument(" not in src)
        snap, split = src.find("config = ocr_args.product_config(args)"), src.find("ocr_args.resolve_split(args)")
        check("run_ocr2 把生效配置写进 _meta.config，且在拆进程自动关之前取（config 记请求的值）",
              0 <= snap < split and '"config": config' in src, (snap, split))


def t_ocr_ab_report() -> None:
    """OCR 级 A/B 的观测对账（审查 2026-09-10）：**只数文本会漏掉跨 conf 门的行**。"""
    print("ocr_ab_report.compare_obs")
    from ocr_ab_report import compare_obs

    def row(f, box, text, conf):
        return {"frame": f, "box": box, "text": text, "conf": conf}

    a = [row(0, [0, 0, 9, 9], "あいう", 0.99), row(0, [5, 5, 9, 9], "X", 0.501),
         row(30, [0, 0, 9, 9], "あいう", 0.99)]
    same = compare_obs(a, [dict(r) for r in a])
    check("两臂一样：identical，文本改 0", same["identical"] and same["text_diff"] == 0)
    b = [dict(r) for r in a]
    b[1]["conf"] = 0.499
    c = compare_obs(a, b)
    check("**文本没变、conf 跨过 0.5 门也要数出来**（上一版的表漏的就是这一栏）",
          c["text_diff"] == 0 and c["gate_cross_same_text"] == 1 and c["fate_changed"] == 1)
    b[0]["text"] = "あいえ"
    c = compare_obs(a, b)
    check("文本变 + 跨门：命运变 = 并集，不重复计",
          c["text_diff"] == 1 and c["fate_changed"] == 2)
    b[2]["text"] = "あいえ"
    b[2]["conf"] = 0.3
    check("同一行既改文本又跨门只算一次", compare_obs(a, b)["fate_changed"] == 3
          and compare_obs(a, b)["gate_cross_same_text"] == 1)
    check("conf 门可调", compare_obs(a, [dict(r, conf=0.8) for r in a], min_conf=0.9)
          ["gate_cross"] == 2)
    shifted = [dict(r) for r in a]
    shifted[1]["box"] = [5, 5, 9, 10]
    c = compare_obs(a, shifted)
    # ⚠ 这条 2026-09-20 改了契约：原来是"框序列不同就返回 None、不比"，
    # 意图（不许按下标错位比）对，但**汇总那边把 None 当 0 加**，印出假的"文本改 0"。
    # 现在改成**按 (帧, 框) 配对比**——错位在结构上不可能发生，而且换 det 引擎那种
    # 框会变的臂也对得了账。`cmp_rows` 是真比过的行数，报数必须拿它当分母。
    check("**框序列不同时按 (帧,框) 配对比，不按下标**",
          not c["same_boxes"] and c["text_diff"] is not None and c["cmp_rows"] <= len(a))
    check("行数不同也算框不同", not compare_obs(a, a[:2])["same_boxes"])


def t_reuse_confirm() -> None:
    """`--reuse-confirm`（2026-09-22，decode-buffer §8.8）：没被第二次真读证实的文本走严门。

    修的病：打字机中间态被真读之后，句尾再长一两个字只占整框很小的像素份额，整框相关系数 0.94 过得了非数字的 0.8 门，
    **半截读数被一路沿用到字消失**（yuka-f5 14954 s 缺句尾 `も` 6 秒）。"""
    print("reuse_confirm")
    import io
    import types

    import numpy as np

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.extract import ocr_args
    from flowocr.extract.reuse_v2 import ReuseV2

    box = [[0, 0, 60, 18]]
    poly = [[[0, 0], [60, 0], [60, 18], [0, 18]]]

    def make(*extra):
        args = ocr_args.parse_args(["dummy.mp4", "--out", "x.jsonl", "--no-refine-fused", *extra])
        state = {"corr": 1.0}
        return ReuseV2(args, lambda a, b: state["corr"], lambda a, b: (0, 0), lambda crop: ("?", 0.9)), state

    def step(v, state, seq, corr, read_text):
        """这一帧的像素相关系数是 corr；要真读的话读到 read_text。返回 (是否真读, 这一帧落进行里的文本)。"""
        state["corr"] = corr
        dec, todo, assign = v.decide(seq, np.zeros((40, 200), np.uint8), box)
        dec = list(dec) + [(i, read_text, 0.99, 0) for i in todo]
        v.commit(seq, seq * 30, seq * 500_000, box, poly, dec, set(todo), assign,
                 lambda i: np.zeros((18, 60, 3), np.uint8), {}, False, io.StringIO())
        return bool(todo), v.buf[seq][0]["text"]

    # f5 的形状：先读到半截（新链），下一帧句尾长出来、整框相关系数 0.94
    v, st = make()
    step(v, st, 0, 1.0, "連帯感に")
    read, text = step(v, st, 1, 0.94, "連帯感にも")
    check("未证实的新链 + 相关系数 0.94 -> **真读**，读到完整句尾（不再被沿用成半截）",
          read and text == "連帯感にも" and v.stats["gate_unconfirmed"] == 1, (read, text, v.stats["gate_unconfirmed"]))
    v0, st0 = make("--no-reuse-confirm")
    step(v0, st0, 0, 1.0, "連帯感に")
    read0, text0 = step(v0, st0, 1, 0.94, "連帯感にも")
    check("关掉 --reuse-confirm 就复现原来的病：0.94 过 0.8 门、半截被沿用",
          not read0 and text0 == "連帯感に", (read0, text0))

    # 两次真读逐字相同 -> 证实 -> 之后照旧用宽松门（背景一动就重读是 reuse-v2 当初放宽的理由，不动它）
    v, st = make()
    step(v, st, 0, 1.0, "ABC")
    step(v, st, 1, 0.5, "ABC")                     # 相关系数 0.5：任何门都拦，真读，读到同一串 -> 证实
    read, _ = step(v, st, 2, 0.94, "XYZ")
    check("证实过的链：0.94 过宽松门、沿用", not read and v.live[0].confirmed, (read, v.live[0].confirmed))
    step(v, st, 3, 0.5, "ABD")                     # 真读读到不一样的 -> 回到未证实
    after_change = v.live[0].confirmed
    read, _ = step(v, st, 4, 0.94, "ABD")          # 未证实 -> 0.94 过不了严门 -> 真读，读到同一串 -> 再证实
    check("文本变了就回到未证实、下一帧再走严门（真读），读到同一串才重新证实",
          after_change is False and read and v.live[0].confirmed is True, (after_change, read, v.live[0].confirmed))
    check("参数进了产物配置（改产物、所以旧 obs 要重建），关着 --reuse-v2 时不写",
          ocr_args.config_of(["x.mp4", "--out", "o.jsonl"]).get("reuse_confirm") is True
          and "reuse_confirm" not in ocr_args.config_of(["x.mp4", "--out", "o.jsonl", "--no-reuse-v2"]))

    # `--reuse-dw-anchor`（decode-buffer §8.12）：字从面板边缘一点点露出来，框每个采样点只宽 3 px。
    # 宽度门和上一个采样点比就永远过门；和**真读那一帧**比，累计宽过 --reuse-dw 8 就真读
    def step_w(v, state, seq, w, read_text):
        bx = [[0, 0, w, 18]]
        px = [[[0, 0], [w, 0], [w, 18], [0, 18]]]
        state["corr"] = 1.0
        dec, todo, assign = v.decide(seq, np.zeros((40, 200), np.uint8), bx)
        dec = list(dec) + [(i, read_text, 0.99, 0) for i in todo]
        v.commit(seq, seq * 30, seq * 500_000, bx, px, dec, set(todo), assign,
                 lambda i: np.zeros((18, w, 3), np.uint8), {}, False, io.StringIO())
        return bool(todo), v.buf[seq][0]["text"]

    for flag in ("--reuse-dw-anchor", "--no-reuse-dw-anchor"):
        v, st = make(flag)
        step_w(v, st, 0, 90, "白銀みさ")
        step_w(v, st, 1, 90, "白銀みさ")                 # 第二次真读：证实
        got = [step_w(v, st, k, 90 + 3 * (k - 1), "白銀みさき") for k in range(2, 7)]   # 93, 96, 99, 102, 105
        reads = [r for r, _ in got]
        if flag == "--reuse-dw-anchor":
            check("--reuse-dw-anchor：累计宽了 9 px（> --reuse-dw 8）那一帧真读、读到末字",
                  reads == [False, False, True, False, False] and got[-1][1] == "白銀みさき", got)
        else:
            check("关掉它就复现原来的病：每步 3 px、永远过宽度门，末字缺着被沿用",
                  not any(reads) and got[-1][1] == "白銀みさ", got)
    check("参数进了产物配置（改产物）",
          ocr_args.config_of(["x.mp4", "--out", "o.jsonl"]).get("reuse_dw_anchor") is True)


def t_detpost() -> None:
    """det 不借 Paddle（2026-09-25，`flowocr.extract.detpost`）：配置从官方 yml 读出来的就是 PaddleX 会用的那组；
    照抄的 DBPostProcess 在 40 张随机概率图上的输出和冻结的结果逐字节相同（回归：`tests/_oracle_dbpost.npz` 是 2026-10-02
    拿 PaddleX 3.7.2 的原实现在同一批输入上产的，输入一起存着，不随 cv2 版本变；要重冻得另开装了 paddlex 的环境）；
    `src/` 下任何地方都不 import paddle*（2026-10 运行时去掉了 Paddle，静态查）。"""
    print("detpost（det 的配置与后处理不借 Paddle）")
    import ast
    import random

    # 前面几条守卫把 cv2 桩成空模块（`sys.modules.setdefault`）；这里的对拍要**真 cv2**（findContours / minAreaRect）。
    # 有真的就换回来、detpost 重载让它绑到真的；没有（系统 Python）就只做 ① / ③
    _stub = sys.modules.get("cv2")
    if _stub is not None and not hasattr(_stub, "findContours"):
        del sys.modules["cv2"]
    try:
        import cv2
    except ImportError:
        cv2 = None
        if _stub is not None:
            sys.modules["cv2"] = _stub
    from flowocr.extract import detpost
    if cv2 is not None and detpost.cv2 is not cv2:
        detpost = importlib.reload(detpost)

    # ① 官方 yml -> 和 PaddleX 同一组参数（数值是 2026-09-25 从 Paddle predictor 上 dump 下来的，tmp/det_params.py）
    yml = ROOT / "tests" / "_det_inference.yml"
    spec = detpost.DetSpec.from_yml(yml)
    check("官方 det yml 解析出的缩放 / 归一化 / 阈值和 PaddleX 的一致（960/max、BGR、thresh 0.2 / box 0.45 / unclip 1.4 / 3000 候选）",
          (spec.limit_side_len, spec.limit_type, spec.max_side_limit, spec.img_mode) == (960, "max", 4000, "BGR")
          and spec.alpha == (1.0 / 255.0 / 0.229, 1.0 / 255.0 / 0.224, 1.0 / 255.0 / 0.225)
          and spec.beta == (-0.485 / 0.229, -0.456 / 0.224, -0.406 / 0.225)
          and (spec.thresh, spec.box_thresh, spec.unclip_ratio, spec.max_candidates) == (0.2, 0.45, 1.4, 3000), spec)
    # ② 后处理回归：冻结的 PaddleX 输出当基准（输入和输出都在 npz 里）
    if cv2 is None:
        print("        （这个解释器没有 cv2：跳过后处理回归）")
    else:
        import numpy as np
        fx = np.load(ROOT / "tests" / "_oracle_dbpost.npz")
        mine = detpost.DBPostProcess(spec)
        same = total = 0
        for i in range(40):
            preds = [fx[f"pm{i}"][None, None]]
            h4, w4, ry, rx = fx[f"shape{i}"].tolist()
            shape = (int(h4), int(w4), ry, rx)
            ob, os_ = fx[f"box{i}"], list(fx[f"score{i}"])
            mb, ms = mine(preds, [shape])
            total += len(os_)
            same += (np.array_equal(ob, mb[0]) and ob.dtype == mb[0].dtype and ob.shape == mb[0].shape
                     and os_ == list(ms[0]))
        check(f"DBPostProcess 照抄版的输出和冻结的 PaddleX 结果逐字节相同（40 张随机概率图、{total} 个框）", same == 40, same)
    # ③ 运行时没有 Paddle：`src/` 下任何 .py 都不 import paddle / paddleocr / paddlex（含函数里的延迟 import）
    hits = []
    for f in sorted((ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            hits += [f"{f.relative_to(ROOT).as_posix()}:{node.lineno} {m}" for m in mods if m.split(".")[0].startswith("paddle")]
    check("src/ 下任何地方都不 import paddle*（2026-10 运行时去掉了 Paddle）", not hits, hits)
    src = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    check("gpu_blocker 只问 onnxruntime 的 provider（便宜检查不建模型）", "get_available_providers" in src)
    _srv = (ROOT / "src/flowocr/extract/ort_server.py").read_text(encoding="utf-8")
    check("ort_server 有 --threads / --spin（CPU 路径的线程池旋钮；关自旋 gi-s1 60 s 64.1 → 51.3 s）",
          "intra_op_num_threads = a.threads" in _srv and "session.intra_op.allow_spinning" in _srv)



def t_audit_perf0925() -> None:
    """perf-nightly-0925 实验 审计（2026-09-25，Claude / Codex 两份）的整改，逐条钉行为：
    ① det 的 yml 读法对着冻结的 PaddleX `_build` 结果（几种写法，`tests/_oracle_dbpost.npz` 同一次冻结的 `_oracle_detspec.json`）；② 自带 ONNX 缺 yml 要报错、模型名读 yml；
    ③ det 流水 0 / 1 同序、尾批、异常、提前关；④ 字节预算的等待里主流交到块尾要放行；⑤ 子进程没清干净不重试；
    ⑥ `--rec-inflight auto`、`--det-lookahead` 只收 0 / 1、`--ort-server-extra` 进 config。"""
    print("audit_perf0925（perf-nightly 审计整改）")
    import ast
    import copy
    import threading as _th
    import time as _time
    from types import SimpleNamespace as NS
    from unittest import mock

    import yaml

    _stub = sys.modules.get("cv2")
    if _stub is not None and not hasattr(_stub, "findContours"):
        del sys.modules["cv2"]
        try:
            import cv2  # noqa: F401
        except ImportError:
            sys.modules["cv2"] = _stub
    from flowocr.extract import detpost

    # ① yml 的几种写法：冻结的 PaddleX `_build` 结果当基准（2026-10-02 PaddleX 3.7.2 产，DetSpec 全字段），另比写死的期望
    base = yaml.safe_load((ROOT / "tests" / "_det_inference.yml").read_text(encoding="utf-8"))

    def variant(fn):
        c = copy.deepcopy(base)
        fn(c)
        return c

    def op(c, name):
        return next(o for o in c["PreProcess"]["transform_ops"] if list(o)[0] == name)

    def set_rz(c, d):
        op(c, "DetResizeForTest")["DetResizeForTest"] = d

    def drop(c, name):
        c["PreProcess"]["transform_ops"] = [o for o in c["PreProcess"]["transform_ops"] if list(o)[0] != name]

    def norm(c, k, v=None):
        d = op(c, "NormalizeImage")["NormalizeImage"]
        if v is None:
            d.pop(k)
        else:
            d[k] = v

    variants = {
        "官方": (variant(lambda c: None), (960, "max", "BGR")),
        "yml 写了 limit_type / limit_side_len（PaddleX 不看）": (variant(lambda c: set_rz(c, {"limit_type": "min", "limit_side_len": 64})),
                                                             (960, "max", "BGR")),
        "resize_long 1280": (variant(lambda c: set_rz(c, {"resize_long": 1280})), (1280, "max", "BGR")),
        "没有 DecodeImage（PaddleX 默认 RGB）": (variant(lambda c: drop(c, "DecodeImage")), (960, "max", "RGB")),
        "Normalize 没写 order（按 hwc）": (variant(lambda c: norm(c, "order")), (960, "max", "BGR")),
        "scale 写成 1/255": (variant(lambda c: norm(c, "scale", "1/255")), (960, "max", "BGR")),
        "非长边模型（默认 736 / min）": (variant(lambda c: c["Global"].__setitem__("model_name", "PP-OCRv4_server_seal_det")),
                                  (736, "min", "BGR")),
    }
    import json
    frozen = json.loads((ROOT / "tests" / "_oracle_detspec.json").read_text(encoding="utf-8"))

    def oracle(name):
        d = frozen[name]
        return detpost.DetSpec(**{k: tuple(v) if isinstance(v, list) else v for k, v in d.items()})

    with tempfile.TemporaryDirectory() as td:
        yml = Path(td) / "inference.yml"
        bad = []
        for name, (cfg, want) in variants.items():
            yml.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
            spec = detpost.DetSpec.from_yml(yml)
            if (spec.limit_side_len, spec.limit_type, spec.img_mode) != want or spec.alpha[0] != 1 / 255 / 0.229:
                bad.append((name, spec))
            if name not in frozen or oracle(name) != spec:
                bad.append((name, "和冻结的 PaddleX 结果不一致", spec, frozen.get(name)))
        check(f"det yml 的 {len(variants)} 种写法读出的参数和冻结的 PaddleX `_build` 结果一致（全字段）",
              not bad and set(frozen) == set(variants), bad)
        rejected = []
        for name, fn in {"Normalize chw": lambda c: norm(c, "order", "chw"),
                         "image_shape 定形缩放": lambda c: set_rz(c, {"image_shape": [640, 640]}),
                         "认不得的 op": lambda c: c["PreProcess"]["transform_ops"].append({"Pad": None}),
                         "scale 是任意表达式": lambda c: norm(c, "scale", "__import__('os').getpid()")}.items():
            yml.write_text(yaml.safe_dump(variant(fn), allow_unicode=True), encoding="utf-8")
            try:
                detpost.DetSpec.from_yml(yml)
            except ValueError:
                rejected.append(name)
        check("det yml 里快路径没写的形态（chw / 定形缩放 / 生 op / 表达式 scale）都拒绝，不 eval", len(rejected) == 4, rejected)

        # ② 自带 ONNX（`--det-onnx`）：缺 yml 且不是默认那份 -> 报错；有 yml 时模型名和配置都从它来（`--det-model` 2026-10 删了）
        from flowocr.extract.fast_det import FastDet
        onnx = Path(td) / "sub" / "my_det.onnx"
        onnx.parent.mkdir()
        onnx.write_bytes(b"")
        errs = []
        try:
            FastDet.from_onnx(str(onnx), None, default_yml_ok=False)
        except ValueError as exc:
            errs.append(str(exc))
        (onnx.parent / "inference.yml").write_text(yaml.safe_dump(variant(
            lambda c: c["Global"].__setitem__("model_name", "PP-OCRv4_server_seal_det"))), encoding="utf-8")
        ok = FastDet.from_onnx(str(onnx), None, default_yml_ok=False)
        check("自带 det ONNX：缺 yml 报错（不再悄悄套默认模型的阈值）；有 yml 时模型名和缩放都读它（换模型不靠 --det-model）",
              len(errs) == 1 and "inference.yml" in errs[0]
              and (ok.spec.model_name, ok.spec.limit_side_len, ok.spec.limit_type) == ("PP-OCRv4_server_seal_det", 736, "min"), errs)

    # ③ det 流水：把 det_stream 从源码抠出来跑（不 import run_ocr2），假检测器按批记账
    tree = ast.parse((ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8"))

    def lift(name, ns):
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module([fn], []), f"run_ocr2.{name}", "exec"), ns)
        return ns[name]

    det_stream = lift("det_stream", {"itertools": __import__("itertools"), "time": _time, "stage": lambda k, t0: None})

    class FakeDet:
        def __init__(self, fail_at=None):
            self.calls, self.fail_at = 0, fail_at

        def infer_batch(self, imgs):
            self.calls += 1
            if self.fail_at is not None and self.calls == self.fail_at:
                raise RuntimeError("假的：推理炸了")
            return ("x", [int(im) for im in imgs])

        def post_batch(self, state):
            return [((v, len(state[1])), None) for v in state[1]]

        def predict_batch(self, imgs):
            return self.post_batch(self.infer_batch(imgs))

    def seq(n, batch, look, fail_at=None, take=None):
        g = det_stream(iter([(i, i / 2, i) for i in range(n)]), FakeDet(fail_at), batch, False, look)
        out = []
        try:
            for item in g:
                out.append((item[0], item[3]))
                if take is not None and len(out) >= take:
                    g.close()
                    break
        except RuntimeError as exc:
            out.append(("err", str(exc)))
        return out

    same = all(seq(n, b, 0) == seq(n, b, 1) for n in range(0, 18) for b in (2, 4))
    err0, err1 = seq(11, 2, 0, fail_at=3), seq(11, 2, 1, fail_at=3)
    t = _th.Thread(target=lambda: seq(40, 4, 1, take=3), daemon=True)
    t.start()
    t.join(5)
    check("det 流水：0 / 1 两档同序同值（0~17 帧、批 2 / 4，含尾批）；推理出错两档交出同样多帧再抛同一个错；提前关不卡",
          same and err0 == err1 and err1[-1] == ("err", "假的：推理炸了") and len(err1) == 5 and not t.is_alive(), (err0, err1))

    # ④ 字节预算的等待里主流交到块尾：辅助帧要放行（原来 main_eof 只在进等待前算一次，Codex P1）
    from flowocr.extract import decode_shards as dsh
    s = dsh.ShardedSource("v", lanes=["hw"], blocks=[(0, 60, None)], start_idx=0, end_idx=60, grid=_every(30), src_fps=60.0,
                          width=4, height=4, hwaccel="cuda", aux_remote=None, budget_mb=0.00001)
    b = NS(last_main=None, hi=60, waiting=0, throttled=False, nbytes=0)
    s._buffered = 10_000
    done = []

    def admit():
        with s._cv:
            s._admit(b, 100)
        done.append(True)

    th = _th.Thread(target=admit, daemon=True)
    th.start()
    _time.sleep(0.3)
    blocked = not done
    with s._cv:
        b.last_main = 30                                          # 30 是块里最后一个采样格（下一格 60 = 块尾）
        s._cv.notify_all()
    th.join(3)
    check("ShardedSource：辅助帧被预算挡着时主流交到块尾，等待当场放行（不等到收摊）", blocked and done == [True], (blocked, done))
    s.close()

    # ⑤ 硬解交付不对、子进程没清干净：监督者退 EXIT_HW_UNCLEAN（不是 75），广播那条路 run_groups 就不整批重跑（Codex P1）
    from flowocr.extract import supervisor as sv
    runs = []

    def fake_run(argv, env):
        runs.append(argv)
        return sv.EXIT_HW_MISMATCH, False

    with mock.patch.object(sv, "_run_worker", fake_run), mock.patch.dict(sv.os.environ, {"FLOWOCR_DECODE_HUB": "tcp://x"}):
        rc_hub = sv.supervise(["v"])
    runs_hub = len(runs)
    env = {k: v for k, v in sv.os.environ.items() if k != "FLOWOCR_DECODE_HUB"}
    with mock.patch.object(sv, "_run_worker", fake_run), mock.patch.dict(sv.os.environ, env, clear=True):
        rc_one = sv.supervise(["v"])
    check("硬解交付不对 + 子进程没清干净：监督者退 EXIT_HW_UNCLEAN（单组不起第二趟；广播那条路也不交给 run_groups 重跑）",
          rc_hub == rc_one == sv.EXIT_HW_UNCLEAN and runs_hub == 1 and len(runs) == 2, (rc_hub, rc_one, len(runs)))

    # run_groups.main 整条跑（假的组 / 广播进程 / 共享服务，不起任何子进程）：这一批自己起的进程没确认退出就不整批重跑（Codex 复审 P1）
    import types as _types
    from flowocr.extract import run_groups as rgm

    class Done:
        def __init__(self, rc):
            self.returncode = rc

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            pass

    class Srv:
        def __init__(self, stuck):
            self.stuck, self.dead = stuck, False

        def kill(self):
            self.dead = not self.stuck

        def poll(self):
            return 0 if self.dead else None

        def wait(self, timeout=None):
            if not self.dead:
                raise rgm.subprocess.TimeoutExpired("假服务", timeout)
            return 0

    def batch(group_rc, srv_stuck=False, hub_ok=True):
        srv = Srv(srv_stuck)
        fake_r = _types.ModuleType("flowocr.extract.run_ocr2")
        fake_r.start_ort_services = lambda *a: ([srv], {})
        hub = NS(proc=Done(0), started=NS(is_set=lambda: True), close=lambda: hub_ok)
        retries = []
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(sys.modules, {"flowocr.extract.run_ocr2": fake_r}), \
                mock.patch.object(sys.modules["flowocr.extract"], "run_ocr2", fake_r, create=True), \
                mock.patch.object(rgm, "group_names", return_value=["a", "b"]), \
                mock.patch.object(rgm, "hub_wanted", return_value=""), \
                mock.patch.object(rgm, "Hub", return_value=hub), \
                mock.patch.object(rgm.subprocess, "Popen", side_effect=lambda *a, **k: Done(group_rc)), \
                mock.patch.object(rgm.subprocess, "call", side_effect=lambda c, **k: retries.append(c) or 0), \
                mock.patch("builtins.print"):
            rc = rgm.main(["v.mp4", "--regions", "r.json", "--outdir", td])
        return rc, len(retries)

    got = {"都退了": batch(sv.EXIT_HW_MISMATCH), "共享服务没退": batch(sv.EXIT_HW_MISMATCH, srv_stuck=True),
           "广播进程没退": batch(sv.EXIT_HW_MISMATCH, hub_ok=False), "组报 76": batch(sv.EXIT_HW_UNCLEAN)}
    check("run_groups：硬解交付不对时，组 / 广播进程 / 共享服务都确认退了才整批软解重跑；任何一样没退就不重跑、退 EXIT_HW_UNCLEAN",
          got == {"都退了": (0, 2), "共享服务没退": (sv.EXIT_HW_UNCLEAN, 0), "广播进程没退": (sv.EXIT_HW_UNCLEAN, 0),
                  "组报 76": (sv.EXIT_HW_UNCLEAN, 0)}, got)

    # ⑥ 参数：rec_inflight auto、det_lookahead 只收 0 / 1、ort_server_extra 进 config 且只赦免空
    from flowocr.extract import ocr_args
    ri = lift("rec_inflight", {})
    a = ocr_args.parse_args(["x.mp4", "--out", "o.jsonl"])
    a3 = ocr_args.parse_args(["x.mp4", "--out", "o.jsonl", "--rec-inflight", "3"])
    check("rec_inflight：auto = GPU 2 / CPU 1；显式给数 CPU 上也照给的；同步路径 1",
          a.rec_inflight == "auto" and ri(a, "gpu") == 2 and ri(a, "cpu") == 1 and ri(a3, "cpu") == 3
          and ri(NS(rec_async=False, rec_inflight=2), "gpu") == 1, a.rec_inflight)
    try:
        ocr_args.parse_args(["x.mp4", "--out", "o.jsonl", "--det-lookahead", "2"])
        la_ok = False
    except SystemExit:
        la_ok = True
    c0 = ocr_args.config_of(["x.mp4", "--out", "o.jsonl"])
    c1 = ocr_args.config_of(["x.mp4", "--out", "o.jsonl", "--ort-server-extra=--cuda-graph-batch 4"])
    old = {k: v for k, v in c0.items() if k != "ort_server_extra"}
    check("--det-lookahead 只收 0 / 1；--ort-server-extra 进 config：没这个键的旧产物照复用，非空的不复用",
          la_ok and c1["ort_server_extra"] == "--cuda-graph-batch 4" and not ocr_args.config_differs(old, c0)
          and "ort_server_extra" in ocr_args.config_differs(c0, c1), ocr_args.config_differs(c0, c1))
    sa = lift("ort_server_args", {"os": __import__("os")})
    ax = ocr_args.parse_args(["x.mp4", "--out", "o.jsonl", "--ort-server-extra=--threads 4"])
    g, c = sa(ax, "gpu"), sa(ax, "cpu")
    a_on = ocr_args.parse_args(["x.mp4", "--out", "o.jsonl", "--ort-spin", "on"])
    check("ort_server_args：GPU 预热、按形状分 session；CPU 不预热、--per-shape 0；两边默认都 --spin 0（--ort-spin on 才自旋）；"
          "--ort-server-extra 原样追加在最后",
          "--warm" in g and "--per-shape" not in g and "--warm" not in c and c[c.index("--per-shape") + 1] == "0"
          and g[g.index("--spin") + 1] == c[c.index("--spin") + 1] == "0" and "--spin" not in sa(a_on, "gpu")
          and g[-2:] == c[-2:] == ["--threads", "4"], (g, c))


def t_analysis_speed() -> None:
    """阶段二提速（analysis-speed 计划）的**逐位等价**：旧实现原样抄在这里当 oracle，固定种子随机对拍。"""
    print("analysis_speed（旧实现对拍）")
    import random
    from difflib import SequenceMatcher as SM
    from types import SimpleNamespace as NS

    from flowocr.analyze import build_tracks as BT
    from flowocr.analyze import uigate as UG

    rnd = random.Random(20260924)
    alpha = "あいうえおかきくけこアイウエ…！、。abcAB12 "

    def rs(lo=0, hi=14):
        return "".join(rnd.choice(alpha) for _ in range(rnd.randint(lo, hi)))

    def old_text_sim(a, b, prefix_min=0):
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if (a.startswith(b) or b.startswith(a)) and min(len(a), len(b)) >= prefix_min:
            return 0.95
        return SM(None, a, b).ratio()

    bad = 0
    for _ in range(4000):
        a = rs()
        b = rnd.choice([rs(), a[:rnd.randint(0, len(a))], a + rs(0, 3), a[:-1] + rs(1, 1) if a else rs()])
        thr = rnd.choice([0.0, 0.5, 0.8, 0.9, 0.95, 1.0])
        pm = rnd.choice([0, 2, 5])
        bad += BT.text_sim_at_least(a, b, thr, pm) != (old_text_sim(a, b, pm) >= thr)
        bad += BT.text_sim(a, b, pm) != old_text_sim(a, b, pm)
    check("text_sim / text_sim_at_least 对旧定义逐位相同（4000 组随机串、六档门、三档 prefix_min）", bad == 0, bad)

    def old_best_overlap(head, tail, min_k=4, thr=0.85):
        best_k, best_score = 0, 0.0
        for k in range(min_k, min(len(head), len(tail)) + 1):
            ratio = SM(None, head[-k:], tail[:k]).ratio()
            if ratio < thr:
                continue
            score = ratio * k
            if score > best_score:
                best_k, best_score = k, score
        return best_k

    bad = 0
    for _ in range(3000):
        mid = rs(0, 12)
        head, tail = rs(0, 12) + mid, mid + rs(0, 12)
        if rnd.random() < 0.3:
            tail = rs(0, 20)
        bad += BT.best_overlap(head, tail) != old_best_overlap(head, tail)
    check("best_overlap 对旧实现逐位相同（3000 组，一半造了真重叠）", bad == 0, bad)

    def old_footprints(items, thr=0.6):
        fps = []
        for it in sorted(items, key=lambda x: x.t0):
            for f in fps:
                if UG.iou(f.box, it.box) >= thr:
                    f.items.append(it)
                    n = len(f.items)
                    f.box = [int((f.box[k] * (n - 1) + it.box[k]) / n) for k in range(4)]
                    break
            else:
                fps.append(UG.Footprint(box=list(it.box), items=[it]))
        return fps

    bad = 0
    for _ in range(40):
        items = []
        for i in range(rnd.randint(5, 250)):
            x, y = rnd.uniform(0, 1800), rnd.uniform(0, 1000)
            if rnd.random() < 0.5 and items:
                bx = items[rnd.randrange(len(items))].box
                x, y = bx[0] + rnd.uniform(-8, 8), bx[1] + rnd.uniform(-8, 8)
            w, h = rnd.uniform(20, 400), rnd.uniform(15, 60)
            items.append(UG.Item((x, y, x + w, y + h), rnd.randint(0, 10 ** 7), 0, "t", key=i))
        new = UG.footprints(items)
        old = old_footprints(items)
        bad += [(f.box, [it.key for it in f.items]) for f in new] != [(f.box, [it.key for it in f.items]) for f in old]
    check("uigate.footprints（网格索引）对逐个扫的旧实现：框位和成员顺序全同（40 组随机框集，一半贴着已有框）", bad == 0, bad)
    check("footprints：thr = 0 当场报错（网格索引靠 IoU ≥ thr > 0 ⇒ 有交集）",
          raises(lambda: UG.footprints([UG.Item((0, 0, 1, 1), 0, 0, "t")], thr=0.0), ValueError))

    def old_typewriter_growth(prev_box, prev_text, cur_box, cur_text, min_chars=BT.TYPEWRITER_MIN_CHARS):
        a, b = "".join(prev_text.split()), "".join(cur_text.split())
        if len(a) < max(1, min_chars) or len(b) <= len(a):
            return False
        if len(a) < 2:
            if not b.startswith(a):
                return False
        elif SM(None, a, b[:len(a)]).ratio() < 0.8:
            return False
        ah, bh = prev_box[3] - prev_box[1], cur_box[3] - cur_box[1]
        if max(ah, bh) / max(1.0, min(ah, bh)) > 1.4:
            return False
        lo, hi = max(prev_box[1], cur_box[1]), min(prev_box[3], cur_box[3])
        if hi - lo < 0.6 * min(ah, bh):
            return False
        tol = 0.6 * min(ah, bh)
        grew_right = abs(cur_box[0] - prev_box[0]) <= tol and cur_box[2] > prev_box[2]
        grew_both = (cur_box[0] <= prev_box[0] + tol and cur_box[2] >= prev_box[2] - tol
                     and abs((cur_box[0] + cur_box[2]) - (prev_box[0] + prev_box[2])) <= 2 * tol)
        return grew_right or grew_both

    bad = 0
    for _ in range(4000):
        a = rs(0, 8)
        b = rnd.choice([a + rs(0, 6), rs(0, 14), a[:1] + rs(0, 6)])
        pb = [rnd.uniform(0, 100), rnd.uniform(0, 100), 0, 0]
        pb[2], pb[3] = pb[0] + rnd.uniform(10, 300), pb[1] + rnd.uniform(10, 60)
        cb = [pb[0] + rnd.uniform(-20, 20), pb[1] + rnd.uniform(-20, 20), 0, 0]
        cb[2], cb[3] = cb[0] + rnd.uniform(10, 400), cb[1] + rnd.uniform(10, 70)
        mc = rnd.choice([1, 2])
        # 旧实现写死框高比 1.4；默认 2026-09-24 放到 1.7，逐位对拍在同一个 1.4 上比
        bad += BT.typewriter_growth(pb, a, cb, b, mc, h_ratio=1.4) != old_typewriter_growth(pb, a, cb, b, mc)
    check("typewriter_growth（几何先判、文本后算）对旧实现逐位相同（4000 组，含 1 字那一支）", bad == 0, bad)
    # yuka-f1 遮罩臂 `はいはーい…`：半截框 35 px、整句框被 det 撑到 53 px（1.51 倍）——1.4 断链，默认 1.7 接上
    _half, _full = [428, 872, 758, 907], [424, 860, 1158, 913]
    check("打字机生长的框高比门默认 1.7：1.51 倍的框高变化照样算长出来的（1.4 时断链，ocr-regions §5.1）",
          BT.GROW_H_RATIO == 1.7
          and BT.typewriter_growth(_half, "はいはーい！私は橘", _full, "はいはーい！私は橘シェリーっていいますっ。")
          and not BT.typewriter_growth(_half, "はいはーい！私は橘", _full, "はいはーい！私は橘シェリーっていいますっ。",
                                       h_ratio=1.4))

    def old_dominant(rs_):
        groups = []
        sm = SM(None)
        for r in rs_:
            dur = (r.t_end - r.t_start) / 1e6
            a = r.text
            la = len(a)
            if a:
                sm.set_seq1(a)
            for k, (rep, tot) in enumerate(groups):
                if not a or not rep:
                    continue
                if not (a == rep or a.startswith(rep) or rep.startswith(a)):
                    lb = len(rep)
                    if 3 * min(la, lb) < 2 * max(la, lb):
                        continue
                    sm.set_seq2(rep)
                    if sm.quick_ratio() < 0.8 or sm.ratio() < 0.8:
                        continue
                groups[k] = (rep if len(rep) >= la else a, tot + dur)
                break
            else:
                groups.append((a, dur))
        total = sum(t for _, t in groups)
        return max(t for _, t in groups) / total if total else 0.0

    bad = 0
    for _ in range(300):
        base = [rs(1, 10) for _ in range(rnd.randint(1, 6))]
        runs = []
        for _ in range(rnd.randint(1, 60)):
            t = rnd.choice(base)
            t = rnd.choice([t, t + rs(0, 2), t[:-1], rs(0, 10), ""])
            s0 = rnd.randint(0, 10 ** 7)
            runs.append(NS(text=t, t_start=s0, t_end=s0 + rnd.randint(1, 10 ** 6)))
        bad += BT.dominant_text_share(runs) != old_dominant(runs)
    check("dominant_text_share（每组一个 SequenceMatcher）对旧实现逐位相同（300 组随机 run 表，含空文本 / 抖动变体）", bad == 0, bad)


def t_edge_proc() -> None:
    """`--edge-proc`（回抠换进程）靠两个前提：①`EdgeRefiner` 对行**只做** `row.setdefault("edge", {})[键] = 证据`
    （那边的行是 `RowRef` 替身，只实现了这一件事）；②替身写证据 = 发 `("ev", 行号, 键, 证据)`。"""
    print("edge_proc（回抠换进程）")
    import re

    src = (ROOT / "src/flowocr/extract/edge_refine.py").read_text(encoding="utf-8")
    uses = [m.group(0) for m in re.finditer(r"\brow\b(?:\.\w+|\[)", src)]
    check("EdgeRefiner 对行只用 setdefault（RowRef 只替身了这一件事；加别的用法要同步改 edge_proc）",
          uses and set(uses) == {"row.setdefault"}, sorted(set(uses)))
    from flowocr.extract.edge_proc import RowRef
    sent = []
    r = RowRef(7, sent.append)
    r.setdefault("edge", {})["on"] = {"n": 1}
    check("RowRef：写证据就是发 (ev, 行号, 键, 证据)", sent == [("ev", 7, "on", {"n": 1})], sent)
    check("RowRef：碰 edge 以外的键当场报错（不静默丢）", raises(lambda: r.setdefault("text", ""), KeyError))
    from flowocr.extract import ocr_args as oa
    check("edge_proc 在 SCHEDULING（写进 config、分得开臂、不触发重建）",
          "edge_proc" in oa.SCHEDULING
          and not oa.same_arm(["v.mp4", "--out", "a"], ["v.mp4", "--out", "b", "--no-edge-proc"]))
    d = oa.parse_args(["v.mp4", "--out", "a"])
    check("拆进程：回抠那刀默认开", d.edge_proc)
    # 不支持的组合：关掉那一刀、说原因，不报错；config 记请求的值（resolve_split 不并进 resolve）
    import contextlib
    import io as _io
    with contextlib.redirect_stdout(_io.StringIO()):
        a2 = oa.parse_args(["v.mp4", "--out", "a", "--refine-aux", "nvdec"])
        w2 = oa.resolve_split(a2)
    check("--refine-aux nvdec：edge 关、说原因", not a2.edge_proc and len(w2) == 1, w2)
    check("关掉之后 config 仍记请求的值（同 --workers 的规矩；实际的在 _meta.proc_split）",
          oa.config_of(["v.mp4", "--out", "a", "--refine-aux", "nvdec"])["edge_proc"] is True)
    # 2026-09-24 owner 定删掉的三个旋钮：解析器不认了；旧产物里记着的值不触发重建
    # （front_proc / det_proc 取任何值都不改产物、键留在 SCHEDULING；decode_cpu_lanes 只有 0 是空操作、进 REMOVED_NOOP）
    with contextlib.redirect_stderr(_io.StringIO()):
        gone = all(raises(lambda f=f: oa.parse_args(["v.mp4", "--out", "a", f]), SystemExit)
                   for f in ("--front-proc", "--det-proc"))
        gone = gone and raises(lambda: oa.parse_args(["v.mp4", "--out", "a", "--decode-cpu-lanes", "1"]), SystemExit)
    _now = oa.config_of(["v.mp4", "--out", "a"])
    check("--front-proc / --det-proc / --decode-cpu-lanes 已删：解析器不认；旧产物记着 front_proc=True / det_proc=True / decode_cpu_lanes=0 照样复用，"
          "decode_cpu_lanes=1 重建",
          gone and {"front_proc", "det_proc"} <= oa.SCHEDULING and oa.REMOVED_NOOP.get("decode_cpu_lanes") == 0
          and oa.config_differs({**_now, "front_proc": True, "det_proc": True, "decode_cpu_lanes": 0}, _now) == []
          and oa.config_differs({**_now, "decode_cpu_lanes": 1}, _now) == ["decode_cpu_lanes"])
    _ro = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    check("run_ocr2 不再引用 front_proc / det_proc / decode_cpu_lanes",
          not any(k in _ro for k in ("front_proc", "det_proc", "decode_cpu_lanes", "FrontProxy", "RemoteDet")))

    # 解码分片（decode-buffer §8.24）：块的规划、块的命令、拆进程规则
    from flowocr.extract import decode_shards as dsh
    from flowocr.extract import framesource as fsx
    bl = dsh.plan_blocks("v", 0, 3000, 60.0, 10.0, kfs=[0.0, 4.0, 11.0, 12.0, 25.0, 49.9])
    check("plan_blocks：第一块走普通路径（None）、之后从 ≥ 起点 + 块长的关键帧起（+0.5 ms）、首尾相接、最后到窗口尾；"
          "切完剩不到半块就不切（49.9 s 那个关键帧后面只剩 6 帧，并进上一块）",
          bl == [(0, 660, None), (660, 1500, 11.0005), (1500, 3000, 25.0005)], bl)
    bl2 = dsh.plan_blocks("v", 600, 1200, 60.0, 10.0, kfs=[5.0, 30.0])
    check("plan_blocks：窗口外 / 离起点不够一块的关键帧不切", bl2 == [(600, 1200, None)], bl2)
    bl3 = list(dsh.iter_blocks(iter([0.0, 150.0]), 0, 18000, 60.0, 20.0))
    check("iter_blocks：关键帧来一个判一个（边探边切）；长 GOP 切出的就是长块（内存靠字节预算，不靠块长）",
          bl3 == [(0, 9000, None), (9000, 18000, 150.0005)], bl3)
    _shard_behaviour_checks(dsh, fsx)
    cmd = fsx.build_ffmpeg_cmd("v.mp4", grid=_every(30), start_sec=0.0, n_out=10, select_by="pts", src_fps=60.0,
                               block=(660, 1500, 11.0005),
                               aux={"grid": [1, 1, 1, 30], "scale": 0.35, "n_frames": 840, "url": "tcp://x:1", "w": 672, "h": 378})
    js = " ".join(cmd)
    check("build_ffmpeg_cmd 的块：从关键帧 -noaccurate_seek、输入端 -t 到块尾、两路都按帧号区间取",
          "-noaccurate_seek" in cmd and cmd[cmd.index("-ss") + 1] == "11.000500" and "-t" in cmd
          and js.count(r"between(round(t*60.000000000)\,660\,1499)") == 2, js[:300])
    cmd0 = fsx.build_ffmpeg_cmd("v.mp4", grid=_every(30), start_sec=11.0, n_out=10, select_by="pts", src_fps=60.0,
                                block=(660, 1500, None),
                                aux={"grid": [1, 1, 1, 30], "scale": 0.35, "n_frames": 840, "url": "tcp://x:1", "w": 672, "h": 378})
    js0 = " ".join(cmd0)
    check("build_ffmpeg_cmd 的**第一块**（kf=None）：照普通路径精确 seek，但同样带输入端 -t 和帧号区间（2026-09-24 自检：缺口落在第一块时辅助流越界、帧号倒退）",
          "-noaccurate_seek" not in cmd0 and cmd0[cmd0.index("-ss") + 1] == "10.991667" and "-t" in cmd0
          and js0.count(r"between(round(t*60.000000000)\,660\,1499)") == 2, js0[:300])
    cmdp = fsx.build_ffmpeg_cmd("v.mp4", grid=_every(30), start_sec=0.0, n_out=10, select_by="pts", src_fps=60.0,
                                block=(0, 1200, None), preroll_ticks=512)
    check("build_ffmpeg_cmd 的第一块在文件头：前摇平移照做，-t 多留 10 s 余量",
          "-ignore_editlist" in cmdp and float(cmdp[cmdp.index("-t") + 1]) > 1199.5 / 60 + 9)
    check("build_ffmpeg_cmd 的块不配 index 选帧 / 前摇平移",
          raises(lambda: fsx.build_ffmpeg_cmd("v.mp4", grid=_every(30), start_sec=0.0, n_out=1, select_by="index",
                                              src_fps=60.0, block=(0, 60, 0.0)), ValueError))
    check("ShardedSource：路只认 hw / sw；有 hw 路要给 hwaccel；只配按 pts 选帧",
          raises(lambda: dsh.ShardedSource("v", lanes=["gpu"], blocks=bl, start_idx=0, end_idx=3000, grid=_every(30), src_fps=60, width=4, height=4), ValueError)
          and raises(lambda: dsh.ShardedSource("v", lanes=["hw", "hw"], blocks=bl, start_idx=0, end_idx=3000, grid=_every(30), src_fps=60, width=4, height=4), ValueError)
          and raises(lambda: dsh.ShardedSource("v", lanes=["sw", "sw"], blocks=bl, start_idx=0, end_idx=3000, grid=_every(30), src_fps=60, width=4, height=4,
                                               select_by="index"), ValueError))
    with contextlib.redirect_stdout(_io.StringIO()):
        c1 = oa.parse_args(["v.mp4", "--out", "a", "--decode-shards", "3"])
        u1 = oa.resolve_split(c1)
        c2 = oa.parse_args(["v.mp4", "--out", "a", "--decode-shards", "3", "--workers", "2"])
        u2 = oa.resolve_split(c2)
        c3 = oa.parse_args(["v.mp4", "--out", "a", "--decode-shards", "2", "--decoder", "cv2"])
        u3 = oa.resolve_split(c3)
    check("--decode-shards：默认组合按请求生效；撞上 --workers > 1 / cv2 解码器**安静让路**（不进 why、原因记 decode_shards_off）",
          c1.decode_shards == 3 and u1 == [] and c2.decode_shards == 0 and u2 == [] and c3.decode_shards == 0
          and "--workers" in c2.decode_shards_off and "cv2" in c3.decode_shards_off, (u1, u2, u3))
    check("--decode-shards 默认 2（2026-09-24：先 3，字节预算之后复测 2 路同读数、少一个上下文，defaults.md §1.18）",
          oa.parse_args(["v.mp4", "--out", "a"]).decode_shards == 2)
    _hw = [oa.config_of(["v.mp4", "--out", "a", "--decode-shards", n, "--decode-block", b]) for n, b in (("2", "10"), ("3", "20"))]
    check("decode_shards / decode_block 在 SCHEDULING：路数 / 块长不触发重建（全硬解逐字节相同）",
          {"decode_shards", "decode_block"} <= oa.SCHEDULING and oa.config_differs(*_hw) == [], oa.config_differs(*_hw))
    from flowocr.extract import ocr_parallel as opx
    with contextlib.redirect_stdout(_io.StringIO()):
        _child = oa.parse_args(["v.mp4", "--out", "a", "--workers", "3", *opx.CHILD_ARGS])
        oa.resolve_split(_child)
    check("--workers 的子进程显式关掉解码分片（子进程只看得见 --workers 1、会按默认再开 3 路；2026-09-24 审计）",
          _child.workers == 1 and _child.decode_shards == 0
          and "*ocr_parallel.CHILD_ARGS]" in (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8"))
    _pe = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    check("run_ocr2：分片因硬解开不了而关掉时，`_meta.proc_split.decode` 改记 0 + 原因（原来仍记请求的 3）",
          'meta["proc_split"]["decode"] = 0' in _pe)

    # 模块顶层不 import 推理库（最早为 `--front-proc` 立的、查的是 paddle / paddleocr，那个旋钮已删；2026-10 运行时去掉 Paddle 之后
    # 改查 onnxruntime：推理在 ORT 服务进程里，管线进程只有 `gpu_blocker` 用到它、各自 import，不白付 import 和一份 CUDA 上下文）
    # （decode-buffer §8.16）。静态查：模块顶层（含 if / try 块）不许出现这些 import
    import ast

    def top_imports(nodes):                              # 模块顶层语句，钻进 if / try / with，不进函数和类
        for n in nodes:
            if isinstance(n, ast.Import):
                yield from (a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                yield (n.module or "").split(".")[0]
            elif not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    yield from top_imports(getattr(n, field, []) or [])

    mods = set(top_imports(ast.parse((ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")).body))
    _heavy = {m for m in mods if m == "onnxruntime" or m.startswith("paddle")}
    check("run_ocr2 顶层不 import onnxruntime / paddle*（要的地方各自 import）", not _heavy, sorted(_heavy))

    # 只解码进程的出错那条路（真起一个子进程，不碰 GPU）：有 hw 路却没给 hwaccel，子进程建取帧层就抛——回溯要在 start 里抛出来，close 要很快返回、子进程已退
    import time as _time

    from flowocr.extract import decode_proc as dpx
    dp = dpx.DecodeProxy()
    err = None
    try:
        dp.start({"video": "no-such-video.mp4", "lanes": ["hw", "hw"], "block_sec": 20.0, "H": 4, "W": 4, "slots": 2,
                  "aux": None, "source_kw": {"grid": _every(30), "src_fps": 60.0, "width": 4, "height": 4, "pix": "nv12",
                                             "select_by": "pts", "hwaccel": None, "start_idx": 0, "end_idx": 600}})
        for _ in dp:
            pass
    except RuntimeError as exc:
        err = str(exc)
    t0 = _time.perf_counter()
    dp.close()
    dt = _time.perf_counter() - t0
    check("decode_proc 出错：子进程的回溯在管线进程这边抛出来", err is not None and "Traceback" in err, (err or "")[-300:])
    check("decode_proc 出错：close 很快返回、子进程已退", dt < 10 and dp.child.proc.poll() is not None,
          (round(dt, 1), dp.child.proc.poll()))
    from flowocr.extract import childproc
    _ep = (ROOT / "src/flowocr/extract/edge_proc.py").read_text(encoding="utf-8")
    check("拆进程的胶水只有一份：回抠进程和只解码进程都走 childproc（起子进程 / 口令 / 收尾）",
          "childproc.Child(" in _ep and "childproc.connect()" in _ep and "subprocess.Popen" not in _ep
          and childproc.AUTH_ENV == "FLOWOCR_CHILD_KEY")


def t_audit_0923() -> None:
    """2026-09-23 Codex 审计 + 接手自查的四个状态 / 口径缺陷（最小复现改成守卫，`tmp/audit-nightly-repro.py` 同形状）。"""
    print("审计 09-23：在途读数、切点切链、带孔可观察、截短 run 的占用、子框遮罩重排")
    import io
    import types

    import numpy as np

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.analyze import uigate
    from flowocr.extract import ocr_args
    from flowocr.extract import regions as RG
    from flowocr.extract.reuse_v2 import ReuseV2

    box = [[10, 10, 70, 28]]
    poly = [[[10, 10], [70, 10], [70, 28], [10, 28]]]
    gray = np.zeros((100, 100), np.uint8)

    def fresh():
        args = ocr_args.parse_args(["dummy.mp4", "--out", "x.jsonl", "--no-refine-fused"])
        st = {"corr": 1.0}
        return ReuseV2(args, lambda a, b: st["corr"], lambda a, b: (0, 0), lambda c: ("BACKFILL", 0.99)), st

    def stage1(v, st, seq, corr):
        st["corr"] = corr
        dec, todo, assign = v.decide(seq, gray.copy(), box)
        s = v.commit_stage1(seq, seq * 30, seq * 500_000, box, poly, list(dec) + [(i, "", 0.0, 0) for i in todo],
                            set(todo), assign, lambda i: np.zeros((18, 60, 3), np.uint8), {}, False)
        return s, todo

    # 1. 固定滞后 W=4（第 k 帧在第 k+3 帧 stage1 之后结算）：同一条链连续几帧真读，结算最老的一个**不许**清掉在途标记
    v, st = fresh()
    win, reads, pend_before = [], [], []
    for seq in range(6):
        pend_before.append((v.live[0].text_pending, v.live[0].inflight) if v.live else None)
        s, todo = stage1(v, st, seq, 0.94 if seq == 5 else 0.5)
        reads.append(bool(todo))
        win.append((s, {i: ("AAA" if seq < 2 else "BBB", 0.99) for i in todo}))
        if len(win) > 3:
            old, tx = win.pop(0)
            v.commit_stage2(old, tx, io.StringIO())
    check("W=4：第 5 帧判决时第 2~4 帧的真读还在途 -> 链仍是待定（在途 3 个），相关系数 0.94 走严门、真读",
          pend_before[5] == (True, 3) and reads[5], (pend_before, reads))

    # 1b. 链断时交给在线回抠的 `ended` 要带**结算后**的文本（2026-09-23 接手自查）：原来在 stage1 抄 `c.text`，
    #     异步窗口下链最后几次真读还在途，抄到的是旧句 / 空串——回抠的"长字续接"（is_growth）于是认不出，
    #     段被拆开、全字锚到后半段上偏早（gi-s2 全字代价 22 -> 75，relinked 184 -> 51）
    class Sink:
        def __init__(self):
            self.ended = []

        def sample(self, seq, idx, t_us, items, ended, stale):
            self.ended += [(seq, e[3]) for e in ended]

    v, st = fresh()
    v.edge = Sink()
    win = []
    for seq in range(6):
        bx = box if seq < 3 else []
        st["corr"] = 0.5                                   # 每帧都真读
        dec, todo, assign = v.decide(seq, gray.copy(), bx)
        s = v.commit_stage1(seq, seq * 30, seq * 500_000, bx, poly if bx else [],
                            list(dec) + [(i, "", 0.0, 0) for i in todo], set(todo), assign,
                            lambda i: np.zeros((18, 60, 3), np.uint8), {}, False)
        win.append((s, {i: (["A", "AB", "ABC"][seq], 0.99) for i in todo}))
        if len(win) > 3:
            old, tx = win.pop(0)
            v.commit_stage2(old, tx, io.StringIO())
    for old, tx in win:
        v.commit_stage2(old, tx, io.StringIO())
    check("异步窗口下链断时交给回抠的文本是**最后一次真读结算后**的（不是 stage1 那一刻的旧文本）",
          v.edge.ended == [(3, "ABC")], v.edge.ended)

    # 2. 自选范围的时间切点：切到的链不许续（两次采样之间关了又开，两帧都看得见）
    v, st = fresh()
    s, todo = stage1(v, st, 0, 1.0)
    v.commit_stage2(s, {i: ("BEFORE", 0.99) for i in todo}, io.StringIO())
    uid0 = v.live[0].uid
    v.sever(np.ones((100, 100), bool))
    s, todo = stage1(v, st, 1, 1.0)
    check("切点切到的链：相关系数 1.0 也不续，按新链真读", todo == [0] and v.live[0].uid != uid0, (todo, v.live[0].uid, uid0))
    v, st = fresh()
    s, todo = stage1(v, st, 0, 1.0)
    v.commit_stage2(s, {i: ("X", 0.99) for i in todo}, io.StringIO())
    far = np.zeros((100, 100), bool)
    far[80:, 80:] = True
    v.sever(far)
    s, todo = stage1(v, st, 1, 1.0)
    check("切点没切到的链照常沿用", todo == [] and not v.live[0].severed, todo)

    # 3. 可观察时长看框里有没有有效像素，不是看中心（带孔的连通框、有孔不拆）
    g = RG.RegionGroup("hole", [RG.Rect(40, 40, 60, 60, neg=True)], 100, 100)
    b = [10, 10, 90, 90]
    check("带孔的框：中心在孔里也算全程看得见（原来是 0，占比被放大到上百万）",
          [x for x, _ in RG.split_box(b, g.mask_at(0))] == [b] and g.observable_us(b, 0, 10_000_000) == 10_000_000,
          g.observable_us(b, 0, 10_000_000))
    check("框全在孔里：看不见", g.observable_us([45, 45, 55, 55], 0, 10_000_000) == 0)

    # 4. 被切点截到不足一格的 run：占用原样算，不补回一个采样间隔；点观测照旧补一格
    it_run = uigate.Item((0, 0, 10, 10), 0, 100_000, "x")
    it_pt = uigate.Item((0, 0, 10, 10), 0, 0, "x")
    check("截短的 run 占 0.1 s（原来补成 0.5 s，占比能超过 1）、点观测补满一格",
          uigate.occupancy([it_run], 500_000) == 100_000 and uigate.occupancy([it_pt], 500_000) == 500_000,
          (uigate.occupancy([it_run], 500_000), uigate.occupancy([it_pt], 500_000)))

    # 5. split_polys 之后按下标过滤：多边形 / 框 / 子框遮罩要一起重排（原来 own_of 留着旧下标 -> IndexError）
    m = np.zeros((100, 100), bool)
    m[0:5, 0:5] = True
    m[10:40, 10:15] = True
    m[10:40, 35:40] = True
    m[35:40, 10:40] = True
    m[15:25, 20:30] = True
    polys = [[[0, 0], [5, 0], [5, 5], [0, 5]], [[10, 10], [40, 10], [40, 40], [10, 40]]]
    p, bxs, own = RG.split_polys(polys, m)
    keep = [i for i in range(len(bxs)) if i != 0]
    p2, b2, own2 = RG.take(p, bxs, own, keep)
    frame = np.full((100, 100, 3), 7, np.uint8)
    ok = all(RG.masked_crop(frame, b2[j], own2.get(j)).shape[:2] == (b2[j][3] - b2[j][1], b2[j][2] - b2[j][0])
             for j in range(len(b2)))
    check("take：删掉前面的框后子框遮罩跟着重排，每个遮罩和自己的框同形状",
          ok and all(own2[j].shape == (b2[j][3] - b2[j][1], b2[j][2] - b2[j][0]) for j in own2), (b2, list(own2)))


def t_regions() -> None:
    """自选 OCR 范围（`flowocr.extract.regions`，ocr-regions 计划 §1–§2、§7 第 1–2 条）：范围语义与 det 框拆分。"""
    print("ocr-regions：范围与拆框")
    import json
    import tempfile

    import numpy as np

    from flowocr.extract import regions as RG

    W, H = 100, 100
    R = RG.Rect
    full = RG.RegionGroup("g", [], W, H)
    check("没配矩形的组：整段全屏（mask None、unrestricted）", full.unrestricted and full.mask_at(3.0) is None)
    neg_only = RG.RegionGroup("n", [R(40, 0, 60, 100, neg=True, t0=10, t1=20)], W, H)
    check("只有负矩形：负矩形生效时扣掉、过期后恢复全屏",
          neg_only.mask_at(5) is None and int(neg_only.mask_at(15).sum()) == 8000 and neg_only.mask_at(25) is None,
          (neg_only.mask_at(5), neg_only.mask_at(15) is None))
    pos_late = RG.RegionGroup("p", [R(0, 0, 50, 50, t0=5)], W, H)
    check("配了正矩形但此刻一个都不生效：范围为空，不退回全屏", int(pos_late.mask_at(1).sum()) == 0)
    check("切点只列遮罩真变了的时刻", neg_only.boundaries() == [10.0, 20.0] and pos_late.boundaries() == [5.0],
          (neg_only.boundaries(), pos_late.boundaries()))
    same = RG.RegionGroup("s", [R(0, 0, 50, 100), R(50, 0, 100, 100), R(0, 0, 10, 10, t0=3)], W, H)
    check("等价范围（拆成两块的全屏、再叠一块盖在里面的）不产生切点、也不算受限",
          same.boundaries() == [] and same.mask_at(4) is None, (same.boundaries(),))

    m = neg_only.mask_at(15)
    check("负矩形纵贯一个框：拆成左右两个子框、各自裁到有效部分",
          [b for b, _ in RG.split_box([10, 10, 90, 20], m)] == [[10, 10, 40, 20], [60, 10, 90, 20]],
          RG.split_box([10, 10, 90, 20], m))
    check("框整个被遮：丢掉", RG.split_box([45, 10, 55, 20], m) == [])
    check("框略出画面（负坐标）且画面内全在范围里：原样返回，不绕回另一头切",
          RG.split_box([-3, 10, 20, 20], m) == [([-3, 10, 20, 20], None)], RG.split_box([-3, 10, 20, 20], m))
    check("框一部分被遮、剩下仍连通：一个框、裁到有效部分",
          [b for b, _ in RG.split_box([30, 10, 50, 20], m)] == [[30, 10, 40, 20]])
    hole = RG.RegionGroup("h", [R(50, 50, 55, 55, neg=True)], W, H).mask_at(0)
    check("框里只挖一个孔、周围仍连通：不拆（孔里是灰的）",
          [b for b, _ in RG.split_box([40, 40, 70, 70], hole)] == [[40, 40, 70, 70]])
    # 两块只在一个角上相碰：按 4 邻接算两块
    corner = np.zeros((H, W), bool)
    corner[10:20, 10:20] = True
    corner[20:30, 20:30] = True
    check("只在角上相碰的两块按 4 邻接算两块", len(RG.split_box([10, 10, 30, 30], corner)) == 2)
    # 一块 U 形包住另一块：内块的外接矩形里没有外人，U 形那块的外接矩形里有内块 -> 要 own 遮罩
    u = np.zeros((H, W), bool)
    u[10:40, 10:15] = u[10:40, 35:40] = u[35:40, 10:40] = True       # U
    u[15:25, 20:30] = True                                             # 被 U 包住的内块
    subs = RG.split_box([10, 10, 40, 40], u)
    own = [o for b, o in subs if b == [10, 10, 40, 40]]
    check("外接矩形里有另一块的有效像素时给 own 遮罩（rec / 复用裁剪要把它涂灰）",
          len(subs) == 2 and own and own[0] is not None and not own[0][10, 15], [(b, o is None) for b, o in subs])
    frame = np.full((H, W, 3), 7, np.uint8)
    c = RG.masked_crop(frame, [10, 10, 40, 40], own[0])
    check("子框裁剪：非本块像素涂 FILL、源帧不被写坏",
          int(c[10, 15, 0]) == RG.FILL and int(c[0, 0, 0]) == 7 and int(frame[20, 25, 0]) == 7)
    f2 = np.zeros((H, W, 3), np.uint8)
    RG.apply_mask(f2, m)
    check("范围外涂 FILL（rec padding 的 127.5 取整），范围内不动",
          int(f2[50, 50, 0]) == RG.FILL and int(f2[50, 10, 0]) == 0)

    spec = {"groups": [{"name": "subs", "rects": [{"box": [0, 70, 100, 100]}, {"box": [40, 70, 60, 100], "neg": True, "t": [10, 20]}]},
                       {"name": "ui", "rects": [{"box": [0, 0, 30, 20]}]}]}
    with tempfile.TemporaryDirectory() as td:
        p = f"{td}/r.json"
        open(p, "w", encoding="utf-8").write(json.dumps(spec))
        sp = RG.group_spec(p, "subs")
        g2 = RG.from_spec(sp, 1920, 1080)
        check("按组名挑一个组、规格往返不变", sp["name"] == "subs" and len(g2.rects) == 2 and g2.boundaries() == [10.0, 20.0],
              sp)
        try:
            RG.group_spec(p)
            need_name = False
        except ValueError:
            need_name = True
        check("多个组却没给 --region-group：报错（每组一条独立 OCR 线）", need_name)
        open(p, "w", encoding="utf-8").write(json.dumps({"groups": [{"name": "a"}, {"name": "a"}]}))
        try:
            RG.group_spec(p, "a")
            dup = False
        except ValueError:
            dup = True
        check("组名重复直接报错（产物按组名分文件）", dup)
        for bad in ({"box": [10, 0, 5, 10]}, {"box": [0, 0, 10, 10], "t": [5, 5]}):
            open(p, "w", encoding="utf-8").write(json.dumps({"groups": [{"name": "a", "rects": [bad]}]}))
            try:
                RG.group_spec(p)
                ok = False
            except ValueError:
                ok = True
            check(f"坏矩形报错：{bad}", ok)


def t_regions_cuts() -> None:
    """自选范围的**时间关闭段**（ocr-regions 计划 §2、§7 第 3 条）：build_runs 不让事件跨过切到它的切点，
    被截的一端标 clipped；切点没切到的框照常连着；回抠不动被截的那一端。"""
    print("ocr-regions：时间切点")
    import numpy as np

    from flowocr.analyze import build_tracks as bt
    from flowocr.extract import regions as RG

    W, H = 1000, 500
    g = RG.RegionGroup("g", [RG.Rect(0, 0, 50, 100, neg=True, t0=1.25, t1=2.25)], W, H)
    cuts = RG.cuts_us(g)
    check("切点：负矩形的起止两个", [t for t, _ in cuts] == [1_250_000, 2_250_000], [t for t, _ in cuts])
    fu = 500_000
    obs = []
    for k in range(8):                       # 0 ~ 3.5 s，每 0.5 s 一票
        t = k * fu
        if not (1_250_000 <= t < 2_250_000):  # 关闭段里左半边看不见
            obs.append({"t_us": t, "box": [100, 100, 300, 130], "text": "左边的字", "conf": 0.99})
        obs.append({"t_us": t, "box": [700, 100, 900, 130], "text": "右边的字", "conf": 0.99})
    runs = bt.build_runs(obs, fu, 0.5, 0.6, gap_frames=4, cuts=cuts)
    left = sorted((r for r in runs if r.text == "左边的字"), key=lambda r: r.t_start)
    right = [r for r in runs if r.text == "右边的字"]
    check("关闭段两侧的同一段字拆成两个事件（gap_frames 4 本来能跨过 1 s 的空档）",
          len(left) == 2, [(r.t_start, r.t_end) for r in left])
    check("前一段终点截到切点、标 clip_end；后一段起点标 clip_start",
          left[0].t_end == 1_250_000 and left[0].clip_end == 1_250_000 and left[0].clip_start is None
          and left[1].clip_start == 2_250_000 and left[1].t_start == 2_500_000,
          [(r.t_start, r.t_end, r.clip_start, r.clip_end) for r in left])
    check("切点没切到的框（右半边）照常一个事件、不标 clipped",
          len(right) == 1 and right[0].clip_start is None and right[0].clip_end is None,
          [(r.t_start, r.t_end) for r in right])
    obs_left = g.observable_us([100, 100, 300, 130], 0, 3_000_000)
    obs_right = g.observable_us([700, 100, 900, 130], 0, 3_000_000)
    check("可观察时长：左边的框扣掉 1.25–2.25 s 的关闭段（UI 占比 / fill 的分母，方案 §4），右边的全程看得见",
          obs_left == 2_000_000 and obs_right == 3_000_000, (obs_left, obs_right))
    check("不受限的组：可观察时长就是整段",
          RG.RegionGroup("all", [], W, H).observable_us([0, 0, 10, 10], 0, 5_000_000) == 5_000_000)
    ev = bt.event_dict(left[0], 0, 0)
    check("事件 flags 里写 clipped_end", "clipped_end" in ev["flags"] and "clipped_start" not in ev["flags"], ev["flags"])
    no_cut = bt.build_runs(obs, fu, 0.5, 0.6, gap_frames=4)
    check("不给切点就是原来的行为（左边那段跨过空档连成一个）",
          len([r for r in no_cut if r.text == "左边的字"]) == 1)

    import json
    from flowocr.analyze import refine_boundaries as RB
    run = {"t_start": 2_500_000, "t_end": 3_000_000, "flags": ["clipped_start"]}
    job = {"run": run, "frame_us": fu, "ocr": None,
           "measured": (3_200_000, True, None, None, "")}
    RB.apply(job, None, None)
    check("回抠：被截的起点不动、没被截的终点照常挪", run["t_start"] == 2_500_000 and run["t_end"] == 3_200_000,
          (run["t_start"], run["t_end"]))

    # 涂遮罩按矩形切片赋值（2026-09-24：布尔索引一帧 48 ms，一组自选范围整体比全屏慢 59%）：和布尔索引逐位相同
    import numpy as _np
    from flowocr.extract import regions as RGm
    rng = _np.random.default_rng(7)
    same = True
    for _ in range(200):
        Hh, Ww = (int(v) for v in rng.integers(4, 40, 2))
        mm = _np.zeros((Hh, Ww), bool)
        for _ in range(int(rng.integers(1, 5))):
            y0, x0 = int(rng.integers(0, Hh)), int(rng.integers(0, Ww))
            mm[y0:int(rng.integers(y0, Hh + 1)), x0:int(rng.integers(x0, Ww + 1))] = bool(rng.random() < 0.7)
        fr = rng.integers(0, 255, (Hh, Ww, 3)).astype(_np.uint8)
        ref = fr.copy()
        ref[~mm] = RGm.FILL
        same &= _np.array_equal(RGm.apply_mask(fr.copy(), mm), ref)
    check("apply_mask：按矩形切片涂和布尔索引逐位相同（200 个随机遮罩）", same)
    # --region-crop（方案 §3.3 的对照臂）：静态外接矩形盖住每一刻的有效范围；框平移回原片坐标
    gc = RGm.from_spec({"name": "g", "rects": [{"box": [0, 70, 100, 88]}, {"box": [10, 10, 20, 20], "t": [5, 9]}]}, 100, 100)
    bb = gc.union_bbox()
    full = RGm.from_spec({"name": "g", "rects": [{"box": [0, 0, 50, 50], "neg": True, "t": [0, 3]}]}, 100, 100)

    class _D:
        def predict(self, im):
            return [_np.array([[0, 0], [2, 0], [2, 1], [0, 1]])], [0.9]

        def predict_batch(self, ims):
            return [self.predict(im) for im in ims]
    cd = RGm.CroppedDet(_D(), bb)
    p1 = cd.predict(_np.zeros((100, 100, 3), _np.uint8))[0][0]
    check("CroppedDet：外接矩形 = 各时段有效范围的并（时变的小矩形也盖住）、没得裁（某时刻全屏）返回 None；多边形平移回原片坐标",
          bb == (0, 10, 100, 88) and full.union_bbox() is None and p1.tolist() == [[0, 10], [2, 10], [2, 11], [0, 11]], (bb, p1))
    from flowocr.extract import ocr_args as _oa
    check("--region-crop 默认关、ADDED_NOOP 兜旧产物（没这个键的旧 obs 不重建）",
          _oa.parse_args(["v.mp4", "--out", "a"]).region_crop is False and _oa.ADDED_NOOP.get("region_crop") is False)

    # run_groups（多组同时跑）：组名、时间线按组分文件、拒掉会让进程数相乘的参数
    from flowocr.extract import run_groups as RGp
    with tempfile.TemporaryDirectory() as _d:
        _sp = Path(_d) / "r.json"
        _sp.write_text('{"groups": [{"name": "game", "rects": []}, {"name": "stream", "rects": []}]}', encoding="utf-8")
        _names = RGp.group_names(str(_sp))
        _sp.write_text('{}', encoding="utf-8")
        _names0 = RGp.group_names(str(_sp))
    check("run_groups.group_names：按配置取组名；没有 groups = 一个默认组", _names == ["game", "stream"] and _names0 == ["all"],
          (_names, _names0))
    check("run_groups.per_group：--timeline P / --timeline=P 按组改名（几组写同一份会互相覆盖），别的参数不动",
          RGp.per_group(["v", "--timeline", "t.jsonl", "--x"], "g") == ["v", "--timeline", "t.jsonl.g", "--x"]
          and RGp.per_group(["--timeline=t.jsonl"], "g") == ["--timeline=t.jsonl.g"])
    import contextlib
    import io as _io
    with contextlib.redirect_stdout(_io.StringIO()):
        _hw_default = RGp.hub_wanted(_oa.parse_args(["v.mp4", "--out", "a"]))
        _hw_off = RGp.hub_wanted(_oa.parse_args(["v.mp4", "--out", "a", "--decode-shards", "0"]))
        _hw_cv2 = RGp.hub_wanted(_oa.parse_args(["v.mp4", "--out", "a", "--decoder", "cv2"]))
    check("run_groups.hub_wanted：看 resolve_split 之后的值——不分片 / cv2 解码时不起广播解码（各组根本不会去接，它会等满订阅数挂到超时）",
          _hw_default == "" and _hw_off and "cv2" in _hw_cv2, (_hw_default, _hw_off, _hw_cv2))
    check("run_groups.check_rest：拒掉 --workers（组数 × 段数个进程）/ --out / --regions / --region-group",
          all(raises(lambda a=a: RGp.check_rest(a), SystemExit)
              for a in (["--workers", "3"], ["--workers=2"], ["--out", "x"], ["--region-group", "g"]))
          and not raises(lambda: RGp.check_rest(["--decode-shards", "2"]), SystemExit))

    # 广播解码的辅助流扇出（decode_proc.AuxFanout）：每个订阅者一条有界队列；某个订阅者的回抠进程断了，别的组照常收全
    from unittest import mock as _mock

    from flowocr.extract import decode_proc as DPm
    from flowocr.extract import decode_shards as DSm

    class _W:
        def __init__(self, remote):
            if remote.bad:
                raise ConnectionRefusedError("假的：这一组的回抠进程没了")
            self.remote = remote

        def send(self, frame, n, t):
            self.remote.got.append(n)

        def close(self):
            self.remote.closed = True
    from types import SimpleNamespace as _NS
    rems = [_NS(bad=False, got=[], closed=False, pts_put=lambda x: None), _NS(bad=True, got=[], closed=False, pts_put=lambda x: None)]
    with _mock.patch.object(DSm, "TcpAux", _W):
        fan = DPm.AuxFanout(rems, depth=4).open()
        for k in range(20):
            fan.send(_np.zeros(3, _np.uint8), k, k / 60)
        fan.close()
    check("AuxFanout：一份辅助流按订阅者各一条有界队列转出；一个订阅者连不上不堵别的组（队列满 4、送 20 帧也不卡）",
          rems[0].got == list(range(20)) and rems[0].closed and fan.dead == [False, True] and fan.errors, (rems[0].got, fan.dead))
    _sv = (ROOT / "src/flowocr/extract/supervisor.py").read_text(encoding="utf-8")
    check("supervisor：广播解码（FLOWOCR_DECODE_HUB）下硬解中途出错不各自改软解（N 路软解同时起），退出码交给 run_groups 整批重跑",
          'rc == EXIT_HW_MISMATCH and os.environ.get("FLOWOCR_DECODE_HUB")' in _sv
          and DPm.HUB_ENV == "FLOWOCR_DECODE_HUB")

    # 名牌判据的两边比较按可观察时间折算（方案 §4）：名牌那个框位一半时间被关掉，它的 run 被切点截短——
    # 截过的时长是配置截的，不能拿去和正文的自然时长比。正文 12 条各 3 s（y 800），名牌 12 条（y 700，名字轮着出）
    # 其中 8 条被截成 0.5 s 且标了 clipped，4 条是自然的 3 s
    from flowocr.analyze import uigate as UG
    S = 1_000_000
    items = []
    for k in range(12):
        items.append(UG.Item((100, 800, 900, 840), k * 5 * S, k * 5 * S + 3 * S, f"台词第{k}句的内容", key=("b", k), w=6))
        clipped = k < 8
        items.append(UG.Item((100, 740, 260, 780), k * 5 * S, k * 5 * S + (S // 2 if clipped else 3 * S),
                             ["エマ", "ノア"][k % 2], key=("n", k), w=6, clipped=clipped))
    got_np = {it.key for it in UG.pick_nameplates(items, win_us=120 * S, step_us=S // 2)}
    stripped = [UG.Item(it.box, it.t0, it.t1, it.text, key=it.key, w=it.w) for it in items]   # 同样的时长、不带截断标记
    got_old = {it.key for it in UG.pick_nameplates(stripped, win_us=120 * S, step_us=S // 2)}
    check("名牌判据：被配置截短的 run 不拿去比时长（截短 8 条时，不折算就漏掉整个名牌框位、折算后挑中）",
          {("n", k) for k in range(12)} <= got_np and not any(k[0] == "n" for k in got_old)
          and not any(k[0] == "b" for k in got_np), (sorted(got_np), sorted(got_old)))


def t_rec_window() -> None:
    """组批（`--rec-window` / `--rec-async`）的**状态转移**守卫（2026-09-19 审计第 1 条）。

    为什么非要单独一份：`--rec-window 1` 的逐字节自检**一条 W>1 的路都走不到**——
    `text_pending` 的严格门、`inherit`（沿用行的文本在 stage2 从链上取）、`_drop_pending(upto=)`、
    回补只看 `before` 这四条，只有窗口里才会发生。已知的两个洞都在这里：
    ①窗口头几帧的沿用行写出**空文本**（守卫全过、`lost_rec` 是 0、W=1 自检也过，只有逐框对账看得见）；
    ②拆 commit 时手工切 `pending` 没退 `cache_bytes`，**两个记账数一起错**。

    ⚠ 这份守卫用**假后端**测状态机，不测门的数值：`corr` 是可控的桩，每次"真读"发一个独一无二的串
    （**纯字母**——带数字会让 `hasdigit` 走严格门，那样两条臂都一直真读，守卫就什么都分不出来了）。
    """
    print("组批：--rec-window / --rec-async 的状态转移")
    import io
    import json
    import types

    import numpy as np

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.extract import ocr_args
    from flowocr.extract.reuse_v2 import ReuseV2

    # 相关系数落在两道门**之间**（--reuse-corr 0.8 < 0.9 < --reuse-corr-digit 0.98）的那几帧，
    # 就是"判决依赖手里的文本"的地方——策略 B 在这里把文本待定的链改判成真读。
    CORR = [1.0, 1.0, 0.9, 0.9, 1.0, 0.9, 1.0, 1.0, 0.9, 0.9, 1.0, 0.9] * 3
    BOXES = [[0, 0, 40, 18], [60, 0, 96, 18], [120, 0, 152, 18]]
    ABC = "abcdefghijklmnopqrstuvwxyz"

    def run(window: int, lag: int) -> tuple[list[dict], int, int, ReuseV2]:
        """跑一遍。`window <= 1` 走老路（`commit`）；否则 stage1 排队、**按帧序** stage2。

        `lag` = 结果滞后几帧才回来（0 = 整窗一起回 = 同步窗口；1 = 边跑边回 = 异步消费者）。
        """
        # `--no-reuse-confirm`：这组验的是策略 B（文本待定走严门）本身。假 rec 每次都返回新串、链永远证实不了，
        # 开着 `--reuse-confirm` 时所有链都走严门，策略 B 的效果被它盖住、测不出来（证实门另有守卫，见 t_reuse_confirm）
        args = ocr_args.parse_args(["dummy.mp4", "--out", "x.jsonl", "--no-refine-fused",
                                    "--reuse-cache-mb", "8", "--no-reuse-confirm"])
        state = {"corr": 1.0}
        tick = {"n": 0}

        def read() -> tuple[str, float]:
            tick["n"] += 1
            n = tick["n"]
            return ABC[n // 26 % 26] + ABC[n % 26] + ABC[n * 7 % 26], 0.99

        v = ReuseV2(args, lambda a, b: state["corr"], lambda a, b: (0, 0), lambda crop: read())
        fh = io.StringIO()
        win: list[tuple] = []
        n_read = n_pend = 0
        bad_cache = [0]

        def settle(items: list[tuple]) -> None:
            for st_, texts in items:                      # **按帧序**提交
                v.commit_stage2(st_, texts, fh)

        for seq, c in enumerate(CORR):
            state["corr"] = c
            polys = [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]] for b in BOXES]

            def crop(i):
                return np.full((18, 40, 3), (i + 1) * 7, np.uint8)

            n_pend += sum(ch.text_pending for ch in v.live)
            dec, todo, assign = v.decide(seq, np.zeros((40, 200), np.uint8), BOXES)
            n_read += len(todo)
            texts = {i: read() for i in todo}
            if window <= 1:
                dec2 = list(dec) + [(i, t, s_, 0) for i, (t, s_) in texts.items()]
                v.commit(seq, seq * 30, seq * 500_000, BOXES, polys, dec2, set(todo), assign,
                         crop, {}, False, fh)
                continue
            dec2 = list(dec) + [(i, "", 0.0, 0) for i in todo]
            st = v.commit_stage1(seq, seq * 30, seq * 500_000, BOXES, polys, dec2, set(todo),
                                 assign, crop, {}, False)
            win.append((st, texts))
            if len(win) >= window:                        # 窗口满了：整窗结算（同步那条路）
                settle(win)
                win = []
            elif lag and len(win) > lag:                  # 异步那条路：滞后 lag 帧就结算最老的一帧
                settle(win[:1])
                win = win[1:]
            real = sum(int(pnd[2].nbytes) for c in v.live for pnd in c.pending)
            book = sum(c.cache_bytes for c in v.live)
            bad_cache[0] += (book != real or v.cache != book + sum(v.reserved.values())
                             or v.cache > v.cache_cap)
        settle(win)
        v.flush_all(fh)
        rows = [json.loads(x) for x in fh.getvalue().splitlines()]
        return rows, n_read, n_pend, v, bad_cache[0]

    r0, read0, _p0, _v0, _c0 = run(0, 0)
    r8, read8, pend8, v8, bad_cache = run(8, 0)
    r8b, read8b, _p8b, _v8b, _c8b = run(8, 0)
    ra, reada, penda, _va, _ca = run(8, 3)
    _r1, read1, pend1, _v1, _c1 = run(8, 1)

    reused8 = [r for r in r8 if r.get("reused")]
    tokens = {r["text"] for r in r0} | {r["text"] for r in r8} | {r["text"] for r in ra}
    check("窗口里**没有空文本的沿用行**（§11.6 那个洞：只回填 pending 盖不住新链）",
          len(reused8) > 10 and all(r["text"] for r in reused8),
          (len(reused8), [r for r in reused8 if not r["text"]][:3]))
    check("沿用行的文本一定是**某次真读发出来的**那个串（不是占位、也不是串台）",
          all(r["text"] in tokens and len(r["text"]) == 3 for r in reused8),
          [r["text"] for r in reused8 if len(r["text"]) != 3][:3])
    check("窗口和逐帧**产出的行数、框、时刻一致**（只有文本可能因为多读而不同）",
          [(r["frame"], tuple(r["box"])) for r in r0] == [(r["frame"], tuple(r["box"])) for r in r8],
          (len(r0), len(r8)))
    check("同一条路跑两遍**逐行相同**（状态机本身是确定的；产物的抖动只来自后端）", r8 == r8b,
          next(((a, b) for a, b in zip(r8, r8b) if a != b), None))
    check("策略 B 真的生效了：歧义带上文本待定的链**改判成真读**，所以窗口里读得更多",
          pend8 > 0 and read8 > read0, (read0, read8, pend8))
    # ⚠ **同步和异步的产物本来就不一样**，不是 bug：结果回得早，链的文本就早一步填上，
    # 后面几帧的 `text_pending` 跟着少——滞后（固定滞后结算的 W）越长，待定越多、真读越多。
    # 2026-09-23 改口：原来比的是"滞后 3 对同步整窗 8"、断言前者待定更少——那只在**结算最老的一个就清掉待定**的旧代码里成立
    # （Codex 审计 P1：还有更晚的读在途时不许清）。按在途计数之后，模糊带（0.8~0.98）上读过一次的链每帧都走严门、
    # 一路读下去，而整窗同步在窗口边界一次清空——两者不单调可比。单调可比的是同一个窗口下的滞后长短
    check("同一个窗口，结果回得越早（滞后 1 对 3），文本待定的链越少、真读也越少",
          pend1 <= penda and read1 <= reada and penda > 0, (pend1, penda, read1, reada))
    check("窗口路径上**缓存记账逐帧对着真字节核**（拆 commit 那次就是这里错的；"
          "跑完才核一次会放过中途虚高——`t_reuse_v2` 那条本来就是逐帧的）",
          bad_cache == 0 and v8.stats["cache_overrun"] == 0, (bad_cache, v8.stats["cache_overrun"]))


def t_recpool() -> None:
    """`RecPool`（`--rec-async` 的后台消费者）本身的守卫（2026-09-19 复审第 4 条）。

    上面那条窗口守卫是拿"滞后几帧"模拟异步的，**没经过线程**；而池子这一层能静默出错的地方
    恰恰都不在判决里：结果错位、少返回、线程里的异常、插队、收工排空。用假 rec 几行就能钉住。
    """
    print("RecPool（异步消费者）")
    import threading
    import types

    import numpy as np

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.extract.recpool import RecPool

    class FakeRec:
        """每个裁剪的"文本"= 它第一个像素的值，于是**错位一眼看得出来**。"""

        def __init__(self, drop=0):
            self.drop, self.batches = drop, []

        def predict(self, crops, batch_size=1):
            self.batches.append(len(crops))
            out = [{"rec_text": f"v{int(c[0, 0, 0])}", "rec_score": 0.5} for c in crops]
            return out[:len(out) - self.drop] if self.drop else out

    def crop(v, w=320):
        return np.full((48, w, 3), v, np.uint8)

    rec = FakeRec()
    pool = RecPool([rec], cap=8, ratio=0.0, knee=4)
    ids = pool.new_ids(12)
    pool.submit([(r, crop(i + 1)) for i, r in enumerate(ids)])
    got = pool.collect(ids)
    check("结果**按请求号对回去**，不会错位（假 rec 把像素值当文本）",
          [got[r][0] for r in ids] == [f"v{i + 1}" for i in range(12)],
          [got[r][0] for r in ids])
    check("**真的攒成了批**（膝点 4、上限 8）", max(rec.batches) >= 4, rec.batches)
    check("池子的账：调用数和送进去的裁剪数都对得上",
          pool.stats()["sent"] == 12 and pool.stats()["calls"] == len(rec.batches),
          pool.stats())
    pool.close()

    # 后端少返回：**缺的位置必须是 None**，绝不能把后面的结果顶上来
    pool = RecPool([FakeRec(drop=1)], cap=8, ratio=0.0, knee=1)
    ids = pool.new_ids(3)
    pool.submit([(r, crop(9)) for r in ids])
    got = pool.collect(ids)
    check("后端少返回时**补 None、不错位**（主线程据此记 n_lost）",
          sum(v is None for v in got.values()) >= 1, got)
    pool.close()

    # 插队：优先项所在的那一组先走
    pool = RecPool([FakeRec()], cap=8, ratio=0.0, knee=99)     # 膝点高到永远不自发
    slow = pool.new_ids(2)
    pool.submit([(r, crop(1, 320)) for r in slow])
    fast = pool.new_ids(1)
    pool.submit([(r, crop(2, 640)) for r in fast])
    got = pool.collect(fast, prio=True)                       # 只等插队那一个
    check("**插队**：膝点再高，被标成优先的请求也会被发出去", got[fast[0]][0] == "v2", got)
    pool.close()

    # 线程里的异常要带回主线程，而不是把主线程挂死
    class Boom:
        def predict(self, crops, batch_size=1):
            raise RuntimeError("后端炸了")

    pool = RecPool([Boom()], cap=4, ratio=0.0, knee=1)
    ids = pool.new_ids(1)
    pool.submit([(ids[0], crop(3))])
    try:
        pool.collect(ids)
        boom = "没抛"
    except RuntimeError as exc:
        boom = str(exc)
    check("后台线程的异常**带回主线程**（不然主线程等到 stall_sec 才知道）", boom == "后端炸了", boom)

    # 收工：关了之后要把排着的排空
    rec = FakeRec()
    pool = RecPool([rec], cap=8, ratio=0.0, knee=99)
    ids = pool.new_ids(5)
    pool.submit([(r, crop(4)) for r in ids])
    pool.close()
    check("`close()` 把排着的**排空**（膝点没到也要发），而且线程真的退了",
          not pool.pend and pool.stats()["sent"] == 5 and not any(th.is_alive() for th in pool.ths), pool.stats())

    # 分组键是**形状宽**（同 predict_bucketed）：后端报了 shape_w 时，330/340/600/640 都是 (n, 640)，该一批（decode-buffer §8.15）
    class ShapeRec(FakeRec):
        def shape_w(self, tw):
            from flowocr.extract import recprep
            return recprep.grid_w(tw, 320)

    for R, want in ((FakeRec, 4), (ShapeRec, 1)):
        rec = R()
        pool = RecPool([rec], cap=8, ratio=0.0, knee=99)
        ids = pool.new_ids(4)
        pool.submit([(r, crop(i + 1, w)) for i, (r, w) in enumerate(zip(ids, (330, 340, 600, 640)))])
        pool.close()
        check(f"异步池按{'形状宽' if R is ShapeRec else '精确宽'}分组：4 个裁剪 → {want} 次调用",
              len(rec.batches) == want, rec.batches)
    check("池子按分组宽记批大小（shape_hist）", pool.stats()["shape_hist"] == {"640": {"4": 1}}, pool.stats())

    # 几个在途（--rec-inflight，decode-buffer §8.18）：两个后端各一个线程、从同一个队列挑；结果照样按请求号对回去，
    # 两个后端真的**同时**在推理（慢后端 + 屏障：两个线程都进了 predict 才放行，只有一个线程时会超时）
    class SlowRec(FakeRec):
        gate = threading.Barrier(2, timeout=5)

        def predict(self, crops, batch_size=1):
            try:
                self.gate.wait()
                self.met = True
            except threading.BrokenBarrierError:
                pass
            return super().predict(crops, batch_size)

    a, b = SlowRec(), SlowRec()
    pool = RecPool([a, b], cap=1, ratio=0.0, knee=1)
    ids = pool.new_ids(6)
    pool.submit([(r, crop(i + 1)) for i, r in enumerate(ids)])
    got = pool.collect(ids)
    pool.close()
    check("两个后端同时在途：结果按请求号对回去、两个都用上了、确实有过两个同时在 predict 里",
          [got[r][0] for r in ids] == [f"v{i + 1}" for i in range(6)] and a.batches and b.batches
          and getattr(a, "met", False) and pool.stats()["inflight"] == 2 and pool.stats()["sent"] == 6,
          (a.batches, b.batches, pool.stats()))


def t_recpack() -> None:
    """rec 打包的纯函数（09-10）：近宽分组、异步池挑批、CTC 贪心解码。"""
    print("recpack")
    import numpy as np

    from flowocr.extract import ocr_args as _oa
    from flowocr.extract import recort as _rc
    _ws = _rc.warm_shapes(16)
    check("rec 预热表（recort.warm_shapes）：宽都在分桶网格上、批都是 2 的幂且不超 max_batch；窄的批到 16、宽的只到 2；"
          "--ort-warm 默认开、在 NOISE_EQUIV（不改读数）",
          all(w % _rc.GRID == 0 and b & (b - 1) == 0 and b <= 16 for b, w in _ws)
          and (16, 320) in _ws and (4, 960) in _ws and (4, 1280) not in _ws and (2, 2560) in _ws
          and all(b <= 4 for b, w in _rc.warm_shapes(4))
          and _oa.parse_args(["v.mp4", "--out", "a"]).ort_warm and "ort_warm" in _oa.NOISE_EQUIV, _ws)
    from flowocr.extract import recpack as rp

    g = rp.group_near([320, 320, 336, 400, 320, 800], 0.0, 32)
    check("ratio=0 就是同宽分桶", sorted(map(sorted, g)) == [[0, 1, 4], [2], [3], [5]])
    g = rp.group_near([320, 320, 336, 400, 320, 800], 0.25, 32)
    check("近宽：336/400 并进 320 那组（≤ 320×1.25），800 单独",
          sorted(map(sorted, g)) == [[0, 1, 2, 3, 4], [5]])
    check("组内最大宽不超过最小宽 ×(1+ratio)",
          all(max(w) <= min(w) * 1.1 for w in
              ([[320, 330, 352, 360, 500, 510][i] for i in grp]
               for grp in rp.group_near([320, 330, 352, 360, 500, 510], 0.1, 32))))
    check("每组至多 cap 个", max(map(len, rp.group_near([320] * 70, 0.0, 32))) == 32)
    check("每个下标恰好出现一次",
          sorted(i for grp in rp.group_near([5, 3, 9, 3, 7], 0.5, 2) for i in grp) == [0, 1, 2, 3, 4])

    # pick_batch：异步消费者"有多少取多少"的判据（09-19，组批第二步）
    check("pick_batch 空表返回空", rp.pick_batch([], 0.05, 32) == [])
    check("没有优先项就取**最大的那一组**（两个桶 2 条和 5 条 -> 取 5 条，不等谁长满）",
          rp.pick_batch([320, 320, 800, 800, 800, 800, 800], 0.0, 32) == [2, 3, 4, 5, 6])
    check("有优先项就取**含优先项最多**的那一组（哪怕它更小）",
          rp.pick_batch([320, 320, 800, 800, 800, 800, 800], 0.0, 32, {0, 1}) == [0, 1])
    check("优先项那一组仍然按近宽 + cap 组好——插队不等于把批打小",
          rp.pick_batch([320, 320, 320, 800], 0.0, 32, {0}) == [0, 1, 2])
    check("pick_batch 也守 cap", len(rp.pick_batch([320] * 70, 0.0, 32)) == 32)
    # **反复取最大组 = 一次性分桶的同一批批**（静态集合下）。这是
    # `--rec-window 1 --rec-async` 能和同步路径逐字节相同的前提：一帧的裁剪一次到齐、中途没有新请求，
    # 于是后台线程一组一组地取，取出来的**批的集合**和 `predict_bucketed` 一次分好的完全一样（只是顺序不同）。
    import random as _rnd
    _rng = _rnd.Random(7)
    same = True
    for _ in range(200):
        ws = [_rng.choice([320, 320, 320, 336, 352, 400, 480, 640, 960]) for _ in range(_rng.randint(1, 40))]
        ratio, cap = _rng.choice([0.0, 0.05, 0.2]), _rng.choice([2, 8, 32])
        one = sorted(sorted(g) for g in rp.group_near(ws, ratio, cap))
        left, got = list(range(len(ws))), []
        while left:
            pick = rp.pick_batch([ws[i] for i in left], ratio, cap)
            got.append(sorted(left[k] for k in pick))
            left = [i for k, i in enumerate(left) if k not in set(pick)]
        same &= sorted(got) == one
    check("反复取最大组 = 一次性分桶的同一批批（200 组随机宽度）", same)

    chars = ["blank", "a", "b", "c"]
    idx = np.array([1, 1, 0, 2, 2, 0, 1])
    prob = np.array([0.9, 0.8, 0.99, 0.7, 0.6, 0.99, 0.5])
    t, sc = rp.ctc_greedy(idx, prob, chars)
    check("CTC 贪心：相邻重复合并、去 blank", t == "aba")
    check("分数 = 留下的时间步的最大概率均值（和 PaddleX 同口径）", abs(sc - (0.9 + 0.7 + 0.5) / 3) < 1e-9)
    check("一个字都没留下时分数为 0", rp.ctc_greedy(np.array([0, 0]), np.array([0.9, 0.9]), chars)
          == ("", 0.0))

    check("张量宽：短行补到 320，长行按比例，超过 3200 的封顶（PaddleX 会压扁）",
          rp.rec_target_w(48, 100) == 320 and rp.rec_target_w(48, 960) == 960
          and rp.rec_target_w(10, 5000) == 3200)

    class FakeRec:
        def __init__(self, drop_last=False):
            self.calls, self.drop_last = [], drop_last

        def predict(self, crops, batch_size):
            self.calls.append(batch_size)
            out = [{"rec_text": f"w{c.shape[1]}"} for c in crops]
            return out[:-1] if self.drop_last and len(out) > 1 else out

    crops = [np.zeros((48, w, 3), np.uint8) for w in (100, 960, 100, 200, 960)]
    fr = FakeRec()
    res, ncall = rp.predict_bucketed(fr, crops, 32)
    check("分桶：结果按原顺序回填", [r["rec_text"] for r in res] == ["w100", "w960", "w100", "w200", "w960"])
    check("分桶：320 宽的三个（100/200 都补到 320）一批、960 两个一批 → 2 次调用",
          ncall == 2 and sorted(fr.calls) == [2, 3])
    res, _ = rp.predict_bucketed(FakeRec(drop_last=True), crops, 32)
    check("**某批少返回一条时按下标回填 None，绝不压缩**（压缩会让后面的框配到前一个框的文本）",
          sum(r is None for r in res) == 2 and res[0] is not None)
    _, ncall = rp.predict_bucketed(FakeRec(), [np.zeros((48, 100, 3), np.uint8)] * 70, 32)
    check("分桶：每批至多 cap 个（70 个 → 32+32+6）", ncall == 3)

    class ShapeRec(FakeRec):
        """后端自己收形状的（OrtRec）：上游要按**形状宽**分桶，不是精确宽。"""
        def shape_w(self, tw):
            from flowocr.extract import recdecode
            return recdecode.grid_w(tw, 320)
    near = [np.zeros((48, w, 3), np.uint8) for w in (330, 340, 600, 640)]      # 都落在 640 这个形状上
    _, n_exact = rp.predict_bucketed(FakeRec(), near, 32)
    sr = ShapeRec()
    _, n_shape = rp.predict_bucketed(sr, near, 32)
    check("(batch, width) 只由一处决定：后端报了 `shape_w` 就按形状宽分桶（4 个精确宽 → 1 批）",
          n_exact == 4 and n_shape == 1 and sr.calls == [4])
    from flowocr.extract import recdecode as _rd
    check("`grid_w`：向上取整到网格、封顶 REC_MAX_W、grid=0 原样",
          (_rd.grid_w(330, 320), _rd.grid_w(640, 320), _rd.grid_w(3300, 320), _rd.grid_w(500, 0))
          == (640, 640, rp.REC_MAX_W, 500))


def t_ocr_parallel() -> None:
    """`run_ocr2 --workers N` 的切段 / 改参数 / 拼 _meta（09-10）。"""
    print("ocr_parallel")
    from flowocr.extract import ocr_parallel as op

    cuts = op.split_frames(890580, 908580, _every(30), 3)
    check("切点在采样网格上（等距：30 的整数倍）", all(c % 30 == 0 for c in cuts))
    from flowocr.extract.framegrid import TimeGrid as _TG
    _g8 = _TG(2, 15)                                      # 60 fps 上 8 fps（不等距）
    _c8 = op.split_frames(0, 36000, _g8, 3)
    check("不等距网格的切点也在网格上、大致三等分", all(_g8.member(c) for c in _c8) and abs(_c8[0] - 12000) <= 8, _c8)
    check("切点严格递增、落在窗口内部",
          890580 < cuts[0] < cuts[1] < 908580 and len(cuts) == 2)
    check("等分：每段 6000 帧", cuts == [896580, 902580])
    check("workers=1 不切", op.split_frames(0, 18000, _every(30), 1) == [])
    try:
        op.split_frames(0, 60, _every(30), 3)
        raised = False
    except ValueError:
        raised = True
    check("窗口太短切不开时抛，不静默给出空段", raised)

    argv = ["v.mp4", "--out", "o.jsonl", "--start=10", "--end", "20", "--rec-bucket", "16",
            "--workers", "3", "--det-batch", "4"]
    check("去掉 --out/--start/--end/--workers（含 --x=v 写法），其余旋钮原样保留",
          op.strip_opts(argv, {"--out", "--start", "--end", "--workers"})
          == ["v.mp4", "--rec-bucket", "16", "--det-batch", "4"])

    def part(first, last, **kw):
        return {"complete": True, "bad_pts": None, "stopped_early": False,
                "frames_advanced": 100, "frames_requested": 100, "sampled_frames": 10,
                "boxes": 50, "rec_calls": 20, "reused_boxes": 30, "dropped_dense": 0,
                "dropped_tiny": 1, "lost_rec": 0, "wall_sec": 5.0, "start_sec": first,
                "end_sec": last, "pts_first_sec": first, "pts_last_sec": last, **kw}

    base = {"config": {"workers": 2}, "video": "v"}
    m = op.merge_metas([part(0.0, 4.5), part(5.0, 9.5)], base, wall=6.0, cuts_sec=[5.0], interval=0.5)
    check("计数相加", m["boxes"] == 100 and m["rec_calls"] == 40 and m["sampled_frames"] == 20)
    check("复用率按合计重算", m["reuse_rate"] == round(60 / 100, 4))
    check("时间轴取首段起点、末段终点", m["pts_first_sec"] == 0.0 and m["pts_last_sec"] == 9.5)
    check("墙钟是外层的，不是各段相加", m["wall_sec"] == 6.0 and m["part_wall_sec"] == [5.0, 5.0])
    check("配置来自父进程（workers=2 进 config）", m["config"] == {"workers": 2} and m["complete"])
    bad = op.merge_metas([part(0.0, 5.0), part(5.0, 9.5)], base, wall=6.0, cuts_sec=[5.0], interval=0.5)
    check("**段间 PTS 不递增 → 不算完成**（每段内部的门管不到接缝）",
          not bad["complete"] and "段间" in bad["bad_pts"])
    check("**中间段**没跑完 → 整体没跑完",
          not op.merge_metas([part(0.0, 4.5, complete=False), part(5.0, 9.5)], base,
                             wall=6.0, cuts_sec=[5.0], interval=0.5)["complete"])
    # 审查 2026-09-10：`end=0` 时容器虚报的 1% 帧数全压在末段上，3 个 worker 时末段只有 97%
    tail = part(10.0, 14.5, complete=False, frames_requested=12361, frames_advanced=12001)
    three = [part(0.0, 4.5, frames_requested=12000, frames_advanced=12000),
             part(5.0, 9.5, frames_requested=12000, frames_advanced=12000), tail]
    check("**末段自己没过覆盖率、整窗口过了 → 算完成**（和单进程同一把尺：36001/36361 = 99%）",
          op.merge_metas(three, base, wall=6.0, cuts_sec=[5.0, 10.0], interval=0.5)["complete"])
    short = [*three[:2], part(10.0, 14.5, complete=False, frames_requested=12361,
                              frames_advanced=6000)]
    check("末段真的断了（整窗口 < 98%）→ 不算完成",
          not op.merge_metas(short, base, wall=6.0, cuts_sec=[5.0, 10.0], interval=0.5)["complete"])
    hole = op.merge_metas([part(0.0, 4.5), part(8.0, 12.5)], base, wall=6.0, cuts_sec=[5.0],
                          interval=0.5)
    check("**切点处漏了一截（PTS 仍递增）→ 不算完成**——段间递增判据拦不住的那种",
          not hole["complete"] and hole["bad_pts"] is None and "漏帧" in hole["seam_problems"][0])
    check("正常切点（间隔恰好一个采样间隔）没有报警", m["seam_problems"] == [])
    check("名义 / 平均帧率解析", op.parse_rate("60/1") == 60.0
          and abs(op.parse_rate("60000/1001") - 59.94) < 0.01
          and op.parse_rate("0/0") is None and op.parse_rate("N/A") is None)
    f3 = op.seam_drift_sec(1997062, 998531000 / 16648233, 60.0)
    f5 = op.seam_drift_sec(1332360, 1332360000 / 22205999, 60.0)
    gate = op.DRIFT_MAX_FRAMES / 60.0
    check("**f3（有缺口）整片漂移远超半帧门限，f5（没缺口）远低于**——拒绝开多 worker 的判据分得开",
          f3 > gate * 100 and f5 < gate / 5)
    f3_5min = op.seam_drift_sec(18000, 998531000 / 16648233, 60.0)
    check("**按整个文件估，不按窗口**：f3 上 5 分钟窗口按窗口估只有 0.11 s（会被放行），"
          "按整片估是 12 s（拒）——复审 2026-09-10",
          f3_5min < 0.125 and f3 > gate)
    from flowocr.extract import ocr_args
    check("workers / det-batch 是产物参数（进 config，复用判据会比）",
          {"workers", "det_batch"} <= set(ocr_args.config_of(["v.mp4", "--out", "x"]))
          and not ({"workers", "det_batch"} & ocr_args.NON_PRODUCT))


def t_ptsclock() -> None:
    """时间戳只认真实 PTS（hw-decode 计划 §0）。

    为什么要守：坏时间轴**不会让任何下游报错**，只会让所有时间都错一点。
    旧口径 `idx / src_fps` 用的是容器平均帧率，f3 在 7.2 h 处晚 9.5 s——
    整整半年没人发现，就是因为它不抛异常。
    """
    print("ptsclock")
    import json
    import tempfile
    import types

    from flowocr.extract import ptsclock as pc

    c = pc.PtsClock()
    check("正常路径没坏：0.5 s 一格换出微秒整数",
          [c.push(t) for t in (0.0, 0.5, 1.0)] == [0, 500_000, 1_000_000])
    check("跨 pts 缺口不拦——缺口是素材的事实，不是错误",
          c.push(6.2) == 6_200_000)
    check("覆盖到的跨度按首末算", abs(c.span_sec - 6.2) < 1e-9)
    check("数了几帧", c.n == 4)

    m = c.meta()
    check("`timebase` 是下游认时间轴的唯一凭据", m["timebase"] == "pts")
    check("首末 pts 一起写进 _meta",
          (m["pts_first_sec"], m["pts_last_sec"]) == (0.0, 6.2))

    c2 = pc.PtsClock(); c2.push(1.0)
    check("守卫会响：pts 倒退", raises(lambda: c2.push(0.9), pc.BadPts))
    c3 = pc.PtsClock(); c3.push(1.0)
    check("**守卫会响：两帧同一个时刻**（不响的话下游把两次观测叠成一个时刻，"
          "既不报错也数不出来）", raises(lambda: c3.push(1.0), pc.BadPts))
    check("守卫会响：NaN", raises(lambda: pc.PtsClock().push(float("nan")), pc.BadPts))
    check("守卫会响：inf", raises(lambda: pc.PtsClock().push(float("inf")), pc.BadPts))
    check("守卫会响：负数", raises(lambda: pc.PtsClock().push(-0.001), pc.BadPts))
    check("BadPts 是 ValueError 的子类，老式 except 也接得住",
          issubclass(pc.BadPts, ValueError))
    # **这两条以前嵌在 `except` 里**：不抛就一条都不执行，而"不抛"恰恰是要抓的失败，
    # 于是守卫会静默地什么都不测。改成先把消息取出来再断言（取不到就是空串，必挂）。
    def bad_msg(**kw) -> str:
        c = pc.PtsClock(); c.push(1.0)
        try:
            c.push(0.5, **kw)
        except pc.BadPts as exc:
            return str(exc)
        return ""                                     # 没抛 = 下面两条必然不过

    check("没给 where 就不编一个帧号出来", bad_msg() and "帧" not in bad_msg())
    check("给了 where 就要带上", "帧 12345" in bad_msg(where="帧 12345"))

    empty = pc.PtsClock()
    check("一帧都没有时跨度是 0，不抛也不除零", empty.span_sec == 0.0)
    check("一帧都没有时首末是 None",
          empty.meta()["pts_first_sec"] is None
          and empty.meta()["pts_last_sec"] is None)
    # ---- IndexClock：帧号 <-> 真实时间（回抠这一级，2026-09-17，reuse-budget 计划 §8 第 1 条）----
    us = 1_000_000 / 59.987
    const = pc.IndexClock(us)
    check("**不给锚点就是原来的常量换算**（CFR 素材上产物因此不变）",
          const.t_of(1234) == 1234 * us and const.idx_of(1234 * us) == 1234 and len(const) == 0)
    # 合成一段有丢帧缺口的时间轴：采样点每 30 帧一个，第 60 帧之后少交付了 12 帧
    pairs = [(0, 0), (30, 500_000), (60, 1_000_000), (90, 1_700_000), (120, 2_200_000)]
    ck = pc.IndexClock(us, pairs)
    check("锚点上取值精确、两个锚点之间线性插值（缺口那一段用的是那一段自己的帧率）",
          ck.t_of(60) == 1_000_000 and ck.t_of(75) == 1_350_000 and ck.t_of(45) == 750_000)
    check("t_of / idx_of 严格互逆（含缺口那一段）",
          all(abs(ck.idx_of(ck.t_of(i)) - i) < 1e-6 for i in (0, 17, 45, 60, 75, 101, 120)))
    check("锚点之外退回名义帧率（窗口首尾会探出去几十帧）",
          ck.t_of(-10) == -10 * us and abs(ck.t_of(130) - (2_200_000 + 10 * us)) < 1e-6)
    check("**缺口之后帧号时钟和 pts 差出 12 帧**（这就是回抠窗口原来错开的量）",
          round((ck.t_of(120) - 120 * us) / us) == 12)
    d = Path(tempfile.mkdtemp())
    obs = d / "o.jsonl"
    obs.write_text("\n".join([
        json.dumps({"_meta": {"timebase": "pts", "src_fps": 59.987}}),
        json.dumps({"frame": 0, "t_us": 0, "text": "a"}),
        json.dumps({"frame": 0, "t_us": 0, "text": "b"}),          # 同一帧的第二个框：不重复记锚点
        json.dumps({"frame": 30, "t_us": 500_100, "text": "c"}),
    ]) + "\n", encoding="utf-8")
    ck2 = pc.index_clock_from_obs(obs, us)
    check("从 obs 建时钟：逐采样点取 (帧号, pts)、同一帧只记一次", len(ck2) == 2 and ck2.t_of(30) == 500_100)
    old = d / "old.jsonl"
    old.write_text("\n".join([
        json.dumps({"_meta": {"src_fps": 59.987}}),
        json.dumps({"frame": 30, "t_us": 500_100, "text": "c"}),
    ]) + "\n", encoding="utf-8")
    check("**旧产物（时间轴不是 pts）一律退回常量换算**——那种时间轴本身不可信，不能拿来当锚点",
          len(pc.index_clock_from_obs(old, us)) == 0)
    # make_job 两头都走时钟：缺口之后的 run，窗口整体挪过去
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.analyze import refine_boundaries as rb
    run = {"t_start": 2_200_000, "t_end": 2_700_000, "box": [0, 0, 10, 10], "text": "ab", "n_obs": 2}
    ja = rb.make_job(dict(run), 500_000, us, ck)
    jb = rb.make_job(dict(run), 500_000, us, const)
    # 方向两种都真实：**缺口**让 pts 落在帧号时钟之后（常量换算把时刻算到 12 帧之后，这里就是这种），
    # 而**平均帧率偏低**让 pts 跑在前面（f4 的 20 分钟窗口里没有缺口，光这一项就 15 帧 / 250 ms）
    check("**窗口按真实 pts 开**：缺口之后同一条 run 的窗整体挪 12 帧（原来就是错开这么多开的）",
          jb["s"] - ja["s"] == 12 and jb["e"] - ja["e"] == 12 and jb["tw_lo"] - ja["tw_lo"] == 12,
          (ja["s"], jb["s"]))
    check("不给时钟的 make_job 和改之前逐值相同（守卫里别的用例就是这么调的）",
          rb.make_job(dict(run), 500_000, us)["s"] == jb["s"])


def t_framesource() -> None:
    """采样帧从哪来（hw-decode 计划 B / C）。

    只测**纯逻辑**：命令长什么样、pts 怎么解析、预取保不保序、结束标记。
    真解码的等价性靠端到端 obs 对账（hw-decode-results 报告 §3）。
    """
    print("framesource")
    from flowocr.extract import framesource as fs

    cmd = fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0.0, n_out=120)
    s = " ".join(cmd)
    check("**用 select 不用 fps**（fps 是补帧滤镜，pts 缺口处会复制帧填上）",
          "select=" in s and "-vf fps" not in s and ",fps=" not in s)
    check("**给了 -fps_mode passthrough**（否则 muxer 那层还会再补一次帧）",
          "-fps_mode" in cmd and cmd[cmd.index("-fps_mode") + 1] == "passthrough")
    check("挂了 showinfo（管道里是裸像素，pts 只能从 stderr 读）", "showinfo" in s)
    check("默认 nv12（带宽减半 + 转换与 cv2 同源）",
          cmd[cmd.index("-pix_fmt") + 1] == "nv12")
    check("送出的帧数写死，不靠对面自己停",
          cmd[cmd.index("-frames:v") + 1] == "120")
    check("start=0 时不加 -ss", "-ss" not in cmd)
    seek = fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=12.5, n_out=4)
    check("start>0 时 -ss 在 -i **前面**（快速且精确 seek）",
          "-ss" in seek and seek.index("-ss") < seek.index("-i"))
    check("**seek 时必须给 -copyts**（`-ss` 默认把时间戳归零，"
          "窗口产物的时间轴会整体平移几个小时，而没有任何一层会报错）",
          "-copyts" in seek and seek.index("-copyts") < seek.index("-i"))
    # 精确 seek 修之前丢了首格的产物要作废，别的旧产物照旧复用（2026-09-26 审计：tmp/tg/yuka-f5-f22.jsonl 那一份；
    # 当时磁盘上 3,461 份 obs 里只有它中这个形状）
    from flowocr.extract import ocr_complete as OCC
    bad = {"start_sec": 14843.0, "sample_fps": 2.2, "pts_first_sec": 14843.65, "grid_skipped": 1}
    check("复用·精确 seek 修之前丢了窗口首格的产物作废；带 seek_rev 的、从文件头起的、没记缺格的、首格没晚的都不作废",
          OCC.seek_dropped_first(bad) != ""
          and OCC.seek_dropped_first({**bad, "seek_rev": fs.SEEK_REV}) == ""
          and OCC.seek_dropped_first({**bad, "start_sec": 0.0}) == ""
          and OCC.seek_dropped_first({**bad, "grid_skipped": 0}) == ""
          and OCC.seek_dropped_first({**bad, "pts_first_sec": 14843.183}) == "")
    check("bgr24 时要挂一层 format=bgr24",
          "format=bgr24" in " ".join(
              fs.build_ffmpeg_cmd("a.mp4", grid=_every(2), start_sec=0, n_out=2, pix="bgr24")))
    check("守卫会响：网格的帧率比不为正",
          raises(lambda: fs.TimeGrid(0, 30), ValueError) and raises(lambda: fs.TimeGrid.for_rate(60, 0), ValueError))
    check("守卫会响：要送 0 帧",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", grid=_every(1), start_sec=0, n_out=0),
                 ValueError))
    # 回抠的窗口解码（2026-09-16，reuse-budget 计划 §7）：帧号集合并区间、select 只送区间里的帧
    check("merge_intervals：排序去重、相邻并起来、gap 内也并",
          fs.merge_intervals([5, 3, 4, 9, 10, 20, 20]) == [(3, 5), (9, 10), (20, 20)]
          and fs.merge_intervals([1, 4, 8], gap=2) == [(1, 4), (8, 8)])
    wcmd, base, n_out = fs.build_ffmpeg_window_cmd("a.mp4", [(120, 130), (300, 302)], src_fps=60.0)
    check("窗口命令：-ss 到第一个区间起点并 -copyts，select 的 n 按起点重编，帧数 = 区间长度之和",
          base == 120 and n_out == 14 and "-copyts" in wcmd and wcmd[wcmd.index("-ss") + 1] == "2.000000"
          and any("between(n\\,0\\,10)+between(n\\,180\\,182)" in x for x in wcmd)
          and wcmd[wcmd.index("-frames:v") + 1] == "14", wcmd)
    check("窗口 step / scale：隔帧按绝对帧号对齐、模板帧照取；scale 进滤镜链；坏值会响",
          fs.window_frames([(120, 125)], step=2, keep=[123]) == [120, 122, 123, 124]
          and fs.build_ffmpeg_window_cmd("a.mp4", [(120, 125)], src_fps=60.0, step=2, keep=[123])[2] == 4
          and any("*not(mod(n+120\\,2))" in x and "eq(n\\,3)" in x
                  for x in fs.build_ffmpeg_window_cmd("a.mp4", [(120, 125)], src_fps=60.0, step=2, keep=[123])[0])
          and any("scale=trunc(iw*0.5/2)*2" in x for x in fs.build_ffmpeg_window_cmd("a.mp4", [(0, 3)], src_fps=30.0, scale=0.5)[0])
          and raises(lambda: fs.build_ffmpeg_window_cmd("a.mp4", [(0, 3)], src_fps=30.0, step=0), ValueError)
          and raises(lambda: fs.build_ffmpeg_window_cmd("a.mp4", [(0, 3)], src_fps=30.0, scale=1.5), ValueError))
    # **窗口 seek 必须按真实 pts**（2026-09-18 审计的 P1）：`select` 里的 `n` 数的是 seek 之后交付的第几帧，
    # 所以"n + base = 绝对帧号"只在 seek 恰好落在帧 base 上时成立。按 `base/src_fps`（容器平均帧率）算，
    # 有缺口的素材上会落错位置——**交付的是别的帧，却仍被标成我们要的帧号**。
    # 最小反例和修好之后的对照在 a1-mask-reuse 实验里的 `gap_window_check.py`（合成一段带缺口的视频，
    # 灰度值 = 帧号 × 10，所以"取到的是哪一帧"是看得见的）：旧行为取到 69/80，要的是 50/60。
    # 实测这份偏差在真素材上有多大：e-yuka-f4 的 20 分钟窗口末端 **−257.7 ms = 15.5 帧**（60.0 vs 59.9870）。
    def ss_of(**kw):
        c = fs.build_ffmpeg_window_cmd("a.mp4", [(120, 130)], src_fps=60.0, **kw)[0]
        return c[c.index("-ss") + 1]

    check("**窗口 seek 用真实 pts（base_ts）**，不给才退回 base/src_fps",
          ss_of(base_ts=2.257) == "2.257000" and ss_of() == "2.000000",
          (ss_of(base_ts=2.257), ss_of()))
    # 窗口解码那半 2026-09-22 拆进了 extract/refine_video（tools/refine_boundaries.py 只剩 shim）
    src_rb = (ROOT / "src" / "flowocr" / "extract" / "refine_video.py").read_text(encoding="utf-8")
    check("回抠把同一份时钟传给窗口解码（ts_of=），不然窗口按 pts 算、seek 按平均帧率走",
          "framesource.FfmpegWindows(" in src_rb and "ts_of=" in src_rb)
    check("窗口命令：从 0 起就不 -ss；空区间 / 坏像素格式会响",
          "-ss" not in fs.build_ffmpeg_window_cmd("a.mp4", [(0, 3)], src_fps=30.0)[0]
          and raises(lambda: fs.build_ffmpeg_window_cmd("a.mp4", [], src_fps=30.0), ValueError)
          and raises(lambda: fs.build_ffmpeg_window_cmd("a.mp4", [(0, 1)], src_fps=30.0, pix="rgb"), ValueError))
    check("守卫会响：不认识的像素格式",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", grid=_every(1), start_sec=0,
                                             n_out=1, pix="rgb48"), ValueError))
    check("守卫会响：不认识的解码后端",
          raises(lambda: fs.make_source("a.mp4", backend="vlc", grid=_every(1), start_idx=0,
                                        end_idx=2, src_fps=60, width=8, height=8),
                 ValueError))

    hw = fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4, hwaccel="cuda")
    hs = " ".join(hw)
    check("**hwaccel 要带 -hwaccel_output_format**（不带的话每帧解完都下载回内存，"
          "『跳过的帧不过 PCIe』就不成立）", "-hwaccel_output_format" in hw)
    check("**hwdownload 在 select 之后**（丢弃发生在下载之前才省得下 PCIe）",
          hs.index("select=") < hs.index("hwdownload"))
    check("不给 hwaccel 就一个字都不提（生产路径永远走这条）",
          "hwaccel" not in " ".join(fs.build_ffmpeg_cmd("a.mp4", grid=_every(2),
                                                        start_sec=0, n_out=2)))
    # 有前摇的 av1 的平移修法（decode-buffer §8.6）：两路都要平移，而且平移必须在 select / split 之前
    pr = fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4, hwaccel="cuda",
                             select_by="pts", src_fps=60.0, preroll_ticks=82944)
    ps = " ".join(pr)
    check("前摇平移：-ignore_editlist 在 -i 之前、setpts 在 select 之前（按整数刻度）",
          pr.index("-ignore_editlist") < pr.index("-i") and ps.index("setpts=PTS-82944") < ps.index("select="))
    pd = fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4, hwaccel="cuda", select_by="pts",
                             src_fps=60.0, preroll_ticks=82944,
                             aux={"grid": [1, 1, 1, 30], "scale": 0.35, "n_frames": 120, "url": "tcp://x", "w": 672, "h": 378})
    check("前摇平移：双路时平移在 split 之前（辅路和采样路看同一条时间轴）",
          "[0:v]setpts=PTS-82944,split=2" in " ".join(pd))
    check("前摇平移不给就一个字都不提", "ignore_editlist" not in hs and "setpts" not in hs)
    check("守卫会响：前摇平移配 index 选帧（前摇帧会被数进格子）",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4, hwaccel="cuda",
                                             select_by="index", preroll_ticks=10), ValueError))
    check("守卫会响：前摇平移配非零起点（seek 本来就不少交付，平移反而错）",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=5.0, n_out=4, hwaccel="cuda",
                                             select_by="pts", src_fps=60.0, preroll_ticks=10), ValueError))
    # **探针不许自己拼命令**：它原来那份 `build_cmd` 没有 `-copyts`，
    # 加 `--start` 会原样复活「窗口时间轴平移几小时、没人报错」那个坑。
    probe_src = (ROOT / "dev_tools/probe_ffmpeg_decode.py").read_text(encoding="utf-8")
    check("**probe_ffmpeg_decode 用共享的那份命令**，不自带一份",
          "framesource.build_ffmpeg_cmd" in probe_src
          and "def build_cmd" not in probe_src)
    check("**两个解码探针都有 --start**（不然量的一律是文件头，"
          "而同一个文件里不同段落的解码成本差 2.5×）",
          '"--start"' in probe_src
          and '"--start"' in (ROOT / "dev_tools/probe_hwdecode.py").read_text(encoding="utf-8"))

    check("解析 showinfo 的 pts_time",
          fs.parse_pts_line("[Parsed_showinfo_1 @ 0x1] n:3 pts:600 pts_time:10.5 x") == 10.5)
    check("整数 pts_time 也认", fs.parse_pts_line("pts_time:7 foo") == 7.0)
    check("**不是那种行就返回 None**（别把普通日志当时间戳）",
          fs.parse_pts_line("frame= 60 fps=45 speed=22.1x") is None)

    # 预取：保序 + 生产者的异常必须冒出来
    class Fake:
        def __init__(self, items, boom=False):
            self.items, self.boom = items, boom
            self.stopped_early, self.advanced, self.closed = False, len(items), False
            self.produced = 0            # 真正被生产者取走了几个

        def describe(self):
            return "fake"

        def __iter__(self):
            for it in self.items:
                self.produced += 1
                yield it
            if self.boom:
                raise RuntimeError("解码炸了")

        def close(self):
            self.closed = True

    want = [(i, i / 60, i) for i in range(50)]
    check("**预取严格保序**（复用判断依赖『上一帧』，乱序就全错）",
          list(fs.PrefetchSource(Fake(list(want)), depth=3)) == want)
    check("深度 1 也能跑完", list(fs.PrefetchSource(Fake(list(want)), 1)) == want)
    check("空来源不挂起", list(fs.PrefetchSource(Fake([]), 4)) == [])

    def boom():
        return list(fs.PrefetchSource(Fake(list(want), boom=True), 4))

    check("**生产者线程的异常要冒出来**（吞了就变成『提前没帧了』，"
          "而那和『正常读到头』在下游看起来一模一样）", raises(boom, RuntimeError))
    check("守卫会响：队列深度 < 1",
          raises(lambda: fs.PrefetchSource(Fake([]), 0), ValueError))
    inner = Fake(list(want))
    p = fs.PrefetchSource(inner, 2)
    check("stopped_early / advanced 透传到里层", (p.stopped_early, p.advanced) ==
          (inner.stopped_early, inner.advanced))
    p.close()
    check("close 传到里层（子进程不能漏）", inner.closed)

    # 中途 break：生产者多半正卡在满队列的 q.put 上。close 要先解开它再关里层，
    # 否则**长任务里会漏一个 ffmpeg 在后台读几个小时的素材**。
    inner2 = Fake(list(want))
    p2 = fs.PrefetchSource(inner2, 2)
    for k, _item in enumerate(p2):
        if k == 3:
            break
    p2.close()
    check("**中途 break 之后 close 不挂起**（生产者卡在满队列上也要能收掉）",
          inner2.closed)
    # 2026-09-10：close 以前只排空队列、**不 join**。生产者线程还活着的时候
    # `inner.close()` 就下去了——`Cv2Source` 那条是主线程 `cap.release()` 撞上
    # 生产者 `cap.grab()`，OpenCV 上是未定义行为，可能直接把进程带走，
    # 于是 `bad_pts` 承诺的「退出码 4 + 留产物排查」反而拿不到。
    check("**close 之后生产者线程必须已经退干净**（不能和 inner.close 抢同一个解码器）",
          p2._th is not None and not p2._th.is_alive())
    big = [(i, i / 60, i) for i in range(5000)]
    inner3 = Fake(big)
    p3 = fs.PrefetchSource(inner3, 2)
    for k, _item in enumerate(p3):
        if k == 3:
            break
    p3.close()
    check("**中途 break 之后生产者立刻停，不会一路解码到片尾**"
          f"（取了 {inner3.produced}/5000 个）", inner3.produced < 500)
    check("迭代两次不会吐出上一轮的残渣（每次换新队列）",
          list(fs.PrefetchSource(Fake(list(want)), 2)) ==
          list(fs.PrefetchSource(Fake(list(want)), 2)))
    check("结束标记不是 None（None 是合法的『这一帧没解出来』）",
          fs.QUEUE_SENTINEL is not None)


def t_obs_diff_grid() -> None:
    """`obs_diff` 第 1 层判的是**采样格子**，不是"有框的时刻"（2026-09-10 修）。

    来历：`--decoder` 的 A/B 里，f5 两臂采样格子都是 2,400 帧，只因为 ffmpeg 那边
    多两帧**一个框都没检出**，第 1 层就报"⚠ 采样时刻对不上"并宣布
    **下面两层的数没有意义**——而那两层恰恰是这次要看的东西。
    空帧不写观测行，所以"观测行里出现过的 t_us"天生少于采样帧数。
    """
    print("obs_diff 的采样格子判据")
    import obs_diff as od

    full = {"sampled_frames": 2400}
    ts = list(range(0, 2400 * 500_000, 500_000))          # 每 0.5 s 一帧
    same, lines = od.grid_verdict(full, dict(full), ts, ts)
    check("格子相同、时刻也相同 -> 过", same and "**完全相同**" in lines[0])

    # 乙少两帧"有框的时刻"，但采样格子一样 —— **这一层必须过**
    ts_b = ts[:100] + ts[102:]
    same, lines = od.grid_verdict(full, dict(full), ts, ts_b)
    joined = "\n".join(lines)
    check("**空帧不算格子对不上**（这一层要过，否则下面两层被误判成没意义）", same)
    check("空帧数要报出来", "空帧" in joined)
    check("差异归到第 2 层，而不是宣布下面没意义",
          "第 2 层的检测差异" in joined and "没有意义" not in joined)

    # 真正的格子不同：这才该拦
    same, lines = od.grid_verdict(full, {"sampled_frames": 2398}, ts, ts_b)
    check("**采样帧数不同才算格子对不上**", not same)
    check("拦下来时要说下面两层没意义", "没有意义" in "\n".join(lines))

    # 旧产物没有 sampled_frames：退回旧判据，但要明说是退化的
    same, lines = od.grid_verdict({}, {}, ts, ts_b)
    check("旧产物退回比时刻集合", not same)
    check("**退化判据要自报家门**（别让人以为格子真的不同）",
          "退回" in "\n".join(lines) and "也可能只是空帧不同" in "\n".join(lines))


def t_decoder_default() -> None:
    """默认解码器只有一份常量（owner 2026-09-10：全局统一用 ffmpeg，不按素材分叉）。

    `run_ocr2 --decoder` 的默认值和 `ocr_complete` 的复用判据都要读
    `framesource.DEFAULT_DECODER`。**任何一处写死字面量**，默认一改两边就分家——
    一边产 ffmpeg、一边还拿 cv2 当"已跑完"，正是 owner 说的"不想有的用 cv2 有的用 ffmpeg"。
    静态查，不 import run_ocr2（它顶层拉 cv2 和整个提取包）。
    """
    print("默认解码器只有一份")
    import ast
    from flowocr.extract import framesource as fs

    check("**默认解码器是 ffmpeg**（改它要连 docs/architecture/defaults.md §1.2 一起改）",
          fs.DEFAULT_DECODER == "ffmpeg")
    # 参数定义 2026-09-10 挪进了 tools/ocr_args.py（复用判据要用同一个解析器）
    src = (ROOT / "src/flowocr/extract/ocr_args.py").read_text(encoding="utf-8")
    default = None
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--decoder"):
            for kw in node.keywords:
                if kw.arg == "default":
                    default = ast.unparse(kw.value)
    check("**run_ocr2 的 --decoder 默认值读共享常量**，不写死字面量",
          default == "framesource.DEFAULT_DECODER", f"实际写的是 {default!r}")
    from flowocr.extract import ocr_args
    check("解析出来的默认解码器就是那个常量",
          ocr_args.config_of(["v.mp4", "--out", "x"])["decoder"] == fs.DEFAULT_DECODER)
    oc_src = (ROOT / "src/flowocr/extract/ocr_complete.py").read_text(encoding="utf-8")
    check("复用判据也读同一个常量", "framesource.DEFAULT_DECODER" in oc_src)


def t_backfill_complete() -> None:
    """旧产物的 `_meta.complete` 重判（audit-5 §7）：**值由计数器算，不手填**。"""
    print("backfill_complete")
    import json

    import backfill_complete as bf

    check("容器多报 1%（36001/36361）重判为跑完",
          bf.verdict({"frames_advanced": 36001, "frames_requested": 36361}) is True)
    check("**真中断的照样判没跑完**（14000/36361）",
          bf.verdict({"frames_advanced": 14000, "frames_requested": 36361}) is False)
    check("缺计数器时返回 None——**不猜**",
          bf.verdict({"complete": False}) is None)

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.jsonl"
        body = ['{"t_us": 0, "text": "a"}', '{"t_us": 1, "text": "b"}']
        p.write_text(json.dumps({"_meta": {"complete": False, "frames_advanced": 36001,
                                           "frames_requested": 36361, "wall_sec": 1.5}}) + "\n"
                     + "\n".join(body) + "\n", encoding="utf-8")
        before = p.read_text(encoding="utf-8").splitlines()[1:]
        check("**默认只看不写**", bf.main([str(p)]) == 0
              and json.loads(p.open(encoding="utf-8").readline())["_meta"]["complete"] is False)
        bf.main([str(p), "--write"])
        meta = json.loads(p.open(encoding="utf-8").readline())["_meta"]
        check("--write 才落盘，且留痕", meta["complete"] is True
              and "backfilled" in "".join(meta))
        check("**正文一行都不动**（只换首行）",
              p.read_text(encoding="utf-8").splitlines()[1:] == before)
        check("别的字段不丢", meta["wall_sec"] == 1.5)


def t_predet_stable() -> None:
    """predet 的提前停止：**区域集合缩水不算稳定**（methodology-audit-4 报告 C8）。"""
    print("predet.stable")
    # 判据抽到了 `dev_tools/predet_stop.py`——`predet_scan` 顶层 import cv2 和
    # paddleocr，共享层的自检够不着它，而**够不着就等于测不了**。
    import predet_stop as ps

    def r(x0, y0, x1, y1):
        return {"rect": [x0, y0, x1, y1]}

    five = [r(0, 0, 10, 10), r(20, 0, 30, 10), r(40, 0, 50, 10),
            r(60, 0, 70, 10), r(80, 0, 90, 10)]
    check("同一批区域判稳定", ps.stable(five, list(five)) is True)
    check("**五个缩成一个不算稳定**", ps.stable(five, [five[0]]) is False)
    check("凭空多出一批也不算稳定",
          ps.stable([five[0]], five) is False)
    check("上一轮为空时不算稳定", ps.stable([], five) is False)


def t_same_frame_dedup() -> None:
    """**同一帧里的两个框不能变成两帧的时间支持**（methodology-audit-4 报告 C4）。

    旧写法：新建 run 之后没把它记进 `used`，于是同一帧后面那个重叠/相似的框
    又匹配上它、`n_obs += 1`——一个时刻被记成两次观测。下游拿 `n_obs` 当时间
    支持度，还用它推代表观测时刻去抠模板，会抠到根本没观测过的帧上。
    """
    print("同帧去重")
    from flowocr.analyze import build_tracks as bt

    o = [{"t_us": 0, "box": [100, 100, 200, 130], "text": "hello", "conf": 0.99},
         {"t_us": 0, "box": [102, 101, 203, 131], "text": "hello", "conf": 0.98}]
    runs = bt.build_runs(o, 500_000, 0.35, 0.55, 1)
    check("同帧的两个重叠框不给同一条 run 记两次观测",
          all(r.n_obs == 1 for r in runs), [r.n_obs for r in runs])
    # 跨帧再次看到**才是**真的时间支持
    o2 = [dict(o[0]), dict(o[0], t_us=500_000)]
    runs2 = bt.build_runs(o2, 500_000, 0.35, 0.55, 1)
    check("跨帧再看到照常累加", len(runs2) == 1 and runs2[0].n_obs == 2,
          [(len(runs2), runs2[0].n_obs)])


def t_frame_grid() -> None:
    """采样格：**按什么数**，以及数错了谁来喊（2026-09-20，hw-decode-results §9）。

    病是这样的：`select='not(mod(n,stride))'` 数的是**进到 select 的第几帧**，
    而 NVDEC 在有前摇（首包 pts 为负）的 av1 上会少交付 1~3 帧——于是**整条采样格平移**，
    `frame` 字段和 `t_us` 从此错配，**下游没有任何一层看得出来**。
    真值不是"另一条解码路"，是**容器的 pts**：第 k 格要的就是 `(start_idx + k*stride)/src_fps`。

    两件事钉在这里：`--frame-select pts` 换成按时刻数；`FfmpegSource` 逐帧对一次账。
    """
    print("采样格")
    from flowocr.extract import framesource as fs

    idx = " ".join(fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4))
    pts = " ".join(fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4,
                                       select_by="pts", src_fps=60.0))
    check("默认仍按交付顺序数（index）", "mod(n\\,30)" in idx and "round(t*" not in idx)
    check("pts 那条按 round(t*src_fps) 数", "round(t*60.000000000)" in pts and "mod(n\\," not in pts)
    check("pts 那条挡掉负 pts 的前摇", "gte(t\\,0)" in pts)
    check("pts 选帧没有 src_fps 就报错（不许默默按 0 算）",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4,
                                             select_by="pts")))
    check("不认识的选帧方式报错",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4,
                                             select_by="nearest")))
    # cv2 是自己按 idx 数的，选帧根本不经过 ffmpeg——静默忽略会让 A/B 两条臂塌成一条
    check("cv2 后端拒绝 --frame-select pts（不静默忽略）",
          raises(lambda: fs.make_source("a.mp4", backend="cv2", grid=_every(30), start_idx=0,
                                        end_idx=60, src_fps=60.0, width=8, height=8,
                                        select_by="pts")))

    # 核对那一半（2026-09-20 复审 P1 后重写）：**偏离格子**和**没交付的格**是两件事
    check("grid_step：在格上、没跳格", fs.grid_step(0, 30, 0, _every(30)) == (0, []))
    check("grid_step：素材缺口跳过两格", fs.grid_step(20, 35, 0, _every(5)) == (0, [25, 30]))
    check("grid_step：第一帧就晚一格（gi-s2 硬解的形状）", fs.grid_step(None, 30, 0, _every(30)) == (0, [0]))
    check("grid_step：偏离格子（index 选帧 + 少交付）", fs.grid_step(0, 31, 0, _every(30)) == (1, [30]))
    check("grid_step：非零起点", fs.grid_step(None, 90, 60, _every(30)) == (0, [60]))
    _g8 = fs.TimeGrid(2, 15)                      # 60 fps 上 8 fps：格子 0 8 15 23 30 38 45 …
    check("grid_step：不等距网格——在格上 / 缺口跳格 / 偏离格子",
          fs.grid_step(15, 23, 0, _g8) == (0, []) and fs.grid_step(8, 38, 0, _g8) == (0, [15, 23, 30])
          and fs.grid_step(15, 24, 0, _g8) == (1, [23]))
    check("parse_showinfo 按命名实例分流，带上 showinfo 自己的帧计数 n",
          fs.parse_showinfo("[showinfo@aux @ 0000] n:   3 pts:  3 pts_time:0.3 dur") == ("aux", 3, 0.3)
          and fs.parse_showinfo("[showinfo@main @ 0000] n: 0 pts: 0 pts_time:14843.5 x") == ("main", 0, 14843.5)
          and fs.parse_showinfo("[Parsed_scale_1 @ 0000] w:960") is None)
    # 2026-09-20 晚实测的病：进度行 `frame=… \r` 不换行，下一行 showinfo 粘在它后面
    check("被进度行粘住的 showinfo 行照样认得出（第一版锚行首，丢行 -> 之后整体错开一格）",
          fs.parse_showinfo("frame=   15 fps=0.0 q=-0.0 elapsed=0:00:00.51    \r"
                            "[showinfo@aux @ 0000012e] n: 478 pts: 7 pts_time:7.9667 x") == ("aux", 478, 7.9667))
    check("ffmpeg 命令带 -nostats（从源头关掉进度行）",
          "-nostats" in fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4))
    import inspect as _insp
    check("采样流和辅路都拿 showinfo 的 n 逐帧核对（丢行当场报，不静默错位）",
          "if sn != n:" in _insp.getsource(fs.FfmpegSource) and "if sn != k:" in _insp.getsource(fs.AuxReader))
    _cmd = " ".join(fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4, select_by="pts", src_fps=60.0,
                                        aux={"grid": [1, 2, 1, 30], "scale": 0.5, "n_frames": 60, "url": "tcp://x"}))
    check("两路各挂一个命名 showinfo（校验和关掉）",
          "showinfo@main=checksum=0" in _cmd and "showinfo@aux=checksum=0" in _cmd)
    check("pts 选帧时辅路也按 pts 数、也挡负 pts（两路同一套帧身份）",
          "gte(t\\,0)*not(mod(round(t*60.000000000)\\,2))" in _cmd)
    # 复审第三轮 P1：帧身份的帧率
    check("id_rate：名义帧率合理就用它（f3：60 vs 平均 59.978）", fs.id_rate(60.0, 59.978) == 60.0)
    check("id_rate：合成缺口视频（名义 10 / 平均 8.83）", fs.id_rate(10.0, 53 / 6) == 10.0)
    check("id_rate：名义帧率比平均高出一倍以上（把时间基当帧率报）就不信", fs.id_rate(90000.0, 59.94) == 59.94)
    check("id_rate：探不到名义帧率时退回平均", fs.id_rate(None, 29.97) == 29.97)
    _dup = [round(k / 60 * 59.978) for k in range(0, 6000)]
    check("反例：按平均帧率取整，6000 帧里真的会撞号（这正是复审复现的病）", len(set(_dup)) < len(_dup))
    check("按名义帧率取整：6000 帧一个不撞", len({round(k / 60 * 60.0) for k in range(6000)}) == 6000)
    import inspect as _i3
    check("两路都有碰撞守卫（帧号必须严格递增）",
          "idx <= prev_idx" in _i3.getsource(fs.FfmpegSource) and "key <= prev_key" in _i3.getsource(fs.AuxReader))
    _ro3 = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    check("run_ocr2：ffmpeg 路径帧率走 id_rate（名义帧率）", "framesource.id_rate(" in _ro3)
    check("run_ocr2：--end 0 的窗口尾按时长算，不按帧的个数", "total_frames * src_fps // avg_fps" in _ro3)
    # 复审第三轮 P2：重试由外层进程监督，第一趟退出之后才起第二趟
    # 2026-09-22 监督者搬进 extract/supervisor.py（console script `flowocr-ocr` 要不 import run_ocr2 就能起它）
    _sv = (ROOT / "src/flowocr/extract/supervisor.py").read_text(encoding="utf-8")
    check("监督者在 import 推理库之前就分出去（它自己不占显存）",
          _ro3.index("raise SystemExit(supervisor.supervise())") < _ro3.index("import cv2")
          and not __import__("re").search(r"^\s*(import|from) (cv2|paddle\w*|onnxruntime|numpy)\b", _sv, __import__("re").M))
    check("worker 不再在 except 里同步起子进程（第一版那样两套推理资源同时在卡上）",
          "subprocess.call([sys.executable, *sys.argv" not in _ro3 and "return EXIT_HW_MISMATCH" in _ro3)
    _ro3 = _sv                                 # 下面几条看的都是监督者的代码
    check("每趟结束都终止整个 Job 并等它清零、再关句柄（复审第四轮：原来要等监督者退出才触发）",
          "TerminateJobObject" in _ro3 and "ActiveProcesses" in _ro3 and "CloseHandle" in _ro3)
    check("清不空就不起第二趟（宁可失败，不同时占两套卡）", "if not clean:" in _ro3)
    check("Job 不可用时有进程树兜底（psutil，按 create_time 防 pid 复用）",
          "_track_tree(" in _ro3 and "_drain_tree(" in _ro3 and "create_time" in _ro3)
    # 兜底收尾按 ppid 求闭包（worker 退出前一瞬间起的子进程，轮询记不到）——**行为上核**：子进程起一个孙进程就退，
    # 轮询记录留空，`_drain_tree` 必须顺着 ppid 找到孙进程并杀掉。2026-09-23 起 ppid 全表一次拿（冷扫 1.07 s -> 0.01 s），
    # 原来那条字面匹配 `info["ppid"] in roots` 跟着实现走了，换成这个
    try:
        import psutil as _ps
    except ImportError:
        _ps = None
    if _ps is None:
        print("  （跳过 ppid 闭包的行为核对：这个解释器没有 psutil）")
    else:
        import time

        from flowocr.extract import supervisor as _sup
        _t0 = time.time()
        # 用**基础解释器**起：venv 的 `Scripts\python.exe` 是启动器，会在中间多一层"真 python"，它退了之后 ppid 就接不上了——
        # 这正是"轮询没记到"的情形，生产上靠轮询记下那一层（worker 活得远比 0.5 s 久）。拿 venv 解释器跑守卫时这一条原来必挂
        _py = getattr(sys, "_base_executable", "") or sys.executable
        _pp = subprocess.Popen([_py, "-c",
                                "import subprocess, sys; q = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
                                " print(q.pid, flush=True)"], stdout=subprocess.PIPE, text=True)
        _gc = int(_pp.stdout.readline())
        _pp.wait()
        _clean = _sup._drain_tree({}, _pp.pid, _t0)
        check("兜底收尾按 ppid 求闭包：父进程已退、轮询没记到的孙进程也找得到、杀得掉",
              _clean and not _ps.pid_exists(_gc), (_clean, _gc))
    check("Job 在也按进程树收尾（venv 启动器会让孙进程静默逃出 Job，契约测试跑出来的）",
          "clean = _drain_tree(seen, p.pid, t_start) and clean_job" in _ro3)
    check("监督者用 Job Object 绑住 worker（被杀时连 ffmpeg / ORT 服务一起收；不用 stdin 看门狗——Windows 上会死锁）",
          "_kill_on_close_job()" in _ro3 and "AssignProcessToJobObject" in _ro3
          and "sys.stdin.buffer.read()" not in _ro3)
    from flowocr.extract import ocr_args as _oa3
    check("帧身份口径进 config、且不在 ADDED_NOOP（平均帧率那一版的产物不复用）",
          _oa3.config_of(["v.mp4", "--out", "x.jsonl"]).get("frame_id") == "nominal"
          and "frame_id" not in _oa3.ADDED_NOOP
          and "frame_id" not in _oa3.config_of(["v.mp4", "--out", "x.jsonl", "--decoder", "cv2"]))
    check("probe_interval：起点 ≤ 0 时从头读（首包 pts 为负的文件上 `0%1` 一个包都读不出来）",
          fs.probe_interval(-1.0, 1.0) == "%1.000000" and fs.probe_interval(0.0, 1.0) == "%1.000000"
          and fs.probe_interval(58.0, 61.0) == "58.000000%61.000000")
    check("source_has_frames：探不出来按「源里有」算（不许读成合法缺口）",
          fs.source_has_frames("不存在的文件.mp4", [30, 60], 60.0) == [30, 60])
    check("格子上的帧：偏移 0", fs.grid_skew_of(0.5, 30, 60.0) == 0)
    check("少交付 1 帧：pts 比帧号晚一格", fs.grid_skew_of(0.5 + 1 / 60, 30, 60.0) == 1)
    check("少交付 3 帧（gi-s2 实测的那个数）", fs.grid_skew_of(0.5 + 3 / 60, 30, 60.0) == 3)
    check("整格丢掉（pts 选帧下硬解的形态）", fs.grid_skew_of(1.0, 30, 60.0) == 30)
    check("非整数帧率上仍按最近的源帧数", fs.grid_skew_of(30 / 59.978, 30, 59.978) == 0)
    # **写出去的帧号认 pts**（2026-09-20 修，owner："是 bug 就直接修掉"）：
    # 09-09 把 `t_us` 改成了真实 pts，`frame` 当时**没跟着改**，于是在有缺口的素材上
    # `frame` 是"第几个采样帧 × stride + 起点"而不是源帧号（f3 跨缺口的窗上差 309~330 帧）。
    # 实测：修前 606 行里 335 行的 `frame/src_fps` 和 `t_us` 对不上，修后 0 行。
    for t, want_idx in ((0.5, 30), (0.5 + 3 / 60, 30), (26349.633, 1580070)):
        got = want_idx + fs.grid_skew_of(t, want_idx, 59.97819708554055 if t > 1 else 60.0)
        fps = 59.97819708554055 if t > 1 else 60.0
        check(f"帧号认 pts：t={t} -> {got}", got == round(t * fps), (got, round(t * fps)))
    # **写 obs 行的地方有两处**（run_ocr2 的主路 + reuse_v2 的延迟落盘，而 `--reuse-v2` 是默认）。
    # 2026-09-20 第一版只改了主路：缺口之后一半的行还是旧编号，552 行里 281 行对不上，
    # **而没有任何一层报错**。这条守卫钉的就是"别再只改一处"。
    from pathlib import Path as _P
    _root = ROOT
    for _f in ("src/flowocr/extract/run_ocr2.py", "src/flowocr/extract/reuse_v2.py"):
        _src = (_root / _f).read_text(encoding="utf-8")
        check(f"{_f} 写 frame 走 framesource.pts_frame", "framesource.pts_frame(" in _src)
        check(f"{_f} 里没有裸的 \"frame\": idx", '"frame": idx' not in _src)
    check("pts_frame 就是 idx + grid_skew_of（判据只有一份）",
          all(fs.pts_frame(int(t * 1e6), i, f) == i + fs.grid_skew_of(t, i, f)
              for t, i, f in ((0.5, 30, 60.0), (26350.083, 1580100, 59.97819708554055))))

    import inspect
    check("cv2 那条路也按 pts 编号、也记 grid_off（两条来源同一套口径）",
          "grid_off" in inspect.getsource(fs.Cv2Source) and "round(t * self.src_fps)" in inspect.getsource(fs.Cv2Source))
    check("FfmpegSource yield 的就是 pts 帧号（不是『第几个采样帧 × stride』那第三种编号）",
          "idx = round(float(t) * self.src_fps)" in inspect.getsource(fs.FfmpegSource))
    check("AuxReader 按 pts 编号（和采样帧同一套身份）",
          "key = round(t * self.src_fps)" in inspect.getsource(fs.AuxReader))
    _ro2 = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    _sv2 = (ROOT / "src/flowocr/extract/supervisor.py").read_text(encoding="utf-8")
    check("硬解中途交付不对：worker 退出码 75，外层监督者等它退出后再起软解那一趟",
          "except framesource.HwaccelMismatch" in _ro2 and '"--hwaccel", ""' in _sv2
          and "rc == EXIT_HW_MISMATCH" in _sv2 and "raise SystemExit(worker_main())" in _ro2)
    check("硬解判据只在源里真有那一帧时才算错（素材缺口放行）",
          "source_has_frames(" in inspect.getsource(fs.FfmpegSource))
    check("HwaccelMismatch 是 RuntimeError（hwaccel_blocker 的 except 接得住，开头试解才退得回软解）",
          issubclass(fs.HwaccelMismatch, RuntimeError))
    # 辅助流跟着硬解一起走（2026-09-20）：辅路在显存里缩完再下传，不再拒绝这个组合
    hw_aux = " ".join(fs.build_ffmpeg_cmd(
        "a.mp4", grid=_every(30), start_sec=0, n_out=4, hwaccel="cuda",
        aux={"grid": [1, 1, 1, 30], "scale": 0.5, "n_frames": 120, "url": "tcp://x", "w": 960, "h": 540}))
    check("硬解的辅路在 GPU 上缩（scale_cuda），不是 CPU 的 scale",
          "scale_cuda=960:540" in hw_aux and "scale=trunc" not in hw_aux)
    check("硬解的辅路缩完才 hwdownload（过 PCIe 的是 540p 不是 1080p）",
          hw_aux.index("scale_cuda=960:540") < hw_aux.index("hwdownload,format=nv12,format=gray"))
    sw_aux = " ".join(fs.build_ffmpeg_cmd(
        "a.mp4", grid=_every(30), start_sec=0, n_out=4,
        aux={"grid": [1, 1, 1, 30], "scale": 0.5, "n_frames": 120, "url": "tcp://x", "w": 960, "h": 540}))
    check("软解那条还是 CPU 的 scale（没被带跑）",
          "scale=trunc" in sw_aux and "scale_cuda" not in sw_aux)
    from flowocr.extract import ocr_args as _oa
    check("`--hwaccel` 不再自动关掉两遍合一遍",
          _oa.config_of(["v.mp4", "--out", "x.jsonl", "--hwaccel", "cuda"]).get("refine_fused") is True)
    # 2026-09-20 owner 定翻默认：**理由是 CPU 不是墙钟**（319 -> 189 CPU 秒 = −41%，墙钟只 −4%）
    _base = ["v.mp4", "--out", "x.jsonl"]
    check("`--hwaccel` 默认 cuda", _oa.config_of(_base).get("hwaccel") == "cuda")
    # ⚠ 当天放进 SCHEDULING、又拿出来了（Codex 复审 P2 #3）：等价只在 nv12 采样帧上证过，
    # bgr24 下像素本来就不同、辅路缩放器不同时 `edge` 也没逐条比过
    check("`--hwaccel` 不在 SCHEDULING：软硬解的 obs 互相不复用",
          "hwaccel" not in _oa.SCHEDULING
          and _oa.config_differs(_oa.config_of(_base + ["--hwaccel", ""]), _oa.config_of(_base)) == ["hwaccel"])
    check("bgr24 下切软硬解同样不复用（复审的反例）",
          _oa.config_differs(_oa.config_of(_base + ["--pipe-pix", "bgr24", "--hwaccel", ""]),
                             _oa.config_of(_base + ["--pipe-pix", "bgr24"])) == ["hwaccel"])
    check("但软解臂仍分得开（arm_check 拦得住）",
          not _oa.same_arm(_base, _base + ["--hwaccel", ""]))
    # **回退路径不许被新默认咬断**（2026-09-20 整体检查抓到的）：`--hwaccel` / `--frame-select`
    # 都只有 ffmpeg 后端认，翻默认那天写死成 `cuda` / `pts`，于是当时文档里写着的
    # `--decoder cv2` 一跑就是"--hwaccel 要 --decoder ffmpeg"。现在两个默认跟着解码器走
    _cv2 = _oa.config_of(_base + ["--decoder", "cv2"])
    check("`--decoder cv2` 不给别的：硬解自动为空（不进 config）", "hwaccel" not in _cv2, _cv2.get("hwaccel"))
    check("`--decoder cv2` 不给别的：选帧自动取 index", _cv2["frame_select"] == "index")
    check("ffmpeg 默认仍是 cuda + pts",
          _oa.config_of(_base).get("hwaccel") == "cuda" and _oa.config_of(_base)["frame_select"] == "pts")
    check("显式冲突（cv2 + --hwaccel cuda）不被悄悄改掉——留给 run_ocr2 报错",
          _oa.config_of(_base + ["--decoder", "cv2", "--hwaccel", "cuda"]).get("hwaccel") == "cuda")
    # 并行拼 `_meta` 从父进程那份起步，而父进程不做试解——子进程的生效值和格子核对不能丢
    from flowocr.extract import ocr_parallel as _op
    _ms = [{"hwaccel_effective": "", "grid_off": {"3": 2}, "grid_skipped": 1}, {"hwaccel_effective": "cuda"},
           {"hwaccel_effective": "cuda", "grid_off": {"3": 1}, "grid_skipped": 2}]
    for _x in _ms:
        _x.update(frames_advanced=10, frames_requested=10, complete=True, start_sec=0, end_sec=1)
    _m = _op.merge_metas(_ms, {}, wall=1.0, cuts_sec=[0.3, 0.6], interval=0.5)
    check("并行拼接：各段硬解生效值不一致时如实写 mixed", _m.get("hwaccel_effective") == "mixed:soft,cuda,cuda",
          _m.get("hwaccel_effective"))
    check("并行拼接：grid_off 各段逐键相加、grid_skipped 相加",
          _m.get("grid_off") == {"3": 3} and _m.get("grid_skipped") == 3, (_m.get("grid_off"), _m.get("grid_skipped")))
    # 开不了的素材要**退回去**，不是整段跑不了（同 `--workers` 的老规矩）
    check("有试解这道预检", callable(getattr(fs, "hwaccel_blocker", None)))
    _ro = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    check("run_ocr2 真的接了预检（2026-09-23 起在后台和建模型并行跑、建取帧层之前取结论）+ 记生效值",
          "framesource.hwaccel_blocker" in _ro and "hw_probe.result()" in _ro and 'meta["hwaccel_effective"]' in _ro)

    check("预取包在外面时 grid_off / grid_skipped 透得出来",
          fs.PrefetchSource.grid_off.fget(
              type("X", (), {"inner": type("Y", (), {"grid_off": {3: 7}})()})()) == {3: 7}
          and fs.PrefetchSource.grid_skipped.fget(
              type("X", (), {"inner": type("Y", (), {"grid_skipped": 4})()})()) == 4)
    check("没有这一项的来源（cv2）也不炸",
          fs.PrefetchSource.grid_off.fget(type("X", (), {"inner": object()})()) == {})


def t_accel_defaults() -> None:
    """加速开关翻默认之后的三条不变量（2026-09-20，acceleration-rollout 计划步 1）。

    翻的是两个：`--rec-engine ort`（**改产物**）和 `--det-prefetch 2`（**产物逐字节相同**）。
    这两件事对"要不要重建现成 obs"的答案**相反**，所以要分别钉住：

    1. 改产物的那个**必须**让老产物重建——否则两条路的观测会混进同一个文件；
    2. 不改产物的那个**必须不**触发重建——`--det-prefetch` 09-20 之前在复用判据里，
       默认一翻就会让磁盘上所有现成 obs（含整片）白白重跑一遍；
    3. 但它**仍要能分出两条臂**——它是这一轮最大的一笔加速（−15%~−25%），
       量不了就等于没有。`NON_PRODUCT` 做不到第 3 条，所以才有 `SCHEDULING`。
    """
    print("加速默认值")
    from flowocr.extract import ocr_args as oa

    base = ["v.mp4", "--out", "x.jsonl"]
    cur = oa.config_of(base)
    # 2026-10 运行时去掉 Paddle：rec / det 引擎不再是选项，config 里固定写身份串 "ort"（复用判据靠它拦旧的 Paddle 产物）
    check("rec / det 引擎是固定的身份串 ort（不随任何参数变）",
          cur["rec_engine"] == cur["det_engine"] == "ort"
          and oa.config_of(base + ["--det-onnx", "m.onnx", "--device", "cpu"])["det_engine"] == "ort", (cur["rec_engine"], cur["det_engine"]))
    import contextlib as _cl
    import io as _io
    _gone = []
    for _opt in (["--det-engine", "ort"], ["--rec-engine", "ort"], ["--no-fast-det"], ["--rec-batch-size", "8"],
                 ["--rec-model", "PP-OCRv6_medium_rec"], ["--det-model", "PP-OCRv6_medium_det"]):
        try:
            with _cl.redirect_stderr(_io.StringIO()):
                oa.parse_args(base + _opt)
        except SystemExit:
            _gone.append(_opt[0])
    check("删掉的 6 个选项给了就报 argparse 未知参数（不另做兼容）", len(_gone) == 6, _gone)
    # 换了 det 模型（2026-09-24 Codex 审计 P1）：复用判据不许把两种后端当噪音级——旧产物里记着 det_model
    _mob = dict(cur, det_engine="paddle", det_model="PP-OCRv5_mobile_det")
    check("**两种 det 的产物只在验证过的模型对上按噪音级复用**：默认模型可以、换了模型的不行",
          "det_engine" not in oa.NOISE_EQUIV
          and oa.config_differs(dict(cur, det_engine="paddle"), cur) == []
          and oa.config_differs(_mob, cur) == ["det_engine", "det_model"]
          and oa.config_differs(dict(cur, det_engine="paddle", det_onnx="my.onnx"), dict(cur, det_onnx="my.onnx")) == ["det_engine"],
          oa.config_differs(_mob, cur))
    check("det 预取默认 2", cur["det_prefetch"] == 2, cur["det_prefetch"])

    # 2：调度类旋钮不进复用判据
    off = oa.config_of(base + ["--det-prefetch", "0"])
    check("换 det 预取不算配置对不上", oa.config_differs(cur, off) == [],
          oa.config_differs(cur, off))
    # 3：但它分得开两条臂（否则 ocr_ab.sh 的 arm_check 会把 B / D 判成同一条）
    check("换 det 预取仍是两条臂", not oa.same_arm(base, base + ["--det-prefetch", "0"]))

    # 1：改产物的那个照旧拦（老产物 = 09-18 之前的 Paddle-rec 产物，没有这些键）
    old = {k: v for k, v in oa.config_of(base).items()
           if k not in ("rec_engine", "det_engine", "rec_onnx", "det_onnx", "det_prefetch",
                        "ort_share", "ort_max_sessions", "ort_share_mem", "ort_batch_ladder", "pregate",
                        "rec_async", "rec_knee", "rec_near", "rec_window")}
    # 2026-09-23 翻的两批默认：`--ort-share-mem`（池子跟着到 64；文本 0 行改动 -> owner 定噪音级，进 NOISE_EQUIV、不重建）
    # 和异步 rec（`--rec-window 4 --rec-async --rec-knee 1`，改读哪一帧，照规矩重建）
    want_diff = ["rec_async", "rec_engine", "rec_knee", "rec_window"]
    check("老产物对上现默认 -> rec_engine、异步 rec 这几个键不同（要重建），ORT 资源类的不算",
          oa.config_differs(old, cur) == want_diff, oa.config_differs(old, cur))
    check("老产物对上其余几个的回退臂 -> 只剩 rec_engine（Paddle rec 的回退臂 2026-10 随 Paddle 一起去掉，这批旧产物该重建）",
          oa.config_differs(old, oa.config_of(base + ["--no-ort-share-mem", "--rec-window", "0", "--rec-knee", "16"])) == ["rec_engine"])

    # SCHEDULING 的门槛：只放"取任何值都不改产物"的键，别当成第二个 ADDED_NOOP
    check("SCHEDULING 的成员是白名单，不许随手加",
          oa.SCHEDULING == frozenset({"det_prefetch", "edge_proc", "decode_shards", "decode_block", "front_proc", "det_proc",
                                      "det_lookahead"}),
          sorted(oa.SCHEDULING))   # det_proc / decode_shards：gi-s1 60 s 确定性路径 obs_identical 逐字节相同（decode-buffer §8.22 / §8.24）；
                                   # det_lookahead：同一段同一路径 0 / 1 两趟 obs_identical（2026-09-25，tmp/tl/detlook0925）
    check("SCHEDULING 和 NON_PRODUCT 不重叠（重叠了就分不出臂）",
          not (oa.SCHEDULING & oa.NON_PRODUCT))
    check("det_prefetch 已从 ADDED_NOOP 挪走（留着会让人以为只有 0 才赦免）",
          "det_prefetch" not in oa.ADDED_NOOP)


def t_model_paths() -> None:
    """模型 / 缓存 / ORT 解释器怎么找（正规化阶段 D：一个 venv 的 CPU 路径，发行包里没有 `explore/`）。"""
    print("模型路径与 ORT 解释器")
    import os

    import flowocr.paths as fp
    from flowocr.extract import ortclient

    saved = {k: os.environ.get(k) for k in ("FLOWOCR_MODELS", "FLOWOCR_DATA_ROOT")}
    saved_cwd = os.getcwd()
    # 旧布局的相对路径（老 obs 的 config 里记的就是这种），拿来测"按文件名找"。拆开写是给发布检查看的：这不是指向私有目录的指针
    _LEGACY = "explore" + "/"
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        try:
            # 下面几条都假设"相对路径在当前目录下不存在"——在一个空目录里跑（在主 checkout = 数据根里跑时，
            # 那些相对路径可能真的在，resolve_model 原样返回，这条就假失败了；2026-09-22 合并后实测）
            (d / "cwd").mkdir()
            os.chdir(d / "cwd")
            os.environ["FLOWOCR_MODELS"] = str(d / "m")
            (d / "m").mkdir()
            (d / "m" / "x.onnx").write_bytes(b"0")
            check("FLOWOCR_MODELS 覆盖模型根", fp.models_root() == (d / "m").resolve())
            check("resolve_model：相对路径当前目录找不到 -> 按文件名到模型根找",
                  fp.resolve_model(_LEGACY + "onnxrt/models/x.onnx") == str((d / "m").resolve() / "x.onnx"))
            check("resolve_model：存在的路径原样返回；都找不到也原样返回（让打开它的那步报清楚）",
                  fp.resolve_model(str(d / "m" / "x.onnx")) == str(d / "m" / "x.onnx")
                  and fp.resolve_model(_LEGACY + "onnxrt/models/nope.onnx") == _LEGACY + "onnxrt/models/nope.onnx")
        finally:
            os.chdir(saved_cwd)
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    check("源码态：模型根是数据根的 models/（和安装态同一个位置，不依赖随时可删的 `explore/`）",
          fp.models_root() == fp.data_root() / "models")
    # 官方 ONNX 模型（owner 2026-09-22：全用官方的）：钉提交号 + sha256，rec 融 argmax 只加节点
    from flowocr import models as FM
    check("官方模型表：提交号是 40 位、每个文件都钉了 sha256、rec 融 argmax、det 不融",
          all(len(m.rev) == 40 and all(len(s) == 64 for s in m.files.values()) for m in FM.MODELS.values())
          and FM.MODELS["rec"].fuse_argmax and not FM.MODELS["det"].fuse_argmax
          and FM.MODELS["rec"].repo == "PaddlePaddle/PP-OCRv6_medium_rec_onnx")
    check("默认配置要的模型是 rec + det（D1 翻默认后 det 也走 ORT；断网时 fetch 过默认就够跑默认配置）",
          FM.DEFAULT == ("rec", "det"))
    from flowocr.extract import ocr_args as _oa_m
    _mc = _oa_m.config_of(["v.mp4", "--out", "o"])
    check("--rec-onnx / --det-onnx 默认空 = 官方默认，不再指 onnxrt 实验目录下的 models/",
          _mc["rec_onnx"] == "" and _mc["det_onnx"] == "")
    check("owner 认可的那一批新旧模型互相复用（旧自转默认 <-> 官方默认），不写这两个键的老产物也算",
          _oa_m.config_differs({**_mc, "rec_onnx": _oa_m.OLD_REC_ONNX, "det_onnx": _oa_m.OLD_DET_ONNX}, _mc) == []
          and _oa_m.config_differs({k: v for k, v in _mc.items() if k not in ("rec_onnx", "det_onnx")}, _mc) == [])
    check("**换成别的自定义模型必须重建**（Codex 复审 P2：原来整个键豁免，换任意模型都照样复用）",
          _oa_m.config_differs({**_mc, "rec_onnx": "recognizer-a.onnx"}, {**_mc, "rec_onnx": "completely-different-b.onnx"})
          == ["rec_onnx"]
          and _oa_m.config_differs(_mc, {**_mc, "det_onnx": "mine.onnx"}) == ["det_onnx"]
          and "rec_onnx" not in _oa_m.NOISE_EQUIV and "det_onnx" not in _oa_m.NOISE_EQUIV
          and not _oa_m.same_arm(["v.mp4", "--out", "a"], ["v.mp4", "--out", "b", "--rec-onnx", "x.onnx"]))
    # 源文件换了（表里换提交号 / 重新下载），派生的 argmax 图必须重做（Codex 复审：原来只看派生文件在不在）——全离线造一个小模型走 ensure
    _saved_mr = os.environ.get("FLOWOCR_MODELS")
    try:
        import onnx  # noqa: F401
        from onnx import TensorProto, helper
        with tempfile.TemporaryDirectory() as _td:
            os.environ["FLOWOCR_MODELS"] = _td
            def _tiny(op: str, p: Path) -> str:
                g = helper.make_graph([helper.make_node(op, ["x"], ["y"])], "g",
                                      [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["b", "t", 5])],
                                      [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["b", "t", 5])])
                onnx.save(helper.make_model(g), str(p))
                return FM.sha256(p)
            _m1 = FM.Model("t/fake_rec", "0" * 40, {}, fuse_argmax=True)
            _m1.dir.mkdir(parents=True)
            _s1 = _tiny("Identity", _m1.dir / "inference.onnx")
            _m1 = FM.Model("t/fake_rec", "0" * 40, {"inference.onnx": _s1}, fuse_argmax=True)
            FM.ensure(_m1)
            _first = [n.op_type for n in onnx.load(str(_m1.onnx)).graph.node]
            _s2 = _tiny("Relu", _m1.dir / "inference.onnx")               # "上游更新了"：源文件换了，表里钉的 sha 跟着换
            _m2 = FM.Model("t/fake_rec", "1" * 40, {"inference.onnx": _s2}, fuse_argmax=True)
            _stale = FM.derived_stale(_m2)
            FM.ensure(_m2)
            _second = [n.op_type for n in onnx.load(str(_m2.onnx)).graph.node]
            check("源文件换了派生的 argmax 图就重做（按来源 sha 判，不按文件在不在）",
                  _first == ["Identity", "ArgMax", "ReduceMax"] and _stale and not FM.derived_stale(_m2)
                  and _second == ["Relu", "ArgMax", "ReduceMax"], (_first, _stale, _second))
    except ImportError:
        print("  SKIP  派生图过期（这个解释器没装 onnx）")
    finally:
        if _saved_mr is None:
            os.environ.pop("FLOWOCR_MODELS", None)
        else:
            os.environ["FLOWOCR_MODELS"] = _saved_mr
    try:
        import onnx  # noqa: F401
        from onnx import TensorProto, helper
        with tempfile.TemporaryDirectory() as _td:
            _td = Path(_td)
            _g = helper.make_graph([helper.make_node("Identity", ["x"], ["y"])], "g",
                                   [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["b", "t", 5])],
                                   [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["b", "t", 5])])
            onnx.save(helper.make_model(_g), str(_td / "a.onnx"))
            FM.fuse_argmax(_td / "a.onnx", _td / "b.onnx")
            _m = onnx.load(str(_td / "b.onnx"))
            check("fuse_argmax：输出换成 cls(int64) + prob，原有节点一个不动、只多两个",
                  [o.name for o in _m.graph.output] == ["cls", "prob"]
                  and _m.graph.output[0].type.tensor_type.elem_type == TensorProto.INT64
                  and [n.op_type for n in _m.graph.node] == ["Identity", "ArgMax", "ReduceMax"])
    except ImportError:
        print("  SKIP  fuse_argmax（这个解释器没装 onnx）")
    # ORT worker 的解释器：当前解释器装了 onnxruntime 就用自己，否则 onnxrt 实验目录 那个 venv（生产 venv 故意不装）
    orig = ortclient._has_onnxruntime
    try:
        ortclient._has_onnxruntime = lambda: True
        check("装了 onnxruntime -> ORT worker 用当前解释器", ortclient.server_python() == sys.executable)
        ortclient._has_onnxruntime = lambda: False
        check("没装 -> 明确报错（不再退回 onnxrt 实验目录 的 venv：生产代码不依赖可整目录删的 `explore/`）",
              raises(ortclient.server_python, SystemExit))
        os.environ["FLOWOCR_ORT_PYTHON"] = "X:/other/python.exe"
        ortclient._has_onnxruntime = lambda: True
        check("FLOWOCR_ORT_PYTHON 覆盖一切（A/B 换 ORT 版本时一次只变一件事）", ortclient.server_python() == "X:/other/python.exe")
    finally:
        ortclient._has_onnxruntime = orig
        os.environ.pop("FLOWOCR_ORT_PYTHON", None)
    src = (ROOT / "src" / "flowocr" / "extract" / "ort_server.py").read_text(encoding="utf-8")
    check("ort_server：有 CUDA EP 却回落到 CPU 仍然退出码 3（不静默跑 CPU），只有 CPU 包才走 CPU",
          "cuda_ok and \"CUDA\" not in prov" in src and "return 3" in src and '"CPUExecutionProvider"' in src)
    # 2026-09-22 ORT 1.26 + cuDNN 9.9 在 sm_120 上：EXHAUSTIVE 搜索对 fp16 卷积没有可用引擎，ORT 的 python 封装会静默重建成 CPU session；
    # DEFAULT 能跑但慢 25 倍，所以默认**不能**是 DEFAULT
    check("ort_server：cuDNN 卷积搜索可开臂、默认仍是 EXHAUSTIVE（DEFAULT 慢 25 倍），且关掉 ORT 的静默 CPU 回落",
          'default="EXHAUSTIVE"' in src and '"cudnn_conv_algo_search": a.cudnn_conv_search' in src and "s.disable_fallback()" in src)
    check("ort_server：stdin 模式下推理抛错回 error 行给调用方，不带着 traceback 死在 DEVNULL 里",
          '{"error": repr(e)[:2000]}' in src)
    src3 = (ROOT / "src" / "flowocr" / "extract" / "ortclient.py").read_text(encoding="utf-8")
    check("ortclient：worker 起不来时报错带它的 stderr 尾部（stderr 落临时文件，不再 DEVNULL）",
          "stderr=self._err" in src3 and "def _stderr_tail" in src3 and "stderr=subprocess.DEVNULL" not in src3)
    # --rec-inflight 的旁路连接（decode-buffer §8.18）：**行为上核**——CPU 上起一个真服务（管道模式 + --side-listen），
    # 从旁路再接一条，两条连接同时跑同一个形状（服务端普通 session 不加锁），结果和串行相同
    _nc = ortclient.OrtClient.__new__(ortclient.OrtClient)
    _nc.side_addr = ""
    try:
        _nc.side_channel()
        _err = "没抛"
    except RuntimeError as exc:
        _err = str(exc)
    check("没开旁路端口时 side_channel() 直接抛（不退回自己起一个 worker——那是一整份模型和显存）", "--side-listen" in _err, _err)
    from flowocr import models as _fm
    if not _fm.MODELS["rec"].onnx.is_file() or not ortclient._has_onnxruntime():
        print("  （跳过旁路连接的行为核对：没有 rec 模型或 onnxruntime）")
    else:
        import threading as _th

        import numpy as _np
        _cli = ortclient.OrtClient({"rec": str(_fm.MODELS["rec"].onnx)}, device="cpu", extra_args=["--side-listen"])
        try:
            _side = _cli.side_channel()
            _x = [_np.random.default_rng(k).standard_normal((2, 3, 48, 320)).astype(_np.float32) for k in (0, 1)]
            _ref = [_cli.run("rec", x) for x in _x]
            _got: list = [None, None]

            def _go(k, c):
                _got[k] = c.run("rec", _x[k])
            _ts = [_th.Thread(target=_go, args=(0, _cli)), _th.Thread(target=_go, args=(1, _side))]
            [t.start() for t in _ts]
            [t.join() for t in _ts]
            _same = all(_got[k] is not None and _np.array_equal(_ref[k][0], _got[k][0])
                        and _np.allclose(_ref[k][1], _got[k][1], atol=1e-5) for k in (0, 1))
            _side.close()
        finally:
            _cli.close()
        check("旁路连接：同一个服务上两条连接同时推理，结果和串行相同（cls 逐位、prob 1e-5 内）", _same)
    src2 = (ROOT / "src" / "flowocr" / "extract" / "run_ocr2.py").read_text(encoding="utf-8")
    # child_env：源码态把 src/ 放进 PYTHONPATH 最前面；安装态什么都不加（site-packages 进 PYTHONPATH 会泄漏给别的解释器起的子进程，
    # 2026-09-22 nightly：FLOWOCR_ORT_PYTHON 钉到 1.29 的 venv，worker 却 import 到了本 venv 的 ORT 1.26 + cu12 库）
    check("child_env：源码态 src/ 在 PYTHONPATH 最前面",
          fp.child_env({"PYTHONPATH": "x"})["PYTHONPATH"].split(os.pathsep)[:2] == [str(fp.PACKAGE_ROOT.parent), "x"])
    _ic = fp.is_checkout
    try:
        fp.is_checkout = lambda root=None: False
        check("child_env：安装态不动 PYTHONPATH", fp.child_env({"PYTHONPATH": "x"}) == {"PYTHONPATH": "x"})
    finally:
        fp.is_checkout = _ic
    # ---- 设备：`--device` 必须管住**两个**后端（Codex 审计 P1：原来只有 Paddle 听它，ORT rec 照样上 GPU）----
    import importlib
    ro = importlib.import_module("flowocr.extract.ocr_args")   # 设备映射在共享层（这里不 import run_ocr2：它顶层拉 cv2 和整个提取包）
    check("--device -> ORT worker 的设备：gpu -> gpu:0，**gpu:N 的卡号带过去**（复审 P2），cpu -> cpu",
          (ro.ort_device("gpu"), ro.ort_device("gpu:0"), ro.ort_device("gpu:2"), ro.ort_device("cpu"))
          == ("gpu:0", "gpu:0", "gpu:2", "cpu"))
    import ast as _ast
    _srv_src = (ROOT / "src" / "flowocr" / "extract" / "ort_server.py").read_text(encoding="utf-8")
    _fn = next(n for n in _ast.parse(_srv_src).body if isinstance(n, _ast.FunctionDef) and n.name == "norm_device")
    _ns: dict = {}
    exec(compile(_ast.Module([_fn], []), "ort_server.norm_device", "exec"), _ns)   # 不 import ort_server：它顶层拉 onnxruntime
    check("ort_server 的设备口径和 ocr_args.ort_device 逐个一致（握手按字符串比，两份必须同口径）",
          all(_ns["norm_device"](x) == ro.ort_device(x) for x in ("cpu", "gpu", "gpu:0", "gpu:3", "GPU:1")))
    check("ORT 的 CUDA EP 按卡号建（device_id 跟着 --device），握手按归一后的设备比",
          'cuda_opts["device_id"] = int(a.device.split(":")[1])' in _srv_src
          and 'norm_device(h["device"]) != hello.get("device")' in _srv_src)
    check("要的卡不在就在便宜检查里拦（gpu:N 超出可见卡数）", "只看得到" in src2 and "gpu_blocker(args.device" in src2)
    check("认不出的 --device 解析时就报错（别静默按 GPU 跑）",
          raises(lambda: ro.config_of(["v.mp4", "--out", "o", "--device", "cuda"]), SystemExit)
          and raises(lambda: ro.check_device("gpu:x"), SystemExit))
    check("`auto` 不许直接喂给 ORT worker（要先试推理解析）", raises(lambda: ro.ort_device("auto"), ValueError))
    _cfg_auto = ro.config_of(["v.mp4", "--out", "o"])
    check("--device 默认 auto（计划 §7.1：不支持的卡回退 CPU），config 记请求的值",
          _cfg_auto["device"] == "auto" and ro.config_of(["v.mp4", "--out", "o", "--device", "GPU"])["device"] == "gpu")
    check("GPU / CPU 的产物互相复用（owner 2026-09-22 定噪音级，NOISE_EQUIV）：device 不同不算对不上",
          ro.config_differs({**_cfg_auto, "device": "gpu"}, _cfg_auto) == []
          and ro.config_differs({**_cfg_auto, "device": "cpu"}, {**_cfg_auto, "device": "gpu"}) == [])
    _res = {"ort_share_mem": False, "ort_max_sessions": 12, "rec_pre": "client", "ort_share": False}
    check("ORT 资源类旋钮互相复用（owner 2026-09-23：噪音级都复用）；改读法的 dw-anchor 照旧拦",
          ro.config_differs({**_cfg_auto, **_res}, _cfg_auto) == []
          and ro.config_differs({**_cfg_auto, "reuse_dw_anchor": False}, _cfg_auto) == ["reuse_dw_anchor"]
          and not (set(_res) & set(ro.ADDED_NOOP))
          and ro.config_differs({**_cfg_auto, "ort_cuda_graph": 4}, _cfg_auto) == ["ort_cuda_graph"])
    check("……但分得开 A/B 臂（写进 config，same_arm 不把 CPU 臂当成默认臂）",
          not ro.same_arm(["v.mp4", "--out", "a"], ["v.mp4", "--out", "b", "--device", "cpu"])
          and "device" not in ro.NON_PRODUCT and "device" not in ro.SCHEDULING)
    check("三处起 ORT 的地方都把**解析过的**设备传下去（rec / det 的 client + 多 worker 的共享服务）",
          src2.count("device=ort_device(device)") == 2 and '"--device", srv_dev' in src2
          and "srv_dev = ort_device(args.device if args.device != \"auto\"" in src2)   # 共享服务那处先判过 auto
    check("auto：便宜检查 -> GPU 上建模型 + 试推理；只有『GPU 不可用』那几类才退回 CPU，其余报错（release-plan F5）；参数错（SystemExit）不当成 GPU 不可用",
          "def resolve_device" in src2 and "trial_infer(models[0]" in src2 and 'build_models(args, "cpu")' in src2
          and "except Exception as exc:" in src2 and "if not ocr_args.gpu_unavailable(" in src2)
    _unavail = ["RuntimeError: CUDA error: no kernel image is available for execution on the device",
                "RuntimeError: ORT worker 起不来：…；它的 stderr 尾部：LoadLibrary failed with error 126 when trying to load onnxruntime_providers_cuda.dll",
                "RuntimeError: ORT worker 起不来：{'error': 'rec 的 provider 回落到 CPUExecutionProvider（这份 onnxruntime 有 CUDA EP 却没用上）'}",
                "OSError: CUDA driver version is insufficient for CUDA runtime version",
                'OSError: [WinError 126] Error loading "C:\\x\\cudnn64_9.dll" or one of its dependencies.']
    _err = ["RuntimeError: ORT worker 报错：operation not permitted when stream is capturing",
            "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB",
            "ValueError: 输入 120 MB 超过共享内存的输入区",
            "RuntimeError: ORT worker 报错：Error loading model from inference.onnx: invalid protobuf"]
    check("GPU 试推理的异常分类：架构不支持 / 库加载不了 / CUDA EP 没用上 / 驱动太旧 = 不可用（退回 CPU）；捕图失败、显存不够、别的错 = 报错",
          all(ro.gpu_unavailable(m) for m in _unavail) and not any(ro.gpu_unavailable(m) for m in _err),
          str([m for m in _unavail if not ro.gpu_unavailable(m)] + [m for m in _err if ro.gpu_unavailable(m)]))
    check("实际设备和回退原因写进 _meta（计划 §7.1：不用 auto 代替结果）",
          'meta["device_effective"]' in src2
          and 'meta["device_resolved"]' in src2 and 'meta["device_fallback"]' in src2)
    from flowocr.extract import ocr_parallel as _op2
    _dm = [{"device_resolved": "gpu", "device_fallback": None, "device_effective": {"det": "ort:CUDAExecutionProvider", "rec": "ort:CUDAExecutionProvider"},
            "frames_advanced": 10, "frames_requested": 10, "complete": True, "start_sec": 0, "end_sec": 1} for _ in range(2)]
    _dm2 = [dict(_dm[0]), {**_dm[1], "device_resolved": "cpu", "device_fallback": "x"}]
    _mm = _op2.merge_metas(_dm, {}, wall=1.0, cuts_sec=[0.5], interval=0.5)
    _mm2 = _op2.merge_metas(_dm2, {}, wall=1.0, cuts_sec=[0.5], interval=0.5)
    check("并行拼接带上各段的实际设备（父进程不建模型，不抄就丢）；各段不一致时逐段列出",
          _mm.get("device_effective") == _dm[0]["device_effective"] and _mm.get("device_resolved") == "gpu"
          and _mm2.get("device_resolved") == {"mixed": ["gpu", "cpu"]}, (_mm.get("device_effective"), _mm2.get("device_resolved")))
    import inspect
    check("OrtClient 收 device 并交给 worker", "device" in inspect.signature(ortclient.OrtClient.__init__).parameters)
    srv = (ROOT / "src" / "flowocr" / "extract" / "ort_server.py").read_text(encoding="utf-8")
    check("ort_server 按 --device 决定 provider（不再按『装没装 CUDA EP』自己挑）",
          '"--device"' in srv and 'cuda_ok = a.device != "cpu"' in srv)
    # ---- 阶段 2 只凭提取产物就能结算（计划 §3.1）：obs 那条路的帧率取 obs 的 src_fps，不开视频 ----
    # 回抠的命令行 09-26 去掉了：obs 那条路只在建轨末尾结算（settle_refine），开视频的路只在开发探针里
    import inspect as _inspect
    from flowocr.analyze import build_tracks as _bt
    settle_src = _inspect.getsource(_bt.settle_refine)
    check("阶段 2 按 obs 证据结算时帧率取 obs 的 _meta.src_fps、不开视频（原来一律 cv2 读视频，视频挪走会把 60 fps 当 30）；"
          "obs 没记 src_fps 就不结算",
          'fps = float(meta["src_fps"])' in settle_src and 'not meta.get("src_fps")' in settle_src
          and 'decoder="obs"' in settle_src)
    probe_src = (ROOT / "dev_tools" / "refine_offline_probe.py").read_text(encoding="utf-8")
    check("开视频测只在开发探针里，而且要给 --video", 'ap.add_argument("--video", required=True' in probe_src)
    sys.path.insert(0, str(ROOT / "dev_tools"))
    import refine_offline_probe as ROP
    pbase = {"regions": [{"index": 0, "label": "subtitle"}, {"index": 1, "label": "static-overlay"}],
             "events": [{"id": i, "region": 1 if i == 4 else 0, "text": "x", "t_start": 0, "t_full": None, "t_end": 9000} for i in range(5)]}

    def pev(i, **kw):
        return {**pbase["events"][i], **kw}
    p_on = {"events": [pev(0, t_full=300, t_full_how={"first": "glyph", "full": "glyph"}, t_end_how="curve"),
                       pev(1, t_end=9300, t_end_how="ncc"),                                 # 只量到结尾：没有 t_full_how
                       pev(2, t_full=500, t_full_how={"first": "glyph", "full": "none"}),  # 全字是采样级退回值
                       pev(3), pev(4, t_end=9900, t_end_how="curve")]}
    p_off = {"events": [pev(0, t_full=1300, t_full_how={"first": "glyph", "full": "glyph"}, t_end_how="curve"),
                        pev(1), pev(2, t_full=1500, t_full_how={"first": "glyph", "full": "glyph"}), pev(3), pev(4)]}
    pst, _ = ROP.compare(pbase, p_on, p_off, "subtitle", 1.0)
    check("离线对照探针：三个时刻各算各的分母（目标 = 回抠标签区域的全部事件、四类加起来守恒）；只量到结尾的事件算进消失；"
          "`full == none` 的退回值不算量到；差值只在两边都量到的上面算",
          all(sum(pst[k][c] for c in ("both", "online_only", "offline_only", "neither")) == 4 for k in pst)
          and (pst["t_end"]["both"], pst["t_end"]["online_only"], pst["t_end"]["neither"]) == (1, 1, 2)
          and (pst["t_full"]["both"], pst["t_full"]["offline_only"], pst["t_full"]["neither"]) == (1, 1, 2)
          and len(pst["t_full"]["diffs"]) == 1 and abs(pst["t_full"]["diffs"][0] - 1.0) < 1e-6,
          {k: {c: v for c, v in s.items() if c != "diffs"} for k, s in pst.items()})


def t_extract_debts() -> None:
    """提取层代码欠账（2026-09-26，followups「提取与推理」）：看得见几张卡、多组先取模型、裁不了的 region_crop 复用、
    捕图读写闸、回抠进程的等待与收尾、精确 seek 退半帧。"""
    print("\n## 提取层代码欠账")
    import threading
    from types import SimpleNamespace as _NS
    import time as _time
    from flowocr.extract import edge_proc as EP

    def proxy(stall: float):
        p = EP.EdgeProxy.__new__(EP.EdgeProxy)      # 不起子进程，只测等待判据
        p.cv, p.done_seq, p.error, p.stats, p.stall_sec = threading.Condition(), 0, None, None, stall
        return p
    slow = proxy(0.4)

    def tick():                                      # 每 0.15 s 做完一批：总共等 1.2 s（> 0.4 s）但一直在前进
        for _ in range(8):
            _time.sleep(0.15)
            with slow.cv:
                slow.done_seq += 1
                slow.cv.notify_all()
    threading.Thread(target=tick, daemon=True).start()
    slow._wait(lambda: slow.done_seq >= 8, "等")
    stuck = proxy(0.4)
    t0 = _time.perf_counter()
    stuck._wait(lambda: stuck.done_seq >= 1, "等")
    import os as _os
    from flowocr.extract import ocr_args as _OA
    saved = _os.environ.pop("CUDA_VISIBLE_DEVICES", None)
    try:
        smi = "GPU 0: NVIDIA GeForce RTX 5070 Ti (UUID: GPU-x)\n"
        got_n = [_OA.visible_gpu_count(smi), _OA.visible_gpu_count(smi * 2), _OA.visible_gpu_count("")]
        for v in ("1", "0,1", "", "-1"):
            _os.environ["CUDA_VISIBLE_DEVICES"] = v
            got_n.append(_OA.visible_gpu_count(smi * 4))
    finally:
        _os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        if saved is not None:
            _os.environ["CUDA_VISIBLE_DEVICES"] = saved
    check("看得见几张卡：数 nvidia-smi -L 的 GPU 行；设了 CUDA_VISIBLE_DEVICES 按它数（nvidia-smi 不认它），空串 / -1 = 0 张",
          got_n == [1, 2, 0, 1, 2, 0, 0], got_n)
    import inspect as _insp
    from flowocr.extract import run_groups as _RG
    _ro2s = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    # 2026-10 去掉 Paddle：原来这里还要先取 Paddle 的官方模型（prefetch_paddle_models）；ORT 的模型由共享服务在父进程里取
    check("run_groups 并行起多组之前在父进程起共享服务、det / rec 两个模型都先取好（首跑时几组子进程同时取同一份模型会整批失败）",
          "R.start_ort_services(args" in _insp.getsource(_RG.main)
          and 'want = [("rec", onnx_path(args, "rec")), ("det", onnx_path(args, "det"))]' in _ro2s)
    import json as _json
    from flowocr.extract import framesource as _FS, ocr_complete as _OC
    with tempfile.TemporaryDirectory() as rd:
        rj = Path(rd) / "r.json"
        rj.write_text(_json.dumps({"groups": [{"name": "g", "rects": [{"box": [0, 50, 100, 100], "neg": False}]}]}),
                      encoding="utf-8")
        rargv = ["x.mp4", "--out", "y.jsonl", "--regions", str(rj)]

        def crop_reusable(meta_extra: dict, argv_extra=()) -> bool:
            p = Path(rd) / "o.jsonl"
            cfg = _OA.config_of(rargv)                   # 默认 --region-crop（给了 --regions 就开）
            p.write_text(_json.dumps({"_meta": {"complete": True, "timebase": "pts", "decoder": _FS.DEFAULT_DECODER,
                                                "config": cfg, "src_fps": 60.0, "stride": 30, **meta_extra}}) + "\n",
                         encoding="utf-8")
            return _OC.obs_reusable(p, [*rargv, *argv_extra])[0]
        off = {"region_crop_off": "组范围的外接矩形就是全屏（或某一时段全屏），没得裁"}
        check("--region-crop 裁不了（_meta.region_crop_off）的产物按实际比：--no-region-crop 复用它；真裁了的照旧不混用",
              crop_reusable(off, ["--no-region-crop"]) and crop_reusable(off)
              and not crop_reusable({"region_crop": [0, 0, 100, 50]}, ["--no-region-crop"])
              and crop_reusable({"region_crop": [0, 0, 100, 50]}))
    from flowocr.extract import ort_server as _OS
    g = _OS._CaptureGate()
    order: list = []
    held = threading.Event()

    def reader(tag, hold):
        with g.shared():
            order.append(f"{tag}+")
            held.set()
            _time.sleep(hold)
            order.append(f"{tag}-")

    def writer():
        held.wait()
        with g.exclusive():
            order.append("W+")
            _time.sleep(0.05)
            order.append("W-")
    t_r1 = threading.Thread(target=reader, args=("r1", 0.3))
    t_w = threading.Thread(target=writer)
    t_r1.start()
    t_w.start()
    _time.sleep(0.1)                                 # 写者已经在排队：之后来的读者要等写者做完
    t_r2 = threading.Thread(target=reader, args=("r2", 0.0))
    t_r2.start()
    for t_ in (t_r1, t_w, t_r2):
        t_.join(5)
    check("捕图的读写闸：独占等在途的共享都退出才进；写者排上队之后新来的共享先等（不被源源不断的推理饿死）",
          order == ["r1+", "r1-", "W+", "W-", "r2+", "r2-"], order)
    fin = proxy(0.3)
    fin.last_seq, fin.wait_sec = 4, 0.0
    fin._send = lambda msg: None
    fin.child = _NS(close=lambda wait=0: None)

    def backlog_then_finalize():                     # 积压的 4 批按进度做完，再"算结尾窗"0.8 s（> stall_sec）才交统计
        for _ in range(4):
            _time.sleep(0.1)
            with fin.cv:
                fin.done_seq += 1
                fin.cv.notify_all()
        _time.sleep(0.8)
        with fin.cv:
            fin.stats = {"segments": 1}
            fin.cv.notify_all()
    fin.th = threading.Thread(target=backlog_then_finalize, daemon=True)
    fin.th.start()
    fin_stats = fin.finish(99)
    check("回抠进程收尾分两段：积压按进度等，批号到头之后的结尾窗 / 汇总给固定宽限（没有进度信号，按 stall_sec 判会误伤）",
          fin.error is None and fin_stats.get("segments") == 1, fin.error)
    check("回抠进程的等待按进度判卡（同解码分片）：一直在前进就不判，批号不动 stall_sec 才判",
          slow.error is None and slow.done_seq == 8 and stuck.error is not None and _time.perf_counter() - t0 < 2.5,
          (slow.error, stuck.error))

    from flowocr.extract import framesource as fsx
    # 精确 seek 退半帧：毫秒量化 pts 的帧（比 帧号/帧率 早 0.33 ms）不能被 seek 丢掉（yuka-f5 14843 s，--fps 2.2）
    ms = fsx.build_ffmpeg_cmd("v.mp4", grid=_every(30), start_sec=890591 / 60, n_out=4, select_by="pts", src_fps=60.0)
    blk = fsx.build_ffmpeg_cmd("v.mp4", grid=_every(30), start_sec=890591 / 60, n_out=4, select_by="pts", src_fps=60.0,
                               block=(890591, 891791, None))
    ss = float(ms[ms.index("-ss") + 1])
    check("窗口起点的精确 seek 落在起点帧前半帧：盖得住 pts 取整到 ms 的起点帧（14843.183 < 890591/60），又放不进前一帧",
          14843.183 - 0.0005 > ss > 890590 / 60 + 0.0005 and float(blk[blk.index("-ss") + 1]) == ss
          and abs(ss + float(blk[blk.index("-t") + 1]) - 891790.5 / 60) < 1e-5, (ss, blk))


def t_recover_batch() -> None:
    """回补跨链并批（2026-09-26）：同一帧里几条链一起按层二分，读的框、写回的文本和逐链做完全一样，只是每层合成一次发。"""
    print("\n## 回补跨链并批")
    from flowocr.extract import reuse_v2 as RV

    truth = {f"c1-{j}": ("A" if j < 4 else "B") for j in range(7)}
    truth.update({"c2-0": "X", "c2-1": "Y", "c2-2": "Y"})

    def setup():
        v = RV.ReuseV2.__new__(RV.ReuseV2)
        v.stats = {"recover_reads": 0, "recover_events": 0}
        v.calls = []
        v.rec_one = None
        v.rec_many = lambda crops: (v.calls.append(list(crops)), [(truth[c], 0.9) for c in crops])[1]
        c1, c2 = RV.Chain([0, 0, 10, 10], "A", 0.9, 0), RV.Chain([0, 20, 10, 30], "X", 0.9, 0)
        p1 = [[j, {"text": "A", "conf": 0.9, "reused": True}, f"c1-{j}"] for j in range(7)]
        p2 = [[j, {"text": "X", "conf": 0.9, "reused": True}, f"c2-{j}"] for j in range(3)]
        return v, [(c1, "B", 0.9, p1), (c2, "Y", 0.9, p2)]

    vs, jobs_s = setup()
    sep = vs._recover(jobs_s[:1]) + vs._recover(jobs_s[1:])
    vj, jobs_j = setup()
    joint = vj._recover(jobs_j)
    rows = lambda jobs: [[r[1]["text"] for r in p] for _, _, _, p in jobs]
    check("回补跨链并批：两条链一起二分，读的框、写回的文本、返回值和逐链做一样；每层只发一次（调用数 = 最深那条链的层数）",
          joint == sep and rows(jobs_j) == rows(jobs_s) == [["A"] * 4 + ["B"] * 3, ["X", "Y", "Y"]]
          and sorted(c for call in vj.calls for c in call) == sorted(c for call in vs.calls for c in call)
          and len(vj.calls) == max(sum(1 for call in vs.calls if call[0].startswith(p)) for p in ("c1", "c2"))
          and vj.stats == vs.stats == {"recover_reads": sum(r for r, _ in sep), "recover_events": 2},
          (joint, sep, vj.calls, vs.calls))
