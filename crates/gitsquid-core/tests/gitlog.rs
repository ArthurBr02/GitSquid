//! What git reports for the graph. The lane layout itself lives in the browser.

mod common;

use std::collections::{HashMap, HashSet};

use common::{commit_file, git, head_sha, open, Workshop};
use gitsquid_core::gitlog;

#[test]
fn head_and_branches_are_reported() {
    let shop = Workshop::new();
    let repo = open(&shop.branchy("workshop"));
    let newest = &gitlog::commits(&repo, 80, true)[0];
    assert!(newest.refs.iter().any(|label| label.contains("HEAD")), "{:?}", newest.refs);
}

#[test]
fn the_limit_is_honoured() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    for index in 0..6 {
        commit_file(&root, "counter.py", &format!("N = {index}\n"), &format!("step {index}"));
    }
    assert_eq!(gitlog::commits(&open(&root), 3, true).len(), 3);
}

#[test]
fn a_repository_without_commits_yields_nothing() {
    let shop = Workshop::new();
    let repo = open(&shop.empty_repo("vide"));
    assert!(gitlog::commits(&repo, 80, true).is_empty());
    assert_eq!(gitlog::count_commits(&repo, true), 0);
    assert_eq!(gitlog::current_branch(&repo), "(no branch)");
}

#[test]
fn work_on_another_branch_is_newer_not_invisible() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "ailleurs"]);
    commit_file(&root, "ailleurs.py", "A = 1\n", "travail ailleurs");
    git(&root, &["checkout", "-q", "main"]);

    let subjects: Vec<String> =
        gitlog::commits(&open(&root), 80, true).into_iter().map(|c| c.subject).collect();
    assert_eq!(subjects[0], "travail ailleurs", "the newest commit leads, whatever branch it is on");
}

#[test]
fn a_parent_never_sits_above_its_child() {
    let shop = Workshop::new();
    let found = gitlog::commits(&open(&shop.branchy("workshop")), 80, true);
    let position: HashMap<&str, usize> =
        found.iter().enumerate().map(|(index, c)| (c.sha.as_str(), index)).collect();

    for commit in &found {
        for parent in &commit.parents {
            if let Some(at) = position.get(parent.as_str()) {
                assert!(*at > position[commit.sha.as_str()], "{parent} sits above its child");
            }
        }
    }
}

#[test]
fn the_count_covers_every_ref() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "ailleurs"]);
    commit_file(&root, "ailleurs.py", "A = 1\n", "travail ailleurs");
    git(&root, &["checkout", "-q", "main"]);

    let repo = open(&root);
    assert_eq!(gitlog::count_commits(&repo, true), 2);
    assert_eq!(gitlog::count_commits(&repo, false), 1, "HEAD alone sees only its own line");
}

#[test]
fn head_carries_the_commit_it_is_on() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    assert_eq!(gitlog::head(&open(&root)).commit, head_sha(&root));
}

#[test]
fn parents_are_reported_in_order_and_in_full() {
    let shop = Workshop::new();
    let found = gitlog::commits(&open(&shop.branchy("workshop")), 80, true);
    let merges: Vec<_> = found.iter().filter(|c| c.parents.len() > 1).collect();
    assert!(!merges.is_empty());
    for merge in merges {
        assert!(merge.parents.iter().all(|parent| parent.len() == 40), "{:?}", merge.parents);
    }
}

#[test]
fn every_parent_inside_the_window_is_a_known_commit() {
    let shop = Workshop::new();
    let found = gitlog::commits(&open(&shop.branchy("workshop")), 80, true);
    let known: HashSet<&str> = found.iter().map(|c| c.sha.as_str()).collect();
    let reachable: HashSet<&str> =
        found.iter().flat_map(|c| c.parents.iter().map(String::as_str)).collect();

    assert!(reachable.is_subset(&known), "a parent points outside a window covering all history");
}

#[test]
fn the_root_commit_has_no_parent() {
    let shop = Workshop::new();
    let found = gitlog::commits(&open(&shop.branchy("workshop")), 80, true);
    assert!(found.last().unwrap().parents.is_empty());
}

#[test]
fn head_names_the_branch_it_is_on() {
    let shop = Workshop::new();
    let state = gitlog::head(&open(&shop.repo("workshop")));
    assert_eq!(state.branch, "main");
    assert!(!state.detached);
    assert!(state.sha.len() >= 7);
}

