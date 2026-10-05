//! Tests for prompt and bash submission and queueing.

use super::*;

/// Sending a prompt is a submit: it retires the active ephemeral tip.
#[test]
fn send_prompt_clears_active_ephemeral_tip() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let agent = app.agents.get_mut(&id).unwrap();
    let _ = agent.ephemeral_tip.show(
        crate::tips::EphemeralTip::new("t", ratatui::text::Line::from("hint")),
        &mut std::collections::HashMap::new(),
    );
    assert!(agent.ephemeral_tip.is_active());

    let _ = dispatch(Action::SendPrompt("hello".into()), &mut app);
    assert!(
        !app.agents.get(&id).unwrap().ephemeral_tip.is_active(),
        "prompt submit must clear the tip"
    );
}

/// Sending a bash command is a submit: it retires the active ephemeral tip.
#[test]
fn send_bash_command_clears_active_ephemeral_tip() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let agent = app.agents.get_mut(&id).unwrap();
    let _ = agent.ephemeral_tip.show(
        crate::tips::EphemeralTip::new("t", ratatui::text::Line::from("hint")),
        &mut std::collections::HashMap::new(),
    );
    assert!(agent.ephemeral_tip.is_active());

    let _ = dispatch(Action::SendBashCommand("ls".into()), &mut app);
    assert!(
        !app.agents.get(&id).unwrap().ephemeral_tip.is_active(),
        "bash submit must clear the tip"
    );
}

/// `ShowUndoTip` on a never-drawn agent is refused by the renderability
/// gate: no tip shown, no count burned, no effects. (Tip on so the
/// renderability gate — not the per-tip gate — is what refuses.)
#[test]
fn show_undo_tip_refused_on_undrawn_agent() {
    let mut app = test_app_with_agent();
    app.contextual_hints.undo = true;
    let id = AgentId(0);

    let effects = dispatch(Action::ShowUndoTip, &mut app);
    assert!(effects.is_empty());
    assert!(app.tip_seen_counts.is_empty(), "no count burned");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

/// `ShowUndoTip` on a drawable agent shows the tip and increments the
/// per-session seen count in memory — emitting no effects (nothing is
/// persisted to disk).
#[test]
fn show_undo_tip_shows_and_counts_in_memory() {
    use crate::tips::clear_detector::UNDO_TIP_SEEN_KEY;
    let mut app = test_app_with_agent();
    app.contextual_hints.undo = true;
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (80, 30);

    let effects = dispatch(Action::ShowUndoTip, &mut app);
    assert!(app.agents[&id].ephemeral_tip.is_active());
    assert_eq!(app.tip_seen_counts.get(UNDO_TIP_SEEN_KEY), Some(&1));
    assert!(
        effects.is_empty(),
        "seen count is in-memory; nothing persisted"
    );
}

/// `ShowUndoTip` is a no-op when its per-tip gate is off: no tip shown, no
/// count burned — even on a drawable agent.
#[test]
fn show_undo_tip_no_op_when_flag_off() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (80, 30);
    app.contextual_hints.undo = false;

    let effects = dispatch(Action::ShowUndoTip, &mut app);
    assert!(effects.is_empty());
    assert!(app.tip_seen_counts.is_empty(), "no count burned");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

// ── Small-screen tip (`show_small_screen_tip` + its one-shot trigger) ──

/// `show_small_screen_tip` on a drawable agent shows the tip and increments
/// the per-session seen count in memory (nothing persisted — the fn returns
/// nothing, so it cannot raise effects).
#[test]
fn show_small_screen_tip_shows_and_counts_in_memory() {
    use crate::tips::small_screen::SMALL_SCREEN_TIP_SEEN_KEY;
    let mut app = test_app_with_agent();
    app.contextual_hints.small_screen = true;
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 24);

    crate::app::dispatch::show_small_screen_tip(&mut app);
    assert!(app.agents[&id].ephemeral_tip.is_active());
    assert_eq!(app.tip_seen_counts.get(SMALL_SCREEN_TIP_SEEN_KEY), Some(&1));
}

/// `show_small_screen_tip` is a no-op when its per-tip gate is off: no tip
/// shown, no count burned — even on a drawable agent.
#[test]
fn show_small_screen_tip_no_op_when_flag_off() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 24);
    app.contextual_hints.small_screen = false;

    crate::app::dispatch::show_small_screen_tip(&mut app);
    assert!(app.tip_seen_counts.is_empty(), "no count burned");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

/// The trigger defers — WITHOUT consuming the one-shot — until the active
/// view is an agent with a stable, draw-measured size; the first stable
/// in-band measure then shows the tip exactly once.
#[test]
fn small_screen_trigger_waits_for_stable_agent_measure_then_fires_once() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Welcome view: no evaluation, one-shot not consumed.
    app.active_view = ActiveView::Welcome;
    app.maybe_trigger_small_screen_tip();
    assert!(!app.small_screen_tip_evaluated);

    // Agent view, but never drawn (size (0,0)): still deferred.
    app.active_view = ActiveView::Agent(id);
    app.maybe_trigger_small_screen_tip();
    assert!(!app.small_screen_tip_evaluated);

    // Pending post-resize re-measure: still deferred.
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.last_terminal_size = (100, 24);
        agent.terminal_size_stale = true;
    }
    app.maybe_trigger_small_screen_tip();
    assert!(!app.small_screen_tip_evaluated);

    // Stable in-band measure: evaluates once and shows.
    app.agents.get_mut(&id).unwrap().terminal_size_stale = false;
    app.maybe_trigger_small_screen_tip();
    assert!(app.small_screen_tip_evaluated);
    assert!(app.agents[&id].ephemeral_tip.is_active());

    // One-shot: a later call (e.g. after a resize back into the band) is inert.
    app.agents.get_mut(&id).unwrap().ephemeral_tip.clear_all();
    app.maybe_trigger_small_screen_tip();
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

/// An in-band first measure whose banner row is occluded (session banner,
/// permission ask, modal, open dropdown) defers WITHOUT consuming — the show
/// gate would refuse it, and spending the one-shot invisibly would kill the
/// hint for the run. Once the occluder clears, the next draw shows it.
#[test]
fn small_screen_trigger_defers_while_banner_row_occluded() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.last_terminal_size = (100, 24);
        agent.session_banner_active = true;
    }

    app.maybe_trigger_small_screen_tip();
    assert!(!app.small_screen_tip_evaluated, "occlusion must defer");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
    assert!(app.tip_seen_counts.is_empty(), "no count burned");

    // Occluder gone: the next draw evaluates and shows.
    app.agents.get_mut(&id).unwrap().session_banner_active = false;
    app.maybe_trigger_small_screen_tip();
    assert!(app.small_screen_tip_evaluated);
    assert!(app.agents[&id].ephemeral_tip.is_active());
}

/// An out-of-band first measure consumes the one-shot without showing, so a
/// later resize INTO the band can never bring the tip back.
#[test]
fn small_screen_trigger_out_of_band_consumes_without_showing() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 40);

    app.maybe_trigger_small_screen_tip();
    assert!(app.small_screen_tip_evaluated, "evaluation is consumed");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
    assert!(app.tip_seen_counts.is_empty(), "no count burned");

    // Later in-band measure: still nothing (one-shot already spent).
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 24);
    app.maybe_trigger_small_screen_tip();
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

/// The small-screen tip is ambient: submitting the promote prompt right
/// after it shows must NOT retire it (a real turn takes seconds, so the
/// submit-clear reduced the tip to a sub-second blink), while the
/// edit-contextual tips keep their retire-on-submit behavior
/// (`send_prompt_clears_active_ephemeral_tip` above pins that side).
#[test]
fn send_prompt_keeps_ambient_small_screen_tip() {
    use crate::tips::small_screen::SMALL_SCREEN_TIP_SEEN_KEY;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 24);

    app.maybe_trigger_small_screen_tip();
    assert!(app.agents[&id].ephemeral_tip.is_active());

    let _ = dispatch(Action::SendPrompt("hello".into()), &mut app);
    assert!(
        app.agents[&id].ephemeral_tip.is_active(),
        "ambient tip must survive the prompt submit"
    );
    assert_eq!(
        app.tip_seen_counts.get(SMALL_SCREEN_TIP_SEEN_KEY),
        Some(&1),
        "surviving the submit is the same show — no second count"
    );
}

