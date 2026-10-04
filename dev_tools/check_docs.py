#!/usr/bin/env python3
# project-conventions tools, updated 2026-09-25. Copies in a project's dev_tools/ compare against this date.
"""Check a repository's Markdown: links and anchors resolve, plans open with a status block.

A relative link that climbs out of the repository is always reported: it is
broken in every other clone.

With --public, also check that public documents point only at public ones: no
link into a private path, no mention of a file inside one or of a private file
entry (e.g. `docs/plans/x-plan.md` in backticks), no link to a local absolute
path. Private paths and the publish allowlist come from `.publish.toml` when
present (see publish.py), otherwise from publish.py's defaults; --private
overrides them.

It proves a link lands somewhere, not that the target says what the link
claims; that stays a reviewer's job.

Usage:
    check_docs.py [-C REPO] [--public] [--private PREFIX ...] [--exclude PREFIX ...] [--plans GLOB]
Exit status 1 when anything is reported.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import publish  # shipped next to this script; both checkers share what counts as a link
except ImportError:
    sys.exit("check_docs.py needs publish.py in the same directory (copy both, even without publishing).")
from publish import FENCE, INLINE_CODE, LINK  # noqa: E402
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
EXPLICIT_ANCHOR = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"")
LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")  # `file.py:228`, a line the editor jumps to
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

DEFAULT_EXCLUDE = ["docs/archive/"]
DEFAULT_PLANS = "docs/plans/*-plan.md"


def slug(heading: str) -> str:
    """GitHub's heading anchor: link text kept, tags dropped, lowercase,
    everything but word characters, `-` and spaces removed, spaces to `-`."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def prose_lines(text: str):
    """(line number, line) outside fenced code, with inline code removed."""
    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield n, INLINE_CODE.sub("", line)


def anchors_of(text: str) -> set[str]:
    found, seen = set(), {}
    for _, line in prose_lines(text):
        found.update(EXPLICIT_ANCHOR.findall(line))
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if m := HEADING.match(line):
            base = slug(m.group(1))
            count = seen.get(base, 0)
            found.add(base if count == 0 else f"{base}-{count}")
            seen[base] = count + 1
    return found


def markdown_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=root, capture_output=True)
    if proc.returncode == 0:
        return sorted(n for n in proc.stdout.decode("utf-8").split("\0") if n and (root / n).exists())
    skip = {".git", ".venv", "node_modules", ".worktrees"}
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md")
                  if not skip & set(p.relative_to(root).parts))


def private_rule(root: Path, override: list[str] | None):
    """(private prefixes, is-stripped test). The publish config's allowlist makes
    individual items under a private prefix public again."""
    if override is not None:
        return override, lambda path: under(path, override)
    cfg = publish.Config.load(root)
    return cfg.private, cfg.is_stripped


def under(path: str, prefixes: list[str]) -> bool:
    return any(path == p.strip("/") or path.startswith(p.strip("/") + "/") for p in prefixes)


def resolve(doc: str, target: str) -> str | None:
    """Repository-relative path, or None when it climbs out of the repository."""
    parts = [] if target.startswith("/") else list(PurePosixPath(doc).parent.parts)
    for part in PurePosixPath(target.lstrip("/")).parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def check(root: Path, public: bool, exclude: list[str], plans_glob: str,
          private: list[str] | None = None) -> list[str]:
    private, stripped = private_rule(root, private) if public else ([], lambda path: False)
    docs = [d for d in markdown_files(root) if not under(d, exclude)]
    texts = {d: (root / d).read_text(encoding="utf-8", errors="replace") for d in docs}
    anchor_cache: dict[str, set[str]] = {}
    problems = []

    mention = publish.mention_pattern(private) if public else None
    for doc, text in texts.items():
        doc_is_public = not stripped(doc)
        if public and doc_is_public and mention:
            for n, line in enumerate(text.splitlines(), 1):
                for m in mention.finditer(line):
                    if not publish.names_stripped_file(m.group(0), private, stripped):
                        continue
                    problems.append(f"{doc}:{n}: public document mentions a private file: {m.group(0)}")
        for n, line in prose_lines(text):
            for m in LINK.finditer(line):
                raw = m.group(1)
                if SCHEME.match(raw) and not re.match(r"^[A-Za-z]:[/\\]", raw):
                    continue  # URL or mailto:
                path_part, _, fragment = raw.partition("#")
                path_part = LINE_SUFFIX.sub("", unquote(path_part.split("?", 1)[0]))
                target = resolve(doc, path_part) if path_part else doc
                where = f"{doc}:{n}"
                if target is None:
                    # A relative link that climbs out of the repository is broken in
                    # every other clone, published or not.
                    problems.append(f"{where}: link leaves the repository: {raw}")
                    continue
                if re.match(r"^[A-Za-z]:[/\\]", path_part):
                    if public and doc_is_public:
                        problems.append(f"{where}: link to a local absolute path: {raw}")
                    continue
                if public and doc_is_public and stripped(target):
                    problems.append(f"{where}: public document links to a private path: {raw}")
                    continue
                full = root / target
                if not full.exists():
                    if stripped(target) or under(target, private) or under(target, exclude):
                        continue  # absent where the tree is stripped; judged in --public
                    problems.append(f"{where}: broken link: {raw}")
                    continue
                if fragment and full.suffix == ".md":
                    if target not in anchor_cache:
                        anchor_cache[target] = anchors_of(
                            texts.get(target) or full.read_text(encoding="utf-8", errors="replace"))
                    if unquote(fragment).lower() not in anchor_cache[target]:
                        problems.append(f"{where}: no heading for anchor: {raw}")

    for doc, text in texts.items():
        if not fnmatch.fnmatch(doc, plans_glob):
            continue
        lines = text.splitlines()
        body = list(lines[1:])
        while body and not body[0].strip():
            body.pop(0)
        block = []
        for line in body:
            if not line.startswith(">"):
                break
            block.append(line)
        if not lines or not lines[0].startswith("# ") or not any("**状态**" in l for l in block):
            problems.append(f"{doc}:1: plan does not open with a status block (> **状态**：…)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-C", dest="repo", default=".")
    parser.add_argument("--public", action="store_true",
                        help="also enforce: public documents link only to public ones")
    parser.add_argument("--exclude", nargs="*", default=DEFAULT_EXCLUDE,
                        help=f"path prefixes not scanned (default: {DEFAULT_EXCLUDE})")
    parser.add_argument("--plans", default=DEFAULT_PLANS,
                        help=f"glob of plans that need a status block (default: {DEFAULT_PLANS})")
    parser.add_argument("--private", nargs="*",
                        help="private path prefixes (default: from .publish.toml or publish.py)")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    problems = check(root, args.public, args.exclude, args.plans, args.private)
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
