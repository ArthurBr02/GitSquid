"""Staging, committing, and branching — the GitKraken half of the loop."""

from __future__ import annotations

import pytest

from gitsquid import worktree
from gitsquid.worktree import WorktreeError
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
        for hostile in ["../escape.py", "/etc/passwd", ".git/config", ".gitsquid/gitsquid.db"]:
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


def split_hunks(diff: str) -> tuple[str, list[str]]:
    """The file header, then one text per hunk — what the interface sends back per hunk."""
    header: list[str] = []
    hunks: list[list[str]] = []
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
        else:
            header.append(line)
    return "\n".join(header) + "\n", ["\n".join(lines) + "\n" for lines in hunks]


@pytest.fixture
def two_hunks(repo):
    """A file edited in two places far enough apart for git to emit two hunks."""
    lines = [f"line {number}\n" for number in range(40)]
    (repo / "long.txt").write_text("".join(lines), encoding="utf-8")
    worktree.stage(repo, ["long.txt"])
    worktree.commit(repo, "Ajoute long.txt")

    lines[0] = "premiere ligne modifiee\n"
    lines[-1] = "derniere ligne modifiee\n"
    (repo / "long.txt").write_text("".join(lines), encoding="utf-8")
    return repo


class TestHunks:
    def test_one_hunk_can_be_staged_alone(self, two_hunks):
        header, hunks = split_hunks(worktree.file_diff(two_hunks, "long.txt", staged=False))
        assert len(hunks) == 2

        worktree.apply_patch(two_hunks, header + hunks[0], target="stage")
        staged = worktree.file_diff(two_hunks, "long.txt", staged=True)
        assert "premiere ligne modifiee" in staged
        assert "derniere ligne modifiee" not in staged
        assert "derniere ligne modifiee" in worktree.file_diff(two_hunks, "long.txt", staged=False)

    def test_a_staged_hunk_can_be_returned_to_the_working_tree(self, two_hunks):
        header, hunks = split_hunks(worktree.file_diff(two_hunks, "long.txt", staged=False))
        worktree.apply_patch(two_hunks, header + hunks[0], target="stage")

        staged_header, staged_hunks = split_hunks(
            worktree.file_diff(two_hunks, "long.txt", staged=True)
        )
        worktree.apply_patch(two_hunks, staged_header + staged_hunks[0], target="unstage")
        assert worktree.file_diff(two_hunks, "long.txt", staged=True).strip() == ""

    def test_discarding_a_hunk_leaves_the_other_edit(self, two_hunks):
        header, hunks = split_hunks(worktree.file_diff(two_hunks, "long.txt", staged=False))
        worktree.apply_patch(two_hunks, header + hunks[0], target="discard")

        text = (two_hunks / "long.txt").read_text()
        assert "premiere ligne modifiee" not in text
        assert "derniere ligne modifiee" in text

    def test_a_hunk_touching_a_forbidden_path_is_refused(self, repo):
        patch = (
            "diff --git a/.git/config b/.git/config\n"
            "--- a/.git/config\n+++ b/.git/config\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        with pytest.raises(WorktreeError, match="Refusing path"):
            worktree.apply_patch(repo, patch, target="stage")

    def test_a_hunk_that_no_longer_matches_the_file_is_refused(self, two_hunks):
        header, hunks = split_hunks(worktree.file_diff(two_hunks, "long.txt", staged=False))
        (two_hunks / "long.txt").write_text("tout autre chose\n", encoding="utf-8")
        with pytest.raises(WorktreeError, match="reload it"):
            worktree.apply_patch(two_hunks, header + hunks[0], target="discard")

    def test_an_unknown_target_is_refused(self, repo):
        with pytest.raises(WorktreeError, match="Unknown patch target"):
            worktree.apply_patch(repo, "diff", target="delete")


class TestAmend:
    def test_amending_replaces_the_last_commit(self, repo):
        before = len(git(repo, "log", "--oneline").stdout.splitlines())
        (repo / "calc.py").write_text("oubli\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])

        worktree.commit(repo, "initial, corrige", amend=True)
        assert len(git(repo, "log", "--oneline").stdout.splitlines()) == before
        assert git(repo, "log", "-1", "--pretty=%s").stdout.strip() == "initial, corrige"

    def test_amending_only_the_message_needs_nothing_staged(self, repo):
        worktree.commit(repo, "un meilleur titre", amend=True)
        assert git(repo, "log", "-1", "--pretty=%s").stdout.strip() == "un meilleur titre"

    def test_amending_is_refused_before_the_first_commit(self, tmp_path):
        fresh = tmp_path / "neuf"
        fresh.mkdir()
        git(fresh, "init", "-q", "-b", "main")
        with pytest.raises(WorktreeError, match="no commit to amend"):
            worktree.commit(fresh, "rien", amend=True)

    def test_the_head_message_is_readable_for_the_prefill(self, repo):
        assert worktree.head_message(repo) == "initial"


class TestIgnore:
    def test_a_file_is_added_to_gitignore_and_disappears_from_the_status(self, repo):
        (repo / "bruit.log").write_text("noise\n", encoding="utf-8")
        assert "Ignored" in worktree.ignore(repo, ["bruit.log"])

        assert "/bruit.log" in (repo / ".gitignore").read_text()
        assert entry_for(repo, "bruit.log") is None

    def test_ignoring_twice_changes_nothing(self, repo):
        (repo / "bruit.log").write_text("noise\n", encoding="utf-8")
        worktree.ignore(repo, ["bruit.log"])
        assert worktree.ignore(repo, ["bruit.log"]) == "Already ignored."
        assert (repo / ".gitignore").read_text().count("bruit.log") == 1

    def test_a_tracked_file_has_its_removal_staged(self, repo):
        assert "takes effect when you commit" in worktree.ignore(repo, ["README.md"])
        assert git(repo, "ls-files", "README.md").stdout.strip() == ""
        assert (repo / "README.md").exists()


class TestStashExtras:
    def test_apply_keeps_the_stash_in_the_list(self, repo):
        (repo / "calc.py").write_text("travail\n", encoding="utf-8")
        worktree.stash_save(repo, "garde-moi")

        ref = worktree.stash_list(repo)[0]["ref"]
        assert "kept it" in worktree.stash_apply(repo, ref)
        assert (repo / "calc.py").read_text() == "travail\n"
        assert len(worktree.stash_list(repo)) == 1

    def test_a_stash_can_become_a_branch(self, repo):
        (repo / "calc.py").write_text("travail\n", encoding="utf-8")
        worktree.stash_save(repo, "pour une branche")

        ref = worktree.stash_list(repo)[0]["ref"]
        assert "feature/depuis-stash" in worktree.stash_branch(repo, ref, "feature/depuis-stash")
        assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "feature/depuis-stash"
        assert worktree.stash_list(repo) == []

    def test_squash_merge_leaves_the_work_staged(self, repo):
        worktree.create_branch(repo, "feature/squash")
        (repo / "ajoute.py").write_text("A = 1\n", encoding="utf-8")
        worktree.stage(repo, ["ajoute.py"])
        worktree.commit(repo, "travail a ecraser")
        worktree.checkout(repo, "main")

        assert "Squashed" in worktree.merge(repo, "feature/squash", squash=True)
        assert entry_for(repo, "ajoute.py").staged


class TestLineCounts:
    def test_each_side_of_the_index_is_counted_separately(self, repo):
        (repo / "calc.py").write_text("une ligne\n", encoding="utf-8")
        worktree.stage(repo, ["calc.py"])
        (repo / "calc.py").write_text("une ligne\ndeux lignes\n", encoding="utf-8")

        counts = worktree.line_counts(repo)["calc.py"]
        assert counts["staged"][0] == 1
        assert counts["unstaged"] == [1, 0]

    def test_a_binary_file_is_marked_rather_than_counted(self, repo):
        (repo / "image.bin").write_bytes(bytes(range(256)))
        worktree.stage(repo, ["image.bin"])
        assert worktree.line_counts(repo)["image.bin"]["staged"] == [-1, -1]

    def test_a_clean_tree_counts_nothing(self, repo):
        assert worktree.line_counts(repo) == {}


class TestResolvingConflicts:
    @pytest.fixture
    def conflicted(self, repo):
        (repo / "shared.py").write_text("valeur = 0\n", encoding="utf-8")
        worktree.stage(repo, ["shared.py"])
        worktree.commit(repo, "base")
        worktree.create_branch(repo, "side")
        (repo / "shared.py").write_text("valeur = 2\n", encoding="utf-8")
        worktree.stage(repo, ["shared.py"])
        worktree.commit(repo, "cote")
        worktree.checkout(repo, "main")
        (repo / "shared.py").write_text("valeur = 1\n", encoding="utf-8")
        worktree.stage(repo, ["shared.py"])
        worktree.commit(repo, "principal")
        git(repo, "merge", "side")
        return repo

    def test_keeping_our_side_stages_it_as_resolved(self, conflicted):
        assert "your side" in worktree.resolve(conflicted, ["shared.py"], side="ours")
        assert (conflicted / "shared.py").read_text() == "valeur = 1\n"
        # Keeping our side restores exactly what HEAD holds, so git has nothing left to report.
        assert [entry for entry in worktree.status(conflicted) if entry.conflicted] == []

    def test_keeping_their_side_takes_the_incoming_content(self, conflicted):
        worktree.resolve(conflicted, ["shared.py"], side="theirs")
        assert (conflicted / "shared.py").read_text() == "valeur = 2\n"
        entry = entry_for(conflicted, "shared.py")
        assert entry.staged and not entry.conflicted

    def test_a_file_that_is_not_in_conflict_is_refused(self, conflicted):
        with pytest.raises(WorktreeError, match="not in conflict"):
            worktree.resolve(conflicted, ["calc.py"], side="ours")

    def test_an_unknown_side_is_refused(self, conflicted):
        with pytest.raises(WorktreeError, match="'ours' or 'theirs'"):
            worktree.resolve(conflicted, ["shared.py"], side="mine")
