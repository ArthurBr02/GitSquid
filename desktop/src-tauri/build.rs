fn main() {
    // Declaring the command generates the `allow-pick-repository` permission that the
    // capability grants to the page — without it the ACL refuses the call.
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["pick_repository"]),
        ),
    )
    .expect("failed to run tauri-build");
}
