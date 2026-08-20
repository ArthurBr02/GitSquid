"""The page splits patches itself: what it reads out of a diff is a contract worth testing."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).parent / "diff_parse_runner.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to run the browser code"
)

TWO_FILES = """diff --git a/calc.py b/calc.py
index 1111111..2222222 100644
--- a/calc.py
+++ b/calc.py
@@ -1,3 +1,3 @@
 def add(a, b):
-    return a + b
+    return a - b
@@ -10,2 +10,3 @@ def total(values):
     result = 0
+    seen = set()
diff --git a/neuf.py b/neuf.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/neuf.py
@@ -0,0 +1,2 @@
+VALEUR = 1
+AUTRE = 2
"""


def run(request: dict) -> dict:
    result = subprocess.run(
        ["node", str(RUNNER)], input=json.dumps(request),
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def parse(diff: str) -> list[dict]:
    return run({"op": "parse", "diff": diff})["files"]


class TestParsing:
    def test_a_patch_splits_into_files_and_hunks(self):
        files = parse(TWO_FILES)
        assert [file["path"] for file in files] == ["calc.py", "neuf.py"]
        assert [len(file["hunks"]) for file in files] == [2, 1]

    def test_a_file_keeps_the_header_a_hunk_needs_to_apply_on_its_own(self):
        header = parse(TWO_FILES)[0]["header"]
        assert header[0].startswith("diff --git")
        assert any(line.startswith("--- ") for line in header)
        assert any(line.startswith("+++ ") for line in header)

    def test_a_new_file_reads_as_an_addition(self):
        assert [file["status"] for file in parse(TWO_FILES)] == ["M", "A"]

    def test_a_deleted_file_reads_as_a_deletion(self):
        diff = ("diff --git a/vieux.py b/vieux.py\ndeleted file mode 100644\n"
                "--- a/vieux.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-VALEUR = 1\n")
        assert parse(diff)[0]["status"] == "D"

    def test_a_patch_without_a_git_header_still_reads(self):
        diff = "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-un\n+deux\n"
        files = parse(diff)
        assert len(files) == 1 and len(files[0]["hunks"]) == 1
        assert files[0]["path"] == "calc.py"

    def test_an_empty_patch_yields_nothing(self):
        assert parse("") == []


class TestInlineMarks:
    def cut(self, before: str, after: str):
        return run({"op": "inline", "before": before, "after": after})["cut"]

    def test_a_small_edit_is_located_between_the_common_ends(self):
        head, tail = self.cut("    return a + b", "    return a - b")
        assert "    return a ".startswith("    return a"[:head])
        assert tail > 0

    def test_two_identical_lines_have_nothing_to_mark(self):
        assert self.cut("same", "same") is None

    def test_a_line_rewritten_end_to_end_is_not_marked(self):
        assert self.cut("alpha beta gamma", "delta epsilon zeta") is None


class TestSharedFolder:
    def root(self, *directories: str) -> str:
        return run({"op": "root", "directories": list(directories)})["root"]

    def test_a_deep_shared_folder_is_named_once(self):
        assert self.root(
            "src/main/java/com/example/app/fil/",
            "src/main/java/com/example/app/mail/",
        ) == "src/main/java/com/example/app/"

    def test_folders_that_part_early_share_nothing_worth_saying(self):
        assert self.root("src/main/java/", "tests/") == ""

    def test_one_folder_alone_needs_no_heading(self):
        assert self.root("src/main/java/com/example/") == ""

    def test_a_shallow_shared_folder_is_not_worth_a_line(self):
        assert self.root("src/one/", "src/two/") == ""


ONE_FILE = """diff --git a/poeme.txt b/poeme.txt
index 1111111..2222222 100644
--- a/poeme.txt
+++ b/poeme.txt
@@ -1,5 +1,6 @@
 ligne 1
-ligne 2
+ligne deux modifiee
 ligne 3
 ligne 4
+ligne quatre et demie
 ligne 5
@@ -9,2 +10,2 @@ context
 ligne 9
