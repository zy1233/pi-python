//! Single-row welcome toast overlay (above the prompt when present).

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use unicode_width::UnicodeWidthStr;

use crate::render::SafeBuf;
use crate::theme::Theme;

/// Prefer one row above the prompt, right-aligned to it. With no prompt
/// (login / gate), paint the last row of `area` — stacked welcome layouts
/// put the version badge there, so the toast may overlay it briefly.
pub(crate) fn paint_welcome_toast(
    buf: &mut Buffer,
    area: Rect,
    msg: &str,
    prompt_rect: Option<Rect>,
) {
    let theme = Theme::current();
    let max_msg = (area.width as usize).saturating_sub(4);
    if max_msg == 0 || area.height == 0 {
        return;
    }
    let body = if UnicodeWidthStr::width(msg) <= max_msg {
        std::borrow::Cow::Borrowed(msg)
    } else {
        std::borrow::Cow::Owned(truncate_to_width(msg, max_msg))
    };
    let toast = format!(" {body} ");
    let w = UnicodeWidthStr::width(toast.as_str()) as u16;
    let (x, y) = if let Some(prompt) = prompt_rect.filter(|r| r.width > 0 && r.y > area.y) {
        let max_x = area.right().saturating_sub(w).max(area.x);
        let x = prompt.right().saturating_sub(w + 1).clamp(area.x, max_x);
        (x, prompt.y.saturating_sub(1))
    } else {
        (
            area.right().saturating_sub(w.saturating_add(1)),
            area.bottom().saturating_sub(1),
        )
    };
    let style = Style::default()
        .fg(theme.accent_user)
        .bg(theme.bg_base)
        .add_modifier(Modifier::BOLD);
    buf.set_string_safe(x, y, &toast, style);
}

