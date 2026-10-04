"""叠加字幕的排版：字号、锚点、底板、画面边界、按轨迹逐段运动。阶段 3（字幕稿要写预览位置）和阶段 4（生成最终行）共用这一份。

方案：overlay-style 计划（对齐、锚点、底板）、script-fx 计划（拆成字幕稿 + 特效渲染之后这些从 `overlay` 挪到这里）。
这里只做几何，不做判断：对齐方式是阶段 2 判好的，效果时刻是阶段 3 判好的。
"""
from __future__ import annotations

from flowocr.output import export

OVERLAY_PAD = 6
"""底板比 OCR 框外扩的像素：盖住原字笔画的边缘。也是字离画面边的最小距离。"""

PLATE_MODES = ("box", "rows", "text")
"""底板怎么画（owner 2026-09-12 要三种比观感）：
* `box`：原字幕正文框的并集，一整块；
* `rows`：逐行一块，每行宽 = max(原文这一行的区间, 译文这一行的区间)——多行字幕不填满矩形；
* `text`：只有译文文字底下有底板。"""

OVERLAY_MIN_SCALE = 0.75
"""叠加字号不低于"按框高换算的字号"的这么多（owner 2026-09-12：第五个选项字被压得过小）。只给译文。"""

ANCHOR = {"left": 4, "center": 5, "right": 6}
"""对齐方式 → `\\an`（竖直居中，横向锚左沿 / 中心 / 右沿）。"""
ALIGN_OF = {v: k for k, v in ANCHOR.items()}


def text_span(axis: float, w: float, align: str) -> tuple[float, float]:
    """锚在 `axis` 上、宽 `w` 的一行字占的横向区间。"""
    if align == "left":
        return axis, axis + w
    if align == "right":
        return axis - w, axis
    return axis - w / 2, axis + w / 2


def anchor_axis(rows: list[list[float]], align: str, common: bool) -> list[float]:
    """每行的锚轴。`common`（matched 的多行）取整组的公共轴：居中用**最宽那行**的中心，左 / 右用最外的沿——
    `rows_of` 的行框只是这一行**被收下的**事件的并集，一行被 det 切成两框、其中一个被剔时只剩半行，
    拿它自己的中心当轴，这一行会歪半个行宽；缺了半截的那行不会是最宽的（两行时中位数就是平均，挡不住）。
    不 `common`（tracks 一个事件一行）取这一行自己的框。"""
    def own(r):
        return r[0] if align == "left" else r[2] if align == "right" else (r[0] + r[2]) / 2
    if not common:
        return [own(r) for r in rows]
    if align == "left":
        a = min(r[0] for r in rows)
    elif align == "right":
        a = max(r[2] for r in rows)
    else:
        a = own(max(rows, key=lambda r: r[2] - r[0]))
    return [a] * len(rows)


def plate_rects(rows: list[list[float]], text_w: list[float], axis, mode: str,
                align: str = "center") -> list[list[float]]:
    """每行一个底板矩形 [x0, y0, x1, y1]（`box` 模式一整块）。`axis` 是锚轴（一个数 = 各行共用，或逐行一个）；
    译文区间按锚点算（`text_span`）。上下相邻的两行外扩后会叠一条，半透明时叠的地方更黑，所以从中间劈开。"""
    p = OVERLAY_PAD
    if mode == "box":
        return [[min(r[0] for r in rows) - p, rows[0][1] - p, max(r[2] for r in rows) + p, rows[-1][3] + p]]
    axes = axis if isinstance(axis, list) else [axis] * len(rows)
    out = []
    for r, w, a in zip(rows, text_w, axes):
        tx0, tx1 = text_span(a, w, align)
        x0, x1 = (tx0, tx1) if mode == "text" else (min(r[0], tx0), max(r[2], tx1))
        out.append([x0 - p, r[1] - p, x1 + p, r[3] + p])
    for a, b in zip(out, out[1:]):
        if b[1] < a[3]:
            a[3] = b[1] = (a[3] + b[1]) / 2
    return out


def chunks(p: str, n: int = 24) -> list[str]:
    """长段量字宽时切小块再加：`render_ink` 一页 4096 px 宽，整段渲会被截断。"""
    return [p[i:i + n] for i in range(0, len(p), n)] or [p]


