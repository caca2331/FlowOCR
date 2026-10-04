"""把旧产物的 `_meta.complete` 按**当前判据**重算一遍写回去。

为什么需要（audit-5 §7）：`_meta.complete` 是 2026-09-08 上午那版判据写的，
那版把"解码器提前返回"当成没跑完——**可读到真正的文件结尾也会提前返回**，
而容器元数据还会多报 1%（`gi-s3` 声称 36,361 帧、实际 36,001）。于是那天上午跑的
产物**全部自称未完成**。当天下午修了判据，`out/gamestream` 那九段是手工回填的，
`tmp/recbs` 那批漏了。驱动脚本现在按 `_meta.complete` 判复用，这些文件会被
当成"没跑完"——要么跳过、要么白重跑一遍。

**这里不改判据、也不手改数据**：值由 `ocr_complete.is_complete(frames_advanced,
frames_requested)` 算出来——那本来就是 `complete` 的唯一来源（`run_ocr2` 里就是这么写的），
所以这等于"用当前口径重判一次"。真中断的产物比值低，照样判 false。

**默认只看不写**（`--write` 才落盘），并且逐个打印改了什么：
改数据这件事本身要留痕，别让它变成一次静默的批量覆盖。

    python dev_tools/backfill_complete.py tmp/recbs/*.jsonl
    python dev_tools/backfill_complete.py tmp/recbs/*.jsonl --write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.extract import ocr_complete as oc  # noqa: E402


def verdict(meta: dict) -> bool | None:
    """当前判据的结论；两个计数器缺一个就返回 `None`（**不猜**）。"""
    got, want = meta.get("frames_advanced"), meta.get("frames_requested")
    if got is None or want is None:
        return None
    return oc.is_complete(int(got), int(want))


def rewrite_meta(path: Path, meta: dict) -> None:
    """只换首行。产物动辄几 MB，但一行 JSON 改不了长度就地写不安全——整份重写。"""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = json.dumps({"_meta": meta}, ensure_ascii=False) + "\n"
    path.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obs", nargs="+", help="要重判的 *.jsonl")
    ap.add_argument("--write", action="store_true", help="真写回去（默认只看）")
    a = ap.parse_args(argv)

    changed = skipped = same = 0
    for name in a.obs:
        p = Path(name)
        try:
            with p.open(encoding="utf-8") as fh:
                meta = json.loads(fh.readline())["_meta"]
        except Exception as exc:
            print(f"  [skip] {p.name}：读不到 _meta（{type(exc).__name__}）")
            skipped += 1
            continue
        now, was = verdict(meta), meta.get("complete")
        if now is None:
            print(f"  [skip] {p.name}：没有 frames_advanced/frames_requested，**不猜**")
            skipped += 1
            continue
        if bool(was) == now:
            same += 1
            continue
        got, want = meta["frames_advanced"], meta["frames_requested"]
        print(f"  {p.name}：complete {was} -> **{now}**（推进 {got}/{want}）")
        changed += 1
        if a.write:
            meta["complete"] = now
            meta["complete_backfilled"] = "dev_tools/backfill_complete.py"
            rewrite_meta(p, meta)
    verb = "已写回" if a.write else "**只看没写**（加 --write 才落盘）"
    print(f"{len(a.obs)} 份：改判 {changed}（{verb}）、结论相同 {same}、跳过 {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
