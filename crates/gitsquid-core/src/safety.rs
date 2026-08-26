//! Path and text guards. Nothing here touches git: these run before a request reaches it.

use std::sync::LazyLock;

use regex::Regex;

use crate::{Error, Result};

pub const REDACTED: &str = "[redacted]";

static SECRET_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"sk-ant-[A-Za-z0-9_\-]{8,}",
        r"\bgh[pousr]_[A-Za-z0-9]{16,}\b",
        r"\bAKIA[0-9A-Z]{12,}\b",
        r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b",
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b",
        r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    ]
    .iter()
    .map(|pattern| Regex::new(pattern).expect("a compiled secret pattern"))
    .collect()
});

/// `token: hunter2` and friends, where the label is worth keeping and the value is not.
static LABELLED_SECRET: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)\b\s*[:=]\s*["']?([^\s"'#,;]{6,})["']?"#,
    )
    .expect("a compiled labelled-secret pattern")
});

const SENSITIVE_NAMES: &[&str] = &[
    ".env", ".env.local", ".netrc", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_ed25519",
];
const SENSITIVE_SUFFIXES: &[&str] =
    &[".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk"];
/// Conventional templates that exist to be read and copied — they hold no secret.
const TEMPLATE_NAMES: &[&str] = &[".env.example", ".env.sample", ".env.template", ".env.dist"];

/// Strip credential-shaped substrings before anything is stored or displayed.
pub fn redact(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut out = text.to_string();
    for pattern in SECRET_PATTERNS.iter() {
        out = pattern.replace_all(&out, REDACTED).into_owned();
    }
    LABELLED_SECRET
        .replace_all(&out, |captures: &regex::Captures| {
            format!("{}={REDACTED}", &captures[1])
        })
        .into_owned()
}

fn file_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

pub fn is_sensitive_path(path: &str) -> bool {
    let name = file_name(path);
    if TEMPLATE_NAMES.contains(&name) {
        return false;
    }
    if SENSITIVE_NAMES.contains(&name) || name.starts_with(".env.") {
        return true;
    }
    let lowered = name.to_lowercase();
    SENSITIVE_SUFFIXES.iter().any(|suffix| lowered.ends_with(suffix))
}

fn starts_with_drive_letter(path: &str) -> bool {
    let mut chars = path.chars();
    matches!((chars.next(), chars.next()), (Some(letter), Some(':')) if letter.is_ascii_alphabetic())
}

/// Reject absolute paths, parent traversal, NUL bytes, and writes into .git/.gitsquid.
pub fn is_safe_relative_path(path: &str) -> bool {
    if path.is_empty() || path.contains('\0') {
        return false;
    }
    let normalized = path.replace('\\', "/");
    let normalized = normalized.trim();
    if normalized.starts_with('/') || starts_with_drive_letter(normalized) {
        return false;
    }
    let parts: Vec<&str> = normalized
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    if parts.is_empty() || parts.contains(&"..") {
        return false;
    }
    !matches!(parts[0], ".git" | ".gitsquid")
}

/// Validate a free-text field coming from the interface.
pub fn clean_text_input(value: &str, max_len: usize, field: &str) -> Result<String> {
    let cleaned: String = value
        .chars()
        .filter(|ch| *ch == '\n' || *ch == '\t' || *ch >= ' ')
        .collect();
    let cleaned = cleaned.trim();
    if cleaned.is_empty() {
        return Err(Error::git(format!("{field} must not be empty.")));
    }
    if cleaned.chars().count() > max_len {
        return Err(Error::git(format!(
            "{field} must be at most {max_len} characters (got {}).",
            cleaned.chars().count()
        )));
    }
    Ok(cleaned.to_string())
}

/// Bounds-check a patch without touching its bytes — whitespace is load-bearing in a diff.
pub fn clean_patch_input(value: &str, max_len: usize, field: &str) -> Result<String> {
    if value.is_empty() || value.contains('\0') {
        return Err(Error::git(format!(
            "{field} must be non-empty text without NUL bytes."
        )));
    }
    if value.trim().is_empty() {
        return Err(Error::git(format!("{field} must not be blank.")));
    }
    if value.chars().count() > max_len {
        return Err(Error::git(format!(
            "{field} must be at most {max_len} characters (got {}).",
            value.chars().count()
        )));
    }
    Ok(value.to_string())
}

