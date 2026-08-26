"use strict";

function commitBox(repo, staged) {
  const detached = Boolean((repo.head || {}).detached);
  const message = el("textarea", {
    id: "commit-message", rows: "3", maxlength: "4000",
    placeholder: "Commit message — describe what this commit does",
    "aria-label": "Commit message",
    oninput: (event) => { state.commitMessage = event.target.value; },
    onkeydown: (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); commitStaged(); }
    },
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

  return el("div", { class: "commit-box" }, [
    message,
    el("div", { class: "row-actions" }, [
      el("button", {
        type: "button", class: "btn primary", disabled: !state.amend && staged === 0,
        onclick: () => commitStaged(),
        text: state.amend
          ? "Amend the last commit"
          : `Commit ${plural(staged, "file")}${detached ? "" : ` to ${repo.branch}`}`,
      }),
      el("label", { class: "amend-toggle", for: "commit-amend",
        title: repo.head_message ? "Replace the last commit instead of adding one" : "There is no commit to amend yet" },
        [amendBox, el("span", { text: "Amend" })]),
      el("span", { class: `staged-note${detached ? " warn" : ""}`,
        text: detached
          ? "No branch is checked out: this commit would be easy to lose. Create a branch first."
          : (state.amend ? "The last commit is replaced, staged files included."
            : (staged ? "" : "Stage a file to enable the commit.")) }),
    ]),
  ]);
}

function fileGroup({ label, files, staged, conflicted }) {
  const context = worktreeContext(staged);
  const group = el("div", { class: "file-group" }, [
    el("div", { class: "file-group-head" }, [
      el("span", { text: label }),
      el("span", { class: "count", text: String(files.length) }),
      files.length ? el("button", {
        type: "button", class: "btn tiny ghost",
        onclick: () => worktreeAction(staged ? "unstage" : "stage", files.map((file) => file.path)),
        text: conflicted ? "Mark all resolved" : staged ? "Unstage all" : "Stage all",
      }) : null,
    ]),
    conflicted
      ? el("p", { class: "tree-empty", style: "padding:2px 16px 8px",
          text: "Resolve these in your editor, then stage them to mark them settled." })
      : (files.length ? null : el("p", { class: "tree-empty", style: "padding:8px 16px",
          text: staged ? "Nothing staged yet." : "No unstaged edit." })),
  ]);

  const shaped = files.map((entry) => ({
    path: entry.path,
    status: entry.conflicted ? "U" : (staged ? entry.index_code : (entry.untracked ? "A" : entry.work_code)),
    original: entry.original,
    sensitive: entry.sensitive,
    untracked: entry.untracked,
    source: entry,
    ...(entry.conflicted ? {} : lineCounts(entry, staged)),
  }));
  group.append(renderFileList(context, shaped, `${label} files`, (entry) => ({
    extras: [
      el("button", {
        type: "button", class: "btn tiny ghost stage-btn",
        "aria-label": `${conflicted ? "Mark resolved" : staged ? "Unstage" : "Stage"} ${entry.path}`,
        onclick: (event) => { event.stopPropagation(); worktreeAction(staged ? "unstage" : "stage", [entry.path]); },
        text: conflicted ? "Resolved" : staged ? "Unstage" : "Stage",
      }),
      menuButton(entry.path, () => fileMenu(entry.source, staged)),
    ],
    menu: () => fileMenu(entry.source, staged),
  })));
  return group;
}

function renderWorktreeDetail() {
  const files = state.worktree.files;
  const repo = state.data.state.repo;
  const staged = files.filter((file) => file.staged && !file.conflicted);
  const unstaged = files.filter((file) => (file.unstaged || file.untracked) && !file.conflicted);
  const conflicted = files.filter((file) => file.conflicted);

  fill(clear($("detail")),
    el("div", { class: "detail-head" }, [
      el("h2", { text: files.length ? "Uncommitted changes" : "Working tree" }),
      el("div", { class: "detail-sub" }, [
        el("span", { text: files.length ? plural(files.length, "file") : "clean — nothing to commit" }),
        el("span", { text: `${staged.length} staged` }),
        el("span", { text: `${unstaged.length} unstaged` }),
        conflicted.length ? el("span", { class: "tag failed", text: "conflicts" }) : null,
      ]),
    ]),
    commitBox(repo, staged.length),
    conflicted.length
      ? fileGroup({ label: "Conflicted", files: conflicted, staged: false, conflicted: true })
      : null,
    fileGroup({ label: "Staged", files: staged, staged: true, conflicted: false }),
    fileGroup({ label: "Unstaged", files: unstaged, staged: false, conflicted: false }),
  );
}

