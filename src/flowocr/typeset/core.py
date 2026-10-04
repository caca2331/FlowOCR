"""阶段 4 的实现：读一份字幕稿（或它自己的输出），按源行生成最终的叠加行。

方案：script-fx 计划。约定（artifacts.md 的字幕稿一节）：

* 源行 = `[Events]` 里 Effect 是 `fo:…`（阶段 3 写的参数）或空的 `Dialogue:`。处理过的源行改成 `Comment:`、
  Effect 前面加 `fo-src|`；生成行 Effect 标 `fo-fx`、样式用派生的 `<Style>.fo-fx`。
  别的行（用户自己注释掉的、别人的 `fx`、`Banner;`…）原样留着。
* `restore` 删掉 `fo-fx` 行和派生样式、把 `fo-src|` 行恢复成源行，得到逐字节相同的字幕稿；处理之前先自动 `restore`，
  所以可以反复重跑。
* 带 `fo` 参数的源行按参数生成，**固定顺序**：排版（分行、字号、每行锚点、量文字边界）→ 元素（正文、底板、编号，
  外加扩展的元素）→ 运动（所有元素一起按 `path` 逐段展开）→ 时间效果（逐字 `\\alpha`、`\\fad`、配色）。
  不带 `fo` 的源行（手补的）照主流 tag 原样生成一条，只换样式。
* 谁说了算（owner 2026-09-27）：`fo` 的 `was` 记着阶段 3 写下的、归阶段 4 管的 tag 的值；源行里的值不一样就是人改过，
  这一项以人为准、管它的参数在这一行上作废，打一行提示。
* 打字机：`fo` 的 `tw`（首字出现到全字出现的毫秒数）逐字匀速显出；人在正文里写了 `\\k` 族 tag 的行按人写的节奏，`tw` 不用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from flowocr import extensions
from flowocr.output import export, layout
from flowocr.typeset import assfile

EFFECT_SRC, EFFECT_FX = "fo-src|", "fo-fx"
"""处理过的源行 Effect 的前缀（后面是原来的 Effect，`restore` 去掉它）；生成行的 Effect。"""
FX_STYLE_SUFFIX = ".fo-fx"
"""派生样式名的后缀。`restore` 按它删派生样式，所以要一个用户不会自己起的名字（`.fx` 会撞上手起的 `Sign.fx`）。"""
INFO_KEY = "FlowOCR Typeset"

OVERLAY_COLOR = "101010"
"""底板颜色（ASS 的 BBGGRR）：近黑。透明度另给（`plate_alpha`）。"""
OFFMAIN_COLOR = "FFD773"
"""`dev` 里非主轨聚类的字色（BBGGRR）：#73d7ff 浅蓝（owner 2026-09-25：原来的淡红和白字区别不够明显）。"""
UI_COLOR = "33E0FF"
"""`dev` 里被判成常驻 UI 的字色（BBGGRR）：#ffe033 黄（owner 2026-09-25，原来是灰）。"""
LABEL_SCALE, LABEL_MIN = 0.6, 18
"""`dev` 的聚类编号：正文字号的这么多倍，不小于这个字号（ASS 的 Fontsize）。"""
LABEL_COLOR, LABEL_INSET = "2D2DFF", 3
"""`dev` 的聚类编号：字色 #ff2d2d（BBGGRR），画在这条字幕底板外接矩形的**内侧**右上角、离边这么多像素
（owner 2026-09-25：原来在底板外侧、太小，看不清也分不清属于哪条）。"""

FX_STYLE = {"OutlineColour": "&H00101010", "BackColour": "&H00000000", "BorderStyle": "1", "Outline": "2", "Shadow": "0"}
"""派生样式 `<Style>.fo-fx` 覆盖源样式的这几项：字幕稿的样式用 `BorderStyle=3` 出预览底框（没有描边），
生成行要的是现在叠加的样子（近黑描边 2、没有框、没有阴影）。字体、颜色、字号这些跟着源样式走。"""

TYPEWRITER_MODES = ("off", "on", "dim")
FO_PARAMS = {"ev", "key", "box", "fit", "rows", "cap", "plate", "path", "tw", "wrap", "off", "why", "was", "spk"}
"""内置认得的 `fo` 参数；扩展效果的名字另加。不认识的当场报错（`unknown=ignore` 才跳过）。"""
KIND_OF_STYLE = {"Panel": "block", "Choice": "choice", "Offmain": "offmain", "Name": "name"}


@dataclass(frozen=True)
class Look:
    """阶段 4 的一个预设：长什么样。`label`：底板内侧右上角画区域号；`colors`：UI 黄字、非主轨浅蓝。"""
    alpha: int
    plate: str
    typewriter: str
    fade: str
    label: bool
    colors: bool


LOOKS = {
    "default": Look(0x20, "rows", "on", "on", False, False),
    # owner 2026-09-24：开发看——底板 50%、UI 黄字、非主轨浅蓝、底板内侧右上角红字区域号（颜色与编号位置 09-25 改）
    "dev": Look(0x80, "rows", "on", "on", True, True),
}


@dataclass
class Element:
    """一条生成行的内容：行首 tag 块是 `head` + 位置 + `tail` + 淡入淡出 + `tail2`，后面接 `body`。
    位置由 `(x, y)` 按轨迹算（`layout.pos_tag`），`where` 给了就直接用它（人改过位置的行）。
    `fade`：这个元素跟着源行淡入淡出；`tokens`：逐字显出的正文记号（`body` 由它按段重算）——
    在动的行按轨迹拆成几段时，时间效果按**源行**的时间算、换成各段的相对时间，不在每段从头放一遍。"""
    layer: int
    x: float
    y: float
    head: str
    tail: str
    body: str
    kind: str
    where: str | None = None
    tail2: str = ""
    fade: bool = False
    tokens: list | None = None


