"""`*-matched.json`（阶段 2 的匹配产物）的读与校验，**全仓库只有这一份**。

和 `tracksio` 对 `*-tracks.json` 的做法一样：版本按**整串**比，不等就拒，不做向后兼容（docs/architecture/README.md「产物契约」）。
2026-09-22 从 `scriptmatch` 搬到 `artifacts`（Codex 审计 P2）：阶段 3 的输出预设要读这份产物，
而 `output` 只许依赖 `artifacts`——判断留在 `analyze`，**产物的格式属于 artifacts**。
`scriptmatch` 顶层 re-export 这里的名字，老调用方（`SM.load_matched` / `SM.SCHEMA` / `SM.body`）不变。

一份 matched 里有什么：`cues`（一条 cue 一条记录，`ref` 是认领的剧本行或 None）、`stats`、`provenance`
（含 `subs`：产它的那份 `*-tracks.json`），以及可选的 `overlay`（叠加层判定，`--no-overlay` 时没有）。
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "flowocr-matched/2"
"""`*-matched.json` 的版本。**写它的人必须有个读它的人**（2026-09-15 审计）：
`/1` → `/2` 升过一次而全仓库没有一处读这个字符串，等于没有版本控制——
旧产物喂给期待 `overlay` 的代码只会得到 `KeyError`，而不是"这是旧 schema"。"""


class MatchedSchemaError(Exception):
    """`*-matched.json` 不是当前版本 / 缺必需字段。"""


REQUIRED = ("schema", "provenance", "stats", "cues")
"""每份 matched.json 都要有的顶层键。`overlay` 不在里头——`--no-overlay` 和非 tracks 输入都没有它，
要用它的调用方自己检查（`load(..., need_overlay=True)`）。"""

LAYERS = ("body", "extra", "name", "choice", "offmain", "panel")
"""`overlay.items` 的层（owner 2026-09-14，product-goals 第 16 条）：判定（`scriptmatch.overlay_items`）写进 JSON，
导出时逐层开关（叠加预设的 `layers`）。属于产物契约，所以放这里——判断和投影两边都读它。

* `body`：主认领那条剧本行，盖在 cue 的正文行上（`body_of`）；
* `extra`：同一条 cue 装着的第二条剧本行，盖在**它自己的**那几行上——不并进正文、不拆 cue；
* `name`：名牌（`game_align.head_lines` 认出的开头行），译文先取剧本说话人的中文名（`speaker_names`），
  查不到再按字形查文本包的短名词表（`term_names`）；条目的 `name_src` 记是哪一种（`speakers` / `terms` / 空）；
* `choice`：主轨外的选项按钮（`choice_items` 里剧本行类型是 `Choice` / `OffBand` 的）；
* `offmain`：主轨外、逐字等于剧本行的其余文字（台词落到了别的区域、`Loose` 散条目…，`choice_layer`）；
* `panel`：阅读面板长文本（`readable_items`，实验，要 `--readable-textmap` + `--obs`）。"""


def load(path: Path, need_overlay: bool = False) -> dict:
    """读 `*-matched.json`，**校验版本和必需字段**，对不上就抛 `MatchedSchemaError`。

    `need_overlay=True` 时再要求有 `overlay.items`，并把"这份是不是 `--no-overlay` 产的"说清楚。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(doc, need_overlay, str(path))
    return doc


def validate(doc: dict, need_overlay: bool = False, where: str = "<doc>") -> None:
    """同 `load` 的校验，给已经读进来的 dict 用（阶段 3 的预设拿到的就是 dict）。"""
    got = doc.get("schema")
    if got != SCHEMA:
        raise MatchedSchemaError(
            f"{where} 的 schema 是 {got!r}，当前是 {SCHEMA!r}——重跑匹配生成，不做兼容")
    missing = [k for k in REQUIRED if k not in doc]
    if missing:
        raise MatchedSchemaError(f"{where} 缺顶层键 {missing}")
    if need_overlay and (doc.get("overlay") or {}).get("items") is None:
        raise MatchedSchemaError(
            f"{where} 没有 overlay.items（`--no-overlay` 产的，或匹配时 --subs 不是 *-tracks.json）")


def body(rec: dict, lang: str) -> list[str]:
    """导出字幕时这条 cue 的正文：匹配上就用剧本原文（`\\N` 是条内换行），否则原样留 OCR。"""
    r = rec["ref"]
    t = (r or {}).get(lang) if r else None
    return (t or rec["ocr"]).replace("\\N", "\n").split("\n")


def tracks_path(doc: dict) -> str:
    """产这份 matched 的 `*-tracks.json`（provenance 记的 `subs`）。叠加 ASS 要它的框和画面尺寸。"""
    return str((doc.get("provenance") or {}).get("subs") or "")
