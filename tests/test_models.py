from __future__ import annotations

import pytest

from gitia import models
from gitia.models import (
    Change,
    ChangeRepo,
    ChangeSource,
    ChangeStatus,
    Event,
    EventLog,
    TransitionError,
    ValidationError,
    diff_digest,
)

# Referenced through the module so pytest does not try to collect them as test classes.
RunRecord = models.TestRun
RunRepo = models.TestRunRepo

DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
-old
+new
"""


def a_change(**overrides) -> Change:
    fields = {"task": "Rename the total helper", "diff": DIFF}
    fields.update(overrides)
    return Change(**fields)


class TestChangeValidation:
    def test_rejects_empty_task(self):
        with pytest.raises(ValidationError, match="task"):
            a_change(task="   ")

    def test_rejects_empty_diff(self):
        with pytest.raises(ValidationError, match="diff"):
            a_change(diff="")

    def test_rejects_overlong_task(self):
        with pytest.raises(ValidationError, match="at most"):
            a_change(task="x" * 2001)

    def test_strips_control_characters_from_task(self):
        change = a_change(task="clean\x07 this\x00 up")
        assert change.task == "clean this up"

    def test_redacts_secrets_from_rationale(self):
        change = a_change(rationale="used key sk-ant-api03-ABCDEFGHIJKLMNOP to test")
        assert "sk-ant" not in change.rationale
        assert "[redacted]" in change.rationale

    def test_digest_is_content_addressed(self):
        assert a_change().diff_sha == diff_digest(DIFF)
        assert a_change().short_sha == diff_digest(DIFF)[:12]

    def test_accepts_string_enums(self):
        change = a_change(status="applied", source="patch-file")
        assert change.status is ChangeStatus.APPLIED
        assert change.source is ChangeSource.PATCH_FILE


class TestStatusMachine:
    def test_proposed_can_be_applied(self):
        change = a_change()
        change.transition_to(ChangeStatus.APPLIED)
        assert change.status is ChangeStatus.APPLIED
        assert change.applied_at is not None

    def test_proposed_cannot_jump_to_verified(self):
        change = a_change()
        with pytest.raises(TransitionError):
            change.transition_to(ChangeStatus.VERIFIED)

    def test_reverted_is_terminal(self):
        change = a_change(status=ChangeStatus.REVERTED)
        assert not change.can_transition_to(ChangeStatus.APPLIED)
        with pytest.raises(TransitionError, match="final"):
            change.transition_to(ChangeStatus.APPLIED)

    def test_failed_can_be_retried_or_reverted(self):
        change = a_change(status=ChangeStatus.FAILED)
        assert change.can_transition_to(ChangeStatus.APPLIED)
        assert change.can_transition_to(ChangeStatus.REVERTED)


class TestChangeRepo:
    def test_round_trip_preserves_every_field(self, conn):
        repo = ChangeRepo(conn)
        original = a_change(
            model="claude-opus-5",
            files_touched=["calc.py", "test_calc.py"],
            base_commit="a" * 40,
            input_tokens=10,
            output_tokens=20,
        )
        repo.add(original)
        assert original.id is not None

        loaded = repo.get(original.id)
        assert loaded.task == original.task
        assert loaded.diff == original.diff
        assert loaded.files_touched == ["calc.py", "test_calc.py"]
        assert loaded.model == "claude-opus-5"
        assert loaded.input_tokens == 10
        assert loaded.status is ChangeStatus.PROPOSED
        assert loaded.is_sample is False

    def test_get_unknown_id_returns_none(self, conn):
        assert ChangeRepo(conn).get(4321) is None

    def test_save_persists_a_transition(self, conn):
        repo = ChangeRepo(conn)
        change = repo.add(a_change())
        change.transition_to(ChangeStatus.APPLIED)
        repo.save(change)
        assert repo.get(change.id).status is ChangeStatus.APPLIED

    def test_save_without_add_is_rejected(self, conn):
        with pytest.raises(ValidationError):
            ChangeRepo(conn).save(a_change())

    def test_list_is_newest_first_and_filterable(self, conn):
        repo = ChangeRepo(conn)
        first = repo.add(a_change(task="first"))
        second = repo.add(a_change(task="second", status=ChangeStatus.VERIFIED))
        assert [c.id for c in repo.list()] == [second.id, first.id]
        assert [c.task for c in repo.list(status=ChangeStatus.VERIFIED)] == ["second"]
        assert repo.latest().id == second.id
        assert repo.count() == 2

    def test_exists_matches_digest_and_timestamp(self, conn):
        repo = ChangeRepo(conn)
        change = repo.add(a_change())
        assert repo.exists(change.diff_sha, change.created_at)
        assert not repo.exists(change.diff_sha, "2000-01-01T00:00:00+00:00")

    def test_delete_samples_leaves_real_changes(self, conn):
        repo = ChangeRepo(conn)
        repo.add(a_change(task="real work"))
        repo.add(a_change(task="demo", is_sample=True))
        assert repo.delete_samples() == 1
        assert [c.task for c in repo.list()] == ["real work"]


class TestTestRunAndEvents:
    def test_exit_code_drives_passed(self):
        assert RunRecord(command="pytest", exit_code=0, duration_ms=5).passed
        assert not RunRecord(command="pytest", exit_code=1, duration_ms=5).passed

    def test_output_is_redacted_and_tailed(self):
        run = RunRecord(
            command="pytest",
            exit_code=1,
            duration_ms=5,
            output_tail="x" * 5000 + " token=supersecretvalue",
        )
        assert "supersecretvalue" not in run.output_tail
        assert run.output_tail.startswith("…[truncated]")

    def test_runs_are_stored_against_a_change(self, conn):
        change = ChangeRepo(conn).add(a_change())
        runs = RunRepo(conn)
        runs.add(RunRecord(command="pytest -q", exit_code=0, duration_ms=12, change_id=change.id))
        stored = runs.for_change(change.id)
        assert len(stored) == 1
        assert stored[0].passed and stored[0].command == "pytest -q"

    def test_events_are_ordered_and_redacted(self, conn):
        change = ChangeRepo(conn).add(a_change())
        log = EventLog(conn)
        log.record("proposed", "first", change_id=change.id)
        log.record("applied", "key sk-ant-api03-SECRETVALUE1234", change_id=change.id)
        trail = log.for_change(change.id)
        assert [event.kind for event in trail] == ["proposed", "applied"]
        assert "SECRETVALUE" not in trail[1].message

    def test_event_message_is_capped(self):
        assert len(Event(kind="k", message="m" * 9000).message) == 4000

    def test_cascade_delete_removes_children(self, conn):
        change = ChangeRepo(conn).add(a_change(is_sample=True))
        RunRepo(conn).add(
            RunRecord(command="pytest", exit_code=0, duration_ms=1, change_id=change.id, is_sample=True)
        )
        EventLog(conn).record("proposed", "sample", change_id=change.id, is_sample=True)
        ChangeRepo(conn).delete_samples()
        assert RunRepo(conn).for_change(change.id) == []
        assert EventLog(conn).for_change(change.id) == []
