//! @-context detection: parses `@query` tokens from prompt text + cursor position.
//!
//! Given the prompt text and cursor position, determines whether the cursor is
//! inside an `@`-token and extracts the query string for fuzzy matching.
//!
//! ## Rules
//!
//! - The `@` must NOT be preceded by an alphanumeric character or underscore
//!   (avoids triggering on email addresses like `user@example.com`).
//! - The token extends from `@` to the first whitespace, comma, or semicolon.
//! - The cursor must be within the token range.
//! - The query is the text between `@` (exclusive) and the cursor.
//!
//! ## Special modes
//!
//! - **Dir mode**: query ends with `/` → restrict matches to directories only.
//! - **Hidden mode**: query starts with `!` → show hidden/gitignored files.

use std::ops::Range;

/// Context for the current @-completion token.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AtContext {
    /// Byte range in the input text (includes the `@` as the first character).
    pub range: Range<usize>,
    /// Cursor byte position within the input text.
    pub cursor: usize,
    /// Query string: text after `@` (and after `!` if hidden mode) up to cursor.
    pub query: String,
}

impl AtContext {
    /// Whether the query requests directory-only results (ends with `/`).
    pub fn is_dir_mode(&self) -> bool {
        self.query.ends_with('/')
    }

    /// Whether the query requests hidden/gitignored files (starts with `!`).
    pub fn is_hidden_mode(&self) -> bool {
        self.query.starts_with('!')
    }

    /// The effective query for the fuzzy matcher (strips leading `!`).
    pub fn matcher_query(&self) -> &str {
        self.query.strip_prefix('!').unwrap_or(&self.query)
    }

    /// Byte range covering only the path portion of the @-token: starts
    /// after the leading `@` and (in hidden mode) the `!` prefix, ends at
    /// the @-token end. This is the range that should be replaced when
    /// inserting a path while preserving the `@` and any hidden-mode
    /// marker (see `accept_file_search_result_no_space` and
    /// `FileSearchState::try_replace`).
    pub fn path_range(&self) -> Range<usize> {
        let prefix = 1 + if self.is_hidden_mode() { 1 } else { 0 };
        self.range.start + prefix..self.range.end
    }
}

/// Like [`detect`], but treats whitespace *inside* `drill_prefix` (the path of
/// the directory being drilled into) as part of the @-token, so `@my dir/` stays
/// one token. Self-validating: inert once the path content stops matching it.
pub fn detect_with_drill(
    text: &str,
    cursor: usize,
    drill_prefix: Option<&str>,
) -> Option<AtContext> {
    // Cursor must be within text bounds and on a char boundary.
    if cursor > text.len() || !text.is_char_boundary(cursor) {
        return None;
    }

    // Find the rightmost `@` before the cursor.
    let at_idx = text[..cursor].rfind('@')?;

    // Reject if `@` is preceded by alphanumeric or underscore (email-like).
    if let Some(ch) = text[..at_idx].chars().next_back()
        && (ch.is_alphanumeric() || ch == '_')
    {
        return None;
    }

    // Path content starts after `@` (+ optional `!` hidden-mode marker).
    let content_start = at_idx + 1;
    let after_bang = if text[content_start..].starts_with('!') {
        content_start + 1
    } else {
        content_start
    };
    // Whitespace inside the drilled prefix is path content, not a terminator.
    let internal_until = drill_prefix.and_then(|prefix| {
        text.get(after_bang..)
            .filter(|rest| rest.starts_with(prefix))
            .map(|_| after_bang + prefix.len())
    });

    // Find the end of the @-token: first whitespace, comma, or semicolon after `@`.
    let token_end = text[at_idx + 1..]
        .char_indices()
        .find_map(|(offset, ch)| {
            let abs = at_idx + 1 + offset;
            if (ch.is_whitespace() || matches!(ch, ',' | ';'))
                && internal_until.is_none_or(|until| abs >= until)
            {
                Some(abs)
            } else {
                None
            }
        })
        .unwrap_or(text.len());

    // Cursor must be within the @-token.
    if cursor > token_end {
        return None;
    }

    Some(AtContext {
        range: at_idx..token_end,
        cursor,
        query: text[at_idx + 1..cursor].to_owned(),
    })
}

