"""装成包之后从外部文件加载扩展（CI 用，本地也能跑）：拿 `ci_smoke.py` 产的 tracks.json，
用**装好的** `flowocr-match` / `flowocr-render` 加载仓库里 `examples/` 的匹配器和输出预设，核对产物。

    python dev_tools/ci_extensions.py --tracks tmp/ci-smoke/out/smoke-tracks.json [--workdir tmp/ci-ext]

要在**非 editable 安装**的环境里跑（CI 里是 wheel 装进一个全新 venv）：`examples/` 在包外，
editable 安装时源码树和 `examples/` 挨着，证明不了"用户 pip 装完、拿自己的文件当扩展"这条路。
所以先核 `flowocr` 是从 site-packages 导入的，不是这份源码树。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode:
        raise SystemExit(f"失败（退出码 {r.returncode}）：{' '.join(cmd)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tracks", required=True, help="ci_smoke.py 产的 smoke-tracks.json")
    ap.add_argument("--workdir", default="tmp/ci-ext")
    ap.add_argument("--allow-source", action="store_true", help="本地调试：允许从源码树导入 flowocr（CI 里不给）")
    a = ap.parse_args()
    import flowocr
    where = Path(flowocr.__file__).resolve()
    if (REPO / "src") in where.parents and not a.allow_source:   # 装进仓库下 tmp\ 的 venv 也在 REPO 里，只认 src/ 才算源码树
        raise SystemExit(f"flowocr 是从源码树导入的（{where}）：这一步要在装成包的环境里跑")
    print(f"flowocr 来自 {where}")
    wd = Path(a.workdir)
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    table = wd / "table.json"
    table.write_text(json.dumps({"HELLO FLOWOCR": "你好，FLOWOCR"}, ensure_ascii=False), encoding="utf-8")
    bin_dir = Path(sys.executable).parent
    matched = wd / "matched.json"
    run([str(bin_dir / "flowocr-match"), a.tracks, "--matcher", str(REPO / "examples" / "matcher_minimal.py"),
         "--out", str(matched), "--opt", f"table={table}"])
    m = json.loads(matched.read_text(encoding="utf-8"))
    if m.get("schema") != "example-matched/1" or not m["stats"]["hit"]:
        raise SystemExit(f"示例匹配器的产物不对：schema {m.get('schema')!r}、stats {m.get('stats')}")
    run([str(bin_dir / "flowocr-render"), a.tracks, "--preset", str(REPO / "examples" / "preset_minimal.py"),
         "--outdir", str(wd), "--opt", "prefix=ex-"])
    srts = sorted(wd.glob("ex-*.srt"))
    if not srts or not any(p.stat().st_size for p in srts):
        raise SystemExit(f"示例预设没写出非空的 SRT：{[p.name for p in srts]}")
    print(f"外部扩展通过：匹配命中 {m['stats']['hit']}/{m['stats']['cues']}，预设写出 {len(srts)} 份 SRT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
