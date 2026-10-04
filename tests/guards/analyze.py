"""阶段 2（建轨、聚类、常驻 UI、名牌、回抠结算、聚类的尺）的守卫。"""
from __future__ import annotations

import importlib  # noqa: F401
import inspect  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

from guards._common import *  # noqa: F401,F403


def t_panel_like() -> None:
    """面板样 cue 的判定（`build_tracks.panel_like`）+ `scriptmatch.feed_from_doc` 只按标取舍。

    判据是量出来的（matcher 计划 §9.1）：行数 ≥5 且框并集高 > 0.15 画面高。
    这里守两件事：**正常的多行字幕不许被判成面板**（"名牌 + 两行"就是 3 行，
    按行数单独剔会塌 −73），以及**投影层不许自己重判**（artifacts.md 第 1 条）。
    """
    print("build_tracks.panel_like / scriptmatch.feed_from_doc")
    import copy as _copy0

    from flowocr.analyze import build_tracks as bt
    from flowocr.analyze import scriptmatch as SM

    def evs(boxes):
        return [{"box": b, "text": f"行{i}"} for i, b in enumerate(boxes)]

    H = 1080
    # 正常字幕：名牌 + 两行正文，挤在画面下方一小条里
    sub = evs([[400, 830, 900, 865], [300, 880, 1600, 925], [300, 930, 1500, 975]])
    check("名牌 + 两行字幕不是面板样", not bt.panel_like(sub, H))
    # 面板：6 行、竖着铺开 1/3 屏
    panel = evs([[300, 200 + i * 60, 1600, 240 + i * 60] for i in range(6)])
    check("6 行、竖跨 0.3 屏 = 面板样", bt.panel_like(panel, H))
    # 行数够但挤在一起（密排小字）→ 不算：框高不过门
    tight = evs([[300, 900 + i * 8, 1600, 906 + i * 8] for i in range(6)])
    check("行数够但框高不过门就不是面板样", not bt.panel_like(tight, H))
    check("行数不够一律不是面板样",
          not bt.panel_like(evs([[0, 0, 100, 100], [0, 500, 100, 600]]), H))
    check("画面高未知（0）时不判面板", not bt.panel_like(panel, 0))

    # 投影：feed_from_doc 只看标，不自己算
    doc = {"size": [1920, H], "provenance": {"main_track": "main"},
           "events": [{"text": "正文", "box": [300, 880, 1600, 925]},
                      {"text": "面板行", "box": [300, 200, 1600, 560]}],
           "tracks": [
               {"id": "main", "kind": "main", "srt": "x-main.srt", "ui_lines": [],
                "cues": [{"id": "mc0", "t_start": 0, "t_end": 1_000_000, "events": [0]}]},
               {"id": "r00", "kind": "region", "label": "dialogue-or-caption",
                "srt": "x-region00.srt", "ui_lines": [],
                "cues": [{"id": "c0", "t_start": 0, "t_end": 1_000_000, "events": [0]},
                         {"id": "c1", "t_start": 2_000_000, "t_end": 3_000_000,
                          "events": [1], "panel_like": True}]},
               {"id": "r01", "kind": "region", "label": "noise",
                "srt": "x-region01-noise.srt", "ui_lines": [],
                "cues": [{"id": "n0", "t_start": 4_000_000, "t_end": 5_000_000, "events": [0]}]}]}
    cues, evs_of, panel_of = SM.feed_from_doc(doc, "nonoise")
    check("feed nonoise 排除主轨那条聚合轨（不然同一段文字喂两遍）", len(cues) == 2,
          f"{len(cues)} 条")
    check("feed nonoise 不含 noise 轨", all("noise" not in c.source for c in cues))
    check("feed all 含 noise 轨", len(SM.feed_from_doc(doc, "all")[0]) == 3)
    check("面板标随 cue 一起返回（strict 用）", panel_of == [False, True], f"{panel_of}")
    check("cue 的来源轨记在 Cue.source 上（归因用）",
          {c.source for c in cues} == {"x-region00.srt"})
    kept, _, _ = SM.feed_from_doc(doc, "nonoise", "keep")
    dropped, _, _ = SM.feed_from_doc(doc, "nonoise", "drop")
    check("--feed-panel drop 按标剔掉面板样 cue", len(kept) - len(dropped) == 1,
          f"{len(kept)} -> {len(dropped)}")
    check("drop 之后剩下的是正文那条", len(dropped) == 1 and dropped[0].lines == ("正文",))
    check("evs_of 和 cue 一一对应（叠加层要拿框）", len(evs_of) == len(cues))

    # **喂入是白名单，不是黑名单**（2026-09-20，owner 和复审同时指出的真回归）：
    # `kind="nameplate"` 轨和区域轨**装的是同一批 run**（build_tracks 有意让它重叠），
    # 原来这里只排 `kind == "main"`，名牌于是被喂第二遍、而且是**独立成条**的形式——
    # audit-3 记过代价：只有名字的 cue 会去抢含名字的剧本行，**重复认领涨 2.2~7.0 倍**。
    # 游戏素材的用途 2 默认就是 `--feed nonoise`，这条路是通的。
    withnp = _copy0.deepcopy(doc)
    withnp["tracks"].append(
        {"id": "np", "kind": "nameplate", "label": "nameplate", "region": -1,
         "srt": "x-nameplate.srt", "ui_lines": [],
         "cues": [{"id": "p0", "t_start": 0, "t_end": 1_000_000, "events": [0]}]})
    for mode in ("nonoise", "all", "main"):
        a = len(SM.feed_from_doc(doc, mode)[0])
        b = len(SM.feed_from_doc(withnp, mode)[0])
        check(f"加一条名牌轨，`--feed {mode}` 的条数不变（同一批 run 不许喂两遍）", a == b,
              f"{a} -> {b}")

    # **三种主轨形态都不许丢真实区域**（2026-09-18 审计的 P1）：`build_tracks` 只在真的并了带时
    # 才另建一条 `kind="main"` 的聚合轨；**没并带时 `provenance.main_track` 直接指向一条真实区域**
    # （`r00` 那种）。原来按 `id == main_track` 排，于是只有一条字幕区域的产物上全量喂入输出 0 条。
    import copy as _copy
    no_agg = _copy.deepcopy(doc)
    no_agg["tracks"] = [tr for tr in no_agg["tracks"] if tr.get("kind") != "main"]
    no_agg["provenance"]["main_track"] = "r00"          # 没并带：主轨就是那条真实区域
    got, _, _ = SM.feed_from_doc(no_agg, "nonoise")
    check("**没并带时不许把真实主区域排掉**（审计 P1：那会把正文整条扔了）", len(got) == 2,
          f"{len(got)} 条")
    only_one = _copy.deepcopy(no_agg)
    only_one["tracks"] = [tr for tr in only_one["tracks"] if tr["id"] == "r00"]
    check("只有一条字幕区域时全量喂入也要出 cue（原来是 0 条）",
          len(SM.feed_from_doc(only_one, "nonoise")[0]) == 2)
    no_main = _copy.deepcopy(no_agg)
    no_main["provenance"]["main_track"] = ""            # 挑不出主轨，但屏幕上仍有文字
    check("挑不出主轨的产物照样能全量喂入", len(SM.feed_from_doc(no_main, "nonoise")[0]) == 2)
    # 标是 build_tracks 打的：把标去掉，drop 就不该剔（投影层不许自己重判）
    import copy
    doc2 = copy.deepcopy(doc)
    for tr in doc2["tracks"]:
        for c in tr["cues"]:
            c.pop("panel_like", None)
    check("没有标时 drop 不许自己重判", len(SM.feed_from_doc(doc2, "nonoise", "drop")[0]) == 2)


def t_drop_ui() -> None:
    """常驻 UI 短行的剔除：会响，但**不能把说话人名牌也剔了**。"""
    print("build_tracks.drop_persistent_ui")
    from flowocr.analyze import build_tracks as bt

    class R:                      # 只用到 .text
        def __init__(self, t): self.text = t

    def segs(rows):               # rows: 每条 cue 的行列表
        return [(i * 1000, i * 1000 + 500, [R(t) for t in r]) for i, r in enumerate(rows)]

    # `Auto` 在 100% 的 cue 里 -> 剔；名字只在 30% -> 留
    # 正文每条都不同——**这很重要**：判据是"短行在多少条 cue 里出现"，
    # 拿同一句台词复制 10 遍当样本，它自己就会被判成 UI（第一版测试就是这么写错的）。
    rows = [["Auto", f"这是第{i}句台词"] for i in range(30)]
    rows += [["Auto", "階堂ヒロ", f"名牌那条第{i}句"] for i in range(13)]
    out = bt.drop_persistent_ui(segs(rows), 0.5)
    texts = [[r.text for r in a] for _, _, a in out]
    check("常驻 UI 行被剔掉", all("Auto" not in t for t in texts))
    check("30% 的名牌行被留下", any("階堂ヒロ" in t for t in texts))
    check("cue 数不变（每条都还有正文）", len(out) == len(rows), f"{len(out)} vs {len(rows)}")
    check("名牌占比 13/43 = 30% < 0.5 所以留下",
          sum(1 for t in texts if "階堂ヒロ" in t) == 13)

    # 整条只有 UI 的 cue 要整条删掉，不能留个空 cue
    rows2 = [["Auto"]] * 4 + [["Auto", f"第{i}句"] for i in range(6)]
    out2 = bt.drop_persistent_ui(segs(rows2), 0.5, min_support=1)
    check("只剩 UI 的 cue 被整条删掉", len(out2) == 6, f"得到 {len(out2)}")

    # **绝对条数下限**：小区域不能因为"这行占了 100%"就被整个清空。
    # 这正是上一版的病：yuka 五部产物里出现 89-139 个空的区域 SRT，
    # zh-news 60 秒片段上 23 个区域清空 11 个（methodology-audit-2 报告）。
    check("默认 min_support=20 时，10 条的小区域一行都不剔",
          len(bt.drop_persistent_ui(segs(rows2), 0.5)) == len(rows2),
          f"得到 {len(bt.drop_persistent_ui(segs(rows2), 0.5))}")
    tiny = [["米"]]                                   # 1 条 cue 的区域
    check("1 条 cue 的区域不会被整个清空",
          len(bt.drop_persistent_ui(segs(tiny), 0.5)) == 1)
    check("支持度够了就照剔（Auto 出现 30 次）",
          "Auto" not in {r.text for _, _, a in
                         bt.drop_persistent_ui(segs(rows), 0.5) for r in a})
    check("persistent_ui_lines 报的就是被删的那些行",
          bt.persistent_ui_lines(segs(rows), 0.5) == {"Auto"},
          str(bt.persistent_ui_lines(segs(rows), 0.5)))

    # --ui-min-onshare：全片尺度的地板（gamestream-first-look 报告 §10.3）
    # 上面这 43 条 cue 每条 500 µs，`Auto` 在场共 21,500 µs。
    check("min_onshare 默认 0 时行为不变",
          bt.persistent_ui_lines(segs(rows), 0.5, min_onshare=0.0) == {"Auto"})
    check("给了 min_onshare 却没给 film_us 要报错，不许拿区域跨度顶替",
          raises(lambda: bt.persistent_ui_lines(segs(rows), 0.5, min_onshare=0.3)))
    check("同一批 cue：片长 43,000 µs（在场 50%）时 `Auto` 仍判 UI",
          bt.persistent_ui_lines(segs(rows), 0.5, min_onshare=0.3,
                                 film_us=43_000) == {"Auto"})
    check("**同一批 cue、片长长 10 倍（在场 5%）就不再判 UI** —— 这正是整片上塌掉的那件事",
          bt.persistent_ui_lines(segs(rows), 0.5, min_onshare=0.3,
                                 film_us=430_000) == set())

    # 阈值 0 = 关
    check("min_share<=0 时原样返回", len(bt.drop_persistent_ui(segs(rows), 0)) == len(rows))

    # 长行不算 UI，哪怕它每条都在（常驻旁白不该被当按钮删掉）
    long_rows = [["这是一句很长的常驻说明文字", f"第{i}句台词"] for i in range(10)]
    out3 = bt.drop_persistent_ui(segs(long_rows), 0.5)
    check("超过 6 字的行不判为 UI",
          all(len(a) == 2 for _, _, a in out3), "长行被误删了")


def t_punct() -> None:
    """`is_punct_only`：疑似真漏归因的分界线，两档漏条率差 54 倍，判错了结论就反。"""
    print("evalkit.is_punct_only")
    for s in ("……！", "――", "…………。", "っ……！", "！！", "(……)", "  "):
        check(f"{s!r} 判为纯标点", evalkit.is_punct_only(s))
    for s in ("エマくん。", "……っと言った", "A", "1"):
        check(f"{s!r} 不判为纯标点", not evalkit.is_punct_only(s))
    check("ASS 换行符先去掉再判", evalkit.is_punct_only("……" + chr(92) + "N！"))
    check("median 偶数取两中间数的平均", evalkit.median([1, 2, 3, 4]) == 2.5)
    check("median 奇数取中间那个", evalkit.median([5, 1, 3]) == 3)

    # 三档分类（owner 2026-09-06）。**内容字数不是字符数**：
    # `…………。` 有 6 个字符、0 个内容字——历史上的"行长分档"就栽在这上面。
    check("内容字数不数标点", evalkit.content_len("…………。") == 0
          and evalkit.content_len("エマは……") == 3, evalkit.content_len("エマは……"))
    P, T, C = evalkit.CLASSES
    for s, want in (("……！", P), ("――", P), ("っ……！", P),
                    ("え？", T), ("はい。", T), ("大", T),
                    ("エマは……", C), ("君はどうして嘘をつくんだ。", C)):
        check(f"{s!r} 归到 {want}", evalkit.triviality(s) == want, evalkit.triviality(s))
    check("三档名字是唯一口径（别在工具里各写各的）", len(evalkit.CLASSES) == 3)


def t_purity() -> None:
    """`probe_slot_merge.purity`：跨层比较的那个量必须**按 run 数**，不能按组数。

    为什么要测（methodology-audit-2 报告 §3）：三层产物的组大小差两个数量级
    ——行级组在一个 60 秒窗里只有几条 run，槽位是全片的。
    第一版按"多少个组是纯的"数，层 1 必然赢，**和算法好坏无关**，
    而这种偏差不会报错，只会打出一张看着很合理的表。
    """
    print("\n[probe_slot_merge.purity]")
    from probe_slot_merge import purity

    # 一个 100 条的混合组（名字 10 条）+ 十个 1 条的纯名牌组
    groups = [list(range(100))] + [[100 + i] for i in range(10)]
    flag = [i < 10 or i >= 100 for i in range(110)]
    got, n_grp, med = purity(groups, flag, 0.5)
    check("按 run 数算：混合组里的 10 条不算『分出来了』", got == 10, got)
    check("纯组数只是参考，不参与那个比例", n_grp == 10, n_grp)
    check("占比中位按名字 run 加权（10 条 0.1 vs 10 条 1.0）", med == 0.55, med)
    # 全在一个大组里 = 完全没分出来
    got2, _, med2 = purity([list(range(110))], flag, 0.5)
    check("全并成一个组时纯度为 0", got2 == 0, got2)
    check("没有名字的组不进中位数统计", purity([[0, 1]], [False, False], 0.5) == (0, 0, 0.0))


def t_nameplate() -> None:
    """挑名牌的几何判据：**主角的名牌不能被当成水印排掉**。

    存在的理由（methodology-audit-2 报告 §3.5）：原判据用
    `dominant_share <= 0.5` 排水印，而主角 `階堂ヒロ` 占那块区域 68-85%，
    于是 f3/f4/f5 主角的名牌整块被排除，召回卡在 40-51%，
    **而这件事在任何自检里都不会响**——它只是少挑了一个区域。
    """
    print("[nameplate 判据]")
    import argparse

    from flowocr.analyze import nameplate

    ap = argparse.ArgumentParser()
    nameplate.add_args(ap)
    a = ap.parse_args([])

    def reg(i, **kw):
        d = {"i": i, "label": "x", "n": 500, "cy": 0.72, "cx_std": 0.02, "h": 137,
             "pers": 0.7, "distinct": 0.08, "domi": 0.2, "dur": 4.5, "fill": 0.4,
             "score": 1.0, "runs": []}
        d.update(kw)
        return d

    main_r = reg(0, cy=0.824, h=41, score=999.0)
    plate = reg(1)
    hero = reg(2, domi=0.85)                    # 主角一个人占 85%
    auto = reg(3, dur=25.0, fill=0.9)           # `Auto` 按钮
    junk = reg(4, dur=0.5)                      # 误识的图标
    title = reg(5, cy=0.30)                     # 屏幕顶上的标题
    small = reg(6, h=45)                        # 字号和正文差不多
    got_main, picked = nameplate.pick([main_r, plate, hero, auto, junk, title, small], a)
    ids = {r["i"] for r in picked}
    check("主轨认的是 primary_score 最高的", got_main["i"] == 0)
    check("普通名牌挑中", 1 in ids)
    check("**主角独占 85% 的名牌也要挑中**（这是 domi 判据栽的地方）", 2 in ids, sorted(ids))
    check("常驻按钮（时长 25s）不挑", 3 not in ids)
    check("误识图标（时长 0.5s）不挑", 4 not in ids)
    check("屏幕顶上的标题不挑（dy 上界不能省）", 5 not in ids)
    check("字号和正文差不多的不挑", 6 not in ids)

    # 贴法：**时间重叠最大**那条，不是"起点最近"也不是"覆盖起点的最后一条"
    # （speaker-name-line 报告：那两种只有 73.1% / 97.2% 对）
    C = srtio.Cue
    main = [C(10.0, 12.0, ("台词甲",)), C(12.5, 16.0, ("台词乙",))]
    names = [C(9.5, 12.2, ("桜羽エマ",))]        # 和甲重叠 2.0s、和乙重叠 0
    blocks, tagged = nameplate.attach(main, names)
    check("名牌贴进重叠最大的那条", blocks[0][2] == ["桜羽エマ", "台词甲"], blocks[0][2])
    check("不重叠的那条不动", blocks[1][2] == ["台词乙"], blocks[1][2])
    check("贴上几条要报出来", tagged == 1, tagged)
    check("cue 数不变（贴进去 != 多一条）", len(blocks) == len(main))
    dup = nameplate.attach([C(10.0, 12.0, ("桜羽エマ", "台词甲"))], names)
    check("正文里已经粘着这个名字就不重复贴", dup[1] == 0, dup[1])


