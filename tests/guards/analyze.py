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
    # cue 的文字按可见的行拼：同一行被切成三段、事件序按框上沿排是乱的（hsr「船の行き先は…」，2026-10-05）
    row = _copy0.deepcopy(doc)
    row["events"] = [{"text": "姫子", "box": [914, 794, 1004, 846]},
                     {"text": "と呼ばれる場所よ。", "box": [1176, 854, 1400, 890]},
                     {"text": "「渡画泉隠」", "box": [1032, 858, 1184, 887]},
                     {"text": "船の行き先は境界", "box": [500, 860, 974, 887]}]
    row["tracks"][1]["cues"] = [{"id": "c0", "t_start": 0, "t_end": 1_000_000, "events": [0, 1, 2, 3]}]
    rc, revs, _ = SM.feed_from_doc(row, "nonoise")
    check("feed 的 cue 文字按行从上往下、行内从左往右拼（同一行的几段连成一行）",
          rc[0].lines == ("姫子", "船の行き先は境界「渡画泉隠」と呼ばれる場所よ。") and len(revs[0]) == 4,
          (rc[0].lines, len(revs[0])))

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

    原来开视频测时 `first_true` / `last_true` 找不到返回区间端点、被当成边界写出"精确"的新时间；现在只有 obs 证据那条路
    （离线测量 frame-pts 计划删了），同一条契约落在证据上：没量到的那一侧原样退回，量没量到要说清楚。
    """
    print("回抠的证据契约")
    import types
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.analyze import refine_boundaries as rb
    from flowocr.extract import edge_refine as ER
    from flowocr.extract.framegrid import AuxClock, Clock, SampleClock

    clk = AuxClock(Clock(60), SampleClock(2))
    rs = dict(t_start=500_000, t_end=1_500_000, text="hello", box=[0, 0, 10, 10], n_obs=1)
    job = rb.make_job(rs, 500_000)
    m = ER.measured_from_records(None, None, None, rs["t_end"], 500_000, clk)
    rb.apply(job, None, None)
    check("**一条证据都没有：measured 为 None、不记 refined、时间原样**",
          m is None and not job["refined"] and rs["t_start"] == 500_000 and rs["t_end"] == 1_500_000 and "t_full" not in rs,
          (m, job["refined"], rs))
    off = {"lo": 1_150_000, "hi": 1_700_000, "last_us": 1_000_000, "n": 5, "text": "hello",
           "ncc_end": None, "ncc_open": False, "last_full": None, "gone": None}
    check("结尾证据里 ncc 没找到（ncc_end 是 None）、曲线也没给：不算量到，原样退回",
          ER.measured_from_records(None, None, off, 1_500_000, 500_000, clk) is None)
    m_ncc = ER.measured_from_records(None, None, dict(off, ncc_end=1_116_667), 1_500_000, 500_000, clk)
    m_open = ER.measured_from_records(None, None, dict(off, ncc_end=1_516_667, ncc_open=True, last_full=1_400_000),
                                      1_500_000, 500_000, clk)
    check("**开放边界**：最后还像的帧到了链断的那个采样点（ncc_open）记 open、t_end 只是下界、完全显示结束不填；"
          "窗里看到它消失了是 ncc，t_end = 它后面那一个辅助帧（ncc_end）",
          m_ncc[0] == 1_116_667 and m_ncc[4] == "ncc" and m_open[4] == "open" and m_open[0] == 1_516_667 and m_open[3] is None,
          (m_ncc, m_open))
    check("short / stale 的证据当没量到", ER.measured_from_records(None, None, dict(off, ncc_end=1_116_667, short=True),
                                                             1_500_000, 500_000, clk) is None
          and not ER.usable(dict(off, stale=True)))

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
        oldkey = obs_with({"src_fps": 60.0, "config": {"refine_fused": True}, "edge": {"on": 12}, "stride": 30})
        check("回抠的证据判定：配置开了融合、而且 edge 真有条数才算；空臂（noop）、旧 obs 都不算",
              rb.obs_evidence(real)[1] and not rb.obs_evidence(noop)[1] and not rb.obs_evidence(old)[1])
        check("**有证据、但帧身份是旧的帧号（没有 frame_key）的 obs：要回抠就报错退出、说重跑阶段 1**（`--refine off` 照读）",
              raises(lambda: bt.settle_refine({"events": []}, oldkey, "t"), SystemExit))
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
    with tempfile.TemporaryDirectory() as _d:
        _p = Path(_d) / "o.jsonl"
        _p.write_text('{"_meta": {}}\n' + _rows(0), encoding="utf-8")
        _mixed = RB.attach_ocr([_j() for _ in range(TF.OCR_SLOPES_MIN - 1)] + [dict(_j(), effect_pool=False)], _p)
    check("全片 OCR 打字速度只从正文区域的 job 统计（defaults.md 1.65）：标题 / UI 的 job（effect_pool=False）不凑数",
          _mixed is None, _mixed)

    # 回抠覆盖全部区域、文字效果只看正文区域（defaults.md 1.65）
    _sig = inspect.signature(RB.refine_tracks).parameters["labels"].default
    check("回抠默认覆盖全部区域：DEFAULT_LABELS = all，refine_tracks 的默认就是它",
          RB.DEFAULT_LABELS == "all" and _sig == RB.DEFAULT_LABELS, (RB.DEFAULT_LABELS, _sig))
    _tw = [{"text": "あいうえおかきく", "grow_ms": 400 + 10 * k} for k in range(8)]   # 每字 50 ms 上下，很紧
    _fade = [{"text": "第三章消えた灯台", "grow_ms": g} for g in (0, 0, 0, 0, 0, 0, 30, 60, 900, 1200, 1500, 1800, 2000, 2500)]
    _base = RB.text_effect_of([{"label": "dialogue-or-caption", "runs": _tw}], 1e6 / 60)
    _with_ui = RB.text_effect_of([{"label": "dialogue-or-caption", "runs": _tw}, {"label": "misc", "runs": _fade},
                                  {"label": "noise", "runs": _fade}], 1e6 / 60)
    _as_main = RB.text_effect_of([{"label": "dialogue-or-caption", "runs": _tw + _fade}], 1e6 / 60)
    check("文字效果只从正文区域统计：misc / noise 区域里四散的生长不改判定；同样的 run 放进正文区域才会改（对照，守卫本身灵敏）",
          _base == _with_ui and _base.get("verdict") == "typewriter" and _as_main.get("verdict") != "typewriter",
          (_base, _with_ui, _as_main))

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

    # 2026-10-16：uint8 上的中位数 / 95 分位走直方图、结尾窗 ncc 模板侧预算——都要和 numpy 原式逐位相同（升级 numpy 时这条先报）
    rng = np.random.default_rng(7)
    bad = []
    for tr in range(600):
        shp = (int(rng.integers(1, 30)), int(rng.integers(1, 120)))
        img = (rng.integers(0, 256, shp) if tr % 3 == 0 else rng.choice(rng.integers(0, 256, 3), shp) if tr % 3 == 1
               else np.clip(rng.normal(rng.integers(0, 256), 25, shp), 0, 255)).astype(np.uint8)
        dev = img.astype(np.int16) - int(np.median(img))
        b, d = dev[dev > 0], -dev[dev < 0]
        if TF.polarity(img) != bool((np.percentile(b, 95) if b.size else 0) >= (np.percentile(d, 95) if d.size else 0)):
            bad.append(("polarity", tr))
        if TF._u8_median_int(img) != int(np.median(img)):
            bad.append(("median_int", tr))
        m1, m2 = TF._u8_median_f32(img), np.median(img.astype(np.float32))
        if type(m1) is not type(m2) or m1 != m2:
            bad.append(("median_f32", tr))
        r1, r2 = TF._u8_rowmedian_f32(img), np.median(img.astype(np.float32), axis=1)
        if r1.dtype != r2.dtype or not np.array_equal(r1, r2):
            bad.append(("rowmedian", tr))
        other = rng.integers(0, 256, shp).astype(np.uint8)
        if TF.ncc_to(img)(other) != TF.ncc(other, img):
            bad.append(("ncc_to", tr))
    check("**直方图版中位数 / 95 分位、`ncc_to` 和 numpy 原式逐位相同**（600 组随机 / 三值 / 偏态 uint8）", not bad, bad[:5])

    # 2026-10-16：`signals` 里"整叠不像首帧"改走整数矩、字形一致率 / 逐字格改成只碰字形像素的计数——都要和原式逐位相同
    def glyph_ref(frames, tpl):
        mask = TF.glyph_mask(tpl)
        if mask.sum() < 20:
            return np.full(len(frames), np.nan)
        return (np.abs(frames.astype(np.int16) - tpl.astype(np.int16))[:, mask] <= TF.GLYPH_TOL).mean(axis=1)

    def cell_ref(frames, ts, x0, x1, n_chars):
        k = len(frames) - 1
        mask = TF.glyph_mask(frames[k], TF.polarity(frames[k]))
        close = np.abs(frames.astype(np.int16) - frames[k].astype(np.int16)) <= TF.GLYPH_TOL
        cw = (x1 - x0) / n_chars
        xs, ys = [], []
        for c in range(n_chars):
            a, b = int(round(x0 + c * cw)), int(round(x0 + (c + 1) * cw))
            m = mask[:, a:b]
            if m.sum() < TF.CELL_MIN_PX:
                continue
            o = TF.last_onset(close[:, :, a:b][:, m].mean(axis=1) >= TF.GLYPH_THR)
            if o is not None:
                xs.append(c)
                ys.append(ts[o])
        fit = TF.theil_sen(xs, ys)
        return None if fit is None else (fit[0], fit[0] + fit[1] * (n_chars - 1))
    bad = []
    for tr in range(300):
        T, h, w = int(rng.integers(3, 30)), int(rng.integers(6, 40)), int(rng.integers(10, 300))
        if tr % 3 == 0:
            st = rng.integers(0, 256, (T, h, w)).astype(np.uint8)
        else:                                            # 逐字长出来的亮字 / 暗字，加一点噪声
            st = np.repeat(rng.integers(30, 90, (1, h, w)), T, 0)
            n = max(2, w // h)
            for t in range(T):
                for j in range(min(n, int(n * t / (0.7 * T)))):
                    st[t, h // 4: 3 * h // 4, j * h + 1: (j + 1) * h - 1] = 220 if tr % 3 == 1 else 5
            st = np.clip(st + rng.integers(-3, 4, st.shape), 0, 255).astype(np.uint8)
        near = st.copy()                                 # 和首帧的相关系数贴着门限
        near[1:] = st[0]
        j = int(rng.integers(0, h * w))
        near[1:].reshape(T - 1, -1)[:, :j] = rng.integers(0, 256, j)
        for x in (st, near):
            if TF._first_unlike(x) != TF._first_unlike_ref(x):
                bad.append(("first_unlike", tr))
        g1, g2 = TF.glyph_score(st, st[-1]), glyph_ref(st, st[-1])
        if g1.dtype != g2.dtype or not np.array_equal(g1, g2, equal_nan=True):
            bad.append(("glyph_score", tr))
        ts = list(range(0, T * 16_667, 16_667))
        n = int(rng.integers(2, 20))
        if TF.cell_fit(st, ts, 1, w - 1, n) != cell_ref(st, ts, 1, w - 1, n):
            bad.append(("cell_fit", tr))
    check("**整数矩版\"不像首帧\"、计数版字形一致率 / 逐字格和原式逐位相同**（300 组随机 / 逐字长出 + 贴门限的叠）", not bad, bad[:5])


def t_edge_refine() -> None:
    """两遍合一遍（edge_refine，reuse-budget 计划 §7.6）：合成一段打字机，帧环 + 链事件 -> `edge` 证据，
    再从证据重建 measured 走 refine_boundaries.apply——三个时刻都要落在合成的真值上。时间全是帧自己的 `t_us`（frame-pts 计划）。"""
    print("edge_refine（两遍合一遍）")
    import threading
    import types

    import numpy as np

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from flowocr.extract import edge_refine as ER
    from flowocr.extract import framesource as fs
    from flowocr.extract import ocr_args
    from flowocr.extract import ocr_parallel
    from flowocr.extract.framegrid import AuxClock, Axis, Clock, SampleClock
    from flowocr.analyze import refine_boundaries as rb

    dual = fs.build_ffmpeg_cmd("a.mp4", window=window2(0, 8), axis=Axis((1, 60)),
                               aux={"clock": [30, 1, 2, 1], "scale": 0.5, "url": "tcp://127.0.0.1:5"})
    ds = " ".join(dual)
    check("**同一次解码分两路**：split 双输出，采样帧走 pipe:1（带 showinfo）、辅助流走 TCP（灰度、缩、按辅助桶取帧），都读到 EOF",
          "-filter_complex" in dual and "split=2" in ds and "showinfo@main=checksum=0[sa]" in ds
          and "format=gray,showinfo@aux=checksum=0[sb]" in ds
          and dual[dual.index("pipe:1") - 1] == "nv12" and dual[-1] == "tcp://127.0.0.1:5" and "-frames:v" not in dual
          and "-vf" not in dual, ds)
    # 双输出和 hwaccel 组合得了（辅路 scale_cuda 在显存里缩），细节在 t_frame_grid。这里只钉住"缩放要的宽高没给就报错"
    check("硬解辅路没给宽高就报错（scale_cuda 不吃表达式）",
          raises(lambda: fs.build_ffmpeg_cmd("a.mp4", window=window2(0, 8), axis=Axis((1, 60)), hwaccel="cuda",
                                             aux={"clock": [60, 1, 2, 1], "scale": 0.5, "url": "tcp://x"})))
    vid = str(Path(__file__).resolve())
    cfg0 = ocr_args.config_of([vid, "--out", "x"])
    offs = [ocr_args.config_of([vid, "--out", "x", *extra]) for extra in
            (["--no-refine-fused"], ["--no-reuse-v2"], ["--no-reuse"])]
    check("**`--refine-fused` 是默认**（owner 09-17，能开就开）：默认就在 config 里；`--no-refine-fused`、"
          "`--no-reuse-v2`、`--no-reuse` 时自动不开（`--hwaccel` 不在这张名单里）、那组键一个不留（OPT_IN_FUSED）",
          ocr_args.OPT_IN_FUSED <= set(cfg0) and cfg0["refine_fused"] is True
          and all(not (ocr_args.OPT_IN_FUSED & set(c)) for c in offs))
    m = ocr_parallel.merge_metas([{"edge": {"on": 2, "worker_busy_sec": 1.5, "aux_last_key": 5_000_000},
                                   "reuse_v2": {"cache_peak": 7_000_000, "refresh": 3},
                                   "advanced_us": 1, "requested_us": 1, "complete": True},
                                  {"edge": {"on": 3, "worker_busy_sec": 0.25, "aux_last_key": 9_000_000},
                                   "reuse_v2": {"cache_peak": 6_000_000, "refresh": 4},
                                   "advanced_us": 1, "requested_us": 1, "complete": True}],
                                 {}, wall=1.0, cuts_sec=[0.5])
    check("--workers 拼 _meta 时 edge / reuse_v2 的计数逐键相加；辅助流最后一帧的位置（aux_last_key）取最大",
          m["edge"] == {"on": 5, "worker_busy_sec": 1.75, "aux_last_key": 9_000_000}, m.get("edge"))
    check("**峰值不相加**：三个 worker 各自的缓存峰值取 max，不然 8 MB 上限会被拼成 21 MB（2026-09-17）",
          m["reuse_v2"] == {"cache_peak": 7_000_000, "refresh": 7}, m.get("reuse_v2"))
    check("并行产物也记实际用了几个 worker（不然读的人分不出『三个 worker』和『旧产物』）",
          m["workers_effective"] == 2, m.get("workers_effective"))
    check("`--refine-aux` 是产物参数（noop 不产 edge）：空臂和默认的 config 必须不等",
          "refine_aux" in cfg0 and not ocr_args.same_arm([vid, "--out", "x"], [vid, "--out", "x", "--refine-aux", "noop"]))
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
    # obs 指纹表的 import 闭包（2026-09-24：新加的选帧模块决定证据窗口取哪些帧，漏进表里差点没人发现）。
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
    fp_ocr = set(ocr_args.CODE_FP_FILES)
    check("OCR 指纹覆盖 ORT 那条路（recdecode / recort / ortclient / 服务端）",
          {"src/flowocr/extract/recdecode.py", "src/flowocr/extract/recort.py", "src/flowocr/extract/ortclient.py",
           "src/flowocr/extract/ort_server.py"} <= fp_ocr,
          sorted(fp_ocr))
    import json
    from flowocr.analyze import refine_boundaries as RB
    check("回抠指纹覆盖吸附目标点的时间桶（framegrid：结算时把候选时刻吸附到哪些点由它定）",
          "src/flowocr/extract/framegrid.py" in RB.CODE_FP_FILES, RB.CODE_FP_FILES)
    # 五张表 2026-09-21 统一成仓库相对路径（flowocr.provenance 文件头）；basename 混进来就是又一处旧口径
    check("五张指纹表全是仓库相对路径（tools/… src/… `explore/`…）",
          all("/" in f for m in (importlib.import_module("flowocr.analyze.build_tracks"), RB, ocr_args,
                                 importlib.import_module("flowocr.analyze.gamescript"), importlib.import_module("flowocr.analyze.scriptmatch"))
              for f in m.CODE_FP_FILES))
    # **运行时必需的源文件必须在版本库里**：`.gitignore` 的 `/explore/*` 曾把 `ort_server.py` 整个吞掉
    import subprocess
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True, check=True).stdout.split())
    need = set(ocr_args.CODE_FP_FILES) | set(RB.CODE_FP_FILES) | set(importlib.import_module("flowocr.analyze.build_tracks").CODE_FP_FILES)
    check("**指纹里的每个文件都在 git 里**（不然新 checkout 上根本没有这份代码）",
          need <= tracked, sorted(need - tracked))
    ring = ER.FrameRing(5)
    ring.release(10_000)
    for i in range(15):
        ring.push(i * 1000, np.zeros((2, 2), np.uint8))
    check("**帧环：放掉之后才推进来的旧帧不占位**（第一版就是被这些旧帧填满而死锁的）",
          len(ring.frames) == 5 and ring.highest == 14_000 and ring.dropped == 10 and ring.wait_for(14_000),
          (len(ring.frames), ring.highest, ring.dropped))
    r2 = ER.FrameRing(100)
    for k in (0, 16_667, 33_333, 116_667, 133_333, 150_000, 400_000):      # 键不等距（VFR、缺口）
        r2.push(k, np.full((1, 1), k % 251, np.uint8))
    r2.release(30_000)
    check("**帧环按 t_us 做键、二分取帧**：键不等距时 [lo, hi] 取的就是落在里面的帧（升序）；放帧按时刻；键不递增当场抛",
          [k for k, _ in r2.take(20_000, 150_000)] == [33_333, 116_667, 133_333, 150_000]
          and [k for k, _ in r2.take(0, 10**9)] == [33_333, 116_667, 133_333, 150_000, 400_000]
          and r2.take(200_000, 399_999) == [] and r2.covers(30_000) and not r2.covers(29_999)
          and raises(lambda: r2.push(400_000, np.zeros((1, 1), np.uint8)), RuntimeError))
    clk60 = AuxClock(Clock(60), SampleClock(2))
    er2 = ER.EdgeRefiner(start_us=0, width=64, height=32, scale=1.0, clock=clk60.as_list(), ref_fps=60, thr=.8)
    seg2 = er2._open(7, {}, "ab", [4, 4, 40, 20], 500_000, 5)
    seg3 = er2._open(8, {}, "ab", [4, 4, 40, 20], 20_000, 5)
    er2.abort()
    check("**打字机窗从上一个采样帧再往前 LEAD 起、按时间取环里的帧**（不再对齐网格）；不越过窗口起点",
          seg2.lo == 450_000 and seg3.lo == 0, (seg2.lo, seg3.lo))
    check("**落盘前等的是『这一批开的段都停变了』那一批**，不是这一批本身（辅助流落后时证据会挂在已落盘的行上）",
          er2.settled_by(10) == 10 + ER.MAX_OPEN_SAMPLES + 1)
    check("放帧按上一个采样帧的时刻往回留：一个名义间隔 + max(LEAD, 半个间隔 + END_LEAD)（VFR 上采样帧常晚到，按名义间隔从当前帧倒推会判 short）",
          er2.interval_us == 500_000 and er2.keep_back_us == 500_000 + 300_000 + 1)
    # 结尾窗起点：末次观测 − 0.5 个名义采样间隔 − 50 ms（owner 2026-11-23）。**CFR 上和旧的帧号口径逐位相同**：
    # 旧 = 下一格 − round(1.5 × 间隔帧数) − round(50 ms 的帧数)、再落到辅助网格上；终点 = 下一格 + 200 ms 的帧数、落到辅助网格上
    bad = []
    for fps, q, step in ((60, 30, 1), (60, 30, 2), (60, 6, 1), (30, 15, 1), (24, 12, 1), (60, 12, 4)):
        us_ = 1e6 / fps
        for last in range(q * 3, q * 40, q):
            t_end = last + q
            old_lo = t_end - int(round(1.5 * (t_end - last))) - int(round(ER.END_LEAD_US / us_))
            old_lo += (-old_lo) % step                  # 旧的往下落到辅助网格上；现在按时间取，不对齐网格（取环里 ≥ lo 的第一帧）
            old_hi = t_end + int(round(ER.END_TAIL_US / us_))
            old_hi += (-old_hi) % step
            lo, hi = ER.end_window(round(last * us_), round(t_end * us_), q * us_)
            new = [i for i in range(0, q * 50, step) if lo <= round(i * us_)]
            new_hi = [i for i in range(0, q * 50, step) if round(i * us_) <= hi][-1]
            if new[0] != old_lo or (fps % 5 == 0 and new_hi != old_hi):
                bad.append((fps, q, step, last, new[0], old_lo, new_hi, old_hi))
    check("**结尾窗起点在 CFR 上和旧的帧号口径取到同一帧**（60/30/24 fps、各种采样间隔；辅助流隔帧时旧的还往下对齐网格，现在不对齐，"
          "比的是不对齐时的那一帧；owner 2026-11-23）；"
          "终点 t_end + 200 ms 在 200 ms 是整帧数的帧率上也相同（24 fps 上旧的按帧取整多取 8 ms，现在按微秒）",
          not bad, bad[:3])
    check("结尾窗在 VFR 上不跟着实际间隔往回伸：t_end 晚很多时起点照样是末次观测 − 半个名义间隔 − 50 ms；不早于链首",
          ER.end_window(10_000_000, 15_000_000, 500_000) == (9_700_000, 15_200_000)
          and ER.end_window(10_000_000, 10_500_000, 500_000, first_us=9_900_000) == (9_900_000, 10_700_000))
    # **帧环的容量必须装得下批 det 的前瞻**（2026-09-17 实测的死锁），按时间算（frame-pts 计划第 5 节）：
    # 跨度 = max(5, strides, 在途 + 4) 个名义采样间隔 + LEAD + END_LEAD + END_TAIL，× (辅助帧率 + 采样帧率) + 8
    check("容量按时间算：没有在途时 5 个采样间隔 + 窗口头尾，×（辅助 60 + 采样 2）+ 8 = 182 帧",
          ER.ring_capacity(500_000, 60, 2) == 182, ER.ring_capacity(500_000, 60, 2))
    check("**容量 = 在途 + 4 个间隔**：默认 20 -> 24 个间隔、拆进程 36 -> 40 个（`--refine-ring` 更大时按它）；辅助流 30 时约减半",
          ER.ring_capacity(500_000, 60, 2, 5, in_flight=20) == -(-(24 * 500_000 + 300_000) * 62 // 1_000_000) + 8
          and ER.ring_capacity(500_000, 60, 2, 5, in_flight=36) == -(-(40 * 500_000 + 300_000) * 62 // 1_000_000) + 8
          and ER.ring_capacity(500_000, 60, 2, 48, in_flight=20) == -(-(48 * 500_000 + 300_000) * 62 // 1_000_000) + 8
          and ER.ring_capacity(500_000, 30, 2) == -(-2_800_000 * 32 // 1_000_000) + 8)
    # 容量够用：按辅助桶取的辅助流里，连续 n 个采样帧之间的辅助帧数不超过 (n + 1) 个名义间隔 × (辅助 + 采样) 帧率——
    # VFR 上空桶里没有帧，不让上界失效。合成一条不规则的 120 fps 时间轴（片段 4 那种稀密不均）核这条上界
    import random as _rnd
    _r = _rnd.Random(5)
    _t, _ts = 0, []
    while _t < 120 * 60:
        _ts.append(_t)
        _t += _r.choice([1, 1, 2, 3, 5, 8, 13, 40])
    _clk = AuxClock(Clock(60), SampleClock(2))
    _pa, _sel, _samp = None, [], []
    for _p in _ts:
        _c = _clk.cells(_p, Axis((1, 120)))
        if _c != _pa:
            _sel.append(_p)
            if _pa is None or _c[0] != _pa[0]:
                _samp.append(_p)
        _pa = _c
    _worst = 0
    for _i in range(len(_samp) - 30):
        _n = sum(1 for _p in _sel if _samp[_i] <= _p <= _samp[_i + 30])
        _worst = max(_worst, _n / (31 * 0.5 * 62))
    check("容量按时间算够用：不规则时间轴上任意 30 个连续采样帧之间的辅助帧数不超过 31 个名义间隔 ×（辅助 + 采样）帧率",
          _worst <= 1.0, round(_worst, 3))
    from types import SimpleNamespace as _NS
    from flowocr.extract import decode_proc as _DP
    base = dict(prefetch=4, det_batch=4, det_prefetch=2, det_lookahead=1, rec_window=4, decode_shards=0,
                recover_async=False, rec_async=True)
    n0 = ER.ring_in_flight(_NS(**base))
    grows = {k: ER.ring_in_flight(_NS(**{**base, k: base[k] + 1})) > n0
             for k in ("prefetch", "det_batch", "det_prefetch", "det_lookahead", "rec_window")}
    check("在途逐项算全：取帧预取 + det 批 ×（1 + det 预取 + 流水）+ rec 窗口 + 拆进程的槽，每一项加 1 在途都跟着涨"
          "（漏一项就是 09-23 那种环满）",
          n0 == 4 + 4 * (1 + 2 + 1) + 4 and all(grows.values())
          and ER.ring_in_flight(_NS(**{**base, "decode_shards": 2})) == n0 + _DP.slots_for(4, 2)
          and ER.ring_in_flight(_NS(**{**base, "det_batch": 1})) == 4 + 1 * (1 + 2) + 4,
          (n0, grows))
    check("回补异步时扣着等回补的回抠批次也压着帧环：在途加 RECOVER_HOLD；同步 rec 池用不上它、不加",
          ER.ring_in_flight(_NS(**{**base, "recover_async": True})) == n0 + ER.RECOVER_HOLD
          and ER.ring_in_flight(_NS(**{**base, "recover_async": True, "rec_async": False})) == n0)
    stall = ER.FrameRing(2, stall_sec=0.2)
    for i in range(4):                               # 塞满又没人取
        stall.push(i, np.zeros((2, 2), np.uint8))
    check("**互等有出口**：环满、没人取、流没结束，卡过 stall_sec 就抛，不再挂到天荒地老"
          "（同一条教训的第三次：§7.6 的帧环、§7.12 的负 pts）",
          stall.stalled and raises(lambda: stall.wait_for(99), RuntimeError), stall.stalled)

    # 合成：200x40 灰度，暗底 20；框 [20,10,120,30] 里 5 个字格，字 c 在第 30+3c 帧弹出（亮 220），第 gone_at 帧整行消失。
    # 60 fps、采样 10 fps（每 6 帧）：第 5 个采样点（帧 30）新链 "a"，帧 36 "abc"，帧 42 "abcde"，帧 48 起不变，帧 90 那个采样点没框了。
    us = 1_000_000 / 60
    box = [20, 10, 120, 30]

    def frame(i: int, gone_at: int = 90) -> np.ndarray:
        f = np.full((40, 200), 20, np.uint8)
        if i < gone_at:
            for c in range(5):
                if i >= 30 + 3 * c:
                    f[15:25, 25 + 20 * c:35 + 20 * c] = 220
        return f
    CLK = AuxClock(Clock(60), SampleClock(10))

    def run_synth(pts, stale_at: int | None = None, clock=CLK, gone_at: int = 90):
        """喂一遍这段合成素材，返回 (on, off, stats)。`pts(帧号) -> t_us` 是真实时间轴；辅助流只推 `clock` 选中的帧
        （同 ffmpeg 辅路：辅助桶或采样桶换了）。`stale_at` = 在第几个采样点报"这条链的历史被回补改过"。"""
        er = ER.EdgeRefiner(start_us=pts(0), width=200, height=40, scale=1.0, clock=clock.as_list(), ref_fps=60, thr=0.8)

        def feed() -> None:
            prev = None
            for i in range(121):
                c = clock.cells(i, Axis((1, 60)))
                if c != prev:
                    er.ring.push(pts(i), frame(i, gone_at))
                prev = c
            er.ring.close()
        threading.Thread(target=feed, daemon=True).start()
        rows_ = {}
        texts = {5: ("a", "new"), 6: ("abc", "changed"), 7: ("abcde", "changed")}
        for k in range(0, 5):                      # 前几个采样点没有框：工作线程照样记下它们的时刻（"上一个采样帧"从这里取）
            er.sample(k, pts(6 * k), [], [])
        for k in range(5, 15):
            rows_[k] = {"t_us": pts(6 * k), "box": box, "text": "abcde"}
            text, kind = texts.get(k, ("abcde", "same"))
            er.sample(k, pts(6 * k), [(1, rows_[k], box, text, kind)], [], {1} if stale_at == k else ())
        er.sample(15, pts(90), [], [(1, rows_[14], box, "abcde", pts(84))], {1} if stale_at == 15 else ())
        st_ = er.finish(pts(120))
        return rows_[5]["edge"]["on"], rows_[14]["edge"]["off"], st_

    P = lambda i: int(round(i * us))
    on, off, st = run_synth(P)
    check("链首行挂上 `edge.on`：窗从首字前一个采样帧 − LEAD 到停变那个采样帧（µs），模板 = 帧 48",
          st["on"] == 1 and st["off"] == 1 and (on["lo"], on["hi"], on["n"]) == (P(24) - 50_000, P(48), 5)
          and not any(k in on for k in ("a_idx", "a_us", "step", "grid")), (st, on))
    check("首字格开始出现 = 帧 30、尾字格定稿 = 帧 42、逐字格直线首尾也落在 30 / 42（时刻是帧自己的 t_us）",
          on["first_rise"] == P(30) and on["full_glyph"] == P(42)
          and on["cell"] and abs(on["cell"][0] - 30 * us) < us and abs(on["cell"][1] - 42 * us) < us, on)
    check("链末行挂上 `edge.off`：last_us = 末次观测；退回判据在帧 89 还像模板 -> ncc_end = 它后面那一个辅助帧（帧 90）、没到链断的采样点（ncc_open 假）；"
          "曲线说完全显示结束 89 / 完全消失 90",
          off["last_us"] == P(84) and off["ncc_end"] == P(90) and off["ncc_open"] is False
          and off["last_full"] == P(89) and off["gone"] == P(90) and "last_idx" not in off and "ncc_gone" not in off, off)
    check("帧环没死锁、窗口要的帧都进过环", st["frames_pushed"] >= 103 and st["short_windows"] == 0, st)
    # 第二遍：证据 -> measured -> apply()
    run = dict(t_start=P(30), t_end=P(90), t_full_sampled=P(42), text="abcde", box=box, n_obs=10)
    job = rb.make_job(run, P(6))
    job["measured"] = ER.measured_from_records(on, on, off, run["t_end"], P(6), CLK)
    rb.apply(job, None, None)
    check("**从 obs 证据重建后三个时刻落在合成真值上**：首字 30、全字 42、完全显示结束 89、完全消失 90（帧）",
          job["refined"] and abs(run["t_start"] - 30 * us) < 1 and abs(run["t_full"] - 42 * us) < 1
          and abs(run["t_end"] - 90 * us) < 1 and abs(run["t_full_end"] - 89 * us) < 1 and run["t_end_how"] == "curve", run)

    # 末段在动的门（defaults.md 1.65）：同一份证据，run 的采样级终点放在帧 96（晚于真值 90），看终点认不认结尾窗
    def settle(boxes, starts=True, t_end=96, **job_extra):
        r = dict(run, t_start=P(30), t_end=P(t_end), t_full=None, **({"boxes": boxes} if boxes else {}))
        r.pop("t_end_how", None), r.pop("t_full_end", None)
        j = dict(rb.make_job(r, P(6)), **job_extra)
        j["measured"] = ER.measured_from_records(on if starts else None, on if starts else None, off, r["t_end"], P(6), CLK)
        rb.apply(j, None, None)
        return r, j
    t78, t84 = P(78), P(84)
    bx = [200, 10, 300, 30]                                                    # 轨迹离屏边远（字高 20 px）
    mv = lambda k: [bx[0] - k, bx[1], bx[2] - k, bx[3]]                        # 往左飞 k px
    r_fly, j_fly = settle([[P(30), bx], [t78, mv(40)], [t84, mv(60)]])                  # 最后一步 20 px = 1 个字高
    r_slow, _ = settle([[P(30), bx], [t78, mv(40)], [t84, mv(48)]])                     # 最后一步 8 px = 0.4 个字高
    r_stop, _ = settle([[P(30), bx], [P(42), mv(40)], [t84, mv(48)]])                   # 早停：收尾点隔 7 个间隔、摊下来 0.06
    r_out, _ = settle([[P(30), bx], [t78, [0, 10, 40, 30]], [t84, [0, 12, 20, 28]]])     # 出屏：左沿贴屏边、右沿走 20 px，框被裁矮
    r_still, _ = settle(None)
    r_bare, j_bare = settle([[P(30), bx], [t78, mv(40)], [t84, mv(60)]], starts=False)
    r_grow, _ = settle([[P(30), bx], [t78, mv(40)], [t84, [bx[0] - 40, bx[1], bx[2] + 20, bx[3]]]])   # 只有右沿走 60 px：形变
    r_late, _ = settle([[P(30), bx], [t78, mv(40)], [t84, mv(60)]], t_end=84)        # 证据（帧 90）晚于采样级（帧 84）
    r_union, _ = settle([[P(30), bx], [t78, mv(40)], [t84, [bx[0] - 60, bx[1], bx[2] - 40, bx[3]]]], off_box=mv(60))
    r_right, _ = settle([[P(30), bx], [t78, [150, 10, 195, 30]], [t84, [170, 10, 200, 30]]], frame_size=(200, 100))  # 右沿贴屏边
    check("**末段在动的 run 终点留采样级**（t_end_how = moving，不填完全显示结束），起点照样回抠到帧 30；出屏的（贴边那条边不动、框被裁矮）也算",
          r_fly["t_end"] == P(96) and r_fly["t_end_how"] == "moving" and r_fly["t_full_end"] is None
          and abs(r_fly["t_start"] - 30 * us) < 1 and j_fly["refined"] and j_fly.get("moving_end")
          and r_out["t_end_how"] == "moving" and r_out["t_end"] == P(96), (r_fly, r_out))
    check("末段在动的门不碰静止 / 慢移 / 早停 / 只有一条边在动（形变）的 run：终点照旧认证据（帧 90、curve）",
          all(abs(r["t_end"] - 90 * us) < 1 and r["t_end_how"] == "curve" for r in (r_still, r_slow, r_stop, r_grow)),
          [(r["t_end"], r.get("t_end_how")) for r in (r_still, r_slow, r_stop, r_grow)])
    check("末段在动的门只拒收比采样级早的终点：证据晚于采样级的照收（帧 90、curve）",
          abs(r_late["t_end"] - 90 * us) < 1 and r_late["t_end_how"] == "curve", r_late)
    check("末段在动的门：收尾点是并集时按 obs 里最后一次观测的真实框判平移；右沿贴屏边（画面尺寸来自产物）只看左沿",
          r_union["t_end_how"] == "moving" and r_union["t_end"] == P(96)
          and r_right["t_end_how"] == "moving" and r_right["t_end"] == P(96), (r_union, r_right))
    check("末段在动、起点也没证据的 run：时刻全不动、不算回抠成功，但 t_end_how 记 moving（数得出来）",
          r_bare["t_end"] == P(96) and r_bare["t_end_how"] == "moving" and not j_bare["refined"]
          and j_bare.get("moving_end"), r_bare)
    # **开放边界**（在线证据）：字其实还在屏上、链却在帧 90 那个采样点断了（det 切碎、读数变了）——退回判据一路像到链断的采样点
    on_o, off_o, _ = run_synth(P, gone_at=110)
    m_o = ER.measured_from_records(None, None, off_o, P(90), P(6), CLK)
    check("**开放边界**：最后还像的帧到了链断那个采样点（帧 90）-> 工作线程记 ncc_open、ncc_end = 帧 91；结算记 open、t_end 只是下界、"
          "不填完全显示结束；早一帧就不像了的（上面那份）照旧是 ncc",
          off_o["ncc_open"] is True and off_o["ncc_end"] == P(91) and m_o[4] == "open" and m_o[1] and m_o[3] is None
          and m_o[0] == P(91) and ER.measured_from_records(None, None, dict(off, gone=None), P(90), P(6), CLK)[4] == "ncc",
          (off_o, m_o))
    check("没有任何证据 = None（该条原样退回，同 no_evidence）",
          ER.measured_from_records(None, None, None, 1, 1, CLK) is None
          and ER.measured_from_records({"short": True, "lo": 0, "hi": 1}, None, None, 1, 1, CLK) is None)
    # **辅助流不是每帧都要时**（60 fps 上辅助 24 fps + 采样 10 fps，两种桶的公共细分）：窗口按时间取真实的辅助帧、证据不 short；
    # 阶段 2 重建的 ts 是 [lo, hi] 里辅助流的目标点（辅助桶与采样桶的 T_k），三个时刻离真值不超过辅助帧的间距
    C24 = AuxClock(Clock(24), SampleClock(10))
    ong, offg, stg = run_synth(P, clock=C24)
    sg = ER.sig_from_records(ong, ong, C24)
    check("**辅助流隔帧（24 fps）：证据齐全；重建的 ts 是目标点**（证据里不存每帧时刻）",
          stg["short_windows"] == 0 and sg is not None and sg["ts"] == C24.targets_us(ong["lo"], ong["hi"])
          and sg["ts"][0] >= ong["lo"] and sg["ts"][-1] <= ong["hi"], (stg, sg and sg["ts"][:6]))
    check("辅助流隔帧：首字 / 全字 / 消失离真值不超过 3 帧（辅助帧间距 2~3 帧）",
          abs(ong["first_rise"] - 30 * us) <= 3 * us and abs(ong["full_glyph"] - 42 * us) <= 3 * us
          and offg["gone"] is not None and abs(offg["gone"] - 90 * us) <= 3 * us,
          (ong["first_rise"] / us, ong["full_glyph"] / us, offg["gone"] and offg["gone"] / us))
    # **逐帧候选也吸附到目标点**：pts 按毫秒取整的素材（yuka）上证据里的帧时刻离目标点零点几毫秒，不吸附就和吸附过的外推值 /
    # 逐字格直线混着比——FUSE_TOL_US 正好 3 帧，first_old 早 0.33 ms 就和外推值拆成两簇、首字落到采样级起点上。
    # 数取自 yuka-f5 15063.5 s「すでに十分お話はしましたし」的真实证据（旧臂 15063.400、修之前 15063.500，真值 15063.417）
    from flowocr.extract import typewriter_fuse as TF
    C60 = AuxClock(Clock(60), SampleClock(2))
    on_y = {"lo": 15_062_950_000, "hi": 15_064_500_000, "n": 13, "text": "すでに十分お話はしましたし",
            "first_old": 15_063_383_000, "first_rise": 15_063_517_000, "full_glyph": None,
            "cell": [15_063_540_250.0, 15_063_711_250.0]}
    sy = ER.sig_from_records(on_y, on_y, C60)
    est_y = TF.combine(sy, 15_063_500_000, 15_064_000_000, 500_000,
                       [(15_063_500_000, 5), (15_064_000_000, 13)], None, 16_577.380952467236)
    check("**逐帧候选（first_old / first_rise / full_glyph）吸附到目标点**，和外推值按同一组点比 3 帧容差："
          "毫秒取整的 first_old 不再被拆出外推值那一簇（yuka-f5 那条首字 15063.400，不是采样级 15063.500）",
          sy["first_old"] == 15_063_383_333 and sy["first_rise"] == 15_063_516_667 and sy["full_glyph"] is None
          and est_y["first"] == 15_063_400_000, (sy and {k: sy[k] for k in ("first_old", "first_rise")}, est_y))
    # **时刻就是帧自己的 pts**：同一段像素、时间轴整体挪 0.25 s（15 帧，目标点也正好挪 15 个），三个时刻和窗口端点必须跟着挪
    SH = 250_000
    on2, off2, _ = run_synth(lambda i: P(i) + SH)
    check("**证据里的时刻都是帧自己的 t_us**：时间轴整体偏 0.25 s，窗口端点、首字 / 全字 / 消失跟着偏",
          on2["lo"] - on["lo"] == SH and on2["hi"] - on["hi"] == SH and on2["first_rise"] - on["first_rise"] == SH
          and on2["full_glyph"] - on["full_glyph"] == SH and off2["gone"] - off["gone"] == SH
          and off2["last_full"] - off["last_full"] == SH and off2["ncc_end"] - off["ncc_end"] == SH, (on2, off2))
    check("重建也一样（ncc_end / 目标点都跟着挪）",
          ER.measured_from_records(on2, on2, off2, 0, P(6), CLK)[0] - ER.measured_from_records(on, on, off, 0, P(6), CLK)[0] == SH
          and ER.sig_from_records(on2, on2, CLK)["ts"][0] - ER.sig_from_records(on, on, CLK)["ts"][0] == SH)
    # **回补把变化点前移 -> 这一段的证据作废**（审计 P2）：帧环只留几个采样间隔，重新量不可能，
    # 所以标 stale、第二遍当没量到（口径同 short），该 run 退回采样级
    on3, off3, st3 = run_synth(P, stale_at=7)
    check("回补改写历史 -> `edge` 证据标 stale、当没量到（不是拿错窗口的时刻去写产物）",
          on3.get("stale") and st3["stale_windows"] >= 1 and not ER.usable(on3)
          and ER.measured_from_records(on3, on3, dict(off3, stale=True), 7, 1, CLK) is None, (on3.get("stale"), st3))
    check("stale 的 off 不参与结尾判决，但 on 还能用时打字机那一侧照旧",
          ER.usable(off) and not ER.usable(dict(off, stale=True))
          and ER.measured_from_records(on, on, dict(off, stale=True), 123, 1, CLK)[0] == 123)
    # refine_from_obs：按 run 的首行 / 末行认领证据（同一行位置）
    d = Path(tempfile.mkdtemp())
    obs = d / "o.jsonl"
    obs.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"_meta": {"aux_clock": CLK.as_list(), "frame_key": "t_us"}},
        {"t_us": P(30), "box": box, "text": "a", "edge": {"on": on}},
        {"t_us": P(84), "box": box, "text": "abcde", "edge": {"off": off}},
        {"t_us": P(84), "box": [20, 200, 120, 220], "text": "zzz", "edge": {"off": off}},
    ]) + "\n", encoding="utf-8")
    run2 = dict(run)
    j2 = rb.make_job(run2, P(6))
    ev = rb.refine_from_obs([j2], obs, us, P(6))
    check("refine_from_obs：首行的 on + 末行的 off 认领到这条 run，别的行位置的 off 不认（默认救回作废证据，这里没有作废的）",
          ev == {"on_rows": 1, "off_rows": 2, "jobs": 1, "jobs_with_on": 1, "jobs_with_off": 1,
                 "off_rows_salvaged": 0, "jobs_with_off_salvaged": 0} and "measured" in j2, ev)
    # 采样间隔不等长（VFR / 不整除）：段首证据在 run 起点前 1.03 个名义间隔那一票上，也要认领到
    run3 = dict(run, t_start=round(30 * us + 1.03 * 6 * us))
    j3 = rb.make_job(run3, P(6))
    ev3 = rb.refine_from_obs([j3], obs, us, P(6))
    check("refine_from_obs：「前一票」按 1.5 个名义间隔找（间隔比名义长一帧时照样认领段首证据）",
          ev3["jobs_with_on"] == 1, ev3)
    obs_old = d / "old.jsonl"
    obs_old.write_text(json.dumps({"_meta": {"stride": 6}}) + "\n", encoding="utf-8")
    check("refine_from_obs：obs 没记 aux_clock（旧的帧号口径）就报错，不拿别的时间桶吸附",
          raises(lambda: rb.refine_from_obs([rb.make_job(dict(run), P(6))], obs_old, us, P(6)), ValueError))


def t_slot_fits_why() -> None:
    print("build_tracks.slot_fits_why")
    import random

    from flowocr.analyze import build_tracks as bt

    rng = random.Random(7)

    def sig():
        x0 = rng.uniform(0, 1500); y0 = rng.uniform(0, 1000)
        ch = rng.uniform(8, 60)
        h = ch * rng.choice((1.0, rng.uniform(2, 10)))         # 列长 > 列宽的是竖排的列
        return (x0 + rng.uniform(10, 300) / 2, x0, x0 + rng.uniform(10, 400),
                y0, y0 + rng.uniform(10, 300), h, ch, 1.0 if h > ch else 0.0)

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


def t_within_regions() -> None:
    """非区域轨只装区域轨已有的 run（2026-10-05：`--srt-mode body` 的筛行按上下文定，
    同一条 run 在区域轨里被筛、在并带主轨 / 名牌轨里留下，写到最后被 schema 拦下，12 臂崩 2 臂）。"""
    print("非区域轨的段：只留区域轨里已有的 run")
    from types import SimpleNamespace

    from flowocr.analyze import build_tracks as bt

    a, b, c = (SimpleNamespace(text=t) for t in ("甲", "乙", "丙"))
    segs = [(0, 10, [a, b]), (10, 20, [c]), (20, 30, [b])]
    got, dropped = bt.within_regions(segs, {id(a), id(b)})
    check("区域轨筛掉的行拿掉、拿空的段整段丢、其余原样",
          [(s, e, [r.text for r in rs]) for s, e, rs in got] == [(0, 10, ["甲", "乙"]), (20, 30, ["乙"])] and dropped == 1,
          (got, dropped))
    src = (ROOT / "src" / "flowocr" / "analyze" / "build_tracks.py").read_text(encoding="utf-8")
    check("并带主轨和名牌轨的段都过这道闸", src.count("within_regions(cue_segments(") == 2)


def t_column_regions() -> None:
    """竖排列的相邻判据只有一份（`textdir.columns_adjacent`，阶段 3 拆 `col` 行也用它）：建轨并区照旧按列轨的中位数判、宽比门是 `--region-h-ratio`。"""
    print("竖排列：建轨并区的相邻判据")
    from flowocr.analyze import build_tracks as bt
    from flowocr.artifacts import textdir

    check("列宽比门的默认值和建轨的 REGION_H_RATIO 相等", textdir.COL_W_RATIO == bt.REGION_H_RATIO)
    adj = textdir.columns_adjacent
    a = (0, 100, 50, 20)
    check("判据逐项：cx 差 ≤ 2.2 个较窄列宽且上下有重叠算相邻；远了、宽比超门、上下不重叠都不算；宽比门 0 = 不看",
          [adj(a, (10, 110, 94, 20), 1.7), adj(a, (10, 110, 95, 20), 1.7), adj(a, (10, 110, 80, 40), 1.7),
           adj(a, (10, 110, 80, 40), 0), adj(a, (100, 200, 70, 20), 1.7)] == [True, False, False, True, False])

    def run(box, text):
        return bt.Run(box=box, text=text, t_start=0, t_end=1_000_000, n_obs=1, conf=1.0)
    # 片段 8 的样子：两个挨着的小标签（宽 38）和一整列大字说明（宽 118，在画面另一头），再加一列紧挨着说明、宽一倍多的列
    runs = [run([1126, 810, 1164, 901], "応接室"), run([1070, 808, 1108, 901], "校長室"),
            run([24, 52, 142, 1044], "各々隠れる場所が決定"), run([150, 60, 400, 1000], "とても太い列の字")]
    tracks = [[0], [1], [2], [3]]
    got = sorted(sorted(g) for g in bt.build_regions(runs, tracks, 1920, 1080))
    check("建轨并区：挨着的两列并成一块，远处的列、宽比超门的列各自一块", got == [[0, 1], [2], [3]], got)
    got = sorted(sorted(g) for g in bt.build_regions(runs, tracks, 1920, 1080, h_ratio=0))
    check("--region-h-ratio 0 也关掉列宽比门（挨着的宽列并进来），远处的列仍然分开", got == [[0, 1], [2, 3]], got)


def t_row_order() -> None:
    """cue 里的行序：先分行、行从上往下、行内从左往右（cue-shape 计划的 c）。"""
    print("行序：分行之后行内从左到右")
    from types import SimpleNamespace

    from flowocr.analyze import build_tracks as bt
    from flowocr.artifacts import tracksio

    def run(t, box):
        return SimpleNamespace(text=t, box=box, t_start=0, cy=(box[1] + box[3]) / 2, cx=(box[0] + box[2]) / 2)
    top = run("君は私の凶器。", [400, 860, 700, 905])
    left = run("大魔女を", [400, 921, 560, 966])          # 破折号断开的同一行：左半截纵向中心略低
    right = run("ユキを殺す【犯人】は、", [640, 918, 1000, 964])
    last = run("私の役割だ。", [400, 980, 640, 1025])
    got = [r.text for r in bt.row_order([last, right, top, left])]
    check("破折号断开的同一行按左沿排、行从上往下", got == ["君は私の凶器。", "大魔女を", "ユキを殺す【犯人】は、", "私の役割だ。"], got)
    check("输入顺序不影响结果（segment_runs 拿 id 列表判内容没变）",
          [r.text for r in bt.row_order([left, top, last, right])] == got)
    tall = [400, 860, 900, 966]                          # 一个框装着两行
    check("高框不和旁边的单行框串成一行（字高比 > 1.7）",
          not tracksio.same_row(tall, [950, 860, 1200, 905], "二行の\n高い框", "単行"))
    # 竖排（vertical-text 计划）：三列竖排说明字（片段 5 的 15 s），读序是列从右往左；同一列被切成上下两截时按上沿拼成一列
    c1 = run("紗耶の活躍", [658, 46, 802, 460])
    c2 = run("もっとみんな", [539, 37, 632, 474])
    c3 = run("褒めてよ！", [452, 40, 551, 436])
    got = [r.text for r in bt.row_order([c3, c1, c2])]
    check("竖排的列从右往左", got == ["紗耶の活躍", "もっとみんな", "褒めてよ！"], got)
    rows = tracksio.rows_of_events([{"text": "てみます", "box": [300, 500, 360, 760]}, {"text": "してみます", "box": [200, 100, 262, 420]},
                                    {"text": "セルフロウリュウ", "box": [300, 60, 362, 470]}])
    check("竖排一列切成上下两截按上沿拼回一列、列从右往左", [r["text"] for r in rows] == ["セルフロウリュウてみます", "してみます"],
          [r["text"] for r in rows])
    check("两根竖条左右并排不算同一行（按列宽比重叠）",
          not tracksio.same_row([300, 60, 362, 470], [200, 100, 262, 420], "セルフロウリュウ", "してみます"))
    # 竖条框装着单个窄字（wuwa 文字墙的「Ⅱ」）照横排排：按列算会被排到所有横排行之后
    rows = tracksio.rows_of_events([{"text": "上の行", "box": [100, 100, 400, 140]}, {"text": "Ⅱ", "box": [100, 160, 120, 200]},
                                    {"text": "下の行", "box": [100, 220, 400, 260]}])
    check("竖条框里的单个窄字照横排按纵向排", [r["text"] for r in rows] == ["上の行", "Ⅱ", "下の行"], [r["text"] for r in rows])
    check("一竖一横不算同一行（同一行要按同一个轴排）",
          not tracksio.same_row([100, 100, 130, 160], [140, 105, 300, 155], "縦列", "横の行"))
    _r = [bt.Run(box=[1800, 200, 1840, y1], text="あいうえおかきくけこ"[:n], t_start=0, t_end=1, n_obs=1, conf=1.0) for y1, n in ((300, 3), (600, 9))]
    check("跨窗缝合的字高：竖排的列量列宽，不量列长", [bt.char_h(r) for r in _r] == [40, 40], [bt.char_h(r) for r in _r])

    def _col(y0, y1, n):            # 一个窗里同一处的竖排列：宽 20 px
        return bt.region_geom([bt.Run(box=[1800, y0, 1820, y1], text="あいうえおかきくけこ"[:n], t_start=0, t_end=1,
                                      n_obs=1, conf=1.0)], [0])
    check("跨窗缝合：同一处竖排列，长句窗（200 px）和短句窗（100 px）缝得上（字高比门量列宽）",
          bt.slot_fits(_col(200, 300, 5), _col(200, 400, 10), 1920, anchor=True) is not None)
    check("跨窗缝合：同一处竖排列两窗错开 40 px 缝得上（共边容差按框高，不按列宽）",
          bt.slot_fits(_col(200, 400, 10), _col(240, 440, 10), 1920, anchor=True) is not None)

    def _geom(boxes, text):
        return bt.region_geom([bt.Run(box=b, text=text, t_start=0, t_end=1, n_obs=1, conf=1.0) for b in boxes],
                              list(range(len(boxes))))
    # 片段 2 的读数：横排字幕带 y 788–1036、字高 156；竖排说明字的槽位 y 71–1005、列宽 96、列长 718。
    # 共边容差、跨度比下限按列长算的话两道门都放空（跨度比 934 / (2 × 156) = 2.99 ≤ 3），字幕带缝进了竖排那块
    _band = _geom([[80, 788, 1826, 944], [80, 880, 1826, 1036]], "何なん俺お前しか会えへんの？")
    _cols = _geom([[700, 71, 796, 789], [600, 287, 696, 1005]], "さくちゃんは意外と力持ち")
    check("跨窗缝合：横排字幕带不缝进同一片高度上的竖排列（有一边横排时空间尺度按字号）",
          bt.slot_fits(_band, _cols, 1920, anchor=True) is None and bt.slot_fits(_cols, _band, 1920, anchor=True) is None,
          bt.slot_fits_why(_band, _cols, 1920, anchor=True))
    from flowocr.analyze import uigate

    def _top(texts):                   # 每句在屏 10 s、接着排
        f = uigate.Footprint([0, 0, 100, 30], [uigate.Item((0, 0, 100, 30), k * 10_000_000, (k + 1) * 10_000_000, t)
                                               for k, t in enumerate(texts)])
        return uigate.measure(f, 10_000_000 * len(texts), 500_000)["top_share"]
    check("静态 UI 的\"同一句\"：读法在抖的标签、在变的分数还是同一句；轮着换的提问（同一句式换两个字也算）各句不是",
          _top(["Dummy", "Duininy", "Duminy", "Dumny"]) >= 0.5 and _top(["スコア：1000", "スコア：4800", "スコア：600"]) == 1.0
          and _top(["Q・好きな食べ物は？", "Q・休みの日は何してる？", "Q・横浜の印象は？", "Q・絵を描くのは得意？"]) <= 0.25
          and _top(["Q・好きな映画は？", "Q・好きな音楽は？", "Q・好きな料理は？", "Q・好きな動物は？"]) <= 0.25)
    chain = [[0, 0, 100, 40], [110, 25, 210, 85], [220, 70, 320, 110]]   # 中间一个略高的框同时压着上下两行
    check("三个框链式重叠（首尾不重叠）：不做传递闭包、不把上下两行串成一行",
          tracksio.row_index(chain, ["a"] * 3)[0] != tracksio.row_index(chain, ["a"] * 3)[2], tracksio.row_index(chain, ["a"] * 3))


def t_cue_join() -> None:
    """同句合并（`--cue-join on`，cue-shape 计划的 b）：并哪些、不并哪些、藏哪些成员。"""
    print("同句合并：并 / 不并 / 藏")
    from flowocr.analyze import build_tracks as bt
    from flowocr.artifacts import tracksio

    S = 1_000_000
    TOL = S // 2

    def run(text, t0, t1, y=900, x0=400, n=None, name=False):
        r = bt.Run([x0, y, x0 + 40 * max(1, len(text)), y + 40], text, int(t0 * S), int(t1 * S), n if n is not None else max(1, int(2 * (t1 - t0))), 0.9)
        r.nameplate = name
        return r

    def segs_of(rs, join=True):
        return bt.cue_segments(rs, "segment", end_tol_us=TOL, join=join)

    def texts(segs):
        return [[r.text for r in a] for _, _, a in segs]

    # 打字机半截：OCR 把 · / ・ 读得不一样、run 断了
    l1 = run("そして館長は言い、", 0, 10, y=900)
    half = run("飼育·展", 1.5, 3, y=960)             # 读到 3 次：不是杂行，得按"被接上"藏
    full = run("飼育・展示することを決めたのです", 3, 10, y=960)
    got = segs_of([l1, half, full])
    check("半截行续上：并成一条，半截行留作成员", len(got) == 1 and {r.text for r in got[0][2]} == {l1.text, half.text, full.text}, texts(got))
    check("半截行投影时藏（partial）", bt.seg_hide(*got[0], TOL)[1] == {id(half)}, bt.seg_hide(*got[0], TOL))
    check("开头相似：短行错 1 个字照认，错 2 个不认", bt.head_similar("飼育·展", "飼育・展示することを")
          and not bt.head_similar("飼育·展", "飼料が展示"))
    check("以句末标点结尾的一行只认精确前缀（魔裁 `……どうして。` 和下一句 `どうしてユキちゃんは、`）",
          not bt.head_similar("……どうして。", "どうしてユキちゃんは、") and bt.head_similar("君は私の凶器。", "君は私の凶器。大魔女を"))
    check("句末标点读得不一样、后面没字了：照认（同一句重读）", bt.head_similar("Amazonにて販売中!", "Amazonにて販売中！"))
    title, name = run("傭兵", 0, 20, y=860, n=40), run("シュラム", 0, 20, y=820, n=40)
    lead2, line = run("がのオレ", 3, 4, n=2), run("がのオレでも、人間とフェイの関係が", 4, 19)
    check("首帧碎片 + 一直在屏的短称号：并（称号同一条 run 接上就算）", any(lead2 in a and line in a for _, _, a in segs_of([title, name, lead2, line])))
    a, b = run("……どうして。", 0, 2.6), run("どうしてユキちゃんは、", 2.6, 8)
    check("说完的一句和下一句只差一个字：不并", len(segs_of([a, b])) == 2)
    check("--cue-join off 等于原来的切分", texts(segs_of([l1, half, full], join=False))
          == texts(bt.merge_growing_segments(bt.drop_slivers(bt.segment_runs([l1, half, full]), 150_000))))
    # ▼ 一闪一灭（1 字、5 字的误读都算杂行）
    for tri, y, hidden in (("0", 1000, True), ("un2ra", 900, True), ("un2ra", 1000, False)):
        body = run("ありがとう、助かったよ。", 0, 8)
        tip = run(tri, 3, 4, y=y, x0=1000, n=2)
        got = segs_of([body, tip])
        where = "和正文同一行" if y == 900 else "自成一行"
        check(f"▼ 读成 `{tri}`（{where}）一闪：不切开；" + ("藏成杂行" if hidden else "≥ 3 字、自成一行的不藏（可能是晚打出来的真正文）"),
              len(got) == 1 and (id(tip) in bt.seg_hide(*got[0], TOL)[0]) == hidden, texts(got))
    # 名牌抖动、正文是同一条 run
    body = run("それじゃあ、また明日ね。", 0, 8)
    n1, n2 = run("ビリー", 0, 4, y=840, name=True), run("• ビU— •", 4, 8, y=840, name=True)
    check("名牌抖动、正文同一条 run：并", len(segs_of([body, n1, n2])) == 1)
    # 打字机首帧的 1–2 字碎片
    lead = run("は", 0, 0.5, n=1)
    rest = run("はじめまして、よろしくね。", 0.5, 6)
    check("首帧碎片是后一行的前缀、正好在它出现时消失：并", len(segs_of([lead, rest])) == 1)
    # 不并
    a, b = run("今日はいい天気ですね。", 0, 5), run("明日は雨が降るそうです。", 5, 10)
    check("两句不同：不并", len(segs_of([a, b])) == 2)
    a, b = run("今日はいい天気ですね。", 0, 5), run("今日はいい天気ですね。", 6, 10)
    check("同一句、中间空了超过容差：不并", len(segs_of([a, b])) == 2)
    a, b = run("ありがとう！", 0, 5), run("ありがとう！", 5, 10)
    na, nb = run("リン", 0, 5, y=840, name=True), run("ニコ", 5, 10, y=840, name=True)
    check("换了说话人的同一句（正文换了 run、名牌不同）：不并", len(segs_of([a, b, na, nb])) == 2)
    hud = run("矢を放つ", 0, 60, y=200, n=120)
    dlg = run("ここから先は気をつけて進もう。", 10, 15)
    check("常驻 HUD 行一直挂着、对话出现：不并进 HUD 那条（新行要和被接上的行一起消失）",
          not any(len(a) == 2 and a[0].t_start == 0 for _, _, a in segs_of([hud, dlg]) if a[0] is hud and dlg in a and _ == 0), texts(segs_of([hud, dlg])))
    old = run("REJECPREJEC", 0, 10, n=21)
    sup = run("SUPPORTER", 9.5, 20, y=960, n=21)
    new = run("REJECT", 10, 20, n=20)
    check("长命行只有尾巴落在段里、正好在切点消失：不当杂行，换成别的字不并（wuwa 广告横幅）",
          not any({old.text, new.text} <= {r.text for r in a} for _, _, a in segs_of([old, sup, new])), texts(segs_of([old, sup, new])))
    big = run("でもご心配なく、ずばり地脈共鳴！この", 0, 20, x0=100)
    frag = bt.Run([100 + 40 * 8, 900, 100 + 40 * 18, 940], "ずばり地脈共鳴！この", int(1 * S), int(2.5 * S), 3, 0.9)
    frag.nameplate = False
    got = segs_of([big, frag])
    check("det 多切出来的碎片（框在另一行里、文本是它的一段）消失：不切开，藏成半截",
          len(got) == 1 and id(frag) in bt.seg_hide(*got[0], TOL)[1], texts(got))
    # 不变量：藏掉的半截行，同一行里留着一条看得见的顶替它的 run（peer 代码审 B1）
    other = run("次の行のテキスト", 0, 7, y=960, n=14)
    a1, a2 = run("同じ言葉です。", 0, 5, n=10), run("同じ言葉です。", 5, 7, n=4)    # 同文本断开，前一条更长
    hide, part = bt.seg_hide(0, 7 * S, [a1, a2, other], TOL)
    check("同文本断开、前一条更长：只藏一条，那一行还看得见", len({id(a1), id(a2)} & hide) == 1, (hide, part))
    big2, small2 = run("おいおいー", 0, 1, n=2), run("おいおい", 1, 6, n=10)        # 杂行 + 它的碎片
    hide, part = bt.seg_hide(0, 6 * S, [big2, small2, other], TOL)
    check("杂行加上它的碎片：那一行留一条看得见的", id(small2) not in hide or id(big2) not in hide, (hide, part))
    late = run("無事で何よりよ、ビリー", 5, 5.5, y=1100, n=1)
    check("自成一行、晚打出来、只读到 1 次的正文（≥ 3 字）不藏", id(late) not in bt.seg_hide(0, 7 * S, [other, late], TOL)[0])
    tip1 = run("0", 5, 5.5, y=1100, n=1)
    check("自成一行的 1–2 字杂字（▼ 读成的 `0`）照藏", id(tip1) in bt.seg_hide(0, 7 * S, [other, tip1], TOL)[0])
    j1, j2 = run("飼育·展示することを決めた", 0, 4, n=8), run("飼育・展示することを決めた", 4, 5, n=2)   # 抖动续接断得晚：前一条更长
    hide, part = bt.seg_hide(0, 5 * S, [j1, j2, other], TOL)
    check("OCR 抖动的续接断得晚（前一条更长）：只留一条可见，另一条是半截行", len({id(j1), id(j2)} & part) == 1, (hide, part))
    o1, o2 = run("同時に読めた行です", 0, 6, n=12), run("同時に読めた行です", 1, 5, n=8)     # 同文本、时间重叠的两次读数
    check("同文本、时间重叠的两次读数：只留一条", len({id(o1), id(o2)} & bt.seg_hide(0, 6 * S, [o1, o2, other], TOL)[0]) == 1)
    c1, c2 = (run("キャッシュの行です", 0, 6, n=12), other), (run("キャッシュの行です", 0, 6, n=12), other)
    bt._HIDE_CACHE[(0, 6 * S, TOL, tuple(id(r) for r in c2))] = (c1, ({-1}, {-1}))     # 同样的键、另一批对象（id 复用的情形）
    check("seg_hide 的缓存：键对上但不是同一批 run 时不用缓存的结果", bt.seg_hide(0, 6 * S, list(c2), TOL) != ({-1}, {-1}))
    host = run("飼育·展示することを決めた", 0, 3, x0=100, n=6)
    jit = run("飼育・展示することを決めた", 3, 7, x0=100, n=8)
    fr = bt.Run([100 + 40 * 4, 900, 100 + 40 * 10, 940], "することを", int(1 * S), int(2 * S), 3, 0.9)
    fr.nameplate = False
    hide, part = bt.seg_hide(0, 7 * S, [host, jit, fr, other], TOL)
    check("碎片的宿主被藏、代表是抖动版：碎片也藏，不露半行", id(fr) in hide and id(jit) not in hide, (hide, part))
    lv1, lv2 = run("Lv.90", 0, 7, x0=100, n=14), run("Lv.90", 2, 5, x0=1200, n=6)
    check("同一高度、横向离得远的两处同样的字：都留着", not bt.seg_hide(0, 7 * S, [lv1, lv2, other], TOL)[0])
    # 藏：同一行同文本的重复读数只留活得最久的；一行都不剩时不藏
    w1, w2, w3 = run("取録", 0, 9, y=100, n=18), run("取録", 15, 20, y=100, n=10), run("KU100", 0, 30, y=60, n=60)
    hide, partial = bt.seg_hide(0, 30 * S, [w1, w2, w3], TOL)
    check("同一行文本相同的几条只留一条（消失得晚的那条）", partial == {id(w1)} and id(w2) not in hide, (hide, partial))
    lone = run("x", 0, 1, n=1)
    check("一段只有短命行时不藏", bt.seg_hide(0, 10 * S, [lone], TOL) == (set(), set()))
    h1, h2 = run("0", 0, 0.5, n=1), run("0", 0.5, 1, n=1)                         # 代表也是 1–2 字的杂字：只撤杂行，半截行照藏
    check("代表全被判成杂行时：杂行不藏，半截行照藏", bt.seg_hide(0, 10 * S, [h1, h2], TOL) == ({id(h1)}, {id(h1)}),
          bt.seg_hide(0, 10 * S, [h1, h2], TOL))
    from flowocr.artifacts import tracksio as _T
    rdoc = {"events": [{"id": 0, "t_start": 0, "t_end": 1 * S}, {"id": 1, "t_start": 1 * S, "t_end": 5 * S}],
            "tracks": [{"id": "r00", "cues": [{"id": "c0", "t_start": 0, "t_end": 5 * S, "events": [0, 1], "hide": [0], "partial": [0]}]}]}
    before = _T.event_times(rdoc)
    rdoc["events"][0]["t_start"] = 300_000                 # 回抠把半截行的首字挪到 0.3 s
    _T.refresh_cue_bounds(rdoc, before)
    check("合并后回抠：cue 起点跟着半截行回抠后的起点走（半截行仍是成员）", rdoc["tracks"][0]["cues"][0]["t_start"] == 300_000)
    # 投影：cue 上的 hide / partial
    doc = {"events": [{"id": i, "text": t, "flags": []} for i, t in enumerate(["半截", "全句", "▼"])]}
    tr = {"ui_lines": []}
    cue = {"events": [0, 1, 2], "hide": [0, 2], "partial": [0]}
    check("cue_lines：剔 UI 的投影两类都不给，全部文字的视图只不给半截行",
          [e["text"] for e in tracksio.cue_lines(doc, tr, cue)] == ["全句"]
          and [e["text"] for e in tracksio.cue_lines(doc, tr, cue, drop_filtered=False)] == ["全句", "▼"])


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


def t_band_windows() -> None:
    print("build_tracks.band_windows（--main-band-unit window）")
    import statistics
    from flowocr.analyze import build_tracks as bt
    W, H, M = 1920, 1080, 60_000_000

    def run(cy, h, t0):
        return bt.Run([600, cy - h / 2, 1300, cy + h / 2], "あいうえお", int(t0), int(t0 + 2e6), 4, 0.9)

    # 区域 1 是混合区域：窗 0 里是字幕带上的两行（cy 885、字高 42），窗 1、2 里是上方的大字（cy 460、字高 90），
    # 整块的中位几何离主轨远（整块判不并），按窗拆开窗 0 那块就在带上
    runs = ([run(885, 42, k * M + 1e6) for k in range(3)]          # 0–2：主轨
            + [run(885, 42, 5e6), run(885, 42, 9e6)]               # 3–4：区域 1 的窗 0
            + [run(460, 90, M + 1e6), run(460, 90, M + 5e6), run(460, 90, 2 * M + 1e6), run(460, 90, 2 * M + 5e6)])
    ids = {0: [0, 1, 2], 1: [3, 4, 5, 6, 7, 8]}
    feats = {ri: {"cy": statistics.median(runs[i].cy for i in v) / H,
                  "h": statistics.median(runs[i].h for i in v) / H} for ri, v in ids.items()}
    rep = [(0, "subtitle", feats[0]), (1, "dialogue-or-caption", feats[1])]
    geom = {ri: bt.region_geom(runs, v) for ri, v in ids.items()}
    check("整块判：混合区域不并", bt.band_regions(rep, 0, 3.0, need_x=True, geom=geom, W=W) == [])
    got = bt.band_windows(runs, ids, rep, 0, {0}, M, 3.0, 0.7, 1.4, False, geom[0], W, H)
    check("**按窗判：只并带上那个窗的 run**", got == {1: [3, 4]}, str(got))
    # 块过了带、块里单条不在带上的不进来：窗 0 再加一条下方的小字（cy 1010、字高 25），块的中位几何仍在带上
    runs2 = runs + [run(1010, 25, 7e6)]
    ids2 = {0: [0, 1, 2], 1: [3, 4, 9, 5, 6, 7, 8]}
    got2 = bt.band_windows(runs2, ids2, rep, 0, {0}, M, 3.0, 0.7, 1.4, False, geom[0], W, H)
    check("**块过了带，成员逐条再过一次**：块里单条不在带上的不并", got2 == {1: [3, 4]}, str(got2))
    check("--main-band-unit 只有 region / window（逐条 run 的对照臂量下来更差，已删）",
          next(a for a in bt.make_parser()._actions if a.dest == "main_band_unit").choices == ("region", "window"))
    check("已经整块并进来的区域不再拆", bt.band_windows(runs, ids, rep, 0, {0, 1}, M, 3.0, 0.7, 1.4, False, geom[0], W, H) == {})
    check("label 闸门照样管按窗的块",
          bt.band_windows(runs, ids, [(0, "subtitle", feats[0]), (1, "misc", feats[1])], 0, {0}, M,
                          3.0, 0.7, 1.4, True, geom[0], W, H) == {})
    # 下游"在不在主轨"按事件判：区域整块在主轨里，或事件带 band_merged（按窗补进来的）
    from flowocr.artifacts import tracksio
    check("**按窗补进来的事件算主轨**（区域不在 regions 里，靠 band_merged）",
          tracksio.in_main({"region": 7, "flags": ["band_merged"]}, {0, 3}))
    check("整块并进来的区域里的事件算主轨", tracksio.in_main({"region": 3, "flags": []}, {0, 3}))
    check("同一块区域里没补进来的事件不算", not tracksio.in_main({"region": 7, "flags": []}, {0, 3}))
    check("--main-band-unit 默认是 region（按窗是实验开关，翻默认归 owner）",
          bt.make_parser().get_default("main_band_unit") == "region")


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
    # 名牌保护绑时间窗（审计七 §3.8）：左上角常驻按钮 10 分钟里每分钟一条，第 0 条被误标成名牌——只放行它所在那个窗
    auto = [bt.Run(box=[80, 20, 220, 60], text="オート再生", t_start=k * 60_000_000, t_end=k * 60_000_000 + 50_000_000,
                   n_obs=100, conf=.9) for k in range(10)]
    auto[0].nameplate = True
    dargs = bt.make_parser().parse_args(["x.jsonl", "--outdir", "o"])
    check("框位门的名牌保护只盖名牌标所在的那个窗（同一位置别的时段照判 UI）",
          bt.ui_footprints(auto, 500_000, dargs) == set(range(2, 10)), sorted(bt.ui_footprints(auto, 500_000, dargs)))

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
    A = [{"t_us": 0, "box": [0, 0, 9, 9], "text": "同", "conf": 0.9},
         {"t_us": 1, "box": [0, 0, 9, 9], "text": "变前", "conf": 0.9},
         {"t_us": 2, "box": [5, 5, 9, 9], "text": "只在 A", "conf": 0.9}]
    B = [{"t_us": 0, "box": [0, 0, 9, 9], "text": "同", "conf": 0.9},
         {"t_us": 1, "box": [0, 0, 9, 9], "text": "变后", "conf": 0.9},
         {"t_us": 3, "box": [7, 7, 9, 9], "text": "只在 B", "conf": 0.9}]
    c = _ab.compare_obs(A, B)
    check("**框序列不同也照样对账**（原来直接返回 None，汇总再把它当 0）",
          c["text_diff"] == 1 and c["cmp_rows"] == 2, c)
    check("只在一侧有的行单独数，不混进比较",
          c["only_a"] == 1 and c["only_b"] == 1, (c["only_a"], c["only_b"]))
    c2 = _ab.compare_obs(A, [dict(x) for x in A])
    check("逐行相同时 identical / same_boxes 都成立、文本改 0",
          c2["identical"] and c2["same_boxes"] and c2["text_diff"] == 0)
    c3 = _ab.compare_obs(A, [{"t_us": 9, "box": [1, 1, 2, 2], "text": "x", "conf": 0.9}])
    check("**一行都配不上时 cmp_rows = 0**（分母用它，报表才不会印出假的 0%）",
          c3["cmp_rows"] == 0 and c3["text_diff"] == 0, c3)
    # ⚠ **B 臂新增的行也要数**（2026-09-20 复审第 1 条）：原来汇总只报
    # `rows_a - cmp_rows`（= A 独有），B 新出现的行一条都不出现在报表里，
    # 而它们照样会改下游。实测六段：A 独有 419、**B 独有 412**。
    c4 = _ab.compare_obs(A[:1], A[:1] + [{"t_us": 7, "box": [1, 1, 2, 2], "text": "B 新增",
                                          "conf": 0.9}])
    check("**只有 B 新增行时也要报出来**（only_b 不许漏）",
          c4["only_b"] == 1 and c4["only_a"] == 0 and c4["cmp_rows"] == 1, c4)
    c5 = _ab.compare_obs(A[:1] + [{"t_us": 7, "box": [1, 1, 2, 2], "text": "只在 A", "conf": 0.9}],
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
    _addr = fake_server({"ok": 1, "models": ["rec", "det"], "device": "gpu",
                         "providers": {"rec": "CUDAExecutionProvider", "det": "TensorrtExecutionProvider"}})
    check("服务给得出就接上去；记下连的是哪个服务（合并几个进程的服务端累计数时分组用）",
          cli._attach(_addr, {"rec": "x.onnx"}) is True and cli.server_id == _addr)
    check("接上之后记下服务给**这个 client 的模型**实际用的 provider（进 _meta.ort，追溯设备；det 走 TRT 不串到 rec 上）",
          cli.provider == "CUDAExecutionProvider")
    check("默认拓扑下 client 带着两个模型时，追溯设备按模型取（det 退回 CUDA、rec 走 TRT 时各是各的）",
          cli.provider_for("det") == "TensorrtExecutionProvider" and cli.provider_for("rec") == "CUDAExecutionProvider")
    check("几个模型 provider 不同时用 + 连起来",
          oc.provider_of({"providers": {"det": "TensorrtExecutionProvider", "rec": "CUDAExecutionProvider"}}, ["det", "rec"])
          == "TensorrtExecutionProvider+CUDAExecutionProvider")
    import io as _io
    check("ready 行之前的提示行（缺 TRT 引擎时现建）跳过，读到的是 ready 那行",
          oc.read_ready(_io.StringIO('{"note": "[trt] 建 det"}\nnative noise\n{"ready": 1, "providers": {}}\n')).get("ready") == 1
          and oc.read_ready(_io.StringIO("Traceback boom\n")) == {"line": "Traceback boom\n"})
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
    label = R([800, 812, 1000, 852], "聖火競技場")                     # 地图地名：5 字的短标签，注音整词居中
    check("振り仮名：居中只在正文是正文长度（≥ BODY_CLEN 内容字）时当名牌不标；短标签上整词居中的注音照标（gi1 留出检验）",
          bt.ruby_runs([label, R([790, 786, 1010, 814], "せいかきょうぎじょう")]) == {1}
          and 2 not in bt.ruby_runs(runs))
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
    # 聚类的尺的 S5（同刻相邻行）：只看几何和时间——同时在场的上下行进候选；不同时在场的、同一行左右两段、隔得远的都不进
    from types import SimpleNamespace as _NS
    from flowocr.analyze import slot_pairs as SP
    r5 = [bt.Run(box=[600, 900, 1300, 936], text="一行目です", t_start=0, t_end=4_000_000, n_obs=5, conf=.9),
          bt.Run(box=[650, 950, 1250, 986], text="二行目です", t_start=500_000, t_end=4_000_000, n_obs=5, conf=.9),
          bt.Run(box=[650, 1000, 1250, 1036], text="あとの行です", t_start=5_000_000, t_end=8_000_000, n_obs=5, conf=.9),
          bt.Run(box=[1310, 900, 1600, 936], text="同じ行の右側", t_start=0, t_end=4_000_000, n_obs=5, conf=.9),
          bt.Run(box=[600, 300, 1300, 336], text="遠くの行です", t_start=0, t_end=4_000_000, n_obs=5, conf=.9)]
    adj = SP.adjacent_pairs(_NS(runs=r5))
    check("尺·S5 候选：同时在场的上下相邻行才进（不同时在场、同一行、隔得远的不进）",
          [(i, j) for i, j, _ in adj] == [(0, 1)] and 500_000 <= adj[0][2] <= 4_000_000, adj)

    # 回抠结算救回被回补作废的结尾证据（--refine-salvage-end）
    import json as _json
    from flowocr.analyze import refine_boundaries as RBS
    with tempfile.TemporaryDirectory() as td:
        op = Path(td) / "o.jsonl"
        off = {"lo": 0, "hi": 1_000_000, "last_us": 500_000, "stale": True, "text": "あ"}
        rows = [{"_meta": {"aux_clock": [60, 1, 2, 1], "src_fps": 60.0, "frame_key": "t_us"}},
                {"t_us": 500_000, "box": [0, 0, 100, 30], "text": "あ", "conf": .9, "edge": {"off": off}},
                {"t_us": 1_000_000, "box": [0, 100, 100, 130], "text": "い", "conf": .9,
                 "edge": {"off": {**off, "last_us": 1_000_000, "short": True, "text": "い"}}},
                # 换句那条路：回补把挂证据的这一行也改成了新句（行上 `う`、证据量的是旧句 `え`）——模板帧上已经是新句
                {"t_us": 500_000, "box": [0, 200, 100, 230], "text": "う", "conf": .9,
                 "edge": {"off": {**off, "text": "え"}}}]
        op.write_text("\n".join(_json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        jobs = lambda: [{"run": {"t_start": 0, "t_end": 1_000_000, "box": [0, 0, 100, 30]}},   # noqa: E731
                        {"run": {"t_start": 0, "t_end": 1_500_000, "box": [0, 100, 100, 130]}},
                        {"run": {"t_start": 0, "t_end": 1_000_000, "box": [0, 200, 100, 230]}}]
        j0, j1 = jobs(), jobs()
        s0 = RBS.refine_from_obs(j0, op, 1e6 / 60, 500_000, salvage_stale_end=False)
        s1 = RBS.refine_from_obs(j1, op, 1e6 / 60, 500_000, salvage_stale_end=True)
        check("回抠·救回作废的结尾证据：关了不认领；开了认领窗口凑齐、挂它的行没被改写的那条（作废标去掉）；"
              "窗口不全的、挂它的行被回补改成别的文本的仍不认",
              s0["jobs_with_off"] == 0 and s1["jobs_with_off"] == 1 and s1["jobs_with_off_salvaged"] == 1
              and j1[0]["ev_off"]["stale"] is False and "ev_off" not in j1[1] and "ev_off" not in j1[2], (s0, s1))

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
          [(sg.start, sg.end, [e["id"] for e in sg.evs]) for sg in segs]
          == [(983_000, 1_400_000, [0, 1]), (1_483_000, 1_533_000, [2]), (2_267_000, 2_633_000, [3])],
          [(sg.start, sg.end) for sg in segs])
    # Codex 复审的反例：每一步和上一步重叠过半，五步之后完全离开首框
    dcues = [srtio.Cue(k * 0.5, k * 0.5 + 0.5, ("x",)) for k in range(5)]
    drift = [(k, [ev(k, [100, 100 + 10 * k, 600, 128 + 10 * k], k * 500_000, k * 500_000 + 500_000)]) for k in range(5)]
    dsegs = SM.position_segments(drift, dcues, 0, 2_500_000)
    check("叠加·慢慢漂的字：段里每条都要和画的那个框同处（只比上一条没有传递性），不许整段钉在首框",
          len(dsegs) > 1 and all(max(SM.box_inside(e["box"], r), SM.box_inside(r, e["box"])) >= SM.SAME_PLACE
                                 for evs, rows, *_ in dsegs for e in evs
                                 for r in [[min(x[0] for x in rows), min(x[1] for x in rows),
                                            max(x[2] for x in rows), max(x[3] for x in rows)]]),
          [(sg.start, sg.end, sg.rows) for sg in dsegs])
    # 画的框因行数变多换掉时，已在段里的也要重核：第三条两行、框下移，首条已不在新框里 -> 第三条另起
    swap = [(0, [ev(0, [100, 100, 600, 128], 0, 500_000)]), (1, [ev(1, [100, 110, 600, 138], 500_000, 1_000_000)]),
            (2, [ev(2, [100, 120, 600, 148], 1_000_000, 1_500_000), ev(3, [100, 150, 600, 178], 1_000_000, 1_500_000)])]
    check("叠加·画的框换成行数更多的那条时，段里已有的也要和新框同处，不同处就另起一段",
          [[e["id"] for e in sg.evs] for sg in SM.position_segments(swap, dcues, 0, 1_500_000)] == [[0, 1], [2, 3]])
    still = {"id": 9, "box": [586, 735, 692, 770], "t_start": 0, "t_end": 9_000_000}
    scroll = {**still, "boxes": [[0, [586, 963, 692, 999]], [500_000, [588, 739, 690, 768]], [1_000_000, [586, 735, 692, 770]],
                                 [1_500_000, [586, 677, 692, 713]], [2_000_000, [586, 663, 692, 713]], [5_000_000, [584, 735, 694, 780]]]}
    check("叠加·名牌层：不动的事件一段（框和起止就是事件的）；在动的沿轨迹按位置切段、同处的并成一段、首尾贴事件的起止（星铁对话记录里的 `ドロシー`）",
          SM.event_segments(still) == [([586, 735, 692, 770], 0, 9_000_000)]
          and [(a, b) for _, a, b in SM.event_segments(scroll)]
          == [(0, 500_000), (500_000, 1_500_000), (1_500_000, 5_000_000), (5_000_000, 9_000_000)],
          SM.event_segments(scroll))
    check("叠加·打字机长字（小框落进大框）不切，只有一段时起止就是记录的起止",
          [(sg.start, sg.end) for sg in SM.position_segments(grow + [(2, [])], pcues, 983_000, 1_533_000)]
          == [(983_000, 1_533_000)])
