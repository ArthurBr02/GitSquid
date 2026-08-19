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
  counts: {},
  amend: false,
  busy: false,
};

/* ---------- DOM helpers (no innerHTML: every value is set as text) ---------- */

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    // Through the CSSOM, never as a style attribute: the page's CSP forbids inline styles.
    else if (key === "style") for (const rule of String(value).split(";")) {
      const [property, setting] = rule.split(":");
      if (setting) node.style.setProperty(property.trim(), setting.trim());
    }
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

function copy(text, what) {
  navigator.clipboard.writeText(text).then(
    () => toast("ok", `${what} copied.`),
    () => toast("bad", "The clipboard refused the copy."),
  );
}

/* One dialog for every "name this" question: branch, tag, rename, stash message. */
function ask({ title, hint = "", label, placeholder = "", value = "", submit = "OK", optional = false, extra = null }) {
  const modal = $("ask-modal");
  const field = $("ask-value");
  const form = $("ask-form");
  $("ask-title").textContent = title;
  $("ask-hint").textContent = hint;
  $("ask-hint").hidden = !hint;
  $("ask-label").textContent = label;
  $("ask-submit").textContent = submit;
  $("ask-error").hidden = true;
  field.placeholder = placeholder;
  field.value = value;
  $("ask-extra-field").hidden = !extra;
  if (extra) {
    $("ask-extra-label").textContent = extra.label;
    $("ask-extra").placeholder = extra.placeholder || "";
    $("ask-extra").value = "";
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      form.removeEventListener("submit", onSubmit);
      modal.removeEventListener("close", onClose);
      resolve(result);
    };
    const onSubmit = (event) => {
      event.preventDefault();
      const primary = field.value.trim();
      if (!primary && !optional) {
        showFormError($("ask-error"), "This field is required.");
        return;
      }
      finish({ value: primary, extra: extra ? $("ask-extra").value.trim() : "" });
      modal.close();
    };
    const onClose = () => finish(null);
    form.addEventListener("submit", onSubmit);
    modal.addEventListener("close", onClose);
    modal.showModal();
    field.focus();
    field.select();
  });
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
  $("row-count").textContent = visible.length === state.rows.length
    ? `${visible.length} row${visible.length === 1 ? "" : "s"}`
    : `${visible.length} of ${state.rows.length}`;
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
      oncontextmenu: (event) => { event.preventDefault(); select(row.key); Menu.show(event, rowMenu(row)); },
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
    side.append(
      el("span", { class: "relative when", text: relativeTime(row.when) }),
      el("button", {
        type: "button", class: "row-menu", "aria-label": "Actions for this row",
        onclick: (event) => { event.stopPropagation(); select(row.key); Menu.show(event, rowMenu(row)); },
      }, ["⋯"]),
    );
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

/* ---------- context menus: one builder per kind of row ---------- */

const confirmed = (question, action, extra) => () => {
  if (confirm(question)) worktreeAction(action, null, extra);
};
const runs = (action, extra) => () => worktreeAction(action, null, extra);

async function askThen(question, action, build) {
  const answer = await ask(question);
  if (answer) worktreeAction(action, null, build(answer));
}

function commitMenu(row) {
  const branch = state.data.state.repo.branch;
  const sha = row.sha;
  return [
    { header: `Commit ${row.short}` },
    { label: "Check out this commit", hint: "detached",
      run: confirmed(`Check out ${row.short}? HEAD becomes detached — create a branch to keep work here.`, "checkout-commit", { sha }) },
    { label: "New branch here…",
      run: () => askThen({ title: "Branch from this commit", hint: `Branches at ${row.short} and switches to it.`, label: "Branch name", placeholder: "feature/ma-fonctionnalite", submit: "Create" }, "branch-from", (a) => ({ sha, name: a.value })) },
    { label: "Tag this commit…",
      run: () => askThen({ title: "Tag this commit", label: "Tag name", placeholder: "v1.2.0", submit: "Tag", extra: { label: "Message (optional — an annotated tag)", placeholder: "What this release contains" } }, "tag-create", (a) => ({ sha, name: a.value, message: a.extra })) },
    "-",
    { label: `Cherry-pick onto ${branch}`, run: runs("cherry-pick", { sha }) },
    { label: "Revert this commit", hint: "new commit", run: runs("revert-commit", { sha }) },
    { label: `Rebase ${branch} onto here`,
      run: confirmed(`Replay ${branch} on top of ${row.short}? This rewrites the commits of ${branch}.`, "rebase", { target: sha }) },
    { header: `Reset ${branch} to here` },
    { label: "Soft — keep the index and the working tree", run: runs("reset", { sha, mode: "soft" }) },
    { label: "Mixed — keep the working tree", run: runs("reset", { sha, mode: "mixed" }) },
    { label: "Hard — discard everything after it", danger: true,
      run: confirmed(`Hard reset ${branch} to ${row.short}? Every uncommitted edit and every later commit is lost.`, "reset", { sha, mode: "hard" }) },
    "-",
    { label: "Copy SHA", run: () => copy(sha, "SHA") },
    { label: "Copy message", run: () => copy(row.subject || "", "Message") },
  ];
}

