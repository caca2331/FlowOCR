"""阶段 3（导出、叠加、扩展）的守卫。"""
from __future__ import annotations

import importlib  # noqa: F401
import inspect  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

import re  # noqa: E402,F401
from guards._common import *  # noqa: F401,F403


def _disp(fill: str = "on", **tiers: str) -> dict:
    """匹配产物夹具的 `display`（显示协议）：`组名=档位`；两个保留组没给就是 `off`。"""
    tiers = {"unmatched": "off", "unfed": "off", **tiers}
    return {"groups": {g: {"tier": t, "label": g} for g, t in tiers.items()}, "fill": {"tier": fill}}


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
          callable(X.load("default_srt", "preset").render) and callable(X.load("gametext", "matcher").match)
          and callable(X.load("gametext", "matcher").cluster_patch))
    check("内置名单列得出来", "default_srt" in X.builtin_names("preset") and "srt_main" not in X.builtin_names("preset")
          and "gametext" in X.builtin_names("matcher"))
    ex_p, ex_m = ROOT / "examples" / "preset_minimal.py", ROOT / "examples" / "matcher_minimal.py"
    check("examples 里的两个例子按文件路径加载得到", callable(X.load(str(ex_p), "preset").render)
          and callable(X.load(str(ex_m), "matcher").match) and callable(X.load(str(ex_m), "matcher").cluster_patch))
    check("点分模块名和内置名加载到同一个模块",
          X.load("flowocr.output.presets.default_srt", "preset") is X.load("default_srt", "preset"))
    check("没有的内置名报 LookupError（列出有的）", raises(lambda: X.load("nope", "preset"), LookupError))
    check("种类打错报 ValueError", raises(lambda: X.load("default_srt", "presets"), ValueError))
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
        nn = X.load("naninovel", "matcher")
        a_nn = BT.apply_matcher_patch(BT.make_parser(), ["o", "--outdir", "d", "--tag", "f1", "--matcher", "naninovel",
                                                          "--matcher-ctx", "game=mocai"])
        a_nn0 = BT.apply_matcher_patch(BT.make_parser(), ["o", "--outdir", "d", "--tag", "f1", "--matcher", "naninovel"])
        check("naninovel 的补丁：《魔裁》（game=mocai）打开按窗并带；不给游戏、别的游戏不改默认（owner 2026-10-06）",
              nn.cluster_patch({"game": "mocai"}) == {"main_band_unit": "window"}
              and nn.cluster_patch({"tag": "f1"}) == {} and nn.cluster_patch({"game": "other"}) == {}
              and a_nn.main_band_unit == "window" and a_nn0.main_band_unit == "region"
              and nn.cluster_patch({"game": "mocai"}) is not nn.PATCHES["mocai"])

        # 最小的一份 tracks 产物：一条区域轨、两条事件（一条是这条轨判的常驻 UI）
        doc = {"meta": {}, "frame_us": 500_000, "size": [1920, 1080],
               "provenance": {"main_track": "r00", "main_srt": "t-region00-subtitle.srt"},
               "regions": [{"index": 0}],
               "events": [{"id": 0, "region": 0, "t_start": 0, "t_end": 1_000_000, "text": "台词", "flags": [], "box": [100, 900, 300, 940]},
                          {"id": 1, "region": 0, "t_start": 0, "t_end": 1_000_000, "text": "UI", "flags": [], "box": [1500, 50, 1600, 80]}],
               "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                           "srt": "t-region00-subtitle.srt", "ui_lines": ["UI"],
                           "cues": [{"id": "c0", "t_start": 0, "t_end": 1_000_000, "events": [0, 1]}]}]}
        tp = d / "t-tracks.json"
        doc = tracksio.dump(tp, doc)
        want = d / "want.srt"
        srtio.write_srt_blocks(want, [(0, 1_000_000, ["台词"])])
        got = X.load("default_srt", "preset").render(doc, d / "p1", {})
        check("default_srt：主轨、UI 行剔掉；默认文件名和阶段 2 的主轨 SRT 不同（渲到建轨目录不会把它换掉）；没有对齐判定的产物照常出",
              [p.name for p in got] == ["tracks-default.srt"] and got[0].name != doc["provenance"]["main_srt"]
              and got[0].read_bytes() == want.read_bytes(), [p.name for p in got])
        got2 = X.load("default_srt", "preset").render(doc, d / "p2", {"name": "m.srt"})
        check("default_srt 的 name 选项改文件名", [p.name for p in got2] == ["m.srt"] and got2[0].read_bytes() == want.read_bytes())
        check("没有主轨的产物 default_srt 明确报错，不静默写空文件；不认识的选项报错",
              raises(lambda: X.load("default_srt", "preset").render({**doc, "provenance": {}}, d / "p3", {}), ValueError)
              and raises(lambda: X.load("default_srt", "preset").render(doc, d / "p3", {"srt": "on"}), ValueError))
        ex_s = ROOT / "examples" / "preset_srt_oneline.py"
        got3 = X.load(str(ex_s), "preset").render(doc, d / "p7", {})
        check("examples/preset_srt_oneline.py：换 srt_blocks 的 text 规则（一条 cue 拼成一行）",
              got3 and got3[0].read_text(encoding="utf-8").count("台词") == 1)
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
        # 匹配器推荐预设（owner 2026-10-06，matcher-next 计划方案 A）：声明了就写进产物，render 不给 --preset 时照用
        rm = d / "recmatcher.py"
        rm.write_text(f"from importlib import util as _u\n_s = _u.spec_from_file_location('mm', {str(ex_m)!r})\n"
                      "_m = _u.module_from_spec(_s); _s.loader.exec_module(_m)\nmatch = _m.match\n"
                      f"PRESET = {str(up)!r}\nPRESET_OPTS = {{'k': 'v'}}\n", encoding="utf-8")
        mp2 = d / "m2.json"
        rc3 = M.main([str(tp), "--matcher", str(rm), "--out", str(mp2), "--opt", f"table={d / 'table.json'}"])
        from flowocr.provenance import portable as _portable
        rec = json.loads(mp2.read_text(encoding="utf-8")).get("provenance", {}).get("recommend")
        rc4 = R.main([str(mp2), "--outdir", str(d / "p8")])
        check("匹配器声明了 PRESET：产物 provenance.recommend 记下预设、选项和匹配器；render 不给 --preset 就用它；没声明的不记",
              rc3 == 0 and rec == {"preset": _portable(str(up)), "opts": {"k": "v"}, "by": _portable(str(rm))}
              and rc4 == 0 and (d / "p8" / "user.txt").read_text(encoding="utf-8") == "原文"
              and "recommend" not in (json.loads(mp.read_text(encoding="utf-8")).get("provenance") or {}))
        check("显式给的 --preset 优先于推荐（推荐的预设只收 example-matched/1，显式给 matched_srt 就按 matched_srt 核 schema）",
              raises(lambda: R.main([str(mp2), "--preset", "matched_srt", "--outdir", str(d / "p9")]), SystemExit))
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            up2 = d / "mypreset2.py"
            up2.write_text(up.read_text(encoding="utf-8"), encoding="utf-8")
            R.main([str(mp2), "--preset", str(up2), "--outdir", str(d / "p10")])
        check("显式给了别的预设：照用，只提示一句匹配器推荐的是哪个（owner 2026-10-06）", "推荐的是预设" in buf.getvalue(), buf.getvalue())
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            R.main([str(mp2), "--preset", str(up), "--outdir", str(d / "p12")])
        check("显式给的就是推荐的那个（绝对路径写法）：不提示", "推荐的是预设" not in buf2.getvalue(), buf2.getvalue())
        po = d / "optpreset.py"
        po.write_text('ACCEPTS = ("example-matched/1",)\n\n\ndef render(document, output_dir, options=None):\n'
                      '    import json\n    from pathlib import Path\n    p = Path(output_dir)\n    p.mkdir(parents=True, exist_ok=True)\n'
                      '    f = p / "opts.json"\n    f.write_text(json.dumps(options, sort_keys=True), encoding="utf-8")\n    return [f]\n', encoding="utf-8")
        rm2 = d / "recmatcher2.py"
        rm2.write_text(rm.read_text(encoding="utf-8").replace(repr(str(up)), repr(str(po))).replace("{'k': 'v'}", "{'k': 'v', 'j': '1'}"),
                       encoding="utf-8")
        mp3 = d / "m3.json"
        M.main([str(tp), "--matcher", str(rm2), "--out", str(mp3), "--opt", f"table={d / 'table.json'}"])
        R.main([str(mp3), "--outdir", str(d / "p11"), "--opt", "k=w"])
        sub = d / "sub"
        sub.mkdir()
        (sub / "relmatcher.py").write_text("def match(document, context, options=None):\n    return {}\nPRESET = 'mypreset.py'\n", encoding="utf-8")
        (sub / "mypreset.py").write_text(up.read_text(encoding="utf-8"), encoding="utf-8")
        rrec = X.recommendation(X.load(str(sub / "relmatcher.py"), "matcher"), str(sub / "relmatcher.py"))
        check("推荐的预设写成相对 .py：按匹配器文件所在目录解析成固定路径再记（不随渲染时的 cwd 漂）",
              rrec["preset"] == _portable(str((sub / "mypreset.py").resolve())), rrec)
        import os
        from flowocr import paths as _paths
        cwd0 = os.getcwd()
        decoy = d / "decoy"
        (decoy / "plugins").mkdir(parents=True)
        (decoy / "plugins" / "p.py").write_text("x = 1\n", encoding="utf-8")
        try:
            os.chdir(decoy)
            rp = R.recommended_path("plugins/p.py")
        finally:
            os.chdir(cwd0)
        check("推荐里相对数据根的 .py 按数据根解析，不看 cwd（cwd 下有同名文件也不加载它）",
              rp == _paths.data_root() / "plugins" / "p.py" and R.recommended_path("~/x/p.py") == Path.home() / "x" / "p.py", rp)
        check("推荐的选项和命令行 --opt 同名时命令行的优先，其余推荐的选项照给",
              json.loads((d / "p11" / "opts.json").read_text(encoding="utf-8")) == {"j": "1", "k": "w"})
        check("内置推荐：naninovel 推荐 matched_srt + lang=cn；gametext 不声明（不给 --preset 就是 default）",
              X.recommendation(X.load("naninovel", "matcher"), "naninovel") == {"preset": "matched_srt", "opts": {"lang": "cn"}, "by": "naninovel"}
              and X.recommendation(X.load("gametext", "matcher"), "gametext") is None)
        check("预设声明了 ACCEPTS 就核 schema：喂错产物当场报错，不在预设内部炸成 KeyError",
              raises(lambda: R.main([str(tp), "--preset", str(up), "--outdir", str(d / "p6")]), SystemExit)
              and raises(lambda: R.main([str(tp), "--preset", "matched_srt", "--outdir", str(d / "p6")]), SystemExit))
        from flowocr.artifacts import matchedio as MI
        check("matched 的格式只有一份实现（scriptmatch re-export artifacts 那份）",
              SM_mod.SCHEMA is MI.SCHEMA and SM_mod.load_matched is MI.load and SM_mod.body is MI.body)
        check("matched_srt 吃 matched、三个叠加预设和 default_srt 两种都吃（ACCEPTS 写清楚）",
              X.load("matched_srt", "preset").ACCEPTS == (MI.SCHEMA,)
              and all(X.load(p, "preset").ACCEPTS == (tracksio.SCHEMA, MI.SCHEMA) for p in ("dev", "default_all", "default", "default_srt")))
        check("matched 校验版本：schema 不对就抛，不当成能读",
              raises(lambda: MI.validate({"schema": "flowocr-matched/1", "provenance": {}, "stats": {}, "cues": []}),
                     MI.MatchedSchemaError)
              and raises(lambda: MI.validate({"schema": MI.SCHEMA, "cues": []}), MI.MatchedSchemaError))
        no_ov = {"schema": MI.SCHEMA, "provenance": {}, "stats": {}, "cues": [], "display": _disp(dialogue="always")}
        check("叠加 ASS 的预设：产物里没有 overlay.items 就明确报错（--no-overlay 产的）；没有它的产物照常能读",
              raises(lambda: MI.validate(no_ov, need_overlay=True), MI.MatchedSchemaError) and not raises(lambda: MI.validate(no_ov)))


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
    band_it = OV.tracks_items(doc_band, "main")
    check("tracks·主轨：名牌所在区域并进了主轨时也只画一次；UI 按主轨自己的判定剔；同一条 cue 的两行合成一个句条目（名牌照旧单独一条）",
          sorted(sorted(e["id"] for e in it.events) for it in band_it) == [[0, 1], [3]]
          and [it.lines for it in band_it if it.kind == "line"] == [[evs[0]["text"], evs[1]["text"]]], [it.lines for it in band_it])
    from flowocr.artifacts import matchedio as MI
    m_items = [{"layer": "body", "start_us": 0, "end_us": 5_000_000, "rows": [evs[0]["box"], evs[1]["box"]],
                "events": [0, 1], "jp": "原文", "cn": "译文", "group": "dialogue", "fill": False},
               {"layer": "name", "start_us": 0, "end_us": 9_000_000, "rows": [evs[3]["box"]], "events": [3],
                "jp": None, "cn": None, "ocr": "名前", "group": "dialogue", "fill": False}]
    drawn: set[int] = set()
    bare: set[int] = set()
    OV.project_items(m_items, doc, "cn", MI.Visible(frozenset({"dialogue"}), True), drawn=drawn, bare=bare)
    check("matched·投影：可见、有中文的条目画出并记下事件；可见但没有中文的（没对上说话人的名牌）不画、事件记成 bare；组不可见的什么都不画",
          drawn == {0, 1} and bare == {3} and OV.project_items(m_items, doc, "cn", MI.Visible(frozenset({"unmatched"}), True)) == [],
          (drawn, bare))
    everyone = {e["id"] for e in evs}
    check("matched·OCR 原文补画（ocr_items）：只画给的事件，照 tracks 画、不当译文",
          [x.events[0]["id"] for x in OV.ocr_items(doc, {2, 3, 4})] == [2, 3, 4] and len(OV.ocr_items(doc, everyone)) == 5
          and not any(x.translated for x in OV.ocr_items(doc, everyone)))
    panel = OV.Item(0, 9_000_000, [[90, 890, 710, 980]], ["一段"], "block", translated=True, row_h=30)
    check("matched·OCR 原文补画：已画的阅读面板底下的事件不再补画原文（面板条目不记事件，按框 + 时间判）",
          [x.events[0]["id"] for x in OV.ocr_items(doc, everyone, [panel])] == [2, 3, 4])
    doc_fine = {**doc, "frame_us": 50_000}             # 采样间隔 50 ms：1 s 的事件切得开
    under = [x for x in OV.tracks_items(doc_fine, "all", per_event=True) if x.events[0]["id"] == 0][0]
    s0, e0, fu = under.start_us, under.end_us, doc_fine["frame_us"]
    mid = [OV.Item(s0 + 4 * fu, s0 + 6 * fu, panel.rows, ["一段"], "block"), OV.Item(s0 + 8 * fu, s0 + 9 * fu, panel.rows, ["一段"], "block")]
    parts = [(x.start_us, x.end_us) for x in OV.ocr_items(doc_fine, everyone, mid) if x.events[0]["id"] == 0]
    edge = [(x.start_us, x.end_us) for x in OV.ocr_items(doc_fine, everyone, [OV.Item(s0 + fu // 2, e0, panel.rows, ["一段"], "block")])
            if x.events[0]["id"] == 0]
    tiny = [x for x in OV.ocr_items({**doc, "frame_us": 10 * (e0 - s0)}, everyone) if x.events[0]["id"] == 0]
    far = [OV.Item(e0 + 20_000_000, e0 + 30_000_000, panel.rows, ["一段"], "block"), OV.Item(0, max(0, s0 - 1), panel.rows, ["一段"], "block")]
    tiny_far = [x for x in OV.ocr_items({**doc, "frame_us": 10 * (e0 - s0)}, everyone, far) if x.events[0]["id"] == 0]
    check("matched·OCR 原文补画：面板只盖住事件的一部分时间，只扣面板在屏的时段，面板前后、两块面板之间照画；扣剩不到一个采样间隔的碎段不画；"
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
              "\\an4\\pos(100,915)" in text0[0] and "\\an5\\pos(35,20)" in text0[2], text0[:3])   # 贴边的 UI：字离画面边至少 EDGE_PAD，轴往里推
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
        check("左锚的译文伸出画面时把轴往里推，右沿贴着画面边", abs(x + 10 * size - (1920 - LY.EDGE_PAD)) <= 1, wline)
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
            return [(48 * len(t), 30, 0, 0) for _f, _s, t in items]
        with patch.object(export, "render_ink", fake_ink):
            rc = R.main([str(tp), "--outdir", str(d / "r"), "--opt", "plate_color=off"])   # 底板取色要视频，另有守卫
            inked.clear()
            direct = X.load("default", "preset").render(tracksio.load(tp), d / "p", {"plate_color": "off"})
            inked_direct = list(inked)
            named = X.load("default", "preset").render(tracksio.load(tp), d / "n", {"name": "out.subs", "plate_color": "off"})
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
    # 重读（检测器三场修复计划 U4）：zzz 7512.0 s 那句，台词框退场下移，最后一帧每行另起 n_obs 1 的事件
    S_ = 1_000_000
    r1, r2 = [355, 446, 620, 466], [356, 469, 604, 483]
    l1 = ev(5784, r1, t0=int(7512.5 * S_), t1=int(7520.0 * S_), t_full_sampled=int(7512.5 * S_), text="ちょちょちょ…もう戦闘モードなワケ？")
    l2 = ev(5785, r2, t0=int(7512.5 * S_), t1=int(7520.0 * S_), t_full_sampled=int(7513.0 * S_), text="あーしも戦わなきゃいけない感じ!？")
    tails = [ev(5788, [356, 463, 447, 482], t0=int(7520.0 * S_), t1=int(7520.5 * S_), t_full_sampled=int(7520.0 * S_), text="ちょちょちよ"),
             ev(5787, [501, 462, 619, 480], t0=int(7520.0 * S_), t1=int(7520.5 * S_), t_full_sampled=int(7520.0 * S_), text="闘モードなワケ？"),
             ev(5789, [355, 482, 605, 500], t0=int(7520.0 * S_), t1=int(7520.5 * S_), t_full_sampled=int(7520.0 * S_), text="あーしも戦わなきゃいけない感じ！？")]
    it_tail = OV.Item(int(7512.0 * S_), int(7520.5 * S_), [r1, r2], ["a", "b"], "line", events=[l1, l2] + tails)
    check("效果·重读：原事件消失时另起的、字几乎都在原事件里的晚到事件不算时刻（zzz 2:05:12 打字机从 8.0 s 回到 1.0 s；"
          "小写ょ读成よ的那条也算）；成员不变",
          [e["id"] for e in OV.effect_events(it_tail.events, 500_000)] == [5784, 5785]
          and OV.effect_times(it_tail, raw, fr)[0] == 1_000_000 and len(it_tail.events) == 5,
          OV.effect_times(it_tail, raw, fr))
    short2 = ev(9, [380, 470, 470, 490], t0=int(1.0 * S_), t1=int(9.0 * S_), t_full_sampled=int(1.5 * S_), text="てるの。")
    first = ev(8, [380, 440, 900, 466], t0=0, t1=int(9.0 * S_), t_full_sampled=int(1.0 * S_), text="あなたがそう思ってるの。")
    scroll = [ev(40, [100, 400, 900, 430], t0=0, t1=10 * S_, t_full_sampled=S_, t_full_end=10 * S_ - 300_000, text="記録の一行です"),
              ev(41, [100, 300, 900, 330], t0=10 * S_, t1=40 * S_, t_full_sampled=10 * S_, t_full_end=39_500_000, text="記録の一行です")]
    it_scroll = OV.Item(0, 40 * S_, [[100, 300, 900, 430]], ["x"], "line", events=scroll)
    check("效果·重读：淡出照样从最后那段读数算（对话记录滚动时同一行在新位置重读，排掉的话淡出从第一段算、拉成几十秒）",
          [e["id"] for e in OV.effect_events(scroll, 500_000)] == [40] and OV.effect_times(it_scroll, raw, fr)[2] == 500_000,
          OV.effect_times(it_scroll, raw, fr))
    check("效果·重读：和第一行同时在屏的短第二行（字恰好都在第一行里，gi1 0:39:39）不是重读",
          [e["id"] for e in OV.effect_events([first, short2], 500_000)] == [8, 9])
    twin = [ev(20, [0, 0, 100, 30], t0=0, t1=S_, text="LEVEL"), ev(21, [0, 40, 100, 70], t0=0, t1=S_, text="LEVEL")]
    punct = [first, ev(10, [380, 470, 470, 490], t0=int(9.0 * S_), t1=int(9.5 * S_), text="……")]
    check("效果·重读：起点相同、字相同的两个事件不互相排掉；去完标点为空的读数不算重读",
          [e["id"] for e in OV.effect_events(twin, 500_000)] == [20, 21]
          and [e["id"] for e in OV.effect_events(punct, 500_000)] == [8, 10])
    only = [ev(31, [0, 40, 100, 70], t0=S_, t1=2 * S_, text="LEVEL")]
    pool = [ev(30, [0, 0, 100, 30], t0=0, t1=S_, text="LEVEL")] + only
    check("效果·重读：按行算时对照整条判，某一行会被拿空时这一行不去重读",
          [e["id"] for e in OV.effect_events(only, 500_000, pool)] == [31])
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
              and "FlowOCR Script: 1" in text and "Style: Body," in text and f",3,{LYT.OVERLAY_PAD},0,5," in text, src[:2])

        def run(txt: str, look: str = "default", fx=(), unknown: str = "error", **kw):
            ts = TS.Typesetter(replace(TS.LOOKS[look], plate_color="off", **kw), TS.load_fx(fx), unknown)
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
            rc_cli = TM.main([str(d / "y.script.ass"), "-o", str(d / "cli.ass"), "--opt", "plate_color=off"])
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


def _clip_at(tags: str, t: float) -> tuple[float, ...]:
    """按 libass 的读法算一串 `\\clip(…)\\t(a,b,\\clip(…))…` 在 `t` 毫秒时的矩形：从前往后，每个 `\\t` 按进度把上一个值插到它的目标。"""
    import re
    nums = lambda s: tuple(float(v) for v in s.split(","))           # noqa: E731
    cur = nums(re.match(r"\\clip\(([^)]*)\)", tags)[1])
    for a, b, r in re.findall(r"\\t\((\d+),(\d+),\\clip\(([^)]*)\)\)", tags):
        a, b, r = float(a), float(b), nums(r)
        k = 0.0 if t <= a else 1.0 if t >= b else (t - a) / (b - a)
        cur = tuple(c + (x - c) * k for c, x in zip(cur, r))
    return cur


def t_plate_grow() -> None:
    """打字机行的底板跟着字长（overlay-quality-lang 计划第 1 件）：外包络、`rows` 切两块正好是并集、字没出现时零面积、
    全字后等于并集、不超出静态底板；阶段 3 的 `grow`（同键同夹法、采样级往前放一个间隔、缺行补齐）；阶段 4 只动打字机行的底板。"""
    print("底板跟着字长")
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS
    from flowocr.typeset import grow as G

    # ---- 折线与外包络 ----
    src = G.ramp(0, 1000, 120, 300)
    tr = G.steps([(0, 150), (500, 200), (1000, 260)])
    env = G.envelope([src, tr])
    check("外包络：匀速的原文追上逐字跳的译文处补节点，之后是原文；同一段匀速不拆碎",
          env == [(0, 150.0), (500 / 3, 150.0), (1000, 300)], env)
    check("还没出现是 None；跳的时刻左极限是跳之前、右值是跳之后",
          G.at(tr, -1) is None and G.before(tr, 0) is None and G.before(tr, 500) == 150 and G.at(tr, 500) == 200)

    # ---- rows：切两块，拼起来正好是并集 ----
    static = [90, 10, 310, 50]
    clips = G.row_clips(static, [(100, src), (140, tr)])
    check("左沿不同：在靠右的左沿（140）切成两块", len(clips) == 2)

    def union_ok(t):
        a, b = (_clip_at(c, t) for c in clips)
        cover = lambda r: (r[0], r[2]) if r[2] > r[0] else None      # noqa: E731
        ca, cb = cover(a), cover(b)
        want_r = max(v for v in (G.at(src, t), G.at(tr, t)) if v is not None)
        got = [c for c in (ca, cb) if c]
        no_overlap = not (ca and cb) or ca[1] <= cb[0]
        return no_overlap and min(c[0] for c in got) == 100 and abs(max(c[1] for c in got) - min(want_r, 310)) <= 1
    check("rows 两块：任何时刻不重叠、合起来 = [靠左的左沿, 两段右沿的较大值]，不超出静态底板",
          all(union_ok(t) for t in (0, 50, 166, 300, 499, 500, 750, 1000, 5000)))
    late = G.row_clips(static, [(100, G.ramp(300, 800, 120, 300)), (140, G.steps([(600, 180)]))])
    check("一方还没开始：右块按切点算、宽 0（不按负值或它的左沿算）；左块在左方出现前是零面积",
          _clip_at(late[1], 0)[2] == _clip_at(late[1], 0)[0] == 140 and _clip_at(late[0], 100)[2] == 0
          and _clip_at(late[0], 400)[0] == 100)
    same = G.row_clips(static, [(100, src), (105, tr)])
    check("左沿只差几像素（重画在原文上的原文）：不切块，一块", len(same) == 1 and _clip_at(same[0], 0)[0] == 100, same)
    gap = G.row_clips([90, 10, 400, 50], [(100, G.ramp(0, 100, 150, 150)), (300, G.steps([(0, 380)]))])
    check("两段不相连：全字后中间的空档不盖（并集，不是静态底板）",
          _clip_at(gap[0], 1000)[2] == 150 and _clip_at(gap[1], 1000)[0] == 300)

    # ---- 整块：外接矩形四条边都跟着走，只有右沿线性长 ----
    box = G.bbox_clip([90, 10, 310, 90], [(100, 10, 50, G.ramp(200, 500, 120, 300)),
                                          (100, 50, 90, G.ramp(500, 1000, 120, 250))])
    check("整块：第一段出现前零面积；第二行开始时下沿跳下去（\\t(t,t+1)）；全字后等于外接矩形、不超出静态底板",
          _clip_at(box, 100) == (0, 0, 0, 0) and _clip_at(box, 499)[3] == 50 and _clip_at(box, 501)[3] == 90
          and _clip_at(box, 5000) == (100, 10, 300, 90) and "\\t(500,501," in box, box)

    # ---- 阶段 3：grow ----
    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    rows = [[100, 900, 700, 930], [100, 940, 500, 970], [100, 980, 300, 1010]]

    def ev(box, s, f, **kw):
        return {"id": 1, "box": box, "t_start": s, "t_end": 9_000_000, "region": 0, "text": "x", "t_full_sampled": f, **kw}
    it = SC.Item(1_000_000, 9_000_000, rows, ["一", "二", "三"], "line",
                 events=[ev(rows[0], 1_000_000, 2_000_000), ev(rows[2], 3_000_000, 4_000_000)])
    g = SC.grow_times(it, doc, 3_000_000)
    check("grow（采样级）：首字 / 全字都往前放一个采样间隔、夹到 [0, tw]；没分到事件的第二行在前后两行之间按框宽补",
          g == [(0, 500), (500, 1500), (1500, 2500)], g)
    ok = {"first": "agree", "full": "agree"}
    it_r = replace(it, events=[ev(rows[0], 1_000_000, None, t_full=1_800_000, t_full_how=ok),
                               ev(rows[1], 2_000_000, None, t_full=2_600_000, t_full_how={"first": "none", "full": "agree"}),
                               ev(rows[2], 3_000_000, None, t_full=4_500_000, t_full_how=ok)])
    g_r = SC.grow_times(it_r, {**doc, "text_effect": {"samples": 20, "verdict": "typewriter"}}, 3_000_000)
    check("grow（回抠过）：量到的照用，只有没量到的首字往前放一个间隔；全字夹到 tw",
          g_r == [(0, 800), (500, 1600), (2000, 3000)], g_r)
    check("grow：没做打字机的不写", SC.grow_times(it, doc, 0) is None)
    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        d = Path(td)
        SC.export_script(d / "g.script.ass", [it], doc)
        line = next(ln for ln in (d / "g.script.ass").read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:"))
        fo = AF.parse_fo(line.split(",", 9)[8])
        check("字幕稿：打字机行写 grow，读回来和算的一样", fo.get("grow") and TS.grow_of(fo["grow"]) == g, fo)

        # ---- 阶段 4：只动打字机行的底板 ----
        text = (d / "g.script.ass").read_text(encoding="utf-8")

        def run(txt, **kw):
            ts = TS.Typesetter(replace(TS.LOOKS["default"], plate_color="off", **kw))
            doc_ = AF.loads(txt)
            ts.run(doc_)
            return [ln for ln in doc_.dumps().splitlines() if ",fo-fx," in ln and "\\p1" in ln], ts
        plates, ts = run(text)
        check("阶段 4：打字机行的底板带 \\clip，字没出现之前零面积，全字后每行都盖上",
              plates and all("\\clip(" in p for p in plates) and ts.counts.get("plate_grow") == 1
              and any(_clip_at(p.split("\\1a&H20&")[1], 5000)[2] > 0 for p in plates), plates)
        static, _ = run(text, typewriter="off")
        no_tw, _ = run(text.replace(";tw=3000;", ";").replace(f";grow={fo['grow']}", ""))
        check("不做打字机（typewriter=off，或没有 tw）：静态底板，没有 \\clip，和改之前一样每行一块",
              len(static) == 3 and not any("\\clip" in p for p in static) and no_tw == static, (static, no_tw))
        bad, ts_b = run(text.replace(f";grow={fo['grow']}", ";grow=0+500"))
        check("grow 段数和原文框对不上（人改过 box）：按整条的打字机节奏估原文，打提示",
              bad and any("对不上" in m for _, m in ts_b.notes))
        moved, _ = run(text.replace("\\pos(", "\\pos(1", 1))
        check("人挪过位置的行：静态底板", moved and not any("\\clip" in p for p in moved), moved)


def t_sentence_items() -> None:
    """字幕稿一句一条（cue-shape 计划的 a / c）：句条目和例外、半截行只给首字、逐行时刻 `rowt`、阶段 4 按行显字、原文逐行字号。"""
    print("字幕稿一句一条")
    from dataclasses import replace

    from unittest.mock import patch

    from flowocr.artifacts import tracksio
    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS

    S = 1_000_000

    def ev(i, box, t0, t1, text="あいうえおかきくけこ", flags=(), **kw):
        return {"id": i, "region": 0, "box": box, "t_start": int(t0 * S), "t_end": int(t1 * S), "text": text,
                "flags": list(flags), "conf": 0.9, "n_obs": 10, **kw}
    r1, r2, r3 = [100, 900, 700, 940], [100, 950, 600, 990], [100, 1000, 500, 1040]
    evs = [ev(0, r1, 0, 6), ev(1, r2, 0.1, 6), ev(2, [100, 840, 300, 880], 0, 20, "名前", ["nameplate"]),
           ev(3, [100, 950, 300, 990], 0, 0.1, "あいう"),                 # 第二行的半截行，正好在它出现时消失
           ev(4, [1500, 100, 1700, 130], 0, 6, "HP", ["ui_footprint"]),
           ev(5, r3, 0, 6.6, "とても遅く消える行"),                         # 比这一句晚 0.6 s 消失
           ev(6, [100, 1050, 400, 1090], 0, 20, "ずっと居る")]               # 挂在两条 cue 里
    doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
           "provenance": {"main_track": "r00"}, "events": evs,
           "regions": [{"index": 0, "align": "left", "align_how": {"by": "stats"}}],
           "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "a.srt", "ui_lines": [],
                       "cues": [{"id": "c0", "t_start": 0, "t_end": 6 * S, "events": [2, 0, 3, 1, 4, 5, 6], "hide": [3], "partial": [3]},
                                {"id": "c1", "t_start": 6 * S, "t_end": 20 * S, "events": [2, 6]}]}]}
    tracksio.validate(doc)
    its = SC.tracks_items(doc, "main")
    body = [it for it in its if len(it.events) > 1]
    check("一条 cue 的正文两行合成一个句条目，行间是可见的两行", len(body) == 1 and body[0].lines == [evs[0]["text"], evs[1]["text"]]
          and body[0].rows == [r1, r2], [(it.lines, [e["id"] for e in it.events]) for it in its])
    b = body[0]
    check("半截行不进 events、挂在它那一行的 row_leads 上；起点取半截行的（首字出现）",
          3 not in [e["id"] for e in b.events] and [[x["id"] for x in rl] for rl in b.row_leads] == [[], [3]] and b.start_us == 0)
    singles = sorted(it.events[0]["id"] for it in its if len(it.events) == 1)
    check("照旧单独一条：名牌、比这一句晚消失的、挂在两条 cue 里的；剔 UI 的主轨视图不画 UI", singles == [2, 5, 6], singles)
    check("全部区域视图：UI 按 UI 标、单独一条；per_event 时一事件一条",
          any(it.ui and it.events[0]["id"] == 4 for it in SC.tracks_items(doc, "all"))
          and all(len(it.events) == 1 for it in SC.tracks_items(doc, "all", per_event=True)))
    loose = {**doc, "events": [ev(3, [100, 950, 300, 990], 0, 0.05, "あいう") if e["id"] == 3 else e for e in evs]}
    loose["events"][1] = ev(1, r2, 2.0, 6)                           # 半截行 0.05 s 消失、整行 2 s 才出来：隔开了
    li = SC.tracks_items(loose, "main")
    check("和后续隔开了的半截行：不挂成首字，照原样单独一条", any(it.events[0]["id"] == 3 and len(it.events) == 1 for it in li),
          [[e["id"] for e in it.events] for it in li])
    dup = {**doc, "events": evs[:3] + [ev(3, r2, 0, 5.9, evs[1]["text"])] + evs[4:]}         # 同文本、长的被藏成半截行、和代表重叠
    dup["events"][1] = ev(1, r2, 5.0, 6)
    di = SC.tracks_items(dup, "main")
    lead_it = [it for it in di if it.events[0]["id"] == 3]
    check("单独画的半截行只画到同一处那行出现之前（同一处同一刻不画两层）", lead_it and lead_it[0].end_us <= 5 * S,
          [(it.events[0]["id"], it.start_us, it.end_us) for it in di])
    lone = {**doc, "tracks": [{**doc["tracks"][0], "cues": [{"id": "c0", "t_start": 0, "t_end": 6 * S, "events": [3, 6], "hide": [3], "partial": [3]},
                                                            {"id": "c1", "t_start": 6 * S, "t_end": 20 * S, "events": [6]}]}]}
    check("一条 cue 里没有能成句的事件（都单独一条）时，它的半截行也照原样单独画，不丢",
          any(it.events[0]["id"] == 3 for it in SC.tracks_items(lone, "main")))

    rows = tracksio.rows_of_events([{"box": [400, 921, 560, 966], "text": "大魔女を"}, {"box": [640, 918, 1000, 964], "text": "ユキを殺す【犯人】は、"},
                                    {"box": [1500, 920, 1700, 965], "text": "Auto"}])
    check("同一行相隔不远的两段拼成一行（全角之间直接相连），隔得远的同一行单独成行",
          [r["text"] for r in rows] == ["大魔女をユキを殺す【犯人】は、", "Auto"] and rows[0]["box"] == [400, 918, 1000, 966], rows)
    check("拼行：两边都不是全角时空一格", tracksio.join_row_text("INZONE", "Buds") == "INZONE Buds" and tracksio.join_row_text("君は", "Auto") == "君はAuto")

    # ---- 逐行时刻 ----
    d0 = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    stag = SC.Item(0, 8 * S, [r1, r2], ["一行目", "二行目"], "line", events=[ev(0, r1, 0, 8), ev(1, r2, 3, 8)],
                   row_events=[[ev(0, r1, 0, 8)], [ev(1, r2, 3, 8)]], row_leads=[[], []])
    check("即时出现、第二行晚 3 s 的句子：写 rowt，两行都不打字（全字 = 首字）", SC.row_times(stag, d0, 33_333) == [(0, 0), (3000, 3000)])
    typed = replace(stag, row_events=[[ev(0, r1, 0, 8, t_full_sampled=2 * S)], [ev(1, r2, 3, 8, t_full_sampled=5 * S)]])
    check("各行自己打字：每行在自己的 [首字, 全字]", SC.row_times(typed, d0, 33_333) == [(0, 2000), (3000, 5000)])
    check("各行首字都在起点一个采样间隔之内：不写 rowt", SC.row_times(replace(stag, row_events=[[ev(0, r1, 0, 8)], [ev(1, r2, 0.3, 8)]]), d0, 33_333) is None)

    # ---- 阶段 4 ----
    check("with_rowt：打字的行在自己的区间匀速、不打字的行整行在首字；关了打字机也按首字起显",
          [t[2] for t in TS.with_rowt([[("ch", "あ"), ("ch", "い"), ("ch", "う")]], [(1000, 3000)], True)[0]] == [1000, 2000, 3000]
          and [t[2] for t in TS.with_rowt([[("ch", "あ"), ("ch", "い")]], [(1000, 3000)], False)[0]] == [1000, 1000])
    check("dim + 逐行：首字之前全透明、全字之前半透明", TS.body_of([("ch", "あ", 0)], "dim", 3000, 1000).startswith("{\\alpha&HFF&\\t(1000,1001,\\alpha&H80&)\\t(3000,3001"))
    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        dd = Path(td)
        SC.export_script(dd / "s.script.ass", [stag], d0)
        text = (dd / "s.script.ass").read_text(encoding="utf-8")
        fo = AF.parse_fo(next(ln for ln in text.splitlines() if ln.startswith("Dialogue:")).split(",", 9)[8])
        check("字幕稿：句条目写 rowt、一行都不打字时 tw 不写", fo.get("rowt") == "0+0|3000+3000" and not fo.get("tw"), fo)

        def run(txt, **kw):
            ts = TS.Typesetter(replace(TS.LOOKS["default"], plate_color="off", **kw))
            doc_ = AF.loads(txt)
            ts.run(doc_)
            return [ln for ln in doc_.dumps().splitlines() if ",fo-fx," in ln and "\\p1" not in ln], ts
        lines, ts = run(text)
        second = [ln for ln in lines if "二" in ln]
        check("阶段 4：第二行的字在 3 s 才出现（不从条目起点画），第一行从起点",
              second and "\\t(3000,3001," in second[0] and any("一" in ln and "\\t(0,1," in ln for ln in lines), lines)
        off, _ = run(text, typewriter="off")
        check("阶段 4 关了打字机：第二行照样到首字时刻才出现", any("二" in ln and "\\t(3000,3001," in ln for ln in off), off)
        bad, ts_b = run(text.replace("rowt=0+0|3000+3000", "rowt=0+0"))
        check("rowt 段数和行数对不上：回退并提示", any("rowt" in m and "对不上" in m for _, m in ts_b.notes))
        def plates(txt, **kw):
            ts = TS.Typesetter(replace(TS.LOOKS["default"], plate_color="off", **kw))
            doc_ = AF.loads(txt)
            ts.run(doc_)
            return [ln for ln in doc_.dumps().splitlines() if ",fo-fx," in ln and "\\p1" in ln]
        for mode in ("on", "dim"):
            low = [pl for pl in plates(text, typewriter=mode)
                   if "\\clip(" in pl and int(re.search(r"\\pos\(\d+,(\d+)\)", pl).group(1)) > 940]   # 第二行的底板
            check(f"阶段 4（typewriter={mode}）：晚出现那一行的底板在首字之前是零面积",
                  low and all(_clip_at(pl.split("\\1a&H20&")[1], 1000)[2] - _clip_at(pl.split("\\1a&H20&")[1], 1000)[0] <= 0 for pl in low), low)
        check("晚出现的行不淡入（淡入只在条目起点）", not any("二" in ln and "\\fad(" in ln for ln in lines))
        SC.export_script(dd / "o.script.ass", [replace(stag, row_events=[[ev(0, r1, 0, 8, t_full_sampled=2 * S)], [ev(1, r2, 3, 8, t_full_sampled=5 * S)]])],
                         d0, typewriter=False)
        fo_off = AF.parse_fo(next(ln for ln in (dd / "o.script.ass").read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")).split(",", 9)[8])
        check("阶段 3 typewriter=off：rowt 照写、全部全字 = 首字", fo_off.get("rowt") == "0+0|3000+3000", fo_off)
        k, _ = run(text.replace("}一行目", "}{\\k50}一行目"))
        check("人写了 \\k 节奏：以人为准，不用 rowt", not any("\\t(3000,3001," in ln for ln in k), k)
        sizes = [int(re.search(r"\\fs(\d+)", ln).group(1)) for ln in run(text.replace("box=100 900 700 940|100 950 600 990",
                                                                                      "box=100 900 700 940|100 950 600 970"))[0]]
        check("原文逐行摆：每行按自己的框适配字号（矮的那行字更小）", len(set(sizes)) == 2, sizes)
        from flowocr.output import layout as LY
        two = LY.plate_rects([[100, 900, 700, 940], [100, 950, 600, 970]], [600, 500], [400, 350], "rows", "center", [40.0, 20.0])
        check("底板的正文高可以逐行给：矮行的底板不撑到大字那行的高", (two[1][3] - two[1][1]) < (two[0][3] - two[0][1]), two)


def t_default_srt() -> None:
    """阶段 3 的 SRT（`default_srt`，cue-shape 计划的 d）：条目和 `default` 同一批、名牌 / UI 不出、matched 出剧本原句、
    空行剔掉、`start=full`、`keep=all`；`scriptmatch --ass` 拒收只出 SRT 的预设。"""
    print("阶段 3 的 SRT")
    import json as _json
    import sys as _sys
    from unittest.mock import patch

    from flowocr import extensions as X
    from flowocr.analyze import scriptmatch as SM
    from flowocr.artifacts import matchedio as MI
    from flowocr.artifacts import srtio, tracksio
    from flowocr.output import script as SC

    S = 1_000_000
    P = X.load("default_srt", "preset")

    def ev(i, box, t0, t1, text, flags=(), **kw):
        return {"id": i, "region": 0, "box": box, "t_start": int(t0 * S), "t_end": int(t1 * S), "text": text,
                "flags": list(flags), "conf": 0.9, "n_obs": 10, **kw}
    r1, r2 = [100, 900, 700, 940], [100, 950, 600, 990]
    evs = [ev(0, r1, 0, 5, "一行目のせりふ", t_full=int(1 * S)), ev(1, r2, 0.2, 5, "二行目のせりふ", t_full=int(2 * S)),
           ev(2, [100, 840, 300, 880], 0, 5, "名前", ["nameplate"]), ev(3, [1500, 50, 1600, 80], 0, 5, "HP", ["ui_footprint"])]
    doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
           "provenance": {"main_track": "r00", "main_srt": "t-region00-subtitle.srt"}, "events": evs,
           "regions": [{"index": 0, "align": "left", "align_how": {"by": "stats"}}],
           "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "t-region00-subtitle.srt", "ui_lines": [],
                       "cues": [{"id": "c0", "t_start": 0, "t_end": 5 * S, "events": [2, 0, 1, 3]}]}]}
    tracksio.validate(doc)
    items = SC.tracks_items(doc, "main")
    check("名牌 / UI 不出、句条目一条、几行按 SRT 换行", SC.srt_blocks(items) == [(0, 5 * S, ["一行目のせりふ", "二行目のせりふ"])],
          SC.srt_blocks(items))
    check("start=full：取正文行全字出现的最晚时刻、夹在条目里", SC.srt_blocks(items, start="full")[0][0] == 2 * S)
    hdoc = {**doc, "provenance": {**doc["provenance"], "args": {"cue_join": "on"}},
            "events": evs + [ev(4, [100, 1000, 400, 1040], 0, 9, "ずっと居る称号")],
            "tracks": [{**doc["tracks"][0], "cues": [{"id": "c0", "t_start": 0, "t_end": 5 * S, "events": [2, 0, 1, 3, 4]},
                                                     {"id": "c1", "t_start": 5 * S, "t_end": 9 * S, "events": [4]}]}]}
    hit = SC.tracks_items(hdoc, "main")
    check("跨句常驻（挂在几条 cue 里）的行：字幕稿照画、SRT 不出", any(it.spans and it.lines == ["ずっと居る称号"] for it in hit)
          and all("ずっと居る称号" not in b[2] for b in SC.srt_blocks(hit)), SC.srt_blocks(hit))
    off = SC.tracks_items({**hdoc, "provenance": {**doc["provenance"], "args": {"cue_join": "off"}}}, "main")
    check("--cue-join off 建的产物（同一句可能被切成几条 cue）：不标跨句常驻，挂在几条 cue 里的行照进 SRT",
          not any(it.spans for it in off) and any("ずっと居る称号" in b[2] for b in SC.srt_blocks(off)))
    body = SC.tracks_items({**hdoc, "provenance": {**doc["provenance"], "args": {"cue_join": "on", "srt_mode": "body"}}}, "main")
    check("--srt-mode body 建的产物（不做同句合并）：同样不标跨句常驻", not any(it.spans for it in body))
    all_items, *_ = SC.build_items(doc, {"keep": "all"}, align=False)
    check("keep=all：条数等于 default_all 的正文条目数（跨句常驻的除外）",
          len(SC.srt_blocks(all_items)) == sum(1 for it in all_items if SC.style_of(it) in SC.SRT_STYLES and not it.spans))
    tr_it = SC.Item(0, S, [r1, r2], ["x", "y"], "line", translated=True, source="訳文の一行目\\N\\N  \n訳文の二行目")
    check("srt_text：matched 出剧本原句、\\N 和换行都认、空行剔掉", SC.srt_text(tr_it) == ["訳文の一行目", "訳文の二行目"], SC.srt_text(tr_it))
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        tp = d / "t-tracks.json"
        tracksio.dump(tp, doc)
        m = {"schema": MI.SCHEMA, "provenance": {"subs": str(tp)}, "stats": {}, "display": _disp(dialogue="always"),
             "cues": [{"start": 0.0, "end": 5.0, "ocr": "一行目のせりふ", "extra": [], "echo": None, "echo_of": None,
                       "ref": {"key": "k", "jp": "原文", "cn": "第一句译文\\N第二句译文", "group": "dialogue", "fill": False}}],
             "overlay": {"items": [{"layer": "body", "start_us": 0, "end_us": 5 * S, "rows": [r1, r2], "events": [0, 1], "claim": [0, -1],
                                    "jp": "原文", "cn": "第一句译文\\N第二句译文", "group": "dialogue", "fill": False},
                                   {"layer": "name", "start_us": 0, "end_us": 5 * S, "rows": [evs[2]["box"]], "events": [2], "cn": "名字",
                                    "group": "dialogue", "fill": False}],
                         "stats": {}, "fed": [0, 1, 2]}}
        got = P.render(m, d / "o", {"lang": "cn"})
        check("matched：译文出剧本原句、按剧本的换行分行；名牌层不出；文件名带 lang",
              [x.name for x in got] == ["t-cn-default.srt"] and [c.lines for c in srtio.read_srt(got[0])] == [("第一句译文", "第二句译文")],
              [c.lines for c in srtio.read_srt(got[0])])
        check("matched 找不到对应的 tracks：报 FileNotFoundError",
              raises(lambda: P.render({**m, "provenance": {"subs": str(d / "nope.json")}}, d / "o2", {"lang": "cn"}), FileNotFoundError))
        argv = ["scriptmatch", str(d / "ref.json"), "--subs", str(tp), "--out", str(d / "m.json"), "--ass", str(d / "x.ass"),
                "--preset", "default_srt"]
        import contextlib
        import io
        err = io.StringIO()
        with patch.object(_sys, "argv", argv), contextlib.redirect_stderr(err):
            check("scriptmatch --ass 拒收只出 SRT 的预设（不把 SRT 写进给 ASS 的文件名）", raises(SM.main, SystemExit) and "不出 ASS" in err.getvalue(),
                  err.getvalue()[-200:])
    _ = _json


