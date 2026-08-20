"use strict";

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
  const isHead = (row.refs || []).some((ref) => ref.startsWith("HEAD"));
  const parent = (row.parents || [])[0];
  // Branch refs on this commit that are not the current branch and not HEAD — merge/checkout targets.
  const otherBranches = (row.refs || []).filter((ref) =>
    ref !== branch && !ref.startsWith("HEAD") && !ref.startsWith("tag:"));
  return [
    { header: `Commit ${row.short}` },
    ...(isHead && parent && (row.parents || []).length === 1 ? [
      { label: "Amend this commit…", hint: "message and files",
        run: () => { select("wip"); state.amend = true; state.commitMessage = ""; renderWorktreeDetail(); } },
      { label: "Undo this commit", hint: "keeps the work",
        run: confirmed(`Undo ${row.short}? Its changes come back as staged work, nothing is lost.`,
          "reset", { sha: parent, mode: "soft" }) },
      "-",
    ] : []),
    ...otherBranches.map((ref) => (
      { label: `Merge ${ref} into ${branch}`,
        run: confirmed(`Merge ${ref} into ${branch}?`, "merge", { branch: ref }) }
    )),
    ...(otherBranches.length ? ["-"] : []),
    { label: "New branch here…",
      run: () => askThen({ title: "Branch from this commit", hint: `Branches at ${row.short} and switches to it.`, label: "Branch name", placeholder: "feature/ma-fonctionnalite", submit: "Create" }, "branch-from", (a) => ({ sha, name: a.value })) },
    { label: "Tag this commit…",
      run: () => askThen({ title: "Tag this commit", label: "Tag name", placeholder: "v1.2.0", submit: "Tag", extra: { label: "Message (optional — an annotated tag)", placeholder: "What this release contains" } }, "tag-create", (a) => ({ sha, name: a.value, message: a.extra })) },
    "-",
    { label: `Cherry-pick onto ${branch}`, run: runs("cherry-pick", { sha }) },
    { label: "Revert this commit", hint: "new commit", run: runs("revert-commit", { sha }) },
    { label: `Rebase ${branch} onto here`,
      run: confirmed(`Replay ${branch} on top of ${row.short}? This rewrites the commits of ${branch}.`, "rebase", { target: sha }) },
    { label: "Move HEAD here…", hint: "double click", run: () => moveHead(row) },
    "-",
    { label: "Copy SHA", run: () => copy(sha, "SHA") },
    { label: "Copy message", run: () => copy(row.subject || "", "Message") },
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
      run: () => { if (confirm(`Discard every edit in ${plural(paths.length, "file")}? This cannot be undone.`)) worktreeAction("discard", paths); } },
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
    { header: `${stash.ref} — ${stash.subject}` },
    { label: "Show what it holds", disabled: !stash.sha, run: () => openCommit(stash.sha) },
    { label: "Apply", hint: "keeps it", run: runs("stash-apply", { ref: stash.ref }) },
    { label: "Pop", hint: "removes it", run: runs("stash-pop", { ref: stash.ref }) },
    { label: "Branch from it…",
      run: () => askThen({ title: `Branch from ${stash.ref}`, hint: "Creates the branch, restores the stash on it, and drops the stash.", label: "Branch name", submit: "Create" }, "stash-branch", (a) => ({ ref: stash.ref, name: a.value })) },
    { label: "Drop", danger: true,
      run: confirmed(`Drop ${stash.ref}? Its content is lost.`, "stash-drop", { ref: stash.ref }) },
  ];
}

function fileMenu(entry, staged) {
  if (entry.conflicted) {
    return [
      { header: `${entry.path} — in conflict` },
      { label: "Keep my side", hint: "ours",
        run: confirmed(`Keep your side of ${entry.path} whole and stage it?`, "resolve", { paths: [entry.path], side: "ours" }) },
      { label: "Keep the incoming side", hint: "theirs",
        run: confirmed(`Keep the incoming side of ${entry.path} whole and stage it?`, "resolve", { paths: [entry.path], side: "theirs" }) },
      { label: "Mark resolved", hint: "stage", run: () => worktreeAction("stage", [entry.path]) },
      "-",
      { label: "Open the diff", run: () => openFileView(worktreeContext(false), entry.path) },
      { label: "Copy path", run: () => copy(entry.path, "Path") },
    ];
  }
  return [
    { header: entry.path },
    { label: staged ? "Unstage" : "Stage",
      run: () => worktreeAction(staged ? "unstage" : "stage", [entry.path]) },
    { label: "Open the diff", run: () => openFileView(worktreeContext(staged), entry.path) },
    { label: "File history", run: () => openFileHistory(entry.path) },
    { label: "Blame", hint: "who wrote what", disabled: Boolean(entry.untracked),
      run: () => openBlame(worktreeContext(staged), entry.path) },
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
  return commitMenu(row);
}
