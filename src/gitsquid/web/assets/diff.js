"use strict";

function parseDiff(text) {
  if (!text || !text.trim()) return [];
  const files = [];
  let file = null;
  let hunk = null;
  const ensure = () => {
    if (!file) { file = { header: [], hunks: [] }; files.push(file); }
    return file;
  };
  for (const line of text.replace(/\n$/, "").split("\n")) {
    if (line.startsWith("diff --git ")) {
      file = { header: [line], hunks: [] };
      files.push(file);
      hunk = null;
    } else if (line.startsWith("@@")) {
      hunk = { lines: [line] };
      ensure().hunks.push(hunk);
    } else if (hunk) {
      hunk.lines.push(line);
    } else {
      ensure().header.push(line);
    }
  }
  return files;
}

function diffLine(kind, oldNumber, newNumber, content, pick = null) {
  const gutter = (side, number) => (pick
    ? el("button", {
        type: "button", class: `ln ${side} pickable`, tabindex: "-1",
        "aria-label": `${pick.picked ? "Unpick" : "Pick"} line ${number || ""}`,
        onclick: () => togglePick(pick.key),
        text: number,
      })
    : el("span", { class: `ln ${side}`, "aria-hidden": "true", text: number }));
  return el("div", { class: `${kind}${pick && pick.picked ? " picked" : ""}` }, [
    gutter("old", oldNumber),
    gutter("new", newNumber),
    el("span", { class: "tx" }, content),
  ]);
}

