"""一条轨的"体检"：几个不看参照就能算的数，专门用来发现**产物形状不对**。

存在的理由（head2head-5films 报告）：`input4` 的主轨出来 20,108 条、
**64% 是上一条的逐字重复**，而头对头的召回分**反而更高**（碎片化给了匹配器更多次机会）。
也就是说：**下游指标不会告诉你产物坏了**，得有一把只看产物自己的尺。

四个数，都不需要参照：

* **条/h** —— 同一类素材之间应该在一个量级。yuka 五部正常是 570–670；
  坏掉的 input4 是 2,611。
* **cue 时长中位** —— 正常 3.5–5.0 s；坏掉的是 1.0 s。
* **逐字重复率** —— 与上一条文本相同且时间相接的占比。正常 1–3%；坏掉的是 64%。
  这一项最灵敏，而且**只看产物**。
* **常驻短行占比** —— 最常见的那个 ≤6 字独立行占了多少条 cue。
  `Auto` 在 input3 的主轨里占 97%（见 `build_tracks.drop_persistent_ui`）。

用法：python dev_tools/track_health.py 轨.srt [轨.srt ...] [--hours 9.2]
"""
from __future__ import annotations

import statistics as st
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import srtio  # noqa: E402

DUP_GAP_S = 0.6          # 两条之间的空隙小于这么多秒才算"相接"


def health(path: str | Path, hours: float | None = None) -> dict:
    cues = srtio.read_srt(path, sort=True)
    n = len(cues)
    if n == 0:
        return {"path": str(path), "n": 0}
    dup = sum(1 for a, b in zip(cues, cues[1:])
              if a.text("") == b.text("") and b.start - a.end < DUP_GAP_S)
    short: Counter[str] = Counter()
    for c in cues:
        short.update({l.strip() for l in c.lines if 0 < len(l.strip()) <= 6})
    top = short.most_common(1)[0] if short else ("", 0)
    span = (cues[-1].end - cues[0].start) / 3600
    return {"path": str(path), "n": n,
            "per_h": n / (hours if hours else max(span, 1e-9)),
            "dur_med": st.median(c.dur for c in cues),
            "dup": dup / n,
            "top_short": top[0], "top_share": top[1] / n,
            "span_h": span}


UI_SHARE = 0.5
"""`build_tracks.UI_SHARE`（判常驻 UI 的占比门）**在这里复读一份**，守卫钉住两边相等
（`tests/guards/analyze.py::t_ui_alarm_gate`）。

为什么不 import：`track_health` 是"只看产物"的轻量工具，为一个常量拖进 `build_tracks`
（它带 numpy / SequenceMatcher 一串）不划算（2026-09-19 复审第 4 条）。
⚠ 真正稳的做法是**从产物的 provenance 里读那一趟实际用的 `--ui-share`**——
但这个工具的入参是 SRT、看不到 `tracks.json`，要改得先改入参。记在 ui-gate 计划 §11.4。"""
UI_NEAR = 0.40
"""报警的门，**故意比剔除的门低**：落在 [UI_NEAR, UI_SHARE) 的短行是最坏的一档——
剔除够不着它，它却已经混进正文里了（见 main 里那段注释）。

0.40 的依据**只有一部片子上的两个点**（e-yuka-f5：可接受的两条臂是 `大` 34%、坏的那条是 `Auto` 49.2%），
取中间让两边各留 5~6 个点。它只是**报警**、不改产物，所以这点证据够用；
真要把它当判据得先在多段异构素材上画分布（ui-gate 计划 §5）。"""


def main(paths: list[str], hours: float | None) -> int:
    print(f"{'轨':<46}{'条':>7}{'条/h':>7}{'时长中位':>9}{'逐字重复':>9}   最常见短行")
    bad = 0
    for p in paths:
        h = health(p, hours)
        if not h["n"]:
            print(f"{Path(p).name:<46}{'空':>7}")
            continue
        flag = ""
        if h["dup"] >= 0.10:
            flag = "  ← **重复率异常**"
            bad += 1
        # 常驻短行也要会响：`Auto` 在 input3 主轨里占 97%，四个数里只有重复率报警，
        # 这一项当时只印不响（methodology-audit-2 报告）。
        # ⚠ **报警的门必须低于剔除的门**（2026-09-19 实测踩到）：原来两边都是 0.5
        # （`build_tracks.persistent_ui_lines` 的 `min_share`），于是"差一点没被剔掉"的那一档
        # **既不剔、也不报**——e-yuka-f5 上组批臂的 `Auto` 落在 **49.2%**，
        # 剔除没生效、报警也没响，而它污染了 293/595 条主轨 cue，用途 2 的命中直接 158 → 0。
        # 这和 `scriptmatch` 那个"可疑改写的门取了 --fuzzy、于是恒为 0"是同一个形状：
        # **指标的门不能等于接受判据的门。**
        elif h["top_share"] >= UI_SHARE:
            flag = f"  ← **{h['top_short']!r} 占了半数以上的 cue，像常驻 UI**"
            bad += 1
        elif h["top_share"] >= UI_NEAR:
            flag = (f"  ← **{h['top_short']!r} 占 {h['top_share']:.0%}，"
                    f"**贴着剔除门（{UI_SHARE:.0%}）却没被剔**")
            bad += 1
        print(f"{Path(p).name:<46}{h['n']:>7}{h['per_h']:>7.0f}{h['dur_med']:>8.1f}s"
              f"{h['dup']:>8.0%}   {h['top_short']!r} {h['top_share']:.0%}{flag}")
    if not hours:
        print("（**条/h 的分母是这条轨自己的首尾跨度**，不是片长——"
              "文档里报数请给 `--hours`，两个口径差几个百分点）")
    if bad:
        print(f"\n**{bad} 条轨触发了报警。** 逐字重复率正常值是 1–3%；"
              f"64% 那次的病因是 `gap_frames` 容差没留余量（见 build_runs 的注释）。"
              f"注意**下游召回分不会跌**，反而可能涨——碎片化给了匹配器更多次机会。")
    return 0


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--hours"]
    hrs = None
    if "--hours" in sys.argv:
        i = sys.argv.index("--hours")
        hrs = float(sys.argv[i + 1])
        a = [x for x in a if x != sys.argv[i + 1]]
    if not a:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(a, hrs))