def width_keys(lines: list[str], block: bool) -> list[str]:
    """一条要量哪些字宽：普通条目量每一行（行里的换行拆开量），面板量每段切成的小块。"""
    if block:
        return [c for p in lines for c in chunks(p)]
    return [x for ln in lines for x in ln.split("\n")]


def line_width(font: str, text: str, widths: dict) -> float:
    """字号 1 时一行（可能带换行、按最宽的那段算）的墨迹宽。"""
    return max((widths.get((font, x), 0.0) for x in text.split("\n")), default=0.0)


def fit_line(rows: list[list[float]], lines: list[str], font: str, widths: dict, translated: bool, W: int) -> int:
    """普通条目的字号 = `export.fit_size`：框高按行数分、框宽按 libass 真渲的墨迹宽反推，取小的。
    译文另有下限：框很窄（选项按钮 `「未着」` 日文括号窄、中文括号宽）时不许缩到框高换算字号的
    `OVERLAY_MIN_SCALE` 以下，宁可让字超出框、底板跟着放宽；但不许超出画面。原文压回自己的框，只按框定
    （框偏高时保底会把字撑出框，gi-s2 摆帧）。"""
    box = [min(r[0] for r in rows), rows[0][1], max(r[2] for r in rows), rows[-1][3]]
    size = export.fit_size("\n".join(lines), font, box[2] - box[0], box[3] - box[1], widths)
    if not translated:
        return size
    # 保底按**看得见的行数**分框高：一个框里画几行（人在字幕稿里加了换行）时，保底不能按一行算，不然几行叠起来冲出框
    n = sum(len([x for x in ln.split("\n") if x.strip()]) or 1 for ln in lines)
    by_h = (box[3] - box[1]) / max(1, n) * export.h_ratio(font)
    w1 = max((line_width(font, ln, widths) for ln in lines), default=0.0)
    size = max(size, int(OVERLAY_MIN_SCALE * by_h))
    return min(size, int((W - 4 * OVERLAY_PAD) / w1)) if w1 > 0 else size


def expand_rows(rows: list[list[float]], k: int, H: int) -> list[list[float]]:
    """正文比原文框装得下的多出 `k` 行：按原来一行的高度（几个框时取行距）往下扩出 `k` 行，往下会出画面就往上扩。
    扩出的行横向取原文几行合起来的范围；压到别的字不管（owner 2026-09-27：一个框里分成两行时扩出一行，不为重叠做保证）。"""
    rows = [list(r) for r in rows]
    if k <= 0:
        return rows
    h = rows[-1][3] - rows[-1][1]
    pitch = (rows[-1][1] - rows[0][1]) / (len(rows) - 1) if len(rows) > 1 else h
    x0, x1 = min(r[0] for r in rows), max(r[2] for r in rows)
    below = [[x0, rows[-1][1] + j * pitch, x1, rows[-1][1] + j * pitch + h] for j in range(1, k + 1)]
    if below[-1][3] <= H - OVERLAY_PAD:
        return rows + below
    return [[x0, rows[0][1] - j * pitch, x1, rows[0][1] - j * pitch + h] for j in range(k, 0, -1)] + rows


def expand_box(rect: list[float], cap: int, k: int, H: int) -> list[float]:
    """一个框本来装着 `cap` 行（OCR 原文带换行），正文多出 `k` 行：框按一行的高度（框高 / `cap`）长出 `k` 行，方向同 `expand_rows`。"""
    h = (rect[3] - rect[1]) / cap
    grown = expand_rows([[rect[0], rect[1] + i * h, rect[2], rect[1] + (i + 1) * h] for i in range(cap)], k, H)
    return [rect[0], grown[0][1], rect[2], grown[-1][3]]


