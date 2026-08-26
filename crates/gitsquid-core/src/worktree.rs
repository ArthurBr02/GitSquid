//! The working tree and the index, as the staging panel reads them.

use std::collections::HashMap;

use git2::{Repository, Status};
use serde::Serialize;

use crate::safety::is_sensitive_path;
use crate::time::{now, relative};
use crate::{Error, Result};

const STATUS_LABELS: &[(char, &str)] = &[
    ('M', "modified"),
    ('A', "added"),
    ('D', "deleted"),
    ('R', "renamed"),
    ('C', "copied"),
    ('U', "conflicted"),
    ('?', "untracked"),
    ('T', "typechange"),
];

const INDEX_SIDE: Status = Status::INDEX_NEW
    .union(Status::INDEX_MODIFIED)
    .union(Status::INDEX_DELETED)
    .union(Status::INDEX_RENAMED)
    .union(Status::INDEX_TYPECHANGE);

fn label_for(code: char) -> String {
    STATUS_LABELS
        .iter()
        .find(|(letter, _)| *letter == code)
        .map(|(_, label)| (*label).to_string())
        .unwrap_or_default()
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
pub struct Counts {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub staged: Option<[i64; 2]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub unstaged: Option<[i64; 2]>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FileEntry {
    pub path: String,
    pub original: Option<String>,
    pub index_code: String,
    pub work_code: String,
    pub staged: bool,
    pub unstaged: bool,
    pub untracked: bool,
    pub conflicted: bool,
    pub index_label: String,
    pub work_label: String,
    pub sensitive: bool,
    pub counts: Counts,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WorktreeView {
    pub files: Vec<FileEntry>,
    pub staged: usize,
    pub unstaged: usize,
    pub conflicted: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Tracking {
    pub upstream: Option<String>,
    pub ahead: usize,
    pub behind: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StashEntry {
    #[serde(rename = "ref")]
    pub reference: String,
    pub subject: String,
    pub age: String,
    pub sha: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RemoteInfo {
    pub name: String,
    pub url: String,
}

/// The two porcelain letters: what the index holds, and what the working tree holds.
fn codes(status: Status) -> (char, char) {
    if status.contains(Status::CONFLICTED) {
        return ('U', 'U');
    }
    if status.contains(Status::WT_NEW) && !status.intersects(INDEX_SIDE) {
        return ('?', '?');
    }
    let index = if status.contains(Status::INDEX_NEW) {
        'A'
    } else if status.contains(Status::INDEX_MODIFIED) {
        'M'
    } else if status.contains(Status::INDEX_DELETED) {
        'D'
    } else if status.contains(Status::INDEX_RENAMED) {
        'R'
    } else if status.contains(Status::INDEX_TYPECHANGE) {
        'T'
    } else {
        ' '
    };
    let work = if status.contains(Status::WT_MODIFIED) {
        'M'
    } else if status.contains(Status::WT_DELETED) {
        'D'
    } else if status.contains(Status::WT_RENAMED) {
        'R'
    } else if status.contains(Status::WT_TYPECHANGE) {
        'T'
    } else if status.contains(Status::WT_NEW) {
        'M'
    } else {
        ' '
    };
    (index, work)
}

fn entry_paths(entry: &git2::StatusEntry) -> (String, Option<String>) {
    let delta = entry.head_to_index().or_else(|| entry.index_to_workdir());
    let renamed = delta
        .as_ref()
        .filter(|delta| matches!(delta.status(), git2::Delta::Renamed | git2::Delta::Copied))
        .and_then(|delta| delta.old_file().path().map(|path| path.to_string_lossy().into_owned()));
    // For a rename `StatusEntry::path()` hands back the name the file *had*; the panel wants
    // the one it has now, which is what porcelain puts first too.
    let path = delta
        .as_ref()
        .and_then(|delta| delta.new_file().path().map(|path| path.to_string_lossy().into_owned()))
        .or_else(|| entry.path().map(str::to_string))
        .unwrap_or_default();
    (path, renamed)
}

fn describe(entry: &git2::StatusEntry, counts: &HashMap<String, Counts>) -> FileEntry {
    let status = entry.status();
    let (index_code, work_code) = codes(status);
    let (path, original) = entry_paths(entry);
    let counts = counts.get(&path).copied().unwrap_or_default();

    FileEntry {
        staged: !matches!(index_code, ' ' | '?'),
        unstaged: work_code != ' ',
        untracked: index_code == '?',
        conflicted: index_code == 'U' || work_code == 'U',
        index_label: label_for(index_code),
        work_label: label_for(work_code),
        sensitive: is_sensitive_path(&path),
        index_code: index_code.to_string(),
        work_code: work_code.to_string(),
        original,
        path,
        counts,
    }
}

fn status_options(all_untracked: bool) -> git2::StatusOptions {
    let mut options = git2::StatusOptions::new();
    options
        .include_untracked(true)
        .recurse_untracked_dirs(all_untracked)
        .renames_head_to_index(true)
        .renames_index_to_workdir(true);
    options
}

pub fn status(repo: &Repository) -> Vec<FileEntry> {
    let counts = line_counts(repo);
    let mut options = status_options(true);
    let Ok(statuses) = repo.statuses(Some(&mut options)) else {
        return Vec::new();
    };
    let mut entries: Vec<FileEntry> =
        statuses.iter().map(|entry| describe(&entry, &counts)).collect();
    entries.sort_by(|left, right| left.path.cmp(&right.path));
    entries
}

/// Lines gained and lost per file, on each side of the index. `[-1, -1]` marks a binary file.
pub fn line_counts(repo: &Repository) -> HashMap<String, Counts> {
    let mut found: HashMap<String, Counts> = HashMap::new();
    let head_tree = repo.head().and_then(|head| head.peel_to_tree()).ok();

    let staged = repo.diff_tree_to_index(head_tree.as_ref(), None, None).ok();
    let unstaged = repo.diff_index_to_workdir(None, None).ok();

    for (diff, side) in [(staged, true), (unstaged, false)] {
        let Some(diff) = diff else { continue };
        for (index, delta) in diff.deltas().enumerate() {
            let Some(path) =
                delta.new_file().path().map(|path| path.to_string_lossy().into_owned())
            else {
                continue;
            };
            let patch = git2::Patch::from_diff(&diff, index).ok().flatten();
            let binary = patch
                .as_ref()
                .is_none_or(|patch| patch.delta().flags().contains(git2::DiffFlags::BINARY));
            let pair = match patch.as_ref().filter(|_| !binary) {
                Some(patch) => {
                    let (_, added, removed) = patch.line_stats().unwrap_or((0, 0, 0));
                    [added as i64, removed as i64]
                }
                None => [-1, -1],
            };
            let entry = found.entry(path).or_default();
            if side {
                entry.staged = Some(pair);
            } else {
                entry.unstaged = Some(pair);
            }
        }
    }
    found
}

pub fn view(repo: &Repository) -> WorktreeView {
    let files = status(repo);
    WorktreeView {
        staged: files.iter().filter(|file| file.staged).count(),
        unstaged: files.iter().filter(|file| file.unstaged || file.untracked).count(),
        conflicted: files.iter().filter(|file| file.conflicted).count(),
        files,
    }
}

/// The diff of one file, on the side of the index the caller asked for.
pub fn file_diff(
    repo: &Repository,
    path: &str,
    staged: bool,
    ignore_whitespace: bool,
    context: u32,
) -> Result<String> {
    crate::validate::require_paths(std::slice::from_ref(&path.to_string()))?;

    let mut options = git2::DiffOptions::new();
    options.context_lines(context.min(100)).ignore_whitespace(ignore_whitespace).pathspec(path);
    let diff = if staged {
        let head_tree = repo.head().and_then(|head| head.peel_to_tree()).ok();
        repo.diff_tree_to_index(head_tree.as_ref(), None, Some(&mut options))?
    } else {
        // An untracked file has no diff against the index; showing its content makes it read
        // as the whole-file addition it will become.
        options.include_untracked(true).show_untracked_content(true).recurse_untracked_dirs(true);
        repo.diff_index_to_workdir(None, Some(&mut options))?
    };

    let mut text = Vec::new();
    diff.print(git2::DiffFormat::Patch, |_, _, line| {
        if matches!(line.origin(), '+' | '-' | ' ') {
            text.push(line.origin() as u8);
        }
        text.extend_from_slice(line.content());
        true
    })?;
    Ok(String::from_utf8_lossy(&text).into_owned())
}

/// Ahead/behind counts against the upstream branch, when there is one.
pub fn tracking(repo: &Repository) -> Tracking {
    let none = Tracking { upstream: None, ahead: 0, behind: 0 };
    let Ok(head) = repo.head() else { return none };
    let Some(name) = head.shorthand() else { return none };
    let Ok(branch) = repo.find_branch(name, git2::BranchType::Local) else { return none };
    let Ok(upstream) = branch.upstream() else { return none };

    let Some(upstream_name) = upstream.name().ok().flatten().map(str::to_string) else {
        return none;
    };
    let (ahead, behind) = head
        .target()
        .zip(upstream.get().target())
        .and_then(|(local, remote)| repo.graph_ahead_behind(local, remote).ok())
        .unwrap_or((0, 0));

    Tracking { upstream: Some(upstream_name), ahead, behind }
}

/// A stash is a commit, so it carries its sha: the interface reads it like any other.
pub fn stash_list(repo: &Repository) -> Vec<StashEntry> {
    let Ok(reflog) = repo.reflog("refs/stash") else { return Vec::new() };
    let clock = now();
    reflog
        .iter()
        .enumerate()
        .map(|(index, entry)| {
            let oid = entry.id_new();
            let commit = repo.find_commit(oid).ok();
            StashEntry {
                reference: format!("stash@{{{index}}}"),
                subject: commit
                    .as_ref()
                    .and_then(|commit| commit.summary())
                    .or_else(|| entry.message())
                    .unwrap_or("")
                    .to_string(),
                age: commit
                    .as_ref()
                    .map(|commit| relative(commit.time().seconds(), clock))
                    .unwrap_or_default(),
                sha: oid.to_string(),
            }
        })
        .collect()
}

pub fn remotes(repo: &Repository) -> Vec<RemoteInfo> {
    let Ok(names) = repo.remotes() else { return Vec::new() };
    names
        .iter()
        .flatten()
        .filter_map(|name| {
            let remote = repo.find_remote(name).ok()?;
            Some(RemoteInfo {
                name: name.to_string(),
                url: remote.url().unwrap_or("").to_string(),
            })
        })
        .collect()
}

pub fn head_message(repo: &Repository) -> String {
    repo.head()
        .and_then(|head| head.peel_to_commit())
        .map(|commit| commit.message().unwrap_or("").trim().to_string())
        .unwrap_or_default()
}

pub fn default_remote(repo: &Repository) -> Result<String> {
    let found = remotes(repo);
    if found.is_empty() {
        return Err(Error::git("This repository has no remote configured."));
    }
    if found.iter().any(|remote| remote.name == "origin") {
        return Ok("origin".to_string());
    }
    Ok(found[0].name.clone())
}

// ------------------------------------------------------------------ the index, written

use std::path::Path;

use crate::phrasing::plural;
use crate::{diffs, validate};

fn workdir(repo: &Repository) -> Result<&Path> {
    repo.workdir().ok_or_else(|| Error::git("This repository has no working tree."))
}

/// `git add`: new content and deletions alike.
pub fn stage(repo: &Repository, paths: &[String]) -> Result<String> {
    validate::require_paths(paths)?;
    let mut index = repo.index()?;
    index.add_all(paths.iter(), git2::IndexAddOption::DEFAULT, None)?;
    // add_all alone never records a removal; update_all is the other half of `git add`.
    index.update_all(paths.iter(), None)?;
    index.write()?;
    Ok(format!("Staged {}.", plural(paths.len(), "file")))
}

pub fn unstage(repo: &Repository, paths: &[String]) -> Result<String> {
    validate::require_paths(paths)?;
    match repo.head().and_then(|head| head.peel(git2::ObjectType::Commit)) {
        Ok(head) => repo.reset_default(Some(&head), paths.iter())?,
        Err(_) => {
            // No HEAD yet: there is nothing to reset to, so the entry simply leaves the index.
            let mut index = repo.index()?;
            for path in paths {
                let _ = index.remove_path(Path::new(path));
            }
            index.write()?;
        }
    }
    Ok(format!("Unstaged {}.", plural(paths.len(), "file")))
}

/// Throw away working-tree edits. Destructive: the caller must confirm first.
pub fn discard(repo: &Repository, paths: &[String]) -> Result<String> {
    validate::require_paths(paths)?;
    let root = workdir(repo)?.to_path_buf();
    let untracked: Vec<String> = status(repo)
        .into_iter()
        .filter(|entry| entry.untracked)
        .map(|entry| entry.path)
        .collect();

    let tracked: Vec<&String> = paths.iter().filter(|path| !untracked.contains(path)).collect();
    let mut removed = 0;
    for path in paths.iter().filter(|path| untracked.contains(path)) {
        let target = root.join(path);
        if target.is_file() {
            std::fs::remove_file(&target)
                .map_err(|error| Error::git(format!("Could not delete {path}: {error}")))?;
        }
        removed += 1;
    }

    if !tracked.is_empty() {
        // `git checkout -- <path>` restores from the index, not from HEAD: a staged change
        // survives discarding the edits made on top of it.
        let mut builder = git2::build::CheckoutBuilder::new();
        builder.force();
        for path in &tracked {
            builder.path(path.as_str());
        }
        repo.checkout_index(None, Some(&mut builder))?;
    }

    let tail = if removed > 0 { format!(", {removed} deleted.") } else { ".".to_string() };
    Ok(format!("Discarded {}{tail}", plural(paths.len(), "file")))
}

/// Append paths to .gitignore, and drop the tracked ones from the index.
pub fn ignore(repo: &Repository, paths: &[String]) -> Result<String> {
    validate::require_paths(paths)?;
    let target = workdir(repo)?.join(".gitignore");
    let existing: Vec<String> = std::fs::read_to_string(&target)
        .unwrap_or_default()
        .lines()
        .map(str::to_string)
        .collect();

    let added: Vec<String> = paths
        .iter()
        .map(|path| format!("/{path}"))
        .filter(|line| !existing.contains(line))
        .filter(|line| !existing.contains(&line[1..].to_string()))
        .collect();
    if added.is_empty() {
        return Ok("Already ignored.".to_string());
    }

    let body = existing.iter().chain(added.iter()).cloned().collect::<Vec<_>>().join("\n");
    std::fs::write(&target, format!("{}\n", body.trim_end_matches('\n')))
        .map_err(|error| Error::git(format!("Could not write .gitignore: {error}")))?;

    let mut index = repo.index()?;
    let tracked: Vec<&String> =
        paths.iter().filter(|path| index.get_path(Path::new(path), 0).is_some()).collect();
    if tracked.is_empty() {
        return Ok(format!("Ignored {} in .gitignore.", plural(added.len(), "path")));
    }
    for path in &tracked {
        index.remove_path(Path::new(path.as_str()))?;
    }
    index.write()?;
    Ok(format!(
        "Ignored {} in .gitignore. {} were tracked: their removal from the index is staged, \
         and takes effect when you commit it.",
        plural(added.len(), "path"),
        tracked.len()
    ))
}

/// Stage, unstage, or discard one hunk.
pub fn apply_patch(repo: &Repository, patch: &str, target: &str) -> Result<String> {
    let (location, reversed, done) = match target {
        "stage" => (git2::ApplyLocation::Index, false, "Hunk staged."),
        "unstage" => (git2::ApplyLocation::Index, true, "Hunk unstaged."),
        "discard" => (git2::ApplyLocation::WorkDir, true, "Hunk discarded."),
        _ => return Err(Error::git(format!("Unknown patch target: {target}"))),
    };

    let problems = diffs::validate(patch);
    if !problems.is_empty() {
        return Err(Error::git(problems.join("; ")));
    }

    let normalized = diffs::normalize(patch);
    // libgit2 has no `--reverse`, so undoing a hunk means applying its mirror image.
    let text = if reversed { diffs::reverse(&normalized) } else { normalized };
    let diff = git2::Diff::from_buffer(text.as_bytes())
        .map_err(|error| Error::git(format!("git cannot read this hunk: {}", error.message())))?;

    repo.apply(&diff, location, None).map_err(|error| {
        Error::git(format!(
            "git refuses this hunk: {}. The file changed since the diff was displayed — \
             reload it and try again.",
            error.message()
        ))
    })?;
    Ok(done.to_string())
}

/// Settle a conflict by keeping one side whole, then staging it as resolved.
pub fn resolve(repo: &Repository, paths: &[String], side: &str) -> Result<String> {
    validate::require_paths(paths)?;
    if !matches!(side, "ours" | "theirs") {
        return Err(Error::git("A conflict is resolved with 'ours' or 'theirs'."));
    }
    let root = workdir(repo)?.to_path_buf();
    let mut index = repo.index()?;

    let mut wanted: Vec<(String, git2::IndexEntry)> = Vec::new();
    for conflict in index.conflicts()?.flatten() {
        let entry = if side == "ours" { conflict.our } else { conflict.their };
        let Some(entry) = entry else { continue };
        let path = String::from_utf8_lossy(&entry.path).into_owned();
        if paths.contains(&path) {
            wanted.push((path, entry));
        }
    }

    if let Some(unknown) = paths.iter().find(|path| !wanted.iter().any(|(name, _)| name == *path)) {
        return Err(Error::git(format!("{unknown} is not in conflict.")));
    }

    for (path, entry) in &wanted {
        let blob = repo.find_blob(entry.id)?;
        std::fs::write(root.join(path), blob.content())
            .map_err(|error| Error::git(format!("Could not write {path}: {error}")))?;
    }
    for (path, _) in &wanted {
        // Removing the path clears all three conflict stages before the resolution is staged.
        index.remove_path(Path::new(path))?;
        index.add_path(Path::new(path))?;
    }
    index.write()?;

    let kept = if side == "ours" { "your side" } else { "the incoming side" };
    Ok(format!("Kept {kept} for {}, staged as resolved.", plural(paths.len(), "file")))
}

// ------------------------------------------------------------------ branches

pub fn checkout(repo: &Repository, branch: &str) -> Result<String> {
    let name = validate::require_branch(branch)?;
    let found = repo
        .find_branch(&name, git2::BranchType::Local)
        .map_err(|_| Error::git(format!("There is no branch called {name}.")))?;
    let target = found.get().peel(git2::ObjectType::Commit)?;

    repo.checkout_tree(&target, None)
        .map_err(|error| Error::git(format!("Could not switch branch: {}", error.message())))?;
    repo.set_head(&format!("refs/heads/{name}"))?;
    Ok(format!("Switched to {name}."))
}

pub fn create_branch(repo: &Repository, name: &str) -> Result<String> {
    let name = validate::require_branch(name)?;
    let head = repo
        .head()
        .and_then(|head| head.peel_to_commit())
        .map_err(|_| Error::git("There is no commit to branch from yet."))?;
    repo.branch(&name, &head, false)
        .map_err(|error| Error::git(format!("Could not create the branch: {}", error.message())))?;
    checkout(repo, &name)?;
    Ok(format!("Created and switched to {name}."))
}

pub fn delete_branch(repo: &Repository, name: &str, force: bool) -> Result<String> {
    let name = validate::require_branch(name)?;
    if crate::gitlog::current_branch(repo) == name {
        return Err(Error::git("You cannot delete the branch you are on. Switch first."));
    }
    let mut found = repo
        .find_branch(&name, git2::BranchType::Local)
        .map_err(|_| Error::git(format!("There is no branch called {name}.")))?;

    if !force {
        // `git branch -d` refuses to drop work HEAD has never seen; libgit2 does not check.
        let tip = found.get().target();
        let head = repo.head().ok().and_then(|head| head.target());
        if let Some((tip, head)) = tip.zip(head) {
            let ahead = repo.graph_ahead_behind(tip, head).map(|(ahead, _)| ahead).unwrap_or(0);
            if ahead > 0 {
                return Err(Error::git(format!(
                    "{name} is not fully merged. Deleting it would lose those commits."
                )));
            }
        }
    }
    found
        .delete()
        .map_err(|error| Error::git(format!("Could not delete {name}: {}", error.message())))?;
    Ok(format!("Deleted {name}."))
}

// ------------------------------------------------------------------ stashes

/// `stash@{3}` → 3. Anything else never reaches git.
fn stash_index(reference: &str) -> Result<usize> {
    reference
        .strip_prefix("stash@{")
        .and_then(|rest| rest.strip_suffix('}'))
        .filter(|digits| (1..=4).contains(&digits.len()))
        .and_then(|digits| digits.parse().ok())
        .ok_or_else(|| Error::git("Not a stash reference."))
}

pub fn stash_pop(repo: &mut Repository, reference: &str) -> Result<String> {
    let index = stash_index(reference)?;
    repo.stash_pop(index, None)
        .map_err(|error| Error::git(format!("Could not restore {reference}: {}", error.message())))?;
    Ok(format!("Restored {reference}."))
}

/// Restore a stash and keep it in the list, which is what tells apply from pop.
pub fn stash_apply(repo: &mut Repository, reference: &str) -> Result<String> {
    let index = stash_index(reference)?;
    repo.stash_apply(index, None)
        .map_err(|error| Error::git(format!("Could not apply {reference}: {}", error.message())))?;
    Ok(format!("Applied {reference}, and kept it in the list."))
}

pub fn stash_drop(repo: &mut Repository, reference: &str) -> Result<String> {
    let index = stash_index(reference)?;
    repo.stash_drop(index)
        .map_err(|error| Error::git(format!("Could not drop the stash: {}", error.message())))?;
    Ok(format!("Dropped {reference}."))
}

// ------------------------------------------------------------------ what only `git` can write

use crate::git_cli::{self, QUICK};

pub const MAX_MESSAGE_LEN: usize = 4000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CommitResult {
    pub sha: String,
    pub short: String,
    pub message: String,
    pub amend: bool,
}

/// Committing goes through `git`: libgit2 would refuse an identity with an empty email, and it
/// runs no hooks at all — a client that skips your pre-commit hook is lying to you.
pub fn commit(repo: &Repository, message: &str, amend: bool) -> Result<CommitResult> {
    let message = message.trim();
    if message.is_empty() {
        return Err(Error::git("A commit message is required."));
    }
    if message.chars().count() > MAX_MESSAGE_LEN {
        return Err(Error::git(format!(
            "The commit message must be at most {MAX_MESSAGE_LEN} characters."
        )));
    }
    if amend && repo.head().and_then(|head| head.peel_to_commit()).is_err() {
        return Err(Error::git("There is no commit to amend yet."));
    }
    if !amend && !status(repo).iter().any(|entry| entry.staged) {
        return Err(Error::git("Nothing is staged. Stage a file before committing."));
    }

    let mut args = vec!["commit"];
    if amend {
        args.push("--amend");
    }
    args.extend_from_slice(&["-m", message]);

    let result = git_cli::run(repo, &args, QUICK)?;
    if !result.ok() {
        let detail = result.both();
        if detail.contains("user.email") || detail.contains("user.name") {
            return Err(Error::git(
                "git has no identity configured for this repository. Run `git config user.name` \
                 and `git config user.email` first.",
            ));
        }
        return Err(Error::git(format!("Commit refused: {detail}")));
    }

    let sha = git_cli::run(repo, &["rev-parse", "HEAD"], QUICK)?.stdout.trim().to_string();
    Ok(CommitResult {
        short: sha.chars().take(7).collect(),
        sha,
        message: message.lines().next().unwrap_or("").to_string(),
        amend,
    })
}

pub fn merge(repo: &Repository, branch: &str, squash: bool) -> Result<String> {
    let branch = validate::require_branch(branch)?;
    let mode = if squash { "--squash" } else { "--no-ff" };
    let result = git_cli::run(repo, &["merge", mode, &branch], QUICK)?;
    if !result.ok() {
        let text = result.both();
        if text.contains("CONFLICT") {
            return Err(Error::git(format!(
                "Merging {branch} produced conflicts. Resolve them in the working tree, then \
                 commit — or abort the merge."
            )));
        }
        return Err(Error::git(format!("Merge failed: {text}")));
    }
    if squash {
        return Ok(format!("Squashed {branch} into the index. Commit it to finish."));
    }
    Ok(format!("Merged {branch}."))
}

pub fn stash_save(repo: &Repository, message: &str) -> Result<String> {
    if status(repo).is_empty() {
        return Err(Error::git("Nothing to stash — the working tree is clean."));
    }
    let message: String = message.trim().chars().take(MAX_MESSAGE_LEN).collect();
    let mut args = vec!["stash", "push", "--include-untracked"];
    if !message.is_empty() {
        args.extend_from_slice(&["-m", &message]);
    }
    git_cli::checked(repo, &args, "Could not stash", QUICK)?;
    Ok("Stashed the working tree.".to_string())
}

pub fn stash_branch(repo: &Repository, reference: &str, name: &str) -> Result<String> {
    stash_index(reference)?;
    let name = validate::require_branch(name)?;
    git_cli::checked(
        repo,
        &["stash", "branch", &name, reference],
        "Could not branch from the stash",
        QUICK,
    )?;
    Ok(format!("Created {name} from {reference}."))
}

pub fn fetch(repo: &Repository) -> Result<String> {
    git_cli::remote_command(repo, &["fetch", "--all", "--prune"], "Fetch")
}

pub fn pull(repo: &Repository) -> Result<String> {
    git_cli::remote_command(repo, &["pull", "--ff-only"], "Pull")
}

pub fn push(repo: &Repository, force: bool) -> Result<String> {
    let mut args = vec!["push".to_string()];
    if force {
        // --force-with-lease refuses to overwrite work the remote gained since the last fetch.
        args.push("--force-with-lease".to_string());
    }
    if tracking(repo).upstream.is_none() {
        let branch = crate::gitlog::current_branch(repo);
        args.push("--set-upstream".to_string());
        args.push(default_remote(repo)?);
        args.push(branch);
    }
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    git_cli::remote_command(repo, &borrowed, if force { "Force push" } else { "Push" })
}
