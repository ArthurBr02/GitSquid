"""What git reports for the graph. The lane layout itself lives in tests/test_graph_layout.py."""

from __future__ import annotations

import pytest

from gitsquid import gitlog
from tests.conftest import git


def commit_file(repo, name, text, message):
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def branchy(repo):
    """One commit that is the parent of three branches, then three merges back."""
    for index in range(3):
        git(repo, "checkout", "-q", "-b", f"feature/{index}", "main")
        commit_file(repo, f"feature_{index}.py", f"VALUE = {index}\n", f"work {index}")
    git(repo, "checkout", "-q", "main")
    for index in range(3):
        git(repo, "merge", "-q", "--no-ff", f"feature/{index}", "-m", f"merge {index}")
    return repo


class TestRefsAndLimits:
    def test_head_and_branches_are_reported(self, branchy):
        head = gitlog.commits(branchy)[0]
        assert any("HEAD" in ref for ref in head.refs)

    def test_the_limit_is_honoured(self, repo):
        for index in range(6):
            commit_file(repo, "counter.py", f"N = {index}\n", f"step {index}")
        assert len(gitlog.commits(repo, limit=3)) == 3

    def test_a_repository_without_commits_yields_nothing(self, tmp_path):
        empty = tmp_path / "vide"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        assert gitlog.commits(empty) == []


class TestWhatTheGraphShows:
    def test_work_on_another_branch_is_newer_not_invisible(self, repo):
        git(repo, "checkout", "-q", "-b", "ailleurs")
        commit_file(repo, "ailleurs.py", "A = 1\n", "travail ailleurs")
        git(repo, "checkout", "-q", "main")

        subjects = [commit.subject for commit in gitlog.commits(repo)]
        assert subjects[0] == "travail ailleurs", "the newest commit leads, whatever branch it is on"

    def test_a_parent_never_sits_above_its_child(self, branchy):
        commits = gitlog.commits(branchy)
        position = {commit.sha: index for index, commit in enumerate(commits)}
        for commit in commits:
            for parent in commit.parents:
                if parent in position:
                    assert position[parent] > position[commit.sha]

    def test_the_count_covers_every_ref(self, repo):
        git(repo, "checkout", "-q", "-b", "ailleurs")
        commit_file(repo, "ailleurs.py", "A = 1\n", "travail ailleurs")
        git(repo, "checkout", "-q", "main")

        assert gitlog.count_commits(repo) == 2

    def test_head_carries_the_commit_it_is_on(self, repo):
        current = git(repo, "rev-parse", "HEAD").stdout.strip()
        assert gitlog.head(repo)["commit"] == current


class TestParents:
    """The layout is computed in the browser, so the parent list is the whole contract."""

    def test_parents_are_reported_in_order(self, branchy):
        merges = [commit for commit in gitlog.commits(branchy) if len(commit.parents) > 1]
        assert merges
        for merge in merges:
            assert all(len(parent) == 40 for parent in merge.parents)

    def test_every_parent_inside_the_window_is_a_known_commit(self, branchy):
        commits = gitlog.commits(branchy)
        known = {commit.sha for commit in commits}
        reachable = {parent for commit in commits for parent in commit.parents}
        assert reachable <= known, "a parent points outside a window that covers the whole history"

    def test_the_root_commit_has_no_parent(self, branchy):
        assert gitlog.commits(branchy)[-1].parents == []


class TestFileHistory:
    def test_only_the_commits_that_touched_the_file_are_listed(self, repo):
        commit_file(repo, "calc.py", "VALEUR = 1\n", "change calc")
        commit_file(repo, "autre.py", "A = 1\n", "add autre")

        history = gitlog.file_history(repo, "calc.py")
        assert [entry["subject"] for entry in history] == ["change calc", "initial"]
        assert history[0]["short"] == history[0]["sha"][:7]

    def test_a_file_git_never_saw_has_no_history(self, repo):
        assert gitlog.file_history(repo, "jamais-vu.py") == []


