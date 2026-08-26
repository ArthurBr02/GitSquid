// Every command must be declared here: tauri-build turns each name into the `allow-<name>`
// permission that capabilities/default.json grants. A command missing from this list is
// registered in generate_handler! and still refused by the ACL at runtime.
const COMMANDS: &[&str] = &[
    "pick_repository",
    "state",
    "graph",
    "worktree_view",
    "repos",
    "search",
    "file_history",
    "blame",
    "file_diff",
    "commit_detail",
    "commit_patch",
    "open_repo",
    "clone_repo",
    "forget_repo",
    "worktree_action",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
