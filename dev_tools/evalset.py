"""结构化评测的骨架：固定样本清单 -> 逐条记录 -> 两臂逐条配对（共同集合、只在一边、判定怎么变的）。

    python dev_tools/evalset.py run <清单.json> --arm final --out tmp/ev/final.jsonl
    python dev_tools/evalset.py compare tmp/ev/final.jsonl tmp/ev/frozen.jsonl

**为什么**：各把尺各自报合计，比臂时最容易错在"两臂量的不是同一批条目"（打字机真值上一条高误差的真值在新臂配不上，
就从总账里消失、读成打平）。这里把每把尺的输出统一成**逐条记录**，比臂只走 `compare`：
按 `(样本, 条目)` 配对，共同集合上比数、只在一边测到的单列、判定的转移矩阵（完整 -> 位置错 几条）逐格列出，
`metrics` 里变了的数（交付病例的 `time_ok`、`sim`）逐条列出——判定不变的也列。

- **记录**（jsonl 一行一条）：`suite`（哪把尺）、`sample`（样本 id）、`item`、`kind`（分项）、`verdict`（判定）、`metrics`（数值，可空）。
  `verdict` 用各尺自己的词，但 `MEASURED` 之外的（`未测`）不进共同集合。
- **清单**（`run` 的输入）：`{"name": …, "split": "regression|holdout", "suite": "delivery", "samples": [{"id": "gi", "case": "data/gt/delivery-gi.json",
  "ass": "out/…/{arm}/gi/c-default_all.ass"}]}`——`{arm}` 换成 `--arm`；相对路径按数据根解析。
  `split`：`regression` = 被反复调参的固定回归集（原 `validation`），`holdout` = 按素材隔离、只拿来下泛化结论的集合。
- 目前接上的尺：`delivery`（`dev_tools/delivery_cases.py` 的交付病例）。接别的尺 = 写一个 `adapter(样本) -> [记录]` 登记进 `SUITES`。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr.artifacts.evalkit import denom, require_nonzero  # noqa: E402
from flowocr.paths import data_root  # noqa: E402

UNMEASURED = "未测"


def delivery_adapter(sample: dict, arm: str) -> list[dict]:
    import delivery_cases as DC
    root = data_root()
    case = json.loads((root / sample["case"]).read_text(encoding="utf-8"))
    ass = root / sample["ass"].format(arm=arm)
    if not ass.is_file():
        return [{"item": it["id"], "kind": it["kind"], "verdict": UNMEASURED, "metrics": {}}
                for m in case["moments"] for it in m["items"]]
    out = []
    for r in DC.score_case(case, ass):
        met = {"sim": r["sim"]}
        if "on_ok" in r or "off_ok" in r:
            met["time_ok"] = int(r.get("on_ok", True) and r.get("off_ok", True))
        out.append({"item": r["id"], "kind": r["kind"], "verdict": r["verdict"], "metrics": met})
    return out


SUITES = {"delivery": delivery_adapter}


def cmd_run(a) -> int:
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    fn = SUITES[man["suite"]]
    rows = []
    for s in man["samples"]:
        for r in fn(s, a.arm):
            rows.append({"suite": man["suite"], "set": man["name"], "split": man.get("split", ""), "arm": a.arm,
                         "sample": s["id"], **r})
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    c = Counter(r["verdict"] for r in rows)
    print(f"{man['name']}（{man['suite']}，{man.get('split')}）臂 {a.arm}：{len(rows)} 条；{dict(c)} -> {out}")
    return 0


def load(p: str) -> dict[tuple, dict]:
    rows = [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
    return {(r["suite"], r["sample"], r["item"]): r for r in rows}


def metric_changes(A: dict, B: dict, common: list[tuple]) -> dict[str, list[tuple]]:
    """共同集合上，两臂判定相同或不同、`metrics` 里同名的数变了的条目：`{指标: [(键, A 的值, B 的值)]}`。
    只比判定会把"判定不变、时段变错"（交付病例的 `time_ok` 1 -> 0）读成不变（验收 peer 2026-10-06）。"""
    out: dict[str, list[tuple]] = {}
    for k in common:
        ma, mb = A[k].get("metrics") or {}, B[k].get("metrics") or {}
        for m in sorted(set(ma) | set(mb)):
            if ma.get(m) != mb.get(m):
                out.setdefault(m, []).append((k, ma.get(m), mb.get(m)))
    return out


def cmd_compare(a) -> int:
    A, B = load(a.a), load(a.b)
    arm_a = next(iter(A.values()))["arm"] if A else a.a
    arm_b = next(iter(B.values()))["arm"] if B else a.b
    keys = sorted(set(A) | set(B))
    common = [k for k in keys if k in A and k in B and UNMEASURED not in (A[k]["verdict"], B[k]["verdict"])]
    only_a = [k for k in keys if k in A and A[k]["verdict"] != UNMEASURED and (k not in B or B[k]["verdict"] == UNMEASURED)]
    only_b = [k for k in keys if k in B and B[k]["verdict"] != UNMEASURED and (k not in A or A[k]["verdict"] == UNMEASURED)]
    require_nonzero(len(common), "两臂都测到的条目", "清单、臂名或产物路径对不上")
    print(f"两臂都测到 {denom(len(common), len(keys))}；只有 {arm_a} 测到 {len(only_a)}、只有 {arm_b} 测到 {len(only_b)}")
    for kind in sorted({A.get(k, B.get(k))["kind"] for k in common}) + ["全部"]:
        sel = [k for k in common if kind in ("全部", A[k]["kind"])]
        tr = Counter((A[k]["verdict"], B[k]["verdict"]) for k in sel)
        moved = {f"{x}->{y}": n for (x, y), n in sorted(tr.items()) if x != y}
        print(f"  {kind:<10}共同 {len(sel)}：不变 {sum(n for (x, y), n in tr.items() if x == y)}"
              + (f"；变了 {moved}" if moved else ""))
    print("\n判定变了的条目：")
    for k in common:
        if A[k]["verdict"] != B[k]["verdict"]:
            print(f"  {k[1]}/{k[2]}（{A[k]['kind']}）{A[k]['verdict']} -> {B[k]['verdict']}")
    mc = metric_changes(A, B, common)
    if mc:
        print("\n数值变了的条目（判定不变的也列）：")
        for m, lst in mc.items():
            print(f"  {m}：{len(lst)} 条")
            for k, va, vb in lst:
                print(f"    {k[1]}/{k[2]}（{A[k]['kind']}，{A[k]['verdict']} -> {B[k]['verdict']}）{va} -> {vb}")
    for lab, lst in ((f"只有 {arm_a} 测到", only_a), (f"只有 {arm_b} 测到", only_b)):
        if lst:
            print(f"{lab}：" + "、".join(f"{k[1]}/{k[2]}" for k in lst))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("manifest")
    r.add_argument("--arm", required=True)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.set_defaults(fn=cmd_compare)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
