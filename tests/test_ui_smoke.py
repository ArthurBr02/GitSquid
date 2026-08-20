"""The page, in a real browser: the only test that proves the scripts, the CSS and the API
agree. Needs `pip install playwright` and `python -m playwright install webkit`; skips itself
without them."""

from __future__ import annotations

import threading

import pytest

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
def running(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("GITSQUID_CONFIG_DIR", str(tmp_path / "config"))
    (repo / "extra.py").write_text("VALEUR = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "un second commit")
    (repo / "calc.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")

    server = UIServer(repo, port=0)
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

    def test_every_graph_node_stays_centred_on_its_commit_row(self, page):
        geometry = page.evaluate("""() => {
          const canvas = document.querySelector("#graph-canvas");
          const top = canvas.getBoundingClientRect().top;
          const commitCentres = [...document.querySelectorAll("#rows li:not(.wip)")]
            .map((row) => {
              const box = row.getBoundingClientRect();
              return box.top + box.height / 2 - top;
            });
          // the filled circles are the commits; the hollow ones are the merge, HEAD and
          // selection rings drawn around them
          const nodeCentres = [...new Set(
            [...canvas.querySelectorAll(":scope > circle")]
              .filter((circle) => circle.getAttribute("fill") !== "none")
              .map((circle) => Number(circle.getAttribute("cy")))
          )];
          const rowsHeight = [...document.querySelectorAll("#rows li")]
            .reduce((height, row) => height + row.getBoundingClientRect().height, 0);
          return {
            commitCentres,
            nodeCentres,
            rowsHeight,
            canvasHeight: Number(canvas.getAttribute("height")),
          };
        }""")

        assert geometry["nodeCentres"] == geometry["commitCentres"]
        assert geometry["canvasHeight"] == geometry["rowsHeight"]

    def test_the_scope_can_be_narrowed_to_this_branch(self, page):
        page.click("#btn-filter")
        page.wait_for_selector(".context-menu")
        page.locator(".menu-item", has_text="This branch only").click()
        page.wait_for_timeout(600)
        assert "This branch" in page.inner_text("#btn-filter")
        assert page.locator("#rows li").count() >= 2


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


class TestMovingHead:
    def test_a_double_click_offers_every_way_to_get_there(self, page):
        page.locator("#rows li", has_text="initial").first.dblclick()
        page.wait_for_selector("#choose-modal[open]")
        labels = page.locator(".choice-label").all_inner_texts()
        assert "Check out this commit" in labels
        assert any("Reset main here" in label for label in labels)
        assert any("Throw the work away" in label for label in labels)
        page.click("#choose-cancel")

    def test_choosing_a_reset_moves_the_branch(self, page, running):
        page.locator("#rows li", has_text="initial").first.dblclick()
        page.wait_for_selector("#choose-modal[open]")
        page.locator(".choice", has_text="Keep everything, unstaged").click()
        page.wait_for_selector(".toast")
        page.wait_for_timeout(600)

        assert "now points at" in page.inner_text(".toast")
        assert "initial" in page.inner_text("#rows li.head"), "HEAD moved to the older commit"
        # nothing points at the commit that was reset away, so git no longer lists it
        assert page.locator("#rows li .row-title").all_inner_texts().count("un second commit") == 0

    def test_the_commit_you_are_on_says_so(self, page):
        page.locator("#rows li.head").first.dblclick()
        page.wait_for_timeout(400)
        assert page.locator("#choose-modal[open]").count() == 0
        assert "already on this commit" in page.inner_text(".toast")


class TestTheWorkingTree:
    def test_a_file_can_be_staged_from_the_panel(self, page):
        page.click("#wip-row")
        page.wait_for_selector(".file-row")
        page.locator(".file-row", has_text="calc.py").first.locator(".stage-btn").click()
        page.wait_for_selector(".toast")
        page.wait_for_timeout(400)
        assert "1 staged" in page.inner_text("#wip-row")

    def test_one_line_can_be_picked_and_staged(self, page):
        page.click("#wip-row")
        page.wait_for_selector(".file-row")
        page.locator(".file-row", has_text="calc.py").first.click()
        page.wait_for_selector("#viewer:not([hidden]) .diff")

        page.locator(".diff .add .ln.pickable").first.click()
        page.wait_for_selector(".pick-bar")
        assert "1 line picked" in page.inner_text(".pick-bar")
        page.locator(".pick-bar button", has_text="Stage them").click()
        page.wait_for_selector(".toast")
        page.wait_for_timeout(500)
        assert "Staged 1 line" in page.inner_text(".toast")

    def test_a_hunk_carries_its_own_actions(self, page):
        page.click("#wip-row")
        page.wait_for_selector(".file-row")
        page.locator(".file-row", has_text="calc.py").first.click()
        page.wait_for_selector("#viewer:not([hidden]) .diff")
        assert page.locator(".hunk-bar button", has_text="Stage hunk").count() == 1
