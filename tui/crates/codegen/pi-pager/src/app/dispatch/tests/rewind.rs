//! Tests for conversation rewind dispatchers and prompt-entry lookup.

use super::*;

#[test]
fn cancel_does_not_rewind_when_in_flight_block_committed() {
    // Minimal-mode regression: a user-prompt block commits to native
    // scrollback immediately (it is never `is_running`), and a committed
    // block can't be "un-printed". Cancelling must NOT rewind such a block —
    // doing so would `remove_entry` it from state while the printed copy
    // stays on screen AND restore the text into the input, showing the prompt
    // twice (dogfood bug: double-Esc on a just-promoted queued prompt).
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    dispatch(Action::SendPrompt("queued prompt".into()), &mut app);
    assert!(app.agents[&id].session.in_flight_prompt.is_some());
    assert_eq!(app.agents[&id].scrollback.len(), 1);

    // Simulate minimal's commit pass printing the user block into native
    // scrollback (sets the entry's `committed` flag).
    let entry_id = app.agents[&id]
        .session
        .in_flight_prompt
        .as_ref()
        .unwrap()
        .scrollback_entry;
    let idx = app.agents[&id].scrollback.index_of_id(entry_id).unwrap();
    app.agents
        .get_mut(&id)
        .unwrap()
        .scrollback
        .mark_committed(idx);

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::CancelTurn { .. }));

    // Standard cancel, NOT the rewind: the prompt is not restored to the
    // input and the committed block stays in scrollback (no duplicate).
    assert!(
        app.agents[&id].prompt.text().is_empty(),
        "committed in-flight block must not be rewound into the input"
    );
    assert_eq!(
        app.agents[&id].scrollback.len(),
        1,
        "committed block must stay in scrollback (it's already printed)"
    );
    assert!(app.agents[&id].session.state.is_cancelling());
}

#[test]
fn rewind_then_resubmit_drains_immediately_and_discards_orphan() {
    // After a rewind, state is Idle so a follow-up prompt can drain
    // without waiting for the cancelled turn's PromptResponse.
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    dispatch(Action::SendPrompt("first".into()), &mut app);
    let first_pid = app.agents[&id].session.current_prompt_id.clone();
    assert!(first_pid.is_some());
    dispatch(Action::CancelTurn, &mut app);
    assert!(app.agents[&id].session.state.is_idle());
    assert!(app.agents[&id].session.current_prompt_id.is_none());

    // User edits and re-submits without waiting.
    let effects = dispatch(Action::SendPrompt("second".into()), &mut app);
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "second"));
    assert!(app.agents[&id].session.state.is_turn_running());
    let second_pid = app.agents[&id].session.current_prompt_id.clone();
    assert!(second_pid.is_some());
    assert_ne!(first_pid, second_pid);

    // The cancelled "first" PromptResponse arrives mid-second-turn,
    // carrying first_pid. Mismatch with current_prompt_id (second_pid)
    // → discarded. State for "second" is untouched.
    dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled).meta(
                serde_json::json!({ "promptId": first_pid })
                    .as_object()
                    .cloned(),
            )),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    assert!(app.agents[&id].session.state.is_turn_running());
    assert_eq!(app.agents[&id].session.current_prompt_id, second_pid);
}

/// Ctrl+C rewind cancel carries the rewound turn's prompt id.
#[test]
fn cancel_rewind_effect_carries_the_rewound_prompt_id() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    dispatch(Action::SendPrompt("rewind me".into()), &mut app);
    let pid = app.agents[&id]
        .session
        .current_prompt_id
        .clone()
        .expect("turn running");

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                rewind_prompt_id: Some(p),
                ..
            }] if *p == pid
        ),
        "rewind cancel must carry the captured prompt id, got {effects:?}"
    );
    let agent = &app.agents[&id];
    assert!(agent.session.state.is_idle());
    assert_eq!(agent.prompt.text(), "rewind me");
    assert!(agent.is_rewound_prompt(&pid));
}

