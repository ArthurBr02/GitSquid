from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

STATE_DIRNAME = ".gitsquid"
DB_FILENAME = "gitsquid.db"
# The tool was called gitia before; repositories indexed then keep working untouched.
LEGACY_STATE_DIRNAME = ".gitia"
LEGACY_DB_FILENAME = "gitia.db"
ENV_PREFIXES = ("GITSQUID_", "GITIA_")

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


def _env(name: str) -> str:
    """Read GITSQUID_<name>, falling back to the pre-rename GITIA_<name>."""
    for prefix in ENV_PREFIXES:
        raw = os.environ.get(prefix + name, "").strip()
        if raw:
            return raw
    return ""


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = _env(name)
    if not raw:
        return default
    label = ENV_PREFIXES[0] + name
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{label} must be an integer, got {raw!r}.") from exc
    if value < minimum:
        raise ConfigError(f"{label} must be >= {minimum}, got {value}.")
    return value


def state_paths(root: Path) -> tuple[Path, Path]:
    """The state directory and database, or the pre-rename pair when that is what exists."""
    legacy = root / LEGACY_STATE_DIRNAME
    if not (root / STATE_DIRNAME).exists() and (legacy / LEGACY_DB_FILENAME).exists():
        return legacy, legacy / LEGACY_DB_FILENAME
    return root / STATE_DIRNAME, root / STATE_DIRNAME / DB_FILENAME


def load_settings(repo: Path | None = None) -> Settings:
    root = find_repo_root(repo)
    load_dotenv(root / ".env", override=False)
    load_dotenv(override=False)

    effort = (_env("EFFORT") or DEFAULT_EFFORT).lower()
    if effort not in VALID_EFFORTS:
        raise ConfigError(
            f"GITSQUID_EFFORT must be one of {', '.join(VALID_EFFORTS)}, got {effort!r}."
        )

    state_dir, db_path = state_paths(root)
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None

    return Settings(
        repo=root,
        state_dir=state_dir,
        db_path=db_path,
        model=_env("MODEL") or DEFAULT_MODEL,
        effort=effort,
        max_context_chars=_int_env("MAX_CONTEXT_CHARS", DEFAULT_MAX_CONTEXT_CHARS, minimum=1000),
        max_file_bytes=_int_env("MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES, minimum=100),
        test_command=_env("TEST_COMMAND") or DEFAULT_TEST_COMMAND,
        test_timeout=_int_env("TEST_TIMEOUT", DEFAULT_TEST_TIMEOUT),
        api_key=api_key,
    )
