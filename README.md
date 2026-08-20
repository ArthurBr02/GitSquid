# GitSquid

A Git client for one repository at a time. It draws the graph, stages by file, by hunk or by line,
commits, branches, merges, rebases, blames, and searches the whole history — in a page served on
your own machine, with nothing behind it but `git`.

No account, no server, no telemetry, no database of its own.

```bash
gitsquid ui          # the interface, on http://127.0.0.1:8756
gitsquid ui --repo . # this folder rather than the last one you opened
gitsquid doctor      # which repository would open, and what this is made of
```

---

## Setup

Requires **Python 3.12+** and **git** on `PATH`.

```bash
./scripts/install.sh          # creates .venv and installs gitsquid in editable mode
source .venv/bin/activate
gitsquid ui
```

### Scripts

| Script | What it does |
| --- | --- |
| `./scripts/install.sh` | Create `.venv`, install runtime + dev dependencies. |
| `./scripts/dev.sh` | Editable install plus a `gitsquid doctor` smoke check. |
| `./scripts/test.sh` | Run the whole test suite (`pytest`). |
| `python -m playwright install webkit` | Once, to let the browser smoke test actually run a browser; it skips itself otherwise. |
| `./scripts/build.sh` | Build the wheel and sdist into `dist/`. |
| `./scripts/run.sh` | Production-style local run: build, install the wheel into `.venv-prod`, and launch `gitsquid ui` from it. |

---

## The interface

![A commit, its files, and the graph behind it](docs/commit.png)

![One file of that commit, read in the middle pane](docs/diff.png)

`gitsquid ui` serves a single page at `http://127.0.0.1:8756/`. No account, no login, no token —
it is a local tool for one person. Three columns, one job each:

- **Left — what the repository holds.** Collapsible sections, remembered between sessions: the
  working tree, local branches with how far each has drifted from its upstream, remote branches,
  tags, stashes. Every entry is the same row, and every row answers a right click — or the ⋯ that
  appears on hover — with what can be done to it.
- **Middle — the graph, or one file.** Every branch's commits, newest first — work pushed to
  another branch is newer, not invisible — with a WIP node for uncommitted work on top. Where you
  are is ringed on the graph and marked on its row, and the chip in the header says which branch
  that is and jumps to it. The lanes are laid out over the rows actually on screen and painted as
  one drawing, so a branch keeps one colour from tip to root and a merge leaves one curve per
  parent. Click a file anywhere in the interface and the graph gives way to that file's
  diff, full width, with <kbd>↑</kbd> <kbd>↓</kbd> to walk the other files of the same commit and
  <kbd>Esc</kbd> to come back.
- **Right — what is selected.** A commit shows its message, then its files — grouped by folder,
  with their status letter and the lines each one gained and lost — never the whole patch at once,
  which is what makes a 27-file merge readable. The working tree shows the commit box, with an
  **Amend** toggle, and the files grouped into conflicted, staged and unstaged.

The top bar holds the repository chip — every repository you have opened, plus **Clone a
repository…** for one you do not have yet — then Fetch, Pull and Push with their ahead/behind
counts. The **⋯** menu keeps the rest: the repository list, a manual refresh, the appearance, the
shortcuts. A right click on Push offers a force push with lease. The page refreshes itself when you
come back to the window, so what your editor did shows up without asking.

**What a right click offers.** On a commit: check it out, branch from it, tag it, cherry-pick it,
revert it, rebase the current branch onto it, reset the branch to it (soft / mixed / hard), copy
its SHA or its message. On a branch: check out, merge or squash merge, rebase onto it, branch from
it, rename, push, delete. On a remote branch: check out as a tracking branch, fetch, delete on the
remote. On a tag: check out, push, delete. On a stash: show what it holds, apply, pop, branch from
it, drop. On a file:
stage, unstage, discard, ignore, file history, blame, copy path — and, in a commit, restore that
version into the working tree.

- **Per-hunk and per-line staging** — a working-tree diff carries Stage, Unstage and Discard on
  each hunk; click the line numbers to pick individual lines and act on exactly those. An unpicked
  removal stays as context and an unpicked addition disappears, so what git receives is a patch of
  precisely what you chose.
- **Search** — typing filters the rows on screen; <kbd>Enter</kbd> asks git instead, across every
  branch, by message, author or touched path.
