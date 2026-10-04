"""拿一份"参考时间轴"来量我们条目的边界误差。

参考时间轴的来源可以是 VideoSubFinder 的 `-ces`（只出时间轴、逐帧精度、不做 OCR），
也可以是任何别的 SRT。比的是**边界**，不是文本——文本对不对由 CER 另外量。

## 两条口径上的坑（都踩过，别再踩）

**一、参照没有文本时，按文本配对会静默配上 0 条。** VideoSubFinder 的 `-ces` 在正文位置
写的是时长（`2,449`），不是台词。用 `--match text` 去配它，10 条参考配上 0 条，
而旧版脚本只打一行汇总就退出、误差一行都不印、退出码还是 0。现在默认 `--match auto`：
先看参照到底有没有可用文本，没有就退回 overlap 并**明说**；配上 0 条一律非零退出。

**二、误差是在"配上的那些条目"上算的，配上几条会变。** 无重叠的参考算漏配，
直接不进中位/P90——所以"边界改坏→掉出配对→中位反而变好"是可能的。
实测 ja-dialogue：不回抠配上 6/10，回抠配上 5/10，两行中位数**不是同一批条目**算的。
所以：汇总行永远打印"配上/无对应"；要做 A/B 就用 `--pair-report` 存一份配对结果，
另一次跑加 `--common-with` 限定到两次都配上的那批，数字才可比。

用法：
    python dev_tools/compare_timing.py out/vsf/ja-dialogue-timings.srt \
        out/ja-dialogue/ja-dialogue-region01-subtitle.srt --min-ref-dur 0.5 \
        --pair-report tmp/pair-before.json
    python dev_tools/compare_timing.py out/vsf/ja-dialogue-timings.srt \
        out/ja-dialogue/ja-dialogue-region01-subtitle.srt \
        --common-with tmp/pair-before.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import evalkit          # noqa: E402
from flowocr.artifacts import srtio            # noqa: E402

MIN_P90_SAMPLES = evalkit.MIN_PCTL_SAMPLES
"""配上的条目少于这么多就不报 P90。10 条参考里配上 6 条时，"P90" 只是第 5 大那个值。
阈值与口径统一在 `flowocr.artifacts.evalkit`——那一条是全项目唯一被验证有效的止损方式，
不该每个工具各留一份。"""


def read_srt(path: Path) -> list[tuple[float, float, str]]:
    """解析与"一条 = 一个 cue"的口径都来自 `flowocr.artifacts.srtio`；
    这里保持本工具历史上的拼行分隔符 `" / "`，否则文本配对的读数会变。"""
    return sorted((c.start, c.end, c.text(" / ")) for c in srtio.read_srt(path))


def overlap(a: tuple[float, float, str], b: tuple[float, float, str]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def norm(t: str) -> str:
    return "".join(t.split())


def sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def has_usable_text(entries: list[tuple[float, float, str]]) -> float:
    """这份 SRT 的正文里有多少条含"像话的字"（不是纯数字/标点）。

    VideoSubFinder 的 `-ces` 时间轴正文写的是时长（`2,449`），
    拿它按文本配对必然全军覆没——所以要能先认出来。
    """
    if not entries:
        return 0.0
    n = sum(1 for _, _, t in entries if any(c.isalpha() for c in t))
    return n / len(entries)


def key_of(r: tuple[float, float, str]) -> str:
    """参考条目的身份：起止时间（ms）。用来跨两次运行取交集。"""
    return f"{round(r[0] * 1000)}-{round(r[1] * 1000)}"


def pct(vals: list[float], q: float) -> float:
    """与 build_tracks.pct 同一个定义（最近秩）。别在同一个仓库里用两套分位数。"""
    v = sorted(vals)
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))] if v else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("ours")
    ap.add_argument("--min-ref-dur", type=float, default=0.5,
                    help="参考时间轴里短于这个秒数的条目不参与比较（多是转场/打字机的过渡帧）")
    ap.add_argument("--match", choices=("auto", "overlap", "text"), default="auto",
                    help="怎么配对。auto（默认）= 参照有可用文本就按文本、否则退回 overlap "
                         "并明说。text：只按时间重叠配对会被长条目骗——一条 100 秒的常驻条目"
                         "对 3 秒的参考条目重叠也是满的，会抢走正确那条的配对；"
                         "但参照若不带文本（VideoSubFinder 的时间轴）就一条也配不上。"
                         "overlap：旧口径，只在参照无文本时才是唯一选择。")
    ap.add_argument("--min-sim", type=float, default=0.4,
                    help="按文本配对时的相似度下限；低于它就算无对应")
    ap.add_argument("--pair-report", default=None,
                    help="把逐条配对结果写成 JSON，供另一次运行用 --common-with 取交集")
    ap.add_argument("--common-with", default=None,
                    help="读一份 --pair-report，只统计**两次都配上**的参考条目。"
                         "A/B 对比必须用它，否则两行中位数不是同一批条目算的")
    ap.add_argument("--near-miss-ms", type=float, default=150.0,
                    help="无对应的参考条目里，与我们最近的条目间隙小于这么多毫秒的，"
                         "算『擦肩而过』单独报——它们不是漏检，是 overlap>0 这个刀刃判据"
                         "的边界效应")
    ap.add_argument("--quiet", action="store_true", help="只出汇总，不逐条打印")
    args = ap.parse_args()

    ref_all = read_srt(Path(args.reference))
    ref = [x for x in ref_all if x[1] - x[0] >= args.min_ref_dur]
    ours = read_srt(Path(args.ours))

    # ---- 配对口径：先看参照到底有没有文本 ----
    ref_text_frac = has_usable_text(ref)
    match = args.match
    if match == "auto":
        match = "text" if ref_text_frac >= 0.5 else "overlap"
        if match == "overlap":
            print(f"⚠ 参照只有 {ref_text_frac:.0%} 的条目带可用文本"
                  f"（VideoSubFinder 的时间轴正文写的是时长），**退回 --match overlap**。"
                  f"这条口径会被长条目骗，读数时要记着。")
    elif match == "text" and ref_text_frac < 0.5:
        print(f"⚠ 指定了 --match text，但参照只有 {ref_text_frac:.0%} 的条目带可用文本，"
              f"多半会配上 0 条。想比这种时间轴请用 --match overlap 或 auto。")

    common: set[str] | None = None
    if args.common_with:
        prev = json.loads(Path(args.common_with).read_text(encoding="utf-8"))
        common = {p["key"] for p in prev["pairs"] if p["matched"]}
        print(f"限定到 {Path(args.common_with).name} 里配上的 {len(common)} 条参考条目")

    start_err: list[float] = []
    end_err: list[float] = []
    near_misses: list[float] = []          # 完全不重叠，只差一点
    overlapped_no_text: list[float] = []   # 有重叠，但文本配不上（只可能在 text 口径下）
    pairs: list[dict] = []
    unmatched = 0
    skipped = 0
    if not args.quiet:
        print(f"{'ref start':>10} {'ref end':>9} | {'our start':>10} {'our end':>9} | "
              f"{'Δstart':>7} {'Δend':>7}  text")
    for r in ref:
        k = key_of(r)
        if common is not None and k not in common:
            skipped += 1
            continue
        cand = [o for o in ours if overlap(r, o) > 0]
        if match == "text":
            # 在时间上重叠的候选里挑文本最像的；相似度打平再看重叠
            best = max(cand, key=lambda o: (round(sim(r[2], o[2]), 3), overlap(r, o)), default=None)
            if best is not None and sim(r[2], best[2]) < args.min_sim:
                best = None
        else:
            best = max(cand, key=lambda o: overlap(r, o), default=None)
        if best is None:
            unmatched += 1
            # 擦肩而过还是真漏？`overlap > 0` 是个刀刃判据：`--srt-start full` 把起点
            # 往后推之后，本来只压住参考条目几十毫秒的条目会退到它前面，配对直接归零。
            # 实测 zh-sub 上 refined-full 失配的 4 条，间隙全在 0–100 ms。
            # 间隙夹到 0：重叠时这个差是负的，不夹会算出"间隙 -1166 ms"这种鬼话。
            # 分两类看：**真有重叠却没配上** 只可能是文本对不上（text 口径），
            # 那不是时间的问题；**完全不重叠** 才要问是漏了还是擦肩而过。
            gap = min((max(0.0, o[0] - r[1], r[0] - o[1]) for o in ours), default=float("inf"))
            (overlapped_no_text if cand else near_misses).append(gap)
            pairs.append({"key": k, "matched": False, "gap": round(gap, 4)})
            if not args.quiet:
                print(f"{r[0]:>10.3f} {r[1]:>9.3f} |     —          —    |       —       —  (无对应)")
            continue
        ds, de = best[0] - r[0], best[1] - r[1]
        start_err.append(abs(ds))
        end_err.append(abs(de))
        pairs.append({"key": k, "matched": True, "ds": round(ds, 4), "de": round(de, 4)})
        if not args.quiet:
            print(f"{r[0]:>10.3f} {r[1]:>9.3f} | {best[0]:>10.3f} {best[1]:>9.3f} | "
                  f"{ds:>+7.3f} {de:>+7.3f}  {best[2][:40]}")

    n_considered = len(ref) - skipped
    print(f"\n参考条目 {len(ref)}（已滤掉 <{args.min_ref_dur}s 的）"
          + (f"，取交集后参与 {n_considered}" if common is not None else "")
          + f"，**配上 {len(start_err)}/{n_considered}"
            f"（{len(start_err)/max(1,n_considered):.0%}）**，无对应 {unmatched}"
            f"（配对口径：{match}）")
    print(f"我们的条目 {len(ours)} 条")
    if overlapped_no_text:
        print(f"无对应的里有 **{len(overlapped_no_text)} 条其实时间上是重叠的**，"
              f"只是文本配不上（相似度 <{args.min_sim}）——这不是时间的问题")
    if near_misses:
        close = [g for g in near_misses if g <= args.near_miss_ms / 1000]
        print(f"无对应的里有 {len(near_misses)} 条完全不重叠，其中 **{len(close)} 条只差 "
              f"≤{args.near_miss_ms:.0f} ms**（擦肩而过，不是真漏；"
              f"间隙中位 {statistics.median(near_misses)*1000:.0f} ms）")
    if start_err:
        n = len(start_err)
        # 样本太少时 P90 是噪声：6 个点的"P90"落在第 5 个上，写进文档就成了假精度。
        # 宁可不报，也别让人拿它当结论（doc/boundary-refinement.md 就吃过这个亏）。
        def line(name: str, errs: list[float]) -> str:
            tail = (f"  P90 {pct(errs, 0.9):.3f}s" if n >= MIN_P90_SAMPLES
                    else f"  P90 —（样本 {n} < {MIN_P90_SAMPLES}，不报）")
            return f"{name}  中位 {statistics.median(errs):.3f}s{tail}  最大 {max(errs):.3f}s"
        print(line("起点误差", start_err))
        print(line("终点误差", end_err))
        print(f"注：以上只统计**配上的那 {n} 条**。配对数不同的两次跑不可直接比，"
              "用 --pair-report / --common-with 取交集。")

    if args.pair_report:
        Path(args.pair_report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.pair_report).write_text(json.dumps(
            {"reference": args.reference, "ours": args.ours, "match": match,
             "min_ref_dur": args.min_ref_dur, "n_ref": len(ref),
             "n_matched": len(start_err), "pairs": pairs},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"逐条配对已写入 {args.pair_report}")

    if not start_err:
        print("✗ 一条都没配上，没有可报的误差——先检查配对口径，别把空结果当成结论。",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
