from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    Change,
    ChangeRepo,
    ChangeSource,
    ChangeStatus,
    Event,
    EventLog,
    TestRun,
    TestRunRepo,
    ValidationError,
    diff_digest,
    utcnow,
)

FORMAT = "gitsquid-export"
LEGACY_FORMAT = "gitia-export"
FORMAT_VERSION = 1
MAX_IMPORT_BYTES = 50 * 1024 * 1024


class ImportError_(Exception):
    pass


@dataclass(frozen=True)
class ImportStats:
    changes: int = 0
    test_runs: int = 0
    events: int = 0
    skipped: int = 0


def export_payload(conn: sqlite3.Connection, *, repo_name: str) -> dict[str, Any]:
    changes = ChangeRepo(conn)
    runs = TestRunRepo(conn)
    events = EventLog(conn)
    payload: dict[str, Any] = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "exported_at": utcnow(),
        "repo": repo_name,
        "changes": [],
    }
    for change in reversed(changes.list(limit=1_000_000)):
        payload["changes"].append(
            {
                "task": change.task,
                "status": str(change.status),
                "source": str(change.source),
                "model": change.model,
                "rationale": change.rationale,
                "diff": change.diff,
                "files_touched": change.files_touched,
                "base_commit": change.base_commit,
                "input_tokens": change.input_tokens,
                "output_tokens": change.output_tokens,
                "is_sample": change.is_sample,
                "created_at": change.created_at,
                "applied_at": change.applied_at,
                "test_runs": [
                    {
                        "command": run.command,
                        "exit_code": run.exit_code,
                        "duration_ms": run.duration_ms,
                        "output_tail": run.output_tail,
                        "is_sample": run.is_sample,
                        "created_at": run.created_at,
                    }
                    for run in reversed(runs.for_change(change.id))
                ],
                "events": [
                    {
                        "kind": event.kind,
                        "message": event.message,
                        "is_sample": event.is_sample,
                        "created_at": event.created_at,
                    }
                    for event in events.for_change(change.id)
                ],
            }
        )
    return payload


def export_to_file(conn: sqlite3.Connection, path: Path, *, repo_name: str) -> int:
    payload = export_payload(conn, repo_name=repo_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(payload["changes"])


def _require(mapping: Any, key: str, kinds: tuple[type, ...], *, where: str) -> Any:
    if not isinstance(mapping, dict):
        raise ImportError_(f"{where} must be an object.")
    value = mapping.get(key)
    if not isinstance(value, kinds):
        names = "/".join(kind.__name__ for kind in kinds)
        raise ImportError_(f"{where}: field '{key}' must be {names}.")
    return value


def load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ImportError_(f"No such file: {path}")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise ImportError_(f"{path} is larger than the {MAX_IMPORT_BYTES // 1024 // 1024}MB limit.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportError_(f"{path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") not in {FORMAT, LEGACY_FORMAT}:
        raise ImportError_(f"{path} is not a {FORMAT} file.")
    version = payload.get("version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise ImportError_(f"Unsupported export version: {version!r}.")
    if not isinstance(payload.get("changes"), list):
        raise ImportError_("The export has no 'changes' list.")
    return payload


def _change_from(raw: dict, where: str) -> Change:
    task = _require(raw, "task", (str,), where=where)
    diff = _require(raw, "diff", (str,), where=where)
    created_at = _require(raw, "created_at", (str,), where=where)
    def text(key: str) -> str | None:
        return raw[key] if isinstance(raw.get(key), str) else None

    try:
        return Change(
            task=task,
            diff=diff,
            status=ChangeStatus(raw.get("status", "proposed")),
            source=ChangeSource(raw.get("source", "model")),
            model=text("model"),
            rationale=text("rationale") or "",
            files_touched=[path for path in raw.get("files_touched", []) if isinstance(path, str)],
            base_commit=text("base_commit"),
            is_sample=bool(raw.get("is_sample", False)),
            created_at=created_at,
            applied_at=text("applied_at"),
        )
    except (ValidationError, ValueError) as exc:
        raise ImportError_(f"{where}: {exc}") from exc


def _test_runs_from(raw: dict, change: Change, where: str) -> list[TestRun]:
    return [
        TestRun(
            change_id=change.id,
            command=_require(run, "command", (str,), where=f"{where}.test_runs"),
            exit_code=int(_require(run, "exit_code", (int,), where=f"{where}.test_runs")),
            duration_ms=int(run.get("duration_ms") or 0),
            output_tail=str(run.get("output_tail") or ""),
            is_sample=bool(run.get("is_sample", False)),
            created_at=str(run.get("created_at") or change.created_at),
        )
        for run in raw.get("test_runs", []) or []
    ]


def _events_from(raw: dict, change: Change) -> list[Event]:
    return [
        Event(
            kind=str(event.get("kind") or "imported"),
            message=str(event.get("message") or ""),
            change_id=change.id,
            is_sample=bool(event.get("is_sample", False)),
            created_at=str(event.get("created_at") or change.created_at),
        )
        for event in raw.get("events", []) or []
    ]


def _insert_event(conn: sqlite3.Connection, event: Event) -> None:
    # Not EventLog.record(): an imported event keeps the timestamp it came with.
    conn.execute(
        "INSERT INTO events (change_id, kind, message, is_sample, created_at) VALUES (?,?,?,?,?)",
        (event.change_id, event.kind, event.message, int(event.is_sample), event.created_at),
    )


def import_from_file(conn: sqlite3.Connection, path: Path) -> ImportStats:
    payload = load_payload(path)
    changes = ChangeRepo(conn)
    runs = TestRunRepo(conn)
    events = EventLog(conn)
    imported = skipped = run_count = event_count = 0

    for position, raw in enumerate(payload["changes"], start=1):
        where = f"changes[{position}]"
        change = _change_from(raw, where)
        if changes.exists(diff_digest(change.diff), change.created_at):
            skipped += 1
            continue

        changes.add(change)
        imported += 1
        for run in _test_runs_from(raw, change, where):
            runs.add(run)
            run_count += 1
        for event in _events_from(raw, change):
            _insert_event(conn, event)
            event_count += 1
        events.record("imported", f"imported from {path.name}", change_id=change.id)
        event_count += 1

    return ImportStats(changes=imported, test_runs=run_count, events=event_count, skipped=skipped)
