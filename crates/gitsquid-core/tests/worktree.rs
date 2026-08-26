//! The working tree and the index, as the staging panel reads them.

mod common;

use common::{commit_file, git, open, Workshop};
use gitsquid_core::worktree;

#[test]
fn a_clean_tree_lists_nothing() {
    let shop = Workshop::new();
    assert!(worktree::status(&open(&shop.repo("workshop"))).is_empty());
}

#[test]
fn each_side_of_the_index_gets_its_own_letter() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "MODIFIE = 1\n").unwrap();
    std::fs::write(root.join("neuf.py"), "N = 1\n").unwrap();
    git(&root, &["add", "neuf.py"]);
    std::fs::write(root.join("jamais.py"), "U = 1\n").unwrap();

    let files = worktree::status(&open(&root));
    let by_path: std::collections::HashMap<&str, &worktree::FileEntry> =
        files.iter().map(|file| (file.path.as_str(), file)).collect();

    let modified = by_path["calc.py"];
    assert_eq!((modified.index_code.as_str(), modified.work_code.as_str()), (" ", "M"));
    assert!(modified.unstaged && !modified.staged);
    assert_eq!(modified.work_label, "modified");

    let added = by_path["neuf.py"];
    assert_eq!(added.index_code, "A");
    assert!(added.staged && !added.untracked);
    assert_eq!(added.index_label, "added");

    let untracked = by_path["jamais.py"];
    assert_eq!((untracked.index_code.as_str(), untracked.work_code.as_str()), ("?", "?"));
    assert!(untracked.untracked && !untracked.staged);
}

#[test]
fn a_file_can_be_staged_and_modified_at_once() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "ETAPE = 1\n").unwrap();
    git(&root, &["add", "calc.py"]);
    std::fs::write(root.join("calc.py"), "ETAPE = 2\n").unwrap();

    let entry = &worktree::status(&open(&root))[0];
    assert_eq!((entry.index_code.as_str(), entry.work_code.as_str()), ("M", "M"));
    assert!(entry.staged && entry.unstaged);
}

#[test]
fn the_list_is_sorted_by_path() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    for name in ["zebre.py", "alpha.py", "milieu.py"] {
        std::fs::write(root.join(name), "X = 1\n").unwrap();
    }
    let paths: Vec<String> =
        worktree::status(&open(&root)).into_iter().map(|file| file.path).collect();
    assert_eq!(paths, ["alpha.py", "milieu.py", "zebre.py"]);
}

#[test]
fn a_credential_file_is_flagged_as_sensitive() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join(".env"), "TOKEN=abcdef123456\n").unwrap();
    std::fs::write(root.join("calc.py"), "X = 1\n").unwrap();

    let files = worktree::status(&open(&root));
    let secret = files.iter().find(|file| file.path == ".env").expect(".env is listed");
    let ordinary = files.iter().find(|file| file.path == "calc.py").expect("calc.py is listed");
    assert!(secret.sensitive);
    assert!(!ordinary.sensitive);
}

#[test]
fn a_staged_rename_keeps_the_name_it_had() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["mv", "calc.py", "calcul.py"]);

    let entry = &worktree::status(&open(&root))[0];
    assert_eq!(entry.path, "calcul.py");
    assert_eq!(entry.index_code, "R");
    assert_eq!(entry.original.as_deref(), Some("calc.py"));
}

#[test]
fn an_accented_name_is_read_as_it_is_written() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "café été.txt", "contenu\n", "un nom accentue");
    std::fs::write(root.join("café été.txt"), "modifié\n").unwrap();

    let paths: Vec<String> =
        worktree::status(&open(&root)).into_iter().map(|file| file.path).collect();
    assert_eq!(paths, ["café été.txt"], "no octal escaping");

    let diff = worktree::file_diff(&open(&root), "café été.txt", false, false, 3).unwrap();
    assert!(diff.contains("modifié"), "{diff}");
}

#[test]
fn counts_ride_along_with_each_file() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "UNE = 1\nDEUX = 2\nTROIS = 3\n").unwrap();
    git(&root, &["add", "calc.py"]);

    let entry = &worktree::status(&open(&root))[0];
    let staged = entry.counts.staged.expect("a staged pair");
    assert_eq!(staged[0], 3, "three lines gained");
    assert!(entry.counts.unstaged.is_none(), "nothing left unstaged");
}

#[test]
fn a_binary_file_reports_no_line_count() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("image.bin"), (0..=255u8).collect::<Vec<u8>>()).unwrap();
    git(&root, &["add", "image.bin"]);

    let entry = &worktree::status(&open(&root))[0];
    assert_eq!(entry.counts.staged, Some([-1, -1]));
}

#[test]
fn the_view_counts_each_kind() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "M = 1\n").unwrap();
    std::fs::write(root.join("neuf.py"), "N = 1\n").unwrap();
    git(&root, &["add", "neuf.py"]);
    std::fs::write(root.join("jamais.py"), "U = 1\n").unwrap();

    let view = worktree::view(&open(&root));
    assert_eq!(view.files.len(), 3);
    assert_eq!(view.staged, 1);
    assert_eq!(view.unstaged, 2, "the modified file and the untracked one");
    assert_eq!(view.conflicted, 0);
}

#[test]
fn a_conflict_is_reported_on_both_sides() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "cote"]);
    commit_file(&root, "calc.py", "COTE = 1\n", "cote");
    git(&root, &["checkout", "-q", "main"]);
    commit_file(&root, "calc.py", "MAIN = 1\n", "main");
    git(&root, &["merge", "cote"]);

    let view = worktree::view(&open(&root));
    assert_eq!(view.conflicted, 1);
    let entry = view.files.iter().find(|file| file.path == "calc.py").expect("calc.py");
    assert!(entry.conflicted);
    assert_eq!(entry.index_label, "conflicted");
}

