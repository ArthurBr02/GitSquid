"use strict";

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

function isUntracked(context, path) {
  const entry = (context.files || []).find((file) => file.path === path);
  return Boolean(entry && entry.untracked);
}

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

// From a file's history you want that file at that commit, not the commit.
async function openFileAtCommit(sha, path) {
  try {
    const commit = await api(`/api/commits/${sha}`);
    await openFileView(commitContext(commit), path);
  } catch (error) {
    toast("bad", error.message);
  }
}

async function openFileView(context, path) {
  state.view = { context, path, mode: "diff", diff: null, loading: true, picks: new Set() };
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

// An untracked file has no diff, so no counts.
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
    ...(context.kind === "commit" ? ["-", {
      label: "Restore this version", hint: "into the working tree", danger: true,
      run: confirmed(
        `Bring ${entry.path} back as it was at ${context.where}? Your current version is overwritten.`,
        "restore-file", { sha: context.rev, path: entry.path },
      ),
    }] : []),
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

function viewerHead(view, entry) {
  const { context, path, mode } = view;
  const [dir, name] = splitPath(path);
  const at = context.files.findIndex((file) => file.path === path);
  return fill(clear($("viewer-head")),
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
      onclick: (event) => Menu.show(event, diffViewMenu()),
    }, [icon("dots")]),
    el("span", { class: "viewer-nav" }, [
      el("button", {
        type: "button", class: "icon-btn", title: "Previous file (↑)", "aria-label": "Previous file",
        disabled: at <= 0, onclick: () => viewerStep(-1),
      }, [icon("chevron-left")]),
      el("button", {
        type: "button", class: "icon-btn", title: "Next file (↓)", "aria-label": "Next file",
        disabled: at < 0 || at >= context.files.length - 1, onclick: () => viewerStep(1),
      }, [icon("chevron-right")]),
    ]),
    el("button", {
      type: "button", class: "icon-btn", title: "Back to the graph (Esc)",
      "aria-label": "Back to the graph", onclick: closeViewer,
    }, [icon("close")]),
  );
}

function viewerBody(view, entry) {
  const { context, path, diff, loading, truncated, error, mode, blame } = view;
  const body = clear($("viewer-body"));
  body.classList.toggle("wrap", state.diffView.wrap);

  if (loading) return body.append(el("p", { class: "empty-state", text: "Loading the diff…" }));
  if (error) return body.append(el("p", { class: "banner bad", text: error }));
  if (mode === "blame") {
    body.append(renderBlame(blame));
    if (blame.truncated) {
      body.append(el("p", { class: "banner warn", text: "Only the first eight thousand lines are blamed." }));
    }
    return undefined;
  }
  if (!diff || !diff.trim()) {
    return body.append(el("p", { class: "empty-state", text: entry.binary
      ? "Binary file — nothing to show as text."
      : "No textual difference for this file." }));
  }

  fill(body,
    renderDiff(diff, context.hunks ? context.hunks(entry) : null, { headers: !path }),
    pickBar(),
    truncated
      ? el("p", { class: "banner warn", text: "This patch is very large and was truncated for display." })
      : null,
  );
  if (!body.contains(document.activeElement)) body.scrollTop = 0;
  return undefined;
}

function renderViewer() {
  const showingGraph = !state.view;
  $("pane-head").hidden = !showingGraph;
  $("rows-wrap").hidden = !showingGraph;
  $("graph-empty").hidden = !showingGraph || Boolean(state.rows.filter(matches).length);
  $("viewer").hidden = showingGraph;
  if (showingGraph) return;

  const view = state.view;
  const entry = view.context.files.find((file) => file.path === view.path) || { status: "M", path: view.path };
  viewerHead(view, entry);
  viewerBody(view, entry);
}
