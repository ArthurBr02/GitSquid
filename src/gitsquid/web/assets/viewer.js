"use strict";

/* The middle pane when it shows a file: which files a context holds, how to fetch one,
   and how a diff or a blame is drawn. */

/* ---------- the middle pane: the graph, or one file of it ---------- */

/* A context knows which files it holds and how to fetch the patch of one of them. */
function commitContext(commit) {
  return {
    kind: "commit",
    where: commit.short,
    rev: commit.sha,
    files: commit.files,
    fetch: (path) => api(`/api/commits/${commit.sha}/patch?path=${encodeURIComponent(path)}${readingFlags()}`),
  };
}

function worktreeContext(staged) {
  const files = state.worktree.files
    .filter((file) => (staged ? file.staged : file.unstaged || file.untracked))
    .map((file) => ({
      path: file.path,
      status: file.conflicted ? "U" : (staged ? file.index_code : (file.untracked ? "A" : file.work_code)),
      untracked: file.untracked,
      ...lineCounts(file, staged),
    }));
  return {
    kind: "worktree",
    staged,
    rev: "",
    where: staged ? "staged" : "working tree",
    files,
    fetch: (path) => api(`/api/filediff?path=${encodeURIComponent(path)}&staged=${staged ? 1 : 0}${readingFlags()}`),
    hunks: (entry) => (entry && !entry.untracked ? { staged } : null),
  };
}

function changeContext(change) {
  const files = parseDiff(change.diff).map((file) => ({
    path: filePathOf(file),
    status: fileStatusOf(file),
    added: file.hunks.reduce((n, hunk) => n + hunk.lines.filter((l) => l.startsWith("+")).length, 0),
    removed: file.hunks.reduce((n, hunk) => n + hunk.lines.filter((l) => l.startsWith("-")).length, 0),
    text: [...file.header, ...file.hunks.flatMap((hunk) => hunk.lines)].join("\n") + "\n",
  }));
  return {
    kind: "change",
    where: `change #${change.id}`,
    files,
    fetch: (path) => Promise.resolve({ diff: files.find((file) => file.path === path)?.text || "" }),
  };
}

