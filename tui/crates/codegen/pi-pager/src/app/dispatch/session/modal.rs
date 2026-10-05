//! Session close helpers.
//!
//! The `/sessions` picker modal was removed; the remaining entry point
//! (session close) goes through these helpers.
use crate::app::agent::AgentId;
use crate::app::app_view::AppView;
/// Remove an agent and clean up all references to it:
/// `forked_from` pointers on surviving agents.
pub(in crate::app::dispatch) fn remove_agent_and_cleanup(app: &mut AppView, agent_id: AgentId) {
    let removed = app.agents.shift_remove(&agent_id);
    for agent in app.agents.values_mut() {
        if agent.session.forked_from == Some(agent_id) {
            agent.session.forked_from = None;
        }
    }
    if removed.is_some() {
        drop(removed);
        crate::memory_release::release_retained_memory("agent-close");
    }
}