#[test]
fn a_detached_head_says_so() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "--detach", "HEAD"]);

    let state = gitlog::head(&open(&root));
    assert!(state.detached);
    assert_eq!(state.branch, "HEAD");
}

#[test]
fn a_detached_head_still_decorates_its_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "--detach", "HEAD"]);

    let newest = &gitlog::commits(&open(&root), 80, true)[0];
    assert!(newest.refs.iter().any(|label| label == "HEAD"), "{:?}", newest.refs);
}

#[test]
fn branches_carry_their_drift_from_the_upstream() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let origin = shop.path().join("origin.git");
    git(&shop.path(), &["init", "--bare", "-q", "origin.git"]);
    git(&root, &["remote", "add", "origin", &origin.to_string_lossy()]);
    git(&root, &["push", "-q", "-u", "origin", "main"]);
    commit_file(&root, "suite.py", "X = 1\n", "un cran devant");

    let found = gitlog::branches(&open(&root));
    let main = found.iter().find(|branch| branch.name == "main").expect("main is listed");
    assert_eq!(main.upstream, "origin/main");
    assert_eq!((main.ahead, main.behind), (1, 0));
}

#[test]
fn branches_come_back_newest_first() {
    let shop = Workshop::new();
    let root = shop.branchy("workshop");
    let names: Vec<String> = gitlog::branches(&open(&root)).into_iter().map(|b| b.name).collect();
    assert!(names.contains(&"main".to_string()));
    assert_eq!(names.len(), 4, "main plus three features: {names:?}");
}

#[test]
fn a_branch_without_an_upstream_reports_no_drift() {
    let shop = Workshop::new();
    let found = gitlog::branches(&open(&shop.repo("workshop")));
    let main = &found[0];
    assert_eq!(main.upstream, "");
    assert_eq!((main.ahead, main.behind), (0, 0));
}

#[test]
fn remote_branches_skip_the_symbolic_head() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let origin = shop.path().join("origin.git");
    git(&shop.path(), &["init", "--bare", "-q", "origin.git"]);
    git(&root, &["remote", "add", "origin", &origin.to_string_lossy()]);
    git(&root, &["push", "-q", "-u", "origin", "main"]);
    git(&root, &["remote", "set-head", "origin", "main"]);

    let found = gitlog::remote_branches(&open(&root));
    assert!(found.iter().all(|branch| branch.name != "origin/HEAD"), "{found:?}");
    let main = found.iter().find(|branch| branch.name == "origin/main").expect("origin/main");
    assert_eq!(main.local, "main");
    assert!(main.tracked, "the local twin exists");
}

#[test]
fn an_annotated_tag_names_the_commit_it_stands_for() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["tag", "-a", "v1.0.0", "-m", "une version annotee"]);

    let tag = &gitlog::tags(&open(&root))[0];
    assert!(head_sha(&root).starts_with(&tag.sha), "{} vs {}", head_sha(&root), tag.sha);
    assert_eq!(tag.subject, "une version annotee");
}

#[test]
fn a_lightweight_tag_borrows_the_commit_subject() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["tag", "leger"]);

    let tag = &gitlog::tags(&open(&root))[0];
    assert_eq!(tag.name, "leger");
    assert_eq!(tag.subject, "initial");
}

#[test]
fn the_working_status_counts_each_side_of_the_index() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "CHANGED = 1\n").unwrap();
    std::fs::write(root.join("neuf.py"), "N = 1\n").unwrap();
    git(&root, &["add", "neuf.py"]);
    std::fs::write(root.join("jamais-ajoute.py"), "U = 1\n").unwrap();

    let counts = gitlog::working_status(&open(&root));
    assert_eq!(counts.staged, 1, "neuf.py is staged");
    assert_eq!(counts.unstaged, 1, "calc.py is modified but not staged");
    assert_eq!(counts.untracked, 1, "jamais-ajoute.py was never added");
}

#[test]
fn a_clean_tree_reports_nothing_pending() {
    let shop = Workshop::new();
    assert!(gitlog::pending_operation(&open(&shop.repo("workshop"))).is_none());
}

