"""`dev_tools/ocr_ab.sh` 的报表：各臂都和 A 比——墙钟（逐轮 + 中位）、整棵进程树的 CPU 秒（有 `.cpu.json` 旁注时）、观测对账、主轨对账。

    python dev_tools/ocr_ab_report.py tmp/ab/bucket [--min-conf 0.5]

观测对账**不止看文本**（2026-09-10 审查）：上一版只报"文本改 34 条"，
而端到端六段里另有 16 行**文本没变、conf 跨过了 build_tracks 的 `--min-conf 0.5`**——
那一行进不进下游是会变的。所以这里把"下游命运可能变了的行"单独数一栏：
文本变了 **或** 跨过了 conf 门。

每臂同一段跑了几遍，就顺带报**同一条臂自己是不是确定的**（A1 vs A2）：
臂内不确定时，A/B 的差里混着臂内抖动，不能全算给旋钮。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）


def load_obs(path: Path) -> tuple[dict, list[dict]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0])["_meta"], [json.loads(x) for x in lines[1:]]


def compare_obs(a: list[dict], b: list[dict], min_conf: float = 0.5) -> dict:
    """两份观测对账。**框序列相同就逐行比；不同就按 (帧, 框) 配对比共有的那批**。

    ⚠ **2026-09-20 修的一个会骗人的 0**：原来"框序列不同"时直接返回
    `text_diff=None`（这个保守选择本身是对的），可汇总那边写的是 `tot[k] += c[k] or 0`
    ——**`None` 被当成 0 加进去**，再拿**全部**行当分母，于是印出
    "观测 39514 行：文本改 0（0.000%）"，而真相是**一行都没比**。
    照着那张表读会得出"ORT 不改产物"，实测是文本 97.6%~98.1% 相同、
    每段 23~25 行跨 conf 0.5 门。这正是 docs/dev-guide/verification.md「报数口径」 那条
    「报一个『0』的时候，连它筛掉了多少一起报」。

    现在换 det 引擎这种**框会变**的臂也能对账：`cmp_rows` 是真正比过的行数，
    调用方必须拿它当分母（`rows_a` 只是 A 臂的总行数）。
    配对口径和 `dev_tools/obs_textdiff.py` 一致——**判据只有一份**，
    那边现在 import 这里。
    """
    same_boxes = len(a) == len(b) and all(
        x["frame"] == y["frame"] and x["box"] == y["box"] for x, y in zip(a, b))
    ka = {(x["frame"], tuple(x["box"])): x for x in a}
    kb = {(y["frame"], tuple(y["box"])): y for y in b}
    common = sorted(ka.keys() & kb.keys())
    conf = lambda o: o.get("conf") or 0.0
    td = [k for k in common if ka[k].get("text") != kb[k].get("text")]
    gc = [k for k in common if (conf(ka[k]) >= min_conf) != (conf(kb[k]) >= min_conf)]
    return {"rows_a": len(a), "rows_b": len(b), "same_boxes": same_boxes,
            "identical": a == b,
            # **真正比过的行**：框序列不同时它 < rows_a，报数必须用它当分母
            "cmp_rows": len(common),
            "only_a": len(ka) - len(common), "only_b": len(kb) - len(common),
            "text_diff": len(td), "gate_cross": len(gc),
            "gate_cross_same_text": len(set(gc) - set(td)),
            "fate_changed": len(set(td) | set(gc))}


def cpu_of(obs: Path) -> dict | None:
    """`dev_tools/jobcpu.py` 写的旁注（`<obs>.cpu.json`）；没有 / 记账不可用返回 None。
    `cpu_sec` 含起动（import、建模型）——两臂一样付，比差值；`peak_commit_mb` 是同一时刻整棵树的提交内存峰值。"""
    p = obs.with_suffix(".cpu.json")
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return None if "error" in d else d


def main_srt_of(trdir: Path) -> Path | None:
    from flowocr.artifacts import tracksio

    tj = next(trdir.glob("*-tracks.json"), None)
    if tj is None:
        return None
    name = tracksio.load(tj)["provenance"].get("main_srt")
    return trdir / name if name else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--min-conf", type=float, default=0.5,
                    help="build_tracks 的 --min-conf 默认值；跨过它的行下游命运会变")
    a = ap.parse_args()
    from flowocr.artifacts import srtio

    d = Path(a.dir)
    runs: dict[str, dict[str, dict[int, Path]]] = {}
    for p in d.glob("*.jsonl"):
        m = re.fullmatch(r"(.+)-([A-E])-(\d+)\.jsonl", p.name)
        if m:
            runs.setdefault(m.group(1), {}).setdefault(m.group(2), {})[int(m.group(3))] = p
    if not runs:
        print(f"{d} 下没有 <id>-<臂>-<k>.jsonl")
        return 1
    arms = (d / "arms.txt").read_text(encoding="utf-8") if (d / "arms.txt").exists() else ""
    print(arms.strip())
    others = sorted({arm for r in runs.values() for arm in r} - {"A"})
    print("（「框同」一栏写「全同」= 两臂的观测行逐行相同，_meta 以外逐字节等价）")
    for arm in others:
        report_arm(d, runs, arm, a.min_conf, srtio)
    return 0


def report_arm(d: Path, runs: dict, arm: str, min_conf: float, srtio) -> None:
    """一条臂和 A 比：墙钟（逐轮 + 中位）、观测对账、臂内确定性、主轨。"""
    tot_a = tot_b = 0.0
    cpu_rows: list[tuple] = []
    tot = {"rows": 0, "cmp_rows": 0, "only_a": 0, "only_b": 0, "text_diff": 0,
           "gate_cross_same_text": 0, "fate_changed": 0}
    print(f"\n==== {arm} 对 A ====")
    print(f"{'片段':<20}{'A 中位':>9}{arm + ' 中位':>9}{'Δ':>8}  {'逐轮 Δ':<28}"
          f"{'行':>7}{'比了':>7}{'框同':>5}{'文本改':>7}{'只跨门':>7}{'命运变':>7}  臂内确定  主轨")
    for sid in sorted(runs):
        ra, rb = runs[sid].get("A", {}), runs[sid].get(arm, {})
        ks = sorted(set(ra) & set(rb))
        if not ks:
            print(f"{sid:<20} 两臂没有成对的产物，跳过")
            continue
        ma = {k: load_obs(ra[k]) for k in ks}
        mb = {k: load_obs(rb[k]) for k in ks}
        wa = [ma[k][0]["wall_sec"] for k in ks]
        wb = [mb[k][0]["wall_sec"] for k in ks]
        med_a, med_b = statistics.median(wa), statistics.median(wb)
        tot_a += med_a
        tot_b += med_b
        rounds = " ".join(f"{wb[i] / wa[i] - 1:+.1%}" for i in range(len(ks)))
        ca, cb = [cpu_of(ra[k]) for k in ks], [cpu_of(rb[k]) for k in ks]
        if all(ca) and all(cb):
            cpu_rows.append((sid, [x["cpu_sec"] for x in ca], [x["cpu_sec"] for x in cb],
                             max(x["peak_commit_mb"] for x in ca), max(x["peak_commit_mb"] for x in cb),
                             max((x.get("gpu_mem_delta_mb", -1) for x in ca), default=-1),
                             max((x.get("gpu_mem_delta_mb", -1) for x in cb), default=-1)))
        c = compare_obs(ma[ks[0]][1], mb[ks[0]][1], min_conf)
        det = []
        for name, mm in (("A", ma), (arm, mb)):
            if len(mm) > 1:
                det.append(f"{name}{'是' if all(mm[k][1] == mm[ks[0]][1] for k in ks) else '**否**'}")
        sa, sb = main_srt_of(d / f"tr-{sid}-A"), main_srt_of(d / f"tr-{sid}-{arm}")
        if sa and sb and sa.exists() and sb.exists():
            if sa.read_bytes() == sb.read_bytes():
                main = f"逐字节相同（{srtio.count_cues(sa)} 条）"
            else:
                main = f"不同：{srtio.count_cues(sa)} → {srtio.count_cues(sb)} 条"
        else:
            main = "（没建轨）"
        tot["rows"] += c["rows_a"]
        tot["rows_b_tot"] = tot.get("rows_b_tot", 0) + c["rows_b"]
        tot["cmp_rows"] += c["cmp_rows"]        # **分母用这个**，不是 rows
        tot["only_a"] += c["only_a"]
        tot["only_b"] += c["only_b"]            # ⚠ **两侧都要报**，见下面汇总那段
        for k in ("text_diff", "gate_cross_same_text", "fate_changed"):
            tot[k] += c[k] or 0
        cell = (lambda v: "—" if v is None else str(v))
        print(f"{sid:<20}{med_a:>9.2f}{med_b:>9.2f}{med_b / med_a - 1:>+8.1%}  {rounds:<28}"
              f"{c['rows_a']:>7}{c['cmp_rows']:>7}"
              f"{('全同' if c['identical'] else '是') if c['same_boxes'] else '否':>5}"
              f"{cell(c['text_diff']):>7}"
              f"{cell(c['gate_cross_same_text']):>7}{cell(c['fate_changed']):>7}  "
              f"{'/'.join(det) or '—':<8}  {main}")
    if cpu_rows:
        print("\n  CPU（整棵进程树的用户 + 内核秒，含起动；dev_tools/jobcpu.py）、提交内存峰值、整卡显存增量（峰值减起前，逐轮最大；-1 = 没记）：")
        print(f"  {'片段':<20}{'A 中位':>9}{arm + ' 中位':>9}{'Δ':>8}  {'逐轮 Δ':<22}{'A 峰值 MB':>10}{arm + ' 峰值 MB':>10}"
              f"{'A 显存+MB':>10}{arm + ' 显存+MB':>10}")
        ca_tot = cb_tot = 0.0
        for sid, xa, xb, pa, pb, ga, gb in cpu_rows:
            ma_, mb_ = statistics.median(xa), statistics.median(xb)
            ca_tot, cb_tot = ca_tot + ma_, cb_tot + mb_
            rr = " ".join(f"{xb[i] / xa[i] - 1:+.1%}" for i in range(len(xa)))
            print(f"  {sid:<20}{ma_:>9.1f}{mb_:>9.1f}{mb_ / ma_ - 1:>+8.1%}  {rr:<22}{pa:>10.0f}{pb:>10.0f}{ga:>10}{gb:>10}")
        print(f"  CPU 合计（各段中位之和）：{ca_tot:.1f} → {cb_tot:.1f} s = {cb_tot / ca_tot - 1:+.1%}"
              + ("" if len(cpu_rows) == len(runs) else f"（只有 {len(cpu_rows)} / {len(runs)} 段两臂都有旁注）"))
    if tot_a:
        print(f"合计（各段中位之和）：{tot_a:.2f} → {tot_b:.2f} s = {tot_b / tot_a - 1:+.1%}"
              f"（**加总会被框多的段主导**，跨素材比别拿这个数）")
        r = max(1, tot["cmp_rows"])
        print(f"观测：A {tot['rows']} 行 / {arm} {tot['rows_b_tot']} 行，"
              f"**(帧,框) 配得上、真比过的 {tot['cmp_rows']} 行**；"
              f"**A 独有 {tot['only_a']}、{arm} 独有 {tot['only_b']}**"
              + (" ——⚠ **一行都没比上，下面这些数别读成 0**" if not tot["cmp_rows"] else ""))
        print(f"  文本改 {tot['text_diff']}（{tot['text_diff'] / r:.3%}）、"
              f"文本没变但跨过 conf {min_conf} 门 {tot['gate_cross_same_text']}、"
              f"下游命运可能变的合计 {tot['fate_changed']}（{tot['fate_changed'] / r:.3%}）")
        print(f"  ⚠ 上面这三个数**只描述共有框里的变化**——"
              f"{arm} 新出现的 {tot['only_b']} 行和 A 里消失的 {tot['only_a']} 行"
              f"**一条都不在里面**，而它们照样会改下游（2026-09-20 复审：原来只报了 A 独有那一侧）")


if __name__ == "__main__":
    raise SystemExit(main())
