"""通用匹配器：把主轨的每条 cue 对上剧本原文，产出用途 2 的**最终产物**。

方案与取舍写在 matcher 计划，这里只记实现上的判断：

* **匹配失败是一等公民**（owner，product-goals 用途 2）：对不上就原样留 OCR 文本、标 `how: "none"`，
  不硬塞一个最像的。导出字幕时这类条目照旧出现，只是没被替换。
* **时间一律取 cue 的**。原文替换掉 OCR 文本之后，时间轴是唯一保留下来的自有信息（product-goals 那张表），
  不从剧本合成，也不在这里做打字机延迟修正。
* **判据不另写一份**：cue 的读法（`game_align.variants`：去掉开头 0–3 行名牌 / 称号 × 去不去掉末行键位）、
  按时段圈候选、第二遍的整行包含认领，全部**直接调 `game_align`**。量尺和产品共用判据、不共用入口——
  拿匹配后的文本去量匹配器是循环论证（docs/dev-guide/verification.md「归因：问『这一处发生了什么』」）。
* **一条 cue 可以带第二条剧本行**（`extra`）：屏幕上一条 cue 装两行剧本是常事（选项按钮 + 正文、
  并带并进两个说话人）。产品侧**不拆 cue**——拆开要分时间，而时间是我们唯一的自有信息。
  **导出契约（owner 2026-09-14，product-goals 第 16 条）**：主产物是**叠加 ASS**，`extra` 是它独立的一层
  （`LAYERS`），和名牌 / 选项 / 阅读面板一样逐层可开关；各层盖哪些块在 `overlay_items` 里判定、写进 JSON 的
  `overlay.items`，导出器只投影。SRT 是拍平投影，只出主 `ref`（`body()`），所以"覆盖"（`ref` ∪ `extra`，
  和量尺同口径）**大于 SRT 里出现的剧本行数**，两个数都打出来，别混着读。

用法：见 `CLI_DESCRIPTION`。
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from flowocr.artifacts import evalkit  # noqa: E402
from flowocr.artifacts import matchedio  # noqa: E402
from flowocr.artifacts.matchedio import LAYERS  # noqa: E402   层的名单：判定（这里）和投影（overlay）两边都用
from flowocr.analyze import game_align as GA  # noqa: E402
from flowocr.analyze import script_align as SA  # noqa: E402
from flowocr.artifacts import srtio  # noqa: E402
from flowocr.artifacts import tracksio  # noqa: E402

CODE_FP_FILES = ("src/flowocr/analyze/scriptmatch.py", "src/flowocr/artifacts/evalkit.py", "src/flowocr/analyze/game_align.py",
                 "src/flowocr/analyze/script_align.py", "src/flowocr/artifacts/srtio.py", "src/flowocr/artifacts/tracksio.py",
                 "src/flowocr/artifacts/matchedio.py")
"""本文件 + 它 import 的本地模块（同 `build_tracks.CODE_FP_FILES` 的口径，仓库相对路径）。
再往上的两层各自有指纹、写在 provenance 里：剧本是 `ref_code_fp`（gamescript），轨是 `tracks_code_fp`。"""
CLI_DESCRIPTION = """把轨的每条 cue 对上剧本原文（带评测口径的报表；通用入口是 flowocr-match）。

对不上的原样留 OCR 文本、标 how: "none"；时间一律取 cue 的。一条 cue 可以带第二条剧本行（extra），
SRT 只出主认领，所以报表里的"覆盖"会大于 SRT 里出现的剧本行数。叠加层的判定写进 --out 的 overlay.items。

    python -m flowocr.analyze.scriptmatch out/gametext/hsr-ref.json --subs out/gs-hsr/hsr-tracks.json \\
        --out out/gametext/hsr-matched.json --srt tmp/hsr-cn.srt --lang cn"""
# `*-matched.json` 的格式（版本、校验、正文取法）在 `flowocr.artifacts.matchedio`——阶段 3 的预设也要读它，
# 而 output 只许依赖 artifacts（2026-09-22 Codex 审计 P2）。这里 re-export，老调用方不变。
SCHEMA = matchedio.SCHEMA
MatchedSchemaError = matchedio.MatchedSchemaError
REQUIRED = matchedio.REQUIRED
load_matched = matchedio.load

CONF_HIGH, CONF_MED = 0.9, 0.75
"""改写的置信度分档（按 cue 读法与剧本行的相似度）。`contain` 一律算高——整行都在 cue 里。"""

SUSPECT = 0.85
""""可疑改写"的门，**必须严格高于 `--fuzzy`**。

接受判据本身就是"相似度 ≥ fuzzy"（`game_align.run`），而这里记的分是同一个量
（`sim_of` 和 `run` 用同一组 cue 读法、同一个 `SequenceMatcher.ratio`），`pick_variant` 又只在组里取最大——
所以拿 `--fuzzy` 当这条线，"低于门的改写"**结构上恒为 0**，量不了任何东西。
和 `unmatched_subs` 那个"幽灵条目上限"一个形状（docs/dev-guide/verification.md「报数口径」：指标的门不能等于接受判据的门）：
2026-09-11 初版把它写成 `CONF_MED`（= 默认 fuzzy 0.75），四场整片读出来全是 0，被当成"没有可疑改写"。
0.85 是拍的，只用来圈出**要摆帧的尾巴**，不是合格线。"""


def sim_of(raw: str, text: str, heads: frozenset[str] = frozenset()) -> float:
    """cue 的几种读法里和这条剧本行最像的那个的相似度。

    ⚠ **`heads` 必须和匹配时用的那一份相同**（2026-09-18 审计的 P2，已修）：
    `game_align.score` / `.run` 算候选读法时是 `variants(raw, heads)`——**开头行（名牌 / 称号）先剥掉**；
    而这里原来调的是 `variants(raw)`，**没剥**。于是同一条 cue「匹配时用的读法」和「报分时用的读法」不是一回事：
    名牌留在读法里，相似度被压低。审计给的两条实例：报 0.364 / 0.419，按真正用的读法是 **0.923 / 0.857**。
    影响面：`可疑改写 < 0.85` 这张清单、`conf` 的 high/med/low 分档、以及 `--feed-panel strict` 的那道门
    （它就是拿这个 score 判"弱认领"）。所以这不是"报表好看一点"的事，是**判决也跟着错**。
    """
    best = 0.0
    for v in GA.variants(raw, heads):
        best = max(best, SequenceMatcher(None, v, SA.norm(text), autojunk=False).ratio())
    return round(best, 3)


def pick_variant(groups: dict[str, list[int]], script: list[SA.Entry], i: int, raw: str,
                 heads: frozenset[str] = frozenset()) -> int:
    """认上的这一行若属于变体组（绝区零两位主角的两种说法），**输出屏幕上的那一种**：
    组里谁和这条 cue 最像就换成谁。

    量尺那边一组只算一个分母条目、认哪一种都算命中（`gamescript.fold_variants`）；
    产品这边不行——2026-09-11 整场绝区零实测，低分改写里大半是"屏幕上是アキラ的说法、
    输出成了リン的"。只在库里明确成组的行之间挑，挑不到别的台词上。"""
    group = groups.get(script[i].key)
    if not group:
        return i
    return max(group, key=lambda k: (sim_of(raw, script[k].text, heads), -k))


def variant_index(ref: GA.Ref, script: list[SA.Entry]) -> dict[str, list[int]]:
    """组里每一行的 key -> 这一组全部成员的下标（只有 ≥2 个成员的组才进）。"""
    idx = {e.key: k for k, e in enumerate(script)}
    members: dict[str, list[int]] = {}
    for k, rep in ref.variant_of.items():
        if k in idx:
            members.setdefault(rep, [idx[rep]] if rep in idx else []).append(idx[k])
    return {script[m].key: g for g in members.values() if len(g) > 1 for m in g}


PANEL_MIN_SCORE = 0.85
"""面板样 cue 的认领要多强才算（`--feed-panel strict`）。

为什么要"形状 + 强度"两个条件（matcher 计划）：单看形状会误伤——
星铁的对话记录面板和**原神的"台词 + 选项按钮"**形状一样（都是 5–6 行、竖跨 0.15+ 屏），
但前者的认领 score 只有 0.36~0.58（整块旧台词对一句），后者是 1.00
（选项行被 `head_lines` 当开头行剥掉了，剩下的正文精确对上）。
所以**弱认领 + 面板形状**才是"拿旧台词冒充此刻"的特征。`contain` 不受这条限制：
整行原文就在 cue 里，相似度不是它的判据。"""