/// No prompt id → no optimistic rewind; send a standard cancel.
#[test]
fn cancel_without_prompt_id_skips_rewind_and_sends_normal_cancel() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    dispatch(Action::SendPrompt("cannot rewind".into()), &mut app);
    assert!(app.agents[&id].session.in_flight_prompt.is_some());
    // Simulate the id being gone while the stash survives.
    app.agents.get_mut(&id).unwrap().session.current_prompt_id = None;

    let effects = dispatch(Action::CancelTurn, &mut app);
    assert!(
        matches!(
            effects.as_slice(),
            [Effect::CancelTurn {
                rewind_prompt_id: None,
                ..
            }]
        ),
        "id-less cancel must not request a rewind, got {effects:?}"
    );
    let agent = &app.agents[&id];
    assert!(
        agent.prompt.text().is_empty(),
        "no optimistic composer restore without an id"
    );
    assert_eq!(
        agent.scrollback.len(),
        1,
        "the prompt block stays in scrollback (standard cancel)"
    );
    assert!(agent.session.state.is_cancelling());
}

/// Set up an app whose agent has one user prompt + one agent message in
/// the transcript, is inline-editing that prompt with `edited` typed in,
/// and has an unrelated draft sitting in the composer.
fn app_mid_inline_edit(edited: &str) -> AppView {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    agent
        .scrollback
        .push_block(RenderBlock::user_prompt("fix the bug"));
    agent
        .scrollback
        .push_block(RenderBlock::agent_message("done"));
    agent.scrollback.prepare_layout(80, 40);
    assert!(agent.enter_inline_edit(0));
    agent
        .inline_edit
        .as_mut()
        .unwrap()
        .textarea
        .set_text(edited);
    agent.prompt.set_text("composer draft");
    app
}

/// Rewind point for the fixture's single prompt.
fn rewind_point(prompt_index: usize) -> crate::views::rewind::RewindPointInfo {
    crate::views::rewind::RewindPointInfo {
        prompt_index,
        created_at: String::new(),
        num_file_snapshots: 0,
        prompt_preview: Some("fix the bug".into()),
        has_file_changes: false,
    }
}

/// Points-loaded task result carrying the fixture's single rewind point.
fn points_loaded(id: AgentId) -> Action {
    Action::TaskComplete(TaskResult::RewindPointsLoaded {
        agent_id: id,
        points: vec![rewind_point(0)],
    })
}

/// Drive an idle inline-edit submit through to execute: points fetch →
/// confirm (setting on) → Executing. Returns the effects of the confirm step.
fn drive_inline_submit_to_execute(app: &mut AppView) -> Vec<Effect> {
    let id = AgentId(0);
    let effects = dispatch(Action::InlineEditSubmit, app);
    assert!(
        matches!(&effects[0], Effect::FetchRewindPoints { .. }),
        "got {effects:?}"
    );
    dispatch(points_loaded(id), app);
    // Confirm-before-rewind (default on) gates every target, including 0.
    assert!(matches!(
        app.agents[&id].rewind_state.as_ref().unwrap().phase,
        crate::views::rewind::RewindPhase::Confirm { .. }
    ));
    dispatch(Action::RewindConfirm(0), app)
}

/// Submitting an inline edit enters the exact same flow as `/rewind`: a
/// Loading overlay + a points fetch pre-targeted at the edited prompt. The
/// editor stays open behind the flow and nothing is stashed yet.
#[test]
fn inline_edit_submit_enters_rewind_flow_via_points_fetch() {
    let mut app = app_mid_inline_edit("fix the bug properly");
    let id = AgentId(0);

    let effects = dispatch(Action::InlineEditSubmit, &mut app);

    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::FetchRewindPoints { .. }));
    let agent = &app.agents[&id];
    let state = agent.rewind_state.as_ref().expect("rewind flow entered");
    assert!(matches!(
        state.phase,
        crate::views::rewind::RewindPhase::Loading
    ));
    assert_eq!(
        state.selected_prompt_index,
        Some(0),
        "pre-targeted at the edited prompt"
    );
    assert!(agent.inline_edit.is_some(), "editor stays open");
    assert!(
        agent.pending_inline_resubmit.is_none(),
        "nothing stashed before an execute"
    );
}

