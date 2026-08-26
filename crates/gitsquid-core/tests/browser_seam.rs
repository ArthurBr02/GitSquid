//! The seams no single language can test alone: a patch the page builds in JavaScript and git
//! then applies for real, and a layout compared against the graph git itself draws.
//! Skipped where node is not installed — the Rust engine does not depend on it.

mod common;

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use common::{commit_file, git, open, Workshop};
use gitsquid_core::{gitlog, worktree};

fn bridge() -> Option<PathBuf> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../desktop/tests/bridge.mjs");
    let usable = path.is_file()
        && Command::new("node")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
    usable.then_some(path)
}

fn ask(request: serde_json::Value) -> serde_json::Value {
    let script = bridge().expect("checked by the caller");
    let mut child = Command::new("node")
        .arg(&script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("node runs");
    child
        .stdin
        .as_mut()
        .expect("a stdin pipe")
        .write_all(request.to_string().as_bytes())
        .expect("the request is written");
    let out = child.wait_with_output().expect("node finishes");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    serde_json::from_slice(&out.stdout).expect("the bridge answers with JSON")
}

fn picked_patch(diff: &str, picks: &[String]) -> String {
    let answer = ask(serde_json::json!({ "op": "pick", "diff": diff, "picks": picks }));
    answer["patch"].as_str().unwrap_or_default().to_string()
}

/// The page names a line by its hunk and its place in it; find that key for a given line.
fn key_for(diff: &str, text: &str) -> String {
    let mut hunk: i32 = -1;
    let mut position = 0;
    for line in diff.lines() {
        if line.starts_with("@@") {
            hunk += 1;
            position = 0;
        } else if hunk >= 0 {
            if line == text {
                return format!("{hunk}:{position}");
            }
            position += 1;
        }
    }
    panic!("no such line in the diff: {text:?}");
}

/// A poem edited in two places far enough apart for git to emit two hunks.
fn poem(shop: &Workshop) -> PathBuf {
    let root = shop.repo("workshop");
    let mut lines: Vec<String> = (1..=30).map(|number| format!("ligne {number}\n")).collect();
    commit_file(&root, "poeme.txt", &lines.concat(), "le poeme");

    lines[1] = "ligne deux modifiee\n".to_string();
    lines[24] = "ligne vingt-cinq modifiee\n".to_string();
    std::fs::write(root.join("poeme.txt"), lines.concat()).unwrap();
    root
}

#[test]
fn one_picked_line_is_the_only_thing_staged() {
    let Some(_) = bridge() else { return };
    let shop = Workshop::new();
    let root = poem(&shop);

    let diff = worktree::file_diff(&open(&root), "poeme.txt", false, false, 3).unwrap();
    assert_eq!(diff.lines().filter(|line| line.starts_with("@@")).count(), 2);

    let picked = key_for(&diff, "+ligne deux modifiee");
    let patch = picked_patch(&diff, &[picked]);
    worktree::apply_patch(&open(&root), &patch, "stage").unwrap();

    let staged = worktree::file_diff(&open(&root), "poeme.txt", true, false, 3).unwrap();
    assert!(staged.contains("+ligne deux modifiee"), "{staged}");
    assert!(!staged.contains("vingt-cinq"), "{staged}");
}

#[test]
fn a_picked_line_can_be_discarded_from_the_working_tree() {
    let Some(_) = bridge() else { return };
    let shop = Workshop::new();
    let root = poem(&shop);

    let diff = worktree::file_diff(&open(&root), "poeme.txt", false, false, 3).unwrap();
    let picks = [key_for(&diff, "-ligne 2"), key_for(&diff, "+ligne deux modifiee")];
    let patch = picked_patch(&diff, &picks);
    worktree::apply_patch(&open(&root), &patch, "discard").unwrap();

    let text = std::fs::read_to_string(root.join("poeme.txt")).unwrap();
    assert!(!text.contains("ligne deux modifiee"), "{text}");
    assert!(text.contains("ligne 2\n"), "{text}");
    assert!(text.contains("ligne vingt-cinq modifiee"), "the other edit must survive");
}

#[test]
fn picks_in_two_hunks_land_in_one_patch_git_accepts() {
    let Some(_) = bridge() else { return };
    let shop = Workshop::new();
    let root = poem(&shop);

    let diff = worktree::file_diff(&open(&root), "poeme.txt", false, false, 3).unwrap();
    let picks =
        [key_for(&diff, "+ligne deux modifiee"), key_for(&diff, "+ligne vingt-cinq modifiee")];
    let patch = picked_patch(&diff, &picks);
    worktree::apply_patch(&open(&root), &patch, "stage").unwrap();

    let staged = worktree::file_diff(&open(&root), "poeme.txt", true, false, 3).unwrap();
    assert!(staged.contains("+ligne deux modifiee"), "{staged}");
    assert!(staged.contains("+ligne vingt-cinq modifiee"), "{staged}");
}

/// How wide git's own ASCII graph gets — the reference the page must not exceed.
fn git_graph_width(root: &Path) -> usize {
    let text = git(root, &["log", "--graph", "--oneline", "--topo-order", "-80"]);
    text.lines()
        .filter_map(|line| {
            let drawing: String =
                line.chars().take_while(|ch| " |*/\\_".contains(*ch)).collect();
            (!drawing.trim_end().is_empty()).then(|| drawing.trim_end().len() / 2 + 1)
        })
        .max()
        .unwrap_or(1)
}

#[test]
fn the_page_never_draws_a_wider_graph_than_git_does() {
    let Some(_) = bridge() else { return };
    let shop = Workshop::new();
    let root = shop.branchy("workshop");

    let rows: Vec<serde_json::Value> = gitlog::commits(&open(&root), 80, true)
        .into_iter()
        .map(|commit| serde_json::json!({ "kind": "commit", "sha": commit.sha, "parents": commit.parents }))
        .collect();
    let answer = ask(serde_json::json!({ "op": "layout", "rows": rows }));
    let columns = answer["columns"].as_u64().unwrap_or(u64::MAX) as usize;

    let reference = git_graph_width(&root);
    assert!(columns <= reference, "the page drew {columns} lanes where git draws {reference}");
}