function inlineParts(before, after) {
  const shortest = Math.min(before.length, after.length);
  let head = 0;
  while (head < shortest && before[head] === after[head]) head += 1;
  let tail = 0;
  while (tail < shortest - head
    && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail += 1;

  const middle = [before.slice(head, before.length - tail), after.slice(head, after.length - tail)];
  if (!middle[0] && !middle[1]) return null;
  // Highlighting the whole line says nothing the colour of the line did not already say.
  if (middle[0].length > before.length * 0.7 && middle[1].length > after.length * 0.7) return null;
  return [head, tail];
}

function markedLine(text, cut) {
  if (!cut) return [text];
  const [head, tail] = cut;
  return [
    text.slice(0, head + 1),
    el("span", { class: "ink", text: text.slice(head + 1, text.length - tail) }),
    text.slice(text.length - tail),
  ];
}

// An unpicked addition vanishes, an unpicked removal becomes context, headers are recounted.
function linePatch(file, picks) {
  const body = [];
  let drift = 0;
  file.hunks.forEach((hunk, hunkIndex) => {
    const lines = [];
    let before = 0;
    let after = 0;
    let kept = false;
    let emitted = false;
    hunk.lines.slice(1).forEach((line, lineIndex) => {
      if (line.startsWith("\\")) {
        if (emitted) lines.push(line);
        return;
      }
      const picked = picks.has(`${hunkIndex}:${lineIndex}`);
      emitted = true;
      if (line.startsWith("+")) {
        emitted = picked;
        if (!picked) return;
        lines.push(line);
        after += 1;
        kept = true;
      } else if (line.startsWith("-")) {
        before += 1;
        if (picked) {
          lines.push(line);
          kept = true;
        } else {
          lines.push(` ${line.slice(1)}`);
          after += 1;
        }
      } else {
        lines.push(line);
        before += 1;
        after += 1;
      }
    });
    if (!kept) return;
    const header = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(hunk.lines[0]);
    const start = header ? Number(header[1]) : 1;
    body.push(`@@ -${start},${before} +${start + drift},${after} @@${header ? header[3] : ""}`, ...lines);
    drift += after - before;
  });
  return body.length ? `${[...file.header, ...body].join("\n")}\n` : "";
}

function pickedLines() {
  return state.view && state.view.picks ? state.view.picks : new Set();
}

function togglePick(key) {
  const picks = new Set(pickedLines());
  if (picks.has(key)) picks.delete(key);
  else picks.add(key);
  state.view = { ...state.view, picks };
  renderViewer();
}

function pickBar() {
  const picks = pickedLines();
  if (!picks.size) return null;
  const staged = state.view.context.staged;
  return el("div", { class: "pick-bar" }, [
    el("span", { text: `${plural(picks.size, "line")} picked` }),
    el("button", {
      type: "button", class: "btn tiny",
      onclick: () => applyPicked(staged ? "unstage" : "stage"),
      text: staged ? "Unstage them" : "Stage them",
    }),
    staged ? null : el("button", {
      type: "button", class: "btn tiny danger",
      onclick: () => {
        if (confirm(`Discard ${plural(picks.size, "line")}? They are lost.`)) applyPicked("discard");
      },
      text: "Discard them",
    }),
    el("button", {
      type: "button", class: "btn tiny ghost",
      onclick: () => { state.view = { ...state.view, picks: new Set() }; renderViewer(); },
      text: "Clear",
    }),
  ]);
}

function hunkBar(file, hunk, actions) {
  const patch = [...file.header, ...hunk.lines].join("\n") + "\n";
  const buttons = actions.staged
    ? [["Unstage hunk", "unstage", false]]
    : [["Stage hunk", "stage", false], ["Discard hunk", "discard", true]];
  return el("div", { class: "hunk-bar" }, buttons.map(([label, target, danger]) =>
    el("button", {
      type: "button",
      class: `btn tiny ${danger ? "danger" : "ghost"}`,
      onclick: () => {
        if (danger && !confirm("Discard this hunk? The lines are lost.")) return;
        applyHunk(patch, target);
      },
      text: label,
    })));
}

// Entries are [text, position]; the position is the line's place in its hunk.
function renderRewrite(box, removed, added, numbers, pickFor) {
  const paired = removed.length === added.length;
  removed.forEach(([line, position], index) => {
    const cut = paired ? inlineParts(line.slice(1), added[index][0].slice(1)) : null;
    box.append(diffLine("del", String(numbers.old++), "", markedLine(line, cut), pickFor(position)));
  });
  added.forEach(([line, position], index) => {
    const cut = paired ? inlineParts(removed[index][0].slice(1), line.slice(1)) : null;
    box.append(diffLine("add", "", String(numbers.new++), markedLine(line, cut), pickFor(position)));
  });
}

const MAX_DIFF_LINES = 4000;

// `actions` is set only for a working-tree file, the only place a hunk can be staged.
function renderDiff(diff, actions = null, { headers = true } = {}) {
  const box = el("pre", { class: "diff" });
  const picks = pickedLines();
  let budget = MAX_DIFF_LINES;
  for (const file of parseDiff(diff)) {
    if (budget <= 0) break;
    if (headers) for (const line of file.header) box.append(diffLine("meta", "", "", [line]));
    file.hunks.forEach((hunk, hunkIndex) => {
      if (budget <= 0) return;
      if (actions) box.append(hunkBar(file, hunk, actions));
      const numbers = { old: 0, new: 0 };
      if ((budget -= hunk.lines.length) <= 0) {
        box.append(diffLine("meta", "", "", [
          "\u2026 the rest of this patch is not shown. Open a file on its own to read it in full.",
        ]));
        return;
      }
      const lines = hunk.lines;
      const pickFor = (position) => {
        if (!actions) return null;
        const key = `${hunkIndex}:${position}`;
        return { key, picked: picks.has(key) };
      };

      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        if (line.startsWith("@@")) {
          const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
          if (match) { numbers.old = Number(match[1]); numbers.new = Number(match[2]); }
          box.append(diffLine("hunk", "", "", [line]));
        } else if (line.startsWith("-")) {
          const removed = [];
          const added = [];
          while (index < lines.length && lines[index].startsWith("-")) {
            removed.push([lines[index], index - 1]);
            index += 1;
          }
          while (index < lines.length && lines[index].startsWith("+")) {
            added.push([lines[index], index - 1]);
            index += 1;
          }
          index -= 1;
          renderRewrite(box, removed, added, numbers, pickFor);
        } else if (line.startsWith("+")) {
          box.append(diffLine("add", "", String(numbers.new++), [line], pickFor(index - 1)));
        } else if (line.startsWith("\\")) {
          box.append(diffLine("meta", "", "", [line]));
        } else {
          box.append(diffLine("", String(numbers.old++), String(numbers.new++), [line || " "]));
        }
      }
    });
  }
  return box;
}