/// A submit whose text is unchanged (or empty) has nothing to do: the
/// editor just closes; no rewind flow, no effects.
#[test]
fn inline_edit_submit_with_unchanged_text_closes_editor() {
    let mut app = app_mid_inline_edit("fix the bug");
    let id = AgentId(0);

    let effects = dispatch(Action::InlineEditSubmit, &mut app);

    assert!(effects.is_empty());
    let agent = &app.agents[&id];
    assert!(agent.inline_edit.is_none(), "editor closed");
    assert!(agent.rewind_state.is_none(), "no rewind flow entered");
    assert!(agent.scrollback.inline_edit_height().is_none());
}

/// Points loaded with a pre-selected target skip the picker and open confirm
/// when the setting is on; the editor stays open behind it.
#[test]
fn inline_edit_points_loaded_opens_target_zero_confirm_over_open_editor() {
    let mut app = app_mid_inline_edit("fix the bug properly");
    let id = AgentId(0);
    dispatch(Action::InlineEditSubmit, &mut app);

    let effects = dispatch(points_loaded(id), &mut app);
    assert!(
        effects.is_empty(),
        "confirm setting on waits for Yes/No, got {effects:?}"
    );

    let agent = &app.agents[&id];
    assert!(matches!(
        agent.rewind_state.as_ref().unwrap().phase,
        crate::views::rewind::RewindPhase::Confirm {
            target_prompt_index: 0,
            ..
        }
    ));
    assert!(agent.inline_edit.is_some(), "editor still open");
    assert!(agent.pending_inline_resubmit.is_none());
}

/// Settings action updates the live confirm-before-rewind value.
#[test]
fn set_confirm_before_rewind_updates_live_value() {
    let mut app = test_app_with_agent();
    assert!(app.current_ui.confirm_before_rewind_enabled());

    let effects = dispatch(Action::SetConfirmBeforeRewind(false), &mut app);
    assert!(
        matches!(
            &effects[0],
            Effect::PersistSetting {
                key: "confirm_before_rewind",
                value: crate::settings::SettingValue::Bool(false),
                ..
            }
        ),
        "got {effects:?}"
    );
    assert!(!app.current_ui.confirm_before_rewind_enabled());
    assert_eq!(app.current_ui.confirm_before_rewind, Some(false));

    let effects = dispatch(Action::SetConfirmBeforeRewind(false), &mut app);
    assert!(
        effects.is_empty(),
        "idempotent when already false, got {effects:?}"
    );
}

/// Multi-turn fixture with two user prompts for picker / non-zero target tests.
fn app_with_two_turns() -> AppView {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.session_id = Some(acp::SessionId::new("sess".to_string()));
        for i in 0..2 {
            let mut b = UserPromptBlock::new(format!("turn {i}"));
            b.prompt_index = Some(i);
            agent.scrollback.push_block(RenderBlock::UserPrompt(b));
            agent
                .scrollback
                .push_block(RenderBlock::agent_message("ok"));
        }
        agent.scrollback.prepare_layout(80, 40);
    }
    app
}

/// With confirm-before-rewind on (default), picking a non-zero target opens confirm.
#[test]
fn picker_select_nonzero_target_opens_confirm_when_setting_on() {
    let mut app = app_with_two_turns();
    assert!(app.current_ui.confirm_before_rewind_enabled());
    let id = AgentId(0);

    dispatch(Action::RewindShowPicker, &mut app);
    dispatch(
        Action::TaskComplete(TaskResult::RewindPointsLoaded {
            agent_id: id,
            points: vec![rewind_point(1), rewind_point(0)],
        }),
        &mut app,
    );

    let effects = dispatch(Action::RewindPickerSelect(1), &mut app);
    assert!(
        effects.is_empty(),
        "confirm setting on waits, got {effects:?}"
    );
    assert!(matches!(
        app.agents[&id].rewind_state.as_ref().unwrap().phase,
        crate::views::rewind::RewindPhase::Confirm {
            target_prompt_index: 1,
            active_idx: 0,
            ..
        }
    ));
}

