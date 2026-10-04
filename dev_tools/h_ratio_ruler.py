"""字高比两道门的各条臂，在聚类的尺上打分（误合并 / 误拆分分开报；h-ratio 计划）。

    python dev_tools/h_ratio_ruler.py data/gt/slotpairs-*.json [--split dev]     # 在数据根里跑

臂 = (窗内 region_h_ratio, 跨窗 slot_h_ratio)。窗内那道门变了要重建窗区域（cluster_layers 按参数缓存），
跨窗那道只换缝合。run 在各臂之间一一对应（run 在分区之前就建好了），所以按 run 下标给分配。
另外逐对记"哪条臂和默认判得不一样"，打出翻转的对（原标签 + note），看翻的是不是名牌 / 正文那一类。
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr.analyze import cluster_layers as CL  # noqa: E402
from flowocr.analyze import slot_pairs as SP  # noqa: E402

ARMS = {
    "1.7/1.7": (1.7, 1.7),
    "0/1.7": (0.0, 1.7),
    "1.7/0": (1.7, 0.0),
    "0/0": (0.0, 0.0),
    "2.5/2.5": (2.5, 2.5),
}


def arm_fn(rh: float, sh: float):
    def fn(L):
        Lv = L if rh == 1.7 else CL.load(L.tag, ["--region-h-ratio", str(rh)])
        assert len(Lv.runs) == len(L.runs), "run 对不上"
        slots = CL.stitch(Lv, h_ratio=sh)
        return {i: k for k, sl in enumerate(slots) for w in sl for i in Lv.win_regions[w]["runs"]}
    return fn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("gt", nargs="+")
    ap.add_argument("--split", default="dev")
    a = ap.parse_args()
    extra = {name: arm_fn(*v) for name, v in ARMS.items()}
    tot = {name: Counter() for name in ARMS}
    per_tag = {}
    flips = []
    for p in a.gt:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        if a.split and doc.get("split") != a.split:
            continue
        res = SP.score_doc(doc, {}, extra)
        for name in ARMS:
            tot[name].update(res[name])
            per_tag.setdefault(doc["tag"], {name: Counter() for name in ARMS})[name].update(res[name])
        asg = SP._ASSIGN[doc["tag"]]
        for it in doc["items"]:
            if it["label"] not in ("same", "different"):
                continue
            v = SP.verdicts(asg, it)
            if v is None:
                continue
            base = v["1.7/1.7"]
            for name in ARMS:
                if v[name] != base:
                    flips.append((name, doc["tag"], it["id"], it["stratum"], it["label"], v[name], (it.get("note") or "")[:90]))

    def cell(c, strata):
        fm = sum(c[(s, "different", "together")] for s in strata)
        fs = sum(c[(s, "same", "apart")] for s in strata)
        n = sum(v for k, v in c.items() if k[0] in strata and len(k) == 3)
        return f"{fm}+{fs}={fm + fs}/{n}"

    print("每格 = 误合并 + 误拆分 = 错 / 分母（标了 same / different 的对）")
    print(f"{'臂（窗内/跨窗）':<16}{'S2-4（率）':>18}{'S1（分歧）':>18}{'全部':>18}")
    for name, c in tot.items():
        print(f"{name:<16}{cell(c, ('S2', 'S3', 'S4')):>18}{cell(c, ('S1',)):>18}{cell(c, ('S1', 'S2', 'S3', 'S4')):>18}")
    print("\n按素材（全部层）：")
    for tag, d in per_tag.items():
        print(f"  {tag:<10}" + "".join(f"{name} {cell(c, ('S1', 'S2', 'S3', 'S4')):>14}  " for name, c in d.items()))
    print(f"\n和默认判得不一样的对（{len(flips)}）：臂 / 素材 / id / 层 / 标签 / 这条臂判对了没有 / note")
    for f in flips:
        print("  ", f)


if __name__ == "__main__":
    main()
