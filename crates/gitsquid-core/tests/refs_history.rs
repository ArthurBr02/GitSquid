//! Tags, remotes, and everything that moves HEAD without replaying a commit.

mod common;

use common::{commit_file, git, head_sha, open, Workshop};
use gitsquid_core::{gitlog, history, refs};

fn with_remote(shop: &Workshop) -> std::path::PathBuf {
    let root = shop.repo("workshop");
    git(&shop.path(), &["init", "--bare", "-q", "origin.git"]);
    let origin = shop.path().join("origin.git");
    git(&root, &["remote", "add", "origin", &origin.to_string_lossy()]);
    git(&root, &["push", "-q", "-u", "origin", "main"]);
    root
}

// ------------------------------------------------------------------ tags

#[test]
fn a_lightweight_tag_points_at_head() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let message = refs::create_lightweight_tag(&open(&root), "v1.0.0", "").unwrap();
    assert!(message.contains("v1.0.0"), "{message}");

    let names: Vec<String> = gitlog::tags(&open(&root)).into_iter().map(|tag| tag.name).collect();
    assert_eq!(names, ["v1.0.0"]);
}

#[test]
fn a_tag_can_name_an_older_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let first = head_sha(&root);
    commit_file(&root, "later.py", "X = 1\n", "later");

    refs::create_lightweight_tag(&open(&root), "v0.1.0", &first).unwrap();
    assert_eq!(git(&root, &["rev-parse", "v0.1.0^{commit}"]).trim(), first);
}

#[test]
fn deleting_a_tag_removes_it() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    refs::create_lightweight_tag(&open(&root), "v1.0.0", "").unwrap();

    let message = refs::delete_tag(&open(&root), "v1.0.0").unwrap();
    assert!(message.contains("Deleted"), "{message}");
    assert!(gitlog::tags(&open(&root)).is_empty());
}

#[test]
fn hostile_tag_names_are_refused() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    for bad in ["v1 0", "-x", "..", "a\\b", ""] {
        let error = refs::create_lightweight_tag(&repo, bad, "").unwrap_err();
        assert!(error.to_string().contains("tag name"), "{bad:?}: {error}");
    }
}

#[test]
fn a_tag_on_something_that_is_not_a_commit_id_is_refused() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    assert!(refs::create_lightweight_tag(&repo, "v1.0.0", "HEAD; rm -rf /").is_err());
}

// ------------------------------------------------------------------ branches and remotes

#[test]
fn renaming_a_branch_keeps_the_commits() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let head = head_sha(&root);

    refs::rename_branch(&open(&root), "main", "principale").unwrap();
    assert_eq!(gitlog::current_branch(&open(&root)), "principale");
    assert_eq!(head_sha(&root), head);
}

#[test]
fn an_invalid_new_branch_name_is_refused() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    assert!(refs::rename_branch(&repo, "main", "nom invalide").is_err());
}

#[test]
fn a_remote_can_be_added_and_removed() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let repo = open(&root);

    let message = refs::add_remote(&repo, "origin", "https://example.invalid/depot.git").unwrap();
    assert!(message.contains("origin"), "{message}");
    let names: Vec<String> = gitsquid_core::worktree::remotes(&open(&root))
        .into_iter()
        .map(|entry| entry.name)
        .collect();
    assert_eq!(names, ["origin"]);

    let message = refs::remove_remote(&open(&root), "origin").unwrap();
    assert!(message.contains("Removed"), "{message}");
    assert!(gitsquid_core::worktree::remotes(&open(&root)).is_empty());
}

#[test]
fn an_ssh_or_local_remote_is_accepted() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let repo = open(&root);
    refs::add_remote(&repo, "ssh-one", "git@example.invalid:group/depot.git").unwrap();
    refs::add_remote(&repo, "local", &shop.path().to_string_lossy()).unwrap();

    let names: std::collections::HashSet<String> = gitsquid_core::worktree::remotes(&open(&root))
        .into_iter()
        .map(|entry| entry.name)
        .collect();
    assert_eq!(names, ["ssh-one".to_string(), "local".to_string()].into_iter().collect());
}

#[test]
fn a_hostile_remote_name_or_url_is_refused() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    for (name, url) in [
        ("-x", "https://example.invalid/d.git"),
        ("has space", "https://example.invalid/d.git"),
        ("origin", "--upload-pack=touch"),
        ("origin", "not a url"),
    ] {
        assert!(refs::add_remote(&repo, name, url).is_err(), "{name:?} {url:?} should be refused");
    }
}

#[test]
fn the_same_remote_name_twice_is_refused() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let repo = open(&root);
    refs::add_remote(&repo, "origin", "https://example.invalid/depot.git").unwrap();

    let error = refs::add_remote(&open(&root), "origin", "https://example.invalid/other.git")
        .unwrap_err();
    assert!(error.to_string().contains("already exists"), "{error}");
}

#[test]
fn removing_a_remote_that_is_not_there_is_refused() {
    let shop = Workshop::new();
    let error = refs::remove_remote(&open(&shop.repo("workshop")), "absent").unwrap_err();
    assert!(error.to_string().contains("no remote called"), "{error}");
}

