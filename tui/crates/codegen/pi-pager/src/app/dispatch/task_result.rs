//! Async task-result application: routes task results into state.
use super::auth::{
    ensure_login_method, handle_auth_complete, handle_auth_url_ready, 
};
use super::billing::{
    PAYWALL_AUTO_CHECK_TIMEOUT, apply_auto_topup, handle_billing_fetched,
    handle_check_subscription_complete, handle_credit_limit_recheck_complete,
    handle_gate_refreshed, handle_gate_verify_timeout,
};
use super::ctx::{ get_active_agent_mut};
use super::prompt::{
    handle_prompt_response,
};
use super::router::{dispatch, };
use super::session::session_list::{
    handle_session_list_failed, handle_session_list_loaded,
};
use super::session::lifecycle::{
    dispatch_exit_session, handle_session_created, handle_session_failed,
    handle_switch_model_complete, handle_worktree_session_failed,
};
use super::session::load::{
    handle_session_load_failed,
    handle_session_loaded, 
    remove_session_from_pickers,
};
use super::session::modal::remove_agent_and_cleanup;
use super::settings::ui::apply_setting_rollback;
use super::status::{
    scrub_error_for_toast,
};
use crate::app::actions::{
    ClipboardPasteCompletion, ClipboardPasteContext, ClipboardPasteFailure, ClipboardPasteTarget,
    Effect, ProbedAttachment, 
    TaskResult,
};
use crate::app::agent_view::AgentDeferredSend;
use crate::app::app_view::{ActiveView, AppView, AuthState};
use crate::scrollback::block::RenderBlock;
use agent_client_protocol as acp;
pub(super) fn unregister_session_effect(session_id: Option<acp::SessionId>) -> Vec<Effect> {
    session_id
        .map(|sid| Effect::UnregisterActiveSession { session_id: sid })
        .into_iter()
        .collect()
}
pub(super) fn unregister_all_active_sessions(app: &AppView) -> Vec<Effect> {
    app.agents
        .values()
        .filter_map(|a| {
            a.session
                .session_id
                .as_ref()
                .map(|sid| Effect::UnregisterActiveSession {
                    session_id: sid.clone(),
                })
        })
        .collect()
}
pub(super) const X11_PRIMARY_PASTE_HINT: &str = "Try Shift+Insert to paste selected text";
fn show_clipboard_toast(target: &ClipboardPasteTarget, message: &str, app: &mut AppView) {
    match target {
        ClipboardPasteTarget::AgentPrompt { agent_id, .. } => {
            if let Some(agent) = app.agents.get_mut(agent_id) {
                agent.show_toast(message);
            }
        }
    }
}
pub(super) fn maybe_show_x11_primary_paste_hint(
    eligible: bool,
    completion: ClipboardPasteCompletion,
    target: &ClipboardPasteTarget,
    app: &mut AppView,
) {
    if !eligible || completion != ClipboardPasteCompletion::FullMiss {
        return;
    }
    show_clipboard_toast(target, X11_PRIMARY_PASTE_HINT, app);
}
/// Whether a completed clipboard probe should fall through to the `grok wrap`
/// host-image request. A clean `FullMiss` always qualifies; a remote read
/// *error* (`AttachmentRead`) also qualifies because inside `grok wrap` the
/// authoritative pasteboard is the local host's, not the (absent) remote one, so
/// the error is recoverable over the wrap OSC path. Every other failure
/// (`TextRead`, `TargetInsertion`, `AlreadyReported`) is a real dead end and
/// must keep toasting. The request itself still self-gates on
/// `osc52_sink_active()`, so this is inert outside `grok wrap`.
pub(super) fn wrap_host_image_request_eligible(completion: ClipboardPasteCompletion) -> bool {
    matches!(
        completion,
        ClipboardPasteCompletion::FullMiss
            | ClipboardPasteCompletion::Failed(ClipboardPasteFailure::AttachmentRead)
    )
}
pub(super) fn show_clipboard_failure(
    target: &ClipboardPasteTarget,
    failure: ClipboardPasteFailure,
    app: &mut AppView,
) {
    let message = match failure {
        ClipboardPasteFailure::AlreadyReported => return,
        ClipboardPasteFailure::TextRead => "Couldn't read clipboard text",
        ClipboardPasteFailure::AttachmentRead => "Couldn't read clipboard contents",
        ClipboardPasteFailure::TargetInsertion => "Couldn't paste clipboard contents",
    };
    show_clipboard_toast(target, message, app);
}
fn apply_clipboard_paste_result(
    ctx: ClipboardPasteContext,
    image: ProbedAttachment,
    file_urls: Option<String>,
    app: &mut AppView,
) -> ClipboardPasteCompletion {
    match ctx.target.clone() {
        ClipboardPasteTarget::AgentPrompt { agent_id, .. } => app
            .agents
            .get_mut(&agent_id)
            .map_or(ClipboardPasteCompletion::Dropped, |agent| {
                agent.complete_clipboard_attachment_paste(ctx, image, file_urls)
            }),
    }
}
fn drain_clipboard_target(target: &ClipboardPasteTarget, app: &mut AppView) -> Vec<Effect> {
    match target {
        ClipboardPasteTarget::AgentPrompt { agent_id, .. } => {
            let is_active = app.active_view == ActiveView::Agent(*agent_id);
            let Some(agent) = app.agents.get_mut(agent_id) else {
                return vec![];
            };
            let resend = agent.take_deferred_send_after_paste();
            let action = resend
                .filter(|kind| is_active || matches!(kind, AgentDeferredSend::Stash))
                .and_then(|kind| agent.resume_deferred_send(kind));
            let mut effects = std::mem::take(&mut agent.pending_effects);
            if let Some(action) = action {
                effects.extend(dispatch(action, app));
            }
            effects
        }
    }
}
/// Handle a completed async task result.
pub(super) fn dispatch_task_result(result: TaskResult, app: &mut AppView) -> Vec<Effect> {
    if result.ends_startup() {
        app.finish_startup(pi_telemetry::startup::StartupOutcome::Ok);
    }
    match result {
        TaskResult::SessionCreated {
            agent_id,
            session_id,
            models: new_models,
            scheduler_background_loops,
        } => handle_session_created(
            app,
            agent_id,
            session_id,
            new_models,
            scheduler_background_loops,
        ),
        TaskResult::SessionFailed { agent_id, error } => {
            handle_session_failed(app, agent_id, error)
        }
        TaskResult::WorktreeSessionFailed { agent_id, error } => {
            handle_worktree_session_failed(app, agent_id, error)
        }
        TaskResult::BillingFetched {
            agent_id,
            balance,
            silent,
            subscription_tier,
            autotopup,
        } => handle_billing_fetched(
            app,
            agent_id,
            balance,
            silent,
            subscription_tier,
            autotopup,
        ),
        TaskResult::AppBillingFetched { balance, autotopup } => {
            app.credit_balance = balance;
            apply_auto_topup(&mut app.auto_topup, &autotopup);
            vec![]
        }
        TaskResult::GateRefreshed { settings } => handle_gate_refreshed(app, settings),
        TaskResult::SessionLoaded {
            agent_id,
            session_id,
            models: new_models,
            code_restored,
            restore_summary,
            restore_degree,
            running_prompt_id,
            scheduler_background_loops,
        } => handle_session_loaded(
            app,
            agent_id,
            session_id,
            new_models,
            code_restored,
            restore_summary,
            restore_degree,
            running_prompt_id,
            scheduler_background_loops,
        ),
        TaskResult::SessionLoadFailed {
            agent_id,
            session_id,
            error,
        } => handle_session_load_failed(app, agent_id, session_id, error),
        TaskResult::SessionListLoaded { sessions, seq } => {
            handle_session_list_loaded(app, sessions, seq)
        }
        TaskResult::SessionListFailed { error, seq } => {
            handle_session_list_failed(app, error, seq)
        }
        TaskResult::PromptResponse {
            agent_id,
            result,
            http_status,
            prompt_id,
        } => {
            let effects = handle_prompt_response(app, agent_id, result, http_status, prompt_id);
            app.refresh_status_line_for(agent_id);
            effects
        }
        TaskResult::PreferredModelPersisted { result } => {
            if let Err(err) = result
                && let Some(agent) = get_active_agent_mut(app)
            {
                agent.scrollback.push_block(RenderBlock::system(format!(
                    "Couldn't save preferred model: {err} (still active for this session)"
                )));
            }
            vec![]
        }
        TaskResult::CancelComplete => {
            tracing::trace!("Cancel notification sent successfully");
            vec![]
        }
        TaskResult::SwitchModelComplete {
            agent_id,
            model_id,
            effort,
            result,
            prev_model_id,
        } => handle_switch_model_complete(app, agent_id, model_id, effort, result, prev_model_id),
        TaskResult::ClipboardAttachmentProbed {
            ctx,
            image,
            file_urls,
        } => {
            let is_clipboard_key = ctx.source.is_clipboard_key();
            let primary_hint_eligible = is_clipboard_key
                && !app.screen_mode.is_minimal()
                && crate::clipboard::x11_primary_guidance_available();
            let target = ctx.target.clone();
            let wrap_text = if is_clipboard_key {
                ctx.source.text().map(str::to_owned)
            } else {
                None
            };
            let completion = apply_clipboard_paste_result(ctx, image, file_urls, app);
            let wrap_request_emitted = wrap_host_image_request_eligible(completion)
                && is_clipboard_key
                && crate::wrap_clipboard_image::maybe_request_wrap_host_image(
                    None,
                    wrap_text.as_deref(),
                    None,
                );
            let effects = drain_clipboard_target(&target, app);
            maybe_show_x11_primary_paste_hint(
                primary_hint_eligible && !wrap_request_emitted,
                completion,
                &target,
                app,
            );
            if let ClipboardPasteCompletion::Failed(failure) = completion
                && !wrap_request_emitted
            {
                show_clipboard_failure(&target, failure, app);
            }
            effects
        }
        TaskResult::PromptImagePreviewPrepared => vec![],
        TaskResult::AuthComplete { request_seq, meta } => {
            handle_auth_complete(app, request_seq, meta)
        }
        TaskResult::AuthFailed { request_seq, error } => {
            if let AuthState::Authenticating {
                request_seq: current_seq,
                ..
            } = &app.auth_state
                && *current_seq == request_seq
            {
                app.auth_state = AuthState::Pending { error: Some(error) };
                app.auth_code_input.reset();
            }
            vec![]
        }
        TaskResult::AuthUrlReady {
            request_seq,
            auth_url,
            external,
            mode,
        } => handle_auth_url_ready(app, request_seq, auth_url, external, mode),
        TaskResult::AuthCodeSubmitted { .. } => vec![],
        TaskResult::AuthCancelComplete => vec![],
        TaskResult::RenameSessionComplete { agent_id, title } => {
            if let Some(agent) = app.agents.get_mut(&agent_id) {
                let safe = crate::views::session_title::sanitize_display_text(&title);
                agent
                    .scrollback
                    .push_block(crate::scrollback::block::RenderBlock::system(format!(
                        "Session renamed to \"{safe}\""
                    )));
            }
            vec![]
        }
        TaskResult::RenameSessionFailed { agent_id, error } => {
            if let Some(agent) = app.agents.get_mut(&agent_id) {
                agent
                    .scrollback
                    .push_block(crate::scrollback::block::RenderBlock::system(format!(
                        "Couldn't rename session: {error}"
                    )));
            }
            vec![]
        }
        TaskResult::DeleteSessionComplete {
            source,
            session_id,
            after,
        } => {
            use crate::app::actions::AfterSessionDelete;
            remove_session_from_pickers(
                app,
                &source,
                &session_id,
                after != AfterSessionDelete::Stay,
            );
            if after == AfterSessionDelete::Stay {
                app.show_toast("Session deleted");
                return vec![];
            }
            let sid = acp::SessionId::new(session_id.clone());
            let to_remove: Vec<_> = app
                .agents
                .iter()
                .filter(|(_, agent)| agent.session.session_id.as_ref() == Some(&sid))
                .map(|(id, _)| *id)
                .collect();
            let foreground =
                matches!(app.active_view, ActiveView::Agent(id) if to_remove.contains(&id));
            for id in to_remove {
                remove_agent_and_cleanup(app, id);
            }
            let mut effects = unregister_session_effect(Some(sid));
            if foreground {
                effects.extend(dispatch_exit_session(app));
            }
            app.show_toast("Session deleted");
            effects
        }
        TaskResult::DeleteSessionFailed {
            source,
            session_id,
            error,
        } => {
            tracing::warn!(source, session_id = %session_id, error = %error, "session delete failed");
            app.show_toast(&format!("Couldn't delete session: {error}"));
            vec![]
        }
        TaskResult::StatusLineCommandFinished { id, outcome } => {
            app.on_status_line_command_finished(id, outcome);
            vec![]
        }
        TaskResult::AuthCopyFeedbackTimeout { generation } => {
            if generation == app.auth_clipboard_feedback_generation {
                app.auth_clipboard_delivery = None;
            }
            vec![]
        }
        TaskResult::PaywallCheckTick => {
            let timed_out = app
                .paywall_check_started
                .is_some_and(|t| t.elapsed() >= PAYWALL_AUTO_CHECK_TIMEOUT);
            if !app.has_access() && !timed_out {
                vec![
                    Effect::CheckSubscription { verify: None },
                    Effect::SchedulePaywallCheck,
                ]
            } else {
                vec![]
            }
        }
        TaskResult::CheckSubscriptionComplete { verify, meta } => {
            handle_check_subscription_complete(app, verify, meta)
        }
        TaskResult::GateVerifyTimeout { generation } => handle_gate_verify_timeout(app, generation),
        TaskResult::CreditLimitRecheckComplete { agent_id, meta } => {
            handle_credit_limit_recheck_complete(app, agent_id, meta)
        }
        TaskResult::LogoutComplete => {
            app.auth_state = AuthState::Pending { error: None };
            app.access_gate_shown_logged = false;
            app.announcement_cta_impressions_logged.clear();
            app.gate = None;
            app.pending_gate_verification = None;
            app.last_subscription_check_at = None;
            app.login_method_id = None;
            ensure_login_method(app);
            app.auth_clipboard_delivery = None;
            let effects = dispatch_exit_session(app);
            app.welcome_prompt_focused = false;
            effects
        }
        TaskResult::SettingPersisted { key, value } => {
            tracing::trace!(target: "settings", ?key, ?value, "setting persisted");
            vec![]
        }
        TaskResult::SettingPersistFailed {
            key,
            rollback_value,
            error,
        } => {
            let rollback_effects = apply_setting_rollback(app, key, &rollback_value);
            tracing::warn!(target: "settings", ?key, ?rollback_value, %error, "setting persist failed; rolled back");
            let scrubbed = scrub_error_for_toast(&error);
            app.show_toast(&format!("\u{2717} Could not save {key}: {scrubbed}"));
            rollback_effects
        }
        TaskResult::SettingPersistFailedBestEffort { key, error } => {
            tracing::warn!(
                target: "settings",
                ?key, %error,
                "setting persist failed (best-effort); in-memory state stays at optimistic value",
            );
            let scrubbed = scrub_error_for_toast(&error);
            app.show_toast(&format!("\u{2717} Could not save {key}: {scrubbed}"));
            vec![]
        }
    }
}
