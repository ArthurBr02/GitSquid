"use strict";

const ROW_H = 42;

const STATUS_COLORS = {
  proposed: "#58a6ff",
  applied: "#f0b429",
  verified: "#5ed69a",
  failed: "#ff6b6b",
  reverted: "#c792ea",
};

const state = {
  data: null,
  worktree: { files: [], staged: 0, unstaged: 0, conflicted: 0 },
  repos: { active: "", repos: [] },
  rows: [],
  filter: "all",
  query: "",
  selected: null,
  selectedFile: null,
  fileDiff: null,
  commitMessage: "",
  busy: false,
};

/* ---------- DOM helpers (no innerHTML: every value is set as text) ---------- */

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);
const clear = (node) => { while (node.firstChild) node.firstChild.remove(); return node; };

function relativeTime(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso.slice(0, 10);
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 2592000) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(then).toISOString().slice(0, 10);
}

function splitPath(path) {
  const cut = path.lastIndexOf("/");
  return cut === -1 ? ["", path] : [path.slice(0, cut + 1), path.slice(cut + 1)];
}

/* ---------- api ---------- */

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch { /* empty body */ }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

/* ---------- feedback ---------- */

function status(message, busy = false) {
  const target = clear($("status-text"));
  if (busy) target.append(el("span", { class: "spinner", "aria-hidden": "true" }), " ");
  target.append(message);
}

function toast(kind, message) {
  const node = el("div", { class: `toast ${kind}`, role: "status" }, [
    el("span", { class: "label", text: kind === "ok" ? "Success" : kind === "bad" ? "Failure" : "Info" }),
    el("span", { text: message }),
  ]);
  $("toasts").append(node);
  setTimeout(() => node.remove(), kind === "bad" ? 9000 : 5000);
}

async function withBusy(message, task) {
  if (state.busy) return undefined;
  state.busy = true;
  status(message, true);
  document.body.setAttribute("aria-busy", "true");
  try {
    return await task();
  } catch (error) {
    toast("bad", error.message);
    throw error;
  } finally {
    state.busy = false;
    status("Ready.");
    document.body.removeAttribute("aria-busy");
  }
}

const quiet = (promise) => promise.catch(() => {});

/* ---------- rows ---------- */

/* Commits keep the topological order git gave them — sorting them by date would let a parent
   land above its child, and the lanes would then run off the bottom of the list. */
function buildRows() {
  const { commits, changes } = state.data.graph;
  const pending = changes
    .map((change) => ({ ...change, kind: "change", key: `c${change.id}`, when: change.created_at }))
    .sort((a, b) => (a.when < b.when ? 1 : a.when > b.when ? -1 : 0));

  const rows = [];
  let next = 0;
  for (const commit of commits) {
    while (next < pending.length && pending[next].when > commit.date) rows.push(pending[next++]);
    rows.push({ ...commit, kind: "commit", key: `g${commit.sha}`, when: commit.date });
  }
  rows.push(...pending.slice(next));
  if (state.worktree.files.length) {
    rows.unshift({ kind: "wip", key: "wip", when: new Date().toISOString() });
  }
  return rows;
}

function matches(row) {
  if (row.kind === "wip") return state.filter === "all" && !state.query;
  if (state.filter !== "all") {
    if (state.filter === "commits") return row.kind === "commit";
    if (row.kind !== "change" || row.status !== state.filter) return false;
  }
  if (!state.query) return true;
  const haystack = row.kind === "change"
    ? `${row.task} ${row.files.join(" ")} ${row.source}`
    : `${row.subject} ${row.author} ${row.short}`;
  return haystack.toLowerCase().includes(state.query);
}