def t_persistence_invariance() -> None:
    """常驻度**只该跟着屏上占用变，不该跟着切分变**（methodology-audit-4 报告 C2）。"""
    print("常驻度对切分的不变性")
    from flowocr.analyze import build_tracks as bt

    W = 60_000_000                                   # 60 秒一个窗
    one = [bt.Run(box=[0, 0, 10, 10], text="x", t_start=0, t_end=180_000_000,
                  n_obs=360, conf=1.0)]
    three = [bt.Run(box=[0, 0, 10, 10], text="x", t_start=k * 60_000_000,
                    t_end=(k + 1) * 60_000_000, n_obs=120, conf=1.0) for k in range(3)]
    a = bt.covered_windows(one, [0], W)
    b = bt.covered_windows(three, [0, 1, 2], W)
    check("一条 180 秒的 run 覆盖三个窗", a == [0, 1, 2], a)
    check("**切成三条之后覆盖不变**（屏上占用没变）", b == a, (a, b))
    # 归属窗那套仍然是 1 个——它是给"别重复输出"用的，不该拿来数常驻度
    check("window_of 仍按中点给一个归属窗", bt.window_of(one[0], W) == 1,
          bt.window_of(one[0], W))


def t_refine_no_evidence() -> None:
    """回抠：**"没找到边界"不能写成"成功定位"**（methodology-audit-4 报告 C5）。

    `first_true`/`last_true` 找不到时返回区间端点，而 `finalize` 拿这个端点当边界
    还记 `refined=True`；常量图（空白/纯色）的归一化相关系数恒为 0，
    于是**一次都没观测到边界，也会写出一组"精确"的新时间**。
    """
    print("回抠的证据契约")
    import types
    # `refine_boundaries` 顶层 import cv2（候选 venv 才有）；这里测的是纯逻辑，
    # 把 cv2 桩掉即可，**测的仍是真实现**。
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.analyze import refine_boundaries as rb

    i, ok = rb.first_true(0, 5, lambda k: k >= 3)
    check("找到了：返回下标 + True", (i, ok) == (3, True), (i, ok))
    i, ok = rb.first_true(0, 5, lambda k: False)
    check("**没找到：第二个返回值是 False**（不是悄悄给端点）", (i, ok) == (5, False), (i, ok))
    i, ok = rb.last_true(0, 5, lambda k: k <= 2)
    check("last_true 找到了", (i, ok) == (2, True), (i, ok))
    i, ok = rb.last_true(0, 5, lambda k: False)
    check("last_true 没找到也说清楚", (i, ok) == (0, False), (i, ok))
    # 端到端走 finalize：全是常量图时终点观测不到，**原样退回**（反例原样见 audit-probes 实验里的 audit-core-probes.py C5）
    import numpy as np
    rs = dict(t_start=500_000, t_end=1_500_000, text="hello", box=[0, 0, 10, 10], n_obs=1)
    job = rb.make_job(rs, 500_000, 500_000)
    job["crops"] = {i: (np.zeros((10, 10), np.uint8), (0, 0, 10, 10)) for i in range(job["hi"] + 1)}
    short = rb.make_job(dict(t_start=1_000_000, t_end=2_000_000, t_full_sampled=1_500_000, text="x",
                             box=[0, 0, 10, 10], n_obs=2), 500_000, 1_000_000 / 60)
    check("**打字机窗（模板帧）不越过末次观测帧**（短条目模板落到字已消失的帧上，全字晚 45 帧）",
          short["hi_start"] <= short["e"], (short["hi_start"], short["e"]))
    rb.measure(job, .8)
    rb.apply(job, None, None)
    check("**常量图：终点没观测到就不记 refined、时间原样**",
          not job["refined"] and rs["t_start"] == 500_000 and rs["t_end"] == 1_500_000 and "t_full" not in rs,
          (job["refined"], rs))
    rng = np.random.default_rng(0)
    txt, bg = rng.integers(0, 255, (10, 10), dtype=np.uint8), rng.integers(0, 255, (10, 10), dtype=np.uint8)

    def end_how(gone_at, last_decoded=None):
        r = dict(t_start=500_000, t_end=1_500_000, text="hello", box=[0, 0, 10, 10], n_obs=3)
        j = rb.make_job(r, 500_000, 100_000)
        top = j["hi"] if last_decoded is None else last_decoded
        j["crops"] = {i: (txt if gone_at is None or i < gone_at else bg, (0, 0, 10, 10)) for i in range(top + 1)}
        rb.measure(j, .8)
        return j.get("measured", (None,) * 5)[4], j
    h_gone, j_gone = end_how(17)
    h_win, _ = end_how(None)
    h_eof, _ = end_how(None, last_decoded=j_gone["hi"] - 3)
    check("**开放边界**（开视频测）：最后还像的那帧之后窗里再没有真解到的帧——一路像到窗尾、或视频先结束（片尾还在屏上）——记 open；"
          "窗里看到它消失了照旧是量到（这张图上曲线先给出，curve）",
          h_gone in ("ncc", "curve") and (h_win, h_eof) == ("open", "open"), (h_gone, h_win, h_eof, j_gone["e"], j_gone["hi"]))

    # 建轨默认 `--refine auto`（owner 2026-09-25）：要**真的有**证据才结算；没有就原样、不开视频、不报错
    import contextlib
    import io
    import json
    from flowocr.analyze import build_tracks as bt
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        def obs_with(meta):
            p = d / f"o{len(list(d.iterdir()))}.jsonl"
            p.write_text(json.dumps({"_meta": meta}) + "\n", encoding="utf-8")
            return p
        real = obs_with({"src_fps": 60.0, "config": {"refine_fused": True}, "edge": {"on": 12}})
        noop = obs_with({"src_fps": 60.0, "config": {"refine_fused": True}, "edge": {"on": 0, "noop": True}})
        old = obs_with({"src_fps": 60.0, "config": {}})
        check("回抠的证据判定：配置开了融合、而且 edge 真有条数才算；空臂（noop）、旧 obs 都不算",
              rb.obs_evidence(real)[1] and not rb.obs_evidence(noop)[1] and not rb.obs_evidence(old)[1])
        with contextlib.redirect_stdout(io.StringIO()) as said:
            untouched = {"events": []}
            settled = bt.settle_refine(untouched, old, "t")
        check("建轨 --refine auto 碰上没证据的 obs：不结算、不动产物、打一行提示（产物停在采样级）",
              settled is False and untouched == {"events": []} and "没有在线回抠的证据" in said.getvalue(), said.getvalue())
        import tomllib
        scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
        check("回抠没有命令行入口（owner 2026-09-26：测量只在阶段 1、结算只在建轨，不另存 -tracks-refined.json）："
              "模块没有 main、pyproject 里没有 flowocr-refine",
              not hasattr(rb, "main") and "flowocr-refine" not in scripts, sorted(scripts))
        now_fp = bt.code_fp(rb.CODE_FP_FILES)
        check("评测驱动判建轨缓存：产物回抠过而回抠代码指纹和现在的不同就报原因（只改回抠代码时不静默复用）；没回抠过、指纹相同都不报",
              rb.refine_code_changed({}) is None
              and rb.refine_code_changed({"boundary_refined": {"code_fp": now_fp}}) is None
              and "对不上" in (rb.refine_code_changed({"boundary_refined": {"code_fp": "old"}}) or ""))


def t_typewriter_fuse() -> None:
    """打字机首字 / 全字的多信号融合（tools/typewriter_fuse.py）：投票、区间、两种"出现"的定义。"""
    print("typewriter_fuse")
    import numpy as np

    from flowocr.extract import typewriter_fuse as TF

    # 全片 OCR 打字速度的两道门（2026-09-24，ocr-regions §5.1 末尾：3 条假斜率把 yuka-f1 遮罩臂定成 163 ms/字）
    check("OCR 斜率：字数随时间严格增加才拟（停在 11 个字 2 秒、读少了的不是打字）",
          TF.ocr_slope([(0, 3), (500_000, 12), (1_000_000, 11), (1_500_000, 11)], 24) is None
          and TF.ocr_slope([(0, 3), (500_000, 12)], 24) is not None)
    import json
    from flowocr.analyze import refine_boundaries as RB
    _j = lambda: {"run": {"t_start": 0, "t_full_sampled": 1_000_000, "text": "あ" * 24}, "box": [0, 0, 100, 30]}
    _rows = lambda k: "\n".join(json.dumps({"t_us": t, "box": [0, 0, 100, 30], "text": "あ" * n})
                                for t, n in ((0, 3), (500_000, 12))) + "\n"
    with tempfile.TemporaryDirectory() as _d:
        _p = Path(_d) / "o.jsonl"
        _p.write_text('{"_meta": {}}\n' + _rows(0), encoding="utf-8")
        _few = RB.attach_ocr([_j() for _ in range(TF.OCR_SLOPES_MIN - 1)], _p)
        _enough = RB.attach_ocr([_j() for _ in range(TF.OCR_SLOPES_MIN)], _p)
    check(f"全片 OCR 打字速度：拟得出斜率的少于 {TF.OCR_SLOPES_MIN} 句就不给（退回逐字格斜率），够了才给",
          _few is None and _enough is not None, (_few, _enough))

    _bad = TF.ocr_fit([(1_000_000, 3), (1_500_000, 12), (2_000_000, 11)], 24, 2_500_000, 3_000_000, 500_000, 20_000)
    _one = TF.ocr_fit([(1_000_000, 3)], 24, 2_500_000, 3_000_000, 500_000, 20_000)
    check("单句外推：中途读数字数不严格增加时不拟直线，按只有第一个读数 + 全片速度处理（同 ocr_slope 那道门）",
          _bad == _one, (_bad, _one))
    from flowocr.extract.reuse_v2 import same_text
    check("回补比文本去掉标点和空白（只差一个 `、` 的重读不算改了历史）；两边都只剩标点时仍逐字比",
          same_text("華を添えるが如く、", "華を添えるが如く") and not same_text("彼女の", "その")
          and same_text("……！", "……！") and not same_text("……！", "？？"))

    v, how = TF.fuse([100_000, 110_000, 400_000], 0, 500_000, 0)
    check("两路同意取那一簇，离群的一路不参与", how == "agree" and 100_000 <= v <= 110_000, (v, how))
    v, how = TF.fuse([None, 200_000], 0, 500_000, 0)
    check("只剩一路记 single", (v, how) == (200_000, "single"), (v, how))
    v, how = TF.fuse([900_000], 0, 500_000, 300_000)
    check("**区间外的候选丢掉**、退回 fallback 并记 none", (v, how) == (300_000, "none"), (v, how))
    # 外推值落在区间下界上不投票（2026-09-24，ocr-regions §5.1：三路像素各成一簇时它靠"平票取靠前"赢下、首字早一个采样间隔）
    _of = TF.ocr_fit
    try:
        TF.ocr_fit = lambda pts, n, t0, tf, span, v: (t0 - span, None)
        _ts = list(range(0, 1_000_001, 16_667))
        _sig = {"ts": _ts, "n": 10, "cell": [700_000, 950_000], "first_old": 800_000, "first_rise": 866_667,
                "full_glyph": 950_000}
        _pinned = TF.combine(_sig, 1_000_000, 1_000_000, 500_000, [], None, None)
        TF.ocr_fit = lambda pts, n, t0, tf, span, v: (t0 - 150_000, None)
        _free = TF.combine(_sig, 1_000_000, 1_000_000, 500_000, [], None, None)
    finally:
        TF.ocr_fit = _of
    check("combine：外推首字压在区间下界上不投票（像素三路平票时取像素那边）；不在下界上的外推值照旧排第一",
          _pinned["first"] == TF.snap(_ts, 866_667) and _free["first"] == TF.snap(_ts, 850_000), (_pinned, _free))
    check("last_onset 取最后一段持续到末尾的过门起点", TF.last_onset([1, 1, 0, 1, 1]) == 3)
    check("末帧不过门 = 没找到（不给端点）", TF.last_onset([1, 1, 0]) is None)
    check("字一帧弹出：开始出现 = 定稿那一帧", TF.rise_onset([0, 0, 0, 1, 1]) == 3)
    check("逐字淡入：退到淡入起点，不停在定稿",
          TF.rise_onset([0.1, 0.1, 0.2, 0.4, 0.7, 1, 1]) == 2)
    a, b = TF.theil_sen(range(6), [10, 12, 14, 16, 100, 20])
    check("Theil–Sen 不被一个被挡住的字格带偏", abs(b - 2) < 1e-9 and abs(a - 10) < 1e-9, (a, b))
    tpl = np.zeros((20, 20), np.uint8)
    tpl[5:15, 8:12] = 255
    s = TF.glyph_score(np.stack([np.zeros_like(tpl), tpl]), tpl)
    check("亮字判亮；没字的帧一致率 0、长全的帧 1", TF.polarity(tpl) and s[0] == 0 and s[1] == 1, str(s))
    first, full = TF.ocr_fit([(1_000_000, 5), (1_500_000, 10)], 20, 1_000_000, 2_500_000, 500_000, None)
    check("两个中途读数外推到第 0.5 / N−0.5 个字",
          abs(first - 550_000) < 1 and abs(full - 2_450_000) < 1, (first, full))
    ts = list(range(0, 3_000_000, 16_667))
    sig = {"ts": ts, "n": 10, "first_old": None, "first_rise": 1_000_000, "full_glyph": 2_400_000, "cell": None}
    got = TF.combine(sig, 1_000_000, 1_500_000, 500_000, [], None, None)
    check("**全字只防晚很多：不晚于 OCR 读全 + 10 帧**（远晚的候选不采纳）",
          got["full"] <= 1_500_000 + TF.LATE_CAP_US, got)
    # 证据网格带锚点舍入（毫秒时间基，2026-09-24 --fps 2.2 上撞到）：ts 整体晚 333 µs 时，吸附后的首字仍不晚于 t_start
    _sig333 = dict(sig, ts=[t + 333 for t in ts], first_rise=1_000_000)
    _g333 = TF.combine(_sig333, 1_000_000, 1_500_000, 500_000, [], None, None)
    check("**首字吸附到证据网格之后仍不晚于 t_start**（网格是锚点折算的估计，t_start 是实测 pts）",
          _g333["first"] <= 1_000_000 and _g333["full"] >= _g333["first"], _g333)
    # 结尾：10 帧完全清楚 → 3 帧淡出 → 空白 → 同位置出下一句（亮字，暗底，4 个字格）
    frames = []
    for k in range(20):
        f = np.full((20, 44), 20, np.uint8)
        alpha = 1.0 if k < 10 else max(0.0, 1 - (k - 9) / 4) if k < 14 else (1.0 if k >= 16 else 0.0)
        for c in range(4):
            f[5:15, 3 + c * 10:8 + c * 10] = int(20 + 200 * alpha)
        frames.append(f)
    lf, gone = TF.end_keyframes(np.stack(frames), [k * 16_667 for k in range(20)], (2, 2, 42, 18), "abcd")
    check("**结尾：完全显示结束 = 最后一帧全清楚，完全消失 = 之后第一帧全看不见（下一句紧接着出也不影响）**",
          lf == 9 * 16_667 and gone == 13 * 16_667, (lf, gone))
    import typewriter_eval as TE
    late20, early20 = 20 / 60 * 1e6, -20 / 60 * 1e6
    check("**代价按『缩短清晰显示』那一侧重罚**：全字晚 20 帧 ×5、完全消失早 20 帧 ×5，反方向 ×2",
          TE.cost_of(late20, "full") == 60 and TE.cost_of(early20, "full") == 30
          and TE.cost_of(early20, "gone") == 60 and TE.cost_of(late20, "gone") == 30,
          (TE.cost_of(late20, "full"), TE.cost_of(early20, "full"), TE.cost_of(early20, "gone"), TE.cost_of(late20, "gone")))
    te_doc = {"provenance": {"main_track": "r00"}, "tracks": [{"id": "r00", "kind": "region", "cues": [{"events": [0, 1]}]}],
              "events": [{"id": 0, "text": "あ", "t_start": 1_000_000, "t_end": 3_000_000, "t_end_how": "open"},
                         {"id": 1, "text": "い", "t_start": 5_000_000, "t_end": 7_000_000, "t_end_how": "ncc"}]}
    te = TE.score(te_doc, {"events": [{"text": "あ", "first": 1.0, "gone": 4.0}, {"text": "い", "first": 5.0, "gone": 7.0}]},
                  False, "")
    check("评测：完全消失是开放边界（t_end_how = open，只是下界）的单独数、不进分档和代价；量到的照常打",
          te["open"] == 1 and sum(te["bins"]["gone"].values()) == 1 and te["cost"]["gone"] == 0, te)
    first, _ = TF.ocr_fit([(1_000_000, 15)], 20, 1_000_000, 2_500_000, 500_000, 100_000)
    check("**外推出界时夹进 OCR 证据给的区间**（首字不早于上一个采样点）", first == 500_000, first)
    # `signals` 里"整框像不像窗口首帧"那一段 2026-09-16 改成整叠一次算（原来逐帧调 ncc，占 signals 的 2/3），
    # 但**累加的精度从 float32 变成了 float64**，当时只在 yuka-f1 一段上核过 497 条事件逐帧相同。
    # 这里拿逐帧 `TF.ncc` 当参照实现对账，包括故意把相关系数摆在 EMPTY_THR 上下的帧（2026-09-17 审计补的）
    rng = np.random.default_rng(3)
    base = rng.integers(0, 255, (12, 26), dtype=np.uint8)
    bad = 0
    for trial in range(40):
        st = [base]
        for k in range(1, 9):
            noise = rng.integers(0, 255, base.shape, dtype=np.uint8)
            w = 0.02 * trial + 0.01 * k              # 逐步从"几乎没变"混到"完全不像"，必然跨过 0.90
            st.append((base * (1 - w) + noise * w).astype(np.uint8))
        st = np.stack(st)
        ref = next((i for i in range(len(st))
                    if float(st[i].std()) >= 1.0 and TF.ncc(st[i], st[0]) < TF.EMPTY_THR), None)
        sig = TF.signals(st, [i * 16_667 for i in range(len(st))], (2, 2, 24, 10), "abc")
        got = None if sig is None else sig["first_old"]
        if got != (None if ref is None else ref * 16_667):
            bad += 1
    check("**`signals` 的向量化和逐帧 `ncc` 逐个对得上**（40 组、相关系数横跨 EMPTY_THR）", bad == 0, bad)


