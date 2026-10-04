"""聚类的尺的复标：盲抽样 / 对账 / 摆帧错行的波及面（cluster-ruler 计划 §6.5）。

    python dev_tools/slotpairs_relabel.py sample --seed 20260924 --quota same=18,different=18,widget=7,unsure=7 --out tmp/relabel
        -> <out>/blind.json（只有 id / 图 / 两边 OCR 文本，给标注者）+ <out>/key.json（原标签，**标完之前别打开**）
    python dev_tools/slotpairs_relabel.py compare data/gt/review/slotpairs-relabel-0924.json
        -> 一致率（Wilson 区间）、4×4 混淆矩阵、按批 / 按层、方向、逐条不一致
    python dev_tools/slotpairs_relabel.py exposure --arms median,pooled
        -> 摆帧画框错行（动过的 run，黄框离 t 时刻真实位置 ≥ 1.5 行高）的对有多少，其中多少是两臂分出胜负的对
    python dev_tools/slotpairs_relabel.py rerender --out tmp/slotpairs-rerender
        -> 画错行的对用修过的画框（slot_pairs.box_at）重摆图，出盲图清单 blind.json + 原标签 key.json

盲的规矩：抽样只把 id / 图 / 文本写进 blind.json；复标结果单独存一份（每对 label / note，文件头记 labeled_by / 时刻 / 协议），
对账时才和原标签合起来。复标文件是盲标的原始记录，事后觉得标错了也不改它，在文档里注明。
复标 / 复核文件放 `data/gt/review/`：放在 `data/gt/` 下会被真值的 `slotpairs-*.json` glob 当成真值读进去。
换上复核标签重算各臂：`python -m flowocr.analyze.slot_learned --lofo --relabel <复核文件>`（只换测试集标签）。
⚠ 按原标签分层抽的话批间对数不均，"按批"那一行只能看方向。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

from flowocr import paths

LABELS = ("same", "widget", "different", "unsure")
MISDRAWN_LINES = 1.5


def gt_files() -> list[Path]:
    return sorted(Path(p) for p in glob.glob(str(paths.data_root() / "data/gt/slotpairs-*-s[0-9].json")))


def all_items() -> dict[str, dict]:
    out = {}
    for f in gt_files():
        for it in json.loads(f.read_text(encoding="utf-8"))["items"]:
            out[it["id"]] = it
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return c - h, c + h


def batch_of(pid: str) -> str:
    return pid.rsplit("-", 1)[0].rsplit("-", 1)[1]      # f5-s3-012 -> s3


def cmd_sample(a) -> int:
    quota = {k: int(v) for k, v in (x.split("=") for x in a.quota.split(","))}
    by = defaultdict(list)
    for it in all_items().values():
        if it.get("label"):
            by[it["label"]].append(it)
    rnd = random.Random(a.seed)
    pick = []
    for lab, n in quota.items():
        pick += rnd.sample(sorted(by[lab], key=lambda x: x["id"]), min(n, len(by[lab])))
    rnd.shuffle(pick)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "blind.json").write_text(json.dumps(
        [{"id": it["id"], "image": it["image"], "a": it["a"]["key"][3], "b": it["b"]["key"][3]} for it in pick],
        ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(
        {it["id"]: {k: it.get(k) for k in ("label", "labeled_by", "note", "stratum")} for it in pick},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(pick)} 对 -> {out / 'blind.json'}（原标签在 key.json，标完之前别打开）")
    return 0


def cmd_compare(a) -> int:
    rel = json.loads(Path(a.relabel).read_text(encoding="utf-8"))
    mine = {it["id"]: it for it in rel["items"]}
    orig = all_items()
    cm, per_batch, per_stratum, dis = Counter(), defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), []
    for i, m in mine.items():
        o = orig[i]
        x, y = o["label"], m["label"]
        cm[(x, y)] += 1
        for d, k in ((per_batch, batch_of(i)), (per_stratum, o.get("stratum"))):
            d[k][0] += x == y
            d[k][1] += 1
        if x != y:
            dis.append((i, x, y, o.get("labeled_by"), (o.get("note") or "")[:80], m.get("note", "")[:60]))
    n = len(mine)
    k = sum(v for (x, y), v in cm.items() if x == y)
    lo, hi = wilson(k, n)
    print(f"复标者 {rel.get('labeled_by')}：一致 {k}/{n} = {k / n:.0%}（Wilson 95% {lo:.0%}~{hi:.0%}）——自洽率，不是独立检验")
    print("\n行 = 原标签，列 = 复标")
    print(f"{'':10}" + "".join(f"{c:>10}" for c in LABELS))
    for r in LABELS:
        print(f"{r:10}" + "".join(f"{cm[(r, c)]:>10}" for c in LABELS))
    print("\n按批（按原标签分层抽时批间对数不均，只看方向）：", {b: f"{v[0]}/{v[1]}" for b, v in sorted(per_batch.items())})
    print("按层：", {s: f"{v[0]}/{v[1]}" for s, v in sorted(per_stratum.items(), key=lambda x: str(x[0]))})
    scored = [d for d in dis if {"same", "different"} & {d[1], d[2]}]
    flip = [d for d in dis if {d[1], d[2]} == {"same", "different"}]
    print(f"\n不一致 {len(dis)}；计分身份变了 {len(scored)}；same <-> different 翻转 {len(flip)}")
    print("方向：原 same -> 非 same", sum(d[1] == "same" for d in dis), "；原非 same -> same", sum(d[2] == "same" for d in dis))
    for d in dis:
        print(" | ".join(str(x) for x in d))
    return 0


def cmd_exposure(a) -> int:
    from flowocr.analyze import cluster_layers as CL
    from flowocr.analyze import slot_pairs as SP
    arm_a, arm_b = a.arms.split(",")
    by_tag = defaultdict(list)
    seeds = {int(x) for x in a.seeds.split(",")} if a.seeds else None
    strata = set(a.strata.split(",")) if a.strata else None
    for f in gt_files():
        d = json.loads(f.read_text(encoding="utf-8"))
        if seeds is None or d["seed"] in seeds:
            by_tag[d["tag"]].append(d)
    c = Counter()
    for tag, docs in by_tag.items():
        L = CL.load(tag, verbose=False)
        idx = run_index(L, SP)
        asg = {n: SP.assignment(L, CL.stitch(L, **SP.ARMS[n])) for n in (arm_a, arm_b)}
        for d in docs:
            for it in d["items"]:
                bad = misdrawn(L, SP, idx, it) >= MISDRAWN_LINES
                c["对"] += 1
                c["错行"] += bad
                if it["label"] not in ("same", "different") or (strata and it.get("stratum") not in strata):
                    continue
                c["计分"] += 1
                c["计分且错行"] += bad
                v = SP.verdicts(asg, it)
                if v is None or v[arm_a] == v[arm_b]:
                    continue
                win = arm_a if v[arm_a] else arm_b
                c[f"{win} 胜"] += 1
                c[f"{win} 胜且错行"] += bad
    print(f"摆帧错行（≥ {MISDRAWN_LINES} 行高）：{c['错行']}/{c['对']} 对，计分的 {c['计分且错行']}/{c['计分']}")
    for w in (arm_a, arm_b):
        print(f"{w} 胜 {c[w + ' 胜']} 对，其中错行 {c[w + ' 胜且错行']}")
    wa, wb = c[arm_a + " 胜"], c[arm_b + " 胜"]
    ba, bb = c[arm_a + " 胜且错行"], c[arm_b + " 胜且错行"]
    print(f"逐对 {wa} : {wb}；错行的对标签全翻时最多摆到 {wa - ba} : {wb + ba}（{arm_a} 最不利）~ {wa + bb} : {wb - bb}（{arm_b} 最不利）")
    print(f"⚠ 这是上界（错行的对重摆图复核之前，不知道标签错了几对）；分集 / 批 / 层按参数取（seeds={a.seeds or '全部'} strata={a.strata or '全部'}）")
    return 0


def run_index(L, SP) -> dict[str, int]:
    """run 的身份（`run_key`）-> `L.runs` 下标。"""
    return {json.dumps(SP.run_key(r)): i for i, r in enumerate(L.runs)}


def misdrawn(L, SP, idx: dict[str, int], it) -> float:
    """这一对里画框离 t 时刻真实位置最远的那一侧，按行高算（旧摆帧用 `r.box` 画在前 4 秒中点那一帧上）。"""
    worst = 0.0
    for s in "ab":
        i = idx.get(json.dumps(it[s]["key"]))
        if i is None:
            continue
        r = L.runs[i]
        t = (r.t_start + min(r.t_end, r.t_start + 4_000_000)) / 2
        b0, b1 = r.box, SP.box_at(r, t)
        worst = max(worst, (abs(b0[1] - b1[1]) + abs(b0[0] - b1[0])) / max(1.0, b1[3] - b1[1]))
    return worst


def cmd_rerender(a) -> int:
    """画错行的对用修过的画框重摆图，出盲图清单（只有 id / 新图 / 两边文本）；原标签照 sample 的做法另存 key.json。"""
    from flowocr.analyze import cluster_layers as CL
    from flowocr.analyze import slot_pairs as SP
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    blind, key = [], {}
    by_tag = defaultdict(list)
    for f in gt_files():
        d = json.loads(f.read_text(encoding="utf-8"))
        by_tag[d["tag"]].append(d)
    for tag, docs in by_tag.items():
        L = CL.load(tag, verbose=False)
        idx = run_index(L, SP)
        for d in docs:
            for it in d["items"]:
                if misdrawn(L, SP, idx, it) < MISDRAWN_LINES:
                    continue
                img = out / f"{it['id']}.jpg"
                SP.render(L, {"runs": [idx[json.dumps(it[s]["key"])] for s in "ab"]}, img)
                blind.append({"id": it["id"], "image": str(img), "a": it["a"]["key"][3], "b": it["b"]["key"][3]})
                key[it["id"]] = {k: it.get(k) for k in ("label", "labeled_by", "note", "stratum")}
    random.Random(a.seed).shuffle(blind)
    (out / "blind.json").write_text(json.dumps(blind, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(blind)} 对重摆图 -> {out}（原标签在 key.json，标完之前别打开）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--quota", default="same=18,different=18,widget=7,unsure=7")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_sample)
    c = sub.add_parser("compare")
    c.add_argument("relabel")
    c.set_defaults(fn=cmd_compare)
    e = sub.add_parser("exposure")
    e.add_argument("--arms", default="median,pooled")
    e.add_argument("--seeds", default="", help="只算这几批，如 2,3（39 : 16 那个读数的口径）")
    e.add_argument("--strata", default="", help="只算这几层，如 S2,S3,S4（率层）")
    e.set_defaults(fn=cmd_exposure)
    rr = sub.add_parser("rerender")
    rr.add_argument("--out", required=True)
    rr.add_argument("--seed", type=int, default=20260924)
    rr.set_defaults(fn=cmd_rerender)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
