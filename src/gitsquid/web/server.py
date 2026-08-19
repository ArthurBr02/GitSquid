from __future__ import annotations

import json
import threading
from dataclasses import asdict
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import diffs, gitlog, history, indexer, portability, refs, registry, sampledata, worktree
from ..config import ConfigError, Settings, load_settings
from ..db import open_db
from ..llm import AnthropicBackend, PatchFileBackend, ProposalError
from ..models import ChangeRepo, ChangeStatus, EventLog, TestRunRepo
from ..gitcmd import GitError, require_paths
from ..safety import clean_text_input
from ..workflow import ChangeService, WorkflowError

ASSETS = Path(__file__).parent / "assets"
# Everything that moves HEAD, rewrites history, or touches a remote lands in the audit trail;
# staging noise does not.
AUDITED_ACTIONS = frozenset({
    "merge", "rebase", "cherry-pick", "revert-commit", "reset", "checkout-commit", "branch-from",
    "branch", "delete-branch", "rename-branch", "checkout-remote", "delete-remote-branch",
    "tag-create", "tag-delete", "tag-push", "push", "pull", "stash-branch", "abort", "continue",
})
MAX_BODY_BYTES = 2 * 1024 * 1024
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


class UIServer:
    """Serves the local interface. Loopback only — no account, no token, no network exposure."""

    def __init__(self, settings: Settings, *, host: str = "127.0.0.1", port: int = 8756) -> None:
        self.settings = settings
        registry.add(settings.repo)
        handler = partial(_Handler, self)
        self._http = ThreadingHTTPServer((host, port), handler)
        self._http.daemon_threads = True
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self._http.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def serve_forever(self) -> None:
        self._http.serve_forever()

    def shutdown(self) -> None:
        self._http.shutdown()
        self._http.server_close()

    def service(self):
        conn = open_db(self.settings.db_path)
        return conn, ChangeService(conn, self.settings)

    # --- read endpoints -------------------------------------------------

    def repos(self) -> dict:
        return {
            "active": str(self.settings.repo),
            "repos": [entry.as_dict() for entry in registry.known()],
        }

    def open_repo(self, payload: dict) -> dict:
        raw = _string(payload, "path").strip()
        if not raw:
            raise ApiError("A repository path is required.")
        try:
            root = registry.add(Path(raw).expanduser())
            settings = load_settings(root)
        except ConfigError as exc:
            raise ApiError(str(exc)) from exc
        with self._lock:
            open_db(settings.db_path).close()
            self.settings = settings
        return {"message": f"Opened {settings.repo.name}.", "active": str(settings.repo)}

    def forget_repo(self, payload: dict) -> dict:
        raw = _string(payload, "path").strip()
        if Path(raw).expanduser().resolve() == self.settings.repo:
            raise ApiError("Close this repository by opening another one first.")
        if not registry.remove(raw):
            raise ApiError("That repository is not in the list.", HTTPStatus.NOT_FOUND)
        return {"message": "Removed from the list. Nothing on disk was touched."}

    def state(self) -> dict:
        conn, _ = self.service()
        try:
            summary = indexer.index_summary(conn)
            counts = {
                str(status): conn.execute(
                    "SELECT COUNT(*) FROM changes WHERE status=?", (str(status),)
                ).fetchone()[0]
                for status in ChangeStatus
            }
            return {
                "repo": {
                    "name": self.settings.repo.name,
                    "path": str(self.settings.repo),
                    "branch": gitlog.current_branch(self.settings.repo),
                    "branches": gitlog.branches(self.settings.repo),
                    "status": gitlog.working_status(self.settings.repo),
                    "remote_branches": gitlog.remote_branches(self.settings.repo),
                    "tags": gitlog.tags(self.settings.repo),
                    "remotes": worktree.remotes(self.settings.repo),
                    "tracking": worktree.tracking(self.settings.repo),
                    "stashes": worktree.stash_list(self.settings.repo),
                    "operation": gitlog.pending_operation(self.settings.repo),
                    "head_message": worktree.head_message(self.settings.repo),
                },
                "config": {
                    "model": self.settings.model,
                    "effort": self.settings.effort,
                    "test_command": self.settings.test_command,
                    "context_budget": self.settings.max_context_chars,
                    "database": str(self.settings.db_path),
                    "model_available": self.settings.model_available,
                },
                "index": summary,
                "counts": counts,
                "total_changes": ChangeRepo(conn).count(),
                "samples": sampledata.count(conn),
            }
        finally:
            conn.close()

    def graph(self, limit: int = 80) -> dict:
        conn, _ = self.service()
        try:
            rows = [asdict(commit) | {"short": commit.short} for commit in gitlog.commits(self.settings.repo, limit=limit)]
            changes = [_change_row(change) for change in ChangeRepo(conn).list(limit=limit)]
            return {"commits": rows, "changes": changes}
        finally:
            conn.close()

    def change_detail(self, change_id: int) -> dict:
        conn, _ = self.service()
        try:
            change = ChangeRepo(conn).get(change_id)
            if change is None:
                raise ApiError(f"No change #{change_id}.", HTTPStatus.NOT_FOUND)
            added, removed = diffs.stats(change.diff)
            check = diffs.check(self.settings.repo, change.diff)
            return _change_row(change) | {
                "diff": change.diff,
                "rationale": change.rationale,
                "base_commit": change.base_commit,
                "digest": change.short_sha,
                "added": added,
                "removed": removed,
                "applies_cleanly": check.ok,
                "check_message": "" if check.ok else check.message,
                "input_tokens": change.input_tokens,
                "output_tokens": change.output_tokens,
                "test_runs": [
                    {
                        "command": run.command,
                        "exit_code": run.exit_code,
                        "passed": run.passed,
                        "duration_ms": run.duration_ms,
                        "output": run.output_tail,
                        "created_at": run.created_at,
                    }
                    for run in TestRunRepo(conn).for_change(change_id)
                ],
                "events": [
                    {"kind": event.kind, "message": event.message, "created_at": event.created_at}
                    for event in EventLog(conn).for_change(change_id)
                ],
            }
        finally:
            conn.close()

    def worktree(self) -> dict:
        entries = worktree.status(self.settings.repo)
        return {
            "files": [entry.as_dict() for entry in entries],
            "staged": sum(1 for entry in entries if entry.staged),
            "unstaged": sum(1 for entry in entries if entry.unstaged or entry.untracked),
            "conflicted": sum(1 for entry in entries if entry.conflicted),
        }

    def file_diff(self, path: str, staged: bool) -> dict:
        try:
            return {"path": path, "staged": staged,
                    "diff": worktree.file_diff(self.settings.repo, path, staged=staged)}
        except GitError as exc:
            raise ApiError(str(exc)) from exc

    def file_history(self, path: str) -> dict:
        try:
            require_paths([path])
        except GitError as exc:
            raise ApiError(str(exc)) from exc
        return {"path": path, "commits": gitlog.file_history(self.settings.repo, path)}

    def worktree_action(self, action: str, payload: dict) -> dict:
        handlers = _handlers(self.settings.repo, payload)
        with self._lock:
            if action == "commit":
                return self._commit(payload)
            handler = handlers.get(action)
            if handler is None:
                raise ApiError(f"Unknown action: {action}", HTTPStatus.NOT_FOUND)
            try:
                message = handler()
            except GitError as exc:
                raise ApiError(str(exc)) from exc
            if action in AUDITED_ACTIONS:
                self._record(action, message)
            return {"message": message}

    def _commit(self, payload: dict) -> dict:
        amend = bool(payload.get("amend"))
        try:
            result = worktree.commit(self.settings.repo, _string(payload, "message"), amend=amend)
        except GitError as exc:
            raise ApiError(str(exc)) from exc
        verb = "Amended" if amend else "Committed"
        self._record("amended" if amend else "committed", f"{result['short']} {result['message']}")
        return {"message": f"{verb} {result['short']}.", "commit": result}

    def _record(self, kind: str, message: str) -> None:
        conn, _ = self.service()
        try:
            EventLog(conn).record(kind, message)
        finally:
            conn.close()

    def commit_detail(self, sha: str) -> dict:
        try:
            return gitlog.commit_detail(self.settings.repo, sha)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

    # --- write endpoints ------------------------------------------------

    def reindex(self) -> dict:
        with self._lock:
            conn, _ = self.service()
            try:
                stats = indexer.index_repo(
                    conn, self.settings.repo, max_file_bytes=self.settings.max_file_bytes
                )
                EventLog(conn).record("indexed", f"{stats.files_indexed} indexed from the interface")
                return {"message": f"{stats.files_indexed} file(s) re-indexed, {stats.files_unchanged} unchanged.", "index": indexer.index_summary(conn)}
            finally:
                conn.close()

    def propose(self, payload: dict) -> dict:
        task = _string(payload, "task")
        patch = payload.get("patch") or ""
        with self._lock:
            conn, service = self.service()
            try:
                try:
                    task = clean_text_input(task, max_len=2000, field="task")
                except ValueError as exc:
                    raise ApiError(str(exc)) from exc
                if patch.strip():
                    backend = PatchFileBackend(patch)
                elif self.settings.model_available:
                    backend = AnthropicBackend(self.settings)
                else:
                    raise ApiError(
                        "No ANTHROPIC_API_KEY, so no model can be called. Paste a diff instead — "
                        "GitSquid will validate, apply, test and record it."
                    )
                try:
                    outcome = service.propose(task, backend)
                except ProposalError as exc:
                    raise ApiError(str(exc)) from exc
                return {
                    "message": f"Recorded change #{outcome.change.id}.",
                    "change_id": outcome.change.id,
                    "applies_cleanly": outcome.applies_cleanly,
                    "check_message": outcome.check_message,
                }
            finally:
                conn.close()

    def act(self, change_id: int, action: str, payload: dict) -> dict:
        with self._lock:
            conn, service = self.service()
            try:
                change = ChangeRepo(conn).get(change_id)
                if change is None:
                    raise ApiError(f"No change #{change_id}.", HTTPStatus.NOT_FOUND)
                if change.is_sample and action in {"apply", "revert"}:
                    raise ApiError("Sample records describe a fictional repository and are never applied.")
                try:
                    if action == "apply":
                        service.apply(change)
                        return {"message": f"Change #{change_id} applied to the working tree."}
                    if action == "revert":
                        service.revert(change)
                        return {"message": f"Change #{change_id} reversed."}
                    if action == "test":
                        command = payload.get("command") or self.settings.test_command
                        run = service.verify(change, command=str(command)[:500])
                        return {
                            "message": f"`{run.command}` exited {run.exit_code} in {run.duration_ms}ms.",
                            "passed": run.passed,
                            "output": run.output_tail,
                        }
                except WorkflowError as exc:
                    raise ApiError(str(exc)) from exc
                raise ApiError(f"Unknown action: {action}", HTTPStatus.NOT_FOUND)
            finally:
                conn.close()

    def samples(self, action: str) -> dict:
        with self._lock:
            conn, _ = self.service()
            try:
                if action == "load":
                    if sampledata.count(conn) > 0:
                        raise ApiError("Sample records are already loaded.")
                    return {"message": f"Loaded {sampledata.load(conn)} sample change(s)."}
                removed = sampledata.clear(conn)
                return {"message": f"Deleted {removed['changes']} sample change(s)."}
            finally:
                conn.close()

    def export(self) -> bytes:
        conn, _ = self.service()
        try:
            payload = portability.export_payload(conn, repo_name=self.settings.repo.name)
            return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        finally:
            conn.close()

    def import_payload(self, payload: dict) -> dict:
        raw = payload.get("document")
        if not isinstance(raw, dict):
            raise ApiError("Paste the contents of a GitSquid export file.")
        with self._lock:
            conn, _ = self.service()
            target = self.settings.state_dir / "import-staging.json"
            try:
                target.write_text(json.dumps(raw), encoding="utf-8")
                stats = portability.import_from_file(conn, target)
                return {"message": f"Imported {stats.changes} change(s); {stats.skipped} already present."}
            except portability.ImportError_ as exc:
                raise ApiError(str(exc)) from exc
            finally:
                target.unlink(missing_ok=True)
                conn.close()