def t_edge_refine() -> None:
    """两遍合一遍（tools/edge_refine.py，reuse-budget 计划 §7.6）：合成一段打字机，帧环 + 链事件 -> `edge` 证据，
    再从证据重建 measure() 的输出走 refine_boundaries.apply——三个时刻都要落在合成的真值上。"""
    print("edge_refine（两遍合一遍）")
    import threading
    import types

    import numpy as np

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.extract import edge_refine as ER
    from flowocr.extract import framegrid as AG
    from flowocr.extract import framesource as fs
    from flowocr.extract import ocr_args
    from flowocr.extract import ocr_parallel
    from flowocr.analyze import refine_boundaries as rb

    cmd = fs.build_ffmpeg_fullrate_cmd("a.mp4", start_sec=1.5, n_frames=10, step=2, scale=0.5)
    check("辅助流命令：-ss/-copyts 同采样流、select 隔帧、缩放、只送灰度",
          "-copyts" in cmd and cmd.index("-ss") < cmd.index("-i") and "mod(n" in " ".join(cmd)
          and "scale=trunc(iw*0.5/2)*2" in " ".join(cmd) and cmd[cmd.index("-pix_fmt") + 1] == "gray"
          and cmd[cmd.index("-frames:v") + 1] == "10")
    check("辅助流命令：坏参数会响", raises(lambda: fs.build_ffmpeg_fullrate_cmd("a.mp4", start_sec=0, n_frames=0), ValueError))
    dual = fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4,
                               aux={"grid": [1, 2, 1, 30], "scale": 0.5, "n_frames": 60, "url": "tcp://127.0.0.1:5"})
    ds = " ".join(dual)
    check("**同一次解码分两路**：split 双输出，采样帧走 pipe:1（带 showinfo）、辅助流走 TCP（灰度、缩、隔帧、自己的 -frames:v）",
          "-filter_complex" in dual and "split=2" in ds and "showinfo@main=checksum=0[sa]" in ds
          and "format=gray,showinfo@aux=checksum=0[sb]" in ds
          and dual[dual.index("pipe:1") - 1] == "nv12" and dual[-1] == "tcp://127.0.0.1:5" and dual[-8] == "60"
          and "-vf" not in dual, ds)
    # 2026-09-20 改了：双输出**现在和 hwaccel 组合得了**（辅路 scale_cuda 在显存里缩），
    # 细节在 t_frame_grid。这里只钉住"缩放要的宽高没给就报错，不许默默按别的尺寸缩"
    check("硬解辅路没给宽高就报错（scale_cuda 不吃表达式）",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", grid=_every(30), start_sec=0, n_out=4, hwaccel="cuda",
                                             aux={"grid": [1, 1, 1, 30], "scale": 0.5, "n_frames": 6, "url": "tcp://x"})))
    vid = str(Path(__file__).resolve())
    cfg0 = ocr_args.config_of([vid, "--out", "x"])
    offs = [ocr_args.config_of([vid, "--out", "x", *extra]) for extra in
            (["--no-refine-fused"], ["--decoder", "cv2"], ["--no-reuse-v2"], ["--no-reuse"])]
    # ⚠ `--hwaccel cuda` **2026-09-20 从这张"自动不开"的名单里拿掉了**：辅路改成显存里 scale_cuda
    # 缩完再下传，硬解也能同一次解码分两路。原来那条让"开硬解 = 自动退回两遍回抠"（35–105 s），
    # 把硬解省下的全吃回去还倒欠（defaults.md §1.8）
    check("**`--refine-fused` 是默认**（owner 09-17，能开就开）：默认就在 config 里；`--no-refine-fused`、`--decoder cv2`、"
          "`--no-reuse-v2`、`--no-reuse` 时自动不开（**`--hwaccel` 不再在这张名单里**）、那组键一个不留（OPT_IN_FUSED）",
          ocr_args.OPT_IN_FUSED <= set(cfg0) and cfg0["refine_fused"] is True
          and all(not (ocr_args.OPT_IN_FUSED & set(c)) for c in offs))
    m = ocr_parallel.merge_metas([{"edge": {"on": 2, "worker_busy_sec": 1.5}, "reuse_v2": {"cache_peak": 7_000_000, "refresh": 3},
                                   "frames_advanced": 1, "frames_requested": 1, "complete": True},
                                  {"edge": {"on": 3, "worker_busy_sec": 0.25}, "reuse_v2": {"cache_peak": 6_000_000, "refresh": 4},
                                   "frames_advanced": 1, "frames_requested": 1, "complete": True}],
                                 {}, wall=1.0, cuts_sec=[0.5], interval=0.5)
    check("--workers 拼 _meta 时 edge / reuse_v2 的计数逐键相加", m["edge"] == {"on": 5, "worker_busy_sec": 1.75}, m.get("edge"))
    check("**峰值不相加**：三个 worker 各自的缓存峰值取 max，不然 8 MB 上限会被拼成 21 MB（2026-09-17）",
          m["reuse_v2"] == {"cache_peak": 7_000_000, "refresh": 7}, m.get("reuse_v2"))
    check("并行产物也记实际用了几个 worker（不然读的人分不出『三个 worker』和『旧产物』）",
          m["workers_effective"] == 2, m.get("workers_effective"))
    check("`--refine-aux` 是产物参数（noop 不产 edge、nvdec/cuvid 的 edge 不一样）：空臂和默认的 config 必须不等",
          "refine_aux" in cfg0 and not ocr_args.same_arm([vid, "--out", "x"], [vid, "--out", "x", "--refine-aux", "noop"])
          and not ocr_args.same_arm([vid, "--out", "x"], [vid, "--out", "x", "--refine-aux", "nvdec"]))
    check("**A/B 的臂撞没撞比解析后的配置、不比参数字面**：默认值一翻，"
          "`--reuse-v2 --reuse-corr 0.8 --refresh-every 32` 就和空参数是同一条臂了（dev_tools/arm_check.py）",
          ocr_args.same_arm([vid, "--out", "a"], [vid, "--out", "b", "--reuse-v2", "--reuse-corr", "0.8",
                                                  "--refresh-every", "32"])
          and not ocr_args.same_arm([vid, "--out", "a"], [vid, "--out", "b", "--no-reuse-v2", "--no-refine-fused"]))
    check("obs 也有代码指纹（`_meta.code_fp`，不进 config）：覆盖在线量时刻的那几份代码",
          len(ocr_args.code_fp()) == 12
          and {"src/flowocr/extract/run_ocr2.py", "src/flowocr/extract/reuse_v2.py"} <= set(ocr_args.CODE_FP_FILES)
          and {"src/flowocr/extract/edge_refine.py", "src/flowocr/extract/typewriter_fuse.py"} <= set(ocr_args.CODE_FP_FILES)
          and "code_fp" not in ocr_args.config_of([vid, "--out", "x"]))
    # obs 指纹表的 import 闭包（2026-09-24：新加的网格模块决定证据窗口取哪些帧，漏进表里差点没人发现）。
    # 例外：timeline（--timeline 的计时，NON_PRODUCT）、paths（文件放哪，不改数）
    import re
    _fp = set(ocr_args.CODE_FP_FILES)
    _miss = set()
    for _f in _fp:
        _t = (ROOT / _f).read_text(encoding="utf-8")
        for _base, _names in re.findall(r"^[ \t]*from (flowocr[\w.]*) import ([^\n#(]+)", _t, re.M):
            for _it in (x.strip().split()[0] for x in _names.split(",") if x.strip()):
                _leaf = (ROOT / "src" / Path(*(_base + "." + _it).split("."))).with_suffix(".py")
                _p = _leaf if _leaf.exists() else (ROOT / "src" / Path(*_base.split("."))).with_suffix(".py")
                if _p.exists():
                    _miss.add(_p.relative_to(ROOT).as_posix())
    _miss -= _fp | {"src/flowocr/extract/timeline.py", "src/flowocr/paths.py"}
    check("obs 指纹表盖住它 import 的本地模块（例外只有 timeline / paths）", not _miss, sorted(_miss))
    # **换了后端也要换指纹**（2026-09-18 审计）：ORT 那条路的前后处理 / CTC 解码 / IPC /
    # 服务端都产数，漏进指纹就等于"换了产数的代码而指纹没动"（这个项目为追溯栽过四次）。
    fp_ocr = set(ocr_args.CODE_FP_FILES)
    check("OCR 指纹覆盖 ORT 那条路（recdecode / recort / ortclient / 服务端）",
          {"src/flowocr/extract/recdecode.py", "src/flowocr/extract/recort.py", "src/flowocr/extract/ortclient.py",
           "src/flowocr/extract/ort_server.py"} <= fp_ocr,
          sorted(fp_ocr))
    import json
    from flowocr.analyze import refine_boundaries as RB
    check("回抠指纹覆盖时钟和取帧（窗口开在哪一帧、帧号换回什么时刻都由它们定）",
          {"src/flowocr/extract/ptsclock.py", "src/flowocr/extract/framesource.py"} <= set(RB.CODE_FP_FILES),
          RB.CODE_FP_FILES)
    # 五张表 2026-09-21 统一成仓库相对路径（flowocr.provenance 文件头）；basename 混进来就是又一处旧口径
    check("五张指纹表全是仓库相对路径（tools/… src/… `explore/`…）",
          all("/" in f for m in (importlib.import_module("flowocr.analyze.build_tracks"), RB, ocr_args,
                                 importlib.import_module("flowocr.analyze.gamescript"), importlib.import_module("flowocr.analyze.scriptmatch"))
              for f in m.CODE_FP_FILES))
    # **运行时必需的源文件必须在版本库里**：`.gitignore` 的 `/explore/*` 曾把 `ort_server.py` 整个吞掉，
    # 于是 ORT 那条路在新 checkout / 新 worktree 上跑不起来，而本机因为文件躺在槽里看不出来。
    import subprocess
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True, check=True).stdout.split())
    need = set(ocr_args.CODE_FP_FILES) | set(RB.CODE_FP_FILES) | set(importlib.import_module("flowocr.analyze.build_tracks").CODE_FP_FILES)
    check("**指纹里的每个文件都在 git 里**（不然新 checkout 上根本没有这份代码）",
          need <= tracked, sorted(need - tracked))
    ring = ER.FrameRing(5)
    ring.release(10)
    for i in range(15):
        ring.push(i, np.zeros((2, 2), np.uint8))
    check("**帧环：放掉之后才推进来的旧帧不占位**（第一版就是被这些旧帧填满而死锁的）",
          len(ring.frames) == 5 and ring.highest == 14 and ring.dropped == 10 and ring.wait_for(14),
          (len(ring.frames), ring.highest, ring.dropped))
    er2 = ER.EdgeRefiner(src_fps=60, start_idx=0, width=64, height=32, scale=1.0, grid=[1, 2, 1, 6], thr=.8)
    seg2 = er2._open(7, {}, "ab", [4, 4, 40, 20], 30, 5)
    er2.abort()
    check("**网格隔帧时窗口起点对齐到格子**（不对齐首字会吸附到比 t_start 晚 1 帧、产物校验拒收）",
          seg2.lo == 20 and seg2.lo % 2 == 0, seg2.lo)
    check("**落盘前等的是『这一批开的段都停变了』那一批**，不是这一批本身（辅助流落后时证据会挂在已落盘的行上）",
          er2.settled_by(10) == 10 + ER.MAX_OPEN_SAMPLES + 1)
    nv = fs.build_ffmpeg_fullrate_cmd("a.mp4", start_sec=2.0, n_frames=9, step=1, scale=0.5, hwaccel="cuda", size=(960, 540))
    check("辅助流走 NVDEC：-hwaccel 在 -i 前、scale_cuda 在 hwdownload 前、带 showinfo（首帧 pts 对齐）、出 nv12",
          nv.index("-hwaccel") < nv.index("-i") and "scale_cuda=960:540,hwdownload,format=nv12,showinfo" in " ".join(nv)
          and nv[nv.index("-pix_fmt") + 1] == "nv12"
          and raises(lambda: fs.build_ffmpeg_fullrate_cmd("a.mp4", start_sec=0, n_frames=9, hwaccel="cuda"), ValueError))
    # **帧环的容量必须装得下批 det 的前瞻**（2026-09-17 实测的死锁）：主循环要先攒够 det_batch 个采样帧
    # 才处理第一帧、也才 release 出空间，而采样帧和辅助流出自同一个 ffmpeg——环一满整个 ffmpeg 就停。
    # 30 秒窗口实测：`--det-batch 8` + 默认环（5 个间隔 = 173 帧）100 秒都出不了一帧；`--refine-ring 16` 11 秒跑完
    # 2026-09-23：下界从"det 批的前瞻 + 1"改成"解码头到工作线程之间的全部在途 + 4"（decode-buffer §8.16）
    check("没有在途（EdgeRefiner 的缺省）：5 个采样间隔 + lead + tail + 8 = 173 帧",
          ER.ring_capacity(30, 3, 12) == 173, ER.ring_capacity(30, 3, 12))
    check("**容量 = 在途 + 4 个间隔**：默认 20 -> 24 个间隔、拆进程 36 -> 40 个（`--refine-ring` 更大时按它）",
          ER.ring_capacity(30, 3, 12, 5, in_flight=20) == 24 * 30 + 23
          and ER.ring_capacity(30, 3, 12, 5, in_flight=36) == 40 * 30 + 23
          and ER.ring_capacity(30, 3, 12, 48, in_flight=20) == 48 * 30 + 23,
          (ER.ring_capacity(30, 3, 12, 5, in_flight=20), ER.ring_capacity(30, 3, 12, 5, in_flight=36)))
    # 原来只能查 run_ocr2 源码里有没有那几个参数名（run_ocr2 顶层 import 推理库），函数挪进 edge_refine 之后按行为测
    from types import SimpleNamespace as _NS
    from flowocr.extract import decode_proc as _DP
    base = dict(prefetch=4, det_batch=4, det_prefetch=2, det_lookahead=1, rec_window=4, decode_shards=0)
    n0 = ER.ring_in_flight(_NS(**base))
    grows = {k: ER.ring_in_flight(_NS(**{**base, k: base[k] + 1})) > n0
             for k in ("prefetch", "det_batch", "det_prefetch", "det_lookahead", "rec_window")}
    check("在途逐项算全：取帧预取 + det 批 ×（1 + det 预取 + 流水）+ rec 窗口 + 拆进程的槽，每一项加 1 在途都跟着涨"
          "（漏一项就是 09-23 那种环满）",
          n0 == 4 + 4 * (1 + 2 + 1) + 4 and all(grows.values())
          and ER.ring_in_flight(_NS(**{**base, "decode_shards": 2})) == n0 + _DP.slots_for(4, 2)
          and ER.ring_in_flight(_NS(**{**base, "det_batch": 1})) == 4 + 1 * (1 + 2) + 4,
          (n0, grows))
    check("帧环按辅助流的帧数给容量：每帧都进环时同源帧数；120 fps 片子隔帧取（步长 2）约减半；不等距时两个网格的上界相加",
          ER.ring_capacity(30, 3, 12, aux=AG.AuxGrid(AG.TimeGrid.every(1), AG.TimeGrid.every(30))) == 173
          and ER.ring_capacity(60, 6, 24, aux=AG.AuxGrid(AG.TimeGrid.every(2), AG.TimeGrid.every(60)))
          == -(-(5 * 60 + 6 + 24 + 8) // 2) + 1
          and ER.aux_frames_in(AG.AuxGrid(AG.TimeGrid(2, 5), AG.TimeGrid(1, 15)), 150) == 61 + 11,
          (ER.ring_capacity(60, 6, 24, aux=AG.AuxGrid(AG.TimeGrid.every(2), AG.TimeGrid.every(60))),))
    stall = ER.FrameRing(2, stall_sec=0.2)
    for i in range(4):                               # 塞满又没人取
        stall.push(i, np.zeros((2, 2), np.uint8))
    check("**互等有出口**：环满、没人取、流没结束，卡过 stall_sec 就抛，不再挂到天荒地老"
          "（同一条教训的第三次：§7.6 的帧环、§7.12 的负 pts）",
          stall.stalled and raises(lambda: stall.wait_for(99), RuntimeError), stall.stalled)
    _erg = ER.EdgeRefiner(src_fps=60, start_idx=0, width=64, height=32, scale=1.0, grid=[1, 2, 2, 15], thr=.8)
    _erg.abort()
    check("edge_refine 的采样网格从辅助流网格里来（嵌套）；窗口的上一个 / 下一个采样帧按网格问（不等距时间隔差一帧）",
          _erg.sgrid == AG.TimeGrid(2, 15) and _erg.sgrid.prev(15) == 8 and _erg.sgrid.next(15) == 23)

    # 合成：200x40 灰度，暗底 20；框 [20,10,120,30] 里 5 个字格，字 c 在第 30+3c 帧弹出（亮 220），第 90 帧整行消失。
    # 采样间隔 6 帧：第 5 个采样点（帧 30）新链 "a"，帧 36 "abc"，帧 42 "abcde"，帧 48 起不变，帧 90 那个采样点没框了。
    us = 1_000_000 / 60
    box = [20, 10, 120, 30]

    def frame(i: int) -> np.ndarray:
        f = np.full((40, 200), 20, np.uint8)
        if i < 90:
            for c in range(5):
                if i >= 30 + 3 * c:
                    f[15:25, 25 + 20 * c:35 + 20 * c] = 220
        return f
    def run_synth(pts, stale_at: int | None = None, grid=(1, 1, 1, 6)):
        """喂一遍这段合成素材，返回 (on, off, stats)。`pts(帧号) -> t_us` 是**真实时间轴**——
        丢帧缺口的素材上它和 `帧号 / 平均帧率` 差得越来越多（yuka f4 20 分钟窗口末尾 −257.7 ms）。
        `stale_at` = 在第几个采样点报"这条链的历史被回补改过"；`grid` = 辅助流网格（只推网格上的帧，同 ffmpeg 辅路）。"""
        grid = list(grid)
        g_ = AG.AuxGrid.from_list(grid)
        er = ER.EdgeRefiner(src_fps=60, start_idx=0, width=200, height=40, scale=1.0, grid=grid, thr=0.8)

        def feed() -> None:
            for i in range(121):
                if g_.member(i):
                    er.ring.push(i, frame(i))
            er.ring.close()
        threading.Thread(target=feed, daemon=True).start()
        rows_ = {}
        texts = {5: ("a", "new"), 6: ("abc", "changed"), 7: ("abcde", "changed")}
        for k in range(5, 15):
            rows_[k] = {"frame": 6 * k, "t_us": pts(6 * k), "box": box, "text": "abcde"}
            text, kind = texts.get(k, ("abcde", "same"))
            er.sample(k, 6 * k, pts(6 * k), [(1, rows_[k], box, text, kind)],
                      [], {1} if stale_at == k else ())
        er.sample(15, 90, pts(90), [], [(1, rows_[14], box, "abcde", 84)], {1} if stale_at == 15 else ())
        st_ = er.finish(120)
        return rows_[5]["edge"]["on"], rows_[14]["edge"]["off"], st_

    on, off, st = run_synth(lambda i: int(round(i * us)))
    check("链首行挂上 `edge.on`：窗从首字前一个间隔 + LEAD 到停变那个采样点，模板 = 帧 48",
          st["on"] == 1 and st["off"] == 1 and (on["lo"], on["hi"], on["n"]) == (21, 48, 5), (st, on))
    check("首字格开始出现 = 帧 30、尾字格定稿 = 帧 42、逐字格直线首尾也落在 30 / 42",
          on["first_rise"] == round(30 * us) and on["full_glyph"] == round(42 * us)
          and on["cell"] and abs(on["cell"][0] - 30 * us) < us and abs(on["cell"][1] - 42 * us) < us, on)
    check("链末行挂上 `edge.off`：退回判据在帧 89 还像模板、曲线说完全显示结束 89 / 完全消失 90",
          off["ncc_gone"] == 89 and off["last_full"] == round(89 * us) and off["gone"] == round(90 * us), off)
    # 结尾窗要到帧 102；之后的帧 finish() 关环时喂帧线程还卡在满环上、不再计数，所以只要求"窗口要的都进过环"
    check("帧环没死锁、窗口要的帧都进过环", st["frames_pushed"] >= 103 and st["short_windows"] == 0, st)
    # 第二遍：证据 -> measured -> 原来的 apply()
    run = dict(t_start=round(30 * us), t_end=round(90 * us), t_full_sampled=round(42 * us), text="abcde", box=box, n_obs=10)
    job = rb.make_job(run, round(6 * us), us)
    SG6 = AG.TimeGrid.every(6)                     # 合成素材每 6 帧采一次
    meas = ER.measured_from_records(on, on, off, run["t_end"], us, round(6 * us), SG6)
    job["measured"] = meas
    rb.apply(job, None, None)
    check("**从 obs 证据重建后三个时刻落在合成真值上**：首字 30、全字 42、完全显示结束 89、完全消失 90（帧）",
          job["refined"] and abs(run["t_start"] - 30 * us) < 1 and abs(run["t_full"] - 42 * us) < 1
          and abs(run["t_end"] - 90 * us) < 1 and abs(run["t_full_end"] - 89 * us) < 1 and run["t_end_how"] == "curve", run)
    m_open = ER.measured_from_records(None, None, dict(off, ncc_gone=90, gone=None), run_t_end := round(90 * us), us, round(6 * us), SG6)
    m_ncc = ER.measured_from_records(None, None, dict(off, ncc_gone=89, gone=None), run_t_end, us, round(6 * us), SG6)
    check("**开放边界**（在线证据）：退回判据一路像到链断那个采样点（run 采样级的 t_end）= 窗里没看到消失，记 open、"
          "t_end 照旧取下界、不填完全显示结束；早一帧就不像了的照旧是 ncc",
          m_open[4] == "open" and m_open[1] and m_open[3] is None and abs(m_open[0] - 91 * us) < 1
          and m_ncc[4] == "ncc" and m_ncc[3] is not None, (m_open, m_ncc))
    # 不整除的采样（60 fps 上 2.2 / 2.1 fps）：查找终点是网格的下一格，不是"末次观测 + 名义间隔"（2026-09-26 Codex 复审反例）
    g22, g21 = AG.TimeGrid.for_rate(60, 2.2), AG.TimeGrid.for_rate(60, 2.1)
    k22 = [ER.measured_from_records(None, None, dict(off, last_idx=0, ncc_gone=k, gone=None), round(0 + 1e6 / 2.2), us, round(1e6 / 2.2), g22)[4]
           for k in (g22.next(0) - 1, g22.next(0))]
    k21 = [ER.measured_from_records(None, None, dict(off, last_idx=58, ncc_gone=k, gone=None), round(58 * us + 1e6 / 2.1), us, round(1e6 / 2.1), g21)[4]
           for k in (g21.next(58) - 1, g21.next(58))]
    check("**开放边界按采样网格的实际下一格判**（不整除的 2.2 / 2.1 fps 上名义间隔差一帧，两个方向都会判反）："
          "下一格已不像 = ncc，下一格还像 = open",
          (g22.next(0), g21.next(58)) == (28, 86) and k22 == ["ncc", "open"] and k21 == ["ncc", "open"], (g22.next(0), g21.next(58), k22, k21))
    check("没有任何证据 = None（该条原样退回，同 no_evidence）",
          ER.measured_from_records(None, None, None, 1, us, 1, SG6) is None
          and ER.measured_from_records({"short": True, "lo": 0, "hi": 1, "step": 1}, None, None, 1, us, 1, SG6) is None)
    # **不整除的网格**（2026-09-24，时间网格）：60 fps 上 24 fps（p/q = 2/5）+ 采样帧嵌套。窗口两端落在网格上、
    # 证据不 short，重建的 ts 就是网格上的帧（和实际推进环里的逐个对上），三个时刻离真值不超过网格间距
    ong, offg, stg = run_synth(lambda i: int(round(i * us)), grid=(2, 5, 1, 6))
    g25 = AG.AuxGrid.from_list([2, 5, 1, 6])
    sg = ER.sig_from_records(ong, ong, us)
    check("**不整除网格：窗口端点在网格上、证据齐全、记 grid 不记 step**",
          stg["short_windows"] == 0 and g25.member(ong["lo"]) and g25.member(offg["lo"]) and g25.member(offg["hi"])
          and ong.get("grid") == [2, 5, 1, 6] and "step" not in ong, (stg, ong.get("grid"), ong["lo"], offg["lo"], offg["hi"]))
    check("不整除网格：重建的 ts 就是 [lo, hi] 里网格上的帧",
          sg is not None and sg["ts"] == [round(i * us) for i in range(ong["lo"], ong["hi"] + 1) if g25.member(i)], sg and sg["ts"][:6])
    check("不整除网格：首字 / 全字 / 消失离真值不超过 3 帧（网格间距 2~3 帧）",
          abs(ong["first_rise"] - 30 * us) <= 3 * us and abs(ong["full_glyph"] - 42 * us) <= 3 * us
          and offg["gone"] is not None and abs(offg["gone"] - 90 * us) <= 3 * us,
          (ong["first_rise"] / us, ong["full_glyph"] / us, offg["gone"] and offg["gone"] / us))
    on_u, off_u, _ = run_synth(lambda i: int(round(i * us)), grid=(1, 2, 1, 6))
    check("等距网格（隔帧）照旧只记 step（和时间网格之前逐字节相同的前提）",
          on_u.get("step") == 2 and "grid" not in on_u and off_u.get("step") == 2, (on_u.get("step"), on_u.get("grid")))
    # **时刻要锚在真实 pts 上**（2026-09-17 审计 P1）：辅助流只有帧号，而 run 的 t_start/t_end 是 pts；
    # 有丢帧缺口的素材上两套时钟会差出几百毫秒（f4 20 分钟 258 ms、f3 7.2 h 9.5 s）。
    # 同一段像素、时间轴整体挪 0.25 s：三个时刻必须跟着挪
    SH = 250_000
    on2, off2, _ = run_synth(lambda i: int(round(i * us)) + SH)
    check("**证据的时刻锚在采样点的真实 pts 上**（`a_idx` / `a_us`）：时间轴整体偏 0.25 s，首字 / 全字 / 消失跟着偏",
          on2["a_us"] - on["a_us"] == SH and on2["first_rise"] - on["first_rise"] == SH
          and on2["full_glyph"] - on["full_glyph"] == SH and off2["gone"] - off["gone"] == SH
          and off2["last_full"] - off["last_full"] == SH, (on2, off2))
    check("重建也按锚折算（ncc_gone / 窗口网格都算）",
          ER.measured_from_records(on2, on2, off2, 0, us, round(6 * us), SG6)[0]
          - ER.measured_from_records(on, on, off, 0, us, round(6 * us), SG6)[0] == SH
          and ER.sig_from_records(on2, on2, us)["ts"][0] - ER.sig_from_records(on, on, us)["ts"][0] == SH)
    check("没有锚的旧产物退回纯帧号时钟（不炸）",
          ER.rec_time({k: v for k, v in on.items() if k not in ("a_idx", "a_us")}, 30, us) == round(30 * us))
    # **回补把变化点前移 -> 这一段的证据作废**（审计 P2）：帧环只留 5 个采样间隔，重新量不可能，
    # 所以标 stale、第二遍当没量到（口径同 short），该 run 退回采样级
    on3, off3, st3 = run_synth(lambda i: int(round(i * us)), stale_at=7)
    check("回补改写历史 -> `edge` 证据标 stale、当没量到（不是拿错窗口的时刻去写产物）",
          on3.get("stale") and st3["stale_windows"] >= 1 and not ER.usable(on3)
          and ER.measured_from_records(on3, on3, dict(off3, stale=True), 7, us, 1, SG6) is None, (on3.get("stale"), st3))
    check("stale 的 off 不参与结尾判决，但 on 还能用时打字机那一侧照旧",
          ER.usable(off) and not ER.usable(dict(off, stale=True))
          and ER.measured_from_records(on, on, dict(off, stale=True), 123, us, 1, SG6)[0] == 123)
    # refine_from_obs：按 run 的首行 / 末行认领证据（同一行位置）
    import json
    d = Path(tempfile.mkdtemp())
    obs = d / "o.jsonl"
    obs.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"_meta": {"stride": 6}},
        {"frame": 30, "t_us": round(30 * us), "box": box, "text": "a", "edge": {"on": on}},
        {"frame": 84, "t_us": round(84 * us), "box": box, "text": "abcde", "edge": {"off": off}},
        {"frame": 84, "t_us": round(84 * us), "box": [20, 200, 120, 220], "text": "zzz", "edge": {"off": off}},
    ]) + "\n", encoding="utf-8")
    run2 = dict(run)
    j2 = rb.make_job(run2, round(6 * us), us)
    ev = rb.refine_from_obs([j2], obs, us, round(6 * us))
    check("refine_from_obs：首行的 on + 末行的 off 认领到这条 run，别的行位置的 off 不认（默认救回作废证据，这里没有作废的）",
          ev == {"on_rows": 1, "off_rows": 2, "jobs": 1, "jobs_with_on": 1, "jobs_with_off": 1,
                 "off_rows_salvaged": 0, "jobs_with_off_salvaged": 0} and "measured" in j2, ev)
    # 时间网格上采样间隔不等距（2026-09-24，gi-s2 --fps 2.2）：段首证据在 run 起点前 1.03 个名义间隔那一票上，也要认领到
    run3 = dict(run, t_start=round(30 * us + 1.03 * 6 * us))
    j3 = rb.make_job(run3, round(6 * us), us)
    ev3 = rb.refine_from_obs([j3], obs, us, round(6 * us))
    check("refine_from_obs：「前一票」按 1.5 个名义间隔找（间隔比名义长一帧时照样认领段首证据）",
          ev3["jobs_with_on"] == 1, ev3)


