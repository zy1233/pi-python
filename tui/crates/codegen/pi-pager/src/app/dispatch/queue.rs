//! Prompt-queue dispatch: the local drip-feed drain ([`maybe_drain_queue`]) and
//! the action arm for a queued row whose edited text resolved to a pager
//! builtin.

use super::ctx::NO_SESSION_NOTICE;
use crate::acp::meta::user_prompt_meta;
use crate::app::actions::Effect;
use crate::app::agent::AgentId;
use crate::app::agent_view::{AgentView, PromptMode};
use crate::app::app_view::{ActiveView, AppView};
use crate::scrollback::block::RenderBlock;
use crate::scrollback::state::ScrollbackState;
use agent_client_protocol as acp;
use std::time::Instant;

fn page_flip_on_send() -> bool {
    crate::appearance::cache::load_page_flip_on_send()
}

/// Slash output skips the prompt drain; pin it at the top when page-flip is on.
pub(super) fn push_and_page_flip(scrollback: &mut ScrollbackState, block: RenderBlock) {
    scrollback.push_block(block);
    if !page_flip_on_send() {
        return;
    }
    let idx = scrollback.len() - 1;
    scrollback.scroll_to_entry_top(idx);
    scrollback.enable_follow_with_preserve();
}

fn combine_queued_prompts_enabled() -> bool {
    crate::appearance::cache::load_combine_queued_prompts()
}

/// Drain prompt-side images and snapshot all chip elements (paste blocks,
/// @-file refs, image chips) into the most recently enqueued `QueuedPrompt`.
///
/// Must be called after `enqueue_prompt` / `push_back` and before
/// `prompt.set_text("")` (which clears element and image state). If the
/// last queued entry already has `wire_blocks` (skill injection), images
/// are dropped with a toast instead of merged.
pub(super) fn drain_prompt_state_to_last_queued(agent: &mut AgentView) {
    let prompt_state = agent.prompt.stash();
    let (_, images, chip_elements) = prompt_state.into_submission();

    let Some(entry) = agent.session.pending_prompts.back_mut() else {
        return;
    };

    entry.chip_elements = chip_elements;

    if images.is_empty() {
        return;
    }

    // wire_blocks policy: skill-injected prompts do not carry prompt images.
    if entry.wire_blocks.is_some() {
        agent.show_toast("Images removed (skill prompt)");
        return;
    }

    entry.images = images;
}

/// Prepend `<system-reminder>` framing to a cron prompt for the model.
///
/// Delegates to the shared implementation in `pi_tools::reminders`.
/// The UI shows the raw `prompt` text via `RenderBlock::cron_prompt`; this
/// wrapped version is only sent to the model via `Effect::SendPrompt` so
/// the model knows the message is a scheduled task execution, not a human.
fn format_cron_prompt(prompt: &str, task_id: &str, human_schedule: &str) -> String {
    pi_tools::reminders::format_scheduled_task_prompt(prompt, task_id, human_schedule)
}

/// Try to send the next queued entry (prompt, command, bash, or cron) if the agent is idle.
///
/// Called after enqueue operations and task completions to advance the queue.
///
/// Branches on `QueueEntryKind`:
/// - **Prompt**: pushes user prompt block to scrollback, starts turn, returns `Effect::SendPrompt`
/// - **Command**: starts command, returns the appropriate `Effect` (e.g., `Effect::Compact`)
/// - **BashCommand**: starts turn (no user block), returns `Effect::SendBashCommand`
/// - **Cron**: pushes cron prompt block to scrollback, starts turn, returns `Effect::SendPrompt`
pub(super) struct QueueDrain {
    pub(super) effects: Vec<Effect>,
}

impl QueueDrain {
    fn blocked() -> Self {
        Self {
            effects: Vec::new(),
        }
    }
}

