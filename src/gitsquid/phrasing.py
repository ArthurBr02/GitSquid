"""How the interface counts things. One rule, so nothing says "1 file(s)"."""

from __future__ import annotations

IRREGULAR = {"is": "are", "was": "were"}


def plural(count: int, word: str, *, many: str | None = None) -> str:
    if count == 1:
        return f"{count} {word}"
    return f"{count} {many or IRREGULAR.get(word) or word + 's'}"
