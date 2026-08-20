from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
def isolated_config(monkeypatch, tmp_path):
    """The repository list a test writes never reaches the one you use."""
    monkeypatch.setenv("GITSQUID_CONFIG_DIR", str(tmp_path / "config"))


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
def repo_with_remote(repo: Path, tmp_path: Path) -> Path:
    """A repository whose `origin` is a bare clone on disk — no network in the tests."""
    git(tmp_path, "init", "--bare", "-q", "origin.git")
    git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


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


@pytest.fixture
def patch_add_multiply(repo: Path) -> str:
    return make_patch(repo, "calc.py", CALC_PY + '\n\ndef multiply(a, b):\n    return a * b\n')