function changeMenu(row) {
  const applicable = ["proposed", "failed"].includes(row.status);
  const testable = ["applied", "verified", "failed"].includes(row.status);
  return [
    { header: `Change #${row.id}` },
    { label: "Apply to the working tree", disabled: row.is_sample || !applicable, run: () => changeAction(row.id, "apply") },
    { label: "Run tests", disabled: !testable, run: () => changeAction(row.id, "test") },
    { label: "Revert", danger: true, disabled: row.is_sample || !testable,
      run: () => { if (confirm(`Reverse change #${row.id} in the working tree?`)) changeAction(row.id, "revert"); } },
    "-",
    { label: "Copy the task", run: () => copy(row.task, "Task") },
  ];
}

function wipMenu() {
  const files = state.worktree.files;
  const paths = files.map((file) => file.path);
  const unstaged = files.filter((file) => file.unstaged || file.untracked).map((file) => file.path);
  return [
    { header: "Uncommitted changes" },
    { label: "Stage everything", disabled: !unstaged.length, run: () => worktreeAction("stage", unstaged) },
    { label: "Unstage everything", disabled: !state.worktree.staged, run: () => worktreeAction("unstage", files.filter((file) => file.staged).map((file) => file.path)) },
    { label: "Discard everything", danger: true, disabled: !paths.length,
      run: () => { if (confirm(`Discard every edit in ${paths.length} file(s)? This cannot be undone.`)) worktreeAction("discard", paths); } },
    "-",
    { label: "Stash…", run: () => stashWorkingTree() },
  ];
}

function branchMenu(branch, isCurrent) {
  const current = state.data.state.repo.branch;
  const hasRemote = (state.data.state.repo.remotes || []).length > 0;
  return [
    { header: branch.name },
    { label: "Check out", disabled: isCurrent, run: () => switchBranch(branch.name) },
    { label: `Merge into ${current}`, disabled: isCurrent,
      run: confirmed(`Merge ${branch.name} into ${current}?`, "merge", { branch: branch.name }) },
    { label: `Merge into ${current} (squash)`, disabled: isCurrent,
      run: confirmed(`Squash ${branch.name} into the index of ${current}?`, "merge", { branch: branch.name, squash: true }) },
    { label: `Rebase ${current} onto it`, disabled: isCurrent,
      run: confirmed(`Replay ${current} on top of ${branch.name}? This rewrites the commits of ${current}.`, "rebase", { target: branch.name }) },
    "-",
    { label: "New branch from here…",
      run: () => askThen({ title: `Branch from ${branch.name}`, label: "Branch name", submit: "Create" }, "branch-from", (a) => ({ sha: branch.sha, name: a.value })) },
    { label: "Rename…",
      run: () => askThen({ title: `Rename ${branch.name}`, label: "New name", value: branch.name, submit: "Rename" }, "rename-branch", (a) => ({ branch: branch.name, name: a.value })) },
    { label: "Push", disabled: !hasRemote, run: runs("push-branch", { branch: branch.name }) },
    { label: "Delete", danger: true, disabled: isCurrent,
      run: confirmed(`Delete the branch ${branch.name}?`, "delete-branch", { branch: branch.name }) },
    { label: "Delete even if unmerged", danger: true, disabled: isCurrent,
      run: confirmed(`Force-delete ${branch.name}? Commits only on that branch are lost.`, "delete-branch", { branch: branch.name, force: true }) },
    "-",
    { label: "Copy name", run: () => copy(branch.name, "Branch name") },
  ];
}

