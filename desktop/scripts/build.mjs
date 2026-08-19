#!/usr/bin/env node
"use strict";

// Tauri bundles for the operating system it runs on — there is no cross-compilation of a
// desktop bundle. Refuse the wrong host here, rather than three minutes into a Rust build.

import { spawnSync } from "node:child_process";

const TARGETS = {
  macos: { host: "darwin", bundles: "app,dmg", label: "macOS (.app, .dmg)" },
  windows: { host: "win32", bundles: "msi,nsis", label: "Windows (.msi, .exe)" },
  linux: { host: "linux", bundles: "deb,appimage", label: "Linux (.deb, .AppImage)" },
};
const HOST_NAMES = { darwin: "macOS", win32: "Windows", linux: "Linux" };

const [name, ...rest] = process.argv.slice(2);
const target = TARGETS[name];

if (!target) {
  console.error(`Unknown target ${name ?? "(none)"}. Use one of: ${Object.keys(TARGETS).join(", ")}.`);
  process.exit(2);
}

if (process.platform !== target.host) {
  const here = HOST_NAMES[process.platform] ?? process.platform;
  console.error(
    `Cannot build the ${target.label} bundle on ${here}.\n` +
    `Run \`npm run build:${name}\` on ${HOST_NAMES[target.host]}, or on a ${HOST_NAMES[target.host]} CI runner.`,
  );
  process.exit(1);
}

const args = ["tauri", "build", "--bundles", target.bundles];
if (name === "macos" && rest.includes("--universal")) {
  args.push("--target", "universal-apple-darwin");
}

console.log(`Building GitSquid for ${target.label}…`);
const result = spawnSync("npx", [...args, ...rest.filter((flag) => flag !== "--universal")], {
  stdio: "inherit",
  cwd: new URL("..", import.meta.url).pathname,
  shell: process.platform === "win32",
});
process.exit(result.status ?? 1);
