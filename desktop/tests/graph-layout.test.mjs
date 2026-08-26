// The commit graph: lanes must join across every row, including the rows that are not commits.
// The layout is pure graph arithmetic, so the histories here are written out rather than
// grown with git — what it needs from a commit is its sha and its parents, nothing more.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Graph } from "./browser.mjs";

const node = (sha, parents) => ({ kind: "commit", sha, parents });

/// Four commits in a line, newest first.
const LINEAR = [
  node("d", ["c"]), node("c", ["b"]), node("b", ["a"]), node("a", []),
];

/// One commit that is the parent of three branches, then three merges back — the shape the
/// Python fixture grew with git, written down.
const BRANCHY = [
  node("merge2", ["merge1", "work2"]),
  node("merge1", ["merge0", "work1"]),
  node("merge0", ["root", "work0"]),
  node("work2", ["root"]),
  node("work1", ["root"]),
  node("work0", ["root"]),
  node("root", []),
];

const key = (placed) => placed.row.sha || placed.row.kind;
const reachingBottom = (placed) =>
  new Set(placed.edges.filter((edge) => ["pass", "out", "stub"].includes(edge.kind)).map((edge) => edge.to));
const enteringTop = (placed) =>
  new Set(placed.edges.filter((edge) => ["pass", "in"].includes(edge.kind)).map((edge) => edge.from));

const without = (set, ...others) => {
  const left = new Set(set);
  for (const other of others) for (const value of other) left.delete(value);
  return [...left].sort();
};

function assertLinesJoin(laid) {
  for (let index = 0; index + 1 < laid.rows.length; index += 1) {
    const above = laid.rows[index];
    const below = laid.rows[index + 1];
    const dangling = without(reachingBottom(above), enteringTop(below), [below.node.column]);
    assert.deepEqual(dangling, [],
      `a line stops between ${key(above)} and ${key(below)}: column(s) ${dangling} lead nowhere`);
    const invented = without(enteringTop(below), reachingBottom(above));
    assert.deepEqual(invented, [],
      `${key(below)} is entered by column(s) ${invented} that nothing feeds`);
  }
}

const layout = (rows, flat = false) => Graph.layout(rows, { flat });

// ------------------------------------------------------------------ continuity

test("a linear history stays in one column", () => {
  const laid = layout(LINEAR);
  assert.equal(laid.columns, 1);
  assert.deepEqual([...new Set(laid.rows.map((placed) => placed.node.column))], [0]);
  assert.equal(enteringTop(laid.rows[0]).size, 0, "the tip has nothing above it");
  for (const placed of laid.rows.slice(1)) {
    assert.ok(placed.edges.some((edge) => edge.kind === "in"), `${key(placed)} floats free`);
  }
});

test("no line dangles on a branchy history", () => {
  assertLinesJoin(layout(BRANCHY));
});

test("a change row lets every lane through", () => {
  // The bug that made the graph unreadable: rows that are not commits cut the lanes.
  const rows = [...BRANCHY.slice(0, 2), { kind: "change", status: "proposed" }, ...BRANCHY.slice(2)];
  const laid = layout(rows);
  assertLinesJoin(laid);

  const change = laid.rows[2];
  const above = laid.rows[1];
  const allowed = new Set([...enteringTop(change), change.node.column]);
  assert.deepEqual(without(reachingBottom(above), allowed), []);
});

test("the working tree row rides the tip lane", () => {
  const laid = layout([{ kind: "wip" }, ...BRANCHY]);
  assert.equal(laid.rows[0].node.column, laid.rows[1].node.column);
  assertLinesJoin(laid);
});

// ------------------------------------------------------------------ width

test("a shared parent does not leak a column", () => {
  // Three children of one commit used to claim three columns that never closed.
  assert.ok(layout(BRANCHY).columns <= 4, `${layout(BRANCHY).columns} columns`);
});

test("a freed column is reused before a new one is opened", () => {
  const dag = [
    node("m2", ["t2", "b2"]), node("b2", ["t2"]), node("t2", ["m1"]),
    node("m1", ["t1", "b1"]), node("b1", ["t1"]), node("t1", []),
  ];
  assert.ok(layout(dag).columns <= 2, "the second branch opened a new column");
});

test("every node sits inside the reported width", () => {
  for (const placed of layout(BRANCHY).rows) {
    assert.ok(placed.node.column < placed.width, `${key(placed)} sits outside its row`);
  }
});

// ------------------------------------------------------------------ readability

test("a branch keeps one colour from tip to root", () => {
  const colours = new Set(layout(LINEAR).rows.map((placed) => placed.node.color));
  assert.equal(colours.size, 1, "the trunk changes colour along the way");
});

test("a merge leaves one line per parent", () => {
  const laid = layout(BRANCHY);
  const merges = laid.rows.filter((placed) => placed.node.shape === "merge");
  assert.ok(merges.length > 0);
  for (const merge of merges) {
    assert.equal(merge.edges.filter((edge) => edge.kind === "out").length, 2);
  }
});

test("two lanes side by side never share a colour", () => {
  for (const placed of layout(BRANCHY).rows) {
    const colours = placed.edges
      .filter((edge) => ["pass", "in"].includes(edge.kind))
      .map((edge) => edge.color);
    assert.equal(colours.length, new Set(colours).size,
      `${key(placed)} draws two lanes in the same colour`);
  }
});

test("a filtered list drops the lanes instead of lying", () => {
  // Filtering leaves holes in the history, so edges would join rows that are not adjacent.
  const laid = layout(BRANCHY.filter((_, index) => index % 2 === 0), true);
  assert.equal(laid.columns, 1);
  assert.ok(laid.rows.every((placed) => placed.edges.length === 0));
  assert.deepEqual([...new Set(laid.rows.map((placed) => placed.node.column))], [0]);
});