/// Whole-lifecycle once-only pin: show at promote, survive the submit, pause
/// under occlusion, resume, expire on the visible-time budget — exactly one
/// seen-count for the run and the spent one-shot never re-triggers.
#[test]
fn small_screen_tip_lifecycle_shows_once_across_submit_and_occlusion() {
    use crate::tips::small_screen::SMALL_SCREEN_TIP_SEEN_KEY;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 24);

    app.maybe_trigger_small_screen_tip();
    let _ = dispatch(Action::SendPrompt("hello".into()), &mut app);

    let agent = app.agents.get_mut(&id).unwrap();
    assert!(agent.ephemeral_tip.is_active());
    // Occlusion window mid-turn: frozen, then resumes.
    agent.note_terminal_resize();
    for _ in 0..200 {
        let _ = agent.tick_ephemeral_tip();
    }
    assert!(
        agent.ephemeral_tip.is_active(),
        "must not expire off-screen"
    );
    agent.note_terminal_size((100, 24));
    // Lives out the remaining visible-time budget, then expires.
    for _ in 0..300 {
        let _ = agent.tick_ephemeral_tip();
    }
    assert!(
        !agent.ephemeral_tip.is_active(),
        "expires after visible TTL"
    );

    // Expiry does not resurrect anything: one-shot spent, count capped at 1.
    app.maybe_trigger_small_screen_tip();
    assert!(!app.agents[&id].ephemeral_tip.is_active());
    assert_eq!(app.tip_seen_counts.get(SMALL_SCREEN_TIP_SEEN_KEY), Some(&1));
    assert!(app.small_screen_tip_evaluated);
}

/// The user's compact setting being ON suppresses the tip (the hint would
/// advertise a mode they already use) — the one-shot is still consumed.
#[test]
fn small_screen_trigger_suppressed_when_user_compact_on() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 24);
    app.current_ui.compact_mode = true;

    app.maybe_trigger_small_screen_tip();
    assert!(app.small_screen_tip_evaluated);
    assert!(!app.agents[&id].ephemeral_tip.is_active());
    assert!(app.tip_seen_counts.is_empty(), "no count burned");
}

// ── SSH wrap tip (`show_ssh_wrap_tip` + its one-shot trigger) ──

/// `show_ssh_wrap_tip` on a drawable agent shows the tip and increments the
/// per-session seen count in memory (nothing persisted — the fn returns
/// nothing, so it cannot raise effects).
#[test]
fn show_ssh_wrap_tip_shows_and_counts_in_memory() {
    use crate::tips::ssh_wrap::{SSH_WRAP_TIP_KEY, SSH_WRAP_TIP_SEEN_KEY};
    let mut app = test_app_with_agent();
    app.contextual_hints.ssh_wrap = true;
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 40);

    crate::app::dispatch::show_ssh_wrap_tip(&mut app);
    assert_eq!(
        app.agents[&id].ephemeral_tip.current_key(),
        Some(SSH_WRAP_TIP_KEY)
    );
    assert_eq!(app.tip_seen_counts.get(SSH_WRAP_TIP_SEEN_KEY), Some(&1));
}

/// `show_ssh_wrap_tip` is a no-op when `contextual_hints.ssh_wrap` is off:
/// no tip shown, no count burned — even on a drawable agent.
#[test]
fn show_ssh_wrap_tip_no_op_when_flag_off() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 40);
    app.contextual_hints.ssh_wrap = false;

    crate::app::dispatch::show_ssh_wrap_tip(&mut app);
    assert!(app.tip_seen_counts.is_empty(), "no count burned");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

/// The seen cap holds at one show per session even if the show fn re-runs
/// after the first tip expired or was cleared.
#[test]
fn show_ssh_wrap_tip_respects_once_per_session_cap() {
    use crate::tips::ssh_wrap::SSH_WRAP_TIP_SEEN_KEY;
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 40);

    crate::app::dispatch::show_ssh_wrap_tip(&mut app);
    app.agents.get_mut(&id).unwrap().ephemeral_tip.clear_all();
    crate::app::dispatch::show_ssh_wrap_tip(&mut app);
    assert!(
        !app.agents[&id].ephemeral_tip.is_active(),
        "second show must be seen-gated"
    );
    assert_eq!(app.tip_seen_counts.get(SSH_WRAP_TIP_SEEN_KEY), Some(&1));
}

/// The trigger defers — WITHOUT consuming the one-shot — until the active
/// view is an agent with a stable, draw-measured size; the first stable
/// measure with the environment recommending wrap then shows it exactly once.
#[test]
fn ssh_wrap_trigger_waits_for_stable_agent_measure_then_fires_once() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Welcome view: no evaluation, one-shot not consumed.
    app.active_view = ActiveView::Welcome;
    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(!app.ssh_wrap_tip_evaluated);

    // Agent view, but never drawn (size (0,0)): still deferred.
    app.active_view = ActiveView::Agent(id);
    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(!app.ssh_wrap_tip_evaluated);

    // Pending post-resize re-measure: still deferred.
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.last_terminal_size = (100, 40);
        agent.terminal_size_stale = true;
    }
    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(!app.ssh_wrap_tip_evaluated);

    // Stable measure + recommending environment: evaluates once and shows.
    app.agents.get_mut(&id).unwrap().terminal_size_stale = false;
    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(app.ssh_wrap_tip_evaluated);
    assert_eq!(
        app.agents[&id].ephemeral_tip.current_key(),
        Some(crate::tips::ssh_wrap::SSH_WRAP_TIP_KEY)
    );

    // One-shot: later calls are inert.
    app.agents.get_mut(&id).unwrap().ephemeral_tip.clear_all();
    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

/// A not-recommending environment (local session, wrap sink already active,
/// or a VS Code remote) consumes the one-shot without showing — the shape is
/// process-constant, so there is nothing to re-evaluate later.
#[test]
fn ssh_wrap_trigger_env_not_recommending_consumes_without_showing() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 40);

    app.maybe_trigger_ssh_wrap_tip_inner(false);
    assert!(app.ssh_wrap_tip_evaluated, "evaluation is consumed");
    assert!(!app.agents[&id].ephemeral_tip.is_active());
    assert!(app.tip_seen_counts.is_empty(), "no count burned");

    // The one-shot is spent: even a recommending call stays inert.
    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(!app.agents[&id].ephemeral_tip.is_active());
}

/// A busy tip slot defers WITHOUT consuming — replacing would burn the other
/// session-load tip's once-per-session show; once the slot frees, the next
/// draw shows the wrap tip.
#[test]
fn ssh_wrap_trigger_defers_while_tip_slot_busy() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    // In the small-screen band so the other session-load tip takes the slot
    // first (mirrors the real draw order: the small-screen trigger runs
    // first).
    app.agents.get_mut(&id).unwrap().last_terminal_size = (100, 24);
    app.maybe_trigger_small_screen_tip();
    assert!(app.agents[&id].ephemeral_tip.is_active());

    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(!app.ssh_wrap_tip_evaluated, "busy slot must defer");
    assert_eq!(
        app.agents[&id].ephemeral_tip.current_key(),
        Some(crate::tips::small_screen::SMALL_SCREEN_TIP_KEY),
        "the earlier tip keeps the slot"
    );

    // Slot free (the first tip expired or cleared): the next draw shows it.
    app.agents.get_mut(&id).unwrap().ephemeral_tip.clear_all();
    app.maybe_trigger_ssh_wrap_tip_inner(true);
    assert!(app.ssh_wrap_tip_evaluated);
    assert_eq!(
        app.agents[&id].ephemeral_tip.current_key(),
        Some(crate::tips::ssh_wrap::SSH_WRAP_TIP_KEY)
    );
}

#[test]
fn focus_prompt_switches_pane() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let effects = dispatch(Action::FocusPrompt, &mut app);
    assert!(effects.is_empty());
    assert_eq!(app.agents[&id].active_pane, ActivePane::Prompt);
}

#[test]
fn send_prompt_produces_effect_and_clears_input() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .textarea
        .insert_str("hello");

    let effects = dispatch(Action::SendPrompt("hello".into()), &mut app);

    // Prompt is enqueued and immediately drained (agent was idle).
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "hello"));
    assert!(app.agents[&id].prompt.text().is_empty());
    assert!(app.agents[&id].session.state.is_turn_running());
    assert_eq!(app.agents[&id].scrollback.len(), 1);
    // Queue should be empty (drained).
    assert_eq!(app.agents[&id].session.queue_len(), 0);
}

/// Register `pr-workflow` as an ACP-advertised skill on the agent's slash
/// registry, mirroring the shell's available-commands sync. Shared with the
/// modes tests (`/plan <desc>` range forwarding).
pub(super) fn register_pr_workflow_skill(app: &mut AppView, id: AgentId) {
    let agent = app.agents.get_mut(&id).unwrap();
    let models = agent.session.models.clone();
    agent.prompt.sync_acp_commands(
        &[
            acp::AvailableCommand::new("pr-workflow", "PR workflow skill").meta(
                serde_json::json!({
                    "path": "/tmp/skills/pr-workflow/SKILL.md",
                    "scope": "local",
                })
                .as_object()
                .cloned(),
            ),
        ],
        None,
        &models,
    );
}

