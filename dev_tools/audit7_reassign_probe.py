"""第七轮审计 §2.1 / §2.5 的复现脚本：`--slot-reassign` 两臂的**产物差**。

只读产物，不跑 GPU。回答三个问题：

1. **碎片化**：区域数 + 两个"单"（只占一个时间窗的区域 / 只有一条 cue 的区域）。
   一致性那个指标把 `len(sl) < 2` 的槽位排出分母，所以它对"全拆成单例"是盲的；
2. **主轨成员差**：按稳定键（`t_start, t_end, box, text` 的多重集）算移出 / 移入。
   ⚠ **不能按事件 id 比**：两臂的区域排序不同，id 不是同一个东西；
3. **移出的剧本行去哪了**——三档分开数，⚠ **事件数和文本种数不能相减**
   （复审 2026-09-20 抓到的算术错）：移出的事件里文本是剧本行的有几个、
   其中**同一条剧本行在新主轨里还有没有别的事件顶着**、以及**整种消失**几种文本；
4. **主轨里的冗余**："同一文本、距上次出现 < 5 s"的**事件**占比（⚠ 先按事件去重，
   见 `redundancy`）——"掉的是冗余碎片"这句话要的是这个数，不是"内容还在不在"。

用法：
    python dev_tools/audit7_reassign_probe.py [两臂的 tracks.json ...]
不给参数就跑 `tmp/reassign/` 下那四对（f5 / gi2 整片 + 两段切片）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev_tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr import paths  # noqa: E402

ROOT = paths.data_root()
PAIRS = [("f5 整片", "tmp/reassign/full-f5-off/f5-tracks.json",
          "tmp/reassign/full-f5-on/f5-tracks.json", "out/gametext/f5-ref.json"),
         ("gi2 整片", "tmp/reassign/full-gi2-off/gi2-tracks.json",
          "tmp/reassign/full-gi2-on/gi2-tracks.json", "out/gametext/gi2-ref.json"),
         ("q-gi-s2", "tmp/reassign/q-gi-s2-off/q-gi-s2-tracks.json",
          "tmp/reassign/q-gi-s2-on/q-gi-s2-tracks.json", ""),
         ("e-yuka-f5", "tmp/reassign/e-yuka-f5-off/e-yuka-f5-tracks.json",
          "tmp/reassign/e-yuka-f5-on/e-yuka-f5-tracks.json", "")]


def load(rel: str) -> dict | None:
    p = ROOT / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main_track(doc: dict) -> dict:
    mt = doc["provenance"].get("main_track")
    return next(t for t in doc["tracks"] if t["id"] == mt)


def members(doc: dict) -> Counter:
    """主轨成员的**稳定键**多重集。id 在两臂之间不是同一个东西，只能这么比。"""
    ev = doc["events"]
    return Counter((ev[i]["t_start"], ev[i]["t_end"], tuple(ev[i]["box"]), ev[i]["text"])
                   for c in main_track(doc)["cues"] for i in c["events"])


def singles(doc: dict) -> tuple[int, int]:
    """碎片化的两个数：**只出现在一个时间窗的区域** / **只有一条 cue 的区域**。

    ⚠ 前者才是 `slot_consistency` 那个分母漏掉的东西（它把 `len(sl) < 2` 的槽位
    整个排出分母，所以"全拆成单例"能让残差归零）。后者是另一回事——
    一个区域可以横跨多窗却只合出一条 cue。**两个别混着叫"单成员"**
    （复审 2026-09-20 抓到：探针原来只报后者，却按前者解释）。"""
    win1 = sum(1 for r in doc["regions"] if r.get("n_windows_present") == 1)
    cue1 = sum(1 for t in doc["tracks"] if t.get("kind") == "region" and len(t["cues"]) == 1)
    return win1, cue1


def redundancy(doc: dict, gap_us: int = 5_000_000) -> tuple[int, int]:
    """主轨里"同一文本、距上次出现不到 `gap_us`"的**事件**有几个。

    ⚠ **按事件去重，不是按 cue 引用**（复审 2026-09-20 抓到）：一条在屏久的 run
    会被**多条 cue** 引用（gi2 上平均 1.62 条），而它们的 `t_start` 是同一个数，
    必然判成"间隔 0 秒"——于是这个数结构性虚高，off 臂 13.3% 被读成 46.3%。
    要量"导出文本里重复了多少"是另一件事，那得用 **cue 自己的时间**，另立口径。
    """
    mt = main_track(doc)
    ids = sorted({i for c in mt["cues"] for i in c["events"]})
    from flowocr.analyze import gamescript as GS
    rows = sorted((doc["events"][i]["t_start"], GS.cnorm(doc["events"][i]["text"]))
                  for i in ids)
    last: dict[str, int] = {}
    n = 0
    for t, txt in rows:
        if txt and t - last.get(txt, -10 ** 9) < gap_us:
            n += 1
        last[txt] = t
    return n, len(rows)


def srt_cues(rel: str, doc: dict) -> int:
    """主轨 **SRT** 的条数。⚠ 和 JSON 里 `cues` 的长度**不是一个数**：
    剔完常驻 UI 变空的 cue 不写进 SRT（f5 上 5008 vs 4963）。文档引的是 SRT 那个。"""
    from flowocr.artifacts import srtio
    p = (ROOT / rel).parent / doc["provenance"]["main_srt"]
    return srtio.count_cues(p) if p.exists() else -1


def script_lines(rel: str) -> set[str]:
    from flowocr.analyze import gamescript as GS
    d = load(rel)
    if not d:
        return set()
    return {GS.cnorm(ln["text"]) for u in d["units"] for ln in u["lines"] if ln.get("text")}


def report(tag: str, off: dict, on: dict, ref: str, ra: str = "", rb: str = "") -> None:
    ka, kb = members(off), members(on)
    out, ins = ka - kb, kb - ka
    sa, sb = singles(off), singles(on)
    print(f"[{tag}] 区域 {len(off['regions'])} → {len(on['regions'])}"
          f"（只占一个窗 {sa[0]} → {sb[0]}、只有一条 cue {sa[1]} → {sb[1]}）；"
          f"主轨 SRT {srt_cues(ra, off)} → {srt_cues(rb, on)} 条"
          f"（JSON cue {len(main_track(off)['cues'])} → {len(main_track(on)['cues'])}）；"
          f"成员 {sum(ka.values())} → {sum(kb.values())}"
          f"（移出 {sum(out.values())}、移入 {sum(ins.values())}）")
    lines = script_lines(ref) if ref else set()
    if not lines:
        return
    from flowocr.analyze import gamescript as GS
    ta = Counter(GS.cnorm(off["events"][i]["text"])
                 for c in main_track(off)["cues"] for i in c["events"])
    tb = Counter(GS.cnorm(on["events"][i]["text"])
                 for c in main_track(on)["cues"] for i in c["events"])
    have = [t for t in ta if t in lines]
    gone = [t for t in have if tb.get(t, 0) == 0]
    sc = [k for k in out.elements() if GS.cnorm(k[3]) in lines]
    sub = [k for k in sc if tb.get(GS.cnorm(k[3]), 0) > 0]
    print(f"    移出的事件里文本是剧本行的 {len(sc)} 个：{len(sub)} 个在新主轨里**还有替身**、"
          f"{len(sc) - len(sub)} 个没有（涉及 {len(gone)} 种文本，占主轨出现过的 {len(have)} 种）")
    for t in gone[:5]:
        print(f"      · {t[:34]}")
    for tag2, d in (("off", off), ("on", on)):
        n, tot = redundancy(d)
        print(f"    [{tag2}] 主轨事件冗余（同一文本距上次 <5 s）：{n}/{tot} = {n / max(1, tot):.1%}")


def main() -> int:
    argv = sys.argv[1:]
    pairs = ([("自定", argv[0], argv[1], argv[2] if len(argv) > 2 else "")]
             if len(argv) >= 2 else PAIRS)
    n = 0
    for tag, a, b, ref in pairs:
        off, on = load(a), load(b)
        if not (off and on):
            print(f"[{tag}] 缺产物，跳过（{a}）")
            continue
        report(tag, off, on, ref, a, b)
        n += 1
    if not n:
        print("一对产物都没找到——先用 python -m flowocr.analyze.build_tracks 跑两臂")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
