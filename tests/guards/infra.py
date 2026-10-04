"""产物读写、provenance、路径与打包、CLI 与文档同步的守卫。"""
from __future__ import annotations

import importlib  # noqa: F401
import inspect  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

from guards._common import *  # noqa: F401,F403
# 第 2 条的正文就是数字 "3"——`grep -c '^[0-9]\\+$'` 会把它误算成序号行。
# 这正是 30 小时没被发现的那个计数错的一半来源，所以样本里必须有它。


def t_srtio() -> None:
    print("srtio")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.srt"
        p.write_text(SRT_OK, encoding="utf-8")
        cues = srtio.read_srt(p)
        check("count_cues 数时间戳行", srtio.count_cues(p) == 2, f"得到 {srtio.count_cues(p)}")
        check("解析条数与 count_cues 一致", len(cues) == 2, f"得到 {len(cues)}")
        check("多行 cue 不被拼掉", cues[0].lines == ("第一条", "第二行"), str(cues[0].lines))
        check("拼行分隔符由调用方决定",
              cues[0].text(" / ") == "第一条 / 第二行" and cues[0].text(" ") == "第一条 第二行")
        check("正文是纯数字的 cue 不被当成序号行", cues[1].lines == ("3",), str(cues[1].lines))

        # 一个块里两条时间戳 = 畸形，必须抛，不能静默少算
        bad = Path(d) / "b.srt"
        bad.write_text("1\n00:00:01,000 --> 00:00:02,000\n00:00:03,000 --> 00:00:04,000\nx\n\n",
                       encoding="utf-8")
        raised = False
        try:
            srtio.read_srt(bad)
        except srtio.CueCountMismatch:
            raised = True
        check("解析数与权威计数不符时抛错", raised)
        check("显式 strict=False 才放行", len(srtio.read_srt(bad, strict=False)) == 1)

        empty = Path(d) / "c.srt"
        empty.write_text("", encoding="utf-8")
        check("空文件解析为 0 条且不抛", srtio.read_srt(empty) == [])


def t_evalkit() -> None:
    print("evalkit")
    check("样本不足时 pctl 返回 None", evalkit.pctl([1.0] * 5, 0.9) is None)
    check("样本够时 pctl 出数", evalkit.pctl(list(range(30)), 0.9) is not None)
    check("fmt_pctl 在样本不足时明说，而不是给个数",
          "<" in evalkit.fmt_pctl([1.0] * 5, 0.9), evalkit.fmt_pctl([1.0] * 5, 0.9))
    check("denom 永远带分母", evalkit.denom(3, 4) == "3/4 = 75.0%", evalkit.denom(3, 4))

    hit = False
    try:
        evalkit.require_nonzero(0, "配上的条目")
    except SystemExit as e:
        hit = e.code != 0
    check("配上 0 条时非零退出", hit)
    try:
        evalkit.require_nonzero(1, "配上的条目")
        ok = True
    except SystemExit:
        ok = False
    check("非 0 时放行", ok)


def t_tools_exit_nonzero() -> None:
    """端到端：评测工具拿到空参照时必须非零退出，而不是打一行汇总走人。"""
    print("工具端到端")
    with tempfile.TemporaryDirectory() as d:
        empty = Path(d) / "empty.srt"
        empty.write_text("", encoding="utf-8")
        real = Path(d) / "real.srt"
        real.write_text(SRT_OK, encoding="utf-8")
        for tool, args in (("compare_text.py", [str(empty), str(real)]),
                           ("extra_entries.py", [str(empty), str(real)])):
            r = subprocess.run([sys.executable, str(ROOT / "dev_tools" / tool), *args],
                               capture_output=True, text=True)
            check(f"{tool} 空参照非零退出", r.returncode != 0, f"退出码 {r.returncode}")


