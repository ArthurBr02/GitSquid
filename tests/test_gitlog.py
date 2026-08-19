"""What git reports for the graph. The lane layout itself lives in tests/test_graph_layout.py."""

from __future__ import annotations

import pytest

from gitia import gitlog
from tests.conftest import git


def commit_file(repo, name, text, message):
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def branchy(repo):
    """One commit that is the parent of three branches, then three merges back."""
    for index in range(3):
        git(repo, "checkout", "-q", "-b", f"feature/{index}", "main")
        commit_file(repo, f"feature_{index}.py", f"VALUE = {index}\n", f"work {index}")
    git(repo, "checkout", "-q", "main")
    for index in range(3):
        git(repo, "merge", "-q", "--no-ff", f"feature/{index}", "-m", f"merge {index}")
    return repo


class TestRefsAndLimits:
    def test_head_and_branches_are_reported(self, branchy):
        head = gitlog.commits(branchy)[0]
        assert any("HEAD" in ref for ref in head.refs)

    def test_the_limit_is_honoured(self, repo):
        for index in range(6):
            commit_file(repo, "counter.py", f"N = {index}\n", f"step {index}")
        assert len(gitlog.commits(repo, limit=3)) == 3

    def test_a_repository_without_commits_yields_nothing(self, tmp_path):
        empty = tmp_path / "vide"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        assert gitlog.commits(empty) == []


class TestParents:
    """The layout is computed in the browser, so the parent list is the whole contract."""

    def test_parents_are_reported_in_order(self, branchy):
        merges = [commit for commit in gitlog.commits(branchy) if len(commit.parents) > 1]
        assert merges
        for merge in merges:
            assert all(len(parent) == 40 for parent in merge.parents)

    def test_every_parent_inside_the_window_is_a_known_commit(self, branchy):
        commits = gitlog.commits(branchy)
        known = {commit.sha for commit in commits}
        reachable = {parent for commit in commits for parent in commit.parents}
        assert reachable <= known, "a parent points outside a window that covers the whole history"

    def test_the_root_commit_has_no_parent(self, branchy):
        assert gitlog.commits(branchy)[-1].parents == []
