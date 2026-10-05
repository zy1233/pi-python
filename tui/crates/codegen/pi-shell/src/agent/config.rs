use crate::agent::model_providers::{
    ModelProviderConfig, auth_config_issues, model_provider_auth_name, parse_model_providers,
};
use crate::auth::{GrokComConfig, OidcAuthConfig};
use crate::{config::StorageMode, sampling::ApiBackend, tools::config::ShellToolsetConfig};
use agent_client_protocol as acp;
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::num::NonZeroU64;
use std::path::PathBuf;
use pi_agent::prompt::skills::SkillsConfig;
use pi_sampling_types::{
    CompactionAtTokens, CompactionsRemaining, ReasoningEffort, ReasoningEffortOption,
};
use pi_tools::types::compat::{
    COMPAT_CELLS, CompatConfig, CompatConfigToml, CompatRemoteKey, CompatSurface, CompatVendor,
};
/// The mode in which the agent is running.
/// Determines behavior like relay sync enablement.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AgentMode {
    /// TUI interactive mode - full UI with relay sync support
    Tui,
    /// Headless mode - no UI, connected to relay WebSocket
    Headless,
    /// Stdio mode - JSON-RPC over stdin/stdout
    Stdio,
    /// Server mode - WebSocket server for external clients
    Serve,
    /// Generic/unknown mode
    #[default]
    Generic,
}
/// How a model's API key is presented to its endpoint (`auth_scheme` in `config.toml`).
///
/// Formerly defined in the removed `pi-sampler` crate; kept here because it is part of the
/// persisted `config.toml` model schema.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum AuthScheme {
    #[default]
    Bearer,
    XApiKey,
}
/// Default agent type when the server or user config doesn't specify one.
pub const DEFAULT_AGENT_TYPE: &str = "grok-build-plan";