/// A plain prompt with a mid-text recognized `/skill` token drains
/// into a token-styled user block and carries the ranges on `SendPrompt`
/// (stamped into wire meta for replay).
#[ignore = "pi-python: grok-specific feature not supported"]
#[test]
fn send_prompt_mid_text_skill_token_carries_ranges() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    register_pr_workflow_skill(&mut app, id);

    let effects = dispatch(
        Action::SendPrompt("great /pr-workflow all good now".into()),
        &mut app,
    );

    assert_eq!(effects.len(), 1);
    match &effects[0] {
        Effect::SendPrompt {
            text,
            skill_token_ranges,
            ..
        } => {
            assert_eq!(text, "great /pr-workflow all good now");
            assert_eq!(skill_token_ranges, &vec![6..18]);
        }
        other => panic!("expected SendPrompt, got {other:?}"),
    }
    // The drained echo block styles exactly the composer-recognized token.
    match &app.agents[&id].scrollback.get(0).unwrap().block {
        RenderBlock::UserPrompt(b) => {
            assert_eq!(b.skill_token_ranges, vec![6..18]);
        }
        other => panic!("expected UserPrompt, got {other:?}"),
    }
}

/// An unrecognized `/word` mid-text stays a plain prompt: no ranges on the
/// block or the effect.
#[test]
fn send_prompt_unknown_token_has_empty_ranges() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let effects = dispatch(Action::SendPrompt("great /frobnicate now".into()), &mut app);

    assert_eq!(effects.len(), 1);
    match &effects[0] {
        Effect::SendPrompt {
            skill_token_ranges, ..
        } => assert!(skill_token_ranges.is_empty()),
        other => panic!("expected SendPrompt, got {other:?}"),
    }
    match &app.agents[&id].scrollback.get(0).unwrap().block {
        RenderBlock::UserPrompt(b) => assert!(b.skill_token_ranges.is_empty()),
        other => panic!("expected UserPrompt, got {other:?}"),
    }
}

/// An image-bearing prompt with token ranges: the LOCAL echo styles the
/// token, but the wire blocks stay unstamped — the image builder rewrites
/// the text (placeholder stripping), which would shift byte offsets, so
/// replay renders these plain (known limitation).
#[test]
fn image_prompt_with_ranges_styles_echo_but_wire_meta_absent() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        // Real constructor path, with the image attached to the queued row.
        let agent = app.agents.get_mut(&id).unwrap();
        agent
            .session
            .enqueue_prompt_with_skill_tokens("great /pr-workflow go".into(), vec![6..18]);
        agent.session.pending_prompts.back_mut().unwrap().images =
            vec![crate::app::agent_view::test_fixtures::test_pasted_image()];
    }

    let effects = dispatch(Action::DrainQueue, &mut app);

    match &app.agents[&id].scrollback.get(0).unwrap().block {
        RenderBlock::UserPrompt(b) => assert_eq!(b.skill_token_ranges, vec![6..18]),
        other => panic!("expected UserPrompt, got {other:?}"),
    }
    match &effects[0] {
        Effect::SendPromptBlocks { blocks, .. } => {
            let acp::ContentBlock::Text(tb) = &blocks[0] else {
                panic!("first block must be text");
            };
            assert!(
                tb.meta
                    .as_ref()
                    .and_then(|m| m.get("skillTokenRanges"))
                    .is_none(),
                "images path is known-plain on replay: no ranges meta"
            );
        }
        other => panic!("expected SendPromptBlocks, got {other:?}"),
    }
}

/// A leading skill invocation still takes the InjectSkill path: the drained
/// block is a skill prompt (`display_as_skill`), not the mid-text styling.
#[ignore = "pi-python: grok-specific feature not supported"]
#[test]
fn send_prompt_leading_skill_keeps_inject_skill_path() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    register_pr_workflow_skill(&mut app, id);

    let effects = dispatch(Action::SendPrompt("/pr-workflow ship it".into()), &mut app);

    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::SendPromptBlocks { .. })),
        "leading skill must send structured blocks, got {effects:?}"
    );
    match &app.agents[&id].scrollback.get(0).unwrap().block {
        RenderBlock::UserPrompt(b) => {
            assert_eq!(
                b.skill_token_ranges,
                vec![0..12],
                "InjectSkill path styles the leading /pr-workflow token"
            );
            assert_eq!(b.text, "/pr-workflow ship it");
        }
        other => panic!("expected UserPrompt, got {other:?}"),
    }
}

#[test]
fn follow_up_chip_preserves_prompt_draft() {
    // A chip click submits the suggestion but must not wipe a typed draft.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .textarea
        .insert_str("my draft");
    dispatch(Action::SubmitFollowUp("Summarize".into()), &mut app);
    assert_eq!(app.agents[&id].prompt.text(), "my draft");
}

#[test]
fn send_prompt_clears_follow_up_chips() {
    // Production turn-start path: the local queue drain clears the
    // previous response's chips.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents
        .get_mut(&id)
        .unwrap()
        .apply_follow_ups("resp-1".into(), vec!["a".into()]);
    dispatch(Action::SendPrompt("hello".into()), &mut app);
    assert!(
        app.agents[&id].follow_ups.is_none(),
        "starting a turn must clear chips"
    );
}

#[test]
fn chip_submit_while_enqueued_clears_follow_up_chips() {
    // A chip click submitted while a turn is RUNNING *and* the local queue
    // is non-empty takes the ENQUEUE path, not immediate-server-send:
    // `immediate_server_send_eligible` is false whenever `pending_prompts`
    // is non-empty. Before the fix, only the immediate-send branch cleared
    // the chips, so this path left them on screen after the user had already
    // acted on one. The clear now runs for every `SubmitFollowUp` path.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.apply_follow_ups("resp-1".into(), vec!["Summarize".into()]);
        assert!(agent.follow_ups.is_some(), "precondition: chips shown");
        // Running turn + a non-empty local queue → NOT immediate-send
        // eligible, so the submit is held in the local queue instead.
        agent.session.state = AgentState::TurnRunning;
        agent.session.enqueue_prompt("earlier".into());
        assert!(
            !agent.session.pending_prompts.is_empty(),
            "precondition: non-empty local queue forces the enqueue path"
        );
    }

    let effects = dispatch(Action::SubmitFollowUp("Summarize".into()), &mut app);

    // Not immediate-sent: the chip text is enqueued locally (it does not
    // produce a `SendPrompt` effect for the chip while a turn is running).
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::SendPrompt { text, .. } if text == "Summarize")),
        "chip must be enqueued, not immediate-sent, got {effects:?}"
    );
    // The chips are cleared on the enqueue path too (the bug fix).
    assert!(
        app.agents[&id].follow_ups.is_none(),
        "enqueue chip path must clear chips"
    );
}

#[test]
fn send_prompt_while_running_stays_local() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;

    let effects = dispatch(Action::SendPrompt("later".into()), &mut app);
    assert!(
        effects.is_empty(),
        "a prompt typed mid-turn must only queue, got {effects:?}"
    );
    // Local drip-feed path: the prompt waits for the running turn to end.
    assert_eq!(app.agents[&id].session.queue_len(), 1);
}

/// A prompt with attachments typed mid-turn joins the local queue like any other.
#[test]
fn send_prompt_with_images_while_running_stays_local() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent
            .prompt
            .insert_image(crate::prompt_images::PastedImage {
                element_id: pi_ratatui_textarea::ElementId::from_raw(0),
                display_number: 0,
                mime_type: "image/png".to_owned(),
                dimensions: Some((8, 8)),
                byte_len: 1,
                encoded_bytes: Some(vec![0].into()),
                source_path: None,
                staged_temp_path: None,
                session_image_path: None,
                preview: crate::prompt_images::PromptImagePreview::default(),
            })
            .unwrap();
    }

    let effects = dispatch(Action::SendPrompt("with image".into()), &mut app);
    assert!(
        effects.is_empty(),
        "an image prompt typed mid-turn must only queue, got {effects:?}"
    );
    assert_eq!(app.agents[&id].session.queue_len(), 1);
}

/// A prompt typed while a turn is running joins the local drip-feed queue
/// BEHIND an older prompt already waiting there — e.g. prompts queued during
/// "Starting session…" before the turn began, where the first drains to start
/// the turn and the rest are stranded locally — so FIFO order is preserved.
#[test]
fn send_while_running_with_pending_local_prompt_preserves_fifo() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        // A turn is running (prompt "1" already drained from the startup
        // queue) and an older prompt ("2") is still stranded in the local
        // drip-feed queue because it was enqueued before the turn began.
        agent.session.state = AgentState::TurnRunning;
        agent.session.enqueue_prompt("two".into());
        assert_eq!(agent.session.pending_prompts.len(), 1);
    }

    // Send "3" while the queue still holds "2": it must route LOCALLY.
    let effects = dispatch(Action::SendPrompt("three".into()), &mut app);

    // No immediate server-authoritative send (no SendPrompt effect, and no
    // drain since the turn is running).
    assert!(
        effects.is_empty(),
        "must not immediate-send while a local prompt is pending, got {effects:?}"
    );
    // "3" joined the LOCAL queue behind "2" (FIFO preserved).
    let agent = &app.agents[&id];
    let order: Vec<&str> = agent
        .session
        .pending_prompts
        .iter()
        .map(|p| p.text.as_str())
        .collect();
    assert_eq!(
        order,
        vec!["two", "three"],
        "new prompt must queue behind the older local prompt"
    );
}

