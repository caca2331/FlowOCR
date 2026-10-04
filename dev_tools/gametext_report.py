"""`dev_tools/gametext_eval.sh` 的汇总表：每段一行，**按臂分组**（臂不是片子，h2h_report 栽过）。

    python dev_tools/gametext_report.py                  # 读 tmp/gametext/*.json
    python dev_tools/gametext_report.py --dir tmp/gametext

每一格都从 `game_align --out` 的 JSON 里原样取，不在这里重算——
这张表只是排版，口径全在 game_align / script_align.gap_report。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import evalkit  # noqa: E402
from flowocr import paths  # noqa: E402

TAG_RE = re.compile(r"^((?:gi|hsr|zzz|wuwa)(?:-s\d+|\d+)?)(.*)$")
"""切片 `gi-s1`、整场 `gi1`，以及只有一场的整场 `hsr` / `zzz`；后面剩下的是臂后缀。"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(paths.data_root() / "tmp/gametext"))
    a = ap.parse_args()

    arms: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for p in sorted(Path(a.dir).glob("*.json")):
        m = TAG_RE.match(p.stem)
        if not m:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if not (isinstance(d, dict) and "band_entries" in d and "ref_git_head" in d):
            # 名字像、内容不是（放错目录的别的 JSON）：说一声跳过，别让它冒充一条臂或把表打崩
            print(f"[skip] {p.name} 不是 game_align --out 的产物")
            continue
        if "miss_by_class" not in d:
            # 旧口径（疑似真漏只算短 gap，audit-6 §1.1 之前）：不混进新表，也不做兼容
            print(f"[skip] {p.name} 是旧口径的产物（没有 miss_by_class）——重跑 gametext_eval")
            continue
        arms[m.group(2)].append((m.group(1), d))
    if not arms:
        raise SystemExit(f"{a.dir} 下没有 game_align 的 JSON——先跑 bash dev_tools/gametext_eval.sh")

    if any(not d.get("gametext_integrity_checked") for rows in arms.values() for _, d in rows):
        print("⚠ 文本包：有的上游快照没做完整性检查（工作区是否等于所记 commit 无从验证）")
    # 同一款游戏，所有臂、所有段必须出自同一份语料（文本包指纹），否则表里混着分母的变化
    by_game: dict[str, set] = defaultdict(set)
    for rows in arms.values():
        for tag, d in rows:
            by_game[re.match(r"[a-z]+", tag).group(0)].add(d.get("gametext_fingerprint"))
    for g, fset in sorted(by_game.items()):
        if len(fset) > 1:
            print(f"⚠ {g} 的剧本出自 {len(fset)} 份不同的语料（文本包指纹 {sorted(str(f)[:19] for f in fset)}）——分母不可比，重跑 gametext_eval")
    P, T, C = evalkit.CLASSES
    for arm, rows in sorted(arms.items()):
        heads = {d.get("tracks_git_head") for _, d in rows}
        fps = {d.get("tracks_code_fp") for _, d in rows}
        rfps = {d.get("ref_code_fp") for _, d in rows}
        print(f"\n## 臂 {arm or '（默认）'}：{len(rows)} 段；build_tracks 代码指纹 {sorted(map(str, fps))}"
              f"（git_head {sorted(map(str, heads))}）；gamescript 代码指纹 {sorted(map(str, rfps))}")
        # 比**代码指纹**不比 HEAD（audit-6 §2.1）：HEAD 移动、代码没变时 git_head 会不同，
        # 那不该报警；反过来 `+dirty` 相同也证明不了代码相同，指纹能
        for what, fp in (("轨（build_tracks）", fps), ("剧本（gamescript）", rfps)):
            if None in fp:
                print(f"  ⚠ 有的{what}没有代码指纹（早于该字段）——是不是同一份代码产的，证明不了")
            elif len(fp) > 1:
                print(f"  ⚠ 这一臂里{what}的代码指纹不止一个——表里混着代码版本的变化")
        print(f"  {'段':<8}{'cue':>7}{'对不上剧本':>14}{'该上屏':>8}{'命中':>16}"
              f"{'长gap':>7}{'疑似真漏 ' + C:>22}{P:>12}{T:>18}{'重复认领':>10}{'行首缺字':>14}")
        for tag, d in sorted(rows, key=lambda x: x[0]):
            bc = d["miss_by_class"]
            ht = d.get("head_tail", {})

            def cell(c):
                tot, m, _ = bc.get(c, [0, 0, 0])
                return evalkit.denom(m, tot) if tot else "—"
            print(f"  {tag:<8}{d['cues']:>7}"
                  f"{evalkit.denom(d['unmatched_cues'], d['cues']):>20}"
                  f"{d['band_entries']:>8}{evalkit.denom(d['band_hit'], d['band_entries']):>20}"
                  f"{d['gap_long_entries']:>7}{cell(C):>24}{cell(P):>16}{cell(T):>18}"
                  f"{d['repeat_extra']:>10}"
                  f"{evalkit.denom(ht.get('head_ge1', 0), ht.get('pairs', 0)):>18}")
    print("\n读法：『对不上剧本』是幽灵条目的**上限**（库外的文字都落在这里）；『疑似真漏』是**全部未命中**"
          "（长 gap 在这把尺上没有分支解释，一起算；『长gap』列是其中成段漏的条数），逐档 = 该档分母 − 命中。"
          "分母各臂相同，所以三档可以跨臂比；逐条看变了什么用 `${LOG}-vs.log` 的命中集合差。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
