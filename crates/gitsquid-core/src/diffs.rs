//! Reading and reversing unified diffs. The interface sends one hunk at a time; everything
//! here runs before that patch is allowed anywhere near the index.

use std::sync::LazyLock;

use regex::Regex;

use crate::safety::{is_safe_relative_path, is_sensitive_path};

static GIT_HEADER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^diff --git a/(.+?) b/(.+)$").expect("git header pattern"));
static PLUS_FILE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^\+\+\+ (?:b/)?(.+?)\s*$").expect("+++ pattern"));
static MINUS_FILE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^--- (?:a/)?(.+?)\s*$").expect("--- pattern"));
static HUNK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@").expect("hunk header pattern")
});

pub fn normalize(diff: &str) -> String {
    let text = diff.replace("\r\n", "\n");
    let text = text.trim_matches('\n');
    if text.is_empty() {
        return String::new();
    }
    format!("{text}\n")
}

/// Every path the patch claims to touch, in the order it names them.
pub fn touched_files(diff: &str) -> Vec<String> {
    let mut paths: Vec<String> = Vec::new();
    let mut remember = |candidate: &str| {
        let path = candidate
            .trim()
            .strip_prefix("a/")
            .or_else(|| candidate.trim().strip_prefix("b/"))
            .unwrap_or(candidate.trim());
        if !path.is_empty() && path != "/dev/null" && !paths.iter().any(|seen| seen == path) {
            paths.push(path.to_string());
        }
    };

    for captures in GIT_HEADER.captures_iter(diff) {
        remember(&captures[1]);
        remember(&captures[2]);
    }
    for pattern in [&*PLUS_FILE, &*MINUS_FILE] {
        for captures in pattern.captures_iter(diff) {
            remember(&captures[1]);
        }
    }
    paths
}

/// Structural and path safety checks — run before the patch ever reaches git.
pub fn validate(diff: &str) -> Vec<String> {
    if diff.trim().is_empty() {
        return vec!["The patch is empty.".to_string()];
    }
    let mut problems = Vec::new();
    if !HUNK.is_match(diff) {
        problems.push("The patch has no @@ hunk header, so it is not a unified diff.".to_string());
    }
    let files = touched_files(diff);
    if files.is_empty() {
        problems.push("The patch does not name any file.".to_string());
    }
    for path in &files {
        if !is_safe_relative_path(path) {
            problems.push(format!(
                "Refusing path outside the repository or inside .git/.gitsquid: {path}"
            ));
        } else if is_sensitive_path(path) {
            problems.push(format!("Refusing to patch a credential file: {path}"));
        }
    }
    problems
}

// ------------------------------------------------------------------ reversal

/// Turn a unified diff into the patch that undoes it. libgit2 exposes no `--reverse`, so
/// unstaging and discarding a hunk both go through here first.
pub fn reverse(patch: &str) -> String {
    let lines: Vec<&str> = patch.split_inclusive('\n').collect();
    let mut out = String::with_capacity(patch.len());
    let mut index = 0;
    while index < lines.len() {
        let (body, end) = split_end(lines[index]);
        // `---` and `+++` swap their paths but keep their markers: libgit2 rejects a patch
        // whose `+++` comes first.
        if let (Some(old), Some(next)) = (body.strip_prefix("--- "), lines.get(index + 1)) {
            let (next_body, next_end) = split_end(next);
            if let Some(new) = next_body.strip_prefix("+++ ") {
                out.push_str("--- ");
                out.push_str(&swap_side(new, 'a'));
                out.push_str(end);
                out.push_str("+++ ");
                out.push_str(&swap_side(old, 'b'));
                out.push_str(next_end);
                index += 2;
                continue;
            }
        }
        out.push_str(&reverse_line(body));
        out.push_str(end);
        index += 1;
    }
    out
}

fn split_end(line: &str) -> (&str, &str) {
    match line.strip_suffix('\n') {
        Some(body) => (body, "\n"),
        None => (line, ""),
    }
}

fn swap_side(payload: &str, side: char) -> String {
    let path = payload.trim_end();
    if path == "/dev/null" {
        return path.to_string();
    }
    let bare = path.strip_prefix("a/").or_else(|| path.strip_prefix("b/")).unwrap_or(path);
    format!("{side}/{bare}")
}

fn reverse_line(line: &str) -> String {
    if let Some(rest) = line.strip_prefix("diff --git a/") {
        if let Some((old, new)) = rest.split_once(" b/") {
            return format!("diff --git a/{new} b/{old}");
        }
    }
    if let Some(rest) = line.strip_prefix("new file mode ") {
        return format!("deleted file mode {rest}");
    }
    if let Some(rest) = line.strip_prefix("deleted file mode ") {
        return format!("new file mode {rest}");
    }
    if let Some(rest) = line.strip_prefix("index ") {
        return reverse_index_line(rest);
    }
    if line.starts_with("@@") {
        return reverse_hunk_header(line);
    }
    if let Some(rest) = line.strip_prefix('+') {
        return format!("-{rest}");
    }
    if let Some(rest) = line.strip_prefix('-') {
        return format!("+{rest}");
    }
    line.to_string()
}

fn reverse_index_line(rest: &str) -> String {
    let (range, mode) = match rest.split_once(' ') {
        Some((range, mode)) => (range, Some(mode)),
        None => (rest, None),
    };
    let Some((old, new)) = range.split_once("..") else {
        return format!("index {rest}");
    };
    match mode {
        Some(mode) => format!("index {new}..{old} {mode}"),
        None => format!("index {new}..{old}"),
    }
}

