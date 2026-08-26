//! Staging, discarding, branching — everything that writes without creating a commit.

mod common;

use common::{commit_file, git, open, split_hunks, Workshop};
use gitsquid_core::worktree;

fn owned(paths: &[&str]) -> Vec<String> {
    paths.iter().map(|path| path.to_string()).collect()
}

fn entry_for(repo: &git2::Repository, path: &str) -> Option<worktree::FileEntry> {
    worktree::status(repo).into_iter().find(|entry| entry.path == path)
}

#[test]
fn staging_moves_a_file_to_the_index() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "changed\n").unwrap();

    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    let entry = entry_for(&open(&root), "calc.py").expect("calc.py is listed");
    assert!(entry.staged);
    assert_eq!(entry.index_code, "M");
}

#[test]
fn staging_records_a_deletion_too() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::remove_file(root.join("README.md")).unwrap();

    worktree::stage(&open(&root), &owned(&["README.md"])).unwrap();
    let entry = entry_for(&open(&root), "README.md").expect("README.md is listed");
    assert_eq!(entry.index_code, "D", "add_all alone would have missed the removal");
}

#[test]
fn unstage_returns_a_file_to_the_working_tree() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "changed\n").unwrap();

    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    worktree::unstage(&repo, &owned(&["calc.py"])).unwrap();

    let entry = entry_for(&open(&root), "calc.py").expect("calc.py is listed");
    assert!(entry.unstaged && !entry.staged);
}

#[test]
fn unstage_works_before_the_first_commit() {
    let shop = Workshop::new();
    let root = shop.empty_repo("neuf");
    std::fs::write(root.join("premier.py").clone(), "X = 1\n").unwrap();

    let repo = open(&root);
    worktree::stage(&repo, &owned(&["premier.py"])).unwrap();
    worktree::unstage(&repo, &owned(&["premier.py"])).unwrap();

    let entry = entry_for(&open(&root), "premier.py").expect("premier.py is listed");
    assert!(entry.untracked, "with no HEAD to reset to, the entry simply leaves the index");
}

#[test]
fn discard_restores_a_tracked_file() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let original = std::fs::read_to_string(root.join("calc.py")).unwrap();
    std::fs::write(root.join("calc.py"), "ruined\n").unwrap();

    worktree::discard(&open(&root), &owned(&["calc.py"])).unwrap();
    assert_eq!(std::fs::read_to_string(root.join("calc.py")).unwrap(), original);
}

#[test]
fn discard_deletes_an_untracked_file() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("junk.py"), "noise\n").unwrap();

    worktree::discard(&open(&root), &owned(&["junk.py"])).unwrap();
    assert!(!root.join("junk.py").exists());
}

#[test]
fn discard_restores_from_the_index_not_from_head() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "ETAPE = 1\n").unwrap();
    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    std::fs::write(root.join("calc.py"), "ETAPE = 2\n").unwrap();

    worktree::discard(&open(&root), &owned(&["calc.py"])).unwrap();
    assert_eq!(
        std::fs::read_to_string(root.join("calc.py")).unwrap(),
        "ETAPE = 1\n",
        "`git checkout -- path` restores the staged version, not the committed one"
    );
}

#[test]
fn paths_outside_the_repository_are_refused() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    for hostile in ["../escape.py", "/etc/passwd", ".git/config", ".gitsquid/gitsquid.db"] {
        let error = worktree::stage(&repo, &owned(&[hostile])).unwrap_err();
        assert!(error.to_string().contains("outside the repository"), "{hostile}: {error}");
    }
}

#[test]
fn an_empty_or_huge_selection_is_refused() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    assert!(worktree::stage(&repo, &[]).unwrap_err().to_string().contains("No file selected"));

    let many: Vec<String> = (0..501).map(|n| format!("file{n}.py")).collect();
    assert!(worktree::stage(&repo, &many).unwrap_err().to_string().contains("Too many files"));
}

// ------------------------------------------------------------------ branches

#[test]
fn create_switches_to_the_new_branch() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let message = worktree::create_branch(&open(&root), "feature/x").unwrap();
    assert!(message.contains("feature/x"), "{message}");
    assert_eq!(git(&root, &["rev-parse", "--abbrev-ref", "HEAD"]).trim(), "feature/x");
}

