//! Remotes and cloning. `origin` is a bare repository on disk, so nothing leaves the machine.

mod common;

use common::{commit_file, git, open, Workshop};
use gitsquid_core::{clone, gitlog, refs, worktree};

fn with_origin(shop: &Workshop) -> (std::path::PathBuf, std::path::PathBuf) {
    let root = shop.repo("workshop");
    git(&shop.path(), &["init", "--bare", "-q", "origin.git"]);
    let origin = shop.path().join("origin.git");
    git(&root, &["remote", "add", "origin", &origin.to_string_lossy()]);
    git(&root, &["push", "-q", "-u", "origin", "main"]);
    (root, origin)
}

#[test]
fn remote_commands_refuse_without_a_remote() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    for error in [
        worktree::fetch(&open(&root)).unwrap_err(),
        worktree::pull(&open(&root)).unwrap_err(),
        worktree::push(&open(&root), false).unwrap_err(),
    ] {
        assert!(error.to_string().contains("no remote configured"), "{error}");
    }
}

#[test]
fn pushing_sends_the_branch_to_the_remote() {
    let shop = Workshop::new();
    let (root, origin) = with_origin(&shop);
    commit_file(&root, "suite.py", "X = 1\n", "un cran devant");

    worktree::push(&open(&root), false).unwrap();
    assert_eq!(
        git(&origin, &["log", "-1", "--pretty=%s"]).trim(),
        "un cran devant",
        "the remote should have the new commit"
    );
}

#[test]
fn fetching_brings_the_remote_branches_up_to_date() {
    let shop = Workshop::new();
    let (root, origin) = with_origin(&shop);

    // Someone else pushes, through a second clone of the same bare repository.
    let other = shop.path().join("autre");
    git(&shop.path(), &["clone", "-q", &origin.to_string_lossy(), "autre"]);
    git(&other, &["config", "user.email", "other@example.invalid"]);
    git(&other, &["config", "user.name", "Other"]);
    commit_file(&other, "ailleurs.py", "A = 1\n", "travail distant");
    git(&other, &["push", "-q", "origin", "main"]);

    worktree::fetch(&open(&root)).unwrap();
    let state = worktree::tracking(&open(&root));
    assert_eq!(state.behind, 1, "the fetch should have noticed the remote moved on");
}

#[test]
fn pulling_fast_forwards() {
    let shop = Workshop::new();
    let (root, origin) = with_origin(&shop);
    let other = shop.path().join("autre");
    git(&shop.path(), &["clone", "-q", &origin.to_string_lossy(), "autre"]);
    git(&other, &["config", "user.email", "other@example.invalid"]);
    git(&other, &["config", "user.name", "Other"]);
    commit_file(&other, "ailleurs.py", "A = 1\n", "travail distant");
    git(&other, &["push", "-q", "origin", "main"]);

    worktree::pull(&open(&root)).unwrap();
    assert!(root.join("ailleurs.py").exists());
    assert_eq!(worktree::tracking(&open(&root)).behind, 0);
}

#[test]
fn an_annotated_tag_keeps_its_message_and_reaches_the_remote() {
    let shop = Workshop::new();
    let (root, origin) = with_origin(&shop);

    refs::create_tag(&open(&root), "v2.0.0", "", "La version deux").unwrap();
    assert_eq!(gitlog::tags(&open(&root))[0].subject, "La version deux");

    let message = refs::push_tag(&open(&root), "v2.0.0").unwrap();
    assert!(message.contains("origin"), "{message}");
    assert!(git(&origin, &["tag"]).contains("v2.0.0"));
}

#[test]
fn pushing_a_tag_needs_a_remote() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    refs::create_tag(&open(&root), "v1.0.0", "", "").unwrap();
    let error = refs::push_tag(&open(&root), "v1.0.0").unwrap_err();
    assert!(error.to_string().contains("no remote"), "{error}");
}

#[test]
fn a_branch_can_be_pushed_by_name() {
    let shop = Workshop::new();
    let (root, origin) = with_origin(&shop);
    git(&root, &["checkout", "-q", "-b", "feature/envoyee"]);
    commit_file(&root, "envoye.py", "E = 1\n", "travail");
    git(&root, &["checkout", "-q", "main"]);

    refs::push_branch(&open(&root), "feature/envoyee").unwrap();
    assert!(git(&origin, &["branch"]).contains("feature/envoyee"));
}

#[test]
fn deleting_on_the_remote_leaves_the_local_branch() {
    let shop = Workshop::new();
    let (root, origin) = with_origin(&shop);
    git(&root, &["push", "-q", "origin", "main:jetable"]);

    let message = refs::delete_remote_branch(&open(&root), "origin/jetable").unwrap();
    assert!(message.contains("Deleted"), "{message}");
    assert!(!git(&origin, &["branch"]).contains("jetable"));
    assert_eq!(gitlog::current_branch(&open(&root)), "main");
}

// ------------------------------------------------------------------ cloning

#[test]
fn cloning_lands_a_working_repository() {
    let shop = Workshop::new();
    let (_, origin) = with_origin(&shop);
    let parent = shop.plain("cible");

    let target = clone::clone(&origin.to_string_lossy(), &parent, "").unwrap();
    assert_eq!(target, parent.join("origin"));
    assert!(target.join(".git").exists());
    assert_eq!(gitlog::count_commits(&open(&target), false), 1);
}

#[test]
fn a_clone_can_be_given_its_own_folder_name() {
    let shop = Workshop::new();
    let (_, origin) = with_origin(&shop);
    let parent = shop.plain("cible");

    let target = clone::clone(&origin.to_string_lossy(), &parent, "mon-depot").unwrap();
    assert_eq!(target, parent.join("mon-depot"));
}

#[test]
fn cloning_over_an_existing_folder_is_refused() {
    let shop = Workshop::new();
    let (_, origin) = with_origin(&shop);
    let parent = shop.plain("cible");
    std::fs::create_dir(parent.join("occupe")).unwrap();

    let error = clone::clone(&origin.to_string_lossy(), &parent, "occupe").unwrap_err();
    assert!(error.to_string().contains("already exists"), "{error}");
}

#[test]
fn a_url_that_is_not_one_is_refused() {
    let shop = Workshop::new();
    let parent = shop.plain("cible");
    for bad in ["not a url", "--upload-pack=touch", ""] {
        let error = clone::clone(bad, &parent, "").unwrap_err();
        assert!(error.to_string().contains("does not look like"), "{bad:?}: {error}");
    }
}

#[test]
fn a_hostile_folder_name_never_escapes_the_parent() {
    let shop = Workshop::new();
    let (_, origin) = with_origin(&shop);
    let parent = shop.plain("cible");
    for bad in ["../escape", "a/b", ".."] {
        let error = clone::clone(&origin.to_string_lossy(), &parent, bad).unwrap_err();
        assert!(error.to_string().contains("folder name"), "{bad:?}: {error}");
    }
}

#[test]
fn cloning_into_somewhere_that_is_not_a_directory_is_refused() {
    let shop = Workshop::new();
    let (_, origin) = with_origin(&shop);
    let error = clone::clone(&origin.to_string_lossy(), &shop.path().join("nulle-part"), "")
        .unwrap_err();
    assert!(error.to_string().contains("not a directory"), "{error}");
}
