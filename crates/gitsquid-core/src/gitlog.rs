//! What the graph, the sidebar and the commit panel read. Everything here is read-only.

use std::collections::HashMap;

use git2::{Oid, Repository, RepositoryState, Sort};
use serde::Serialize;

use crate::time::iso8601;
use crate::{Error, Result};

/// `--date-order`: a parent never sits above its child, but two branches interleave by date
/// instead of one being drained before the other starts.
const GRAPH_ORDER: Sort = Sort::TOPOLOGICAL.union(Sort::TIME);

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Commit {
    pub sha: String,
    pub short: String,
    pub parents: Vec<String>,
    pub author: String,
    pub date: String,
    pub subject: String,
    pub refs: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Head {
    pub branch: String,
    pub sha: String,
    pub commit: String,
    pub detached: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BranchInfo {
    pub name: String,
    pub sha: String,
    pub upstream: String,
    pub ahead: usize,
    pub behind: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RemoteBranch {
    pub name: String,
    pub sha: String,
    pub local: String,
    pub tracked: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TagInfo {
    pub name: String,
    pub sha: String,
    pub subject: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
pub struct WorkingStatus {
    pub staged: usize,
    pub unstaged: usize,
    pub untracked: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Pending {
    pub kind: String,
    pub conflicts: Vec<String>,
    pub resumable: bool,
}

// ------------------------------------------------------------------ where HEAD is

pub fn current_branch(repo: &Repository) -> String {
    match repo.head() {
        Ok(head) => head.shorthand().unwrap_or("HEAD").to_string(),
        Err(_) => "(no branch)".to_string(),
    }
}

/// Where HEAD is, and whether it is attached to a branch at all.
pub fn head(repo: &Repository) -> Head {
    let branch = current_branch(repo);
    let commit = repo
        .head()
        .and_then(|head| head.peel_to_commit())
        .map(|commit| commit.id().to_string())
        .unwrap_or_default();
    Head {
        sha: commit.chars().take(9).collect(),
        detached: branch == "HEAD",
        branch,
        commit,
    }
}

/// Newest first, on the timestamp each row carries.
fn newest_first<T>(mut rows: Vec<(i64, T)>) -> Vec<T> {
    rows.sort_by_key(|(when, _)| std::cmp::Reverse(*when));
    rows.into_iter().map(|(_, row)| row).collect()
}

fn short_id(repo: &Repository, oid: Oid) -> String {
    repo.find_object(oid, None)
        .and_then(|object| object.short_id())
        .ok()
        .and_then(|buf| buf.as_str().map(str::to_string))
        .unwrap_or_else(|| oid.to_string().chars().take(7).collect())
}

// ------------------------------------------------------------------ refs

/// What `%D` prints beside a commit: `HEAD -> main`, `origin/main`, `tag: v1.0.0`.
fn decorations(repo: &Repository) -> HashMap<Oid, Vec<String>> {
    let mut found: HashMap<Oid, Vec<String>> = HashMap::new();
    let detached = repo.head_detached().unwrap_or(false);
    let current = if detached {
        None
    } else {
        repo.head().ok().and_then(|head| head.shorthand().map(str::to_string))
    };

    if let Ok(references) = repo.references() {
        for reference in references.flatten() {
            let Some(name) = reference.name() else { continue };
            let Ok(commit) = reference.peel_to_commit() else { continue };
            let label = if let Some(tag) = name.strip_prefix("refs/tags/") {
                format!("tag: {tag}")
            } else if let Some(branch) = name.strip_prefix("refs/heads/") {
                match &current {
                    Some(on) if on == branch => format!("HEAD -> {branch}"),
                    _ => branch.to_string(),
                }
            } else if let Some(remote) = name.strip_prefix("refs/remotes/") {
                remote.to_string()
            } else {
                continue;
            };
            found.entry(commit.id()).or_default().push(label);
        }
    }

    if detached {
        if let Ok(commit) = repo.head().and_then(|head| head.peel_to_commit()) {
            found.entry(commit.id()).or_default().insert(0, "HEAD".to_string());
        }
    }
    found
}

/// Local branches, newest first, each with how far it has drifted from its upstream.
pub fn branches(repo: &Repository) -> Vec<BranchInfo> {
    let Ok(found) = repo.branches(Some(git2::BranchType::Local)) else {
        return Vec::new();
    };
    let mut rows: Vec<(i64, BranchInfo)> = Vec::new();
    for (branch, _) in found.flatten() {
        let Some(name) = branch.name().ok().flatten().map(str::to_string) else { continue };
        let Ok(commit) = branch.get().peel_to_commit() else { continue };

        let upstream = branch.upstream().ok();
        let upstream_name = upstream
            .as_ref()
            .and_then(|up| up.name().ok().flatten())
            .unwrap_or_default()
            .to_string();
        let (ahead, behind) = upstream
            .as_ref()
            .and_then(|up| up.get().target())
            .and_then(|target| repo.graph_ahead_behind(commit.id(), target).ok())
            .unwrap_or((0, 0));

        rows.push((
            commit.time().seconds(),
            BranchInfo {
                name,
                sha: short_id(repo, commit.id()),
                upstream: upstream_name,
                ahead,
                behind,
            },
        ));
    }
    newest_first(rows)
}

/// Remote-tracking branches, minus the symbolic `origin/HEAD`, with their local twin marked.
pub fn remote_branches(repo: &Repository) -> Vec<RemoteBranch> {
    let local: Vec<String> = branches(repo).into_iter().map(|branch| branch.name).collect();
    let Ok(found) = repo.branches(Some(git2::BranchType::Remote)) else {
        return Vec::new();
    };
    let mut rows: Vec<(i64, RemoteBranch)> = Vec::new();
    for (branch, _) in found.flatten() {
        // `origin/HEAD` is a symbolic ref standing for another branch, not a branch of its own.
        if branch.get().symbolic_target().is_some() {
            continue;
        }
        let Some(name) = branch.name().ok().flatten().map(str::to_string) else { continue };
        let Ok(commit) = branch.get().peel_to_commit() else { continue };
        let short = name.split_once('/').map(|(_, rest)| rest).unwrap_or(&name).to_string();
        rows.push((
            commit.time().seconds(),
            RemoteBranch {
                sha: short_id(repo, commit.id()),
                tracked: local.contains(&short),
                local: short,
                name,
            },
        ));
    }
    newest_first(rows)
}

pub fn tags(repo: &Repository) -> Vec<TagInfo> {
    let Ok(names) = repo.tag_names(None) else { return Vec::new() };
    let mut rows: Vec<(i64, TagInfo)> = Vec::new();
    for name in names.iter().flatten() {
        let Ok(reference) = repo.find_reference(&format!("refs/tags/{name}")) else { continue };
        let Ok(commit) = reference.peel_to_commit() else { continue };
        // An annotated tag is its own object, and carries its own message.
        let annotated = reference.peel_to_tag().ok();
        let subject = annotated
            .as_ref()
            .and_then(|tag| tag.message())
            .map(|message| message.lines().next().unwrap_or("").trim().to_string())
            .unwrap_or_else(|| commit.summary().unwrap_or("").to_string());
        let when = annotated
            .as_ref()
            .and_then(|tag| tag.tagger().map(|tagger| tagger.when().seconds()))
            .unwrap_or_else(|| commit.time().seconds());

        rows.push((
            when,
            TagInfo { name: name.to_string(), sha: short_id(repo, commit.id()), subject },
        ));
    }
    newest_first(rows)
}

// ------------------------------------------------------------------ the working tree, counted

pub fn working_status(repo: &Repository) -> WorkingStatus {
    let mut options = git2::StatusOptions::new();
    // Matching `git status --porcelain`: untracked directories collapse to one entry.
    options.include_untracked(true).recurse_untracked_dirs(false);
    let Ok(statuses) = repo.statuses(Some(&mut options)) else {
        return WorkingStatus::default();
    };

    let staged_flags = git2::Status::INDEX_NEW
        | git2::Status::INDEX_MODIFIED
        | git2::Status::INDEX_DELETED
        | git2::Status::INDEX_RENAMED
        | git2::Status::INDEX_TYPECHANGE;
    let unstaged_flags = git2::Status::WT_MODIFIED
        | git2::Status::WT_DELETED
        | git2::Status::WT_RENAMED
        | git2::Status::WT_TYPECHANGE
        | git2::Status::CONFLICTED;

    let mut counts = WorkingStatus::default();
    for entry in statuses.iter() {
        let status = entry.status();
        if status.contains(git2::Status::WT_NEW) && !status.intersects(staged_flags) {
            counts.untracked += 1;
            continue;
        }
        if status.intersects(staged_flags) {
            counts.staged += 1;
        }
        if status.intersects(unstaged_flags) {
            counts.unstaged += 1;
        }
    }
    counts
}

// ------------------------------------------------------------------ the graph

fn every_commit_tip(repo: &Repository, walk: &mut git2::Revwalk, every_ref: bool) -> bool {
    if !every_ref {
        return walk.push_head().is_ok();
    }
    let mut pushed = walk.push_head().is_ok();
    if let Ok(references) = repo.references() {
        for reference in references.flatten() {
            if let Ok(commit) = reference.peel_to_commit() {
                pushed |= walk.push(commit.id()).is_ok();
            }
        }
    }
    pushed
}

pub fn count_commits(repo: &Repository, every_ref: bool) -> usize {
    let Ok(mut walk) = repo.revwalk() else { return 0 };
    if !every_commit_tip(repo, &mut walk, every_ref) {
        return 0;
    }
    walk.filter_map(|step| step.ok()).count()
}

/// Every ref's commits, newest first — work on another branch is newer, not invisible.
pub fn commits(repo: &Repository, limit: usize, every_ref: bool) -> Vec<Commit> {
    let Ok(mut walk) = repo.revwalk() else { return Vec::new() };
    if walk.set_sorting(GRAPH_ORDER).is_err() || !every_commit_tip(repo, &mut walk, every_ref) {
        return Vec::new();
    }
    let labels = decorations(repo);
    walk.filter_map(|step| step.ok())
        .take(limit)
        .filter_map(|oid| repo.find_commit(oid).ok())
        .map(|commit| describe(repo, &commit, &labels))
        .collect()
}

fn describe(repo: &Repository, commit: &git2::Commit, labels: &HashMap<Oid, Vec<String>>) -> Commit {
    let author = commit.author();
    Commit {
        sha: commit.id().to_string(),
        short: short_id(repo, commit.id()),
        parents: commit.parent_ids().map(|id| id.to_string()).collect(),
        author: author.name().unwrap_or("").to_string(),
        date: iso8601(author.when().seconds(), author.when().offset_minutes()),
        subject: commit.summary().unwrap_or("").to_string(),
        refs: labels.get(&commit.id()).cloned().unwrap_or_default(),
    }
}

// ------------------------------------------------------------------ an operation left half-done

/// A merge, rebase, cherry-pick or revert git stopped in the middle of — usually a conflict.
pub fn pending_operation(repo: &Repository) -> Option<Pending> {
    let kind = match repo.state() {
        RepositoryState::Clean => return None,
        RepositoryState::Merge => "merge",
        RepositoryState::Revert | RepositoryState::RevertSequence => "revert",
        RepositoryState::CherryPick | RepositoryState::CherryPickSequence => "cherry-pick",
        RepositoryState::Bisect => "bisect",
        _ => "rebase",
    };

    let mut options = git2::StatusOptions::new();
    options.include_untracked(false);
    let conflicts = repo
        .statuses(Some(&mut options))
        .map(|statuses| {
            statuses
                .iter()
                .filter(|entry| entry.status().contains(git2::Status::CONFLICTED))
                .filter_map(|entry| entry.path().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    Some(Pending { resumable: kind != "bisect", kind: kind.to_string(), conflicts })
}

// ------------------------------------------------------------------ one commit, in detail

pub const MAX_PATCH_CHARS: usize = 400_000;
/// How far back the search and the file history are willing to walk before giving up.
const MAX_SCAN: usize = 20_000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FileChange {
    pub status: String,
    pub path: String,
    pub original: Option<String>,
    pub added: Option<usize>,
    pub removed: Option<usize>,
    pub binary: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CommitDetail {
    pub sha: String,
    pub short: String,
    pub parents: Vec<String>,
    pub merge: bool,
    pub author: String,
    pub email: String,
    pub date: String,
    pub subject: String,
    pub refs: Vec<String>,
    pub body: String,
    pub files: Vec<FileChange>,
    pub added: usize,
    pub removed: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Patch {
    pub sha: String,
    pub path: String,
    pub diff: String,
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct HistoryEntry {
    pub sha: String,
    pub short: String,
    pub author: String,
    pub date: String,
    pub subject: String,
}

/// An annotated tag and a stash are objects too: read the commit they stand for.
fn resolve_commit<'a>(repo: &'a Repository, sha: &str) -> Result<git2::Commit<'a>> {
    let sha = crate::validate::require_sha(sha)?;
    repo.revparse_single(&sha)
        .and_then(|object| object.peel_to_commit())
        .map_err(|_| Error::git("No such commit."))
}

/// A merge shows no diff at all by default; a reader wants what its first parent did not have.
fn against_first_parent<'a>(
    repo: &'a Repository,
    commit: &git2::Commit,
    options: Option<&mut git2::DiffOptions>,
) -> Result<git2::Diff<'a>> {
    let tree = commit.tree()?;
    let parent = commit.parent(0).ok().and_then(|parent| parent.tree().ok());
    let mut diff = repo.diff_tree_to_tree(parent.as_ref(), Some(&tree), options)?;
    // `-M`: without it a rename reads as a deletion plus an unrelated addition.
    diff.find_similar(None)?;
    Ok(diff)
}

fn status_letter(status: git2::Delta) -> &'static str {
    match status {
        git2::Delta::Added | git2::Delta::Untracked => "A",
        git2::Delta::Deleted => "D",
        git2::Delta::Renamed => "R",
        git2::Delta::Copied => "C",
        git2::Delta::Typechange => "T",
        _ => "M",
    }
}

fn path_of(file: &git2::DiffFile) -> String {
    file.path().map(|path| path.to_string_lossy().into_owned()).unwrap_or_default()
}

fn changed_files(diff: &git2::Diff) -> Vec<FileChange> {
    let mut files = Vec::new();
    for (index, delta) in diff.deltas().enumerate() {
        let status = status_letter(delta.status());
        let renamed = matches!(delta.status(), git2::Delta::Renamed | git2::Delta::Copied);
        let patch = git2::Patch::from_diff(diff, index).ok().flatten();
        // libgit2 only marks a delta binary once it has loaded the content, which building the
        // patch is what does — so the flag is read from the patch's own delta, not the iterator's.
        let binary = patch
            .as_ref()
            .is_none_or(|patch| patch.delta().flags().contains(git2::DiffFlags::BINARY));
        // A binary file has no line count, not a count of zero.
        let counts = patch.as_ref().filter(|_| !binary).map(|patch| {
            let (_, added, removed) = patch.line_stats().unwrap_or((0, 0, 0));
            (added, removed)
        });

        files.push(FileChange {
            status: status.to_string(),
            path: path_of(&delta.new_file()),
            original: renamed.then(|| path_of(&delta.old_file())),
            added: counts.map(|(added, _)| added),
            removed: counts.map(|(_, removed)| removed),
            binary,
        });
    }
    files
}

/// Everything the panel shows except the patches, which are fetched one file at a time.
pub fn commit_detail(repo: &Repository, sha: &str) -> Result<CommitDetail> {
    let commit = resolve_commit(repo, sha)?;
    let diff = against_first_parent(repo, &commit, None)?;
    let files = changed_files(&diff);
    let author = commit.author();
    let parents: Vec<String> = commit.parent_ids().map(|id| id.to_string()).collect();

    Ok(CommitDetail {
        sha: commit.id().to_string(),
        short: commit.id().to_string().chars().take(7).collect(),
        merge: parents.len() > 1,
        parents,
        author: author.name().unwrap_or("").to_string(),
        email: author.email().unwrap_or("").to_string(),
        date: iso8601(author.when().seconds(), author.when().offset_minutes()),
        subject: commit.summary().unwrap_or("").to_string(),
        refs: decorations(repo).get(&commit.id()).cloned().unwrap_or_default(),
        body: commit.message().unwrap_or("").trim().to_string(),
        added: files.iter().filter_map(|file| file.added).sum(),
        removed: files.iter().filter_map(|file| file.removed).sum(),
        files,
    })
}

/// The patch of one file in a commit — or of the whole commit when no path is given.
pub fn commit_patch(
    repo: &Repository,
    sha: &str,
    path: Option<&str>,
    ignore_whitespace: bool,
    context: u32,
) -> Result<Patch> {
    let commit = resolve_commit(repo, sha)?;
    let mut options = git2::DiffOptions::new();
    options.context_lines(context.min(100)).ignore_whitespace(ignore_whitespace);
    if let Some(path) = path {
        if !crate::safety::is_safe_relative_path(path) {
            return Err(Error::git("Refusing a path outside the repository."));
        }
        options.pathspec(path);
    }

    let diff = against_first_parent(repo, &commit, Some(&mut options))?;
    let mut text = Vec::new();
    diff.print(git2::DiffFormat::Patch, |_, _, line| {
        // A latin-1 line in a diff must not lose the request that reads it.
        match line.origin() {
            '+' | '-' | ' ' => text.push(line.origin() as u8),
            _ => {}
        }
        text.extend_from_slice(line.content());
        true
    })?;

    let rendered = String::from_utf8_lossy(&text).into_owned();
    let truncated = rendered.chars().count() > MAX_PATCH_CHARS;
    Ok(Patch {
        sha: commit.id().to_string(),
        path: path.unwrap_or("").to_string(),
        diff: rendered.chars().take(MAX_PATCH_CHARS).collect(),
        truncated,
    })
}

// ------------------------------------------------------------------ searching, and one file's past

fn touches(repo: &Repository, commit: &git2::Commit, needle: &str) -> bool {
    let Ok(diff) = against_first_parent(repo, commit, None) else { return false };
    diff.deltas().any(|delta| {
        path_of(&delta.new_file()).contains(needle) || path_of(&delta.old_file()).contains(needle)
    })
}

/// Commits whose message, author or touched paths match — the whole history, not the window.
pub fn search(repo: &Repository, query: &str, limit: usize) -> Vec<Commit> {
    let query = query.trim();
    if query.chars().count() < 2 {
        return Vec::new();
    }
    let lowered = query.to_lowercase();

    let Ok(mut walk) = repo.revwalk() else { return Vec::new() };
    if walk.set_sorting(GRAPH_ORDER).is_err() || !every_commit_tip(repo, &mut walk, true) {
        return Vec::new();
    }
    let labels = decorations(repo);

    let mut found: Vec<Commit> = Vec::new();
    for oid in walk.filter_map(|step| step.ok()).take(MAX_SCAN) {
        let Ok(commit) = repo.find_commit(oid) else { continue };
        let author = commit.author();
        let matched = commit.message().unwrap_or("").to_lowercase().contains(&lowered)
            || author.name().unwrap_or("").to_lowercase().contains(&lowered)
            || author.email().unwrap_or("").to_lowercase().contains(&lowered)
            // A path is matched the way a pathspec is: as written.
            || touches(repo, &commit, query);
        if matched {
            found.push(describe(repo, &commit, &labels));
            if found.len() >= limit {
                break;
            }
        }
    }
    found.sort_by(|left, right| right.date.cmp(&left.date));
    found
}

/// The commits that touched one file, renames followed.
pub fn file_history(repo: &Repository, path: &str, limit: usize) -> Result<Vec<HistoryEntry>> {
    if !crate::safety::is_safe_relative_path(path) {
        return Err(Error::git("Refusing a path outside the repository."));
    }
    let Ok(mut walk) = repo.revwalk() else { return Ok(Vec::new()) };
    if walk.set_sorting(GRAPH_ORDER).is_err() || walk.push_head().is_err() {
        return Ok(Vec::new());
    }

    let mut tracked = path.to_string();
    let mut found = Vec::new();
    for oid in walk.filter_map(|step| step.ok()).take(MAX_SCAN) {
        let Ok(commit) = repo.find_commit(oid) else { continue };
        let Ok(diff) = against_first_parent(repo, &commit, None) else { continue };

        let Some(delta) = diff.deltas().find(|delta| path_of(&delta.new_file()) == tracked) else {
            continue;
        };
        let author = commit.author();
        found.push(HistoryEntry {
            sha: commit.id().to_string(),
            short: commit.id().to_string().chars().take(7).collect(),
            author: author.name().unwrap_or("").to_string(),
            date: iso8601(author.when().seconds(), author.when().offset_minutes()),
            subject: commit.summary().unwrap_or("").to_string(),
        });
        // `--follow`: when the file arrived under another name, keep reading that name.
        if matches!(delta.status(), git2::Delta::Renamed | git2::Delta::Copied) {
            tracked = path_of(&delta.old_file());
        }
        if found.len() >= limit {
            break;
        }
    }
    Ok(found)
}

// ------------------------------------------------------------------ blame

pub const MAX_BLAME_LINES: usize = 8000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BlameLine {
    pub sha: String,
    pub short: String,
    pub author: String,
    pub date: String,
    pub summary: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Blame {
    pub path: String,
    pub rev: String,
    pub lines: Vec<BlameLine>,
    pub truncated: bool,
}

/// git repeats a commit's details once, then only its sha: carry them across the lines.
fn parse_blame(porcelain: &str) -> Vec<BlameLine> {
    let mut known: HashMap<String, (String, String, String)> = HashMap::new();
    let mut lines: Vec<BlameLine> = Vec::new();
    let mut current = String::new();

    for raw in porcelain.split('\n') {
        if let Some(text) = raw.strip_prefix('\t') {
            let (author, date, summary) = known.get(&current).cloned().unwrap_or_default();
            lines.push(BlameLine {
                short: current.chars().take(7).collect(),
                sha: current.clone(),
                author,
                date,
                summary,
                text: text.to_string(),
            });
            if lines.len() >= MAX_BLAME_LINES {
                break;
            }
            continue;
        }
        if raw.is_empty() {
            continue;
        }
        let (head, rest) = raw.split_once(' ').unwrap_or((raw, ""));
        if head.len() == 40 && head.chars().all(|ch| ch.is_ascii_hexdigit()) {
            current = head.to_string();
            known.entry(current.clone()).or_default();
        } else if !current.is_empty() {
            let Some(entry) = known.get_mut(&current) else { continue };
            match head {
                "author" => entry.0 = rest.to_string(),
                // Blame timestamps are normalised to UTC; only the commit panel shows an offset.
                "author-time" => {
                    entry.1 = rest
                        .split(' ')
                        .next()
                        .and_then(|epoch| epoch.parse::<i64>().ok())
                        .map(|epoch| iso8601(epoch, 0))
                        .unwrap_or_default()
                }
                "summary" => entry.2 = rest.to_string(),
                _ => {}
            }
        }
    }
    lines
}

/// Who last touched each line; an empty `rev` means the working tree as it stands.
///
/// Through the CLI, not `blame_file`: libgit2 resolves every author through the mailmap, which
/// rebuilds the signature and rejects the empty emails real histories contain.
pub fn blame(repo: &Repository, path: &str, rev: &str) -> Result<Blame> {
    if !crate::safety::is_safe_relative_path(path) {
        return Err(Error::git("Refusing a path outside the repository."));
    }
    let revision = match rev.trim() {
        "" => String::new(),
        rev => resolve_commit(repo, rev)?.id().to_string(),
    };

    let mut args = vec!["blame", "--porcelain", "-w"];
    if !revision.is_empty() {
        args.push(&revision);
    }
    args.extend_from_slice(&["--", path]);

    let result = crate::git_cli::run(repo, &args, crate::git_cli::REPLAY)?;
    if !result.ok() {
        return Err(Error::git(
            "git cannot attribute this file — it is untracked, binary, or absent at that commit.",
        ));
    }
    let lines = parse_blame(&result.stdout);
    Ok(Blame {
        path: path.to_string(),
        rev: revision,
        truncated: lines.len() >= MAX_BLAME_LINES,
        lines,
    })
}