def t_srt_echo() -> None:
    """SRT 一类预设去回显（owner 2026-10-06，matcher-next 计划第 1 节）：`matched_srt` / `default_srt`（及走 `srt_blocks` 的用户预设）
    默认不出 `same_time` / `log`，`echo=keep` 放回来；字幕稿（叠加）照画。"""
    print("SRT 去回显")
    from flowocr import extensions as X
    from flowocr.artifacts import matchedio as MI
    from flowocr.artifacts import srtio, tracksio
    from flowocr.output import script as SC

    S = 1_000_000
    # 记录 0..6（第 i 条在 10·i 秒）；记录 0 带两条 extra：k5 有叠加条目、k6 没有（事件全被主认领占了那种）
    forms = (None, "same_time", "log", "replay", "other", "same_time", "log")
    of = (None, [0, -1], [0, -1], [0, -1], [0, -1], [0, 0], [0, 1])          # 5 的主是记录 0 的 extra k5，6 的主是 extra k6
    evs = [{"id": i, "region": 0, "box": [100, 900, 700, 940], "t_start": i * 10 * S, "t_end": (i * 10 + 5) * S,
            "text": f"せりふ{i}", "flags": [], "conf": 0.9, "n_obs": 10} for i in range(len(forms))]
    evs.append({**evs[0], "id": 7, "box": [100, 800, 700, 840], "text": "せりふ5"})
    doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
           "provenance": {"main_track": "r00", "main_srt": "t-region00-subtitle.srt"}, "events": evs,
           "regions": [{"index": 0, "align": "left", "align_how": {"by": "stats"}}],
           "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "t-region00-subtitle.srt", "ui_lines": [],
                       "cues": [{"id": f"c{i}", "t_start": i * 10 * S, "t_end": (i * 10 + 5) * S, "events": [i, 7] if i == 0 else [i]}
                                for i in range(len(forms))]}]}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        tp = d / "t-tracks.json"
        tracksio.dump(tp, doc)

        def claim(k):
            return {"key": f"k{k}", "jp": f"台詞{k}", "cn": f"台词{k}", "score": 1.0, "group": "dialogue", "fill": False}
        m = {"schema": MI.SCHEMA, "provenance": {"subs": str(tp)}, "stats": {}, "display": _disp(dialogue="always"),
             "cues": [{"start": i * 10.0, "end": i * 10.0 + 5, "ocr": f"せりふ{i}", "how": "exact", "conf": "high", "cues": [i],
                       "ref": claim(i), "echo": f, "echo_of": of[i],
                       "extra": [{**claim(k), "echo": None, "echo_of": None} for k in ((5, 6) if i == 0 else ())]}
                      for i, f in enumerate(forms)],
             "overlay": {"items": [{"layer": "body", "start_us": i * 10 * S, "end_us": (i * 10 + 5) * S, "rows": [evs[i]["box"]],
                                    "events": [i], **claim(i), "claim": [i, -1], "echo": f, "echo_of": of[i]}
                                   for i, f in enumerate(forms)]
                      + [{"layer": "extra", "start_us": 0, "end_us": 5 * S, "rows": [evs[7]["box"]], "events": [7], **claim(5),
                          "claim": [0, 0], "echo": None, "echo_of": None}], "stats": {}, "fed": [e["id"] for e in evs]}}

        def texts(p):
            return sorted((round(c.start), c.lines[0]) for c in srtio.read_srt(p))
        ms = X.load("matched_srt", "preset")
        a = texts(ms.render(m, d / "a", {"lang": "cn"})[0])
        b = texts(ms.render(m, d / "b", {"lang": "cn", "echo": "keep"})[0])
        check("matched_srt：默认不出同时回显 / 记录面板回显（重播、其他照出）；主落在别的记录的 extra 上的回显照出（它只出 ref，不然这一句一次都不出）；echo=keep 全放回来",
              [t for _, t in a] == ["台词0", "台词3", "台词4", "台词5", "台词6"] and len(b) == 7, (a, b))
        ds = X.load("default_srt", "preset")
        a = texts(ds.render(m, d / "c", {"lang": "cn"})[0])
        b = texts(ds.render(m, d / "e", {"lang": "cn", "echo": "keep"})[0])
        check("default_srt：去掉主那次认领有条目要出的回显（1、2，以及主是有叠加条目的 extra k5 时的 5：k5 由那条 extra 出）；"
              "主是没有叠加条目的 extra（k6）时照出那条回显；echo=keep 全放回来",
              a == [(0, "台词0"), (0, "台词5"), (30, "台词3"), (40, "台词4"), (60, "台词6")] and len(b) == 8, (a, b))
        check("echo 的取值打错报错（两个 SRT 预设都是）",
              raises(lambda: ms.render(m, d / "f", {"echo": "all"}), ValueError) and raises(lambda: ds.render(m, d / "f", {"echo": "all"}), ValueError))
        items, *_ = SC.build_items(m, {"lang": "cn"}, align=False)
        check("字幕稿 / 叠加照画回显（去回显只在 SRT 投影里）", [it.echo for it in items] == [*forms, None], [it.echo for it in items])
        tracks_items = SC.tracks_items(doc, "main")
        check("tracks 产物没有回显判定，SRT 不受影响", len(SC.srt_blocks(tracks_items)) == len(forms))


