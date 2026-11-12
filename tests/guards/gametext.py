"""文本库、剧本匹配、用途 2 的尺的守卫。"""
from __future__ import annotations

import importlib  # noqa: F401
import inspect  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401

from guards._common import *  # noqa: F401,F403


def t_arms() -> None:
    """A/B 臂不是片子。

    存在的理由（methodology-audit-3 报告 §3）：`head2head.sh` 加了 `SUFFIX` 之后，
    `h2h_report` 还在按 `f*-*.json` 通配，于是四条臂的目录被报成
    **"20 部片子：我们赢 10/20 = 50.0%"**——同样五部数了四遍。
    """
    print("A/B 臂")
    import h2h_report as hr

    check("臂名从片号右边切出来", hr.split_arm("f3-sr20m") == ("f3", "-sr20m"))
    check("默认臂的臂名是空串", hr.split_arm("f3") == ("f3", ""))
    films = {f"f{n}{arm}": {"ours": {}, "base": {}}
             for n in (1, 2, 3, 4, 5) for arm in ("", "-sr20", "-sr20mb")}
    arms = hr.by_arm(films)
    check("三条臂分成三组，每组五片",
          sorted(arms) == ["", "-sr20", "-sr20mb"] and all(len(v) == 5 for v in arms.values()),
          {k: len(v) for k, v in arms.items()})


def t_hit_delta() -> None:
    """命中集合差：**同文本条目互换不算变化**，分档按内容字数。

    存在的理由（methodology-audit-3 报告 §1）：`script_align` 的三档是 gap 分桶的
    产物、跨臂比会被重新分桶骗到；命中是集合，集合差才是能跨臂比的那个口径。
    而 `align()` 给同文本的多条剧本条目挑"游标前方第一条"，
    所以默认按**文本多重集**求差——按 key 会把纯记账变动算成一增一减。
    """
    print("命中集合差")
    from collections import Counter

    import hit_delta as hd
    from flowocr.analyze import script_align as SA

    a_txt, b_txt = Counter(["ですね。", "……！"]), Counter(["ですね。", "……！"])
    check("同文本条目换了 key，文本口径下差为空", not (b_txt - a_txt) and not (a_txt - b_txt))
    gained = Counter(["こんにちは、エマ。", "……！", "え？"])
    g = hd.classify(gained, None)
    P, T, C = evalkit.CLASSES
    check("新增命中按内容字数分档", (g[P], g[T], g[C]) == (1, 1, 1), g)
    check("key 口径要拿 key->text 的表来分档",
          hd.classify(Counter(["k1"]), {"k1": "……！"})[P] == 1)

    # 代价侧：超额认领 = 输出里比整部剧本还多的那些条目，多出来的必然是错的
    # （methodology-audit-3 报告 §1.5）。剧本里有 2 条 `はい。`，输出里出现 5 次
    # -> 3 条必然错；输出里没出现过的行不算数（那是漏，不是错）。
    have = Counter({SA.norm("はい。"): 2, SA.norm("ダメだ。"): 1})
    subs = [(0.0, "はい。", "x")] * 5 + [(9.0, "ダメだ。", "x")]
    check("超额认领只数超出剧本条数的那部分", hd.overclaim(subs, have) == 3,
          hd.overclaim(subs, have))
    check("输出不超过剧本条数时为 0",
          hd.overclaim([(0.0, "はい。", "x")] * 2, have) == 0)

    # 命中要有 OCR 文本支撑：匹配器按上下文填在垃圾 cue 上的不算（yuka f5 发饰的 `米`，2026-10-05）
    line = "（それはもちろん、彼女がしでかしたあの事件だ……！）"
    check("文本支撑：垃圾 cue 不算；半截（只读到句尾、拼了 NO DATA）算；1–2 字要字全在；纯标点连标点比",
          not SA.supports("米", line) and SA.supports("NO DATA 彼女がしでかしたあの", line)
          and SA.supports("ら 待っててよ。", "あてぃしが笑顔にしてあげるから……待っててよ。")
          and SA.supports("火花 「未着」", "「未着」……") and not SA.supports("…不不。", "……ふふ。")
          and SA.supports("ムアラニ …!!!", "…！！！") and SA.supports("カチーナ はあ…", "はぁ…"))
    scr = [SA.Entry(key="a", kind="Narrative", cmd="", text=line, src="u"),
           SA.Entry(key="b", kind="Narrative", cmd="", text="こんにちは、エマ。", src="u")]
    ok = SA.text_support(scr, [0, 1], [(10.0, 12.0, 0), (20.0, 22.0, 0)],
                         [[(10.5, 11.5, "米"), (19.0, 20.5, "こんにちは、エマ")]])
    check("text_support：认领它的输出 cue 时段里有读到它的 OCR 才算（时段正重叠）", ok == {1}, ok)
    # 多个文件：输出 cue 的第三个分量是 OCR 文件序号，只在那一份里找（文件 1 同一时段读到了，文件 0 没读到）
    ok2 = SA.text_support(scr, [1], [(20.0, 22.0, 1)],
                          [[(19.0, 20.5, "米")], [(19.0, 20.5, "こんにちは、エマ")]])
    ok3 = SA.text_support(scr, [1], [(20.0, 22.0, 0)],
                          [[(19.0, 20.5, "米")], [(19.0, 20.5, "こんにちは、エマ")]])
    check("text_support：多文件时按输出 cue 记的文件序号找 OCR，不串到别的文件", ok2 == {1} and ok3 == set(), (ok2, ok3))


def t_miss_window() -> None:
    """疑似漏字幕判断器的时间窗与另一版本（`dev_tools/miss_detect.py` 的 `claim_window` / `sibling`）：两个纯函数。"""
    print("时间窗与另一版本")
    from types import SimpleNamespace as _NS

    import miss_detect as MD

    prev, nxt = [(10.0, 12.0), (500.0, 502.0)], [(15.0, 18.0), (900.0, 901.0)]
    check("时间窗：取离锚点最近的那次输出——前一条不晚于它的结束、后一条不早于它的开始",
          MD.claim_window(13.0, prev, nxt) == (12.0, 15.0), MD.claim_window(13.0, prev, nxt))
    check("时间窗：剧本头尾那一端没有已输出行就是 None",
          MD.claim_window(13.0, None, nxt) == (None, 15.0) and MD.claim_window(13.0, prev, None) == (12.0, None))
    texts = {"a": "インターノットの依頼も特に変わったものはなく、簡単なやつばっか", "b": "全然関係ない別の台詞です"}
    shown = [(11.0, 14.0, "a"), (11.0, 14.0, "b")]
    sib = MD.sibling("インターノットの依頼も特に変わったものはなくて、簡単なやつばっか…", 12.0, 15.0, shown, texts)
    check("另一版本：同一个窗里已输出的行和它几乎一样就算（两头都没已输出行不查）",
          sib is not None and sib[0] == "a"
          and MD.sibling("全く違う文章をここに書いておく", 12.0, 15.0, shown, texts) is None
          and MD.sibling(texts["a"], None, None, shown, texts) is None, sib)
    # naninovel 给说话人表：レイア的 `エマくん。` 不能被ミリア的 `エマちゃん。` 当成另一版本（《魔裁》第 5 部的漏报）
    det = _NS(ad=_NS(speaker={"leia": "蓮見レイア", "miria": "佐伯ミリア", "miria2": "佐伯ミリア"}), rep=lambda k: k)
    same = lambda a, b: MD.Detector.same_speaker(det, a, b)  # noqa: E731
    texts2 = {"miria": "エマちゃん。"}
    check("另一版本：适配层给了说话人表时要说话人相同（naninovel），不给时不管（gametext 两位主角各一版）",
          MD.sibling("エマくん。", 10.0, 12.0, [(10.0, 12.0, "miria")], texts2, lambda k: same("leia", k)) is None
          and MD.sibling("エマくん。", 10.0, 12.0, [(10.0, 12.0, "miria")], texts2, lambda k: same("miria2", k)) is not None
          and MD.Detector.same_speaker(_NS(ad=_NS(speaker=None), rep=lambda k: k), "leia", "miria"))


def t_miss_detect() -> None:
    """疑似漏字幕判断器（`dev_tools/miss_detect.py`，miss-detector 计划 S2 的契约测试）：合成剧本 + tracks + obs，
    真的跑 `run_match` 出匹配产物，再过检测器——每类端到端钉一条；定层在同一份产物上改认领 / 喂入表各造一条。"""
    print("疑似漏字幕判断器")
    import contextlib
    import io
    import json as _json

    import miss_detect as MD
    from flowocr.analyze import game_align as GA
    from flowocr.analyze import gamescript as GS
    from flowocr.analyze import scriptmatch as SM
    from flowocr.artifacts import tracksio

    def L(key, text, kind="Talk", status=None, cn="译文", anchor=None, **kw):
        return {"key": key, "kind": kind, "status": status, "role": "X", "text": text, "cn": cn,
                "anchor": None if anchor is None else {"unique": True, "episodes": [anchor]}, **kw}
    # 选行：处境排掉、变体并到代表行、选项看前面那行走没走过
    sdoc = {"units": [{"uid": "u", "lines": [
        L("p", "最初の台詞です", anchor=[1.0, 2.0]), L("q", "最初の台詞だよ", status="Variant", variant_of="p", jp_tpl="最初の{NICKNAME}台詞だよ"),
        L("o", "見ていない部分", status="OutOfSpan"), L("w", "選ばなかった方", status="Unwalked"),
        L("c1", "行かない", kind="Choice"), L("x", "選んだ方の台詞", anchor=[5.0, 6.0]), L("c2", "もちろん行く", kind="Choice"),
        L("c3", "印のある選択肢", kind="Choice", anchor=[9.0, 9.5])]}]}
    sdoc["units"][0]["lines"].insert(4, L("w2", "もう一つの道", status="Unwalked"))
    sdoc["units"] += [{"uid": "u2", "lines": [L("s1", "最初に出る選択肢", kind="Choice"), L("s2", "その後の台詞", anchor=[30.0, 31.0])]},
                      {"uid": "u3", "lines": [L("o1", "見ていない台詞", status="OutOfSpan"), L("o2", "見ていない選択肢", kind="Choice")]}]
    sdoc["sequence"] = [[ln["key"], 1.0 + i, [[float(i), float(i) + 10]]]
                        for i, ln in enumerate(ln for u in sdoc["units"] for ln in u["lines"])]
    sel = {ln.key: ln for ln in MD.select_lines(sdoc)}
    check("检测器·该出现的行：没播 / 没走的排掉；变体并到代表行（时段、说法都并）；选项前面那行没走过 / 没播就排掉，"
          "单元开头的选项看后面那行，有锚点的选项照收",
          sorted(sel) == ["c2", "c3", "p", "s1", "s2", "x"] and sel["p"].texts == ["最初の台詞です", "最初の台詞だよ"]
          and len(sel["p"].windows) == 2 and sel["p"].anchored and sel["p"].tpl and not sel["x"].tpl, sorted(sel))
    # obs 搜读数：一帧里排成三行的拼起来算；长句里的碎片、只共有一半的另一种说法不算
    rows = [(30_000_000, [100, 800, 400, 840], "それでは一"), (30_000_000, [100, 850, 400, 890], "緒に行き"),
            (30_000_000, [100, 900, 400, 940], "ましょうか"), (55_000_000, [100, 800, 400, 840], "台詞です"),
            (70_000_000, [100, 800, 900, 840], "一緒にテレビでも見ないか？")]
    ot = MD.ObsText(rows)
    check("检测器·obs 搜读数：不到 3 个内容字的说法（变体里偶尔有）不拿去搜；带占位的行读数比原文长也算",
          MD.ObsText([(1_000_000, [0, 0, 300, 40], "台詞")]).read(["台詞"], [(0.0, 2.0)]) is None
          and MD.ObsText([(1_000_000, [0, 0, 300, 40], "旅人のねるめろ(本物)だよ")]).read(["旅人のだよ。"], [(0.0, 2.0)], tpl=True) == 1.0
          and MD.ObsText([(1_000_000, [0, 0, 300, 40], "旅人のねるめろ(本物)だよ")]).read(["旅人のだよ。"], [(0.0, 2.0)]) is None)
    ob = MD.ObsText([(1_000_000, [0, 0, 300, 40], "私たちも魔法を使える")])
    line = ["……いいや。私たちだって魔法を使える。"]
    others = frozenset({GS.cnorm("私たちも魔法を使える"), GS.cnorm(line[0])})
    check("检测器·obs 搜读数（naninovel 的 others）：读数逐字就是剧本里另一行（法庭『反論』的选项）的不算读到这一行；不给 others 照算；读数就是这一行本身照算",
          ob.read(line, [(0.0, 2.0)]) == 1.0 and ob.read(line, [(0.0, 2.0)], others=others) is None
          and ob.partial(line, [(0.0, 2.0)], others) == 0.0
          and MD.ObsText([(1_000_000, [0, 0, 300, 40], line[0])]).read(line, [(0.0, 2.0)], others=others) == 1.0)
    check("检测器·部分读数：没读到的行记窗里八成落在行内的读数最多盖住这一行几成（碎片 `台詞です` 盖住长句 15 个内容字里的 4 个）",
          ot.partial(["全然読まれていない長い台詞です"], [(50.0, 60.0)]) == 0.27
          and MD.ObsText([(1_000_000, [0, 0, 900, 40], "長い台詞ですがそれ以外のことも沢山書いてある看板")]).partial(
              ["全然読まれていない長い台詞です"], [(0.0, 2.0)]) == 0.0, ot.partial(["全然読まれていない長い台詞です"], [(50.0, 60.0)]))
    check("检测器·obs 搜读数：排成三行、每个框各占不到一半而拼起来过半的算读到过；3 字碎片落在长句里、只共有一半的另一种说法都不算",
          ot.read(["それでは一緒に行きましょうか"], [(25.0, 35.0)]) == 30.0
          and ot.read(["全然読まれていない長い台詞です"], [(50.0, 60.0)]) is None
          and ot.read(["お兄ちゃんも暇なら、一緒にテレビ観ない？"], [(65.0, 75.0)]) is None
          and ot.read(["それでは一緒に行きましょうか"], [(40.0, 50.0)]) is None)
    check("检测器·同一时刻画两块：引用同一事件、时段相交的两块报出来，首尾相接的不报",
          MD.double_draws([{"group": "a", "layer": "x", "events": [1], "start_us": 0, "end_us": 10},
                           {"group": "b", "layer": "y", "events": [1], "start_us": 5, "end_us": 20},
                           {"group": "b", "layer": "y", "events": [1], "start_us": 20, "end_us": 30}]) == [(1, "a/x", "b/y")])
    cev = MD.Events([{"id": i, "text": t, "t_start": 0, "t_end": 5_000_000}
                     for i, t in enumerate(("推奨次元界オーナメント", "次元界オーナメント"))])
    cov = cev.cover(cev.evs[0], "そこにあるのは没入装置、私が言ってた「次元界オーナメント」だよ")
    rev = MD.Events([{"id": i, "text": t, "t_start": 0, "t_end": 5_000_000} for i, t in enumerate(("それでは", "一緒に行き", "ましょうか"))])
    check("检测器·定位盖住多少按这一行的字位并：同屏两处字面不同、盖的是同一段字，不得加成过 WEAK；排成三行、各占不到一半的并起来过",
          cov < MD.WEAK and rev.cover(rev.evs[0], "それでは一緒に行きましょうか") == 1.0, cov)
    pa = {"code_fp": "x", "obs": "o.jsonl", "obs_mtime": 1.0, "gametext": "g", "params": {"qmin": 8}}
    check("检测器·跨臂比较：剧本 provenance（代码、obs、文本包、参数）不同就拒",
          MD.same_ref(pa, dict(pa)) == [] and MD.same_ref(pa, {**pa, "obs": "p.jsonl"}) == ["obs"]
          and raises(lambda: MD.compare({"provenance": pa, "rows": []}, {"provenance": {**pa, "gametext": "h"}, "rows": []}),
                     SystemExit))
    # 端到端：a 已输出；g 和 a 几乎一样、没出 -> 另一版本；e 界面文字 -> 决定不显示；sp 横幅（special，默认可见）没读到 -> 没读到；b 有锚点、tracks 里没有 -> 漏（建轨没留下）；
    # c 没锚点、obs 拼起来读到 -> 漏（锚点漏掉）；h 认领了、没有中文 -> 漏（画不出来）；d 只有碎片 -> 没读到；f 没播 -> 不进分母
    S = 1_000_000
    lines = [L("a", "今日はとても良い天気ですね", anchor=[10.0, 12.0]), L("g", "今日はとても良い天気ですよね"),
             L("b", "明日も晴れるといいですね", anchor=[20.0, 22.0]), L("c", "それでは一緒に行きましょうか"),
             L("h", "向こうの山はとても高いです", cn=None, anchor=[40.0, 42.0]), L("d", "全然読まれていない長い台詞です"),
             L("e", "ここは注意が必要な場所です", kind="Loose", role="UIText", anchor=[60.0, 62.0]), L("f", "見ていない部分の台詞", status="OutOfSpan"),
             L("sp", "柱の氷晶を破壊して技を中断させよう", kind="ReminderUI", role="Banner", anchor=[65.0, 66.0]),
             L("v", "雨が降りそうな空模様ですね"), L("v2", "雨が降りそうな空模様だね", status="Variant", variant_of="v"),
             L("pp", "……"), L("bb", "まっ、安心してちょうだい。あんたたちの話", anchor=[24.0, 25.0]), L("nm", "カチーナ…")]
    ts = {"a": 10.0, "g": 13.0, "b": 20.0, "c": 30.0, "h": 40.0, "d": 55.0, "e": 60.0, "sp": 65.0, "f": 70.0, "v": 80.0, "v2": 80.0, "pp": 90.0, "bb": 25.0, "nm": 95.0}
    shows = [(0, 10, 12, "今日はとても良い天気ですね"), (0, 40, 42, "向こうの山はとても高いです"), (1, 50, 53, "知らない看板の文字です"),
             (0, 80, 82, "雨が降りそうな空模様だね")]
    rows = rows + [(90_000_000, [100, 800, 600, 840], "それは……どうかな"), (21_000_000, [100, 800, 600, 840], "明日も晴れるといいですね"),
                   (95_000_000, [100, 600, 300, 640], "カチーナ")]
    evs = [{"id": i, "region": r, "box": [100, 900 - 300 * r, 700, 940 - 300 * r], "t_start": int(a * S), "t_end": int(b * S),
            "text": t, "flags": [], "conf": 0.9, "n_obs": 10} for i, (r, a, b, t) in enumerate(shows)]
    tdoc = {"schema": tracksio.SCHEMA, "tag": "m", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
            "provenance": {"main_track": "r00", "main_srt": "m-region00-subtitle.srt"}, "events": evs,
            "regions": [{"index": 0}, {"index": 1}],
            "tracks": [{"id": f"r0{r}", "kind": "region", "region": r, "label": "subtitle", "srt": f"m-region0{r}-subtitle.srt",
                        "ui_lines": [], "cues": [{"id": f"c{i}", "t_start": e["t_start"], "t_end": e["t_end"], "events": [i]}
                                                 for i, e in enumerate(evs) if e["region"] == r]} for r in (0, 1)]}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rp, tp, op, mp = d / "m-ref.json", d / "m-tracks.json", d / "m.jsonl", d / "matched.json"
        op.write_text("\n".join(_json.dumps(x, ensure_ascii=False) for x in
                                [{"_meta": {"height": 1080}}] + [{"frame": i, "t_us": t, "box": b, "text": x}
                                                                 for i, (t, b, x) in enumerate(rows)]), encoding="utf-8")
        refdoc = {"schema": GS.SCHEMA, "provenance": {"obs": str(op), "game": "syn", "code_fp": "x", "params": {"qmin": 8}},
                  "speakers": {"カチーナ": "卡齐娜"},
                  "units": [{"uid": "u", "lines": lines}],
                  "sequence": [[ln["key"], ts[ln["key"]], [[ts[ln["key"]] - 5, ts[ln["key"]] + 5]]]
                               for ln in lines if ln["status"] != "OutOfSpan"]}
        rp.write_text(_json.dumps(refdoc, ensure_ascii=False), encoding="utf-8")
        tracksio.dump(tp, tdoc)
        cref = GA.load_ref(rp)
        with contextlib.redirect_stdout(io.StringIO()):
            res = SM.run_match(cref, str(tp), "nonoise", "keep", 0.75, SM.MERGE_GAP)
        st = SM.match_stats(cref, res["recs"], res["suspect_raw"])
        mdoc = SM.matched_doc(cref, res["recs"], res["n_raw"], st, str(rp), res["subs_path"], res["tp"], [],
                              {"fuzzy": 0.75, "merge_gap": SM.MERGE_GAP, "feed": "nonoise", "feed_panel": "keep"},
                              res["items"], res["ov_stats"], res["evs"], res["textbox"])
        mp.write_text(_json.dumps(mdoc, ensure_ascii=False), encoding="utf-8")
        from flowocr.output import script as _SC
        tb_ok = isinstance(mdoc["overlay"].get("textbox"), dict) and not raises(lambda: _SC.need_textbox(mdoc))
        check("gametext 路径（run_match -> matched_doc）也写文本框画像和正文条目的原文整块，过阶段 3 的门",
              tb_ok and any(it["layer"] == "body" for it in mdoc["overlay"]["items"]), mdoc["overlay"].get("textbox"))
        det = MD.Detector(mp)
        out = det.run()
        got = {r["key"]: (r["cat"], r.get("layer"), r.get("read")) for r in out["rows"]}
        sp_hidden = MD.Detector(mp, "cn", None, "special").run()
        b = det.b_list(out["rows"], pack=False)
        saved = dict(GS.LOADERS)
        GS.LOADERS["syn"] = lambda: [GS.Unit("pk", None, [GS.Line("pk1", "Talk", "X", None, "知らない看板の文字です", None)])]
        try:
            bp = det.b_list(out["rows"], pack=True)
        finally:
            GS.LOADERS.clear()
            GS.LOADERS.update(saved)

        # 定层各钉一条：在同一份产物上造各层的情形（改检测器手里的认领 / 喂入表，或给一行不同的说法）
        def diag(text, t, kind="Talk", cn="译文", patch=None, key="zq"):
            dt = MD.Detector(mp)
            if patch:
                patch(dt)
            return dt.diagnose(MD.Line(key, text, kind, None, "X", "u", cn, t, [(t - 3, t + 3)], True, [text]))
        e0 = det.feed.fed_cue[0]
        layers = {
            "认领没条目": diag("今日はとても良い天気ですね", 10.0, patch=lambda dt: (
                dt.claims.__setitem__("zq", [(10.0, 12.0)]), dt.claim_recs.__setitem__("zq", [{"ocr": "今日はとても良い天気ですね"}])))["layer"],
            # 认领没撑住（认领它的 cue 里没有一行读数撑得住）就不按"认领了、没生成条目"定层，往事件那边追
            "认领没撑住": diag("今日はとても良い天気ですね", 10.0, patch=lambda dt: (
                dt.claims.__setitem__("zq", [(10.0, 12.0)]), dt.claim_recs.__setitem__("zq", [{"ocr": "全然違う台詞"}])))["layer"],
            "定位弱": diag("今日はとても良い天気ですね、それにしても明日の予定はどうなっているのかな", 10.0)["layer"],
            "同一段字": diag("今日はとても良い天気ですね、本当に", 10.0)["layer"],
            "认成别的行": diag("今日はとても良い天気ですね、本当に", 10.0, patch=lambda dt: dt.feed.rec_of.__setitem__(
                e0, {"ref": {"key": "zz", "jp": "全然違う台詞"}}))["layer"],
            # 事件 0 已被 a 的正文条目画着，那就是"认成别的行"；下面两种先把它拿掉
            "没认上": diag("今日はとても良い天気ですね、本当に", 10.0, patch=lambda dt: (
                dt.feed.rec_of.__setitem__(e0, {"ref": None}), dt.items_ev.pop(0)))["layer"],
            "没喂": diag("今日はとても良い天気ですね、本当に", 10.0, patch=lambda dt: (dt.feed.fed_cue.pop(0), dt.items_ev.pop(0)))["layer"]}
        cw = diag("知らない看板の文字です", 51.0, kind="Choice", cn=None)
        from flowocr.analyze import script_align as _SA
        hd = MD.Detector(mp)
        hd.feed.heads = frozenset({_SA.norm("ナヴィア")})
        hd.claim_recs = {"n1": [{"ocr": "ナヴィア"}], "n2": [{"ocr": "ナヴィア\nナヴィア…"}],
                         "n3": [{"ocr": "口ナヴィアの執務室に行く"}], "t1": [{"ocr": "旅人のねるめろ(本物)だよ"}],
                         "p1": [{"ocr": "ムアラニ\n…!!!"}], "s1": [{"ocr": "カチーナ：コホンッ。"}]}
        hd.names = {GS.cnorm("カチーナ")}

        def nl(k, text="ナヴィア…", tpl=False):
            return MD.Line(k, text, "Talk", None, "X", "u", "译文", 1.0, [(0.0, 2.0)], False, [text], tpl)
        head_only = (hd.claim_supported(nl("n1")), hd.claim_supported(nl("n2")), hd.claim_supported(nl("n3")))
        claim_more = (hd.claim_supported(nl("t1", "旅人のだよ。", tpl=True)), hd.claim_supported(nl("t1", "旅人のだよ。")),
                      hd.claim_supported(nl("p1", "…！！！")), hd.claim_supported(nl("s1", "コホンッ。")))
        bad = det.invariants([{"key": "q", "cat": MD.MISS}])
        det.m = {**det.m, "overlay": {**det.m["overlay"], "items": det.m["overlay"]["items"] + [
            {"key": "nope", "events": [999], "group": "dialogue", "layer": "body", "start_us": 0, "end_us": 1}]}}
        bad2 = det.invariants([])
    want = {"a": (MD.OUT, None, None), "g": (MD.SIB, None, None), "e": (MD.OFF, None, None),
            "b": (MD.MISS, "建轨没留下", "锚点 + 读数"), "c": (MD.MISS, "建轨没留下", "obs 搜到（锚点漏掉）"),
            "h": (MD.MISS, MD.L_HIDDEN, "认领过"), "d": (MD.UNREAD, None, None), "sp": (MD.UNREAD, None, None), "v": (MD.OUT, None, None),
            "pp": (MD.UNREAD, None, None), "bb": (MD.UNREAD, None, None), "nm": (MD.UNREAD, None, None)}
    why = {r["key"]: r.get("why") for r in out["rows"]}
    check("检测器·分类先后与定层：已输出 / 另一版本已输出 / 决定不显示 / 漏（带定层）/ 没读到 各落一类；没有中文的认领不算已输出；"
          "没播的不进分母；只有变体画出来时代表行算已输出；纯标点行不搜 obs（窗里别的台词带 `……` 也不算读到），记太短判不了；"
          "有锚点也要搜到读数（锚点没有读数支撑的记没读到）；整行是说话人名的判不了",
          got == want and why["h"] == "没有 cn 文本" and why["pp"] == GA.OBS_SHORT and why["bb"] == MD.NO_SUPPORT
          and why["nm"] == MD.NAME_LIKE
          and [r for r in out["rows"] if r["key"] == "d"][0].get("partial") == 0.27
          and "partial" not in [r for r in out["rows"] if r["key"] == "pp"][0], (got, why))
    check("检测器·特殊上屏形式（ReminderUI）按 special 分栏、默认进覆盖分母（这里没读到）；--hide-groups special 时记作决定不显示",
          [(r["group"], r["cat"]) for r in out["rows"] if r["key"] == "sp"] == [("special", MD.UNREAD)]
          and [r["cat"] for r in sp_hidden["rows"] if r["key"] == "sp"] == [MD.OFF],
          [(r["group"], r["cat"]) for rows in (out["rows"], sp_hidden["rows"]) for r in rows if r["key"] == "sp"])
    check("检测器·三条不变量在合成产物上成立（key 回得到剧本行、事件都在、漏都带定层、没有同一时刻画两块）", out["invariants"]["ok"],
          out["invariants"])
    check("检测器·B 清单：unmatched ∪ unfed 的事件扣掉被该出现行定位到的，剩文本包外的看板字、按区域记",
          b["rest"] == 1 and b["regions"][0]["region"] == 1 and b["regions"][0]["samples"] == ["知らない看板の文字です"], b)
    check("检测器·B 清单的文本包检索：文本包里有这句的事件单列（in_pack）", bp["in_pack"] == 1 and bp["regions"][0]["in_pack"] == 1, bp)
    check("检测器·定层各层：认领了没条目 / 定位弱（长句里只找到一小段，不按它定层）/ 同一段字归另一版本 / 认成别的行 / 没认上 / 没喂",
          layers == {"认领没条目": MD.L_NOITEM, "认领没撑住": MD.SIB, "定位弱": MD.L_WEAK, "同一段字": MD.SIB, "认成别的行": "喂了、认成别的行",
                     "没认上": "喂了、没认上", "没喂": "没喂"}, layers)
    check("检测器·认领只靠名牌（开头行）、或只靠一行含着这几个字的长界面字撑着的不算读到过；cue 里有一行正文撑得住才算",
          head_only == (False, True, False), head_only)
    check("检测器·认领撑不撑得住：带占位的行读数比原文长也算（不带占位的不算）；纯标点行有一行非名牌读数就算；`名前：台詞` 剥掉已知说话人名再比",
          claim_more == (True, False, True, True), claim_more)
    check("检测器·主轨外的事件记选项层第一道没过的门（这里是剧本行没有译文）",
          cw.get("choice_why") == "剧本行没有译文", cw)
    pfx = GA.Ref(doc={"units": [{"uid": "u", "lines": [
        L("ch", "どうして戦士を選抜する必要があるの？", kind="Choice"), L("npc", "そろそろ出発しようか", anchor=[9.0, 11.0])]}]},
        script=[], windows=[], role={}, variant_of={})
    from types import SimpleNamespace as _NS
    stub = _NS(main=set(), ad=_NS(prefix=SM.ChoicePrefix(pfx, SM.unit_spans(pfx)), unit_span=SM.unit_spans(pfx)), items_ev={},
               doc={"frame_us": 500_000})

    def why_p(text, t=10.5, kind="Choice"):
        ln = MD.Line("ch", "どうして戦士を選抜する必要があるの？", kind, None, "PLAYER", "u", "译文", 10.0, [],
                     texts=["どうして戦士を選抜する必要があるの？"])
        e = {"id": 0, "region": 3, "text": text, "box": [1500, 500, 1800, 540], "t_start": int(t * 1e6), "t_end": int(t * 1e6) + 3_000_000}
        return MD.Detector.choice_why(stub, e, ln)
    got_p = [why_p("どうして戦"), why_p("そろそろ出発し"), why_p("どうして戦士を選抜す", kind="Talk"),
             why_p("どうして戦士を選抜す", t=50.0), why_p("どうして戦士を選抜す")]
    check("检测器·没出块的原因跟着选项层的前缀路：不是够长的唯一前缀 / 前缀对上的是别的行 / 不是选项类 / 同单元别的行不在播，各报各的；四道门都过就往下查",
          got_p == ["字和剧本行不逐字相等，也不是够长、只对得上一种说法的前缀", "前缀对上的是别的行", "只是前缀，前缀路只收选项类",
                    "前缀对上了，但同一单元别的行此刻不在播", "过了各道门、块没出（并段 / 被阅读面板扣掉）"], got_p)
    check("检测器·不变量的反例：漏没定层、叠加条目的 key 回不到剧本行、引用的事件不在 tracks 里，都报出来",
          not bad["ok"] and bad["n_miss_without_layer"] == 1 and not bad2["ok"]
          and bad2["keys_unknown"] == ["nope"] and bad2["events_unknown"] == [999], (bad, bad2))


def t_gtdbundle() -> None:
    """游戏文本包（`gtd-bundle/1`）：找包、校验、两种形态读出同样的东西。

    存在的理由：剧本的分母出自哪份语料，只有文本包的指纹说得清；包拷坏了、换了版本、缺了语种，
    要在读库这一步明确报出来，而不是产出一份看起来正常的剧本。"""
    print("游戏文本包")
    import io
    import json
    from flowocr.analyze import gtdbundle as GB
    from flowocr.analyze import gamescript as GS
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        dirb = _mini_bundle(td / "dir" / "genshin")
        zp = _zip_bundle(dirb, td / "zips" / "genshin-1.0-x.zip")
        bd, bz = GB.open_bundle(dirb), GB.open_bundle(zp)
        rows_d = list(bd.iter_rows("quests", "ja"))
        rows_z = list(bz.iter_rows("quests", "ja"))
        pd, pz = bd.provenance(), bz.provenance()
        check("目录与 zip 两种形态：读出的行相同，provenance 只差 source（且只记名字、不记绝对路径），带上清单的 rev",
              rows_d == rows_z and len(rows_d) == 3
              and {**pd, "source": 0} == {**pz, "source": 0} and pz["source"] == zp.name and pz["rev"] == 1,
              f"{len(rows_d)} vs {len(rows_z)}；{pd} / {pz}")
        import zipfile as _zf
        with _zf.ZipFile(td / "zips" / "photos.zip", "w") as z:
            z.writestr("a.txt", "x")
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            found = GB.locate("genshin", td / "zips").source
        check("locate：包目录里按清单的 game 找到那一份；目录里无关的 zip（没有清单）跳过、不拖累定位", found.samefile(zp))

        bad = GB.open_bundle(_zip_bundle(dirb, td / "bad" / "genshin-1.0-y.zip", tamper="quests_jp.jsonl.zst"))
        check("成员被改过（清单没动）：读到底时报 sha256 对不上；缓存路径的 verify_stored 同样报",
              raises(lambda: list(bad.iter_rows("quests", "ja")), GB.BundleError)
              and raises(lambda: bad.verify_stored(["quests"]), GB.BundleError)
              and not raises(lambda: bz.verify_stored(["quests", "reminders"]), GB.BundleError))

        plain = GB.open_bundle(_mini_bundle(td / "plain" / "genshin", compress=False))
        check("同一内容不压缩：指纹不变（指纹只看解压后的内容）、读出的行相同",
              plain.fingerprint == bd.fingerprint and list(plain.iter_rows("quests", "ja")) == rows_d,
              f"{plain.fingerprint} vs {bd.fingerprint}")
        quiet = GB.open_bundle(_zip_bundle(plain.source, td / "quiet" / "genshin-1.0-q.zip",
                                           tamper="quests_jp.jsonl"))
        check("改动能正常解码（末尾换行变成别的空白，行照样读得出）：只有哈希抓得到，读到底时报",
              raises(lambda: list(quiet.iter_rows("quests", "ja")), GB.BundleError))

        # 改了内容、同时把清单里这个文件的存储哈希改成新值，内容哈希与指纹不动：清单自洽、指纹还和指纹表对得上
        import hashlib
        import zstandard
        forged = _mini_bundle(td / "forged" / "genshin")
        mf = json.loads((forged / "manifest.json").read_text(encoding="utf-8"))
        stored = zstandard.ZstdCompressor(level=3).compress(b'{"id": 0, "text": "forged"}\n{"id": 1}\n{"id": 2}\n')
        (forged / "quests_jp.jsonl.zst").write_bytes(stored)
        for f in mf["files"]:
            if f["path"] == "quests_jp.jsonl.zst":
                f.update(sha256=hashlib.sha256(stored).hexdigest(), bytes=len(stored))
        (forged / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")
        fb = GB.open_bundle(forged)
        with redirect_stdout(io.StringIO()) as out_ok:
            GB.main(["genshin", "--gametext", str(zp)])
        check("核对版本的命令：先核包里每个文件的两种哈希再打印指纹——内容被换、存储哈希跟着改的包，"
              "只核存储字节会放过（指纹照样对得上），verify 抓得到；好包照常打印指纹",
              fb.fingerprint == bd.fingerprint
              and not raises(lambda: fb.verify_stored(["quests"]), GB.BundleError)
              and raises(lambda: fb.verify(), GB.BundleError)
              and raises(lambda: GB.main(["genshin", "--gametext", str(forged)]), GB.BundleError)
              and raises(lambda: GB.main(["genshin", "--gametext", str(bad.source)]), GB.BundleError)
              and out_ok.getvalue().strip() == bd.fingerprint, out_ok.getvalue())

        nojp = GB.open_bundle(_mini_bundle(td / "nojp" / "genshin", langs=(("zh-Hans", "chs"),)))
        try:
            nojp.require({"quests": 1})
            msg = ""
        except GB.BundleError as exc:
            msg = str(exc)
        check("缺日文的包：require 报出缺的表 × 语种，而不是读的时候 FileNotFoundError", "quests×ja" in msg, msg)

        def contract_msg(change):
            cb = _mini_bundle(td / ("c-" + change) / "genshin")
            mc = json.loads((cb / "manifest.json").read_text(encoding="utf-8"))
            if change == "missing":
                del mc["tables"]["quests"]["contract"]
            else:
                mc["tables"]["quests"]["contract"] = "genshin.quests/2"
            (cb / "manifest.json").write_text(json.dumps(mc), encoding="utf-8")
            try:
                GB.open_bundle(cb).require({"quests": 1, "reminders": 1})
                return ""
            except GB.BundleError as exc:
                return str(exc)
        missing, newer = contract_msg("missing"), contract_msg("major")
        check("内容契约：清单里缺 contract（加契约前的旧包）、主版本不是这里认得的，require 都拒绝并说清是哪张表；对得上的照常过",
              "没有内容契约版本" in missing and "genshin.quests/2" in newer and "quests" in newer
              and not raises(lambda: bd.require({"quests": 1, "reminders": 1}), GB.BundleError), f"{missing} / {newer}")

        import shutil
        # Windows"全部解压缩"的默认布局：<包目录>/<zip 名>/<game>/manifest.json，比 zip 深两层
        shutil.copytree(dirb, td / "unz" / "genshin-1.0-x" / "genshin")
        shutil.copytree(dirb, td / "zips" / "genshin-1.0-x" / "genshin")
        with redirect_stdout(io.StringIO()):
            nested = GB.locate("genshin", td / "unz").source
            same = GB.locate("genshin", td / "zips")
        cands = GB.candidates(td / "zips")
        check("解压出来也认：包目录下再套一层（Windows 默认解压）找得到；和原 zip 并存时两份都是候选、指纹相同，算同一份、不报错",
              nested.samefile(td / "unz" / "genshin-1.0-x" / "genshin")
              and zp in cands and td / "zips" / "genshin-1.0-x" / "genshin" in cands
              and same.fingerprint == bd.fingerprint, f"{nested}；{cands}")

        _zip_bundle(_mini_bundle(td / "other" / "genshin", n=4), td / "zips" / "genshin-1.1-z.zip")
        check("同一款游戏两份内容不同的包：报错列出候选，不按版本号猜",
              raises(lambda: GB.locate("genshin", td / "zips"), GB.BundleError)
              and raises(lambda: GB.locate("zzz", dirb), GB.BundleError))

        m = json.loads((dirb / "manifest.json").read_text(encoding="utf-8"))
        wrong = td / "wrong" / "genshin"
        wrong.mkdir(parents=True)
        (wrong / "manifest.json").write_text(json.dumps({**m, "schema": "gtd-bundle/0"}), encoding="utf-8")
        lying = td / "lying" / "genshin"
        lying.mkdir(parents=True)
        (lying / "manifest.json").write_text(json.dumps({**m, "fingerprint": "sha256:" + "0" * 64}), encoding="utf-8")
        check("清单 schema 不认得、指纹和文件表自相矛盾：都拒绝",
              raises(lambda: GB.open_bundle(wrong), GB.BundleError)
              and raises(lambda: GB.open_bundle(lying), GB.BundleError))

        saved = (GS.GAMETEXT, dict(GS._BUNDLES))
        try:
            GS.GAMETEXT, GS._BUNDLES = str(zp), {}
            pairs_z = list(GS.paired("genshin", "reminders"))
            GS.GAMETEXT, GS._BUNDLES = str(dirb), {}
            pairs_d = list(GS.paired("genshin", "reminders"))
        finally:
            GS.GAMETEXT, GS._BUNDLES = saved[0], saved[1]
        check("gamescript 经 --gametext 读包：zip 与目录两种形态配出的日中行对相同",
              pairs_z == pairs_d and len(pairs_z) == 3 and pairs_z[0][0]["text"] == "reminders-ja-0")

        withp = _mini_bundle(td / "withp" / "genshin", tables=("quests", "reminders", "pronouns"))
        badp = _mini_bundle(td / "badp" / "genshin", tables=("quests", "reminders", "pronouns"))
        mb = json.loads((badp / "manifest.json").read_text(encoding="utf-8"))
        mb["tables"]["pronouns"]["contract"] = "genshin.pronouns/2"
        (badp / "manifest.json").write_text(json.dumps(mb), encoding="utf-8")
        saved = (GS.GAMETEXT, dict(GS._BUNDLES), dict(GS._PRON))
        got_p = []
        try:
            for src in (withp, dirb):
                GS.GAMETEXT, GS._BUNDLES, GS._PRON = str(src), {}, {}
                got_p.append(GS.pronoun_table("genshin"))
            GS.GAMETEXT, GS._BUNDLES, GS._PRON = str(badp), {}, {}
            bad_raises = raises(lambda: GS.pronoun_table("genshin"), GB.BundleError)
        finally:
            GS.GAMETEXT, GS._BUNDLES, GS._PRON = saved
        check("称谓表是可选表：包里有就读成 {语种: {键: 文本}}，没有是 None（称谓留成占位），契约主版本不对报错",
              got_p[0] == {"ja": {i: f"pronouns-ja-{i}" for i in range(3)}, "zh-Hans": {i: f"pronouns-zh-Hans-{i}" for i in range(3)}}
              and got_p[1] is None and bad_raises, got_p)


def t_gametext() -> None:
    """游戏文本库 -> 剧本（gamescript）与量轨（game_align）的几处判据。

    存在的理由：这一层决定**分母**——选项 / 没走的分支 / 重演拷贝要是漏排，
    就会以 1–3 条的短 gap 出现、全被算成"疑似真漏"，而报表照样是绿的。
    """
    print("游戏文本库")
    from flowocr.analyze import gamescript as GS
    from flowocr.analyze import game_align as GA
    import gametext_report as GR
    from flowocr.analyze import script_align as SA
    from flowocr.artifacts import tracksio

    check("原神语音字幕：Normal（字幕带，摆帧过）进分母，Banner 这类不进",
          "Reminder" not in GS.NON_BAND_KINDS and "ReminderUI" in GS.NON_BAND_KINDS)
    # 星铁选项：NPC 的回答在选项的 `reply` 里（上屏，属于该选项的分支）；wait 引用的是选项的
    # `trigger`（= 回答那句），不是选项自己的 id——两处第一版都错过（整场 hsr 反查出来的）
    ev = [{"kind": "option", "sentence_id": 3, "text": "はい", "trigger": "TalkSentence_4",
           "reply": {"sentence_id": 4, "speaker": "F", "text": "回答A"}},
          {"kind": "option", "sentence_id": 6, "text": "いいえ", "trigger": "TalkSentence_7",
           "reply": {"sentence_id": 7, "speaker": "F", "text": "回答B"}},
          {"kind": "wait", "custom_string": "TalkSentence_4", "sentence_id": 4, "text": "回答A"},
          {"kind": "talk", "sentence_id": 9, "text": "Aの続き"},
          {"kind": "wait", "custom_string": "TalkSentence_7", "sentence_id": 7, "text": "回答B"},
          {"kind": "talk", "sentence_id": 10, "text": "Bの続き"},
          {"kind": "wait", "custom_string": "TalkSentence_99"},
          {"kind": "talk", "sentence_id": 11, "text": "合流"}]
    u = GS._hsr_script("hsr:x", None, ev, ev)
    got = {ln.key: (ln.kind, ln.memberships) for ln in u.lines}
    check("星铁选项的回答进剧本、归自己那一支；wait 按选项的 trigger 认分支段；认不出的回主干",
          got == {"hsr:3": ("Choice", []), "hsr:4": ("Talk", [("hsr:x#g1", "3")]),
                  "hsr:6": ("Choice", []), "hsr:7": ("Talk", [("hsr:x#g1", "6")]),
                  "hsr:9": ("Talk", [("hsr:x#g1", "3")]), "hsr:10": ("Talk", [("hsr:x#g1", "6")]),
                  "hsr:11": ("Talk", [])}, got)
    # 汇总表按"段 + 臂后缀"分组：整场 hsr / zzz 没有编号，旧正则直接不认、整段从表里消失
    check("汇总表认得切片 / 整场 / 只有一场的整场，并把臂后缀剥出来",
          [GR.TAG_RE.match(s).groups() for s in ("gi-s1", "gi1-band0", "hsr", "zzz-sr20",
                                                 "zzz-s2-median")]
          == [("gi-s1", ""), ("gi1", "-band0"), ("hsr", ""), ("zzz", "-sr20"),
              ("zzz-s2", "-median")])

    raw = "#{M#俺}{F#私}は{NICKNAME}の<color=#FFCC00FF>友達</color>{RUBY#[S]ルビ}だ\\n二行目"
    check("性别写法二选一、玩家占位 / 标签 / 注音删掉、\\n 写成 \\N",
          (GS.clean(raw, "F"), GS.clean(raw, "M")) == ("私はの友達だ\\N二行目", "俺はの友達だ\\N二行目"),
          (GS.clean(raw, "F"), GS.clean(raw, "M")))
    rich = GS.clean("#{NICKNAME}，<color=#FFCC00FF>友<b>達</b></color><size=30>大</size>\\n<i>二行目</i>", "F", rich=True)
    check("样式：译文 / 原文模板另存一份带样式的（规范写法：小写、去透明度），字号等别的标签删掉；和 keep 的那份只差标签；跨行照留",
          rich == "{NICKNAME}，<color=#ffcc00>友<b>達</b></color>大\\N<i>二行目</i>"
          and GS.richtext.plain(rich) == GS.clean("#{NICKNAME}，<color=#FFCC00FF>友<b>達</b></color><size=30>大</size>\\n<i>二行目</i>", "F", keep=True),
          rich)
    check("绝区零的 <ruby><rt>注音</rt>正文</ruby>：注音整个删，正文留着（原来注音的字留在正文里）",
          GS.clean("通过<ruby><rt>图灵测试</rt>禁果测试</ruby>的", "F") == "通过禁果测试的")
    ruby = {"漢{RUBY#[D]かんじ}字です": "<ruby=かんじ>漢字</ruby>です", "漢{RUBY#[S]かん}字": "<ruby=かん>漢</ruby>字",
            "これ漢字{RUBY#[D]かんじ}": "これ漢<ruby=かんじ>字</ruby>", "{RUBY#[S]x}字": "字", "{NICKNAME}{RUBY#[S]x}字": "{NICKNAME}字",
            "<b>漢</b>{RUBY#[D]かんじ}<i>字</i>": "<ruby=かんじ><b>漢</b><i>字</i></ruby>",
            "漢{RUBY#[D]a}字{RUBY#[D]b}語": "<ruby=a>漢字</ruby><ruby=b>語</ruby>"}
    got = {raw: GS.clean(raw, "F", rich=True) for raw in ruby}
    check("Ruby·原神：标的是读音的中心——[D] 包住两边各一个字、[S] 包住前一个字；[D] 在句尾当 [S]，[S] 在句首、前面是占位的丢掉；"
          "隔着的标签跳过（交叉的样式拆开、Ruby 不拆）；归了前一处的字不再用；去掉标签和不带样式的那份相同",
          got == ruby and all(GS.richtext.plain(r) == GS.clean(raw, "F", keep=True) for raw, r in got.items()), got)
    ruby = {"和{RUBY_B#岁月之泰坦}欧洛尼斯{RUBY_E#}一样": "和<ruby=岁月之泰坦>欧洛尼斯</ruby>一样",
            "你在{RUBY_B#{TEXTJOIN#254}}个人媒体{RUBY_E#}上": "你在<ruby={TEXTJOIN#254}>个人媒体</ruby>上",
            "的{RUBY_B#<color=#dbc291ff>负世之泰坦</color>}<color=#dbc291ff>刻法勒</color>{RUBY_E#}": "的<ruby=负世之泰坦><color=#dbc291>刻法勒</color></ruby>",
            "愣着{RUBY_B#音同「霸」}{RUBY_E#}矲主": "愣着矲主",
            "通过<ruby><rt>图灵测试Plus版本</rt>禁果测试</ruby>的": "通过<ruby=图灵测试Plus版本>禁果测试</ruby>的",
            "[<ruby=くさびがた>楔形</ruby>陣形]": "[<ruby=くさびがた>楔形</ruby>陣形]"}
    got = {raw: GS.clean(raw, "F", rich=True) for raw in ruby}
    check("Ruby·星铁 / 绝区零：有范围的照范围（读音是占位的留着、读音里的标签删掉、正文空的不要）；绝区零两种写法都认；去掉标签和不带样式的那份相同",
          got == ruby and all(GS.richtext.plain(r) == GS.clean(raw, "F", keep=True) for raw, r in got.items()), got)
    from types import SimpleNamespace
    from flowocr import placeholders as PH
    from flowocr.analyze.matchers import gametext as GT
    from flowocr.artifacts import matchedio as MIO
    from flowocr.output import script as SC
    check("占位：译文 / 原文模板里原样留着（全部花括号，不只玩家名），用来匹配的原文删掉；星铁注音的两个花括号是排版、照删",
          GS.clean("#{NICKNAME}，你好{ABYSSWAR#1}", "F", keep=True) == "{NICKNAME}，你好{ABYSSWAR#1}"
          and GS.clean("#{NICKNAME}，你好", "F") == "你好"
          and GS.clean("和{RUBY_B#岁月之泰坦}欧洛尼斯{RUBY_E#}一样", "F", keep=True) == "和欧洛尼斯一样")
    mk = PH.Marks({"{NICKNAME}": {"value": "ねる[1]", "how": "从视频原文读出（5/5 票）"}})
    got_m = [mk.mark("{NICKNAME}，你好{ABYSSWAR#1}"), mk.mark("嗯，{NICKNAME}"), mk.mark("没有占位"), mk.mark(None)]
    check("阶段 3 标占位：按出现先后编号、同一个占位同一个号；值是匹配器猜的、没有就空着；值里的方括号换成全角；图例记原来的占位和来源",
          got_m == ["[`0:ねる［1］]，你好[`1:]", "嗯，[`0:ねる［1］]", "没有占位", None]
          and mk.legend() == {"FlowOCR Placeholder 0": "{NICKNAME} 从视频原文读出（5/5 票）",
                              "FlowOCR Placeholder 1": "{ABYSSWAR#1} 匹配器没给值"}
          and dict(mk.n) == {0: 2, 1: 1}, (got_m, mk.legend()))
    legend = PH.legend_of(["Title: x", "FlowOCR Placeholder 0: {NICKNAME} 从视频原文读出（5/5 票）",
                           "FlowOCR Placeholder 1: {ABYSSWAR#1} 匹配器没给值"])
    got_a = [PH.apply("{\\an7\\pos(1,2)}[`0:荧]，你好", legend),
             PH.apply("{\\an7\\pos(1,2)}[`1:]，你好\\N嗯，[`1:]。", legend),
             PH.apply("[`5:x]，你好", legend), PH.apply("荧，你好", legend)]
    check("阶段 4 换占位：图例里有的标记换成值；值空的删掉、收拾落单的标点（行首在 tag 块后面算）；图例里没有的编号原样留着；人改成文字的没什么可换",
          legend == {0: "{NICKNAME}", 1: "{ABYSSWAR#1}"}
          and got_a == [("{\\an7\\pos(1,2)}荧，你好", [(0, "荧")]), ("{\\an7\\pos(1,2)}你好\\N嗯。", [(1, ""), (1, "")]),
                        ("[`5:x]，你好", []), ("荧，你好", [])], got_a)
    got_b = [PH.apply(x, legend)[0] for x in ("{\\an5}「[`1:]，你好」", "嗯（[`1:]，趁现在！）", "[`1:]，\\N你好呀", "[`1:]\\N你好", "「[`1:]！」")]
    check("空占位在开口括号后面也算句首（不留落单的逗号）；收拾标点不跨行，整行只剩占位 + 分隔号的留成空行（行数不变，阶段 4 按原文框逐行摆）",
          got_b == ["{\\an5}「你好」", "嗯（趁现在！）", "\\N你好呀", "\\N你好", "「！」"], got_b)
    mk2 = PH.Marks({"{NICKNAME}": {"value": "a]b{c}\\N"}})
    check("标记里的值：会打断标记、会被 ASS 当成特效 / 换行的字换成全角；图例里带空格的占位整个读出来",
          mk2.mark("{NICKNAME}") == "[`0:a］b｛c｝＼N]"
          and PH.legend_of(["FlowOCR Placeholder 0: {大地獣, ネクタール} 匹配器没给值"]) == {0: "{大地獣, ネクタール}"},
          mk2.mark("{NICKNAME}"))
    tb = {"INFO_MALE_PRONOUN_HE": "他", "INFO_FEMALE_PRONOUN_SHE": "她",
          "INFO_MALE_PRONOUN_BROTHER": "哥哥", "INFO_FEMALE_PRONOUN_SISTER": "妹妹"}
    sx = [GS.sexpro("神秘的{PLAYERAVATAR#SEXPRO[INFO_MALE_PRONOUN_HE|INFO_FEMALE_PRONOUN_SHE]}来了", tb),
          GS.sexpro("找{MATEAVATAR#SEXPRO[INFO_MALE_PRONOUN_BROTHER|INFO_FEMALE_PRONOUN_SISTER]}", tb),
          GS.sexpro("{PLAYERAVATAR#SEXPRO[INFO_MALE_PRONOUN_X|INFO_FEMALE_PRONOUN_SHE]}", tb)]
    check("原神称谓占位改写成性别写法（旅行者按自己的性别；另一位双子男女对调）；表里没有的键原样留着",
          sx == ["神秘的{M#他}{F#她}来了", "找{M#妹妹}{F#哥哥}", "{PLAYERAVATAR#SEXPRO[INFO_MALE_PRONOUN_X|INFO_FEMALE_PRONOUN_SHE]}"]
          and [GS.clean(sx[0], g, keep=True) for g in "MF"] == ["神秘的他来了", "神秘的她来了"]
          and GS.clean(sx[1], "M", keep=True) == "找妹妹", sx)
    check("不断行空格是排版写法、换成空格，不当占位；图标字删掉，不当占位",
          GS.clean("近くて{NON_BREAK_SPACE}遠い", "F", keep=True) == "近くて 遠い"
          and GS.clean("按{REGEX#UNICODE[E001]}键", "F", keep=True) == "按键")
    check("删占位收拾落单的标点：行首吃后面的分隔号 / 叹号 / 问号（连着几个占位、包在标签里也算）、句末吃前面的、夹在两个中间留一个；整行只剩句号留着；句中的照删",
          [GS.clean(r, "F") for r in ("#{NICKNAME}，刚刚我说过", "你好，{NICKNAME}。", "嗯，{NICKNAME}，走吧",
                                      "{NICKNAME}、さっき", "一行\\n{NICKNAME}，二行", "这是{NICKNAME}的书",
                                      "#{NICKNAME}！快！", "{NICKNAME}{PLAYERAVATAR#SEXPRO[A|B]}，走", "{NICKNAME}。",
                                      "<color=#FFF>{NICKNAME}</color>？！你来了", "{NICKNAME}，{NICKNAME}，快醒醒。")]
          == ["刚刚我说过", "你好。", "嗯，走吧", "さっき", "一行\\N二行", "这是的书", "快！", "走", "。", "你来了", "快醒醒。"])
    split = SC.wrap_lines("[`0:ねるめろ(本物)]，刚刚我说过的那件事你还记得吗", 6)
    check("译文按文本框折行时，占位标记当一个字、不拆开（宽按值算：值比一行还宽也整个留在一行里）",
          len(split) >= 2 and "".join(split) == "[`0:ねるめろ(本物)]，刚刚我说过的那件事你还记得吗"
          and split[0] == "[`0:ねるめろ(本物)]，"
          and all(x.count("[`") == len(PH.MARK_RE.findall(x)) for x in split), split)
    check("从视频原文读名字：模板在占位前后的字在 OCR 里找得到，夹在中间的就是名字（句首 / 句中 / 句尾）；占位在句首时只在那一行找（前面的名牌行不切进来）；紧贴着另一个占位时不截（分不出两个值）；找不到是 None",
          GT.guess("ルミネ、さっき言ったように", "{NICKNAME}、さっき言ったように") == "ルミネ"
          and GT.guess("おい、ルミネ！", "おい、{NICKNAME}！") == "ルミネ"
          and GT.guess("お前はルミネだな", "お前は{NICKNAME}だな") == "ルミネ"
          and GT.guess("全然違う文", "{NICKNAME}、さっき言ったように") is None
          and GT.guess("派蒙\nねるめろ、最近どう", "{NICKNAME}、最近どう") == "ねるめろ"
          and GT.guess("ルミネ、さっき", "{NICKNAME}{PLAYERAVATAR#SEXPRO[A|B]}、さっき") is None)
    check("名牌候选：去掉首尾分隔号；带换行的、只有数字 / 单个拉丁字母的（键位提示）不算；名字里的括号留着",
          GT.name_ok("ねるめろ(本物)") == "ねるめろ(本物)" and GT.name_ok("ぴくと：") == "ぴくと"
          and GT.name_ok("派蒙\nねるめろ") is None and GT.name_ok("F") is None and GT.name_ok("12") is None
          and GT.guess("お前たち…ねるめろ(本物)と眷属", "お前たち…{NICKNAME}と眷属") == "ねるめろ(本物)")
    inf = GT.infer([("ルミネ、さっき", "{NICKNAME}、さっき"), ("おい、ルミネ！", "おい、{NICKNAME}！"), ("ルミナ、行こう", "{NICKNAME}、行こう")])
    check("读名字整部片投票：得票最多、≥ 2 票、严格超过读得出的一半才采用；票不够、平票都是 None",
          inf["value"] == "ルミネ" and inf["votes"] == 2 and inf["of"] == 3
          and GT.infer([("ルミネ、さっき", "{NICKNAME}、さっき")])["value"] is None
          and GT.infer([], ["甲", "甲", "乙", "乙"])["value"] is None and GT.infer([], ["乙", "乙", "甲", "甲"])["value"] is None)
    ln = lambda k, cn, tpl=None, role=None: {"key": k, "cn": cn, "role": role, **({"jp_tpl": tpl} if tpl else {})}
    fake = lambda game: SimpleNamespace(doc={"provenance": {"game": game}, "units": [{"lines": [
        ln("a", "{NICKNAME}，你好", "{NICKNAME}、さっき"), ln("b", "走吧", role="PLAYER"), ln("c", "没有占位")]}]})
    recs = [{"ref": {"key": "a"}, "ocr": "ルミネ、さっき", "cues": [0]}, {"ref": {"key": "b"}, "ocr": "行こう", "cues": [1]}]
    evs = [[], [{"text": "ルミネ", "flags": ["nameplate"]}, {"text": "パイモン"}]]
    got_f = [GT.fills(fake("genshin"), recs, evs), GT.fills(fake("starrail"), recs, evs),
             GT.fills(fake("genshin"), recs[1:], evs)]
    check("匹配器的 fills：认上的行有 {NICKNAME} 才猜；原神另数主角台词那条 cue 里打了名牌标的事件（星铁不数）；没有就是空字典",
          got_f[0]["{NICKNAME}"]["value"] == "ルミネ" and got_f[0]["{NICKNAME}"]["how"] == "从视频原文读出（2/2 票）"
          and got_f[1]["{NICKNAME}"]["value"] is None and got_f[2] == {}
          and GT.needs_events(fake("genshin")) and not GT.needs_events(fake("zzz")), got_f)
    tj = {"default": 0, "jp": ["次元プーマン", "プーマン", "泡泡"], "cn": ["次元扑满", "扑满", "泡泡"]}
    T = "{TEXTJOIN#54}"
    got_pi = [GT.pick_item("あれはプーマンだよ", "あれは{TEXTJOIN#54}だよ", tj["jp"], T),
              GT.pick_item("次元プーマン、おいで", "{TEXTJOIN#54}、おいで", tj["jp"], T),
              GT.pick_item("あれはモフモフだよ", "あれは{TEXTJOIN#54}だよ", tj["jp"], T),
              GT.pick_item("全然違う…次元プーマンが", "あれは{TEXTJOIN#54}だよ", tj["jp"], T),
              GT.pick_item("全然違う", "あれは{TEXTJOIN#54}だよ", tj["jp"], T),
              GT.pick_item("改めて——プーマン、星、ここに", "改めて——{TEXTJOIN#54}、{NICKNAME}、ここに", tj["jp"], T)]
    check("星铁玩家起名的占位：按锚点截出那段（截不出就整句）里找候选、取最长的（截出的那段连着旁边的占位也认得出）；截得出却没有候选的是自定义",
          got_pi == [("item", 1), ("item", 0), ("custom", "モフモフ"), ("item", 0), None, ("item", 1)], got_pi)
    c87 = ["暉長石号", "テールオブパム号"]
    got_fz = [GT.pick_item("同時刻、輝長石号", "同時刻、{TEXTJOIN#87}", c87, "{TEXTJOIN#87}"),
              GT.pick_item("元プーマンを返還する", "{TEXTJOIN#54}を返還する", tj["jp"], T),
              GT.pick_item("泡泡を返遠する、このプーマンの", "{TEXTJOIN#54}を返還する、このプーマンの", tj["jp"], T),
              GT.pick_item("1 37枚", "1{TEXTJOIN#44}{TEXTJOIN#45}枚", ["3", "7", "1"], "{TEXTJOIN#44}"),
              GT.pick_item("ペペ！ペペおいで！ペペ！", "{TEXTJOIN#23}！{TEXTJOIN#23}おいで！{TEXTJOIN#23}！", ["ペペ"], "{TEXTJOIN#23}"),
              GT.guess("「ミミ」、また明日", "「{TEXTJOIN#191}」、また明日", "{TEXTJOIN#191}")]
    check("认候选宁缺毋错：错一个字按相似度认得出；截短后和别的候选分不清就不投；整句回退时剧本固定文字里的候选不算；"
          "几个互不包含的候选同时命中算分不清；同一占位出现几次只按第一个截；名字尾巴上没配对的括号去掉",
          got_fz == [("item", 0), None, ("item", 2), None, ("item", 0), "ミミ"], got_fz)
    tj2 = {"default": 0, "jp": ["次元プーマン", "ポポ", "プー神"], "cn": ["次元扑满", "波波", "扑神"]}
    check("另一个占位（或同一个再出现）只隔着标点时不截（`星！星`、`モフモフ、星` 不当名字）；短候选读错一个字分不清、不投自定义票；离所有候选都远才算自定义",
          GT.guess("星！星、行こう", "{NICKNAME}！{NICKNAME}、行こう") is None
          and GT.guess("モモ！モモ、おいで", "{TEXTJOIN#23}！{TEXTJOIN#23}、おいで", "{TEXTJOIN#23}") is None
          and GT.guess("星、さっきは星", "{NICKNAME}、さっきは{NICKNAME}") == "星"
          and GT.guess("よし\nルミネ、さっき", "よし\\N{NICKNAME}、さっき") == "ルミネ"
          and GT.guess("改めて——モフモフ、星、ここに", "改めて——{TEXTJOIN#54}、{NICKNAME}、ここに", "{TEXTJOIN#54}") is None
          and GT.guess("改めて——星、モフモフ、ここに", "改めて——{NICKNAME}、{TEXTJOIN#54}、ここに", "{TEXTJOIN#54}") is None
          and GT.pick_item("あれはプー袖だよ", "あれは{TEXTJOIN#54}だよ", tj2["jp"], T) is None
          and GT.pick_item("あれはモフモフだよ", "あれは{TEXTJOIN#54}だよ", tj2["jp"], T) == ("custom", "モフモフ")
          and GT.pick_item("改めて——元プーマン、星、ここに", "改めて——{TEXTJOIN#54}、{NICKNAME}、ここに", tj["jp"], T) is None
          and GT.pick_item("改めて——プーマン、星、ここに", "改めて——{TEXTJOIN#54}、{NICKNAME}、ここに", tj["jp"], T) == ("item", 1))
    one = {"key": "a", "cn": "那是{TEXTJOIN#54}", "jp_tpl": "あれは{TEXTJOIN#54}だよ"}
    two = {"key": "c", "cn": "{TEXTJOIN#54}来了", "jp_tpl": "{TEXTJOIN#54}が来た"}
    r1 = GT.textjoin_fills(SimpleNamespace(doc={"textjoins": {"54": tj2}}),
                           [{"ref": {"key": "a"}, "ocr": "あれはモフモフだよ"}] * 2, {"a": one})
    r2 = GT.textjoin_fills(SimpleNamespace(doc={"textjoins": {"54": tj2}}),
                           [{"ref": {"key": "a"}, "ocr": "あれはモフモフだよ"}, {"ref": {"key": "c"}, "ocr": "モフモフが来た"}],
                           {"a": one, "c": two})
    check("自定义名字的票按剧本行算：同一行两条 cue 只算 1 票（回落默认项），两行各一票才采用",
          "默认值" in r1["{TEXTJOIN#54}"]["how"] and r2["{TEXTJOIN#54}"]["value"] == "モフモフ", (r1, r2))
    check("候选里嵌着的玩家名换成读出的名字、读不出就删；别的占位删掉；删空了是 None",
          GT._filled("{NICKNAME}", "星") == "星" and GT._filled("{NICKNAME}", None) is None
          and GT._filled("ありがとう、{TEXTJOIN#59}！", None) == "ありがとう！" and GT._filled(None, "星") is None)
    check("星铁注音里嵌占位（{RUBY_B#{TEXTJOIN#n}}）整段删；候选和台词一样清洗（候选自带的注音删掉）",
          GS.clean("あなたが{RUBY_B#{TEXTJOIN#254}}個人メディア", "F", keep=True) == "あなたが個人メディア"
          and GS.clean("タルタロフ{RUBY_B#ごう}号{RUBY_E#}", "F") == "タルタロフ号")
    lns = {"a": {"key": "a", "cn": "那是{TEXTJOIN#54}", "jp_tpl": "あれは{TEXTJOIN#54}だよ"},
           "b": {"key": "b", "cn": "{TEXTJOIN#87}出发", "jp_tpl": "{TEXTJOIN#87}、出発"}}
    fref = SimpleNamespace(doc={"textjoins": {"54": tj, "87": {"default": 1, "jp": ["甲号", "乙号"], "cn": ["甲号", "乙号"]}}})
    recs_tj = [{"ref": {"key": "a"}, "ocr": "あれはプーマンだよ"}, {"ref": {"key": "a"}, "ocr": "あれはプーマンだよ！"},
               {"ref": {"key": "b"}, "ocr": "全然読めない"}]
    tf = GT.textjoin_fills(fref, recs_tj, lns)
    check("TEXTJOIN 的 fills：认出的候选按语种记 by_lang；没认出用默认项；Marks 按输出语种取值",
          tf["{TEXTJOIN#54}"]["by_lang"] == {"jp": "プーマン", "cn": "扑满"} and tf["{TEXTJOIN#54}"]["votes"] == 2
          and tf["{TEXTJOIN#87}"]["by_lang"] == {"jp": "乙号", "cn": "乙号"} and "默认值" in tf["{TEXTJOIN#87}"]["how"]
          and PH.Marks(tf, "cn").mark("那是{TEXTJOIN#54}") == "那是[`0:扑满]"
          and PH.Marks(tf, "jp").mark("あれは{TEXTJOIN#54}") == "あれは[`0:プーマン]"
          and GT.textjoin_fills(SimpleNamespace(doc={}), recs_tj, lns) == {}, tf)
    lns["c"] = {"key": "c", "cn": "你在个人媒体上", "cn_rich": "你在<ruby={TEXTJOIN#87}>个人媒体</ruby>上"}
    check("TEXTJOIN 只出现在 Ruby 读音里（带样式那份）也给 fills（默认项）",
          "{TEXTJOIN#87}" in GT.textjoin_fills(fref, [{"ref": {"key": "c"}, "ocr": "x"}], lns))
    rec = {"ref": {"jp": "さっき", "jp_tpl": "{NICKNAME}、さっき", "cn": "{NICKNAME}，你好"}, "ocr": "x"}
    check("matched 的正文：日文取留着占位的原文模板、中文取译文；给了 Marks 就标出来（SRT 用），不给原样",
          MIO.body(rec, "jp", PH.Marks({"{NICKNAME}": {"value": "荧"}})) == ["[`0:荧]、さっき"]
          and MIO.body(rec, "cn") == ["{NICKNAME}，你好"] and MIO.text_of({"jp": "さっき"}, "jp") == "さっき")
    check("括号独白才算进字幕带的玩家台词",
          GS.is_monologue("（{F#私}が行く…）") and not GS.is_monologue("行こう。"))
    check("检索归一：小书写假名并大写、标点空白去掉",
          GS.cnorm("そっか…。ぃ A") == GS.cnorm("そつか。い A") == "そつかいA")
    # 1 -> {2 选项A, 3 选项B}；2 -> 4 -> 6；3 -> 5 -> 6（6 是汇合点，两边都能到）
    ex = GS.exclusive_members({1: [2, 3], 2: [4], 3: [5], 4: [6], 5: [6], 6: []})
    check("独占可达集：汇合点不属于任何一支，回复只属于自己那一支",
          ex.get(4) == [(1, 2)] and ex.get(5) == [(1, 3)] and 6 not in ex and 1 not in ex, dict(ex))
    # 选项 B 绕回分支点：B 的独占集只剩它自己那一截
    ex2 = GS.exclusive_members({1: [2, 3], 2: [4], 3: [5], 4: [], 5: [1]})
    check("选项绕回分支点时独占集只剩回复那一截",
          ex2.get(5) == [(1, 3)] and ex2.get(4) == [(1, 2)] and 1 not in ex2, dict(ex2))
    check("同组兄弟走过而自己没走 -> Unwalked；谁都没走 -> Ambiguous；走过 -> 不改",
          (GS.branch_kind([("g", "B")], {"g": {"A"}}), GS.branch_kind([("g", "B")], {}),
           GS.branch_kind([("g", "A")], {"g": {"A"}})) == ("Unwalked", "Ambiguous", None))

    def unit(uid, texts, span=0.0, keys=None):
        eps = [[0.0, 0.0], [span, span]] if span else [[0.0, 0.0]]
        return {"uid": uid, "lines": [{"key": (keys or {}).get(i, f"{uid}{i}"), "kind": "NPC", "status": None,
                                       "text": t, "anchor": {"t0": 0.0, "t1": span, "queries": 1,
                                                             "episodes": eps}}
                                      for i, t in enumerate(texts)]}
    check("『上屏过两次』看两次上屏之间的间隔，一条挂了一分多钟的台词不算",
          not GS.shown_twice({"episodes": [[0.0, 90.0]]}, 60.0)
          and GS.shown_twice({"episodes": [[0.0, 5.0], [300.0, 305.0]]}, 60.0))
    check("绝区零教程提示按平台给的几种写法：PC 流留键盘那一种，没有就留 FALLBACK",
          GS.clean("{LAYOUT_MOBILE#「OK」をタップ}{LAYOUT_KEYBOARD#「OK」を選択}してください", "F")
          == "「OK」を選択してください"
          and GS.clean("{LAYOUT_FALLBACK#タップして}{LAYOUT_CONTROLLER#押して}移動", "F") == "タップして移動")
    us = [unit("a", ["今日はいい天気だね"]), unit("b", ["別の台詞", "今日はいい天気だね"],
                                                   span=300.0, keys={1: "a0"})]
    check("同一个节点挂在两个单元下：后一份无条件记 Duplicate（key 重复会让查表互相覆盖）",
          GS.mark_duplicates(us, 0.5, 60.0) == 1 and us[1]["lines"][1]["status"] == "Duplicate")
    us = [unit("a", ["今日はいい天気だね", "そうだね", "散歩に行こう"]),
          unit("b", ["今日はいい天気だね", "そうだね", "散歩に行こう", "新しい台词だよ"])]
    n = GS.mark_duplicates(us, 0.5, 60.0)
    check("重演拷贝：后一份里和前面重复的行记 Duplicate，独有的行留着",
          n == 3 and [l["status"] for l in us[1]["lines"]] == ["Duplicate"] * 3 + [None]
          and {l["kind"] for l in us[1]["lines"]} == {"NPC"}, n)
    us = [unit("a", ["今日はいい天気だね", "散歩に行こう"]),
          unit("b", ["今日はいい天気だね", "散歩に行こう"], span=300.0)]
    check("obs 里隔了很久又出现一次 = 真的重播，不记 Duplicate",
          GS.mark_duplicates(us, 0.5, 60.0) == 0)

    g = GS.variant_groups(["リンさん、ビリーさん、聞こえるかしら？", "アキラさん、ビリーさん、聞こえるかしら？",
                           "いいわね。では実戦訓練に移行するわ。", "……"])
    check("绝区零两位主角的相邻两种说法连成一组，别的行不连", g == [[0, 1]], g)
    eps = {1: [[100.0, 105.0]], 8: [[5000.0, 5005.0]], 10: [[5030.0, 5031.0]], 17: [[5100.0, 5101.0]]}
    check("没播的几截：头尾之外，中间零锚点 ≥5 行且两头隔了几个小时的也算；正常播完的一段不算",
          GS.unplayed([1, 8, 10, 17], eps, 20) == {0, 2, 3, 4, 5, 6, 7, 18, 19},
          sorted(GS.unplayed([1, 8, 10, 17], eps, 20)))
    check("两头锚点前后颠倒（后一行更早就上屏过）也算没播的一截",
          GS.unplayed([0, 6], {0: [[900.0, 901.0]], 6: [[100.0, 101.0]]}, 7) == {1, 2, 3, 4, 5})
    check("上屏时段：相邻出现隔 ≤3 秒算同一次，隔一小时算另一次",
          GS.episodes([10.0, 10.5, 12.0, 3600.0]) == [[10.0, 12.0], [3600.0, 3600.0]])
    seq_units = [{"uid": "a", "t0": 10.0, "lines": [
                     {"key": "a0", "kind": "NPC", "status": None, "anchor": None},
                     {"key": "a1", "kind": "NPC", "status": None, "anchor": {"episodes": [[10.0, 12.0]]}},
                     {"key": "a2", "kind": "NPC", "status": None, "anchor": None},
                     {"key": "a3", "kind": "NPC", "status": None, "anchor": {"episodes": [[90.0, 95.0], [3000.0, 3004.0]]}}]},
                 {"uid": "b", "t0": 50.0, "lines": [
                     {"key": "b0", "kind": "NPC", "status": None, "anchor": {"episodes": [[50.0, 51.0]]}},
                     {"key": "b1", "kind": "NPC", "status": "Duplicate", "anchor": None}]}]
    seq = GS.sequence(seq_units, 120.0, 30.0)
    check("对齐序列按行排：单元 a 的后半截排到 b 后面、拷贝不进",
          [k for k, _, _ in seq] == ["a0", "a1", "a2", "b0", "a3"], seq)
    w = {k: w for k, _, w in seq}
    check("可认领时段：有锚点的每次上屏两头外扩；没锚点的借前一个锚点往后放宽，单元开头的借后一个往前放宽",
          w["a3"] == [[60.0, 125.0], [2970.0, 3034.0]] and w["a2"] == [[10.0, 132.0]]
          and w["a0"] == [[-110.0, 12.0]], w)

    v = GA.variants("シュラム\n傭兵\nさすがのオレでも、\n戦争とは無関係\nD")
    check("cue 的读法：去掉名牌 / 称号 / 末尾键位",
          SA.norm("さすがのオレでも、戦争とは無関係") in v and SA.norm("シュラム傭兵さすがのオレでも、戦争とは無関係D") in v,
          v)
    # 按时间圈候选：短句的精确匹配不会把后面的对齐带跑；同一句切成两条 cue 都认领同一行；
    # 窗口外的同文本行不认领
    texts = ["うん", "オレならこの場を切り抜けられると思った", "次の台詞はこちらです", "うん"]
    script = [SA.Entry(key=str(i), kind="NPC", cmd="", text=t, src="u") for i, t in enumerate(texts)]
    wins = [[[0.0, 100.0]], [[0.0, 40.0]], [[0.0, 50.0], [3000.0, 3030.0]], [[800.0, 1000.0]]]
    subs = [(10.5, "うん", "x"), (12.5, "オレならこの場を切り抜けられると思った", "x"),
            (14.0, "オレならこの場を切り抜けられると思つた", "x"), (3010.0, "次の台詞はこちらでず", "x"),
            (900.0, "うん", "x")]
    st = GA.run(script, wins, subs, 0.75)
    check("按时段圈候选：切开的两条 cue 认领同一行、隔一小时的第二次上屏也配得上、同文本各归各的时段",
          st["sub_hit"] == [0, 1, 1, 2, 3], st["sub_hit"])
    check("原始轨口径的重复认领：一行被 3 条 cue 认领 = 多出 2 次",
          GA.repeat_claims([0, 0, 0, 1, -1]) == (1, 2))
    # 产物的重复认领按带内 / 带外分开报（owner 2026-10-06）：带外（UI 文案、选项）本来就反复上屏，不进报警
    bscript = [SA.Entry(key="t", kind="Talk", cmd="", text="台詞", src="u"),
               SA.Entry(key="u", kind="ReminderUI", cmd="", text="マップを開く", src="u")]
    bref = GA.Ref(doc={}, script=bscript, windows=[], role={}, variant_of={})
    rec = lambda k: {"how": "exact", "conf": "high", "extra": [], "ref": {"key": k, "score": 1.0}}   # noqa: E731
    from flowocr.analyze import scriptmatch as SM
    bst = SM.match_stats(bref, [rec("t"), rec("t"), rec("u"), rec("u"), rec("u")])
    vscript = bscript + [SA.Entry(key="tv", kind="Talk", cmd="", text="台詞だ", src="u", status="Variant")]
    vref = GA.Ref(doc={}, script=vscript, windows=[], role={}, variant_of={"tv": "t"})
    vst = SM.match_stats(vref, [rec("tv"), rec("tv"), rec("u")])
    vst2 = SM.match_stats(vref, [rec("t"), rec("tv")])
    check("重复认领按代表行数（owner 2026-10-06）：变体说法被重复认领算带内；同一句的两种说法各认领一次也算多出一次",
          (vst["extra"], vst["extra_band"], vst2["extra"], vst2["extra_band"]) == (1, 1, 1, 1),
          (vst["extra"], vst["extra_band"], vst2["extra"], vst2["extra_band"]))
    check("重复认领按带内 / 带外拆：带内多出的单独记（报警看它），两栏加起来等于总数",
          (bst["groups"], bst["extra"], bst["groups_band"], bst["extra_band"]) == (2, 3, 1, 1),
          (bst["groups"], bst["extra"], bst["groups_band"], bst["extra_band"]))
    # 第二遍：一条 cue 装着两行剧本（选项按钮 + 正文）时两行都算认领；
    # 而库里近似重复的另一行（只上屏了一种说法）不许白送——它和主认领占的是同一段字
    texts2 = ["また後で来るね", "リン、時間があるなら、一緒にテレビでも見ないか？",
              "リン、時間があるなら、一緒にテレビを見ないかい？", "画面に出ていない別の台詞です"]
    script2 = [SA.Entry(key=str(i), kind="Talk", cmd="", text=t, src="u") for i, t in enumerate(texts2)]
    wins2 = [[[0.0, 100.0]]] * 4
    st2 = GA.run(script2, wins2, [(10.0, "また後で来るね\nアキラ\nリン、時間があるなら、\n一緒にテレビでも見ないか？", "x")], 0.75)
    check("第二遍『整行落在 cue 里』：选项 + 正文两行都认领；近似重复的另一行占同一段字，不认",
          [e.matched for e in script2] == [0, 0, -1, -1] and st2["sub_extra"] == [[0]]
          and st2["contain"] == 0 and GA.claims(st2) == [1, 0],
          ([e.matched for e in script2], st2["sub_extra"]))
    script3 = [SA.Entry(key="a", kind="Talk", cmd="", text="キュン——", src="u")]
    st3 = GA.run(script3, [[[0.0, 100.0]]], [(10.0, "ユーちゃん\nキュン\n1", "x")], 0.75)
    check("第二遍也接住『cue 混进垃圾行、几种读法都够不着模糊阈值』：整行在里面就算认领，cue 不再算幽灵",
          script3[0].matched == 0 and st3["contain"] == 1 and st3["unmatched_subs"] == 0,
          (script3[0].matched, st3["contain"], st3["unmatched_subs"]))
    scr_band = [SA.Entry(key="c", kind="Choice", cmd="", text="「未着」", src="u"),
                SA.Entry(key="t", kind="Talk", cmd="", text="「未着」……", src="u")]
    st_band = GA.run(scr_band, [[[0.0, 100.0]]] * 2, [(10.0, "火花\n「未着」", "x")], 0.75)
    check("逐字相等打平时先认该在字幕带的行：OCR 漏了省略号，台词 `「未着」……` 不许让给选项按钮 `「未着」`",
          st_band["sub_hit"] == [1], st_band["sub_hit"])
    check("挖掉主认领对上的字：剩下的是 cue 里另一段",
          GA.rest_of("またあとでくるねリンじかんがあるなら", "リンじかんがあるなら") == "またあとでくるね")
    # 开头行（名牌）：非末行、配过 ≥3 种正文。去掉末行之后只剩名牌的读法不许去认领喊名字的台词；
    # 名牌也不许被第二遍整行认领（2026-09-12，matcher 计划 §6 那 12 条逐条看出来的）
    # 正文要**真的不同**：`台詞その0です` / `台詞その1です` 这种只差一个字的会被 `distinct_bodies` 算成同一种
    bodies3 = ["今日はいい天気だね", "早く行こうよ", "それは本当なの？"]
    heads_subs = [(40.0 * k, f"カチーナ\n{b}", "x") for k, b in enumerate(bodies3)] + [(9.0, "パイモン\nカチーナ！", "x")]
    check("开头行：配过 ≥3 种正文但**都挤在几秒里**的不算（正文的第一行就是这个形状）",
          GA.head_lines([(float(k), f"カチーナ\n{b}", "x") for k, b in enumerate(bodies3)]) == frozenset())
    heads = GA.head_lines(heads_subs)
    check("开头行：配过 ≥3 种正文的非末行才算；只出现一次的不算",
          heads == frozenset({SA.norm("カチーナ")}), heads)
    tw = [(float(k), f"リン、時間があるなら\n{s}", "x")
          for k, s in enumerate(["一緒に", "一緒にテレ", "一緒にテレビでも見ないか？", "一緒にテレビでも見ないか?"])]
    check("**两行正文的第一行不是开头行**：第二行被打字机打成几种半截 / OCR 抖几个字，算同一种正文",
          GA.head_lines(tw) == frozenset(), GA.head_lines(tw))
    # 全量喂入时一句台词和别的区域的字同刻并进 cue、落在非末行上（hsr「あなたが彼を食い止めたのですね。」，2026-10-05）
    line = "あなたが彼を食い止めたのですね。"
    echo = [(40.0 * k, f"{line}\n{b}", "x") for k, b in enumerate(bodies3)]
    band_scr = [SA.Entry(key="t", kind="Talk", cmd="", text=line, src="u"),
                SA.Entry(key="c", kind="Choice", cmd="", text="カチーナ", src="u")]
    # 逐字相等的短行只对上 cue 的一部分读法，包含它的更长台词对整条 cue 更像（zzz 0:18:20「首席技術者？」，2026-10-05）
    sup = [SA.Entry(key="a", kind="Talk", cmd="", text="首席技術者？", src="u"),
           SA.Entry(key="b", kind="Talk", cmd="", text="首席技術者？\\Nってことは、なんかを研究してる人なの？", src="u")]
    st_sup = GA.run(sup, [[[0.0, 100.0]]] * 2, [(10.0, "首席技術者？\nつてことは、なんかを研究してる人なの？", "x")], 0.75)
    one = [SA.Entry(key="a", kind="Talk", cmd="", text="ありがとう", src="u"),
           SA.Entry(key="b", kind="Talk", cmd="", text="ありがとう！", src="u")]
    st_one = GA.run(one, [[[0.0, 100.0]]] * 2, [(10.0, "ありがとう", "x")], 0.75)
    check("逐字相等的短行只对上一部分读法、以它开头的更长台词对整条 cue ≥0.9 时认长的；单行 cue 照旧认逐字相等的",
          st_sup["sub_hit"] == [1] and st_sup["superset"] == 1 and st_one["sub_hit"] == [0],
          (st_sup["sub_hit"], st_one["sub_hit"]))
    # 长台词只是在中间包含短行、不以它开头：不改认（gi1 主轨口径上的巧合包含，owner 10-06 收紧）
    mid = [SA.Entry(key="a", kind="Talk", cmd="", text="研究してる人なの？", src="u"),
           SA.Entry(key="b", kind="Talk", cmd="", text="首席技術者？研究してる人なの？ね", src="u")]
    st_mid = GA.run(mid, [[[0.0, 100.0]]] * 2, [(10.0, "首席技術者？\n研究してる人なの？\nOK", "x")], 0.75)
    check("长台词只在中间包含逐字相等的短行（不以它开头）：不改认",
          st_mid["sub_hit"] == [0] and st_mid["superset"] == 0, st_mid["sub_hit"])
    # 长的那行已被前一条 cue 认领（同一屏两行台词被切成两条 cue）：照样改认长的，短的那行不因此被认领——
    # 屏上没有单独出现过短的那一句，认它就是虚的（zzz `_007`，2026-10-06 验收 peer）
    twice = [(10.0, "首席技術者？\nつてことは、なんかを研究してる人なの？", "x"), (12.0, "首席技術者？\nつてことは、なんかを研究してる人なの？", "x")]
    st_tw = GA.run(sup, [[[0.0, 100.0]]] * 2, twice, 0.75)
    check("改认长台词：长的已被认领时照样改认（重复认领记在长的上），短的那行仍没人认",
          st_tw["sub_hit"] == [1, 1] and sup[0].matched == -1 and sup[1].matched == 0,
          (st_tw["sub_hit"], sup[0].matched, sup[1].matched))
    # 打字机半截（gi2 2:15 连点跳过，2 fps 只采到打字中途，2026-10-05）
    long = "もし仕事の日の天気が毎日こんなにキュートだったら、最高なのに。"
    pre = [SA.Entry(key="p", kind="Talk", cmd="", text=long, src="u"),
           SA.Entry(key="q", kind="Talk", cmd="", text="まったく別のせりふです", src="u")]
    st_pre = GA.run(pre, [[[0.0, 100.0]]] * 2, [(10.0, "編集長\nもし仕事の日の天気が毎日こんなに", "x")], 0.75)
    check("打字机半截：读到的是窗里恰好一条没被认领的台词的开头，就认它（记模糊）",
          st_pre["sub_hit"] == [0] and st_pre["prefix"] == 1, (st_pre["sub_hit"], st_pre["prefix"]))
    hud = [(t, "もし仕事の日の天気が毎日こんなに", "x") for t in (10.0, 50.0)]
    st_hud = GA.run([SA.Entry(key="p", kind="Talk", cmd="", text=long, src="u")], [[[0.0, 100.0]]], hud, 0.75)
    already = [(10.0, "もし仕事の日の天気が毎日こんなに", "x"), (11.0, long, "x")]
    st_al = GA.run([SA.Entry(key="p", kind="Talk", cmd="", text=long, src="u")], [[[0.0, 100.0]]], already, 0.75)
    check("打字机半截不认：同样的字挂了几十秒（任务名 / 地名那种常驻字）；那行已经被整句认领（只补漏）",
          st_hud["sub_hit"] == [-1, -1] and st_al["sub_hit"] == [-1, 0], (st_hud["sub_hit"], st_al["sub_hit"]))
    # 一条 cue 自己就挂了 20 s（菜单里静止的列表项）：起点只有一个，按在屏的首末量才挡得住（gi-s1 531 s，2026-10-06 验收 peer）
    one = [(10.0, "もし仕事の日の天気が毎日こんなに", "x")]
    st_long = GA.run([SA.Entry(key="p", kind="Talk", cmd="", text=long, src="u")], [[[0.0, 100.0]]], one, 0.75, ends=[30.0])
    st_short = GA.run([SA.Entry(key="p", kind="Talk", cmd="", text=long, src="u")], [[[0.0, 100.0]]], one, 0.75, ends=[10.5])
    check("打字机半截不认：一条 cue 自己在屏超过 PREFIX_MAX_SPAN（按起点到终点量）；一闪就变的照认",
          st_long["sub_hit"] == [-1] and st_short["sub_hit"] == [0], (st_long["sub_hit"], st_short["sub_hit"]))
    # 读到一大段（检测器三场修复计划 U5）：游戏版本和文本包不同，屏上是剧本行的后半（gi1 0:52:27）
    full = "ううん、この子たちは「クビナガライノ」っていうの。何頭かいる中でも、トトは一番親切で優しい子なんだよ。"
    shown = "カチーナ\nうん、何頭かいる中でも、トトは一番親切で優しい子なんだよ。"
    inf = lambda: [SA.Entry(key="f", kind="Talk", cmd="", text=full, src="u"),            # noqa: E731
                   SA.Entry(key="g", kind="Talk", cmd="", text="まったく別のせりふです", src="u")]
    st_inf = GA.run(inf(), [[[0.0, 100.0]]] * 2, [(10.0, shown, "x")], 0.75, ends=[16.0])
    check("读到一大段：读法是窗里恰好一条没被认领的台词的后半一大段，就认它（记模糊）；整行相似够不着门",
          st_inf["sub_hit"] == [0] and st_inf["infix"] == 1, (st_inf["sub_hit"], st_inf.get("infix")))
    other = "ねえねえ、この子たちは「クビナガライノ」なんだって。何頭かいる中でも、トトは一番親切で優しい子なんだよ。"
    two = inf() + [SA.Entry(key="h", kind="Talk", cmd="", text=other, src="u")]
    st_two = GA.run(two, [[[0.0, 100.0]]] * 3, [(10.0, shown, "x")], 0.75, ends=[16.0])
    taken = [(5.0, other, "x"), (10.0, shown, "x")]
    st_tk = GA.run(two, [[[0.0, 100.0]]] * 3, taken, 0.75, ends=[8.0, 16.0])
    check("读到一大段不认：窗里两条都包得下（两位主角各一版）；另一条已被认领的也算候选（同一句重显不去认没人认的那条）",
          st_two["sub_hit"] == [-1] and st_tk["sub_hit"] == [2, -1], (st_two["sub_hit"], st_tk["sub_hit"]))
    st_off = GA.run(inf(), [[[0.0, 100.0]]] * 2, [(10.0, shown, "x")], 0.75, ends=[16.0], main=[False])
    st_hang = GA.run(inf(), [[[0.0, 100.0]]] * 2, [(10.0, shown, "x")], 0.75, ends=[45.0])
    st_short2 = GA.run(inf(), [[[0.0, 100.0]]] * 2, [(10.0, "カチーナ\nトトは一番親切で", "x")], 0.75, ends=[16.0])
    amb = [SA.Entry(key="f", kind="Talk", cmd="", text=full, src="u"),
           SA.Entry(key="p", kind="Talk", cmd="", text="何頭かいる中でも、トトは一番親切で優しい子なんだよ。それでね、もう一つあるの。ほかの子も紹介するね", src="u")]
    st_amb = GA.run(amb, [[[0.0, 100.0]]] * 2, [(10.0, "カチーナ\n何頭かいる中でも、トトは一番親切で優しい子なんだよ。", "x")], 0.75, ends=[16.0])
    check("读到一大段不认：一个读法既是 A 的开头（归打字机半截）又落在 B 里，A 照样算候选、两条就不认",
          st_amb["sub_hit"] == [-1] and st_amb["infix"] == 0, (st_amb["sub_hit"], st_amb.get("infix")))
    check("读到一大段不认：cue 不在主轨上；同样的字在屏超过 INFIX_MAX_SPAN（30 s）；读法不到 INFIX_MIN 个内容字或占不到一半",
          st_off["sub_hit"] == [-1] and st_hang["sub_hit"] == [-1] and st_short2["sub_hit"] == [-1],
          (st_off["sub_hit"], st_hang["sub_hit"], st_short2["sub_hit"]))
    # 夹缝认领（shortline-context 计划，owner 2026-10-24）：短句夹在同一单元相邻两行的认领之间，读法是它的一截就认（gi2 `ナヴィア / でも`）
    from flowocr.artifacts import srtio
    UP, DOWN = "旅人、こっちだよ。早く来て！", "そろそろ出発しようか、みんな待ってる。"

    def flank(texts, subs, status=None, wins=None, rows=None, variant_of=None, step=0.0):
        scr = [SA.Entry(key=f"k{i}", kind="Talk", cmd="", text=t, src="u", status=(status or {}).get(i))
               for i, t in enumerate(texts)]
        st = GA.run(scr, wins or [[[0.0, 100.0]]] * len(texts), subs, 0.75,
                    rows=rows or {f"k{i}": ("u", i) for i in range(len(texts))}, variant_of=variant_of or {}, step=step)
        return st, scr
    fl_a, fl_b = (10.0, f"ナヴィア\n{UP}", "x"), (14.0, f"ナヴィア\n{DOWN}", "x")
    fl_x = (12.0, "ナヴィア\nでも", "x")
    st_fl, scr_fl = flank([UP, "でも…", DOWN], [fl_a, fl_x, fl_b])
    cref = GA.Ref(doc={"units": [{"uid": "u", "lines": [{"key": e.key, "cn": e.text} for e in scr_fl]}]}, script=scr_fl,
                  windows=[[[0.0, 100.0]]] * 3, role={}, variant_of={}, rows={f"k{i}": ("u", i) for i in range(3)})
    crecs, _ = SM.match(cref, [srtio.Cue(t, t + 1.5, tuple(raw.split("\n"))) for t, raw, _ in (fl_a, fl_x, fl_b)], 0.75)
    cst = SM.match_stats(cref, crecs)
    check("夹缝认领：上一行 10 s、下一行 14 s 都认了，12 s 的 `ナヴィア / でも` 认中间那行 `でも…`（相似度够不着 --fuzzy），记 context、不进可疑改写",
          st_fl["sub_hit"] == [0, 1, 2] and st_fl["context"] == 1 and st_fl["context_subs"] == [1]
          and crecs[1]["how"] == "context" and crecs[1]["ref"]["score"] < SM.SUSPECT and len(cst["scores"]) == 2,
          (st_fl["sub_hit"], st_fl.get("context_subs"), [r["how"] for r in crecs], cst["scores"]))
    st_btn, _ = flank(["上の行のせりふです、よろしくね", "そう…", "下の選択肢を選んでください"],
                      [(10.0, "上の行のせりふです、よろしくね", "x"), (20.0, "下の選択肢を選んでください", "x"), (20.0, "そう", "x")])
    check("夹缝认领：同刻算两边（同屏的选项按钮和下一行一起上屏，zzz `そう…`）",
          st_btn["sub_hit"] == [0, 2, 1] and st_btn["context"] == 1, st_btn["sub_hit"])
    btn = ["上の行のせりふです、よろしくね", "そう…", "下の選択肢を選んでください"]

    def btn_at(t_next, step):
        return flank(btn, [(10.0, btn[0], "x"), (t_next, btn[2], "x"), (20.0, "そう", "x")], step=step)[0]["sub_hit"]
    early, far, nostep = btn_at(20.0 - 1 / 60, 0.5), btn_at(19.0, 0.5), btn_at(20.0 - 1 / 60, 0.0)
    check("夹缝认领：同刻容不到一个采样间隔——回抠后下一行的按钮比 X 早 1 帧也算夹住；差两个采样间隔不算；不给采样间隔时照原来的比法（早 1 帧不算）",
          early == [0, 2, 1] and far == [0, 2, -1] and nostep == [0, 2, -1], (early, far, nostep))
    st_rev, _ = flank(btn + ["まったく別のせりふです"],
                      [(19.8, btn[2], "x"), (20.0, "そう", "x"), (20.1, "まったく別のせりふです", "x"), (20.2, btn[0], "x")], step=0.5)
    check("夹缝认领：容差下两端倒序（下一行 19.8 s、上一行 20.2 s 都在 20.0 s 的一个间隔内）时，两端之间单元里认了别的行（20.1 s）照样不认",
          st_rev["sub_hit"][1] == -1, st_rev["sub_hit"])
    import claim_audit as CA
    fl_audit = CA.audit(cref, [fl_a, fl_x, fl_b], [11.5, 13.5, 15.5], None, 0.5)[3]
    check("`claim_audit.audit` 跟得上 `claim_flanked` 的参数（剧本有行序时不报错），列出夹缝认领",
          [(si, e.key) for si, e in fl_audit] == [(1, "k1")], fl_audit)
    lc = GS.cnorm("ロマンチックだね…")
    check("夹缝认领·文字票：≥ 3 字的行读错一个字形、丢一个字、多一个杂字、只读到前一半多一点都认",
          all(GA.flank_fits(GS.cnorm(r), lc) for r in ("口マンチックだね", "ロマンチクだね", "ロマンチIックだね", "ロマンチッ")),
          [GA.flank_fits(GS.cnorm(r), lc) for r in ("口マンチックだね", "ロマンチクだね", "ロマンチIックだね", "ロマンチッ")])
    st_gl, _ = flank([UP, "ロマンだね…", DOWN], [fl_a, (12.0, "ナヴィア\n口マンだね", "x"), fl_b])
    check("夹缝认领：读错一个字形的 3–5 字的行（相似度、整行包含都够不着门），夹住了就认",
          st_gl["sub_hit"] == [0, 1, 2] and st_gl["contain"] == 0 and st_gl["context"] == 1, st_gl["sub_hit"])
    check("夹缝认领·文字票不认：2 字的行读错一个字（`でｔ`）；3 字读法配 2 字的行（`探そう` 对 `そう…`）；"
          "2 字读法错一处配 3 字的行（`イパ` 对 `キュイ——`）；读法不到这一行一半",
          not any(GA.flank_fits(GS.cnorm(r), GS.cnorm(c)) for r, c in
                  (("でｔ", "でも…"), ("探そう", "そう…"), ("イパ", "キュイ——"), ("ナヴィアのや", "ナヴィアのやつは遺跡で足を踏み外して"))))
    fl_cases = {
        "中间那行已被认": flank([UP, "でも…", DOWN], [fl_a, (11.0, "でも…", "x"), fl_x, fl_b]),
        "只一边认了": flank([UP, "でも…", DOWN], [fl_a, fl_x]),
        "上一行的认领在 X 之后": flank([UP, "でも…", DOWN], [fl_x, (13.0, UP, "x"), fl_b]),
        "两次认领之间单元里认了别的行": flank([UP, "でも…", DOWN, "まったく別のせりふです"],
                                     [fl_a, (11.0, "まったく別のせりふです", "x"), fl_x, fl_b]),
        "2 字的行读错一个字": flank([UP, "でも…", DOWN], [fl_a, (12.0, "ナヴィア\nでｔ", "x"), fl_b]),
        "中间那行不在带内": flank([UP, "でも…", DOWN], [fl_a, fl_x, fl_b], status={1: "OutOfSpan"}),
        "X 不在那行的可认领时段里": flank([UP, "でも…", DOWN], [fl_a, fl_x, fl_b],
                                    wins=[[[0.0, 100.0]], [[50.0, 100.0]], [[0.0, 100.0]]]),
        "X 同时对上两个单元的夹缝": flank([UP, "でも…", DOWN, UP + "ね", "でも…", DOWN + "ね"],
                                    [fl_a, (10.0, UP + "ね", "x"), fl_x, fl_b, (14.0, DOWN + "ね", "x")],
                                    rows={"k0": ("u", 0), "k1": ("u", 1), "k2": ("u", 2), "k3": ("w", 0), "k4": ("w", 1), "k5": ("w", 2)}),
        "变体组的另一种说法已被认": flank([UP, "でも…", DOWN, "でもね…"], [(11.0, "でもね…", "x"), fl_a, fl_x, fl_b],
                                    status={3: "Variant"}, variant_of={"k3": "k1"},
                                    rows={"k0": ("u", 0), "k1": ("u", 1), "k2": ("u", 2), "k3": ("v", 0)}),
        # 10-24 的两个反例：zzz 地图标记 `ラミル`（`ラミル…？` 已被真按钮那条 cue 认上，上一行全场没人认）、
        # hsr 记录面板说话人栏 `姫子`（`姫子……？` 上下两行都没人认，单元里被认的是隔着几行的别的行）
        "地图标记 ラミル": flank(["ここから先は危ないぞ", "ラミル…？", "行ってみようか、気をつけて"],
                             [(11297.0, "ラミル", "x"), (11311.5, "ラミル…？", "x"), (11313.0, "行ってみようか、気をつけて", "x")],
                             wins=[[[11000.0, 11400.0]]] * 3),
        "记录面板 姫子": flank(["列車に戻りましょう、皆さん", "準備はできてるわ", "それでいいのかしら", "姫子……？", "ええ、行くわよ"],
                           [(1380.0, "列車に戻りましょう、皆さん", "x"), (1388.0, "姫子", "x"), (1390.0, "準備はできてるわ", "x")],
                           wins=[[[1300.0, 1500.0]]] * 5),
    }
    # 每个反例里 X 都没被前四遍认走、上下行至少一边认上了，"不认"才是夹缝这一遍挡的（ラミル 那条 X 是 11297 s 那条）
    check("夹缝认领不认：" + "；".join(fl_cases),
          all(st["context"] == 0 and -1 in st["sub_hit"] and any(h >= 0 for h in st["sub_hit"]) for st, _ in fl_cases.values())
          and fl_cases["地图标记 ラミル"][0]["sub_hit"] == [-1, 1, 2] and fl_cases["记录面板 姫子"][0]["sub_hit"] == [0, -1, 1],
          {k: (st["sub_hit"], st["sub_extra"]) for k, (st, _) in fl_cases.items()})
    np_subs = [(40.0 * k, f"ナヴィア\n{b}", "x") for k, b in enumerate(["今日はいい天気だね", "早く行こうよ", "それは本当なの？"], 1)]
    st_np, _ = flank([UP, "ナヴィア…？", DOWN], [fl_a, (12.0, "ナヴィア", "x"), fl_b] + np_subs)
    check("夹缝认领不认：只剩名牌的 cue（开头行先剥掉，没有读法）——名牌 `ナヴィア` 不认喊名字的台词 `ナヴィア…？`",
          st_np["context"] == 0 and st_np["sub_hit"][1] == -1, st_np["sub_hit"])
    st_twice, _ = flank([UP, "でも…", DOWN], [fl_a, fl_x, (12.5, "ナヴィア\nでも", "x"), fl_b])
    check("夹缝认领一行只认一次：两条 X 都满足时认最早那条，重复认领不涨",
          st_twice["sub_hit"] == [0, 1, -1, 2] and GA.repeat_claims(st_twice["sub_hit"]) == (0, 0), st_twice["sub_hit"])
    st_norows = GA.run([SA.Entry(key=f"k{i}", kind="Talk", cmd="", text=t, src="u") for i, t in enumerate([UP, "でも…", DOWN])],
                       [[[0.0, 100.0]]] * 3, [fl_a, fl_x, fl_b], 0.75)
    check("夹缝认领：不给 rows（老调用）时这一遍不跑", st_norows["sub_hit"] == [0, -1, 2] and st_norows["context"] == 0,
          st_norows["sub_hit"])
    check("开头行：本身逐字是一整条台词的不算（带外的剧本行不挡：名牌 / UI 标签照旧是开头行）",
          GA.head_lines(echo) == frozenset({SA.norm(line)})
          and GA.head_lines(echo, GA.band_lines(band_scr)) == frozenset()
          and GA.head_lines(heads_subs, GA.band_lines(band_scr)) == frozenset({SA.norm("カチーナ")}),
          (GA.head_lines(echo, GA.band_lines(band_scr)), GA.head_lines(heads_subs, GA.band_lines(band_scr))))
    check("读法：去掉末行后只剩名牌的不要；带着末行的照留（末行碰巧和名牌同字也照留）",
          SA.norm("カチーナ") not in GA.variants("カチーナ\nはあ、はあ", heads)
          and SA.norm("はあ、はあ") in GA.variants("カチーナ\nはあ、はあ", heads)
          and SA.norm("カチーナ！") in GA.variants("パイモン\nカチーナ！", heads))
    check("读法：名牌和正文之间夹了 1–2 字的 OCR 碎片，去掉正文后照样不算一句；末行是键位提示时照常去掉",
          not any(SA.norm("カチーナ") in v for v in GA.variants("カチーナ\nL\nはあ、はあ", heads)
                  if SA.norm("はあ、はあ") not in v)
          and SA.norm("うん") in GA.variants("パイモン\nうん\nD", heads))
    check("读法：名牌不和打字机打出的半截拼成一种读法；只有名牌一行的 cue 没有读法",
          all(SA.norm("カチーナ") not in v for v in GA.variants("カチーナ\n(…すご", heads))
          and GA.variants("カチーナ", heads) == [])
    check("读法：『名牌: 台词』同一行格式剥掉名字前缀；不是已知名牌的冒号不动",
          GA.variants("カチーナ: ハッ!", heads) == [SA.norm("ハッ!")]
          and SA.norm("時刻: 12時") in GA.variants("時刻: 12時", heads))
    scr4 = [SA.Entry(key="n", kind="Talk", cmd="", text="カチーナ！", src="u"),
            SA.Entry(key="b", kind="Talk", cmd="", text="はあ、はあ…", src="u")]
    subs4 = heads_subs[:3] + [(20.0, "カチーナ\nはあ、はあ", "x")]
    st4 = GA.run(scr4, [[[0.0, 100.0]]] * 2, subs4, 0.75)
    check("**短正文的 cue 不被名牌抢主认领**：认的是正文那行，喊名字那行不认",
          st4["sub_hit"][3] == 1 and scr4[0].matched == -1, (st4["sub_hit"], [e.matched for e in scr4]))
    scr5 = [SA.Entry(key="s", kind="Talk", cmd="", text="シーシィア…？", src="u"),
            SA.Entry(key="x", kind="Talk", cmd="", text="あーしは雑草だから…", src="u")]
    subs5 = [(40.0 * k, f"シーシイア\n{b}", "x") for k, b in enumerate(bodies3)] + [(20.0, "シーシイア\nあーしは雑草だから…", "x")]
    st5 = GA.run(scr5, [[[0.0, 100.0]]] * 2, subs5, 0.75)
    check("**名牌不被第二遍整行认领**", st5["sub_extra"][3] == [] and scr5[0].matched == -1, st5["sub_extra"])
    scr6 = [SA.Entry(key=k, kind="Talk", cmd="", text="いやああぁぁ————！", src="u") for k in ("p", "q")]
    st6 = GA.run(scr6, [[[0.0, 100.0]]] * 2, [(10.0, "US\nいやああああ", "x")], 0.75)
    check("**第二遍认了一行就挖掉它的字**：同文本不同编号的两行不被同一段字各认一次",
          len(GA.claims(st6)) == 1, (st6["sub_hit"], st6["sub_extra"]))

    # ---- 检索层（audit-6 §3）：分母是这一层定的，守卫原来唯独没盖它
    import io
    import json
    import os
    from contextlib import redirect_stdout
    with tempfile.TemporaryDirectory() as d:
        doc = synthetic_gamescript(Path(d))
    got = {u["uid"]: {ln["key"]: ln for ln in u["lines"]} for u in doc["units"]}
    check("检索层：只圈 obs 读到过 ≥2 行的单元；一行没读到的、只撞常用句的不圈",
          sorted(got) == ["A", "C"], sorted(got))
    A, Cu = got.get("A", {}), got.get("C", {})
    sts = {k: ln["status"] for k, ln in A.items()}
    check("检索层：兄弟选项的回复读到了而它没有 -> Unwalked；最后一个锚点之后 -> OutOfSpan；类型照旧是 Talk",
          sts == {"a0": None, "as": "OffBand", "a1": None, "a2": "Unwalked", "ac": None, "a3": None, "a4": "OutOfSpan"}
          and {ln["kind"] for ln in A.values()} == {"Talk"}, (sts, {k: ln["kind"] for k, ln in A.items()}))
    check("短行（不到 --qmin 字）在圈中单元的时段内按逐字相等补锚点，于是位置判据也管得上它",
          doc["stats"]["short_anchors"] == 1 and A.get("as", {}).get("anchor", {}).get("in_band") is False,
          (doc["stats"]["short_anchors"], A.get("as", {}).get("anchor")))
    check("锚点记 unique：只被常用句（撞了 >3 个单元的查询）锚住的行 unique=False（audit-6 §1.4）",
          bool(A.get("ac", {}).get("anchor")) and A["ac"]["anchor"]["unique"] is False
          and A["a0"]["anchor"]["unique"] is True)
    check("变体组：obs 那一截同时包含于两种说法、两行都被锚住，也只留一行进分母（audit-6 §1.3）",
          (Cu.get("c0", {}).get("status"), Cu.get("c1", {}).get("status"), Cu.get("c1", {}).get("variant_of"))
          == (None, "Variant", "c0"), {k: ln["status"] for k, ln in Cu.items()})
    check("字幕带：锚点框的众数窗口；读到过而框全在带外的记 OffBand，带里的和只撞常用句的（没有位置证据）不动",
          doc["stats"]["band"] is not None and doc["stats"]["band"][0] < .84 < .86 < doc["stats"]["band"][1]
          and Cu.get("c3", {}).get("status") == "OffBand" and Cu.get("c2", {}).get("status") is None
          and A.get("ac", {}).get("anchor", {}).get("in_band") is None,
          (doc["stats"]["band"], {k: ln["status"] for k, ln in Cu.items()}))
    check(f"字幕带：锚住的行不到 {GS.BAND_MIN_LINES} 条不估；估的时候少数几行带外的拉不动窗口",
          GS.band_of([[.85]] * (GS.BAND_MIN_LINES - 1)) is None
          and GS.band_of([[.85], [.86, .2], [.84], [.85], [.4], [.1]])[0] < .84)
    grp = [{"key": "r", "kind": "Talk", "status": None, "anchor": {"in_band": None}},
           {"key": "v", "kind": "Talk", "status": "Variant", "variant_of": "r", "anchor": {"in_band": False}},
           {"key": "s", "kind": "Talk", "status": None, "anchor": {"in_band": None}},
           {"key": "w", "kind": "Talk", "status": "Variant", "variant_of": "s", "anchor": {"in_band": True}},
           {"key": "x", "kind": "Choice", "status": None, "anchor": {"in_band": False}}]
    n = GS.mark_offband([{"lines": grp}])
    check("OffBand 看整个变体组：代表行没有位置证据时用另一种说法的；已经排出分母的不重判",
          n == 1 and [g["status"] for g in grp] == ["OffBand", "Variant", None, "Variant", None],
          [g["status"] for g in grp])
    check("绝区零字幕带散条目：CN 按基底成组、G/B 两份同序号是变体；TL 去序号成组；名牌 / 歌词 / 别的 head 不进",
          [GS.band_loose_slot(h, i) for h, i in (("CN", "C280_EP020_010"), ("CN", "C280_EP020G_040"),
                                                  ("CN", "C280_EP020B_040"), ("TL", "TL_C32_Youkai_010"),
                                                  ("TL", "TL_X_Name_01"), ("CN", "C270_EP050_060_Lyric"),
                                                  ("Comic", "Comic_abc_1"))]
          == [("C280_EP020", 10, ""), ("C280_EP020", 40, "G"), ("C280_EP020", 40, "B"),
              ("TL_C32_Youkai", 10, ""), None, None, None])
    reps = [{"key": "u", "anchor": None}, {"key": "n", "anchor": {"unique": False}},
            {"key": "k", "anchor": {"unique": True}}]
    GS.fold_variants([reps])
    check("变体组的代表行：唯一查询锚住的 > 被锚住的 > 组里第一个",
          [r.get("variant_of") for r in reps] == ["k", "k", None], reps)

    # 按游戏的特化（product-goals 第 14 条）：表在内置匹配器 gametext 里，build_tracks --matcher 取
    from flowocr.analyze.matchers import gametext as GT
    check("按游戏的补丁按标签前缀认游戏：星铁三段都开时间门，原神 / 绝区零不开，没库的不开",
          [GT.cluster_patch({"tag": t}) for t in ("hsr", "hsr-s1", "hsr-s2", "gi1", "gi-s1", "zzz", "wuwa-s1")]
          == [{"region_time_gate": 2}] * 3 + [{}, {}, {}, {}],
          [GT.cluster_patch({"tag": t}) for t in ("hsr", "hsr-s1", "gi1", "zzz", "wuwa-s1")])
    check("补丁表里的 key 都是认得的游戏（打错名字会静默失效）",
          set(GT.PATCHES) <= set(GS.GAMES), sorted(set(GT.PATCHES) - set(GS.GAMES)))

    # 匹配器（产品侧）：相邻同一条剧本行并成一条、匹配失败原样留 OCR
    from unittest.mock import patch

    from flowocr.analyze import scriptmatch as SM
    from flowocr.output import layout as LY
    from flowocr.output import script as OV
    from flowocr.typeset import core as TS

    def rec(t0, t1, key, ocr="x"):
        return {"start": t0, "end": t1, "ocr": ocr, "how": "fuzzy", "conf": "high",
                "ref": None if key is None else {"key": key, "jp": key, "cn": None, "score": 0.9},
                "extra": [], "cues": [t0]}
    got = SM.merge_runs([rec(0, 5, "a"), rec(5, 9, "a"), rec(60, 65, "a"),   # 隔一分钟 = 真实重播
                         rec(65, 70, "b"), rec(70, 75, None), rec(75, 80, None)], SM.MERGE_GAP)
    check("匹配器：相邻且同一条剧本行的 cue 并成一条；隔太久的重播不并；没匹配上的不并",
          [(r["start"], r["end"], (r["ref"] or {}).get("key"), r["n_cues"]) for r in got]
          == [(0, 9, "a", 2), (60, 65, "a", 1), (65, 70, "b", 1), (70, 75, None, 1), (75, 80, None, 1)],
          [(r["start"], r["end"], (r["ref"] or {}).get("key"), r["n_cues"]) for r in got])

    def rec_c(t0, t1, key, cue):
        return dict(rec(t0, t1, key), cues=[cue])
    # 真记录面板（检测器三场修复计划 U3）：和别的 cue 不并，"最近一条"分开记
    seq = [rec_c(0, 5, "a", 0), rec_c(5, 6, "a", 1), rec_c(6.5, 9, "a", 2)]
    shape = lambda rs: [(r["start"], r["end"], r["cues"]) for r in rs]   # noqa: E731
    split = SM.merge_runs([dict(r) for r in seq], SM.MERGE_GAP, ["A"] * 3, [False, True, False])
    check("匹配器：真记录面板不并进台词框的记录；隔着记录面板的前后两条也不并（按来源记最近一条，记录面板也算）",
          shape(split) == [(0, 5, [0]), (5, 6, [1]), (6.5, 9, [2])], shape(split))
    check("匹配器：不给真记录面板（--feed main、naninovel）时照旧全并",
          shape(SM.merge_runs([dict(r) for r in seq], SM.MERGE_GAP, ["A"] * 3)) == [(0, 9, [0, 1, 2])])
    alt = [rec_c(0, 2, "a", 0), rec_c(2, 4, "a", 1), rec_c(4, 6, "a", 2)]
    got_alt = SM.merge_runs([dict(r) for r in alt], SM.MERGE_GAP, ["A"] * 3, [True, False, True])
    check("匹配器：面板 / 台词框 / 面板交错：各自成条", shape(got_alt) == [(0, 2, [0]), (2, 4, [1]), (4, 6, [2])], shape(got_alt))
    # 变体组：量尺认哪一种都算命中，产品必须输出**屏幕上那一种**
    vs = [SA.Entry(key="r", kind="Talk", cmd="", text="私が…みんなのプロキシだよ！", src="u"),
          SA.Entry(key="v", kind="Variant", cmd="", text="僕が…みんなのプロキシだ！", src="u")]
    vref = GA.Ref(doc={}, script=vs, windows=[], role={}, variant_of={"v": "r"})
    g = SM.variant_index(vref, vs)
    check("匹配器：变体组里挑和 cue 最像的那一种输出（量尺那边一组只算一条）",
          g == {"r": [0, 1], "v": [0, 1]}
          and SM.pick_variant(g, vs, 0, "アキラ\n僕が…みんなのプロキシだ！") == 1
          and SM.pick_variant(g, vs, 1, "リン\n私が…みんなのプロキシだよ！") == 0, g)
    check("匹配器：匹配失败是一等公民——正文原样留 OCR；`\\N` 是条内换行",
          SM.body({"ref": None, "ocr": "読めない\\N文字"}, "cn") == ["読めない", "文字"]
          and SM.body({"ref": {"cn": "上\\N下"}, "ocr": "x"}, "cn") == ["上", "下"]
          and SM.body({"ref": {"cn": None, "jp": "原文"}, "ocr": "OCR"}, "cn") == ["OCR"])
    # 导出契约：SRT 只出主 ref。这条**不是**"应该如此"，是把现状钉住——"覆盖"算 ref ∪ extra，
    # 比 SRT 里真出现的多，两个数都要打（2026-09-12 外部 review 抓出来的）
    check("匹配器：导出的正文只有主 `ref`，第二条剧本行（extra）不进 SRT",
          SM.body({"ref": {"cn": "主行"}, "ocr": "x",
                   "extra": [{"key": "e", "cn": "第二行"}]}, "cn") == ["主行"])
    # 可疑改写的门必须**严格高于**接受判据的门，否则这个数结构上恒为 0（同 unmatched_subs 的形状）
    check(f"匹配器：可疑改写的门 {SM.SUSPECT} 严格高于默认 --fuzzy {SM.CONF_MED}（否则恒为 0，量不了东西）",
          SM.SUSPECT > SM.CONF_MED, (SM.SUSPECT, SM.CONF_MED))
    check("匹配器：并起来的记录带着它由哪几条 cue 并成（叠加 ASS 要回到这些 cue 取框）",
          got[0]["cues"] == [0, 5])

    # ---- 用途 2 叠加 ASS（scriptmatch --ass → 字幕稿 flowocr.output.script → 阶段 4 flowocr.typeset）----
    check("叠加分行（wrap_lines）：照剧本换行切、去空行（末尾多一个 \\N 的是一行）；没有「一行几字」就不折，也不按原文行框数凑行数",
          OV.wrap_lines("上一句\\N下一句") == ["上一句", "下一句"]
          and OV.wrap_lines("一二三四五六") == ["一二三四五六"]
          and OV.wrap_lines("一二\\N三四\\N五六") == ["一二", "三四", "五六"]
          and OV.wrap_lines("一二三\\N") == ["一二三"] and OV.wrap_lines("一二\\N\\N三四") == ["一二", "三四"]
          and OV.wrap_lines("一二三四五六七八九十" * 4) == ["一二三四五六七八九十" * 4])
    check("叠加分行：给了「一行几个全角字」时宽过才折、尽量往后断；照剧本换行之后逐行折；没宽过的不动",
          OV.wrap_lines("一二三四五六七八", 5) == ["一二三四五", "六七八"]
          and OV.wrap_lines("一二三四五\\N六七八九十一二", 5) == ["一二三四五", "六七八九十", "一二"]
          and OV.wrap_lines("一二三四五", 5) == ["一二三四五"] and OV.wrap_lines("ab一二三四", 5) == ["ab一二三四"])
    check("叠加分行：避头（句读挂在上一行末尾）、避尾（左引号挪到下一行）；拉丁文只在空格处断、空格不进行首；没空格的长词不断",
          OV.wrap_lines("一二三四五。六七八九", 5) == ["一二三四五。", "六七八九"]
          and OV.wrap_lines("一二三四「五六七八九", 5)[:2] == ["一二三四", "「五六七八"]
          and OV.wrap_lines("This is a test sentence", 5) == ["This is a", "test", "sentence"]
          and OV.wrap_lines("abcdefghijklmnopqrstuvwxyz", 3) == ["abcdefghijklmnopqrstuvwxyz"])
    rows_p = [[100, 100, 900, 130], [300, 136, 700, 166]]
    pd = 14
    box_p, rows_pl, text_pl = (LY.plate_rects(rows_p, [400, 600], 500, m, pd) for m in ("box", "rows", "text"))
    check("叠加底板：box 一整块；rows 每行 max(原文, 译文) 区间；text 只盖译文；上下两行外扩后的交叠从中间劈开",
          box_p == [[100 - pd, 100 - pd, 900 + pd, 166 + pd]]
          and rows_pl[0][0] == 100 - pd and rows_pl[0][2] == 900 + pd and rows_pl[1][0] == 200 - pd and rows_pl[1][2] == 800 + pd
          and text_pl[0][0] == 300 - pd and text_pl[0][2] == 700 + pd
          and rows_pl[0][3] == rows_pl[1][1] == 133, (box_p, rows_pl, text_pl))
    far = [[100, 100, 300, 130], [100, 300, 900, 330]]
    tall_pl = LY.plate_rects(far, [400, 600], 500, "rows", pd, text_h=60)
    box_tw = LY.plate_rects(far, [1000, 600], 500, "box", pd, text_h=60)
    check("字不出底板：正文比原文框高时底板竖直方向跟着长（以行中心为准）；box 也把译文的左右范围并进来",
          tall_pl[0][1] == 115 - 30 - pd and tall_pl[0][3] == 115 + 30 + pd
          and box_tw[0][0] == 0 - pd and box_tw[0][2] == 1000 + pd
          and box_tw[0][3] == 315 + 30 + pd, (tall_pl, box_tw))
    left_pl = LY.plate_rects(rows_p, [1000, 200], 100, "text", pd, "left")
    check("叠加底板按锚点算译文区间：左锚从轴往右长（不是以轴为中心）",
          left_pl[0][0] == 100 - pd and left_pl[0][2] == 1100 + pd and left_pl[1][2] == 300 + pd, left_pl)
    tw = [TS.body_of(r, "on") for r in TS.with_tw(TS.text_rows("一二\\N三")[0], 300, False)]
    check("叠加：打字机逐字显出——字幕稿里正文是纯文字、fo 记 tw，阶段 4 换成逐字 \\alpha：首字在起点、末字正好在全字出现那一刻，字序跨行接着数",
          len(tw) == 2 and "\\t(0,1," in tw[0] and "\\t(150,151," in tw[0] and "\\t(300,301," in tw[1], tw)
    with tempfile.TemporaryDirectory() as td:
        tw_doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {}, "regions": []}
        tw_it = OV.Item(0, 3_000_000, [[0, 0, 200, 30], [0, 40, 100, 70]], ["一二", "三"], "line",
                        events=[{"box": [0, 0, 200, 30], "t_start": 0, "t_full_sampled": 1_500_000}])
        render_overlay(Path(td) / "tw.ass", [tw_it], tw_doc, "default")
        tw_out = [x for x in (Path(td) / "tw.ass").read_text(encoding="utf-8").splitlines() if x.startswith("Dialogue: 1,")]
    check("叠加：写出来的逐字显出——两行共用一个字序，末字在全字出现（1.5 s）那一刻",
          len(tw_out) == 2 and "\\t(0,1," in tw_out[0] and "\\t(750,751," in tw_out[0] and "\\t(1500,1501," in tw_out[1], tw_out)
    evs_o = [{"text": "カチーナ", "box": [900, 800, 1000, 840]},
             {"text": "第一行のせりふです", "box": [500, 850, 1400, 880]},
             {"text": "第二行のせりふです", "box": [500, 890, 1400, 920]},
             {"text": "回", "box": [1000, 930, 1020, 950]}]
    short_b = SM.body_of([{"text": "「未着」", "box": [0, 0, 10, 10]}, {"text": "回", "box": [0, 0, 10, 10]}],
                         frozenset(), "「未着」……")
    check("叠加：1–2 字的短台词字全在原文里、占原文一半以上就算正文；单字垃圾 `回` 不算",
          [e["text"] for e in short_b] == ["「未着」"], [e["text"] for e in short_b])
    evs_o.append({"text": "un2ra", "box": [1090, 895, 1143, 928]})
    b_o = SM.body_of(evs_o, frozenset({SA.norm("カチーナ")}), "第一行のせりふです第二行のせりふです")
    check("叠加：只盖正文——名牌、1–2 字的键位提示、**字不在剧本原文里的 OCR 垃圾**（▼ 读成 un2ra）都不算；正文按 cy 分出两行",
          [e["text"] for e in b_o] == ["第一行のせりふです", "第二行のせりふです"] and len(SM.rows_of(b_o)) == 2,
          [e["text"] for e in b_o])
    hd = frozenset({SA.norm("アテア")})
    b_n = [[e["text"] for e in SM.body_of([{"text": t, "box": [0, 0, 10, 10]}], hd, ref)]
           for t, ref in (("アテア：コホンッ。", "コホンッ。"), ("謎の声：コホンッ。", "コホンッ。"))]
    check("叠加：同一行的『开头行：台词』先剥掉名字再比原文（认领凭剥过的读法认上，这里不剥正文就被筛光）；冒号前不是开头行的不剥（剥了就会过）",
          b_n == [["アテア：コホンッ。"], []], b_n)
    b_t = [[e["text"] for e in SM.body_of([{"text": t, "box": [0, 0, 10, 10]}], frozenset(), "旅人のだよ。", tpl)]
           for t, tpl in (("旅人のねるめろ(本物)だよ", "旅人の{NICKNAME}だよ。"), ("旅人のねるめろ(本物)だよ", ""),
                          ("そうだよ、旅人の言う通りだ", "旅人の{NICKNAME}だよ。"),
                          ("この子たちは優しい子なんだよ、旅人のおかげだよ", "旅人の{NICKNAME}だよ。"))]
    check("叠加：带占位的行，屏上填进玩家名、读数比原文长时，读数合模板的形状（原文的字按顺序在、多出来的只连成占位个数那么多段）也算正文；"
          "不带占位的不算；原文的字凑够了、多出来的字却散成几段的长读数不算",
          b_t == [["旅人のねるめろ(本物)だよ"], [], [], []], b_t)

    # ---- 叠加分层（owner 2026-09-14，product-goals 第 16 条）：判定写进 JSON、导出只投影 ----
    from types import SimpleNamespace as _NS
    lref = GA.Ref(doc={"units": [{"uid": "u", "lines": [
        {"key": "a", "kind": "Talk", "status": None, "text": "第一行のせりふです", "cn": "第一行台词", "speaker": "カチーナ", "speaker_cn": "卡齐娜"},
        {"key": "a2", "kind": "Talk", "status": None, "text": "今日はいい天気ですね", "cn": "今天天气真好", "speaker": "カチーナ", "speaker_cn": "卡齐娜"},
        {"key": "a3", "kind": "Talk", "status": None, "text": "ありがとう、助かったよ", "cn": "谢谢", "speaker": "カチーナ", "speaker_cn": "卡其娜"},
        {"key": "b", "kind": "Choice", "status": None, "text": "また後で来るね", "cn": "待会儿再来", "speaker": None}]}]},
        script=[], windows=[], role={}, variant_of={})
    check("名牌译文：说话人日文（归一）-> 中文，同名取出现最多的写法；没中文的不进表",
          SM.speaker_names(lref) == {SA.norm("カチーナ"): "卡齐娜"}, SM.speaker_names(lref))
    lref2 = GA.Ref(doc={"units": lref.doc["units"], "speakers": {"シュラム": "舒拉姆"}},
                   script=[], windows=[], role={}, variant_of={})
    check("名牌译文：剧本带整库的 speakers 表时先用它，表里没有的再从剧本行补（原神任务对话的剧本行多半没说话人）",
          SM.speaker_names(lref2) == {SA.norm("シュラム"): "舒拉姆", SA.norm("カチーナ"): "卡齐娜"},
          SM.speaker_names(lref2))
    # 09-14 之前的剧本没有 `speakers`、行里也没有 `speaker_cn`：名牌层会**整层没译文**而不报错
    # （2026-09-15 审计：磁盘上 11 份 ref 全是这种，照文档跑出来 0/39 而文档记 28）。
    # 判据只看键在不在，不看它空不空——空表是"库里就没有说话人"（绝区零 scene），那是另一回事
    lref_old = GA.Ref(doc={"units": [{"uid": "u", "lines": [
        {"key": "a", "kind": "Talk", "status": None, "text": "第一行のせりふです", "cn": "第一行台词", "speaker": "カチーナ"}]}]},
        script=[], windows=[], role={}, variant_of={})
    _empty_doc = {"events": [], "size": [1920, 1080], "frame_us": 500_000, "tracks": [], "provenance": {}}
    # matched.json 的加载校验（2026-09-15 审计：schema 串升过一次而全仓库没人读它）
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "m.json"
        good = {"schema": SM.SCHEMA, "provenance": {}, "stats": {}, "cues": [], "display": SM.DISPLAY,
                "overlay": {"items": [], "stats": {}, "fed": []}}
        mp.write_text(json.dumps(good), encoding="utf-8")
        ok_plain = SM.load_matched(mp)["schema"] == SM.SCHEMA and SM.load_matched(mp, need_overlay=True) is not None
        mp.write_text(json.dumps({**good, "schema": "flowocr-matched/1"}), encoding="utf-8")
        rejects_old = False
        try:
            SM.load_matched(mp)
        except SM.MatchedSchemaError:
            rejects_old = True
        mp.write_text(json.dumps({k: v for k, v in good.items() if k != "cues"}), encoding="utf-8")
        rejects_missing = False
        try:
            SM.load_matched(mp)
        except SM.MatchedSchemaError:
            rejects_missing = True
        mp.write_text(json.dumps({k: v for k, v in good.items() if k != "overlay"}), encoding="utf-8")
        no_ov_ok, no_ov_rejected = SM.load_matched(mp) is not None, False
        try:
            SM.load_matched(mp, need_overlay=True)
        except SM.MatchedSchemaError:
            no_ov_rejected = True
    check("matched.json 读走 load_matched：版本不等 / 缺顶层键就抛；--no-overlay 产的只在 need_overlay 时才拒",
          ok_plain and rejects_old and rejects_missing and no_ov_ok and no_ov_rejected,
          (ok_plain, rejects_old, rejects_missing, no_ov_ok, no_ov_rejected))
    check("旧剧本（没有 speakers 键）：名牌译文表为空，而且 ref_has_speakers 要记成 False 写进产物",
          SM.speaker_names(lref_old) == {}
          and SM.overlay_items([], _empty_doc, [], [], ref=lref_old)[1]["ref_has_speakers"] is False
          and SM.overlay_items([], _empty_doc, [], [], ref=lref2)[1]["ref_has_speakers"] is True,
          SM.speaker_names(lref_old))
    evs_l = [{"id": 0, "region": 0, "text": "カチーナ", "box": [900, 800, 1000, 840], "t_start": 0, "t_end": 60_000_000},
             {"id": 1, "region": 0, "text": "第一行のせりふです", "box": [500, 850, 1400, 880], "t_start": 0, "t_end": 5_000_000},
             {"id": 2, "region": 0, "text": "また後で来るね", "box": [1500, 500, 1800, 540],
              "t_start": 3_000_000, "t_end": 9_000_000},
             {"id": 3, "region": 0, "text": "今日はいい天気ですね", "box": [500, 850, 1400, 880],
              "t_start": 20_000_000, "t_end": 25_000_000},
             {"id": 4, "region": 0, "text": "ありがとう、助かったよ", "box": [500, 850, 1400, 880],
              "t_start": 40_000_000, "t_end": 45_000_000}]
    cues_l = [_NS(start=0.0, end=5.0, lines=["カチーナ", "第一行のせりふです", "また後で来るね"]),
              _NS(start=20.0, end=25.0, lines=["カチーナ", "今日はいい天気ですね"]),
              _NS(start=40.0, end=45.0, lines=["カチーナ", "ありがとう、助かったよ"])]
    evs_of_l = [[evs_l[0], evs_l[1], evs_l[2]], [evs_l[0], evs_l[3]], [evs_l[0], evs_l[4]]]
    recs_l = [{"start": 0.0, "end": 5.0, "ref": {"key": "a", "jp": "第一行のせりふです", "cn": "第一行台词"},
               "extra": [{"key": "b", "jp": "また後で来るね", "cn": "待会儿再来"}], "cues": [0]}]
    SM.mark_display(recs_l, cues_l, lambda c: "dialogue")
    doc_l = {"events": evs_l, "size": [1920, 1080], "frame_us": 500_000, "provenance": {}}
    items_l, st_l, _ = SM.overlay_items(recs_l, doc_l, evs_of_l, cues_l, ref=lref)
    check("叠加分层：正文 / extra 各盖自己的行（extra 不并进正文）、名牌一块带中文名；顺序 body→…→extra→name",
          [(it["layer"], it["events"], it["cn"]) for it in items_l]
          == [("body", [1], "第一行台词"), ("extra", [2], "待会儿再来"), ("name", [0], "卡齐娜")]
          and st_l["name_translated"] == 1,
          [(it["layer"], it["events"], it["cn"]) for it in items_l])
    from flowocr.artifacts.matchedio import Visible as _Vis
    proj_all = OV.project_items(items_l, doc_l, "cn", _Vis(frozenset({"dialogue", "name"}), True))
    check("叠加投影：按组的可见性画、按语言取译文，不重新判断；层只定怎么画（名牌不带打字机要的正文事件）；多行用公共轴",
          [t.kind for t in OV.project_items(items_l, doc_l, "cn", _Vis(frozenset({"dialogue"}), True))] == ["line", "line"]
          and [(t.lines, t.kind) for t in proj_all] == [(["第一行台词"], "line"), (["待会儿再来"], "line"), (["卡齐娜"], "name")]
          and proj_all[2].events == [] and all(t.translated for t in proj_all),
          [(t.lines, t.kind) for t in proj_all])
    nameless = GA.Ref(doc={"units": []}, script=[], windows=[], role={}, variant_of={})
    nm = [it for it in SM.overlay_items(recs_l, doc_l, evs_of_l, cues_l, ref=nameless)[0] if it["layer"] == "name"]
    check("名牌没对上说话人表时 jp / cn 都为空（jp 口径不拿 OCR 读数盖名牌自己），OCR 原文留在 ocr",
          len(nm) == 1 and nm[0]["jp"] is None and nm[0]["cn"] is None and nm[0]["ocr"] == "カチーナ"
          and items_l[2]["jp"] == "カチーナ", nm)
    check("名牌挂在正文上：正文紧贴在名牌下方、左右对得上才算；并排一行的角色列表 / 离得远的键位不算",
          tracksio.name_over_body(evs_l[0]["box"], evs_l[1]["box"])
          and not tracksio.name_over_body(evs_l[0]["box"], [1500, 850, 1700, 880])
          and not tracksio.name_over_body(evs_l[0]["box"], [1100, 800, 1300, 840]))
    check("主轨外逐字等于剧本行的块按剧本行分层：选项类（类型 Choice、或处境 OffBand）进 choice，其余进 offmain；"
          "有别的处境的选项（重演拷贝）不算选项类",
          [SM.choice_layer(SM.choice_like(k, st)) for k, st in
           (("Choice", None), ("Talk", "OffBand"), ("Talk", None), ("Loose", None), ("Choice", "Duplicate"))]
          == ["choice", "choice", "offmain", "offmain", "offmain"])
    check("显示协议·定组按类型（处境不改组）：选项 / 黑屏旁白 / 特殊上屏形式（原神非 Normal 的语音字幕、绝区零 Tips / MiddleTips）/ "
          "散条目按 role / 带外台词 / 台词；没列到的类型落进 dialogue",
          [SM.group_of(k, st, r) for k, st, r in (
              ("Choice", None, None), ("Choice", "Duplicate", None), ("BlackScreen", "OutOfSpan", None),
              ("ReminderUI", None, "Banner"), ("ReminderUI", "OutOfSpan", "WhiteMessage"), ("Loose", None, "Tips"),
              ("Loose", "OutOfSpan", "MiddleTips"), ("Loose", None, "Comic"), ("Loose", None, "Cinema"),
              ("Loose", None, "Cissia"), ("Loose", None, "UIText"), ("Talk", "OffBand", "?"), ("NPC", None, None),
              ("Reminder", "Variant", "Normal"), ("SomethingNew", None, None))]
          == ["choice", "choice", "narration", "special", "special", "special", "special", "cutscene", "cutscene", "ui", "ui",
              "offband", "dialogue", "dialogue", "dialogue"])
    check("显示协议·声明自洽：保留组在、档位合法、没用关键字当组名；dialogue 直接进 default，special 默认进，ui / unmatched / unfed 默认不进",
          set(MIO.RESERVED) <= set(SM.DISPLAY["groups"]) and not set(MIO.KEYWORDS) & set(SM.DISPLAY["groups"])
          and all(d["tier"] in MIO.TIERS for d in SM.DISPLAY["groups"].values())
          and SM.DISPLAY["groups"]["dialogue"]["tier"] == "always" and SM.DISPLAY["groups"][MIO.SPECIAL]["tier"] == "on"
          and all(SM.DISPLAY["groups"][g]["tier"] == "off" for g in ("ui", "elsewhere", "unmatched", "unfed")))
    tref = GA.Ref(doc={"units": [{"uid": "u", "lines": [
        {"key": "t", "kind": "Talk", "status": None, "role": "x", "text": "ここで待っていてくれ", "cn": "在这里等我",
         "anchor": {"episodes": [[10.0, 12.0]]}}]}]}, script=[], windows=[], role={}, variant_of={})
    tdoc = {"events": [{"id": 0, "region": 3, "text": "ここで待っていてくれ", "box": [300, 100, 900, 140],
                        "t_start": 10_000_000, "t_end": 14_000_000}], "frame_us": 500_000}
    check("选项层那条路找到的台词（主轨外逐字相等、没被认领，多是对话记录面板）归 elsewhere，默认不画",
          [(it["layer"], it["group"]) for it in SM.choice_items(tdoc, set(), tref)] == [("offmain", "elsewhere")],
          SM.choice_items(tdoc, set(), tref))
    fr = [{"cues": [0], "ref": {"key": "a", "kind": "Talk", "jp": "今日はいい天気ですね"},
           "extra": [{"key": "b", "kind": "Choice", "jp": "また後で来るね"}]},
          {"cues": [1], "ref": {"key": "c", "kind": "Talk", "jp": "全然違う台詞がここにある"}, "extra": []}]
    SM.mark_display(fr, [_NS(lines=["今日はいい天気ですね", "また後で来る"]), _NS(lines=["…"])],
                    lambda c: "choice" if c["kind"] == "Choice" else "dialogue")
    check("显示协议·填位：认领它的 cue 里有一条读到了这一行就不是填位，一条都没读到是；组按认领各打各的",
          [(c["group"], c["fill"]) for c in (fr[0]["ref"], fr[0]["extra"][0], fr[1]["ref"])]
          == [("dialogue", False), ("choice", False), ("dialogue", True)],
          [(c["group"], c["fill"]) for c in (fr[0]["ref"], fr[0]["extra"][0], fr[1]["ref"])])
    cref = GA.Ref(doc={"units": [{"uid": "u", "lines": [
        {"key": "ch", "kind": "Choice", "status": None, "role": "PLAYER", "text": "また後で来るね", "cn": "待会儿再来",
         "anchor": {"episodes": [[10.0, 12.0]]}}]}]}, script=[], windows=[], role={}, variant_of={})
    cdoc = {"events": [{"id": 0, "region": 3, "text": "また後で来るね", "box": [1500, 500, 1800, 540],
                        "t_start": 10_000_000, "t_end": 14_000_000}], "frame_us": 500_000}
    got_free, got_taken = SM.choice_items(cdoc, set(), cref), SM.choice_items(cdoc, set(), cref, {0})
    check("选项层：已被正文 / 第二条认领用掉的事件不再出块（同一个按钮不画两遍）；出的块带组、不是填位",
          [(it["layer"], it["group"], it["fill"]) for it in got_free] == [("choice", "choice", False)] and got_taken == [],
          (got_free, got_taken))

    def pref(lines, text, t=10.5, ctx=True):
        r = GA.Ref(doc={"units": [{"uid": "u", "lines": [
            {"key": k, "kind": kind, "status": st[0] if st else None, "role": "PLAYER", "text": x, "cn": cn,
             "anchor": {"episodes": [[10.0, 12.0]]}} for k, kind, x, cn, *st in lines]
            + ([{"key": "npc", "kind": "NPC", "status": None, "role": "NPC", "text": "そろそろ出発しようか", "cn": "该出发了",
                 "anchor": {"episodes": [[9.0, 11.0]]}}] if ctx else [])}]}, script=[], windows=[], role={}, variant_of={})
        d = {"events": [{"id": 0, "region": 3, "text": text, "box": [1500, 500, 1800, 540],
                         "t_start": int(t * 1e6), "t_end": int(t * 1e6) + 3_000_000}], "frame_us": 500_000}
        return [(it["key"], it["cn"]) for it in SM.choice_items(d, set(), r)]
    ch = ("ch", "Choice", "どうして戦士を選抜する必要があるの？", "为什么要选拔战士？")
    check("选项层·前缀路：按钮后半截被挡住，读数是选项行的前缀（≥6 字、占一半）就出整句译文；"
          "处境 OffBand 的行也算选项类；太短、不到一半、以它开头的还有别的说法（台词、没译文的行也算）、行不是选项类、不在单元时段里、单元此刻不在播（别的行的锚点离得远；这一行自己的锚点可能就是凭这个读数锚上的，不算），都不认",
          pref([ch], "どうして戦士を選抜す") == [("ch", "为什么要选拔战士？")]
          and pref([ch], "どうして戦") == []
          and pref([("c8", "Choice", "どうして戦士なの", "为什么是战士")], "どうして戦") == []
          and pref([("ob", "Talk", "あの日のことを今でも覚えているよ", "那天的事我到现在还记得", "OffBand")], "あの日のことを今でも")
          == [("ob", "那天的事我到现在还记得")]
          and pref([("ch", "Choice", "どうして戦士を選抜する必要があるのか、ちゃんと説明してほしいな", "x")], "どうして戦士を選抜す") == []
          and pref([ch, ("t", "Talk", "どうして戦士を選抜するんだ？", "")], "どうして戦士を選抜す") == []
          and pref([ch, ("t", "Talk", "どうして戦士を選抜す", "")], "どうして戦士を選抜す") == []
          and pref([("t", "Talk", ch[2], ch[3])], "どうして戦士を選抜す") == []
          and pref([ch], "どうして戦士を選抜す", t=200.0) == []
          and pref([ch], "どうして戦士を選抜す", t=50.0) == []
          and pref([ch], "どうして戦士を選抜す", ctx=False) == [],
          [pref([ch], "どうして戦士を選抜す"), pref([ch, ("t", "Talk", "どうして戦士を選抜するんだ？", "")], "どうして戦士を選抜す")])
    lv = [pref([("h", "Choice", "また出た、ホース先生！", "又来了")], "また出た、ホ一ス先生！"),
          pref([("k", "Choice", "カード一枚", "一张卡")], "カード一枚")]
    lv.append(pref([("hp", "Choice", "また出た、ホース先生！今日も元気だね", "又来了")], "また出た、ホ一ス先"))
    check("选项层：长音 `ー` 被读成汉字 `一`（片假名后面）时折回来再认一次（逐字和前缀两条路都折）；原样先认，剧本里真是汉字 `一` 的照认",
          lv == [[("h", "又来了")], [("k", "一张卡")], [("hp", "又来了")]], lv)
    nref = GA.Ref(doc={"speakers": {"カチーナ": "卡齐娜"}, "units": [{"uid": "u", "lines": [
        {"key": "nc", "kind": "Choice", "status": None, "role": "PLAYER", "text": "カチーナ", "cn": "卡齐娜？",
         "anchor": {"episodes": [[1.0, 2.0]]}}]}]}, script=[], windows=[], role={}, variant_of={})
    n_items = SM.overlay_items(recs_l, doc_l, evs_of_l, cues_l, ref=nref)[0]

    def double_draws(items):
        """同一个事件被时段相交的两个条目引用（同一时刻画了两块）：(事件, 层, 层)。在动的名牌切成的几段首尾相接，不相交。"""
        by: dict[int, list] = {}
        for it in items:
            for i in it["events"]:
                by.setdefault(i, []).append(it)
        return [(i, a["layer"], b["layer"]) for i, its in by.items() for k, a in enumerate(its) for b in its[k + 1:]
                if a["start_us"] < b["end_us"] and b["start_us"] < a["end_us"]]
    nref_bare = GA.Ref(doc={**nref.doc, "speakers": {}}, script=[], windows=[], role={}, variant_of={})
    b_items = SM.overlay_items(recs_l, doc_l, evs_of_l, cues_l, ref=nref_bare)[0]
    check("一个事件同一时刻只画一块：名牌的字恰好也是一行选项时，名牌译出了归名牌、没译出归选项层（名牌条目去掉）；条目的事件不重复列",
          [(it["layer"], it["events"]) for it in n_items if 0 in it["events"]] == [("name", [0])]
          and [(it["layer"], it["events"]) for it in b_items if 0 in it["events"]] == [("choice", [0])]
          and double_draws(n_items) == [] and double_draws(b_items) == []
          and all(len(set(it["events"])) == len(it["events"]) for it in n_items + b_items),
          ([(it["layer"], it["events"]) for it in n_items], [(it["layer"], it["events"]) for it in b_items]))
    ov = SM.overlay_doc([{"group": "dialogue", "events": [3]}, {"group": "name", "events": [1]}], {},
                        [[{"id": 3}, {"id": 1}], [{"id": 3}, {"id": 7}]], {})
    check("显示协议·overlay：fed 是喂给匹配器的事件（去重、升序），stats 按组计数",
          ov["fed"] == [1, 3, 7] and ov["stats"]["groups"] == {"dialogue": 1, "name": 1}, ov)
    check("进不进分母：类型在带内且没有处境",
          [GS.in_band(k, st) for k, st in (("NPC", None), ("Talk", "OffBand"), ("Choice", None), ("BlackScreen", "OutOfSpan"))]
          == [True, False, False, False])
    moving_ev, still_ev = {"box": [0, 0, 10, 10], "boxes": [[0, [0, 0, 10, 10]]]}, {"box": [0, 0, 10, 10]}
    check("主轨外的块：在动的只收选项类（滑入的按钮），在动的台词 / UI 不收（静止框盖不住滚动的面板）；不动的都收",
          SM.block_ok(moving_ev, True) and not SM.block_ok(moving_ev, False)
          and SM.block_ok(still_ev, False) and SM.block_ok(still_ev, True))
    check("主轨外的块：offmain 至少在屏上两个采样点（一帧的是滚动残影），选项类不拦",
          not SM.block_long_enough(False, 500_000, 500_000) and SM.block_long_enough(False, 1_000_000, 500_000)
          and SM.block_long_enough(True, 500_000, 500_000))
    from flowocr.analyze import build_tracks as _bt     # 本函数后面另有 `bt` 局部名，这里别撞
    tw_run = _bt.Run([0, 0, 100, 20], "知ってる、捕まえたのは僕だ。", 0, 1_500_000, 3, .9,
                     ["知", "知って", "知ってる、捕まえたのは僕だ。"], times=[0, 500_000, 1_000_000])
    still_run = _bt.Run([0, 0, 100, 20], "そうだね", 0, 1_000_000, 2, .9, ["そうだね", "そうだね"], times=[0, 500_000])
    check("t_full_sampled：第一票读全的时刻；首票就全 = 没有打字过程，记 t_start；和 t_full 分开（t_full 仍是 None）",
          _bt.sampled_full(tw_run) == 1_000_000 and _bt.sampled_full(still_run) == 0
          and _bt.event_dict(tw_run, 0, 0)["t_full"] is None
          and _bt.event_dict(tw_run, 0, 0)["t_full_sampled"] == 1_000_000)

    # 探针必须拿**产物自己的**聚类参数重建，不能用函数默认值
    # （2026-09-12 外部 review：漏传 region_time_gate，分层表"干不干净"整列错，没有任何报错）
    import probe_band_layers as PBL
    check("探针 probe_band_layers 把 cluster_windowed 的旋钮接全了（以后加旋钮不接就跑不起来）",
          PBL.check_cluster_kwargs() is None)
    check("探针的聚类参数逐个来自产物 provenance，不是函数默认值",
          PBL.cluster_kwargs({"mutual_x": True, "region_time_gate": 7, "region_h_ratio": 2.5})
          == {"mutual_x": True, "time_gate": 7, "h_ratio": 2.5},
          PBL.cluster_kwargs({"mutual_x": True, "region_time_gate": 7, "region_h_ratio": 2.5}))
    check("旧产物没有 region_h_ratio 键：取加旋钮之前的行为 1.7",
          PBL.cluster_kwargs({"mutual_x": False, "region_time_gate": 0})["h_ratio"] == 1.7)

    # 跨进程确定性：内置 hash() 对 str 每个进程随机加盐；gram 哈希必须和它无关
    code = ("import sys, json; sys.path[:0] = [sys.argv[1], sys.argv[2]]; from flowocr.analyze import gamescript as GS; "
            "import guards._common as T, tempfile, pathlib; "
            "d = tempfile.mkdtemp(); "
            "print(json.dumps([GS.gram_hashes('テストの文字列ですよね').tolist(), "
            "T.synthetic_gamescript(pathlib.Path(d))], ensure_ascii=False))")
    outs = [subprocess.run([sys.executable, "-c", code, str(ROOT / "src"), str(ROOT / "tests")],
                           capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "PYTHONHASHSEED": s}).stdout for s in ("1", "2")]
    check("gram 哈希与合成剧本跨进程逐字相同（两个 PYTHONHASHSEED）",
          outs[0] == outs[1] != "" and json.loads(outs[0])[0] == GS.gram_hashes("テストの文字列ですよね").tolist(),
          outs[0][:200])

    # 缓存里 pickle 的类必须挂在 `gamescript` 模块下：当脚本跑时若挂在 `__main__`，
    # 别的工具 import gamescript 再读同一份缓存就找不到类（2026-09-14 实测过的 AttributeError）
    code = ("import contextlib, io, pickle, runpy, sys; sys.argv = ['gamescript', '--help']; sys.path.insert(0, SRC_DIR)\n"
            "try:\n    with contextlib.redirect_stdout(io.StringIO()):\n"
            "        runpy.run_module('flowocr.analyze.gamescript', run_name='__main__', alter_sys=True)\nexcept SystemExit:\n    pass\n"
            "GSm = sys.modules['flowocr.analyze.gamescript']\n"   # 当脚本（-m）跑时类仍挂在正式包名下（它 __main__ 里先按包名 import 自己）
            "sys.stdout.write(pickle.dumps(GSm.Unit('u', None, [GSm.Line('k', 'Talk', 'NPC', None, 'あ', None)])).hex())")
    code = code.replace("SRC_DIR", repr(str(ROOT / "src")))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
    try:
        import pickle
        back = pickle.loads(bytes.fromhex(res.stdout))
        ok = type(back) is GS.Unit and type(back.lines[0]) is GS.Line
    except Exception as exc:        # noqa: BLE001
        ok, back = False, f"{type(exc).__name__}: {exc} / {res.stderr[-300:]}"
    check("gamescript 当脚本跑时类挂在 `gamescript` 模块下：子进程 pickle 的 Unit 在 import 侧读得回来", ok, str(back)[:200])

    line = GS.cnorm("これは検索の下限を確かめるための長い一行のテキストです")
    idx = GS.Index([line, GS.cnorm("まったく関係のない別の行がここに入っています")])
    found = lambda n: [0 in idx.query(line[o:o + n]) for o in range(len(line) - n + 1)]  # noqa: E731
    check(f"--qmin 的下限 {GS.QMIN_FLOOR}：这么长的查询在库行里任何位置都找得到，短一个字就有找不到的",
          all(found(GS.QMIN_FLOOR)) and not all(found(GS.QMIN_FLOOR - 1)),
          (found(GS.QMIN_FLOOR), found(GS.QMIN_FLOOR - 1)))

    # 变体组在量轨这一侧：任一种说法被认领都记在代表行上，sub_hit 也改指代表行
    ref = GA.Ref(doc={}, script=[SA.Entry(key="r", kind="Talk", cmd="", text="リンさん、聞こえるかしら", src="u"),
                                 SA.Entry(key="v", kind="Variant", cmd="", text="アキラさん、聞こえるかしら",
                                          src="u")],
                 windows=[[[0.0, 100.0]], [[0.0, 100.0]]], role={}, variant_of={"v": "r"})
    st = GA.score(ref, ref.script, [(5.0, "アキラさん、聞こえるかしら", "x")], 0.75)
    check("变体组：另一种说法被认领 -> 代表行算命中、sub_hit 指代表行、via 记下是哪一种",
          ref.script[0].matched == 0 and st["sub_hit"] == [0] and st["via"]["r"].key == "v",
          (ref.script[0].matched, st["sub_hit"]))

    # 长 gap 在这把尺上没有分支解释（audit-6 §1.1）：long_is_branch=False 时一起算进疑似真漏
    ents = [SA.Entry(key=str(i), kind="Talk", cmd="", text=f"内容のある台詞{i}", src="u") for i in range(8)]
    for i, e in enumerate(ents):
        e.matched = 0 if i in (0, 6) else -1        # 1–5 连续 5 条未命中（长 gap），7 单独一条
    with redirect_stdout(io.StringIO()):
        g_branch = SA.gap_report(ents, 5)["by_class"][evalkit.CLASSES[2]]
        g_nobr = SA.gap_report(ents, 5, long_is_branch=False)["by_class"][evalkit.CLASSES[2]]
    check("长 gap：《魔裁》口径不计（分母 3、漏 1）；游戏库口径一起算（分母 8、漏 6、其中长 gap 5）",
          (g_branch, g_nobr) == ([3, 1, 0], [8, 6, 5]), (g_branch, g_nobr))

    # 窗内并区的时间门（`--region-time-gate`，默认 0 = 关，corpus §8）：关着的时候必须和以前逐字一样
    from flowocr.analyze import build_tracks as bt

    def two_rows(*spans):      # 上下两行、x 相同、行距 1.5 倍字高；第二行按 spans 出现若干次
        rs = [bt.Run(box=[400, 800, 1500, 830], text="上の行", t_start=0, t_end=1_000_000, n_obs=2, conf=0.9)]
        rs += [bt.Run(box=[400, 845, 1500, 875], text="下の行", t_start=t, t_end=t + 1_000_000,
                      n_obs=2, conf=0.9) for t in spans]
        return rs
    same, apart = two_rows(500_000), two_rows(5_000_000)
    tr_same, tr_apart = [[0], [1]], [[0], [1]]
    check("时间门默认关：产物和以前一样（从不同屏的两行照旧并成一块）",
          len(bt.build_regions(apart, tr_apart, 1920, 1080)) == 1
          and len(bt.build_regions(same, tr_same, 1920, 1080)) == 1)
    check("时间门开（2）：同屏过的照连；各占一段、只切换一次的分开",
          len(bt.build_regions(same, tr_same, 1920, 1080, time_gate=2)) == 1
          and len(bt.build_regions(apart, tr_apart, 1920, 1080, time_gate=2)) == 2)
    # 交错：A 出现在 B 的两次之间 = 切换两次，算同一块（绝区零对话框的行数随台词变，未必同屏）
    inter = two_rows(-5_000_000, 5_000_000)
    check("时间门开（2）：你一条我一条地交错也算同一块",
          len(bt.build_regions(inter, [[0], [1, 2]], 1920, 1080, time_gate=2)) == 1)
    check("交错次数与同屏：各占一段 1 次、夹在中间 2 次；相交的时段算同屏",
          (bt.alternations([(0, 10)], [(20, 30)]), bt.alternations([(10, 20)], [(0, 5), (30, 40)]),
           bt.cooccur([(0, 10)], [(5, 30)]), bt.cooccur([(0, 10)], [(20, 30)]))
          == (1, 2, True, False))

    from flowocr.artifacts import tracksio
    prov = {"obs": "out/x.jsonl", "argv": ["out/x.jsonl", "--outdir", "o", "--tag", "t", "--main-band", "0"]}
    prov2 = {"obs": "out/x.jsonl", "argv": ["--iou", "0.3", "out/x.jsonl", "--outdir=o", "--tag", "t", "--viewport", "0,0,976,549"]}
    prov3 = {"obs": "out/x.jsonl", "argv": ["out/x.jsonl", "--viewport=0,0,976,549", "--iou", "0.3"]}
    check("BUILD_ARGS 按名剥掉不算旋钮的 obs / --outdir / --tag / --viewport，不靠位置（audit-6 §2.4）：驱动没带同一个 --viewport 也不重建、不丢记下的位置",
          (tracksio.build_args(prov), tracksio.build_args(prov2), tracksio.build_args(prov3)) == (["--main-band", "0"], ["--iou", "0.3"], ["--iou", "0.3"]),
          (tracksio.build_args(prov), tracksio.build_args(prov2), tracksio.build_args(prov3)))
    want = "--iou 0.3 --viewport 0,0,976,549".split()
    drivers = [(Path(__file__).resolve().parents[2] / "dev_tools" / f).read_text(encoding="utf-8") for f in ("head2head.sh", "gametext_eval.sh")]
    check("驱动这一趟的 BUILD_ARGS 和产物那侧同一套剥法：带同一个 --viewport 重跑命中缓存，不带也命中",
          tracksio.knob_args(want) == tracksio.knob_args(want[:2]) == tracksio.build_args(prov2)
          and all("tracksio.knob_args(sys.argv[2].split())" in d for d in drivers))


def t_readables() -> None:
    """阅读面板长文本（`scriptmatch.readable_items` / `readables_of`）：只读一遍 obs、帧里有一行找到段就留下整帧；包里没表要报错。"""
    print("阅读面板长文本")
    import json
    import tempfile
    from pathlib import Path
    from flowocr.analyze import scriptmatch as SM
    from flowocr.analyze import game_align as GA
    para_jp = "この本には古い時代の物語がたくさん書かれている"
    paras = {GA.GS.cnorm(para_jp): (para_jp, "这本书里写着许多古老时代的故事", "This book holds many tales of olden times.")}
    rows = [{"t_us": 0, "text": "第一章", "box": [500, 300, 600, 330]},                  # 小标题：不去找段，但块要长满到它
            {"t_us": 0, "text": "この本には古い時代の物語がたくさん", "box": [500, 340, 1100, 370]},
            {"t_us": 0, "text": "書かれている", "box": [500, 375, 700, 405]},
            {"t_us": 500_000, "text": "第一章", "box": [500, 300, 600, 330]},
            {"t_us": 500_000, "text": "この本には古い時代の物語がたくさん", "box": [500, 340, 1100, 370]},
            {"t_us": 500_000, "text": "書かれている", "box": [500, 375, 700, 405]},
            {"t_us": 1_000_000, "text": "ぜんぜん関係のない長めの一行の文字列です", "box": [500, 340, 1100, 370]},
            {"t_us": 1_500_000, "text": "この本には古い時代の物語がたくさん", "box": [500, 340, 1100, 370]}]
    taken = [{"t_start": 1_400_000, "t_end": 1_600_000, "box": [480, 320, 1120, 390]}]   # 1.5 s 那一行在台词正文框里
    with tempfile.TemporaryDirectory() as d:
        obs = Path(d) / "o.jsonl"
        obs.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [{"_meta": {}}, *rows]), encoding="utf-8")
        items, used = SM.readable_items(obs, paras, 500_000, taken)
        check("只读一遍 obs：找到段的帧留下、相邻帧同一段接着延长；没找到段的帧、台词正文框里的行不算；块长满到同一栏的小标题",
              len(items) == 1 and (items[0]["start_us"], items[0]["end_us"]) == (0, 1_000_000)
              and items[0]["rows"][0][1] == 300 and items[0]["paras"]["cn"] == ["这本书里写着许多古老时代的故事"]
              and items[0]["paras"]["en"] == ["This book holds many tales of olden times."] and used == 2,
              (items, used))
        # 一帧里某一段只认出一行、这一行只占那一段一小部分：屏上一行短 UI 恰好是长文的子串（称号撞上书，gi2 2026-10-06），不算；
        # 一句话的短段（道具说明）单行就是整段，算
        long_jp = "とある記者は「スチームバード新聞」編集長として長年にわたり街の出来事を記録し続けてきたのだった"
        short_jp = "秘境に足を踏み入れるために大事なもの"
        paras2 = {GA.GS.cnorm(long_jp): (long_jp, "长", None), GA.GS.cnorm(short_jp): (short_jp, "短", None)}
        one = Path(d) / "one.jsonl"
        one.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
            {"_meta": {}},
            {"t_us": 0, "text": "「スチームバード新聞」編集長として", "box": [500, 300, 900, 330]},
            {"t_us": 500_000, "text": "秘境に足を踏み入れるために大事なもの。", "box": [500, 300, 1100, 330]}]), encoding="utf-8")
        got1, _ = SM.readable_items(one, paras2, 500_000, [])
        check("一段只认出一行：这一行占不到那一段一半（短 UI 撞上长文）不算，单行就是整段（一句话的说明）照算",
              [x["paras"]["cn"] for x in got1] == [["短"]], got1)
        bad = Path(d) / "b.jsonl"
        bad.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [{"_meta": {}}, rows[3], rows[0]]), encoding="utf-8")
        check("obs 没按时间排就当场报错（按帧只读一遍的前提）",
              raises(lambda: SM.readable_items(bad, paras, 500_000, []), SystemExit))

    # 契约里 id 单独不唯一（`(source, id)` 才唯一）：逐行配对，两条都收进段索引（review 2026-10-06）
    class OneTable:
        fingerprint = "fp"
        def has(self, tables, langs=("ja", "zh-Hans")):
            return True
        def require(self, tables, langs=("ja", "zh-Hans")):
            pass
        def verify_stored(self, tables, langs=("ja", "zh-Hans")):
            pass
    jp2 = "二冊目の本にはまた別の古い物語が書かれている"
    pairs = [({"id": 1, "source": "Readable/", "text": para_jp}, {"id": 1, "source": "Readable/", "text": "这本书里写着许多古老时代的故事"},
              {"id": 1, "source": "Readable/", "text": "This book holds many tales of olden times."}),
             ({"id": 1, "source": "MailExcelConfigData", "text": jp2}, {"id": 1, "source": "MailExcelConfigData", "text": "第二本书里写着另一个古老的故事"},
              {"id": 1, "source": "MailExcelConfigData", "text": "One.\n\nTwo."})]
    o_b, o_p, o_root = GA.GS.bundle_of, GA.GS.paired, SM.paths.data_root
    with tempfile.TemporaryDirectory() as d2:
        GA.GS.bundle_of, GA.GS.paired, SM.paths.data_root = (lambda game: OneTable()), (lambda game, stem: iter(pairs)), (lambda: Path(d2))
        try:
            got = SM.readable_paras("genshin")
            again = SM.readable_paras("genshin")          # 第二次走缓存
        finally:
            GA.GS.bundle_of, GA.GS.paired, SM.paths.data_root = o_b, o_p, o_root
    check("readables 表 id 重复、source 不同：两条都收进段索引；缓存读回来一样；英文段数和日文对得上的带英文段，对不上的记 None",
          got is not None and len(got) == 2 and got == again
          and sorted(v[2] or "" for v in got.values()) == ["", "This book holds many tales of olden times."], got)

    class NoTable:
        fingerprint = "x"
        def has(self, tables, langs=("ja", "zh-Hans")):
            return False
    orig = GA.GS.bundle_of
    GA.GS.bundle_of = lambda game: NoTable()
    try:
        ref = GA.Ref(doc={"provenance": {"game": "genshin"}}, script=[], windows=[], role={}, variant_of={})
        check("选开了阅读面板、文本包里却没有 readables 表：当场报错，不静默什么都不盖",
              raises(lambda: SM.readables_of(ref), SystemExit))
    finally:
        GA.GS.bundle_of = orig


def t_head_lines_order() -> None:
    """开头行（名牌层）判定与遍历顺序无关（2026-09-26）。"""
    print("\n## 开头行判定的确定性")
    from itertools import permutations
    from flowocr.analyze import game_align as GA
    # 等长的三条：X~Y、Y~Z 相似 ≥0.6，X 与 Z 不像。Y 先当代表时 X、Z 都并进它（1 种），X 先当代表时 Z 另起（2 种）——
    # 只按长度排、等长按输入（= set 迭代）顺序走的话，数出几种取决于进程的哈希盐
    xyz = ["あいうえお", "あいうかき", "いうかきく"]
    got = {GA.distinct_bodies(list(p), 99) for p in permutations(xyz)}
    check("开头行·distinct_bodies 与末行的遍历顺序无关（str 哈希每进程加盐，只按长度排会让同一份轨两次跑出不同的名牌层）",
          len(got) == 1, f"{got}")
    # 喂入按可见的行拼（`tracksio.rows_of_events`）之后，和别的字在同一行上的名牌事件拼进那一行，不再单独算一次开头行
    # （hsr 队伍栏「1」+「サンデー」，2026-10-06 验收 peer）：名牌只靠它自己单独成行的 cue 凑够 HEAD_MIN_BODIES
    from flowocr.artifacts import tracksio
    from flowocr.analyze import script_align as SA
    def ev(text, box):
        return {"text": text, "box": box}
    party = tracksio.rows_of_events([ev("サンデー", [1206, 594, 1316, 629]), ev("1", [1156, 600, 1194, 625])])
    check("按行拼：同一行上的名牌和队伍栏数字拼成一行（「1サンデー」）", [r["text"] for r in party] == ["1サンデー"],
          [r["text"] for r in party])
    talk = [(10.0 * k, f"サンデー\n{b}", "x") for k, b in enumerate(("どうかしましたか？", "行動を始める前に"))]
    with_party = talk + [(30.0, "\n".join(r["text"] for r in party) + "\nキュレネ", "x")]
    check("开头行：名牌和别的字拼在同一行时不替它凑数（两条台词 cue 不够 HEAD_MIN_BODIES）",
          SA.norm("サンデー") not in GA.head_lines(with_party)
          and SA.norm("サンデー") in GA.head_lines(talk + [(30.0, "サンデー\nワタシたちには", "x")]))


def t_nameplate_terms() -> None:
    """名牌的短名词表（2026-09-26，followups「名牌上的人名没有译名」）：说话人表查不到的名牌查 terms。"""
    print("\n## 名牌的短名词表")
    from types import SimpleNamespace as _NS
    from flowocr import textfold
    from flowocr.analyze import game_align as GA
    from flowocr.analyze import gamescript as GS
    from flowocr.analyze import script_align as SA
    from flowocr.analyze import scriptmatch as SM
    want = GS.term_wanted(["シュラム", " 傭兵 ", "口", "E", "Bi", "8147!!", "任務アイテム"])
    check("短名词·查询键：内容 ≥2 字且含假名 / 汉字才查（噪音读数 口 / E / Bi / 数字不查，免得在默认预设里被『译』出来）",
          want == {"シュラム", "傭兵", "任務アイテム"}, want)
    row = lambda i, t: {"id": i, "text": t}
    pairs = [(row(1, "旅人"), row(1, "旅行者"), row(1, "Traveler")), (row(2, "旅人"), row(2, "旅人"), row(2, "Wanderer")),
             (row(3, "旅人"), row(3, "旅行者"), row(3, "Traveler")),
             (row(4, "スコア"), row(4, "分数"), row(4, "Score")), (row(5, "スコア"), row(5, "评分"), row(5, "Rating")),
             (row(6, "シュラム"), row(6, "施拉姆"), None), (row(7, "{NICKNAME}"), row(7, "{NICKNAME}"), None),
             (row(8, "傭兵"), row(8, None), row(8, "Mercenary")), (row(9, "未出現"), row(9, "没出现"), None)]
    # 隊長：中文多数是 队长（4 行），英文整体多数 Leader 却全在 统领 那几行——英文要在中文取中的行里取
    pairs += [(row(10 + i, "隊長"), row(10 + i, cn), row(10 + i, en)) for i, (cn, en) in
              enumerate([("队长", "Captain"), ("队长", "Captain"), ("队长", "Chief"), ("队长", "Head"),
                         ("统领", "Leader"), ("统领", "Leader"), ("统领", "Leader")])]
    got_t = GS.pick_terms(pairs, {"旅人", "スコア", "シュラム", "傭兵", "隊長"}, "F")
    check("短名词·消歧按 hash 条数取多数、打平取短的再按字典序；只收 obs 里出现过的键；中文缺的不收；"
          "英文在中文取中的那些行里同法取（不和中文分开投），没有英文那份的不带 en",
          got_t == {"旅人": {"cn": "旅行者", "n": 2, "of": 3, "en": "Traveler"}, "スコア": {"cn": "分数", "n": 1, "of": 2, "en": "Score"},
                    "シュラム": {"cn": "施拉姆", "n": 1, "of": 1}, "隊長": {"cn": "队长", "n": 4, "of": 7, "en": "Captain"}}, got_t)
    tref = GA.Ref(doc={"units": [], "speakers": {"カチーナ": "卡齐娜"},
                       "terms": {"カチーナ": {"cn": "卡琪娜", "n": 1, "of": 1}, "シュラム": {"cn": "施拉姆", "n": 2, "of": 2}}},
                  script=[], windows=[], role={}, variant_of={})
    evs = [{"id": 0, "region": 0, "text": "カチーナ", "box": [900, 800, 1000, 840], "t_start": 0, "t_end": 60_000_000},
           {"id": 1, "region": 0, "text": "シュラム", "box": [900, 800, 1000, 840], "t_start": 0, "t_end": 60_000_000},
           {"id": 2, "region": 0, "text": "第一行のせりふです", "box": [500, 850, 1400, 880], "t_start": 0, "t_end": 5_000_000},
           {"id": 3, "region": 0, "text": "今日はいい天気ですね", "box": [500, 850, 1400, 880], "t_start": 20_000_000, "t_end": 25_000_000},
           {"id": 4, "region": 0, "text": "ありがとう、助かったよ", "box": [500, 850, 1400, 880], "t_start": 40_000_000, "t_end": 45_000_000}]
    cues = [_NS(start=0.0, end=5.0, lines=["カチーナ", "第一行のせりふです"]),
            _NS(start=20.0, end=25.0, lines=["シュラム", "今日はいい天気ですね"]),
            _NS(start=40.0, end=45.0, lines=["カチーナ", "ありがとう、助かったよ"]),
            _NS(start=60.0, end=65.0, lines=["シュラム", "第一行のせりふです"]),
            _NS(start=80.0, end=85.0, lines=["シュラム", "ありがとう、助かったよ"]),
            _NS(start=100.0, end=105.0, lines=["カチーナ", "今日はいい天気ですね"])]     # 开头行要配过 ≥3 种正文
    evs_of = [[evs[0], evs[2]], [evs[1], evs[3]], [evs[0], evs[4]], [evs[1], evs[2]], [evs[1], evs[4]], [evs[0], evs[3]]]
    doc = {"events": evs, "size": [1920, 1080], "frame_us": 500_000, "provenance": {}}
    # シュラム 下面那句被认成带内台词（3 次里 1 次，过 `TERM_ON_DIALOGUE`）：它挂在台词上，才拿字形查短名词表
    talk = [{"start": 20.0, "end": 25.0, "cues": [1], "extra": [], "how": "exact",
             "ref": {"key": "k", "kind": "Talk", "jp": "今日はいい天気ですね", "cn": "今天天气真好", "score": 1.0}}]
    SM.mark_display(talk, cues, lambda c: "dialogue")
    items, st, _ = SM.overlay_items(talk, doc, evs_of, cues, ref=tref)
    nm = sorted((it["ocr"], it["cn"], it["name_src"]) for it in items if it["layer"] == "name")
    check("短名词·名牌层先查说话人表、查不到再查短名词表，条目记 name_src；stats 记 name_from_terms",
          SM.term_names(tref) == {SA.norm("カチーナ"): "卡琪娜", SA.norm("シュラム"): "施拉姆"}
          and nm == [("カチーナ", "卡齐娜", "speakers"), ("シュラム", "施拉姆", "terms")]
          and st["name_translated"] == 2 and st["name_from_terms"] == 1, (nm, st))
    mv = [{**evs[0], "boxes": [[0, [900, 600, 1000, 640]], [30_000_000, [900, 800, 1000, 840]]]}] + evs[1:]
    mv_of = [[mv[e["id"]] for e in g] for g in evs_of]
    _, st_mv, _ = SM.overlay_items(talk, {**doc, "events": mv}, mv_of, cues, ref=tref)
    check("名牌层 stats：在动的名牌切成几块时 `name` 数块，`name_events` / `name_translated` / `name_from_terms` 按事件去重（和切段之前的读数同口径）",
          (st_mv["name"], st_mv["name_events"], st_mv["name_translated"], st_mv["name_from_terms"]) == (3, 2, 2, 1), st_mv)
    items0, st0, _ = SM.overlay_items([], doc, evs_of, cues, ref=tref)
    nm0 = sorted((it["ocr"], it["cn"]) for it in items0 if it["layer"] == "name")
    check("短名词·下面没有一句被认成台词的开头行不查短名词表（任务目标、装备名这类 UI 不当名牌译）；说话人表照查；"
          "挡掉的按事件记进 stats 的 `name_terms_off`",
          nm0 == [("カチーナ", "卡齐娜"), ("シュラム", None)] and st0["name_terms_off"] == 1 and st["name_terms_off"] == 0,
          (nm0, st0["name_terms_off"], st["name_terms_off"]))
    # 名牌容错查（owner 2026-10-06）：没译出的块只吸附到本段已译出、只差一个字、唯一的名牌；两边都 ≥2 字，被吸附的那行要挂在台词上
    def nit(ocr, cn=None, src=None, ev=0):
        return {"layer": "name", "events": [ev], "ocr": ocr, "jp": ocr if cn else None, "cn": cn, "name_src": src}
    fz = [nit("カチーナ", "卡齐娜", "speakers", 1), nit("刃", "刃", "speakers", 2),
          nit("アキラ", "哲", "terms", 3), nit("アキト", "阿基特", "speakers", 4),
          nit("カチーノ", ev=10), nit("カチーノ", ev=10), nit("冷", ev=11), nit("アキロ", ev=12), nit("カチーラ", ev=13), nit("チーナ", ev=14)]
    sh = {SA.norm(x): 1.0 for x in ("カチーノ", "冷", "アキロ", "チーナ")} | {SA.norm("カチーラ"): 0.1}
    one = [nit("刃", "刃", "speakers", 1), nit("刃x", ev=10)]
    SM.fuzzy_names(one, {SA.norm("刃x"): 1.0})
    check("名牌容错查：已译出那一侧不足 2 字也不借（`刃` / `刃x` 只差一个字）", one[1]["cn"] is None, one[1])
    nfix = SM.fuzzy_names(fz, sh)
    got = [(it["ocr"], it["cn"], it["name_src"]) for it in fz[4:]]
    check("名牌容错查：差一个字（换 / 漏）、唯一邻居的借译文、记 name_src=fuzzy、jp 取被借的名牌；单字名、两个邻居、不挂在台词上的都不借",
          nfix == {"voted": 0, "fuzzy": 2} and got == [("カチーノ", "卡齐娜", "fuzzy"), ("カチーノ", "卡齐娜", "fuzzy"), ("冷", None, None),
                                                      ("アキロ", None, None), ("カチーラ", None, None), ("チーナ", "卡齐娜", "fuzzy")]
          and fz[4]["jp"] == "カチーナ", (nfix, got))
    # 名牌投票（owner 2026-10-24）：逐字查不到的读数，按它挂着的那几条记录的说话人投票；读数要能是那人名字的一截，≤2 字的只许少读、不许读错
    HIRO, EMA = ("二階堂ヒロ", "二阶堂希罗"), ("桜羽エマ", "樱羽艾玛")
    def tally(*who):
        return {ri: w for ri, w in enumerate(who)}
    vt = [nit("階堂ヒロ", ev=20), nit("階堂ヒ口", ev=21), nit("ヒロ", ev=22), nit("ヒカ", ev=23), nit("桜羽工マ", ev=24),
          nit("階堂巳口", ev=25), nit("ええ。", ev=26), nit("羽工マ", ev=27)]
    vv = {SA.norm("階堂ヒロ"): tally(HIRO, HIRO, HIRO), SA.norm("階堂ヒ口"): tally(HIRO, HIRO, HIRO, EMA),
          SA.norm("ヒロ"): tally(HIRO, HIRO, HIRO), SA.norm("ヒカ"): tally(*[HIRO] * 5),
          SA.norm("桜羽工マ"): tally(EMA, EMA), SA.norm("階堂巳口"): tally(HIRO, HIRO, HIRO),
          SA.norm("ええ。"): tally(EMA, EMA, EMA, EMA), SA.norm("羽工マ"): tally(EMA, EMA, EMA, HIRO, HIRO)}
    nv = SM.fuzzy_names(vt, {}, vv)
    got = [(it["ocr"], it["cn"], it["name_src"]) for it in vt]
    check("名牌投票：少读一截、少读又错一个字形的、2 字少读的按说话人认上（name_src=voted，jp 取说话人的名字）；"
          "2 字读错一个（短名字错配）、只有 2 票、错两处、字面不是名字、票数占比不到 2/3 的都不认",
          nv == {"voted": 3, "fuzzy": 0}
          and got == [("階堂ヒロ", "二阶堂希罗", "voted"), ("階堂ヒ口", "二阶堂希罗", "voted"), ("ヒロ", "二阶堂希罗", "voted"),
                      ("ヒカ", None, None), ("桜羽工マ", None, None), ("階堂巳口", None, None), ("ええ。", None, None), ("羽工マ", None, None)]
          and vt[0]["jp"] == "二階堂ヒロ", (nv, got))
    veto = [nit("カチーナ", "卡齐娜", "speakers", 1), nit("カチーノ", ev=10), nit("カチーラ", ev=11)]
    SM.fuzzy_names(veto, {SA.norm("カチーノ"): 1.0, SA.norm("カチーラ"): 1.0},
                   {SA.norm("カチーノ"): tally(HIRO, HIRO, HIRO), SA.norm("カチーラ"): tally(("カチーナ", "卡齐娜"), HIRO)})
    check("名牌投票：证据充分地指向另一个人时，不再借本段已译出的名牌（`カチーノ` 下面三句都是ヒロ说的）；票不够时照借",
          [(it["cn"], it["name_src"]) for it in veto[1:]] == [(None, None), ("卡齐娜", "fuzzy")], veto)
    check("名牌投票·字面：读数是名字的一截（两头没读到的不算差），差的处数按编辑距离算",
          textfold.fits_within("階堂ヒロ", "二階堂ヒロ", 0) and textfold.fits_within("ケ崎", "城ヶ崎ノア", 0) and textfold.fits_within("階堂ヒ口", "二階堂ヒロ", 1)
          and not textfold.fits_within("階堂ヒ口", "二階堂ヒロ", 0) and not textfold.fits_within("階堂巳口", "二階堂ヒロ", 1)
          and not textfold.fits_within("。", "二階堂ヒロ", 1))
    old = GA.Ref(doc={"units": [], "speakers": {}}, script=[], windows=[], role={}, variant_of={})
    check("短名词·旧剧本（没有 terms）照读：表为空、名牌层不出译文",
          SM.term_names(old) == {} and all(it["cn"] is None for it in SM.overlay_items([], doc, evs_of, cues, ref=old)[0]))


def t_name_roles() -> None:
    """名牌逐块分组（owner 2026-11-05）：在屏时正下方有认上的台词的留在 `name`，没有的归 `stray_name`（默认不画）；
    挂上的名牌里字小一号、叠在名字下面的是称号（层 `title`），称号挂上时名字也算挂上。"""
    print("\n## 名牌逐块分组")
    from flowocr.analyze import scriptmatch as SM
    from flowocr.analyze.matchers import naninovel as NN
    from flowocr.artifacts import matchedio as MI
    from flowocr.artifacts import tracksio
    from flowocr.output import script as SC
    S = 1_000_000

    def nm(box, a, b, ev):
        return {"layer": "name", "group": "name", "fill": False, "start_us": a * S, "end_us": b * S, "rows": [box],
                "events": [ev], "jp": "x", "cn": "x", "name_src": "speakers", "ocr": "x"}

    def line(a, b, top=850, layer="body", group="dialogue", echo=None):
        return {"layer": layer, "group": group, "start_us": a * S, "end_us": b * S, "rows": [[500, top, 1400, top + 30]], "echo": echo}
    name, title = [890, 757, 1028, 792], [930, 793, 1024, 823]      # 名字框高 35、称号 30
    names = [nm([900, 800, 1000, 840], 0, 5, 0), nm([900, 800, 1000, 840], 10, 15, 1), nm([900, 800, 1000, 840], 20, 25, 2),
             nm([900, 800, 1000, 840], 50, 55, 3), nm([900, 800, 1000, 840], 60, 65, 4),
             nm(name, 30, 35, 5), nm(title, 30, 35, 6), nm(name, 40, 45, 7), nm(title, 40, 45, 8),
             {**nm(name, 70, 75, 9), "cn": None}, nm(title, 70, 75, 10),
             nm(name, 80, 85, 11), nm([930, 722, 1024, 752], 80, 85, 12)]      # 称号在名字上面
    lines = [line(3, 8), line(20, 25, echo="log"), line(50, 55, layer="extra"), line(60, 65, group="offband"),
             line(30, 35, top=905), line(70, 75), line(80, 85, top=870)]
    # 30 s 那对称号在下：称号下沿到台词 82 ≤ 3×30、名字 113 > 3×35；80 s 那对称号在上：名字 78 ≤ 3×35、称号 118 > 3×30——都只有一块直接挂上
    assert not tracksio.name_over_body(name, lines[4]["rows"][0]) and tracksio.name_over_body(title, lines[4]["rows"][0])
    assert tracksio.name_over_body(name, lines[6]["rows"][0]) and not tracksio.name_over_body([930, 722, 1024, 752], lines[6]["rows"][0])
    n = SM.name_roles(names, lines)
    got = [(it["group"], it["layer"]) for it in names]
    check("名牌分组：在屏时正下方有认上的台词（正文 / extra、组 dialogue）的留在 name；没有台词、台词是对话记录里的旧行（echo=log）、"
          "台词在带外（offband）的归 stray_name",
          got[:5] == [("name", "name"), ("stray_name", "name"), ("stray_name", "name"), ("name", "name"), ("stray_name", "name")], got)
    check("称号：挂上的名牌里叠在有译文的名字下面（或上面）、字小一号的层改成 title；一对里一块挂上、离台词远的那块也算挂上"
          "（称号在下时是名字，在上时是称号）；没挂上的一对都留在 name 层；上面那块没有译文（绝区零的招牌「QICAI JIE」）时不算称号",
          got[5:] == [("name", "name"), ("name", "title"), ("stray_name", "name"), ("stray_name", "name"), ("name", "name"), ("name", "name"),
                      ("name", "name"), ("name", "title")]
          and n == {"name": 6, "title": 2, "stray_name": 5}, (got[5:], n))
    tie = tracksio.stack_partners([(0, 10, name), (0, 10, title), (0, 10, name), (0, 10, [890, 757, 1028, 789]), None])
    check("称号认名字：重叠一样久取下标小的；字号相同的两行不互认；不参加的是 None", tie == [None, 0, None, None, None], tie)
    check("显示声明：两个内置匹配器都声明 name（默认画）和 stray_name（默认不画）",
          all(D["groups"]["name"]["tier"] == "on" and D["groups"][MI.STRAY_NAME]["tier"] == "off" for D in (SM.DISPLAY, NN.DISPLAY)))
    check("字幕稿：称号层投成 title 条目、样式 Title，名字是 Name；阶段 4 把 Title 当名牌排",
          SC.style_of(SC.Item(0, 1, [title], ["佣兵"], "title")) == "Title" and "Title" in SC.STYLES
          and SC.style_of(SC.Item(0, 1, [name], ["施拉姆"], "name")) == "Name")
    body = SC.Item(0, 5 * S, [[500, 850, 1400, 880]], ["台詞"], "line")
    tr = SC.mark_names([SC.Item(0, 5 * S, [name], ["シュラム"], "name"), SC.Item(0, 5 * S, [title], ["傭兵"], "name"),
                         SC.Item(0, 5 * S, [title], ["傭兵"], "name", ui=True), body,
                         SC.Item(10 * S, 15 * S, [name], ["冒険ランク"], "name"), SC.Item(10 * S, 15 * S, [title], ["45"], "name"),
                         SC.Item(20 * S, 25 * S, [name], ["シュラム"], "name"), SC.Item(20 * S, 25 * S, [title], ["傭兵"], "name"),
                         SC.Item(20 * S, 25 * S, [[500, 850, 1400, 880]], ["台詞"], "line", main=False)])
    check("tracks 的名牌分档：正下方同时有主轨句子的留 name、叠在它下面字小一号的改 title；下面没有句子、句子不在主轨上的改 stray_name"
          "（样式 StrayName）；UI、正文不动",
          [it.kind for it in tr] == ["name", "title", "name", "line", "stray_name", "stray_name", "stray_name", "stray_name", "line"]
          and SC.style_of(tr[4]) == "StrayName", [it.kind for it in tr])
    gap = [SC.Item(0, 5 * S, [[910, 788, 1004, 818]], ["リン"], "name"), SC.Item(0, 5 * S, [[560, 883, 1340, 917]], ["台詞"], "line")]
    check("绝区零：名牌下沿到台词上沿 65 px、名牌字高 30（2.2 个字高）也算挂上（`NAME_GAP`），隔 95 px（3.2 个字高）的不算",
          SC.mark_names(gap)[0].kind == "name" and not tracksio.name_over_body([910, 788, 1004, 818], [560, 913, 1340, 947]))
    # 绝区零：台词左对齐、名牌居中，首行短时首行够不到名牌下面，按整块的左右比（`block_head`）
    zname, zrows = [471, 402, 507, 417], [[304, 455, 469, 470], [303, 472, 672, 491]]
    zn = [nm(zname, 0, 5, 0)]
    SM.name_roles(zn, [{"layer": "body", "group": "dialogue", "start_us": 0, "end_us": 5 * S, "rows": zrows, "echo": None}])
    zt = SC.mark_names([SC.Item(0, 5 * S, [zname], ["ラミル"], "name"), SC.Item(0, 5 * S, zrows, ["こんばんは、リンちゃん。", "x"], "line")])
    check("首行短的台词：名牌按整块台词的左右比（首行够不到名牌正下方也算挂上），匹配器和 tracks 两条路一样",
          not tracksio.name_over_body(zname, zrows[0]) and tracksio.name_over_body(zname, tracksio.block_head(zrows))
          and zn[0]["group"] == "name" and zt[0].kind == "name", (zn[0]["group"], zt[0].kind))
    # 匹配器认开头行时（同一条 cue 的事件还没成块）：从这一行往下连着的几行凑成台词块，同屏右下角的按钮不算进左右
    button = [1400, 470, 1600, 500]
    cue_rows = [zrows[0], button, zrows[1]]
    far = [1500, 402, 1560, 417]
    check("凑台词块（`rows_below`）：首行短、下一行长连成一块，名牌照样挂上；同一条 cue 里右下角的按钮不连进来、不把左右撑到它下面的空处",
          tracksio.rows_below(cue_rows, zrows[0]) == zrows
          and tracksio.name_over_body(zname, tracksio.block_head(tracksio.rows_below(cue_rows, zrows[0]), zrows[0]))
          and tracksio.name_over_body(far, tracksio.block_head(cue_rows, zrows[0]))
          and not any(tracksio.name_over_body(far, tracksio.block_head(tracksio.rows_below(cue_rows, b), b)) for b in cue_rows),
          tracksio.rows_below(cue_rows, zrows[0]))
    split = [470, 456, 520, 471]                 # 首行被 OCR 切成两个框，后一个框比前一个低 1 px
    check("凑台词块：同一行被切开的后一个框不算成下一行（上沿没低过已收那几行下沿的一半字高），下一行照样连上",
          tracksio.rows_below([zrows[0], split, zrows[1]], zrows[0]) == zrows, tracksio.rows_below([zrows[0], split, zrows[1]], zrows[0]))
    # 连串只看和串里最晚终点的间隔：一路每段都不超过 NAME_INERTIA_US 就一直接下去，重叠的也接上
    g = tracksio.NAME_INERTIA_US // S
    box = [930, 790, 1024, 820]
    chain = [(a * S, b * S, box, "k") for a, b in ((0, 5), (5 + 0.75 * g, 6 + 0.75 * g), (6 + 1.5 * g, 7 + 1.5 * g),
                                                     (3, 6), (7 + 2.75 * g, 8 + 2.75 * g))]
    check("时间惯性连串：每段断开都不超过 NAME_INERTIA_US 的一路接下去（离第一块已经远过上限也接）、时间重叠的接上；断开超过的另起一串",
          tracksio.plate_runs(chain) == [0, 0, 0, 0, 4], tracksio.plate_runs(chain))
    # 时间惯性：同一块名牌（同名、同一处）断开不超过 NAME_INERTIA_US 的连成一串，一串里有一块挂上整串都算
    run = [nm(name, 0, 5, 0), nm(name, 5 + g, 6 + g, 1), nm([890, 600, 1028, 635], 6, 7, 2),
           {**nm(name, 8 + 2 * g, 9 + 2 * g, 3)}, {**nm(name, 5.5, 6.5, 4), "cn": "y"}]
    SM.name_roles(run, [line(0, 5)])
    check("时间惯性：同名、同一处、断开不超过 NAME_INERTIA_US 的名牌块跟着挂上的那块算挂上；换了位置、断开更久、名字不同的不算",
          [it["group"] for it in run] == ["name", "name", "stray_name", "stray_name", "stray_name"], [it["group"] for it in run])
    from flowocr.artifacts.matchedio import Visible as _Vis
    tdoc = {"events": [{"id": 0, "region": 0, "box": name, "text": "x", "t_start": 0, "t_end": S}], "provenance": {}}
    proj = SC.project_items([{**nm(name, 0, 1, 0), "group": MI.STRAY_NAME}, nm(name, 0, 1, 0)], tdoc, "cn",
                            _Vis(frozenset({"name", MI.STRAY_NAME}), True))
    check("匹配产物打开 stray_name 组时，这一组的名牌用 StrayName 样式，挂上的照旧是 Name",
          [SC.style_of(it) for it in proj] == ["StrayName", "Name"], [SC.style_of(it) for it in proj])
    # 退回画 OCR 原文的名牌（没译出、`unmatched` 可见）：事件 1 挂上、2 没挂上、4 是称号、3 不在名牌层；
    # 5 / 6 是没带建轨 `nameplate` 标的名牌（挂上 / 没挂上），7 是不在名牌层、没带标的杂字；8 是挂上、有译文的名牌（照画译文）
    pl = ["nameplate"]
    oevs = [{"id": i, "region": 0, "box": bx, "t_start": t * S, "t_end": (t + 5) * S, "text": tx, "flags": fl, "conf": 0.9, "n_obs": 10}
            for i, bx, t, tx, fl in ((0, [500, 850, 1400, 880], 0, "せりふ", []), (1, name, 0, "アンピー", pl), (2, name, 10, "ボタン", pl),
                                     (3, name, 20, "ミニ", pl), (4, title, 0, "傭兵", pl),
                                     (5, name, 30, "リン", []), (6, name, 40, "#1", []), (7, name, 60, "ざつおん", []), (8, name, 70, "アキラ", pl))]
    odoc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
            "provenance": {"main_track": "r00"}, "events": oevs, "regions": [{"index": 0, "align": "center", "align_how": {"by": "none"}}],
            "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "t-r00.srt", "ui_lines": [],
                        "cues": [{"id": f"c{e['id']}", "t_start": e["t_start"], "t_end": e["t_end"], "events": [e["id"]]} for e in oevs]}]}
    ref = {"key": "k0", "jp": "台詞", "cn": "台词", "group": "dialogue", "fill": False}
    blank = {"jp": None, "cn": None, "name_src": None}
    oitems = [{"layer": "body", "start_us": 0, "end_us": 5 * S, "rows": [oevs[0]["box"]], "events": [0], **ref, "claim": [0, -1],
               "echo": None, "echo_of": None},
              {**nm(name, 0, 5, 1), **blank}, {**nm(name, 10, 15, 2), **blank, "group": MI.STRAY_NAME},
              {**nm(title, 0, 5, 4), **blank, "layer": "title"}, {**nm(name, 30, 35, 5), **blank},
              {**nm(name, 40, 45, 6), **blank, "group": MI.STRAY_NAME}, nm(name, 70, 75, 8)]
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "t-tracks.json"
        tracksio.dump(tp, odoc)
        om = {"schema": MI.SCHEMA, "provenance": {"subs": str(tp)}, "stats": {}, "display": SM.DISPLAY,
              "cues": [{"start": 0.0, "end": 5.0, "ocr": "せりふ", "ref": ref, "extra": [], "echo": None, "echo_of": None}],
              "overlay": {"items": oitems, "stats": {}, "fed": list(range(9))}}
        built = SC.build_items(om, {"lang": "cn", "show": "all"}, align=False)[0]
        got = {it.events[0]["id"]: SC.style_of(it) for it in built if not it.translated}
        got[8] = [SC.style_of(it) for it in built if it.translated and it.kind != "line"]
    check("匹配产物退回画 OCR 原文的名牌一律 StrayName（Name / Title 只给认出了译名的）：名牌层的事件不论挂没挂上、带不带建轨的名牌标，"
          "不在名牌层但带名牌标的也是；不在名牌层、没带标的照旧 Body",
          got == {1: "StrayName", 2: "StrayName", 3: "StrayName", 4: "StrayName", 5: "StrayName", 6: "StrayName", 7: "Body", 8: ["Name"]}, got)


NANI_A = """; 日本語 <ja> to 简体中文 <zh-Hans> localization document for 'Act/A01' Naninovel scenario script

# A01_Narrative001
; ――ここは<ruby="ろうごく">牢獄</ruby>だ。
——这里是囚牢。

# A01_Ema001
; > Ema: |#A01_Ema001|
; え、な、なに、ここ……？<br>どうなってるの！？
咦，怎么回事？<br>
这是怎么了！？

# A01_Unknown001
; > ＠遠野ハンナ
; > Unknown: |#A01_Unknown001|
; 何を考えていますの！？
你在想什么！？

# A01_Choice001
; > @choice |#A01_Choice001| button:ChoiceButtons/Adv/Bad
; 牢屋を調べる
调查牢房

# A01_Hiro001
; > Hiro: |#A01_Hiro001|
; <color=#ff0000>静かに</color>してくれないか？
能安静点吗？

# A01_Narrative002
; エマは鉄格子に縋りついて叫んだ。
艾玛抓着铁栏大喊。

# A01_Ema002
; > Ema: |#A01_Ema002|
; ねえ、ここどこなのかな！？
喂，这里是哪里！？
"""
NANI_B = """# B01_Ema001
; > Ema: |#B01_Ema001|
; それはきっと、この証拠品で証明できると思うよ！
一定能用这件证据证明！

# B01_Coco001
; > Coco: |#B01_Coco001|
; そんな証拠、どこにもないじゃん！
哪有那种证据！

# B01_Ema002
; > Ema: |#B01_Ema002|
; それはきっと、この証拠品で証明できると思うよ！
一定能用这件证据证明！

# B01_Coco002
; > Coco: |#B01_Coco002|
; だったら見せてみなよ！
那你拿出来看看！
"""
def t_name_ink() -> None:
    """名牌字色（`analyze.nameink`）：同一种名牌先量 3 块、不一致加到 9 块按合标占比定；阶段 3 写名牌色、不上色的用 PlainName。"""
    import contextlib
    import io

    import numpy as np
    from flowocr.analyze import nameink as NI
    from flowocr.analyze import scriptmatch as SM
    from flowocr.artifacts import matchedio as MI
    from flowocr.artifacts import tracksio
    from flowocr.output import script as SC
    from flowocr.typeset import core as TC
    S = 1_000_000
    gold = NI.INK["genshin"]["name"]
    rgb_of = lambda lab: [int(NI.to_hex(lab)[k:k + 2], 16) for k in (1, 3, 5)]   # noqa: E731
    check("名牌色 Lab → #rrggbb → Lab 来回差不到 1",
          all(float(np.linalg.norm(NI.to_lab(np.array(rgb_of(lab), np.uint8)) - np.array(lab))) < 1.0
              for g in NI.INK.values() for lab in g.values()))
    box = [900, 800, 1000, 840]

    def frame(color, bg=(30, 32, 40)):
        img = np.full((1080, 1920, 3), bg, np.uint8)
        for x in range(906, 996, 8):                  # 3 px 宽的竖笔画
            img[806:834, x:x + 3] = color
        return img
    W = [235, 240, 245]
    dimmed = lambda lab: frame([v // 4 for v in rgb_of(lab)], (8, 8, 10))   # noqa: E731  弹窗把整屏压暗：名牌字还在，只是暗
    colors: dict[int, object] = {}
    asked: list[int] = []

    def fake_grab(video, t_us, w, h):
        asked.append(t_us)
        return colors[t_us]
    items = []

    top = [900, 40, 1000, 80]                         # 画面顶上别的界面里的同一个名字

    def kind(cn, pattern, layer="name", group="name", base=0, at=box):
        lab = NI.INK["genshin"][layer]
        for k, c in enumerate(pattern):
            a = (base + 10 * k) * S
            img = {"w": frame(W), "g": frame(rgb_of(lab)), "d": dimmed(lab), "n": None}[c]
            if img is not None and at is not box:
                img = np.roll(img, at[1] - box[1], axis=0)
            colors[a + S // 2] = img
            items.append({"layer": layer, "group": group, "cn": cn, "start_us": a, "end_us": a + S, "rows": [at]})
    kind("派蒙", "ggggg", base=0)
    kind("眼", "wwww", base=1000)
    kind("卡齐娜", "gggggg" + "w" + "ggggg", base=2000)         # 先挑的 0 / 6 / 11 里有一块白：加到 9 块，白的 1 / 9
    kind("阿米娜", "gwgwggwggggg", base=3000)                   # 9 块里白的 3 块，合标 6 / 9 < 0.8
    kind("佣兵", "ggg", layer="title", base=4000)
    kind("派蒙", "gg", group="stray_name", base=5000)
    kind("拉马奈", "gggdddggg", base=7000)                      # 先挑的 0 / 4 / 8 里有一块压暗：加到 9 块，压暗的 3 块不算，合标 6 / 6
    kind("纳西妲", "nnn", base=8000)                            # 一帧都没取到：没有证据，不上色
    kind("大卫", "ggggg", base=9000)                            # 同一个名字两处：常规位置的照名牌色判，顶上界面里的另判
    kind("大卫", "ww", base=9500, at=top)
    items.append({"layer": "name", "group": "name", "cn": None, "start_us": 6000 * S, "end_us": 6001 * S, "rows": [box]})
    log = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        vid = Path(td) / "v.mp4"
        vid.write_bytes(b"")
        doc = {"size": [1920, 1080], "provenance": {"video": str(vid)}}
        saved, saved_which = NI.grab, NI.shutil.which
        NI.grab, NI.shutil.which = fake_grab, lambda name: name
        try:
            with contextlib.redirect_stdout(log):
                got = NI.check(items, doc, "genshin")
                none_game = NI.check([dict(it) for it in items], doc, "zzz")
                none_vid = NI.check([dict(it) for it in items], {**doc, "provenance": {"video": str(Path(td) / "nope.mp4")}}, "genshin")
                NI.shutil.which = lambda name: None
                none_ff = NI.check([dict(it) for it in items], doc, "genshin")
        finally:
            NI.grab, NI.shutil.which = saved, saved_which
    verdict = {}
    for it in items:
        key = (it["cn"], it["layer"], it["group"]) + ((it["rows"][0][1],) if it["cn"] == "大卫" else ())
        verdict.setdefault(key, set()).add(it.get("ink", "-"))
    check("名牌字色：都合标的上色、都不合标的不上色；3 块不一致时加到 9 块，合标 ≥ 80% 才上色，压暗的块不计；一帧都没取到的不上色；同一个名字在不同位置分开判；没挂上的、没译名的不判",
          verdict == {("派蒙", "name", "name"): {True}, ("眼", "name", "name"): {False}, ("卡齐娜", "name", "name"): {True},
                      ("阿米娜", "name", "name"): {False}, ("佣兵", "title", "name"): {True},
                      ("派蒙", "name", "stray_name"): {"-"}, (None, "name", "name"): {"-"},
                      ("拉马奈", "name", "name"): {True}, ("纳西妲", "name", "name"): {False},
                      ("大卫", "name", "name", 800): {True}, ("大卫", "name", "name", 40): {False}}, verdict)
    check("名牌字色：先每种 3 块（不到 3 块全量），不一致的（含没有证据的块）加量到 9 块；共量 3+3+9+9+3+9+3+3+2 帧，日志分开报没取到帧、画面压暗",
          len(asked) == 44 and len(set(asked)) == 44 and "没取到帧 3、画面压暗（或框在画面外）3" in log.getvalue(), (len(asked), log.getvalue()))
    check("名牌字色：产物记各层的名牌色；没有名牌色的游戏（绝区零）、找不到源视频、PATH 上没有 ffmpeg 时什么都不判",
          got == {"name": NI.to_hex(gold), "title": NI.to_hex(NI.INK["genshin"]["title"])} and none_game is None and none_vid is None
          and none_ff is None and "没有 ffmpeg" in log.getvalue(), got)
    plate, lifted, up_top = [886, 847, 1028, 892], [877, 784, 1027, 834], [1074, 81, 1214, 109]   # gi2「派蒙」、hsr「ヨク・タベル」
    check("名牌的位置（`same_spot`）：对话框里随台词行数上挪 1.4 个字高的还是一处（`same_plate` 不算同一块），画面顶上别的界面另算一处",
          NI.same_spot(plate, lifted) and not tracksio.same_plate(plate, lifted) and not NI.same_spot([762, 794, 1074, 842], up_top))
    gold_bg = np.full((1080, 1920, 3), rgb_of(gold), np.uint8)
    gold_bg[806:834, 906:996:8] = W                                # 金色背景上的白字：背景本身是名牌色
    check("量字色：框里名牌色笔画的占比减去框外一圈的——金字过、白字不过、金色背景上的白字也不过；整屏压暗的、框在画面外的没有证据（None）",
          NI.ink_share(frame(rgb_of(gold)), box, gold) >= NI.MIN_SHARE > NI.ink_share(frame(W), box, gold)
          and NI.ink_share(gold_bg, box, gold) < NI.MIN_SHARE and NI.ink_share(dimmed(gold), box, gold) is None
          and NI.ink_share(frame(W), [2000, 10, 2100, 40], gold) is None,
          [NI.ink_share(f, box, gold) for f in (frame(rgb_of(gold)), frame(W), gold_bg, dimmed(gold))])
    # 阶段 3：判过字色的名牌，不合标的另用 PlainName；Name / Title 样式写名牌色；阶段 4 照名牌排 PlainName
    tdoc = {"events": [{"id": 0, "region": 0, "box": box, "text": "x", "t_start": 0, "t_end": S}], "provenance": {}}
    from flowocr.artifacts.matchedio import Visible as _Vis
    mk = lambda **kw: {"layer": "name", "group": "name", "start_us": 0, "end_us": S, "rows": [box], "events": [0],  # noqa: E731
                       "jp": "x", "cn": "x", **kw}
    proj = SC.project_items([mk(ink=True), mk(ink=False), mk(), mk(layer="title", ink=False)], tdoc, "cn", _Vis(frozenset({"name"}), True))
    fills = SC.name_fills({"overlay": {"name_ink": {"name": "#f6c510", "title": "#cfac2c"}}})
    sty = {ln.split(",")[0][7:]: ln.split(",")[3] for ln in SC.style_lines(fills=fills)}
    check("阶段 3：ink 为 False 的名牌（名字、称号）用 PlainName（白），其余照层；Name / Title 样式写名牌色（&H00BBGGRR），别的样式白；阶段 4 按名牌排 PlainName",
          [SC.style_of(it) for it in proj] == ["Name", "PlainName", "Name", "PlainName"]
          and sty["Name"] == "&H0010C5F6" and sty["Title"] == "&H002CACCF" and sty["PlainName"] == SC.STYLE_FILL
          and sty["Body"] == SC.STYLE_FILL and SC.name_fills({"overlay": {}}) == {} and TC.KIND_OF_STYLE["PlainName"] == "name",
          ([SC.style_of(it) for it in proj], sty))
    # 接线：匹配产物的 overlay.name_ink 经 render_script 写进字幕稿的样式头，ink 为 False 的那块用 PlainName
    evs = [{"id": i, "region": 0, "box": box, "t_start": a * S, "t_end": (a + 2) * S, "text": "パイモン", "flags": ["nameplate"],
            "conf": 0.9, "n_obs": 10} for i, a in ((0, 0), (1, 10))]
    rdoc = {"schema": tracksio.SCHEMA, "tag": "t", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
            "provenance": {"main_track": "r00"}, "events": evs, "regions": [{"index": 0, "align": "center", "align_how": {"by": "none"}}],
            "tracks": [{"id": "r00", "kind": "region", "region": 0, "srt": "t-r00.srt", "ui_lines": [],
                        "cues": [{"id": f"c{e['id']}", "t_start": e["t_start"], "t_end": e["t_end"], "events": [e["id"]]} for e in evs]}]}
    plate = {"fill": False, "name_src": "speakers", "ocr": "パイモン"}
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "t-tracks.json"
        tracksio.dump(tp, rdoc)
        om = {"schema": MI.SCHEMA, "provenance": {"subs": str(tp)}, "stats": {}, "display": SM.DISPLAY, "cues": [],
              "overlay": {"items": [{**mk(ink=True), "start_us": 0, "end_us": 2 * S, "events": [0], "cn": "派蒙", **plate},
                                    {**mk(ink=False), "start_us": 10 * S, "end_us": 12 * S, "events": [1], "cn": "派蒙", **plate}],
                          "stats": {}, "fed": [0, 1], "name_ink": {"name": "#f6c510"}, "textbox": {}}}
        with contextlib.redirect_stdout(io.StringIO()):
            p, *_ = SC.render_script(om, td, {"lang": "cn", "show": "all"})
        ass = Path(p).read_text(encoding="utf-8-sig").splitlines()
    head = {ln.split(",")[0][7:]: ln.split(",")[3] for ln in ass if ln.startswith("Style: ")}
    used = [ln.split(",")[3] for ln in ass if ln.startswith("Dialogue: ")]
    check("接线：render_script 把匹配产物的 overlay.name_ink 写进字幕稿的 Name 样式，不上色的那块用白字的 PlainName",
          head.get("Name") == "&H0010C5F6" and head.get("PlainName") == SC.STYLE_FILL and used == ["Name", "PlainName"], (head, used))


NANI_NAMES = """; 日本語 <ja> to 简体中文 <zh-Hans> localization document for 'CharacterNames' managed text document

; 桜羽エマ
Ema: 樱羽艾玛

; 二階堂ヒロ
Hiro: 二阶堂希罗

; 沢渡ココ
Coco: 泽渡可可

; 遠野ハンナ
Hanna: 远野汉娜

; ？？？
Unknown: ? ? ?
"""


def t_naninovel() -> None:
    """内置匹配器 naninovel（顺序剧本，《魔裁》）：剧本读法、说话人、对 match_ref 改的几处判据、产物契约。

    存在的理由：这几处每一处都是 owner 的 match_ref 在 input1 上摆帧摆出来的错（matcher 计划 / try-list P1）——
    上下文加分把乱码挂到下一句的前一行、打字机中途挂到上一行、各分支共用的台词挂到没走的那一支；改回去不会有别的地方报错。"""
    print("naninovel 匹配器")
    import json as _json
    from flowocr import extensions as X
    from flowocr.analyze.matchers import naninovel as NV
    from flowocr.artifacts import matchedio, tracksio

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "Scripts" / "Act").mkdir(parents=True)
        (d / "Scripts" / "Act" / "A01.bytes").write_text(NANI_A, encoding="utf-8")
        (d / "Scripts" / "Act" / "B01.bytes").write_text(NANI_B, encoding="utf-8")
        (d / "CharacterNames.bytes").write_text(NANI_NAMES, encoding="utf-8")
        lines = NV.load_script(d / "Scripts", NV.load_names(d / "CharacterNames.bytes"))
        by = {ln.key: ln for ln in lines}
        check("剧本：Unity 富文本剥掉（ruby 留本字、color 去掉），<br> 记成 \\N，中文多行拼起来；@choice 不是字幕带",
              by["A01_Narrative001"].jp == "――ここは牢獄だ。" and by["A01_Hiro001"].jp == "静かにしてくれないか？"
              and by["A01_Ema001"].jp == "え、な、なに、ここ……？\\Nどうなってるの！？"
              and by["A01_Ema001"].cn == "咦，怎么回事？\\N这是怎么了！？"
              and not by["A01_Choice001"].band and all(ln.band for k, ln in by.items() if k != "A01_Choice001"),
              [(ln.key, ln.jp, ln.cn, ln.band) for ln in lines][:5])
        check("剧本：Ruby 留在带样式的那份里（规范写法 <ruby=读音>），去掉标签就是显示文本",
              by["A01_Narrative001"].jp_rich == "――ここは<ruby=ろうごく>牢獄</ruby>だ。"
              and NV.richtext.plain(by["A01_Narrative001"].jp_rich) == by["A01_Narrative001"].jp, by["A01_Narrative001"].jp_rich)
        check("说话人：名牌 id 查名字表；旁白 None；名牌藏成 ？？？ 的行记脚本注记的真名",
              (by["A01_Ema001"].speaker, by["A01_Ema001"].speaker_cn) == ("桜羽エマ", "樱羽艾玛")
              and by["A01_Narrative001"].speaker is None
              and (by["A01_Unknown001"].speaker, by["A01_Unknown001"].speaker_cn) == ("遠野ハンナ", "远野汉娜"))
        check("归一：标签、标点、全半角、小书写假名都不算差别",
              NV.norm("{\\c&H0000ff&}え、な……<b>ぁ</b>！？ＡＢ\\N") == NV.norm("えなあAB"))
        # 两个二元组各撞两行、倒排表交错（あい -> 0, 2；いう -> 1, 3）、共有数全是 1：按集合顺序取的话，
        # 不管哪个二元组先数，前 3 个都不是 [0, 1, 2]（[0, 2, 1] 或 [1, 3, 0]），改回去必红
        check("全局搜索的候选：并列按下标排，同一份输入每次跑都一样",
              NV.Index(["あいx", "いうy", "あいz", "いうw"]).candidates("あいう", top=3) == [0, 1, 2])

        texts = [(0, 3, ["ここは牢獄だ。"]),
                 (3, 3.5, ["え、な"]),                                   # 打字机中途：属于下一行
                 (3.5, 6, ["え、な、なに、ここ……?", "どうなってるの！?"]),
                 (6, 7, ["証拠品リスト"]),                               # 乱码 / 别的画面：不许靠后文挂到哪一行上
                 (7, 10, ["何を考えていますの！？"]),
                 (10, 13, ["静かにしてくれないか？"]),
                 (13, 16, ["エマは鉄格子に縋りついて叫んだ。"]),
                 (16, 19, ["ねえ、ここどこなのかな！？"]),
                 (25, 28, ["ねえ、ここどこなのかな！？"]),                # 同一行隔了 6 s 再显示一次
                 (30, 33, ["それはきっと、この証拠品で証明できると思うよ！"]),  # 两支里都有这句，后文定是第二支
                 (33, 36, ["だったら見せてみなよ！"])]
        evs, cues = [], []
        for k, (a, b, ls) in enumerate(texts):
            ids = []
            for r, t in enumerate(ls):
                ids.append(len(evs))
                evs.append({"id": len(evs), "region": 0, "t_start": int((a + (2 if k == 7 else 0)) * 1e6), "t_end": int(b * 1e6),
                            "text": t, "flags": [], "box": [400, 850 + 40 * r, 1500, 880 + 40 * r]})
            cues.append({"id": f"c{k}", "t_start": int(a * 1e6), "t_end": int(b * 1e6), "events": ids})
        doc = {"meta": {}, "frame_us": 500_000, "size": [1920, 1080],
               "provenance": {"main_track": "r00", "main_srt": "n-region00-subtitle.srt"}, "regions": [{"index": 0}],
               "events": evs, "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                                          "srt": "n-region00-subtitle.srt", "cues": cues}]}
        tp = d / "n-tracks.json"
        doc = tracksio.dump(tp, doc)
        m = X.load("naninovel", "matcher")
        out = m.match(doc, {"script": str(d / "Scripts"), "tracks_path": str(tp)}, {})
        matchedio.validate(out, need_overlay=True)
        recs = out["cues"]
        keyed = [(r["cues"], (r["ref"] or {}).get("key")) for r in recs]
        check("内置名单里有 naninovel；产物过 matchedio 校验（带叠加层）", "naninovel" in X.builtin_names("matcher"))
        check("打字机中途的半截认成它自己那一行、和打完的那条并成一条记录（原版挂到上一行）；乱码不认（原版靠后文挂到下一句的前一行）",
              keyed[:3] == [([0], "A01_Narrative001"), ([1, 2], "A01_Ema001"), ([3], None)], keyed)
        check("带标签的行配得上；隐藏名字的行记真名；旁白的说话人是 None",
              keyed[4:6] == [([5], "A01_Hiro001"), ([6], "A01_Narrative002")]
              and recs[3]["ref"]["speaker"] == "遠野ハンナ" and recs[5]["ref"]["speaker"] is None
              and recs[4]["ref"]["speaker"] == "二階堂ヒロ" and recs[4]["how"] == "exact", keyed)
        check("同一行隔了 merge_gap 以上再显示：两条记录（时间各是各的），不并成一段",
              keyed[6:8] == [([7], "A01_Ema002"), ([8], "A01_Ema002")] and recs[7]["start"] == 25.0, keyed)
        check("两支里都有的同一句：取后文对得上的那一支（同分时后文优先于离游标近）",
              keyed[8:] == [([9], "B01_Ema002"), ([10], "B01_Coco002")], keyed)
        check("记录的起点取 cue 里字最早出现的时刻（cue 起点和字之间的空档不算这一句的）",
              recs[6]["start"] == 18.0 and recs[6]["end"] == 19.0, recs[6])
        check("stats：说话人按角色 / 旁白计数；provenance 记剧本与参数",
              out["stats"]["speaker"] == {"narration": 2, "character": 7}
              and out["provenance"]["params"]["merge_gap"] == NV.MERGE_GAP, (out["stats"], out["provenance"]["params"]))
        got = X.load("matched_srt", "preset").render(out, d / "srt", {"lang": "cn"})
        body = got[0].read_text(encoding="utf-8")
        check("阶段 3 的 matched_srt 吃得下：认上的出译文，没认上的留 OCR",
              "这里是哪里" in body and "証拠品リスト" in body and "你在想什么" in body)
        import copy
        from flowocr.artifacts import srtio
        moved = copy.deepcopy(out)
        b0 = next(it for it in moved["overlay"]["items"] if it["layer"] == "body" and it["claim"] == [0, -1])
        b0["start_us"] += 700_000
        b0["end_us"] -= 300_000
        first = srtio.read_srt(X.load("matched_srt", "preset").render(moved, d / "srt2", {"lang": "cn"})[0])[0]
        check("matched_srt 的起止取这条记录正文叠加条目的（被名牌等拉早的起点、拖晚的终点已在叠加层改对）",
              abs(first.start - b0["start_us"] / 1e6) < 0.002 and abs(first.end - b0["end_us"] / 1e6) < 0.002,
              (first.start, first.end, b0["start_us"], b0["end_us"]))
        later = dict(b0, start_us=b0["start_us"] + 2_000_000)                # 同一条记录换了位置的第 2 段正文
        moved["overlay"]["items"].append(later)
        first = srtio.read_srt(X.load("matched_srt", "preset").render(moved, d / "srt4", {"lang": "cn"})[0])[0]
        check("matched_srt：一条记录有好几段正文时起点取最早那段的",
              abs(first.start - b0["start_us"] / 1e6) < 0.002, (first.start, b0["start_us"]))
        moved["overlay"]["items"].remove(later)
        bare_doc = {k: v for k, v in moved.items() if k != "overlay"}
        first = srtio.read_srt(X.load("matched_srt", "preset").render(bare_doc, d / "srt3", {"lang": "cn"})[0])[0]
        check("没有叠加层的匹配产物（--no-overlay、naninovel 关掉 overlay）照出 SRT，起止取记录的",
              abs(first.start - recs[0]["start"]) < 0.002 and abs(first.end - recs[0]["end"]) < 0.002, (first.start, first.end))
        nobody = copy.deepcopy(moved)
        nobody["overlay"]["items"] = [it for it in nobody["overlay"]["items"] if not (it["layer"] == "body" and it["claim"] == [0, -1])]
        nb = srtio.read_srt(X.load("matched_srt", "preset").render(nobody, d / "srt5", {"lang": "cn"})[0])[0]
        check("matched_srt：有叠加层、但这条记录没有正文条目时，起止退回记录的",
              abs(nb.start - recs[0]["start"]) < 0.002 and abs(nb.end - recs[0]["end"]) < 0.002, (nb.start, nb.end))
        one = m.match(doc, {"script": str(d / "Scripts"), "tracks_path": str(tp)}, {"start": "16", "end": "29", "overlay": "0"})
        check("回显判定（和 gametext 共用）：同一行隔开再显示一次是重播，echo_of 指最早那次；其余记录的 echo 都是空",
              (recs[7]["echo"], recs[7]["echo_of"]) == ("replay", [6, -1])
              and all(r["echo"] is None and r["echo_of"] is None for i, r in enumerate(recs) if i != 7)
              and out["stats"]["repeat_extra_echo"] == {"same_time": 0, "log": 0, "replay": 1, "other": 0}
              and [it["echo"] for it in out["overlay"]["items"] if it["layer"] == "body" and it["key"] == "A01_Ema002"] == [None, "replay"],
              [(r["echo"], r["echo_of"]) for r in recs])
        check("start / end 只匹配起点落在这段里的 cue；overlay=0 不判叠加层",
              [r["ref"]["key"] for r in one["cues"]] == ["A01_Ema002", "A01_Ema002"] and "overlay" not in one)
        check("没给剧本目录就明确报错", raises(lambda: m.match(doc, {}, {}), ValueError))
        _json.dumps(out, ensure_ascii=False)

        # 名牌粘在主轨 cue 里（名牌在正文上一行）：叠加层的名牌按 CharacterNames 出译文。naninovel 原来调 overlay_items 不交名字表，
        # 名牌层整层没有译文、叠加画的是 OCR 原文（2026-10-24，宣传片素材的 `セナ` / `リオ`）
        plate = [(0, "桜羽エマ", "え、な、なに、ここ……？"), (15, "桜羽エマ", "ねえ、ここどこなのかな！？"),
                 (40, "桜羽エマ", "それはきっと、この証拠品で証明できると思うよ！"),
                 (50, "デスム", "静かにしてくれないか？"), (65, "デスム", "だったら見せてみなよ！"), (90, "デスム", "そんな証拠、どこにもないじゃん！")]
        pevs, pcues = [], []
        for k, (a, nm, body) in enumerate(plate):
            ids = [len(pevs), len(pevs) + 1]
            pevs += [{"id": ids[0], "region": 0, "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": nm, "flags": [],
                      "box": [300, 800, 460, 840]},
                     {"id": ids[1], "region": 0, "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": body, "flags": [],
                      "box": [300, 850, 1500, 890]}]
            pcues.append({"id": f"p{k}", "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "events": ids})
        pdoc = tracksio.dump(d / "p-tracks.json", {**doc, "events": pevs, "tracks": [{**doc["tracks"][0], "cues": pcues}]})
        pout = m.match(pdoc, {"script": str(d / "Scripts")}, {})
        pnames = [(it["ocr"], it["cn"], it["name_src"]) for it in pout["overlay"]["items"] if it["layer"] == "name"]
        bare = m.match(pdoc, {"script": str(d / "Scripts"), "names": str(d / "no-such-names.bytes")}, {})
        check("naninovel 叠加层的名牌：名字表里有的出中文名（name_src = speakers），表里没有的照旧没有译文；没有名字表时整层没有译文、ref_has_speakers 记 False",
              pnames == [("桜羽エマ", "樱羽艾玛", "speakers")] * 3 + [("デスム", None, None)] * 3
              and pout["overlay"]["stats"]["name_translated"] == 3 and pout["overlay"]["stats"]["ref_has_speakers"] is True
              and all(it["cn"] is None for it in bare["overlay"]["items"] if it["layer"] == "name")
              and bare["overlay"]["stats"]["ref_has_speakers"] is False
              and NV.name_table(NV.load_names(d / "CharacterNames.bytes"))["桜羽エマ"] == "樱羽艾玛",
              (pnames, pout["overlay"]["stats"]))
        # 名牌投票走通整条路（`record_speakers` 从 naninovel 的认领取说话人）：读错一个字的 4 字名牌认上，2 字读错一个的不认
        ema = ["え、な、なに、ここ……？", "ねえ、ここどこなのかな！？", "それはきっと、この証拠品で証明できると思うよ！"]
        plate2 = [(t, nm, ema[k % 3]) for k, (t, nm) in enumerate(
            [(0, "桜羽工マ"), (15, "桜羽工マ"), (40, "桜羽工マ"), (50, "エラ"), (65, "エラ"), (90, "エラ")])]
        q_evs, q_cues = [], []
        for k, (a, nm, body) in enumerate(plate2):
            ids = [len(q_evs), len(q_evs) + 1]
            q_evs += [{**pevs[0], "id": ids[0], "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": nm},
                      {**pevs[1], "id": ids[1], "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": body}]
            q_cues.append({"id": f"q{k}", "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "events": ids})
        qdoc = tracksio.dump(d / "q-tracks.json", {**doc, "events": q_evs, "tracks": [{**doc["tracks"][0], "cues": q_cues}]})
        qout = m.match(qdoc, {"script": str(d / "Scripts")}, {})
        qnames = [(it["ocr"], it["cn"], it["name_src"]) for it in qout["overlay"]["items"] if it["layer"] == "name"]
        check("naninovel 名牌投票（整条路）：`桜羽工マ` 下面三句都是エマ说的 -> 樱羽艾玛（voted）；2 字的 `エラ` 读错一个字，同样三票也不认；stats 记 name_from_voted",
              qnames == [("桜羽工マ", "樱羽艾玛", "voted")] * 3 + [("エラ", None, None)] * 3
              and qout["overlay"]["stats"]["name_from_voted"] == 3, (qnames, [(r["ref"] or {}).get("speaker") for r in qout["cues"]]))

        # 开头行排掉本身是整条台词的行（`naninovel.head_script_lines`，2026-10-24）：短台词 `ああ。` 落在三条两行 cue 的首行、配过三种末行，
        # 不交剧本台词时它被认成名牌、叠加画出 OCR 原文（《魔裁》f3 14 块）；剧本里另有一整条台词恰好就是名字 `桜羽エマ`，名牌不许跟着被排掉
        import shutil as _sh

        import miss_detect as MD
        from flowocr.analyze import game_align as GA
        from flowocr.analyze import script_align as SA
        from flowocr.analyze import scriptmatch as SM
        _sh.copytree(d / "Scripts", d / "Scripts3")
        (d / "Scripts3" / "Act" / "D01.bytes").write_text(
            "# D01_Coco001\n; > Coco: |#D01_Coco001|\n; 桜羽エマ\n艾玛\n\n"
            "# D01_Hiro001\n; > Hiro: |#D01_Hiro001|\n; ああ。\n啊。\n", encoding="utf-8")
        hplate = [(0, "ああ。", "静かにしてくれないか？"), (20, "ああ。", "だったら見せてみなよ！"), (45, "ああ。", "そんな証拠、どこにもないじゃん！"),
                  (60, "桜羽エマ", ema[0]), (80, "桜羽エマ", ema[1]), (105, "桜羽エマ", ema[2])]
        h_evs, h_cues = [], []
        for k, (a, nm, body) in enumerate(hplate):
            ids = [len(h_evs), len(h_evs) + 1]
            h_evs += [{**pevs[0], "id": ids[0], "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": nm},
                      {**pevs[1], "id": ids[1], "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": body}]
            h_cues.append({"id": f"h{k}", "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "events": ids})
        hdoc = tracksio.dump(d / "h-tracks.json", {**doc, "events": h_evs, "tracks": [{**doc["tracks"][0], "cues": h_cues}]})
        hnames = NV.load_names(d / "CharacterNames.bytes")
        hlines = NV.load_script(d / "Scripts3", hnames)
        hout = m.match(hdoc, {"script": str(d / "Scripts3")}, {})
        hgot = [(it["ocr"], it["cn"]) for it in hout["overlay"]["items"] if it["layer"] == "name"]
        hfeed = MD.NaniFeed(hdoc, hout, NV.head_script_lines(hlines, hnames))
        check("naninovel 开头行：一整条台词（`ああ。`）落在 cue 首行、配过多种末行也不当名牌；剧本里和角色名逐字相同的台词（`桜羽エマ`）不挡名牌；"
              "疑似漏字幕判断器的 naninovel 适配层用同一份开头行",
              hgot == [("桜羽エマ", "樱羽艾玛")] * 3
              and SA.norm("ああ。") in NV.head_script_lines(hlines, hnames)
              and SA.norm("桜羽エマ") not in NV.head_script_lines(hlines, hnames)
              and hfeed.heads == frozenset({SA.norm("桜羽エマ")}), (hgot, hfeed.heads))
        # gametext 不变：不交 `script_lines` 时照旧取 `GA.band_lines(ref.script)`，带内台词全排、不为角色名开例外
        hcues, hevs_of, _ = NV.main_cues(hdoc)
        def g_names(texts: list[str]) -> list[str]:
            gref = GA.Ref(doc={"units": [], "speakers": {"桜羽エマ": "樱羽艾玛"}}, windows=[], role={}, variant_of={},
                          script=[SA.Entry(key=f"g{i}", kind="Talk", cmd="", text=t, src="u") for i, t in enumerate(texts)])
            return [it["ocr"] for it in SM.overlay_items([], hdoc, hevs_of, hcues, ref=gref)[0] if it["layer"] == "name"]
        check("gametext 开头行不变：带内台词逐字相等的排掉（含和说话人同名的那行）；不在剧本里的照旧是名牌",
              g_names(["ああ。", "桜羽エマ"]) == [] and g_names(["ああ。"]) == ["桜羽エマ"] * 3
              and g_names([]) == ["ああ。"] * 3 + ["桜羽エマ"] * 3, (g_names(["ああ。", "桜羽エマ"]), g_names([])))

        # ---- 疑似漏字幕判断器的 naninovel 适配层（miss-detector 计划 S5）：同一份剧本，另加一条主轨外的区域和 obs
        import contextlib
        import io

        import miss_detect as MD
        doc2 = _json.loads(_json.dumps(doc))
        e_off = {"id": len(evs), "region": 1, "t_start": 28_500_000, "t_end": 29_500_000, "text": "そんな証拠、どこにもないじゃん！",
                 "flags": [], "box": [600, 300, 1300, 340]}
        doc2["events"].append(e_off)
        doc2["regions"].append({"index": 1})
        doc2["tracks"].append({"id": "r01", "kind": "region", "region": 1, "label": "dialogue-or-caption", "srt": "x.srt",
                               "cues": [{"id": "o0", "t_start": e_off["t_start"], "t_end": e_off["t_end"], "events": [e_off["id"]]}]})
        obs = d / "n-obs.jsonl"
        obs.write_text("\n".join([_json.dumps({"t_us": t_us, "box": e["box"], "text": e["text"]}, ensure_ascii=False)
                                  for e in doc2["events"] for t_us in range(e["t_start"], e["t_end"], 500_000)]
                                 + [_json.dumps({"t_us": 37_000_000, "box": [600, 300, 1300, 340], "text": "私たちも魔法を使える"},
                                                ensure_ascii=False)]), encoding="utf-8")
        doc2["provenance"]["obs"] = str(obs)
        tp2 = d / "n2-tracks.json"
        doc2 = tracksio.dump(tp2, doc2)
        # 另一份剧本目录多一个文件：一句台词和法庭"反論"按钮上的缩写（`@choice`），按钮的字盖住台词的大半；obs 在末尾读到按钮
        import shutil
        shutil.copytree(d / "Scripts", d / "Scripts2")
        (d / "Scripts2" / "Act" / "C01.bytes").write_text(
            "# C01_Ema001\n; > Ema: |#C01_Ema001|\n; ……いいや。私たちだって魔法を使える。\n不，我们也能用魔法。\n\n"
            "# C01_Choice001\n; > @choice |#C01_Choice001| button:Rebuttal\n; 私たちも魔法を使える\n我们也能用魔法\n", encoding="utf-8")
        lines = NV.load_script(d / "Scripts2", NV.load_names(d / "CharacterNames.bytes"))
        out2 = m.match(doc2, {"script": str(d / "Scripts2"), "names": str(d / "CharacterNames.bytes"), "tracks_path": str(tp2)}, {})
        mp = d / "n2-matched.json"
        mp.write_text(_json.dumps(out2, ensure_ascii=False), encoding="utf-8")
        ref_srt = d / "span.srt"
        ref_srt.write_text("".join(f"{k + 1}\n00:00:{k:02d},000 --> 00:00:{k:02d},900\n{ln.jp.replace(chr(92) + 'N', chr(10))}\n\n"
                                   for k, ln in enumerate(x for x in lines if x.band)), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            det = MD.Detector(mp, span_ref=[str(ref_srt)])
            res = det.run()
        cat = {r["key"]: (r["cat"], r.get("layer"), r.get("why")) for r in res["rows"]}
        check("检测器·naninovel 适配层：分母是参照字幕圈出的字幕带行（选项不算）；认上的已输出、另一支的同一句归另一版本；"
              "主轨外的区域读到了没喂，定层『没喂』带原因；窗由前后已输出行夹出；读数逐字是『反論』按钮那一行的不算读到台词；三条不变量成立",
              det.ad.name == "naninovel" and "A01_Choice001" not in cat and len(cat) == sum(1 for x in lines if x.band)
              and cat["A01_Ema001"][0] == MD.OUT and cat["B01_Coco002"][0] == MD.OUT and cat["B01_Ema001"][0] == MD.SIB
              and cat["B01_Coco001"] == (MD.MISS, MD.L_NOFEED, "主轨以外的区域（naninovel 只喂主轨）")
              and [r for r in res["rows"] if r["key"] == "B01_Coco001"][0]["window"] == [28.0, 30.0]
              and cat["C01_Ema001"] == (MD.UNREAD, None, None)
              and res["invariants"]["ok"], cat)
        check("检测器·naninovel 不给 --span-ref 就明确报错（顺序剧本没有锚点，分母不能拿输出自己圈）",
              raises(lambda: MD.Detector(mp), SystemExit))
        check("检测器·naninovel 圈分母：对上的下标隔太远就断开，太短的段当误对丢掉；隔正好 SPAN_GAP 的并成一段，正好 SPAN_MIN 条的段留下",
              MD.spans_of([3] + list(range(500, 506)) + list(range(900, 960, 10))) == [(500, 505), (900, 950)]
              and MD.spans_of([0, MD.SPAN_GAP, 2 * MD.SPAN_GAP, 3 * MD.SPAN_GAP, 4 * MD.SPAN_GAP]) == [(0, 4 * MD.SPAN_GAP)]
              and MD.spans_of(list(range(MD.SPAN_MIN - 1))) == [])
        check("检测器·naninovel 的剧本 provenance 不含 tracks（跨臂比的正是建轨不同的臂），含 obs",
              "subs" not in det.ad.provenance and "tracks_code_fp" not in det.ad.provenance and det.ad.provenance["obs"] == str(obs))
        agg = {"events": [{"id": i, "region": i % 2, "t_start": 0, "t_end": 1_000_000, "text": "x", "flags": []} for i in (0, 1, 2)],
               "regions": [{"index": 0}, {"index": 1}], "provenance": {"main_track": "main"},
               "tracks": [{"id": "main", "kind": "main", "regions": [0, 1], "cues": [{"id": "m0", "t_start": 0, "t_end": 1_000_000,
                                                                                        "events": [0, 1], "hide": [0]}]},
                          {"id": "r01", "kind": "region", "region": 1, "cues": [{"id": "o0", "events": [2]}]}]}
        nf = MD.NaniFeed(agg, {"provenance": {"params": {}}, "stats": {"cues_raw": 1}, "cues": []}, frozenset())
        check("检测器·naninovel 的没喂原因：主轨是几块区域并成的聚合轨时，在主轨 cue 里的按 cue 报（hide 等），不在的才是主轨以外",
              [nf.why_not_fed({"id": i, "text": "x", "flags": []}) for i in (0, 2)]
              == ["cue 里藏起的行（hide：打字机半截 / 杂行）", "主轨以外的区域（naninovel 只喂主轨）"])


def t_echo() -> None:
    """回显判定（`flowocr.analyze.echo`，matcher-next 计划第 1 节，owner 10-06）：四种形态、主的挑法、变体组、extra、产物字段、旧 schema 报错。

    存在的理由：SRT 一类预设按 `echo` 去掉同时回显和记录面板回显——标错一条就是字幕里少一句（主挑错）或多一句（回显漏标），
    而认领本身不变，别的守卫都看不出来。"""
    print("回显判定")
    import json as _json
    from flowocr.analyze import echo as EC
    from flowocr.analyze import scriptmatch as SM
    from flowocr.artifacts import matchedio

    def rec(key, a, b, cue, score=1.0, extra=()):
        return {"start": a, "end": b, "ocr": key, "how": "exact", "conf": "high", "cues": [cue],
                "ref": {"key": key, "score": score, "jp": key}, "extra": [{"key": k, "score": 1.0, "jp": k} for k in extra]}
    # 喂入 cue：下标 -> (来源区域, 在主轨上, 面板样)
    cue = [("A", True, False), ("B", False, False), ("A", True, False), ("L", False, True), ("C", False, False),
           ("M", False, False), ("M", False, False), ("M", False, False), ("A", True, False), ("D", False, False)]
    recs = [rec("k1", 10, 12, 0),        # 0 主：离锚点 10 最近的那次出现、主轨上
            rec("k1", 10.5, 12, 1),      # 1 同一次出现、另一区域 -> same_time
            rec("k1", 100, 102, 2),      # 2 隔开、同区域 -> replay
            rec("k1", 200, 202, 3),      # 3 隔开、面板样 -> log
            rec("k1", 300, 302, 4),      # 4 隔开、另一区域、不成批 -> other
            rec("k2", 50, 52, 9), rec("k3", 51, 53, 9),            # 5 6 k2 / k3 第一次（各自的主）
            rec("k1", 400, 402, 5), rec("k2", 400.5, 402, 6), rec("k3", 401, 402, 7),   # 7 8 9 这一刻成批认领了旧行 -> log
            rec("k1", 12.5, 13, 8),      # 10 同一次出现、同区域、和主相接（merge_runs 没并上的）-> other
            {"start": 20, "end": 21, "ocr": "x", "how": "none", "conf": "none", "cues": [9], "ref": None, "extra": []}]
    EC.mark(recs, [c[0] for c in cue], [c[1] for c in cue], [c[2] for c in cue], {"k1": 10.0})
    got = [(r["echo"], r["echo_of"]) for r in recs]
    check("四种形态：同一次出现的另一区域 same_time、别的出现里面板样或成批旧行 log、同区域隔开 replay、其余 other；echo_of 指主那次认领 [记录, slot]（slot −1 = ref）",
          got == [(None, None), ("same_time", [0, -1]), ("replay", [0, -1]), ("log", [0, -1]), ("other", [0, -1]), (None, None), (None, None),
                  ("log", [0, -1]), ("log", [5, -1]), ("log", [6, -1]), ("other", [0, -1]), (None, None)], got)
    def batch(starts, grid):
        """k1 / k2 / k3 先各出现一次，400 s 前后另一区域成批再认一遍（starts 是那三次的起点）；返回那三次的形态。"""
        rs = [rec("k1", 10, 12, 0), rec("k2", 50, 52, 9), rec("k3", 51, 53, 9)] + \
             [rec(k, t, 402, c) for k, t, c in zip(("k1", "k2", "k3"), starts, (5, 6, 7))]
        EC.mark(rs, [c[0] for c in cue], [c[1] for c in cue], [c[2] for c in cue], {"k1": 10.0}, grid=grid)
        return [r["echo"] for r in rs[3:]]
    g60 = EC.grid_of({"meta": {"src_fps": 60.0, "stride": 30}})          # 60 fps 按 2 fps 采，等距
    fr, fr0, ong, onl = (batch((400.0, 398.72, 398.75), g60), batch((400.0, 398.72, 398.75), None),
                         batch((400.0, 398.5, 398.5), g60), batch((400.0, 398.5, 398.5), None))
    check("成批旧行的「这一刻」按采样格号比：回抠后邻行起点差 1.28 s（采样级差 2 格）仍算同一刻（log）；没有网格照原来的 ±1 s（other）；"
          "采样级起点上差 3 格（1.5 s）有没有网格都不算",
          fr == ["log", "log", "log"] and fr0 == ["other", "other", "other"] and ong == onl == ["other", "other", "other"],
          (fr, fr0, ong, onl))
    import math
    g22 = EC.grid_of({"meta": {"src_fps": 60.0, "sample_fps": 2.2}})     # 60 fps 按 2.2 fps 采：间隔 27 / 28 帧交替
    real = [math.ceil(k * 60 / 2.2) / 60 for k in range(40)]
    keys = [g22[0](t) for t in real]
    spans = [EC.in_window(keys, keys[i], g22[1]) for i in range(5, 35)]
    g25 = EC.grid_of({"meta": {"src_fps": 25.0, "sample_fps": 2.0}})     # 25 fps 按 2 fps 采：间隔 12 / 13 帧交替
    b25 = [g25[0](t) for t in (100.04, 101.52, 100.52, 102.0)]          # 回抠后 100.04（采样级 100.52）对 101.52；采样级 100.52 对 102.0（差 3 格）
    check("「这一刻」在不等距的网格上：60 fps 按 2.2 fps 采时采样级起点差 2 格都算同一刻、差 3 格都不算（最近的 3 格只差 1.35 s）；"
          "25 fps 按 2 fps 采时回抠后差 1.48 s 的两行（采样级差 2 格）算，采样级差 3 格、同样差 1.48 s 的不算",
          spans == [(i - 2, i + 3) for i in range(5, 35)] and b25[1] - b25[0] == 2 and b25[3] - b25[2] == 3 and g25[1] == 2,
          ([(i, sp) for i, sp in zip(range(5, 35), spans) if sp != (i - 2, i + 3)], b25, g25[1]))
    recs6 = [rec("s1", 10, 12, 0), rec("s1", 400, 402, 2), rec("s2", 50, 52, 0), rec("s2", 400, 402, 5),
             rec("s3", 51, 53, 0), rec("s3", 400.5, 402, 6)]
    EC.mark(recs6, [c[0] for c in cue], [c[1] for c in cue], [c[2] for c in cue], None)
    check("成批旧行只判另一区域的：同区域的再现赶上同刻成批旧行仍是重播（NPC 重复问候 + 选项菜单重现、快速回看），另一区域的才是 log",
          [r["echo"] for r in recs6] == [None, "replay", None, "log", None, "log"], [r["echo"] for r in recs6])
    check("计数：几种形态之和 = 多出来的认领次数（同 repeat_extra）",
          EC.counts(recs) == {"same_time": 1, "log": 4, "replay": 1, "other": 2} and sum(EC.counts(recs).values()) == 8,
          EC.counts(recs))
    # 主的挑法：离锚点最近的那次出现；同一次出现里 主轨上 > 不是面板样 > 分数高 > 早
    cue2 = [("X", False, False), ("Y", True, True), ("Z", True, False), ("W", True, False), ("V", True, False),
            ("A", True, False), ("A", True, False)]
    recs2 = [rec("p", 1000, 1003, 0, 1.0), rec("p", 1000.5, 1003, 1, 0.99), rec("p", 1001, 1003, 2, 0.8),
             rec("p", 1001, 1003, 3, 0.95), rec("p", 1001.5, 1003, 4, 0.95),
             rec("q", 100, 102, 5), rec("q", 900, 902, 6)]
    EC.mark(recs2, [c[0] for c in cue2], [c[1] for c in cue2], [c[2] for c in cue2], {"p": 1000.0, "q": 890.0})
    check("同一次出现里主轨上 > 不是面板样 > 分数高 > 早（不在主轨上的 1.0、面板样的 0.99 都让给主轨上的 0.95，同分取早的）",
          [r["echo_of"] for r in recs2[:5]] == [[3, -1], [3, -1], [3, -1], None, [3, -1]] and recs2[3]["echo"] is None, [r["echo_of"] for r in recs2])
    check("主取离剧本锚点时刻最近的那次出现（给的锚点不在最早那次时不取最早；产物里锚点是这一行在 obs 里第一次被检索到的时刻，实际几乎总是最早那次）",
          (recs2[5]["echo"], recs2[5]["echo_of"], recs2[6]["echo"]) == ("replay", [6, -1], None), (recs2[5], recs2[6]))
    recs3 = [rec("q", 100, 102, 5), rec("q", 900, 902, 6)]
    EC.mark(recs3, [c[0] for c in cue2], [c[1] for c in cue2], [c[2] for c in cue2], None)
    check("没有锚点时刻（顺序剧本）每行取最早那次出现当主", (recs3[0]["echo"], recs3[1]["echo"], recs3[1]["echo_of"]) == (None, "replay", [0, -1]))
    # 变体组并到代表行；extra 也是一次认领、字段写在它自己身上
    rep = {"v2": "v1"}.get
    recs4 = [rec("v1", 10, 12, 0), rec("v2", 10, 12, 1), rec("z", 300, 302, 4, extra=("v1",))]
    EC.mark(recs4, [c[0] for c in cue], [c[1] for c in cue], [c[2] for c in cue],
            EC.anchors([["v2", 9.0, []], ["v1", 10.0, []], ["z", 300.0, []]], lambda k: rep(k, k)), lambda k: rep(k, k))
    check("变体组按代表行算一句（另一种说法同时出现是 same_time）；extra 的认领标在 extra 自己身上，记录的 echo 只说主认领",
          (recs4[1]["echo"], recs4[1]["echo_of"]) == ("same_time", [0, -1])
          and (recs4[2]["echo"], recs4[2]["extra"][0]["echo"], recs4[2]["extra"][0]["echo_of"]) == (None, "other", [0, -1]),
          recs4)
    # 主落在 extra 上（主轨上 > 不在主轨上，挑主顺序不看是 ref 还是 extra）：echo_of 指到那条 extra；拍平的 SRT 只出 ref，这时那条回显的 ref 不能去
    recs5 = [rec("z", 10, 12, 0, extra=("p",)), rec("p", 10, 12, 1)]
    EC.mark(recs5, [c[0] for c in cue], [c[1] for c in cue], [c[2] for c in cue], {"p": 10.0, "z": 10.0})
    check("主是别的记录的 extra 时 echo_of 是 [记录, extra 下标]；matched_srt 的取舍（record_kept）照出这条回显，主是 ref 的照去",
          (recs5[1]["echo"], recs5[1]["echo_of"], recs5[0]["extra"][0]["echo"]) == ("same_time", [0, 0], None)
          and matchedio.record_kept(recs5[1]) and not matchedio.record_kept(recs4[1])
          and matchedio.echo_kept(recs5[1]) is False, (recs5[1], recs4[1]))
    # 真记录面板（检测器三场修复计划 U3）：不参与串出现、定主，一律 log
    cue7 = [("A", True, False), ("A", True, True), ("A", True, False)]
    lp = [False, True, False]
    r7 = [rec("t", 10, 12, 0), rec("t", 12.5, 20, 1), rec("t", 20.5, 22, 2)]
    EC.mark(r7, [c[0] for c in cue7], [c[1] for c in cue7], [c[2] for c in cue7], {"t": 10.0}, log_panel_of=lp)
    r7b = [rec("t", 10, 12, 0), rec("t", 12.5, 20, 1), rec("t", 20.5, 22, 2)]
    EC.mark(r7b, [c[0] for c in cue7], [c[1] for c in cue7], [c[2] for c in cue7], {"t": 10.0})
    check("真记录面板：台词框 / 记录面板 / 台词框（隔 > 2 s）判 主 / log / replay；不给真记录面板时三段串成一次出现、记录面板和后一段都落到 other",
          [r["echo"] for r in r7] == [None, "log", "replay"] and [r["echo"] for r in r7b] == [None, "other", "other"],
          ([r["echo"] for r in r7], [r["echo"] for r in r7b]))
    r8 = [rec("u", 5, 6, 1), rec("u", 10, 12, 0)]
    EC.mark(r8, [c[0] for c in cue7], [c[1] for c in cue7], [c[2] for c in cue7], {"u": 5.0}, log_panel_of=lp)
    check("真记录面板：只有记录面板的那次出现离锚点更近，主仍取台词框那次", (r8[0]["echo"], r8[0]["echo_of"], r8[1]["echo"]) == ("log", [1, -1], None), r8)
    r9 = [rec("w", 10, 12, 1), rec("w", 100, 102, 1)]
    r9b = [rec("w", 10, 12, 1), rec("w", 100, 102, 1)]
    EC.mark(r9, [c[0] for c in cue7], [c[1] for c in cue7], [c[2] for c in cue7], None, log_panel_of=lp)
    EC.mark(r9b, [c[0] for c in cue7], [c[1] for c in cue7], [c[2] for c in cue7], None)
    check("真记录面板：一句的认领全是记录面板时照原来的规则", [(r["echo"], r["echo_of"]) for r in r9] == [(r["echo"], r["echo_of"]) for r in r9b])
    # 真记录面板的判法（scriptmatch.log_panels）：面板样 cue 里有别的、早先（> LOG_PANEL_OLD 秒）上过屏的台词才算；横幅那种这一刻才第一次出现的不算
    from types import SimpleNamespace
    from flowocr.analyze import game_align as GA
    from flowocr.analyze import script_align as SA
    old_l, banner_l, mine_l = "前の台詞はここにありました。", "探偵手帳を開いて、手がかりを確認する", "特別にお迎えに来てあげたよ。"
    script = [SA.Entry(key=k, kind="Talk", cmd="", text=t, src="u") for k, t in (("o", old_l), ("b", banner_l), ("m", mine_l))]

    def lp_case(anchor_other: float, other_text: str, other_y: int = 0):
        ref = GA.Ref(doc={"sequence": [["o", anchor_other, []], ["b", anchor_other, []], ["m", 100.0, []]]}, script=script,
                     windows=[[[0.0, 200.0]]] * 3, role={}, variant_of={})
        c = SimpleNamespace(start=100.0, end=105.0, lines=[other_text, mine_l], source="A")
        evs = [{"id": 1, "box": [0, other_y, 500, other_y + 30], "t_start": 100_000_000, "t_end": 105_000_000, "text": other_text},
               {"id": 2, "box": [0, 700, 500, 730], "t_start": 100_000_000, "t_end": 105_000_000, "text": mine_l}]
        rec = {"ref": {"key": "m", "jp": mine_l}, "extra": []}
        return SM.log_panels(ref, [c], [evs], [True], [rec])
    name_l = "カチーナ"
    script_n = script + [SA.Entry(key="n", kind="Talk", cmd="", text=name_l, src="u")]
    ref_n = GA.Ref(doc={"sequence": [["o", 80.0, []], ["b", 80.0, []], ["m", 100.0, []], ["n", 50.0, []]]}, script=script_n,
                   windows=[[[0.0, 200.0]]] * 4, role={}, variant_of={"o": "m"})
    cN = SimpleNamespace(start=100.0, end=105.0, lines=[name_l, mine_l], source="A")
    evN = [{"id": 1, "box": [0, 640, 500, 670], "t_start": 100_000_000, "t_end": 105_000_000, "text": name_l},
           {"id": 2, "box": [0, 700, 500, 730], "t_start": 100_000_000, "t_end": 105_000_000, "text": mine_l}]
    cV = SimpleNamespace(start=100.0, end=105.0, lines=[old_l, mine_l], source="A")
    evV = [dict(evN[0], text=old_l), evN[1]]
    rec_m = {"ref": {"key": "m", "jp": mine_l}, "extra": []}
    check("真记录面板：4 字的名字读数（血条「カチーナ」）对上只有名字的台词不算；本句的变体（代表行相同）不算别的旧台词",
          SM.log_panels(ref_n, [cN], [evN], [True], [rec_m]) == [False] and SM.log_panels(ref_n, [cV], [evV], [True], [rec_m]) == [False])
    ref_c = GA.Ref(doc={"sequence": [["o", 80.0, []], ["b", 80.0, []], ["m", 100.0, []]]}, script=script,
                   windows=[[[0.0, 200.0]]] * 3, role={}, variant_of={})
    c0 = SimpleNamespace(start=100.0, end=105.0, lines=[old_l, mine_l], source="A")
    c1 = SimpleNamespace(start=105.5, end=110.0, lines=[mine_l], source="A")
    c2 = SimpleNamespace(start=110.5, end=115.0, lines=[mine_l], source="A")
    e0 = [dict(evN[0], text=old_l), evN[1]]
    same_spot = [dict(evN[1], id=3)]
    away = [dict(evN[1], id=4, box=[0, 300, 500, 330])]
    chain = SM.log_panels(ref_c, [c0, c1, c2], [e0, same_spot, [dict(same_spot[0], id=5)]], [True, False, False], [rec_m] * 3)
    moved = SM.log_panels(ref_c, [c0, c1], [e0, away], [True, False], [rec_m] * 2)
    check("真记录面板的延续：同一来源紧接着、同一句、同一处的 cue 也算，能一条接一条延续；不在同一处的（台词框里的重显）不算",
          chain == [True, True, True] and moved == [True, False], (chain, moved))
    check("真记录面板：面板样 cue 里正文上下几行有 20 s 前就上过屏的别的台词，算；这一刻才出现的（选项菜单）不算；"
          "离正文十几行远的（挂在台词框上方的任务提示横幅 hsr 1:23:23，旧台词也不算）不算",
          lp_case(80.0, old_l, 640) == [True] and lp_case(96.0, old_l, 640) == [False] and lp_case(80.0, banner_l, 100) == [False])
    check("锚点：变体组取代表行自己的时刻，代表行不在序列里取组里最早的",
          EC.anchors([["v2", 9.0, []], ["v1", 10.0, []], ["w2", 7.0, []], ["w3", 5.0, []]], lambda k: {"v2": "v1", "w2": "w1", "w3": "w1"}.get(k, k))
          == {"v1": 10.0, "w1": 5.0})
    # 产物：schema 升版，旧版明确报错；字段每条都有
    check("matched schema 是 /8；读到 /7、/6 明确报错、提示重跑匹配",
          matchedio.SCHEMA == "flowocr-matched/8"
          and all(raises(lambda v=v: matchedio.validate({"schema": f"flowocr-matched/{v}", "provenance": {}, "stats": {}, "cues": []}),
                         matchedio.MatchedSchemaError) for v in (6, 7)))
    try:
        matchedio.validate({"schema": "flowocr-matched/3", "provenance": {}, "stats": {}, "cues": []})
        msg = ""
    except matchedio.MatchedSchemaError as e:
        msg = str(e)
    check("旧 schema 的报错里写着重跑匹配", "重跑匹配" in msg, msg)
    check("SRT 去回显的取值：drop 去 same_time / log、keep 全留；别的取值报错",
          [matchedio.echo_kept({"echo": e}) for e in (None, *matchedio.ECHOES)] == [True, False, False, True, True]
          and all(matchedio.echo_kept({"echo": e}, "keep") for e in matchedio.ECHOES)
          and raises(lambda: matchedio.echo_kept({}, "all"), ValueError))
    check("scriptmatch / naninovel 的代码指纹含回显判定和它按格号比用的采样网格（改判据换指纹）",
          all({"src/flowocr/analyze/echo.py", "src/flowocr/extract/framegrid.py"} <= set(m_.CODE_FP_FILES)
              for m_ in (SM, importlib.import_module("flowocr.analyze.matchers.naninovel"))))
    # 契约：真的 run_match（全量喂入）-> match_stats，带内各形态之和 == repeat_extra_band
    import contextlib
    import io
    from flowocr.analyze import game_align as GA
    from flowocr.analyze import gamescript as GS
    from flowocr.artifacts import tracksio
    S = 1_000_000
    lines = [("a", "今日はとても良い天気ですね", "今天天气很好"), ("b", "明日も晴れるといいですね", "希望明天也是晴天"),
             ("c", "それでは一緒に行きましょうか", "那我们一起走吧")]
    refdoc = {"schema": GS.SCHEMA, "provenance": {}, "units": [{"uid": "u", "lines": [
        {"key": k, "kind": "Talk", "status": None, "text": t, "cn": c, "role": "X"} for k, t, c in lines]}],
        "sequence": [[k, 10.0 * (i + 1), [[0.0, 1000.0]]] for i, (k, _, _) in enumerate(lines)]}
    text = {k: t for k, t, _ in lines}
    # (区域, 起, 止, 行)：r0 是字幕带（主轨），r1 另一区域
    shows = [(0, 10, 12, "a"), (1, 10, 12, "a"), (0, 20, 22, "b"), (0, 30, 32, "c"), (0, 100, 102, "a"),
             (1, 300, 301, "b"), (1, 300, 301, "c"), (1, 300.5, 301, "a")]
    evs = [{"id": i, "region": r, "box": [100, 900 - 300 * r, 700, 940 - 300 * r], "t_start": int(a * S), "t_end": int(b * S),
            "text": text[k], "flags": [], "conf": 0.9, "n_obs": 10} for i, (r, a, b, k) in enumerate(shows)]
    tdoc = {"schema": tracksio.SCHEMA, "tag": "e", "meta": {"src_fps": 30.0}, "frame_us": 500_000, "size": [1920, 1080],
            "provenance": {"main_track": "r00", "main_srt": "e-region00-subtitle.srt"}, "events": evs,
            "regions": [{"index": 0}, {"index": 1}],
            "tracks": [{"id": f"r0{r}", "kind": "region", "region": r, "label": "subtitle", "srt": f"e-region0{r}-subtitle.srt", "ui_lines": [],
                        "cues": [{"id": f"c{i}", "t_start": e["t_start"], "t_end": e["t_end"], "events": [i]} for i, e in enumerate(evs) if e["region"] == r]}
                       for r in (0, 1)]}
    with tempfile.TemporaryDirectory() as td:
        rp, tp = Path(td) / "e-ref.json", Path(td) / "e-tracks.json"
        rp.write_text(_json.dumps(refdoc, ensure_ascii=False), encoding="utf-8")
        tracksio.dump(tp, tdoc)
        cref = GA.load_ref(rp)
        with contextlib.redirect_stdout(io.StringIO()):
            res = SM.run_match(cref, str(tp), "nonoise", "keep", 0.75, SM.MERGE_GAP, overlay=False)
        st = SM.match_stats(cref, res["recs"], res["suspect_raw"])
    forms = sorted((r["start"], r["echo"] or "") for r in res["recs"] if r["ref"] and r["ref"]["key"] == "a")
    check("契约（真的 run_match -> match_stats）：带内各形态之和 == repeat_extra_band；同时 / 重播 / 记录面板各就各位",
          sum(st["echo_band"].values()) == st["extra_band"] == 5
          and st["echo_band"] == {"same_time": 1, "log": 3, "replay": 1, "other": 0}
          and forms == [(10.0, ""), (10.0, "same_time"), (100.0, "replay"), (300.5, "log")],
          (st["echo_band"], st["extra_band"], forms))
    _json.dumps(recs, ensure_ascii=False)


def t_body_start() -> None:
    """正文块的起点（`scriptmatch.body_start`，时间疑点检测器计划的源头修正）：被名牌等别的字拉早的往后挪到这句字出现；
    同一句的变体、打字刚出的几个字、和剧本原文同文的读数算这句字出现；只往后挪，1.5 个采样间隔以内不挪（回抠过的也一样）。
    终点夹到正文消失（`body_end`，检测器三场修复计划 U2）。"""
    print("正文块的起点")
    from flowocr.analyze import scriptmatch as SM
    from flowocr.artifacts import tracksio

    S, F = 1_000_000, 500_000
    start = lambda *a: SM.body_start(*a)[0]                  # noqa: E731（借来的读数由 t_body_lent 守）
    T = int(SM.BODY_SHIFT_TOL * F)                           # 采样级的容差（1.5 个采样间隔）
    r1, name_box, far = [100, 900, 700, 940], [100, 840, 300, 880], [1000, 100, 1300, 130]

    def ev(i, box, t0, t1, text):
        return {"id": i, "box": box, "t_start": int(t0 * S), "t_end": int(t1 * S), "text": text}
    JP = "その継承者っていうのが"
    body = ev(1, r1, 24, 30, JP)
    name, counter = ev(0, name_box, 0, 30, "カチーナ"), ev(2, far, 0, 30, "19000/24829")
    idx = tracksio.EventIndex([name, body, counter], SM.BODY_LOOKBACK_US)
    check("被同一条 cue 里的名牌、计数器拉早的正文，起点挪到正文出现", start(0, [body], JP, idx, T) == 24 * S)
    variant = ev(3, r1, 0, 24, "その継承者っていうのが。")
    check("同一处更早就在屏的同一句变体（多读一个句号）算这句字出现：起点不挪",
          start(0, [body], JP, tracksio.EventIndex([name, body, variant], SM.BODY_LOOKBACK_US), T) == 0)
    typing = ev(4, r1, 20, 24, "そ")
    check("同一处打字刚出的一个字算这句字出现：挪到它", start(0, [body], JP, tracksio.EventIndex([name, body, typing], SM.BODY_LOOKBACK_US), T) == 20 * S)
    elsewhere = ev(5, far, 10, 30, "その継承者っていうのが")
    check("别处的同一句字不算（只认正文位置上的）", start(0, [body], JP, tracksio.EventIndex([name, body, elsewhere], SM.BODY_LOOKBACK_US), T) == 24 * S)
    old = ev(6, r1, -300, 30, "その継承者っていうのが。")
    check("比回看窗口还早就在屏、一直挂过起点的同一句变体也算（长事件另存）：起点不挪",
          start(0, [body], JP, tracksio.EventIndex([name, body, old], SM.BODY_LOOKBACK_US), T) == 0)
    misread, exact = ev(7, r1, 24, 30, "SKILL INFD"), ev(8, r1, 6, 20, "SKILL INFO")
    check("正文事件读错、同一处更早有和剧本原文同文的读数：挪到那个读数，不挪到读错的正文",
          start(0, [misread], "SKILL INFO", tracksio.EventIndex([name, misread, exact], SM.BODY_LOOKBACK_US), T) == 6 * S
          and start(0, [misread], "", tracksio.EventIndex([name, misread, exact], SM.BODY_LOOKBACK_US), T) == 24 * S)
    check("只差一个采样间隔的不挪；起点本来就晚的不往前推",
          start(int(23.5 * S), [body], JP, idx, T) == int(23.5 * S) and start(25 * S, [body], JP, idx, T) == 25 * S)
    check("终点：夹到正文最晚的消失（同一条 cue 里晚消失的名牌不再拖晚），不早于起点；开放边界的不夹",
          SM.body_end(31 * S, 24 * S, [body]) == 30 * S and SM.body_end(29 * S, 24 * S, [body]) == 29 * S
          and SM.body_end(31 * S, 32 * S, [body]) == 32 * S
          and SM.body_end(31 * S, 24 * S, [dict(body, t_end_how="open")]) == 31 * S
          and SM.body_end(30 * S + 600, 24 * S, [body]) == 30 * S + 600          # 不到 1 ms 是记录起止的取整，不夹
          and SM.body_end(int(30.7 * S), 24 * S, [body], slack=T) == int(30.7 * S))    # 采样级的不到 1.5 个采样间隔不夹
    hud = ev(12, r1, 30, 40, "LT+◆その継承者っていうのが")
    check("终点：正文位置上整个包住正文的读数（带按键前缀，zzz「LT + ◆ で任務目標に向かう」）也算这句字还在",
          SM.body_end(40 * S, 24 * S, [body], [body, hud], JP) == 40 * S)
    tail_variant, tail_name = ev(10, r1, 30, 40, "その継承者っていうのが。"), ev(11, name_box, 24, 40, "カチーナ")
    check("终点：记录里正文位置上的同一句变体（不在正文里，yuka-f3「裁判の後。」）也算这句字还在；别处、别的字（名牌）不算",
          SM.body_end(40 * S, 24 * S, [body], [body, tail_variant, tail_name], JP) == 40 * S
          and SM.body_end(40 * S, 24 * S, [body], [body, tail_name], JP) == 30 * S)


def t_body_lent() -> None:
    """正文块定起点借来的读数记进它的 `events`（2026-10-24，defaults.md 1.66）：正文事件读错（zzz `SKILL INFD`），
    起点挪到同一处更早、和剧本原文同文的读数上——那个读数要在 body 条目的 `events` 里；选项路不再拿它在同一处再出一块；
    打字机看得见这句字早就在屏，不把整句判成从起点慢慢打出（原来 66.5 s）。"""
    print("正文块借来的读数")
    from types import SimpleNamespace as _NS
    from flowocr.analyze import game_align as GA
    from flowocr.analyze import scriptmatch as SM
    from flowocr.output import script as OS

    S, box = 1_000_000, [237, 111, 309, 124]
    ref = GA.Ref(doc={"units": [{"uid": "u", "lines": [
        {"key": "si", "kind": "Loose", "status": None, "role": "UIText", "text": "SKILL INFO", "cn": "技能信息",
         "anchor": {"episodes": [[6.0, 30.0]]}}]}]}, script=[], windows=[], role={}, variant_of={})
    exact = {"id": 0, "region": 5, "text": "SKILL INFO", "box": box, "t_start": 6 * S, "t_end": 20 * S, "t_full_sampled": 6 * S}
    misread = {"id": 1, "region": 0, "text": "SKILL INFD", "box": box, "t_start": 24 * S, "t_end": 30 * S, "t_full_sampled": 24 * S}
    doc = {"events": [exact, misread], "size": [1280, 720], "frame_us": 500_000, "provenance": {}}
    cues = [_NS(start=6.0, end=30.0, lines=["SKILL INFD"])]
    recs = [{"start": 6.0, "end": 30.0, "cues": [0], "extra": [], "how": "fuzzy",
             "ref": {"key": "si", "kind": "Loose", "jp": "SKILL INFO", "cn": "技能信息", "score": 0.9}}]
    SM.mark_display(recs, cues, lambda c: "ui")
    items, *_ = SM.overlay_items(recs, doc, [[misread]], cues, ref=ref)
    body = [it for it in items if it["layer"] == "body"]
    check("正文块借来定起点的同一句读数记进它的 events（排在正文事件后面，区域仍取正文事件的）；选项路不再拿它出块",
          [(it["start_us"], it["events"]) for it in body] == [(6 * S, [1, 0])]
          and not [it for it in items if it["layer"] in ("choice", "offmain")],
          [(it["layer"], it["start_us"], it["events"]) for it in items])

    def tw(ids):
        it = OS.Item(6 * S, 30 * S, [box], ["技能信息"], "line", events=[doc["events"][i] for i in ids])
        return OS.effect_times(it, doc, doc["frame_us"])[0]
    check("打字机：只看正文事件时整句被判成从起点打了 18 s；记进借来的读数后看得见它早就在屏，不打字",
          tw([1]) == 18 * S and tw(body[0]["events"] if body else [1]) == 0, (tw([1]), body))
    names = [{"layer": "name", "events": [0], "ocr": "SKILL INFO"}, {"layer": "name", "events": [7], "ocr": "リン"}]
    kept, n = SM.yield_to_body(names, body)
    check("名牌层让出已记进正文块 events 的事件（借来定起点的读数被认成了名牌：《魔裁》f3「裁判の後。」在同一个框里叠画 OCR 原文）；别的名牌照留",
          [it["events"] for it in kept] == [[7]] and n == 1, (kept, n))

    # 名牌块被正文整块借成首行：位置离前后名牌远的去掉，贴着名牌处的不动
    evs = [{"id": i, "region": 0 if i < 10 else 1} for i in range(12)]

    def nm(i, y, t):
        return {"layer": "name", "events": [i], "start_us": t * S, "rows": [[100, y - 30, 200, y]]}
    body_it = {"layer": "body", "events": [10], "start_us": 50 * S, "block": [[100, 880, 400, 909], [100, 936, 400, 965]],
               "block_drawn_by": [None, None]}
    refs = [nm(i, 835, i) for i in range(5)]
    for y, want in ((909, 1), (850, 0)):
        its = [dict(body_it, block_drawn_by=[7, None]), *[dict(r) for r in refs], nm(5, 835, 5), nm(6, y, 50)]
        out, n = SM.yield_to_block(its, {"events": evs}, {"1": {"pitch": 56.0}})
        check(f"名牌块被正文整块借成首行、下沿离前后名牌下沿中位差半个行距以上的去掉，整块那一行改记没人画；贴着名牌处的不动（下沿 {y}）",
              n == want and len(out) == 8 - want and out[0]["block_drawn_by"] == ([None, None] if want else [7, None]),
              (n, out[0]["block_drawn_by"]))
    # 去掉的块不在末尾：后面名牌在 block_drawn_by 里的下标跟着前移；参照不够、行距量不出的不动
    other = {"layer": "body", "events": [11], "start_us": 60 * S, "block": [[100, 805, 200, 835]], "block_drawn_by": [9]}
    its = [dict(body_it, block_drawn_by=[8, None]), dict(other), *[dict(r) for r in refs], nm(5, 835, 5), nm(6, 909, 50), nm(9, 835, 60)]
    out, n = SM.yield_to_block(its, {"events": evs}, {"1": {"pitch": 56.0}})
    few_out, few_n = SM.yield_to_block([dict(body_it, block_drawn_by=[3, None]), *[dict(r) for r in refs[:2]], nm(6, 909, 50)],
                                       {"events": evs}, {"1": {"pitch": 56.0}})
    nop_out, nop_n = SM.yield_to_block([dict(it) for it in its], {"events": evs}, {"1": {}})
    check("yield_to_block：去掉中间一块后，后面名牌在 block_drawn_by 里的下标前移；前后名牌不到 INERTIA_MIN_REF 块、或行距量不出的不去",
          n == 1 and out[0]["block_drawn_by"] == [None, None] and out[1]["block_drawn_by"] == [8] and out[8]["events"] == [9]
          and few_n == 0 and len(few_out) == 4 and nop_n == 0 and len(nop_out) == len(its),
          (n, [it.get("block_drawn_by") for it in out[:2]], few_n, nop_n))


def _sq():
    """守卫用的视口：1000 × 1000 的录像、游戏画面就是整个画面，条目的 `dx` = 区域 `cx` − 0.5。"""
    from flowocr.analyze import textbox as TB
    return TB.Viewport(0, 0, 1000, 1000, (1000, 1000), "frame")


def _prof(drafts, meta, layout=None):
    """按 `_sq` 的视口出画像。"""
    from flowocr.analyze import textbox as TB
    return TB.profile(TB.region_of(drafts, meta), layout, _sq() if layout else None)


def t_textbox() -> None:
    """匹配器写的原文整块与文本框画像（`analyze.textbox`，line-breaks 计划第 1 步 B 段）：折行证据、`valign` / `pitch` / `em`、补行、惯性，
    以及两条匹配路径都把契约字段写进产物、名牌层不受影响。

    存在的理由：这几样错了阶段 3 不会报错，只会把译文摆错一行、折错地方——缺首行的条目整体低一行画、断在标点处的折行把中文误折、
    居中的游戏按行号取参照错开半行、横向吸附把投票判对齐的区域拖到一条边上。"""
    print("文本框画像与原文整块")
    import json as _json
    from flowocr import extensions as X
    from flowocr.analyze import scriptmatch as SM
    from flowocr.analyze import textbox as TB
    from flowocr.artifacts import matchedio, tracksio
    from flowocr.output import layout as LY
    from flowocr.output import script as SC

    def ev(i, text, box, flags=()):
        return {"id": i, "text": text, "box": list(box), "flags": list(flags), "region": 0, "t_start": 0, "t_end": 1}

    def dr(jp, rows, idx=0):
        """剧本 `jp`、收进条目的 OCR 行 [(字, 框, 标)] 的原文整块（补行之前）。"""
        return TB.draft_of(idx, {"jp": jp, "start_us": 0}, 0, [ev(100 + k, t, b, *f) for k, (t, b, *f) in enumerate(rows)])

    check("文本框：valign 的取值和阶段 3 的 VALIGNS 相同；新模块进匹配产物的代码指纹表（naninovel 并进 scriptmatch 那张）",
          TB.MODES == LY.VALIGNS and "src/flowocr/analyze/textbox.py" in SM.CODE_FP_FILES)

    # ---- 折行证据与 chars
    wrap31 = dr("たしかに集中していて周りの音が聞こえなくなることはあるでしょうけど、\\Nそれは違う。",
                [("たしかに集中していて周りの音が聞こえなくなることはあるでしょ", [100, 100, 1180, 130]),
                 ("うけど、", [100, 150, 240, 180]), ("それは違う。", [100, 200, 320, 230])])
    wrap20 = dr("あかさたなはまやらわいきしちにひみりゐえけせてねへめれ",
                [("あかさたなはまやらわいきしちにひみりゐえ", [100, 100, 800, 130]), ("けせてねへめれ", [100, 150, 350, 180])])
    ev31, ev20 = TB.wrap_evidence(wrap31), TB.wrap_evidence(wrap20)
    n31 = float(len("たしかに集中していて周りの音が聞こえなくなることはあるでしょ"))
    check("折行证据：剧本同一行落在两个 OCR 行上、断点前后是两个能断开的全角字——下界是被折那一截的宽（全角字），上界多下一截首字；"
          "整块按屏上的行排（被折的那行占两个位置）",
          [(x["lo"], x["hi"]) for x in ev31] == [(n31, n31 + 1)] and [(x["lo"], x["hi"]) for x in ev20] == [(20.0, 21.0)]
          and [s.line for s in wrap31.slots] == [0, 0, 1], (ev31, ev20, [s.line for s in wrap31.slots]))
    punct = [dr("見た者はいない……　正体は誰も知らない", [("見た者はいない……", [100, 100, 500, 130]), ("正体は誰も知らない", [100, 150, 500, 180])]),
             dr("あるでしょうけど、それは違う", [("あるでしょうけど、", [100, 100, 500, 130]), ("それは違う", [100, 150, 400, 180])]),
             dr("あるでしょうけどそれはコーヒー", [("あるでしょうけどそれはコ", [100, 100, 500, 130]), ("ーヒー", [100, 150, 400, 180])])]
    check("折行证据：断在空格、标点处的不算（可能是按词断的），断点后是长音这类不放行首的字也不算",
          [TB.wrap_evidence(d) for d in punct] == [[], [], []], [TB.wrap_evidence(d) for d in punct])
    ruby_ui = dr("少女は項垂れたまま動かなかった", [("うなだ", [200, 60, 260, 75]), ("少女は項垂れたまま動かなかった", [100, 80, 700, 110]),
                                                ("NO DATA", [100, 130, 300, 160])])
    ruby_flag = dr("あいうえおかきくけこさしすせそ", [("あいうえおかき", [100, 60, 400, 75], ["ruby"]), ("くけこさしすせそ", [100, 80, 500, 110])])
    check("折行证据不计 Ruby 行和 UI 行：没标出的注音（读音对不上剧本）、混进来的界面字对不上任何一行；打了 ruby 标的行对得上也不算；"
          "剧本只有一行的条目，原文整块就是 OCR 行",
          TB.wrap_evidence(ruby_ui) == [] and TB.wrap_evidence(ruby_flag) == []
          and [s.box for s in ruby_ui.slots] == [r.box for r in ruby_ui.rows] and {s.src for s in ruby_ui.slots} == {"ocr"},
          (TB.wrap_evidence(ruby_ui), TB.wrap_evidence(ruby_flag), ruby_ui.hits))
    p2, diag2 = _prof([wrap31, wrap20], {"align_how": {"by": "stats"}})
    p1, _ = _prof([wrap31], {"align_how": {"by": "vote"}})
    check("chars：证据按单个匹配产物数，不到 WRAP_MIN_EVIDENCE 条不给（不折）；两条互不相容时成立条数并列、取大的那条的下界，诊断里报成立 / 不成立各几条；"
          "没量出 em 时没有 box_w；量不出 em 时对齐用建轨判的、横向不吸",
          TB.WRAP_MIN_EVIDENCE == 2 and p1["chars"] is None and p1["evidence"] == 1
          and p2["chars"] == n31 and p2["evidence"] == 2 and (diag2["wrap_held"], diag2["wrap_broken"]) == (1, 1)
          and p2["em"] is None and p2["box_w"] is None and p2["h_inertia"] is False and p2["align"] == "center", (p1, p2, diag2))

    # 对齐打分：整行读全的行按字格边比离散
    lines = ["あいうえおかきくけ", "さしすせそ", "たちつてとなにぬねの", "はひふへほ", "まみむめもやゆよ", "（らりるれろわをん"]

    def rows_at(edge):
        out = []
        for i, t in enumerate(lines):
            w = len(t) * 30.0 - (15.0 if t[0] == "（" else 0.0)
            x0 = {"left": 100.0 + (15.0 if t[0] == "（" else 0.0), "right": 900.0 - w, "center": 500.0 - w / 2}[edge]
            out.append(dr(t, [(t, [x0, 100 + 60 * i, x0 + w, 130 + 60 * i])]))
        return out
    got = {e: TB.align_of(rows_at(e), 30.0, {"align": "center"})[:2] for e in ("left", "right", "center")}
    few = TB.align_of(rows_at("left")[:4], 30.0, {"align": "right"})[:2]
    check("对齐打分：整行读全的行按字格边（行首左括号往外补半格）比左沿 / 右沿 / 中心的离散，最小的够小、又和次好的分得开就用它、横向吸附；"
          "整行读全的行不到 ALIGN_MIN_ROWS 个用建轨判的、不吸",
          got == {"left": ("left", True), "right": ("right", True), "center": ("center", True)} and few == ("right", False), (got, few))
    same = TB.align_of([dr(lines[0], [(lines[0], [100.0, 100.0, 100.0 + len(lines[0]) * 30.0, 130.0])]) for _ in range(6)], 30.0, {"align": "right"})
    check("对齐打分：三种离散全是 0（同一行反复出现、分不出对齐）时不判，用建轨判的、不吸", same[:2] == ("right", False), same)
    wide = [dr(t, [(t, [100.0, 100.0, 100.0 + len(t) * 30.0, 130.0])]) for t in lines]
    keep = TB.BOX_MIN_LINES
    try:
        TB.BOX_MIN_LINES = len(lines)
        bw, ch_bw, ev_bw = TB.box_width(wide, 30.0, None, 1), TB.box_width(wide, 30.0, 20, 5), TB.box_width(wide, 30.0, None, TB.WRAP_MIN_EVIDENCE)
        rep_bw = TB.box_width(wide[1:] + wide[1:2] * 5, 30.0, None, 0)
    finally:
        TB.BOX_MIN_LINES = keep
    check("文本框宽：有 chars 是 chars × em；没有、折行证据又不到 WRAP_MIN_EVIDENCE 条时，不同的剧本行到 BOX_MIN_LINES 句取最宽那行的字格宽加 WIDEN_TOL 个 em；"
          "同一行反复读到只算一句，不够、或有折行证据却定不出 chars 的是 None",
          (bw[0], bw[1]["by"], ch_bw[0], ev_bw[0], rep_bw[0]) == (300.0 + TB.WIDEN_TOL * 30.0, "rows", 600.0, None, None), (bw, ch_bw, ev_bw, rep_bw))
    lay = {"aspect": 1.0, "boxes": [{"id": "b", "dx": 0.0, "cy": 0.5, "em_h": 0.03, "pitch_em": 1.5, "valign": "bottom", "chars": 20, "box_em": 20, "align": "right"}]}
    meta = {"features": {"cx": 0.52, "cy": 0.49}, "align": "left"}
    few = rows_at("left")[:4]
    p_in, d_in = _prof(few, meta, lay)
    p_far, d_far = _prof(few, {**meta, "features": {"cx": 0.7, "cy": 0.5}}, lay)
    narrow = {**lay, "boxes": [{**lay["boxes"][0], "chars": 3, "box_em": 3}]}
    p_rej, d_rej = _prof(rows_at("left"), meta, narrow)
    check("版式文件：区域中心在 LAYOUT_NEAR 以内就补量不出的字段（em、行距、竖直对齐、chars、框宽、判不出的对齐，长度按 em 换算）；"
          "离得远的不补；区域自己的行宽过条目框宽的不认这一条",
          (p_in["em"], p_in["pitch"], p_in["valign"], p_in["chars"], p_in["box_w"], p_in["align"], p_in["h_inertia"]) == (30.0, 45.0, "bottom", 20, 600.0, "right", True)
          and d_in["layout"] == {"entry": "b", "fields": ["em", "pitch", "valign", "chars", "align"]}
          and p_far["em"] is None and d_far["layout"] is None and d_rej["layout"] == {"rejected": "b"} and p_rej["chars"] is None,
          (p_in, d_in["layout"], d_far["layout"], d_rej["layout"]))
    # 量得出的不被覆盖：竖直对齐按组判得出、对齐行够了却分不开、判出整块居中行左对齐的，都不补
    by_mode_d = [dr("あいう\\Nかきく" if i % 2 else "さしす", [("あいう", [100.0, 100.0, 190.0, 130.0])]
                    + ([("かきく", [100.0, 160.0, 190.0, 190.0])] if i % 2 else [])) for i in range(12)]
    p_vm, d_vm = _prof(by_mode_d, meta, lay)
    spread_d = [dr(t, [(t, [100.0 + 37 * i, 100 + 60 * i, 100.0 + 37 * i + len(t) * 30.0, 130 + 60 * i])]) for i, t in enumerate(lines)]
    p_sp, d_sp = _prof(spread_d, meta, lay)
    check("版式文件只补量不出的：现量的竖直对齐（按组判出）不改；整行读全的行够了、三种对齐分不开的不补对齐、不吸",
          d_vm["layout"]["entry"] == "b" and d_sp["layout"]["entry"] == "b"
          and d_vm["valign"]["by"] != "none" and p_vm["valign"] == "top" and "valign" not in d_vm["layout"]["fields"]
          and d_sp["align"]["n"] >= TB.ALIGN_MIN_ROWS and d_sp["align"]["by"] == "tracks" and p_sp["align"] == "left" and p_sp["h_inertia"] is False,
          (d_vm["valign"], d_vm["layout"], d_sp["align"], p_sp["align"]))
    two = {"aspect": 1.0, "boxes": [{"id": "far", "dx": 0.06, "cy": 0.5, "em_h": 0.03}, {"id": "near", "dx": 0.03, "cy": 0.5, "em_h": 0.03},
                                    {"id": "big", "dx": 0.02, "cy": 0.49, "em_h": 0.05}]}
    ev_bad = [{"lo": 45.0, "hi": 46.0}]
    check("版式条目：几条都对上取中心最近的；量出的一字宽和条目差过 LAYOUT_EM_TOL 的对不上；折行证据在条目的 chars 下不成立的不认",
          TB.layout_entry(two, meta, 30.0, _sq())["id"] == "near" and TB.layout_entry({**two, "boxes": two["boxes"][2:]}, meta, 30.0, _sq()) is None
          and TB.entry_fits({"chars": 55}, [], 30.0, ev_bad) is False and TB.entry_fits({"chars": 45}, [], 30.0, ev_bad) is True)
    with tempfile.TemporaryDirectory() as td:
        good, bad = Path(td) / "g.json", Path(td) / "b.json"
        good.write_text(_json.dumps({"schema": TB.LAYOUT_SCHEMA, "aspect": 1.5, "boxes": []}), encoding="utf-8")
        bad.write_text(_json.dumps({"schema": "flowocr-layout/1", "aspect": 1.5, "boxes": []}), encoding="utf-8")
        try:
            TB.layout_for(str(bad), None)
            bad_err = False
        except ValueError:
            bad_err = True
        got_lf = (TB.layout_for("none", "genshin"), TB.layout_for(str(good), "genshin")["boxes"], (TB.layout_for(None, "genshin") or {}).get("source"),
                  TB.layout_for(None, "no-such-game"), TB.layout_for(None, None))
        ok_box = {"id": "b", "dx": -0.3, "cy": 0.8, "em_h": 0.03, "chars": 41.0, "valign": "top", "align": "left"}
        rejected = []
        for broken in ({"valign": "middle"}, {"align": "justify"}, {"em_h": 0}, {"box_em": -3}, {"chars": 40.5}, {"chars": "41"},
                       {"cy": 1.4}, {"id": 7}, {"em_h": None}, {"em_h": float("inf")}, {"chars": float("inf")}, {"dx": None}, {"aspect": 0}):
            top = {"aspect": broken.get("aspect", 1.5)}
            bad.write_text(_json.dumps({"schema": TB.LAYOUT_SCHEMA, **top, "boxes": [{**ok_box, **{k: v for k, v in broken.items() if k != "aspect"}}]}),
                           encoding="utf-8")
            try:
                TB.load_layout(bad)
            except ValueError as e:
                rejected.append(next(iter(broken)) in str(e))
        good.write_text(_json.dumps({"schema": TB.LAYOUT_SCHEMA, "aspect": 1.5, "boxes": [ok_box]}), encoding="utf-8")
        ok_chars = TB.load_layout(good)["boxes"][0]["chars"]
        builtin = [TB.load_layout(p)["game"] for p in sorted(TB.LAYOUT_DIR.glob("*.json"))]
    check("layout_for：none 不用；给了路径读那份；没给按游戏找随代码走的文件；没有这款游戏的文件、没给游戏是 None；schema 不对（含按整个录像画面存的旧版）报错",
          got_lf == (None, [], "layouts/genshin.json", None, None) and bad_err, got_lf)
    check("load_layout：条目的非法枚举、非正长度、非整数 chars、出界的 cy、非字符串 id、缺 em_h、无穷大、缺 dx、非正的 aspect 都在读时报错并点出字段；"
          "chars 存成整数；随代码走的几份都读得进",
          rejected == [True] * 13 and ok_chars == 41 and type(ok_chars) is int and len(builtin) >= 4, (rejected, ok_chars, builtin))

    # ---- 视口：版式条目按游戏画面存，匹配时先定游戏画面在录像里的位置（`locate`）
    zl = {"aspect": 16 / 9, "viewport": None, "boxes": [{"id": "main", "dx": -0.01, "cy": 0.83, "em_h": 0.0264}]}

    def reg(i, W, H, x, y, em, n=4):
        """区域 i：中心在录像的 (x, y) 像素、一字宽 em、n 条台词。"""
        return TB.Region({"index": i, "features": {"cx": x / W, "cy": y / H}}, rows_at("left")[:n], em, n, [])

    def at(vx, vy, vh, W, H, i=0, n=4):
        """游戏画面左上 (vx, vy)、高 vh 时主对话框所在的区域。"""
        return reg(i, W, H, vx + vh * 16 / 9 / 2 - 0.01 * vh, vy + 0.83 * vh, 0.0264 * vh, n)
    full = TB.locate(zl, [at(0, 0, 1080, 1920, 1080)], 1920, 1080)
    corner = TB.locate(zl, [at(0, 0, 549, 1280, 720, i=1, n=6), reg(2, 1280, 720, 300, 620, 14.0, 2)], 1280, 720)
    ultra = TB.locate(zl, [at(320, 0, 1080, 2560, 1080)], 2560, 1080)
    small = TB.locate(zl, [at(1400, 800, 250, 1920, 1080)], 1920, 1080)
    pinned = TB.locate({**zl, "viewport": (100, 50, 976, 549)}, [at(0, 0, 1080, 1920, 1080)], 1920, 1080)
    # 整个画面对得上就不反推：另一个区域条目更多、能配出别的视口也不换
    keep = TB.locate(zl, [at(0, 0, 1080, 1920, 1080, n=2), at(200, 100, 700, 1920, 1080, i=1, n=8)], 1920, 1080)
    check("视口：全屏录像按整个画面；游戏缩在一角（主播布局）时按主对话框的一字宽和中心反推，几个配法取对得上的台词多的；"
          "带鱼屏按 aspect 居中放进录像、对得上；反推的视口小过录像高的 VIEWPORT_MIN_H 不认、退回整个画面（记 fallback，和对上了的 frame 分开）；"
          "--ctx viewport 给了就用；整个画面对得上就不反推",
          full[1] == {"by": "frame", "box": [0, 0, 1920, 1080]}
          and corner[1]["by"] == "inferred" and corner[1]["region"] == 1 and corner[1]["entry"] == "main"
          and all(abs(a - b) < 0.5 for a, b in zip(corner[1]["box"], [0, 0, 976, 549]))
          and ultra[1] == {"by": "frame", "box": [320, 0, 1920, 1080]}
          and small[1] == {"by": "fallback", "box": [0, 0, 1920, 1080]} and pinned[1] == {"by": "ctx", "box": [100, 50, 976, 549]} and keep[1]["by"] == "frame",
          (full[1], corner[1], ultra[1], small[1], pinned[1], keep[1]))
    vp_c = corner[0]
    p_c, d_c = TB.profile(reg(5, 1280, 720, 488 - 0.01 * 549, 0.83 * 549, None), zl, vp_c)
    check("视口：量不出一字宽的区域照旧只凭位置对条目，一字宽按视口高换算（em_h × 视口高）",
          d_c["layout"] == {"entry": "main", "fields": ["em"]} and abs(p_c["em"] - 0.0264 * 549) < 0.05, (p_c, d_c["layout"]))
    out_err = raises(lambda: TB.locate({**zl, "viewport": (500, 0, 976, 549)}, [], 1280, 720), ValueError)
    parse_err = [raises(lambda s=s: tracksio.parse_viewport(s), ValueError) for s in ("1,2,3", "0,0,0,5", "a,b,c,d", "0,0,inf,5")]
    check("视口：--ctx viewport 出了录像画面、不是四个数、宽高不为正都报错；layout_for 把它记在 viewport 里",
          out_err and all(parse_err) and TB.layout_for(None, "zzz", "0,0,976,549")["viewport"] == (0.0, 0.0, 976.0, 549.0)
          and TB.layout_for(None, "zzz")["viewport"] is None, parse_err)
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("layout_export", Path(__file__).resolve().parents[2] / "dev_tools" / "layout_export.py")
    LE = _ilu.module_from_spec(spec)
    spec.loader.exec_module(LE)
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "m.json"
        exp_err = []
        for by in ("inferred", "fallback"):
            mp.write_text(_json.dumps({"overlay": {"stats": {"textbox": {"viewport": {"by": by, "box": [0, 0, 976, 549]}}}}}), encoding="utf-8")
            exp_err.append(raises(lambda: LE.candidates(mp, 1), SystemExit))
        v1 = Path(td) / "v1.json"
        v1.write_text(_json.dumps({"schema": "flowocr-layout/1", "boxes": [{"id": "b", "cx": 0.5, "cy": 0.8, "em_h": 0.03, "latin_em": 0.6}]}),
                      encoding="utf-8")
        old_v1, old_none = LE.old_boxes(v1), LE.old_boxes(Path(td) / "nope.json")
    check("导出版式文件：匹配时反推出来的视口、没对上退回整个画面的都不收（前者拿旧文件配出来，后者游戏画面多半不占满录像）；"
          "输出文件是读不了的旧格式时不带它的 latin_em、照样导出；两条的中心差横向按视口宽算",
          exp_err == [True, True] and old_v1 == [] and old_none == []
          and LE.dist({"dx": 0.16, "cy": 0.5}, {"dx": 0.0, "cy": 0.5}, 2.0) == 0.08, (exp_err, old_v1))

    def two_rows(i, w1, w2):
        t1, t2 = "あ" * w1, "い" * w2
        x0 = 500.0 - w1 * 15.0
        return dr(t1 + "\\N" + t2, [(t1, [x0, 100 + 90 * i, x0 + w1 * 30.0, 130 + 90 * i]), (t2, [x0, 160 + 90 * i, x0 + w2 * 30.0, 190 + 90 * i])])
    hybrid = rows_at("center") + [two_rows(i, 10 + i, 3 + i) for i in range(5)]
    hyb = TB.align_of(hybrid, 30.0, {"align": "left"})
    check("对齐打分：整块居中、块里各行左对齐的版式（多行条目各行左沿齐、中心不齐）不判居中，用建轨判的、不吸",
          hyb[:2] == ("left", False) and hyb[2]["by"] == "inner_left", hyb)

    def evs(*los):
        return [{"lo": float(x), "hi": float(x) + 1} for x in los]
    gi = TB.fit_chars(evs(*[40] * 5, *[41] * 4, 50, 50))
    check("chars 取让最多条证据成立的值（lo ≤ C ≤ hi，候选只取下界）：下界多在 40–41、2 条在 50 时取 41（41 让 40、41 两组都成立）；"
          "单条矛盾的样本不把它拉高；不到 WRAP_MIN_EVIDENCE 条不给",
          gi == (41.0, 9) and TB.fit_chars(evs(30, 30, 45)) == (30.0, 2) and TB.fit_chars(evs(34, 34, 34, 68)) == (34.0, 3)
          and TB.fit_chars(evs(30)) == (None, 0),
          (gi, TB.fit_chars(evs(30, 30, 45)), TB.fit_chars(evs(34, 34, 34, 68))))
    scatter = evs(30, 30, 36, 40, 44, 50)
    check("chars：取出的值下成立的不到 WRAP_MIN_HELD（一半）就不给——证据不是同一个框宽折出来的（次要区域常见）；正好一半照给",
          TB.WRAP_MIN_HELD == 0.5 and TB.fit_chars(scatter) == (None, 2) and TB.fit_chars(evs(30, 30, 40, 50)) == (30.0, 2),
          (TB.fit_chars(scatter), TB.fit_chars(evs(30, 30, 40, 50))))

    # ---- em、valign、pitch
    kanji = "亜伊宇絵於加機久計古佐志須世曽多知津手登奈仁奴根野波比"
    em_ds = [dr(kanji[:n], [(kanji[:n], [100, 100, 100 + 35 * n + 12, 130])]) for n in range(5, 26)]
    pe, _ = _prof(em_ds, None)
    check("em：行宽对行字宽（全角字）的回归斜率，检测框外扩的常数项不算进去", pe["em"] == 35.0, pe)

    def slots_draft(idx, start, bottoms, h=30, x0=100, x1=600, srcs=None, tops=None):
        srcs = srcs or ["ocr"] * len(bottoms)
        tops = tops or [b - h for b in bottoms]
        return TB.Draft(idx, 0, start, [], [], [], [TB.Slot([x0, t, x1, b], s, i) for i, (t, b, s) in enumerate(zip(tops, bottoms, srcs))])

    def by_mode(m, ruby=False):
        out = []
        for k in (1, 2, 3):
            for r in range(5):
                bots = [500 + TB.offset(m, i, k, 50) for i in range(k)]
                tops = [b - 30 - (15 if ruby and i == 0 else 0) for i, b in enumerate(bots)]
                out.append(slots_draft(len(out), len(out), bots, tops=tops))
        return out
    got_va = {m: TB.valign_of(by_mode(m), 50)[0] for m in TB.MODES}
    one_k = [slots_draft(i, i, [500, 550]) for i in range(6)]
    pr, _ = _prof(by_mode("top", ruby=True), None)
    check("valign：按整块的行数分组比三种锚，上对齐 / 下对齐 / 居中都判得出；只有一种行数时判不出、取 top",
          got_va == {m: m for m in TB.MODES} and TB.valign_of(one_k, 50) == ("top", {"groups": {"2": 6}, "by": "none"}), got_va)
    check("valign / pitch 按下沿量：首行带 Ruby（上沿高出 15）不带偏行距和对齐", pr["pitch"] == 50.0 and pr["valign"] == "top", pr)

    # ---- 按位置核对行：误读把首行读成第 2 行的字时，留位置对得上的真第 2 行（f1「ありえない可能性を除けば……」）
    mis = [TB.Row([400, 870, 1000, 909], "(【ありえたかもしれない可能性】"), TB.Row([400, 925, 1200, 967], "【ありえたかもしれない可能性】が残ります。"),
           TB.Row([400, 985, 800, 1025], "それが真実です。")]
    mis_lines = ["ありえない可能性を除けば……", "【ありえたかもしれない可能性】が残ります。", "それが真実です。"]
    by_text, by_pos = [h[0] for h in TB.assign(mis, mis_lines)], [h[0] for h in TB.assign(mis, mis_lines, 58.0)]
    off = [TB.Row([400, 870, 900, 909], "一行目の文章です"), TB.Row([400, 925, 900, 967], "二行目の文章です"),
           TB.Row([400, 1100, 900, 1140], "三行目の文章です")]
    check("原文整块·给了行距就按位置核：同一句读了两遍时留位置和其余行对得上的那个（只按字会丢掉真的第 2 行）；"
          "推出的首行下沿离中位半个行距以上的行不收",
          by_text == [1, -1, 2] and by_pos == [-1, 1, 2]
          and [h[0] for h in TB.assign(off, ["一行目の文章です", "二行目の文章です", "三行目の文章です"], 58.0)] == [0, 1, -1],
          (by_text, by_pos))

    # ---- 补行：同一条 cue 里的真实事件优先，找不到才合成
    three = "一行目の文章です\\N二行目の文章です\\N三行目の文章です"

    def fill_case(cue_evs, owner=None):
        d = dr(three, [("一行目の文章です", [100, 100, 500, 130]), ("三行目の文章です", [120, 200, 400, 230])], idx=0)
        d.cue_evs, d.own = cue_evs, {100, 101}
        TB.fill(d, 50.0, owner or {})
        return [(s.src, s.box, s.drawn_by) for s in d.slots]
    real_ev = ev(7, "二行目の文章です", [100, 150, 450, 180])
    far_ev = ev(8, "二行目の文章です", [100, 300, 450, 330])
    check("补行：缺的行先用同一条 cue 里字对得上、位置对得上的真实事件（记 event:<id>，它被别的条目画了就记那个条目的下标）",
          fill_case([far_ev, real_ev], {7: [0, 5]})[1] == ("event:7", [100.0, 150.0, 450.0, 180.0], 5), fill_case([far_ev, real_ev], {7: [0, 5]}))
    check("补行：找不到（字对得上但位置差得远的不用）就按行距合成：下沿从最近的真实行推，横向取真实几行合起来的范围",
          fill_case([far_ev])[1] == ("synth", [100, 150.0, 500, 180.0], None), fill_case([far_ev]))

    # ---- 每一行该显示的日文、按字数放宽
    folded = dr("あいうえおかきくけこさしすせそ\\Nたちつてと",
                [("うえおかきくけこ", [200, 100, 480, 130]), ("さしすせそ", [100, 150, 275, 180]), ("つてと", [140, 200, 245, 230])])
    check("每一行该显示的日文：被游戏折开的行按后一截的起点切开、各取一截（前一截 OCR 只读到中间也从行首起算）；只落在一个 OCR 行上的取剧本整行",
          [s.jp for s in folded.slots] == ["あいうえおかきくけこ", "さしすせそ", "たちつてと"], [s.jp for s in folded.slots])

    def wide(box, jp, align, chars=None, src="ocr", cap=None):
        d = TB.Draft(0, 0, 0, [], [], [], [TB.Slot(list(box), src, 0, jp=jp)])
        got = TB.widen(d, 35.0, chars, align, cap)
        return d.slots[0].box, got
    ten = "あいうえおかきくけこ"
    check("按字数放宽：期望宽 = 墨迹宽 × em，从对齐边往外放（左对齐放右沿、右对齐放左沿、居中两边对称）；已经够宽的不收窄，差不到 WIDEN_TOL 个 em 的不放；"
          "行首左括号、行尾句读点各扣半格（行尾省略号不扣）；宽过 chars 的行、期望宽超过区域 OCR 行宽上限的行（chars 为 null 时整行日文的合成行；游戏会折开、不知道是哪一截）和对不上剧本的行（jp 为 None）不动",
          wide([100, 0, 200, 30], ten, "left") == ([100, 0, 450.0, 30], [("ocr", 250.0)])
          and wide([600, 0, 700, 30], ten, "right")[0] == [350.0, 0, 700, 30]
          and wide([300, 0, 400, 30], ten, "center")[0] == [175.0, 0, 525.0, 30]
          and wide([100, 0, 500, 30], ten, "left") == ([100, 0, 500, 30], [])
          and wide([100, 0, 440, 30], ten, "left") == ([100, 0, 440, 30], [])
          and wide([100, 0, 200, 30], "（" + ten[:8] + "。", "left", src="synth") == ([100, 0, 415.0, 30], [("synth", 215.0)])
          and TB.ink_cw(ten[:8] + "……") == 10.0
          and wide([100, 0, 200, 30], ten, "left", chars=6.0) == ([100, 0, 200, 30], [])
          and wide([100, 0, 200, 30], ten, "left", src="synth", cap=300.0) == ([100, 0, 200, 30], [])
          and wide([100, 0, 200, 30], ten, "left", cap=340.0) == ([100, 0, 450.0, 30], [("ocr", 250.0)])
          and wide([100, 0, 200, 30], None, "left") == ([100, 0, 200, 30], []),
          [wide([100, 0, 200, 30], ten, a) for a in ("left", "right", "center")])

    # ---- 惯性
    def snap(drafts, va="top", h=False, align="left", p=50.0):
        st = TB.inertia(drafts, {"valign": va, "h_inertia": h}, p, align)
        return st, {d.idx: [s.box for s in d.slots] for d in drafts}
    refs_c = [slots_draft(i, i, [500]) for i in range(3)] + [slots_draft(4 + i, 4 + i, [450, 500, 550]) for i in range(3)]
    _, bc = snap(refs_c + [slots_draft(3, 3, [478, 528])], "center")
    refs_b = [slots_draft(i, i, [600]) for i in range(3)] + [slots_draft(4 + i, 4 + i, [550, 600]) for i in range(3)]
    _, bb = snap(refs_b + [slots_draft(3, 3, [505, 555, 605])], "bottom")
    check("惯性·竖直按 valign 把真实行换算成整块的锚，不同行数的条目共用一个参照（居中：两行的条目吸到 1 行、3 行参照的中心两侧；下对齐：3 行吸到末行下沿）",
          [b[3] for b in bc[3]] == [475.0, 525.0] and [b[3] for b in bb[3]] == [500.0, 550.0, 600.0], (bc[3], bb[3]))
    refs_t = [slots_draft(i, i, [500]) for i in (0, 1, 2, 5, 6, 7)]
    st_t, bt_ = snap(refs_t + [slots_draft(3, 3, [509]), slots_draft(4, 4, [560], x0=300)])
    check("惯性·竖直差得少（0.3 行高）的吸过去，差得多（2 行高）的当成另一种版式、用自己的框",
          bt_[3][0][3] == 500.0 and bt_[4][0] == [300, 530, 600, 560] and st_t["other_layout"] == 1, (bt_[3], bt_[4], st_t))
    refs_h = [slots_draft(i, i, [500], x0=426, x1=900) for i in (0, 1, 2, 4, 5, 6)]
    item_h = slots_draft(3, 3, [503, 553], x0=442, x1=900)
    item_h.slots[1].box = [600, 523, 800, 553]                     # 开头的「……」整个没读到的那一行
    _, bh = snap(refs_h + [item_h], h=True)
    item_v = slots_draft(3, 3, [503, 553], x0=442, x1=900)
    _, bv = snap([slots_draft(i, i, [500], x0=426, x1=900) for i in (0, 1, 2, 4, 5, 6)] + [item_v], h=False)
    tail = slots_draft(3, 3, [503], x0=1062, x1=1308)              # OCR 只读到行尾的单行（「…思うよ！」）
    _, bt2 = snap(refs_h + [tail], h=True)
    check("惯性·同一版式时横向一律吸到参照真实行对齐边的中位：开头是括号的行（左沿 442）、差得多的行（漏读了开头的省略号，左沿 600）都吸到 426；"
          "只挪对齐那条边、不平移，右沿不动（原文行尾不露出来）；OCR 只读到行尾的单行，整块盖住原框；对齐不是统计判出的区域横向不吸、竖直照吸",
          bh[3] == [[426, 470.0, 900, 500.0], [426, 520.0, 800, 550.0]] and bt2[3] == [[426, 470.0, 1308, 500.0]]
          and bv[3] == [[442, 470.0, 900, 500.0], [442, 520.0, 900, 550.0]], (bh[3], bt2[3], bv[3]))
    refs_q = [slots_draft(i, i, [500], x0=x, x1=900) for i, x in zip((0, 1, 2, 4, 5, 6), (426, 450, 450, 426, 450, 450))]
    _, bq = snap(refs_q + [slots_draft(3, 3, [503], x0=446, x1=900)], h=True)
    check("惯性·左对齐的参照边取前后真实行左沿的低分位（INERTIA_EDGE_Q）：开头是括号的行多、中位偏到 450 的几段里，本身缩进的行（446）照样吸回真左沿 426；"
          "右对齐取右沿的高分位、居中取中心的中位",
          bq[3] == [[426, 470.0, 900, 500.0]] and TB.ref_edge([[0, 0, x, 1] for x in (900, 880, 880, 880, 880, 900)], "right") == 900
          and TB.ref_edge([[x, 0, x + 100, 1] for x in (0, 10, 20)], "center") == 60, bq[3])
    refs_r = [slots_draft(i, i, [500], x0=450, x1=900) for i in (0, 1, 2, 4, 5, 6)]
    _, br = snap(refs_r + [slots_draft(3, 3, [503], x0=426, x1=700)], h=True)
    check("惯性·吸附只放宽、不收窄：参照偏右（参照左沿 450）时左沿不往右收（426 留着）",
          br[3] == [[426, 470.0, 700, 500.0]], br[3])
    check("惯性·横向只放宽：左对齐右沿不动、左沿只往左；右对齐对称；居中按参照中心对称放到盖住原框",
          TB.snap_x([534, 0, 1092, 10], "left", 428) == [428, 0, 1092, 10] and TB.snap_x([400, 0, 1092, 10], "left", 428) == [400, 0, 1092, 10]
          and TB.snap_x([100, 0, 500, 10], "right", 520) == [100, 0, 520, 10] and TB.snap_x([100, 0, 560, 10], "right", 520) == [100, 0, 560, 10]
          and TB.snap_x([500, 0, 700, 10], "center", 650) == [500, 0, 800, 10] and TB.snap_x([500, 0, 700, 10], "center", 580) == [460, 0, 700, 10],
          [TB.snap_x([500, 0, 700, 10], "center", c) for c in (650, 580)])
    refs_s = [slots_draft(i, i, [10, 550], x0=426, x1=900, srcs=["synth", "ocr"]) for i in (0, 1, 2)]
    for d in refs_s:
        d.slots[0].box = [0, 0, 10, 10]
    _, bs = snap(refs_s + [slots_draft(3, 3, [503], x0=440, x1=900)], h=True)
    refs_3 = [slots_draft(i, i, [500, 533, 566]) for i in (0, 1, 2, 4, 5, 6)]
    st_3, b3 = snap(refs_3 + [slots_draft(3, 3, [500, 533, 627])])
    check("惯性·整块的锚对得上、但吸过去会把某个真实行挪半个行高以上的（hsr 聊天记录：整块两行相隔 30 px、参照行距约 67 px，按参照行距排会推到日文下一行）当成另一种版式、不吸",
          b3[3] == [[100, 470, 600, 500], [100, 503, 600, 533], [100, 597, 600, 627]] and st_3["other_layout"] == st_3["other_layout_row"] == 1,
          (b3[3], st_3))
    st_f, bf = snap([slots_draft(0, 0, [500]), slots_draft(1, 1, [500]), slots_draft(2, 2, [520], x0=300)], h=True)
    check("惯性·参照只用前后条目的真实行（合成的行不算）；参照不到 INERTIA_MIN_REF 条的用自己的框",
          bs[3] == [[426, 470.0, 900, 500.0]] and bf[2] == [[300, 490, 600, 520]] and st_f["few_ref"] == 3, (bs[3], bf[2], st_f))

    # ---- 两条匹配路径都把字段写进产物；缺首行的条目从同一条 cue 的名牌事件补；名牌层逐条不变、rows 不变
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "Scripts" / "Act").mkdir(parents=True)
        bodies = ["ねえ、ここどこなのかな！？", "それはきっと、この証拠品で証明できると思うよ！", "だったら見せてみなよ！"]
        (d / "Scripts" / "Act" / "T01.bytes").write_text("".join(
            f"# T01_Ema00{k}\n; > Ema: |#T01_Ema00{k}|\n; ……エマさん。<br>{b}\n……艾玛同学。<br>\n第{k}句。\n\n" for k, b in enumerate(bodies))
            + "# T01_Narrative001\n; ここは牢獄だ。\n这里是囚牢。\n", encoding="utf-8")
        tevs, tcues = [], []
        for k, (a, b) in enumerate(zip((0, 20, 40), bodies)):
            ids = [len(tevs), len(tevs) + 1]
            tevs += [{"id": ids[0], "region": 0, "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": "……エマさん。", "flags": [],
                      "box": [400, 850, 700, 880]},
                     {"id": ids[1], "region": 0, "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "text": b, "flags": [],
                      "box": [400, 892, 1500, 922]}]
            tcues.append({"id": f"t{k}", "t_start": int(a * 1e6), "t_end": int((a + 3) * 1e6), "events": ids})
        tevs.append({"id": 6, "region": 0, "t_start": 60_000_000, "t_end": 63_000_000, "text": "ここは牢獄だ。", "flags": [], "box": [400, 850, 800, 880]})
        tcues.append({"id": "t3", "t_start": 60_000_000, "t_end": 63_000_000, "events": [6]})
        tdoc = tracksio.dump(d / "t-tracks.json", {"meta": {}, "frame_us": 500_000, "size": [1920, 1080],
                                                   "provenance": {"main_track": "r00", "main_srt": "t-r00.srt"}, "regions": [{"index": 0}],
                                                   "events": tevs, "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle",
                                                                               "srt": "t-r00.srt", "cues": tcues}]})
        m = X.load("naninovel", "matcher")
        with contextlib.redirect_stdout(io.StringIO()):
            out = m.match(tdoc, {"script": str(d / "Scripts")}, {})
            real_annotate = TB.annotate
            TB.annotate = lambda items, doc, shown, layout=None: ({}, {"regions": {}, "block_src": {}, "ocr_rows_dropped": 0, "first_missing": 0, "first_filled": 0,
                                                         "drawn_by": 0, "inertia": {}, "widened": {"rows": 0, "by_src": {}, "dx": {}}})
            try:
                plain = m.match(tdoc, {"script": str(d / "Scripts")}, {})
            finally:
                TB.annotate = real_annotate
        ov = out["overlay"]
        items = ov["items"]
        names = [(k, it) for k, it in enumerate(items) if it["layer"] == "name"]
        body = [it for it in items if it["layer"] == "body"]
        two = [it for it in body if "\\N" in it["jp"]]
        ok_contract = True
        try:
            matchedio.validate(out, need_overlay=True)
            SC.need_textbox(out)
        except Exception as e:      # noqa: BLE001
            ok_contract = repr(e)
        check("naninovel 产物：schema 当前版本、overlay.textbox 按区域号给画像，每个正文条目 block 非空、block_src / block_drawn_by 和它等长，过阶段 3 的门",
              ok_contract is True and out["schema"] == matchedio.SCHEMA and set(ov["textbox"]) == {"0"}
              and ov["textbox"]["0"]["valign"] in TB.MODES and set(ov["textbox"]["0"]) >= {"valign", "pitch", "em", "chars", "box_w", "h_inertia", "evidence"}
              and all(it["block"] and len(it["block_src"]) == len(it["block"]) == len(it["block_drawn_by"]) for it in body),
              (ok_contract, ov["textbox"]))
        plate_of = {it["events"][0]: k for k, it in names}
        check("原文整块：缺的首行（被认成名牌的「……エマさん。」）从同一条 cue 的那个事件补，记下画着它的名牌条目；rows 不变（只有收进来的正文行）；"
              "剧本只有一行的条目整块就是 OCR 行",
              len(two) == 3 and len(names) == 3
              and all(it["rows"] == [[400, 892, 1500, 922]] and len(it["block"]) == 2
                      and it["block_src"][0].startswith("event:") and it["block_src"][1] == "ocr"
                      and it["block_drawn_by"][0] == plate_of[int(it["block_src"][0][6:])] for it in two)
              and [(it["block"], it["block_src"]) for it in body if it["jp"] == "ここは牢獄だ。"] == [([[400, 850, 800, 880]], ["ocr"])]
              and ov["stats"]["textbox"]["first_filled"] == 3 and ov["stats"]["textbox"]["drawn_by"] == 3,
              [(it["rows"], it["block"], it["block_src"], it["block_drawn_by"]) for it in body])
        strip = ("block", "block_src", "block_drawn_by")
        check("名牌层逐条不变：原文整块只写正文条目的新字段，名牌的组、层、框和不建整块时逐条相同；正文条目的其余字段也相同",
              [it for it in items if it["layer"] not in ("body", "extra")] == [it for it in plain["overlay"]["items"] if it["layer"] not in ("body", "extra")]
              and [{k: v for k, v in it.items() if k not in strip} for it in body]
              == [it for it in plain["overlay"]["items"] if it["layer"] == "body"],
              _json.dumps([it for it in items if it["layer"] == "name"], ensure_ascii=False)[:400])


def t_en_target() -> None:
    """英文译文（en-target 计划）：剧本日中英三份配对、名牌带英文名、出英文要产物里有英文、英文按游戏字宽折行和定字号、
    阅读面板的英文段在空格处断、占位删掉后不留双空格、版式文件的 latin_em 带进画像。"""
    print("\n## 英文译文")
    import json
    from types import SimpleNamespace as _NS
    from flowocr import placeholders as PH
    from flowocr.analyze import gamescript as GS
    from flowocr.analyze import gtdbundle as GB
    from flowocr.analyze import textbox as TB
    from flowocr.artifacts import matchedio as MIO
    from flowocr.output import layout as LO
    from flowocr.output import script as SC
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        b3 = GB.open_bundle(_mini_bundle(td / "b3", langs=(("ja", "jp"), ("zh-Hans", "chs"), ("en", "en"))))
        b2 = GB.open_bundle(_mini_bundle(td / "b2"))
        saved = dict(GS._BUNDLES)
        try:
            GS._BUNDLES["genshin"] = b3
            three = list(GS.paired("genshin", "quests"))
            GS._BUNDLES["genshin"] = b2
            two = list(GS.paired("genshin", "quests"))
        finally:
            GS._BUNDLES.clear()
            GS._BUNDLES.update(saved)
    check("剧本：包里有英文那份时日中英三份逐行配对，没有时第三项一律 None（旧包照读）",
          [e["text"] for _, _, e in three] == ["quests-en-0", "quests-en-1", "quests-en-2"] and all(e is None for _, _, e in two)
          and [a["text"] for a, _, _ in two] == ["quests-ja-0", "quests-ja-1", "quests-ja-2"], (three, two))

    def fake(en_rows):
        rows = {"ja": [{"id": i, "text": "j"} for i in range(3)], "zh-Hans": [{"id": i, "text": "c"} for i in range(3)], GS.EN: en_rows}
        return _NS(iter_rows=lambda stem, lang: iter(rows[lang]), has=lambda tables, langs: True)
    short = []
    for en_rows in ([{"id": 0, "text": "e"}, {"id": 1, "text": "e"}], [{"id": i if i != 1 else 9, "text": "e"} for i in range(3)]):
        saved = dict(GS._BUNDLES)
        try:
            GS._BUNDLES["genshin"] = fake(en_rows)
            short.append(raises(lambda: list(GS.paired("genshin", "quests")), SystemExit))
        finally:
            GS._BUNDLES.clear()
            GS._BUNDLES.update(saved)
    check("剧本：英文那份少一行、或逐行 id 对不上，当场报错（不静默截断、不错配）", short == [True, True], short)

    doc = {"cues": [{"ref": {"key": "k", "jp": "あ", "cn": "啊"}}], "overlay": {"items": [{"layer": "body", "cn": "啊"}]}}
    check("出英文：产物里一条英文都没有（Naninovel、加英文之前的匹配）就报错，有就放行；jp 不核；不认得的语种报错",
          raises(lambda: MIO.need_lang(doc, "en"), ValueError) and not raises(lambda: MIO.need_lang(doc, "cn"), ValueError)
          and not raises(lambda: MIO.need_lang({**doc, "overlay": {"items": [{"layer": "body", "cn": "啊", "en": "Ah"}]}}, "en"), ValueError)
          and not raises(lambda: MIO.need_lang({}, "jp"), ValueError) and raises(lambda: MIO.need_lang(doc, "fr"), ValueError))
    panel = {"cues": [{"ref": None}], "overlay": {"items": [{"layer": "panel", "paras": {"jp": ["あ", "い"], "cn": ["啊", "咦"], "en": ["Ah", None]}}]}}
    check("只有阅读面板带译文的片段：面板每段都有这个语种就放行（cn），有一段缺就不算（en），同面板投影",
          not raises(lambda: MIO.need_lang(panel, "cn"), ValueError) and raises(lambda: MIO.need_lang(panel, "en"), ValueError))

    # 折行：拉丁字按 `latin`（一个字占几个全角字）量；宽字母（W）多的句子同样字数会早折
    narrow, wide_ = "aaaa aaaa aaaa aaaa", "WWWW WWWW WWWW WWWW"
    lat = {c: 0.5 for c in "a "} | {"W": 1.0}
    check("英文折行：按每个拉丁字的宽（`latin`）量、只在空格处断；宽字母多的同样字数早折；不给 `latin` 照 cw 一律半个字",
          SC.wrap_lines(narrow, 10, None, lat) == [narrow]
          and SC.wrap_lines(wide_, 10, None, lat) == ["WWWW WWWW", "WWWW WWWW"]
          and SC.wrap_lines(wide_, 10) == [wide_], (SC.wrap_lines(wide_, 10, None, lat), SC.wrap_lines(wide_, 10)))

    F = "F"
    widths = {(F, LO.EM_REF): 20.0, (F, LO.EM_REF * 2): 40.0,
              (F, LO.LATIN_REF): 0.5 * len(LO.LATIN_REF), (F, LO.LATIN_REF + " " + LO.LATIN_REF): 0.5 * (2 * len(LO.LATIN_REF) + 1),
              (F, "Hello there"): 4.4, (F, "你好"): 2.0}
    rows = [[100, 100, 900, 130]]
    s_en = LO.body_size(rows, ["Hello there"], F, widths, 31.0, None, 1920, 0.6)
    s_def = LO.body_size(rows, ["Hello there"], F, widths, 31.0, None, 1920)
    s_cn = LO.body_size(rows, ["你好"], F, widths, 31.0, None, 1920)
    check("英文字号：拉丁字平均宽 = latin_em × em（字号 1 时平均 0.5 → 31 × 0.6 / 0.5 = 37.2）；没给 latin_em（不是输出英文）内容是拉丁字也按全角字 = em（31）",
          (s_en, s_def, s_cn) == (37, 31, 31), (s_en, s_def, s_cn))
    rel = LO.latin_rel(F, {**widths, **{(F, c * n): (1.0 if c == "W" else 0.5) * n for c in LO.LATIN_CHARS for n in (10, 20)},
                           (F, "xx"): 1.0, (F, "x" + " " * 10 + "x"): 1.0 + 2.5})
    check("拉丁字相对宽：步进宽 / 平均拉丁字宽（W 是 2、普通字 1、空格 0.5）",
          [round(rel[c], 9) for c in "Wa "] == [2.0, 1.0, 0.5], {k: rel.get(k) for k in "Wa "})

    _, lines = LO.block_layout([0, 0, 100, 1000], ["alpha beta gamma delta"], 10, F, {(F, "alpha beta gamma delta"): 22.0}, size=10, latin=True)
    _, cjk = LO.block_layout([0, 0, 100, 1000], ["alpha beta gamma delta"], 10, F, {(F, "alpha beta gamma delta"): 22.0}, size=10)
    check("阅读面板：英文段在空格处断、不切在词中间，断处的空格留在行尾（拼回去就是原段）；不是输出英文的照旧按字数切",
          lines == ["alpha beta ", "gamma ", "delta"] and cjk == ["alpha beta", " gamma del", "ta"], (lines, cjk))
    pan = {"layer": "panel", "start_us": 0, "end_us": 1, "rows": [[0, 0, 100, 100]], "row_h": 10,
           "paras": {"jp": ["あ", "い"], "cn": ["甲", "乙"], "en": ["Alpha", None]}}
    check("阅读面板：英文缺一段（段数和日文对不上）的整块不画英文、不崩；中文照画",
          SC._project_one(pan, {}, "en", None, {}, PH.Marks(), latin={"a": 1.0}) is None
          and SC._project_one(pan, {}, "cn", None, {}, PH.Marks()).lines == ["甲", "乙"])

    check("占位删掉后（英文）：词间不留两个空格、标点前不留空格；中文照旧",
          PH.drop("I'm {NICKNAME} and you?") == "I'm and you?" and PH.drop("Thanks {NICKNAME}.") == "Thanks."
          and PH.drop("Hello, {NICKNAME}!") == "Hello!" and PH.drop("你好，{NICKNAME}。") == "你好。"
          and PH.drop("当前清缴积分：[ {X} ]") == "当前清缴积分：[  ]" and PH.drop("{NICKNAME} and I.\n{NICKNAME} went") == "and I.\nwent",
          (PH.drop("I'm {NICKNAME} and you?"), PH.drop("Thanks {NICKNAME}."), PH.drop("Hello, {NICKNAME}!")))

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "l.json"
        f.write_text(json.dumps({"schema": TB.LAYOUT_SCHEMA, "aspect": 1.0, "boxes": [{"id": "b", "dx": 0.0, "cy": 0.5, "em_h": 0.03, "latin_em": 0.66}]}),
                     encoding="utf-8")
        lay = TB.load_layout(f)
        f.write_text(json.dumps({"schema": TB.LAYOUT_SCHEMA, "aspect": 1.0, "boxes": [{"id": "b", "dx": 0.0, "cy": 0.5, "em_h": 0.03, "latin_em": 0}]}),
                     encoding="utf-8")
        bad = raises(lambda: TB.load_layout(f), ValueError)
    meta = {"features": {"cx": 0.5, "cy": 0.5}, "align": "left"}
    prof, diag = _prof([], meta, lay)
    prof0, _ = _prof([], meta)
    from flowocr.analyze import game_align as GA
    from flowocr.analyze import scriptmatch as SM
    from flowocr.analyze import script_align as SA
    nref = GA.Ref(doc={"units": [{"uid": "u", "lines": [{"speaker": "パイモン", "speaker_cn": "派蒙", "speaker_en": "Paimon"}]}],
                       "speakers": {"パイモン": "派蒙"}, "speakers_en": {"ウェンティ": "Venti"},
                       "terms": {"傭兵": {"cn": "佣兵", "en": "Mercenary"}, "隊長": {"cn": "队长"}}},
                  script=[], windows=[], role={}, variant_of={})
    check("名牌的英文名：说话人表按 speaker_en / speakers_en、短名词表按 terms 的 en 查（没有英文的不收）",
          SM.speaker_names(nref, "en") == {SA.norm("パイモン"): "Paimon", SA.norm("ウェンティ"): "Venti"}
          and SM.term_names(nref, "en") == {SA.norm("傭兵"): "Mercenary"} and SM.term_names(nref) == {SA.norm("傭兵"): "佣兵", SA.norm("隊長"): "队长"},
          (SM.speaker_names(nref, "en"), SM.term_names(nref, "en")))
    check("版式文件的 latin_em：对上的条目带进画像（诊断记进补的字段），不合规（非正）读时报错；没有版式文件的画像不带",
          prof.get("latin_em") == 0.66 and "latin_em" in diag["layout"]["fields"] and bad and "latin_em" not in prof0, (prof, diag["layout"]))
