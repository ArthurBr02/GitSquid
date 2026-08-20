"use strict";

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

function repoMenu() {
  const known = state.repos.repos.filter((repo) => repo.exists);
  return [
    { header: "Repositories" },
    ...known.slice(0, 8).map((repo) => ({
      label: repo.name,
      hint: repo.path === state.repos.active ? "open" : "",
      className: repo.path === state.repos.active ? "on" : "",
      run: () => { if (repo.path !== state.repos.active) openRepo(repo.path); },
    })),
    "-",
    { label: "Open another…", hint: "O", run: openReposDialog },
    { label: "Clone a repository…", run: cloneRepository },
  ];
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
    state.view = null;
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