def t_slot_fits_why() -> None:
    print("build_tracks.slot_fits_why")
    import random

    from flowocr.analyze import build_tracks as bt

    rng = random.Random(7)

    def sig():
        x0 = rng.uniform(0, 1500); y0 = rng.uniform(0, 1000)
        return (x0 + rng.uniform(10, 300) / 2, x0, x0 + rng.uniform(10, 400),
                y0, y0 + rng.uniform(10, 300), rng.uniform(8, 60))

    pairs = [(sig(), sig()) for _ in range(500)]
    same = all(bt.slot_fits(a, b, 1920, True) == bt.slot_fits_why(a, b, 1920, True)[0]
               for a, b in pairs)
    check("slot_fits 就是 slot_fits_why 的第一个返回值（判据只有一份）", same)
    consistent = all((bt.slot_fits_why(a, b, 1920, True)[0] is None)
                     == bool(bt.slot_fits_why(a, b, 1920, True)[1])
                     for a, b in pairs)
    check("不匹配时必须说出是哪一条，匹配时理由为空", consistent)
    hit = {bt.slot_fits_why(a, b, 1920, True)[1] for a, b in pairs} - {""}
    check("随机签名能覆盖到多条判据（否则这个测试没在测东西）",
          len(hit) >= 3, f"只覆盖到 {sorted(hit)}")