function filePathOf(file) {
  const header = file.header.find((line) => line.startsWith("diff --git ")) || "";
  const match = /^diff --git a\/(.+?) b\/(.+)$/.exec(header);
  if (match) return match[2];
  const plus = file.header.find((line) => line.startsWith("+++ "));
  return plus ? plus.slice(4).replace(/^b\//, "") : "(unknown file)";
}

function fileStatusOf(file) {
  if (file.header.some((line) => line.startsWith("new file"))) return "A";
  if (file.header.some((line) => line.startsWith("deleted file"))) return "D";
  if (file.header.some((line) => line.startsWith("rename "))) return "R";
  return "M";
}

const readingFlags = () =>
  `${state.diffView.space ? "&ws=1" : ""}&ctx=${state.diffView.context}`;

/* How the diff is read, not what it says: kept between sessions. */
function diffViewMenu() {
  const toggle = (key, refetch) => () => {
    state.diffView[key] = !state.diffView[key];
    remember(`diff.${key}`, state.diffView[key] ? "on" : "off");
    if (refetch && state.view) openFileView(state.view.context, state.view.path);
    else renderViewer();
  };
  const view = state.view;
  return [
    { header: "This file" },
    view && view.mode === "blame"
      ? { label: "Show the diff", run: () => openFileView(view.context, view.path) }
      : { label: "Blame", hint: "who wrote what",
          disabled: !view || !view.path || isUntracked(view.context, view.path),
          run: () => openBlame(view.context, view.path) },
    { label: "Copy this patch", disabled: !view || !view.diff,
      run: () => copy(view.diff, "Patch") },
    { header: "Reading" },
    { label: "Wrap long lines", className: state.diffView.wrap ? "on" : "", run: toggle("wrap", false) },
    { label: "Ignore whitespace", className: state.diffView.space ? "on" : "", run: toggle("space", true) },
    { header: "Context around a change" },
    ...[3, 10, 25].map((lines) => ({
      label: plural(lines, "line"),
      className: state.diffView.context === lines ? "on" : "",
      run: () => {
        state.diffView.context = lines;
        remember("diff.context", String(lines));
        if (state.view) openFileView(state.view.context, state.view.path);
      },
    })),
  ];
}

const UNBORN = "0".repeat(40);

/* git cannot attribute a file it has never seen. */
function isUntracked(context, path) {
  const entry = (context.files || []).find((file) => file.path === path);
  return Boolean(entry && entry.untracked);
}

/* Who last touched each line — the other question you ask of a file. */
async function openBlame(context, path) {
  state.view = { context, path, mode: "blame", loading: true };
  renderViewer();
  try {
    const rev = context.rev ? `&rev=${context.rev}` : "";
    const payload = await api(`/api/blame?path=${encodeURIComponent(path)}${rev}`);
    if (state.view && state.view.path === path) {
      state.view = { ...state.view, blame: payload, loading: false };
    }
  } catch (error) {
    state.view = { ...state.view, loading: false, error: error.message };
  }
  renderViewer();
  markOpenFile();
}

function renderBlame(payload) {
  const box = el("pre", { class: "blame" });
  let previous = null;
  payload.lines.forEach((line, index) => {
    const starts = line.sha !== previous;
    previous = line.sha;
    const local = line.sha === UNBORN;
    box.append(el("div", { class: `blame-line${starts ? " start" : ""}` }, [
      local
        ? el("span", { class: "who local", text: starts ? "uncommitted" : "" })
        : el("button", {
            type: "button", class: "who",
            title: starts ? `${line.summary}\n${line.author} · ${new Date(line.date).toLocaleString()}` : "",
            onclick: () => openCommit(line.sha),
            text: starts ? `${line.short} ${line.author.split(" ")[0]}` : "",
          }),
      el("span", { class: "ln", "aria-hidden": "true", text: String(index + 1) }),
      el("span", { class: "tx", text: line.text || " " }),
    ]));
  });
  return box;
}

async function openFileView(context, path) {
  state.view = { context, path, mode: "diff", diff: null, loading: true };
  renderViewer();
  try {
    const payload = await context.fetch(path);
    if (state.view && state.view.path === path) {
      state.view = { ...state.view, diff: payload.diff, truncated: payload.truncated, loading: false };
    }
  } catch (error) {
    state.view = { ...state.view, diff: "", loading: false, error: error.message };
  }
  renderViewer();
  markOpenFile();
}

function closeViewer() {
  state.view = null;
  renderViewer();
  markOpenFile();
}

/* Walking a commit's files with the arrow keys is the whole point of the file list. */
function viewerStep(delta) {
  if (!state.view) return;
  const paths = state.view.context.files.map((file) => file.path);
  const at = paths.indexOf(state.view.path);
  const next = paths[Math.min(Math.max(at + delta, 0), paths.length - 1)];
  if (next && next !== state.view.path) openFileView(state.view.context, next);
}

function markOpenFile() {
  const open = state.view ? state.view.path : null;
  for (const row of document.querySelectorAll(".file-list .file-row")) {
    row.setAttribute("aria-current", row.dataset.path === open ? "true" : "false");
  }
}

/* An untracked file has no diff to count: every line of it is new. */
function lineCounts(entry, staged) {
  const pair = (entry.counts || {})[staged ? "staged" : "unstaged"];
  if (!pair) return {};
  return pair[0] < 0 ? { binary: true } : { added: pair[0], removed: pair[1] };
}

function fileStats(entry) {
  if (entry.binary) return [el("span", { class: "stat-bin", text: "binary" })];
  if (entry.added === undefined) return [];
  return [
    el("span", { class: "stat-add", text: `+${entry.added}` }),
    el("span", { class: "stat-del", text: `−${entry.removed}` }),
  ];
}

/* The same file row in a commit, in a change, and in the working tree. */
/* Twenty files under src/main/java/com/example/thing/ are twenty names, not twenty paths:
   the folder is said once, and the rows carry what tells them apart. */
function commonRoot(directories) {
  const parts = directories.filter(Boolean).map((dir) => dir.replace(/\/$/, "").split("/"));
  if (parts.length < 2) return "";
  const shortest = Math.min(...parts.map((one) => one.length));
  let shared = 0;
  while (shared < shortest && parts.every((one) => one[shared] === parts[0][shared])) shared += 1;
  const root = parts[0].slice(0, shared).join("/");
  return root.length > 12 ? `${root}/` : "";
}

function renderFileList(context, files, label, decorate = null) {
  const groups = new Map();
  for (const entry of files) {
    const [dir] = splitPath(entry.path);
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir).push(entry);
  }
  const root = commonRoot([...groups.keys()]);

  const list = el("div", { class: "file-list", role: "listbox", "aria-label": label });
  for (const [dir, entries] of groups) {
    if (dir) {
      list.append(el("div", { class: "file-folder", title: dir,
        text: dir.slice(root.length).replace(/\/$/, "") || "." }));
    }
    for (const entry of entries) {
      const shaped = dir ? { ...entry, display: splitPath(entry.path)[1], nested: true } : entry;
      const own = decorate ? decorate(entry) : null;
      const row = fileListRow(context, shaped, own ? own.extras : []);
      list.append(own ? Menu.attach(row, own.menu) : row);
    }
  }
  return root
    ? el("div", {}, [el("div", { class: "file-root", title: root, text: root }), list])
    : list;
}

