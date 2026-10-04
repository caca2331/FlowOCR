"""**探针**：字幕带上的 run 没进主轨，是在哪一层走丢的——窗内区域（并查集）还是跨窗缝合。

来历（game-text-corpus 报告 §7.4，audit-6 之后）：星铁整场换对主轨之后，"obs 读到过却没命中"的有内容行
80% 落在主轨以外的区域，框却都在字幕带。docs/dev-guide/pitfalls.md「"某个旋钮无效"不能反推病灶在哪」的规矩是**把每一层的产物分别数一遍**，这里就数两层：

  层 2  窗内区域（`cluster_windowed` 里的 `build_regions`）：目标 run 所在的窗区域干不干净
        （有没有带外的 run 混进来）；
  层 3  跨窗缝合之后的最终区域（直接读 `-tracks.json`）：它落进了哪个区域、那个区域进没进主轨。

目标 run = 框的 cy 在 `--band` 里、内容 ≥ `--min-chars` 字的 run（字幕带形状，**只用来打标签，不进修法**）。
run 按产物 provenance 里记的参数重建（同一份 obs、同一组 build_runs 参数 = 同一批 run），
再按 (起点, 文字, 框) 对回 tracks.json 的事件。

    python dev_tools/probe_band_layers.py out/gamestream/hsr.jsonl out/gs-hsr/hsr-tracks.json
"""
from __future__ import annotations

import argparse
import inspect
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.analyze import build_tracks as bt  # noqa: E402
from flowocr.analyze import gamescript as GS  # noqa: E402
from flowocr.artifacts import tracksio  # noqa: E402


RUN_ARGS = ("min_conf", "iou", "sim", "gap_frames", "no_typewriter", "text_pick",
            "tail_max", "vote_independent", "prefix_min", "typewriter_min_chars", "grow_h_ratio")
"""`build_runs` 吃的那些参数。两臂只要这几个相同，run 下标就能对上（`--vs` 靠它）。"""


def cluster_kwargs(pa: dict) -> dict:
    """窗内聚类的旋钮**一律从产物 provenance 取**，这里不许写默认值。

    2026-09-12 外部 review 抓到的：漏传 `region_time_gate`，于是探针拿 gate=0 重建窗区域、
    而产物是 gate=2，分层表里"窗区域干不干净"整整一列是错的，**一行报错都没有**。
    `check_cluster_kwargs` 现在机械地拦这件事：`cluster_windowed` 以后再加旋钮，
    不在这里接上就跑不起来。
    `region_h_ratio` 是 2026-09-24 加的旋钮：更早的产物 provenance 里没有这个键，那时的行为就是 1.7（`REGION_H_RATIO`），
    所以缺键时取它——这是"加旋钮之前的值"，不是函数默认值。"""
    return {"mutual_x": pa["mutual_x"], "time_gate": pa["region_time_gate"],
            "h_ratio": pa.get("region_h_ratio", bt.REGION_H_RATIO)}