#[test]
fn checking_out_a_remote_branch_creates_the_local_twin() {
    let shop = Workshop::new();
    let root = with_remote(&shop);
    git(&root, &["checkout", "-q", "-b", "feature/distante"]);
    commit_file(&root, "distant.py", "D = 1\n", "work");
    git(&root, &["push", "-q", "origin", "feature/distante"]);
    git(&root, &["checkout", "-q", "main"]);
    git(&root, &["branch", "-q", "-D", "feature/distante"]);

    let message = refs::track_remote_branch(&open(&root), "origin/feature/distante").unwrap();
    assert!(message.contains("tracking"), "{message}");
    assert_eq!(gitlog::current_branch(&open(&root)), "feature/distante");
}

#[test]
fn a_remote_branch_whose_twin_exists_just_switches() {
    let shop = Workshop::new();
    let root = with_remote(&shop);
    git(&root, &["checkout", "-q", "-b", "autre"]);

    let message = refs::track_remote_branch(&open(&root), "origin/main").unwrap();
    assert!(message.contains("already tracks"), "{message}");
    assert_eq!(gitlog::current_branch(&open(&root)), "main");
}

#[test]
fn a_name_without_a_known_remote_is_refused() {
    let shop = Workshop::new();
    let root = with_remote(&shop);
    let repo = open(&root);
    for bad in ["main", "ailleurs/main", "origin/", ""] {
        let error = refs::track_remote_branch(&repo, bad).unwrap_err();
        assert!(error.to_string().contains("not a remote branch"), "{bad:?}: {error}");
    }
}

// ------------------------------------------------------------------ moving HEAD

fn two_commits(shop: &Workshop) -> (std::path::PathBuf, String, String) {
    let root = shop.repo("workshop");
    let first = head_sha(&root);
    commit_file(&root, "extra.py", "VALEUR = 1\n", "add extra");
    let second = head_sha(&root);
    (root, first, second)
}

#[test]
fn checking_out_a_commit_detaches_head() {
    let shop = Workshop::new();
    let (root, first, _) = two_commits(&shop);

    let message = history::checkout_commit(&open(&root), &first).unwrap();
    assert!(message.contains("detached"), "{message}");
    assert_eq!(gitlog::current_branch(&open(&root)), "HEAD");
}

#[test]
fn a_branch_can_start_at_an_older_commit() {
    let shop = Workshop::new();
    let (root, first, _) = two_commits(&shop);

    history::branch_from(&open(&root), &first, "feature/repartir").unwrap();
    assert_eq!(gitlog::current_branch(&open(&root)), "feature/repartir");
    assert_eq!(head_sha(&root), first);
}

#[test]
fn only_hexadecimal_reaches_git() {
    let shop = Workshop::new();
    let repo = open(&shop.repo("workshop"));
    for hostile in ["--upload-pack=touch", "HEAD; rm -rf /", "main", ""] {
        let error = history::checkout_commit(&repo, hostile).unwrap_err();
        assert!(error.to_string().contains("Not a commit id"), "{hostile:?}: {error}");
    }
}

#[test]
fn soft_reset_keeps_the_work_staged() {
    let shop = Workshop::new();
    let (root, first, _) = two_commits(&shop);

    history::reset(&open(&root), &first, "soft").unwrap();
    assert_eq!(head_sha(&root), first);
    assert!(root.join("extra.py").exists());
}

#[test]
fn hard_reset_throws_the_work_away() {
    let shop = Workshop::new();
    let (root, first, _) = two_commits(&shop);

    history::reset(&open(&root), &first, "hard").unwrap();
    assert!(!root.join("extra.py").exists());
}

#[test]
fn an_unknown_reset_mode_is_refused() {
    let shop = Workshop::new();
    let (root, first, _) = two_commits(&shop);
    let error = history::reset(&open(&root), &first, "nuclear").unwrap_err();
    assert!(error.to_string().contains("Reset mode"), "{error}");
}

#[test]
fn a_file_comes_back_as_it_was() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let original = std::fs::read_to_string(root.join("calc.py")).unwrap();
    commit_file(&root, "calc.py", "casse\n", "casse calc");
    let first = git(&root, &["rev-parse", "HEAD~1"]).trim().to_string();

    let message = history::restore_file(&open(&root), &first, "calc.py").unwrap();
    assert!(message.contains("Restored"), "{message}");
    assert_eq!(std::fs::read_to_string(root.join("calc.py")).unwrap(), original);
}

#[test]
fn a_restored_file_lands_staged() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "calc.py", "casse\n", "casse calc");
    let first = git(&root, &["rev-parse", "HEAD~1"]).trim().to_string();

    history::restore_file(&open(&root), &first, "calc.py").unwrap();
    let entry = gitsquid_core::worktree::status(&open(&root))
        .into_iter()
        .find(|entry| entry.path == "calc.py")
        .expect("calc.py is listed");
    assert!(entry.staged, "`git checkout <sha> -- <path>` stages what it restores");
}

#[test]
fn a_restore_path_outside_the_repository_is_refused() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let error = history::restore_file(&open(&root), &head_sha(&root), "../../etc/passwd")
        .unwrap_err();
    assert!(error.to_string().contains("outside the repository"), "{error}");
}

#[test]
fn a_file_absent_from_that_commit_is_reported() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let error = history::restore_file(&open(&root), &head_sha(&root), "jamais-vu.py").unwrap_err();
    assert!(error.to_string().contains("Could not restore"), "{error}");
}