@dataclass
class Line:
    """一条源行排好版的样子，扩展效果的挂点读它（只读）。"""
    style: str
    actor: str
    params: dict
    start_us: int
    end_us: int
    region: int
    kind: str
    rows: list[list[float]]
    lines: list[str]
    size: int
    align: str
    plates: list[list[float]] = field(default_factory=list)
    look: Look | None = None


# ---- 还原 ----

def restore(doc: assfile.Doc, origin: list[int] | None = None) -> int:
    """删掉 `fo-fx` 行、派生样式和阶段 4 写的 `[Script Info]` 键，把 `fo-src|` 行恢复成源行（去掉前缀）。返回恢复的源行数。
    `origin` 给了就填上 `[Events]` 里留下的每一行原来是这一节的第几行（报提示时按用户打开的那份文件报行号）。"""
    n = 0
    ev = doc.section("Events")
    if ev is not None:
        names = assfile.format_of(ev) or []
        kept = []
        for i, ln in enumerate(ev.lines):
            r = assfile.parse_row(ln, names, assfile.EVENT_KINDS) if names else None
            if r is not None and r.get("Effect").strip() == EFFECT_FX:
                continue
            if r is not None and r.kind == "Comment" and r.get("Effect").startswith(EFFECT_SRC):
                r.kind = "Dialogue"
                r.set("Effect", r.get("Effect")[len(EFFECT_SRC):])
                ln = str(r)
                n += 1
            kept.append(ln)
            if origin is not None:
                origin.append(i)
        ev.lines = kept
    st = doc.section("V4+ Styles")
    if st is not None:
        names = assfile.format_of(st) or []
        st.lines = [ln for ln in st.lines
                    if not ((r := assfile.parse_row(ln, names, ("Style",))) and r.get("Name").endswith(FX_STYLE_SUFFIX))]
    info = doc.section("Script Info")
    if info is not None:
        info.lines = [ln for ln in info.lines if not ln.startswith(INFO_KEY + ":")]
    return n


# ---- 读源行 ----

def cs_of(t: str) -> int:
    h, m, s = t.strip().split(":")
    return (int(h) * 3600 + int(m) * 60) * 100 + round(float(s) * 100)


def parse_was(v) -> dict[str, str]:
    if not isinstance(v, str):
        return {}
    return dict(x.split(":", 1) for x in v.split("|") if ":" in x)


def box4(s: str) -> list[float]:
    b = assfile.numbers(s)
    if len(b) != 4:
        raise ValueError(f"框要 4 个数（x0 y0 x1 y1），给的是 {s!r}")
    return b


def rows_of(v) -> list[list[float]]:
    return [box4(r) for r in str(v).split("|") if r]


def path_of(v) -> list:
    out = []
    for p in str(v).split("|"):
        t, b = p.split(":", 1)
        out.append([int(t), box4(b)])
    return out


def text_rows(text: str) -> tuple[list[list[tuple]], list[str], str, int | None]:
    """去掉 `fo` 之后的正文 → (逐行的记号, 逐行的纯文字, 行首块里不归阶段 4 管的 tag, 全字出现的时刻毫秒)。
    记号是 ("tags", 块内容) / ("ch", 字, 出现时刻毫秒或 None) / ("nl",)（行里的软换行 `\\n` 不算分行）。
    卡拉OK（人手写的节奏）：每个 `\\k` 族 tag 开一个音节，音节里的字在音节起点出现（音节起点 = 前面各音节时长之和，厘秒）；
    全字出现 = 最后一个音节的起点；没有卡拉OK是 None。"""
    rows: list[list[tuple]] = [[]]
    t_cs, cur, has_k = 0, None, False
    lead = ""
    parts = assfile.blocks(text)
    for i, (kind, s) in enumerate(parts):
        if kind == "tags":
            kept = []
            for t in assfile.split_tags(s):                    # 顶层的 tag：\t(…,\fs80) 里面的不单算
                got = assfile.tag_of(t)
                if got and got[0] == "k":                      # 卡拉OK 计时换成逐字 \alpha，不留
                    cur = t_cs
                    t_cs += int(got[1])
                    has_k = True
                    continue
                if got and i == 0:                             # 行首块里归阶段 4 管的（\an \pos \move \fs \fad）由阶段 4 重写
                    continue
                kept.append(t)                                 # 其余（行内的 \fs80、\1c、\t(…)…）原样留着
            rest = "".join(kept)
            if i == 0:
                lead = rest
            elif rest.strip():
                rows[-1].append(("tags", rest))
            continue
        j = 0
        while j < len(s):
            if s.startswith("\\N", j):
                rows.append([])
                j += 2
                continue
            if s.startswith("\\n", j):
                rows[-1].append(("nl",))
                j += 2
                continue
            ch = "\\h" if s.startswith("\\h", j) else s[j]              # 硬空格 \h 是一个字：逐字 \alpha 不能插进它中间
            rows[-1].append(("ch", ch, None if cur is None else cur * 10))
            j += len(ch)
    plain = ["".join(PLAIN.get(t[1], t[1]) for t in r if t[0] == "ch") for r in rows]
    return rows, plain, lead, cur * 10 if has_k else None


PLAIN = {"\\h": " "}
"""纯文字（量字宽、折行、给扩展效果看）里转义换成的字：`\\h` 是不断行的空格。"""