function remoteBranchMenu(entry) {
  return [
    { header: entry.name },
    { label: entry.tracked ? `Check out ${entry.local}` : `Check out as ${entry.local}`,
      run: runs("checkout-remote", { branch: entry.name }) },
    { label: "Fetch", run: runs("fetch") },
    { label: "Delete on the remote", danger: true,
      run: confirmed(`Delete ${entry.name} on the remote? Other clones keep their copy until they prune.`, "delete-remote-branch", { branch: entry.name }) },
    "-",
    { label: "Copy name", run: () => copy(entry.name, "Branch name") },
  ];
}

function tagMenu(tag) {
  const hasRemote = (state.data.state.repo.remotes || []).length > 0;
  return [
    { header: tag.name },
    { label: "Check out", hint: "detached",
      run: confirmed(`Check out ${tag.name}? HEAD becomes detached.`, "checkout-commit", { sha: tag.sha }) },
    { label: "Push to the remote", disabled: !hasRemote, run: runs("tag-push", { name: tag.name }) },
    { label: "Delete", danger: true,
      run: confirmed(`Delete the tag ${tag.name}? Only the local one — the remote keeps its copy.`, "tag-delete", { name: tag.name }) },
    "-",
    { label: "Copy name", run: () => copy(tag.name, "Tag name") },
  ];
}

function stashMenu(stash) {
  return [
    { header: stash.ref },
    { label: "Apply", hint: "keeps it", run: runs("stash-apply", { ref: stash.ref }) },
    { label: "Pop", hint: "removes it", run: runs("stash-pop", { ref: stash.ref }) },
    { label: "Branch from it…",
      run: () => askThen({ title: `Branch from ${stash.ref}`, hint: "Creates the branch, restores the stash on it, and drops the stash.", label: "Branch name", submit: "Create" }, "stash-branch", (a) => ({ ref: stash.ref, name: a.value })) },
    { label: "Drop", danger: true,
      run: confirmed(`Drop ${stash.ref}? Its content is lost.`, "stash-drop", { ref: stash.ref }) },
  ];
}

function fileMenu(entry, staged) {
  return [
    { header: entry.path },
    { label: staged ? "Unstage" : "Stage",
      run: () => worktreeAction(staged ? "unstage" : "stage", [entry.path]) },
    { label: "Open the diff", run: () => openFile(entry.path, staged) },
    { label: "File history", run: () => openFileHistory(entry.path) },
    "-",
    { label: "Ignore it", hint: ".gitignore", disabled: staged,
      run: confirmed(`Add ${entry.path} to .gitignore?`, "ignore", { paths: [entry.path] }) },
    { label: "Discard the edits", danger: true, disabled: staged,
      run: () => { if (confirm(`Discard your edits to ${entry.path}? This cannot be undone.`)) worktreeAction("discard", [entry.path]); } },
    "-",
    { label: "Copy path", run: () => copy(entry.path, "Path") },
  ];
}

function rowMenu(row) {
  if (row.kind === "wip") return wipMenu();
  return row.kind === "change" ? changeMenu(row) : commitMenu(row);
}

/* ---------- sidebar: collapsible sections of one row component ---------- */

const FILTER_LABELS = {
  all: "Everything", commits: "Commits", proposed: "Proposed", applied: "Applied",
  verified: "Verified", failed: "Failed", reverted: "Reverted",
};

function remember(key, value) {
  try { localStorage.setItem(`gitsquid.${key}`, value); } catch { /* private mode */ }
}

function recall(key, fallback) {
  try { return localStorage.getItem(`gitsquid.${key}`) ?? fallback; } catch { return fallback; }
}

/* A row is a row: branches, tags, stashes and the working tree all use this one. */
function treeRow({ id, label, meta, icon, sub, current, className = "", onclick, menu }) {
  const node = el("div", {
    id,
    class: `tree-row ${className}`,
    role: "button",
    tabindex: "0",
    "aria-current": current ? "true" : undefined,
    onclick,
    onkeydown: (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onclick(); }
    },
  }, [
    icon ? el("span", { class: "tree-icon", "aria-hidden": "true", text: icon }) : null,
    el("span", { class: "tree-label", title: label }, [
      label,
      sub ? el("span", { class: "sub" }, sub) : null,
    ]),
    meta ? el("span", { class: "tree-meta", text: meta }) : null,
    menu ? el("button", {
      type: "button", class: "row-menu", "aria-label": `Actions for ${label}`,
      onclick: (event) => { event.stopPropagation(); Menu.show(event, menu()); },
    }, ["⋯"]) : null,
  ]);
  return menu ? Menu.attach(node, menu) : node;
}

