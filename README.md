# GitSquid

A Git client for one repository at a time, in one native window, with nothing behind it but `git`.

No account, no server, no telemetry, no database of its own.

![A commit, its files, and the graph behind it](docs/commit.png)

---

## Install

Needs **git** on `PATH`. Nothing else — libgit2 is compiled into the binary.

```bash
./scripts/setup.sh    # check the toolchain, install what building needs
./scripts/dev.sh      # run it
```

`./scripts/release.sh macos` (or `linux`, `windows`) builds an installable bundle into `dist/`.

Building also needs **Rust** and **Node**, plus the platform's webview toolchain (Xcode CLT on
macOS, `libwebkit2gtk-4.1-dev` on Linux, MSVC build tools on Windows). `setup.sh` checks.

| Script | |
| --- | --- |
| `./scripts/setup.sh` | Install what building needs. |
| `./scripts/dev.sh` | Run from source, rebuilding on change. |
| `./scripts/test.sh` | The whole suite: the Rust engine, then the page's JavaScript. |
| `./scripts/release.sh <macos\|linux\|windows>` | Build a bundle into `dist/`. |

Tauri does not cross-compile, so each platform is built on itself — hence three CI runners.

---

## What it does

**Three columns.** The repository on the left (working tree, branches, remotes, tags, stashes),
the graph in the middle, what you selected on the right. Every row answers a right click.

- **Stage by file, by hunk, or by line.** Click line numbers to pick exactly what goes in.
- **The graph** shows every branch, newest first, with uncommitted work on top. Double click a
  commit to move HEAD there — checkout, detach, or reset, each spelled out.
- **Commit, amend, undo.** Branch, tag, merge, squash-merge, rebase, cherry-pick, revert, reset,
  stash, fetch, pull, push, clone.
- **When a replay conflicts**, a bar names it, counts the files, and offers Continue, Skip or
  Abort. Each file can be settled by keeping one side whole.
- **Read the past** with file history (renames followed), blame, and search across every branch by
  message, author or path.
- Wrap, ignore whitespace, more context — remembered, like the theme and the pane widths.

Nothing needs a mouse. <kbd>?</kbd> lists every shortcut.

![One file of that commit, read in the middle pane](docs/diff.png)

---

## How it is built

One Rust workspace, one binary: `crates/gitsquid-core` is the engine, `desktop/src-tauri` the
window, `desktop/ui` the page — plain HTML, CSS and JavaScript, no framework, no build step.
The page reaches the engine through Tauri's IPC: no server, no port, no HTTP.

The engine works through **libgit2**, except where that would be wrong. It runs `git` instead for:

| | Why |
| --- | --- |
| commit, merge, cherry-pick, revert, stash, annotated tags | libgit2 will not build a signature from an identity with an empty email, and it runs none of your hooks. |
| blame | It resolves authors through a mailmap, which hits the same signature check. |
| rebase | Its state must stay finishable with `git rebase --continue` from a terminal. |
| fetch, pull, push, clone | Your credential helpers and `~/.ssh/config` belong to `git`. |

---

## What it keeps

| What | Where |
| --- | --- |
| Repositories you have opened | `~/.config/gitsquid/repos.json` — paths only |
| Theme, pane widths, diff options | The window's local storage |
| Everything else | Your repository, in git, where it already was |

Nothing else is written. Uninstalling leaves your repositories exactly as git left them.

Every path, ref and commit id is validated before it reaches a command line, so nothing from the
page is ever read as a git option. Destructive actions ask first. `git` runs with
`GIT_TERMINAL_PROMPT=0`, so a remote operation fails readably instead of hanging on a prompt —
credentials stay git's business.

---

## Limitations

- One repository at a time. No pull requests, no code review, no issues.
- No conflict resolution in an editor, no interactive rebase, no submodules, worktrees or LFS.
- Pull is fast-forward only, on purpose: no merge commit behind your back.
- No side-by-side diff, and no syntax highlighting inside one.

---

MIT. Icons are [PrimeIcons](https://github.com/primefaces/primeicons), MIT, vendored under
`desktop/ui/assets/primeicons/`.