/// `@@ -a,b +c,d @@ ctx` becomes `@@ -c,d +a,b @@ ctx`.
fn reverse_hunk_header(line: &str) -> String {
    let Some(rest) = line.strip_prefix("@@ ") else {
        return line.to_string();
    };
    let Some((ranges, tail)) = rest.split_once(" @@") else {
        return line.to_string();
    };
    let mut parts = ranges.split(' ');
    let (Some(old), Some(new)) = (parts.next(), parts.next()) else {
        return line.to_string();
    };
    let (Some(old), Some(new)) = (old.strip_prefix('-'), new.strip_prefix('+')) else {
        return line.to_string();
    };
    format!("@@ -{new} +{old} @@{tail}")
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n-old\n+new\n";

    #[test]
    fn touched_files_from_git_header() {
        assert_eq!(touched_files(GOOD), ["calc.py"]);
    }

    #[test]
    fn touched_files_from_bare_unified_diff() {
        let bare = "--- a/one.py\n+++ b/two.py\n@@ -1 +1 @@\n-a\n+b\n";
        assert_eq!(touched_files(bare), ["two.py", "one.py"]);
    }

    #[test]
    fn a_new_file_ignores_dev_null() {
        let created = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n";
        assert_eq!(touched_files(created), ["new.py"]);
    }

    #[test]
    fn normalize_ensures_one_trailing_newline() {
        assert_eq!(normalize("a\r\nb"), "a\nb\n");
        assert_eq!(normalize("a\n\n\n"), "a\n");
        assert_eq!(normalize("   "), "   \n");
        assert_eq!(normalize(""), "");
    }

    #[test]
    fn a_well_formed_diff_has_no_problems() {
        assert!(validate(GOOD).is_empty());
    }

    #[test]
    fn an_empty_patch_is_refused() {
        assert_eq!(validate("   "), ["The patch is empty."]);
    }

    #[test]
    fn prose_without_hunks_is_refused() {
        let problems = validate("I would change calc.py like this\n");
        assert!(problems.iter().any(|problem| problem.contains("not a unified diff")), "{problems:?}");
    }

    #[test]
    fn parent_traversal_is_refused() {
        let problems = validate(&GOOD.replace("calc.py", "../../etc/passwd"));
        assert!(problems.iter().any(|problem| problem.contains("outside the repository")), "{problems:?}");
    }

    #[test]
    fn absolute_paths_are_refused() {
        let patch = GOOD.replace("a/calc.py", "a//etc/passwd").replace("b/calc.py", "b//etc/passwd");
        assert!(!validate(&patch).is_empty());
    }

    #[test]
    fn writes_into_the_state_directories_are_refused() {
        for target in [".git/config", ".gitsquid/gitsquid.db"] {
            let problems = validate(&GOOD.replace("calc.py", target));
            assert!(problems.iter().any(|problem| problem.starts_with("Refusing path")), "{target}: {problems:?}");
        }
    }

    #[test]
    fn credential_files_are_refused() {
        let problems = validate(&GOOD.replace("calc.py", "deploy/id_rsa"));
        assert!(problems.iter().any(|problem| problem.contains("credential file")), "{problems:?}");
    }

    #[test]
    fn reversing_twice_returns_the_original() {
        assert_eq!(reverse(&reverse(GOOD)), GOOD);
    }

    #[test]
    fn reversal_swaps_the_body_lines() {
        let back = reverse(GOOD);
        assert!(back.contains("+old\n"), "{back}");
        assert!(back.contains("-new\n"), "{back}");
    }

    #[test]
    fn reversal_keeps_the_markers_in_order() {
        let back = reverse(GOOD);
        let minus = back.find("--- ").expect("a --- line");
        let plus = back.find("+++ ").expect("a +++ line");
        assert!(minus < plus, "--- must still come first:\n{back}");
    }

    #[test]
    fn reversal_swaps_the_hunk_ranges() {
        let patch = "--- a/x\n+++ b/x\n@@ -1,3 +1,5 @@ fn main\n ctx\n+added\n+added\n";
        assert!(reverse(patch).contains("@@ -1,5 +1,3 @@ fn main\n"), "{}", reverse(patch));
    }

    #[test]
    fn a_created_file_reverses_into_a_deletion() {
        let created = "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n";
        let back = reverse(created);
        assert!(back.contains("deleted file mode 100644\n"), "{back}");
        assert!(back.contains("--- a/new.py\n"), "{back}");
        assert!(back.contains("+++ /dev/null\n"), "{back}");
        assert!(back.contains("-x = 1\n"), "{back}");
    }

    #[test]
    fn a_rename_reverses_its_two_sides() {
        let renamed = "diff --git a/one.py b/two.py\n--- a/one.py\n+++ b/two.py\n@@ -1 +1 @@\n-a\n+b\n";
        let back = reverse(renamed);
        assert!(back.contains("diff --git a/two.py b/one.py\n"), "{back}");
        assert!(back.contains("--- a/two.py\n"), "{back}");
        assert!(back.contains("+++ b/one.py\n"), "{back}");
    }

    #[test]
    fn an_index_line_swaps_its_blobs() {
        let patch = "index 0123abc..def4567 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n";
        assert!(reverse(patch).starts_with("index def4567..0123abc 100644\n"), "{}", reverse(patch));
    }

    #[test]
    fn a_no_newline_marker_survives_untouched() {
        let patch = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n";
        assert!(reverse(patch).contains("\\ No newline at end of file\n"));
    }

    #[test]
    fn a_patch_without_a_trailing_newline_keeps_that_shape() {
        let patch = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b";
        let back = reverse(patch);
        assert!(!back.ends_with('\n'), "{back:?}");
        assert_eq!(reverse(&back), patch);
    }
}
