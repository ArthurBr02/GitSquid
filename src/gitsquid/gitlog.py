from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .gitcmd import run
from .safety import is_safe_relative_path

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


def head(repo: Path) -> dict:
    """Where HEAD is, and whether it is attached to a branch at all."""
    branch = current_branch(repo)
    sha = run(repo, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    return {"branch": branch, "sha": sha, "detached": branch == "HEAD"}


_TRACK = re.compile(r"(ahead|behind) (\d+)")


def branches(repo: Path) -> list[dict]:
    """Local branches, newest first, each with how far it has drifted from its upstream."""
    result = run(
        repo,
        ["for-each-ref", "--sort=-committerdate",
         "--format=%(refname:short)%1f%(objectname:short)%1f%(upstream:short)%1f%(upstream:track)",
         "refs/heads"],
    )
    if result.returncode != 0:
        return []
    found = []
    for line in result.stdout.rstrip("\n").splitlines():
        parts = line.split("\x1f")
        if not parts or not parts[0]:
            continue
        track = dict.fromkeys(("ahead", "behind"), 0)
        for direction, count in _TRACK.findall(parts[3] if len(parts) > 3 else ""):
            track[direction] = int(count)
        found.append({
            "name": parts[0],
            "sha": parts[1] if len(parts) > 1 else "",
            "upstream": parts[2] if len(parts) > 2 else "",
            "ahead": track["ahead"],
            "behind": track["behind"],
        })
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


def count_commits(repo: Path) -> int:
    result = run(repo, ["rev-list", "--count", "HEAD"])
    return int(result.stdout.strip() or 0) if result.returncode == 0 else 0


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


def _require_sha(sha: str) -> str:
    if not sha or len(sha) > 64 or not all(char in "0123456789abcdefABCDEF" for char in sha):
        raise ValueError("Not a commit id.")
    return sha


def _commit_of(repo: Path, sha: str) -> str:
    """An annotated tag and a stash are objects too: read the commit they stand for."""
    _require_sha(sha)
    resolved = run(repo, ["rev-parse", "--verify", f"{sha}^{{commit}}"]).stdout.strip()
    if not resolved:
        raise ValueError("No such commit.")
    return resolved


def _merge_view(parents: list[str]) -> list[str]:
    """A merge shows no diff at all by default; a reader wants what it brought in."""
    return ["-m", "--first-parent"] if len(parents) > 1 else []


def _touched_files(repo: Path, sha: str, view: list[str]) -> list[dict]:
    """One entry per file: its status, and the lines it gained and lost."""
    counts: dict[str, tuple[int | None, int | None]] = {}
    numstat = run(repo, ["show", *view, "--numstat", "--format=", sha]).stdout
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], parts[-1]
        # git writes "-" for a binary file: it has no line count, not a count of zero.
        counts[path] = (
            None if added == "-" else int(added),
            None if removed == "-" else int(removed),
        )

    files = []
    raw = run(repo, ["show", *view, "--name-status", "--format=", sha]).stdout
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        status, path = parts[0], parts[-1]
        added, removed = counts.get(path, (0, 0))
        files.append({
            "status": status[0],
            "score": status[1:],
            "path": path,
            "original": parts[1] if status.startswith(("R", "C")) and len(parts) > 2 else None,
            "added": added,
            "removed": removed,
            "binary": added is None,
        })
    return files


