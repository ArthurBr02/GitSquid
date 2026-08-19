from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from .diffs import normalize as normalize_diff
from .safety import clean_patch_input, clean_text_input, redact, tail

MAX_TASK_LEN = 2000
MAX_RATIONALE_LEN = 20_000
MAX_DIFF_LEN = 1_000_000


class ChangeStatus(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    VERIFIED = "verified"
    FAILED = "failed"
    REVERTED = "reverted"


class ChangeSource(StrEnum):
    MODEL = "model"
    PATCH_FILE = "patch-file"
    SAMPLE = "sample"


_TERMINAL = {ChangeStatus.REVERTED}
_ALLOWED_TRANSITIONS: dict[ChangeStatus, frozenset[ChangeStatus]] = {
    ChangeStatus.PROPOSED: frozenset({ChangeStatus.APPLIED, ChangeStatus.FAILED}),
    ChangeStatus.APPLIED: frozenset(
        {ChangeStatus.VERIFIED, ChangeStatus.FAILED, ChangeStatus.REVERTED}
    ),
    ChangeStatus.VERIFIED: frozenset({ChangeStatus.REVERTED}),
    ChangeStatus.FAILED: frozenset({ChangeStatus.REVERTED, ChangeStatus.APPLIED}),
    ChangeStatus.REVERTED: frozenset(),
}


class ValidationError(ValueError):
    pass


class TransitionError(Exception):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def diff_digest(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()


@dataclass(slots=True)
class Change:
    task: str
    diff: str
    source: ChangeSource = ChangeSource.MODEL
    status: ChangeStatus = ChangeStatus.PROPOSED
    model: str | None = None
    rationale: str = ""
    files_touched: list[str] = field(default_factory=list)
    base_commit: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    is_sample: bool = False
    created_at: str = field(default_factory=utcnow)
    applied_at: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        try:
            self.task = clean_text_input(self.task, max_len=MAX_TASK_LEN, field="task")
            self.diff = normalize_diff(clean_patch_input(self.diff, max_len=MAX_DIFF_LEN, field="diff"))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if self.rationale:
            self.rationale = redact(self.rationale)[:MAX_RATIONALE_LEN]
        self.status = ChangeStatus(self.status)
        self.source = ChangeSource(self.source)

    @property
    def diff_sha(self) -> str:
        return diff_digest(self.diff)

    @property
    def short_sha(self) -> str:
        return self.diff_sha[:12]

    def can_transition_to(self, target: ChangeStatus) -> bool:
        return ChangeStatus(target) in _ALLOWED_TRANSITIONS[self.status]

    def transition_to(self, target: ChangeStatus) -> None:
        target = ChangeStatus(target)
        if not self.can_transition_to(target):
            reason = "is final" if self.status in _TERMINAL else f"cannot become {target}"
            raise TransitionError(f"Change in status '{self.status}' {reason}.")
        self.status = target
        if target is ChangeStatus.APPLIED:
            self.applied_at = utcnow()


@dataclass(slots=True)
class TestRun:
    command: str
    exit_code: int
    duration_ms: int
    change_id: int | None = None
    output_tail: str = ""
    is_sample: bool = False
    created_at: str = field(default_factory=utcnow)
    id: int | None = None

    def __post_init__(self) -> None:
        self.output_tail = tail(redact(self.output_tail or ""))

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(slots=True)
class Event:
    kind: str
    message: str
    change_id: int | None = None
    is_sample: bool = False
    created_at: str = field(default_factory=utcnow)
    id: int | None = None

    def __post_init__(self) -> None:
        self.message = redact(self.message)[:4000]


def _row_to_change(row: sqlite3.Row) -> Change:
    change = Change.__new__(Change)
    change.id = row["id"]
    change.task = row["task"]
    change.status = ChangeStatus(row["status"])
    change.source = ChangeSource(row["source"])
    change.model = row["model"]
    change.rationale = row["rationale"]
    change.diff = row["diff"]
    change.files_touched = json.loads(row["files_touched"] or "[]")
    change.base_commit = row["base_commit"]
    change.input_tokens = row["input_tokens"]
    change.output_tokens = row["output_tokens"]
    change.is_sample = bool(row["is_sample"])
    change.created_at = row["created_at"]
    change.applied_at = row["applied_at"]
    return change


class ChangeRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, change: Change) -> Change:
        cur = self._conn.execute(
            """INSERT INTO changes
               (task, status, source, model, rationale, diff, diff_sha, files_touched,
                base_commit, input_tokens, output_tokens, is_sample, created_at, applied_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                change.task,
                str(change.status),
                str(change.source),
                change.model,
                change.rationale,
                change.diff,
                change.diff_sha,
                json.dumps(change.files_touched),
                change.base_commit,
                change.input_tokens,
                change.output_tokens,
                int(change.is_sample),
                change.created_at,
                change.applied_at,
            ),
        )
        change.id = int(cur.lastrowid)
        return change

    def save(self, change: Change) -> Change:
        if change.id is None:
            raise ValidationError("Cannot save a change that was never added.")
        self._conn.execute(
            """UPDATE changes SET status=?, rationale=?, files_touched=?, applied_at=?
               WHERE id=?""",
            (
                str(change.status),
                change.rationale,
                json.dumps(change.files_touched),
                change.applied_at,
                change.id,
            ),
        )
        return change

    def get(self, change_id: int) -> Change | None:
        row = self._conn.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone()
        return _row_to_change(row) if row else None

    def list(self, *, limit: int = 20, status: ChangeStatus | None = None) -> list[Change]:
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM changes WHERE status=? ORDER BY id DESC LIMIT ?",
                (str(status), limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM changes ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_change(row) for row in rows]

    def latest(self) -> Change | None:
        rows = self.list(limit=1)
        return rows[0] if rows else None

    def exists(self, diff_sha: str, created_at: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM changes WHERE diff_sha=? AND created_at=?", (diff_sha, created_at)
        ).fetchone()
        return row is not None

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0])

    def delete_samples(self) -> int:
        cur = self._conn.execute("DELETE FROM changes WHERE is_sample=1")
        return cur.rowcount or 0


class TestRunRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, run: TestRun) -> TestRun:
        cur = self._conn.execute(
            """INSERT INTO test_runs
               (change_id, command, exit_code, duration_ms, passed, output_tail, is_sample, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                run.change_id,
                run.command,
                run.exit_code,
                run.duration_ms,
                int(run.passed),
                run.output_tail,
                int(run.is_sample),
                run.created_at,
            ),
        )
        run.id = int(cur.lastrowid)
        return run

    def for_change(self, change_id: int) -> list[TestRun]:
        rows = self._conn.execute(
            "SELECT * FROM test_runs WHERE change_id=? ORDER BY id DESC", (change_id,)
        ).fetchall()
        return [
            TestRun(
                id=row["id"],
                change_id=row["change_id"],
                command=row["command"],
                exit_code=row["exit_code"],
                duration_ms=row["duration_ms"],
                output_tail=row["output_tail"],
                is_sample=bool(row["is_sample"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_samples(self) -> int:
        cur = self._conn.execute("DELETE FROM test_runs WHERE is_sample=1")
        return cur.rowcount or 0


class EventLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(
        self, kind: str, message: str, *, change_id: int | None = None, is_sample: bool = False
    ) -> Event:
        event = Event(kind=kind, message=message, change_id=change_id, is_sample=is_sample)
        cur = self._conn.execute(
            "INSERT INTO events (change_id, kind, message, is_sample, created_at) VALUES (?,?,?,?,?)",
            (event.change_id, event.kind, event.message, int(event.is_sample), event.created_at),
        )
        event.id = int(cur.lastrowid)
        return event

    def for_change(self, change_id: int) -> list[Event]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE change_id=? ORDER BY id", (change_id,)
        ).fetchall()
        return [
            Event(
                id=row["id"],
                change_id=row["change_id"],
                kind=row["kind"],
                message=row["message"],
                is_sample=bool(row["is_sample"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def recent(self, limit: int = 20) -> list[Event]:
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Event(
                id=row["id"],
                change_id=row["change_id"],
                kind=row["kind"],
                message=row["message"],
                is_sample=bool(row["is_sample"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_samples(self) -> int:
        cur = self._conn.execute("DELETE FROM events WHERE is_sample=1")
        return cur.rowcount or 0
