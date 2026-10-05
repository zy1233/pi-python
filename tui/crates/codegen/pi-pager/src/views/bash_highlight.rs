//! Shell command syntax highlighting shared by the permission view, bash tool
//! blocks and the turn status line.

use ratatui::style::Style;
use ratatui::text::Span;

use crate::syntax::get_syntect;
use crate::theme::Theme;

/// Highlight a shell command string into styled spans.
///
/// Uses syntect with the best available grammar for the platform: tries
/// "powershell" first on Windows, falls back to "bash". Returns plain
/// `theme.command` color if no grammar matches. Results should be cached.
pub fn highlight_bash_command(command: &str) -> Vec<Span<'static>> {
    let syntect = get_syntect();
    let grammar = if cfg!(windows) { "powershell" } else { "bash" };
    let Some(mut hl) = syntect
        .highlight_lines_for_token(grammar)
        .or_else(|| syntect.highlight_lines_for_token("bash"))
    else {
        let theme = Theme::current();
        return vec![Span::styled(
            command.to_string(),
            Style::default().fg(theme.command),
        )];
    };

    let line = format!("{command}\n");
    match hl.highlight_line(&line, &syntect.syntax_set) {
        Ok(ranges) => {
            let mut spans = Vec::new();
            for (style, segment) in ranges {
                let mut text = segment.to_owned();
                while text.ends_with('\n') || text.ends_with('\r') {
                    text.pop();
                }
                if text.is_empty() {
                    continue;
                }
                // Raw syntect RGB here used to bypass quantization and leak
                // polarity-tuned tmTheme colors into minimal.
                spans.push(Span::styled(
                    text,
                    crate::syntax::syntect_to_ratatui_fg(style),
                ));
            }
            if spans.is_empty() {
                let theme = Theme::current();
                vec![Span::styled(
                    command.to_string(),
                    Style::default().fg(theme.command),
                )]
            } else {
                spans
            }
        }
        Err(_) => {
            let theme = Theme::current();
            vec![Span::styled(
                command.to_string(),
                Style::default().fg(theme.command),
            )]
        }
    }
}
