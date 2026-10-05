//! Read-only system-block text for `/queue`, `/tasks`, and `/usage`.
//!
//! Plain text committed into scrollback — the primary inspection surface in
//! minimal mode (no interactive panes). Kept out of `dispatch` for easy
//! unit tests.

use crate::app::agent_view::AgentView;

/// `/queue` body — a read-only list of the queued prompts (the local
/// drip-feed queue, in drain order).
pub(crate) fn queue_block_text(agent: &AgentView) -> String {
    let rows: Vec<String> = agent
        .session
        .pending_prompts
        .iter()
        .enumerate()
        .map(|(i, prompt)| format_queue_row(i + 1, &prompt.text))
        .collect();

    if rows.is_empty() {
        "Queue is empty.".to_string()
    } else {
        let header = format!(
            "Queued prompt{} ({}):",
            if rows.len() == 1 { "" } else { "s" },
            rows.len()
        );
        join_header_rows(header, rows)
    }
}

/// First non-empty, trimmed line of `text` (empty string if none). Collapses a
/// multi-line prompt/command to a single display line.
pub(crate) fn first_nonempty_line(text: &str) -> &str {
    text.lines()
        .map(str::trim)
        .find(|l| !l.is_empty())
        .unwrap_or("")
}

/// Format one `/queue` row as `  #N  <first non-empty line>` with a
/// `(+K more lines)` suffix for multi-line prompts.
fn format_queue_row(pos: usize, text: &str) -> String {
    let first_line = first_nonempty_line(text);
    let extra = text.lines().count().saturating_sub(1);
    if extra > 0 {
        format!(
            "  #{pos}  {first_line}  (+{extra} more line{})",
            if extra == 1 { "" } else { "s" }
        )
    } else {
        format!("  #{pos}  {first_line}")
    }
}

/// Join a header line above its rows into a single block string.
fn join_header_rows(header: String, rows: Vec<String>) -> String {
    std::iter::once(header)
        .chain(rows)
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_nonempty_line_skips_blank_leading_lines() {
        assert_eq!(first_nonempty_line("\n  \n  hello \nworld"), "hello");
        assert_eq!(first_nonempty_line("   "), "");
        assert_eq!(first_nonempty_line(""), "");
        assert_eq!(first_nonempty_line("only"), "only");
    }

    #[test]
    fn format_queue_row_single_line() {
        assert_eq!(format_queue_row(1, "fix the bug"), "  #1  fix the bug");
    }

    #[test]
    fn format_queue_row_multiline_reports_extra_lines() {
        assert_eq!(
            format_queue_row(2, "first\nsecond"),
            "  #2  first  (+1 more line)"
        );
        assert_eq!(
            format_queue_row(3, "first\nsecond\nthird"),
            "  #3  first  (+2 more lines)"
        );
    }
}
