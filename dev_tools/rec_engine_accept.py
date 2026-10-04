"""换 rec 推理引擎的**臂验收**：文本分栏（同 `arm_vs_truth` 的判据）+ 用途 2 的命中集合差。

为什么不能只报"文本 98.7% 相同"（`obs_textdiff.py` 那个数）：那是**全部行**的相同率，
分母里绝大多数是低分乱码 / 短行，而下游 `build_tracks` 的门是 `conf >= 0.5`、
用途 2 看的是"剧本行有没有被读到"。09-17 审计的第四种形状正是这个：
**报一个比例的时候，要连它筛掉了什么一起报**。所以这里按 `arm_vs_truth.diff` 的分栏数
（`sub` 严重不一致 / `near` 不同但相似 / `tw` 打字机前缀 / `lowconf` / `short`），
并且单独数**跨过 conf 0.5 那道门的行**——那才是下游看得见的。

⚠ 这里的"真值"是**默认臂**（写这个工具时默认是 Paddle rec；2026-10 去掉 Paddle 之后，拿它比两条 ORT 臂也照样用），
不是 `--no-reuse` 那个复用真值：问的问题是"换引擎 / 换臂改了什么"，不是"复用坏了什么"。两个问题的分母不同，别混读。

usage: python dev_tools/rec_engine_accept.py <seg id…> [--arm -ort] [--dir out/samples] [--show 4]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "dev_tools"))
sys.path.insert(0, str(CODE / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from flowocr import paths  # noqa: E402
os.chdir(paths.data_root())
import textkinds  # noqa: E402   判据只有一份（arm_vs_truth 也 import 它）

ap = argparse.ArgumentParser()
ap.add_argument("segs", nargs="+")
ap.add_argument("--arm", default="-ortw1")
ap.add_argument("--base", default="-w1",
                help="参照臂的后缀（**空串 = 默认臂**）。**两条臂的 `--workers` 必须相同**："
                     "`--workers` 会在切点处重置链，产物本来就差几行，两个变量混在一起就分不清是谁改的。"
                     "比单进程的 ORT 臂用 -w1；比 3 worker 的组批臂要显式给空串（2026-09-19 这里踩过）")
ap.add_argument("--dir", default="out/samples")
ap.add_argument("--show", type=int, default=4)
a = ap.parse_args()


def rows(p: Path) -> tuple[dict, dict]:
    meta, out = {}, {}
    with p.open(encoding="utf-8") as fh:
        for ln in fh:
            o = json.loads(ln)
            if "_meta" in o:
                meta = o["_meta"]
                continue
            out[(o["frame"], tuple(o["box"]))] = o
    return meta, out


print(f"{'段':<20}{'行(默认/臂)':>16}{'配上':>7}{'文本同':>8}{'严重':>6}{'相似':>6}"
      f"{'打字机':>7}{'低分':>6}{'短':>4}{'过conf门':>10}{'rec(送进去)':>15}{'墙钟 s':>14}")
tot = Counter()
for seg in a.segs:
    pa, pb = Path(a.dir) / f"{seg}{a.base}.jsonl", Path(a.dir) / f"{seg}{a.arm}.jsonl"
    if not pa.exists() or not pb.exists():
        print(f"{seg:<20}  ⚠ 缺产物：{pa if not pa.exists() else pb}")
        continue
    ma, ra = rows(pa)
    mb, rb = rows(pb)
    for m, p in ((ma, pa), (mb, pb)):
        if not m.get("complete"):
            raise SystemExit(f"{p} 没跑完（_meta.complete 不为真）——臂验收不能拿半份产物比")
    common = ra.keys() & rb.keys()
    kinds = Counter()
    gate = 0
    shown = 0
    for k in common:
        x, y = ra[k], rb[k]
        d = textkinds.diff(y, x)                       # new = 臂, old = 默认（顺序同 sim_policy）
        kinds[d or "same"] += 1
        if ((x.get("conf") or 0) >= 0.5) != ((y.get("conf") or 0) >= 0.5):
            gate += 1
        if d in ("sub", "near") and shown < a.show:
            shown += 1
            print(f"    {'严重' if d == 'sub' else '相似'} 帧 {k[0]} conf {x['conf']:.2f}/{y['conf']:.2f}"
                  f"  {a.base or '默认'} {x['text'][:26]!r}  {a.arm} {y['text'][:26]!r}")
    print(f"{seg:<20}{f'{len(ra)}/{len(rb)}':>16}{len(common):>7}"
          f"{f'{kinds['same'] / max(1, len(common)):.2%}':>8}"
          f"{kinds['sub']:>6}{kinds['near']:>6}{kinds['tw']:>7}{kinds['lowconf']:>6}{kinds['short']:>4}"
          f"{gate:>10}{f'{ma["rec_calls"]}/{mb["rec_calls"]}':>15}"
          f"{f'{ma.get("wall_sec", 0):.1f}/{mb.get("wall_sec", 0):.1f}':>14}")
    for kk, vv in kinds.items():
        tot[kk] += vv
    tot["common"] += len(common)
    tot["gate"] += gate
print(f"\n合计：配上 {tot['common']} 行，文本相同 {tot['same'] / max(1, tot['common']):.3%}；"
      f"**严重不一致 {tot['sub']}、相似 {tot['near']}、打字机 {tot['tw']}**，"
      f"低分 {tot['lowconf']}、短 {tot['short']}；跨 conf 0.5 门 {tot['gate']}")
print("⚠ 这几栏只说文本，**说明不了漏条 / 误并 / 幽灵事件**——那要用途 2 的命中集合差（另一步）。")
