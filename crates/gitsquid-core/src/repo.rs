//! Finding the repository a request is about.

use std::path::{Path, PathBuf};

use crate::{Error, Result};

/// The working-tree root containing `start`, which may be the root itself or any depth below it.
pub fn find_repo_root(start: &Path) -> Result<PathBuf> {
    let missing = || {
        Error::config(format!(
            "{} is not inside a Git repository. Run `git init` first, or cd into one.",
            start.display()
        ))
    };
    let found = git2::Repository::discover(start).map_err(|_| missing())?;
    // A bare repository has no working tree, so there is nothing for the interface to show.
    let workdir = found.workdir().ok_or_else(missing)?;
    Ok(workdir.canonicalize().unwrap_or_else(|_| workdir.to_path_buf()))
}