pub(super) fn maybe_drain_queue(agent: &mut AgentView) -> QueueDrain {
    use crate::app::agent::QueueEntryKind;
    use crate::unified_log as ulog;

    let sid = agent.session.session_id.as_ref().map(|s| s.0.as_ref());
    let queue_depth = agent.session.pending_prompts.len();

    let log_blocked = |reason: &str, sid: Option<&str>| {
        if queue_depth > 0 {
            ulog::debug(
                "prompt.drain_blocked",
                sid,
                Some(serde_json::json!({"reason": reason, "queue_depth": queue_depth})),
            );
        }
    };

    if !agent.session.state.is_idle() {
        log_blocked("turn_running", sid);
        return QueueDrain::blocked();
    }
    // Hold the drain during an in-flight model switch. See the
    // `model_switch_pending` field doc for why a reconnect must clear it.
    if agent.session.model_switch_pending {
        log_blocked("model_switch_pending", sid);
        return QueueDrain::blocked();
    }
    if agent.session.loading_replay {
        log_blocked("loading_replay", sid);
        return QueueDrain::blocked();
    }
    let Some(session_id) = agent.session.session_id.clone() else {
        log_blocked("no_session_id", None);
        return QueueDrain::blocked();
    };

    // Block drain if the user is editing the front prompt.
    if let PromptMode::EditingQueued { id, .. } = &agent.prompt_mode
        && agent
            .session
            .pending_prompts
            .front()
            .is_some_and(|p| p.id == *id)
    {
        // The prompt being edited is next to send — don't drain it
        // from under the user. The turn status line will show a
        // "waiting on your edit" indicator.
        log_blocked("user_editing_front", Some(&session_id.0));
        return QueueDrain::blocked();
    }

    // Row the user is actively editing (if any). The front-row case is already
    // handled above; pass it so a combined drain also stops before an edited
    // *follower* instead of merging it away.
    let editing_id = match &agent.prompt_mode {
        PromptMode::EditingQueued { id, .. } => Some(*id),
        _ => None,
    };
    let queued = match if combine_queued_prompts_enabled() {
        agent.session.dequeue_combined_prompt(editing_id)
    } else {
        agent.session.dequeue_prompt()
    } {
        Some(q) => q,
        None => return QueueDrain::blocked(),
    };

    // A new turn is starting: follow-up chips belong to the previous
    // response and must not linger into it.
    agent.clear_follow_ups();

    // This client is now sending its own prompt — it "takes the wheel" and is
    // no longer a passive viewer. Clearing this restores strict prompt-id gate
    // semantics (so stale chunks from a later rewind/cancel of THIS turn are
    // dropped, not adopted). See `AgentView::attached_as_viewer`.
    agent.attached_as_viewer = false;

    ulog::info(
        "prompt.drain",
        Some(&session_id.0),
        Some(serde_json::json!({
            "kind": queued.kind.as_label(),
            "remaining_in_queue": agent.session.pending_prompts.len(),
            "prompt_len": queued.text.len(),
        })),
    );
    tracing::debug!(
        target: "qtrace",
        pid = std::process::id(),
        event = "local_drain",
        kind = queued.kind.as_label(),
        remaining = agent.session.pending_prompts.len(),
        session = session_id.0.as_ref(),
        text = %queued.text.chars().take(48).collect::<String>(),
        "draining prompt LOCALLY as a new running turn",
    );

    let agent_id = agent.session.id;

    // Track whether this turn is a bash-mode command for post-turn focus.
    agent.bash_turn = queued.kind == QueueEntryKind::BashCommand;
    agent.cron_task_id = if queued.kind == QueueEntryKind::Cron {
        queued.task_id.clone()
    } else {
        None
    };
    // Generate a fresh prompt_id for every outgoing prompt/command. This is
    // threaded through PromptRequest._meta to the agent and echoed on every
    // SessionNotification + the PromptResponse, letting us correlate
    // notifications back to the originating prompt for cancel/rewind.
    let prompt_id = uuid::Uuid::new_v4().to_string();

    // Record it as self-originated so the ACP gate treats this turn's deltas as
    // ours (drive it; drop a stale post-rewind chunk on a mismatch) rather than
    // adopting them as another client's turn. The `Cron` arm overrides
    // `prompt_id` with a `scheduler-fired-` prefix and records that id itself.
    if queued.kind != QueueEntryKind::Cron {
        agent.note_self_originated_prompt(&prompt_id);
    }

    match queued.kind {
        QueueEntryKind::Prompt => {
            agent.start_turn_boundary();
            agent.session.current_prompt_id = Some(prompt_id.clone());
            // Scrollback shows display text (never raw skill XML). Combined
            // drains paint one bubble per original follow-up.
            let is_skill = queued.display_as_skill;
            let multi = pi_prompt_queue::is_combined(&queued.combined_texts);
            let (prompt_idx, prompt_entry_id, combined_entries) = if multi {
                let (first_idx, _, last_id, all_ids) =
                    paint_combined_user_bubbles(agent, &queued.combined_texts);
                (first_idx, last_id, all_ids)
            } else {
                let block = if is_skill {
                    RenderBlock::skill_prompt(&queued.text)
                } else if !queued.skill_token_ranges.is_empty() {
                    RenderBlock::user_prompt_with_skill_tokens(
                        &queued.text,
                        queued.skill_token_ranges.clone(),
                    )
                } else {
                    RenderBlock::user_prompt(&queued.text)
                };
                let id = agent.scrollback.push_block(block);
                (agent.scrollback.len().saturating_sub(1), id, vec![id])
            };
            // Stash for cancel-with-restore. Only plain (non-skill) prompts
            // can be reversed back into the input box.
            if queued.wire_blocks.is_none() {
                let earlier = combined_entries
                    .iter()
                    .copied()
                    .filter(|id| *id != prompt_entry_id)
                    .collect();
                agent.session.in_flight_prompt = Some(crate::app::agent::InFlightPrompt {
                    text: queued.text.clone(),
                    images: queued.images.clone(),
                    scrollback_entry: prompt_entry_id,
                    combined_scrollback_entries: earlier,
                    chip_elements: queued.chip_elements.clone(),
                });
            }
            agent.turn_started_at = Some(Instant::now());
            let flip = page_flip_on_send();
            agent.scrollback.follow_new_turn(Some(prompt_idx), flip);

            let combined_segs = queued.combined_texts.clone();
            let effects = if let Some(mut blocks) = queued.wire_blocks {
                // Skill injection: send structured blocks.
                // Annotate the first text block's meta with the display text
                // so the pager can reconstruct the clean prompt on session
                // restore (replay). Without this, replay shows the raw skill
                // instructions instead of the user-facing display text.
                if let Some(acp::ContentBlock::Text(tb)) = blocks.first_mut() {
                    let map = tb.meta.get_or_insert_with(acp::Meta::new);
                    map.insert(
                        user_prompt_meta::DISPLAY_TEXT.into(),
                        serde_json::Value::String(queued.text),
                    );
                    if is_skill {
                        map.insert(
                            user_prompt_meta::DISPLAY_AS_SKILL.into(),
                            serde_json::Value::Bool(true),
                        );
                    }
                    pi_prompt_queue::stamp_combined_display_texts(map, &combined_segs);
                } else {
                    tracing::debug!(
                        "wire_blocks[0] is not TextContent — displayText annotation skipped"
                    );
                }
                vec![Effect::SendPromptBlocks {
                    agent_id,
                    session_id,
                    blocks,
                    prompt_id,
                }]
            } else if !queued.images.is_empty() {
                // Image-bearing prompt: build text + image content blocks.
                // Pass the session cwd so orphan `[Image #N: <path>]`
                // placeholders (paste from a previous session, etc.)
                // can be recovered from disk via the shared helper.
                // Token ranges are NOT stamped here: the builder rewrites the
                // text (placeholder stripping), which would shift byte offsets.
                let mut blocks = crate::prompt_images::build_content_blocks_with_workspace(
                    queued.text,
                    queued.images,
                    Some(std::path::Path::new(&agent.session.cwd)),
                );
                if let Some(acp::ContentBlock::Text(tb)) = blocks.first_mut() {
                    let map = tb.meta.get_or_insert_with(acp::Meta::new);
                    pi_prompt_queue::stamp_combined_display_texts(map, &combined_segs);
                }
                vec![Effect::SendPromptBlocks {
                    agent_id,
                    session_id,
                    blocks,
                    prompt_id,
                }]
            } else if multi {
                // Stamp combinedDisplayTexts so reload paints multi-bubble. No
                // skillTokenRanges: dequeue_combined_prompt clears them on every
                // combined drain (multi paints plain per-segment bubbles).
                let mut tb = acp::TextContent::new(queued.text);
                let map = tb.meta.get_or_insert_with(acp::Meta::new);
                pi_prompt_queue::stamp_combined_display_texts(map, &combined_segs);
                vec![Effect::SendPromptBlocks {
                    agent_id,
                    session_id,
                    blocks: vec![acp::ContentBlock::Text(tb)],
                    prompt_id,
                }]
            } else {
                // Normal prompt: send text as-is.
                vec![Effect::SendPrompt {
                    agent_id,
                    session_id,
                    text: queued.text,
                    prompt_id,
                    skill_token_ranges: queued.skill_token_ranges,
                }]
            };
            QueueDrain { effects }
        }
        QueueEntryKind::BashCommand => {
            // Start turn but do NOT push a user prompt block.
            // The execute block from the shell IS the visual entry.
            agent.start_turn_boundary();
            agent.session.current_prompt_id = Some(prompt_id.clone());
            agent.turn_started_at = Some(Instant::now());

            agent.scrollback.follow_new_turn(None, page_flip_on_send());

            QueueDrain {
                effects: vec![Effect::SendBashCommand {
                    agent_id,
                    session_id,
                    command: queued.text,
                    prompt_id,
                }],
            }
        }
        QueueEntryKind::Cron => {
            let prompt_id = format!("scheduler-fired-{prompt_id}");
            agent.note_self_originated_prompt(&prompt_id);
            agent.start_turn_boundary();
            agent.session.current_prompt_id = Some(prompt_id.clone());
            agent
                .scrollback
                .push_block(RenderBlock::cron_prompt(&queued.text));
            agent.turn_started_at = Some(Instant::now());

            let prompt_idx = agent.scrollback.len().saturating_sub(1);
            let flip = page_flip_on_send();
            agent.scrollback.follow_new_turn(Some(prompt_idx), flip);

            let framed_text = format_cron_prompt(
                &queued.text,
                queued.task_id.as_deref().unwrap_or("unknown"),
                queued.human_schedule.as_deref().unwrap_or("unknown"),
            );

            let mut meta_map = serde_json::Map::new();
            meta_map.insert(
                user_prompt_meta::DISPLAY_TEXT.into(),
                serde_json::Value::String(queued.text),
            );
            meta_map.insert(
                user_prompt_meta::DISPLAY_AS_CRON.into(),
                serde_json::Value::Bool(true),
            );
            let blocks = vec![acp::ContentBlock::Text(
                acp::TextContent::new(framed_text).meta(Some(meta_map)),
            )];

            QueueDrain {
                effects: vec![Effect::SendPromptBlocks {
                    agent_id,
                    session_id,
                    blocks,
                    prompt_id,
                }],
            }
        }
    }
}

