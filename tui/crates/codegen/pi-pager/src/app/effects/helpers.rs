#![cfg_attr(rustfmt, rustfmt::skip)]
use std::path::Path;
use agent_client_protocol as acp;
use tokio::task::JoinSet;
use pi_acp_lib::{AcpAgentTx, acp_send};
use super::actions::{PermissionModePersist, TaskResult};
use super::agent::AgentId;
use crate::acp::model_state::MODEL_CONFIG_ID_META_KEY;
use crate::unified_log as ulog;
use pi_shell::sampling::error::{
    RATE_LIMITED_ERROR_CODE, error_detail_from_data, format_rate_limited_user_message,
    http_status_from_error,
};
/// Floor for the session create/load RPCs.
const SESSION_RPC_FLOOR: std::time::Duration = std::time::Duration::from_secs(180);
/// Headroom over the agent-side `.envrc` budget for the rest of session setup.
const SESSION_RPC_SLACK: std::time::Duration = std::time::Duration::from_secs(50);
/// Always covers the agent-side `.envrc` budget so the backstop cannot fire
/// before the agent's own deadline. Reads `GROK_ENVRC_TIMEOUT_SECS` in this
/// process; the agent inherits the same environment.
pub(super) fn session_rpc_timeout() -> std::time::Duration {
    SESSION_RPC_FLOOR.max(pi_workspace::envrc::loader_budget() + SESSION_RPC_SLACK)
}
/// `acp_send` bounded by [`session_rpc_timeout`]; on expiry, an error naming
/// `action` instead of an eternal spinner.
pub(super) async fn acp_send_bounded<R, T>(
    request: T,
    tx: &tokio::sync::mpsc::UnboundedSender<R>,
    action: &str,
) -> Result<T::Response, acp::Error>
where
    T: pi_acp_lib::AcpRequest,
    R: From<pi_acp_lib::AcpArgs<T>> + std::fmt::Debug,
{
    let timeout = session_rpc_timeout();
    match tokio::time::timeout(timeout, acp_send(request, tx)).await {
        Ok(result) => result,
        Err(_elapsed) => {
            Err(
                acp::Error::new(
                    acp::ErrorCode::InternalError.into(),
                    format!(
                "{action} timed out after {}s. It may still finish in the background; \
                 retrying right away can run into the same delay.",
                timeout.as_secs()
            ),
                ),
            )
        }
    }
}
pub(super) fn log_prompt_result(
    session_id: &acp::SessionId,
    result: &Result<acp::PromptResponse, acp::Error>,
) {
    let sid = &session_id.0;
    match result {
        Ok(_) => ulog::info("agent response complete", Some(sid), None),
        Err(e) => {
            ulog::error(
                "agent response failed",
                Some(sid),
                Some(serde_json::json!({"error": e.to_string()})),
            )
        }
    }
}
/// Upper bound on the off-thread clipboard-attachment probe. A wedged osascript
/// read must not pin `paste_probe_in_flight` and silently stash every later send.
pub(super) const CLIPBOARD_PROBE_TIMEOUT_SECS: u64 = 10;
/// Convert an ACP error to a user-friendly string for display.
/// Rate-limit errors: free-usage paywall, else server detail (with API-key
/// rewrite when the body pushes personal SuperGrok), else auth-aware fallback
/// (see [`format_rate_limited_user_message`]).
/// All other errors render as the formatted request-failure banner text
/// (status headline + sanitized detail).
pub(super) fn format_acp_error(err: &acp::Error, is_api_key_auth: bool) -> String {
    if i32::from(err.code) == RATE_LIMITED_ERROR_CODE {
        let detail = err.data.as_ref().and_then(error_detail_from_data);
        return sanitize_user_error(
            &format_rate_limited_user_message(detail.as_deref(), is_api_key_auth),
        );
    }
    if err.code == acp::ErrorCode::InvalidParams && let Some(data) = &err.data
        && let Some(msg) = error_detail_from_data(data) && !msg.is_empty()
    {
        return sanitize_user_error(&msg);
    }
    let raw = err
        .data
        .as_ref()
        .and_then(error_detail_from_data)
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| err.to_string());
    crate::app::error_display::format_request_failure(
            http_status_from_error(err),
            None,
            &raw,
        )
        .message()
}
/// CANONICAL wire parser for `LoadSessionResponse._meta.codeRestore`. Any
/// other code consuming this shape MUST go through this function — do not
/// re-implement.
pub(super) fn parse_session_load_restore_meta(
    resp_meta: Option<&acp::Meta>,
) -> (bool, Option<String>, Option<pi_workspace::session::git::RestoreDegree>) {
    let code_restore = resp_meta.and_then(|m| m.get("codeRestore"));
    let code_restored = code_restore
        .and_then(|r| r.get("restored"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let restore_summary = code_restore
        .and_then(|r| r.get("summary"))
        .and_then(|v| v.as_str())
        .map(String::from);
    let restore_degree = code_restore
        .and_then(|r| r.get("degree"))
        .cloned()
        .and_then(|v| serde_json::from_value(v).ok());
    (code_restored, restore_summary, restore_degree)
}
/// CANONICAL wire parser for `LoadSessionResponse._meta key`.
///
/// Returns the session's in-flight running prompt id when the session was
/// loaded MID-turn (some other client is driving), otherwise `None`. The
/// loader adopts this id so subsequent live `session/update` deltas pass the
/// `current_prompt_id` gate (see `app/acp_handler.rs`). `pub(super)` for the
/// reconnect re-init in `event_loop.rs`, which reads the same response meta.
pub(crate) fn parse_session_load_running_prompt_id(
    _resp_meta: Option<&acp::Meta>,
) -> Option<String> {
    None
}
/// CANONICAL wire parser for the `session/new` / `session/load` response
/// `_meta[SCHEDULER_BACKGROUND_LOOPS_META_KEY]`.
///
/// Carries whether THIS session's scheduled fires run as detached background
/// subagents, as the shell resolved it when the session's actor spawned. The
/// pager stores it per session and must not re-resolve the setting: a
/// mid-session flip would then make `/loop`'s wording describe a runtime the
/// already-spawned session will never use. `None` when the shell predates the
/// key (or for gateway chat sessions, which have no local fires), leaving the
/// reader on the startup seed.
pub(crate) fn parse_session_scheduler_background_loops(
    resp_meta: Option<&acp::Meta>,
) -> Option<bool> {
    resp_meta
        .and_then(|m| {
            m.get(pi_shell::session::SCHEDULER_BACKGROUND_LOOPS_META_KEY)
        })
        .and_then(|v| v.as_bool())
}
/// Model-state parser for the `session/new` / `session/load` responses.
///
/// Priority:
/// 1. `models` when present (native unstable payload).
/// 2. The standard ACP Session Config Option with `category: model` (a `select`):
///    its values become the `/model` catalog and its id is remembered so the switch can
///    use `session/set_config_option`.
/// 3. Else read `_meta` keys (legacy pi-python hints) and synthesize a single-entry
///    model catalog so status surfaces can render a real name.
pub(super) fn parse_session_response_models(
    models: Option<acp::SessionModelState>,
    config_options: Option<&[acp::SessionConfigOption]>,
    resp_meta: Option<&acp::Meta>,
) -> Option<acp::SessionModelState> {
    if models.is_some() {
        return models;
    }
    if let Some(state) = config_options.and_then(model_state_from_config_options) {
        return Some(state);
    }
    let meta = resp_meta?;
    let model_id = parse_non_empty_meta_string(
        meta,
        &["pi/currentModelId", "currentModelId", "modelId", "model"],
    )?;
    let display_name = parse_non_empty_meta_string(
        meta,
        &[
            "pi/currentModelDisplayName",
            "currentModelDisplayName",
            "modelDisplayName",
            "modelName",
        ],
    )
    .unwrap_or_else(|| model_id.clone());

    let id = acp::ModelId::new(model_id);
    let mut info = acp::ModelInfo::new(id.clone(), display_name);
    if let Some(provider) = parse_non_empty_meta_string(meta, &["pi/provider", "provider"]) {
        info = info.meta(
            serde_json::json!({
                "provider": provider,
            })
            .as_object()
            .cloned(),
        );
    }
    Some(acp::SessionModelState::new(id, vec![info]))
}

/// Synthesize a model catalog from the first `select` Session Config Option whose category
/// is `model`. The option id is stored in the state's `_meta` ([`MODEL_CONFIG_ID_META_KEY`]).
fn model_state_from_config_options(
    options: &[acp::SessionConfigOption],
) -> Option<acp::SessionModelState> {
    let (option, select) = options.iter().find_map(|option| {
        match (&option.category, &option.kind) {
            (
                Some(acp::SessionConfigOptionCategory::Model),
                acp::SessionConfigKind::Select(select),
            ) => Some((option, select)),
            _ => None,
        }
    })?;
    let flat: Vec<&acp::SessionConfigSelectOption> = match &select.options {
        acp::SessionConfigSelectOptions::Ungrouped(opts) => opts.iter().collect(),
        acp::SessionConfigSelectOptions::Grouped(groups) => {
            groups.iter().flat_map(|group| group.options.iter()).collect()
        }
        _ => Vec::new(),
    };
    let available: Vec<acp::ModelInfo> = flat
        .into_iter()
        .map(|value| {
            let info = acp::ModelInfo::new(
                acp::ModelId::new(value.value.0.clone()),
                value.name.clone(),
            );
            match &value.description {
                Some(description) => info.description(description.clone()),
                None => info,
            }
        })
        .collect();
    if available.is_empty() {
        return None;
    }
    let mut meta = acp::Meta::new();
    meta.insert(
        MODEL_CONFIG_ID_META_KEY.to_string(),
        serde_json::Value::String(option.id.0.to_string()),
    );
    Some(
        acp::SessionModelState::new(acp::ModelId::new(select.current_value.0.clone()), available)
            .meta(meta),
    )
}

fn parse_non_empty_meta_string(meta: &acp::Meta, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| {
        meta.get(*key)
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToString::to_string)
    })
}
/// Whether `raw` is (or wraps) a disk-full / ENOSPC failure.
pub(crate) fn is_disk_full_error(raw: &str) -> bool {
    raw.contains(pi_fast_worktree::OUT_OF_DISK_CONTEXT)
        || raw.contains(pi_fast_worktree::ENOSPC_OS_MESSAGE)
        || raw.contains("Disk quota exceeded") || raw.contains("Out of disk space")
}
/// Sanitize an error string before showing it to the user.
///
/// Strips protocol jargon (ACP, JSON-RPC) and other technical noise that would
/// be meaningless in a toast, and collapses known disk-full markers.
pub(crate) fn sanitize_user_error(raw: &str) -> String {
    if is_disk_full_error(raw) {
        return pi_fast_worktree::ENOSPC_OS_MESSAGE.to_string();
    }
    static REPLACEMENTS: &[(&str, &str)] = &[
        ("cli-chat-proxy", "server"),
        ("cli_chat_proxy", "server"),
        ("inference-api", "server"),
        ("inference_api", "server"),
        ("research-api", "server"),
        ("research_api", "server"),
        ("grok-code-backend", "server"),
        ("ACP error:", "error:"),
        ("ACP request failed:", "request failed:"),
        ("JSON-RPC error", "request error"),
        ("acp_send", "request"),
        ("ExtRequest", "request"),
        ("ExtNotification", "notification"),
        ("Authentication required: ", ""),
        ("Authentication failed: ", ""),
    ];
    let mut result = raw.to_string();
    for (pattern, replacement) in REPLACEMENTS {
        result = result.replace(pattern, replacement);
    }
    if result.chars().count() > 200 {
        let truncated: String = result.chars().take(180).collect();
        result = format!("{truncated}...");
    }
    result
}
/// Additive session creation flags passed from CLI → AppView → effects.
///
/// The flags map to built-in `BuiltinAgentName` profiles (`agentProfile`)
/// and, independently, gate the `ask_user_question` tool at the builder
/// (`askUserQuestion`). `--no-ask-user` always strips the tool, regardless
/// of which profile was selected.
///
/// The `askUserQuestion` column is the value the pager stamps into `_meta`;
/// `omitted` means the shell resolves the gate itself (default ON).
///
/// | plan  | subagents | ask-user | agentProfile                   | askUserQuestion    |
/// |-------|-----------|----------|--------------------------------|--------------------|
/// | false | false     | false    | `grok-build` (default)         | `false`            |
/// | false | true      | false    | `grok-build` (default)         | `false`            |
/// | false | false     | true     | `grok-build-ask-user`          | omitted (shell gate) |
/// | false | true      | true     | `grok-build-ask-user`          | omitted (shell gate) |
/// | true  | false     | false    | `grok-build-plan-no-subagents` | `false`            |
/// | true  | true      | false    | `grok-build-plan`              | `false`            |
/// | true  | false     | true     | `grok-build-plan-no-subagents` | omitted (shell gate) |
/// | true  | true      | true     | `grok-build-plan`              | omitted (shell gate) |
///
/// When [`Self::chat_mode`] is set (gateway light-frontend / `--chat`), Build
/// `agentProfile` injection is omitted (K12) and `_meta key.kind`
/// is stamped `"chat"` so the shell takes `require_gateway` / thin profile.
#[derive(Debug, Clone, Default)]
pub(crate) struct SessionFlags {
    pub plan_mode: bool,
    pub subagents: bool,
    pub ask_user: bool,
    pub agent_override: Option<serde_json::Value>,
    /// Always-approve for this session (`_meta.yoloMode`).
    pub yolo_mode: bool,
    /// Auto (classifier) permission mode (`_meta.autoMode`). Mutually exclusive
    /// with `yolo_mode` on the agent; both may be set only if yolo wins at spawn.
    pub auto_mode: bool,
    /// Gateway light-frontend (`kind: "chat"`) — `--chat` / `/chat`.
    /// Mutual exclusivity with Build plan profiles: profiles are omitted and a
    /// warn is logged when plan flags are also set (K12).
    pub chat_mode: bool,
    /// Local-workspace stamp for ACP `_meta` (scrub still strips envId / Direct hub).
    #[cfg(feature = "local-workspace")]
    pub local_workspace: Option<crate::app::session_startup::LocalWorkspaceConfig>,
    /// Effective screen mode label (`ScreenMode::meta_label`), stamped into
    /// every `PromptRequest._meta.screenMode` for minimal-vs-regular usage
    /// telemetry. `None` (key omitted) only under `Default` in tests; real
    /// launches always know their mode.
    pub screen_mode_label: Option<&'static str>,
    /// Active auth is API key (not OAuth/session). Drives rate-limit copy in
    /// `format_acp_error`. Default `false` (OAuth copy) for tests.
    pub is_api_key_auth: bool,
}
impl SessionFlags {
    /// Resolve the agent profile name from the flags.
    ///
    /// Returns `None` for the default `grok-build` profile (no `_meta`
    /// needed; it already includes TaskTool). Chat mode never injects a
    /// Build profile (remote owns agent behavior).
    pub(super) fn agent_profile(&self) -> Option<&'static str> {
        if self.chat_mode {
            return None;
        }
        match (self.plan_mode, self.subagents, self.ask_user) {
            (true, true, _) => Some("grok-build-plan"),
            (true, false, _) => Some("grok-build-plan-no-subagents"),
            (false, _, true) => Some("grok-build-ask-user"),
            (false, _, false) => None,
        }
    }
    /// Build the `_meta` JSON value for ACP `NewSessionRequest` / `LoadSessionRequest`.
    ///
    /// In practice always `Some`: the permission seeds (`yoloMode` /
    /// `autoMode`) are emitted unconditionally (absent key ≠ off; see the
    /// emit-site comment below). `--no-ask-user` always forces
    /// `askUserQuestion: false` into the meta, even when paired with
    /// `GROK_AGENT` — the env var chooses the *agent*, but the tool-strip is
 /// independent. Chat mode additionally stamps `legacy ext RPC`.
    pub(crate) fn to_meta(&self) -> Option<acp::Meta> {
        let mut meta = serde_json::Map::new();
        if self.chat_mode {
            if self.plan_mode || self.agent_override.is_some()
                || std::env::var("GROK_AGENT").ok().is_some_and(|s| !s.trim().is_empty())
            {
                tracing::warn!(
                    "chat mode active: omitting Build agentProfile (plan/agent override ignored)"
                );
            }
        } else if let Some(ref profile) = self.agent_override {
            meta.insert("agentProfile".into(), profile.clone());
        } else if std::env::var("GROK_AGENT").ok().is_some_and(|s| !s.trim().is_empty())
        {} else if let Some(profile) = self.agent_profile() {
            meta.insert("agentProfile".into(), serde_json::json!(profile));
        }
        if self.chat_mode {
            #[cfg(feature = "local-workspace")]
            if let Some(ref lw) = self.local_workspace {
                stamp_local_workspace_meta(&mut meta, lw);
            }
        }
        if !self.ask_user {
            meta.insert("askUserQuestion".into(), serde_json::json!(false));
        }
        meta.insert("yoloMode".into(), serde_json::json!(self.yolo_mode));
        meta.insert(
            "autoMode".into(),
            serde_json::json!(super::dispatch::effective_auto(
                self.yolo_mode,
                self.auto_mode
            )),
        );
        meta.retain(|k, _| !crate::acp::vendor::is_vendor_meta_key(k));
        let mut result = if meta.is_empty() { None } else { Some(meta) };
        if self.chat_mode {
            apply_chat_kind_meta(&mut result);
        }
        result
    }
}
/// Workspace-bind `_meta` keys **always** forbidden on chat create/load.
///
/// `legacy ext RPC` is intentionally omitted: scrub keeps it
/// iff `legacy ext RPC`.
#[allow(dead_code)]
pub(super) const CHAT_FORBIDDEN_WORKSPACE_BIND_KEYS: &[&str] = &[
    "envId",
    "cloud_server_id",
];
/// FS-only tool ids for local existing workspace (chat attach/own).
#[cfg(feature = "local-workspace")]
pub(super) const LOCAL_WORKSPACE_FS_ONLY_TOOL_IDS: &[&str] = &[
    "workspace.fs_list",
    "workspace.fs_exists",
    "workspace.fs_read_file",
    "workspace.fs_write_file",
    "workspace.fs_delete_file",
    "workspace.put_files",
    "workspace.get_files",
];
/// Strip Build `agentProfile` in chat mode. Do not stamp grok `legacy ext RPC`.
pub(super) fn apply_chat_kind_meta(meta: &mut Option<acp::Meta>) {
    let obj = meta.get_or_insert_with(acp::Meta::new);
    obj.remove("agentProfile");
    obj.insert("pi/session".into(), serde_json::json!({ "kind": "chat" }));
    obj.retain(|k, _| !crate::acp::vendor::is_vendor_meta_key(k));
}
/// Stamp chat+local intent. Attach also stamps `legacy ext RPC`.
/// Own leaves `server_id` unset — shell supervisor mints before handshake.
///
/// Never stamps `envId` or `legacy ext RPC`.
#[cfg(feature = "local-workspace")]
pub(super) fn stamp_local_workspace_meta(
    meta: &mut serde_json::Map<String, serde_json::Value>,
    cfg: &crate::app::session_startup::LocalWorkspaceConfig,
) {
    use crate::app::session_startup::LocalWorkspaceMode;
    let mut local = serde_json::Map::new();
    let mode = match cfg.mode {
        LocalWorkspaceMode::Attach => "attach",
        LocalWorkspaceMode::Own => "own",
    };
    local.insert("mode".into(), serde_json::json!(mode));
    if let Some(ref sid) = cfg.server_id {
        local.insert("server_id".into(), serde_json::json!(sid));
    }
    if let Some(ref cwd) = cfg.cwd {
        local
            .insert("cwd".into(), serde_json::json!(cwd.to_string_lossy().into_owned()));
    }
    // Phase 4: do not stamp legacy vendor ext workspace bind keys onto standard ACP.
    let _ = (local, meta, cfg);
    tracing::debug!(
        target: crate::views::welcome::workspace_mode::WORKSPACE_MODE_LOG,
        event = "acp_meta_stamped_skipped",
        mode,
        "skipping local_workspace on session meta (standard ACP only)"
    );
}
/// Apply [`stamp_local_workspace_meta`] onto optional ACP meta.
#[cfg(feature = "local-workspace")]
pub(super) fn apply_local_workspace_meta(
    meta: &mut Option<acp::Meta>,
    cfg: &crate::app::session_startup::LocalWorkspaceConfig,
) {
    let obj = meta.get_or_insert_with(acp::Meta::new);
    stamp_local_workspace_meta(obj, cfg);
}
/// Shared chat create/load/worktree meta finalize: kind + local stamp + scrub.
pub(super) fn finalize_chat_session_meta(
    meta: &mut Option<acp::Meta>,
    is_chat_path: bool,
    #[cfg_attr(not(feature = "local-workspace"), allow(unused_variables))]
    session_flags: &SessionFlags,
) {
    if !is_chat_path {
        return;
    }
    apply_chat_kind_meta(meta);
    #[cfg(feature = "local-workspace")]
    if let Some(ref lw) = session_flags.local_workspace {
        apply_local_workspace_meta(meta, lw);
    }
    scrub_chat_workspace_bind_meta(meta);
}
/// Remove client workspace-bind keys from chat create/load meta (defense in depth).
///
/// Narrow scrub exception: keep `legacy ext RPC` when local
/// intent is **attach**. Own stamps intent only (shell mints `server_id`).
/// Never keep `envId` or Direct hub `legacy ext RPC`.
pub(super) fn scrub_chat_workspace_bind_meta(meta: &mut Option<acp::Meta>) {
    let Some(obj) = meta.as_mut() else {
        return;
    };
    for key in CHAT_FORBIDDEN_WORKSPACE_BIND_KEYS {
        obj.remove(*key);
    }
    obj.retain(|k, _| !crate::acp::vendor::is_vendor_meta_key(k));
}
/// Params for shell ACP `legacy ext RPC`.
///
/// v1 surface is **shell ACP-only** (no pager slash/command wiring). Pager
/// dogfood / headless clients call the extension directly with this payload.
/// No remove path until session end.
#[cfg(feature = "local-workspace")]
#[allow(dead_code)]
pub(crate) fn mid_session_add_local_workspace_params(
    session_id: &str,
    cfg: &crate::app::session_startup::LocalWorkspaceConfig,
) -> serde_json::Value {
    let mut meta = serde_json::Map::new();
    stamp_local_workspace_meta(&mut meta, cfg);
    let mut opt = Some(meta);
    scrub_chat_workspace_bind_meta(&mut opt);
    serde_json::json!({
        "sessionId": session_id,
        "meta": opt.unwrap_or_default(),
    })
}
/// Fail closed on operator attestation outside the FS-only allowlist.
/// `None` / empty attested set → uncheckable → refuse. Live server is not probed.
#[cfg(feature = "local-workspace")]
pub(crate) fn reject_non_fs_only_advertised_tools(
    advertised_tool_ids: Option<&[&str]>,
) -> Result<(), String> {
    let Some(ids) = advertised_tool_ids else {
        return Err(
            "operator attestation GROK_CHAT_LOCAL_WORKSPACE_ADVERTISED_TOOLS is unset \
             (uncheckable); refuse attach. Live workspace_server was not inspected. Set \
             the env to a comma-separated FS-only catalog."
                .into(),
        );
    };
    if ids.is_empty() {
        return Err(
            "operator attestation GROK_CHAT_LOCAL_WORKSPACE_ADVERTISED_TOOLS is empty \
             (uncheckable); refuse attach. Live workspace_server was not inspected."
                .into(),
        );
    }
    let forbidden: Vec<&str> = ids
        .iter()
        .copied()
        .filter(|id| !LOCAL_WORKSPACE_FS_ONLY_TOOL_IDS.contains(id))
        .collect();
    if forbidden.is_empty() {
        Ok(())
    } else {
        Err(
                format!(
            "operator attestation lists tools outside the FS-only allowlist: {}. \
             Live workspace_server was not inspected. Fix \
             GROK_CHAT_LOCAL_WORKSPACE_ADVERTISED_TOOLS or restart workspace_server \
             with --require-explicit-toolset and an FS-only catalog.",
            forbidden.join(", ")
        ),
            )
    }
}
/// Metadata returned from effect execution so the event loop can patch
/// state that requires a spawned task handle (e.g., auth AbortHandle).
#[derive(Default)]
pub(crate) struct EffectMeta {
    /// Auth abort handle + its request sequence. The event loop must
    /// install this into `AppView.auth_state` if the current auth state
    /// still matches the sequence.
    pub auth_abort_handle: Option<(u64, tokio::task::AbortHandle)>,
    /// Auth URL poll abort handle + request sequence (installed on
    /// `AppView.auth_url_poll_handle` when the seq still matches).
    pub auth_url_poll_handle: Option<(u64, tokio::task::AbortHandle)>,
}
/// Extract the first user prompt text from a session's `chat_history.jsonl`.
///
/// Returns the first line of the `<user_query>` content (if present),
/// or the first line of the raw user message text.
pub(super) fn extract_first_user_prompt(
    info: &pi_shell::session::info::Info,
) -> Option<String> {
    use std::io::BufRead;
    let history_path = pi_shell::session::persistence::session_dir(info)
        .join("chat_history.jsonl");
    let file = std::fs::File::open(history_path).ok()?;
    let reader = std::io::BufReader::new(file);
    for line in reader.lines() {
        let line = line.ok()?;
        let v: serde_json::Value = serde_json::from_str(&line).ok()?;
        if v.get("type").and_then(|t| t.as_str()) != Some("user") {
            continue;
        }
        let content = v.get("content");
        let text = content
            .and_then(|c| c.as_array())
            .and_then(|arr| {
                arr
                    .iter()
                    .find_map(|block| {
                        if block.get("type")?.as_str()? == "text" {
                            block.get("text")?.as_str().map(String::from)
                        } else {
                            None
                        }
                    })
            })
            .or_else(|| content.and_then(|c| c.as_str()).map(String::from))?;
        if let Some(start) = text.find("<user_query>") {
            let after = &text[start + "<user_query>".len()..];
            let end = after.find("</user_query>").unwrap_or(after.len());
            let query = after[..end].trim();
            if !query.is_empty() && !query.starts_with('<') {
                return Some(query.to_string());
            }
        }
    }
    None
}
/// Parse the `legacy ext RPC` response payload (the unwrapped
/// `{ "sessions": [...] }` object) into [`SessionPickerEntry`] rows.
///
/// Used by the resume picker ([`Effect::FetchSessionList`]).
/// Sessions older than 30 days, and sessions with no usable user prompt
/// (empty `summary` after fallbacks), are dropped.
pub(super) fn parse_session_picker_entries(
    payload: &serde_json::Value,
) -> Vec<crate::app::app_view::SessionPickerEntry> {
    use crate::app::app_view::SessionPickerEntry;
    let entries: Vec<serde_json::Value> = payload
        .get("sessions")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let now = chrono::Utc::now();
    let cutoff = now - chrono::Duration::days(30);
    entries
        .into_iter()
        .filter_map(|v| {
            let id = v
                .get("sessionId")
                .or_else(|| v.get("session_id"))
                .and_then(|s| s.as_str())?
                .to_string();
            let summary = v
                .get("summary")
                .and_then(|s| s.as_str())
                .unwrap_or_default()
                .to_string();
            let first_prompt = v
                .get("firstPrompt")
                .or_else(|| v.get("first_prompt"))
                .and_then(|s| s.as_str())
                .map(String::from);
            let is_conversation = false;
            let parsed_updated: Option<chrono::DateTime<chrono::Utc>> = v
                .get("updatedAt")
                .or_else(|| v.get("updated_at"))
                .and_then(|s| s.as_str())
                .and_then(|s| s.parse().ok());
            let parsed_created: Option<chrono::DateTime<chrono::Utc>> = v
                .get("createdAt")
                .or_else(|| v.get("created_at"))
                .and_then(|s| s.as_str())
                .and_then(|s| s.parse().ok());
            let updated_at: chrono::DateTime<chrono::Utc> = match parsed_updated {
                Some(ts) => {
                    if !is_conversation && ts < cutoff {
                        return None;
                    }
                    ts
                }
                None => {
                    if !is_conversation {
                        return None;
                    }
                    parsed_created.unwrap_or(chrono::DateTime::<chrono::Utc>::UNIX_EPOCH)
                }
            };
            use pi_tools::implementations::skills::skill::extract_skill_display_text;
            let display = if let Some(ref fp) = first_prompt {
                if let Some(d) = extract_skill_display_text(fp) {
                    d
                } else if !summary.is_empty() {
                    extract_skill_display_text(&summary).unwrap_or(summary)
                } else {
                    fp.lines().next().unwrap_or_default().trim().to_string()
                }
            } else if !summary.is_empty() {
                extract_skill_display_text(&summary).unwrap_or(summary)
            } else {
                let info_cwd = v
                    .get("cwd")
                    .and_then(|s| s.as_str())
                    .unwrap_or_default()
                    .to_string();
                let info = pi_shell::session::info::Info {
                    id: acp::SessionId::new(id.clone()),
                    cwd: info_cwd,
                };
                extract_first_user_prompt(&info).unwrap_or_default()
            };
            let created_at: chrono::DateTime<chrono::Utc> = parsed_created
                .unwrap_or(updated_at);
            let cwd_str = v
                .get("cwd")
                .and_then(|s| s.as_str())
                .unwrap_or_default()
                .to_string();
            let hostname = v.get("hostname").and_then(|s| s.as_str()).map(String::from);
            let source = if is_conversation {
                "conversation".to_string()
            } else {
                v.get("source").and_then(|s| s.as_str()).unwrap_or("local").to_string()
            };
            let model_id = v
                .get("modelId")
                .or_else(|| v.get("model_id"))
                .and_then(|s| s.as_str())
                .map(String::from);
            let num_messages = v
                .get("numMessages")
                .or_else(|| v.get("num_messages"))
                .and_then(|n| n.as_u64())
                .unwrap_or(0) as usize;
            let last_active_at: Option<chrono::DateTime<chrono::Utc>> = v
                .get("lastActiveAt")
                .or_else(|| v.get("last_active_at"))
                .and_then(|s| s.as_str())
                .and_then(|s| s.parse().ok());
            let branch = v.get("branch").and_then(|s| s.as_str()).map(String::from);
            let worktree_label = v
                .get("worktreeLabel")
                .or_else(|| v.get("worktree_label"))
                .and_then(|s| s.as_str())
                .map(String::from);
            let last_turn_summary = v
                .get("lastTurnSummary")
                .or_else(|| v.get("last_turn_summary"))
                .and_then(|s| s.as_str())
                .map(String::from);
            let last_recap = v
                .get("lastRecap")
                .or_else(|| v.get("last_recap"))
                .and_then(|s| s.as_str())
                .map(String::from);
            let repo_name = crate::views::session_picker::repo_name_from_cwd(&cwd_str);
            Some(SessionPickerEntry {
                id,
                summary: display,
                updated_at,
                created_at,
                cwd: cwd_str,
                hostname,
                source,
                model_id,
                num_messages,
                last_active_at,
                branch,
                repo_name,
                worktree_label,
                last_turn_summary,
                last_recap,
                card_detail: None,
            })
        })
        .filter_map(|mut e| {
            if e.summary.is_empty() {
                if e.source == "conversation" {
                    e.summary = "Untitled".to_string();
                } else {
                    return None;
                }
            }
            if e.source == "remote"
                && pi_shell::session::resolve_local_session_any_cwd(&e.id)
                    .is_some()
            {
                e.source = "local".to_string();
            }
            Some(e)
        })
        .collect()
}