#[test]
fn checkout_returns_to_an_existing_branch() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    worktree::create_branch(&open(&root), "feature/y").unwrap();
    worktree::checkout(&open(&root), "main").unwrap();
    assert_eq!(git(&root, &["rev-parse", "--abbrev-ref", "HEAD"]).trim(), "main");
}

#[test]
fn invalid_branch_names_are_refused() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    for bad in ["bad name", "..", "-x", "a\\b"] {
        assert!(worktree::create_branch(&repo, bad).is_err(), "{bad:?} should be refused");
    }
    let error = worktree::checkout(&repo, "  ").unwrap_err();
    assert!(error.to_string().contains("1 to 200"), "{error}");
}

#[test]
fn a_branch_that_does_not_exist_is_reported() {
    let shop = Workshop::new();
    let error = worktree::checkout(&open(&shop.repo("workshop")), "fantome").unwrap_err();
    assert!(error.to_string().contains("no branch called"), "{error}");
}

#[test]
fn deleting_the_branch_you_are_on_is_refused() {
    let shop = Workshop::new();
    let error = worktree::delete_branch(&open(&shop.repo("workshop")), "main", false).unwrap_err();
    assert!(error.to_string().contains("cannot delete the branch you are on"), "{error}");
}

#[test]
fn deleting_an_unmerged_branch_needs_force() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "feature/perdue"]);
    commit_file(&root, "seule.py", "x = 1\n", "travail non fusionne");
    git(&root, &["checkout", "-q", "main"]);

    let repo = open(&root);
    let error = worktree::delete_branch(&repo, "feature/perdue", false).unwrap_err();
    assert!(error.to_string().contains("not fully merged"), "{error}");

    let message = worktree::delete_branch(&repo, "feature/perdue", true).unwrap();
    assert!(message.contains("Deleted"), "{message}");
}

#[test]
fn a_merged_branch_deletes_without_force() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["branch", "feature/deja-la"]);
    let message = worktree::delete_branch(&open(&root), "feature/deja-la", false).unwrap();
    assert!(message.contains("Deleted"), "{message}");
}

// ------------------------------------------------------------------ hunks

/// A file edited in two places far enough apart for git to emit two hunks.
fn two_hunks(shop: &Workshop) -> std::path::PathBuf {
    let root = shop.repo("workshop");
    let mut lines: Vec<String> = (0..40).map(|n| format!("line {n}\n")).collect();
    commit_file(&root, "long.txt", &lines.concat(), "Ajoute long.txt");

    lines[0] = "premiere ligne modifiee\n".to_string();
    lines[39] = "derniere ligne modifiee\n".to_string();
    std::fs::write(root.join("long.txt"), lines.concat()).unwrap();
    root
}

#[test]
fn one_hunk_can_be_staged_alone() {
    let shop = Workshop::new();
    let root = two_hunks(&shop);
    let repo = open(&root);

    let (header, hunks) = split_hunks(&worktree::file_diff(&repo, "long.txt", false, false, 3).unwrap());
    assert_eq!(hunks.len(), 2, "the fixture must produce two hunks");

    worktree::apply_patch(&repo, &format!("{header}{}", hunks[0]), "stage").unwrap();

    let staged = worktree::file_diff(&open(&root), "long.txt", true, false, 3).unwrap();
    let rest = worktree::file_diff(&open(&root), "long.txt", false, false, 3).unwrap();
    assert!(staged.contains("premiere ligne modifiee"), "{staged}");
    assert!(!staged.contains("derniere ligne modifiee"), "{staged}");
    assert!(rest.contains("derniere ligne modifiee"), "{rest}");
}

#[test]
fn a_staged_hunk_can_be_returned_to_the_working_tree() {
    let shop = Workshop::new();
    let root = two_hunks(&shop);
    let repo = open(&root);
    let (header, hunks) = split_hunks(&worktree::file_diff(&repo, "long.txt", false, false, 3).unwrap());
    worktree::apply_patch(&repo, &format!("{header}{}", hunks[0]), "stage").unwrap();

    let repo = open(&root);
    let (staged_header, staged_hunks) =
        split_hunks(&worktree::file_diff(&repo, "long.txt", true, false, 3).unwrap());
    worktree::apply_patch(&repo, &format!("{staged_header}{}", staged_hunks[0]), "unstage").unwrap();

    let left = worktree::file_diff(&open(&root), "long.txt", true, false, 3).unwrap();
    assert!(left.trim().is_empty(), "the index should be back to HEAD:\n{left}");
}

