from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .gitcmd import run
from .safety import is_safe_relative_path, is_sensitive_path

_GIT_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_PLUS_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$", re.MULTILINE)
_MINUS_FILE = re.compile(r"^--- (?:a/)?(.+?)\s*$", re.MULTILINE)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    message: str


def normalize(diff: str) -> str:
    text = diff.replace("\r\n", "\n").strip("\n")
    return text + "\n" if text else ""


def touched_files(diff: str) -> list[str]:
    paths: list[str] = []
    for match in _GIT_HEADER.finditer(diff):
        for path in match.groups():
            if path not in paths and path != "/dev/null":
                paths.append(path)
    for pattern in (_PLUS_FILE, _MINUS_FILE):
        for match in pattern.finditer(diff):
            path = match.group(1).strip()
            if path == "/dev/null" or path.startswith("a/") or path.startswith("b/"):
                path = path.removeprefix("a/").removeprefix("b/")
            if path and path != "/dev/null" and path not in paths:
                paths.append(path)
    return paths


def validate(diff: str) -> list[str]:
    """Structural and path safety checks — run before the patch ever reaches git."""
    problems: list[str] = []
    if not diff.strip():
        return ["The patch is empty."]
    if not _HUNK.search(diff):
        problems.append("The patch has no @@ hunk header, so it is not a unified diff.")
    files = touched_files(diff)
    if not files:
        problems.append("The patch does not name any file.")
    for path in files:
        if not is_safe_relative_path(path):
            problems.append(f"Refusing path outside the repository or inside .git/.gitsquid: {path}")
        elif is_sensitive_path(path):
            problems.append(f"Refusing to patch a credential file: {path}")
    return problems


def current_commit(repo: Path) -> str | None:
    result = run(repo, ["rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def working_tree_dirty(repo: Path) -> bool:
    result = run(repo, ["status", "--porcelain"])
    return bool(result.stdout.strip())


def _apply(repo: Path, diff: str, *, check: bool, reverse: bool) -> ApplyResult:
    problems = validate(diff)
    if problems:
        return ApplyResult(False, "; ".join(problems))
    args = ["apply", "-p1", "--whitespace=nowarn"]
    if check:
        args.append("--check")
    if reverse:
        args.append("--reverse")
    args.append("-")
    result = run(repo, args, stdin=normalize(diff), timeout=120)
    if result.returncode == 0:
        return ApplyResult(True, "ok")
    return ApplyResult(False, (result.stderr or result.stdout).strip() or "git apply failed")


def check(repo: Path, diff: str) -> ApplyResult:
    return _apply(repo, diff, check=True, reverse=False)


def apply(repo: Path, diff: str) -> ApplyResult:
    return _apply(repo, diff, check=False, reverse=False)


def revert(repo: Path, diff: str) -> ApplyResult:
    return _apply(repo, diff, check=False, reverse=True)


def stats(diff: str) -> tuple[int, int]:
    added = sum(
        1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    return added, removed
