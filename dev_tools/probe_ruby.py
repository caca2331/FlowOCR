"""注音判据（`build_tracks.ruby_runs`）的检验探针：逐条假名候选重放判据，列出卡在哪一条，可选出摆帧联系表给人 / agent 标注。

    python dev_tools/pyrun.py dev_tools/probe_ruby.py out/x/x-tracks.json --out tmp/ruby-x.json [--exclude 3600-6000]
    python dev_tools/pyrun.py dev_tools/probe_ruby.py out/x/x-tracks.json --out tmp/ruby-x.json --video x.mp4 --sheets tmp/sheets/x
    python dev_tools/pyrun.py dev_tools/probe_ruby.py --score data/gt/ruby-holdout.json --rows tmp/ruby-x.json   # 按标注算准确率

- **输入要 `--refine off` 的 tracks**：判据在回抠之前跑，回抠改了事件的起止，"同屏 ≥ 自己时长一半"那条会重放不准。
  探针先拿现行 `ruby_runs` 重放一遍、和产物里的 `ruby` 标对数，对不上会印出来。
- **状态**（只列过了前三条——全假名 2–16 字、同屏、字高比和紧贴上方——的候选）：`ruby` 全过；`c4` 只卡"居中压在正文长度的行上"；
  `c5` 只卡"覆盖的字里汉字过半"；`c45` 两条都卡。前三条就不过的不列（召回只量得到这批几何候选里的）。
- `--exclude a-b`（秒）排除参与过调参的窗口。联系表每格红框 = 候选、绿框 = 配上的正文，取候选首帧（地图拖动时首帧之外框会错位）。
- 标注文件：`{"labeled_by": ..., "sets": {<名>: {"tracks": ..., "labels": {<事件 id>: "ruby" | "name" | ...}}}}`；
  `--score` 按状态 × 标签交叉计数。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from types import SimpleNamespace

from flowocr.analyze.build_tracks import RUBY_H, ruby_runs, script_of
from flowocr.analyze import uigate
from flowocr.artifacts import evalkit

BUCKET_US = 10_000_000


def as_run(e: dict) -> SimpleNamespace:
    b = e["box"]
    return SimpleNamespace(box=b, text=e["text"], t_start=e["t_start"], t_end=e["t_end"],
                           h=b[3] - b[1], w=b[2] - b[0], cx=(b[0] + b[2]) / 2, eid=e["id"])


def kana_only(t: str) -> bool:
    s = "".join(ch for ch in t if not ch.isspace() and ch not in "ー・")
    return 2 <= len(s) <= 16 and all(script_of(ch) == "kana" for ch in s)


def classify(runs, i, bodies):
    """和 `ruby_runs` 同一套几何；前三条过了的正文里取最好的那个状态。"""
    r = runs[i]
    dur = max(1, r.t_end - r.t_start)
    seen, best = set(), None
    for k in range(r.t_start // BUCKET_US, r.t_end // BUCKET_US + 1):
        for j in bodies.get(k, ()):
            if j in seen or j == i:
                continue
            seen.add(j)
            b = runs[j]
            if (min(r.t_end, b.t_end) - max(r.t_start, b.t_start) < 0.5 * dur
                    or not RUBY_H[0] <= r.h / max(1.0, b.h) <= RUBY_H[1]
                    or not b.box[1] - 0.8 * r.h <= r.box[3] <= b.box[1] + 0.5 * r.h
                    or r.box[0] < b.box[0] - r.h or r.box[2] > b.box[2] + r.h):
                continue
            c4 = not (abs(r.cx - b.cx) < 0.5 * r.h and evalkit.content_len(b.text) >= uigate.BODY_CLEN)
            cw = max(1.0, b.w) / max(1, len(b.text))
            k0, k1 = int((r.box[0] - b.box[0]) / cw), int((r.box[2] - b.box[0]) / cw)
            under = [ch for ch in b.text[max(0, k0):max(0, k1) + 1] if not ch.isspace()]
            c5 = bool(under) and 2 * sum(script_of(ch) == "han" for ch in under) >= len(under)
            st = "ruby" if c4 and c5 else ("c5" if c4 else ("c4" if c5 else "c45"))
            rank = {"ruby": 0, "c4": 1, "c5": 1, "c45": 2}[st]
            if best is None or rank < best[0]:
                best = (rank, st, j)
            if st == "ruby":
                return st, j
    return (best[1], best[2]) if best else (None, None)


def probe(tracks: str, exclude: list[str]) -> list[dict]:
    evs = json.load(open(tracks, encoding="utf-8"))["events"]
    runs = [as_run(e) for e in evs]
    flagged = {i for i, e in enumerate(evs) if "ruby" in e.get("flags", [])}
    replay = ruby_runs(runs)
    print(f"重放 ruby_runs {len(replay)} 条，产物标 ruby {len(flagged)} 条，交集 {len(replay & flagged)}", file=sys.stderr)
    ex = [tuple(float(x) * 1e6 for x in s.split("-")) for s in exclude]
    bodies: dict[int, list[int]] = {}
    for j, r in enumerate(runs):
        if any(script_of(ch) == "han" for ch in r.text):
            for k in range(r.t_start // BUCKET_US, r.t_end // BUCKET_US + 1):
                bodies.setdefault(k, []).append(j)
    rows = []
    for i, r in enumerate(runs):
        if not kana_only(r.text) or any(lo <= r.t_start < hi for lo, hi in ex):
            continue
        st, j = classify(runs, i, bodies)
        if st is None:
            continue
        b = runs[j]
        rows.append({"eid": r.eid, "status": st, "flag": i in flagged, "t": r.t_start / 1e6, "t_end": r.t_end / 1e6, "text": r.text, "box": r.box,
                     "body_eid": b.eid, "body": b.text, "body_box": b.box})
    rows.sort(key=lambda x: ({"ruby": 0, "c5": 1, "c45": 2, "c4": 3}[x["status"]], x["t"]))
    print(dict(Counter(x["status"] for x in rows)), file=sys.stderr)
    return rows


def sheets(video: str, rows: list[dict], prefix: str, per: int = 12, cw: int = 720, ch: int = 200) -> None:
    import cv2
    import numpy as np
    tiles = []
    for n, r in enumerate(rows):
        cmd = ["ffmpeg", "-nostats", "-loglevel", "error", "-ss", f"{r['t'] + 0.001:.3f}", "-i", video,
               "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
        img = cv2.imdecode(np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.uint8), cv2.IMREAD_COLOR)
        H, W = img.shape[:2]
        bs = [r["box"], r["body_box"]]
        pad = 2 * max(8, r["box"][3] - r["box"][1])
        X0, Y0 = max(0, int(min(b[0] for b in bs)) - 2 * pad), max(0, int(min(b[1] for b in bs)) - pad)
        X1, Y1 = min(W, int(max(b[2] for b in bs)) + 2 * pad), min(H, int(max(b[3] for b in bs)) + pad)
        crop = img[Y0:Y1, X0:X1].copy()
        for b, col in zip(bs, [(0, 0, 255), (0, 200, 0)]):
            cv2.rectangle(crop, (int(b[0]) - X0, int(b[1]) - Y0), (int(b[2]) - X0, int(b[3]) - Y0), col, 1)
        s = min(cw / crop.shape[1], ch / crop.shape[0])
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))))
        tile = np.zeros((ch + 4, cw, 3), np.uint8)
        tile[:crop.shape[0], :crop.shape[1]] = crop
        cv2.putText(tile, str(n), (4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        tiles.append(tile)
    for k in range(0, len(tiles), per):
        grp = tiles[k:k + per] + ([np.zeros_like(tiles[0])] if len(tiles[k:k + per]) % 2 else [])
        path = f"{prefix}-{k // per:02d}.png"
        if not cv2.imwrite(path, np.vstack([np.hstack(grp[i:i + 2]) for i in range(0, len(grp), 2)])):
            raise SystemExit(f"写不了 {path}（目录不存在？）")
        print(path, f"行 {k}..{min(k + per, len(tiles)) - 1}")


def score(labels_path: str, rows_path: str, name: str) -> None:
    lab = json.load(open(labels_path, encoding="utf-8"))["sets"][name]["labels"]
    rows = json.load(open(rows_path, encoding="utf-8"))
    c = Counter((r["status"], lab.get(str(r["eid"]), "?")) for r in rows)
    for (st, lb), n in sorted(c.items()):
        print(f"  {st:5} {lb:6} {n}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tracks", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--video")
    ap.add_argument("--sheets", help="联系表文件名前缀（要 --video）")
    ap.add_argument("--score", help="标注文件；配 --rows 和 --set")
    ap.add_argument("--rows")
    ap.add_argument("--set")
    a = ap.parse_args()
    if a.score:
        score(a.score, a.rows, a.set)
        return 0
    rows = probe(a.tracks, a.exclude)
    if a.out:
        json.dump(rows, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.sheets:
        sheets(a.video, rows, a.sheets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