def with_tw(rows: list[list[tuple]], tw_ms: int, nl_slot: bool) -> list[list[tuple]]:
    """`tw` 匀速逐字：几行合起来的第 k 个位置（从 0 数）在 `tw_ms * k // (n - 1)` 毫秒出现——首字在起点、末字正好在全字出现。
    `nl_slot`：行里的换行（整块画在一条里的几行）也占一个位置（和拆之前逐字 `\\alpha` 的算法一样：tracks 事件本身带的换行算一个字）；
    按原框逐行摆的几行之间不占。"""
    kinds = ("ch", "nl") if nl_slot else ("ch",)
    n = sum(t[0] in kinds for r in rows for t in r)
    last, k, out = max(1, n - 1), 0, []
    for r in rows:
        row = []
        for t in r:
            if t[0] == "ch":
                row.append(("ch", t[1], tw_ms * k // last))
            else:
                row.append(t)
            k += t[0] in kinds
        out.append(row)
    return out


def body_of(tokens: list[tuple], mode: str, full_ms: int | None = None) -> str:
    """一行的正文：`on` 逐字 `\\alpha`（没出现的字全透明但照样占位置，居中的行不会边打边挪）；
    `dim` 全字出现（`full_ms`）之前整行半透明；`off` 直接显出。"""
    if mode == "dim":
        plain = body_of(tokens, "off")
        return ("{\\alpha&H80&\\t(%d,%d,\\alpha&H00&)}" % (full_ms, full_ms + 1) + plain) if full_ms is not None else plain
    out = []
    for t in tokens:
        if t[0] == "tags":
            out.append("{" + t[1] + "}")
        elif t[0] == "nl":
            out.append("\\N")
        elif mode == "on" and t[2] is not None:
            out.append("{\\alpha&HFF&\\t(%d,%d,\\alpha&H00&)}" % (t[2], t[2] + 1) + t[1])
        else:
            out.append(t[1])
    return "".join(out)


def rows_joined(rows: list[list[tuple]]) -> list[tuple]:
    """几行并成一行（行间 `\\N`）：一个原文框、译文却有几行时，整块画在这个框里。"""
    out: list[tuple] = []
    for k, r in enumerate(rows):
        if k:
            out.append(("nl",))
        out += r
    return out


@dataclass
class Src:
    """一条要处理的源行。"""
    no: int                                    # 文件里的行号（从 1 数），报提示用
    row: assfile.Row
    fo: dict | None
    text: str

    @property
    def style(self) -> str:
        return self.row.get("Style").strip()

    @property
    def actor(self) -> str:
        return self.row.get("Name")


# ---- 生成 ----

def derive_style(r: assfile.Row, name: str) -> str:
    """源样式 `r` → 派生样式 `<name>.fo-fx`（`FX_STYLE` 那几项换掉，其余照抄）。"""
    d = replace(r, fields=list(r.fields))
    d.set("Name", name + FX_STYLE_SUFFIX)
    for k, v in FX_STYLE.items():
        if k in r.names:
            d.set(k, v)
    return str(d)


def plate_el(rect: list[float], back: str) -> Element:
    """一块底板：矩形 `[x0, y0, x1, y1]` 画成一条绘图（左上角锚点），`back` 是颜色和透明度；跟着源行淡入淡出。"""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    return Element(0, rect[0], rect[1], "\\an7", "\\p1\\bord0\\shad0" + back,
                   "m 0 0 l %.0f 0 %.0f %.0f 0 %.0f{\\p0}" % (w, w, h, h), "plate", fade=True)


def carry_rows(rows: list[list[tuple]]) -> list[list[tuple]]:
    """按原框逐行摆时每行是一条单独的 Dialogue；前面几行里的行内 tag（`A{\\1c&H0000FF&}B\\NCD` 的颜色）在源行里对下一行照样生效，
    所以按顺序补到下一行开头（后出现的覆盖先出现的，和 libass 读一整行时一样）。"""
    out, carried = [], []
    for r in rows:
        out.append(([("tags", "".join(carried))] if carried else []) + r)
        carried += [t[1] for t in r if t[0] == "tags"]
    return out


def runs_of(tokens: list[tuple]) -> list[list[tuple[str, float | None]]]:
    """一条的记号 → 看得见的每一行（行里的 `\\n` / 并起来的几行分开）按行内 `\\fs` 切成的几段 (文字, 字号；None = 整行字号)。
    `\\r` 回到样式的字号。`\\t(…,\\fs80)` 这种渐变不算（量的是起始的样子）。"""
    lines: list[list[tuple[str, float | None]]] = [[]]
    size, cur = None, ""
    for t in tokens:
        if t[0] == "tags":
            new = size
            for tag in assfile.split_tags(t[1]):
                got = assfile.tag_of(tag)
                if got and got[0] == "fs":
                    new = float(got[1])
                elif re.fullmatch(r"\\r[^\\]*", tag.rstrip()):
                    new = None
            if new != size:
                if cur:
                    lines[-1].append((cur, size))
                cur, size = "", new
        elif t[0] == "nl":
            if cur:
                lines[-1].append((cur, size))
            cur = ""
            lines.append([])
        else:
            cur += PLAIN.get(t[1], t[1])
    if cur:
        lines[-1].append((cur, size))
    return lines


def tokens_width(tokens: list[tuple], size: int, font: str, widths: dict) -> float:
    """一条正文画出来的宽（像素）：看得见的每一行里各段的墨迹宽 × 各自的字号，取最宽的那行。行内没有 `\\fs` 时就是整行 × 字号。"""
    return max((sum(widths.get((font, s), 0.0) * (sz or size) for s, sz in ln) for ln in runs_of(tokens)), default=0.0)


def shift_times(block: str, a: int, dur: int) -> str:
    """一个 tag 块里人写的 `\\t(…)` / `\\fade(…)` 的时刻（相对源行起点）改成相对这一段的起点（源行起点后 `a` 毫秒）：
    段接段连起来和一整条相同，不在每段从头变一遍。`\\t` 没写时刻或结束时刻写 0 的是整条 `[0, dur]`（libass 把结束 0 读成到行尾）；
    时刻减完可以是负的（libass 照算进度），但段开始前已经变完的 `\\t` 换成里面的 tag 本身——结束时刻减到正好 0 会被当成到段尾。"""
    out = []
    for tag in assfile.split_tags(block):
        m = re.fullmatch(r"\\(t|fade)\((.*)\)\s*", tag, re.S)
        if not m:
            out.append(tag)
            continue
        args = split_args(m[2])
        if m[1] == "fade" and len(args) == 7:
            out.append("\\fade(%s,%s,%s,%d,%d,%d,%d)" % (*args[:3], *(int(float(v)) - a for v in args[3:])))
            continue
        if m[1] == "t":
            nums = [x for x in args if not x.lstrip().startswith("\\")]
            rest = ",".join(x for x in args if x.lstrip().startswith("\\"))
            t1, t2 = (float(nums[0]), float(nums[1]) or float(dur)) if len(nums) >= 2 else (0.0, float(dur))
            if t2 - a <= 0:
                out.append(rest)
                continue
            accel = nums[2] if len(nums) >= 3 else nums[0] if len(nums) == 1 else None
            out.append("\\t(%d,%d,%s%s)" % (t1 - a, t2 - a, accel + "," if accel else "", rest))
            continue
        out.append(tag)
    return "".join(out)


def split_args(s: str) -> list[str]:
    """tag 参数按顶层逗号切（`\\t(0,500,\\clip(1,2,3,4))` 里 clip 的逗号不切）。"""
    out, cur, depth = [], "", 0
    for c in s:
        if c == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += c
        depth += (c == "(") - (c == ")")
    out.append(cur)
    return out


def shift_tokens(tokens: list[tuple], off_ms: int, dur: int) -> list[tuple]:
    """逐字显出的时刻改成从这一段的起点（源行起点后 `off_ms`）算，在这之前已经出现的字直接显出；
    行内人写的 `\\t` / `\\fade` 也换算（`shift_times`）。"""
    out = []
    for t in tokens:
        if t[0] == "ch" and t[2] is not None:
            out.append(("ch", t[1], t[2] - off_ms if t[2] - off_ms > 0 else None))
        elif t[0] == "tags":
            out.append(("tags", shift_times(t[1], off_ms, dur)))
        else:
            out.append(t)
    return out


def fade_tag(fin: int, fout: int, dur: int, a: int, whole: bool) -> str:
    """源行 `[0, dur]` 毫秒上的淡入 `fin` / 淡出 `fout`，落在从 `a` 开始的这一段里的样子。整条就一段（`whole`）时是原样的 `\\fad`；
    在动的行按轨迹拆成几段时用七参数的 `\\fade`（透明度 255 = 全透明），段接段连起来和一整条淡入淡出相同，不在每段从头淡一遍。"""
    if not fin and not fout:
        return ""
    if whole:
        return "\\fad(%d,%d)" % (fin, fout)
    t3 = dur - fout - a if fout else dur - a + 1                       # 没有淡出：透明度到行尾都是 0
    if fin and a < fin:
        return "\\fade(%d,0,255,0,%d,%d,%d)" % (round(255 * (1 - a / fin)), fin - a, t3, max(t3, dur - a))
    if not fout:
        return ""
    if a < dur - fout:
        return "\\fade(0,0,255,0,0,%d,%d)" % (t3, dur - a)
    x = round(255 * (a - (dur - fout)) / fout)                         # 这一段从淡出中间开始
    return "\\fade(%d,%d,255,0,0,0,%d)" % (x, x, dur - a)


def dialogue(layer: int, s_us: int, e_us: int, style: str, actor: str, text: str) -> str:
    return (f"Dialogue: {layer},{export.ass_time(s_us, True)},{export.ass_time(e_us, False)},"
            f"{style}{FX_STYLE_SUFFIX},{actor},0,0,0,{EFFECT_FX},{text}")


class Typesetter:
    """一次处理：`look` + 选项 + 扩展效果。`notes` 收提示（行号, 话），`counts` 收计数。"""

    def __init__(self, look: Look, fx_mods: tuple = (), unknown: str = "error", widths: dict | None = None):
        self.look = look
        self.fx = {m.NAME: m for m in fx_mods}
        self.unknown = unknown
        self.widths = widths                    # 已经量好的字宽（连跑时阶段 3 交过来的），量过的不再起 ffmpeg
        self.H = 1080                           # 画面高（run 时从 PlayResY 读）：扩出行时别扩出画面
        self.notes: list[tuple[int, str]] = []
        self.counts: dict[str, int] = {}

    def count(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n

    def run(self, doc: assfile.Doc) -> None:
        ev0 = doc.section("Events")
        base = (sum(len(s.lines) + (s.name is not None) for s in doc.sections[:doc.sections.index(ev0)]) + 1
                if ev0 is not None else 0)                       # 按输入文件数行号：restore 之前算
        origin: list[int] = []
        restore(doc, origin)
        info = doc.section("Script Info")
        def play_res(key: str, default: int) -> int:
            return int(next((ln.split(":", 1)[1] for ln in info.lines if ln.startswith(key + ":")), default)) if info else default
        W = play_res("PlayResX", 1920)
        self.H = play_res("PlayResY", 1080)
        st_sec, ev_sec = doc.section("V4+ Styles"), doc.section("Events")
        if ev_sec is None:
            raise ValueError("没有 [Events] 节，不是字幕稿")
        st_names = assfile.format_of(st_sec) or [] if st_sec else []
        styles = {r.get("Name").strip(): r for ln in (st_sec.lines if st_sec else [])
                  if (r := assfile.parse_row(ln, st_names, ("Style",)))}
        ev_names = assfile.format_of(ev_sec)
        if not ev_names or ev_names[-1] != "Text":
            raise ValueError("[Events] 的 Format 不对（最后一个字段要是 Text）")
        srcs: dict[int, Src] = {}
        for i, ln in enumerate(ev_sec.lines):
            r = assfile.parse_row(ln, ev_names, assfile.EVENT_KINDS)
            if r is None or r.kind != "Dialogue":
                continue
            no = base + origin[i] + 1
            fo = assfile.parse_fo(r.get("Effect"))
            if fo is None and r.get("Effect").strip():
                self.notes.append((no, f"Effect 列是 {r.get('Effect').strip()[:40]!r}，不是源行，原样保留"))
                continue
            srcs[i] = Src(no, r, fo, r.get("Text"))
        self.check_params(srcs.values())
        used = sorted({s.style for s in srcs.values()})
        fallback = styles.get("Default") or next(iter(styles.values()), None)
        base_style = {s: styles.get(s) or fallback for s in used}
        for s in used:
            if s not in styles:
                self.notes.append((0, f"样式 {s!r} 不存在，派生样式照 {fallback.get('Name').strip() if fallback else '（无）'} 生成"))
        fonts = {s: r.get("Fontname").strip() if r else export.FONT_CN for s, r in base_style.items()}
        for s, f in fonts.items():
            if f not in export.FONT_H_RATIO:
                self.notes.append((0, f"样式 {s!r} 的字体 {f!r} 没有量过框高换算（export.FONT_H_RATIO），字号适配按 {export.FONT_CN} 的比例"))
        laid, bad = {}, []
        for i, src in srcs.items():
            if src.fo is None:
                continue
            try:
                laid[i] = self.lay_out(src, fonts[src.style], W)
            except (ValueError, KeyError, IndexError) as e:        # 人手改坏的参数值（tw=1.5s、box 少一个数…）
                bad.append(f"第 {src.no} 行：{type(e).__name__}: {e}")
        if bad:
            raise ValueError(f"fo 参数的值读不了 {len(bad)} 处：" + "；".join(bad[:12]) + ("…" if len(bad) > 12 else ""))
        widths = export.ink_widths(((f, "\n".join(keys)) for f, keys in (x["measure"] for x in laid.values())),
                                   known=self.widths)
        for x in laid.values():
            self.fit(x, widths, W)
        self.same_size(laid)
        out = []
        for i, ln in enumerate(ev_sec.lines):
            if i not in srcs:
                out.append(ln)
                continue
            src = srcs[i]
            gen = self.generate(laid[i], widths, W) if i in laid else [self.plain(src)]
            c = replace(src.row, fields=list(src.row.fields))
            c.kind = "Comment"
            c.set("Effect", EFFECT_SRC + src.row.get("Effect"))
            out.append(str(c))
            out += gen
            self.count("源行")
            self.count("生成行", len(gen))
        ev_sec.lines = out
        if st_sec is not None:
            derived = [derive_style(r, s) for s, r in base_style.items() if r is not None]
            last = max((k for k, ln in enumerate(st_sec.lines) if ln.startswith("Style:")), default=len(st_sec.lines) - 1)
            st_sec.lines[last + 1:last + 1] = derived
        if info is not None:
            lk = self.look
            val = f"plate={lk.plate} alpha=0x{lk.alpha:02X} typewriter={lk.typewriter} fade={lk.fade}" \
                  f"{' label' if lk.label else ''}{' colors' if lk.colors else ''}"
            last = max((k for k, ln in enumerate(info.lines) if ln.strip()), default=-1)
            info.lines.insert(last + 1, f"{INFO_KEY}: {val}")

    def check_params(self, srcs) -> None:
        known = FO_PARAMS | set(self.fx)
        bad = [(s.no, k) for s in srcs if s.fo for k in s.fo if k not in known]
        if bad and self.unknown != "ignore":
            where = "、".join(f"第 {no} 行 {k}" for no, k in bad[:12]) + ("…" if len(bad) > 12 else "")
            raise ValueError(f"不认识的 fo 参数 {len(bad)} 处：{where}。扩展效果用 --fx 加载；要跳过加 --opt unknown=ignore")
        for no, k in bad:
            self.notes.append((no, f"不认识的 fo 参数 {k!r}，跳过"))

    # -- 第 1 步：排版 --

    def lay_out(self, src: Src, font: str, W: int) -> dict:
        """解析一条带 `fo` 的源行，定下哪些参数有效（谁说了算）、要量哪些字宽。字号在 `fit` 里定。"""
        p = src.fo
        text = src.text
        rows_tok, plain, lead, full_ms = text_rows(text)
        was = parse_was(p.get("was"))
        pos, move, an, fad = (assfile.first_tag(text, k) for k in ("pos", "move", "an", "fad"))
        fs = assfile.first_tag(text, "fs", lead_only=True)      # 整行的字号；行内的 A{\fs80}B 只管后面几个字，不算

        def same(tag_args, recorded):                  # tag 的参数用逗号，fo 里记的用空格
            return tag_args is not None and assfile.numbers(tag_args) == assfile.numbers(recorded)
        moved = ("pos" in was and not same(pos, was["pos"])) or ("move" in was and not same(move, was["move"])) \
            or ("pos" in was and move is not None) or ("move" in was and pos is not None)
        realigned = "an" in was and not same(an, was["an"])
        if realigned:
            self.notes.append((src.no, f"对齐改过（\\an{an}）：以人为准，这一行按人给的锚点和位置画"))
        moved = moved or realigned
        resized = "fs" in was and not same(fs, was["fs"])
        box = rows_of(p["box"]) if p.get("box") else []
        kind = KIND_OF_STYLE.get(src.style, "line")
        if kind == "block" and not p.get("wrap"):
            kind = "line"
        if moved and not realigned:
            self.notes.append((src.no, "位置改过：以人为准，这一行不按原框逐行摆、不按轨迹走"))
        if resized:
            self.notes.append((src.no, f"字号改过（\\fs{fs}）：以人为准，不再适配"))
        mode = "free"
        cap = max(len(box), int(p["cap"]) if p.get("cap") not in (None, True) else 0)   # 原文框本来装几行（阶段 3 记的）
        base = tuple(box[0][:2]) if box else (0, 0)     # 轨迹 path 记的是原文框的位置：扩出行只改排版，不改这个基准
        if kind != "block" and p.get("rows") and box and not moved:
            n = len(plain)
            if n == len(box):
                mode = "rows"
            elif n > cap == len(box):                      # 多出来的行按原来一行的高度扩出去，逐行摆
                box, mode = layout.expand_rows(box, n - cap, self.H), "rows"
            elif n > cap and len(box) == 1:                # 一个框本来装着几行：框按一行的高度长出去
                box, mode = [layout.expand_box(box[0], cap, n - cap, self.H)], "single"
            elif len(box) == 1:
                mode = "single"
            else:
                self.notes.append((src.no, f"正文 {n} 行、原文框 {len(box)} 个（装 {cap} 行）对不上：不按原框逐行摆"))
            if n > cap and mode != "free":
                self.notes.append((src.no, f"正文 {n} 行、原文框装 {cap} 行：按原来一行的高度扩出 {n - cap} 行（压到别的字不管）"))
        m = re.fullmatch(r"r(\d+)", src.actor.strip())
        s0, e0 = cs_of(src.row.get("Start")) * 10_000, cs_of(src.row.get("End")) * 10_000
        fit = p.get("fit")
        tw = int(p["tw"]) if p.get("tw") not in (None, True) and full_ms is None else None
        x = {"src": src, "font": font, "rows_tok": rows_tok, "plain": plain, "lead": lead,
             "has_k": full_ms is not None or bool(tw), "tw": tw, "full_ms": full_ms if full_ms is not None else tw,
             "an": int(an) if an else 5, "pos": pos, "move": move, "fs": float(fs) if fs else None, "fad": fad,
             "box": box, "base": base, "cap": cap, "kind": kind, "mode": mode, "moved": moved, "resized": resized,
             "fit": (fit if fit in ("src", "tr") else "src") if fit and not resized else None,
             "translated": fit == "tr", "path": path_of(p["path"]) if p.get("path") and not moved else None,
             "region": int(m[1]) if m else -1, "start_us": s0, "end_us": e0}
        if kind == "block":
            paras = [re.sub(r"\\N", "", q) for q in re.split(r"\\N\\N", "\\N".join(plain))] if plain else []
            x["paras"], x["wrap"] = paras, int(p["wrap"])
            x["measure"] = (font, layout.width_keys(paras, True))
        else:
            lines = plain if mode != "single" else ["\n".join(plain)]
            x["lines"] = lines
            # 底板要按画出来的宽：行内 \fs 切开的几段各量各的（tokens_width）；字号适配按整行（fit_line）
            tok_rows = carry_rows(rows_tok) if mode == "rows" else [rows_joined(rows_tok)]
            runs = [s for r in tok_rows for ln in runs_of(r) for s, _ in ln]
            x["measure"] = (font, runs + layout.width_keys(lines, False))
        return x

    def fit(self, x: dict, widths: dict, W: int) -> None:
        if x["kind"] == "block":
            box = x["box"][0] if x["box"] else [0, 0, W, 0]
            keep = int(x["fs"]) if x["resized"] and x["fs"] is not None else None
            x["size"], x["wrapped"] = layout.block_layout(box, x["paras"], x["wrap"], x["font"], widths, size=keep)
            return
        if x["fit"] and x["box"] and x["mode"] != "free":
            x["size"] = layout.fit_line(x["box"], x["lines"], x["font"], widths, x["fit"] == "tr", W)
        elif x["fit"] and x["box"]:
            # 不按原框逐行摆（行数对不上 / 人挪过）：整块在几个框合起来的范围里适配，但正文行数比原文框装的少时
            # 行高按原来一行的高度算——两行并成一行不该把字放大到两行那么高（owner 2026-09-27 改字幕稿时撞到：\fs51 → \fs102）
            bx, n = x["box"], len(x["lines"])
            y0, y1 = bx[0][1], bx[-1][3]
            union = [min(r[0] for r in bx), y0, max(r[2] for r in bx), y0 + (y1 - y0) * n / max(n, x["cap"])]
            x["size"] = layout.fit_line([union], ["\n".join(x["lines"])], x["font"], widths, x["fit"] == "tr", W)
        else:
            x["size"] = int(x["fs"]) if x["fs"] is not None else 48

    def same_size(self, laid: dict) -> None:
        """同栏同时在屏的选项按钮字号取中位数（`layout.same_size`）；只算还在适配字号的那些。"""
        idx = [i for i, x in laid.items() if x["kind"] in ("choice", "offmain") and x["fit"] and x["box"]]
        got = layout.same_size([(laid[i]["start_us"], laid[i]["end_us"], laid[i]["box"][0]) for i in idx],
                               [laid[i]["size"] for i in idx])
        for i, s in zip(idx, got):
            laid[i]["size"] = s

    # -- 第 2–4 步：元素、运动、时间效果 --

    def generate(self, x: dict, widths: dict, W: int) -> list[str]:
        """第 2 步（元素）按种类摆好，其余（编号、扩展挂点、第 3 步运动、第 4 步时间效果）面板和普通条目走同一条路。"""
        src, look, p = x["src"], self.look, x["src"].fo
        back = "\\1c&H%s&\\1a&H%02X&" % (OVERLAY_COLOR, look.alpha)
        fad = assfile.numbers(x["fad"]) if x["fad"] and look.fade == "on" else []
        x["fad_ms"] = (int(fad[0]), int(fad[1])) if len(fad) == 2 else (0, 0)
        if any(x["fad_ms"]):
            self.count("fade")
        actor, style = src.actor, src.style
        s_us, e_us, size = x["start_us"], x["end_us"], x["size"]
        user_where = "\\move(%s)" % x["move"] if x["move"] else "\\pos(%s)" % x["pos"] if x["pos"] else ""
        segs, base = [(s_us, e_us, None, None)], (0, 0)
        els: list[Element] = []
        if x["kind"] == "block":
            box = [x["box"][0]] if x["box"] else []
            plates = layout.plate_rects(box, [], None, "box") if p.get("plate") and box else []
            els += [plate_el(pl, back) for pl in plates]
            body = "\\N".join(x["wrapped"])
            if box and not x["moved"]:
                els.append(Element(1, box[0][0], box[0][1], "\\an7", "\\fs%d" % size, body, "text", tail2=x["lead"], fade=True))
            else:                                              # 人挪过（\pos / \move）或改过锚点：照人给的画
                els.append(Element(1, 0, 0, "\\an%d" % x["an"] if x["moved"] else "\\an7", "\\fs%d" % size, body, "text",
                                   where=user_where, tail2=x["lead"], fade=True))
            align, lines = "left", x["paras"]
            x["mode_tw"] = "off"
        else:
            align = layout.ALIGN_OF.get(x["an"], {1: "left", 7: "left", 3: "right", 9: "right"}.get(x["an"], "center"))
            color = (UI_COLOR if look.colors and style == "UI" else OFFMAIN_COLOR if look.colors and p.get("off") else None)
            ctag = "\\1c&H%s&" % color if color else ""
            x["mode_tw"] = look.typewriter if x["has_k"] else "off"
            if x["has_k"]:
                self.count("typewriter")
            box, lines = x["box"], x["lines"]
            tok = x["rows_tok"] if x["mode"] == "rows" else [rows_joined(x["rows_tok"])]
            if x["tw"]:
                tok = with_tw(tok, x["tw"], x["mode"] != "rows")
            if x["mode"] == "rows":
                tok = carry_rows(tok)
            tw = [tokens_width(t, size, x["font"], widths) for t in tok]
            plate_mode = p.get("plate") if isinstance(p.get("plate"), str) else look.plate
            if x["mode"] in ("rows", "single"):
                axes = layout.axes_in_screen(box, tw, align, x["translated"], W)
                plates = layout.plate_rects(box, tw, axes, plate_mode, align) if p.get("plate") else []
                els += [plate_el(pl, back) for pl in plates]
                for r, a, t in zip(box, axes, tok):
                    els.append(Element(1, a, (r[1] + r[3]) / 2, "\\an%d" % layout.ANCHOR[align], "\\fs%d%s" % (size, ctag),
                                       body_of(t, x["mode_tw"], x["full_ms"]), "text", tail2=x["lead"], fade=True, tokens=t))
                if x["path"]:
                    segs = layout.motion_segments(x["path"], s_us, e_us)
                base = x["base"]
            else:
                plates = layout.plate_rects(box, [], None, "box") if p.get("plate") and box else []
                els += [plate_el(pl, back) for pl in plates]
                els.append(Element(1, 0, 0, "\\an%d" % x["an"], "\\fs%d%s" % (size, ctag),
                                   body_of(tok[0], x["mode_tw"], x["full_ms"]), "text",
                                   where=user_where, tail2=x["lead"], fade=True, tokens=tok[0]))
        line = Line(style, actor, p, s_us, e_us, x["region"], x["kind"], box, lines, size, align, plates, look)
        if look.label and x["region"] >= 0 and plates and x["kind"] != "block":
            px, py = max(q[2] for q in plates) - LABEL_INSET, min(q[1] for q in plates) + LABEL_INSET
            els.append(Element(2, px, py, "\\an9", "\\fs%d\\bord1\\1c&H%s&" % (max(LABEL_MIN, int(LABEL_SCALE * size)), LABEL_COLOR),
                               f"{x['region']}" + ("ui" if style == "UI" else ""), "label"))
        for name, mod in self.fx.items():
            if name in p and hasattr(mod, "elements"):
                els += list(mod.elements(line, p[name]))
        x["line"] = line
        self.count(x["kind"])
        return self.emit(x, els, segs, base)

    def emit(self, x: dict, els: list[Element], segs, base) -> list[str]:
        """第 3、4 步：所有元素一起按轨迹逐段展开，每段各一套（底板、正文、编号的先后照元素的顺序）；
        时间效果按**源行**的时间算——拆成几段时淡入淡出换成各段的 `\\fade`、逐字显出的时刻和人写的 `\\t` / `\\fade`
        都减去这一段的起点，不在每段从头放一遍。扩展效果的 `tags` 挂点在每一段的元素定稿之后调，改的东西各段都在。"""
        src, p = x["src"], x["src"].fo
        s_us, e_us = x["start_us"], x["end_us"]
        dur, (fin, fout) = (e_us - s_us) // 1000, x["fad_ms"]
        whole = len(segs) == 1
        out = []
        for seg in segs:
            a = (seg[0] - s_us) // 1000
            ftag = fade_tag(fin, fout, dur, a, whole)
            full = x["full_ms"] - a if x["full_ms"] is not None and x["full_ms"] - a > 0 else None
            for el in els:
                e = el if whole else replace(
                    el, tail2=shift_times(el.tail2, a, dur),
                    body=el.body if el.tokens is None else body_of(shift_tokens(el.tokens, a, dur), x["mode_tw"], full))
                e = replace(e)
                for name, mod in self.fx.items():
                    if name in p and hasattr(mod, "tags"):
                        mod.tags(x["line"], p[name], e)
                where = e.where if e.where is not None else layout.pos_tag(e.x, e.y, seg, base)
                text = "{%s%s%s%s%s}%s" % (e.head, where, e.tail, ftag if e.fade else "", e.tail2, e.body)
                out.append(dialogue(e.layer, seg[0], seg[1], src.style, src.actor, text))
        return out

    def plain(self, src: Src) -> str:
        """不带 `fo` 的源行：照主流 tag 原样生成一条，只换派生样式、标 `fo-fx`。"""
        r = replace(src.row, fields=list(src.row.fields))
        r.set("Style", src.style + FX_STYLE_SUFFIX)
        r.set("Effect", EFFECT_FX)
        self.count("plain")
        return str(r)


def look_of(preset: str, options: dict | None) -> tuple[Look, str]:
    o = dict(options or {})
    if preset not in LOOKS:
        raise ValueError(f"阶段 4 的预设只有 {list(LOOKS)}：{preset!r}")
    lk = LOOKS[preset]
    alpha = o.get("plate_alpha", lk.alpha)
    lk = replace(lk, plate=str(o.get("plate", lk.plate)), typewriter=str(o.get("typewriter", lk.typewriter)),
                 fade=str(o.get("fade", lk.fade)), alpha=int(alpha, 0) if isinstance(alpha, str) else int(alpha))
    if lk.plate not in layout.PLATE_MODES:
        raise ValueError(f"plate 只能取 {list(layout.PLATE_MODES)}：{lk.plate!r}")
    if lk.typewriter not in TYPEWRITER_MODES:
        raise ValueError(f"typewriter 只能取 {list(TYPEWRITER_MODES)}：{lk.typewriter!r}")
    if lk.fade not in ("off", "on"):
        raise ValueError(f"fade 只能是 off / on：{lk.fade!r}")
    unknown = str(o.get("unknown", "error"))
    if unknown not in ("error", "ignore"):
        raise ValueError(f"unknown 只能是 error / ignore：{unknown!r}")
    return lk, unknown


def load_fx(specs) -> tuple:
    mods = []
    for spec in specs or ():
        m = extensions.load(spec, "fx")
        if not isinstance(getattr(m, "NAME", None), str) or not (hasattr(m, "elements") or hasattr(m, "tags")):
            raise TypeError(f"扩展效果 `{spec}` 要有 NAME（它在 fo 里的参数名）和 elements / tags 至少一个挂点")
        mods.append(m)
    return tuple(mods)


def typeset_file(src: Path, out: Path | None = None, preset: str = "default", options: dict | None = None,
                 fx=(), quiet: bool = False, widths: dict | None = None) -> tuple[Path, Typesetter]:
    """读一份字幕稿（或阶段 4 自己的输出），写最终 ASS。默认输出：`x.script.ass` → `x.ass`，其余 `x.ass` → `x.fx.ass`。
    `widths`：已经量好的字宽（叠加预设连跑时阶段 3 交过来的）。"""
    src = Path(src)
    look, unknown = look_of(preset, options)
    doc = assfile.loads(src.read_text(encoding="utf-8"))
    ts = Typesetter(look, load_fx(fx), unknown, widths)
    ts.run(doc)
    out = Path(out) if out else default_out(src)
    out.write_text(doc.dumps(), encoding="utf-8")
    if not quiet:
        report(out, ts)
    return out, ts


def default_out(src: Path) -> Path:
    name = src.name
    if name.endswith(".script.ass"):
        return src.with_name(name[:-len(".script.ass")] + ".ass")
    return src.with_name(src.stem + ".fx.ass")


def restore_file(src: Path, out: Path | None = None, force: bool = False) -> tuple[Path, int]:
    """阶段 4 的输出 → 字幕稿（逐字节相同）。默认输出 `x.ass` / `x.fx.ass` → `x.script.ass`。
    输出的文件已经在时不覆盖（除非 `force`）：那份字幕稿可能是人改过的，拿一份旧的最终 ASS 还原会把改动冲掉。"""
    src = Path(src)
    if out is None:
        if src.name.endswith(".script.ass"):
            raise ValueError(f"{src.name} 已经是字幕稿；要还原的是阶段 4 的输出（x.ass），或用 -o 指定输出")
        stem = src.name[:-len(".fx.ass")] if src.name.endswith(".fx.ass") else src.stem
        out = src.with_name(stem + ".script.ass")
    out = Path(out)
    if out.exists() and not force:
        raise FileExistsError(f"{out} 已经在（可能是改过的字幕稿），不覆盖；确认要覆盖加 --force，或用 -o 另写一份")
    doc = assfile.loads(src.read_text(encoding="utf-8"))
    n = restore(doc)
    out.write_text(doc.dumps(), encoding="utf-8")
    return out, n


def report(out: Path, ts: Typesetter) -> None:
    c = ts.counts
    kinds = "、".join(f"{k} {c[k]}" for k in ("line", "name", "choice", "offmain", "block", "plain") if c.get(k))
    print(f"-> {out}（源行 {c.get('源行', 0)} 条 → 生成 {c.get('生成行', 0)} 条：{kinds}；"
          f"打字机 {c.get('typewriter', 0)} 条、淡入淡出 {c.get('fade', 0)} 条）")
    if ts.notes:
        print(f"  提示 {len(ts.notes)} 条：")
        for no, msg in ts.notes[:12]:
            print(f"    {'第 %d 行：' % no if no else ''}{msg}")
        if len(ts.notes) > 12:
            print(f"    …另 {len(ts.notes) - 12} 条")