function renderRows() {
  const wrap = $("rows-wrap");
  const list = clear($("rows"));
  const visible = state.rows.filter(matches);
  // A filtered list has holes in it, so the lanes would join rows that are not adjacent.
  const flat = Boolean(state.query) || (state.filter !== "all" && state.filter !== "commits");
  const laid = Graph.layout(visible, { flat });
  const budget = Math.min(Math.round((wrap.clientWidth || 700) / 3), 280);
  const { gap, width } = Graph.measure(laid.columns, budget);

  wrap.style.setProperty("--lane-width", `${width}px`);
  list.setAttribute("aria-activedescendant", state.selected ? `row-${state.selected}` : "");

  const emptyNote = $("graph-empty");
  emptyNote.hidden = visible.length > 0;
  if (!visible.length) {
    emptyNote.textContent = state.rows.length
      ? "No row matches this filter."
      : "No change recorded and no commit yet. Use \u201cNew change\u201d to propose your first diff.";
  }

  for (const row of visible) {
    const item = el("li", {
      class: `row${row.kind === "wip" ? " wip" : ""}`,
      role: "option",
      id: `row-${row.key}`,
      "aria-selected": state.selected === row.key ? "true" : "false",
      onclick: () => select(row.key),
    });

    const main = el("div", { class: "row-main" });
    const side = el("div", { class: "row-side" });

    if (row.kind === "wip") {
      main.append(
        el("span", { class: "row-title", text: "Uncommitted changes" }),
        el("span", { class: "tag applied", text: "WIP" }),
      );
      side.append(el("span", { text: `${state.worktree.files.length} file(s)` }));
    } else if (row.kind === "change") {
      main.append(
        el("span", { class: `tag ${row.status}`, text: row.status }),
        el("span", { class: "row-title", text: row.task }),
        row.is_sample ? el("span", { class: "tag sample", text: "sample" }) : null,
      );
      side.append(el("span", { text: `#${row.id}` }), el("span", { text: `${row.files.length} file(s)` }));
    } else {
      main.append(el("span", { class: "row-title", text: row.subject || "(no message)" }));
      // Two refs at most: beyond that the subject loses more than the badges add.
      for (const ref of row.refs.slice(0, 2)) {
        main.append(el("span", { class: "tag ref", title: ref, text: ref }));
      }
      if (row.refs.length > 2) {
        main.append(el("span", {
          class: "tag more", title: row.refs.join(", "), text: `+${row.refs.length - 2}`,
        }));
      }
      side.append(
        el("span", { class: "who", title: row.author, text: row.author }),
        el("span", { class: "sha", text: row.short }),
      );
    }
    side.append(el("span", { class: "relative when", text: relativeTime(row.when) }));
    item.append(main, side);
    list.append(item);
  }

  Graph.paint($("graph-canvas"), laid, {
    rowHeight: ROW_H,
    gap,
    width,
    pendingColor: (row) => (row.kind === "wip" ? "#f0b429" : STATUS_COLORS[row.status] || "#58a6ff"),
    isSelected: (row) => row.key === state.selected,
  });
}

/* ---------- sidebar ---------- */

function renderSidebar() {
  const { repo, config, index, counts, total_changes: total, samples } = state.data.state;
  $("repo-name").textContent = repo.name;
  $("repo-branch").textContent = repo.branch;
  $("db-path").textContent = config.database;
  $("branch-base").textContent = repo.branch;

  const model = clear($("model-state"));
  model.append(
    el("span", { class: `dot ${config.model_available ? "on" : "off"}`, "aria-hidden": "true" }),
    el("span", {
      text: config.model_available
        ? `${config.model} · effort ${config.effort}`
        : "degraded mode — no API key, paste diffs instead",
    }),
  );

  const filters = clear($("filters"));
  const entries = [
    ["all", "Everything", total + state.data.graph.commits.length],
    ...Object.entries(counts).map(([name, count]) => [name, name[0].toUpperCase() + name.slice(1), count]),
    ["commits", "Commits", state.data.graph.commits.length],
  ];
  for (const [key, label, count] of entries) {
    filters.append(el("li", {}, [
      el("button", {
        type: "button",
        role: "option",
        "aria-selected": state.filter === key ? "true" : "false",
        onclick: () => { state.filter = key; renderSidebar(); renderRows(); },
      }, [
        el("span", { class: "swatch", style: `background:${STATUS_COLORS[key] || "#6b7488"}` }),
        el("span", { text: label }),
        el("span", { class: "count", text: String(count) }),
      ]),
    ]));
  }

  const wipButton = $("btn-wip");
  wipButton.setAttribute("aria-current", state.selected === "wip" ? "true" : "false");
  wipButton.disabled = state.worktree.files.length === 0;
  const counts_ = clear($("wip-counts"));
  if (!state.worktree.files.length) {
    counts_.append(el("span", { text: "clean" }));
  } else {
    counts_.append(
      el("span", { class: state.worktree.staged ? "on" : "", text: `${state.worktree.staged} staged` }),
      el("span", { text: ` · ${state.worktree.unstaged} unstaged` }),
    );
  }

  const tracking = repo.tracking || { ahead: 0, behind: 0, upstream: null };
  const hasRemote = (repo.remotes || []).length > 0;
  for (const [id, count] of [["badge-ahead", tracking.ahead], ["badge-behind", tracking.behind]]) {
    const badge = $(id);
    badge.hidden = !count;
    badge.textContent = String(count || "");
  }
  for (const id of ["btn-fetch", "btn-pull", "btn-push"]) {
    $(id).disabled = !hasRemote;
    $(id).title = hasRemote ? $(id).title : "This repository has no remote configured.";
  }

  const stashes = clear($("stashes"));
  if (!(repo.stashes || []).length) {
    stashes.append(el("li", {}, [el("span", { class: "empty", text: "No stash." })]));
  }
  for (const stash of repo.stashes || []) {
    stashes.append(el("li", {}, [
      el("span", { class: "subject", title: stash.subject, text: stash.subject }),
      el("span", { class: "age", text: stash.age }),
      el("button", {
        type: "button", class: "btn tiny ghost", "aria-label": `Restore ${stash.ref}`,
        onclick: () => worktreeAction("stash-pop", null, { ref: stash.ref }), text: "Pop",
      }),
      el("button", {
        type: "button", class: "btn tiny danger", "aria-label": `Drop ${stash.ref}`,
        onclick: () => {
          if (confirm(`Drop ${stash.ref}? Its content is lost.`)) {
            worktreeAction("stash-drop", null, { ref: stash.ref });
          }
        },
        text: "Drop",
      }),
    ]));
  }

  const branches = clear($("branches"));
  if (!repo.branches.length) {
    branches.append(el("li", {}, [el("span", { class: "count", text: "no branch yet" })]));
  }
  for (const branch of repo.branches) {
    const isCurrent = branch.name === repo.branch;
    branches.append(el("li", {}, [
      el("button", {
        type: "button",
        class: `name ${isCurrent ? "current" : ""}`,
        "aria-current": isCurrent ? "true" : "false",
        title: isCurrent ? "Current branch" : `Switch to ${branch.name}`,
        onclick: () => { if (!isCurrent) switchBranch(branch.name); },
      }, [
        el("span", { text: branch.name }),
        el("span", { class: "count", text: branch.sha }),
      ]),
      !isCurrent ? el("span", { class: "row-act" }, [
        el("button", {
          type: "button", class: "btn tiny ghost",
          "aria-label": `Merge ${branch.name} into ${repo.branch}`,
          title: `Merge into ${repo.branch}`,
          onclick: () => {
            if (confirm(`Merge ${branch.name} into ${repo.branch}?`)) {
              worktreeAction("merge", null, { branch: branch.name });
            }
          },
          text: "Merge",
        }),
        el("button", {
          type: "button", class: "btn tiny danger", "aria-label": `Delete ${branch.name}`,
          onclick: () => {
            if (confirm(`Delete the branch ${branch.name}?`)) {
              worktreeAction("delete-branch", null, { branch: branch.name });
            }
          },
          text: "×",
        }),
      ]) : null,
    ]));
  }

  const stats = clear($("index-stats"));
  for (const [label, value] of [
    ["Files", index.files.toLocaleString()],
    ["Chunks", index.chunks.toLocaleString()],
    ["Changes", String(total)],
    ["Samples", String(samples)],
    ["Budget", `${(config.context_budget / 1000).toFixed(0)}k chars`],
  ]) {
    stats.append(el("dt", { text: label }), el("dd", { text: value }));
  }
}

