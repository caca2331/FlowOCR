"""**探针**：不靠角色名表，光看几何，能不能把名牌区域挑出来。

来历（methodology-audit-2 报告 §3）：把全片名牌并进主轨，input1 上
**命中 +66、纯标点行漏条率 97.6% → 52.4%**，比"并整个辩论屏区域"两项都好。
但那次挑名牌用的是**角色名表**——探针能用、修法不能用（换素材就没有名表了）。

所以这里先回答一个前置问题：**几何够不够**。名牌的形状假设是——

* 位置固定：`cx` 在全片上几乎不动（框中心的横向标准差小）；
* 在正文**上方紧挨着的一档**：`cy` 比主轨小 `--dy` ~ `--dy-max`——
  **上界不能省**，否则屏幕顶上的标题/计时器也会被挑中（实测 region36/42 就是这么混进来的）；
* **字比正文大**：字高中位是主轨的 `--font-ratio` 倍以上；
* **词表很小**：名牌就那么十来个名字轮着出现，所以 `distinct_text_ratio` 低；
* **cue 时长像台词**：名牌跟着台词起落，时长中位 4.5–5.5 s。

  > **`dominant_share` 那道闸门是错的，2026-09-07 换掉了。**
  > 原来拿"一句占 90%+ = 水印"来排除台标，结果**主角的名牌先被排除了**：
  > `階堂ヒロ` 在 f3/f4/f5 各占那块区域的 68–85%，于是 846/615/455 条名字
  > 一条都挑不中（召回卡在 40–51%）。真正分得开的是**时长**——
  > 名牌 4.5–5.5 s，`Auto` 按钮 21–43 s，误识的 `大`/`米`/`è` 0.5–1.5 s。
  > 换成时长窗之后召回 81.6 / 90.1 / 77.1%。**时长窗 1.5–15 / 2–10 / 3–8
  > 三档结果完全相同**——阈值坐在平台上。
  > `--max-fill`（真水印几乎全程在，`#` 实测 fill 1.00）在这五部上**一个数都不改**，
  > 留着是给别的素材兜底，别把这次的收益记到它头上。

判据的准确性拿名表当参照来量（**只在这里用名表**）：
挑出来的区域里有多少 run 真是名字（精确率）、全片名字有多少落在挑中的区域里（召回）。

用法：
    python dev_tools/probe_nameplate_geom.py out/yuka-f1chk2/f1-tracks.json \\
        --names tmp/match/Text/CharacterNames.bytes
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import tracksio  # noqa: E402
from flowocr.artifacts import evalkit  # noqa: E402
from flowocr.analyze import nameplate  # noqa: E402


def load_names(p: Path) -> list[str]:
    return sorted({ln[2:].strip() for ln in p.read_text(encoding="utf-8").splitlines()
                   if ln.startswith("; ") and not ln.startswith("; 日本") and ln[2:].strip()})


def is_name_variant(s: str, names: list[str], max_chars: int = 8,
                    min_sub: int = 2) -> bool:
    """比 `is_name` 松：某个名字的**子串**（至少 `min_sub` 个字），或相似度 ≥0.6。

    用来给"精确率是下界"配一个上界。实测挑中的 run 里判为"不是名字"的那些，
    最常见的是 `桜羽工マ`（719 次）——OCR 把 `桜羽エマ` 的 `エ` 读成 `工`，
    相似度 0.75，**卡在 `is_name` 的 0.8 门槛下面**；其次是 `桜羽工` / `桜` / `橘`
    这种截断读。真正不像名字的只有 `提示する`(24) / `SKetch`(7) 这几种。

    ⚠ **`min_sub` 默认 2，这是 §21.2 定下的口径**（2026-09-20 复审第 2 条落实到代码）：
    原来是 1，于是**单个汉字**只要出现在任何角色名里就算"像名字"——`月` / `大` / `橘` / `桜`
    全都过。§21.2 当时只是把结论改了口（50 条里 100% → 80%），**函数没动**，
    于是下一份探针（`probe_np_score`）又把松尺继承了一遍，f5 的精确率上界虚高
    77% → 真值 69%。**"和旧表校准一致"证明不了尺是对的**——旧表用的就是这把松尺。
    要复现 §21.2 之前的数就显式给 `min_sub=1`。
    """
    s = s.strip()
    if not s or len(s) > max_chars:
        return False
    return any((len(s) >= min_sub and s in n)
               or SequenceMatcher(None, s, n).ratio() >= 0.6 for n in names)


def is_name(s: str, names: list[str], max_chars: int = 8) -> bool:
    """这条文本像不像角色名。**只用来评判据，不进修法**——换素材就没有名表了。

    `probe_slot_merge.py` 也用它给 run 打标签，所以放在模块级：
    历史上这段规则被抄过三份（探针、scratchpad、报告脚本），
    fuzzy 阈值一处改了别处没改，两张表就不可比了。
    """
    s = s.strip()
    if not s or len(s) > max_chars:
        return False
    return any(s == n or (len(s) >= 3 and n.endswith(s)) or
               SequenceMatcher(None, s, n).ratio() >= 0.8 for n in names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracks", help="build_tracks 产的 *-tracks.json")
    ap.add_argument("--names", default=None,
                    help="角色名表（**只用来评判据，不进修法**）。"
                         "**省掉它**就进『无参照模式』：不报精确率/召回，改成逐条判据的"
                         "过没过——换素材时手里根本没有名表，而那时最要问的恰恰是"
                         "『为什么一个都没挑中』")
    ap.add_argument("--show-fail", type=int, default=6,
                    help="无参照模式下，列几个『落选但看着像』的区域（按 run 数排）")
    nameplate.add_args(ap)
    ap.add_argument("--max-chars", type=int, default=8, help="名牌不会太长")
    a = ap.parse_args()

    d = tracksio.load(Path(a.tracks))

    d["regions"] = tracksio.regions_with_runs(d)
    W, H = d["size"]
    names = load_names(Path(a.names)) if a.names else []

    regs = nameplate.region_stats(d)
    for r in regs if names else ():
        r["n_name"] = sum(1 for b in r["runs"]
                          if is_name(b["text"], names, a.max_chars))
        r["n_var"] = sum(1 for b in r["runs"]
                         if is_name(b["text"], names, a.max_chars)
                         or is_name_variant(b["text"], names, a.max_chars))
    evalkit.require_nonzero(len(regs), "有 run 的区域")
    main_r, picked = nameplate.pick(regs, a)
    print(f"主轨 = region{main_r['i']:02d}（{main_r['label']}），"
          f"cy={main_r['cy']:.3f}、字高中位 {main_r['h']:.0f}px、run {main_r['n']}")

    if not names:
        # **无参照模式**：没有角色名表（换素材时的常态），就不谈精确率/召回，
        # 只回答"挑中了谁、以及落选的那些是被哪一条判据挡的"。
        print(f"\n（**无参照模式**：没给 `--names`，不报精确率/召回）"
              f"\n几何判据挑中 {len(picked)} 个区域"
              + ("：" if picked else "——**一个都没有**。下面是落选的候选，看是哪条挡的："))
        for r in sorted(picked, key=lambda x: -x["n"]):
            print(f"  region{r['i']:02d}（{r['label']}）run {r['n']}  cy={r['cy']:.3f}"
                  f"  字高 {r['h']:.0f}  时长中位 {r['dur']:.1f}s")
        cand = [r for r in regs if r["i"] != main_r["i"] and r not in picked
                and r["cy"] < main_r["cy"] and r["n"] >= a.min_runs]
        for r in sorted(cand, key=lambda x: -x["n"])[:a.show_fail]:
            bad = [f"**{n}**（{d}）" for n, ok, d in nameplate.explain(r, main_r, a) if not ok]
            print(f"\n  region{r['i']:02d}（{r['label']}）run {r['n']}"
                  f"  cy={r['cy']:.3f}  字高 {r['h']:.0f}  时长中位 {r['dur']:.1f}s"
                  f"\n    挡住它的：" + "、".join(bad))
        if not cand:
            print("  （主轨上方没有 run 数够格的区域——先看聚类分没分开）")
        return 0

    tot_name = sum(r["n_name"] for r in regs if r["i"] != main_r["i"])
    got_name = sum(r["n_name"] for r in picked)
    got_all = sum(r["n"] for r in picked)
    print(f"\n几何判据（cy 高出 {a.dy}–{a.dy_max}、字高 ≥{a.font_ratio}×、"
          f"cx 标准差 ≤{a.cx_std}、cue 时长中位 {a.dur_lo}–{a.dur_hi}s、"
          f"fill ≤{a.max_fill}、run ≥{a.min_runs}）挑中 {len(picked)} 个区域：")
    print(f"  {'区域':<6}{'run':>7}{'其中名字':>9}{'精确率':>9}{'cy':>7}{'字高':>7}"
          f"{'cx std':>8}{'distinct':>9}{'domi':>7}{'时长':>7}{'fill':>6}")
    for r in sorted(picked, key=lambda x: -x["n_name"]):
        print(f"  {r['i']:<6}{r['n']:>7}{r['n_name']:>9}{r['n_name']/r['n']:>9.0%}"
              f"{r['cy']:>7.3f}{r['h']:>7.0f}{r['cx_std']:>8.3f}"
              f"{r['distinct']:>9.3f}{r['domi']:>7.3f}{r['dur']:>7.1f}{r['fill']:>6.2f}")
    print(f"\n  **精确率** {evalkit.denom(got_name, got_all)}（挑中的 run 里有多少真是名字）")
    got_var = sum(r["n_var"] for r in picked)
    print(f"    这是**下界**：名表按 fuzzy 0.8 匹配，而 OCR 把 `桜羽エマ` 读成 `桜羽工マ` "
          f"时相似度只有 0.75，会被判成『不是名字』。")
    print(f"    把名字的 OCR 变体（子串 / 相似度 ≥0.6）也算上是**上界** "
          f"{evalkit.denom(got_var, got_all)}。")
    print(f"  **召回**   {evalkit.denom(got_name, tot_name)}（全片名字 run 有多少落在挑中的区域里）")
    miss = [r for r in regs if r["i"] != main_r["i"] and r["n_name"] >= a.min_runs
            and r not in picked]
    if miss:
        print("\n  漏掉的名牌区域（几何没挑中但名字很多）——**这些就是判据还差的地方**：")
        for r in sorted(miss, key=lambda x: -x["n_name"])[:6]:
            print(f"    region{r['i']:02d}  run {r['n']:>5}  名字 {r['n_name']:>5}"
                  f"  cy={r['cy']:.3f}（主轨 {main_r['cy']:.3f}）"
                  f"  字高 {r['h']:.0f}（主轨 {main_r['h']:.0f}）  cx std={r['cx_std']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