def t_display_protocol() -> None:
    """显示协议的输出侧（miss-detector 计划，owner 2026-10-07）：所有吃匹配产物的预设按 `matchedio.visible` 的可见集合画；
    `show` / `hide` 只改点名的组或填位；`keep` / `layers` 对匹配产物报错；`unmatched` / `unfed` 补画 OCR 原文；`matched_srt` 按可见性出。"""
    print("显示协议（输出侧）")
    import contextlib
    import io
    from unittest.mock import patch

    from flowocr import extensions as X
    from flowocr.artifacts import matchedio as MI
    from flowocr.artifacts import srtio, tracksio
    from flowocr.output import export
    from flowocr.output import script as SC

    S = 1_000_000

    def ev(i, region, box, t0, text):
        return {"id": i, "region": region, "box": box, "t_start": t0 * S, "t_end": (t0 + 5) * S, "text": text,
                "flags": [], "conf": 0.9, "n_obs": 10}
    evs = [ev(0, 0, [100, 900, 700, 940], 0, "せりふ0"), ev(1, 1, [800, 500, 1000, 540], 0, "せんたく1"),
           ev(2, 1, [800, 560, 1000, 600], 30, "せんたく2"), ev(3, 1, [1500, 100, 1700, 140], 40, "めにゅう3"),
           ev(4, 0, [100, 900, 700, 940], 10, "せりふ4"), ev(5, 0, [100, 900, 700, 940], 20, "ざつおん"),
           ev(6, 1, [1500, 50, 1600, 80], 60, "よそ6"), ev(7, 1, [1000, 300, 1200, 330], 70, "めんか7")]
    doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
           "provenance": {"main_track": "r00", "main_srt": "t-region00-subtitle.srt"}, "events": evs,
           "regions": [{"index": 0, "align": "left", "align_how": {"by": "stats"}}, {"index": 1, "align": "center", "align_how": {"by": "none"}}],
           "tracks": [{"id": f"r0{r}", "kind": "region", "region": r, "srt": f"t-region0{r}.srt", "ui_lines": [],
                       "cues": [{"id": f"r0{r}c{e['id']}", "t_start": e["t_start"], "t_end": e["t_end"], "events": [e["id"]]}
                                for e in evs if e["region"] == r]} for r in (0, 1)]}

    def claim(k, group, cn=True, fill=False):
        return {"key": f"k{k}", "jp": f"台詞{k}", "cn": f"台词{k}" if cn else None, "group": group, "fill": fill}

    def rec(t, ocr, ref, extra=(), echo=None, echo_of=None):
        return {"start": float(t), "end": t + 5.0, "ocr": ocr, "ref": ref, "extra": list(extra), "echo": echo, "echo_of": echo_of}

    def item(layer, k, group, events, t0, fill=False, cn=True, claim_=None):
        return {"layer": layer, "key": f"k{k}", "jp": f"台詞{k}", "cn": f"台词{k}" if cn else None, "group": group, "fill": fill,
                "start_us": t0 * S, "end_us": (t0 + 5) * S, "rows": [evs[events[0]]["box"]], "events": events,
                **({"claim": claim_, "echo": None, "echo_of": None} if claim_ else {})}
    # 记录：0 台词（always）+ 一条选项 extra；1 台词但没有中文；2 没认领；3 填位的选项；4 界面文字（off）；
    # 5 / 6 同一句台词，5 是主（填位）、6 是它的同时回显（不是填位）
    cues = [rec(0, "せりふ0", claim(0, "dialogue"), [claim(1, "choice")]), rec(10, "せりふ4", claim(4, "dialogue", cn=False)),
            rec(20, "ざつおん", None), rec(30, "せんたく2", claim(2, "choice", fill=True)), rec(40, "めにゅう3", claim(3, "ui")),
            rec(50, "せりふ6", claim(6, "dialogue", fill=True)), rec(60, "せりふ6", claim(6, "dialogue"), echo="same_time", echo_of=[5, -1])]
    panel = {"layer": "panel", "group": "panel", "fill": False, "start_us": 70 * S, "end_us": 76 * S, "rows": [[980, 280, 1300, 400]],
             "paras": {"jp": ["めんか"], "cn": ["面板"]}, "row_h": 30, "events": []}
    items = [item("body", 0, "dialogue", [0], 0, claim_=[0, -1]), item("choice", 1, "choice", [1], 0),
             item("body", 4, "dialogue", [4], 10, cn=False, claim_=[1, -1]), item("choice", 2, "choice", [2], 30, fill=True),
             item("offmain", 3, "ui", [3], 40), panel]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        tp = d / "t-tracks.json"
        tracksio.dump(tp, doc)
        m = {"schema": MI.SCHEMA, "provenance": {"subs": str(tp)}, "stats": {}, "cues": cues,
             "display": _disp(dialogue="always", choice="on", ui="off", panel="off"),
             "overlay": {"items": items, "stats": {}, "fed": [0, 1, 2, 3, 4, 5]}}
        MI.validate(m, need_overlay=True)

        def bad(**over):
            return raises(lambda: MI.validate({**m, **over}, need_overlay=True), MI.MatchedSchemaError)
        g0 = m["display"]["groups"]
        check("validate 拒收：组名用了关键字 all / fill、缺保留组、档位不合法、条目的组没声明、body 条目的组和它的认领对不上",
              bad(display={**m["display"], "groups": {**g0, "all": {"tier": "on", "label": "x"}}})
              and bad(display={**m["display"], "groups": {**g0, "fill": {"tier": "on", "label": "x"}}})
              and bad(display={**m["display"], "groups": {k: v for k, v in g0.items() if k != "unfed"}})
              and bad(display={**m["display"], "groups": {**g0, "ui": {"tier": "maybe", "label": "x"}}})
              and bad(overlay={**m["overlay"], "items": items + [{**items[1], "group": "nope"}]})
              and bad(overlay={**m["overlay"], "items": [{**items[0], "group": "choice"}] + items[1:]}))

        def build(**o):
            return SC.build_items(m, {"lang": "cn", **o}, align=False)[0]

        def keys(its):
            return sorted(it.key for it in its if it.translated and it.key)

        def raw(its):
            return sorted(it.events[0]["id"] for it in its if not it.translated)
        check("show / hide 报错：hide 一个 always 组、没声明的名字、hide=all、同名既 show 又 hide",
              raises(lambda: build(hide="dialogue"), ValueError) and raises(lambda: build(show="nope"), ValueError)
              and raises(lambda: build(hide="all"), ValueError) and raises(lambda: build(show="ui", hide="ui"), ValueError))
        check("匹配产物传 keep / layers 报错（叠加 / 字幕稿 / default_srt / matched_srt 都是）；tracks 产物传 show / hide 报错、keep 照旧",
              raises(lambda: build(keep="all"), ValueError) and raises(lambda: build(layers="body"), ValueError)
              and raises(lambda: X.load("default", "preset").render(m, d / "x", {"keep": "main"}), ValueError)
              and raises(lambda: X.load("default_srt", "preset").render(m, d / "x", {"keep": "all"}), ValueError)
              and raises(lambda: X.load("matched_srt", "preset").render(m, d / "x", {"layers": "body"}), ValueError)
              and raises(lambda: SC.build_items(doc, {"show": "all"}, align=False), ValueError)
              and not raises(lambda: SC.build_items(doc, {"keep": "all"}, align=False)))
        check("默认：always + on 的组画、off 的组（ui、panel）不画，填位按 display.fill（on）画；没有中文的台词不画、也不补画 OCR",
              keys(build()) == ["k0", "k1", "k2"] and raw(build()) == [], (keys(build()), raw(build())))
        check("show= 打开 off 的组；层只定怎么画（ui 组的 offmain 条目、panel 组的面板块都随组出来）",
              keys(build(show="ui")) == ["k0", "k1", "k2", "k3"]
              and [it.kind for it in build(show="panel") if it.kind == "block"] == ["block"])
        m_off = {**m, "display": {**m["display"], "fill": {"tier": "off"}}}
        check("填位：hide=fill 不画；display.fill=off 时默认不画、show=fill 画；hide=choice 连填位的选项一起关",
              keys(build(hide="fill")) == ["k0", "k1"]
              and keys(SC.build_items(m_off, {"lang": "cn"}, align=False)[0]) == ["k0", "k1"]
              and keys(SC.build_items(m_off, {"lang": "cn", "show": "fill"}, align=False)[0]) == ["k0", "k1", "k2"]
              and keys(build(hide="choice")) == ["k0"])
        un, unfed = MI.fed_rest(m, doc)
        check("lang=cn 且 unmatched 可见：没有中文的可见条目（k4）退回画 OCR 原文，另画 fed_rest 的 unmatched 事件；lang=jp 时 k4 画日文、不补画",
              (un, unfed) == ([5], [6, 7]) and raw(build(show="unmatched")) == [4, 5]
              and raw(SC.build_items(m, {"lang": "jp", "show": "unmatched"}, align=False)[0]) == [5]
              and "k4" in keys(SC.build_items(m, {"lang": "jp"}, align=False)[0]), raw(build(show="unmatched")))
        check("补画的事件集合：unmatched 可见 = fed_rest 的 unmatched（+ 没文本的可见条目），unfed 可见 = fed_rest 的 unfed；"
              "被隐藏组的条目引用的事件（ui 的 3）两组都不画",
              raw(build(show="unfed")) == unfed and raw(build(show="unmatched,unfed")) == sorted(un + unfed + [4])
              and 3 not in raw(build(show="unmatched,unfed")))
        e7 = [it for it in build(show="unfed") if not it.translated and it.events[0]["id"] == 7]
        check("阅读面板：面板不画时它底下的 unfed 事件照画整段；面板画出来时扣掉被盖住的那段（这里整段都在面板里，不画）",
              len(e7) == 1 and (e7[0].start_us, e7[0].end_us) == (70 * S, 75 * S) and 7 not in raw(build(show="unfed,panel")))
        all_ = build(show="all")
        check("show=all：全部组加填位、两个保留组的 OCR 原文（没中文的 k4 退回 OCR；面板也画了，它底下的 7 扣掉）；hide 照样生效",
              keys(all_) == ["k0", "k1", "k2", "k3"] and raw(all_) == [4, 5, 6]
              and keys(build(show="all", hide="ui")) == ["k0", "k1", "k2"], (keys(all_), raw(all_)))
        with patch.object(export, "ink_widths", fake_widths), contextlib.redirect_stdout(io.StringIO()):
            drafts = {p: X.load(p, "preset").render(m, d / p, {"lang": "cn", "plate_color": "off", **o})[1].read_text(encoding="utf-8")
                      for p, o in (("default", {}), ("default_all", {}), ("dev", {"hide": "ui"}))}
        check("叠加预设：default 按默认可见集合；default_all / dev 等于 show=all（用户的 hide 照样生效）；字幕稿的 Note 写可见的组",
              "台词3" not in drafts["default"] and "ざつおん" not in drafts["default"]
              and "台词3" in drafts["default_all"] and "ざつおん" in drafts["default_all"]
              and "台词3" not in drafts["dev"] and "ざつおん" in drafts["dev"]
              and "FlowOCR Note: 剧本译文 cn，可见的组 dialogue,choice，填位画" in drafts["default"], drafts["default"][:600])

        def srt(preset, **o):
            with contextlib.redirect_stdout(io.StringIO()):
                p = X.load(preset, "preset").render(m, d / f"{preset}-{len(o)}-{'-'.join(o.values())}", {"lang": "cn", **o})[0]
            return [(round(c.start), c.lines[0]) for c in srtio.read_srt(p)]
        check("matched_srt：ref 可见、有中文才出剧本文本；没中文的只在 unmatched 可见时出 OCR；ref 为空的只在 unmatched 可见时出",
              srt("matched_srt") == [(0, "台词0"), (30, "台词2"), (50, "台词6")]
              and srt("matched_srt", show="unmatched") == [(0, "台词0"), (10, "せりふ4"), (20, "ざつおん"), (30, "台词2"), (50, "台词6")]
              and srt("matched_srt", show="ui", lang="jp") == [(0, "台詞0"), (10, "台詞4"), (30, "台詞2"), (40, "台詞3"), (50, "台詞6")],
              (srt("matched_srt"), srt("matched_srt", show="unmatched")))
        check("matched_srt：去回显叠在可见性之后——主（填位）被 hide=fill 关掉时它的同时回显照出，不然这一句一次都不出；hide=choice 关掉选项",
              srt("matched_srt", hide="fill") == [(0, "台词0"), (60, "台词6")] and srt("matched_srt", hide="choice") == [(0, "台词0"), (50, "台词6")])
        check("default_srt 走 build_items：默认不出 OCR 原文，show=unmatched 出；show=all 的文件名是 -default_all",
              all("ざつおん" not in t for _, t in srt("default_srt")) and any(t == "ざつおん" for _, t in srt("default_srt", show="unmatched"))
              and srt("default_srt", show="all") and (d / "default_srt-1-all" / "t-cn-default_all.srt").is_file())