/// Map a standard ACP `session/list` response onto picker rows.
///
/// Vendor `legacy ext RPC` extras (`query`, `allowRelax`, kind facets) are
/// not on the wire; callers filter locally when the resume picker has a query.
pub(super) fn session_picker_entries_from_acp(
    resp: &acp::ListSessionsResponse,
) -> Vec<crate::app::app_view::SessionPickerEntry> {
    let fallback_updated = chrono::Utc::now().to_rfc3339();
    let sessions: Vec<serde_json::Value> = resp
        .sessions
        .iter()
        .map(|s| {
            let title = s
                .title
                .as_deref()
                .map(str::trim)
                .filter(|t| !t.is_empty())
                .unwrap_or(s.session_id.0.as_ref());
            serde_json::json!({
                "sessionId": s.session_id.0,
                "cwd": s.cwd.to_string_lossy(),
                "summary": title,
                "updatedAt": s.updated_at.clone().unwrap_or_else(|| fallback_updated.clone()),
                "source": "local",
            })
        })
        .collect();
    parse_session_picker_entries(&serde_json::json!({ "sessions": sessions }))
}

pub(super) async fn send_logout(_tx: &AcpAgentTx) {}

/// Best-effort auth cancel: stops the shell's device/loopback wait so a
/// later login is single-flight. Errors are ignored — UI already left
/// `Authenticating`. `request_seq` scopes the cancel to the abandoned attempt.
pub(super) async fn send_auth_cancel(_tx: &AcpAgentTx, _request_seq: u64) -> TaskResult {
    TaskResult::AuthCancelComplete
}

