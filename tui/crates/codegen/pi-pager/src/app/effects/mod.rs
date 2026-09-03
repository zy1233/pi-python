#![cfg_attr(rustfmt, rustfmt::skip)]
//! Async effect execution.
//!
//! This module takes [`Effect`] values produced by [`super::dispatch`] and
//! spawns them as async tasks on a [`JoinSet`].  When tasks complete,
//! the event loop converts their output into [`TaskResult`] and feeds it
//! back through dispatch.
mod helpers;
use super::actions;
use super::session_title_resolve::worktree_resume_failure_message;
#[allow(unused_imports)]
use super::{agent, dispatch};
pub use helpers::ConversationsPartial;
pub(super) use helpers::{
    parse_session_load_running_prompt_id, parse_session_scheduler_background_loops,
};
pub(crate) use helpers::{
    EffectMeta, RestoreProgressMsg, SessionFlags, is_disk_full_error,
    persist_permission_mode_and_notify, persist_setting, sanitize_user_error,
};
#[cfg(feature = "local-workspace")]
pub(crate) use helpers::reject_non_fs_only_advertised_tools;
use helpers::*;
use std::path::{Path, PathBuf};
use agent_client_protocol as acp;
use tokio::task::JoinSet;
use pi_acp_lib::{AcpAgentTx, acp_send};
use pi_telemetry::startup::{self, StartupPhase};
use actions::{
    ClipboardPasteTarget, Effect, ProbedAttachment, SubagentKillOutcome,
    SwitchModelError, TaskResult,
};
use actions::PermissionModeKind;
use crate::views::usage_modal::SessionInfoField;
#[cfg(test)]
use actions::PermissionModePersist;
#[cfg(test)]
use agent::AgentId;
use crate::unified_log as ulog;
use pi_shell::sampling::error::http_status_from_error;
use pi_shell::session::{ExtMethodResult, SessionInfoResponse};
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
    progress_tx: &tokio::sync::mpsc::UnboundedSender<RestoreProgressMsg>,
) -> (bool, EffectMeta) {
    let mut meta = EffectMeta::default();
    let effect_is_send_now = matches!(effect, Effect::SendPromptNow { .. });
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
        Effect::SetWorkingDir { path } => {
            if let Err(e) = std::env::set_current_dir(&path) {
                tracing::warn!(error = %e, "change location: failed to set_current_dir");
            }
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
                                models: resp.models,
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
                                models: resp.models,
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
        Effect::ScanForeignSessions { cwd, compat, grok_home, coordinator, seq } => {
            if coordinator.latest_seq() != seq {
                return (false, meta);
            }
            let semaphore = coordinator.semaphore();
            let latest_seq = coordinator.latest_seq_handle();
            let abort_handle = tasks
                .spawn(async move {
                    let Ok(permit) = semaphore.acquire_owned().await else {
                        return TaskResult::ForeignSessionsScanned {
                            entries: Vec::new(),
                            seq,
                        };
                    };
                    if latest_seq.load(std::sync::atomic::Ordering::Acquire) != seq {
                        return TaskResult::ForeignSessionsScanned {
                            entries: Vec::new(),
                            seq,
                        };
                    }
                    let enabled = crate::app::foreign_sessions::gated_sources_async(
                            compat,
                            &grok_home,
                        )
                        .await;
                    if latest_seq.load(std::sync::atomic::Ordering::Acquire) != seq
                        || !(enabled.claude || enabled.codex || enabled.cursor)
                    {
                        return TaskResult::ForeignSessionsScanned {
                            entries: Vec::new(),
                            seq,
                        };
                    }
                    let summaries = tokio::task::spawn_blocking(move || {
                            let _permit = permit;
                            pi_foreign_sessions::scan_foreign_sessions(
                                &cwd,
                                enabled,
                            )
                        })
                        .await
                        .unwrap_or_else(|error| {
                            tracing::warn!(%error, "foreign session scan task failed");
                            Vec::new()
                        });
                    let entries = summaries
                        .into_iter()
                        .map(crate::app::foreign_sessions::map_summary)
                        .collect();
                    TaskResult::ForeignSessionsScanned {
                        entries,
                        seq,
                    }
                });
            coordinator.install_abort_handle(seq, abort_handle);
        }
        Effect::CanonicalizeForeignResumeCwd { requested_cwd, launch_token } => {
            tasks
                .spawn(async move {
                    let cwd_for_task = requested_cwd.clone();
                    let canonical_cwd = tokio::task::spawn_blocking(move || {
                            dunce::canonicalize(cwd_for_task).ok()
                        })
                        .await
                        .unwrap_or_else(|error| {
                            tracing::warn!(%error, "foreign resume cwd canonicalization task failed");
                            None
                        });
                    TaskResult::ForeignResumeCwdCanonicalized {
                        requested_cwd,
                        canonical_cwd,
                        launch_token,
                    }
                });
        }
        Effect::DetectForeignResumeHint {
            canonical_cwd,
            compat,
            grok_home,
            launch_token,
        } => {
            tasks
                .spawn(async move {
                    let cwd_for_scan = canonical_cwd.clone();
                    let recent = crate::app::foreign_sessions::with_gated_sources_async(
                            compat,
                            &grok_home,
                            |enabled| async move {
                                tokio::task::spawn_blocking(move || pi_foreign_sessions::most_recent_foreign_session(
                                        &cwd_for_scan,
                                        enabled,
                                        crate::app::foreign_sessions::RESUME_HINT_WINDOW,
                                    ))
                                    .await
                                    .unwrap_or_else(|error| {
                                        tracing::warn!(%error, "foreign resume detection task failed");
                                        None
                                    })
                            },
                        )
                        .await
                        .flatten();
                    TaskResult::ForeignResumeHintDetected {
                        canonical_cwd,
                        launch_token,
                        hint: recent,
                    }
                });
        }
        Effect::FetchSessionList { query, seq, kind_filter: _ } => {
            let tx = acp_tx.clone();
            let cwd = cwd.to_path_buf();
            tasks
                .spawn(async move {
                    let request = acp::ListSessionsRequest::default().cwd(cwd.clone());
                    let result = acp_send(request, &tx).await;
                    match result {
                        Ok(resp) => {
                            let mut sessions = session_picker_entries_from_acp(&resp);
                            if let Some(q) = query.as_ref().filter(|s| !s.is_empty()) {
                                let needle = q.to_lowercase();
                                sessions.retain(|e| {
                                    e.summary.to_lowercase().contains(&needle)
                                        || e.id.to_lowercase().contains(&needle)
                                        || e.cwd.to_lowercase().contains(&needle)
                                });
                            }
                            TaskResult::SessionListLoaded {
                                sessions,
                                partial: None,
                                scope: pi_shell::session::unified_list::ListScope::Cwd,
                                seq,
                                query,
                            }
                        }
                        Err(e) => {
                            TaskResult::SessionListFailed {
                                error: sanitize_user_error(&format!("{e}")),
                                seq,
                                query,
                            }
                        }
                    }
                });
        }
        Effect::DebounceSessionSearch { query, seq } => {
            tasks
                .spawn(async move {
                    tokio::time::sleep(
                            std::time::Duration::from_millis(SESSION_SEARCH_DEBOUNCE_MS),
                        )
                        .await;
                    TaskResult::SessionSearchDebounceExpired {
                        query,
                        seq,
                    }
                });
        }
        Effect::FetchRoster => {
            tasks.spawn(async move {
                TaskResult::RosterLoaded {
                    sessions: Vec::new(),
                }
            });
        }
        Effect::FetchDashboardSessions => {
            let tx = acp_tx.clone();
            let cwd = cwd.to_path_buf();
            tasks
                .spawn(async move {
                    let request = acp::ListSessionsRequest::default().cwd(cwd);
                    match acp_send(request, &tx).await {
                        Ok(resp) => {
                            let sessions = session_picker_entries_from_acp(&resp)
                                .iter()
                                .map(session_picker_entry_to_roster)
                                .collect();
                            TaskResult::DashboardSessionsLoaded {
                                sessions,
                            }
                        }
                        Err(_) => {
                            TaskResult::DashboardSessionsLoaded {
                                sessions: vec![],
                            }
                        }
                    }
                });
        }
        Effect::RestoreAndLoadSession { agent_id, session_id, session_cwd: _ } => {
            use pi_shell::agent::session_registry_client::SessionRegistryClient;
            use pi_shell::session::restore::restore_session_with_storage;
            let setup_started = std::time::Instant::now();
            let raw_config = pi_shell::config::load_effective_config();
            let setup = raw_config
                .ok()
                .and_then(|raw| {
                    let cfg = pi_shell::agent::config::Config::new_from_toml_cfg(
                            &raw,
                        )
                        .ok()?;
                    let proxy_base = cfg.endpoints.proxy_url();
                    let deployment_key = cfg.endpoints.deployment_key.clone();
                    let alpha_test_key = cfg.endpoints.alpha_test_key.clone();
                    let auth_manager = crate::app::session_startup::pre_acp_auth_manager(
                        &cfg,
                    );
                    let registry = SessionRegistryClient::new(&proxy_base, String::new())
                        .with_deployment_key(deployment_key.clone())
                        .with_alpha_test_key(alpha_test_key.clone())
                        .with_session_id(session_id.clone())
                        .with_auth(auth_manager.clone());
                    let storage = pi_shell::auth::credential_provider::build_storage_client_for_proxy(
                        &proxy_base,
                        deployment_key,
                        alpha_test_key,
                        Some(auth_manager.clone()),
                        None,
                        Some(session_id.clone()),
                        "grok-pager",
                    );
                    Some((auth_manager, registry, storage))
                });
            tracing::info!(
                elapsed_ms = setup_started.elapsed().as_millis() as u64,
                ok = setup.is_some(),
                "restore: auth/client setup"
            );
            let target_cwd = cwd.to_path_buf();
            let ptx = progress_tx.clone();
            tasks
                .spawn(async move {
                    let Some((auth_manager, registry_client, storage_client)) = setup
                    else {
                        return TaskResult::SessionRestoreFailed {
                            agent_id,
                            error: "Failed to load configuration.".into(),
                        };
                    };
                    let _ = auth_manager.auth().await;
                    let progress: Option<
                        pi_shell::session::restore::ProgressCallback,
                    > = {
                        use pi_shell::session::restore::{PhaseStep, RestorePhase};
                        Some(
                            Box::new(move |event| {
                                let msg = match (event.phase, event.step) {
                                    (RestorePhase::Download, PhaseStep::Start) => {
                                        Some("Downloading session archives...".to_string())
                                    }
                                    (RestorePhase::Download, PhaseStep::End) => {
                                        Some(
                                            format!(
                                "Downloads finished ({}).",
                                format_restore_elapsed(event.elapsed),
                            ),
                                        )
                                    }
                                    (RestorePhase::Codebase, PhaseStep::Start) => {
                                        Some("Restoring code...".to_string())
                                    }
                                    (RestorePhase::Codebase, PhaseStep::End) => {
                                        event
                                            .detail
                                            .as_ref()
                                            .map(|detail| format!("Code restored ({detail})."))
                                    }
                                    (RestorePhase::Memory, PhaseStep::Start) => {
                                        Some("Restoring memory...".to_string())
                                    }
                                    (RestorePhase::SessionState, PhaseStep::Start) => {
                                        Some("Restoring session state...".to_string())
                                    }
                                    (RestorePhase::SessionState, PhaseStep::End) => {
                                        event
                                            .detail
                                            .as_ref()
                                            .map(|detail| format!("Session state restored ({detail})."))
                                    }
                                    (RestorePhase::Finalize, _) => {
                                        let elapsed_secs = event.elapsed.as_secs();
                                        let status = if event.incomplete {
                                            "Restore incomplete"
                                        } else {
                                            "Restore complete"
                                        };
                                        if elapsed_secs >= 60 {
                                            Some(
                                                format!(
                                        "{status} ({}m{:02}s).",
                                        elapsed_secs / 60,
                                        elapsed_secs % 60
                                    ),
                                            )
                                        } else {
                                            Some(format!("{status} ({elapsed_secs}s)."))
                                        }
                                    }
                                    _ => None,
                                };
                                if let Some(text) = msg {
                                    let _ = ptx
                                        .send(RestoreProgressMsg {
                                            agent_id,
                                            message: text,
                                        });
                                }
                            }),
                        )
                    };
                    let cwd_str = target_cwd.to_string_lossy().to_string();
                    match restore_session_with_storage(
                            &registry_client,
                            &storage_client,
                            &session_id,
                            &cwd_str,
                            pi_shell::session::restore::RestoreSessionOpts {
                                turn_override: None,
                                progress,
                                restore_code: true,
                            },
                        )
                        .await
                    {
                        Ok(result) => {
                            let effective_id = if result.local_session_id.is_empty() {
                                session_id
                            } else {
                                result.local_session_id
                            };
                            TaskResult::SessionRestored {
                                agent_id,
                                local_session_id: effective_id,
                            }
                        }
                        Err(e) => {
                            TaskResult::SessionRestoreFailed {
                                agent_id,
                                error: format!("{e:#}"),
                            }
                        }
                    }
                });
        }
        Effect::LoadCardDetail { source, session_id, cwd, generation } => {
            tasks
                .spawn(async move {
                    use crate::app::app_view::CardDetail;
                    let result_session_id = session_id.clone();
                    let detail = tokio::task::spawn_blocking(move || {
                            let info = pi_shell::session::info::Info {
                                id: acp::SessionId::new(session_id),
                                cwd,
                            };
                            let history_path = pi_shell::session::persistence::session_dir(
                                    &info,
                                )
                                .join("chat_history.jsonl");
                            let first_prompt_preview = extract_first_user_prompt(&info)
                                .unwrap_or_default();
                            let (turn_count, tool_call_count) = count_chat_history_stats(
                                &history_path,
                            );
                            CardDetail {
                                turn_count,
                                tool_call_count,
                                first_prompt_preview,
                            }
                        })
                        .await
                        .unwrap_or(CardDetail {
                            turn_count: 0,
                            tool_call_count: 0,
                            first_prompt_preview: String::new(),
                        });
                    TaskResult::CardDetailLoaded {
                        source,
                        session_id: result_session_id,
                        generation,
                        detail,
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
        Effect::SendPromptBlocks { agent_id, session_id, blocks, prompt_id }
        | Effect::SendPromptNow { agent_id, session_id, blocks, prompt_id } => {
            let send_now = effect_is_send_now;
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
                        "kind": if send_now { "send_now" } else { "blocks" },
                        "block_count": blocks.len(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    let send_start = std::time::Instant::now();
                    let mut meta = prompt_request_meta(&prompt_id, screen_mode);
                    if send_now && let Some(map) = meta.as_object_mut() {
                        map.insert("sendNow".into(), serde_json::Value::Bool(true));
                    }
                    let requeue_blocks = send_now.then(|| blocks.clone());
                    let req = acp::PromptRequest::new(session_id.clone(), blocks)
                        .meta(meta.as_object().cloned());
                    let result = acp_send(req, &tx).await;
                    let send_elapsed_ms = send_start.elapsed().as_millis() as u64;
                    ulog::info(
                        "prompt.acp_send.done",
                        Some(&session_id.0),
                        Some(
                            serde_json::json!({
                        "kind": if send_now { "send_now" } else { "blocks" },
                        "elapsed_ms": send_elapsed_ms,
                        "ok": result.is_ok(),
                        "prompt_id": prompt_id,
                    }),
                        ),
                    );
                    log_prompt_result(&session_id, &result);
                    if let (Some(blocks), Err(e)) = (requeue_blocks, &result) {
                        return TaskResult::SendPromptNowFailed {
                            agent_id,
                            session_id,
                            prompt_id,
                            error: format_acp_error(e, is_api_key_auth),
                            blocks,
                        };
                    }
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
            cancel_subagents,
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
                        "cancel_subagents": cancel_subagents,
                        "trigger": trigger_str,
                        "rewind_if_no_output": rewind_prompt_id.is_some(),
                        "rewind_prompt_id": rewind_prompt_id.as_deref(),
                    }),
                        ),
                    );
                    let send_start = std::time::Instant::now();
                    let mut meta = serde_json::json!({ "cancelSubagents": cancel_subagents });
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
        Effect::TogglePlanMode { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::QueueRemove { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::QueueReorder { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::QueueClear { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::QueueEdit { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::QueueHoldEdit { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::QueueReleaseEdit { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::QueueInterject { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
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
        Effect::SetModeThenPrompt {
            session_id,
            mode_id,
            agent_id,
            text,
            prompt_id,
            skill_token_ranges,
        } => {
            let tx = acp_tx.clone();
            let screen_mode = session_flags.screen_mode_label;
            let is_api_key_auth = session_flags.is_api_key_auth;
            tasks
                .spawn(async move {
                    let mode_req = acp::SetSessionModeRequest::new(
                        session_id.clone(),
                        mode_id,
                    );
                    if let Err(e) = acp_send(mode_req, &tx).await {
                        tracing::warn!("Failed to set session mode: {e}");
                    }
                    ulog::info(
                        "prompt submitted",
                        Some(&session_id.0),
                        Some(serde_json::json!({"len": text.len()})),
                    );
                    let prompt = vec![plain_prompt_content_block(text, &skill_token_ranges)];
                    let req = acp::PromptRequest::new(session_id.clone(), prompt)
                        .meta(
                            prompt_request_meta(&prompt_id, screen_mode)
                                .as_object()
                                .cloned(),
                        );
                    let result = acp_send(req, &tx).await;
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
        Effect::Compact { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::CompactComplete {
                    agent_id,
                    result: Ok(()),
                }
            });
        }
        Effect::FetchPromptHistory { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::PromptHistoryLoaded {
                    agent_id,
                    prompts: Vec::new(),
                }
            });
        }
        Effect::KillBgTask { session_id, task_id, .. } => {
            let sid = session_id.0.to_string();
            tasks.spawn(async move {
                TaskResult::BgTaskKilled {
                    session_id: sid,
                    task_id,
                    outcome: parse_kill_outcome("{}"),
                }
            });
        }
        Effect::KillSubagent { session_id, subagent_id } => {
            tasks.spawn(async move {
                TaskResult::KillSubagentComplete {
                    session_id,
                    subagent_id,
                    outcome: SubagentKillOutcome::NothingLive { status: None },
                }
            });
        }
        Effect::DeleteScheduledTask { .. } => {
            tasks.spawn(async move {
                TaskResult::CancelComplete
            });
        }
        Effect::DemoteToBackground { .. } => {
            tasks.spawn(async move {
                TaskResult::CancelComplete
            });
        }
        Effect::SwitchModel {
            agent_id,
            session_id,
            model_id,
            effort,
            prev_model_id,
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
                    let req = acp::SetSessionModelRequest::new(
                            session_id,
                            model_id.clone(),
                        )
                        .meta(meta);
                    let result = acp_send(req, &tx)
                        .await
                        .map(|_| ())
                        .map_err(|e| {
                            use pi_shell::agent::config::ModelSwitchIncompatibleAgentError;
                            if let Some(typed) = ModelSwitchIncompatibleAgentError::from_acp_error(
                                &e,
                            ) {
                                SwitchModelError::IncompatibleAgent {
                                    error: typed,
                                    prev_model_id: prev_model_id.clone(),
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
                                    ClipboardPasteTarget::DashboardDispatch
                                    | ClipboardPasteTarget::DashboardPeek { .. } => {
                                        ProbedAttachment::Image(pasted)
                                    }
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
        Effect::PlanDoctorFix { target, report, terminal, request } => {
            tasks
                .spawn(async move {
                    let result = tokio::task::spawn_blocking(move || match request {
                            crate::slash::command::DoctorRequest::ListFixes => {
                                Ok(
                                    actions::DoctorPlanningOutcome::Listing(
                                        crate::diagnostics::format_applicable_automatic_fixes(
                                            &report,
                                            &terminal,
                                        ),
                                    ),
                                )
                            }
                            crate::slash::command::DoctorRequest::Fix(id) => {
                                match crate::diagnostics::select_fix_plan(
                                    id,
                                    &report,
                                    &terminal,
                                ) {
                                    Ok(Some(plan)) => {
                                        Ok(actions::DoctorPlanningOutcome::Plan(Box::new(plan)))
                                    }
                                    Ok(None) => {
                                        Ok(
                                            actions::DoctorPlanningOutcome::RunLocally(
                                                crate::diagnostics::human_fix_command(id)
                                                    .unwrap_or_else(|| id.to_string()),
                                            ),
                                        )
                                    }
                                    Err(error) => Err(error.to_string()),
                                }
                            }
                            crate::slash::command::DoctorRequest::Report => {
                                unreachable!("report does not enter the planning effect")
                            }
                        })
                        .await
                        .map_err(|error| format!("Could not prepare the fix: {error}"))
                        .and_then(|result| result);
                    TaskResult::DoctorFixPlanned {
                        target,
                        result,
                    }
                });
        }
        Effect::ApplyDoctorFix { target, plan } => {
            tasks
                .spawn(async move {
                    let result = tokio::task::spawn_blocking(move || crate::diagnostics::apply_fix(
                            *plan,
                        ))
                        .await
                        .map_err(|error| format!("Could not apply the fix: {error}"))
                        .and_then(|result| result.map_err(|error| error.to_string()));
                    TaskResult::DoctorFixApplied {
                        target,
                        result,
                    }
                });
        }
        Effect::FetchChangelog => {
            tasks
                .spawn(async move {
                    let changelog = tokio::task::spawn_blocking(|| {
                            pi_shell::util::changelog::ChangelogManager::new()
                                .fetch()
                        })
                        .await
                        .unwrap_or_else(|e| {
                            tracing::warn!(error = %e, "changelog fetch task failed");
                            pi_shell::util::changelog::Changelog {
                                markdown: None,
                                entries: None,
                            }
                        });
                    TaskResult::ChangelogFetched {
                        markdown: changelog.markdown,
                        entries: changelog.entries.unwrap_or_default(),
                    }
                });
        }
        Effect::PersistAnnouncementsHidden { hidden_ids } => {
            tasks
                .spawn(async move {
                    pi_announcements::write_hidden_announcement_ids(&hidden_ids)
                        .await;
                    TaskResult::AnnouncementsHiddenPersisted {
                        result: Ok(()),
                    }
                });
        }
        Effect::PersistPrivacyBannerAcked { acked_at } => {
            tasks
                .spawn(async move {
                    if let Err(e) = pi_shell::util::config::set_privacy_banner_acked(
                            acked_at,
                        )
                        .await
                    {
                        tracing::warn!(error = %e, "failed to persist privacy_banner_acked");
                    }
                    TaskResult::CancelComplete
                });
        }
        Effect::PersistConsentAnswer { account, notice_id, version, acked } => {
            tasks
                .spawn(async move {
                    match pi_shell::util::config::set_consent_answer(
                            account,
                            notice_id,
                            version,
                            acked,
                        )
                        .await
                    {
                        Ok(()) => TaskResult::CancelComplete,
                        Err(e) if !acked => {
                            TaskResult::ConsentPersistFailed {
                                error: e.to_string(),
                            }
                        }
                        Err(e) => {
                            tracing::warn!(error = %e, "consent ack not persisted");
                            TaskResult::CancelComplete
                        }
                    }
                });
        }
        Effect::RecordConsentUpstream { notice_id, version } => {
            tasks.spawn(async move {
                TaskResult::ConsentRecorded {
                    notice_id,
                    version,
                }
            });
        }
        Effect::PersistMemoryFullscreen { fullscreen } => {
            persist_hint(
                tasks,
                "memory_modal_fullscreen",
                fullscreen,
                "memory fullscreen",
            );
        }
        Effect::PersistDashboard(persisted) => {
            tasks
                .spawn(async move {
                    let result = tokio::task::spawn_blocking(move || {
                            if let Err(e) = crate::views::dashboard::state::write_persisted(
                                &persisted,
                            ) {
                                tracing::warn!(error = %e, "failed to persist dashboard config");
                            }
                        })
                        .await;
                    if let Err(e) = result {
                        tracing::warn!(error = %e, "failed to persist dashboard: join error");
                    }
                    TaskResult::CancelComplete
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
        Effect::FetchMcpsList { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::McpsListLoaded {
                    agent_id,
                    result: Ok(crate::views::mcps_modal::convert_list_response(
                        crate::views::mcps_modal::McpsListResponse { servers: Vec::new() },
                    )),
                }
            });
        }
        Effect::McpAuthTrigger { agent_id, server_name, .. } => {
            tasks.spawn(async move {
                TaskResult::McpAuthTriggerDone {
                    agent_id,
                    server_name,
                    result: Err("MCP auth trigger not supported in standard ACP".into()),
                }
            });
        }
        Effect::McpSetupSubmit { agent_id, server_name, .. } => {
            tasks.spawn(async move {
                TaskResult::McpSetupSubmitDone {
                    agent_id,
                    server_name,
                    result: Ok(()),
                }
            });
        }
        Effect::FetchHooksList { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::HooksListLoaded {
                    agent_id,
                    result: Ok(pi_hooks_plugins_types::HooksListResponse {
                        hooks: Vec::new(),
                        project_trusted: true,
                        load_errors: Vec::new(),
                    }),
                }
            });
        }
        Effect::FetchPluginsList { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::PluginsListLoaded {
                    agent_id,
                    result: Ok(pi_hooks_plugins_types::PluginsListResponse {
                        plugins: Vec::new(),
                    }),
                }
            });
        }
        Effect::HooksAction { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::HooksActionResult {
                    agent_id,
                    result: Ok(pi_hooks_plugins_types::ActionOutcome {
                        status: pi_hooks_plugins_types::OutcomeStatus::Success,
                        message: String::new(),
                        requires_reload: false,
                        requires_restart: false,
                    }),
                }
            });
        }
        Effect::PluginsAction { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::PluginsActionResult {
                    agent_id,
                    result: Ok(pi_hooks_plugins_types::ActionOutcome {
                        status: pi_hooks_plugins_types::OutcomeStatus::Success,
                        message: String::new(),
                        requires_reload: false,
                        requires_restart: false,
                    }),
                }
            });
        }
        Effect::FetchMarketplaceList { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::MarketplaceListLoaded {
                    agent_id,
                    result: Ok(pi_hooks_plugins_types::MarketplaceListResponse {
                        sources: Vec::new(),
                    }),
                }
            });
        }
        Effect::FetchPluginCtaCatalog { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::PluginCtaCatalogLoaded {
                    agent_id,
                    result: Ok(pi_hooks_plugins_types::MarketplaceListResponse {
                        sources: Vec::new(),
                    }),
                }
            });
        }
        Effect::FetchSkillsList { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::SkillsListLoaded {
                    agent_id,
                    result: Ok(Vec::new()),
                }
            });
        }
        Effect::FetchWorkflowsList { agent_id, session_id } => {
            tasks.spawn(async move {
                TaskResult::WorkflowsListLoaded {
                    agent_id,
                    session_id,
                    result: Ok(Vec::new()),
                }
            });
        }
        Effect::ToggleSkill { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::SkillsToggleDone {
                    agent_id,
                    result: Ok(Vec::new()),
                }
            });
        }
        Effect::CheckMarketplaceUpdates { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::MarketplaceUpdatesAvailable {
                    agent_id,
                    updates: Vec::new(),
                }
            });
        }
        Effect::MarketplaceAction { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::MarketplaceActionResult {
                    agent_id,
                    result: Ok(pi_hooks_plugins_types::ActionOutcome {
                        status: pi_hooks_plugins_types::OutcomeStatus::Success,
                        message: String::new(),
                        requires_reload: false,
                        requires_restart: false,
                    }),
                }
            });
        }
        Effect::InstallPluginFromCta {
            agent_id,
            plugin_relative_path,
            ..
        } => {
            let plugin_name = plugin_relative_path
                .rsplit('/')
                .next()
                .unwrap_or(plugin_relative_path.as_str())
                .to_string();
            tasks.spawn(async move {
                TaskResult::CtaPluginInstallDone {
                    agent_id,
                    plugin_name,
                    result: Ok(pi_hooks_plugins_types::ActionOutcome {
                        status: pi_hooks_plugins_types::OutcomeStatus::Success,
                        message: String::new(),
                        requires_reload: false,
                        requires_restart: false,
                    }),
                }
            });
        }
        Effect::ReloadPluginsForCta { agent_id, plugin_name, .. } => {
            tasks.spawn(async move {
                TaskResult::CtaPluginReloadDone {
                    agent_id,
                    plugin_name,
                    result: Ok(pi_hooks_plugins_types::ActionOutcome {
                        status: pi_hooks_plugins_types::OutcomeStatus::Success,
                        message: String::new(),
                        requires_reload: false,
                        requires_restart: false,
                    }),
                }
            });
        }
        Effect::FetchPluginCtaMcps { agent_id, session_id, plugin_name } => {
            let tx = acp_tx.clone();
            tasks.spawn(fetch_plugin_cta_mcps(agent_id, session_id, plugin_name, tx));
        }
        Effect::RetryPluginCtaMcps { agent_id, session_id, plugin_name } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    tokio::time::sleep(
                            std::time::Duration::from_millis(CTA_MCP_RETRY_DELAY_MS),
                        )
                        .await;
                    fetch_plugin_cta_mcps(agent_id, session_id, plugin_name, tx).await
                });
        }
        Effect::DismissCtaInstalled { agent_id, plugin_name } => {
            tasks
                .spawn(async move {
                    tokio::time::sleep(
                            std::time::Duration::from_millis(CTA_INSTALLED_DISMISS_MS),
                        )
                        .await;
                    TaskResult::CtaInstalledDismissTimeout {
                        agent_id,
                        plugin_name,
                    }
                });
        }
        Effect::UpsertMcpServer { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::McpToggleDone {
                    agent_id,
                    result: Ok(()),
                }
            });
        }
        Effect::DeleteMcpServer { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::McpToggleDone {
                    agent_id,
                    result: Ok(()),
                }
            });
        }
        Effect::ToggleMcpServer { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::McpToggleDone {
                    agent_id,
                    result: Ok(()),
                }
            });
        }
        Effect::ToggleMcpTool { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::McpToggleDone {
                    agent_id,
                    result: Ok(()),
                }
            });
        }
        Effect::ShareSession { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::ShareSessionFailed {
                    agent_id,
                    error: "Share session is not supported in standard ACP".to_string(),
                }
            });
        }
        Effect::FetchSessionAgentName { agent_id, session_id } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    match fetch_session_info(&session_id, &tx).await {
                        Ok(info) => {
                            TaskResult::SessionAgentNameResolved {
                                agent_id,
                                agent_name: info.data.agent_name,
                            }
                        }
                        Err(e) => {
                            tracing::debug!("session agent name fetch failed: {e}");
                            TaskResult::SessionAgentNameResolved {
                                agent_id,
                                agent_name: None,
                            }
                        }
                    }
                });
        }
        Effect::ShowSessionInfo { agent_id, session_id, show_resolved_model, nonce } => {
            let is_api_key_auth = session_flags.is_api_key_auth;
            let api_key_env_set = pi_shell::agent::auth_method::has_pi_api_key_env();
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    match fetch_session_info(&session_id, &tx).await {
                        Ok(info) => {
                            let title = lookup_session_title(&session_id, &info.cwd)
                                .await;
                            let text = format_session_info(
                                &info,
                                title.as_deref(),
                                show_resolved_model,
                                is_api_key_auth,
                                api_key_env_set,
                            );
                            let fields = session_info_fields(
                                &info,
                                title.as_deref(),
                                show_resolved_model,
                            );
                            TaskResult::SessionInfoComplete {
                                agent_id,
                                session_id,
                                info: Box::new(info),
                                text,
                                fields,
                                nonce,
                            }
                        }
                        Err(error) => {
                            TaskResult::SessionInfoFailed {
                                agent_id,
                                session_id,
                                error,
                                nonce,
                            }
                        }
                    }
                });
        }
        Effect::RenameSession { agent_id, session_id, title, cwd, kind } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    match session_rename_rpc(
                            &tx,
                            actions::RenameSessionRequest::for_rename(
                                session_id.0.to_string(),
                                title.clone(),
                                cwd.to_string_lossy().to_string(),
                                kind,
                            ),
                        )
                        .await
                    {
                        Ok(()) => {
                            TaskResult::RenameSessionComplete {
                                agent_id,
                                title,
                            }
                        }
                        Err(error) => {
                            TaskResult::RenameSessionFailed {
                                agent_id,
                                error,
                            }
                        }
                    }
                });
        }
        Effect::ResetSessionTitle {
            agent_id,
            session_id,
            cwd,
            kind,
            previous_display_name,
            previous_generated_title,
        } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    match session_rename_rpc(
                            &tx,
                            actions::RenameSessionRequest::for_reset(
                                session_id.0.to_string(),
                                cwd.to_string_lossy().to_string(),
                                kind,
                            ),
                        )
                        .await
                    {
                        Ok(()) => {
                            TaskResult::ResetSessionTitleComplete {
                                agent_id,
                            }
                        }
                        Err(error) => {
                            TaskResult::ResetSessionTitleFailed {
                                agent_id,
                                error,
                                previous_display_name,
                                previous_generated_title,
                            }
                        }
                    }
                });
        }
        Effect::DeleteSession { source, session_id, after, .. } => {
            tasks.spawn(async move {
                TaskResult::DeleteSessionComplete {
                    source,
                    session_id,
                    after,
                }
            });
        }
        Effect::SetCodingDataSharing {
            agent_id,
            opted_in,
            seq,
            ..
        } => {
            tasks.spawn(async move {
                TaskResult::CodingDataSharingUpdated {
                    agent_id,
                    opted_in,
                    seq,
                }
            });
        }
        Effect::ShowContextInfo { agent_id, session_id, nonce } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    match fetch_session_info(&session_id, &tx).await {
                        Ok(info) => {
                            TaskResult::ContextInfoComplete {
                                agent_id,
                                session_id,
                                info: Box::new(info),
                                nonce,
                            }
                        }
                        Err(error) => {
                            TaskResult::ContextInfoFailed {
                                agent_id,
                                session_id,
                                error,
                                nonce,
                            }
                        }
                    }
                });
        }
        Effect::FetchSessionUsage { agent_id, session_id, nonce } => {
            let tx = acp_tx.clone();
            tasks
                .spawn(async move {
                    match fetch_session_usage(&session_id, &tx).await {
                        Ok(usage) => {
                            TaskResult::SessionUsageComplete {
                                agent_id,
                                session_id,
                                usage: Box::new(usage),
                                nonce,
                            }
                        }
                        Err(error) => {
                            TaskResult::SessionUsageFailed {
                                agent_id,
                                session_id,
                                error,
                                nonce,
                            }
                        }
                    }
                });
        }
        Effect::SendFeedback { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::FeedbackComplete { agent_id }
            });
        }
        Effect::UploadFeedbackTrace { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::FeedbackTraceUploaded {
                    agent_id,
                    error: None,
                }
            });
        }
        Effect::RewriteMemoryNote {
            agent_id,
            raw_text,
            nonce,
            ..
        } => {
            tasks.spawn(async move {
                TaskResult::MemoryNoteRewritten {
                    agent_id,
                    result: Ok(raw_text),
                    nonce,
                }
            });
        }
        Effect::SaveMemoryNote { agent_id, text, cwd } => {
            tasks
                .spawn(async move {
                    let result = tokio::task::spawn_blocking(move || {
                            let storage = pi_shell::session::memory::MemoryStorage::new(
                                &cwd,
                                None,
                            );
                            storage
                                .append_to_memory(
                                    pi_shell::session::memory::MemoryScope::Global,
                                    &text,
                                )
                        })
                        .await
                        .map_err(|e| format!("task join error: {e}"))
                        .and_then(|r| r.map_err(|e| format!("{e}")));
                    TaskResult::MemoryNoteSaved {
                        agent_id,
                        result,
                    }
                });
        }
        Effect::SendBtw {
            agent_id,
            minimal_request_id,
            ..
        } => {
            tasks.spawn(async move {
                TaskResult::BtwResponse {
                    agent_id,
                    result: Err("BTW is not supported in standard ACP".into()),
                    minimal_request_id,
                }
            });
        }
        Effect::SendRecap { session_id, auto } => {
            tasks.spawn(async move {
                TaskResult::RecapRequested {
                    session_id,
                    auto,
                    error: None,
                }
            });
        }
        Effect::SendInterject {
            agent_id,
            text,
            blocks,
            ..
        } => {
            tasks.spawn(async move {
                TaskResult::InterjectFailed {
                    agent_id,
                    error: "Interjection is not supported in standard ACP".into(),
                    text,
                    blocks,
                }
            });
        }
        Effect::FetchCatalogEntry { .. } => {
            tasks.spawn(async move {
                TaskResult::CatalogEntryFailed {
                    error: "Bundle catalog is not supported in standard ACP".to_string(),
                }
            });
        }
        Effect::FetchBundleStatus => {
            tasks.spawn(async move {
                TaskResult::BundleStatusFailed {
                    error: "Bundle status is not supported in standard ACP".to_string(),
                }
            });
        }
        Effect::RefreshAvailableCommands { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::AvailableCommandsRefreshed {
                    agent_id,
                    commands: vec![],
                }
            });
        }
        Effect::FetchRewindPoints { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::RewindPointsLoaded {
                    agent_id,
                    points: vec![],
                }
            });
        }
        Effect::RewindExecute { agent_id, .. } => {
            tasks.spawn(async move {
                TaskResult::RewindExecuteFailed {
                    agent_id,
                    error: "Rewind is not supported in standard ACP".to_string(),
                }
            });
        }
        Effect::DeepSearchSessions { seq, .. } => {
            tasks.spawn(async move {
                TaskResult::DeepSearchResults {
                    results: Vec::new(),
                    seq,
                }
            });
        }
        Effect::ForkSession {
            agent_id,
            ..
        } => {
            tasks.spawn(async move {
                TaskResult::ForkSessionFailed {
                    agent_id,
                    error: "Session fork is not supported in standard ACP mode".to_string(),
                }
            });
        }
        Effect::HydrateSessionMetaFromDisk {
            agent_id,
            session_id,
            cwd,
            last_turn_summary_gen,
        } => {
            tasks
                .spawn(async move {
                    let info = pi_shell::session::info::Info {
                        id: session_id,
                        cwd: cwd.to_string_lossy().to_string(),
                    };
                    let path = pi_shell::session::persistence::session_dir(&info)
                        .join("summary.json");
                    type DiskTitle = (Option<(String, bool)>, Option<String>);
                    let (title, last_turn_summary) = tokio::task::spawn_blocking(move || -> Option<
                            DiskTitle,
                        > {
                            let raw = std::fs::read_to_string(path).ok()?;
                            let summary: pi_shell::session::persistence::Summary = serde_json::from_str(
                                    &raw,
                                )
                                .ok()?;
                            let manual = summary.manual_title_opt();
                            let is_manual = manual.is_some();
                            let title = manual
                                .or_else(|| summary.display_title_opt())
                                .map(|t| (t, is_manual));
                            Some((title, summary.last_turn_summary))
                        })
                        .await
                        .ok()
                        .flatten()
                        .unwrap_or((None, None));
                    TaskResult::SessionMetaFromDisk {
                        agent_id,
                        title,
                        last_turn_summary,
                        last_turn_summary_gen,
                    }
                });
        }
        Effect::FetchBilling { agent_id, silent, nonce } => {
            tasks.spawn(async move {
                TaskResult::BillingFetched {
                    agent_id,
                    balance: None,
                    silent,
                    subscription_tier: None,
                    autotopup: crate::views::credit_bar::AutoTopupFetch::Cleared,
                    nonce,
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
        Effect::DebounceSuggestions { agent_id, generation } => {
            tasks
                .spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                    TaskResult::SuggestionDebounceExpired {
                        agent_id,
                        generation,
                    }
                });
        }
        Effect::DebouncePluginCta { agent_id, generation } => {
            tasks
                .spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                    TaskResult::PluginCtaDebounceExpired {
                        agent_id,
                        generation,
                    }
                });
        }
        Effect::FetchShellSuggestions { .. } => {
            tasks.spawn(async move { TaskResult::CancelComplete });
        }
        Effect::FetchPromptSuggestion {
            agent_id,
            generation,
            ..
        } => {
            tasks.spawn(async move {
                TaskResult::PromptSuggestionLoaded {
                    agent_id,
                    suggestion: None,
                    generation,
                }
            });
        }
    }
    (false, meta)
}
/// Fetch session info (not supported in standard ACP).
async fn fetch_session_info(
    _session_id: &acp::SessionId,
    _tx: &AcpAgentTx,
) -> Result<SessionInfoResponse, String> {
    Err("Session info is not supported in standard ACP".to_string())
}