def t_plate_grow_modes() -> None:
    """底板跟着字长的各条路：逐行摆的 `dim` / `plate=text`、整块画的 `single`（按 cap 等分、扩行）和 `free`（按 \\pos 取锚轴）、
    带轨迹的行（人手加打字机）照旧静态、扩展效果的 `tags` 挂点在切成两块的一行上调两次、宽度不定的标点按全角算。"""
    print("底板跟着字长：各种排法")
    import re
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS

    cells = TS.cells_of(TS.with_tw(TS.text_rows("“中”…—")[0], 400, False)[0])[0]
    fr = TS.text_front(cells, 0, 500, 50, 0, None)
    check("译文右沿：引号、省略号、破折号（east_asian_width = A）和汉字一样按全角算，五个字各占五分之一",
          [round(v) for _, v in fr][1::2] == [100, 200, 300, 400] and fr[-1] == (400, 500.0), fr)

    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}

    def ev(i, box, s=0, f=2_000_000):
        return {"id": i, "box": box, "t_start": s, "t_end": 6_000_000, "region": 0, "text": "x", "t_full_sampled": f}
    it_t = SC.Item(0, 6_000_000, [[600, 900, 1300, 940]], ["短译文"], "line", events=[ev(1, [600, 900, 1300, 940])],
                   region=0, translated=True, layer="body")
    it_s = SC.Item(0, 6_000_000, [[600, 500, 1300, 580]], ["一行原文\n二行原文"], "line",
                   events=[ev(2, [600, 500, 1300, 580])], region=1)
    it_f = SC.Item(0, 6_000_000, [[600, 300, 1300, 340], [700, 345, 1200, 385]], ["一行"], "line",
                   events=[ev(3, [600, 300, 1300, 340]), ev(3, [700, 345, 1200, 385], 1_000_000)],
                   region=2, translated=True, layer="body")
    it_m = SC.Item(0, 2_000_000, [[100, 100, 400, 130]], ["走"], "line", events=[ev(4, [100, 100, 400, 130])], region=3,
                   boxes=[[0, [100, 100, 400, 130]], [2_000_000, [300, 100, 600, 130]]])
    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        d = Path(td)
        SC.export_script(d / "m.script.ass", [it_t, it_s, it_f, it_m], doc)
        text = (d / "m.script.ass").read_text(encoding="utf-8")

        def run(txt, fx=(), **kw):
            ts = TS.Typesetter(replace(TS.LOOKS["default"], plate_color="off", **kw), TS.load_fx(fx))
            doc_ = AF.loads(txt)
            ts.run(doc_)
            return doc_.dumps(), ts

        def plates(txt, actor):
            return [ln.split("\\1a&H20&", 1)[1] for ln in txt.splitlines()
                    if ",fo-fx," in ln and "\\p1" in ln and ln.split(",")[4] == actor]
        out, _ = run(text)
        t_on = plates(out, "r0")
        from flowocr.output.layout import OVERLAY_PAD as pd
        src_left = 600 - pd
        check("逐行摆、译文比原文窄：切成两块，一开始原文那块就有（领先一个行高），译文那块按首字",
              len(t_on) == 2 and _clip_at(t_on[0], 1)[0] == src_left and _clip_at(t_on[0], 1)[2] > src_left, t_on)
        dim, _ = run(text, typewriter="dim")
        d0 = plates(dim, "r0")
        check("dim：译文一开始就整行画着，译文那块从 0 起就是整行译文的宽（原文追上来之前不变），比逐字显出时宽",
              _clip_at(d0[1], 0)[2] == _clip_at(d0[1], 800)[2] > _clip_at(t_on[1], 1)[2], (d0, t_on))
        txt_mode, _ = run(text, plate="text")
        tm = plates(txt_mode, "r0")
        check("plate=text：只算译文——一块，左沿是译文的；原文那段（左边）从不盖",
              len(tm) == 1 and all(_clip_at(tm[0], t)[0] > src_left + 12 or _clip_at(tm[0], t)[2] == 0 for t in (1, 3000, 5000)), tm)

        s_line = next(ln for ln in text.splitlines() if ",r1," in ln)
        sp = plates(out, "r1")
        check("single（一个框装两行原文）：一块外接矩形；第一行原文先盖，第二行（按 cap 等分的下半格）后盖；全字后盖满框高",
              ";cap=2;" in s_line and len(sp) == 1 and _clip_at(sp[0], 100)[3] <= 540 + pd
              and _clip_at(sp[0], 5000)[1] <= 500 - pd + 1 and _clip_at(sp[0], 5000)[3] >= 580, (s_line, sp))
        grown, ts_g = run(text.replace(s_line, s_line + "\\N三行原文"))
        gp = plates(grown, "r1")
        check("single 扩行（正文加到三行，框往下扩出一行）：全字后 clip 盖到扩出来的那行（只有译文）",
              len(gp) == 1 and _clip_at(gp[0], 5000)[3] > 580 + pd and any("扩出 1 行" in m for _, m in ts_g.notes), gp)

        f_line = next(ln for ln in text.splitlines() if ",r2," in ln)
        fp = plates(out, "r2")
        pos_x = float(re.search(r"\\pos\(([\d.]+),", f_line)[1])
        check("free（两个原文框、一行译文）：一块外接矩形；译文那段按 \\pos 取锚轴（居中在 \\pos 的 x 上）",
              len(fp) == 1 and _clip_at(fp[0], 5000)[0] <= pos_x <= _clip_at(fp[0], 5000)[2], (f_line, fp))

        m_line = next(ln for ln in text.splitlines() if ",r3," in ln)
        mov, _ = run(text.replace(m_line, m_line.replace(";rows;", ";rows;tw=500;", 1)))
        check("带轨迹的行人手加了打字机：照旧静态底板（clip 是屏幕坐标、不跟 \\move 走）",
              ";path=" in m_line and plates(mov, "r3") and not any("\\clip" in p for p in plates(mov, "r3")),
              plates(mov, "r3"))

        mark = d / "mark.py"
        mark.write_text("NAME = 'mark'\nCALLS = []\n\ndef tags(line, value, el):\n    if el.kind == 'plate':\n"
                        "        CALLS.append(line.region)\n        el.tail += '\\\\blur1'\n", encoding="utf-8")
        t_line = next(ln for ln in text.splitlines() if ",r0," in ln)
        mk, ts_m = run(text.replace(t_line, t_line.replace(";rows;", ";rows;mark;", 1)), fx=(str(mark),))
        calls = next(iter(ts_m.fx.values())).CALLS
        check("扩展效果的 tags 挂点：切成两块的一行，底板上调两次、两块都带它改的东西",
              calls == [0, 0] and all("\\blur1" in p for p in plates(mk, "r0")), (calls, plates(mk, "r0")))


def t_ink_shift() -> None:
    """底板按墨迹实际落在哪里算（2026-10-05 owner 截图：译文左沿冲出底板）：libass 按字格对齐，墨迹偏移 = 居中 (左留白 − 右留白)/2、
    左对齐 左留白、右对齐 −右留白；字宽表里另记留白、只查 (字体, 文本) 的地方碰不到它。"""
    print("墨迹偏移")
    from flowocr.output import export
    from flowocr.typeset import core as TS

    f = "F"
    w = {(f, "疑惑。"): 3.0, (export.SIDE, f, "疑惑。"): (0.02, 0.5)}
    tok = TS.text_rows("疑惑。")[0][0]
    check("墨迹偏移：居中 = (左留白 − 右留白)/2 × 字号；左对齐 = 左留白；右对齐 = −右留白；没量过留白是 0",
          abs(TS.ink_shift(tok, 64, f, w, "center") - (0.02 - 0.5) / 2 * 64) < 1e-9
          and abs(TS.ink_shift(tok, 64, f, w, "left") - 0.02 * 64) < 1e-9
          and abs(TS.ink_shift(tok, 64, f, w, "right") + 0.5 * 64) < 1e-9
          and TS.ink_shift(TS.text_rows("别的")[0][0], 64, f, w, "center") == 0.0)
    check("留白另记在 (SIDE, 字体, 文本) 键下：ink_side 取得到，(字体, 文本) 仍是墨迹宽",
          export.ink_side(f, "疑惑。", w) == (0.02, 0.5) and w[(f, "疑惑。")] == 3.0 and export.ink_side(f, "无", w) == (0.0, 0.0))


def t_ink_shift_plates() -> None:
    """端到端：量到的右留白让居中行的底板整体左移 (左 − 右)/2 × 字号，字的 \\pos 不动。"""
    print("墨迹偏移进底板")
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS

    def widths_with(rsb):
        def fake(pairs, known=None):
            out = fake_widths(pairs, known)
            for (f, ln) in list(out):
                if isinstance(f, str) and f != export.SIDE:
                    out[(export.SIDE, f, ln)] = (0.0, rsb)
            return out
        return fake
    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    it = SC.Item(0, 3_000_000, [[700, 900, 1220, 940]], ["一二三四。"], "line",
                 events=[{"id": 1, "box": [700, 900, 1220, 940], "t_start": 0, "t_end": 3_000_000, "region": 0, "text": "x"}],
                 region=0, translated=True, layer="body")
    got = {}
    for rsb in (0.0, 0.5):
        with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", widths_with(rsb)):
            p = Path(td) / "e.script.ass"
            SC.export_script(p, [it], doc)
            d = AF.loads(p.read_text(encoding="utf-8"))
            ts = TS.Typesetter(replace(TS.LOOKS["default"], plate_color="off", plate="text"))
            ts.run(d)
            lines = [ln for ln in d.dumps().splitlines() if ",fo-fx," in ln]
            plate = next(ln for ln in lines if "\\p1" in ln)
            text = next(ln for ln in lines if "\\p1" not in ln)
            got[rsb] = (float(plate.split("\\pos(")[1].split(",")[0]), text.split("}")[0], int(text.split("\\fs")[1].split("}")[0].split("\\")[0]))
    (x0, t0, fs), (x1, t1, _) = got[0.0], got[0.5]
    check("右留白 0.5 字号：text 底板左移 0.25 × 字号（±1 px），正文的位置和字号不变",
          abs((x0 - x1) - 0.25 * fs) <= 1 and t0 == t1, got)


def t_plate_box_tall() -> None:
    """plate=box + 打字机：正文比原文框高时，跟着字长的 clip 全字后上下沿等于静态底板（原来 clip 按原文框高裁，字冲出底板）。"""
    print("box 底板盖住字高")
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS
    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    rows = [[700, 900, 1220, 920], [700, 950, 1220, 970]]           # 框只有 20 px 高，字号按两行行距撑大
    ev = lambda i, b: {"id": i, "box": b, "t_start": 0, "t_end": 3_000_000, "region": 0, "text": "x",   # noqa: E731
                       "t_full_sampled": 2_000_000}
    it = SC.Item(0, 3_000_000, rows, ["一二三四五", "六七八九十"], "line", events=[ev(1, rows[0]), ev(2, rows[1])],
                 region=0, translated=True, layer="body")
    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        p = Path(td) / "b.script.ass"
        SC.export_script(p, [it], doc)
        d = AF.loads(p.read_text(encoding="utf-8"))
        TS.Typesetter(replace(TS.LOOKS["default"], plate_color="off", plate="box")).run(d)
        pl = next(ln for ln in d.dumps().splitlines() if ",fo-fx," in ln and "\\p1" in ln)
    import re
    x0, y0 = (int(v) for v in re.search(r"\\pos\((\d+),(\d+)\)", pl).groups())
    w, h = (int(v) for v in re.search(r"m 0 0 l (\d+) 0 \d+ (\d+)", pl).groups())
    clip = _clip_at(pl.split("\\1a&H20&", 1)[1], 9000)
    check("plate=box 打字机行：全字后 clip 的上下沿等于静态底板（比原文框高）", clip[1] == y0 and clip[3] == y0 + h and h > 20 + 2 * 6 + 50,
          (clip, (x0, y0, w, h)))