pub(super) async fn send_check_subscription(
    _tx: &AcpAgentTx,
    verify: Option<u64>,
) -> TaskResult {
    TaskResult::CheckSubscriptionComplete {
        verify,
        meta: None,
    }
}

/// One-shot subscription re-check for the credit-limit retry flow.
/// Same ACP call as `send_check_subscription` but returns a
/// `CreditLimitRecheckComplete` so the dispatch layer can decide
/// whether to retry the stashed prompt or show the upsell.
pub(super) async fn send_credit_limit_recheck(
    _tx: &AcpAgentTx,
    agent_id: AgentId,
) -> TaskResult {
    TaskResult::CreditLimitRecheckComplete {
        agent_id,
        meta: None,
    }
}
pub(super) async fn send_authenticate(
    tx: &AcpAgentTx,
    request_seq: u64,
    method_id: acp::AuthMethodId,
    use_oauth: bool,
    force_interactive: bool,
) -> TaskResult {
    let mut meta = serde_json::json!({
        "use_oauth": use_oauth,
        "request_seq": request_seq,
    });
    if force_interactive {
        meta["force_interactive"] = serde_json::json!(true);
    }
    let req = acp::AuthenticateRequest::new(method_id).meta(meta.as_object().cloned());
    match acp_send(req, tx).await {
        Ok(resp) => {
            ulog::info("auth completed", None, None);
            TaskResult::AuthComplete {
                request_seq,
                meta: resp.meta.map(serde_json::Value::Object),
            }
        }
        Err(e) => {
            let error = sanitize_user_error(&e.to_string());
            ulog::error(
                "auth failed",
                None,
                Some(serde_json::json!({"error": &error})),
            );
            TaskResult::AuthFailed {
                request_seq,
                error,
            }
        }
    }
}
/// Translate a settings-registry key + value into the matching shell
/// helper call. Type mismatches return an error (not panic) so a
/// spawned task doesn't crash the pager. Unknown keys also return
/// a descriptive error.
pub(crate) async fn persist_setting(
    key: crate::settings::SettingKey,
    value: crate::settings::SettingValue,
) -> Result<(), String> {
    use crate::settings::SettingValue;
    fn kind_mismatch(key: &str, expected: &str, got: &SettingValue) -> String {
        format!("persist_setting({key}) expected {expected}, got {got:?}")
    }
    match key {
        "compact_mode" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("compact_mode", "Bool", &value));
            };
            pi_shell::util::config::set_compact_mode(b)
                .await
                .map_err(|e| e.to_string())
        }
        "show_timestamps" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("show_timestamps", "Bool", &value));
            };
            pi_shell::util::config::set_show_timestamps(b)
                .await
                .map_err(|e| e.to_string())
        }
        "page_flip_on_send" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("page_flip_on_send", "Bool", &value));
            };
            pi_shell::util::config::set_page_flip_on_send(b)
                .await
                .map_err(|e| e.to_string())
        }
        "combine_queued_prompts" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("combine_queued_prompts", "Bool", &value));
            };
            pi_shell::util::config::set_combine_queued_prompts(b)
                .await
                .map_err(|e| e.to_string())
        }
        "show_timeline" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("show_timeline", "Bool", &value));
            };
            pi_shell::util::config::set_show_timeline(b)
                .await
                .map_err(|e| e.to_string())
        }
        "simple_mode" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("simple_mode", "Bool", &value));
            };
            pi_shell::util::config::set_simple_mode(b)
                .await
                .map_err(|e| e.to_string())
        }
        "contextual_hints.undo" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("contextual_hints.undo", "Bool", &value));
            };
            pi_shell::util::config::set_contextual_hint_undo(b)
                .await
                .map_err(|e| e.to_string())
        }
        "contextual_hints.plan_mode" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("contextual_hints.plan_mode", "Bool", &value));
            };
            pi_shell::util::config::set_contextual_hint_plan_mode(b)
                .await
                .map_err(|e| e.to_string())
        }
        "contextual_hints.image_input" => {
            let SettingValue::Bool(b) = value else {
                return Err(
                    kind_mismatch("contextual_hints.image_input", "Bool", &value),
                );
            };
            pi_shell::util::config::set_contextual_hint_image_input(b)
                .await
                .map_err(|e| e.to_string())
        }
        "contextual_hints.small_screen" => {
            let SettingValue::Bool(b) = value else {
                return Err(
                    kind_mismatch("contextual_hints.small_screen", "Bool", &value),
                );
            };
            pi_shell::util::config::set_contextual_hint_small_screen(b)
                .await
                .map_err(|e| e.to_string())
        }
        "contextual_hints.word_select" => {
            let SettingValue::Bool(b) = value else {
                return Err(
                    kind_mismatch("contextual_hints.word_select", "Bool", &value),
                );
            };
            pi_shell::util::config::set_contextual_hint_word_select(b)
                .await
                .map_err(|e| e.to_string())
        }
        "contextual_hints.ssh_wrap" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("contextual_hints.ssh_wrap", "Bool", &value));
            };
            pi_shell::util::config::set_contextual_hint_ssh_wrap(b)
                .await
                .map_err(|e| e.to_string())
        }
        "theme" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("theme", "Enum", &value));
            };
            pi_shell::util::config::set_theme(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "auto_dark_theme" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("auto_dark_theme", "Enum", &value));
            };
            pi_shell::util::config::set_auto_dark_theme(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "auto_light_theme" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("auto_light_theme", "Enum", &value));
            };
            pi_shell::util::config::set_auto_light_theme(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "default_model" => {
            let SettingValue::String(s) = value else {
                return Err(kind_mismatch("default_model", "String", &value));
            };
            pi_shell::util::config::set_default_model(s)
                .await
                .map_err(|e| e.to_string())
        }
        "scroll_speed" => {
            let SettingValue::Int(i) = value else {
                return Err(kind_mismatch("scroll_speed", "Int", &value));
            };
            pi_shell::util::config::set_scroll_speed(i)
                .await
                .map_err(|e| e.to_string())
        }
        "scroll_mode" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("scroll_mode", "Enum", &value));
            };
            pi_shell::util::config::set_scroll_mode(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "invert_scroll" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("invert_scroll", "Bool", &value));
            };
            pi_shell::util::config::set_invert_scroll(b)
                .await
                .map_err(|e| e.to_string())
        }
        "display_refresh_auto_cadence" => {
            let SettingValue::Bool(b) = value else {
                return Err(
                    kind_mismatch("display_refresh_auto_cadence", "Bool", &value),
                );
            };
            pi_shell::util::config::set_display_refresh_auto_cadence(b)
                .await
                .map_err(|e| e.to_string())
        }
        "scroll_lines" => {
            let SettingValue::Int(i) = value else {
                return Err(kind_mismatch("scroll_lines", "Int", &value));
            };
            pi_shell::util::config::set_scroll_lines(i)
                .await
                .map_err(|e| e.to_string())
        }
        "default_selected_permission" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("default_selected_permission", "Enum", &value));
            };
            pi_shell::util::config::set_default_selected_permission(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "vim_mode" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("vim_mode", "Bool", &value));
            };
            pi_shell::util::config::set_vim_mode(b)
                .await
                .map_err(|e| e.to_string())
        }
        "remember_tool_approvals" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("remember_tool_approvals", "Bool", &value));
            };
            pi_shell::util::config::set_remember_tool_approvals(b)
                .await
                .map_err(|e| e.to_string())
        }
        "toolset.ask_user_question.timeout_enabled" => {
            let SettingValue::Bool(b) = value else {
                return Err(
                    kind_mismatch(
                        "toolset.ask_user_question.timeout_enabled",
                        "Bool",
                        &value,
                    ),
                );
            };
            pi_shell::util::config::set_ask_user_question_timeout_enabled(b)
                .await
                .map_err(|e| e.to_string())
        }
        "show_thinking_blocks" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("show_thinking_blocks", "Bool", &value));
            };
            pi_shell::util::config::set_show_thinking_blocks(b)
                .await
                .map_err(|e| e.to_string())
        }
        "group_tool_verbs" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("group_tool_verbs", "Bool", &value));
            };
            pi_shell::util::config::set_group_tool_verbs(b)
                .await
                .map_err(|e| e.to_string())
        }
        "collapsed_edit_blocks" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("collapsed_edit_blocks", "Bool", &value));
            };
            pi_shell::util::config::set_collapsed_edit_blocks(b)
                .await
                .map_err(|e| e.to_string())
        }
        "prompt_suggestions" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("prompt_suggestions", "Bool", &value));
            };
            pi_shell::util::config::set_prompt_suggestions(b)
                .await
                .map_err(|e| e.to_string())
        }
        "keep_text_selection" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("keep_text_selection", "Enum", &value));
            };
            pi_shell::util::config::set_keep_text_selection(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "respect_manual_folds" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("respect_manual_folds", "Bool", &value));
            };
            tokio::task::spawn_blocking(move || crate::appearance::persist_respect_manual_folds(
                    b,
                ))
                .await
                .map_err(|e| e.to_string())?
                .map_err(|e| e.to_string())
        }
        "render_mermaid" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("render_mermaid", "Enum", &value));
            };
            pi_shell::util::config::set_render_mermaid(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "hunk_tracker_mode" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("hunk_tracker_mode", "Enum", &value));
            };
            pi_shell::util::config::set_hunk_tracker_mode(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "screen_mode" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("screen_mode", "Enum", &value));
            };
            pi_shell::util::config::set_screen_mode(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "voice_keybind_enabled" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("voice_keybind_enabled", "Bool", &value));
            };
            pi_shell::util::config::set_voice_keybind_enabled(b)
                .await
                .map_err(|e| e.to_string())
        }
        "voice_capture_mode" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("voice_capture_mode", "Enum", &value));
            };
            pi_shell::util::config::set_voice_capture_mode(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "voice_stt_language" => {
            let SettingValue::Enum(s) = value else {
                return Err(kind_mismatch("voice_stt_language", "Enum", &value));
            };
            pi_shell::util::config::set_voice_stt_language(s.to_string())
                .await
                .map_err(|e| e.to_string())
        }
        "max_thoughts_width" => {
            let SettingValue::Int(i) = value else {
                return Err(kind_mismatch("max_thoughts_width", "Int", &value));
            };
            pi_shell::util::config::set_max_thoughts_width(i)
                .await
                .map_err(|e| e.to_string())
        }
        "show_tips" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("show_tips", "Bool", &value));
            };
            pi_shell::util::config::set_show_tips(b)
                .await
                .map_err(|e| e.to_string())
        }
        "auto_update" => {
            let SettingValue::Bool(b) = value else {
                return Err(kind_mismatch("auto_update", "Bool", &value));
            };
            pi_shell::util::config::set_auto_update(b)
                .await
                .map_err(|e| e.to_string())
        }
        "fork_secondary_model" => {
            let SettingValue::String(s) = value else {
                return Err(kind_mismatch("fork_secondary_model", "String", &value));
            };
            pi_shell::util::config::set_fork_secondary_model(s)
                .await
                .map_err(|e| e.to_string())
        }
        other => Err(format!("unknown setting key for persist: `{other}`")),
    }
}
/// Body for `Effect::PersistPermissionMode`. Factored out for testability.
///
/// 1. Persist `ui.permission_mode` to disk.
/// 2. Fire ACP `legacy ext RPC` (gated on disk success for
///    `WithRollback`; always for `BestEffort`).
/// 3. Return the matching `TaskResult`.
pub(crate) async fn persist_permission_mode_and_notify(
    canonical: &'static str,
    session_id: Option<acp::SessionId>,
    persist: PermissionModePersist,
    tx: AcpAgentTx,
) -> TaskResult {
    let enabled = canonical == "always-approve";
    let auto_mode = canonical == "auto";
    let config_str: &'static str = canonical;
    let disk_result = pi_shell::util::config::update_config(|cfg| {
            cfg.ui.permission_mode = Some(config_str.to_string());
        })
        .await;
    let disk_outcome: Result<(), String> = disk_result.map_err(|e| e.to_string());
    if should_send_yolo_acp_notification(&disk_outcome, persist) && session_id.is_some()
    {
        let params = serde_json::json!({
            "yolo_mode": enabled,
            "auto_mode": auto_mode,
            "permission_mode": config_str,
        });
        let notification = acp::ExtNotification::new(
            "pi/yolo_mode_changed",
            serde_json::value::to_raw_value(&params)
                .expect("serialize yolo_mode_changed params")
                .into(),
        );
        if let Err(e) = acp_send(notification, &tx).await {
            tracing::warn!("Failed to send yolo_mode_changed notification: {e}");
        }
    }
    route_permission_mode_result(disk_outcome, persist, config_str)
}
/// Whether to fire the ACP `legacy ext RPC` notification.
/// `WithRollback` suppresses on disk failure (agent must not see the
/// optimistic value). `BestEffort` always fires.
pub(super) fn should_send_yolo_acp_notification(
    disk_outcome: &Result<(), String>,
    persist: PermissionModePersist,
) -> bool {
    match (disk_outcome, persist) {
        (_, PermissionModePersist::BestEffort) => true,
        (Ok(()), PermissionModePersist::WithRollback(_)) => true,
        (Err(_), PermissionModePersist::WithRollback(_)) => false,
    }
}
/// Map disk-write outcome + persist variant to the correct `TaskResult`.
pub(super) fn route_permission_mode_result(
    disk_outcome: Result<(), String>,
    persist: PermissionModePersist,
    config_str: &'static str,
) -> TaskResult {
    match (disk_outcome, persist) {
        (Ok(()), _) => {
            TaskResult::SettingPersisted {
                key: "permission_mode",
                value: crate::settings::SettingValue::Enum(config_str),
            }
        }
        (Err(e), PermissionModePersist::WithRollback(prev_canonical)) => {
            tracing::warn!("failed to save permission mode preference: {e} — rolling back");
            TaskResult::SettingPersistFailed {
                key: "permission_mode",
                rollback_value: crate::settings::SettingValue::Enum(prev_canonical),
                error: e,
            }
        }
        (Err(e), PermissionModePersist::BestEffort) => {
            tracing::warn!("failed to save permission mode preference (best-effort): {e}");
            TaskResult::SettingPersistFailedBestEffort {
                key: "permission_mode",
                error: e,
            }
        }
    }
}
/// Fire-and-forget blocking write of one `[hints]` value to config.toml.
/// `what` names the preference for log messages.
pub(super) fn persist_hint(
    tasks: &mut JoinSet<TaskResult>,
    key: &'static str,
    value: impl Into<toml_edit::Value> + Send + 'static,
    what: &'static str,
) {
    tasks
        .spawn(async move {
            match tokio::task::spawn_blocking(move || crate::config_toml_edit::set_hint(
                    key,
                    value,
                ))
                .await
            {
                Ok(Ok(())) => {}
                Ok(Err(e)) => tracing::warn!("failed to persist {what}: {e}"),
                Err(e) => tracing::warn!("failed to persist {what} (join error): {e}"),
            }
            TaskResult::CancelComplete
        });
}
/// A blocking flock on the shared, possibly-network `~/.grok` lock must never
/// stall the event-loop thread (and would hang exit on `/quit`); the registry
/// is best-effort, so skip on contention.
pub(super) fn unregister_active_session_best_effort(session_id: &acp::SessionId) {
    unregister_active_session_best_effort_in(
        &pi_shell::util::grok_home::grok_home(),
        session_id,
    );
}
pub(super) fn unregister_active_session_best_effort_in(
    root: &Path,
    session_id: &acp::SessionId,
) {
    match pi_active_sessions::try_unregister_in(root, session_id) {
        Ok(true) => {}
        Ok(false) => {
            tracing::debug!(
            session_id = %session_id.0,
            "Skipped active-session unregister under lock contention; \
             reaped by collect_crashed on next launch"
        )
        }
        Err(e) => tracing::warn!(?e, "Failed to unregister active session"),
    }
}
