from __future__ import annotations

import json
import threading
from dataclasses import asdict
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__, clone, gitcmd, gitlog, history, refs, registry, worktree
from ..config import ConfigError, find_repo_root
from ..gitcmd import GitError, require_paths

ASSETS = Path(__file__).parent / "assets"
# What moves HEAD, rewrites history or touches a remote is recorded; staging noise is not.
MAX_BODY_BYTES = 2 * 1024 * 1024
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


class UIServer:
    """Serves the local interface. Loopback only — no account, no token, no network exposure."""

    def __init__(self, repo: Path, *, host: str = "127.0.0.1", port: int = 8756) -> None:
        self.repo = repo
        registry.add(repo)
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

    def repos(self) -> dict:
        return {
            "active": str(self.repo),
            "repos": [entry.as_dict() for entry in registry.known()],
        }

    def open_repo(self, payload: dict) -> dict:
        raw = _string(payload, "path").strip()
        if not raw:
            raise ApiError("A repository path is required.")
        try:
            root = registry.add(Path(raw).expanduser())
        except ConfigError as exc:
            raise ApiError(str(exc)) from exc
        with self._lock:
            self.repo = root
        return {"message": f"Opened {root.name}.", "active": str(root)}

    def clone_repo(self, payload: dict) -> dict:
        """Clone, register, and open — the three things you always want together."""
        url = _string(payload, "url")
        parent = _string(payload, "parent")
        try:
            target = clone.clone(url, Path(parent).expanduser(), name=str(payload.get("name") or ""))
            registry.add(target)
        except (GitError, ConfigError) as exc:
            raise ApiError(str(exc)) from exc
        with self._lock:
            self.repo = target
        return {"message": f"Cloned into {target}.", "active": str(target)}

    def forget_repo(self, payload: dict) -> dict:
        raw = _string(payload, "path").strip()
        if Path(raw).expanduser().resolve() == self.repo:
            raise ApiError("Close this repository by opening another one first.")
        if not registry.remove(raw):
            raise ApiError("That repository is not in the list.", HTTPStatus.NOT_FOUND)
        return {"message": "Removed from the list. Nothing on disk was touched."}

    def state(self) -> dict:
        return {"repo": self._repository(), "config": self._configuration()}

    def _repository(self) -> dict:
        repo = self.repo
        return {
            "name": repo.name,
            "path": str(repo),
            "branch": gitlog.current_branch(repo),
            "head": gitlog.head(repo),
            "branches": gitlog.branches(repo),
            "remote_branches": gitlog.remote_branches(repo),
            "tags": gitlog.tags(repo),
            "status": gitlog.working_status(repo),
            "remotes": worktree.remotes(repo),
            "tracking": worktree.tracking(repo),
            "stashes": worktree.stash_list(repo),
            "operation": gitlog.pending_operation(repo),
            "head_message": worktree.head_message(repo),
        }

    def _configuration(self) -> dict:
        return {"version": __version__, "git_version": gitcmd.git_version()}

    def graph(self, limit: int = 80, *, every_ref: bool = True) -> dict:
        limit = max(10, min(limit, 5000))
        found = gitlog.commits(self.repo, limit=limit, every_ref=every_ref)
        return {
            "commits": [asdict(commit) | {"short": commit.short} for commit in found],
            "limit": limit,
            "every_ref": every_ref,
            "total_commits": gitlog.count_commits(self.repo, every_ref=every_ref),
        }

    def worktree(self) -> dict:
        entries = worktree.status(self.repo)
        counts = worktree.line_counts(self.repo)
        return {
            "files": [entry.as_dict() | {"counts": counts.get(entry.path, {})} for entry in entries],
            "staged": sum(1 for entry in entries if entry.staged),
            "unstaged": sum(1 for entry in entries if entry.unstaged or entry.untracked),
            "conflicted": sum(1 for entry in entries if entry.conflicted),
        }

    def file_diff(self, path: str, staged: bool, ignore_whitespace: bool = False,
                  context: int = 3) -> dict:
        try:
            return {"path": path, "staged": staged,
                    "diff": worktree.file_diff(self.repo, path, staged=staged,
                                               ignore_whitespace=ignore_whitespace,
                                               context=context)}
        except GitError as exc:
            raise ApiError(str(exc)) from exc

    def blame(self, path: str, rev: str) -> dict:
        try:
            return gitlog.blame(self.repo, path, rev=rev)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

    def file_history(self, path: str) -> dict:
        try:
            require_paths([path])
        except GitError as exc:
            raise ApiError(str(exc)) from exc
        return {"path": path, "commits": gitlog.file_history(self.repo, path)}

    def worktree_action(self, action: str, payload: dict) -> dict:
        handlers = _handlers(self.repo, payload)
        with self._lock:
            if action == "commit":
                return self._commit(payload)
            handler = handlers.get(action)
            if handler is None:
                raise ApiError(f"Unknown action: {action}", HTTPStatus.NOT_FOUND)
            try:
                return {"message": handler()}
            except GitError as exc:
                raise ApiError(str(exc)) from exc

    def _commit(self, payload: dict) -> dict:
        amend = bool(payload.get("amend"))
        try:
            result = worktree.commit(self.repo, _string(payload, "message"), amend=amend)
        except GitError as exc:
            raise ApiError(str(exc)) from exc
        return {"message": f"{'Amended' if amend else 'Committed'} {result['short']}.", "commit": result}

    def search(self, query: str) -> dict:
        rows = [asdict(commit) | {"short": commit.short}
                for commit in gitlog.search(self.repo, query[:200])]
        return {"query": query, "commits": rows}

    def commit_detail(self, sha: str) -> dict:
        try:
            return gitlog.commit_detail(self.repo, sha)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

    def commit_patch(self, sha: str, path: str, ignore_whitespace: bool = False,
                     context: int = 3) -> dict:
        try:
            return gitlog.commit_patch(self.repo, sha, path or None,
                                       ignore_whitespace=ignore_whitespace, context=context)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc


