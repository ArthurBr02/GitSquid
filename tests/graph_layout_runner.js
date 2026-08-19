"use strict";

/* Feeds rows from the test suite through the browser layout so pytest can assert on it. */

const path = require("path");
const Graph = require(path.join(__dirname, "..", "src", "gitsquid", "web", "assets", "graph.js"));

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const request = JSON.parse(input);
  const laid = Graph.layout(request.rows, { flat: Boolean(request.flat) });
  process.stdout.write(JSON.stringify({
    columns: laid.columns,
    rows: laid.rows.map((placed) => ({
      key: placed.row.sha || placed.row.kind,
      kind: placed.row.kind,
      node: placed.node,
      edges: placed.edges,
      width: placed.width,
    })),
  }));
});