/// Picking any target (including 0) opens confirm when the setting is on.
#[test]
fn picker_select_target_zero_opens_confirm() {
    let mut app = app_with_two_turns();
    let id = AgentId(0);

    dispatch(Action::RewindShowPicker, &mut app);
    dispatch(
        Action::TaskComplete(TaskResult::RewindPointsLoaded {
            agent_id: id,
            points: vec![rewind_point(1), rewind_point(0)],
        }),
        &mut app,
    );

    let effects = dispatch(Action::RewindPickerSelect(0), &mut app);
    assert!(
        effects.is_empty(),
        "confirm setting on waits for Yes/No, got {effects:?}"
    );
    assert!(matches!(
        app.agents[&id].rewind_state.as_ref().unwrap().phase,
        crate::views::rewind::RewindPhase::Confirm {
            target_prompt_index: 0,
            active_idx: 0,
            ..
        }
    ));
}

/// No / Esc dismiss from confirm during inline edit restores the editor draft.
#[test]
fn inline_edit_dismiss_from_confirm_keeps_editor() {
    let mut app = app_mid_inline_edit("fix the bug properly");
    let id = AgentId(0);
    dispatch(Action::InlineEditSubmit, &mut app);
    dispatch(points_loaded(id), &mut app);
    assert!(matches!(
        app.agents[&id].rewind_state.as_ref().unwrap().phase,
        crate::views::rewind::RewindPhase::Confirm { .. }
    ));

    dispatch(Action::RewindDismiss, &mut app);

    let agent = &app.agents[&id];
    assert!(agent.rewind_state.is_none(), "overlay dismissed");
    assert_eq!(
        agent
            .inline_edit
            .as_ref()
            .expect("editor still open")
            .textarea
            .text(),
        "fix the bug properly"
    );
    assert!(agent.pending_inline_resubmit.is_none());
    assert_eq!(
        agent.prompt.text(),
        "composer draft",
        "composer draft restored on dismiss"
    );
}

/// Inline-edit of an older prompt with confirm on (default): opens confirm.
#[test]
fn inline_edit_nonzero_target_opens_confirm_when_setting_on() {
    let mut app = test_app_with_agent();
    assert!(app.current_ui.confirm_before_rewind_enabled());
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.session_id = Some(acp::SessionId::new("sess".to_string()));
        for i in 0..2 {
            let mut b = UserPromptBlock::new(format!("turn {i}"));
            b.prompt_index = Some(i);
            agent.scrollback.push_block(RenderBlock::UserPrompt(b));
            agent
                .scrollback
                .push_block(RenderBlock::agent_message("ok"));
        }
        agent.scrollback.prepare_layout(80, 40);
        assert!(agent.enter_inline_edit(2));
        agent
            .inline_edit
            .as_mut()
            .unwrap()
            .textarea
            .set_text("turn 1 edited");
    }

    dispatch(Action::InlineEditSubmit, &mut app);
    let effects = dispatch(
        Action::TaskComplete(TaskResult::RewindPointsLoaded {
            agent_id: id,
            points: vec![rewind_point(1), rewind_point(0)],
        }),
        &mut app,
    );
    assert!(effects.is_empty(), "confirm setting on, got {effects:?}");
    assert!(matches!(
        app.agents[&id].rewind_state.as_ref().unwrap().phase,
        crate::views::rewind::RewindPhase::Confirm {
            target_prompt_index: 1,
            active_idx: 0,
            ..
        }
    ));
    assert!(app.agents[&id].pending_inline_resubmit.is_none());
}

