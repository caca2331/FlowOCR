"""**探针**：把"疑似真漏"定位到视频时刻，并用**只看这一处**的判据归因。

来历（methodology-audit-2 报告）：`script_align --raw-ocr` 那一刀问的是
"这段文本在**整片的** OCR 里出现过没有"——**全局模糊搜**。
docs/dev-guide/verification.md「归因：问『这一处发生了什么』」 早就写过这条教训（"归因判据要问『这一处发生了什么』"），
但 `--raw-ocr` 又踩了一次，而且这次被短行放大：

    input5：疑似真漏 138 条，--raw-ocr 判 105 条（76%）是"匹配器丢的"，
            其中 **93/98 是纯标点行**（`……！`/`―ー`/`…………。`）。
    整片里这类行有几千条，所以"别处出现过"对它们几乎恒真。

抽帧一看就知道那个归因是反的：那些时刻**屏幕上确实有那行字，
而 det 在字幕带里一个正文框都没出**（名牌那种大字反而框得好好的）。

所以这里换一条判据，只问这一处：

  1. **定位**：基线 `lower{N}_jp.ass` 的正文就是剧本原文，拿它过一遍
     `script_align.align()`，就得到"剧本下标 -> 视频时间"。基线命中、
     我们漏的条目有**精确时间**；两边都漏的用前后最近的命中条目夹出区间。
     ⚠ 基线时间是**匹配器合成**的（`start = ocr起点 - display_delay`），
     只精确到 ±1 条 cue，所以下面的数是**下界**，逐条结论必须抽帧确认。
  2. **归因**：在 t±`--win` 的**字幕带**里，有没有**像正文的观测**
     （字高在 `--h-range` 内），并把 `conf ≥ --min-conf` 和低于门槛的**分开数**。
     有框但读的是别的话 = 时间偏了或聚类/匹配器丢的。

     ⚠ **不要把"带内无观测"直接说成 det 漏检**（methodology-audit-4 报告 §3）：
     obs 里的 `conf` 是 rec_score 不是检测置信度，而且这条流已经过了排区和
     极小裁剪过滤。"无观测"至少混着三种情况——det 没出框、框被过滤掉、
     这一刻没采样到——本工具分不开，要分得对目标帧补跑检测并保留全部框。

**`--band` 是素材相关的**（默认值来自 yuka 那五部 1920x1080 的直播录像：
正文 y 850-1010，名牌在 y 700-850，`Auto` 在左下，直播水印在右下）。
换素材必须重新量，照抄会得到一堆假阴性。

用法：
    python dev_tools/probe_gap_locate.py 5 --frames        # 打印 ffmpeg 抽帧命令
    python dev_tools/probe_gap_locate.py 1 --cat only-ours --show 20
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.artifacts import evalkit          # noqa: E402
from flowocr import paths  # noqa: E402
from flowocr.analyze import script_align as sa  # noqa: E402

BRK = chr(92) + "N"
PUNCT = re.compile(r"^[…。、！？!?\-―ー.\s（）()「」『』ッっ・]*$")
ROOT = paths.data_root()
"""**素材与产物的根**（worktree 里解析回主 checkout，见 flowocr.paths）。
不是这份代码所在的目录——槽里没有 `data/corpus` 和 `tmp/match`。"""


def clen(t: str) -> int:
    return len("".join(t.replace(BRK, "").split()))


def is_punct(t: str) -> bool:
    return bool(PUNCT.match(t.replace(BRK, "")))


def locate(a) -> list[dict]:
    """给每条疑似真漏配上时间。返回 [{text, punct, t, exact, both}]。"""
    script = sa.load_script(Path(a.script), Path(a.text))
    subs = sa.read_subs([Path(a.span_ref)])
    st = sa.align(script, subs, a.window, 0.75)
    t_of: dict[int, float] = {}
    for si, hit in enumerate(st["sub_hit"]):
        if hit >= 0 and hit not in t_of:
            t_of[hit] = subs[si][0]
    evalkit.require_nonzero(len(t_of), "基线在剧本上配上的条目",
                            "--span-ref 给的 .ass 正文得是剧本原文。")
    idx_sorted = sorted(t_of)

    by_key = {(e.key, e.src): i for i, e in enumerate(script)}
    ours = json.loads(Path(a.ours).read_text(encoding="utf-8"))["missed"]
    bkeys = {(e["key"], e["src"])
             for e in json.loads(Path(a.base).read_text(encoding="utf-8"))["missed"]}
    evalkit.require_nonzero(len(ours), "我们这侧的疑似真漏")

    out: list[dict] = []
    for e in ours:
        i = by_key.get((e["key"], e["src"]))
        if i is None:
            continue
        both = (e["key"], e["src"]) in bkeys
        if not both and i in t_of:
            t, exact = t_of[i], True
        else:
            lo = max((j for j in idx_sorted if j < i), default=None)
            hi = min((j for j in idx_sorted if j > i), default=None)
            if lo is None or hi is None:
                continue
            t, exact = (t_of[lo] + t_of[hi]) / 2, False
        out.append({"text": e["text"], "kind": e["kind"], "punct": is_punct(e["text"]),
                    "t": t, "exact": exact, "both": both})
    return out


def band_boxes(obs_path: Path, times: list[float], a) -> tuple[dict, dict]:
    """一趟扫 obs，收集每个目标时刻**字幕带里像正文的框**。

    返回 `(达到 conf 门槛的, 没达到的)` 两格——**必须分开**：obs 里的 `conf` 是
    rec_score，把低分的一并丢掉再宣称"没出框"，等于把识别没把握说成检测漏检
    （methodology-audit-4 报告 §3）。
    另外这里能看到的只是**过滤后的观测流**（排区、极小裁剪都已经筛过），
    所以"带内无观测"本身也不等于 det 没出框。
    """
    x0, y0, x1, y1 = a.band
    hlo, hhi = a.h_range
    got: dict[int, list] = {k: [] for k in range(len(times))}
    low: dict[int, list] = {k: [] for k in range(len(times))}
    if not times:
        return got, low
    lo, hi = min(times) - a.win, max(times) + a.win
    with obs_path.open(encoding="utf-8") as fh:
        for ln in fh:
            if '"t_us"' not in ln:
                continue
            o = json.loads(ln)
            t = o["t_us"] / 1e6
            if t > hi:
                break
            if t < lo:
                continue
            b = o["box"]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                continue
            if not (hlo <= b[3] - b[1] <= hhi):
                continue
            for k, tt in enumerate(times):
                if abs(t - tt) <= a.win:
                    # **低分的也收进来，另记一格**（methodology-audit-4 报告 §3）：
                    # obs 里的 `conf` 是 **rec_score**，不是检测置信度。
                    # 原来在这里按 conf 过滤，然后把空集合叫作"det 漏检"，
                    # 于是**检测成功、识别没把握**被算成了"没出框"。
                    (got if o["conf"] >= a.min_conf else low)[k].append(
                        (t, o["text"], o["conf"]))
    return got, low


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("film", type=int, help="yuka input 的序号")
    p.add_argument("--cat", choices=["all", "only-ours", "both"], default="all")
    p.add_argument("--ours", help="tmp/h2h/new-f{N}-ours.json")
    p.add_argument("--base", help="tmp/h2h/f{N}-base.json")
    p.add_argument("--obs", help="out/yuka/i{N}-full.jsonl")
    p.add_argument("--video", help="抽帧用的源视频（只读）")
    p.add_argument("--span-ref", help="基线 .ass（正文是剧本原文，用来定位）")
    p.add_argument("--script", default="data/corpus/mocai/script-raw/Scripts")
    p.add_argument("--text", default="data/corpus/mocai/script-jp/Scripts")
    p.add_argument("--window", type=int, default=400)
    p.add_argument("--win", type=float, default=0.75, help="时间窗 ±秒（默认一个采样点）")
    p.add_argument("--band", type=int, nargs=4, default=[380, 850, 1600, 1010],
                   metavar=("X0", "Y0", "X1", "Y1"), help="字幕带（**素材相关，换素材要重量**）")
    p.add_argument("--h-range", type=int, nargs=2, default=[30, 70], metavar=("LO", "HI"))
    p.add_argument("--min-conf", type=float, default=0.5)
    p.add_argument("--show", type=int, default=12)
    p.add_argument("--frames", action="store_true", help="打印 ffmpeg 抽帧命令（不自己跑）")
    a = p.parse_args()

    n = a.film
    a.ours = a.ours or f"tmp/h2h/new-f{n}-ours.json"
    a.base = a.base or f"tmp/h2h/f{n}-base.json"
    a.obs = a.obs or f"out/yuka/i{n}-full.jsonl"
    a.span_ref = a.span_ref or f"data/corpus/mocai/game_text/lower{n}_jp.ass"

    rows = locate(a)
    if a.cat == "only-ours":
        rows = [r for r in rows if not r["both"]]
    elif a.cat == "both":
        rows = [r for r in rows if r["both"]]
    rows.sort(key=lambda r: r["t"])
    ex = [r for r in rows if r["exact"]]
    print(f"input{n} / {a.cat}：可定位 {len(rows)} 条，"
          f"其中基线给了精确时间的 {evalkit.denom(len(ex), len(rows))}；"
          f"纯标点 {evalkit.denom(sum(1 for r in rows if r['punct']), len(rows))}")

    # 归因只在**有精确时间**的那批上做——区间中点会落到邻居身上，归因没意义
    got, low = band_boxes(Path(a.obs), [r["t"] for r in ex], a)
    # 三格分开数：带内**一个观测都没有** / 只有 rec 低分的观测 / 有合格观测。
    # 前两格的病因完全不同，混成一个"det 漏检"会把投入排到错的地方。
    stat = {True: [0, 0, 0], False: [0, 0, 0]}    # punct -> [总数, 无观测, 只有低分]
    for k, r in enumerate(ex):
        stat[r["punct"]][0] += 1
        if not got[k]:
            stat[r["punct"]][1 if not low[k] else 2] += 1
    for punct, name in ((True, "纯标点行"), (False, "有内容字行")):
        tot, none, lowc = stat[punct]
        print(f"  {name}：带内**一个观测都没有** {evalkit.denom(none, tot)}；"
              f"**只有 rec 低分（<{a.min_conf}）的观测** {evalkit.denom(lowc, tot)}")
    print("  ⚠ 这几格**不等于 det / rec 的归因**（methodology-audit-4 报告 §3）："
          "\n    obs 里的 `conf` 是 rec_score，不是检测置信度；观测流还经过排区和"
          "极小裁剪过滤。"
          "\n    所以『带内无观测』= det 没出框 / 框被过滤掉了 / 这一刻没采样到，"
          "**本工具分不开**——"
          "\n    要分开得对目标帧补跑一次检测、把全部框留下来再逐层对照。"
          "\n    另外基线时间由匹配器合成、只精确到 ±1 条 cue，逐条结论请抽帧确认。")

    print(f"\n前 {a.show} 条明细：")
    for k, r in enumerate(ex[:a.show]):
        inband = "、".join(f"{t[1][:14]}({t[2]:.2f})" for t in got[k][:3]) or "**无框**"
        print(f"  t={r['t']:9.1f} {'标点' if r['punct'] else '有字'} "
              f"{r['kind']:<9} 剧本[{r['text'][:22]}]  带内: {inband}")
    if a.frames:
        if not a.video:
            print("\n--frames 需要 --video 指出源视频（素材只读，本工具不写它）")
            return 2
        print("\n抽帧命令（自己跑，别在这里改素材）：")
        for r in ex[:a.show]:
            print(f"  ffmpeg -nostdin -loglevel error -ss {r['t']:.2f} -i \"{a.video}\""
                  f" -frames:v 1 -vf scale=1280:-1 -q:v 4 -y tmp/gap-frames/f{n}-{r['t']:.0f}.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