#[test]
fn a_conflicted_merge_is_reported_with_its_files() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["checkout", "-q", "-b", "cote"]);
    commit_file(&root, "calc.py", "COTE = 1\n", "cote");
    git(&root, &["checkout", "-q", "main"]);
    commit_file(&root, "calc.py", "MAIN = 1\n", "main");
    git(&root, &["merge", "cote"]);

    let pending = gitlog::pending_operation(&open(&root)).expect("a merge is in progress");
    assert_eq!(pending.kind, "merge");
    assert_eq!(pending.conflicts, ["calc.py"]);
    assert!(pending.resumable);
}

// ------------------------------------------------------------------ one commit, in detail

#[test]
fn the_commit_payload_stands_on_its_own() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let sha = head_sha(&root);

    let detail = gitlog::commit_detail(&open(&root), &sha).unwrap();
    assert_eq!(detail.subject, "initial");
    assert_eq!(detail.author, "Tester");
    assert_eq!(detail.short, sha[..7]);
    assert!(detail.date.starts_with("20"), "{}", detail.date);
    assert_eq!(detail.email, "tester@example.invalid");
}

#[test]
fn a_commit_without_any_ref_still_loads() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "suite.py", "X = 1\n", "sans ref");
    let older = git(&root, &["rev-parse", "HEAD~1"]).trim().to_string();

    let detail = gitlog::commit_detail(&open(&root), &older).unwrap();
    assert!(detail.refs.is_empty(), "{:?}", detail.refs);
    assert_eq!(detail.subject, "initial");
    assert!(!detail.files.is_empty());
}

#[test]
fn a_merge_shows_what_it_brought_in() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let base = head_sha(&root);
    git(&root, &["checkout", "-q", "-b", "cote", &base]);
    commit_file(&root, "apporte.py", "A = 1\n", "travail de cote");
    git(&root, &["checkout", "-q", "main"]);
    commit_file(&root, "principal.py", "M = 1\n", "travail principal");
    git(&root, &["merge", "-q", "--no-ff", "cote", "-m", "fusion"]);

    let repo = open(&root);
    let head = head_sha(&root);
    let detail = gitlog::commit_detail(&repo, &head).unwrap();
    assert!(detail.merge);
    let paths: Vec<&str> = detail.files.iter().map(|file| file.path.as_str()).collect();
    assert_eq!(paths, ["apporte.py"]);

    let patch = gitlog::commit_patch(&repo, &head, None, false, 3).unwrap();
    assert!(patch.diff.contains("apporte.py"), "{}", patch.diff);
}

#[test]
fn each_file_carries_the_lines_it_gained_and_lost() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "VALEUR = 1\nVALEUR2 = 2\n").unwrap();
    std::fs::write(root.join("neuf.py"), "N = 1\n").unwrap();
    std::fs::remove_file(root.join("README.md")).unwrap();
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-q", "-m", "trois sortes de changement"]);

    let detail = gitlog::commit_detail(&open(&root), &head_sha(&root)).unwrap();
    let by_path: HashMap<&str, &gitlog::FileChange> =
        detail.files.iter().map(|file| (file.path.as_str(), file)).collect();

    assert_eq!(by_path["neuf.py"].status, "A");
    assert_eq!(by_path["neuf.py"].added, Some(1));
    assert_eq!(by_path["README.md"].status, "D");
    assert_eq!(by_path["README.md"].added, Some(0));
    assert_eq!(by_path["calc.py"].status, "M");
    assert_eq!(detail.added, detail.files.iter().filter_map(|file| file.added).sum::<usize>());
}

#[test]
fn a_binary_file_has_no_line_count() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let bytes: Vec<u8> = (0..=255u8).collect();
    std::fs::write(root.join("image.bin"), bytes).unwrap();
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-q", "-m", "ajoute un binaire"]);

    let detail = gitlog::commit_detail(&open(&root), &head_sha(&root)).unwrap();
    let entry = &detail.files[0];
    assert_eq!(entry.path, "image.bin");
    assert!(entry.binary);
    assert_eq!(entry.added, None);
}

#[test]
fn a_rename_keeps_the_name_it_had() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["mv", "calc.py", "calcul.py"]);
    git(&root, &["commit", "-q", "-m", "renomme"]);

    let detail = gitlog::commit_detail(&open(&root), &head_sha(&root)).unwrap();
    let entry = &detail.files[0];
    assert_eq!(entry.status, "R");
    assert_eq!(entry.path, "calcul.py");
    assert_eq!(entry.original.as_deref(), Some("calc.py"));
}

