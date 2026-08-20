"""Operations that move HEAD or replay commits: what a graph client offers on a right click."""

from __future__ import annotations

from pathlib import Path

from . import gitlog
from .phrasing import conflicts
from .gitcmd import GitError, both, checked, require_branch, require_commit_ref, require_sha, run

RESET_MODES = {"soft", "mixed", "hard"}
_CONTINUE = {
    "rebase": ["rebase", "--continue"],
    "merge": ["merge", "--continue"],
    "cherry-pick": ["cherry-pick", "--continue"],
    "revert": ["revert", "--continue"],
}
_NO_EDITOR = ["-c", "core.editor=true"]


def _conflict_guard(repo: Path, result, *, action: str) -> str:
    text = both(result)
    if result.returncode == 0:
        return text
    if "CONFLICT" in text or "conflict" in text:
        raise GitError(
            f"{action} stopped on a conflict. Resolve the files, then continue — or abort it."
        )
    raise GitError(f"{action} failed: {text or 'unknown error'}")


def checkout_commit(repo: Path, sha: str) -> str:
    sha = require_sha(sha)
    checked(repo, ["checkout", "--detach", sha], action="Could not check out that commit")
    return f"HEAD is now detached at {sha[:7]}. Create a branch to keep work here."


def branch_from(repo: Path, sha: str, name: str) -> str:
    sha = require_sha(sha)
    name = require_branch(repo, name)
    checked(repo, ["checkout", "-b", name, sha], action="Could not create the branch")
    return f"Created {name} at {sha[:7]}."


def cherry_pick(repo: Path, sha: str) -> str:
    sha = require_sha(sha)
    _conflict_guard(repo, run(repo, [*_NO_EDITOR, "cherry-pick", sha]), action="Cherry-pick")
    return f"Cherry-picked {sha[:7]} onto {gitlog.current_branch(repo)}."


def revert_commit(repo: Path, sha: str) -> str:
    """A new commit that undoes an old one — history is added to, never rewritten."""
    sha = require_sha(sha)
    _conflict_guard(repo, run(repo, ["revert", "--no-edit", sha]), action="Revert")
    return f"Reverted {sha[:7]} in a new commit."


def reset(repo: Path, sha: str, *, mode: str = "mixed") -> str:
    if mode not in RESET_MODES:
        raise GitError(f"Reset mode must be one of {', '.join(sorted(RESET_MODES))}.")
    sha = require_sha(sha)
    checked(repo, ["reset", f"--{mode}", sha], action="Could not reset")
    kept = {
        "soft": "the working tree and the index are untouched",
        "mixed": "the working tree is untouched, the index was cleared",
        "hard": "the working tree was overwritten",
    }[mode]
    return f"{gitlog.current_branch(repo)} now points at {sha[:7]} — {kept}."


def rebase(repo: Path, target: str) -> str:
    target = require_commit_ref(repo, target)
    branch = gitlog.current_branch(repo)
    _conflict_guard(repo, run(repo, [*_NO_EDITOR, "rebase", target], timeout=180), action="Rebase")
    return f"Replayed {branch} onto {target}."


def abort(repo: Path) -> str:
    pending = gitlog.pending_operation(repo)
    if pending is None:
        raise GitError("Nothing to abort — no merge, rebase, cherry-pick or revert is in progress.")
    checked(repo, [pending["kind"], "--abort"], action=f"Could not abort the {pending['kind']}")
    return f"Aborted the {pending['kind']}. The repository is back where it started."


SKIPPABLE = {"rebase", "cherry-pick", "revert"}


def skip(repo: Path) -> str:
    """Leave this commit out of the replay and carry on with the next one."""
    pending = gitlog.pending_operation(repo)
    if pending is None or pending["kind"] not in SKIPPABLE:
        raise GitError("Nothing to skip.")
    result = run(repo, [*_NO_EDITOR, pending["kind"], "--skip"], timeout=180)
    _conflict_guard(repo, result, action=pending["kind"].capitalize())
    return f"Skipped that commit and continued the {pending['kind']}."


def resume(repo: Path) -> str:
    pending = gitlog.pending_operation(repo)
    if pending is None or pending["kind"] not in _CONTINUE:
        raise GitError("Nothing to continue.")
    if pending["conflicts"]:
        raise GitError(
            f"{conflicts(len(pending['conflicts']))}. Resolve and stage them first."
        )
    result = run(repo, [*_NO_EDITOR, *_CONTINUE[pending["kind"]]], timeout=180)
    _conflict_guard(repo, result, action=pending["kind"].capitalize())
    return f"Continued the {pending['kind']}."
