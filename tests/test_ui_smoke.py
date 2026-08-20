"""The page, in a real browser. Skipped when Playwright or its browser is not installed.

This is the only test that proves the scripts, the CSS and the API agree with each other:
everything else stops at the JSON. Install it with `pip install playwright` and
`playwright install webkit`.
"""

from __future__ import annotations

import sys
import threading

import pytest

from gitsquid.db import open_db
from gitsquid.indexer import index_repo
from gitsquid.web import UIServer
from tests.conftest import git

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as driver:
        try:
            instance = driver.webkit.launch()
        except Exception as exc:  # the browser binary is a separate download
            pytest.skip(f"no webkit browser available: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def running(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("GITSQUID_TEST_COMMAND", f"{sys.executable} -m pytest -q")
    monkeypatch.setenv("GITSQUID_CONFIG_DIR", str(tmp_path / "config"))
    repo = settings.repo
    (repo / "extra.py").write_text("VALEUR = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "un second commit")
    (repo / "calc.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")

    conn = open_db(settings.db_path)
    index_repo(conn, repo, max_file_bytes=settings.max_file_bytes)
    conn.close()

    server = UIServer(settings, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def page(browser, running):
    problems: list[str] = []
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    sheet = context.new_page()
    sheet.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    sheet.on("console", lambda message: problems.append(f"console: {message.text}")
             if message.type == "error" and "stylesheet" not in message.text else None)
    sheet.goto(running.url, wait_until="networkidle")
    sheet.wait_for_selector("#rows li")
    yield sheet
    assert problems == [], f"the page reported: {problems}"
    context.close()


class TestTheGraph:
    def test_the_rows_and_the_sidebar_render(self, page):
        assert page.locator("#rows li").count() >= 3  # two commits and the working tree
        assert page.locator(".section").count() == 5
        assert "workshop" in page.inner_text("#repo-chip")

    def test_a_filter_narrows_the_list(self, page):
        page.click("#btn-filter")
        page.wait_for_selector(".context-menu")
        page.locator(".menu-item", has_text="Commits").click()
        page.wait_for_timeout(200)
        assert "2 of" in page.inner_text("#row-count")


class TestACommit:
    def test_its_files_are_listed_and_open_in_the_middle(self, page):
        page.locator("#rows li", has_text="un second commit").first.click()
        page.wait_for_selector(".files-section .file-row")
        assert page.locator(".files-section .file-row").count() == 1

        page.locator(".files-section .file-row").first.click()
        page.wait_for_selector("#viewer:not([hidden]) .diff")
        assert page.locator("#rows-wrap").is_hidden()
        assert "extra.py" in page.inner_text("#viewer-head")

        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert page.locator("#rows-wrap").is_visible()

    def test_a_right_click_offers_the_history_actions(self, page):
        page.locator("#rows li", has_text="un second commit").first.click(button="right")
        page.wait_for_selector(".context-menu")
        labels = [text.split("\n")[0] for text in page.locator(".context-menu .menu-item").all_inner_texts()]
        assert "Cherry-pick onto main" in labels
        assert "Revert this commit" in labels


class TestTheWorkingTree:
    def test_a_file_can_be_staged_from_the_panel(self, page):
        page.click("#wip-row")
        page.wait_for_selector(".file-row")
        page.locator(".file-row", has_text="calc.py").first.locator(".stage-btn").click()
        page.wait_for_selector(".toast")
        page.wait_for_timeout(400)
        assert "1 staged" in page.inner_text("#wip-row")

    def test_a_hunk_carries_its_own_actions(self, page):
        page.click("#wip-row")
        page.wait_for_selector(".file-row")
        page.locator(".file-row", has_text="calc.py").first.click()
        page.wait_for_selector("#viewer:not([hidden]) .diff")
        assert page.locator(".hunk-bar button", has_text="Stage hunk").count() == 1