// ------------------------------------------------------------------ diffs

#[test]
fn the_staged_and_unstaged_sides_show_different_diffs() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "ETAPE = 1\n").unwrap();
    git(&root, &["add", "calc.py"]);
    std::fs::write(root.join("calc.py"), "ETAPE = 2\n").unwrap();

    let repo = open(&root);
    let staged = worktree::file_diff(&repo, "calc.py", true, false, 3).unwrap();
    let unstaged = worktree::file_diff(&repo, "calc.py", false, false, 3).unwrap();
    assert!(staged.contains("+ETAPE = 1"), "{staged}");
    assert!(unstaged.contains("+ETAPE = 2"), "{unstaged}");
}

#[test]
fn an_untracked_file_reads_as_a_whole_file_addition() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("jamais.py"), "PREMIERE = 1\nSECONDE = 2\n").unwrap();

    let diff = worktree::file_diff(&open(&root), "jamais.py", false, false, 3).unwrap();
    assert!(diff.contains("+PREMIERE = 1"), "{diff}");
    assert!(diff.contains("+SECONDE = 2"), "{diff}");
}

#[test]
fn whitespace_only_changes_can_be_ignored() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "espaces.py", "VALEUR = 1\n", "base");
    std::fs::write(root.join("espaces.py"), "VALEUR   =   1\n").unwrap();

    let repo = open(&root);
    let noticed = worktree::file_diff(&repo, "espaces.py", false, false, 3).unwrap();
    let ignored = worktree::file_diff(&repo, "espaces.py", false, true, 3).unwrap();
    assert!(noticed.contains("+VALEUR   =   1"), "{noticed}");
    assert!(!ignored.contains("+VALEUR   =   1"), "whitespace was meant to be ignored:\n{ignored}");
}

#[test]
fn a_diff_path_outside_the_repository_is_refused() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    assert!(worktree::file_diff(&open(&root), "../../etc/passwd", false, false, 3).is_err());
}

// ------------------------------------------------------------------ upstream, stashes, remotes

fn with_origin(shop: &Workshop) -> std::path::PathBuf {
    let root = shop.repo("workshop");
    git(&shop.path(), &["init", "--bare", "-q", "origin.git"]);
    let origin = shop.path().join("origin.git");
    git(&root, &["remote", "add", "origin", &origin.to_string_lossy()]);
    git(&root, &["push", "-q", "-u", "origin", "main"]);
    root
}

#[test]
fn a_branch_without_an_upstream_tracks_nothing() {
    let shop = Workshop::new();
    let state = worktree::tracking(&open(&shop.repo("workshop")));
    assert_eq!(state.upstream, None);
    assert_eq!((state.ahead, state.behind), (0, 0));
}

#[test]
fn a_tracked_branch_reports_how_far_it_has_drifted() {
    let shop = Workshop::new();
    let root = with_origin(&shop);
    commit_file(&root, "suite.py", "X = 1\n", "un cran devant");

    let state = worktree::tracking(&open(&root));
    assert_eq!(state.upstream.as_deref(), Some("origin/main"));
    assert_eq!((state.ahead, state.behind), (1, 0));
}

#[test]
fn remotes_are_listed_with_their_url() {
    let shop = Workshop::new();
    let root = with_origin(&shop);
    let found = worktree::remotes(&open(&root));
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].name, "origin");
    assert!(found[0].url.ends_with("origin.git"), "{}", found[0].url);
}

#[test]
fn a_repository_without_a_remote_says_so() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    assert!(worktree::remotes(&open(&root)).is_empty());
    let error = worktree::default_remote(&open(&root)).unwrap_err();
    assert!(error.to_string().contains("no remote configured"), "{error}");
}

#[test]
fn origin_wins_when_several_remotes_exist() {
    let shop = Workshop::new();
    let root = with_origin(&shop);
    git(&root, &["remote", "add", "autre", "https://example.invalid/x.git"]);
    assert_eq!(worktree::default_remote(&open(&root)).unwrap(), "origin");
}

#[test]
fn stashes_come_back_newest_first_with_their_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "PREMIER = 1\n").unwrap();
    git(&root, &["stash", "push", "-q", "-m", "premier travail"]);
    std::fs::write(root.join("calc.py"), "SECOND = 1\n").unwrap();
    git(&root, &["stash", "push", "-q", "-m", "second travail"]);

    let found = worktree::stash_list(&open(&root));
    assert_eq!(found.len(), 2);
    assert_eq!(found[0].reference, "stash@{0}");
    assert_eq!(found[1].reference, "stash@{1}");
    assert!(found[0].subject.contains("second travail"), "{}", found[0].subject);
    assert_eq!(found[0].sha.len(), 40);
    assert!(found[0].age.ends_with("ago"), "{}", found[0].age);
}

#[test]
fn no_stash_is_an_empty_list_not_a_failure() {
    let shop = Workshop::new();
    assert!(worktree::stash_list(&open(&shop.repo("workshop"))).is_empty());
}

#[test]
fn the_head_message_comes_back_whole() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["commit", "-q", "--allow-empty", "-m", "titre\n\nun corps sur deux lignes"]);

    let message = worktree::head_message(&open(&root));
    assert!(message.starts_with("titre"), "{message}");
    assert!(message.contains("un corps sur deux lignes"), "{message}");
}
