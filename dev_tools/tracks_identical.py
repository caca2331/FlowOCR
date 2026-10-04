"""两份建轨产物是不是**同一个结果**：`<tag>-tracks.json` 去掉 `provenance`（git_head / code_fp / argv 这类追溯字段，两棵树必然不同）
之后逐字节比，外加同目录下所有 `.srt` 逐字节比。给"逐字节相同地提速"这类改动当验收尺（analysis-speed 实验）。

    python dev_tools/tracks_identical.py <目录 A> <目录 B>

退出码：0 = 相同；1 = 有差异（打出第一处）；2 = 用法 / 文件缺失。
⚠ 先拿同一份代码跑两遍（A/A）确认 build_tracks 本身是确定的，再拿它比 A/B——否则比出来的差异分不清是改动还是不确定性。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def canon(p: Path) -> str:
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("provenance", None)
    return json.dumps(d, ensure_ascii=False, sort_keys=False)


def first_diff(a: str, b: str) -> str:
    n = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"第 {n} 个字符起不同：A …{a[max(0, n - 60):n + 60]!r}\n                    B …{b[max(0, n - 60):n + 60]!r}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    da, db = Path(argv[0]), Path(argv[1])
    ta, tb = sorted(da.glob("*-tracks.json")), sorted(db.glob("*-tracks.json"))
    if len(ta) != 1 or len(tb) != 1:
        print(f"每个目录要恰好一份 *-tracks.json：{[p.name for p in ta]} / {[p.name for p in tb]}")
        return 2
    bad = 0
    ca, cb = canon(ta[0]), canon(tb[0])
    if ca != cb:
        bad += 1
        print(f"✗ tracks.json（去掉 provenance）不同，{len(ca)} 对 {len(cb)} 字符；{first_diff(ca, cb)}")
    sa = {p.name: p for p in da.glob("*.srt")}
    sb = {p.name: p for p in db.glob("*.srt")}
    if set(sa) != set(sb):
        bad += 1
        print(f"✗ SRT 文件集合不同：只在 A {sorted(set(sa) - set(sb))}，只在 B {sorted(set(sb) - set(sa))}")
    for name in sorted(set(sa) & set(sb)):
        if sa[name].read_bytes() != sb[name].read_bytes():
            bad += 1
            print(f"✗ {name} 不同")
    if bad:
        return 1
    print(f"✓ 相同：tracks.json（去掉 provenance）+ {len(sa)} 份 SRT 逐字节相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
