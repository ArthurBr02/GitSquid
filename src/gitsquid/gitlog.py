from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .gitcmd import run

MAX_PATCH_CHARS = 400_000
SEP = "\x1f"
END = "\x1e"
LOG_FORMAT = SEP.join(["%H", "%P", "%an", "%aI", "%s", "%D"]) + END


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: list[str]
    author: str
    date: str
    subject: str
    refs: list[str]

    @property
    def short(self) -> str:
        return self.sha[:7]


def current_branch(repo: Path) -> str:
    result = run(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else "(no branch)"


def branches(repo: Path) -> list[dict[str, str]]:
    result = run(
        repo, ["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)%1f%(objectname:short)%1f%(upstream:short)", "refs/heads"]
    )
    if result.returncode != 0:
        return []
    found = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\x1f")
        if parts and parts[0]:
            found.append(
                {"name": parts[0], "sha": parts[1] if len(parts) > 1 else "", "upstream": parts[2] if len(parts) > 2 else ""}
            )
    return found


def working_status(repo: Path) -> dict[str, int]:
    result = run(repo, ["status", "--porcelain"])
    staged = unstaged = untracked = 0
    for line in result.stdout.splitlines():
        if line.startswith("??"):
            untracked += 1
            continue
        if line[:1].strip():
            staged += 1
        if line[1:2].strip():
            unstaged += 1
    return {"staged": staged, "unstaged": unstaged, "untracked": untracked}


def commits(repo: Path, *, limit: int = 80) -> list[Commit]:
    """Commits newest first, in topological order so the graph can be laid out row by row."""
    result = run(repo, ["log", "--topo-order", f"--max-count={limit}", f"--pretty=format:{LOG_FORMAT}"])
    if result.returncode != 0:
        return []
    found: list[Commit] = []
    for record in result.stdout.split(END):
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split(SEP)
        if len(fields) < 6:
            continue
        sha, parents, author, date, subject, refs = fields[:6]
        found.append(
            Commit(
                sha=sha,
                parents=[p for p in parents.split() if p],
                author=author,
                date=date,
                subject=subject,
                refs=[ref.strip() for ref in refs.split(",") if ref.strip()],
            )
        )
    return found


def commit_detail(repo: Path, sha: str) -> dict:
    """Body and touched files for one commit. `sha` is validated before it reaches git."""
    if not sha or len(sha) > 64 or not all(char in "0123456789abcdefABCDEF" for char in sha):
        raise ValueError("Not a commit id.")
    header = run(repo, ["show", "-s", f"--format={SEP.join(['%H', '%P', '%an', '%aI', '%s', '%D'])}", sha])
    # Never str.strip() a record: Python counts the separator itself as whitespace, so a
    # commit that carries no ref would lose its last field.
    fields = header.stdout.rstrip("\n").split(SEP)
    if header.returncode != 0 or len(fields) < 6:
        raise ValueError("No such commit.")
    parents = fields[1].split()
    # A merge shows no diff at all by default; what a reader wants to see is what it brought in.
    view = ["-m", "--first-parent"] if len(parents) > 1 else []
    body = run(repo, ["show", "-s", "--format=%B", sha]).stdout.strip()
    stat = run(repo, ["show", *view, "--stat", "--format=", sha]).stdout.strip()
    files = run(repo, ["show", *view, "--name-status", "--format=", sha]).stdout.strip()
    patch = run(repo, ["show", *view, "--no-color", "--format=", sha]).stdout
    return {
        "sha": fields[0],
        "short": fields[0][:7],
        "parents": parents,
        "merge": len(parents) > 1,
        "author": fields[2],
        "date": fields[3],
        "subject": fields[4],
        "refs": [ref.strip() for ref in fields[5].split(",") if ref.strip()],
        "body": body,
        "stat": stat,
        "diff": patch[:MAX_PATCH_CHARS],
        "truncated": len(patch) > MAX_PATCH_CHARS,
        "files": [
            {"status": parts[0], "path": parts[-1]}
            for parts in (line.split("\t") for line in files.splitlines() if line.strip())
        ],
    }


def remote_branches(repo: Path) -> list[dict[str, str]]:
    """Remote-tracking branches, minus the symbolic `origin/HEAD`, with their local twin marked."""
    local = {branch["name"] for branch in branches(repo)}
    result = run(
        repo,
        ["for-each-ref", "--sort=-committerdate",
         "--format=%(refname:short)%1f%(objectname:short)%1f%(symref)", "refs/remotes"],
    )
    found = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\x1f")
        name = parts[0] if parts else ""
        if not name or (len(parts) > 2 and parts[2]):
            continue
        short = name.split("/", 1)[1] if "/" in name else name
        found.append({
            "name": name,
            "sha": parts[1] if len(parts) > 1 else "",
            "local": short,
            "tracked": short in local,
        })
    return found


def tags(repo: Path) -> list[dict[str, str]]:
    result = run(
        repo,
        ["for-each-ref", "--sort=-creatordate",
         "--format=%(refname:short)%1f%(objectname:short)%1f%(contents:subject)", "refs/tags"],
    )
    found = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\x1f")
        if parts and parts[0]:
            found.append({
                "name": parts[0],
                "sha": parts[1] if len(parts) > 1 else "",
                "subject": parts[2] if len(parts) > 2 else "",
            })
    return found


def file_history(repo: Path, path: str, *, limit: int = 50) -> list[dict[str, str]]:
    """The commits that touched one file, renames followed."""
    result = run(
        repo,
        ["log", "--follow", f"--max-count={limit}", f"--pretty=format:{LOG_FORMAT}", "--", path],
    )
    if result.returncode != 0:
        return []
    found = []
    for record in result.stdout.split(END):
        fields = record.strip("\n").split(SEP)
        if len(fields) >= 6 and fields[0]:
            found.append({
                "sha": fields[0], "short": fields[0][:7], "author": fields[2],
                "date": fields[3], "subject": fields[4],
            })
    return found


_OPERATIONS = (
    ("rebase-merge", "rebase"), ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"), ("BISECT_LOG", "bisect"),
)


def pending_operation(repo: Path) -> dict | None:
    """A merge, rebase, cherry-pick or revert git stopped in the middle of — usually a conflict."""
    git_dir = run(repo, ["rev-parse", "--absolute-git-dir"])
    if git_dir.returncode != 0:
        return None
    root = Path(git_dir.stdout.strip())
    for marker, kind in _OPERATIONS:
        if (root / marker).exists():
            conflicts = run(repo, ["diff", "--name-only", "--diff-filter=U"]).stdout.splitlines()
            return {"kind": kind, "conflicts": conflicts, "resumable": kind != "bisect"}
    return None
