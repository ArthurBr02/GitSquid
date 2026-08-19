"""Named refs: tags, and the branches that live on a remote."""

from __future__ import annotations

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


def push_branch(repo: Path, name: str) -> str:
    name = require_branch(repo, name)
    remote = default_remote(repo)
    remote_command(repo, ["push", "--set-upstream", remote, f"{name}:{name}"], action="Push")
    return f"Pushed {name} to {remote}."