def t_plate_color() -> None:
    """底板取色（阶段 4 的 `plate_color`）：在合成小视频上颜色取自背景、不被笔画带偏（关了笔画检测守卫会响）、
    跨两种背景的底板分格、字色按底板深浅；很短的行也取到色；阶段 3 记下视频、阶段 4 照它找；找不到报错；
    `dev` 不取色；重跑、`restore` 逐字节。"""
    print("底板取色")
    import contextlib
    import io
    from dataclasses import replace
    from unittest.mock import patch

    import numpy as np

    from flowocr import provenance
    from flowocr.artifacts import tracksio
    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS
    from flowocr.typeset import platecolor as PC

    LIGHT, DARK = "80C0E0", "603020"                          # 背景（ASS 的 BBGGRR）：左半浅、右半深
    S = 1_000_000

    def lab(bgr_hex):
        return PC.bgr_to_lab(np.array([list(bytes.fromhex(bgr_hex))], np.uint8))[0]

    def de(a, b):
        return float(np.linalg.norm(lab(a) - lab(b)))

    def cells(text):
        """取色小格：(区域号, 左边 x, 初始颜色)。"""
        return [(ln.split(",")[4], int(re.search(r"\\pos\((\d+)", ln)[1]), re.search(r"\\1c&H(\w{6})&", ln)[1])
                for ln in text.splitlines() if ln.startswith("Dialogue:") and ",P.fo-fx," in ln]

    def ev(i, box, t0, t1):
        return {"id": i, "region": i, "box": box, "t_start": int(t0 * S), "t_end": int(t1 * S), "text": "あいう",
                "flags": [], "conf": 0.9, "n_obs": 10}

    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        d = Path(td)
        vid = d / "v.mkv"
        strokes = [f"drawbox=x={x}:y=152:w=5:h=26:color=0x202020:t=fill" for x in range(70, 250, 20)]     # 浅底上的黑笔画
        strokes += [f"drawbox=x={x}:y=152:w=5:h=26:color=0xF0F0F0:t=fill" for x in range(390, 570, 20)]   # 深底上的白笔画
        rgb = lambda h: h[4:] + h[2:4] + h[:2]                                    # noqa: E731
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x{rgb(LIGHT)}:s=640x360:d=3:r=30",
                        "-vf", ",".join([f"drawbox=x=320:y=0:w=320:h=360:color=0x{rgb(DARK)}:t=fill"] + strokes),
                        "-c:v", "ffv1", "-pix_fmt", "bgr0", str(vid)], check=True)
        boxes = {0: [60, 150, 260, 180], 1: [380, 150, 580, 180], 2: [220, 60, 420, 90], 3: [60, 250, 260, 280]}
        evs = [ev(0, boxes[0], 0, 2.5), ev(1, boxes[1], 0, 2.5), ev(2, boxes[2], 0, 2.5), ev(3, boxes[3], 2.6, 2.8)]
        doc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [640, 360],
               "provenance": {"main_track": "r00", "video": provenance.portable(vid)}, "events": evs,
               "regions": [{"index": i, "align": "center", "align_how": {"by": "stats"}} for i in range(4)],
               "tracks": [{"id": f"r{i:02d}", "kind": "region", "region": i, "srt": f"{i}.srt", "ui_lines": [],
                           "cues": [{"id": f"c{i}", "t_start": e["t_start"], "t_end": e["t_end"], "events": [i]}]}
                          for i, e in enumerate(evs)]}
        tracksio.validate(doc)
        with contextlib.redirect_stdout(io.StringIO()):
            draft, *_ = SC.render_script(doc, d, {"keep": "all"})
        text = draft.read_text(encoding="utf-8")
        check("阶段 3 把产物记的源视频写进字幕稿的 FlowOCR Video", f"FlowOCR Video: {provenance.portable(vid)}" in text)

        def run(txt, look="default", video=None, fx=(), **kw):
            ts = TS.Typesetter(replace(TS.LOOKS[look], **kw), TS.load_fx(fx), video=video)
            dd = AF.loads(txt)
            ts.run(dd)
            return dd.dumps(), ts

        final, ts = run(text)
        got = cells(final)
        bad = [(x, c) for _, x, c in got if de(c, LIGHT if x < 300 else DARK) > 4]
        check("小格颜色取自背景：浅底、深底、跨两色、很短的各一块，笔画（黑 / 白）不带偏颜色，每格离所在背景 ΔE ≤ 4",
              not bad and {r for r, _, _ in got} == {"r0", "r1", "r2", "r3"}, (got, bad))
        straddle = sorted((x, c) for r, x, c in got if r == "r2")
        check("跨两种背景的底板切成小格：最左一格浅、最右一格深",
              len(straddle) >= 2 and de(straddle[0][1], LIGHT) <= 4 and de(straddle[-1][1], DARK) <= 4, straddle)
        body = {ln.split(",")[4]: ln.split(",", 9)[9] for ln in final.splitlines()
                if ln.startswith("Dialogue:") and ",Body.fo-fx," in ln and "\\p1" not in ln}
        check("字色按底板深浅：浅底深色字、深底白字，描边换成底板色",
              body["r0"].startswith("{\\1c&H1A1A1A&\\3c&H") and body["r1"].startswith("{\\1c&HFFFFFF&\\3c&H")
              and de(re.search(r"\\3c&H(\w{6})&", body["r0"])[1], LIGHT) <= 4, body)
        short = [c for r, _, c in got if r == "r3"]
        check("很短的行（0.2 s，头尾各去 0.15 s 就没了）取中点那一帧，也换成取色小格；没有没取到样的提示",
              len(short) == 1 and de(short[0], LIGHT) <= 4 and not ts.notes, (short, ts.notes))
        check("底板样式 P.fo-fx 只有一份；FlowOCR Typeset 记算法版本",
              final.count("Style: P.fo-fx,") == 1 and f"plate_color={PC.VERSION}" in final)
        again, _ = run(final)
        rest = AF.loads(final)
        TS.restore(rest)
        check("取色后重跑在自己的输出上逐字节相同；restore 删掉 P.fo-fx、还原出逐字节相同的字幕稿",
              again == final and rest.dumps() == text and "P.fo-fx" not in rest.dumps())
        with patch.object(PC, "INK_THR", 255):                    # 不找笔画：笔画按背景取进来
            worst = max(de(c, LIGHT if x < 300 else DARK) for _, x, c in cells(run(text)[0]))
        check("守卫会响：不抹笔画时有格子偏离背景 ΔE > 4", worst > 4, worst)

        novid = "\n".join(ln for ln in text.splitlines() if not ln.startswith("FlowOCR Video:"))

        def err(fn):
            try:
                fn()
            except ValueError as e:
                return str(e)
            return ""
        check("字幕稿里没记视频、或记的视频找不到：当场报错，提示 --video 和 plate_color=off（不静默退回近黑）",
              "--video" in err(lambda: run(novid)) and "plate_color=off" in err(lambda: run(novid))
              and "--video" in err(lambda: run(text.replace(provenance.portable(vid), provenance.portable(d / "nope.mkv")))))
        check("--video 优先于字幕稿里记的", cells(run(novid, video=str(vid))[0]) == got)
        late = text + "\n" + next(ln for ln in text.splitlines() if ",r3," in ln and ln.startswith("Dialogue:")) \
            .replace("0:00:02.60,0:00:02.80", "0:00:03.50,0:00:04.00").replace(",r3,", ",r9,")
        lfin, lts = run(late)
        check("字幕行在视频结尾之后：不报错，那块底板留近黑并提示，别的照常取色",
              any(",r9," in ln and f"\\1c&H{TS.OVERLAY_COLOR}&" in ln for ln in lfin.splitlines())
              and any("没取到样" in n for _, n in lts.notes) and cells(lfin) == got, lts.notes)
        dev, _ = run(novid, "dev")
        off, _ = run(novid, plate_color="off")
        grid = [t for t, _ in PC.frames(str(vid), 0.0, 3.0, 64, 36)]
        odd = []
        for t0 in [0.0, 0.03, 1.28, 1.3, 1.37, 2.199999, 2.85]:
            p = PC.Ev(["Dialogue: 0", "", "", "Body", "", "0", "0", "0", "fo-fx", ""], t0, t0 + 0.04)
            wd = PC.window_of(p, 3.0)
            hit = [t for t in grid if wd[0] <= t < wd[1]]
            if len(hit) != 1 or abs(hit[0] - (t0 + 0.02)) > 0.5 / PC.FPS + 1e-6:
                odd.append((t0, wd, hit))
        check("很短的行：取样窗口里正好一帧，是离中点最近的那帧（视频结尾前的也往前挪进视频里）", not odd and grid[-1] < 3.0, odd)
        tail = text + "\n" + next(ln for ln in text.splitlines() if ",r3," in ln and ln.startswith("Dialogue:")) \
            .replace("0:00:02.60,0:00:02.80", "0:00:02.92,0:00:02.99").replace(",r3,", ",r8,")
        tfin, tts = run(tail)
        check("视频结尾前最后一个取样帧之后的短行照样取到色，不算在结尾之后",
              any(r == "r8" for r, _, _ in cells(tfin)) and not tts.notes, tts.notes)
        only = "\n".join(ln for ln in novid.splitlines() if not ln.startswith("Dialogue:")) \
            + "\nComment: 0,0:00:00.00,0:00:01.00,Body,,0,0,0,,{\\p1}m 0 0 l 10 0 10 10 0 10"
        check("没有要取色的生成底板（只有注释里的 \\p1）：不找视频、不报错", "{\\p1}m 0 0" in run(only)[0])
        body_style = next(ln for ln in text.splitlines() if ln.startswith("Style: Body,"))
        named = text.replace(body_style, body_style + "\n" + body_style.replace("Style: Body,", "Style: P,"), 1) \
            .replace(",Body,r0,", ",P,r0,", 1)
        nfin, _ = run(named)
        st = [ln.split(":", 1)[1].split(",", 1)[0].strip() for ln in nfin.splitlines() if ln.startswith("Style:")]
        nrest = AF.loads(nfin)
        TS.restore(nrest)
        check("源样式叫 P 时，它的派生样式 P.fo-fx 和底板样式不重名（底板换成 P1.fo-fx）；restore 照样逐字节",
              len(st) == len(set(st)) and "P.fo-fx" in st and "P1.fo-fx" in st and ",P1.fo-fx,r0," in nfin
              and nrest.dumps() == named, st)
        real = PC.frames

        def broken(video, s0, e0, w, h, hw=False, **kw):
            if hw:
                raise RuntimeError("替身：NVDEC 中途坏了")
            return real(video, s0, e0, w, h, hw, **kw)
        with patch.object(PC, "hw_works", lambda *a: True), patch.object(PC, "frames", broken):
            try:
                run(text)
                said = ""
            except RuntimeError as e:
                said = str(e)
        check("NVDEC 试解过了、中途解码失败：报错退出，不写半成品", said == "替身：NVDEC 中途坏了", said)
        flip = d / "flip.mkv"                                     # 0.1 s 之前浅、之后深
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x{rgb(LIGHT)}:s=640x360:d=1:r=30",
                        "-vf", f"drawbox=x=0:y=0:w=640:h=360:color=0x{rgb(DARK)}:t=fill:enable='gte(t,0.1)'",
                        "-c:v", "ffv1", "-pix_fmt", "bgr0", str(flip)], check=True)
        first = "\n".join(ln for ln in text.splitlines() if not ln.startswith("Dialogue:") or ",r3," in ln) \
            .replace("0:00:02.60,0:00:02.80", "0:00:00.00,0:00:00.04")
        fc = cells(run(first, video=str(flip))[0])
        check("视频开头的短行（0–0.04 s）取的是 0 s 那帧（解码起点不为负，帧的时刻不错位）",
              len(fc) == 1 and de(fc[0][2], LIGHT) <= 4, fc)
        check("dev、plate_color=off 不取色、不读视频：没有 P.fo-fx", "P.fo-fx" not in dev + off and "\\p1" in dev + off)
        check("plate_color 只认 off / on", raises(lambda: TS.look_of("default", {"plate_color": "auto"}), ValueError))

        r0 = next(ln for ln in text.splitlines() if ln.startswith("Dialogue:") and ",r0," in ln)
        framed = text.replace(r0, r0.replace(";plate", ";plate;frame=0000FF", 1))
        fxs = (str(ROOT / "examples" / "fx_minimal.py"),)
        frame_of = lambda t: [ln for ln in t.splitlines() if "\\3c&H0000FF&" in ln]      # noqa: E731
        f_on, f_off = run(framed, fx=fxs)[0], run(framed, fx=fxs, plate_color="off")[0]
        check("扩展效果画的框（也是 \\p1 矩形）不当底板：不切格、不换样式、不改色，和不取色时逐字相同；取色照旧",
              ";plate;frame" in framed and len(frame_of(f_on)) == 1 and frame_of(f_on) == frame_of(f_off)
              and cells(f_on) == got, frame_of(f_on))
        mine = text.replace(r0, r0.replace("あいう", "{\\1c&H00FF00&}あいう", 1))
        mb = next(ln for ln in run(mine)[0].splitlines() if ",r0," in ln and ",Body.fo-fx," in ln and "\\p1" not in ln)
        check("行首手写的 \\1c 写在自动字色之后（libass 取后写的），压过自动黑白字",
              0 <= mb.find("\\1c&H1A1A1A&") < mb.find("\\1c&H00FF00&"), mb)
        yellow = "\n".join(ln.replace(",&H00FFFFFF,", ",&H0000FFFF,", 1) if ln.startswith("Style: Body,") else ln
                             for ln in text.splitlines())
        yfin = run(yellow)[0]
        yb = [ln for ln in yfin.splitlines() if ",Body.fo-fx," in ln and "\\p1" not in ln and ln.startswith("Dialogue:")]
        check("人改过样式主色（Body 改成黄）：这个样式的正文不自动改字色、描边，底板照样取色",
              ",&H0000FFFF," in yellow and yb and not any("\\1c&H1A1A1A&" in ln or "\\1c&HFFFFFF&\\3c" in ln for ln in yb)
              and cells(yfin) == got, yb[:1])
        nostyle = "\n".join(ln for ln in text.splitlines() if not ln.startswith("Style:"))
        ns = run(nostyle)[0]
        nscell = [ln for ln in ns.splitlines() if ln.startswith("Dialogue:") and "\\blur" in ln and "\\p1" in ln]
        check("字幕稿没有 Style 行：不写底板样式，小格留在原样式、自带 \\an7\\bord0\\shad0（位置不跑）",
              nscell and "Style: P" not in ns and all("\\an7" in ln and "\\bord0" in ln for ln in nscell), nscell[:2])
        mv = PC.Ev(["0", "0:00:00.00", "0:00:02.50", "Body.fo-fx", "", "0", "0", "0", "fo-fx",
                    "{\\an7\\move(60,150,380,150,0,2000)\\p1\\bord0\\shad0\\1c&H101010&\\1a&H20&}m 0 0 l 200 0 200 30 0 30{\\p0}"],
                   0.0, 2.5)
        mres = PC.apply(["Comment: x", mv.line()], ["src", "plate"], str(vid), 640, 360)
        mcols = [re.findall(r"\\1c&H(\w{6})&", ln) for ln in mres.lines[1:]]
        check("带 \\move 的底板沿轨迹取色：从浅底走到深底，起始色浅、最后一个关键帧深",
              mres.sampled == 1 and mcols and all(de(c[0], LIGHT) <= 4 and de(c[-1], DARK) <= 4 for c in mcols), mcols)

        def right(chain, t):
            """\\clip 链在 t 毫秒时的右边沿（起始矩形 + 逐步线性过渡）。"""
            r = int(PC.CLIP_INIT.search(chain)[3])
            for m in PC.CLIP_STEP.finditer(chain):
                a, b, nr = int(m[1]), int(m[2]), int(m[5])
                r = r if t < a else nr if t >= b else r + (nr - r) * (t - a) / (b - a)
            return r
        chain = "{\\clip(100,0,100,50)" + "".join("\\t(%d,%d,\\clip(100,0,%d,50))" % (i * 100, i * 100 + 80, 100 + 50 * (i + 1))
                                                for i in range(8)) + "\\p1}m"
        diff = []
        for x0, x1 in [(90, 140), (180, 230), (260, 300), (480, 520), (600, 650)]:
            cut = PC.trim_clip(chain, x0, x1)
            seen = lambda c, t: min(max(right(c, t), x0), x1) if PC.CLIP_INIT.search(c) else x1     # noqa: E731
            diff += [(x0, t, seen(chain, t), seen(cut, t)) for t in range(0, 1000, 10) if abs(seen(chain, t) - seen(cut, t)) > 1e-6]
        check("打字机的 \\clip 链按格裁剪：每格在每个时刻露出的部分和整条链一样（写到之前藏着、写过之后整格可见）",
              not diff and len(PC.CLIP_STEP.findall(PC.trim_clip(chain, 180, 230))) < 8, diff[:5])
        va = d / "va.mkv"                                         # 音轨 5 s、画面 3 s
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(vid), "-f", "lavfi", "-i", "sine=d=5", "-c:v", "copy",
                        "-c:a", "flac", str(va)], check=True)
        afin, ats = run(late, video=str(va))
        check("音轨比画面长：时长按视频流算，落在音轨尾巴上的行留近黑并提示，不报解码错",
              abs(PC.duration(str(va)) - 3.0) < 0.05 and any("没取到样" in n for _, n in ats.notes)
              and cells(afin) == got, (PC.duration(str(va)), ats.notes))

        check("NVDEC 试解：ffv1 解不了，返回 False（整趟软解），不抛", PC.hw_works(str(vid), 0.0, 640, 360) is False)
        v264 = d / "v.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(vid), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v264)], check=True)
        if PC.hw_works(str(v264), 0.0, 640, 360):
            soft = [f for _, f in PC.frames(str(v264), 0.5, 2.5, 640, 360)]
            hard = [f for _, f in PC.frames(str(v264), 0.5, 2.5, 640, 360, hw=True)]
            check("NVDEC 和软解解出的帧逐字节相同（NVDEC 下传后转 yuv420p，和软解同一条转色路径）",
                  len(soft) == len(hard) == 10 and all((a == b).all() for a, b in zip(soft, hard)), (len(soft), len(hard)))
        else:
            print("  SKIP  这台机器开不了 NVDEC，不核两条路逐字节相同")