/* ---------- diff rendering with line numbers ---------- */

function renderDiff(diff) {
  const box = el("pre", { class: "diff" });
  let oldLine = 0;
  let newLine = 0;

  for (const line of diff.replace(/\n$/, "").split("\n")) {
    let kind = "";
    let number = "";
    if (line.startsWith("@@")) {
      kind = "hunk";
      const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (match) { oldLine = Number(match[1]); newLine = Number(match[2]); }
    } else if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ")
      || line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file")
      || line.startsWith("similarity ") || line.startsWith("rename ")) {
      kind = "meta";
    } else if (line.startsWith("+")) {
      kind = "add"; number = String(newLine++);
    } else if (line.startsWith("-")) {
      kind = "del"; number = String(oldLine++);
    } else if (line.startsWith(" ") || line === "") {
      number = String(newLine++); oldLine++;
    }
    box.append(el("div", { class: kind }, [
      el("span", { class: "ln", "aria-hidden": "true", text: number }),
      el("span", { class: "tx", text: line || " " }),
    ]));
  }
  return box;
}

/* ---------- working tree detail ---------- */

function fileRow(entry, staged) {
  const code = staged ? entry.index_code : (entry.untracked ? "A" : entry.work_code);
  const [dir, name] = splitPath(entry.path);
  const key = `${staged ? "s" : "u"}:${entry.path}`;

  return el("div", {
    class: "file-row",
    role: "option",
    tabindex: "0",
    "aria-current": state.selectedFile === key ? "true" : "false",
    "aria-label": `${entry.path}, ${staged ? entry.index_label : entry.work_label || "untracked"}`,
    onclick: () => openFile(entry.path, staged),
    onkeydown: (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openFile(entry.path, staged); } },
  }, [
    el("span", { class: `code ${code}`, title: staged ? entry.index_label : entry.work_label, text: code }),
    el("span", { class: "path" }, [el("span", { class: "dir", text: dir }), el("span", { text: name })]),
    entry.sensitive ? el("span", { class: "warn-flag", title: "Credential-shaped file — never indexed", text: "⚠" }) : null,
    el("span", { class: "row-act" }, [
      el("button", {
        type: "button", class: "btn tiny ghost",
        "aria-label": `${staged ? "Unstage" : "Stage"} ${entry.path}`,
        onclick: (event) => { event.stopPropagation(); worktreeAction(staged ? "unstage" : "stage", [entry.path]); },
        text: staged ? "Unstage" : "Stage",
      }),
      !staged ? el("button", {
        type: "button", class: "btn tiny danger",
        "aria-label": `Discard ${entry.path}`,
        onclick: (event) => {
          event.stopPropagation();
          if (confirm(`Discard your edits to ${entry.path}? This cannot be undone.`)) {
            worktreeAction("discard", [entry.path]);
          }
        },
        text: "Discard",
      }) : null,
    ]),
  ]);
}

