//! Session rename / close helpers.
//!
//! The `/sessions` picker modal was removed; the remaining entry points
//! (session close, rename-by-title) go through these dispatchers.
use crate::app::actions::Effect;
use crate::app::agent::AgentId;
use crate::app::app_view::{ActiveView, AppView};
use crate::scrollback::block::RenderBlock;
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
/// Rename the current session via legacy ext RPC
///
/// Produces Effect::RenameSession which spawns an async ACP ext request.
/// On completion, TaskResult::RenameSessionComplete shows the result.
pub(in crate::app::dispatch) fn dispatch_rename_session(
    app: &mut AppView,
    title: String,
) -> Vec<Effect> {
    let ActiveView::Agent(id) = app.active_view else {
        return vec![];
    };
    let Some(agent) = app.agents.get_mut(&id) else {
        return vec![];
    };
    let Some(session_id) = agent.session.session_id.clone() else {
        return vec![];
    };
    let title = pi_shell::session::persistence::sanitize_rename_title(&title).into_owned();
    if title.is_empty() {
        agent.scrollback.push_block(RenderBlock::system(
            "Couldn't rename session: title must not be blank".to_string(),
        ));
        return vec![];
    }
    agent.display_name = Some(title.clone());
    vec![Effect::RenameSession {
        agent_id: id,
        session_id,
        title,
        cwd: agent.session.cwd.clone(),
        kind: agent.rename_kind(),
    }]
}