/// Truncate `text` to at most `budget` terminal columns, appending an
/// ellipsis if truncated. Uses display width (not char count) so CJK
/// and emoji characters measure correctly.
fn truncate_to_width(text: &str, budget: usize) -> String {
    use unicode_width::UnicodeWidthChar;

    if UnicodeWidthStr::width(text) <= budget {
        return text.to_owned();
    }
    let target = budget.saturating_sub(1); // room for ellipsis
    let mut out = String::new();
    let mut w = 0usize;
    for ch in text.chars() {
        let cw = UnicodeWidthChar::width(ch).unwrap_or(0);
        if w + cw > target {
            break;
        }
        out.push(ch);
        w += cw;
    }
    out.push('\u{2026}');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_to_width_ascii() {
        assert_eq!(truncate_to_width("short", 100), "short");
        assert_eq!(truncate_to_width("hello world", 5), "hell\u{2026}");
    }

    #[test]
    fn truncate_to_width_cjk() {
        // Each CJK char = 2 cols. "你好世界" = 8 cols. Budget 5 → 2 chars (4 cols) + ellipsis.
        let result = truncate_to_width("你好世界", 5);
        assert!(
            UnicodeWidthStr::width(result.as_str()) <= 5,
            "truncated CJK {result:?} wider than 5"
        );
        assert!(result.ends_with('\u{2026}'));
    }

    #[test]
    fn truncate_to_width_boundaries() {
        // Exactly at budget → unchanged (no ellipsis).
        assert_eq!(truncate_to_width("abcd", 4), "abcd");
        // One column over → truncated with the ellipsis.
        assert_eq!(truncate_to_width("abcde", 4), "abc\u{2026}");
        // Zero-width combining marks don't consume the budget.
        let combining = "a\u{0301}b\u{0301}"; // 2 display columns
        assert_eq!(UnicodeWidthStr::width(combining), 2);
        assert_eq!(truncate_to_width(combining, 5), combining);
        // Degenerate budget 0 → just the ellipsis (no codepoint dropped silently).
        assert_eq!(truncate_to_width("x", 0), "\u{2026}");
    }

    fn toast_row(buf: &Buffer, area: Rect, y: u16) -> String {
        (area.x..area.right())
            .map(|x| buf.cell((x, y)).map(|c| c.symbol()).unwrap_or(" "))
            .collect::<Vec<_>>()
            .join("")
    }

    #[test]
    fn paint_welcome_toast_truncates_narrow_width() {
        let area = Rect::new(0, 0, 40, 6);
        let prompt = Rect::new(0, 4, 40, 2);
        let mut buf = Buffer::empty(area);
        let long = crate::app::link_opener::browser_unavailable_line(
            "https://example.com/legal/terms-of-service",
            false,
        );

        paint_welcome_toast(&mut buf, area, &long, Some(prompt));
        let y = prompt.y.saturating_sub(1);
        let long_row = toast_row(&buf, area, y);
        assert!(
            long_row.contains('\u{2026}'),
            "narrow width must truncate with ellipsis: {long_row:?}"
        );
        assert!(
            long_row.contains("https://example.com"),
            "URL-first message should keep the URL prefix under truncation: {long_row:?}"
        );
        assert!(
            !toast_row(&buf, area, prompt.y).contains("https://"),
            "truncated toast must not spill into prompt row"
        );
    }

    #[test]
    fn paint_welcome_toast_truncates_wide_glyphs_by_display_width() {
        let area = Rect::new(0, 0, 12, 4);
        let prompt = Rect::new(0, 2, 12, 2);
        let mut buf = Buffer::empty(area);
        // Each CJK glyph is display width 2; char-count truncation would overfill.
        let msg = "你好世界测试文字更多内容";
        paint_welcome_toast(&mut buf, area, msg, Some(prompt));
        let y = prompt.y.saturating_sub(1);
        let row = toast_row(&buf, area, y);
        assert!(
            row.contains('\u{2026}'),
            "wide glyphs must truncate by display width: {row:?}"
        );
        let content: String = row.chars().filter(|c| *c != ' ').collect();
        assert!(
            UnicodeWidthStr::width(content.as_str()) <= area.width as usize,
            "painted width must fit area: {row:?}"
        );
        assert!(
            !toast_row(&buf, area, prompt.y).contains('你'),
            "must not spill into prompt row"
        );
    }

    #[test]
    fn paint_welcome_toast_right_aligns_to_prompt() {
        let area = Rect::new(0, 0, 80, 10);
        let prompt = Rect::new(2, 8, 76, 2);
        let mut buf = Buffer::empty(area);
        let msg = "ok";
        paint_welcome_toast(&mut buf, area, msg, Some(prompt));

        let y = prompt.y.saturating_sub(1);
        let row = toast_row(&buf, area, y);
        assert!(row.contains("ok"), "toast must paint: {row:?}");
        let toast = " ok ";
        let w = UnicodeWidthStr::width(toast) as u16;
        let expected_x = prompt.right().saturating_sub(w + 1);
        let content_x = (area.x..area.right())
            .find(|&x| buf.cell((x, y)).is_some_and(|c| c.symbol() == "o"))
            .expect("expected toast content");
        assert_eq!(
            content_x,
            expected_x + 1,
            "toast should right-align to prompt (content after pad)"
        );
    }

    #[test]
    fn paint_welcome_toast_without_prompt_right_aligns_bottom_row() {
        let area = Rect::new(0, 0, 80, 5);
        let mut buf = Buffer::empty(area);
        paint_welcome_toast(&mut buf, area, "ok", None);
        let y = area.bottom().saturating_sub(1);
        let row = toast_row(&buf, area, y);
        assert!(
            row.contains("ok"),
            "toast must paint on bottom row: {row:?}"
        );
        let toast = " ok ";
        let w = UnicodeWidthStr::width(toast) as u16;
        let expected_x = area.right().saturating_sub(w.saturating_add(1));
        let content_x = (area.x..area.right())
            .find(|&x| buf.cell((x, y)).is_some_and(|c| c.symbol() == "o"))
            .expect("expected toast content");
        assert_eq!(
            content_x,
            expected_x + 1,
            "no-prompt toast should right-align within area (content after pad)"
        );
    }
}
