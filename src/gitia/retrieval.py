from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_WORD = re.compile(r"[A-Za-z0-9_]{2,}")
# Deliberately short: words like "add", "get", "set" are identifiers in code, not noise.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "when", "then",
        "should", "must", "not", "but", "you", "your", "please", "would", "could",
    }
)


@dataclass(frozen=True)
class Hit:
    path: str
    start_line: int
    end_line: int
    content: str
    score: float

    @property
    def label(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


def to_fts_query(text: str, *, max_terms: int = 24) -> str:
    """Turn arbitrary user text into an FTS5 expression with no operator injection."""
    terms: list[str] = []
    for word in _WORD.findall(text):
        lowered = word.lower()
        if lowered in _STOPWORDS or lowered in terms:
            continue
        terms.append(lowered)
        if len(terms) >= max_terms:
            break
    return " OR ".join(f'"{term}"' for term in terms)


def search(conn: sqlite3.Connection, query: str, *, limit: int = 12) -> list[Hit]:
    expression = to_fts_query(query)
    if not expression:
        return []
    try:
        rows = conn.execute(
            """SELECT f.path AS path, c.start_line, c.end_line, c.content,
                      bm25(chunks_fts) AS score
               FROM chunks_fts
               JOIN chunks c ON c.id = chunks_fts.chunk_id
               JOIN files  f ON f.id = c.file_id
               WHERE chunks_fts MATCH ?
               ORDER BY score
               LIMIT ?""",
            (expression, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        Hit(
            path=row["path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            content=row["content"],
            score=float(row["score"]),
        )
        for row in rows
    ]


@dataclass(frozen=True)
class Context:
    hits: list[Hit]
    chars: int
    truncated: bool

    @property
    def files(self) -> list[str]:
        seen: list[str] = []
        for hit in self.hits:
            if hit.path not in seen:
                seen.append(hit.path)
        return seen

    def render(self) -> str:
        parts = [f"----- {hit.label} -----\n{hit.content}" for hit in self.hits]
        body = "\n".join(parts)
        if self.truncated:
            body += (
                "\n----- context truncated -----\n"
                "Only the excerpts above were retrieved; the rest of the repository was not read.\n"
            )
        return body


def build_context(
    conn: sqlite3.Connection,
    task: str,
    *,
    budget_chars: int,
    limit: int = 24,
    pinned: list[str] | None = None,
) -> Context:
    hits = search(conn, task, limit=limit)
    if pinned:
        hits = _prepend_pinned(conn, pinned, hits)

    kept: list[Hit] = []
    used = 0
    truncated = False
    for hit in hits:
        cost = len(hit.content) + len(hit.label) + 20
        if used + cost > budget_chars:
            truncated = True
            continue
        kept.append(hit)
        used += cost
    return Context(hits=kept, chars=used, truncated=truncated or len(kept) < len(hits))


def _prepend_pinned(conn: sqlite3.Connection, pinned: list[str], hits: list[Hit]) -> list[Hit]:
    pinned_hits: list[Hit] = []
    for path in pinned:
        rows = conn.execute(
            """SELECT f.path AS path, c.start_line, c.end_line, c.content
               FROM chunks c JOIN files f ON f.id = c.file_id
               WHERE f.path = ? ORDER BY c.ordinal""",
            (path,),
        ).fetchall()
        pinned_hits.extend(
            Hit(
                path=row["path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content=row["content"],
                score=-1000.0,
            )
            for row in rows
        )
    known = {(hit.path, hit.start_line) for hit in pinned_hits}
    return pinned_hits + [hit for hit in hits if (hit.path, hit.start_line) not in known]
