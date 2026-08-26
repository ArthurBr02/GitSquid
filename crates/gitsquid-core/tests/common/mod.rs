//! The fixtures the Python suite kept in `tests/conftest.py`: a throwaway directory holding
//! real repositories, built with the `git` CLI so nothing under test also builds them.

#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::process::Command;

use gitsquid_core::registry::Registry;

pub const CALC: &str = "\"\"\"Tiny module the tests patch.\"\"\"\n\n\ndef add(a, b):\n    return a + b\n\n\ndef total(values):\n    result = 0\n    for value in values:\n        result = add(result, value)\n    return result\n";

pub fn git(repo: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .expect("git is installed");
    String::from_utf8_lossy(&out.stdout).into_owned()
}

pub struct Workshop {
    dir: tempfile::TempDir,
}

impl Workshop {
    pub fn new() -> Self {
        Self { dir: tempfile::tempdir().expect("a temporary directory") }
    }

    pub fn path(&self) -> PathBuf {
        self.dir.path().canonicalize().expect("a real path")
    }

    /// The repository list, isolated: what a test writes never reaches the one you use.
    pub fn registry(&self) -> Registry {
        Registry::under(self.path().join("config"))
    }

    /// An empty directory that is not a repository.
    pub fn plain(&self, name: &str) -> PathBuf {
        let root = self.path().join(name);
        std::fs::create_dir_all(&root).expect("a directory");
        root
    }

    /// A repository with one commit behind it.
    pub fn repo(&self, name: &str) -> PathBuf {
        let root = self.plain(name);
        git(&root, &["init", "-q", "-b", "main"]);
        git(&root, &["config", "user.email", "tester@example.invalid"]);
        git(&root, &["config", "user.name", "Tester"]);
        std::fs::write(root.join("calc.py"), CALC).expect("calc.py");
        std::fs::write(root.join("README.md"), "# workshop\n\nA repository used by the tests.\n")
            .expect("README.md");
        git(&root, &["add", "-A"]);
        git(&root, &["commit", "-q", "-m", "initial"]);
        root
    }

    /// A repository with no commit yet — the state right after `git init`.
    pub fn empty_repo(&self, name: &str) -> PathBuf {
        let root = self.plain(name);
        git(&root, &["init", "-q", "-b", "main"]);
        root
    }
}

pub fn commit_file(repo: &Path, name: &str, text: &str, message: &str) {
    let target = repo.join(name);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).expect("a parent directory");
    }
    std::fs::write(&target, text).expect("the file is written");
    git(repo, &["add", "-A"]);
    git(repo, &["commit", "-q", "-m", message]);
}

pub fn head_sha(repo: &Path) -> String {
    git(repo, &["rev-parse", "HEAD"]).trim().to_string()
}

pub fn open(repo: &Path) -> git2::Repository {
    git2::Repository::open(repo).expect("the repository opens")
}

impl Workshop {
    /// One commit that is the parent of three branches, then three merges back.
    pub fn branchy(&self, name: &str) -> PathBuf {
        let root = self.repo(name);
        for index in 0..3 {
            git(&root, &["checkout", "-q", "-b", &format!("feature/{index}"), "main"]);
            commit_file(&root, &format!("feature_{index}.py"), &format!("VALUE = {index}\n"), &format!("work {index}"));
        }
        git(&root, &["checkout", "-q", "main"]);
        for index in 0..3 {
            git(&root, &["merge", "-q", "--no-ff", &format!("feature/{index}"), "-m", &format!("merge {index}")]);
        }
        root
    }
}

impl Workshop {
    /// A repository committed the way this project's own history is: an author with no email.
    /// git accepts it; libgit2 refuses to build such a signature, which is why every operation
    /// that writes a commit goes through the CLI.
    pub fn repo_without_email(&self, name: &str) -> PathBuf {
        let root = self.plain(name);
        git(&root, &["init", "-q", "-b", "main"]);
        git(&root, &["config", "user.email", ""]);
        git(&root, &["config", "user.name", "Arthur"]);
        std::fs::write(root.join("calc.py"), CALC).expect("calc.py");
        git(&root, &["add", "-A"]);
        git(&root, &["commit", "-q", "-m", "initial"]);
        root
    }
}

/// The file header, then one text per hunk — what the interface sends back per hunk.
pub fn split_hunks(diff: &str) -> (String, Vec<String>) {
    let mut header: Vec<&str> = Vec::new();
    let mut hunks: Vec<Vec<&str>> = Vec::new();
    for line in diff.lines() {
        if line.starts_with("@@") {
            hunks.push(vec![line]);
        } else if let Some(last) = hunks.last_mut() {
            last.push(line);
        } else {
            header.push(line);
        }
    }
    (
        format!("{}\n", header.join("\n")),
        hunks.into_iter().map(|lines| format!("{}\n", lines.join("\n"))).collect(),
    )
}
