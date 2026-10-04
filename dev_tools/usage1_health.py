"""**用途 1 的产物体检**：只看产物，不看参照——`track_health.py` 的 overlay 版。

`track_health.py` 量的是"这条轨当字幕好不好用"（条/h、时长中位、逐字重复）。
用途 1 要的是**另外四件事**（产品方向记录）：一块 = 屏幕上一处文字、
bbox 要准（拿来排版）、多块同屏时要有阅读顺序、块在存续期内位置要稳。
这四项**一次都没量过**（gamestream-first-look 报告 §7），这个工具补上。

量什么、怎么读：

* **同屏块数**：overlay 一次要贴几块。中位 1–2 说明画面上就一处文字；
  P90 很高说明是满屏 UI 的场景（菜单、探索态）。
* **框抖动**：`tracks.json` 里一条 run 只有**一个** box，等于假设"这段时间文字不动"。
  拿原始 obs 里同一条 run 存续期内的观测框回算标准差，就知道这个假设站不站得住。
  判据用**相对字高**（无量纲，owner 指示 7）：抖动 > 0.5 倍字高的块，
  贴回原位时会明显错位。
* **同屏重叠**：两块的框相交，overlay 会互相压住。
* **阅读顺序**：同屏块按 (y, x) 排序时，有多少对块的 y 区间互相重叠——
  那些对的先后是**没有定义**的（并排的两块），要由上层决定。
* **JSON vs SRT**：常驻 UI 剔除只作用在**写 SRT** 那一步，`tracks.json` 的 runs
  是未过滤的（读代码确认：`write_srt()` 里过滤，JSON 直接从 `rs` 出）。
  所以**用途 1 的产物是 JSON**——带 bbox、没被用途 2 的规则筛过；
  "逐区域 SRT 是完整文字产物"这个理解不成立（methodology-audit-4 报告 C6）。
  这里把两边的数并排打出来，供对照。

用法：
    python dev_tools/usage1_health.py out/gs-gi-s2/gi-s2-tracks.json \
        --obs out/gamestream/gi-s2.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import tracksio  # noqa: E402
from flowocr.artifacts import evalkit  # noqa: E402
from flowocr.artifacts import srtio  # noqa: E402

JITTER_HI = 0.5
"""抖动超过这么多倍字高就算"框跟不住文字"。"""

def norm_txt(t: str) -> str:
    """比框的时候只关心"是不是同一段文字"，空白和换行不算。"""
    return "".join(t.split())


def iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def y_overlap(a, b) -> bool:
    return not (a[3] <= b[1] or b[3] <= a[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracks", help="build_tracks 产的 *-tracks.json")
    ap.add_argument("--obs", default=None,
                    help="原始 obs jsonl。给了才能量框抖动（run 里只存一个框）")
    ap.add_argument("--regions", default="all",
                    help="`all`=全部区域（用途 1 的口径）；`main`=只看主轨")
    ap.add_argument("--sample-us", type=int, default=500_000,
                    help="同屏块数按这个步长采样（默认 0.5s，和 2fps 采样对齐）")
    a = ap.parse_args()

    d = tracksio.load(Path(a.tracks))

    d["regions"] = tracksio.regions_with_runs(d)
    W, H = d["size"]
    regs = d["regions"]
    if a.regions == "main" and regs:
        regs = [max(regs, key=lambda r: r["primary_score"])]
    runs = [(r["index"], b) for r in regs for b in r["runs"]]
    evalkit.require_nonzero(len(runs), "产物里的块（run）")
    span = max(b["t_end"] for _, b in runs) - min(b["t_start"] for _, b in runs)
    print(f"{Path(a.tracks).name}：{len(regs)} 个区域、**{len(runs)} 块**、"
          f"跨度 {span/1e6/60:.1f} 分钟（{W}x{H}）")

    # ---- 同屏块数 / 重叠 / 阅读顺序 ----
    t0 = min(b["t_start"] for _, b in runs)
    t1 = max(b["t_end"] for _, b in runs)
    counts, n_ovl, n_pairs, n_ambig = [], 0, 0, 0
    t = t0
    while t <= t1:
        live = [b["box"] for _, b in runs if b["t_start"] <= t <= b["t_end"]]
        counts.append(len(live))
        for i in range(len(live)):
            for j in range(i + 1, len(live)):
                n_pairs += 1
                if overlaps(live[i], live[j]):
                    n_ovl += 1
                if y_overlap(live[i], live[j]):
                    n_ambig += 1
        t += a.sample_us
    counts.sort()
    p90 = counts[int(len(counts) * 0.9)] if counts else 0
    print(f"\n**同屏块数**：中位 {st.median(counts):.0f}、P90 {p90}、最多 {max(counts)}"
          f"（{len(counts)} 个采样时刻）")
    print(f"**同屏重叠**：{evalkit.denom(n_ovl, n_pairs)} 的同屏块对框相交"
          f"——overlay 会互相压住")
    print(f"**阅读顺序不定**：{evalkit.denom(n_ambig, n_pairs)} 的同屏块对 y 区间重叠"
          f"（并排的两块，按 (y,x) 排也定不出先后，要上层决定）")

    # ---- 框抖动：run 只有一个框，拿 obs 回算 ----
    if a.obs:
        rows = [json.loads(l) for l in Path(a.obs).read_text(encoding="utf-8").splitlines()
                if l.strip()]
        obs = [o for o in rows if "_meta" not in o]
        obs.sort(key=lambda o: o["t_us"])
        jit, moved = [], 0
        for _, b in runs:
            box, h = b["box"], max(1, b["box"][3] - b["box"][1])
            # **光靠几何关联会把"同一处换了一句话"算成抖动**：字幕带上前后两句长度
            # 不同，x1 差几百像素，量出来是框在动，其实是内容换了。所以**必须同时
            # 要求文本一致**——问的是"同一段文字的框动没动"。
            want = norm_txt(b["text"])
            near = [o for o in obs
                    if b["t_start"] <= o["t_us"] <= b["t_end"] and iou(o["box"], box) > 0.3
                    and SequenceMatcher(None, want, norm_txt(o["text"])).ratio() >= 0.8]
            if len(near) < 3:
                continue
            # **纵向和横向要分开报。** 实测抖动几乎全在右边缘（x1 的 sd 51–102 px，
            # 而 y0/y1 只有 0–2 px）——那是**打字机在往右长**，不是块在动
            # （text-effects 报告的三态就是它）。把两者混在一起报，
            # 会把"文字正在打出来"说成"框跟不住文字"（我第一版就是这么报的）。
            sd_y = max(st.pstdev([o["box"][k] for o in near]) for k in (1, 3))
            sd_x = max(st.pstdev([o["box"][k] for o in near]) for k in (0, 2))
            jit.append((sd_y / h, sd_x / h))
            if sd_y / h > JITTER_HI:
                moved += 1
        if jit:
            ys = sorted(j[0] for j in jit)
            xs = sorted(j[1] for j in jit)
            print(f"\n**框稳定性**（相对字高，{len(jit)} 块有 ≥3 个同文本观测）：")
            print(f"  **纵向**（真·位移的信号）中位 {st.median(ys):.2f}、"
                  f"P90 {ys[int(len(ys)*0.9)]:.2f}；"
                  f"超过 {JITTER_HI} 倍字高的 {evalkit.denom(moved, len(jit))}"
                  f" —— 这些块『一段一个框』不成立，贴回去会错位")
            print(f"  横向（多半是打字机在往右长，**不算缺陷**）中位 {st.median(xs):.2f}、"
                  f"P90 {xs[int(len(xs)*0.9)]:.2f}")
        else:
            print("\n**框稳定性**：没有块凑够 3 个同文本观测，量不了")

    # ---- 在场密度 + 可疑块 ----
    # 在场密度 = n_obs / 期望采样数。低于 0.5 说明这条 run 的时间区间盖住了它
    # **不在屏幕上**的时刻——overlay 会往空处贴译文。实测这批素材中位 0.67、
    # 最低就是单次观测的下限 0.50，**没有跨缺席的块**；留着这一项是为了换素材时能发现。
    fu = d.get("frame_us") or 500_000
    dens = [b["n_obs"] / ((b["t_end"] - b["t_start"]) / fu + 1) for _, b in runs]
    thin = sum(1 for x in dens if x < 0.5)
    print(f"\n**在场密度**（n_obs / 期望采样数）中位 {st.median(dens):.2f}；"
          f"低于 0.5 的 {evalkit.denom(thin, len(dens))}"
          f" —— 低密度块的时间区间盖住了它不在屏幕上的时刻")
    # 可疑块：**只报分布，不下判断**。误识的小图标是这批素材里真实存在的一类
    # （`F` / `D` / `O` 这种，nameplate.py 的 docstring 记过 `大`/`米`/`è`）。
    lowc = sum(1 for _, b in runs if b.get("conf", 1.0) < 0.6)
    tiny = sum(1 for _, b in runs if len(norm_txt(b["text"])) <= 1)
    print(f"**可疑块**：conf < 0.6 的 {evalkit.denom(lowc, len(runs))}；"
          f"只有 1 个字的 {evalkit.denom(tiny, len(runs))}"
          f"\n  （overlay 会把这些也贴上去；用途 1 要不要滤、按什么滤，是产品取舍，"
          f"这里只报数）")

    # ---- JSON 和 SRT 是两个产物：过滤只作用在 SRT ----
    # 判据不在这里重写（`drop_persistent_ui` 是唯一实现）；这里只比**真实产物**。
    tag = Path(a.tracks).name.removesuffix("-tracks.json")
    srt_cues = 0
    found = 0
    for r in regs:
        p = Path(a.tracks).parent / f"{tag}-region{r['index']:02d}-{r['label']}.srt"
        if p.exists():
            found += 1
            srt_cues += srtio.count_cues(p)
    if found:
        print(f"\n**JSON 与 SRT 是两个产物**：JSON {len(runs)} 块（**不过滤**）"
              f" vs {found} 个区域 SRT 合计 {srt_cues} 条 cue（**过了常驻 UI 过滤**）。")
        print("  用途 1 要的是画面上所有文字，**产物是 `tracks.json` 的 runs**——"
              "带 bbox，而且没被过滤；\n  逐区域 SRT 既没有位置、又被用途 2 的规则"
              "筛过一遍（methodology-audit-4 报告 C6）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
