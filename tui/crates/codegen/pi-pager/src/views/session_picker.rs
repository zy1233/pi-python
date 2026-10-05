//! Shared session picker helpers.
//!
//! Centralises data types, entry building, and index-mapping logic used by
//! both the welcome-screen session picker (`welcome/mod.rs` + `app_view.rs`)
//! and the modal session picker (`ActiveModal::SessionPicker` in
//! `agent_view.rs`).

use indexmap::IndexMap;

use crate::app::app_view::SessionPickerEntry;
use crate::views::picker::{PickerEntry, PickerField, PickerRow, PickerState};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Session id for free-text Enter (`SubmitQuery` with no selectable rows).
///
/// Only a trimmed UUID is loadable — pasted garbage must not call
/// `LoadSession` (that left the TUI stuck mid-load).
pub fn session_id_for_direct_load(query: &str) -> Option<&str> {
    let q = query.trim();
    // `Uuid::try_parse` rejects empty, multi-line, and non-UUID text.
    uuid::Uuid::try_parse(q).ok()?;
    Some(q)
}

/// Derive a short repo display name from a CWD path.
///
/// Uses the last 2 normal path components joined by `-`. For paths with
/// only one normal component (e.g., `/pi`), returns that component alone.
/// Does not perform tilde expansion — callers provide absolute paths.
/// Returns `"unknown"` for empty input.
///
/// Examples: `/home/user/fw/1` → `"fw-1"`, `/pi` → `"pi"`, `/` → `"/"`.
///
/// Shared by the session-list builder (which stamps each entry's `repo_name`)
/// and the picker pinning below, so the current-cwd key matches a group key.
/// Callers pass the *live* cwd (`app.cwd` / `agent.session.cwd`) so a project
/// switch (`Effect::SetWorkingDir`) is reflected immediately.
pub(crate) fn repo_name_from_cwd(cwd: &str) -> String {
    let path = std::path::Path::new(cwd);
    let components: Vec<&str> = path
        .components()
        .filter_map(|c| match c {
            std::path::Component::Normal(os) => os.to_str(),
            _ => None,
        })
        .collect();
    if components.is_empty() {
        return if cwd.is_empty() {
            "unknown".to_string()
        } else {
            cwd.to_string()
        };
    }
    let start = components.len().saturating_sub(2);
    let tail = &components[start..];
    tail.join("-")
}

/// Order repo groups alphabetically, then pin the current working
/// directory's repo group (if present) to the front. Shared by
/// [`build_entry_map`] and [`build_grouped_picker_entries`] so the
/// index-mapping and rendering paths stay in lock-step.
fn order_repo_groups(groups: &mut IndexMap<&str, Vec<usize>>, current_repo: Option<&str>) {
    groups.sort_keys();
    if let Some(cur) = current_repo
        && let Some(pos) = groups.keys().position(|k| *k == cur)
    {
        groups.move_index(pos, 0);
    }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Which underlying data a picker position maps to.
#[derive(Debug, Clone)]
pub enum PickerItem {
    Fuzzy { original_index: usize },
}

/// A session armed for deletion, captured on `d` so the `y` confirm keeps
/// a valid `(source, session_id, cwd)` even if the lists shift. Shared by
/// the welcome and modal `/resume` pickers so they can't drift apart.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingDelete {
    pub source: String,
    pub session_id: String,
    pub cwd: String,
}

/// Outcome of routing a key through an armed [`PendingDelete`] confirm.
pub(crate) enum PendingDeleteKey {
    /// `y`: caller should delete this session.
    Confirm(PendingDelete),
    /// `n`: arm cleared; caller should redraw.
    Cancel,
    /// Other key: arm cleared, but the key should still be processed.
    Disarmed,
    /// Nothing armed, or not an unmodified key press.
    NotArmed,
}

/// Arm a [`PendingDelete`] from the selected row, or `None` if the position
/// is not selectable.
pub(crate) fn pending_delete_from_selection(
    selected: usize,
    entry_map: &[Option<PickerItem>],
    entries: Option<&[SessionPickerEntry]>,
) -> Option<PendingDelete> {
    match entry_map.get(selected).and_then(|e| e.as_ref())? {
        PickerItem::Fuzzy { original_index } => {
            entries
                .and_then(|e| e.get(*original_index))
                .map(|e| PendingDelete {
                    source: e.source.clone(),
                    session_id: e.id.clone(),
                    cwd: e.cwd.clone(),
                })
        }
    }
}

