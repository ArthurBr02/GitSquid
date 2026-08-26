//! Running `git` itself, for the operations libgit2 cannot do faithfully: anything that writes
//! a commit (it needs a signature libgit2 refuses to build from a degraded identity, and it must
//! run the repository's hooks), anything touching a remote (credential helpers, `~/.ssh/config`),
//! rebase (its on-disk state must stay readable by the command line), and blame.

use std::io::{Read, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use git2::Repository;

use crate::{Error, Result};

pub const QUICK: Duration = Duration::from_secs(60);
pub const REPLAY: Duration = Duration::from_secs(180);
pub const NETWORK: Duration = Duration::from_secs(180);
pub const CLONE: Duration = Duration::from_secs(900);

#[derive(Debug, Clone)]
pub struct Output {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

impl Output {
    pub fn ok(&self) -> bool {
        self.code == 0
    }

    /// Both streams together: git splits its reporting between them with no fixed rule.
    pub fn both(&self) -> String {
        format!("{}{}", self.stdout, self.stderr).trim().to_string()
    }
}

pub fn workdir(repo: &Repository) -> Result<&Path> {
    repo.workdir().ok_or_else(|| Error::git("This repository has no working tree."))
}

/// Read a pipe to the end on its own thread: a child that fills the buffer while we wait for it
/// to exit would deadlock against us.
fn drain<R: Read + Send + 'static>(stream: Option<R>) -> std::thread::JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut buffer = Vec::new();
        if let Some(mut stream) = stream {
            let _ = stream.read_to_end(&mut buffer);
        }
        buffer
    })
}

fn wait_for(child: &mut Child, timeout: Duration) -> Result<i32> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(status.code().unwrap_or(-1)),
            Ok(None) => {}
            Err(error) => return Err(Error::git(format!("git failed: {error}"))),
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(Error::git(format!(
                "git did not finish within {}s.",
                timeout.as_secs()
            )));
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

pub fn run_in(root: &Path, args: &[&str], stdin: Option<&str>, timeout: Duration) -> Result<Output> {
    let mut command = Command::new("git");
    command
        // quotePath=false: a file named café is a file named café, not "caf\303\251".
        .args(["-c", "core.quotePath=false", "-C"])
        .arg(root)
        .args(args)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // git needs the caller's environment for its identity and credential helpers, but must
        // never block on a prompt no one can answer.
        .env("GIT_TERMINAL_PROMPT", "0");

    let mut child = command.spawn().map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => Error::git("git is not installed or not on PATH."),
        _ => Error::git(format!("git failed: {error}")),
    })?;

    let out = drain(child.stdout.take());
    let err = drain(child.stderr.take());
    if let (Some(mut pipe), Some(text)) = (child.stdin.take(), stdin) {
        let text = text.to_string();
        std::thread::spawn(move || {
            let _ = pipe.write_all(text.as_bytes());
        });
    }

    let code = wait_for(&mut child, timeout)?;
    let stdout = out.join().unwrap_or_default();
    let stderr = err.join().unwrap_or_default();
    Ok(Output {
        code,
        // A latin-1 line in a diff must not fail the request that reads it.
        stdout: String::from_utf8_lossy(&stdout).into_owned(),
        stderr: String::from_utf8_lossy(&stderr).into_owned(),
    })
}

pub fn run(repo: &Repository, args: &[&str], timeout: Duration) -> Result<Output> {
    run_in(workdir(repo)?, args, None, timeout)
}

/// Run, and turn a non-zero exit into the message the interface will show.
pub fn checked(repo: &Repository, args: &[&str], action: &str, timeout: Duration) -> Result<String> {
    let result = run(repo, args, timeout)?;
    if !result.ok() {
        let detail = result.both();
        return Err(Error::git(format!(
            "{action}: {}",
            if detail.is_empty() { format!("git {} failed", args[0]) } else { detail }
        )));
    }
    Ok(result.stdout)
}

/// A network operation, with git's credential failure translated into one readable line.
pub fn remote_command(repo: &Repository, args: &[&str], action: &str) -> Result<String> {
    if crate::worktree::remotes(repo).is_empty() {
        return Err(Error::git("This repository has no remote configured."));
    }
    let result = run(repo, args, NETWORK)?;
    let text = result.both();
    if !result.ok() {
        if text.contains("could not read Username") || text.contains("Authentication failed") {
            return Err(Error::git(format!(
                "{action} needs credentials git could not supply without a prompt. Configure a \
                 credential helper or an SSH key, then try again."
            )));
        }
        return Err(Error::git(format!(
            "{action} failed: {}",
            if text.is_empty() { "unknown error" } else { &text }
        )));
    }
    Ok(if text.is_empty() { format!("{action} done — already up to date.") } else { text })
}

pub fn version() -> String {
    let mut command = Command::new("git");
    command.arg("--version").stdout(Stdio::piped()).stderr(Stdio::null()).stdin(Stdio::null());
    let Ok(output) = command.output() else { return "unknown".to_string() };
    String::from_utf8_lossy(&output.stdout)
        .trim()
        .strip_prefix("git version ")
        .unwrap_or("unknown")
        .to_string()
}