def _handlers(repo: Path, payload: dict):
    """One name per action, grouped by the module that owns it."""
    paths = payload.get("paths") or []

    def branch() -> str:
        return _string(payload, "branch")

    def sha() -> str:
        return _string(payload, "sha")

    return {
        "stage": lambda: worktree.stage(repo, paths),
        "unstage": lambda: worktree.unstage(repo, paths),
        "discard": lambda: worktree.discard(repo, paths),
        "ignore": lambda: worktree.ignore(repo, paths),
        "stage-hunk": lambda: worktree.apply_patch(repo, _string(payload, "patch"), target="stage"),
        "unstage-hunk": lambda: worktree.apply_patch(repo, _string(payload, "patch"), target="unstage"),
        "discard-hunk": lambda: worktree.apply_patch(repo, _string(payload, "patch"), target="discard"),
        "checkout": lambda: worktree.checkout(repo, branch()),
        "branch": lambda: worktree.create_branch(repo, _string(payload, "name")),
        "merge": lambda: worktree.merge(repo, branch(), squash=bool(payload.get("squash"))),
        "delete-branch": lambda: worktree.delete_branch(repo, branch(), force=bool(payload.get("force"))),
        "fetch": lambda: worktree.fetch(repo),
        "pull": lambda: worktree.pull(repo),
        "push": lambda: worktree.push(repo, force=bool(payload.get("force"))),
        "stash": lambda: worktree.stash_save(repo, str(payload.get("message") or "")),
        "stash-pop": lambda: worktree.stash_pop(repo, _string(payload, "ref")),
        "stash-apply": lambda: worktree.stash_apply(repo, _string(payload, "ref")),
        "stash-drop": lambda: worktree.stash_drop(repo, _string(payload, "ref")),
        "stash-branch": lambda: worktree.stash_branch(repo, _string(payload, "ref"), _string(payload, "name")),
        "rename-branch": lambda: refs.rename_branch(repo, branch(), _string(payload, "name")),
        "push-branch": lambda: refs.push_branch(repo, branch()),
        "checkout-remote": lambda: refs.track_remote_branch(repo, branch()),
        "delete-remote-branch": lambda: refs.delete_remote_branch(repo, branch()),
        "tag-create": lambda: refs.create_tag(
            repo, _string(payload, "name"), sha=str(payload.get("sha") or ""),
            message=str(payload.get("message") or ""),
        ),
        "tag-delete": lambda: refs.delete_tag(repo, _string(payload, "name")),
        "tag-push": lambda: refs.push_tag(repo, _string(payload, "name")),
        "checkout-commit": lambda: history.checkout_commit(repo, sha()),
        "branch-from": lambda: history.branch_from(repo, sha(), _string(payload, "name")),
        "cherry-pick": lambda: history.cherry_pick(repo, sha()),
        "revert-commit": lambda: history.revert_commit(repo, sha()),
        "reset": lambda: history.reset(repo, sha(), mode=str(payload.get("mode") or "mixed")),
        "rebase": lambda: history.rebase(repo, _string(payload, "target")),
        "abort": lambda: history.abort(repo),
        "continue": lambda: history.resume(repo),
    }