/// Route a key through an armed [`PendingDelete`]: `y` confirms, `n`
/// cancels, any other unmodified key disarms and falls through.
pub(crate) fn handle_pending_delete_key(
    pending: &mut Option<PendingDelete>,
    ev: &crossterm::event::Event,
) -> PendingDeleteKey {
    use crossterm::event::{Event, KeyCode, KeyEventKind};
    if pending.is_none() {
        return PendingDeleteKey::NotArmed;
    }
    let Event::Key(k) = ev else {
        return PendingDeleteKey::NotArmed;
    };
    if k.kind != KeyEventKind::Press || !k.modifiers.is_empty() {
        return PendingDeleteKey::NotArmed;
    }
    match k.code {
        KeyCode::Char('y') => pending
            .take()
            .map(PendingDeleteKey::Confirm)
            .unwrap_or(PendingDeleteKey::Cancel),
        KeyCode::Char('n') => {
            *pending = None;
            PendingDeleteKey::Cancel
        }
        _ => {
            *pending = None;
            PendingDeleteKey::Disarmed
        }
    }
}

/// Owned data for a single session picker row. Built once per frame and
/// then borrowed by `PickerEntry` / `PickerField` slices. Shared between
/// the welcome-screen `render_session_picker` and the
/// `ActiveModal::SessionPicker` rendering in `agent_view.rs`.
pub struct SessionEntryData {
    pub summary: String,
    pub right_text: String,
    pub is_selected: bool,
    pub is_expanded: bool,
    pub field_data: Vec<(String, String)>,
    pub badge: &'static str,
    pub collapsible: bool,
}

/// Loading gate for a session picker surface's spinner: nothing loaded yet
/// while the list fetch is still in flight. Shared by rendering, redraw
/// forcing, and tick demand so the three cannot drift (a spinner that
/// renders without demanding ticks parks on its first frame).
pub(crate) fn loading_spinner_active(
    entries: Option<&[SessionPickerEntry]>,
    loading: bool,
) -> bool {
    entries.is_none_or(<[SessionPickerEntry]>::is_empty) && loading
}

#[derive(Debug, Clone)]
pub(crate) struct PickerSelectionAnchor {
    key: Option<PickerSelectionKey>,
    fallback_index: usize,
    scroll_delta: Option<isize>,
}