function renderWorktreeDetail() {
  const detail = clear($("detail"));
  const files = state.worktree.files;
  const staged = files.filter((file) => file.staged);
  const unstaged = files.filter((file) => file.unstaged || file.untracked);

  detail.append(el("div", { class: "detail-head" }, [
    el("h2", { text: "Uncommitted changes" }),
    el("div", { class: "detail-sub" }, [
      el("span", { text: `${files.length} file(s)` }),
      el("span", { text: `${staged.length} staged` }),
      el("span", { text: `${unstaged.length} unstaged` }),
      state.worktree.conflicted ? el("span", { class: "tag failed", text: "conflicts" }) : null,
    ]),
  ]));

  const message = el("textarea", {
    id: "commit-message", rows: "3", maxlength: "4000",
    placeholder: "Commit message — describe what this commit does",
    "aria-label": "Commit message",
    oninput: (event) => { state.commitMessage = event.target.value; },
  });
  message.value = state.commitMessage;

  detail.append(el("div", { class: "commit-box" }, [
    message,
    el("div", { class: "row-actions" }, [
      el("button", {
        type: "button", class: "btn primary", disabled: staged.length === 0,
        onclick: () => commitStaged(),
        text: `Commit ${staged.length} file(s)`,
      }),
      el("span", { class: "staged-note", text: staged.length ? "" : "Stage a file to enable the commit." }),
    ]),
  ]));

  for (const [label, list, isStaged] of [["Staged", staged, true], ["Unstaged", unstaged, false]]) {
    const group = el("div", { class: "file-group" });
    group.append(el("div", { class: "file-group-head" }, [
      el("span", { text: label }),
      el("span", { class: "count", text: String(list.length) }),
      list.length ? el("button", {
        type: "button", class: "btn tiny ghost",
        onclick: () => worktreeAction(isStaged ? "unstage" : "stage", list.map((file) => file.path)),
        text: isStaged ? "Unstage all" : "Stage all",
      }) : null,
    ]));
    if (!list.length) {
      group.append(el("p", { class: "empty-state", style: "padding:14px 18px;text-align:left", text: isStaged ? "Nothing staged yet." : "No unstaged edit." }));
    }
    for (const entry of list) group.append(fileRow(entry, isStaged));
    detail.append(group);
  }

  if (state.fileDiff) {
    detail.append(el("div", { class: "diff-head" }, [
      el("span", { text: state.fileDiff.path }),
      el("span", { class: `tag ${state.fileDiff.staged ? "verified" : "applied"}`, text: state.fileDiff.staged ? "staged" : "unstaged" }),
    ]));
    detail.append(el("section", { class: "detail-section" }, [
      state.fileDiff.diff.trim()
        ? renderDiff(state.fileDiff.diff)
        : el("p", { class: "prose", text: "No textual diff — the file may be binary or unchanged." }),
    ]));
  }
}

async function openFile(path, staged) {
  state.selectedFile = `${staged ? "s" : "u"}:${path}`;
  renderWorktreeDetail();
  try {
    state.fileDiff = await api(`/api/filediff?path=${encodeURIComponent(path)}&staged=${staged ? 1 : 0}`);
  } catch (error) {
    state.fileDiff = { path, staged, diff: "" };
    toast("bad", error.message);
  }
  renderWorktreeDetail();
}

async function worktreeAction(action, paths, extra = {}) {
  await quiet(withBusy(`Running ${action}…`, async () => {
    const result = await post(`/api/worktree/${action}`, { paths, ...extra });
    toast("ok", result.message);
    state.fileDiff = null;
    state.selectedFile = null;
    await refresh();
  }));
}

async function commitStaged() {
  const message = ($("commit-message") || {}).value || state.commitMessage;
  if (!message.trim()) {
    toast("bad", "A commit message is required.");
    $("commit-message").focus();
    return;
  }
  await quiet(withBusy("Committing…", async () => {
    const result = await post("/api/worktree/commit", { message });
    toast("ok", result.message);
    state.commitMessage = "";
    state.fileDiff = null;
    state.selectedFile = null;
    await refresh(false);
  }));
}