def t_placeholder_fill() -> None:
    """阶段 3 标的占位 `[`编号:值]`，阶段 4 按图例换成值、空的删掉并收拾标点；还原后字幕稿逐字节不变。"""
    print("占位：阶段 4 填值")
    import re
    from dataclasses import replace

    from flowocr import placeholders as PH
    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS
    from unittest.mock import patch
    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    mk = PH.Marks({"{NICKNAME}": {"value": "荧", "how": "测试"}})
    rows = [[700, 900, 1220, 940]]
    it = lambda s, text: SC.Item(s, s + 1_000_000, rows, [mk.mark(text)], "name", region=0, translated=True,  # noqa: E731
                                 layer="name")
    items = [it(0, "{NICKNAME}，你好"), it(2_000_000, "{X}，走吧"), it(4_000_000, "嗯，{NICKNAME}。")]
    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        p = Path(td) / "p.script.ass"
        SC.export_script(p, items, doc, info=mk.legend())
        src = p.read_text(encoding="utf-8")
        d = AF.loads(src)
        ts = TS.Typesetter(replace(TS.LOOKS["default"], plate_color="off"))
        ts.run(d)
        out = d.dumps()
        TS.restore(d)
        back = d.dumps()
    gen = [re.sub(r"\{[^}]*\}", "", ln.split(",", 9)[9]) for ln in out.splitlines() if ",fo-fx," in ln and "\\p1" not in ln]
    check("字幕稿正文是标记、图例在 [Script Info]；阶段 4 换成值、空的连同落单的逗号删掉；还原逐字节相同",
          "[`0:荧]，你好" in src and "FlowOCR Placeholder 1: {X} 匹配器没给值" in src
          and gen == ["荧，你好", "走吧", "嗯，荧。"] and ts.fills == {("{NICKNAME}", "荧"): 2, ("{X}", ""): 1}
          and back == src, (gen, ts.fills))


def t_game_style() -> None:
    """剧本的行内样式（`flowocr.richtext`）：规范写法、投回纯文字的行、阶段 3 写成 ASS / SRT 标签、阶段 4 的字色。"""
    print("剧本样式")
    import re
    from dataclasses import replace

    import numpy as np

    from flowocr import placeholders as PH
    from flowocr import richtext as RT
    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS
    from flowocr.typeset import platecolor as PC
    from unittest.mock import patch

    got = [RT.from_unity(x) for x in ("<b>x<i>y</b>z</i>", "a<color=#yellow>b</color>c", "<color=cyan>a</color>",
                                       "<color=#00e1fff>a</color>", "多余</b>闭<i>没闭合", "<b></b>空")]
    check("规范写法：交叉的拆开重开、认不出的颜色只留字、Unity 颜色名和七位笔误认得、配不上对的删掉、没闭合的补上、空的一对去掉",
          got == ["<b>x<i>y</i></b><i>z</i>", "abc", "<color=#00ffff>a</color>", "<color=#00e1ff>a</color>", "多余闭<i>没闭合</i>", "空"], got)
    check("投回纯文字：非空白的字逐个对上，开标签贴后一个字、闭标签贴前一个字，\\N 当空白；字对不上是 None",
          RT.project("<b>ab</b> c<i>d\ne</i>", "ab\\Ncd e") == "<b>ab</b>\\Nc<i>d e</i>" and RT.project("<b>ab</b>", "ax") is None)
    check("投回：存进产物的那份本身带 \\N（剧本条内换行），按行投、并成一行投都对得上，行的布局按目标",
          RT.project("<color=#ffcc00>第一行</color>\\N第二行", "第一行\n第二行") == "<color=#ffcc00>第一行</color>\n第二行"
          and RT.project("<color=#ffcc00>第一行</color>\\N第二行", "第一行第二行") == "<color=#ffcc00>第一行</color>第二行")
    got = [RT.from_unity(x) for x in ("a<b> </b>c", "a<color=#FFB78C>\r\n</color>b")]
    check("只包着空白的一对去掉（绝区零逐字上色的换行）：不存成先闭后开、样式不漏到后面、写 ASS 不崩",
          got == ["a c", "a\r\nb"] and RT.to_ass("a</color>b", export.ass_text) == "ab"
          and RT.project("<b>x</b><i> </i>y", "x y") == "<b>x</b> y", got)
    got = RT.to_ass("<color=#dbc291>金<b>粗</b></color>白\n<i>斜</i>{x}", export.ass_text)
    check("ASS：#rrggbb 写成 \\1c&HBBGGRR&，闭标签回到外层（最外层是空的 \\1c）、只写变了的几项；正文的花括号照旧转义",
          got == "{\\1c&H91C2DB&}金{\\b1}粗{\\1c\\b0}白\\N{\\i1}斜{\\i0}(x)", got)

    it = {"layer": "choice", "start_us": 0, "end_us": 1, "rows": [[0, 0, 10, 10]], "events": [],
          "cn": "{NICKNAME}，你好呀", "cn_rich": "{NICKNAME}，<b>你好</b>呀"}
    on = SC._project_one(it, {"events": []}, "cn", set(), {}, PH.Marks({"{NICKNAME}": {"value": "荧"}}, "cn"), True)
    mk = PH.Marks({"{NICKNAME}": {"value": "荧"}}, "cn")
    off = SC._project_one(it, {"events": []}, "cn", set(), {}, mk, False)
    check("阶段 3 取文本：style=on 记带样式的原句（占位已标），行照旧是纯文字；off 没有样式；占位只数一次",
          on.styled == "[`0:荧]，<b>你好</b>呀" and on.lines == ["[`0:荧]，你好呀"] and off.styled is None
          and off.lines == on.lines and dict(mk.n) == {0: 1}, (on, off))
    body2 = dict(it, layer="body", rows=[[0, 0, 100, 10], [0, 20, 100, 30]], cn="第一行\\N第二行", cn_rich="<color=#ffcc00>第一行</color>\\N第二行")
    two = SC._project_one(body2, {"events": []}, "cn", set(), {}, PH.Marks(), True)
    check("阶段 3：带条内换行的剧本行，重分成两行后样式投得上（不悄悄退回纯文字）",
          two.lines == ["第一行", "第二行"] and SC.styled_lines(two, two.lines) == ["<color=#ffcc00>第一行</color>", "第二行"],
          (two.lines, SC.styled_lines(two, two.lines)))

    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    rows = [[700, 900, 1220, 940], [700, 950, 1220, 990]]
    items = [SC.Item(0, 1_000_000, rows, ["金色的话", "第二行"], "choice", region=0, translated=True, layer="choice",
                     source="金色的话第二行", styled="<color=#dbc291>金色的<b>话第</b></color>二行"),
             SC.Item(2_000_000, 3_000_000, rows[:1], ["没有样式"], "choice", region=0, translated=True, layer="choice")]
    with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
        p = Path(td) / "p.script.ass"
        n, _w = SC.export_script(p, items, doc)
        src = p.read_text(encoding="utf-8")
        d = AF.loads(src)
        TS.Typesetter(replace(TS.LOOKS["dev"], plate_color="off")).run(d)
        out = d.dumps()
    body = [ln for ln in src.splitlines() if ln.startswith("Dialogue:")]
    check("字幕稿：样式按重分过的行投回去、跨行的标签在下一行照样生效；没样式的照旧纯文字；计数",
          "{\\1c&H91C2DB&}金色的{\\b1}话\\N第{\\1c\\b0}二行" in body[0]
          and body[1].endswith("}没有样式") and n["styled"] == 1, body)
    check("SRT 是基础输出：带样式的条目照样出纯文字", SC.srt_text(items[0]) == ["金色的话第二行"], SC.srt_text(items[0]))
    gen = [ln for ln in out.splitlines() if ",fo-fx," in ln and "\\p1" not in ln and "金色" in ln]
    check("阶段 4：剧本的字色标签照画；dev 给整行定了字色时，闭标签（空的 \\1c）回到这一行的字色，不回样式的白",
          gen and all("\\1c&H91C2DB&" in ln and not re.search(r"\\1c(?=[\\}])", ln.split("}", 1)[1]) for ln in gen), gen)

    light, dark = np.array([90.0, 0.0, 0.0]), np.array([20.0, 0.0, 0.0])
    t = "{\\an5}金{\\1c&H91C2DB&}色{\\1c}字{\\1c&H00FF00&}人"
    got_l, got_d = PC.recolor_text(t, light), PC.recolor_text(t, dark)
    check("底板取色：行内有值的颜色（剧本的、人写的）原样不动；闭标签（空的 \\1c）回到这一行的深 / 白字",
          all("\\1c&H91C2DB&" in g and "\\1c&H00FF00&" in g for g in (got_l, got_d))
          and "{\\1c&H1A1A1A&}字" in got_l and "{\\1c&HFFFFFF&}字" in got_d, (got_l, got_d))

    legend = {0: "{NICKNAME}"}
    got = [PH.apply(x, legend)[0] for x in ("{\\an5}{\\1c&H91C2DB&}[`0:]{\\1c}，你好", "{\\an5}{\\1c&H91C2DB&}[`0:荧]{\\1c}，你好",
                                            "{\\an5}[`0:]{\\1c&H91C2DB&}，你好{\\1c}")]
    check("空占位被剧本样式整段包着：那一对 tag 块一起去掉、落单的逗号照收拾；有值的照留；只认只开 / 只关样式的块，"
          "行首定位块不碰（占位后面紧接着开样式的，逗号隔着 tag 块收拾不到，已知限度）",
          got == ["{\\an5}你好", "{\\an5}{\\1c&H91C2DB&}荧{\\1c}，你好", "{\\an5}{\\1c&H91C2DB&}，你好{\\1c}"], got)


def t_game_ruby() -> None:
    """剧本的 Ruby（第一期：存、写标记，阶段 4 不画）：规范写法、投回、只剥一种、写成注释块、重分行不拆、阶段 4 原样带到最终 ASS。"""
    print("剧本 Ruby")
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr import placeholders as PH
    from flowocr import richtext as RT
    from flowocr.output import export
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS

    got = [RT.from_unity(x) for x in ('<ruby="a">x<ruby=b>y</ruby>z</ruby>', "<b>x<ruby=R>y</b>z</ruby>", "<ruby=R>a<b>b</ruby>c</b>",
                                       '<ruby="">x</ruby>', "<ruby=a> </ruby>", "<ruby=a>没闭合", "多余</ruby>", "<ruby=' a\n b '>x</ruby>")]
    check("规范写法·Ruby：嵌套的里层丢掉、和样式交叉时拆样式不拆 Ruby、读音空的 / 正文是空白的不要、没闭合的补上、落单的闭标签删掉、读音的空白并成一个",
          got == ["<ruby=a>xyz</ruby>", "<b>x</b><ruby=R><b>y</b>z</ruby>", "<ruby=R>a<b>b</b></ruby><b>c</b>", "x", " ",
                  "<ruby=a>没闭合</ruby>", "多余", "<ruby=a b>x</ruby>"], got)
    t = "<b>粗</b><ruby={TEXTJOIN#1}>正文</ruby>后"
    check("plain 连读音删掉；select 只剥一种；Ruby 的正文在纯文字里的范围",
          RT.plain(t) == "粗正文后" and RT.select(t, style=False) == "粗<ruby={TEXTJOIN#1}>正文</ruby>后"
          and RT.select(t, ruby=False) == "<b>粗</b>正文后" and RT.select(t, False, False) == RT.plain(t)
          and RT.ruby_spans(t) == [(1, 3)], (RT.select(t, style=False), RT.ruby_spans(t)))
    check("投回纯文字：Ruby 和别的标签一样贴着字；只包着空白的一对不要",
          RT.project("<ruby=かんじ>漢字</ruby>です", "漢字\nです") == "<ruby=かんじ>漢字</ruby>\nです"
          and RT.project("a<ruby=x>b</ruby>c", "a b c") == "a <ruby=x>b</ruby> c"
          and RT.project("a<ruby=x> </ruby>b", "a b") == "a b")
    got = [RT.to_ass(x, export.ass_text) for x in ("<b>粗<ruby=x>体</ruby></b>后", "<ruby=x><b>体</b></ruby>后",
                                                    "<ruby=a{b}\\c>字</ruby>", "<color=#dbc291>金<ruby=き>金</ruby></color>")]
    check("ASS：Ruby 写成单独的注释块 {ruby:读音} … {/ruby}，就在原位、不并进样式块（样式块在它前面先写出来）；读音里的花括号、反斜杠换成全角",
          got == ["{\\b1}粗{ruby:x}体{/ruby}{\\b0}后", "{ruby:x}{\\b1}体{/ruby}{\\b0}后", "{ruby:a｛b｝＼c}字{/ruby}",
                  "{\\1c&H91C2DB&}金{ruby:き}金{/ruby}"]
          and not any(re.search(r"\{[^{}]*\\[^{}]*ruby|\{ruby:[^{}]*\\", g) for g in got), got)

    widths = [5, 5]
    plain_cut = SC.overlay_lines("一二三四五六", widths)
    kept = SC.overlay_lines("一二三四五六", widths, "一二<ruby=x>三四</ruby>五六")
    brk = SC.overlay_lines("一二三\\N四五六", widths, "一二<ruby=x>三\\N四</ruby>五六")
    check("重分行：Ruby 的正文当一个字、行界只落在它前后（按比例取近的一边）；它里面的剧本换行去掉；没给带标签的那份照旧",
          plain_cut == ["一二三", "四五六"] and kept == ["一二", "三四五六"] and brk == ["一二", "三四五六"], (plain_cut, kept, brk))
    it = {"layer": "body", "start_us": 0, "end_us": 1, "rows": [[0, 0, 100, 10], [0, 20, 100, 30]], "events": [],
          "cn": "一二三四五六", "cn_rich": "<b>一二</b><ruby={NICKNAME}>三四</ruby>五六"}
    mk = PH.Marks({"{NICKNAME}": {"value": "荧"}}, "cn")
    both = SC._project_one(it, {"events": []}, "cn", set(), {}, mk, True, True)
    check("阶段 3：行（纯文字）和投回的带标签的行一致，Ruby 整个在一行里；读音里的占位照样标",
          both.lines == ["一二", "三四五六"] and SC.styled_lines(both, both.lines) == ["<b>一二</b>", "<ruby=[`0:荧]>三四</ruby>五六"]
          and dict(mk.n) == {0: 1}, (both.lines, SC.styled_lines(both, both.lines)))
    mk = PH.Marks({"{NICKNAME}": {"value": "荧"}}, "cn")
    r_only = SC._project_one(it, {"events": []}, "cn", set(), {}, PH.Marks(), False, True)
    s_only = SC._project_one(it, {"events": []}, "cn", set(), {}, mk, True, False)
    none = SC._project_one(it, {"events": []}, "cn", set(), {}, PH.Marks(), False, False)
    check("阶段 3：style=off ruby=on 只留 Ruby，反过来只留样式（读音里的占位不标、不进图例），两个都关没有标签；纯文字的行都一样",
          r_only.styled == "一二<ruby=[`0:]>三四</ruby>五六" and s_only.styled == "<b>一二</b>三四五六" and none.styled is None
          and mk.ids == {} and r_only.lines == both.lines and none.lines == plain_cut, (r_only.styled, s_only.styled, mk.ids))
    lit = SC._project_one({**it, "cn": "按<b>键", "cn_rich": None}, {"events": []}, "cn", set(), {}, PH.Marks(), True, True)
    check("阶段 3：没有带样式那份时纯文字照原样，字面的 <b> 是字、不当标签", lit.styled is None and lit.lines[0] + "".join(lit.lines[1:]) == "按<b>键",
          (lit.styled, lit.lines))
    try:
        SC.build_items({"schema": "x"}, {"ruby": "yes"})
        bad = False
    except ValueError as e:
        bad = "ruby" in str(e)
    check("ruby 选项只认 on / off；叠加预设把它交给阶段 3",
          bad and SC.RUBY_MODES == ("on", "off") and "ruby" in __import__("flowocr.output.presets._overlay", fromlist=["x"]).STAGE3)

    toks = [[("tags", "ruby:x"), ("ch", "a", None), ("tags", "/ruby"), ("tags", "\\1c&H0000FF&")], [("ch", "b", None)]]
    rows, plain, lead, _k = TS.text_rows("{\\an5\\pos(1,2)}{ruby:x}漢{/ruby}字")
    rows2, _p, lead2, _k = TS.text_rows("{ruby:x}漢{/ruby}字")
    check("阶段 4：Ruby 标记块是行内块（在正文开头也不当行首块）、不是状态（逐行摆时不补到下一行开头）",
          TS.carry_rows(toks)[1] == [("tags", "\\1c&H0000FF&"), ("ch", "b", None)]
          and rows[0][0] == ("tags", "ruby:x") and lead == "" and plain == ["漢字"]
          and rows2[0][0] == ("tags", "ruby:x") and lead2 == "", (rows, lead, rows2, lead2))
    legend = {0: "{NICKNAME}"}
    got = [PH.apply(x, legend)[0] for x in ("{\\an5}{ruby:[`0:]}漢字{/ruby}です", "{\\an5}{ruby:[`0:荧]}漢字{/ruby}",
                                            "{ruby:[`0:]、三月七}失散{/ruby}", "{\\an5}{\\1c&H91C2DB&}{ruby:x}[`0:]{/ruby}{\\1c}，你好",
                                            "{\\an5}[`0:]，{ruby:x}漢{/ruby}字", "嗯，[`0:]，{ruby:x}走{/ruby}吧")]
    check("阶段 4 换占位：读音里的标记照换；读音换空了 Ruby 标记去掉、正文留着；正文删空的连同包着它的样式块去掉、标点照收拾；紧挨着 Ruby 标记的空占位照收拾标点",
          got == ["{\\an5}漢字です", "{\\an5}{ruby:荧}漢字{/ruby}", "{ruby:三月七}失散{/ruby}", "{\\an5}你好",
                  "{\\an5}{ruby:x}漢{/ruby}字", "嗯，{ruby:x}走{/ruby}吧"], got)

    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    rows = [[700, 900, 1220, 940], [700, 950, 1220, 990]]
    styled = "<color=#dbc291>金色<ruby=きん>的话</ruby></color>\n<ruby=に>第二</ruby>行"
    mk_item = lambda st: SC.Item(0, 1_000_000, rows, ["金色的话", "第二行"], "line", region=0, translated=True, layer="body",  # noqa: E731
                                 source="金色的话第二行", styled=st, events=[])

    def run(st: str | None) -> tuple[str, list[str], dict]:
        with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
            p = Path(td) / "p.script.ass"
            n, _w = SC.export_script(p, [mk_item(st)], doc)
            src = p.read_text(encoding="utf-8")
            d = AF.loads(src)
            TS.Typesetter(replace(TS.LOOKS["dev"], plate_color="off")).run(d)
        return src, [ln for ln in d.dumps().splitlines() if ",fo-fx," in ln and "\\p1" not in ln], n
    src, gen, n = run(styled)
    _s, ref, n0 = run(RT.select(styled, ruby=False))
    body = [ln for ln in src.splitlines() if ln.startswith("Dialogue:")][0]
    texts = [ln for ln in gen if "金" in ln or "第二" in ln]
    check("字幕稿 + 阶段 4：Ruby 标记写进字幕稿；最终 ASS 的正文行里没有标记、和不带 Ruby 的那次逐字相同（标记没补到下一行开头）；计数分开",
          "金色{ruby:きん}的话{/ruby}" in body and len(texts) == 2 and not any("ruby" in ln for ln in gen)
          and texts == [ln for ln in ref if "金" in ln or "第二" in ln]
          and n["ruby"] == 2 and n["styled"] == 1 and n0["ruby"] == 0, (body, texts, n))