def _change_row(change) -> dict:
    return {
        "id": change.id,
        "task": change.task,
        "status": str(change.status),
        "source": str(change.source),
        "model": change.model,
        "files": change.files_touched,
        "is_sample": change.is_sample,
        "created_at": change.created_at,
        "applied_at": change.applied_at,
    }


def _string(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ApiError(f"Field '{key}' must be text.")
    return value


class _Handler(BaseHTTPRequestHandler):
    server_version = "gitsquid"
    sys_version = ""

    def __init__(self, ui: UIServer, *args, **kwargs) -> None:
        self.ui = ui
        super().__init__(*args, **kwargs)

    def log_message(self, *args) -> None:  # never log request contents
        return

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # A local tool has nothing to gain from caching, and a stale asset in the
        # desktop webview silently serves yesterday's interface.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _asset(self, name: str) -> None:
        path = (ASSETS / name).resolve()
        if not path.is_file() or ASSETS.resolve() not in path.parents:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        content_type = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ApiError("Request body too large.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError("Request body is not valid JSON.") from exc

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Only loopback requests are served."})
            return
        route = parsed.path
        if route.startswith("/assets/"):
            self._asset(route.removeprefix("/assets/"))
            return

        try:
            if route == "/":
                self._asset("index.html")
            elif route == "/api/state":
                self._json(HTTPStatus.OK, self.ui.state())
            elif route == "/api/graph":
                self._json(HTTPStatus.OK, self.ui.graph())
            elif route == "/api/worktree":
                self._json(HTTPStatus.OK, self.ui.worktree())
            elif route == "/api/repos":
                self._json(HTTPStatus.OK, self.ui.repos())
            elif route == "/api/filediff":
                self._json(HTTPStatus.OK, self.ui.file_diff(
                    (query.get("path") or [""])[0], (query.get("staged") or ["0"])[0] == "1"))
            elif route == "/api/filehistory":
                self._json(HTTPStatus.OK, self.ui.file_history((query.get("path") or [""])[0]))
            elif route == "/api/export":
                body = self.ui.export()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="gitsquid-export.json"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route.startswith("/api/changes/"):
                self._json(HTTPStatus.OK, self.ui.change_detail(int(route.rsplit("/", 1)[1])))
            elif route.startswith("/api/commits/"):
                self._json(HTTPStatus.OK, self.ui.commit_detail(route.rsplit("/", 1)[1]))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Malformed identifier."})
        except Exception as exc:  # keep the UI usable when a command fails
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Only loopback requests are served."})
            return
        try:
            payload = self._body()
            route = parsed.path
            if route == "/api/index":
                self._json(HTTPStatus.OK, self.ui.reindex())
            elif route == "/api/repos/open":
                self._json(HTTPStatus.OK, self.ui.open_repo(payload))
            elif route == "/api/repos/forget":
                self._json(HTTPStatus.OK, self.ui.forget_repo(payload))
            elif route == "/api/propose":
                self._json(HTTPStatus.OK, self.ui.propose(payload))
            elif route == "/api/import":
                self._json(HTTPStatus.OK, self.ui.import_payload(payload))
            elif route.startswith("/api/samples/"):
                self._json(HTTPStatus.OK, self.ui.samples(route.rsplit("/", 1)[1]))
            elif route.startswith("/api/worktree/"):
                self._json(HTTPStatus.OK, self.ui.worktree_action(route.rsplit("/", 1)[1], payload))
            elif route.startswith("/api/changes/"):
                parts = route.removeprefix("/api/changes/").split("/")
                if len(parts) != 2:
                    raise ApiError("Expected /api/changes/<id>/<action>.", HTTPStatus.NOT_FOUND)
                self._json(HTTPStatus.OK, self.ui.act(int(parts[0]), parts[1], payload))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Malformed identifier."})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})


def serve(settings: Settings, *, port: int = 8756) -> UIServer:
    return UIServer(settings, port=port)
