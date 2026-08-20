"use strict";

const ROW_H = 42;

const state = {
  data: null,
  worktree: { files: [], staged: 0, unstaged: 0, conflicted: 0 },
  repos: { active: "", repos: [] },
  rows: [],
  query: "",
  selected: null,
  commitMessage: "",
  view: null,
  painting: null,
  render: 0,
  limit: 80,
  refs: recall("graph.refs", "all"),
  diffView: {
    wrap: recall("diff.wrap", "off") === "on",
    space: recall("diff.space", "off") === "on",
    context: Number(recall("diff.context", "3")) || 3,
  },
  search: null,
  amend: false,
  busy: false,
};

// Sorting commits by date would put a parent above its child and run the lanes off the list.
function buildRows() {
  const rows = state.data.graph.commits.map((commit) => ({
    ...commit, kind: "commit", key: `g${commit.sha}`, when: commit.date,
  }));
  if (state.worktree.files.length) {
    rows.unshift({ kind: "wip", key: "wip", when: new Date().toISOString() });
  }
  return rows;
}

function matches(row) {
  if (row.kind === "wip") return !state.query;
  if (!state.query) return true;
  return `${row.subject} ${row.author} ${row.short}`.toLowerCase().includes(state.query);
}

function rowContent(row) {
  const main = el("div", { class: "row-main" });
  const side = el("div", { class: "row-side" });

  if (row.kind === "wip") {
    fill(main,
      el("span", { class: "row-title", text: "Uncommitted changes" }),
      el("span", { class: "tag applied", text: "WIP" }));
    side.append(el("span", { text: plural(state.worktree.files.length, "file") }));
  } else {
    main.append(el("span", {
      class: "row-title", title: isHead(row) ? "You are here" : undefined,
      text: row.subject || "(no message)",
    }));
    // Beyond two refs the subject loses more room than the badges are worth.
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
    el("span", { class: "relative when", title: new Date(row.when).toLocaleString(), text: relativeTime(row.when) }),
    menuButton("this row", () => rowMenu(row), { before: () => select(row.key) }),
  );
  return [main, side];
}

const isHead = (row) => row.kind === "commit" && row.sha === (state.data.state.repo.head || {}).commit;

function rowElement(row) {
  return el("li", {
    class: `row${row.kind === "wip" ? " wip" : ""}${isHead(row) ? " head" : ""}`,
    role: "option",
    id: `row-${row.key}`,
    "aria-selected": state.selected === row.key ? "true" : "false",
    onclick: () => select(row.key),
    ondblclick: () => { if (row.kind === "commit") moveHead(row); },
    oncontextmenu: (event) => { event.preventDefault(); select(row.key); Menu.show(event, rowMenu(row)); },
  }, rowContent(row));
}

function depthBar() {
  const more = clear($("rows-more"));
  const { commits, total_commits: total } = state.data.graph;
  const deeper = total > commits.length && !state.query;
  more.hidden = !deeper;
  if (!deeper) return;
  more.append(
    el("span", { text: `${commits.length} of ${plural(total, "commit")} loaded` }),
    el("button", {
      type: "button", class: "btn tiny ghost",
      onclick: () => { state.limit = Math.min(state.limit + 200, 5000); quiet(refresh()); },
      text: `Load ${Math.min(200, total - commits.length)} more`,
    }),
  );
}

function emptyNote(visible) {
  const note = $("graph-empty");
  note.hidden = visible.length > 0;
  if (visible.length) return;
  note.textContent = state.search
    ? `No commit in this repository mentions “${state.search.query}”.`
    : (state.rows.length
      ? `Nothing matches “${state.query}”.`
      : "No commit yet. Stage something and make the first one.");
}

