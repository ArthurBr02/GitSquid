//! The JSON the interface reads. These key names are the contract between the engine and the
//! page: renaming a field here is a breaking change no compiler would catch.

mod common;

use common::{commit_file, git, head_sha, open, Workshop};
use gitsquid_core::{gitlog, worktree};

fn keys(value: &serde_json::Value) -> Vec<String> {
    let mut found: Vec<String> =
        value.as_object().expect("an object").keys().cloned().collect();
    found.sort();
    found
}

fn expect(value: &serde_json::Value, wanted: &[&str]) {
    let mut wanted: Vec<String> = wanted.iter().map(|key| key.to_string()).collect();
    wanted.sort();
    assert_eq!(keys(value), wanted);
}

#[test]
fn a_commit_carries_what_the_graph_draws() {
    let shop = Workshop::new();
    let found = gitlog::commits(&open(&shop.repo("workshop")), 10, true);
    let value = serde_json::to_value(&found[0]).unwrap();
    expect(&value, &["sha", "short", "parents", "author", "date", "subject", "refs"]);
}

#[test]
fn a_commit_detail_carries_what_the_panel_shows() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let detail = gitlog::commit_detail(&open(&root), &head_sha(&root)).unwrap();
    let value = serde_json::to_value(&detail).unwrap();
    expect(&value, &[
        "sha", "short", "parents", "merge", "author", "email", "date", "subject", "refs",
        "body", "files", "added", "removed",
    ]);
    expect(&value["files"][0], &["status", "path", "original", "added", "removed", "binary"]);
}

#[test]
fn a_worktree_entry_carries_what_the_staging_panel_shows() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "changed\n").unwrap();

    let view = worktree::view(&open(&root));
    let value = serde_json::to_value(&view).unwrap();
    expect(&value, &["files", "staged", "unstaged", "conflicted"]);
    expect(&value["files"][0], &[
        "path", "original", "index_code", "work_code", "staged", "unstaged", "untracked",
        "conflicted", "index_label", "work_label", "sensitive", "counts",
    ]);
}

/// `counts` is read as `entry.counts[side]`, so an absent side must be absent, not null.
#[test]
fn an_untouched_side_leaves_its_counts_out_entirely() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "changed\n").unwrap();

    let view = worktree::view(&open(&root));
    let counts = serde_json::to_value(view.files[0].counts).unwrap();
    assert_eq!(keys(&counts), ["unstaged"], "nothing is staged, so no staged pair");
}

/// The one renamed field in the whole payload: `ref` is a reserved word in Rust.
#[test]
fn a_stash_is_keyed_by_ref_not_by_reference() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "travail\n").unwrap();
    git(&root, &["stash", "push", "-q", "-m", "un travail"]);

    let entries = worktree::stash_list(&open(&root));
    let value = serde_json::to_value(&entries[0]).unwrap();
    expect(&value, &["ref", "subject", "age", "sha"]);
    assert_eq!(value["ref"], "stash@{0}");
}

#[test]
fn a_blame_line_carries_what_the_gutter_shows() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "poeme.txt", "un\n", "premier jet");

    let blame = gitlog::blame(&open(&root), "poeme.txt", "").unwrap();
    let value = serde_json::to_value(&blame).unwrap();
    expect(&value, &["path", "rev", "lines", "truncated"]);
    expect(&value["lines"][0], &["sha", "short", "author", "date", "summary", "text"]);
}

#[test]
fn the_sidebar_payloads_keep_their_names() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["tag", "v1.0.0"]);
    let found = open(&root);

    expect(&serde_json::to_value(gitlog::head(&found)).unwrap(),
           &["branch", "sha", "commit", "detached"]);
    expect(&serde_json::to_value(&gitlog::branches(&found)[0]).unwrap(),
           &["name", "sha", "upstream", "ahead", "behind"]);
    expect(&serde_json::to_value(&gitlog::tags(&found)[0]).unwrap(),
           &["name", "sha", "subject"]);
    expect(&serde_json::to_value(gitlog::working_status(&found)).unwrap(),
           &["staged", "unstaged", "untracked"]);
    expect(&serde_json::to_value(worktree::tracking(&found)).unwrap(),
           &["upstream", "ahead", "behind"]);
}

#[test]
fn a_pending_operation_keeps_its_names() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "cote"]);
    commit_file(&root, "calc.py", "COTE = 1\n", "cote");
    git(&root, &["checkout", "-q", "main"]);
    commit_file(&root, "calc.py", "MAIN = 1\n", "main");
    git(&root, &["merge", "cote"]);

    let pending = gitlog::pending_operation(&open(&root)).expect("a merge is in progress");
    expect(&serde_json::to_value(pending).unwrap(), &["kind", "conflicts", "resumable"]);
}

#[test]
fn a_history_entry_and_a_patch_keep_their_names() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let found = open(&root);

    let history = gitlog::file_history(&found, "calc.py", 10).unwrap();
    expect(&serde_json::to_value(&history[0]).unwrap(),
           &["sha", "short", "author", "date", "subject"]);

    let patch = gitlog::commit_patch(&found, &head_sha(&root), None, false, 3).unwrap();
    expect(&serde_json::to_value(patch).unwrap(), &["sha", "path", "diff", "truncated"]);
}
