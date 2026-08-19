from __future__ import annotations

import re
from pathlib import PurePosixPath

REDACTED = "[redacted]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)\b\s*[:=]\s*"
        r"[\"']?([^\s\"'#,;]{6,})[\"']?"
    ),
)

_SENSITIVE_NAMES = frozenset(
    {".env", ".env.local", ".netrc", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_ed25519"}
)
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk")
# Conventional templates that exist to be read and copied — they hold no secret.
_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})


def redact(text: str) -> str:
    """Strip credential-shaped substrings before anything is stored or displayed."""
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            out = pattern.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
        else:
            out = pattern.sub(REDACTED, out)
    return out


def is_sensitive_path(path: str) -> bool:
    name = PurePosixPath(path).name
    if name in _TEMPLATE_NAMES:
        return False
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return True
    return name.lower().endswith(_SENSITIVE_SUFFIXES)


def is_safe_relative_path(path: str) -> bool:
    """Reject absolute paths, parent traversal, NUL bytes, and writes into .git/.gitia."""
    if not path or "\x00" in path:
        return False
    normalized = path.replace("\\", "/").strip()
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    parts = PurePosixPath(normalized).parts
    if not parts or any(part == ".." for part in parts):
        return False
    return parts[0] not in {".git", ".gitia"}


def clean_text_input(value: str, *, max_len: int, field: str) -> str:
    """Validate a free-text field coming from the terminal or an import file."""
    if value is None:
        raise ValueError(f"{field} is required.")
    cleaned = "".join(ch for ch in value if ch == "\n" or ch == "\t" or ch >= " ").strip()
    if not cleaned:
        raise ValueError(f"{field} must not be empty.")
    if len(cleaned) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters (got {len(cleaned)}).")
    return cleaned


def clean_patch_input(value: str, *, max_len: int, field: str) -> str:
    """Bounds-check a patch without touching its bytes — whitespace is load-bearing in a diff."""
    if not value or "\x00" in value:
        raise ValueError(f"{field} must be non-empty text without NUL bytes.")
    if not value.strip():
        raise ValueError(f"{field} must not be blank.")
    if len(value) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters (got {len(value)}).")
    return value


def tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return "…[truncated]\n" + text[-limit:]
