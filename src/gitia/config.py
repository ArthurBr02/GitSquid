from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

STATE_DIRNAME = ".gitia"
DB_FILENAME = "gitia.db"

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_CONTEXT_CHARS = 48_000
DEFAULT_MAX_FILE_BYTES = 200_000
DEFAULT_TEST_COMMAND = "pytest -q"
DEFAULT_TEST_TIMEOUT = 600

VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    repo: Path
    state_dir: Path
    db_path: Path
    model: str
    effort: str
    max_context_chars: int
    max_file_bytes: int
    test_command: str
    test_timeout: int
    api_key: str | None

    @property
    def model_available(self) -> bool:
        return bool(self.api_key)

    @property
    def initialized(self) -> bool:
        return self.db_path.exists()


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


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}.")
    return value


def load_settings(repo: Path | None = None) -> Settings:
    root = find_repo_root(repo)
    load_dotenv(root / ".env", override=False)
    load_dotenv(override=False)

    effort = (os.environ.get("GITIA_EFFORT") or DEFAULT_EFFORT).strip().lower()
    if effort not in VALID_EFFORTS:
        raise ConfigError(
            f"GITIA_EFFORT must be one of {', '.join(VALID_EFFORTS)}, got {effort!r}."
        )

    state_dir = root / STATE_DIRNAME
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None

    return Settings(
        repo=root,
        state_dir=state_dir,
        db_path=state_dir / DB_FILENAME,
        model=(os.environ.get("GITIA_MODEL") or DEFAULT_MODEL).strip(),
        effort=effort,
        max_context_chars=_int_env("GITIA_MAX_CONTEXT_CHARS", DEFAULT_MAX_CONTEXT_CHARS, minimum=1000),
        max_file_bytes=_int_env("GITIA_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES, minimum=100),
        test_command=(os.environ.get("GITIA_TEST_COMMAND") or DEFAULT_TEST_COMMAND).strip(),
        test_timeout=_int_env("GITIA_TEST_TIMEOUT", DEFAULT_TEST_TIMEOUT),
        api_key=api_key,
    )
