from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


# Every state carries a text token as well as a colour, so nothing depends on colour alone.
STATE_TOKENS = {
    "ok": ("[ok]", "bold green"),
    "fail": ("[fail]", "bold red"),
    "warn": ("[warn]", "bold yellow"),
    "info": ("[info]", "bold cyan"),
    "wait": ("[working]", "bold blue"),
    "empty": ("[empty]", "bold magenta"),
    "invalid": ("[invalid]", "bold red"),
}



def _color_enabled() -> bool:
    return not (os.environ.get("NO_COLOR") or os.environ.get("GITSQUID_NO_COLOR"))


def _width() -> int | None:
    """Honour COLUMNS; when output is piped use 120 so paths and tasks stay readable."""
    override = os.environ.get("COLUMNS", "")
    if override.isdigit() and int(override) > 20:
        return int(override)
    return None if sys.stdout.isatty() else 120


def console(*, stderr: bool = False) -> Console:
    # Built per call so NO_COLOR and COLUMNS are read from the live environment.
    return Console(
        stderr=stderr,
        no_color=not _color_enabled(),
        highlight=False,
        soft_wrap=False,
        width=_width(),
    )


def _line(kind: str, message: str, *, stderr: bool = False) -> None:
    token, style = STATE_TOKENS[kind]
    console(stderr=stderr).print(Text(token, style=style), Text(message))


def ok(message: str) -> None:
    _line("ok", message)


def info(message: str) -> None:
    _line("info", message)


def warn(message: str) -> None:
    _line("warn", message, stderr=True)


def fail(message: str) -> None:
    _line("fail", message, stderr=True)


def empty(message: str, hint: str = "") -> None:
    _line("empty", message)
    if hint:
        console().print(Text(f"  next: {hint}", style="dim"))


def invalid(title: str, problems: list[str]) -> None:
    _line("invalid", title, stderr=True)
    target = console(stderr=True)
    for problem in problems:
        target.print(Text(f"  - {problem}"))


@contextmanager
def working(message: str) -> Iterator[None]:
    """Loading state: a labelled spinner on a TTY, a plain line everywhere else."""
    token, style = STATE_TOKENS["wait"]
    target = console()
    if target.is_terminal and _color_enabled():
        with target.status(Text(f"{token} {message}", style=style), spinner="line"):
            yield
    else:
        target.print(Text(token, style=style), Text(message))
        yield


def show_diff(diff: str, *, plain: bool = False) -> None:
    if plain or not _color_enabled():
        console().print(Text(diff))
        return
    console().print(Syntax(diff, "diff", theme="ansi_dark", word_wrap=False))


def panel(title: str, body: str) -> None:
    console().print(Panel(Text(body), title=title, title_align="left", border_style="dim"))


def kv(rows: list[tuple[str, str]], *, title: str = "") -> None:
    table = Table(box=None, show_header=False, title=title or None, title_justify="left")
    table.add_column("Field", style="bold", no_wrap=True)
    table.add_column("Value", overflow="fold")
    for key, value in rows:
        table.add_row(key, value)
    console().print(table)