/// Fetch session usage (not supported in standard ACP).
async fn fetch_session_usage(
    _session_id: &acp::SessionId,
    _tx: &AcpAgentTx,
) -> Result<pi_shell::extensions::notification::PromptUsage, String> {
    Err("Session usage is not supported in standard ACP".to_string())
}

/// Shared rename RPC for rename and `/rename --auto` (not supported in standard ACP).
async fn session_rename_rpc(
    _tx: &AcpAgentTx,
    request: actions::RenameSessionRequest,
) -> Result<(), String> {
    let verb = if request.reset_to_auto {
        "reset session title"
    } else {
        "rename session"
    };
    Err(format!("{verb} is not supported in standard ACP"))
}
/// Session title from local persistence: loads only this session's summary
/// (`cwd` from the `x.ai/session/info` response), never the all-sessions list.
async fn lookup_session_title(session_id: &acp::SessionId, cwd: &str) -> Option<String> {
    lookup_session_title_in(
            pi_shell::util::grok_home::grok_home(),
            session_id,
            cwd,
        )
        .await
}
/// [`lookup_session_title`] against an explicit root, for tests.
async fn lookup_session_title_in(
    root: std::path::PathBuf,
    session_id: &acp::SessionId,
    cwd: &str,
) -> Option<String> {
    use pi_shell::session::storage::{JsonlStorageAdapter, StorageAdapter};
    let info = pi_shell::session::info::Info {
        id: session_id.clone(),
        cwd: cwd.to_string(),
    };
    JsonlStorageAdapter::with_root(root)
        .load_summary(&info)
        .await
        .ok()
        .and_then(|s| s.display_title_opt())
}
/// Format session info into a human-readable string.
///
/// Mirrors the TUI's `render_session_info` for pager display.
/// Structured `/session-info` rows — the single source of truth for both the
/// formatted string ([`format_session_info`]) and the modal, so neither has to
/// re-parse the other. Auth is not a field here; it is prose the string appends
/// on its own. `compact` marks the dense model/runtime group the modal renders
/// as `Label: value` on one line.
fn session_info_fields(
    info: &SessionInfoResponse,
    title: Option<&str>,
    show_resolved_model: bool,
) -> Vec<SessionInfoField> {
    let mut fields = Vec::new();
    let mut push = |label: &'static str, value: String, compact: bool| {
        fields
            .push(SessionInfoField {
                label,
                value,
                compact,
            });
    };
    if let Some(t) = title {
        push("Title", t.to_string(), false);
    }
    push(
        "Shell version",
        pi_version::display_version(pi_update::channel_label()),
        false,
    );
    push("Session ID", info.session_id.to_string(), false);
    if let Some(id) = info.data.conversation_id.as_deref().filter(|id| !id.is_empty()) {
        push("Conversation ID", id.to_string(), false);
    }
    push("Working directory", info.cwd.to_string(), false);
    let model = info.data.model.as_deref().unwrap_or("unknown");
    let model_display = pi_shell::session::model_display_name(
        info.data.model_display_name.as_deref(),
        model,
        info.data.resolved_model_id.as_deref(),
        show_resolved_model,
    );
    push("Model", model_display.to_string(), true);
    if pi_shell::session::should_show_model_fingerprint(
        info.data.show_model_fingerprint,
        model,
    ) && let Some(fp) = info.data.model_fingerprint.as_deref()
    {
        push("Model Hash", fp.to_string(), true);
    }
    if let Some(b) = info.data.api_backend.as_deref() {
        push("API Backend", b.to_string(), true);
    }
    if let Some(profile) = pi_sandbox::profile_name() {
        push("Sandbox", profile.to_string(), true);
    }
    push("Turn", info.data.turn_index.to_string(), true);
    let ctx = &info.data.context;
    push(
        "Context",
        format!("{} / {} tokens ({}%)", ctx.used, ctx.total, ctx.usage_pct),
        true,
    );
    fields
}
/// The `/session-info` block as a plain string for minimal-mode scrollback.
/// Built from [`session_info_fields`] (one `  Label: value` line each) with the
/// auth prose spliced in after the shell version, so it stays a single source
/// of truth with the modal.
fn format_session_info(
    info: &SessionInfoResponse,
    title: Option<&str>,
    show_resolved_model: bool,
    is_api_key_auth: bool,
    api_key_env_set: bool,
) -> String {
    let auth_lines = format_auth_lines(is_api_key_auth, api_key_env_set);
    let mut out = String::new();
    for field in session_info_fields(info, title, show_resolved_model) {
        out.push_str("  ");
        out.push_str(field.label);
        out.push_str(": ");
        out.push_str(&field.value);
        out.push('\n');
        if field.label == "Shell version" {
            out.push_str(&auth_lines);
        }
    }
    out.truncate(out.trim_end_matches('\n').len());
    out
}
/// Auth section for `/session-info` — active login method.
///
/// This reflects the process login / ACP auth method, not per-model sampling
/// credentials (a model `api_key`/`env_key` can still own the turn).
fn format_auth_lines(is_api_key_auth: bool, api_key_env_set: bool) -> String {
    if is_api_key_auth {
        let method = if api_key_env_set {
            "  Auth method: API key (PI_API_KEY)\n"
        } else {
            "  Auth method: API key\n"
        };
        return format!(
            "{method}  Run `grok login` to use your SuperGrok subscription instead.\n"
        );
    }
    String::from("  Auth method: OAuth\n")
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
pub(crate) const REWIND_MODE_WIRE: &str = "conversation_only";
pub(crate) fn rewind_execute_params(
    session_id: &str,
    target_prompt_index: usize,
) -> serde_json::Value {
    serde_json::json!({
        "sessionId": session_id,
        "targetPromptIndex": target_prompt_index,
        "force": true,
        "mode": REWIND_MODE_WIRE,
    })
}
/// Build the `x.ai/interject` params. The optional structured `content`
/// (text + images) is omitted ENTIRELY when `None` so the legacy wire
/// shape stays byte-identical. Extracted from the spawn for testability.
fn build_interject_params(
    session_id: &acp::SessionId,
    text: &str,
    interjection_id: &str,
    blocks: Option<&[acp::ContentBlock]>,
) -> serde_json::Value {
    let mut params = serde_json::json!({
        "sessionId": session_id.0.to_string(),
        "text": text,
        "interjectionId": interjection_id,
    });
    if let Some(blocks) = blocks {
        params["content"] = serde_json::to_value(blocks)
            .expect("serialize interject content");
    }
    params
}
#[cfg(test)]
mod tests;
