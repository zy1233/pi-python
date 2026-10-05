//! Session display-title helpers shared by the session surfaces.
//!
//! Title derivation order ([`entry_title`]):
//! 1. `AgentView::display_name` if set (post-rename),
//! 2. else `AgentView::generated_session_title` (LLM title or from disk on resume),
//! 3. else the trimmed first ~60 chars of the first user-prompt block in scrollback,
//! 4. else `"session abc12345"` (or `"loading..."` when no session id
//!    is established yet).

use std::borrow::Cow;

use crate::app::agent_view::AgentView;
use crate::scrollback::block::RenderBlock;

/// Maximum characters of a derived first-prompt title.
const MAX_TITLE_CHARS: usize = 60;

/// Derive the display title for an agent (rename > generated title > first-prompt > id).
///
/// Centralised so every surface that shows a session name agrees on the same
/// precedence. Trimming and truncation happen in this single place to avoid drift.
pub fn entry_title(agent: &AgentView) -> String {
    if let Some(name) = agent.display_name.as_deref() {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            return truncate_title(&sanitize_display_text(trimmed));
        }
    }
    if let Some(title) = agent.generated_session_title.as_deref() {
        let trimmed = title.trim();
        if !trimmed.is_empty() {
            let clean =
                pi_tools::implementations::skills::skill::extract_skill_display_text(trimmed);
            let text = clean.as_deref().unwrap_or(trimmed);
            return truncate_title(&sanitize_display_text(text));
        }
    }
    if let Some(text) = first_user_prompt_text(agent) {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            let clean =
                pi_tools::implementations::skills::skill::extract_skill_display_text(trimmed);
            let display = clean.as_deref().unwrap_or(trimmed);
            return truncate_title(&sanitize_display_text(display));
        }
    }
    match agent.session.session_id.as_ref() {
        Some(sid) => {
            let short: String = sid.0.chars().take(8).collect();
            format!("session {short}")
        }
        None => "loading...".to_string(),
    }
}

pub(crate) fn rename_source_title_raw(agent: &AgentView) -> Option<&str> {
    for name in [
        agent.display_name.as_deref(),
        agent.generated_session_title.as_deref(),
    ]
    .into_iter()
    .flatten()
    {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            return Some(trimmed);
        }
    }
    None
}

/// Take the first scrollback `UserPrompt` block's text, if any.
///
/// Skips indices whose `entry()` returns `None` (defensive: the indexed
/// range matches `scrollback.len()` so this should not happen in
/// practice) instead of bailing out with `?`, which would conflate
/// "no UserPrompt anywhere" with "hit an unexpected gap mid-scan".
fn first_user_prompt_text(agent: &AgentView) -> Option<String> {
    for i in 0..agent.scrollback.len() {
        if let Some(entry) = agent.scrollback.entry(i)
            && let RenderBlock::UserPrompt(block) = &entry.block
        {
            return Some(block.text.clone());
        }
    }
    None
}

/// First line of the most recent user prompt (`RenderBlock::UserPrompt`) in
/// the agent's scrollback, ANSI-stripped + sanitised; `None` when the user
/// hasn't sent any prompts yet.
pub(crate) fn last_user_prompt_line(agent: &AgentView) -> Option<String> {
    let len = agent.scrollback.len();
    for idx in (0..len).rev() {
        let entry = agent.scrollback.entry(idx)?;
        if let RenderBlock::UserPrompt(b) = &entry.block {
            let first = b.text.lines().next().unwrap_or("").trim();
            if first.is_empty() {
                continue;
            }
            let stripped = strip_ansi_escapes::strip_str(first);
            let safe = sanitize_display_text(&stripped).into_owned();
            return Some(safe.trim().to_string());
        }
    }
    None
}

/// First renderable line of the newest agent message, ANSI-stripped +
/// sanitised. Pairing guarantee: returns `None` when a `UserPrompt` is newer
/// than every agent message (that prompt is unanswered — an older reply would
/// misrepresent the latest exchange), or when the message has no renderable
/// line (older messages are not scanned).
pub(crate) fn last_agent_message_line(agent: &AgentView) -> Option<String> {
    let len = agent.scrollback.len();
    for idx in (0..len).rev() {
        let entry = agent.scrollback.entry(idx)?;
        match &entry.block {
            RenderBlock::AgentMessage(msg) => {
                let text = msg.text();
                for line in text.lines() {
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    let stripped = strip_ansi_escapes::strip_str(trimmed);
                    let safe = sanitize_display_text(&stripped).into_owned();
                    let safe = safe.trim().to_string();
                    if !safe.is_empty() {
                        return Some(safe);
                    }
                }
                return None;
            }
            // The user's latest prompt marks the turn boundary — no reply yet.
            RenderBlock::UserPrompt(_) => return None,
            _ => {}
        }
    }
    None
}

/// Take the first `MAX_TITLE_CHARS` chars and append an ellipsis when
/// truncated. Char-based (not byte-based) so multi-byte codepoints
/// don't get split.
fn truncate_title(text: &str) -> String {
    if text.chars().count() <= MAX_TITLE_CHARS {
        return text.to_string();
    }
    let head: String = text.chars().take(MAX_TITLE_CHARS).collect();
    format!("{head}...")
}