def t_evpair() -> None:
    """事件配对的判据（`dev_tools/evpair.py`）——**只有一份**，而且和遍历顺序无关。

    2026-09-19 复审抓到的：`arm_events` 是"全局按 IoU 排序后独占分配"，
    `text_direction` 却另写了一份"逐个事件挑当前最优"——后者换个事件顺序结果就可能不同，
    而两者回答的是同一个问题。抽成共享层之后，这里钉住三条不变量。
    """
    print("evpair（事件配对）")
    import random

    import evpair

    def ev(x0, y0, x1, y1, t0, t1, text=""):
        return {"box": [x0, y0, x1, y1], "t_start": t0, "t_end": t1, "text": text}

    a1 = [ev(0, 0, 100, 40, 0, 1000), ev(0, 0, 100, 40, 2000, 3000)]
    b1 = [ev(2, 1, 101, 41, 100, 1100), ev(2, 1, 101, 41, 2100, 3100)]
    pa, pb = evpair.pair_events(a1, b1, 0.7)
    check("同一处的两段**按时间各配各的**（不会串到另一段上）", pa == {0: 0, 1: 1}, pa)
    check("反向表和正向表一致", pb == {0: 0, 1: 1}, pb)

    far = [ev(0, 0, 100, 40, 0, 1000), ev(500, 0, 600, 40, 0, 1000)]
    near = [ev(0, 0, 100, 40, 0, 1000)]
    pa, _ = evpair.pair_events(far, near, 0.7)
    check("**一对一**：B 只有一个事件时，A 里只能有一个配上", len(pa) == 1 and 0 in pa, pa)

    check("时间不重叠就不配（几何像也不行）",
          evpair.pair_events([ev(0, 0, 100, 40, 0, 1000)],
                             [ev(0, 0, 100, 40, 5000, 6000)], 0.7) == ({}, {}), "配上了")

    # **和遍历顺序无关**：把两侧打乱再配，配出来的"事件对"集合必须一样
    rng = random.Random(3)
    ea = [ev(x, 0, x + 100, 40, t, t + 900) for x in (0, 60, 300) for t in (0, 1000, 2000)]
    eb = [ev(x + 3, 1, x + 102, 41, t + 50, t + 950) for x in (0, 60, 300) for t in (0, 1000, 2000)]
    base = {(id(ea[i]), id(eb[j])) for i, j in evpair.pair_events(ea, eb, 0.7)[0].items()}
    same = True
    for _ in range(20):
        ia, ib = list(range(len(ea))), list(range(len(eb)))
        rng.shuffle(ia)
        rng.shuffle(ib)
        sa, sb = [ea[i] for i in ia], [eb[j] for j in ib]
        got = {(id(sa[i]), id(sb[j])) for i, j in evpair.pair_events(sa, sb, 0.7)[0].items()}
        same &= got == base
    check("**打乱两侧的顺序，配出来的事件对集合不变**（原来那份逐个贪心的会变）", same)


def t_nameplate_track() -> None:
    """名牌这一层的结构不变量（2026-09-20 接进管线时补，复审第 3 条）。

    两条：**打标不删**（名牌轨里的事件必须同时还在它自己的区域轨里），
    以及**判据只有一份**（`build_tracks` 不许自己重写一遍，调 `uigate`）。
    """
    print("名牌轨：打标不删 + 判据只有一份")
    import re

    from flowocr.analyze import build_tracks as bt
    from flowocr.analyze import uigate

    src = (ROOT / "src" / "flowocr" / "analyze" / "build_tracks.py").read_text(encoding="utf-8")
    check("build_tracks 挑名牌调的是 uigate，没自己写一份",
          "uigate.pick_nameplates(" in src
          and not re.search(r"def .*nameplate.*\(", src))
    check("uigate.py 进了代码指纹文件表（产物变了要追溯得到）",
          "src/flowocr/analyze/uigate.py" in bt.CODE_FP_FILES, bt.CODE_FP_FILES)

    # 一个最小的 doc：两条 run，一条是名牌（在正文带上方、时长更长、文本重复）
    body = [uigate.Item((300, 880, 1600, 925), t, t + 4_000_000, f"这是第{i}句台词，够长了")
            for i, t in enumerate(range(0, 40_000_000, 5_000_000))]
    plate = [uigate.Item((300, 800, 500, 845), t, t + 4_500_000, "階堂ヒロ")
             for t in range(0, 40_000_000, 5_000_000)]
    got = uigate.pick_nameplates(body + plate, win_us=120_000_000)
    check("名牌挑得出来（在正文带上方 + 时长 ≥ 台词 + 词表小）",
          got and all(it.text == "階堂ヒロ" for it in got), [it.text for it in got][:3])
    check("正文自己不会被当成名牌", all(it not in body for it in got))
    # 把正文的时长拉长到名牌之上 → 第 6 条判据（时长 ≥ 台词）把它挡掉
    long_body = [uigate.Item(b.box, b.t0, b.t0 + 9_000_000, b.text) for b in body]
    check("正文更长时名牌被挡掉（owner 09-19：时长通常 >= 台词）",
          not uigate.pick_nameplates(long_body + plate, win_us=120_000_000))
    # **窗里东西太少就不判**（`len(chunk) < 10` 那条地板）：窗开到 10 秒时每窗只剩两三条，
    # 带都聚不出来。钉住这个行为——它解释了"为什么窗不能开太小"。
    check("窗太小（每窗不足 10 条）时一条都不挑，不会瞎判",
          not uigate.pick_nameplates(body + plate, win_us=10_000_000))


def t_ui_alarm_gate() -> None:
    """**报警的门必须低于剔除的门**（2026-09-19 实测踩到的一档）。

    `build_tracks.persistent_ui_lines` 按 `min_share`（0.5）剔常驻 UI，而 `track_health` 原来
    **也**用 0.5 报警——于是"差一点没被剔掉"的那一档既不剔、也不报。
    e-yuka-f5 上组批臂的 `Auto` 落在 **49.2%**：污染了 293/595 条主轨 cue，用途 2 的命中 158 → 0，
    而四个体检数一个都没响。同 `scriptmatch` 那个"可疑改写的门取了 --fuzzy、于是恒为 0"。
    """
    print("常驻 UI：报警的门 vs 剔除的门")
    import track_health

    check("`track_health` 的报警门**严格低于** `build_tracks` 的剔除门",
          track_health.UI_NEAR < track_health.UI_SHARE,
          (track_health.UI_NEAR, track_health.UI_SHARE))
    check("两边说的是同一条线：track_health 的 UI_SHARE 跟着 build_tracks 的默认 --ui-share",
          abs(track_health.UI_SHARE - 0.5) < 1e-9, track_health.UI_SHARE)
    # 真发生过的那三个占比：49.2% 必须报、34% 不许报（那是真字幕里的字）
    check("49.2%（贴着门没被剔）要报警", track_health.UI_NEAR <= 0.492 < track_health.UI_SHARE)
    check("34%（真字幕的字）不许报警", 0.34 < track_health.UI_NEAR)


def t_prefix_min() -> None:
    """前缀加分的最短长度门（audit-4 C4 第二条）。"""
    print("build_tracks.text_sim 前缀门")
    from flowocr.analyze import build_tracks as bt

    long_line = "すがのオレでも、人間とフェイの関係がさらに悪"
    check("显式 prefix_min=0 仍是旧行为：1 个字也给 0.95",
          bt.text_sim("1", "1-ザ-ID:712688376", 0) == 0.95)
    # 绊线：默认必须是开着的（改回 0 等于把那条退化路径放回来）
    check("**默认开着**（PREFIX_MIN_CHARS > 0，CLI 的 default 就取它）",
          bt.PREFIX_MIN_CHARS > 0, str(bt.PREFIX_MIN_CHARS))
    check("**开了门之后 1 个字不再算前缀**（那个 `1` 配上 ID 串的实例）",
          bt.text_sim("1", "1-ザ-ID:712688376", 3) < 0.55,
          str(bt.text_sim("1", "1-ザ-ID:712688376", 3)))
    check("2 个字也不算（trivial 档）",
          bt.text_sim("任務", "任務進行度 12/30", 3) < 0.55)
    # **≥3 字的真打字机不能被误伤**——这半是审计说错的那半
    check("5 字前缀照旧 0.95（真的打字机增长）",
          bt.text_sim("すがのオレ", long_line, 3) == 0.95)
    check("3 字整好过门", bt.text_sim("すがの", long_line, 3) == 0.95)
    check("门只挡前缀分支，不影响完全相同", bt.text_sim("1", "1", 3) == 1.0)
    check("**3 就是 evalkit 三档里那条线，不是新常数**",
          bt.PREFIX_MIN_CHARS == 3 and
          evalkit.triviality("abc") == evalkit.CLASSES[2] and
          evalkit.triviality("ab") == evalkit.CLASSES[1],
          f"{bt.PREFIX_MIN_CHARS} / {evalkit.triviality('abc')} / {evalkit.triviality('ab')}")


def t_vote_independent() -> None:
    """文本投票只数独立读数：一次读错不该被框级复用复制成多数（audit-4 C4）。"""
    print("build_tracks 投票口径")
    from flowocr.analyze import build_tracks as bt

    box = [100, 900, 500, 940]

    def obs(seq):          # seq: [(text, reused), ...]，一帧一条，位置不动
        return [{"t_us": i * 500_000, "box": list(box), "text": t, "conf": 0.9,
                 **({"reused": True} if ru else {})} for i, (t, ru) in enumerate(seq)]

    # 第一帧读错（多个"火"），随后两帧是**复用**同一个错读，最后两帧独立读对
    seq = [("本当に帰る火", False), ("本当に帰る火", True), ("本当に帰る火", True),
           ("本当に帰る", False), ("本当に帰る", False)]
    old = bt.build_runs(obs(seq), 500_000, 0.35, 0.55, 1)
    new = bt.build_runs(obs(seq), 500_000, 0.35, 0.55, 1, vote_independent=True)
    check("并成了一条 run（前提没塌）", len(old) == 1 and len(new) == 1,
          f"{len(old)} / {len(new)}")
    check("旧口径：复用把错读顶成多数（3 比 2）", old[0].text == "本当に帰る火", old[0].text)
    check("**只数独立票之后选对了**（1 比 2）", new[0].text == "本当に帰る", new[0].text)

    # 全是沿用的 run 没有独立票——退回全体，**不能把它清空**
    allru = [("Auto", False)] + [("Auto", True)] * 4
    r = bt.build_runs(obs(allru), 500_000, 0.35, 0.55, 1, vote_independent=True)
    check("全是沿用的 run 退回全体投票，文本不为空",
          len(r) == 1 and r[0].text == "Auto", str([x.text for x in r]))
    check("每一票都记了是不是沿用来的",
          len(r[0].reused) == len(r[0].texts) == 5, f"{r[0].reused}")
    # 代表文本三选（综合清单 M3）：3 票低 conf 的错读 vs 2 票高 conf 的正读
    cobs = [{"t_us": i * 500_000, "box": [100, 100, 300, 130], "text": t, "conf": c}
            for i, (t, c) in enumerate([("本当に帰る火", .6), ("本当に帰る", .99), ("本当に帰る火", .6),
                                        ("本当に帰る", .99), ("本当に帰る火", .6)])]
    picks = {m: bt.build_runs(cobs, 500_000, 0.35, 0.55, 1, text_pick=m)[0].text for m in ("vote", "conf", "maxconf")}
    check("代表文本：vote 按票数（3 票错读赢），conf 按置信度加权、maxconf 取最高那票（2 票高 conf 的正读赢）",
          picks == {"vote": "本当に帰る火", "conf": "本当に帰る", "maxconf": "本当に帰る"}, picks)


def t_dur_cap() -> None:
    """时长闸门只剩下限：上限那道悬崖已经去掉（2026-09-08）。"""
    print("build_tracks 时长闸门")
    from flowocr.analyze import build_tracks as bt

    base = {"cy": 0.87, "w": 0.14, "n_runs": 74, "n_tracks": 4, "h": 0.035,
            "fill_active": 0.30, "persistence": 1.0, "dominant_share": 0.23,
            "time_text_ratio": 0.0, "y_span": 0.19, "x_span": 0.5,
            "lang": "ja", "letter_frac": 0.9, "median_dur_s": 14.5}

    def lab_score(dur):
        f = dict(base, median_dur_s=dur)
        lab = bt.label_region(f, 2)
        return lab, bt.primary_score(f, lab)

    # 原神那段实测：10 分钟窗中位 14.5s、5 分钟窗 16.2s——**同一条字幕带**
    a, b = lab_score(14.5), lab_score(16.2)
    check("14.5s 和 16.2s 的字幕带判成同一类（15s 那道悬崖没了）",
          a[0] == b[0], f"{a[0]} vs {b[0]}")
    check("两边都拿得到非零主轨分", a[1] > 0 and b[1] > 0, f"{a[1]} / {b[1]}")
    check("长到 60s 也不再因为时长被否", lab_score(60.0)[1] > 0)
    # 下限还在：闪一下就没的不是字幕
    check("下限还在：0.2s 的仍然给 0 分", lab_score(0.2)[1] == 0.0)
    # 上限的职责由 resident 判据接着管，别以为去掉上限=什么都收
    resident = dict(base, median_dur_s=120.0, fill_active=0.95, dominant_share=0.9)
    check("**常驻且不换内容的照旧被排除**（这才是上限原来的职责）",
          bt.primary_score(resident, bt.label_region(resident, 2)) == 0.0,
          bt.label_region(resident, 2))


def t_band_regions() -> None:
    print("build_tracks.band_regions")
    from flowocr.analyze import build_tracks as bt

    # 主轨 cy=0.82 字高 0.039（约 42px/1080）；第二行差 2 倍字高
    rep = [
        (0, "subtitle", {"cy": 0.820, "h": 0.039}),
        (1, "dialogue-or-caption", {"cy": 0.898, "h": 0.038}),   # 同带第二行 dcy=2.0
        (2, "dialogue-or-caption", {"cy": 0.500, "h": 0.039}),   # 太远
        (3, "static-overlay", {"cy": 0.840, "h": 0.010}),        # 同高度但字太小
        (4, "misc", {"cy": 0.860, "h": 0.120}),                  # 同高度但字太大
    ]
    got = bt.band_regions(rep, 0, 3.0)
    check("同一条带的第二行会被并进来", got == [1], str(got))
    check("差太远的不并", 2 not in got)
    check("字高比不对的不并（太小/太大各一个）", 3 not in got and 4 not in got)
    check("主轨自己不在返回里", 0 not in got)
    check("阈值收到 1 倍字高就不再并", bt.band_regions(rep, 0, 1.0) == [])

    # --main-band-strict：两道现成的闸门，都不引入新常数
    rep2 = rep + [(5, "static-overlay", {"cy": 0.900, "h": 0.038}),   # 几何过线但 label 不对
                  (6, "dialogue-or-caption", {"cy": 0.895, "h": 0.038})]  # 画面另一侧
    check("松模式下 static-overlay 也会被并进来（这就是现在的默认行为）",
          5 in bt.band_regions(rep2, 0, 3.0))
    check("**label 闸门把 label 不对的挡掉**",
          5 not in bt.band_regions(rep2, 0, 3.0, need_label=True))
    check("label 闸门仍然并同带的正文", 1 in bt.band_regions(rep2, 0, 3.0, need_label=True))
    # x 判据：主轨横贯画面，候选缩在最右侧且 cx 差远 -> 该挡
    geom = {0: (960.0, 300.0, 1620.0, 880.0, 920.0, 42.0),
            6: (1850.0, 1800.0, 1900.0, 960.0, 1000.0, 41.0),
            1: (960.0, 300.0, 1620.0, 960.0, 1000.0, 41.0)}
    got = bt.band_regions(rep2, 0, 3.0, need_x=True, geom=geom, W=1920)
    check("**x 判据挡掉画面另一侧的**（x 不重叠且 cx 差 >0.08W）", 6 not in got, str(got))
    check("x 对得上的照并", 1 in got, str(got))
    check("**两道闸门互相独立**：只开 x 时 label 不对的照旧能进",
          5 in bt.band_regions(rep2, 0, 3.0, need_x=True,
                               geom={**geom, 5: geom[1]}, W=1920))
    # **不能只测选中的**：默认关的时候一个都不选
    check("--main-band 0 等于关（调用方不该调用它，这里只保证语义）",
          bt.band_regions(rep, 0, 0.0) == [])
    # **默认值本身是个结论，不是随手填的**（2026-09-08 owner 拍板由 0 改成 3）。
    # 放个绊线：谁把它改回 0，等于把"主轨=单个区域"那套脆弱性放回来，
    # 而那正是四次"主轨换区域"事故的共同病根。
    check("--main-band 默认是开的（MAIN_BAND_DEFAULT > 0）",
          bt.MAIN_BAND_DEFAULT > 0, str(bt.MAIN_BAND_DEFAULT))


def t_pick_main() -> None:
    """主轨：**排名和接受是两件事**，接受不了就没有主轨。

    存在的理由（methodology-audit-4 报告 C3 + gamestream-first-look 报告 §3）：
    旧写法只要有区域就无条件 `max(primary_score)` 指一条主轨，哪怕全是 0 分；
    换素材实测有台词的 5 段里错了 2 段，而且**错的那两条分数比对的还高**。
    """
    print("主轨选择")
    from flowocr.analyze import build_tracks as bt

    def reg(i, lab, score):
        return (i, lab, {"primary_score": score, "cy": 0.8})

    rows = [reg(0, "misc", 25.6), reg(1, "subtitle", 9.3), reg(2, "dialogue-or-caption", 1.9)]
    main, cand = bt.pick_main(rows)
    check("分更高但 label 不是正文类的不当主轨", main is not None and main[0] == 1,
          main[0] if main else None)
    check("候选按分数排序、只留可接受的", [c[0] for c in cand] == [1, 2], [c[0] for c in cand])
    check("全是 0 分时没有主轨", bt.pick_main([reg(0, "subtitle", 0.0)])[0] is None)
    check("一个可接受的都没有时没有主轨", bt.pick_main([reg(0, "misc", 99.0)])[0] is None)
    check("min_score 抬高后可以没有主轨",
          bt.pick_main(rows, min_score=10.0)[0] is None)
    check("空输入不炸", bt.pick_main([])[0] is None)