class TestCommitDetail:
    def test_the_payload_stands_on_its_own(self, repo):
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        detail = gitlog.commit_detail(repo, sha)
        assert detail["subject"] == "initial"
        assert detail["author"] == "Tester"
        assert detail["short"] == sha[:7]
        assert detail["date"].startswith("20")

    def test_a_commit_without_any_ref_still_loads(self, repo):
        """The separator counts as whitespace in Python: stripping a record loses a field."""
        commit_file(repo, "suite.py", "X = 1\n", "sans ref")
        older = git(repo, "rev-parse", "HEAD~1").stdout.strip()

        detail = gitlog.commit_detail(repo, older)
        assert detail["refs"] == []
        assert detail["subject"] == "initial"
        assert detail["files"]

    def test_a_merge_shows_what_it_brought_in(self, repo):
        base = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "checkout", "-q", "-b", "cote", base)
        commit_file(repo, "apporte.py", "A = 1\n", "travail de cote")
        git(repo, "checkout", "-q", "main")
        commit_file(repo, "principal.py", "M = 1\n", "travail principal")
        git(repo, "merge", "-q", "--no-ff", "cote", "-m", "fusion")

        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        detail = gitlog.commit_detail(repo, head)
        assert detail["merge"] is True
        assert [file["path"] for file in detail["files"]] == ["apporte.py"]
        assert "apporte.py" in gitlog.commit_patch(repo, head)["diff"]

    def test_each_file_carries_the_lines_it_gained_and_lost(self, repo):
        (repo / "calc.py").write_text("VALEUR = 1\nVALEUR2 = 2\n", encoding="utf-8")
        (repo / "neuf.py").write_text("N = 1\n", encoding="utf-8")
        (repo / "README.md").unlink()
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "trois sortes de changement")

        detail = gitlog.commit_detail(repo, git(repo, "rev-parse", "HEAD").stdout.strip())
        by_path = {file["path"]: file for file in detail["files"]}
        assert by_path["neuf.py"]["status"] == "A" and by_path["neuf.py"]["added"] == 1
        assert by_path["README.md"]["status"] == "D" and by_path["README.md"]["added"] == 0
        assert by_path["calc.py"]["status"] == "M"
        assert detail["added"] == sum(file["added"] for file in detail["files"])

    def test_a_binary_file_has_no_line_count(self, repo):
        (repo / "image.bin").write_bytes(bytes(range(256)))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "ajoute un binaire")

        detail = gitlog.commit_detail(repo, git(repo, "rev-parse", "HEAD").stdout.strip())
        entry = detail["files"][0]
        assert entry["binary"] is True and entry["added"] is None

    def test_a_patch_can_be_fetched_for_one_file_alone(self, repo):
        (repo / "calc.py").write_text("VALEUR = 1\n", encoding="utf-8")
        (repo / "autre.py").write_text("A = 1\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "deux fichiers")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()

        patch = gitlog.commit_patch(repo, head, "calc.py")
        assert "calc.py" in patch["diff"] and "autre.py" not in patch["diff"]
        assert patch["truncated"] is False

    def test_a_patch_path_outside_the_repository_is_refused(self, repo):
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(ValueError, match="outside the repository"):
            gitlog.commit_patch(repo, head, "../../etc/passwd")

    def test_an_unknown_commit_is_reported(self, repo):
        with pytest.raises(ValueError, match="No such commit"):
            gitlog.commit_detail(repo, "0" * 40)


class TestSearch:
    @pytest.fixture
    def history(self, repo):
        commit_file(repo, "facture.py", "TOTAL = 1\n", "Corrige le calcul de facture")
        commit_file(repo, "autre.py", "A = 1\n", "Ajoute autre chose")
        git(repo, "commit", "-q", "--allow-empty", "-m", "Note de version",
            "--author", "Camille <camille@example.invalid>")
        return repo

    def test_a_message_is_found(self, history):
        assert [commit.subject for commit in gitlog.search(history, "facture")] == [
            "Corrige le calcul de facture"
        ]

    def test_the_search_ignores_case(self, history):
        assert gitlog.search(history, "FACTURE")

    def test_an_author_is_found(self, history):
        assert [commit.subject for commit in gitlog.search(history, "camille")] == ["Note de version"]

    def test_a_path_is_found_even_when_the_message_says_nothing(self, history):
        assert [commit.subject for commit in gitlog.search(history, "facture.py")] == [
            "Corrige le calcul de facture"
        ]

    def test_results_come_back_newest_first_and_without_duplicates(self, history):
        found = gitlog.search(history, "a")
        assert len(found) == len({commit.sha for commit in found})
        assert found == sorted(found, key=lambda commit: commit.date, reverse=True)

    def test_a_query_too_short_to_mean_anything_is_refused(self, history):
        assert gitlog.search(history, "a" * 1) == []
        assert gitlog.search(history, "  ") == []

    def test_nothing_matches_nothing(self, history):
        assert gitlog.search(history, "introuvable-xyz") == []


class TestBlame:
    def test_every_line_names_who_last_touched_it(self, repo):
        commit_file(repo, "poeme.txt", "un\ndeux\n", "premier jet")
        (repo / "poeme.txt").write_text("un\ndeux modifie\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "retouche")

        lines = gitlog.blame(repo, "poeme.txt")["lines"]
        assert [line["text"] for line in lines] == ["un", "deux modifie"]
        assert lines[0]["summary"] == "premier jet"
        assert lines[1]["summary"] == "retouche"
        assert lines[0]["sha"] != lines[1]["sha"]
        assert lines[0]["author"] == "Tester"
        assert lines[0]["date"].startswith("20")

    def test_blame_can_be_asked_at_an_older_commit(self, repo):
        commit_file(repo, "poeme.txt", "un\n", "premier jet")
        older = git(repo, "rev-parse", "HEAD").stdout.strip()
        commit_file(repo, "poeme.txt", "un\ndeux\n", "suite")

        assert len(gitlog.blame(repo, "poeme.txt", rev=older)["lines"]) == 1

    def test_uncommitted_work_is_blamed_on_nobody(self, repo):
        commit_file(repo, "poeme.txt", "un\n", "premier jet")
        (repo / "poeme.txt").write_text("un\nligne en cours\n", encoding="utf-8")

        lines = gitlog.blame(repo, "poeme.txt")["lines"]
        assert lines[1]["sha"] == "0" * 40
        assert lines[1]["text"] == "ligne en cours"

    def test_a_path_outside_the_repository_is_refused(self, repo):
        with pytest.raises(ValueError, match="outside the repository"):
            gitlog.blame(repo, "../../etc/passwd")

    def test_an_unknown_file_is_reported(self, repo):
        with pytest.raises(ValueError, match="cannot attribute"):
            gitlog.blame(repo, "jamais-vu.py")


class TestAnnotatedTags:
    def test_a_tag_names_the_commit_it_stands_for(self, repo):
        git(repo, "tag", "-a", "v1.0.0", "-m", "une version annotee")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()

        tag = gitlog.tags(repo)[0]
        assert head.startswith(tag["sha"])
        assert tag["subject"] == "une version annotee"

    def test_the_panel_reads_a_tag_object_as_its_commit(self, repo):
        git(repo, "tag", "-a", "v1.0.0", "-m", "une version annotee")
        tag_object = git(repo, "rev-parse", "v1.0.0").stdout.strip()

        detail = gitlog.commit_detail(repo, tag_object)
        assert detail["subject"] == "initial"
        assert detail["files"]
        assert "calc.py" in gitlog.commit_patch(repo, tag_object)["diff"]


class TestFilesGitNeverPromisedWereUtf8:
    @pytest.fixture
    def latin(self, repo):
        (repo / "latin.txt").write_bytes("café en latin-1\n".encode("latin-1"))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "un fichier latin-1")
        return repo

    def test_a_patch_comes_back_instead_of_an_exception(self, latin):
        head = git(latin, "rev-parse", "HEAD").stdout.strip()
        patch = gitlog.commit_patch(latin, head, "latin.txt")
        assert "latin-1" in patch["diff"]

    def test_the_file_is_listed_like_any_other(self, latin):
        head = git(latin, "rev-parse", "HEAD").stdout.strip()
        assert [file["path"] for file in gitlog.commit_detail(latin, head)["files"]] == ["latin.txt"]

    def test_blame_survives_it_too(self, latin):
        assert gitlog.blame(latin, "latin.txt")["lines"]


class TestAccentedPaths:
    @pytest.fixture
    def accented(self, repo):
        (repo / "café été.txt").write_text("contenu\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "un nom accentue")
        return repo

    def test_a_name_is_reported_as_it_is_written(self, accented):
        head = git(accented, "rev-parse", "HEAD").stdout.strip()
        assert [file["path"] for file in gitlog.commit_detail(accented, head)["files"]] == ["café été.txt"]

    def test_its_patch_can_be_fetched_by_that_name(self, accented):
        head = git(accented, "rev-parse", "HEAD").stdout.strip()
        assert "contenu" in gitlog.commit_patch(accented, head, "café été.txt")["diff"]

    def test_the_working_tree_reads_it_too(self, accented):
        from gitsquid import worktree

        (accented / "café été.txt").write_text("modifié\n", encoding="utf-8")
        assert "modifié" in worktree.file_diff(accented, "café été.txt", staged=False)
        assert [entry.path for entry in worktree.status(accented)] == ["café été.txt"]


class TestHead:
    def test_head_names_the_branch_it_is_on(self, repo):
        state = gitlog.head(repo)
        assert state["branch"] == "main" and state["detached"] is False
        assert len(state["sha"]) >= 7

    def test_a_detached_head_says_so(self, repo):
        git(repo, "checkout", "-q", "--detach", "HEAD")
        state = gitlog.head(repo)
        assert state["detached"] is True
        assert state["branch"] == "HEAD"
