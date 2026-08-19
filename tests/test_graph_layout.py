"""The commit graph: lanes must join across every row, including the rows that are not commits."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gitsquid import gitlog
from tests.conftest import git

RUNNER = Path(__file__).parent / "graph_layout_runner.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required to run the browser layout")


def layout(rows: list[dict], *, flat: bool = False) -> dict:
    result = subprocess.run(
        ["node", str(RUNNER)],
        input=json.dumps({"rows": rows, "flat": flat}),
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def commit_rows(repo: Path, limit: int = 80) -> list[dict]:
    return [
        {"kind": "commit", "sha": commit.sha, "parents": commit.parents}
        for commit in gitlog.commits(repo, limit=limit)
    ]


def node(sha: str, parents: list[str]) -> dict:
    return {"kind": "commit", "sha": sha, "parents": parents}


def commit_file(repo, name, text, message):
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def branchy(repo):
    """One commit that is the parent of three branches, then three merges back."""
    for index in range(3):
        git(repo, "checkout", "-q", "-b", f"feature/{index}", "main")
        commit_file(repo, f"feature_{index}.py", f"VALUE = {index}\n", f"work {index}")
    git(repo, "checkout", "-q", "main")
    for index in range(3):
        git(repo, "merge", "-q", "--no-ff", f"feature/{index}", "-m", f"merge {index}")
    return repo


def git_graph_width(repo: Path, limit: int = 80) -> int:
    """How wide git's own ASCII graph gets — the reference we must not exceed."""
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--graph", "--oneline", "--topo-order", f"-{limit}"],
        capture_output=True, text=True, check=False,
    )
    widths = []
    for line in result.stdout.splitlines():
        match = re.match(r"^([ |*/\\_]+)", line)
        if match:
            widths.append(len(match.group(1).rstrip()) // 2 + 1)
    return max(widths) if widths else 1


def reaching_bottom(row: dict) -> set[int]:
    return {edge["to"] for edge in row["edges"] if edge["kind"] in {"pass", "out", "stub"}}


def entering_top(row: dict) -> set[int]:
    return {edge["from"] for edge in row["edges"] if edge["kind"] in {"pass", "in"}}


def assert_lines_join(laid: dict) -> None:
    for above, below in zip(laid["rows"], laid["rows"][1:]):
        dangling = reaching_bottom(above) - entering_top(below) - {below["node"]["column"]}
        assert not dangling, (
            f"a line stops between {above['key']} and {below['key']}: column(s) {sorted(dangling)} lead nowhere"
        )
        invented = entering_top(below) - reaching_bottom(above)
        assert not invented, f"{below['key']} is entered by column(s) {sorted(invented)} that nothing feeds"


class TestContinuity:
    def test_a_linear_history_stays_in_one_column(self, repo):
        commit_file(repo, "a.py", "A = 1\n", "second")
        commit_file(repo, "b.py", "B = 2\n", "third")
        laid = layout(commit_rows(repo))

        assert laid["columns"] == 1
        assert {row["node"]["column"] for row in laid["rows"]} == {0}
        assert not entering_top(laid["rows"][0]), "the tip has nothing above it"
        for row in laid["rows"][1:]:
            assert [edge for edge in row["edges"] if edge["kind"] == "in"], f"{row['key']} floats free"

    def test_no_line_dangles_on_a_branchy_history(self, branchy):
        assert_lines_join(layout(commit_rows(branchy)))

    def test_a_change_row_lets_every_lane_through(self, branchy):
        """The bug that made the graph unreadable: rows that are not commits cut the lanes."""
        rows = commit_rows(branchy)
        with_change = [*rows[:2], {"kind": "change", "status": "proposed"}, *rows[2:]]
        laid = layout(with_change)

        assert_lines_join(laid)
        change = laid["rows"][2]
        above = laid["rows"][1]
        assert reaching_bottom(above) <= entering_top(change) | {change["node"]["column"]}

    def test_the_working_tree_row_rides_the_tip_lane(self, branchy):
        rows = commit_rows(branchy)
        laid = layout([{"kind": "wip"}, *rows])

        assert laid["rows"][0]["node"]["column"] == laid["rows"][1]["node"]["column"]
        assert_lines_join(laid)


class TestWidth:
    def test_width_never_exceeds_the_graph_git_itself_draws(self, branchy):
        assert layout(commit_rows(branchy))["columns"] <= git_graph_width(branchy)

    def test_a_shared_parent_does_not_leak_a_column(self):
        """Three children of one commit used to claim three columns that never closed."""
        dag = [
            node("m3", ["m2", "c"]), node("m2", ["m1", "b"]), node("m1", ["root", "a"]),
            node("c", ["root"]), node("b", ["root"]), node("a", ["root"]), node("root", []),
        ]
        assert layout(dag)["columns"] <= 4

    def test_a_freed_column_is_reused_before_a_new_one_is_opened(self):
        dag = [
            node("m2", ["t2", "b2"]), node("b2", ["t2"]), node("t2", ["m1"]),
            node("m1", ["t1", "b1"]), node("b1", ["t1"]), node("t1", []),
        ]
        assert layout(dag)["columns"] <= 2, "the second branch opened a new column"

    def test_every_node_sits_inside_the_reported_width(self, branchy):
        for row in layout(commit_rows(branchy))["rows"]:
            assert row["node"]["column"] < row["width"]


class TestReadability:
    def test_a_branch_keeps_one_colour_from_tip_to_root(self, repo):
        for index in range(4):
            commit_file(repo, "counter.py", f"N = {index}\n", f"step {index}")
        colours = {row["node"]["color"] for row in layout(commit_rows(repo))["rows"]}
        assert len(colours) == 1, "the trunk changes colour along the way"

    def test_a_merge_leaves_one_line_per_parent(self, branchy):
        laid = layout(commit_rows(branchy))
        merges = [row for row in laid["rows"] if row["node"]["shape"] == "merge"]
        assert merges
        for merge in merges:
            outgoing = [edge for edge in merge["edges"] if edge["kind"] == "out"]
            assert len(outgoing) == 2

    def test_two_lanes_side_by_side_never_share_a_colour(self, branchy):
        laid = layout(commit_rows(branchy))
        for row in laid["rows"]:
            colours = [edge["color"] for edge in row["edges"] if edge["kind"] in {"pass", "in"}]
            assert len(colours) == len(set(colours)), f"{row['key']} draws two lanes in the same colour"

    def test_a_filtered_list_drops_the_lanes_instead_of_lying(self, branchy):
        """Filtering leaves holes in the history, so edges would join rows that are not adjacent."""
        laid = layout(commit_rows(branchy)[::2], flat=True)
        assert laid["columns"] == 1
        assert all(not row["edges"] for row in laid["rows"])
        assert {row["node"]["column"] for row in laid["rows"]} == {0}