def _worktree_actions(repo: Path, payload: dict, paths: list[str]) -> dict:
    """The working tree and the index."""
    return {
        "stage": lambda: worktree.stage(repo, paths),
        "unstage": lambda: worktree.unstage(repo, paths),
        "discard": lambda: worktree.discard(repo, paths),
        "ignore": lambda: worktree.ignore(repo, paths),
        "resolve": lambda: worktree.resolve(repo, paths, side=str(payload.get("side") or "")),
        "stage-hunk": lambda: worktree.apply_patch(repo, _string(payload, "patch"), target="stage"),
        "unstage-hunk": lambda: worktree.apply_patch(repo, _string(payload, "patch"), target="unstage"),
        "discard-hunk": lambda: worktree.apply_patch(repo, _string(payload, "patch"), target="discard"),
        "stash": lambda: worktree.stash_save(repo, str(payload.get("message") or "")),
        "stash-pop": lambda: worktree.stash_pop(repo, _string(payload, "ref")),
        "stash-apply": lambda: worktree.stash_apply(repo, _string(payload, "ref")),
        "stash-drop": lambda: worktree.stash_drop(repo, _string(payload, "ref")),
        "stash-branch": lambda: worktree.stash_branch(repo, _string(payload, "ref"), _string(payload, "name")),
    }


def _branch_actions(repo: Path, payload: dict, branch, name) -> dict:
    """Branches, tags, and the remotes they travel to."""
    return {
        "checkout": lambda: worktree.checkout(repo, branch()),
        "branch": lambda: worktree.create_branch(repo, name()),
        "merge": lambda: worktree.merge(repo, branch(), squash=bool(payload.get("squash"))),
        "delete-branch": lambda: worktree.delete_branch(repo, branch(), force=bool(payload.get("force"))),
        "rename-branch": lambda: refs.rename_branch(repo, branch(), name()),
        "push-branch": lambda: refs.push_branch(repo, branch()),
        "checkout-remote": lambda: refs.track_remote_branch(repo, branch()),
        "delete-remote-branch": lambda: refs.delete_remote_branch(repo, branch()),
        "tag-create": lambda: refs.create_tag(
            repo, name(), sha=str(payload.get("sha") or ""), message=str(payload.get("message") or "")
        ),
        "tag-delete": lambda: refs.delete_tag(repo, name()),
        "tag-push": lambda: refs.push_tag(repo, name()),
        "remote-add": lambda: refs.add_remote(repo, name(), _string(payload, "url")),
        "remote-remove": lambda: refs.remove_remote(repo, name()),
        "fetch": lambda: worktree.fetch(repo),
        "pull": lambda: worktree.pull(repo),
        "push": lambda: worktree.push(repo, force=bool(payload.get("force"))),
    }


