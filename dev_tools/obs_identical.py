"""两份 obs 除 `_meta` 之外是不是**逐行逐字节相同**（改了不该改产物的东西之后必跑）。

为什么要专门一个工具：这个项目栽过好几次"改动宣称不动产物、其实动了"
（docs/dev-guide/verification.md「A/B 的规矩」"一次只改一条，一条一对账"：docstring 写着"逐字节相同"，实际 f1 的 runs 从 57,402 变成 57,357）。
`_meta` 里本来就有 wall_sec / stage_sec / config，必须排除；行则必须一字不差。

对不上时打出前几处差异，并按字段归堆——"差在哪个字段"比"差了几行"有用得多。

usage: python dev_tools/obs_identical.py <a.jsonl> <b.jsonl> [--show 5]
退出码：相同 0、不同 1（可以直接写进驱动的 && 链）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("a")
ap.add_argument("b")
ap.add_argument("--show", type=int, default=5)
a = ap.parse_args()


def rows(p: str) -> list[str]:
    with open(p, encoding="utf-8") as fh:
        first = fh.readline()
        if '"_meta"' not in first:
            fh.seek(0)                      # 没有 _meta 的老产物：整份都是行
        return [ln.rstrip("\n") for ln in fh]


ra, rb = rows(a.a), rows(a.b)
print(f"{Path(a.a).name}: {len(ra)} 行 / {Path(a.b).name}: {len(rb)} 行")
if len(ra) != len(rb):
    print(f"❌ 行数就不同（{len(ra)} vs {len(rb)}）")
    raise SystemExit(1)
diff = [i for i, (x, y) in enumerate(zip(ra, rb)) if x != y]
if not diff:
    print("✅ 除 `_meta` 之外**逐行逐字节相同**")
    raise SystemExit(0)
fields: Counter = Counter()
for i in diff:
    x, y = json.loads(ra[i]), json.loads(rb[i])
    for k in set(x) | set(y):
        if x.get(k) != y.get(k):
            fields[k] += 1
print(f"❌ {len(diff)} / {len(ra)} 行不同（{len(diff) / len(ra):.2%}）；差在这些字段："
      + " ".join(f"{k} {v}" for k, v in fields.most_common()))
for i in diff[:a.show]:
    print(f"   行 {i}:\n     A {ra[i][:200]}\n     B {rb[i][:200]}")
raise SystemExit(1)
