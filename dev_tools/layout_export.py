"""从整场的匹配产物导出一款游戏的**版式文件**（`flowocr.analyze.textbox` 的 `layout_for`）：短片段上量不出的画像字段从它补。

    python dev_tools/layout_export.py <matched.json> [<matched.json> ...] --game <游戏> [--out <文件>] [--min-items 200]

只取条目到 `--min-items` 条、量出了一字宽的区域（主对话框这类；次要区域整场的样本也少，进文件反而带错）；
几份产物里中心相近（`LAYOUT_NEAR` 的一半以内）、一字宽相近的区域并成一条，数值取中位、类别取多数（打平不给）。
每个字段只取**这份产物自己量出来的**：判不出的竖直 / 横向对齐、从版式文件补的字段不进，免得旧文件的值自己喂自己。
位置和长度按**游戏画面**（视口）存、和分辨率无关：中心离视口中心的横向距离 / 视口高、离视口顶 / 视口高，一字宽 / 视口高，行距 / 一字宽，文本框宽 / 一字宽；
视口取匹配时记下的（`overlay.stats.textbox.viewport`）：给定的（建轨 `--viewport` / 匹配 `--ctx viewport=`）、或按整个画面对上的；匹配时反推出来的不收（那是拿旧文件配出来的，再导出就是旧值喂自己），
没对上、退回整个画面的（`fallback`）也不收（游戏画面多半不占满录像），
主播布局这类录像要用给了游戏画面位置的轨道（或匹配时 `--ctx viewport=`）重跑匹配；没用版式文件跑的匹配没记视口，按整个画面。`aspect`（视口宽 / 高）取几份的中位。
默认写到 `src/flowocr/analyze/layouts/<游戏>.json`。
英文字宽 `latin_em`（一个拉丁字平均占几个一字宽）从日文录像量不出，是按英文版录像手填的：输出文件已经有时，中心相近的条目把它原样带过来。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from flowocr import paths
from flowocr.analyze import textbox as TB
from flowocr.artifacts import tracksio


def resolve(p: str) -> Path:
    """产物里记的路径（`provenance.portable`：相对数据根、可能是 Windows 写法、家目录下写成 `~/…`）换成能开的路径。"""
    q = Path(p.replace("\\", "/")).expanduser()
    return q if q.is_absolute() else paths.data_root() / q


def candidates(path: Path, min_items: int) -> list[dict]:
    """一份匹配产物里够格的区域，各一条候选（字段缺的不写；`aspect` 是这份的视口宽 / 高）。"""
    m = json.loads(path.read_text(encoding="utf-8"))
    ov = m["overlay"]
    v = ov["stats"]["textbox"].get("viewport")
    if v and v["by"] in ("inferred", "fallback"):
        raise SystemExit(f"{path}：匹配时游戏画面是{'反推的' if v['by'] == 'inferred' else '没对上、退回整个画面的'}（{v['box']}），"
                         f"导出要确定的位置：建轨加 --viewport x,y,w,h（或匹配加 --ctx viewport=）重跑匹配")
    doc = tracksio.load(resolve(m["provenance"]["subs"]))
    W, H = doc["size"]
    v = v or {"by": "frame", "box": [0, 0, W, H]}
    vp = TB.Viewport(*v["box"], (W, H), v["by"])
    feats = {str(r["index"]): r.get("features") or {} for r in doc.get("regions") or ()}
    out = []
    for reg, p in ov["textbox"].items():
        d = ov["stats"]["textbox"]["regions"][reg]
        filled = set((d.get("layout") or {}).get("fields") or ())
        f = feats.get(reg) or {}
        if d["items"] < min_items or not p.get("em") or "em" in filled or "cx" not in f:
            continue
        em = p["em"]
        dx, cy = vp.rel(f)
        c = {"dx": dx, "cy": cy, "em_h": em / vp.h, "aspect": vp.w / vp.h, "src": f"{doc.get('tag') or path.parent.name}:r{reg}"}
        if p.get("pitch") and "pitch" not in filled:
            c["pitch_em"] = p["pitch"] / em
        if d["valign"].get("by") != "none" and "valign" not in filled:
            c["valign"] = p["valign"]
        if p.get("chars") and "chars" not in filled:
            c["chars"] = p["chars"]
        if p.get("box_w") and (d.get("box_w") or {}).get("by") in ("chars", "rows") and not filled & {"box_w", "chars"}:
            c["box_em"] = p["box_w"] / em
        if (d.get("align") or {}).get("by") == "rows" and "align" not in filled:
            c["align"] = p["align"]
        out.append(c)
    return out


def dist(a: dict, b: dict, aspect: float) -> float:
    """两条的中心差，同 `textbox.layout_entry`：纵向按视口高、横向按视口宽。"""
    return max(abs(a["dx"] - b["dx"]) / aspect, abs(a["cy"] - b["cy"]))


def merge(cands: list[dict], aspect: float) -> list[dict]:
    """中心差在 `LAYOUT_NEAR` 一半以内、一字宽 / 视口高差在 `LAYOUT_EM_TOL` 以内的并成一条。"""
    groups: list[list[dict]] = []
    for c in cands:
        for g in groups:
            h = g[0]
            if (dist(c, h, aspect) <= TB.LAYOUT_NEAR / 2
                    and abs(c["em_h"] - h["em_h"]) <= TB.LAYOUT_EM_TOL * h["em_h"]):
                g.append(c)
                break
        else:
            groups.append([c])
    boxes = []
    for i, g in enumerate(sorted(groups, key=lambda g: -len(g))):
        b: dict = {"id": f"box{i}"}
        for k, nd in (("dx", 5), ("cy", 4), ("em_h", 5), ("pitch_em", 3), ("box_em", 2), ("chars", None)):
            xs = [c[k] for c in g if k in c]
            if xs:
                b[k] = round(statistics.median(xs), nd)     # chars 取整（nd = None）
        for k in ("valign", "align"):
            top = Counter(c[k] for c in g if k in c).most_common(2)
            if top and (len(top) == 1 or top[0][1] > top[1][1]):
                b[k] = top[0][0]
        b["sources"] = [c["src"] for c in g]
        boxes.append(b)
    return boxes


def old_boxes(out: Path) -> list[dict]:
    """输出文件已有的条目（只为把 `latin_em` 带过来才读）；没有、或是读不了的旧格式（按整个录像画面存的）是空的，后者打警告。"""
    if not out.is_file():
        return []
    try:
        return TB.load_layout(out)["boxes"]
    except ValueError as e:
        print(f"[警告] 输出文件读不了、不带它的 latin_em（要的话导出后手填）：{e}", file=sys.stderr)
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("matched", nargs="+", type=Path)
    ap.add_argument("--game", required=True, help="匹配器认的游戏名（gametext 剧本的 game、naninovel 的 --ctx game=），也是文件名")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--min-items", type=int, default=200)
    a = ap.parse_args(argv)
    cands = [c for p in a.matched for c in candidates(p, a.min_items)]
    if not cands:
        print("没有够格的区域（条目数、一字宽）", file=sys.stderr)
        return 1
    out = a.out or TB.LAYOUT_DIR / f"{a.game}.json"
    aspect = round(statistics.median(c["aspect"] for c in cands), 5)
    boxes = merge(cands, aspect)
    old = old_boxes(out)
    for b in boxes:
        ds = {i: dist(o, b, aspect) for i, o in enumerate(old) if o.get("latin_em")}
        if ds and min(ds.values()) <= TB.LAYOUT_NEAR / 2:
            b["latin_em"] = old[min(ds, key=ds.get)]["latin_em"]
    # sources 排最后（诊断，人读时放在一条的末尾）
    doc = {"schema": TB.LAYOUT_SCHEMA, "game": a.game, "aspect": aspect,
           "boxes": [{k: v for k, v in b.items() if k != "sources"} | {"sources": b["sources"]} for b in boxes]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"-> {out}：{len(doc['boxes'])} 条（{len(cands)} 个区域）")
    for b in doc["boxes"]:
        print("  ", {k: v for k, v in b.items() if k != "sources"}, f"来源 {len(b['sources'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
