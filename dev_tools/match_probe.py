r"""把匹配器的**配对**导出来——因为现有的尺子量不了匹配器。

为什么需要它（script-corpus 报告）：`match_ref.py` 匹配完之后，
`.ass` 里的文本**已经换成剧本原文**了（带 `\N` 换行，一眼可辨），
时间也是按 `TEXT_SPEED` 合成的。于是：

- 拿 `script_align.py` 量匹配后的产物，量的是"**匹配器声称命中了什么**"。
  两边用同一个匹配器时，头对头仍然公平；
  但**任何对匹配器本身的改动都不能用这把尺量**——放宽阈值必然"命中"上升，
  这是循环论证。
- 时间戳也接不回去：`lower5_jp.ass` 的 2,780 条起点，
  在源 `lower5.srt` 里 60 ms 容差内只找得到 8 条（0.3%）。

所以要有一条非循环的路：**让匹配器把 (OCR 原文 → 剧本行) 这一对吐出来**，
再看这一对**本身像不像**。像不像用的是 OCR 的原始文本，
和匹配器的打分完全独立，放宽阈值骗不了它。

不改 owner 的参考脚本：用模块导入 + 猴补丁改阈值。
配对靠 `start = ocr.start - display_delay` 反解回 OCR 条目（匹配器自己写进结果里的）。

用法：
    python dev_tools/match_probe.py --subs data/corpus/mocai/game_text/lower5.srt \
        --refs data/corpus/mocai/script-raw/Scripts --mode dialogue \
        --out out/match/lower5.jsonl
    # 扫短语惩罚：
    python dev_tools/match_probe.py ... --sweep-short 0,3,5,8
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import statistics
import sys
from contextlib import redirect_stdout
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts.evalkit import denom, require_nonzero   # noqa: E402

REF_SCRIPT = Path(__file__).resolve().parent / "reference" / "match_ref.py"


def load_matcher():
    spec = importlib.util.spec_from_file_location("match_ref", REF_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["match_ref"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_once(m, subs_path: str, refs_root: str, mode: str) -> list[dict]:
    """跑一遍匹配，返回 (OCR 原文, 剧本行, 各自清洗后的文本) 的配对。"""
    ocr = m.load_srt(subs_path)
    refs = m.load_reference_directories(refs_root, mode=mode)
    by_start = {round(o.start_seconds, 3): o for o in ocr}
    with redirect_stdout(io.StringIO()):          # 匹配器会打一堆 [SKIP] debug
        res = m.process_subtitles(ocr, refs)
    out = []
    for r in res:
        # 匹配器把 start 写成 `ocr.start - display_delay`，反解回去就能找回 OCR 条目
        k = round(r["start"] + r.get("display_delay", 0.0), 3)
        o = by_start.get(k)
        if o is None:                              # 浮点或后处理挪过，容差再找一次
            cand = min(by_start, key=lambda x: abs(x - k), default=None)
            if cand is not None and abs(cand - k) < 0.02:
                o = by_start[cand]
        ref = refs[r["ref_idx"]]
        out.append({"ocr": o.text if o else "", "ocr_clean": o.clean_text if o else "",
                    "script": ref.jp_text, "script_clean": ref.clean_jp_text,
                    "ref_idx": r["ref_idx"], "start": r["start"]})
    return out, len(ocr), len(refs)


def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def report(pairs: list[dict], n_subs: int, label: str) -> dict:
    """配对本身像不像。**用 OCR 原始文本算，不带短语惩罚**——
    这是和匹配器打分独立的量，放宽阈值骗不了它。"""
    sims = [sim(p["ocr_clean"], p["script_clean"]) for p in pairs]
    joined = sum(1 for p in pairs if p["ocr_clean"])
    bad = [s for s in sims if s < 0.5]
    st = {"label": label, "matched": len(pairs), "subs": n_subs,
          "joined": joined,
          "sim_median": round(statistics.median(sims), 3) if sims else 0.0,
          "sim_p10": round(sorted(sims)[len(sims) // 10], 3) if sims else 0.0,
          "weak": len(bad), "weak_frac": round(len(bad) / max(1, len(sims)), 4),
          "ref_span": len({p["ref_idx"] for p in pairs})}
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subs", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--mode", default="dialogue")
    ap.add_argument("--out", default=None, help="把配对写成 jsonl")
    ap.add_argument("--sweep-short", default="",
                    help="逗号分隔的 SHORT_PHRASE_LEN 取值，各跑一遍对比")
    ap.add_argument("--show", type=int, default=6, help="列几条最不像的配对")
    ap.add_argument("--orphans", action="store_true",
                    help="把**匹配器没放下去**的字幕条挑出来，再去整个剧本里全局模糊搜一遍："
                         "搜得到 = 匹配器真丢了；搜不到 = 那条字幕本来就不在剧本里"
                         "（画面文字/水印/OCR 垃圾）。这是唯一非循环的『匹配器丢了多少』")
    args = ap.parse_args()

    m = load_matcher()
    base = m.SHORT_PHRASE_LEN
    values = [int(x) for x in args.sweep_short.split(",") if x.strip()] or [base]

    rows = []
    for v in values:
        m.SHORT_PHRASE_LEN = v
        pairs, n_subs, n_refs = run_once(m, args.subs, args.refs, args.mode)
        st = report(pairs, n_subs, f"SHORT_PHRASE_LEN={v}")
        rows.append(st)
        require_nonzero(st["matched"], "匹配上的条目",
                        "一条都没匹配上，先确认剧本目录和 mode 对不对")
        print(f"[{st['label']}] 字幕 {n_subs} 条 / 剧本 {n_refs} 条 -> "
              f"匹配 {st['matched']}，配回 OCR 原文 {st['joined']}", flush=True)
        if args.out and v == values[0]:
            p = Path(args.out); p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w", encoding="utf-8") as fh:
                for x in pairs:
                    fh.write(json.dumps(x, ensure_ascii=False) + "\n")
            print(f"  配对已写到 {p}")

    print(f"\n{'设置':<24}{'匹配条数':>9}{'配对相似度中位':>16}{'P10':>8}"
          f"{'弱配对<0.5':>12}{'占比':>8}")
    for st in rows:
        print(f"{st['label']:<24}{st['matched']:>9}{st['sim_median']:>16.3f}"
              f"{st['sim_p10']:>8.3f}{st['weak']:>12}{st['weak_frac']:>8.1%}")

    if args.orphans:
        m.SHORT_PHRASE_LEN = values[0]
        ocr = m.load_srt(args.subs)
        refs = m.load_reference_directories(args.refs, mode=args.mode)
        used = {round(p["start"] + 0.0, 3) for p in pairs}
        matched_starts = set()
        for p in pairs:
            matched_starts.add(round(p["start"], 3))
        # 用配对里记下来的 OCR 原文反查：没出现在配对里的字幕条就是 orphan
        paired_text = Counter(p["ocr"] for p in pairs)
        orphans = []
        for o in ocr:
            if paired_text.get(o.text, 0) > 0:
                paired_text[o.text] -= 1
            else:
                orphans.append(o)
        # 全局模糊搜：quick_ratio 是 ratio 的上界，先挡一道
        cleans = [(i, r.clean_jp_text) for i, r in enumerate(refs) if r.clean_jp_text]
        found, missing = [], []
        sm = SequenceMatcher(None)
        for o in orphans:
            a = o.clean_text
            if not a:
                missing.append((o, 0.0, -1)); continue
            sm.set_seq1(a); best, bi = 0.0, -1
            la = len(a)
            for i, b in cleans:
                lb = len(b)
                if 3 * min(la, lb) < 2 * max(la, lb):
                    continue
                sm.set_seq2(b)
                if sm.quick_ratio() <= best:
                    continue
                v = sm.ratio()
                if v > best:
                    best, bi = v, i
                    if best >= 0.99:
                        break
            (found if best >= 0.8 else missing).append((o, best, bi))
        # **口径修正**：匹配器会把连续几条字幕并进同一个 buffer（同一条剧本行）。
        # 被并掉的字幕条在配对表里找不到自己，但那条剧本行**已经输出了**，不算丢。
        # 只有"它最像的那条剧本行压根没被输出"才是真丢。
        covered = {p["ref_idx"] for p in pairs}
        absorbed = [x for x in found if x[2] in covered]
        found = [x for x in found if x[2] not in covered]
        n = len(orphans)
        print(f"\n**匹配器没放下去的字幕条：{n} / {len(ocr)} = {n/max(1,len(ocr)):.1%}**")
        print(f"   其中在剧本里搜得到、但那条剧本行**已经由别的字幕条输出**："
              f"{len(absorbed)} = {len(absorbed)/max(1,n):.1%}  ← 被并进同一个 buffer，不算丢")
        print(f"   在剧本里搜得到、且那条剧本行**没被输出**：{len(found)} = {len(found)/max(1,n):.1%}"
              f"  ← 这才是匹配器真丢的")
        print(f"   搜不到：{len(missing)} = {len(missing)/max(1,n):.1%}"
              f"  ← 本来就不在剧本里（画面文字/水印/OCR 垃圾）")
        c = Counter(min(len(o.clean_text), 12) for o, _, _ in found)
        print("   真丢的按 OCR 清洗后字数：" + "  ".join(f"{k}字×{v}" for k, v in sorted(c.items())))
        print(f"\n真丢的前 {args.show} 条：")
        for o, sc, bi in sorted(found, key=lambda x: -x[1])[:args.show]:
            print(f"      sim {sc:.2f} | OCR {o.text[:26]!r} -> 剧本 {refs[bi].jp_text[:26]!r}")
        print(f"   搜不到的前 {args.show} 条：")
        for o, sc, bi in missing[:args.show]:
            print(f"      best {sc:.2f} | OCR {o.text[:34]!r}")

    if args.show and rows:
        m.SHORT_PHRASE_LEN = values[0]
        pairs, _, _ = run_once(m, args.subs, args.refs, args.mode)
        worst = sorted(pairs, key=lambda p: sim(p["ocr_clean"], p["script_clean"]))
        print(f"\n最不像的 {args.show} 条配对（SHORT_PHRASE_LEN={values[0]}）：")
        for p in worst[:args.show]:
            print(f"   sim {sim(p['ocr_clean'], p['script_clean']):.2f} | "
                  f"OCR {p['ocr'][:30]!r} -> 剧本 {p['script'][:30]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
