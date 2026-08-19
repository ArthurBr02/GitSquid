"""What still works after the rename from gitia to GitSquid, and what the interface reopens."""

from __future__ import annotations

import json

import pytest

from gitsquid import cli, portability, registry
from gitsquid.config import ConfigError, load_settings
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


class TestRenamedState:
    def test_a_fresh_repository_uses_the_new_directory(self, repo):
        settings = load_settings(repo)
        assert settings.state_dir == repo / ".gitsquid"
        assert settings.db_path.name == "gitsquid.db"

    def test_a_repository_indexed_before_the_rename_keeps_its_database(self, repo):
        legacy = repo / ".gitia"
        legacy.mkdir()
        (legacy / "gitia.db").write_bytes(b"")

        settings = load_settings(repo)
        assert settings.state_dir == legacy
        assert settings.db_path == legacy / "gitia.db"

    def test_the_new_directory_wins_once_it_exists(self, repo):
        (repo / ".gitia").mkdir()
        (repo / ".gitia" / "gitia.db").write_bytes(b"")
        (repo / ".gitsquid").mkdir()

        assert load_settings(repo).state_dir == repo / ".gitsquid"


class TestRenamedEnvironment:
    def test_the_new_names_are_read(self, monkeypatch, repo):
        monkeypatch.setenv("GITSQUID_MODEL", "claude-sonnet-5")
        assert load_settings(repo).model == "claude-sonnet-5"

    def test_the_old_names_still_work(self, monkeypatch, repo):
        monkeypatch.setenv("GITIA_MODEL", "claude-haiku-4-5-20251001")
        monkeypatch.setenv("GITIA_TEST_COMMAND", "make check")
        settings = load_settings(repo)
        assert settings.model == "claude-haiku-4-5-20251001"
        assert settings.test_command == "make check"

    def test_the_new_name_wins_over_the_old_one(self, monkeypatch, repo):
        monkeypatch.setenv("GITIA_EFFORT", "low")
        monkeypatch.setenv("GITSQUID_EFFORT", "max")
        assert load_settings(repo).effort == "max"

    def test_an_invalid_value_names_the_current_variable(self, monkeypatch, repo):
        monkeypatch.setenv("GITIA_MAX_FILE_BYTES", "beaucoup")
        with pytest.raises(ConfigError, match="GITSQUID_MAX_FILE_BYTES"):
            load_settings(repo)


class TestRenamedRegistry:
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


class TestRenamedExport:
    def test_a_gitia_export_can_still_be_imported(self, conn, tmp_path, settings, service, patch_add_multiply):
        from tests.conftest import StubBackend

        service.propose("Add a multiply helper", StubBackend(patch_add_multiply))
        document = portability.export_payload(conn, repo_name="workshop")
        document["format"] = "gitia-export"
        target = tmp_path / "ancien-export.json"
        target.write_text(json.dumps(document), encoding="utf-8")

        stats = portability.import_from_file(conn, target)
        assert stats.skipped == 1

    def test_a_foreign_document_is_still_refused(self, conn, tmp_path):
        target = tmp_path / "etranger.json"
        target.write_text(json.dumps({"format": "autre-chose"}), encoding="utf-8")
        with pytest.raises(portability.ImportError_, match="gitsquid-export"):
            portability.import_from_file(conn, target)


class TestTheRepositoryTheInterfaceOpens:
    def test_the_last_repository_opened_wins_over_the_current_folder(self, config_home, tmp_path, monkeypatch):
        first = make_repo(tmp_path / "premier")
        last = make_repo(tmp_path / "dernier")
        registry.add(first)
        registry.add(last)
        monkeypatch.chdir(first)

        assert cli._resolve_ui_repo(None).repo == last

    def test_an_explicit_repository_wins_over_the_memory(self, config_home, tmp_path, monkeypatch):
        first = make_repo(tmp_path / "premier")
        registry.add(make_repo(tmp_path / "dernier"))
        assert cli._resolve_ui_repo(first).repo == first

    def test_a_repository_that_moved_away_is_skipped(self, config_home, tmp_path, monkeypatch):
        kept = make_repo(tmp_path / "garde")
        registry.add(kept)
        registry.add(make_repo(tmp_path / "disparu"))
        (tmp_path / "disparu" / ".git").rename(tmp_path / "disparu" / ".git-gone")

        assert cli._resolve_ui_repo(None).repo == kept

    def test_without_any_memory_the_current_folder_is_used(self, config_home, repo, monkeypatch):
        monkeypatch.chdir(repo)
        assert cli._resolve_ui_repo(None).repo == repo