#[test]
fn turn_end_drains_next_queued_prompt() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Submit first prompt (drains immediately → Running).
    let effects = dispatch(Action::SendPrompt("first".into()), &mut app);
    assert_eq!(effects.len(), 1);
    assert!(app.agents[&id].session.state.is_turn_running());

    // Submit second prompt while running → joins the local queue.
    let effects = dispatch(Action::SendPrompt("second".into()), &mut app);
    assert!(effects.is_empty(), "got {effects:?}");
    assert_eq!(app.agents[&id].session.queue_len(), 1);

    // Turn ends → the queue drains "second" (+ the billing refresh).
    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    assert_eq!(effects.len(), 2);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "second"));
    assert!(matches!(
        &effects[1],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_turn_running());
    assert_eq!(app.agents[&id].session.queue_len(), 0);
    // Scrollback: user "first" + "Worked for" + user "second".
    assert_eq!(app.agents[&id].scrollback.len(), 3);
}

#[test]
fn turn_end_with_empty_queue_stays_idle() {
    // Pin prompt suggestions OFF so the effect list below is deterministic
    // regardless of the dev machine's `[ui].prompt_suggestions` config
    // (thread-local cache; see `turn_end_fetches_prompt_suggestion_when_enabled`).
    crate::appearance::cache::set_prompt_suggestions(false);
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    app.agents.get_mut(&id).unwrap().turn_started_at = Some(std::time::Instant::now());

    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );

    // Silent billing refresh after turn completion.
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_idle());
    // Session event "Worked for" added.
    assert_eq!(app.agents[&id].scrollback.len(), 1);
}

#[test]
fn multiple_queued_prompts_drain_one_per_turn() {
    // Deterministic effect lists — see `turn_end_with_empty_queue_stays_idle`.
    crate::appearance::cache::set_prompt_suggestions(false);
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Send first (immediate drain).
    dispatch(Action::SendPrompt("a".into()), &mut app);
    // Queue two more while running (local queue path).
    enqueue_local(&mut app, id, "b");
    enqueue_local(&mut app, id, "c");
    assert_eq!(app.agents[&id].session.queue_len(), 2);

    let end_turn = || {
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: AgentId(0),
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        })
    };

    // Turn end → drain "b" + FetchBilling.
    let effects = dispatch(end_turn(), &mut app);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "b"));
    assert!(matches!(
        &effects[1],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert_eq!(app.agents[&id].session.queue_len(), 1);

    // Turn end → drain "c" + FetchBilling.
    let effects = dispatch(end_turn(), &mut app);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "c"));
    assert!(matches!(
        &effects[1],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert_eq!(app.agents[&id].session.queue_len(), 0);

    // Turn end → FetchBilling only.
    let effects = dispatch(end_turn(), &mut app);
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_idle());
}

#[test]
fn prompt_response_resets_turn_state() {
    // Deterministic effect lists — see `turn_end_with_empty_queue_stays_idle`.
    crate::appearance::cache::set_prompt_suggestions(false);
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    app.agents.get_mut(&id).unwrap().turn_started_at = Some(std::time::Instant::now());

    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    // Silent billing refresh after turn completion.
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_idle());
    assert!(app.agents[&id].turn_started_at.is_none());
    // mark_turn_finished must stamp the activity anchor used by the
    // dashboard relative-time label -- if this regresses, rows will show
    // "now" forever instead of advancing.
    assert!(
        app.agents[&id].last_active_at.is_some(),
        "mark_turn_finished must update last_active_at"
    );
    // Session event message should be in scrollback.
    assert_eq!(app.agents[&id].scrollback.len(), 1);
}

/// A context overflow (the `ContextTooLarge` block from the RetryState handler)
/// suppresses both the redundant `TurnFailed` and the error toast. Derived from
/// the scrollback, not a session flag; the control case proves it drives suppression.
#[test]
fn prompt_response_context_overflow_suppresses_turn_failed_and_toast() {
    fn run_failed_turn(context_overflow: bool) -> (bool, bool) {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.session.state = AgentState::TurnRunning;
            agent.turn_started_at = Some(std::time::Instant::now());
            if context_overflow {
                // Mirror the RetryState handler pushing the actionable block.
                agent
                    .scrollback
                    .push_block(RenderBlock::session_event(SessionEvent::ContextTooLarge));
            }
        }
        dispatch(
            Action::TaskComplete(TaskResult::PromptResponse {
                agent_id: id,
                result: Err("API error (status 500): the prompt is too long for this \
                                 model's context window"
                    .to_string()),
                http_status: None,
                prompt_id: None,
            }),
            &mut app,
        );
        let has_turn_failed = (0..app.agents[&id].scrollback.len()).any(|idx| {
            matches!(
                app.agents[&id].scrollback.entry(idx).map(|e| &e.block),
                Some(RenderBlock::SessionEvent(ev))
                    if matches!(ev.event, SessionEvent::TurnFailed { .. })
            )
        });
        (has_turn_failed, app.deferred_notification.is_some())
    }

    // Control: with no ContextTooLarge block, PromptResponse still ends the
    // turn with TurnFailed + a toast. Overflow copy in the error string must
    // not change that — only a prior ContextTooLarge banner (from RetryState
    // `error_type=context_length`) suppresses the marker.
    let (failed_block, toast) = run_failed_turn(false);
    assert!(failed_block, "baseline: a failed turn pushes TurnFailed");
    assert!(toast, "baseline: a failed turn emits an error toast");

    // With the block present, both are suppressed in favour of the actionable prompt.
    let (failed_block, toast) = run_failed_turn(true);
    assert!(
        !failed_block,
        "context overflow must suppress the redundant TurnFailed block"
    );
    assert!(!toast, "context overflow must suppress the error toast");
}

#[test]
fn prompt_response_request_failed_banner_suppresses_turn_failed_and_toast() {
    fn run_failed_turn(banner_shown: bool) -> (bool, bool) {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.session.state = AgentState::TurnRunning;
            agent.turn_started_at = Some(std::time::Instant::now());
            if banner_shown {
                // Mirror the RetryState handler pushing the formatted banner.
                agent.scrollback.push_block(RenderBlock::session_event(
                    SessionEvent::RequestFailed {
                        status: Some(500),
                        headline: "Server error (500)".into(),
                        detail: "Something went wrong on our side.".into(),
                    },
                ));
            }
        }
        dispatch(
            Action::TaskComplete(TaskResult::PromptResponse {
                agent_id: id,
                result: Err("Server error (500): Something went wrong on our side.".to_string()),
                http_status: Some(500),
                prompt_id: None,
            }),
            &mut app,
        );
        let has_turn_failed = (0..app.agents[&id].scrollback.len()).any(|idx| {
            matches!(
                app.agents[&id].scrollback.entry(idx).map(|e| &e.block),
                Some(RenderBlock::SessionEvent(ev))
                    if matches!(ev.event, SessionEvent::TurnFailed { .. })
            )
        });
        (has_turn_failed, app.deferred_notification.is_some())
    }

    let (failed_block, toast) = run_failed_turn(false);
    assert!(failed_block, "baseline: a failed turn pushes TurnFailed");
    assert!(toast, "baseline: a failed turn emits an error toast");

    let (failed_block, toast) = run_failed_turn(true);
    assert!(
        !failed_block,
        "a RequestFailed banner must suppress the redundant TurnFailed"
    );
    assert!(
        !toast,
        "a RequestFailed banner must suppress the error toast"
    );
}

/// The 401/402 race fallbacks must fire on the banner-formatted error text
/// PromptResponse now carries (the RetryState notification may lose the race,
/// so no ReAuthRequired block exists yet).
#[test]
fn prompt_response_formatted_401_suppresses_turn_failed_and_stashes_prompt() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.turn_started_at = Some(std::time::Instant::now());
        agent.session.in_flight_prompt = Some(crate::app::agent::InFlightPrompt {
            text: "resend me".into(),
            images: Vec::new(),
            scrollback_entry: crate::scrollback::entry::EntryId::new(1),
            combined_scrollback_entries: Vec::new(),
            chip_elements: Vec::new(),
        });
    }
    dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Err("Request failed (401): Invalid or expired credentials".to_string()),
            http_status: Some(401),
            prompt_id: None,
        }),
        &mut app,
    );
    let agent = &app.agents[&id];
    let has_turn_failed = (0..agent.scrollback.len()).any(|idx| {
        matches!(
            agent.scrollback.entry(idx).map(|e| &e.block),
            Some(RenderBlock::SessionEvent(ev))
                if matches!(ev.event, SessionEvent::TurnFailed { .. })
        )
    });
    assert!(
        !has_turn_failed,
        "401 must suppress the redundant TurnFailed"
    );
    assert_eq!(
        agent
            .reauth_stashed_prompt
            .as_ref()
            .map(|p| p.text.as_str()),
        Some("resend me"),
        "401 must stash the prompt for auto-resubmit after /login"
    );
}

