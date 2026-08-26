//! The list of repositories the interface can switch between, kept in one JSON file.

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::repo::find_repo_root;
use crate::Result;

pub const MAX_REPOS: usize = 50;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct KnownRepo {
    pub path: PathBuf,
    pub name: String,
    pub exists: bool,
}

impl KnownRepo {
    fn at(path: PathBuf) -> Self {
        let name = path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_default();
        let exists = path.join(".git").exists();
        Self { path, name, exists }
    }
}

/// Where the list lives. The directory is injected rather than read from the environment,
/// so a test never writes into the list you actually use.
#[derive(Debug, Clone)]
pub struct Registry {
    dir: PathBuf,
}

impl Registry {
    /// `base` is the configuration root; the list sits in its `gitsquid` subdirectory.
    pub fn under(base: impl AsRef<Path>) -> Self {
        Self { dir: base.as_ref().join("gitsquid") }
    }

    pub fn from_env() -> Self {
        Self::under(base_dir())
    }

    pub fn path(&self) -> PathBuf {
        self.dir.join("repos.json")
    }

    /// The list written before the rename is read until the new one exists.
    fn source(&self) -> PathBuf {
        let current = self.path();
        let legacy = self.dir.parent().map(|parent| parent.join("gitia").join("repos.json"));
        match legacy {
            Some(legacy) if !current.exists() && legacy.exists() => legacy,
            _ => current,
        }
    }

    fn read(&self) -> Vec<PathBuf> {
        let Ok(text) = std::fs::read_to_string(self.source()) else {
            return Vec::new();
        };
        let Ok(payload) = serde_json::from_str::<serde_json::Value>(&text) else {
            return Vec::new();
        };
        let Some(entries) = payload.get("repos").and_then(|repos| repos.as_array()) else {
            return Vec::new();
        };
        let mut paths: Vec<PathBuf> = Vec::new();
        for entry in entries.iter().take(MAX_REPOS) {
            let Some(raw) = entry.as_str() else { continue };
            if raw.trim().is_empty() {
                continue;
            }
            let candidate = PathBuf::from(raw);
            if !paths.contains(&candidate) {
                paths.push(candidate);
            }
        }
        paths
    }

    fn write(&self, paths: &[PathBuf]) -> std::io::Result<()> {
        std::fs::create_dir_all(&self.dir)?;
        let kept: Vec<String> = paths
            .iter()
            .take(MAX_REPOS)
            .map(|path| path.to_string_lossy().into_owned())
            .collect();
        let payload = serde_json::json!({ "version": 1, "repos": kept });
        std::fs::write(self.path(), serde_json::to_string_pretty(&payload)?)
    }

    pub fn known(&self) -> Vec<KnownRepo> {
        self.read().into_iter().map(KnownRepo::at).collect()
    }

    /// Register a repository. The path must resolve to the root of a Git repository.
    pub fn add(&self, path: &Path) -> Result<PathBuf> {
        let root = find_repo_root(path)?;
        let mut paths = self.read();
        paths.retain(|known| known != &root);
        paths.insert(0, root.clone());
        let _ = self.write(&paths);
        Ok(root)
    }

    /// Forget a repository. Nothing on disk is touched.
    pub fn remove(&self, path: &Path) -> bool {
        let target = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
        let paths = self.read();
        let remaining: Vec<PathBuf> = paths.iter().filter(|known| *known != &target).cloned().collect();
        if remaining.len() == paths.len() {
            return false;
        }
        let _ = self.write(&remaining);
        true
    }
}

fn base_dir() -> PathBuf {
    for name in ["GITSQUID_CONFIG_DIR", "GITIA_CONFIG_DIR", "XDG_CONFIG_HOME"] {
        if let Ok(value) = std::env::var(name) {
            if !value.trim().is_empty() {
                return PathBuf::from(value);
            }
        }
    }
    home_dir().join(".config")
}

fn home_dir() -> PathBuf {
    std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("."))
}
