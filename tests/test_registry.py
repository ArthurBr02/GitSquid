"""The multi-repository list: which repositories the interface can switch between."""

from __future__ import annotations

import json

import pytest

from gitsquid import registry
from gitsquid.config import ConfigError
from tests.conftest import git


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GITSQUID_CONFIG_DIR", str(tmp_path / "config"))


def a_repo(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    return root


class TestRegistry:
    def test_an_empty_registry_lists_nothing(self):
        assert registry.known() == []

    def test_adding_registers_the_repository_root(self, repo):
        assert registry.add(repo) == repo
        assert [entry.name for entry in registry.known()] == ["workshop"]

    def test_adding_from_a_subdirectory_stores_the_root(self, repo):
        nested = repo / "deep" / "deeper"
        nested.mkdir(parents=True)
        assert registry.add(nested) == repo

    def test_a_directory_that_is_not_a_repository_is_refused(self, tmp_path):
        plain = tmp_path / "rien"
        plain.mkdir()
        with pytest.raises(ConfigError, match="not inside a Git repository"):
            registry.add(plain)

    def test_the_most_recent_repository_comes_first(self, tmp_path, repo):
        other = a_repo(tmp_path, "second")
        registry.add(repo)
        registry.add(other)
        assert [entry.name for entry in registry.known()] == ["second", "workshop"]

    def test_adding_twice_does_not_duplicate(self, repo):
        registry.add(repo)
        registry.add(repo)
        assert len(registry.known()) == 1

    def test_removing_leaves_the_repository_on_disk(self, repo):
        registry.add(repo)
        assert registry.remove(repo) is True
        assert registry.known() == []
        assert repo.exists()

    def test_removing_something_absent_reports_it(self, repo):
        assert registry.remove(repo) is False

    def test_entries_report_whether_they_still_exist(self, tmp_path):
        gone = a_repo(tmp_path, "disparu")
        registry.add(gone)
        import shutil

        shutil.rmtree(gone)
        entry = registry.known()[0]
        assert entry.exists is False

    def test_a_corrupt_registry_file_is_ignored(self):
        target = registry.registry_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not json", encoding="utf-8")
        assert registry.known() == []

    def test_a_registry_with_the_wrong_shape_is_ignored(self):
        target = registry.registry_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"repos": "not-a-list"}), encoding="utf-8")
        assert registry.known() == []

    def test_the_list_is_capped(self, tmp_path, monkeypatch):
        target = registry.registry_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"version": 1, "repos": [f"/tmp/r{n}" for n in range(120)]}), encoding="utf-8"
        )
        assert len(registry.known()) == registry.MAX_REPOS
