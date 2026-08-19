"""End-to-end smoke test: the core loop driven through the real CLI, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gitia.cli import app
from tests.conftest import make_patch

runner = CliRunner()


@pytest.fixture(autouse=True)
def local_test_command(monkeypatch):
    monkeypatch.setenv("GITIA_TEST_COMMAND", f"{sys.executable} -m pytest -q")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")


def gitia(repo: Path, *args: str):
    """Invoke the CLI and expose stdout and stderr together as `.text`."""
    result = runner.invoke(app, [*args, "--repo", str(repo)])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    stderr = result.stderr if result.stderr_bytes is not None else ""
    result.text = result.stdout + stderr
    return result


def test_core_loop_end_to_end(repo, tmp_path):
    patch = make_patch(repo, "calc.py", (repo / "calc.py").read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")
    patch_file = tmp_path / "multiply.diff"
    patch_file.write_text(patch, encoding="utf-8")

    # 1. An uninitialised repository says so instead of failing obscurely.
    empty = gitia(repo, "log")
    assert empty.exit_code == 2
    assert "gitia init" in empty.text

    # 2. Init and index.
    assert gitia(repo, "init").exit_code == 0
    assert (repo / ".gitia" / "gitia.db").exists()

    indexed = gitia(repo, "index")
    assert indexed.exit_code == 0
    assert "file(s) indexed" in indexed.text

    # 3. Empty state before any change exists.
    assert "No change recorded yet" in gitia(repo, "log").text

    # 4. Degraded mode is explicit: no key, so a model proposal is refused with an alternative.
    degraded = gitia(repo, "propose", "add a multiply helper")
    assert degraded.exit_code == 2
    assert "--patch-file" in degraded.text

    # 5. Propose from a patch file. Nothing is written to the working tree yet.
    proposed = gitia(repo, "propose", "Add a multiply helper", "--patch-file", str(patch_file))
    assert proposed.exit_code == 0
    assert "applies cleanly" in proposed.text
    assert "multiply" not in (repo / "calc.py").read_text()

    # 6. Apply.
    applied = gitia(repo, "apply", "1", "--yes")
    assert applied.exit_code == 0
    assert "def multiply" in (repo / "calc.py").read_text()

    # 7. Verify: the repository's own tests run and the change becomes verified.
    tested = gitia(repo, "test", "1")
    assert tested.exit_code == 0, tested.text
    assert "verified" in tested.text

    listed = gitia(repo, "log")
    assert "verified" in listed.text and "Add a multiply helper" in listed.text

    shown = gitia(repo, "show", "1")
    assert "Audit trail" in shown.text
    for step in ("proposed", "applied", "tested"):
        assert step in shown.text

    # 8. Export, then import into a second repository.
    export_file = tmp_path / "history.json"
    assert gitia(repo, "export", str(export_file)).exit_code == 0
    payload = json.loads(export_file.read_text())
    assert payload["changes"][0]["status"] == "verified"

    assert "already present" in gitia(repo, "import", str(export_file)).text

    # 9. Revert puts the file back.
    reverted = gitia(repo, "revert", "1", "--yes")
    assert reverted.exit_code == 0
    assert "multiply" not in (repo / "calc.py").read_text()
    assert "reverted" in gitia(repo, "log").text


def test_failing_change_is_recorded_and_revertible(repo, tmp_path, patch_breaks_tests):
    patch_file = tmp_path / "break.diff"
    patch_file.write_text(patch_breaks_tests, encoding="utf-8")

    gitia(repo, "init")
    gitia(repo, "index")
    result = gitia(
        repo, "run", "Break addition on purpose", "--patch-file", str(patch_file),
        "--yes", "--revert-on-failure",
    )

    assert result.exit_code == 1
    assert "reversed automatically" in result.text
    assert "return a + b" in (repo / "calc.py").read_text()
    assert "reverted" in gitia(repo, "log").text


def test_sample_data_loads_and_clears(repo):
    gitia(repo, "init")
    loaded = gitia(repo, "sample", "load")
    assert loaded.exit_code == 0

    listing = gitia(repo, "log")
    assert "SAMPLE" in listing.text

    blocked = gitia(repo, "apply", "1", "--yes")
    assert blocked.exit_code == 2
    assert "sample data" in blocked.text

    cleared = gitia(repo, "sample", "clear")
    assert cleared.exit_code == 0
    assert "No change recorded yet" in gitia(repo, "log").text


def test_doctor_reports_configuration_and_degraded_mode(repo):
    gitia(repo, "init")
    result = gitia(repo, "doctor")
    assert result.exit_code == 0
    assert str(repo / ".gitia" / "gitia.db") in result.text.replace("\n", "")
    assert "Degraded mode" in result.text


def test_unknown_change_id_is_a_clean_error(repo):
    gitia(repo, "init")
    result = gitia(repo, "show", "999")
    assert result.exit_code == 2
    assert "No change #999" in result.text


def test_the_interface_falls_back_to_the_last_repository(repo, tmp_path, monkeypatch):
    """A desktop launcher has no useful working directory — the registry must answer."""
    from gitia.cli import _resolve_ui_repo
    from gitia import registry

    monkeypatch.setenv("GITIA_CONFIG_DIR", str(tmp_path / "config"))
    registry.add(repo)

    elsewhere = tmp_path / "nowhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    settings = _resolve_ui_repo(None)
    assert settings.repo == repo


def test_the_interface_refuses_when_nothing_is_registered(tmp_path, monkeypatch):
    import typer

    from gitia.cli import _resolve_ui_repo

    monkeypatch.setenv("GITIA_CONFIG_DIR", str(tmp_path / "empty"))
    elsewhere = tmp_path / "nowhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    with pytest.raises(typer.Exit):
        _resolve_ui_repo(None)
