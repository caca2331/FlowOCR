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


def t_missed_trace() -> None:
    """匹配后的漏行追查（`dev_tools/missed_trace.py`）：时间窗、窗里读到过、另一版本三个纯函数。"""
    print("漏行追查")
    import missed_trace as MT

    prev, nxt = [(10.0, 12.0), (500.0, 502.0)], [(15.0, 18.0), (900.0, 901.0)]
    check("时间窗：取离锚点最近的那次认领——前一条不晚于它的结束、后一条不早于它的开始",
          MT.window(13.0, prev, nxt) == (12.0, 15.0), MT.window(13.0, prev, nxt))
    check("时间窗：剧本头尾那一端没有认领行就是 None",
          MT.window(13.0, None, nxt) == (None, 15.0) and MT.window(13.0, prev, None) == (12.0, None))
    check("窗里读到过：锚点的上屏时段和窗（两头放宽 PAD）有交叠才算；远处的读到不算",
          MT.in_window([[14.0, 14.5]], 12.0, 15.0) and not MT.in_window([[300.0, 301.0]], 12.0, 15.0)
          and MT.in_window([[16.5, 17.0]], 12.0, 15.0))
    texts = {"a": "インターノットの依頼も特に変わったものはなく、簡単なやつばっか", "b": "全然関係ない別の台詞です"}
    claimed = [(11.0, 14.0, "a"), (11.0, 14.0, "b")]
    sib = MT.sibling("インターノットの依頼も特に変わったものはなくて、簡単なやつばっか…", 12.0, 15.0, claimed, texts)
    check("另一版本：同一个窗里被认领的行和它几乎一样就算（两头都没认领行不查）",
          sib is not None and sib[0] == "a"
          and MT.sibling("全く違う文章をここに書いておく", 12.0, 15.0, claimed, texts) is None
          and MT.sibling(texts["a"], None, None, claimed, texts) is None, sib)


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
        return {"uid": uid, "lines": [{"key": (keys or {}).get(i, f"{uid}{i}"), "kind": "NPC",
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
          GS.mark_duplicates(us, 0.5, 60.0) == 1 and us[1]["lines"][1]["kind"] == "Duplicate")
    us = [unit("a", ["今日はいい天気だね", "そうだね", "散歩に行こう"]),
          unit("b", ["今日はいい天気だね", "そうだね", "散歩に行こう", "新しい台词だよ"])]
    n = GS.mark_duplicates(us, 0.5, 60.0)
    check("重演拷贝：后一份里和前面重复的行记 Duplicate，独有的行留着",
          n == 3 and [l["kind"] for l in us[1]["lines"]] == ["Duplicate"] * 3 + ["NPC"], n)
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
                     {"key": "a0", "kind": "NPC", "anchor": None},
                     {"key": "a1", "kind": "NPC", "anchor": {"episodes": [[10.0, 12.0]]}},
                     {"key": "a2", "kind": "NPC", "anchor": None},
                     {"key": "a3", "kind": "NPC", "anchor": {"episodes": [[90.0, 95.0], [3000.0, 3004.0]]}}]},
                 {"uid": "b", "t0": 50.0, "lines": [
                     {"key": "b0", "kind": "NPC", "anchor": {"episodes": [[50.0, 51.0]]}},
                     {"key": "b1", "kind": "Duplicate", "anchor": None}]}]
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
    kinds = {k: ln["kind"] for k, ln in A.items()}
    check("检索层：兄弟选项的回复读到了而它没有 -> Unwalked；最后一个锚点之后 -> OutOfSpan",
          kinds == {"a0": "Talk", "as": "OffBand", "a1": "Talk", "a2": "Unwalked", "ac": "Talk",
                    "a3": "Talk", "a4": "OutOfSpan"}, kinds)
    check("短行（不到 --qmin 字）在圈中单元的时段内按逐字相等补锚点，于是位置判据也管得上它",
          doc["stats"]["short_anchors"] == 1 and A.get("as", {}).get("anchor", {}).get("in_band") is False,
          (doc["stats"]["short_anchors"], A.get("as", {}).get("anchor")))
    check("锚点记 unique：只被常用句（撞了 >3 个单元的查询）锚住的行 unique=False（audit-6 §1.4）",
          bool(A.get("ac", {}).get("anchor")) and A["ac"]["anchor"]["unique"] is False
          and A["a0"]["anchor"]["unique"] is True)
    check("变体组：obs 那一截同时包含于两种说法、两行都被锚住，也只留一行进分母（audit-6 §1.3）",
          (Cu.get("c0", {}).get("kind"), Cu.get("c1", {}).get("kind"), Cu.get("c1", {}).get("variant_of"))
          == ("Talk", "Variant", "c0"), {k: ln["kind"] for k, ln in Cu.items()})
    check("字幕带：锚点框的众数窗口；读到过而框全在带外的记 OffBand，带里的和只撞常用句的（没有位置证据）不动",
          doc["stats"]["band"] is not None and doc["stats"]["band"][0] < .84 < .86 < doc["stats"]["band"][1]
          and Cu.get("c3", {}).get("kind") == "OffBand" and Cu.get("c2", {}).get("kind") == "Talk"
          and A.get("ac", {}).get("anchor", {}).get("in_band") is None,
          (doc["stats"]["band"], {k: ln["kind"] for k, ln in Cu.items()}))
    check(f"字幕带：锚住的行不到 {GS.BAND_MIN_LINES} 条不估；估的时候少数几行带外的拉不动窗口",
          GS.band_of([[.85]] * (GS.BAND_MIN_LINES - 1)) is None
          and GS.band_of([[.85], [.86, .2], [.84], [.85], [.4], [.1]])[0] < .84)
    grp = [{"key": "r", "kind": "Talk", "anchor": {"in_band": None}},
           {"key": "v", "kind": "Variant", "variant_of": "r", "anchor": {"in_band": False}},
           {"key": "s", "kind": "Talk", "anchor": {"in_band": None}},
           {"key": "w", "kind": "Variant", "variant_of": "s", "anchor": {"in_band": True}},
           {"key": "x", "kind": "Choice", "anchor": {"in_band": False}}]
    n = GS.mark_offband([{"lines": grp}])
    check("OffBand 看整个变体组：代表行没有位置证据时用另一种说法的；已经排出分母的不重判",
          n == 1 and [g["kind"] for g in grp] == ["OffBand", "Variant", "Talk", "Variant", "Choice"],
          [g["kind"] for g in grp])
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
    check("叠加：译文恰好分成原字幕的行数——剧本换行数对得上就照它，否则按字数均分；一行的不拆",
          OV.overlay_lines("上一句\\N下一句", 2) == ["上一句", "下一句"]
          and OV.overlay_lines("一二三四五六", 2) == ["一二三", "四五六"]
          and OV.overlay_lines("一二\\N三四\\N五六", 2) == ["一二三", "四五六"]
          and OV.overlay_lines("一二三四五六", 1) == ["一二三四五六"])
    rows_p = [[100, 100, 900, 130], [300, 136, 700, 166]]
    box_p, rows_pl, text_pl = (LY.plate_rects(rows_p, [400, 600], 500, m) for m in ("box", "rows", "text"))
    check("叠加底板：box 一整块；rows 每行 max(原文, 译文) 区间；text 只盖译文；上下两行外扩后的交叠从中间劈开",
          box_p == [[94, 94, 906, 172]]
          and rows_pl[0][0] == 94 and rows_pl[0][2] == 906 and rows_pl[1][0] == 194 and rows_pl[1][2] == 806
          and text_pl[0][0] == 294 and text_pl[0][2] == 706
          and rows_pl[0][3] == rows_pl[1][1] == 133, (box_p, rows_pl, text_pl))
    left_pl = LY.plate_rects(rows_p, [1000, 200], 100, "text", "left")
    check("叠加底板按锚点算译文区间：左锚从轴往右长（不是以轴为中心）",
          left_pl[0][0] == 94 and left_pl[0][2] == 1106 and left_pl[1][2] == 306, left_pl)
    tw = [TS.body_of(r, "on") for r in TS.with_tw(TS.text_rows("一二\\N三")[0], 300, False)]
    check("叠加：打字机逐字显出——字幕稿里正文是纯文字、fo 记 tw，阶段 4 换成逐字 \\alpha：首字在起点、末字正好在全字出现那一刻，字序跨行接着数",
          len(tw) == 2 and "\\t(0,1," in tw[0] and "\\t(150,151," in tw[0] and "\\t(300,301," in tw[1], tw)
    with tempfile.TemporaryDirectory() as td:
        tw_doc = {"size": [1920, 1080], "frame_us": 500_000, "meta": {}, "regions": []}
        tw_it = OV.Item(0, 3_000_000, [[0, 0, 200, 30], [0, 40, 100, 70]], ["一二", "三"], "line",
                        events=[{"t_full_sampled": 1_500_000}])
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

    # ---- 叠加分层（owner 2026-09-14，product-goals 第 16 条）：判定写进 JSON、导出只投影 ----
    from types import SimpleNamespace as _NS
    lref = GA.Ref(doc={"units": [{"uid": "u", "lines": [
        {"key": "a", "kind": "Talk", "text": "第一行のせりふです", "cn": "第一行台词", "speaker": "カチーナ", "speaker_cn": "卡齐娜"},
        {"key": "a2", "kind": "Talk", "text": "今日はいい天気ですね", "cn": "今天天气真好", "speaker": "カチーナ", "speaker_cn": "卡齐娜"},
        {"key": "a3", "kind": "Talk", "text": "ありがとう、助かったよ", "cn": "谢谢", "speaker": "カチーナ", "speaker_cn": "卡其娜"},
        {"key": "b", "kind": "Choice", "text": "また後で来るね", "cn": "待会儿再来", "speaker": None}]}]},
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
        {"key": "a", "kind": "Talk", "text": "第一行のせりふです", "cn": "第一行台词", "speaker": "カチーナ"}]}]},
        script=[], windows=[], role={}, variant_of={})
    _empty_doc = {"events": [], "size": [1920, 1080], "frame_us": 500_000, "tracks": [], "provenance": {}}
    # matched.json 的加载校验（2026-09-15 审计：schema 串升过一次而全仓库没人读它）
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "m.json"
        good = {"schema": SM.SCHEMA, "provenance": {}, "stats": {}, "cues": [],
                "overlay": {"items": [], "stats": {}}}
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
    doc_l = {"events": evs_l, "size": [1920, 1080], "frame_us": 500_000, "provenance": {}}
    items_l, st_l = SM.overlay_items(recs_l, doc_l, evs_of_l, cues_l, ref=lref)
    check("叠加分层：正文 / extra 各盖自己的行（extra 不并进正文）、名牌一块带中文名；顺序 body→…→extra→name",
          [(it["layer"], it["events"], it["cn"]) for it in items_l]
          == [("body", [1], "第一行台词"), ("extra", [2], "待会儿再来"), ("name", [0], "卡齐娜")]
          and st_l["name_translated"] == 1,
          [(it["layer"], it["events"], it["cn"]) for it in items_l])
    proj_all = OV.project_items(items_l, doc_l, "cn", OV.LAYERS)
    check("叠加投影：按层开关、按语言取译文，不重新判断；名牌 / 选项不带打字机要的正文事件；多行用公共轴",
          [t.kind for t in OV.project_items(items_l, doc_l, "cn", ("body", "choice"))] == ["line"]
          and [(t.lines, t.kind) for t in proj_all] == [(["第一行台词"], "line"), (["待会儿再来"], "line"), (["卡齐娜"], "name")]
          and proj_all[2].events == [] and all(t.translated for t in proj_all),
          [(t.lines, t.kind) for t in proj_all])
    nameless = GA.Ref(doc={"units": []}, script=[], windows=[], role={}, variant_of={})
    nm = [it for it in SM.overlay_items(recs_l, doc_l, evs_of_l, cues_l, ref=nameless)[0] if it["layer"] == "name"]
    check("名牌没对上说话人表时 jp / cn 都为空（jp 口径不拿 OCR 读数盖名牌自己），OCR 原文留在 ocr",
          len(nm) == 1 and nm[0]["jp"] is None and nm[0]["cn"] is None and nm[0]["ocr"] == "カチーナ"
          and items_l[2]["jp"] == "カチーナ", nm)
    check("名牌挂在正文上：正文紧贴在名牌下方、左右对得上才算；并排一行的角色列表 / 离得远的键位不算",
          SM.name_over_body(evs_l[0], evs_l[1])
          and not SM.name_over_body(evs_l[0], {"box": [1500, 850, 1700, 880]})
          and not SM.name_over_body(evs_l[0], {"box": [1100, 800, 1300, 840]}))
    check("主轨外逐字等于剧本行的块按剧本行类型分层：选项类（Choice / OffBand）进 choice，其余进 offmain",
          [SM.choice_layer(k) for k in ("Choice", "OffBand", "Talk", "Loose")]
          == ["choice", "choice", "offmain", "offmain"])
    moving_ev, still_ev = {"box": [0, 0, 10, 10], "boxes": [[0, [0, 0, 10, 10]]]}, {"box": [0, 0, 10, 10]}
    check("主轨外的块：在动的只收选项类（滑入的按钮），在动的台词 / UI 不收（静止框盖不住滚动的面板）；不动的都收",
          SM.block_ok(moving_ev, "Choice") and not SM.block_ok(moving_ev, "Talk")
          and SM.block_ok(still_ev, "Talk") and SM.block_ok(still_ev, "OffBand"))
    check("主轨外的块：offmain 至少在屏上两个采样点（一帧的是滚动残影），选项类不拦",
          not SM.block_long_enough("Talk", 500_000, 500_000) and SM.block_long_enough("Talk", 1_000_000, 500_000)
          and SM.block_long_enough("Choice", 500_000, 500_000))
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
    prov2 = {"obs": "out/x.jsonl", "argv": ["--iou", "0.3", "out/x.jsonl", "--outdir=o", "--tag", "t"]}
    check("BUILD_ARGS 按名剥掉驱动自己填的 obs / --outdir / --tag，不靠位置（audit-6 §2.4）",
          (tracksio.build_args(prov), tracksio.build_args(prov2)) == (["--main-band", "0"], ["--iou", "0.3"]),
          (tracksio.build_args(prov), tracksio.build_args(prov2)))


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


