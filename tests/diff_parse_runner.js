"use strict";

/* Runs the page's own diff parsing so pytest can assert on it, without a browser. */

const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "src", "gitsquid", "web", "assets", "viewer.js"), "utf8",
);
const exposed = new Function(`${source}\nreturn { parseDiff, inlineParts, filePathOf, fileStatusOf, commonRoot };`)();

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const request = JSON.parse(input);
  if (request.op === "parse") {
    const files = exposed.parseDiff(request.diff).map((file) => ({
      path: exposed.filePathOf(file),
      status: exposed.fileStatusOf(file),
      header: file.header,
      hunks: file.hunks.map((hunk) => hunk.lines),
    }));
    process.stdout.write(JSON.stringify({ files }));
    return;
  }
  if (request.op === "root") {
    process.stdout.write(JSON.stringify({ root: exposed.commonRoot(request.directories) }));
    return;
  }
  process.stdout.write(JSON.stringify({ cut: exposed.inlineParts(request.before, request.after) }));
});
