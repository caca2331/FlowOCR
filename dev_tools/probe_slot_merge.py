"""**探针**：某一类 run 是在**哪一层**被并进大区域的——行级 / 窗内 / 跨窗缝合。

目标那一类怎么挑：`--names` 给名表（原始用途，名牌），或 `--target-regex`
给正则（游戏直播那批没有名表——gi-s1 的键位提示就是 `^[A-Za-z]$`）。
**两者都只用来打标签，不进修法。**

来历（methodology-audit-2 报告 §3）：`probe_nameplate_geom.py` 在 f1/f2 上
能靠几何把名牌区域挑出来（精确 61%、召回 71–74%），**f3/f4/f5 上挑中 0 个**——
三部的失败形状一样：名牌本该在 `cy≈0.72`、`cx` 几乎不动，
却落在一个 `cy` 中位 0.51–0.56、`cx_std` 0.19 的巨型混合区域里。

那时的说法是"聚类没把名牌带分出来"，**但没说是哪一层**。
docs/dev-guide/pitfalls.md「"某个旋钮无效"不能反推病灶在哪」对这种情况有一条现成的规矩（2026-09-04 那次缝合层事故留下的）：

> **"某个旋钮无效"不能反推病灶在哪。把每一层的产物分别数一遍，
> 两个数一摆就知道是哪层塌了。**

所以这里就只做一件事：拿名表给每条 run 打上"是不是名字"，
然后在三层产物上数**同一个量**——

    名牌纯度 = 落在"名字占比 >= --pure 的组"里的名字 run 数 / 全片名字 run 数

三层分别是：

  1. **行级** `build_tracks`（窗内，质心分配）
  2. **窗内区域** `build_regions`（窗内，并查集——传递性在这里咬过人）
  3. **跨窗槽位** `stitch_slots`（贪心 + 收敛后再合并一轮）

哪一层的数掉下去，病灶就在哪一层。**名表只用来打标签，不进修法。**

用法（几分钟，整片；--window 只用于快速试跑，结论不能从窗口外推）：
    python dev_tools/probe_slot_merge.py out/yuka/i3-full.jsonl \
        --names tmp/match/Text/CharacterNames.bytes
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.analyze import build_tracks as bt  # noqa: E402
from flowocr.artifacts import evalkit  # noqa: E402
from probe_nameplate_geom import is_name, load_names  # noqa: E402


def purity(groups: list[list[int]], flag: list[bool], pure: float) -> tuple[int, int, float]:
    """返回 (落在"名字占比 >= pure"的组里的名字 run 数, 纯名牌组数, 名字所在组的占比中位)。

    **按 run 数，不按组数。** 三层的组大小差着两个数量级——行级组在一个 60 秒窗里
    只有几条 run，槽位是全片的——拿"多少个组是纯的"跨层比会得出假结论
    （层 1 必然赢，因为它的组小）。按 run 数就没有这个尺度问题。
    """
    got = n_grp = 0
    shares: list[float] = []
    for g in groups:
        k = sum(1 for i in g if flag[i])
        if not k:
            continue
        s = k / len(g)
        shares += [s] * k
        if s >= pure:
            got += k
            n_grp += 1
    return got, n_grp, st.median(shares) if shares else 0.0


def geom_str(runs, ids, W: int, H: int) -> str:
    """cx_std 按 W 归一化——要和 probe_nameplate_geom 的那一列直接对着看。"""
    rs = [runs[i] for i in ids]
    cy = st.median(r.cy for r in rs) / H
    cxs = [r.cx / W for r in rs]
    return (f"run {len(ids):>6}  cy={cy:.3f}  h={st.median(r.h for r in rs):>5.0f}"
            f"  cx_std={(st.pstdev(cxs) if len(cxs) > 1 else 0.0):.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obs")
    ap.add_argument("--names", help="角色名表（**只用来打标签**）。"
                    "游戏直播那批素材没有名表，用 --target-regex")
    ap.add_argument("--target-regex", help="用正则挑目标 run（和 --names 二选一）。"
                    "**只用来打标签，不进修法**。"
                    "例：gi-s1 的键位提示 `--target-regex '^[A-Za-z]$'`")
    ap.add_argument("--target-name", default=None,
                    help="打印时管这类 run 叫什么（默认名表 -> 名牌、正则 -> 目标）")
    ap.add_argument("--pure", type=float, default=0.5,
                    help="组里名字 run 占比达到这个才算『名牌被单独分出来了』")
    ap.add_argument("--window-sec", type=float, default=60.0)
    ap.add_argument("--iou", type=float, default=0.35)
    ap.add_argument("--sim", type=float, default=0.55)
    ap.add_argument("--gap-frames", type=int, default=1)
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--mutual-x", action="store_true")
    ap.add_argument("--no-slot-anchor", action="store_true")
    ap.add_argument("--slot-geom", choices=("pooled", "median"), default="pooled",
                    help="槽位签名怎么算（build_tracks 的同名旋钮）")
    ap.add_argument("--slot-mutual-x", action="store_true",
                    help="缝合层的 x 判据除以较宽的那个（build_tracks 的同名旋钮）")
    ap.add_argument("--slot-span-ratio", type=float, default=bt.SLOT_SPAN_RATIO,
                    help="槽位和候选的 y 跨度最多差几倍（build_tracks 的同名旋钮）")
    ap.add_argument("--window", type=float, nargs=2, default=None, metavar=("T0", "T1"),
                    help="只跑这一段（秒）——**试跑用**，60 秒的结论不能外推到整片")
    a = ap.parse_args()

    lines = Path(a.obs).read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])["_meta"]
    obs = [json.loads(x) for x in lines[1:] if x.strip()]
    obs = [o for o in obs if o["conf"] >= a.min_conf]
    if a.window:
        obs = [o for o in obs if a.window[0] * 1e6 <= o["t_us"] <= a.window[1] * 1e6]
    evalkit.require_nonzero(len(obs), "满足置信度的观测")
    W, H = meta.get("width"), meta.get("height")
    if not W:
        W, H = bt.probe_size(meta["video"])
    frame_us = int(round(1e6 / meta["sample_fps"]))

    runs = bt.build_runs(obs, frame_us, a.iou, a.sim, a.gap_frames)
    if bool(a.names) == bool(a.target_regex):
        raise SystemExit("--names 和 --target-regex **二选一**（都给或都不给都不行）")
    if a.names:
        names = load_names(Path(a.names))
        flag = [is_name(r.text, names) for r in runs]
        what = a.target_name or "名牌"
        hint = "名表和这部素材对不上？先确认 --names 是这个游戏的。"
    else:
        rx = re.compile(a.target_regex)
        flag = [bool(rx.search(r.text)) for r in runs]
        what = a.target_name or "目标"
        hint = "正则一条 run 都没挑中——先拿 SRT 确认这类文本长什么样。"
    tot = sum(flag)
    print(f"{Path(a.obs).name}: {len(obs)} obs -> {len(runs)} runs，"
          f"其中像{what}的 **{evalkit.denom(tot, len(runs))}**")
    evalkit.require_nonzero(tot, f"像{what}的 run", hint)

    window_us = int(a.window_sec * 1e6)
    tracks, win_regions = bt.cluster_windowed(runs, W, H, window_us, a.mutual_x)
    slots = bt.stitch_slots(runs, win_regions, W, H, not a.no_slot_anchor,
                            a.slot_mutual_x, a.slot_span_ratio, a.slot_geom)

    layers = [
        ("1 行级 build_tracks（窗内，质心）", tracks),
        ("2 窗内区域 build_regions（并查集）", [w["runs"] for w in win_regions]),
        ("3 跨窗槽位 stitch_slots（贪心）",
         [sorted({i for w in sl for i in win_regions[w]["runs"]}) for sl in slots]),
    ]
    print(f"\n**{what}纯度**：{what} run 落在『{what}占比 >= {a.pure:.0%} 的组』里的比例")
    print(f"  {'层':<34}{'组数':>7}{'平均组':>7}{'纯' + what + '组':>10}"
          f"{what + '落在纯组里':>18}{'所在组' + what + '占比中位':>20}")
    prev = None
    for name, groups in layers:
        got, n_grp, med = purity(groups, flag, a.pure)
        avg = st.mean(len(g) for g in groups) if groups else 0.0
        drop = "" if prev is None else f"   <- 比上一层 {got - prev:+d}"
        print(f"  {name:<34}{len(groups):>7}{avg:>7.1f}{n_grp:>10}"
              f"{evalkit.denom(got, tot):>18}{med:>20.2f}{drop}")
        prev = got
    # **这个量随组数单调，引用时必须连组数一起报**（methodology-audit-3 报告 §2）：
    # 组越小越容易过 50% 那道线——一个只含 1 条名字 run 的组是 100% 纯的，
    # 于是"每条 run 单独成组"这种退化聚类在这把尺上得满分。
    # 所以层 1/2 的高分**不能读成"名牌带被分出来了"**，只能读成"名字 run 倾向于扎堆"；
    # 层间只有**掉下去的那一层**是硬结论（它不可能是碎片化造成的）。
    print(f"  ⚠ **纯度随组数单调**：上表层 1 有 {len(layers[0][1])} 组、"
          f"层 3 只有 {len(layers[-1][1])} 组，两者不是同一个粒度。"
          f"\n    高分只说明『{what} run 和 {what} run 扎堆』，**不等于{what}带被聚成了一个区域**；"
          f"\n    真正硬的是**掉下去的那一层**——碎片化只会让这个数变高，不会让它变低。")

    # 最后一层里"吃掉目标最多的那个不纯的组"——病灶就在它身上
    final = layers[-1][1]
    bad = max(range(len(final)),
              key=lambda k: sum(1 for i in final[k] if flag[i])
              if sum(1 for i in final[k] if flag[i]) / len(final[k]) < a.pure else -1)
    ids = final[bad]
    k = sum(1 for i in ids if flag[i])
    print(f"\n吃掉{what}最多的**混合槽位** #{bad}：{geom_str(runs, ids, W, H)}"
          f"  {what} {evalkit.denom(k, len(ids))}")

    # 它是由哪些窗区域拼起来的：按"这个窗区域是不是目标"分开看
    members = [w for sl in [slots[bad]] for w in sl] if bad < len(slots) else []
    pure_w = [w for w in members
              if sum(1 for i in win_regions[w]["runs"] if flag[i]) / len(win_regions[w]["runs"]) >= a.pure]
    dirty_w = [w for w in members if w not in set(pure_w)]
    print(f"  成员窗区域 {len(members)} 个：其中**本身就是{what}的** {len(pure_w)} 个、"
          f"其余 {len(dirty_w)} 个")
    if pure_w and dirty_w:
        gp = bt.region_geom(runs, [i for w in pure_w for i in win_regions[w]["runs"]])
        gd = bt.region_geom(runs, [i for w in dirty_w for i in win_regions[w]["runs"]])
        print(f"    {what}那半  cx={gp[0]:.0f} x=[{gp[1]:.0f},{gp[2]:.0f}] "
              f"y=[{gp[3]:.0f},{gp[4]:.0f}] h={gp[5]:.0f}")
        print(f"    其余那半  cx={gd[0]:.0f} x=[{gd[1]:.0f},{gd[2]:.0f}] "
              f"y=[{gd[3]:.0f},{gd[4]:.0f}] h={gd[5]:.0f}")
        direct = bt.slot_fits(gp, gd, W, not a.no_slot_anchor, a.slot_mutual_x,
                              a.slot_span_ratio)
        print(f"    **两半直接比**（slot_fits）：{'不匹配（说明是质心漂移之后才并上的）' if direct is None else f'匹配 yo={direct:.2f}——判据本身就拦不住'}")
    # 名牌到底散在几个槽位里
    spread = {}
    for gi, g in enumerate(final):
        c = sum(1 for i in g if flag[i])
        if c:
            spread[gi] = c
    top = sorted(spread.items(), key=lambda x: -x[1])[:6]
    print(f"\n  {what} run 散在 {len(spread)} 个槽位里，最大的几个："
          + "、".join(f"#{g}:{c}" for g, c in top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
