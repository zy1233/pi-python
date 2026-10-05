use super::*;

/// Result of looking up which view a notification's `session_id` targets.
///
/// The matched view's mutation must happen on the agent identified here,
/// regardless of which view the user is currently looking at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SessionMatch {
    /// The session_id matches the root session of this agent.
    Root(AgentId),
    /// The session_id matches a subagent view child of this agent
    /// (i.e. an entry in `agent.subagent_views`). The child's key is the
    /// notification's `session_id.0.as_ref()`; the caller re-derives it
    /// to avoid an extra allocation.
    Child(AgentId),
}

impl SessionMatch {
    /// The owning agent's id, regardless of variant.
    ///
    /// For `Root`, this is the agent whose root session matched. For `Child`,
    /// this is the parent agent that owns the matching `subagent_views` entry.
    /// Callers that only need to look up the owning agent (without
    /// distinguishing root vs child) should use this instead of duplicating
    /// the `match { Root(id) | Child(id) => id }` pattern.
    pub(super) fn agent_id(self) -> AgentId {
        match self {
            SessionMatch::Root(id) | SessionMatch::Child(id) => id,
        }
    }
}

/// Locate the agent (or subagent view) a notification's `session_id` belongs to.
///
/// Search order:
/// 1. Exact root match: an agent whose `session.session_id` equals `session_id`.
/// 2. Subagent view: any agent whose `subagent_views` map contains `session_id`
///    as a key.
/// 3. Race-window fallback: when no exact match exists AND the currently active
///    agent has no `session_id` yet, route to it. Notifications can race ahead
///    of `TaskResult::SessionCreated`, and the only agent that could possibly
///    own such a pre-assignment notification is the one the user just created
///    (which is necessarily active and has `session_id == None`).
///
/// Returns `None` when the notification cannot be associated with any agent;
/// the caller should drop it (sending an empty Ok response if applicable).
///
/// All ACP-notification handlers must route through this function rather than
/// gating on `app.active_view` directly; see the `handle_scheduled_task_*`
/// family for the legacy active-view pattern still pending migration.
pub(super) fn find_session_match(
    app: &AppView,
    session_id: &acp::SessionId,
) -> Option<SessionMatch> {
    // Single pass over `app.agents`: prefer an exact root match (returned
    // immediately, since root takes precedence) but track the first child
    // match seen as a fallback used after the full scan completes.
    //
    // Comparing `Option<&SessionId>` to `Some(&session_id)` borrows both
    // sides -- no SessionId clone. The HashMap lookup uses the inner `&str`
    // directly via the `Borrow<str>` impl on `String`, so no allocation
    // either. This preserves the previous two-pass semantics (root wins
    // when both could match) while halving the iteration cost on the hot
    // notification path.
    let child_key: &str = session_id.0.as_ref();
    let mut child_match: Option<AgentId> = None;
    for (id, agent) in &app.agents {
        if agent.session.session_id.as_ref() == Some(session_id) {
            return Some(SessionMatch::Root(*id));
        }
        if child_match.is_none() && agent.subagent_views.contains_key(child_key) {
            child_match = Some(*id);
        }
    }
    if let Some(id) = child_match {
        return Some(SessionMatch::Child(id));
    }
    // Pass 3: race-window fallback for notifications that arrive before the
    // root session_id has been assigned. Only the active agent is eligible,
    // and only when its `session_id` is still `None` -- otherwise we would
    // misroute a stranger's notification to whichever agent happens to be
    // foregrounded.
    if let ActiveView::Agent(active_id) = app.active_view
        && let Some(agent) = app.agents.get(&active_id)
        && agent.session.session_id.is_none()
    {
        return Some(SessionMatch::Root(active_id));
    }
    None
}

/// Whether the matched agent is the one currently displayed.
pub(super) fn is_matched_agent_active(app: &AppView, matched_agent: AgentId) -> bool {
    matches!(app.active_view, ActiveView::Agent(id) if id == matched_agent)
}