def match(ref: GA.Ref, cues: list[srtio.Cue], fuzzy: float,
          panel_of: list[bool] | None = None, panel_mode: str = "keep") -> tuple[list[dict], dict]:
    """每条 cue 一条记录。返回 (记录, `game_align.score` 的统计)。

    `panel_mode="strict"` + `panel_of`：面板样 cue 的**弱认领**（score < `PANEL_MIN_SCORE`）退回"没认上"。
    """
    script = [SA.Entry(key=e.key, kind=e.kind, cmd="", text=e.text, src=e.src) for e in ref.script]
    subs = [(c.start, "\n".join(c.lines), "") for c in cues]
    st = GA.score(ref, script, subs, fuzzy)
    cn = {ln["key"]: ln.get("cn") for u in ref.doc["units"] for ln in u["lines"]}
    unit = {ln["key"]: u["uid"] for u in ref.doc["units"] for ln in u["lines"]}
    sn = [SA.norm(e.text) for e in script]
    heads = st.get("heads") or frozenset()     # **匹配用的那一份开头行**，报分要用同一份（见 sim_of）
    vgroups = variant_index(ref, script)
    out = []
    n_strict = [0]
    for i, c in enumerate(cues):
        raw = "\n".join(c.lines)
        h, extra = st["sub_hit"][i], st["sub_extra"][i]
        rec = {"start": round(c.start, 3), "end": round(c.end, 3), "ocr": raw,
               "how": "none", "ref": None, "extra": [], "cues": [i]}
        if h >= 0:
            h = pick_variant(vgroups, script, h, raw, heads)
            # `heads` 同样要带上（2026-09-19 自查补漏）：上一处把 `sim_of` 修了，**这一行漏了**——
            # 于是"名牌 + 正文"的 cue 剥开之后明明和剧本行逐字相同，却被记成 `fuzzy`。
            # 判据只有一份的意思是**这一份的每个调用点**都用同一种读法
            rec["how"] = "exact" if sn[h] in GA.variants(raw, heads) else "fuzzy"
            rec["ref"] = ref_rec(script[h], cn, unit, sim_of(raw, script[h].text, heads))
        elif extra:
            j = pick_variant(vgroups, script, extra[0], raw, heads)
            rec["how"] = "contain"
            rec["ref"] = ref_rec(script[j], cn, unit, sim_of(raw, script[j].text, heads))
        for j in extra[1:] if h < 0 else extra:
            rec["extra"].append(ref_rec(script[j], cn, unit, sim_of(raw, script[j].text, heads)))
        if (panel_mode == "strict" and panel_of and panel_of[i] and rec["ref"]
                and rec["how"] != "contain" and rec["ref"]["score"] < PANEL_MIN_SCORE):
            n_strict[0] += 1
            rec["how"], rec["ref"], rec["extra"] = "none", None, []
        r = rec["ref"]
        rec["conf"] = ("none" if not r else "high" if rec["how"] == "contain" or r["score"] >= CONF_HIGH
                       else "med" if r["score"] >= CONF_MED else "low")
        out.append(rec)
    if n_strict[0]:
        print(f"[喂入] --feed-panel strict：面板样 cue 的弱认领（score < {PANEL_MIN_SCORE}）"
              f"退回没认上 {n_strict[0]} 条")
    return out, st


MERGE_GAP = 2.0
"""相邻两条 cue 认的是同一条剧本行、且间隔不超过这么多秒，就并成一条（`--merge-gap 0` 关掉）。

**不是可选的美化**：主轨的 cue 按整条多行文本切，名牌一抖就是一条新 cue
（整场绝区零近一半的相邻 cue 正文相同，game-text-corpus 报告），
换成原文之后就是同一句话连打五遍。留间隔上限是为了不把**真实重播**（隔一阵又演一次）并掉。"""


def merge_runs(recs: list[dict], gap: float, src_of: list | None = None) -> list[dict]:
    """相邻、同一条剧本行的记录并成一条：时间取首尾，OCR 文本留最长的那条（信息最全）。

    "相邻"按**来源区域**各自判（`src_of[cue 下标]` = 那条 cue 来自哪条区域轨；不给就是一个来源）：`--feed nonoise` 把各区域的 cue
    按时间交错着喂（`Cue.source` 是区域轨的 SRT 名），同一句话被一闪而过的杂项切成两条时，中间常夹着别的区域的 cue，按喂入序列判相邻就并不上——
    gi2 整场的重复认领 165 次里 122 次是这种间隔 ≤ 2 s 的（followups-evidence-0926 报告）。只并同一区域的，
    主对话和阅读面板里的同一句不会并成一个叠加块。`--feed main` 只有一个来源，结果和按序列判相同。

    ⚠ 并出来的 `ocr`（最长那条）和 `ref.score`（最像那次）可以来自不同的 cue；低分那条的可疑改写报警随合并消失，
    所以 `run_match` 另报合并前的条数（`suspect_raw`）。同一来源里的几条 cue 也不一定在同一处，
    叠加块按位置另切（`position_segments`）。"""
    if gap <= 0:
        return recs
    out: list[dict] = []
    last: dict = {}                                   # 来源 -> 这个来源在 out 里最近的一条
    for r in recs:
        src = src_of[r["cues"][0]] if src_of is not None else None
        p = out[last[src]] if src in last else None
        same = (p and p["ref"] and r["ref"] and p["ref"]["key"] == r["ref"]["key"]
                and r["start"] - p["end"] <= gap)
        if not same:
            out.append({**r, "n_cues": 1})
            last[src] = len(out) - 1
            continue
        p["end"] = r["end"]
        p["n_cues"] += 1
        p["cues"] = p["cues"] + r["cues"]
        if len(r["ocr"]) > len(p["ocr"]):
            p["ocr"] = r["ocr"]
        if r["ref"]["score"] > p["ref"]["score"]:     # 认的是同一行，留最像的那次的分
            p["ref"], p["how"], p["conf"] = r["ref"], r["how"], r["conf"]
        seen = {e["key"] for e in p["extra"]}
        p["extra"].extend(e for e in r["extra"] if e["key"] not in seen)
    return out


def ref_rec(e: SA.Entry, cn: dict, unit: dict, score: float) -> dict:
    return {"key": e.key, "kind": e.kind, "unit": unit.get(e.key), "jp": e.text,
            "cn": cn.get(e.key), "score": score}


body = matchedio.body
"""一条 cue 的正文取法（`flowocr.artifacts.matchedio.body`）：匹配上用剧本原文，否则原样留 OCR。"""


FEEDS = ("main", "nonoise", "all")
"""**喂给匹配器的是哪些文本**（owner 2026-09-17，product-goals 第 18 条）。

owner 把匹配器定为"特化的聚类规则 + 文本替换"，**"被匹配器吸收"本身就是一条分类判据**——
那就不该在它前面再插一道有损过滤。而 `main` 正是那道过滤：主轨 = "按分数挑一个区域 + 并带"的投影。

* `main`（默认，现状口径）：只喂主轨；
* `nonoise`：喂**除 `noise` 之外的全部区域轨**（= owner 说的"保留全部非噪音的 OCR 文本"）；
* `all`：全部区域轨，连 `noise` 一起。

量过的账在 game-text-corpus 报告：11 段没有一段变差，四个整场 +25~+69 命中，
新吸收的抽 8 条摆帧 8/8 是真吸收。**2026-09-20 owner 把默认从 `main` 翻成 `nonoise`**
（defaults.md §1.4；它顺带解掉了 `--slot-reassign` 的主要代价——主轨口径下丢的那批行，
`nonoise` 下 f2 看得见 64/66、f4 看得见 37/37）。⚠ 那次翻转的**代价侧当天补量是负的**：
gi2 整场上重复认领涨、待 owner 重新决定，数字见 defaults.md §1.4 那张表。
"""


