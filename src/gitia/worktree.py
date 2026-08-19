from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .safety import is_safe_relative_path, is_sensitive_path

MAX_MESSAGE_LEN = 4000
MAX_BRANCH_LEN = 200
STATUS_LABELS = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "U": "conflicted", "?": "untracked", "T": "typechange",
}


class WorktreeError(Exception):
    pass


@dataclass(frozen=True)
class FileEntry:
    path: str
    index_code: str
    work_code: str
    original: str | None = None

    @property
    def staged(self) -> bool:
        return self.index_code not in {" ", "?"}

    @property
    def unstaged(self) -> bool:
        return self.work_code not in {" "}

    @property
    def untracked(self) -> bool:
        return self.index_code == "?"

    @property
    def conflicted(self) -> bool:
        return "U" in (self.index_code, self.work_code)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "original": self.original,
            "index_code": self.index_code,
            "work_code": self.work_code,
            "staged": self.staged,
            "unstaged": self.unstaged,
            "untracked": self.untracked,
            "conflicted": self.conflicted,
            "index_label": STATUS_LABELS.get(self.index_code, ""),
            "work_label": STATUS_LABELS.get(self.work_code, ""),
            "sensitive": is_sensitive_path(self.path),
        }


def _git(repo: Path, args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            # Inherit the environment (git needs HOME for the user's identity) but never
            # let git block the server on an interactive credential prompt.
            env=os.environ | {"GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise WorktreeError("git is not installed or not on PATH.") from exc
    except subprocess.SubprocessError as exc:
        raise WorktreeError(f"git failed: {exc}") from exc


def _checked(repo: Path, args: list[str], *, action: str) -> str:
    result = _git(repo, args)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip() or f"git {args[0]} failed"
        raise WorktreeError(f"{action}: {message}")
    return result.stdout


def _validate_paths(paths: list[str]) -> list[str]:
    if not paths:
        raise WorktreeError("No file selected.")
    if len(paths) > 500:
        raise WorktreeError("Too many files in one operation.")
    for path in paths:
        if not isinstance(path, str) or not is_safe_relative_path(path):
            raise WorktreeError(f"Refusing a path outside the repository: {path!r}")
    return paths


def status(repo: Path) -> list[FileEntry]:
    """Parse `git status --porcelain=v1 -z`, which is the machine-stable format."""
    result = _git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if result.returncode != 0:
        return []
    tokens = result.stdout.split("\0")
    entries: list[FileEntry] = []
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if len(record) < 4:
            continue
        index_code, work_code, path = record[0], record[1], record[3:]
        original = None
        if index_code in {"R", "C"} and index < len(tokens):
            original = tokens[index]
            index += 1
        entries.append(
            FileEntry(path=path, index_code=index_code, work_code=work_code, original=original)
        )
    return sorted(entries, key=lambda entry: entry.path)


def file_diff(repo: Path, path: str, *, staged: bool) -> str:
    _validate_paths([path])
    if staged:
        return _git(repo, ["diff", "--cached", "--no-color", "--", path]).stdout
    result = _git(repo, ["diff", "--no-color", "--", path])
    if result.stdout.strip():
        return result.stdout
    if _git(repo, ["ls-files", "--error-unmatch", "--", path]).returncode == 0:
        return result.stdout  # tracked and fully staged: no unstaged change is the right answer
    # Untracked files have no diff against the index; show them as a whole-file addition.
    return _git(repo, ["diff", "--no-color", "--no-index", "--", "/dev/null", path]).stdout


def stage(repo: Path, paths: list[str]) -> str:
    _validate_paths(paths)
    _checked(repo, ["add", "--", *paths], action="Could not stage")
    return f"Staged {len(paths)} file(s)."


def unstage(repo: Path, paths: list[str]) -> str:
    _validate_paths(paths)
    result = _git(repo, ["restore", "--staged", "--", *paths])
    if result.returncode != 0:  # no HEAD yet: fall back to the plumbing form
        result = _git(repo, ["rm", "--cached", "-r", "--", *paths])
        if result.returncode != 0:
            raise WorktreeError(
                f"Could not unstage: {(result.stderr or result.stdout).strip()}"
            )
    return f"Unstaged {len(paths)} file(s)."


def discard(repo: Path, paths: list[str]) -> str:
    """Throw away working-tree edits. Destructive: the caller must confirm first."""
    _validate_paths(paths)
    tracked = {entry.path for entry in status(repo) if not entry.untracked}
    removed = 0
    for path in paths:
        if path in tracked:
            _checked(repo, ["checkout", "--", path], action="Could not discard")
        else:
            target = (repo / path).resolve()
            if repo.resolve() not in target.parents:
                raise WorktreeError(f"Refusing to delete outside the repository: {path}")
            if target.is_file():
                target.unlink()
            removed += 1
    return f"Discarded {len(paths)} file(s)" + (f", {removed} deleted." if removed else ".")


def commit(repo: Path, message: str) -> dict:
    message = (message or "").strip()
    if not message:
        raise WorktreeError("A commit message is required.")
    if len(message) > MAX_MESSAGE_LEN:
        raise WorktreeError(f"The commit message must be at most {MAX_MESSAGE_LEN} characters.")
    if not any(entry.staged for entry in status(repo)):
        raise WorktreeError("Nothing is staged. Stage a file before committing.")

    result = _git(repo, ["commit", "-m", message])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if "user.email" in detail or "user.name" in detail:
            raise WorktreeError(
                "git has no identity configured for this repository. Run "
                "`git config user.name` and `git config user.email` first."
            )
        raise WorktreeError(f"Commit refused: {detail}")
    sha = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    return {"sha": sha, "short": sha[:7], "message": message.splitlines()[0]}


def _validate_branch(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > MAX_BRANCH_LEN:
        raise WorktreeError("A branch name of 1 to 200 characters is required.")
    return name


def checkout(repo: Path, branch: str) -> str:
    branch = _validate_branch(branch)
    if _git(repo, ["check-ref-format", "--branch", branch]).returncode != 0:
        raise WorktreeError(f"{branch!r} is not a valid branch name.")
    _checked(repo, ["checkout", branch], action="Could not switch branch")
    return f"Switched to {branch}."


def create_branch(repo: Path, name: str) -> str:
    name = _validate_branch(name)
    if _git(repo, ["check-ref-format", "--branch", name]).returncode != 0:
        raise WorktreeError(f"{name!r} is not a valid branch name.")
    _checked(repo, ["checkout", "-b", name], action="Could not create the branch")
    return f"Created and switched to {name}."


def remotes(repo: Path) -> list[dict[str, str]]:
    result = _git(repo, ["remote", "-v"])
    seen: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], parts[1])
    return [{"name": name, "url": url} for name, url in seen.items()]


def tracking(repo: Path) -> dict:
    """Ahead/behind counts against the upstream branch, when there is one."""
    upstream = _git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if upstream.returncode != 0:
        return {"upstream": None, "ahead": 0, "behind": 0}
    counts = _git(repo, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
    behind = ahead = 0
    if counts.returncode == 0:
        parts = counts.stdout.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])
    return {"upstream": upstream.stdout.strip(), "ahead": ahead, "behind": behind}


