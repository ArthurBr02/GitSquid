"""What survived the rename from gitia to GitSquid, and which repository the interface opens."""

from __future__ import annotations

import json

import pytest

from gitsquid import cli, registry
from tests.conftest import git


@pytest.fixture
def config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GITSQUID_CONFIG_DIR", str(tmp_path / "config"))
    return registry.config_dir()


def make_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "tester@example.invalid")
    git(root, "config", "user.name", "Tester")
    (root / "file.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


class TestTheRepositoryList:
    def test_the_list_written_before_the_rename_is_read(self, config_home, tmp_path):
        older = make_repo(tmp_path / "ancien")
        legacy = config_home.parent / "gitia"
        legacy.mkdir(parents=True)
        (legacy / "repos.json").write_text(
            json.dumps({"version": 1, "repos": [str(older)]}), encoding="utf-8"
        )

        assert [entry.path for entry in registry.known()] == [older]

    def test_adding_a_repository_writes_the_new_file(self, config_home, tmp_path):
        registry.add(make_repo(tmp_path / "neuf"))
        assert (config_home / "repos.json").exists()

    def test_the_old_environment_variable_still_points_at_it(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GITSQUID_CONFIG_DIR", raising=False)
        monkeypatch.setenv("GITIA_CONFIG_DIR", str(tmp_path / "ancienne-config"))
        assert registry.config_dir() == tmp_path / "ancienne-config" / "gitsquid"


class TestTheRepositoryTheInterfaceOpens:
    def test_the_last_one_opened_wins_over_the_current_folder(self, config_home, tmp_path, monkeypatch):
        first = make_repo(tmp_path / "premier")
        last = make_repo(tmp_path / "dernier")
        registry.add(first)
        registry.add(last)
        monkeypatch.chdir(first)

        assert cli._resolve_repo(None) == last

    def test_an_explicit_repository_wins_over_the_memory(self, config_home, tmp_path):
        first = make_repo(tmp_path / "premier")
        registry.add(make_repo(tmp_path / "dernier"))
        assert cli._resolve_repo(first) == first

    def test_a_repository_that_moved_away_is_skipped(self, config_home, tmp_path):
        kept = make_repo(tmp_path / "garde")
        registry.add(kept)
        registry.add(make_repo(tmp_path / "disparu"))
        (tmp_path / "disparu" / ".git").rename(tmp_path / "disparu" / ".git-gone")

        assert cli._resolve_repo(None) == kept

    def test_without_any_memory_the_current_folder_is_used(self, config_home, repo, monkeypatch):
        monkeypatch.chdir(repo)
        assert cli._resolve_repo(None) == repo
