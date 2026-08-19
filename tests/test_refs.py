"""Tags and the branches that live on a remote."""

from __future__ import annotations

import pytest

from gitia import gitlog, refs
from gitia.gitcmd import GitError
from tests.conftest import git


class TestTags:
    def test_a_lightweight_tag_points_at_head(self, repo):
        assert "v1.0.0" in refs.create_tag(repo, "v1.0.0")
        assert [tag["name"] for tag in gitlog.tags(repo)] == ["v1.0.0"]

    def test_an_annotated_tag_keeps_its_message(self, repo):
        refs.create_tag(repo, "v2.0.0", message="La version deux")
        assert gitlog.tags(repo)[0]["subject"] == "La version deux"

    def test_a_tag_can_name_an_older_commit(self, repo):
        first = git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "later.py").write_text("X = 1\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "later")

        refs.create_tag(repo, "v0.1.0", sha=first)
        assert git(repo, "rev-parse", "v0.1.0^{commit}").stdout.strip() == first

    def test_deleting_removes_it(self, repo):
        refs.create_tag(repo, "v1.0.0")
        assert "Deleted" in refs.delete_tag(repo, "v1.0.0")
        assert gitlog.tags(repo) == []

    def test_hostile_tag_names_are_refused(self, repo):
        for bad in ["v1 0", "-x", "..", "a\\b", ""]:
            with pytest.raises(GitError, match="tag name"):
                refs.create_tag(repo, bad)

    def test_pushing_a_tag_needs_a_remote(self, repo):
        refs.create_tag(repo, "v1.0.0")
        with pytest.raises(GitError, match="no remote"):
            refs.push_tag(repo, "v1.0.0")

    def test_a_tag_reaches_the_remote(self, repo_with_remote, tmp_path):
        refs.create_tag(repo_with_remote, "v1.0.0")
        assert "origin" in refs.push_tag(repo_with_remote, "v1.0.0")
        assert "v1.0.0" in git(tmp_path / "origin.git", "tag").stdout


class TestBranchRenaming:
    def test_renaming_keeps_the_commits(self, repo):
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        refs.rename_branch(repo, "main", "principale")
        assert gitlog.current_branch(repo) == "principale"
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == head

    def test_an_invalid_new_name_is_refused(self, repo):
        with pytest.raises(GitError):
            refs.rename_branch(repo, "main", "nom invalide")


class TestRemoteBranches:
    def test_a_repository_without_a_remote_lists_none(self, repo):
        assert gitlog.remote_branches(repo) == []

    def test_the_pushed_branch_is_listed_and_tracked(self, repo_with_remote):
        listed = gitlog.remote_branches(repo_with_remote)
        assert [entry["name"] for entry in listed] == ["origin/main"]
        assert listed[0]["tracked"] is True

    def test_checking_out_a_remote_branch_creates_the_local_twin(self, repo_with_remote):
        git(repo_with_remote, "checkout", "-q", "-b", "feature/distante")
        (repo_with_remote / "distant.py").write_text("D = 1\n", encoding="utf-8")
        git(repo_with_remote, "add", "-A")
        git(repo_with_remote, "commit", "-q", "-m", "work")
        git(repo_with_remote, "push", "-q", "origin", "feature/distante")
        git(repo_with_remote, "checkout", "-q", "main")
        git(repo_with_remote, "branch", "-q", "-D", "feature/distante")

        assert "tracking" in refs.track_remote_branch(repo_with_remote, "origin/feature/distante")
        assert gitlog.current_branch(repo_with_remote) == "feature/distante"

    def test_deleting_on_the_remote_leaves_the_local_branch(self, repo_with_remote, tmp_path):
        git(repo_with_remote, "push", "-q", "origin", "main:jetable")
        assert "Deleted" in refs.delete_remote_branch(repo_with_remote, "origin/jetable")
        assert "jetable" not in git(tmp_path / "origin.git", "branch").stdout
        assert gitlog.current_branch(repo_with_remote) == "main"

    def test_a_name_without_a_known_remote_is_refused(self, repo_with_remote):
        for bad in ["main", "ailleurs/main", "origin/", ""]:
            with pytest.raises(GitError, match="not a remote branch"):
                refs.track_remote_branch(repo_with_remote, bad)