pub fn tail(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let kept: String = text
        .chars()
        .skip(text.chars().count() - limit)
        .collect();
    format!("…[truncated]\n{kept}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_relative_path_inside_the_repository_is_accepted() {
        assert!(is_safe_relative_path("calc.py"));
        assert!(is_safe_relative_path("src/deep/module.rs"));
        assert!(is_safe_relative_path("./calc.py"));
    }

    #[test]
    fn absolute_and_traversing_paths_are_refused() {
        for path in ["/etc/passwd", "../escape.py", "src/../../escape.py", "C:/Windows/system32"] {
            assert!(!is_safe_relative_path(path), "{path} should be refused");
        }
    }

    #[test]
    fn the_state_directories_are_refused() {
        assert!(!is_safe_relative_path(".git/config"));
        assert!(!is_safe_relative_path(".gitsquid/gitsquid.db"));
        // Only as the first segment: a file that merely mentions them is fine.
        assert!(is_safe_relative_path("docs/.git/notes.md"));
    }

    #[test]
    fn an_empty_or_nul_bearing_path_is_refused() {
        assert!(!is_safe_relative_path(""));
        assert!(!is_safe_relative_path("calc\0.py"));
        assert!(!is_safe_relative_path("."));
    }

    #[test]
    fn a_backslash_path_is_normalised_before_judging() {
        assert!(!is_safe_relative_path("..\\escape.py"));
        assert!(is_safe_relative_path("src\\module.rs"));
    }

    #[test]
    fn credential_files_are_recognised_anywhere_in_the_tree() {
        for path in ["deploy/id_rsa", ".env", "keys/server.pem", "a/b/.env.production"] {
            assert!(is_sensitive_path(path), "{path} should be sensitive");
        }
    }

    #[test]
    fn templates_are_not_credential_files() {
        for path in [".env.example", ".env.sample", ".env.template", ".env.dist"] {
            assert!(!is_sensitive_path(path), "{path} is a template");
        }
    }

    #[test]
    fn an_ordinary_source_file_is_not_sensitive() {
        assert!(!is_sensitive_path("src/main.rs"));
        assert!(!is_sensitive_path("keychain.rs"));
    }

    #[test]
    fn a_suffix_match_ignores_case() {
        assert!(is_sensitive_path("certs/SERVER.PEM"));
    }

    #[test]
    fn known_token_shapes_are_redacted() {
        let text = "key sk-ant-abcdefgh12 and ghp_0123456789abcdefgh and AKIA0123456789ABCD";
        let out = redact(text);
        assert!(!out.contains("sk-ant-abcdefgh12"), "{out}");
        assert!(!out.contains("ghp_0123456789abcdefgh"), "{out}");
        assert!(!out.contains("AKIA0123456789ABCD"), "{out}");
    }

    #[test]
    fn a_labelled_secret_keeps_its_label() {
        assert_eq!(redact("password: hunter2000"), "password=[redacted]");
        assert_eq!(redact("api_key = abcdef123456"), "api_key=[redacted]");
    }

    #[test]
    fn a_private_key_block_goes_whole() {
        let text = "before\n-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\n-----END RSA PRIVATE KEY-----\nafter";
        let out = redact(text);
        assert!(out.starts_with("before"), "{out}");
        assert!(out.ends_with("after"), "{out}");
        assert!(!out.contains("AAAA"), "{out}");
    }

    #[test]
    fn redacting_leaves_ordinary_prose_alone() {
        assert_eq!(redact("a commit that fixes the parser"), "a commit that fixes the parser");
        assert_eq!(redact(""), "");
    }

    #[test]
    fn text_input_is_trimmed_and_bounded() {
        assert_eq!(clean_text_input("  hello  ", 20, "Message").unwrap(), "hello");
        assert!(clean_text_input("   ", 20, "Message").is_err());
        assert!(clean_text_input("way too long", 4, "Message").is_err());
    }

    #[test]
    fn control_characters_are_dropped_but_newlines_survive() {
        assert_eq!(clean_text_input("a\u{7}b\nc", 20, "Message").unwrap(), "ab\nc");
    }

    #[test]
    fn a_patch_keeps_its_whitespace() {
        let patch = "@@ -1 +1 @@\n-  indented\n+\tindented\n";
        assert_eq!(clean_patch_input(patch, 500, "Patch").unwrap(), patch);
        assert!(clean_patch_input("   ", 500, "Patch").is_err());
        assert!(clean_patch_input("a\0b", 500, "Patch").is_err());
    }

    #[test]
    fn tail_keeps_the_end_and_says_so() {
        assert_eq!(tail("short", 10), "short");
        let out = tail("abcdefghij", 4);
        assert_eq!(out, "…[truncated]\nghij");
    }
}
