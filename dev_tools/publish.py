#!/usr/bin/env python3
# project-conventions tools, updated 2026-09-25. Copies in a project's dev_tools/ compare against this date.
"""Publish the development branch's tree to the public branch, minus private paths.

Model (see git.md, "发布脚本"):

- `dev` holds everything, private paths included. `main` is written only by this
  script: each publish is one commit on top of `main` whose tree is `dev`'s tree
  minus the private paths, with a `Published-From: <dev sha>` trailer.
- Right after, `dev` gets a merge commit whose tree is `dev`'s own tree and whose
  second parent is the new `main` commit. `dev`'s files do not change, but `main`
  becomes an ancestor of `dev`, so an external PR merged on `main` comes back to
  `dev` with a plain `git merge main`.
- Publishing refuses when `main` has commits `dev` lacks (a PR not merged back
  would be silently reverted), when a non-snapshot commit on `main` touches a
  private path, and when the public tree references private paths or links to
  files that are not in it.

Configuration lives in `.publish.toml` at the repository root (always stripped);
the public remote is per clone: `git config publish.remote <name>`.

Usage:
    publish.py check [--warn-only]
    publish.py publish [-m MESSAGE] [--push] [--warn-only]
    publish.py reconnect
    publish.py install-hook --remote NAME
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

TRAILER = "Published-From"
CONFIG_NAME = ".publish.toml"
ZERO = "0" * 40

DEFAULT_PRIVATE = [
    "docs/plans/",
    "docs/archive/",
    "docs/reports/",
    "explore/",
    "CLAUDE.local.md",
    ".claude/",
    ".agents/",
    ".workbuddy/",
    ".qoder/",
    ".codex/",
]
# Private locations that must never appear in a public tree. Home-directory
# shortcuts and absolute user paths identify one machine and one person.
# Boundaries are ASCII-only on purpose: `\b` and `\w` count CJK characters as word
# characters, so `见C:\Users\…` or `~/.agents目录` would slip through.
DEFAULT_FORBIDDEN = [
    r"~[/\\]\.agents(?![A-Za-z0-9_])",
    r"~[/\\]\.claude(?![A-Za-z0-9_])",
    r"(?<![A-Za-z0-9_])[A-Za-z]:[/\\]Users[/\\][^/\\\s]+",
    r"/home/[^/\s]+/",
    r"/Users/[^/\s]+/",
]
TEXT_SUFFIXES = {
    ".md", ".txt", ".rst", ".py", ".toml", ".json", ".yml", ".yaml",
    ".ini", ".cfg", ".ps1", ".sh", ".cmd", ".bat",
}
LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")


class PublishError(Exception):
    pass


@dataclass
class Config:
    source: str = "dev"
    target: str = "main"
    private: list[str] = field(default_factory=lambda: list(DEFAULT_PRIVATE))
    public: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=lambda: list(DEFAULT_FORBIDDEN))
    # Paths exempt from the private-reference scan (not from the link check),
    # e.g. a vendored copy of this script, whose patterns match themselves.
    unchecked: list[str] = field(default_factory=list)
    ci_gate: str = ""
    ci_workflows: list[str] = field(default_factory=lambda: ["CI"])
    ci_timeout_minutes: int = 45
    ci_check: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> "Config":
        path = root / CONFIG_NAME
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise PublishError(f"{CONFIG_NAME}: unknown keys {sorted(unknown)}")
        return cls(**data)

    def _match(self, path: str, prefixes: list[str]) -> bool:
        for p in prefixes:
            p = p.strip("/")
            if path == p or path.startswith(p + "/"):
                return True
        return False

    def is_stripped(self, path: str) -> bool:
        if path == CONFIG_NAME:
            return True
        return self._match(path, self.private) and not self._match(path, self.public)


class Git:
    def __init__(self, cwd: Path):
        self.cwd = cwd

    def run(self, *args: str, input: bytes | None = None, env: dict | None = None,
            check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.cwd, input=input, capture_output=True,
            env={**os.environ, **(env or {})},
        )
        if check and proc.returncode != 0:
            raise PublishError(
                f"git {' '.join(args)} failed:\n{proc.stderr.decode(errors='replace').strip()}")
        return proc.stdout.decode("utf-8", errors="replace")

    def ok(self, *args: str) -> bool:
        return subprocess.run(["git", *args], cwd=self.cwd, capture_output=True).returncode == 0

    def rev(self, ref: str) -> str | None:
        out = self.run("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False).strip()
        return out or None

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", a, b)

    def tree_of(self, commit: str) -> str:
        return self.run("rev-parse", f"{commit}^{{tree}}").strip()

    def message(self, commit: str) -> str:
        return self.run("log", "-1", "--format=%B", commit)

    def blob(self, sha: str) -> bytes:
        return subprocess.run(["git", "cat-file", "blob", sha], cwd=self.cwd,
                              capture_output=True, check=True).stdout

    def ls_tree(self, treeish: str) -> list[tuple[str, str, str, str]]:
        out = self.run("ls-tree", "-r", "-z", "--full-tree", treeish)
        entries = []
        for rec in filter(None, out.split("\0")):
            meta, path = rec.split("\t", 1)
            mode, kind, sha = meta.split()
            entries.append((mode, kind, sha, path))
        return entries


def published_from(git: Git, commit: str) -> str | None:
    for line in git.message(commit).splitlines():
        if line.startswith(f"{TRAILER}: "):
            return line.split(": ", 1)[1].strip()
    return None


def build_tree(git: Git, cfg: Config, source: str) -> str:
    """Write the public tree: `source`'s tree minus stripped paths."""
    keep = [e for e in git.ls_tree(source) if not cfg.is_stripped(e[3])]
    index = Path(git.run("rev-parse", "--absolute-git-dir").strip()) / "publish-index"
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        git.run("read-tree", "--empty", env=env)
        payload = "".join(f"{m} {k} {s}\t{p}\0" for m, k, s, p in keep).encode()
        git.run("update-index", "-z", "--index-info", input=payload, env=env)
        return git.run("write-tree", env=env).strip()
    finally:
        index.unlink(missing_ok=True)


