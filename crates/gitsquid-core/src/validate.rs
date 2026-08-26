//! Bounds and shape checks for everything the interface sends. No repository needed:
//! git2 validates ref names on their own, where the shell had to run `git check-ref-format`.

use git2::{Branch, Remote, Tag};

use crate::safety::is_safe_relative_path;
use crate::{Error, Result};

pub const MAX_REF_LEN: usize = 200;
pub const MAX_PATHS: usize = 500;

pub fn require_paths(paths: &[String]) -> Result<()> {
    if paths.is_empty() {
        return Err(Error::git("No file selected."));
    }
    if paths.len() > MAX_PATHS {
        return Err(Error::git("Too many files in one operation."));
    }
    for path in paths {
        if !is_safe_relative_path(path) {
            return Err(Error::git(format!(
                "Refusing a path outside the repository: {path:?}"
            )));
        }
    }
    Ok(())
}

fn bounded(name: &str, kind: &str) -> Result<String> {
    let name = name.trim();
    if name.is_empty() || name.len() > MAX_REF_LEN || name.starts_with('-') {
        return Err(Error::git(format!(
            "A {kind} name of 1 to {MAX_REF_LEN} characters is required."
        )));
    }
    Ok(name.to_string())
}

pub fn require_branch(name: &str) -> Result<String> {
    let name = bounded(name, "branch")?;
    if !Branch::name_is_valid(&name).unwrap_or(false) {
        return Err(Error::git(format!("{name:?} is not a valid branch name.")));
    }
    Ok(name)
}

pub fn require_tag(name: &str) -> Result<String> {
    let name = bounded(name, "tag")?;
    if !Tag::is_valid_name(&name) {
        return Err(Error::git(format!("{name:?} is not a valid tag name.")));
    }
    Ok(name)
}

pub fn require_remote(name: &str) -> Result<String> {
    let name = name.trim();
    // libgit2 accepts `-x`, having no command line to protect; the network operations do shell
    // out to `git`, where a leading dash turns the name into an option.
    let shaped = name.len() <= 61
        && name.starts_with(|first: char| first.is_ascii_alphanumeric())
        && name.chars().all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-'));
    if !shaped || !Remote::is_valid_name(name) {
        return Err(Error::git(
            "A remote name is letters, digits, dot, dash or underscore.",
        ));
    }
    Ok(name.to_string())
}

fn looks_like_a_sha(value: &str) -> bool {
    (4..=64).contains(&value.len()) && value.chars().all(|ch| ch.is_ascii_hexdigit())
}

/// Commit ids reach git as arguments, so nothing but hexadecimal is ever accepted.
pub fn require_sha(value: &str) -> Result<String> {
    let sha = value.trim();
    if !looks_like_a_sha(sha) {
        return Err(Error::git("Not a commit id."));
    }
    Ok(sha.to_string())
}

/// A commit id or a branch name — what every history command accepts as a target.
pub fn require_commit_ref(value: &str) -> Result<String> {
    let candidate = value.trim();
    if looks_like_a_sha(candidate) {
        return Ok(candidate.to_string());
    }
    require_branch(candidate)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn owned(paths: &[&str]) -> Vec<String> {
        paths.iter().map(|path| path.to_string()).collect()
    }

    #[test]
    fn a_selection_of_safe_paths_passes() {
        assert!(require_paths(&owned(&["calc.py", "src/main.rs"])).is_ok());
    }

    #[test]
    fn an_empty_selection_is_refused() {
        assert!(require_paths(&[]).is_err());
    }

    #[test]
    fn too_many_paths_are_refused_before_git_sees_them() {
        let many: Vec<String> = (0..MAX_PATHS + 1).map(|n| format!("file{n}.rs")).collect();
        assert!(require_paths(&many).is_err());
    }

    #[test]
    fn one_unsafe_path_rejects_the_whole_selection() {
        assert!(require_paths(&owned(&["calc.py", "../escape.py"])).is_err());
    }

    #[test]
    fn ordinary_branch_names_pass() {
        for name in ["main", "feature/x", "release-1.2"] {
            assert_eq!(require_branch(name).unwrap(), name);
        }
    }

    #[test]
    fn a_branch_name_is_trimmed_first() {
        assert_eq!(require_branch("  main  ").unwrap(), "main");
    }

    #[test]
    fn malformed_branch_names_are_refused() {
        for name in ["", "  ", "--force", "a branch", "feature//x", "x..y", "with~tilde"] {
            assert!(require_branch(name).is_err(), "{name:?} should be refused");
        }
    }

    #[test]
    fn an_overlong_ref_is_refused() {
        assert!(require_branch(&"a".repeat(MAX_REF_LEN + 1)).is_err());
    }

    #[test]
    fn tag_names_follow_the_same_rules() {
        assert_eq!(require_tag("v1.0.0").unwrap(), "v1.0.0");
        assert!(require_tag("a tag").is_err());
        assert!(require_tag("-leading").is_err());
    }

    #[test]
    fn remote_names_are_letters_digits_and_punctuation() {
        assert_eq!(require_remote("origin").unwrap(), "origin");
        assert_eq!(require_remote("up-stream_2.0").unwrap(), "up-stream_2.0");
        assert!(require_remote("a remote").is_err());
        assert!(require_remote("").is_err());
        // A leading dash would reach `git fetch` as an option, not as a name.
        assert!(require_remote("-x").is_err());
        assert!(require_remote("--upload-pack=touch").is_err());
    }

    #[test]
    fn a_commit_id_must_be_hexadecimal() {
        assert_eq!(require_sha("6b21aff").unwrap(), "6b21aff");
        assert_eq!(require_sha(&"a".repeat(40)).unwrap(), "a".repeat(40));
        for value in ["", "abc", "zzzz", "6b21aff; rm -rf /", &"a".repeat(65)] {
            assert!(require_sha(value).is_err(), "{value:?} should be refused");
        }
    }

    #[test]
    fn a_commit_ref_takes_a_sha_or_a_branch() {
        assert_eq!(require_commit_ref("6b21aff").unwrap(), "6b21aff");
        assert_eq!(require_commit_ref("main").unwrap(), "main");
        assert!(require_commit_ref("--upstream").is_err());
    }

    #[test]
    fn a_short_hex_word_is_read_as_a_sha_not_a_branch() {
        // "beef" is valid as both; the sha reading wins, as it does in the shell version.
        assert_eq!(require_commit_ref("beef").unwrap(), "beef");
    }
}