async function switchBranch(name) {
  await quiet(withBusy(`Switching to ${name}…`, async () => {
    const result = await post("/api/worktree/checkout", { branch: name });
    toast("ok", result.message);
    await refresh(false);
  }));
}

/* ---------- change and commit detail ---------- */

function actionButton(label, kind, handler, disabled) {
  return el("button", { type: "button", class: `btn ${kind}`, disabled, onclick: handler, text: label });
}

async function renderChangeDetail(id) {
  const detail = clear($("detail"));
  detail.append(el("div", { class: "empty-state" }, [el("p", { text: "Loading change…" })]));
  const change = await api(`/api/changes/${id}`);
  clear(detail);

  const head = el("div", { class: "detail-head" }, [el("h2", { text: change.task })]);
  head.append(el("div", { class: "detail-sub" }, [
    el("span", { class: `tag ${change.status}`, text: change.status }),
    el("span", { text: `#${change.id}` }),
    el("span", { text: change.source }),
    el("span", { text: `+${change.added} / -${change.removed}` }),
    el("span", { class: "relative", text: relativeTime(change.created_at) }),
    change.is_sample ? el("span", { class: "tag sample", text: "sample" }) : null,
  ]));

  const after = (task) => quiet(task().then(() => refresh()));
  const applicable = ["proposed", "failed"].includes(change.status);
  head.append(el("div", { class: "detail-actions" }, [
    actionButton("Apply", "primary", () => after(() =>
      withBusy(`Applying change #${id}…`, async () => {
        toast("ok", (await post(`/api/changes/${id}/apply`)).message);
      })), change.is_sample || !applicable || !change.applies_cleanly),
    actionButton("Run tests", "ghost", () => after(() =>
      withBusy("Running the test command…", async () => {
        const result = await post(`/api/changes/${id}/test`);
        toast(result.passed ? "ok" : "bad", result.message);
      })), !["applied", "verified", "failed"].includes(change.status)),
    actionButton("Revert", "danger", () => {
      if (!confirm(`Reverse change #${id} in the working tree?`)) return;
      after(() => withBusy(`Reverting change #${id}…`, async () => {
        toast("ok", (await post(`/api/changes/${id}/revert`)).message);
      }));
    }, change.is_sample || !["applied", "verified", "failed"].includes(change.status)),
  ]));
  detail.append(head);

  if (change.is_sample) {
    detail.append(el("div", { class: "detail-section" }, [
      el("p", { class: "banner warn", text: "Sample record. It describes a fictional billing module, is never applied, and is deleted by “Clear samples”." }),
    ]));
  } else if (!change.applies_cleanly && change.status === "proposed") {
    detail.append(el("div", { class: "detail-section" }, [
      el("p", { class: "banner bad", text: `git refuses this patch: ${change.check_message}` }),
    ]));
  }

  if (change.rationale) {
    detail.append(el("section", { class: "detail-section" }, [
      el("h3", { text: "Rationale" }), el("p", { class: "prose", text: change.rationale }),
    ]));
  }

  const facts = el("dl", { class: "kv" });
  for (const [label, value] of [
    ["Files", change.files.join(", ") || "none"],
    ["Model", change.model || "none"],
    ["Digest", change.digest],
    ["Base commit", (change.base_commit || "unknown").slice(0, 12)],
    ["Applied", change.applied_at || "never"],
    ["Tokens", change.input_tokens ? `${change.input_tokens} in / ${change.output_tokens} out` : "n/a"],
  ]) {
    facts.append(el("dt", { text: label }), el("dd", { text: value }));
  }
  detail.append(el("section", { class: "detail-section" }, [el("h3", { text: "Details" }), facts]));
  detail.append(el("section", { class: "detail-section" }, [el("h3", { text: "Diff" }), renderDiff(change.diff)]));

  const runs = el("section", { class: "detail-section" }, [el("h3", { text: "Test runs" })]);
  if (!change.test_runs.length) {
    runs.append(el("p", { class: "prose", text: "Not verified yet. Apply the change, then run the tests." }));
  }
  for (const run of change.test_runs) {
    runs.append(el("div", { class: `run ${run.passed ? "pass" : "fail"}` }, [
      el("div", { class: "run-head" }, [
        el("span", { text: run.passed ? "passed" : `failed (exit ${run.exit_code})` }),
        el("span", { text: run.command }),
        el("span", { text: `${run.duration_ms}ms` }),
      ]),
      el("pre", { text: run.output || "(no output)" }),
    ]));
  }
  detail.append(runs);

  const trail = el("ol", { class: "trail" });
  for (const event of change.events) {
    trail.append(el("li", {}, [
      el("span", { text: event.created_at }),
      el("span", { class: "kind", text: event.kind }),
      el("span", { text: event.message }),
    ]));
  }
  detail.append(el("section", { class: "detail-section" }, [el("h3", { text: "Audit trail" }), trail]));
}