/// Paint one user bubble per combined segment of a drained, combined prompt.
///
/// Returns `(first_idx, first_id, last_id, all_segment_ids oldest→newest)`.
fn paint_combined_user_bubbles(
    agent: &mut AgentView,
    segments: &[String],
) -> (
    usize,
    crate::scrollback::EntryId,
    crate::scrollback::EntryId,
    Vec<crate::scrollback::EntryId>,
) {
    let mut first_idx = None;
    let mut first_id = None;
    let mut last_id = None;
    let mut all_ids = Vec::with_capacity(segments.len());
    for seg in segments {
        let id = agent
            .scrollback
            .push_block(RenderBlock::user_prompt(seg.clone()));
        all_ids.push(id);
        if first_idx.is_none() {
            first_idx = Some(agent.scrollback.len().saturating_sub(1));
            first_id = Some(id);
        }
        last_id = Some(id);
    }
    (
        first_idx.expect("segments non-empty"),
        first_id.expect("segments non-empty"),
        last_id.expect("segments non-empty"),
        all_ids,
    )
}

/// Drain the next queued prompt for `agent_id`, returning its effects.
pub(crate) fn drain_queue_for_agent(app: &mut AppView, agent_id: AgentId) -> Vec<Effect> {
    let Some(agent) = app.agents.get_mut(&agent_id) else {
        return vec![];
    };
    maybe_drain_queue(agent).effects
}