#[test]
fn discarding_a_hunk_leaves_the_other_edit() {
    let shop = Workshop::new();
    let root = two_hunks(&shop);
    let repo = open(&root);
    let (header, hunks) = split_hunks(&worktree::file_diff(&repo, "long.txt", false, false, 3).unwrap());

    worktree::apply_patch(&repo, &format!("{header}{}", hunks[0]), "discard").unwrap();

    let text = std::fs::read_to_string(root.join("long.txt")).unwrap();
    assert!(!text.contains("premiere ligne modifiee"), "{text}");
    assert!(text.contains("derniere ligne modifiee"), "{text}");
}

#[test]
fn a_hunk_touching_a_forbidden_path_is_refused() {
    let shop = Workshop::new();
    let patch = "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n@@ -1 +1 @@\n-old\n+new\n";
    let error = worktree::apply_patch(&open(&shop.repo("workshop")), patch, "stage").unwrap_err();
    assert!(error.to_string().contains("Refusing path"), "{error}");
}

#[test]
fn a_hunk_that_no_longer_matches_the_file_is_refused() {
    let shop = Workshop::new();
    let root = two_hunks(&shop);
    let (header, hunks) =
        split_hunks(&worktree::file_diff(&open(&root), "long.txt", false, false, 3).unwrap());
    std::fs::write(root.join("long.txt"), "tout autre chose\n").unwrap();

    let error = worktree::apply_patch(&open(&root), &format!("{header}{}", hunks[0]), "discard")
        .unwrap_err();
    assert!(error.to_string().contains("reload it"), "{error}");
}

#[test]
fn an_unknown_patch_target_is_refused() {
    let shop = Workshop::new();
    let error = worktree::apply_patch(&open(&shop.repo("workshop")), "diff", "delete").unwrap_err();
    assert!(error.to_string().contains("Unknown patch target"), "{error}");
}

// ------------------------------------------------------------------ .gitignore

#[test]
fn a_file_is_added_to_gitignore_and_leaves_the_status() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("bruit.log"), "noise\n").unwrap();

    let message = worktree::ignore(&open(&root), &owned(&["bruit.log"])).unwrap();
    assert!(message.contains("Ignored"), "{message}");
    assert!(std::fs::read_to_string(root.join(".gitignore")).unwrap().contains("/bruit.log"));
    assert!(entry_for(&open(&root), "bruit.log").is_none());
}

#[test]
fn ignoring_twice_changes_nothing() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("bruit.log"), "noise\n").unwrap();

    worktree::ignore(&open(&root), &owned(&["bruit.log"])).unwrap();
    let again = worktree::ignore(&open(&root), &owned(&["bruit.log"])).unwrap();
    assert_eq!(again, "Already ignored.");
    assert_eq!(
        std::fs::read_to_string(root.join(".gitignore")).unwrap().matches("bruit.log").count(),
        1
    );
}

#[test]
fn ignoring_a_tracked_file_stages_its_removal() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let message = worktree::ignore(&open(&root), &owned(&["README.md"])).unwrap();

    assert!(message.contains("takes effect when you commit"), "{message}");
    assert_eq!(git(&root, &["ls-files", "README.md"]).trim(), "");
    assert!(root.join("README.md").exists(), "the file itself stays on disk");
}

// ------------------------------------------------------------------ conflicts

fn conflicted(shop: &Workshop) -> std::path::PathBuf {
    let root = shop.repo("workshop");
    commit_file(&root, "shared.py", "valeur = 0\n", "base");
    git(&root, &["checkout", "-q", "-b", "side"]);
    commit_file(&root, "shared.py", "valeur = 2\n", "cote");
    git(&root, &["checkout", "-q", "main"]);
    commit_file(&root, "shared.py", "valeur = 1\n", "principal");
    git(&root, &["merge", "side"]);
    root
}

#[test]
fn keeping_our_side_stages_it_as_resolved() {
    let shop = Workshop::new();
    let root = conflicted(&shop);
    let message = worktree::resolve(&open(&root), &owned(&["shared.py"]), "ours").unwrap();

    assert!(message.contains("your side"), "{message}");
    assert_eq!(std::fs::read_to_string(root.join("shared.py")).unwrap(), "valeur = 1\n");
    assert!(worktree::status(&open(&root)).iter().all(|entry| !entry.conflicted));
}