#[derive(Debug, Clone)]
enum PickerSelectionKey {
    Fuzzy { source: String, id: String },
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn capture_picker_selection(
    entries: Option<&[SessionPickerEntry]>,
    state: &PickerState,
    query: &str,
    grouped: bool,
    current_repo: Option<&str>,
) -> PickerSelectionAnchor {
    let map = build_entry_map(entries, query, grouped, current_repo);
    let key = map
        .get(state.selected)
        .and_then(|item| item.as_ref())
        .and_then(|item| match item {
            PickerItem::Fuzzy { original_index } => entries
                .and_then(|entries| entries.get(*original_index))
                .map(|entry| PickerSelectionKey::Fuzzy {
                    source: entry.source.clone(),
                    id: entry.id.clone(),
                }),
        });
    PickerSelectionAnchor {
        key,
        fallback_index: state.selected,
        scroll_delta: state
            .scroll_offset
            .map(|offset| state.selected as isize - offset as isize),
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn restore_picker_selection(
    anchor: PickerSelectionAnchor,
    entries: Option<&[SessionPickerEntry]>,
    state: &mut PickerState,
    query: &str,
    grouped: bool,
    current_repo: Option<&str>,
) {
    let map = build_entry_map(entries, query, grouped, current_repo);
    let selected = anchor
        .key
        .as_ref()
        .and_then(|key| {
            map.iter().position(|item| match (key, item.as_ref()) {
                (
                    PickerSelectionKey::Fuzzy { source, id },
                    Some(PickerItem::Fuzzy { original_index }),
                ) => entries
                    .and_then(|entries| entries.get(*original_index))
                    .is_some_and(|entry| &entry.source == source && &entry.id == id),
                _ => false,
            })
        })
        .or_else(|| selectable_fallback(&map, anchor.fallback_index))
        .unwrap_or(0);
    state.selected = selected;
    state.scroll_offset = anchor.scroll_delta.map(|delta| {
        (selected as isize - delta)
            .max(0)
            .min(map.len().saturating_sub(1) as isize) as usize
    });
}

fn selectable_fallback<T>(map: &[Option<T>], preferred: usize) -> Option<usize> {
    if map.is_empty() {
        return None;
    }
    let preferred = preferred.min(map.len() - 1);
    (preferred..map.len())
        .find(|index| map[*index].is_some())
        .or_else(|| (0..preferred).rev().find(|index| map[*index].is_some()))
}

// ---------------------------------------------------------------------------
// Filtering
// ---------------------------------------------------------------------------

/// Case-insensitive substring match (callers pass a pre-lowercased query).
///
/// Deliberately not an ordered-chars subsequence match: that matched so
/// loosely (e.g. "rc" hitting "rust-check") that spurious title rows
/// drowned out the results users actually searched for.
pub(crate) fn fuzzy_matches_session(name: &str, query: &str) -> bool {
    query.is_empty() || name.to_lowercase().contains(query)
}

/// Filter session entries by query, returning indices of matching entries.
///
/// When the query is empty, all entries match.
pub(crate) fn filter_session_entries(
    entries: Option<&[SessionPickerEntry]>,
    query: &str,
) -> Vec<usize> {
    let Some(entries) = entries else {
        return vec![];
    };
    let q = query.to_lowercase();
    entries
        .iter()
        .enumerate()
        .filter(|(_, e)| {
            query.is_empty()
                || fuzzy_matches_session(&e.id, &q)
                || fuzzy_matches_session(&e.summary, &q)
        })
        .map(|(i, _)| i)
        .collect()
}

// ---------------------------------------------------------------------------
// Entry map building
// ---------------------------------------------------------------------------

/// Rebuild expansion keys in the backing-data index space used by session rendering.
pub(crate) fn expand_all_mapped_session_items(
    state: &mut PickerState,
    entry_map: &[Option<PickerItem>],
) {
    state.expanded.clear();
    if state.query().is_empty() {
        return;
    }
    for item in entry_map.iter().flatten() {
        let PickerItem::Fuzzy { original_index } = item;
        state.expanded.insert(*original_index);
    }
}

/// Build the position-indexed session map, including non-selectable headers.
pub(crate) fn build_entry_map(
    entries: Option<&[SessionPickerEntry]>,
    query: &str,
    grouped: bool,
    current_repo: Option<&str>,
) -> Vec<Option<PickerItem>> {
    let filtered = filter_session_entries(entries, query);
    if grouped {
        let entries_data = entries.unwrap_or(&[]);
        let mut map: Vec<Option<PickerItem>> = Vec::new();
        let mut groups: IndexMap<&str, Vec<usize>> = IndexMap::new();
        for &orig_idx in &filtered {
            groups
                .entry(entries_data[orig_idx].repo_name.as_str())
                .or_default()
                .push(orig_idx);
        }
        order_repo_groups(&mut groups, current_repo);
        for (_repo, members) in &groups {
            map.push(None); // repo group header
            for &orig_idx in members {
                map.push(Some(PickerItem::Fuzzy {
                    original_index: orig_idx,
                }));
            }
        }
        map
    } else {
        filtered
            .into_iter()
            .map(|original_index| Some(PickerItem::Fuzzy { original_index }))
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum SessionPickerWorktreeSelection {
    Fuzzy(usize),
    Unavailable,
}

/// Resolve Ctrl+W before generic editing because the line editor binds it to delete-word.
pub(crate) fn session_picker_worktree_selection(
    key: &crossterm::event::KeyEvent,
    state: &mut PickerState,
    entry_map: &[Option<PickerItem>],
    non_selectable: &[bool],
    entries: Option<&[SessionPickerEntry]>,
) -> Option<SessionPickerWorktreeSelection> {
    if key.kind != crossterm::event::KeyEventKind::Press || !crate::key!('w', CONTROL).matches(key)
    {
        return None;
    }
    if entry_map.is_empty() {
        return Some(SessionPickerWorktreeSelection::Unavailable);
    }
    crate::views::picker::clamp_picker_selection(state, entry_map.len(), non_selectable);
    Some(
        match entry_map
            .get(state.selected)
            .and_then(|entry| entry.as_ref())
        {
            Some(PickerItem::Fuzzy { original_index }) => entries
                .and_then(|entries| entries.get(*original_index))
                .map_or(SessionPickerWorktreeSelection::Unavailable, |_| {
                    SessionPickerWorktreeSelection::Fuzzy(*original_index)
                }),
            None => SessionPickerWorktreeSelection::Unavailable,
        },
    )
}

/// Rebuild backing-index expansion after a session query changes.
pub(crate) fn sync_session_picker_query_expansion(
    entries: Option<&[SessionPickerEntry]>,
    state: &mut PickerState,
    grouped: bool,
    current_repo: Option<&str>,
) {
    let entry_map = build_entry_map(entries, state.query(), grouped, current_repo);
    expand_all_mapped_session_items(state, &entry_map);
}

// ---------------------------------------------------------------------------
// Session entry data building
// ---------------------------------------------------------------------------

/// Build owned rendering data for each session entry in the filtered list.
///
/// The caller zips the result with `PickerField` slices and builds
/// `PickerEntry` items that borrow from the returned data.
pub(crate) fn build_session_entry_data(
    entries_data: &[SessionPickerEntry],
    filtered_indices: &[usize],
    state: &PickerState,
    content_width: u16,
) -> Vec<SessionEntryData> {
    use crate::render::line_utils::truncate_str;

    filtered_indices
        .iter()
        .enumerate()
        .map(|(fi, &orig_idx)| {
            let entry = &entries_data[orig_idx];
            let summary = if entry.summary.is_empty() {
                "(no prompt)".to_string()
            } else {
                entry.summary.clone()
            };
            // Prefer last_active_at; fall back to updated_at (not created_at)
            // so pre-migration sessions don't jump to their creation date.
            let right_text = format_time_ago(entry.last_active_at.unwrap_or(entry.updated_at));
            let is_selected = !state.selection_hidden && fi == state.selected;
            let is_expanded = state.expanded.contains(&orig_idx);

            let mut field_data: Vec<(String, String)> = Vec::new();
            if is_expanded {
                field_data.push(("ID".into(), entry.id.clone()));
                field_data.push(("CWD".into(), entry.cwd.clone()));
                if let Some(ref model) = entry.model_id {
                    field_data.push(("Model".into(), model.clone()));
                }
                let fmt_time = |dt: chrono::DateTime<chrono::Utc>| {
                    dt.with_timezone(&chrono::Local)
                        .format("%b %d, %l:%M%P")
                        .to_string()
                };
                field_data.push(("Created".into(), fmt_time(entry.created_at)));
                field_data.push(("Updated".into(), fmt_time(entry.updated_at)));
                field_data.push(("Source".into(), entry.source.clone()));
                if let Some(ref host) = entry.hostname {
                    field_data.push(("Host".into(), host.clone()));
                }
                if entry.num_messages > 0 {
                    field_data.push(("Messages".into(), entry.num_messages.to_string()));
                }
                // Recap ("where was I") and the last-turn summary, whenever
                // available. Truncated to the card width like the Prompt line.
                let max_w = content_width.saturating_sub(4 + 12) as usize;
                if let Some(recap) = entry.last_recap.as_deref().map(str::trim)
                    && !recap.is_empty()
                {
                    field_data.push(("Recap".into(), truncate_str(recap, max_w)));
                }
                if let Some(last_turn) = entry.last_turn_summary.as_deref().map(str::trim)
                    && !last_turn.is_empty()
                {
                    field_data.push(("Last turn".into(), truncate_str(last_turn, max_w)));
                }
                if let Some(ref detail) = entry.card_detail {
                    field_data.push((
                        "Turns".into(),
                        format!("{}    Tools  {}", detail.turn_count, detail.tool_call_count),
                    ));
                    if !detail.first_prompt_preview.is_empty() {
                        let preview = truncate_str(&detail.first_prompt_preview, max_w);
                        field_data.push(("Prompt".into(), preview));
                    }
                }
            }

            SessionEntryData {
                summary,
                right_text,
                is_selected,
                is_expanded,
                field_data,
                badge: "",
                collapsible: true,
            }
        })
        .collect()
}

/// Build grouped picker entries: sessions grouped by `repo_name` (the
/// current working directory's repo pinned first, the rest alphabetical)
/// with non-selectable `Header` rows separating each group. Returns the
/// entry list and a boolean mask where `true` marks non-selectable header rows.
pub(crate) fn build_grouped_picker_entries<'a>(
    entries_data: &'a [SessionPickerEntry],
    filtered_indices: &[usize],
    built: &'a [SessionEntryData],
    fields_vecs: &'a [Vec<PickerField<'a>>],
    state: &PickerState,
    current_repo: Option<&str>,
) -> (Vec<PickerEntry<'a>>, Vec<bool>) {
    // Group filtered entries by repo_name, sort alphabetically, then pin the
    // current working directory's repo group to the top.
    let mut groups: IndexMap<&str, Vec<usize>> = IndexMap::new();
    for (fi, &orig_idx) in filtered_indices.iter().enumerate() {
        let repo = entries_data[orig_idx].repo_name.as_str();
        groups.entry(repo).or_default().push(fi);
    }
    order_repo_groups(&mut groups, current_repo);

    let mut result: Vec<PickerEntry<'a>> = Vec::new();
    let mut non_selectable: Vec<bool> = Vec::new();

    // Track the grouped position (including headers) to correctly compute selection.
    let mut grouped_pos: usize = 0;
    for (repo_name, member_indices) in &groups {
        // Insert a non-selectable header for this repo group.
        non_selectable.push(true);
        result.push(PickerEntry::Header { label: repo_name });
        grouped_pos += 1;

        // Insert each session row indented under the header.
        for &fi in member_indices {
            let b = &built[fi];
            let fields = &fields_vecs[fi];
            non_selectable.push(false);
            // Use grouped position for selection, not flat filtered index.
            let selected = !state.selection_hidden && grouped_pos == state.selected;
            result.push(PickerEntry::Row(PickerRow {
                label: &b.summary,
                right_label: &b.right_text,
                selected,
                expanded: b.is_expanded,
                fields,
                description_lines: &[],
                summary_lines: &[],
                dimmed: false,
                indent: 1,
                badge: b.badge,
                badge_color: None,
                collapsible: b.collapsible,
                underline_last_desc: false,
            }));
            grouped_pos += 1;
        }
    }

    (result, non_selectable)
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/// Format a timestamp as a human-readable relative time.
pub(crate) fn format_time_ago(dt: chrono::DateTime<chrono::Utc>) -> String {
    let now = chrono::Utc::now();
    let duration = now.signed_duration_since(dt);

    let raw = if duration.num_minutes() < 1 {
        "just now".to_string()
    } else if duration.num_minutes() < 60 {
        format!("{}m ago", duration.num_minutes())
    } else if duration.num_hours() < 24 {
        format!("{}h ago", duration.num_hours())
    } else if duration.num_days() < 30 {
        format!("{}d ago", duration.num_days())
    } else {
        format!("{}mo ago", duration.num_days() / 30)
    };
    // Right-align to fixed width so the column doesn't jump
    format!("{:>8}", raw)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_name_from_cwd_two_components() {
        assert_eq!(repo_name_from_cwd("/home/user/fw/1"), "fw-1");
    }

    #[test]
    fn repo_name_from_cwd_standard_path() {
        assert_eq!(repo_name_from_cwd("/home/user/pi"), "user-pi");
    }

    #[test]
    fn repo_name_from_cwd_empty() {
        assert_eq!(repo_name_from_cwd(""), "unknown");
    }

    #[test]
    fn repo_name_from_cwd_deep_path() {
        assert_eq!(
            repo_name_from_cwd("/home/user/projects/rust/myapp"),
            "rust-myapp"
        );
    }

    #[test]
    fn repo_name_from_cwd_root() {
        assert_eq!(repo_name_from_cwd("/"), "/");
    }

    #[test]
    fn repo_name_from_cwd_single_dir() {
        assert_eq!(repo_name_from_cwd("/pi"), "pi");
    }

    /// Substring-only title matching: the old ordered-chars fallback let
    /// short queries match most titles, drowning real hits in junk rows.
    #[test]
    fn fuzzy_matches_session_is_substring_only() {
        assert!(fuzzy_matches_session("Rust-Check pipeline", "rust-check"));
        assert!(fuzzy_matches_session("Fix session picker", "picker"));
        assert!(fuzzy_matches_session("anything", ""));
        assert!(
            !fuzzy_matches_session("rust-check", "rc"),
            "ordered-chars subsequence must no longer match"
        );
    }

    /// Rows are filtered locally by case-insensitive substring on title or id.
    #[test]
    fn filter_session_entries_matches_title_or_id() {
        let mut roadmap = make_entry("sess-1", "r");
        roadmap.summary = "Quarterly roadmap notes".into();
        let entries = vec![roadmap, make_entry("needle-2", "r")];

        assert_eq!(
            filter_session_entries(Some(&entries), ""),
            vec![0, 1],
            "an empty query keeps every row"
        );
        assert_eq!(
            filter_session_entries(Some(&entries), "ROADMAP"),
            vec![0],
            "title matching is case-insensitive"
        );
        assert_eq!(
            filter_session_entries(Some(&entries), "needle"),
            vec![1],
            "the session id matches too"
        );
        assert!(
            filter_session_entries(Some(&entries), "hit").is_empty(),
            "an unrelated query hides every row"
        );
        assert!(filter_session_entries(None, "x").is_empty());
    }

    fn make_entry(id: &str, repo: &str) -> SessionPickerEntry {
        SessionPickerEntry {
            id: id.into(),
            summary: id.into(),
            updated_at: chrono::Utc::now(),
            created_at: chrono::Utc::now(),
            cwd: format!("/{repo}"),
            hostname: None,
            source: String::new(),
            model_id: None,
            num_messages: 0,
            last_active_at: None,
            branch: None,
            repo_name: repo.into(),
            worktree_label: None,
            last_turn_summary: None,
            last_recap: None,
            card_detail: None,
        }
    }

    /// Grouped entry map places repo headers and resolves mouse-click
    /// indices to the correct original session index.
    #[test]
    fn grouped_entry_map_resolves_correct_session() {
        let entries = vec![
            make_entry("s0", "repo-b"),
            make_entry("s1", "repo-a"),
            make_entry("s2", "repo-a"),
        ];

        let map = build_entry_map(Some(&entries), "", true, None);

        // Expected layout (sorted by repo_name):
        //   0: None          (header "repo-a")
        //   1: Fuzzy(orig=1)  (s1 under repo-a)
        //   2: Fuzzy(orig=2)  (s2 under repo-a)
        //   3: None          (header "repo-b")
        //   4: Fuzzy(orig=0)  (s0 under repo-b)
        assert_eq!(map.len(), 5);
        assert!(map[0].is_none(), "repo-a header");
        assert!(
            matches!(map[1], Some(PickerItem::Fuzzy { original_index: 1 })),
            "first session under repo-a"
        );
        assert!(
            matches!(map[2], Some(PickerItem::Fuzzy { original_index: 2 })),
            "second session under repo-a"
        );
        assert!(map[3].is_none(), "repo-b header");
        assert!(
            matches!(map[4], Some(PickerItem::Fuzzy { original_index: 0 })),
            "session under repo-b"
        );

        // Mouse-click index 1 (first data row) must resolve to s1, not s0.
        match &map[1] {
            Some(PickerItem::Fuzzy { original_index }) => {
                assert_eq!(*original_index, 1);
                assert_eq!(entries[*original_index].id, "s1");
            }
            other => panic!("expected Fuzzy, got {other:?}"),
        }

        // Mouse-click index 4 (under repo-b header) resolves to s0.
        match &map[4] {
            Some(PickerItem::Fuzzy { original_index }) => {
                assert_eq!(*original_index, 0);
                assert_eq!(entries[*original_index].id, "s0");
            }
            other => panic!("expected Fuzzy, got {other:?}"),
        }
    }

    /// The flat (ungrouped) map lists the filtered rows only, without headers.
    #[test]
    fn flat_entry_map_lists_filtered_rows_without_headers() {
        let entries = vec![make_entry("s0", "r"), make_entry("s1", "r")];

        let map = build_entry_map(Some(&entries), "", false, None);
        assert_eq!(map.len(), 2);
        assert!(matches!(
            map[0],
            Some(PickerItem::Fuzzy { original_index: 0 })
        ));
        assert!(matches!(
            map[1],
            Some(PickerItem::Fuzzy { original_index: 1 })
        ));

        let map = build_entry_map(Some(&entries), "s1", false, None);
        assert_eq!(map.len(), 1, "only the matching row remains");
        assert!(matches!(
            map[0],
            Some(PickerItem::Fuzzy { original_index: 1 })
        ));
    }

    /// A query expands every matching row, keyed by the backing-data index
    /// (not the visual position); group headers are never items.
    #[test]
    fn expand_all_mapped_session_items_uses_backing_indices() {
        let entries = vec![make_entry("zero", "repo-a"), make_entry("needle", "repo-b")];
        let map = build_entry_map(Some(&entries), "needle", true, None);
        // [repo-b header, needle]
        assert_eq!(map.len(), 2);
        let mut state = PickerState::default();
        state.set_query("needle");

        expand_all_mapped_session_items(&mut state, &map);

        assert!(!state.expanded.contains(&0), "group header is not an item");
        assert!(
            state.expanded.contains(&1),
            "the matching row is expanded by its backing index"
        );
        assert_eq!(state.expanded.len(), 1);

        state.set_query("");
        expand_all_mapped_session_items(&mut state, &map);
        assert!(
            state.expanded.is_empty(),
            "an empty query collapses everything"
        );
    }

    /// Empty entries list produces empty map.
    #[test]
    fn empty_entries_produces_empty_map() {
        let map = build_entry_map(None, "", true, None);
        assert!(map.is_empty());

        let map = build_entry_map(Some(&[]), "", false, None);
        assert!(map.is_empty());
    }

    /// The expanded resume card surfaces the recap and last-turn summary when
    /// present, and omits those rows when absent.
    #[test]
    fn expanded_card_shows_recap_and_last_turn_when_present() {
        let mut entry = make_entry("s_recap", "repo");
        entry.source = "local".into();
        entry.last_recap = Some("Where we left off: auth refactor".into());
        entry.last_turn_summary = Some("Wired retries into billing".into());
        let mut state = PickerState::default();
        state.expanded.insert(0);

        let built = build_session_entry_data(&[entry], &[0], &state, 80);
        let has = |label: &str, value: &str| {
            built[0]
                .field_data
                .iter()
                .any(|(l, v)| l == label && v.contains(value))
        };
        assert!(has("Recap", "auth refactor"), "recap row missing");
        assert!(has("Last turn", "billing"), "last-turn row missing");

        // Absent when the entry has neither.
        let bare = make_entry("s_bare", "repo");
        let mut state = PickerState::default();
        state.expanded.insert(0);
        let built = build_session_entry_data(&[bare], &[0], &state, 80);
        assert!(
            !built[0]
                .field_data
                .iter()
                .any(|(l, _)| l == "Recap" || l == "Last turn"),
            "recap/last-turn rows must be omitted when absent"
        );
    }

    /// `current_repo` pins the matching group to the top; remaining groups
    /// stay alphabetical. Without it, groups are purely alphabetical.
    #[test]
    fn grouped_entry_map_pins_current_repo_first() {
        let entries = vec![
            make_entry("s0", "repo-a"),
            make_entry("s1", "repo-b"),
            make_entry("s2", "repo-c"),
        ];

        // Pin repo-c: its group leads, then repo-a, repo-b alphabetically.
        let map = build_entry_map(Some(&entries), "", true, Some("repo-c"));
        // [repo-c hdr, s2, repo-a hdr, s0, repo-b hdr, s1]
        assert_eq!(map.len(), 6);
        assert!(map[0].is_none(), "repo-c header pinned first");
        assert!(matches!(
            map[1],
            Some(PickerItem::Fuzzy { original_index: 2 })
        ));
        assert!(map[2].is_none(), "repo-a header");
        assert!(matches!(
            map[3],
            Some(PickerItem::Fuzzy { original_index: 0 })
        ));
        assert!(map[4].is_none(), "repo-b header");
        assert!(matches!(
            map[5],
            Some(PickerItem::Fuzzy { original_index: 1 })
        ));

        // A current_repo with no matching group is a no-op (pure alphabetical).
        let map = build_entry_map(Some(&entries), "", true, Some("repo-zzz"));
        assert!(map[0].is_none(), "repo-a header");
        assert!(matches!(
            map[1],
            Some(PickerItem::Fuzzy { original_index: 0 })
        ));
    }

    #[test]
    fn session_id_for_direct_load_accepts_uuid_only() {
        let sid = "019fb61a-85a5-7ba0-a4ec-24647dca1893";
        assert_eq!(session_id_for_direct_load(sid), Some(sid));
        assert_eq!(session_id_for_direct_load(&format!("  {sid}  ")), Some(sid));
        assert_eq!(session_id_for_direct_load("not-a-uuid"), None);
        assert_eq!(session_id_for_direct_load(""), None);
        assert_eq!(session_id_for_direct_load("pasted garbage!!!"), None);
        assert_eq!(session_id_for_direct_load("hello\nworld"), None);
        assert_eq!(session_id_for_direct_load(&format!("{sid}\nextra")), None);
    }
}