def t_game_ruby_draw() -> None:
    """剧本的 Ruby（第二期：阶段 4 画）：读音画在正文那几个字的墨迹中心上方、字号按行缩放 / 缩到放得下 / 有下限 / 推进画面；
    整块画的按行等分，`\\move` 和阅读面板不画；底板并进读音（静态、跟着字长）；打字机、`dim`、按轨迹分段跟着正文；
    字色跟着正文（`dev` 的黄字、底板取色）；正文里不再有标记；没有 Ruby 的行和不带 Ruby 的那次逐字节相同。"""
    print("剧本 Ruby：阶段 4 画")
    import subprocess
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr import richtext as RT
    from flowocr.output import export, layout
    from flowocr.output import script as SC
    from flowocr.typeset import assfile as AF
    from flowocr.typeset import core as TS
    from flowocr.typeset import ruby as RB

    doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {"src_fps": 30.0}}
    rows = [[700, 900, 1220, 940], [700, 950, 1220, 990]]
    styled = "<color=#dbc291>金色<ruby=きん>的话</ruby></color>\n<ruby=に>第二</ruby>行"
    pad, gap = layout.OVERLAY_PAD, RB.RUBY_GAP

    def item(st, rows=rows, lines=("金色的话", "第二行"), region=0, **kw):
        return SC.Item(0, 1_000_000, rows, list(lines), "line", region=region, translated=True, layer="body",
                       source="".join(lines), styled=st, events=[], **kw)

    def draft(items) -> str:
        with tempfile.TemporaryDirectory() as td, patch.object(export, "ink_widths", fake_widths):
            p = Path(td) / "p.script.ass"
            SC.export_script(p, items, doc)
            return p.read_text(encoding="utf-8")

    def run(text, look="dev", video=None, **kw):
        ts = TS.Typesetter(replace(TS.LOOKS[look], **{"plate_color": "off", **kw}), video=video)
        d = AF.loads(text)
        with patch.object(export, "ink_widths", fake_widths):
            ts.run(d)
        return [ln for ln in d.dumps().splitlines() if ",fo-fx," in ln], ts

    def body(ln):
        return ln.split(",", 9)[9]

    def at(ln):                                        # \pos 或 \move 的起点
        m = re.search(r"\\(?:pos|move)\(([-\d.]+),([-\d.]+)", ln)
        return float(m[1]), float(m[2])

    def fs(ln):
        return int(re.search(r"\\fs(\d+)", ln)[1])

    def split(gen):                                    # 底板、正文、读音（\an2）；区域号是 \an9
        return ([ln for ln in gen if "\\p1" in ln], [ln for ln in gen if "\\p1" not in ln and "\\an2" not in ln and "\\an9" not in ln],
                [ln for ln in gen if "\\an2" in ln])

    def rect(ln):
        (x0, y0), m = at(ln), re.search(r"m 0 0 l ([\d.]+) 0 [\d.]+ ([\d.]+)", ln)
        return [x0, y0, x0 + float(m[1]), y0 + float(m[2])]

    def ruby_rect(ln, n):                              # 读音 n 个字（fake 字宽：每字 1 个字号）
        (x, y), s = at(ln), fs(ln)
        return [x - n * s / 2, y - s, x + n * s / 2, y]

    def near(a, b, tol=1.0):
        return abs(a - b) <= tol

    # ---- 按原框逐行摆 ----
    text = draft([item(styled)])
    gen, ts = run(text)
    plates, texts, rub = split(gen)
    (x0, y0), (x1, y1), sz = at(texts[0]), at(texts[1]), fs(texts[0])
    gh = sz / export.h_ratio(export.FONT_CN)
    want0 = (x0 - 4 * sz / 2 + 4 * sz - 2 * sz / 2, y0 - gh / 2 - gap * sz)        # 金色[的话]：行左沿 + 宽(前缀+正文) − 宽(正文) / 2
    want1 = (x1 - 3 * sz / 2 + 2 * sz - 2 * sz / 2, y1 - gh / 2 - gap * sz)        # [第二]行
    check("逐行摆：每处 Ruby 一条 \\an2 的读音，横向在正文那几个字的中心、底边在这一行字形顶往上 RUBY_GAP 个字号，字号是这一行的一半；计数",
          len(rub) == 2 and all(near(a, b) for a, b in zip(at(rub[0]) + at(rub[1]), want0 + want1))
          and at(rub[0])[1] < y0 - gh / 2 and fs(rub[0]) == fs(rub[1]) == int(sz * RB.RUBY_SCALE)
          and body(rub[0]).endswith("きん") and body(rub[1]).endswith("に") and ts.counts.get("ruby") == 2,
          (rub, want0, want1))
    rs = "\\fs%d" % fs(rub[0])
    check("读音前面接正文开头处生效的样式块（剧本的字色、从上一行补过来的那块），块后再写一次读音的字号；正文里没有标记",
          "{\\1c&H91C2DB&%s}きん" % rs in body(rub[0]) and "{\\1c&H91C2DB&\\1c%s}に" % rs in body(rub[1])
          and not any("ruby" in ln for ln in gen), rub)
    gen_r, _ = run(text.replace("{ruby:きん}", "{\\r}{ruby:きん}").replace("{ruby:に}第二{/ruby}", "第二{/ruby}"))
    _p, _t, rub_r = split(gen_r)
    gen_i, _ = run(text.replace("{ruby:に}第二{/ruby}", "{ruby:に}{\\b1}第二{\\b0}{/ruby}"))
    rub_i = split(gen_i)[2]
    check("开在 Ruby 里、正文第一个字前的样式块（星铁 {RUBY_B#<color=…>…）也接到读音前",
          len(rub_i) == 2 and body(rub_i[1]).endswith("\\b1%s}に" % rs), rub_i)
    check("人写的 \\r 不把读音退回正文大小；落单的 {/ruby} 也从正文里去掉",
          len(rub_r) == 1 and fs(rub_r[0]) == fs(rub[0]) and body(rub_r[0]).endswith("\\r%s}きん" % rs)
          and not any("ruby" in ln for ln in gen_r), gen_r)
    r0, r1 = ruby_rect(rub[0], 2), ruby_rect(rub[1], 1)
    p0, p1 = rect(plates[0]), rect(plates[1])
    check("底板（rows）：这一行那块往上并进读音（外扩边距）；下一行的读音落在上一行那块里时上沿停在上一行的下沿，两块不叠",
          near(p0[1], r0[1] - pad) and p0[0] <= r0[0] - pad and p0[2] >= r0[2] + pad and near(p1[1], p0[3]), (p0, p1, r0, r1))
    gen_b, _ = run(text, plate="box")
    pb = rect(split(gen_b)[0][0])
    check("底板（box）：一整块并进全部读音", len(split(gen_b)[0]) == 1 and pb[1] <= r0[1] - pad + 1 and pb[0] <= r1[0] - pad, pb)

    other = item(None, rows=[[700, 600, 1220, 640]], lines=("没有注音",), region=1)
    with_r, _ = run(draft([item(styled), other]))
    no_r, _ = run(draft([item(RT.select(styled, ruby=False)), other]))
    check("没有 Ruby 的行和不带 Ruby 的那次逐字节相同；带 Ruby 的那条的正文行也相同（只多了读音、底板并进去）",
          [ln for ln in with_r if ",r1," in ln] == [ln for ln in no_r if ",r1," in ln]
          and split(with_r)[1] == split(no_r)[1] and not split(no_r)[2], (with_r, no_r))

    # ---- 缩字号、下限、推进画面 ----
    def one(st, rows1=([700, 950, 1220, 990],), lines1=("第二行",), **kw):
        gen, _ = run(draft([item(st, rows=[list(r) for r in rows1], lines=lines1, **kw)]))
        return split(gen)
    _p, (t_s,), (r_s,) = one("<ruby=一二三四五六七八>第二</ruby>行")
    _p, (t_f,), (r_f,) = one("<ruby=" + "注" * 20 + ">第二</ruby>行")
    s_s, s_f = fs(t_s), fs(t_f)
    check("读音比这一行正文宽：缩到放得下；到 RUBY_MIN_SCALE 还放不下就照下限画、仍以正文为中心（两边伸出去）",
          fs(r_s) == int(3 * s_s / 8) and fs(r_f) == int(RB.RUBY_MIN_SCALE * s_f)
          and near(at(r_f)[0], at(t_f)[0] - 3 * s_f / 2 + s_f) and 20 * fs(r_f) > 3 * s_f, (r_s, r_f))
    _p, (t_c,), (r_c,) = one("<ruby=很长很长的读音注释>一</ruby>二", rows1=([10, 950, 130, 990],), lines1=("一二",), align="left")
    check("读音超出画面：往里推到 EDGE_PAD", "\\an4" in t_c and near(at(r_c)[0], layout.EDGE_PAD + 9 * fs(r_c) / 2), (t_c, r_c))

    # ---- 整块画：single、人挪过的 \pos、\move、阅读面板 ----
    single = draft([item(styled, rows=[[700, 900, 1220, 990]])]).replace("fo:box=", "fo:cap=2;box=")   # 一个框本来装两行
    _p, (t_1,), r_1 = split(run(single)[0])
    (x, y), s1 = at(t_1), fs(t_1)
    g1 = s1 / export.h_ratio(export.FONT_CN)
    want = [(x - 2 * s1 + 3 * s1, y - s1 / 2 - g1 / 2 - gap * s1), (x - 1.5 * s1 + s1, y + s1 / 2 - g1 / 2 - gap * s1)]
    check("一个框画几行（single）：几行按锚点竖直等分（行距按字号），各行按对齐算左沿",
          "\\N" in body(t_1) and len(r_1) == 2 and all(near(a, b) for p, w in zip(r_1, want) for a, b in zip(at(p), w)), (r_1, want))
    moved = text.replace("\\pos(960,945)", "\\pos(800,500)")
    gen_m, ts_m = run(moved)
    pm, (t_m,), r_m = split(gen_m)
    sm = fs(t_m)
    gm = sm / export.h_ratio(export.FONT_CN)
    check("人挪过 \\pos（整块画）：读音按人给的锚点算；底板还在原文框上、不并进读音",
          len(r_m) == 2 and near(at(r_m[0])[0], 800 - 2 * sm + 3 * sm) and near(at(r_m[0])[1], 500 - sm / 2 - gm / 2 - gap * sm)
          and pm == split(run(re.sub(r"\{ruby:[^{}]*\}|\{/ruby\}", "", moved))[0])[0],
          (r_m, pm))
    gen_v, ts_v = run(text.replace("\\pos(960,945)", "\\move(960,945,1000,945)"))
    check("整块画在 \\move 上：不画 Ruby、打提示，正文里的标记照样去掉",
          not split(gen_v)[2] and not ts_v.counts.get("ruby") and any("Ruby" in m for _, m in ts_v.notes)
          and not any("ruby" in ln for ln in gen_v), (gen_v, ts_v.notes))
    panel = draft([SC.Item(0, 3_000_000, [[200, 200, 800, 400]], ["第一段", "第二段"], "block", region=3, translated=True,
                           row_h=30, layer="panel")]).replace("第二段", "{ruby:x}第二{/ruby}段")
    gen_p, ts_p = run(panel)
    check("阅读面板：不画 Ruby（按纯文字重新折行），没有标记", "{ruby:x}" in panel and not split(gen_p)[2]
          and not ts_p.counts.get("ruby") and not any("ruby" in ln for ln in gen_p), gen_p)

    # ---- 打字机、底板跟着字长、dim、按轨迹分段 ----
    tw = text.replace("fo:box=", "fo:tw=1000;box=")      # 7 个字匀速：的 = 第 2 个（333 ms）、第 = 第 4 个（666 ms）
    gen_t, _ = run(tw, plate="text")
    pt, tt, rt = split(gen_t)
    check("打字机：读音在正文第一个字出现时整段出现",
          "\\t(333,334,\\alpha&H00&)}的" in body(tt[0]) and body(rt[0]).endswith("{\\alpha&HFF&\\t(333,334,\\alpha&H00&)}きん")
          and body(rt[1]).endswith("{\\alpha&HFF&\\t(666,667,\\alpha&H00&)}に"), rt)

    def clip(ln):
        return ln.split("\\1a&H80&")[1].split("}")[0]

    def reach(lines, t):                               # 这一行几块 clip 合起来在 t 时的（上沿, 右沿）
        got = [r for r in (_clip_at(clip(ln), t) for ln in lines) if r[2] > r[0]]
        return (min(r[1] for r in got), max(r[2] for r in got)) if got else (None, None)
    row0 = [ln for ln in pt if rect(ln) == rect(pt[0])]
    check("底板跟着字长（逐行）：读音出现时这一行的右沿一下长到读音的右沿，竖直本来就是并进读音的整块高",
          reach(row0, 332)[1] < r0[2] + pad - 1 and reach(row0, 334)[1] >= r0[2] + pad - 1
          and reach(row0, 334)[0] <= r0[1] - pad + 1, (reach(row0, 332), reach(row0, 334), r0))
    pbx = split(run(tw, plate="box")[0])[0]
    check("底板跟着字长（整块 bbox）：读音出现时上沿一下长到读音的上沿；全字后盖住全部读音",
          len(pbx) == 1 and _clip_at(clip(pbx[0]), 332)[1] > r0[1] - pad + 1 and _clip_at(clip(pbx[0]), 334)[1] <= r0[1] - pad + 1
          and _clip_at(clip(pbx[0]), 5000)[2] >= r0[2] + pad - 1, pbx)
    _p, td_, rd = split(run(tw, typewriter="dim")[0])
    dim = re.match(r"\{\\alpha&H80&\\t\(\d+,\d+,\\alpha&H00&\)\}", body(td_[0]).split("}", 1)[1])
    check("dim：读音跟着这一行半透明、全字后显出", dim and all(body(r).split("}", 1)[1].startswith(dim[0]) for r in rd), (td_, rd))
    path = ";path=0:700 900 1220 940|500000:700 900 1220 940|1000000:800 900 1320 940"
    _p, tm, rmv = split(run(tw.replace(";rows;", path + ";rows;"))[0])
    segs_t = [ln for ln in tm if "金" in ln]
    segs_r = [ln for ln in rmv if "きん" in ln]

    def coords(ln):
        return [float(v) for v in re.search(r"\\(?:pos|move)\(([^)]*)\)", ln)[1].split(",")[:4]]
    off = [[a - b for a, b in zip(coords(r)[:2] * 2, coords(t)[:2] * 2)] for r, t in zip(segs_r, segs_t)]
    check("按轨迹分段：读音和正文同样分段、同样平移（\\move），逐字时刻换成各段的相对时间（段前已出现的直接显出）",
          len(segs_r) == len(segs_t) == 2 and "\\move(" in segs_r[1] and off[0] == off[1]
          and [a - b for a, b in zip(coords(segs_r[1]), coords(segs_t[1]))] == off[1]
          and "\\alpha" not in body(segs_r[1]) and "\\t(166,167,\\alpha&H00&)}に" in body([ln for ln in rmv if "に" in ln][1]),
          (segs_r, segs_t))

    # ---- 字色：dev 的黄字、底板取色 ----
    ui_text = draft([item(styled, ui=True, main=False)])
    _p, _t, r_ui = split(run(ui_text)[0])
    check("dev：UI 黄字的行，读音的行首块带同样的字色", ",UI.fo-fx," in r_ui[0]
          and all(f"\\1c&H{TS.UI_COLOR}&" in ln.split("}")[0] for ln in r_ui), r_ui)
    with tempfile.TemporaryDirectory() as td:
        vid = Path(td) / "v.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xE0E0E0:s=1920x1080:d=1:r=5",
                        "-c:v", "ffv1", "-pix_fmt", "bgr0", str(vid)], check=True)
        gen_c, _ = run(text, look="default", video=str(vid), plate_color="on")
    _p, tc, rc = split(gen_c)
    check("底板取色：浅底板上读音和正文一起改成深字",
          rc and tc and all(body(ln).startswith("{\\1c&H1A1A1A&") for ln in rc + tc), (tc, rc))


def t_delivery_cases() -> None:
    """按交付行为验收的打分（dev_tools/delivery_cases.py）：只读 ASS 里画了什么、什么时候、画在哪。"""
    print("交付病例打分")
    import delivery_cases as DC
    with tempfile.TemporaryDirectory() as td:
        ass = Path(td) / "x.ass"
        ass.write_text("\n".join([
            "[Events]",
            "Dialogue: 0,0:00:01.00,0:00:05.00,Name,,0,0,0,,{\\an5\\pos(500,860)}名前",
            "Dialogue: 0,0:00:01.00,0:00:03.00,Body,,0,0,0,,{\\an5\\move(500,960,500,920,0,500)}一行目の台詞、",
            "Dialogue: 0,0:00:03.00,0:00:06.00,Body,,0,0,0,,{\\an5\\pos(500,920)}一行目の台詞、",
            "Dialogue: 0,0:00:01.00,0:00:06.00,Body,,0,0,0,,{\\an5\\pos(500,960)}二行目です。",
            "Dialogue: 0,0:00:06.00,0:00:09.00,Log,,0,0,0,,{\\an5\\pos(500,300)}一行目の台詞、",
            "Dialogue: 0,0:00:01.00,0:00:06.00,Body,,0,0,0,,{\\p1}m 0 0 l 10 0 10 10",
            "Dialogue: 0,0:00:01.00,0:00:06.00,UI,,0,0,0,,{\\an5\\pos(1500,100)}メニュー",
        ]), encoding="utf-8")
        evs = DC.parse_ass(ass)
        body = {"id": "b", "kind": "dialogue", "text": "一行目の台詞、二行目です。", "box": [300, 900, 700, 980],
                "t_on": 1.2, "t_off": 6.0}
        r = DC.judge(body, 2.0, evs)
        check("交付病例：框里相邻的名牌不拼进正文；按位置切开的同一行按整条链算时段（另一处同文本的回显不接）",
              r["verdict"] == "完整" and r["t"] == [1.0, 6.0] and r["on_ok"] and r["off_ok"], r)
        # 同一行接缝处从 \an4（锚点在行首）换成 \an5（行中）：锚点差半个行宽，照样是同一处（G3-d 的 zzz z6）；
        # 一句的两行各自成链时，时段取两行都在屏的那一段
        al = Path(td) / "align.ass"
        al.write_text("\n".join([
            "[Events]",
            "Dialogue: 0,0:00:01.50,0:00:06.00,Body,,0,0,0,,{\\an5\\fs40\\pos(396,910)}四行目、",
            "Dialogue: 0,0:00:01.00,0:00:03.00,Body,,0,0,0,,{\\an4\\fs40\\pos(300,960)}三行目です。",
            "Dialogue: 0,0:00:03.00,0:00:06.00,Body,,0,0,0,,{\\an5\\fs40\\pos(396,960)}三行目です。",
        ]), encoding="utf-8")
        two = DC.judge({"id": "a", "kind": "dialogue", "text": "四行目、三行目です。", "box": [250, 880, 600, 990]},
                       2.0, DC.parse_ass(al))
        check("交付病例：同一行换了对齐方式照样接成一条链；拆成几行时时段取整句都在屏的那一段",
              two["verdict"] == "完整" and two["t"] == [1.5, 6.0], two)
        moved = DC.judge({**body, "box": [300, 600, 700, 700]}, 2.0, evs)
        ui = {"id": "u", "kind": "ui", "text": "設定", "box": [1400, 80, 1600, 120]}
        bad, gone = DC.judge(ui, 2.0, evs), DC.judge({**ui, "box": [0, 0, 50, 50]}, 2.0, evs)
        check("交付病例：整句画在框外 = 位置错；框里画的对不上 = 残缺；什么都没画 = 缺",
              (moved["verdict"], bad["verdict"], gone["verdict"]) == ("位置错", "残缺", "缺"), (moved, bad, gone))
        # 坐标系：病例框是视频像素，锚点是 ASS 的 PlayRes 坐标；不同就换算，缺一个就报错（验收 peer 2026-10-06）
        half = Path(td) / "half.ass"
        half.write_text("[Script Info]\nPlayResX: 960\nPlayResY: 540\n\n[Events]\n"
                        "Dialogue: 0,0:00:01.00,0:00:05.00,Body,,0,0,0,,{\\an5\\pos(250,470)}一行目の台詞、二行目です。\n",
                        encoding="utf-8")
        case = {"size": [1920, 1080], "moments": [{"t": 2.0, "items": [{"id": "b", "kind": "dialogue",
                "text": "一行目の台詞、二行目です。", "box": [300, 900, 700, 980]}]}]}
        got = DC.score_case(case, half)
        try:
            DC.score_case({k: v for k, v in case.items() if k != "size"}, half)
            raised = False
        except ValueError:
            raised = True
        check("交付病例：ASS 的 PlayRes 和病例视频尺寸不同时锚点换算到视频像素；病例没写 size 就报错",
              got[0]["verdict"] == "完整" and raised, (got, raised))


