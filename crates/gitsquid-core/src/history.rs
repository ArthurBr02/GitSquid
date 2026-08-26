//! Operations that move HEAD: what a graph client offers on a right click.

use std::path::Path;

use git2::{Repository, ResetType};

use crate::gitlog::current_branch;
use crate::validate::{require_branch, require_paths, require_sha};
use crate::{Error, Result};

fn commit_at<'a>(repo: &'a Repository, sha: &str) -> Result<git2::Commit<'a>> {
    let sha = require_sha(sha)?;
    repo.revparse_single(&sha)
        .and_then(|object| object.peel_to_commit())
        .map_err(|_| Error::git("No such commit."))
}

pub fn checkout_commit(repo: &Repository, sha: &str) -> Result<String> {
    let commit = commit_at(repo, sha)?;
    repo.checkout_tree(commit.as_object(), None).map_err(|error| {
        Error::git(format!("Could not check out that commit: {}", error.message()))
    })?;
    repo.set_head_detached(commit.id())?;
    Ok(format!(
        "HEAD is now detached at {}. Create a branch to keep work here.",
        &commit.id().to_string()[..7]
    ))
}

pub fn branch_from(repo: &Repository, sha: &str, name: &str) -> Result<String> {
    let commit = commit_at(repo, sha)?;
    let name = require_branch(name)?;
    repo.branch(&name, &commit, false)
        .map_err(|error| Error::git(format!("Could not create the branch: {}", error.message())))?;
    crate::worktree::checkout(repo, &name)?;
    Ok(format!("Created {name} at {}.", &commit.id().to_string()[..7]))
}

/// Bring one file back as it was at a commit. It lands staged, like `git checkout` leaves it.
pub fn restore_file(repo: &Repository, sha: &str, path: &str) -> Result<String> {
    let commit = commit_at(repo, sha)?;
    require_paths(std::slice::from_ref(&path.to_string()))?;

    let tree = commit.tree()?;
    // A pathspec matching nothing checks out silently, so the absence is caught here instead.
    tree.get_path(Path::new(path))
        .map_err(|_| Error::git(format!("Could not restore that version: {path} is not in that commit.")))?;

    let mut builder = git2::build::CheckoutBuilder::new();
    builder.force().update_index(true).path(path);
    repo.checkout_tree(commit.as_object(), Some(&mut builder)).map_err(|error| {
        Error::git(format!("Could not restore that version: {}", error.message()))
    })?;
    Ok(format!("Restored {path} as it was at {}, staged.", &commit.id().to_string()[..7]))
}

pub fn reset(repo: &Repository, sha: &str, mode: &str) -> Result<String> {
    let kind = match mode {
        "soft" => ResetType::Soft,
        "mixed" => ResetType::Mixed,
        "hard" => ResetType::Hard,
        _ => return Err(Error::git("Reset mode must be one of hard, mixed, soft.")),
    };
    let commit = commit_at(repo, sha)?;
    repo.reset(commit.as_object(), kind, None)
        .map_err(|error| Error::git(format!("Could not reset: {}", error.message())))?;

    let kept = match mode {
        "soft" => "the working tree and the index are untouched",
        "mixed" => "the working tree is untouched, the index was cleared",
        _ => "the working tree was overwritten",
    };
    Ok(format!(
        "{} now points at {} — {kept}. What came after is out of the graph; git keeps it in \
         the reflog for a while.",
        current_branch(repo),
        &commit.id().to_string()[..7]
    ))
}

// ------------------------------------------------------------------ replaying commits

use crate::git_cli::{self, REPLAY};
use crate::gitlog::pending_operation;
use crate::phrasing::conflicts;

/// git opens an editor for these unless told otherwise, and there is no terminal to open it in.
const NO_EDITOR: [&str; 2] = ["-c", "core.editor=true"];

fn conflict_guard(result: &git_cli::Output, action: &str) -> Result<String> {
    let text = result.both();
    if result.ok() {
        return Ok(text);
    }
    if text.to_lowercase().contains("conflict") {
        return Err(Error::git(format!(
            "{action} stopped on a conflict. Resolve the files, then continue — or abort it."
        )));
    }
    Err(Error::git(format!(
        "{action} failed: {}",
        if text.is_empty() { "unknown error" } else { &text }
    )))
}

pub fn cherry_pick(repo: &Repository, sha: &str) -> Result<String> {
    let sha = require_sha(sha)?;
    let result = git_cli::run(repo, &[NO_EDITOR[0], NO_EDITOR[1], "cherry-pick", &sha], REPLAY)?;
    conflict_guard(&result, "Cherry-pick")?;
    Ok(format!("Cherry-picked {} onto {}.", &sha[..7.min(sha.len())], current_branch(repo)))
}

/// A new commit that undoes an old one — history is added to, never rewritten.
pub fn revert_commit(repo: &Repository, sha: &str) -> Result<String> {
    let sha = require_sha(sha)?;
    let result = git_cli::run(repo, &["revert", "--no-edit", &sha], REPLAY)?;
    conflict_guard(&result, "Revert")?;
    Ok(format!("Reverted {} in a new commit.", &sha[..7.min(sha.len())]))
}

pub fn rebase(repo: &Repository, target: &str) -> Result<String> {
    let target = crate::validate::require_commit_ref(target)?;
    let branch = current_branch(repo);
    let result = git_cli::run(repo, &[NO_EDITOR[0], NO_EDITOR[1], "rebase", &target], REPLAY)?;
    conflict_guard(&result, "Rebase")?;
    Ok(format!("Replayed {branch} onto {target}."))
}

pub fn abort(repo: &Repository) -> Result<String> {
    let Some(pending) = pending_operation(repo) else {
        return Err(Error::git(
            "Nothing to abort — no merge, rebase, cherry-pick or revert is in progress.",
        ));
    };
    git_cli::checked(
        repo,
        &[&pending.kind, "--abort"],
        &format!("Could not abort the {}", pending.kind),
        REPLAY,
    )?;
    Ok(format!("Aborted the {}. The repository is back where it started.", pending.kind))
}

const SKIPPABLE: [&str; 3] = ["rebase", "cherry-pick", "revert"];

pub fn skip(repo: &Repository) -> Result<String> {
    let pending = pending_operation(repo).filter(|op| SKIPPABLE.contains(&op.kind.as_str()));
    let Some(pending) = pending else { return Err(Error::git("Nothing to skip.")) };

    let result = git_cli::run(repo, &[NO_EDITOR[0], NO_EDITOR[1], &pending.kind, "--skip"], REPLAY)?;
    conflict_guard(&result, &capitalised(&pending.kind))?;
    Ok(format!("Skipped that commit and continued the {}.", pending.kind))
}

pub fn resume(repo: &Repository) -> Result<String> {
    let Some(pending) = pending_operation(repo) else {
        return Err(Error::git("Nothing to continue."));
    };
    if pending.kind == "bisect" {
        return Err(Error::git("Nothing to continue."));
    }
    if !pending.conflicts.is_empty() {
        return Err(Error::git(format!(
            "{}. Resolve and stage them first.",
            conflicts(pending.conflicts.len())
        )));
    }
    let result =
        git_cli::run(repo, &[NO_EDITOR[0], NO_EDITOR[1], &pending.kind, "--continue"], REPLAY)?;
    conflict_guard(&result, &capitalised(&pending.kind))?;
    Ok(format!("Continued the {}.", pending.kind))
}

fn capitalised(kind: &str) -> String {
    let mut chars = kind.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}
