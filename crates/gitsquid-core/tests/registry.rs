//! The multi-repository list: which repositories the interface can switch between.

mod common;

use common::Workshop;
use gitsquid_core::registry::MAX_REPOS;

#[test]
fn an_empty_registry_lists_nothing() {
    let shop = Workshop::new();
    assert!(shop.registry().known().is_empty());
}

#[test]
fn adding_registers_the_repository_root() {
    let shop = Workshop::new();
    let repo = shop.repo("workshop");
    let registry = shop.registry();

    assert_eq!(registry.add(&repo).unwrap(), repo);
    let names: Vec<String> = registry.known().into_iter().map(|entry| entry.name).collect();
    assert_eq!(names, ["workshop"]);
}

#[test]
fn adding_from_a_subdirectory_stores_the_root() {
    let shop = Workshop::new();
    let repo = shop.repo("workshop");
    let nested = repo.join("deep/deeper");
    std::fs::create_dir_all(&nested).unwrap();

    assert_eq!(shop.registry().add(&nested).unwrap(), repo);
}

#[test]
fn a_directory_that_is_not_a_repository_is_refused() {
    let shop = Workshop::new();
    let plain = shop.plain("rien");
    let error = shop.registry().add(&plain).unwrap_err();
    assert!(error.to_string().contains("not inside a Git repository"), "{error}");
}

#[test]
fn the_most_recent_repository_comes_first() {
    let shop = Workshop::new();
    let first = shop.repo("workshop");
    let second = shop.repo("second");
    let registry = shop.registry();

    registry.add(&first).unwrap();
    registry.add(&second).unwrap();

    let names: Vec<String> = registry.known().into_iter().map(|entry| entry.name).collect();
    assert_eq!(names, ["second", "workshop"]);
}

#[test]
fn adding_twice_does_not_duplicate() {
    let shop = Workshop::new();
    let repo = shop.repo("workshop");
    let registry = shop.registry();

    registry.add(&repo).unwrap();
    registry.add(&repo).unwrap();

    assert_eq!(registry.known().len(), 1);
}

#[test]
fn removing_leaves_the_repository_on_disk() {
    let shop = Workshop::new();
    let repo = shop.repo("workshop");
    let registry = shop.registry();
    registry.add(&repo).unwrap();

    assert!(registry.remove(&repo));
    assert!(registry.known().is_empty());
    assert!(repo.exists());
}

#[test]
fn removing_something_absent_reports_it() {
    let shop = Workshop::new();
    let repo = shop.repo("workshop");
    assert!(!shop.registry().remove(&repo));
}

#[test]
fn entries_report_whether_they_still_exist() {
    let shop = Workshop::new();
    let gone = shop.repo("disparu");
    let registry = shop.registry();
    registry.add(&gone).unwrap();

    std::fs::remove_dir_all(&gone).unwrap();

    assert!(!registry.known()[0].exists);
}

#[test]
fn a_corrupt_registry_file_is_ignored() {
    let shop = Workshop::new();
    let registry = shop.registry();
    std::fs::create_dir_all(registry.path().parent().unwrap()).unwrap();
    std::fs::write(registry.path(), "{not json").unwrap();

    assert!(registry.known().is_empty());
}

#[test]
fn a_registry_with_the_wrong_shape_is_ignored() {
    let shop = Workshop::new();
    let registry = shop.registry();
    std::fs::create_dir_all(registry.path().parent().unwrap()).unwrap();
    std::fs::write(registry.path(), r#"{"repos": "not-a-list"}"#).unwrap();

    assert!(registry.known().is_empty());
}

#[test]
fn the_list_is_capped() {
    let shop = Workshop::new();
    let registry = shop.registry();
    std::fs::create_dir_all(registry.path().parent().unwrap()).unwrap();
    let many: Vec<String> = (0..120).map(|n| format!("/tmp/r{n}")).collect();
    let payload = serde_json::json!({ "version": 1, "repos": many });
    std::fs::write(registry.path(), payload.to_string()).unwrap();

    assert_eq!(registry.known().len(), MAX_REPOS);
}

#[test]
fn adding_a_repository_writes_the_list_to_disk() {
    let shop = Workshop::new();
    let registry = shop.registry();
    registry.add(&shop.repo("neuf")).unwrap();

    assert!(registry.path().exists());
}

// ------------------------------------------------------------------ which one reopens

#[test]
fn nothing_reopens_when_nothing_was_ever_opened() {
    let shop = Workshop::new();
    assert_eq!(shop.registry().most_recent_existing(), None);
}

#[test]
fn the_last_one_opened_reopens() {
    let shop = Workshop::new();
    let first = shop.repo("premier");
    let last = shop.repo("dernier");
    let registry = shop.registry();
    registry.add(&first).unwrap();
    registry.add(&last).unwrap();

    assert_eq!(registry.most_recent_existing(), Some(last));
}

#[test]
fn a_repository_that_moved_away_is_skipped() {
    let shop = Workshop::new();
    let kept = shop.repo("garde");
    let gone = shop.repo("disparu");
    let registry = shop.registry();
    registry.add(&kept).unwrap();
    registry.add(&gone).unwrap();

    std::fs::rename(gone.join(".git"), gone.join(".git-gone")).unwrap();

    assert_eq!(registry.most_recent_existing(), Some(kept), "the newest one is no longer a repository");
}
