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
