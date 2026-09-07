//! The IPC surface. One command per entry of the route table the HTTP server used to expose,
//! typed by Tauri instead of parsed out of a URL.

use std::path::PathBuf;
use std::sync::RwLock;

use gitsquid_core::registry::{KnownRepo, Registry};
use gitsquid_core::{clone, git_cli, gitlog, history, refs, repo, worktree, Error};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

/// Commands answer with a message the interface can show; there is nothing to match on.
type Answer<T> = Result<T, String>;

fn failed(error: Error) -> String {
    error.to_string()
}

/// Which repository the window is looking at. `git2::Repository` is `Send` but not `Sync`, so
/// none is kept: every command opens its own, which also means each request rereads the disk.
pub struct Session {
    active: RwLock<Option<PathBuf>>,
    registry: Registry,
}

impl Session {
    pub fn new() -> Self {
        let registry = Registry::from_env();
        let active = registry
            .most_recent_existing()
            .or_else(|| std::env::current_dir().ok().and_then(|cwd| repo::find_repo_root(&cwd).ok()));
        Self { active: RwLock::new(active), registry }
    }

    fn path(&self) -> Answer<PathBuf> {
        self.active
            .read()
            .ok()
            .and_then(|active| active.clone())
            .ok_or_else(|| "No repository is open. Use Repository → Open Repository…".to_string())
    }

    fn open(&self) -> Answer<gitsquid_core::git2::Repository> {
        let path = self.path()?;
        gitsquid_core::git2::Repository::open(&path)
            .map_err(|_| format!("{} is no longer a Git repository.", path.display()))
    }

    fn switch_to(&self, root: PathBuf) {
        if let Ok(mut active) = self.active.write() {
            *active = Some(root);
        }
    }
}

impl Default for Session {
    fn default() -> Self {
        Self::new()
    }
}

// ------------------------------------------------------------------ reading

#[derive(Serialize)]
pub struct RepoState {
    name: String,
    path: String,
    branch: String,
    head: gitlog::Head,
    branches: Vec<gitlog::BranchInfo>,
    remote_branches: Vec<gitlog::RemoteBranch>,
    tags: Vec<gitlog::TagInfo>,
    status: gitlog::WorkingStatus,
    remotes: Vec<worktree::RemoteInfo>,
    tracking: worktree::Tracking,
    stashes: Vec<worktree::StashEntry>,
    operation: Option<gitlog::Pending>,
    head_message: String,
}

#[derive(Serialize)]
pub struct Config {
    version: String,
    git_version: String,
}

#[derive(Serialize)]
pub struct AppState {
    repo: RepoState,
    config: Config,
}

#[tauri::command]
pub fn state(session: tauri::State<'_, Session>) -> Answer<AppState> {
    let path = session.path()?;
    let found = session.open()?;
    Ok(AppState {
        repo: RepoState {
            name: path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default(),
            path: path.to_string_lossy().into_owned(),
            branch: gitlog::current_branch(&found),
            head: gitlog::head(&found),
            branches: gitlog::branches(&found),
            remote_branches: gitlog::remote_branches(&found),
            tags: gitlog::tags(&found),
            status: gitlog::working_status(&found),
            remotes: worktree::remotes(&found),
            tracking: worktree::tracking(&found),
            stashes: worktree::stash_list(&found),
            operation: gitlog::pending_operation(&found),
            head_message: worktree::head_message(&found),
        },
        config: Config {
            version: env!("CARGO_PKG_VERSION").to_string(),
            git_version: git_cli::version(),
        },
    })
}

#[derive(Serialize)]
pub struct Graph {
    commits: Vec<gitlog::Commit>,
    limit: usize,
    every_ref: bool,
    total_commits: usize,
}

#[tauri::command]
pub fn graph(session: tauri::State<'_, Session>, limit: usize, refs: String) -> Answer<Graph> {
    let found = session.open()?;
    let limit = limit.clamp(10, 5000);
    let every_ref = refs != "head";
    Ok(Graph {
        commits: gitlog::commits(&found, limit, every_ref),
        total_commits: gitlog::count_commits(&found, every_ref),
        limit,
        every_ref,
    })
}

