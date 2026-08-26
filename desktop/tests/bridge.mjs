#!/usr/bin/env node
// The only place the page's JavaScript still speaks over a pipe: the Rust suite drives it to
// check the seams that no single language can test alone — a patch the page builds and git
// applies, and a layout compared against git's own ASCII graph.

import { page, pickLines, Graph } from "./browser.mjs";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const request = JSON.parse(input);
  if (request.op === "pick") {
    process.stdout.write(JSON.stringify({ patch: pickLines(request.diff, request.picks) }));
    return;
  }
  if (request.op === "layout") {
    const laid = Graph.layout(request.rows, { flat: Boolean(request.flat) });
    process.stdout.write(JSON.stringify({ columns: laid.columns }));
    return;
  }
  process.stderr.write(`unknown op: ${request.op}\n`);
  process.exit(2);
});
void page;
