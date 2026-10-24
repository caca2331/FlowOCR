"""逐次列出匹配器两种"不是逐字相等"的认领，给新素材上的观察项摆帧用：

* 打字机半截（`game_align.claim_prefix`）：cue 只读到前半，认成以它开头的整句；
* 改认长台词（`game_align.superset_hit`）：逐字相等的短行让给以它开头的更长台词；
* 读到一大段（`game_align.claim_infix`）：cue 读到的是剧本行中间或后半的一大段（游戏版本和文本包不同），认成那一整行（检测器三场修复计划 U5）。

前两种是 2026-10-06 进的默认（defaults.md 1.43），第三种 2026-10-10（1.53）；打字机半截和读到一大段都没有足够的独立正例，是观察项：新素材上建轨、匹配之后跑一遍，
看每次在 cue 起点、终点、终点 +1.5 s 三个时刻屏上是不是那一句（`--frames` 把这三帧抽出来）。

用法：python dev_tools/pyrun.py dev_tools/claim_audit.py <剧本 ref.json> <tracks.json> [--feed nonoise|main] [--frames <目录>]
  `--feed nonoise`（默认，和产品匹配器同一种喂法）；`main` 是主轨口径的尺（读 tracks 旁边的主轨 SRT）。
  `--frames`：每次打字机半截认领抽三帧（视频取 tracks 的 `provenance.video`），写成 `<目录>/<cue 起点>-<1|2|3>.png`。
不写产物。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr import provenance  # noqa: E402
from flowocr.analyze import game_align as GA  # noqa: E402
from flowocr.analyze import script_align as SA  # noqa: E402
from flowocr.analyze import scriptmatch as SM  # noqa: E402
from flowocr.artifacts import tracksio  # noqa: E402


FUZZY = 0.75
"""和 `scriptmatch --fuzzy` 的默认、内置 gametext 匹配器的默认同一个值。"""


def frames(video: Path, out: Path, t0: float, t1: float) -> None:
    """cue 起点 +0.2 s、终点 −0.1 s、终点 +1.5 s 三帧（起点那帧往后挪一点，免得落在上一句的最后一帧上）。"""
    out.mkdir(parents=True, exist_ok=True)
    for k, ts in enumerate((t0 + 0.2, max(t0, t1 - 0.1), t1 + 1.5), 1):
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{ts:.2f}", "-i", str(video),
                        "-frames:v", "1", str(out / f"{t0:09.1f}-{k}.png")], check=True)


def feed(tracks: Path, how: str) -> tuple[list, list[float], list[bool] | None]:
    """(subs, ends, main)：和 `GA.score` 的入参同形；main 是每条 cue 在不在主轨上（和产品匹配器同一份，`--feed main` 是 None）。"""
    doc = tracksio.load(tracks)
    if how == "main":
        name = doc["provenance"].get("main_srt")
        if not name:
            raise SystemExit(f"{tracks} 没有主轨（provenance.main_srt 为空），--feed main 没有可读的")
        srt = tracks.parent / name
        return GA.read_cues(srt), GA.read_ends(srt), None
    cues, evs_of, _ = SM.feed_from_doc(doc, "nonoise")
    return [(c.start, "\n".join(c.lines), "") for c in cues], [c.end for c in cues], SM.cue_main(cues, evs_of, doc)


def audit(ref: GA.Ref, subs: list, ends: list[float], main: list[bool] | None = None) -> tuple[list, list, list]:
    """跑一遍 `GA.score`，截下三种认领：prefix / infix = [(cue 下标, 剧本行)]，superset = [(短行, 长台词)]。"""
    prefix, superset, infix = [], [], []
    orig_p, orig_s, orig_i = GA.claim_prefix, GA.superset_hit, GA.claim_infix

    def spy_p(script, subs_, cand, sub_hit, sub_extra, heads, cn, ends_=None):
        before = list(sub_hit)
        n = orig_p(script, subs_, cand, sub_hit, sub_extra, heads, cn, ends_)
        prefix.extend((si, sub_hit[si]) for si in range(len(sub_hit)) if before[si] < 0 <= sub_hit[si])
        return n

    def spy_i(script, subs_, cand, sub_hit, sub_extra, heads, cn, ends_=None, main_=None):
        before = list(sub_hit)
        n = orig_i(script, subs_, cand, sub_hit, sub_extra, heads, cn, ends_, main_)
        infix.extend((si, sub_hit[si]) for si in range(len(sub_hit)) if before[si] < 0 <= sub_hit[si])
        return n

    def spy_s(hit, vs, cnd, sn, cn, sm):
        r = orig_s(hit, vs, cnd, sn, cn, sm)
        if r >= 0:
            superset.append((hit, r))
        return r

    script = GA.fresh_script(ref)
    GA.claim_prefix, GA.superset_hit, GA.claim_infix = spy_p, spy_s, spy_i
    try:
        GA.score(ref, script, subs, FUZZY, ends, main)
    finally:
        GA.claim_prefix, GA.superset_hit, GA.claim_infix = orig_p, orig_s, orig_i
    return ([(si, script[li]) for si, li in prefix], [(script[h], script[r]) for h, r in superset],
            [(si, script[li]) for si, li in infix])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ref", type=Path, help="gamescript 的剧本 JSON")
    ap.add_argument("tracks", type=Path, help="<tag>-tracks.json")
    ap.add_argument("--feed", choices=("nonoise", "main"), default="nonoise")
    ap.add_argument("--frames", type=Path, help="把每次打字机半截认领的三帧抽到这个目录")
    a = ap.parse_args(argv)
    subs, ends, main = feed(a.tracks, a.feed)
    prefix, superset, infix = audit(GA.load_ref(a.ref), subs, ends, main)
    one = lambda t: t.replace("\n", " / ").replace("\\N", " / ")   # noqa: E731
    print(f"打字机半截（claim_prefix）：{len(prefix)} 次")
    for si, e in prefix:
        t, raw, _ = subs[si]
        print(f"  {t:9.1f}–{ends[si]:9.1f} s  cue「{one(raw)[:40]}」 -> {e.key}「{one(e.text)[:40]}」")
        if a.frames:
            frames(provenance.local(tracksio.load(a.tracks)["provenance"]["video"]), a.frames, t, ends[si])
    print(f"读到一大段（claim_infix）：{len(infix)} 次")
    for si, e in infix:
        t, raw, _ = subs[si]
        print(f"  {t:9.1f}–{ends[si]:9.1f} s  cue「{one(raw)[:40]}」 -> {e.key}「{one(e.text)[:40]}」")
        if a.frames:
            frames(provenance.local(tracksio.load(a.tracks)["provenance"]["video"]), a.frames, t, ends[si])
    print(f"改认长台词（superset_hit）：{len(superset)} 次")
    for short, long_ in superset:
        print(f"  短 {short.key}「{one(short.text)[:24]}」 -> 长 {long_.key}「{one(long_.text)[:48]}」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