#[test]
fn prompt_response_formatted_402_takes_credit_limit_path() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnRunning;
        agent.turn_started_at = Some(std::time::Instant::now());
    }
    // http_status field absent (older shell): the status must be recovered
    // from the formatted text.
    dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Err("Request failed (402): Grok Build usage balance exhausted".to_string()),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    let agent = &app.agents[&id];
    let has_turn_failed = (0..agent.scrollback.len()).any(|idx| {
        matches!(
            agent.scrollback.entry(idx).map(|e| &e.block),
            Some(RenderBlock::SessionEvent(ev))
                if matches!(ev.event, SessionEvent::TurnFailed { .. })
        )
    });
    assert!(
        !has_turn_failed,
        "a credit-limit 402 shows the upsell, not TurnFailed"
    );
}

#[test]
fn prompt_response_disk_full_suppresses_turn_failed_and_toast() {
    fn run(pre_seed_disk_full: bool, with_user_echo: bool) -> (bool, bool, usize) {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.session.state = AgentState::TurnRunning;
            agent.turn_started_at = Some(std::time::Instant::now());
            if pre_seed_disk_full {
                agent
                    .scrollback
                    .push_block(RenderBlock::session_event(SessionEvent::DiskFull));
            }
            if with_user_echo {
                agent
                    .scrollback
                    .push_block(RenderBlock::user_prompt("try again"));
            }
        }
        dispatch(
            Action::TaskComplete(TaskResult::PromptResponse {
                agent_id: id,
                result: Err(pi_fast_worktree::ENOSPC_OS_MESSAGE.to_string()),
                http_status: None,
                prompt_id: None,
            }),
            &mut app,
        );
        let disk_fulls = (0..app.agents[&id].scrollback.len())
            .filter(|idx| {
                matches!(
                    app.agents[&id].scrollback.entry(*idx).map(|e| &e.block),
                    Some(RenderBlock::SessionEvent(ev))
                        if matches!(ev.event, SessionEvent::DiskFull)
                )
            })
            .count();
        let failed = (0..app.agents[&id].scrollback.len()).any(|idx| {
            matches!(
                app.agents[&id].scrollback.entry(idx).map(|e| &e.block),
                Some(RenderBlock::SessionEvent(ev))
                    if matches!(ev.event, SessionEvent::TurnFailed { .. })
            )
        });
        (failed, app.deferred_notification.is_some(), disk_fulls)
    }

    let (failed, toast, disk_fulls) = run(true, false);
    assert!(!failed && !toast && disk_fulls == 1);

    let (failed, toast, disk_fulls) = run(false, false);
    assert!(!failed && !toast && disk_fulls == 1);

    let (failed, toast, disk_fulls) = run(true, true);
    assert!(!failed && !toast && disk_fulls == 2);
}

#[test]
fn prompt_response_routes_idle_title_through_frame_pipeline() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
    app.agents.get_mut(&id).unwrap().turn_started_at = Some(std::time::Instant::now());

    // Simulate stale title/progress escapes left over from a previous tick.
    app.pending_notification_escapes = Some("stale-busy-title".into());

    dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );

    // The stale busy-title escapes must be replaced with idle-title
    // escapes so they go through the frame pipeline (writer thread)
    // in the correct order, not contain the old busy title.
    assert!(
        app.pending_notification_escapes
            .as_ref()
            .is_none_or(|s| !s.contains("stale-busy-title")),
        "stale notification escapes must be replaced on turn completion",
    );

    // The notification must be deferred (not fired immediately) so the
    // terminal has at least one render frame to apply the idle title
    // before the notification reads the tab title for its subtitle.
    assert!(
        app.deferred_notification.is_some(),
        "turn-complete notification must be deferred",
    );
    assert_eq!(
        app.deferred_notification.as_ref().unwrap().1,
        3,
        "deferred notification must wait >75 ms (Ghostty title debounce)",
    );
}

#[test]
fn turn_complete_notification_suppressed_when_queue_non_empty() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Start first turn.
    let effects = dispatch(Action::SendPrompt("first".into()), &mut app);
    assert_eq!(effects.len(), 1);
    assert!(app.agents[&id].session.state.is_turn_running());

    // A second prompt typed while the first runs waits in the local queue.
    let effects = dispatch(Action::SendPrompt("second".into()), &mut app);
    assert!(effects.is_empty(), "got {effects:?}");
    assert_eq!(app.agents[&id].session.queue_len(), 1);

    // First turn completes — the queued prompt drains straight into the next
    // turn, so the TurnComplete notification must be suppressed.
    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "second"));
    assert!(app.agents[&id].session.state.is_turn_running());
    assert!(
        app.deferred_notification.is_none(),
        "notification must be suppressed while another turn is about to start",
    );
    assert!(
        app.pending_notification_escapes.is_none(),
        "idle title escapes must also be suppressed while prompt queue is non-empty",
    );

    // Second turn completes — queue is now empty, notification must fire.
    dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    assert!(
        app.deferred_notification.is_some(),
        "notification must fire when prompt queue is empty",
    );
}

#[test]
fn prompt_response_disarms_pending_reconcile() {
    // Healthy path: the RPC response arrives within the grace window —
    // the marker must be disarmed so the reconcile can never double-fire.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.state = AgentState::TurnCancelling;
        agent.session.current_prompt_id = Some("pid-stuck".into());
    }
    arm_reconcile(
        &mut app,
        id,
        "pid-stuck",
        "cancelled",
        std::time::Duration::ZERO,
    );

    let _ = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled).meta(
                serde_json::json!({ "promptId": "pid-stuck" })
                    .as_object()
                    .cloned(),
            )),
            http_status: None,
            prompt_id: Some("pid-stuck".into()),
        }),
        &mut app,
    );

    let agent = &app.agents[&id];
    assert!(
        agent.pending_turn_end_reconcile.is_none(),
        "PromptResponse for the armed prompt must disarm the reconcile"
    );
    assert!(
        agent.session.state.is_idle(),
        "the normal PromptResponse teardown still runs"
    );
}

#[test]
fn prompt_response_resets_cancelling_to_idle() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnCancelling;
    app.agents.get_mut(&id).unwrap().turn_started_at = Some(std::time::Instant::now());

    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    // Silent billing refresh after turn completion.
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_idle());
    // Cancellation produces a "Turn cancelled" session event.
    assert_eq!(app.agents[&id].scrollback.len(), 1);
}

#[test]
fn cancel_with_queued_prompt_drains_on_completion() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Send "first" — drains immediately (agent was idle).
    dispatch(Action::SendPrompt("first".into()), &mut app);
    assert!(app.agents[&id].session.state.is_turn_running());

    // Enqueue follow-up prompt while first is running (local queue path).
    enqueue_local(&mut app, id, "queued");
    assert_eq!(app.agents[&id].session.queue_len(), 1);

    // User cancels the running turn.
    let effects = dispatch(Action::CancelTurn, &mut app);
    assert_eq!(effects.len(), 1);
    assert!(app.agents[&id].session.state.is_cancelling());
    // The queued prompt remains queued until the cancelled turn finishes.
    assert_eq!(app.agents[&id].session.queue_len(), 1);

    // PromptResponse for cancelled first turn arrives.
    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );

    assert_eq!(effects.len(), 2);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "queued"));
    assert!(matches!(
        &effects[1],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_turn_running());
    assert_eq!(app.agents[&id].session.queue_len(), 0);
}

#[test]
fn cancel_with_empty_queue_stays_idle() {
    // When cancelled turn ends and queue is empty, behavior is unchanged.
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnCancelling;
    app.agents.get_mut(&id).unwrap().turn_started_at = Some(std::time::Instant::now());

    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::EndTurn)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );
    // Silent billing refresh after turn completion.
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_idle());
}

#[test]
fn send_prompt_stashes_in_flight_for_restore() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    dispatch(Action::SendPrompt("hello world".into()), &mut app);
    let stash = app.agents[&id]
        .session
        .in_flight_prompt
        .as_ref()
        .expect("in-flight prompt should be stashed");
    assert_eq!(stash.text, "hello world");
    assert!(stash.images.is_empty());
}

#[test]
fn cancel_with_multiple_queued_prompts_drains_only_front_prompt() {
    // Cancel completion should only resume the next queued prompt, not
    // every queued prompt at once.
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    // Simulate: a turn was cancelled with multiple queued follow-ups.
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnCancelling;
    app.agents.get_mut(&id).unwrap().turn_started_at = Some(std::time::Instant::now());
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .enqueue_prompt("queued-1".into());
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .enqueue_prompt("queued-2".into());

    // PromptResponse arrives — should send only the front queued prompt.
    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );

    assert_eq!(effects.len(), 2);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "queued-1"));
    assert!(matches!(
        &effects[1],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_turn_running());
    assert_eq!(app.agents[&id].session.queue_len(), 1);
    assert_eq!(app.agents[&id].session.pending_prompts[0].text, "queued-2");
}