-ligne 10
+ligne dix modifiee
"""


class TestPickingLines:
    """Positions count from the first line after the @@ header, starting at zero."""

    REMOVAL = "0:1"          # -ligne 2
    ITS_REPLACEMENT = "0:2"  # +ligne deux modifiee
    LONE_ADDITION = "0:5"    # +ligne quatre et demie
    SECOND_HUNK_REMOVAL = "1:1"

    def patch(self, *picks: str) -> str:
        return run({"op": "lines", "diff": ONE_FILE, "picks": list(picks)})["patch"]

    def headers(self, patch: str) -> list[str]:
        return [line for line in patch.splitlines() if line.startswith("@@")]

    def test_picking_nothing_produces_nothing(self):
        assert self.patch() == ""

    def test_one_added_line_is_the_only_change_left(self):
        body = self.patch(self.LONE_ADDITION).splitlines()
        assert "+ligne quatre et demie" in body
        assert "+ligne deux modifiee" not in body
        # the removal nobody picked stays, as context
        assert " ligne 2" in body

    def test_a_picked_removal_stays_a_removal(self):
        body = self.patch(self.REMOVAL).splitlines()
        assert "-ligne 2" in body
        assert "+ligne deux modifiee" not in body

    def test_the_header_counts_what_the_hunk_now_holds(self):
        # one line added to five: the hunk holds five before and six after
        assert self.headers(self.patch(self.LONE_ADDITION)) == ["@@ -1,5 +1,6 @@"]
        # one line removed from five, nothing added
        assert self.headers(self.patch(self.REMOVAL)) == ["@@ -1,5 +1,4 @@"]

    def test_a_hunk_with_nothing_picked_is_dropped(self):
        patch = self.patch(self.SECOND_HUNK_REMOVAL)
        assert len(self.headers(patch)) == 1
        assert "ligne dix modifiee" not in patch

    def test_picks_in_both_hunks_keep_both(self):
        assert len(self.headers(self.patch(self.REMOVAL, self.SECOND_HUNK_REMOVAL))) == 2

    def test_a_later_hunk_starts_where_the_earlier_picks_left_the_file(self):
        # the first hunk gains a line, so the second one starts one line further down
        headers = self.headers(self.patch(self.LONE_ADDITION, self.SECOND_HUNK_REMOVAL))
        assert headers == ["@@ -1,5 +1,6 @@", "@@ -9,2 +10,1 @@ context"]

    def test_the_file_header_is_carried_so_git_can_apply_it(self):
        patch = self.patch(self.LONE_ADDITION)
        assert patch.startswith("diff --git a/poeme.txt b/poeme.txt")
        assert "--- a/poeme.txt" in patch and "+++ b/poeme.txt" in patch
        assert patch.endswith("\n")


class TestGitAcceptsWhatThePagePicks:
    """The construction is only correct if git applies it: build in node, apply for real."""

    def picked_patch(self, diff: str, picks: list[str]) -> str:
        return run({"op": "lines", "diff": diff, "picks": picks})["patch"]

    def key_for(self, diff: str, text: str) -> str:
        """The page names a line by its hunk and its place in it; find that for a given line."""
        hunk = -1
        position = 0
        for line in diff.splitlines():
            if line.startswith("@@"):
                hunk += 1
                position = 0
            elif hunk >= 0:
                if line == text:
                    return f"{hunk}:{position}"
                position += 1
        raise AssertionError(f"no such line in the diff: {text!r}")

    @pytest.fixture
    def poem(self, repo):
        from gitsquid import worktree

        lines = [f"ligne {number}\n" for number in range(1, 31)]
        (repo / "poeme.txt").write_text("".join(lines), encoding="utf-8")
        worktree.stage(repo, ["poeme.txt"])
        worktree.commit(repo, "le poeme")

        lines[1] = "ligne deux modifiee\n"     # first hunk
        lines[24] = "ligne vingt-cinq modifiee\n"  # far enough away to be a second hunk
        (repo / "poeme.txt").write_text("".join(lines), encoding="utf-8")
        return repo

    def test_one_picked_line_is_the_only_thing_staged(self, poem):
        from gitsquid import worktree

        diff = worktree.file_diff(poem, "poeme.txt", staged=False)
        assert len([line for line in diff.splitlines() if line.startswith("@@")]) == 2

        picked = self.key_for(diff, "+ligne deux modifiee")
        worktree.apply_patch(poem, self.picked_patch(diff, [picked]), target="stage")

        staged = worktree.file_diff(poem, "poeme.txt", staged=True)
        assert "+ligne deux modifiee" in staged
        assert "vingt-cinq" not in staged

    def test_a_picked_line_can_be_discarded_from_the_working_tree(self, poem):
        from gitsquid import worktree

        diff = worktree.file_diff(poem, "poeme.txt", staged=False)
        # the removal and the line that replaced it, together: that edit goes away
        picked = [self.key_for(diff, "-ligne 2"), self.key_for(diff, "+ligne deux modifiee")]
        worktree.apply_patch(poem, self.picked_patch(diff, picked), target="discard")

        text = (poem / "poeme.txt").read_text()
        assert "ligne deux modifiee" not in text
        assert "ligne 2\n" in text
        assert "ligne vingt-cinq modifiee" in text, "the other edit must survive"

    def test_picks_in_two_hunks_land_in_one_patch_git_accepts(self, poem):
        from gitsquid import worktree

        diff = worktree.file_diff(poem, "poeme.txt", staged=False)
        picked = [self.key_for(diff, "+ligne deux modifiee"),
                  self.key_for(diff, "+ligne vingt-cinq modifiee")]
        worktree.apply_patch(poem, self.picked_patch(diff, picked), target="stage")

        staged = worktree.file_diff(poem, "poeme.txt", staged=True)
        assert "+ligne deux modifiee" in staged
        assert "+ligne vingt-cinq modifiee" in staged