# The one private file the conventions tell the public AGENTS.md to name
# (agent-files.md, "私有补充"): naming it is by design, not a dangling pointer.
NAMED_BY_CONVENTION = {"CLAUDE.local.md"}

# Characters that end a path mention. Fullwidth punctuation included: otherwise
# `docs/x.md）。…` swallows the Chinese that follows into the path.
PATH_END = r"\s`'\")\]>|,;，。；：、（）「」『』【】《》！？"


def mention_pattern(private: list[str]) -> re.Pattern | None:
    """A path token starting with a private entry, e.g. `docs/plans/x-plan.md` or
    `docs/product.md`; `names_stripped_file` decides whether it is a problem.
    Only repository-root-relative spellings are recognised."""
    prefixes = [re.escape(p.strip("/")) for p in private if p.strip("/")]
    if not prefixes:
        return None
    # ASCII-only lookbehind: `\w` would count CJK, so `见docs/plans/x.md` would not match
    return re.compile(r"(?<![A-Za-z0-9_./-])(?:\./)?(?:" + "|".join(prefixes) + r")[^" + PATH_END + r"]*")


def names_stripped_file(token: str, private: list[str], stripped) -> bool:
    """Whether a `mention_pattern` match sends a public reader to a file they cannot open.

    Naming a private directory itself ("docs/plans/ is not published") is fine;
    naming a file inside one, or a private file entry, is not. `stripped` is the
    is-stripped test, so allowlisted files stay fine to name."""
    # 带锚点或行号的写法（`docs/product.md#x`、`docs/product.md:12`）按文件本身判
    path = re.sub(r"(#.*|(:\d+)+)$", "", token.removeprefix("./")).rstrip("./")
    if path in NAMED_BY_CONVENTION or any(path == p.strip("/") for p in private if p.endswith("/")):
        return False
    return stripped(path)


