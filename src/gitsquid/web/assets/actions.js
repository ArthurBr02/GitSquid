"use strict";

/* Everything that asks the server to change something, and the questions asked before
   it does. */

async function worktreeAction(action, paths, extra = {}) {
  await quiet(withBusy(`Running ${action}…`, async () => {
    const result = await post(`/api/worktree/${action}`, { paths, ...extra });
    toast("ok", result.message);
    closeViewer();
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
