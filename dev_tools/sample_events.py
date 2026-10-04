"""**上屏事件的真值抽样**：随机抽时刻，把产物画到帧上，供人逐帧对照标注。

存在的理由（methodology-audit-4 报告 §2/§5，audit4-response 报告的"没做"第 1 条）：
到目前为止所有"召回/漏条"的数都是**认领覆盖**——剧本行有没有被我们的输出认领。
剧本是"该说什么"的清单，**不是"屏幕上何时出现过什么"的清单**：它没有播放事件、
没有时间、也不知道哪句被重播过。要判"这一处的文字抓到没有"，只能回到画面。

这个工具只做抽样和摆帧，**不做判断**：

* 时刻**均匀随机**抽（`--seed` 固定可复现），**不从已命中的清单里抽**——
  从命中清单里抽只会证明命中的都命中了；
* 每个时刻输出一张画了块的帧 + 一份该时刻产物里所有块的清单；
* 标注的人（或模型）逐帧回答两件事：**屏幕上有几处文字**、**产物报的块对不对**。

产出的标注写回 `--labels` 指向的 JSON，之后可以反复用来算召回/精确，
也可以当回归集——**它是这个项目第一份不依赖剧本的真值**。

用法（要带 cv2 的解释器）：
    <rapidocr 实验的 venv>/Scripts/python.exe dev_tools/sample_events.py \
        out/gs-gi-s2/gi-s2-tracks.json --video D:/.../gi-s2.mp4 \
        --n 8 --seed 1 --outdir tmp/gt/gi-s2
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import tracksio  # noqa: E402

import cv2

COLORS = [(0, 255, 0), (0, 165, 255), (255, 0, 255), (255, 255, 0),
          (0, 0, 255), (255, 128, 0), (128, 0, 255), (0, 255, 255)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracks")
    ap.add_argument("--video", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--n", type=int, default=8, help="抽几个时刻")
    ap.add_argument("--seed", type=int, default=0, help="固定它才能复现同一批样本")
    ap.add_argument("--margin", type=float, default=5.0,
                    help="首尾各留几秒不抽（片头片尾不代表正片）")
    ap.add_argument("--strata", type=int, default=0,
                    help="**默认 0 = 均匀随机**（旧行为）。>0 时按**同屏块数**分这么多层，"
                         "每层等额抽——audit-4 §5 要的『按画面类型分层抽』。"
                         "分层量取自产物本身（那一刻有几个块在屏上），"
                         "**不引入新的人工判断**，所以分层口径也能复现："
                         "对话场景块少、菜单/战斗块多。每个样本的层号写进 manifest，"
                         "召回率可以分层报——不分层的话样本比例是随机撞出来的，"
                         "不是设计的（现有那 11 帧就是这个问题）")
    ap.add_argument("--grid", type=float, default=1.0,
                    help="分层时按几秒一个候选时刻扫描")
    ap.add_argument("--from-manifest", default="",
                    help="**不重新抽，照抄这份 manifest 的时刻**（连层号一起），只把块换成这份产物的。"
                         "给「同一批时刻、换了产物」重新摆帧用：`label_n_onscreen` 是屏幕的属性、"
                         "和产物无关，所以旧标注里那一栏可以直接复用；`label_missed` / `label_spurious` "
                         "是对产物说的，要按新产物重标（2026-09-17，reuse-budget 计划 §8 第 3 条）")
    a = ap.parse_args()

    d = tracksio.load(Path(a.tracks))

    d["regions"] = tracksio.regions_with_runs(d)
    runs = [(r["index"], b) for r in d["regions"] for b in r["runs"]]
    if not runs:
        raise SystemExit("产物里一个块都没有")
    t0 = min(b["t_start"] for _, b in runs) / 1e6 + a.margin
    t1 = max(b["t_end"] for _, b in runs) / 1e6 - a.margin
    if t1 <= t0:
        raise SystemExit("可抽样的区间太短")

    rng = random.Random(a.seed)
    strata_info = None
    if a.strata > 1:
        # 候选时刻按同屏块数排序，切成等大的层，每层等额抽
        grid = [t0 + i * a.grid for i in range(int((t1 - t0) / a.grid) + 1)]
        cnt = []
        for t in grid:
            tu = int(t * 1e6)
            cnt.append((sum(1 for _, b in runs if b["t_start"] <= tu < b["t_end"]), t))
        cnt.sort()
        cnt = [c for c in cnt if c[0] > 0]          # 屏上一个块都没有的时刻不抽
        if len(cnt) < a.strata:
            raise SystemExit(f"有块的候选时刻只有 {len(cnt)} 个，不够分 {a.strata} 层")
        size = len(cnt) // a.strata
        times, strata_info = [], []
        for s_i in range(a.strata):
            lo = s_i * size
            hi = len(cnt) if s_i == a.strata - 1 else (s_i + 1) * size
            part = cnt[lo:hi]
            k = a.n // a.strata + (1 if s_i < a.n % a.strata else 0)
            pick = rng.sample(part, min(k, len(part)))
            # **在网格点附近抖一下**：run 的边界都落在采样间隔的整数倍上，
            # 而网格也是整秒——不抖的话样本会系统性地压在边界上（同上那条）。
            times += [(min(max(t + rng.uniform(-a.grid / 2, a.grid / 2), t0), t1), s_i)
                      for _, t in pick]
            strata_info.append({"stratum": s_i, "n_candidates": len(part),
                                "blocks_min": part[0][0], "blocks_max": part[-1][0],
                                "n_sampled": len(pick)})
        times.sort()
    elif a.from_manifest:
        src = json.loads(Path(a.from_manifest).read_text(encoding="utf-8"))
        times = sorted((s["t"], s.get("stratum", -1)) for s in src["samples"])
        print(f"照抄 {a.from_manifest} 的 {len(times)} 个时刻（不重新抽）")
    else:
        times = sorted((rng.uniform(t0, t1), -1) for _ in range(a.n))
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(a.video)
    manifest = {"tracks": str(a.tracks), "video": str(a.video), "seed": a.seed,
                "n": a.n, "strata": a.strata, "strata_info": strata_info,
                "from_manifest": a.from_manifest or None, "samples": []}
    for k, (t, s_i) in enumerate(times):
        t_us = int(t * 1e6)
        # **半开区间 `[t_start, t_end)`**（2026-09-08 修）：`build_runs` 末尾会把
        # `t_end` 往后推一个采样间隔（"末帧也占满一个采样间隔"），所以 `t == t_end`
        # 那一刻画面上其实已经没有它了。用闭区间抽样，正好落在边界上时产物会
        # "多报"一大片——wuwa-s2 有一帧因此报了 47 块而画面上只有 11 处文字，
        # 我差点把它写成"精确率 23%"。**先怀疑评测口径，再怀疑算法。**
        live = [(ri, b) for ri, b in runs if b["t_start"] <= t_us < b["t_end"]]
        live.sort(key=lambda x: (x[1]["box"][1], x[1]["box"][0]))
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            print(f"  [skip] 读不到 {t:.1f}s")
            continue
        clean = outdir / f"{k:02d}-{t:.1f}s-clean.jpg"
        cv2.imwrite(str(clean), frame)
        for ri, b in live:
            x0, y0, x1, y1 = b["box"]
            cv2.rectangle(frame, (x0, y0), (x1, y1), COLORS[ri % len(COLORS)], 2)
        boxed = outdir / f"{k:02d}-{t:.1f}s-boxed.jpg"
        cv2.imwrite(str(boxed), frame)
        manifest["samples"].append({
            "i": k, "t": round(t, 2), "stratum": s_i,
            "clean": clean.name, "boxed": boxed.name,
            "blocks": [{"region": ri, "box": b["box"], "text": b["text"],
                        "conf": b.get("conf")} for ri, b in live],
            # 标注者填这四个字段。**人看或模型看图都算真值**（owner 2026-09-08，
            # 产品方向记录第 12 条），但要记下是谁标的——第二批就漏记过。
            # 口径：一处文字被 det 切成两块**不算漏也不算多**。
            "label_n_onscreen": None,    # 屏幕上实际有几处文字
            "label_missed": None,        # 产物漏了几处
            "label_spurious": None,      # 产物多报了几块（画面上那里没字）
            "labeled_by": None,          # 谁标的（人名 / 模型名 + 日期）
        })
    cap.release()
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"抽了 {len(manifest['samples'])} 个时刻 -> {outdir}")
    for s in manifest["samples"]:
        print(f"  [{s['i']:02d}] 层{s['stratum']:>2} t={s['t']:8.1f}s  "
              f"产物报 {len(s['blocks']):>2} 块  {s['boxed']}")
    print("\n**清单和帧都在了；填 manifest.json 里的三个 label_ 字段就是真值。**"
          "\n注意：抽样是均匀随机的，**不是从已命中的清单里抽**——"
          "从命中清单里抽只会证明命中的都命中了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
