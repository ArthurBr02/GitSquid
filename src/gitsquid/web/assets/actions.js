"use strict";

async function worktreeAction(action, paths, extra = {}) {
  // Staging one file should not close the file you were reading.
  const open = state.view && state.view.context.kind === "worktree" ? { ...state.view } : null;
  await quiet(withBusy(`Running ${action}…`, async () => {
    const result = await post(`/api/worktree/${action}`, { paths, ...extra });
    toast("ok", result.message);
    await refresh();
    if (!open) return;
    // The file may have crossed the index: follow it rather than closing on it.
    const holding = [worktreeContext(open.context.staged), worktreeContext(!open.context.staged)]
      .find((context) => context.files.some((file) => file.path === open.path));
    if (!holding) closeViewer();
    else if (open.mode === "blame") await openBlame(holding, open.path);
    else await openFileView(holding, open.path);
  }));
}

const RESET_MODES = [
  ["soft", "Keep everything, staged", "The files do not change; what came after stays in the index."],
  ["mixed", "Keep everything, unstaged", "The files do not change; the index is cleared."],
  ["hard", "Throw the work away", "The files go back to that commit. Anything uncommitted is lost."],
];

function localBranchesAt(row) {
  const names = new Set((row.refs || []).map((ref) => ref.replace(/^HEAD -> /, "")));
  return state.data.state.repo.branches.filter((branch) => names.has(branch.name));
}

async function moveHead(row) {
  const repo = state.data.state.repo;
  const head = repo.head || {};
  if (row.sha === head.commit) return toast("ok", "You are already on this commit.");

  const dirty = state.worktree.files.length;
  const here = localBranchesAt(row).filter((branch) => branch.name !== repo.branch);
  const on = head.detached ? "HEAD" : repo.branch;
  const chosen = await choose({
    title: `Move HEAD to ${row.short}`,
    hint: `${row.subject || "(no message)"}${dirty ? ` — ${plural(dirty, "uncommitted file")} in the way` : ""}`,
    options: [
      ...here.map((branch) => ({
        value: { action: "checkout", payload: { branch: branch.name } },
        label: `Check out ${branch.name}`,
        detail: "That branch already points here, so nothing is rewritten.",
      })),
      {
        value: { action: "checkout-commit", payload: { sha: row.sha } },
        label: "Check out this commit",
        detail: "HEAD detaches here. Your branch keeps pointing where it does.",
      },
      ...RESET_MODES.map(([mode, label, detail]) => ({
        value: { action: "reset", payload: { sha: row.sha, mode } },
        label: `Reset ${on} here — ${label}`,
        detail,
        danger: mode === "hard",
      })),
    ],
  });
  if (chosen) await worktreeAction(chosen.action, null, chosen.payload);
  return undefined;
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
    closeViewer();
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

function remotesMenu(repo) {
  return [
    { header: "Remotes" },
    { label: "Fetch and prune", run: () => worktreeAction("fetch", null) },
    ...repo.remotes.map((remote) => ({
      label: `Remove ${remote.name}`, hint: remote.url.slice(0, 28), danger: true,
      run: confirmed(`Remove the remote ${remote.name}? Nothing local is touched.`,
        "remote-remove", { name: remote.name }),
    })),
    "-",
    { label: "Add a remote…", run: addRemote },
  ];
}

async function addRemote() {
  const answer = await ask({
    title: "Add a remote",
    hint: "Nothing is fetched or pushed until you ask for it.",
    label: "Name",
    value: "origin",
    submit: "Add",
    extra: { label: "URL", placeholder: "git@example.com:group/project.git" },
  });
  if (answer) worktreeAction("remote-add", null, { name: answer.value, url: answer.extra });
}

async function cloneRepository() {
  const answer = await ask({
    title: "Clone a repository",
    hint: "It lands in the folder you name, and opens here when it is done.",
    label: "URL",
    placeholder: "git@example.com:group/project.git",
    submit: "Clone",
    extra: { label: "Into which folder", placeholder: "/Users/you/Projects" },
  });
  if (!answer) return;
  if (!answer.extra) return toast("bad", "Say where to put it — an absolute folder path.");
  await quiet(withBusy(`Cloning ${answer.value}…`, async () => {
    const result = await post("/api/repos/clone", { url: answer.value, parent: answer.extra });
    toast("ok", result.message);
    state.selected = null;
    state.view = null;
    await refresh(false);
  }));
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

function openDialog(modalId, focusId, errorId) {
  if (errorId) $(errorId).hidden = true;
  $(modalId).showModal();
  if (focusId) $(focusId).focus();
}


async function applyPicked(target) {
  const view = state.view;
  const file = parseDiff(view.diff)[0];
  const patch = linePatch(file, view.picks);
  if (!patch) return;
  const said = {
    stage: `Staged ${plural(view.picks.size, "line")}.`,
    unstage: `Unstaged ${plural(view.picks.size, "line")}.`,
    discard: `Discarded ${plural(view.picks.size, "line")}.`,
  }[target];
  state.view = { ...view, picks: new Set() };
  await applyHunk(patch, target, said);
}

async function applyHunk(patch, target, said = "") {
  const opened = state.view;
  await quiet(withBusy(said ? "Applying…" : "Applying the hunk…", async () => {
    const result = await post(`/api/worktree/${target}-hunk`, { patch });
    toast("ok", said || result.message);
    await refresh();
    if (opened) await openFileView(worktreeContext(opened.context.staged), opened.path);
  }));
}
