"""按交付行为验收的小病例：真素材上摆帧标的"这一刻屏幕上有什么"，拿去量**交付的最终 ASS**（阶段 4）。

    python dev_tools/delivery_cases.py measure data/gt/delivery-gi.json            # 量时段：从标注时刻往前 / 后找画面变了的时刻，出核对用的条带图
    python dev_tools/delivery_cases.py score data/gt/delivery-gi.json <x.ass> [<另一份病例> <另一份 ASS> …]

**量什么**（每个标注项一个判定，和匹配器无关——只读 ASS 里画了什么、什么时候、画在哪）：

- `完整`：标注时刻在屏的事件里，锚点（`\\pos` / `\\move` 在那一刻的位置）落在标注框内的那几条按先上后下排，
  其中连续的一段拼起来和标注的完整正文相似度 ≥ `SIM`（去空白、NFKC）。一句多行台词被拆成几条事件、只要都画在框里，照样算完整。
- `残缺`：框里画了字，但拼起来对不上整句（只画了一行、读错太多）。
- `位置错`：整句画出来了，锚点却不在框里（09-26：移动的对白画在旧位置）。
- `缺`：框里什么都没画，别处也没有这句。
- 时间（只对标了 `t_on` / `t_off` 的项、且判成完整的）：事件起点不晚于 `t_on + TOL`（字全出来时译文已在），终点和 `t_off` 差 ≤ `TOL`；
  同一行被切成首尾相接的几条事件（按位置 / 轨迹切段、换了对齐方式）时按整条链算；一句拆成几行事件时取整句都在屏的那一段（起点取最晚的一行、终点取最早的一行）。

**量最终的 ASS**（阶段 4 的 `<tag>-<预设>.ass`）：字幕稿（`.script.ass`）里移动的字只画在一个位置、轨迹记在 `fo:` 参数里，
拿它量会把移动的字全判成位置错。

按 `kind`（`dialogue` 对白正文、`nameplate` 名牌和称号、`option` 选项、`ui` 界面 / HUD、`caption` 主播侧字幕、`chat` 评论墙）分项报召回。
默认预设只出主轨 + 名牌，所以 ui / caption / chat 和不在主轨上的选项在它上面本来就是缺——分项报正是为了把"该有的没有"和"本来不出"分开读；用途 1 看 `default_all`。

**选项**（`option`）和对白同一套判定（完整 / 残缺 / 位置错 / 缺，标了时段的再量时段），标法上多三条：
一个选项按钮一项，正文不含箭头 / 气泡图标 / 键位提示，按钮里折成两行的直接连起来（两行各画一条事件照样拼得上）；
框只框正文，相邻按钮的锚点落在框外，拼正文时拼不进来；`t_on` / `t_off` 是菜单淡入后第一次 / 淡出前最后一次完整可见，高亮在按钮间移动不算下屏。

**病例文件**（数据根 `data/gt/delivery-<片段>.json`，素材路径是本机的、不进版本库）：

    {"schema": "flowocr-delivery-cases/1", "clip": "gi", "video": "<路径>", "size": [1920, 1080], "labeled_by": "…", "note": "…",
     "moments": [{"t": 30.0, "items": [{"id": "g3", "kind": "dialogue", "text": "完整正文（多行直接连起来）",
                                        "box": [x0, y0, x1, y1], "t_on": 19.4, "t_off": 41.3}]}]}

- `t` 和 ASS 同一条时间轴（原视频的秒）；`box` 是那一刻画面上的位置（视频像素，`size` 是视频的宽高）——移动的字按那一刻的位置标；
  打分时 ASS 的锚点按它的 `PlayResX` / `PlayResY` 换到视频像素（两者不同时换算，缺一个就报错）；
- `t_on` = 整句第一次完整可见、`t_off` = 最后一次完整可见（秒，±0.1）；不标时段的项（UI、评论墙）只量有没有、画在哪。
  `measure` 从 `t` 往前 / 后按 10 fps 比框内最亮的像素（字），变化超过阈值处当候选，并出条带图（框周围的放大条、每 0.1 s 一行）给人核对后写回。
  候选只是起点：字重绘闪一帧、背景变亮、选项的高亮移到别的按钮（按钮底色变了）都会让它停早；移动的字框会移出原位，候选没有意义，要看更大范围的条带图。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

SIM = 0.85
TOL = 0.3
PAD = 8
KINDS = ("dialogue", "nameplate", "option", "ui", "caption", "chat")
DIALOGUE = re.compile(r"^Dialogue:\s*[^,]*,(\d+):(\d+):(\d+)\.(\d+),(\d+):(\d+):(\d+)\.(\d+),", re.M)
EM = 0.8
"""全角字宽 / `\\fs`：ASS 的字号约等于行高，常见中日文字体的全角字宽是它的 0.75–0.85。只用来估事件画在哪一片（判时段链接不接得上），不当精确几何。"""
NO_STYLE = {"fs": 18.0, "an": 2, "sx": 100.0, "sy": 100.0}
"""样式表里找不到时用 libass 的缺省样式。"""


def sec(h, m, s, cs) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def norm(s: str) -> str:
    return "".join(unicodedata.normalize("NFKC", s).split())


def sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


# ---- 读 ASS --------------------------------------------------------------------------------------------------
def styles(head: str) -> dict[str, dict]:
    """`[V4+ Styles]` 里每个样式的字号、对齐、横纵缩放（按 `Format:` 行找列）。"""
    out, cols = {}, None
    for line in head.splitlines():
        if line.startswith("Format:") and "Alignment" in line:
            cols = [c.strip() for c in line[7:].split(",")]
        elif line.startswith("Style:") and cols:
            v = dict(zip(cols, (c.strip() for c in line[6:].split(","))))
            out[v["Name"]] = {"fs": float(v["Fontsize"]), "an": int(v["Alignment"]),
                              "sx": float(v.get("ScaleX", 100)), "sy": float(v.get("ScaleY", 100))}
    return out


def parse_ass(path: Path) -> list[dict]:
    """每条 Dialogue：起止、纯文本、锚点（`\\pos` 或 `\\move` 的参数）、对齐方式、估的文字宽高（`size`，见 `EM`）。
    绘图事件（`\\p1`，底板）和空文本跳过。"""
    head = path.read_text(encoding="utf-8-sig")
    sty = styles(head)
    out = []
    for line in head.splitlines():
        m = DIALOGUE.match(line)
        if not m:
            continue
        f = line.split(",", 9)
        tags = "".join(re.findall(r"\{([^}]*)\}", f[9]))
        if re.search(r"\\p[1-9]", tags):
            continue
        rows = [r.replace("\\h", " ") for r in re.split(r"\\[Nn]", re.sub(r"\{[^}]*\}", "", f[9]))]
        plain = "".join(rows)
        if not plain.strip():
            continue
        pos = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", tags)
        mv = re.search(r"\\move\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)(?:,(\d+),(\d+))?\)", tags)
        st = sty.get(f[3].strip(), NO_STYLE)
        an = re.search(r"\\an([1-9])", tags)
        fs, sx, sy = (float(v[-1]) if v else st[k] for k, v in
                      (("fs", re.findall(r"\\fs([\d.]+)", tags)), ("sx", re.findall(r"\\fscx([\d.]+)", tags)),
                       ("sy", re.findall(r"\\fscy([\d.]+)", tags))))
        w = max(sum(1.0 if unicodedata.east_asian_width(c) in "WF" else 0.5 for c in r) for r in rows) * fs * EM * sx / 100
        out.append({"t0": sec(*m.groups()[:4]), "t1": sec(*m.groups()[4:]), "text": plain,
                    "pos": tuple(float(v) for v in pos.groups()) if pos else None,
                    "move": tuple(float(v) if v is not None else None for v in mv.groups()) if mv else None,
                    "an": int(an.group(1)) if an else st["an"], "size": (w, len(rows) * fs * sy / 100)})
    return out


def anchor(ev: dict, t: float) -> tuple[float, float] | None:
    """事件在时刻 t 的锚点；`\\move` 按 libass 的规矩线性插值（没给时刻 = 整条事件）。"""
    if ev["move"]:
        x1, y1, x2, y2, m1, m2 = ev["move"]
        dur = (ev["t1"] - ev["t0"]) * 1000
        m1, m2 = (m1 or 0.0), (m2 if m2 else dur)
        k = min(1.0, max(0.0, ((t - ev["t0"]) * 1000 - m1) / max(1.0, m2 - m1)))
        return x1 + (x2 - x1) * k, y1 + (y2 - y1) * k
    return ev["pos"]


def extent(ev: dict, t: float) -> tuple[float, float, float, float] | None:
    """事件在时刻 t 大致画在哪一片 (x0, y0, x1, y1)：锚点按对齐方式（`\\an`：列 左 / 中 / 右，行 底 / 中 / 顶）摊开估的文字宽高。"""
    a = anchor(ev, t)
    if a is None:
        return None
    (w, h), col, row = ev["size"], (ev["an"] - 1) % 3, (ev["an"] - 1) // 3
    x0, y1 = a[0] - w * col / 2, a[1] + h * row / 2
    return x0, y1 - h, x0 + w, y1


# ---- 打分 ----------------------------------------------------------------------------------------------------
def best_run(text: str, seq: list[dict]) -> tuple[float, list[dict]]:
    """按阅读顺序排好的事件里，拼起来和 `text` 最像的**连续一段**（框里可能还画着相邻的名牌 / 称号，不能整框一起拼）。"""
    best, got = 0.0, []
    for i in range(len(seq)):
        for j in range(i + 1, len(seq) + 1):
            s_ = sim(text, "".join(e["text"] for e in seq[i:j]))
            if s_ > best:
                best, got = s_, seq[i:j]
    return best, got


LINK_PX = 60


def near(p, q) -> bool:
    """接缝两边是不是画在同一处：纵向中心相距 ≤ `LINK_PX`、横向范围重叠到窄的那边的一半以上。
    比估的范围、不比锚点：同一行从 `\\an4`（锚点在行首）换成 `\\an5`（行中）时锚点差半个行宽。"""
    if p is None or q is None:
        return False
    over = min(p[2], q[2]) - max(p[0], q[0])
    return abs((p[1] + p[3]) - (q[1] + q[3])) / 2 <= LINK_PX and over >= min(p[2] - p[0], q[2] - q[0]) / 2


def chain(evs: list[dict], hit: list[dict]) -> tuple[float, float]:
    """命中的每条事件按"同一段文本、首尾相接（间隔 ≤ 0.1 s）、接缝处画在同一处（`near`）"往前后接上：
    按位置切段、按轨迹切段的同一行是几条事件，时段看整条链，不看标注时刻那一段。
    要画在同一处，是因为同一句可能同时画在别处（对话记录、另一块区域的回显），那不是这一条的延续。
    一句拆成几行事件时取各行的链都在屏的那一段：起点取最晚的、终点取最早的。"""
    def one(ev: dict) -> tuple[float, float]:
        same = [e for e in evs if norm(e["text"]) == norm(ev["text"])]
        lo = hi = ev
        moved = True
        while moved:
            moved = False
            for e in same:
                if e["t0"] < lo["t0"] and abs(e["t1"] - lo["t0"]) <= 0.1 and near(extent(e, e["t1"] - 0.01), extent(lo, lo["t0"])):
                    lo, moved = e, True
                if e["t1"] > hi["t1"] and abs(e["t0"] - hi["t1"]) <= 0.1 and near(extent(e, e["t0"]), extent(hi, hi["t1"] - 0.01)):
                    hi, moved = e, True
        return lo["t0"], hi["t1"]
    spans = [one(e) for e in hit]
    return max(s[0] for s in spans), min(s[1] for s in spans)


def order(ea: list) -> list[dict]:
    return [e for e, _ in sorted(ea, key=lambda x: (round(x[1][1] / 8), x[1][0]))]


def judge(item: dict, t: float, evs: list[dict]) -> dict:
    """锚点要落在标注框里（四边各放宽 `PAD` 像素：居中锚点本来就在框里，左上锚点贴着框边）。"""
    x0, y0, x1, y1 = item["box"]
    live = [(e, anchor(e, t)) for e in evs if e["t0"] <= t < e["t1"]]
    live = [(e, a) for e, a in live if a is not None]
    inside = order([(e, a) for e, a in live if x0 - PAD <= a[0] <= x1 + PAD and y0 - PAD <= a[1] <= y1 + PAD])
    s_in, hit = best_run(item["text"], inside)
    res = {"id": item["id"], "kind": item["kind"], "sim": round(s_in, 3), "got": "".join(e["text"] for e in hit)}
    if s_in >= SIM:
        res["verdict"] = "完整"
        on, off = chain(evs, hit)
        if item.get("t_on") is not None:
            res["on_ok"] = on <= item["t_on"] + TOL
        if item.get("t_off") is not None:
            res["off_ok"] = abs(off - item["t_off"]) <= TOL
        res["t"] = [round(on, 2), round(off, 2)]
        return res
    s_all, elsewhere = best_run(item["text"], order(live))
    if s_all >= SIM:
        res["verdict"] = "位置错"
        res["got"] = "".join(e["text"] for e in elsewhere)
    elif inside:
        res["verdict"] = "残缺"
    else:
        res["verdict"] = "缺"
    return res


def play_res(path: Path) -> tuple[float, float] | None:
    """ASS 的 `PlayResX` / `PlayResY`（锚点所在的坐标系）；没写就是 None。"""
    head = path.read_text(encoding="utf-8-sig")
    x, y = (re.search(rf"^PlayRes{k}:\s*([\d.]+)", head, re.M) for k in "XY")
    return (float(x.group(1)), float(y.group(1))) if x and y else None


def to_video(evs: list[dict], res: tuple[float, float] | None, size: list[int] | None) -> list[dict]:
    """锚点从 ASS 的 PlayRes 坐标换到病例的视频像素（标注框的坐标系）。两边一样就原样返回；
    病例没写 `size` 或 ASS 没写 PlayRes 时没法核对，报错——两者不同时按 ASS 坐标比框会整批判成位置错（验收 peer 2026-10-06）。"""
    if size is None or res is None:
        raise ValueError(f"坐标系没法核对：病例的 size = {size}、ASS 的 PlayRes = {res}（病例要写 \"size\": [宽, 高]）")
    sx, sy = size[0] / res[0], size[1] / res[1]
    if (sx, sy) == (1.0, 1.0):
        return evs
    def sc(v, k):
        return None if v is None else v * (sx if k % 2 == 0 else sy)
    out = []
    for e in evs:
        mv = e["move"]
        out.append({**e, "pos": tuple(sc(v, k) for k, v in enumerate(e["pos"])) if e["pos"] else None,
                    "move": tuple(sc(v, k) for k, v in enumerate(mv[:4])) + tuple(mv[4:]) if mv else None,
                    "size": (e["size"][0] * sx, e["size"][1] * sy)})
    return out


def score_case(case: dict, ass: Path) -> list[dict]:
    """⚠ 没有 `\\pos` / `\\move` 的事件锚点为空，画了也判不进任何框（本项目的叠加预设每条都带 `\\pos`）。"""
    evs = to_video(parse_ass(ass), play_res(ass), case.get("size"))
    return [judge(it, m["t"], evs) | {"t_moment": m["t"]} for m in case["moments"] for it in m["items"]]


def cmd_score(a) -> int:
    if len(a.pairs) % 2:
        raise SystemExit("参数要成对：<病例.json> <x.ass> …")
    rows = []
    for cp, ap in zip(a.pairs[::2], a.pairs[1::2]):
        case = json.loads(Path(cp).read_text(encoding="utf-8"))
        res = score_case(case, Path(ap))
        print(f"== {case['clip']}  {ap}")
        for r in res:
            tm = "" if "t" not in r else (f"  时段 {r['t'][0]}–{r['t'][1]}"
                                          + ("" if r.get("on_ok", True) else " ⚠起点晚")
                                          + ("" if r.get("off_ok", True) else " ⚠终点差"))
            print(f"  {r['id']:<5}{r['kind']:<10}{r['verdict']:<4} sim {r['sim']:.2f}{tm}  画的：{r['got'][:40]!r}")
        rows += res
    print("\n按类：完整 / 位置错 / 残缺 / 缺（总数）；时段对（完整且标了时段的里）")
    for k in KINDS + ("全部",):
        sel = [r for r in rows if k in ("全部", r["kind"])]
        if not sel:
            continue
        c = Counter(r["verdict"] for r in sel)
        timed = [r for r in sel if r["verdict"] == "完整" and ("on_ok" in r or "off_ok" in r)]
        ok_t = sum(1 for r in timed if r.get("on_ok", True) and r.get("off_ok", True))
        print(f"  {k:<10}{c['完整']} / {c['位置错']} / {c['残缺']} / {c['缺']}（{len(sel)}）"
              + (f"；时段对 {ok_t}/{len(timed)}" if timed else ""))
    return 0


# ---- 量时段 --------------------------------------------------------------------------------------------------
STEP = 0.1
SPAN = 15.0
DIFF = 40.0
"""字的像素（框里最亮的 15%）平均变化超过它，算"这句不在了"。"""


def crops(video: str, box: list[int], t0: float, t1: float, pad: int = 6) -> tuple[list[float], list[np.ndarray]]:
    q = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", video], capture_output=True, text=True)
    W, H = (int(v) for v in q.stdout.strip().split(",")[:2])
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    t0 = max(0.0, t0)
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t0:.3f}", "-i", video, "-t", f"{t1 - t0:.3f}",
                        "-vf", f"fps={1 / STEP:g},crop={w}:{h}:{x0}:{y0}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    if p.returncode:
        raise SystemExit(f"ffmpeg 失败：{p.stderr.decode(errors='replace')[:300]}")
    n = w * h
    fr = [np.frombuffer(p.stdout[i:i + n], np.uint8).reshape(h, w).astype(np.int16) for i in range(0, len(p.stdout) - n + 1, n)]
    return [t0 + k * STEP for k in range(len(fr))], fr


def cmd_measure(a) -> int:
    from PIL import Image, ImageDraw
    path = Path(a.case)
    case = json.loads(path.read_text(encoding="utf-8"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for m in case["moments"]:
        for it in m["items"]:
            if it["kind"] in ("ui", "chat"):
                continue
            ts, fr = crops(case["video"], it["box"], m["t"] - SPAN, m["t"] + SPAN)
            k0 = min(range(len(ts)), key=lambda k: abs(ts[k] - m["t"]))
            ref = fr[k0]
            mask = ref >= np.percentile(ref, 85)             # 只比框里最亮的那些像素（字），背景在动不算变
            d = [float(np.abs(f - ref)[mask].mean()) for f in fr]
            lo = k0
            while lo > 0 and d[lo - 1] < DIFF:
                lo -= 1
            hi = k0
            while hi < len(fr) - 1 and d[hi + 1] < DIFF:
                hi += 1
            print(f"{it['id']:<5}{it['kind']:<10}候选 t_on {ts[lo]:.1f}  t_off {ts[hi]:.1f}"
                  f"（标注里 {it.get('t_on')} / {it.get('t_off')}）")
            for tag, k in (("on", lo), ("off", hi)):
                rows = [(ts[j], fr[j]) for j in range(max(0, k - 5), min(len(fr), k + 6))]
                hgt = rows[0][1].shape[0]
                sheet = Image.new("L", (rows[0][1].shape[1] + 90, (hgt + 4) * len(rows)), 24)
                dr = ImageDraw.Draw(sheet)
                for r, (tt, f) in enumerate(rows):
                    sheet.paste(Image.fromarray(f.clip(0, 255).astype(np.uint8)), (90, r * (hgt + 4)))
                    dr.text((4, r * (hgt + 4) + 2), f"{tt:.1f}{' *' if tt == ts[k] else ''}", fill=255)
                sheet.save(out / f"{case['clip']}-{it['id']}-{tag}.png")
    print(f"条带图 -> {out}（每行 0.1 s，* 是候选；核对后把 t_on / t_off 写回病例文件）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("pairs", nargs="+", help="<病例.json> <x.ass>，可多对")
    s.set_defaults(fn=cmd_score)
    m = sub.add_parser("measure")
    m.add_argument("case")
    m.add_argument("--out", default="tmp/delivery-strips")
    m.set_defaults(fn=cmd_measure)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