def t_audit7() -> None:
    """审计七的四条契约（methodology-audit-7 报告）。**这一批是"改完没人钉住"的落点**：
    七个提交动了缝合 / UI 门 / 名牌判据 / 共享服务，守卫一条都没加。"""
    print("audit-7 契约")
    import argparse
    import json
    import socket
    import threading

    from flowocr.analyze import build_tracks as bt
    from flowocr.extract import ortclient as oc
    from flowocr.artifacts import tracksio
    from flowocr.analyze import uigate as ug

    # ---- ①时间占比：同一段占用，拆 / 并 / 重叠，读数不变（§1.1）----
    # ⚠ `step` 是**采样间隔**：run 的 t_end 本来就已经含一个间隔，所以补齐是恒等的。
    # 拿"run 时长的中位数"当 step 正是 §1.1 那个病（比中位数短的 run 被拉长）。
    span, step = 100_000_000, 500_000
    def share(items):
        return ug.measure(ug.Footprint(box=[0, 0, 10, 10], items=items), span, step)["share"]
    one = [ug.Item((0, 0, 10, 10), 0, 10_000_000, "x")]
    many = [ug.Item((0, 0, 10, 10), i * 500_000, (i + 1) * 500_000, "x") for i in range(20)]
    dup = one * 3                                     # 同一段重复观测
    overlap = [ug.Item((0, 0, 10, 10), 0, 6_000_000, "x"),
               ug.Item((0, 0, 10, 10), 4_000_000, 10_000_000, "x")]
    check("时间占比：一条 10 s 的 run = 0.10", abs(share(one) - 0.10) < 1e-9, share(one))
    check("**拆成 20 条 0.5 s 读数不变**（原来逐条相加 = 0.20，拆得越碎占比越高）",
          abs(share(many) - 0.10) < 1e-9, share(many))
    _ext = max(i.t1 for i in dup) - min(i.t0 for i in dup)
    check("占用**不会超过这个框位自己的首末跨度**（旧口径逐条相加会，读成 0.30）",
          ug.occupancy(dup, step) <= _ext
          and sum(max(it.t1 - it.t0, step) for it in dup) > _ext)
    check("同一段重复观测不重复计", abs(share(dup) - 0.10) < 1e-9, share(dup))
    check("重叠的两段按并集算", abs(share(overlap) - 0.10) < 1e-9, share(overlap))
    check("点观测（obs 行）补满一个采样间隔",
          ug.occupancy([ug.Item((0, 0, 1, 1), t, t, "x") for t in (0, 500_000)], 500_000)
          == 1_000_000)

    # ---- ②新增一条无关正文带，不改变原正文的"在带里"（§1.2）----
    band = lambda top: {"top": top, "h": 40, "x0": 100, "xl": 100, "xr": 400,
                        "w": 10, "ts": set()}
    body = [100, 200, 400, 240]                       # 正好落在 top=200 那条带里
    a, b = ug.relate(body, 10.0, [band(200)]), ug.relate(body, 10.0, [band(200), band(320)])
    check("**下方多一条带，正文的 in_band 不变**（UI 门拿它当正文保护）",
          a["in_band"] and b["in_band"], (a["in_band"], b["in_band"]))
    plate = [100, 80, 200, 110]                       # 在 top=200 那条带上方 3 个字高
    r = ug.relate(plate, 3.0, [band(20), band(200)])
    check("名牌仍归给**下方 1~6 个字高**那条带，不是最近的那条",
          r["band_top"] == 200 and not r["in_band"], (r["band_top"], r["in_band"]))

    # ---- ③三种 UI 模式真的隔离（§1.4）----
    segs = [(0, 1_000_000, [])]
    args = argparse.Namespace(ui_gate="footprint", ui_share=0.5)
    check("`--ui-gate footprint` 真的关掉文本门",
          bt.text_ui_lines(segs, args, {}) == set())
    check("`--ui-gate text` 真的关掉框位门",
          bt.ui_footprints([], 500_000, argparse.Namespace(ui_gate="text")) == set())

    # ---- ④重分配：成员守恒 + 碎片化看得见（§2.1）----
    runs = [bt.Run(box=[100, 100 + 60 * k, 400, 130 + 60 * k], text=f"行{k}",
                   t_start=k * 10 ** 6, t_end=k * 10 ** 6 + 500_000, n_obs=1, conf=.9)
            for k in range(6)]
    wr = [{"runs": [k], "win": k} for k in range(6)]
    slots = bt.stitch_slots(runs, wr, 1280, 720, reassign=True)
    check("重分配之后成员守恒（不多不少）",
          sorted(i for sl in slots for i in sl) == list(range(6)),
          sorted(i for sl in slots for i in sl))
    check("每个窗区域只属于一个槽位",
          len([i for sl in slots for i in sl]) == 6)

    # ---- ⑤ui_footprint 在**两条**投影路上都算 UI（§3.1）----
    ev_fp = {"text": "UID: 1", "flags": ["ui_footprint"]}
    ev_tx = {"text": "Auto", "flags": ["ui_filtered"]}
    ev_ok = {"text": "台词", "flags": []}
    check("ui_flagged 认 ui_footprint 和 ui_filtered，不认正文（**只给没有轨的地方用**）",
          tracksio.ui_flagged(ev_fp) and tracksio.ui_flagged(ev_tx)
          and not tracksio.ui_flagged(ev_ok))
    # ⚠ **作用域**：`ui_filtered` 是**区域轨**那一遍盖的影子标，主轨自己的 `ui_lines`
    # 是另判的。拿事件上的标当全局判据，就会把"区域轨里判的 UI"带进主轨——
    # 2026-09-20 一天踩两次（第二次：并带主轨 build 写 457、重导只剩 329）。
    doc2 = {"events": [ev_tx, ev_ok], "tracks": [], "regions": [], "provenance": {}}
    main_tr = {"id": "main", "kind": "main", "ui_lines": []}      # 主轨没判它是 UI
    reg_tr = {"id": "r00", "kind": "region", "ui_lines": ["Auto"]}
    got_main = tracksio.cue_lines(doc2, main_tr, {"id": "c0", "events": [0, 1]})
    got_reg = tracksio.cue_lines(doc2, reg_tr, {"id": "c0", "events": [0, 1]})
    check("**区域轨判的 UI 不许带进主轨**：主轨 ui_lines 为空就该留着它",
          [e["text"] for e in got_main] == ["Auto", "台词"], [e["text"] for e in got_main])
    check("同一个事件在它自己的区域轨里照旧被剔（逐轨判决还在）",
          [e["text"] for e in got_reg] == ["台词"], [e["text"] for e in got_reg])

    # **端到端走一遍导出器**：上面那条单测钉的是 `cue_lines`，而回归是在 `export_srt`
    # 上被发现的（并带主轨 build 写 457、重导 329）。原来那条"逐字节相同"的守卫
    # 跑的数据里**没有并带主轨**，`ui_filtered` 和 `ui_lines` 恰好等价，所以挡不住。
    from flowocr.output import export as _ex
    with tempfile.TemporaryDirectory() as _d:
        _d = Path(_d)
        doc3 = {"schema": tracksio.SCHEMA, "tag": "g", "meta": {},
                "frame_us": 500_000, "size": [1000, 1000],
                "regions": [{"index": 0, "label": "misc", "n_windows_present": 1,
                             "lang": "ja", "primary_score": 1.0, "features": {}}],
                "events": [{"id": 0, "region": 0, "t_start": 0, "t_end": 1_000_000,
                            "t_full": 0, "box": [0, 0, 10, 10], "text": "Auto",
                            "conf": .9, "n_obs": 2, "flags": ["ui_filtered"]},
                           {"id": 1, "region": 0, "t_start": 0, "t_end": 1_000_000,
                            "t_full": 0, "box": [0, 20, 10, 30], "text": "台词",
                            "conf": .9, "n_obs": 2, "flags": []}],
                "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "misc",
                            "srt": "g-region00-misc.srt", "ui_lines": ["Auto"],
                            "cues": [{"id": "r00c0", "t_start": 0, "t_end": 1_000_000,
                                      "events": [0, 1]}]},
                           {"id": "main", "kind": "main", "regions": [0], "label": "misc",
                            "srt": "g-main.srt", "ui_lines": [],
                            "cues": [{"id": "mainc0", "t_start": 0, "t_end": 1_000_000,
                                      "events": [0, 1]}]}],
                "provenance": {"main_srt": "g-main.srt", "main_track": "main"}}
        tracksio.validate(doc3)
        _ex.export_srt(doc3, _d)
        main_txt = (_d / "g-main.srt").read_text(encoding="utf-8")
        reg_txt = (_d / "g-region00-misc.srt").read_text(encoding="utf-8")
        check("**并带主轨重导时不许被区域轨的 ui_filtered 削掉**（457 → 329 那次回归）",
              "Auto" in main_txt and "台词" in main_txt, main_txt.replace("\n", "|"))
        check("同一次导出里，区域轨该剔的照旧剔掉",
              "Auto" not in reg_txt and "台词" in reg_txt, reg_txt.replace("\n", "|"))
    doc = {"events": [ev_fp, ev_ok], "tracks": [], "regions": [], "provenance": {}}
    tr = {"id": "r00", "kind": "region", "ui_lines": []}
    kept = tracksio.cue_lines(doc, tr, {"id": "c0", "events": [0, 1]})
    check("cue_lines 也剔框位那条（导出器和 build_tracks 才对得上）",
          [e["text"] for e in kept] == ["台词"], [e["text"] for e in kept])

    # ---- ⑤b 冗余率按**事件**数，一条 run 被几条 cue 引用不该把它撑起来（§6.3）----
    import importlib.util as _ilu
    _sp = _ilu.spec_from_file_location(
        "a7probe", ROOT / "dev_tools/audit7_reassign_probe.py")
    _a7 = _ilu.module_from_spec(_sp)
    _sp.loader.exec_module(_a7)
    _ev = [{"id": 0, "region": 0, "t_start": 0, "t_end": 9_000_000, "text": "同一句"},
           {"id": 1, "region": 0, "t_start": 20_000_000, "t_end": 21_000_000, "text": "同一句"},
           {"id": 2, "region": 0, "t_start": 0, "t_end": 1_000_000, "text": "别的"}]
    _mk = lambda cues: {"events": _ev, "provenance": {"main_track": "main"},
                        "tracks": [{"id": "main", "kind": "main", "cues": cues}]}
    one = _mk([{"id": "c0", "events": [0, 2]}, {"id": "c1", "events": [1]}])
    split = _mk([{"id": "c0", "events": [0, 2]}, {"id": "c1", "events": [0]},
                 {"id": "c2", "events": [0]}, {"id": "c3", "events": [1]}])
    check("**同一个事件被几条 cue 引用，冗余率不变**（原来按 cue 引用数，虚高 13.3% → 46.3%）",
          _a7.redundancy(one) == _a7.redundancy(split) == (0, 3),
          (_a7.redundancy(one), _a7.redundancy(split)))
    near = _mk([{"id": "c0", "events": [0, 2]},
                {"id": "c1", "events": [1]}])
    near["events"] = [dict(_ev[0]), dict(_ev[1], t_start=2_000_000), dict(_ev[2])]
    check("**两个独立的同文事件挨得近才算冗余**（2 s < 5 s）",
          _a7.redundancy(near) == (1, 3), _a7.redundancy(near))

    # ---- ⑤c A/B 报表：框变了也要能对账，而且**不许把"没比"印成 0**（2026-09-20）----
    import ocr_ab_report as _ab
    A = [{"frame": 0, "box": [0, 0, 9, 9], "text": "同", "conf": 0.9},
         {"frame": 1, "box": [0, 0, 9, 9], "text": "变前", "conf": 0.9},
         {"frame": 2, "box": [5, 5, 9, 9], "text": "只在 A", "conf": 0.9}]
    B = [{"frame": 0, "box": [0, 0, 9, 9], "text": "同", "conf": 0.9},
         {"frame": 1, "box": [0, 0, 9, 9], "text": "变后", "conf": 0.9},
         {"frame": 3, "box": [7, 7, 9, 9], "text": "只在 B", "conf": 0.9}]
    c = _ab.compare_obs(A, B)
    check("**框序列不同也照样对账**（原来直接返回 None，汇总再把它当 0）",
          c["text_diff"] == 1 and c["cmp_rows"] == 2, c)
    check("只在一侧有的行单独数，不混进比较",
          c["only_a"] == 1 and c["only_b"] == 1, (c["only_a"], c["only_b"]))
    c2 = _ab.compare_obs(A, [dict(x) for x in A])
    check("逐行相同时 identical / same_boxes 都成立、文本改 0",
          c2["identical"] and c2["same_boxes"] and c2["text_diff"] == 0)
    c3 = _ab.compare_obs(A, [{"frame": 9, "box": [1, 1, 2, 2], "text": "x", "conf": 0.9}])
    check("**一行都配不上时 cmp_rows = 0**（分母用它，报表才不会印出假的 0%）",
          c3["cmp_rows"] == 0 and c3["text_diff"] == 0, c3)
    # ⚠ **B 臂新增的行也要数**（2026-09-20 复审第 1 条）：原来汇总只报
    # `rows_a - cmp_rows`（= A 独有），B 新出现的行一条都不出现在报表里，
    # 而它们照样会改下游。实测六段：A 独有 419、**B 独有 412**。
    c4 = _ab.compare_obs(A[:1], A[:1] + [{"frame": 7, "box": [1, 1, 2, 2], "text": "B 新增",
                                          "conf": 0.9}])
    check("**只有 B 新增行时也要报出来**（only_b 不许漏）",
          c4["only_b"] == 1 and c4["only_a"] == 0 and c4["cmp_rows"] == 1, c4)
    c5 = _ab.compare_obs(A[:1] + [{"frame": 7, "box": [1, 1, 2, 2], "text": "只在 A", "conf": 0.9}],
                         A[:1])
    check("交换 A/B 之后两侧的数对称地换过来",
          c5["only_a"] == 1 and c5["only_b"] == 0, c5)

    # ---- ⑥名牌判据只接 run 级 Item（`cover` 在点观测上恒 ≈ 1，§3.3）----
    pts = [ug.Item((0, 0, 10, 10), t, t, "名") for t in range(0, 20_000_000, 500_000)]
    try:
        ug.pick_nameplates(pts)
        ok = False
    except ValueError:
        ok = True
    check("pick_nameplates 拒收点观测（否则 cover 那条判据静默失效）", ok)

    # ---- ⑦共享 ORT 服务：能力核不过就退回自己 spawn，不抛异常（§1.3）----
    def fake_server(reply: dict) -> str:
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        def serve():
            conn, _ = srv.accept()
            f = conn.makefile("rwb")
            f.readline()
            f.write((json.dumps(reply) + "\n").encode())
            f.flush()
            conn.close()
            srv.close()
        threading.Thread(target=serve, daemon=True).start()
        return f"127.0.0.1:{srv.getsockname()[1]}"

    # 地址表：**一个引擎一个服务**，各连各的；要的模型分散在两个服务里就自己 spawn
    check("老写法（不分引擎）原样返回", oc.pick_addr("127.0.0.1:1", {"rec": "x"}) == "127.0.0.1:1")
    check("`rec=…,det=…` 按模型挑",
          oc.pick_addr("rec=h:1,det=h:2", {"rec": "x"}) == "h:1"
          and oc.pick_addr("rec=h:1,det=h:2", {"det": "x"}) == "h:2")
    check("**一个 client 要的两个模型在两个服务里就不连**（一根管子连不了两处）",
          oc.pick_addr("rec=h:1,det=h:2", {"rec": "x", "det": "y"}) == "")
    check("表里没有这个模型也不连", oc.pick_addr("rec=h:1", {"det": "x"}) == "")
    check("没设这个变量就不连", oc.pick_addr("", {"rec": "x"}) == "")

    cli = oc.OrtClient.__new__(oc.OrtClient)
    cli.device = "gpu"          # 握手要报自己要哪个设备（Codex 审计 P1：设备不对就别接）
    cli.cap_in = 1024
    cli.shm = type("S", (), {"name": "dummy", "close": lambda self: None,
                             "unlink": lambda self: None})()
    check("共享服务不提供 det 时**返回 False**（调用方自己 spawn），不抛",
          cli._attach(fake_server({"ok": 0, "missing": ["det"], "models": ["rec"]}),
                      {"det": "x.onnx"}) is False)
    check("服务给得出就接上去",
          cli._attach(fake_server({"ok": 1, "models": ["rec"], "provider": "CUDAExecutionProvider",
                                   "device": "gpu"}), {"rec": "x.onnx"}) is True)
    check("接上之后记下服务实际用的 provider（进 _meta.ort，追溯设备）",
          cli.provider == "CUDAExecutionProvider")
    cli.f.close()
    cli.sock.close()
    check("**设备对不上就不接**（服务是 CPU 的、这个 client 要 GPU）：返回 False，自己 spawn",
          cli._attach(fake_server({"ok": 0, "missing": [], "models": ["rec"], "device": "cpu"}),
                      {"rec": "x.onnx"}) is False)
    check("连不上也只是返回 False（服务先挂了的情形）",
          cli._attach("127.0.0.1:1", {"rec": "x.onnx"}) is False)


