//! Minimal-mode below-prompt **list panel**: `/resume` (session picker),
//! rendered as a simple list *below the input bar* instead of a centered modal
//! window (design nit: "the resume list should not be in a modal").
//!
//! ## Why this is a render-only change
//!
//! Input routing is unchanged — the existing `handle_modal_key`
//! (`ActiveModal::SessionPicker`) owns navigation and close-on-Esc. The session
//! picker rebuilds its entry map from data on every keypress
//! (render-independent), so we just reuse the *same* builders
//! ([`build_grouped_picker_entries`]) — the rendered order then matches the
//! handler's `selected`.
//!
//! The rows are drawn with [`picker::render_picker_content`], so row look +
//! selection highlight match the full TUI; only the modal-window chrome (border,
//! tabs, footer bar) is dropped.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Span;

use pi_pager::app::agent_view::AgentView;
use pi_pager::minimal_api;
use pi_pager::theme::Theme;
use pi_pager::views::modal::ActiveModal;
use pi_pager::views::picker::{self, PickerEntry, PickerField, PickerHitAreas};

/// Rows of chrome around the scrolling list: title + subtitle/search + divider
/// + footer.
const CHROME_ROWS: u16 = 4;

/// Which below-prompt list panel is active for the focused agent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum ListPanel {
    /// `/resume` session picker (`ActiveModal::SessionPicker`).
    Resume,
}

/// Detect an active below-prompt list panel, or `None`.
///
/// Only the session picker is hosted as a simple list; every other modal keeps
/// its existing (centered) rendering. Callers must check this *before*
/// `overlay::app_modal_active`, since `SessionPicker` is also an `active_modal`.
pub(super) fn active(agent: &AgentView) -> Option<ListPanel> {
    if matches!(agent.active_modal, Some(ActiveModal::SessionPicker { .. })) {
        return Some(ListPanel::Resume);
    }
    None
}

/// Target viewport height for the active list panel: chrome + the exact body
/// height, clamped to `[CHROME_ROWS + 1, ceiling]`. Sizing to the exact content
/// height keeps the footer directly under the last row (no blank band); when the
/// body exceeds `ceiling` the list scrolls internally.
pub(super) fn panel_height(agent: &AgentView, kind: ListPanel, width: u16, ceiling: u16) -> u16 {
    let body = match kind {
        ListPanel::Resume => resume_body_rows(agent, width),
    };
    CHROME_ROWS
        .saturating_add(body)
        .clamp(CHROME_ROWS + 1, ceiling.max(CHROME_ROWS + 1))
}

/// Render the active list panel into `area` (the whole live region). Returns the
/// text cursor for the panel's search bar when search is focused, else `None`.
pub(super) fn render(
    buf: &mut Buffer,
    area: Rect,
    agent: &mut AgentView,
    kind: ListPanel,
    theme: &Theme,
) -> Option<(u16, u16)> {
    if area.height < 2 || area.width < 8 {
        return None;
    }
    match kind {
        ListPanel::Resume => render_resume(buf, area, agent, theme),
    }
}

// ─────────────────────────────── chrome ─────────────────────────────────────

/// Split `area` into (title_row, second_row, divider_row, list_area, footer_row).
/// `second_row` hosts the search bar.
fn chrome_layout(area: Rect) -> (Rect, Rect, Rect, Rect, Rect) {
    let row = |dy: u16| Rect {
        x: area.x,
        y: area.y + dy,
        width: area.width,
        height: 1,
    };
    let title = row(0);
    let second = row(1);
    let divider = row(2);
    let footer = Rect {
        x: area.x,
        y: area.y + area.height - 1,
        ..row(0)
    };
    let list = Rect {
        x: area.x,
        y: area.y + 3,
        width: area.width,
        height: area.height.saturating_sub(CHROME_ROWS),
    };
    (title, second, divider, list, footer)
}

fn render_title(buf: &mut Buffer, row: Rect, theme: &Theme, title: &str) {
    buf.set_style(row, Style::default().bg(Color::Reset));
    let style = Style::default()
        .fg(theme.accent_user)
        .bg(Color::Reset)
        .add_modifier(Modifier::BOLD);
    buf.set_span(row.x + 1, row.y, &Span::styled(title, style), row.width);
}

fn render_dim_line(buf: &mut Buffer, row: Rect, theme: &Theme, text: &str) {
    buf.set_style(row, Style::default().bg(Color::Reset));
    let style = theme.dim().bg(Color::Reset);
    buf.set_span(row.x + 1, row.y, &Span::styled(text, style), row.width);
}