def feed_from_doc(doc: dict, mode: str, panel: str = "keep") -> tuple[list, list[list[dict]], list[bool]]:
    """从一级产物**投影**出要喂的 cue 流 + 每条 cue 的事件（`mode != "main"` 时用）。

    **投影，不判断**（artifacts.md 第 1 条）：切分、常驻 UI 的剔除、并带都已经写在 JSON 里，
    这里只做三件事——挑轨、按 `cue_lines` 取该导出的事件、按时间排序。
    规则和 `export.export_srt` 逐条对齐（剔完常驻 UI 为空的 cue 跳过）。

    ⚠ **要排除的是"聚合轨"（`kind == "main"`），不是 `provenance.main_track` 指的那条**
    （2026-09-18 审计抓到的 P1）：`build_tracks` 只在**真的并了带**的时候才另建一条 `id="main"` 的聚合轨；
    没并带时 `main_track` 直接指向一条**真实区域**（`r00` 那种），按 id 排就把正文整条扔了——
    只有一条字幕区域的产物上 `nonoise` / `all` 会输出 0 条 cue。按 `kind` 排只扔掉真正重复的那份投影。

    `panel="drop"` 时把 `build_tracks` 标了 `panel_like` 的 cue 剔掉（对话记录面板那种：
    文本是真的、时间是错的，matcher 计划）。**判定是 build_tracks 做的，这里只按标取舍**
    （artifacts.md 第 1 条）；旧产物没有这个标，剔不掉，会打印一行提示。
    第三个返回值是逐 cue 的"是不是面板样"，给 `panel="strict"` 用（见 `PANEL_MIN_SCORE`）。
    """
    if mode not in FEEDS:
        raise SystemExit(f"--feed 只能取 {'/'.join(FEEDS)}：{mode}")
    rows: list[tuple[int, int, list[dict], str]] = []
    n_panel = 0
    n_agg = 0                              # 被白名单挡下的轨（聚合投影 / 名牌轨 / 以后别的）
    for tr in doc["tracks"]:
        # **白名单：只喂区域轨。** 别的轨都是同一批 run 的另一种投影，喂进来就是喂两遍：
        #   `kind="main"`      聚合投影（并带的那条）；
        #   `kind="nameplate"` 名牌轨（2026-09-20 加的，和区域轨**有意重叠**）——
        #                      它独立成条喂进匹配器的代价见 methodology-audit-3：
        #                      只有名字的 cue 会去抢含名字的剧本行，重复认领涨 2.2~7.0 倍。
        # ⚠ 原来这里是**黑名单**（只排 main），名牌轨一加就漏了。黑名单挡不住"以后又加一种轨"。
        if tr.get("kind") != "region":
            n_agg += 1
            continue
        if mode == "nonoise" and tr.get("label") == "noise":
            continue
        for cue in tr["cues"]:
            if panel == "drop" and cue.get("panel_like"):
                n_panel += 1
                continue
            evs = tracksio.cue_lines(doc, tr, cue)
            if evs:
                rows.append((cue["t_start"], cue["t_end"], evs, tr["srt"],
                         bool(cue.get("panel_like"))))
    rows.sort(key=lambda r: (r[0], r[1]))
    if panel == "drop":
        n_flag = sum(1 for tr in doc["tracks"] for c in tr["cues"] if c.get("panel_like"))
        print(f"[喂入] --feed-panel drop：剔掉 {n_panel} 条面板样 cue"
              + ("（这份产物里一个 `panel_like` 标都没有——是 build_tracks 打标之前产的？）"
                 if n_flag == 0 else ""))
    cues = [srtio.Cue(a / 1e6, b / 1e6, tuple(e["text"] for e in evs), src)
             for a, b, evs, src, _ in rows]
    return cues, [evs for _, _, evs, _, _ in rows], [pl for *_, pl in rows]


def cue_events(doc: dict, cues: list) -> list[list[dict]]:
    """主轨每条**导出了的** cue 的事件，和 SRT 一条对一条（`export.export_srt` 跳过剔完常驻 UI 为空的 cue，这里照做）。
    条数或起点对不上就抛——叠加字幕要拿框，对错一条就整片错位。"""
    tr = tracksio.main_track(doc)
    if tr is None:
        raise SystemExit("tracks.json 没有主轨")
    pairs = [(cue, evs) for cue, evs in ((c, tracksio.cue_lines(doc, tr, c)) for c in tr["cues"]) if evs]
    if len(pairs) != len(cues):
        raise SystemExit(f"主轨 cue 对不上 SRT：tracks 里导出了 {len(pairs)} 条，SRT 有 {len(cues)} 条")
    bad = sum(1 for (cue, _), c in zip(pairs, cues) if abs(cue["t_start"] / 1e6 - c.start) > 0.002)
    if bad:
        raise SystemExit(f"主轨 cue 起点和 SRT 对不上 {bad} 条（SRT 不是这份 tracks.json 导出的？）")
    return [evs for _, evs in pairs]


BODY_IN_REF = 0.6
"""正文事件的字要有这么多落在剧本原文里才算正文（`body_of`）。"""


def body_of(evs: list[dict], heads: frozenset[str], ref_text: str = "") -> list[dict]:
    """一条 cue 里要被盖住的**正文**事件：内容 ≥3 字、不是开头行（名牌），并且字**落在剧本原文里**。

    最后这条是 2026-09-12 摆帧抓的：hsr 2:18 翻页提示 ▼ 被 OCR 读成 `un2ra` / `Y02R2`（5 个内容字），
    混进正文——框被撑大，打字机"全字出现"取到它最后一次出现的时刻，一句台词逐字显了 12 秒。"""
    rt = GA.GS.cnorm(ref_text)

    def is_body(e: dict) -> bool:
        if SA.norm(e["text"]) in heads:
            return False
        q = GA.GS.cnorm(e["text"])
        if evalkit.content_len(e["text"]) >= 3:
            return not rt or GA.GS.contained(q, rt) >= BODY_IN_REF
        # 1–2 字的短台词（`「未着」……`，hsr 2:10 漏叠的那条）：字全在剧本原文里、而且占了原文内容的一半以上才算，
        # 挡住的是 `回` / `A` 这类碰巧落在长句里的单字垃圾
        return bool(q and rt) and GA.GS.contained(q, rt) >= 0.99 and len(q) >= 0.5 * len(rt)
    return [e for e in evs if is_body(e)]


def rows_of(body: list[dict]) -> list[list[float]]:
    """正文事件按 cy 分行，每行一个框（行内事件框的并集），从上到下。"""
    rows: list[list[float]] = []
    for e in sorted(body, key=lambda e: (e["box"][1] + e["box"][3]) / 2):
        x0, y0, x1, y1 = e["box"]
        if rows and (y0 + y1) / 2 - (rows[-1][1] + rows[-1][3]) / 2 <= 0.5 * (y1 - y0):
            r = rows[-1]
            rows[-1] = [min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)]
        else:
            rows.append([x0, y0, x1, y1])
    return rows


CHOICE_KINDS = ("Choice", "OffBand")
"""剧本行类型里算"选项"的：`Choice`（库的结构判的）、`OffBand`（位置判的，绝区零的选项按钮落在这里）。"""


def choice_layer(kind: str) -> str:
    """主轨外逐字等于剧本行的块归哪一层：选项类进 `choice`，其余进 `offmain`。
    2026-09-14 数过：原来统称 choice 的块里真选项只有 hsr 整场 87/340（`Talk` 217）、zzz 整场 178/498（`Loose` 241）。"""
    return "choice" if kind in CHOICE_KINDS else "offmain"


def block_ok(ev: dict, kind: str) -> bool:
    """主轨外逐字等于剧本行的事件能不能进叠加层：**动的块只收选项类**。

    静止的框盖不住在动的字——滚动的回看面板（hsr 6873 s 的ログ）上，事件带 `boxes[]` 轨迹，而叠加块只有一个框，
    于是译文错位压到别的行上（2026-09-14 摆帧）。hsr 整场 `offmain` 226 条里 131 条来源事件在动。
    滚动长文本归 `panel` 层（`readable_items` 逐帧读 obs）；选项按钮滑入时也会被记成 moving，所以选项类不拦。"""
    return kind in CHOICE_KINDS or "boxes" not in ev


def block_long_enough(kind: str, dur_us: int, frame_us: int) -> bool:
    """`offmain` 的块至少在屏上两个采样点：只读到一帧的是滚动途中 / 翻页瞬间的残影，盖上去就是闪一下
    （hsr 整场 `offmain` 去掉在动的之后 106 条里 32 条只有一帧，6873 s 回看面板上剩下的错位全是这种）。
    选项类不拦（按钮可能只停很短）。块的时长含末帧那一个采样间隔，所以一帧 = `frame_us`，留半帧余量。"""
    return kind in CHOICE_KINDS or dur_us > frame_us + frame_us // 2


