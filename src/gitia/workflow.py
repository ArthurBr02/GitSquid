from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import diffs
from .config import Settings
from .llm import Proposal, ProposalBackend, ProposalError, ProposalRequest
from .models import (
    Change,
    ChangeRepo,
    ChangeStatus,
    EventLog,
    TestRun,
    TestRunRepo,
    TransitionError,
    utcnow,
)
from .retrieval import Context, build_context
from .runner import run_tests


class WorkflowError(Exception):
    pass


@dataclass(frozen=True)
class ProposalOutcome:
    change: Change
    context: Context
    applies_cleanly: bool
    check_message: str


class ChangeService:
    """The core loop: propose a diff, apply it, verify it, record every step."""

    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self._conn = conn
        self._settings = settings
        self.changes = ChangeRepo(conn)
        self.test_runs = TestRunRepo(conn)
        self.events = EventLog(conn)

    @property
    def repo(self) -> Path:
        return self._settings.repo

    def propose(
        self, task: str, backend: ProposalBackend, *, pinned: list[str] | None = None
    ) -> ProposalOutcome:
        context = build_context(
            self._conn,
            task,
            budget_chars=self._settings.max_context_chars,
            pinned=pinned,
        )
        request = ProposalRequest(
            task=task,
            context=context.render(),
            files=context.files,
            repo_name=self.repo.name,
        )
        proposal: Proposal = backend.propose(request)
        diff = diffs.normalize(proposal.diff)

        problems = diffs.validate(diff)
        if problems:
            raise ProposalError("Rejected patch: " + "; ".join(problems))

        change = Change(
            task=task,
            diff=diff,
            source=proposal.source,
            model=proposal.model,
            rationale=proposal.rationale,
            files_touched=diffs.touched_files(diff),
            base_commit=diffs.current_commit(self.repo),
            input_tokens=proposal.input_tokens,
            output_tokens=proposal.output_tokens,
        )
        self.changes.add(change)
        self.events.record(
            "proposed",
            f"{proposal.source} proposal touching {len(change.files_touched)} file(s)",
            change_id=change.id,
        )

        result = diffs.check(self.repo, diff)
        if not result.ok:
            self.events.record("check-failed", result.message, change_id=change.id)
        return ProposalOutcome(
            change=change,
            context=context,
            applies_cleanly=result.ok,
            check_message=result.message,
        )

    def apply(self, change: Change) -> Change:
        if change.status is ChangeStatus.APPLIED:
            raise WorkflowError(f"Change #{change.id} is already applied.")
        try:
            change.transition_to(ChangeStatus.APPLIED)
        except TransitionError as exc:
            raise WorkflowError(str(exc)) from exc

        result = diffs.apply(self.repo, change.diff)
        if not result.ok:
            change.status = ChangeStatus.FAILED
            change.applied_at = None
            self.changes.save(change)
            self.events.record("apply-failed", result.message, change_id=change.id)
            raise WorkflowError(f"git apply refused the patch: {result.message}")

        self.changes.save(change)
        self.events.record(
            "applied", f"applied to working tree at {change.applied_at}", change_id=change.id
        )
        return change

    def verify(self, change: Change, *, command: str | None = None) -> TestRun:
        command = command or self._settings.test_command
        run = run_tests(
            self.repo,
            command,
            timeout=self._settings.test_timeout,
            change_id=change.id,
        )
        self.test_runs.add(run)

        target = ChangeStatus.VERIFIED if run.passed else ChangeStatus.FAILED
        if change.can_transition_to(target):
            change.status = target
            self.changes.save(change)
        self.events.record(
            "tested",
            f"`{command}` exited {run.exit_code} in {run.duration_ms}ms",
            change_id=change.id,
        )
        return run

    def revert(self, change: Change) -> Change:
        if change.status not in {ChangeStatus.APPLIED, ChangeStatus.VERIFIED, ChangeStatus.FAILED}:
            raise WorkflowError(
                f"Change #{change.id} is '{change.status}' — there is nothing applied to revert."
            )
        result = diffs.revert(self.repo, change.diff)
        if not result.ok:
            self.events.record("revert-failed", result.message, change_id=change.id)
            raise WorkflowError(
                f"Could not reverse the patch: {result.message}. "
                "The working tree has moved on; resolve it with git."
            )
        change.status = ChangeStatus.REVERTED
        self.changes.save(change)
        self.events.record("reverted", f"reversed at {utcnow()}", change_id=change.id)
        return change