/// Dismissing the confirm aborts: the overlay closes, nothing was stashed,
/// and the editor is still open with the edit intact.
#[test]
fn inline_edit_dismiss_from_confirm_returns_to_editor() {
    let mut app = app_mid_inline_edit("fix the bug properly");
    let id = AgentId(0);
    dispatch(Action::InlineEditSubmit, &mut app);
    dispatch(points_loaded(id), &mut app);

    dispatch(Action::RewindDismiss, &mut app);

    let agent = &app.agents[&id];
    assert!(agent.rewind_state.is_none());
    assert!(agent.pending_inline_resubmit.is_none());
    assert_eq!(
        agent
            .inline_edit
            .as_ref()
            .expect("editor still open")
            .textarea
            .text(),
        "fix the bug properly"
    );
}

/// Submitting mid-turn raises the same cancel-offer `/rewind` does,
/// pre-targeted at the edited prompt, over the still-open editor;
/// confirming cancels the turn and re-enters the flow via a points fetch.
#[test]
fn inline_edit_busy_submit_cancel_offer_confirm_cancels_and_fetches_points() {
    let mut app = app_mid_inline_edit("fix the bug properly");
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = crate::app::agent::AgentState::TurnRunning;

    let effects = dispatch(Action::InlineEditSubmit, &mut app);
    assert!(effects.is_empty(), "no effects yet: {effects:?}");
    {
        let agent = &app.agents[&id];
        let state = agent.rewind_state.as_ref().unwrap();
        assert!(matches!(
            state.phase,
            crate::views::rewind::RewindPhase::CancelOffer { .. }
        ));
        assert_eq!(state.selected_prompt_index, Some(0));
        assert!(agent.inline_edit.is_some(), "editor open behind the offer");
        assert!(agent.pending_inline_resubmit.is_none());
    }

    let effects = dispatch(Action::RewindCancelOffer, &mut app);
    assert!(matches!(&effects[0], Effect::CancelTurn { .. }));
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::FetchRewindPoints { .. })),
        "got {effects:?}"
    );
    let agent = &app.agents[&id];
    let state = agent.rewind_state.as_ref().unwrap();
    assert!(matches!(
        state.phase,
        crate::views::rewind::RewindPhase::Loading
    ));
    assert_eq!(
        state.selected_prompt_index,
        Some(0),
        "target survives the cancel"
    );
    assert!(agent.inline_edit.is_some(), "editor still open");
}

/// Dismissing the mid-turn cancel-offer ("let it finish") returns straight
/// to the still-open editor with the edit intact.
#[test]
fn inline_edit_busy_cancel_offer_dismiss_returns_to_editor() {
    let mut app = app_mid_inline_edit("fix the bug properly");
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = crate::app::agent::AgentState::TurnRunning;
    dispatch(Action::InlineEditSubmit, &mut app);

    dispatch(Action::RewindDismiss, &mut app);

    let agent = &app.agents[&id];
    assert!(agent.rewind_state.is_none());
    assert_eq!(
        agent
            .inline_edit
            .as_ref()
            .expect("editor open")
            .textarea
            .text(),
        "fix the bug properly"
    );
}

/// A failed execute drops the stashed resubmit but leaves the editor open
/// behind the error overlay — dismissing the error returns to editing.
#[test]
fn inline_edit_execute_failure_keeps_editor_open() {
    let mut app = app_mid_inline_edit("fix the bug properly");
    let id = AgentId(0);
    drive_inline_submit_to_execute(&mut app);
    assert!(app.agents[&id].pending_inline_resubmit.is_some());

    let effects = dispatch(
        Action::TaskComplete(TaskResult::RewindExecuteFailed {
            agent_id: id,
            error: "boom".into(),
        }),
        &mut app,
    );

    assert!(effects.is_empty());
    let agent = &app.agents[&id];
    assert!(
        agent.pending_inline_resubmit.is_none(),
        "stash dies with its rewind"
    );
    match &agent.rewind_state.as_ref().unwrap().phase {
        crate::views::rewind::RewindPhase::Error { message } => {
            assert_eq!(message, "boom");
        }
        other => panic!("expected Error, got {other:?}"),
    }
    assert_eq!(
        agent
            .inline_edit
            .as_ref()
            .expect("editor open")
            .textarea
            .text(),
        "fix the bug properly"
    );
    assert_eq!(agent.scrollback.len(), 2, "transcript untouched");
}

