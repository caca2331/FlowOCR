"""阶段 3（导出、叠加、扩展）的守卫。"""
from __future__ import annotations

import importlib  # noqa: F401
import inspect  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

from guards._common import *  # noqa: F401,F403


def t_extensions() -> None:
    """用户代码扩展（project-structure §6）：加载 / 核入口 / 匹配器补丁只有一份表 / 内置预设与 build_tracks 逐字节同款。"""
    print("扩展入口")
    import json

    from flowocr import extensions as X
    from flowocr.analyze.matchers import gametext
    from flowocr.output import render as R
    from flowocr.analyze import build_tracks as BT
    from flowocr.analyze import scriptmatch as SM_mod
    from flowocr.artifacts import srtio
    from flowocr.artifacts import tracksio

    check("内置预设 / 匹配器按名字加载，入口是可调用的",
          callable(X.load("srt_main", "preset").render) and callable(X.load("gametext", "matcher").match)
          and callable(X.load("gametext", "matcher").cluster_patch))
    check("内置名单列得出来", "srt_main" in X.builtin_names("preset") and "gametext" in X.builtin_names("matcher"))
    ex_p, ex_m = ROOT / "examples" / "preset_minimal.py", ROOT / "examples" / "matcher_minimal.py"
    check("examples 里的两个例子按文件路径加载得到", callable(X.load(str(ex_p), "preset").render)
          and callable(X.load(str(ex_m), "matcher").match) and callable(X.load(str(ex_m), "matcher").cluster_patch))
    check("点分模块名和内置名加载到同一个模块",
          X.load("flowocr.output.presets.srt_main", "preset") is X.load("srt_main", "preset"))
    check("没有的内置名报 LookupError（列出有的）", raises(lambda: X.load("nope", "preset"), LookupError))
    check("种类打错报 ValueError", raises(lambda: X.load("srt_main", "presets"), ValueError))
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "bad.py").write_text("x = 1\n", encoding="utf-8")
        check("缺入口的模块报 TypeError，不冒充成功", raises(lambda: X.load(str(d / "bad.py"), "preset"), TypeError))
        check("不存在的文件报 FileNotFoundError", raises(lambda: X.load(str(d / "no.py"), "preset"), FileNotFoundError))

        # 匹配器补丁：表只在 gametext 里，build_tracks --matcher 把它套成**默认值**（计划 §5 的生效顺序）
        check("cluster_patch 的表：星铁开时间门、原神 / 绝区零 / 认不出的不开",
              gametext.cluster_patch({"tag": "hsr-s1"}) == {"region_time_gate": 2}
              and gametext.cluster_patch({"game": "starrail"}) == {"region_time_gate": 2}
              and gametext.cluster_patch({"game": "genshin"}) == {} and gametext.cluster_patch({}) == {})
        base = ["o", "--outdir", "d", "--matcher", "gametext", "--tag"]
        a_hsr = BT.apply_matcher_patch(BT.make_parser(), base + ["hsr-s1"])
        a_gi = BT.apply_matcher_patch(BT.make_parser(), base + ["gi-s1"])
        a_user = BT.apply_matcher_patch(BT.make_parser(), base + ["hsr-s1", "--region-time-gate", "0"])
        a_none = BT.apply_matcher_patch(BT.make_parser(), ["o", "--outdir", "d", "--tag", "hsr-s1"])
        a_notag = BT.apply_matcher_patch(BT.make_parser(), ["x/hsr-s2.jsonl", "--outdir", "d", "--matcher", "gametext"])
        a_tag = BT.apply_matcher_patch(BT.make_parser(), ["x/hsr-s2.jsonl", "--outdir", "d", "--matcher", "gametext",
                                                          "--tag", "hsr-s2"])
        check("不写 --tag 和显式写同名 --tag 行为一致：补丁按生效标签（obs 文件名）认游戏（Codex 复审 P2）",
              a_notag.region_time_gate == a_tag.region_time_gate == 2
              and a_notag.matcher_patch == a_tag.matcher_patch == {"region_time_gate": 2}
              and BT.effective_tag(a_notag) == BT.effective_tag(a_tag) == "hsr-s2")
        check("生效顺序：默认 -> 匹配器补丁 -> 命令行显式覆盖（覆盖了什么记在 provenance）",
              (a_hsr.region_time_gate, a_gi.region_time_gate, a_user.region_time_gate, a_none.region_time_gate)
              == (2, 0, 0, 0)
              and a_hsr.matcher_patch == {"region_time_gate": 2} and a_hsr.matcher_overridden == {}
              and a_user.matcher_overridden == {"region_time_gate": 0} and a_none.matcher_patch == {})
        check("补丁给了解析器不认的参数名就当场报错（不静默失效）",
              raises(lambda: BT.apply_matcher_patch(BT.make_parser(), ["o", "--outdir", "d", "--matcher",
                                                                       str(ROOT / "tests" / "_badpatch.py")]),
                     SystemExit))
        check("补丁返回的是副本（调用方改了不污染下次）",
              gametext.cluster_patch({"game": "starrail"}) is not gametext.PATCHES["starrail"])

        # 最小的一份 tracks 产物：一条区域轨、两条事件（一条是这条轨判的常驻 UI）
        doc = {"meta": {}, "frame_us": 500_000, "size": [1920, 1080],
               "provenance": {"main_track": "r00", "main_srt": "t-region00-subtitle.srt"},
               "regions": [{"index": 0}],
               "events": [{"id": 0, "region": 0, "t_start": 0, "t_end": 1_000_000, "text": "台词", "flags": []},
                          {"id": 1, "region": 0, "t_start": 0, "t_end": 1_000_000, "text": "UI", "flags": []}],
               "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                           "srt": "t-region00-subtitle.srt", "ui_lines": ["UI"],
                           "cues": [{"id": "c0", "t_start": 0, "t_end": 1_000_000, "events": [0, 1]}]}]}
        tp = d / "t-tracks.json"
        doc = tracksio.dump(tp, doc)
        want = d / "want.srt"
        srtio.write_srt_blocks(want, [(0, 1_000_000, ["台词"])])
        got = X.load("srt_main", "preset").render(doc, d / "p1", {})
        check("srt_main：只写主轨、文件名同 provenance.main_srt、内容和 build_tracks 的投影同款（UI 行剔掉）",
              [p.name for p in got] == ["t-region00-subtitle.srt"] and got[0].read_bytes() == want.read_bytes())
        got2 = X.load("srt_main", "preset").render(doc, d / "p2", {"name": "m.srt"})
        check("srt_main 的 name 选项改文件名", [p.name for p in got2] == ["m.srt"] and got2[0].read_bytes() == want.read_bytes())
        check("没有主轨的产物 srt_main 明确报错，不静默写空文件",
              raises(lambda: X.load("srt_main", "preset").render({**doc, "provenance": {}}, d / "p3", {}), ValueError))
        rc = R.main([str(tp), "--preset", str(ex_p), "--outdir", str(d / "p4"), "--opt", "prefix=x-"])
        check("render 入口：跑文件路径给的预设、--opt 原样交给它",
              rc == 0 and (d / "p4" / "x-r00-subtitle.srt").read_bytes() == want.read_bytes())
        (d / "table.json").write_text(json.dumps({"台词": "原文"}, ensure_ascii=False), encoding="utf-8")
        m = X.load(str(ex_m), "matcher").match(doc, {}, {"table": str(d / "table.json")})
        check("示例匹配器：主轨 cue 查表换原文", m["stats"] == {"cues": 1, "hit": 1} and m["cues"][0]["ref"] == "原文")

        # ---- 用户匹配器**一路贯通到输出预设**（Codex 审计 P2：原来只证明了"模块加载得到"）----
        from flowocr.analyze import match as M
        mp = d / "m.json"
        rc = M.main([str(tp), "--matcher", str(ex_m), "--out", str(mp), "--opt", f"table={d / 'table.json'}"])
        up = d / "mypreset.py"
        up.write_text('''ACCEPTS = ("example-matched/1",)


def render(document, output_dir, options=None):
    from pathlib import Path
    p = Path(output_dir)
    p.mkdir(parents=True, exist_ok=True)
    f = p / "user.txt"
    f.write_text(chr(10).join(c["ref"] or c["raw"] for c in document["cues"]), encoding="utf-8")
    return [f]
''', encoding="utf-8")
        rc2 = R.main([str(mp), "--preset", str(up), "--outdir", str(d / "p5")])
        check("阶段 2 匹配入口 -> 阶段 3 预设：用户匹配器的产物喂得进用户预设（内置和它走同一条路）",
              rc == 0 and rc2 == 0 and json.loads(mp.read_text(encoding="utf-8"))["schema"] == "example-matched/1"
              and (d / "p5" / "user.txt").read_text(encoding="utf-8") == "原文")
        check("预设声明了 ACCEPTS 就核 schema：喂错产物当场报错，不在预设内部炸成 KeyError",
              raises(lambda: R.main([str(tp), "--preset", str(up), "--outdir", str(d / "p6")]), SystemExit)
              and raises(lambda: R.main([str(mp), "--preset", "srt_main", "--outdir", str(d / "p6")]), SystemExit))
        from flowocr.artifacts import matchedio as MI
        check("matched 的格式只有一份实现（scriptmatch re-export artifacts 那份）",
              SM_mod.SCHEMA is MI.SCHEMA and SM_mod.load_matched is MI.load and SM_mod.body is MI.body)
        check("matched_srt 吃 matched、srt_main 吃 tracks、三个叠加预设两种都吃（ACCEPTS 写清楚）",
              X.load("matched_srt", "preset").ACCEPTS == (MI.SCHEMA,)
              and all(X.load(p, "preset").ACCEPTS == (tracksio.SCHEMA, MI.SCHEMA) for p in ("dev", "default_all", "default"))
              and X.load("srt_main", "preset").ACCEPTS == (tracksio.SCHEMA,))
        check("matched 校验版本：schema 不对就抛，不当成能读",
              raises(lambda: MI.validate({"schema": "flowocr-matched/1", "provenance": {}, "stats": {}, "cues": []}),
                     MI.MatchedSchemaError)
              and raises(lambda: MI.validate({"schema": MI.SCHEMA, "cues": []}), MI.MatchedSchemaError))
        check("叠加 ASS 的预设：产物里没有 overlay.items 就明确报错（--no-overlay 产的）",
              raises(lambda: MI.validate({"schema": MI.SCHEMA, "provenance": {}, "stats": {}, "cues": []},
                                         need_overlay=True), MI.MatchedSchemaError))


