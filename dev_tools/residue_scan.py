#!/usr/bin/env python3
"""目录外残留扫描：探索前后各拍一张快照，再 diff。

只看"探索会污染的地方"——用户级的模型/包缓存目录，以及几个常见的隐藏家目录。
不递归到底：每个监视根只展开到 --depth 层（默认 2），记录名字、类型、大小、mtime。

用法：
    python dev_tools/residue_scan.py snapshot --label before-rapidocr
    python dev_tools/residue_scan.py snapshot --label after-rapidocr
    python dev_tools/residue_scan.py diff --before before-rapidocr --after after-rapidocr
    python dev_tools/residue_scan.py list

快照写到 data/residue/<label>.json —— 它是参照物，不是产物，别放 tmp/。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dev_tools"))
sys.path.insert(0, str(REPO / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr import paths  # noqa: E402

SNAP_DIR = paths.data_root() / "data" / "residue"   # 快照是本机状态，不跟代码走：槽里跑也落回主 checkout

# 监视根：探索型依赖最常写入的用户级位置，(路径, 深度上限覆盖)。
# 新发现的坑请加在这里并同步残留清单。
WATCH_ROOTS = [
    ("~/.cache", None),
    ("~/.paddleocr", None),
    ("~/.paddlex", None),
    ("~/.paddle", None),
    ("~/.EasyOCR", None),
    ("~/.keras", None),
    ("~/.insightface", None),
    ("~/.torch", None),
    ("~/.u2net", None),
    ("~/AppData/Local/uv", None),
    ("~/AppData/Local/pip", None),
    # %TEMP% 故意不看：顶层就有两万多条，噪声淹没信号，且系统会自清
    ("~/AppData/Roaming", 1),
    ("~/AppData/Local", 1),
]

MAX_ENTRIES = 20000


def _iter_entries(root: Path, depth: int):
    """按层展开 root，最多 depth 层；返回 (相对路径, 类型, 大小, mtime)。"""
    if not root.exists():
        return
    stack = [(root, 0)]
    count = 0
    while stack:
        cur, lvl = stack.pop()
        try:
            children = sorted(cur.iterdir())
        except (PermissionError, OSError):
            continue
        for child in children:
            try:
                st = child.stat()
            except (PermissionError, OSError, ValueError):
                continue
            is_dir = child.is_dir()
            yield (
                child.relative_to(root).as_posix(),
                "d" if is_dir else "f",
                0 if is_dir else st.st_size,
                int(st.st_mtime),
            )
            count += 1
            if count >= MAX_ENTRIES:
                return
            if is_dir and lvl + 1 < depth:
                stack.append((child, lvl + 1))


def cmd_snapshot(args: argparse.Namespace) -> int:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    roots: dict[str, dict[str, list]] = {}
    for spec, depth_override in WATCH_ROOTS:
        root = Path(os.path.expanduser(spec))
        depth = args.depth if depth_override is None else min(args.depth, depth_override)
        entries = {rel: [kind, size, mtime] for rel, kind, size, mtime in _iter_entries(root, depth)}
        roots[spec] = {"path": str(root), "exists": root.exists(), "entries": entries}
        print(f"  {spec:<28} {len(entries):>6} entries", file=sys.stderr)
    out = SNAP_DIR / f"{args.label}.json"
    # 同名 label 不许静默覆盖。踩过：整片跑完拍的 after-fullfilm 被后来一次
    # 重扫盖掉，文件名还叫"整片跑完"，内容却是几小时后的状态——快照是**参照物**，
    # 被覆盖就等于参照物没了，而且不报错、看不出来。
    if out.exists() and not args.force:
        sys.exit(f"快照已存在：{out}——它是参照物，覆盖了就找不回来。"
                 f"换个 label，或者确实要重拍就加 --force")
    out.write_text(json.dumps({"depth": args.depth, "roots": roots}, ensure_ascii=False), encoding="utf-8")
    print(f"snapshot -> {out}")
    return 0


def _load(label: str) -> dict:
    path = SNAP_DIR / f"{label}.json"
    if not path.exists():
        sys.exit(f"no such snapshot: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_diff(args: argparse.Namespace) -> int:
    before, after = _load(args.before), _load(args.after)
    total_new = total_changed = 0
    for spec, aft in after["roots"].items():
        bef_entries = before["roots"].get(spec, {}).get("entries", {})
        aft_entries = aft["entries"]
        new = [k for k in aft_entries if k not in bef_entries]
        changed = [
            k for k in aft_entries
            if k in bef_entries and aft_entries[k][1] != bef_entries[k][1] and aft_entries[k][0] == "f"
        ]
        gone = [k for k in bef_entries if k not in aft_entries]
        if not (new or changed or gone):
            continue
        print(f"\n=== {spec}  ({aft['path']})")
        for k in sorted(new)[: args.limit]:
            kind, size, _ = aft_entries[k]
            print(f"  + {kind} {size:>12,}  {k}")
        if len(new) > args.limit:
            print(f"  + ... 还有 {len(new) - args.limit} 条")
        for k in sorted(changed)[: args.limit]:
            print(f"  ~ f {bef_entries[k][1]:>12,} -> {aft_entries[k][1]:,}  {k}")
        for k in sorted(gone)[: args.limit]:
            print(f"  - {bef_entries[k][0]} {k}")
        total_new += len(new)
        total_changed += len(changed)
    print(f"\n合计：新增 {total_new}，大小变化 {total_changed}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    if not SNAP_DIR.exists():
        print("(还没有快照)")
        return 0
    for p in sorted(SNAP_DIR.glob("*.json")):
        print(f"{p.stem:<32} {p.stat().st_size:>10,} bytes")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="拍一张快照")
    s.add_argument("--label", required=True)
    s.add_argument("--depth", type=int, default=2)
    s.add_argument("--force", action="store_true", help="允许覆盖同名快照")
    s.set_defaults(func=cmd_snapshot)

    d = sub.add_parser("diff", help="比较两张快照")
    d.add_argument("--before", required=True)
    d.add_argument("--after", required=True)
    d.add_argument("--limit", type=int, default=40, help="每个监视根最多打印多少条")
    d.set_defaults(func=cmd_diff)

    sub.add_parser("list", help="列出已有快照").set_defaults(func=cmd_list)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
