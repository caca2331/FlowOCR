"""两条臂的**事件级**对账：漏条 / 幽灵 / 切分 / 边界，外加"跨下游门"的行数。

为什么要它（2026-09-18 审计第 7 条）：`rec_engine_accept.py` 那套分栏只说**文本**，
`game_align --vs` 只说**剧本认领集合**。两样都说明不了"漏条 / 误并 / 幽灵事件"——
那要一把**事件级**的尺。这份探针补上，并且**同一组素材上分别量 A/A、B/B、A/B**：
前两者是各自的底噪（同一条命令跑两遍），第三者才是"换引擎/换旋钮真改了什么"。
不这么摆，1% 量级的差根本读不出是效果还是抖动。

事件是什么：`*-tracks.json` 的 `events` —— 同一处文字的一段（`t_start` / `t_full` / `t_end` / `box` / `text`）。
这正是产品关心的单位：漏条 = 少一个事件、幽灵 = 多一个、误并 = 两个并成一个、边界 = 起止时刻挪了。

**配对判据是几何 + 时间，不看文本**（非循环）：`IoU ≥ 0.7` 且时间有重叠。配上之后再报文本差。
切分差单独数：A 的一个事件压着 B 的 ≥2 个（B 更碎）、反过来（B 更并）。

usage: python dev_tools/arm_events.py <seg id…> --pairs -w1:-w1b,-ortw1:-ortw1b,-w1:-ortw1
"""
from __future__ import annotations

import argparse
import json
import random
import os
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "dev_tools"))
sys.path.insert(0, str(CODE / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr import paths  # noqa: E402
os.chdir(paths.data_root())
import evpair  # noqa: E402   事件配对的判据（两个探针共用一份）
from flowocr.artifacts import tracksio  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("segs", nargs="+")
ap.add_argument("--pairs", required=True, help="逗号分隔的 `臂A:臂B`（臂 = 产物后缀，空 = 默认臂）")
ap.add_argument("--dir", default="out/samples")
ap.add_argument("--iou", type=float, default=0.7)
ap.add_argument("--show", type=int, default=0,
                help="每一类**随机抽**几条打出来（只在 A / 只在 B / 文本差 / 首尾挪动超过 1 s）")
ap.add_argument("--seed", type=int, default=7, help="抽样的种子（换个种子再看一遍，别只看一批）")
a = ap.parse_args()




def load(seg: str, arm: str):
    p = Path(a.dir) / f"{seg}{arm}" / f"{seg}-tracks.json"
    if not p.is_file():
        return None
    return tracksio.load(p)


def main_cues(doc) -> int:
    mt = doc["provenance"].get("main_track") or ""
    for tr in doc["tracks"]:
        if tr["id"] == mt:
            return len(tr["cues"])
    return 0


print(f"{'段':<20}{'臂对':<20}{'事件 A/B':>13}{'配上':>6}{'只在A':>6}{'只在B':>6}"
      f"{'文本差':>7}{'首差':>6}{'尾差':>6}{'最大挪(ms)':>11}{'A碎':>5}{'B碎':>5}{'主轨cue':>9}")
for seg in a.segs:
    for pair in a.pairs.split(","):
        arm_a, arm_b = pair.split(":")
        da, db = load(seg, arm_a), load(seg, arm_b)
        if da is None or db is None:
            print(f"{seg:<20}{pair:<20}  ⚠ 缺产物")
            continue
        ea, eb = da["events"], db["events"]
        half = da["frame_us"] / 2
        pa, pb = evpair.pair_events(ea, eb, a.iou)      # 配对判据只有一份，见 dev_tools/evpair.py
        # 切分差：一侧的事件压着另一侧 ≥2 个（不看是否配上）
        def split_count(src, dst):
            n = 0
            for e in src:
                k = sum(1 for f in dst if evpair.overlap(e, f) > 0 and evpair.iou(e["box"], f["box"]) >= a.iou)
                n += k >= 2
            return n

        txt = sum(1 for i, j in pa.items() if ea[i]["text"] != eb[j]["text"])
        ds = [abs(ea[i]["t_start"] - eb[j]["t_start"]) for i, j in pa.items()]
        de = [abs(ea[i]["t_end"] - eb[j]["t_end"]) for i, j in pa.items()]
        n_ds = sum(1 for x in ds if x > half)
        n_de = sum(1 for x in de if x > half)
        worst = max(ds + de + [0]) / 1000
        print(f"{seg:<20}{pair:<20}{f'{len(ea)}/{len(eb)}':>13}{len(pa):>6}"
              f"{len(ea) - len(pa):>6}{len(eb) - len(pb):>6}{txt:>7}{n_ds:>6}{n_de:>6}{worst:>11.1f}"
              f"{split_count(eb, ea):>5}{split_count(ea, eb):>5}"
              f"{f'{main_cues(da)}/{main_cues(db)}':>9}")
        # **抽样要随机、而且要分层**（2026-09-19 审计第 3 条）：原来是
        # `list(set(...))[:n]`——小整数的 set 按升序迭代，取到的永远是**下标最小的前 n 个**，
        # 不是样本；而且只看了"只在一侧"，文本差和边界挪动那两类从来没人逐条看过，
        # 于是"差的全是 HUD 碎片"这句话的证据面比它听起来窄。
        rng = random.Random(a.seed)

        def show(tag: str, items: list[str]) -> None:
            if not items:
                return
            for line in (items if len(items) <= a.show else rng.sample(items, a.show)):
                print(f"    {tag}：{line}")

        show("只在 A", [f"{ea[i]['t_start']/1e6:8.1f}s {ea[i]['box']} {ea[i]['text'][:28]!r}"
                      for i in sorted(set(range(len(ea))) - set(pa))])
        show("只在 B", [f"{eb[j]['t_start']/1e6:8.1f}s {eb[j]['box']} {eb[j]['text'][:28]!r}"
                      for j in sorted(set(range(len(eb))) - set(pb))])
        show("文本差", [f"{ea[i]['t_start']/1e6:8.1f}s {ea[i]['box']} "
                      f"{ea[i]['text'][:22]!r} -> {eb[j]['text'][:22]!r}"
                      for i, j in sorted(pa.items()) if ea[i]["text"] != eb[j]["text"]])
        show("挪 >1s", [f"{ea[i]['t_start']/1e6:8.1f}s {ea[i]['box']} "
                       f"首 {(eb[j]['t_start']-ea[i]['t_start'])/1e6:+.1f}s "
                       f"尾 {(eb[j]['t_end']-ea[i]['t_end'])/1e6:+.1f}s {ea[i]['text'][:20]!r}"
                       for i, j in sorted(pa.items())
                       if max(abs(ea[i]['t_start']-eb[j]['t_start']),
                              abs(ea[i]['t_end']-eb[j]['t_end'])) > 1e6])
print("\n读法：**A/A 和 B/B 是底噪**（同一条命令跑两遍），A/B 要减掉底噪才谈得上「效果」。"
      "\n『碎』= 一侧的一个事件压着另一侧 ≥2 个（误并 / 碎裂）；『首差/尾差』= 挪动超过半个采样间隔的配对数。")
