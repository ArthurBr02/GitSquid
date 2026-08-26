# GitSquid

A Git client for one repository at a time. It draws the graph, stages by file, by hunk or by line,
commits, branches, merges, rebases, blames, and searches the whole history — in one native window,
with nothing behind it but `git`.

No account, no server, no telemetry, no database of its own.

---

## Setup

Requires **git** on `PATH`. Nothing else at runtime: libgit2 is compiled into the binary.

Download the bundle for your platform, or build it yourself:

```bash
./scripts/setup.sh            # check the toolchain, install what building needs
./scripts/dev.sh              # run the app from source
```

Building needs **Rust** and **Node**, plus the platform's webview toolchain — Xcode command line
tools on macOS, `libwebkit2gtk-4.1-dev` and friends on Linux, the MSVC build tools on Windows.
`setup.sh` checks for them and installs the Linux ones on apt systems.

### Scripts

| Script | What it does |
| --- | --- |
| `./scripts/setup.sh` | Check the toolchain, install the platform dependencies and the npm ones. |
| `./scripts/dev.sh` | Run the app from source, rebuilding on change. |
| `./scripts/test.sh` | The whole suite: the Rust engine, then the page's own JavaScript. |
| `./scripts/release.sh <macos\|linux\|windows>` | Build an installable bundle and collect it in `dist/`. |

Tauri does not cross-compile a desktop bundle, so each platform is built on itself — that is what
the release workflow's three runners are for.

---

## The interface

![A commit, its files, and the graph behind it](docs/commit.png)

![One file of that commit, read in the middle pane](docs/diff.png)

One window, one repository. No account, no login, no token — it is a local tool for one person.
Three columns, one job each:

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

Nothing listens on a socket: the page reaches the engine through the window's own IPC, so there is
no port to expose and no credential to manage. It declares a strict CSP and loads no remote asset.
`git` runs with `GIT_TERMINAL_PROMPT=0`, so a remote operation fails with a readable message
instead of hanging on a password prompt.

---

## How it is built

One Rust workspace, one binary.

| Piece | What it is |
| --- | --- |
| `crates/gitsquid-core` | The engine: everything GitSquid knows how to do to a repository. |
| `desktop/src-tauri` | The native window, and the commands the page calls. |
| `desktop/ui` | The page itself — plain HTML, CSS and JavaScript, no framework, no build step. |

The engine reads and writes through **libgit2**, except where that would be wrong. Anything that
creates a commit or an annotated tag runs `git` instead: libgit2 refuses to build a signature from
an identity with an empty email, and it runs none of your hooks. Blame, rebase and every network
operation shell out too — respectively because libgit2 resolves authors through a mailmap that hits
the same signature check, because a rebase must stay finishable from a terminal, and because your
credential helpers and `~/.ssh/config` belong to `git`.

The page talks to the engine through Tauri's IPC. There is no server, no port, and no HTTP.

---

## Architecture

`crates/gitsquid-core/src/`, one responsibility per module:

| Module | Responsibility |
| --- | --- |
| `repo.rs` | Find the root of the repository you are in. |
| `git_cli.rs` | The one place `git` is invoked: process, timeouts, credential-prompt refusal, and the readable translation of an auth failure. |
| `validate.rs` | Every ref, path and commit id is checked here before it can reach a command line. |
| `gitlog.rs` | Reads: commits, parents, branches, remote branches, tags, status, file history, blame, and the operation git stopped in the middle of. |
| `worktree.rs` | The working tree and the index: stage, unstage, discard, ignore, per-hunk apply, commit, amend, branch, merge, stash, and the remote sync. |
| `refs.rs` | Tags, remote branches, remotes, and branch renaming. |
| `history.rs` | Check out a commit, branch from it, cherry-pick, revert, reset, rebase, restore one file — and abort, skip or continue what conflicts. |
| `clone.rs` | Getting a repository in the first place. |
| `diffs.rs` | Unified-diff parsing, path safety, and the patch inverter that unstaging a hunk needs — libgit2 has no `--reverse`. |
| `registry.rs` | The list of known repositories, shared by every session. |
| `safety.rs` | Path validation and credential-shaped file detection. |
| `phrasing.rs` | How the product counts things, so nothing says "1 file(s)". |
| `time.rs` | Dates in the shapes git prints them, without a datetime dependency. |
| `desktop/ui/assets/` | The page: small scripts, one job each — `base` (elements, text, the IPC), `menu` (the context-menu component), `menus` (what each row offers), `sidebar`, `viewer` (the middle pane when it shows a file), `diff` (how a patch is drawn and picked apart), `panels` (the right column), `actions`, `repos`, and `app` (state, rows, selection, boot). `graph.js` lays out and paints the lanes. `primeicons/` is the icon font, vendored (MIT) so the page fetches nothing from the network. |

---

## What it keeps, and where

| What | Where |
| --- | --- |
| The list of repositories you have opened | `~/.config/gitsquid/repos.json` — paths only, override with `GITSQUID_CONFIG_DIR` |
| How you like to read a diff, the theme, the pane widths | The window's own local storage |
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
- **Listen** on nothing. There is no socket, no port, and no server.
- **Network** only where you point git: fetch, pull, push and clone talk to your remotes and to
  nothing else. The page itself loads no remote asset.

No analytics, no telemetry, no third-party accounts, no background process.

---

## Accessibility

- In the page, every action is reachable from the keyboard: the graph walks with the arrow keys,
  <kbd>Shift</kbd>+<kbd>F10</kbd> opens the menu of the selected row, and every menu walks with the
  arrow keys. Nothing needs a mouse.
- Status is carried by a letter as well as a colour (A, M, D, R, U), and the interface follows the
  system's light or dark preference unless you override it.

---

## Security

- Nothing listens on a socket. The page reaches the engine through the window's own IPC, and every
  command it may call is named in the app's capability — one missing from that list is refused, not
  quietly allowed. The page declares a strict CSP and loads no remote asset.
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