def t_motion() -> None:
    """位移事件（text-motion 计划第 1 步）：**序列，不是三个汇总数**。"""
    print("probe_motion")
    from flowocr.analyze import build_tracks as bt
    import probe_motion as pm

    # 三行一起往上滚：estimate_shift 要 >=3 票才认，一行独滚认不出来（探针自己会报）
    obs = []
    for i in range(8):
        for k, txt in enumerate(("第一行文字", "第二行文字", "第三行文字")):
            y = 600 - i * 24 + k * 40
            obs.append({"t_us": i * 500_000, "box": [100, y, 400, y + 30],
                        "text": txt, "conf": .9 - i * .05, "reused": bool(i % 2)})
    runs = bt.build_runs(obs, 500_000, .35, .55, 1)
    check("一起滚的三行合成三条 run", len(runs) == 3, len(runs))
    check("**会动**的 run 带位置历史", all(r.moving and len(r.boxes) == 8 for r in runs),
          [(r.moving, len(r.boxes)) for r in runs])
    steps = pm.steps_of(runs[0])
    check("位移事件是一串，不是一个平均", len(steps) == 7, len(steps))
    check("每步 dy = −24、间隔 = 一个采样间隔",
          all(abs(dy + 24) < 1e-6 and dt == 500_000 for _, _, dy, dt in steps))

    still = bt.build_runs([{"t_us": i * 500_000, "box": [0, 0, 100, 20],
                            "text": "不动的一行", "conf": .9} for i in range(6)],
                          500_000, .35, .55, 1)
    check("不动的 run 一条位移事件都没有", pm.steps_of(still[0]) == [])
    check("不动的 run 不留轨迹（只有首帧那一条）", len(still[0].boxes) == 1)

    check("行距用**同时并存**的两行 cy 差，不是字高", abs(pm.pitch_of(runs) - 40) < 1)
    check("并存的行不够时退回 0，由调用方决定怎么办", pm.pitch_of(still) == 0.0)
    check("conf 趋势能看出淡出（末段比首段低）", pm.conf_trend(runs[0]) < 0,
          pm.conf_trend(runs[0]))
    check("票数太少时不硬给趋势", pm.conf_trend(still[0]) == 0.0 or len(still[0].confs) >= 4)
    # **间隔是"上一次位移到这一次位移"**，不是"离上一条记录多久"（第二轮复审）：
    # 轨迹里有静止段的收尾点，拿它当基准会把步进滚动的 interval/Δ 一律压成 1。
    step_obs = []
    for i in range(10):
        dy = 0 if i < 3 else (24 if i < 6 else 48)          # 1.5 s 和 3.0 s 各一步
        for k, txt in enumerate(("第一行", "第二行", "第三行", "第四行")):
            y = 600 - dy + k * 40
            step_obs.append({"t_us": i * 500_000, "box": [100, y, 400, y + 30],
                             "text": txt, "conf": .9})
    step_runs = bt.build_runs(step_obs, 500_000, .35, .55, 1)
    ivs = [dt for *_, dt in pm.steps_of(step_runs[0])]
    check("**步进的间隔是两次位移之间**（1.5 s，不是一个采样间隔）",
          ivs == [1_500_000, 1_500_000], ivs)
    check("散点图没有点也不炸", "没有位移事件" in pm.scatter([])[0])
    check("散点图只有一个点也不除零（上下界相等）", len(pm.scatter([(1.0, 1.0)])) > 2)


def t_motion_gates() -> None:
    """两道运动闸门（text-motion 计划第 2/3 步）：**默认都不生效**。

    第 2 步的机制要说清：**不是把在动的区域从缝合里删掉**（那会丢 run），
    只是把它排到最后再去找槽位——65% 那次事故的机制是"排序键把最坏的样本
    排在最前面当种子"，改的是顺序，不是成员规则。
    """
    print("运动闸门")
    from flowocr.analyze import build_tracks as bt

    def R(y, moving=False, scrolling=False, n=1):
        r = bt.Run([0, y, 100, y + 20], "x", 0, 1_000_000, n, .9, ["x"])
        r.moving, r.scrolling = moving, scrolling
        return r

    runs = [R(0), R(20, moving=True), R(40, scrolling=True), R(60)]
    check("moving_share 两个原语都算", bt.moving_share(runs, [0, 1, 2, 3]) == 0.5)
    check("空集合不除零", bt.moving_share(runs, []) == 0.0)
    check("只看给的那几条", bt.moving_share(runs, [1]) == 1.0)

    # 第 2 步：种子顺序。滚动的大区域 run 最多，默认必然第一个当种子。
    scroll = [R(y, moving=True) for y in range(0, 400, 20)]          # 20 条，跨度大
    still = [R(800), R(820)]                                        # 2 条，底部窄带
    all_runs = scroll + still
    wr = [{"win": 0, "tracks": [], "runs": list(range(len(scroll)))},
          {"win": 1, "tracks": [], "runs": [len(scroll), len(scroll) + 1]}]
    order_default = bt.stitch_slots(all_runs, wr, 1000, 1000)
    check("默认（闸门关）时 run 多的先当种子", len(order_default) >= 1)
    slots_gate = bt.stitch_slots(all_runs, wr, 1000, 1000, move_seed_last=0.5)
    seeded_first = slots_gate[0][0] if slots_gate else None
    check("**开闸门后，第一个种子不是那个在动的区域**", seeded_first == 1,
          f"第一个槽位的种子是窗区域 {seeded_first}")
    check("在动的区域没被丢掉，仍在某个槽位里",
          any(0 in sl for sl in slots_gate), slots_gate)
    # **默认路径必须逐字不动**：这是改默认路径的旋钮，关着的时候要和没有它一样。
    check("闸门关（0）时和不传这个参数完全一样",
          bt.stitch_slots(all_runs, wr, 1000, 1000, move_seed_last=0.0) == order_default)

    # 第 3 步：主轨接受判据
    def rep(score, moving):
        return [(0, "subtitle", {"primary_score": score, "moving_share": moving})]

    # 绊线：owner 09-11 翻成默认、同日从 0.5 改到 0.3（星铁 / 绝区零整场主轨被阅读面板 / 弹幕抢走；
    # 0.5 离面板的 0.536 只差 0.036），CLI 的 default 就取它
    check("**默认 0.3：在动的区域（阅读面板那种 0.536）不当主轨**，CLI 默认取同一个常数",
          bt.MAIN_MOVE_MAX == 0.3 and bt.pick_main(rep(5.0, 0.536))[0] is None
          and bt.pick_main(rep(5.0, 0.048))[0] is not None
          and "default=MAIN_MOVE_MAX" in (ROOT / "src/flowocr/analyze/build_tracks.py").read_text(encoding="utf-8"))
    check("1.0 = 不拦（在动的照样能当主轨）",
          bt.pick_main(rep(5.0, 0.9), 0.0, 1.0)[0] is not None)
    # **边界**：`moving_share` 本身能取到 1.0（整块都在动），关闸也是 1.0——
    # 写成 `share < move_max` 时这条在关着的时候也被排除了：关掉的旋钮改了行为。
    check("**moving_share=1.0 时，闸门关（1.0）仍然保留候选**",
          bt.pick_main(rep(5.0, 1.0), 0.0, 1.0)[0] is not None)
    check("**开了阈值就不接受在动的区域**",
          bt.pick_main(rep(5.0, 0.9), 0.0, 0.5)[0] is None)
    check("不动的区域不受影响",
          bt.pick_main(rep(5.0, 0.1), 0.0, 0.5)[0] is not None)
    check("没有 moving_share 字段的旧调用当 0 处理",
          bt.pick_main([(0, "subtitle", {"primary_score": 5.0})], 0.0, 0.5)[0] is not None)


def t_cluster_ruler() -> None:
    """聚类的尺和 `--cluster learned` 那条路（2026-09-21，cluster-ruler 计划）。"""
    print("\n[聚类的尺 / learned 聚类]")
    from flowocr.analyze import build_tracks as bt
    from flowocr.analyze import cluster_layers as CL
    from flowocr.analyze import pair_features as PF
    from flowocr.analyze import slot_learned as SL
    from flowocr.analyze import slot_lines
    from flowocr.analyze import slot_pairs as SP
    src = "\n".join(Path(bt.__file__).read_text(encoding="utf-8").splitlines())
    cut = src.index("\n" + CL.LAYER_END)
    check("层缓存的键只 hash `slot_fits` 之前的代码——决定 run / 窗区域的函数必须都在它前面",
          all(src.index(f"\ndef {fn}(") < cut for fn in
              ("build_runs", "build_tracks", "build_regions", "cluster_windowed", "region_geom", "window_of")))
    a = CL._args("f5", [])
    check("层缓存键里的参数表都是 build_tracks 真有的参数", all(k in a for k in CL.LAYER_ARGS),
          [k for k in CL.LAYER_ARGS if k not in a])
    with tempfile.TemporaryDirectory() as td_:
        fake = Path(td_) / "o.jsonl"
        fake.write_text("{}\n", encoding="utf-8")
        k0 = CL.layer_key(fake, a)
        check("层缓存的键：换**缝合层**的参数不变（`--slot-geom` / `--cluster` / `--slot-reassign`），换**层**参数要变（`--iou`）",
              k0 == CL.layer_key(fake, CL._args("f5", ["--slot-geom", "pooled", "--no-slot-reassign", "--cluster", "learned"]))
              and k0 != CL.layer_key(fake, CL._args("f5", ["--iou", "0.5"])))
        check("`load()` 用的就是 `layer_key`（别再出现『函数改了、真正的键没改』）",
              "key = layer_key(obs, a)" in Path(CL.__file__).read_text(encoding="utf-8"))
    from types import SimpleNamespace as _NS
    mv = _NS(moving=True, box=[0, 300, 10, 310], boxes=[(0, [0, 500, 10, 510]), (1_000_000, [0, 400, 10, 410]), (3_000_000, [0, 300, 10, 310])])
    check("摆帧画框：动过的 run 取 t 时刻轨迹上的框（`r.box` 是最后的位置，滚动面板上会画到别的行上，cluster-ruler §6.5）；没动过的照旧",
          SP.box_at(mv, 2_000_000) == [0, 400, 10, 410] and SP.box_at(mv, 0) == [0, 500, 10, 510]
          and SP.box_at(_NS(moving=False, box=[1, 2, 3, 4], boxes=[(0, [9, 9, 9, 9])]), 5e6) == [1, 2, 3, 4])
    # —— Codex 审计（2026-09-21）要的两条契约 ——
    cached = {"tag": "x", "obs": "o.jsonl", "video": "v", "W": 1920, "H": 1080, "frame_us": 500000,
              "args": CL._args("f5", ["--slot-geom", "pooled", "--no-slot-reassign"]), "runs": [], "win_regions": []}
    got_kw = {}
    L2 = CL.from_cache(cached, a, key="")               # key="" -> stitch 不读写磁盘缓存
    real_stitch = bt.stitch_slots
    bt.stitch_slots = lambda runs, wr, W, H, **kw: got_kw.update(kw) or []
    try:
        CL.stitch(L2)
    finally:
        bt.stitch_slots = real_stitch
    check("同一份层缓存先后用不同缝合参数加载：**传给缝合器的是本次的参数**，不是建缓存那次的",
          (got_kw.get("geom_mode"), got_kw.get("reassign")) == ("median", True), got_kw)
    ka, kb, kc = [[0, 1, [0, 0, 9, 9], t] for t in "abc"]
    it_ok = {"a": {"key": ka}, "b": {"key": kb}, "label": "same", "stratum": "S2"}
    it_lost = {"a": {"key": ka}, "b": {"key": kc}, "label": "same", "stratum": "S2"}       # c 在臂 Y 里对不回
    it_gone = {"a": {"key": kc}, "b": {"key": kc}, "label": "same", "stratum": "S2"}       # 两端都对不回
    asg = {"X": {SP.item_key({"key": k}): 1 for k in (ka, kb, kc)},
           "Y": {SP.item_key({"key": k}): 1 for k in (ka, kb)}}
    check("标注两端全部 / 部分失联时不许进『预测正确』（`None == None` 不是同类）",
          SP.verdicts(asg, it_ok) == {"X": True, "Y": True} and SP.verdicts(asg, it_lost) is None
          and SP.verdicts({"Y": asg["Y"]}, it_gone) is None)
    SP._ASSIGN["__fake__"] = asg
    try:
        r = SP.score_doc({"tag": "__fake__", "items": [it_ok, it_lost, it_gone]}, {})
    finally:
        SP._ASSIGN.pop("__fake__", None)
    check("各臂的比较分母必须一致：一条臂对不回的样本，所有臂都记 lost",
          r["X"] == r["Y"] and r["X"][("S2", "lost")] == 2 and r["X"][("S2", "same", "together")] == 1, r)
    check("默认：`--slot-geom median`（owner 09-21）、`--cluster slots`（learned 是实验性的，不进默认）",
          (a["slot_geom"], a["cluster"]) == ("median", "slots"), (a["slot_geom"], a["cluster"]))
    check("尺的每条臂都把旋钮显式写出来（空字典 = 跟着默认漂，默认一翻两条臂就塌成一条）",
          all(v for v in SP.ARMS.values()) and SP.BASE in SP.ARMS, SP.ARMS)
    m = SL.load_model()
    check("随代码走的模型和当前特征表对得上（特征一改就要 `slot_learned.py --save` 重训）",
          m.names == SL.USED_NAMES and len(m.coef) == len(m.names) == len(m.mean) == len(m.scale))
    import numpy as np
    pr = m.predict_proba(np.zeros((2, len(m.names))))
    check("模型加载不依赖 sklearn、输出是概率", pr.shape == (2, 2) and abs(float(pr[0].sum()) - 1) < 1e-9)
    check("`slot_learned` 顶层不拖重模块进来（build_tracks --cluster learned 要 import 它）",
          not any(x in Path(SL.__file__).read_text(encoding="utf-8").split("def main()")[0]
                  for x in ("\nimport slot_pairs", "\nimport pair_model", "\nimport cluster_layers", "\nimport sklearn")))
    # 行位 -> 带：相邻行、同一种 x 锚才并；不同锚的不并（第一版"任一方的锚对得上就并"会一路串）
    sig = [{"tx": "xl", "y0": 860, "y1": 900, "h": 40, "xl": .22, "xc": .40, "xr": .60},
           {"tx": "xl", "y0": 908, "y1": 948, "h": 40, "xl": .22, "xc": .35, "xr": .50},
           {"tx": "xc", "y0": 956, "y1": 996, "h": 40, "xl": .22, "xc": .50, "xr": .80}]
    band = slot_lines.band_of(sig, column=False)
    check("带：同锚相邻行并、锚类型不同的不并", band[0] == band[1] != band[2], band)
    check("成对特征的顺序和 FEATURE_NAMES 一致（pair() 里有断言，这里核通道前缀）",
          {n.split(":")[0] for n in PF.FEATURE_NAMES} == set(PF.CHANNELS))


