//! Replaying commits: cherry-pick, revert, rebase, and the half-finished states they leave.

mod common;

use common::{commit_file, git, head_sha, open, Workshop};
use gitsquid_core::{gitlog, history};

/// `main` and `side` change the same line, so any replay conflicts.
fn conflicting(shop: &Workshop) -> (std::path::PathBuf, String) {
    let root = shop.repo("workshop");
    commit_file(&root, "shared.py", "VALEUR = 0\n", "shared");
    git(&root, &["checkout", "-q", "-b", "side"]);
    commit_file(&root, "shared.py", "VALEUR = 2\n", "side change");
    let side = head_sha(&root);
    git(&root, &["checkout", "-q", "main"]);
    commit_file(&root, "shared.py", "VALEUR = 1\n", "main change");
    (root, side)
}

#[test]
fn cherry_pick_copies_one_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "side"]);
    commit_file(&root, "picked.py", "VALEUR = 5\n", "work to pick");
    let sha = head_sha(&root);
    git(&root, &["checkout", "-q", "main"]);

    let message = history::cherry_pick(&open(&root), &sha).unwrap();
    assert!(message.contains("Cherry-picked"), "{message}");
    assert!(root.join("picked.py").exists());
}

#[test]
fn revert_undoes_a_commit_with_a_new_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "extra.py", "VALEUR = 1\n", "add extra");
    let second = head_sha(&root);

    let message = history::revert_commit(&open(&root), &second).unwrap();
    assert!(message.contains("Reverted"), "{message}");
    assert!(!root.join("extra.py").exists());
    assert_eq!(gitlog::commits(&open(&root), 80, true).len(), 3);
}

#[test]
fn rebase_replays_the_branch() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let base = head_sha(&root);
    commit_file(&root, "on_main.py", "M = 1\n", "main moves on");
    git(&root, &["checkout", "-q", "-b", "feature", &base]);
    commit_file(&root, "on_feature.py", "F = 1\n", "feature work");

    let message = history::rebase(&open(&root), "main").unwrap();
    assert!(message.contains("Replayed"), "{message}");
    assert!(root.join("on_main.py").exists() && root.join("on_feature.py").exists());
}

#[test]
fn a_rebase_started_here_stays_readable_by_the_command_line() {
    let shop = Workshop::new();
    let (root, _) = conflicting(&shop);
    git(&root, &["checkout", "-q", "side"]);

    assert!(history::rebase(&open(&root), "main").is_err(), "the rebase must stop on a conflict");

    // The whole reason rebase is not done through libgit2: `git` itself must be able to finish it.
    std::fs::write(root.join("shared.py"), "VALEUR = 3\n").unwrap();
    git(&root, &["add", "shared.py"]);
    let out = git(&root, &["-c", "core.editor=true", "rebase", "--continue"]);
    assert!(gitlog::pending_operation(&open(&root)).is_none(), "git could not continue: {out}");
}

#[test]
fn a_conflicting_cherry_pick_reports_the_conflict() {
    let shop = Workshop::new();
    let (root, side) = conflicting(&shop);

    let error = history::cherry_pick(&open(&root), &side).unwrap_err();
    assert!(error.to_string().contains("conflict"), "{error}");
    assert_eq!(gitlog::pending_operation(&open(&root)).unwrap().kind, "cherry-pick");
}

#[test]
fn a_clean_repository_has_nothing_to_abort_or_skip() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    assert!(gitlog::pending_operation(&open(&root)).is_none());

    let error = history::abort(&open(&root)).unwrap_err();
    assert!(error.to_string().contains("Nothing to abort"), "{error}");
    let error = history::skip(&open(&root)).unwrap_err();
    assert!(error.to_string().contains("Nothing to skip"), "{error}");
}

#[test]
fn aborting_a_conflicted_replay_restores_the_branch() {
    let shop = Workshop::new();
    let (root, side) = conflicting(&shop);
    assert!(history::cherry_pick(&open(&root), &side).is_err());

    let message = history::abort(&open(&root)).unwrap();
    assert!(message.contains("Aborted"), "{message}");
    assert!(gitlog::pending_operation(&open(&root)).is_none());
    assert_eq!(std::fs::read_to_string(root.join("shared.py")).unwrap(), "VALEUR = 1\n");
}

#[test]
fn continuing_is_refused_while_files_still_conflict() {
    let shop = Workshop::new();
    let (root, side) = conflicting(&shop);
    assert!(history::cherry_pick(&open(&root), &side).is_err());

    let error = history::resume(&open(&root)).unwrap_err();
    assert!(error.to_string().contains("still conflict"), "{error}");
}

#[test]
fn a_resolved_conflict_can_be_continued() {
    let shop = Workshop::new();
    let (root, side) = conflicting(&shop);
    assert!(history::cherry_pick(&open(&root), &side).is_err());

    std::fs::write(root.join("shared.py"), "VALEUR = 3\n").unwrap();
    git(&root, &["add", "shared.py"]);

    let message = history::resume(&open(&root)).unwrap();
    assert!(message.contains("Continued"), "{message}");
    assert!(gitlog::pending_operation(&open(&root)).is_none());
}

#[test]
fn a_conflicted_cherry_pick_can_be_skipped() {
    let shop = Workshop::new();
    let (root, side) = conflicting(&shop);
    assert!(history::cherry_pick(&open(&root), &side).is_err());

    let message = history::skip(&open(&root)).unwrap();
    assert!(message.contains("Skipped"), "{message}");
    assert!(gitlog::pending_operation(&open(&root)).is_none());
    assert_eq!(std::fs::read_to_string(root.join("shared.py")).unwrap(), "VALEUR = 1\n");
}

#[test]
fn a_replay_target_must_be_a_commit_id() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    for hostile in ["--upload-pack=touch", "HEAD; rm -rf /", "main", ""] {
        assert!(history::cherry_pick(&repo, hostile).is_err(), "{hostile:?}");
        assert!(history::revert_commit(&repo, hostile).is_err(), "{hostile:?}");
    }
}

#[test]
fn a_rebase_target_may_be_a_branch_name() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    let error = history::rebase(&repo, "--onto=/etc").unwrap_err();
    assert!(!error.to_string().contains("Replayed"), "{error}");
}