def _link_target(text: str) -> str | None:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text) or text.startswith("#"):
        return None  # URL, mailto:, or same-file anchor
    return unquote(text.split("#", 1)[0].split("?", 1)[0]) or None


def _resolve(doc: str, target: str) -> str | None:
    base = [] if target.startswith("/") else list(PurePosixPath(doc).parent.parts)
    for part in PurePosixPath(target.lstrip("/")).parts:
        if part == "..":
            if not base:
                return None
            base.pop()
        elif part != ".":
            base.append(part)
    return "/".join(base)


def check_tree(git: Git, cfg: Config, tree: str, source: str) -> list[str]:
    """Problems in the public tree: private references and links that go nowhere."""
    files = {e[3]: e for e in git.ls_tree(tree) if e[1] == "blob"}
    dirs = {"/".join(PurePosixPath(p).parts[:i]) for p in files
            for i in range(1, len(PurePosixPath(p).parts))}
    source_paths = {e[3] for e in git.ls_tree(source)}
    forbidden = [re.compile(p) for p in cfg.forbidden]
    mention = mention_pattern(cfg.private)
    problems = []
    for path, (_, _, sha, _) in sorted(files.items()):
        if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = git.blob(sha).decode("utf-8", errors="replace")
        scan = not cfg._match(path, cfg.unchecked)
        fenced = False
        for n, line in enumerate(text.splitlines(), 1):
            for rx in forbidden if scan else ():
                if rx.search(line):
                    problems.append(f"{path}:{n}: private reference matching {rx.pattern!r}")
            for m in mention.finditer(line) if scan and mention else ():
                if names_stripped_file(m.group(0), cfg.private, cfg.is_stripped):
                    problems.append(f"{path}:{n}: mentions a stripped file: {m.group(0)}")
            if not path.endswith(".md"):
                continue
            if FENCE.match(line):
                fenced = not fenced
                continue
            if fenced:
                continue
            for m in LINK.finditer(INLINE_CODE.sub("", line)):
                target = _link_target(m.group(1))
                if target is None:
                    continue
                resolved = _resolve(path, target)
                if resolved is None:
                    problems.append(f"{path}:{n}: link leaves the repository: {m.group(1)}")
                elif resolved in files or resolved in dirs or resolved == "":
                    continue
                elif resolved in source_paths or any(s.startswith(resolved + "/") for s in source_paths):
                    problems.append(f"{path}:{n}: link to a stripped path: {m.group(1)}")
                else:
                    problems.append(f"{path}:{n}: broken link: {m.group(1)}")
    return problems


def private_touching_commits(git: Git, cfg: Config, target: str | None) -> list[str]:
    """Non-snapshot commits on the public branch that change a private path."""
    if not target:
        return []
    bad = []
    for commit in git.run("rev-list", target).split():
        if published_from(git, commit):
            continue
        changed = git.run("diff-tree", "-r", "--root", "--name-only", "-z",
                          "--no-commit-id", "--diff-merges=first-parent", commit)
        hits = [p for p in filter(None, changed.split("\0")) if cfg.is_stripped(p)]
        if hits:
            bad.append(f"{commit[:12]} touches {', '.join(hits[:5])}")
    return bad


def unmerged_foreign_commits(git: Git, cfg: Config, target: str, source: str) -> list[str]:
    """Commits on the target that the source lacks and that are not interrupted publishes.

    A commit the source lacks is safe to reconnect only if this script made it
    (it carries the trailer) from a commit the source already has. Anything else,
    typically an external PR, has to reach the source by a real merge; reconnecting
    over it would record it as merged while the source's files still lack it, and
    the next publish would revert it.
    """
    foreign = []
    for commit in git.run("rev-list", target, "--not", source).split():
        origin = published_from(git, commit)
        if not origin or not git.rev(origin) or not git.is_ancestor(origin, source):
            foreign.append(commit)
    return foreign