function fileRowMenu(context, entry) {
  return [
    { header: entry.path },
    { label: "Show the diff", run: () => openFileView(context, entry.path) },
    { label: "Blame", hint: "who wrote what", disabled: Boolean(entry.untracked),
      run: () => openBlame(context, entry.path) },
    { label: "File history", run: () => openFileHistory(entry.path) },
    "-",
    { label: "Copy path", run: () => copy(entry.path, "Path") },
  ];
}

function fileListRow(context, entry, extras = []) {
  const [dir, name] = splitPath(entry.display || entry.path);
  const row = el("div", {
    class: `file-row${entry.nested ? " nested" : ""}`,
    role: "option",
    tabindex: "0",
    "data-path": entry.path,
    "aria-current": state.view && state.view.path === entry.path ? "true" : "false",
    "aria-label": `${entry.path}, ${STATUS_WORDS[entry.status] || "changed"}`,
    onclick: () => openFileView(context, entry.path),
    onkeydown: (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openFileView(context, entry.path); }
    },
  }, [
    el("span", { class: `code ${entry.status}`, title: STATUS_WORDS[entry.status] || "", text: entry.status }),
    el("span", { class: "path", title: entry.original ? `${entry.original} → ${entry.path}` : entry.path },
      [el("span", { class: "dir", text: `${dir}\u200e` }), el("span", { class: "name", text: name })]),
    entry.sensitive ? el("span", { class: "warn-flag", title: "Credential-shaped file — never indexed", text: "⚠" }) : null,
    el("span", { class: "stats" }, fileStats(entry)),
    ...extras,
  ]);
  return extras.length ? row : Menu.attach(row, () => fileRowMenu(context, entry));
}

const STATUS_WORDS = {
  A: "added", M: "modified", D: "deleted", R: "renamed", C: "copied",
  U: "conflicted", T: "type changed", "?": "untracked",
};

function renderViewer() {
  const viewer = $("viewer");
  const graphIsShowing = !state.view;
  $("pane-head").hidden = !graphIsShowing;
  $("rows-wrap").hidden = !graphIsShowing;
  $("graph-empty").hidden = !graphIsShowing || Boolean(state.rows.filter(matches).length);
  viewer.hidden = graphIsShowing;
  if (graphIsShowing) return;

  const { context, path, diff, loading, truncated, error, mode, blame } = state.view;
  const entry = context.files.find((file) => file.path === path) || { status: "M", path };
  const [dir, name] = splitPath(path);
  const at = context.files.findIndex((file) => file.path === path);

  fill(clear($("viewer-head")),
    path ? el("span", { class: `code ${entry.status}`, title: STATUS_WORDS[entry.status] || "", text: entry.status }) : null,
    path
      ? el("span", { class: "path", title: path },
          [el("span", { class: "dir", text: `${dir}\u200e` }), el("span", { class: "name", text: name })])
      : el("span", { class: "path" }, [el("span", { class: "name", text: `Whole patch of ${context.where}` })]),
    el("span", { class: "stats" }, fileStats(entry)),
    mode === "blame" ? el("span", { class: "tag ref", text: "blame" }) : null,
    el("span", { class: "where", text: context.where }),
    el("button", {
      type: "button", class: "icon-btn", title: "How this diff is shown",
      "aria-haspopup": "menu", "aria-label": "Diff options",
      onclick: (event) => Menu.show(event, diffViewMenu()), text: "⋯",
    }),
    el("span", { class: "viewer-nav" }, [
      el("button", {
        type: "button", class: "icon-btn", title: "Previous file (↑)", "aria-label": "Previous file",
        disabled: at <= 0, onclick: () => viewerStep(-1), text: "‹",
      }),
      el("button", {
        type: "button", class: "icon-btn", title: "Next file (↓)", "aria-label": "Next file",
        disabled: at < 0 || at >= context.files.length - 1, onclick: () => viewerStep(1), text: "›",
      }),
    ]),
    el("button", {
      type: "button", class: "icon-btn", title: "Back to the graph (Esc)",
      "aria-label": "Back to the graph", onclick: closeViewer, text: "✕",
    }),
  );

  const body = clear($("viewer-body"));
  body.classList.toggle("wrap", state.diffView.wrap);
  if (loading) {
    body.append(el("p", { class: "empty-state", text: "Loading the diff…" }));
    return;
  }
  if (error) {
    body.append(el("p", { class: "banner bad", text: error }));
    return;
  }
  if (mode === "blame") {
    body.append(renderBlame(blame));
    if (blame.truncated) {
      body.append(el("p", { class: "banner warn", text: "Only the first eight thousand lines are blamed." }));
    }
    return;
  }
  if (!diff || !diff.trim()) {
    body.append(el("p", { class: "empty-state", text: entry.binary
      ? "Binary file — nothing to show as text."
      : "No textual difference for this file." }));
    return;
  }
  const singleFile = Boolean(path);
  body.append(renderDiff(diff, context.hunks ? context.hunks(entry) : null, { headers: !singleFile }));
  if (!body.contains(document.activeElement)) body.scrollTop = 0;
  if (truncated) {
    body.append(el("p", { class: "banner warn", text: "This patch is very large and was truncated for display." }));
  }
}

