// gitsquid desktop shell: starts the local engine, then shows it in a native window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod engine;

use std::sync::Mutex;

use tauri::menu::{AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::DialogExt;

use engine::Engine;

pub struct Backend(pub Mutex<Engine>);

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
    let port = {
        let state = app.state::<Backend>();
        let engine = state.0.lock().expect("engine lock");
        engine.port()
    };
    let Some(port) = port else {
        app.dialog().message("The engine is not running yet.").title("GitSquid").show(|_| {});
        return;
    };

    match engine::open_repository(port, &path) {
        Ok(message) => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("location.reload()");
            }
            let _ = app.emit("repository-opened", message);
        }
        Err(error) => {
            app.dialog()
                .message(error)
                .title("Could not open that folder")
                .show(|_| {});
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend(Mutex::new(Engine::new())))
        .invoke_handler(tauri::generate_handler![pick_repository])
        .setup(|app| {
            let handle = app.handle().clone();
            app.set_menu(build_menu(&handle)?)?;

            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("GitSquid")
                .inner_size(1440.0, 920.0)
                .min_inner_size(940.0, 620.0)
                .center()
                .build()?;

            std::thread::spawn(move || {
                engine::start(handle, window);
            });
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
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::Destroyed) {
                let app = window.app_handle().clone();
                let state = app.state::<Backend>();
                let mut engine = state.0.lock().expect("engine lock");
                engine.stop();
            }
        })
        .run(tauri::generate_context!())
        .expect("GitSquid desktop failed to start");
}
