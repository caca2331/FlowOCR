"""用途 2 的多臂对照（h-ratio 计划）：每条臂 × 每段素材 -> build_tracks -> scriptmatch（默认 --feed nonoise）+ game_align（主轨，参考）。
在数据根里跑子进程；代码是这份 checkout 的 src（槽里跑就是槽里的代码）。每条结果追加进 <out>/results.jsonl。

    python dev_tools/use2_arms.py <out> <臂名=build_tracks 参数>... [--tags a,b 或 tag@obs 路径] [--jobs 4]
    臂的写法：name=--region-h-ratio 0 --slot-h-ratio 0（等号后面原样当 build_tracks 参数，空 = 默认）
比臂的代价侧用 `dup_other`（带内重复认领里回显判定为"其他"的，报警看它）；`dup_band`（带内合计，含重播和回显）、`dup`（全部）留作参考。
`gametext` 素材上的主尺（owner 2026-10-07）是 `dev_tools/miss_detect.py` 在匹配产物上量的"该出现的行的输出覆盖"，只数有内容（≥3 字）的行：
`md_*` 几栏，含义见 `miss_detect.ruler`；主轨命中（`main_hit`，game_align）留作诊断栏。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "src"))
sys.path.insert(0, str(CODE / "dev_tools"))
from flowocr import paths  # noqa: E402
import miss_detect as MD  # noqa: E402

ROOT = paths.data_root()
PY = sys.executable
ENV = dict(os.environ, PYTHONPATH=str(CODE / "src"), PYTHONIOENCODING="utf-8")
TAGS = "gi-s1 gi-s2 gi-s3 hsr-s1 hsr-s2 zzz-s1 zzz-s2 gi1 gi2 hsr zzz".split()


def obs_of(tag: str) -> str:
    return tag.split("@", 1)[1] if "@" in tag else f"out/gamestream/{tag}.jsonl"


def base_tag(tag: str) -> str:
    return tag.split("@", 1)[0]


def run(cmd, log):
    r = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace")
    Path(log).write_text(r.stdout + r.stderr, encoding="utf-8")
    return r.returncode, r.stdout + r.stderr


def one(out: Path, arm: str, args: list[str], tag: str) -> dict:
    d = out / arm / tag.replace("@", "_").replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    rc, _ = run([PY, "-m", "flowocr.analyze.build_tracks", obs_of(tag), "--outdir", str(d), "--tag", base_tag(tag),
                 "--matcher", "gametext", *args], d / "build.log")
    res = {"arm": arm, "tag": tag, "build_rc": rc}
    if rc:
        return res
    tr = str(d / f"{base_tag(tag)}-tracks.json")
    ref = f"out/gametext/{base_tag(tag)}-ref.json"
    rc, txt = run([PY, "-m", "flowocr.analyze.scriptmatch", ref, "--subs", tr, "--out", str(d / "matched.json"),
                   "--lang", "cn"], d / "match.log")
    res["match_rc"] = rc
    # **读不到数就是没有数**，不许记成 0（2026-09-24 Codex 审计 P2：失败的臂会看起来更好）；缺了的键进 `missing`
    pats = {("denom", "cover"): r"覆盖：该上屏的 (\d+) 条里 (\d+)/", ("_", "dup"): r"重复认领：(\d+) 条被 ≥2 条 cue 认领，多出 (\d+) 次",
            # 比臂的代价侧看带内"其他"（回显判定，2026-10-06：重播、回显不是错）；带内合计留作参考列
            ("dup_band",): r"带内（该在字幕带的台词）\d+ 条、多出 (\d+) 次",
            ("dup_other",): r"\*\*其他 (\d+)（报警看这个）\*\*",
            ("suspect",): r"<0\.85 的 (\d+) 条", ("cues_fed",): r"cue (\d+) 条 ->"}
    rc2, txt2 = run([PY, "-m", "flowocr.analyze.game_align", ref, "--subs", tr, "--show", "0"], d / "align.log")
    res["align_rc"] = rc2
    (d / "miss.json").unlink(missing_ok=True)      # 别读到上一次的
    rc3, _ = run([PY, str(CODE / "dev_tools" / "miss_detect.py"), str(d / "matched.json"), "--no-pack", "--show", "0",
                  "--out", str(d / "miss.json")], d / "miss.log") if rc == 0 else (1, "")
    res["miss_rc"] = rc3
    if (d / "miss.json").exists() and rc == 0:
        res.update(MD.ruler(json.loads((d / "miss.json").read_text(encoding="utf-8"))))
    pats2 = {("main_hit",): r"命中 (\d+)（", ("main_cues",): r"：(\d+) 条 cue；"}
    missing = []
    for src, table in ((txt if rc == 0 else "", pats), (txt2 if rc2 == 0 else "", pats2)):
        for keys, pat in table.items():
            m = re.search(pat, src)
            if not m:
                missing += [k for k in keys if k != "_"]
                continue
            res.update({k: int(v) for k, v in zip(keys, m.groups()) if k != "_"})
    if "suspect" in missing and rc == 0 and "改写幅度" not in txt:
        missing.remove("suspect")              # 一条 cue 都没换成原文时 scriptmatch 不打这一行，0 是真值
        res["suspect"] = 0
    if "md_den" not in res:
        missing.append("md_den")
    if missing or rc or rc2 or rc3:
        res["error"] = f"match_rc {rc} / align_rc {rc2} / miss_rc {rc3}（1 也可能是不变量没过）；读不到 {missing}（看 {d}/*.log）"
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("arms", nargs="+")
    ap.add_argument("--tags", default=",".join(TAGS))
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    arms = {}
    for s in a.arms:
        name, _, rest = s.partition("=")
        arms[name] = rest.split()
    tags = a.tags.split(",")
    jobs = [(arm, args, tag) for tag in tags for arm, args in arms.items()]
    bad = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        for res in ex.map(lambda j: one(out, *j), jobs):
            print(json.dumps(res, ensure_ascii=False), flush=True)
            bad += bool(res.get("error") or res.get("build_rc"))
            with open(out / "results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
    if bad:
        raise SystemExit(f"**{bad} 条没跑成 / 读不到数**（看各条的 error / build_rc）——这批结果不能拿来比臂")


if __name__ == "__main__":
    main()
