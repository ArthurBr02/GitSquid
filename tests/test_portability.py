from __future__ import annotations

import json

import pytest

from gitia import portability
from gitia.indexer import index_repo
from gitia.models import ChangeRepo, ChangeStatus
from tests.conftest import StubBackend


@pytest.fixture
def populated(service, conn, settings, patch_add_multiply):
    index_repo(conn, settings.repo, max_file_bytes=settings.max_file_bytes)
    outcome = service.propose("Add a multiply helper", StubBackend(patch_add_multiply))
    service.apply(outcome.change)
    service.verify(outcome.change, command="python -c 'print(1)'")
    return service


class TestExport:
    def test_writes_a_versioned_payload(self, populated, conn, tmp_path):
        target = tmp_path / "out" / "export.json"
        assert portability.export_to_file(conn, target, repo_name="workshop") == 1

        payload = json.loads(target.read_text())
        assert payload["format"] == "gitia-export"
        assert payload["version"] == 1
        assert payload["repo"] == "workshop"

        change = payload["changes"][0]
        assert change["task"] == "Add a multiply helper"
        assert change["status"] == "verified"
        assert change["files_touched"] == ["calc.py"]
        assert change["test_runs"][0]["exit_code"] == 0
        assert [event["kind"] for event in change["events"]] == ["proposed", "applied", "tested"]

    def test_empty_database_exports_an_empty_list(self, conn, tmp_path):
        target = tmp_path / "empty.json"
        assert portability.export_to_file(conn, target, repo_name="workshop") == 0
        assert json.loads(target.read_text())["changes"] == []


class TestImport:
    def test_round_trip_into_a_fresh_database(self, populated, conn, tmp_path, settings):
        target = tmp_path / "export.json"
        portability.export_to_file(conn, target, repo_name="workshop")

        from gitia.db import open_db

        other = open_db(tmp_path / "other.db")
        stats = portability.import_from_file(other, target)

        assert stats.changes == 1 and stats.skipped == 0
        imported = ChangeRepo(other).latest()
        assert imported.task == "Add a multiply helper"
        assert imported.status is ChangeStatus.VERIFIED
        assert imported.files_touched == ["calc.py"]
        other.close()

    def test_importing_twice_skips_duplicates(self, populated, conn, tmp_path):
        target = tmp_path / "export.json"
        portability.export_to_file(conn, target, repo_name="workshop")

        first = portability.import_from_file(conn, target)
        second = portability.import_from_file(conn, target)
        assert (first.changes, first.skipped) == (0, 1)
        assert (second.changes, second.skipped) == (0, 1)
        assert ChangeRepo(conn).count() == 1

    def test_rejects_a_file_that_is_not_an_export(self, conn, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"format": "something-else"}))
        with pytest.raises(portability.ImportError_, match="not a gitia-export"):
            portability.import_from_file(conn, bad)

    def test_rejects_invalid_json(self, conn, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        with pytest.raises(portability.ImportError_, match="not valid UTF-8 JSON"):
            portability.import_from_file(conn, bad)

    def test_rejects_a_future_version(self, conn, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"format": "gitia-export", "version": 99, "changes": []}))
        with pytest.raises(portability.ImportError_, match="Unsupported export version"):
            portability.import_from_file(conn, bad)

    def test_rejects_a_change_with_the_wrong_types(self, conn, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "format": "gitia-export",
                    "version": 1,
                    "changes": [{"task": 42, "diff": "x", "created_at": "now"}],
                }
            )
        )
        with pytest.raises(portability.ImportError_, match="'task' must be str"):
            portability.import_from_file(conn, bad)

    def test_rejects_a_missing_file(self, conn, tmp_path):
        with pytest.raises(portability.ImportError_, match="No such file"):
            portability.import_from_file(conn, tmp_path / "nope.json")

    def test_imported_diffs_are_still_validated_for_path_safety(self, conn, tmp_path):
        payload = {
            "format": "gitia-export",
            "version": 1,
            "changes": [
                {
                    "task": "hostile import",
                    "diff": "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        target = tmp_path / "hostile.json"
        target.write_text(json.dumps(payload))
        portability.import_from_file(conn, target)

        from gitia import diffs

        imported = ChangeRepo(conn).latest()
        assert diffs.validate(imported.diff) != []
