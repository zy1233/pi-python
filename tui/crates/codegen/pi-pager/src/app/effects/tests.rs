#![cfg_attr(rustfmt, rustfmt::skip)]
use super::*;
/// The invalid-params server detail survives `attach_prompt_usage`
/// wrapping `error.data` as `{message, promptUsage}`.
#[test]
fn format_acp_error_reads_detail_from_wrapped_data() {
    let bare = acp::Error::invalid_params().data("model does not support tools");
    assert_eq!(format_acp_error(&bare, false), "model does not support tools");
    let wrapped = acp::Error::invalid_params()
        .data(
            serde_json::json!({
            "message": "model does not support tools",
            "promptUsage": { "inputTokens": 12, "outputTokens": 0, "numTurns": 1 }
        }),
        );
    assert_eq!(format_acp_error(&wrapped, false), "model does not support tools");
}
#[test]
fn format_acp_error_formats_http_500_dump() {
    let err = acp::Error::internal_error()
        .data(
            serde_json::json!({
            "message": "API error (status 500 Internal Server Error): {\"error\":\"upstream exploded\"}",
            "http_status": 500
        }),
        );
    assert_eq!(
            format_acp_error(&err, false),
            "Server error (500): Something went wrong on our side. Wait a minute and send again."
        );
}
#[test]
fn format_acp_error_rate_limit_surfaces_detail_or_fallback() {
    use pi_shell::sampling::error::{
        FREE_USAGE_USER_MESSAGE, RATE_LIMITED_ERROR_CODE,
        RATE_LIMITED_USER_MESSAGE_API_KEY, RATE_LIMITED_USER_MESSAGE_OAUTH,
    };
    let cap_body = "The service is temporarily at capacity. Please retry your request shortly.";
    let capacity = acp::Error::new(RATE_LIMITED_ERROR_CODE, "Rate limited")
        .data(format!(
            "API error (status 429 Too Many Requests): {cap_body}"
        ));
    assert_eq!(format_acp_error(&capacity, false), cap_body);
    assert_eq!(format_acp_error(&capacity, true), cap_body);
    let rpm_body = "You are sending requests too quickly. Please slow down, or upgrade to a Grok subscription for higher limits: https://grok.com/supergrok";
    let rpm = acp::Error::new(RATE_LIMITED_ERROR_CODE, "Rate limited")
        .data(format!("API error (status 429 Too Many Requests): {rpm_body}"));
    assert!(format_acp_error(&rpm, false).contains("grok.com/supergrok"));
    assert_eq!(format_acp_error(&rpm, true), RATE_LIMITED_USER_MESSAGE_API_KEY);
    let empty = acp::Error::new(RATE_LIMITED_ERROR_CODE, "Rate limited");
    assert_eq!(format_acp_error(&empty, false), RATE_LIMITED_USER_MESSAGE_OAUTH);
    assert_eq!(format_acp_error(&empty, true), RATE_LIMITED_USER_MESSAGE_API_KEY);
    let free = acp::Error::new(RATE_LIMITED_ERROR_CODE, "Rate limited")
        .data(
            "API error (status 429 Too Many Requests): \
             subscription:free-usage-exhausted: You have used all your free usage.",
        );
    assert_eq!(format_acp_error(&free, false), FREE_USAGE_USER_MESSAGE);
    assert_eq!(format_acp_error(&free, true), FREE_USAGE_USER_MESSAGE);
    let free_wrapped = acp::Error::new(RATE_LIMITED_ERROR_CODE, "Rate limited")
        .data(
            serde_json::json!({
                "message": "API error (status 429 Too Many Requests): \
                    subscription:free-usage-exhausted: You have used all your free usage.",
                "promptUsage": { "inputTokens": 12, "outputTokens": 0, "numTurns": 1 }
            }),
        );
    assert_eq!(
            format_acp_error(&free_wrapped, false),
            FREE_USAGE_USER_MESSAGE
        );
}
/// Non-empty token ranges ride the wire block meta as `skillTokenRanges`
/// byte pairs; the text itself is untouched.
#[test]
fn plain_prompt_block_stamps_skill_token_ranges_meta() {
    let block = plain_prompt_content_block("great /pr-workflow go".into(), &[6..18]);
    let acp::ContentBlock::Text(tb) = block else {
        panic!("expected text block");
    };
    assert_eq!(tb.text, "great /pr-workflow go");
    let meta = tb.meta.expect("meta stamped when ranges non-empty");
    assert_eq!(meta["skillTokenRanges"], serde_json::json!([[6, 18]]));
}
/// Empty ranges keep `meta: None` — the legacy wire shape is unchanged.
#[test]
fn plain_prompt_block_no_meta_when_ranges_empty() {
    let block = plain_prompt_content_block("hello".into(), &[]);
    let acp::ContentBlock::Text(tb) = block else {
        panic!("expected text block");
    };
    assert_eq!(tb.text, "hello");
    assert!(tb.meta.is_none());
}
/// With a screen mode, `_meta` carries both `promptId` and `screenMode`
/// (the shell threads the latter into `prompt_submitted.screen_mode`).
#[test]
fn prompt_request_meta_stamps_screen_mode() {
    let meta = prompt_request_meta("p-1", Some("minimal"));
    assert_eq!(
            meta,
            serde_json::json!({ "promptId": "p-1", "screenMode": "minimal" })
        );
}
/// Without a screen mode (`SessionFlags::default()` in tests), the key is
/// omitted — the legacy `{"promptId": …}` wire shape stays byte-identical.
#[test]
fn prompt_request_meta_omits_screen_mode_when_unset() {
    let meta = prompt_request_meta("p-2", None);
    assert_eq!(meta, serde_json::json!({ "promptId": "p-2" }));
}
#[test]
fn picker_drops_conversation_without_updated_at_in_standard_acp_mode() {
    let payload = serde_json::json!({
            "sessions": [{
                "sessionId": "conv_abc",
                "cwd": "",
                "summary": "Compare GPU vendors",
                "source": "conversation",
                "_meta": { "pi/session": { "kind": "chat" } }
            }]
        });
    let entries = parse_session_picker_entries(&payload);
    assert!(
        entries.is_empty(),
        "legacy conversation rows without updatedAt are not special-cased"
    );
}
#[test]
fn picker_drops_old_conversation_past_cutoff_in_standard_acp_mode() {
    let payload = serde_json::json!({
            "sessions": [{
                "sessionId": "conv_old",
                "cwd": "",
                "summary": "Ancient chat",
                "source": "conversation",
                "updatedAt": "2020-01-01T00:00:00Z",
                "_meta": { "pi/session": { "kind": "chat" } }
            }]
        });
    let entries = parse_session_picker_entries(&payload);
    assert!(
        entries.is_empty(),
        "legacy conversation cutoff is not applied without vendor session kind"
    );
}
#[test]
fn picker_drops_local_with_missing_updated_at() {
    let payload = serde_json::json!({
            "sessions": [{
                "sessionId": "local_no_ts",
                "cwd": "/Users/me/pi",
                "summary": "no timestamp",
                "source": "local"
            }]
        });
    let entries = parse_session_picker_entries(&payload);
    assert!(
            entries.is_empty(),
            "local rows still require a parseable updatedAt"
        );
}
/// Untitled legacy conversation rows with empty summary are still dropped.
#[test]
fn picker_drops_untitled_conversation_with_empty_summary() {
    let payload = serde_json::json!({
            "sessions": [{
                "sessionId": "conv_untitled",
                "cwd": "",
                "summary": "",
                "source": "conversation",
                "updatedAt": "2026-07-01T00:00:00Z",
                "_meta": { "pi/session": { "kind": "chat" } }
            }]
        });
    let entries = parse_session_picker_entries(&payload);
    assert!(
        entries.is_empty(),
        "empty-summary rows stay dropped even with a timestamp"
    );
}
/// The recap and last-turn summary ride the session-list wire and land on
/// the picker entry so the expanded card can show them.
#[test]
fn picker_parses_last_recap_and_last_turn_summary() {
    let recent = chrono::Utc::now().to_rfc3339();
    let payload = serde_json::json!({
            "sessions": [{
                "sessionId": "s_recap",
                "cwd": "/Users/me/pi",
                "summary": "Auth refactor",
                "source": "local",
                "updatedAt": recent,
                "lastTurnSummary": "Wired retries into billing",
                "lastRecap": "Where we left off: auth refactor across the API"
            }]
        });
    let entries = parse_session_picker_entries(&payload);
    assert_eq!(entries.len(), 1);
    assert_eq!(
            entries[0].last_turn_summary.as_deref(),
            Some("Wired retries into billing")
        );
    assert_eq!(
            entries[0].last_recap.as_deref(),
            Some("Where we left off: auth refactor across the API")
        );
}
/// Canary: the empty-summary drop still applies to Build rows.
#[test]
fn picker_still_drops_build_row_with_empty_summary() {
    let payload = serde_json::json!({
            "sessions": [{
                "sessionId": "local_empty",
                "cwd": "/nonexistent/effects-test",
                "summary": "",
                "source": "local",
                "updatedAt": "2026-07-01T00:00:00Z"
            }]
        });
    let entries = parse_session_picker_entries(&payload);
    assert!(entries.is_empty(), "empty-summary Build rows stay dropped");
}
#[test]
fn parse_session_load_restore_meta_full_shape() {
    use pi_workspace::session::git::RestoreDegree;
    let meta = serde_json::json!({
            "codeRestore": {
                "restored": true,
                "summary": "checked out abc12345",
                "degree": "head_only",
            }
        });
    let (restored, summary, degree) = parse_session_load_restore_meta(meta.as_object());
    assert!(restored);
    assert_eq!(summary.as_deref(), Some("checked out abc12345"));
    assert_eq!(degree, Some(RestoreDegree::HeadOnly));
}
#[test]
fn parse_session_load_restore_meta_absent_returns_false() {
    let (restored, summary, degree) = parse_session_load_restore_meta(None);
    assert!(!restored);
    assert!(summary.is_none());
    assert!(degree.is_none());
}
#[test]
fn parse_session_load_restore_meta_no_coderestore_key() {
    let meta = serde_json::json!({ "other": 1 });
    let (restored, summary, degree) = parse_session_load_restore_meta(meta.as_object());
    assert!(!restored);
    assert!(summary.is_none());
    assert!(degree.is_none());
}
/// Parser must reject unknown degree strings in the meta path.
#[test]
fn parse_session_load_restore_meta_rejects_unknown_degree() {
    let meta = serde_json::json!({
            "codeRestore": { "restored": true, "summary": "x", "degree": "weird" }
        });
    let (_, _, degree) = parse_session_load_restore_meta(meta.as_object());
    assert!(degree.is_none());
}
#[test]
fn parse_session_response_models_prefers_native_payload() {
    let id = acp::ModelId::new(std::sync::Arc::from("native-model"));
    let native = acp::SessionModelState::new(
        id.clone(),
        vec![acp::ModelInfo::new(id.clone(), "Native Model")],
    );
    let meta = serde_json::json!({
        "pi/currentModelId": "meta-model",
        "pi/currentModelDisplayName": "Meta Model",
    });
    let parsed = parse_session_response_models(
        Some(native.clone()),
        None,
        meta.as_object(),
    );
    assert_eq!(parsed, Some(native));
}
#[test]
fn parse_session_response_models_falls_back_to_meta() {
    let meta = serde_json::json!({
        "pi/currentModelId": "deepseek-flash",
        "pi/currentModelDisplayName": "DeepSeek Flash",
        "pi/provider": "deepseek",
    });
    let parsed = parse_session_response_models(None, None, meta.as_object())
        .expect("meta model fallback should build SessionModelState");
    assert_eq!(parsed.current_model_id.0.as_ref(), "deepseek-flash");
    assert_eq!(parsed.available_models.len(), 1);
    assert_eq!(parsed.available_models[0].name, "DeepSeek Flash");
    assert_eq!(
        parsed.available_models[0]
            .meta
            .as_ref()
            .and_then(|m| m.get("provider"))
            .and_then(|v| v.as_str()),
        Some("deepseek")
    );
}
#[test]
fn parse_session_response_models_none_without_native_or_meta_model() {
    let meta = serde_json::json!({ "pi/provider": "deepseek" });
    let parsed = parse_session_response_models(None, None, meta.as_object());
    assert!(parsed.is_none(), "no model id means no fallback state");
}
/// The `model` option exactly as the Python `pi_agent_cli` agent serializes it
/// (`SessionConfigOptionSelect`, category `model`).
fn python_agent_model_config_option() -> acp::SessionConfigOption {
    serde_json::from_value(serde_json::json!({
        "id": "model",
        "name": "Model",
        "category": "model",
        "type": "select",
        "currentValue": "qwen/qwen3.8-27b:free",
        "options": [
            { "value": "qwen/qwen3.8-27b:free", "name": "Qwen 27B (free)" },
            { "value": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "description": "fast" },
        ],
    }))
    .expect("Python agent model option must deserialize into the Rust schema")
}
#[test]
fn parse_session_response_models_reads_model_config_option() {
    let options = vec![python_agent_model_config_option()];
    let parsed = parse_session_response_models(None, Some(options.as_slice()), None)
        .expect("a `model` select config option must yield a model catalog");
    assert_eq!(parsed.current_model_id.0.as_ref(), "qwen/qwen3.8-27b:free");
    assert_eq!(parsed.available_models.len(), 2);
    assert_eq!(parsed.available_models[0].name, "Qwen 27B (free)");
    assert_eq!(
        parsed.available_models[1].description.as_deref(),
        Some("fast")
    );
    // The config option id must travel with the state so `/model` can address
    // `session/set_config_option`.
    let state = crate::acp::model_state::ModelState::from(Some(parsed));
    assert_eq!(state.config_option_id.as_deref(), Some("model"));
    assert_eq!(state.available.len(), 2);
}
#[test]
fn parse_session_response_models_prefers_native_over_config_option() {
    let id = acp::ModelId::new(std::sync::Arc::from("native-model"));
    let native = acp::SessionModelState::new(
        id.clone(),
        vec![acp::ModelInfo::new(id, "Native Model")],
    );
    let options = vec![python_agent_model_config_option()];
    let parsed =
        parse_session_response_models(Some(native.clone()), Some(options.as_slice()), None);
    assert_eq!(parsed, Some(native));
}
#[test]
fn parse_session_response_models_ignores_non_model_config_options() {
    let mode = acp::SessionConfigOption::select(
        "mode",
        "Mode",
        "ask",
        vec![
            acp::SessionConfigSelectOption::new("ask", "Ask"),
            acp::SessionConfigSelectOption::new("code", "Code"),
        ],
    )
    .category(acp::SessionConfigOptionCategory::Mode);
    let options = vec![mode];
    let parsed = parse_session_response_models(None, Some(options.as_slice()), None);
    assert!(parsed.is_none(), "only category=model options feed /model");
}
#[test]
fn parse_session_response_models_flattens_grouped_model_options() {
    let option = acp::SessionConfigOption::select(
        "model",
        "Model",
        "b",
        acp::SessionConfigSelectOptions::Grouped(vec![
            acp::SessionConfigSelectGroup::new(
                "g1",
                "Group 1",
                vec![acp::SessionConfigSelectOption::new("a", "A")],
            ),
            acp::SessionConfigSelectGroup::new(
                "g2",
                "Group 2",
                vec![acp::SessionConfigSelectOption::new("b", "B")],
            ),
        ]),
    )
    .category(acp::SessionConfigOptionCategory::Model);
    let options = vec![option];
    let parsed = parse_session_response_models(None, Some(options.as_slice()), None)
        .expect("grouped options flatten into one catalog");
    assert_eq!(parsed.current_model_id.0.as_ref(), "b");
    let ids: Vec<&str> = parsed
        .available_models
        .iter()
        .map(|m| m.model_id.0.as_ref())
        .collect();
    assert_eq!(ids, ["a", "b"]);
}
/// Unknown keys return a descriptive error.
#[tokio::test]
async fn persist_setting_unknown_key_returns_err() {
    use crate::settings::SettingValue;
    let result = persist_setting("not-a-real-setting", SettingValue::Bool(true)).await;
    match result {
        Err(msg) => {
            assert!(
                msg.contains("unknown setting key"),
                "expected error to mention unknown setting key, got: {msg}",
            )
        }
        Ok(()) => panic!("expected Err for unknown key"),
    }
}
/// Type-mismatch returns Err (not panic) for spawned-task safety.
#[tokio::test]
async fn persist_setting_type_mismatch_errors_compact_mode() {
    use crate::settings::SettingValue;
    let r = persist_setting("compact_mode", SettingValue::String("nope".into())).await;
    let err = r.expect_err("compact_mode with String payload must return Err");
    assert!(
            err.contains("persist_setting(compact_mode) expected Bool"),
            "error message must mention key + expected kind, got: {err}",
        );
}
/// Type-mismatch for `show_timestamps`.
#[tokio::test]
async fn persist_setting_type_mismatch_errors_show_timestamps() {
    use crate::settings::SettingValue;
    let r = persist_setting("show_timestamps", SettingValue::String("nope".into()))
        .await;
    let err = r.expect_err("show_timestamps with String payload must return Err");
    assert!(
            err.contains("persist_setting(show_timestamps) expected Bool"),
            "error message must mention key + expected kind, got: {err}",
        );
}
/// Type-mismatch for `show_timeline`.
#[tokio::test]
async fn persist_setting_type_mismatch_errors_show_timeline() {
    use crate::settings::SettingValue;
    let r = persist_setting("show_timeline", SettingValue::String("nope".into())).await;
    let err = r.expect_err("show_timeline with String payload must return Err");
    assert!(
            err.contains("persist_setting(show_timeline) expected Bool"),
            "error message must mention key + expected kind, got: {err}",
        );
}
#[tokio::test]
async fn persist_setting_type_mismatch_errors_page_flip_on_send() {
    use crate::settings::SettingValue;
    let r = persist_setting("page_flip_on_send", SettingValue::String("nope".into()))
        .await;
    let err = r.expect_err("page_flip_on_send with String payload must return Err");
    assert!(
            err.contains("persist_setting(page_flip_on_send) expected Bool"),
            "got: {err}",
        );
}
#[tokio::test]
async fn persist_setting_type_mismatch_errors_combine_queued_prompts() {
    use crate::settings::SettingValue;
    let r = persist_setting(
            "combine_queued_prompts",
            SettingValue::String("nope".into()),
        )
        .await;
    let err = r.expect_err("combine_queued_prompts with String payload must return Err");
    assert!(
            err.contains("persist_setting(combine_queued_prompts) expected Bool"),
            "got: {err}",
        );
}
/// Type-mismatch for `simple_mode`.
#[tokio::test]
async fn persist_setting_type_mismatch_errors_simple_mode() {
    use crate::settings::SettingValue;
    let r = persist_setting("simple_mode", SettingValue::Int(42)).await;
    let err = r.expect_err("simple_mode with Int payload must return Err");
    assert!(
            err.contains("persist_setting(simple_mode) expected Bool"),
            "error message must mention key + expected kind, got: {err}",
        );
}
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
/// Spawn a fake ACP agent that counts `legacy ext RPC`
/// notifications. Exits when the channel closes.
fn spawn_fake_acp_agent(
    mut rx: tokio::sync::mpsc::UnboundedReceiver<pi_acp_lib::AcpAgentMessage>,
) -> Arc<AtomicUsize> {
    let counter = Arc::new(AtomicUsize::new(0));
    let counter_clone = counter.clone();
    tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if let pi_acp_lib::AcpAgentMessage::ExtNotification(args) = msg {
                if args.request.method.as_ref() == "pi/yolo_mode_changed" {
                    counter_clone.fetch_add(1, Ordering::SeqCst);
                }
                let _ = args.response_tx.send(Ok(()));
            }
        }
    });
    counter
}
/// Redirect `GROK_HOME` to a tempdir for test isolation.
fn setup_grok_home_in_tempdir() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().expect("tempdir creation");
    unsafe {
        std::env::set_var("GROK_HOME", tmp.path());
    }
    tmp
}
fn register_session_in(root: &std::path::Path, id: &str) -> acp::SessionId {
    use pi_active_sessions::{ActiveSession, register_in};
    let session_id = acp::SessionId::new(id);
    register_in(
            root,
            ActiveSession {
                session_id: session_id.clone(),
                pid: std::process::id(),
                cwd: "/tmp/test".into(),
                opened_at: chrono::Utc::now(),
            },
        )
        .expect("register");
    session_id
}
/// Lock-free: the helper removes the registry entry (the normal path).
#[test]
fn unregister_best_effort_removes_entry_when_lock_free() {
    let dir = tempfile::tempdir().expect("tempdir");
    let sid = register_session_in(dir.path(), "s1");
    unregister_active_session_best_effort_in(dir.path(), &sid);
    assert!(
            pi_active_sessions::list_in(dir.path())
                .expect("list")
                .is_empty(),
            "lock-free unregister must remove the entry",
        );
}
/// Contended: the quit path must skip the shared flock rather than block.
/// The unregister runs on a worker joined against a deadline so a blocking
/// regression fails fast here instead of deadlocking the test binary.
#[cfg(any(target_os = "linux", target_os = "macos"))]
#[test]
fn unregister_best_effort_is_nonblocking_under_lock_contention() {
    use std::os::unix::io::AsRawFd;
    use std::sync::mpsc;
    use std::time::Duration;
    let dir = tempfile::tempdir().expect("tempdir");
    let sid = register_session_in(dir.path(), "s1");
    let lock = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(dir.path().join("active_sessions.lock"))
        .expect("open lock");
    assert_eq!(unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) }, 0);
    let (tx, rx) = mpsc::channel();
    let root = dir.path().to_path_buf();
    let worker = std::thread::spawn(move || {
        unregister_active_session_best_effort_in(&root, &sid);
        let _ = tx.send(());
    });
    let returned = rx.recv_timeout(Duration::from_secs(2)).is_ok();
    assert_eq!(unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_UN) }, 0);
    worker.join().expect("worker thread");
    assert!(
            returned,
            "contended unregister blocked on the shared flock instead of skipping",
        );
    assert_eq!(
            pi_active_sessions::list_in(dir.path())
                .expect("list")
                .len(),
            1,
            "contended unregister must leave the entry for collect_crashed",
        );
}
/// A real I/O error (uncreatable registry root) is swallowed: the
/// best-effort helper logs and returns instead of panicking.
#[test]
fn unregister_best_effort_swallows_io_error() {
    let file = tempfile::NamedTempFile::new().expect("tempfile");
    let bad_root = file.path().join("not-a-dir");
    unregister_active_session_best_effort_in(&bad_root, &acp::SessionId::new("s1"));
}
/// BestEffort path fires exactly one ACP notification regardless
/// of disk outcome.
#[tokio::test]
async fn persist_permission_mode_acp_notification_fires_once_on_best_effort() {
    use agent_client_protocol as acp;
    let _guard = setup_grok_home_in_tempdir();
    let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
    let counter = spawn_fake_acp_agent(rx);
    let session_id = Some(acp::SessionId::new(Arc::from("test-session")));
    let result = persist_permission_mode_and_notify(
            "always-approve",
            session_id,
            PermissionModePersist::BestEffort,
            tx,
        )
        .await;
    tokio::task::yield_now().await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    assert_eq!(
            counter.load(Ordering::SeqCst),
            1,
            "ACP `pi/yolo_mode_changed` notification must fire exactly once \
             on BestEffort path (regardless of disk outcome)",
        );
    assert!(
            matches!(
                result,
                TaskResult::SettingPersisted { .. }
                    | TaskResult::SettingPersistFailedBestEffort { .. },
            ),
            "BestEffort path must return SettingPersisted (Ok) or \
             SettingPersistFailedBestEffort (Err), got {result:?}",
        );
}
/// WithRollback: notification count matches disk outcome
/// (1 on Ok, 0 on Err).
#[tokio::test]
async fn persist_permission_mode_acp_notification_gated_on_disk_for_with_rollback() {
    use agent_client_protocol as acp;
    let _guard = setup_grok_home_in_tempdir();
    let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
    let counter = spawn_fake_acp_agent(rx);
    let session_id = Some(acp::SessionId::new(Arc::from("test-session")));
    let result = persist_permission_mode_and_notify(
            "always-approve",
            session_id,
            PermissionModePersist::WithRollback("ask"),
            tx,
        )
        .await;
    tokio::task::yield_now().await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    let count = counter.load(Ordering::SeqCst);
    match result {
        TaskResult::SettingPersisted { .. } => {
            assert_eq!(
                    count, 1,
                    "WithRollback + disk Ok must fire ACP notification exactly once",
                );
        }
        TaskResult::SettingPersistFailed { .. } => {
            assert_eq!(
                    count, 0,
                    "WithRollback + disk Err must SUPPRESS the ACP notification \
                     (Issue 3 — keeps agent and pager state consistent on rollback)",
                );
        }
        other => {
            panic!("expected SettingPersisted or SettingPersistFailed, got {other:?}")
        }
    }
}
/// `session_id: None` suppresses ACP notification unconditionally.
#[tokio::test]
async fn persist_permission_mode_no_session_id_suppresses_acp() {
    let _guard = setup_grok_home_in_tempdir();
    let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
    let counter = spawn_fake_acp_agent(rx);
    let _result = persist_permission_mode_and_notify(
            "always-approve",
            None,
            PermissionModePersist::BestEffort,
            tx,
        )
        .await;
    tokio::task::yield_now().await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    assert_eq!(
            counter.load(Ordering::SeqCst),
            0,
            "session_id=None must suppress the ACP notification — sessionless \
             agents have no ACP channel to notify",
        );
}
/// BestEffort + disk failure must NOT return `SettingPersisted`.
#[tokio::test]
async fn persist_permission_mode_best_effort_failure_returns_dedicated_variant() {
    let _guard = setup_grok_home_in_tempdir();
    let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
    let _counter = spawn_fake_acp_agent(rx);
    let result = persist_permission_mode_and_notify(
            "always-approve",
            None,
            PermissionModePersist::BestEffort,
            tx,
        )
        .await;
    match result {
        TaskResult::SettingPersisted { key, value } => {
            assert_eq!(key, "permission_mode");
            assert_eq!(value, crate::settings::SettingValue::Enum("always-approve"));
        }
        TaskResult::SettingPersistFailedBestEffort { key, error: _ } => {
            assert_eq!(
                    key, "permission_mode",
                    "BestEffort failure MUST report key=permission_mode and \
                     NOT lie about success via SettingPersisted",
                );
        }
        other => {
            panic!(
                "BestEffort must return SettingPersisted (Ok) or \
                 SettingPersistFailedBestEffort (Err), got {other:?} — \
                 a regression to `SettingPersisted` on failure is Round-2 Issue 2",
            )
        }
    }
}
/// (Err, WithRollback) → SUPPRESS for all canonicals.
#[test]
fn should_send_yolo_acp_with_rollback_suppresses_on_err() {
    let result: Result<(), String> = Err("simulated disk failure".to_string());
    assert!(
            !should_send_yolo_acp_notification(&result, PermissionModePersist::WithRollback("ask")),
            "WithRollback + Err MUST suppress the ACP notification",
        );
    assert!(
            !should_send_yolo_acp_notification(
                &result,
                PermissionModePersist::WithRollback("always-approve")
            ),
            "WithRollback + Err MUST suppress regardless of the prior canonical",
        );
    assert!(
            !should_send_yolo_acp_notification(
                &result,
                PermissionModePersist::WithRollback("default")
            ),
            "WithRollback + Err MUST suppress for the 'default' prior canonical too",
        );
}
/// (Ok, WithRollback) → FIRE for all canonicals.
#[test]
fn should_send_yolo_acp_with_rollback_fires_on_ok() {
    let ok: Result<(), String> = Ok(());
    assert!(
            should_send_yolo_acp_notification(&ok, PermissionModePersist::WithRollback("ask")),
            "WithRollback + Ok must fire the ACP notification (happy path)",
        );
    assert!(
            should_send_yolo_acp_notification(
                &ok,
                PermissionModePersist::WithRollback("always-approve")
            ),
            "WithRollback + Ok fires regardless of the prior canonical",
        );
    assert!(
            should_send_yolo_acp_notification(&ok, PermissionModePersist::WithRollback("default")),
            "WithRollback + Ok fires for 'default' prior canonical too",
        );
}
#[test]
fn should_send_yolo_acp_best_effort_fires_on_both_outcomes() {
    let ok: Result<(), String> = Ok(());
    let err: Result<(), String> = Err("simulated".to_string());
    assert!(
            should_send_yolo_acp_notification(&ok, PermissionModePersist::BestEffort),
            "BestEffort + Ok must notify",
        );
    assert!(
            should_send_yolo_acp_notification(&err, PermissionModePersist::BestEffort),
            "BestEffort + Err must STILL notify (cycle_mode contract \
             — the cycle_mode state machine doesn't have a clean \
             single-field rollback)",
        );
}
#[test]
fn route_permission_mode_result_ok_returns_persisted() {
    let result = route_permission_mode_result(
        Ok(()),
        PermissionModePersist::WithRollback("ask"),
        "always-approve",
    );
    match result {
        TaskResult::SettingPersisted { key, value } => {
            assert_eq!(key, "permission_mode");
            assert_eq!(value, crate::settings::SettingValue::Enum("always-approve"));
        }
        other => panic!("Ok must return SettingPersisted, got {other:?}"),
    }
}
#[test]
fn route_permission_mode_result_err_with_rollback_off_routes_to_failed() {
    let result = route_permission_mode_result(
        Err("simulated".to_string()),
        PermissionModePersist::WithRollback("ask"),
        "always-approve",
    );
    match result {
        TaskResult::SettingPersistFailed { key, rollback_value, error } => {
            assert_eq!(key, "permission_mode");
            assert_eq!(rollback_value, crate::settings::SettingValue::Enum("ask"));
            assert_eq!(error, "simulated");
        }
        other => {
            panic!("WithRollback + Err must return SettingPersistFailed, got {other:?}")
        }
    }
}
#[test]
fn route_permission_mode_result_err_with_rollback_on_routes_to_failed() {
    let result = route_permission_mode_result(
        Err("simulated".to_string()),
        PermissionModePersist::WithRollback("always-approve"),
        "ask",
    );
    match result {
        TaskResult::SettingPersistFailed { key, rollback_value, error } => {
            assert_eq!(key, "permission_mode");
            assert_eq!(
                    rollback_value,
                    crate::settings::SettingValue::Enum("always-approve"),
                    "prev_canonical='always-approve' must route to canonical \
                     'always-approve' for rollback",
                );
            assert_eq!(error, "simulated");
        }
        other => {
            panic!("WithRollback + Err must return SettingPersistFailed, got {other:?}")
        }
    }
}
/// Rollback preserves "default" canonical (not collapsed to "ask").
#[test]
fn route_permission_mode_result_err_with_rollback_default_routes_to_failed() {
    let result = route_permission_mode_result(
        Err("simulated".to_string()),
        PermissionModePersist::WithRollback("default"),
        "always-approve",
    );
    match result {
        TaskResult::SettingPersistFailed { key, rollback_value, error } => {
            assert_eq!(key, "permission_mode");
            assert_eq!(
                    rollback_value,
                    crate::settings::SettingValue::Enum("default"),
                    "PR 11: prev_canonical='default' must roll back to canonical 'default', \
                     NOT collapse onto 'ask' through a bool projection",
                );
            assert_eq!(error, "simulated");
        }
        other => {
            panic!("WithRollback + Err must return SettingPersistFailed, got {other:?}")
        }
    }
}
/// Ok path preserves "default" canonical verbatim.
#[test]
fn route_permission_mode_result_ok_preserves_default_canonical() {
    let result = route_permission_mode_result(
        Ok(()),
        PermissionModePersist::WithRollback("ask"),
        "default",
    );
    match result {
        TaskResult::SettingPersisted { key, value } => {
            assert_eq!(key, "permission_mode");
            assert_eq!(
                    value,
                    crate::settings::SettingValue::Enum("default"),
                    "PR 11: 'default' canonical must survive the route fn intact",
                );
        }
        other => panic!("Ok must return SettingPersisted, got {other:?}"),
    }
}
/// `(Err, BestEffort)` must NOT return `SettingPersisted`.
#[test]
fn route_permission_mode_result_err_best_effort_routes_to_dedicated_variant() {
    let result = route_permission_mode_result(
        Err("simulated".to_string()),
        PermissionModePersist::BestEffort,
        "always-approve",
    );
    match result {
        TaskResult::SettingPersistFailedBestEffort { key, error } => {
            assert_eq!(key, "permission_mode");
            assert_eq!(error, "simulated");
        }
        TaskResult::SettingPersisted { .. } => {
            panic!(
                "BestEffort + Err MUST NOT return SettingPersisted — that would lie about \
                 success on disk failure (Round-2 Issue 2 regression)",
            )
        }
        other => {
            panic!("BestEffort + Err must return SettingPersistFailedBestEffort, got {other:?}",)
        }
    }
}
/// `FetchSessionList` uses standard `session/list` (cwd filter on the wire;
/// the picker query is applied locally). The `seq` is echoed back.
#[tokio::test]
async fn fetch_session_list_uses_standard_session_list() {
    use pi_acp_lib::AcpAgentMessage;
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if let AcpAgentMessage::ListSessions(args) = msg {
                let cwd = args
                    .request
                    .cwd
                    .as_ref()
                    .expect("session/list should pass cwd")
                    .to_string_lossy()
                    .into_owned();
                let recent = chrono::Utc::now().to_rfc3339();
                let sessions: Vec<agent_client_protocol::SessionInfo> = vec![
                    serde_json::from_value(serde_json::json!({
                        "sessionId": "sess-hit",
                        "cwd": cwd,
                        "title": "hit me",
                        "updatedAt": recent,
                    }))
                    .expect("session info"),
                    serde_json::from_value(serde_json::json!({
                        "sessionId": "sess-other",
                        "cwd": cwd,
                        "title": "other",
                        "updatedAt": recent,
                    }))
                    .expect("session info"),
                ];
                let resp = serde_json::from_value(serde_json::json!({
                    "sessions": sessions,
                }))
                .expect("list response");
                let _ = args.response_tx.send(Ok(resp));
            }
        }
    });
    let mut tasks = JoinSet::new();
    execute(
        Effect::FetchSessionList { seq: 8 },
        &mut tasks,
        &tx,
        Path::new("."),
        &SessionFlags::default(),
    );
    match tasks.join_next().await.expect("task").expect("no panic") {
        TaskResult::SessionListLoaded { sessions, seq } => {
            assert_eq!(seq, 8, "seq must be echoed, not reconstructed");
            let ids: Vec<&str> = sessions.iter().map(|s| s.id.as_str()).collect();
            assert_eq!(ids.len(), 2, "both sessions are returned unfiltered");
            assert!(ids.contains(&"sess-hit") && ids.contains(&"sess-other"));
        }
        other => panic!("expected SessionListLoaded, got {other:?}"),
    }
}
/// A scripted `session/list` agent: answers request number `n` with `pages[n]` (`None` ends the
/// script with an error). Every request's `(cwd, cursor)` is recorded.
type ListRequests = std::sync::Arc<std::sync::Mutex<Vec<(Option<String>, Option<String>)>>>;
fn scripted_list_agent(
    pages: impl Fn(usize) -> Option<serde_json::Value> + Send + 'static,
) -> (pi_acp_lib::AcpAgentTx, ListRequests) {
    use pi_acp_lib::AcpAgentMessage;
    let seen: ListRequests = Default::default();
    let seen_by_agent = seen.clone();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if let AcpAgentMessage::ListSessions(args) = msg {
                let mut seen = seen_by_agent.lock().unwrap();
                let n = seen.len();
                seen.push((
                    args.request.cwd.as_ref().map(|c| c.to_string_lossy().into_owned()),
                    args.request.cursor.clone(),
                ));
                drop(seen);
                let answer = match pages(n) {
                    Some(page) => Ok(serde_json::from_value(page).expect("list response")),
                    None => Err(acp::Error::new(acp::ErrorCode::InternalError.into(), "boom")),
                };
                let _ = args.response_tx.send(answer);
            }
        }
    });
    (tx, seen)
}
fn list_page(ids: &[&str], next_cursor: Option<&str>) -> serde_json::Value {
    let sessions: Vec<_> = ids
        .iter()
        .map(|id| serde_json::json!({
            "sessionId": id, "cwd": "/work", "title": id, "updatedAt": "2026-10-01T10:00:00Z",
        }))
        .collect();
    match next_cursor {
        Some(c) => serde_json::json!({ "sessions": sessions, "nextCursor": c }),
        None => serde_json::json!({ "sessions": sessions }),
    }
}
async fn run_fetch_session_list(tx: &pi_acp_lib::AcpAgentTx, seq: u64) -> TaskResult {
    let mut tasks = JoinSet::new();
    execute(
        Effect::FetchSessionList { seq },
        &mut tasks,
        tx,
        Path::new("/work"),
        &SessionFlags::default(),
    );
    tasks.join_next().await.expect("task").expect("no panic")
}
/// ACP: `nextCursor` present means more pages. The picker is filled from all of them, in order,
/// and each request carries the cwd and the cursor of the page before (the cursor is opaque:
/// it comes back exactly as it was sent).
#[tokio::test]
async fn fetch_session_list_follows_next_cursor() {
    let (tx, seen) = scripted_list_agent(|n| match n {
        0 => Some(list_page(&["s5", "s4"], Some("opaque/+=cursor 1"))),
        1 => Some(list_page(&["s3", "s2"], Some("c2"))),
        2 => Some(list_page(&["s1"], None)),
        _ => None,
    });
    match run_fetch_session_list(&tx, 3).await {
        TaskResult::SessionListLoaded { sessions, seq } => {
            assert_eq!(seq, 3);
            let ids: Vec<&str> = sessions.iter().map(|s| s.id.as_str()).collect();
            assert_eq!(ids, ["s5", "s4", "s3", "s2", "s1"], "all pages, in the agent's order");
        }
        other => panic!("expected SessionListLoaded, got {other:?}"),
    }
    let seen = seen.lock().unwrap().clone();
    let cursors: Vec<Option<&str>> = seen.iter().map(|(_, c)| c.as_deref()).collect();
    assert_eq!(cursors, [None, Some("opaque/+=cursor 1"), Some("c2")]);
    assert!(seen.iter().all(|(cwd, _)| cwd.as_deref() == Some("/work")), "cwd on every page");
}
/// A page without a cursor is the end: one request, as before pagination existed.
#[tokio::test]
async fn fetch_session_list_without_a_cursor_is_one_request() {
    let (tx, seen) = scripted_list_agent(|n| (n == 0).then(|| list_page(&["only"], None)));
    assert!(matches!(
        run_fetch_session_list(&tx, 1).await,
        TaskResult::SessionListLoaded { sessions, .. } if sessions.len() == 1
    ));
    assert_eq!(seen.lock().unwrap().len(), 1);
}
/// An agent that keeps handing out the cursor it already gave must not trap the picker in a loop:
/// the sessions received so far are shown.
#[tokio::test]
async fn fetch_session_list_stops_on_a_repeated_cursor() {
    let (tx, seen) = scripted_list_agent(|n| Some(list_page(&[format!("s{n}").as_str()], Some("same"))));
    match run_fetch_session_list(&tx, 1).await {
        TaskResult::SessionListLoaded { sessions, .. } => {
            let ids: Vec<&str> = sessions.iter().map(|s| s.id.as_str()).collect();
            assert_eq!(ids, ["s0", "s1"], "the page that repeated the cursor is kept");
        }
        other => panic!("expected SessionListLoaded, got {other:?}"),
    }
    assert_eq!(seen.lock().unwrap().len(), 2, "no third request");
}
/// An empty cursor is not a position to continue from.
#[tokio::test]
async fn fetch_session_list_treats_an_empty_cursor_as_the_end() {
    let (tx, seen) = scripted_list_agent(|n| (n == 0).then(|| list_page(&["a"], Some(""))));
    assert!(matches!(
        run_fetch_session_list(&tx, 1).await,
        TaskResult::SessionListLoaded { sessions, .. } if sessions.len() == 1
    ));
    assert_eq!(seen.lock().unwrap().len(), 1);
}
/// An agent whose cursors never end is cut off at `MAX_SESSION_LIST_PAGES`.
#[tokio::test]
async fn fetch_session_list_is_bounded_by_the_page_cap() {
    let (tx, seen) = scripted_list_agent(|n| {
        Some(list_page(&[format!("s{n}").as_str()], Some(format!("c{n}").as_str())))
    });
    match run_fetch_session_list(&tx, 1).await {
        TaskResult::SessionListLoaded { sessions, .. } => {
            assert_eq!(sessions.len(), MAX_SESSION_LIST_PAGES);
        }
        other => panic!("expected SessionListLoaded, got {other:?}"),
    }
    assert_eq!(seen.lock().unwrap().len(), MAX_SESSION_LIST_PAGES);
}
/// A failure on a later page fails the fill (and echoes `seq`): a picker that quietly stopped
/// half way would hide sessions.
#[tokio::test]
async fn fetch_session_list_fails_when_a_later_page_fails() {
    let (tx, _seen) = scripted_list_agent(|n| (n == 0).then(|| list_page(&["a"], Some("c1"))));
    match run_fetch_session_list(&tx, 7).await {
        TaskResult::SessionListFailed { seq, error } => {
            assert_eq!(seq, 7);
            assert!(error.contains("boom"), "error text is surfaced: {error}");
        }
        other => panic!("expected SessionListFailed, got {other:?}"),
    }
}
#[tokio::test]
async fn fetch_session_list_echoes_seq_on_error() {
    use pi_acp_lib::AcpAgentMessage;
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if let AcpAgentMessage::ListSessions(args) = msg {
                let _ = args.response_tx.send(Err(acp::Error::new(
                    acp::ErrorCode::InternalError.into(),
                    "boom",
                )));
            }
        }
    });
    let mut tasks = JoinSet::new();
    execute(
        Effect::FetchSessionList { seq: 9 },
        &mut tasks,
        &tx,
        Path::new("."),
        &SessionFlags::default(),
    );
    match tasks.join_next().await.expect("task").expect("no panic") {
        TaskResult::SessionListFailed { seq, error } => {
            assert_eq!(seq, 9, "seq must be echoed on failure too");
            assert!(error.contains("boom"), "error text is surfaced: {error}");
        }
        other => panic!("expected SessionListFailed, got {other:?}"),
    }
}
#[tokio::test]
async fn fetch_session_list_ignores_kind_facet_filter() {
    use std::sync::{Arc, Mutex};
    use pi_acp_lib::AcpAgentMessage;
    let captured: Arc<Mutex<usize>> = Arc::default();
    let captured_for_task = captured.clone();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if let AcpAgentMessage::ListSessions(args) = msg {
                *captured_for_task.lock().unwrap() += 1;
                let resp = serde_json::from_value(serde_json::json!({ "sessions": [] }))
                    .expect("list response");
                let _ = args.response_tx.send(Ok(resp));
            }
        }
    });
    let mut tasks = JoinSet::new();
    execute(
        Effect::FetchSessionList {
            seq: 1,
        },
        &mut tasks,
        &tx,
        Path::new("."),
        &SessionFlags::default(),
    );
    let _ = tasks.join_next().await;
    assert_eq!(*captured.lock().unwrap(), 1);
}
/// Verify that every profile name produced by `SessionFlags::agent_profile()`
/// is a valid `BuiltinAgentName` that the shell can resolve.
#[test]
fn agent_profile_names_are_valid_builtins() {
    use std::str::FromStr;
    use pi_agent::config::BuiltinAgentName;
    let test_cases: &[(SessionFlags, &str)] = &[
        (
            SessionFlags {
                plan_mode: true,
                subagents: true,
                ask_user: false,
                ..Default::default()
            },
            "grok-build-plan",
        ),
        (
            SessionFlags {
                plan_mode: true,
                subagents: false,
                ask_user: false,
                ..Default::default()
            },
            "grok-build-plan-no-subagents",
        ),
        (
            SessionFlags {
                plan_mode: true,
                subagents: true,
                ask_user: true,
                ..Default::default()
            },
            "grok-build-plan",
        ),
        (
            SessionFlags {
                plan_mode: true,
                subagents: false,
                ask_user: true,
                ..Default::default()
            },
            "grok-build-plan-no-subagents",
        ),
        (
            SessionFlags {
                plan_mode: false,
                subagents: false,
                ask_user: true,
                ..Default::default()
            },
            "grok-build-ask-user",
        ),
        (
            SessionFlags {
                plan_mode: false,
                subagents: true,
                ask_user: true,
                ..Default::default()
            },
            "grok-build-ask-user",
        ),
    ];
    for (flags, expected_name) in test_cases {
        let profile = flags.agent_profile();
        assert_eq!(
                profile,
                Some(*expected_name),
                "flags {flags:?} should produce profile {expected_name:?}"
            );
        let builtin = BuiltinAgentName::from_str(expected_name);
        assert!(
                builtin.is_ok(),
                "profile name {expected_name:?} is not a valid BuiltinAgentName: {:?}",
                builtin.err()
            );
    }
}
/// Default flags produce no agent profile (uses grok-build default).
#[test]
fn default_flags_produce_no_profile() {
    let flags = SessionFlags::default();
    assert_eq!(flags.agent_profile(), None);
}
/// --subagents alone produces no profile (grok-build already has TaskTool).
#[test]
fn subagents_without_plan_produces_no_profile() {
    let flags = SessionFlags {
        plan_mode: false,
        subagents: true,
        ask_user: false,
        ..Default::default()
    };
    assert_eq!(flags.agent_profile(), None);
}
/// Neutralize `GROK_AGENT` for the profile-matrix tests below: agent-driven
/// dev shells export it, which flips `to_meta` into the defer-to-shell
/// escape hatch and drops `agentProfile` — the tests would then assert the
/// wrong branch. Empty string counts as unset (`!s.trim().is_empty()`).
/// Callers must be `#[serial_test::serial(GROK_AGENT)]` (process-global env).
fn without_grok_agent() -> crate::test_util::EnvVarGuard {
    crate::test_util::EnvVarGuard::set("GROK_AGENT", "")
}
/// At the runtime defaults (every `--no-*` flag false → every
/// `SessionFlags` bool true via `!args.no_*`), `to_meta()` reflects the
/// full plan profile and no separate `askUserQuestion` toggle.
#[serial_test::serial(GROK_AGENT)]
#[test]
fn runtime_default_flags_produce_plan_meta() {
    let _env = without_grok_agent();
    let flags = SessionFlags {
        plan_mode: true,
        subagents: true,
        ask_user: true,
        ..Default::default()
    };
    let meta = flags.to_meta().unwrap();
    assert_eq!(meta["agentProfile"], "grok-build-plan");
    assert!(meta.get("askUserQuestion").is_none());
    assert_eq!(meta["yoloMode"], false);
}
/// --plan alone produces meta with `agentProfile` only and a
/// `askUserQuestion: false` since `ask_user` is off here.
#[serial_test::serial(GROK_AGENT)]
#[test]
fn plan_only_meta() {
    let _env = without_grok_agent();
    let flags = SessionFlags {
        plan_mode: true,
        subagents: false,
        ask_user: false,
        ..Default::default()
    };
    let meta = flags.to_meta().unwrap();
    assert_eq!(meta["agentProfile"], "grok-build-plan-no-subagents");
    assert_eq!(meta["askUserQuestion"], false);
    assert_eq!(meta["yoloMode"], false);
}
/// --plan --subagents selects the full plan profile.
#[serial_test::serial(GROK_AGENT)]
#[test]
fn plan_with_subagents_meta() {
    let _env = without_grok_agent();
    let flags = SessionFlags {
        plan_mode: true,
        subagents: true,
        ask_user: false,
        ..Default::default()
    };
    let meta = flags.to_meta().unwrap();
    assert_eq!(meta["agentProfile"], "grok-build-plan");
    assert_eq!(meta["askUserQuestion"], false);
    assert_eq!(meta["yoloMode"], false);
}
/// --ask-user alone selects the grok-build-ask-user profile.
#[serial_test::serial(GROK_AGENT)]
#[test]
fn ask_user_alone_meta() {
    let _env = without_grok_agent();
    let flags = SessionFlags {
        plan_mode: false,
        subagents: false,
        ask_user: true,
        ..Default::default()
    };
    let meta = flags.to_meta().unwrap();
    assert_eq!(meta["agentProfile"], "grok-build-ask-user");
    assert!(meta.get("askUserQuestion").is_none());
    assert_eq!(meta["yoloMode"], false);
}
/// --plan --ask-user: plan already includes ask-user; profile is plan.
#[serial_test::serial(GROK_AGENT)]
#[test]
fn plan_with_ask_user_uses_plan_profile() {
    let _env = without_grok_agent();
    let flags = SessionFlags {
        plan_mode: true,
        subagents: false,
        ask_user: true,
        ..Default::default()
    };
    let meta = flags.to_meta().unwrap();
    assert_eq!(meta["agentProfile"], "grok-build-plan-no-subagents");
    assert!(meta.get("askUserQuestion").is_none());
    assert_eq!(meta["yoloMode"], false);
}
/// --no-plan --no-subagents --no-ask-user picks the default profile but
/// must still emit `askUserQuestion: false` so the shell can strip the
/// tool at the builder. Mirrors the runtime: `subagents` toggle alone
/// does not need an `agentProfile` (default `grok-build` already has it).
#[test]
fn subagents_alone_emits_only_ask_user_question_disable() {
    let flags = SessionFlags {
        plan_mode: false,
        subagents: true,
        ask_user: false,
        ..Default::default()
    };
    let meta = flags.to_meta().expect("askUserQuestion=false must produce meta");
    assert!(meta.get("agentProfile").is_none());
    assert_eq!(meta["askUserQuestion"], false);
}
/// All three flags on at the runtime default produce grok-build-plan
/// and no `askUserQuestion` field.
#[serial_test::serial(GROK_AGENT)]
#[test]
fn all_flags_meta() {
    let _env = without_grok_agent();
    let flags = SessionFlags {
        plan_mode: true,
        subagents: true,
        ask_user: true,
        ..Default::default()
    };
    let meta = flags.to_meta().unwrap();
    assert_eq!(meta["agentProfile"], "grok-build-plan");
    assert!(meta.get("askUserQuestion").is_none());
    assert_eq!(meta["yoloMode"], false);
}
/// `--no-ask-user` is the user-discovered bug — the flag must surface
/// as `_meta.askUserQuestion = false` regardless of which profile (if
/// any) the other flags select.
#[test]
fn to_meta_emits_ask_user_question_false_when_disabled() {
    for plan in [false, true] {
        for subagents in [false, true] {
            let flags = SessionFlags {
                plan_mode: plan,
                subagents,
                ask_user: false,
                ..Default::default()
            };
            let meta = flags
                .to_meta()
                .unwrap_or_else(|| {
                    panic!(
                        "ask_user=false must always emit meta (plan={plan}, subagents={subagents})"
                    )
                });
            assert_eq!(
                    meta["askUserQuestion"], false,
                    "askUserQuestion must be false (plan={plan}, subagents={subagents}); meta={meta:?}"
                );
        }
    }
}
/// Symmetric positive control: when `ask_user` is enabled the field is
/// omitted entirely (the shell defaults to enabled when the key is
/// absent — see `parse_ask_user_question_from_meta`).
#[test]
fn to_meta_omits_ask_user_question_when_enabled() {
    for plan in [false, true] {
        for subagents in [false, true] {
            let flags = SessionFlags {
                plan_mode: plan,
                subagents,
                ask_user: true,
                ..Default::default()
            };
            if let Some(meta) = flags.to_meta() {
                assert!(
                        meta.get("askUserQuestion").is_none(),
                        "askUserQuestion must be absent when enabled (plan={plan}, subagents={subagents}); meta={meta:?}"
                    );
            }
        }
    }
}
#[test]
fn to_meta_emits_auto_mode_when_enabled() {
    let flags = SessionFlags {
        auto_mode: true,
        yolo_mode: false,
        ..Default::default()
    };
    let meta = flags.to_meta().expect("auto_mode must emit meta");
    assert_eq!(meta["autoMode"], true);
    assert_eq!(
            meta["yoloMode"], false,
            "yoloMode must be explicitly false, not omitted (absent key falls \
             back to the shell's connect-time default / leader injection)"
        );
}
#[test]
fn create_permission_override_replaces_global_permission_seeds() {
    let flags = SessionFlags {
        yolo_mode: true,
        auto_mode: false,
        ..Default::default()
    };
    let mut meta = flags.to_meta();
    apply_permission_mode_override(&mut meta, Some(PermissionModeKind::Auto));
    let meta = meta.expect("permission metadata");
    assert_eq!(meta["yoloMode"], false);
    assert_eq!(meta["autoMode"], true);
}
/// yoloMode must ride the meta explicitly for BOTH polarities — absent
/// key ≠ off (see the emit-site comment in `to_meta`). Pins the
/// pre-session Always-Approve → Normal cycle not creating a yolo session.
#[test]
fn to_meta_always_emits_yolo_mode_explicitly() {
    for yolo in [false, true] {
        let flags = SessionFlags {
            yolo_mode: yolo,
            ..Default::default()
        };
        let meta = flags.to_meta().expect("permission seeds must always emit meta");
        assert_eq!(
                meta["yoloMode"],
                serde_json::json!(yolo),
                "yoloMode must be explicit (yolo={yolo}); meta={meta:?}"
            );
    }
}
#[test]
fn to_meta_yolo_suppresses_auto_mode() {
    let flags = SessionFlags {
        auto_mode: true,
        yolo_mode: true,
        ..Default::default()
    };
    let meta = flags.to_meta().expect("yolo must emit meta");
    assert_eq!(meta["yoloMode"], true);
    assert_eq!(
            meta["autoMode"], false,
            "yolo wins; autoMode must be explicitly false (not omitted)"
        );
}
/// Verify that each resolved profile name produces a valid
/// `AgentDefinition` whose name matches the expected kebab-case string.
#[test]
fn agent_profile_definitions_have_correct_names() {
    use std::str::FromStr;
    use pi_agent::config::BuiltinAgentName;
    for name in [
        "grok-build-plan",
        "grok-build-plan-no-subagents",
        "grok-build-ask-user",
    ] {
        let builtin = BuiltinAgentName::from_str(name).unwrap();
        let def = builtin.definition();
        assert_eq!(
                def.name, name,
                "definition name should match the kebab-case profile name"
            );
    }
}
#[test]
fn session_picker_summary_strips_skill_xml() {
    use pi_tools::implementations::skills::skill::extract_skill_display_text;
    let summary = "<command-name>pr-babysit</command-name>\n\
                        <command-message>/pr-babysit</command-message>\n\
                        <command-args>check</command-args>"
        .to_string();
    let display = extract_skill_display_text(&summary).unwrap_or(summary);
    assert_eq!(display, "/pr-babysit check");
}
#[test]
fn session_picker_summary_preserves_normal_text() {
    use pi_tools::implementations::skills::skill::extract_skill_display_text;
    let summary = "Fix authentication bug in login flow".to_string();
    let display = extract_skill_display_text(&summary).unwrap_or(summary);
    assert_eq!(display, "Fix authentication bug in login flow");
}
#[test]
fn sanitize_user_error_strips_auth_prefixes() {
    assert_eq!(
            sanitize_user_error(
                "Authentication required: Login timed out after 10 minutes. Please try again."
            ),
            "Login timed out after 10 minutes. Please try again."
        );
    assert_eq!(
            sanitize_user_error("Authentication failed: something went wrong"),
            "something went wrong"
        );
    assert_eq!(
            sanitize_user_error("Login timed out after 10 minutes. Please try again."),
            "Login timed out after 10 minutes. Please try again."
        );
}
#[test]
fn sanitize_user_error_collapses_disk_full() {
    assert_eq!(
            sanitize_user_error(
                "couldn't create worktree: Internal error: \"hub error: Worktree creation failed: not enough free disk space\""
            ),
            "No space left on device"
        );
    assert_eq!(
            sanitize_user_error(
                "couldn't create worktree: failed to copy index: No space left on device (os error 28)"
            ),
            "No space left on device"
        );
    assert_eq!(
            sanitize_user_error("Internal error: \"Disk quota exceeded or out of space.\""),
            "No space left on device"
        );
    assert_eq!(
            sanitize_user_error("couldn't create worktree: failed to get HEAD commit from source"),
            "couldn't create worktree: failed to get HEAD commit from source"
        );
}
