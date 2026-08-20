"use strict";

function treeRow({ id, label, meta, metaTitle, icon: iconName, sub, current, className = "", onclick, menu }) {
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
    iconName ? el("span", { class: "tree-icon" }, [icon(iconName, 12)]) : null,
    el("span", { class: "tree-label", title: label }, [
      label,
      sub ? el("span", { class: "sub" }, sub) : null,
    ]),
    meta ? el("span", { class: `tree-meta ${metaTitle && /[↑↓]/.test(meta) ? "drift" : ""}`, title: metaTitle, text: meta }) : null,
    menu ? menuButton(label, menu) : null,
  ]);
  return menu ? Menu.attach(node, menu) : node;
}

const CLOSED_BY_DEFAULT = new Set(["remotes", "tags"]);

function section(key, { title, count, action, rows, empty }) {
  const open = recall(`section.${key}`, CLOSED_BY_DEFAULT.has(key) ? "closed" : "open") === "open";
  const head = el("summary", { class: "section-head" }, [
    el("span", { class: "caret" }, [icon("chevron-right", 12)]),
    el("span", { class: "section-title", text: title }),
    count !== undefined ? el("span", { class: "section-count", text: String(count) }) : null,
    action ? el("button", {
      type: "button", class: "icon-btn section-action", title: action.title,
      "aria-label": action.title,
      onclick: (event) => { event.preventDefault(); event.stopPropagation(); action.run(event); },
    }, [icon(action.label)]) : null,
  ]);
  const body = el("div", { class: "section-body" }, rows.length ? rows : [
    el("p", { class: "tree-empty", text: empty }),
  ]);
  const node = el("details", { class: "section", open: open || undefined, "data-key": key }, [head, body]);
  node.addEventListener("toggle", () => remember(`section.${key}`, node.open ? "open" : "closed"));
  return node;
}

function worktreeSection(files) {
  return section("worktree", {
    title: "Working tree",
    empty: "",
    rows: [treeRow({
      id: "wip-row",
      label: files.length ? "Uncommitted changes" : "Clean",
      icon: "edit",
      current: state.selected === "wip",
      sub: files.length
        ? [el("span", { class: state.worktree.staged ? "on" : "", text: `${state.worktree.staged} staged` }),
           ` · ${state.worktree.unstaged} unstaged`]
        : ["nothing to commit"],
      onclick: () => select("wip"),
      menu: files.length ? wipMenu : null,
    })],
  });
}

function branchesSection(repo) {
  return section("branches", {
    title: "Branches",
    count: repo.branches.length,
    action: { label: "plus", title: "Create a branch", run: createBranch },
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
        icon: isCurrent ? "commit" : "circle",
        className: isCurrent ? "current" : "",
        onclick: () => { if (!isCurrent) switchBranch(branch.name); },
        menu: () => branchMenu(branch, isCurrent),
      });
    }),
  });
}

function remotesSection(repo) {
  const remote = repo.remote_branches || [];
  const hasRemote = (repo.remotes || []).length > 0;
  return section("remotes", {
    title: "Remote branches",
    count: remote.length,
    action: {
      label: hasRemote ? "refresh" : "plus",
      title: hasRemote ? "Fetch, or manage the remotes" : "Add a remote",
      run: hasRemote ? (event) => Menu.show(event || $("sidebar"), remotesMenu(repo)) : addRemote,
    },
    empty: hasRemote ? "Nothing fetched yet." : "No remote yet — add one to push.",
    rows: remote.map((entry) => treeRow({
      label: entry.name,
      meta: entry.tracked ? "tracked" : entry.sha,
      icon: "remote",
      onclick: () => worktreeAction("checkout-remote", null, { branch: entry.name }),
      menu: () => remoteBranchMenu(entry),
    })),
  });
}

function tagsSection(repo) {
  const tags = repo.tags || [];
  return section("tags", {
    title: "Tags",
    count: tags.length,
    action: { label: "plus", title: "Tag the current commit", run: createTag },
    empty: "No tag.",
    rows: tags.map((tag) => treeRow({
      label: tag.name,
      meta: tag.sha,
      icon: "tag",
      onclick: () => openCommit(tag.sha),
      menu: () => tagMenu(tag),
    })),
  });
}

function stashesSection(repo) {
  const stashes = repo.stashes || [];
  return section("stashes", {
    title: "Stashes",
    count: stashes.length,
    action: { label: "stash", title: "Stash the working tree", run: stashWorkingTree },
    empty: "No stash.",
    rows: stashes.map((stash) => treeRow({
      label: stash.subject,
      meta: stash.age,
      icon: "layers",
      onclick: () => { if (stash.sha) openCommit(stash.sha); },
      menu: () => stashMenu(stash),
    })),
  });
}

function renderSidebar() {
  const { repo } = state.data.state;
  fill(clear($("sidebar")),
    worktreeSection(state.worktree.files),
    branchesSection(repo),
    remotesSection(repo),
    tagsSection(repo),
    stashesSection(repo),
  );
  renderChrome();
  renderOperation(repo);
}

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

function renderRepoChip(repo, config) {
  const head = repo.head || { detached: false, sha: "" };
  document.title = `${repo.name} — GitSquid`;
  $("repo-name").textContent = repo.name;
  $("repo-branch").textContent = head.detached ? `detached at ${head.sha}` : repo.branch;
  $("repo-branch").classList.toggle("detached", head.detached);
  $("db-path").textContent = config.database;
  $("db-path").title = config.database;
}

function renderRemoteButtons(repo) {
  const tracking = repo.tracking || { ahead: 0, behind: 0 };
  for (const [id, count] of [["badge-ahead", tracking.ahead], ["badge-behind", tracking.behind]]) {
    const badge = $(id);
    badge.hidden = !count;
    badge.textContent = String(count || "");
  }
  for (const id of ["btn-fetch", "btn-pull", "btn-push"]) {
    $(id).disabled = (repo.remotes || []).length === 0;
  }
}

function renderChrome() {
  const { repo, config } = state.data.state;
  renderRepoChip(repo, config);
  renderRemoteButtons(repo);

  const head = repo.head || { branch: "", sha: "", detached: false };
  $("head-label").textContent = head.detached ? head.sha : (head.branch || "HEAD");
  $("btn-head").title = `Go to where you are: ${head.branch || "detached"} at ${head.sha} (H)`;
  $("filter-label").textContent = state.refs === "head" ? "This branch" : "Every branch";
  $("btn-filter").classList.toggle("on", state.refs === "head");
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
  const showRefs = (refs) => () => {
    state.refs = refs;
    remember("graph.refs", refs);
    quiet(refresh());
  };
  return [
    { header: "Show" },
    { label: "Every branch", className: state.refs === "all" ? "on" : "", run: showRefs("all") },
    { label: "This branch only", className: state.refs === "head" ? "on" : "", run: showRefs("head") },
  ];
}

function moreMenu() {
  const { config } = state.data.state;
  return [
    { header: `GitSquid ${config.version} · git ${config.git_version}`, plain: true },
    { label: "Repositories…", hint: "O", run: openReposDialog },
    { label: "Refresh", hint: "R", run: () => quiet(refresh()) },
    "-",
    { label: "Appearance…", run: () => Menu.show($("btn-more"), themeMenu()) },
    { label: "Keyboard shortcuts", hint: "?", run: () => $("help-modal").showModal() },
  ];
}
