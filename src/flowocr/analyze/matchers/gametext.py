"""内置匹配器：**游戏文本库**（原神 / 星铁 / 绝区零的文本包，`gtd-bundle/1`，由 `gamescript` 读成剧本；game-text-corpus 报告）。

两个入口（`flowocr.extensions`）：

* `cluster_patch(context, options)`：按游戏给默认聚类器（build_tracks）的参数覆盖。表就是 `PATCHES`
  （原 `tools/game_patches.py`，2026-09-22 搬进来；那个文件现在是把字典翻成 CLI 参数的薄适配）。
  `context` 给 `game`（`genshin` / `starrail` / `zzz`）或 `tag`（素材标签，前缀认游戏，同 `gamescript.TAG_GAME`）。
* `match(document, context, options)`：`document` 是 `*-tracks.json` 的 dict；`context["ref"]` 是 `gamescript` 产的剧本 JSON 路径，
  `context["tracks_path"]` 是那份 tracks 的路径（记进 provenance；`feed == "main"` 时从它取主轨 SRT）。
  返回 `*-matched.json` 的 dict（`scriptmatch.SCHEMA`）。判据和 CLI `scriptmatch.py` 是同一份 `run_match`。

`options` 的键和默认值同 `scriptmatch.py` 的旋钮：`feed`（nonoise）、`feed_panel`（keep）、`fuzzy`（0.75）、
`merge_gap`（`scriptmatch.MERGE_GAP`）、`overlay`（True）、`readable_textmap` / `obs`（None）。
"""
from __future__ import annotations

from pathlib import Path

from flowocr.analyze import game_align as GA
from flowocr.analyze import gamescript as GS
from flowocr.analyze import scriptmatch as SM

PATCHES: dict[str, dict] = {
    # 星铁：窗内并区的时间门。遗器故事的阅读面板和字幕带**从不同屏**，却因为空间上挨着被并查集
    # 串成一块（game-text-corpus 报告）；这道门要求"同屏过 or 时段交错 ≥2 次"才连边。
    # 整场 hsr +34 条命中，cue 1,645 → 1,450、对不上剧本 344 → 269、重复认领 811 → 654，两段切片 ±0。
    # **原神 / 绝区零不开**：那两款上是 −49 / −58 / −30，病在"主轨只能挑一个区域"接不住被分开的带。
    "starrail": {"region_time_gate": 2},
}
"""游戏 -> build_tracks 的参数覆盖。键是参数名（`region_time_gate`），不是 CLI 旗标。"""


def _flag(v) -> bool:
    """`options` 的值可能是命令行来的**字符串**（`--opt overlay=0`）——`bool("0")` 是 True，
    静默把"关掉"读成"开着"。这里按字面认（2026-09-22）。"""
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(v)


def game_of(context: dict) -> str | None:
    """`context["game"]` 优先；否则按素材标签前缀认（`gi-s1` → genshin），认不出就 None。"""
    game = context.get("game")
    if game:
        return str(game)
    tag = str(context.get("tag") or "")
    return GS.TAG_GAME.get(tag.split("-")[0].rstrip("0123456789")) if tag else None


def cluster_patch(context: dict, options: dict | None = None) -> dict:
    """这款游戏要给默认聚类器的参数覆盖；没有就是空字典（不改默认）。"""
    return dict(PATCHES.get(game_of(context) or "", {}))


def match(document: dict, context: dict, options: dict | None = None) -> dict:
    """见文件头。`document` 不动；返回新的 matched dict。"""
    o = dict(options or {})
    ref_path = context.get("ref")
    if not ref_path:
        raise ValueError("gametext 匹配器要 context['ref']（gamescript 产的剧本 JSON 路径）")
    ref = GA.load_ref(Path(ref_path))
    tracks_path = str(context.get("tracks_path") or "")
    params = {"feed": o.get("feed", "nonoise"), "feed_panel": o.get("feed_panel", "keep"),
              "fuzzy": float(o.get("fuzzy", 0.75)), "merge_gap": float(o.get("merge_gap", SM.MERGE_GAP))}
    res = SM.run_match(ref, tracks_path, params["feed"], params["feed_panel"], params["fuzzy"], params["merge_gap"],
                       overlay=_flag(o.get("overlay", True)), readable_textmap=o.get("readable_textmap"),
                       obs=o.get("obs"), doc=document)
    st = SM.match_stats(ref, res["recs"], res["suspect_raw"])
    return SM.matched_doc(ref, res["recs"], res["n_raw"], st, str(ref_path), res["subs_path"], res["tp"],
                          list(context.get("argv") or []), params, res["items"], res["ov_stats"])
