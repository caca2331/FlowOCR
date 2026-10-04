"""**探针**：框位那条名牌判据的精确率 / 召回，按**角色名表**量（只读 `*-tracks.json`）。

为什么要有这个文件（2026-09-20 审计七 §2.4）：ui-gate 计划 §19.4 / §22.3 / §25.1
那三张五部表是**临时脚本**量的，磁盘上没留下来——于是加了第 4 条判据（`cover`）之后
只补了 f1/f3/f5，f2/f4 再也没人跑，索引行就把两个版本的数拼成了一个范围。
把它落成工具，谁都能重数：

    python dev_tools/probe_np_score.py out/yuka-f{1,2,3,4,5}/f*-tracks.json \\
        --names tmp/match/Text/CharacterNames.bytes
    python dev_tools/probe_np_score.py out/yuka-f5/f5-tracks.json --names … --sweep-cover 0,.3,.5,.7

**判据不在这里**：`uigate.pick_nameplates` 是唯一实现，这份只喂 Item、只数对错。
名表的口径也只有一份——`probe_nameplate_geom.is_name` / `is_name_variant`
（精确率报**下界~上界**：`桜羽工マ` 这种 OCR 错字卡在 0.8 门下面，只报下界会低估）。

⚠ **口径写死在这里，省得下次又是临时脚本**：

* 一个"条目" = 一条 **run**（`tracks.json` 的一个事件），不是 cue、不是观测；
* **召回的分母 = 全片文本像名字的 run，含主轨里的那些**——这是 §19.4/§22.3/§25.1
  那三张表的口径（校准过：f3 55% / f5 66% 和 §25.1 逐数相同）。
  `--exclude-main` 换成"排掉主轨"那一种（名字混进正文 cue 的不算名牌判据的漏），
  读数会高 2~12 个点，**两种口径不能混着引**；
* 精确率的分母 = 判据挑中的 run；**上界那把尺要求子串至少 2 个字**（§21.2 的口径）——
  取 1 的话 `月` / `大` / `橘` 这种单字都算"像名字"，上界会虚高（f5 77% → 69%）。

⚠ 这份**不重跑 `build_tracks`**：事件（run）和聚类旋钮无关（`--slot-reassign` 两臂上
`len(events)` 逐数相同），所以老产物照样能量。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
import probe_nameplate_geom as PNG  # noqa: E402
from flowocr.artifacts import tracksio  # noqa: E402
from flowocr.analyze import uigate  # noqa: E402


def score_one(doc: dict, names: list[str], a, min_cover: float) -> dict:
    items = [uigate.Item(tuple(e["box"]), e["t_start"], e["t_end"], e["text"],
                         key=i, w=max(1, e.get("n_obs", 1)))
             for i, e in enumerate(doc["events"])]
    picked = {it.key for it in uigate.pick_nameplates(
        items, win_us=int(a.window * 1e6), dur_ratio=a.dur_ratio,
        max_churn=a.churn, min_cover=min_cover,
        step_us=doc.get("frame_us") or 1)}
    skip: set[int] = set()
    if a.exclude_main:
        mt = tracksio.main_track(doc)
        if mt:
            skip = {i for c in mt["cues"] for i in c["events"]}
    name = {i for i, e in enumerate(doc["events"])
            if PNG.is_name(e["text"], names, a.max_chars)}
    # ⚠ 上界那把尺的 `min_sub` **默认 2**（§21.2 的口径，2026-09-20 落实到代码）：
    # 取 1 的话单个汉字（`月`/`大`/`橘`/`桜`）只要是某个名字的子串就算"像名字"，
    # 上界虚高（f5 77% → 69%）。`--loose-variant` 复现旧口径。
    var = {i for i, e in enumerate(doc["events"])
           if i in name or PNG.is_name_variant(e["text"], names, a.max_chars,
                                               min_sub=1 if a.loose_variant else 2)}
    denom = name - skip
    hit = picked & name
    return {"picked": len(picked), "hit": len(hit),
            "prec_lo": len(picked & name) / max(1, len(picked)),
            "prec_hi": len(picked & var) / max(1, len(picked)),
            "denom": len(denom), "recall": len(picked & denom) / max(1, len(denom))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracks", nargs="+", help="一个或多个 *-tracks.json")
    ap.add_argument("--names", required=True, help="角色名表（**只用来评判据，不进修法**）")
    ap.add_argument("--window", type=float, default=120.0, help="同 build_tracks --nameplate-window")
    ap.add_argument("--dur-ratio", type=float, default=0.8)
    ap.add_argument("--churn", type=float, default=0.8)
    ap.add_argument("--cover", type=float, default=0.5, help="同 build_tracks --nameplate-cover")
    ap.add_argument("--sweep-cover", default="",
                    help="逗号分隔，逐个 cover 各量一遍（看阈值坐不坐在平台上）")
    ap.add_argument("--exclude-main", action="store_true",
                    help="召回的分母**排掉主轨里的名字 run**（默认连它们一起算，见文件头）")
    ap.add_argument("--max-chars", type=int, default=8)
    ap.add_argument("--loose-variant", action="store_true",
                    help="精确率**上界**那把尺退回 §21.2 之前的松口径（单字子串也算像名字）。"
                         "只用来复现旧表，别拿它报新数")
    ap.add_argument("--json", default=None, help="把逐片的数写一份 JSON")
    a = ap.parse_args()

    names = PNG.load_names(Path(a.names))
    covers = ([float(x) for x in a.sweep_cover.split(",")] if a.sweep_cover else [a.cover])
    out: list[dict] = []
    for cov in covers:
        print(f"\n=== cover >= {cov:g}（窗 {a.window:g}s、时长 ≥ 正文×{a.dur_ratio}、"
              f"词表 ≤ {a.churn}）===")
        print(f"{'片':<26}{'挑中':>7}{'其中名字':>9}{'精确率(下~上)':>16}"
              f"{'名字 run':>10}{'召回':>8}")
        for p in a.tracks:
            doc = tracksio.load(Path(p))
            r = score_one(doc, names, a, cov)
            r |= {"tracks": p, "cover": cov, "git_head": doc["provenance"].get("git_head")}
            out.append(r)
            print(f"{Path(p).stem:<26}{r['picked']:>7}{r['hit']:>9}"
                  f"{r['prec_lo']:>8.0%}~{r['prec_hi']:<7.0%}"
                  f"{r['denom']:>10}{r['recall']:>8.0%}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
