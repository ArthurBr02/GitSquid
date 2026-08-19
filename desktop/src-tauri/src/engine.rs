//! Supervises the `gitsquid ui` process that does the real work.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, WebviewWindow};

const READY_TIMEOUT: Duration = Duration::from_secs(25);
const HOST: Ipv4Addr = Ipv4Addr::LOCALHOST;

#[derive(Default)]
pub struct Engine {
    child: Option<Child>,
    port: Option<u16>,
}

impl Engine {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn port(&self) -> Option<u16> {
        self.port
    }

    pub fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.port = None;
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Where to find the `gitsquid` executable, most explicit first.
fn candidates() -> Vec<PathBuf> {
    let mut found = Vec::new();
    for name in ["GITSQUID_BIN", "GITIA_BIN"] {
        if let Ok(explicit) = std::env::var(name) {
            if !explicit.trim().is_empty() {
                found.push(PathBuf::from(explicit));
            }
        }
    }
    // The project this shell was built from: desktop/src-tauri -> project root.
    let project = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(PathBuf::from);
    if let Some(root) = project {
        for venv in [".venv", ".venv-prod"] {
            found.push(root.join(venv).join("bin/gitsquid"));
            found.push(root.join(venv).join("bin/gitia"));
        }
    }
    found.push(PathBuf::from("gitsquid"));
    // The engine was called gitia before the rename; an older install still works.
    found.push(PathBuf::from("gitia"));
    found
}

fn resolve_binary() -> Option<PathBuf> {
    for candidate in candidates() {
        if candidate.is_absolute() {
            if candidate.is_file() {
                return Some(candidate);
            }
        } else if Command::new(&candidate)
            .arg("--help")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
        {
            return Some(candidate);
        }
    }
    None
}

fn free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind(SocketAddrV4::new(HOST, 0))?;
    Ok(listener.local_addr()?.port())
}

fn wait_until_ready(port: u16, child: &mut Child) -> Result<(), String> {
    let deadline = Instant::now() + READY_TIMEOUT;
    while Instant::now() < deadline {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("the engine exited early ({status})"));
        }
        if TcpStream::connect_timeout(
            &SocketAddrV4::new(HOST, port).into(),
            Duration::from_millis(250),
        )
        .is_ok()
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    Err(format!("no answer on port {port} after {}s", READY_TIMEOUT.as_secs()))
}

fn collect<R: Read + Send + 'static>(stream: R, log: Arc<Mutex<String>>) {
    std::thread::spawn(move || {
        for line in BufReader::new(stream).lines().map_while(Result::ok) {
            if let Ok(mut buffer) = log.lock() {
                if buffer.len() < 8_000 {
                    buffer.push_str(&line);
                    buffer.push('\n');
                }
            }
        }
    });
}

fn report(window: &WebviewWindow, message: &str, error: Option<&str>, output: &str) {
    let payload = serde_json::json!({
        "message": message,
        "error": error,
        "output": output,
    });
    let _ = window.eval(format!("window.gitsquidStatus({payload})"));
}

pub fn start(app: AppHandle, window: WebviewWindow) {
    let Some(binary) = resolve_binary() else {
        report(
            &window,
            "",
            Some("gitsquid was not found on this machine."),
            "Looked for $GITSQUID_BIN, the project's .venv/bin/gitsquid, and `gitsquid` on PATH.\n\
             Install it with ./scripts/install.sh, then reopen this window.",
        );
        return;
    };

    let port = match free_port() {
        Ok(port) => port,
        Err(error) => {
            report(&window, "", Some("No local port was available."), &error.to_string());
            return;
        }
    };

    report(&window, "Starting the local engine…", None, "");

    let spawned = Command::new(&binary)
        .args(["ui", "--no-open", "--port", &port.to_string()])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();

    let mut child = match spawned {
        Ok(child) => child,
        Err(error) => {
            report(
                &window,
                "",
                Some(&format!("Could not run {}", binary.display())),
                &error.to_string(),
            );
            return;
        }
    };

    // Keep the engine's own messages so a failure can be shown instead of swallowed.
    let log = Arc::new(Mutex::new(String::new()));
    if let Some(stream) = child.stderr.take() {
        collect(stream, Arc::clone(&log));
    }
    if let Some(stream) = child.stdout.take() {
        collect(stream, Arc::clone(&log));
    }

    match wait_until_ready(port, &mut child) {
        Ok(()) => {
            if let Ok(state) = app.state::<crate::Backend>().0.lock().as_mut() {
                state.child = Some(child);
                state.port = Some(port);
            }
            let url = format!("http://127.0.0.1:{port}/");
            let _ = window.eval(format!("location.replace('{url}')"));
        }
        Err(reason) => {
            let _ = child.kill();
            let output = log.lock().map(|buffer| buffer.clone()).unwrap_or_default();
            report(&window, "", Some(&format!("The engine did not answer: {reason}")), &output);
        }
    }
}

/// Ask the running engine to switch repository. Small enough to speak HTTP by hand.
pub fn open_repository(port: u16, path: &str) -> Result<String, String> {
    let body = serde_json::json!({ "path": path }).to_string();
    let request = format!(
        "POST /api/repos/open HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );

    let mut stream = TcpStream::connect_timeout(
        &SocketAddrV4::new(HOST, port).into(),
        Duration::from_secs(5),
    )
    .map_err(|error| format!("The engine is unreachable: {error}"))?;
    stream
        .write_all(request.as_bytes())
        .map_err(|error| error.to_string())?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| error.to_string())?;

    let (head, payload) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| "The engine sent a malformed answer.".to_string())?;
    let value: serde_json::Value = serde_json::from_str(payload.trim()).unwrap_or_default();

    if head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200") {
        return Ok(value
            .get("message")
            .and_then(|message| message.as_str())
            .unwrap_or("Repository opened.")
            .to_string());
    }
    Err(value
        .get("error")
        .and_then(|error| error.as_str())
        .unwrap_or("That folder is not a Git repository.")
        .to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_free_port_can_actually_be_bound() {
        let port = free_port().expect("a port");
        assert!(port > 0);
        TcpListener::bind(SocketAddrV4::new(HOST, port)).expect("the port is really free");
    }

    #[test]
    fn candidates_end_with_the_bare_names_so_path_is_the_last_resort() {
        let found = candidates();
        assert_eq!(found.last().unwrap(), &PathBuf::from("gitia"), "the pre-rename name comes last");
        assert!(found.contains(&PathBuf::from("gitsquid")));
        assert!(found.iter().any(|path| path.ends_with(".venv/bin/gitsquid")));
    }

    #[test]
    fn an_explicit_binary_wins() {
        std::env::set_var("GITSQUID_BIN", "/somewhere/gitsquid");
        let found = candidates();
        std::env::remove_var("GITSQUID_BIN");
        assert_eq!(found.first().unwrap(), &PathBuf::from("/somewhere/gitsquid"));
    }

    #[test]
    fn opening_a_repository_reports_an_unreachable_engine() {
        // A port nothing listens on: the shell must explain, not panic.
        let port = free_port().expect("a port");
        let error = open_repository(port, "/tmp").expect_err("no engine is listening");
        assert!(error.contains("unreachable"), "{error}");
    }
}