#[test]
fn keeping_their_side_takes_the_incoming_content() {
    let shop = Workshop::new();
    let root = conflicted(&shop);
    worktree::resolve(&open(&root), &owned(&["shared.py"]), "theirs").unwrap();

    assert_eq!(std::fs::read_to_string(root.join("shared.py")).unwrap(), "valeur = 2\n");
    let entry = entry_for(&open(&root), "shared.py").expect("shared.py is listed");
    assert!(entry.staged && !entry.conflicted);
}

#[test]
fn a_file_that_is_not_in_conflict_is_refused() {
    let shop = Workshop::new();
    let root = conflicted(&shop);
    let error = worktree::resolve(&open(&root), &owned(&["calc.py"]), "ours").unwrap_err();
    assert!(error.to_string().contains("not in conflict"), "{error}");
}

#[test]
fn an_unknown_side_is_refused() {
    let shop = Workshop::new();
    let root = conflicted(&shop);
    let error = worktree::resolve(&open(&root), &owned(&["shared.py"]), "mine").unwrap_err();
    assert!(error.to_string().contains("'ours' or 'theirs'"), "{error}");
}

// ------------------------------------------------------------------ stashes

#[test]
fn a_stash_can_be_popped_applied_and_dropped() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let original = std::fs::read_to_string(root.join("calc.py")).unwrap();
    std::fs::write(root.join("calc.py"), "travail en cours\n").unwrap();
    git(&root, &["stash", "push", "-q", "-m", "mon travail"]);
    assert_eq!(std::fs::read_to_string(root.join("calc.py")).unwrap(), original);

    let mut repo = open(&root);
    let message = worktree::stash_apply(&mut repo, "stash@{0}").unwrap();
    assert!(message.contains("kept it"), "{message}");
    assert_eq!(std::fs::read_to_string(root.join("calc.py")).unwrap(), "travail en cours\n");
    assert_eq!(worktree::stash_list(&open(&root)).len(), 1, "apply keeps the stash");

    let mut repo = open(&root);
    worktree::stash_drop(&mut repo, "stash@{0}").unwrap();
    assert!(worktree::stash_list(&open(&root)).is_empty());
}

#[test]
fn popping_restores_and_removes_in_one_go() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "travail en cours\n").unwrap();
    git(&root, &["stash", "push", "-q", "-m", "mon travail"]);

    let mut repo = open(&root);
    worktree::stash_pop(&mut repo, "stash@{0}").unwrap();
    assert_eq!(std::fs::read_to_string(root.join("calc.py")).unwrap(), "travail en cours\n");
    assert!(worktree::stash_list(&open(&root)).is_empty());
}

#[test]
fn stash_references_are_validated() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    for hostile in ["stash@{0}; rm -rf /", "HEAD", "stash@{}", "../x", "stash@{99999}"] {
        let mut repo = open(&root);
        let error = worktree::stash_pop(&mut repo, hostile).unwrap_err();
        assert!(error.to_string().contains("stash reference"), "{hostile:?}: {error}");
    }
}

// ------------------------------------------------------------------ the signature wall

#[test]
fn libgit2_refuses_to_write_a_commit_without_an_email() {
    let shop = Workshop::new();
    let root = shop.repo_without_email("sans-email");
    let repo = open(&root);

    // Reading that history is perfectly fine.
    assert_eq!(gitsquid_core::gitlog::count_commits(&repo, true), 1);
    assert_eq!(gitsquid_core::gitlog::commits(&repo, 10, true)[0].subject, "initial");

    // Building the signature such a commit would need is not.
    let Err(error) = repo.signature() else { panic!("libgit2 accepted an empty email") };
    assert!(error.message().contains("empty name or email"), "{error}");

    // Which is the whole reason commit, merge, cherry-pick, revert and stash push go through
    // the `git` CLI instead. The CLI itself has no such objection:
    std::fs::write(root.join("calc.py"), "suite\n").unwrap();
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    git(&root, &["commit", "-q", "-m", "deuxieme"]);
    assert_eq!(gitsquid_core::gitlog::count_commits(&open(&root), true), 2);
}