def t_evalset_compare() -> None:
    """结构化评测的两臂比较（dev_tools/evalset.py）：判定不变、数值变了的条目也要报出来。"""
    print("评测骨架·两臂比较")
    import evalset as ES
    k = ("delivery", "gi", "g3")
    A = {k: {"kind": "dialogue", "verdict": "完整", "metrics": {"sim": 1.0, "time_ok": 1}}}
    B = {k: {"kind": "dialogue", "verdict": "完整", "metrics": {"sim": 1.0, "time_ok": 0}}}
    mc = ES.metric_changes(A, B, [k])
    check("evalset compare：判定相同、time_ok 1 -> 0 也报出来（只比判定会读成不变）",
          mc == {"time_ok": [(k, 1, 0)]}, mc)


def t_timing_check() -> None:
    """时间疑点检测（时间疑点检测器计划）：同一句字的判定、五条规则命中 / 不命中、tracks 条目不触发 A2、B2 让给 A2、CLI 冒烟。"""
    print("时间疑点")
    import contextlib
    import io
    import json
    from dataclasses import replace

    from flowocr import textfold as TF
    from flowocr.artifacts import tracksio
    from flowocr.output import script as SC
    from flowocr.output import timing_check as TC

    S = 1_000_000
    check("同一句字：只差标点、全角半角、打字中途的前缀都算；一个字的前缀、别的字不算",
          TF.same_reading("裁判の後。", "裁判の後") and TF.same_reading("雪だ！ヒュー", "雪だ!ヒューヒュー!")
          and not TF.same_reading("裁", "裁判の後") and not TF.same_reading("カチーナ", "その継承者っていうのが"))

    def ev(i, box, t0, t1, text, flags=(), **kw):
        return {"id": i, "region": 0, "box": box, "t_start": int(t0 * S), "t_end": int(t1 * S), "text": text,
                "flags": list(flags), "conf": 0.9, "n_obs": 10, **kw}

    def tracks_doc(evs, cues):
        return {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
                "provenance": {"main_track": "r00"}, "events": evs,
                "regions": [{"index": 0, "align": "left", "align_how": {"by": "stats"}}],
                "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "a.srt", "ui_lines": [], "cues": cues}]}

    r1, r2, name_box, far = [100, 900, 700, 940], [100, 950, 600, 990], [100, 840, 300, 880], [1000, 900, 1500, 940]
    body = "その継承者っていうのが"

    def a2_on(variant_box):
        evs = [ev(0, name_box, 0, 30, "カチーナ"), ev(1, r1, 24, 30, body), ev(2, variant_box, 0, 24, body + "。")]
        doc = tracks_doc(evs, [{"id": "c0", "t_start": 0, "t_end": 30 * S, "events": [0, 1]},
                               {"id": "c1", "t_start": 0, "t_end": 24 * S, "events": [2]}])
        it = SC.Item(0, 30 * S, [r1], ["译文"], "line", events=[evs[1]], region=0, translated=True, layer="body", key="k1")
        return TC.rule_a2([it], doc, TC.Context(doc)), doc, evs
    sus, doc, evs = a2_on(far)
    check("A2：matched 正文条目的起点被同一条 cue 里的名牌定早，报出，定下它的是名牌、不同文；那一刻屏上没有同一句字",
          len(sus) == 1 and sus[0].evidence["setter"][0]["id"] == 0 and not sus[0].evidence["setter"][0]["same_reading"]
          and sus[0].evidence["same_on_screen"] == [], [s.evidence for s in sus])
    sus_v, *_ = a2_on(r1)
    check("A2：起点那一刻同一处已有同一句字（读数多一个句号、没收进成员）时照报，并列出那个事件（多半是成员没收全）",
          len(sus_v) == 1 and [x["id"] for x in sus_v[0].evidence["same_on_screen"]] == [2])
    near = SC.Item(24 * S - 500_300, 30 * S, [r1], ["译文"], "line", events=[evs[1]], region=0, translated=True, layer="body")
    check("A2：只早一个采样间隔（带几百微秒的抖动）的不报", TC.rule_a2([near], doc, TC.Context(doc)) == [])

    tdoc = tracks_doc([ev(0, name_box, 0, 30, "カチーナ", ["nameplate"]), ev(1, r1, 24, 30, body)],
                      [{"id": "c0", "t_start": 0, "t_end": 30 * S, "events": [0, 1]}])
    tracksio.validate(tdoc)
    titems = SC.tracks_items(tdoc, "main")
    check("A2：tracks 的条目按构造不触发（起点取成员最小值、cue 起点只从下面夹）",
          titems and not any(s.rule == "A2" for s in TC.check(titems, tdoc)), [(it.start_us, it.lines) for it in titems])

    def line(t0, t1, box, evs, **kw):
        return SC.Item(int(t0 * S), int(t1 * S), [box], ["x"], "line", events=evs, region=0, **kw)
    a = line(0, 10, r1, [ev(10, r1, 0, 10, "あいうえおか")])
    b = line(5, 15, r1, [ev(11, r1, 5, 15, "かきくけこさ")])
    c = line(5, 15, far, [ev(12, far, 5, 15, "さしすせそた")])
    d = line(9.5, 15, r1, [ev(13, r1, 9.5, 15, "たちつてとな")])
    na = line(0, 10, r1, [ev(14, r1, 0, 10, "リン")])
    nb = line(5, 15, r1, [ev(15, r1, 5, 15, "Uン")])
    ctx = TC.Context(doc)
    check("A1：同一处前后两条重叠超过容差报；横向不相交的、重叠不到 1.5 个采样间隔的不报",
          [s.rule for s in TC.rule_a1([a, b], doc, ctx)] == ["A1"] and TC.rule_a1([a, c], doc, ctx) == []
          and TC.rule_a1([a, d], doc, ctx) == [])
    short, longer = TC.rule_a1([na, nb], doc, ctx), TC.rule_a1([na, b], doc, ctx)
    check("A1：重叠的只是两边各一行短字（一直挂着的名字）照报、标出来；一边是长行的不标",
          [s.evidence["short_rows"] for s in short] == [True] and [s.evidence["short_rows"] for s in longer] == [False])

    first = ev(20, r1, 0, 30, "あいうえおかき", t_full_sampled=500_000)
    check("B1：第一行显出后隔 ≥ 1.5 s 才有下一行，报；隔 0.5 s 的不报",
          [s.rule for s in TC.rule_b1([line(0, 30, r1, [first, ev(21, r2, 3, 30, "くけこさしす")])], doc, ctx)] == ["B1"]
          and TC.rule_b1([line(0, 30, r1, [first, ev(21, r2, 1, 30, "くけこさしす")])], doc, ctx) == [])

    ten = "あいうえおかきくけこ"

    def tw_item(k, tw_s, dwell_s=2.0, lead_s=0.0):
        t0 = 20 * k
        e = ev(30 + k, r1, t0, t0 + tw_s + dwell_s, ten, t_full_sampled=int((t0 + tw_s) * S))
        return line(t0 - lead_s, t0 + tw_s + dwell_s, r1, [e])
    norm = [tw_item(k, 1.8) for k in range(6)]
    slow = tw_item(6, 9.0)
    check("B2：字速比邻近中位数慢 3 倍以上，报；正常的不报",
          [s.start_us for s in TC.rule_b2(norm + [slow], doc, ctx)] == [slow.start_us])
    slow_a2 = tw_item(6, 9.0, lead_s=3.0)
    got = TC.check(norm + [slow_a2], doc)
    slow_norm, fast = [tw_item(k, 9.0) for k in range(6)], tw_item(7, 1.8)       # 采样级的打字机至少 2 个采样间隔，偏快的就比慢邻居
    got_fast = TC.rule_b2(slow_norm + [fast], doc, ctx)
    check("B2：快到邻近中位数 1/3 以下的也报，证据标 fast",
          [(s.start_us, s.evidence["side"]) for s in got_fast] == [(fast.start_us, "fast")], [(s.start_us, s.evidence) for s in got_fast])
    check("B2：A2 报了起点早的条目不再报偏慢（首字就是那个偏早的起点）",
          {s.rule for s in got if s.start_us == slow_a2.start_us} == {"A2"}, [(s.rule, s.start_us) for s in got])
    stay = tw_item(6, 1.8, dwell_s=40.0)
    check("B3：全字后每字停留比邻近中位数长 4 倍以上、且停留 ≥ 3 s，报",
          [s.start_us for s in TC.rule_b3(norm + [stay], doc, ctx)] == [stay.start_us])
    tiny = [tw_item(k, 0.1, dwell_s=0.05) for k in range(6)] + [tw_item(6, 0.1, dwell_s=2.5)]
    few = [tw_item(k, 1.8) for k in range(6)] + [line(140, 182, r1, [ev(60, r1, 140, 182, "あいう", t_full_sampled=141 * S)])]
    check("B3：每字停留离群但总停留不到 3 s 的不报；不足 8 字的不参与",
          TC.rule_b3(tiny, doc, ctx) == [] and TC.rule_b3(few, doc, ctx) == [])

    with tempfile.TemporaryDirectory() as td:
        p, out = Path(td) / "t-tracks.json", Path(td) / "sus.json"
        p.write_text(json.dumps(tdoc, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            rc = TC.main([str(p), "--json", str(out)])
        check("命令行：读 tracks 产物，打汇总行，--json 存全量",
              rc == 0 and printed.getvalue().startswith("时间疑点 ") and "suspects" in json.loads(out.read_text(encoding="utf-8")),
              printed.getvalue())
    burst = [line(30 + 3 * k, 34 + 3 * k, r1, [ev(40 + k, r1, 30 + 3 * k, 34 + 3 * k, f"ノイズの読み{k}")]) for k in range(6)]
    got = TC.rule_a1(burst, doc, TC.Context(doc))
    check("A1：同一处接连的重叠并成一段只报一条，记次数和段尾；这一条自己的起止不变",
          len(got) == 1 and got[0].evidence.get("burst") == 5 and got[0].end_us == burst[1].end_us
          and got[0].evidence["burst_end_us"] == burst[-1].end_us, [(s.start_us, s.end_us, s.evidence) for s in got])
    # 回显条目单列（owner 2026-10-10，检测器三场修复计划 U1）：照报、注明哪一边、汇总单数、列表排后
    own = TC.rule_a1([a, replace(b, echo="log")], doc, ctx)
    other = TC.rule_a1([replace(a, echo="replay"), b], doc, ctx)
    check("回显：A1 这一条是回显的注明形态；前一条是回显的另记一边；都照报",
          [(s.echo, s.echo_other, s.echoish) for s in own] == [("log", None, True)] and "回显·对话记录" in own[0].notes
          and [(s.echo, s.echo_other) for s in other] == [(None, "replay")] and "前一条是回显·重演" in other[0].notes,
          [(s.echo, s.echo_other, s.notes) for s in own + other])
    mixed = burst[:2] + [replace(burst[2], echo="log")] + burst[3:]
    got = TC.rule_a1(mixed, doc, TC.Context(doc))
    check("回显：A1 并段里别的几次沾了回显、报出的第一次不沾的，照样单列并注明",
          len(got) == 1 and got[0].echoish and "并段里有回显·对话记录" in got[0].notes, [(s.echo_other, s.notes) for s in got])
    plain = TC.rule_a1([a, b], doc, ctx)
    rep = TC.report(own + plain, 1.0)
    check("回显：汇总行每条规则另报其中回显几条，逐条列表不沾回显的在前",
          "A1 同位重叠 2（主轨 2，每小时 2.0，其中回显 1）" in rep.splitlines()[0]
          and "（回显）" not in rep.splitlines()[1] and "（回显）" in rep.splitlines()[2], rep)
    check("回显：tracks 的条目没有回显判定，一条都不沾", not any(s.echoish for s in TC.check(titems, tdoc)))
    late = SC.Item(24 * S, 33 * S, [r1], ["译文"], "line", events=[evs[1]], region=0, translated=True, layer="body")
    got = TC.rule_a2([late], doc, TC.Context(doc))
    check("A2：终点晚于成员也报（证据里是终点那一侧）", [s.evidence["side"] for s in got] == ["end"], [s.detail for s in got])
    with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
        _, n_rs, _, _ = SC.render_script(tdoc, td, {})
    check("阶段 3 的 render_script 跑检测器、计数放进 n", "timing_suspects" in n_rs, dict(n_rs))
    from unittest import mock
    with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()) as printed, \
            mock.patch.object(TC, "check", return_value=sus):
        SC.render_script(tdoc, td, {"keep": "all", "why": "on", "typewriter": "dim", "tracks": "C:/a b/t-tracks.json"})
    hint = next((x for x in printed.getvalue().splitlines() if x.startswith("⚠ 时间疑点")), "")
    check("阶段 3 的疑点提示带上这次生效的选项（预设注入的 keep=all 也在内），只带决定条目和时刻的那几个",
          hint.endswith('timing_check <这份产物> --opt keep=all --opt "tracks=C:/a b/t-tracks.json" --opt typewriter=dim'), hint)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t-tracks.json"
        p.write_text(json.dumps(tdoc, ensure_ascii=False), encoding="utf-8")
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                TC.main([str(p), "--opt", "fade=of"])
            typo = None
        except SystemExit:
            typo = err.getvalue()
    check("命令行的 typewriter / fade 拼错了照阶段 3 的判据报用法错，不静默按另一种口径算", typo is not None and "fade" in typo, typo)
    late_item = line(5, 30, r1, [first, ev(21, r2, 6, 30, "くけこさしす")])
    check("B1：最早的成员比条目起点还早开始时，全字时刻按条目起点夹（离下一行 1 s，不报；不夹会算成 5.5 s）",
          TC.rule_b1([late_item], doc, ctx) == [])

    def two_rows(k, tw2_s):
        t0 = 30 * k
        e1 = ev(70 + 2 * k, r1, t0, t0 + 20, ten, t_full_sampled=int((t0 + 1.8) * S))
        e2 = ev(71 + 2 * k, r2, t0 + 3, t0 + 20, ten, t_full_sampled=int((t0 + 3 + tw2_s) * S))
        return SC.Item(int(t0 * S), int((t0 + 20) * S), [r1, r2], ["x", "y"], "line", events=[e1, e2], region=0,
                       row_events=[[e1], [e2]])
    rows_norm, rows_slow = [two_rows(k, 1.8) for k in range(6)], two_rows(6, 9.0)
    got_rows = TC.rule_b2(rows_norm + [rows_slow], doc, ctx)
    check("B2：有逐行时刻的按行算，只报慢的那一行，明细写第几行",
          [(s.start_us, s.detail.startswith("第 2 行")) for s in got_rows] == [(rows_slow.start_us, True)], [(s.start_us, s.detail) for s in got_rows])
    def a3_on(early_box, early_text, early_end, member_text=body):
        evs = [ev(80, early_box, 0, early_end, early_text), ev(81, r1, 10, 20, member_text)]
        d3 = tracks_doc(evs, [{"id": "c0", "t_start": 0, "t_end": 20 * S, "events": [0, 1]}])
        return TC.rule_a3([line(10, 20, r1, [evs[1]])], d3, TC.Context(d3)), d3, evs
    hit, *_ = a3_on(r1, body + "。", 12)
    check("A3：起点之前同一处已有同一句字、一直在屏到起点，报，偏差是早出现的那段，明细注明没有字幕盖着",
          [(s.rule, s.off_us, s.evidence["drawn_by"]) for s in hit] == [("A3", 10 * S, None)] and "没有字幕盖着" in hit[0].detail,
          [(s.off_us, s.detail) for s in hit])
    check("A3：别的字、别处的同一句、起点前超过容差就没了的同一句都不报",
          a3_on(r1, "全然違う文章です", 12)[0] == [] and a3_on(far, body, 12)[0] == [] and a3_on(r1, body, 9)[0] == [])
    frag = [ev(80, r1, 0, 5, body), ev(82, r1, 5.5, 9.5, body + "。"), ev(81, r1, 10, 20, body)]
    d_frag = tracks_doc(frag, [{"id": "c0", "t_start": 0, "t_end": 20 * S, "events": [0, 1, 2]}])
    got_frag = TC.rule_a3([line(10, 20, r1, [frag[2]])], d_frag, TC.Context(d_frag))
    check("A3：同一句断成几段（段间隔一个采样间隔）挂到起点前也算，接到最早那段",
          [(s.off_us, s.evidence["segments"], s.evidence["gap"]) for s in got_frag] == [(10 * S, 2, True)]
          and "刚断开" in got_frag[0].detail, [(s.off_us, s.detail) for s in got_frag])
    check("A3：只差一个字的读错（SKILL INFD / SKILL INFO）也算同一句",
          len(a3_on(r1, "SKILL INFO", 12, "SKILL INFD")[0]) == 1)
    _, d3, evs3 = a3_on(r1, body + "。", 12)
    prev, cur = line(0, 10, r1, [evs3[0]]), line(10, 20, r1, [evs3[1]])
    drawn = TC.rule_a3([prev, cur], d3, TC.Context(d3))
    check("A3：早出现的那段已是前一条的成员（同一句拆成两条画）照报，明细标出前一条",
          [s.evidence["drawn_by"]["start_us"] for s in drawn] == [0] and "拆成两条" in drawn[0].detail, [s.detail for s in drawn])
    many = [ev(90 + k, [10 * k, 10, 10 * k + 5, 15], k, k + 3, "x") for k in range(40)] \
        + [ev(200, far, -500, 500, "y"), ev(201, far, 3, 20, "z")]           # 短事件走二分取窗，长的走另存的那份
    idx = tracksio.EventIndex(many, 5 * S)
    check("事件索引：按窗口取的某一刻在屏的事件（含起点放宽），和逐个扫一遍的一样；短事件走窗口、比窗口长的另存",
          len(idx.long) == 2 and all(
              sorted(e["id"] for e in idx.on_screen(t * S // 2, slack)) ==
              sorted(e["id"] for e in many if e["t_start"] <= t * S // 2 + slack and e["t_end"] > t * S // 2)
              for t in range(-4, 90) for slack in (0, S // 2)))
    alone = [replace(tw_item(k, 1.8), region=10 + k) for k in range(6)] + [replace(tw_item(6, 9.0), region=20)]
    check("B2：同组邻居不足 3 个时比整份的中位数（原来阶段 3 打字机偏慢警告的口径）",
          [s.start_us for s in TC.rule_b2(alone, doc, ctx)] == [alone[-1].start_us])
