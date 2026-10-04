"""文字出现 / 消失的四个关键帧对逐帧真值打分，**按误差分档**（owner 2026-09-12：≤1 帧 / 2–3 帧 / 4–10 帧 / 10+ 帧，
10+ 和 4–10 最影响观感，调参先压它们），另报代价。

    python dev_tools/typewriter_eval.py <tracks.json> data/gt/typewriter-<tag>.json [--split validation]
    python dev_tools/typewriter_eval.py <tracks.json> data/gt/end-<tag>.json        # 结尾两个关键帧
    python dev_tools/typewriter_eval.py <tracks.json> <gt.json> --sampled            # 没回抠的轨

四个关键帧（真值文件里有哪个就打哪个）：

| 真值键 | 产物字段 | `--sampled` 时 |
| --- | --- | --- |
| `first` 首字出现 | `t_start` | `t_start` |
| `full` 全字出现 | `t_full` | `t_full_sampled` |
| `last_full` 完全显示结束 | `t_full_end` | 没有 |
| `gone` 完全消失 | `t_end`（`t_end_how = open` 的单列、不计代价） | `t_end` |

真值文件：`{"what", "labeled_by", "date", "video", "how", "events": [{"text", <键>…, "split", "anchor"?}]}`。
事件按**文本相同 + t_start 相差 1.5 s 以内**配到主轨上（事件 id 跟着 build_tracks 的代码变，不能当键）；
锚点取 `anchor`（抽样时的采样级 t_start），没有就取 `first` / `full`。配不上的单列，不静默少算。
帧 = 1/60 s（绝区零源片 30 fps 时它的 1 帧 = 这里 2 帧）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import tracksio  # noqa: E402

BINS = (("≤1", 1.25), ("2–3", 3.25), ("4–10", 10.25), ("10+", float("inf")))
"""上界（帧）。+0.25 帧容 pts 取整。"""

KEYS = (("first", "首字出现"), ("full", "全字出现"), ("last_full", "完全显示结束"), ("gone", "完全消失"))

COST_CAP_FR = 10
COST_MILD_OVER = 2.0
COST_BAD_OVER = 5.0
BAD_SIDE = {"first": +1, "full": +1, "last_full": -1, "gone": -1}
"""单条代价（帧）：10 帧以内 = 误差帧数（早晚一样）；超出 10 帧的部分，**会缩短"清晰显示"那一侧** ×5、另一侧 ×2
（owner 2026-09-12 定：偏得越多扣得越多；清晰显示比实际长一点不要紧，短了难看——
全字出现 / 首字出现晚了、完全显示结束 / 完全消失早了是 ×5）。"""


def cost_of(err_us: float, key: str = "full") -> float:
    fr = err_us * 60 / 1e6
    a = abs(fr)
    if a <= COST_CAP_FR:
        return a
    bad = (fr > 0) == (BAD_SIDE[key] > 0)
    return COST_CAP_FR + (a - COST_CAP_FR) * (COST_BAD_OVER if bad else COST_MILD_OVER)


def bin_of(err_us: float) -> str:
    fr = abs(err_us) * 60 / 1e6
    return next(name for name, ub in BINS if fr <= ub)


def got_of(e: dict, key: str, sampled: bool):
    if key == "first":
        return e["t_start"]
    if key == "full":
        return e.get("t_full_sampled") if sampled else e.get("t_full")
    if key == "last_full":
        return None if sampled else e.get("t_full_end")
    return e["t_end"]


def score(doc: dict, gt: dict, sampled: bool, split: str) -> dict:
    # 只在**主轨**上配：真值是从主轨抽的，别的区域里同文本的副本（名牌带、并带前的碎片）没回抠也会被配上
    tr = tracksio.main_track(doc)
    if tr is None:
        raise SystemExit("产物没有主轨，打不了分")
    by_text: dict[str, list[dict]] = {}
    for eid in sorted({i for cue in tr["cues"] for i in cue["events"]}):
        e = doc["events"][eid]
        by_text.setdefault(e["text"], []).append(e)
    zero = lambda v: {k: v for k, _ in KEYS}  # noqa: E731
    out = {"bins": {k: dict.fromkeys((b for b, _ in BINS), 0) for k, _ in KEYS},
           "early": zero(0), "late": zero(0), "late10": zero(0), "cost": zero(0.0), "unmatched": [], "rows": [],
           # 配上了、真值有这一项、产物却**没测出**这一项（`t_full` 为空之类）：不计代价、不进分档，**单独数**——
           # 不数的话"测不出"看起来和"误差 0"一样好（2026-09-24 Codex 复审 P2）
           "missing": zero(0),
           # 完全消失是**开放边界**（`t_end_how = open`：测量窗里没看到它消失，t_end 只是下界）：同样不计代价、单独数
           "open": 0}
    for g in gt["events"]:
        if split and g.get("split") != split:
            continue
        a = g.get("anchor", g.get("first") if g.get("first") is not None else g.get("full"))
        if a is None:
            out["unmatched"].append(g["text"])
            continue
        cands = [e for e in by_text.get(g["text"], []) if abs(e["t_start"] - a * 1e6) <= 1.5e6]
        if not cands:
            out["unmatched"].append(g["text"])
            continue
        e = min(cands, key=lambda e: abs(e["t_start"] - a * 1e6))
        row = {"text": g["text"][:24]}
        for key, _ in KEYS:
            got = got_of(e, key, sampled)
            if g.get(key) is None:
                continue
            if got is None:
                out["missing"][key] += 1
                continue
            if key == "gone" and e.get("t_end_how") == "open":
                out["open"] += 1
                continue
            err = got - g[key] * 1e6
            out["bins"][key][bin_of(err)] += 1
            out["cost"][key] += cost_of(err, key)
            if abs(err) * 60 / 1e6 > BINS[0][1]:
                out["early" if err < 0 else "late"][key] += 1
            if err * 60 / 1e6 * BAD_SIDE[key] > BINS[2][1]:
                out["late10"][key] += 1
            row[key] = round(err / 1e6, 3)
        out["rows"].append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tracks")
    ap.add_argument("gt", nargs="+")
    ap.add_argument("--sampled", action="store_true", help="打采样级（没回抠的轨）：全字取 t_full_sampled、没有完全显示结束")
    ap.add_argument("--split", default="", help="只打这一组（design / validation）；默认全部")
    ap.add_argument("--rows", action="store_true", help="逐条列出误差（秒）")
    a = ap.parse_args()
    doc = tracksio.load(Path(a.tracks))
    for p in a.gt:
        gt = json.loads(Path(p).read_text(encoding="utf-8"))
        r = score(doc, gt, a.sampled, a.split)
        n = len(r["rows"])
        print(f"{Path(p).name}（{'采样级' if a.sampled else '回抠'}，{a.split or '全部'}，配上 {n} 条"
              + (f"，**配不上 {len(r['unmatched'])} 条**" if r["unmatched"] else "") + "）")
        for key, name in KEYS:
            m = sum(r["bins"][key].values())
            if r["missing"][key]:
                print(f"  {name}  **配上了、产物没测出 {r['missing'][key]} 条**（不在下面的分档和代价里）")
            if key == "gone" and r["open"]:
                print(f"  {name}  **开放边界 {r['open']} 条**（t_end_how = open，窗里没看到消失、只是下界；不在下面的分档和代价里）")
            if not m:
                continue
            cells = "  ".join(f"{b}: {r['bins'][key][b]}" for b, _ in BINS)
            print(f"  {name}  {cells}   （>1 帧里 偏早 {r['early'][key]} / 偏晚 {r['late'][key]}，"
                  f"其中{'晚' if BAD_SIDE[key] > 0 else '早'} 10+ {r['late10'][key]}）  代价 {r['cost'][key]:.0f}（每条 {r['cost'][key] / m:.2f}）")
        if a.rows:
            for row in r["rows"]:
                print("   ", row)
        if r["unmatched"]:
            print("  配不上：", " | ".join(t[:20] for t in r["unmatched"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