/// `/resume` session picker: Enter picks a session.
const RESUME_FOOTER: &str = "\u{2191}/\u{2193} navigate \u{00b7} enter confirm \u{00b7} esc cancel";

fn render_footer(buf: &mut Buffer, row: Rect, theme: &Theme, text: &str) {
    render_dim_line(buf, row, theme, text);
}

fn render_divider(buf: &mut Buffer, row: Rect, theme: &Theme) {
    picker::render_divider(buf, row.x, row.y, row.width, theme, None);
}

// ─────────────────────────────── resume ─────────────────────────────────────

/// Exact body height (display rows) for the session-picker list.
fn resume_body_rows(agent: &AgentView, width: u16) -> u16 {
    let Some(ActiveModal::SessionPicker { entries, state, .. }) = &agent.active_modal else {
        return 0;
    };
    let entries_data = entries.as_deref().unwrap_or(&[]);
    let content_width = width.saturating_sub(2);
    let filtered =
        minimal_api::filter_session_entries(entries.as_deref(), state.query());
    let built =
        minimal_api::build_session_entry_data(entries_data, &filtered, state, content_width);
    let fields_vecs: Vec<Vec<PickerField>> = built
        .iter()
        .map(|b| {
            b.field_data
                .iter()
                .map(|(l, v)| PickerField { label: l, value: v })
                .collect()
        })
        .collect();
    let current_repo = minimal_api::repo_name_from_cwd(&agent.session.cwd.to_string_lossy());
    let (picker_entries, _) = minimal_api::build_grouped_picker_entries(
        entries_data,
        &filtered,
        &built,
        &fields_vecs,
        state,
        Some(current_repo.as_str()),
    );
    measure_entries(&picker_entries)
}

fn render_resume(
    buf: &mut Buffer,
    area: Rect,
    agent: &mut AgentView,
    theme: &Theme,
) -> Option<(u16, u16)> {
    let cwd = agent.session.cwd.to_string_lossy().to_string();
    let Some(ActiveModal::SessionPicker { entries, state, .. }) = &mut agent.active_modal else {
        return None;
    };
    let (title_row, search_row, divider_row, list_area, footer_row) = chrome_layout(area);

    let entries_data = entries.as_deref().unwrap_or(&[]);
    let content_width = area.width.saturating_sub(2);
    let filtered =
        minimal_api::filter_session_entries(entries.as_deref(), state.query());
    let built =
        minimal_api::build_session_entry_data(entries_data, &filtered, state, content_width);
    let fields_vecs: Vec<Vec<PickerField>> = built
        .iter()
        .map(|b| {
            b.field_data
                .iter()
                .map(|(l, v)| PickerField { label: l, value: v })
                .collect()
        })
        .collect();
    let current_repo = minimal_api::repo_name_from_cwd(&cwd);
    let (picker_entries, non_sel) = minimal_api::build_grouped_picker_entries(
        entries_data,
        &filtered,
        &built,
        &fields_vecs,
        state,
        Some(current_repo.as_str()),
    );

    render_title(buf, title_row, theme, "Resume session");
    // Focus-aware search bar (cursor only when search is focused).
    minimal_api::render_picker_search_bar(
        buf,
        Rect::new(
            search_row.x + 1,
            search_row.y,
            search_row.width.saturating_sub(1),
            1,
        ),
        theme,
        state,
        true,
        None,
    );
    render_divider(buf, divider_row, theme);

    let nsc = vec![false; picker_entries.len()];
    let hit = picker::render_picker_content(
        buf,
        list_area,
        theme,
        state,
        &picker_entries,
        &non_sel,
        &nsc,
        None,
        false,
    );
    state.hit_areas = Some(PickerHitAreas {
        close_button: Rect::default(),
        search_bar: search_row,
        item_rects: hit.item_rects,
        entry_indices: hit.entry_indices,
        tab_rects: vec![],
    });

    render_footer(buf, footer_row, theme, RESUME_FOOTER);
    None
}

// ─────────────────────────────── helpers ────────────────────────────────────

/// Sum the display height of grouped picker entries: a header is one row (plus
/// the blank spacer `render_picker_content` draws before non-first headers); a
/// row is its label line plus its collapsed summary lines (what the picker
/// draws when the row is not expanded).
fn measure_entries(entries: &[PickerEntry<'_>]) -> u16 {
    entries
        .iter()
        .enumerate()
        .map(|(idx, e)| match e {
            PickerEntry::Header { .. } => {
                if idx == 0 {
                    1u16
                } else {
                    2u16
                }
            }
            PickerEntry::Row(r) => {
                if r.expanded {
                    1u16.saturating_add(r.description_lines.len() as u16)
                        .saturating_add(r.fields.len() as u16)
                } else {
                    1u16.saturating_add(r.summary_lines.len() as u16)
                }
            }
        })
        .fold(0u16, |acc, h| acc.saturating_add(h))
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::layout::Rect;

    fn agent() -> AgentView {
        minimal_api::test_agent_view(Some("s1"), std::path::PathBuf::from("/tmp/repo"))
    }

    fn session_entry(id: &str) -> pi_pager::app::app_view::SessionPickerEntry {
        pi_pager::app::app_view::SessionPickerEntry {
            id: id.into(),
            summary: id.into(),
            updated_at: chrono::Utc::now(),
            created_at: chrono::Utc::now(),
            cwd: "/tmp/repo".into(),
            hostname: None,
            source: String::new(),
            model_id: None,
            num_messages: 0,
            last_active_at: None,
            branch: None,
            repo_name: "repo".into(),
            worktree_label: None,
            last_turn_summary: None,
            last_recap: None,
            card_detail: None,
        }
    }

    fn with_resume(entries: Vec<pi_pager::app::app_view::SessionPickerEntry>) -> AgentView {
        let mut a = agent();
        a.active_modal = Some(ActiveModal::SessionPicker {
            state: picker::PickerState::default(),
            entries: Some(entries),
            loading: false,
            previous_palette: None,
            window: pi_pager::views::modal_window::ModalWindowState::new(),
            pending_delete: None,
        });
        a
    }

    fn buffer_text(buf: &Buffer) -> String {
        let area = buf.area;
        let mut out = String::new();
        for y in area.y..area.y + area.height {
            for x in area.x..area.x + area.width {
                out.push_str(buf.cell((x, y)).map(|c| c.symbol()).unwrap_or(" "));
            }
            out.push('\n');
        }
        out
    }

    #[test]
    fn active_detects_resume_and_none() {
        assert_eq!(active(&agent()), None);
        assert_eq!(
            active(&with_resume(vec![session_entry("hello")])),
            Some(ListPanel::Resume)
        );
    }

    #[test]
    fn resume_panel_renders_title_rows_and_footer() {
        let mut a = with_resume(vec![session_entry("first task"), session_entry("second")]);
        let theme = Theme::current();
        let area = Rect::new(0, 0, 80, 24);
        let mut buf = Buffer::empty(area);
        render(&mut buf, area, &mut a, ListPanel::Resume, &theme);

        let text = buffer_text(&buf);
        assert!(text.contains("Resume session"), "title:\n{text}");
        assert!(text.contains("first task"), "session row:\n{text}");
        assert!(text.contains("enter confirm"), "resume footer:\n{text}");
        assert!(
            !text.contains("r refresh"),
            "resume footer must stay session-picker copy:\n{text}"
        );
    }

    #[test]
    fn resume_search_uses_picker_grapheme_viewport_at_narrow_width() {
        let grapheme = "👩🏽\u{200d}💻";
        let combining = "e\u{301}";
        let mut agent = with_resume(vec![session_entry("match")]);
        let Some(ActiveModal::SessionPicker { state, .. }) = &mut agent.active_modal else {
            panic!("expected session picker");
        };
        state.set_query(format!("a{grapheme}{combining}"));
        state.search_active = true;

        let theme = Theme::current();
        let area = Rect::new(0, 0, 14, 5);
        let mut actual = Buffer::empty(area);
        render(&mut actual, area, &mut agent, ListPanel::Resume, &theme);

        let Some(ActiveModal::SessionPicker { state, .. }) = &agent.active_modal else {
            panic!("expected session picker");
        };
        let mut expected = Buffer::empty(area);
        minimal_api::render_picker_search_bar(
            &mut expected,
            Rect::new(1, 1, 13, 1),
            &theme,
            state,
            true,
            None,
        );
        for x in 1..14 {
            let actual_cell = actual.cell((x, 1)).expect("actual search cell");
            let expected_cell = expected.cell((x, 1)).expect("expected search cell");
            assert_eq!(actual_cell.symbol(), expected_cell.symbol(), "column {x}");
            assert_eq!(actual_cell.style(), expected_cell.style(), "column {x}");
        }
        let text = buffer_text(&actual);
        assert!(text.contains(grapheme), "ZWJ grapheme was split: {text:?}");
        assert!(
            text.contains(combining),
            "combining grapheme was split: {text:?}"
        );
        assert_eq!(
            actual.cell((13, 1)).expect("cursor cell").bg,
            theme.text_primary
        );
    }
}
