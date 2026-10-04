"""公开树检查：把某个提交按 `.publish.toml` 剥离成公开 `main` 会有的那棵树，在里面跑守卫与文档检查。

    python dev_tools/pyrun.py dev_tools/public_tree.py [<提交>]      # 默认 HEAD；树导出到 tmp/public-tree/（每次重建）

`publish.py check` 只查公开树里的私有引用与死链；守卫依赖 `dev_tools/`、`explore/` 的那几处，
要在真剥离过的树里跑一遍才知道少了私有文件还过不过（发布前验证的一步，见 release skill）。
剥离用 `publish.py` 自己的判定（`Config.is_stripped`），导出的树 `git init` 成一份 checkout，
守卫里按 git 判断的地方才和真 checkout 一样。用当前解释器（经 pyrun 就是开发 venv），`PYTHONPATH` 指向导出树的 `src`。
退出码：0 两项都过；1 有一项没过；2 导出或剥离出错。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE / "dev_tools"))
from publish import CONFIG_NAME, Config  # noqa: E402


def _rm_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def export(commit: str, dst: Path) -> int:
    """导出 `commit` 的树到 `dst`、按它自己的 `.publish.toml` 剥离，返回留下的文件数。"""
    if dst.exists():
        shutil.rmtree(dst, onerror=_rm_readonly)
    dst.mkdir(parents=True)
    tar = subprocess.run(["git", "archive", commit], cwd=CODE, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        tf.extractall(dst, filter="data")
    cfg = Config.load(dst)
    files = [p for p in dst.rglob("*") if p.is_file()]
    kept = 0
    for p in files:
        rel = p.relative_to(dst).as_posix()
        if rel == CONFIG_NAME or cfg.is_stripped(rel):
            p.unlink()
        else:
            kept += 1
    for d in sorted((p for p in dst.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    git = ["git", "-c", "user.name=public-tree", "-c", "user.email=public-tree@localhost"]
    subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dst, check=True)
    subprocess.run([*git, "commit", "-q", "-m", f"public tree of {commit}"], cwd=dst, check=True)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("commit", nargs="?", default="HEAD")
    ap.add_argument("--out", default=str(CODE / "tmp" / "public-tree"), help="导出目录（每次清空重建）")
    a = ap.parse_args()
    commit = subprocess.run(["git", "rev-parse", "--verify", a.commit + "^{commit}"], cwd=CODE,
                            capture_output=True, text=True).stdout.strip()
    if not commit:
        print(f"public_tree: 认不出提交 {a.commit!r}", file=sys.stderr)
        return 2
    dst = Path(a.out)
    try:
        kept = export(commit, dst)
    except (subprocess.CalledProcessError, OSError, tarfile.TarError) as e:
        print(f"public_tree: 导出或剥离失败：{e}", file=sys.stderr)
        return 2
    print(f"公开树：{commit[:12]} 剥离后 {kept} 个文件 -> {dst}", flush=True)
    env = {**os.environ, "PYTHONPATH": str(dst / "src")}
    results = {}
    for name, script in (("守卫", "tests/test_guards.py"), ("文档检查", "dev_tools/check_docs.py")):
        r = subprocess.run([sys.executable, script], cwd=dst, env=env)
        results[name] = r.returncode
        print(f"{name}：{'过' if r.returncode == 0 else f'没过（退出 {r.returncode}）'}", flush=True)
    return 0 if all(c == 0 for c in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