#[test]
fn stacked_rewinds_each_get_their_own_pid_and_orphans_drop_independently() {
    // Two rewinds → two cancelled PRs to drain. Each carries its own
    // promptId; both fail to match current_prompt_id (None) and are
    // silently discarded with no banner.
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    dispatch(Action::SendPrompt("a".into()), &mut app);
    let pid_a = app.agents[&id].session.current_prompt_id.clone();
    dispatch(Action::CancelTurn, &mut app);
    dispatch(Action::SendPrompt("b".into()), &mut app);
    let pid_b = app.agents[&id].session.current_prompt_id.clone();
    dispatch(Action::CancelTurn, &mut app);
    assert_ne!(pid_a, pid_b);
    assert!(app.agents[&id].session.current_prompt_id.is_none());

    let pr = |pid: &Option<String>| {
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled)
                .meta(serde_json::json!({ "promptId": pid }).as_object().cloned())),
            http_status: None,
            prompt_id: None,
        })
    };
    dispatch(pr(&pid_a), &mut app);
    dispatch(pr(&pid_b), &mut app);
    assert_eq!(app.agents[&id].scrollback.len(), 0);
    assert!(app.agents[&id].session.state.is_idle());
}

fn user_block(text: &str, pi: Option<usize>) -> RenderBlock {
    let mut b = UserPromptBlock::new(text);
    b.prompt_index = pi;
    RenderBlock::UserPrompt(b)
}

#[test]
fn primary_path_returns_correct_idx_for_each_prompt() {
    let mut sb = ScrollbackState::new();
    let alpha = sb.push_block(user_block("alpha", Some(0)));
    sb.push_block(RenderBlock::agent_message("a"));
    let bravo = sb.push_block(user_block("bravo", Some(1)));
    sb.push_block(RenderBlock::agent_message("b"));
    let charlie = sb.push_block(user_block("charlie", Some(2)));
    sb.push_block(RenderBlock::agent_message("c"));

    let alpha_idx = sb.index_of_id(alpha).unwrap();
    let bravo_idx = sb.index_of_id(bravo).unwrap();
    let charlie_idx = sb.index_of_id(charlie).unwrap();

    assert_eq!(
        find_user_prompt_entry_for_shell_index(&sb, 0),
        Some(alpha_idx)
    );
    assert_eq!(
        find_user_prompt_entry_for_shell_index(&sb, 1),
        Some(bravo_idx)
    );
    assert_eq!(
        find_user_prompt_entry_for_shell_index(&sb, 2),
        Some(charlie_idx)
    );
}

#[test]
fn fallback_path_returns_correct_idx_when_prompt_index_is_none() {
    let mut sb = ScrollbackState::new();
    let alpha = sb.push_block(user_block("alpha", None));
    sb.push_block(RenderBlock::agent_message("a"));
    let bravo = sb.push_block(user_block("bravo", None));
    sb.push_block(RenderBlock::agent_message("b"));
    let charlie = sb.push_block(user_block("charlie", None));
    sb.push_block(RenderBlock::agent_message("c"));

    let alpha_idx = sb.index_of_id(alpha).unwrap();
    let bravo_idx = sb.index_of_id(bravo).unwrap();
    let charlie_idx = sb.index_of_id(charlie).unwrap();

    assert_eq!(
        find_user_prompt_entry_for_shell_index(&sb, 0),
        Some(alpha_idx)
    );
    assert_eq!(
        find_user_prompt_entry_for_shell_index(&sb, 1),
        Some(bravo_idx)
    );
    assert_eq!(
        find_user_prompt_entry_for_shell_index(&sb, 2),
        Some(charlie_idx)
    );
}