async function renderCommitDetail(sha) {
  const row = state.rows.find((item) => item.key === `g${sha}`);
  const detail = clear($("detail"));
  const commit = await api(`/api/commits/${sha}`);

  detail.append(el("div", { class: "detail-head" }, [
    el("h2", { text: row.subject || "(no message)" }),
    el("div", { class: "detail-sub" }, [
      el("span", { text: row.short }),
      el("span", { text: row.author }),
      el("span", { class: "relative", text: relativeTime(row.date) }),
      ...row.refs.map((ref) => el("span", { class: "tag ref", text: ref })),
    ]),
  ]));

  if (commit.body && commit.body !== row.subject) {
    detail.append(el("section", { class: "detail-section" }, [
      el("h3", { text: "Message" }), el("p", { class: "prose", text: commit.body }),
    ]));
  }

  const files = el("ul", { class: "files" });
  for (const file of commit.files) {
    files.append(el("li", {}, [el("span", { class: "st", text: file.status }), el("span", { text: file.path })]));
  }
  detail.append(el("section", { class: "detail-section" }, [
    el("h3", { text: `Files (${commit.files.length})` }),
    commit.files.length ? files : el("p", { class: "prose", text: "No file changed." }),
  ]));

  detail.append(el("section", { class: "detail-section" }, [
    el("h3", { text: "Diff" }),
    commit.diff.trim() ? renderDiff(commit.diff) : el("p", { class: "prose", text: "No textual diff." }),
    commit.truncated ? el("p", { class: "banner warn", text: "This patch is very large and was truncated for display." }) : null,
  ]));
}

/* ---------- selection ---------- */

function select(key) {
  state.selected = key;
  if (key !== "wip") { state.selectedFile = null; state.fileDiff = null; }
  renderRows();
  renderSidebar();
  const node = $(`row-${key}`);
  if (node) node.scrollIntoView({ block: "nearest" });

  if (key === "wip") { renderWorktreeDetail(); return; }
  const row = state.rows.find((item) => item.key === key);
  if (!row) return;
  const render = row.kind === "change" ? renderChangeDetail(row.id) : renderCommitDetail(row.sha);
  render.catch((error) => {
    clear($("detail")).append(el("div", { class: "empty-state" }, [
      el("h2", { text: "Could not load this item" }), el("p", { text: error.message }),
    ]));
  });
}

function move(step) {
  const visible = state.rows.filter(matches);
  if (!visible.length) return;
  const current = visible.findIndex((row) => row.key === state.selected);
  const next = Math.min(Math.max(current + step, 0), visible.length - 1);
  select(visible[current === -1 ? 0 : next].key);
}

/* ---------- data ---------- */

async function refresh(keepSelection = true) {
  const [stateData, graph, worktreeData, repos] = await Promise.all([
    api("/api/state"), api("/api/graph"), api("/api/worktree"), api("/api/repos"),
  ]);
  state.data = { state: stateData, graph };
  state.worktree = worktreeData;
  state.repos = repos;
  state.rows = buildRows();

  const stillThere = state.rows.some((row) => row.key === state.selected);
  if (!keepSelection || !stillThere) {
    state.selected = state.rows.length ? state.rows[0].key : null;
  }
  renderSidebar();
  renderRows();
  if (state.selected) select(state.selected);
}

/* ---------- dialogs ---------- */

function showFormError(node, message) {
  node.textContent = message;
  node.hidden = false;
  node.scrollIntoView({ block: "nearest" });
}

function openPropose() {
  const available = state.data.state.config.model_available;
  $("propose-hint").textContent = available
    ? `The task and the retrieved excerpts go to ${state.data.state.config.model}. Paste a diff below to skip the model.`
    : "No API key configured, so a diff is required. gitia validates, applies, tests and records it exactly the same way.";
  $("patch-requirement").textContent = available ? "optional" : "required";
  $("propose-error").hidden = true;
  $("propose-modal").showModal();
  $("propose-task").focus();
}

async function submitPropose(event) {
  event.preventDefault();
  const task = $("propose-task").value.trim();
  const patch = $("propose-patch").value;
  const error = $("propose-error");
  const available = state.data.state.config.model_available;

  if (!task) return showFormError(error, "Describe the change you want. The task is required.");
  if (!available && !patch.trim()) return showFormError(error, "Without an API key a unified diff is required.");
  if (patch.trim() && !patch.includes("@@")) return showFormError(error, "That does not look like a unified diff — no @@ hunk header found.");
  error.hidden = true;

  try {
    const result = await withBusy("Proposing a change…", () => post("/api/propose", { task, patch }));
    $("propose-modal").close();
    $("propose-task").value = "";
    $("propose-patch").value = "";
    toast(result.applies_cleanly ? "ok" : "bad",
      result.applies_cleanly ? result.message : `${result.message} git refuses it: ${result.check_message}`);
    await refresh(false);
    select(`c${result.change_id}`);
  } catch (failure) {
    showFormError(error, failure.message);
  }
}

