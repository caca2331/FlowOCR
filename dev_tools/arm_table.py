"""把 `tmp/h2h/` 里几条 A/B 臂的结果并成一张表：命中 / 疑似真漏 / **三档分开**。

存在的理由（owner 指示 9）：疑似真漏必须按内容字数分
纯标点 / trivial(1-2字) / 有内容(≥3字) 三档看，三档的量级差两个数量级，
混成一个数必然把结论带反。`h2h_report.py` 是"我们 vs 基线"的两方对比；
这个脚本是"同一侧的几条臂互相比"，两者的分工不同，别互相塞。

臂名就是 `head2head.sh` 的 `SUFFIX`（空 = 默认臂）。三档是从 json 里的 `missed`
现算的，用 `evalkit.triviality`——**不从日志里抠**，日志会被下一条臂覆盖。

用法：
    python dev_tools/arm_table.py tmp/h2h --arms ",-m,-sr20,-sr20m"
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import evalkit  # noqa: E402


def load(d: Path, film: str, arm: str) -> dict | None:
    p = d / f"{film}{arm}-ours.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", default="tmp/h2h")
    ap.add_argument("--films", default="f1,f2,f3,f4,f5", help="逗号分隔")
    ap.add_argument("--arms", default=",-m,-sr20,-sr20m",
                    help="逗号分隔的 head2head.sh SUFFIX，空串是默认臂。"
                         "**不能用 nargs**：SUFFIX 以 `-` 开头，argparse 会当成选项")
    ap.add_argument("--base", default="", help="拿哪条臂当基准算差值")
    a = ap.parse_args()
    d = Path(a.dir)
    arms = a.arms.split(",")
    films = a.films.split(",")

    names = {"": "默认", "-m": "默认+名牌", "-sr20": "span2.0",
             "-sr20m": "span2.0+名牌", "-sr20mb": "span2.0+名牌+同带"}
    P, T, C = evalkit.CLASSES
    print(f"{'片':<4}{'臂':<14}{'命中':>7}{'Δ':>7}{'疑似真漏':>9}{'Δ':>6}"
          f"{P:>8}{T:>16}{C:>16}")
    for film in films:
        base = load(d, film, a.base)
        for arm in arms:
            r = load(d, film, arm)
            if r is None:
                continue
            cls = Counter(evalkit.triviality(m["text"]) for m in r["missed"])
            dh = dg = ""
            if base is not None and arm != a.base:
                dh = f"{r['dialogue_hit'] - base['dialogue_hit']:+d}"
                dg = f"{r['gap_short_entries'] - base['gap_short_entries']:+d}"
            print(f"{film:<4}{names.get(arm, arm or '默认'):<14}{r['dialogue_hit']:>7}{dh:>7}"
                  f"{r['gap_short_entries']:>9}{dg:>6}"
                  f"{cls[P]:>8}{cls[T]:>16}{cls[C]:>16}")
        print()
    print("**只有第三档是『该抓到而没抓到』**（owner 2026-09-06）：纯标点行没有判别力、"
          "不作强求，trivial 两档合起来占疑似真漏的六到八成。")
    # 三档在这张表里是**逐臂上下排**的，眼睛很容易顺着列往下减——那正是被禁止的读法。
    # 所以把禁令和替代品一起印在表尾（methodology-audit-3 报告 §1）。
    print("⚠ **这三列不能上下相减**：`gap_short + gap_long ≡ entries − hits`，"
          "周围条目被匹配上之后，长 gap 里的漏条会被**重新分桶**成短 gap，"
          "\n   于是『有内容』那一档看着变多（f1 26→37）——那是分桶变动，不是修法伤了它。"
          "\n   要看**收益落在哪一档**，用 `python dev_tools/hit_delta.py <片号...> --b=<臂>`："
          "命中是集合，集合差不经过 gap 分桶。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
