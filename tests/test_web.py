"""The local interface: authorisation guards and the core loop over HTTP."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

from gitsquid.db import open_db
from gitsquid.indexer import index_repo
from gitsquid.web import UIServer
from tests.conftest import git, make_patch


@pytest.fixture
def server(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("GITSQUID_TEST_COMMAND", f"{sys.executable} -m pytest -q")
    monkeypatch.setenv("GITSQUID_CONFIG_DIR", str(tmp_path / "config"))
    conn = open_db(settings.db_path)
    index_repo(conn, settings.repo, max_file_bytes=settings.max_file_bytes)
    conn.close()

    instance = UIServer(settings, port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    thread.join(timeout=5)


def call(server, path, *, method="GET", body=None, host=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload or b"{}")
        except json.JSONDecodeError:
            return error.code, {}


class TestGuards:
    def test_no_credential_is_needed_on_the_loopback(self, server):
        """The tool is single-user and local: opening the bare URL must just work."""
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/") as response:
            assert response.status == 200
        assert call(server, "/api/state")[0] == 200

    def test_foreign_host_headers_are_refused(self, server):
        status, payload = call(server, "/api/state", host="evil.example.com")
        assert status == 403
        assert "loopback" in payload["error"]

    def test_assets_load_for_the_page(self, server):
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/assets/app.css") as response:
            assert response.status == 200
            assert b"--bg" in response.read()

    def test_assets_are_confined_to_the_assets_directory(self, server):
        status, _ = call(server, "/assets/../../config.py")
        assert status == 404

    def test_unknown_routes_are_not_found(self, server):
        assert call(server, "/api/nope")[0] == 404


class TestReadApi:
    def test_state_reports_repository_and_degraded_mode(self, server, settings):
        status, payload = call(server, "/api/state")
        assert status == 200
        assert payload["repo"]["name"] == "workshop"
        assert payload["config"]["model_available"] is False
        assert payload["index"]["files"] == 3
        assert payload["counts"]["proposed"] == 0

    def test_graph_returns_the_commits_the_layout_needs(self, server):
        status, payload = call(server, "/api/graph")
        assert status == 200
        assert len(payload["commits"]) == 1
        commit = payload["commits"][0]
        assert commit["subject"] == "initial"
        assert commit["parents"] == []
        assert commit["short"] == commit["sha"][:7]
        assert payload["changes"] == []

    def test_commit_detail_lists_files(self, server):
        sha = call(server, "/api/graph")[1]["commits"][0]["sha"]
        status, payload = call(server, f"/api/commits/{sha}")
        assert status == 200
        assert {file["path"] for file in payload["files"]} == {"calc.py", "test_calc.py", "README.md"}

    def test_a_bogus_commit_id_is_rejected(self, server):
        status, payload = call(server, "/api/commits/not-a-sha")
        assert status == 400
        assert "commit id" in payload["error"]

    def test_missing_change_is_reported(self, server):
        assert call(server, "/api/changes/77")[0] == 404


class TestWorktreeOverHttp:
    def test_status_reports_staged_and_unstaged(self, server, repo):
        (repo / "calc.py").write_text("edited\n", encoding="utf-8")
        (repo / "fresh.py").write_text("x = 1\n", encoding="utf-8")

        status, payload = call(server, "/api/worktree")
        assert status == 200
        assert payload["unstaged"] == 2
        assert {file["path"] for file in payload["files"]} == {"calc.py", "fresh.py"}

    def test_stage_commit_cycle(self, server, repo):
        (repo / "calc.py").write_text("edited\n", encoding="utf-8")

        assert call(server, "/api/worktree/stage", method="POST", body={"paths": ["calc.py"]})[0] == 200
        assert call(server, "/api/worktree")[1]["staged"] == 1

        status, payload = call(
            server, "/api/worktree/commit", method="POST", body={"message": "Edit the calculation"}
        )
        assert status == 200
        assert payload["commit"]["short"]
        assert call(server, "/api/worktree")[1]["files"] == []

        graph = call(server, "/api/graph")[1]
        assert graph["commits"][0]["subject"] == "Edit the calculation"

    def test_a_commit_is_recorded_in_the_audit_trail(self, server, repo, settings):
        from gitsquid.db import open_db
        from gitsquid.models import EventLog

        (repo / "calc.py").write_text("edited\n", encoding="utf-8")
        call(server, "/api/worktree/stage", method="POST", body={"paths": ["calc.py"]})
        call(server, "/api/worktree/commit", method="POST", body={"message": "Tracked commit"})

        conn = open_db(settings.db_path)
        kinds = [event.kind for event in EventLog(conn).recent(5)]
        conn.close()
        assert "committed" in kinds

    def test_committing_nothing_is_refused(self, server):
        status, payload = call(server, "/api/worktree/commit", method="POST", body={"message": "x"})
        assert status == 400 and "Nothing is staged" in payload["error"]

    def test_hostile_paths_are_refused(self, server):
        status, payload = call(
            server, "/api/worktree/stage", method="POST", body={"paths": ["../../etc/passwd"]}
        )
        assert status == 400 and "outside the repository" in payload["error"]

    def test_file_diff_is_served_for_the_selected_file(self, server, repo):
        (repo / "calc.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")
        status, payload = call(server, "/api/filediff?path=calc.py&staged=0")
        assert status == 200
        assert "@@" in payload["diff"]

    def test_file_diff_refuses_a_traversal(self, server):
        status, payload = call(server, "/api/filediff?path=../../etc/passwd&staged=0")
        assert status == 400

    def test_branch_creation_and_checkout(self, server):
        assert call(server, "/api/worktree/branch", method="POST", body={"name": "feature/web"})[0] == 200
        assert call(server, "/api/state")[1]["repo"]["branch"] == "feature/web"

        assert call(server, "/api/worktree/checkout", method="POST", body={"branch": "main"})[0] == 200
        assert call(server, "/api/state")[1]["repo"]["branch"] == "main"

    def test_an_invalid_branch_name_is_refused(self, server):
        status, payload = call(server, "/api/worktree/branch", method="POST", body={"name": "bad name"})
        assert status == 400 and "not a valid branch name" in payload["error"]

    def test_discard_restores_a_file(self, server, repo):
        original = (repo / "calc.py").read_text()
        (repo / "calc.py").write_text("ruined\n", encoding="utf-8")
        assert call(server, "/api/worktree/discard", method="POST", body={"paths": ["calc.py"]})[0] == 200
        assert (repo / "calc.py").read_text() == original

    def test_unknown_worktree_action_is_not_found(self, server):
        assert call(server, "/api/worktree/explode", method="POST", body={})[0] == 404


class TestLoopOverHttp:
    def test_propose_apply_test_revert(self, server, repo, settings):
        patch = make_patch(repo, "calc.py", (repo / "calc.py").read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")

        status, payload = call(
            server, "/api/propose", method="POST",
            body={"task": "Add a multiply helper", "patch": patch},
        )
        assert status == 200 and payload["applies_cleanly"]
        change_id = payload["change_id"]
        assert "multiply" not in (repo / "calc.py").read_text()

        assert call(server, f"/api/changes/{change_id}/apply", method="POST", body={})[0] == 200
        assert "def multiply" in (repo / "calc.py").read_text()

        status, payload = call(server, f"/api/changes/{change_id}/test", method="POST", body={})
        assert status == 200 and payload["passed"] is True

        status, payload = call(server, f"/api/changes/{change_id}")
        assert payload["status"] == "verified"
        assert [event["kind"] for event in payload["events"]] == ["proposed", "applied", "tested"]

        assert call(server, f"/api/changes/{change_id}/revert", method="POST", body={})[0] == 200
        assert "multiply" not in (repo / "calc.py").read_text()

    def test_propose_without_a_key_or_patch_explains_degraded_mode(self, server):
        status, payload = call(server, "/api/propose", method="POST", body={"task": "do something"})
        assert status == 400
        assert "ANTHROPIC_API_KEY" in payload["error"]

    def test_an_empty_task_is_rejected(self, server):
        status, payload = call(server, "/api/propose", method="POST", body={"task": "  ", "patch": "x"})
        assert status == 400
        assert "task" in payload["error"]

    def test_an_unsafe_patch_is_rejected(self, server):
        hostile = "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n"
        status, payload = call(
            server, "/api/propose", method="POST", body={"task": "escape", "patch": hostile}
        )
        assert status == 400
        assert "Rejected patch" in payload["error"]

    def test_samples_load_are_guarded_and_clear(self, server):
        assert call(server, "/api/samples/load", method="POST", body={})[0] == 200
        status, payload = call(server, "/api/samples/load", method="POST", body={})
        assert status == 400 and "already loaded" in payload["error"]

        status, payload = call(server, "/api/changes/1/apply", method="POST", body={})
        assert status == 400 and "Sample records" in payload["error"]

        assert call(server, "/api/samples/clear", method="POST", body={})[0] == 200
        assert call(server, "/api/state")[1]["samples"] == 0

    def test_export_then_import_round_trip(self, server, repo):
        patch = make_patch(repo, "calc.py", (repo / "calc.py").read_text() + "\n\nVALUE = 1\n")
        call(server, "/api/propose", method="POST", body={"task": "Add a constant", "patch": patch})

        request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/export")
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.loads(response.read())
        assert document["format"] == "gitsquid-export"
        assert len(document["changes"]) == 1

        status, payload = call(server, "/api/import", method="POST", body={"document": document})
        assert status == 200
        assert "1 already present" in payload["message"]

    def test_import_rejects_a_foreign_document(self, server):
        status, payload = call(server, "/api/import", method="POST", body={"document": {"format": "nope"}})
        assert status == 400
        assert "gitsquid-export" in payload["error"]

    def test_reindex_reports_progress(self, server, repo):
        (repo / "extra.py").write_text("VALUE = 2\n", encoding="utf-8")
        status, payload = call(server, "/api/index", method="POST", body={})
        assert status == 200
        assert payload["index"]["files"] == 4


class TestMultipleRepositories:
    def test_the_active_repository_is_registered_on_start(self, server, repo):
        status, payload = call(server, "/api/repos")
        assert status == 200
        assert payload["active"] == str(repo)
        assert [entry["name"] for entry in payload["repos"]] == ["workshop"]

    def test_opening_a_second_repository_switches_everything(self, server, repo, tmp_path):
        other = tmp_path / "atelier"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "config", "user.email", "tester@example.invalid")
        git(other, "config", "user.name", "Tester")
        (other / "main.go").write_text("package main\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "depart")

        status, payload = call(server, "/api/repos/open", method="POST", body={"path": str(other)})
        assert status == 200
        assert payload["active"] == str(other)

        assert call(server, "/api/state")[1]["repo"]["name"] == "atelier"
        assert call(server, "/api/graph")[1]["commits"][0]["subject"] == "depart"
        assert (other / ".gitsquid" / "gitsquid.db").exists()
        assert {entry["name"] for entry in call(server, "/api/repos")[1]["repos"]} == {"workshop", "atelier"}

    def test_each_repository_keeps_its_own_history(self, server, repo, tmp_path, patch_add_multiply):
        call(server, "/api/propose", method="POST",
             body={"task": "Add a multiply helper", "patch": patch_add_multiply})
        assert len(call(server, "/api/graph")[1]["changes"]) == 1

        other = tmp_path / "autre"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        call(server, "/api/repos/open", method="POST", body={"path": str(other)})
        assert call(server, "/api/graph")[1]["changes"] == []

        call(server, "/api/repos/open", method="POST", body={"path": str(repo)})
        assert len(call(server, "/api/graph")[1]["changes"]) == 1

    def test_opening_a_directory_that_is_not_a_repository_is_refused(self, server, tmp_path):
        plain = tmp_path / "pas-un-depot"
        plain.mkdir()
        status, payload = call(server, "/api/repos/open", method="POST", body={"path": str(plain)})
        assert status == 400
        assert "not inside a Git repository" in payload["error"]

    def test_the_active_repository_cannot_be_forgotten(self, server, repo):
        status, payload = call(server, "/api/repos/forget", method="POST", body={"path": str(repo)})
        assert status == 400
        assert "opening another one first" in payload["error"]

    def test_forgetting_leaves_the_repository_on_disk(self, server, repo, tmp_path):
        other = tmp_path / "jetable"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        call(server, "/api/repos/open", method="POST", body={"path": str(other)})

        assert call(server, "/api/repos/forget", method="POST", body={"path": str(repo)})[0] == 200
        assert repo.exists()
        assert [entry["name"] for entry in call(server, "/api/repos")[1]["repos"]] == ["jetable"]


class TestRepositoryManagement:
    def test_stash_round_trip(self, server, repo):
        (repo / "calc.py").write_text("mise de cote\n", encoding="utf-8")

        assert call(server, "/api/worktree/stash", method="POST", body={"message": "wip"})[0] == 200
        assert call(server, "/api/worktree")[1]["files"] == []

        stashes = call(server, "/api/state")[1]["repo"]["stashes"]
        assert len(stashes) == 1

        assert call(server, "/api/worktree/stash-pop", method="POST",
                    body={"ref": stashes[0]["ref"]})[0] == 200
        assert (repo / "calc.py").read_text() == "mise de cote\n"

    def test_stashing_a_clean_tree_is_refused(self, server):
        status, payload = call(server, "/api/worktree/stash", method="POST", body={})
        assert status == 400 and "clean" in payload["error"]

    def test_a_forged_stash_reference_is_refused(self, server):
        status, _ = call(server, "/api/worktree/stash-pop", method="POST",
                         body={"ref": "stash@{0}; rm -rf /"})
        assert status == 400

    def test_merging_a_branch_moves_its_commits_in(self, server, repo):
        call(server, "/api/worktree/branch", method="POST", body={"name": "feature/z"})
        (repo / "nouveau.py").write_text("VALEUR = 1\n", encoding="utf-8")
        call(server, "/api/worktree/stage", method="POST", body={"paths": ["nouveau.py"]})
        call(server, "/api/worktree/commit", method="POST", body={"message": "Ajoute nouveau.py"})
        call(server, "/api/worktree/checkout", method="POST", body={"branch": "main"})
        assert not (repo / "nouveau.py").exists()

        status, payload = call(server, "/api/worktree/merge", method="POST", body={"branch": "feature/z"})
        assert status == 200, payload
        assert (repo / "nouveau.py").exists()

    def test_deleting_a_merged_branch(self, server):
        call(server, "/api/worktree/branch", method="POST", body={"name": "feature/tmp"})
        call(server, "/api/worktree/checkout", method="POST", body={"branch": "main"})
        assert call(server, "/api/worktree/delete-branch", method="POST",
                    body={"branch": "feature/tmp"})[0] == 200
        assert [b["name"] for b in call(server, "/api/state")[1]["repo"]["branches"]] == ["main"]

    def test_deleting_the_current_branch_is_refused(self, server):
        status, payload = call(server, "/api/worktree/delete-branch", method="POST",
                               body={"branch": "main"})
        assert status == 400 and "cannot delete the branch you are on" in payload["error"]

    def test_remote_operations_explain_the_absence_of_a_remote(self, server):
        for action in ("fetch", "pull", "push"):
            status, payload = call(server, f"/api/worktree/{action}", method="POST", body={})
            assert status == 400
            assert "no remote configured" in payload["error"]

    def test_state_reports_remotes_and_tracking(self, server):
        repo_state = call(server, "/api/state")[1]["repo"]
        assert repo_state["remotes"] == []
        assert repo_state["tracking"] == {"upstream": None, "ahead": 0, "behind": 0}


def commit_over_http(server, repo, name, text, message):
    (repo / name).write_text(text, encoding="utf-8")
    call(server, "/api/worktree/stage", method="POST", body={"paths": [name]})
    call(server, "/api/worktree/commit", method="POST", body={"message": message})
    return call(server, "/api/graph")[1]["commits"][0]["sha"]


class TestHistoryOverHttp:
    def test_a_commit_can_be_tagged_and_the_tag_listed(self, server):
        sha = call(server, "/api/graph")[1]["commits"][0]["sha"]
        status, payload = call(server, "/api/worktree/tag-create", method="POST",
                               body={"name": "v1.0.0", "sha": sha, "message": "premiere"})
        assert status == 200, payload

        tags = call(server, "/api/state")[1]["repo"]["tags"]
        assert [tag["name"] for tag in tags] == ["v1.0.0"]

        assert call(server, "/api/worktree/tag-delete", method="POST", body={"name": "v1.0.0"})[0] == 200
        assert call(server, "/api/state")[1]["repo"]["tags"] == []

    def test_a_branch_can_start_at_an_older_commit(self, server, repo):
        first = call(server, "/api/graph")[1]["commits"][0]["sha"]
        commit_over_http(server, repo, "plus_tard.py", "X = 1\n", "plus tard")

        status, _ = call(server, "/api/worktree/branch-from", method="POST",
                         body={"sha": first, "name": "feature/retour"})
        assert status == 200
        assert call(server, "/api/state")[1]["repo"]["branch"] == "feature/retour"
        assert not (repo / "plus_tard.py").exists()

    def test_checking_out_a_commit_detaches_head(self, server):
        sha = call(server, "/api/graph")[1]["commits"][0]["sha"]
        status, payload = call(server, "/api/worktree/checkout-commit", method="POST", body={"sha": sha})
        assert status == 200 and "detached" in payload["message"]

    def test_revert_adds_a_commit_that_undoes_the_work(self, server, repo):
        sha = commit_over_http(server, repo, "a_annuler.py", "X = 1\n", "a annuler")
        assert call(server, "/api/worktree/revert-commit", method="POST", body={"sha": sha})[0] == 200
        assert not (repo / "a_annuler.py").exists()

    def test_reset_moves_the_branch(self, server, repo):
        first = call(server, "/api/graph")[1]["commits"][0]["sha"]
        commit_over_http(server, repo, "jetable.py", "X = 1\n", "jetable")

        status, payload = call(server, "/api/worktree/reset", method="POST",
                               body={"sha": first, "mode": "hard"})
        assert status == 200, payload
        assert not (repo / "jetable.py").exists()

    def test_an_unknown_reset_mode_is_refused(self, server):
        sha = call(server, "/api/graph")[1]["commits"][0]["sha"]
        status, payload = call(server, "/api/worktree/reset", method="POST",
                               body={"sha": sha, "mode": "atomique"})
        assert status == 400 and "Reset mode" in payload["error"]

    def test_a_forged_commit_id_never_reaches_git(self, server):
        for action, body in [
            ("cherry-pick", {"sha": "--upload-pack=touch"}),
            ("checkout-commit", {"sha": "HEAD"}),
            ("branch-from", {"sha": "; rm -rf /", "name": "x"}),
        ]:
            status, payload = call(server, f"/api/worktree/{action}", method="POST", body=body)
            assert status == 400 and "commit id" in payload["error"]

    def test_a_history_action_lands_in_the_audit_trail(self, server, repo, settings):
        from gitsquid.db import open_db
        from gitsquid.models import EventLog

        sha = commit_over_http(server, repo, "trace.py", "X = 1\n", "trace")
        call(server, "/api/worktree/revert-commit", method="POST", body={"sha": sha})

        conn = open_db(settings.db_path)
        kinds = [event.kind for event in EventLog(conn).recent(5)]
        conn.close()
        assert "revert-commit" in kinds

    def test_a_conflict_is_reported_as_a_pending_operation_and_can_be_aborted(self, server, repo):
        commit_over_http(server, repo, "partage.py", "VALEUR = 0\n", "partage")
        git(repo, "checkout", "-q", "-b", "side")
        side = commit_over_http(server, repo, "partage.py", "VALEUR = 2\n", "cote")
        git(repo, "checkout", "-q", "main")
        commit_over_http(server, repo, "partage.py", "VALEUR = 1\n", "principal")

        status, payload = call(server, "/api/worktree/cherry-pick", method="POST", body={"sha": side})
        assert status == 400 and "conflict" in payload["error"]

        operation = call(server, "/api/state")[1]["repo"]["operation"]
        assert operation["kind"] == "cherry-pick"
        assert operation["conflicts"] == ["partage.py"]

        assert call(server, "/api/worktree/abort", method="POST", body={})[0] == 200
        assert call(server, "/api/state")[1]["repo"]["operation"] is None


class TestFileActionsOverHttp:
    def test_one_hunk_can_be_staged_from_the_interface(self, server, repo):
        lines = [f"line {number}\n" for number in range(40)]
        (repo / "long.txt").write_text("".join(lines), encoding="utf-8")
        call(server, "/api/worktree/stage", method="POST", body={"paths": ["long.txt"]})
        call(server, "/api/worktree/commit", method="POST", body={"message": "long"})

        lines[0] = "premiere modifiee\n"
        lines[-1] = "derniere modifiee\n"
        (repo / "long.txt").write_text("".join(lines), encoding="utf-8")

        diff = call(server, "/api/filediff?path=long.txt&staged=0")[1]["diff"]
        head, _, rest = diff.partition("@@")
        first_hunk = head + "@@" + rest.split("\n@@")[0] + "\n"

        status, payload = call(server, "/api/worktree/stage-hunk", method="POST", body={"patch": first_hunk})
        assert status == 200, payload
        staged = call(server, "/api/filediff?path=long.txt&staged=1")[1]["diff"]
        assert "premiere modifiee" in staged and "derniere modifiee" not in staged

    def test_a_hunk_outside_the_repository_is_refused(self, server):
        hostile = "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n@@ -1 +1 @@\n-a\n+b\n"
        status, payload = call(server, "/api/worktree/stage-hunk", method="POST", body={"patch": hostile})
        assert status == 400 and "Refusing path" in payload["error"]

    def test_ignoring_a_file_writes_gitignore(self, server, repo):
        (repo / "bruit.log").write_text("noise\n", encoding="utf-8")
        status, _ = call(server, "/api/worktree/ignore", method="POST", body={"paths": ["bruit.log"]})
        assert status == 200
        assert "/bruit.log" in (repo / ".gitignore").read_text()
        assert [file["path"] for file in call(server, "/api/worktree")[1]["files"]] == [".gitignore"]

    def test_amending_replaces_the_last_commit(self, server, repo):
        before = len(call(server, "/api/graph")[1]["commits"])
        (repo / "calc.py").write_text("oubli\n", encoding="utf-8")
        call(server, "/api/worktree/stage", method="POST", body={"paths": ["calc.py"]})

        status, payload = call(server, "/api/worktree/commit", method="POST",
                               body={"message": "initial, corrige", "amend": True})
        assert status == 200 and "Amended" in payload["message"]

        commits = call(server, "/api/graph")[1]["commits"]
        assert len(commits) == before and commits[0]["subject"] == "initial, corrige"

    def test_file_history_lists_the_commits_that_touched_a_file(self, server, repo):
        commit_over_http(server, repo, "calc.py", "VALEUR = 2\n", "seconde version")
        status, payload = call(server, "/api/filehistory?path=calc.py")
        assert status == 200
        assert [commit["subject"] for commit in payload["commits"]] == ["seconde version", "initial"]

    def test_file_history_refuses_a_traversal(self, server):
        status, payload = call(server, "/api/filehistory?path=../../etc/passwd")
        assert status == 400 and "outside the repository" in payload["error"]

    def test_the_head_message_is_exposed_for_the_amend_prefill(self, server):
        assert call(server, "/api/state")[1]["repo"]["head_message"] == "initial"


class TestSearchOverHttp:
    def test_the_whole_history_is_searched(self, server, repo):
        commit_over_http(server, repo, "facture.py", "TOTAL = 1\n", "Corrige la facture")
        status, payload = call(server, "/api/search?q=facture")
        assert status == 200
        assert [commit["subject"] for commit in payload["commits"]] == ["Corrige la facture"]

    def test_a_query_of_one_letter_returns_nothing(self, server):
        assert call(server, "/api/search?q=a")[1]["commits"] == []

    def test_the_graph_says_how_deep_it_looked(self, server, repo):
        for index in range(12):
            commit_over_http(server, repo, f"step{index}.py", "X = 1\n", f"step {index}")
        status, payload = call(server, "/api/graph?limit=10")
        assert status == 200
        assert len(payload["commits"]) == 10
        assert payload["total_commits"] == 13
        assert payload["limit"] == 10

    def test_a_limit_that_is_not_a_number_falls_back(self, server):
        assert call(server, "/api/graph?limit=abc")[1]["limit"] == 80

    def test_an_absurd_limit_is_clamped(self, server):
        assert call(server, "/api/graph?limit=999999")[1]["limit"] == 5000
        assert call(server, "/api/graph?limit=0")[1]["limit"] == 10

    def test_a_conflict_can_be_settled_from_the_interface(self, server, repo):
        commit_over_http(server, repo, "partage.py", "VALEUR = 0\n", "partage")
        git(repo, "checkout", "-q", "-b", "cote")
        commit_over_http(server, repo, "partage.py", "VALEUR = 2\n", "cote")
        git(repo, "checkout", "-q", "main")
        commit_over_http(server, repo, "partage.py", "VALEUR = 1\n", "principal")
        git(repo, "merge", "cote")

        status, payload = call(server, "/api/worktree/resolve", method="POST",
                               body={"paths": ["partage.py"], "side": "theirs"})
        assert status == 200, payload
        assert (repo / "partage.py").read_text() == "VALEUR = 2\n"
        assert call(server, "/api/worktree")[1]["conflicted"] == 0


class TestWhatTheGraphCovers:
    def test_every_branch_by_default(self, server, repo):
        git(repo, "checkout", "-q", "-b", "ailleurs")
        commit_over_http(server, repo, "ailleurs.py", "A = 1\n", "travail ailleurs")
        git(repo, "checkout", "-q", "main")

        payload = call(server, "/api/graph")[1]
        assert payload["every_ref"] is True
        assert payload["commits"][0]["subject"] == "travail ailleurs"
        assert payload["total_commits"] == 2

    def test_this_branch_only_on_request(self, server, repo):
        git(repo, "checkout", "-q", "-b", "ailleurs")
        commit_over_http(server, repo, "ailleurs.py", "A = 1\n", "travail ailleurs")
        git(repo, "checkout", "-q", "main")

        payload = call(server, "/api/graph?refs=head")[1]
        assert payload["every_ref"] is False
        assert [commit["subject"] for commit in payload["commits"]] == ["initial"]
        assert payload["total_commits"] == 1


class TestTheIconFont:
    def test_it_is_served_from_the_assets(self, server):
        import urllib.request

        url = f"http://127.0.0.1:{server.port}/assets/primeicons/fonts/primeicons.woff2"
        with urllib.request.urlopen(url, timeout=30) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "font/woff2"
            assert response.read(4) == b"wOF2"

    def test_the_policy_lets_the_page_load_it(self, server):
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=30) as response:
            assert "font-src 'self'" in response.headers["Content-Security-Policy"]