def _remote_command(repo: Path, args: list[str], *, action: str) -> str:
    if not remotes(repo):
        raise WorktreeError("This repository has no remote configured.")
    result = _git(repo, args, timeout=180)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        if "could not read Username" in output or "Authentication failed" in output:
            raise WorktreeError(
                f"{action} needs credentials git could not supply without a prompt. "
                "Configure a credential helper or an SSH key, then try again."
            )
        raise WorktreeError(f"{action} failed: {output or 'unknown error'}")
    return output or f"{action} done — already up to date."


def fetch(repo: Path) -> str:
    return _remote_command(repo, ["fetch", "--all", "--prune"], action="Fetch")


def pull(repo: Path) -> str:
    return _remote_command(repo, ["pull", "--ff-only"], action="Pull")


def push(repo: Path) -> str:
    args = ["push"]
    if tracking(repo)["upstream"] is None:
        branch = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        args += ["--set-upstream", "origin", branch]
    return _remote_command(repo, args, action="Push")


def merge(repo: Path, branch: str) -> str:
    branch = _validate_branch(branch)
    if _git(repo, ["check-ref-format", "--branch", branch]).returncode != 0:
        raise WorktreeError(f"{branch!r} is not a valid branch name.")
    result = _git(repo, ["merge", "--no-ff", branch])
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        if "CONFLICT" in output:
            raise WorktreeError(
                f"Merging {branch} produced conflicts. gitia does not resolve them — "
                "fix them with git, or run `git merge --abort`."
            )
        raise WorktreeError(f"Merge failed: {output}")
    return f"Merged {branch}."


def delete_branch(repo: Path, name: str, *, force: bool = False) -> str:
    name = _validate_branch(name)
    current = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if name == current:
        raise WorktreeError("You cannot delete the branch you are on. Switch first.")
    result = _git(repo, ["branch", "-D" if force else "-d", name])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if "not fully merged" in detail:
            raise WorktreeError(
                f"{name} is not fully merged. Deleting it would lose those commits."
            )
        raise WorktreeError(f"Could not delete {name}: {detail}")
    return f"Deleted {name}."


def stash_list(repo: Path) -> list[dict]:
    result = _git(repo, ["stash", "list", "--format=%gd%x1f%s%x1f%cr"])
    entries = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) >= 2:
            entries.append(
                {"ref": parts[0], "subject": parts[1], "age": parts[2] if len(parts) > 2 else ""}
            )
    return entries


def stash_save(repo: Path, message: str = "") -> str:
    message = (message or "").strip()[:MAX_MESSAGE_LEN]
    if not status(repo):
        raise WorktreeError("Nothing to stash — the working tree is clean.")
    args = ["stash", "push", "--include-untracked"]
    if message:
        args += ["-m", message]
    _checked(repo, args, action="Could not stash")
    return "Stashed the working tree."


def _validate_stash_ref(ref: str) -> str:
    import re

    if not re.fullmatch(r"stash@\{\d{1,4}\}", ref or ""):
        raise WorktreeError("Not a stash reference.")
    return ref


def stash_pop(repo: Path, ref: str) -> str:
    ref = _validate_stash_ref(ref)
    result = _git(repo, ["stash", "pop", ref])
    if result.returncode != 0:
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        raise WorktreeError(f"Could not restore {ref}: {detail}")
    return f"Restored {ref}."


def stash_drop(repo: Path, ref: str) -> str:
    ref = _validate_stash_ref(ref)
    _checked(repo, ["stash", "drop", ref], action="Could not drop the stash")
    return f"Dropped {ref}."