function section(key, { title, count, action, rows, empty }) {
  const open = recall(`section.${key}`, "open") === "open";
  const head = el("summary", { class: "section-head" }, [
    el("span", { class: "caret", "aria-hidden": "true", text: "▸" }),
    el("span", { class: "section-title", text: title }),
    count !== undefined ? el("span", { class: "section-count", text: String(count) }) : null,
    action ? el("button", {
      type: "button", class: "icon-btn section-action", title: action.title,
      "aria-label": action.title,
      onclick: (event) => { event.preventDefault(); event.stopPropagation(); action.run(); },
    }, [action.label]) : null,
  ]);
  const body = el("div", { class: "section-body" }, rows.length ? rows : [
    el("p", { class: "tree-empty", text: empty }),
  ]);
  const node = el("details", { class: "section", open: open || undefined, "data-key": key }, [head, body]);
  node.addEventListener("toggle", () => remember(`section.${key}`, node.open ? "open" : "closed"));
  return node;
}

function renderSidebar() {
  const { repo } = state.data.state;
  const sidebar = clear($("sidebar"));
  const files = state.worktree.files;
  const hasRemote = (repo.remotes || []).length > 0;

  sidebar.append(section("worktree", {
    title: "Working tree",
    rows: [treeRow({
      id: "wip-row",
      label: files.length ? "Uncommitted changes" : "Clean",
      icon: "◆",
      className: files.length ? "" : "quiet",
      current: state.selected === "wip",
      sub: files.length
        ? [el("span", { class: state.worktree.staged ? "on" : "", text: `${state.worktree.staged} staged` }),
           ` · ${state.worktree.unstaged} unstaged`]
        : ["nothing to commit"],
      onclick: () => { if (files.length) select("wip"); },
      menu: files.length ? wipMenu : null,
    })],
    empty: "",
  }));

  sidebar.append(section("branches", {
    title: "Branches",
    count: repo.branches.length,
    action: { label: "+", title: "Create a branch", run: createBranch },
    empty: "No branch yet.",
    rows: repo.branches.map((branch) => {
      const isCurrent = branch.name === repo.branch;
      return treeRow({
        label: branch.name,
        meta: branch.sha,
        icon: isCurrent ? "●" : "○",
        className: isCurrent ? "current" : "",
        onclick: () => { if (!isCurrent) switchBranch(branch.name); },
        menu: () => branchMenu(branch, isCurrent),
      });
    }),
  }));

  const remote = repo.remote_branches || [];
  sidebar.append(section("remotes", {
    title: "Remote branches",
    count: remote.length,
    action: hasRemote ? { label: "⟳", title: "Fetch from the remote", run: () => worktreeAction("fetch", null) } : null,
    empty: hasRemote ? "Nothing fetched yet." : "No remote configured.",
    rows: remote.map((entry) => treeRow({
      label: entry.name,
      meta: entry.tracked ? "tracked" : entry.sha,
      icon: "⇅",
      onclick: () => worktreeAction("checkout-remote", null, { branch: entry.name }),
      menu: () => remoteBranchMenu(entry),
    })),
  }));

  const tags = repo.tags || [];
  sidebar.append(section("tags", {
    title: "Tags",
    count: tags.length,
    action: { label: "+", title: "Tag the current commit", run: createTag },
    empty: "No tag.",
    rows: tags.map((tag) => treeRow({
      label: tag.name,
      meta: tag.sha,
      icon: "⚑",
      onclick: () => openCommit(tag.sha),
      menu: () => tagMenu(tag),
    })),
  }));

  const stashes = repo.stashes || [];
  sidebar.append(section("stashes", {
    title: "Stashes",
    count: stashes.length,
    action: { label: "⤓", title: "Stash the working tree", run: stashWorkingTree },
    empty: "No stash.",
    rows: stashes.map((stash) => treeRow({
      label: stash.subject,
      meta: stash.age,
      icon: "≡",
      onclick: () => worktreeAction("stash-apply", null, { ref: stash.ref }),
      menu: () => stashMenu(stash),
    })),
  }));

  renderChrome();
  renderOperation(repo);
}

