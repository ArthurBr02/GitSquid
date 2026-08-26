// The page splits patches itself: what it reads out of a diff is a contract worth testing.

import assert from "node:assert/strict";
import { test } from "node:test";

import { page, pickLines } from "./browser.mjs";

const TWO_FILES = `diff --git a/calc.py b/calc.py
index 1111111..2222222 100644
--- a/calc.py
+++ b/calc.py
@@ -1,3 +1,3 @@
 def add(a, b):
-    return a + b
+    return a - b
@@ -10,2 +10,3 @@ def total(values):
     result = 0
+    seen = set()
diff --git a/neuf.py b/neuf.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/neuf.py
@@ -0,0 +1,2 @@
+VALEUR = 1
+AUTRE = 2
`;

const parse = (diff) =>
  page.parseDiff(diff).map((file) => ({
    path: page.filePathOf(file),
    status: page.fileStatusOf(file),
    header: file.header,
    hunks: file.hunks.map((hunk) => hunk.lines),
  }));

test("a patch splits into files and hunks", () => {
  const files = parse(TWO_FILES);
  assert.deepEqual(files.map((file) => file.path), ["calc.py", "neuf.py"]);
  assert.deepEqual(files.map((file) => file.hunks.length), [2, 1]);
});

test("a file keeps the header a hunk needs to apply on its own", () => {
  const header = parse(TWO_FILES)[0].header;
  assert.ok(header[0].startsWith("diff --git"));
  assert.ok(header.some((line) => line.startsWith("--- ")));
  assert.ok(header.some((line) => line.startsWith("+++ ")));
});

test("a new file reads as an addition", () => {
  assert.deepEqual(parse(TWO_FILES).map((file) => file.status), ["M", "A"]);
});

test("a deleted file reads as a deletion", () => {
  const diff = "diff --git a/vieux.py b/vieux.py\ndeleted file mode 100644\n" +
    "--- a/vieux.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-VALEUR = 1\n";
  assert.equal(parse(diff)[0].status, "D");
});

test("a patch without a git header still reads", () => {
  const files = parse("--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-un\n+deux\n");
  assert.equal(files.length, 1);
  assert.equal(files[0].hunks.length, 1);
  assert.equal(files[0].path, "calc.py");
});

test("an empty patch yields nothing", () => {
  assert.deepEqual(parse(""), []);
});

// ------------------------------------------------------------------ inline marks

test("a small edit is located between the common ends", () => {
  const [head, tail] = page.inlineParts("    return a + b", "    return a - b");
  assert.ok("    return a ".startsWith("    return a".slice(0, head)));
  assert.ok(tail > 0);
});

test("two identical lines have nothing to mark", () => {
  assert.equal(page.inlineParts("same", "same"), null);
});

test("a line rewritten end to end is not marked", () => {
  assert.equal(page.inlineParts("alpha beta gamma", "delta epsilon zeta"), null);
});

// ------------------------------------------------------------------ shared folder

test("a deep shared folder is named once", () => {
  assert.equal(
    page.commonRoot([
      "src/main/java/com/example/app/fil/",
      "src/main/java/com/example/app/mail/",
    ]),
    "src/main/java/com/example/app/",
  );
});

test("folders that part early share nothing worth saying", () => {
  assert.equal(page.commonRoot(["src/main/java/", "tests/"]), "");
});

test("one folder alone needs no heading", () => {
  assert.equal(page.commonRoot(["src/main/java/com/example/"]), "");
});

test("a shallow shared folder is not worth a line", () => {
  assert.equal(page.commonRoot(["src/one/", "src/two/"]), "");
});

// ------------------------------------------------------------------ picking lines

// Positions count from the first line after the @@ header, starting at zero.
const ONE_FILE = `diff --git a/poeme.txt b/poeme.txt
index 1111111..2222222 100644
--- a/poeme.txt
+++ b/poeme.txt
@@ -1,5 +1,6 @@
 ligne 1
-ligne 2
+ligne deux modifiee
 ligne 3
 ligne 4
+ligne quatre et demie
 ligne 5
@@ -9,2 +10,2 @@ context
 ligne 9
-ligne 10
+ligne dix modifiee
`;

const REMOVAL = "0:1";              // -ligne 2
const LONE_ADDITION = "0:5";        // +ligne quatre et demie
const SECOND_HUNK_REMOVAL = "1:1";

const patchFor = (...picks) => pickLines(ONE_FILE, picks);
const headers = (patch) => patch.split("\n").filter((line) => line.startsWith("@@"));

test("picking nothing produces nothing", () => {
  assert.equal(patchFor(), "");
});

test("one added line is the only change left", () => {
  const body = patchFor(LONE_ADDITION).split("\n");
  assert.ok(body.includes("+ligne quatre et demie"));
  assert.ok(!body.includes("+ligne deux modifiee"));
  // the removal nobody picked stays, as context
  assert.ok(body.includes(" ligne 2"));
});

test("a picked removal stays a removal", () => {
  const body = patchFor(REMOVAL).split("\n");
  assert.ok(body.includes("-ligne 2"));
  assert.ok(!body.includes("+ligne deux modifiee"));
});

test("the header counts what the hunk now holds", () => {
  assert.deepEqual(headers(patchFor(LONE_ADDITION)), ["@@ -1,5 +1,6 @@"]);
  assert.deepEqual(headers(patchFor(REMOVAL)), ["@@ -1,5 +1,4 @@"]);
});

test("a hunk with nothing picked is dropped", () => {
  const patch = patchFor(SECOND_HUNK_REMOVAL);
  assert.equal(headers(patch).length, 1);
  assert.ok(!patch.includes("ligne dix modifiee"));
});

test("picks in both hunks keep both", () => {
  assert.equal(headers(patchFor(REMOVAL, SECOND_HUNK_REMOVAL)).length, 2);
});

test("a later hunk starts where the earlier picks left the file", () => {
  assert.deepEqual(headers(patchFor(LONE_ADDITION, SECOND_HUNK_REMOVAL)), [
    "@@ -1,5 +1,6 @@",
    "@@ -9,2 +10,1 @@ context",
  ]);
});

test("the file header is carried so git can apply it", () => {
  const patch = patchFor(LONE_ADDITION);
  assert.ok(patch.startsWith("diff --git a/poeme.txt b/poeme.txt"));
  assert.ok(patch.includes("--- a/poeme.txt"));
  assert.ok(patch.includes("+++ b/poeme.txt"));
  assert.ok(patch.endsWith("\n"));
});
