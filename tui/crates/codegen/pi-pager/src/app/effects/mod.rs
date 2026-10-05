#![cfg_attr(rustfmt, rustfmt::skip)]
//! Async effect execution.
//!
//! This module takes [`Effect`] values produced by [`super::dispatch`] and
//! spawns them as async tasks on a [`JoinSet`].  When tasks complete,
//! the event loop converts their output into [`TaskResult`] and feeds it
//! back through dispatch.
mod helpers;
use super::actions;
#[allow(unused_imports)]
use super::{agent, dispatch};
pub(super) use helpers::{
    parse_session_load_running_prompt_id, parse_session_scheduler_background_loops,
};
pub(crate) use helpers::{
    EffectMeta, SessionFlags, is_disk_full_error,
    persist_permission_mode_and_notify, persist_setting, sanitize_user_error,
};
#[cfg(feature = "local-workspace")]
pub(crate) use helpers::reject_non_fs_only_advertised_tools;
use helpers::*;
use std::path::Path;
use agent_client_protocol as acp;
use tokio::task::JoinSet;
use pi_acp_lib::{AcpAgentTx, acp_send};
use pi_telemetry::startup::{self, StartupPhase};
use actions::{
    ClipboardPasteTarget, Effect, ProbedAttachment, 
    SwitchModelError, TaskResult,
};
use actions::PermissionModeKind;
#[cfg(test)]
use actions::PermissionModePersist;
use crate::unified_log as ulog;
use pi_shell::sampling::error::http_status_from_error;
fn apply_permission_mode_override(
    meta: &mut Option<acp::Meta>,
    permission_mode_override: Option<PermissionModeKind>,
) {
    let Some(mode) = permission_mode_override else {
        return;
    };
    let meta = meta.get_or_insert_with(acp::Meta::new);
    meta.insert("yoloMode".into(), serde_json::Value::Bool(mode.is_always_approve()));
    meta.insert("autoMode".into(), serde_json::Value::Bool(mode.is_auto()));
}
pub(crate) fn execute(
    effect: Effect,
    tasks: &mut JoinSet<TaskResult>,
    acp_tx: &AcpAgentTx,
    cwd: &Path,
    session_flags: &SessionFlags,
) -> (bool, EffectMeta) {
    let mut meta = EffectMeta::default();
    match effect {
        Effect::RegisterActiveSession { session_id, cwd } => {
            crate::app::signal_handler::set_current_session_id(Some(session_id.clone()));
            if let Err(e) = pi_active_sessions::register(pi_active_sessions::ActiveSession {
                session_id,
                pid: std::process::id(),
                cwd,
                opened_at: chrono::Utc::now(),
            }) {
                tracing::warn!(?e, "Failed to register active session");
            }
        }
        Effect::UnregisterActiveSession { session_id } => {
            crate::app::signal_handler::set_current_session_id(None);
            unregister_active_session_best_effort(&session_id);
        }
        Effect::Quit => {
            ulog::info("pager quit", None, None);
            return (true, meta);
        }
        Effect::RunStatusLineCommand(run) => {
            tasks
                .spawn(async move {
                    let (id, outcome) = run.execute().await;
                    TaskResult::StatusLineCommandFinished {
                        id,
                        outcome,
                    }
                });
        }
        Effect::ScheduleClearAuthCopyFeedback { generation } => {
            tasks
                .spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                    TaskResult::AuthCopyFeedbackTimeout {
                        generation,
                    }
                });
        }
        Effect::Logout => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    send_logout(&tx).await;
                    TaskResult::LogoutComplete
                });
        }
        Effect::CancelAuth { request_seq } => {
            let tx = acp_tx.clone();
            tasks.spawn(async move { send_auth_cancel(&tx, request_seq).await });
        }
        Effect::CheckSubscription { verify } => {
            let tx = acp_tx.clone();
            tasks.spawn(async move { send_check_subscription(&tx, verify).await });
        }
        Effect::CreditLimitRecheck { agent_id } => {
            let tx = acp_tx.clone();
            tasks.spawn(async move { send_credit_limit_recheck(&tx, agent_id).await });
        }
        Effect::SchedulePaywallCheck => {
            tasks
                .spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                    TaskResult::PaywallCheckTick
                });
        }
        Effect::ScheduleGateVerifyTimeout { generation } => {
            tasks
                .spawn(async move {
                    tokio::time::sleep(crate::app::subscription::GATE_VERIFY_TIMEOUT)
                        .await;
                    TaskResult::GateVerifyTimeout {
                        generation,
                    }
                });
        }
        Effect::SwitchAccount { request_seq, method_id, use_oauth } => {
            let tx = acp_tx.clone();
            let abort_handle = tasks
                .spawn(async move {
                    send_logout(&tx).await;
                    send_authenticate(&tx, request_seq, method_id, use_oauth, false)
                        .await
                });
            meta.auth_abort_handle = Some((request_seq, abort_handle));
        }
        Effect::CreateSession {
            agent_id,
            cwd: session_cwd,
            model_id,
            permission_mode_override,
            preferred_session_id,
            chat_kind,
        } => {
            let tx = acp_tx.clone();
            let compat = pi_tools::types::compat::CompatConfig::default();
            let mcp_servers = pi_shell::util::config::load_mcp_servers(
                &session_cwd,
                &compat,
            );
            let mcp_count = mcp_servers.len();
            #[allow(unused_mut)]
            let mut meta = session_flags.to_meta();
            apply_permission_mode_override(&mut meta, permission_mode_override);
            let is_chat_path = chat_kind || session_flags.chat_mode;
            finalize_chat_session_meta(&mut meta, is_chat_path, session_flags);
            if let Some(ref mid) = model_id {
                meta.get_or_insert_with(acp::Meta::new)
                    .insert("modelId".into(), serde_json::json!(mid.0));
            }
            if let Some(ref sid) = preferred_session_id {
                meta.get_or_insert_with(acp::Meta::new)
                    .insert("sessionId".into(), serde_json::json!(sid));
            }
            if is_chat_path {
                scrub_chat_workspace_bind_meta(&mut meta);
            }
            let preferred_for_preflight = preferred_session_id.clone();
            tasks
                .spawn(async move {
                    if let Some(ref sid) = preferred_for_preflight {
                        let session_cwd_str = session_cwd.to_string_lossy();
                        if let Err(e) = crate::app::session_startup::ensure_session_id_available(
                            sid,
                            &session_cwd_str,
                        ) {
                            return TaskResult::SessionFailed {
                                agent_id,
                                error: sanitize_user_error(&e.to_string()),
                            };
                        }
                    }
                    let _phase = startup::phase_scope(StartupPhase::SessionCreate);
                    ulog::info(
                        "session.create.start",
                        None,
                        Some(serde_json::json!({"mcp_server_count": mcp_count})),
                    );
                    let create_start = std::time::Instant::now();
                    let result = helpers::acp_send_bounded(
                            acp::NewSessionRequest::new(session_cwd.clone())
                                .mcp_servers(mcp_servers)
                                .meta(meta),
                            &tx,
                            "Session creation",
                        )
                        .await;
                    let create_elapsed_ms = create_start.elapsed().as_millis() as u64;
                    match result {
                        Ok(resp) => {
                            ulog::info(
                                "session.create.done",
                                Some(&resp.session_id.0),
                                Some(
                                    serde_json::json!({
                                "elapsed_ms": create_elapsed_ms,
                                "mcp_server_count": mcp_count,
                            }),
                                ),
                            );
                            TaskResult::SessionCreated {
                                agent_id,
                                session_id: resp.session_id,
                                models: parse_session_response_models(
                                    resp.models,
                                    resp.config_options.as_deref(),
                                    resp.meta.as_ref(),
                                ),
                                scheduler_background_loops: parse_session_scheduler_background_loops(
                                    resp.meta.as_ref(),
                                ),
                            }
                        }
                        Err(e) => {
                            let error = e.to_string();
                            ulog::error(
                                "session.create.failed",
                                None,
                                Some(
                                    serde_json::json!({
                                "elapsed_ms": create_elapsed_ms,
                                "error": &error,
                            }),
                                ),
                            );
                            TaskResult::SessionFailed {
                                agent_id,
                                error: sanitize_user_error(&error),
                            }
                        }
                    }
                });
        }
        Effect::CreateWorktreeSession {
            agent_id,
            ..
        } => {
            tasks.spawn(async move {
                TaskResult::WorktreeSessionFailed {
                    agent_id,
                    error: "Git worktree sessions are not supported in standard ACP mode".to_string(),
                }
            });
        }
        Effect::LoadSession { agent_id, session_id, session_cwd, chat_kind } => {
            let tx = acp_tx.clone();
            let mut meta = session_flags.to_meta();
            let is_chat_path = chat_kind || session_flags.chat_mode;
            finalize_chat_session_meta(&mut meta, is_chat_path, session_flags);
            let cwd = session_cwd.unwrap_or_else(|| cwd.to_path_buf());
            let mcp_started = std::time::Instant::now();
            let mcp_servers = pi_shell::util::config::load_mcp_servers(
                &cwd,
                &pi_tools::types::compat::CompatConfig::default(),
            );
            tracing::info!(
                elapsed_ms = mcp_started.elapsed().as_millis() as u64,
                server_count = mcp_servers.len(),
                "load_session: mcp server discovery"
            );
            let acp_session_id = acp::SessionId::new(session_id);
            tasks
                .spawn(async move {
                    let _phase = startup::phase_scope(StartupPhase::SessionCreate);
                    ulog::info("session.load.start", Some(&acp_session_id.0), None);
                    let load_started = std::time::Instant::now();
                    let result = helpers::acp_send_bounded(
                            acp::LoadSessionRequest::new(
                                    acp_session_id.clone(),
                                    cwd.clone(),
                                )
                                .mcp_servers(mcp_servers.clone())
                                .meta(meta.clone()),
                            &tx,
                            "Session loading",
                        )
                        .await;
                    let load_elapsed_ms = load_started.elapsed().as_millis() as u64;
                    tracing::info!(
                    session_id = %acp_session_id.0,
                    elapsed_ms = load_elapsed_ms,
                    ok = result.is_ok(),
                    "load_session: acp load_session completed"
                );
                    match result {
                        Ok(resp) => {
                            ulog::info(
                                "session.load.done",
                                Some(&acp_session_id.0),
                                Some(serde_json::json!({"elapsed_ms": load_elapsed_ms})),
                            );
                            let (code_restored, restore_summary, restore_degree) = parse_session_load_restore_meta(
                                resp.meta.as_ref(),
                            );
                            let running_prompt_id = parse_session_load_running_prompt_id(
                                resp.meta.as_ref(),
                            );
                            TaskResult::SessionLoaded {
                                agent_id,
                                session_id: acp_session_id,
                                models: parse_session_response_models(
                                    resp.models,
                                    resp.config_options.as_deref(),
                                    resp.meta.as_ref(),
                                ),
                                code_restored,
                                restore_summary,
                                restore_degree,
                                running_prompt_id,
                                scheduler_background_loops: parse_session_scheduler_background_loops(
                                    resp.meta.as_ref(),
                                ),
                            }
                        }
                        Err(e) => {
                            let error = e.to_string();
                            ulog::error(
                                "session.load.failed",
                                Some(&acp_session_id.0),
                                Some(
                                    serde_json::json!({"elapsed_ms": load_elapsed_ms, "error": &error}),
                                ),
                            );
                            TaskResult::SessionLoadFailed {
                                agent_id,
                                session_id: acp_session_id,
                                error: sanitize_user_error(&error),
                            }
                        }
                    }
                });
        }
        Effect::FetchSessionList { seq } => {
            let tx = acp_tx.clone();
            let cwd = cwd.to_path_buf();
            tasks
                .spawn(async move {
                    let request = acp::ListSessionsRequest::default().cwd(cwd.clone());
                    let result = acp_send(request, &tx).await;
                    match result {
                        Ok(resp) => TaskResult::SessionListLoaded {
                            sessions: session_picker_entries_from_acp(&resp),
                            seq,
                        },
                        Err(e) => TaskResult::SessionListFailed {
                            error: sanitize_user_error(&format!("{e}")),
                            seq,
                        },
                    }
                });
        }
        Effect::SendPrompt {
            agent_id,
            session_id,
            text,
            prompt_id,
            skill_token_ranges,
        } => {
            let tx = acp_tx.clone();
            let screen_mode = session_flags.screen_mode_label;
            let is_api_key_auth = session_flags.is_api_key_auth;
            tasks
                .spawn(async move {
                    ulog::info(
                        "prompt.acp_send.start",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "kind": "text",
                        "len": text.len(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    let send_start = std::time::Instant::now();
                    let prompt = vec![plain_prompt_content_block(text, &skill_token_ranges)];
                    let req = acp::PromptRequest::new(session_id.clone(), prompt)
                        .meta(
                            prompt_request_meta(&prompt_id, screen_mode)
                                .as_object()
                                .cloned(),
                        );
                    let result = acp_send(req, &tx).await;
                    let send_elapsed_ms = send_start.elapsed().as_millis() as u64;
                    ulog::info(
                        "prompt.acp_send.done",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "kind": "text",
                        "elapsed_ms": send_elapsed_ms,
                        "ok": result.is_ok(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    log_prompt_result(&session_id, &result);
                    let http_status = result
                        .as_ref()
                        .err()
                        .and_then(http_status_from_error);
                    TaskResult::PromptResponse {
                        agent_id,
                        result: result
                            .map_err(|e| format_acp_error(&e, is_api_key_auth)),
                        http_status,
                        prompt_id: Some(prompt_id),
                    }
                });
        }
        Effect::SendPromptBlocks { agent_id, session_id, blocks, prompt_id } => {
            let tx = acp_tx.clone();
            let screen_mode = session_flags.screen_mode_label;
            let is_api_key_auth = session_flags.is_api_key_auth;
            tasks
                .spawn(async move {
                    ulog::info(
                        "prompt.acp_send.start",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "kind": "blocks",
                        "block_count": blocks.len(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    let send_start = std::time::Instant::now();
                    let meta = prompt_request_meta(&prompt_id, screen_mode);
                    let req = acp::PromptRequest::new(session_id.clone(), blocks)
                        .meta(meta.as_object().cloned());
                    let result = acp_send(req, &tx).await;
                    let send_elapsed_ms = send_start.elapsed().as_millis() as u64;
                    ulog::info(
                        "prompt.acp_send.done",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "kind": "blocks",
                        "elapsed_ms": send_elapsed_ms,
                        "ok": result.is_ok(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    log_prompt_result(&session_id, &result);
                    let http_status = result
                        .as_ref()
                        .err()
                        .and_then(http_status_from_error);
                    TaskResult::PromptResponse {
                        agent_id,
                        result: result
                            .map_err(|e| format_acp_error(&e, is_api_key_auth)),
                        http_status,
                        prompt_id: Some(prompt_id),
                    }
                });
        }
        Effect::SendBashCommand { agent_id, session_id, command, prompt_id } => {
            let tx = acp_tx.clone();
            let screen_mode = session_flags.screen_mode_label;
            let is_api_key_auth = session_flags.is_api_key_auth;
            tasks
                .spawn(async move {
                    use pi_shell::extensions::prompt_meta::PromptBlockMeta;
                    ulog::info(
                        "prompt.acp_send.start",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "kind": "bash",
                        "len": command.len(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    let send_start = std::time::Instant::now();
                    let meta = PromptBlockMeta::bash(&command);
                    let prompt = vec![acp::ContentBlock::Text(
                    acp::TextContent::new(command).meta(
                        serde_json::to_value(&meta)
                            .expect("PromptBlockMeta serializes")
                            .as_object()
                            .cloned(),
                    ),
                )];
                    let req = acp::PromptRequest::new(session_id.clone(), prompt)
                        .meta(
                            prompt_request_meta(&prompt_id, screen_mode)
                                .as_object()
                                .cloned(),
                        );
                    let result = acp_send(req, &tx).await;
                    let send_elapsed_ms = send_start.elapsed().as_millis() as u64;
                    ulog::info(
                        "prompt.acp_send.done",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "kind": "bash",
                        "elapsed_ms": send_elapsed_ms,
                        "ok": result.is_ok(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    log_prompt_result(&session_id, &result);
                    let http_status = result
                        .as_ref()
                        .err()
                        .and_then(http_status_from_error);
                    TaskResult::PromptResponse {
                        agent_id,
                        result: result
                            .map_err(|e| format_acp_error(&e, is_api_key_auth)),
                        http_status,
                        prompt_id: Some(prompt_id),
                    }
                });
        }
        Effect::CancelTurn {
            session_id,
            trigger,
            rewind_prompt_id,
        } => {
            let tx = acp_tx.clone();
            let trigger_str = trigger.map(|t| t.as_wire_str());
            tasks
                .spawn(async move {
                    ulog::info(
                        "cancel.acp_send.start",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "trigger": trigger_str,
                        "rewind_if_no_output": rewind_prompt_id.is_some(),
                        "rewind_prompt_id": rewind_prompt_id.as_deref(),
                    }),
                        ),
                    );
                    let send_start = std::time::Instant::now();
                    let mut meta = serde_json::json!({});
                    if let Some(t) = trigger_str {
                        meta[crate::app::turn_completion::CANCEL_TRIGGER_KEY] = t.into();
                    }
                    if let Some(pid) = rewind_prompt_id {
                        meta["rewindIfNoOutput"] = true.into();
                        meta["rewindIfPristine"] = true.into();
                        meta["promptId"] = pid.into();
                    }
                    let req = acp::CancelNotification::new(session_id.clone())
                        .meta(meta.as_object().cloned());
                    let result = acp_send(req, &tx).await;
                    ulog::info(
                        "cancel.acp_send.done",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "ok": result.is_ok(),
                        "elapsed_ms": send_start.elapsed().as_millis() as u64,
                    }),
                        ),
                    );
                    if let Err(e) = result {
                        tracing::warn!("Failed to send cancel notification: {e}");
                    }
                    TaskResult::CancelComplete
                });
        }
        Effect::SetSessionMode { session_id, mode_id } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    let req = acp::SetSessionModeRequest::new(session_id, mode_id);
                    if let Err(e) = acp_send(req, &tx).await {
                        tracing::warn!("Failed to set session mode: {e}");
                    }
                    TaskResult::CancelComplete
                });
        }
        Effect::SwitchModel {
            agent_id,
            session_id,
            model_id,
            effort,
            prev_model_id,
            config_option_id,
        } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    let meta = effort
                        .map(|eff| {
                            use pi_shell::sampling::types::{
                                REASONING_EFFORT_META_KEY, reasoning_effort_meta_value,
                            };
                            let mut m = acp::Meta::new();
                            m.insert(
                                REASONING_EFFORT_META_KEY.to_string(),
                                reasoning_effort_meta_value(eff),
                            );
                            m
                        });
                    // Standard ACP: the agent advertised a `model` Session Config Option →
                    // select the value through `session/set_config_option`. Otherwise fall
                    // back to the unstable `session/set_model`.
                    let sent = match config_option_id {
                        Some(config_id) => {
                            let req = acp::SetSessionConfigOptionRequest::new(
                                    session_id,
                                    config_id,
                                    &*model_id.0,
                                )
                                .meta(meta);
                            acp_send(req, &tx).await.map(|_| ())
                        }
                        None => {
                            let req = acp::SetSessionModelRequest::new(
                                    session_id,
                                    model_id.clone(),
                                )
                                .meta(meta);
                            acp_send(req, &tx).await.map(|_| ())
                        }
                    };
                    let result = sent
                        .map_err(|e| {
                            use pi_shell::agent::config::ModelSwitchIncompatibleAgentError;
                            if let Some(typed) = ModelSwitchIncompatibleAgentError::from_acp_error(
                                &e,
                            ) {
                                SwitchModelError::IncompatibleAgent {
                                    error: typed,
                                }
                            } else {
                                SwitchModelError::Other(sanitize_user_error(&e.to_string()))
                            }
                        });
                    TaskResult::SwitchModelComplete {
                        agent_id,
                        model_id,
                        effort,
                        result,
                        prev_model_id,
                    }
                });
        }
        Effect::ProbeClipboardAttachment { ctx, change_count } => {
            tasks
                .spawn(async move {
                    let probe_target = ctx.target.clone();
                    let probe_text = ctx.source.text().map(str::to_owned);
                    let probe_bracketed = ctx.source.is_bracketed();
                    let probe = tokio::task::spawn_blocking(move || {
                        if change_count.is_some()
                            && crate::clipboard::clipboard_change_count() != change_count
                        {
                            return (ProbedAttachment::ProbeDropped, None);
                        }
                        if probe_bracketed
                            && crate::terminal::terminal_context()
                                .brand
                                .delivers_ime_as_bracketed_paste()
                        {
                            match crate::clipboard::bracketed_payload_came_from_clipboard_result(
                                probe_text.as_deref().unwrap_or(""),
                            ) {
                                Ok(true) => {}
                                Ok(false) => return (ProbedAttachment::ProbeDropped, None),
                                Err(_) => return (ProbedAttachment::ProbeFailed, None),
                            }
                        }
                        let (image_data, file_urls) = match crate::clipboard::system_clipboard_probe_attachments(
                            probe_text.as_deref(),
                        ) {
                            Ok(probe) => probe,
                            Err(_) => return (ProbedAttachment::ProbeFailed, None),
                        };
                        let image = match image_data {
                            Some(data) => {
                                let mut pasted = crate::prompt_images::from_clipboard_data(
                                    &data,
                                );
                                pasted.prepare_preview_blocking();
                                match &probe_target {
                                    ClipboardPasteTarget::AgentPrompt {
                                        images_dir: Some(dir),
                                        ..
                                    } => {
                                        match crate::prompt_images::persist_to_session(
                                            &mut pasted,
                                            dir,
                                        ) {
                                            Ok(()) => ProbedAttachment::Image(pasted),
                                            Err(e) => ProbedAttachment::PersistFailed(e.to_string()),
                                        }
                                    }
                                    ClipboardPasteTarget::AgentPrompt {
                                        images_dir: None,
                                        ..
                                    } => ProbedAttachment::Image(pasted),
                                }
                            }
                            None => ProbedAttachment::NoRaster,
                        };
                        (image, file_urls)
                    });
                    let (image, file_urls) = match tokio::time::timeout(
                            std::time::Duration::from_secs(CLIPBOARD_PROBE_TIMEOUT_SECS),
                            probe,
                        )
                        .await
                    {
                        Ok(Ok(pair)) => pair,
                        Ok(Err(e)) => {
                            tracing::warn!(error = %e, "clipboard attachment probe task failed");
                            (ProbedAttachment::ProbeFailed, None)
                        }
                        Err(_elapsed) => {
                            tracing::warn!("clipboard attachment probe timed out");
                            (ProbedAttachment::ProbeFailed, None)
                        }
                    };
                    TaskResult::ClipboardAttachmentProbed {
                        ctx,
                        image,
                        file_urls,
                    }
                });
        }
        Effect::PreparePromptImagePreview { preparation } => {
            tasks
                .spawn(async move {
                    let preview = preparation.preview();
                    if tokio::task::spawn_blocking(move || preparation.run())
                        .await
                        .is_err()
                    {
                        preview.mark_failed();
                    }
                    TaskResult::PromptImagePreviewPrepared
                });
        }
        Effect::PersistWorktreeMode { mode, config_key } => {
            debug_assert!(
                config_key == "fork_worktree_mode" || config_key == "new_session_worktree_mode",
                "unexpected worktree config_key"
            );
            persist_hint(tasks, config_key, mode.as_config_str(), "worktree mode");
        }
        Effect::PersistPreferredModel { model_id, reasoning_effort } => {
            let model_id_str = model_id.0.to_string();
            tasks
                .spawn(async move {
                    let result = pi_shell::util::config::persist_models_default(
                            Some(model_id_str),
                            reasoning_effort,
                        )
                        .await
                        .map_err(|e| e.to_string());
                    if let Err(ref e) = result {
                        tracing::warn!("failed to save default model preference: {e}");
                    }
                    TaskResult::PreferredModelPersisted {
                        result,
                    }
                });
        }
        Effect::PersistPermissionMode { canonical, session_id, persist } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(
                    persist_permission_mode_and_notify(
                        canonical,
                        session_id,
                        persist,
                        tx,
                    ),
                );
        }
        Effect::PersistSetting { key, value, rollback_value } => {
            tasks
                .spawn(async move {
                    match persist_setting(key, value.clone()).await {
                        Ok(()) => {
                            TaskResult::SettingPersisted {
                                key,
                                value,
                            }
                        }
                        Err(error) => {
                            TaskResult::SettingPersistFailed {
                                key,
                                rollback_value,
                                error,
                            }
                        }
                    }
                });
        }
        Effect::Authenticate {
            request_seq,
            method_id,
            use_oauth,
            force_interactive,
        } => {
            let tx = acp_tx.clone();
            let abort_handle = tasks
                .spawn(async move {
                    send_authenticate(
                            &tx,
                            request_seq,
                            method_id,
                            use_oauth,
                            force_interactive,
                        )
                        .await
                });
            meta.auth_abort_handle = Some((request_seq, abort_handle));
        }
        Effect::PollAuthUrl { request_seq } => {
            let abort_handle = tasks.spawn(async move {
                TaskResult::AuthUrlReady {
                    request_seq,
                    auth_url: None,
                    external: false,
                    mode: None,
                }
            });
            meta.auth_url_poll_handle = Some((request_seq, abort_handle));
        }
        Effect::SubmitAuthCode { request_seq, .. } => {
            tasks.spawn(async move {
                TaskResult::AuthCodeSubmitted {
                    request_seq,
                }
            });
        }
        Effect::DeleteSession {
            source,
            session_id,
            cwd,
            after,
        } => {
            let tx = acp_tx.clone();
            tasks.spawn(async move {
                let payload = serde_json::json!({
                    "sessionId": session_id.clone(),
                    "cwd": cwd.clone(),
                    "source": source.clone(),
                });
                let ext = match serde_json::value::to_raw_value(&payload) {
                    Ok(raw) => acp::ExtRequest::new("pi/session/delete", raw.into()),
                    Err(error) => {
                        return TaskResult::DeleteSessionFailed {
                            source,
                            session_id,
                            error: sanitize_user_error(&error.to_string()),
                        };
                    }
                };
                match acp_send(ext, &tx).await {
                    Ok(_) => TaskResult::DeleteSessionComplete {
                        source,
                        session_id,
                        after,
                    },
                    Err(error) => TaskResult::DeleteSessionFailed {
                        source,
                        session_id,
                        error: sanitize_user_error(&error.to_string()),
                    },
                }
            });
        }
        Effect::FetchBilling { agent_id, silent } => {
            tasks.spawn(async move {
                TaskResult::BillingFetched {
                    agent_id,
                    balance: None,
                    silent,
                    subscription_tier: None,
                    autotopup: crate::views::credit_bar::AutoTopupFetch::Cleared,
                }
            });
        }
        Effect::RefreshGate => {
            tasks
                .spawn(async move {
                    let settings = tokio::task::spawn_blocking(|| {
                            if !pi_shell::util::config::resolve_remote_fetch_enabled() {
                                return None;
                            }
                            let grok_home = pi_shell::util::grok_home::grok_home();
                            let store = pi_shell::auth::read_auth_json(
                                    &grok_home.join("auth.json"),
                                )
                                .ok()?;
                            let scope = pi_shell::auth::GrokComConfig::default()
                                .auth_scope();
                            let auth = pi_shell::auth::lookup_auth(
                                &store,
                                &scope,
                            )?;
                            let proxy_base = std::env::var(
                                    "GROK_CLI_CHAT_PROXY_BASE_URL",
                                )
                                .unwrap_or_else(|_| {
                                    pi_shell::agent::config::CLI_CHAT_PROXY_BASE_URL_DEFAULT
                                        .to_owned()
                                });
                            pi_shell::remote::fetch_settings_blocking(
                                    &proxy_base,
                                    &auth,
                                    None,
                                )
                                .into_option()
                        })
                        .await
                        .ok()
                        .flatten();
                    TaskResult::GateRefreshed {
                        settings,
                    }
                });
        }
        Effect::FetchAppBilling => {
            tasks.spawn(async move {
                TaskResult::AppBillingFetched {
                    balance: None,
                    autotopup: crate::views::credit_bar::AutoTopupFetch::Cleared,
                }
            });
        }
    }
    (false, meta)
}