def same_size(boxes: list[tuple[int, int, list[float]]], sizes: list[int]) -> list[int]:
    """同一栏、同时在屏的选项按钮字号取中位数：各按钮的框高、读法（整句 / 残读并进来的）不一，
    各算各的就一个大一个小（hsr 0:23 摆帧：第二个选项比兄弟大一号）。`boxes[i]` = (起, 止, 第一行框)。"""
    out = []
    for s0, e0, bi in boxes:
        peers = [sz for (s1, e1, bj), sz in zip(boxes, sizes)
                 if s1 < e0 and s0 < e1
                 and min(bi[2], bj[2]) - max(bi[0], bj[0]) > 0.5 * min(bi[2] - bi[0], bj[2] - bj[0])]
        out.append(sorted(peers)[len(peers) // 2])
    return out


def axes_in_screen(rows: list[list[float]], text_w: list[float], align: str, common: bool, W: int) -> list[float]:
    """锚轴，最宽那行放不进画面就把整组的轴往里推（左 / 右锚时译文比原文长会从一侧伸出去）。"""
    axes = anchor_axis(rows, align, common)
    spans = [text_span(a, w, align) for a, w in zip(axes, text_w)]
    lo, hi = min(s for s, _ in spans), max(z for _, z in spans)
    pad = OVERLAY_PAD
    shift = pad - lo if lo < pad else (W - pad) - hi if hi > W - pad else 0
    return [a + shift for a in axes]


def block_layout(box: list[float], paras: list[str], row_h: int, font: str, widths: dict,
                 size: int | None = None) -> tuple[int, list[str]]:
    """长文本块：译文按段折行填进块里。字号从原文行高换算起，放不下就往小缩（`size` 给了就用它，只折行）；
    折行按每段的平均字宽手算（libass 的自动折行只在空格处断，中文整段不会折）。返回（字号, 折好的行，段间空一行）。"""
    bw, bh = box[2] - box[0], box[3] - box[1]
    unit = [sum(widths.get((font, c), 0.0) for c in chunks(p)) / max(1, len(p)) for p in paras]

    def layout(size: int) -> list[str]:
        out = []
        for p, u in zip(paras, unit):
            k = max(1, int(bw / max(u * size, 1e-6)))
            out += [p[i:i + k] for i in range(0, len(p), k)] + [""]
        return out[:-1]
    if size is not None:
        return size, layout(size)
    size = max(export.FIT_MIN_SIZE, int(row_h * export.h_ratio(font)))
    while size > export.FIT_MIN_SIZE and len(layout(size)) * size > bh:
        size -= 1
    return size, layout(size)


def motion_segments(boxes: list | None, s: int, e: int) -> list[tuple[int, int, tuple, tuple]]:
    """在动的事件在 `[s, e]` 里**按轨迹逐段拆**，每段 = (起, 止, 起点框左上角, 止点框左上角)。

    一条从首点到末点的 `\\move` 会把中间过程抹平（先静止后上移的字被画成从头匀速滑、步进滚动被画成平滑滚动），
    所以同一位置的相邻两点是一段停顿，位置变了才是一段位移。轨迹段被 `[s, e]` 裁掉一半时端点**跟着插值**——
    照搬原端点会把整段位移重播一遍。轨迹之外的尾巴停在最后一个位置。不动的（没有轨迹）返回一段、端点为 None。"""
    if not boxes or len(boxes) < 2:
        return [(s, e, None, None)]

    def at(b0, b1, t0, t1, t):
        f = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
        return (b0[0] + (b1[0] - b0[0]) * f, b0[1] + (b1[1] - b0[1]) * f)

    out = []
    for (t0, b0), (t1, b1) in zip(boxes, boxes[1:]):
        a, z = max(s, t0), min(e, t1)
        if z <= a:
            continue
        if b0[:2] == b1[:2]:
            out.append((a, z, (b0[0], b0[1]), (b0[0], b0[1])))
        else:
            out.append((a, z, at(b0, b1, t0, t1, a), at(b0, b1, t0, t1, z)))
    last_t, last_b = boxes[-1]
    if e > max(s, last_t):
        out.append((max(s, last_t), e, (last_b[0], last_b[1]), (last_b[0], last_b[1])))
    return out or [(s, e, None, None)]


def pos_tag(x: float, y: float, seg: tuple, base: tuple[float, float]) -> str:
    """一个元素在一段里的位置标签：静止用 `\\pos`，位移用 `\\move`。`(x, y)` 是按 `base`（条目框的左上角）
    排出来的静止位置，在动的段把它平移到轨迹上。"""
    a, z, p0, p1 = seg
    if p0 is None:
        return "\\pos(%.0f,%.0f)" % (x, y)
    x0, y0 = x + p0[0] - base[0], y + p0[1] - base[1]
    if p0 == p1:
        return "\\pos(%.0f,%.0f)" % (x0, y0)
    x1, y1 = x + p1[0] - base[0], y + p1[1] - base[1]
    return "\\move(%.0f,%.0f,%.0f,%.0f,0,%d)" % (x0, y0, x1, y1, max(z - a, 0) // 1000)