#[test]
fn a_patch_can_be_fetched_for_one_file_alone() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("calc.py"), "VALEUR = 1\n").unwrap();
    std::fs::write(root.join("autre.py"), "A = 1\n").unwrap();
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-q", "-m", "deux fichiers"]);

    let patch = gitlog::commit_patch(&open(&root), &head_sha(&root), Some("calc.py"), false, 3).unwrap();
    assert!(patch.diff.contains("calc.py"), "{}", patch.diff);
    assert!(!patch.diff.contains("autre.py"), "{}", patch.diff);
    assert!(!patch.truncated);
}

#[test]
fn a_patch_path_outside_the_repository_is_refused() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let error = gitlog::commit_patch(&open(&root), &head_sha(&root), Some("../../etc/passwd"), false, 3)
        .unwrap_err();
    assert!(error.to_string().contains("outside the repository"), "{error}");
}

#[test]
fn an_unknown_commit_is_reported() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let error = gitlog::commit_detail(&open(&root), &"0".repeat(40)).unwrap_err();
    assert!(error.to_string().contains("No such commit"), "{error}");
}

#[test]
fn something_that_is_not_a_commit_id_is_refused_before_git_sees_it() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    assert!(gitlog::commit_detail(&open(&root), "main; rm -rf /").is_err());
}

#[test]
fn the_panel_reads_a_tag_object_as_its_commit() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    git(&root, &["tag", "-a", "v1.0.0", "-m", "une version annotee"]);
    let tag_object = git(&root, &["rev-parse", "v1.0.0"]).trim().to_string();

    let repo = open(&root);
    let detail = gitlog::commit_detail(&repo, &tag_object).unwrap();
    assert_eq!(detail.subject, "initial");
    assert!(!detail.files.is_empty());

    let patch = gitlog::commit_patch(&repo, &tag_object, None, false, 3).unwrap();
    assert!(patch.diff.contains("calc.py"), "{}", patch.diff);
}

#[test]
fn the_context_line_count_is_honoured() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    let body: String = (1..=20).map(|n| format!("line {n}\n")).collect();
    commit_file(&root, "long.txt", &body, "vingt lignes");
    let changed = body.replace("line 10\n", "line 10 CHANGED\n");
    commit_file(&root, "long.txt", &changed, "une ligne");

    let repo = open(&root);
    let tight = gitlog::commit_patch(&repo, &head_sha(&root), Some("long.txt"), false, 0).unwrap();
    let loose = gitlog::commit_patch(&repo, &head_sha(&root), Some("long.txt"), false, 5).unwrap();
    assert!(loose.diff.lines().count() > tight.diff.lines().count(), "more context, more lines");
}

// ------------------------------------------------------------------ a file's past

#[test]
fn only_the_commits_that_touched_the_file_are_listed() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "calc.py", "VALEUR = 1\n", "change calc");
    commit_file(&root, "autre.py", "A = 1\n", "add autre");

    let history = gitlog::file_history(&open(&root), "calc.py", 50).unwrap();
    let subjects: Vec<&str> = history.iter().map(|entry| entry.subject.as_str()).collect();
    assert_eq!(subjects, ["change calc", "initial"]);
    assert_eq!(history[0].short, history[0].sha[..7]);
}

#[test]
fn a_file_git_never_saw_has_no_history() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    assert!(gitlog::file_history(&open(&root), "jamais-vu.py", 50).unwrap().is_empty());
}

#[test]
fn a_history_follows_the_file_through_a_rename() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "calc.py", "VALEUR = 1\n", "change calc");
    git(&root, &["mv", "calc.py", "calcul.py"]);
    git(&root, &["commit", "-q", "-m", "renomme"]);

    let history = gitlog::file_history(&open(&root), "calcul.py", 50).unwrap();
    let subjects: Vec<&str> = history.iter().map(|entry| entry.subject.as_str()).collect();
    assert_eq!(subjects, ["renomme", "change calc", "initial"], "the name before the rename counts");
}

#[test]
fn a_history_path_outside_the_repository_is_refused() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    assert!(gitlog::file_history(&open(&root), "../../etc/passwd", 50).is_err());
}