def t_rich_json() -> None:
    """富信息 JSON 与导出器（docs/architecture/artifacts.md）。

    要钉住的是**"一级产物只有一份、导出器只投影"**这件事本身：
    ①产物自己说得清（校验会响）；②SRT 走导出器**逐字节相同**；
    ③过滤是打标不是删——用途 1 拿得到被剔的事件。
    """
    print("tracksio / export")
    import json
    from unittest.mock import patch

    from flowocr.analyze import build_tracks as bt
    from flowocr.output import export
    from flowocr.artifacts import tracksio

    good = {"tag": "t", "meta": {}, "frame_us": 500_000, "size": [100, 100],
            "provenance": {"main_track": "r00"},
            "regions": [{"index": 0, "label": "subtitle"}],
            "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "t.srt",
                        "ui_lines": ["Auto"],
                        "cues": [{"id": "r00c0", "t_start": 0, "t_end": 1, "events": [0, 1]}]}],
            "events": [{"id": 0, "region": 0, "t_start": 0, "t_full": None, "t_end": 1,
                        "box": [0, 0, 10, 10], "text": "台詞", "conf": .9, "n_obs": 1,
                        "n_independent": 1, "flags": []},
                       {"id": 1, "region": 0, "t_start": 0, "t_full": None, "t_end": 1,
                        "box": [0, 0, 10, 10], "text": "Auto", "conf": .9, "n_obs": 1,
                        "n_independent": 1, "flags": ["ui_filtered"]}]}
    tracksio.validate({"schema": tracksio.SCHEMA, **good})
    check("正常产物过校验", True)

    def broken(mut):
        d = json.loads(json.dumps({"schema": tracksio.SCHEMA, **good}))
        mut(d)
        return raises(lambda: tracksio.validate(d), tracksio.SchemaError)

    check("**旧 schema 直接抛**，不做兼容", broken(lambda d: d.update(schema="old")))
    check("事件 id 必须就是下标", broken(lambda d: d["events"][1].update(id=7)))
    check("cue 指向不存在的事件要响", broken(lambda d: d["tracks"][0]["cues"][0]
                                            ["events"].append(9)))
    check("空 cue 要响", broken(lambda d: d["tracks"][0]["cues"][0].update(events=[])))
    check("事件的区域必须存在", broken(lambda d: d["events"][0].update(region=3)))
    check("t_full 落在 [t_start, t_end] 外要响",
          broken(lambda d: d["events"][0].update(t_full=99)))
    check("轨 id 不许重复",
          broken(lambda d: d["tracks"].append(dict(d["tracks"][0]))))

    doc = {"schema": tracksio.SCHEMA, **good}
    tr = tracksio.track(doc, "r00")
    check("SRT 投影按这条轨的 ui_lines 剔",
          [e["text"] for e in tracksio.cue_lines(doc, tr, tr["cues"][0])] == ["台詞"])
    check("**审计投影一条都不剔**（要看删了什么）",
          len(tracksio.cue_lines(doc, tr, tr["cues"][0], drop_filtered=False)) == 2)
    check("主轨由 provenance 指定，不 glob 猜", tracksio.main_track(doc)["id"] == "r00")

    # ---- `--srt-mode body`：只有正文行切 cue（hsr 5591 s 那一段的真实事件布局，owner 2026-09-12 看预览指出）----
    S = 1_000_000

    def R(box, text, t0, t1):
        return bt.Run(box, text, int(t0 * S), int(t1 * S), 2, .9, [text])
    hsr = [R([916, 796, 1002, 844], "火花", 5583.5, 5591.0),
           R([528, 860, 1370, 887], "あいつなら、こないだゴッサム市で暴れて、CEOが牢屋にぶち込んだよ。", 5583.5, 5591.0),
           R([900, 798, 1018, 842], "不死途", 5591.0, 5596.0),
           R([776, 860, 804, 889], "知", 5591.0, 5591.5),
           R([774, 860, 1128, 887], "知ってる、捕まえたのは僕だ。", 5591.5, 5596.0),
           R([1072, 921, 1096, 951], "回", 5591.5, 5592.0), R([1074, 927, 1092, 947], "A", 5592.5, 5593.0),
           R([1075, 925, 1108, 957], "回", 5594.0, 5594.5), R([1074, 937, 1092, 957], "A", 5595.0, 5595.5),
           R([900, 798, 1020, 842], "不死途", 5597.0, 5613.0),
           R([324, 856, 1548, 889], "そう、僕が1番気になってるのが彼だ。物語のパターンは何通りもあった", 5597.0, 5608.0),
           R([680, 856, 1232, 891], "「未着」はなんらかの方法で「貪慾」に勝った。", 5608.0, 5613.0)]
    bsegs = bt.cue_segments(hsr, mode="body")
    check("**body：名牌 / 1 字首帧 / 闪烁的 ▼ 提示都不切 cue**——四句台词四条 cue（segment 档切成九条）",
          [(s / S, e / S) for s, e, _ in bsegs] == [(5583.5, 5591.0), (5591.0, 5596.0), (5597.0, 5608.0), (5608.0, 5613.0)]
          and len(bt.cue_segments(hsr, mode="segment")) > 4,
          ([(s / S, e / S) for s, e, _ in bsegs], len(bt.cue_segments(hsr, mode="segment"))))
    check("body：行按 (cy, cx) 排、名牌在正文上面；横跨两句的名牌两条 cue 都有；"
          "**只盖住这条 cue 一小段的 1–2 字碎片不挂进来**（闪烁的 ▼ 和打字机首帧 `知`）",
          [r.text for r in bsegs[1][2]] == ["不死途", "知ってる、捕まえたのは僕だ。"]
          and bsegs[2][2][0].text == bsegs[3][2][0].text == "不死途",
          [[r.text for r in seg[2]] for seg in bsegs])
    two_line = [R([0, 800, 1000, 830], "第一行のせりふです", 0, 5), R([0, 840, 1000, 870], "第二行が後から出る", 1, 5)]
    check("body：两行正文打字机（上一行先出）是一条 cue，起点是首字出现",
          [(s / S, e / S) for s, e, _ in bt.cue_segments(two_line, mode="body")] == [(0.0, 5.0)])
    check("body：没有名牌的纯标点台词自己成条（不会因为不是锚就消失）",
          [(s / S, e / S) for s, e, _ in bt.cue_segments([R([400, 850, 500, 880], "……", 0, 2)], mode="body")] == [(0.0, 2.0)])
    uid = [R([0, 1040, 140, 1070], "UID:800076807", 0, 20)] + [R([300, 850, 1500, 880], f"台詞その{k}です", 2 + 5 * k, 6 + 5 * k) for k in range(3)]
    check("body：角落里 x 不重叠的水印不算『在正文上方』，正文照样各自成条",
          len([seg for seg in bt.cue_segments(uid, mode="body") if any(r.text.startswith("台詞") for r in seg[2])]) == 3)
    short_reply = [R([900, 800, 1000, 840], "カチーナ", 0, 10),
                   R([850, 860, 950, 890], "はあ…", 0, 3.5),
                   R([500, 860, 1400, 890], "また次回、どうにかして勝つ方法を考えるしかないかな。", 3, 10),
                   R([1200, 860, 1260, 890], "15m", 5, 5.5), R([1200, 860, 1260, 890], "15m", 7, 7.5)]
    sr = bt.cue_segments(short_reply, mode="body")
    check("body：1–2 字的短台词只和下一句首尾挨着（末帧补的半个间隔）时自成一条，不被下一句吞掉；"
          "闪烁的 3 字 HUD（15m）盖不住半段，不挂进 cue",
          [(s / S, e / S) for s, e, _ in sr] == [(0.0, 3.0), (3.0, 10.0)]
          and [r.text for r in sr[0][2]] == ["カチーナ", "はあ…"]
          and [r.text for r in sr[1][2]] == ["カチーナ", "また次回、どうにかして勝つ方法を考えるしかないかな。"],
          [((s / S, e / S), [r.text for r in a]) for s, e, a in sr])
    hud = [R([900, 800, 1000, 840], "パイモン", 10, 14),
           R([500, 850, 1400, 880], "見ろ。壁にいろいろ落書きされてるぞ。", 10, 14),
           R([1300, 890, 1420, 920], "Lv.90", 0, 60)]
    hs = [a for a in bt.cue_segments(hud, mode="body") if any(r.text.startswith("見ろ") for r in a[2])]
    check("body：正文底下压着常驻 HUD（`Lv.90` 挂几十秒）时，正文仍是锚、自成一条 cue",
          [(s / S, e / S) for s, e, _ in hs] == [(10.0, 14.0)], [(s / S, e / S, [r.text for r in a]) for s, e, a in hs])
    punct = [R([900, 800, 1000, 840], "ムアラニ", 0, 20), R([850, 850, 950, 880], "…!!!", 8, 10)]
    punct[1].n_obs = 4
    check("body：名牌底下的纯标点短台词（读到过 ≥3 次）盖不住半段也留在 cue 里",
          any(r.text == "…!!!" for _, _, a in bt.cue_segments(punct, mode="body") for r in a))
    body_box, full_box = [776, 860, 804, 889], [774, 860, 1128, 887]
    check("打字机 1 字首帧：默认 2 字门接不上；`--typewriter-min-chars 1` 接上，但 1 个字必须逐字相同",
          not bt.typewriter_growth(body_box, "知", full_box, "知ってる、捕まえたのは僕だ。")
          and bt.typewriter_growth(body_box, "知", full_box, "知ってる、捕まえたのは僕だ。", min_chars=1)
          and not bt.typewriter_growth(body_box, "如", full_box, "知ってる、捕まえたのは僕だ。", min_chars=1))

    # ---- 回抠刷新 cue 边界：**不许把切分丢掉**（segment 档最容易发作）----
    # 常驻 `Auto` 挂 40 秒，20 条台词各 2 秒：segment 在每个首尾边界切一刀，
    # 于是 `Auto` 同时是 20 条 cue 的成员。按"成员生命周期 min/max"重算的话，
    # 20 条字幕会**全部变成 0–40 秒**——`--srt-only` 也会触发。
    runs = [bt.Run([0, 0, 100, 20], "Auto", 0, 40_000_000, 80, .9, ["Auto"])]
    runs += [bt.Run([0, 40, 400, 60], f"台词{i}", i * 2_000_000, (i + 1) * 2_000_000,
                    4, .9, [f"台词{i}"]) for i in range(20)]
    segs = bt.cue_segments(runs, mode="segment")
    eid = {}
    evs = []
    for i, r in enumerate(runs):
        eid[id(r)] = i
        evs.append(bt.event_dict(r, i, 0))
    seg_doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {}, "frame_us": 500_000,
               "size": [1000, 1000], "provenance": {"main_track": "r00"},
               "regions": [{"index": 0, "label": "subtitle"}],
               "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                           "srt": "t-region00-subtitle.srt", "ui_lines": [],
                           "cues": bt.cues_from_segments("r00", segs, eid)}],
               "events": evs}
    before = [(c["t_start"], c["t_end"]) for c in seg_doc["tracks"][0]["cues"]]
    tracksio.refresh_cue_bounds(seg_doc)
    after = [(c["t_start"], c["t_end"]) for c in seg_doc["tracks"][0]["cues"]]
    check("**时间没变时，刷新不许动 cue 的切分边界**（常驻行不该把每条撑成整段）",
          after == before, f"{before[:2]} -> {after[:2]}")

    # 真回抠了：某条台词的边界挪动半个采样间隔，cue 的边界要跟着挪。
    # **配对靠回抠前的快照**——`before` 记的是"当初谁和这条 cue 的边界重合"。
    snap = tracksio.event_times(seg_doc)
    seg_doc["events"][1]["t_start"] -= 250_000
    seg_doc["events"][1]["t_end"] += 250_000
    tracksio.refresh_cue_bounds(seg_doc, snap)
    moved = [(c["t_start"], c["t_end"]) for c in seg_doc["tracks"][0]["cues"]]
    # 起点只有台词0一个来源 → 跟着挪；终点 2.0 s 上还有"台词1 开始"这个来源没动，
    # **共享切点取最早的那个证据**，所以留在 2.0 s（tracksio 里写了这个取舍）。
    check("回抠挪了成员边界，cue 跟着挪（且只挪那一条）",
          moved[0] == (before[0][0] - 250_000, before[0][1])
          and moved[2:] == before[2:], f"{moved[:3]}")
    check("**cue 首尾不许倒挂**（每个切点各自配对，必须再夹一道单调）",
          all(c["t_start"] <= c["t_end"] for c in seg_doc["tracks"][0]["cues"]))
    # **没有快照就一个边界都不许动**：那时无从知道是谁生成了这条边界，
    # 猜（min/max 或容差窗）就会把切分毁掉。
    seg_doc["events"][3]["t_end"] += 250_000
    keep = [(c["t_start"], c["t_end"]) for c in seg_doc["tracks"][0]["cues"]]
    tracksio.refresh_cue_bounds(seg_doc)
    check("**没给快照时不动边界**",
          [(c["t_start"], c["t_end"]) for c in seg_doc["tracks"][0]["cues"]] == keep)

    # **相邻两段共享同一个切点，回抠之后必须还共享**（第二轮复审）：
    # "B 消失"既是前段的终点、也是后段的起点，可后段的成员里已经没有 B 了——
    # 逐 cue 配对时后段的起点就不动，于是 A 在交界处重复显示 0.25 秒。
    A = bt.Run([0, 0, 100, 20], "A", 0, 10_000_000, 20, .9, ["A"])
    B = bt.Run([0, 40, 100, 60], "B", 0, 2_000_000, 4, .9, ["B"])
    two = bt.cue_segments([A, B], mode="segment")
    ids = {id(A): 0, id(B): 1}
    doc3 = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {}, "frame_us": 500_000,
            "size": [1000, 1000], "provenance": {"main_track": "r00"},
            "regions": [{"index": 0, "label": "subtitle"}],
            "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                        "srt": "t.srt", "ui_lines": [],
                        "cues": bt.cues_from_segments("r00", two, ids)}],
            "events": [bt.event_dict(A, 0, 0), bt.event_dict(B, 1, 0)]}
    snap3 = tracksio.event_times(doc3)
    doc3["events"][1]["t_end"] = 2_250_000          # B 的消失点回抠到 2.25 s
    tracksio.refresh_cue_bounds(doc3, snap3)
    cs = doc3["tracks"][0]["cues"]
    check("**相邻段的共同切点一起挪**（否则交界处会重复显示）",
          cs[0]["t_end"] == cs[1]["t_start"] == 2_250_000,
          f"{cs[0]['t_end']} / {cs[1]['t_start']}")

    # **一条 cue 的边界只认它自己的成员**（第三轮复审）：只按"旧时间戳相同"合并的话，
    # 别的轨、别的 cue 里碰巧同一时刻开始/结束的事件会被当成同一个切点。
    def two_track_doc(mode):
        x = bt.Run([0, 0, 100, 20], "A", 1_000_000, 2_000_000, 2, .9, ["A"])
        y = bt.Run([0, 40, 100, 60], "B", 1_000_000, 2_000_000, 2, .9, ["B"])
        return x, y, {
            "schema": tracksio.SCHEMA, "tag": "t", "meta": {}, "frame_us": 500_000,
            "size": [1000, 1000], "provenance": {"main_track": "r00"},
            "regions": [{"index": 0, "label": "subtitle"}, {"index": 1, "label": "subtitle"}],
            "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                        "srt": "a.srt", "ui_lines": [],
                        "cues": bt.cues_from_segments("r00", bt.cue_segments([x], mode=mode),
                                                      {id(x): 0})},
                       {"id": "r01", "kind": "region", "region": 1, "label": "subtitle",
                        "srt": "b.srt", "ui_lines": [],
                        "cues": bt.cues_from_segments("r01", bt.cue_segments([y], mode=mode),
                                                      {id(y): 1})}],
            "events": [bt.event_dict(x, 0, 0), bt.event_dict(y, 1, 1)]}

    _, _, dd = two_track_doc("segment")
    snap4 = tracksio.event_times(dd)
    dd["events"][0]["t_start"], dd["events"][0]["t_end"] = 750_000, 2_250_000   # 只回抠 r00
    tracksio.refresh_cue_bounds(dd, snap4)
    c00, c01 = dd["tracks"][0]["cues"][0], dd["tracks"][1]["cues"][0]
    check("**只回抠一条轨，另一条不许跟着变**",
          (c01["t_start"], c01["t_end"]) == (1_000_000, 2_000_000),
          f"r01 变成了 {c01['t_start']}–{c01['t_end']}")
    check("**本轨的终点修正不许被别人压掉**",
          (c00["t_start"], c00["t_end"]) == (750_000, 2_250_000),
          f"r00 是 {c00['t_start']}–{c00['t_end']}")

    # `raw` 档：同轨两条**各自**回抠，起点相同不等于同一个切点
    c = bt.Run([0, 0, 100, 20], "C", 1_000_000, 2_000_000, 2, .9, ["C"])
    d = bt.Run([0, 40, 100, 60], "D", 1_000_000, 3_000_000, 4, .9, ["D"])
    ids2 = {id(c): 0, id(d): 1}
    raw_doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {}, "frame_us": 500_000,
               "size": [1000, 1000], "provenance": {"main_track": "r00"},
               "regions": [{"index": 0, "label": "subtitle"}],
               "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                           "srt": "a.srt", "ui_lines": [],
                           "cues": bt.cues_from_segments(
                               "r00", bt.cue_segments([c, d], mode="raw"), ids2)}],
               "events": [bt.event_dict(c, 0, 0), bt.event_dict(d, 1, 0)]}
    snap5 = tracksio.event_times(raw_doc)
    raw_doc["events"][0]["t_start"] = 750_000
    raw_doc["events"][1]["t_start"] = 1_250_000
    tracksio.refresh_cue_bounds(raw_doc, snap5)
    got = [(x["t_start"], x["t_end"]) for x in raw_doc["tracks"][0]["cues"]]
    check("**raw 档：起点相同不等于同一个切点**（两条各回各的）",
          sorted(x[0] for x in got) == [750_000, 1_250_000], got)
    check("**同屏的两条 cue 不许被夹成首尾相接**（它们本来就重叠）",
          max(x[1] for x in got) == 3_000_000, got)

    # **首尾相接的独立字幕不算共享切点**（第四轮复审）：判据是"有成员跨过切点"，
    # 不是"旧时间戳相接"。A 0–2 s、B 2–4 s，回抠说 A 1.8 s 消失、B 1.9 s 出现——
    # 中间那 0.1 s 屏幕本来就是空的，绑成一个切点就会**在空白期提前显示**。
    for mode in ("cue", "raw", "segment"):
        A2 = bt.Run([0, 0, 100, 20], "A", 0, 2_000_000, 4, .9, ["A"])
        B2 = bt.Run([0, 0, 100, 20], "B", 2_000_000, 4_000_000, 4, .9, ["B"])
        ids3 = {id(A2): 0, id(B2): 1}
        gap_doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {}, "frame_us": 500_000,
                   "size": [1000, 1000], "provenance": {"main_track": "r00"},
                   "regions": [{"index": 0, "label": "subtitle"}],
                   "tracks": [{"id": "r00", "kind": "region", "region": 0,
                               "label": "subtitle", "srt": "a.srt", "ui_lines": [],
                               "cues": bt.cues_from_segments(
                                   "r00", bt.cue_segments([A2, B2], mode=mode), ids3)}],
                   "events": [bt.event_dict(A2, 0, 0), bt.event_dict(B2, 1, 0)]}
        snap6 = tracksio.event_times(gap_doc)
        gap_doc["events"][0]["t_end"] = 1_800_000
        gap_doc["events"][1]["t_start"] = 1_900_000
        tracksio.refresh_cue_bounds(gap_doc, snap6)
        gg = [(c["t_start"], c["t_end"]) for c in gap_doc["tracks"][0]["cues"]]
        check(f"**{mode} 档：相接但没有共同成员的两条，各回各的边界**"
              "（否则空白期就提前显示了）",
              sorted(gg) == [(0, 1_800_000), (1_900_000, 4_000_000)], gg)

    # 起点口径是**投影**，不是产物里的状态：存了 full 就切不回 first 的那个坑
    ev = doc3["events"][0]
    ev["t_full"] = 1_600_000
    ev["t_start"] = 1_000_000
    cue0 = cs[0]
    evs0 = [doc3["events"][i] for i in cue0["events"]]
    check("full 起点取成员 t_full 的最大值",
          export.cue_start(doc3, cue0, evs0, "full") == 1_600_000)
    check("**再切回 first 还能回到首字**（full 不许写回 cue）",
          export.cue_start(doc3, cue0, evs0, "first") == cue0["t_start"])

    # 端到端：build_tracks 写的 SRT 和导出器投影出来的**逐字节相同**
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        meta = {"width": 1000, "height": 1000, "sample_fps": 2, "video": "synthetic"}
        rows = []
        for i in range(40):
            t = i * 500_000
            rows.append({"t_us": t, "box": [100, 800, 700, 840],
                         "text": "ずっと同じ台詞です", "conf": .95})
            rows.append({"t_us": t, "box": [20, 20, 80, 40], "text": "Auto", "conf": .95})
            for k, txt in enumerate(("第一行文字", "第二行文字", "第三行文字")):
                y = 700 - i * 24 + k * 40
                rows.append({"t_us": t, "box": [100, y, 400, y + 30], "text": txt, "conf": .9})
        (d / "obs.jsonl").write_text(
            "\n".join(json.dumps(x) for x in [{"_meta": meta}, *rows]), encoding="utf-8")
        import contextlib
        import io
        with patch.object(sys, "argv", ["build_tracks.py", str(d / "obs.jsonl"), "--outdir",
                                        str(d), "--tag", "t", "--ui-min-support", "5"]), \
                contextlib.redirect_stdout(io.StringIO()):
            bt.main()
        built = {p.name: p.read_bytes() for p in d.glob("*.srt")}
        doc2 = tracksio.load(d / "t-tracks.json")
        redo = d / "redo"
        redo.mkdir()
        export.export_srt(doc2, redo)
        check("**导出器出的 SRT 和 build_tracks 写的逐字节相同**",
              built and all(built[n] == (redo / n).read_bytes() for n in built),
              f"{len(built)} 份")
        check("provenance 并进了同一份 JSON，不再单独一个文件",
              "provenance" in doc2 and not (d / "t-provenance.json").exists())
        # **全是沿用票时 n_independent = 0**，不许回退成 n_obs——那正好把
        # "这条一次都没被重新识别过"抹掉，而下游要的就是这件事（audit-4 C4）。
        allr = bt.Run([0, 0, 10, 10], "x", 0, 1_000_000, 3, .9, ["x"] * 3,
                      reused=[True, True, True])
        check("**全是沿用票时 n_independent 是 0**",
              bt.event_dict(allr, 0, 0)["n_independent"] == 0)
        halfr = bt.Run([0, 0, 10, 10], "x", 0, 1_000_000, 4, .9, ["x"] * 4,
                       reused=[False, True, False, True])
        check("有独立票时照常数", bt.event_dict(halfr, 0, 0)["n_independent"] == 2)
        check("会动的事件带轨迹，不动的只有一个框",
              all(len(e.get("boxes", [])) > 1 for e in doc2["events"] if "moving" in e["flags"])
              and all("boxes" not in e for e in doc2["events"] if "moving" not in e["flags"]))
        check("build_tracks 给每个区域写了对齐判定和依据（叠加导出只照着锚边）",
              doc2["regions"] and all(r["align"] in tracksio.ALIGNS and r["align_how"]["by"] in ("stats", "vote", "none")
                                      for r in doc2["regions"]))
        from flowocr.output import layout as LY
        from flowocr.output import script as SC
        n_dev = render_overlay(d / "dev.ass", SC.tracks_items(doc2, "all"), doc2, "dev")
        text = (d / "dev.ass").read_text(encoding="utf-8")
        # 条数契约：**不动的事件**底板 + 正文 + 编号各一条；会动的**按轨迹分段**，每段各一套——
        # 一条从首点到末点的 `\move` 会把停顿和步进抹成匀速滑（运动形态审计要看的恰恰是这个）。
        # 阶段 4 按字幕稿里的起止（厘秒）切段
        segs = sum(len(LY.motion_segments(e.get("boxes") if "moving" in e["flags"] else None,
                                          -(-e["t_start"] // 10_000) * 10_000, e["t_end"] // 10_000 * 10_000))
                   for e in doc2["events"])
        check("dev 叠加：每个事件只画一次，底板 / 正文 / 编号各一条 × 轨迹段数（不动的就是 1 段）",
              text.count("\nDialogue: 1,") == segs and text.count("\nDialogue: 2,") == segs
              and sum(n_dev[k] for k in ("line", "name")) == len(doc2["events"]), (text.count("\nDialogue: 1,"), segs))
        check("dev 叠加：Actor（Name 字段）带事件的区域号（Aegisub 里能按它排序、挑行）",
              all(ln.split(",")[4].startswith("r") for ln in text.splitlines() if ln.startswith("Dialogue:")))
        check("叠加 ASS 的 PlayRes 就是视频尺寸", "PlayResX: 1000" in text)
    one = [[0, [0, 0, 10, 10]], [1_000_000, [100, 0, 110, 10]]]
    half = LY.motion_segments(one, 0, 500_000)
    # 轨迹段被裁一半时端点要**插值**：照搬原端点的话，半段时间里会把整段位移重播一遍
    check("**裁一半的位移段，端点跟着插值**（不是重播整段）",
          len(half) == 1 and LY.pos_tag(0, 0, half[0], (0, 0)) == "\\move(0,0,50,0,0,500)", half)
    check("不裁的时候还是整段；不动的事件一段、用 \\pos",
          LY.pos_tag(0, 0, LY.motion_segments(one, 0, 1_000_000)[0], (0, 0)) == "\\move(0,0,100,0,0,1000)"
          and LY.motion_segments(None, 0, 9) == [(0, 9, None, None)]
          and LY.pos_tag(3, 4, (0, 9, None, None), (0, 0)) == "\\pos(3,4)")
    check("在动的元素按它在条目框里的偏移跟着轨迹走（底板、正文、编号一起挪）",
          LY.pos_tag(5, 2, (0, 1000, (10, 0), (20, 0)), (0, 0)) == "\\move(15,2,25,2,0,1)")
    # 字号换算：**量出来的**（`output-format-plan（已归档）` §3.1，探针 一次性探针 probe_ass_fontsize.py）。
    # 写死一个数会让 overlay 的字比框小一大截
    check("叠加用的字体有自己量出来的换算比例，且 > 1（Fontsize 必然大于字形像素高）",
          set(export.FONT_H_RATIO) == {export.FONT_CN} and export.FONT_H_RATIO[export.FONT_CN] > 1,
          export.FONT_H_RATIO)
    check("ASS 只到厘秒，起点向上取整、终点向下取整",
          export.ass_time(1_234_567, True) == "0:00:01.24"
          and export.ass_time(1_234_567, False) == "0:00:01.23",
          export.ass_time(1_234_567, True))
    check("花括号不会被当成覆盖标签", "{" not in export.ass_text("a{b}c"))


def t_overlay_style() -> None:
    """叠加样式（overlay-style 计划）：对齐判据、锚点、三个预设的保留集合、编号不影响正文、效果的门。"""
    print("叠加样式")
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr import extensions as X
    from flowocr.analyze import align as AL
    from flowocr.artifacts import tracksio
    from flowocr.output import export
    from flowocr.output import layout as LY
    from flowocr.output import render as R
    from flowocr.output import script as OV
    from flowocr.typeset import core as TS

    def ev(i, box, t0=0, t1=1_000_000, region=0, flags=(), text="あいうえお", **kw):
        return {"id": i, "region": region, "t_start": t0, "t_end": t1, "box": list(box), "text": text,
                "flags": list(flags), **kw}

    # ---- 对齐判据：区域统计 ----
    lefts = [ev(i, [100, 40 * i, 300 + 90 * i, 40 * i + 30]) for i in range(6)]
    centers = [ev(i, [960 - 100 - 45 * i, 40 * i, 960 + 100 + 45 * i, 40 * i + 30]) for i in range(6)]
    rights = [ev(i, [1500 - 200 - 90 * i, 40 * i, 1500, 40 * i + 30]) for i in range(6)]
    same_w = [ev(i, [100, 40 * i, 700, 40 * i + 30]) for i in range(6)]
    check("对齐·区域统计：左沿齐 → left、中心齐 → center、右沿齐 → right",
          [AL.by_stats(x)[0] for x in (lefts, centers, rights)] == ["left", "center", "right"])
    check("对齐·区域统计：行宽几乎不变（三条边一样稳）、样本太少都判不出，交给投票",
          AL.by_stats(same_w)[0] is None and AL.by_stats(lefts[:4]) == (None, None))
    # ---- 成对投票（owner 的"上下附近左边界一致"） ----
    full, short_l, short_c = [100, 800, 700, 830], [100, 840, 400, 870], [250, 840, 550, 870]
    check("对齐·成对投票：左沿齐中心不齐 → left；中心齐两沿不齐 → center；等宽的一对不投",
          AL.pair_vote(full, short_l) == "left" and AL.pair_vote(full, short_c) == "center"
          and AL.pair_vote(full, [100, 840, 700, 870]) is None)
    # 排满的左对齐文本框：大多数行等宽（统计判不出），两条 cue 的末行短、左沿齐 → 投票 left
    box_evs = [ev(i, full, t0=10_000_000 * i, t1=10_000_000 * i + 5) for i in range(8)]
    box_evs += [ev(8 + k, full, t0=100_000_000 + k * 10_000_000, t1=100_000_000 + k * 10_000_000 + 5) for k in range(2)]
    box_evs += [ev(10 + k, short_l, t0=100_000_000 + k * 10_000_000, t1=100_000_000 + k * 10_000_000 + 5) for k in range(2)]
    got, how = AL.judge(box_evs, [])
    check("对齐·统计判不出时投票定区域（排满的左对齐文本框，两条多行 cue 的末行短）",
          got == "left" and how["by"] == "vote" and how["votes"] == {"left": 2}, (got, how))
    def pair(i, t, other):
        return [ev(i, full, t0=t, t1=t + 5), ev(i + 1, other, t0=t, t1=t + 5)]
    base = [ev(i, full, t0=10_000_000 * i, t1=10_000_000 * i + 5) for i in range(20)]
    one_vote = base + pair(100, 900_000_000, short_l)
    tie = one_vote + pair(102, 910_000_000, short_l) + pair(104, 920_000_000, short_c) + pair(106, 930_000_000, short_c)
    check("对齐·1 票不算、2 : 2 平局居中（都记成 none）",
          AL.judge(one_vote, [])[0] == "center" and AL.judge(one_vote, [])[1]["by"] == "none"
          and AL.judge(tie, [])[0] == "center" and AL.judge(tie, [])[1]["by"] == "none")
    check("对齐·名牌不进统计但能当投票的邻居（名牌 + 单行正文左沿齐）",
          AL.judge([ev(0, full, t0=0, t1=5), ev(1, full, t0=10, t1=15)],
                   [ev(9, [100, 760, 250, 790], t0=0, t1=5, flags=["nameplate"]),
                    ev(8, [100, 760, 250, 790], t0=10, t1=15, flags=["nameplate"])])[0] == "left")
    check("对齐·在动 / 滚动 / 名牌 / UI 的事件不进区域统计",
          not any(AL.usable(ev(0, full, flags=[f])) for f in ("moving", "scrolling", "nameplate", "ui_footprint")))
    bad = {"schema": tracksio.SCHEMA, "meta": {}, "frame_us": 1, "size": [1, 1], "provenance": {}, "tracks": [],
           "events": [], "regions": [{"index": 0, "align": "middle", "align_how": {}}]}
    check("tracksio：align 取值不对就抛；没有 align 的旧产物照常能读",
          raises(lambda: tracksio.validate(bad), tracksio.SchemaError)
          and not raises(lambda: tracksio.validate({**bad, "regions": [{"index": 0}]})))

    # ---- 锚点 ----
    rows_half = [[100, 0, 900, 30], [300, 40, 500, 70]]          # 第二行被剔掉半截（真实是 300–700）
    check("锚点·matched 多行：居中取最宽那行的中心（半截的行不带歪），左 / 右取最外的沿",
          LY.anchor_axis(rows_half, "center", True) == [500, 500]
          and LY.anchor_axis(rows_half, "left", True) == [100, 100]
          and LY.anchor_axis(rows_half, "right", True) == [900, 900])
    check("锚点·tracks 一行：取这一行自己的框",
          LY.anchor_axis(rows_half, "center", False) == [500, 400] and LY.anchor_axis(rows_half, "left", False) == [100, 300])

    # ---- tracks 的三种保留集合 ----
    evs = [ev(0, [100, 900, 700, 930], region=0), ev(1, [100, 940, 400, 970], region=0),
           ev(2, [10, 10, 60, 30], region=1, text="HP"), ev(3, [100, 860, 200, 890], region=1, flags=["nameplate"], text="名前"),
           ev(4, [800, 500, 900, 530], region=1, text="UI", flags=["ui_footprint"])]
    doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
           "provenance": {"main_track": "r00"},
           "regions": [{"index": 0, "align": "left", "align_how": {"by": "stats"}},
                       {"index": 1, "align": "center", "align_how": {"by": "none"}}],
           "events": evs,
           "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "a.srt", "ui_lines": [],
                       "cues": [{"id": "r00c0", "t_start": 0, "t_end": 5, "events": [0, 1]},
                                {"id": "r00c1", "t_start": 5, "t_end": 9, "events": [1]}]},   # segment 模式：同一事件在两条 cue
                      {"id": "r01", "kind": "region", "region": 1, "srt": "b.srt", "ui_lines": ["HP"],
                       "cues": [{"id": "r01c0", "t_start": 0, "t_end": 9, "events": [2, 3, 4]}]},
                      {"id": "np", "kind": "nameplate", "region": -1, "srt": "n.srt", "ui_lines": [],
                       "cues": [{"id": "npc0", "t_start": 0, "t_end": 9, "events": [3]}]}]}
    tracksio.validate(doc)
    all_it, main_it = OV.tracks_items(doc, "all"), OV.tracks_items(doc, "main")
    check("tracks·全部：每个事件只出一条（segment 模式下跨 cue 的不重画），UI 按那条轨的 ui_lines + 全局框位标",
          [it.events[0]["id"] for it in all_it] == [0, 1, 2, 3, 4]
          and [it.ui for it in all_it] == [False, False, True, False, True]
          and [it.main for it in all_it] == [True, True, False, False, False]
          and all_it[3].kind == "name" and all_it[0].align == "left" and all_it[0].cue == "r00c0")
    check("tracks·主轨：主轨（cue_lines，UI 已剔）+ 名牌轨，按事件 id 去重",
          [it.events[0]["id"] for it in main_it] == [0, 1, 3])
    doc_band = {**doc, "provenance": {"main_track": "main"},
                "tracks": doc["tracks"] + [{"id": "main", "kind": "main", "regions": [0, 1], "srt": "m.srt", "ui_lines": ["HP"],
                                            "cues": [{"id": "mc0", "t_start": 0, "t_end": 9, "events": [0, 1, 2, 3, 4]}]}]}
    check("tracks·主轨：名牌所在区域并进了主轨时也只画一次；UI 按主轨自己的判定剔",
          [it.events[0]["id"] for it in OV.tracks_items(doc_band, "main")] == [0, 1, 3])
    m_items = [{"layer": "body", "start_us": 0, "end_us": 5_000_000, "rows": [evs[0]["box"], evs[1]["box"]],
                "events": [0, 1], "jp": "原文", "cn": "译文"},
               {"layer": "name", "start_us": 0, "end_us": 9_000_000, "rows": [evs[3]["box"]], "events": [3],
                "jp": None, "cn": None, "ocr": "名前"}]
    used_cn: set[int] = set()
    OV.project_items(m_items, doc, "cn", OV.LAYERS, used_cn)
    used_nobody: set[int] = set()
    OV.project_items(m_items, doc, "cn", ("name",), used_nobody)
    check("matched·全部保留（dev / default_all）：只有真叠了译文的条目占掉事件；没对上说话人的名牌、没认领的行、"
          "关掉的层都照 tracks 画 OCR 原文",
          used_cn == {0, 1} and [x.events[0]["id"] for x in OV.unmatched_items(doc, used_cn)] == [2, 3, 4]
          and used_nobody == set() and len(OV.unmatched_items(doc, used_nobody)) == 5
          and not any(x.translated for x in OV.unmatched_items(doc, used_cn)),
          (used_cn, used_nobody))
    panel = OV.Item(0, 9_000_000, [[90, 890, 710, 980]], ["一段"], "block", translated=True, row_h=30)
    check("matched·全部保留：已画的阅读面板底下的事件不再补画原文（面板条目不记事件，按框 + 时间判）",
          [x.events[0]["id"] for x in OV.unmatched_items(doc, used_nobody, [panel])] == [2, 3, 4])
    doc_fine = {**doc, "frame_us": 50_000}             # 采样间隔 50 ms：1 s 的事件切得开
    under = [x for x in OV.tracks_items(doc_fine, "all") if x.events[0]["id"] == 0][0]
    s0, e0, fu = under.start_us, under.end_us, doc_fine["frame_us"]
    mid = [OV.Item(s0 + 4 * fu, s0 + 6 * fu, panel.rows, ["一段"], "block"), OV.Item(s0 + 8 * fu, s0 + 9 * fu, panel.rows, ["一段"], "block")]
    parts = [(x.start_us, x.end_us) for x in OV.unmatched_items(doc_fine, used_nobody, mid) if x.events[0]["id"] == 0]
    edge = [(x.start_us, x.end_us) for x in OV.unmatched_items(doc_fine, used_nobody, [OV.Item(s0 + fu // 2, e0, panel.rows, ["一段"], "block")])
            if x.events[0]["id"] == 0]
    tiny = [x for x in OV.unmatched_items({**doc, "frame_us": 10 * (e0 - s0)}, used_nobody) if x.events[0]["id"] == 0]
    far = [OV.Item(e0 + 20_000_000, e0 + 30_000_000, panel.rows, ["一段"], "block"), OV.Item(0, max(0, s0 - 1), panel.rows, ["一段"], "block")]
    tiny_far = [x for x in OV.unmatched_items({**doc, "frame_us": 10 * (e0 - s0)}, used_nobody, far) if x.events[0]["id"] == 0]
    check("matched·全部保留：面板只盖住事件的一部分时间，只扣面板在屏的时段，面板前后、两块面板之间照画；扣剩不到一个采样间隔的碎段不画；"
          "没被面板盖到的事件哪怕本身短于一个采样间隔也照画；同位置、时间不相交的面板不算盖到（Codex 复审反例）",
          e0 - s0 >= 10 * fu and parts == [(s0, s0 + 4 * fu), (s0 + 6 * fu, s0 + 8 * fu), (s0 + 9 * fu, e0)] and edge == []
          and len(tiny) == 1 and len(tiny_far) == 1 and (tiny_far[0].start_us, tiny_far[0].end_us) == (s0, e0),
          (s0, e0, fu, parts, edge, len(tiny), len(tiny_far)))

    # ---- 建轨末尾结算回抠（--refine auto）：端到端跑 build_tracks.main——结算在内存里做、SRT 重出之后才写 tracks.json；
    #      结算或写出哪一步失败都不留下 tracks.json（上一轮的那份开头就删了），驱动判缓存认不到半成品 ----
    import contextlib
    import io
    import json as _json
    from unittest.mock import patch as _patch
    from flowocr.analyze import build_tracks as BT
    from flowocr.analyze import refine_boundaries as RBm
    with tempfile.TemporaryDirectory() as td2:
        d2 = Path(td2)
        tp2 = d2 / "t-tracks.json"
        obs2 = d2 / "o.jsonl"
        rows2 = [{"t_us": i * 500_000, "box": [100, 800, 700, 840], "text": "ずっと同じ台詞です", "conf": .95} for i in range(12)]
        obs2.write_text("\n".join(_json.dumps(x) for x in [
            {"_meta": {"width": 1000, "height": 1000, "sample_fps": 2, "video": "synthetic", "src_fps": 30.0,
                       "config": {"refine_fused": True}, "edge": {"on": 3}}}, *rows2]), encoding="utf-8")

        def build(stub, export_fails=False):
            tp2.write_text("{}", encoding="utf-8")                  # 上一轮的产物
            msg = ""
            with _patch.object(RBm, "refine_tracks", stub), contextlib.redirect_stdout(io.StringIO()), \
                    _patch.object(sys, "argv", ["build_tracks.py", str(obs2), "--outdir", str(d2), "--tag", "t"]), \
                    contextlib.ExitStack() as st:
                if export_fails:
                    def denied(*a, **k):
                        raise PermissionError("SRT 被占用")
                    st.enter_context(_patch.object(export, "export_srt", denied))
                try:
                    BT.main()
                except (SystemExit, PermissionError) as exc:
                    msg = str(exc)
            return msg

        def stub(data, **kw):
            for ev in [*data["events"], *(c for tr in data["tracks"] for c in tr["cues"])]:
                ev["t_end"] += 1000                                   # 帧级时刻：SRT 重出之后看得出来
            data["text_effect"] = {"samples": 0}
            data["boundary_refined"] = {"runs": 1, "decoder": kw["decoder"]}
            return {"runs": 1, "moved": 1, "mean_start": 0.0, "mean_end": 0.0, "how": {}}
        build(stub)
        back = tracksio.load(tp2) if tp2.is_file() else {}
        srts = sorted(d2.glob("t-*.srt"))
        check("建轨 --refine auto 正路径：tracks.json 带 boundary_refined（decoder=obs）、SRT 是结算后的时刻、不另存 -tracks-refined.json",
              back.get("boundary_refined", {}).get("decoder") == "obs" and srts
              and all(p.read_text(encoding="utf-8").split("\n")[1].endswith(",001") for p in srts)
              and not list(d2.glob("*-refined*")), (sorted(p.name for p in d2.iterdir()), back.get("boundary_refined")))

        def boom(data, **kw):
            raise SystemExit("[证据] 一条证据都没认领到")
        msg = build(boom)
        check("建轨 --refine auto 结算失败：不留 tracks.json（上一轮的也删了，免得被当缓存复用），报错指向 --refine off",
              not tp2.exists() and "--refine off" in msg, msg)
        msg = build(stub, export_fails=True)
        check("建轨 --refine auto 结算成功、重出 SRT 失败：同样不留 tracks.json（不配着采样级或写了一半的 SRT 被复用）",
              not tp2.exists() and "占用" in msg, msg)

        # SRT 起点口径（原来回抠 CLI 的 --srt-only --srt-start full）挪进导出：另存 -full，不覆盖默认那份
        tp3 = d2 / "e" / "t-tracks.json"
        tp3.parent.mkdir()
        tracksio.dump(tp3, {k: v for k, v in doc.items() if k != "schema"})
        with contextlib.redirect_stdout(io.StringIO()):
            export.main([str(tp3)])
            first = {p.name: p.read_bytes() for p in tp3.parent.glob("*.srt")}
            export.main([str(tp3), "--start", "full"])
        full = sorted(p.name for p in tp3.parent.glob("*-full.srt"))
        check("导出 --start full：每条轨另存一份 -full.srt，默认那份一个字节不动（起点口径只在导出时投影）",
              full == sorted(n[:-4] + "-full.srt" for n in first)
              and all((tp3.parent / n).read_bytes() == b for n, b in first.items()), full)

    # ---- 预览的字幕轨平移（dev_tools/preview.py：--mode ass 把原片时间轴的 ASS 平移到片段上） ----
    import preview as PV
    src = "\n".join([
        "[Events]",
        "Dialogue: 0,0:00:10.00,0:00:20.00,overlay,,0,0,0,,{\\alpha&HFF&\\t(1000,1001,\\alpha&H00&)}あ{\\alpha&HFF&\\t(6000,6001,\\alpha&H00&)}い",
        "Dialogue: 0,0:00:11.00,0:00:19.00,overlay,,0,0,0,,{\\move(0,0,100,0,1000,5000)}走到一半",
        "Dialogue: 0,0:00:10.00,0:00:19.00,overlay,,0,0,0,,{\\move(0,0,100,0,0,1000)}已走完",
        "Dialogue: 0,0:00:05.00,0:00:10.00,overlay,,0,0,0,,窗前",
        "Dialogue: 0,0:00:40.00,0:00:41.00,overlay,,0,0,0,,窗后",
        "Dialogue: 0,0:00:14.00,0:00:30.00,overlay,,0,0,0,,带, 逗号",
    ])
    shifted = [ln for ln in PV.shift_ass(src, 13.0, 10.0).splitlines() if ln.startswith("Dialogue:")]
    check("预览平移：已完成的 \\t 夹成 (0,1) 一开场就到位（不是 libass 当成整条的 (0,0)）、没完成的往前扣；"
          "走到一半的 \\move 从半路接着走、走完的换成 \\pos；窗外的丢掉；正文带逗号不串列",
          len(shifted) == 4
          and "\\t(0,1,\\alpha&H00&)}あ" in shifted[0] and "\\t(3000,3001," in shifted[0] and ",0:00:00.00,0:00:07.00," in shifted[0]
          and "\\move(25,0,100,0,0,3000)" in shifted[1]
          and "\\pos(100,0)" in shifted[2]
          and shifted[3].endswith(",带, 逗号") and ",0:00:01.00,0:00:10.00," in shifted[3],
          shifted)
    sl = [ln for ln in PV.slice_ass("\n".join([
        "[Events]",
        "Dialogue: 0,0:00:00.00,0:00:10.00,overlay,,0,0,0,,{\\alpha&HFF&\\t(5000,5001,\\alpha&H00&)}长",
        "Dialogue: 0,0:00:04.00,0:00:06.00,overlay,,0,0,0,,短"])).splitlines() if ln.startswith("Dialogue:")]
    check("预览字幕轨切段（为 VLC 3：时间重叠的事件会冻画面、漏画）：长事件在别人的起止处切成首尾相接的段，"
          "打字机时刻按段扣；被包住的短事件原样",
          len(sl) == 4
          and ",0:00:00.00,0:00:04.00," in sl[0] and "\\t(5000,5001," in sl[0]
          and ",0:00:04.00,0:00:06.00," in sl[1] and "\\t(1000,1001," in sl[1]
          and ",0:00:06.00,0:00:10.00," in sl[2] and "\\t(0,1," in sl[2]
          and sl[3].endswith(",0:00:04.00,0:00:06.00,overlay,,0,0,0,,短"), sl)

    def libass_alpha(ass: str, text: str, t_abs: float):
        """照 libass（`interpolate_alpha`，\\fad 的 t3 = 时长 − 淡出）算正文为 text 的事件在绝对时刻 t_abs 秒的透明度；不在屏上是 None。
        和 preview.py 各写一份：守卫核的是"画出来的样子"，不照抄被测的实现。"""
        import re
        for ln in ass.splitlines():
            if not ln.startswith("Dialogue:") or not ln.endswith("}" + text):
                continue
            f = ln.split(":", 1)[1].split(",", 9)
            s, e = (int(x[:-9]) * 3600 + int(x[-8:-6]) * 60 + float(x[-5:]) for x in (f[1].strip(), f[2]))
            if not s <= t_abs < e:
                continue
            m = re.search(r"\\fade?\(([-\d,]+)\)", f[9])
            v = [int(x) for x in m[1].split(",")]
            dur = round((e - s) * 1000)
            a1, a2, a3, t1, t2, t3, t4 = (255, 0, 255, 0, v[0], dur - v[1], dur) if len(v) == 2 else v
            now = round((t_abs - s) * 1000)
            if now < t1:
                return a1
            if now < t2:
                return a1 + (a2 - a1) * (now - t1) / (t2 - t1)
            if now < t3:
                return a2
            return a2 + (a3 - a2) * (now - t3) / (t4 - t3) if now < t4 else a3
        return None
    fsrc = "\n".join([
        "[Events]",
        "Dialogue: 0,0:00:00.00,0:00:04.00,overlay,,0,0,0,,{\\fad(1000,1000)}淡",
        "Dialogue: 0,0:00:00.50,0:00:03.50,overlay,,0,0,0,,{\\fad(0,0)}切",   # 切点落在淡入中间（0.5 s）和淡出中间（3.5 s）
        "Dialogue: 0,0:00:03.20,0:00:03.30,overlay,,0,0,0,,{\\fad(0,0)}再切"])
    w0, wd = 0.75, 3.0                        # 平移窗 [0.75, 3.75)：起点在淡入中间、终点在淡出中间
    variants = {"原文": (fsrc, 0.0), "切段": (PV.slice_ass(fsrc), 0.0), "平移": (PV.shift_ass(fsrc, w0, wd), w0),
                "平移后切段": (PV.slice_ass(PV.shift_ass(fsrc, w0, wd)), w0)}
    bad = []
    for k in range(0, 4000, 10):
        t_abs = k / 1000
        ref = libass_alpha(fsrc, "淡", t_abs)
        for name, (ass, off) in variants.items():
            if off and not off <= t_abs < off + wd:
                continue
            got = libass_alpha(ass, "淡", t_abs - off)
            if got is None or abs(got - ref) > 1:
                bad.append((name, t_abs, ref, got))
    check("预览字幕轨·淡入淡出：原文 / 切段 / 平移 / 平移后切段，同一绝对时刻的透明度处处相同（±1 级取整；按 libass 的算式逐 10 ms 核，"
          "切点落在淡入、淡出中间；平移生成的七参数 \\fade 再切段也接得上；窗尾截掉的淡出不凭空出现）",
          not bad, bad[:6])

    # ---- 画出来：编号不影响正文、锚点标签、UI / 非主轨的颜色 ----
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        render_overlay(d / "a.ass", all_it, doc, "dev")
        render_overlay(d / "b.ass", all_it, doc, "dev", label=False)
        a = [ln for ln in (d / "a.ass").read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")]
        b = [ln for ln in (d / "b.ass").read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")]
        check("编号不影响正文：有无编号，底板和正文的 Dialogue 逐字节相同，多出来的只有编号那层",
              [x for x in a if not x.startswith("Dialogue: 2,")] == b and sum(x.startswith("Dialogue: 2,") for x in a) == 5)
        text0 = [x for x in a if x.startswith("Dialogue: 1,")]
        check("左对齐区域用 \\an4 锚在自己框的左沿；居中区域 \\an5 锚在框中心",
              "\\an4\\pos(100,915)" in text0[0] and "\\an5\\pos(35,20)" in text0[2], text0[:3])
        check("dev：非主轨浅蓝（#73d7ff）、UI 黄字（#ffe033）且编号带 ui 后缀；主轨不改色",
              f"\\1c&H{TS.OFFMAIN_COLOR}&" in text0[3] and TS.OFFMAIN_COLOR == "FFD773"
              and f"\\1c&H{TS.UI_COLOR}&" in text0[2] and TS.UI_COLOR == "33E0FF" and "\\1c&H" not in text0[0]
              and any(x.startswith("Dialogue: 2,") and x.endswith("}1ui") for x in a))
        # 每条编号的位置：落在它前面那几块底板（同一条字幕）外接矩形的内侧右上角
        import re
        inner, plates_seen = [], []
        for x in a:
            m = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", x)
            if x.startswith("Dialogue: 0,"):
                w, h = map(float, re.search(r"m 0 0 l ([\d.]+) 0 [\d.]+ ([\d.]+)", x).groups())
                plates_seen.append((float(m[1]), float(m[2]), float(m[1]) + w, float(m[2]) + h))
            elif x.startswith("Dialogue: 2,"):
                x1, y0 = max(p[2] for p in plates_seen), min(p[1] for p in plates_seen)
                inner.append("\\an9" in x and f"\\1c&H{TS.LABEL_COLOR}&" in x
                             and abs(float(m[1]) - (x1 - TS.LABEL_INSET)) < 1 and abs(float(m[2]) - (y0 + TS.LABEL_INSET)) < 1)
                plates_seen = []
        check("dev 的区域编号：红字（#ff2d2d）、右上对齐，画在这条字幕底板外接矩形的内侧右上角",
              len(inner) == 5 and all(inner) and TS.LABEL_COLOR == "2D2DFF", inner)
        render_overlay(d / "c.ass", all_it, doc, "default")
        c = (d / "c.ass").read_text(encoding="utf-8")
        check("default_all：UI 照常画、不改字色、没有编号，底板 α 0x20",
              c.count("\nDialogue: 1,") == 5 and f"\\1c&H{TS.OFFMAIN_COLOR}&" not in c and f"\\1c&H{TS.UI_COLOR}&" not in c
              and "\nDialogue: 2," not in c and "\\1a&H20&" in c)
        # 画面边界：左锚的译文比原文长、字比画面剩下的宽 → 轴往里推，右边贴着画面边
        wide = OV.Item(0, 1_000_000, [[1800, 0, 1900, 30]], ["あ" * 10], "line", align="left", translated=True)
        render_overlay(d / "w.ass", [wide], doc, "default")
        wline = [x for x in (d / "w.ass").read_text(encoding="utf-8").splitlines() if x.startswith("Dialogue: 1,")][0]
        size = int(wline.split("\\fs")[1].split("}")[0].split("\\")[0])
        x = float(wline.split("\\pos(")[1].split(",")[0])
        check("左锚的译文伸出画面时把轴往里推，右沿贴着画面边", abs(x + 10 * size - (1920 - LY.OVERLAY_PAD)) <= 1, wline)
        own = OV.Item(0, 1_000_000, [[1800, 0, 1900, 30]], ["あ" * 10], "line", align="left")
        render_overlay(d / "o.ass", [own], doc, "default")
        oline = [x for x in (d / "o.ass").read_text(encoding="utf-8").splitlines() if x.startswith("Dialogue: 1,")][0]
        check("原文压回自己的框不保底：字号只按框定（框偏高时保底会把字撑出框）",
              int(oline.split("\\fs")[1].split("}")[0].split("\\")[0]) == 10, oline)
        # 预设入口：叠加预设缺 align 当场报错；render 不给 --preset 就是 default（有检验力的是文件名和退出码——
        # 两边进的是同一个函数）。scriptmatch --ass 与 render 的端到端比对没进守卫（要剧本），是手跑核的
        old = {**doc, "regions": [{"index": 0}, {"index": 1}]}
        check("叠加预设读到没有对齐判定的旧产物当场报错（提示重跑 build_tracks），不静默退回居中",
              raises(lambda: X.load("default", "preset").render(old, d / "x", {}), ValueError))
        tp = d / "t-tracks.json"
        tracksio.dump(tp, {k: v for k, v in doc.items() if k != "schema"})
        inked: list[int] = []

        def fake_ink(items):                                 # 记下每次真渲了几行：连跑时阶段 4 不该再渲
            inked.append(len(items))
            return [(48 * len(t), 30) for _f, _s, t in items]
        with patch.object(export, "render_ink", fake_ink):
            rc = R.main([str(tp), "--outdir", str(d / "r")])
            inked.clear()
            direct = X.load("default", "preset").render(tracksio.load(tp), d / "p", {})
            inked_direct = list(inked)
            named = X.load("default", "preset").render(tracksio.load(tp), d / "n", {"name": "out.subs"})
        check("render 不给 --preset 就是 default（写出 <tag>-default.ass，内容和直接调预设相同）",
              rc == 0 and (d / "r" / "t-default.ass").read_bytes() == direct[0].read_bytes())
        check("叠加预设返回 [最终 ASS, 字幕稿]（preview.py / scriptmatch --ass 靠这个）；name 给的就是最终文件名，不以 .ass 结尾也不改",
              [p.name for p in direct] == ["t-default.ass", "t-default.script.ass"]
              and [p.name for p in named] == ["out.subs", "out.subs.script.ass"] and all(p.is_file() for p in named))
        check("连跑时阶段 4 用阶段 3 量好的字宽，不再起 ffmpeg 渲一遍（一次预设调用只渲一次）", len(inked_direct) == 1, inked_direct)

    # ---- 效果的门 ----
    def item(**kw):
        return OV.Item(0, 3_000_000, [[0, 0, 100, 30]], ["あいう"], "line", events=[ev(0, [0, 0, 100, 30], t1=3_000_000, **kw)])
    raw = {"frame_us": 500_000, "meta": {"src_fps": 30.0}}
    fr = OV.native_frame_us(raw)
    check("效果·没回抠：采样级全字出现离首字 ≥ 2 个采样间隔才逐字显出（多读两个字骗出来的一个间隔不算）",
          OV.effect_times(item(t_full_sampled=1_000_000), raw, fr)[0] == 1_000_000
          and OV.effect_times(item(t_full_sampled=500_000), raw, fr)[0] == 0)
    whole = ev(0, [370, 925, 1586, 965], t0=0, t1=17_500_000, t_full_sampled=1_000_000)
    frag = ev(1, [398, 927, 584, 963], t0=14_000_000, t1=14_500_000, t_full_sampled=14_000_000, text="なると")
    line2 = ev(2, [666, 963, 1238, 1001], t0=1_000_000, t1=17_500_000, t_full_sampled=1_500_000)
    it_frag = OV.Item(0, 17_500_000, [whole["box"], line2["box"]], ["あいう", "えお"], "line", events=[whole, line2, frag])
    check("效果·碎片：det 切出来、框落在更早那行里的晚到事件不算时刻（gi-s2 50.5 s 那句被 64.5 s 的半截行拖成 15.5 s）；"
          "第二行框不在第一行里，照算",
          [e["id"] for e in OV.typing_events([whole, line2, frag])] == [0, 2]
          and OV.effect_times(it_frag, raw, fr)[0] == 1_500_000)
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()) as warned:
        slow = OV.slow_typewriters([(30.0, 0), (28.0, 1), (31.0, 2), (27.0, 3), (29.0, 4), (150.0, 5)])
    check("打字机偏慢：比中位数慢 3 倍的列出来并报警；条数不够算中位数就不报",
          slow == [(150.0, 5)] and "打字机偏慢 1 条" in warned.getvalue()
          and OV.slow_typewriters([(30.0, 0), (300.0, 1)]) == [])
    ok = {"first": "agree", "full": "agree"}                     # 回抠过的事件带 t_full_how
    refined = {**raw, "text_effect": {"samples": 20, "verdict": "instant", "iqr_over_median": 0.5}}
    unclear = {**raw, "text_effect": {"samples": 20, "verdict": "instant", "iqr_over_median": 2.63}}
    check("效果·回抠过：整段判 instant 且字确实没有生长就不逐字显出，改复刻淡入（最长 FADE_IN_MAX_US）；"
          "判打字机或没判定（样本不够）才逐字显出",
          OV.effect_times(item(t_full=1_000_000, t_full_how=ok), refined, fr)[:2] == (0, OV.FADE_IN_MAX_US)
          and OV.effect_times(item(t_full=200_000, t_full_how=ok), refined, fr)[:2] == (0, 200_000)
          and OV.effect_times(item(t_full=1_000_000, t_full_how=ok), {**raw, "text_effect": {"samples": 20, "verdict": "typewriter"}}, fr)[:2] == (1_000_000, 0)
          and OV.effect_times(item(t_full=1_000_000, t_full_how=ok), {**raw, "text_effect": {"samples": 3}}, fr)[0] == 1_000_000)
    check("效果·回抠过：全字出现离首字不到 2 个原生帧不算打字机、也不复刻淡入；淡出不足一帧不做",
          OV.effect_times(item(t_full=40_000, t_full_how=ok), {**raw, "text_effect": {"samples": 3}}, fr) == (0, 0, 0)
          and OV.effect_times(item(t_full=80_000, t_full_how=ok), {**raw, "text_effect": {"samples": 3}}, fr)[0] == 80_000
          and OV.effect_times(item(t_full_end=3_000_000 - 10_000), refined, fr)[2] == 0
          and OV.effect_times(item(t_full_end=2_000_000), refined, fr)[2] == 1_000_000)
    l1 = ev(0, [0, 0, 100, 30], t0=0, t1=5_000_000, t_full_end=4_800_000, t_end_how="curve")
    l2c = ev(1, [0, 40, 100, 70], t0=0, t1=5_500_000, t_full_end=5_500_000, t_end_how="ncc")
    l2o = ev(1, [0, 40, 100, 70], t0=0, t1=5_500_000, t_end_how="open")
    two = lambda second: OV.Item(0, 5_500_000, [l1["box"], second["box"]], ["一行", "二行"], "line", events=[l1, second])  # noqa: E731
    check("效果·多行条目里有开放边界的成员（没看到它消失）：整条不做淡出，不借别的行的 t_full_end 给整句淡出（Codex 复审反例：从 0 变成 700 ms）；"
          "都量到了照常按最晚的完全显示结束算",
          OV.effect_times(two(l2o), refined, fr)[2] == 0 and OV.effect_times(two(l2c), refined, fr)[2] == 0
          and OV.effect_times(OV.Item(0, 5_500_000, [l1["box"]], ["一行"], "line", events=[l1]), refined, fr)[2] == 700_000)
    check("效果·整段 instant 但离散度 > 1（判不清，gi-s2 回抠产物是 2.63）：按逐条的帧数门走，拿不准时跟着原文打字的节奏",
          OV.effect_times(item(t_full=1_000_000, t_full_how=ok), unclear, fr)[:2] == (1_000_000, 0)
          and OV.effect_times(item(t_full=40_000, t_full_how=ok), unclear, fr)[:2] == (0, 0))
    check("效果·只回抠了部分区域：没被回抠的事件（没有 t_full_how）照样走采样级的门，不因为整份有 text_effect 就被关掉",
          OV.effect_times(item(t_full_sampled=1_000_000), refined, fr)[0] == 1_000_000)
    tw_ok = {**raw, "text_effect": {"samples": 20, "verdict": "typewriter"}}
    check("效果·回抠只量到首字、全字没量出来（full_how none）时，全字时刻按采样间隔的门量（它是采样级精度），也不拿它复刻淡入",
          OV.effect_times(item(t_full=600_000, t_full_how={"first": "agree", "full": "none"}), tw_ok, fr)[:2] == (0, 0)
          and OV.effect_times(item(t_full=600_000, t_full_how={"first": "agree", "full": "agree"}), tw_ok, fr)[0] == 600_000)
    moving = item(t_full_sampled=1_000_000)
    moving.boxes = [[0, [0, 0, 100, 30]], [1_000_000, [50, 0, 150, 30]]]
    check("效果·在动的、名牌不做",
          OV.effect_times(moving, raw, fr) == (0, 0, 0)
          and OV.effect_times(replace(item(t_full_sampled=1_000_000), kind="name"), raw, fr) == (0, 0, 0))


def t_script_fx() -> None:
    """字幕稿 + 阶段 4（script-fx 计划）：可以反复重跑、restore 逐字节还原、别人的行不碰、人改过的以人为准、
    扩展效果、`fo` 编码、卡拉OK 取整不漂。"""
    print("字幕稿与阶段 4")
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr.output import export
    from flowocr.output import layout as LYT
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS

    # ---- Effect 里的 fo 参数与 ASS 读写 ----
    nasty = "a;b,c{d}\\e%f\ng"
    enc = AF.encode_fo({"why": nasty, "rows": True, "off": False, "key": None, "box": "1 2 3 4|5 6 7 8"})
    check("fo：值里的 ; , { } \\ % 换行按百分号编码，读回来相同；True 只写名字，False / None 不写；"
          "编码后没有逗号（Effect 不是最后一个字段）；数值列表用空格",
          AF.parse_fo(enc) == {"why": nasty, "rows": True, "box": "1 2 3 4|5 6 7 8"}
          and AF.parse_fo("  " + enc + " ") == AF.parse_fo(enc) and AF.parse_fo("Banner;10") is None
          and not any(c in enc for c in ",{}\\\n"), enc)
    check("fo：可以跟在别的 Effect 内容后面（备注、Banner;…），它之后到末尾都是参数；不在项的开头的 fo: 不算",
          AF.parse_fo("Banner;10;" + enc) == AF.parse_fo(enc) and AF.parse_fo("备注;fo:rows") == {"rows": True}
          and AF.parse_fo("xfo:rows") is None)
    raw_ass = ("\ufeff[Script Info]\r\nScriptType: v4.00+\r\n\r\n[Aegisub Project Garbage]\r\nActive Line: 3\r\n\r\n"
               "[Events]\r\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
               "Dialogue: 0,0:00:00.00,0:00:01.00,Body,,0,0,0,,带, 逗号\r\n")
    check("ASS 读写：BOM、CRLF、不认识的节（Aegisub 加的）原样；正文带逗号不串列",
          AF.loads(raw_ass).dumps() == raw_ass
          and AF.parse_row(AF.loads(raw_ass).section("Events").lines[1],
                           AF.format_of(AF.loads(raw_ass).section("Events")), AF.EVENT_KINDS).get("Text") == "带, 逗号")

    # ---- 打字机：tw 匀速逐字；手写的 \k 优先 ----
    long = [t[2] for t in TS.with_tw(TS.text_rows("一" * 40)[0], 3333, False)[0] if t[0] == "ch"]
    check("tw：40 个字、3.333 s，第 k 个字在 tw·k//39 毫秒出现（和拆之前逐字 \\alpha 的算法逐毫秒相同），末字正好在 tw",
          long == [3333 * k // 39 for k in range(40)])
    nl = [t[2] for t in TS.with_tw([TS.rows_joined(TS.text_rows("一二\\N三")[0])], 300, True)[0] if t[0] == "ch"]
    check("tw：整块画在一条里的几行，行间的换行也占一个位置（tracks 事件本身带的换行，和拆之前一样）", nl == [0, 100, 300], nl)
    hand = TS.text_rows("{\\k20}我{\\k30}们")
    check("手写的 \\k：按人写的节奏（音节起点），全字出现 = 最后一个音节的起点",
          [t[2] for t in hand[0][0] if t[0] == "ch"] == [0, 200] and hand[3] == 200)

    # ---- 一份字幕稿 ----
    def ev(i, box, **kw):
        return {"id": i, "box": box, "t_start": 0, "t_end": 5_000_000, "region": 0, "text": "x", **kw}
    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    it_a = SC.Item(0, 5_000_000, [[100, 900, 300, 930], [100, 940, 250, 970]], ["第一行台词", "第二行"], "line",
                   events=[ev(1, [100, 900, 300, 930], t_full_sampled=1_500_000), ev(1, [100, 900, 300, 930])],
                   region=0, align="left", translated=True, layer="body", key="k;1")
    it_b = SC.Item(1_000_000, 4_000_000, [[800, 500, 1100, 530]], ["UI文字"], "line",
                   events=[ev(2, [800, 500, 1100, 530])], region=1, ui=True, main=False)
    it_c = SC.Item(0, 1_000_000, [[0, 100, 100, 130]], ["走"], "line", events=[ev(3, [0, 100, 100, 130])], region=2,
                   boxes=[[0, [0, 100, 100, 130]], [1_000_000, [100, 100, 200, 130]]])
    it_d = SC.Item(0, 3_000_000, [[200, 200, 800, 400]], ["第一段文字比较长需要折行" * 3, "第二段"], "block",
                   region=3, translated=True, row_h=30, layer="panel")
    it_e = SC.Item(6_000_000, 8_000_000, [[100, 900, 700, 930]], ["上一句", "下一句"], "line",
                   events=[ev(5, [100, 900, 700, 930])], region=0, translated=True, layer="body")
    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        d = Path(td)
        draft = d / "x.script.ass"
        SC.export_script(draft, [it_a, it_b, it_c, it_d, it_e], doc)
        text = draft.read_text(encoding="utf-8")
        src = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
        check("字幕稿：一个条目一行源行，Style 按角色、Actor 是区域号、Effect 是 fo 参数（没有逗号，字段不串）；"
              "正文是一个 tag 块 + 纯文字（多行用 \\N）；打字机记成 tw，事件号去重",
              len(src) == 5 and [s.split(",")[3] for s in src] == ["Body", "UI", "Body", "Panel", "Body"]
              and [s.split(",")[4] for s in src] == ["r0", "r1", "r2", "r3", "r0"]
              and all(s.split(",", 9)[8].startswith("fo:") and s.split(",", 9)[9].startswith("{\\an")
                      and "fo:" not in s.split(",", 9)[9] for s in src)
              and src[0].split(",", 9)[9].endswith("}第一行台词\\N第二行") and ",fo:ev=1;key=k%3B1;" in src[0]
              and ";tw=1500;" in src[0] and "\\k" not in src[0] and ";off;" in src[1] and "orig" not in text
              and "FlowOCR Script: 1" in text and "Style: Body," in text and ",3,6,0,5," in text, src[:2])

        def run(txt: str, look: str = "default", fx=(), unknown: str = "error", **kw):
            ts = TS.Typesetter(replace(TS.LOOKS[look], **kw), TS.load_fx(fx), unknown)
            doc_ = AF.loads(txt)
            ts.run(doc_)
            return doc_.dumps(), ts

        def gen(txt: str, actor: str):
            return [ln for ln in txt.splitlines() if ln.startswith("Dialogue:") and ",fo-fx," in ln
                    and ln.split(",")[4] == actor]

        final, ts = run(text)
        again, _ = run(final)
        rest = AF.loads(final)
        TS.restore(rest)
        check("阶段 4 可以反复重跑：跑在自己的输出上逐字节相同；restore 还原出逐字节相同的字幕稿",
              again == final and rest.dumps() == text)
        check("生成行：派生样式 <Style>.fo-fx（BorderStyle 1、描边 2）、Effect fo-fx；源行改成 Comment、Effect 前面加 fo-src|",
              "Style: Body.fo-fx," in final and ",1,2,0,5," in final.split("Style: Body.fo-fx,")[1].split("\n")[0]
              and all(ln.startswith("Comment:") for ln in final.splitlines() if ",fo-src|" in ln)
              and sum(",fo-src|fo:" in ln for ln in final.splitlines()) == 5)
        check("生成行：打字机换成逐字 \\alpha（\\ko 不进最终文件）；多行按原框逐行摆",
              "\\ko" not in "".join(ln for ln in final.splitlines() if ",fo-fx," in ln)
              and len([x for x in gen(final, "r0") if x.startswith("Dialogue: 1,") and "0:00:00.00" in x]) == 2)
        e_rows = [x for x in gen(final, "r0") if x.startswith("Dialogue: 1,") and "0:00:06.00" in x]
        check("一个原文框、译文两行：按原来一行的高度往下扩出一行、逐行摆，两行都画（原来按框 zip 只画第一行）",
              len(e_rows) == 2 and "\\pos(400,915)\\fs40}上一句" in e_rows[0] and "\\pos(400,945)\\fs40}下一句" in e_rows[1],
              e_rows)
        panel = gen(final, "r3")
        check("面板：一整块底板 + 按框折行的正文（\\q2），不做效果",
              len(panel) == 2 and panel[0].startswith("Dialogue: 0,") and "\\q2" in panel[1] and "\\N" in panel[1], panel)

        # ---- 别人的行不碰 ----
        foreign = ["Dialogue: 0,0:00:01.00,0:00:02.00,Body,,0,0,0,fx,{\\pos(1,1)}别人的特效行",
                   "Comment: 0,0:00:01.00,0:00:02.00,Body,,0,0,0,,用户自己停用的行",
                   "Comment: 0,0:00:01.00,0:00:02.00,Body,r0,0,0,0,fo:box=0 0 10 10;plate,用户自己停用的字幕稿行",
                   "Dialogue: 0,0:00:01.00,0:00:02.00,Body,,0,0,0,Banner;10,横幅"]
        with_foreign = text.rstrip("\n") + "\n" + "\n".join(foreign) + "\n"
        f2, ts2 = run(with_foreign)
        r2 = AF.loads(f2)
        TS.restore(r2)
        check("别人的 fx 行、用户注释掉的行（带不带 fo 参数都一样）、Banner 效果行：阶段 4 原样留着、不生成，restore 后也原样",
              all(x in f2.splitlines() for x in foreign) and r2.dumps() == with_foreign
              and sum("不是源行" in m for _, m in ts2.notes) == 2)

        noted = text.replace(src[1], src[1].replace(",fo:", ",备注;fo:", 1))
        f3, _ = run(noted)
        r3 = AF.loads(f3)
        TS.restore(r3)
        check("Effect 里 fo: 前面有别的内容：照样当源行生成（和没有时生成的一样），前面那段留在源行上、restore 逐字节还原",
              gen(f3, "r1") == gen(final, "r1") and r3.dumps() == noted
              and any(",fo-src|备注;fo:" in ln for ln in f3.splitlines()))

        # ---- 人改过的 ----
        a_line, e_line = src[0], src[4]
        a_fs = __import__("re").search(r"\\fs(\d+)", a_line)[1]
        a_pos = __import__("re").search(r"\\pos\(([^)]*)\)", a_line)[1]
        e_fs = __import__("re").search(r"\\fs(\d+)", e_line)[1]
        longer, _ = run(text.replace(e_line, e_line.replace("上一句", "上一句" + "很" * 40)))
        l_rows = [x for x in gen(longer, "r0") if x.startswith("Dialogue: 1,") and "0:00:06.00" in x]
        l_plate = [x for x in gen(longer, "r0") if x.startswith("Dialogue: 0,") and "0:00:06.00" in x]
        check("改了译文：字号重新适配（字多了，变小），底板跟着译文变宽",
              l_rows and f"\\fs{e_fs}" not in l_rows[0] and l_plate
              and float(l_plate[0].split(" l ")[1].split()[0]) > 700 - 100, (e_fs, l_rows[:1], l_plate[:1]))
        resized, ts_r = run(text.replace(a_line, a_line.replace(f"\\fs{a_fs}", "\\fs55", 1)))
        check("手改了 \\fs：以人为准、不再适配，打提示",
              all("\\fs55" in x for x in gen(resized, "r0") if x.startswith("Dialogue: 1,") and "0:00:00.00" in x)
              and any("字号改过" in m for _, m in ts_r.notes))
        moved, ts_m = run(text.replace(a_line, a_line.replace(f"\\pos({a_pos})", "\\pos(960,540)", 1)))
        m_rows = [x for x in gen(moved, "r0") if x.startswith("Dialogue: 1,") and "0:00:00.00" in x]
        check("拖了位置：以人为准，这一行整块画在人给的位置（不再按原框逐行摆），打提示",
              len(m_rows) == 1 and "\\pos(960,540)" in m_rows[0] and "\\N" in m_rows[0]
              and any("位置改过" in m for _, m in ts_m.notes), m_rows)
        noplate, _ = run(text.replace(src[1], src[1].replace(";plate;", ";", 1)))
        check("删掉 plate：这一行没有底板", not [x for x in gen(noplate, "r1") if x.startswith("Dialogue: 0,")]
              and [x for x in gen(final, "r1") if x.startswith("Dialogue: 0,")])
        hand = "Dialogue: 0,0:00:09.00,0:00:10.00,Body,,0,0,0,,{\\pos(10,10)}手补"
        handed, _ = run(text.rstrip("\n") + "\n" + hand + "\n")
        check("手补一行（没有 fo）：照主流 tag 原样生成一条，只换派生样式",
              "Dialogue: 0,0:00:09.00,0:00:10.00,Body.fo-fx,,0,0,0,fo-fx,{\\pos(10,10)}手补" in handed.splitlines())
        split_b = src[1].replace("0:00:01.00,0:00:04.00", "0:00:01.00,0:00:02.00") + "\n" + \
            src[1].replace("0:00:01.00,0:00:04.00", "0:00:02.00,0:00:04.00")
        splitted, _ = run(text.replace(src[1], split_b))
        check("一行拆成前后两条：各自按自己的起止渲染，底板都盖同一片原文",
              len([x for x in gen(splitted, "r1") if x.startswith("Dialogue: 0,")]) == 2
              and len({x.split("}")[0].split("\\pos")[1] for x in gen(splitted, "r1") if x.startswith("Dialogue: 0,")}) == 1)
        three = text.replace(src[0], src[0] + "\\N第三行")
        mis, ts_x = run(three)
        m_rows = [x for x in gen(mis, "r0") if x.startswith("Dialogue: 1,") and "0:00:00.00" in x]
        check("两个原文框、正文加到三行：按行距往下扩出一行、逐行摆，打提示（owner 2026-09-27：扩出的行压到别的字不管）",
              len(m_rows) == 3 and __import__("re").sub(r"\{[^}]*\}", "", m_rows[2]).endswith(",第三行")
              and "\\pos(100,995)" in m_rows[2] and any("扩出 1 行" in m for _, m in ts_x.notes), m_rows)
        check("扩出的行往下会出画面就往上扩；横向取原文几行合起来的范围",
              LYT.expand_rows([[100, 1040, 700, 1070]], 1, 1080) == [[100, 1010, 700, 1040], [100, 1040, 700, 1070]]
              and LYT.expand_rows([[100, 900, 300, 930], [100, 940, 250, 970]], 1, 1080)[2] == [100, 980, 300, 1010])
        shifted = text.replace(src[2], src[2].replace("0:00:00.00,0:00:01.00", "0:00:00.50,0:00:01.50"))
        s_rows = [x for x in gen(run(shifted)[0], "r2") if x.startswith("Dialogue: 1,")]
        check("平移了在动的行的时间：轨迹按视频时刻走（0.5 s 时已经走到一半），走完之后停在终点",
              len(s_rows) == 2 and "\\move(100,115,150,115,0,500)" in s_rows[0] and "\\pos(150,115)" in s_rows[1], s_rows)
        unknown = text.replace(src[1], src[1].replace(";plate;", ";plate;foo=1;", 1))
        check("不认识的 fo 参数当场报错（列出行号）；unknown=ignore 才跳过并提示",
              raises(lambda: run(unknown), ValueError)
              and any("foo" in m for _, m in run(unknown, unknown="ignore")[1].notes))
        framed = text.replace(src[1], src[1].replace(";plate;", ";plate;frame=0000FF;", 1))
        fx_out, _ = run(framed, fx=(str(ROOT / "examples" / "fx_minimal.py"),))
        check("扩展效果（examples/fx_minimal.py）：带 frame 参数的行多一个空心框元素，其余行不受影响",
              sum("\\3c&H0000FF&" in x for x in gen(fx_out, "r1")) == 1
              and gen(fx_out, "r0") == gen(final, "r0"))
        dev, _ = run(text, "dev")
        check("dev：UI 黄字、非主轨浅蓝按 Style 和 off 画；区域号取 Actor",
              any(f"\\1c&H{TS.UI_COLOR}&" in x for x in gen(dev, "r1"))
              and any(x.startswith("Dialogue: 2,") and x.endswith("}1ui") for x in gen(dev, "r1")))
        wrapped = text.replace("第二段", "第二段改了")
        check("面板改了译文：按框重新折行", "改了" in "".join(gen(run(wrapped)[0], "r3")))

        # ---- 审查修的几处（Claude Code 自审 2026-09-27）----
        hr = TS.text_rows("你\\h好")
        hb = TS.body_of(TS.with_tw(hr[0], 200, False)[0], "on")
        check("硬空格 \\h 是一个字：逐字 \\alpha 不插进它中间；纯文字里换成不断行空格（量字宽、折行用）",
              hr[1] == ["你 好"] and "}\\h" in hb and "\\{" not in hb and hb.count("\\t(") == 3, hb)
        bl = next(ln for ln in text.splitlines() if ln.startswith("Style: Body,"))
        styled = text.replace(bl, bl.replace("Style: Body,", "Style: Sign.fx,") + "\n" + bl)
        s_final, _ = run(styled)
        s_rest = AF.loads(s_final)
        TS.restore(s_rest)
        check("用户自己起的 Sign.fx 样式不被当成派生样式删掉（派生样式后缀是 .fo-fx）",
              "Style: Sign.fx," in s_final and s_rest.dumps() == styled)
        a_no = text.splitlines().index(src[0]) + 1
        bad_tw = text.replace(src[0], src[0].replace(";tw=1500;", ";tw=1.5s;"))
        try:
            run(bad_tw)
            bad_msg = ""
        except ValueError as e:
            bad_msg = str(e)
        check("fo 参数的值写坏了（tw=1.5s）：报错并指出是第几行", f"第 {a_no} 行" in bad_msg, bad_msg)
        fl = final.splitlines()
        k_src = next(k for k, ln in enumerate(fl) if ",fo-src|" in ln and f"\\pos({a_pos})" in ln)
        fl[k_src] = fl[k_src].replace(f"\\pos({a_pos})", "\\pos(960,540)")
        _, ts_f = run("\n".join(fl))
        check("跑在阶段 4 的输出上时，提示的行号是这份文件里源行的行号（不是 restore 之后的）",
              (k_src + 1, "位置改过：以人为准，这一行不按原框逐行摆、不按轨迹走") in ts_f.notes,
              (k_src + 1, ts_f.notes[:3]))
        (d / "y.script.ass").write_text(text, encoding="utf-8")
        (d / "y.ass").write_text(final, encoding="utf-8")
        check("--restore 默认不覆盖已有的字幕稿（可能改过）；字幕稿本身不能拿来 restore；--force 才覆盖，还原出逐字节相同的",
              raises(lambda: TS.restore_file(d / "y.ass"), FileExistsError)
              and raises(lambda: TS.restore_file(d / "y.script.ass"), ValueError)
              and TS.restore_file(d / "y.ass", force=True)[0].read_text(encoding="utf-8") == text)
        check("默认输出名：x.script.ass → x.ass，其余 x.ass → x.fx.ass",
              TS.default_out(Path("a.script.ass")).name == "a.ass" and TS.default_out(Path("a.ass")).name == "a.fx.ass")
        from flowocr.typeset import __main__ as TM
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc_cli = TM.main([str(d / "y.script.ass"), "-o", str(d / "cli.ass")])
            rc_rst = TM.main([str(d / "cli.ass"), "--restore", "-o", str(d / "cli.script.ass")])
        # ---- Codex 复审（2026-09-27）与 owner 改字幕稿撞到的 ----
        e_line = src[4]
        inl = text.replace(e_line, e_line.replace("上一句", "上{\\t(0,1000,\\fs80)}一句").replace("下一句", "{\\fs80}下一句"))
        inl_out, ts_i = run(inl)
        e_rows = [x for x in gen(inl_out, "r0") if x.startswith("Dialogue: 1,") and "0:00:06.00" in x]
        check("行内的 \\fs80、\\t(…,\\fs80) 原样留着（只剥行首块里归阶段 4 管的）；行内的字号不算人改了整行字号",
              len(e_rows) == 2 and "上{\\t(0,1000,\\fs80)}一句" in e_rows[0] and "{\\fs80}下一句" in e_rows[1]
              and not any("字号改过" in m for _, m in ts_i.notes), e_rows)
        realn, ts_a = run(text.replace(a_line, a_line.replace("{\\an4\\pos(", "{\\an7\\pos(", 1)))
        r_rows = [x for x in gen(realn, "r0") if x.startswith("Dialogue: 1,") and "0:00:00.00" in x]
        check("改了对齐（\\an4 → \\an7）：以人为准，照人给的锚点和位置画、打提示（不再被改回 \\an4/5/6）",
              len(r_rows) == 1 and f"{{\\an7\\pos({a_pos})" in r_rows[0]
              and any("对齐改过" in m for _, m in ts_a.notes), r_rows)
        colored, _ = run(text.replace(a_line, a_line.replace("第一行台词", "第一行{\\1c&H0000FF&}台词", 1)))
        c_rows = [x for x in gen(colored, "r0") if x.startswith("Dialogue: 1,") and "0:00:00.00" in x]
        check("按原框逐行摆拆成几条时，上一行的行内样式（颜色）带到下一行开头",
              len(c_rows) == 2 and "}{\\1c&H0000FF&}" in c_rows[1] and "\\1c&H0000FF&" in c_rows[0], c_rows)
        c_line = src[2].replace("0:00:00.00,0:00:01.00", "0:00:00.50,0:00:01.50")
        c_line = c_line.replace(",{\\an5", ",{\\an5\\fad(100,100)", 1).replace(";was=", ";tw=800;was=", 1)
        c_line = c_line[:-1] + "走路啊"                               # 三个字：0 / 400 / 800 ms 出现
        mv_rows = [x for x in gen(run(text.replace(src[2], c_line))[0], "r2") if x.startswith("Dialogue: 1,")]
        check("在动的行按轨迹拆成几段：淡入淡出和逐字显出按源行时间算，不在每段从头放（段接段连起来是一整条的样子）",
              len(mv_rows) == 2 and "\\fade(255,0,255,0,100,900,1000)" in mv_rows[0] and "\\t(400,401," in mv_rows[0]
              and "\\fade(0,0,255,0,0,400,500)" in mv_rows[1] and "\\fad(" not in "".join(mv_rows)
              and "\\t(300,301," in mv_rows[1] and "\\t(400" not in mv_rows[1], mv_rows)
        p_line = src[3]
        p_pos = __import__("re").search(r"\\pos\(([^)]*)\)", p_line)[1]
        p_moved = p_line.replace(f"\\pos({p_pos})", "\\move(200,200,300,200)", 1).replace(";plate=box;", ";plate=box;frame;", 1)
        p_out, _ = run(text.replace(p_line, p_moved), fx=(str(ROOT / "examples" / "fx_minimal.py"),))
        p_gen = gen(p_out, "r3")
        check("面板也走公共流程：人把 \\pos 改成 \\move 时照 \\move 画（不是 \\pos(0,0)），扩展效果的挂点也调",
              any("\\move(200,200,300,200)" in x for x in p_gen) and not any("\\pos(0,0)" in x for x in p_gen)
              and any("\\3c&H" in x for x in p_gen), p_gen)
        check("一个原文框、正文两行：字幕稿（阶段 3）按同一规则扩行，预览的字号和位置跟阶段 4 一致（一行的字号、两行的中心）",
              all("\\fs40}" in r for r in e_rows) and "|fs:40|" in e_line and "\\pos(400,930)" in e_line, (e_rows, e_line[-120:]))
        wide_b = text.replace(src[1], src[1].replace("}UI文字", "}U{\\fs80}I文字", 1))
        b_plate = [x for x in gen(run(wide_b)[0], "r1") if x.startswith("Dialogue: 0,")][0]
        b_w = float(b_plate.split(" l ")[1].split()[0])
        check("行内 \\fs80 的字按 80 号量宽：底板跟着画出来的字宽（3 个字 × 80 超出原文框 300 宽）",
              b_w >= 3 * 80 + 2 * LYT.OVERLAY_PAD, (b_w, b_plate))
        t_line = src[2].replace("0:00:00.00,0:00:01.00", "0:00:00.50,0:00:01.50")
        t_line = t_line.replace(",{\\an5", ",{\\an5\\t(0,500,\\1c&H0000FF&)", 1)
        t_rows = [x for x in gen(run(text.replace(src[2], t_line))[0], "r2") if x.startswith("Dialogue: 1,")]
        check("人写的 \\t 在轨迹分段后按源行时间接着算，不每段重播：第二段从 500 ms 起时已经变完，换成里面的 tag 本身"
              "（写成 \\t(-500,0,…) 会被 libass 读成到段尾才变完）；结束时刻写 0 的是到源行尾",
              len(t_rows) == 2 and "\\t(0,500,\\1c&H0000FF&)" in t_rows[0]
              and "\\1c&H0000FF&" in t_rows[1] and "\\t(" not in t_rows[1]
              and TS.shift_times("\\t(0,0,\\frz30)", 500, 1500) == "\\t(-500,1000,\\frz30)", t_rows)
        blur_py = d / "fx_blur.py"
        blur_py.write_text("NAME = 'blur'\n\ndef tags(line, value, el):\n    if el.kind == 'text':\n"
                           "        el.body = '{\\\\blur3}' + el.body\n", encoding="utf-8")
        bl_line = src[2].replace("0:00:00.00,0:00:01.00", "0:00:00.50,0:00:01.50").replace(";rows;", ";rows;blur;", 1)
        bl_rows = [x for x in gen(run(text.replace(src[2], bl_line), fx=(str(blur_py),))[0], "r2") if x.startswith("Dialogue: 1,")]
        check("扩展效果的 tags 挂点在轨迹分段之后调：每一段都有它改的东西（原来分段重算正文把它冲掉）",
              len(bl_rows) == 2 and all("}{\\blur3}" in r for r in bl_rows), bl_rows)
        short, _ = run(text.replace(a_line, a_line.replace("}第一行台词\\N第二行", "}台词", 1)))
        s_rows = [x for x in gen(short, "r0") if x.startswith("Dialogue: 1,") and "0:00:00.00" in x]
        check("两个原文框、正文并成一行：字号按原来一行的高度适配，不放大到两行那么高",
              len(s_rows) == 1 and "\\fs47" in s_rows[0], s_rows)
        it_f = SC.Item(0, 1_000_000, [[100, 1040, 700, 1070]], ["上", "下"], "line", events=[ev(6, [100, 1040, 700, 1070])],
                       region=5, translated=True, boxes=[[0, [100, 1040, 700, 1070]], [1_000_000, [200, 1040, 800, 1070]]])
        it_g = SC.Item(0, 1_000_000, [[100, 500, 700, 560]], ["一行\n二行"], "line", events=[ev(7, [100, 500, 700, 560])],
                       region=6)
        SC.export_script(d / "z.script.ass", [it_f, it_g], doc)
        z = (d / "z.script.ass").read_text(encoding="utf-8")
        f_rows = [x for x in gen(run(z)[0], "r5") if x.startswith("Dialogue: 1,")]
        check("贴底的框扩出的行往上扩；在动的行轨迹照原文框走（基准不跟着扩行挪），两行中心 1025 / 1055、都在画面里",
              len(f_rows) == 2 and __import__("re").search(r"\\move\(\d+,1025,\d+,1025,", f_rows[0])
              and __import__("re").search(r"\\move\(\d+,1055,\d+,1055,", f_rows[1]), f_rows)
        g_line = next(ln for ln in z.splitlines() if ",r6," in ln)
        g_out, ts_g = run(z.replace(g_line, g_line + "\\N三行"))
        g_fs = __import__("re").search(r"\\fs(\d+)", g_line)[1]
        g_rows = [x for x in gen(g_out, "r6") if x.startswith("Dialogue: 1,")]
        check("一个原文框本来装着两行（cap=2）、正文加到三行：框按一行的高度长出一行，字号不变，打提示",
              ";cap=2;" in g_line and len(g_rows) == 1 and __import__("re").search(rf"\\fs{g_fs}\D", g_rows[0])
              and any("扩出 1 行" in m for _, m in ts_g.notes), (g_fs, g_rows))
        check("命令行：出最终 ASS 和直接调一样；--restore -o 另写一份字幕稿",
              rc_cli == 0 and (d / "cli.ass").read_text(encoding="utf-8") == final
              and rc_rst == 0 and (d / "cli.script.ass").read_text(encoding="utf-8") == text)
