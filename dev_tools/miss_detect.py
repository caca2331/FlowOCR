"""疑似漏字幕判断器（miss-detector 计划 S2）：屏幕上出现过、OCR 也读到了、最终输出里却没有的字，逐条带时刻、类型、丢在哪一层。

"最终输出里有没有"按**显示协议**判（`flowocr.artifacts.matchedio`）：`overlay.items` 里有一个按默认可见规则画出来的条目认领这一行
（或同组变体），且有所选语种的文本。分母是适配层给的"该出现的行"，不只字幕带台词：选项、过场、黑屏旁白、界面文字都在，按组分栏。

每条该出现的行按这个先后落进且只落进一类：
* **已输出**（其中填位单列）；
* **另一版本已输出**：同一时间窗里有一条几乎一样的行画出来了（`sibling`）；或者（只对读到过、组可见的行）
  定位到的那段字落在它的 cue 认的那一行里，且定位不弱；适配层给了说话人表（naninovel）时两条路都要说话人相同；
* **匹配器决定不显示**：`group_of` 给这一行的组默认不可见（只靠 `elsewhere` 条目画到的台词，行组是 `dialogue`，不在这一类，照样算漏）；
* **漏**：读到过、没输出，带**定层**——画不出来（组不可见 / 填位不画 / 没有该语种文本）、认领了没生成条目、建轨没留下、
  定位弱（只找到一小段，`WEAK`）、没喂、喂了认成别的行、喂了没认上；主轨外的事件另带"选项层为什么没出块"；
* **没读到**：第二层的候选，带原因：不到 3 个内容字又没被认领（太短判不了）、有锚点但时段里搜不到读数（锚点没有读数支撑）、
  整行是一个说话人名（判不了）。

另出 **B 清单**（文本包外的字）：`unmatched` ∪ `unfed` 的事件，去掉常驻 UI、注音、不到 3 个内容字的，扣掉任一该出现行定位到的，按区域汇总，
再用 gamescript 的检索把"文本包里有、单元没圈中"的单列。

自检三条不变量：叠加条目的剧本 key 都回得到剧本行、引用的事件都在 tracks 里；每条"漏"都带定层；一个事件同一时刻只画一块。
**报的漏条只是线索**：检测器复用了匹配器的判据（锚点、包含度、定组），摆帧才算数（计划「按结果改匹配器」）。

适配层只回答三件事（该出现的行、读到过没有、归哪个组），按匹配产物的 `provenance.tool` 选：
* `gametext`（`GametextAdapter`）：圈中单元里的行，时段来自 gamescript 的 `sequence`；
* `naninovel`（`NaninovelAdapter`）：没有锚点、没有时段。分母是参照字幕（`--span-ref`，正文是剧本原文，同 `script_align --span-ref`）
  圈出的剧本区间里、匹配器认得了的字幕带行；时间窗由剧本顺序上前后已输出行夹出（输出变了窗也变，报数时写明）；读到过照样在窗里搜 obs。

用法：
    python dev_tools/miss_detect.py out/x/zzz/matched.json [--lang cn] [--show 20] [--out miss.json]
    python dev_tools/miss_detect.py <A matched.json> --vs <B matched.json>     # 两臂逐条比（剧本 provenance 不同就拒）
    python dev_tools/miss_detect.py out/x/f5/matched.json --span-ref data/corpus/mocai/game_text/lower5_jp.ass   # naninovel
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from flowocr import paths
from flowocr import provenance as PV
from flowocr.analyze import game_align as GA
from flowocr.analyze import gamescript as GS
from flowocr.analyze import script_align as SA
from flowocr.analyze import scriptmatch as SM
from flowocr.analyze.matchers import naninovel as NN
from flowocr.artifacts import evalkit, tracksio
from flowocr.artifacts import matchedio as MIO

OUT, SIB, OFF, MISS, UNREAD = "已输出", "另一版本已输出", "匹配器决定不显示", "漏", "没读到"
CATS = (OUT, SIB, OFF, MISS, UNREAD)
L_NOEV, L_NOFEED, L_OTHER, L_NONE = "建轨没留下", "没喂", "喂了、认成别的行", "喂了、没认上"
L_HIDDEN, L_NOITEM, L_WEAK = "画不出来", "认领了、没生成条目", "定位弱（只找到一小段）"
NAME_LIKE, NO_SUPPORT = "和说话人名同字，判不了", "锚点没有读数支撑"
LAYERS = (L_HIDDEN, L_NOITEM, L_NOEV, L_WEAK, L_NOFEED, L_OTHER, L_NONE)
WEAK = 0.5
"""定位到的字盖住这一行不到这么多（`Events.cover`）就不按事件定层，记"定位弱"：长句里只读到一个名字、一个界面词时，
`Feed.find` 的判法（字八成落在行里、取最长）会挑中这个词，再按它所在的 cue 定层就是错的——S2 评审在四场上数到
"另一版本已输出（同一段字）"里 cover < 0.5 的 1 / 7 / 11 / 25 条，hsr 一句台词定位到界面词 `推奨次元界オーナメント`（0.09）。
0.5 是先取的，S3 摆帧再定。"""
SKIP_STATUS = ("Unwalked", "Ambiguous", "Duplicate", "OutOfSpan")
"""处境是这几种的行不进分母：没走的分支、判不出走没走、重演拷贝、单元里这段视频没播的那几截。`Variant` 记到代表行上。"""
WALKED = (None, "Variant", "OffBand")
"""选项前面那行的处境是这几种，才算这段视频播到了这个分支点（见 `select_lines`）。"""
LONG_EV = 600.0
"""按时间窗找事件时往前回看多少秒：比这更长的事件（挂一整场的 UI）不拿来定位。"""
JOIN_GAP = 0.6
"""obs 搜读数：同一帧里上下相邻的两个框，竖直间隙不超过矮的那个框高的这么多倍、左右有重叠，算同一处、拼起来读。"""
JOIN_MAX = 3
"""拼起来的块内容字超过这一行的这么多倍（再加 6）就不拿整块比：对话记录面板这种大块，零散的字总能凑够一半。"""
B_MIN = 3
"""B 清单的"有意义"门：事件里假名 / 汉字至少这么多个（同三档的"有内容"，但只数假名汉字）。只数内容字的话，
gi1 的候选七成是 `Lv.102`、`35536/35536`、`9 ms` 这类数字和英文缩写。只对日文素材成立，别的语种要换判据。"""
SPAN_WINDOW, SPAN_FUZZY = 400, 0.75
"""naninovel 圈分母时参照字幕对剧本的游标窗与模糊门，同 `script_align` / `hit_delta` 的默认。"""
SPAN_GAP, SPAN_MIN = 200, 5
"""naninovel 圈分母：参照字幕对上的剧本下标排好序，隔 > `SPAN_GAP` 行就断成两段，不到 `SPAN_MIN` 条的段当误对丢掉，
分母是留下的各段（首到尾）里的字幕带行。`script_align --span-ref` 取全部对上的最前到最后，一条误对就能把没播的整章圈进来：
《魔裁》第 5 部 `lower5_jp.ass` 有 1 条对到了第二幕第 1 章，最前到最后是 19751–33878，字幕带行 12550 条（有内容档 11944，检测器覆盖 22.9%）；
分段后是 27229–27365 / 29058–29635 / 29939–33878 三段、3961 条（有内容档 3700）。"""
KANA_HAN = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u3400-\u9fff]")       # 不含长音 `ー`、中点 `・`
PACK_CONTAIN = 0.85
"""B 清单的文本包检索：事件的字有这么多落在库里某一行里算"文本包里有"（同 gamescript 的 `--min-contain`）。"""
CODE_FP_FILES = tuple(sorted({"dev_tools/miss_detect.py", *SM.CODE_FP_FILES, *NN.CODE_FP_FILES}))
"""检测器自己 + 它借判据的两个匹配器的文件：跨臂报表比它（`code_fp`），检测器的判法变了，两臂的分类就不是同一把尺量的。"""



# ---------------------------------------------------------------- 共用件

PAD = 2.0
"""时间窗两头各放宽几秒：认领的起止是 cue 的边界，字常常早一点上屏、晚一点消失。"""
FIND_IN = 0.8
"""事件的字有这么多落在剧本行里，才算"这一行在 tracks 里的事件"。"""
FIND_MIN = 3
"""事件至少这么多内容字才拿来找（1–2 字的碎片落在哪条长行里都像）。"""
CLOSEST = 0.5
"""`建轨没留下` 时退一步找的线索门槛：窗里字有这么多落在行里的事件，列出来给人看是不是改写过的另一版本。"""
SIBLING = 0.6
"""两条剧本行（内容归一后）相似到这个比例，算同一句的另一版本（`sibling`）。"""


def resolve(p: str) -> Path:
    q = Path(p.replace("\\", "/")).expanduser()   # provenance 里的相对路径可能是 Windows 写法；家目录下的写成 `~/…`（`build_tracks.portable`）
    return q if q.is_absolute() else paths.data_root() / q


def claim_times(recs: list[dict], variant_of: dict[str, str]) -> dict[str, list[tuple[float, float]]]:
    """剧本行（变体并到代表行）-> 它被认领的每一次 (起, 止)，主认领和第二行（`extra`）都算——和覆盖同口径。"""
    out: dict[str, list[tuple[float, float]]] = {}
    for r in recs:
        for k in ([r["ref"]["key"]] if r["ref"] else []) + [x["key"] for x in r["extra"]]:
            out.setdefault(variant_of.get(k, k), []).append((r["start"], r["end"]))
    return out


def claim_window(t: float, prev: list[tuple[float, float]] | None, nxt: list[tuple[float, float]] | None
                 ) -> tuple[float | None, float | None]:
    """这一行该在的时间窗：前一条已输出行离锚点时刻 `t` 最近、不晚于它的那次的结束，到后一条已输出行最近、不早于它的
    那次的开始。前后没有已输出行（剧本头尾）那一端就是 None。晚于 / 早于 `t` 的全都没有时退回离得最近的那次。"""
    lo = hi = None
    if prev:
        before = [e for s, e in prev if s <= t + PAD]
        lo = max(before) if before else min(e for _, e in prev)
    if nxt:
        after = [s for s, e in nxt if e >= t - PAD]
        hi = min(after) if after else max(s for s, _ in nxt)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def sibling(text: str, lo: float | None, hi: float | None, shown: list[tuple[float, float, str]],
            texts: dict[str, str], ok=lambda k: True) -> tuple[str, float] | None:
    """时间窗（放宽 `PAD`）里已输出的剧本行中，和这一行最像、相似 ≥ `SIBLING` 的那条：(key, 相似度)。窗两头都没有就不查。
    `ok(key)` 为假的已输出行不算（naninovel：说话人不同的不是同一句的另一版本）。"""
    if lo is None and hi is None:
        return None
    a = (lo if lo is not None else hi) - PAD
    b = (hi if hi is not None else lo) + PAD
    rt = GS.cnorm(text)
    best = None
    for s, e, k in shown:
        if s > b or e < a or not ok(k):
            continue
        r = SequenceMatcher(None, rt, GS.cnorm(texts.get(k, "")), autojunk=False).ratio()
        if r >= SIBLING and (best is None or r > best[1]):
            best = (k, round(r, 3))
    return best


def hms(t: float | None) -> str:
    if t is None:
        return "   —    "
    h, r = divmod(int(t), 3600)
    return f"{h}:{r // 60:02d}:{r % 60:02d}"


class Feed:
    """gametext 的喂法：事件 -> 所在区域轨 / cue、进没进匹配器、进了的话那条 cue 被认成了什么。"""

    def __init__(self, doc: dict, matched: dict, ref: GA.Ref):
        pr = matched["provenance"]["params"]
        self.doc = doc
        with contextlib.redirect_stdout(io.StringIO()):          # feed_from_doc 会打印喂入统计
            self.cues, evs_of, _ = SM.feed_from_doc(doc, pr["feed"], pr["feed_panel"])
        if len(self.cues) != matched["stats"]["cues_raw"]:
            raise SystemExit(f"投影出的 cue {len(self.cues)} 条，matched 记的是 {matched['stats']['cues_raw']} 条："
                             f"tracks 或喂法和产这份 matched 的不是同一份")
        self.fed_cue = {}
        for i, evs in enumerate(evs_of):
            for e in evs:
                self.fed_cue.setdefault(e["id"], i)
        self.rec_of = {i: r for r in matched["cues"] for i in r["cues"]}
        # 和匹配器用的同一份开头行（`GA.run` 带剧本行）
        self.heads = GA.head_lines([(c.start, "\n".join(c.lines), "") for c in self.cues],
                                   GA.band_lines(ref.script))
        self.label = {r["index"]: r.get("label") for r in doc["regions"]}
        self.where = {}                                           # 事件 -> (区域轨, cue)
        for tr in doc["tracks"]:
            if tr.get("kind") != "region":
                continue
            for c in tr["cues"]:
                for i in c["events"]:
                    self.where.setdefault(i, (tr, c))
        self.feed_mode, self.panel = pr["feed"], pr["feed_panel"]

    def why_not_fed(self, e: dict) -> str:
        tr, cue = self.where.get(e["id"], (None, None))
        if tr is None:
            return "不在任何区域轨的 cue 里"
        if self.feed_mode == "nonoise" and tr.get("label") == "noise":
            return "noise 区域"
        return self.cue_reason(e, tr, cue)

    def cue_reason(self, e: dict, tr: dict, cue: dict) -> str:
        """事件在喂入的那条轨的 cue 里、却没喂进去：cue 里藏起 / 注音 / 常驻 UI / 面板样。"""
        if e["id"] in (cue.get("hide") or ()):
            return "cue 里藏起的行（hide：打字机半截 / 杂行）"
        flags = set(e.get("flags") or ())
        if "ruby" in flags:
            return "按注音剔掉"
        if "ui_footprint" in flags or e["text"].strip() in set(tr.get("ui_lines") or ()):
            return "按常驻 UI 剔掉"
        if self.panel == "drop" and cue.get("panel_like"):
            return "面板样 cue 被剔"
        return "原因不明"

# ---------------------------------------------------------------- 适配层：gametext


@dataclass
class Line:
    """一条该出现的行（变体并到代表行上）。`windows` 是 gamescript `sequence` 给的时段（秒），代表行和变体的并在一起。"""
    key: str
    text: str
    kind: str
    status: str | None
    role: str | None
    uid: str
    cn: str | None
    t: float | None                        # gamescript `sequence` 的时刻；naninovel 没有（None，窗由输出夹出）
    windows: list[tuple[float, float]]
    anchored: bool = False                 # 有唯一查询的锚点（gamescript），代表行或任一变体
    texts: list[str] = field(default_factory=list)     # 代表行和变体的原文，搜读数时都算
    tpl: bool = False                      # 原文里有运行时才定的占位（`jp_tpl`，玩家名等）：屏幕上那段字比剧本原文多


def select_lines(ref_doc: dict) -> list[Line]:
    """gametext 的"该出现的行"：圈中单元里的行，排掉 `SKIP_STATUS`，变体并到代表行。

    选项（`Choice`）永远不会被记成 `OutOfSpan` / `Unwalked`（gamescript 只按非选项行判），所以另加一道：选项自己有锚点，
    或它前面最近的非选项行这段视频播过、走过（处境在 `WALKED` 里）——没走的分支里套着的选项、没播那截里的选项都排掉。
    单元开头的选项看后面最近的那行。剧本 JSON 里没有分支成员关系，这是从处境推的近似。"""
    seq = {k: (t, w) for k, t, w in ref_doc["sequence"]}
    out: dict[str, Line] = {}
    members: dict[str, list[dict]] = defaultdict(list)
    for u in ref_doc["units"]:
        lines = u["lines"]
        plain = [i for i, ln in enumerate(lines) if ln["kind"] != "Choice"]
        for i, ln in enumerate(lines):
            st = ln["status"]
            if st in SKIP_STATUS or ln["key"] not in seq:
                continue
            if st == "Variant":
                members[ln["variant_of"]].append(ln)
                continue
            a = ln.get("anchor")
            if ln["kind"] == "Choice" and not (a and a["unique"]):
                j = bisect.bisect_left(plain, i)
                nb = plain[j - 1] if j > 0 else (plain[j] if j < len(plain) else None)
                if nb is None or lines[nb]["status"] not in WALKED:
                    continue
            t, w = seq[ln["key"]]
            out[ln["key"]] = Line(ln["key"], ln["text"], ln["kind"], st, ln["role"], u["uid"], ln.get("cn"), t,
                                  [tuple(x) for x in w], bool(a and a["unique"]), [ln["text"]], bool(ln.get("jp_tpl")))
    for rep, ms in members.items():
        if rep not in out:
            continue
        for ln in ms:
            out[rep].windows += [tuple(x) for x in seq[ln["key"]][1]]
            out[rep].anchored |= bool(ln.get("anchor") and ln["anchor"]["unique"])
            out[rep].texts.append(ln["text"])
            out[rep].tpl |= bool(ln.get("jp_tpl"))
    order = {k: i for i, (k, _, _) in enumerate(ref_doc["sequence"])}
    return sorted(out.values(), key=lambda x: order[x.key])


def gametext_group(ln: Line) -> str:
    """这一行归哪个组：匹配器自己的定组函数（显示协议规则第 4 条，单一来源）。"""
    return SM.group_of(ln.kind, ln.status, ln.role)


class GametextAdapter:
    """`gametext` 匹配产物的适配层：剧本是 gamescript 的 `ref.json`（provenance 的 `ref`）。"""
    name = "gametext"
    choice = True                     # 主轨外的事件有选项层那条路（`choice_why`）
    output_windows = False            # 时段来自 gamescript，不由输出夹出
    # "另一版本"不看说话人：绝区零两位主角各说一版的台词正是另一版本，两版说话人不同
    speaker: dict[str, str | None] | None = None

    def __init__(self, m: dict, doc: dict):
        self.ref = GA.load_ref(resolve(m["provenance"]["ref"]))
        self.variant_of = self.ref.variant_of
        self.rep = lambda k: self.variant_of.get(k, k)
        self.lines = select_lines(self.ref.doc)
        self.by_key = {ln["key"]: ln for u in self.ref.doc["units"] for ln in u["lines"] if ln["status"] != "Duplicate"}
        self.texts = {ln["key"]: ln["text"] for u in self.ref.doc["units"] for ln in u["lines"]}
        self.feed = Feed(doc, m, self.ref)
        self.unit_span = SM.unit_spans(self.ref)
        self.prefix = SM.ChoicePrefix(self.ref, self.unit_span)
        self.names = {GS.cnorm(k) for k in self.ref.doc.get("speakers") or {}} - {""}
        # 搜读数时排掉"逐字是另一行 / 说话人名"的读数（`ObsText.read` 的 `others`），同 naninovel：没锚点的行借邻近锚点的窗、往后放宽 120 s，窗也宽
        self.others = (frozenset(GS.cnorm(ln["text"]) for u in self.ref.doc["units"] for ln in u["lines"]) | self.names) - {""}
        self.obs_path = resolve(self.ref.doc["provenance"]["obs"])
        self.provenance = ref_provenance(self.ref.doc)

    group = staticmethod(gametext_group)

    def window(self, ln: Line, prev, nxt) -> tuple[float | None, float | None]:
        return claim_window(ln.t, prev, nxt)

    def in_pack(self, evs: list[dict]) -> set[int]:
        """事件的字落在文本包某一行里的（gamescript 的检索）。"""
        _, _, ptexts, idx, _ = GS.library(self.ref.doc["provenance"]["game"])
        out = set()
        for e in evs:
            q = GS.cnorm(e["text"])
            if len(q) >= GS.QMIN_FLOOR and any(GS.contained(q, ptexts[j]) >= PACK_CONTAIN for j in idx.query(q)):
                out.add(e["id"])
        return out


class NaniFeed(Feed):
    """naninovel 的喂法：只喂主轨导出了的 cue（`naninovel.main_cues`），记录的 `cues` 是它的下标。其余同 `Feed`。"""

    def __init__(self, doc: dict, matched: dict, script_lines: frozenset[str]):
        pr = matched["provenance"]["params"]
        self.doc = doc
        self.cues, evs_of, _ = NN.main_cues(doc, pr.get("start"), pr.get("end"))
        if len(self.cues) != matched["stats"]["cues_raw"]:
            raise SystemExit(f"投影出的主轨 cue {len(self.cues)} 条，matched 记的是 {matched['stats']['cues_raw']} 条："
                             f"tracks 和产这份 matched 的不是同一份")
        self.fed_cue = {}
        for i, evs in enumerate(evs_of):
            for e in evs:
                self.fed_cue.setdefault(e["id"], i)
        self.rec_of = {i: r for r in matched["cues"] for i in r["cues"]}
        # 和叠加层用的同一份开头行（`script_lines` 是 `naninovel.head_script_lines`，匹配器交给 `overlay_items` 的那份）
        self.heads = GA.head_lines([(c.start, "\n".join(c.lines), "") for c in self.cues], script_lines)
        self.label = {r["index"]: r.get("label") for r in doc["regions"]}
        # 主轨可能是几块区域并成的聚合轨（`kind == "main"`，《魔裁》），区域轨的 `where` 里没有它：事件在主轨哪条 cue 里另记
        self.main_tr = tracksio.main_track(doc) or {"cues": []}
        self.main_where = {i: c for c in self.main_tr["cues"] for i in c["events"]}
        self.where = {}
        for tr in doc["tracks"]:
            if tr.get("kind") != "region":
                continue
            for c in tr["cues"]:
                for i in c["events"]:
                    self.where.setdefault(i, (tr, c))
        self.feed_mode, self.panel = "main", None

    def why_not_fed(self, e: dict) -> str:
        cue = self.main_where.get(e["id"])
        if cue is None:
            return "主轨以外的区域（naninovel 只喂主轨）"
        return self.cue_reason(e, self.main_tr, cue)


def spans_of(got: list[int]) -> list[tuple[int, int]]:
    """排好序的对上下标 -> 圈分母的几段（`SPAN_GAP` / `SPAN_MIN`）。"""
    groups: list[list[int]] = []
    for i in got:
        if groups and i - groups[-1][-1] <= SPAN_GAP:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [(g[0], g[-1]) for g in groups if len(g) >= SPAN_MIN]


class NaninovelAdapter:
    """`naninovel` 匹配产物的适配层：剧本是 Naninovel 的脚本目录（provenance 的 `ref` / `names`），顺序剧本、没有锚点。

    * 该出现的行：参照字幕（`--span-ref`，正文是剧本原文的 .ass / .srt，如 `game_text/lower5_jp.ass`）按 `script_align.align`
      对上剧本，对上的下标分段圈出区间（`spans_of`：同 `script_align --span-ref`，但去掉孤立的误对），区间里匹配器认得了的字幕带行（`naninovel.Line.band`；
      比 `script_align` 的口径多名字隐藏的 `Unknown` 行和 `@print` / `@char` 引出的行，它们也在字幕带里）。
    * 时间窗：剧本顺序上前后最近的已输出行夹出（`window`），读数、定位都在这个窗里找。
    * 组：剧本整本是台词，全是 `dialogue`（匹配器 `mark_display` 也这么打）。"""
    name = "naninovel"
    choice = False
    output_windows = True

    def __init__(self, m: dict, doc: dict, span_ref: list[str] | None):
        pv = m["provenance"]
        if not span_ref:
            raise SystemExit("naninovel 的匹配产物要给 --span-ref（参照字幕，正文是剧本原文）圈分母：顺序剧本没有锚点，"
                             "不给就只能拿输出自己圈，分母跟着输出变")
        names = NN.load_names(resolve(pv["names"]) if pv.get("names") else None)
        script = NN.load_script(resolve(pv["ref"]), names)
        ents = [SA.Entry(key=x.key, kind=x.kind, cmd="" if x.band else "@choice", text=x.jp, src=x.unit) for x in script]
        SA.align(ents, SA.read_subs([resolve(p) for p in span_ref]), SPAN_WINDOW, SPAN_FUZZY)
        self.spans = spans_of(sorted(i for i, e in enumerate(ents) if e.matched >= 0))
        if not self.spans:
            raise SystemExit("--span-ref 对上剧本的不够成段（每段至少 SPAN_MIN 条），圈不出区间")
        self.variant_of: dict[str, str] = {}
        self.rep = lambda k: k
        self.lines = [Line(x.key, x.jp, x.kind, None, x.speaker, x.unit, x.cn, None, [], False, [x.jp], False)
                      for lo, hi in self.spans for x in script[lo:hi + 1] if x.band]
        self.by_key = {x.key: {"key": x.key, "text": x.jp, "kind": x.kind, "cn": x.cn} for x in script}
        self.texts = {x.key: x.jp for x in script}
        # "另一版本"要说话人相同（旁白是 None）：顺序剧本没有按主角分的版本，字相近、说话人不同的是另一个人的另一句
        # （《魔裁》第 5 部漏报：レイア的 `エマくん。` 被ミリア的 `エマちゃん。` 顶掉、エマ的 `ヒロ……ちゃん……。` 被下一句旁白顶掉）
        self.speaker = {x.key: x.speaker for x in script}
        self.feed = NaniFeed(doc, m, NN.head_script_lines(script, names))
        self.names = {GS.cnorm(jp) for jp, _ in names.values()} - {""}
        self.obs_path = resolve((doc.get("provenance") or {})["obs"])
        self._norm = [GS.cnorm(x.jp) for x in script]
        self._index = None
        # 搜读数时，读数逐字就是剧本里另一行（又不是这一行）的不算读到这一行：窗由输出夹出、常常很宽，
        # 《魔裁》第 5 部第一版报的 27 条"漏"里 14 条是法庭"反論"的选项按钮（`@choice`，`私たちも魔法を使える` 对台词 `……いいや。私たちだって魔法を使える。`）
        self.others = frozenset(x for x in self._norm if x) | self.names
        # 跨臂比要相同的：分母只取决于剧本、名字表、参照字幕和分段，读数取决于 obs；tracks 不进来（比的正是建轨不同的臂）
        self.provenance = {"script": pv["ref"], "names": pv.get("names"), "span_ref": [str(p) for p in span_ref],
                           "spans": [list(x) for x in self.spans], "obs": str(self.obs_path),
                           "obs_mtime": self.obs_path.stat().st_mtime if self.obs_path.exists() else None}

    @staticmethod
    def group(ln: Line) -> str:
        return "dialogue"

    def window(self, ln: Line, prev, nxt) -> tuple[float | None, float | None]:
        """后一条已输出行最早那次的开始，到前一条已输出行在它之前最晚那次的结束（重播的那几次不算）。"""
        hi = min(s for s, _ in nxt) if nxt else None
        lo = None
        if prev:
            before = [e for s, e in prev if hi is None or s <= hi]
            lo = max(before) if before else min(e for _, e in prev)
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        return lo, hi

    def in_pack(self, evs: list[dict]) -> set[int]:
        """事件的字落在剧本某一行里的（整本剧本，不限区间）。按字的二元组先圈候选（`naninovel.Index`），不和三万行逐条比。"""
        if self._index is None:
            self._index = NN.Index(self._norm)
        out = set()
        for e in evs:
            q = GS.cnorm(e["text"])
            if len(q) >= GS.QMIN_FLOOR and any(GS.contained(q, self._norm[j]) >= PACK_CONTAIN for j in self._index.candidates(q)):
                out.add(e["id"])
        return out


class ObsText:
    """obs 的读数，按帧：搜一行在时间窗里读到过没有（适配层第 2 点，有没有锚点的行都要搜）。

    一帧里的框按"同一处"拼成块（上下相邻、左右有重叠，`JOIN_GAP`）：一句台词在屏幕上常排成几行，每个框各占不到一半。
    时段两头各放宽 `PAD`（同按时段找事件）。
    只搜内容 ≥ `FILL_MIN` 字的说法。判据两头都要：读数占这一行过半（`script_align.supports` 的"占这一行"那一支，`partial=False`），**而且**读数的字八成落在这一行里
    （`FIND_IN`，同 `Events.find`）。时间窗很宽（没锚点的行借前一个锚点、往后放宽 `--claim-back`）：只要前一半的话，
    长行会被碎片判成读到过，别的句子靠助词也能凑够一半（zzz 整场第一版：另一位主角的说法 `一緒にテレビでも見ないか？`
    被当成读到了 `お兄ちゃんも暇なら、一緒にテレビ観ない？`）。"""

    def __init__(self, rows: list[tuple[int, list[int], str]]):
        frames: dict[int, list[tuple[list[int], str]]] = defaultdict(list)
        for t_us, box, text in rows:
            frames[t_us].append((box, text))
        self.t = sorted(frames)
        self.frames = frames
        self._texts: dict[int, list[tuple[str, int, bool]]] = {}
        self._memo: dict[tuple[str, str], bool] = {}
        self._pmemo: dict[tuple[str, str], float] = {}         # `partial`：同一段字在几百帧里反复出现，按（读数，行）记一次

    @classmethod
    def load(cls, obs: Path) -> "ObsText":
        rows = []
        with open(obs, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if "_meta" not in r:
                    rows.append((r["t_us"], r["box"], r["text"]))
        return cls(rows)

    def texts(self, t_us: int) -> list[tuple[str, int, bool]]:
        """这一帧要比的文本：每个框，加上拼起来的每一块（块里不止一个框时）：(文本, 内容字数, 是不是拼起来的块)。"""
        if t_us in self._texts:
            return self._texts[t_us]
        boxes = sorted(self.frames[t_us], key=lambda x: (x[0][1], x[0][0]))
        parent = list(range(len(boxes)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i, (a, _) in enumerate(boxes):
            for j in range(i + 1, len(boxes)):
                b = boxes[j][0]
                h = min(a[3] - a[1], b[3] - b[1])
                if b[1] - a[3] > JOIN_GAP * h:
                    break
                if b[1] >= a[1] and min(a[2], b[2]) > max(a[0], b[0]):
                    parent[root(j)] = root(i)
        blocks: dict[int, list[str]] = defaultdict(list)
        for i, (_, text) in enumerate(boxes):
            blocks[root(i)].append(text)
        out = [(t, False) for _, t in boxes] + [("".join(b), True) for b in blocks.values() if len(b) > 1]
        out = [(t, len(SA._content(t)), blk) for t, blk in out]
        self._texts[t_us] = out
        return out

    def read(self, texts: list[str], windows: list[tuple[float, float]], tpl: bool = False,
             others: frozenset[str] | None = None) -> float | None:
        """时间窗里有没有一帧读到了这几种说法之一：返回第一次读到的时刻（秒），没有就 None。
        `tpl`：原文里有占位（屏幕上填进了玩家名，读数比原文长），不加"读数八成落在这一行里"那道门。
        `others`：剧本里全部行的归一文本（适配层给，见 `NaninovelAdapter.others`）；读数逐字就是其中另一行、又不是这一行的，不算读到这一行。"""
        # 不到 FILL_MIN 个内容字的说法不搜（`Detector.read` 的说明）：变体里偶尔有比代表行短得多的
        lines = [(x, n) for x in texts if (n := len(SA._content(x))) >= SA.FILL_MIN]
        own = {GS.cnorm(x) for x in texts}
        for a, b in sorted(windows):
            lo = bisect.bisect_left(self.t, int((a - PAD) * 1e6))
            hi = bisect.bisect_right(self.t, int((b + PAD) * 1e6))
            for t_us in self.t[lo:hi]:
                for o, on, blk in self.texts(t_us):
                    if others is not None and (qo := GS.cnorm(o)) in others and qo not in own:
                        continue
                    for line, n in lines:
                        if blk and on > JOIN_MAX * n + 6:
                            continue
                        k = (o, line, tpl)
                        if k not in self._memo:
                            self._memo[k] = (SA.supports(o, line, partial=False)
                                             and (tpl or GS.contained(GS.cnorm(o), GS.cnorm(line)) >= FIND_IN))
                        if self._memo[k]:
                            return t_us / 1e6
        return None


    def partial(self, texts: list[str], windows: list[tuple[float, float]], others: frozenset[str] | None = None) -> float:
        """时间窗里读数盖住这一行的最高比例：只算字八成落在这一行里的读数（框和拼起来的块都算），盖住多少按这一行的内容字数。
        给没读到的行定"有部分读数"（读到一部分、不到 `read` 的一半门）这一层用：打字机没打完、玩家跳过的真漏多在这里（S3 评审）。"""
        best = 0.0
        lines = [(x, GS.cnorm(x)) for x in texts if len(SA._content(x)) >= SA.FILL_MIN]
        own = {rt for _, rt in lines}
        for a, b in sorted(windows):
            lo = bisect.bisect_left(self.t, int((a - PAD) * 1e6))
            hi = bisect.bisect_right(self.t, int((b + PAD) * 1e6))
            for t_us in self.t[lo:hi]:
                for o, _, _ in self.texts(t_us):
                    if others is not None and (qo := GS.cnorm(o)) in others and qo not in own:
                        continue
                    for _, rt in lines:
                        k = (o, rt)
                        if k not in self._pmemo:
                            q = GS.cnorm(o)
                            v = 0.0
                            if q and rt and GS.contained(q, rt) >= FIND_IN:
                                m = sum(x.size for x in SequenceMatcher(None, q, rt, autojunk=False).get_matching_blocks())
                                v = m / len(rt)
                            self._pmemo[k] = v
                        best = max(best, self._pmemo[k])
        return round(best, 2)


# ---------------------------------------------------------------- 核心


def drawn_items(m: dict, vis: MIO.Visible, lang: str, rep_of) -> dict[str, list[dict]]:
    """已输出：剧本 key（变体并到代表行）-> 按可见规则画出来、有 `lang` 文本的叠加条目。"""
    out: dict[str, list[dict]] = defaultdict(list)
    for it in m["overlay"]["items"]:
        if it.get("key") and vis.shows(it) and MIO.text_of(it, lang):
            out[rep_of(it["key"])].append(it)
    return out


def double_draws(items: list[dict]) -> list[tuple[int, str, str]]:
    """一个事件同一时刻画了两块（显示协议规则第 5 条）：(事件, 一块的组/层, 另一块的组/层)。"""
    by: dict[int, list[dict]] = defaultdict(list)
    for it in items:
        for i in dict.fromkeys(it.get("events") or ()):
            by[i].append(it)
    return [(i, f"{a['group']}/{a['layer']}", f"{b['group']}/{b['layer']}") for i, its in by.items()
            for k, a in enumerate(its) for b in its[k + 1:]
            if a["start_us"] < b["end_us"] and b["start_us"] < a["end_us"]]


class Events:
    """tracks 的事件按起点排好，给"按时间窗找像这一行的事件"用。"""

    def __init__(self, evs: list[dict]):
        self.evs = sorted(evs, key=lambda e: e["t_start"])
        self.t0 = [e["t_start"] for e in self.evs]
        self.q = {e["id"]: GS.cnorm(e["text"]) for e in self.evs}

    def near(self, windows: list[tuple[float, float]]):
        seen = set()
        for a, b in windows:
            a_us, b_us = (a - PAD) * 1e6, (b + PAD) * 1e6
            lo = bisect.bisect_left(self.t0, a_us - LONG_EV * 1e6)
            for e in self.evs[lo:bisect.bisect_right(self.t0, b_us)]:
                if e["t_end"] >= a_us and e["id"] not in seen:
                    seen.add(e["id"])
                    yield e

    def cover(self, e: dict, text: str) -> float:
        """和 `e` 同时在屏、字八成落在这一行里的事件（含 `e`）并起来，盖住这一行多少内容字：每个事件和这一行的匹配块
        在这一行的字位上打标，数打了标的字位。一句台词常排成两三行、各是一个事件，只看 `e` 一个的话天然只有 1/2、1/3；
        按字位并（不按事件字数加）是因为同屏几处界面字可能含同一个词（hsr 的 `推奨次元界オーナメント` 和另一处的
        `次元界オーナメント`），按字数加会把同一段字算两遍、凑过 `WEAK`（S2 第 2 轮评审）。"""
        rt = GS.cnorm(text)
        hit = bytearray(len(rt))
        for x in self.near([(e["t_start"] / 1e6, e["t_end"] / 1e6)]):
            q = self.q[x["id"]]
            if (x["t_start"] < e["t_end"] and e["t_start"] < x["t_end"] and len(q) >= FIND_MIN
                    and GS.contained(q, rt) >= FIND_IN):
                for m in SequenceMatcher(None, q, rt, autojunk=False).get_matching_blocks():
                    hit[m.b:m.b + m.size] = b"\x01" * m.size
        return sum(hit) / max(1, len(rt))

    def find(self, text: str, windows, need: float = FIND_IN) -> dict | None:
        """窗里最像这一行的事件：字有 `need` 落在行里、内容最长的那个（窗是 sequence 的时段）。"""
        rt = GS.cnorm(text)
        best, best_n = None, 0
        for e in self.near(windows):
            q = self.q[e["id"]]
            if len(q) >= FIND_MIN and len(q) > best_n and GS.contained(q, rt) >= need:
                best, best_n = e, len(q)
        return best


class Detector:
    def __init__(self, matched_path: Path, lang: str = "cn", show=None, hide=None, span_ref: list[str] | None = None):
        self.path = matched_path
        self.m = m = json.loads(matched_path.read_text(encoding="utf-8"))
        MIO.validate(m, need_overlay=True, where=str(matched_path))
        pv = m["provenance"]
        self.doc = tracksio.load(resolve(pv["subs"]))
        self.ad = (NaninovelAdapter(m, self.doc, span_ref) if pv.get("tool") == "matchers.naninovel"
                   else GametextAdapter(m, self.doc))
        self.ref = getattr(self.ad, "ref", None)
        self.lang = lang
        self.vis = MIO.visible(m, show, hide)
        self.rep = self.ad.rep
        self.lines = self.ad.lines
        self.by_key = self.ad.by_key
        self.drawn = drawn_items(m, self.vis, lang, self.rep)
        self.items_of: dict[str, list[dict]] = defaultdict(list)
        self.items_ev: dict[int, list[dict]] = defaultdict(list)
        for it in m["overlay"]["items"]:
            if it.get("key"):
                self.items_of[self.rep(it["key"])].append(it)
            for i in it.get("events") or ():
                self.items_ev[i].append(it)
        self.claims = claim_times(m["cues"], self.ad.variant_of)
        self.claim_recs: dict[str, list[dict]] = defaultdict(list)
        for r in m["cues"]:
            for c in ([r["ref"]] if r["ref"] else []) + list(r["extra"]):
                self.claim_recs[self.rep(c["key"])].append(r)
        self.feed = self.ad.feed
        self.events = Events(self.doc["events"])
        self.main = tracksio.main_regions(self.doc)
        self.names = self.ad.names
        self._obs = None

    @property
    def obs(self) -> ObsText:
        if self._obs is None:
            self._obs = ObsText.load(self.ad.obs_path)
        return self._obs

    def read(self, ln: Line) -> tuple[str | None, float | None, str | None]:
        """适配层第 2 点：(怎么读到的, 读到的时刻, 没读到的原因)，读到过时第一项不为空。

        * 有叠加条目、或认领它的 cue 里有一行**不是开头行（名牌）**的读数撑得住它（`claim_supported`），算读到过：认领是窄时段里的证据。
          只靠名牌撑着的认领不算（`ナヴィア` 名牌被认成台词 `ナヴィア…`，叠加不画它是对的；S3 第二批摆帧 4 条误报里的 N19）。
        * 不到 3 个内容字的行（纯标点、1–2 字）不搜，记"太短判不了"（同 `game_align.OBS_SHORT`）：时段动辄几百秒，`……` 随便哪个框里有一个、
          `うん` 撞上 `うんざり` 就算读到（S2 第 3 轮评审：gi2 纯标点 48 条全成了伪"漏"）。
        * 整行就是一个说话人名的（`ヌヴィレット…`）不搜，记"和说话人名同字"：名牌上天天有这几个字（S3 摆帧）。
        * 其余在它的时段里搜 obs（`ObsText.read`），**有锚点的也要搜到**：gamescript 的锚点只说明某个读数八成落在这一行里，
          一句开头相同的另一句台词（`まあ、安心してちょうだい。…` 对 `まっ、安心してちょうだい。…`）、打字机打到一半的读数都能锚上来；
          S3 第一批摆帧 20 条"漏"里 8 条"别的东西"都对不上屏上的这一行（锚点 = 读到过的口径下，精确率 10/20）。没搜到的有锚点行记"锚点没有读数支撑"。
          ⚠ 这条判据和下面认领的那条，都是在 S3 摆过帧的样本上定的（样本内）；会不会把真漏推进"没读到"要另抽帧量（漏报，计划的验收）。"""
        if ln.key in self.items_of or self.claim_supported(ln):
            return ("认领过", None, None)
        # 两个口径哪个说短都算短：三档（`content_len`）把 `——` 算内容字，`すぅ——…` 归有内容档、按搜读数的口径（`SA._content`）却不到 3 字；
        # 反过来 `cnorm` 留着 `ー`、把 `ッ` 并成 `ツ`，`しーっ！` 三档是 1–2 字、搜读数的口径却够 3 字，在宽时段里一搜就会撞上别处的字
        if len(SA._content(ln.text)) < SA.FILL_MIN or evalkit.triviality(ln.text) != evalkit.CLASSES[2]:
            return (None, None, GA.OBS_SHORT)
        if GS.cnorm(ln.text) in self.names:
            return (None, None, NAME_LIKE)
        t = self.obs.read(ln.texts, ln.windows, ln.tpl, self.ad.others)
        if t is not None:
            return ("锚点 + 读数" if ln.anchored else "obs 搜到（锚点漏掉）", t, None)
        return (None, None, NO_SUPPORT if ln.anchored else None)

    def claim_supported(self, ln: Line) -> bool:
        """认领这一行的记录的 OCR 原文（`ocr`：合并时取的最长那条 cue，`scriptmatch.merge_runs`）里，有没有一行不是开头行（`Feed.heads`，名牌）的读数撑得住它：`supports`，而且（≥ `FILL_MIN` 字的行）
        读数的字八成落在这一行里（同 obs 搜读数的两道门）——任务追踪栏 `口ヌヴィレットの執務室に行く`、一句旁白里带着人名，都撑不起 `ヌヴィレット様…`。
        * 读数开头是"已知说话人名 + `：`"（`アテア：コホンッ。`，旁白式字幕的格式）先剥掉再比（只在认领这条路上剥；obs 搜读数那条没剥，
          没被认领的这种行会被第二道门挡掉——要修在匹配器那侧，S3 报告的 S4 第 2 项）；
        * 不到 `FILL_MIN` 字的行（纯标点、1–2 字）只看 `supports`：它对短行本来就要字全在，认领又是窄时段（S2 第 3 轮评审定的口径）；
        * 带占位的行不加第二道：屏幕上填进了玩家名（`旅人のねるめろ(本物)だよ` 对剧本 `旅人のだよ。`），读数本来就比原文长。"""
        short = len(SA._content(ln.text)) < SA.FILL_MIN
        for r in self.claim_recs.get(ln.key, ()):
            for o in (r.get("ocr") or "").split("\n"):
                if SA.norm(o) in self.feed.heads:
                    continue
                o = self.unprefix(o)
                if any(SA.supports(o, x) and (short or ln.tpl or GS.contained(GS.cnorm(o), GS.cnorm(x)) >= FIND_IN)
                       for x in ln.texts):
                    return True
        return False

    def unprefix(self, o: str) -> str:
        """`名前：台詞` 格式的读数剥掉名字那半（名字要是已知说话人名）。"""
        m = re.match(r"^\s*([^：:]{1,12})[：:]\s*(.+)$", o)
        return m.group(2) if m and GS.cnorm(m.group(1)) in self.names else o

    def window(self, ln: Line, out_pos: list[int], seq: list[Line], drawn: dict[str, list[dict]]
               ) -> tuple[float | None, float | None]:
        """这一行前后最近的已输出行夹出的时间窗（`claim_window`，按画出来的条目）。"""
        k = self.pos[ln.key]
        j = bisect.bisect_left(out_pos, k)
        times = lambda p: [(it["start_us"] / 1e6, it["end_us"] / 1e6) for it in drawn[seq[p].key]]  # noqa: E731
        return self.ad.window(ln, times(out_pos[j - 1]) if j > 0 else None, times(out_pos[j]) if j < len(out_pos) else None)

    def choice_why(self, e: dict, ln: Line) -> str:
        """主轨外的事件，选项层（`scriptmatch.choice_items`）为什么没给这一行出块：第一道没过的门。"""
        if tracksio.in_main(e, self.main):
            return "在主轨里（选项层只收主轨外的）"
        q0, own = GS.cnorm(e["text"]), {GS.cnorm(x) for x in ln.texts}
        forms = list(dict.fromkeys((q0, SM.long_vowel(q0))))      # 同选项层：原样先试，再把长音读成的 `一` 折回来
        if not own & set(forms):
            # 前缀路（`SM.ChoicePrefix`）的门按选项层的先后查
            ks = [k for f in forms if (k := self.ad.prefix.match(f, e["t_start"] / 1e6))]
            if not ks:
                return "字和剧本行不逐字相等，也不是够长、只对得上一种说法的前缀"
            k = next((k for k in ks if k in own), ks[0])
            if k not in own:
                return "前缀对上的是别的行"
            if not SM.choice_like(ln.kind, ln.status):
                return "只是前缀，前缀路只收选项类"
            if not self.ad.prefix.live(ln.uid, k, e["t_start"] / 1e6, e["t_end"] / 1e6):
                return "前缀对上了，但同一单元别的行此刻不在播"
        if len(GS.cnorm(ln.text)) < 2:
            return "剧本行不到 2 个内容字"
        if not ln.cn:
            return "剧本行没有译文"
        sp = self.ad.unit_span.get(ln.uid)
        if sp is None or not sp[0] <= e["t_start"] / 1e6 <= sp[1]:
            return "不在单元时段 ±60 s 里"
        choice = SM.choice_like(ln.kind, ln.status)
        if not SM.block_ok(e, choice):
            return "在动、又不是选项类"
        if not SM.block_long_enough(choice, e["t_end"] - e["t_start"], self.doc["frame_us"]):
            return "只在屏上一个采样点"
        # 同 `overlay_items` 传给 `choice_items` 的 taken：正文 / 第二条认领 / 译出了的名牌
        used = [it for it in self.items_ev.get(e["id"], ())
                if it["layer"] in ("body", "extra") or (it["layer"] in ("name", "title") and it.get("cn"))]
        if used:
            return f"事件已被别的条目用掉（{used[0]['group']}/{used[0]['layer']}）"
        return "过了各道门、块没出（并段 / 被阅读面板扣掉）"

    def diagnose(self, ln: Line) -> dict:
        """定层：认领到画出来这一段从后往前查，再按 tracks 事件往前追。"""
        its = self.items_of.get(ln.key)
        if its:
            why = Counter("填位不画" if it.get("fill") and not self.vis.fill and it["group"] in self.vis.groups
                          else f"组 {it['group']} 不可见" if it["group"] not in self.vis.groups
                          else f"没有 {self.lang} 文本" for it in its)
            return {"layer": L_HIDDEN, "why": why.most_common(1)[0][0], "items": len(its)}
        if ln.key in self.claims and self.claim_supported(ln):
            return {"layer": L_NOITEM, "why": "正文事件被 body_of 筛光，或已被同一记录的主认领用掉",
                    "claim_t": min(t for t, _ in self.claims[ln.key]),
                    "claim_ocr": self.claim_recs[ln.key][0].get("ocr") if self.claim_recs.get(ln.key) else None}
        # 代表行和变体的说法都找，取盖得最多的（屏幕上出的可能是变体那一种）
        found = [(self.events.cover(e, x), e) for x in ln.texts if (e := self.events.find(x, ln.windows))]
        if not found:
            c = self.events.find(ln.text, ln.windows, need=CLOSEST)
            return {"layer": L_NOEV, **({"closest": c["id"], "closest_text": c["text"], "closest_t": c["t_start"] / 1e6}
                                          if c else {})}
        cov, e = max(found, key=lambda x: x[0])
        out = {"event": e["id"], "event_text": e["text"], "event_t": e["t_start"] / 1e6, "region": e["region"],
               "cover": round(cov, 2)}
        if cov < WEAK:
            return {**out, "layer": L_WEAK}
        if self.ad.choice and not tracksio.in_main(e, self.main):
            out["choice_why"] = self.choice_why(e, ln)
        other = [it for it in self.items_ev.get(e["id"], ()) if it.get("key") and self.rep(it["key"]) != ln.key]
        i = self.feed.fed_cue.get(e["id"])
        rec = self.feed.rec_of.get(i) if i is not None else None
        if rec and rec["ref"]:
            if (GS.contained(GS.cnorm(e["text"]), GS.cnorm(rec["ref"].get("jp") or "")) >= FIND_IN
                    and self.same_speaker(ln.key, rec["ref"]["key"])):
                return {**out, "layer": SIB, "sibling": rec["ref"]["key"], "sibling_via": "同一段字"}
            return {**out, "layer": L_OTHER, "claimed": rec["ref"]["key"], "claimed_jp": rec["ref"].get("jp")}
        if other:          # 喂了没认上、或根本没喂，事件却被选项层画成了别的行
            return {**out, "layer": L_OTHER, "claimed": other[0]["key"], "claimed_jp": other[0].get("jp"),
                    "via": f"{other[0]['group']}/{other[0]['layer']}"}
        if i is not None:
            return {**out, "layer": L_NONE}
        return {**out, "layer": L_NOFEED, "why": self.feed.why_not_fed(e)}

    def same_speaker(self, a: str, b: str) -> bool:
        """`a` 能不能把 `b` 当成自己的另一版本：适配层不给说话人表（gametext）时不管，给了就要相同。"""
        sp = self.ad.speaker
        return sp is None or sp.get(a) == sp.get(self.rep(b))

    def run(self) -> dict:
        seq, drawn = self.lines, self.drawn
        self.pos = {ln.key: k for k, ln in enumerate(seq)}
        out_pos = [k for k, ln in enumerate(seq) if ln.key in drawn]
        texts = self.ad.texts
        shown = sorted((it["start_us"] / 1e6, it["end_us"] / 1e6, self.rep(it["key"]))
                       for its in drawn.values() for it in its)
        rows = []
        for ln in seq:
            g = self.ad.group(ln)
            r = {"key": ln.key, "text": ln.text, "kind": ln.kind, "status": ln.status, "role": ln.role, "group": g,
                 "class": evalkit.triviality(ln.text), "t": ln.t}
            if ln.key in drawn:
                r["cat"] = OUT
                r["fill"] = all(it.get("fill") for it in drawn[ln.key])
                rows.append(r)
                continue
            lo, hi = self.window(ln, out_pos, seq, drawn)
            r["window"] = [lo, hi]
            if self.ad.output_windows and (lo is not None or hi is not None):
                # 窗由输出夹出（naninovel）：读数、定位都在这个窗里找；行的时刻记窗的起点
                ln.windows = [(lo if lo is not None else hi, hi if hi is not None else lo)]
                r["t"] = ln.t = ln.windows[0][0]
            sib = sibling(ln.text, lo, hi, shown, texts, lambda k: self.same_speaker(ln.key, k))
            if sib:
                rows.append({**r, "cat": SIB, "sibling": sib[0], "sibling_via": "整行相似"})
                continue
            rd = self.read(ln)
            if g not in self.vis.groups:
                rows.append({**r, "cat": OFF, "read": rd[0]})
                continue
            if rd[0] is None:
                # 漏报分层要的两个字段：认领了却没撑住；最好的部分读数（只对能搜的行算）
                extra = {"claimed_unsupported": True} if ln.key in self.claim_recs else {}
                if rd[2] not in (GA.OBS_SHORT, NAME_LIKE):
                    extra["partial"] = self.obs.partial(ln.texts, ln.windows, self.ad.others)
                rows.append({**r, "cat": UNREAD, **({"why": rd[2]} if rd[2] else {}), **extra})
                continue
            d = self.diagnose(ln)
            if d["layer"] == SIB:
                rows.append({**r, **d, "cat": SIB, "read": rd[0]})
            else:
                rows.append({**r, **d, "cat": MISS, "read": rd[0], **({"read_t": rd[1]} if rd[1] is not None else {})})
        # `provenance` 管分母（`same_ref` 比它）；`code` 管尺和被量的匹配器是不是同一版代码（跨臂报表另比）
        return {"matched": str(self.path), "lang": self.lang, "visible": sorted(self.vis.groups), "fill_visible": self.vis.fill,
                "adapter": self.ad.name, "provenance": self.ad.provenance,
                "code": {"detector": PV.code_fp(CODE_FP_FILES), "matcher": self.m["provenance"].get("code_fp")},
                "rows": rows, "invariants": self.invariants(rows)}

    def invariants(self, rows: list[dict]) -> dict:
        ids = {e["id"] for e in self.doc["events"]}
        items = self.m["overlay"]["items"]
        bad_key = sorted({it["key"] for it in items if it.get("key") and it["key"] not in self.by_key})
        bad_ev = sorted({i for it in items for i in it.get("events") or () if i not in ids})
        no_layer = [r["key"] for r in rows if r["cat"] == MISS and not r.get("layer")]
        dd = double_draws(items)
        return {"keys_unknown": bad_key[:20], "n_keys_unknown": len(bad_key), "events_unknown": bad_ev[:20],
                "n_events_unknown": len(bad_ev), "miss_without_layer": no_layer[:20], "n_miss_without_layer": len(no_layer),
                "double_draws": [list(x) for x in dd[:20]], "n_double_draws": len(dd),
                "ok": not (bad_key or bad_ev or no_layer or dd)}

    def b_list(self, rows: list[dict], pack: bool = True) -> dict:
        """B 清单：`unmatched` ∪ `unfed` 的事件，去掉常驻 UI、注音、不到 `B_MIN` 个内容字的，扣掉任一该出现行定位到的，按区域汇总。"""
        un, unfed = MIO.fed_rest(self.m, self.doc)
        rest_of = {i: "unmatched" for i in un} | {i: "unfed" for i in unfed}
        ev = {e["id"]: e for e in self.doc["events"]}
        cand = []
        for i, grp in rest_of.items():
            e = ev[i]
            tr, _ = self.feed.where.get(i, (None, None))
            flags = set(e.get("flags") or ())
            if ("ruby" in flags or "ui_footprint" in flags or (tr and e["text"].strip() in set(tr.get("ui_lines") or ()))
                    or len(KANA_HAN.findall(e["text"])) < B_MIN):
                continue
            cand.append(e)
        cev = Events(cand)
        located = set()
        for ln in self.lines:
            rts = [GS.cnorm(x) for x in ln.texts]
            # naninovel 已输出的行没有窗（窗由输出夹出，只给没输出的行设），拿它画出来的时段去扣
            wins = ln.windows or [(it["start_us"] / 1e6, it["end_us"] / 1e6) for it in self.drawn.get(ln.key, ())]
            for e in cev.near(wins):
                q = cev.q[e["id"]]
                if len(q) >= FIND_MIN and any(GS.contained(q, rt) >= FIND_IN for rt in rts):
                    located.add(e["id"])
        rest = [e for e in cand if e["id"] not in located]
        in_pack = self.ad.in_pack(rest) if pack and rest else set()
        label = {r["index"]: r.get("label") for r in self.doc["regions"]}
        regions: dict[int, dict] = {}
        for e in rest:
            g = regions.setdefault(e["region"], {"region": e["region"], "label": label.get(e["region"]), "n": 0, "dur": 0.0,
                                                 "moving": 0, "in_pack": 0, "unmatched": 0, "unfed": 0, "texts": Counter()})
            g["n"] += 1
            g["dur"] += (e["t_end"] - e["t_start"]) / 1e6
            g["moving"] += "boxes" in e
            g["in_pack"] += e["id"] in in_pack
            g[rest_of[e["id"]]] += 1
            g["texts"][e["text"]] += 1
        regs = sorted(regions.values(), key=lambda g: -g["n"])
        for g in regs:
            g["dur"] = round(g["dur"], 1)
            g["samples"] = [t for t, _ in g.pop("texts").most_common(5)]
        return {"candidates": len(cand), "located": len(located), "rest": len(rest), "in_pack": len(in_pack),
                "pack_checked": pack, "regions": regs}


def ref_provenance(ref_doc: dict) -> dict:
    """跨臂比较要相同的剧本 provenance：分母来自剧本，剧本的代码、obs、文本包、参数不一样，两臂的分母就不是一回事。"""
    pv = ref_doc["provenance"]
    return {"code_fp": pv.get("code_fp"), "obs": pv.get("obs"), "obs_mtime": pv.get("obs_mtime"),
            "gametext": (pv.get("gametext") or {}).get("fingerprint"), "params": pv.get("params")}


def same_ref(a: dict, b: dict) -> list[str]:
    """两臂的剧本 provenance 哪几项不同（空表 = 可以比）。"""
    return [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def ruler(res: dict) -> dict:
    """检测结果 -> 比臂的几栏（只数有内容 ≥3 字的行）：`md_den` 该出现 − 匹配器决定不显示、`md_cov` 已输出 + 另一版本已输出、
    `md_miss` 漏（含定位弱）、`md_weak` 漏里定位弱的（丢在哪一层判不了，比臂时别把它的变化读成漏条变化）、`md_unread` 没读到、`md_fill` 已输出里的填位。"""
    rows = [x for x in res["rows"] if x["class"] == evalkit.CLASSES[2]]
    n = lambda c: sum(1 for x in rows if x["cat"] == c)  # noqa: E731
    return {"md_den": len(rows) - n(OFF), "md_cov": n(OUT) + n(SIB), "md_miss": n(MISS), "md_unread": n(UNREAD),
            "md_weak": sum(1 for x in rows if x["cat"] == MISS and x.get("layer") == L_WEAK),
            "md_fill": sum(1 for x in rows if x["cat"] == OUT and x.get("fill"))}


# ---------------------------------------------------------------- 报告


def summary(res: dict) -> None:
    rows = res["rows"]
    C = evalkit.CLASSES[2]
    print(f"{res['matched']}（{res['lang']}；可见组 {','.join(res['visible'])}，填位{'画' if res['fill_visible'] else '不画'}）")
    print(f"该出现 {len(rows)} 行，按组 × 类（{C}）：")
    groups = [g for g, _ in Counter(r["group"] for r in rows).most_common()]
    print(f"  {'组':<10}{'该出现':>8}" + "".join(f"{c:>12}" for c in CATS) + f"{'其中填位':>10}{'覆盖':>10}")
    for g in groups + ["合计"]:
        rs = [r for r in rows if r["class"] == C and (g == "合计" or r["group"] == g)]
        c = Counter(r["cat"] for r in rs)
        den = len(rs) - c[OFF]
        cov = f"{(c[OUT] + c[SIB]) / den:.1%}" if den else "—"
        print(f"  {g:<10}{len(rs):>8}" + "".join(f"{c[k]:>12}" for k in CATS)
              + f"{sum(1 for r in rs if r['cat'] == OUT and r.get('fill')):>10}{cov:>10}")
    print("  覆盖 = (已输出 + 另一版本已输出) / (该出现 − 匹配器决定不显示)")
    for cls in evalkit.CLASSES[:2]:
        c = Counter(r["cat"] for r in rows if r["class"] == cls)
        short = sum(1 for r in rows if r["class"] == cls and r.get("why") == GA.OBS_SHORT)
        print(f"  {cls}：" + "、".join(f"{k} {c[k]}" for k in CATS) + f"（没读到里太短判不了 {short}）")
    miss = [r for r in rows if r["cat"] == MISS and r["class"] == C]
    print(f"漏（{C}）{len(miss)} 条丢在哪一层：")
    for (lay, g), v in Counter((r["layer"], r["group"]) for r in miss).most_common():
        print(f"  {lay} · {g}：{v}")
    why = Counter(r.get("why") for r in miss if r.get("why"))
    if why:
        print("  原因：" + "、".join(f"{k} {v}" for k, v in why.most_common(8)))
    cw = Counter(r["choice_why"] for r in miss if r.get("choice_why"))
    if cw:
        print("  主轨外的事件，选项层没出块：" + "、".join(f"{k} {v}" for k, v in cw.most_common()))
    a = Counter(r.get("read") for r in rows if r["class"] == C and r["cat"] in (MISS, OFF))
    print(f"读到过的方式（{C}，漏 + 决定不显示）：" + "、".join(f"{k} {v}" for k, v in a.most_common() if k))
    inv = res["invariants"]
    print("不变量：" + ("全部成立" if inv["ok"] else
                     f"⚠ 未知 key {inv['n_keys_unknown']}、未知事件 {inv['n_events_unknown']}、"
                     f"漏没定层 {inv['n_miss_without_layer']}、同一时刻画两块 {inv['n_double_draws']}"))
    b = res.get("b_list")
    if b:
        print(f"B 清单（文本包外的字）：候选 {b['candidates']}，被该出现行定位到的 {b['located']}，剩 {b['rest']}"
              + (f"，其中文本包里有（单元没圈中、或不在分母 / 不在窗里）{b['in_pack']}" if b["pack_checked"] else "（没查文本包）"))
        noise = [g for g in b["regions"] if g["label"] == "noise"]
        if noise:
            print(f"  噪音区域 {len(noise)} 个、共 {sum(g['n'] for g in noise)} 条（下面不列）")
        for g in [g for g in b["regions"] if g["label"] != "noise"][:10]:
            print(f"  r{g['region']:02d}（{g['label']}）{g['n']} 条、在屏 {g['dur']:.0f} s、在动 {g['moving']}、文本包里有 {g['in_pack']}"
                  f"、unmatched {g['unmatched']} / unfed {g['unfed']}：" + " | ".join(t[:16] for t in g["samples"][:3]))
    print("  ⚠ 是线索、不是确认的错：检测器复用了匹配器的判据，摆帧才算数")


def show(res: dict, n: int) -> None:
    C = evalkit.CLASSES[2]
    miss = [r for r in res["rows"] if r["cat"] == MISS and r["class"] == C]
    print(f"\n逐条漏（{C}，前 {n} 条）：")
    for r in miss[:n]:
        tail = f"{r['layer']}" + (f"（{r['why']}）" if r.get("why") else "")
        if "event" in r:
            tail += f"：事件 #{r['event']} {hms(r['event_t'])} r{r['region']:02d}「{r['event_text'][:20]}」（盖住 {r['cover']:.0%}）"
        if r.get("claimed"):
            tail += f" -> 认成「{(r.get('claimed_jp') or '')[:20]}」"
        if r.get("choice_why"):
            tail += f"；选项层：{r['choice_why']}"
        print(f"  [{r['group']}] {hms(r['t'])} {r['text'][:36]}\n      -> {tail}")


def compare(a: dict, b: dict) -> dict:
    """两臂逐 key 比（有内容档）：分类之间怎么挪的，"漏"带上定层（新漏 / 修好丢在哪一层）。返回逐条的差。"""
    diff = same_ref(a["provenance"], b["provenance"])
    if diff:
        raise SystemExit(f"两臂的剧本 provenance 不同（{', '.join(diff)}），分母不是一回事，拒比")
    C = evalkit.CLASSES[2]
    tag = lambda r: r["cat"] + (f"/{r['layer']}" if r["cat"] == MISS else "")  # noqa: E731
    ra = {r["key"]: r for r in a["rows"] if r["class"] == C}
    rb = {r["key"]: r for r in b["rows"] if r["class"] == C}
    moved = [{"key": k, "text": (rb.get(k) or ra[k])["text"], "a": tag(ra[k]) if k in ra else None, "b": tag(rb[k]) if k in rb else None}
             for k in sorted(set(ra) | set(rb)) if (tag(ra[k]) if k in ra else None) != (tag(rb[k]) if k in rb else None)]
    print(f"\n两臂逐条（{C}）：A {a['matched']} -> B {b['matched']}")
    for (x, y), v in Counter((d["a"], d["b"]) for d in moved).most_common():
        print(f"  {x} -> {y}：{v}")
    return {"moved": moved}


def detect(path: Path, lang: str, show_=None, hide=None, pack: bool = True, span_ref: list[str] | None = None) -> dict:
    det = Detector(path, lang, show_, hide, span_ref)
    res = det.run()
    res["b_list"] = det.b_list(res["rows"], pack)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matched", help="匹配产物 matched.json（gametext / naninovel；剧本、tracks、obs 都从 provenance 取）")
    ap.add_argument("--lang", default="cn", help="判已输出用的语种（默认 cn）")
    ap.add_argument("--show-groups", default=None, help="同预设的 show=（默认按产物声明的默认可见）")
    ap.add_argument("--hide-groups", default=None, help="同预设的 hide=")
    ap.add_argument("--vs", default=None, help="另一条臂的 matched.json：逐条比（剧本 provenance 不同就拒）")
    ap.add_argument("--span-ref", nargs="+", default=None,
                    help="naninovel 必给：参照字幕（正文是剧本原文，如 data/corpus/mocai/game_text/lower5_jp.ass），圈分母的剧本区间")
    ap.add_argument("--no-pack", action="store_true", help="B 清单不查文本包（省读库的时间）")
    ap.add_argument("--show", type=int, default=15, help="逐条列几条漏")
    ap.add_argument("--out", default=None, help="汇总与逐条写到这个 JSON")
    a = ap.parse_args()
    path = resolve(a.matched)
    try:
        MIO.visible(json.loads(path.read_text(encoding="utf-8")), a.show_groups, a.hide_groups)
    except ValueError as e:
        ap.error(str(e))
    res = detect(path, a.lang, a.show_groups, a.hide_groups, not a.no_pack, a.span_ref)
    summary(res)
    if a.show:
        show(res, a.show)
    if a.vs:
        res_b = detect(resolve(a.vs), a.lang, a.show_groups, a.hide_groups, not a.no_pack, a.span_ref)
        res = {**res, "vs": res_b, "compare": compare(res, res_b)}      # --out 两臂都写
        if not res_b["invariants"]["ok"]:
            print(f"⚠ B 臂的不变量不成立：{res_b['invariants']}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n-> {a.out}")
    return 0 if res["invariants"]["ok"] and (res.get("vs") or res)["invariants"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