def choice_items(doc: dict, main_regions: set[int], ref: GA.Ref) -> list[dict]:
    """**主轨之外**、逐字等于剧本行的事件（选项按钮这一类）：同一条剧本行、隔 ≤1 s 的并成一段。

    2026-09-12 owner 看预览：hsr 0:21 的五个选项不在主轨里，没盖。剧本里选项是 `Choice` 行（有中文），
    所以只认**归一后逐字相等**（选项短，模糊匹配会乱认），并且事件时刻落在那一行所在单元的时段里（±60 s）。"""
    unit_span: dict[str, list[float]] = {}
    for u in ref.doc["units"]:
        ts = [t for ln in u["lines"] for e in ((ln.get("anchor") or {}).get("episodes") or []) for t in e]
        if ts:
            unit_span[u["uid"]] = [min(ts) - 60, max(ts) + 60]
    by_text: dict[str, list[tuple]] = {}
    for u in ref.doc["units"]:
        span = unit_span.get(u["uid"])
        for ln in u["lines"]:
            k = GA.GS.cnorm(ln["text"])
            if span and ln.get("cn") and len(k) >= 2:
                by_text.setdefault(k, []).append((ln["key"], ln["cn"], span, ln["kind"], ln["text"]))
    squash = lambda s: "".join(unicodedata.normalize("NFKC", s).split())   # noqa: E731
    hits: dict[str, list] = {}
    for e in doc["events"]:
        if e["region"] in main_regions:
            continue
        t, q = e["t_start"] / 1e6, GA.GS.cnorm(e["text"])
        cands = [c for c in by_text.get(q, ()) if c[2][0] <= t <= c[2][1]]
        if not cands:
            continue
        # 归一化会去掉标点，按钮 `「未着」` 和台词 `「未着」……` 就成了同一个键；先认原文逐字相同的，再认 Choice 行——
        # 第一版取第一个，第五个选项被换成了台词的译文 `「未抵」……`，多出来的省略号把字挤小（hsr 0:24 摆帧）
        best = max(cands, key=lambda c: (squash(c[4]) == squash(e["text"]), c[3] == "Choice"))
        key, text = best[:2]
        if not block_ok(e, best[3]):
            continue
        hits.setdefault(key, []).append((e, text, q, best[4], best[3]))
    others = sorted((e for e in doc["events"] if e["region"] not in main_regions), key=lambda e: e["t_start"])
    starts = [e["t_start"] for e in others]
    out = []
    for key, evs in hits.items():
        evs.sort(key=lambda x: x[0]["t_start"])
        groups: list[list] = []
        for e, text, jp, script_jp, kind in evs:
            # 还得是同一个位置：阅读面板里的小标题 `ブラーチ` 和选项按钮 `「ブラーチ」` 归一后逐字相等，
            # 只按时间并会把两处的框并成一个大框（第一版摆帧，一个「布拉琪」铺满半屏）。
            # 找的是**所有**开着的组；位置几乎不动（IoU ≥ 0.5）时隔 CHOICE_BRIDGE_US 以内也接上（惯性）
            cur = next((g for g in groups if box_iou(g[2], e["box"]) >= 0.3
                        and e["t_start"] - g[1] <= (CHOICE_BRIDGE_US if box_iou(g[2], e["box"]) >= 0.5
                                                    else 1_000_000)), None)
            if cur:
                cur[1] = max(cur[1], e["t_end"])
                cur[8].append(e["id"])
                cur[2] = [min(cur[2][0], e["box"][0]), min(cur[2][1], e["box"][1]),
                          max(cur[2][2], e["box"][2]), max(cur[2][3], e["box"][3])]
            else:
                cur = [e["t_start"], e["t_end"], list(e["box"]), text, jp, script_jp, key, kind, [e["id"]]]
                groups.append(cur)
        # 惯性的另一半：组末尾之后 CHOICE_BRIDGE_US 以内、落在组框里、字是这条选项一部分的**残读**
        # （hsr 0:24 第二个选项被读成 `チ」` / `一チ`，逐字匹配认不出，按钮就断了）接进来
        for g in groups:
            i = bisect.bisect_left(starts, g[0])
            while i < len(others) and others[i]["t_start"] <= g[1] + CHOICE_BRIDGE_US:
                o = others[i]
                i += 1
                if o["t_end"] <= g[1] or box_inside(o["box"], g[2]) < 0.8:
                    continue
                q = GA.GS.cnorm(o["text"])
                # 残读也要过 `block_ok`（2026-09-15 审计）：它当不了种子，却能把块的结束时间往后推 2.5 s，
                # 于是**在动的**事件仍能让一个静止的 offmain 块多活一会儿——正是 matcher 计划那两道 offmain 过滤要挡的形状从另一个口子进来
                if q and GA.GS.contained(q, g[4]) >= 0.6 and block_ok(o, g[7]):
                    g[1] = max(g[1], o["t_end"])
                    g[8].append(o["id"])
        groups.sort(key=lambda g: g[0])
        for g in groups:
            prev = out[-1] if out and out[-1][4] == g[4] else None
            if prev and g[0] <= prev[1] and box_iou(prev[2], g[2]) >= 0.5:
                prev[1] = max(prev[1], g[1])
                prev[8] += g[8]          # 接上以后重叠的两段并掉，免得半透明底板叠两层
            else:
                out.append(g)
    return [{"layer": choice_layer(x[7]), "start_us": x[0], "end_us": x[1], "rows": [x[2]], "events": x[8],
             "key": x[6], "jp": x[5], "cn": x[3]}
            for x in out if block_long_enough(x[7], x[1] - x[0], doc["frame_us"])]


CHOICE_BRIDGE_US = 2_500_000
"""选项按钮的惯性：同一位置隔这么久以内再读到（或读到残片）就算没断过。"""


def box_inside(a: list[float], b: list[float]) -> float:
    """a 有多少面积落在 b 里。"""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return ix * iy / area if area > 0 else 0.0


READABLE_MIN_CHARS = 12
"""阅读面板这类长文本：屏幕上一行至少这么多内容字才去 TextMap 里找（短的撞车太多）。"""
READABLE_IN = 0.9
"""一行 OCR 的字有这么多落在某一段里，才算这一段。"""


def paragraphs(t: str) -> list[str]:
    """TextMap 的一整条长文本拆成段：去掉 `<b>` / `<i>` 这类标签，按换行（字面的 `\\n` 或真换行）切，丢空段。"""
    t = re.sub(r"<[^>]*>", "", t).replace("\\n", "\n").replace("\xa0", " ")
    return [p.strip() for p in t.split("\n") if p.strip()]


