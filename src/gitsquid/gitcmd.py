from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .safety import is_safe_relative_path

MAX_REF_LEN = 200
MAX_PATHS = 500


class GitError(Exception):
    pass


def run(
    repo: Path, args: list[str], *, stdin: str | None = None, timeout: int = 60
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            # quotePath=false: a file named café is a file named café, not "caf\303\251".
            ["git", "-c", "core.quotePath=false", "-C", str(repo), *args],
            input=stdin,
            capture_output=True,
            text=True,
            # Repositories hold files git never promised were UTF-8. A latin-1 line must not
            # crash a diff; it comes back with a replacement character instead.
            errors="replace",
            timeout=timeout,
            check=False,
            # Inherit the environment (git needs HOME for the user's identity) but never
            # let git block on an interactive credential prompt.
            env=os.environ | {"GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH.") from exc
    except subprocess.SubprocessError as exc:
        raise GitError(f"git failed: {exc}") from exc


def output(repo: Path, args: list[str], *, timeout: int = 60) -> str:
    result = run(repo, args, timeout=timeout)
    return result.stdout.strip() if result.returncode == 0 else ""


def both(result: subprocess.CompletedProcess) -> str:
    return ((result.stdout or "") + (result.stderr or "")).strip()


def checked(
    repo: Path, args: list[str], *, action: str, stdin: str | None = None, timeout: int = 60
) -> str:
    result = run(repo, args, stdin=stdin, timeout=timeout)
    if result.returncode != 0:
        raise GitError(f"{action}: {both(result) or f'git {args[0]} failed'}")
    return result.stdout


def require_paths(paths: list[str]) -> list[str]:
    if not paths:
        raise GitError("No file selected.")
    if len(paths) > MAX_PATHS:
        raise GitError("Too many files in one operation.")
    for path in paths:
        if not isinstance(path, str) or not is_safe_relative_path(path):
            raise GitError(f"Refusing a path outside the repository: {path!r}")
    return paths


def _require_ref(repo: Path, name: str, *, full: str, kind: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > MAX_REF_LEN or name.startswith("-"):
        raise GitError(f"A {kind} name of 1 to {MAX_REF_LEN} characters is required.")
    if run(repo, ["check-ref-format", full, name]).returncode != 0:
        raise GitError(f"{name!r} is not a valid {kind} name.")
    return name


def require_branch(repo: Path, name: str) -> str:
    return _require_ref(repo, name, full="--branch", kind="branch")


def require_tag(repo: Path, name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > MAX_REF_LEN or name.startswith("-"):
        raise GitError(f"A tag name of 1 to {MAX_REF_LEN} characters is required.")
    if run(repo, ["check-ref-format", f"refs/tags/{name}"]).returncode != 0:
        raise GitError(f"{name!r} is not a valid tag name.")
    return name


def require_sha(value: str) -> str:
    """Commit ids reach git as arguments, so nothing but hexadecimal is ever accepted."""
    sha = (value or "").strip()
    if not 4 <= len(sha) <= 64 or not all(char in "0123456789abcdefABCDEF" for char in sha):
        raise GitError("Not a commit id.")
    return sha


def require_commit_ref(repo: Path, value: str) -> str:
    """A commit id or a branch name — what every history command accepts as a target."""
    candidate = (value or "").strip()
    if all(char in "0123456789abcdefABCDEF" for char in candidate) and 4 <= len(candidate) <= 64:
        return candidate
    return require_branch(repo, candidate)


def remotes(repo: Path) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for line in run(repo, ["remote", "-v"]).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], parts[1])
    return [{"name": name, "url": url} for name, url in seen.items()]


def default_remote(repo: Path) -> str:
    found = remotes(repo)
    if not found:
        raise GitError("This repository has no remote configured.")
    names = [entry["name"] for entry in found]
    return "origin" if "origin" in names else names[0]


def remote_command(repo: Path, args: list[str], *, action: str) -> str:
    """A network operation, with git's credential failure translated into one readable line."""
    if not remotes(repo):
        raise GitError("This repository has no remote configured.")
    result = run(repo, args, timeout=180)
    text = both(result)
    if result.returncode != 0:
        if "could not read Username" in text or "Authentication failed" in text:
            raise GitError(
                f"{action} needs credentials git could not supply without a prompt. "
                "Configure a credential helper or an SSH key, then try again."
            )
        raise GitError(f"{action} failed: {text or 'unknown error'}")
    return text or f"{action} done — already up to date."
