//! Clean a session title that came from outside (the agent sends it) before the pager keeps it.
//!
//! These four items are all that is left of the old `session::persistence` module, which also
//! read and wrote session files. The pager reads no session file now (a session is found through
//! the agent, over ACP), but it still cleans every title it receives, so the cleaning stays.

use std::borrow::Cow;

/// Maximum Unicode scalars in a session title. Counted after the control characters are
/// removed and the title is trimmed.
pub const MAX_TITLE_SCALARS: usize = 100;

/// C0/C1 controls plus the bidi/format overrides. Shared by the code that drops them from a
/// title that is kept and the code that replaces them with U+FFFD in a title that is shown, so
/// the character class cannot drift.
#[inline]
pub fn is_forbidden_title_char(c: char) -> bool {
    c.is_control()
        || matches!(
            c,
            '\u{200E}' | '\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}'
        )
}

/// Drop C0/C1 and bidi/format controls, then trim. A title must not carry a terminal escape
/// sequence or an RTL override into the name of a session.
///
/// Input that is already clean is borrowed (the trim is a subslice). Only a title that has a
/// forbidden character allocates.
fn sanitize_title(title: &str) -> Cow<'_, str> {
    if title.chars().any(is_forbidden_title_char) {
        let mut cleaned: String = title
            .chars()
            .filter(|c| !is_forbidden_title_char(*c))
            .collect();
        let trimmed = cleaned.trim();
        if trimmed.len() != cleaned.len() {
            cleaned = trimmed.to_string();
        }
        Cow::Owned(cleaned)
    } else {
        Cow::Borrowed(title.trim())
    }
}

/// Sanitize, then cap at [`MAX_TITLE_SCALARS`]. `None` when the result is blank. A title that
/// is too long is cut, not refused.
pub fn sanitize_and_cap_title(title: &str) -> Option<String> {
    let cleaned = sanitize_title(title);
    if cleaned.is_empty() {
        return None;
    }
    if cleaned.chars().count() <= MAX_TITLE_SCALARS {
        Some(cleaned.into_owned())
    } else {
        Some(cleaned.chars().take(MAX_TITLE_SCALARS).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_clean_title_is_kept_and_not_copied() {
        assert!(matches!(sanitize_title("fix the build"), Cow::Borrowed(_)));
        assert_eq!(
            sanitize_and_cap_title("fix the build").as_deref(),
            Some("fix the build")
        );
    }

    #[test]
    fn spaces_at_the_ends_are_trimmed() {
        assert_eq!(
            sanitize_and_cap_title("  fix the build \t").as_deref(),
            Some("fix the build")
        );
    }

    #[test]
    fn terminal_escapes_and_other_controls_are_dropped() {
        assert_eq!(
            sanitize_and_cap_title("a\u{1b}[31mb\u{7}c\nd").as_deref(),
            Some("a[31mbcd")
        );
        // C1 controls too.
        assert_eq!(sanitize_and_cap_title("a\u{9b}b").as_deref(), Some("ab"));
    }

    #[test]
    fn bidi_overrides_and_marks_are_dropped() {
        assert_eq!(
            sanitize_and_cap_title("a\u{202E}b\u{2066}c\u{200F}d").as_deref(),
            Some("abcd")
        );
    }

    #[test]
    fn spaces_that_a_dropped_character_leaves_at_the_ends_are_trimmed_too() {
        assert_eq!(
            sanitize_and_cap_title("\u{202E} title \u{200F}").as_deref(),
            Some("title")
        );
    }

    #[test]
    fn a_title_with_nothing_left_is_none() {
        assert_eq!(sanitize_and_cap_title(""), None);
        assert_eq!(sanitize_and_cap_title("   "), None);
        assert_eq!(sanitize_and_cap_title("\u{1b}\u{202E}\n"), None);
    }

    #[test]
    fn a_long_title_is_cut_at_a_number_of_scalars_and_not_in_a_character() {
        // Each of these is one scalar and more than one byte.
        let long: String = "é".repeat(MAX_TITLE_SCALARS + 20);
        let cut = sanitize_and_cap_title(&long).unwrap();
        assert_eq!(cut.chars().count(), MAX_TITLE_SCALARS);
        assert!(cut.chars().all(|c| c == 'é'));

        let exact: String = "x".repeat(MAX_TITLE_SCALARS);
        assert_eq!(
            sanitize_and_cap_title(&exact).as_deref(),
            Some(exact.as_str())
        );
    }

    #[test]
    fn the_forbidden_class_is_the_one_the_pager_uses_to_draw_a_title() {
        for c in [
            '\u{0}', '\u{1b}', '\u{7f}', '\u{9b}', '\u{200E}', '\u{202A}', '\u{2069}',
        ] {
            assert!(is_forbidden_title_char(c), "{c:?}");
        }
        for c in ['a', ' ', 'é', '日', '🦀', '\u{200D}'] {
            assert!(!is_forbidden_title_char(c), "{c:?}");
        }
    }
}