/// Strip C0/C1 and bidi/format controls that could inject terminal escape
/// sequences or spoof the title. Replaces them with `U+FFFD` so the caller
/// can still see something was there. Same character class as
/// [`pi_shell::session::persistence::is_forbidden_title_char`]; persist
/// drops, display replaces.
///
/// Returns `Cow::Borrowed(s)` when no sanitization is needed, so the
/// common per-render call on a clean cached display_name does not
/// allocate.
pub(crate) fn sanitize_display_text(s: &str) -> Cow<'_, str> {
    use pi_shell::session::persistence::is_forbidden_title_char;
    if s.chars().any(is_forbidden_title_char) {
        Cow::Owned(
            s.chars()
                .map(|c| {
                    if is_forbidden_title_char(c) {
                        '\u{FFFD}'
                    } else {
                        c
                    }
                })
                .collect(),
        )
    } else {
        Cow::Borrowed(s)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── sanitize_display_text ───────────────────────────────────────

    #[test]
    fn sanitize_passes_through_clean_ascii_unchanged_no_alloc() {
        let s = "session foo bar";
        let out = sanitize_display_text(s);
        assert_eq!(out.as_ref(), s);
        assert!(matches!(out, Cow::Borrowed(_)));
    }

    #[test]
    fn sanitize_passes_through_unicode_widechars() {
        for s in ["セッション one", "session 🦀 two", "naïve"] {
            let out = sanitize_display_text(s);
            assert_eq!(out.as_ref(), s);
            assert!(matches!(out, Cow::Borrowed(_)), "input={s:?}");
        }
    }

    #[test]
    fn sanitize_strips_osc_escape_sequence() {
        // Attack: OSC title-set + clear screen.
        let attack = "\x1b]0;PWNED\x07\x1b[2J safe text";
        let out = sanitize_display_text(attack);
        assert!(matches!(out, Cow::Owned(_)));
        for c in out.chars() {
            assert!(!c.is_control(), "leaked control char: {:?}", c);
        }
        // Replacement chars should be present where escapes were.
        assert!(out.contains('\u{FFFD}'));
        assert!(out.ends_with(" safe text"));
    }

    #[test]
    fn sanitize_strips_csi_sequence() {
        let csi = "\x1b[31mred\x1b[0m";
        let out = sanitize_display_text(csi);
        for c in out.chars() {
            assert!(!c.is_control());
        }
    }

    #[test]
    fn sanitize_strips_bel_and_del() {
        let s = "ring\x07the\x7fbell";
        let out = sanitize_display_text(s);
        assert_eq!(out.as_ref(), "ring\u{FFFD}the\u{FFFD}bell");
    }

    #[test]
    fn sanitize_strips_c1_controls() {
        let s = "ok\u{9b}[31m\u{9c}done";
        let out = sanitize_display_text(s);
        assert_eq!(out.as_ref(), "ok\u{FFFD}[31m\u{FFFD}done");
    }

    #[test]
    fn sanitize_strips_bidi_overrides() {
        let s = "deploy-prod\u{202e}gnp.hs";
        let out = sanitize_display_text(s);
        assert_eq!(out.as_ref(), "deploy-prod\u{FFFD}gnp.hs");
    }

    #[test]
    fn sanitize_strips_tab_newline_carriage_return() {
        // Tabs and newlines are also ASCII controls -- a single-line
        // rename input should never contain them, so strip all.
        let s = "a\tb\nc\rd";
        let out = sanitize_display_text(s);
        assert_eq!(out.as_ref(), "a\u{FFFD}b\u{FFFD}c\u{FFFD}d");
    }

    #[test]
    fn sanitize_empty_returns_empty_borrowed() {
        let out = sanitize_display_text("");
        assert_eq!(out.as_ref(), "");
        assert!(matches!(out, Cow::Borrowed(_)));
    }

    // ── rename_source_title ─────────────────────────────────────────

    // ── truncate_title ──────────────────────────────────────────────

    #[test]
    fn truncate_title_keeps_short_strings() {
        assert_eq!(truncate_title("hello"), "hello");
    }

    #[test]
    fn truncate_title_appends_ellipsis_when_too_long() {
        let long = "x".repeat(MAX_TITLE_CHARS + 5);
        let out = truncate_title(&long);
        assert!(out.ends_with("..."));
        assert_eq!(out.chars().count(), MAX_TITLE_CHARS + 3);
    }

    #[test]
    fn truncate_title_handles_multibyte_codepoints_safely() {
        // Each "é" (U+00E9) is one char (two bytes); ensure char-based
        // truncation does not split a multibyte codepoint mid-byte.
        // This does NOT exercise grapheme-cluster handling -- a
        // decomposed sequence (e + U+0301) would split at the
        // codepoint boundary today; that's a separate concern.
        let s: String = std::iter::repeat_n('é', MAX_TITLE_CHARS + 2).collect();
        let out = truncate_title(&s);
        assert!(out.ends_with("..."));
        assert_eq!(out.chars().count(), MAX_TITLE_CHARS + 3);
    }

    // ── format_relative_time ────────────────────────────────────────

}
