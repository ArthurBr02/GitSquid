"use strict";

/* The left column and the chrome around the graph: sections, rows, the top bar. It
   renders repository state; it never changes any. */

/* ---------- sidebar: collapsible sections of one row component ---------- */

const FILTER_LABELS = {
  all: "Everything", commits: "Commits", proposed: "Proposed", applied: "Applied",
  verified: "Verified", failed: "Failed", reverted: "Reverted",
};

/* A row is a row: branches, tags, stashes and the working tree all use this one. */
function treeRow({ id, label, meta, metaTitle, icon, sub, current, className = "", onclick, menu }) {
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
    meta ? el("span", { class: `tree-meta ${metaTitle && /[↑↓]/.test(meta) ? "drift" : ""}`, title: metaTitle, text: meta }) : null,
    menu ? el("button", {
      type: "button", class: "row-menu", "aria-label": `Actions for ${label}`,
      onclick: (event) => { event.stopPropagation(); Menu.show(event, menu()); },
    }, ["⋯"]) : null,
  ]);
  return menu ? Menu.attach(node, menu) : node;
}

const CLOSED_BY_DEFAULT = new Set(["remotes", "tags"]);

function section(key, { title, count, action, rows, empty }) {
  const open = recall(`section.${key}`, CLOSED_BY_DEFAULT.has(key) ? "closed" : "open") === "open";
  const head = el("summary", { class: "section-head" }, [
    el("span", { class: "caret", "aria-hidden": "true", text: "▸" }),
    el("span", { class: "section-title", text: title }),
    count !== undefined ? el("span", { class: "section-count", text: String(count) }) : null,
    action ? el("button", {
      type: "button", class: "icon-btn section-action", title: action.title,
      "aria-label": action.title,
      onclick: (event) => { event.preventDefault(); event.stopPropagation(); action.run(event); },
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
      onclick: () => select("wip"),
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
      const drift = [branch.ahead ? `↑${branch.ahead}` : "", branch.behind ? `↓${branch.behind}` : ""]
        .filter(Boolean).join(" ");
      return treeRow({
        label: branch.name,
        meta: drift || branch.sha,
        metaTitle: drift
          ? `${branch.ahead} ahead of, ${branch.behind} behind ${branch.upstream}`
          : (branch.upstream ? `up to date with ${branch.upstream}` : "no upstream"),
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
    action: {
      label: hasRemote ? "⟳" : "+",
      title: hasRemote ? "Fetch, or manage the remotes" : "Add a remote",
      run: hasRemote
        ? (event) => Menu.show(event || $("sidebar"), remotesMenu(repo))
        : addRemote,
    },
    empty: hasRemote ? "Nothing fetched yet." : "No remote yet — add one to push.",
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
  fill(bar,
    el("span", { class: "op-kind", text: `${operation.kind} in progress` }),
    el("span", {
      text: blocked
        ? `${plural(blocked, "file")} still ${blocked === 1 ? "conflicts" : "conflict"}. Resolve in your editor, stage, then continue.`
        : "Nothing conflicts any more — continue when you are ready.",
    }),
    operation.resumable ? el("button", {
      type: "button", class: "btn tiny", disabled: blocked > 0,
      onclick: () => worktreeAction("continue", null), text: "Continue",
    }) : null,
    ["rebase", "cherry-pick", "revert"].includes(operation.kind) ? el("button", {
      type: "button", class: "btn tiny ghost",
      title: "Leave this commit out and carry on",
      onclick: () => {
        if (confirm("Skip this commit? The replay carries on with the next one.")) {
          worktreeAction("skip", null);
        }
      },
      text: "Skip",
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
  document.title = `${repo.name} — GitSquid`;
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
    el("button", {
      type: "button", class: "model-chip",
      onclick: (event) => Menu.show(event, config.model_available ? [
        { header: "Model" },
        { label: config.model, hint: `effort ${config.effort}` },
        { label: "Where it comes from", hint: ".env" },
      ] : [
        { header: "Degraded mode" },
        { label: "No ANTHROPIC_API_KEY in this repository" },
        { label: "Everything but a model proposal works" },
        { label: "Copy the line to add to .env",
          run: () => copy("ANTHROPIC_API_KEY=sk-ant-…", "The .env line") },
      ]),
      text: config.model_available ? config.model : "no API key",
    }),
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

  state.counts = { ...counts, all: state.rows.length, commits: state.data.graph.commits.length };
}

let lastRefresh = 0;

function refreshOnFocus() {
  if (state.busy || document.hidden || Date.now() - lastRefresh < 1500) return;
  lastRefresh = Date.now();
  quiet(refresh());
}

const THEMES = { system: "Follow the system", light: "Light", dark: "Dark" };

function applyTheme(name) {
  const root = document.documentElement;
  if (name === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", name);
  remember("theme", name);
}

function themeMenu() {
  const current = recall("theme", "system");
  return [
    { header: "Appearance" },
    ...Object.entries(THEMES).map(([key, label]) => ({
      label, className: key === current ? "on" : "", run: () => applyTheme(key),
    })),
  ];
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
    { label: "Refresh", hint: "R", run: () => quiet(refresh()) },
    "-",
    { label: "Re-index this repository", hint: "I", run: reindex },
    { label: "Export the history…", run: exportHistory },
    { label: "Import a history…", run: () => openDialog("import-modal", "import-doc", "import-error") },
    "-",
    { label: samples ? "Delete the sample records" : "Load sample records",
      run: () => samplesAction(samples ? "clear" : "load") },
    { label: "Appearance…", run: (event) => Menu.show($("btn-more"), themeMenu()) },
    { label: "Keyboard shortcuts", hint: "?", run: () => $("help-modal").showModal() },
  ];
}