#[test]
fn cancel_drain_is_blocked_when_editing_front_prompt() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnCancelling;
    app.agents.get_mut(&id).unwrap().turn_started_at = Some(std::time::Instant::now());
    let queued_id = app
        .agents
        .get_mut(&id)
        .unwrap()
        .session
        .enqueue_prompt("queued-1".into());
    app.agents
        .get_mut(&id)
        .unwrap()
        .session
        .enqueue_prompt("queued-2".into());
    app.agents.get_mut(&id).unwrap().prompt_mode = PromptMode::EditingQueued {
        id: queued_id,
        original: "queued-1".into(),
        kind: crate::app::agent::QueueEntryKind::Prompt,
    };

    let effects = dispatch(
        Action::TaskComplete(TaskResult::PromptResponse {
            agent_id: id,
            result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled)),
            http_status: None,
            prompt_id: None,
        }),
        &mut app,
    );

    // Drain blocked but billing refresh still happens.
    assert_eq!(effects.len(), 1);
    assert!(matches!(
        &effects[0],
        Effect::FetchBilling { silent: true, .. }
    ));
    assert!(app.agents[&id].session.state.is_idle());
    assert_eq!(app.agents[&id].session.queue_len(), 2);
    assert_eq!(app.agents[&id].session.pending_prompts[0].text, "queued-1");
    assert_eq!(app.agents[&id].session.pending_prompts[1].text, "queued-2");
}

/// An IDLE bash submit is a local enqueue + immediate drain.
#[test]
fn bash_while_idle_stays_on_local_path() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let effects = dispatch(Action::SendBashCommand("ls -la".into()), &mut app);
    // Idle → local enqueue + immediate drain → SendBashCommand from drain.
    assert!(matches!(
        effects.as_slice(),
        [Effect::SendBashCommand { .. }]
    ));
    // Drain started the turn and set the bash-focus flag locally.
    assert!(app.agents[&id].session.state.is_turn_running());
    assert!(app.agents[&id].bash_turn);
}

/// A bash command typed while a turn is RUNNING queues locally behind it: no
/// immediate effect, the composer clears, and the entry waits for the drain.
#[test]
fn bash_while_running_is_queued_locally() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;

    let effects = dispatch(Action::SendBashCommand("ls -la".into()), &mut app);

    assert!(
        effects.is_empty(),
        "nothing may send mid-turn, got {effects:?}"
    );
    let agent = &app.agents[&id];
    assert_eq!(agent.session.queue_len(), 1, "the command must be queued");
    assert!(
        agent.prompt.text().is_empty(),
        "the composer must be cleared"
    );
}

/// A bash command submitted before the session binds is queued, clears the composer, and still lands
/// in up-arrow history with its `! ` prefix. It waits for the drain rather than emitting an effect.
#[test]
fn bash_before_the_session_binds_is_queued_and_recorded() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.agents.get_mut(&id).unwrap().session.session_id = None;

    let effects = dispatch(Action::SendBashCommand("ls -la".into()), &mut app);

    assert!(effects.is_empty(), "nothing may send yet, got {effects:?}");
    let agent = &app.agents[&id];
    assert_eq!(agent.session.queue_len(), 1, "the command must be queued");
    assert!(
        agent.prompt.text().is_empty(),
        "the composer must be cleared"
    );
    assert_eq!(
        agent.session.prompt_history.first().map(String::as_str),
        Some("! ls -la"),
        "up-arrow history must record the command"
    );
}

#[test]
fn switch_model_holds_prompt_until_complete() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    let model_id = acp::ModelId::new(std::sync::Arc::from("grok-4.5"));

    dispatch(
        Action::SwitchModel {
            model_id: model_id.clone(),
            effort: None,
        },
        &mut app,
    );
    assert!(app.agents[&id].session.model_switch_pending);

    let effects = dispatch(Action::SendPrompt("hello".into()), &mut app);
    assert!(
        effects.is_empty(),
        "prompt must be queued while model switch is pending"
    );
    assert_eq!(app.agents[&id].session.queue_len(), 1);

    let effects = dispatch(
        Action::TaskComplete(TaskResult::SwitchModelComplete {
            agent_id: id,
            model_id,
            effort: None,
            result: Ok(()),
            prev_model_id: None,
        }),
        &mut app,
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::SendPrompt { .. }))
    );
    assert_eq!(app.agents[&id].session.queue_len(), 0);
}

#[test]
fn edit_prompt_direct_route_preserves_nonempty_draft_and_elements() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    app.screen_mode = crate::app::ScreenMode::Minimal;
    app.agents
        .get_mut(&id)
        .unwrap()
        .prompt
        .set_text("existing draft");

    let effects = dispatch(Action::EditPromptExternal, &mut app);
    assert!(effects.is_empty());
    assert_eq!(app.agents[&id].prompt.text(), "existing draft");
    assert!(matches!(
        app.pending_editor,
        Some(crate::app::external_editor::PendingEditorRequest::PromptDraft {
            ref original_text,
            ..
        }) if original_text == "existing draft"
    ));
    assert!(app.agents[&id].session.pending_prompts.is_empty());

    app.pending_editor = None;
    let agent = app.agents.get_mut(&id).unwrap();
    agent.prompt.set_text("");
    agent.prompt.textarea.insert_element(
        "@src/lib.rs",
        crate::views::prompt_widget::KIND_FILE_REF,
        None,
    );
    let chip_text = agent.prompt.text().to_owned();
    let _ = dispatch(Action::EditPromptExternal, &mut app);
    assert!(app.pending_editor.is_none());
    assert_eq!(app.agents[&id].prompt.text(), chip_text);
    assert!(!app.agents[&id].prompt.textarea.elements().is_empty());
}

#[test]
fn slash_unknown_command_passthrough_enqueues_prompt() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let effects = dispatch(Action::SendPrompt("/unknown-cmd arg1".into()), &mut app);
    // Unknown slash command → PassThrough → enqueue as prompt.
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "/unknown-cmd arg1"));
    assert!(app.agents[&id].prompt.text().is_empty());
}

#[test]
fn non_slash_prompt_still_works() {
    // Verify normal prompts are unaffected by the slash dispatch rewrite.
    let mut app = test_app_with_agent();
    let id = AgentId(0);

    let effects = dispatch(Action::SendPrompt("hello world".into()), &mut app);
    assert_eq!(effects.len(), 1);
    assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "hello world"));
    assert!(app.agents[&id].prompt.text().is_empty());
}

#[test]
fn entry_title_prefers_generated_summary_over_first_prompt() {
    use crate::scrollback::block::RenderBlock;
    use crate::views::session_title::entry_title;
    let mut app = test_app_with_agent();
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent.generated_session_title = Some("LLM short title".into());
    agent
        .scrollback
        .push_block(RenderBlock::user_prompt("longer first user prompt text"));
    let title = entry_title(&app.agents[&AgentId(0)]);
    assert_eq!(title, "LLM short title");
}

#[test]
fn entry_title_falls_back_to_first_user_prompt() {
    use crate::scrollback::block::RenderBlock;
    use crate::views::session_title::entry_title;
    let mut app = test_app_with_agent();
    let agent = app.agents.get_mut(&AgentId(0)).unwrap();
    agent
        .scrollback
        .push_block(RenderBlock::user_prompt("first message in this agent"));
    let title = entry_title(&app.agents[&AgentId(0)]);
    assert_eq!(title, "first message in this agent");
}

/// Paste-then-immediate-send race (agent prompt): an image Cmd+V'd into the
/// prompt must not be dropped when Enter fires before the deferred probe
/// completes. The send is stashed while the probe is in flight and re-issued
/// on completion, so the sent content carries the image.
#[test]
fn agent_send_before_paste_probe_keeps_image() {
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    {
        let agent = app.agents.get_mut(&id).unwrap();
        agent.set_active_pane(ActivePane::Prompt, true);
        agent.prompt.set_text("look at this");
    }
    // Cmd+V an image → the probe defers (raster snapshot present).
    crate::clipboard::set_clipboard_probe_hook(crate::clipboard::ClipboardProbeHook::with_raster(
        None,
    ));
    {
        let agent = app.agents.get_mut(&id).unwrap();
        let _ = agent
            .handle_prompt_key_for_test(&KeyEvent::new(KeyCode::Char('v'), KeyModifiers::CONTROL));
    }
    let ctx = app.agents[&id]
        .pending_effects
        .iter()
        .find_map(|e| match e {
            Effect::ProbeClipboardAttachment { ctx, .. } => Some(ctx.clone()),
            _ => None,
        })
        .expect("Cmd+V of an image must defer a probe");
    crate::clipboard::clear_clipboard_probe_hook();
    assert_eq!(app.agents[&id].paste_probe_in_flight, 1);

    // Enter before the probe completes → the send is stashed (no enqueue).
    let effects = dispatch(Action::SendPrompt("look at this".into()), &mut app);
    assert!(
        effects.is_empty(),
        "the send must be stashed while the probe is in flight"
    );
    assert!(app.agents[&id].deferred_send.is_some());
    assert!(
        app.agents[&id].session.pending_prompts.is_empty(),
        "nothing enqueued before the image attaches"
    );

    // Probe completes with the image → attach it AND re-issue the stashed
    // send, which now carries the image content block.
    let pasted = crate::prompt_images::from_clipboard_data(&crate::clipboard::ImageData {
        data: vec![1, 2, 3],
        mime_type: "image/png".into(),
    });
    let effects = dispatch(
        Action::TaskComplete(TaskResult::ClipboardAttachmentProbed {
            ctx,
            image: crate::app::actions::ProbedAttachment::Image(pasted),
            file_urls: None,
        }),
        &mut app,
    );
    assert_eq!(app.agents[&id].paste_probe_in_flight, 0);
    assert!(
        app.agents[&id].deferred_send.is_none(),
        "the stashed send was consumed"
    );
    let sent_image = effects.iter().any(|e| {
        matches!(
            e,
            Effect::SendPromptBlocks { blocks, .. }
                if blocks.iter().any(|b| matches!(b, acp::ContentBlock::Image(_)))
        )
    });
    assert!(
        sent_image,
        "the re-issued send must carry the pasted image; effects = {effects:?}"
    );
}