def t_nameplate_terms() -> None:
    """名牌的短名词表（2026-09-26，followups「名牌上的人名没有译名」）：说话人表查不到的名牌查 terms。"""
    print("\n## 名牌的短名词表")
    from types import SimpleNamespace as _NS
    from flowocr.analyze import game_align as GA
    from flowocr.analyze import gamescript as GS
    from flowocr.analyze import script_align as SA
    from flowocr.analyze import scriptmatch as SM
    want = GS.term_wanted(["シュラム", " 傭兵 ", "口", "E", "Bi", "8147!!", "任務アイテム"])
    check("短名词·查询键：内容 ≥2 字且含假名 / 汉字才查（噪音读数 口 / E / Bi / 数字不查，免得在默认预设里被『译』出来）",
          want == {"シュラム", "傭兵", "任務アイテム"}, want)
    row = lambda i, t: {"id": i, "text": t}
    pairs = [(row(1, "旅人"), row(1, "旅行者")), (row(2, "旅人"), row(2, "旅人")), (row(3, "旅人"), row(3, "旅行者")),
             (row(4, "スコア"), row(4, "分数")), (row(5, "スコア"), row(5, "评分")),
             (row(6, "シュラム"), row(6, "施拉姆")), (row(7, "{NICKNAME}"), row(7, "{NICKNAME}")),
             (row(8, "傭兵"), row(8, None)), (row(9, "未出現"), row(9, "没出现"))]
    got_t = GS.pick_terms(pairs, {"旅人", "スコア", "シュラム", "傭兵"}, "F")
    check("短名词·消歧按 hash 条数取多数、打平取短的再按字典序；只收 obs 里出现过的键；中文缺的不收",
          got_t == {"旅人": {"cn": "旅行者", "n": 2, "of": 3}, "スコア": {"cn": "分数", "n": 1, "of": 2},
                    "シュラム": {"cn": "施拉姆", "n": 1, "of": 1}}, got_t)
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
    items, st = SM.overlay_items([], doc, evs_of, cues, ref=tref)
    nm = sorted((it["ocr"], it["cn"], it["name_src"]) for it in items if it["layer"] == "name")
    check("短名词·名牌层先查说话人表、查不到再查短名词表，条目记 name_src；stats 记 name_from_terms",
          SM.term_names(tref) == {SA.norm("カチーナ"): "卡琪娜", SA.norm("シュラム"): "施拉姆"}
          and nm == [("カチーナ", "卡齐娜", "speakers"), ("シュラム", "施拉姆", "terms")]
          and st["name_translated"] == 2 and st["name_from_terms"] == 1, (nm, st))
    old = GA.Ref(doc={"units": [], "speakers": {}}, script=[], windows=[], role={}, variant_of={})
    check("短名词·旧剧本（没有 terms）照读：表为空、名牌层不出译文",
          SM.term_names(old) == {} and all(it["cn"] is None for it in SM.overlay_items([], doc, evs_of, cues, ref=old)[0]))