def repair_interrupted(git: Git, cfg: Config, target: str, source: str) -> str:
    """Reconnect after a publish that moved the target but stopped before reconnecting."""
    foreign = unmerged_foreign_commits(git, cfg, target, source)
    if foreign:
        raise PublishError(
            f"{cfg.target} has {len(foreign)} commit(s) {cfg.source} lacks that are not "
            f"interrupted publishes (an external PR?). Merge them first: "
            f"git switch {cfg.source} && git merge {cfg.target}")
    print(f"repairing an interrupted publish of {target[:12]}")
    return reconnect(git, cfg, target)


def reconnect(git: Git, cfg: Config, published: str, attempts: int = 5) -> str:
    """Make `published` an ancestor of the source branch without changing its files.

    `update-ref` with the old value makes this safe against someone committing to
    the source branch meanwhile: the attempt fails and is rebuilt on the new tip,
    so nothing on either side is lost.
    """
    ref = f"refs/heads/{cfg.source}"
    for _ in range(attempts):
        tip = git.rev(cfg.source)
        if tip is None:
            raise PublishError(f"branch {cfg.source} does not exist")
        if git.is_ancestor(published, tip):
            return tip
        merge = git.run(
            "commit-tree", git.tree_of(tip), "-p", tip, "-p", published,
            "-m", f"Record publish of {published[:12]} into {cfg.target} (files unchanged)",
        ).strip()
        if git.ok("update-ref", ref, merge, tip):
            return merge
    raise PublishError(f"{cfg.source} kept moving; run `publish.py reconnect` again")


def _fail_point(name: str) -> None:
    # Test hook: simulate an interruption at a named step.
    if os.environ.get("PUBLISH_TEST_FAIL_AT") == name:
        raise SystemExit(99)


def _sync_with_remote(git: Git, cfg: Config, remote: str) -> None:
    git.run("fetch", "--quiet", remote, f"+refs/heads/{cfg.target}:refs/remotes/{remote}/{cfg.target}",
            check=False)
    remote_tip = git.rev(f"refs/remotes/{remote}/{cfg.target}")
    local_tip = git.rev(cfg.target)
    if remote_tip is None or remote_tip == local_tip:
        return
    if local_tip is None or git.is_ancestor(local_tip, remote_tip):
        git.run("update-ref", f"refs/heads/{cfg.target}", remote_tip, local_tip or ZERO)
        print(f"{cfg.target} fast-forwarded to {remote}/{cfg.target} ({remote_tip[:12]})")
        return
    if not git.is_ancestor(remote_tip, local_tip):
        raise PublishError(f"local {cfg.target} and {remote}/{cfg.target} have diverged")


def _wait_for_ci(git: Git, cfg: Config, sha: str) -> None:
    if cfg.ci_check:
        cmd = [part.replace("{sha}", sha) for part in cfg.ci_check]
        if subprocess.run(cmd, cwd=git.cwd).returncode != 0:
            raise PublishError(f"CI check failed for {sha[:12]}; {cfg.target} not moved")
        return
    deadline = time.monotonic() + cfg.ci_timeout_minutes * 60
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["gh", "run", "list", "--commit", sha, "--json", "name,status,conclusion"],
            cwd=git.cwd, capture_output=True, text=True)
        runs = json.loads(out.stdout or "[]") if out.returncode == 0 else []
        by_name = {r["name"]: r for r in runs}
        if all(by_name.get(w, {}).get("status") == "completed" for w in cfg.ci_workflows) and runs:
            failed = [r["name"] for r in runs if r["conclusion"] != "success"]
            if failed:
                raise PublishError(f"CI failed ({', '.join(failed)}); {cfg.target} not moved")
            return
        time.sleep(20)
    raise PublishError("timed out waiting for CI; the gate branch is left in place")