/// Guard: a stashed send must NOT be re-issued to the wrong session when the
/// user switched agents during the probe window. The image still attaches to
/// the original target; the stale send is dropped, not sent to the new agent.
#[test]
fn agent_paste_completion_after_switch_does_not_send_to_other_agent() {
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    let mut app = test_app_with_agent(); // agent A = AgentId(0), active view
    let a = AgentId(0);
    let b = AgentId(1);
    // A second agent B for the user to switch to mid-probe.
    let session_b = make_test_agent_session(&app, b, "session-b");
    app.agents
        .insert(b, AgentView::new(session_b, ScrollbackState::new()));

    // Cmd+V an image into A (active view A) → the probe defers.
    {
        let agent = app.agents.get_mut(&a).unwrap();
        agent.set_active_pane(ActivePane::Prompt, true);
        agent.prompt.set_text("for agent A");
    }
    crate::clipboard::set_clipboard_probe_hook(crate::clipboard::ClipboardProbeHook::with_raster(
        None,
    ));
    {
        let agent = app.agents.get_mut(&a).unwrap();
        let _ = agent
            .handle_prompt_key_for_test(&KeyEvent::new(KeyCode::Char('v'), KeyModifiers::CONTROL));
    }
    let ctx = app.agents[&a]
        .pending_effects
        .iter()
        .find_map(|e| match e {
            Effect::ProbeClipboardAttachment { ctx, .. } => Some(ctx.clone()),
            _ => None,
        })
        .expect("Cmd+V of an image must defer a probe");
    crate::clipboard::clear_clipboard_probe_hook();
    // Model the event loop: `AppView::handle_input` drains the view's
    // pending effects after the key event, before the completion arrives
    // (the completion arm hands back anything still queued).
    app.agents.get_mut(&a).unwrap().pending_effects.clear();

    // Enter (still on A) → the send is stashed.
    let effects = dispatch(Action::SendPrompt("for agent A".into()), &mut app);
    assert!(effects.is_empty());
    assert!(app.agents[&a].deferred_send.is_some());

    // User switches to agent B during the probe window.
    app.active_view = ActiveView::Agent(b);
    let b_queue_before = app.agents[&b].session.pending_prompts.len();

    // A's probe completes.
    let pasted = crate::prompt_images::from_clipboard_data(&crate::clipboard::ImageData {
        data: vec![1, 2, 3],
        mime_type: "image/png".into(),
    });
    let effects = dispatch(
        Action::TaskComplete(TaskResult::ClipboardAttachmentProbed {
            ctx,
            image: crate::app::actions::ProbedAttachment::Image(pasted),
            file_urls: None,
        }),
        &mut app,
    );

    // (1) The image attaches to the ORIGINAL target A.
    assert_eq!(app.agents[&a].prompt.images.len(), 1);
    assert!(app.agents[&a].prompt.text().contains("[Image #1]"));
    let preview_identity = app.agents[&a].prompt.images[0].preview.identity();
    // (2) A's stash is cleared so it can't leak.
    assert!(app.agents[&a].deferred_send.is_none());
    assert_eq!(app.agents[&a].paste_probe_in_flight, 0);
    // (3) No send is re-issued to the now-active B. The only returned effect
    // prepares the image that was attached to A.
    match effects.as_slice() {
        [Effect::PreparePromptImagePreview { preparation }] => assert_eq!(
            preparation.preview().identity(),
            preview_identity,
            "preview preparation must belong to agent A's attached image",
        ),
        other => panic!("expected only agent A preview preparation, got {other:?}"),
    }
    assert_eq!(
        app.agents[&b].session.pending_prompts.len(),
        b_queue_before,
        "agent B's queue must be untouched"
    );
}

/// A prompt submitted before the session binds is held in the agent's queue rather than dropped.
/// `maybe_drain_queue` emits nothing until `SessionCreated` arrives.
#[test]
fn prompt_before_the_session_binds_is_queued() {
    let mut app = test_app();
    dispatch(Action::NewSession, &mut app);
    let id = AgentId(0);
    assert!(
        app.agents[&id].session.session_id.is_none(),
        "precondition: session not bound yet"
    );

    let effects = dispatch_send_prompt_inner(&mut app, "fix the bug".into(), true, false, false);

    assert_eq!(
        app.agents[&id].session.queue_len(),
        1,
        "the prompt must be queued, not dropped"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::SendPrompt { .. })),
        "nothing may go to the wire before the session binds, got {effects:?}"
    );
}

// ── Screen-mode slash gate tests ────────────────────────────────────

/// Returns true if any system block in agent 0's scrollback contains
/// `needle`. Avoids `last_system_text`'s "last block must be System" panic
/// for the allowed-command control (which may leave no system block).
fn scrollback_has_system_text(app: &AppView, id: AgentId, needle: &str) -> bool {
    let sb = &app.agents[&id].scrollback;
    (0..sb.len()).any(
        |i| matches!(&sb.get(i).unwrap().block, RenderBlock::System(s) if s.text.contains(needle)),
    )
}

/// Inline (`--no-alt-screen`) is a full TUI, so fullscreen-only commands run
/// there — the gate keys off "is minimal", not "is `ScreenMode::Fullscreen`".
#[test]
fn non_minimal_mode_allows_fullscreen_pane_slash_command() {
    let mut app = test_app_with_agent();
    app.screen_mode = crate::app::ScreenMode::Inline;
    let _ = dispatch_send_prompt(&mut app, "/find foo".to_string());
    assert!(
        !scrollback_has_system_text(&app, AgentId(0), "isn't available"),
        "the gate must not fire outside minimal mode"
    );
}

#[test]
fn minimal_mode_allows_mode_agnostic_slash_command() {
    let mut app = test_app_with_agent();
    app.screen_mode = crate::app::ScreenMode::Minimal;
    // `/help` is a minimal-native command (opens the command palette).
    let _ = dispatch_send_prompt(&mut app, "/help".to_string());
    assert!(
        !scrollback_has_system_text(&app, AgentId(0), "isn't available"),
        "denylist default must keep mode-agnostic commands available"
    );
}

// ── /queue (ShowQueue) dispatch tests ───────────────────────────────

#[test]
fn show_queue_empty_commits_empty_message() {
    let mut app = test_app_with_agent();
    let before = agent_scrollback_len(&app);
    let effects = dispatch(Action::ShowQueue, &mut app);
    assert!(effects.is_empty(), "got: {effects:?}");
    assert_eq!(agent_scrollback_len(&app), before + 1);
    assert_eq!(last_system_text(&app, AgentId(0)), "Queue is empty.");
}

#[test]
fn show_queue_lists_local_prompts_in_order() {
    let mut app = test_app_with_agent();
    {
        let agent = app.agents.get_mut(&AgentId(0)).unwrap();
        agent.session.enqueue_prompt("first prompt".to_string());
        agent
            .session
            .enqueue_prompt("second\nwith two lines".to_string());
    }
    let effects = dispatch(Action::ShowQueue, &mut app);
    assert!(effects.is_empty(), "got: {effects:?}");
    let text = last_system_text(&app, AgentId(0));
    assert!(text.contains("Queued prompts (2):"), "got: {text:?}");
    assert!(text.contains("#1  first prompt"), "got: {text:?}");
    // Multi-line prompts collapse to the first line + a count suffix.
    assert!(text.contains("#2  second  (+1 more line)"), "got: {text:?}");
}

#[test]
fn show_queue_no_active_agent_is_noop() {
    let mut app = test_app();
    let effects = dispatch(Action::ShowQueue, &mut app);
    assert!(effects.is_empty(), "ShowQueue without an agent is a no-op");
}

// ── Cancel marker (PromptResponse rail) ────────