#[tauri::command]
pub fn worktree_view(session: tauri::State<'_, Session>) -> Answer<worktree::WorktreeView> {
    Ok(worktree::view(&session.open()?))
}

#[derive(Serialize)]
pub struct Repos {
    active: String,
    repos: Vec<KnownRepo>,
}

#[tauri::command]
pub fn repos(session: tauri::State<'_, Session>) -> Answer<Repos> {
    Ok(Repos {
        active: session.path().map(|path| path.to_string_lossy().into_owned()).unwrap_or_default(),
        repos: session.registry.known(),
    })
}

#[derive(Serialize)]
pub struct Search {
    query: String,
    commits: Vec<gitlog::Commit>,
}

#[tauri::command]
pub fn search(session: tauri::State<'_, Session>, q: String) -> Answer<Search> {
    let found = session.open()?;
    let query: String = q.chars().take(200).collect();
    Ok(Search { commits: gitlog::search(&found, &query, 100), query })
}

#[derive(Serialize)]
pub struct FileHistory {
    path: String,
    commits: Vec<gitlog::HistoryEntry>,
}

#[tauri::command]
pub fn file_history(session: tauri::State<'_, Session>, path: String) -> Answer<FileHistory> {
    let found = session.open()?;
    let commits = gitlog::file_history(&found, &path, 50).map_err(failed)?;
    Ok(FileHistory { path, commits })
}

#[tauri::command]
pub fn blame(session: tauri::State<'_, Session>, path: String, rev: String) -> Answer<gitlog::Blame> {
    gitlog::blame(&session.open()?, &path, &rev).map_err(failed)
}

#[derive(Serialize)]
pub struct FileDiff {
    path: String,
    staged: bool,
    diff: String,
}

#[tauri::command]
pub fn file_diff(
    session: tauri::State<'_, Session>,
    path: String,
    staged: bool,
    ws: bool,
    ctx: u32,
) -> Answer<FileDiff> {
    let found = session.open()?;
    let diff = worktree::file_diff(&found, &path, staged, ws, ctx).map_err(failed)?;
    Ok(FileDiff { path, staged, diff })
}

#[tauri::command]
pub fn commit_detail(
    session: tauri::State<'_, Session>,
    sha: String,
) -> Answer<gitlog::CommitDetail> {
    gitlog::commit_detail(&session.open()?, &sha).map_err(failed)
}

#[tauri::command]
pub fn commit_patch(
    session: tauri::State<'_, Session>,
    sha: String,
    path: String,
    ws: bool,
    ctx: u32,
) -> Answer<gitlog::Patch> {
    let found = session.open()?;
    let wanted = if path.is_empty() { None } else { Some(path.as_str()) };
    gitlog::commit_patch(&found, &sha, wanted, ws, ctx).map_err(failed)
}

// ------------------------------------------------------------------ switching repository

#[tauri::command]
pub fn open_repo(session: tauri::State<'_, Session>, path: String) -> Answer<Value> {
    if path.trim().is_empty() {
        return Err("A repository path is required.".to_string());
    }
    let root = session.registry.add(&PathBuf::from(path)).map_err(failed)?;
    let name = root.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
    session.switch_to(root.clone());
    Ok(json!({ "message": format!("Opened {name}."), "active": root.to_string_lossy() }))
}

/// Clone, register, and open — the three things you always want together. `async` for the same
/// reason as [`worktree_action`]: cloning is network work, and the main thread must stay free.
#[tauri::command]
pub async fn clone_repo(
    session: tauri::State<'_, Session>,
    url: String,
    parent: String,
    name: String,
) -> Answer<Value> {
    let target = clone::clone(&url, &PathBuf::from(parent), &name).map_err(failed)?;
    session.registry.add(&target).map_err(failed)?;
    session.switch_to(target.clone());
    Ok(json!({
        "message": format!("Cloned into {}.", target.display()),
        "active": target.to_string_lossy(),
    }))
}

#[tauri::command]
pub fn forget_repo(session: tauri::State<'_, Session>, path: String) -> Answer<Value> {
    let target = PathBuf::from(&path);
    let resolved = target.canonicalize().unwrap_or(target);
    if session.path().ok().as_deref() == Some(resolved.as_path()) {
        return Err("Close this repository by opening another one first.".to_string());
    }
    if !session.registry.remove(&resolved) {
        return Err("That repository is not in the list.".to_string());
    }
    Ok(json!({ "message": "Removed from the list. Nothing on disk was touched." }))
}

