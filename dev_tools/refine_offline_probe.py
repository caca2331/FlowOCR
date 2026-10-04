"""回抠的离线对照探针：同一份没回抠的 tracks，分别按 obs 里的在线证据结算、按开视频离线测量结算，逐事件比三个时刻。

    python dev_tools/refine_offline_probe.py out/x/x-tracks.json --video x.mp4 [--decoder ffmpeg|cv2] [--out tmp/x-probe.tsv]

- **输入要是 `build_tracks --refine off` 的产物**：默认建轨已经按在线证据结算过，拿它再回抠会把帧级时刻当采样级开窗。
- **不写 tracks、不写 SRT**：产物流程里测量只在阶段 1（在线）、结算只在阶段 2（按证据），离线开视频测只用来核对
  在线证据量得对不对（owner 2026-09-26）。`--out` 另写逐事件的 TSV（写进 tmp/）。
- **三个时刻各算各的分母**：目标 = 回抠标签区域里的全部事件；每个时刻分"两边都量到 / 只在线 / 只离线 / 都没量到"，
  差值只在"两边都量到"上算。量到的判据：首字、全字看 `t_full_how` 的 `first` / `full` 不是 `none`（是 `none` 的值是采样级退回），
  消失看有没有 `t_end_how`（只量到结尾的事件没有 `t_full_how`，不能拿它筛），`open`（窗里没看到消失、只是下界）不算量到、另数。
- 两边走同一份结算（`refine_boundaries.refine_tracks`），只差证据从哪来：`obs`（在线量的 `edge`）vs `cv2` / `ffmpeg`（开视频取窗）。
  09-17 翻默认时的读数：两条离线路之间时刻 ≥ 98% 逐帧相同（reuse-budget 计划 §7）。
- 开视频要 CPU 解码整片的窗口，长片要十几分钟；跑之前先 `hw status`。
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
from pathlib import Path

from flowocr.analyze import build_tracks as bt
from flowocr.analyze import refine_boundaries as RB
from flowocr.artifacts import tracksio
from flowocr.extract.refine_video import video_codec, video_fps
from flowocr.provenance import local

KEYS = ("t_start", "t_full", "t_end")


def measured(ev: dict, key: str) -> bool:
    """这个时刻是不是回抠真量出来的（不是采样级的原值或退回值）。"""
    if key == "t_end":
        return ev.get("t_end_how") not in (None, "open")
    how = (ev.get("t_full_how") or {}).get("first" if key == "t_start" else "full")
    return how not in (None, "none") and ev.get(key) is not None


def compare(base: dict, online: dict, offline: dict, labels: str, frame_ms: float) -> tuple[dict, list[dict]]:
    """逐时刻的分母账 + "两边都量到"上的差值（离线 − 在线，原生帧）。"""
    want = None if labels == "all" else set(labels.split(","))
    lab = {r["index"]: r["label"] for r in base["regions"]}
    targets = [i for i, e in enumerate(base["events"]) if want is None or lab.get(e["region"]) in want]
    stats = {k: {"targets": len(targets), "both": 0, "online_only": 0, "offline_only": 0, "neither": 0, "diffs": []} for k in KEYS}
    stats["t_end"].update(open_online=0, open_offline=0)
    rows = []
    for i in targets:
        e_on, e_off = online["events"][i], offline["events"][i]
        row = {"id": i, "text": e_on["text"][:30]}
        for k in KEYS:
            a, b = measured(e_on, k), measured(e_off, k)
            st = stats[k]
            if a and b:
                d = (e_off[k] - e_on[k]) / 1000 / frame_ms
                st["both"] += 1
                st["diffs"].append(d)
                row[k] = round(d, 2)
            else:
                st["online_only" if a else "offline_only" if b else "neither"] += 1
                row[k] = "on" if a else "off" if b else "-"
        stats["t_end"]["open_online"] += e_on.get("t_end_how") == "open"
        stats["t_end"]["open_offline"] += e_off.get("t_end_how") == "open"
        rows.append(row)
    return stats, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracks", help="build_tracks --refine off 的 -tracks.json")
    ap.add_argument("--video", required=True, help="原视频")
    ap.add_argument("--decoder", choices=("auto", "cv2", "ffmpeg"), default="auto",
                    help="离线那一边怎么取帧：auto = av1 走 ffmpeg、其余 cv2")
    ap.add_argument("--obs", default="", help="默认取 tracks provenance 记的 obs")
    ap.add_argument("--labels", default=RB.DEFAULT_LABELS, help="回抠哪些标签的区域（同建轨默认）")
    ap.add_argument("--out", default="", help="逐事件差值写成 TSV（放 tmp/）")
    ap.add_argument("--salvage-end", action=argparse.BooleanOptionalAction, default=bt.REFINE_SALVAGE_END,
                    help="在线那一侧用被回补作废、窗口凑齐的结尾证据（同 build_tracks --refine-salvage-end，默认跟生产一样开），"
                         "--no-salvage-end 看不救回时的对账")
    a = ap.parse_args(argv)

    base = tracksio.load(Path(a.tracks))
    if "boundary_refined" in base:
        raise SystemExit(f"{a.tracks} 已经回抠过：用 build_tracks --refine off 重建一份再比")
    obs = Path(a.obs) if a.obs else local(base["provenance"].get("obs") or "")
    meta, fused = RB.obs_evidence(obs)
    if not fused:
        raise SystemExit(f"{obs} 里没有在线回抠的证据，没东西可比")
    decoder = a.decoder if a.decoder != "auto" else ("ffmpeg" if video_codec(a.video) == "av01" else "cv2")

    online, offline = copy.deepcopy(base), copy.deepcopy(base)
    print("== 在线证据（obs）")
    RB.refine_tracks(online, decoder="obs", fps=float(meta["src_fps"]), obs=obs, labels=a.labels,
                     obs_code_fp=meta.get("code_fp", ""), salvage_stale_end=a.salvage_end)
    print(f"== 离线测量（{decoder}，开视频）")
    RB.refine_tracks(offline, decoder=decoder, fps=video_fps(a.video), obs=obs, video=a.video, labels=a.labels)

    frame_ms = 1000 / float(meta["src_fps"])
    stats, rows = compare(base, online, offline, a.labels, frame_ms)
    print(f"\n离线 − 在线，单位：原生帧（{frame_ms:.2f} ms）；目标 {len(rows)} 个事件（回抠标签区域里的全部事件）")
    for k in KEYS:
        st = stats[k]
        print(f"  {k:8} 两边都量到 {st['both']:5}  只在线 {st['online_only']:4}  只离线 {st['offline_only']:4}  都没量到 {st['neither']:4}"
              + (f"  （开放边界、不算量到：在线 {st['open_online']} / 离线 {st['open_offline']}）" if k == "t_end" else ""))
        v = st["diffs"]
        if v:
            same = sum(abs(x) < 0.5 for x in v) / len(v)
            near = sum(abs(x) <= 1.5 for x in v) / len(v)
            print(f"  {'':8} 两边都量到的里：逐帧相同 {same:6.1%}  ±1 帧内 {near:6.1%}  中位 {statistics.median(v):+.2f}  "
                  f"最大 |差| {max(abs(x) for x in v):.1f}")
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write("id\ttext\t" + "\t".join(KEYS) + "\n")         # 差值（原生帧）；on / off / - = 只在线 / 只离线 / 都没量到
            for r in rows:
                f.write(f"{r['id']}\t{r['text']}\t" + "\t".join(str(r.get(k, "")) for k in KEYS) + "\n")
        print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