async function submitImport(event) {
  event.preventDefault();
  const error = $("import-error");
  let document_;
  try {
    document_ = JSON.parse($("import-doc").value);
  } catch {
    return showFormError(error, "That is not valid JSON.");
  }
  try {
    const result = await withBusy("Importing…", () => post("/api/import", { document: document_ }));
    $("import-modal").close();
    $("import-doc").value = "";
    toast("ok", result.message);
    await refresh(false);
  } catch (failure) {
    showFormError(error, failure.message);
  }
}

async function submitBranch(event) {
  event.preventDefault();
  const error = $("branch-error");
  const name = $("branch-name").value.trim();
  if (!name) return showFormError(error, "A branch name is required.");
  try {
    const result = await withBusy(`Creating ${name}…`, () => post("/api/worktree/branch", { name }));
    $("branch-modal").close();
    $("branch-name").value = "";
    toast("ok", result.message);
    await refresh(false);
  } catch (failure) {
    showFormError(error, failure.message);
  }
}

/* ---------- repositories ---------- */

// The desktop shell exposes a native folder picker; a plain browser tab does not.
const desktopBridge = () => (window.__TAURI__ && window.__TAURI__.core) || null;

async function browseForRepository() {
  const bridge = desktopBridge();
  if (!bridge) return;
  try {
    const chosen = await bridge.invoke("pick_repository");
    if (!chosen) return;
    $("repo-path").value = chosen;
    await openRepo(chosen);
  } catch (error) {
    showFormError($("repo-error"), `The folder picker failed: ${error}`);
  }
}

function renderRepoList() {
  const list = clear($("repo-list"));
  if (!state.repos.repos.length) {
    list.append(el("li", {}, [el("span", { class: "where", text: "No repository registered yet." })]));
  }
  for (const repo of state.repos.repos) {
    const isActive = repo.path === state.repos.active;
    list.append(el("li", {}, [
      el("button", {
        type: "button", class: "open", "aria-current": isActive ? "true" : "false",
        onclick: () => { if (!isActive) openRepo(repo.path); },
      }, [
        el("span", { text: repo.name }),
        el("span", { class: `where ${repo.exists ? "" : "missing"}`,
          text: repo.exists ? repo.path : `${repo.path} — missing` }),
      ]),
      !isActive ? el("button", {
        type: "button", class: "btn tiny ghost", "aria-label": `Remove ${repo.name} from the list`,
        title: "Remove from the list (nothing is deleted on disk)",
        onclick: () => forgetRepo(repo.path), text: "×",
      }) : null,
    ]));
  }
}

function openReposDialog() {
  $("repo-error").hidden = true;
  renderRepoList();
  $("repos-modal").showModal();
  if (desktopBridge() && state.repos.repos.length <= 1) $("btn-browse").focus();
  else $("repo-path").focus();
}

async function openRepo(path) {
  try {
    const result = await withBusy(`Opening ${path}…`, () => post("/api/repos/open", { path }));
    $("repos-modal").close();
    $("repo-path").value = "";
    toast("ok", result.message);
    state.selected = null;
    state.selectedFile = null;
    state.fileDiff = null;
    state.commitMessage = "";
    await refresh(false);
  } catch (failure) {
    showFormError($("repo-error"), failure.message);
    renderRepoList();
  }
}

async function forgetRepo(path) {
  await quiet(withBusy("Removing…", async () => {
    toast("ok", (await post("/api/repos/forget", { path })).message);
    state.repos = await api("/api/repos");
    renderRepoList();
  }));
}

/* ---------- resizable panes ---------- */

function setupResizer(handleId, variable, { min, max, invert = false }) {
  const handle = $(handleId);
  const layout = document.querySelector(".layout");
  const read = () => parseInt(getComputedStyle(layout).getPropertyValue(variable), 10) || min;
  const write = (value) => layout.style.setProperty(variable, `${Math.min(Math.max(value, min), max())}px`);

  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
    const move_ = (motion) => {
      const width = invert ? window.innerWidth - motion.clientX : motion.clientX;
      write(width);
    };
    const stop = () => {
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing");
      window.removeEventListener("pointermove", move_);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move_);
    window.addEventListener("pointerup", stop);
  });

  handle.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 40 : 12;
    if (event.key === "ArrowLeft") { event.preventDefault(); write(read() + (invert ? step : -step)); }
    if (event.key === "ArrowRight") { event.preventDefault(); write(read() + (invert ? -step : step)); }
  });
}

