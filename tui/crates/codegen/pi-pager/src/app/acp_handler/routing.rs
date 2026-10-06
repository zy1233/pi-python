use super::*;

/// Locate the agent a notification's `session_id` belongs to.
///
/// The matched agent's state must be mutated regardless of which view the
/// user is currently looking at.
///
/// Search order:
/// 1. Exact root match: an agent whose `session.session_id` equals `session_id`.
/// 2. Race-window fallback: when no exact match exists AND the currently active
///    agent has no `session_id` yet, route to it. Notifications can race ahead
///    of `TaskResult::SessionCreated`, and the only agent that could possibly
///    own such a pre-assignment notification is the one the user just created
///    (which is necessarily active and has `session_id == None`).
///
/// Returns `None` when the notification cannot be associated with any agent;
/// the caller should drop it (sending an empty Ok response if applicable).
///
/// All ACP-notification handlers must route through this function rather than
/// gating on `app.active_view` directly.
pub(super) fn find_session_match(app: &AppView, session_id: &acp::SessionId) -> Option<AgentId> {
    // Comparing `Option<&SessionId>` to `Some(&session_id)` borrows both
    // sides -- no SessionId clone on the hot notification path.
    for (id, agent) in &app.agents {
        if agent.session.session_id.as_ref() == Some(session_id) {
            return Some(*id);
        }
    }
    // Race-window fallback for notifications that arrive before the root
    // session_id has been assigned. Only the active agent is eligible, and
    // only when its `session_id` is still `None` -- otherwise we would
    // misroute a stranger's notification to whichever agent happens to be
    // foregrounded.
    if let ActiveView::Agent(active_id) = app.active_view
        && let Some(agent) = app.agents.get(&active_id)
        && agent.session.session_id.is_none()
    {
        return Some(active_id);
    }
    None
}

/// Whether the matched agent is the one currently displayed.
pub(super) fn is_matched_agent_active(app: &AppView, matched_agent: AgentId) -> bool {
    matches!(app.active_view, ActiveView::Agent(id) if id == matched_agent)
}
