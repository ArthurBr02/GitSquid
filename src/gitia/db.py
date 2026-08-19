from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY,
    path       TEXT    NOT NULL UNIQUE,
    sha256     TEXT    NOT NULL,
    size_bytes INTEGER NOT NULL,
    language   TEXT    NOT NULL DEFAULT 'text',
    chunked    INTEGER NOT NULL DEFAULT 1,
    indexed_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL,
    content    TEXT    NOT NULL,
    UNIQUE(file_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    path, content, chunk_id UNINDEXED, tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS changes (
    id            INTEGER PRIMARY KEY,
    task          TEXT    NOT NULL,
    status        TEXT    NOT NULL CHECK(status IN
                      ('proposed','applied','verified','failed','reverted')),
    source        TEXT    NOT NULL CHECK(source IN ('model','patch-file','sample')),
    model         TEXT,
    rationale     TEXT    NOT NULL DEFAULT '',
    diff          TEXT    NOT NULL,
    diff_sha      TEXT    NOT NULL,
    files_touched TEXT    NOT NULL DEFAULT '',
    base_commit   TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    is_sample     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    applied_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_status ON changes(status);
CREATE INDEX IF NOT EXISTS idx_changes_identity ON changes(diff_sha, created_at);

CREATE TABLE IF NOT EXISTS test_runs (
    id          INTEGER PRIMARY KEY,
    change_id   INTEGER REFERENCES changes(id) ON DELETE CASCADE,
    command     TEXT    NOT NULL,
    exit_code   INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    output_tail TEXT    NOT NULL DEFAULT '',
    is_sample   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_test_runs_change ON test_runs(change_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    change_id  INTEGER REFERENCES changes(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,
    message    TEXT    NOT NULL,
    is_sample  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_change ON events(change_id);
"""


class SchemaError(Exception):
    pass


def connect(db_path: Path, *, create: bool = True) -> sqlite3.Connection:
    if not create and not db_path.exists():
        raise SchemaError(f"No database at {db_path}. Run `gitia init` first.")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # gitia must never appear in the user's own working tree, whatever created the directory.
    ignore = db_path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"Database schema v{version} is newer than this build (v{SCHEMA_VERSION}). Upgrade gitia."
        )
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def open_db(db_path: Path, *, create: bool = True) -> sqlite3.Connection:
    conn = connect(db_path, create=create)
    initialize(conn)
    return conn