function renderRows() {
  const wrap = $("rows-wrap");
  const list = clear($("rows"));
  const visible = state.rows.filter(matches);
  // A filtered list has holes in it, so the lanes would join rows that are not adjacent.
  const flat = Boolean(state.query);
  const laid = Graph.layout(visible, { flat });
  const { gap, width } = Graph.measure(laid.columns, Math.min(Math.round((wrap.clientWidth || 700) / 3), 280));

  wrap.style.setProperty("--lane-width", `${width}px`);
  list.setAttribute("aria-activedescendant", state.selected ? `row-${state.selected}` : "");
  $("row-count").textContent = state.search
    ? `${plural(visible.length, "result")} for “${state.search.query}”`
    : (visible.length === state.rows.length
      ? plural(visible.length, "row")
      : `${visible.length} of ${state.rows.length}`);

  emptyNote(visible);
  for (const row of visible) list.append(rowElement(row));
  depthBar();

  state.painting = { laid, gap, width };
  paintGraph();
}

function paintGraph() {
  if (!state.painting) return;
  const { laid, gap, width } = state.painting;
  Graph.paint($("graph-canvas"), laid, {
    rowHeight: ROW_H,
    gap,
    width,
    pendingColor: () => "#e8ae33",
    isHead,
    isSelected: (row) => row.key === state.selected,
  });
}

// Rebuilding a thousand rows to move the selection costs 175ms; marking one costs nothing.
let repaint = 0;

function markSelection(previous) {
  const list = $("rows");
  const leaving = previous ? $(`row-${previous}`) : null;
  if (leaving) leaving.setAttribute("aria-selected", "false");
  const node = state.selected ? $(`row-${state.selected}`) : null;
  if (node) {
    node.setAttribute("aria-selected", "true");
    node.scrollIntoView({ block: "nearest" });
  }
  list.setAttribute("aria-activedescendant", state.selected ? `row-${state.selected}` : "");
  clearTimeout(repaint);
  repaint = setTimeout(paintGraph, 90);
}

function select(key) {
  const previous = state.selected;
  state.selected = key;
  if (state.view) closeViewer();
  markSelection(previous);
  const worktreeRow = $("wip-row");
  if (worktreeRow) worktreeRow.setAttribute("aria-current", key === "wip" ? "true" : "false");
  const node = $(`row-${key}`);
  if (node) node.scrollIntoView({ block: "nearest" });

  if (key === "wip") { renderWorktreeDetail(); return; }
  const row = state.rows.find((item) => item.key === key);
  if (!row) return;
  const render = renderCommitDetail(row.sha);
  render.catch((error) => {
    clear($("detail")).append(el("div", { class: "empty-state" }, [
      el("h2", { text: "Could not load this item" }), el("p", { text: error.message }),
    ]));
  });
}