- **Depth and scope** — the graph says how far back it has looked ("80 of 3657 commits loaded")
  and goes further on request; the filter chip switches between every branch and this branch only.
- **Reading options** — wrap long lines, ignore whitespace, or ask for ten or twenty-five lines of
  context instead of three; each one is remembered.
- **Undo and amend** — the head commit offers both: amend reopens it in the commit box, undo is a
  soft reset onto its parent that brings the work back staged.
- **Move HEAD** — double click a commit (or its **Move HEAD here…** menu item) and choose how: check
  out a branch that already points there, detach onto the commit, or reset your branch to it —
  soft, mixed or hard, each spelled out in terms of what happens to your files.
- **Appearance** — dark, light, or whatever the system asks for.
- **Interrupted operations** — when a merge, rebase, cherry-pick or revert stops on a conflict, a
  bar names it, counts the files that still conflict, and offers **Continue** (once none do),
  **Skip** for the commit being replayed, or **Abort**. The conflicted files get their own group in
  the panel, and each can be settled by keeping your side or the incoming side whole.
- **File history** — the commits that touched one file, renames followed, from its context menu.
- **Blame** — who last touched each line, from the same menus; a line's commit is one click away.
- **Remotes** — add or remove one from the sidebar, so a repository that starts without a remote
  does not need a terminal to gain one.

Both dividers are draggable, keep their width between sessions, and are focusable for keyboard
resizing with <kbd>←</kbd><kbd>→</kbd>.

Keyboard: <kbd>W</kbd> uncommitted changes · <kbd>B</kbd> branch ·
<kbd>T</kbd> tag · <kbd>S</kbd> stash · <kbd>H</kbd> where you are · <kbd>R</kbd> refresh ·
<kbd>/</kbd> search · <kbd>↑</kbd><kbd>↓</kbd> or <kbd>j</kbd><kbd>k</kbd> move ·
<kbd>Enter</kbd> focus the detail · <kbd>Shift</kbd>+<kbd>F10</kbd> the menu of the selected row ·
<kbd>Esc</kbd> leave a field, close a menu, or leave a file · <kbd>?</kbd> the full list. Nothing
needs a mouse: every context menu is reachable from the keyboard and walks with the arrow keys.

The server binds `127.0.0.1` only and refuses any request whose `Host` header is not loopback,
which blocks DNS rebinding from a web page you might have open. Nothing outside the machine can
reach it, so there is no credential to manage. The page declares a strict CSP, loads no remote
asset, and the server never logs request contents. `git` runs with `GIT_TERMINAL_PROMPT=0`, so a
remote operation fails with a readable message instead of hanging on a password prompt.

---

## The desktop app

`desktop/` is a Tauri shell: a native window that starts `gitsquid ui` on a free loopback port and
shows it. It needs the `gitsquid` command on the machine — it looks at `$GITSQUID_BIN`, then the
project's `.venv/bin`, then `PATH`.

```bash
cd desktop && npm install
npm run dev                    # the window, against the local engine
npm run build:macos            # .app and .dmg
npm run build:macos:universal  # the same, as a universal binary
npm run build:windows          # .msi and .exe
npm run build:linux            # .deb and .AppImage
```

Each bundle is built by the operating system it targets — Tauri does not cross-compile a desktop
bundle, and running the wrong one tells you so immediately instead of failing inside a Rust build.

---

## Architecture

`src/gitsquid/`, one responsibility per module:

| Module | Responsibility |
| --- | --- |
| `config.py` | Find the root of the repository you are in. |
| `gitcmd.py` | The one place git is invoked: process, timeouts, credential-prompt refusal, and the validation of every ref, path and commit id that reaches a command line. |
| `gitlog.py` | Reads: commits, parents, branches, remote branches, tags, status, file history, blame, and the operation git stopped in the middle of. |
| `worktree.py` | The working tree and the index: stage, unstage, discard, ignore, per-hunk apply, commit, amend, branch, merge, stash, and the remote sync. |
| `refs.py` | Tags, remote branches, remotes, and branch renaming. |
| `history.py` | Check out a commit, branch from it, cherry-pick, revert, reset, rebase, restore one file — and abort, skip or continue what conflicts. |
| `clone.py` | Getting a repository in the first place. |
| `diffs.py` | Unified-diff parsing and path safety, for the patches the interface builds. |
| `registry.py` | The list of known repositories, shared by every session. |
| `safety.py` | Path validation and credential-shaped file detection. |
| `phrasing.py` | How the product counts things, so nothing says "1 file(s)". |
| `ui.py` | Terminal states for the two commands the CLI has. |
| `cli.py` | Typer commands: serve the interface, report the setup. |
| `web/` | Loopback HTTP server and JSON API; `assets/primeicons/` is the icon font, vendored (MIT) so the page fetches nothing from the network. The page is small scripts, one job each: `base` (elements, text, the API), `menu` (the context-menu component), `menus` (what each row offers), `sidebar`, `viewer` (the middle pane when it shows a file), `diff` (how a patch is drawn and picked apart), `panels` (the right column), `actions`, `repos`, and `app` (state, rows, selection, boot). `graph.js` lays out and paints the lanes. |

