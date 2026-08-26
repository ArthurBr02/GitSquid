//! The Git engine behind GitSquid: one repository at a time, read and written through git2.

pub mod clone;
pub mod diffs;
pub mod error;
pub mod git_cli;
pub mod gitlog;
pub mod history;
pub mod phrasing;
pub mod refs;
pub mod registry;
pub mod repo;
pub mod safety;
pub mod time;
pub mod validate;
pub mod worktree;

pub use error::{Error, Result};