def check_cluster_kwargs() -> None:
    want = {n for n, p in inspect.signature(bt.cluster_windowed).parameters.items()
            if p.default is not inspect.Parameter.empty}
    got = set(cluster_kwargs({"mutual_x": None, "region_time_gate": None, "region_h_ratio": None}))
    if got != want:
        raise SystemExit(f"探针没把 cluster_windowed 的旋钮接全：缺 {sorted(want - got)}、"
                         f"多 {sorted(got - want)}（见 cluster_kwargs 的来历）")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obs")
    ap.add_argument("tracks", help="同一份 obs 产的 -tracks.json（取最终区域、主轨与 build_runs 的参数）")
    ap.add_argument("--band", type=float, nargs=2, default=(0.74, 0.90), metavar=("Y0", "Y1"),
                    help="字幕带的纵向范围（框中心 cy / 画面高），只用来打标签")
    ap.add_argument("--min-chars", type=int, default=8, help="内容至少这么多字才算目标")
    ap.add_argument("--vs", default=None, metavar="OTHER-TRACKS.JSON",
                    help="另一条臂的产物：**逐 run** 报两臂之间目标 run 的落点变化"
                         "（两个分类总数相减不是'救回了多少'——2026-09-12 外部 review）")
    a = ap.parse_args()

    check_cluster_kwargs()
    doc = tracksio.load(Path(a.tracks))
    prov = doc["provenance"]
    pa = prov["args"]
    lines = Path(a.obs).read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])["_meta"]
    obs = [o for o in (json.loads(x) for x in lines[1:] if x.strip()) if o["conf"] >= pa["min_conf"]]
    W, H = doc["size"]
    frame_us = int(round(1e6 / meta["sample_fps"]))
    runs = bt.build_runs(obs, frame_us, pa["iou"], pa["sim"], pa["gap_frames"],
                         grow_typewriter=not pa["no_typewriter"], text_pick=pa["text_pick"],
                         tail_max=pa["tail_max"], vote_independent=pa["vote_independent"],
                         prefix_min=pa["prefix_min"],
                         # 这两个旋钮是后加的：旧产物 provenance 里没有，取加旋钮之前的行为
                         typewriter_min_chars=pa.get("typewriter_min_chars", bt.TYPEWRITER_MIN_CHARS),
                         grow_h_ratio=pa.get("grow_h_ratio", 1.4))    # 1.4 = 加旋钮之前的行为（默认 09-24 起是 1.7）
    _, win_regions = bt.cluster_windowed(runs, W, H, int(pa["window_sec"] * 1e6),
                                         **cluster_kwargs(pa))
    win_of = {i: wi for wi, w in enumerate(win_regions) for i in w["runs"]}

    def placement(d: dict):
        """这份产物里，每条 run 落进了哪个区域 / 那个区域进没进主轨。"""
        mt = tracksio.main_track(d)
        mr = set(mt.get("regions") or [mt.get("region")])
        k1 = {(e["t_start"], e["text"], tuple(e["box"])): e["region"] for e in d["events"]}
        k2 = {(e["t_start"], e["text"]): e["region"] for e in d["events"]}
        return mr, k1, k2

    main_regions, by_key, by_key2 = placement(doc)
    reg = {r["index"]: r for r in doc["regions"]}
    lo, hi = a.band

    def in_band(r):
        return lo <= r.cy / H <= hi

    target = [i for i, r in enumerate(runs) if in_band(r) and len(GS.cnorm(r.text)) >= a.min_chars]
    final = {i: by_key.get((runs[i].t_start, runs[i].text, tuple(runs[i].box)),
                           by_key2.get((runs[i].t_start, runs[i].text))) for i in target}
    lost = sum(1 for v in final.values() if v is None)
    print(f"{Path(a.obs).name}: {len(runs)} runs（{len(doc['events'])} 个事件）、窗区域 {len(win_regions)} 个；"
          f"字幕带形状的目标 run {len(target)} 条，对不回事件的 {lost}")
    if len(runs) != len(doc["events"]):
        print("  ⚠ run 数和事件数对不上——provenance 里的参数没重现同一批 run，下面的数不可信")

    def dirty(wi):
        rs = win_regions[wi]["runs"]
        return sum(1 for j in rs if not in_band(runs[j])) / len(rs)

    rows = Counter()
    eaters = Counter()
    for i in target:
        where = ("对不回" if final[i] is None else "主轨内" if final[i] in main_regions else "主轨外")
        rows[(where, "窗区域干净" if dirty(win_of[i]) == 0 else "窗区域混了带外 run")] += 1
        if where == "主轨外":
            eaters[final[i]] += 1
    print(f"主轨区域 {sorted(main_regions)}\n目标 run 按（最终落点 × 它所在的窗区域干不干净）：")
    for (w, c), v in sorted(rows.items()):
        print(f"  {w:<6} {c:<16} {v:>6}")
    out = [dirty(win_of[i]) for i in target if final[i] is not None and final[i] not in main_regions]
    if out:
        print(f"  主轨外的那些，所在窗区域里带外 run 占比：中位 {st.median(out):.2f}，"
              f"干净（=0）的 {sum(1 for x in out if x == 0)}/{len(out)}")
        print("  混了带外 run = 在**窗内并查集**那一层就连到了别的东西上；干净却在主轨外 = **缝合 / 并带**那一层")
    print("主轨外吃掉目标 run 最多的区域：")
    for ri, v in eaters.most_common(6):
        f = reg[ri]["features"]
        print(f"  region{ri:<4} {reg[ri]['label']:<22} 目标 {v:>5}  cy={f['cy']:.2f} y_span={f['y_span']:.2f} "
              f"h={f['h']:.4f} n_runs={f['n_runs']} moving={f['moving_share']:.3f}")

    if a.vs:
        other = tracksio.load(Path(a.vs))
        oa = other["provenance"]["args"]
        differ = {k: (oa.get(k), pa.get(k)) for k in RUN_ARGS if oa.get(k) != pa.get(k)}   # 后加的键旧产物没有：取 get，缺键和有键算不同（保守）
        if differ:
            raise SystemExit(f"两臂的 build_runs 参数不同，run 下标对不上，不能逐 run 比：{differ}")
        omr, ok1, ok2 = placement(other)

        def where_of(i, mr, k1, k2):
            r = k1.get((runs[i].t_start, runs[i].text, tuple(runs[i].box)),
                       k2.get((runs[i].t_start, runs[i].text)))
            return "对不回" if r is None else "主轨内" if r in mr else "主轨外"

        move = Counter((where_of(i, omr, ok1, ok2), where_of(i, main_regions, by_key, by_key2))
                       for i in target)
        print(f"\n逐 run 对账（{Path(a.vs).name} -> {Path(a.tracks).name}，{len(target)} 条目标 run）："
              "\n  **这才是'救回了多少'**——两臂各自的分类总数相减不是它（两边的目标集合一样，落点各自算）")
        for (o, n), v in sorted(move.items()):
            mark = "  ←救回" if (o, n) == ("主轨外", "主轨内") else "  ←丢掉" if (o, n) == ("主轨内", "主轨外") else ""
            print(f"  {o} -> {n:<6} {v:>6}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