async function openFileHistory(path) {
  const token = newestRender();
  const detail = clear($("detail"));
  detail.append(el("div", { class: "empty-state" }, [el("p", { text: "Loading history…" })]));
  let payload;
  try {
    payload = await call("file_history", { path });
  } catch (error) {
    toast("bad", error.message);
    return;
  }
  if (token !== state.render) return;
  clear(detail);
  detail.append(el("div", { class: "detail-head" }, [
    el("h2", { text: path }),
    el("div", { class: "detail-sub" }, [
      el("span", { text: plural(payload.commits.length, "commit") }),
      el("button", { type: "button", class: "btn tiny ghost", onclick: () => select("wip"), text: "Back to the working tree" }),
    ]),
  ]));

  const list = el("ol", { class: "history" });
  for (const commit of payload.commits) {
    list.append(el("li", {}, [
      el("button", {
        type: "button", class: "open", title: `Show ${path} as this commit left it`,
        onclick: () => openFileAtCommit(commit.sha, path),
      }, [
        el("span", { class: "row-title", text: commit.subject || "(no message)" }),
        el("span", { class: "sha", text: commit.short }),
        el("span", { class: "who", text: commit.author }),
        el("span", { class: "relative", text: relativeTime(commit.date) }),
      ]),
      el("button", {
        type: "button", class: "row-menu", title: "Show the whole commit",
        "aria-label": `Show the commit ${commit.short}`,
        onclick: () => openCommit(commit.sha),
      }, [icon("arrow-right")]),
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

// Two selections in flight would race, and the slower request would win the panel.
function newestRender() {
  state.render += 1;
  return state.render;
}

function filesSection(context, label, added, removed, empty) {
  return el("section", { class: "detail-section files-section" }, [
    el("h3", {}, [
      `${label} (${context.files.length})`,
      el("span", { class: "totals" }, [
        el("span", { class: "stat-add", text: `+${added}` }),
        el("span", { class: "stat-del", text: `−${removed}` }),
      ]),
    ]),
    context.files.length
      ? renderFileList(context, context.files, `${label} in this ${context.kind}`)
      : el("p", { class: "prose", text: empty }),
  ]);
}

function commitHead(commit, menu, context) {
  return Menu.attach(el("div", { class: "detail-head" }, [
    el("h2", { text: commit.subject || "(no message)" }),
    el("div", { class: "detail-sub" }, [
      el("button", {
        type: "button", class: "sha-copy", title: "Copy the full SHA",
        onclick: () => copy(commit.sha, "SHA"), text: commit.short,
      }),
      el("span", { text: commit.author }),
      el("span", { class: "relative", title: commit.date, text: relativeTime(commit.date) }),
      commit.merge ? el("span", { class: "tag applied", text: "merge" }) : null,
      ...commit.parents.map((parent, index) => el("button", {
        type: "button", class: "sha-copy", title: `Go to parent ${index + 1}`,
        onclick: () => openCommit(parent),
      }, [icon("corner-up-left", 11), parent.slice(0, 7)])),
      ...commit.refs.map((ref) => el("span", { class: "tag ref", text: ref })),
    ]),
    el("div", { class: "detail-actions" }, [
      el("button", {
        type: "button", class: "btn ghost",
        onclick: () => openFileView({ ...context, files: [] }, ""), text: "Whole patch",
      }),
    ]),
  ]), menu);
}

async function renderCommitDetail(sha) {
  const token = newestRender();
  const detail = clear($("detail"));
  detail.append(el("div", { class: "empty-state" }, [el("p", { text: "Loading the commit…" })]));
  const commit = await call("commit_detail", { sha });
  if (token !== state.render) return;

  const context = commitContext(commit);
  const body = commit.body.split("\n").slice(1).join("\n").trim();
  fill(clear(detail),
    commitHead(commit, () => commitMenu({ ...commit, kind: "commit" }), context),
    body
      ? el("section", { class: "detail-section" }, [el("p", { class: "prose message-body", text: body })])
      : null,
    filesSection(context, "Files", commit.added, commit.removed, "No file changed."),
  );
}
