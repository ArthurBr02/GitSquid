from __future__ import annotations

import sqlite3
from pathlib import Path

import typer

from . import diffs, indexer, portability, sampledata, ui
from .config import ConfigError, Settings, load_settings
from .db import SchemaError, open_db
from .llm import AnthropicBackend, PatchFileBackend, ProposalBackend, ProposalError
from .models import Change, ChangeRepo, ChangeStatus, EventLog, TestRunRepo, ValidationError
from .retrieval import search as search_chunks
from .safety import clean_text_input
from .workflow import ChangeService, WorkflowError

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help="GitSquid — index one repository, propose diffs, run tests, record every change.",
)
sample_app = typer.Typer(no_args_is_help=True, help="Load or delete the labelled sample records.")
app.add_typer(sample_app, name="sample")

EXIT_FAILURE = 1
EXIT_INVALID = 2


def _settings(repo: Path | None = None) -> Settings:
    try:
        return load_settings(repo)
    except ConfigError as exc:
        ui.fail(str(exc))
        raise typer.Exit(EXIT_INVALID) from exc


def _open(settings: Settings, *, create: bool = False) -> sqlite3.Connection:
    if not create and not settings.initialized:
        ui.empty(
            f"No GitSquid database in {settings.repo}.",
            hint="run `gitsquid init` to create .gitsquid/gitsquid.db",
        )
        raise typer.Exit(EXIT_INVALID)
    try:
        return open_db(settings.db_path, create=True)
    except (SchemaError, sqlite3.Error) as exc:
        ui.fail(str(exc))
        raise typer.Exit(EXIT_FAILURE) from exc


def _require_change(repo: ChangeRepo, change_id: int) -> Change:
    change = repo.get(change_id)
    if change is None:
        ui.fail(f"No change #{change_id}. Run `gitsquid log` to list what is recorded.")
        raise typer.Exit(EXIT_INVALID)
    return change


