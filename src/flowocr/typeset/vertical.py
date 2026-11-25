"""阶段 4：竖排原文的两种摆法（字幕稿里带 `col` 的源行，vertical-text 计划）。

阶段 3 把竖排的列单独写成 `col` 行，相邻的列一块一行（`fo` 的 `col`：`box` 是从右往左的列，正文按列 `\\N` 分开，`script.draft_col`）。
这里按**译文内容**选摆法（`flow_of`）：去掉 tag 的正文里有中日韩字（`textdir.cjk_count`，判据只此一份）→ 竖排原位（`fit_columns` / `columns`），
没有 → 横排（`block`，漫画译成英文时的嵌字做法）；行上的 `flow=v|h`、阶段 4 的 `--opt flow=v|h` 覆盖。

这里只有几何和时刻（纯函数）：排好的字（`Glyph`，竖排一格一条、横排一行一条）、底板（`Plate`）、用哪套时刻（`col_timing`）和人挪过时的位移（`col_offset`）
交给 `core` 拼成生成行。

* **竖排原位**：字号按原文自己的字距定（`layout.col_size`：原文照原样一定放得回原列；墨迹宽不超过原列宽 × `layout.COL_INK`），
  字距 = `layout.COL_PITCH` 个全角字步进宽（`layout.col_pitch`）。
  译文 `\\N` 段数等于列数就按人分的列，否则从右往左自动分（`split`：在标点后、避头尾处断）；写不下先让各列往下长到画面底，
  再往左加列（列距沿用原文列距，一列时 `COL_GAP` 个列宽），最后才整体缩字号（不低于 `layout.OVERLAY_MIN_SCALE`）。
  每个字一条生成行（不用 `@字体` + `\\frz`：那依赖渲染器和字体的竖排特性）；标点换竖排字形（`VERT_FORMS`），
  夹在里面的半角字母数字 1–2 个横着挤进一格（縦中横），3 个以上整段侧躺（`\\frz-90`）。
* **横排**：区域是各列的并集，拉丁字母的大写高 ≈ 原文字号 × `CAP_REL`，同时在屏、原文字号相近（`textdir.COL_W_RATIO` 倍以内）的几条取同一字号（`size_groups`）；
  块宽取放得进并集高度的最窄宽度（`narrowest`，不窄于最长单词和 `BLOCK_MIN_CAP` 个大写高，有上限），均衡折行（`balance`，只在空格处断）、居中，块中心对准并集中心，出画面往里推。
  拉丁字体默认跟着样式，阶段 4 的 `latin_font` 可另选（字行带 `\\fn`，量字宽、行高、竖直偏移都按它，表里没有的现量）。
  打字机下拉丁字母按整词出（`by_word`：词在首字母的时刻整个显出），中日韩字仍逐字。
* **底板**（`plates`）：竖排每列一块 = 原列 ∪ 译文列（实现上坐标镜像转置 `tr`，列的从右往左变成行的从上往下，走 `layout` 现成的逐行底板），
  横排的 `rows` 是外扩过的原列加逐行外扩过的译文拼成的不重叠几块（`block_rows`），`box` 一整块 = 并集 ∪ 译文块；
  `text` 只盖译文，`src` 只盖原列（外扩边距、不劈开，阶段 5 的擦除区域就是它）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from flowocr import richtext
from flowocr.artifacts import textdir
from flowocr.output import export, layout
from flowocr.output.script import NO_END, NO_START
from flowocr.typeset import assfile

FLOWS = ("auto", "v", "h")
"""摆法：`auto` 按译文有没有中日韩字选，`v` 竖排原位，`h` 横排。"""

VERT_FORMS = dict(zip("，。、：；…—（）「」『』“”《》【】～ー", "︐︒︑︓︔︙︱︵︶﹁﹂﹃﹄﹁﹂︽︾︻︼≀丨"))
"""竖排时换成的字形（Unicode 的竖排标点）。`！？` 原样立着。"""
NO_GLYPH = frozenset("≀")
"""默认字体（微软雅黑）里没有的竖排字形——libass 缺字形会静默换替身字体、量宽作废，所以不用它们（守卫按字体的 cmap 逐字核这张表）。
缺的退回原字形：逗号句号（`SHIFT_PUNCT`）挪到格子右上，别的（括号引号、波浪线）转 90°。"""
SHIFT_PUNCT = frozenset("，。、")
SHIFT_EM = 0.6
"""缺竖排字形的逗号句号往右上挪这么多个字号：横排字形在格子左下，竖排的写法在右上。"""
TCY_MAX = 2
"""连续这么多个以内的半角字母数字横着挤进一格（縦中横，`2セット目` 的 `2`），更多的整段侧躺。"""
COL_GAP = 1.3
"""只有一列原文时，往左加的列离它这么多个列宽（几列时沿用原文的列距）。"""
BREAK_AFTER = frozenset("，。、！？；：…」』）】,.!?;: 　")
"""自动分列时优先断在这些字后面（列的后半段里有的话）。"""

CAP_REL = 0.5
"""横排：拉丁字母的大写高 ≈ 原文字号（`layout.col_size`）× 这么多。0.7 时英文比原文的日文还大一号（原文 144 号 → 拉丁 172 号，
片段 1 的 demo），漫画英译的字比原来的日文字小；0.5 时约是原文字号的 0.85 倍。"""
CAP_KEY = "H"
"""量拉丁大写字母高的字（`export.ink_tall`）。"""
CAP_DEFAULT = 0.7
"""没量到大写高时，大写高 / 字号按这个算。"""
BLOCK_MAX_W = 0.45
"""横排块宽不宽于画面宽 × 这么多（也不宽于画面宽减两边边距）。"""
LINE_PITCH = 1.2
"""横排行距 = 字号 × 这么多。"""
BLOCK_MIN_CAP = 7
"""横排块宽不窄于大写字母高 × 这么多（不宽于上限）：只按"放得进原列高度的最窄宽度"取时，一整根长列会一行一个词竖着叠下去（片段 8 的
「On / the / 2nd / floor, / …」），读起来像竖排诗；按大写高算，窄字体同样的下限装得下更多字母。"""
SPACE_KEYS = ("xx", "x" + " " * 10 + "x")
"""量空格宽的两串（同 `layout.latin_keys` 的做法）。"""


def flow_of(plain: str, forced: str | None = None) -> str:
    """`col` 行用哪种摆法：`forced`（行上的 `flow`、阶段 4 的 `--opt flow`）是 `v` / `h` 就用它；否则正文（去掉 tag、占位已换）里有中日韩字就竖排（`v`），
    没有就横排（`h`）。全角拉丁字母（ＳＮＳ）不算中日韩字。"""
    if forced in ("v", "h"):
        return forced
    return "v" if textdir.cjk_count(plain) > 0 else "h"


# ---- 竖排原位 ----

@dataclass
class Cell:
    """竖排的一格：`kind` 是 `ch`（一个字）/ `tcy`（縦中横）/ `side`（侧躺）/ `gap`（空格，不画）。
    `text` 是原来的字（换竖排字形之前，避头尾、量宽按它）；`tokens` 是画这一格的记号，行首带上前面生效的行内 tag（同 `core.carry_rows`）；
    `tail` 是这一格另加的 tag（`\\frz-90`、縦中横的 `\\fscx`）；`shift`：缺竖排字形的逗号句号，原字形挪到格子右上。"""
    kind: str
    text: str
    tokens: list
    tail: str = ""
    shift: bool = False


def half(ch: str) -> bool:
    """半角字母数字和半角符号（可打印 ASCII，不含空格）。"""
    return len(ch) == 1 and "!" <= ch <= "~"


def is_space(ch: str) -> bool:
    return ch in (" ", "\\h")


def cells_of(tokens: list[tuple], font: str, widths: dict) -> list[Cell]:
    """一串正文记号（`core.text_rows` 的 ("tags", …) / ("ch", 字, 时刻) / ("nl",)，Ruby 已去掉）→ 竖排的格。
    行里的软换行 `\\n` 不算；连续的半角字（中间可夹空格）1–`TCY_MAX` 个是縦中横，更多是侧躺；单独的空格是半格的 `gap`，全角空格一格。"""
    toks = [t for t in tokens if t[0] != "nl"]
    out: list[Cell] = []
    carried = ""
    unit = layout.col_ink_unit(font, widths)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "tags":
            carried += t[1]
            i += 1
            continue
        lead = [("tags", carried)] if carried else []
        ch = t[1]
        if half(ch):
            j, end = i, i
            while j < len(toks) and (toks[j][0] == "tags" or half(toks[j][1]) or is_space(toks[j][1])):
                if toks[j][0] == "ch" and half(toks[j][1]):
                    end = j + 1
                j += 1
            run = toks[i:end]
            text = "".join(" " if is_space(x[1]) else x[1] for x in run if x[0] == "ch")
            for x in run:
                if x[0] == "tags":
                    carried += x[1]
            if len(text) <= TCY_MAX:
                w = widths.get((font, text), 0.0)
                tail = "\\fscx%d" % round(100 * unit / w) if w > unit else ""
                out.append(Cell("tcy", text, lead + run, tail))
            else:
                out.append(Cell("side", text, lead + run, "\\frz-90"))
            i = end
            continue
        if is_space(ch) or ch == "　":
            out.append(Cell("gap", " " if is_space(ch) else ch, []))
            i += 1
            continue
        v = VERT_FORMS.get(ch)
        if v and v not in NO_GLYPH:
            out.append(Cell("ch", ch, lead + [("ch", v, t[2])]))
        elif v:
            out.append(Cell("ch", ch, lead + [t], "" if ch in SHIFT_PUNCT else "\\frz-90", ch in SHIFT_PUNCT))
        else:
            out.append(Cell("ch", ch, lead + [t]))
        i += 1
    return out


def run_keys(tokens: list[tuple]) -> list[str]:
    """要量墨迹宽的串：縦中横和侧躺的那几段（`cells_of` 按它们定宽 / 长度）。"""
    return [c.text for c in cells_of(tokens, "", {}) if c.kind in ("tcy", "side")]


def cell_len(c: Cell, size: float, font: str, widths: dict) -> float:
    """一格顺着列的长度（像素）：一个字、縦中横是一个字距；侧躺是这段的墨迹宽加上字和字之间的空；空格半个（全角空格一个）字距。"""
    pitch = layout.col_pitch(size, font, widths)
    if c.kind == "gap":
        return pitch * (1.0 if c.text == "　" else 0.5)
    if c.kind == "side":
        ink = layout.col_ink_unit(font, widths) * size
        return widths.get((font, c.text), 0.5 * len(c.text)) * size + max(0.0, pitch - ink)
    return pitch


def break_at(cells: list[Cell], i: int, j: int) -> int:
    """`cells[i:j]` 放得下、`cells[j]` 放不下时这一列在哪断：不让 `NO_START` 的字打头、`NO_END` 的字收尾（避头尾）；
    后半段里有标点（`BREAK_AFTER`）就断在最后一个标点后面。找不到合规的断点就硬断在 `j`。"""
    ok = [b for b in range(j, i, -1) if cells[b].text[:1] not in NO_START and cells[b - 1].text[-1:] not in NO_END]
    if not ok:
        return j
    after = [b for b in ok if cells[b - 1].text[-1:] in BREAK_AFTER and b - i >= (j - i) / 2]
    return after[0] if after else ok[0]


def split(cells: list[Cell], lens: list[float], caps: list[float]) -> list[list[Cell]] | None:
    """从右往左把格填进长 `caps` 的几列，一列满了按 `break_at` 断；放不完是 None。"""
    out: list[list[Cell]] = []
    i = 0
    for cap in caps:
        if i >= len(cells):
            break
        j, acc = i, 0.0
        while j < len(cells) and acc + lens[j] <= cap + 0.5:
            acc += lens[j]
            j += 1
        if j < len(cells):
            if j == i:
                return None
            j = break_at(cells, i, j)
        out.append(cells[i:j])
        i = j
    return out if i >= len(cells) else None


@dataclass
class Slot:
    """一列译文的位置：中心 `x`、首字顶 `top`、最长 `cap`、列宽 `w`。"""
    x: float
    top: float
    cap: float
    w: float


def slots(src: list[list[float]], H: int, extend: bool = False, extra: int = 0) -> list[Slot] | None:
    """译文的列：前几列就是原文的列（`extend` 时往下长到画面底留边距），`extra` 是往左加的列（列距沿用原文的列距中位数，一列时 `COL_GAP` 个列宽，
    首字顶、列宽照最左那列原文）；加到画面左边外去了是 None。"""
    cx = [(b[0] + b[2]) / 2 for b in src]
    out = [Slot(c, b[1], max(b[3] - b[1], H - layout.EDGE_PAD - b[1]) if extend else b[3] - b[1], b[2] - b[0]) for c, b in zip(cx, src)]
    if extra:
        gaps = sorted(a - b for a, b in zip(cx, cx[1:]))
        last = out[-1]
        sp = gaps[(len(gaps) - 1) // 2] if gaps and gaps[(len(gaps) - 1) // 2] > 0 else COL_GAP * last.w
        for m in range(1, extra + 1):
            x = last.x - sp * m
            if x - last.w / 2 < layout.EDGE_PAD:
                return None
            out.append(Slot(x, last.top, last.cap, last.w))
    return out


def min_size(size0: int) -> int:
    return max(export.FIT_MIN_SIZE, math.ceil(layout.OVERLAY_MIN_SCALE * size0))


def fit_columns(cells: list[Cell], given: list[list[Cell]] | None, src: list[list[float]], size0: int, fixed: bool,
                H: int, font: str, widths: dict) -> tuple[int, list[list[Cell]], list[Slot], bool]:
    """竖排原位的字号和分列：返回（字号, 各列的格, 各列的位置, 放没放下）。
    `given` 是人分好的列（译文 `\\N` 段数等于原文列数），只让各列往下长、不够再缩字号；否则从右往左自动分（`split`）：
    依次试原文列长、各列往下长到画面底、往左一列列加，都不行就小一号重来，到 `min_size` 为止（`fixed`：人改过字号，不缩）。
    最后还放不下：最小字号、能加的列都加上，剩下的全放进最后一列（会出画面，打提示）。"""
    sizes = [size0] if fixed else list(range(size0, min_size(size0) - 1, -1))
    for s in sizes:
        lens = [cell_len(c, s, font, widths) for c in cells]
        if given is not None:
            sl = slots(src, H, True)
            if all(sum(cell_len(c, s, font, widths) for c in g) <= x.cap + 0.5 for g, x in zip(given, sl)):
                return s, given, sl, True
            continue
        extra = 0
        for sl in [slots(src, H), slots(src, H, True)]:
            got = split(cells, lens, [x.cap for x in sl])
            if got is not None:
                return s, got, sl[:len(got)], True
        while (sl := slots(src, H, True, extra + 1)) is not None:
            extra += 1
            got = split(cells, lens, [x.cap for x in sl])
            if got is not None:
                return s, got, sl[:len(got)], True
    s = sizes[-1]
    if given is not None:
        return s, given, slots(src, H, True), False
    extra = 0
    while slots(src, H, True, extra + 1) is not None:
        extra += 1
    sl = slots(src, H, True, extra)
    got = split(cells, [cell_len(c, s, font, widths) for c in cells], [x.cap for x in sl[:-1]] + [math.inf]) or [cells]
    return s, got, sl[:len(got)], False


def timed(cols: list[list[Cell]], spans: list[tuple[int, int]] | None, tw: int | None, typing: bool) -> list[list[Cell]]:
    """给各列的字定出现时刻（毫秒，相对源行起点）：`spans` 是逐列的 (首字, 全字)（阶段 3 的 `rowt`，列数对得上时）——
    列内在自己的区间里匀速逐字，不打字（全字 = 首字）或关了打字机（`typing=False`）整列在首字时出现；
    没有 `spans` 有 `tw` 时所有列的字按阅读顺序在 `[0, tw]` 里匀速；都没有就不改（人写的 `\\k` 节奏、或不打字）。"""
    def stamp(col: list[Cell], times) -> list[Cell]:
        out = []
        for c in col:
            toks = []
            for t in c.tokens:
                toks.append(("ch", t[1], next(times)) if t[0] == "ch" else t)
            out.append(replace(c, tokens=toks))
        return out

    def count(col):
        return sum(t[0] == "ch" for c in col for t in c.tokens)
    if spans is not None:
        got = []
        for col, (f, full) in zip(cols, spans):
            n = count(col)
            last = max(1, n - 1)
            got.append(stamp(col, iter([f + (full - f) * k // last if typing and full > f else f for k in range(n)])))
        return got
    if tw:
        n = sum(count(col) for col in cols)
        last = max(1, n - 1)
        it = iter([tw * k // last for k in range(n)])
        return [stamp(col, it) for col in cols]
    return cols


@dataclass
class Glyph:
    """一条要画的字：`\\an5` 的锚点 (`x`, `y`)（已按 `export.ink_dy` 往上挪），记号、字号、另加的 tag；
    `first` / `full` 是 `dim` 用的这一列（这一块）首字 / 全字时刻；`unit` 是它属于第几列译文（横排都是 0）。"""
    x: float
    y: float
    tokens: list
    size: int
    tail: str
    first: int | None
    full: int | None
    unit: int


def columns(cols: list[list[Cell]], sl: list[Slot], size: int, font: str, widths: dict,
            spans: list[tuple[int, int]] | None, full_ms: int | None) -> tuple[list[Glyph], list[list[float]]]:
    """排好的列 → 每格一条字，和每列译文占的矩形（列中心 ± 半个墨迹宽，首字顶到末字底；底板按它算）。列首的空格不画、不占长度。"""
    dy = export.ink_dy(font) * size
    ink = layout.col_ink_unit(font, widths) * size
    glyphs, rects = [], []
    for j, (col, slot) in enumerate(zip(cols, sl)):
        while col and col[0].kind == "gap":
            col = col[1:]
        y = slot.top
        first, full = spans[j] if spans is not None else (None, full_ms)
        for c in col:
            ln = cell_len(c, size, font, widths)
            if c.kind != "gap":
                gx, gy = slot.x, y + ln / 2 - dy
                if c.shift:
                    gx, gy = gx + SHIFT_EM * size, gy - SHIFT_EM * size
                glyphs.append(Glyph(gx, gy, c.tokens, size, c.tail, first, full, j))
            y += ln
        rects.append([slot.x - ink / 2, slot.top, slot.x + ink / 2, max(y, slot.top + 1)])
    return glyphs, rects


# ---- 横排 ----

def words_of(rows: list[list[tuple]]) -> list[list[tuple]]:
    """几列的记号 → 一个个词（空格、列分隔 `\\N`、软换行都是词间的空）。词前的行内 tag 归这个词，末尾多出来的 tag 归最后一个词。"""
    out: list[list[tuple]] = []
    cur: list[tuple] = []
    pend: list[tuple] = []
    for r in rows:
        for t in [*r, ("nl",)]:
            if t[0] == "tags":
                (cur if cur else pend).append(t)
            elif t[0] == "nl" or is_space(t[1]):
                if cur:
                    out.append(cur)
                    cur = []
            else:
                cur += pend + [t]
                pend = []
    if cur:
        out.append(cur)
    if pend and out:
        out[-1] += pend
    return out


def word_text(w: list[tuple]) -> str:
    return "".join(t[1] for t in w if t[0] == "ch")


def balance(ws: list[float], sp: float, width: float) -> list[tuple[int, int]]:
    """均衡折行：宽 `ws` 的词、词间空 `sp`，折成每行不宽于 `width`、各行剩余宽度的平方和最小的几行（动态规划；末行也算，
    所以不会剩一个孤词）。比 `width` 还宽的词独占一行（不计代价）。返回各行的 (起, 止) 词下标。"""
    n = len(ws)
    best = [0.0] + [math.inf] * n
    prev = [0] * (n + 1)
    for j in range(1, n + 1):
        w = -sp
        for i in range(j - 1, -1, -1):
            w += ws[i] + sp
            if w > width and i < j - 1:
                break
            cost = best[i] + (max(0.0, width - w) ** 2 if w <= width else 0.0)
            if cost < best[j]:
                best[j], prev[j] = cost, i
    out, j = [], n
    while j > 0:
        out.append((prev[j], j))
        j = prev[j]
    return out[::-1]


def space_unit(font: str, widths: dict) -> float:
    """字号 1 时一个空格的宽（`SPACE_KEYS` 两串之差 / 10）；没量过按 0.3。"""
    a, b = (widths.get((font, k), 0.0) for k in SPACE_KEYS)
    return (b - a) / 10 if b > a else 0.3


def latin_size(size0: int, font: str, widths: dict) -> int:
    """横排的字号：大写字母高 ≈ 原文字号 × `CAP_REL`（大写高 / 字号用 `CAP_KEY` 量的墨迹高，`font` 是横排用的拉丁字体，哪个字体都现量）。"""
    cap = export.ink_tall(font, CAP_KEY, widths) or CAP_DEFAULT
    return max(export.FIT_MIN_SIZE, int(CAP_REL * size0 / cap))


def block_max_w(W: int) -> float:
    """横排块的宽度上限：min(画面宽 × `BLOCK_MAX_W`, 画面宽 − 两边边距)。"""
    return min(W * BLOCK_MAX_W, W - 2 * layout.EDGE_PAD)


def word_px(t: str, font: str, widths: dict) -> float:
    """字号 1 时一个词的步进宽（墨迹 + 两侧留白；留白可以是负的，墨迹伸出步进格）；没量过按每字 0.5。"""
    return widths.get((font, t), 0.5 * len(t)) + sum(export.ink_side(font, t, widths))


def word_fit(t: str, font: str, widths: dict) -> float:
    """字号 1 时一个词占的宽：墨迹 + 两侧为正的留白，不小于步进宽也不小于墨迹宽。折行、块宽和上限都按它量：
    一行按步进排、实际的墨迹宽（`block` 的各行宽）不会超过这些词的它加上空格（留白为负时墨迹宽比步进宽大，只按步进量会冲出上限）。"""
    return widths.get((font, t), 0.5 * len(t)) + sum(max(0.0, v) for v in export.ink_side(font, t, widths))


def word_cap(words: list[list[tuple]], font: str, widths: dict, W: int) -> float:
    """横排块的字号上限：最长单词（`word_fit`）放得进块宽上限（`block_max_w`）的最大字号；没有词是 inf。"""
    longest = max((word_fit(word_text(w), font, widths) for w in words), default=0.0)
    return block_max_w(W) / longest if longest > 0 else math.inf


def shared_size(sizes: list[int], caps: list[float]) -> tuple[int, list[bool]]:
    """同时在屏、取同一字号的一组横排块（`size_groups` 分的，单独一块也是一组）的字号：各块按原文定的拉丁字号取中位数，
    再压到每块最长单词的上限（`word_cap`）以内——不能先定组字号、再让放不下长单词的那块自己缩，那样组内又不一样大；不低于中位数的 `min_size`。
    第二项逐块：最长单词在这个字号下放得进块宽上限没有（到下限还放不下的那块比上限宽，调用方打提示）。"""
    m = sorted(sizes)[(len(sizes) - 1) // 2]
    cap = min(caps)
    size = m if cap >= m else max(min_size(m), int(cap))
    return size, [size <= c for c in caps]


def narrowest(ws: list[float], sp: float, pitch: float, gh: float, uh: float, cap_w: float) -> float:
    """横排块的宽：均衡折行（`balance`）折出的 n 行放得进高 `uh`（(n − 1) × 行距 `pitch` + 一行字高 `gh` ≤ `uh`）的最窄宽度，不窄于最长的词；
    到上限 `cap_w` 还放不下就取上限（最长的词比上限还宽时取那个词宽）。行数只在"连续几个词连成一行的宽"上变，所以候选就是这些宽：
    先按贪心折（行数随宽度单调不增）二分到最窄的候选，均衡折行偶尔多折一行时再往宽里试。"""
    if not ws:
        return 0.0
    longest = max(ws)
    if longest >= cap_w:
        return longest

    def fits(n: int) -> bool:
        return (n - 1) * pitch + gh <= uh

    def greedy(w: float) -> int:
        n, cur = 1, None
        for x in ws:
            if cur is None:
                cur = x
            elif cur + sp + x <= w:
                cur += sp + x
            else:
                n, cur = n + 1, x
        return n
    acc = [0.0]
    for x in ws:
        acc.append(acc[-1] + x)
    eps = 1e-6                     # `balance` 逐词累加行宽，和这里的前缀和差在末位
    cands = sorted({acc[j] - acc[i] + sp * (j - i - 1) + eps for i in range(len(ws)) for j in range(i + 1, len(ws) + 1)})
    cands = [c for c in cands if longest <= c <= cap_w]
    lo, hi = 0, len(cands)
    while lo < hi:
        mid = (lo + hi) // 2
        if fits(greedy(cands[mid])):
            hi = mid
        else:
            lo = mid + 1
    for c in cands[lo:]:
        if fits(len(balance(ws, sp, c))):
            return c
    return cap_w


def block(words: list[list[tuple]], src: list[list[float]], size: int, font: str, widths: dict, W: int, H: int
          ) -> tuple[list[list[list[tuple]]], list[list[float]], list[float], list[list[float]]]:
    """横排块：返回（各行的词, 各行的中心 [x, y], 块的矩形, 各行的矩形）。字号已定（`shared_size`），这里不再缩；`font` 是横排用的拉丁字体。
    区域 U = 各列的并集。块宽取放得进 U 高的最窄宽度（`narrowest`，不窄于最长单词和 `BLOCK_MIN_CAP` 个大写高），按 `balance` 折；到上限（`block_max_w`）还放不下就按上限折、
    行数照加（块往上下长）；最长单词比上限还宽时块就是那么宽。块中心对准 U 的中心，出画面就往里推。
    宽度按词的墨迹宽和空格宽估（不含词首尾的留白，差几像素由底板边距盖住）。
    各行的矩形：横向是这一行的墨迹宽、居中；竖向相邻两行在行距的中线分开，首行的顶、末行的底是字高（逐行盖译文的底板按它，`plates`）。"""
    u = layout.union(src)
    uh = max(1.0, u[3] - u[1])
    texts = [word_text(w) for w in words]
    # 词宽按"墨迹 + 两侧留白"算（`word_fit`）：只按墨迹加空格宽时，一行的实际墨迹宽被低估约一个字号的 4%（片段 1 的 demo 冲出画面右沿）；
    # 留白为负的不减（墨迹伸出步进格，按步进量会冲出上限）。首末两个词外侧的留白多算了，折行偏保守；各行的实际宽另按步进减掉
    sides = [export.ink_side(font, t, widths) for t in texts]
    ws = [word_fit(t, font, widths) * size for t in texts]          # 折行按它（不小于步进宽和墨迹宽）
    adv = [word_px(t, font, widths) * size for t in texts]          # 各行实际画出来的宽按步进算
    sp = space_unit(font, widths) * size
    pitch = LINE_PITCH * size
    gh = size / export.h_ratio(font, widths)
    w = max(narrowest(ws, sp, pitch, gh, uh, block_max_w(W)), min(block_max_w(W), BLOCK_MIN_CAP * (export.ink_tall(font, CAP_KEY, widths) or CAP_DEFAULT) * size))
    lines = balance(ws, sp, w) if ws else []
    lw = [sum(adv[a:b]) + sp * (b - a - 1) - (sides[a][0] + sides[b - 1][1]) * size for a, b in lines]
    bw = max(lw, default=0.0)
    bh = (len(lines) - 1) * pitch + gh if lines else 0.0
    cx, cy = (u[0] + u[2]) / 2, (u[1] + u[3]) / 2
    cx += max(0.0, layout.EDGE_PAD - (cx - bw / 2)) - max(0.0, (cx + bw / 2) - (W - layout.EDGE_PAD))
    cy += max(0.0, layout.EDGE_PAD - (cy - bh / 2)) - max(0.0, (cy + bh / 2) - (H - layout.EDGE_PAD))
    rect = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]
    centers = [[cx, rect[1] + gh / 2 + i * pitch] for i in range(len(lines))]
    last = len(lines) - 1
    rows = [[cx - x / 2, rect[1] if i == 0 else c[1] - pitch / 2, cx + x / 2, rect[3] if i == last else c[1] + pitch / 2]
            for i, (x, c) in enumerate(zip(lw, centers))]
    return [words[a:b] for a, b in lines], centers, rect, rows


def line_tokens(lines: list[list[list[tuple]]]) -> list[list[tuple]]:
    """各行的词 → 各行的记号：词间一个空格，前面几行的行内 tag 带到下一行开头（同 `core.carry_rows`）。"""
    out, carried = [], ""
    for ws in lines:
        toks: list[tuple] = [("tags", carried)] if carried else []
        for k, w in enumerate(ws):
            if k:
                toks.append(("ch", " ", None))
            toks += w
        carried += "".join(t[1] for t in toks[1 if carried else 0:] if t[0] == "tags" and not richtext.RUBY_BLOCK.fullmatch(t[1]))
        out.append(toks)
    return out


def timed_flat(rows: list[list[tuple]], first: int, full: int, typing: bool) -> list[list[tuple]]:
    """几行的字在 `[first, full]` 里按阅读顺序匀速出现（`typing=False` 或不打字时都在 `first`）：横排块整块从最早一列的首字时刻起。"""
    n = sum(t[0] == "ch" for r in rows for t in r)
    last, k, out = max(1, n - 1), 0, []
    for r in rows:
        row = []
        for t in r:
            if t[0] == "ch":
                row.append(("ch", t[1], first + (full - first) * k // last if typing and full > first else first))
                k += 1
            else:
                row.append(t)
        out.append(row)
    return out


def by_word(rows: list[list[tuple]]) -> list[list[tuple]]:
    """横排块跟着打字机按整词出：一个拉丁词（一行里连续的非空格、非中日韩字，`textdir.cjk_count` 判；夹在中间的行内 tag 不断词）
    的字都取它首字的时刻，逐字母出会停在半个词上（"peel jus"）；中日韩字和空格照旧逐字。没有时刻的记号（`None`）不动。"""
    out = []
    for r in rows:
        row, t0 = [], None
        for t in r:
            if t[0] != "ch":
                row.append(t)
            elif is_space(t[1]) or textdir.cjk_count(t[1]):
                row.append(t)
                t0 = None
            else:
                t0 = t[2] if t0 is None else t0
                row.append(("ch", t[1], t0))
        out.append(row)
    return out


def size_groups(spans: list[tuple[int, int, float]]) -> list[list[int]]:
    """同时在屏的几块横排取同一字号时怎么分组：`spans[i]` = (起, 止, 原文字号)。先按时间重叠连成片（重叠的传递闭包：
    一块常驻的横排块会把整段连成一片，那一段里字号相近的块都取同一个中位数），片内按原文字号排好，在相邻两块比值最大处切开，
    直到每组的最大 / 最小不超过 `textdir.COL_W_RATIO`（默认与阶段 3 拆列的列宽比门同值；阶段 4 读不到 tracks 的 provenance）。
    组内一定同一字号，差得远的两块不会被中间那块串成一组，几乎一样大的两块也不会被切开（30 / 50 / 52 → {30}、{50, 52}）。返回各组的下标。"""
    n = len(spans)
    root = list(range(n))

    def find(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if spans[i][0] < spans[j][1] and spans[j][0] < spans[i][1]:
                root[find(j)] = find(i)

    def cut(ks: list[int]) -> list[list[int]]:          # ks 按原文字号排好
        if spans[ks[-1]][2] <= textdir.COL_W_RATIO * spans[ks[0]][2]:
            return [ks]
        at = max(range(1, len(ks)), key=lambda m: spans[ks[m]][2] / max(1e-9, spans[ks[m - 1]][2]))
        return cut(ks[:at]) + cut(ks[at:])
    out: list[list[int]] = []
    for c in {find(i) for i in range(n)}:
        out += cut(sorted((i for i in range(n) if find(i) == c), key=lambda i: spans[i][2]))
    return sorted(sorted(g) for g in out)


# ---- 时刻 ----

@dataclass
class Timing:
    """`col` 行的时刻（`col_timing` 定，毫秒、相对源行起点）。
    `spans`：逐个译文单位（竖排一列一段、横排整块一段）的 (首字, 全字)，`rowt` 对得上时才有；
    `tw`：没有 `spans` 时所有字在 `[0, tw]` 里匀速（`timed` / `core.with_tw`）；两者都没有就不改记号的时刻（人写的 `\\k`、或不打字）。
    `full` / `mode` 是 `core.body_of` 要的全字时刻和打字机方式；`src` 是原文各列的首字时刻（底板起点，`starts`）；`notes` 是要打的提示。"""
    spans: list[tuple[int, int]] | None
    tw: int | None
    full: int | None
    mode: str
    src: list[int]
    notes: list[str] = field(default_factory=list)

    def starts(self, glyphs: list[Glyph], n: int) -> list[int]:
        """底板按 `Plate.cover` 取起点用的时刻表：译文 0..n−1 是各列（块）首字出现的时刻——有 `spans` 取它的首字，
        逐字显出（`mode == "on"`）取这一列最早的字，否则 0；接着是原文各列（`src`）。"""
        def seen(j: int) -> int:
            if self.spans is not None:
                return self.spans[j][0]
            ts = [t[2] for g in glyphs if g.unit == j for t in g.tokens if t[0] == "ch" and t[2] is not None] if self.mode == "on" else []
            return min(ts, default=0)
        return [seen(j) for j in range(n)] + self.src


def col_timing(rowt: list[tuple[int, int]] | None, tw: int | None, k_full: int | None, n_src: int, n_cols: int | None,
               typewriter: str, by_hand: bool) -> Timing:
    """`col` 行用哪套时刻。`rowt` 一段对一列原文（段数不等于原文列数 `n_src` 就不用、打提示）；
    `tw` 是整条的打字机时长；`k_full` 是人写的 `\\k` 节奏的全字时刻（没写是 None；写了时译文照人写的，`tw` 已作废，
    `rowt` 只用来定原文各列的首字时刻 `src`——底板和 `src` 擦除不提前盖还没出现的列）。
    * 竖排（`n_cols` 是译文列数）：列数等于原文列数时每列在自己的 `rowt` 段里显出；对不上退回整条的 `tw`——
      译文是人用 `\\N` 分过列的（`by_hand`）才打提示，自动分列分出的列数不同（一段译文、空译文）是常态，不打；
    * 横排（`n_cols` 是 None）：整块一段，从最早一列的首字到最晚一列的全字。
    打字机方式：有 `spans` 时关了打字机也按首字起显（`on`，同 `core.with_rowt`），`dim` 照用；否则有 `\\k` 或 `tw` 才按预设，都没有是 `off`。"""
    notes = []
    if rowt is not None and len(rowt) != n_src:
        notes.append(f"rowt 有 {len(rowt)} 段、原文 {n_src} 列，对不上：" + ("原文各列的底板从行首起" if k_full is not None else "按整条的打字机节奏显字"))
        rowt = None
    if k_full is not None:
        spans = None
    elif n_cols is None:
        spans = [(min(a for a, _ in rowt), max(b for _, b in rowt))] if rowt else None
    else:
        spans = rowt if rowt is not None and n_cols == n_src else None
        if rowt is not None and spans is None and by_hand:
            notes.append(f"rowt 有 {n_src} 段、译文 {n_cols} 列，对不上：按整条的打字机节奏显字")
    full = k_full if k_full is not None else max(b for _, b in spans) if spans else tw
    mode = ("dim" if typewriter == "dim" else "on") if spans else typewriter if k_full is not None or tw else "off"
    return Timing(spans, None if spans else tw, full, mode, [a for a, _ in rowt] if rowt else [0] * n_src, notes)


# ---- 人挪过位置 ----

def col_offset(was: dict, pos: str | None, move: str | None) -> tuple:
    """人挪过 `col` 行时译文的位移：`("pos", dx, dy)` = 新 `\\pos` − `was` 里的位置；人写的是 `\\move` 时
    `("move", dx0, dy0, dx1, dy1[, t1, t2])`，起止点各减 `was` 里的位置（原来在动的取 `was` 的 `move` 的起点）。
    `was` 是 `core.parse_was` 解析过的，`pos` / `move` 是行首块里那个 tag 的参数（至少有一个；数不对报 `ValueError`）。
    位移取整到像素：跟着走的几块底板是在整数边界上拼严的（`block_rows`），各块左上角各自取整会在半像素的位移下错开一像素、留缝或叠一条。"""
    m = assfile.numbers(move) if move is not None else None
    q = assfile.numbers(pos) if m is None and pos is not None else None
    if m is not None and len(m) not in (4, 6):
        raise ValueError(f"\\move 要 4 或 6 个数：\\move({move})")
    if m is None and (q is None or len(q) != 2):
        raise ValueError(f"\\pos 要 2 个数：\\pos({pos})")
    ref = assfile.numbers(was.get("pos") or was.get("move") or "")[:2]
    if len(ref) < 2:
        return ("pos", 0.0, 0.0)
    if m is not None:
        return ("move", *(float(round(a - b)) for a, b in zip(m[:4], ref * 2)), *m[4:6])
    return ("pos", float(round(q[0] - ref[0])), float(round(q[1] - ref[1])))


def moved_where(x: float, y: float, d: tuple) -> str:
    """人挪过的 `col` 行里跟着译文走的元素的位置：`d` 是 `col_offset` 给的位移。人写的 `\\move` 没带时刻时这里也不带
    （整条 `[0, 时长]`）；元素晚出现、从中间切开时 `core.shift_move` 换成这一段的时刻。"""
    if d[0] == "pos":
        return "\\pos(%.0f,%.0f)" % (x + d[1], y + d[2])
    times = ",%d,%d" % (d[5], d[6]) if len(d) >= 7 else ""
    return "\\move(%.0f,%.0f,%.0f,%.0f%s)" % (x + d[1], y + d[2], x + d[3], y + d[4], times)


# ---- 底板 ----

def tr(r: list[float]) -> list[float]:
    """镜像转置 (x, y) → (y, −x)：从右往左的列变成从上往下的行，`layout` 现成的逐行底板（相邻两块从中线劈开那一步假定从上往下排）就能用。"""
    return [r[1], -r[2], r[3], -r[0]]


def untr(r: list[float]) -> list[float]:
    """`tr` 的逆：(u, v) → (−v, u)。"""
    return [-r[3], r[0], -r[1], r[2]]


@dataclass
class Plate:
    """一块底板：矩形、盖着哪些（译文 0..n−1、原文列 n..n+k−1，同 `layout.row_bands` 的数法）。
    `group`：底板取色时归哪一块（几块拼成一张底板时同一个号，字色按整张定）；None 是自己一块。"""
    rect: list[float]
    cover: frozenset = field(default_factory=frozenset)
    group: int | None = None


def plates(mode: str, src: list[list[float]], trs: list[list[float]], pad: float, per_col: bool, moved: bool,
           lines: list[list[float]] = ()) -> list[Plate]:
    """`col` 行的底板。`src` 是原文的列，`trs` 是译文占的矩形（竖排每列一块，`per_col`；横排一块），`lines` 是横排块逐行的矩形（`block`）。
    * `src`：每列原文一块，外扩边距、不劈开（阶段 5 擦的就是它）；
    * `text`：只盖译文（竖排每列一块，左右交叠的从中线劈开；横排逐行一块、拼成不重叠的几块，同普通行的 `text`；没给 `lines` 时整块一块）；
    * `textbox`：只盖译文，一整块（竖排几列、横排几行连成一块）；
    * `box`：一整块 = 原文各列 ∪ 译文；
    * 竖排的 `rows`：每列一块 = 原列 ∪ 译文列；列数不同时译文各列加全部原文列拼成不重叠的竖条（`layout.row_bands`），原文列总被盖全；
    * 横排的 `rows`（`block_rows`）：外扩过的原列和逐行外扩过的译文拼成不重叠的几块（`layout.pieces`），正好盖住它们的并、不是一整个大矩形。
    `moved`（人挪过位置）：盖原文和盖译文的分开算——原文那部分（`rows` 拼条、`box` 一块）留在原地，译文那部分另拼、跟着字走（调用方平移）。"""
    n, k = len(trs), len(src)
    srcs = [Plate(layout.padded(s, pad), frozenset({n + i})) for i, s in enumerate(src)]
    if mode == "src":
        return srcs
    if mode == "rows" and not per_col:
        return block_rows(src, lines, pad, moved, n)
    lens, tops, ths = [t[3] - t[1] for t in trs], [t[1] for t in trs], [t[2] - t[0] for t in trs]

    def cols_text() -> list[Plate]:
        got = layout.plate_rects([tr(t) for t in trs], lens, tops, "text", pad, "left", ths)
        return [Plate(untr(r), frozenset({j})) for j, r in enumerate(got)]
    if mode == "text":
        if per_col:
            return cols_text()
        return block_rows([], lines, pad, False, n) if lines else [Plate(layout.padded(t, pad), frozenset({j})) for j, t in enumerate(trs)]
    if mode == "textbox":
        return [Plate(layout.padded(layout.union(trs), pad), frozenset(range(n)))] if trs else []
    whole = mode == "box"
    if moved:
        if whole:
            return ([Plate(layout.padded(layout.union(src), pad), frozenset(range(n, n + k)))] if src else []) + \
                   ([Plate(layout.padded(layout.union(trs), pad), frozenset(range(n)))] if trs else [])
        return ([Plate(untr(b), frozenset(n + i for i in cov)) for b, cov in layout.bands([layout.padded(tr(s), pad) for s in src])]
                + [Plate(untr(b), cov) for b, cov in layout.bands([layout.padded(tr(t), pad) for t in trs])])
    if whole:
        return [Plate(layout.padded(layout.union(src + trs), pad), frozenset(range(n + k)))] if src or trs else []
    if n == k:
        got = layout.plate_rects([tr(s) for s in src], lens, tops, "rows", pad, "left", ths)
        return [Plate(untr(r), frozenset({i, n + i})) for i, r in enumerate(got)]
    return [Plate(untr(b), c) for b, c in layout.row_bands([tr(t) for t in trs], lens, tops, "left", ths, [tr(s) for s in src], pad)]


def block_rows(src: list[list[float]], lines: list[list[float]], pad: float, moved: bool, n: int) -> list[Plate]:
    """横排块的 `rows` 底板（同普通行 `rows` 的意思：盖该盖的原文、加上译文的几行，不是一整块）：外扩过的原列和逐行外扩过的译文
    （先往外取整，拼出来的块之间不留亚像素的缝）拼成不重叠的几块（`layout.pieces`）。盖着的按 `plates` 的数法：译文整块是 0（几行都算它；`n` 是 1，空译文时 0），原文列是 n..n+k−1，
    所以每块从它盖着的里面最早出现的时刻起。几块同一个 `group`：底板取色时字色按整张定，不按落在哪一小块。
    `moved`：原文那部分在原地拼、译文那部分另拼（跟着字走），两部分之间不劈。"""
    def rnd(r: list[float]) -> list[float]:            # 往外取整：盖得住外扩过的原列
        q = layout.padded(r, pad)
        return [float(math.floor(q[0])), float(math.floor(q[1])), float(math.ceil(q[2])), float(math.ceil(q[3]))]
    rs = [rnd(r) for r in lines] + [rnd(s) for s in src]
    ids = [0] * len(lines) + [n + i for i in range(len(src))]

    def tile(idx: list[int]) -> list[Plate]:
        return [Plate(r, frozenset(ids[idx[c]] for c in cov), 0) for r, cov in layout.pieces([rs[i] for i in idx])]
    every = list(range(len(rs)))
    if moved:
        return tile(every[len(lines):]) + tile(every[:len(lines)])
    return tile(every)


def owner(x: float, y: float, rects: list[list[float]]) -> int:
    """(x, y) 落在哪块底板里（没有落进的取中心最近的）：底板取色时逐字行的字色按它所在那块底板定。"""
    for i, r in enumerate(rects):
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            return i
    return min(range(len(rects)), key=lambda i: ((rects[i][0] + rects[i][2]) / 2 - x) ** 2 + ((rects[i][1] + rects[i][3]) / 2 - y) ** 2) if rects else 0
