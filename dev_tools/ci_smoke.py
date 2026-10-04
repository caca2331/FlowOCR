"""三步冒烟（CI 用，本地也能跑）：ffmpeg 生成一段带 5 句底部字幕的 10 秒视频 -> 提取 -> 建轨 -> 默认预设出 ASS，核对读回来的字。

    python dev_tools/ci_smoke.py [--device cpu] [--workdir tmp/ci-smoke]

只证明"装好的包在这台机器上三步都能跑通、读得出最简单的字"，不量质量。要 ffmpeg / ffprobe 在 PATH 上（drawtext 要字体：
Windows 用 Arial）；模型第一次会自动下载。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

LINES = [("HELLO FLOWOCR", 0.5, 2.4), ("SUBTITLES ON THE BOTTOM", 2.6, 4.4), ("EACH LINE HAS A START", 4.6, 6.4),
         ("AND AN END TIME", 6.6, 8.4), ("GOODBYE", 8.6, 9.8)]


def font() -> str:
    for cand in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(cand).is_file():
            return cand.replace(":", "\\:")
    raise SystemExit("找不到 drawtext 能用的字体")


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode:
        raise SystemExit(f"失败（退出码 {r.returncode}）：{' '.join(cmd)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--workdir", default="tmp/ci-smoke")
    a = ap.parse_args()
    wd = Path(a.workdir)
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    style = f"fontfile='{font()}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=300"
    vf = ",".join(f"drawtext={style}:text='{t}':enable='between(t,{s},{e})'" for t, s, e in LINES)
    video = wd / "smoke.mp4"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x203040:s=640x360:d=10:r=30",
         "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)])
    bin_dir = Path(sys.executable).parent
    obs, out = wd / "smoke.jsonl", wd / "out"
    run([str(bin_dir / "flowocr-ocr"), str(video), "--out", str(obs), "--device", a.device])
    run([str(bin_dir / "flowocr-tracks"), str(obs), "--outdir", str(out), "--tag", "smoke"])
    run([str(bin_dir / "flowocr-render"), str(out / "smoke-tracks.json")])

    from flowocr.artifacts import srtio, tracksio
    main_srt = tracksio.load(out / "smoke-tracks.json")["provenance"]["main_srt"]
    if not main_srt:
        raise SystemExit("没有主轨")
    got = [c.text(" ").strip() for c in srtio.read_srt(out / main_srt)]
    want = [t for t, _, _ in LINES]
    print("读到：", got)
    if got != want:
        raise SystemExit(f"主轨和预期不一致：\n  预期 {want}\n  读到 {got}")
    if not (out / "smoke-default.ass").is_file():
        raise SystemExit("默认预设没出 smoke-default.ass")

    # 阶段 4 单独重跑：字幕稿没改、里面也没有 `;frame` 参数，所以带上外部效果模块重出的 ASS 应和 render 写的逐字节相同。
    # 证的是"第 4 步能单独跑、外部效果模块加载得进来"；效果本身由守卫覆盖
    redo = out / "smoke-retypeset.ass"
    fx = Path(__file__).resolve().parents[1] / "examples" / "fx_minimal.py"
    run([str(bin_dir / "flowocr-typeset"), str(out / "smoke-default.script.ass"), "-o", str(redo), "--fx", str(fx)])
    want_b, got_b = (out / "smoke-default.ass").read_bytes(), redo.read_bytes()
    if want_b != got_b:
        a_lines, b_lines = want_b.splitlines(), got_b.splitlines()
        i = next((k for k, (x, y) in enumerate(zip(a_lines, b_lines)) if x != y), min(len(a_lines), len(b_lines)))
        raise SystemExit(f"单独重跑第 4 步的输出和 render 写的不同，第 {i + 1} 行起：\n"
                         f"  render  {a_lines[i] if i < len(a_lines) else '<没有>'!r}\n"
                         f"  typeset {b_lines[i] if i < len(b_lines) else '<没有>'!r}")
    print("冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