def publish(git: Git, cfg: Config, message: str, remote: str | None = None,
            push: bool = False, warn_only: bool = False, dry_run: bool = False) -> str | None:
    if push and not remote:
        raise PublishError("--push needs a public remote: git config publish.remote <name>")
    if push:
        _sync_with_remote(git, cfg, remote)
    source = git.rev(cfg.source)
    if source is None:
        raise PublishError(f"branch {cfg.source} does not exist")
    target = git.rev(cfg.target)

    if target and not git.is_ancestor(target, source):
        if dry_run:
            if unmerged_foreign_commits(git, cfg, target, source):
                raise PublishError(f"{cfg.target} has commits {cfg.source} lacks (an external PR?); "
                                   f"merge {cfg.target} into {cfg.source} first")
            print(f"an interrupted publish of {target[:12]} will be repaired first")
        else:
            source = repair_interrupted(git, cfg, target, source)

    touching = private_touching_commits(git, cfg, target)
    if touching:
        raise PublishError(f"{cfg.target} has non-published commits touching private paths:\n  "
                           + "\n  ".join(touching))

    tree = build_tree(git, cfg, source)
    problems = check_tree(git, cfg, tree, source)
    if problems:
        print(f"{len(problems)} problem(s) in the public tree:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        if not warn_only:
            raise PublishError("refusing to publish; fix the problems or pass --warn-only")

    created = target is None or git.tree_of(target) != tree
    if dry_run:
        print(f"would publish {cfg.source}@{source[:12]} onto {cfg.target}" if created
              else "public tree unchanged; nothing new to commit")
        return None
    if created:
        parents = ["-p", target] if target else []
        commit = git.run("commit-tree", tree, *parents, "-m", message,
                         "-m", f"{TRAILER}: {source}").strip()
    else:
        commit = target
        print("public tree unchanged; nothing new to commit")

    if push:
        _push(git, cfg, remote, commit)
    if created:
        if not git.ok("update-ref", f"refs/heads/{cfg.target}", commit, target or ZERO):
            raise PublishError(f"{cfg.target} moved during publish; rerun")
        _fail_point("after-target")
        print(f"published {cfg.source}@{source[:12]} as {cfg.target}@{commit[:12]}")
    reconnect(git, cfg, commit)
    return commit


def _push(git: Git, cfg: Config, remote: str, commit: str) -> None:
    """Bring the public remote's target to `commit`, through the CI gate if configured.

    Decided on its own, not by whether this run made a commit: a publish made
    locally earlier still has to reach the remote, and still through CI.
    """
    remote_tip = git.rev(f"refs/remotes/{remote}/{cfg.target}")
    if remote_tip == commit:
        print(f"{remote}/{cfg.target} already has {commit[:12]}")
        return
    pending = git.run("rev-list", commit, *(["--not", remote_tip] if remote_tip else [])).split()
    stray = [c[:12] for c in pending if not published_from(git, c)]
    if stray:
        raise PublishError(f"refusing to push commits not made by publish.py: {', '.join(stray)}")
    if cfg.ci_gate:
        git.run("push", "--quiet", remote, f"+{commit}:refs/heads/{cfg.ci_gate}")
        _wait_for_ci(git, cfg, commit)
    git.run("push", "--quiet", remote, f"{commit}:refs/heads/{cfg.target}")
    if cfg.ci_gate:
        git.run("push", "--quiet", remote, f":refs/heads/{cfg.ci_gate}", check=False)
    git.run("update-ref", f"refs/remotes/{remote}/{cfg.target}", commit)
    print(f"pushed {commit[:12]} to {remote}/{cfg.target}")


HOOK = """#!/bin/sh
# Installed by publish.py. On a public remote, only the published branch, the CI
# gate branch and tags on published commits may be pushed. Uses nothing but git
# and shell builtins, and refuses the push whenever a check cannot run.
remote_name="$1"
remote_url="$2"
remotes=$(git config --get-all publish.remote)
rc=$?
if [ $rc -gt 1 ]; then
  echo "pre-push: cannot read publish.remote (git config exit $rc); refusing" >&2; exit 1
fi
public=0
for r in $remotes; do
  if [ "$r" = "$remote_name" ]; then public=1; fi
  url=$(git remote get-url "$r" 2>/dev/null) || url=
  if [ -n "$url" ] && [ "$url" = "$remote_url" ]; then public=1; fi
done
if [ $public -eq 0 ]; then exit 0; fi
target=$(git config --get publish.target) || target=main
gate=$(git config --get publish.gate) || gate=
zero=0000000000000000000000000000000000000000
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  if [ "$local_sha" = "$zero" ]; then continue; fi
  case "$remote_ref" in
    "refs/heads/$target"|"refs/heads/${gate:-$target}")
      origin=$(git log -1 --format='%(trailers:key=Published-From,valueonly)' "$local_sha") || origin=
      if [ -z "$origin" ]; then
        echo "pre-push: $remote_ref must be a commit made by publish.py" >&2; status=1
      fi ;;
    refs/tags/*)
      commit=$(git rev-parse -q --verify "$local_sha^{commit}") || commit=
      if [ -z "$commit" ] || ! git merge-base --is-ancestor "$commit" "refs/heads/$target"; then
        echo "pre-push: $remote_ref must point at a commit on $target" >&2; status=1
      fi ;;
    *)
      echo "pre-push: refusing to push $remote_ref to public remote $remote_name" >&2; status=1 ;;
  esac
done
exit $status
"""


def hooks_dir(git: Git) -> Path:
    """The hooks directory git actually runs, honouring core.hooksPath."""
    path = Path(git.run("rev-parse", "--git-path", "hooks").strip())
    return path if path.is_absolute() else git.cwd / path


def install_hook(git: Git, cfg: Config, remote: str) -> Path:
    path = hooks_dir(git) / "pre-push"
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") != HOOK:
        raise PublishError(
            f"{path} already exists and is not this script's hook; not overwriting it. "
            "Both hooks read the pushed refs from stdin, so combine them by hand "
            "(save stdin to a file, feed it to each) or remove the existing one first.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    git.run("config", "publish.remote", remote)
    git.run("config", "publish.target", cfg.target)
    if cfg.ci_gate:
        git.run("config", "publish.gate", cfg.ci_gate)
    else:
        git.run("config", "--unset", "publish.gate", check=False)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-C", dest="repo", default=".", help="repository (default: cwd)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", help="build the public tree and report problems")
    check.add_argument("--warn-only", action="store_true")
    pub = sub.add_parser("publish", help="publish the source branch onto the target")
    pub.add_argument("-m", "--message", default="Publish snapshot")
    pub.add_argument("--push", action="store_true", help="push to the public remote")
    pub.add_argument("--warn-only", action="store_true", help="report problems but publish")
    sub.add_parser("reconnect", help="repair an interrupted publish (refuses anything else)")
    hook = sub.add_parser("install-hook", help="install the pre-push guard")
    hook.add_argument("--remote", required=True, help="name of the public remote")
    args = parser.parse_args(argv)

    try:
        root = Path(Git(Path(args.repo)).run("rev-parse", "--show-toplevel").strip())
        git = Git(root)
        cfg = Config.load(root)
        remote = git.run("config", "--get", "publish.remote", check=False).strip() or None
        if args.cmd == "check":
            publish(git, cfg, "", warn_only=args.warn_only, dry_run=True)
        elif args.cmd == "publish":
            publish(git, cfg, args.message, remote=remote, push=args.push, warn_only=args.warn_only)
        elif args.cmd == "reconnect":
            target, source = git.rev(cfg.target), git.rev(cfg.source)
            if target is None or source is None:
                raise PublishError(f"{cfg.target} and {cfg.source} must both exist")
            if git.is_ancestor(target, source):
                print(f"nothing to repair: {cfg.target} is already an ancestor of {cfg.source}")
            else:
                print(f"{cfg.source} is at {repair_interrupted(git, cfg, target, source)[:12]}")
        elif args.cmd == "install-hook":
            print(f"installed {install_hook(git, cfg, args.remote)}")
    except PublishError as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
