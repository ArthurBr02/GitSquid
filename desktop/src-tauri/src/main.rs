// gitsquid desktop shell: one window over the engine it links against.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;

use tauri::menu::{AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

use commands::Session;

/// Ask for a folder without ever blocking the main thread: the dialog needs that thread to
/// run, so waiting on it there freezes the app (the spinning cursor). The picker is started
/// non-blocking and the answer arrives on a channel we await off the main thread.
async fn choose_folder(app: &tauri::AppHandle) -> Option<String> {
    let (sender, mut receiver) = tauri::async_runtime::channel(1);
    app.dialog()
        .file()
        .set_title("Choose a Git repository")
        .pick_folder(move |folder| {
            let _ = sender.blocking_send(folder.map(|path| path.to_string()));
        });
    receiver.recv().await.flatten()
}

/// Native folder picker, callable from the page so "Add a repository" is a real browse.
#[tauri::command]
async fn pick_repository(app: tauri::AppHandle) -> Option<String> {
    choose_folder(&app).await
}

fn build_menu(app: &tauri::AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let open = MenuItem::with_id(app, "open-repo", "Open Repository…", true, Some("CmdOrCtrl+O"))?;
    let reload = MenuItem::with_id(app, "reload", "Reload", true, Some("CmdOrCtrl+R"))?;

    let app_menu = Submenu::with_items(
        app,
        "GitSquid",
        true,
        &[
            &PredefinedMenuItem::about(app, Some("About GitSquid"), Some(AboutMetadata::default()))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, None)?,
            &PredefinedMenuItem::quit(app, None)?,
        ],
    )?;
    let repo_menu = Submenu::with_items(app, "Repository", true, &[&open, &reload])?;
    let edit_menu = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;

    Menu::with_items(app, &[&app_menu, &repo_menu, &edit_menu])
}

async fn open_repository(app: tauri::AppHandle) {
    let Some(path) = choose_folder(&app).await else {
        return;
    };
    match commands::open_repo(app.state::<Session>(), path) {
        Ok(answer) => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("location.reload()");
            }
            let message = answer.get("message").and_then(|value| value.as_str()).unwrap_or("");
            let _ = app.emit("repository-opened", message);
        }
        Err(error) => {
            app.dialog().message(error).title("Could not open that folder").show(|_| {});
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Session::new())
        .invoke_handler(tauri::generate_handler![
            pick_repository,
            commands::state,
            commands::graph,
            commands::worktree_view,
            commands::repos,
            commands::search,
            commands::file_history,
            commands::blame,
            commands::file_diff,
            commands::commit_detail,
            commands::commit_patch,
            commands::open_repo,
            commands::clone_repo,
            commands::forget_repo,
            commands::worktree_action,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            app.set_menu(build_menu(&handle)?)?;

            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("GitSquid")
                .inner_size(1440.0, 920.0)
                .min_inner_size(940.0, 620.0)
                .center()
                .build()?;
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open-repo" => {
                let handle = app.clone();
                tauri::async_runtime::spawn(open_repository(handle));
            }
            "reload" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.eval("location.reload()");
                }
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("GitSquid desktop failed to start");
}