/* ---------- wiring ---------- */

function bind() {
  $("btn-propose").addEventListener("click", openPropose);
  $("propose-form").addEventListener("submit", submitPropose);
  $("propose-cancel").addEventListener("click", () => $("propose-modal").close());
  $("import-form").addEventListener("submit", submitImport);
  $("import-cancel").addEventListener("click", () => $("import-modal").close());
  $("branch-form").addEventListener("submit", submitBranch);
  $("branch-cancel").addEventListener("click", () => $("branch-modal").close());
  $("help-close").addEventListener("click", () => $("help-modal").close());
  $("btn-help").addEventListener("click", () => $("help-modal").showModal());
  $("btn-wip").addEventListener("click", () => select("wip"));
  $("repo-chip").addEventListener("click", () => openReposDialog());
  $("repos-close").addEventListener("click", () => $("repos-modal").close());
  if (desktopBridge()) {
    const browse = $("btn-browse");
    browse.hidden = false;
    browse.addEventListener("click", browseForRepository);
    $("repo-path-hint").textContent = "browse or paste a path";
    $("repo-path").required = false;
  }
  $("repo-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const path = $("repo-path").value.trim();
    if (!path) return showFormError($("repo-error"), "An absolute path is required.");
    return openRepo(path);
  });
  $("btn-stash").addEventListener("click", () => {
    const message = prompt("Stash message (optional)");
    if (message !== null) worktreeAction("stash", null, { message });
  });
  for (const [id, action] of [["btn-fetch", "fetch"], ["btn-pull", "pull"], ["btn-push", "push"]]) {
    $(id).addEventListener("click", () => worktreeAction(action, null));
  }
  $("btn-new-branch").addEventListener("click", () => {
    $("branch-error").hidden = true;
    $("branch-modal").showModal();
    $("branch-name").focus();
  });
  $("btn-import").addEventListener("click", () => {
    $("import-error").hidden = true;
    $("import-modal").showModal();
    $("import-doc").focus();
  });

  $("btn-index").addEventListener("click", () => quiet(withBusy("Re-indexing the repository…", async () => {
    toast("ok", (await post("/api/index")).message);
    await refresh();
  })));

  $("btn-export").addEventListener("click", () => quiet(withBusy("Exporting…", async () => {
    const response = await fetch("/api/export");
    if (!response.ok) throw new Error("Export failed.");
    const url = URL.createObjectURL(await response.blob());
    const link = el("a", { href: url, download: "gitia-export.json" });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("ok", "Export downloaded.");
  })));

  for (const [id, action, message] of [
    ["btn-sample-load", "load", "Loading sample records…"],
    ["btn-sample-clear", "clear", "Deleting sample records…"],
  ]) {
    $(id).addEventListener("click", () => quiet(withBusy(message, async () => {
      toast("ok", (await post(`/api/samples/${action}`)).message);
      await refresh(false);
    })));
  }

  $("search").addEventListener("input", (event) => {
    state.query = event.target.value.trim().toLowerCase();
    renderRows();
  });

  // One observer covers the window, the resizable panes and the scrollbar appearing.
  let paneWidth = 0;
  new ResizeObserver(([entry]) => {
    const width = Math.round(entry.contentRect.width);
    if (width === paneWidth || !state.data) return;
    paneWidth = width;
    renderRows();
  }).observe($("rows-wrap"));

  setupResizer("resize-left", "--sidebar-w", { min: 180, max: () => 420 });
  setupResizer("resize-right", "--detail-w", { min: 320, max: () => window.innerWidth - 520, invert: true });

  document.addEventListener("keydown", (event) => {
    const typing = ["INPUT", "TEXTAREA"].includes(event.target.tagName);
    if (event.key === "Escape" && typing) event.target.blur();
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;

    const actions = {
      "/": () => $("search").focus(),
      n: openPropose,
      N: openPropose,
      o: openReposDialog,
      O: openReposDialog,
      w: () => { if (state.worktree.files.length) select("wip"); },
      W: () => { if (state.worktree.files.length) select("wip"); },
      i: () => $("btn-index").click(),
      I: () => $("btn-index").click(),
      "?": () => $("help-modal").showModal(),
      ArrowDown: () => move(1),
      j: () => move(1),
      ArrowUp: () => move(-1),
      k: () => move(-1),
      Enter: () => { if (state.selected) $("detail").focus(); },
    };
    const action = actions[event.key];
    if (action) { event.preventDefault(); action(); }
  });
}

async function boot() {
  bind();
  try {
    await refresh(false);
    status("Ready.");
  } catch (error) {
    status("Ready.");
    clear($("detail")).append(el("div", { class: "empty-state" }, [
      el("h2", { text: "Could not reach gitia" }), el("p", { text: error.message }),
    ]));
    toast("bad", error.message);
  }
}

boot();
