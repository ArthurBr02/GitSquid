from __future__ import annotations

from gitia.indexer import chunk_lines, index_repo, index_summary, language_for, walk_repo
from gitia.retrieval import search, to_fts_query


def index(conn, repo, **kwargs):
    return index_repo(conn, repo, max_file_bytes=kwargs.pop("max_file_bytes", 200_000), **kwargs)


class TestWalking:
    def test_skips_credentials_binaries_and_large_files(self, repo):
        (repo / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-secret\n", encoding="utf-8")
        (repo / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
        (repo / "logo.png").write_bytes(b"\x89PNG\x00binary")
        (repo / "huge.py").write_text("x = 1\n" * 5000, encoding="utf-8")

        decisions = {
            path.name: (indexable, reason)
            for path, indexable, reason in walk_repo(repo, max_file_bytes=1000)
        }
        assert decisions[".env"] == (False, "sensitive")
        assert decisions["server.pem"] == (False, "sensitive")
        assert decisions["logo.png"] == (False, "binary")
        assert decisions["huge.py"] == (False, "too-large")
        assert decisions["calc.py"] == (True, "ok")

    def test_ignores_vendor_directories(self, repo):
        (repo / "node_modules" / "pkg").mkdir(parents=True)
        (repo / "node_modules" / "pkg" / "index.js").write_text("noise", encoding="utf-8")
        names = [path.name for path, _, _ in walk_repo(repo, max_file_bytes=200_000)]
        assert "index.js" not in names

    def test_env_templates_are_readable_but_real_env_files_are_not(self, repo):
        (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (repo / ".env.example").write_text("SECRET=\n", encoding="utf-8")
        (repo / ".env.production").write_text("SECRET=live\n", encoding="utf-8")

        decisions = {
            path.name: indexable for path, indexable, _ in walk_repo(repo, max_file_bytes=200_000)
        }
        assert decisions[".env"] is False
        assert decisions[".env.production"] is False
        assert decisions[".env.example"] is True

    def test_language_detection(self, repo):
        assert language_for(repo / "calc.py") == "python"
        assert language_for(repo / "README.md") == "markdown"
        assert language_for(repo / "thing.unknown") == "text"


class TestChunking:
    def test_chunks_cover_every_line_without_overlap(self):
        lines = [f"line {n}\n" for n in range(1, 201)]
        chunks = list(chunk_lines(lines, size=80))
        assert [(c[0], c[1]) for c in chunks] == [(1, 80), (81, 160), (161, 200)]
        assert "".join(c[2] for c in chunks) == "".join(lines)

    def test_empty_file_yields_nothing(self):
        assert list(chunk_lines([])) == []


class TestIndexing:
    def test_indexes_text_files_and_reports_stats(self, conn, repo):
        stats = index(conn, repo)
        assert stats.files_indexed == 3
        assert stats.chunks >= 3
        assert index_summary(conn)["files"] == 3

    def test_second_run_is_incremental(self, conn, repo):
        index(conn, repo)
        again = index(conn, repo)
        assert again.files_indexed == 0
        assert again.files_unchanged == 3

    def test_force_reindexes_everything(self, conn, repo):
        index(conn, repo)
        forced = index(conn, repo, force=True)
        assert forced.files_indexed == 3
        assert forced.files_unchanged == 0

    def test_edited_file_is_reindexed_and_old_chunks_dropped(self, conn, repo):
        index(conn, repo)
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
        stats = index(conn, repo)
        assert stats.files_indexed == 1
        rows = conn.execute(
            "SELECT COUNT(*) FROM chunks c JOIN files f ON f.id=c.file_id WHERE f.path='calc.py'"
        ).fetchone()[0]
        assert rows == 1

    def test_deleted_file_leaves_the_index(self, conn, repo):
        index(conn, repo)
        (repo / "README.md").unlink()
        stats = index(conn, repo)
        assert stats.files_removed == 1
        assert index_summary(conn)["files"] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE path='README.md'"
        ).fetchone()[0] == 0

    def test_secrets_never_enter_the_index(self, conn, repo):
        (repo / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-api03-NOTREAL\n", encoding="utf-8")
        index(conn, repo)
        hits = conn.execute("SELECT COUNT(*) FROM chunks WHERE content LIKE '%sk-ant%'").fetchone()
        assert hits[0] == 0


class TestSearch:
    def test_finds_indexed_code(self, conn, repo):
        index(conn, repo)
        hits = search(conn, "total values helper")
        assert hits and any(hit.path == "calc.py" for hit in hits)
        assert hits[0].label.startswith(hits[0].path + ":")

    def test_unknown_terms_return_nothing(self, conn, repo):
        index(conn, repo)
        assert search(conn, "quetzalcoatl") == []

    def test_fts_operators_in_user_input_are_neutralised(self, conn, repo):
        index(conn, repo)
        assert search(conn, 'add" OR chunks_fts MATCH "x') is not None
        assert to_fts_query('add" OR "beta') == '"add" OR "or" OR "beta"'
        assert to_fts_query("!!!") == ""

    def test_empty_query_is_not_a_crash(self, conn, repo):
        index(conn, repo)
        assert search(conn, "   ") == []
