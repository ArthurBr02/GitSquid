from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitia.config import Settings
from gitia.db import open_db
from gitia.llm import Proposal, ProposalRequest
from gitia.models import ChangeSource
from gitia.workflow import ChangeService

CALC_PY = '''"""Tiny module the tests patch."""


def add(a, b):
    return a + b


def total(values):
    result = 0
    for value in values:
        result = add(result, value)
    return result
'''

TEST_CALC_PY = """from calc import add, total


def test_add():
    assert add(2, 3) == 5


def test_total():
    assert total([1, 2, 3]) == 6
"""


def git(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, check=False
    )


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No .env file and no ambient API key ever reaches the tests."""
    monkeypatch.setattr("gitia.config.load_dotenv", lambda *a, **k: False)
    for name in (
        "ANTHROPIC_API_KEY",
        "GITIA_MODEL",
        "GITIA_EFFORT",
        "GITIA_MAX_CONTEXT_CHARS",
        "GITIA_MAX_FILE_BYTES",
        "GITIA_TEST_COMMAND",
        "GITIA_TEST_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "workshop"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "tester@example.invalid")
    git(root, "config", "user.name", "Tester")
    (root / "calc.py").write_text(CALC_PY, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST_CALC_PY, encoding="utf-8")
    (root / "README.md").write_text("# workshop\n\nA repository used by the tests.\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def settings(repo: Path) -> Settings:
    state = repo / ".gitia"
    return Settings(
        repo=repo,
        state_dir=state,
        db_path=state / "gitia.db",
        model="claude-opus-5",
        effort="high",
        max_context_chars=20_000,
        max_file_bytes=200_000,
        test_command="python -m pytest -q",
        test_timeout=120,
        api_key=None,
    )


@pytest.fixture
def conn(settings: Settings):
    connection = open_db(settings.db_path)
    yield connection
    connection.close()


@pytest.fixture
def service(conn, settings: Settings) -> ChangeService:
    return ChangeService(conn, settings)


def make_patch(repo: Path, rel: str, new_text: str) -> str:
    """Produce a real `git diff` for a proposed content change, leaving the tree untouched."""
    target = repo / rel
    existed = target.exists()
    original = target.read_text(encoding="utf-8") if existed else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_text, encoding="utf-8")
    if not existed:
        git(repo, "add", "-N", rel)
    diff = git(repo, "diff", "--no-color", "--", rel).stdout
    if existed:
        target.write_text(original, encoding="utf-8")
    else:
        git(repo, "rm", "-q", "--cached", rel)
        target.unlink()
    return diff


class StubBackend:
    """A ProposalBackend that returns a canned diff — the loop under test, no network."""

    name = "stub"

    def __init__(self, diff: str, rationale: str = "Because the tests say so.") -> None:
        self.diff = diff
        self.rationale = rationale
        self.requests: list[ProposalRequest] = []

    def propose(self, request: ProposalRequest) -> Proposal:
        self.requests.append(request)
        return Proposal(
            diff=self.diff,
            rationale=self.rationale,
            source=ChangeSource.MODEL,
            model="stub-model",
            input_tokens=1234,
            output_tokens=56,
        )


@pytest.fixture
def patch_add_multiply(repo: Path) -> str:
    return make_patch(repo, "calc.py", CALC_PY + '\n\ndef multiply(a, b):\n    return a * b\n')


@pytest.fixture
def patch_breaks_tests(repo: Path) -> str:
    return make_patch(repo, "calc.py", CALC_PY.replace("return a + b", "return a - b"))


@pytest.fixture
def repo_with_remote(repo: Path, tmp_path: Path) -> Path:
    """A repository whose `origin` is a bare clone on disk — no network in the tests."""
    git(tmp_path, "init", "--bare", "-q", "origin.git")
    git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo
