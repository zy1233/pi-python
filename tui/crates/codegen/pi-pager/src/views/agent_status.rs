//! Agent status bar — composable right-aligned status items with separators.
//!
//! Provides [`AgentStatusBar`] which collects items as `Line<'static>` spans,
//! lays them out right-aligned with dim `│` separators, and renders into a
//! buffer row.  Returns hit-test areas keyed by item ID.
//!
//! # Example
//!
//! ```ignore
//! let mut status = AgentStatusBar::new(&theme);
//! status.push("context", context_line);
//! status.push("queue", queue_line);
//! let areas = status.render(buf, status_bar_rect);
//! let context_area = areas.get("context");
//! ```

use std::collections::HashMap;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};

use super::context_bar::SEPARATOR;
use crate::theme::Theme;

/// A named status bar item.
struct StatusEntry {
    /// Identifier for hit-test lookup (e.g., "context", "queue").
    id: &'static str,
    /// Pre-built styled content.
    line: Line<'static>,
    /// Display width in columns.
    width: u16,
}

/// Builder for the agent status bar.
///
/// Collect items with [`push`], then call [`render`] to lay them out
/// right-aligned with separators and get back hit-test areas.
pub struct AgentStatusBar<'a> {
    items: Vec<StatusEntry>,
    theme: &'a Theme,
    /// Padding from the right edge of the status bar area.
    right_pad: u16,
}

impl<'a> AgentStatusBar<'a> {
    /// Create a new empty status bar.
    pub fn new(theme: &'a Theme) -> Self {
        Self {
            items: Vec::new(),
            theme,
            right_pad: 0,
        }
    }

    /// Add an item to the status bar.
    ///
    /// Items are rendered left-to-right in push order, but the entire
    /// group is right-aligned within the status bar area.
    pub fn push(&mut self, id: &'static str, line: Line<'static>) {
        let width = line.width() as u16;
        self.items.push(StatusEntry { id, line, width });
    }

    /// Build a separator span: ` │ ` in dim color.
    fn separator(&self) -> Span<'static> {
        Span::styled(
            format!(" {SEPARATOR} "),
            Style::default()
                .fg(self.theme.gray_dim)
                .bg(self.theme.bg_base),
        )
    }

    /// Render all items right-aligned into the given area.
    ///
    /// Layout: `··· item0 │ item1 │ item2` — separators appear only *between*
    /// items, never before the first or after the last.
    ///
    /// Returns a map of item ID → screen `Rect` for hit-testing.
    pub fn render(self, buf: &mut Buffer, area: Rect) -> HashMap<&'static str, Rect> {
        if area.height == 0 || area.width == 0 || self.items.is_empty() {
            return HashMap::new();
        }

        // Fill background
        buf.set_style(area, Style::default().bg(self.theme.bg_base));

        let sep = self.separator();
        let sep_w = sep.width() as u16; // 3

        // Total width: items plus the separators *between* them only — no
        // leading separator before the first item or trailing one after the
        // last.
        let items_width: u16 = self.items.iter().map(|e| e.width).sum();
        let num_seps = (self.items.len() as u16).saturating_sub(1);
        let total_width = items_width + num_seps * sep_w;

        // Right-align: compute starting x
        let start_x = area
            .x
            .saturating_add(area.width.saturating_sub(self.right_pad + total_width));

        let mut x = start_x;
        let mut areas = HashMap::new();

        for (i, entry) in self.items.iter().enumerate() {
            // Separator before every item except the first.
            if i > 0 {
                buf.set_span(x, area.y, &sep, sep_w);
                x += sep_w;
            }

            // Render item
            buf.set_line(x, area.y, &entry.line, entry.width);
            areas.insert(
                entry.id,
                Rect {
                    x,
                    y: area.y,
                    width: entry.width,
                    height: 1,
                },
            );
            x += entry.width;
        }

        areas
    }
}

// ---------------------------------------------------------------------------
// Goal status line
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // The old deliverable-index parity test is removed because deliverables
    // are no longer part of the simplified goal model.

    /// Separators appear only *between* items — never before the first item or
    /// after the last (no leading/trailing divider).
    #[test]
    fn status_bar_separators_only_between_items() {
        let theme = Theme::current();
        let mut bar = AgentStatusBar::new(&theme);
        bar.push("a", Line::from("AA"));
        bar.push("b", Line::from("BB"));
        bar.push("c", Line::from("CC"));

        let area = Rect::new(0, 0, 40, 1);
        let mut buf = Buffer::empty(area);
        bar.render(&mut buf, area);

        let row: String = (0..area.width).map(|x| buf[(x, 0)].symbol()).collect();
        let trimmed = row.trim();

        // Exactly two dividers (between the three items), none at the ends.
        assert_eq!(trimmed.matches(SEPARATOR).count(), 2, "row = {trimmed:?}");
        assert!(
            !trimmed.starts_with(SEPARATOR),
            "no leading divider, row = {trimmed:?}"
        );
        assert!(
            !trimmed.ends_with(SEPARATOR),
            "no trailing divider, row = {trimmed:?}"
        );
        assert_eq!(trimmed, format!("AA {SEPARATOR} BB {SEPARATOR} CC"));
    }

    /// A single item renders with no separators at all (it is both first and
    /// last).
    #[test]
    fn status_bar_single_item_has_no_separators() {
        let theme = Theme::current();
        let mut bar = AgentStatusBar::new(&theme);
        bar.push("only", Line::from("XX"));

        let area = Rect::new(0, 0, 20, 1);
        let mut buf = Buffer::empty(area);
        bar.render(&mut buf, area);

        let row: String = (0..area.width).map(|x| buf[(x, 0)].symbol()).collect();
        assert_eq!(row.trim(), "XX");
        assert!(!row.contains(SEPARATOR));
    }
}