/// Try to drain the next queued prompt (triggered after editing completes).
pub(super) fn dispatch_drain_queue(app: &mut AppView) -> Vec<Effect> {
    let ActiveView::Agent(id) = app.active_view else {
        return vec![];
    };
    drain_queue_for_agent(app, id)
}

/// Decides whether the edited row is dropped and whether its text reaches the send path.
enum EditedCommandGate {
    /// Drop the row and run the command.
    Run,
    /// `dispatch_send_prompt_inner` refuses this one before running it: let it print the refusal
    /// and keep the row.
    RefusedBySendPath,
    /// No bound session: a command that needs one would fail after the row was gone. Keep the row
    /// and say so.
    NeedsSession,
}

/// `Action::RunEditedQueuedCommand` arm: the row's edited text resolved to a pager builtin, so drop
/// the row and run the text through slash dispatch, the owner of resolution and command telemetry.
///
/// Every gate that can refuse the command (active view, the send path's screen-mode refusal, a
/// bound session) runs before the removal, so a refusal leaves the row queued with its pre-edit
/// text instead of trading it for a hint.
pub(super) fn dispatch_run_edited_queued_command(
    app: &mut AppView,
    local_id: u64,
    text: String,
) -> Vec<Effect> {
    // The send half is bound to the active view, so resolve the removal target the same way. The
    // edit exit has already taken the composer text, so a silent bail would drop the command
    // without a trace.
    let ActiveView::Agent(agent_id) = app.active_view else {
        app.show_toast("Open the session to run this command");
        return vec![];
    };
    // The screen-mode refusal is resolved the executor's way (`get_for_dispatch` → `mode_support`)
    // so it cannot disagree with `dispatch_send_prompt_inner`; that path's restricted-command
    // upsell can't fire here because the classifier already required the same lookup.
    let screen_mode = app.screen_mode;
    let gate = {
        let Some(agent) = app.agents.get(&agent_id) else {
            return vec![];
        };
        let registry = agent.prompt.slash_controller.registry();
        let mode_refused = crate::slash::parse_invocation(text.trim()).is_some_and(|invocation| {
            registry
                .get_for_dispatch(invocation.token)
                .is_some_and(|command| {
                    command
                        .mode_support()
                        .refusal(invocation.token, screen_mode)
                        .is_some()
                })
        });
        if mode_refused {
            EditedCommandGate::RefusedBySendPath
        } else {
            // Fail closed on the session itself rather than on what a command declares:
            // `session_scoped` is a menu-offering hint, so it says nothing reliable about whether
            // `run()` needs a bound session.
            match agent.session.session_id.clone() {
                Some(_) => EditedCommandGate::Run,
                None => EditedCommandGate::NeedsSession,
            }
        }
    };

    let mut effects = Vec::new();
    let sends = match gate {
        EditedCommandGate::Run => {
            let Some(agent) = app.agents.get_mut(&agent_id) else {
                return vec![];
            };
            if let Some(removed) = agent.remove_local_queue_row(local_id) {
                // Defensive: the edit exit already cleaned the temp paths the composer
                // shared with this row. Covers images the composer never held.
                for image in &removed.images {
                    crate::prompt_images::cleanup_temp_file(image);
                }
            }
            true
        }
        EditedCommandGate::RefusedBySendPath => true,
        // Row kept and nothing runs: a command that ignores the missing session (`/compact`
        // enqueues regardless) would leave a second row.
        EditedCommandGate::NeedsSession => {
            if let Some(agent) = app.agents.get_mut(&agent_id) {
                agent.show_toast(NO_SESSION_NOTICE);
            }
            false
        }
    };
    if sends {
        effects.extend(super::prompt::dispatch_send_prompt_inner(
            app, text, /* consume_input */ false, /* literal */ false,
            /* is_follow_up */ false,
        ));
    }
    // The edit lock is released either way, so a command that starts no turn (or a refusal that
    // keeps the row) must not strand the queue, exactly as the plain save's `DrainQueue` did.
    effects.extend(drain_queue_for_agent(app, agent_id));
    effects
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::actions::Action;
    use crate::app::agent::AgentState;
    use crate::app::agent_view::test_fixtures::count_turn_markers;
    use crate::app::dispatch::router::dispatch;
    use crate::app::dispatch::tests::{end_turn, enqueue_local, test_app_with_agent};

    /// Scopes `[ui].combine_queued_prompts` on for one test. Restores the
    /// previous value on drop, because the cache is thread-local and a
    /// `--test-threads=1` run reuses one thread across tests.
    struct CombineQueuedPrompts {
        previous: bool,
    }
    impl CombineQueuedPrompts {
        fn enter() -> Self {
            let previous = crate::appearance::cache::load_combine_queued_prompts();
            crate::appearance::cache::set_combine_queued_prompts(true);
            Self { previous }
        }
    }
    impl Drop for CombineQueuedPrompts {
        fn drop(&mut self) {
            crate::appearance::cache::set_combine_queued_prompts(self.previous);
        }
    }

    #[test]
    fn format_cron_prompt_includes_framing() {
        let out = super::format_cron_prompt("do stuff", "task-1", "every 5m");
        assert!(out.starts_with("<system-reminder>"));
        assert!(out.contains("task task-1"));
        assert!(out.contains("every 5m"));
        assert!(out.contains("do stuff"));
        assert!(
            !out.contains("<user_query>"),
            "must not add <user_query> — shell does that"
        );
        assert!(out.ends_with("do stuff"));
    }

    // ── Drain-blocking tests ───────────────────────────────────────────

    #[test]
    fn drain_blocked_when_editing_front_prompt() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);

        // Queue 3 prompts, first one drains immediately (turn starts); the
        // follow-ups populate the local queue directly (see `enqueue_local`).
        dispatch(Action::SendPrompt("first".into()), &mut app);
        enqueue_local(&mut app, id, "second");
        enqueue_local(&mut app, id, "third");
        assert!(app.agents[&id].session.state.is_turn_running());
        assert_eq!(app.agents[&id].session.queue_len(), 2);

        // Simulate user editing "second" (which becomes front after "first" ends).
        let second_id = app.agents[&id].session.pending_prompts[0].id;
        app.agents.get_mut(&id).unwrap().prompt_mode = PromptMode::EditingQueued {
            id: second_id,
            original: "second".into(),
            kind: crate::app::agent::QueueEntryKind::Prompt,
        };

        // Turn ends → should NOT drain "second" (user is editing it), only FetchBilling.
        let effects = dispatch(end_turn(), &mut app);
        assert_eq!(effects.len(), 1);
        assert!(matches!(
            &effects[0],
            Effect::FetchBilling { silent: true, .. }
        ));
        assert!(app.agents[&id].session.state.is_idle());
        // "second" should still be in the queue.
        assert_eq!(app.agents[&id].session.queue_len(), 2);
        assert_eq!(app.agents[&id].session.pending_prompts[0].text, "second");
    }

    #[test]
    fn drain_not_blocked_when_editing_non_front_prompt() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);

        // Queue 3 prompts, first drains.
        dispatch(Action::SendPrompt("first".into()), &mut app);
        enqueue_local(&mut app, id, "second");
        enqueue_local(&mut app, id, "third");

        // Simulate user editing "third" (NOT the front).
        let third_id = app.agents[&id].session.pending_prompts[1].id;
        app.agents.get_mut(&id).unwrap().prompt_mode = PromptMode::EditingQueued {
            id: third_id,
            original: "third".into(),
            kind: crate::app::agent::QueueEntryKind::Prompt,
        };

        // Turn ends → should drain "second" (front, not being edited) + FetchBilling.
        let effects = dispatch(end_turn(), &mut app);
        assert_eq!(effects.len(), 2);
        assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "second"));
        assert!(matches!(
            &effects[1],
            Effect::FetchBilling { silent: true, .. }
        ));
        // "third" should still be in queue.
        assert_eq!(app.agents[&id].session.queue_len(), 1);
        assert_eq!(app.agents[&id].session.pending_prompts[0].text, "third");
    }

    // Edited row that resolved to a pager builtin

    fn run_edited_queued_command(app: &mut AppView, local_id: u64, text: &str) -> Vec<Effect> {
        dispatch(
            Action::RunEditedQueuedCommand {
                local_id,
                text: text.into(),
            },
            app,
        )
    }

    /// With no agent view active the send half would no-op, so nothing runs and the row keeps
    /// its text.
    #[test]
    fn run_edited_queued_command_off_active_view_keeps_row() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        enqueue_local(&mut app, id, "what is the default");
        let local_id = app.agents[&id].session.pending_prompts[0].id;
        app.active_view = ActiveView::Welcome;

        let effects = run_edited_queued_command(&mut app, local_id, "/btw why");

        assert!(effects.is_empty());
        assert_eq!(app.agents[&id].session.queue_len(), 1);
    }

    /// Fail closed while the session is binding: the row survives and the user is told why.
    #[test]
    fn run_edited_queued_command_without_session_keeps_row() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        enqueue_local(&mut app, id, "what is the default");
        let local_id = app.agents[&id].session.pending_prompts[0].id;
        app.agents.get_mut(&id).unwrap().session.session_id = None;

        let effects = run_edited_queued_command(&mut app, local_id, "/btw why");

        assert!(effects.is_empty(), "nothing may run without a session");
        assert_eq!(app.agents[&id].session.queue_len(), 1);
        assert_eq!(
            app.agents[&id]
                .toast
                .as_ref()
                .map(|(text, _)| text.as_str()),
            Some("No active session")
        );
    }

    /// A command that produces no effect at all still costs the row: the removal is not tied to
    /// something coming back from the send path.
    #[test]
    fn run_edited_queued_command_drops_row_for_effect_free_builtin() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        enqueue_local(&mut app, id, "edited into a command");
        let local_id = app.agents[&id].session.pending_prompts[0].id;

        let effects = run_edited_queued_command(&mut app, local_id, "/help");

        assert!(effects.is_empty(), "the palette is state, not an effect");
        assert_eq!(app.agents[&id].session.queue_len(), 0, "row dropped");
        assert!(
            matches!(
                app.agents[&id].active_modal,
                Some(crate::views::modal::ActiveModal::CommandPalette { .. })
            ),
            "the command ran"
        );
    }

    #[test]
    fn drain_queue_action_sends_front_prompt() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);

        // Queue a prompt but don't drain (set turn running first).
        app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
        enqueue_local(&mut app, id, "queued");
        assert_eq!(app.agents[&id].session.queue_len(), 1);

        // Set idle to simulate turn end (without going through PromptResponse).
        app.agents.get_mut(&id).unwrap().session.state = AgentState::Idle;

        // DrainQueue should pop and send.
        let effects = dispatch(Action::DrainQueue, &mut app);
        assert_eq!(effects.len(), 1);
        assert!(matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "queued"));
        assert_eq!(app.agents[&id].session.queue_len(), 0);
    }

    #[test]
    fn drain_queue_when_empty_does_nothing() {
        let mut app = test_app_with_agent();
        let effects = dispatch(Action::DrainQueue, &mut app);
        assert!(effects.is_empty());
    }

    /// A drained bash row sets `bash_turn` and pushes NO user block (the
    /// shell's execute block IS the entry).
    #[test]
    fn drain_bash_row_sets_bash_turn_and_pushes_no_user_block() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        app.agents
            .get_mut(&id)
            .unwrap()
            .session
            .enqueue_bash_command("ls -la".into());
        let before = app.agents[&id].scrollback.len();

        let effects = dispatch(Action::DrainQueue, &mut app);

        assert!(
            matches!(&effects[0], Effect::SendBashCommand { command, .. } if command == "ls -la"),
            "got {effects:?}"
        );
        let agent = &app.agents[&id];
        assert!(agent.bash_turn, "bash drain must set bash_turn");
        assert!(agent.session.state.is_turn_running());
        assert_eq!(agent.scrollback.len(), before);
        assert!(agent.session.in_flight_prompt.is_none());
    }

    /// Starting a drained turn clears the previous response's follow-up chips.
    #[test]
    fn drain_clears_follow_up_chips() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        app.agents
            .get_mut(&id)
            .unwrap()
            .apply_follow_ups("resp-1".into(), vec!["a".into()]);
        enqueue_local(&mut app, id, "next");

        dispatch(Action::DrainQueue, &mut app);

        assert!(
            app.agents[&id].follow_ups.is_none(),
            "a new turn must clear chips"
        );
    }

    #[test]
    fn drain_queue_when_running_does_nothing() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);

        app.agents.get_mut(&id).unwrap().session.state = AgentState::TurnRunning;
        enqueue_local(&mut app, id, "queued");

        // DrainQueue while running → no effect.
        let effects = dispatch(Action::DrainQueue, &mut app);
        assert!(effects.is_empty());
        assert_eq!(app.agents[&id].session.queue_len(), 1);
    }

    #[test]
    fn drain_queue_blocked_during_loading_replay() {
        let mut app = test_app_with_agent();
        let id = AgentId(0);

        // Simulate session-resume state: Idle but still replaying.
        let agent = app.agents.get_mut(&id).unwrap();
        agent.session.loading_replay = true;
        agent.session.enqueue_cron_prompt(
            "check status".into(),
            "task-1".into(),
            "every 5m".into(),
        );

        // Drain while loading_replay is true → must be blocked.
        let effects = maybe_drain_queue(app.agents.get_mut(&id).unwrap()).effects;
        assert!(
            effects.is_empty(),
            "drain must be blocked during loading_replay"
        );
        assert_eq!(
            app.agents[&id].session.queue_len(),
            1,
            "cron prompt must stay queued"
        );

        // Clear loading_replay (simulates SessionLoaded completing).
        app.agents.get_mut(&id).unwrap().session.loading_replay = false;

        // Drain again → should succeed now.
        let effects = maybe_drain_queue(app.agents.get_mut(&id).unwrap()).effects;
        assert_eq!(effects.len(), 1);
        assert!(
            matches!(&effects[0], Effect::SendPromptBlocks { .. }),
            "expected SendPromptBlocks effect, got: {:?}",
            effects[0]
        );
        assert_eq!(
            app.agents[&id].session.queue_len(),
            0,
            "queue must be empty after drain"
        );
        assert_eq!(
            app.agents[&id].cron_task_id.as_deref(),
            Some("task-1"),
            "cron_task_id must track the running cron task"
        );
    }

    #[test]
    fn drain_after_editing_sends_correct_prompt() {
        // Regression: editing #3, prompts #1 and #2 drain, #3 becomes front.
        // User presses Enter (save) → DrainQueue should send #3's updated text,
        // NOT #4 or the old text.
        let mut app = test_app_with_agent();
        let id = AgentId(0);

        // Queue 4 prompts, first drains.
        dispatch(Action::SendPrompt("p1".into()), &mut app);
        enqueue_local(&mut app, id, "p2");
        enqueue_local(&mut app, id, "p3");
        enqueue_local(&mut app, id, "p4");
        assert_eq!(app.agents[&id].session.queue_len(), 3); // p2, p3, p4

        // End turn for p1 → sets Idle → maybe_drain_queue pops p2 → Running again.
        // Queue is now: p3, p4.
        dispatch(end_turn(), &mut app);
        assert_eq!(app.agents[&id].session.queue_len(), 2);

        // Start editing p3 (now front).
        let p3_id = app.agents[&id].session.pending_prompts[0].id;
        app.agents.get_mut(&id).unwrap().prompt_mode = PromptMode::EditingQueued {
            id: p3_id,
            original: "p3".into(),
            kind: crate::app::agent::QueueEntryKind::Prompt,
        };

        // End turn for p2 → should NOT drain p3 (being edited), only FetchBilling.
        let effects = dispatch(end_turn(), &mut app);
        assert_eq!(effects.len(), 1);
        assert!(
            matches!(&effects[0], Effect::FetchBilling { silent: true, .. }),
            "drain should be blocked, only billing refresh"
        );
        assert_eq!(app.agents[&id].session.queue_len(), 2); // p3, p4

        // Simulate user saving edited text.
        app.agents
            .get_mut(&id)
            .unwrap()
            .session
            .pending_prompts
            .iter_mut()
            .find(|p| p.id == p3_id)
            .unwrap()
            .text = "p3-edited".into();
        app.agents.get_mut(&id).unwrap().prompt_mode = PromptMode::Normal;

        // DrainQueue after edit → should send "p3-edited", not "p4".
        let effects = dispatch(Action::DrainQueue, &mut app);
        assert_eq!(effects.len(), 1);
        assert!(
            matches!(&effects[0], Effect::SendPrompt { text, .. } if text == "p3-edited"),
            "should send the edited prompt"
        );
        // p4 should still be in queue.
        assert_eq!(app.agents[&id].session.queue_len(), 1);
        assert_eq!(app.agents[&id].session.pending_prompts[0].text, "p4");
    }

    /// Number of `UserPrompt` blocks with exactly `text`.
    fn user_prompt_count(agent: &crate::app::agent_view::AgentView, text: &str) -> usize {
        (0..agent.scrollback.len())
            .filter_map(|i| agent.scrollback.entry(i))
            .filter(|e| matches!(&e.block, RenderBlock::UserPrompt(ub) if ub.text == text))
            .count()
    }

    /// A combined drain paints one bubble per original follow-up (never the joined body).
    #[test]
    fn combined_drain_paints_one_bubble_per_segment() {
        let _combine = CombineQueuedPrompts::enter();
        let mut app = test_app_with_agent();
        let id = AgentId(0);
        dispatch(Action::SendPrompt("first".into()), &mut app);
        enqueue_local(&mut app, id, "second");
        enqueue_local(&mut app, id, "third");

        dispatch(end_turn(), &mut app);

        let agent = &app.agents[&id];
        assert_eq!(agent.session.queue_len(), 0, "both rows drain together");
        assert_eq!(user_prompt_count(agent, "second"), 1);
        assert_eq!(user_prompt_count(agent, "third"), 1);
        assert_eq!(user_prompt_count(agent, "second\n\nthird"), 0);
        assert_eq!(
            agent.session.in_flight_prompt.as_ref().unwrap().text,
            "second\n\nthird"
        );
    }

    /// Deleting the last queued row through the queue-pane key path writes no turn marker.
    #[test]
    fn local_delete_of_last_queued_row_adds_no_marker() {
        use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

        let mut app = test_app_with_agent();
        let id = AgentId(0);
        dispatch(Action::SendPrompt("first".into()), &mut app);
        enqueue_local(&mut app, id, "queued row");

        let agent = app.agents.get_mut(&id).unwrap();
        assert_eq!(count_turn_markers(agent), 0);

        agent.queue.sync_from_local(&agent.session.pending_prompts);
        let ids = agent.queue.entry_ids();
        assert_eq!(ids.len(), 1);
        agent.queue.list_state.select_by_id(ids[0]);
        let _ = agent.handle_queue_key(&KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));

        assert!(agent.session.pending_prompts.is_empty());
        assert_eq!(
            count_turn_markers(agent),
            0,
            "deleting the last queued row must not write a marker"
        );
    }

    /// A running turn keeps the terminal progress indicator (OSC 9;4) active.
    #[test]
    fn running_turn_keeps_progress_indicator_active() {
        let mut app = test_app_with_agent();
        dispatch(Action::SendPrompt("first".into()), &mut app);

        app.update_notifications();

        assert!(
            app.notification_service.is_progress_active(),
            "live turn must keep the OSC 9;4 progress indicator active"
        );
    }
}