/* A merge, rebase, cherry-pick or revert git stopped in the middle of: the one state where
   the next step is neither committing nor staging. */
function renderOperation(repo) {
  const bar = clear($("op-bar"));
  const operation = repo.operation;
  bar.hidden = !operation;
  if (!operation) return;
  const blocked = operation.conflicts.length;
  bar.append(
    el("span", { class: "op-kind", text: `${operation.kind} in progress` }),
    el("span", {
      text: blocked
        ? `${blocked} file(s) still conflict. Resolve them, stage them, then continue.`
        : "Nothing conflicts any more — continue when you are ready.",
    }),
    operation.resumable ? el("button", {
      type: "button", class: "btn tiny", disabled: blocked > 0,
      onclick: () => worktreeAction("continue", null), text: "Continue",
    }) : null,
    el("button", {
      type: "button", class: "btn tiny danger",
      onclick: () => {
        if (confirm(`Abort the ${operation.kind}? The repository goes back where it started.`)) {
          worktreeAction("abort", null);
        }
      },
      text: "Abort",
    }),
  );
}

/* The top bar, the filter chip and the status bar: everything that frames the graph. */
function renderChrome() {
  const { repo, config, index, counts, total_changes: total } = state.data.state;
  $("repo-name").textContent = repo.name;
  $("repo-branch").textContent = repo.branch;
  $("db-path").textContent = config.database;
  $("db-path").title = config.database;
  $("index-stat").textContent = index.files
    ? `${index.files.toLocaleString()} files · ${index.chunks.toLocaleString()} chunks indexed`
    : "not indexed yet";

  const model = clear($("model-state"));
  model.append(
    el("span", { class: `dot ${config.model_available ? "on" : "off"}`, "aria-hidden": "true" }),
    el("span", { text: config.model_available ? config.model : "no API key" }),
  );
  model.title = config.model_available
    ? `Proposals go to ${config.model} (effort ${config.effort}).`
    : `No ANTHROPIC_API_KEY in ${repo.path}/.env — GitSquid still validates, applies, tests and `
      + "records a diff you paste yourself.";

  $("filter-label").textContent = FILTER_LABELS[state.filter] || "Everything";
  $("btn-filter").classList.toggle("on", state.filter !== "all");

  const tracking = repo.tracking || { ahead: 0, behind: 0 };
  const hasRemote = (repo.remotes || []).length > 0;
  for (const [id, count] of [["badge-ahead", tracking.ahead], ["badge-behind", tracking.behind]]) {
    const badge = $(id);
    badge.hidden = !count;
    badge.textContent = String(count || "");
  }
  for (const id of ["btn-fetch", "btn-pull", "btn-push"]) {
    $(id).disabled = !hasRemote;
  }

  state.counts = { ...counts, all: total + state.data.graph.commits.length,
    commits: state.data.graph.commits.length };
}

function filterMenu() {
  return Object.entries(FILTER_LABELS).map(([key, label]) => ({
    label,
    hint: String((state.counts || {})[key] ?? ""),
    className: key === state.filter ? "on" : "",
    run: () => { state.filter = key; renderRows(); renderChrome(); },
  }));
}

/* Everything that is neither a git verb nor the change loop: rare, and out of the way. */
function moreMenu() {
  const samples = state.data.state.samples;
  return [
    { label: "Repositories…", hint: "O", run: openReposDialog },
    "-",
    { label: "Re-index this repository", hint: "I", run: reindex },
    { label: "Export the history…", run: exportHistory },
    { label: "Import a history…", run: () => openDialog("import-modal", "import-doc", "import-error") },
    "-",
    { label: samples ? "Delete the sample records" : "Load sample records",
      run: () => samplesAction(samples ? "clear" : "load") },
    { label: "Keyboard shortcuts", hint: "?", run: () => $("help-modal").showModal() },
  ];
}

/* ---------- diff rendering with line numbers ---------- */