/* ---------- diff rendering with line numbers ---------- */

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

function diffLine(kind, oldNumber, newNumber, content) {
  return el("div", { class: kind }, [
    el("span", { class: "ln old", "aria-hidden": "true", text: oldNumber }),
    el("span", { class: "ln new", "aria-hidden": "true", text: newNumber }),
    el("span", { class: "tx" }, content),
  ]);
}

/* What actually changed inside a rewritten line, when it is a small part of it. */
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

/* A run of removed lines followed by as many added ones is one edit, read line by line. */
function renderRewrite(box, removed, added, numbers) {
  const paired = removed.length === added.length;
  removed.forEach((line, index) => {
    const cut = paired ? inlineParts(line.slice(1), added[index].slice(1)) : null;
    box.append(diffLine("del", String(numbers.old++), "", markedLine(line, cut)));
  });
  added.forEach((line, index) => {
    const cut = paired ? inlineParts(removed[index].slice(1), line.slice(1)) : null;
    box.append(diffLine("add", "", String(numbers.new++), markedLine(line, cut)));
  });
}

const MAX_DIFF_LINES = 4000;

/* `actions` is set only for a working-tree file, where a single hunk can be staged. */
function renderDiff(diff, actions = null, { headers = true } = {}) {
  const box = el("pre", { class: "diff" });
  let budget = MAX_DIFF_LINES;
  for (const file of parseDiff(diff)) {
    if (budget <= 0) break;
    if (headers) for (const line of file.header) box.append(diffLine("meta", "", "", [line]));
    for (const hunk of file.hunks) {
      if (actions) box.append(hunkBar(file, hunk, actions));
      const numbers = { old: 0, new: 0 };
      if ((budget -= hunk.lines.length) <= 0) {
        box.append(diffLine("meta", "", "", [
          "\u2026 the rest of this patch is not shown. Open a file on its own to read it in full.",
        ]));
        break;
      }
      const lines = hunk.lines;
      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        if (line.startsWith("@@")) {
          const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
          if (match) { numbers.old = Number(match[1]); numbers.new = Number(match[2]); }
          box.append(diffLine("hunk", "", "", [line]));
        } else if (line.startsWith("-")) {
          const removed = [];
          const added = [];
          while (index < lines.length && lines[index].startsWith("-")) removed.push(lines[index++]);
          while (index < lines.length && lines[index].startsWith("+")) added.push(lines[index++]);
          index -= 1;
          renderRewrite(box, removed, added, numbers);
        } else if (line.startsWith("+")) {
          box.append(diffLine("add", "", String(numbers.new++), [line]));
        } else if (line.startsWith("\\")) {
          box.append(diffLine("meta", "", "", [line]));
        } else {
          box.append(diffLine("", String(numbers.old++), String(numbers.new++), [line || " "]));
        }
      }
    }
  }
  return box;
}

async function applyHunk(patch, target) {
  const opened = state.view;
  await quiet(withBusy("Applying the hunk…", async () => {
    toast("ok", (await post(`/api/worktree/${target}-hunk`, { patch })).message);
    await refresh();
    if (opened) await openFileView(worktreeContext(opened.context.staged), opened.path);
  }));
}
