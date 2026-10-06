//! Session list fetching and its completion handlers for the `/resume`
//! picker (modal) and the welcome-screen picker.

use crate::app::actions::Effect;
use crate::app::app_view::{AppView, SessionPickerEntry};
use crate::app::dispatch::ctx::get_active_agent_mut;
use crate::views::modal::ActiveModal;
use crate::views::picker::PickerState;
use crate::views::session_picker::{
    PickerSelectionAnchor, capture_picker_selection, repo_name_from_cwd, restore_picker_selection,
};

/// Mutable view over one picker surface (modal or welcome) that a list
/// fetch completes into.
struct PickerSurface<'a> {
    entries: &'a mut Option<Vec<SessionPickerEntry>>,
    loading: &'a mut bool,
    state: &'a mut PickerState,
    grouped: bool,
    current_repo: String,
}

impl PickerSurface<'_> {
    fn capture_selection(&self) -> PickerSelectionAnchor {
        capture_picker_selection(
            self.entries.as_deref(),
            self.state,
            self.state.query(),
            self.grouped,
            Some(&self.current_repo),
        )
    }

    fn restore_selection(&mut self, anchor: PickerSelectionAnchor) {
        let query = self.state.query().to_owned();
        restore_picker_selection(
            anchor,
            self.entries.as_deref(),
            self.state,
            &query,
            self.grouped,
            Some(&self.current_repo),
        );
        self.state.expanded.clear();
    }

    /// Apply a loaded list; returns a toast when the list is empty.
    fn loaded(
        &mut self,
        sessions: Vec<SessionPickerEntry>,
        empty_notice: String,
    ) -> Option<String> {
        let anchor = self.capture_selection();
        *self.loading = false;
        *self.entries = (!sessions.is_empty()).then_some(sessions);
        let notice = self.entries.is_none().then_some(empty_notice);
        self.restore_selection(anchor);
        notice
    }

    /// Apply a failed fetch; always returns the error toast.
    fn failed(&mut self, error_notice: String) -> Option<String> {
        let anchor = self.capture_selection();
        *self.loading = false;
        *self.entries = None;
        self.restore_selection(anchor);
        Some(error_notice)
    }
}

pub(in crate::app::dispatch) fn dispatch_fetch_session_list(app: &mut AppView) -> Vec<Effect> {
    app.session_picker_loading = true;
    app.session_picker_entries = None;
    app.session_picker_state.selected = 0;
    app.session_picker_state.set_query("");
    app.session_picker_state.search_active = false;
    app.session_picker_state.expanded.clear();
    vec![Effect::FetchSessionList {
        seq: app.session_picker_list_seq,
    }]
}

pub(in crate::app::dispatch) fn handle_session_list_loaded(
    app: &mut AppView,
    sessions: Vec<SessionPickerEntry>,
    seq: u64,
) -> Vec<Effect> {
    if seq != app.session_picker_list_seq {
        return vec![];
    }
    let empty_notice = "No sessions found for this directory".to_owned();
    let mut sessions = Some(sessions);
    let mut notice = None;
    if let Some(agent) = get_active_agent_mut(app) {
        let current_repo = repo_name_from_cwd(&agent.session.cwd.to_string_lossy());
        if let Some(ActiveModal::SessionPicker {
            entries,
            loading,
            state,
            ..
        }) = agent.active_modal.as_mut()
        {
            notice = PickerSurface {
                entries,
                loading,
                state,
                grouped: true,
                current_repo,
            }
            .loaded(sessions.take().unwrap_or_default(), empty_notice.clone());
        }
    }
    if let Some(sessions) = sessions {
        let current_repo = repo_name_from_cwd(&app.cwd.to_string_lossy());
        notice = PickerSurface {
            entries: &mut app.session_picker_entries,
            loading: &mut app.session_picker_loading,
            state: &mut app.session_picker_state,
            grouped: app.session_picker_grouped,
            current_repo,
        }
        .loaded(sessions, empty_notice);
    }
    if let Some(notice) = notice {
        app.show_toast(&notice);
    }
    vec![]
}

pub(in crate::app::dispatch) fn handle_session_list_failed(
    app: &mut AppView,
    error: String,
    seq: u64,
) -> Vec<Effect> {
    if seq != app.session_picker_list_seq {
        return vec![];
    }
    tracing::warn!(error = %error, "session list fetch failed");
    let error_notice = format!("Couldn't load sessions: {error}");
    let mut handled = false;
    let mut notice = None;
    if let Some(agent) = get_active_agent_mut(app) {
        let current_repo = repo_name_from_cwd(&agent.session.cwd.to_string_lossy());
        if let Some(ActiveModal::SessionPicker {
            entries,
            loading,
            state,
            ..
        }) = agent.active_modal.as_mut()
        {
            notice = PickerSurface {
                entries,
                loading,
                state,
                grouped: true,
                current_repo,
            }
            .failed(error_notice.clone());
            handled = true;
        }
    }
    if !handled {
        let current_repo = repo_name_from_cwd(&app.cwd.to_string_lossy());
        notice = PickerSurface {
            entries: &mut app.session_picker_entries,
            loading: &mut app.session_picker_loading,
            state: &mut app.session_picker_state,
            grouped: app.session_picker_grouped,
            current_repo,
        }
        .failed(error_notice);
    }
    if let Some(notice) = notice {
        app.show_toast(&notice);
    }
    vec![]
}
