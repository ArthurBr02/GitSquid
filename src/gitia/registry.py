from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import STATE_DIRNAME, ConfigError, find_repo_root

MAX_REPOS = 50


def config_dir() -> Path:
    base = os.environ.get("GITIA_CONFIG_DIR") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "gitia"


def registry_path() -> Path:
    return config_dir() / "repos.json"


@dataclass(frozen=True)
class KnownRepo:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def exists(self) -> bool:
        return (self.path / ".git").exists()

    @property
    def initialized(self) -> bool:
        return (self.path / STATE_DIRNAME / "gitia.db").exists()

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.name,
            "exists": self.exists,
            "initialized": self.initialized,
        }


def _read() -> list[Path]:
    target = registry_path()
    if not target.exists():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("repos"), list):
        return []
    paths: list[Path] = []
    for entry in payload["repos"][:MAX_REPOS]:
        if isinstance(entry, str) and entry.strip():
            candidate = Path(entry)
            if candidate not in paths:
                paths.append(candidate)
    return paths


def _write(paths: list[Path]) -> None:
    target = registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "repos": [str(path) for path in paths[:MAX_REPOS]]}
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def known() -> list[KnownRepo]:
    return [KnownRepo(path) for path in _read()]


def add(path: Path | str) -> Path:
    """Register a repository. The path must resolve to the root of a Git repository."""
    try:
        root = find_repo_root(Path(path).expanduser())
    except ConfigError as exc:
        raise ConfigError(str(exc)) from exc
    paths = _read()
    if root in paths:
        paths.remove(root)
    paths.insert(0, root)
    _write(paths)
    return root


def remove(path: Path | str) -> bool:
    target = Path(path).expanduser().resolve()
    paths = _read()
    remaining = [item for item in paths if item != target]
    if len(remaining) == len(paths):
        return False
    _write(remaining)
    return True