def t_rejoin_split() -> None:
    """det 切行拼接（`build_tracks.rejoin_split_lines`，followups「det 切行的碎片另起事件」）：
    wuwa-s1 31.5 s 的形状——在场的一整行在一个采样点上被切成两块，右边那块单独也能过普通匹配的门。"""
    print("rejoin_split")
    from flowocr.analyze import build_tracks as bt
    full = "今令尹から聞いたわ。あなたの言う通り、あの人がいなければ乗霄山の"
    row = lambda t, box, text: {"t_us": t, "box": box, "text": text, "conf": .9}  # noqa: E731
    seq = [row(0, [608, 812, 1310, 834], full), row(500_000, [608, 812, 1310, 834], full),
           row(1_000_000, [826, 810, 1310, 838], "あなたの言う通り、あの人がいなければ乗霄山の"),
           row(1_000_000, [608, 812, 818, 834], "今令尹から聞いたわ。"),
           row(1_500_000, [608, 812, 1310, 834], full)]
    on = bt.build_runs(seq, 500_000, .35, .55, 1)
    off = bt.build_runs(seq, 500_000, .35, .55, 1, rejoin_split=False)
    check("切成两块的那一刻拼回去：只有一条 run、4 个采样点、文本是整行", len(on) == 1 and on[0].n_obs == 4 and on[0].text == full,
          [(r.text, r.n_obs) for r in on])
    check("对照臂（--no-rejoin-split）照旧另起 run", len(off) == 2, [(r.text, r.n_obs) for r in off])
    # 两行上下挨着、各自在场：别把下一行的块拼进上一行
    two = [row(0, [100, 100, 500, 130], "上の行のテキスト"), row(0, [100, 140, 500, 170], "下の行のテキスト"),
           row(500_000, [100, 100, 300, 130], "上の行の"), row(500_000, [300, 100, 500, 130], "テキスト"),
           row(500_000, [100, 140, 500, 170], "下の行のテキスト")]
    runs = bt.build_runs(two, 500_000, .35, .55, 1)
    check("只拼同一行上的块，旁边那行照常匹配", len(runs) == 2 and sorted(r.n_obs for r in runs) == [2, 2],
          [(r.text, r.n_obs) for r in runs])
    # 拼起来不像这一行（是换了一句话）：不拼
    other = [row(0, [100, 100, 500, 130], "上の行のテキスト"),
             row(500_000, [100, 100, 300, 130], "まったく別の"), row(500_000, [300, 100, 500, 130], "台詞です")]
    check("拼起来文本对不上的不拼", len(bt.build_runs(other, 500_000, .35, .55, 1)) == 3)
    check("层缓存的键认 rejoin_split（翻它要重建 run）",
          "rejoin_split" in __import__("flowocr.analyze.cluster_layers", fromlist=["LAYER_ARGS"]).LAYER_ARGS)
    a = bt.make_parser().parse_args(["x.jsonl", "--outdir", "o"])
    b = bt.make_parser().parse_args(["x.jsonl", "--outdir", "o", "--no-seg-skip-footprint", "--no-rejoin-split"])
    check("切分前拿出框位 UI（--seg-skip-footprint）默认开、可关；切行拼接默认开、可关",
          a.seg_skip_footprint and a.rejoin_split and not b.seg_skip_footprint and not b.rejoin_split)
    # 区域轨：水印和台词同一区域时分开切，台词 cue 的起点不被水印提前；水印的事件仍在轨里、自成 cue
    wm = bt.Run(box=[1700, 1000, 1900, 1030], text="ユーザーID", t_start=0, t_end=60_000_000, n_obs=100, conf=.9)
    line = bt.Run(box=[600, 900, 1300, 940], text="台詞の一行目です", t_start=30_000_000, t_end=34_000_000, n_obs=8, conf=.9)
    idx = {id(wm): 0, id(line): 1}
    kw = {"mode": "segment"}
    segs, body = bt.split_hidden([wm, line], {0}, idx, kw)
    mixed = bt.cue_segments([wm, line], **kw)
    check("区域轨·框位 UI 分开切：正文段的起点是台词自己的（混着切时这一段从 0 开始）",
          [s for s, _, a in body] == [30_000_000] and any(line in a and s < 30_000_000 for s, _, a in mixed)
          and any(a == [wm] for _, _, a in segs), [(s, [r.text for r in a]) for s, _, a in segs])
    joined = bt._joined([{"text": "HP", "box": [0, 0, 40, 20]}, {"text": "100", "box": [42, 0, 80, 20]},
                         {"text": "Enter", "box": [100, 0, 160, 20]}])
    check("拼接的空格按几何：紧挨着的直接拼，间隙 ≥0.3 行高才隔空格", joined == "HP100 Enter", joined)
    check("--main-band-x 默认开（defaults.md 1.30）、--no-main-band-x 可关；--ruby-mark 默认开；--refine-salvage-end 默认开（1.32）",
          a.main_band_x and a.ruby_mark and a.refine_salvage_end
          and not bt.make_parser().parse_args(["x.jsonl", "--outdir", "o", "--no-main-band-x"]).main_band_x)

    # 振り仮名（`ruby_runs`）：wuwa-s1 28 s 的形状 + 两个不该标的（居中名牌、单个假名）
    R = lambda box, text, t0=0, t1=4_000_000: bt.Run(box=box, text=text, t_start=t0, t_end=t1, n_obs=8, conf=.9)  # noqa: E731
    body = R([604, 812, 1310, 852], "今令尹から聞いたわ。あなたの言う通り")
    runs = [body, R([600, 786, 684, 814], "こんれいいん"),              # 正文左段上方、字高 0.7、全假名 → 注音
            R([930, 786, 990, 814], "リン"),                          # 中心和正文对齐 → 居中的名牌，不标
            R([700, 786, 730, 814], "を"),                            # 单个假名 → 不标
            R([604, 700, 700, 728], "こんにちは"),                     # 离正文太远 → 不标
            R([604, 786, 700, 814], "こんれい", 3_000_000, 9_000_000)]  # 和正文同屏不到自己时长一半 → 不标
    check("振り仮名：只标紧贴含汉字正文上方、字高约一半、全假名 ≥2 字、不居中的那条", bt.ruby_runs(runs) == {1},
          sorted(bt.ruby_runs(runs)))
    check("振り仮名：正文没有汉字就不标", bt.ruby_runs([R(body.box, "あいうえおかきくけこ"), runs[1]]) == set())
    kata = R([604, 812, 1304, 852], "ここにいるのはスターゲイザー号の皆")    # 14 字、每字 50 px；片假名称号压在假名上
    check("振り仮名：中心正下方（±1 字）没有汉字的不标（片假名名字 / 称号压在假名或片假名上，peer 审查 edb5cc4）",
          bt.ruby_runs([kata, R([610, 786, 700, 814], "ここ")]) == set()
          and bt.ruby_runs([kata, R([1210, 786, 1300, 814], "みな")]) == {1})
    from flowocr.output import script as OVL
    rdoc = {"events": [{"id": 0, "text": "こんれいいん", "flags": ["ruby"], "box": [600, 786, 684, 814], "region": 0,
                        "t_start": 0, "t_end": 1_000_000},
                       {"id": 1, "text": "今令尹", "flags": [], "box": [604, 812, 1310, 852], "region": 0,
                        "t_start": 0, "t_end": 1_000_000}],
            "tracks": [{"id": "r00", "kind": "region", "region": 0, "ui_lines": [], "cues": [{"id": "c0", "events": [0, 1]}]}],
            "regions": [{"index": 0, "label": "subtitle"}], "provenance": {}}
    check("振り仮名：全部区域叠加（dev / default_all）照画、按 UI 标；主轨叠加（default）不画",
          [(it.events[0]["id"], it.ui) for it in OVL.tracks_items(rdoc, "all")] == [(0, True), (1, False)]
          and [it.events[0]["id"] for it in OVL.tracks_items({**rdoc, "provenance": {"main_track": "r00"}}, "main")] == [1])
    from flowocr.artifacts import tracksio as TIO
    doc = {"events": [{"id": 0, "text": "こんれいいん", "flags": ["ruby"]}, {"id": 1, "text": "今令尹", "flags": []}]}
    check("振り仮名：cue_lines 剔 UI 的模式（SRT / 匹配器 / default）不给 ruby 事件，全部文字的模式照给（误标读得回来）",
          [e["id"] for e in TIO.cue_lines(doc, {}, {"events": [0, 1]})] == [1]
          and [e["id"] for e in TIO.cue_lines(doc, {}, {"events": [0, 1]}, drop_filtered=False)] == [0, 1])

    # 聚类的尺的重新锚定（`slot_pairs.reanchor`）
    from flowocr.analyze import slot_pairs as SP
    cur = [R([100, 100, 500, 130], "上の行のテキスト", 1_000_000, 5_500_000), R([100, 300, 500, 330], "別の行", 0, 9_000_000)]
    check("尺·对不回的 run 按时间 + 框 + 文本锚到现在那条；锚不上（文本不像）就是 None",
          SP.reanchor((1_000_000, 5_000_000, "上の行のテキスト", (100, 100, 500, 130)), cur)
          == (1_000_000, 5_500_000, "上の行のテキスト", (100, 100, 500, 130))
          and SP.reanchor((1_000_000, 5_000_000, "まったく違う", (100, 100, 500, 130)), cur) is None)
    twins = [cur[0], R([100, 100, 500, 130], "上の行のテキスト", 1_000_000, 5_500_000)]
    check("尺·两条一样好时不锚（并列就算对不回）", SP.reanchor((1_000_000, 5_000_000, "上の行のテキスト", (100, 100, 500, 130)), twins) is None)
    # 两端锚到同一条 run（切行拼接把标注时的两块拼成了一条）：不算"同槽"，算对不回（2026-09-26 审计）
    joined = R([100, 100, 500, 130], "左の半分右の半分", 1_000_000, 5_000_000)
    half = lambda t, x0, x1: {"key": [1_000_000, 5_000_000, [x0, 100, x1, 130], t]}  # noqa: E731
    it_split = {"label": "different", "a": half("左の半分", 100, 300), "b": half("右の半分", 300, 500)}
    k_join = (joined.t_start, joined.t_end, joined.text, tuple(joined.box))
    check("尺·一对的两端锚到同一条 run 就不进分母（原来各臂白拿同槽）",
          SP.verdicts({"X": {k_join: 0}}, it_split, [joined]) is None)


def t_cluster_lines() -> None:
    """`build_tracks --cluster lines`（实验性）：slot_lines 的行位 -> 带当区域，散 run 回落到缝合分组。"""
    print("cluster_lines")
    from flowocr.analyze import build_tracks as bt
    from flowocr.analyze import slot_lines as SL
    runs = []
    for k in range(40):                                    # 底部字幕带两行（每行 20 条）+ 3 条散的
        for j, y in enumerate((900, 950)):
            runs.append(bt.Run(box=[600, y, 1300, y + 36], text=f"台詞{k}-{j}", t_start=k * 2_000_000,
                               t_end=k * 2_000_000 + 1_500_000, n_obs=3, conf=.9))
    runs += [bt.Run(box=[50 + 300 * i, 100, 90 + 300 * i, 120], text="x", t_start=i, t_end=i + 500_000, n_obs=1, conf=.5)
             for i in range(3)]
    fb = {i: 1000 + i for i in range(len(runs))}
    groups = SL.groups_for_pipeline(runs, 1920, 1080, fallback=fb)
    flat = sorted(i for g in groups for i in g)
    check("--cluster lines：分组正好覆盖每条 run 一次；底部两行并成一条带", flat == list(range(len(runs)))
          and any(set(range(80)) <= set(g) for g in groups), [len(g) for g in groups])
    check("--cluster 收 lines", bt.make_parser().parse_args(["x.jsonl", "--outdir", "o", "--cluster", "lines"]).cluster == "lines")
    # lines 进管线时：同刻同框的上下行并成一条带，不看 x 锚类型；从不同时在场的相邻行不并
    rr = [bt.Run(box=[600, 900, 1300, 936], text="a", t_start=k * 2_000_000, t_end=k * 2_000_000 + 1_500_000, n_obs=3, conf=.9)
          for k in range(10)]
    rr += [bt.Run(box=[700, 950, 1200, 986], text="b", t_start=k * 2_000_000, t_end=k * 2_000_000 + 1_500_000, n_obs=3, conf=.9)
           for k in range(10)]
    rr += [bt.Run(box=[650, 1000, 1250, 1036], text="c", t_start=100_000_000 + k * 2_000_000,
                  t_end=100_000_000 + k * 2_000_000 + 1_500_000, n_obs=3, conf=.9) for k in range(10)]
    lines_ = [(("y", "xl", 0), list(range(10))), (("y", "xc", 1), list(range(10, 20))), (("y", "xc", 2), list(range(20, 30)))]
    sig_ = [{"tx": "xl", "y0": 900, "y1": 936, "h": 36, "xl": .31, "xc": .49, "xr": .68},
            {"tx": "xc", "y0": 950, "y1": 986, "h": 36, "xl": .36, "xc": .49, "xr": .63},
            {"tx": "xc", "y0": 1000, "y1": 1036, "h": 36, "xl": .34, "xc": .49, "xr": .65}]
    b2 = SL.band_cooccur(lines_, sig_, rr, [0, 1, 2])
    check("--cluster lines：同刻在场的上下两行（锚类型不同）并成一条带，从不同时在场的相邻行不并",
          b2[0] == b2[1] and b2[2] not in (b2[0], b2[1]), b2)

    # 回抠结算救回被回补作废的结尾证据（--refine-salvage-end）
    import json as _json
    from flowocr.analyze import refine_boundaries as RBS
    with tempfile.TemporaryDirectory() as td:
        op = Path(td) / "o.jsonl"
        off = {"lo": 0, "hi": 60, "last_idx": 30, "stale": True}
        rows = [{"_meta": {"stride": 30, "src_fps": 60.0}},
                {"frame": 30, "t_us": 500_000, "box": [0, 0, 100, 30], "text": "あ", "conf": .9, "edge": {"off": off}},
                {"frame": 60, "t_us": 1_000_000, "box": [0, 100, 100, 130], "text": "い", "conf": .9,
                 "edge": {"off": {**off, "last_idx": 60, "short": True}}}]
        op.write_text("\n".join(_json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        jobs = lambda: [{"run": {"t_start": 0, "t_end": 1_000_000, "box": [0, 0, 100, 30]}},   # noqa: E731
                        {"run": {"t_start": 0, "t_end": 1_500_000, "box": [0, 100, 100, 130]}}]
        j0, j1 = jobs(), jobs()
        s0 = RBS.refine_from_obs(j0, op, 1e6 / 60, 500_000, salvage_stale_end=False)
        s1 = RBS.refine_from_obs(j1, op, 1e6 / 60, 500_000, salvage_stale_end=True)
        check("回抠·救回作废的结尾证据：关了不认领；开了认领窗口凑齐的那条（作废标去掉）、窗口不全的仍不认",
              s0["jobs_with_off"] == 0 and s1["jobs_with_off"] == 1 and s1["jobs_with_off_salvaged"] == 1
              and j1[0]["ev_off"]["stale"] is False and "ev_off" not in j1[1], (s0, s1))

    # 匹配器的相邻合并按来源区域各自判（nonoise 喂法把各区域的 cue 交错着喂）
    from flowocr.analyze import scriptmatch as SM
    def rec(i, t0, t1, key):
        return {"start": t0, "end": t1, "ocr": key, "how": "exact", "conf": "high",
                "ref": {"key": key, "jp": key, "cn": None, "score": 1.0}, "extra": [], "cues": [i]}
    recs = [rec(0, 0, 3, "a"), rec(1, 3.2, 4, "z"), rec(2, 4.5, 8, "a"), rec(3, 8, 9, "a")]
    src = ["r01", "r05", "r01", "r05"]
    got = SM.merge_runs([dict(r) for r in recs], SM.MERGE_GAP, src)
    check("匹配器·合并按区域判相邻：同一区域里被别的区域的 cue 隔开的同一句并上；另一区域的同一句不并过去",
          [(r["start"], r["end"], r["ref"]["key"], r["n_cues"]) for r in got]
          == [(0, 8, "a", 2), (3.2, 4, "z", 1), (8, 9, "a", 1)], [(r["start"], r["end"], r["n_cues"]) for r in got])
    one = SM.merge_runs([dict(r) for r in recs], SM.MERGE_GAP, ["main"] * 4)
    check("匹配器·只有一个来源（--feed main）时仍按喂入序列判相邻：中间隔了别的剧本行就不并",
          [(r["start"], r["n_cues"]) for r in one] == [(0, 1), (3.2, 1), (4.5, 2)], [(r["start"], r["n_cues"]) for r in one])

    # 叠加块按位置切段（2026-09-26 审计：hsr-s1 一句对白 2 s 里换了四处，并成一块后整段画在第一处）
    from flowocr.artifacts import srtio
    def ev(i, box, t0, t1):
        return {"id": i, "box": box, "t_start": t0, "t_end": t1}
    pcues = [srtio.Cue(0.983, 1.017, ("x",)), srtio.Cue(1.2, 1.4, ("x",)),
             srtio.Cue(1.483, 1.533, ("x",)), srtio.Cue(2.267, 2.633, ("x",))]
    grow = [(0, [ev(0, [148, 786, 300, 814], 983_000, 1_017_000)]),     # 打字机：小框落进后一条的大框里
            (1, [ev(1, [148, 786, 658, 814], 1_200_000, 1_400_000)])]
    moved = grow + [(2, [ev(2, [266, 693, 776, 721], 1_483_000, 1_533_000)]),
                    (3, [ev(3, [148, 786, 658, 814], 2_267_000, 2_633_000)])]
    segs = SM.position_segments(moved, pcues, 983_000, 2_633_000)
    check("叠加·同一认领的正文换了位置就另起一段、段间空档不画；回到原处也另起；首尾段贴记录的起止",
          [(a, b, [e["id"] for e in evs]) for evs, _, a, b in segs]
          == [(983_000, 1_400_000, [0, 1]), (1_483_000, 1_533_000, [2]), (2_267_000, 2_633_000, [3])],
          [(a, b) for _, _, a, b in segs])
    # Codex 复审的反例：每一步和上一步重叠过半，五步之后完全离开首框
    dcues = [srtio.Cue(k * 0.5, k * 0.5 + 0.5, ("x",)) for k in range(5)]
    drift = [(k, [ev(k, [100, 100 + 10 * k, 600, 128 + 10 * k], k * 500_000, k * 500_000 + 500_000)]) for k in range(5)]
    dsegs = SM.position_segments(drift, dcues, 0, 2_500_000)
    check("叠加·慢慢漂的字：段里每条都要和画的那个框同处（只比上一条没有传递性），不许整段钉在首框",
          len(dsegs) > 1 and all(max(SM.box_inside(e["box"], r), SM.box_inside(r, e["box"])) >= SM.SAME_PLACE
                                 for evs, rows, _, _ in dsegs for e in evs
                                 for r in [[min(x[0] for x in rows), min(x[1] for x in rows),
                                            max(x[2] for x in rows), max(x[3] for x in rows)]]),
          [(a_, b_, rows) for _, rows, a_, b_ in dsegs])
    # 画的框因行数变多换掉时，已在段里的也要重核：第三条两行、框下移，首条已不在新框里 -> 第三条另起
    swap = [(0, [ev(0, [100, 100, 600, 128], 0, 500_000)]), (1, [ev(1, [100, 110, 600, 138], 500_000, 1_000_000)]),
            (2, [ev(2, [100, 120, 600, 148], 1_000_000, 1_500_000), ev(3, [100, 150, 600, 178], 1_000_000, 1_500_000)])]
    check("叠加·画的框换成行数更多的那条时，段里已有的也要和新框同处，不同处就另起一段",
          [[e["id"] for e in evs] for evs, _, _, _ in SM.position_segments(swap, dcues, 0, 1_500_000)] == [[0, 1], [2, 3]])
    check("叠加·打字机长字（小框落进大框）不切，只有一段时起止就是记录的起止",
          [(a, b) for _, _, a, b in SM.position_segments(grow + [(2, [])], pcues, 983_000, 1_533_000)]
          == [(983_000, 1_533_000)])
