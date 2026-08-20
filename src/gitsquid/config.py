from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ConfigError(Exception):
    pass


def find_repo_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    if shutil.which("git"):
        try:
            out = subprocess.run(
                ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if out.returncode == 0 and out.stdout.strip():
                return Path(out.stdout.strip()).resolve()
        except (OSError, subprocess.SubprocessError):
            pass
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ConfigError(
        f"{start} is not inside a Git repository. Run `git init` first, or cd into one."
    )
