"""Named refs: tags, and the branches that live on a remote."""

from __future__ import annotations

import re
from pathlib import Path

from .gitcmd import (
    GitError,
    checked,
    default_remote,
    remote_command,
    remotes,
    require_branch,
    require_sha,
    require_tag,
    run,
)

MAX_TAG_MESSAGE = 1000


def create_tag(repo: Path, name: str, *, sha: str = "", message: str = "") -> str:
    name = require_tag(repo, name)
    args = ["tag"]
    if message.strip():
        args += ["-a", "-m", message.strip()[:MAX_TAG_MESSAGE]]
    args.append(name)
    if sha:
        args.append(require_sha(sha))
    checked(repo, args, action="Could not create the tag")
    return f"Tagged {sha[:7] or 'HEAD'} as {name}."


def delete_tag(repo: Path, name: str) -> str:
    name = require_tag(repo, name)
    checked(repo, ["tag", "-d", name], action="Could not delete the tag")
    return f"Deleted the tag {name}."


def push_tag(repo: Path, name: str) -> str:
    name = require_tag(repo, name)
    remote = default_remote(repo)
    remote_command(repo, ["push", remote, f"refs/tags/{name}"], action="Push")
    return f"Pushed {name} to {remote}."


def _split_remote(repo: Path, name: str) -> tuple[str, str]:
    """`origin/feature/x` → the remote and the branch name it carries."""
    remote, _, branch = (name or "").partition("/")
    known = {entry["name"] for entry in remotes(repo)}
    if not branch or remote not in known:
        raise GitError(f"{name!r} is not a remote branch.")
    return remote, require_branch(repo, branch)


def track_remote_branch(repo: Path, name: str) -> str:
    """Check out a remote branch: create the local twin the first time, switch to it after."""
    remote, branch = _split_remote(repo, name)
    if run(repo, ["rev-parse", "--verify", f"refs/heads/{branch}"]).returncode == 0:
        checked(repo, ["checkout", branch], action="Could not switch branch")
        return f"Switched to {branch}, which already tracks {remote}."
    checked(repo, ["checkout", "-b", branch, "--track", f"{remote}/{branch}"],
            action="Could not check out the remote branch")
    return f"Created {branch} tracking {remote}/{branch}."


def delete_remote_branch(repo: Path, name: str) -> str:
    """Delete a branch on the remote. Nothing local is touched."""
    remote, branch = _split_remote(repo, name)
    remote_command(repo, ["push", remote, "--delete", branch], action="Delete on the remote")
    return f"Deleted {branch} on {remote}."


def rename_branch(repo: Path, name: str, new_name: str) -> str:
    name = require_branch(repo, name)
    new_name = require_branch(repo, new_name)
    checked(repo, ["branch", "-m", name, new_name], action="Could not rename the branch")
    return f"Renamed {name} to {new_name}."


REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}")
REMOTE_URL = re.compile(r"(?:https?|git|ssh)://\S+|[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:\S+|/\S+")


def add_remote(repo: Path, name: str, url: str) -> str:
    """Give the repository somewhere to push to. Nothing is fetched yet."""
    name, url = (name or "").strip(), (url or "").strip()
    if not REMOTE_NAME.fullmatch(name):
        raise GitError("A remote name is letters, digits, dot, dash or underscore.")
    if not REMOTE_URL.fullmatch(url) or len(url) > 2000:
        raise GitError("That does not look like a remote: use https://, ssh://, user@host:path or an absolute path.")
    if any(entry["name"] == name for entry in remotes(repo)):
        raise GitError(f"A remote called {name} already exists.")
    checked(repo, ["remote", "add", name, url], action="Could not add the remote")
    return f"Added {name} → {url}. Fetch to see its branches."


def remove_remote(repo: Path, name: str) -> str:
    """Forget a remote. Local branches and commits are untouched."""
    name = (name or "").strip()
    if not any(entry["name"] == name for entry in remotes(repo)):
        raise GitError(f"There is no remote called {name}.")
    checked(repo, ["remote", "remove", name], action="Could not remove the remote")
    return f"Removed {name}. Nothing local was touched."


def push_branch(repo: Path, name: str) -> str:
    name = require_branch(repo, name)
    remote = default_remote(repo)
    remote_command(repo, ["push", "--set-upstream", remote, f"{name}:{name}"], action="Push")
    return f"Pushed {name} to {remote}."
