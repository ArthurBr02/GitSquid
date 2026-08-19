from __future__ import annotations

from gitia import diffs
from tests.conftest import make_patch

GOOD = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
-old
+new
"""


class TestParsing:
    def test_touched_files_from_git_header(self):
        assert diffs.touched_files(GOOD) == ["calc.py"]

    def test_touched_files_from_bare_unified_diff(self):
        bare = "--- a/one.py\n+++ b/two.py\n@@ -1 +1 @@\n-a\n+b\n"
        assert diffs.touched_files(bare) == ["two.py", "one.py"]

    def test_new_file_ignores_dev_null(self):
        created = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
        assert diffs.touched_files(created) == ["new.py"]

    def test_stats_counts_lines_not_headers(self):
        assert diffs.stats(GOOD) == (1, 1)

    def test_normalize_ensures_one_trailing_newline(self):
        assert diffs.normalize("a\r\nb") == "a\nb\n"
        assert diffs.normalize("a\n\n\n") == "a\n"
        assert diffs.normalize("   ") == "   \n"


class TestValidation:
    def test_accepts_a_well_formed_diff(self):
        assert diffs.validate(GOOD) == []

    def test_rejects_empty_patch(self):
        assert diffs.validate("   ") == ["The patch is empty."]

    def test_rejects_prose_without_hunks(self):
        problems = diffs.validate("I would change calc.py like this\n")
        assert any("not a unified diff" in problem for problem in problems)

    def test_rejects_parent_traversal(self):
        patch = GOOD.replace("calc.py", "../../etc/passwd")
        assert any("outside the repository" in problem for problem in diffs.validate(patch))

    def test_rejects_absolute_paths(self):
        patch = GOOD.replace("a/calc.py", "a//etc/passwd").replace("b/calc.py", "b//etc/passwd")
        assert diffs.validate(patch) != []

    def test_rejects_writes_into_git_and_state_dirs(self):
        for target in (".git/config", ".gitia/gitia.db"):
            patch = GOOD.replace("calc.py", target)
            assert any("Refusing path" in problem for problem in diffs.validate(patch))

    def test_rejects_credential_files(self):
        patch = GOOD.replace("calc.py", "deploy/id_rsa")
        assert any("credential file" in problem for problem in diffs.validate(patch))


class TestGitRoundTrip:
    def test_check_then_apply_then_revert(self, repo, patch_add_multiply):
        assert "multiply" not in (repo / "calc.py").read_text()

        assert diffs.check(repo, patch_add_multiply).ok
        assert diffs.apply(repo, patch_add_multiply).ok
        assert "def multiply" in (repo / "calc.py").read_text()

        assert diffs.revert(repo, patch_add_multiply).ok
        assert "multiply" not in (repo / "calc.py").read_text()

    def test_check_does_not_touch_the_tree(self, repo, patch_add_multiply):
        before = (repo / "calc.py").read_text()
        diffs.check(repo, patch_add_multiply)
        assert (repo / "calc.py").read_text() == before

    def test_stale_patch_is_refused_not_fuzzed(self, repo, patch_add_multiply):
        (repo / "calc.py").write_text("completely different content\n", encoding="utf-8")
        result = diffs.check(repo, patch_add_multiply)
        assert not result.ok and result.message

    def test_unsafe_patch_never_reaches_git(self, repo):
        result = diffs.apply(repo, GOOD.replace("calc.py", "../escape.py"))
        assert not result.ok
        assert "outside the repository" in result.message
        assert not (repo.parent / "escape.py").exists()

    def test_current_commit_and_dirty_state(self, repo):
        assert len(diffs.current_commit(repo)) == 40
        assert not diffs.working_tree_dirty(repo)
        (repo / "calc.py").write_text("touched\n", encoding="utf-8")
        assert diffs.working_tree_dirty(repo)

    def test_new_file_patch_applies(self, repo):
        patch = make_patch(repo, "extra.py", "VALUE = 42\n")
        assert diffs.apply(repo, patch).ok
        assert (repo / "extra.py").read_text() == "VALUE = 42\n"