def search(repo: Path, query: str, *, limit: int = 100) -> list[Commit]:
    """Commits whose message, author or touched paths match — the whole history, not the window."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    found: dict[str, Commit] = {}
    for match in (["--grep", query], ["--author", query], ["--all-match", "--", f"*{query}*"]):
        args = ["log", "--all", "-i", f"--max-count={limit}", f"--pretty=format:{LOG_FORMAT}"]
        # A path pattern goes after --, everything else is a filter on the commit itself.
        args += match if match[0] != "--all-match" else match[1:]
        result = run(repo, args)
        if result.returncode != 0:
            continue
        for record in result.stdout.split(END):
            fields = record.strip("\n").split(SEP)
            if len(fields) >= 6 and fields[0] and fields[0] not in found:
                found[fields[0]] = Commit(
                    sha=fields[0],
                    parents=[parent for parent in fields[1].split() if parent],
                    author=fields[2],
                    date=fields[3],
                    subject=fields[4],
                    refs=[ref.strip() for ref in fields[5].split(",") if ref.strip()],
                )
    return sorted(found.values(), key=lambda commit: commit.date, reverse=True)[:limit]


def commit_detail(repo: Path, sha: str) -> dict:
    """Everything the panel shows except the patches, which are fetched one file at a time."""
    sha = _commit_of(repo, sha)
    header = run(repo, ["show", "-s", f"--format={SEP.join(['%H', '%P', '%an', '%ae', '%aI', '%s', '%D'])}", sha])
    # Python counts \x1f as whitespace, so str.strip() would eat the last, often empty, field.
    fields = header.stdout.rstrip("\n").split(SEP)
    if header.returncode != 0 or len(fields) < 7:
        raise ValueError("No such commit.")
    parents = fields[1].split()
    files = _touched_files(repo, sha, _merge_view(parents))
    return {
        "sha": fields[0],
        "short": fields[0][:7],
        "parents": parents,
        "merge": len(parents) > 1,
        "author": fields[2],
        "email": fields[3],
        "date": fields[4],
        "subject": fields[5],
        "refs": [ref.strip() for ref in fields[6].split(",") if ref.strip()],
        "body": run(repo, ["show", "-s", "--format=%B", sha]).stdout.strip(),
        "files": files,
        "added": sum(file["added"] or 0 for file in files),
        "removed": sum(file["removed"] or 0 for file in files),
    }


def commit_patch(
    repo: Path, sha: str, path: str | None = None, *,
    ignore_whitespace: bool = False, context: int = 3,
) -> dict:
    """The patch of one file in a commit — or of the whole commit when no path is given."""
    sha = _commit_of(repo, sha)
    parents = run(repo, ["show", "-s", "--format=%P", sha]).stdout.split()
    args = ["show", *_merge_view(parents), "--no-color", "--format=", "-M",
            f"-U{max(0, min(context, 100))}", sha]
    if ignore_whitespace:
        args.append("-w")
    if path:
        if not is_safe_relative_path(path):
            raise ValueError("Refusing a path outside the repository.")
        args += ["--", path]
    patch = run(repo, args).stdout
    return {
        "sha": sha,
        "path": path or "",
        "diff": patch[:MAX_PATCH_CHARS],
        "truncated": len(patch) > MAX_PATCH_CHARS,
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
         "--format=%(refname:short)%1f%(objectname:short)%1f%(contents:subject)%1f%(*objectname:short)",
         "refs/tags"],
    )
    found = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\x1f")
        if parts and parts[0]:
            dereferenced = parts[3] if len(parts) > 3 else ""
            found.append({
                "name": parts[0],
                # An annotated tag is its own object; what the interface wants is the commit.
                "sha": dereferenced or (parts[1] if len(parts) > 1 else ""),
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


MAX_BLAME_LINES = 8000


def blame(repo: Path, path: str, *, rev: str = "") -> dict:
    """Who last touched each line; an empty `rev` means the working tree as it stands."""
    if not is_safe_relative_path(path):
        raise ValueError("Refusing a path outside the repository.")
    if rev:
        rev = _commit_of(repo, rev)
    args = ["blame", "--porcelain", "-w"]
    if rev:
        args.append(rev)
    result = run(repo, [*args, "--", path], timeout=120)
    if result.returncode != 0:
        raise ValueError(
            "git cannot attribute this file — it is untracked, binary, or absent at that commit."
        )

    authors: dict[str, dict[str, str]] = {}
    lines: list[dict] = []
    current = ""
    for raw in result.stdout.split("\n"):
        if raw.startswith("\t"):
            meta = authors.get(current, {})
            lines.append({
                "sha": current,
                "short": current[:7],
                "author": meta.get("author", ""),
                "date": meta.get("date", ""),
                "summary": meta.get("summary", ""),
                "text": raw[1:],
            })
            if len(lines) >= MAX_BLAME_LINES:
                break
            continue
        if not raw:
            continue
        head, _, rest = raw.partition(" ")
        if len(head) == 40 and all(char in "0123456789abcdef" for char in head):
            current = head
            authors.setdefault(current, {})
        elif current:
            if raw.startswith("author "):
                authors[current]["author"] = rest
            elif raw.startswith("author-time "):
                authors[current]["date"] = _iso(rest.split(" ")[0])
            elif raw.startswith("summary "):
                authors[current]["summary"] = rest
    return {"path": path, "rev": rev, "lines": lines, "truncated": len(lines) >= MAX_BLAME_LINES}


def _iso(epoch: str) -> str:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return ""


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
