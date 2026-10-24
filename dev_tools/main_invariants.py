"""主轨成员的不变量：逐份 tracks 核 `main.regions`、`band_merged` 和主轨 cue 对得上。

下游按事件判在不在主轨（`tracksio.in_main`：区域在 `main.regions` 里，或事件带 `band_merged`）。这要求产物满足：

* 打了 `band_merged` 却不在 `main.regions` 里的事件，只出现在 `--main-band-unit window` 的产物里（默认 `region` 臂必须为 0：
  按事件判和按区域判等价）；
* 只并了一部分进主轨的区域不列进 `main.regions`；
* 这些区域里打了标的事件都在主轨 cue 里，进了主轨 cue 的事件都打了标；
* `tracksio.validate` 通过。

改建轨的并带、主轨或 `band_merged` 的写法之后，在一批现成产物上跑一遍；任何一份违反就退出码 1。

用法：python dev_tools/main_invariants.py <tracks.json 或 glob> [...]
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import tracksio  # noqa: E402


def check(doc: dict) -> dict:
    """返回各项计数；`bad` 是违反的项名。"""
    tracksio.validate(doc)
    regs = tracksio.main_regions(doc)
    mt = tracksio.main_track(doc)
    flagged = lambda e: "band_merged" in (e.get("flags") or [])   # noqa: E731
    bm = [e for e in doc["events"] if flagged(e)]
    outside = [e for e in bm if e["region"] not in regs]
    in_cue = {i for c in (mt["cues"] if mt else []) for i in c["events"]}
    part = {e["region"] for e in outside}
    n = {"band_merged": len(bm), "outside_regions": len(outside), "partial_regions": len(part),
         "partial_also_listed": len(part & regs),
         "flagged_not_in_cue": sum(e["id"] not in in_cue for e in outside),
         "in_cue_unflagged": sum(e["region"] in part and e["id"] in in_cue and not flagged(e) for e in doc["events"])}
    unit = (doc["provenance"].get("args") or {}).get("main_band_unit", "region")
    bad = [k for k in ("partial_also_listed", "flagged_not_in_cue", "in_cue_unflagged") if n[k]]
    if unit != "window" and n["outside_regions"]:
        bad.append("outside_regions")
    return {"unit": unit, **n, "bad": bad}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    n_bad = 0
    for pat in argv:
        paths = sorted(glob.glob(pat))
        if not paths:
            print(f"没有匹配的文件：{pat}")
            n_bad += 1
            continue
        for p in paths:
            r = check(tracksio.load(p))
            n_bad += bool(r["bad"])
            print(f"{'违反 ' + ','.join(r['bad']) if r['bad'] else 'ok'}  {p}（{r['unit']}）：band_merged {r['band_merged']}，"
                  f"不在 regions 里的 {r['outside_regions']}（部分并入的区域 {r['partial_regions']}，其中也列在 regions 里 {r['partial_also_listed']}）；"
                  f"打了标却不在主轨 cue 里 {r['flagged_not_in_cue']}；部分并入区域里进了主轨 cue 却没打标 {r['in_cue_unflagged']}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