def t_added_knobs() -> None:
    """**新加的空操作旋钮不该让现成 obs 全部重建**（`ocr_args.config_differs` / `ADDED_NOOP`）。

    2026-09-18：一夜加了 5 个默认关着的旋钮，而复用判据是"两边的键取并集逐键比"——
    旧产物缺键就 `None != 0`，**所有整场素材都要重跑**（小时级），可默认路径的产物**逐字节相同**。
    所以缺键按 `ADDED_NOOP` 兜底。⚠ 这张表只收"默认不开时产物逐字节相同"的旋钮，
    所以这里也守住反面：真改了值、或者本来就有的键值不同，照样要报。
    """
    print("ocr_args.config_differs / ADDED_NOOP")
    from flowocr.extract import ocr_args

    # 采样 / 辅助流按时间网格取帧（2026-09-24，framegrid）：生效网格 + 复用判据按生效网格比
    from flowocr.extract.framegrid import AuxGrid, TimeGrid
    _s = lambda src, fps=2.0, sel="pts": ocr_args.sample_grid(src, fps, sel)
    check("采样网格：60 上 2 fps = 每 30 帧（等距）、59.94 上 2 fps 不等距（1001/30000）、60 上 8 fps 不等距（2/15）",
          _s(60) == TimeGrid.every(30) and _s(60000 / 1001) == TimeGrid(1001, 30000) and _s(60, 8) == TimeGrid(2, 15)
          and _s(60, 8).frames(0, 30) == [0, 8, 15, 23, 30])
    check("采样网格：最长间隔不小于 1.5 个名义间隔 -> 报错（下游的容差盖不住）；整除的照样放行（60 上 30 / 60）",
          raises(lambda: _s(60, 45), ValueError) and _s(60, 40) == TimeGrid(2, 3)
          and _s(60, 30) == TimeGrid.every(2) and _s(60, 60) == TimeGrid.every(1))
    _ntsc = 60000 / 1001
    check("**NTSC 帧率吸附**（2026-09-24 审计）：59.94 素材上给 29.97 / 59.94、23.976 上给 23.976 都是等距；给 2 仍是真时间网格",
          _s(_ntsc, 29.97) == TimeGrid.every(2) and _s(_ntsc, 59.94) == TimeGrid.every(1)
          and _s(24000 / 1001, 23.976) == TimeGrid.every(1) and _s(_ntsc, 2) == TimeGrid(1001, 30000)
          and ocr_args.aux_grid(_ntsc, 2, 59.94).key() == ("step", 1) and _s(_ntsc, 30) == TimeGrid(1001, 2000))
    check("辅助流上限是上限：119.88 fps 素材给 60 -> 自己那部分隔帧（59.94，不为多出的 0.1% 走不等距网格；采样 2 fps 在那上面本身不整除，并集照样带上）；"
          "60 给 25 / 144 给 60 仍是时间网格",
          ocr_args.aux_grid(2 * _ntsc, 2, 60).own == TimeGrid.every(2) and ocr_args.aux_grid(120, 2, 60).key() == ("step", 2)
          and not ocr_args.aux_grid(60, 2, 25).uniform and ocr_args.aux_grid(144, 2, 60).key() == (5, 12, 1, 72))
    check("按帧号数的选法（index / cv2）退回最近的整数步长（加时间网格之前的行为）",
          _s(60, 8, "index") == TimeGrid.every(8) and _s(60000 / 1001, 2, "index") == TimeGrid.every(30))
    _g = lambda src, mx=60.0, fps=2.0: ocr_args.aux_grid(src, fps, mx)
    check("辅助流生效网格：60 fps -> 每帧、120 -> 隔帧（等距）、59.94 -> 每帧、60 给 30 -> 隔帧、60 给 25 / 144 -> 不等距",
          [_g(60).key(), _g(120).key(), _g(60000 / 1001).key(), _g(60, 30).key()] == [("step", 1), ("step", 2), ("step", 1), ("step", 2)]
          and not _g(60, 25).uniform and not _g(144).uniform and _g(144).key() == (5, 12, 1, 72),
          [_g(60).key(), _g(120).key(), _g(60000 / 1001).key(), _g(60, 25).key(), _g(144).key()])
    check("辅助流上限比采样帧率还低 -> 报错（辅助流不能比采样还粗）", raises(lambda: _g(60, 1), ValueError))
    # 选帧规则：时间网格 + 采样帧嵌套；等距时退化成"每 q 帧一帧"
    _ng = AuxGrid(TimeGrid(5, 12), TimeGrid.every(72))
    _mem = [i for i in range(0, 1440) if _ng.member(i)]            # 144 fps 上 10 秒
    _gaps = {b - a for a, b in zip(_mem, _mem[1:])}
    check("**嵌套**：采样帧全在辅助流里；不等距网格帧率 ≈ 上限（每秒 60 + 至多 2 帧采样）、间距 1~3 帧",
          all(_ng.member(i) for i in range(0, 1440, 72)) and 600 <= len(_mem) <= 600 + 20 and _gaps <= {1, 2, 3},
          (len(_mem), sorted(_gaps)))
    _n8 = AuxGrid(TimeGrid(5, 12), TimeGrid(2, 15))                 # 采样也不等距（60 上 8 fps）
    check("**嵌套**（采样也不等距）：采样网格上的帧全在辅助流里",
          all(_n8.member(i) for i in TimeGrid(2, 15).frames(0, 3000)))
    _ug = AuxGrid(TimeGrid.every(2), TimeGrid.every(30))
    check("等距网格：frames / floor / ceil / prev / n_frames / cover_floor 都是原来的整除算术",
          _ug.frames(21, 48) == list(range(22, 49, 2)) and _ug.floor(21) == 20 and _ug.ceil(21) == 22
          and _ug.prev(48) == 46 and _ug.n_frames(60, 121) == 31 and _ug.cover_floor(90) == 89 and _ug.step == 2)
    _tg = TimeGrid(1001, 30000)
    check("TimeGrid 的闭式（第 k 格 = ceil(k·q/p)）和逐帧定义一致：member / floor / ceil / prev / next / frames",
          all(_tg.member(i) == ((i * 1001) // 30000 != ((i - 1) * 1001) // 30000) for i in range(-60, 70000))
          and all(_tg.floor(i) <= i <= _tg.ceil(i) and _tg.member(_tg.floor(i)) and _tg.member(_tg.ceil(i))
                  and _tg.prev(i) < i < _tg.next(i) for i in range(1, 5000, 7))
          and _tg.frames(0, 30000) == [i for i in range(0, 30001) if _tg.member(i)])
    _g22 = TimeGrid(11, 300)                                          # 60 上 2.2 fps：格子 0 / 28 / 55 …
    check("**nearest 按帧距取最近一格**（不等距时不能按格号四舍五入；一样近取前一格）；等距的逐位同原来的 round(x / q) * q",
          _g22.nearest(13.7) == 0 and _g22.nearest(14.0) == 0 and _g22.nearest(14.1) == 28
          and all(_g22.nearest(x + 0.3) == min(_g22.frames(0, 400), key=lambda m: (abs(m - x - 0.3), m)) for x in range(0, 360))
          and [TimeGrid.every(30).nearest(x) for x in (44.9, 45.0, 75.0, 1234.5)]
          == [int(round(x / 30)) * 30 for x in (44.9, 45.0, 75.0, 1234.5)])
    check("不等距辅助网格：floor / ceil 落在网格上、prev 严格更小、frames 和 member 一致",
          all(_ng.member(_ng.floor(i)) and _ng.floor(i) <= i and _ng.member(_ng.ceil(i)) and _ng.ceil(i) >= i
              and _ng.prev(i) < i and _ng.member(_ng.prev(i)) for i in range(1, 300))
          and _ng.frames(10, 40) == [i for i in range(10, 41) if _ng.member(i)] and _ng.step is None)
    check("规范形：写法不同、帧集合相同就相等（旧产物的步长 = 等距网格；比值 ≥ 1 一律每帧）",
          AuxGrid.from_spec({"step": 2}) == _ug and TimeGrid(3, 2) == TimeGrid.every(1)
          and AuxGrid.from_spec({"grid": [5, 12, 1, 72]}) == _ng and _ng != AuxGrid(TimeGrid(5, 12), TimeGrid.every(36)))
    # ffmpeg 表达式和 Python 的 member 选出同一批帧：拿 select 条件在 Python 里按 ffmpeg 的语义（double 运算）求值
    import math as _m

    def _ev(cond, n):
        c = cond.replace("\\,", ",")
        return eval(c.replace("not(", "(0==").replace("mod(", "_mod(").replace("eq(", "_eq("),
                    {"N": n, "_mod": lambda a, b: a - b * _m.floor(a / b), "_eq": lambda a, b: a == b, "floor": _m.floor})
    _ns = list(range(0, 3000)) + list(range(1_296_000, 1_299_000))
    check("**ffmpeg 选帧表达式和 Python 的 member 选出同一批帧**（辅助流 / 59.94 上的采样，6 小时帧号内抽查，double 精度不判错）",
          all(bool(_ev(_ng.select("N"), n)) == _ng.member(n) for n in _ns)
          and all(bool(_ev(_tg.select("N"), n)) == _tg.member(n) for n in _ns), _ng.select("N"))
    check("等距网格生成的 ffmpeg 条件和时间网格之前逐字相同",
          _ug.select("n") == "not(mod(n\\,2))" and AuxGrid(TimeGrid(1, 1), TimeGrid.every(30)).select("n") == ""
          and TimeGrid.every(30).select("n") == "not(mod(n\\,30))")
    import json
    from flowocr.extract import framesource as _fs, ocr_complete as _oc
    _argv = ["x.mp4", "--out", "y.jsonl"]
    with tempfile.TemporaryDirectory() as _d:
        def _reusable(src_fps, meta_extra, argv_extra=(), cfg_extra=None):
            _p = Path(_d) / "o.jsonl"
            _cfg = {k: v for k, v in ocr_args.config_of(_argv).items() if k != "aux_max_fps"}   # 加旋钮之前的产物没有这个键
            _cfg.update(cfg_extra or {})
            _p.write_text(json.dumps({"_meta": {"complete": True, "timebase": "pts", "decoder": _fs.DEFAULT_DECODER,
                                                "config": _cfg, "src_fps": src_fps, "stride": round(src_fps / 2),
                                                **meta_extra}}) + "\n", encoding="utf-8")
            return _oc.obs_reusable(_p, [*_argv, *argv_extra])[0]
        check("旧产物（没有 aux_max_fps、没记网格、config 里 refine_step 1）：60 fps 照样复用，120 fps 重建（旧的是每帧、现在隔帧）",
              _reusable(60.0, {}, cfg_extra={"refine_step": 1}) and not _reusable(120.0, {}, cfg_extra={"refine_step": 1}))
        check("加时间网格之前记了生效步长的产物：120 fps 隔帧的复用；要的上限改成 120 就对不上",
              _reusable(120.0, {"aux_step_effective": 2})
              and not _reusable(120.0, {"aux_step_effective": 2}, ["--aux-max-fps", "120"]))
        check("新产物记生效网格：60 fps 给 25 的复用自己、和 30 的不混用",
              _reusable(60.0, {"aux_grid": [5, 12, 1, 30]}, ["--aux-max-fps", "25"])
              and not _reusable(60.0, {"aux_grid": [5, 12, 1, 30]}, ["--aux-max-fps", "30"])
              and _reusable(60.0, {"aux_grid": [1, 2, 1, 30]}, ["--aux-max-fps", "30"]))
        check("`--refine-step` 删了：旧产物记着 refine_step 2 的（60 fps）当成隔帧，要默认（每帧）就重建",
              not _reusable(60.0, {}, cfg_extra={"refine_step": 2})
              and _reusable(60.0, {}, ["--aux-max-fps", "30"], cfg_extra={"refine_step": 2}))
        check("**采样网格按生效值比**：59.94 fps 的旧产物（每 30 帧一帧）要重建（现在按时间网格取）；新产物记 sample_grid 的复用",
              not _reusable(60000 / 1001, {})
              and _reusable(60000 / 1001, {"sample_grid": [1001, 30000], "aux_grid": [1, 1, 1001, 30000]})
              and _reusable(60.0, {"sample_grid": [1, 30]}))

    base = ["x.mp4", "--out", "y.jsonl"]
    want = ocr_args.config_of(base)
    old = {k: v for k, v in want.items() if k not in ocr_args.ADDED_NOOP}
    # ⚠ ADDED_NOOP 存的是**这个旋钮出现之前的行为**，不是"当前默认"——这两个在默认翻过之后就分家了
    # （2026-09-20 `--rec-engine` 翻成 ort，ADDED_NOOP 仍记 paddle）。所以"缺键 -> 不算差异"
    # 只对**没翻过默认**的那批成立；翻过的那几个，要的是**显式回退臂**才对得上。
    # 布尔旋钮（BooleanOptionalAction）不认 `=False`，要写成 `--x` / `--no-x`。
    # 已经不是命令行选项的键（`rec_engine` / `det_engine`：2026-10 去掉 Paddle 后成了固定的身份串）没有回退臂，跳过
    opts = {a.dest for a in ocr_args.build_parser()._actions}
    ident = sorted(k for k in ocr_args.ADDED_NOOP if k not in opts)
    back = [(f"--{k.replace('_', '-')}" if v else f"--no-{k.replace('_', '-')}") if isinstance(v, bool)
            else f"--{k.replace('_', '-')}={v}" for k, v in ocr_args.ADDED_NOOP.items() if want.get(k) != v and k in opts]
    _back_diff = ocr_args.config_differs(old, ocr_args.config_of(base + back))
    # 身份串键的期望：恰好报出 rec_engine（旧值 paddle 对 ort，没有回退臂）；det_engine 默认模型那一对由 NOISE_EQUIV_IF 放行
    check("ADDED_NOOP 兜的是『这个旋钮出现之前的行为』：缺键 + 回退臂 -> 不算差异；身份串键（rec_engine / det_engine）没有回退臂，"
          "恰好报出 rec_engine",
          ident == ["det_engine", "rec_engine"] and _back_diff == ["rec_engine"], f"身份串 {ident}，差异 {_back_diff}")
    # 2026-10 去掉 Paddle：rec_engine / det_engine 固定写进 config（身份串），两条判据（drop-paddle 计划「obs 复用判据」）
    _arms = ([], ["--no-reuse-v2"], ["--rec-window", "0"], ["--det-batch", "1"])
    _no_rec = [ocr_args.config_differs({k: v for k, v in ocr_args.config_of(base + a).items() if k != "rec_engine"},
                                       ocr_args.config_of(base + a)) for a in _arms]
    check("没有 rec_engine 键的旧 config（09-18 之前的 Paddle-rec 产物）对每条当前臂都判差异（默认 / --no-reuse-v2 / --rec-window 0 / --det-batch 1）",
          all("rec_engine" in d for d in _no_rec) and all(ocr_args.config_of(base + a)["rec_engine"] == "ort" for a in _arms), str(_no_rec))
    _old_paddle_det = {**want, "det_engine": "paddle", "det_model": "PP-OCRv6_medium_det", "rec_model": "PP-OCRv6_medium_rec",
                       "no_fast_det": False, "rec_batch_size": 0}
    _small = {**_old_paddle_det, "det_model": "PP-OCRv6_small_det"}
    check("det_engine=paddle + 默认模型的旧 obs 照旧可复用（删掉的四个选项按 REMOVED_NOOP）；换过 det 模型的照旧拦下",
          ocr_args.config_differs(_old_paddle_det, want) == []
          and ocr_args.config_differs(_small, want) == ["det_engine", "det_model"],
          f"{ocr_args.config_differs(_old_paddle_det, want)} / {ocr_args.config_differs(_small, want)}")
    check("默认翻过的那几个：缺键 + 现默认 -> 照样报差异（老产物是另一条路产的，该重建）",
          ocr_args.config_differs(old, want)       # det_onnx 的旧默认和新默认同属 EQUIV_VALUES 的一类（owner 认可的那批），不算
          == sorted(k for k, v in ocr_args.ADDED_NOOP.items() if not ocr_args.same_value(k, want.get(k), v)
                    and k not in ocr_args.NOISE_EQUIV
                    and not (k in ocr_args.NOISE_EQUIV_IF and ocr_args.NOISE_EQUIV_IF[k](old, want))),
          # 翻过默认、但 owner 定为噪音级的（det_engine，限验证过的模型对）不比
          str(ocr_args.config_differs(old, want)))
    _old_ort = {**old, **{k: want[k] for k in ident}}     # 身份串照现在的值补上（它们没有回退臂，见上），只看新旋钮
    check("真开了新旋钮 -> 报差异",
          ocr_args.config_differs(_old_ort, ocr_args.config_of(base + back + ["--pregate"])) == ["pregate"])
    check("新旋钮给了非默认值 -> 报差异",
          ocr_args.config_differs(_old_ort, ocr_args.config_of(base + back + ["--rec-near", "0.05"]))
          == ["rec_near"])
    check("老键值不同 -> 照样报差异",
          ocr_args.config_differs({**want, "det_batch": 8}, want) == ["det_batch"])
    # 反面：产物**有**这个键但值不同（不是"缺键"），不许被兜底放过
    got = {**want, "rec_engine": "paddle"}
    check("产物里有键但值不同 -> 不许被兜底放过",
          ocr_args.config_differs(got, want) == ["rec_engine"])


def t_provenance() -> None:
    """产物的版本戳：写得出来，而且版本对不上的时候**会响**。

    存在的理由（methodology-audit-2 报告 §2）：改了三次 `build_tracks`，
    产物落在临时目录，报数工具只按文件名 glob——
    **照着文档的复现命令跑，打出来的是另一张表，而且更好看**。
    """
    print("provenance")
    import argparse
    import io
    import json
    from contextlib import redirect_stdout

    from flowocr.analyze import build_tracks as bt
    import h2h_report as hr

    with tempfile.TemporaryDirectory() as d:
        obs = Path(d) / "x.jsonl"
        obs.write_text("{}\n", encoding="utf-8")
        args = argparse.Namespace(obs=str(obs), ui_share=0.5, tag="t")
        # provenance **并进了 `-tracks.json`**（`output-format-plan（已归档）` §2.1），
        # 不再是单独一个文件；这里测的是那个 dict 本身。
        got = bt.provenance(args, {"video": "v.mp4", "sample_fps": 2.0})
        check("provenance 带 git_head", got.get("git_head", "") != "", got.get("git_head"))
        # git_head 要能说出"跑的代码不是这个 commit"（2026-09-08）
        head = bt.git_head()
        check("git_head 拿得到东西", bool(head) and head != "?", head)
        # 路径清单问 build_tracks 要，**别在这里抄第二份**：抄的那份漏了
        # `tools/reference/`（产 `ours*_jp.ass` 的一级代码，audit-5 §7；原在 data/reference）也不会响。
        dirty_now = subprocess.run(
            ["git", "status", "--porcelain", "--", *bt.DIRTY_PATHS],
            capture_output=True, text=True, cwd=ROOT).stdout.strip()
        check("+dirty 的判据盖住 tools/reference（产 ours*_jp.ass 的一级代码）",
              "dev_tools" in bt.DIRTY_PATHS and (ROOT / "dev_tools/reference/match_ref.py").exists())
        # 正式包搬进 src/ 之后它也是产物代码（project-structure 计划 §9 第 1 条）
        check("+dirty 的判据盖住 src/（正式包）", "src" in bt.DIRTY_PATHS and (ROOT / "src/flowocr").is_dir())
        check("代码目录脏时 git_head 必须缀 +dirty，干净时必须不缀"
              "（HEAD 记的是最后一次提交，不是跑的那份代码）",
              head.endswith("+dirty") == bool(dirty_now),
              f"{head!r} / dirty={bool(dirty_now)}")

        # 代码指纹（audit-6 §2.1）：HEAD 移动而代码没变时 git_head 会不同，指纹不会；
        # 文件表要盖住 build_tracks import 的**全部**本地模块，漏一个就证明不了"同一份代码"
        check("provenance 带代码指纹", got.get("code_fp") == bt.code_fp() and len(got["code_fp"]) == 12)
        import re

        def local_files(mod: Path) -> set[str]:
            """`mod` 自己 + 它顶层 import 的本地模块，**仓库相对路径**。
            `import x` 先找 `tools/x.py`；那是 shim（带 `__flowocr_shim__`）就记它指向的真实现，
            **不再读已迁走的旧路径**；`flowocr.*` 直接落到 `src/`——`from flowocr.pkg import a, b as c`
            逐个名字落到 `src/flowocr/pkg/a.py`，落不到的（import 的是函数 / 常量）记 `pkg.py` 本身；
            shim 本身不算产物代码。"""
            def pkg_file(dotted: str) -> Path:
                return (ROOT / "src" / Path(*dotted.split("."))).with_suffix(".py")

            def tool_file(name: str) -> Path:
                cand = ROOT / "dev_tools" / f"{name}.py"
                if cand.exists():
                    m = re.search(r'^__flowocr_shim__ = "([\w.]+)"', cand.read_text(encoding="utf-8"), re.M)
                    if m:
                        return pkg_file(m.group(1))
                return cand

            text = mod.read_text(encoding="utf-8")
            found = [pkg_file(n) if n.startswith("flowocr") else tool_file(n)
                     for n in re.findall(r"^import ([\w.]+)", text, re.M)]
            for base, names in re.findall(r"^from ([\w.]+) import (\([^)]*\)|[^\n]+)", text, re.M):
                for chunk in names.strip("()").split(","):
                    item = chunk.split("#")[0].split()
                    if not item:
                        continue
                    if base.startswith("flowocr"):
                        leaf = pkg_file(base + "." + item[0])
                        found.append(leaf if leaf.exists() else pkg_file(base))
                    else:
                        found.append(tool_file(base))
            out = {mod.relative_to(ROOT).as_posix()}
            for cand in found:
                if cand.exists():
                    out.add(cand.relative_to(ROOT).as_posix())
            return out

        def lazy_files(mod: Path) -> set[str]:
            """函数里（缩进的）`from flowocr… import …` 落到的本地文件。"""
            out = set()
            for base, names in re.findall(r"^[ \t]+from (flowocr[\w.]*) import ([^\n#]+)",
                                          mod.read_text(encoding="utf-8"), re.M):
                for item in names.split(","):
                    leaf = (ROOT / "src" / Path(*(base + "." + item.split()[0]).split("."))).with_suffix(".py")
                    out.add((leaf if leaf.exists() else (ROOT / "src" / Path(*base.split("."))).with_suffix(".py"))
                            .relative_to(ROOT).as_posix())
            return out

        fp_mods = ("flowocr.analyze.build_tracks", "flowocr.analyze.gamescript", "flowocr.analyze.scriptmatch")
        for mod in fp_mods:
            m_ = importlib.import_module(mod)
            local = local_files(Path(m_.__file__).resolve())
            lazy = lazy_files(Path(m_.__file__).resolve())
            # 懒 import 的模块自己顶格 import 的本地模块也算（slot_lines -> slot_modes，2026-09-26 审计补进表）
            lazy |= {f for lf in list(lazy) if (ROOT / lf).exists() for f in local_files(ROOT / lf)}
            check(f"{mod.rsplit('.', 1)[1]} 的指纹文件表 = 它自己 + 它 import 的本地模块（仓库相对路径；多出来的只许是函数里懒 import 的，及其顶格 import）",
                  local <= set(m_.CODE_FP_FILES) and set(m_.CODE_FP_FILES) - local <= lazy,
                  f"import 出来的是 {sorted(local)}，懒 import {sorted(lazy)}，表里是 {sorted(m_.CODE_FP_FILES)}")
        # 懒 import 的本地模块也决定产物（2026-09-23：build_tracks 的 regions / slot_learned 两个都漏过）。
        # 例外要明说理由。gamescript / scriptmatch 的懒 import（build_tracks / 输出预设）还没核，记在 decode-buffer §8.15
        m_bt = importlib.import_module("flowocr.analyze.build_tracks")
        lazy_ok = {"src/flowocr/extensions.py"}   # 加载器；用户匹配器的身份另记在 provenance 的 `matcher`
        miss = lazy_files(Path(m_bt.__file__).resolve()) - lazy_ok - set(m_bt.CODE_FP_FILES)
        check("build_tracks 函数里懒 import 的本地模块也在指纹表里（例外只有加载器）", not miss, sorted(miss))
        # 聚类两条路（`--cluster learned` / `lines`）顶格 import 的本地模块也要在表里：它们只在这两条路上跑，
        # 没有自己的一级指纹（回抠结算另有 `boundary_refined.code_fp`，不在此列）
        deep = {f for m in ("slot_learned", "slot_lines")
                for f in local_files(ROOT / "src" / "flowocr" / "analyze" / f"{m}.py")} - set(m_bt.CODE_FP_FILES)
        check("build_tracks 的聚类路径顶格 import 的本地模块也在指纹表里（slot_lines -> slot_modes、slot_learned -> pair_features）", not deep, sorted(deep))
        check("指纹表里没有 tools/ 的文件（已迁模块全在 src/，tools/ 里只剩探针）",
              not any(f.startswith("tools/") for mod in fp_mods for f in importlib.import_module(mod).CODE_FP_FILES))
        crlf, lf, other = (Path(d) / x for x in ("crlf", "lf", "other"))
        for p, body in ((crlf, b"x = 1\r\ny = 2\r\n"), (lf, b"x = 1\ny = 2\n"), (other, b"x = 1\ny = 3\n")):
            p.mkdir()
            (p / "a.py").write_bytes(body)
        fp = {p.name: bt.code_fp(("a.py",), p) for p in (crlf, lf, other)}
        check("指纹不看换行风格（主 checkout 和槽的 autocrlf 可能不同），改一个字就变",
              fp["crlf"] == fp["lf"] != fp["other"], fp)

        check("provenance 记下参数", got["args"]["ui_share"] == 0.5)
        # argv 记的是"命令行上真打了什么"，head2head.sh 拿它判断旋钮变没变
        # （methodology-audit-3 报告 §4：缓存只看 mtime，换了 BUILD_ARGS 会静默复用）
        check("provenance 记下 argv", isinstance(got.get("argv"), list))

        # 主轨文件名由 build_tracks 定，下游不许 glob 猜（2026-09-07）
        got2 = bt.provenance(args, {}, "t-region01-subtitle.srt", "r01", 4387)
        check("provenance 写下主轨是哪条轨、哪个文件、多少条",
              got2["main_srt"] == "t-region01-subtitle.srt" and got2["main_cues"] == 4387
              and got2["main_track"] == "r01")

        # 上一轮的区域 SRT 必须清掉，否则同 index 不同 label 的旧文件会被 glob 抢走
        od = Path(d) / "od"
        od.mkdir()
        for name in ("t-region01-static-overlay.srt", "t-region01-subtitle.srt",
                     "t-region00-subtitle.srt"):
            (od / name).write_text("", encoding="utf-8")
        (od / "other-region01-subtitle.srt").write_text("", encoding="utf-8")
        for name in ("t-tracks.json", "t-main-full.srt", "t-matched.json"):
            (od / name).write_text("{}", encoding="utf-8")
        n = bt.clear_stale_srts(od, "t")
        check("清掉本 tag 的旧区域 SRT、-full 派生物和上一轮的 tracks.json（建轨成功才重写它）", n == 5, n)
        check("别的 tag 和下游产物不动",
              {q.name for q in od.iterdir()} == {"other-region01-subtitle.srt", "t-matched.json"},
              sorted(q.name for q in od.iterdir()))

        # 版本不一致要出声
        for film, head in (("f1", "aaaaaaaaaaaa"), ("f2", "bbbbbbbbbbbb")):
            (Path(d) / f"{film}-prov.json").write_text(
                json.dumps({"git_head": head}), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            hr.check_provenance(Path(d), {"f1": {}, "f2": {}})
        check("各片 git_head 不一致时会响", "不一致" in buf.getvalue(), buf.getvalue())

        buf = io.StringIO()
        with redirect_stdout(buf):
            hr.check_provenance(Path(d), {"f1": {}, "f9": {}})
        check("缺 prov.json 时明说『不知道是哪一版』", "f9" in buf.getvalue()
              and "不知道" in buf.getvalue(), buf.getvalue())

    # 默认值的指纹（audit-5 §2）：「默认」这条臂跑的是什么，日志里要留得下来——
    # 改默认那次直接把 A/B 的基准臂覆盖了，三处文档里引的数从产物里数不回来。
    ap = argparse.ArgumentParser()
    ap.add_argument("obs")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--main-band", type=float, default=3.0)
    ap.add_argument("--flag", action="store_true")
    fp, line = bt.defaults_fingerprint(ap)
    check("指纹里有每个旋钮的默认值", "main_band=3.0" in line and "flag=False" in line, line)
    check("**位置参数和每次都变的路径不进指纹**",
          "obs=" not in line and "outdir=" not in line, line)
    ap2 = argparse.ArgumentParser()
    ap2.add_argument("obs")
    ap2.add_argument("--outdir", required=True)
    ap2.add_argument("--main-band", type=float, default=0.0)     # 只改了一个默认
    ap2.add_argument("--flag", action="store_true")
    check("**改了一个默认，指纹必须变**（不然这行日志什么也证明不了）",
          bt.defaults_fingerprint(ap2)[0] != fp)
    check("同一个 parser 指纹稳定", bt.defaults_fingerprint(ap)[0] == fp)


def t_pipeline_wiring() -> None:
    """**接线守卫**：`run_ocr2` 的主循环真的把组批那几件事接上了（2026-09-19，一次真实的回归）。

    起因：删投机那一段时，我用"从 A 删到 B"切了一个区间，而**窗口分支正好夹在中间**，
    于是 `--rec-window` 被静默删成了空操作。当时跑的两道自检都照样绿——
    `--rec-window 1` 逐字节相同（分支没了，它当然相同）、守卫全过（守卫直接调 reuse_v2，不走主循环）。
    **是异步臂的读数（真读掉回 W=0 的 2,205、批 5.0 → 1.67）把它抓出来的。**

    所以这里钉的不是行为、是**接线**：拿 AST 看主循环里这几个调用还在不在。
    它挡不住"接错了"，但挡得住"整段没了"——而后者恰恰是自检看不见的那一种。
    """
    print("run_ocr2 的接线（组批那几件事有没有被接上）")
    import ast

    src = (ROOT / "src/flowocr/extract/run_ocr2.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    for name, why in (("commit_stage1", "窗口模式的 stage1"),
                      ("commit_stage2", "窗口结算"),
                      ("drain", "异步路径的固定滞后结算"),
                      ("settle_window", "同步窗口的结算"),
                      ("submit", "把裁剪交给后台消费者"),
                      ("new_ids", "请求号")):
        check(f"主循环里还调着 `{name}`（{why}）", name in called, sorted(called)[:0] or name)
    # 旋钮真的被读到（删掉分支时 args.rec_window 会一个引用都不剩）
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for knob in ("rec_window", "rec_async", "rec_knee", "rec_near"):
        check(f"`args.{knob}` 在 run_ocr2 里真的被读到", knob in attrs, knob)


def t_config_noop() -> None:
    """配置比对的**两半兜底**：后加的旋钮（`ADDED_NOOP`）和删掉的旋钮（`REMOVED_NOOP`）。

    删掉一个旋钮时才发现只有前一半（2026-09-19）：`config_differs` 按 `set(got) | set(want)` 逐键比，
    删了 `--rec-lookahead` 之后，**产物里有、当前解析没有**——磁盘上所有现成 obs（含整片）
    会被判成"参数对不上"、全部重建，而它们和今天的默认**行为完全相同**。
    """
    print("config_differs 的两半兜底（加旋钮 / 删旋钮）")
    from flowocr.extract import ocr_args

    base = {"rec_bucket": 32, "start": 0.0}
    check("后加的旋钮：产物缺键、而要的正好是它的空操作值 -> 不重建",
          ocr_args.config_differs(dict(base), {**base, "rec_window": 0}) == [])
    check("后加的旋钮：要的**不是**空操作值 -> 要重建",
          ocr_args.config_differs(dict(base), {**base, "rec_window": 8}) == ["rec_window"])
    check("**删掉的旋钮**：产物里记着它没开 -> 不重建（`REMOVED_NOOP`）",
          ocr_args.config_differs({**base, "rec_lookahead": 0}, dict(base)) == [])
    check("**删掉的旋钮**：产物里记着它**开过** -> 照样要重建（那一趟和现在行为不同）",
          ocr_args.config_differs({**base, "rec_lookahead": 4}, dict(base)) == ["rec_lookahead"])
    check("没进任何一张表的键，缺了就是缺了",
          ocr_args.config_differs({**base, "reuse_corr": 0.8}, dict(base)) == ["reuse_corr"])
    check("`--rec-lookahead` 真的从解析器里删掉了（探针和驱动也不该再提它）",
          "rec_lookahead" not in ocr_args.config_of(["v.mp4", "--out", "o.jsonl"]))


def t_argparse_help() -> None:
    """`--help` 不许崩。

    来历（2026-09-09）：`run_ocr2 --decoder` 的说明里写了「文本 96.0–97.6% 相同」，
    而 **argparse 会对 help 串做 `%` 格式化**，于是 `--help` 直接
    `ValueError: unsupported format character`。这类错**只在有人敲 --help 时才发作**，
    正常跑一万遍都碰不到——正是"错了不会报错"的一类，所以放守卫里。

    静态查：`%` 后面只准跟 `%` 或 `(`（`%(default)s` 那种）。
    不 import 那些脚本——`run_ocr2` 顶层拉 cv2 和整个提取包，共享层够不着。
    """
    print("argparse help 不许崩")
    import ast

    files = sorted((ROOT / "dev_tools").glob("*.py"))
    files.append(ROOT / "src/flowocr/extract/run_ocr2.py")
    bad: list[str] = []
    n_help = 0
    for path in files:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            bad.append(f"{path.name}: 语法错误")
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            for kw in node.keywords:
                if kw.arg != "help":
                    continue
                try:
                    text = ast.literal_eval(kw.value)
                except (ValueError, TypeError):
                    continue                      # 拼出来的，静态查不了
                if not isinstance(text, str):
                    continue
                n_help += 1
                i = 0
                while (i := text.find("%", i)) != -1:
                    nxt = text[i + 1:i + 2]
                    if nxt not in ("%", "("):
                        bad.append(f"{path.name}:{node.lineno} …{text[max(0,i-12):i+3]}…")
                        break
                    i += 2 if nxt == "%" else 1
    check(f"**扫到了 help 串**（{n_help} 条），不然这个守卫没在测东西", n_help > 40)
    check("**每个 argparse 的 help 串里 `%` 都转义了**（单个 `%` 会让 --help 抛 "
          "ValueError，而那只在有人敲 --help 时才发作）",
          not bad, "有问题的：" + "；".join(bad[:5]))


def t_docs_in_sync() -> None:
    """文档索引跟着文件走：新文档没进索引、归档的计划书占了索引行，都会响。

    入口文件原来写着守卫项数、这里核它等于实际——2026-09-24 两样一起删了：
    并行分支各写一个数、合并必冲突，一个会话里为它撞了 27 次，而这个数不影响任何决定。
    """
    print("文档同步")

    # docs/ 下每篇 .md（含 dev/ reports/ plans/ 各子目录）都要在索引里，按 docs 相对路径找（README 自己除外）。
    # `docs/archive/` **故意不进索引**：那是退出日常指导的历史材料，现行内容另有正式文档。
    # `docs/notes/` 是只留本地的随手笔记（ignore），不是项目文档。
    # 索引有两份：公开的 docs/README.md 与私有的 plans 目录下的 README（计划、报告、归档；发布时剥离，公开树里没有）。
    # 两份里的链接都解析成 docs 相对路径再比（私有那份在 plans/ 下，链接写法不同）。
    import posixpath
    import re
    readme = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    indexed = set()
    for idx in ("README.md", "plans/README.md"):
        if (ROOT / "docs" / idx).is_file():
            base = posixpath.dirname(idx)
            for t in re.findall(r"\]\(([^)#\s]+)", (ROOT / "docs" / idx).read_text(encoding="utf-8")):
                indexed.add(posixpath.normpath(posixpath.join(base, t)))
    missing = [rel for p in sorted((ROOT / "docs").rglob("*.md"))
               if (rel := p.relative_to(ROOT / "docs").as_posix()) not in ("README.md", "plans/README.md")
               and not rel.startswith(("archive/", "notes/")) and rel not in indexed]
    check("**docs/ 下每篇文档都在索引里**（archive/ 除外）",
          not missing, "没索引：" + "、".join(missing))
    # 链接 / 锚点落得到、plan 开头有状态块：dev_tools/check_docs.py（连同 publish.py 从项目约定复制来，第二行是版本日期）
    import check_docs as CD
    probs = CD.check(ROOT, False, CD.DEFAULT_EXCLUDE, CD.DEFAULT_PLANS, None)
    check("**check_docs 0 个问题**（相对链接与锚点落得到、plans/*-plan.md 开头有状态块）", not probs, probs[:5])
    # 只禁**索引条目**（`| [archive/...]` 开头的行）；正文里提一句"过程见 archive/xxx"
    # 是应该的——归档不是藏起来。
    check("**归档的计划书不单独占一行索引**（现行契约看正式文档）",
          not [ln for idx in ("README.md", "plans/README.md") if (ROOT / "docs" / idx).is_file()
               for ln in (ROOT / "docs" / idx).read_text(encoding="utf-8").splitlines()
               if ln.startswith(("| [archive/", "| [../archive/"))])


def t_paths() -> None:
    """代码根 / 产物根：**worktree 解析回主 checkout**（照搬 finesub 的 paths.py）。

    这个项目里它是硬需求：`out/`、`tmp/`、素材、`explore 下各实验的 venv` 全是 ignore 的，
    linked worktree 里根本不存在——不解析回去，槽里就跑不了任何一条管线。
    """
    print("paths")
    from flowocr import paths

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        main, slot = d / "main", d / "main" / ".worktrees" / "s1"
        (main / ".git" / "worktrees" / "s1").mkdir(parents=True)
        slot.mkdir(parents=True)
        (slot / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 's1'}\n",
                                   encoding="utf-8")
        # 2026-09-21 起"是不是 checkout"要 .git + pyproject.toml 两样（flowocr.paths 文件头的三形态表）
        for r in (main, slot):
            (r / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        check("有 .git + pyproject 才算 checkout（主 / 槽都算）",
              paths.is_checkout(main) and paths.is_checkout(slot))
        check("linked worktree 解析回主 checkout",
              paths.main_checkout(slot) == main, paths.main_checkout(slot))
        check("产物根跟着走", paths.data_root(slot) == main)
        check("认得出自己在槽里", paths.is_linked_worktree(slot))

        # 主 checkout 的 .git 是**目录**，不是文件
        (main / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        check("主 checkout 不会被当成槽", paths.main_checkout(main) is None)
        check("主 checkout 的产物根就是它自己", paths.data_root(main) == main)

        # **`gitdir:` 指向别处的仓库不算**——少了这道判据，随便一个 .git 文件
        # 都能把产物写到别人家去（finesub 的 `_main_worktree_root` 同款判据）
        odd = d / "odd"
        odd.mkdir()
        (odd / ".git").write_text("gitdir: /somewhere/else\n", encoding="utf-8")
        check("gitdir 不是 .git/worktrees/<slot> 形状就不认",
              paths.main_checkout(odd) is None)
        bare = d / "bare"
        bare.mkdir()
        check("没有 .git 也不炸", paths.main_checkout(bare) is None)

        # 第三形态：安装后运行（包在 site-packages 里，往上三层没有 checkout）→ 用户数据目录
        check("不是 checkout 就当安装态：产物根落到用户数据目录",
              not paths.is_checkout(bare) and paths.data_root(bare) == paths.user_data_root()
              and paths.user_data_root().name == "flowocr", paths.data_root(bare))
        # 只有 .git 没有 pyproject 也不算 checkout（site-packages 上一层碰巧在别的仓库里的情形）
        half = d / "half"
        (half / ".git").mkdir(parents=True)
        check("只有 .git 没有 pyproject 不算 checkout", not paths.is_checkout(half))
        # `FLOWOCR_DATA_ROOT` 三种形态下都能覆盖
        import os
        saved = os.environ.get("FLOWOCR_DATA_ROOT")
        os.environ["FLOWOCR_DATA_ROOT"] = str(d / "override")
        try:
            check("FLOWOCR_DATA_ROOT 在槽 / 主 / 安装态下都赢",
                  {paths.data_root(slot), paths.data_root(main), paths.data_root(bare)}
                  == {(d / "override").resolve()})
        finally:
            if saved is None:
                del os.environ["FLOWOCR_DATA_ROOT"]
            else:
                os.environ["FLOWOCR_DATA_ROOT"] = saved

    check("代码根是这份代码自己所在的 checkout",
          (paths.CODE_ROOT / "src" / "flowocr" / "paths.py").exists() and paths.is_checkout())
    # **实现只有一份**：迁移期间 tools/paths.py 是 shim；2026-09-22 nightly 调用方全部改成包 import 之后 shim 删光，
    # tools/ 里不许再出现已迁模块的同名文件（不然 `sys.path` 上 tools/ 在前，旧文件会盖住正式包）
    import flowocr.paths as fp
    migrated = [p.stem for pkg in ("extract", "analyze", "artifacts", "output") for p in (ROOT / "src" / "flowocr" / pkg).glob("*.py")
                if p.stem != "__init__"] + ["paths", "provenance"]
    leftovers = [n for n in migrated if (ROOT / "dev_tools" / f"{n}.py").exists() or (ROOT / "explore" / "paddleocr" / f"{n}.py").exists()]
    check("已迁模块在 tools/ 和 paddleocr 实验目录 里没有同名文件（shim 已删、也没被人加回来）", not leftovers, leftovers)
    check("data_root 只有 flowocr.paths 一份实现", paths.data_root is fp.data_root)

    # **驱动脚本里执行的工具必须走 `$CODE`**：`cd` 之后 cwd 是**产物根**，
    # 相对路径会去跑主 checkout 那一份——可能是另一版、甚至没有这个 CLI，
    # 于是"没跑完的产物"被判成已完成。（这条我自己踩过：收尾脚本把带 `echo`
    # 的那行整行还原了，`run_full_ocr.sh` 的复用判据就这么指回了旧文件。）
    import re

    bad_lines = []
    for sh in sorted((ROOT / "dev_tools").glob("*.sh")):
        for i, ln in enumerate(sh.read_text(encoding="utf-8").splitlines(), 1):
            if ln.lstrip().startswith("#"):
                continue
            if re.search(r'(^|[;&|(]|\$\()\s*(python|bash) dev_tools/', ln):
                bad_lines.append(f"{sh.name}:{i}")
    check("驱动脚本没有一处**执行** `python dev_tools/` / `bash dev_tools/`（必须 `$CODE`）",
          not bad_lines, "、".join(bad_lines))

    # **驱动里按 `$CODE` / `$CODE_W` 拼出来的路径必须真的存在**（2026-09-24 整理复审抓到的）：
    # `tools/` 改名 `dev_tools/` 时，`"$CODE"/tools/arm_check.py` 这种写法没被改写脚本认出来，
    # `bash -n` 也查不出——A/B 驱动在起跑前就退出，体检那几行则静默失败、驱动照样返回成功。
    # 内联 Python 里裸 import 正式包的模块（`import tracksio`）同理：09-22 shim 删光之后它一直失败，缓存判定每次都退回重建。
    shells = sorted((ROOT / "dev_tools").glob("*.sh")) + sorted((ROOT / "explore").rglob("*.sh"))
    pkg_mods = {p.stem for p in (ROOT / "src" / "flowocr").rglob("*.py")} - {"__init__"}
    tool_mods = {p.stem for p in (ROOT / "dev_tools").rglob("*.py")}
    missing_paths, bare_pkg = [], []
    for sh in shells:
        if ".venv" in sh.parts or "_cache" in sh.parts:
            continue
        for i, ln in enumerate(sh.read_text(encoding="utf-8").splitlines(), 1):
            if ln.lstrip().startswith("#"):
                continue
            for m in re.finditer(r'\$\{?CODE(?:_W)?\}?"?/([\w.\-/]+)', ln):
                rel = m.group(1).rstrip("/")
                if "$" not in rel and not rel.startswith(".venv/") and not (ROOT / rel).exists():   # .venv 只在本地
                    missing_paths.append(f"{sh.relative_to(ROOT).as_posix()}:{i} {rel}")
            m = re.match(r"\s*(?:import (\w+)|from (\w+) import)", ln)
            name = m and (m.group(1) or m.group(2))
            if name and name in pkg_mods and name not in tool_mods:
                bare_pkg.append(f"{sh.relative_to(ROOT).as_posix()}:{i} {name}")
    check("**驱动里 `$CODE` / `$CODE_W` 拼出的路径都存在**（改目录名时 `bash -n` 查不出）",
          not missing_paths, missing_paths[:8])
    check("**驱动的内联 Python 不裸 import 正式包的模块**（要 `from flowocr.… import …`）",
          not bare_pkg, bare_pkg[:8])
    # 体检工具出错（不是报警，报警照样返回 0）要计进失败：原来路径错了体检静默失败、驱动照样报成功（owner 2026-09-24 定）
    unchecked = []
    for sh in sorted((ROOT / "dev_tools").glob("*.sh")):
        lines = sh.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("#") or not re.search(r"(track_health|usage1_health)\.py", ln):
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if "||" not in ln and not (ln.rstrip().endswith("\\") and "||" in nxt):
                unchecked.append(f"{sh.name}:{i + 1}")
    check("**驱动里调体检工具的地方都接住了失败**（`|| … FAILED`）", not unchecked, unchecked)
    # 用 `$CODE/.venv` 的驱动都把 PYTHONPATH 钉到 `$CODE/src`：槽里的 .venv 是指回主 checkout 的 junction 时，
    # editable 安装会导回主 checkout 的代码，而 provenance 照样记槽的 git_head 以外的东西（2026-09-26 审计）
    unpinned = [sh.name for sh in sorted((ROOT / "dev_tools").glob("*.sh"))
                if 'PY="$CODE/.venv' in (t := sh.read_text(encoding="utf-8")) and 'export PYTHONPATH="$(cd "$CODE"' not in t]
    check("用 `$CODE/.venv` 的驱动都把 PYTHONPATH 钉到 `$CODE/src`", not unpinned, unpinned)


def t_package() -> None:
    """正式包（project-structure 计划 §4）：`src/flowocr/` + `pyproject.toml`，
    指纹 / git_head 只有 `flowocr.provenance` 这一份实现。"""
    print("正式包")
    import flowocr
    import flowocr.provenance as P

    check("pyproject.toml 在、声明 src 布局",
          (ROOT / "pyproject.toml").is_file()
          and 'where = ["src"]' in (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    check("import flowocr 拿到的是 src/ 里这份（不是别处装的）",
          Path(flowocr.__file__).resolve().parent == (ROOT / "src" / "flowocr").resolve()
          and isinstance(flowocr.__version__, str))
    import re
    # 版本号只写在 pyproject.toml；CHANGELOG 最上面那个已发布的标题要是它，release workflow 发版时再核一遍 tag
    import tomllib
    ver = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    heads = [m.group(1) for m in re.finditer(r"^## (\S+?)(?=[ （]|$)", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)]
    released = next((h for h in heads if h != "未发布"), None)
    check("CHANGELOG 最上面那个已发布的标题与 pyproject.toml 的 version 相同", released == ver, (released, ver))
    # 只建有真代码的模块：空壳子包不许先摆上（§4）
    empties = [p.name for p in (ROOT / "src" / "flowocr").iterdir()
               if p.is_dir() and p.name != "__pycache__"
               and not any(f.suffix == ".py" and f.name != "__init__.py" for f in p.rglob("*"))]
    check("src/flowocr 下没有只有 __init__ 的空子包", not empties, empties)
    # 指纹的实现只有一份：build_tracks / ocr_args 的 code_fp 都转发到它
    from flowocr.analyze import build_tracks as bt
    from flowocr.extract import ocr_args
    bt_src = (ROOT / "src" / "flowocr" / "analyze" / "build_tracks.py").read_text(encoding="utf-8")
    oa_src = (ROOT / "src" / "flowocr" / "extract" / "ocr_args.py").read_text(encoding="utf-8")
    check("git_head / DIRTY_PATHS 只在 flowocr.provenance 里定义（build_tracks 只转发）",
          bt.git_head is P.git_head and bt.DIRTY_PATHS is P.DIRTY_PATHS
          and "def git_head" not in bt_src and "DIRTY_PATHS = " not in bt_src)
    check("code_fp 的 hash 只在 flowocr.provenance 里算（build_tracks / ocr_args 都不再自己 sha256）",
          "hashlib" not in bt_src.split("def code_fp")[1][:400] and "hashlib.sha256" not in oa_src)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a").mkdir()
        (d / "b").mkdir()
        (d / "a" / "m.py").write_bytes(b"x = 1\n")
        (d / "b" / "m.py").write_bytes(b"x = 1\n")
        check("路径进指纹：同一份内容换了位置就是另一个指纹（搬目录 = 换代码）",
              P.code_fp(("a/m.py",), d) != P.code_fp(("b/m.py",), d)
              and P.code_fp(("a/m.py",), d) == P.code_fp(("a/m.py",), d))
        check("不在仓库里 git_head 回 '?'（安装态没有 HEAD 可记）", P.git_head(d) == "?")
    # 安装态没有 src/：表里的 src/ 路径按包自己的位置解析（源码态是同一个文件，指纹不变）——2026-09-22 安装态第一次跑就栽在这
    check("指纹表的 src/ 路径按包位置解析，tools/ 路径按 checkout 解析",
          P.file_of("src/flowocr/paths.py") == P.PACKAGE_ROOT.parent / "flowocr" / "paths.py"
          and P.file_of("src/flowocr/paths.py").resolve() == (ROOT / "src" / "flowocr" / "paths.py").resolve()
          and P.file_of("dev_tools/refine_boundaries.py") == P.CODE_ROOT / "dev_tools" / "refine_boundaries.py")
    check("所有指纹表现在只剩 src/ 路径（安装态才算得出指纹）",
          all(f.startswith("src/") for m in (bt, ocr_args, importlib.import_module("flowocr.analyze.gamescript"), importlib.import_module("flowocr.analyze.scriptmatch"),
                                            importlib.import_module("flowocr.analyze.refine_boundaries"))
              for f in m.CODE_FP_FILES))
    # obs 那张表的指纹也走同一份实现：表 + 实现给出的值必须一致
    check("ocr_args.code_fp() == provenance.code_fp(ocr_args.CODE_FP_FILES)",
          ocr_args.code_fp() == P.code_fp(ocr_args.CODE_FP_FILES))


def t_portable_paths() -> None:
    """产物 provenance 里的路径不带用户名（release-plan F8）：`portable` 记、`local` 读回。"""
    print("产物里的路径")
    from flowocr import paths as fp
    from flowocr.provenance import local, portable
    dr, home = fp.data_root(), Path.home()
    inside = dr / "out" / "x" / "x.jsonl"
    elsewhere = home / "Videos" / "clip.mp4"
    check("portable：数据根下记相对数据根、用户目录下记 ~/…、相对路径和参数值原样、None 原样",
          portable(str(inside)) == "out/x/x.jsonl" and portable(str(elsewhere)) == "~/Videos/clip.mp4"
          and portable("out/x.jsonl") == "out/x.jsonl" and portable("0") == "0" and portable(None) is None,
          f"{portable(str(inside))} / {portable(str(elsewhere))}")
    check("local：~ 展开、当前目录没有的相对路径按数据根拼、空串原样（让'找不到 obs'报在原处）",
          local("~/Videos/clip.mp4") == elsewhere and local("out/nope-xyz/x.jsonl") == dr / "out/nope-xyz/x.jsonl"
          and str(local("")) == ".")


def t_runtime_backend() -> None:
    """推理后端装对了没有（2026-10-02，drop-paddle 计划第 7 步）：装成包时 `[nvidia]` / `[cpu]` 的互斥进不了元数据，
    `ffcheck.require_runtime` 起跑时查——一个都没有、两个都在都退出并说怎么修；非 Windows 只警告、照跑（owner 2026-10-02）。"""
    print("推理后端检查")
    import contextlib
    import io
    import importlib.metadata as md
    from unittest import mock

    from flowocr.extract import ffcheck

    def run(installed, plat="win32"):
        def version(d):
            if d in installed:
                return "1.29.0"
            raise md.PackageNotFoundError(d)
        out = io.StringIO()
        with mock.patch.object(ffcheck.importlib.metadata, "version", version), \
             mock.patch.object(ffcheck.sys, "platform", plat), contextlib.redirect_stdout(out):
            try:
                ffcheck.require_runtime()
                return "ok", out.getvalue()
            except SystemExit as exc:
                return str(exc), out.getvalue()

    none, _ = run(())
    both, _ = run(("onnxruntime", "onnxruntime-gpu"))
    gpu, _ = run(("onnxruntime-gpu",))
    cpu, _ = run(("onnxruntime",))
    linux, warn = run(("onnxruntime",), "linux")
    check("没装 onnxruntime -> 退出并说选哪个 extra；两个都装 -> 退出并说只留一个；只装一个 -> 照跑",
          "flowocr[nvidia]" in none and "flowocr[cpu]" in none and "只留一个" in both and gpu == cpu == "ok", (none, both, gpu, cpu))
    check("非 Windows：打一行警告、照跑（不报错）", linux == "ok" and "只在 Windows 上验证过" in warn, (linux, warn))


def t_models_offline_pack() -> None:
    """模型离线包（2026-10-02，drop-paddle 计划第 7 步）：`flowocr-models install` 只认表里的模型文件、逐个核 sha256，
    对不上 / 不认识的文件都不写；`LICENSES/` 一并装进模型根。"""
    print("模型离线包")
    import os
    import zipfile
    from unittest import mock

    from flowocr import models as M

    det = M.MODELS["det"]
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        with mock.patch.dict(os.environ, {"FLOWOCR_MODELS": str(td / "models")}):
            def mk(name, entries):
                z = td / name
                with zipfile.ZipFile(z, "w") as f:
                    for k, v in entries.items():
                        f.writestr(k, v)
                return z

            def err(z):
                try:
                    M.install(z)
                    return ""
                except SystemExit as exc:
                    return str(exc)

            bad_sha = err(mk("a.zip", {f"{det.dir.name}/inference.yml": b"not the real yml"}))
            unknown = err(mk("b.zip", {"evil/../x.onnx": b"x"}))
            escapes = [err(mk(f"e{i}.zip", {n: b"x"})) for i, n in
                       enumerate(("LICENSES/../x", "LICENSES/..\\..\\x", "LICENSES/a/b", "LICENSES/C:x"))]
            ok = M.install(mk("c.zip", {"LICENSES/NOTICE.md": b"n"}))
            check("离线包：LICENSES 下穿出模型根 / 带子目录 / 带盘符的名字都拒绝，模型根外什么都没写",
                  all("不认识的文件" in e for e in escapes) and not (td / "x").exists() and not (td / "models" / "x").exists(),
                  escapes)
            check("离线包：sha256 对不上的模型文件不写、不认识的文件拒绝、LICENSES 装进模型根",
                  "sha256 对不上" in bad_sha and not (det.dir / "inference.yml").exists()
                  and "不认识的文件" in unknown and len(ok) == 1 and (td / "models" / "LICENSES" / "NOTICE.md").is_file(),
                  (bad_sha, unknown, ok))
