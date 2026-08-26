//! How the interface counts things. One rule, so nothing says "1 file(s)".

const IRREGULAR: &[(&str, &str)] = &[("is", "are"), ("was", "were")];

pub fn plural(count: usize, word: &str) -> String {
    pluralise(count, word, None)
}

pub fn plural_as(count: usize, word: &str, many: &str) -> String {
    pluralise(count, word, Some(many))
}

fn pluralise(count: usize, word: &str, many: Option<&str>) -> String {
    if count == 1 {
        return format!("{count} {word}");
    }
    if let Some(many) = many {
        return format!("{count} {many}");
    }
    match IRREGULAR.iter().find(|(one, _)| *one == word) {
        Some((_, irregular)) => format!("{count} {irregular}"),
        None => format!("{count} {word}s"),
    }
}

/// Files do not "still conflict" when there is one of them.
pub fn conflicts(count: usize) -> String {
    let verb = if count == 1 { "conflicts" } else { "conflict" };
    format!("{} still {verb}", plural(count, "file"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_is_singular() {
        assert_eq!(plural(1, "file"), "1 file");
    }

    #[test]
    fn none_and_many_are_plural() {
        assert_eq!(plural(0, "file"), "0 files");
        assert_eq!(plural(3, "file"), "3 files");
    }

    #[test]
    fn a_noun_phrase_pluralises_on_its_last_word() {
        assert_eq!(plural(2, "sample change"), "2 sample changes");
    }

    #[test]
    fn an_irregular_form_can_be_given() {
        assert_eq!(plural_as(2, "entry", "entries"), "2 entries");
    }

    #[test]
    fn a_known_irregular_needs_no_help() {
        assert_eq!(plural(2, "is"), "2 are");
    }

    #[test]
    fn one_file_conflicts_and_several_conflict() {
        assert_eq!(conflicts(1), "1 file still conflicts");
        assert_eq!(conflicts(3), "3 files still conflict");
    }
}
