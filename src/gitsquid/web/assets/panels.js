"use strict";

/* The right column: the working tree, a recorded change, a commit, a file history. */

/* ---------- working tree detail ---------- */

function renderWorktreeDetail() {
  const detail = clear($("detail"));
  const files = state.worktree.files;
  const staged = files.filter((file) => file.staged);
  const unstaged = files.filter((file) => file.unstaged || file.untracked);
  const repo = state.data.state.repo;

  detail.append(el("div", { class: "detail-head" }, [
    el("h2", { text: files.length ? "Uncommitted changes" : "Working tree" }),
    el("div", { class: "detail-sub" }, [
      el("span", { text: files.length ? plural(files.length, "file") : "clean — nothing to commit" }),
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

  detail.append(el("div", { class: "commit-box" }, [
    message,
    el("div", { class: "row-actions" }, [
      el("button", {
        type: "button", class: "btn primary", disabled: !state.amend && staged.length === 0,
        onclick: () => commitStaged(),
        text: state.amend ? "Amend the last commit" : `Commit ${plural(staged.length, "file")}`,
      }),
      el("label", { class: "amend-toggle", for: "commit-amend",
        title: repo.head_message ? "Replace the last commit instead of adding one" : "There is no commit to amend yet" },
        [amendBox, el("span", { text: "Amend" })]),
      el("span", { class: "staged-note",
        text: state.amend ? "The last commit is replaced, staged files included."
          : (staged.length ? "" : "Stage a file to enable the commit.") }),
    ]),
  ]));

  const conflicted = files.filter((file) => file.conflicted);
  const groups = [
    ...(conflicted.length ? [["Conflicted", conflicted, false, true]] : []),
    ["Staged", staged.filter((file) => !file.conflicted), true, false],
    ["Unstaged", unstaged.filter((file) => !file.conflicted), false, false],
  ];
  for (const [label, list, isStaged, isConflict] of groups) {
    const context = worktreeContext(isStaged);
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
    if (isConflict) {
      group.append(el("p", { class: "tree-empty", style: "padding:2px 16px 8px",
        text: "Resolve these in your editor, then stage them to mark them settled." }));
    } else if (!list.length) {
      group.append(el("p", { class: "tree-empty", style: "padding:8px 16px",
        text: isStaged ? "Nothing staged yet." : "No unstaged edit." }));
    }
    const shaped = list.map((entry) => ({
      path: entry.path,
      status: entry.conflicted ? "U" : (isStaged ? entry.index_code : (entry.untracked ? "A" : entry.work_code)),
      original: entry.original,
      sensitive: entry.sensitive,
      untracked: entry.untracked,
      source: entry,
      ...(entry.conflicted ? {} : lineCounts(entry, isStaged)),
    }));
    const listBox = renderFileList(context, shaped, `${label} files`, (entry) => ({
      extras: [
        el("button", {
          type: "button", class: "btn tiny ghost stage-btn",
          "aria-label": `${isConflict ? "Mark resolved" : isStaged ? "Unstage" : "Stage"} ${entry.path}`,
          onclick: (event) => { event.stopPropagation(); worktreeAction(isStaged ? "unstage" : "stage", [entry.path]); },
          text: isConflict ? "Resolved" : isStaged ? "Unstage" : "Stage",
        }),
        el("button", {
          type: "button", class: "row-menu", "aria-label": `Actions for ${entry.path}`,
          onclick: (event) => { event.stopPropagation(); Menu.show(event, fileMenu(entry.source, isStaged)); },
        }, ["⋯"]),
      ],
      menu: () => fileMenu(entry.source, isStaged),
    }));
    group.append(listBox);
    detail.append(group);
  }
}

async function openFileHistory(path) {
  const token = newestRender();
  const detail = clear($("detail"));
  detail.append(el("div", { class: "empty-state" }, [el("p", { text: "Loading history…" })]));
  let payload;
  try {
    payload = await api(`/api/filehistory?path=${encodeURIComponent(path)}`);
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

/* Two selections in flight would race, and the slower request would win the panel. */
function newestRender() {
  state.render += 1;
  return state.render;
}

async function renderChangeDetail(id) {
  const token = newestRender();
  const detail = clear($("detail"));
  detail.append(el("div", { class: "empty-state" }, [el("p", { text: "Loading change…" })]));
  const change = await api(`/api/changes/${id}`);
  if (token !== state.render) return;
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

  const context = changeContext(change);
  const list = renderFileList(context, context.files, "Files in this change");
  detail.append(el("section", { class: "detail-section files-section" }, [
    el("h3", {}, [
      `Files (${context.files.length})`,
      el("span", { class: "totals" }, [
        el("span", { class: "stat-add", text: `+${change.added}` }),
        el("span", { class: "stat-del", text: `−${change.removed}` }),
      ]),
    ]),
    context.files.length ? list : el("p", { class: "prose", text: "This patch touches no file." }),
  ]));

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
  const token = newestRender();
  const detail = clear($("detail"));
  detail.append(el("div", { class: "empty-state" }, [el("p", { text: "Loading the commit…" })]));
  const commit = await api(`/api/commits/${sha}`);
  if (token !== state.render) return;
  const context = commitContext(commit);
  const menu = () => commitMenu({ ...commit, kind: "commit" });
  clear(detail);

  const head = el("div", { class: "detail-head" }, [
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
        onclick: () => openCommit(parent), text: `↰ ${parent.slice(0, 7)}`,
      })),
      ...commit.refs.map((ref) => el("span", { class: "tag ref", text: ref })),
    ]),
    el("div", { class: "detail-actions" }, [
      el("button", {
        type: "button", class: "btn ghost", "aria-haspopup": "menu",
        onclick: (event) => Menu.show(event, menu()), text: "Actions ▾",
      }),
      el("button", {
        type: "button", class: "btn ghost",
        onclick: () => openFileView({ ...context, files: [] }, ""), text: "Whole patch",
      }),
    ]),
  ]);
  detail.append(Menu.attach(head, menu));

  const extra = commit.body.split("\n").slice(1).join("\n").trim();
  if (extra) {
    detail.append(el("section", { class: "detail-section" }, [
      el("p", { class: "prose message-body", text: extra }),
    ]));
  }

  const list = renderFileList(context, commit.files, "Files in this commit");
  detail.append(el("section", { class: "detail-section files-section" }, [
    el("h3", {}, [
      `Files (${commit.files.length})`,
      el("span", { class: "totals" }, [
        el("span", { class: "stat-add", text: `+${commit.added}` }),
        el("span", { class: "stat-del", text: `−${commit.removed}` }),
      ]),
    ]),
    commit.files.length ? list : el("p", { class: "prose", text: "No file changed." }),
  ]));
}
