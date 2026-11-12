"""主轨"按句成条"的量具与驱动（cue-shape 计划的第 1 步）：同一句被切成几条、合并清单、行序、字幕稿形状。

量的都是**阶段 2 的产物**（`<tag>-tracks.json` 和它旁边的主轨 SRT：行不拼、名牌留着），不读阶段 3 的 SRT——
改动做到后面阶段 3 的形状会变，尺子不能跟着变。字幕稿形状（M6）读阶段 3 的字幕稿，这正是它要量的东西。

    python dev_tools/pyrun.py dev_tools/cue_shape.py run --set dev --variant base           # 跑提取（已有就复用）+ 建轨 + default 预设
    python dev_tools/pyrun.py dev_tools/cue_shape.py run --set dev --variant off -- --cue-join off
    python dev_tools/pyrun.py dev_tools/cue_shape.py report --set dev --variant on [--vs off] [--merges out.md]

片段表在数据根的 `data/cue_shape_clips.json`（素材路径是本机的）；产物在数据根的 `out/cue-shape/<片段>/<臂>/`：`obs.jsonl` 是各变体共用的观测，`<变体>/` 下是建轨和渲染。
臂：`full` 不框选；`box` 用片段自带的 `--regions` 框住对话框（只有开发集有）。

指标（定义见 cue-shape 计划的「验证」一节）：
* **冗余 cue**（M1）：和下一条间隔 ≤ 1 s、全文（各行拼起来去空白）是下一条的前缀或和下一条开头的相似度 ≥ 0.85 的 cue，在主轨 SRT 上数；
* **合并清单**（M2）：`--vs` 的那个变体里被并进同一条 cue 的几条，列出来逐条看；
* **行序**（M4）：主轨 cue 里同一行有几个框的，导出顺序不是"先上后下、行内从左到右"的条数；
* **字幕稿形状**（M6）：主轨里有 ≥ 2 行正文的 cue，在 `default` 字幕稿里拆成 ≥ 2 条正文 Dialogue 的条数；例外（挂在几条 cue 里的事件、终点和这一句差得多的）单列。
* **叠加等价**（M5，`overlay`）：两个变体的 `default` 最终 ASS 逐帧、逐个事件框比"有没有字"，按预期的原因归类，剩下的是"其余"。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flowocr.artifacts import srtio, tracksio  # noqa: E402
from flowocr.output.script import ROW_END_TOL_US  # noqa: E402
from flowocr.paths import data_root  # noqa: E402

CLIPS: dict[str, dict] = {}
"""片段表：数据根的 `data/cue_shape_clips.json`（素材路径是本机的，不进版本库）；`load_clips` 读进来。
每个片段 `{set: dev|holdout, video, s, e, box?}`：`box` 是 `--regions` 的矩形，只有开发集有（跑框选那一臂）。"""


def load_clips() -> None:
    p = data_root() / "data" / "cue_shape_clips.json"
    if not p.is_file():
        raise SystemExit(f"没有片段表 {p}：格式见 CLIPS 的说明")
    CLIPS.update(json.loads(p.read_text(encoding="utf-8"))["clips"])


TAG = "c"
GAP_S = 1.0
HEAD_SIM = 0.85


def root() -> Path:
    return data_root() / "out" / "cue-shape"


def arms(cid: str) -> list[str]:
    return ["full", "box"] if CLIPS[cid].get("box") else ["full"]


def pick(sets: str) -> list[str]:
    want = set(sets.split(","))
    return [k for k, v in CLIPS.items() if v["set"] in want or k in want]


def py(*args: str) -> None:
    subprocess.run([sys.executable, *args], check=True)


def cmd_run(a) -> None:
    for cid in pick(a.set):
        c = CLIPS[cid]
        for arm in arms(cid):
            d = root() / cid / arm
            obs = d / "obs.jsonl"
            if not obs.exists():
                extra = []
                if arm == "box":
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "regions.json").write_text(json.dumps({"groups": [{"name": "box", "rects": c["box"]}]}), encoding="utf-8")
                    extra = ["--regions", str(d / "regions.json"), "--region-group", "box"]
                py("-m", "flowocr.extract.run_ocr2", c["video"], "--quality", "high", "--start", str(c["s"]), "--end", str(c["e"]), *extra, "--out", str(obs))
            out = d / a.variant
            log = out.parent / f"{a.variant}.log"
            out.mkdir(parents=True, exist_ok=True)
            with open(log, "w", encoding="utf-8") as f:
                subprocess.run([sys.executable, "-m", "flowocr.analyze.build_tracks", str(obs), "--outdir", str(out), "--tag", TAG, *a.build_args],
                               check=True, stdout=f, stderr=subprocess.STDOUT)
                if not a.no_render:
                    subprocess.run([sys.executable, "-m", "flowocr.output.render", str(out / f"{TAG}-tracks.json"), "--force"],
                                   check=True, stdout=f, stderr=subprocess.STDOUT)
            print(f"{cid}/{arm}/{a.variant} ok")


# ---------- 量 ----------

def squash(lines) -> str:
    return "".join("".join(lines).split())


def redundant(a: str, b: str) -> bool:
    if not a:
        return True
    return a == b or (SequenceMatcher(None, a, b[:len(a)]).ratio() >= HEAD_SIM and len(a) <= len(b) + 2)


def main_srt_cues(d: Path, doc: dict) -> list[srtio.Cue]:
    name = doc["provenance"].get("main_srt") or ""
    return srtio.read_srt(d / name) if name else []


def redund_pairs(cues: list[srtio.Cue]) -> list[tuple[srtio.Cue, srtio.Cue]]:
    return [(x, y) for x, y in zip(cues, cues[1:])
            if y.start - x.end <= GAP_S and redundant(squash(x.lines), squash(y.lines))]


def orphans(doc: dict, tr: dict, cue: dict) -> list[dict]:
    """被藏而同一行没有任何看得见的事件的（≥ 3 个内容字的才算：1–2 字的杂字自成一行照藏是有意的）——`seg_hide` 的不变量，要为 0。"""
    from flowocr.artifacts import evalkit
    hide = set(cue.get("hide") or ())
    vis = [doc["events"][i] for i in cue["events"] if i not in hide]
    out = []
    for i in hide:
        e = doc["events"][i]
        if evalkit.content_len(e["text"]) >= 3 and not any(tracksio.same_row(e["box"], v["box"]) for v in vis):
            out.append(e)
    return out


def doubled(doc: dict, tr: dict, cue: dict) -> list[tuple[dict, dict]]:
    """同一条 cue 里看得见的两个事件同一行、横向重叠过半、文本相似（相同、一方是另一方开头、或相似度 ≥ 0.85）——同一行字写了两遍，要为 0。"""
    vis = tracksio.cue_lines(doc, tr, cue)
    out = []
    for i, a in enumerate(vis):
        for b in vis[i + 1:]:
            if not tracksio.same_row(a["box"], b["box"]):
                continue
            ov = min(a["box"][2], b["box"][2]) - max(a["box"][0], b["box"][0])
            if ov < 0.5 * min(a["box"][2] - a["box"][0], b["box"][2] - b["box"][0]):
                continue
            x, y = squash([a["text"]]), squash([b["text"]])
            if x and y and (x.startswith(y) or y.startswith(x) or SequenceMatcher(None, x, y).ratio() >= HEAD_SIM):
                out.append((a, b))
    return out


def exported(doc: dict, tr: dict, cue: dict) -> list[dict]:
    return tracksio.cue_lines(doc, tr, cue)


def row_order_bad(evs: list[dict]) -> bool:
    rows = tracksio.row_index([e["box"] for e in evs])
    key = [(r, e["box"][0]) for r, e in zip(rows, evs)]
    return key != sorted(key)


def has_shared_row(evs: list[dict]) -> bool:
    rows = tracksio.row_index([e["box"] for e in evs])
    return len(set(rows)) < len(rows)


def script_lines(path: Path) -> list[tuple[str, set[int]]]:
    out = []
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("Dialogue:"):
            continue
        f = ln.split(",", 9)
        m = re.search(r"fo:ev=([0-9 ]+)", f[8])
        out.append((f[3], {int(x) for x in m.group(1).split()} if m else set()))
    return out


def measure(d: Path) -> dict | None:
    p = d / f"{TAG}-tracks.json"
    if not p.exists():
        return None
    doc = tracksio.load(p)
    tr = tracksio.main_track(doc)
    r = {"doc": doc, "cues": main_srt_cues(d, doc)}
    r["redund"] = redund_pairs(r["cues"])
    if tr is None:
        return r
    shared = bad = orph = dbl = 0
    multi = split = split_exc = 0
    count: dict[int, int] = {}
    for cue in tr["cues"]:
        for i in cue["events"]:
            count[i] = count.get(i, 0) + 1
    sl = [x for x in script_lines(d / f"{TAG}-default.script.ass") if x[0] in ("Body", "Extra")]
    for cue in tr["cues"]:
        orph += len(orphans(doc, tr, cue))
        dbl += len(doubled(doc, tr, cue))
        evs = [e for e in exported(doc, tr, cue) if "nameplate" not in (e.get("flags") or [])]
        if len(evs) >= 2 and has_shared_row(evs):
            shared += 1
            bad += row_order_bad(evs)
        if len(set(tracksio.row_index([e["box"] for e in evs]))) >= 2:
            multi += 1
            ids = {e["id"] for e in evs}
            hit = [x for x in sl if x[1] & ids]
            ends = sorted(e["t_end"] for e in evs)
            lo = max(ends, key=lambda x: (sum(1 for y in ends if x <= y <= x + ROW_END_TOL_US), x))
            off_end = any(not lo <= e["t_end"] <= lo + ROW_END_TOL_US for e in evs)
            if len(hit) >= 2:
                if any(count[i] > 1 for i in ids) or off_end:     # 例外：挂在几条 cue 里的、终点和这一句差得多的（字幕稿单独一条）
                    split_exc += 1
                else:
                    split += 1
    r.update(orph=orph, dbl=dbl, shared=shared, bad=bad, multi=multi, split=split, split_exc=split_exc,
             ui={t["id"]: set(t.get("ui_lines") or ()) for t in doc["tracks"]},
             panel=sum(1 for t in doc["tracks"] for c in t["cues"] if c.get("panel_like")))
    return r


def merges(on: dict, off: dict) -> list[tuple[srtio.Cue, list[srtio.Cue]]]:
    """`on` 的每条主轨 cue 整个盖住了 `off` 的几条（off 的 cue 首尾都在 on 的这条里，留 50 ms 的余量）。
    不按中点判：回抠之后相邻两条可以重叠一点，中点落进下一条不等于被并进去。"""
    out = []
    for c in on["cues"]:
        inside = [x for x in off["cues"] if x.start >= c.start - 0.05 and x.end <= c.end + 0.05]
        if len(inside) >= 2:
            out.append((c, inside))
    return out


def cmd_report(a) -> None:
    print(f"变体 {a.variant}" + (f"，对照 {a.vs}" if a.vs else ""))
    print("片段/臂        cue  冗余  藏空一行  一行两遍  同行多框 行序错  多行cue 拆开 拆开(例外)  panel  ui_lines 变化")
    tot = {"cue": 0, "red": 0, "bad": 0, "split": 0, "orph": 0, "dbl": 0}
    md = []
    for cid in pick(a.set):
        for arm in arms(cid):
            r = measure(root() / cid / arm / a.variant)
            if r is None:
                print(f"{cid}/{arm}: 没有产物")
                continue
            ui = ""
            if a.vs:
                b = measure(root() / cid / arm / a.vs)
                if b is not None and "ui" in r and "ui" in b:
                    diff = {k: (r["ui"].get(k, set()) ^ b["ui"].get(k, set())) for k in set(r["ui"]) | set(b["ui"])}
                    ui = "、".join(f"{k}:{sorted(v)}" for k, v in diff.items() if v) or "无"
                    ui += f"（panel {b.get('panel', 0)}→{r.get('panel', 0)}）"
                    for c, inside in merges(r, b):
                        md.append(f"- **{cid}/{arm}** {c.start:.2f}–{c.end:.2f}：" + " ‖ ".join(" / ".join(x.lines) for x in inside))
            print(f"{cid + '/' + arm:14} {len(r['cues']):4} {len(r['redund']):5} {r.get('orph', 0):8} {r.get('dbl', 0):8} {r.get('shared', 0):8} {r.get('bad', 0):6}"
                  f" {r.get('multi', 0):8} {r.get('split', 0):4} {r.get('split_exc', 0):11} {r.get('panel', 0):6}  {ui}")
            tot["cue"] += len(r["cues"])
            tot["red"] += len(r["redund"])
            tot["bad"] += r.get("bad", 0)
            tot["split"] += r.get("split", 0)
            tot["orph"] += r.get("orph", 0)
            tot["dbl"] += r.get("dbl", 0)
            if a.show:
                for x, y in r["redund"]:
                    print(f"    {x.start:9.2f}-{x.end:9.2f} {' / '.join(x.lines)[:70]}\n    {y.start:9.2f}-{y.end:9.2f} {' / '.join(y.lines)[:70]}\n")
    print(f"合计：cue {tot['cue']}、冗余 {tot['red']}、被藏而同一行没有看得见的 {tot['orph']}、一行写两遍 {tot['dbl']}、行序错 {tot['bad']}、多行 cue 拆开（非例外）{tot['split']}")
    if a.merges:
        Path(a.merges).write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"合并清单 {len(md)} 条 -> {a.merges}")


# ---------- M5：叠加等价（libass 逐帧比） ----------

OV_FPS = 2
OV_SCALE = 2
"""渲成原尺寸的 1/2 再比，省内存；差异框按这个比例换回原坐标。"""
OV_BRIGHT, OV_DARK, OV_MIN_PX = 160, 100, 30
"""算"有字"的亮度门、算"没字"的暗度门；一帧里不在预期区域的差异像素超过 `OV_MIN_PX` 才算这一帧有差异。"""
OV_MARGIN_S = 0.5


def ov_frames(d: Path, W: int, H: int, s: float, e: float):
    """把 `d/c-default.ass` 渲在黑底上，按 `OV_FPS` 逐帧给出灰度图（numpy，H/2 × W/2）。时间戳按原片：底片的 pts 挪到 `s`。"""
    import numpy as np
    w, h = W // OV_SCALE, H // OV_SCALE
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={OV_FPS}:d={e - s}",
           "-vf", f"setpts=PTS+{s}/TB,ass=c-default.ass,scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, cwd=d, stdout=subprocess.PIPE)
    n = w * h
    while True:
        buf = proc.stdout.read(n)
        if len(buf) < n:
            break
        yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
    proc.wait()


def ov_expected(d: Path, doc: dict) -> list[tuple[float, float, list[float], str]]:
    """预期会变的地方 `(起, 止, 框, 类)`（秒、原坐标）：被藏的半截行 / 杂行在它们自己的一生里（剔除），
    句条目里每一行在 `[首字, 全字]` 打字时段的框（打字节奏近似）。"""
    out = []
    tr = tracksio.main_track(doc)
    for c in (tr or {}).get("cues", []):
        bad = {e["id"] for e in orphans(doc, tr, c)}          # 违反不变量的藏法不算预期
        for i in c.get("hide") or ():
            if i in bad:
                continue
            ev = doc["events"][i]
            out.append((ev["t_start"] / 1e6, ev["t_end"] / 1e6, ev["box"], "剔除"))
    for ln in (d / f"{TAG}-default.script.ass").read_text(encoding="utf-8").splitlines():
        if not ln.startswith("Dialogue:"):
            continue
        f = ln.split(",", 9)
        t0 = srtio._parse_ass_time(f[1])
        fo = dict(kv.split("=", 1) if "=" in kv else (kv, "") for kv in f[8].split("fo:", 1)[-1].split(";"))
        boxes = [[float(v) for v in b.split()] for b in fo.get("box", "").split("|") if b.strip()]
        segs = fo.get("rowt") or fo.get("grow")
        tw = int(fo["tw"]) / 1000 if fo.get("tw") else 0.0
        if segs and len(segs.split("|")) == len(boxes):
            for b, sg in zip(boxes, segs.split("|")):
                a, z = (int(v) / 1000 for v in sg.split("+"))
                # 没有 rowt 时是整条匀速：每行都可能一直打到整条的 tw
                out.append((t0 + a, t0 + max(z, a, 0.0 if fo.get("rowt") else tw), b, "打字节奏"))
        elif tw and boxes:
            out.append((t0, t0 + int(fo["tw"]) / 1000, [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                                            max(b[2] for b in boxes), max(b[3] for b in boxes)], "打字节奏"))
    return out


OV_PRESENT = 100
"""判"这个框里有字"的亮度门：比 `OV_BRIGHT` 低，淡入淡出中途、半透明的字也算有字（只比有没有，不比亮度）。"""
OV_HAS_TEXT = 0.04
"""一个事件框里亮像素（≥ `OV_BRIGHT`）占框面积的这么多就算"这里有字"。按框判、不按像素判：同一句统一了字号之后字形会挪几个像素，
逐像素比几乎每帧都不同，要量的是"该有字的地方有没有字"。"""


def ov_compare(old_d: Path, new_d: Path, cid: str, shots: Path | None) -> dict:
    """逐帧、逐个事件框比"有没有字"：新的有旧的没有（新画了字）、旧的有新的没有（字没了）。
    落在预期里的不算：被藏的行在它自己的一生里（剔除）、句条目里一行的打字时段（打字节奏）、两臂常驻 UI 判定不同的行（UI 判定）、
    事件自己出现 / 消失的前后 0.1 s（边界）。剩下的是"其余"，两类都该是 0。"""
    import numpy as np
    c = CLIPS[cid]
    doc = tracksio.load(new_d / f"{TAG}-tracks.json")
    old_doc = tracksio.load(old_d / f"{TAG}-tracks.json")
    W, H = doc["size"]
    exp = ov_expected(new_d, doc)
    ui_changed = set()
    for tid in {t["id"] for t in doc["tracks"]} & {t["id"] for t in old_doc["tracks"]}:
        a_ui = set(tracksio.track(doc, tid).get("ui_lines") or ())
        b_ui = set(tracksio.track(old_doc, tid).get("ui_lines") or ())
        ui_changed |= a_ui ^ b_ui
    shown = set()
    for d in (doc, old_doc):
        for tr in d["tracks"]:
            if tr["kind"] in ("main", "nameplate") or tr["id"] == d["provenance"].get("main_track"):
                shown |= {i for cue in tr["cues"] for i in cue["events"]}
    evs = [doc["events"][i] for i in sorted(shown)]
    res = {"frames": 0, "diff": 0, "剔除": 0, "打字节奏": 0, "UI 判定": 0, "边界": 0, "新画了字": 0, "字没了": 0, "where": []}

    def has(img, box):
        x0, y0, x1, y1 = (max(0, int(v / OV_SCALE)) for v in box)
        cut = img[y0:y1 + 1, x0:x1 + 1]
        return cut.size > 0 and (cut >= OV_PRESENT).mean() >= OV_HAS_TEXT

    def overlaps(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
        return ix >= 0.5 * max(1e-9, (a[2] - a[0]) * (a[3] - a[1]))

    for k, (a, b) in enumerate(zip(ov_frames(old_d, W, H, c["s"], c["e"]), ov_frames(new_d, W, H, c["s"], c["e"]))):
        t = c["s"] + k / OV_FPS
        res["frames"] += 1
        hit = False
        for ev in evs:
            if not (ev["t_start"] / 1e6 - OV_MARGIN_S <= t <= ev["t_end"] / 1e6 + OV_MARGIN_S):
                continue
            ho, hn = has(a, ev["box"]), has(b, ev["box"])
            if ho == hn:
                continue
            hit = True
            kind = next((kd for t0, t1, box, kd in exp if t0 - OV_MARGIN_S <= t <= t1 + OV_MARGIN_S
                         and (overlaps(ev["box"], box) or overlaps(box, ev["box"]))), None)
            if kind is None and any(o["text"].strip() in ui_changed and overlaps(ev["box"], o["box"]) | overlaps(o["box"], ev["box"])
                                    and o["t_start"] / 1e6 - OV_MARGIN_S <= t <= o["t_end"] / 1e6 + OV_MARGIN_S for o in evs):
                kind = "UI 判定"                  # 这个框里（或压着它）的那行两臂一臂判成常驻 UI、一臂没判
            if kind is None and min(abs(t - ev["t_start"] / 1e6), abs(t - ev["t_end"] / 1e6)) <= 0.1:
                kind = "边界"
            if kind is None:
                kind = "新画了字" if hn else "字没了"
                res["where"].append((round(t, 2), kind, ev["id"], ev["text"][:24], ev["box"]))
                if shots is not None and len(res["where"]) <= 6:
                    shots.mkdir(parents=True, exist_ok=True)
                    import struct
                    import zlib
                    img = np.concatenate([a, b], axis=1)
                    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(img.shape[0]))

                    def chunk(tag, data):
                        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
                    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", img.shape[1], img.shape[0], 8, 0, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
                    (shots / f"{cid}-{t:.1f}-{kind}-{ev['id']}.png").write_bytes(png)
            res[kind] += 1
        res["diff"] += hit
    return res


def cmd_overlay(a) -> None:
    print(f"M5：{a.old} -> {a.new}，{OV_FPS} 帧/秒逐帧比（左旧右新的截图在 --shots）")
    print("片段/臂          帧数  有差异  （以下按事件框计）剔除  打字节奏  UI判定  边界  其余·新画了字  其余·字没了")
    tot = {"新画了字": 0, "字没了": 0}
    for cid in pick(a.set):
        for arm in arms(cid):
            base = root() / cid / arm
            r = ov_compare(base / a.old, base / a.new, cid, Path(a.shots) / f"{cid}-{arm}" if a.shots else None)
            print(f"{cid + '/' + arm:16} {r['frames']:5} {r['diff']:7} {r['剔除']:18} {r['打字节奏']:9} {r['UI 判定']:7} {r['边界']:5} {r['新画了字']:14} {r['字没了']:12}")
            for w in r["where"][:a.show]:
                print("     ", w)
            tot["新画了字"] += r["新画了字"]
            tot["字没了"] += r["字没了"]
    print(f"合计（事件框 × 帧）：其余·新画了字 {tot['新画了字']}、其余·字没了 {tot['字没了']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--set", required=True, help="dev / holdout / 片段 id，逗号分隔")
    r.add_argument("--variant", required=True)
    r.add_argument("--no-render", action="store_true")
    r.add_argument("build_args", nargs="*", help="`--` 之后的都传给 build_tracks")
    q = sub.add_parser("report")
    q.add_argument("--set", required=True)
    q.add_argument("--variant", required=True)
    q.add_argument("--vs", default="")
    q.add_argument("--merges", default="")
    q.add_argument("--show", action="store_true", help="逐条打出冗余的那几对")
    o = sub.add_parser("overlay", help="M5：两个变体的 default 最终 ASS 用 libass 逐帧比")
    o.add_argument("--set", required=True)
    o.add_argument("--old", required=True)
    o.add_argument("--new", required=True)
    o.add_argument("--shots", default="", help="把『其余』里的差异帧左旧右新存成 PNG（每臂最多 6 张）")
    o.add_argument("--show", type=int, default=8)
    a = ap.parse_args()
    load_clips()
    {"run": cmd_run, "report": cmd_report, "overlay": cmd_overlay}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
