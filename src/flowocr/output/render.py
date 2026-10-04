"""阶段 3 的薄入口：拿一份**阶段 2 的产物**，跑一个输出预设。

    python -m flowocr.output.render out/x/x-tracks.json --outdir tmp/x                       # 默认预设 default
    python -m flowocr.output.render out/x/x-tracks.json --preset srt_main --outdir tmp/x
    python -m flowocr.output.render out/x/x-matched.json --preset dev --outdir tmp/x --opt lang=cn
    python -m flowocr.output.render out/x/x-tracks.json --preset D:/mine/preset.py --outdir tmp/x --opt start=full

吃两种产物（2026-09-22 起，Codex 审计 P2：原来写死 `tracksio.load`，匹配结果连预设的门都进不去）：

| schema | 读法 | 内置预设 |
| --- | --- | --- |
| `flowocr-tracks/1` | `tracksio.load` | 叠加三个（`default` / `default_all` / `dev`，叠 OCR 原文）、`script`（只写字幕稿）、`srt_main`（只取主轨） |
| `flowocr-matched/2` | `matchedio.load` | 叠加三个（叠剧本译文）、`script`、`matched_srt`（拍平成 SRT） |
| 别的（用户匹配器自己的格式） | 原样 `json.load`，**不校验** | 只有声明了这个 schema 的预设收（见 `ACCEPTS`） |

不给 `--preset` 时是 **`default`**（生产默认，owner 2026-09-24）：只留主轨的叠加字幕。叠加预设写两份——
可编辑的字幕稿（`*.script.ass`，阶段 3）和最终 ASS（阶段 4 `flowocr.typeset`）；三个的差别见
`flowocr.output.presets._overlay` 和 script-fx 计划。

预设可以声明 `ACCEPTS = ("flowocr-tracks/1", …)`：喂进来的产物不在里头就**当场报错**，
而不是让它在预设内部以 `KeyError` 的形式炸（"失败不冒充成功"，计划）。不声明就是什么都收。
`--opt k=v` 原样交给预设的 `options`（值是字符串，预设自己解释）；预设要什么参数、默认多少，是它自己的事。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from flowocr import extensions
from flowocr.artifacts import matchedio, tracksio


def load_doc(path: Path) -> dict:
    """按 `schema` 挑读法。认得的两种各自校验版本和必需字段，不认得的原样返回（用户匹配器的格式）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = raw.get("schema") if isinstance(raw, dict) else None
    if schema == tracksio.SCHEMA:
        tracksio.validate(raw)                  # 不再 `tracksio.load` 一遍：整片的 tracks.json 几十 MB，解析两次白花
    elif schema == matchedio.SCHEMA:
        matchedio.validate(raw, where=str(path))
    return raw


def check_accepts(preset, doc: dict, spec: str) -> None:
    """预设声明了 `ACCEPTS` 就核一下，喂错产物当场说清楚。"""
    accepts = getattr(preset, "ACCEPTS", None)
    if accepts is None:
        return
    got = doc.get("schema") if isinstance(doc, dict) else None
    if got not in tuple(accepts):
        raise SystemExit(f"预设 `{spec}` 吃的是 {list(accepts)}，给的是 {got!r}——"
                         f"阶段 2 的哪份产物喂给哪个预设，见 flowocr.output.render 文件头")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跑一个输出预设")
    ap.add_argument("doc", metavar="产物", help="阶段 2 的产物：*-tracks.json 或 *-matched.json")
    ap.add_argument("--preset", default="default",
                    help="内置名（%s）/ 模块名 / .py 路径" % "、".join(extensions.builtin_names("preset")))
    ap.add_argument("--outdir", default="", help="默认写在产物旁边")
    ap.add_argument("--opt", action="append", default=[], metavar="K=V", help="给预设的选项，可多次")
    a = ap.parse_args(argv)
    opts = {}
    for kv in a.opt:
        if "=" not in kv:
            ap.error(f"--opt 要 K=V：{kv}")
        k, v = kv.split("=", 1)
        opts[k] = v
    preset = extensions.load(a.preset, "preset")
    src = Path(a.doc)
    doc = load_doc(src)
    check_accepts(preset, doc, a.preset)
    outdir = Path(a.outdir) if a.outdir else src.parent
    written = preset.render(doc, outdir, opts)
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
