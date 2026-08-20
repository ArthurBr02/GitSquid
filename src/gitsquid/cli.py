from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import NoReturn

import typer

from . import __version__, gitcmd, gitlog, registry, ui
from .config import ConfigError, find_repo_root
from .phrasing import plural

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help="GitSquid — a Git client for one repository at a time.",
)

EXIT_FAILURE = 1
EXIT_INVALID = 2


def _abort(exc: Exception, code: int) -> NoReturn:
    """Report a failure the way the terminal expects, and stop there."""
    ui.fail(str(exc))
    raise typer.Exit(code) from exc


def _resolve_repo(repo: Path | None) -> Path:
    """Without --repo the interface reopens the repository you were last on, wherever you are."""
    if repo is not None:
        try:
            return find_repo_root(repo)
        except ConfigError as exc:
            _abort(exc, EXIT_INVALID)

    for entry in registry.known():  # newest first: the head is the last repository opened
        if entry.exists:
            return entry.path
    try:
        return find_repo_root(None)
    except ConfigError as exc:
        ui.fail(str(exc))
        ui.info("Open one first: `gitsquid ui --repo /path/to/repository`.")
        raise typer.Exit(EXIT_INVALID) from exc


@app.command()
def doctor(
    repo: Path = typer.Option(None, "--repo", help="Repository to inspect (default: current)."),
) -> None:
    """Show which repository would open, and what GitSquid is made of."""
    root = _resolve_repo(repo)
    known = registry.known()
    ui.kv(
        [
            ("Repository", str(root)),
            ("Branch", gitlog.current_branch(root)),
            ("Commits", f"{gitlog.count_commits(root):,}"),
            ("Registered", plural(len(known), "repository", many="repositories")),
            ("Repository list", str(registry.registry_path())),
            ("GitSquid", __version__),
            ("git", gitcmd.git_version()),
        ],
        title="GitSquid status",
    )


@app.command(name="ui")
def ui_command(
    port: int = typer.Option(8756, "--port", "-p", min=1024, max=65535, help="Loopback port."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page automatically."),
    repo: Path = typer.Option(None, "--repo", help="Repository to open (default: the last one)."),
) -> None:
    """Serve the interface on localhost."""
    from .web import UIServer

    root = _resolve_repo(repo)
    try:
        server = UIServer(root, port=port)
    except OSError as exc:
        ui.fail(f"Could not bind port {port}: {exc}")
        ui.info(f"Another port is free: `gitsquid ui --port {port + 1}`.")
        raise typer.Exit(EXIT_FAILURE) from exc

    ui.ok(f"GitSquid is serving {root.name} at {server.url}")
    if repo is None and root != Path.cwd().resolve():
        ui.info("That is the repository you were last on. `--repo .` opens this folder instead.")
    ui.info("Loopback only — nothing outside this machine can reach it.")
    ui.info("Switch or add repositories from the interface. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        ui.info("Stopped.")
    finally:
        server.shutdown()


def main() -> None:
    app()
