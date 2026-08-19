"""Staging, committing, and branching — the GitKraken half of the loop."""

from __future__ import annotations

import pytest

from gitia import worktree
from gitia.worktree import WorktreeError
from tests.conftest import git


def entry_for(repo, path):
    return next((entry for entry in worktree.status(repo) if entry.path == path), None)


class TestStatus:
    def test_a_clean_repository_reports_nothing(self, repo):
        assert worktree.status(repo) == []

    def test_an_edit_is_unstaged(self, repo):
        (repo / "calc.py").write_text("changed\n", encoding="utf-8")
        entry = entry_for(repo, "calc.py")
        assert entry.unstaged and not entry.staged
        assert entry.work_code == "M"
        assert entry.as_dict()["work_label"] == "modified"

    def test_a_new_file_is_untracked(self, repo):
        (repo / "fresh.py").write_text("x = 1\n", encoding="utf-8")
        entry = entry_for(repo, "fresh.py")
        assert entry.untracked and not entry.staged

    def test_staging_moves_a_file_to_the_index(self, repo):
        (repo / "calc.py").write_text("changed\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        entry = entry_for(repo, "calc.py")
        assert entry.staged and entry.index_code == "M"

    def test_deletions_are_reported(self, repo):
        (repo / "README.md").unlink()
        assert entry_for(repo, "README.md").work_code == "D"

    def test_credential_files_are_flagged(self, repo):
        (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
        assert entry_for(repo, ".env").as_dict()["sensitive"] is True

    def test_paths_with_spaces_survive_parsing(self, repo):
        (repo / "a file.py").write_text("x = 1\n", encoding="utf-8")
        assert entry_for(repo, "a file.py") is not None


class TestStageUnstageDiscard:
    def test_unstage_returns_a_file_to_the_working_tree(self, repo):
        (repo / "calc.py").write_text("changed\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        worktree.unstage(repo, ["calc.py"])
        entry = entry_for(repo, "calc.py")
        assert entry.unstaged and not entry.staged

    def test_discard_restores_a_tracked_file(self, repo):
        original = (repo / "calc.py").read_text()
        (repo / "calc.py").write_text("ruined\n", encoding="utf-8")
        worktree.discard(repo, ["calc.py"])
        assert (repo / "calc.py").read_text() == original

    def test_discard_deletes_an_untracked_file(self, repo):
        (repo / "junk.py").write_text("noise\n", encoding="utf-8")
        worktree.discard(repo, ["junk.py"])
        assert not (repo / "junk.py").exists()

    def test_paths_outside_the_repository_are_refused(self, repo):
        for hostile in ["../escape.py", "/etc/passwd", ".git/config", ".gitia/gitia.db"]:
            with pytest.raises(WorktreeError, match="outside the repository"):
                worktree.stage(repo, [hostile])

    def test_an_empty_selection_is_refused(self, repo):
        with pytest.raises(WorktreeError, match="No file selected"):
            worktree.stage(repo, [])

    def test_a_huge_selection_is_refused(self, repo):
        with pytest.raises(WorktreeError, match="Too many files"):
            worktree.stage(repo, [f"file{n}.py" for n in range(501)])


class TestCommit:
    def test_committing_staged_work_creates_a_commit(self, repo):
        (repo / "calc.py").write_text("changed\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        result = worktree.commit(repo, "Change the calculation")

        assert len(result["sha"]) == 40
        assert result["short"] == result["sha"][:7]
        assert worktree.status(repo) == []
        assert "Change the calculation" in git(repo, "log", "-1", "--pretty=%s").stdout

    def test_committing_nothing_is_refused(self, repo):
        with pytest.raises(WorktreeError, match="Nothing is staged"):
            worktree.commit(repo, "empty")

    def test_an_empty_message_is_refused(self, repo):
        (repo / "calc.py").write_text("changed\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        with pytest.raises(WorktreeError, match="message is required"):
            worktree.commit(repo, "   ")

    def test_an_overlong_message_is_refused(self, repo):
        (repo / "calc.py").write_text("changed\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        with pytest.raises(WorktreeError, match="at most"):
            worktree.commit(repo, "x" * 4001)

    def test_only_staged_work_is_committed(self, repo):
        (repo / "calc.py").write_text("staged change\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        (repo / "README.md").write_text("unstaged change\n", encoding="utf-8")

        worktree.commit(repo, "Only the staged file")
        remaining = [entry.path for entry in worktree.status(repo)]
        assert remaining == ["README.md"]


class TestBranches:
    def test_create_switches_to_the_new_branch(self, repo):
        assert "feature/x" in worktree.create_branch(repo, "feature/x")
        assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "feature/x"

    def test_checkout_returns_to_an_existing_branch(self, repo):
        worktree.create_branch(repo, "feature/y")
        worktree.checkout(repo, "main")
        assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"

    def test_invalid_branch_names_are_refused(self, repo):
        for bad in ["bad name", "..", "-x", "a\\b"]:
            with pytest.raises(WorktreeError):
                worktree.create_branch(repo, bad)

    def test_an_empty_branch_name_is_refused(self, repo):
        with pytest.raises(WorktreeError, match="1 to 200"):
            worktree.checkout(repo, "  ")


class TestFileDiff:
    def test_unstaged_edits_produce_a_diff(self, repo):
        (repo / "calc.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")
        diff = worktree.file_diff(repo, "calc.py", staged=False)
        assert "@@" in diff and "return a * b" in diff

    def test_staged_edits_are_read_from_the_index(self, repo):
        (repo / "calc.py").write_text("staged version\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        assert "staged version" in worktree.file_diff(repo, "calc.py", staged=True)
        assert worktree.file_diff(repo, "calc.py", staged=False).strip() == ""

    def test_an_untracked_file_reads_as_a_whole_addition(self, repo):
        (repo / "brand-new.py").write_text("VALUE = 3\n", encoding="utf-8")
        diff = worktree.file_diff(repo, "brand-new.py", staged=False)
        assert "+VALUE = 3" in diff

    def test_a_hostile_path_is_refused(self, repo):
        with pytest.raises(WorktreeError, match="outside the repository"):
            worktree.file_diff(repo, "../../etc/passwd", staged=False)


class TestRemotesAndStash:
    def test_a_repository_without_a_remote_reports_none(self, repo):
        assert worktree.remotes(repo) == []
        assert worktree.tracking(repo) == {"upstream": None, "ahead": 0, "behind": 0}

    def test_remote_commands_refuse_without_a_remote(self, repo):
        for action in (worktree.fetch, worktree.pull, worktree.push):
            with pytest.raises(WorktreeError, match="no remote configured"):
                action(repo)

    def test_a_configured_remote_is_listed(self, repo):
        git(repo, "remote", "add", "origin", "https://example.invalid/depot.git")
        assert worktree.remotes(repo) == [
            {"name": "origin", "url": "https://example.invalid/depot.git"}
        ]

    def test_stash_hides_then_restores_the_working_tree(self, repo):
        original = (repo / "calc.py").read_text()
        (repo / "calc.py").write_text("travail en cours\n", encoding="utf-8")

        worktree.stash_save(repo, "mon travail")
        assert (repo / "calc.py").read_text() == original
        entries = worktree.stash_list(repo)
        assert len(entries) == 1 and "mon travail" in entries[0]["subject"]

        worktree.stash_pop(repo, entries[0]["ref"])
        assert (repo / "calc.py").read_text() == "travail en cours\n"
        assert worktree.stash_list(repo) == []

    def test_stashing_a_clean_tree_is_refused(self, repo):
        with pytest.raises(WorktreeError, match="clean"):
            worktree.stash_save(repo)

    def test_stash_references_are_validated(self, repo):
        for hostile in ["stash@{0}; rm -rf /", "HEAD", "stash@{}", "../x"]:
            with pytest.raises(WorktreeError, match="stash reference"):
                worktree.stash_pop(repo, hostile)

    def test_merge_brings_in_a_branch(self, repo):
        worktree.create_branch(repo, "feature/merge")
        (repo / "extra.py").write_text("VALEUR = 2\n", encoding="utf-8")
        worktree.stage(repo, ["extra.py"])
        worktree.commit(repo, "Ajoute extra.py")
        worktree.checkout(repo, "main")

        assert "Merged" in worktree.merge(repo, "feature/merge")
        assert (repo / "extra.py").exists()

    def test_deleting_an_unmerged_branch_is_refused(self, repo):
        worktree.create_branch(repo, "feature/perdue")
        (repo / "seule.py").write_text("x = 1\n", encoding="utf-8")
        worktree.stage(repo, ["seule.py"])
        worktree.commit(repo, "Travail non fusionne")
        worktree.checkout(repo, "main")

        with pytest.raises(WorktreeError, match="not fully merged"):
            worktree.delete_branch(repo, "feature/perdue")
        assert "Deleted" in worktree.delete_branch(repo, "feature/perdue", force=True)