def _history_actions(repo: Path, payload: dict, sha, name) -> dict:
    """Everything that moves HEAD or replays a commit."""
    return {
        "checkout-commit": lambda: history.checkout_commit(repo, sha()),
        "branch-from": lambda: history.branch_from(repo, sha(), name()),
        "restore-file": lambda: history.restore_file(repo, sha(), _string(payload, "path")),
        "cherry-pick": lambda: history.cherry_pick(repo, sha()),
        "revert-commit": lambda: history.revert_commit(repo, sha()),
        "reset": lambda: history.reset(repo, sha(), mode=str(payload.get("mode") or "mixed")),
        "rebase": lambda: history.rebase(repo, _string(payload, "target")),
        "abort": lambda: history.abort(repo),
        "continue": lambda: history.resume(repo),
        "skip": lambda: history.skip(repo),
    }


def _handlers(repo: Path, payload: dict) -> dict:
    """One name per action, grouped by the module that owns it."""
    paths = payload.get("paths") or []
    def field(key: str):
        """Read a payload field only when the action that needs it actually runs."""
        return lambda: _string(payload, key)

    branch, name, sha = field("branch"), field("name"), field("sha")
    return (
        _worktree_actions(repo, payload, paths)
        | _branch_actions(repo, payload, branch, name)
        | _history_actions(repo, payload, sha, name)
    )


def _one(query: dict, key: str, default: str = "") -> str:
    return (query.get(key) or [default])[0]


def _int(query: dict, key: str, default: int) -> int:
    raw = _one(query, key)
    return int(raw) if raw.isdigit() else default


def _tail(route: str, prefix: str) -> list[str]:
    return route.removeprefix(prefix).split("/")


# A route is (ui, route, argument) -> payload; the argument is the query for GET, the body for
# POST. Prefixed routes end with "/" and match anything under them.
GET_ROUTES = {
    "/api/state": lambda ui, route, query: ui.state(),
    "/api/graph": lambda ui, route, query: ui.graph(
        _int(query, "limit", 80), every_ref=_one(query, "refs", "all") != "head"
    ),
    "/api/worktree": lambda ui, route, query: ui.worktree(),
    "/api/repos": lambda ui, route, query: ui.repos(),
    "/api/search": lambda ui, route, query: ui.search(_one(query, "q")),
    "/api/filehistory": lambda ui, route, query: ui.file_history(_one(query, "path")),
    "/api/blame": lambda ui, route, query: ui.blame(_one(query, "path"), _one(query, "rev")),
    "/api/filediff": lambda ui, route, query: ui.file_diff(
        _one(query, "path"), _one(query, "staged") == "1", _one(query, "ws") == "1",
        _int(query, "ctx", 3),
    ),
    "/api/commits/": lambda ui, route, query: _commit_route(ui, _tail(route, "/api/commits/"), query),
}

POST_ROUTES = {
    "/api/repos/open": lambda ui, route, payload: ui.open_repo(payload),
    "/api/repos/clone": lambda ui, route, payload: ui.clone_repo(payload),
    "/api/repos/forget": lambda ui, route, payload: ui.forget_repo(payload),
    "/api/worktree/": lambda ui, route, payload: ui.worktree_action(
        _tail(route, "/api/worktree/")[0], payload
    ),
}


def _prefixed(routes: dict, route: str):
    for prefix, handler in routes.items():
        if prefix.endswith("/") and route.startswith(prefix):
            return handler
    return None


def _commit_route(ui, parts: list[str], query: dict) -> dict:
    if len(parts) == 1:
        return ui.commit_detail(parts[0])
    if len(parts) == 2 and parts[1] == "patch":
        return ui.commit_patch(
            parts[0], _one(query, "path"), _one(query, "ws") == "1", _int(query, "ctx", 3)
        )
    raise ApiError("Expected /api/commits/<sha>[/patch].", HTTPStatus.NOT_FOUND)


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
            "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'",
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

    def _dispatch(self, routes, route, argument) -> None:
        """Answer with JSON, turning every failure into a status the interface can show."""
        try:
            handler = routes.get(route) or _prefixed(routes, route)
            if handler is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            self._json(HTTPStatus.OK, handler(self.ui, route, argument))
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Malformed identifier."})
        except Exception as exc:  # keep the interface usable when a command fails
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Only loopback requests are served."})
            return
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/":
            self._asset("index.html")
        elif route.startswith("/assets/"):
            self._asset(route.removeprefix("/assets/"))
        else:
            self._dispatch(GET_ROUTES, route, parse_qs(parsed.query))

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Only loopback requests are served."})
            return
        try:
            payload = self._body()
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
            return
        self._dispatch(POST_ROUTES, urlparse(self.path).path, payload)


def serve(repo: Path, *, port: int = 8756) -> UIServer:
    return UIServer(repo, port=port)
