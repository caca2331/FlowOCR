"""文字方向（横排 / 竖排）的判据：提取层选读数、建轨分行排序、阶段 3 / 4 拼行都从这里取，**规则只有一份**。

竖排日文字幕（偶像综艺的说明字、提问框）在 det 那里是一根竖条：一列一个框，高是字数 × 字宽。
rec 只会横着读——竖条原样送进去会被高压到 48 px，整列挤成一两个字的宽度，读出来是乱码。
PP-OCRv6 的 rec 认得**逆时针转 90° 的竖排列**（PaddleOCR 自己的管线对高宽比 ≥ 1.5 的框也是这么转的）：
vertical-text 计划的探针里 405 个竖条框转过去读，日文说明字几乎逐字对；顺时针转、按方格切开横拼都差得多。

但横排素材里也有竖条框：单个窄字（`8`、`k`、`è`）、竖着的装饰线、贴边的半个字——quick 六段 + 中文两段约 0.1%–0.6% 的框。
它们转过去读出来是另一串乱码（`8` -> `do`），分数还常常更高。所以**两种读法都读、按下面的规则挑**，
不只看谁的分高：竖排是中日韩文字的写法，转过去读出来至少两个字、里面有中日韩字，才算竖排。
"""
from __future__ import annotations

TALL = 1.5
"""框高 ≥ 这么多倍框宽算竖条，才去试竖排读法（PaddleOCR 的同一个数）。竖排两个字的一列高宽比约 2。"""

ROT_MIN_SCORE = 0.5
"""竖排读法的分数下限。探针：竖排说明字转过去读 0.9 以上居多，模糊的过场帧 0.6 上下；横排素材竖条框转过去的乱码 0.2–0.9。"""


def tall(w: float, h: float) -> bool:
    """宽 `w`、高 `h` 的框是不是竖条。"""
    return w > 0 and h >= TALL * w


NOT_CJK = frozenset({0x30FB, 0x30FC, 0xFF70})
"""假名区里的中点 `・` 和长音 `ー` / `ｰ`：形状是点和线，竖装饰线转过去读常读成 `ーー`，不算中日韩字。"""


def cjk_count(text: str) -> int:
    """中日韩字（汉字、假名、谚文）有几个。全角标点、`NOT_CJK`、拉丁字母、数字不算。"""
    n = 0
    for ch in text:
        o = ord(ch)
        if o in NOT_CJK:
            continue
        if (0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
                or 0xF900 <= o <= 0xFAFF or 0xAC00 <= o <= 0xD7AF or 0xFF66 <= o <= 0xFF9D):
            n += 1
    return n


def looks_vertical(text: str) -> bool:
    """一段字**能不能是竖排**：至少两个字、里面有中日韩字。单个字横竖读都一样，拉丁字母和数字不竖排。"""
    t = text.strip()
    return len(t) >= 2 and cjk_count(t) >= 1


def prefer_rotated(upright: tuple[str, float], rotated: tuple[str, float]) -> bool:
    """竖条框原样读出 `upright`、逆时针转 90° 读出 `rotated`（都是 `(文字, 分数)`），该不该取转过去的读法。

    转过去的要像竖排（`looks_vertical`）、分数不低于 `ROT_MIN_SCORE`，并且分数比原样的高，或者多读出至少两个字——
    原样读一根竖条最多挤出一个字（「行」0.984 对「行って！」0.939），分数比不出来。"""
    rt, rs = rotated
    ut, us = upright
    if not looks_vertical(rt) or rs < ROT_MIN_SCORE:
        return False
    return rs > us or len(rt.strip()) >= len(ut.strip()) + 2


COL_W_RATIO = 1.7
"""`columns_adjacent` 的列宽比门的默认值：两列宽相差超过这么多倍不算相邻。建轨里它就是窗内并区的字高比门
（`build_tracks.REGION_H_RATIO`，`--region-h-ratio`，按游戏的补丁可改；守卫核两者相等）；阶段 3 取 tracks 的 provenance 里记下的那个值，没记时用它。"""


def columns_adjacent(a: tuple[float, float, float, float], b: tuple[float, float, float, float], w_ratio: float) -> bool:
    """两列竖排字是不是同一块里相邻的列：列宽相近（宽比不超过 `w_ratio`，0 = 不看）、左右挨着（cx 差不超过 2.2 个较窄的列宽，
    和横排行距的门同一个数）、上下有重叠就算。上下不要求像横排那样重叠过半：竖排说明字常把后一列错开往下排（「※今回作るのは」/「栗ご飯です」）。

    `a` / `b` 是一列的 (上沿, 下沿, cx, 列宽)：建轨并区（`build_tracks.build_regions`）传列轨各项的中位数，
    阶段 3 拆 `col` 行（`script.col_groups`）传条目里这一列的框。判据只有这一份，两边按同一规则判"相邻"。"""
    (a0, a1, acx, aw), (b0, b1, bcx, bw) = a, b
    if w_ratio and max(aw, bw) / max(1.0, min(aw, bw)) > w_ratio:
        return False
    return abs(acx - bcx) <= 2.2 * min(aw, bw) and min(a1, b1) > max(a0, b0)


def col_geom(box) -> tuple[float, float, float, float]:
    """一个列框（`[x0, y0, x1, y1]`）→ `columns_adjacent` 要的 (上沿, 下沿, cx, 列宽)。"""
    return (box[1], box[3], (box[0] + box[2]) / 2, box[2] - box[0])


def vertical(box, text: str) -> bool:
    """产物里的一个框（`[x0, y0, x1, y1]`）和它的文字是不是一列竖排字。

    提取层取了竖排读法的框都满足它（竖条 + `looks_vertical`），下游不必另记方向：建轨按列聚类（`build_tracks.vertical_run`）、
    常驻 UI 门按列长量框长（`uigate.measure`）、拼行排序（`tracksio.vertical_line`）都用它。
    反过来不一定：竖条框原样读出两个以上中日韩字的（横排素材里极少：quick 六段只有 wuwa 一处 UI 竖排标签）也算竖排，按列聚、按列排。"""
    return tall(box[2] - box[0], box[3] - box[1]) and looks_vertical(text)
