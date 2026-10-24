"""把 `tmp/h2h/` 里几条 A/B 臂的结果并成一张表：命中 / 疑似真漏 / **三档分开**。

存在的理由（owner 指示 9）：疑似真漏必须按内容字数分
纯标点 / trivial(1-2字) / 有内容(≥3字) 三档看，三档的量级差两个数量级，
混成一个数必然把结论带反。`h2h_report.py` 是"我们 vs 基线"的两方对比；
这个脚本是"同一侧的几条臂互相比"，两者的分工不同，别互相塞。

臂名就是 `head2head.sh` 的 `SUFFIX`（空 = 默认臂）。三档是从 json 里的 `missed`
现算的，用 `evalkit.triviality`——**不从日志里抠**，日志会被下一条臂覆盖。

有 `head2head.sh` 第 4 步的产物（`<片><臂>-miss.json`）时，下面再印一张检测器的表：
naninovel 匹配器产物上该出现的行的覆盖（`detector_table`）。**臂间比较的主尺是这张表**（覆盖 + 重复认领·其他，owner 2026-10-07），
上面的命中三档是诊断尺。

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
from h2h_report import hit_of  # noqa: E402  命中取有 OCR 文本支撑的（填位不算），旧 JSON 退回 dialogue_hit
import miss_detect as MD  # noqa: E402


def load(d: Path, film: str, arm: str, kind: str = "ours") -> dict | None:
    p = d / f"{film}{arm}-{kind}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def comparable(r: dict, base: dict) -> str:
    """两臂的检测结果能不能相减：分母（剧本、名字表、参照字幕、分段）相同，且检测器和匹配器是同一版代码。返回不能的原因，空串 = 能。
    obs 不进这一条：naninovel 的分母不取决于 obs，换 OCR 的臂覆盖照样可比（只在行尾提示）。"""
    diff = [k for k in MD.same_ref(r["provenance"], base["provenance"]) if k not in ("obs", "obs_mtime")]
    if diff:
        return f"分母不同（{', '.join(diff)}）"
    if not r.get("code") or not base.get("code"):
        return "有一边没记代码指纹（旧产物），重跑 head2head.sh 第 4 步"
    code = [k for k in ("detector", "matcher") if r["code"].get(k) != base["code"].get(k)]
    return f"{'、'.join({'detector': '检测器', 'matcher': '匹配器'}[k] for k in code)}代码不同" if code else ""


def detector_table(d: Path, films: list[str], arms: list[str], base_arm: str, names: dict[str, str]) -> None:
    """`head2head.sh` 第 4 步的产物（naninovel 匹配器 + `miss_detect`）-> 每臂一行：覆盖、漏、没读到、重复认领（其他）。
    覆盖 = (已输出 + 另一版本已输出) / (该出现 − 匹配器决定不显示)，只数有内容 ≥3 字的行；分母只取决于剧本和参照字幕，跨臂相同。"""
    if not any((d / f"{f}{a}-miss.json").exists() for f in films for a in arms):
        return
    print(f"\n检测器（naninovel 匹配器的产物上，{evalkit.CLASSES[2]}；`head2head.sh` 第 4 步）")
    print(f"{'片':<4}{'臂':<14}{'该出现':>7}{'覆盖':>8}{'覆盖行':>7}{'Δ':>6}{'漏':>5}{'定位弱':>6}{'没读到':>7}{'重复·其他':>9}")
    for film in films:
        base = load(d, film, base_arm, "miss")
        for arm in arms:
            r = load(d, film, arm, "miss")
            if r is None:
                continue
            c = MD.ruler(r)
            stats = json.loads(Path(r["matched"]).read_text(encoding="utf-8"))["stats"]
            dup = (stats.get("repeat_extra_echo") or {}).get("other", "—")
            notes = [] if r["invariants"]["ok"] else ["⚠ 不变量没过，这行不能比"]
            dc = ""
            if base is not None and arm != base_arm:
                why = comparable(r, base)
                if why:
                    notes.append(f"拒比：{why}")
                elif not base["invariants"]["ok"]:
                    notes.append("⚠ 基准臂不变量没过，不算差")
                else:
                    dc = f"{c['md_cov'] - MD.ruler(base)['md_cov']:+d}"
                if (r["provenance"].get("obs"), r["provenance"].get("obs_mtime")) != (base["provenance"].get("obs"), base["provenance"].get("obs_mtime")):
                    notes.append("obs 不同：覆盖可比，漏 / 没读到的划分随 obs 变")
            den = f"{c['md_cov'] / c['md_den']:.1%}" if c["md_den"] else "—"
            print(f"{film:<4}{names.get(arm, arm or '默认'):<14}{c['md_den']:>7}{den:>8}{c['md_cov']:>7}{dc:>6}"
                  f"{c['md_miss']:>5}{c['md_weak']:>6}{c['md_unread']:>7}{dup:>9}" + "".join(f"  {x}" for x in notes))
        print()
    print("覆盖的绝对值被没播的分支压低（顺序剧本没有处境），只看臂间差；代价看重复认领里回显判定为「其他」的。"
          "\n逐条差（新漏 / 修好在哪一层）：python dev_tools/miss_detect.py <A 的 matched.json> --vs <B 的> --span-ref <参照字幕>。")


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
                if ("dialogue_hit_text" in r) != ("dialogue_hit_text" in base):
                    raise SystemExit(f"{film}{arm} 和基准臂一边有 dialogue_hit_text、一边没有：命中口径不同，重跑其中旧的那条臂")
                dh = f"{hit_of(r) - hit_of(base):+d}"
                dg = f"{r['gap_short_entries'] - base['gap_short_entries']:+d}"
            print(f"{film:<4}{names.get(arm, arm or '默认'):<14}{hit_of(r):>7}{dh:>7}"
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
    detector_table(d, films, arms, a.base, names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
