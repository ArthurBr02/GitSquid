from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import diffs
from .gitcmd import (
    GitError as WorktreeError,
    both,
    checked,
    default_remote,
    remote_command,
    remotes,
    require_branch,
    require_paths,
    run,
)
from .phrasing import plural
from .safety import is_sensitive_path

MAX_MESSAGE_LEN = 4000
STATUS_LABELS = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "U": "conflicted", "?": "untracked", "T": "typechange",
}
PATCH_TARGETS = {
    "stage": ["--cached"],
    "unstage": ["--cached", "--reverse"],
    "discard": ["--reverse"],
}

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


def line_counts(repo: Path) -> dict[str, dict[str, list[int]]]:
    """Lines gained and lost per file, on each side of the index."""
    found: dict[str, dict[str, list[int]]] = {}
    for side, args in (("staged", ["diff", "--cached", "--numstat"]), ("unstaged", ["diff", "--numstat"])):
        for line in run(repo, args).stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, removed, path = parts[0], parts[1], parts[-1]
            entry = found.setdefault(path, {})
            entry[side] = [-1, -1] if added == "-" else [int(added), int(removed)]
    return found


def status(repo: Path) -> list[FileEntry]:
    """Parse `git status --porcelain=v1 -z`, which is the machine-stable format."""
    result = run(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
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
    require_paths([path])
    if staged:
        return run(repo, ["diff", "--cached", "--no-color", "--", path]).stdout
    result = run(repo, ["diff", "--no-color", "--", path])
    if result.stdout.strip():
        return result.stdout
    if run(repo, ["ls-files", "--error-unmatch", "--", path]).returncode == 0:
        return result.stdout  # tracked and fully staged: no unstaged change is the right answer
    # Untracked files have no diff against the index; show them as a whole-file addition.
    return run(repo, ["diff", "--no-color", "--no-index", "--", "/dev/null", path]).stdout


def stage(repo: Path, paths: list[str]) -> str:
    require_paths(paths)
    checked(repo, ["add", "--", *paths], action="Could not stage")
    return f"Staged {plural(len(paths), 'file')}."


def unstage(repo: Path, paths: list[str]) -> str:
    require_paths(paths)
    result = run(repo, ["restore", "--staged", "--", *paths])
    if result.returncode != 0:  # no HEAD yet: fall back to the plumbing form
        checked(repo, ["rm", "--cached", "-r", "--", *paths], action="Could not unstage")
    return f"Unstaged {plural(len(paths), 'file')}."


def discard(repo: Path, paths: list[str]) -> str:
    """Throw away working-tree edits. Destructive: the caller must confirm first."""
    require_paths(paths)
    tracked = {entry.path for entry in status(repo) if not entry.untracked}
    removed = 0
    for path in paths:
        if path in tracked:
            checked(repo, ["checkout", "--", path], action="Could not discard")
        else:
            target = (repo / path).resolve()
            if repo.resolve() not in target.parents:
                raise WorktreeError(f"Refusing to delete outside the repository: {path}")
            if target.is_file():
                target.unlink()
            removed += 1
    return f"Discarded {plural(len(paths), 'file')}" + (f", {removed} deleted." if removed else ".")


def ignore(repo: Path, paths: list[str]) -> str:
    """Append paths to .gitignore, and drop the tracked ones from the index."""
    require_paths(paths)
    target = repo / ".gitignore"
    existing = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    added = [f"/{path}" for path in paths if f"/{path}" not in existing and path not in existing]
    if not added:
        return "Already ignored."
    body = "\n".join(existing + added)
    target.write_text(body.rstrip("\n") + "\n", encoding="utf-8")
    tracked = [path for path in paths if run(repo, ["ls-files", "--error-unmatch", "--", path]).returncode == 0]
    if tracked:
        run(repo, ["rm", "--cached", "-r", "--", *tracked])
        return (
            f"Ignored {plural(len(added), 'path')} in .gitignore. {len(tracked)} were tracked: their "
            "removal from the index is staged, and takes effect when you commit it."
        )
    return f"Ignored {plural(len(added), 'path')} in .gitignore."


def apply_patch(repo: Path, patch: str, *, target: str) -> str:
    """Stage, unstage, or discard one hunk. Same path validation as a model-proposed diff."""
    if target not in PATCH_TARGETS:
        raise WorktreeError(f"Unknown patch target: {target}")
    problems = diffs.validate(patch)
    if problems:
        raise WorktreeError("; ".join(problems))
    args = ["apply", "-p1", "--whitespace=nowarn", *PATCH_TARGETS[target], "-"]
    result = run(repo, args, stdin=diffs.normalize(patch))
    if result.returncode != 0:
        raise WorktreeError(
            f"git refuses this hunk: {both(result) or 'apply failed'}. "
            "The file changed since the diff was displayed — reload it and try again."
        )
    return {"stage": "Hunk staged.", "unstage": "Hunk unstaged.", "discard": "Hunk discarded."}[target]


RESOLUTIONS = {"ours": "--ours", "theirs": "--theirs"}


def resolve(repo: Path, paths: list[str], *, side: str) -> str:
    """Settle a conflict by keeping one side whole, then staging it as resolved."""
    require_paths(paths)
    if side not in RESOLUTIONS:
        raise WorktreeError("A conflict is resolved with 'ours' or 'theirs'.")
    conflicted = {entry.path for entry in status(repo) if entry.conflicted}
    unknown = [path for path in paths if path not in conflicted]
    if unknown:
        raise WorktreeError(f"{unknown[0]} is not in conflict.")
    checked(repo, ["checkout", RESOLUTIONS[side], "--", *paths], action="Could not resolve")
    checked(repo, ["add", "--", *paths], action="Could not stage the resolution")
    kept = "your side" if side == "ours" else "the incoming side"
    return f"Kept {kept} for {plural(len(paths), 'file')}, staged as resolved."


def commit(repo: Path, message: str, *, amend: bool = False) -> dict:
    message = (message or "").strip()
    if not message:
        raise WorktreeError("A commit message is required.")
    if len(message) > MAX_MESSAGE_LEN:
        raise WorktreeError(f"The commit message must be at most {MAX_MESSAGE_LEN} characters.")
    if amend and run(repo, ["rev-parse", "--verify", "HEAD"]).returncode != 0:
        raise WorktreeError("There is no commit to amend yet.")
    if not amend and not any(entry.staged for entry in status(repo)):
        raise WorktreeError("Nothing is staged. Stage a file before committing.")

    result = run(repo, ["commit", *(["--amend"] if amend else []), "-m", message])
    if result.returncode != 0:
        detail = both(result)
        if "user.email" in detail or "user.name" in detail:
            raise WorktreeError(
                "git has no identity configured for this repository. Run "
                "`git config user.name` and `git config user.email` first."
            )
        raise WorktreeError(f"Commit refused: {detail}")
    sha = run(repo, ["rev-parse", "HEAD"]).stdout.strip()
    return {"sha": sha, "short": sha[:7], "message": message.splitlines()[0], "amend": amend}


def head_message(repo: Path) -> str:
    result = run(repo, ["log", "-1", "--pretty=%B"])
    return result.stdout.strip() if result.returncode == 0 else ""


def checkout(repo: Path, branch: str) -> str:
    branch = require_branch(repo, branch)
    checked(repo, ["checkout", branch], action="Could not switch branch")
    return f"Switched to {branch}."


def create_branch(repo: Path, name: str) -> str:
    name = require_branch(repo, name)
    checked(repo, ["checkout", "-b", name], action="Could not create the branch")
    return f"Created and switched to {name}."


def tracking(repo: Path) -> dict:
    """Ahead/behind counts against the upstream branch, when there is one."""
    upstream = run(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if upstream.returncode != 0:
        return {"upstream": None, "ahead": 0, "behind": 0}
    counts = run(repo, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
    behind = ahead = 0
    if counts.returncode == 0:
        parts = counts.stdout.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])
    return {"upstream": upstream.stdout.strip(), "ahead": ahead, "behind": behind}


def fetch(repo: Path) -> str:
    return remote_command(repo, ["fetch", "--all", "--prune"], action="Fetch")


def pull(repo: Path) -> str:
    return remote_command(repo, ["pull", "--ff-only"], action="Pull")


def push(repo: Path, *, force: bool = False) -> str:
    args = ["push"]
    if force:
        # --force-with-lease refuses to overwrite work the remote gained since the last fetch.
        args.append("--force-with-lease")
    if tracking(repo)["upstream"] is None:
        branch = run(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        args += ["--set-upstream", default_remote(repo), branch]
    return remote_command(repo, args, action="Force push" if force else "Push")


def merge(repo: Path, branch: str, *, squash: bool = False) -> str:
    branch = require_branch(repo, branch)
    mode = ["--squash"] if squash else ["--no-ff"]
    result = run(repo, ["merge", *mode, branch])
    if result.returncode != 0:
        text = both(result)
        if "CONFLICT" in text:
            raise WorktreeError(
                f"Merging {branch} produced conflicts. Resolve them in the working tree, "
                "then commit — or abort the merge."
            )
        raise WorktreeError(f"Merge failed: {text}")
    if squash:
        return f"Squashed {branch} into the index. Commit it to finish."
    return f"Merged {branch}."


def delete_branch(repo: Path, name: str, *, force: bool = False) -> str:
    name = require_branch(repo, name)
    current = run(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if name == current:
        raise WorktreeError("You cannot delete the branch you are on. Switch first.")
    result = run(repo, ["branch", "-D" if force else "-d", name])
    if result.returncode != 0:
        detail = both(result)
        if "not fully merged" in detail:
            raise WorktreeError(
                f"{name} is not fully merged. Deleting it would lose those commits."
            )
        raise WorktreeError(f"Could not delete {name}: {detail}")
    return f"Deleted {name}."


def stash_list(repo: Path) -> list[dict]:
    result = run(repo, ["stash", "list", "--format=%gd%x1f%s%x1f%cr"])
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
    checked(repo, args, action="Could not stash")
    return "Stashed the working tree."


def _require_stash(ref: str) -> str:
    import re

    if not re.fullmatch(r"stash@\{\d{1,4}\}", ref or ""):
        raise WorktreeError("Not a stash reference.")
    return ref


def stash_pop(repo: Path, ref: str) -> str:
    ref = _require_stash(ref)
    result = run(repo, ["stash", "pop", ref])
    if result.returncode != 0:
        raise WorktreeError(f"Could not restore {ref}: {both(result)}")
    return f"Restored {ref}."


def stash_apply(repo: Path, ref: str) -> str:
    """Restore a stash but keep it in the list — the difference GitKraken users expect."""
    ref = _require_stash(ref)
    result = run(repo, ["stash", "apply", ref])
    if result.returncode != 0:
        raise WorktreeError(f"Could not apply {ref}: {both(result)}")
    return f"Applied {ref}, and kept it in the list."


def stash_drop(repo: Path, ref: str) -> str:
    ref = _require_stash(ref)
    checked(repo, ["stash", "drop", ref], action="Could not drop the stash")
    return f"Dropped {ref}."


def stash_branch(repo: Path, ref: str, name: str) -> str:
    ref = _require_stash(ref)
    name = require_branch(repo, name)
    checked(repo, ["stash", "branch", name, ref], action="Could not branch from the stash")
    return f"Created {name} from {ref}."
