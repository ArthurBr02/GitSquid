"""Moving HEAD and replaying commits: the right-click half of a graph client."""

from __future__ import annotations

import pytest

from gitsquid import gitlog, history
from gitsquid.gitcmd import GitError
from tests.conftest import git


def commit_file(repo, name, text, message):
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def two_commits(repo):
    first = git(repo, "rev-parse", "HEAD").stdout.strip()
    second = commit_file(repo, "extra.py", "VALEUR = 1\n", "add extra")
    return repo, first, second


@pytest.fixture
def conflicting(repo):
    """`main` and `side` change the same line, so any replay conflicts."""
    commit_file(repo, "shared.py", "VALEUR = 0\n", "shared")
    git(repo, "checkout", "-q", "-b", "side")
    side = commit_file(repo, "shared.py", "VALEUR = 2\n", "side change")
    git(repo, "checkout", "-q", "main")
    commit_file(repo, "shared.py", "VALEUR = 1\n", "main change")
    return repo, side


class TestCheckoutAndBranch:
    def test_checking_out_a_commit_detaches_head(self, two_commits):
        repo, first, _ = two_commits
        assert "detached" in history.checkout_commit(repo, first)
        assert gitlog.current_branch(repo) == "HEAD"

    def test_a_branch_can_start_at_an_older_commit(self, two_commits):
        repo, first, _ = two_commits
        history.branch_from(repo, first, "feature/repartir")
        assert gitlog.current_branch(repo) == "feature/repartir"
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == first

    def test_only_hexadecimal_reaches_git(self, repo):
        for hostile in ["--upload-pack=touch", "HEAD; rm -rf /", "main", ""]:
            with pytest.raises(GitError, match="Not a commit id"):
                history.checkout_commit(repo, hostile)


class TestReplay:
    def test_cherry_pick_copies_one_commit(self, repo):
        git(repo, "checkout", "-q", "-b", "side")
        sha = commit_file(repo, "picked.py", "VALEUR = 5\n", "work to pick")
        git(repo, "checkout", "-q", "main")

        assert "Cherry-picked" in history.cherry_pick(repo, sha)
        assert (repo / "picked.py").exists()

    def test_revert_undoes_a_commit_with_a_new_commit(self, two_commits):
        repo, _, second = two_commits
        assert "Reverted" in history.revert_commit(repo, second)
        assert not (repo / "extra.py").exists()
        assert len(gitlog.commits(repo)) == 3

    def test_rebase_replays_the_branch(self, repo):
        base = git(repo, "rev-parse", "HEAD").stdout.strip()
        commit_file(repo, "on_main.py", "M = 1\n", "main moves on")
        git(repo, "checkout", "-q", "-b", "feature", base)
        commit_file(repo, "on_feature.py", "F = 1\n", "feature work")

        assert "Replayed" in history.rebase(repo, "main")
        assert (repo / "on_main.py").exists() and (repo / "on_feature.py").exists()

    def test_a_conflicting_cherry_pick_reports_the_conflict(self, conflicting):
        repo, side = conflicting
        with pytest.raises(GitError, match="conflict"):
            history.cherry_pick(repo, side)
        assert gitlog.pending_operation(repo)["kind"] == "cherry-pick"


class TestReset:
    def test_soft_reset_keeps_the_work_staged(self, two_commits):
        repo, first, _ = two_commits
        history.reset(repo, first, mode="soft")
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == first
        assert (repo / "extra.py").exists()

    def test_hard_reset_throws_the_work_away(self, two_commits):
        repo, first, _ = two_commits
        history.reset(repo, first, mode="hard")
        assert not (repo / "extra.py").exists()

    def test_an_unknown_mode_is_refused(self, two_commits):
        repo, first, _ = two_commits
        with pytest.raises(GitError, match="Reset mode"):
            history.reset(repo, first, mode="nuclear")


class TestInterruptedOperations:
    def test_a_clean_repository_has_nothing_to_abort(self, repo):
        assert gitlog.pending_operation(repo) is None
        with pytest.raises(GitError, match="Nothing to abort"):
            history.abort(repo)

    def test_aborting_a_conflicted_merge_restores_the_branch(self, conflicting):
        repo, side = conflicting
        with pytest.raises(GitError):
            history.cherry_pick(repo, side)

        assert "Aborted" in history.abort(repo)
        assert gitlog.pending_operation(repo) is None
        assert (repo / "shared.py").read_text() == "VALEUR = 1\n"

    def test_continuing_is_refused_while_files_still_conflict(self, conflicting):
        repo, side = conflicting
        with pytest.raises(GitError):
            history.cherry_pick(repo, side)
        with pytest.raises(GitError, match="still conflict"):
            history.resume(repo)

    def test_a_resolved_conflict_can_be_continued(self, conflicting):
        repo, side = conflicting
        with pytest.raises(GitError):
            history.cherry_pick(repo, side)

        (repo / "shared.py").write_text("VALEUR = 3\n", encoding="utf-8")
        git(repo, "add", "shared.py")
        assert "Continued" in history.resume(repo)
        assert gitlog.pending_operation(repo) is None


class TestSkipping:
    def test_a_conflicted_cherry_pick_can_be_skipped(self, conflicting):
        repo, side = conflicting
        with pytest.raises(GitError):
            history.cherry_pick(repo, side)

        assert "Skipped" in history.skip(repo)
        assert gitlog.pending_operation(repo) is None
        assert (repo / "shared.py").read_text() == "VALEUR = 1\n"

    def test_there_is_nothing_to_skip_in_a_quiet_repository(self, repo):
        with pytest.raises(GitError, match="Nothing to skip"):
            history.skip(repo)


class TestRestoringOneFile:
    def test_a_file_comes_back_as_it_was(self, repo):
        original = (repo / "calc.py").read_text()
        sha = commit_file(repo, "calc.py", "casse\n", "casse calc")
        first = git(repo, "rev-parse", f"{sha}~1").stdout.strip()

        assert "Restored" in history.restore_file(repo, first, "calc.py")
        assert (repo / "calc.py").read_text() == original

    def test_a_path_outside_the_repository_is_refused(self, repo):
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(GitError, match="outside the repository"):
            history.restore_file(repo, sha, "../../etc/passwd")

    def test_a_file_absent_from_that_commit_is_reported(self, repo):
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(GitError, match="Could not restore"):
            history.restore_file(repo, sha, "jamais-vu.py")
