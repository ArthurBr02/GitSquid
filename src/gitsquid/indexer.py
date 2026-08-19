from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .models import utcnow
from .safety import is_sensitive_path

CHUNK_LINES = 80
IGNORED_DIRS = frozenset(
    {
        ".git", ".gitsquid", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "dist", "build", ".tox", ".idea", ".vscode",
        "target", "vendor", ".next", ".cache", "htmlcov", ".eggs",
    }
)
BINARY_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar",
        ".bz2", ".xz", ".7z", ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2", ".ttf",
        ".otf", ".so", ".dylib", ".dll", ".exe", ".bin", ".class", ".jar", ".pyc", ".db",
        ".sqlite", ".sqlite3", ".wasm",
    }
)
LANGUAGES = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".java": "java", ".kt": "kotlin", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".php": "php", ".swift": "swift", ".sh": "shell", ".bash": "shell",
    ".zsh": "shell", ".sql": "sql", ".html": "html", ".css": "css", ".scss": "css",
    ".md": "markdown", ".rst": "text", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".ini": "ini", ".cfg": "ini",
}


@dataclass(frozen=True)
class IndexStats:
    files_indexed: int = 0
    files_unchanged: int = 0
    files_skipped: int = 0
    files_removed: int = 0
    chunks: int = 0

    @property
    def total_seen(self) -> int:
        return self.files_indexed + self.files_unchanged + self.files_skipped


def language_for(path: Path) -> str:
    return LANGUAGES.get(path.suffix.lower(), "text")


def _is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


def _tracked_paths(repo: Path) -> list[Path] | None:
    """Ask git which files exist and are not ignored — the user's .gitignore is the authority."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [repo / rel for rel in result.stdout.split("\0") if rel]


def _in_ignored_dir(repo: Path, parts: tuple[str, ...], cache: dict[str, bool]) -> bool:
    for depth in range(1, len(parts)):
        branch = parts[:depth]
        if branch[-1] in IGNORED_DIRS:
            return True
        key = "/".join(branch)
        if key not in cache:
            cache[key] = (repo.joinpath(*branch) / "pyvenv.cfg").exists()
        if cache[key]:
            return True
    return False


def walk_repo(repo: Path, *, max_file_bytes: int) -> Iterator[tuple[Path, bool, str]]:
    """Yield (path, indexable, reason) for every candidate file under repo."""
    candidates = _tracked_paths(repo)
    if candidates is None:
        candidates = list(repo.rglob("*"))
    venv_cache: dict[str, bool] = {}

    for path in sorted(candidates):
        if not path.is_file() or path.is_symlink():
            continue
        rel_parts = path.relative_to(repo).parts
        if _in_ignored_dir(repo, rel_parts, venv_cache):
            continue
        rel = "/".join(rel_parts)
        if is_sensitive_path(rel):
            yield path, False, "sensitive"
        elif path.suffix.lower() in BINARY_SUFFIXES:
            yield path, False, "binary"
        elif path.stat().st_size > max_file_bytes:
            yield path, False, "too-large"
        else:
            yield path, True, "ok"


def chunk_lines(lines: list[str], size: int = CHUNK_LINES) -> Iterator[tuple[int, int, str]]:
    for start in range(0, max(len(lines), 1), size):
        window = lines[start : start + size]
        if not window:
            break
        yield start + 1, start + len(window), "".join(window)


def _delete_file_rows(conn: sqlite3.Connection, file_id: int) -> None:
    chunk_ids = [
        row[0] for row in conn.execute("SELECT id FROM chunks WHERE file_id=?", (file_id,))
    ]
    for chunk_id in chunk_ids:
        conn.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (chunk_id,))
    conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))


def index_repo(
    conn: sqlite3.Connection, repo: Path, *, max_file_bytes: int, force: bool = False
) -> IndexStats:
    indexed = unchanged = skipped = chunk_count = 0
    seen: set[str] = set()

    for path, indexable, _reason in walk_repo(repo, max_file_bytes=max_file_bytes):
        rel = path.relative_to(repo).as_posix()
        if not indexable:
            skipped += 1
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            skipped += 1
            continue
        if _is_probably_binary(raw):
            skipped += 1
            continue

        seen.add(rel)
        digest = hashlib.sha256(raw).hexdigest()
        row = conn.execute("SELECT id, sha256 FROM files WHERE path=?", (rel,)).fetchone()
        if row and row["sha256"] == digest and not force:
            unchanged += 1
            continue

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        if row:
            file_id = row["id"]
            _delete_file_rows(conn, file_id)
            conn.execute(
                "UPDATE files SET sha256=?, size_bytes=?, language=?, indexed_at=? WHERE id=?",
                (digest, len(raw), language_for(path), utcnow(), file_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO files (path, sha256, size_bytes, language, chunked, indexed_at)"
                " VALUES (?,?,?,?,1,?)",
                (rel, digest, len(raw), language_for(path), utcnow()),
            )
            file_id = int(cur.lastrowid)

        for ordinal, (start, end, content) in enumerate(chunk_lines(lines)):
            cur = conn.execute(
                "INSERT INTO chunks (file_id, ordinal, start_line, end_line, content)"
                " VALUES (?,?,?,?,?)",
                (file_id, ordinal, start, end, content),
            )
            conn.execute(
                "INSERT INTO chunks_fts (path, content, chunk_id) VALUES (?,?,?)",
                (rel, content, int(cur.lastrowid)),
            )
            chunk_count += 1
        indexed += 1

    removed = 0
    for row in conn.execute("SELECT id, path FROM files").fetchall():
        if row["path"] not in seen:
            _delete_file_rows(conn, row["id"])
            conn.execute("DELETE FROM files WHERE id=?", (row["id"],))
            removed += 1

    return IndexStats(
        files_indexed=indexed,
        files_unchanged=unchanged,
        files_skipped=skipped,
        files_removed=removed,
        chunks=chunk_count,
    )


def index_summary(conn: sqlite3.Connection) -> dict[str, int]:
    files = conn.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM files").fetchone()
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return {"files": files[0], "bytes": files[1], "chunks": chunks}
