"""Getting a repository in the first place. Everything else in GitSquid needs one."""

from __future__ import annotations

import re
from pathlib import Path

from .gitcmd import GitError, both, run
from .refs import REMOTE_URL

CLONE_TIMEOUT = 900
NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")


def repository_name(url: str) -> str:
    """`git@host:group/project.git` → `project`, which is where git would put it too."""
    tail = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail.removesuffix(".git") or "repository"


def clone(url: str, parent: Path, *, name: str = "") -> Path:
    """Clone into `parent/name`. The directory must not already exist."""
    url = (url or "").strip()
    if not REMOTE_URL.fullmatch(url) or len(url) > 2000:
        raise GitError("That does not look like a repository: use https://, ssh:// or user@host:path.")
    name = (name or "").strip() or repository_name(url)
    if not NAME.fullmatch(name):
        raise GitError("A folder name is letters, digits, dot, dash or underscore.")

    parent = Path(parent).expanduser()
    if not parent.is_dir():
        raise GitError(f"{parent} is not a directory.")
    target = parent / name
    if target.exists():
        raise GitError(f"{target} already exists. Choose another name, or open it.")

    result = run(parent, ["clone", "--", url, str(target)], timeout=CLONE_TIMEOUT)
    if result.returncode != 0:
        text = both(result)
        if "could not read Username" in text or "Authentication failed" in text or "Permission denied" in text:
            raise GitError(
                "Cloning needs credentials git could not supply without a prompt. "
                "Configure a credential helper or an SSH key, then try again."
            )
        raise GitError(f"Clone failed: {text or 'unknown error'}")
    return target