function parseDiff(text) {
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

function diffLine(text, kind, number) {
  return el("div", { class: kind }, [
    el("span", { class: "ln", "aria-hidden": "true", text: number }),
    el("span", { class: "tx", text: text || " " }),
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

/* `actions` is set only for a working-tree file, where a single hunk can be staged. */
function renderDiff(diff, actions = null) {
  const box = el("pre", { class: "diff" });
  for (const file of parseDiff(diff)) {
    for (const line of file.header) box.append(diffLine(line, "meta", ""));
    for (const hunk of file.hunks) {
      if (actions) box.append(hunkBar(file, hunk, actions));
      let oldLine = 0;
      let newLine = 0;
      for (const line of hunk.lines) {
        if (line.startsWith("@@")) {
          const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
          if (match) { oldLine = Number(match[1]); newLine = Number(match[2]); }
          box.append(diffLine(line, "hunk", ""));
        } else if (line.startsWith("+")) {
          box.append(diffLine(line, "add", String(newLine++)));
        } else if (line.startsWith("-")) {
          box.append(diffLine(line, "del", String(oldLine++)));
        } else if (line.startsWith("\\")) {
          box.append(diffLine(line, "meta", ""));
        } else {
          box.append(diffLine(line, "", String(newLine++)));
          oldLine++;
        }
      }
    }
  }
  return box;
}

async function applyHunk(patch, target) {
  const opened = state.fileDiff;
  await quiet(withBusy("Applying the hunk…", async () => {
    toast("ok", (await post(`/api/worktree/${target}-hunk`, { patch })).message);
    await refresh();
    if (opened) await openFile(opened.path, opened.staged);
  }));
}

/* ---------- working tree detail ---------- */

function fileRow(entry, staged) {
  const code = staged ? entry.index_code : (entry.untracked ? "A" : entry.work_code);
  const [dir, name] = splitPath(entry.path);
  const key = `${staged ? "s" : "u"}:${entry.path}`;

  return Menu.attach(el("div", {
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
    el("button", {
      type: "button", class: "btn tiny ghost stage-btn",
      "aria-label": `${staged ? "Unstage" : "Stage"} ${entry.path}`,
      onclick: (event) => { event.stopPropagation(); worktreeAction(staged ? "unstage" : "stage", [entry.path]); },
      text: staged ? "Unstage" : "Stage",
    }),
    el("button", {
      type: "button", class: "row-menu", "aria-label": `Actions for ${entry.path}`,
      onclick: (event) => { event.stopPropagation(); Menu.show(event, fileMenu(entry, staged)); },
    }, ["⋯"]),
  ]), () => fileMenu(entry, staged));
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

  const repo = state.data.state.repo;
  const message = el("textarea", {
    id: "commit-message", rows: "3", maxlength: "4000",
    placeholder: "Commit message — describe what this commit does",
    "aria-label": "Commit message",
    oninput: (event) => { state.commitMessage = event.target.value; },
  });
  message.value = state.commitMessage;

  const amendBox = el("input", {
    type: "checkbox", id: "commit-amend",
    checked: state.amend || undefined,
    disabled: !repo.head_message,
    onchange: (event) => {
      state.amend = event.target.checked;
      if (state.amend && !state.commitMessage.trim()) state.commitMessage = repo.head_message;
      renderWorktreeDetail();
    },
  });

  detail.append(el("div", { class: "commit-box" }, [
    message,
    el("div", { class: "row-actions" }, [
      el("button", {
        type: "button", class: "btn primary", disabled: !state.amend && staged.length === 0,
        onclick: () => commitStaged(),
        text: state.amend ? "Amend the last commit" : `Commit ${staged.length} file(s)`,
      }),
      el("label", { class: "amend-toggle", for: "commit-amend",
        title: repo.head_message ? "Replace the last commit instead of adding one" : "There is no commit to amend yet" },
        [amendBox, el("span", { text: "Amend" })]),
      el("span", { class: "staged-note",
        text: state.amend ? "The last commit is replaced, staged files included."
          : (staged.length ? "" : "Stage a file to enable the commit.") }),
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
    const entry = files.find((file) => file.path === state.fileDiff.path);
    const hunks = entry && !entry.untracked ? { staged: state.fileDiff.staged } : null;
    detail.append(el("section", { class: "detail-section" }, [
      state.fileDiff.diff.trim()
        ? renderDiff(state.fileDiff.diff, hunks)
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

async function openFileHistory(path) {
  const detail = clear($("detail"));
  detail.append(el("div", { class: "empty-state" }, [el("p", { text: "Loading history…" })]));
  let payload;
  try {
    payload = await api(`/api/filehistory?path=${encodeURIComponent(path)}`);
  } catch (error) {
    toast("bad", error.message);
    return;
  }
  clear(detail);
  detail.append(el("div", { class: "detail-head" }, [
    el("h2", { text: path }),
    el("div", { class: "detail-sub" }, [
      el("span", { text: `${payload.commits.length} commit(s)` }),
      el("button", { type: "button", class: "btn tiny ghost", onclick: () => select("wip"), text: "Back to the working tree" }),
    ]),
  ]));

  const list = el("ol", { class: "history" });
  for (const commit of payload.commits) {
    list.append(el("li", {}, [
      el("button", { type: "button", class: "open", onclick: () => openCommit(commit.sha) }, [
        el("span", { class: "row-title", text: commit.subject || "(no message)" }),
        el("span", { class: "sha", text: commit.short }),
        el("span", { class: "who", text: commit.author }),
        el("span", { class: "relative", text: relativeTime(commit.date) }),
      ]),
    ]));
  }
  detail.append(el("section", { class: "detail-section" }, [
    payload.commits.length ? list : el("p", { class: "prose", text: "No commit touched this file yet." }),
  ]));
}

function openCommit(sha) {
  const row = state.rows.find((item) => item.key === `g${sha}`);
  if (row) { select(row.key); return; }
  quiet(renderCommitDetail(sha));
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
  await quiet(withBusy(state.amend ? "Amending…" : "Committing…", async () => {
    const result = await post("/api/worktree/commit", { message, amend: state.amend });
    toast("ok", result.message);
    state.commitMessage = "";
    state.amend = false;
    state.fileDiff = null;
    state.selectedFile = null;
    await refresh(false);
  }));
}

async function stashWorkingTree() {
  const answer = await ask({
    title: "Stash the working tree",
    hint: "Staged, unstaged and untracked files are put aside and the tree goes back to HEAD.",
    label: "Message (optional)",
    placeholder: "what you were in the middle of",
    submit: "Stash",
    optional: true,
  });
  if (answer) worktreeAction("stash", null, { message: answer.value });
}

async function createBranch() {
  const answer = await ask({
    title: "Create a branch",
    hint: `Branches from ${state.data.state.repo.branch} and switches to it.`,
    label: "Branch name",
    placeholder: "feature/ma-fonctionnalite",
    submit: "Create",
  });
  if (answer) worktreeAction("branch", null, { name: answer.value });
}

async function createTag() {
  const answer = await ask({
    title: "Tag the current commit",
    label: "Tag name",
    placeholder: "v1.2.0",
    submit: "Tag",
    extra: { label: "Message (optional — an annotated tag)", placeholder: "What this release contains" },
  });
  if (answer) worktreeAction("tag-create", null, { name: answer.value, message: answer.extra });
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

const CHANGE_LABELS = { apply: "Applying", test: "Running the test command", revert: "Reverting" };

async function changeAction(id, action) {
  await quiet(withBusy(`${CHANGE_LABELS[action]} on change #${id}…`, async () => {
    const result = await post(`/api/changes/${id}/${action}`);
    toast(action === "test" && !result.passed ? "bad" : "ok", result.message);
    await refresh();
  }));
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

  const applicable = ["proposed", "failed"].includes(change.status);
  const testable = ["applied", "verified", "failed"].includes(change.status);
  head.append(el("div", { class: "detail-actions" }, [
    actionButton("Apply", "primary", () => changeAction(id, "apply"),
      change.is_sample || !applicable || !change.applies_cleanly),
    actionButton("Run tests", "ghost", () => changeAction(id, "test"), !testable),
    actionButton("Revert", "danger", () => {
      if (confirm(`Reverse change #${id} in the working tree?`)) changeAction(id, "revert");
    }, change.is_sample || !testable),
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
  const detail = clear($("detail"));
  const commit = await api(`/api/commits/${sha}`);
  const menu = () => commitMenu({ ...commit, kind: "commit" });

  const head = el("div", { class: "detail-head" }, [
    el("h2", { text: commit.subject || "(no message)" }),
    el("div", { class: "detail-sub" }, [
      el("span", { text: commit.short }),
      el("span", { text: commit.author }),
      el("span", { class: "relative", text: relativeTime(commit.date) }),
      ...commit.refs.map((ref) => el("span", { class: "tag ref", text: ref })),
    ]),
    el("div", { class: "detail-actions" }, [
      el("button", {
        type: "button", class: "btn ghost", "aria-haspopup": "menu",
        onclick: (event) => Menu.show(event, menu()), text: "Actions ▾",
      }),
    ]),
  ]);
  detail.append(Menu.attach(head, menu));

  if (commit.body && commit.body !== commit.subject) {
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
  const worktreeRow = $("wip-row");
  if (worktreeRow) worktreeRow.setAttribute("aria-current", key === "wip" ? "true" : "false");
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

function openSelectedMenu() {
  const row = state.rows.find((item) => item.key === state.selected);
  const node = $(`row-${state.selected}`);
  if (row && node) Menu.show(node, rowMenu(row));
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
    : "No API key configured, so a diff is required. GitSquid validates, applies, tests and records it exactly the same way.";
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
  const write = (value) => {
    const clamped = `${Math.min(Math.max(value, min), max())}px`;
    layout.style.setProperty(variable, clamped);
    remember(`pane${variable}`, clamped);
  };
  const stored = recall(`pane${variable}`, "");
  if (stored) layout.style.setProperty(variable, stored);

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

/* ---------- the rare actions, named once and reached from the ⋯ menu ---------- */

function openDialog(modalId, focusId, errorId) {
  if (errorId) $(errorId).hidden = true;
  $(modalId).showModal();
  if (focusId) $(focusId).focus();
}

function reindex() {
  return quiet(withBusy("Re-indexing the repository…", async () => {
    toast("ok", (await post("/api/index")).message);
    await refresh();
  }));
}

function exportHistory() {
  return quiet(withBusy("Exporting…", async () => {
    const response = await fetch("/api/export");
    if (!response.ok) throw new Error("Export failed.");
    const url = URL.createObjectURL(await response.blob());
    const link = el("a", { href: url, download: "gitsquid-export.json" });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("ok", "Export downloaded.");
  }));
}

function samplesAction(action) {
  return quiet(withBusy(action === "load" ? "Loading sample records…" : "Deleting sample records…", async () => {
    toast("ok", (await post(`/api/samples/${action}`)).message);
    await refresh(false);
  }));
}

/* ---------- wiring ---------- */

function bind() {
  $("btn-propose").addEventListener("click", openPropose);
  $("propose-form").addEventListener("submit", submitPropose);
  $("propose-cancel").addEventListener("click", () => $("propose-modal").close());
  $("import-form").addEventListener("submit", submitImport);
  $("import-cancel").addEventListener("click", () => $("import-modal").close());
  $("ask-cancel").addEventListener("click", () => $("ask-modal").close());
  $("help-close").addEventListener("click", () => $("help-modal").close());
  $("repo-chip").addEventListener("click", openReposDialog);
  $("repos-close").addEventListener("click", () => $("repos-modal").close());
  $("btn-more").addEventListener("click", (event) => Menu.show(event, moreMenu()));
  $("btn-filter").addEventListener("click", (event) => Menu.show(event, filterMenu()));

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

  for (const [id, action] of [["btn-fetch", "fetch"], ["btn-pull", "pull"], ["btn-push", "push"]]) {
    $(id).addEventListener("click", () => worktreeAction(action, null));
  }
  Menu.attach($("btn-push"), () => [
    { label: "Push", run: () => worktreeAction("push", null) },
    { label: "Force push", hint: "with lease", danger: true,
      run: () => {
        if (confirm("Force-push this branch? It overwrites the remote branch, unless someone pushed to it since your last fetch.")) {
          worktreeAction("push", null, { force: true });
        }
      } },
  ]);

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
      o: openReposDialog,
      w: () => { if (state.worktree.files.length) select("wip"); },
      i: reindex,
      b: createBranch,
      t: createTag,
      s: stashWorkingTree,
      "?": () => $("help-modal").showModal(),
      ArrowDown: () => move(1),
      j: () => move(1),
      ArrowUp: () => move(-1),
      k: () => move(-1),
      Enter: () => { if (state.selected) $("detail").focus(); },
      ContextMenu: openSelectedMenu,
      F10: () => { if (event.shiftKey) openSelectedMenu(); },
    };
    const action = actions[event.key] || actions[event.key.toLowerCase?.()];
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
      el("h2", { text: "Could not reach GitSquid" }), el("p", { text: error.message }),
    ]));
    toast("bad", error.message);
  }
}

boot();
