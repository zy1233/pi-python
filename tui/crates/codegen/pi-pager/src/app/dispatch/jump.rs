//! `/jump` picker dispatchers: pure client-side turn navigation.

use crate::app::actions::Effect;
use crate::app::app_view::{ActiveView, AppView};
use crate::scrollback::entry::EntryId;

pub(super) fn dispatch_jump_picker_select(app: &mut AppView, prompt_id: EntryId) -> Vec<Effect> {
    let ActiveView::Agent(id) = app.active_view else {
        return vec![];
    };
    let Some(agent) = app.agents.get_mut(&id) else {
        return vec![];
    };
    let Some(js) = agent.jump_state.take() else {
        return vec![];
    };
    // The stable id resolves at the boundary; it fails only if the prompt was
    // removed (async clear/rewind) while the picker was open. Restore the
    // captured viewport so a failed jump never strands the transcript at the
    // last preview scroll.
    if !agent.scrollback.jump_to_entry(prompt_id) {
        agent.restore_jump_viewport(js.restore);
    }
    vec![]
}

pub(super) fn dispatch_jump_dismiss(app: &mut AppView) -> Vec<Effect> {
    let ActiveView::Agent(id) = app.active_view else {
        return vec![];
    };
    let Some(agent) = app.agents.get_mut(&id) else {
        return vec![];
    };
    agent.dismiss_jump_picker();
    vec![]
}
