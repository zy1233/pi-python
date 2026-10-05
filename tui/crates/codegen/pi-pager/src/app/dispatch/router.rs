//! Top-level action router: maps actions and action results to handlers.
use super::auth::{
    dispatch_cancel_login, dispatch_login, dispatch_logout, dispatch_submit_auth_code,
    dispatch_switch_account,
};
use super::billing::dispatch_open_supergrok_url;
use super::ctx::{
    navigate_clearing_selection, open_url_or_show, sync_sleep_inhibitor, with_active_agent,
    with_scrollback,
};
use super::modes::{dispatch_cycle_mode, set_permission_mode, set_plan_mode, set_yolo_mode};
use super::permissions::{
    dispatch_permission_cancel, dispatch_permission_followup, dispatch_permission_select,
};
use super::prompt::{
    dispatch_accept_word_select_tip, dispatch_clear_prompt, dispatch_send_bash_command,
    dispatch_send_prompt, dispatch_send_prompt_inner, dispatch_show_plan_nudge,
    dispatch_show_undo_tip, dispatch_show_word_select_tip,
};
use super::queue;
use super::queue::dispatch_drain_queue;
use super::session::lifecycle::{
    dispatch_agent_type_mismatch_answered, dispatch_delete_current_session_answered,
    dispatch_exit_session, dispatch_new_session, dispatch_new_session_inner,
    dispatch_new_session_with_id, dispatch_new_worktree_session, dispatch_trust_folder,
    open_new_session_question,
};
use super::session::load::{
    dispatch_load_session, dispatch_pick_session, dispatch_pick_session_in_worktree,
    dispatch_session_picker_closed, dispatch_show_session_picker, session_picker_entry_matches,
    toggle_session_card,
};
use super::session::session_list::dispatch_fetch_session_list;
use super::session::worktree_mode::apply_persist_worktree_mode;
use super::settings::setters::{
    clear_default_model, clear_fork_secondary_model, preview_auto_dark_theme,
    preview_auto_light_theme, preview_theme, set_ask_user_question_timeout_enabled,
    set_auto_dark_theme, set_auto_light_theme, set_auto_update, set_collapsed_edit_blocks,
    set_combine_queued_prompts, set_compact_mode, set_contextual_hint_image_input,
    set_contextual_hint_plan_mode, set_contextual_hint_small_screen, set_contextual_hint_ssh_wrap,
    set_contextual_hint_undo, set_contextual_hint_word_select, set_default_model,
    set_default_selected_permission, set_display_refresh_auto_cadence, set_fork_secondary_model,
    set_group_tool_verbs, set_hunk_tracker_mode, set_invert_scroll, set_keep_text_selection,
    set_max_thoughts_width, set_multiline_mode, set_page_flip_on_send, set_prompt_suggestions,
    set_remember_tool_approvals, set_render_mermaid, set_respect_manual_folds, set_screen_mode,
    set_scroll_lines, set_scroll_mode, set_scroll_speed, set_show_thinking_blocks, set_show_tips,
    set_simple_mode, set_theme, set_timeline, set_timestamps, set_vim_mode, set_voice_capture_mode,
    set_voice_keybind_enabled, set_voice_stt_language,
};
use super::settings::ui::{
    dispatch_confirm_reset_setting, dispatch_open_command_palette, dispatch_open_reset_confirm,
    dispatch_open_settings, dispatch_toggle_mouse_capture,
};
use super::status::{dispatch_copy_session_id, dispatch_show_queue};
use super::task_result::{dispatch_task_result, unregister_all_active_sessions};
use super::transcript::{
    dispatch_copy_block_content, dispatch_copy_block_meta, dispatch_dump_input_log,
    dispatch_open_block_viewer, dispatch_open_transcript_pager,
};
use super::turn::dispatch_cancel_turn;
use super::voice::{dispatch_enable_voice_mode, dispatch_voice_stop, dispatch_voice_toggle};
use crate::app::actions::{Action, Effect};
use crate::app::agent_view::ActivePane;
use crate::app::app_view::{ActiveView, AppView, AuthState};
use crate::scrollback::types::DisplayMode;
use pi_telemetry::session_ctx::log_event;
pub(super) fn dispatch_copy_auth_url(
    app: &mut AppView,
    copy: impl FnOnce(&str) -> crate::clipboard::ClipboardDelivery,
) -> Vec<Effect> {
    let AuthState::Authenticating {
        auth_url: Some(url),
        ..
    } = &app.auth_state
    else {
        return vec![];
    };
    app.auth_clipboard_delivery = Some(copy(url));
    app.auth_clipboard_feedback_generation = app.auth_clipboard_feedback_generation.wrapping_add(1);
    vec![Effect::ScheduleClearAuthCopyFeedback {
        generation: app.auth_clipboard_feedback_generation,
    }]
}
/// Dispatch an action: mutate state, return effects to execute.
///
/// The returned `Vec<Effect>` may be empty (pure state mutation) or contain
/// async work that the event loop should spawn.
///
/// The match feeds the `sync_sleep_inhibitor(app)` tail below it; arms that
/// `return` early bypass that tail deliberately. Do not extract a returning
/// arm into a handler: as a delegation its `return`s become plain arm values
/// and start flowing through the tail. The fat inline arms stayed inline for
/// this reason; audit an arm's `return`s before moving it.
pub(crate) fn dispatch(action: Action, app: &mut AppView) -> Vec<Effect> {
    let effects = match action {
        Action::Quit | Action::QuitConfirmed => {
            if let Some(tx) = &app.voice_cmd_tx {
                let _ = tx.try_send(pi_voice::VoiceCommand::Shutdown);
            }
            let mut effects = unregister_all_active_sessions(app);
            effects.push(Effect::Quit);
            effects
        }
        Action::QuitForUpdate => {
            let mut effects = unregister_all_active_sessions(app);
            app.quit_for_update = true;
            effects.push(Effect::Quit);
            effects
        }
        Action::NewSession => dispatch_new_session(app),
        Action::ChooseNewSessionMode => open_new_session_question(app),
        Action::ExitSession => dispatch_exit_session(app),
        Action::DeleteCurrentSessionAnswered { confirmed } => {
            dispatch_delete_current_session_answered(app, confirmed)
        }
        Action::NewWorktreeSession {
            load_session_id,
            label,
            git_ref,
        } => dispatch_new_worktree_session(app, load_session_id, label, None, None, git_ref, None),
        Action::OpenNewWorktreeDialog => {
            app.new_worktree_dialog = Some(crate::app::app_view::NewWorktreeDialogState::new());
            vec![]
        }
        Action::LoadSession(session_id, session_cwd) => {
            dispatch_load_session(app, session_id, session_cwd)
        }
        Action::NewSessionWithId(session_id) => dispatch_new_session_with_id(app, session_id),
        Action::FetchSessionList => dispatch_fetch_session_list(app),
        Action::ShowSessionPicker => dispatch_show_session_picker(app),
        Action::SessionPickerClosed => dispatch_session_picker_closed(app),
        Action::PickSession(index) => dispatch_pick_session(app, index),
        Action::PickSessionInWorktree(index) => dispatch_pick_session_in_worktree(app, index),
        Action::CopySessionId(index) => dispatch_copy_session_id(app, index),
        Action::ExpandSessionCard { source, session_id } => {
            toggle_session_card(app, &source, &session_id)
        }
        Action::SendPrompt(text) => dispatch_send_prompt(app, text),
        Action::SubmitFollowUp(text) => dispatch_send_prompt_inner(app, text, false, true, true),
        Action::SendSlashCommandPreservingDraft(text) => {
            dispatch_send_prompt_inner(app, text, false, false, false)
        }
        Action::EnableVoiceMode => dispatch_enable_voice_mode(app, true),
        Action::VoiceToggle => dispatch_voice_toggle(app),
        Action::VoiceStop => dispatch_voice_stop(app),
        Action::SendBashCommand(cmd) => dispatch_send_bash_command(app, cmd),
        Action::ShowUndoTip => dispatch_show_undo_tip(app),
        Action::ShowPlanNudge => dispatch_show_plan_nudge(app),
        Action::ShowWordSelectTip => dispatch_show_word_select_tip(app),
        Action::AcceptWordSelectTip => dispatch_accept_word_select_tip(app),
        Action::DrainQueue => dispatch_drain_queue(app),
        Action::RunEditedQueuedCommand { local_id, text } => {
            queue::dispatch_run_edited_queued_command(app, local_id, text)
        }
        Action::FocusPrompt => {
            with_active_agent(app, |agent| {
                agent.set_active_pane(ActivePane::Prompt, false);
            });
            vec![]
        }
        Action::FocusScrollback => {
            with_active_agent(app, |agent| {
                agent.set_active_pane(ActivePane::Scrollback, false);
            });
            vec![]
        }
        Action::ClearPrompt => dispatch_clear_prompt(app),
        Action::SelectNext => {
            navigate_clearing_selection(app, |s| s.select_next());
            vec![]
        }
        Action::SelectPrev => {
            navigate_clearing_selection(app, |s| s.select_prev());
            vec![]
        }
        Action::NextTurn => {
            with_scrollback(app, |s| {
                s.next_turn();
            });
            vec![]
        }
        Action::PrevTurn => {
            with_scrollback(app, |s| {
                s.prev_turn();
            });
            vec![]
        }
        Action::NextResponse => {
            with_scrollback(app, |s| {
                s.next_response();
            });
            vec![]
        }
        Action::PrevResponse => {
            with_scrollback(app, |s| {
                s.prev_response();
            });
            vec![]
        }
        Action::GotoTop => {
            navigate_clearing_selection(app, |s| s.goto_top());
            vec![]
        }
        Action::GotoBottom => {
            navigate_clearing_selection(app, |s| s.goto_bottom());
            vec![]
        }
        Action::ScrollUp(n) => {
            with_scrollback(app, |s| s.scroll_up(n));
            vec![]
        }
        Action::ScrollDown(n) => {
            with_scrollback(app, |s| s.scroll_down(n));
            vec![]
        }
        Action::HalfPageUp => {
            navigate_clearing_selection(app, |s| s.half_page_up());
            vec![]
        }
        Action::HalfPageDown => {
            navigate_clearing_selection(app, |s| s.half_page_down());
            vec![]
        }
        Action::PageUp => {
            navigate_clearing_selection(app, |s| s.page_up());
            vec![]
        }
        Action::PageDown => {
            navigate_clearing_selection(app, |s| s.page_down());
            vec![]
        }
        Action::Collapse => {
            with_scrollback(app, |s| {
                let at_minimum = s
                    .selected()
                    .and_then(|i| s.entry(i))
                    .is_some_and(|e| e.display_mode == DisplayMode::Collapsed);
                if !at_minimum || !s.collapse_group_if_expanded() {
                    s.collapse_selected();
                }
            });
            vec![]
        }
        Action::Expand => {
            with_scrollback(app, |s| {
                if !s.toggle_group_expansion() {
                    s.expand_selected();
                }
            });
            vec![]
        }
        Action::ToggleFold => {
            with_scrollback(app, |s| {
                if !s.toggle_group_expansion() {
                    s.toggle_fold_selected();
                }
            });
            vec![]
        }
        Action::ToggleExpandAll => {
            with_scrollback(app, |s| s.toggle_expand_all());
            vec![]
        }
        Action::ExpandAllThinking => {
            with_scrollback(app, |s| s.expand_all_thinking());
            vec![]
        }
        Action::ToggleRaw => {
            with_scrollback(app, |s| s.toggle_raw_selected());
            vec![]
        }
        Action::ToggleMouseCapture => {
            crate::unified_log::info(
                "mouse_reporting_toggle.dispatch",
                None,
                Some(serde_json::json!({
                    "phase": "entered_dispatch_arm",
                })),
            );
            dispatch_toggle_mouse_capture(app);
            vec![]
        }
        Action::CopyBlockContent => {
            dispatch_copy_block_content(app);
            vec![]
        }
        Action::OpenTranscriptPager => {
            dispatch_open_transcript_pager(app);
            vec![]
        }
        Action::CopyBlockMeta => {
            dispatch_copy_block_meta(app);
            vec![]
        }
        Action::OpenBlockViewer => {
            let mut group_toggled = false;
            with_scrollback(app, |s| group_toggled = s.toggle_group_expansion());
            if group_toggled {
                return vec![];
            }
            let mut credit_card: Option<(String, pi_telemetry::events::CreditLimitChoice)> = None;
            with_scrollback(app, |s| {
                if let Some(idx) = s.selected()
                    && let Some(entry) = s.entry(idx)
                    && let crate::scrollback::block::RenderBlock::CreditLimit(ref blk) = entry.block
                {
                    use crate::scrollback::blocks::CreditLimitCardAction;
                    let choice = match blk.action {
                        CreditLimitCardAction::PurchaseCredits => {
                            pi_telemetry::events::CreditLimitChoice::PurchaseCredits
                        }
                        CreditLimitCardAction::EnablePayg
                        | CreditLimitCardAction::IncreasePaygLimit => {
                            pi_telemetry::events::CreditLimitChoice::PayAsYouGo
                        }
                    };
                    credit_card = Some((blk.url.clone(), choice));
                }
            });
            if let Some((url, choice)) = credit_card {
                log_event(pi_telemetry::events::CreditLimitUpsellClicked {
                    surface: pi_telemetry::events::CreditLimitUpsellSurface::InlineCard,
                    choice,
                });
                open_url_or_show(app, &url);
            } else {
                dispatch_open_block_viewer(app);
            }
            vec![]
        }
        Action::NextModel => vec![],
        Action::SwitchModel { model_id, effort } => {
            let ActiveView::Agent(id) = app.active_view else {
                return vec![];
            };
            let Some(agent) = app.agents.get_mut(&id) else {
                return vec![];
            };
            let Some(session_id) = agent.session.session_id.clone() else {
                let prev_model = agent.session.models.current.clone();
                let prev_effort = agent.session.models.reasoning_effort;
                agent.session.models.set_current(model_id.clone(), effort);
                let resolved_effort = agent.session.models.reasoning_effort;
                let unchanged =
                    prev_model.as_ref() == Some(&model_id) && prev_effort == resolved_effort;
                let rollback_prev = agent
                    .session
                    .deferred_model_switch
                    .take()
                    .and_then(|prior| prior.prev_model_id)
                    .or(prev_model);
                agent.session.deferred_model_switch =
                    Some(crate::app::agent::DeferredModelSwitch {
                        model_id: model_id.clone(),
                        effort,
                        prev_model_id: rollback_prev,
                    });
                return if unchanged {
                    vec![]
                } else {
                    vec![Effect::PersistPreferredModel {
                        model_id,
                        reasoning_effort: resolved_effort,
                    }]
                };
            };
            agent.session.model_switch_pending = true;
            vec![Effect::SwitchModel {
                agent_id: id,
                session_id,
                model_id,
                effort,
                prev_model_id: None,
                config_option_id: agent.session.models.config_option_id.clone(),
            }]
        }
        Action::CancelTurn => dispatch_cancel_turn(app),
        Action::CycleMode => dispatch_cycle_mode(app),
        Action::ShowQueue => dispatch_show_queue(app),
        Action::SetPlanMode(kind) => set_plan_mode(app, kind),
        Action::SetVimMode(v) => set_vim_mode(app, v),
        Action::SetRememberToolApprovals(v) => set_remember_tool_approvals(app, v),
        Action::SetAskUserQuestionTimeoutEnabled(v) => {
            set_ask_user_question_timeout_enabled(app, v)
        }
        Action::SetKeepTextSelection(v) => set_keep_text_selection(app, v),
        Action::SetScrollSpeed(v) => set_scroll_speed(app, v),
        Action::SetScrollMode(v) => set_scroll_mode(app, v),
        Action::SetInvertScroll(v) => set_invert_scroll(app, v),
        Action::SetScrollLines(v) => set_scroll_lines(app, v),
        Action::SetShowThinkingBlocks(v) => set_show_thinking_blocks(app, v),
        Action::SetGroupToolVerbs(v) => set_group_tool_verbs(app, v),
        Action::SetCollapsedEditBlocks(v) => set_collapsed_edit_blocks(app, v),
        Action::SetPromptSuggestions(v) => set_prompt_suggestions(app, v),
        Action::SetRespectManualFolds(v) => set_respect_manual_folds(app, v),
        Action::SetDefaultSelectedPermission(s) => set_default_selected_permission(app, s),
        Action::SetHunkTrackerMode(s) => set_hunk_tracker_mode(app, s),
        Action::SetScreenMode(s) => set_screen_mode(app, s),
        Action::SetVoiceKeybindEnabled(v) => set_voice_keybind_enabled(app, v),
        Action::SetVoiceCaptureMode(s) => set_voice_capture_mode(app, s),
        Action::SetVoiceSttLanguage(s) => set_voice_stt_language(app, s),
        Action::SetYoloMode(v) => set_yolo_mode(app, v),
        Action::SetPermissionMode(kind) => set_permission_mode(app, kind),
        Action::SetMultilineMode(v) => set_multiline_mode(app, v),
        Action::SetRenderMermaid(kind) => set_render_mermaid(app, kind),
        Action::SetCompactMode(v) => set_compact_mode(app, v),
        Action::SetTimestamps(v) => set_timestamps(app, v),
        Action::SetTimeline(v) => set_timeline(app, v),
        Action::SetPageFlipOnSend(v) => set_page_flip_on_send(app, v),
        Action::SetCombineQueuedPrompts(v) => set_combine_queued_prompts(app, v),

        Action::SetSimpleMode(v) => set_simple_mode(app, v),
        Action::SetContextualHintUndo(v) => set_contextual_hint_undo(app, v),
        Action::SetContextualHintPlanMode(v) => set_contextual_hint_plan_mode(app, v),
        Action::SetContextualHintImageInput(v) => set_contextual_hint_image_input(app, v),

        Action::SetContextualHintSmallScreen(v) => set_contextual_hint_small_screen(app, v),
        Action::SetContextualHintWordSelect(v) => set_contextual_hint_word_select(app, v),
        Action::SetContextualHintSshWrap(v) => set_contextual_hint_ssh_wrap(app, v),
        Action::SetTheme(v) => set_theme(app, v),
        Action::SetAutoDarkTheme(v) => set_auto_dark_theme(app, v),
        Action::SetAutoLightTheme(v) => set_auto_light_theme(app, v),
        Action::SetDefaultModel(v) => set_default_model(app, v),
        Action::ClearDefaultModel => clear_default_model(app),
        Action::SetForkSecondaryModel(v) => set_fork_secondary_model(app, v),
        Action::ClearForkSecondaryModel => clear_fork_secondary_model(app),
        Action::SetMaxThoughtsWidth(v) => set_max_thoughts_width(app, v),
        Action::SetShowTips(v) => set_show_tips(app, v),
        Action::SetAutoUpdate(v) => set_auto_update(app, v),
        Action::SetDisplayRefreshAutoCadence(v) => set_display_refresh_auto_cadence(app, v),
        Action::PreviewTheme(v) => preview_theme(app, v),
        Action::PreviewAutoDarkTheme(v) => preview_auto_dark_theme(app, v),
        Action::PreviewAutoLightTheme(v) => preview_auto_light_theme(app, v),
        Action::OpenSettings => dispatch_open_settings(app, None),
        Action::OpenCommandPalette => dispatch_open_command_palette(app),
        Action::OpenResetConfirm { key } => dispatch_open_reset_confirm(app, key),
        Action::ConfirmResetSetting { choice } => dispatch_confirm_reset_setting(app, choice),
        Action::DumpInputLog => dispatch_dump_input_log(app),
        Action::PermissionSelect(option_id) => dispatch_permission_select(app, option_id),
        Action::PermissionFollowup(text) => dispatch_permission_followup(app, text),
        Action::PermissionCancel => dispatch_permission_cancel(app),
        Action::Logout => dispatch_logout(app),
        Action::SwitchAccount => dispatch_switch_account(app),
        Action::CheckSubscription => vec![Effect::CheckSubscription { verify: None }],
        Action::OpenSupergrokUrl => dispatch_open_supergrok_url(app),
        Action::OpenUrl(url) => {
            if url.starts_with("file://") {
                let opened = url::Url::parse(&url)
                    .ok()
                    .and_then(|u| u.to_file_path().ok())
                    .is_some_and(|path| crate::app::link_opener::open_path(&path));
                app.show_toast(if opened {
                    "Opening in default app\u{2026}"
                } else {
                    "Could not open file"
                });
            } else {
                open_url_or_show(app, &url);
            }
            vec![]
        }
        Action::OpenLink(target) => {
            use crate::render::osc8::LinkTarget;
            match crate::render::osc8::resolve_link_open_target(&target) {
                Some(LinkTarget::File(path)) => {
                    let opened = crate::app::link_opener::open_path(&path);
                    app.show_toast(if opened {
                        "Opening in default app\u{2026}"
                    } else {
                        "Could not open file"
                    });
                }
                Some(LinkTarget::Url(url)) => {
                    crate::app::link_opener::open_url(&url);
                }
                None => {}
            }
            vec![]
        }
        Action::OpenNextLink => {
            with_active_agent(app, |agent| agent.cycle_highlighted_link(true));
            vec![]
        }
        Action::OpenPrevLink => {
            with_active_agent(app, |agent| agent.cycle_highlighted_link(false));
            vec![]
        }
        Action::Login => dispatch_login(app),
        Action::CancelLogin => dispatch_cancel_login(app),
        Action::SubmitAuthCode(code) => dispatch_submit_auth_code(app, code),
        Action::CopyAuthUrl => {
            dispatch_copy_auth_url(app, crate::clipboard::SystemClipboard::try_set)
        }
        Action::ShowRawAuthUrl => {
            app.auth_show_raw_url = true;
            vec![]
        }
        Action::HideRawAuthUrl => {
            app.auth_show_raw_url = false;
            vec![]
        }
        Action::TrustFolder => dispatch_trust_folder(app),
        Action::DeleteSession {
            source,
            session_id,
            cwd,
        } => {
            if !matches!(source.as_str(), "local" | "remote" | "both")
                || !session_picker_entry_matches(app, &source, &session_id)
            {
                return vec![];
            }
            app.show_toast("Deleting session\u{2026}");
            vec![Effect::DeleteSession {
                source,
                session_id,
                cwd,
                after: crate::app::actions::AfterSessionDelete::Stay,
            }]
        }
        Action::NewSessionAnswered {
            worktree,
            persist_mode,
        } => {
            let mut effects = if worktree {
                dispatch_new_worktree_session(app, None, None, None, None, None, None)
            } else {
                dispatch_new_session_inner(app, None)
            };
            apply_persist_worktree_mode(
                &mut app.new_session_worktree_mode,
                &mut effects,
                persist_mode,
                "new_session_worktree_mode",
            );
            effects
        }
        Action::AgentTypeMismatchAnswered {
            start_new,
            model_id,
            effort,
        } => dispatch_agent_type_mismatch_answered(app, start_new, model_id, effort),
        Action::EditPromptExternal => super::external_editor::dispatch_edit_prompt_external(app),
        Action::TaskComplete(result) => dispatch_task_result(result, app),
    };
    restore_stash_where_the_draft_was_consumed(app);
    sync_sleep_inhibitor(app);
    effects
}
/// Restores the stashed draft once the agent reports its draft was consumed
/// (a stranded flag restores on a later dispatch).
fn restore_stash_where_the_draft_was_consumed(app: &mut AppView) {
    let ActiveView::Agent(id) = app.active_view else {
        return;
    };
    let Some(agent) = app.agents.get_mut(&id) else {
        return;
    };
    if agent.take_draft_consumed() {
        agent.auto_restore_stash_after_send();
    }
}
