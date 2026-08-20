"""Cloning: the one operation that runs before there is a repository to speak of."""

from __future__ import annotations

import pytest

from gitsquid import clone
from gitsquid.gitcmd import GitError
from tests.conftest import git


class TestNaming:
    def test_the_folder_is_named_after_the_repository(self):
        assert clone.repository_name("https://example.invalid/group/projet.git") == "projet"
        assert clone.repository_name("git@example.invalid:group/projet.git") == "projet"
        assert clone.repository_name("https://example.invalid/group/projet/") == "projet"


class TestCloning:
    def test_a_local_repository_is_cloned_and_usable(self, repo, tmp_path):
        target = clone.clone(str(repo), tmp_path, name="copie")
        assert (target / ".git").exists()
        assert git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"

    def test_the_name_defaults_to_the_source(self, repo, tmp_path):
        elsewhere = tmp_path / "ailleurs"
        elsewhere.mkdir()
        assert clone.clone(str(repo), elsewhere).name == repo.name

    def test_an_existing_directory_is_refused(self, repo, tmp_path):
        (tmp_path / "occupe").mkdir()
        with pytest.raises(GitError, match="already exists"):
            clone.clone(str(repo), tmp_path, name="occupe")

    def test_a_hostile_url_or_name_is_refused(self, repo, tmp_path):
        with pytest.raises(GitError, match="does not look like a repository"):
            clone.clone("--upload-pack=touch", tmp_path)
        with pytest.raises(GitError, match="folder name"):
            clone.clone(str(repo), tmp_path, name="../escape")

    def test_a_missing_parent_is_reported(self, repo, tmp_path):
        with pytest.raises(GitError, match="not a directory"):
            clone.clone(str(repo), tmp_path / "nowhere")

    def test_a_source_that_is_not_a_repository_is_reported(self, tmp_path):
        (tmp_path / "vide").mkdir()
        with pytest.raises(GitError, match="Clone failed"):
            clone.clone(str(tmp_path / "vide"), tmp_path, name="copie")