/// Count of "Turn cancelled by user …" marker blocks in the agent's scrollback.
fn count_cancelled_markers(app: &AppView, id: AgentId) -> usize {
    let agent = &app.agents[&id];
    (0..agent.scrollback.len())
        .filter(|i| {
            matches!(
                agent.scrollback.entry(*i).map(|e| &e.block),
                Some(RenderBlock::SessionEvent(ev))
                    if matches!(ev.event, SessionEvent::TurnCancelled { .. })
            )
        })
        .count()
}


/// A cancelled `PromptResponse` for the running turn.
fn cancelled_prompt_response(id: AgentId) -> Action {
    Action::TaskComplete(TaskResult::PromptResponse {
        agent_id: id,
        result: Ok(acp::PromptResponse::new(acp::StopReason::Cancelled)),
        http_status: None,
        prompt_id: None,
    })
}

/// Control: a plain cancel (no meta, no expectation) still renders its marker.
#[test]
fn plain_cancel_still_pushes_cancelled_marker() {
    let mut app = test_app_with_agent();
    let id = AgentId(0);
    dispatch(Action::SendPrompt("first".into()), &mut app);
    // Turn already produced output → Ctrl+C takes the standard cancel path, not the rewind.
    app.agents.get_mut(&id).unwrap().session.in_flight_prompt = None;
    let _ = dispatch(Action::CancelTurn, &mut app);
    assert!(app.agents[&id].session.state.is_cancelling());

    let _ = dispatch(cancelled_prompt_response(id), &mut app);

    assert!(app.agents[&id].session.state.is_idle());
    assert_eq!(
        count_cancelled_markers(&app, id),
        1,
        "a real user cancel keeps its marker"
    );
}

mod prompt_history_recording_tests {
    use super::test_app_with_agent;
    use crate::app::actions::Action;
    use crate::app::agent::AgentId;
    use crate::app::dispatch::router::dispatch;

    fn history(app: &crate::app::app_view::AppView) -> Vec<String> {
        app.agents[&AgentId(0)].session.prompt_history.clone()
    }

    #[test]
    fn a_typed_slash_command_lands_in_the_history() {
        let mut app = test_app_with_agent();

        dispatch(
            Action::SendPrompt("/rename payment retries".into()),
            &mut app,
        );

        assert_eq!(history(&app), ["/rename payment retries"]);
    }

    #[test]
    fn an_immediate_repeat_collapses_but_an_interleaved_one_does_not() {
        let mut app = test_app_with_agent();

        dispatch(Action::SendPrompt("build it".into()), &mut app);
        dispatch(Action::SendPrompt("build it".into()), &mut app);
        dispatch(Action::SendPrompt("check it".into()), &mut app);
        dispatch(Action::SendPrompt("build it".into()), &mut app);

        assert_eq!(history(&app), ["build it", "check it", "build it"]);
    }

    /// `/notacommand` reaches the command recorder and then the prompt recorder.
    #[test]
    fn an_unknown_command_is_recorded_once() {
        let mut app = test_app_with_agent();

        dispatch(Action::SendPrompt("/notacommand".into()), &mut app);

        assert_eq!(history(&app), ["/notacommand"]);
    }

    /// A stashed draft is not history. Only the send that follows puts a row in the recall list.
    #[test]
    fn stashing_a_draft_adds_no_history_row() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.prompt.set_text("first draft");
            agent.stash_prompt_draft(crate::app::agent_view::StashCause::ClearedDraft);
            agent.prompt.set_text("second draft");
            agent.stash_prompt_draft(crate::app::agent_view::StashCause::ClearedDraft);
        }
        assert!(history(&app).is_empty());

        dispatch(Action::SendPrompt("a real send".into()), &mut app);

        assert_eq!(history(&app), ["a real send"]);
    }

    /// `/remember foo` runs the command recorder and then `SendRememberNote(foo)`.
    #[test]
    fn a_slash_remember_records_only_the_typed_command() {
        let mut app = test_app_with_agent();

        dispatch(
            Action::SendPrompt("/remember deploys need the staging flag".into()),
            &mut app,
        );

        assert_eq!(history(&app), ["/remember deploys need the staging flag"]);
    }
}

mod prompt_stash_dispatch_tests {
    use super::test_app_with_agent;
    use crate::app::actions::Action;
    use crate::app::agent::AgentId;
    use crate::app::agent_view::StashCause;
    use crate::app::dispatch::router::dispatch;

    /// Esc-Esc clear hands the draft to the stash rather than dropping it. Ranking and restore semantics belong to `agent_view::prompt_stash`.
    #[test]
    fn clear_prompt_stashes_the_cleared_draft() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        app.agents
            .get_mut(&id)
            .unwrap()
            .prompt
            .set_text("cleared draft");

        let effects = dispatch(Action::ClearPrompt, &mut app);

        assert!(effects.is_empty());
        let agent = app.agents.get(&id).unwrap();
        assert_eq!(agent.prompt.text(), "", "composer must be cleared");
        let stash = agent.prompt_stash.as_ref().expect("draft was stashed");
        assert_eq!(stash.prompt.text, "cleared draft");
        assert_eq!(stash.cause, StashCause::ClearedDraft);
    }

    /// A cleared `!` draft lands in the slot with its shell mode and nowhere else: it was never sent.
    #[test]
    fn clearing_a_shell_draft_stashes_it_and_records_no_history() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.prompt_input_mode = crate::app::agent_view::PromptInputMode::Bash;
            agent.prompt.set_text("git status");
        }

        dispatch(Action::ClearPrompt, &mut app);

        let agent = app.agents.get(&id).unwrap();
        assert!(agent.combined_prompt_history().is_empty());
        let stash = agent.prompt_stash.as_ref().expect("draft was stashed");
        assert_eq!(stash.prompt.text, "git status");
        assert_eq!(
            stash.input_mode,
            crate::app::agent_view::PromptInputMode::Bash
        );
    }

    #[test]
    fn clear_prompt_on_empty_composer_stashes_nothing() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);

        dispatch(Action::ClearPrompt, &mut app);

        assert!(app.agents.get(&id).unwrap().prompt_stash.is_none());
    }

    #[test]
    fn chord_stashed_draft_auto_restores_after_next_send() {
        // Both sends consume the composer, so both hand the draft back.
        let sends = [
            Action::SendPrompt("quick side question".into()),
            Action::SendBashCommand("git status".into()),
        ];

        for send in sends {
            let label = format!("{send:?}");
            let mut app = test_app_with_agent();
            let id = AgentId(0);
            {
                let agent = app.agents.get_mut(&id).unwrap();
                agent.prompt.set_text("stashed thought");
                agent.stash_prompt_draft(StashCause::Chord);
            }

            dispatch(send, &mut app);

            let agent = app.agents.get(&id).unwrap();
            assert_eq!(
                agent.prompt.text(),
                "stashed thought",
                "{label} must restore the stash"
            );
            assert!(agent.prompt_stash.is_none(), "{label} left the slot full");
        }
    }

    /// A send deferred behind an in-flight paste probe never consumed the draft, so the stash must stay put.
    #[test]
    fn a_deferred_send_does_not_restore_the_stash() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        {
            let agent = app.agents.get_mut(&id).unwrap();
            agent.prompt.set_text("stashed thought");
            agent.stash_prompt_draft(StashCause::Chord);
            agent.prompt.set_text("never sent");
            agent.paste_probe_in_flight = 1;
        }

        dispatch(Action::SendPrompt("never sent".into()), &mut app);

        let agent = app.agents.get(&id).unwrap();
        assert_eq!(
            agent.prompt.text(),
            "never sent",
            "the draft is still unsent"
        );
        assert!(
            agent.prompt_stash.is_some(),
            "a deferred send must not hand the stash back"
        );
    }

    /// An Esc-Esc-cleared draft is a discard: it must NOT bounce back after the next send. Chord pop and history browse remain its recovery paths.
    #[test]
    fn esc_cleared_draft_does_not_auto_restore_after_send() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        app.agents
            .get_mut(&id)
            .unwrap()
            .prompt
            .set_text("discarded draft");

        dispatch(Action::ClearPrompt, &mut app);
        dispatch(Action::SendPrompt("next prompt".into()), &mut app);

        let agent = app.agents.get(&id).unwrap();
        assert_eq!(agent.prompt.text(), "", "discarded draft must stay stashed");
        assert!(agent.prompt_stash.is_some());
    }

    /// Auto-restore never overwrites composer state the user already touched.
    #[test]
    fn auto_restore_declines_on_a_non_empty_or_moded_composer() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        let agent = app.agents.get_mut(&id).unwrap();
        agent.prompt.set_text("stashed thought");
        agent.stash_prompt_draft(StashCause::Chord);

        agent.prompt.set_text("already typing");

        agent.auto_restore_stash_after_send();

        assert_eq!(agent.prompt.text(), "already typing");
        assert!(agent.prompt_stash.is_some());

        agent.prompt.set_text("");
        agent.prompt_input_mode = crate::app::agent_view::PromptInputMode::Bash;

        agent.auto_restore_stash_after_send();

        assert_eq!(
            agent.prompt.text(),
            "",
            "moded composer must not be overwritten"
        );
        assert!(agent.prompt_stash.is_some());
    }
}