async function goToHead() {
  const head = (state.data.state.repo.head || {}).commit;
  if (!head) return;
  const found = () => state.rows.find((row) => row.sha === head);
  while (!found() && state.limit < 5000) {
    state.limit = Math.min(state.limit + 400, 5000);
    await quiet(refresh());
  }
  const row = found();
  if (!row) return toast("bad", "HEAD is further back than the graph will load.");
  select(row.key);
  $(`row-${row.key}`)?.scrollIntoView({ block: "center" });
  return undefined;
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

function searchRows() {
  return state.search.commits.map((commit) => ({
    ...commit, kind: "commit", key: `g${commit.sha}`, when: commit.date,
  }));
}

async function deepSearch(query) {
  if (query.trim().length < 2) return;
  await quiet(withBusy(`Searching the history for “${query}”…`, async () => {
    const payload = await api(`/api/search?q=${encodeURIComponent(query)}`);
    state.search = { query, commits: payload.commits };
    state.query = "";
    state.rows = searchRows();
    state.selected = state.rows.length ? state.rows[0].key : null;
    $("btn-leave-search").hidden = false;
    renderRows();
    renderChrome();
    if (state.selected) select(state.selected);
  }));
}

function leaveSearch() {
  if (!state.search) return;
  state.search = null;
  state.query = "";
  $("search").value = "";
  $("btn-leave-search").hidden = true;
  quiet(refresh(false));
}

async function refresh(keepSelection = true) {
  const [stateData, graph, worktreeData, repos] = await Promise.all([
    api("/api/state"), api(`/api/graph?limit=${state.limit}&refs=${state.refs}`),
    api("/api/worktree"), api("/api/repos"),
  ]);
  state.data = { state: stateData, graph };
  state.worktree = worktreeData;
  state.repos = repos;
  state.rows = state.search ? searchRows() : buildRows();

  const stillThere = state.rows.some((row) => row.key === state.selected);
  if (!keepSelection || !stillThere) {
    state.selected = state.rows.length ? state.rows[0].key : null;
  }
  renderSidebar();
  renderRows();
  if (state.selected) select(state.selected);
}

function showFormError(node, message) {
  node.textContent = message;
  node.hidden = false;
  node.scrollIntoView({ block: "nearest" });
}


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

function bindDialogs() {
  $("ask-cancel").addEventListener("click", () => $("ask-modal").close());
  $("choose-cancel").addEventListener("click", () => $("choose-modal").close());
  $("help-close").addEventListener("click", () => $("help-modal").close());
  $("repos-close").addEventListener("click", () => $("repos-modal").close());
  $("repo-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const path = $("repo-path").value.trim();
    if (!path) return showFormError($("repo-error"), "An absolute path is required.");
    return openRepo(path);
  });
  if (!desktopBridge()) return;
  const browse = $("btn-browse");
  browse.hidden = false;
  browse.addEventListener("click", browseForRepository);
  $("repo-path-hint").textContent = "browse or paste a path";
  $("repo-path").required = false;
}

function bindToolbar() {
  $("repo-chip").addEventListener("click", (event) => Menu.show(event, repoMenu()));
  $("btn-more").addEventListener("click", (event) => Menu.show(event, moreMenu()));
  $("btn-filter").addEventListener("click", (event) => Menu.show(event, filterMenu()));
  $("btn-head").addEventListener("click", goToHead);
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
}

function bindSearch() {
  $("search").addEventListener("input", (event) => {
    if (state.search) return;  // typing again refines nothing until you leave the results
    state.query = event.target.value.trim().toLowerCase();
    renderRows();
  });
  $("search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); deepSearch(event.target.value); }
    if (event.key === "Escape" && state.search) { event.preventDefault(); leaveSearch(); }
  });
  $("btn-leave-search").addEventListener("click", leaveSearch);
}

function bindKeyboard() {
  document.addEventListener("keydown", (event) => {
    const typing = ["INPUT", "TEXTAREA"].includes(event.target.tagName);
    if (event.key === "Escape" && typing) event.target.blur();
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;

    if (state.view) {
      const inViewer = {
        Escape: closeViewer,
        ArrowDown: () => viewerStep(1), j: () => viewerStep(1),
        ArrowUp: () => viewerStep(-1), k: () => viewerStep(-1),
      }[event.key];
      if (inViewer) { event.preventDefault(); inViewer(); return; }
    }

    const actions = {
      "/": () => $("search").focus(),
      o: openReposDialog,
      w: () => select("wip"),
      r: () => quiet(refresh()),
      h: goToHead,
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

function bind() {
  bindDialogs();
  bindToolbar();
  bindSearch();
  bindKeyboard();
  window.addEventListener("focus", refreshOnFocus);
  document.addEventListener("visibilitychange", refreshOnFocus);

  let paneWidth = 0;
  new ResizeObserver(([entry]) => {
    const width = Math.round(entry.contentRect.width);
    if (width === paneWidth || !state.data) return;
    paneWidth = width;
    renderRows();
  }).observe($("rows-wrap"));

  setupResizer("resize-left", "--sidebar-w", { min: 180, max: () => 420 });
  setupResizer("resize-right", "--detail-w", { min: 320, max: () => window.innerWidth - 520, invert: true });
}

async function boot() {
  applyTheme(recall("theme", "system"));
  fillIconSlots();
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
