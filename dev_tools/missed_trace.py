"""匹配后的漏行追查：按剧本顺序走一遍**匹配器的产物**，逐条列出没认领到的剧本行，并定位丢在哪一层。

存在的理由（2026-09-26 审计）：`game_align` 按剧本顺序报"疑似真漏"，但量的是**主轨**；产物默认喂的是全部非噪音
区域轨（`scriptmatch --feed nonoise`），主轨口径的漏行里有一部分匹配后其实认领到了（那晚 zzz 主轨丢的 6 句，
匹配后 5 句在）。而且它只给汇总和前几条样例，不带时间窗、不说丢在哪一层——追一条要手工翻 SRT、tracks、obs。

每条没认领的该上屏行给出（先查"另一版本"，再定层）：
* **另一版本已认领**：同一个时间窗里有一条和它几乎一样的剧本行被认领了（绝区零按主角性别、按版本各存一行，
  变体组没并全），或者 tracks 里找到的那段字同样落在 cue 认的那一行里（改写过的另一版本，整行不像、共有的那段一样）
  ——屏幕上多半只出了那一版，这一行是分母问题，不是漏。定层之前先排掉它，免得把"认了更像的那一版"
  误报成匹配器认错（09-26 第一次在 zzz 上跑：没排之前"喂了、认成别的行"的大半是这种）。
* **缺口**：前后都认领、中间漏了不到 `--branch-run` 条的是短缺口（高概率真漏）；连成长段的是长缺口（照样列，
  分支已经按对话图排出分母，长段里剩下的没有分支解释——同 `game_align` 的口径）。
* **时间窗**：剧本顺序上前一条认领行的结束、后一条认领行的开始（取离这一行锚点时刻最近的那次认领）。
* **obs 这一侧**（gamescript 的锚点，同 `game_align.obs_side`）：窗里读到过 / 读到过但不在这个窗 / 只撞常用句 /
  没读到 / 太短判不了。
* **层**（只对"窗里读到过"的判得出）：在 tracks 的事件里按文本找这一行——
  - `建轨没留下`：obs 读到过，tracks 里窗内没有像它的事件；
  - `没喂`：事件在，但它所在的区域 / cue 没进匹配器（noise 区域、按 UI / 注音剔掉、面板样 cue 被剔）；
  - `喂了、认成别的行`：进了匹配器的那条 cue 认领了另一条剧本行；
  - `喂了、没认上`：那条 cue 没认上任何行（常见是和别的字拼进同一条 cue，相似度不够）。

判据全部复用：剧本顺序与分母（`game_align.load_ref` / `band`）、缺口切分（`script_align.gap_runs`）、三档
（`evalkit.triviality`）、锚点（`game_align.obs_side`）、喂法投影（`scriptmatch.feed_from_doc`），不另写一份。
只读已有产物，不跑 OCR、不跑匹配。

用法：
    python dev_tools/missed_trace.py out/na/f4-e4/zzz/matched.json [--out x.json] [--show 20]
    python dev_tools/missed_trace.py <A matched.json> --vs <B matched.json>     # 两臂逐条：新漏的 / 修好的
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import io
import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from flowocr import paths
from flowocr.analyze import game_align as GA
from flowocr.analyze import gamescript as GS
from flowocr.analyze import scriptmatch as SM
from flowocr.analyze import script_align as SA
from flowocr.artifacts import evalkit, tracksio

PAD = 2.0
"""时间窗两头各放宽几秒：认领的起止是 cue 的边界，字常常早一点上屏、晚一点消失。"""
FIND_IN = 0.8
"""事件的字有这么多落在剧本行里，才算"这一行在 tracks 里的事件"。"""
FIND_MIN = 3
"""事件至少这么多内容字才拿来找（1–2 字的碎片落在哪条长行里都像）。"""

CLOSEST = 0.5
"""`建轨没留下` 时退一步找的线索门槛：窗里字有这么多落在行里的事件，列出来给人看是不是改写过的另一版本。"""
SIBLING = 0.6
"""两条剧本行（内容归一后）相似到这个比例，算同一句的另一版本（`sibling`）。"""

IN_WIN, OTHER_WIN = "窗里读到过", "读到过但不在这个窗"
SIDES = (IN_WIN, OTHER_WIN, GA.OBS_COMMON, GA.OBS_NONE, GA.OBS_SHORT)
L_SIB, L_NOEV, L_NOFEED, L_OTHER, L_NONE = "另一版本已认领", "建轨没留下", "没喂", "喂了、认成别的行", "喂了、没认上"
LAYERS = (L_NOEV, L_NOFEED, L_OTHER, L_NONE)


def resolve(p: str) -> Path:
    q = Path(p.replace("\\", "/"))                # provenance 里的相对路径可能是 Windows 写法
    return q if q.is_absolute() else paths.data_root() / q


def claim_times(recs: list[dict], variant_of: dict[str, str]) -> dict[str, list[tuple[float, float]]]:
    """剧本行（变体并到代表行）-> 它被认领的每一次 (起, 止)，主认领和第二行（`extra`）都算——和覆盖同口径。"""
    out: dict[str, list[tuple[float, float]]] = {}
    for r in recs:
        for k in ([r["ref"]["key"]] if r["ref"] else []) + [x["key"] for x in r["extra"]]:
            out.setdefault(variant_of.get(k, k), []).append((r["start"], r["end"]))
    return out


def window(t: float, prev: list[tuple[float, float]] | None, nxt: list[tuple[float, float]] | None
           ) -> tuple[float | None, float | None]:
    """这一行该在的时间窗：前一条认领行离锚点时刻 `t` 最近、不晚于它的那次认领的结束，到后一条认领行最近、不早于它的
    那次的开始。前后没有认领行（剧本头尾）那一端就是 None。认领晚于 / 早于 `t` 的全都没有时退回离得最近的那次。"""
    lo = hi = None
    if prev:
        before = [e for s, e in prev if s <= t + PAD]
        lo = max(before) if before else min(e for _, e in prev)
    if nxt:
        after = [s for s, e in nxt if e >= t - PAD]
        hi = min(after) if after else max(s for s, _ in nxt)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def sibling(text: str, lo: float | None, hi: float | None, claimed: list[tuple[float, float, str]],
            texts: dict[str, str]) -> tuple[str, float] | None:
    """时间窗（放宽 `PAD`）里被认领的剧本行中，和这一行最像、相似 ≥ `SIBLING` 的那条：(key, 相似度)。窗两头都没有就不查。"""
    if lo is None and hi is None:
        return None
    a = (lo if lo is not None else hi) - PAD
    b = (hi if hi is not None else lo) + PAD
    rt = GS.cnorm(text)
    best = None
    for s, e, k in claimed:
        if s > b or e < a:
            continue
        r = SequenceMatcher(None, rt, GS.cnorm(texts.get(k, "")), autojunk=False).ratio()
        if r >= SIBLING and (best is None or r > best[1]):
            best = (k, round(r, 3))
    return best


def in_window(episodes: list, lo: float | None, hi: float | None) -> bool:
    a = (lo if lo is not None else float("-inf")) - PAD
    b = (hi if hi is not None else float("inf")) + PAD
    return any(e0 <= b and e1 >= a for e0, e1 in episodes)


class Feed:
    """tracks + 这份 matched 用的喂法：事件 -> 所在区域轨 / cue、进没进匹配器、进了的话那条 cue 被认成了什么。"""

    def __init__(self, doc: dict, matched: dict):
        pr = matched["provenance"]["params"]
        self.doc = doc
        with contextlib.redirect_stdout(io.StringIO()):          # feed_from_doc 会打印喂入统计
            self.cues, evs_of, _ = SM.feed_from_doc(doc, pr["feed"], pr["feed_panel"])
        if len(self.cues) != matched["stats"]["cues_raw"]:
            raise SystemExit(f"投影出的 cue {len(self.cues)} 条，matched 记的是 {matched['stats']['cues_raw']} 条："
                             f"tracks 或喂法和产这份 matched 的不是同一份")
        self.fed_cue = {}
        for i, evs in enumerate(evs_of):
            for e in evs:
                self.fed_cue.setdefault(e["id"], i)
        self.rec_of = {i: r for r in matched["cues"] for i in r["cues"]}
        self.heads = GA.head_lines([(c.start, "\n".join(c.lines), "") for c in self.cues])
        self.label = {r["index"]: r.get("label") for r in doc["regions"]}
        self.where = {}                                           # 事件 -> (区域轨, cue)
        for tr in doc["tracks"]:
            if tr.get("kind") != "region":
                continue
            for c in tr["cues"]:
                for i in c["events"]:
                    self.where.setdefault(i, (tr, c))
        self.evs = sorted(doc["events"], key=lambda e: e["t_start"])
        self.t0 = [e["t_start"] for e in self.evs]
        self.feed_mode, self.panel = pr["feed"], pr["feed_panel"]

    def find(self, text: str, lo: float | None, hi: float | None, need: float = FIND_IN) -> dict | None:
        """窗里（放宽 `PAD`）最像这一行的事件：字有 `need` 落在行里、盖住行的字最多的那个。"""
        rt = GS.cnorm(text)
        a = ((lo if lo is not None else 0.0) - PAD) * 1e6
        b = ((hi if hi is not None else 1e12) + PAD) * 1e6
        best, best_n = None, 0
        for e in self.evs[:bisect.bisect_right(self.t0, b)]:
            if e["t_end"] < a:
                continue
            q = GS.cnorm(e["text"])
            if len(q) < FIND_MIN or GS.contained(q, rt) < need:
                continue
            if len(q) > best_n:
                best, best_n = e, len(q)
        return best

    def why_not_fed(self, e: dict) -> str:
        tr, cue = self.where.get(e["id"], (None, None))
        if tr is None:
            return "不在任何区域轨的 cue 里"
        if self.feed_mode == "nonoise" and tr.get("label") == "noise":
            return "noise 区域"
        flags = set(e.get("flags") or ())
        if "ruby" in flags:
            return "按注音剔掉"
        if "ui_footprint" in flags or e["text"].strip() in set(tr.get("ui_lines") or ()):
            return "按常驻 UI 剔掉"
        if self.panel == "drop" and cue.get("panel_like"):
            return "面板样 cue 被剔"
        return "原因不明"

    def diagnose(self, text: str, lo: float | None, hi: float | None) -> dict:
        e = self.find(text, lo, hi)
        if e is None:
            # 找不到够像的：多半是真没留下，也可能屏幕上是改写过的另一版本（锚点是模糊检索锚上的）。给出窗里最近似的一条当线索
            c = self.find(text, lo, hi, need=CLOSEST)
            return {"layer": L_NOEV, **({"closest": c["id"], "closest_text": c["text"], "closest_region": c["region"],
                                         "closest_t": c["t_start"] / 1e6} if c else {})}
        tr, cue = self.where.get(e["id"], (None, None))
        out = {"event": e["id"], "event_text": e["text"], "event_t": [e["t_start"] / 1e6, e["t_end"] / 1e6],
               "region": e["region"], "region_label": self.label.get(e["region"]),
               "track": tr["id"] if tr else None, "cue": cue["id"] if cue else None}
        i = self.fed_cue.get(e["id"])
        if i is None:
            return {**out, "layer": L_NOFEED, "why": self.why_not_fed(e)}
        raw = "\n".join(self.cues[i].lines)
        rec = self.rec_of.get(i)
        out.update(fed_cue=i, cue_text=raw, sim=round(SM.sim_of(raw, text, self.heads), 3))
        if rec and rec["ref"]:
            got = {"claimed": rec["ref"]["key"], "claimed_jp": rec["ref"].get("jp"), "claimed_score": rec["ref"]["score"]}
            # 找到的这段字也落在 cue 认的那一行里：屏幕上出的就是那一版（改写过的另一版本），不是这一行被抢了
            if GS.contained(GS.cnorm(e["text"]), GS.cnorm(rec["ref"].get("jp") or "")) >= FIND_IN:
                return {**out, **got, "layer": L_SIB, "sibling": got["claimed"], "sibling_text": got["claimed_jp"],
                        "sibling_sim": None, "sibling_via": "同一段字"}
            return {**out, **got, "layer": L_OTHER}
        return {**out, "layer": L_NONE}


def trace(matched_path: Path, branch_run: int) -> dict:
    """一份 matched.json -> 汇总 + 逐条漏行（按优先级排：短缺口在前，窗里读到过的在前）。"""
    m = json.loads(matched_path.read_text(encoding="utf-8"))
    pv = m["provenance"]
    ref = GA.load_ref(resolve(pv["ref"]))
    doc = tracksio.load(resolve(pv["subs"]))
    feed = Feed(doc, m)
    claims = claim_times(m["cues"], ref.variant_of)
    seq_t = {k: t for k, t, _ in ref.doc["sequence"]}
    anchor = {ln["key"]: ln.get("anchor") for u in ref.doc["units"] for ln in u["lines"] if ln["kind"] != "Duplicate"}
    side = GA.obs_side(ref.doc)
    dial = [e for e in ref.script if GA.band(e)]
    hit = [e.key in claims for e in dial]
    runs = SA.gap_runs(hit)
    gap_of = {k: ("短缺口" if n < branch_run else "长缺口") for i, n in runs for k in range(i, i + n)}
    hit_idx = [k for k, h in enumerate(hit) if h]
    texts = {ln["key"]: ln["text"] for u in ref.doc["units"] for ln in u["lines"]}
    claimed = sorted((s_, e_, k) for k, ts in claims.items() for s_, e_ in ts)
    items = []
    for k, e in enumerate(dial):
        if hit[k]:
            continue
        j = bisect.bisect_left(hit_idx, k)
        prev = dial[hit_idx[j - 1]] if j > 0 else None
        nxt = dial[hit_idx[j]] if j < len(hit_idx) else None
        t = seq_t.get(e.key, 0.0)
        lo, hi = window(t, claims.get(prev.key) if prev else None, claims.get(nxt.key) if nxt else None)
        s = side.get(e.key, GA.OBS_NONE)
        a = anchor.get(e.key)
        if s == GA.OBS_READ:
            s = IN_WIN if in_window(a["episodes"], lo, hi) else OTHER_WIN
        it = {"key": e.key, "text": e.text, "class": evalkit.triviality(e.text), "gap": gap_of[k],
              "role": ref.role.get(e.key), "obs": s, "window": [lo, hi], "anchor_t": t,
              "prev": prev.key if prev else None, "next": nxt.key if nxt else None}
        sib = sibling(e.text, lo, hi, claimed, texts)
        if sib:
            it.update(layer=L_SIB, sibling=sib[0], sibling_text=texts.get(sib[0]), sibling_sim=sib[1], sibling_via="整行相似")
        elif s == IN_WIN:
            it.update(feed.diagnose(e.text, lo, hi))
        items.append(it)
    rank = {("短缺口", IN_WIN): 0, ("长缺口", IN_WIN): 1, ("短缺口", OTHER_WIN): 2, ("短缺口", GA.OBS_NONE): 3}
    items.sort(key=lambda x: (x.get("layer") == L_SIB, rank.get((x["gap"], x["obs"]), 9),   # 另一版本已认领的排最后 evalkit.CLASSES.index(x["class"]) * -1,
                              x["window"][0] if x["window"][0] is not None else 0.0))
    return {"matched": str(matched_path), "ref": pv["ref"], "tracks": pv["subs"], "feed": pv["params"]["feed"],
            "denominator": len(dial), "claimed": sum(hit), "missed": len(items), "items": items}


def hms(t: float | None) -> str:
    if t is None:
        return "   —    "
    h, r = divmod(int(t), 3600)
    return f"{h}:{r // 60:02d}:{r % 60:02d}"


def summary(res: dict) -> None:
    items = res["items"]
    print(f"{res['matched']}（喂法 {res['feed']}）：该上屏 {res['denominator']} 条，认领 {res['claimed']}，"
          f"没认领 {res['missed']}")
    C = evalkit.CLASSES[2]
    sib = [x for x in items if x.get("layer") == L_SIB and x["class"] == C]
    print(f"没认领的『{C}』里，同一时间窗有另一版本被认领的（多半是分母问题，不算漏）：{len(sib)}")
    print(f"其余没认领的『{C}』按 缺口 × obs 这一侧：")
    print(f"  {'':<8}" + "".join(f"{s:>14}" for s in SIDES))
    for g in ("短缺口", "长缺口"):
        c = Counter(x["obs"] for x in items if x["gap"] == g and x["class"] == C and x.get("layer") != L_SIB)
        print(f"  {g:<8}" + "".join(f"{c[s]:>16}" for s in SIDES))
    rd = [x for x in items if x["obs"] == IN_WIN and x["class"] == C and x.get("layer") != L_SIB]
    c = Counter((x["gap"], x["layer"]) for x in rd)
    print(f"窗里读到过的『{C}』丢在哪一层（高概率真漏，按 tracks 事件定位）：")
    print(f"  {'':<8}" + "".join(f"{s:>14}" for s in LAYERS))
    for g in ("短缺口", "长缺口"):
        print(f"  {g:<8}" + "".join(f"{c[(g, s)]:>16}" for s in LAYERS))
    why = Counter(x["why"] for x in rd if x["layer"] == L_NOFEED)
    if why:
        print("  没喂的原因：" + "、".join(f"{k} {v}" for k, v in why.most_common()))
    print("  ⚠ 是追查线索、不是确认的错：锚点是"
          "文本检索，窗和事件都按文本找，摆帧才算数")


def show(res: dict, n: int) -> None:
    print(f"\n逐条（前 {n} 条；短缺口、窗里读到过的在前）：")
    for x in res["items"][:n]:
        lo, hi = x["window"]
        head = f"  [{x['gap']}·{x['obs']}] {hms(lo)}–{hms(hi)}  {x['text'][:40].replace(chr(10), ' ')}"
        tail = ""
        if x.get("layer"):
            tail = f"\n      -> {x['layer']}"
            if "closest" in x:
                tail += f"（窗里最近似的：#{x['closest']} {hms(x['closest_t'])} r{x['closest_region']:02d}「{x['closest_text'][:24]}」）"
            if "event" in x:
                tail += (f"：事件 #{x['event']} {hms(x['event_t'][0])} 区域 r{x['region']:02d}"
                         f"（{x['region_label']}）「{x['event_text'][:24]}」")
            if x["layer"] == L_SIB:
                tail += (f"（{x['sibling_via']}）：「{(x['sibling_text'] or '')[:30]}」"
                         + (f"（{x['sibling_sim']:.2f}）" if x.get("sibling_sim") is not None else ""))
            elif x["layer"] == L_NOFEED:
                tail += f"；{x['why']}"
            elif x["layer"] == L_OTHER:
                tail += f"；那条 cue 认成「{(x['claimed_jp'] or '')[:24]}」（{x['claimed_score']:.2f}），对这一行 {x['sim']:.2f}"
            elif x["layer"] == L_NONE:
                tail += f"；那条 cue 对这一行 {x['sim']:.2f}：「{x['cue_text'][:40].replace(chr(10), ' / ')}」"
        print(head + tail)


def compare(a: dict, b: dict) -> None:
    """两臂逐条：A 没漏、B 漏了的（新漏）和反过来的（修好的），按剧本 key 比，只看有内容档。"""
    C = evalkit.CLASSES[2]
    ma = {x["key"]: x for x in a["items"] if x["class"] == C}
    mb = {x["key"]: x for x in b["items"] if x["class"] == C}
    new, fixed = [mb[k] for k in mb if k not in ma], [ma[k] for k in ma if k not in mb]
    print(f"\n两臂逐条（{C}）：B 新漏 {len(new)}、B 修好 {len(fixed)}（A {a['matched']} -> B {b['matched']}）")
    for name, xs in (("新漏", new), ("修好", fixed)):
        c = Counter((x["gap"], x["obs"], x.get("layer", "—")) for x in xs)
        for (g, s, lay), v in c.most_common():
            print(f"  {name} {g}·{s}·{lay}：{v}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matched", help="scriptmatch 的 matched.json（剧本、tracks、喂法都从它的 provenance 取）")
    ap.add_argument("--vs", default=None, help="另一条臂的 matched.json：逐条比新漏 / 修好")
    ap.add_argument("--branch-run", type=int, default=5, help="同 game_align：连续这么多条没认领算长缺口")
    ap.add_argument("--show", type=int, default=15, help="逐条列几条")
    ap.add_argument("--out", default=None, help="汇总与逐条清单写到这个 JSON（剧本 key 是主键，两臂可以逐条比）")
    a = ap.parse_args()
    res = trace(resolve(a.matched), a.branch_run)
    summary(res)
    if a.show:
        show(res, a.show)
    if a.vs:
        compare(res, trace(resolve(a.vs), a.branch_run))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
