//! Everything that writes a commit object, and therefore goes through the `git` CLI.

mod common;

use common::{commit_file, git, head_sha, open, Workshop};
use gitsquid_core::{gitlog, worktree};

fn owned(paths: &[&str]) -> Vec<String> {
    paths.iter().map(|path| path.to_string()).collect()
}

#[test]
fn committing_staged_work_creates_a_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "changed\n").unwrap();

    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    let result = worktree::commit(&repo, "Change the calculation", false).unwrap();

    assert_eq!(result.sha.len(), 40);
    assert_eq!(result.short, result.sha[..7]);
    assert!(worktree::status(&open(&root)).is_empty());
    assert_eq!(git(&root, &["log", "-1", "--pretty=%s"]).trim(), "Change the calculation");
}

#[test]
fn committing_nothing_is_refused() {
    let shop = Workshop::new();
    let error = worktree::commit(&open(&shop.repo("workshop")), "empty", false).unwrap_err();
    assert!(error.to_string().contains("Nothing is staged"), "{error}");
}

#[test]
fn an_empty_or_overlong_message_is_refused() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "changed\n").unwrap();
    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();

    assert!(worktree::commit(&repo, "   ", false).unwrap_err().to_string().contains("required"));
    let long = "x".repeat(4001);
    assert!(worktree::commit(&repo, &long, false).unwrap_err().to_string().contains("at most"));
}

#[test]
fn only_staged_work_is_committed() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "staged change\n").unwrap();
    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    std::fs::write(root.join("README.md"), "unstaged change\n").unwrap();

    worktree::commit(&repo, "Only the staged file", false).unwrap();
    let left: Vec<String> =
        worktree::status(&open(&root)).into_iter().map(|entry| entry.path).collect();
    assert_eq!(left, ["README.md"]);
}

#[test]
fn amending_replaces_the_last_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let before = git(&root, &["log", "--oneline"]).lines().count();
    std::fs::write(root.join("calc.py"), "oubli\n").unwrap();

    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    worktree::commit(&repo, "initial, corrige", true).unwrap();

    assert_eq!(git(&root, &["log", "--oneline"]).lines().count(), before);
    assert_eq!(git(&root, &["log", "-1", "--pretty=%s"]).trim(), "initial, corrige");
}

#[test]
fn amending_only_the_message_needs_nothing_staged() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    worktree::commit(&open(&root), "un meilleur titre", true).unwrap();
    assert_eq!(git(&root, &["log", "-1", "--pretty=%s"]).trim(), "un meilleur titre");
}

#[test]
fn amending_is_refused_before_the_first_commit() {
    let shop = Workshop::new();
    let error = worktree::commit(&open(&shop.empty_repo("neuf")), "rien", true).unwrap_err();
    assert!(error.to_string().contains("no commit to amend"), "{error}");
}

/// The reason committing goes through the CLI at all: this repository's own history is written
/// `Arthur <>`, and libgit2 will not build that signature.
#[test]
fn a_repository_with_no_configured_email_can_still_commit() {
    let shop = Workshop::new();
    let root = shop.repo_without_email("sans-email");
    std::fs::write(root.join("calc.py"), "suite\n").unwrap();

    let repo = open(&root);
    worktree::stage(&repo, &owned(&["calc.py"])).unwrap();
    let result = worktree::commit(&repo, "deuxieme", false).unwrap();

    assert_eq!(result.sha.len(), 40);
    assert_eq!(gitlog::count_commits(&open(&root), true), 2);
    assert_eq!(git(&root, &["log", "-1", "--pretty=%an <%ae>"]).trim(), "Arthur <>");
}

// ------------------------------------------------------------------ merge

#[test]
fn merge_brings_in_a_branch() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "feature/merge"]);
    commit_file(&root, "extra.py", "VALEUR = 2\n", "Ajoute extra.py");
    git(&root, &["checkout", "-q", "main"]);

    let message = worktree::merge(&open(&root), "feature/merge", false).unwrap();
    assert!(message.contains("Merged"), "{message}");
    assert!(root.join("extra.py").exists());
}

#[test]
fn a_squash_merge_leaves_the_work_staged() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "feature/squash"]);
    commit_file(&root, "ajoute.py", "A = 1\n", "travail a ecraser");
    git(&root, &["checkout", "-q", "main"]);

    let message = worktree::merge(&open(&root), "feature/squash", true).unwrap();
    assert!(message.contains("Squashed"), "{message}");
    let entry = worktree::status(&open(&root))
        .into_iter()
        .find(|entry| entry.path == "ajoute.py")
        .expect("ajoute.py is listed");
    assert!(entry.staged);
}

#[test]
fn a_conflicting_merge_says_so() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "cote"]);
    commit_file(&root, "calc.py", "COTE = 1\n", "cote");
    git(&root, &["checkout", "-q", "main"]);
    commit_file(&root, "calc.py", "MAIN = 1\n", "main");

    let error = worktree::merge(&open(&root), "cote", false).unwrap_err();
    assert!(error.to_string().contains("produced conflicts"), "{error}");
}

// ------------------------------------------------------------------ stashes that write

