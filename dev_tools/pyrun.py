"""Run the dev venv's Python on this checkout's code, from the main checkout or a worktree slot.

    python dev_tools/pyrun.py -m flowocr.analyze.build_tracks <obs.jsonl> --outdir <dir> --tag <tag>
    python dev_tools/pyrun.py tests/test_guards.py
    python dev_tools/pyrun.py --which        # print the interpreter and code root it would use

Any Python works as the outer interpreter; it only picks the real one and passes the arguments on.
- Code: `<this checkout>/src` goes first on `PYTHONPATH`, so a slot runs the slot's code even when
  its `.venv` is a junction back to the main checkout (editable installs import the main code).
- Interpreter: this checkout's `.venv` if present, else the main checkout's (slots usually have none).
Data and outputs still resolve through `flowocr.paths.data_root()`; nothing here touches them.
"""
import os
import subprocess
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent


def main_checkout(code: Path) -> Path:
    git = code / ".git"
    if git.is_file():  # linked worktree: "gitdir: <main>/.git/worktrees/<name>"
        gitdir = Path(git.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip())
        if not gitdir.is_absolute():  # worktree.useRelativePaths
            gitdir = (code / gitdir).resolve()
        return gitdir.parents[2]
    return code


def interpreter(code: Path) -> Path:
    for root in (code, main_checkout(code)):
        py = root / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            py = root / ".venv" / "bin" / "python"
        if py.exists():
            return py
    sys.exit(f"pyrun: no .venv in {code} or its main checkout; run `uv sync --frozen --extra nvidia --group dev` (or --extra cpu) first")


def main() -> int:
    py, src = interpreter(CODE), CODE / "src"
    if sys.argv[1:] == ["--which"]:
        print(f"interpreter {py}\ncode        {src}")
        return 0
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(src), env.get("PYTHONPATH")]))
    return subprocess.run([str(py), *sys.argv[1:]], env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