// ------------------------------------------------------------------ acting

/// Every field any action might need. Absent ones default, and each action reads only its own.
#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct ActionPayload {
    pub paths: Vec<String>,
    pub branch: String,
    pub name: String,
    pub sha: String,
    pub path: String,
    pub message: String,
    pub patch: String,
    pub side: String,
    #[serde(rename = "ref")]
    pub reference: String,
    pub mode: String,
    pub target: String,
    pub url: String,
    pub force: bool,
    pub squash: bool,
    pub amend: bool,
}

/// `async` so Tauri runs it off the main thread: a fetch or push blocks for as long as the
/// network takes, and on the main thread that is a frozen window.
#[tauri::command]
pub async fn worktree_action(
    session: tauri::State<'_, Session>,
    action: String,
    payload: ActionPayload,
) -> Answer<Value> {
    // `stash_pop` and friends need a mutable handle; opening our own is what makes that safe.
    let mut found = session.open()?;
    let it = &payload;

    if action == "commit" {
        let result = worktree::commit(&found, &it.message, it.amend).map_err(failed)?;
        let verb = if it.amend { "Amended" } else { "Committed" };
        return Ok(json!({ "message": format!("{verb} {}.", result.short), "commit": result }));
    }

    let message = match action.as_str() {
        "stage" => worktree::stage(&found, &it.paths),
        "unstage" => worktree::unstage(&found, &it.paths),
        "discard" => worktree::discard(&found, &it.paths),
        "ignore" => worktree::ignore(&found, &it.paths),
        "resolve" => worktree::resolve(&found, &it.paths, &it.side),
        "stage-hunk" => worktree::apply_patch(&found, &it.patch, "stage"),
        "unstage-hunk" => worktree::apply_patch(&found, &it.patch, "unstage"),
        "discard-hunk" => worktree::apply_patch(&found, &it.patch, "discard"),
        "stash" => worktree::stash_save(&found, &it.message),
        "stash-pop" => worktree::stash_pop(&mut found, &it.reference),
        "stash-apply" => worktree::stash_apply(&mut found, &it.reference),
        "stash-drop" => worktree::stash_drop(&mut found, &it.reference),
        "stash-branch" => worktree::stash_branch(&found, &it.reference, &it.name),

        "checkout" => worktree::checkout(&found, &it.branch),
        "branch" => worktree::create_branch(&found, &it.name),
        "merge" => worktree::merge(&found, &it.branch, it.squash),
        "delete-branch" => worktree::delete_branch(&found, &it.branch, it.force),
        "rename-branch" => refs::rename_branch(&found, &it.branch, &it.name),
        "push-branch" => refs::push_branch(&found, &it.branch),
        "checkout-remote" => refs::track_remote_branch(&found, &it.branch),
        "delete-remote-branch" => refs::delete_remote_branch(&found, &it.branch),
        "tag-create" => refs::create_tag(&found, &it.name, &it.sha, &it.message),
        "tag-delete" => refs::delete_tag(&found, &it.name),
        "tag-push" => refs::push_tag(&found, &it.name),
        "remote-add" => refs::add_remote(&found, &it.name, &it.url),
        "remote-remove" => refs::remove_remote(&found, &it.name),
        "fetch" => worktree::fetch(&found),
        "pull" => worktree::pull(&found),
        "push" => worktree::push(&found, it.force),

        "checkout-commit" => history::checkout_commit(&found, &it.sha),
        "branch-from" => history::branch_from(&found, &it.sha, &it.name),
        "restore-file" => history::restore_file(&found, &it.sha, &it.path),
        "cherry-pick" => history::cherry_pick(&found, &it.sha),
        "revert-commit" => history::revert_commit(&found, &it.sha),
        "reset" => history::reset(&found, &it.sha, &it.mode),
        "rebase" => history::rebase(&found, &it.target),
        "abort" => history::abort(&found),
        "continue" => history::resume(&found),
        "skip" => history::skip(&found),

        unknown => return Err(format!("Unknown action: {unknown}")),
    };
    Ok(json!({ "message": message.map_err(failed)? }))
}
