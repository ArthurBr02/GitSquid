from __future__ import annotations

import pytest

from gitsquid.indexer import index_repo
from gitsquid.llm import ProposalError
from gitsquid.models import ChangeStatus
from gitsquid.workflow import WorkflowError
from tests.conftest import StubBackend, make_patch


@pytest.fixture
def indexed(conn, settings):
    index_repo(conn, settings.repo, max_file_bytes=settings.max_file_bytes)
    return conn


class TestPropose:
    def test_records_a_proposal_without_touching_the_tree(self, service, indexed, repo, patch_add_multiply):
        outcome = service.propose("Add a multiply helper", StubBackend(patch_add_multiply))

        assert outcome.change.id is not None
        assert outcome.change.status is ChangeStatus.PROPOSED
        assert outcome.change.files_touched == ["calc.py"]
        assert outcome.change.model == "stub-model"
        assert outcome.applies_cleanly
        assert "multiply" not in (repo / "calc.py").read_text()

    def test_gives_the_backend_retrieved_context(self, service, indexed, patch_add_multiply):
        backend = StubBackend(patch_add_multiply)
        service.propose("How does total() sum its values?", backend)

        request = backend.requests[0]
        assert "calc.py" in request.files
        assert "def total" in request.context
        assert request.repo_name == "workshop"

    def test_records_the_base_commit_for_traceability(self, service, indexed, patch_add_multiply):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        assert len(outcome.change.base_commit) == 40

    def test_unsafe_patch_is_refused_and_not_recorded(self, service, indexed):
        escape = "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n"
        with pytest.raises(ProposalError, match="Rejected patch"):
            service.propose("Escape the repository", StubBackend(escape))
        assert service.changes.count() == 0

    def test_answer_without_a_diff_is_refused(self, service, indexed):
        with pytest.raises(ProposalError):
            service.propose("Do something", StubBackend("I am not a diff at all\n"))

    def test_stale_patch_is_recorded_but_flagged(self, service, indexed, repo, patch_add_multiply):
        (repo / "calc.py").write_text("unrelated content\n", encoding="utf-8")
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        assert not outcome.applies_cleanly
        assert outcome.check_message
        assert service.changes.get(outcome.change.id) is not None

    def test_context_stays_inside_the_budget(self, service, indexed, patch_add_multiply):
        backend = StubBackend(patch_add_multiply)
        outcome = service.propose("total values add helper", backend)
        assert outcome.context.chars <= 20_000


class TestApplyVerifyRevert:
    def test_full_loop_ends_verified(self, service, indexed, repo, patch_add_multiply):
        outcome = service.propose("Add a multiply helper", StubBackend(patch_add_multiply))
        change = outcome.change

        service.apply(change)
        assert change.status is ChangeStatus.APPLIED
        assert change.applied_at is not None
        assert "def multiply" in (repo / "calc.py").read_text()

        run = service.verify(change)
        assert run.passed
        assert change.status is ChangeStatus.VERIFIED
        assert service.changes.get(change.id).status is ChangeStatus.VERIFIED

    def test_failing_tests_mark_the_change_failed(self, service, indexed, repo, patch_breaks_tests):
        outcome = service.propose("Break addition", StubBackend(patch_breaks_tests))
        service.apply(outcome.change)
        run = service.verify(outcome.change)

        assert not run.passed
        assert outcome.change.status is ChangeStatus.FAILED
        assert "test_add" in run.output_tail

    def test_revert_restores_the_file_and_the_status(self, service, indexed, repo, patch_breaks_tests):
        original = (repo / "calc.py").read_text()
        outcome = service.propose("Break addition", StubBackend(patch_breaks_tests))
        service.apply(outcome.change)
        service.verify(outcome.change)

        service.revert(outcome.change)
        assert outcome.change.status is ChangeStatus.REVERTED
        assert (repo / "calc.py").read_text() == original

    def test_applying_twice_is_refused(self, service, indexed, patch_add_multiply):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        service.apply(outcome.change)
        with pytest.raises(WorkflowError, match="already applied"):
            service.apply(outcome.change)

    def test_reverting_an_unapplied_proposal_is_refused(self, service, indexed, patch_add_multiply):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        with pytest.raises(WorkflowError, match="nothing applied"):
            service.revert(outcome.change)

    def test_reverting_twice_is_refused(self, service, indexed, patch_add_multiply):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        service.apply(outcome.change)
        service.revert(outcome.change)
        with pytest.raises(WorkflowError):
            service.revert(outcome.change)

    def test_apply_failure_marks_failed_and_leaves_the_tree_alone(
        self, service, indexed, repo, patch_add_multiply
    ):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        (repo / "calc.py").write_text("moved on\n", encoding="utf-8")

        with pytest.raises(WorkflowError, match="git apply refused"):
            service.apply(outcome.change)
        assert service.changes.get(outcome.change.id).status is ChangeStatus.FAILED
        assert (repo / "calc.py").read_text() == "moved on\n"

    def test_a_new_file_can_be_proposed_applied_and_reverted(self, service, indexed, repo):
        patch = make_patch(repo, "helpers/strings.py", "def shout(text):\n    return text.upper()\n")
        outcome = service.propose("Add a strings helper", StubBackend(patch))
        service.apply(outcome.change)
        assert (repo / "helpers" / "strings.py").exists()

        service.revert(outcome.change)
        assert not (repo / "helpers" / "strings.py").exists()


class TestAuditTrail:
    def test_every_step_is_recorded(self, service, indexed, patch_add_multiply):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        change = outcome.change
        service.apply(change)
        service.verify(change)
        service.revert(change)

        kinds = [event.kind for event in service.events.for_change(change.id)]
        assert kinds == ["proposed", "applied", "tested", "reverted"]

    def test_test_runs_are_attached_to_the_change(self, service, indexed, patch_add_multiply):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        service.apply(outcome.change)
        service.verify(outcome.change, command="python -c 'print(1)'")

        runs = service.test_runs.for_change(outcome.change.id)
        assert len(runs) == 1 and runs[0].passed
        assert runs[0].duration_ms >= 0

    def test_a_missing_test_command_is_recorded_as_a_failure(self, service, indexed, patch_add_multiply):
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        service.apply(outcome.change)
        run = service.verify(outcome.change, command="definitely-not-a-real-command")

        assert not run.passed
        assert outcome.change.status is ChangeStatus.FAILED

    def test_failed_check_is_recorded(self, service, indexed, repo, patch_add_multiply):
        (repo / "calc.py").write_text("unrelated\n", encoding="utf-8")
        outcome = service.propose("Add multiply", StubBackend(patch_add_multiply))
        kinds = [event.kind for event in service.events.for_change(outcome.change.id)]
        assert kinds == ["proposed", "check-failed"]