def box_iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def readable_items(obs_path: Path, textmap_dir: Path, frame_us: int,
                   taken: list[dict]) -> tuple[list[dict], int]:
    """**实验**（2026-09-12 owner 要"试试看能不能盖"阅读面板）：**逐帧**拿 obs 里的长文本行去上游 TextMap 里找
    所在的**段**，同一帧、同一段的几行并成一块盖上那一段的译文；相邻帧同一段、位置几乎不动就接着延长。
    返回 (items, 用掉的观测条数)。

    **不用 run / 事件**：阅读面板在滚，run 按静止框跟踪，同一个框位置上的文字一路在换，
    投票留下来的文本和并出来的框来自不同的滚动位置（第一版这么做，摆帧整块错位）。逐帧的观测没有这个问题。
    `taken` = 已经被台词叠加认领的正文事件：同一时刻、框重叠的观测跳过（TextMap 的长条目里也有台词本身）。

    剧本（gamescript）只收对话，阅读面板（探偵手帳这类）不在里面，所以这里**直接读 game-text-data 的上游快照**
    `upstream/<游戏>/TextMap/TextMap{JP,CHS}.json`——这越过了"只读产物"的约定，只当原型；
    要留下来得让 game-text-data 出一份长文本产物（文本包里没有 TextMap，用户手里没有这条路，手册不提）。
    日文与中文按同一个 hash 取、按段对位，段数不等的整条跳过。"""
    from flowocr.analyze import gamescript as GS
    jp = json.loads((textmap_dir / "TextMapJP.json").read_text(encoding="utf-8"))
    cn = json.loads((textmap_dir / "TextMapCHS.json").read_text(encoding="utf-8"))
    paras: dict[str, tuple[str, str]] = {}
    for k, v in jp.items():
        if len(v) < 40 or k not in cn:
            continue
        pj, pc = paragraphs(v), paragraphs(cn[k])
        if len(pj) != len(pc):
            continue
        for a, b in zip(pj, pc):
            key = GS.cnorm(a)
            if len(key) >= READABLE_MIN_CHARS:
                paras.setdefault(key, (a, b))
    keys = list(paras)
    index = GS.Index(keys)
    by_sec: dict[int, list[dict]] = {}
    for e in taken:
        for s in range(e["t_start"] // 1_000_000, e["t_end"] // 1_000_000 + 1):
            by_sec.setdefault(s, []).append(e)
    found: dict[str, int | None] = {}
    frames: dict[int, dict[int, list[dict]]] = {}
    n_used = 0
    with open(obs_path, encoding="utf-8") as f:
        next(f)                                  # _meta
        for line in f:
            o = json.loads(line)
            q = GS.cnorm(o["text"])
            if len(q) < READABLE_MIN_CHARS:
                continue
            if q not in found:
                best, best_s = None, READABLE_IN
                for i in index.query(q, top=3):
                    s = GS.contained(q, keys[i])
                    if s >= best_s:
                        best, best_s = i, s
                found[q] = best
            p, t = found[q], o["t_us"]
            # 被台词认领的正文**框里**的观测跳过：打字机打到一半的那一帧框比整句窄，按 IoU 判就漏过去，
            # 半句台词被当成长文本又叠一层（hsr 0:47，摆帧看见的）
            if p is None or any(e["t_start"] <= t <= e["t_end"] and box_inside(o["box"], e["box"]) >= 0.8
                                for e in by_sec.get(t // 1_000_000, ())):
                continue
            frames.setdefault(t, {}).setdefault(p, []).append(o)
            n_used += 1
    # 第二遍：有匹配的那些帧里的**全部**观测，用来把块长满整个面板那一栏（小标题、不到 12 字的短行没去 TextMap 找，
    # 块只按匹配上的行画时它们露在块外，hsr 0:18 摆帧看见的）
    need = set(frames)
    allobs: dict[int, list[dict]] = {}
    with open(obs_path, encoding="utf-8") as f:
        next(f)
        for line in f:
            o = json.loads(line)
            if o["t_us"] in need:
                allobs.setdefault(o["t_us"], []).append(o)

    def grow_column(box: list[float], t: int, row_h: float) -> list[float]:
        """同一帧里 x 上落在块这一栏、上下挨着块（≤ 2.5 行高）的观测，一路并进块；台词正文框里的不并。"""
        cand = [o for o in allobs.get(t, ())
                if min(o["box"][2], box[2]) - max(o["box"][0], box[0]) >= 0.5 * max(1, o["box"][2] - o["box"][0])
                and not any(e["t_start"] <= t <= e["t_end"] and box_inside(o["box"], e["box"]) >= 0.8
                            for e in by_sec.get(t // 1_000_000, ()))]
        grew = True
        while grew:
            grew = False
            for o in cand:
                b = o["box"]
                if box[1] <= b[1] and b[3] <= box[3]:
                    continue
                if b[3] >= box[1] - 2.5 * row_h and b[1] <= box[3] + 2.5 * row_h:
                    box = [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
                    grew = True
        return box

    # **一整块**（owner 2026-09-12：逐段各盖各的观感不好）：同一帧里所有认出来的段并成一块，
    # 框 = 这些行的并集再长满这一栏，译文按段顺序（屏幕上从上到下）排、在块里重新折行（`export_overlay` 的 block 分支）。
    # 相邻帧段集合相同、块几乎不动就延长
    items: list[list] = []
    cur = None
    for t in sorted(frames):
        paras_here = sorted(frames[t], key=lambda p: min(o["box"][1] for o in frames[t][p]))
        obs = [o for p in paras_here for o in frames[t][p]]
        box = [min(o["box"][0] for o in obs), min(o["box"][1] for o in obs),
               max(o["box"][2] for o in obs), max(o["box"][3] for o in obs)]
        row_h = sorted(o["box"][3] - o["box"][1] for o in obs)[len(obs) // 2]
        box = grow_column(box, t, row_h)
        sig = tuple(paras_here)
        # 采样间隔有 ±1 µs 的抖动，接续判据留半帧余量（docs/dev-guide/pitfalls.md「时间戳比较留半个间隔的余量」）
        if cur and cur[5] == sig and cur[1] + frame_us // 2 >= t and box_iou(cur[2][0], box) >= 0.8:
            cur[1] = t + frame_us
        else:
            cur = [t, t + frame_us, [box], {"jp": [paras[keys[p]][0] for p in paras_here],
                                            "cn": [paras[keys[p]][1] for p in paras_here]}, row_h, sig]
            items.append(cur)
    return [{"layer": "panel", "start_us": s, "end_us": e, "rows": rows, "paras": ps, "row_h": row_h}
            for s, e, rows, ps, row_h, _ in items], n_used


def speaker_names(ref: GA.Ref) -> dict[str, str]:
    """说话人：日文（`SA.norm` 归一）-> 中文。名牌层的译文从这里取。

    先用剧本里 gamescript 写的**整库** `speakers` 表（这段视频圈中的剧本行常没有说话人，原神任务对话尤甚），
    表里没有的再从剧本行补（同一个名字取出现最多的写法）。库里没有说话人的（绝区零的 scene）就没有名字译文，
    名牌层照样判出框、只是 `cn` 为空、不叠。

    **旧剧本要拦**（2026-09-15 审计）：`speakers` / `speaker_cn` 是 09-14 才加进 `gamescript` 的。
    剧本早于那一版时这里会安安静静返回空表，名牌层于是**整层没有译文、`--lang cn` 把它全丢掉**，
    一行报错都没有——照文档的命令跑 gi-s2 得到 `名牌有中文名的 0/39`，而 matcher 计划 记的是 28。
    和"旧默认产的 obs 被静默复用"是同一个形状，所以这里明着喊，并把这件事写进产物
    （`overlay.stats.ref_has_speakers`），不让它只在终端上闪过。"""
    if "speakers" not in ref.doc:
        print("⚠ 剧本里没有 `speakers` 表（早于 2026-09-14 的 gamescript）：名牌层将没有任何译文。"
              "重跑 `flowocr.analyze.gamescript` 生成剧本再来。")
    cnt: dict[str, Counter] = defaultdict(Counter)
    for u in ref.doc["units"]:
        for ln in u["lines"]:
            if ln.get("speaker") and ln.get("speaker_cn"):
                cnt[SA.norm(ln["speaker"])][ln["speaker_cn"]] += 1
    names = {k: c.most_common(1)[0][0] for k, c in cnt.items() if k}
    table = {SA.norm(k): v for k, v in (ref.doc.get("speakers") or {}).items() if SA.norm(k) and v}
    return {**names, **table}


def term_names(ref: GA.Ref) -> dict[str, str]:
    """短名词表：日文（`SA.norm` 归一）-> 中文。说话人表（`speaker_names`）查不到的名牌再查它。

    表是 gamescript 从文本包的 `terms` 里挑的——只收这段 obs 里逐字出现过的键、消歧也在那边做完
    （`gamescript.term_table`）；旧包 / 旧剧本没有这一项，返回空表，名牌层照旧只查说话人。
    它按**字形**查到的是游戏自己的译文，但不保证那块字是人名（任务目标、称号也会查到），
    所以名牌条目上记 `name_src`，下游分得清"剧本说话人"和"按字形查到的"。"""
    return {SA.norm(k): v["cn"] for k, v in (ref.doc.get("terms") or {}).items() if SA.norm(k) and v.get("cn")}


NAME_BODY_GAP = 2.0
"""名牌下沿到正文上沿最多隔几个名牌字高（`name_over_body`）。"""


def name_over_body(name: dict, body: dict) -> bool:
    """名牌挂在这行正文上：正文在名牌中线以下、上沿离名牌下沿不超过 `NAME_BODY_GAP` 个字高，
    左右有重叠或左边缘相距不超过两个字高（居中的名牌、左对齐的名牌都算）。
    只看几何、不看名字（第 13 条）；并排一行的角色列表、离得远的键位和菜单标题对不上这个形状。"""
    nb, bb = name["box"], body["box"]
    h = max(1, nb[3] - nb[1])
    if not (bb[1] >= (nb[1] + nb[3]) / 2 and bb[1] - nb[3] <= NAME_BODY_GAP * h):
        return False
    return min(nb[2], bb[2]) - max(nb[0], bb[0]) > 0 or abs(nb[0] - bb[0]) <= 2 * h


SAME_PLACE = 0.5
"""一段叠加块里每条 cue 的正文都要和这一段**实际画的那个框**在同一处：较小那个外接框至少这么多面积落在另一个里（`position_segments`）。
打字机长字是小框落进大框（1.0），画面里跟着角色走的对白一挪就是 0。"""


def _ubox(evs: list[dict]) -> list[float]:
    return [min(e["box"][0] for e in evs), min(e["box"][1] for e in evs),
            max(e["box"][2] for e in evs), max(e["box"][3] for e in evs)]


def _same_place(a: list[float], b: list[float]) -> bool:
    return max(box_inside(a, b), box_inside(b, a)) >= SAME_PLACE


def position_segments(parts: list[tuple[int, list[dict]]], cues: list, s_us: int, e_us: int
                      ) -> list[tuple[list[dict], list[list[float]], int, int]]:
    """一条认领（`merge_runs` 可能并了好几条 cue）的正文按**位置**切成几段叠加块：(事件, 行框, 起, 止)。

    认领是"这几条 cue 是同一句剧本行"，渲染块是"译文画在哪、画多久"——两件事。区域可以包含跟着画面走的字，
    按区域并起来的几条 cue 不一定在同一处（2026-09-26 审计：hsr-s1 一句对白 2 s 里换了四个位置，
    并成一块后整段画在第一处）。一段画的是行数最多的那条 cue 的行框（同样多取先到的）；一条 cue 进这一段的条件是
    加进来之后**段里每条 cue** 都和那时要画的框在同一处（`SAME_PLACE`）——只和上一条比没有传递性，
    慢慢漂的字每一步都像"同一处"，几步之后就完全离开了画的那个框（Codex 复审的反例）；画的框因行数变多而换掉时，
    已在段里的也要重核。段内空档照旧补上；另起一段时两段之间的空档不画——那段时间字在动或不在，不能凭空画在旧位置。
    只有一段时起止就是记录的起止，和切段之前逐项相同。`parts` 是 (cue 下标, 这条 cue 的正文事件)，按时间排。"""
    groups: list[list[tuple[int, list[dict], list[float], int]]] = []

    def render_of(g):                                  # 行数最多的那条（同样多取先到的），和下面取 rows 的规则一致
        best = g[0]
        for m in g[1:]:
            if m[3] > best[3]:
                best = m
        return best

    for i, evs in parts:
        if not evs:
            continue
        cand = (i, evs, _ubox(evs), len(rows_of(evs)))
        if groups:
            trial = groups[-1] + [cand]
            rb = render_of(trial)[2]
            if all(_same_place(m[2], rb) for m in trial):
                groups[-1].append(cand)
                continue
        groups.append([cand])
    out = []
    for k, g in enumerate(groups):
        evs = [e for m in g for e in m[1]]
        rows = rows_of(render_of(g)[1])
        a = s_us if k == 0 else int(round(round(cues[g[0][0]].start, 3) * 1e6))
        b = e_us if k == len(groups) - 1 else int(round(round(cues[g[-1][0]].end, 3) * 1e6))
        out.append((evs, rows, a, b))
    return out


def overlay_items(recs: list[dict], doc: dict, evs_of: list[list[dict]], cues: list,
                  ref: GA.Ref | None = None, textmap_dir: Path | None = None,
                  obs_path: Path | None = None) -> tuple[list[dict], dict]:
    """**判定**：各层该盖哪些块，产出写进 `matched.json` 的 `overlay.items`（语言无关，jp / cn 都带）。
    导出器（`export_overlay`）只按层和语言投影，不再现场判断（artifacts.md 第 1 条）。返回 (条目, 各层计数)。

    顺序 body → choice → panel → extra → name：前三层与 2026-09-14 之前 `export_overlay` 现场判出来的
    **顺序与内容相同**（开 body,choice,offmain 时叠加 ASS 与之前逐字节相同，带 `--readable-textmap` 再加 panel 也相同，
    matcher 计划的回归；那两道 offmain 过滤在那两段切片上不触发），新两层排在后面。
    ⚠ **保持关注**（product-goals 第 16 条）：选项 / 面板的规则是按预览摆帧逐条长出来的，别再按单帧补。"""
    heads = GA.head_lines([(c.start, "\n".join(c.lines), "") for c in cues])
    body_items, extra_items = [], []
    for r in recs:
        if not r["ref"]:
            continue
        s_us, e_us = int(round(r["start"] * 1e6)), int(round(r["end"] * 1e6))
        parts = [(i, body_of(evs_of[i], heads, r["ref"].get("jp") or "")) for i in r["cues"]]
        body = [e for _, b in parts for e in b]
        for evs, rows, a, b in position_segments(parts, cues, s_us, e_us):
            body_items.append({"layer": "body", "start_us": a, "end_us": b, "rows": rows,
                               "events": [e["id"] for e in evs], "key": r["ref"]["key"],
                               "jp": r["ref"].get("jp"), "cn": r["ref"].get("cn")})
        # extra 盖它自己的那几行：字落在这条剧本行里、而且不是主认领已经用掉的事件
        taken = {e["id"] for e in body}
        for x in r["extra"]:
            parts = [(i, [e for e in body_of(evs_of[i], heads, x.get("jp") or "") if e["id"] not in taken])
                     for i in r["cues"]]
            for evs, xrows, a, b in position_segments(parts, cues, s_us, e_us):
                taken |= {e["id"] for e in evs}
                # 时间取**它自己那几个事件**的（裁进这一段的范围）：选项按钮往往比正文晚出现好几秒，
                # 用记录的起止会让译文先浮在空处（2026-09-14 摆帧：zzz 18 条里 17 条事件时段不到记录的 80%）
                x_s = max(a, min(e["t_start"] for e in evs))
                x_e = min(b, max(e["t_end"] for e in evs))
                extra_items.append({"layer": "extra", "start_us": x_s, "end_us": max(x_s, x_e), "rows": xrows,
                                    "events": [e["id"] for e in evs], "key": x["key"],
                                    "jp": x.get("jp"), "cn": x.get("cn")})
    # 名牌：主轨里被认成开头行的**事件**（一个事件一块，时间取事件自己的——名牌跨好几条 cue 挂着）。
    # `名前：台词` 那种同一行格式框不出名字那半，不进这一层
    names = speaker_names(ref) if ref is not None else {}
    terms = term_names(ref) if ref is not None else {}
    name_items, seen = [], set()
    for evs in evs_of:
        for e in evs:
            k = SA.norm(e["text"])
            if k not in heads or e["id"] in seen:
                continue
            # owner 2026-09-14：名牌几乎总和正文一起出现——同一条 cue 里要有一行正文挂在它下面（`name_over_body`），
            # 挡掉被 head_lines 认进来的键位 / UI（hsr 整场 `回` 108→21、`A` 84→12、`D` 32→0，matcher 计划）。
            # 同一个事件在别的 cue 里满足也算，所以只在收下时记 seen
            if not any(b is not e and SA.norm(b["text"]) not in heads and evalkit.content_len(b["text"]) >= 3
                       and name_over_body(e, b) for b in evs):
                continue
            seen.add(e["id"])
            # 名字对上了说话人表（或短名词表）才算"有译文"：没对上的名牌这一层不出译文（jp 口径也不拿 OCR 读数冒充"原文"）；
            # 原文另存 ocr。`dev` / `default_all` 会把这类事件当"没叠译文"的照 tracks 画 OCR 原文（overlay.unmatched_items）
            cn, src = (names[k], "speakers") if names.get(k) else (terms[k], "terms") if terms.get(k) else (None, None)
            name_items.append({"layer": "name", "start_us": e["t_start"], "end_us": e["t_end"],
                               "rows": [list(e["box"])], "events": [e["id"]],
                               "jp": e["text"] if cn else None, "cn": cn, "name_src": src,
                               "ocr": e["text"]})
    name_items.sort(key=lambda it: it["start_us"])
    choices: list[dict] = []
    if ref is not None:
        main = tracksio.main_track(doc)
        mr = set(main.get("regions") or [main.get("region")]) if main else set()
        choices = choice_items(doc, mr, ref)
    panels, n_obs = [], 0
    if textmap_dir:
        # 台词先认领自己的正文事件，面板只拿剩下的：TextMap 里的长条目也包含对话台词本身，
        # 第一版面板先挑，3550 个事件里有台词的正文，打字机叠加从 479 条掉到 272 条
        taken_ev = [doc["events"][i] for it in body_items for i in it["events"]]
        panels, n_obs = readable_items(obs_path, textmap_dir, doc["frame_us"], taken_ev)
        # 面板块里的"选项"不是选项：面板小标题 `ブラーチ` / `羊飼い` 归一后和选项按钮逐字相等，块已经盖了它们
        blocks = [(p["start_us"], p["end_us"], p["rows"][0]) for p in panels]
        choices = [c for c in choices if not any(bs < c["end_us"] and c["start_us"] < be
                                                 and box_inside(c["rows"][0], bb) >= 0.8 for bs, be, bb in blocks)]
    items = body_items + choices + panels + extra_items + name_items
    stats = {k: sum(1 for it in items if it["layer"] == k) for k in LAYERS}
    stats["name_translated"] = sum(1 for it in name_items if it["cn"])
    stats["name_from_terms"] = sum(1 for it in name_items if it["name_src"] == "terms")
    stats["panel_obs_used"] = n_obs
    # 剧本有没有说话人表：没有的话名牌层结构上一条译文都不会有（`speaker_names` 的 ⚠），
    # 记进产物，免得下次看见 `name_translated 0` 去查判据
    stats["ref_has_speakers"] = ref is not None and "speakers" in ref.doc
    return items, stats


def run_match(ref: GA.Ref, subs: str, feed: str, feed_panel: str, fuzzy: float, merge_gap: float,
              overlay: bool = True, readable_textmap: str | None = None, obs: str | None = None,
              doc: dict | None = None) -> dict:
    """匹配的**主体**：喂入 → 匹配 → 并相邻 → （有框时）判叠加层。**不打印统计、不写文件。**

    CLI（`main`）和内置匹配器（`flowocr.analyze.matchers.gametext.match`）都走这里，判据只有这一份
    （2026-09-22 从 main 里拆出来，正规化 project-structure）。`subs` 是 SRT 或 `*-tracks.json` 的路径；
    `doc` 给了就不再从 `subs` 读（匹配器 API 手上已经有 doc），`feed == "main"` 仍要 `subs` 指到文件。
    返回 `recs` / `n_raw` / `suspect_raw` / `cues` / `items` / `ov_stats` / `doc_t` / `subs_path` / `tp`。"""
    panel_of = None
    if feed == "main":
        subs_path, tp = GA.resolve(subs)
        cues = srtio.read_subs([subs_path])
        evs_of = None                      # 下面按主轨和 SRT 逐条对账之后再取
    else:
        if doc is None and not subs.endswith("-tracks.json"):
            raise SystemExit(f"--feed {feed} 要从一级产物投影，--subs 必须给 *-tracks.json")
        # **全量喂入不要求"有合格主轨"**（2026-09-18 审计）：`GA.resolve` 会在 `main_srt` 为空时直接退出，
        # 而"挑不出主轨、但屏幕上仍有可匹配文字"是合法产物——那正是全量喂入要覆盖的情形。
        # 所以这条路只从 tracks + provenance 取，不过 resolve。
        if doc is None:
            doc = tracksio.load(Path(subs))
        prov = doc["provenance"]
        subs_path = Path(subs) if subs else Path("")
        tp = {k: prov.get(k) for k in ("git_head", "code_fp")}
        cues, evs_of, panel_of = feed_from_doc(doc, feed, feed_panel)
        print(f"[喂入] --feed {feed}：{len(cues)} 条 cue（从 tracks.json 投影，"
              f"并带的那条聚合轨已排除；没并带时不排——那是真实区域）")
    recs, _score = match(ref, cues, fuzzy, panel_of, feed_panel)
    n_raw = len(recs)
    suspect_raw = sum(1 for r in recs if r["ref"] and r["how"] != "contain" and r["ref"]["score"] < SUSPECT)
    recs = merge_runs(recs, merge_gap, [c.source for c in cues])
    # 叠加层的判定（有框才判得了）：写进 JSON，--ass 只投影
    items = ov_stats = doc_t = None
    if (doc is not None or subs.endswith("-tracks.json")) and overlay:
        doc_t = doc if doc is not None else tracksio.load(Path(subs))
        items, ov_stats = overlay_items(recs, doc_t,
                                        cue_events(doc_t, cues) if evs_of is None else evs_of,
                                        cues, ref=ref,
                                        textmap_dir=Path(readable_textmap) if readable_textmap else None,
                                        obs_path=Path(obs) if obs else None)
    return {"recs": recs, "n_raw": n_raw, "suspect_raw": suspect_raw, "cues": cues, "items": items, "ov_stats": ov_stats,
            "doc_t": doc_t, "subs_path": subs_path, "tp": tp}


def match_stats(ref: GA.Ref, recs: list[dict], suspect_raw: int | None = None) -> dict:
    """产物 `stats` 和报表共用的那几个数（覆盖 / 重复认领 / 可疑改写的分布），只算一次。

    `suspect_raw`：`merge_runs` 之前的可疑改写条数（`run_match` 给）。合并留组内最高的分，低分那条的报警
    随合并消失、文字和时段却没改——所以合并前后两个数一起报，合并改动不能只看合并后的（2026-09-26 审计）。"""
    how = Counter(r["how"] for r in recs)
    conf = Counter(r["conf"] for r in recs)
    # 覆盖按**代表行**数，才和 game_align 的命中同口径：产品输出的是屏幕上那一种说法
    # （`pick_variant`），而分母里一个变体组只有代表行那一条
    claimed = {ref.variant_of.get(k, k) for k in
               ({r["ref"]["key"] for r in recs if r["ref"]} | {e["key"] for r in recs for e in r["extra"]})}
    band = [e for e in ref.script if GA.band(e)]
    n_band_claimed = sum(1 for e in band if e.key in claimed)
    # 导出契约：SRT 只出主 ref。分母内只由 `extra` 认领的行**不会出现在字幕里**，
    # 所以"覆盖"和"SRT 实际出的"是两个数（模块说明）。
    main_keys = {ref.variant_of.get(r["ref"]["key"], r["ref"]["key"]) for r in recs if r["ref"]}
    n_band_main = sum(1 for e in band if e.key in main_keys)
    n_extra_recs = sum(len(r["extra"]) for r in recs)
    # **按最终记录数，不按 `st`**（2026-09-18 审计）：`st` 是 `game_align.score` 的原始认领，
    # 而 `--feed-panel strict` 会**撤销**一部分认领、`merge_runs` 会并掉相邻的同一行——
    # 拿 `st` 算出来的"重复认领"于是和产物里真出现的次数不是一回事（strict 臂上它一点不动，看着像没效果）。
    keys = [r["ref"]["key"] for r in recs if r.get("ref")]
    keys += [x["key"] for r in recs for x in r.get("extra", ())]
    ci = Counter(keys)
    groups, extra = sum(1 for v in ci.values() if v > 1), sum(v - 1 for v in ci.values())
    scores = [r["ref"]["score"] for r in recs if r["ref"] and r["how"] != "contain"]
    return {"how": how, "conf": conf, "band": band, "n_band_claimed": n_band_claimed,
            "n_band_main": n_band_main, "n_extra_recs": n_extra_recs, "groups": groups, "extra": extra,
            "scores": scores, "suspect_raw": suspect_raw}


def report(st: dict, recs: list[dict], n_raw: int, merge_gap: float, fuzzy: float,
           ov_stats: dict | None, readable_textmap: str | None) -> None:
    """CLI 的统计打印（只打印，什么都不算——数都在 `match_stats` 里）。"""
    how, conf, band, scores = st["how"], st["conf"], st["band"], st["scores"]
    n_band_claimed, n_band_main, n_extra_recs = st["n_band_claimed"], st["n_band_main"], st["n_extra_recs"]
    print(f"cue {n_raw} 条 -> 并掉同一条剧本行的相邻 cue 之后 {len(recs)} 条（--merge-gap {merge_gap}）：" + "、".join(f"{k} {how[k]}" for k in ("exact", "fuzzy", "contain", "none"))
          + f"；置信度 " + "、".join(f"{k} {conf[k]}" for k in ("high", "med", "low", "none")))
    print(f"覆盖：该上屏的 {len(band)} 条里 {evalkit.denom(n_band_claimed, len(band))} 被认领"
          f"（和 game_align 的命中同口径）")
    print(f"  其中**拍平的 SRT 里出现的** {evalkit.denom(n_band_main, len(band))}："
          f"第二条剧本行（`extra`）共 {n_extra_recs} 条不进 SRT（其中分母内、没有别的 cue 认领的 "
          f"{n_band_claimed - n_band_main} 条）；"
          + (f"叠加 ASS 的 extra 层找到框的 {ov_stats['extra']} 条" if ov_stats else "给 tracks.json 才有叠加层"))
    if ov_stats:
        print("叠加层（判定写进 --out 的 overlay.items，导出时按预设的 layers 开关）："
              + "  ".join(f"{k} {ov_stats[k]}" for k in LAYERS)
              + f"；名牌有中文名的 {ov_stats['name_translated']}/{ov_stats['name']}"
              + f"（其中查短名词表的 {ov_stats['name_from_terms']}）"
              + ("" if ov_stats["ref_has_speakers"] else "（**剧本没有说话人表**，这一档结构上就是 0）")
              + (f"；阅读面板用掉 {ov_stats['panel_obs_used']} 条观测" if readable_textmap else ""))
    print(f"重复认领：{st['groups']} 条被 ≥2 条 cue 认领，多出 {st['extra']} 次（报警不是错误数）")
    if scores:
        if fuzzy >= SUSPECT:
            print(f"  ⚠ --fuzzy {fuzzy} ≥ 可疑改写的门 {SUSPECT}：下面那个数结构上恒为 0，别读（见 SUSPECT）")
        print("改写幅度（被换成原文的 cue 与剧本行的相似度；`contain` 不在分布里——整行都在 cue 里，"
              "相似度不是它的判据）："
              + "  ".join(f"P{q*100:.0f} {evalkit.fmt_pctl(scores, q, unit='')}"
                          for q in (0.05, 0.25, 0.5))
              + f"；<{SUSPECT} 的 {sum(1 for s in scores if s < SUSPECT)} 条**是可疑改写，要摆帧**"
              + (f"（合并前 {st['suspect_raw']} 条）" if st.get("suspect_raw") is not None else "")
              + f"（`--fuzzy {fuzzy}` 已经把更低的挡在门外，所以这条线必须更高）")


def matched_doc(ref: GA.Ref, recs: list[dict], n_raw: int, st: dict, ref_path: str, subs_path: Path, tp: dict,
                argv: list[str], params: dict, items, ov_stats) -> dict:
    """`*-matched.json`（`SCHEMA`）。provenance 记这次是谁、用哪版代码、什么参数产的。"""
    from flowocr.analyze import build_tracks as bt
    pv = ref.doc["provenance"]
    scores = st["scores"]
    return {"schema": SCHEMA,
            "provenance": {"tool": "scriptmatch.py", "version": bt.version(), "git_head": bt.git_head(),
                           "code_fp": bt.code_fp(CODE_FP_FILES), "argv": [bt.portable(x) for x in argv],
                           "ref": bt.portable(ref_path), "ref_code_fp": pv.get("code_fp"),
                           "subs": bt.portable(subs_path), "tracks_code_fp": tp.get("code_fp"),
                           "game": pv.get("game"),
                           # 剧本出自哪份语料（gamescript 从文本包清单抄的）；通用剧本没有这一项
                           "gametext": {k: pv["gametext"].get(k) for k in ("fingerprint", "rev", "version", "source")}
                           if pv.get("gametext") else None,
                           "params": {"fuzzy": params["fuzzy"], "merge_gap": params["merge_gap"], "feed": params["feed"],
                                      "feed_panel": params["feed_panel"]}},
            "stats": {"cues": len(recs), "cues_raw": n_raw, "how": dict(st["how"]), "conf": dict(st["conf"]),
                      "band_entries": len(st["band"]), "band_claimed": st["n_band_claimed"],
                      "band_claimed_exported": st["n_band_main"], "extra_records": st["n_extra_recs"],
                      "suspect_below": SUSPECT,
                      "suspect": sum(1 for s in scores if s < SUSPECT), "suspect_raw": st.get("suspect_raw"),
                      "repeat_groups": st["groups"], "repeat_extra": st["extra"]},
            "cues": recs,
            **({"overlay": {"layers": list(LAYERS), "stats": ov_stats, "items": items}} if items is not None else {})}



def main() -> int:
    # --help 只放用法；实现上的判断在模块文档串里，给读代码的人（发布前清理，Opus 审查 O6）
    ap = argparse.ArgumentParser(description=CLI_DESCRIPTION,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref", help="gamescript.py 的剧本 JSON")
    ap.add_argument("--subs", required=True, help="主轨：SRT 或 `*-tracks.json`（取 provenance 的 main_srt）")
    # nonoise 是 2026-09-20 翻的默认，依据两条：①owner 09-17 定的一般形态是『保留全部非噪音文本 + 聚类 + 排序』，
    # 匹配器只是特化的聚类规则、它前面不该有有损过滤；②实测 hsr 整场覆盖 78.0% -> 89.2%，09-18 三段切片验收覆盖只增不减
    # （zzz-s1 +1、其余 ±0），摆帧 24 张确认全量喂入没有把 UI 涂满。它还顺带解掉了 --slot-reassign 的主要代价：
    # 主轨口径下丢的那批行，nonoise 下 f2 看得见 64/66、f4 看得见 37/37（handoff-2026-09-20）
    ap.add_argument("--feed", default="nonoise", choices=FEEDS,
                    help="喂给匹配器的 cue 流：nonoise（默认，除 noise 的全部区域轨）/ "
                         "main（只喂主轨，回退用）/ all（连 noise）。nonoise / all 要 --subs 给 *-tracks.json。"
                         "注意用途 2 的尺（`script_align` / `game_align`）量的仍是主轨 SRT，和产物喂入的口径不同")
    ap.add_argument("--feed-panel", default="keep", choices=("keep", "drop", "strict"),
                    help="喂入时怎么处理 build_tracks 标了 `panel_like` 的 cue（对话记录面板那种："
                         "文本真、时间错）：keep（默认，现状）/ drop（整条不喂）/ "
                         "strict（喂，但只认强匹配，见 PANEL_MIN_SCORE）")
    ap.add_argument("--out", required=True, help="匹配结果 JSON（一条 cue 一条记录）")
    ap.add_argument("--srt", default=None, help="顺便导出一份 SRT")
    ap.add_argument("--lang", choices=("jp", "cn"), default="jp", help="导出的正文取哪种语言")
    ap.add_argument("--fuzzy", type=float, default=0.75, help="同 game_align")
    ap.add_argument("--merge-gap", type=float, default=MERGE_GAP,
                    help="相邻 cue 认的是同一条剧本行、间隔 ≤ 这么多秒就并成一条（0 = 不并，见 merge_runs）")
    ap.add_argument("--ass", default=None,
                    help="顺便导出用途 2 的叠加 ASS：把 --lang 的原文压在原字幕的框上（--subs 必须给 tracks.json）；"
                         "旁边另有一份可编辑的字幕稿（.ass 换成 .script.ass）")
    ap.add_argument("--preset", default="default",
                    help="--ass 用哪个预设（同 render --preset：内置名 / 模块名 / .py 路径）。内置叠加预设："
                         "default（默认，body / extra / name 三层、底板 α 0x20）、default_all、"
                         "dev（全部层、非主轨浅蓝、聚类编号、底板 50%%）。见 flowocr.output.presets._overlay")
    ap.add_argument("--opt", action="append", default=[], metavar="K=V",
                    help="给预设的选项，可多次：plate / plate_alpha / typewriter / fade / layers（同 render --opt）")
    ap.add_argument("--readable-textmap", default=None,
                    help="**实验**：game-text-data 的 upstream/<游戏>/TextMap 目录；给了就把阅读面板这类长文本也盖上（见 readable_items）")
    ap.add_argument("--obs", default=None, help="--readable-textmap 要逐帧的观测（run_ocr2 的 jsonl）")
    ap.add_argument("--no-overlay", action="store_true",
                    help="不判叠加层、`--out` 里不写 `overlay`（只要 SRT / 只要认领统计时用）。"
                         "默认判——给了 `-tracks.json` 就判，`--ass` 只是投影")
    a = ap.parse_args()
    if a.readable_textmap and not a.obs:
        raise SystemExit("--readable-textmap 要逐帧观测，同时给 --obs")
    if (a.ass or a.readable_textmap) and not a.subs.endswith("-tracks.json"):
        raise SystemExit("--ass / --readable-textmap 要框，--subs 必须给 *-tracks.json")
    if a.ass and a.no_overlay:
        raise SystemExit("--ass 投影的就是叠加层，不能同时给 --no-overlay")
    opts = {}
    for kv in a.opt:
        if "=" not in kv:
            ap.error(f"--opt 要 K=V：{kv}")
        k, v = kv.split("=", 1)
        opts[k] = v
    ass_preset = None
    if a.ass:
        # 匹配跑之前就把预设加载好、核它收不收 matched：跑完几分钟才发现 `--preset srt_main` 在预设里炸 KeyError 不划算
        from flowocr import extensions
        ass_preset = extensions.load(a.preset, "preset")
        accepts = getattr(ass_preset, "ACCEPTS", None)
        if accepts is not None and matchedio.SCHEMA not in accepts:
            ap.error(f"--preset {a.preset} 吃的是 {list(accepts)}，不收匹配产物 {matchedio.SCHEMA}")

    ref = GA.load_ref(Path(a.ref))
    res = run_match(ref, a.subs, a.feed, a.feed_panel, a.fuzzy, a.merge_gap,
                    overlay=not a.no_overlay, readable_textmap=a.readable_textmap, obs=a.obs)
    recs, items, ov_stats = res["recs"], res["items"], res["ov_stats"]
    st = match_stats(ref, recs, res["suspect_raw"])
    report(st, recs, res["n_raw"], a.merge_gap, a.fuzzy, ov_stats, a.readable_textmap)
    doc = matched_doc(ref, recs, res["n_raw"], st, a.ref, res["subs_path"], res["tp"], sys.argv[1:],
                      {"fuzzy": a.fuzzy, "merge_gap": a.merge_gap, "feed": a.feed, "feed_panel": a.feed_panel},
                      items, ov_stats)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {p}")
    # SRT / ASS 都是**阶段 3 的投影**，走内置预设那一份实现（2026-09-22 Codex 审计 P2：
    # 同一份产物换预设不该重跑匹配，于是投影只能有一处；这里只是把文件名交给它）
    from flowocr.output.presets import matched_srt as _srt
    if a.srt:
        out = Path(a.srt)
        for w in _srt.render(doc, out.parent, {"lang": a.lang, "name": out.name}):
            print(f"-> {w}（{srtio.count_cues(w)} 条）")   # 条数从写出的文件数（srtio 的唯一口径），不拿 len(recs) 代
    if a.ass:
        out = Path(a.ass)
        ass_preset.render(
            doc, out.parent, {**opts, "lang": a.lang, "tracks": str(res["subs_path"]), "name": out.name})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
