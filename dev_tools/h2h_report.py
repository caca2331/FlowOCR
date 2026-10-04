"""把 head2head.sh 产出的逐片 JSON 汇成一张表。

存在的理由（methodology-audit 报告建议 5）：旗舰结论此前是**一个点估计**
（"整片上赢 0.5 个点"），出自一部片子。一个点估计没法回答"这 0.5 个点算不算数"——
要回答它得有**素材间的波动范围**。所以这里默认按片列开，再给极差；
只有当各片方向一致时，合计数才有意义。

**按臂分组**（methodology-audit-3 报告 §3）：`head2head.sh` 的 `SUFFIX` 让同一个
目录里同时躺着好几条 A/B 臂（`f3-ours.json` 和 `f3-sr20-ours.json`）。上一版按
`f*-*.json` 通配、把它们全当成"片子"，于是在四臂目录上印出
**"20 部片子：我们赢 10/20 = 50.0%"**——同样五部数了四遍。
汇总、极差、版本校验现在**一律逐臂做**：跨臂的比较是 `dev_tools/arm_table.py`
（总数）和 `dev_tools/hit_delta.py`（命中集合差按三档拆）的事，不是这里的。

用法：python dev_tools/h2h_report.py tmp/h2h        # 目录里找 f*-{ours,base}.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import evalkit  # noqa: E402

SIDES = ("base", "ours")
LABEL = {"base": "望言+人手框+老算法", "ours": "flowocr 主轨（全自动）"}


def load(d: Path) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(d.glob("f*-*.json")):
        stem = p.stem                     # f3-ours / f3-sr20-ours（A/B 臂）
        # **从右边切**：`head2head.sh` 的 SUFFIX 会插在中间（f3-sr20-ours），
        # 从左边切会把 side 解析成 "sr20-ours" 而**整臂被静默跳过**——
        # 这个脚本历史上就是靠 glob 猜文件，猜错一次打出过另一张表。
        film, _, side = stem.rpartition("-")
        if side not in SIDES:
            continue
        out.setdefault(film, {})[side] = json.loads(p.read_text(encoding="utf-8"))
    return out


FILM_RE = re.compile(r"^(f\d+)(.*)$")


def split_arm(film_key: str) -> tuple[str, str]:
    """`f3-sr20` -> (`f3`, `-sr20`)；`f3` -> (`f3`, ``)。臂名就是 SUFFIX。"""
    m = FILM_RE.match(film_key)
    return (m.group(1), m.group(2)) if m else (film_key, "")


def by_arm(films: dict[str, dict]) -> dict[str, dict[str, dict]]:
    """把 `load()` 的结果按臂分组。**汇总只能在一条臂内部做**——
    四条臂放一起算"赢几部"是把同样五部数了四遍（methodology-audit-3 报告 §3）。"""
    out: dict[str, dict[str, dict]] = {}
    for key, sides in films.items():
        _, arm = split_arm(key)
        out.setdefault(arm, {})[key] = sides
    return out


def row(r: dict) -> tuple[int, float, int, float]:
    """(命中, 命中率, 疑似真漏, 真漏率)。**分母是"该上屏的对话类"，不是全剧本。**"""
    dial = r["dialogue_entries"]
    return r["dialogue_hit"], r["dialogue_hit"] / dial, r["gap_short_entries"], \
        r["gap_short_entries"] / dial


def check_provenance(d: Path, films: dict) -> None:
    """五片是不是同一版代码产的。

    存在的理由（methodology-audit-2 报告）：09-06 那一夜改了三次 `build_tracks`，
    产物落在临时目录，而这里只按文件名 glob，于是**照着文档的复现命令跑，
    打出来的是另一张表**（5,172 / 3,927 对文档的 5,058 / 3,908，而且更好看）。
    版本对不上就说出来，别让它静默流下去。
    """
    heads: dict[str, str] = {}
    fps: dict[str, str | None] = {}
    missing = []
    for film in sorted(films):
        p = d / f"{film}-prov.json"
        if not p.exists():
            missing.append(film)
            continue
        prov = json.loads(p.read_text(encoding="utf-8"))
        heads[film] = prov.get("git_head", "?")
        fps[film] = prov.get("code_fp")
    if missing:
        print(f"⚠ **{'/'.join(missing)} 没有 `-prov.json`**——这批数是哪一版代码产的**不知道**。"
              f"重跑 `dev_tools/head2head.sh` 会把 `build_tracks` 的 provenance 抄进来。")
    # 有代码指纹就比指纹（audit-6 §2.1）：HEAD 移动而代码没变不算不一致，`+dirty` 也不再证明不了
    if fps and all(fps.values()):
        if len(set(fps.values())) > 1:
            print("⚠ **各片的 build_tracks 代码指纹不一致，这张表不可比**：" +
                  "、".join(f"{k}={v}" for k, v in sorted(fps.items())))
        else:
            print(f"（产物代码指纹 {next(iter(fps.values()))}，{len(fps)} 片一致；"
                  f"git_head {'/'.join(sorted(set(heads.values())))}）")
        return
    if len(set(heads.values())) > 1:
        print("⚠ **各片的 git_head 不一致，这张表不可比**：" +
              "、".join(f"{k}={v}" for k, v in sorted(heads.items())))
    elif heads:
        one = next(iter(heads.values()))
        print(f"（产物版本 git_head={one}，{len(heads)} 片一致）")
        if one.endswith("+dirty"):
            # **"一致"在 dirty 上是假的**：两棵不同的工作树会打出同一个
            # `<sha>+dirty`，字符串相等证明不了代码相同（build_tracks.git_head 那条注释）。
            print("⚠ **但这批是 dirty 树产的**——`+dirty` 只说明当时有未提交改动，"
                  "**两棵不同的工作树会打出同一个字符串**，所以『五片一致』这句在这里"
                  "证明不了代码相同。要引用这张表，先把代码提交了重跑。")


def main(d: Path) -> int:
    films = load(d)
    if not films:
        print(f"{d} 下没有 f*-ours.json / f*-base.json —— 先跑 dev_tools/head2head.sh")
        return 2
    arms = by_arm(films)
    if len(arms) > 1:
        print(f"目录里有 **{len(arms)} 条 A/B 臂**："
              + "、".join(f"`{a or '默认'}`" for a in sorted(arms))
              + "。**逐臂分开报**——把它们并成一张表就是"
              "同样几部片子数了好几遍（methodology-audit-3 报告 §3）。\n"
              "跨臂比总数用 `dev_tools/arm_table.py`；"
              "比**命中集合**（唯一能按三档拆的跨臂口径）用 `dev_tools/hit_delta.py`。")
    for arm in sorted(arms, key=lambda x: (x != "", x)):
        if len(arms) > 1:
            print(f"\n### 臂 `{arm or '默认'}`（{len(arms[arm])} 片）")
        report_arm(d, arms[arm])
    return 0


def report_arm(d: Path, films: dict[str, dict]) -> None:
    """一条臂的表 + 汇总。**汇总里的"几部片子"永远是这条臂里的片数。**"""
    check_provenance(d, films)

    deltas_hit: list[float] = []
    deltas_miss: list[float] = []
    rel_hit: list[float] = []
    rel_miss: list[float] = []
    print("| 片子 | 分母（对话类） | 基线命中 | 我们命中 | **差** | 基线疑似真漏 | 我们疑似真漏 | **差** |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for film in sorted(films):
        s = films[film]
        if set(s) != set(SIDES):
            print(f"| {film} | —— 只有 {'/'.join(sorted(s))}，跳过 | | | | | | |")
            continue
        # 分母必须两边相同，否则两行数字不是同一批（evalkit 的老教训）
        db, do = s["base"]["dialogue_entries"], s["ours"]["dialogue_entries"]
        if db != do:
            evalkit.warn_pair_shift(db, do, "分母（对话类剧本条目）")
        hb, rb, mb, fb = row(s["base"])
        ho, ro, mo, fo = row(s["ours"])
        deltas_hit.append((ro - rb) * 100)
        deltas_miss.append((fo - fb) * 100)
        rel_hit.append(100 * (ho - hb) / max(1, hb))
        rel_miss.append(100 * (mo - mb) / max(1, mb))
        print(f"| {film} | {db} | {hb} = {rb:.1%} | {ho} = {ro:.1%} | "
              f"**{(ro-rb)*100:+.1f} 点** | {mb} = {fb:.1%} | {mo} = {fo:.1%} | "
              f"**{(fo-fb)*100:+.2f} 点** |")

    if len(deltas_hit) < 2:
        print("\n只有一部片子——**这正是审计要修的那个问题**，别在这上面下结论。")
        return

    # **分母是逐片不同的**：它由基线圈出的剧本区间决定，而各片跨的区间差很多
    # （input1 [0,30348] 共 30,349 条，input4 只有 [21395,29123] 共 7,729 条）。
    # 所以命中"率"跨片不可比——input4 的 56.9% 和 input5 的 22.3% 不是一回事。
    # 能横向比的是**相对基线的变化**。
    n = len(deltas_hit)
    win = sum(1 for x in deltas_hit if x > 0)
    print(f"\n**{n} 部片子**：命中率差 {min(deltas_hit):+.1f} ~ {max(deltas_hit):+.1f} 点"
          f"（中位 {evalkit.median(deltas_hit):+.1f}），"
          f"我们赢 {evalkit.denom(win, n)}；"
          f"疑似真漏差 {min(deltas_miss):+.2f} ~ {max(deltas_miss):+.2f} 点。")
    print(f"**跨片可比的口径（相对基线）**：命中 {min(rel_hit):+.1f}% ~ {max(rel_hit):+.1f}%"
          f"（中位 {evalkit.median(rel_hit):+.1f}%）；"
          f"疑似真漏 {min(rel_miss):+.0f}% ~ {max(rel_miss):+.0f}%"
          f"（中位 {evalkit.median(rel_miss):+.0f}%）。")
    print("各片分母不同（由基线圈出的剧本区间决定，7,729 ~ 31,337 条），"
          "**命中率本身跨片不可比**——能横向比的只有上面这一行。")
    if win not in (0, n):
        print("**方向不一致**——不要报合计数，逐片报。合计会把一部的赢摊到全部头上。")
    symmetric_diff(films)


def symmetric_diff(films: dict[str, dict[str, dict]]) -> None:
    """两侧疑似真漏的**对称差**，外加"纯标点/有内容字"分档。

    存在的理由（methodology-audit-2 报告）：五部合计 616 条疑似真漏里
    **429 条（70%）基线也漏**——那是两条采样式管线的共同地板（多半是
    显示时间短于一个采样间隔，或被玩家跳过），不是谁的缺陷。
    直接比两个总数，等于让这个地板把差距稀释掉。
    另外纯标点行（`……！`/`――`）占 60–71%，它们的病因是 **det 没框出来**，
    和有内容字的行不是一回事，必须分档。
    """
    rows = [(f, s) for f, s in sorted(films.items())
            if set(s) == set(SIDES) and all("missed" in s[k] for k in SIDES)]
    if not rows:
        return
    print("\n**疑似真漏的对称差**（`missed` 逐条求交；共同地板单列）：")
    print("| 片子 | 我们漏 | 基线漏 | 两边都漏 | **只我们漏** | **只基线漏** | "
          + " | ".join(f"我们漏里{c}" for c in evalkit.CLASSES) + " |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    tot_both = tot_ours = 0
    for f, s in rows:
        ok = {(e["key"], e["src"]) for e in s["ours"]["missed"]}
        bk = {(e["key"], e["src"]) for e in s["base"]["missed"]}
        by_cls = {c: sum(1 for e in s["ours"]["missed"]
                         if evalkit.triviality(e["text"]) == c) for c in evalkit.CLASSES}
        tot_both += len(ok & bk)
        tot_ours += len(ok)
        print(f"| {f} | {len(ok)} | {len(bk)} | {len(ok & bk)} | **{len(ok - bk)}** | "
              f"**{len(bk - ok)}** | "
              + " | ".join(evalkit.denom(by_cls[c], len(ok)) for c in evalkit.CLASSES) + " |")
    # **"两边都漏 = 共同地板、别记在任何一方头上"是过头话**
    # （methodology-audit-4 报告 §2）：两条管线都漏，也完全可能是**共同盲区**
    # （同样的采样率、同样的 det 弱点），那仍然是缺陷，只是两边一样差。
    print(f"\n合计我们 {tot_ours} 条，其中 {evalkit.denom(tot_both, tot_ours)} **基线也漏**"
          f"——这部分**分不出高下**，但**不代表不是缺陷**：两条都是采样式管线，"
          f"共同盲区（采样率、det 弱点）会同时漏。要判它是不是真漏，得抽帧。"
          f"归因请用 `dev_tools/probe_gap_locate.py`（位置判据），"
          f"**不要**用 `script_align --raw-ocr`（全局搜，对纯标点行几乎恒真）。")


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "tmp/h2h")))
