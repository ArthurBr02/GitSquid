//! Named refs: tags, and the branches that live on a remote.

use std::sync::LazyLock;

use git2::Repository;
use regex::Regex;

use crate::validate::{require_branch, require_remote, require_sha, require_tag};
use crate::worktree::{checkout, remotes};
use crate::{Error, Result};

static REMOTE_URL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:(?:https?|git|ssh)://\S+|[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:\S+|/\S+)$")
        .expect("a compiled remote-url pattern")
});

/// A tag with no message is just a ref: no object, no tagger, nothing for libgit2 to refuse.
pub fn create_lightweight_tag(repo: &Repository, name: &str, sha: &str) -> Result<String> {
    let name = require_tag(name)?;
    let target = match sha.trim() {
        "" => repo
            .head()
            .and_then(|head| head.peel(git2::ObjectType::Commit))
            .map_err(|_| Error::git("There is no commit to tag yet."))?,
        sha => {
            let sha = require_sha(sha)?;
            repo.revparse_single(&sha).map_err(|_| Error::git("No such commit."))?
        }
    };
    repo.tag_lightweight(&name, &target, false)
        .map_err(|error| Error::git(format!("Could not create the tag: {}", error.message())))?;

    let at = if sha.trim().is_empty() { "HEAD".to_string() } else { sha[..7.min(sha.len())].to_string() };
    Ok(format!("Tagged {at} as {name}."))
}

pub fn delete_tag(repo: &Repository, name: &str) -> Result<String> {
    let name = require_tag(name)?;
    repo.tag_delete(&name)
        .map_err(|error| Error::git(format!("Could not delete the tag: {}", error.message())))?;
    Ok(format!("Deleted the tag {name}."))
}

pub fn rename_branch(repo: &Repository, name: &str, new_name: &str) -> Result<String> {
    let name = require_branch(name)?;
    let new_name = require_branch(new_name)?;
    let mut found = repo
        .find_branch(&name, git2::BranchType::Local)
        .map_err(|_| Error::git(format!("There is no branch called {name}.")))?;
    found
        .rename(&new_name, false)
        .map_err(|error| Error::git(format!("Could not rename the branch: {}", error.message())))?;
    Ok(format!("Renamed {name} to {new_name}."))
}

/// `origin/feature/x` → the remote and the branch name it carries.
fn split_remote(repo: &Repository, name: &str) -> Result<(String, String)> {
    let unknown = || Error::git(format!("{name:?} is not a remote branch."));
    let (remote, branch) = name.split_once('/').ok_or_else(unknown)?;
    if branch.is_empty() || !remotes(repo).iter().any(|entry| entry.name == remote) {
        return Err(unknown());
    }
    Ok((remote.to_string(), require_branch(branch)?))
}

/// Check out a remote branch: create the local twin the first time, switch to it after.
pub fn track_remote_branch(repo: &Repository, name: &str) -> Result<String> {
    let (remote, branch) = split_remote(repo, name)?;
    if repo.find_branch(&branch, git2::BranchType::Local).is_ok() {
        checkout(repo, &branch)?;
        return Ok(format!("Switched to {branch}, which already tracks {remote}."));
    }

    let tip = repo
        .find_branch(&format!("{remote}/{branch}"), git2::BranchType::Remote)
        .and_then(|found| found.get().peel_to_commit())
        .map_err(|_| Error::git(format!("{name:?} is not a remote branch.")))?;

    let mut created = repo.branch(&branch, &tip, false).map_err(|error| {
        Error::git(format!("Could not check out the remote branch: {}", error.message()))
    })?;
    created.set_upstream(Some(&format!("{remote}/{branch}")))?;
    checkout(repo, &branch)?;
    Ok(format!("Created {branch} tracking {remote}/{branch}."))
}

/// Give the repository somewhere to push to. Nothing is fetched until you ask.
pub fn add_remote(repo: &Repository, name: &str, url: &str) -> Result<String> {
    let name = require_remote(name)?;
    let url = url.trim();
    if url.len() > 2000 || !REMOTE_URL.is_match(url) {
        return Err(Error::git(
            "That does not look like a remote: use https://, ssh://, user@host:path or an \
             absolute path.",
        ));
    }
    if remotes(repo).iter().any(|entry| entry.name == name) {
        return Err(Error::git(format!("A remote called {name} already exists.")));
    }
    repo.remote(&name, url)
        .map_err(|error| Error::git(format!("Could not add the remote: {}", error.message())))?;
    Ok(format!("Added {name} → {url}. Fetch to see its branches."))
}

/// Forget a remote. Local branches and commits are untouched.
pub fn remove_remote(repo: &Repository, name: &str) -> Result<String> {
    let name = name.trim();
    if !remotes(repo).iter().any(|entry| entry.name == name) {
        return Err(Error::git(format!("There is no remote called {name}.")));
    }
    repo.remote_delete(name)
        .map_err(|error| Error::git(format!("Could not remove the remote: {}", error.message())))?;
    Ok(format!("Removed {name}. Nothing local was touched."))
}

// ------------------------------------------------------------------ what reaches a remote

use crate::git_cli::{self, QUICK};
use crate::worktree::default_remote;

pub const MAX_TAG_MESSAGE: usize = 1000;

/// A tag with a message is an object with a tagger, which libgit2 will not build here; without
/// one it is just a ref, and git2 handles it.
pub fn create_tag(repo: &Repository, name: &str, sha: &str, message: &str) -> Result<String> {
    if message.trim().is_empty() {
        return create_lightweight_tag(repo, name, sha);
    }
    let name = require_tag(name)?;
    let body: String = message.trim().chars().take(MAX_TAG_MESSAGE).collect();
    let mut args = vec!["tag".to_string(), "-a".to_string(), "-m".to_string(), body, name.clone()];
    if !sha.trim().is_empty() {
        args.push(require_sha(sha)?);
    }
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    git_cli::checked(repo, &borrowed, "Could not create the tag", QUICK)?;

    let at = if sha.trim().is_empty() { "HEAD" } else { &sha[..7.min(sha.len())] };
    Ok(format!("Tagged {at} as {name}."))
}

pub fn push_tag(repo: &Repository, name: &str) -> Result<String> {
    let name = require_tag(name)?;
    let remote = default_remote(repo)?;
    git_cli::remote_command(repo, &[ "push", &remote, &format!("refs/tags/{name}")], "Push")?;
    Ok(format!("Pushed {name} to {remote}."))
}

pub fn push_branch(repo: &Repository, name: &str) -> Result<String> {
    let name = require_branch(name)?;
    let remote = default_remote(repo)?;
    git_cli::remote_command(
        repo,
        &["push", "--set-upstream", &remote, &format!("{name}:{name}")],
        "Push",
    )?;
    Ok(format!("Pushed {name} to {remote}."))
}

/// Delete a branch on the remote. Nothing local is touched.
pub fn delete_remote_branch(repo: &Repository, name: &str) -> Result<String> {
    let (remote, branch) = split_remote(repo, name)?;
    git_cli::remote_command(repo, &["push", &remote, "--delete", &branch], "Delete on the remote")?;
    Ok(format!("Deleted {branch} on {remote}."))
}

/// Whether a string is shaped like somewhere git could clone from or push to.
pub fn looks_like_a_remote_url(url: &str) -> bool {
    let url = url.trim();
    url.len() <= 2000 && REMOTE_URL.is_match(url)
}
