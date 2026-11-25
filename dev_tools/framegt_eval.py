"""读帧真值对产物：每一帧真值里的每一块字，在那一刻在屏的区域轨条目里找最像的一条，按横排 / 竖排分开报。

真值格式见 `data/gt/vertical/README.md`（每帧一行 `{"f": "<id>/tNNNN.NN.jpg", "items": [...]}`，`<id>` 对 `samples.json` 里的
`v-idol-<id>`）。产物是 `build_tracks` 的 `<tag>-tracks.json`，只认区域轨（`kind == "region"`），条目文字取阶段 3 的 SRT 视图
（`tracksio.cue_rows`，剔常驻 UI、注音和同句合并留下的半截行）。

口径（每块真值一个数，按 `o` = 竖排 v / 横排 h 分开汇总，默认只数 `kind == "sub"`，即后期加的字幕和说明字）：
* **字准** = max(0, 1 − 编辑距离 / 真值字数)，在 t 在屏的条目的所有连续行段里取最高的那段（一条条目可能装着几块字）；比之前两边都做 NFKC、去空白（列 / 行之间的换行不算字）。
* **读到** = 字准 ≥ `--hit`（默认 0.8）的块数；**全对** = 字准 = 1 的块数。
* **读序反** = 真值多于一列 / 行、有个连续行段按反过来的顺序拼起来比取中的那段字准更高的块数（竖排列序从右往左）。
* **多出** = t 在屏、任何连续行段和这一帧任何一块真值（不分 kind）两个方向的字准都不到 0.5 的条目数（真值不全时会多算：画面里没转写的招牌之类）。
在屏 = 条目的 [t_start − slack, t_end + slack] 盖住 t（`--slack` 默认 0.25 s，半个采样间隔）。

`--view srt` 改量阶段 3 实际交付的 `<tag>-default_all.srt`（`default_srt --opt keep=all`，要先导出）：阶段 3 会把结束时间不同的成员拆成几条、
从 SRT 去掉跨条目常驻的行，所以读数可能比区域轨视图低一点。SRT 的一条就是一个条目、各行就是它的行。

    python dev_tools/framegt_eval.py data/gt/vertical/gt.jsonl --arm=-base [--kinds sub] [--show 20]
    python -m flowocr.output.render out/samples/v-idol-1-v7/v-idol-1-tracks.json --preset default_srt --opt keep=all   # --view srt 之前
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr import paths  # noqa: E402
from flowocr.artifacts import srtio, tracksio  # noqa: E402


def norm(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", s) if not ch.isspace())


def edit(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def acc(gt: str, got: str) -> float:
    g = norm(gt)
    return max(0.0, 1.0 - edit(g, norm(got)) / len(g)) if g else 0.0


def runs(rows: list[str]) -> list[list[str]]:
    """一条条目里连续的行段（一条条目可能装着几块字：一块真值只和其中连着的几行比）。"""
    return [rows[i:j] for i in range(len(rows)) for j in range(i + 1, len(rows) + 1)]


def cues_of(doc: dict) -> list[dict]:
    """区域轨的条目：起止（秒）+ 按导出行序的各行文字。"""
    out = []
    for tr in doc["tracks"]:
        if tr.get("kind") != "region":
            continue
        for c in tr["cues"]:
            rows = [r["text"] for r in tracksio.cue_rows(doc, tr, c)]
            if rows:
                out.append({"t0": c["t_start"] / 1e6, "t1": c["t_end"] / 1e6, "rows": rows, "track": tr["id"]})
    return out


def srt_cues(path: Path) -> list[dict]:
    """SRT 的条目：起止（秒）+ 各行文字。读取走项目共用的 `srtio.read_srt`（带条目数校验）。"""
    return [{"t0": c.start, "t1": c.end, "rows": [x for x in c.lines if x.strip()], "track": "srt"}
            for c in srtio.read_srt(path) if any(x.strip() for x in c.lines)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gt")
    ap.add_argument("--arm", default="", help="产物后缀（sampleset 的 ARM），产物在 out/samples/v-idol-<id><arm>/")
    ap.add_argument("--prefix", default="v-idol-")
    ap.add_argument("--view", choices=("region", "srt"), default="region",
                    help="region = 区域轨（默认）；srt = 阶段 3 导出的 <tag>-default_all.srt")
    ap.add_argument("--kinds", default="sub")
    ap.add_argument("--hit", type=float, default=0.8)
    ap.add_argument("--slack", type=float, default=0.25)
    ap.add_argument("--show", type=int, default=0, help="打印字准最低的这么多块")
    ap.add_argument("--json", default="", help="逐块结果写到这里")
    a = ap.parse_args()
    kinds = set(a.kinds.split(","))
    root = paths.data_root()
    gt = [json.loads(l) for l in Path(a.gt).read_text(encoding="utf-8").splitlines() if l.strip()]
    docs: dict[str, list[dict]] = {}
    rows = []
    extra = 0
    for fr in gt:
        sid, name = fr["f"].split("/")
        t = float(name[1:-4])
        if sid not in docs:
            tag = a.prefix + sid
            d = root / "out" / "samples" / f"{tag}{a.arm}"
            p = d / (f"{tag}-tracks.json" if a.view == "region" else f"{tag}-default_all.srt")
            if not p.exists():
                sys.exit(f"缺产物 {p}")
            docs[sid] = cues_of(tracksio.load(p)) if a.view == "region" else srt_cues(p)
        live = [c for c in docs[sid] if c["t0"] - a.slack <= t <= c["t1"] + a.slack]
        segs = [rs for c in live for rs in runs(c["rows"])]
        for it in fr["items"]:
            g = "".join(it["cols"])
            best, bi = 0.0, -1
            for i, rs in enumerate(segs):
                s = acc(g, "".join(rs))
                if s > best:
                    best, bi = s, i
            rev = len(it["cols"]) > 1 and any(acc(g, "".join(rs[::-1])) > best for rs in segs if len(rs) > 1)
            if it["kind"] in kinds:
                rows.append({"f": fr["f"], "o": it["o"], "kind": it["kind"], "gt": g,
                             "got": "".join(segs[bi]) if bi >= 0 else "",
                             "acc": round(best, 3), "rev": rev, "unsure": it.get("unsure", False)})
        for c in live:
            if len(norm("".join(c["rows"]))) >= 2 and not any(
                    acc(g, x) >= 0.5 or acc(x, g) >= 0.5
                    for x in map("".join, runs(c["rows"])) for g in ("".join(it["cols"]) for it in fr["items"])):
                extra += 1
    by = defaultdict(list)
    for r in rows:
        by[r["o"]].append(r)
    print(f"真值 {len(gt)} 帧；视图 {a.view}；kind ∈ {sorted(kinds)}；读到 = 字准 ≥ {a.hit}")
    for o in ("v", "h"):
        rs = by.get(o, [])
        if not rs:
            continue
        n = len(rs)
        hit = sum(r["acc"] >= a.hit for r in rs)
        ex = sum(r["acc"] == 1.0 for r in rs)
        mean = sum(r["acc"] for r in rs) / n
        rev = sum(r["rev"] for r in rs)
        print(f"  {'竖排' if o == 'v' else '横排'} {n} 块：读到 {hit}（{hit / n:.1%}）、全对 {ex}（{ex / n:.1%}）、平均字准 {mean:.3f}、读序反 {rev}")
    print(f"  多出（在屏、和这一帧哪块真值都不像的条目，按帧计）{extra}")
    if a.show:
        print("字准最低的块：")
        for r in sorted(rows, key=lambda r: r["acc"])[:a.show]:
            print(f"  {r['f']} {r['o']} {r['acc']:.2f} 真值「{r['gt']}」 读到「{r['got']}」{' 读序反' if r['rev'] else ''}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