/// Normalize a display path (strip leading `./`).
pub fn normalize_display_path(path: &str) -> &str {
    path.strip_prefix("./").unwrap_or(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_path() {
        assert_eq!(normalize_display_path("./foo/bar"), "foo/bar");
        assert_eq!(normalize_display_path("foo/bar"), "foo/bar");
        assert_eq!(normalize_display_path("./"), "");
    }

    // ── Drill-aware detection (whitespace inside a drilled dir name) ─────

    #[test]
    fn drill_prefix_allows_internal_space() {
        let ctx = detect_with_drill("@my dir", 7, Some("my dir")).unwrap();
        assert_eq!(ctx.range, 0..7);
        assert_eq!(ctx.query, "my dir");
        assert!(!ctx.is_dir_mode());
    }

    #[test]
    fn drill_prefix_enters_dir_mode_with_trailing_slash() {
        let ctx = detect_with_drill("@my dir/", 8, Some("my dir")).unwrap();
        assert_eq!(ctx.query, "my dir/");
        assert!(ctx.is_dir_mode());
    }

    #[test]
    fn drill_prefix_allows_internal_tab() {
        let ctx = detect_with_drill("@my\tdir", 7, Some("my\tdir")).unwrap();
        assert_eq!(ctx.range, 0..7);
        assert_eq!(ctx.query, "my\tdir");
    }

    #[test]
    fn drill_prefix_with_hidden_mode() {
        let ctx = detect_with_drill("@!my dir", 8, Some("my dir")).unwrap();
        assert!(ctx.is_hidden_mode());
        assert_eq!(ctx.matcher_query(), "my dir");
    }

    #[test]
    fn drill_prefix_mismatch_falls_back_to_whitespace_terminator() {
        // Prefix mismatch → space terminates as usual (sentence typing preserved).
        assert!(detect_with_drill("@foo bar", 8, Some("my dir")).is_none());
    }

    #[test]
    fn drill_prefix_whitespace_after_prefix_terminates() {
        // Whitespace beyond the drilled prefix still ends the token.
        assert!(detect_with_drill("@my dir extra", 13, Some("my dir")).is_none());
    }

    #[test]
    fn drill_prefix_cursor_mid_token() {
        // Cursor inside the drilled name still resolves the full token range.
        let ctx = detect_with_drill("@my dir/sub", 5, Some("my dir")).unwrap();
        assert_eq!(ctx.range, 0..11);
        assert_eq!(ctx.query, "my d");
    }

    #[test]
    fn drill_prefix_inert_when_backspaced_out_of_prefix() {
        // Self-validation: `@my di` no longer starts with `my dir`, so the
        // anchor goes inert and the space re-terminates.
        assert!(detect_with_drill("@my di", 6, Some("my dir")).is_none());
    }

    #[test]
    fn drill_prefix_allows_multibyte_dir_name() {
        // `é` is two bytes; guards the `after_bang + prefix.len()` byte math.
        let ctx = detect_with_drill("@café dir", 10, Some("café dir")).unwrap();
        assert_eq!(ctx.range, 0..10);
        assert_eq!(ctx.query, "café dir");
    }

    #[test]
    fn drill_prefix_empty_collapses_to_no_prefix() {
        // Empty prefix anchors nothing → terminates as if no prefix were set.
        assert!(detect_with_drill("@my dir", 7, Some("")).is_none());
    }

    #[test]
    fn drill_prefix_allows_second_level_space_segment() {
        // Both spaces fall inside the drilled prefix → one token.
        let ctx = detect_with_drill("@a b/c d", 8, Some("a b/c d")).unwrap();
        assert_eq!(ctx.range, 0..8);
        assert_eq!(ctx.query, "a b/c d");
    }
}
