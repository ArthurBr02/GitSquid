// The page's own source, loaded outside a browser so its logic can be tested directly.
// These are plain scripts over shared globals, so each is evaluated in a function scope rather
// than imported: this package is `"type": "module"`, which would make Node read them as ESM and
// ignore the `module.exports` graph.js guards for the browser.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const assets = join(dirname(fileURLToPath(import.meta.url)), "..", "ui", "assets");
const read = (name) => readFileSync(join(assets, name), "utf8");

const evaluate = (source, exported) => new Function(`${source}\nreturn ${exported};`)();

export const page = evaluate(
  [read("viewer.js"), read("diff.js")].join("\n"),
  "{ parseDiff, inlineParts, filePathOf, fileStatusOf, commonRoot, linePatch }",
);

export const Graph = evaluate(read("graph.js"), "Graph");

/// Build the patch the interface would send for a set of picked lines.
export function pickLines(diff, picks) {
  const file = page.parseDiff(diff)[0];
  return page.linePatch(file, new Set(picks));
}