---

## What it keeps, and where

| What | Where |
| --- | --- |
| The list of repositories you have opened | `~/.config/gitsquid/repos.json` — paths only, override with `GITSQUID_CONFIG_DIR` |
| How you like to read a diff, the theme, the pane widths | Your browser's local storage |
| Everything else | Your repository, in git, where it was already |

GitSquid writes nothing else. There is no database, no index, no cache to clear: uninstalling it
leaves your repositories exactly as git left them.

---

## Permissions

- **Read** the repository through git, and the files git reports in it.
- **Write** only through git itself — `add`, `checkout`, `commit`, `apply`, `branch`, `tag`,
  `merge`, `rebase`, `cherry-pick`, `revert`, `reset`, `stash`, `push`, `clone` — only inside the
  repository you opened, and only when you ask. Every path is rejected before it reaches git if it
  is absolute, contains `..`, or points into `.git/`; every branch, tag and commit id is validated
  the same way, so nothing from the interface is ever read as a git option. Anything destructive —
  discard, hard reset, force push, force delete, dropping a stash — asks first.
- **Listen** on `127.0.0.1` only while `gitsquid ui` is running, and only for requests whose `Host`
  header is a loopback name.
- **Network** only where you point git: fetch, pull, push and clone talk to your remotes and to
  nothing else. The page itself loads no remote asset.

No analytics, no telemetry, no third-party accounts, no background process.

---

## Accessibility

- Every terminal state prints a text token (`[ok]`, `[fail]`, `[warn]`, `[info]`) as well as a
  colour, so nothing depends on colour perception. `NO_COLOR=1` disables colour.
- In the page, every action is reachable from the keyboard: the graph walks with the arrow keys,
  <kbd>Shift</kbd>+<kbd>F10</kbd> opens the menu of the selected row, and every menu walks with the
  arrow keys. Nothing needs a mouse.
- Status is carried by a letter as well as a colour (A, M, D, R, U), and the interface follows the
  system's light or dark preference unless you override it.

---

## Security

- The server binds `127.0.0.1` only and refuses any request whose `Host` header is not loopback,
  which blocks DNS rebinding from a web page you might have open. The page declares a strict CSP,
  loads no remote asset, and the server never logs request contents.
- `git` runs with `GIT_TERMINAL_PROMPT=0`, so a remote operation fails with a readable message
  instead of hanging on a password prompt. Credentials stay git's business: GitSquid never asks for
  one, never stores one.
- Every path, ref and commit id from the interface is validated before it reaches a command line,
  and patches built in the page are re-checked for path safety before `git apply` sees them.

---

## Limitations

- One repository at a time: you switch between them, you do not see them side by side. No pull
  requests, no code review, no issue tracking.
- The interface stages by file, by hunk or by line, commits, amends, branches, tags, merges,
  squash-merges, rebases, cherry-picks, reverts, resets, stashes, fetches, pulls, pushes and
  clones. It does **not** resolve conflicts in an editor, rebase interactively, or manage
  submodules, worktrees and LFS. A conflict is named and counted, and can be settled by keeping one
  side whole; anything finer is your editor's job, then Continue, Skip or Abort from the bar.
- Pull is fast-forward only, on purpose: no implicit merge commit behind your back.
- No side-by-side diff, and no syntax highlighting inside one.
- A repository with no commits shows an empty graph — that is the empty state, not an error.

## Licence

MIT. The icons are [PrimeIcons](https://github.com/primefaces/primeicons), MIT, vendored under
`src/gitsquid/web/assets/primeicons/`.