def _confirm(question: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    ui.info(f"{question}  [y/N]")
    return typer.confirm("Confirm", default=False)


@app.command()
def init(
    repo: Path = typer.Option(None, "--repo", help="Repository to index (default: current)."),
) -> None:
    """Create the local database for this repository."""
    settings = _settings(repo)
    existed = settings.initialized
    conn = _open(settings, create=True)
    EventLog(conn).record("init", f"database ready at {settings.db_path}")
    conn.close()

    if existed:
        ui.ok(f"Database already present at {settings.db_path}")
    else:
        ui.ok(f"Created {settings.db_path}")
    ui.info("Next: `gitsquid index` to build the local index, then `gitsquid doctor`.")


@app.command()
def doctor(
    repo: Path = typer.Option(None, "--repo", help="Repository to inspect (default: current)."),
) -> None:
    """Show configuration, data location, and whether model access is available."""
    settings = _settings(repo)
    rows = [
        ("Repository", str(settings.repo)),
        ("Database", str(settings.db_path)),
        ("Initialized", "yes" if settings.initialized else "no — run `gitsquid init`"),
        ("Model", settings.model),
        ("Effort", settings.effort),
        ("Context budget", f"{settings.max_context_chars:,} characters"),
        ("Test command", settings.test_command),
        (
            "Model access",
            "ANTHROPIC_API_KEY set" if settings.model_available else "absent — degraded mode",
        ),
    ]
    if settings.initialized:
        conn = _open(settings)
        summary = indexer.index_summary(conn)
        changes = ChangeRepo(conn)
        rows += [
            ("Indexed files", f"{summary['files']:,} ({summary['chunks']:,} chunks)"),
            ("Recorded changes", f"{changes.count():,}"),
            ("Sample records", f"{sampledata.count(conn):,}"),
        ]
        conn.close()
    ui.kv(rows, title="GitSquid status")

    if not settings.model_available:
        ui.warn(
            "Degraded mode: `gitsquid propose` cannot call a model. Everything else works, and "
            "`gitsquid propose --patch-file p.diff` still validates, applies, tests and records "
            "a diff you wrote yourself."
        )


@app.command()
def index(
    force: bool = typer.Option(False, "--force", help="Re-chunk every file, ignoring hashes."),
    repo: Path = typer.Option(None, "--repo", help="Repository to index (default: current)."),
) -> None:
    """Index the repository into the local database."""
    settings = _settings(repo)
    conn = _open(settings)
    with ui.working(f"Indexing {settings.repo}…"):
        stats = indexer.index_repo(
            conn, settings.repo, max_file_bytes=settings.max_file_bytes, force=force
        )
    EventLog(conn).record(
        "indexed", f"{stats.files_indexed} indexed, {stats.chunks} chunks, {stats.files_removed} removed"
    )
    summary = indexer.index_summary(conn)
    conn.close()

    if summary["files"] == 0:
        ui.empty(
            "Nothing indexable found.",
            hint="gitsquid skips binaries, credential files, and anything over GITSQUID_MAX_FILE_BYTES.",
        )
        return
    ui.ok(
        f"{stats.files_indexed} file(s) indexed, {stats.files_unchanged} unchanged, "
        f"{stats.files_skipped} skipped, {stats.files_removed} removed."
    )
    ui.info(f"Index now holds {summary['files']:,} files and {summary['chunks']:,} chunks.")


@app.command()
def search(
    query: str = typer.Argument(..., help="Words to look for in the indexed code."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=100),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Search the index (this is exactly the retrieval a proposal gets)."""
    settings = _settings(repo)
    conn = _open(settings)
    try:
        cleaned = clean_text_input(query, max_len=500, field="query")
    except ValueError as exc:
        ui.invalid("Invalid search query", [str(exc)])
        raise typer.Exit(EXIT_INVALID) from exc

    hits = search_chunks(conn, cleaned, limit=limit)
    conn.close()
    if not hits:
        ui.empty(
            f"No indexed chunk matches {cleaned!r}.",
            hint="run `gitsquid index` if the repository changed, or try different words.",
        )
        return
    for hit in hits:
        ui.panel(hit.label, hit.content.rstrip("\n"))
    ui.ok(f"{len(hits)} match(es).")


def _backend(settings: Settings, patch_file: Path | None) -> ProposalBackend:
    if patch_file is not None:
        if not patch_file.exists():
            ui.fail(f"No such patch file: {patch_file}")
            raise typer.Exit(EXIT_INVALID)
        return PatchFileBackend(patch_file.read_text(encoding="utf-8", errors="replace"))
    if not settings.model_available:
        ui.fail("No ANTHROPIC_API_KEY, so no model can be called.")
        ui.info(
            "Degraded mode: write the diff yourself and pass `--patch-file p.diff`. "
            "GitSquid will still validate, apply, test, and record it."
        )
        raise typer.Exit(EXIT_INVALID)
    return AnthropicBackend(settings)


def _render_proposal(outcome, *, plain: bool) -> None:
    change = outcome.change
    ui.kv(
        [
            ("Change", f"#{change.id}"),
            ("Source", str(change.source)),
            ("Model", change.model or "none (patch file)"),
            ("Files", ", ".join(change.files_touched) or "none"),
            ("Context", f"{outcome.context.chars:,} chars from {len(outcome.context.files)} file(s)"),
        ],
        title="Proposal",
    )
    if change.rationale:
        ui.panel("Rationale", change.rationale)
    ui.show_diff(change.diff, plain=plain)


@app.command()
def propose(
    task: str = typer.Argument(..., help="What the change should do."),
    patch_file: Path = typer.Option(
        None, "--patch-file", help="Use this unified diff instead of calling a model."
    ),
    file: list[str] = typer.Option(
        None, "--file", "-f", help="Always include this indexed path in the context."
    ),
    plain: bool = typer.Option(False, "--plain", help="Print the diff without syntax colouring."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Propose a diff for a task and record it. Nothing is written to your files."""
    settings = _settings(repo)
    conn = _open(settings)
    service = ChangeService(conn, settings)
    backend = _backend(settings, patch_file)

    try:
        cleaned = clean_text_input(task, max_len=2000, field="task")
    except ValueError as exc:
        ui.invalid("Invalid task", [str(exc)])
        raise typer.Exit(EXIT_INVALID) from exc

    try:
        with ui.working(f"Proposing a diff with {backend.name}…"):
            outcome = service.propose(cleaned, backend, pinned=list(file or []))
    except (ProposalError, ValidationError) as exc:
        ui.fail(str(exc))
        conn.close()
        raise typer.Exit(EXIT_FAILURE) from exc

    _render_proposal(outcome, plain=plain)
    if outcome.applies_cleanly:
        ui.ok(f"Recorded as change #{outcome.change.id} and it applies cleanly.")
        ui.info(f"Next: `gitsquid apply {outcome.change.id}` then `gitsquid test {outcome.change.id}`.")
    else:
        ui.invalid(
            f"Recorded as change #{outcome.change.id}, but git refuses it:",
            [outcome.check_message],
        )
    conn.close()


@app.command()
def show(
    change_id: int = typer.Argument(..., help="Change id from `gitsquid log`."),
    plain: bool = typer.Option(False, "--plain", help="Print the diff without syntax colouring."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Show one recorded change with its diff, test runs, and audit trail."""
    settings = _settings(repo)
    conn = _open(settings)
    change = _require_change(ChangeRepo(conn), change_id)
    added, removed = diffs.stats(change.diff)

    ui.kv(
        [
            ("Change", f"#{change.id}{'  (SAMPLE)' if change.is_sample else ''}"),
            ("Status", str(change.status)),
            ("Task", change.task),
            ("Source", str(change.source)),
            ("Model", change.model or "none"),
            ("Files", ", ".join(change.files_touched) or "none"),
            ("Lines", f"+{added} / -{removed}"),
            ("Digest", change.short_sha),
            ("Base commit", (change.base_commit or "unknown")[:12]),
            ("Created", change.created_at),
            ("Applied", change.applied_at or "never"),
        ],
        title=f"Change #{change.id}",
    )
    if change.rationale:
        ui.panel("Rationale", change.rationale)
    ui.show_diff(change.diff, plain=plain)

    for run in TestRunRepo(conn).for_change(change.id):
        label = "passed" if run.passed else f"failed (exit {run.exit_code})"
        ui.panel(f"Test run {run.created_at} — {label}", run.output_tail or "(no output)")
    trail = EventLog(conn).for_change(change.id)
    if trail:
        ui.panel(
            "Audit trail",
            "\n".join(f"{event.created_at}  {event.kind}: {event.message}" for event in trail),
        )
    conn.close()


@app.command()
def apply(
    change_id: int = typer.Argument(..., help="Change id from `gitsquid log`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Apply a recorded diff to the working tree."""
    settings = _settings(repo)
    conn = _open(settings)
    service = ChangeService(conn, settings)
    change = _require_change(service.changes, change_id)

    if change.is_sample:
        ui.invalid(
            f"Change #{change.id} is sample data.",
            ["Sample records describe a fictional repository and are never applied."],
        )
        conn.close()
        raise typer.Exit(EXIT_INVALID)

    if diffs.working_tree_dirty(settings.repo):
        ui.warn("The working tree has uncommitted changes; `gitsquid revert` may not undo cleanly.")
    if not _confirm(
        f"Apply change #{change.id} to {', '.join(change.files_touched) or 'the working tree'}?",
        assume_yes=yes,
    ):
        ui.info("Nothing applied.")
        conn.close()
        raise typer.Exit(0)

    try:
        service.apply(change)
    except WorkflowError as exc:
        ui.fail(str(exc))
        conn.close()
        raise typer.Exit(EXIT_FAILURE) from exc
    ui.ok(f"Change #{change.id} applied to {settings.repo}.")
    ui.info(f"Next: `gitsquid test {change.id}` to verify, or `gitsquid revert {change.id}` to undo.")
    conn.close()


@app.command()
def test(
    change_id: int = typer.Argument(None, help="Change to attach the run to (default: latest)."),
    command: str = typer.Option(None, "--command", "-c", help="Override GITSQUID_TEST_COMMAND."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Run the verification command and record the result against a change."""
    settings = _settings(repo)
    conn = _open(settings)
    service = ChangeService(conn, settings)

    change = (
        _require_change(service.changes, change_id) if change_id is not None else service.changes.latest()
    )
    if change is None:
        ui.empty("No change recorded yet.", hint="run `gitsquid propose \"...\"` first.")
        conn.close()
        raise typer.Exit(EXIT_INVALID)

    used = command or settings.test_command
    with ui.working(f"Running `{used}`…"):
        run = service.verify(change, command=used)
    conn.close()

    if run.passed:
        ui.ok(f"`{used}` passed in {run.duration_ms}ms — change #{change.id} is verified.")
        return
    ui.fail(f"`{used}` exited {run.exit_code} after {run.duration_ms}ms.")
    ui.panel("Output tail", run.output_tail or "(no output)")
    ui.info(f"Undo with `gitsquid revert {change.id}`.")
    raise typer.Exit(EXIT_FAILURE)


@app.command()
def revert(
    change_id: int = typer.Argument(..., help="Change id from `gitsquid log`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Reverse an applied diff and record the reversal."""
    settings = _settings(repo)
    conn = _open(settings)
    service = ChangeService(conn, settings)
    change = _require_change(service.changes, change_id)

    if not _confirm(f"Reverse change #{change.id} in the working tree?", assume_yes=yes):
        ui.info("Nothing reverted.")
        conn.close()
        raise typer.Exit(0)
    try:
        service.revert(change)
    except WorkflowError as exc:
        ui.fail(str(exc))
        conn.close()
        raise typer.Exit(EXIT_FAILURE) from exc
    ui.ok(f"Change #{change.id} reversed.")
    conn.close()


@app.command(name="log")
def log_command(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=500),
    status: str = typer.Option(None, "--status", help="proposed|applied|verified|failed|reverted"),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """List recorded changes, newest first."""
    settings = _settings(repo)
    conn = _open(settings)
    filter_status = None
    if status:
        try:
            filter_status = ChangeStatus(status.lower())
        except ValueError as exc:
            ui.invalid(
                f"Unknown status {status!r}.",
                [f"Use one of: {', '.join(s.value for s in ChangeStatus)}"],
            )
            conn.close()
            raise typer.Exit(EXIT_INVALID) from exc

    changes = ChangeRepo(conn).list(limit=limit, status=filter_status)
    conn.close()
    if not changes:
        ui.empty(
            "No change recorded yet.",
            hint='gitsquid propose "describe the change you want" — or `gitsquid sample load` to see the shape of the data.',
        )
        return
    ui.changes_table(changes)


@app.command(name="run")
def run_command(
    task: str = typer.Argument(..., help="What the change should do."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply without a confirmation prompt."),
    patch_file: Path = typer.Option(None, "--patch-file", help="Use this diff instead of a model."),
    file: list[str] = typer.Option(None, "--file", "-f", help="Pin an indexed path into context."),
    skip_tests: bool = typer.Option(False, "--skip-tests", help="Apply but do not verify."),
    revert_on_failure: bool = typer.Option(
        False, "--revert-on-failure", help="Reverse the patch automatically if the tests fail."
    ),
    plain: bool = typer.Option(False, "--plain"),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """The core loop: index, propose, apply, test, record — in one command."""
    settings = _settings(repo)
    conn = _open(settings)
    service = ChangeService(conn, settings)
    backend = _backend(settings, patch_file)

    try:
        cleaned = clean_text_input(task, max_len=2000, field="task")
    except ValueError as exc:
        ui.invalid("Invalid task", [str(exc)])
        conn.close()
        raise typer.Exit(EXIT_INVALID) from exc

    with ui.working("Refreshing the index…"):
        stats = indexer.index_repo(conn, settings.repo, max_file_bytes=settings.max_file_bytes)
    ui.ok(f"Index up to date ({stats.files_indexed} re-indexed, {stats.files_unchanged} unchanged).")

    try:
        with ui.working(f"Proposing a diff with {backend.name}…"):
            outcome = service.propose(cleaned, backend, pinned=list(file or []))
    except (ProposalError, ValidationError) as exc:
        ui.fail(str(exc))
        conn.close()
        raise typer.Exit(EXIT_FAILURE) from exc

    _render_proposal(outcome, plain=plain)
    change = outcome.change
    if not outcome.applies_cleanly:
        ui.invalid(f"Change #{change.id} recorded, but git refuses it:", [outcome.check_message])
        conn.close()
        raise typer.Exit(EXIT_FAILURE)

    if not _confirm(f"Apply change #{change.id}?", assume_yes=yes):
        ui.info(f"Stopped before applying. The proposal is kept as change #{change.id}.")
        conn.close()
        raise typer.Exit(0)

    try:
        service.apply(change)
    except WorkflowError as exc:
        ui.fail(str(exc))
        conn.close()
        raise typer.Exit(EXIT_FAILURE) from exc
    ui.ok(f"Applied change #{change.id}.")

    if skip_tests:
        ui.warn(f"Tests skipped. Verify later with `gitsquid test {change.id}`.")
        conn.close()
        return

    with ui.working(f"Running `{settings.test_command}`…"):
        run = service.verify(change)
    if run.passed:
        ui.ok(f"Tests passed in {run.duration_ms}ms — change #{change.id} is verified.")
        conn.close()
        return

    ui.fail(f"Tests exited {run.exit_code}.")
    ui.panel("Output tail", run.output_tail or "(no output)")
    if revert_on_failure:
        try:
            service.revert(change)
            ui.ok(f"Change #{change.id} reversed automatically.")
        except WorkflowError as exc:
            ui.fail(str(exc))
    else:
        ui.info(f"The change is still applied. Undo with `gitsquid revert {change.id}`.")
    conn.close()
    raise typer.Exit(EXIT_FAILURE)


def _resolve_ui_repo(repo: Path | None) -> Settings:
    """Without --repo the interface reopens the repository you were last on, wherever you are."""
    from . import registry

    if repo is not None:
        try:
            return load_settings(repo)
        except ConfigError as exc:
            ui.fail(str(exc))
            raise typer.Exit(EXIT_INVALID) from exc

    for entry in registry.known():  # newest first: the head is the last repository opened
        if entry.exists:
            return load_settings(entry.path)
    try:
        return load_settings(None)
    except ConfigError as exc:
        ui.fail(str(exc))
        ui.info("Open one first: `gitsquid ui --repo /path/to/repository`.")
        raise typer.Exit(EXIT_INVALID) from exc


@app.command(name="ui")
def ui_command(
    port: int = typer.Option(8756, "--port", "-p", min=1024, max=65535, help="Loopback port."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page automatically."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Serve the graph interface on localhost."""
    import webbrowser

    from . import registry
    from .web import UIServer

    settings = _resolve_ui_repo(repo)
    _open(settings, create=True).close()

    try:
        server = UIServer(settings, port=port)
    except OSError as exc:
        ui.fail(f"Could not bind port {port}: {exc}")
        ui.info(f"Another port is free: `gitsquid ui --port {port + 1}`.")
        raise typer.Exit(EXIT_FAILURE) from exc

    ui.ok(f"GitSquid is serving {settings.repo.name} at {server.url}")
    if repo is None and settings.repo != Path.cwd().resolve():
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


@app.command(name="export")
def export_command(
    path: Path = typer.Argument(..., help="Destination .json file."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Export every recorded change, test run, and event to portable JSON."""
    settings = _settings(repo)
    conn = _open(settings)
    count = portability.export_to_file(conn, path, repo_name=settings.repo.name)
    conn.close()
    if count == 0:
        ui.empty(f"Wrote {path}, but there was nothing to export yet.")
        return
    ui.ok(f"Exported {count} change(s) to {path}.")


@app.command(name="import")
def import_command(
    path: Path = typer.Argument(..., help="A GitSquid export .json file."),
    repo: Path = typer.Option(None, "--repo"),
) -> None:
    """Import changes from a GitSquid export, skipping ones already recorded."""
    settings = _settings(repo)
    conn = _open(settings)
    try:
        stats = portability.import_from_file(conn, path)
    except portability.ImportError_ as exc:
        ui.invalid("Import rejected", [str(exc)])
        conn.close()
        raise typer.Exit(EXIT_INVALID) from exc
    conn.close()
    ui.ok(
        f"Imported {stats.changes} change(s), {stats.test_runs} test run(s); "
        f"{stats.skipped} already present."
    )


@sample_app.command("load")
def sample_load(repo: Path = typer.Option(None, "--repo")) -> None:
    """Insert clearly labelled sample records so empty screens have something to show."""
    settings = _settings(repo)
    conn = _open(settings)
    if sampledata.count(conn) > 0:
        ui.warn("Sample records are already loaded. `gitsquid sample clear` removes them first.")
        conn.close()
        raise typer.Exit(0)
    created = sampledata.load(conn)
    conn.close()
    ui.ok(f"Loaded {created} sample change(s), each flagged SAMPLE.")
    ui.info("Remove them at any time with `gitsquid sample clear`.")


@sample_app.command("clear")
def sample_clear(repo: Path = typer.Option(None, "--repo")) -> None:
    """Delete every sample record. Your own changes are untouched."""
    settings = _settings(repo)
    conn = _open(settings)
    removed = sampledata.clear(conn)
    conn.close()
    if not removed["changes"]:
        ui.empty("No sample record to delete.")
        return
    ui.ok(
        f"Deleted {removed['changes']} sample change(s), {removed['test_runs']} test run(s), "
        f"{removed['events']} event(s)."
    )


if __name__ == "__main__":
    app()
