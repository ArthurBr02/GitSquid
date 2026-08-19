from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def _run(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60, check=False
    )


def current_branch(repo: Path) -> str:
    result = _run(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else "(no branch)"


def branches(repo: Path) -> list[dict[str, str]]:
    result = _run(
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
    result = _run(repo, ["status", "--porcelain"])
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
    result = _run(repo, ["log", "--topo-order", f"--max-count={limit}", f"--pretty=format:{LOG_FORMAT}"])
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
    body = _run(repo, ["show", "-s", "--format=%B", sha]).stdout.strip()
    stat = _run(repo, ["show", "--stat", "--oneline", "--format=", sha]).stdout.strip()
    files = _run(repo, ["show", "--name-status", "--format=", sha]).stdout.strip()
    patch = _run(repo, ["show", "--no-color", "--format=", sha]).stdout
    return {
        "sha": sha,
        "body": body,
        "stat": stat,
        "diff": patch[:MAX_PATCH_CHARS],
        "truncated": len(patch) > MAX_PATCH_CHARS,
        "files": [
            {"status": parts[0], "path": parts[-1]}
            for parts in (line.split("\t") for line in files.splitlines() if line.strip())
        ],
    }