#[test]
fn stash_hides_then_restores_the_working_tree() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let original = std::fs::read_to_string(root.join("calc.py")).unwrap();
    std::fs::write(root.join("calc.py"), "travail en cours\n").unwrap();

    worktree::stash_save(&open(&root), "mon travail").unwrap();
    assert_eq!(std::fs::read_to_string(root.join("calc.py")).unwrap(), original);

    let entries = worktree::stash_list(&open(&root));
    assert_eq!(entries.len(), 1);
    assert!(entries[0].subject.contains("mon travail"), "{}", entries[0].subject);

    let mut repo = open(&root);
    worktree::stash_pop(&mut repo, &entries[0].reference).unwrap();
    assert_eq!(std::fs::read_to_string(root.join("calc.py")).unwrap(), "travail en cours\n");
    assert!(worktree::stash_list(&open(&root)).is_empty());
}

#[test]
fn stashing_a_clean_tree_is_refused() {
    let shop = Workshop::new();
    let error = worktree::stash_save(&open(&shop.repo("workshop")), "").unwrap_err();
    assert!(error.to_string().contains("clean"), "{error}");
}

#[test]
fn a_stash_can_become_a_branch() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "travail\n").unwrap();
    worktree::stash_save(&open(&root), "pour une branche").unwrap();

    let reference = worktree::stash_list(&open(&root))[0].reference.clone();
    let message =
        worktree::stash_branch(&open(&root), &reference, "feature/depuis-stash").unwrap();
    assert!(message.contains("feature/depuis-stash"), "{message}");
    assert_eq!(gitlog::current_branch(&open(&root)), "feature/depuis-stash");
    assert!(worktree::stash_list(&open(&root)).is_empty());
}

#[test]
fn a_stash_reads_like_any_other_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "mise de cote\n").unwrap();
    worktree::stash_save(&open(&root), "a lire").unwrap();

    let sha = worktree::stash_list(&open(&root))[0].sha.clone();
    let repo = open(&root);
    let detail = gitlog::commit_detail(&repo, &sha).unwrap();
    let paths: Vec<&str> = detail.files.iter().map(|file| file.path.as_str()).collect();
    assert_eq!(paths, ["calc.py"]);

    let patch = gitlog::commit_patch(&repo, &sha, Some("calc.py"), false, 3).unwrap();
    assert!(patch.diff.contains("mise de cote"), "{}", patch.diff);
}

// ------------------------------------------------------------------ blame

#[test]
fn every_line_names_who_last_touched_it() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "poeme.txt", "un\ndeux\n", "premier jet");
    commit_file(&root, "poeme.txt", "un\ndeux modifie\n", "retouche");

    let blame = gitlog::blame(&open(&root), "poeme.txt", "").unwrap();
    let texts: Vec<&str> = blame.lines.iter().map(|line| line.text.as_str()).collect();
    assert_eq!(texts, ["un", "deux modifie"]);
    assert_eq!(blame.lines[0].summary, "premier jet");
    assert_eq!(blame.lines[1].summary, "retouche");
    assert_ne!(blame.lines[0].sha, blame.lines[1].sha);
    assert_eq!(blame.lines[0].author, "Tester");
    assert!(blame.lines[0].date.starts_with("20"), "{}", blame.lines[0].date);
    assert_eq!(blame.lines[0].short, blame.lines[0].sha[..7]);
}

#[test]
fn blame_can_be_asked_at_an_older_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "poeme.txt", "un\n", "premier jet");
    let older = head_sha(&root);
    commit_file(&root, "poeme.txt", "un\ndeux\n", "suite");

    assert_eq!(gitlog::blame(&open(&root), "poeme.txt", &older).unwrap().lines.len(), 1);
}

#[test]
fn uncommitted_work_is_blamed_on_nobody() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "poeme.txt", "un\n", "premier jet");
    std::fs::write(root.join("poeme.txt"), "un\nligne en cours\n").unwrap();

    let blame = gitlog::blame(&open(&root), "poeme.txt", "").unwrap();
    assert_eq!(blame.lines[1].sha, "0".repeat(40));
    assert_eq!(blame.lines[1].text, "ligne en cours");
}

#[test]
fn a_blame_path_outside_the_repository_is_refused() {
    let shop = Workshop::new();
    let error = gitlog::blame(&open(&shop.repo("workshop")), "../../etc/passwd", "").unwrap_err();
    assert!(error.to_string().contains("outside the repository"), "{error}");
}

#[test]
fn an_unknown_file_cannot_be_attributed() {
    let shop = Workshop::new();
    let error = gitlog::blame(&open(&shop.repo("workshop")), "jamais-vu.py", "").unwrap_err();
    assert!(error.to_string().contains("cannot attribute"), "{error}");
}

/// The case `blame_file` cannot handle at all: an author with no email anywhere in the history.
#[test]
fn blame_survives_a_history_with_no_email() {
    let shop = Workshop::new();
    let root = shop.repo_without_email("sans-email");
    let blame = gitlog::blame(&open(&root), "calc.py", "").unwrap();
    assert!(!blame.lines.is_empty());
    assert_eq!(blame.lines[0].author, "Arthur");
    assert_eq!(blame.lines[0].summary, "initial");
}

#[test]
fn blame_survives_a_latin1_file() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("latin.txt"), b"caf\xe9 en latin-1\n").unwrap();
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-q", "-m", "un fichier latin-1"]);

    assert!(!gitlog::blame(&open(&root), "latin.txt", "").unwrap().lines.is_empty());
}
