"""阶段 3：从阶段 2 产物投影出**字幕稿**（一份能在 Aegisub / mpv 里直接预览、编辑的 ASS），特效另由阶段 4（`flowocr.typeset`）生成。

方案：script-fx 计划（owner 2026-09-27）。这里**只投影**，不重新判断（artifacts.md 第 1 条）：

* 对齐方式是阶段 2 判好的 `regions[].align`；matched 各层盖哪些块是 `scriptmatch.overlay_items` 判好的；
* tracks 取哪些事件、哪些是常驻 UI，按**轨**取（`tracksio.cue_lines`，UI 的权威清单 `ui_lines` 是逐轨的）；
* 效果（打字机 / 淡入淡出）的判定在这里做一次，写进字幕稿（淡入淡出是 `\\fad`，打字机是 Effect 里 `fo` 的 `tw`），阶段 4 不回读阶段 2。

条目（`Item`）= 一组同起同止的行。来源两个：

* `tracks_items`：**一个事件一条**，每个事件只画一次。不按 cue 出：默认的 `--srt-mode segment` 把一条常驻行
  切进好几条 2 秒的 cue，按 cue 出会画好几遍（底板叠成不透明、打字机从头放好几次）；
* `project_items`：一个匹配条目一条，译文按原文行数重分（`overlay_lines`）。

一条条目写成字幕稿里的一行（源行）：Style 按角色、Actor 是区域号、Effect 是阶段 4 要的其余参数 `fo:…`
（原文框、打字机时长、轨迹、`was`…，格式见 artifacts.md 的字幕稿一节）；正文是一个 tag 块（`\\an` `\\pos` `\\fs` `\\fad`）+ 纯文字。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from flowocr import provenance
from flowocr.artifacts import matchedio, tracksio
from flowocr.artifacts.matchedio import LAYERS
from flowocr.output import export, layout
from flowocr.typeset import assfile

FORMAT_VERSION = "1"
"""字幕稿格式的版本，写在 `[Script Info]` 的 `FlowOCR Script` 键里。"""

STYLES = ("Body", "Extra", "Name", "Choice", "Offmain", "Panel", "UI")
"""字幕稿的样式 = 角色（Style 表示"这是什么"，不表示"在哪"）。`Offmain` 是 matched 的"主轨外"那一层；
条目在不在主轨另由 `fo` 参数 `off` 记（dev 按它改字色）。"""

PREVIEW_BOX = "&H20101010"
"""预览底框的颜色（`BorderStyle=3` 用 OutlineColour 画框）：近黑、α 0x20。只供预览，阶段 4 的生成行不用它。"""

TYPEWRITER_MODES = ("off", "on", "dim")
"""打字机效果：`on` 检出了就逐字显出；`dim` 全字出现之前整句半透明（审计时刻用，阶段 4 的选项）；`off` 不做。"""

TW_MIN_FRAMES = 2
"""首字出现到全字出现不到这么多帧的不算打字机（回抠用原生帧、采样级用采样间隔）：
即时出现的字回抠也会给一个晚一两帧的全字时刻；采样级的全字判据会被"后面某帧多读两个字"骗出一个采样间隔。"""

FADE_IN_MAX_US = 300_000
"""复刻的淡入最长这么多（owner 2026-09-25：拿不准的时候，字能及时看清更重要）。回抠量到的"首字 → 全字"
不一定是淡入：整段判不清时它常常是打字过程（gi-s2 回抠产物上 0.4–1.2 s），画成那么长的淡入，字会一直发虚。
主轨上量到的真淡出多数 ≤ 2 帧、最长约 15 帧（text-effects 报告），这个上限盖得住。
⚠ owner 2026-09-25：有点绝对，待观察（followups 清单）。"""

INSTANT_MAX_SPREAD = 1.0
"""回抠的整段判定 `instant` 有两种来源（`refine_boundaries`）：每字生长的中位时长不到一帧（字确实没有生长），
或每字耗时的离散度 `iqr_over_median` 超过 1.0（判不清）。只有前一种才整段关掉逐字显出；
离散度超过这个数的按逐条的帧数门走——拿不准时跟着原文打字的节奏，不压成一段长淡入。"""


@dataclass
class Item:
    """一组同起同止的行。`events` 是效果（打字机 / 淡入淡出）取时刻用的正文事件，只有 `line` 才带；
    `boxes` 是在动的事件的轨迹；`translated`：叠的是剧本译文（matched），不是这个框里原有的那段字（tracks）——
    译文的行是重分过的，锚轴取整组的（`layout.anchor_axis`），字号有保底（`layout.OVERLAY_MIN_SCALE`）；
    原文压回自己的框，锚轴取这一行自己的框，字号只按框定。"""
    start_us: int
    end_us: int
    rows: list[list[float]]
    lines: list[str]
    kind: str                                  # line / name / choice / offmain / block
    events: list[dict] = field(default_factory=list)
    region: int = -1
    main: bool = True
    ui: bool = False
    align: str = "center"
    translated: bool = False
    boxes: list | None = None
    cue: str = ""
    row_h: int = 0                             # block：原文行高（字号从它换算）
    layer: str = ""                            # matched 的层（body / extra / …）；tracks 的条目是空
    key: str | None = None                     # matched：剧本行的键（跨阶段 2 重跑仍然稳定）


def overlay_lines(text: str, rows: int) -> list[str]:
    """译文分成**恰好** `rows` 行（和原字幕逐行对位，底板才能逐行画）：剧本自带换行数正好就照它，
    否则把整句按字数均分。`rows ≤ 1` 时剧本换行照留。"""
    ls = [x for x in text.replace("\\N", "\n").split("\n") if x.strip()] or [text]
    if rows <= 1 or len(ls) == rows:
        return ls
    s = "".join(ls)
    k = -(-len(s) // rows)
    return [s[i:i + k] for i in range(0, len(s), k)]


def main_regions(doc: dict) -> set[int]:
    main = tracksio.main_track(doc)
    if not main:
        return set()
    return set(main.get("regions") or ([main["region"]] if "region" in main else []))


def region_align(doc: dict) -> dict[int, str]:
    return {r["index"]: r.get("align", "center") for r in doc.get("regions", [])}


def need_align(doc: dict, where: str) -> None:
    """叠加要阶段 2 判好的对齐方式；旧产物没有就当场报错，不静默退回居中。"""
    miss = [r["index"] for r in doc["regions"] if "align" not in r]
    if miss:
        raise ValueError(f"{where} 的区域没有对齐判定（regions[].align，缺 {len(miss)} 个）——"
                         "这份产物早于对齐判据，重跑 build_tracks（只是阶段 2，不重跑 OCR）")


def tracks_items(doc: dict, keep: str) -> list[Item]:
    """tracks → 条目：**一个事件一条、只画一次**，按轨挑事件（UI 的作用域跟着轨走）。

    * `keep="main"`：主轨的 cue 过一遍 `cue_lines`（和 `srt_main` 同一个行集）+ 名牌轨的事件；
    * `keep="all"`：全部区域轨，UI 事件也取（`drop_filtered=False`），按**那条区域轨自己的** `ui_lines`
      和全局的 `ui_footprint` 标成 UI；振り仮名（`ruby` 标）也取、按 UI 标（`dev` 画成 UI 的样子，查注音判据靠它）。
      主轨和名牌轨都是区域轨的另一种投影，不再走。
    都按事件 id 去重（跨轨、跨 cue）。"""
    picked: dict[int, bool] = {}
    cue_of: dict[int, str] = {}
    if keep == "main":
        main = tracksio.main_track(doc)
        if main is None:
            raise ValueError("这份产物没有主轨（provenance.main_track 为空），`default` 没有可叠的")
        for tr in [main, *(t for t in doc["tracks"] if t["kind"] == "nameplate")]:
            for cue in tr["cues"]:
                for e in tracksio.cue_lines(doc, tr, cue):
                    picked.setdefault(e["id"], False)
                    cue_of.setdefault(e["id"], cue["id"])
    else:
        for tr in doc["tracks"]:
            if tr["kind"] != "region":
                continue
            ui = set(tr.get("ui_lines") or ())
            for cue in tr["cues"]:
                for e in tracksio.cue_lines(doc, tr, cue, drop_filtered=False):
                    flagged = (e["text"].strip() in ui
                               or bool({"ui_footprint", "ruby"} & set(e.get("flags") or ())))
                    picked[e["id"]] = picked.get(e["id"], False) or flagged
                    cue_of.setdefault(e["id"], cue["id"])
    mains, aligns = main_regions(doc), region_align(doc)
    out = []
    for eid in sorted(picked):
        e = doc["events"][eid]
        moving = len(e.get("boxes") or []) >= 2
        out.append(Item(e["t_start"], e["t_end"], [list(e["box"])], [e["text"]],
                        "name" if "nameplate" in (e.get("flags") or []) else "line",
                        events=[e], region=e["region"], main=e["region"] in mains if mains else True,
                        ui=picked[eid], align="center" if moving else aligns.get(e["region"], "center"),
                        boxes=e["boxes"] if moving else None, cue=cue_of[eid]))
    return out


def project_items(items: list[dict], doc: dict, lang: str, layers, used: set[int] | None = None) -> list[Item]:
    """matched → 条目：**投影**，按层和语言把 `overlay_items` 判好的条目摆成 `Item`，不判断任何东西。
    这一层没有 `lang` 的译文就不叠（画面原样）。区域取条目第一个事件的；对齐取那个区域的，多行用公共轴。
    `used` 给了就把真画出来的条目用到的事件 id 记进去（`unmatched_items` 用）。"""
    mains, aligns = main_regions(doc), region_align(doc)
    out = []
    for it in items:
        item = _project_one(it, doc, lang, layers, mains, aligns)
        if item is None:
            continue
        out.append(item)
        if used is not None:
            used.update(it.get("events") or ())
    return out


def _project_one(it: dict, doc: dict, lang: str, layers, mains, aligns) -> Item | None:
    """一个 matched 条目 → `Item`；不在要的层里、或没有 `lang` 的译文就是 None（不叠）。"""
    if it["layer"] not in layers:
        return None
    evs = [doc["events"][i] for i in it.get("events") or []]
    reg = evs[0]["region"] if evs else -1
    kw = dict(region=reg, main=reg in mains if mains else True, align=aligns.get(reg, "center"), translated=True,
              layer=it["layer"], key=it.get("key"))
    if it["layer"] == "panel":
        if not it["paras"].get(lang):
            return None
        return Item(it["start_us"], it["end_us"], it["rows"], it["paras"][lang], "block", row_h=it["row_h"], **kw)
    text = it.get(lang)
    if not text:
        return None
    if it["layer"] in ("body", "extra"):
        return Item(it["start_us"], it["end_us"], it["rows"], overlay_lines(text, len(it["rows"])), "line",
                    events=evs, **kw)
    return Item(it["start_us"], it["end_us"], it["rows"], [text], it["layer"], **kw)   # choice / offmain / name：一块一行，不做打字机 / 淡入淡出


PANEL_COVER = 0.8
"""补画 OCR 原文时，框有这么多落在一块已画的阅读面板（`block`）里的事件，**面板在屏的那段时间**不画：面板条目不记它盖住的事件。"""


def unmatched_items(doc: dict, used: set[int], blocks: list[Item] = ()) -> list[Item]:
    """tracks 里没被任何已画出的 matched 条目用到的事件，照 tracks 的画法叠 OCR 原文（全部区域轨，UI 照标）。
    全部保留（`keep=all`）渲染 matched 时用：没认领上剧本的行、认领了但没这个语种译文的行、关掉的层都还在画面上，
    看得出匹配漏了什么（owner 2026-09-25）。已画的阅读面板底下的事件只扣掉面板在屏的时段（面板条目不记事件，按框和时间判），
    面板前后照画；扣剩不到一个采样间隔的碎段不画——面板的起止只有采样级精度，那一截在它的误差里。"""
    out = []
    for x in tracks_items(doc, "all"):
        e = x.events[0]
        if e["id"] in used:
            continue
        b = e["box"]
        a = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        cover = []
        for blk in blocks:
            r = blk.rows[0]
            if not (blk.start_us < x.end_us and x.start_us < blk.end_us):
                continue                       # 时间上不相交的面板和这条无关（同位置别的时段的面板不算）
            ix = max(0.0, min(b[2], r[2]) - max(b[0], r[0])) * max(0.0, min(b[3], r[3]) - max(b[1], r[1]))
            if a > 0 and ix >= PANEL_COVER * a:
                cover.append((blk.start_us, blk.end_us))
        if not cover:
            out.append(x)                      # 没被面板盖到的照原样（本身短于一个采样间隔的也画）
            continue
        pieces, s = [], x.start_us
        for cs, ce in sorted(cover):
            if cs > s:
                pieces.append((s, min(cs, x.end_us)))
            s = max(s, ce)
        pieces.append((s, x.end_us))
        out += [x if (ps, pe) == (x.start_us, x.end_us) else replace(x, start_us=ps, end_us=pe)
                for ps, pe in pieces if pe - ps >= doc["frame_us"]]
    return out


def native_frame_us(doc: dict) -> int:
    """原生帧长：从产物 `meta` 里 obs 带进来的源帧率算；没有就退到采样间隔（更保守）。"""
    fps = (doc.get("meta") or {}).get("src_fps")
    return int(round(1e6 / fps)) if fps else int(doc["frame_us"])


FRAGMENT_COVER = 0.9
"""框有这么多落在同一条目里**更早出现**的另一个事件框内的事件，算那一行的碎片（`typing_events`）。"""

SLOW_TW_FACTOR, SLOW_TW_MIN_ITEMS, SLOW_TW_MIN_CHARS = 3.0, 5, 8
"""打字机字速（毫秒 / 字）比这份字幕稿里**主轨**打字机条目的中位数慢这么多倍的，渲染时列出来报警（owner 2026-09-25：
明显慢于主轨上其他打字机的，先当成出了问题去查）。只比主轨上至少这么多字的条目：两三个字的碎片、注音跟着正文一起显出，
折成每字毫秒天然偏大，不说明问题；够格的条目不到这么多条就不算中位数、不报。"""


def typing_events(events: list[dict]) -> list[dict]:
    """算效果时刻用的事件：去掉**碎片**——框几乎整个落在同一条目里一个更早出现的事件框内的事件。
    det 偶尔在一两个采样点把一行静止的字切成两框，切出来的那块另起一个事件、被一起认领进这句台词，
    它的起点晚（一行早就打完了），拿它的"全字出现"会把打字机拖到它身上（gi-s2 129.5 s：
    两行 1 秒打完，被 135.5 s 冒出的半截行拖成 6 秒）。"""
    def area(b):
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    def inside(f, o):
        a = area(f["box"])
        ix = [max(f["box"][0], o["box"][0]), max(f["box"][1], o["box"][1]),
              min(f["box"][2], o["box"][2]), min(f["box"][3], o["box"][3])]
        return a > 0 and area(ix) >= FRAGMENT_COVER * a

    return [f for f in events
            if not any(o is not f and o["t_start"] < f["t_start"] and inside(f, o) for o in events)]


def effect_times(it: Item, doc: dict, frame_us: int, typewriter: bool = True) -> tuple[int, int, int]:
    """(打字机时长, 淡入, 淡出)，单位 µs；不做的是 0。只有不动的 `line` 才做。

    * 时刻只从 `typing_events` 取（一行被 det 切出来的碎片不算）。
    * 打字机：条目的事件被回抠过（带 `t_full_how`）就只认回抠的 `t_full`；整段素材判成 `instant`、而且是
      "字确实没有生长"那种（离散度 ≤ `INSTANT_MAX_SPREAD`）才不做，判不清的、没判定（样本 < 8）的按逐条的门走；没被回抠的（没回抠的产物、或 `--labels` / `--regions` 没挑到的区域）
      用采样级的 `t_full_sampled`。按事件判、不按整份产物判：只回抠了部分区域时，其余区域照样有采样级的效果。
      两种都要 ≥ `TW_MIN_FRAMES` 帧（回抠按原生帧、采样级和"全字没量出来"的回抠值按采样间隔）。
    * 淡出：`t_full_end`（回抠）到条目结束，不足一帧的不做——回抠会把没淡出的也填上，夹在结尾前一点。
      成员里有**开放边界**（`t_end_how = open`：没看到它消失）的整条不做——不知道它什么时候、怎么消失，
      不能借别的行的 `t_full_end` 给整句（多行译文连底板）做淡出。
    * 淡入：**实际没做**逐字显出时才复刻（首字出现 → 回抠的全字出现），不足 `TW_MIN_FRAMES` 个原生帧、
      或全字没量出来的不做，最长 `FADE_IN_MAX_US`。"""
    if it.kind != "line" or it.boxes or not it.events:
        return 0, 0, 0
    s, e = it.start_us, it.end_us
    refined = "text_effect" in doc and any("t_full_how" in ev for ev in it.events)
    core = typing_events(it.events)

    def last(key):
        v = [ev[key] for ev in core if ev.get(key) is not None]
        return min(max(v), e) if v else None
    full = last("t_full")
    # 回抠只量到首字、全字没量出来（`t_full_how.full == "none"`）时，t_full 是融合的回退值，精度是采样级的：
    # 打字机按采样间隔的门量，不然"多读两个字"骗出来的假打字机会从 2 个原生帧的门漏过去；也不拿它复刻淡入
    unmeasured = "none" in [(ev.get("t_full_how") or {}).get("full") for ev in core if ev.get("t_full") is not None]
    tw = 0
    if typewriter and refined:
        eff = doc["text_effect"]
        no_growth = eff.get("verdict") == "instant" and eff.get("iqr_over_median", 0.0) <= INSTANT_MAX_SPREAD
        step = doc["frame_us"] if unmeasured else frame_us
        if full is not None and not no_growth and full - s >= TW_MIN_FRAMES * step:
            tw = full - s
    elif typewriter:
        fs = last("t_full_sampled")
        if fs is not None and fs - s >= TW_MIN_FRAMES * doc["frame_us"]:
            tw = fs - s
    fend = None if any(ev.get("t_end_how") == "open" for ev in core) else last("t_full_end")
    fout = e - fend if fend is not None and e - fend >= frame_us else 0
    # 淡入也要 ≥ 2 个原生帧：即时出现的字回抠给的全字时刻晚一两帧，不该变成一个 1–2 帧的 \fad
    fin = full - s if tw == 0 and full is not None and not unmeasured and full - s >= TW_MIN_FRAMES * frame_us else 0
    return tw, min(fin, FADE_IN_MAX_US), fout


def effect_why(it: Item, doc: dict, tw: int, fin: int, fout: int) -> str | None:
    """效果判定的依据，一句话（只有 `why=on` 写进字幕稿，`dev` 审计用）。"""
    if it.kind != "line" or not it.events:
        return None
    if it.boxes:
        return "在动，不做效果"
    refined = "text_effect" in doc and any("t_full_how" in ev for ev in it.events)
    src = "回抠" if refined else "采样级"
    if refined and (doc["text_effect"] or {}).get("verdict"):
        src += f"，素材判 {doc['text_effect']['verdict']}"
    parts = [f"打字机 {tw / 1e6:.2f}s" if tw else "", f"淡入 {fin / 1e6:.2f}s" if fin else "",
             f"淡出 {fout / 1e6:.2f}s" if fout else ""]
    return "；".join(p for p in parts if p) + f"（{src}）" if any(parts) else f"无效果（{src}）"


def slow_typewriters(speeds: list[tuple[float, int]], label: str = "") -> list[tuple[float, int]]:
    """字速比打字机条目中位数慢 `SLOW_TW_FACTOR` 倍的（毫秒 / 字，起点 µs）。有就打一行报警、逐条列出时刻：
    这种多半不是原文打得慢，而是时刻被别的东西拖长了（碎片、错认领），要去查。"""
    if len(speeds) < SLOW_TW_MIN_ITEMS:
        return []
    med = sorted(v for v, _ in speeds)[len(speeds) // 2]
    slow = sorted(((v, t) for v, t in speeds if v > SLOW_TW_FACTOR * med), key=lambda x: x[1])
    if slow:
        where = "、".join(f"{t / 1e6:.1f} s（{v:.0f} ms/字）" for v, t in slow[:12]) + ("…" if len(slow) > 12 else "")
        print(f"⚠ {label + '：' if label else ''}打字机偏慢 {len(slow)} 条（中位数 {med:.0f} ms/字，超过 {SLOW_TW_FACTOR:g} 倍）：{where}——先当成出了问题去查")
    return slow


# ---- 写字幕稿 ----

def style_of(it: Item) -> str:
    if it.kind == "block":
        return "Panel"
    if it.kind == "choice":
        return "Choice"
    if it.kind == "offmain":
        return "Offmain"
    if it.ui:
        return "UI"
    if it.kind == "name":
        return "Name"
    return "Extra" if it.layer == "extra" else "Body"


def num(v: float) -> str:
    """框和轨迹的坐标写进 `fo`：整数写整数，其余照 `repr` 写全，读回来逐位相同。"""
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def rows_param(rows: list[list[float]]) -> str:
    """数值用空格分隔：参数在 Effect 字段里，逗号会切断 ASS 行的字段。"""
    return "|".join(" ".join(num(x) for x in r) for r in rows)


def path_param(boxes: list) -> str:
    return "|".join(f"{int(t)}:" + " ".join(num(x) for x in b) for t, b in boxes)


def view_rows(it: Item, H: int) -> list[list[float]]:
    """译文比原文框多出几行时，按原来一行的高度扩出去的行框（阶段 4 `layout.expand_rows` 同一规则；字幕稿的预览位置和字号按它算）。"""
    extra = len(it.lines) - len(it.rows)
    return layout.expand_rows(it.rows, extra, H) if it.translated and extra > 0 and it.kind != "block" else it.rows


def draft_line(it: Item, size: int, W: int, H: int, widths: dict, tw_us: int, fin: int, fout: int, why: str | None,
               font: str = export.FONT_CN) -> str:
    """一个条目 → 字幕稿里的一行源行：正文是一个主流 tag 块 + 纯文字（行间 `\\N`），阶段 4 的参数 `fo:…` 放在 Effect 字段——
    正文里没有参数，改字的时候碰不到（owner 2026-09-27 看 Aegisub 里的字幕稿后定：正文尽量是纯文字、便于编辑；参数放 Effect）。"""
    t0, t1 = export.ass_time(it.start_us, True), export.ass_time(it.end_us, False)
    actor = f"r{it.region}" if it.region >= 0 else ""
    ev = list(dict.fromkeys(e["id"] for e in it.events if "id" in e))       # matched 条目的事件可能重复列出
    fo: dict = {"ev": " ".join(map(str, ev)) or None, "key": it.key, "box": rows_param(it.rows)}
    if it.kind == "block":
        size, wrapped = layout.block_layout(it.rows[0], it.lines, it.row_h, font, widths)
        pos = "%.0f,%.0f" % (it.rows[0][0], it.rows[0][1])
        fo.update({"wrap": it.row_h, "plate": "box", "off": not it.main,
                   "was": f"pos:{pos.replace(',', ' ')}|fs:{size}|an:7"})
        head = "{\\an7\\pos(%s)\\fs%d\\q2}" % (pos, size)
        body = "\\N".join(export.ass_text(ln) for ln in wrapped)
        return f"Dialogue: 0,{t0},{t1},{style_of(it)},{actor},0,0,0,{assfile.encode_fo(fo)},{head}{body}"
    rows = view_rows(it, H)
    tw = [layout.line_width(font, ln, widths) * size for ln in it.lines]
    per_row = len(it.lines) == len(rows)
    axes = layout.axes_in_screen(rows, tw if per_row else [max(tw, default=0.0)] * len(rows),
                                 it.align, it.translated, W)
    x = axes[0]
    y = (rows[0][1] + rows[-1][3]) / 2
    if it.boxes:                                      # 预览：整条一个 \move 从首点到末点，精确轨迹在 path 里
        p0, p1 = tuple(it.boxes[0][1][:2]), tuple(it.boxes[-1][1][:2])
        base = (it.rows[0][0], it.rows[0][1])
        where = layout.pos_tag(x, y, (it.start_us, it.end_us, p0, p1), base)
    else:
        where = "\\pos(%.0f,%.0f)" % (x, y)
    owned = where[1:].replace("(", ":", 1).rstrip(")").replace(",", " ")  # "pos:x y" / "move:x0 y0 x1 y1 0 t"
    # 原文框本来装几行：OCR 原文带换行时一个框装着几行（tracks 事件本身带换行），比框数多；阶段 4 只把超出的行往外扩
    cap = sum(len([s for s in ln.split("\n") if s.strip()]) or 1 for ln in it.lines) if not it.translated else len(it.rows)
    fo.update({"fit": "tr" if it.translated else "src", "rows": True, "plate": True,
               "cap": cap if cap != len(it.rows) else None,
               "path": path_param(it.boxes) if it.boxes else None, "tw": tw_us // 1000 or None,
               "off": not it.main, "why": why, "was": f"{owned}|fs:{size}|an:{layout.ANCHOR[it.align]}"})
    fad = "\\fad(%d,%d)" % (fin // 1000, fout // 1000) if fin or fout else ""
    head = "{\\an%d%s\\fs%d%s}" % (layout.ANCHOR[it.align], where, size, fad)
    body = "\\N".join(export.ass_text(ln) for ln in it.lines)
    return f"Dialogue: 0,{t0},{t1},{style_of(it)},{actor},0,0,0,{assfile.encode_fo(fo)},{head}{body}"


def style_lines(font: str = export.FONT_CN) -> list[str]:
    """字幕稿的样式：一个角色一个，参数相同。`BorderStyle=3` 出预览底框（各渲染器都认，框用 OutlineColour、外扩 `OVERLAY_PAD`）；
    次要色全透明、阴影 0：人要自己调某一行的打字机节奏时写 `\\k` 族 tag，预览就是逐字显出（`\\ko` 藏不住阴影，第 0 步探针量过）。"""
    return [f"Style: {s},{font},48,&H00FFFFFF,&HFF000000,{PREVIEW_BOX},&H00000000,"
            f"0,0,0,0,100,100,0,0,3,{layout.OVERLAY_PAD},0,5,0,0,0,1" for s in STYLES]


def export_script(path: Path, items: list[Item], doc: dict, typewriter: bool = True, fade: bool = True,
                  why: bool = False, info: dict | None = None) -> tuple[Counter, dict]:
    """把条目写成字幕稿。返回（计数：各种类、打字机 / 淡入淡出条数、对齐；量好的字宽——连跑阶段 4 时交给它，不用再量一遍）。

    * 字号 = `layout.fit_line`（原文只按框，译文有保底、不超画面）；同栏同时在屏的选项取中位数（`layout.same_size`）；
      面板按 `layout.block_layout` 折行；
    * 位置：单行就是阶段 4 的位置；多行是整组的锚轴和几行的竖直中心（阶段 4 按原框逐行摆，预览里行距是字体的）；
      在动的写一个首点到末点的 `\\move` 供预览，精确轨迹在 `fo` 的 `path`；
    * 效果见 `effect_times`，打字机记成 `fo` 的 `tw`（正文保持纯文字）、淡入淡出写成 `\\fad`。"""
    W, H = doc["size"]
    frame_us = native_frame_us(doc)
    font = export.FONT_CN
    widths = export.ink_widths((font, "\n".join(layout.width_keys(it.lines, it.kind == "block"))) for it in items)
    sizes = [layout.fit_line(view_rows(it, H), it.lines, font, widths, it.translated, W) if it.kind != "block" else 0
             for it in items]
    choice = [i for i, it in enumerate(items) if it.kind in ("choice", "offmain")]
    for i, s in zip(choice, layout.same_size([(items[i].start_us, items[i].end_us, items[i].rows[0]) for i in choice],
                                             [sizes[i] for i in choice])):
        sizes[i] = s
    lines_out, n = [], Counter()
    speeds: list[tuple[float, int]] = []                     # 主轨打字机条目的（毫秒 / 字，起点），见 slow_typewriters
    for it, size in zip(items, sizes):
        n[it.kind] += 1
        tw_us, fin, fout = effect_times(it, doc, frame_us, typewriter) if it.kind != "block" else (0, 0, 0)
        if not fade:
            fin = fout = 0
        n["typewriter"] += tw_us > 0
        n["fade"] += bool(fin or fout)
        if it.kind != "block":
            n[f"align_{it.align}"] += 1
        n_chars = sum(len(ln) for ln in it.lines)
        if tw_us > 0 and it.main and n_chars >= SLOW_TW_MIN_CHARS:
            speeds.append((tw_us / 1000 / (n_chars - 1), it.start_us))
        lines_out.append(draft_line(it, size, W, H, widths, tw_us, fin, fout,
                                    effect_why(it, doc, tw_us, fin, fout) if why else None, font))
    head = {"FlowOCR Script": FORMAT_VERSION, **(info or {})}
    Path(path).write_text(export.ass_doc(W, H, style_lines(font), lines_out, info=head), encoding="utf-8")
    n["typewriter_slow"] = len(slow_typewriters(speeds, Path(path).name))
    return n, widths


KEEPS = ("main", "all")


def events_fingerprint(doc: dict) -> str:
    """tracks 的 `events[]` 的内容指纹（sha256 前 12 位）。字幕稿里的 `ev` 是 `events[]` 的下标，重跑阶段 2 会重新编号——
    以后要按 `ev` 回到产物（合并、回流），先核字幕稿记的这个指纹和手上的产物对不对得上。"""
    blob = json.dumps(doc["events"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def render_script(document: dict, output_dir, options: dict | None, suffix: str = ""
                  ) -> tuple[Path, Counter, list[Item], dict]:
    """阶段 3 的实现：吃 tracks（叠 OCR 原文）或 matched（叠剧本译文），写一份字幕稿。

    options（字符串，`render --opt` 原样给的）：
    * `keep`：`main`（默认：tracks 主轨 + 名牌轨；matched 的 `body` / `extra` / `name`）/ `all`（全部区域轨、全部层，
      matched 另把没叠译文的事件照 tracks 画 OCR 原文，见 `unmatched_items`）；
    * `why`：`on` 把效果判定的依据写进每行的 `fo`（dev 审计用）；
    * `typewriter`（off / on / dim，dim 在这里等于 on）、`fade`（off / on）：off 就不判、不写；
    * matched 还有 `lang`（jp，默认）/ `cn`、`layers`（逗号分隔，默认随 `keep`）、
      `tracks`（产它的那份 `*-tracks.json`，默认按 `provenance.subs` 找）；
    * `name`：文件名（默认 `<tag>[-<lang>]-<suffix>.script.ass`，`suffix` 默认是 `keep`；叠加预设给的是预设名）。"""
    o = dict(options or {})
    keep = str(o.get("keep", "main"))
    if keep not in KEEPS:
        raise ValueError(f"keep 只能取 {list(KEEPS)}：{keep!r}")
    typewriter = str(o.get("typewriter", "on"))
    if typewriter not in TYPEWRITER_MODES:
        raise ValueError(f"typewriter 只能取 {list(TYPEWRITER_MODES)}：{typewriter!r}")
    fade = str(o.get("fade", "on"))
    if fade not in ("off", "on"):
        raise ValueError(f"fade 只能是 off / on：{fade!r}")
    why = str(o.get("why", "off"))
    if why not in ("off", "on"):
        raise ValueError(f"why 只能是 off / on：{why!r}")
    layers = (tuple(x for x in str(o["layers"]).split(",") if x) if o.get("layers")
              else ("body", "extra", "name") if keep == "main" else LAYERS)
    if not set(layers) <= set(LAYERS):
        raise ValueError(f"layers 只能取 {','.join(LAYERS)} 里的：{o.get('layers')}")
    out = Path(output_dir)
    if document.get("schema") == matchedio.SCHEMA:
        matchedio.validate(document, need_overlay=True)
        lang = str(o.get("lang", "jp"))
        if lang not in ("jp", "cn"):
            raise ValueError(f"lang 只能是 jp / cn：{lang!r}")
        tracks = str(o.get("tracks") or matchedio.tracks_path(document))
        if not tracks or not Path(tracks).is_file():
            raise FileNotFoundError(
                f"找不到这份匹配产物对应的 tracks（provenance.subs = {matchedio.tracks_path(document)!r}）；"
                f"叠加要它的框和画面尺寸，用 options['tracks'] 指过去")
        doc = tracksio.load(Path(tracks))
        need_align(doc, tracks)
        used: set[int] = set()
        items = project_items(document["overlay"]["items"], doc, lang, layers, used)
        note = f"剧本译文 {lang}，层 {','.join(layers)}"
        if keep == "all":
            rest = unmatched_items(doc, used, [it for it in items if it.kind == "block"])
            items += rest
            note += f"，另 {len(rest)} 个没叠译文的事件画 OCR 原文"
        tag = doc.get("tag") or Path(tracks).stem.removesuffix("-refined").removesuffix("-tracks")
        base = f"{tag}-{lang}"
        source = f"{tag} {matchedio.SCHEMA}"
    else:
        doc = document
        need_align(doc, str(doc.get("tag") or "这份 tracks"))
        items = tracks_items(doc, keep)
        base = str(doc.get("tag") or "tracks")
        note = f"OCR 原文，{'主轨 + 名牌' if keep == 'main' else '全部区域轨'}"
        source = f"{base} {tracksio.SCHEMA}"
    out.mkdir(parents=True, exist_ok=True)
    p = out / str(o.get("name") or f"{base}-{suffix or keep}.script.ass")
    info = {"FlowOCR Source": f"{source} events-sha256:{events_fingerprint(doc)}",
            "FlowOCR Code": provenance.git_head(), "FlowOCR Note": note}
    n, widths = export_script(p, items, doc, typewriter != "off", fade == "on", why == "on", info)
    kinds = "、".join(f"{k} {n[k]}" for k in ("line", "name", "choice", "offmain", "block") if n[k])
    aligns = "、".join(f"{a} {n['align_' + a]}" for a in tracksio.ALIGNS if n["align_" + a])
    print(f"-> {p}（字幕稿，{len(items)} 条：{kinds}；对齐 {aligns}；打字机 {n['typewriter']} 条、淡入淡出 {n['fade']} 条）")
    return p, n, items, widths