/// Build the single text content block for a plain `Effect::SendPrompt`.
///
/// Non-empty `skill_token_ranges` are stamped into the block `_meta` as
/// `skillTokenRanges: [[start, end], …]` so session replay restyles the echo
/// exactly like the composer highlighted it at submit time. Contract: the
/// offsets index this block's `text`, which is displayed verbatim — this
/// producer never combines them with a `displayText` override, and the
/// tracker ignores them when one is present. Empty ranges keep `meta: None`
/// — the legacy wire shape stays byte-identical. Extracted from the spawn
/// for testability.
fn plain_prompt_content_block(
    text: String,
    skill_token_ranges: &[std::ops::Range<usize>],
) -> acp::ContentBlock {
    let meta = if skill_token_ranges.is_empty() {
        None
    } else {
        let ranges: Vec<serde_json::Value> = skill_token_ranges
            .iter()
            .map(|r| serde_json::json!([r.start, r.end]))
            .collect();
        let mut map = acp::Meta::new();
        map.insert(
            crate::acp::meta::user_prompt_meta::SKILL_TOKEN_RANGES.into(),
            serde_json::Value::Array(ranges),
        );
        Some(map)
    };
    acp::ContentBlock::Text(acp::TextContent::new(text).meta(meta))
}
/// Build the `PromptRequest._meta` payload: `promptId` for notification /
/// response correlation, plus `screenMode` (`fullscreen` | `inline` |
/// `minimal`; headless stamps `"headless"` in its own path) so the shell can
/// attribute `prompt_submitted` telemetry to minimal vs. regular usage.
/// `screen_mode` is `None` only under `SessionFlags::default()` (tests); the
/// key is omitted then, keeping the legacy wire shape byte-identical.
/// Extracted from the spawns for testability.
fn prompt_request_meta(
    prompt_id: &str,
    screen_mode: Option<&'static str>,
) -> serde_json::Value {
    let mut map = serde_json::Map::new();
    map.insert("promptId".into(), serde_json::Value::String(prompt_id.into()));
    if let Some(mode) = screen_mode {
        map.insert("screenMode".into(), serde_json::Value::String(mode.into()));
    }
    serde_json::Value::Object(map)
}
#[cfg(test)]
mod tests;
