from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .models import TestRun


def run_tests(
    repo: Path, command: str, *, timeout: int = 600, change_id: int | None = None
) -> TestRun:
    """Run the configured verification command in the repository and capture the outcome."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = completed.returncode
        output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code = 124
        output = f"Test command timed out after {timeout}s."
    except OSError as exc:
        exit_code = 127
        output = f"Could not run the test command: {exc}"

    duration_ms = int((time.monotonic() - started) * 1000)
    return TestRun(
        command=command,
        exit_code=exit_code,
        duration_ms=duration_ms,
        output_tail=output,
        change_id=change_id,
    )
