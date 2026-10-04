"""两条 A/B 臂的**命中集合差**，按内容字数分三档。

存在的理由（methodology-audit-3 报告 §1）：docs/dev-guide/verification.md「有原文剧本时：三档、命中集合、重复认领」 立过一条对的规矩——
三档分类**不能跨臂比**，因为 `gap_short + gap_long ≡ dialogue_entries − dialogue_hit`，
周围条目被匹配上之后，长 gap 里的漏条会被**重新分桶**成短 gap。
但那条规矩顺手把唯一能看"收益落在哪一档"的口径也关掉了，而没有补替代品。

替代品就是这个：**命中是一个集合**，集合差不经过 gap 分桶，跨臂比是合法的。
而 `script_align` 的 JSON 只写 `dialogue_hit` 这个**数**、逐条清单只有 `missed`，
所以这个差算不出来——这里把它补上。

**默认按归一化文本的多重集求差，不按 key。** `align()` 给同文本的多条剧本条目挑的是
"游标前方第一条"，游标错开一格就会让命中从 keyA 挪到 keyB；按 key 求差会把这种
纯记账变动算成"丢了一条又新增一条"（实测 f1 按 key 是 +108/−42、按文本是 +91/−25，
净值都是 +66）。要看 key 口径用 `--by key`。

判据一律复用 `script_align`（`align` / `is_dialogue` / `read_subs`）和
`evalkit.triviality`，**不在这里重写第二份**——这个项目在"同一条规则抄两份"上
栽过（methodology-audit 报告：5 个工具各写各的 SRT 解析）。

**代价侧一并报：「重复认领」。** 收益（命中）单看会把噪音当成免费的：
任何往轨里多灌条目的改动，在命中这把尺下都显得有效（docs/dev-guide/verification.md「有原文剧本时：三档、命中集合、重复认领」）。
所以这里同时数：输出里某条剧本行出现的次数，比**整部剧本**里它的条数多出多少。
这个判据不依赖游标、不依赖 gap 分桶、不依赖参照的时间轴，只数两个多重集，
而且能在**匹配器输出**上算（`unmatched_subs` 那个"幽灵条目上限"在这里结构上恒为 0，
它量不了任何东西）。

> **它是报警，不是已确认的错误数**（methodology-audit-4 报告 §1 纠正了
> 审计三 §1.5 的措辞）。反例：剧本只有一句、视频真的播了两次（回想/回读/重播——
> 产品目标里明确要求支持），A 抓到一次、B 抓到两次，**两边都没错**，
> 这个数却从 0 变成 1。而且即便每臂的超额数各自是错误的下界，
> **两个下界之差也不是错误增量的下界**。
> 所以：可以说"重复认领涨了多少"，不能换算成"多了多少条确认的错"，
> 也不能用它定位是哪一处错。

用法（**臂名以 `-` 开头，必须写成 `--b=-xxx`**，空格形式会被 argparse 当成选项）：
    python dev_tools/hit_delta.py 1 2 3 4 5 --b=-sr20mb
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import evalkit  # noqa: E402
from flowocr import paths  # noqa: E402
from flowocr.analyze import script_align as SA  # noqa: E402

ROOT = paths.data_root()
"""**素材与产物的根**（worktree 里解析回主 checkout，见 flowocr.paths）。
不是这份代码所在的目录——槽里没有 `data/corpus` 和 `tmp/match`。"""
SCRIPT_RAW = ROOT / "data/corpus/mocai/script-raw/Scripts"
SCRIPT_JP = ROOT / "data/corpus/mocai/script-jp/Scripts"
GT = ROOT / "data/corpus/mocai/game_text"


def reset(script) -> None:
    for e in script:
        e.matched, e.sim = -1, 0.0


def hits(script, subs_path: Path, span, window: int, fuzzy: float) -> tuple[Counter, Counter]:
    """跑一遍游标对齐，返回 (文本多重集, key 计数)。区间由参照字幕圈出，两臂共用。"""
    reset(script)
    SA.align(script, SA.read_subs([subs_path]), window, fuzzy)
    lo, hi = span
    got = [e for i, e in enumerate(script)
           if SA.is_dialogue(e) and lo <= i <= hi and e.matched >= 0]
    return Counter(e.text for e in got), Counter(e.key for e in got)


def overclaim(subs, have: Counter) -> int:
    """输出里比**整部剧本**多出来的条目数——**重复认领报警**，不是已确认的错误数。

    真实重播（回想/回读）也会让它涨，见模块 docstring 里的反例。
    """
    out = Counter(SA.norm(t) for _, t, _ in subs if SA.norm(t))
    return sum((out - have).values())


def classify(c: Counter, text_of: dict[str, str] | None) -> dict[str, int]:
    out = {cls: 0 for cls in evalkit.CLASSES}
    for k, n in c.items():
        out[evalkit.triviality(text_of[k] if text_of else k)] += n
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("films", nargs="+", type=int, help="片号，如 1 2 3 4 5")
    ap.add_argument("--a", default="", help="基准臂的 head2head.sh SUFFIX（空 = 默认臂）")
    ap.add_argument("--b", required=True,
                    help="对比臂的 SUFFIX。**写成 `--b=-sr20mb`**：SUFFIX 以 `-` 开头，"
                         "空格形式 argparse 会当成另一个选项（arm_table 踩过同一个坑）")
    ap.add_argument("--match-dir", default=str(ROOT / "tmp/match"),
                    help="匹配器输出所在目录（ours{N}{SUFFIX}_jp.ass）")
    ap.add_argument("--by", choices=("text", "key"), default="text",
                    help="按归一化文本的多重集求差（默认）还是按剧本 key。"
                         "**key 口径会把同文本条目之间的记账变动算成一增一减**")
    ap.add_argument("--window", type=int, default=400, help="和 script_align 保持一致")
    ap.add_argument("--fuzzy", type=float, default=0.75, help="同上")
    ap.add_argument("--show", type=int, default=5, help="列几条丢失的『有内容』行")
    a = ap.parse_args()

    md = Path(a.match_dir)
    script = SA.load_script(SCRIPT_RAW, SCRIPT_JP)
    text_of = {e.key: e.text for e in script}
    P, T, C = evalkit.CLASSES
    # 两侧都要能写"默认"：把旧默认冻成命名臂之后，**基准臂常常是那条命名臂、
    # 对比臂才是默认**（audit-5 §2 的 `-band0` -> 默认就是这个方向）。
    name_a, name_b = a.a or "默认", a.b or "默认"
    print(f"命中集合差（{a.by} 口径）：**{name_a} -> {name_b}**，分档口径同 evalkit.triviality")
    have = Counter(SA.norm(e.text) for e in script if SA.norm(e.text))
    print(f"{'片':<5}{'基准':>7}{'对比':>7}{'净':>6}{'新增':>6}{'丢失':>6}"
          f"{P:>12}{T:>16}{C:>16}{'重复认领':>12}")
    rows = []
    for n in a.films:
        pa, pb = md / f"ours{n}{a.a}_jp.ass", md / f"ours{n}{a.b}_jp.ass"
        for p in (pa, pb):
            if not p.exists():
                raise SystemExit(f"{p} 不在——那条臂还没跑过？")
        reset(script)
        SA.align(script, SA.read_subs([GT / f"lower{n}_jp.ass"]), a.window, a.fuzzy)
        got = [i for i, e in enumerate(script) if e.matched >= 0]
        evalkit.require_nonzero(len(got), f"input{n} 的参照字幕对上剧本的条目",
                                "圈不出区间，两臂就没有共同分母")
        span = (min(got), max(got))
        ta, ka = hits(script, pa, span, a.window, a.fuzzy)
        tb, kb = hits(script, pb, span, a.window, a.fuzzy)
        ca, cb = (ta, tb) if a.by == "text" else (ka, kb)
        gained, lost = cb - ca, ca - cb
        g = classify(gained, None if a.by == "text" else text_of)
        l = classify(lost, None if a.by == "text" else text_of)
        net = sum(cb.values()) - sum(ca.values())
        oa = overclaim(SA.read_subs([pa]), have)
        ob = overclaim(SA.read_subs([pb]), have)
        print(f"f{n:<4}{sum(ca.values()):>7}{sum(cb.values()):>7}{net:>+6}"
              f"{sum(gained.values()):>+6}{-sum(lost.values()):>+6}"
              + "".join(f"{g[c]-l[c]:>+12}" if c == P else f"{g[c]-l[c]:>+16}"
                        for c in evalkit.CLASSES)
              + f"{f'{oa}->{ob}':>12}")
        rows.append((n, g[C] - l[C], net, ob - oa))
        if a.show:
            ex = [t if a.by == "text" else text_of[t]
                  for t in lost
                  if evalkit.triviality(t if a.by == "text" else text_of[t]) == C]
            if ex:
                print(f"      丢失的『{C}』样例：" + " | ".join(x[:26] for x in ex[:a.show]))
    print(f"\n**只有 `{C}` 这一档是『该抓到而没抓到』**（owner 2026-09-06）："
          + "  ".join(f"f{n} {d:+d}" for n, d, _, _ in rows))
    print("三档在这里**可以跨臂比**：命中是集合，集合差不经过 gap 的长/短分桶。"
          "（`script_align` 的三档不行——那是 gap 分桶的产物）")
    print("**代价侧报警**：每多拿 1 条净覆盖，重复认领涨几条——"
          + "  ".join(f"f{n} {(do/net if net else float('nan')):.1f}"
                      for n, _, net, do in rows)
          + "\n  重复认领 = 输出里某条剧本行比整部剧本多出来的次数。"
            "**是报警，不是已确认的错**："
            "\n  真实重播（回想/回读，产品目标要求支持）也会让它涨，"
            "而且两臂之差不是错误增量的下界（methodology-audit-4 报告 §1）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
