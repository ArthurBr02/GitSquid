# gitia

A repository-local Git client assistant. It indexes one codebase into SQLite, asks one model for
a diff, validates that diff, applies it, runs your tests, and records every step — so you can
always see what changed, why, and whether it passed.

Everything lives inside the repository you point it at. No account, no server, no telemetry.

```
gitia ui                                     # the graph interface, on http://127.0.0.1:8756
gitia init                                   # create .gitia/gitia.db
gitia index                                  # index this repository
gitia run "make period_start timezone-aware" # propose → apply → test → record
gitia log                                    # what happened, newest first
gitia revert 3                               # undo a change, recorded too
```

---

## Setup

Requires **Python 3.12+** and **git** on `PATH`.

```bash
./scripts/install.sh          # creates .venv and installs gitia in editable mode
cp .env.example .env          # then edit .env
source .venv/bin/activate
gitia init && gitia index
gitia doctor                  # config, data location, model availability
```

`ANTHROPIC_API_KEY` in `.env` enables model-generated proposals. Without it the tool still works —
see [Degraded mode](#degraded-mode).

### Scripts

| Script | What it does |
| --- | --- |
| `./scripts/install.sh` | Create `.venv`, install runtime + dev dependencies. |
| `./scripts/dev.sh` | Editable install plus a `gitia doctor` smoke check. |
| `./scripts/test.sh` | Run the whole test suite (`pytest`). |
| `./scripts/build.sh` | Build the wheel and sdist into `dist/`. |
| `./scripts/run.sh` | Production-style local run: build, install the wheel into `.venv-prod`, and launch `gitia ui` from it. |

---

## The core loop

```
        ┌── index ──┐        ┌── propose ──┐      ┌── apply ──┐     ┌── test ──┐
repo ──▶│  files →  │──────▶ │  retrieval  │────▶ │ validate  │───▶ │ your     │
        │  chunks   │        │  → model    │      │ git apply │     │ command  │
        └───────────┘        └─────────────┘      └───────────┘     └──────────┘
              │                     │                    │                │
              └──────────────── SQLite: files, chunks, changes, test_runs, events ───┘
```

Every step writes to the database. A change moves through `proposed → applied → verified | failed`
and can end at `reverted`; illegal transitions are refused by the model layer, not by the UI.

### Commands

| Command | Purpose |
| --- | --- |
| `gitia init` | Create `.gitia/gitia.db`. |
| `gitia index [--force]` | Walk the repository, chunk text files, refresh the FTS5 index. |
| `gitia search QUERY` | Show exactly what retrieval would feed a proposal. |
| `gitia propose TASK` | Ask for a diff, validate it, record it. Writes nothing to your files. |
| `gitia show ID` | Diff, rationale, test runs, and audit trail for one change. |
| `gitia apply ID` | Apply a recorded diff to the working tree. |
| `gitia test [ID]` | Run `GITIA_TEST_COMMAND` and attach the result to a change. |
| `gitia revert ID` | Reverse an applied diff. |
| `gitia log [--status S]` | List recorded changes. |
| `gitia run TASK` | The whole loop in one command. `--revert-on-failure` undoes a red test run. |
| `gitia export FILE` / `gitia import FILE` | Portable JSON in and out. |
| `gitia sample load` / `gitia sample clear` | Labelled demo records, and their deletion. |
| `gitia doctor` | Configuration, data location, degraded-mode status. |
| `gitia ui` | Serve the graph interface on `127.0.0.1`: graph, staging, commit, and the change loop. |

---

## The interface

`gitia ui` serves a single page at `http://127.0.0.1:8756/`. No account, no login, no token —
it is a local tool for one person. It covers both halves of the loop — the git one and the
assistant one:

- **Repository switcher** — the chip in the top bar lists every registered repository; open
  another by absolute path and the whole interface follows. Each repository keeps its own
  database, index, `.env`, and history.
- **Graph** — a WIP node for uncommitted work sits on top, then gitia changes and git commits on
  one timeline. The lanes are laid out over the rows actually on screen and painted as one drawing,
  so every line joins the next row; a branch keeps one colour from tip to root, merges leave one
  curve per parent, and changes ride the lane below them as a diamond coloured by status. Filtering
  the list drops the lanes rather than joining rows that are not adjacent in history.
- **Uncommitted changes** — staged and unstaged files with their status codes, per-file diff with
  line numbers, stage / unstage / discard per file or in bulk, and a commit box. Committing is
  recorded in the audit trail alongside everything else.
- **Change detail** — rationale, facts, diff, every test run with its output, and the audit trail.
  Apply, Run tests, and Revert act from here, and each button disables itself when the change's
  status makes the action impossible.
- **Commit detail** — message, files, and the full patch.
- **Branches** — click to switch, <kbd>+</kbd> to create, and on hover: merge into the current
  branch, or delete (an unmerged branch is refused unless you force it).
- **Remotes** — Fetch, Pull (fast-forward only), and Push in the top bar, with ahead/behind
  badges. They disable themselves when the repository has no remote.
- **Stashes** — stash the working tree, then pop or drop from the sidebar.
- **Sidebar** — status filters with counts, the working-tree summary, index size, and the
  sample-data controls.
- **New change** — task plus an optional diff. Without an API key the diff becomes required, and
  the dialog says so before you submit rather than after.

Both dividers are draggable, and focusable for keyboard resizing with <kbd>←</kbd><kbd>→</kbd>.

Keyboard: <kbd>N</kbd> new change · <kbd>W</kbd> uncommitted changes · <kbd>I</kbd> re-index ·
<kbd>/</kbd> filter · <kbd>↑</kbd><kbd>↓</kbd> or <kbd>j</kbd><kbd>k</kbd> move ·
<kbd>Enter</kbd> focus the detail · <kbd>Esc</kbd> leave a field · <kbd>?</kbd> the full list.
Nothing needs a mouse.

The server binds `127.0.0.1` only and refuses any request whose `Host` header is not loopback,
which blocks DNS rebinding from a web page you might have open. Nothing outside the machine can
reach it, so there is no credential to manage. The page declares a strict CSP, loads no remote
asset, and the server never logs request contents. `git` runs with `GIT_TERMINAL_PROMPT=0`, so a
remote operation fails with a readable message instead of hanging on a password prompt.

---

## Architecture

`src/gitia/`, one responsibility per module:

| Module | Responsibility |
| --- | --- |
| `config.py` | Locate the repository root, read `.env`, validate settings. |
| `db.py` | SQLite connection, schema, `PRAGMA user_version` guard. |
| `models.py` | `Change` / `TestRun` / `Event` plus their repositories and the status state machine. |
| `indexer.py` | Walk, filter, chunk, hash; incremental re-indexing. |
| `retrieval.py` | FTS5 search and context assembly under a character budget. |
| `llm.py` | `ProposalBackend` protocol; the Anthropic backend and the patch-file backend. |
| `diffs.py` | Unified-diff parsing, path safety, `git apply` / `--check` / `--reverse`. |
| `runner.py` | Run the verification command, capture and time it. |
| `gitlog.py` | Read commits, parents, branches, and status. |
| `worktree.py` | Stage, unstage, discard, commit, branch, merge, stash, and remotes. |
| `registry.py` | The list of known repositories, shared by every session. |
| `web/` | Loopback HTTP server, JSON API, and the single-page interface; `assets/graph.js` lays out and paints the commit graph. |
| `workflow.py` | `ChangeService` — the core loop, independent of the CLI. |
| `portability.py` | Export and import, with validation of untrusted files. |
| `safety.py` | Redaction, path validation, input cleaning. |
| `ui.py` | Terminal states: empty, loading, validation, success, failure. |
| `cli.py` | Typer commands. Thin: argument handling and presentation only. |

The CLI depends on `ChangeService`; `ChangeService` depends on the `ProposalBackend` protocol, not
on Anthropic. That is what lets the test suite drive the full loop with a stub backend and no
network.

---

## Data location

| What | Where |
| --- | --- |
| Database | `<repo>/.gitia/gitia.db` (plus `-wal` / `-shm`), one per repository |
| Secrets | `<repo>/.env`, read only by this app, never written to the database |
| Repository list | `~/.config/gitia/repos.json` — paths only, override with `GITIA_CONFIG_DIR` |
| Exports | Wherever you point `gitia export` |

`.gitia/` contains a `.gitignore` with `*`, so it never lands in a commit. Nothing is written
outside the repository, and nothing leaves the machine except the excerpts sent with a proposal
request when you have an API key configured.

### Backup

The database is a single file. Back it up with a copy while no command is running:

```bash
sqlite3 .gitia/gitia.db ".backup '/path/to/gitia-backup.db'"   # safe while in use
gitia export ~/backups/gitia-$(date +%F).json                  # portable, human-readable
```

Restore by copying the file back, or with `gitia import` — import skips changes already recorded,
so re-importing the same file twice is harmless.

To delete everything gitia knows: `rm -rf .gitia/`. To delete only the demo rows:
`gitia sample clear`.

---

## Permissions

gitia asks for nothing it does not need:

- **Read** every file `git ls-files` reports as tracked or untracked-but-not-ignored — your
  `.gitignore` decides what gitia sees. Binaries, files over
  `GITIA_MAX_FILE_BYTES`, and credential-shaped files (`.env*`, `*.pem`, `*.key`, `id_rsa`,
  `.netrc`, …) are skipped and never indexed or sent.
- **Write** only through `git apply`, `git add`, `git checkout`, and `git commit`, only inside the
  repository, and only after you confirm. Every path — in a patch or from the interface — is
  rejected before it reaches git if it is absolute, contains `..`, or points into `.git/`
  or `.gitia/`. Discarding a file asks for confirmation because it cannot be undone.
- **Execute** exactly one command — the `GITIA_TEST_COMMAND` you configured — in the repository
  directory, with a timeout.
- **Listen** on `127.0.0.1` only while `gitia ui` is running, behind a per-session token.
- **Network** only to `api.anthropic.com`, only during `propose` / `run`, and only when
  `ANTHROPIC_API_KEY` is set.

No analytics, no telemetry, no third-party accounts, no background process.

---

## Degraded mode

Without `ANTHROPIC_API_KEY`, model-generated proposals are unavailable. Nothing else changes:

```bash
gitia propose "fix the rounding bug" --patch-file fix.diff
```

Indexing, search, validation, `git apply`, test runs, the audit trail, revert, export, and import
all work identically; the change is recorded with source `patch-file` instead of `model`.
`gitia doctor` tells you which mode you are in. This is the only difference — the tool is a
recording and verification harness first, and a model client second.

---

## Accessibility

- Every state prints a text token (`[ok]`, `[fail]`, `[warn]`, `[invalid]`, `[empty]`,
  `[working]`) as well as a colour, so nothing depends on colour perception.
- `NO_COLOR=1` (or `GITIA_NO_COLOR=1`) disables colour; `--plain` prints diffs without syntax
  highlighting for screen readers and for piping.
- No mouse, no TUI focus traps: every action is one command. Confirmations are explicit `y/N`
  prompts with a safe default, and `--yes` skips them for scripted use.
- Tables carry header labels; timestamps are ISO-8601 UTC; ids are stable and short.
- Long output is written to stdout and errors to stderr, so `2>/dev/null` and pipes behave.

---

## Security

- Test output and rationales pass through a redactor before they are stored or displayed; API
  keys, tokens, JWTs, and PEM private keys are replaced with `[redacted]`.
- File contents are stored only in the local database, never in logs or the audit trail.
- Imported JSON is treated as untrusted: format, version, types, statuses, and lengths are all
  validated, and diffs are re-validated for path safety before they can be applied.
- Free-text input is length-checked and stripped of control characters; search terms are converted
  into a quoted FTS5 expression rather than interpolated.

---

## Limitations

Stated plainly, because some of them are deliberate:

**Deliberately excluded** (these are what a paid product sells):

- **Frontier coding model quality.** One model call, one shot, no agent loop, no self-repair, no
  multi-file planning. A rejected or wrong patch is your problem to re-prompt.
- **Large-context infrastructure.** Retrieval is BM25 over 80-line chunks with a hard character
  budget (48k by default). No embeddings, no reranking, no whole-repository context.
- **IDE-wide polish and latency.** A terminal CLI. No editor integration, no inline diff review,
  no incremental streaming UI, no background indexing.

**Other limits:**

- One database per repository, and no cross-repository view: you switch between repositories,
  you do not see them side by side. No pull requests, no code review, no issue tracking.
- The interface stages, commits, branches, merges, stashes, fetches, pulls, and pushes. It does
  **not** rebase, cherry-pick, clone, resolve conflicts, or edit history. A merge conflict is
  reported and left for git — gitia will not pretend to resolve it.
- Pull is fast-forward only, on purpose: no implicit merge commit behind your back.
- Credentials are git's business. gitia never asks for or stores one, and a remote operation that
  would need an interactive prompt fails with an explanation instead of hanging.
- Staging is per file, not per hunk.
- A repository with no commits shows an empty graph — that is the empty state, not an error.
- `git apply` is strict: a diff generated against stale excerpts is refused rather than fuzzed in.
- `revert` reverses the recorded patch. If you edited the same lines afterwards it will refuse,
  and you resolve it with git.
- Sample records describe a fictional `billing/` module and cannot be applied; they exist to show
  the shape of the data.
- Binary files, large files, and credential files are never indexed, so the model cannot reason
  about them.

## Licence

MIT.