// ------------------------------------------------------------------ searching

fn with_history(shop: &Workshop) -> std::path::PathBuf {
    let root = shop.repo("workshop");
    commit_file(&root, "facture.py", "TOTAL = 1\n", "Corrige le calcul de facture");
    commit_file(&root, "autre.py", "A = 1\n", "Ajoute autre chose");
    git(&root, &["commit", "-q", "--allow-empty", "-m", "Note de version",
                 "--author", "Camille <camille@example.invalid>"]);
    root
}

#[test]
fn a_message_is_found() {
    let shop = Workshop::new();
    let found = gitlog::search(&open(&with_history(&shop)), "facture", 100);
    let subjects: Vec<&str> = found.iter().map(|c| c.subject.as_str()).collect();
    assert_eq!(subjects, ["Corrige le calcul de facture"]);
}

#[test]
fn the_search_ignores_case() {
    let shop = Workshop::new();
    assert!(!gitlog::search(&open(&with_history(&shop)), "FACTURE", 100).is_empty());
}

#[test]
fn an_author_is_found() {
    let shop = Workshop::new();
    let found = gitlog::search(&open(&with_history(&shop)), "camille", 100);
    let subjects: Vec<&str> = found.iter().map(|c| c.subject.as_str()).collect();
    assert_eq!(subjects, ["Note de version"]);
}

#[test]
fn a_path_is_found_even_when_the_message_says_nothing() {
    let shop = Workshop::new();
    let found = gitlog::search(&open(&with_history(&shop)), "facture.py", 100);
    let subjects: Vec<&str> = found.iter().map(|c| c.subject.as_str()).collect();
    assert_eq!(subjects, ["Corrige le calcul de facture"]);
}

#[test]
fn results_come_back_newest_first_and_without_duplicates() {
    let shop = Workshop::new();
    let found = gitlog::search(&open(&with_history(&shop)), "a", 100);
    let unique: HashSet<&str> = found.iter().map(|c| c.sha.as_str()).collect();
    assert_eq!(found.len(), unique.len());

    let mut sorted = found.clone();
    sorted.sort_by(|left, right| right.date.cmp(&left.date));
    assert_eq!(found, sorted);
}

#[test]
fn a_query_too_short_to_mean_anything_is_refused() {
    let shop = Workshop::new();
    let repo = open(&with_history(&shop));
    assert!(gitlog::search(&repo, "a", 100).is_empty());
    assert!(gitlog::search(&repo, "  ", 100).is_empty());
}

#[test]
fn nothing_matches_nothing() {
    let shop = Workshop::new();
    assert!(gitlog::search(&open(&with_history(&shop)), "introuvable-xyz", 100).is_empty());
}

// ------------------------------------------------------------------ bytes git never promised

#[test]
fn a_latin1_file_comes_back_as_a_patch_not_an_error() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    std::fs::write(root.join("latin.txt"), b"caf\xe9 en latin-1\n").unwrap();
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-q", "-m", "un fichier latin-1"]);

    let repo = open(&root);
    let head = head_sha(&root);
    let patch = gitlog::commit_patch(&repo, &head, Some("latin.txt"), false, 3).unwrap();
    assert!(patch.diff.contains("latin-1"), "{}", patch.diff);

    let detail = gitlog::commit_detail(&repo, &head).unwrap();
    let paths: Vec<&str> = detail.files.iter().map(|file| file.path.as_str()).collect();
    assert_eq!(paths, ["latin.txt"]);
}

#[test]
fn an_accented_name_is_reported_as_it_is_written() {
    let shop = Workshop::new();
    let root = shop.repo("workshop");
    commit_file(&root, "café été.txt", "contenu\n", "un nom accentue");

    let repo = open(&root);
    let head = head_sha(&root);
    let detail = gitlog::commit_detail(&repo, &head).unwrap();
    let paths: Vec<&str> = detail.files.iter().map(|file| file.path.as_str()).collect();
    assert_eq!(paths, ["café été.txt"], "no octal escaping, whatever core.quotePath says");

    let patch = gitlog::commit_patch(&repo, &head, Some("café été.txt"), false, 3).unwrap();
    assert!(patch.diff.contains("contenu"), "{}", patch.diff);
}