/// Serde default for `ModelInfo.agent_type` and `ModelEntryConfig.agent_type`.
pub(crate) fn default_agent_type() -> String {
    DEFAULT_AGENT_TYPE.to_owned()
}
/// Default base URL for the cli chat proxy.
pub const CLI_CHAT_PROXY_BASE_URL_DEFAULT: &str = "https://cli-chat-proxy.grok.com/v1";
/// Default base URL for the public pi API.
pub(crate) const PI_API_BASE_URL_DEFAULT: &str = "https://api.x.ai/v1";
/// One or more environment variable names that may hold a model API key.
///
/// Serde `untagged`: accepts a string or an array in TOML/JSON.
///
/// ```toml
/// env_key = "ANTHROPIC_AUTH_TOKEN"
/// # or
/// env_key = ["ANTHROPIC_AUTH_TOKEN", "LC_ANTHROPIC_AUTH_TOKEN"]
/// ```
///
/// At resolve time the **first set, non-blank** value wins (e.g. SSH
/// `AcceptEnv LC_*` forwarding of the Bottlerocket token).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum EnvKeys {
    One(String),
    Many(Vec<String>),
}
impl EnvKeys {
    /// Single-name convenience constructor.
    pub(crate) fn single(name: impl Into<String>) -> Self {
        Self::One(name.into())
    }
    /// Construct from an ordered list (empty names dropped; 0/1/N → Many/One/Many).
    pub fn new(names: impl IntoIterator<Item = impl Into<String>>) -> Self {
        let names: Vec<String> = names
            .into_iter()
            .map(Into::into)
            .filter(|s| !s.is_empty())
            .collect();
        match names.as_slice() {
            [] => Self::Many(Vec::new()),
            [_] => Self::One(names.into_iter().next().expect("len 1")),
            _ => Self::Many(names),
        }
    }
    /// Configured names in priority order.
    pub(crate) fn names(&self) -> Vec<&str> {
        match self {
            Self::One(s) => vec![s.as_str()],
            Self::Many(v) => v.iter().map(String::as_str).collect(),
        }
    }
    /// First name only (useful for single-key assertions / display).
    pub(crate) fn primary(&self) -> Option<&str> {
        match self {
            Self::One(s) if !s.is_empty() => Some(s.as_str()),
            Self::One(_) => None,
            Self::Many(v) => v.iter().map(String::as_str).find(|s| !s.is_empty()),
        }
    }
}
/// Semantic equality: compares the ordered name lists, so `One("X")` and
/// `Many(["X"])` (the shape serde produces for `["X"]`) compare equal.
impl PartialEq for EnvKeys {
    fn eq(&self, other: &Self) -> bool {
        self.names() == other.names()
    }
}
impl Eq for EnvKeys {}
impl std::fmt::Display for EnvKeys {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.names().join(", "))
    }
}
/// Configuration for API endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct EndpointsConfig {
    /// cli chat proxy base URL. `None` = unset (resolvers apply the default);
    /// `Some` = explicitly configured. Tracking explicitness (vs comparing to the
    /// default value) lets an org pin the proxy to the default on purpose.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cli_chat_proxy_base_url: Option<String>,
    /// Base URL for the public pi API.
    pub pi_api_base_url: String,
    /// Optional extra access-header value (applied only with the optional
    /// non-production feature, and only for matching first-party hosts).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub alpha_test_key: Option<String>,
    /// Env: `GROK_MODELS_BASE_URL`. Enables custom endpoint mode.
    /// List URL defaults to `{models_base_url}/models`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub models_base_url: Option<String>,
    /// Env: `GROK_MODELS_LIST_URL`. Overrides the default `{base}/models` list URL.
    #[serde(alias = "models_endpoint", skip_serializing_if = "Option::is_none")]
    pub models_list_url: Option<String>,
    /// Env: `GROK_FEEDBACK_BASE_URL`. Where feedback submissions go.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub feedback_base_url: Option<String>,
    /// Env: `GROK_TRACE_UPLOAD_URL`. Where trace uploads go.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_upload_url: Option<String>,
    /// Env: `GROK_TRACE_UPLOAD_BUCKET`. Direct bucket (`gs://` or `s3://`), bypasses proxy.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_upload_bucket: Option<String>,
    /// Env: `GROK_TRACE_UPLOAD_REGION`. AWS region (S3 only).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_upload_region: Option<String>,
    /// Env: `GROK_TRACE_UPLOAD_CREDENTIALS_FILE`. Path to GCS SA key or AWS credentials file.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_upload_credentials_file: Option<String>,
    /// Inline credentials (JSON/INI). Takes precedence over `credentials_file`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_upload_credentials: Option<String>,
    /// Env: `GROK_TRACE_UPLOAD_ENDPOINT_URL`. Custom S3-compatible endpoint.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_upload_endpoint_url: Option<String>,
    /// Env: `GROK_DEPLOYMENT_KEY`. Management API key for enterprise deployments.
    /// Sent on telemetry and service requests for deployment-level attribution.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub deployment_key: Option<String>,
    /// Env: `GROK_MANAGED_CONFIG_URL`. Override the managed config endpoint.
    /// Defaults to `{proxy_url()}/deployment/config`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub managed_config_url: Option<String>,
    /// Env: `OTEL_EXPORTER_OTLP_ENDPOINT`. OTLP collector base; `/v1/traces` is
    /// appended. Legacy repoint of the INTERNAL trace pipeline — deprecated in
    /// favor of `GROK_INTERNAL_OTLP_TRACES_ENDPOINT`, and ignored by the internal
    /// pipeline when `GROK_EXTERNAL_OTEL` is set (the standard `OTEL_*` vars then
    /// route the external stream only).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub otel_exporter_otlp_endpoint: Option<String>,
    /// Env: `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`. Full traces endpoint, used
    /// verbatim; overrides `otel_exporter_otlp_endpoint`. Same legacy/deprecation
    /// semantics as `otel_exporter_otlp_endpoint`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub otel_exporter_otlp_traces_endpoint: Option<String>,
    /// Env: `OTEL_EXPORTER_OTLP_HEADERS`. `k=v,k2=v2`; merged onto export headers.
    /// Same legacy/deprecation semantics as `otel_exporter_otlp_endpoint`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub otel_exporter_otlp_headers: Option<String>,
    /// Env: `GROK_INTERNAL_OTLP_TRACES_ENDPOINT`. Full INTERNAL traces endpoint,
    /// used verbatim. Dev/debug repoint of the internal span firehose (replaces
    /// the legacy `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` behavior; used by
    /// local-ic-testing / internal dev flows). Wins over the legacy `OTEL_*` vars.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub grok_internal_otlp_traces_endpoint: Option<String>,
    /// Env: `GROK_INTERNAL_OTLP_HEADERS`. `k=v,k2=v2` extra headers for the
    /// internal export (debug). Wins over the legacy `OTEL_EXPORTER_OTLP_HEADERS`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub grok_internal_otlp_headers: Option<String>,
    /// External-OTEL master switch, captured at construction via
    /// [`external_otel_master_switch_resolved`] — the same layered resolution
    /// (requirement pin > `GROK_EXTERNAL_OTEL` env > `[telemetry].otel_enabled`
    /// config, managed layers included) that activates the external stream.
    /// When set, the standard `OTEL_EXPORTER_OTLP_*` vars are reserved for the
    /// external OTEL stream and the internal trace pipeline ignores them
    /// entirely — an admin who opts in (by *any* layer, including an org
    /// enable distributed via managed config with no env var) never receives
    /// the internally-authed firehose. Held as a field (not re-read in the
    /// resolvers) so the resolvers stay pure and testable without env races.
    #[serde(skip)]
    pub external_otel_master_switch: bool,
    /// Env: `OTEL_TRACES_EXPORTER`. `otlp` (default) or `none` to disable spans.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub otel_traces_exporter: Option<String>,
    /// Env: `OTEL_BSP_SCHEDULE_DELAY` (OTel) or `OTEL_TRACES_EXPORT_INTERVAL`
    /// (Claude alias). Batch flush interval (ms).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub otel_traces_export_interval: Option<u64>,
    /// Env: `OTEL_EXPORTER_OTLP_TIMEOUT`. Export HTTP timeout (ms).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub otel_exporter_otlp_timeout: Option<u64>,
    /// Read by `load_management_api_key_sync()`. Declared for `serde_ignored`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub management_api_key: Option<String>,
    /// Read by `load_gcs_service_account_key_sync()`. Declared for `serde_ignored`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gcs_service_account_key: Option<String>,
}
/// A blank or whitespace-only override counts as unset. Single source of truth
/// for the "empty value = not configured" rule shared by the endpoint resolvers.
fn blank_as_unset(opt: &Option<String>) -> Option<String> {
    opt.as_deref()
        .filter(|s| !s.trim().is_empty())
        .map(str::to_owned)
}
/// Parse a `k=v,k2=v2` OTLP header list (the `OTEL_EXPORTER_OTLP_HEADERS`
/// format, shared with `GROK_INTERNAL_OTLP_HEADERS`): split on `,`,
/// `split_once('=')`, trim key/value, skip blank keys, keep empty values.
fn parse_otlp_header_list(raw: &str) -> Vec<(String, String)> {
    raw.split(',')
        .filter_map(|kv| {
            let (k, v) = kv.split_once('=')?;
            let k = k.trim();
            (!k.is_empty()).then(|| (k.to_string(), v.trim().to_string()))
        })
        .collect()
}
impl EndpointsConfig {
    pub(crate) fn has_custom_endpoint(&self) -> bool {
        self.models_base_url.is_some() || self.models_list_url.is_some()
    }
    /// `default()` plus merged managed/requirements endpoint overrides, so
    /// startup fetches use the configured (not public) endpoints. Only merges
    /// layers — never derives one endpoint from another. Falls back to
    /// `default()` on load failure.
    pub(crate) fn from_effective_config() -> Self {
        match crate::config::load_effective_config() {
            Ok(cfg) => Self::from_config_value(&cfg),
            Err(_) => Self::default(),
        }
    }
    /// Layer the `[endpoints]` table from `config` over the env/default base.
    /// No field is derived from another — defaulting is done by the resolvers.
    /// `pub`: the pager resolves the voice STT base through this same path.
    pub fn from_config_value(config: &toml::Value) -> Self {
        let default = Self::default();
        let external_otel_master_switch = default.external_otel_master_switch;
        let mut base = match toml::Value::try_from(default) {
            Ok(v) => v,
            Err(_) => return Self::default(),
        };
        if let Some(endpoints) = config.get("endpoints") {
            crate::config::deep_merge_toml(&mut base, endpoints);
        }
        let mut resolved: Self = base.try_into().unwrap_or_default();
        resolved.external_otel_master_switch = external_otel_master_switch;
        resolved
    }
    /// The cli-chat-proxy base URL through which all auxiliary services (and
    /// OAuth/session inference) resolve: explicit `cli_chat_proxy_base_url`, else
    /// the public default. NEVER falls back to `pi_api_base_url` — that is the
    /// inference endpoint (API-key auth) only.
    pub fn proxy_url(&self) -> String {
        blank_as_unset(&self.cli_chat_proxy_base_url)
            .unwrap_or_else(|| CLI_CHAT_PROXY_BASE_URL_DEFAULT.to_owned())
    }
    pub(crate) fn resolve_inference_base_url(&self) -> String {
        self.models_base_url
            .clone()
            .unwrap_or_else(|| self.proxy_url())
    }
    /// Managed deployment-config URL (`grok setup`): explicit `managed_config_url`,
    /// else `proxy_url` + `/deployment/config`. Never `pi_api_base_url`, so the
    /// deployment key reaches the proxy, not the inference host.
    pub(crate) fn resolve_managed_config_url(&self) -> String {
        blank_as_unset(&self.managed_config_url).unwrap_or_else(|| {
            format!(
                "{}/deployment/config",
                self.proxy_url().trim_end_matches('/')
            )
        })
    }
    /// INTERNAL OTLP traces endpoint. Precedence:
    /// 1. `grok_internal_otlp_traces_endpoint` (verbatim)
    /// 2. legacy `otel_exporter_otlp_traces_endpoint` (verbatim) >
    ///    `otel_exporter_otlp_endpoint` + `/v1/traces` — ONLY when the
    ///    external-OTEL master switch is unset (back-compat; deprecated)
    /// 3. `proxy_url` + `/traces`.
    /// Uses the proxy default (not the `pi_api_base_url` fallback) so
    /// telemetry reports to pi even when inference is overridden. When the
    /// master switch IS set, the standard `OTEL_EXPORTER_OTLP_*` values are
    /// completely ignored here so the internally-authed firehose never lands
    /// at an external collector.
    pub(crate) fn resolve_otlp_traces_endpoint(&self) -> String {
        if let Some(full) = blank_as_unset(&self.grok_internal_otlp_traces_endpoint) {
            return full.trim_end_matches('/').to_string();
        }
        if !self.external_otel_master_switch
            && let Some(legacy) = self.legacy_internal_otlp_traces_endpoint()
        {
            tracing::warn!(
                "Repointing the internal trace pipeline via OTEL_EXPORTER_OTLP_ENDPOINT / \
                 OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is deprecated; use \
                 GROK_INTERNAL_OTLP_TRACES_ENDPOINT instead — the standard OTEL_* vars will \
                 route the external OTEL stream only in a future release"
            );
            return legacy;
        }
        format!("{}/traces", self.proxy_url().trim_end_matches('/'))
    }
    /// Legacy (standard-OTEL-var) internal traces endpoint, if any:
    /// `otel_exporter_otlp_traces_endpoint` verbatim, else
    /// `otel_exporter_otlp_endpoint` + `/v1/traces`. Ignores the master switch.
    fn legacy_internal_otlp_traces_endpoint(&self) -> Option<String> {
        if let Some(full) = blank_as_unset(&self.otel_exporter_otlp_traces_endpoint) {
            return Some(full.trim_end_matches('/').to_string());
        }
        blank_as_unset(&self.otel_exporter_otlp_endpoint)
            .map(|base| format!("{}/v1/traces", base.trim_end_matches('/')))
    }
    /// Extra headers for the INTERNAL export: `grok_internal_otlp_headers`
    /// first; legacy fallback to `otel_exporter_otlp_headers` ONLY when the
    /// external-OTEL master switch is unset (back-compat for existing users).
    pub(crate) fn resolve_otlp_headers(&self) -> Vec<(String, String)> {
        if let Some(headers) = blank_as_unset(&self.grok_internal_otlp_headers) {
            return parse_otlp_header_list(&headers);
        }
        if !self.external_otel_master_switch {
            return parse_otlp_header_list(
                self.otel_exporter_otlp_headers.as_deref().unwrap_or(""),
            );
        }
        Vec::new()
    }
    /// Whether the legacy fallback actually supplied the internal endpoint OR
    /// internal headers from the standard `OTEL_EXPORTER_OTLP_*` vars — i.e.
    /// the master switch is unset AND (`otel_exporter_otlp_traces_endpoint` /
    /// `otel_exporter_otlp_endpoint` is non-blank for the endpoint, or
    /// `otel_exporter_otlp_headers` is non-blank for headers) AND no
    /// `grok_internal_otlp_*` override shadowed that half.
    ///
    /// CONTRACT: this flag is passed to the external OTEL stream's init, which
    /// MUST refuse to activate when it is true — the same standard vars cannot
    /// feed both pipelines (no-double-send invariant, enforced in code).
    pub(crate) fn internal_otlp_consumed_standard_vars(&self) -> bool {
        if self.external_otel_master_switch {
            return false;
        }
        let endpoint_consumed = blank_as_unset(&self.grok_internal_otlp_traces_endpoint).is_none()
            && self.legacy_internal_otlp_traces_endpoint().is_some();
        let headers_consumed = blank_as_unset(&self.grok_internal_otlp_headers).is_none()
            && blank_as_unset(&self.otel_exporter_otlp_headers).is_some();
        endpoint_consumed || headers_consumed
    }
    /// Trace export enabled unless `OTEL_TRACES_EXPORTER=none`. Deliberately
    /// still honored by the internal pipeline even with `GROK_EXTERNAL_OTEL`
    /// set: disabling internal span export is the safe direction.
    pub(crate) fn resolve_traces_export_enabled(&self) -> bool {
        !matches!(
            self.otel_traces_exporter.as_deref().map(str::trim),
            Some("none")
        )
    }
    /// `OTEL_BSP_SCHEDULE_DELAY` / `OTEL_TRACES_EXPORT_INTERVAL` — tuning-only,
    /// deliberately shared between the internal and external pipelines.
    pub(crate) fn resolve_otlp_export_interval(&self) -> Option<std::time::Duration> {
        self.otel_traces_export_interval
            .map(std::time::Duration::from_millis)
    }
    /// `OTEL_EXPORTER_OTLP_TIMEOUT` — tuning-only, deliberately shared between
    /// the internal and external pipelines.
    pub(crate) fn resolve_otlp_timeout(&self) -> Option<std::time::Duration> {
        self.otel_exporter_otlp_timeout
            .map(std::time::Duration::from_millis)
    }
    /// `models_list_url` > `{models_base_url}/models` > `{proxy_base_url}/models`.
    pub(crate) fn resolve_models_list_url(&self) -> String {
        if let Some(ref url) = self.models_list_url {
            return url.clone();
        }
        let base = self
            .models_base_url
            .clone()
            .unwrap_or_else(|| self.proxy_url());
        format!("{}/models", base)
    }
}
impl Default for EndpointsConfig {
    fn default() -> Self {
        Self {
            cli_chat_proxy_base_url: std::env::var("GROK_CLI_CHAT_PROXY_BASE_URL").ok(),
            pi_api_base_url: std::env::var("GROK_PI_API_BASE_URL")
                .unwrap_or_else(|_| PI_API_BASE_URL_DEFAULT.to_owned()),
            alpha_test_key: None,
            models_base_url: env_string("GROK_MODELS_BASE_URL"),
            models_list_url: env_string("GROK_MODELS_LIST_URL"),
            feedback_base_url: env_string("GROK_FEEDBACK_BASE_URL"),
            trace_upload_url: env_string("GROK_TRACE_UPLOAD_URL"),
            trace_upload_bucket: env_string("GROK_TRACE_UPLOAD_BUCKET"),
            trace_upload_region: env_string("GROK_TRACE_UPLOAD_REGION"),
            trace_upload_credentials_file: env_string("GROK_TRACE_UPLOAD_CREDENTIALS_FILE"),
            trace_upload_credentials: None,
            trace_upload_endpoint_url: env_string("GROK_TRACE_UPLOAD_ENDPOINT_URL"),
            deployment_key: env_string("GROK_DEPLOYMENT_KEY"),
            managed_config_url: env_string("GROK_MANAGED_CONFIG_URL"),
            otel_exporter_otlp_endpoint: env_string("OTEL_EXPORTER_OTLP_ENDPOINT"),
            otel_exporter_otlp_traces_endpoint: env_string("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
            otel_exporter_otlp_headers: env_string("OTEL_EXPORTER_OTLP_HEADERS"),
            grok_internal_otlp_traces_endpoint: env_string("GROK_INTERNAL_OTLP_TRACES_ENDPOINT"),
            grok_internal_otlp_headers: env_string("GROK_INTERNAL_OTLP_HEADERS"),
            external_otel_master_switch: external_otel_master_switch_resolved(),
            otel_traces_exporter: env_string("OTEL_TRACES_EXPORTER"),
            otel_traces_export_interval: env_string("OTEL_BSP_SCHEDULE_DELAY")
                .or_else(|| env_string("OTEL_TRACES_EXPORT_INTERVAL"))
                .and_then(|s| s.parse().ok()),
            otel_exporter_otlp_timeout: env_string("OTEL_EXPORTER_OTLP_TIMEOUT")
                .and_then(|s| s.parse().ok()),
            management_api_key: None,
            gcs_service_account_key: None,
        }
    }
}
pub use pi_config_types::{
    BoolFlag, ConfigSource, FEATURES, Feature, FeatureSources, LazinessDetectorPerModelConfig,
    Resolved,
};
/// A requirement pin from `requirements.toml`. Wins over all other sources.
#[derive(Debug, Clone, Default)]
pub struct Constrained<T> {
    pin: Option<T>,
    source: Option<crate::config::RequirementSource>,
}
impl<T: Clone> Constrained<T> {
    pub(crate) fn pin(&mut self, value: T, source: crate::config::RequirementSource) {
        self.pin = Some(value);
        self.source = Some(source);
    }
    pub(crate) fn pinned(&self) -> Option<T> {
        self.pin.clone()
    }
}
/// Enforced requirements from `requirements.toml`. Pinned values win over all other sources.
#[derive(Debug, Clone, Default)]
pub struct Requirements {
    pub telemetry: Constrained<TelemetryMode>,
    pub trace_upload: Constrained<bool>,
    pub image_gen: Constrained<bool>,
    pub image_edit: Constrained<bool>,
    pub video_gen: Constrained<bool>,
    pub sandbox_auto_allow_bash: Constrained<bool>,
    pub sandbox_profile: Constrained<String>,
    pub respect_gitignore: Constrained<bool>,
    pub remote_fetch: Constrained<bool>,
    pub title_refresh: Constrained<bool>,
    /// Pins from a requirements layer or an MDM policy, keyed by [`Feature`].
    features: BTreeMap<Feature, Constrained<bool>>,
}
impl Requirements {
    pub(crate) fn pin_feature(
        &mut self,
        feature: Feature,
        value: bool,
        source: crate::config::RequirementSource,
    ) {
        self.features.entry(feature).or_default().pin(value, source);
    }
    pub(crate) fn pinned_feature(&self, feature: Feature) -> Option<bool> {
        self.features.get(&feature).and_then(Constrained::pinned)
    }
}
/// Inputs for resolving `#[serde(skip)]` runtime fields after `new_from_toml_cfg()`.
///
/// Constructed by each binary from its CLI args and startup state, then passed
/// to [`Config::resolve_runtime_fields`].
pub struct RuntimeResolutionContext<'a> {
    pub raw_config: &'a toml::Value,
    pub remote_settings: Option<&'a crate::util::config::RemoteSettings>,
    pub is_headless: bool,
    /// `Some(true)` = CLI explicitly enabled, `None` = defer to config/env/remote.
    pub cli_subagents: Option<bool>,
    pub cli_web_search_model: Option<&'a str>,
    pub cli_session_summary_model: Option<&'a str>,
    /// CLI memory override set by a legacy compatibility flag.
    pub memory_enabled_override: Option<bool>,
    /// CLI `--disable-web-search` flag. ORed with config.toml value.
    pub disable_web_search: bool,
    /// CLI `--todo-gate` flag. Session-scoped — not persisted.
    pub todo_gate: bool,
    /// CLI `--laziness-debug-log <path>`. When `Some`, the Layer-3
    /// classifier fires after every turn (bypassing the idle wait /
    /// per-model gate / nudge cap) and writes a JSONL line per fire.
    /// Observation-only. Session-scoped — not persisted.
    pub laziness_debug_log: Option<&'a std::path::Path>,
    /// CLI `--storage-mode` override. `None` = defer to env/remote/default.
    pub storage_mode: Option<&'a str>,
}
/// Read an env var as a trimmed string. Returns `None` if unset or empty/whitespace-only.
pub(crate) fn env_string(name: &str) -> Option<String> {
    let value = std::env::var(name).ok()?;
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}
pub(crate) use pi_config::env_bool;
/// Resolve a single vendor-compat cell: env > `[compat]` TOML > remote settings
/// remote flag > default ON.
fn resolve_compat_cell(
    env: &str,
    cfg: Option<bool>,
    remote: Option<bool>,
    default: bool,
) -> Resolved<bool> {
    resolve_compat_cell_with_env(pi_config::env_bool(env), cfg, remote, default)
}
pub(crate) fn resolve_compat_cell_with_env(
    env: Option<bool>,
    cfg: Option<bool>,
    remote: Option<bool>,
    default: bool,
) -> Resolved<bool> {
    if let Some(value) = env {
        Resolved::new(value, ConfigSource::Env)
    } else if let Some(value) = cfg {
        Resolved::new(value, ConfigSource::Config)
    } else if let Some(value) = remote {
        Resolved::new(value, ConfigSource::Remote)
    } else {
        Resolved::new(default, ConfigSource::Default)
    }
}
fn remote_compat_value(
    remote: Option<&crate::util::config::RemoteSettings>,
    key: Option<CompatRemoteKey>,
) -> Option<bool> {
    let remote = remote?;
    match key? {
        CompatRemoteKey::CursorSkills => remote.cursor_skills_enabled,
        CompatRemoteKey::CursorRules => remote.cursor_rules_enabled,
        CompatRemoteKey::CursorAgents => remote.cursor_agents_enabled,
        CompatRemoteKey::CursorMcps => remote.cursor_mcps_enabled,
        CompatRemoteKey::CursorHooks => remote.cursor_hooks_enabled,
        CompatRemoteKey::CursorSessions => remote.cursor_sessions_enabled,
        CompatRemoteKey::ClaudeSkills => remote.claude_skills_enabled,
        CompatRemoteKey::ClaudeRules => remote.claude_rules_enabled,
        CompatRemoteKey::ClaudeAgents => remote.claude_agents_enabled,
        CompatRemoteKey::ClaudeMcps => remote.claude_mcps_enabled,
        CompatRemoteKey::ClaudeHooks => remote.claude_hooks_enabled,
        CompatRemoteKey::ClaudeSessions => remote.claude_sessions_enabled,
        CompatRemoteKey::CodexSessions => remote.codex_sessions_enabled,
    }
}
/// Resolve vendor compatibility cells from TOML and remote settings.
fn resolve_compat_config(
    config: &CompatConfigToml,
    remote: Option<&crate::util::config::RemoteSettings>,
) -> CompatConfig {
    let defaults = CompatConfig::default();
    let mut resolved = defaults;
    for cell in COMPAT_CELLS {
        resolved.set(
            cell,
            resolve_compat_cell(
                cell.env_var(),
                config.value(cell),
                remote_compat_value(remote, cell.remote_key()),
                defaults.value(cell),
            )
            .value,
        );
    }
    resolved
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CompatConfigCellError {
    Unavailable,
    Malformed,
}
pub(crate) fn compat_config_cell(
    raw_config: Result<&toml::Value, ()>,
    cell: pi_tools::types::compat::CompatCell,
) -> Result<Option<bool>, CompatConfigCellError> {
    let raw = raw_config.map_err(|()| CompatConfigCellError::Unavailable)?;
    let Some(compat) = raw.get("compat") else {
        return Ok(None);
    };
    let compat = compat.as_table().ok_or(CompatConfigCellError::Malformed)?;
    let Some(vendor) = compat.get(cell.vendor().as_str()) else {
        return Ok(None);
    };
    let vendor = vendor.as_table().ok_or(CompatConfigCellError::Malformed)?;
    let Some(value) = vendor.get(cell.surface().as_str()) else {
        return Ok(None);
    };
    value
        .as_bool()
        .map(Some)
        .ok_or(CompatConfigCellError::Malformed)
}
/// Resolve only picker-facing session cells from raw config independently.
pub fn resolve_compat_sessions_from_raw(
    raw_config: Result<&toml::Value, ()>,
    remote: Option<&crate::util::config::RemoteSettings>,
) -> CompatConfig {
    let mut config = CompatConfigToml::default();
    for cell in COMPAT_CELLS
        .into_iter()
        .filter(|cell| cell.surface() == CompatSurface::Sessions)
    {
        let value = match compat_config_cell(raw_config, cell) {
            Ok(value) => value,
            Err(error) => {
                tracing::warn!(
                    vendor = cell.vendor().as_str(),
                    ?error,
                    "invalid compat config; disabling foreign sessions"
                );
                Some(false)
            }
        };
        match cell.vendor() {
            CompatVendor::Cursor => config.cursor.sessions = value,
            CompatVendor::Claude => config.claude.sessions = value,
            CompatVendor::Codex => config.codex.sessions = value,
        }
    }
    resolve_compat_config(&config, remote)
}
/// Resolve a string setting: cli > env > config > feature flag. `None` if no source provides a value.
pub(crate) fn resolve_string_flag(
    cli_arg: Option<&str>,
    env_var: &str,
    config_val: Option<&str>,
    feature_flag_val: Option<&str>,
) -> Option<Resolved<String>> {
    if let Some(val) = cli_arg.filter(|s| !s.is_empty()) {
        return Some(Resolved::new(val.to_owned(), ConfigSource::Cli));
    }
    if let Some(val) = env_string(env_var) {
        return Some(Resolved::new(val, ConfigSource::Env));
    }
    if let Some(val) = config_val.filter(|s| !s.is_empty()) {
        return Some(Resolved::new(val.to_owned(), ConfigSource::Config));
    }
    if let Some(val) = feature_flag_val.filter(|s| !s.is_empty()) {
        return Some(Resolved::new(val.to_owned(), ConfigSource::Remote));
    }
    None
}
/// Resolve `enabled` for section-based configs (memory, subagents, etc.).
/// Feature flag only applies when the TOML section is absent.
pub(crate) fn resolve_enabled(
    cli_flag: Option<bool>,
    env_var: &str,
    config_enabled: bool,
    has_local_section: bool,
    feature_flag_val: Option<bool>,
    default: bool,
) -> Resolved<bool> {
    let config_val = if has_local_section {
        Some(config_enabled)
    } else {
        None
    };
    BoolFlag::env(env_var)
        .cli(cli_flag)
        .config(config_val)
        .feature_flag(feature_flag_val)
        .default(default)
        .resolve()
}
pub(crate) use pi_telemetry::config::env_telemetry_mode;
pub(crate) use pi_telemetry::config::{TelemetryConfig, TelemetryMode};
/// Plugin system configuration from `[plugins]` section in config.toml.
///
/// ```toml
/// [plugins]
/// paths = ["~/my-plugins/custom-tools"]
/// disabled = ["user/a1b2c3d4/noisy-plugin"]
/// ```
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct PluginsConfig {
    /// Additional plugin directory paths to load.
    #[serde(default)]
    pub paths: Vec<String>,
    /// Plugin IDs or names to disable. Disabled plugins are discovered
    /// but their components are not loaded into the session.
    #[serde(default)]
    pub disabled: Vec<String>,
    /// Plugin IDs or names to explicitly enable. Used for project-scope plugins
    /// which are disabled by default — adding a plugin here overrides that default.
    #[serde(default)]
    pub enabled: Vec<String>,
    /// CLI `--plugin-dir` paths (populated by CLI arg processing, not config file).
    #[serde(skip)]
    pub cli_plugin_dirs: Vec<std::path::PathBuf>,
}
impl PluginsConfig {
    /// Merge `enabledPlugins` from Claude settings files into this config.
    ///
    /// Reads `enabledPlugins` from `~/.claude/settings.json` only (user scope).
    /// Project-level `<git_root>/.claude/settings.json` is intentionally NOT
    /// read here: a malicious repo could pre-populate `enabledPlugins` to
    /// bypass the project-plugin auto-disable logic in `populate_plugin_lists`,
    /// enabling attacker-controlled hooks (e.g. SessionStart → RCE).
    /// Native `.grok/config.toml` entries already present take precedence:
    /// a name is only added if it isn't already in the opposite list.
    pub(crate) fn merge_claude_enabled_plugins(&mut self, _cwd: Option<&std::path::Path>) {
        if crate::claude_import::is_claude_import_marked_with_log("merge_claude_enabled_plugins") {
            return;
        }
        let mut paths = Vec::new();
        if let Some(home) = dirs::home_dir() {
            paths.push(home.join(".claude").join("settings.json"));
        }
        for path in &paths {
            let (claude_enabled, claude_disabled) =
                pi_agent::plugins::marketplace::load_enabled_disabled_plugins(path);
            for name in claude_enabled {
                if !self.disabled.contains(&name) && !self.enabled.contains(&name) {
                    self.enabled.push(name);
                }
            }
            for name in claude_disabled {
                if !self.enabled.contains(&name) && !self.disabled.contains(&name) {
                    self.disabled.push(name);
                }
            }
        }
    }
    /// Build a `DiscoveryConfig` from this plugins config.
    pub(crate) fn to_discovery_config(
        &self,
    ) -> pi_agent::plugins::discovery::DiscoveryConfig {
        pi_agent::plugins::discovery::DiscoveryConfig {
            cli_plugin_dirs: self.cli_plugin_dirs.clone(),
            config_paths: self.paths.iter().map(std::path::PathBuf::from).collect(),
            disabled: self.disabled.clone(),
            enabled: self.enabled.clone(),
        }
    }
}
/// Feedback submission configuration (`[feedback]` in config.toml).
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct FeedbackConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user: Option<FeedbackUserConfig>,
}
/// Self-reported feedback author identity (never used for authorization).
/// Merged only from trusted config tiers, so a cloned repo can't inject the
/// `command` escape hatch.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct FeedbackUserConfig {
    /// Sources tried in order for the name. `os_user` yields the OS user name;
    /// any other entry is a literal (`$VAR` expanded at load).
    pub name: Vec<String>,
    /// Sources tried in order for the email. `git_email` yields the global git
    /// email; any other entry is a literal (`$VAR` expanded at load) needing `@`.
    pub email: Vec<String>,
    /// Fallback domain for `<name>@<domain>` when no `email` source resolves.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub email_domain: Option<String>,
    /// Optional `sh -c` script printing `{"name","email"}` JSON; its fields win
    /// over the lists above, with per-field fallback. Trusted config tiers only.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct CompactionConfig {
    pub memory_flush: Option<crate::config::MemoryFlushSettings>,
    pub pruning: Option<crate::config::PruningSettings>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct CliConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auto_update: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dismissed_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub installer: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub npm_registry: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub channel: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_tips: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub worktree_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_registry: Option<bool>,
    /// Env `GROK_MINIMUM_VERSION`. See [`crate::util::config::VersionPolicy`] for
    /// the version-policy knobs. (Unrelated to
    /// `version_overrides[].maximum_version`, which gates config patches.)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub minimum_version: Option<String>,
    /// Env `GROK_MAXIMUM_VERSION`. See [`crate::util::config::VersionPolicy`].
    #[serde(skip_serializing_if = "Option::is_none")]
    pub maximum_version: Option<String>,
    /// Env `GROK_REQUIRED_MINIMUM_VERSION`. See [`crate::util::config::VersionPolicy`].
    #[serde(skip_serializing_if = "Option::is_none")]
    pub required_minimum_version: Option<String>,
    /// Env `GROK_REQUIRED_MAXIMUM_VERSION`. See [`crate::util::config::VersionPolicy`].
    #[serde(skip_serializing_if = "Option::is_none")]
    pub required_maximum_version: Option<String>,
    /// Group sessions by repo in the picker and CLI listings.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_picker_grouped: Option<bool>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DiagnosticsConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub crash_handler: Option<bool>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct ModelsConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub default: Option<String>,
    /// The pre-campaign `models.default` (merged user/managed/requirements)
    /// captured when a campaign is overriding the default, so model resolution can
    /// recover if the campaign points at a model missing from the catalog. `None`
    /// when there is nothing to recover to. Runtime-only; never serialized.
    #[serde(skip)]
    pub pre_campaign_default: Option<String>,
    /// Whether an active campaign is currently overriding `models.default`. The
    /// authoritative campaign-driven-default signal (set from the resolved active
    /// set), correct even when the user has no base default. Runtime-only.
    #[serde(skip)]
    pub default_is_campaign_driven: bool,
    /// Persisted effort for the default model; applied in `resolve_model_catalog`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub default_reasoning_effort: Option<ReasoningEffort>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub web_search: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_summary: Option<String>,
    /// Vision model used to transcribe user-supplied
    /// images via a separate endpoint.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image_description: Option<String>,
    /// Model pin for next-prompt suggestions (tab-autocomplete ghost text).
    /// Unset = remote pin, then the client hint / built-in `grok-build-0.1`
    /// default with the catalog guard; see `ModelOverrideConfig::resolve`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompt_suggestion: Option<String>,
    /// Restricts which models are user-selectable for normal chat (picker,
    /// `/model`, `-m`). Non-matching models stay in the catalog but are never
    /// shown, defaulted to, or selectable. Special/internal models (web_search,
    /// image_description, subagents, fork secondary) are exempt.
    ///
    /// Glob patterns (`*`, `?`, `[...]`) match the model id or catalog key,
    /// case-sensitive. Empty = no restriction; an excluded explicit `default`/`-m`
    /// is rejected at startup.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub allowed_models: Option<Vec<String>>,
    /// Force `hidden = true` on these model IDs (still usable via `-m`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hidden_models: Option<Vec<String>>,
    /// Remove these model IDs from the catalog entirely. Wins over `hidden_models`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub disabled_models: Option<Vec<String>>,
    /// Fallback `agent_type` for models without a per-model override.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_type: Option<String>,
    /// Global default request headers applied to every model. A per-model
    /// `[model.<id>].extra_headers` entry overrides per key (case-insensitive).
    #[serde(default, skip_serializing_if = "IndexMap::is_empty")]
    pub extra_headers: IndexMap<String, String>,
    /// Global default values applied to every model that leaves the field
    /// unset; a per-model `[model.<id>]` value always wins. A deliberately
    /// small, allow-listed subset of the per-model fields (only `Option` ones,
    /// so "unset" is unambiguous). Future: these could consolidate into a
    /// `[models.defaults]` sub-table mirroring the per-model schema 1:1; kept
    /// flat for now as that is a larger refactor.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top_p: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_completion_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_retries: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub inference_idle_timeout_secs: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subagent_rate_limit_max_attempts: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stream_tool_calls: Option<bool>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct HarnessConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub block_for_upload: Option<bool>,
    /// Budget (seconds) for the turn-end upload flush when
    /// `block_for_upload` is active. Default 60.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub upload_flush_timeout_secs: Option<u64>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct RelayConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
}
/// `[hub]` section from config.toml.
///
/// Optional default Computer Hub URL for **workspace provider** exposure
/// (`grok workspace` / leader `with_default_hub_url`). Does **not** enable
/// agent-side harness/client connections or alter local session behavior.
///
/// ```toml
/// [hub]
/// url = "wss://hub.x.ai/ws"
/// ```
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct HubConfig {
    /// Hub WebSocket URL (`ws://` or `wss://`) used as the leader default for
    /// `grok workspace start` when the CLI does not pass `--hub-url`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct WorktreePoolConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pool_size: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_count_threshold: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parallelism: Option<usize>,
}
/// `[worktree]` section from config.toml (auto-GC policy lives under `auto_gc`).
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct WorktreeConfigSection {
    #[serde(default)]
    pub auto_gc: crate::util::config::WorktreeAutoGcSettings,
}
/// `[sandbox]` section from config.toml.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SandboxSettingsConfig {
    /// "off", "workspace", "devbox", "read-only", "strict", or custom name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub profile: Option<String>,
    /// Skip bash permission prompts when sandbox is active.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auto_allow_bash: Option<bool>,
}
impl SandboxSettingsConfig {
    pub(crate) fn from_effective_config() -> Self {
        crate::config::load_effective_config()
            .ok()
            .and_then(|v| v.get("sandbox")?.clone().try_into().ok())
            .unwrap_or_default()
    }
    /// Resolve sandbox profile: requirement > CLI > env > config > "off".
    pub fn resolve_profile(
        &self,
        cli_arg: Option<&str>,
        requirement: Option<&str>,
    ) -> Resolved<String> {
        if let Some(val) = requirement {
            return Resolved::new(val.to_owned(), ConfigSource::Requirement);
        }
        resolve_string_flag(cli_arg, "GROK_SANDBOX", self.profile.as_deref(), None)
            .unwrap_or_else(|| Resolved::new("off".to_owned(), ConfigSource::Default))
    }
    /// Resolve auto_allow_bash: requirement > env > config > default (false).
    pub(crate) fn resolve_auto_allow_bash(&self, requirement: Option<bool>) -> Resolved<bool> {
        BoolFlag::env("GROK_SANDBOX_AUTO_ALLOW_BASH")
            .requirement(requirement)
            .config(self.auto_allow_bash)
            .resolve()
    }
}
/// `[marketplace]` section from config.toml (plugin marketplace sources).
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct MarketplaceConfig {
    /// `[[marketplace.sources]]` entries.
    #[serde(default)]
    pub sources: Vec<MarketplaceSourceEntry>,
    /// Written/read out-of-band by `extensions::marketplace`, opaque so a wrong-typed value can't fail load.
    #[serde(default)]
    pub official_marketplace_auto_installed: Option<toml::Value>,
    /// Written/read out-of-band by `extensions::marketplace`, opaque so a wrong-typed value can't fail load.
    #[serde(default)]
    pub default_skills_installs_purged: Option<toml::Value>,
}
/// A single `[[marketplace.sources]]` entry.
#[derive(Clone, Debug, Deserialize)]
pub struct MarketplaceSourceEntry {
    pub name: String,
    #[serde(default)]
    pub path: Option<String>,
    #[serde(default)]
    pub git: Option<String>,
    #[serde(default)]
    pub branch: Option<String>,
}
/// `[storage]` section from config.toml.
///
/// Controls session persistence settings like cleanup TTL.
/// Read by `resolve_cleanup_ttl_days()` in `session/persistence.rs`.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct StorageConfig {
    /// Number of days to keep stale sessions before cleanup. Default: 30.
    pub cleanup_ttl_days: Option<u32>,
}
/// `[paths]` configuration: extra directories to scan for skills, rules, etc.
///
/// These supplement the built-in scan locations (`.grok/skills/`,
/// `.agents/skills/`, `~/.grok/skills/`). They're written by `/import-claude`
/// to preserve previously-discovered Claude directories after the runtime
/// `.claude/` cutoff (see `[claude_compat] imported`).
///
/// Example:
/// ```toml
/// [paths]
/// extra_skill_dirs = ["~/.claude/skills", "/path/to/.claude/skills"]
/// extra_rule_dirs = ["~/.claude/rules"]
/// ```
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PathsConfig {
    /// Additional directories to scan for skills (each contains `<skill>/SKILL.md`).
    pub extra_skill_dirs: Vec<String>,
    /// Additional directories to scan for rules (each contains `*.md`).
    pub extra_rule_dirs: Vec<String>,
}
/// `[permission]` known keys, declared for the unrecognized-key scan only;
/// consumed out-of-band. Keys stay typed so a typo (e.g. `denny`) still warns.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct PermissionKnownKeys {
    /// Compact rule arrays (`parse_toml_permission_section`).
    pub allow: Option<toml::Value>,
    pub deny: Option<toml::Value>,
    pub ask: Option<toml::Value>,
    /// Verbose `[[permission.rules]]` form.
    pub rules: Option<toml::Value>,
}
/// `[shell_environment_policy]` known keys, for the unrecognized-key scan only;
/// the value is parsed at spawn by [`crate::util::config::resolve_shell_env_policy`].
/// `Option<toml::Value>` (no `deny_unknown_fields`) keeps a typo a warning, not a
/// load failure, like [`PermissionKnownKeys`].
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct ShellEnvironmentPolicyKnownKeys {
    pub inherit: Option<toml::Value>,
    pub ignore_default_excludes: Option<toml::Value>,
    pub exclude: Option<toml::Value>,
    pub set: Option<toml::Value>,
    pub include_only: Option<toml::Value>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Config {
    pub features: Features,
    /// `[goal]` section: canonical `/goal` configuration. See [`GoalConfig`].
    #[serde(default)]
    pub goal: GoalConfig,
    #[serde(default)]
    pub workflows: WorkflowsConfig,
    /// `[doom_loop_recovery]` section: the shared settings struct — ONE type
    /// serves this TOML table and the remote remote settings `doom_loop_recovery`
    /// object. See [`crate::util::config::DoomLoopRecoverySettings`].
    #[serde(default)]
    pub doom_loop_recovery: crate::util::config::DoomLoopRecoverySettings,
    /// `[worktree]` section (currently `[worktree.auto_gc]` only).
    #[serde(default)]
    pub worktree: WorktreeConfigSection,
    /// `[auto_mode]` section: Auto permission-mode configuration. See [`AutoModeConfig`].
    #[serde(default)]
    pub auto_mode: AutoModeConfig,
    /// What `[features]` said in the merged layers. One tier of
    /// [`Config::feature`].
    #[serde(skip)]
    pub(crate) feature_values: BTreeMap<Feature, bool>,
    /// `[model.*]` overrides from config.toml. Resolve via `resolve_model_list()`.
    #[serde(skip)]
    pub config_models: IndexMap<String, ConfigModelOverride>,
    #[serde(skip)]
    pub config_warnings: Vec<super::config_model_override_parse::ConfigWarning>,
    pub grok_com_config: GrokComConfig,
    /// `[auth_provider.<name>]` tables, populated by
    /// [`parse_auth_providers`] from trusted config layers only.
    #[serde(skip)]
    pub auth_providers: IndexMap<String, crate::auth::AuthProviderConfig>,
    #[serde(skip)]
    pub model_providers: IndexMap<String, ModelProviderConfig>,
    /// Written by the client via `config_toml_edit`; absorbed so it isn't
    /// flagged as an unrecognized key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hints: Option<toml::Value>,
    #[serde(default)]
    pub ui: UiConfig,
    #[serde(default)]
    pub toolset: ShellToolsetConfig,
    /// Validation only; the value is parsed at spawn by `resolve_shell_env_policy`.
    #[serde(default, skip_serializing)]
    pub shell_environment_policy: ShellEnvironmentPolicyKnownKeys,
    #[serde(default)]
    pub endpoints: EndpointsConfig,
    #[serde(default)]
    pub telemetry: TelemetryConfig,
    /// Session behavior configuration.
    #[serde(default)]
    pub session: SessionConfig,
    /// Agent definition selection configuration.
    /// Set in `config.toml` under `[agent]` to choose which agent definition
    /// is used for all sessions (unless overridden by CLI flag or ACP meta).
    #[serde(default)]
    pub agent: AgentSelectionConfig,
    #[serde(default)]
    pub repo_changes_dedup: RepoChangesDedupConfig,
    /// Skills discovery configuration.
    #[serde(default)]
    pub skills: SkillsConfig,
    /// Raw `[compat]` vendor-compatibility config (per-vendor × per-surface
    /// toggles). Resolved into [`Config::compat_resolved`] by
    /// `resolve_runtime_fields`.
    #[serde(default)]
    pub compat: CompatConfigToml,
    /// Plugin system configuration.
    #[serde(default)]
    pub plugins: PluginsConfig,
    /// Feedback submission configuration.
    #[serde(default)]
    pub feedback: FeedbackConfig,
    /// Filesystem path overrides (`[paths]` in config.toml).
    #[serde(default)]
    pub paths: PathsConfig,
    #[serde(default, skip_serializing)]
    pub cli: CliConfig,
    #[serde(default, skip_serializing)]
    pub models: ModelsConfig,
    #[serde(default, skip_serializing)]
    pub harness: HarnessConfig,
    #[serde(default, skip_serializing)]
    pub relay: RelayConfig,
    /// Computer Hub configuration (`[hub]` in config.toml).
    #[serde(default, skip_serializing)]
    pub hub: HubConfig,
    #[serde(default, skip_serializing)]
    pub worktree_pool: WorktreePoolConfig,
    #[serde(default, skip_serializing)]
    pub sandbox: SandboxSettingsConfig,
    #[serde(default, skip_serializing)]
    pub mcp_servers: std::collections::HashMap<String, crate::util::config::McpServerConfig>,
    #[serde(default, skip_serializing)]
    pub disabled_mcp_servers: Vec<String>,
    #[serde(default, skip_serializing)]
    pub disabled_mcp_tools: std::collections::HashMap<String, Vec<String>>,
    #[serde(default, skip_serializing)]
    pub subagents: crate::config::SubagentsConfig,
    #[serde(default, skip_serializing)]
    pub memory: crate::config::MemorySettings,
    #[serde(default, skip_serializing)]
    pub compaction: CompactionConfig,
    #[serde(default, skip_serializing)]
    pub managed_mcps: crate::config::ManagedMcpsConfig,
    /// `[auth]` alias — consumed by `expand_auth_alias` before serde.
    /// Typed as `GrokComConfig` (same schema) so sub-field typos are caught.
    #[serde(default, skip_serializing)]
    pub auth: Option<GrokComConfig>,
    /// `[desktop]` section — owned by grok-desktop (Electron app), opaque to the CLI agent.
    #[serde(default, skip_serializing)]
    pub desktop: Option<toml::Value>,
    /// Top-level `announcements` array — consumed by `resolve_announcements`.
    #[serde(default, skip_serializing)]
    pub announcements: Vec<pi_announcements::RemoteAnnouncement>,
    /// `[tips]` section — consumed by `merge_tips`.
    #[serde(default, skip_serializing)]
    pub tips: Option<crate::util::config::TipsOverride>,
    /// `[permission]` — consumed out-of-band; see [`PermissionKnownKeys`].
    #[serde(default, skip_serializing)]
    pub permission: PermissionKnownKeys,
    /// `[tools]` — also read by `ToolsConfig::resolve()`.
    #[serde(default, skip_serializing)]
    pub tools: crate::config::ToolsConfig,
    /// `[storage]` — also read by `resolve_cleanup_ttl_days()`.
    #[serde(default, skip_serializing)]
    pub storage: StorageConfig,
    /// `[marketplace]` — also read by `pi_plugin_marketplace::load_sources()`.
    #[serde(default, skip_serializing)]
    pub marketplace: MarketplaceConfig,
    /// `[diagnostics]` — crash handler toggle (`load_crash_handler_enabled_sync`).
    #[serde(default, skip_serializing)]
    pub diagnostics: DiagnosticsConfig,
    /// Storage mode for session persistence.
    /// When running in relay/headless mode, this should be set to Writeback.
    /// Defaults to reading from GROK_STORAGE_MODE env var.
    #[serde(skip)]
    pub storage_mode: StorageMode,
    /// CLI override for the default model ID.
    #[serde(skip)]
    pub default_model_override: Option<String>,
    /// CLI override for reasoning effort.
    #[serde(skip)]
    pub reasoning_effort_override: Option<ReasoningEffort>,
    /// CLI override for the web search model ID.
    #[serde(skip)]
    pub web_search_model_override: Option<String>,
    /// CLI override for the session summary model ID.
    #[serde(skip)]
    pub session_summary_model_override: Option<String>,
    /// CLI override for YOLO mode (auto-approve all permissions).
    /// Takes precedence over default settings.
    #[serde(skip)]
    pub default_yolo_mode: bool,
    /// Start sessions in auto permission mode (classifier) when no per-session override.
    pub default_auto_mode: bool,
    /// CLI memory override preserved across config and remote-setting refreshes.
    #[serde(skip)]
    pub memory_enabled_override: Option<bool>,
    /// Original CLI `--subagents` tri-state, preserved for re-resolution
    /// when remote settings settings are refreshed on /new.
    #[serde(skip)]
    pub cli_subagents: Option<bool>,
    /// Resolved memory configuration. `None` when memory is disabled.
    /// Resolved by [`RuntimeResolutionContext`] in [`Config::resolve_runtime_fields`].
    #[serde(skip)]
    pub memory_config: Option<crate::config::MemoryConfig>,
    /// CLI override: path to an agent profile (.md file with YAML frontmatter).
    #[serde(skip)]
    pub agent_profile_path: Option<PathBuf>,
    /// Client version string (e.g., "0.1.77 (abc1234)").
    /// Set by the TUI/CLI launcher and used as fallback when clients don't provide clientVersion.
    #[serde(skip)]
    pub client_version: Option<String>,
    /// The mode in which the agent is running.
    /// Determines behavior like relay sync enablement (only enabled in TUI mode).
    #[serde(skip)]
    pub mode: AgentMode,
    /// Remote settings fetched from cli-chat-proxy at startup.
    /// Used for upload limits (replaces on-demand /v1/storage/limits fetch).
    #[serde(skip)]
    pub remote_settings: Option<crate::util::config::RemoteSettings>,
    #[serde(skip)]
    pub cli_agents: Vec<pi_agent::config::AgentDefinition>,
    #[serde(skip)]
    pub cli_agent_overrides: CliAgentOverrides,
    /// Whether subagent (task tool) support is enabled. Enabled by default;
    /// disabled only via `GROK_SUBAGENTS=0` or `[subagents] enabled = false`.
    /// Not remotely gated.
    #[serde(skip)]
    pub subagents_enabled: bool,
    /// Resolved max subagent nesting depth (see
    /// [`crate::config::SubagentsConfig::resolve_max_depth`]).
    #[serde(skip)]
    pub subagents_max_depth: u32,
    #[serde(skip)]
    pub subagents_max_concurrent: usize,
    /// Resolved concurrent subagent turn-sampling limit feeding the shared
    /// semaphore. See [`crate::config::SubagentsConfig::resolve_sampling_limit`].
    #[serde(skip)]
    pub subagents_sampling_limit: usize,
    #[serde(skip)]
    pub subagents_limit_behavior:
        pi_tools::implementations::grok_build::task::admission::LimitBehavior,
    #[serde(skip)]
    pub workflow_max_concurrent_agents: usize,
    #[serde(skip)]
    pub media_gen_batch_limits: pi_tools::media_gen_limits::MediaGenBatchLimits,
    /// Per-subagent model ID overrides from `[subagents.models]` in config.toml.
    /// Keys are agent names, values are model IDs. Set alongside `subagents_enabled`
    /// from `SubagentsConfig::resolve()`.
    #[serde(skip)]
    pub subagent_model_overrides: std::collections::HashMap<String, String>,
    /// Per-subagent enable/disable toggles from `[subagents.toggle]` in config.toml.
    /// Keys are agent names, values are booleans. Omitted agents default to enabled.
    #[serde(skip)]
    pub subagent_toggle: std::collections::HashMap<String, bool>,
    /// Trust-independent roles from inline, user, and bundled sources.
    #[serde(skip)]
    pub subagent_roles:
        std::collections::HashMap<String, pi_subagent_resolution::config::SubagentRole>,
    /// Trust-independent personas from inline, user, and bundled sources.
    #[serde(skip)]
    pub subagent_personas:
        std::collections::HashMap<String, pi_subagent_resolution::config::SubagentPersona>,
    /// Whether web search is force-disabled via `--disable-web-search` CLI flag.
    /// When true, the web search tool is never added to the agent toolset
    /// regardless of available credentials.
    #[serde(default)]
    pub disable_web_search: bool,
    /// Whether the runtime turn-end TodoGate is force-enabled via the
    /// `--todo-gate` CLI flag. Session-scoped — not persisted. When
    /// true, flips the runtime policy's `enabled` bit on regardless of
    /// remote settings or the built-in default (which is `false`).
    /// The gate runs only while a `/goal` is active (goal reminders
    /// inject `<task_completion_discipline>`); global built-in templates
    /// do not activate it.
    #[serde(skip)]
    pub todo_gate: bool,
    /// Path for the Layer-3 LazinessDetector debug log
    /// (`--laziness-debug-log`). When `Some`, the classifier fires
    /// after every turn (bypassing the idle wait, the per-model
    /// enable gate, and the nudge cap) and appends a JSONL line per
    /// fire to this file. Observation-only — no nudges are injected
    /// in this mode. Session-scoped, not persisted.
    #[serde(skip)]
    pub laziness_debug_log: Option<std::path::PathBuf>,
    /// Whether tools should respect `.gitignore` patterns.
    /// When `true`, all tools including `read_file` block gitignored files.
    /// When `false` (default), each tool applies its own default
    /// (`read_file` allows, others block).
    /// Resolved by [`crate::config::ToolsConfig::resolve`].
    #[serde(skip)]
    pub respect_gitignore: bool,
    /// When `true` (and no valid `zdr_video_output_s3` bucket is set), the video
    /// tools were marked zdr-restricted by the (now removed) in-process agent
    /// runtime: they stayed advertised but short-circuited at call time with
    /// setup guidance. Resolved by [`crate::config::ToolsConfig::resolve`].
    #[serde(skip)]
    pub disable_zdr_incompatible_tools: bool,
    /// S3 config for ZDR video output (presigned upload to team bucket).
    /// Only used when `disable_zdr_incompatible_tools` is `true` and the
    /// config is valid. Resolved by [`crate::config::ToolsConfig::resolve`].
    #[serde(skip)]
    pub zdr_video_output_s3:
        Option<pi_tools::implementations::grok_build::video_gen::ZdrVideoOutputS3Config>,
    /// Whether to enrich path-not-found errors with CWD reminders,
    /// "dropped repo folder" correction, and similar-name suggestions.
    /// Default `false`. Enabled via remote settings.
    /// Serialized to `config.json` on GCS so traces can distinguish
    /// which sessions had path-not-found hints active.
    #[serde(default)]
    pub path_not_found_hints: bool,
    /// Whether to fetch managed MCP configs from the managed connectors service at startup.
    /// Resolved by [`crate::config::ManagedMcpsConfig::resolve`]: env var >
    /// config.toml > remote settings > default (off in headless, on in interactive).
    #[serde(skip)]
    pub managed_mcps_enabled: bool,
    #[serde(skip)]
    pub managed_mcp_gateway_tools_enabled: bool,
    /// Resolved vendor-compat config (env → `[compat]` TOML → feature flag →
    /// default ON), built from `compat` + `remote_settings` in
    /// `resolve_runtime_fields`. Threaded into skills / rules / AGENTS.md
    /// discovery.
    #[serde(skip)]
    pub compat_resolved: CompatConfig,
    /// Enforced requirement pins from `requirements.toml`.
    #[serde(skip)]
    pub requirements: Requirements,
    /// Model ID for web_search.
    #[serde(skip)]
    pub web_search_model: String,
    /// Session title model. Resolved to the compiled default
    /// (`default_session_summary_model`) when unset; see `ModelOverrideConfig::resolve`.
    #[serde(skip)]
    pub session_summary_model: Option<String>,
    /// Image describe model (`grok-build` default via `ModelOverrideConfig::resolve`).
    #[serde(skip)]
    pub image_description_model: Option<String>,
    /// Next-prompt suggestion model pin (`env > [models] prompt_suggestion >
    /// remote`), consumed catalog-guarded by `handle_suggest_prompt`; see
    /// `ModelOverrideConfig::resolve`.
    #[serde(skip)]
    pub prompt_suggest_model_pin: crate::config::PromptSuggestModelPin,
}
#[derive(Debug, Clone, Default)]
pub struct CliAgentOverrides {
    pub tools: Option<Vec<String>>,
    pub disallowed_tools: Option<Vec<String>>,
    pub permission_rules: Vec<pi_workspace::permission::types::PermissionRule>,
    pub max_turns: Option<u32>,
    pub permission_mode: Option<pi_agent::config::PermissionMode>,
}
pub use pi_agent::config::AgentDefinition;
pub use pi_agent::config::PermissionMode;
pub use pi_shared::ui_config::{ContextualHints, UiConfig};
/// Configuration for selecting the agent definition.
///
/// Set in `config.toml` under `[agent]`:
///
/// ```toml
/// [agent]
/// # Use a named agent (looked up via discovery: .grok/agents/, ~/.grok/agents/, built-ins)
/// name = "my-custom-agent"
///
/// # OR: path to an agent definition file (.md with YAML frontmatter)
/// definition = "/path/to/my-agent.md"
/// ```
///
/// Priority (highest to lowest):
/// 1. ACP session-level `_meta.agentProfile`
/// 2. CLI `--agent-profile` flag
/// 3. `[agent]` config.toml section (this config)
/// 4. `GROK_AGENT` env var
/// 5. Default `grok-build` agent
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct AgentSelectionConfig {
    /// Name of a built-in or discovered agent definition.
    /// Looked up via `pi_agent::discovery::by_name_in_cwd()`.
    /// Examples: "grok-build", "browser-use", or a custom agent name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Path to an agent definition file (.md with YAML frontmatter).
    /// When set, the agent is loaded from this file.
    /// Supports environment variable expansion (e.g., `$HOME/.grok/agents/my-agent.md`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub definition: Option<PathBuf>,
    /// Global system-prompt identity label. Per-model override wins.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system_prompt_label: Option<String>,
}
/// Configuration for session behavior.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct SessionConfig {
    /// Context window usage percentage (0-100) at which auto-compact is triggered.
    /// When the session's token usage exceeds this percentage of the model's context window,
    /// the conversation will be automatically summarized to free up space.
    ///
    /// `None` means "user didn't set it"; the resolver in
    /// `crate::util::config::resolve_auto_compact_threshold_percent` falls
    /// through to remote tiers and ultimately the hardcoded default 85.
    /// Read this field via the resolver — not directly — to honor the full
    /// precedence chain (env, per-model, remote, default).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub auto_compact_threshold_percent: Option<u8>,
    /// Whether to load environment variables from .envrc files.
    /// When enabled, the session will parse .envrc in the workspace directory
    /// and inject the environment variables into bash commands.
    /// Defaults to `true` when unset. `Option<bool>` so `None`
    /// round-trips as absent on disk (managed config wins over default).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub load_envrc: Option<bool>,
}
/// Configuration for change-archive deduplication.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct RepoChangesDedupConfig {
    pub enabled: bool,
    /// Include inline content even when references exist.
    pub include_inline_fallback: bool,
    /// Omit inline content larger than this (0 = no limit).
    pub max_inline_bytes: usize,
    /// Deduplicate untracked file content.
    pub dedup_untracked: bool,
    /// Deduplicate binary file blobs.
    pub dedup_binary: bool,
    /// Skip untracked files larger than this (0 = no limit).
    pub untracked_max_bytes: usize,
    /// Optional glob patterns to exclude untracked paths.
    pub untracked_exclude_globs: Vec<String>,
}
impl Default for RepoChangesDedupConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            include_inline_fallback: false,
            max_inline_bytes: 0,
            dedup_untracked: true,
            dedup_binary: true,
            untracked_max_bytes: 0,
            untracked_exclude_globs: Vec::new(),
        }
    }
}
impl Default for Config {
    fn default() -> Self {
        let endpoints = EndpointsConfig::default();
        let mut cfg = Self {
            features: Features::default(),
            goal: GoalConfig::default(),
            workflows: WorkflowsConfig::default(),
            doom_loop_recovery: crate::util::config::DoomLoopRecoverySettings::default(),
            worktree: WorktreeConfigSection::default(),
            auto_mode: AutoModeConfig::default(),
            feature_values: BTreeMap::new(),
            config_models: IndexMap::new(),
            config_warnings: Vec::new(),
            grok_com_config: GrokComConfig::default(),
            auth_providers: IndexMap::new(),
            model_providers: IndexMap::new(),
            hints: None,
            ui: UiConfig::default(),
            toolset: ShellToolsetConfig::default(),
            shell_environment_policy: ShellEnvironmentPolicyKnownKeys::default(),
            endpoints,
            telemetry: TelemetryConfig::default(),
            session: SessionConfig::default(),
            agent: AgentSelectionConfig::default(),
            repo_changes_dedup: RepoChangesDedupConfig::default(),
            skills: SkillsConfig::default(),
            compat: CompatConfigToml::default(),
            plugins: PluginsConfig::default(),
            feedback: FeedbackConfig::default(),
            paths: PathsConfig::default(),
            cli: CliConfig::default(),
            models: ModelsConfig::default(),
            harness: HarnessConfig::default(),
            relay: RelayConfig::default(),
            hub: HubConfig::default(),
            worktree_pool: WorktreePoolConfig::default(),
            sandbox: SandboxSettingsConfig::default(),
            mcp_servers: std::collections::HashMap::new(),
            disabled_mcp_servers: Vec::new(),
            disabled_mcp_tools: std::collections::HashMap::new(),
            subagents: crate::config::SubagentsConfig::default(),
            memory: crate::config::MemorySettings::default(),
            compaction: CompactionConfig::default(),
            managed_mcps: crate::config::ManagedMcpsConfig::default(),
            auth: None,
            desktop: None,
            announcements: Vec::new(),
            tips: None,
            permission: PermissionKnownKeys::default(),
            tools: crate::config::ToolsConfig::default(),
            storage: StorageConfig::default(),
            marketplace: MarketplaceConfig::default(),
            diagnostics: DiagnosticsConfig::default(),
            storage_mode: StorageMode::resolve(None, None),
            default_model_override: None,
            reasoning_effort_override: None,
            web_search_model_override: None,
            session_summary_model_override: None,
            default_yolo_mode: false,
            default_auto_mode: false,
            agent_profile_path: None,
            client_version: Some(pi_version::VERSION.to_string()),
            mode: AgentMode::default(),
            remote_settings: None,
            cli_agents: Vec::new(),
            cli_agent_overrides: CliAgentOverrides::default(),
            subagents_enabled: true,
            subagents_max_depth: crate::config::SubagentsConfig::DEFAULT_MAX_DEPTH,
            subagents_max_concurrent:
                pi_tools::implementations::grok_build::task::admission::DEFAULT_MAX_CONCURRENT,
            subagents_sampling_limit:
                pi_tools::implementations::grok_build::task::admission::DEFAULT_MAX_CONCURRENT,
            subagents_limit_behavior: Default::default(),
            workflow_max_concurrent_agents:
                crate::session::workflow::host_service::DEFAULT_WORKFLOW_MAX_CONCURRENT_AGENTS,
            media_gen_batch_limits: pi_tools::media_gen_limits::MediaGenBatchLimits::default(
            ),
            subagent_model_overrides: std::collections::HashMap::new(),
            subagent_toggle: std::collections::HashMap::new(),
            subagent_roles: std::collections::HashMap::new(),
            subagent_personas: std::collections::HashMap::new(),
            disable_web_search: false,
            todo_gate: false,
            laziness_debug_log: None,
            respect_gitignore: false,
            disable_zdr_incompatible_tools: false,
            zdr_video_output_s3: None,
            path_not_found_hints: false,
            memory_enabled_override: None,
            cli_subagents: None,
            memory_config: None,
            managed_mcps_enabled: true,
            managed_mcp_gateway_tools_enabled: false,
            compat_resolved: CompatConfig::default(),
            requirements: Requirements::default(),
            web_search_model: crate::models::default_web_search_model().to_owned(),
            session_summary_model: None,
            image_description_model: None,
            prompt_suggest_model_pin: crate::config::PromptSuggestModelPin::Unpinned,
        };
        cfg.apply_env_overrides();
        cfg
    }
}
/// `[features]` booleans read straight off the raw TOML, with no [`Features`]
/// field. The catch-all in [`Features`] types every such key, so this list only
/// decides which of them are known enough not to warn. A key missing from it
/// costs a visible false alarm, not a silent hole in the check. `image_edit` is
/// left out on purpose, because only a pin sets it, so a plain entry in a
/// user's config stays an unrecognized key.
pub(crate) const UNMIRRORED_BOOLEAN_FEATURES: &[&str] = &[
    "campaigns",
    "remember_mode",
    "remote_fetch",
    "zdr_access_enabled",
];
/// A value written where a boolean was meant. Only these fail the load, so a
/// key carrying some later release's typed value does not stop an older build
/// from starting on the same config.
fn reads_as_a_boolean(value: &toml::Value) -> bool {
    match value {
        toml::Value::String(text) => {
            matches!(
                text.trim().to_ascii_lowercase().as_str(),
                "true" | "false" | "yes" | "no" | "on" | "off" | "1" | "0"
            )
        }
        toml::Value::Integer(number) => matches!(*number, 0 | 1),
        _ => false,
    }
}
/// No file is named: the load sees one merged document.
fn non_boolean_feature_error(path: &str, value: &toml::Value) -> String {
    let found = match value {
        toml::Value::String(text) => format!("the quoted \"{text}\""),
        toml::Value::Integer(number) => number.to_string(),
        other => other.type_str().to_owned(),
    };
    format!("{path}: expected true or false, found {found}")
}
/// Config paths read by raw-layer resolvers, not [`Config`] serde fields, so
/// `serde_ignored` must not report them as unrecognized keys.
const NON_SERDE_CONFIG_PATHS: &[&str] = &[crate::util::config::SLASH_COMMAND_TAGS_CONFIG_PATH];
/// [`NON_SERDE_CONFIG_PATHS`] plus the multi-path groups, every registered
/// feature, and every [`UNMIRRORED_BOOLEAN_FEATURES`] key.
fn is_non_serde_config_path(path: &str) -> bool {
    NON_SERDE_CONFIG_PATHS.contains(&path)
        || crate::util::config::WEB_SEARCH_DOMAIN_CONFIG_PATHS.contains(&path)
        || FEATURES.iter().any(|spec| spec.path == path)
        || path
            .strip_prefix("features.")
            .is_some_and(|key| UNMIRRORED_BOOLEAN_FEATURES.contains(&key))
}
/// Parse `[auth_provider.<name>]` tables leniently: a malformed entry warns
/// (surfaced by `grok inspect`) and is skipped, so it fails closed for the
/// models referencing it instead of failing the whole config.
fn parse_auth_providers(
    raw_config: &toml::Value,
) -> (
    IndexMap<String, crate::auth::AuthProviderConfig>,
    Vec<super::config_model_override_parse::ConfigWarning>,
) {
    use super::config_model_override_parse::{ConfigWarning, ConfigWarningKind};
    let mut providers = IndexMap::new();
    let mut warnings = Vec::new();
    let Some(section) = raw_config.get("auth_provider") else {
        return (providers, warnings);
    };
    let Some(table) = section.as_table() else {
        warnings.push(ConfigWarning::auth_provider_section(
            ConfigWarningKind::NotATable,
            format!(
                "`auth_provider` must be a table of [auth_provider.<name>] entries, got {}; \
                 all auth providers ignored",
                section.type_str()
            ),
        ));
        return (providers, warnings);
    };
    for (name, value) in table {
        let mut unknown = Vec::new();
        match serde_ignored::deserialize::<_, _, crate::auth::AuthProviderConfig>(
            value.clone(),
            |path| unknown.push(path.to_string()),
        ) {
            Ok(provider) => {
                for key in unknown {
                    warnings.push(ConfigWarning::auth_provider(
                        name,
                        Some(key.as_str()),
                        ConfigWarningKind::UnknownField,
                        "unrecognized key; field ignored".to_owned(),
                    ));
                }
                for (field, kind, reason) in auth_config_issues(&provider) {
                    warnings.push(ConfigWarning::auth_provider(
                        name,
                        Some(field),
                        kind,
                        reason,
                    ));
                }
                providers.insert(name.clone(), provider);
            }
            Err(error) => {
                warnings.push(ConfigWarning::auth_provider(
                    name,
                    None,
                    ConfigWarningKind::InvalidValue,
                    format!(
                        "failed to parse ({error}); provider skipped, referencing models \
                         resolve with no credential"
                    ),
                ));
            }
        }
    }
    (providers, warnings)
}
impl Config {
    /// Deserialize the merged `base` document, also returning the ignored key
    /// paths whose top-level key appears in `user_config`. Paths outside it
    /// can only come from the serialized-defaults half of the merge and must
    /// not be blamed on the user.
    fn deserialize_collecting_unrecognized(
        base: toml::Value,
        user_config: &toml::Value,
    ) -> Result<(Self, Vec<String>), String> {
        let mut unused_keys = Vec::new();
        let config: Self = serde_ignored::deserialize(base, |path| {
            unused_keys.push(path.to_string());
        })
        .map_err(|e| e.to_string())?;
        let entries = &config.features.entries;
        unused_keys.extend(
            entries
                .flags
                .keys()
                .chain(&entries.ignored)
                .map(|key| format!("features.{key}")),
        );
        let unrecognized_keys = match user_config.as_table() {
            Some(user_table) => unused_keys
                .into_iter()
                .filter(|path| {
                    let top_level = path.split('.').next().unwrap_or(path);
                    user_table.contains_key(top_level)
                })
                .filter(|path| !is_non_serde_config_path(path))
                .collect(),
            None => Vec::new(),
        };
        Ok((config, unrecognized_keys))
    }
    pub fn new_from_toml_cfg(raw_config: &toml::Value) -> Result<Self, String> {
        let raw_config = &Self::expand_auth_alias(raw_config);
        let super::config_model_override_parse::ParsedModelOverrides {
            models: config_models,
            warnings: config_warnings,
        } = super::config_model_override_parse::parse_model_overrides(raw_config);
        let (mut auth_providers, auth_provider_warnings) = parse_auth_providers(raw_config);
        let (model_providers, mut model_provider_warnings) = parse_model_providers(raw_config);
        for (id, provider) in &model_providers {
            if let Some(auth) = &provider.auth {
                let synthetic = model_provider_auth_name(id);
                if auth_providers.contains_key(&synthetic) {
                    model_provider_warnings
                        .push(
                            super::config_model_override_parse::ConfigWarning::model_provider(
                                id,
                                Some("auth"),
                                super::config_model_override_parse::ConfigWarningKind::ConflictingFields,
                                format!(
                                "inline auth overwrites a hand-written \
                                 [auth_provider.\"{synthetic}\"]; the `model_provider:` prefix is \
                                 a reserved namespace"
                            ),
                            ),
                        );
                }
                auth_providers.insert(synthetic, auth.clone());
            }
        }
        let mut base = toml::Value::try_from(Self::default()).map_err(|e| e.to_string())?;
        if let toml::Value::Table(ref mut t) = base {
            t.remove("model");
        }
        let mut raw_without_model_sections = raw_config.clone();
        if let toml::Value::Table(ref mut t) = raw_without_model_sections {
            t.remove("model");
            t.remove("auth_provider");
            t.remove("model_providers");
        }
        let parsed_mcp_servers =
            crate::util::config::parse_mcp_servers_from_toml(&raw_without_model_sections);
        if let toml::Value::Table(ref mut t) = raw_without_model_sections {
            t.remove("mcp_servers");
        }
        crate::config::deep_merge_toml(&mut base, &raw_without_model_sections);
        if let toml::Value::Table(ref mut t) = base {
            t.remove("mcp_servers");
        }
        let (mut config, mut unrecognized_keys) =
            Self::deserialize_collecting_unrecognized(base, &raw_without_model_sections)?;
        config.mcp_servers = parsed_mcp_servers.into_iter().collect();
        config.config_models = config_models;
        config.config_warnings = config_warnings;
        config.auth_providers = auth_providers;
        config.model_providers = model_providers;
        for spec in FEATURES {
            let Some(&value) = config.features.entries.flags.get(spec.key) else {
                continue;
            };
            config.feature_values.insert(spec.id, value);
        }
        config.config_warnings.extend(auth_provider_warnings);
        config.config_warnings.extend(model_provider_warnings);
        unrecognized_keys.sort();
        for key in unrecognized_keys {
            config.config_warnings.push(
                super::config_model_override_parse::ConfigWarning::config_key(
                    key,
                    super::config_model_override_parse::ConfigWarningKind::UnknownField,
                    "unrecognized config key".to_owned(),
                ),
            );
        }
        let declared_provider_names: std::collections::HashSet<&str> = raw_config
            .get("auth_provider")
            .and_then(toml::Value::as_table)
            .map(|t| t.keys().map(String::as_str).collect())
            .unwrap_or_default();
        let declared_model_provider_names: std::collections::HashSet<&str> = raw_config
            .get("model_providers")
            .and_then(toml::Value::as_table)
            .map(|t| t.keys().map(String::as_str).collect())
            .unwrap_or_default();
        for (model_key, model) in &config.config_models {
            if let Some(ref name) = model.auth_provider
                && !config.auth_providers.contains_key(name)
                && !declared_provider_names.contains(name.as_str())
            {
                config.config_warnings.push(
                    super::config_model_override_parse::ConfigWarning::model(
                        model_key,
                        Some("auth_provider"),
                        super::config_model_override_parse::ConfigWarningKind::InvalidValue,
                        format!(
                            "references [auth_provider.{name}], which is not defined; \
                             the model resolves with no provider credential"
                        ),
                    ),
                );
            }
            if let Some(ref id) = model.model_provider
                && !config.model_providers.contains_key(id)
                && !declared_model_provider_names.contains(id.as_str())
            {
                config.config_warnings.push(
                    super::config_model_override_parse::ConfigWarning::model(
                        model_key,
                        Some("model_provider"),
                        super::config_model_override_parse::ConfigWarningKind::InvalidValue,
                        format!(
                            "references [model_providers.{id}], which is not defined; \
                             provider defaults are not applied — the model uses its own \
                             credential if set, otherwise fails closed on a custom endpoint"
                        ),
                    ),
                );
            }
        }
        for (id, provider) in &config.model_providers {
            if let Some(ref name) = provider.auth_provider
                && !config.auth_providers.contains_key(name)
                && !declared_provider_names.contains(name.as_str())
            {
                config.config_warnings.push(
                    super::config_model_override_parse::ConfigWarning::model_provider(
                        id,
                        Some("auth_provider"),
                        super::config_model_override_parse::ConfigWarningKind::InvalidValue,
                        format!(
                            "references [auth_provider.{name}], which is not defined; \
                             inheriting models fail closed with no provider credential"
                        ),
                    ),
                );
            }
        }
        if let Some(problem) = config.ui.status_line.problem() {
            config.config_warnings.push(
                super::config_model_override_parse::ConfigWarning::config_key(
                    "ui.status_line".to_owned(),
                    super::config_model_override_parse::ConfigWarningKind::InvalidValue,
                    problem.to_string(),
                ),
            );
        }
        for key in config.ui.status_line.unknown_keys() {
            config.config_warnings.push(
                super::config_model_override_parse::ConfigWarning::config_key(
                    format!("ui.status_line.{key}"),
                    super::config_model_override_parse::ConfigWarningKind::UnknownField,
                    "unrecognized config key".to_owned(),
                ),
            );
        }
        super::config_model_override_parse::log_config_warnings(&config.config_warnings);
        if config.grok_com_config.oidc.is_none() {
            config.grok_com_config.oidc = OidcAuthConfig::from_env();
        }
        if config.grok_com_config.oidc.is_none() && config.grok_com_config.oauth2.is_none() {
            config.grok_com_config.oauth2 = crate::auth::OAuth2ProviderConfig::from_env();
        }
        if config.client_version.is_none() {
            config.client_version = Self::default().client_version;
        }
        let model_overrides =
            crate::config::ModelOverrideConfig::resolve(None, None, raw_config, None);
        config.web_search_model = model_overrides.web_search;
        config.session_summary_model = model_overrides.session_summary;
        config.image_description_model = model_overrides.image_description;
        config.prompt_suggest_model_pin = model_overrides.prompt_suggestion;
        config.apply_env_overrides();
        Ok(config)
    }
    /// Populate trust-independent `#[serde(skip)]` subagent base fields.
    ///
    /// Must be called after `new_from_toml_cfg` on the **primary startup path**
    /// before the config is handed to the agent. Project definitions are overlaid
    /// per cwd after that cwd's authoritative folder-trust resolve.
    pub(crate) fn resolve_subagents(&mut self, cli_flag: bool, raw_config: &toml::Value) {
        let sa = crate::config::SubagentsConfig::resolve(cli_flag, raw_config);
        let remote_settings = self.remote_settings.clone();
        self.resolve_subagent_limits(&sa, remote_settings.as_ref());
        self.subagents_enabled = sa.enabled;
        self.subagent_model_overrides = sa.models;
        self.subagent_toggle = sa.toggle;
        self.subagent_roles = sa.roles;
        self.subagent_personas = sa.personas;
        let env = std::env::var(crate::config::SubagentsConfig::ENV_MAX_DEPTH).ok();
        let remote = self
            .remote_settings
            .as_ref()
            .and_then(|r| r.subagents_max_depth);
        self.subagents_max_depth =
            crate::config::SubagentsConfig::resolve_max_depth(env.as_deref(), sa.max_depth, remote);
    }
    fn resolve_subagent_limits(
        &mut self,
        sa: &crate::config::SubagentsConfig,
        remote: Option<&crate::util::config::RemoteSettings>,
    ) {
        use crate::config::SubagentsConfig;
        let env = |name: &str| std::env::var(name).ok();
        self.subagents_max_concurrent = SubagentsConfig::resolve_max_concurrent(
            env(SubagentsConfig::ENV_MAX_CONCURRENT).as_deref(),
            sa.max_concurrent,
            remote.and_then(|r| r.subagents_max_concurrent),
        );
        self.subagents_sampling_limit = SubagentsConfig::resolve_sampling_limit(
            env(SubagentsConfig::ENV_SAMPLING_LIMIT).as_deref(),
            sa.sampling_limit,
            remote.and_then(|r| r.subagents_sampling_limit),
            self.subagents_max_concurrent,
        );
        self.subagents_limit_behavior = SubagentsConfig::resolve_limit_behavior(
            env(SubagentsConfig::ENV_LIMIT_BEHAVIOR).as_deref(),
            sa.limit_behavior.as_deref(),
            remote.and_then(|r| r.subagents_limit_behavior.as_deref()),
        );
        self.workflow_max_concurrent_agents = SubagentsConfig::resolve_workflow_max_concurrent(
            env(SubagentsConfig::ENV_WORKFLOW_MAX_CONCURRENT).as_deref(),
            sa.workflow_max_concurrent,
            remote.and_then(|r| r.workflow_max_concurrent_agents),
        );
    }
    /// Resolve all `#[serde(skip)]` runtime fields that have resolver functions.
    ///
    /// Call immediately after `new_from_toml_cfg()`. Fields resolved:
    /// - subagents base layers (6 fields) via `SubagentsConfig::resolve`
    /// - respect_gitignore via `ToolsConfig::resolve`
    /// - disable_zdr_incompatible_tools via `ToolsConfig::resolve`
    /// - media_gen_batch_limits via `ToolsConfig::resolve_max_parallel_*`
    /// - managed_mcps_enabled via `ManagedMcpsConfig::resolve`
    /// - web_search_model / session_summary_model / image_description_model /
    ///   prompt_suggest_model_pin via `ModelOverrideConfig::resolve`
    /// - memory_config via typed `Config::resolve_memory`
    /// - disable_web_search (CLI flag ORed with config.toml)
    /// - storage_mode via `StorageMode::resolve`
    /// - path_not_found_hints from remote_settings
    ///
    /// Note: `worktree_type` is not resolved here; it's an agent-level field,
    /// not a Config field.
    pub fn resolve_runtime_fields(&mut self, ctx: &RuntimeResolutionContext<'_>) {
        self.cli_subagents = ctx.cli_subagents;
        self.web_search_model_override = ctx.cli_web_search_model.map(|s| s.to_owned());
        self.session_summary_model_override = ctx.cli_session_summary_model.map(|s| s.to_owned());
        let cli_flag = ctx.cli_subagents.unwrap_or(false);
        self.resolve_subagents(cli_flag, ctx.raw_config);
        let env = std::env::var(crate::config::SubagentsConfig::ENV_MAX_DEPTH).ok();
        let toml_max = ctx
            .raw_config
            .get("subagents")
            .and_then(|s| s.get("max_depth"))
            .and_then(|v| v.as_integer());
        let remote = ctx.remote_settings.and_then(|r| r.subagents_max_depth);
        self.subagents_max_depth =
            crate::config::SubagentsConfig::resolve_max_depth(env.as_deref(), toml_max, remote);
        let subagents_toml = crate::config::SubagentsConfig {
            max_concurrent: ctx
                .raw_config
                .get("subagents")
                .and_then(|s| s.get("max_concurrent"))
                .and_then(|v| v.as_integer()),
            limit_behavior: ctx
                .raw_config
                .get("subagents")
                .and_then(|s| s.get("limit_behavior"))
                .and_then(|v| v.as_str())
                .map(str::to_owned),
            workflow_max_concurrent: ctx
                .raw_config
                .get("subagents")
                .and_then(|s| s.get("workflow_max_concurrent"))
                .and_then(|v| v.as_integer()),
            ..Default::default()
        };
        self.resolve_subagent_limits(&subagents_toml, ctx.remote_settings);
        let tools = crate::config::ToolsConfig::resolve(ctx.raw_config);
        self.respect_gitignore = match self.requirements.respect_gitignore.pinned() {
            Some(pinned) => pinned,
            None => tools.respect_gitignore,
        };
        self.disable_zdr_incompatible_tools = tools.disable_zdr_incompatible_tools;
        self.zdr_video_output_s3 = tools.zdr_video_output_s3;
        self.media_gen_batch_limits = pi_tools::media_gen_limits::MediaGenBatchLimits {
            max_image: crate::config::ToolsConfig::resolve_max_parallel_image_gen_calls(
                std::env::var(crate::config::ToolsConfig::ENV_MAX_PARALLEL_IMAGE_GEN_CALLS)
                    .ok()
                    .as_deref(),
                tools.media_gen.max_parallel_image_gen_calls,
                ctx.remote_settings
                    .and_then(|r| r.max_parallel_image_gen_calls),
            ),
            max_video: crate::config::ToolsConfig::resolve_max_parallel_video_gen_calls(
                std::env::var(crate::config::ToolsConfig::ENV_MAX_PARALLEL_VIDEO_GEN_CALLS)
                    .ok()
                    .as_deref(),
                tools.media_gen.max_parallel_video_gen_calls,
                ctx.remote_settings
                    .and_then(|r| r.max_parallel_video_gen_calls),
            ),
        };
        let mcps = crate::config::ManagedMcpsConfig::resolve(
            ctx.raw_config,
            ctx.remote_settings,
            ctx.is_headless,
        );
        self.managed_mcps_enabled = mcps.enabled;
        self.managed_mcp_gateway_tools_enabled = mcps.gateway_tools_enabled;
        let models = crate::config::ModelOverrideConfig::resolve(
            ctx.cli_web_search_model,
            ctx.cli_session_summary_model,
            ctx.raw_config,
            ctx.remote_settings,
        );
        self.web_search_model = models.web_search;
        self.session_summary_model = models.session_summary;
        self.image_description_model = models.image_description;
        self.prompt_suggest_model_pin = models.prompt_suggestion;
        self.memory_enabled_override = ctx.memory_enabled_override;
        let mem = self.resolve_memory(ctx.memory_enabled_override, ctx.remote_settings);
        self.memory_config = if mem.enabled { Some(mem) } else { None };
        self.disable_web_search = self.disable_web_search || ctx.disable_web_search;
        self.todo_gate = ctx.todo_gate;
        self.laziness_debug_log = ctx.laziness_debug_log.map(std::path::Path::to_path_buf);
        self.storage_mode =
            crate::config::StorageMode::resolve(ctx.storage_mode, ctx.remote_settings);
        if let Some(v) = ctx.remote_settings.and_then(|s| s.path_not_found_hints) {
            self.path_not_found_hints = v;
        }
        self.compat_resolved = resolve_compat_config(&self.compat, ctx.remote_settings);
    }
    pub(crate) fn resolve_memory(
        &self,
        memory_enabled_override: Option<bool>,
        remote: Option<&crate::util::config::RemoteSettings>,
    ) -> crate::config::MemoryConfig {
        let default_flush = crate::config::MemoryFlushSettings::default();
        let default_pruning = crate::config::PruningSettings::default();
        crate::config::MemoryConfig::resolve_settings(
            memory_enabled_override,
            &self.memory,
            self.compaction
                .memory_flush
                .as_ref()
                .unwrap_or(&default_flush),
            self.compaction.pruning.as_ref().unwrap_or(&default_pruning),
            remote,
        )
    }
    /// If the TOML contains `[auth]`, copy its contents under `[grok_com_config]`.
    /// `[grok_com_config]` takes precedence if both are present (explicit wins).
    ///
    /// This lets customers write the shorter `[auth.oidc]` instead of `[grok_com_config.oidc]`.
    fn expand_auth_alias(raw_config: &toml::Value) -> toml::Value {
        let mut config = raw_config.clone();
        if let toml::Value::Table(ref mut table) = config
            && let Some(auth) = table.remove("auth")
        {
            if let Some(gcc) = table.get_mut("grok_com_config") {
                if let (toml::Value::Table(gcc_table), toml::Value::Table(auth_table)) =
                    (gcc, &auth)
                {
                    for (k, v) in auth_table {
                        gcc_table.entry(k.clone()).or_insert(v.clone());
                    }
                }
            } else {
                table.insert("grok_com_config".to_owned(), auth);
            }
        }
        config
    }
    fn apply_env_overrides(&mut self) {
        self.telemetry.apply_env_overrides();
        if let Some(mode) = env_telemetry_mode("GROK_TELEMETRY_ENABLED") {
            self.features.telemetry = Some(mode);
        }
        self.grok_com_config.force_login_team_uuid = crate::auth::resolve_force_login_team(
            crate::auth::force_login_team_from_requirements(),
            crate::auth::force_login_team_from_env(),
            self.grok_com_config.force_login_team_uuid.take(),
        );
    }
    /// Every tier this config can speak for. A caller with a different remote
    /// snapshot overrides `remote` and leaves the rest.
    pub(crate) fn feature_sources(&self, feature: Feature) -> FeatureSources {
        FeatureSources {
            pin: self.requirements.pinned_feature(feature),
            config: self.feature_values.get(&feature).copied(),
            remote: feature.remote_value(self.remote_settings.as_ref()),
            ..FeatureSources::from_process_env(feature)
        }
    }
    pub fn feature(&self, feature: Feature) -> Resolved<bool> {
        feature.resolve(self.feature_sources(feature))
    }
}
/// Canonical resolver for `mcp.push_server_status`. Stacks the same
/// 7-step `BoolFlag` precedence as
/// [`resolve_mcp_liveness_watchers`]:
///
/// `requirement > cli > env (GROK_MCP_PUSH_SERVER_STATUS) > config >
/// managed > feature_flag > default (true)`.
///
/// `util::config::resolve_mcp_push_server_status` delegates here so
/// the precedence is single-sourced.
///
/// The default is `true` — the pager's subscription to
/// `x.ai/mcp/server_status` is wired default-on, with this
/// flag existing primarily as a kill switch.
pub(crate) fn resolve_mcp_push_server_status(
    requirement: Option<bool>,
    cli: Option<bool>,
    config: Option<bool>,
    managed: Option<bool>,
    feature_flag: Option<bool>,
) -> Resolved<bool> {
    BoolFlag::env("GROK_MCP_PUSH_SERVER_STATUS")
        .requirement(requirement)
        .cli(cli)
        .config(config)
        .managed(managed)
        .feature_flag(feature_flag)
        .default(true)
        .resolve()
}
/// Sync analogue of [`BoolFlag`] for callers that run before the tokio
/// runtime (e.g. `init_sentry`). Loads from disk + env directly rather than
/// from a pre-built `Config`.
///
/// Same convention as [`BoolFlag`]: `resolve()` returns the *enabled* value.
/// `disable_env` is sugar for "force-off if this env is truthy" and does not
/// invert the convention.
///
/// Layer precedence:
/// 1. `requirements.toml`              (admin pin)
/// 2. `managed_settings.json` env      (Claude admin pin, force-off)
/// 3. process env via `disable_env`    (force-off)
/// 4. process env via `enable_env`     (either direction)
/// 5. merged config                    (user/managed defaults)
/// 6. `inherit`, then `default`
pub(crate) struct SyncBoolFlag {
    extract_toml: fn(&toml::Value) -> Option<bool>,
    disable_env: Option<&'static str>,
    enable_env: Option<fn() -> Option<bool>>,
    inherit: Option<fn() -> bool>,
    default: bool,
}
impl SyncBoolFlag {
    pub(crate) const fn new(extract_toml: fn(&toml::Value) -> Option<bool>) -> Self {
        Self {
            extract_toml,
            disable_env: None,
            enable_env: None,
            inherit: None,
            default: false,
        }
    }
    /// Force-off env name (e.g. `"DISABLE_TELEMETRY"`). Truthy at this name
    /// in `managed_settings.json` or process env disables the flag.
    pub(crate) const fn disable_env(mut self, name: &'static str) -> Self {
        self.disable_env = Some(name);
        self
    }
    /// Either-direction env resolver (typically `GROK_*`). Returns
    /// `Some(enabled)` for an explicit signal, `None` to fall through.
    pub(crate) const fn enable_env(mut self, resolver: fn() -> Option<bool>) -> Self {
        self.enable_env = Some(resolver);
        self
    }
    /// Fallback when no source above fires.
    pub(crate) const fn inherit(mut self, resolver: fn() -> bool) -> Self {
        self.inherit = Some(resolver);
        self
    }
    pub(crate) const fn default(mut self, val: bool) -> Self {
        self.default = val;
        self
    }
    pub(crate) fn resolve(&self) -> bool {
        if let Some(enabled) = read_requirements_toml()
            .as_ref()
            .and_then(|r| (self.extract_toml)(r))
        {
            return enabled;
        }
        if let Some(name) = self.disable_env
            && managed_settings_env_flag(name) == Some(true)
        {
            return false;
        }
        if let Some(name) = self.disable_env
            && env_bool(name) == Some(true)
        {
            return false;
        }
        if let Some(resolver) = self.enable_env
            && let Some(enabled) = resolver()
        {
            return enabled;
        }
        if let Some(enabled) = crate::config::load_effective_config()
            .ok()
            .as_ref()
            .and_then(|r| (self.extract_toml)(r))
        {
            return enabled;
        }
        self.inherit.map_or(self.default, |f| f())
    }
}
/// Sync slice of [`Config::resolve_telemetry_mode`] for use before the tokio
/// runtime (e.g. `init_sentry`). `true` only when explicitly off.
pub(crate) fn is_telemetry_disabled_sync() -> bool {
    !SyncBoolFlag::new(telemetry_enabled_from_toml)
        .disable_env("DISABLE_TELEMETRY")
        .enable_env(grok_telemetry_env_enabled)
        .resolve()
}
/// Like [`is_telemetry_disabled_sync`] but only `true` when telemetry is
/// *explicitly* off; absence is not disabled (`.default(true)`) so remote-only
/// enablement still builds the OTLP exporter (the runtime gate then governs it).
pub(crate) fn is_telemetry_explicitly_disabled_sync() -> bool {
    !SyncBoolFlag::new(telemetry_enabled_from_toml)
        .disable_env("DISABLE_TELEMETRY")
        .enable_env(grok_telemetry_env_enabled)
        .default(true)
        .resolve()
}
/// Sync sibling of [`is_telemetry_disabled_sync`] scoped to Sentry. Inherits
/// from telemetry when no Sentry-specific signal is set.
pub fn is_error_reporting_disabled_sync() -> bool {
    !SyncBoolFlag::new(error_reporting_enabled_from_toml)
        .disable_env("DISABLE_ERROR_REPORTING")
        .enable_env(|| env_bool("GROK_ERROR_REPORTING"))
        .inherit(|| !is_telemetry_disabled_sync())
        .resolve()
}
/// `[features] telemetry` as enabled bool. SessionMetrics counts as enabled
/// — see ERROR_REPORTING_PLAN.md. `None` for absent or unparseable.
fn telemetry_enabled_from_toml(root: &toml::Value) -> Option<bool> {
    match root.get("features")?.as_table()?.get("telemetry")? {
        toml::Value::Boolean(b) => Some(*b),
        toml::Value::String(s) => TelemetryMode::parse(s).map(|m| !m.is_disabled()),
        _ => None,
    }
}
/// `[diagnostics] error_reporting` as enabled bool. Bool-only; no
/// `session_metrics` equivalent. `None` falls through to inheritance.
fn error_reporting_enabled_from_toml(root: &toml::Value) -> Option<bool> {
    root.get("diagnostics")?
        .as_table()?
        .get("error_reporting")?
        .as_bool()
}
/// `GROK_TELEMETRY_ENABLED` resolved through `TelemetryMode::parse` so the
/// extended string forms (e.g. `"session_metrics"`) are accepted.
fn grok_telemetry_env_enabled() -> Option<bool> {
    env_telemetry_mode("GROK_TELEMETRY_ENABLED").map(|m| !m.is_disabled())
}
/// Load `~/.grok/requirements.toml` standalone so the admin pin can beat
/// env vars. The merged config layer can't express that — last-merge-wins
/// loses provenance.
pub(crate) fn read_requirements_toml() -> Option<toml::Value> {
    let path = crate::util::grok_home::grok_home().join("requirements.toml");
    let content = std::fs::read_to_string(&path).ok()?;
    toml::from_str(&content).ok()
}
/// Resolve the external-OTEL master switch exactly the way the external
/// stream's activation does: **requirement pin > `GROK_EXTERNAL_OTEL` env >
/// `[telemetry].otel_enabled` config layer (managed config included) > off**.
///
/// The internal trace pipeline keys its "ignore `OTEL_EXPORTER_OTLP_*`"
/// behavior off this value ([`EndpointsConfig::external_otel_master_switch`]),
/// so an org enable distributed via managed config / requirements (no env
/// var) flips **both** sides together. A desync here would leave the
/// internally-authed firehose honoring legacy `OTEL_*` repointing while
/// `internal_pipeline_consumed_otel_vars` simultaneously blocks the external
/// stream — exactly the split this design forbids.
pub(crate) fn external_otel_master_switch_resolved() -> bool {
    external_otel_master_switch_from(
        pi_config::load_merged_requirements().as_ref(),
        env_bool("GROK_EXTERNAL_OTEL"),
        crate::config::load_effective_config().ok().as_ref(),
    )
}
/// Testable core of [`external_otel_master_switch_resolved`].
pub(crate) fn external_otel_master_switch_from(
    requirements: Option<&toml::Value>,
    env_switch: Option<bool>,
    effective_config: Option<&toml::Value>,
) -> bool {
    let table_enabled = |v: Option<&toml::Value>| -> Option<bool> {
        v?.get("telemetry")?.get("otel_enabled")?.as_bool()
    };
    if let Some(pinned) = table_enabled(requirements) {
        return pinned;
    }
    if let Some(env) = env_switch {
        return env;
    }
    table_enabled(effective_config).unwrap_or(false)
}
/// Resolve the external OTEL stream configuration at process startup
/// (env + local config only — remote settings are not yet available when
/// tracing init runs).
///
/// Layering follows `resolve_telemetry_mode`: **requirement > env > config >
/// remote > default**, where the `[telemetry]` `otel_*` keys from the
/// effective config (which already includes managed-config layers distributed
/// by `grok setup`) sit under the env vars, requirements pins are applied on
/// top, and the remote layer is restrictive-only + asynchronous
/// ([`apply_external_otel_remote_policy`]).
pub fn resolve_external_otel_config(
    client: pi_telemetry::external::config::ExternalClientInfo,
) -> Option<pi_telemetry::external::ExternalOtelConfig> {
    resolve_external_otel_config_with(
        crate::config::load_effective_config().ok().as_ref(),
        pi_config::load_merged_requirements().as_ref(),
        |name| std::env::var(name).ok(),
        client,
        EndpointsConfig::default().internal_otlp_consumed_standard_vars(),
    )
}
/// Testable core of [`resolve_external_otel_config`]: all inputs injected so
/// tests don't race on process env / disk.
pub(crate) fn resolve_external_otel_config_with(
    effective_config: Option<&toml::Value>,
    requirements: Option<&toml::Value>,
    getenv: impl Fn(&str) -> Option<String>,
    client: pi_telemetry::external::config::ExternalClientInfo,
    internal_pipeline_consumed_otel_vars: bool,
) -> Option<pi_telemetry::external::ExternalOtelConfig> {
    let file_cfg: Option<pi_telemetry::external::ExternalOtelFileConfig> = effective_config
        .and_then(|cfg| cfg.get("telemetry"))
        .map(|t| pi_telemetry::external::ExternalOtelFileConfig {
            enabled: t.get("otel_enabled").and_then(toml::Value::as_bool),
            metrics_exporter: t
                .get("otel_metrics_exporter")
                .and_then(toml::Value::as_str)
                .map(str::to_owned),
            logs_exporter: t
                .get("otel_logs_exporter")
                .and_then(toml::Value::as_str)
                .map(str::to_owned),
            endpoint: t
                .get("otel_endpoint")
                .and_then(toml::Value::as_str)
                .map(str::to_owned),
            protocol: t
                .get("otel_protocol")
                .or_else(|| t.get("otel_transport"))
                .and_then(toml::Value::as_str)
                .map(str::to_owned),
            certificate: t
                .get("otel_certificate")
                .and_then(toml::Value::as_str)
                .map(str::to_owned),
            client_certificate: t
                .get("otel_client_certificate")
                .and_then(toml::Value::as_str)
                .map(str::to_owned),
            client_key: t
                .get("otel_client_key")
                .and_then(toml::Value::as_str)
                .map(str::to_owned),
            log_user_prompts: t
                .get("otel_log_user_prompts")
                .and_then(toml::Value::as_bool),
            log_tool_details: t
                .get("otel_log_tool_details")
                .and_then(toml::Value::as_bool),
        });
    let req_get =
        |key: &str| -> Option<bool> { requirements?.get("telemetry")?.get(key)?.as_bool() };
    let req_enabled = req_get("otel_enabled");
    let req_prompts = req_get("otel_log_user_prompts");
    let req_details = req_get("otel_log_tool_details");
    let getenv_pinned = |name: &str| -> Option<String> {
        let pin = match name {
            pi_telemetry::external::config::ENV_MASTER_SWITCH => req_enabled,
            "OTEL_LOG_USER_PROMPTS" => req_prompts,
            "OTEL_LOG_TOOL_DETAILS" => req_details,
            _ => None,
        };
        if let Some(v) = pin {
            return Some(if v { "1" } else { "0" }.to_owned());
        }
        getenv(name)
    };
    let mut resolved = pi_telemetry::external::ExternalOtelConfig::resolve_with(
        getenv_pinned,
        file_cfg.as_ref(),
    )?;
    resolved.client = client;
    resolved.internal_pipeline_consumed_otel_vars = internal_pipeline_consumed_otel_vars;
    Some(resolved)
}
/// Read `env.<key>` from Claude-compat `managed_settings.json`. `Some(true)`
/// indicates a force-off signal from a Mac-MDM-style admin policy.
fn managed_settings_env_flag(key: &str) -> Option<bool> {
    let path = pi_config::claude_managed_settings_path()?;
    let content = std::fs::read_to_string(&path).ok()?;
    let json: serde_json::Value = serde_json::from_str(&content).ok()?;
    pi_workspace::permission::resolution::json_env_flag(json.get("env"), key)
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelEntryConfig {
    /// Stable unique identifier for this catalog entry. When present,
    /// used as the catalog map key. Falls back to `model` when absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    /// The routing slug sent in API requests.
    pub model: String,
    /// See [`ModelInfo::model_family`].
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_family: Option<String>,
    /// The base URL of the model. e.g. "https://api.x.ai/v1"
    pub base_url: String,
    /// Human-readable display name of the model.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_completion_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top_p: Option<f32>,
    /// The API key for this model's provider.
    /// If not set, falls back to env_key, then PI_API_KEY.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
    /// Environment variable name(s) that hold the provider API key.
    /// Accepts a string or an array (first set, non-empty value wins).
    /// If not set, falls back to PI_API_KEY.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub env_key: Option<EnvKeys>,
    /// Which API backend to use for this model.
    /// Values: "chat_completions" (default), "responses"
    #[serde(default)]
    pub api_backend: ApiBackend,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub auth_scheme: Option<AuthScheme>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<ReasoningEffort>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub supports_reasoning_effort: bool,
    /// Per-model reasoning-effort menu (source of truth). The two legacy fields
    /// above are derived from this list when it is non-empty.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub reasoning_efforts: Vec<ReasoningEffortOption>,
    /// Extra headers to send with requests to this model's endpoint.
    /// Useful for BYOK (Bring Your Own Key) scenarios.
    /// Example: { "x-anthropic-api-key" = "sk-ant-..." }
    #[serde(default, skip_serializing_if = "IndexMap::is_empty")]
    pub extra_headers: IndexMap<String, String>,
    /// The total context window size in tokens for this model.
    /// Used for auto-compact threshold calculations.
    /// Required — BYOK users must explicitly set this in config.toml.
    pub context_window: NonZeroU64,
    /// Per-model auto-compact threshold (0-100). When the session's token
    /// usage exceeds this percentage of `context_window`, the conversation
    /// is summarized. Resolver precedence:
    /// requirements > env > user (per-model > global) > managed (per-model > global)
    /// > remote per-model (this field) > remote global > 85.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub auto_compact_threshold_percent: Option<u8>,
    /// Per-model system-prompt identity label (not UI `name`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system_prompt_label: Option<String>,
    /// The base URL to use when authenticating with an API key (non-session auth).
    /// When set, `base_url` is used for session-based auth and `api_base_url` for API key auth.
    /// When not set, `base_url` is used for all auth methods (e.g. BYOK / third-party models).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_base_url: Option<String>,
    /// When true, this model uses concise mode (compact system prompt,
    /// concise tool output, concise user message prefix, reduced toolset).
    /// Defaults to false — when omitted or false, nothing changes.
    #[serde(default, skip_serializing_if = "is_false")]
    pub use_concise: bool,
    /// The type of system prompt to use for this model.
    /// e.g. "grok-build", "codex".
    #[serde(default = "default_agent_type")]
    pub agent_type: String,
    /// Maximum seconds to wait between SSE chunks during inference streaming.
    /// When no chunk is received within this duration, the request fails with
    /// a non-retryable `IdleTimeout` error. This is a per-chunk deadline that
    /// resets on every received chunk — NOT a total-turn timeout.
    /// Default: 300 seconds (5 minutes).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inference_idle_timeout_secs: Option<u64>,
    /// Maximum number of retries for transient API errors (429, 500, 502, etc.)
    /// during a single inference request. Default: 5.
    /// Can also be set via the `GROK_MAX_RETRIES` environment variable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_retries: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subagent_rate_limit_max_attempts: Option<u32>,
    /// Exclude from the client model picker; still usable internally (web_search, etc.).
    #[serde(default, skip_serializing_if = "is_false")]
    pub hidden: bool,
    /// When false, only OAuth users see this in the picker.
    #[serde(default = "default_true")]
    pub supported_in_api: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    pub supports_backend_search: bool,
    /// Per-model config for the `x-compactions-remaining` header; `None` disables it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compactions_remaining: Option<CompactionsRemaining>,
    /// Per-model config for the `x-compaction-at` header; `None` disables it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compaction_at_tokens: Option<CompactionAtTokens>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub show_model_fingerprint: bool,
    /// Inject `stream_tool_calls: true` into the request body
    /// so the upstream emits per-chunk `function_call_arguments.delta`
    /// Without this set, pi API models send args as one delta
    /// event, defeating the purpose of streaming.
    ///
    /// Per-model opt-in -- BYOK endpoints that don't understand the
    /// flag should leave this unset to avoid request errors.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stream_tool_calls: Option<bool>,
    /// Per-model Layer-3 LazinessDetector configuration. Defaults to
    /// the all-disabled state via `#[serde(default)]`.
    #[serde(default, skip_serializing_if = "is_default_laziness_detector")]
    pub laziness_detector: LazinessDetectorPerModelConfig,
}
/// True when `cfg` equals the all-disabled default. Derives `PartialEq`
/// on `f32`, which is fine for the current shape because both `f32`
/// fields default to `None` — there's no parsed-vs-literal `0.7` float
/// equality footgun. If a future default introduces `Some(0.7)`, this
/// helper must be reworked (e.g. compare on tolerance, or switch to a
/// bit-pattern compare) so `skip_serializing_if` doesn't start emitting
/// `[laziness_detector]` blocks for every model in `config.toml`.
fn is_default_laziness_detector(cfg: &LazinessDetectorPerModelConfig) -> bool {
    cfg == &LazinessDetectorPerModelConfig::default()
}
/// A `[model.foo]` entry from config.toml, parsed directly from raw TOML
/// (bypassing deep merge). Scalar fields are `Option` so absent means "inherit
/// from defaults/prefetched"; the collection fields (`extra_headers`,
/// `reasoning_efforts`) merge only when non-empty and so cannot express
/// "override to empty."
#[derive(Clone, Debug, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct ConfigModelOverride {
    pub model: Option<String>,
    pub model_family: Option<String>,
    pub base_url: Option<String>,
    pub name: Option<String>,
    pub description: Option<String>,
    pub api_key: Option<String>,
    /// Env var name(s) for the provider key — string or array in config.toml.
    pub env_key: Option<EnvKeys>,
    /// Name of a `[auth_provider.<name>]` credential helper that mints
    /// this model's bearer token. Static `api_key` / `env_key` win when both
    /// are set.
    pub auth_provider: Option<String>,
    pub model_provider: Option<String>,
    pub api_base_url: Option<String>,
    pub max_completion_tokens: Option<u32>,
    pub temperature: Option<f32>,
    pub top_p: Option<f32>,
    pub api_backend: Option<ApiBackend>,
    #[serde(default)]
    pub extra_headers: IndexMap<String, String>,
    #[serde(default)]
    pub query_params: IndexMap<String, String>,
    #[serde(default)]
    pub env_http_headers: IndexMap<String, String>,
    pub context_window: Option<u64>,
    /// Per-model auto-compact threshold override (0-100) from `[model.<id>]`.
    /// Read directly by `resolve_auto_compact_threshold_percent`; intentionally
    /// NOT merged into `ModelInfo.auto_compact_threshold_percent` so the
    /// resolver can keep user-per-model distinct from GB-per-model.
    pub auto_compact_threshold_percent: Option<u8>,
    /// Per-model system-prompt identity; not merged into `ModelInfo` (tiered resolve).
    pub system_prompt_label: Option<String>,
    pub use_concise: Option<bool>,
    pub agent_type: Option<String>,
    pub inference_idle_timeout_secs: Option<u64>,
    pub max_retries: Option<u32>,
    pub subagent_rate_limit_max_attempts: Option<u32>,
    pub hidden: Option<bool>,
    pub supported_in_api: Option<bool>,
    pub reasoning_effort: Option<ReasoningEffort>,
    pub supports_reasoning_effort: Option<bool>,
    pub reasoning_efforts: Vec<ReasoningEffortOption>,
    pub supports_backend_search: Option<bool>,
    /// Aliases must be registered in `config_model_override_parse::ALIASES`;
    /// serde rejects a table that contains both spellings otherwise.
    #[serde(alias = "send_compactions_remaining")]
    pub compactions_remaining: Option<CompactionsRemaining>,
    pub compaction_at_tokens: Option<CompactionAtTokens>,
    pub show_model_fingerprint: Option<bool>,
    pub stream_tool_calls: Option<bool>,
}
/// Shared model metadata — the common fields across all model sources.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ModelInfo {
    /// Stable unique identifier for this catalog entry.
    /// Falls back to `model` when absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    /// The routing slug sent in API requests.
    pub model: String,
    /// Provider family that mints this model's conversation items
    /// (e.g. "pi"); `None` = unknown.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_family: Option<String>,
    /// The base URL of the model (session endpoint). e.g. "https://cli-chat-proxy.grok.com/v1"
    pub base_url: String,
    /// Human-readable name of the model. Honored by both the picker
    /// (`/model`) and `/session-info` -- when set, that's the label shown
    /// to users in either consumer.
    pub name: Option<String>,
    pub description: Option<String>,
    pub max_completion_tokens: Option<u32>,
    pub temperature: Option<f32>,
    pub top_p: Option<f32>,
    pub api_backend: ApiBackend,
    pub auth_scheme: AuthScheme,
    pub extra_headers: IndexMap<String, String>,
    #[serde(default, skip_serializing_if = "IndexMap::is_empty")]
    pub query_params: IndexMap<String, String>,
    #[serde(default, skip_serializing_if = "IndexMap::is_empty")]
    pub env_http_headers: IndexMap<String, String>,
    pub context_window: NonZeroU64,
    /// Per-model auto-compact threshold (0-100). `None` defers to the
    /// global / default tiers in `resolve_auto_compact_threshold_percent`.
    pub auto_compact_threshold_percent: Option<u8>,
    /// Per-model system-prompt identity (not UI picker `name`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system_prompt_label: Option<String>,
    /// When true, this model uses concise mode (compact system prompt,
    /// concise tool output, concise user message prefix, reduced toolset).
    pub use_concise: bool,
    /// The type of agent configuration to use for this model.
    /// Always has a value; defaults to `"grok-build-plan"` when the server
    /// or user config doesn't specify one.
    #[serde(default = "default_agent_type")]
    pub agent_type: String,
    /// Per-chunk idle timeout for inference streaming (see `ModelEntryConfig`).
    pub inference_idle_timeout_secs: Option<u64>,
    pub max_retries: Option<u32>,
    pub subagent_rate_limit_max_attempts: Option<u32>,
    /// Never show in picker (any auth). See also `supported_in_api`.
    pub hidden: bool,
    /// May the user select this model for normal chat? Derived from
    /// `allowed_models` in `resolve_model_catalog`; never persisted.
    #[serde(skip_serializing, default = "default_true")]
    pub user_selectable: bool,
    /// When false, only OAuth users see this in the picker.
    #[serde(default = "default_true")]
    pub supported_in_api: bool,
    pub reasoning_effort: Option<ReasoningEffort>,
    /// When true, the UI shows effort controls for this model.
    pub supports_reasoning_effort: bool,
    /// Per-model reasoning-effort menu (source of truth); legacy fields derived from it.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub reasoning_efforts: Vec<ReasoningEffortOption>,
    pub supports_backend_search: bool,
    /// Per-model config for the `x-compactions-remaining` header; `None` disables it.
    pub compactions_remaining: Option<CompactionsRemaining>,
    /// Per-model config for the `x-compaction-at` header; `None` disables it.
    pub compaction_at_tokens: Option<CompactionAtTokens>,
    pub show_model_fingerprint: bool,
    /// When `Some(true)`, the sampler injects `stream_tool_calls: true`
    pub stream_tool_calls: Option<bool>,
    /// Per-model Layer-3 LazinessDetector configuration. Defaults to
    /// the all-disabled state — the feature is per-model opt-in with a
    /// second-step `max_nudges_per_session > 0` opt-in for actually
    /// injecting nudges. See [`LazinessDetectorPerModelConfig`].
    #[serde(default)]
    pub laziness_detector: LazinessDetectorPerModelConfig,
}
impl ModelInfo {
    /// Extract shared model metadata from a flat config entry.
    pub(crate) fn from_config(entry: &ModelEntryConfig) -> Self {
        ModelInfo {
            user_selectable: true,
            id: entry.id.clone(),
            model: entry.model.clone(),
            model_family: entry.model_family.clone(),
            base_url: entry.base_url.clone(),
            name: entry.name.clone(),
            description: entry.description.clone(),
            max_completion_tokens: entry.max_completion_tokens,
            temperature: entry.temperature,
            top_p: entry.top_p,
            api_backend: entry.api_backend.clone(),
            auth_scheme: entry.auth_scheme.unwrap_or_default(),
            extra_headers: entry.extra_headers.clone(),
            query_params: IndexMap::new(),
            env_http_headers: IndexMap::new(),
            context_window: entry.context_window,
            auto_compact_threshold_percent: entry.auto_compact_threshold_percent,
            system_prompt_label: entry.system_prompt_label.clone(),
            use_concise: entry.use_concise,
            agent_type: entry.agent_type.clone(),
            inference_idle_timeout_secs: entry.inference_idle_timeout_secs,
            max_retries: entry.max_retries,
            subagent_rate_limit_max_attempts: entry.subagent_rate_limit_max_attempts,
            hidden: entry.hidden,
            supported_in_api: entry.supported_in_api,
            reasoning_effort: entry.reasoning_effort,
            supports_reasoning_effort: entry.supports_reasoning_effort,
            reasoning_efforts: entry.reasoning_efforts.clone(),
            supports_backend_search: entry.supports_backend_search,
            compactions_remaining: entry.compactions_remaining,
            compaction_at_tokens: entry.compaction_at_tokens,
            show_model_fingerprint: entry.show_model_fingerprint,
            stream_tool_calls: entry.stream_tool_calls,
            laziness_detector: entry.laziness_detector.clone(),
        }
    }
}
/// Flat struct so credential and endpoint fields coexist after deep-merge.
/// Routing reads fields, not provenance.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ModelEntry {
    pub info: ModelInfo,
    pub api_key: Option<String>,
    pub env_key: Option<EnvKeys>,
    /// Named credential helper (`[model.<id>] auth_provider = "<name>"`),
    /// resolved against `[auth_provider.<name>]` by `resolve_model_list`.
    /// Config-file models only: the built-in catalog never carries one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub auth_provider: Option<crate::auth::AuthProviderRef>,
    /// When set, `base_url` is used for session auth, `api_base_url` for API-key auth.
    pub api_base_url: Option<String>,
}
impl std::ops::Deref for ModelEntry {
    type Target = ModelInfo;
    fn deref(&self) -> &ModelInfo {
        &self.info
    }
}
fn is_false(v: &bool) -> bool {
    !v
}
fn default_true() -> bool {
    true
}
/// Codebase indexing setting for `[features] codebase_indexing`.
///
/// Patterns are matched against the git root when available, otherwise the cwd,
/// which allows explicitly indexing non-git directories.
///
/// ```toml
/// codebase_indexing = false                                          # disable
/// codebase_indexing = true                                           # any git repo (default)
/// codebase_indexing = ["/Users/*/pi*", "!/Users/*/old-*"]           # globs, ! to exclude
/// ```
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CodebaseIndexingSetting {
    Enabled(bool),
    Patterns(Vec<String>),
}
impl Default for CodebaseIndexingSetting {
    fn default() -> Self {
        Self::Enabled(true)
    }
}
/// Optional role pair that drops a malformed value to `None` (with a warn)
/// instead of failing the whole config parse — one typo must not wipe the
/// config. Mirrors the remote tolerance in `util::config::remote`.
fn de_tolerant_goal_role_model<'de, D>(
    deserializer: D,
) -> Result<Option<crate::util::config::GoalRoleModel>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = Option::<toml::Value>::deserialize(deserializer)?;
    Ok(value.and_then(|v| {
        v.try_into()
            .map_err(|e| tracing::warn!(error = %e, "[goal] role model: dropped malformed value"))
            .ok()
    }))
}
/// Skeptic pool variant of [`de_tolerant_goal_role_model`]: a non-array yields
/// an empty pool; malformed entries are dropped, survivor order preserved (the
/// skeptic round-robin depends on it).
fn de_tolerant_goal_role_models<'de, D>(
    deserializer: D,
) -> Result<Vec<crate::util::config::GoalRoleModel>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = Option::<toml::Value>::deserialize(deserializer)?;
    Ok(match value {
        Some(toml::Value::Array(arr)) => arr
            .into_iter()
            .filter_map(|v| {
                v.try_into()
                    .map_err(|e| {
                        tracing::warn!(error = %e, "[goal] skeptic model: dropped malformed entry");
                    })
                    .ok()
            })
            .collect(),
        _ => Vec::new(),
    })
}
/// `[goal]` section: the canonical home for `/goal` configuration. Field names
/// mirror the remote `goal_*` keys with the prefix dropped, so config and remote
/// stay 1:1. Per-key precedence is env > this config > remote > default.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct GoalConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub classifier_enabled: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub planner_enabled: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub summary_enabled: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub use_current_model_only: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub verifier_count: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub classifier_max_runs: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub strategist_every: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reverify_after: Option<u32>,
    #[serde(
        default,
        deserialize_with = "de_tolerant_goal_role_model",
        skip_serializing_if = "Option::is_none"
    )]
    pub planner_model: Option<crate::util::config::GoalRoleModel>,
    #[serde(
        default,
        deserialize_with = "de_tolerant_goal_role_model",
        skip_serializing_if = "Option::is_none"
    )]
    pub strategist_model: Option<crate::util::config::GoalRoleModel>,
    #[serde(
        default,
        deserialize_with = "de_tolerant_goal_role_models",
        skip_serializing_if = "Vec::is_empty"
    )]
    pub skeptic_models: Vec<crate::util::config::GoalRoleModel>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct WorkflowsConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
}
/// `[auto_mode]` section: server-side configuration for Auto permission mode.
/// ONE struct serves both the local `[auto_mode]` TOML table and the remote
/// remote settings `auto_mode` JSON object (coerced via `serde_json::from_value`), so
/// the two stay 1:1. All fields are plain scalars/enums, so they deserialize
/// cleanly from both formats (no custom tolerant deser needed). Unset fields stay
/// `None` here; the wire fn applies the built-in defaults once auto mode is
/// enabled (current model, `low` effort if the model supports it, `just_command`
/// prompt). Precedence: local config > remote > those built-in defaults.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct AutoModeConfig {
    /// The Auto-mode gate. Lowest-precedence layer of the gate chain (env and
    /// local `[auto_mode] enabled` config win over this remote value).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
    /// How much context the classifier prompt includes. `None` ⇒ the wire fn's
    /// built-in default (`just_command`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompt_type: Option<pi_workspace::permission::ClassifierPromptType>,
    /// Routing slug for a dedicated classifier model. `None` ⇒ inherit the
    /// session model. Resolved via `resolve_aux_model_sampling_config`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub classifier_model: Option<String>,
    /// Classifier side-query duration in milliseconds; resolved with bounded defaults.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub classify_timeout_ms: Option<u64>,
    /// Classifier reasoning effort. Applies on BOTH the routed-model path and the
    /// inherited session-model path; `None` ⇒ the wire fn's built-in default
    /// (`low` if the effective model supports reasoning effort, else unset).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<ReasoningEffort>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Features {
    /// when set, the agent may ask permission for tool executions
    #[serde(default)]
    pub support_permission: bool,
    /// `None` = defer to remote settings / default (off).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub telemetry: Option<TelemetryMode>,
    /// Codebase graph indexing for go-to-definition/references.
    /// Accepts: true | false | ["glob", "!negative-glob", ...]
    /// Default: true (index any git repo). Patterns can explicitly match non-git directories.
    #[serde(default)]
    pub codebase_indexing: CodebaseIndexingSetting,
    /// Show a blocking warning when Grok starts outside a Git repository.
    /// Default: false. Used as the local fallback when the `non_git_warning` remote settings
    /// flag in `grok_build_settings` is absent. When the remote flag is present it takes
    /// precedence — `Some(false)` from remote settings overrides `true` here.
    #[serde(default)]
    pub non_git_warning: bool,
    /// Managed config fetching (managed_config.toml + requirements.toml).
    /// `None` = defer to env / default (true).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub managed_config: Option<bool>,
    /// Early-session auto-title refresh. `None` = defer to `resolve_title_refresh`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title_refresh: Option<bool>,
    /// `image_gen` / `/imagine`. `None` = env / remote / default (`true`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image_gen: Option<bool>,
    /// Video tools / `/imagine-video`. `None` = env / remote / default (`true`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video_gen: Option<bool>,
    /// `image_gen` Imagine model override. `None`/empty = defer to remote settings
    /// (`image_gen_model_override`) / env / default (`grok-imagine-image-quality`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image_gen_model_override: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image_edit_model_override: Option<String>,
    /// `summary` (default) | `transcript` | `segments`. `None` = defer to CLI /
    /// env (`GROK_COMPACTION_MODE`). Parsed via `CompactionMode::parse`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compaction_mode: Option<String>,
    /// `none` | `minimal` | `balanced` | `verbose` (default). `None` = defer to
    /// env (`GROK_COMPACTION_DETAIL`). The `segments` verbatim detail level.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compaction_detail: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compaction_tool_choice: Option<String>,
    /// Per-`Ready`-client transport-liveness pollers + the
    /// session-actor `StatusDispatcher`.
    ///
    /// When `true` (default), each successfully-handshaken MCP
    /// client gets a poller that detects rmcp service-loop
    /// termination and pushes `x.ai/mcp/server_status` updates to
    /// the client. When `false`, neither watchers nor the
    /// dispatcher are spawned — useful as an emergency kill switch
    /// for the rollout. `None` = defer to env / default (true).
    ///
    /// Not read through this struct: the live resolver re-reads the
    /// `[features]` key out-of-band from raw TOML in
    /// `util::config::resolve::mcp`. Declared so `serde_ignored`
    /// does not report it as an unrecognized key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_liveness_watchers: Option<bool>,
    /// Bounded stdio auto-restart task.
    ///
    /// When `true`, the session-actor `StatusDispatcher` reacts to
    /// `TransportClosed` / `HandshakeFailed` events on stdio MCP
    /// servers by scheduling up to 3 respawn attempts with
    /// `[1s, 4s, 16s]` backoff. HTTP / HttpAuth servers are NOT
    /// auto-restarted (their existing `reset_transport` path
    /// covers the recovery). `None` = defer to env / default
    /// (recovery is on by default; set `false` here / via
    /// `GROK_MCP_AUTO_RESTART` to opt out).
    ///
    /// Not read through this struct: the live resolver re-reads the
    /// `[features]` key out-of-band from raw TOML in
    /// `util::config::resolve::mcp`. Declared so `serde_ignored`
    /// does not report it as an unrecognized key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_auto_restart: Option<bool>,
    /// Pager-side subscription to the `x.ai/mcp/server_status` push.
    ///
    /// When `true` (default), the pager subscribes to the per-server
    /// status delta the shell emits via the dispatcher and
    /// patches the MCP servers modal in-place (no re-fetch round
    /// trip). When `false`, the pager ignores the push and falls
    /// back to the legacy `x.ai/mcp/tools_changed` debounced refetch
    /// path. `None` = defer to env / default (true).
    ///
    /// Not read through this struct. The pager-side gate
    /// (`acp_handler::push_server_status_enabled`) uses an
    /// **env-only** OnceLock cache via
    /// [`crate::util::config::resolve_mcp_push_server_status(None, None, None)`],
    /// which consults `BoolFlag::env` and the default `true`. The
    /// `[features]` key itself is honoured out-of-band, re-read from
    /// raw TOML in `util::config::resolve::mcp`. This field is
    /// declared so `serde_ignored` does not report the key as
    /// unrecognized.
    ///
    /// Practical consequence: setting
    /// `[features] mcp_push_server_status = false` in
    /// `~/.grok/config.toml` will NOT disable the pager's
    /// subscription on a freshly-launched process. To disable the
    /// pager subscription, set `GROK_MCP_PUSH_SERVER_STATUS=0` in
    /// the env before launch.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_push_server_status: Option<bool>,
    /// Whether the leader's `ConfigFileWatcher` adds the two narrow
    /// non-recursive watches for `<cwd>/` and `<cwd>/.grok/`.
    ///
    /// When `true` (default), edits to `<cwd>/.mcp.json`,
    /// `<cwd>/.grok/config.toml`, or `<cwd>/.claude.json` flow
    /// through the watcher → reloader → `ConfigUpdate::
    /// ProjectMcpServersChanged { cwd }` → `app.rs` ACP-injection
    /// pipeline and the affected sessions reload their MCP servers
    /// within the debounce window (~ 1 s). When `false`, the leader
    /// skips the cwd watches entirely and the only way to pick up a
    /// project-config edit is the user-triggered refresh button.
    ///
    /// The watches are **always non-recursive** — the name follows
    /// the convention for the rollout-gate flag. See
    /// `crate::config::watcher::ConfigFileWatcher::watch_path` for
    /// the inotify-quota rationale.
    ///
    /// The name is a documented misnomer — it gates
    /// the existence of the **cwd** watches, NOT their recursion
    /// mode. A future rename to `mcp_cwd_config_watch` would align
    /// name and behavior; deferred to a follow-up to avoid widening
    /// the config surface across requirements.toml / managed configs.
    ///
    /// Not read through this struct: the live resolver re-reads the
    /// `[features]` key out-of-band from raw TOML in
    /// `util::config::resolve::mcp`. Declared so `serde_ignored`
    /// does not report it as an unrecognized key.
    /// `None` = defer to env / default (true).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp_recursive_config_watch: Option<bool>,
    /// Every remaining `[features]` key, typed. Private: a registry key must be
    /// read through [`Config::feature`], which resolves the other tiers too.
    #[serde(flatten)]
    entries: FeatureEntries,
}
/// The `[features]` entries no field claims: the registry rows, the keys the
/// raw-layer resolvers read, and whatever a typo or a later release leaves
/// behind. Typing them here is what checks a key before anyone thinks to list
/// it, which is the whole point: a quoted `remote_fetch` once read as absent
/// and left an egress gate open.
///
/// Deserialized by hand for two reasons serde cannot cover: to name the key,
/// which its message for a bad map value omits, and to fail only on a value
/// that reads as a boolean, so a key holding a later release's typed value is
/// ignored rather than fatal to a build that predates the field.
#[derive(Clone, Debug, Default)]
struct FeatureEntries {
    flags: BTreeMap<String, bool>,
    /// Keys holding neither a boolean nor a mistaken one, kept so the operator
    /// still hears about them.
    ignored: std::collections::BTreeSet<String>,
}
impl Serialize for FeatureEntries {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.flags.serialize(serializer)
    }
}
impl<'de> Deserialize<'de> for FeatureEntries {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct EntriesVisitor;
        impl<'de> serde::de::Visitor<'de> for EntriesVisitor {
            type Value = FeatureEntries;
            fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str("a table of `[features]` booleans")
            }
            fn visit_map<M: serde::de::MapAccess<'de>>(
                self,
                mut map: M,
            ) -> Result<Self::Value, M::Error> {
                let mut flags = BTreeMap::new();
                let mut ignored = std::collections::BTreeSet::new();
                while let Some(key) = map.next_key::<String>()? {
                    let value: toml::Value = map.next_value()?;
                    match value.as_bool() {
                        Some(flag) => {
                            flags.insert(key, flag);
                        }
                        None if reads_as_a_boolean(&value) => {
                            return Err(serde::de::Error::custom(non_boolean_feature_error(
                                &format!("features.{key}"),
                                &value,
                            )));
                        }
                        None => {
                            ignored.insert(key);
                        }
                    }
                }
                Ok(FeatureEntries { flags, ignored })
            }
        }
        deserializer.deserialize_map(EntriesVisitor)
    }
}
pub(crate) use pi_telemetry::config::deployment_id_from_key;
/// Error code for model switch rejection due to agent type mismatch.
pub(crate) const MODEL_SWITCH_INCOMPATIBLE_AGENT: &str = "MODEL_SWITCH_INCOMPATIBLE_AGENT";
/// Structured error payload for model switch rejection due to agent type
/// incompatibility. Serialized into `acp::Error.data` by the shell and
/// deserialized by the TUI for user-friendly error rendering.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ModelSwitchIncompatibleAgentError {
    /// Stable machine-readable error code (always `MODEL_SWITCH_INCOMPATIBLE_AGENT`).
    pub code: String,
    /// The agent type currently active in the session.
    pub active_agent_type: String,
    /// The agent type required by the target model.
    pub required_agent_type: String,
    /// The model ID that was requested.
    pub model_id: String,
    /// Remediation hint for the client.
    pub suggestion: String,
}
impl ModelSwitchIncompatibleAgentError {
    /// Try to parse from an `acp::Error.data` field.
    pub fn from_acp_error(err: &acp::Error) -> Option<Self> {
        let data = err.data.as_ref()?;
        let code = data.get("code")?.as_str()?;
        if code != MODEL_SWITCH_INCOMPATIBLE_AGENT {
            return None;
        }
        serde_json::from_value(data.clone()).ok()
    }
}
#[cfg(test)]
#[path = "config_tests.rs"]
mod tests;
