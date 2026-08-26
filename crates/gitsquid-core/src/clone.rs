//! Getting a repository in the first place. Everything else in GitSquid needs one.

use std::path::{Path, PathBuf};

use crate::git_cli::{run_in, CLONE};
use crate::refs::looks_like_a_remote_url;
use crate::{Error, Result};

/// `git@host:group/project.git` → `project`, which is where git would put it too.
pub fn repository_name(url: &str) -> String {
    let tail = url.trim_end_matches('/');
    let tail = tail.rsplit('/').next().unwrap_or(tail);
    let tail = tail.rsplit(':').next().unwrap_or(tail);
    match tail.strip_suffix(".git").unwrap_or(tail) {
        "" => "repository".to_string(),
        name => name.to_string(),
    }
}

fn is_folder_name(name: &str) -> bool {
    (1..=100).contains(&name.chars().count())
        && name.chars().all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-'))
        && name != "."
        && name != ".."
}

/// Clone into `parent/name`. The directory must not already exist.
pub fn clone(url: &str, parent: &Path, name: &str) -> Result<PathBuf> {
    let url = url.trim();
    if !looks_like_a_remote_url(url) {
        return Err(Error::git(
            "That does not look like a repository: use https://, ssh:// or user@host:path.",
        ));
    }
    let name = match name.trim() {
        "" => repository_name(url),
        given => given.to_string(),
    };
    if !is_folder_name(&name) {
        return Err(Error::git("A folder name is letters, digits, dot, dash or underscore."));
    }
    if !parent.is_dir() {
        return Err(Error::git(format!("{} is not a directory.", parent.display())));
    }
    let target = parent.join(&name);
    if target.exists() {
        return Err(Error::git(format!(
            "{} already exists. Choose another name, or open it.",
            target.display()
        )));
    }

    let target_text = target.to_string_lossy().into_owned();
    let result = run_in(parent, &["clone", "--", url, &target_text], None, CLONE)?;
    if !result.ok() {
        let text = result.both();
        if text.contains("could not read Username")
            || text.contains("Authentication failed")
            || text.contains("Permission denied")
        {
            return Err(Error::git(
                "Cloning needs credentials git could not supply without a prompt. Configure a \
                 credential helper or an SSH key, then try again.",
            ));
        }
        return Err(Error::git(format!(
            "Clone failed: {}",
            if text.is_empty() { "unknown error" } else { &text }
        )));
    }
    Ok(target)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_name_is_taken_from_the_url_the_way_git_takes_it() {
        assert_eq!(repository_name("https://example.invalid/depot.git"), "depot");
        assert_eq!(repository_name("git@example.invalid:group/projet.git"), "projet");
        assert_eq!(repository_name("git@example.invalid:projet.git"), "projet");
        assert_eq!(repository_name("https://example.invalid/depot/"), "depot");
        assert_eq!(repository_name("https://example.invalid/depot"), "depot");
    }

    #[test]
    fn an_unnameable_url_falls_back() {
        assert_eq!(repository_name("/"), "repository");
        assert_eq!(repository_name(".git"), "repository");
    }

    #[test]
    fn folder_names_stay_inside_the_parent() {
        assert!(is_folder_name("depot"));
        assert!(is_folder_name("mon-depot_2.0"));
        for bad in ["", ".", "..", "a/b", "a b", "../escape", &"x".repeat(101)] {
            assert!(!is_folder_name(bad), "{bad:?} should be refused");
        }
    }
}
