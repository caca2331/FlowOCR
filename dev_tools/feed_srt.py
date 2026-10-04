"""把一级产物按匹配器的喂法投影成一份 SRT：给只吃单个 SRT 的参照匹配器（`dev_tools/reference/match_ref.py`，yuka 头对头）用。

    python dev_tools/feed_srt.py <tracks.json> --feed nonoise --out <x.srt>

投影和 `scriptmatch` 同一个函数（`scriptmatch.feed_from_doc`）：挑轨、按 `cue_lines` 取该导出的事件、按时间排序，不判断任何东西。
`nonoise` / `all` 是全部非噪音 / 全部区域轨交错着按时间排（主轨口径直接用 `main.srt`）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr.analyze import scriptmatch as SM  # noqa: E402
from flowocr.artifacts import srtio, tracksio  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracks")
    ap.add_argument("--feed", default="nonoise", choices=("nonoise", "all"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    doc = tracksio.load(Path(a.tracks))
    cues, _, _ = SM.feed_from_doc(doc, a.feed)
    n = srtio.write_srt_blocks(Path(a.out), ((round(c.start * 1e6), round(c.end * 1e6), list(c.lines)) for c in cues))
    print(f"--feed {a.feed}：{n} 条 cue -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
