pub(crate) mod reloader;
use crate::bundle;
pub use pi_config_types::{
    DEFAULT_RECENCY_DECAY, MemoryConfig, MemoryDreamConfig, MemoryDreamSettings,
    MemoryEmbeddingConfig, MemoryEmbeddingSettings, MemoryFlushConfig, MemoryFlushSettings,
    MemoryGcConfig, MemoryGcSettings, MemoryIndexConfig, MemoryIndexSettings,
    MemoryInitialInjectionConfig, MemoryInitialInjectionSettings, MemorySearchConfig,
    MemorySearchSettings, MemorySessionConfig, MemorySessionSettings, MemorySettings,
    MemoryWatcherConfig, MemoryWatcherSettings, MmrConfig, MmrSettings, PruningConfig,
    PruningSettings, TemporalDecayConfig, TemporalDecaySettings,
};
use serde::Deserialize;
/// Configuration for subagent (task tool) support.
///
/// Parsed from the `[subagents]` section of `~/.grok/config.toml` or
/// `.grok/config.toml`. Enabled by default; can be disabled via
/// `GROK_SUBAGENTS=0` env var or `[subagents] enabled = false`
/// in config.toml.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct SubagentsConfig {
    /// Whether subagent support is enabled.
    pub enabled: bool,
    /// Raw `[subagents] max_depth` (i64 so out-of-range parses; clamped ≥1 at resolve).
    #[serde(default)]
    pub max_depth: Option<i64>,
    #[serde(default)]
    pub max_concurrent: Option<i64>,
    /// Concurrent subagent turn-sampling limit. See [`Self::resolve_sampling_limit`].
    #[serde(default)]
    pub sampling_limit: Option<i64>,
    /// `"queue"` or `"fail"`.
    #[serde(default)]
    pub limit_behavior: Option<String>,
    #[serde(default)]
    pub workflow_max_concurrent: Option<i64>,
    /// Per-subagent model ID overrides.
    /// Keys are agent names, values are model IDs that must exist in the
    /// available models registry. Parsed from `[subagents.models]` in config.toml.
    ///
    /// ```toml
    /// [subagents.models]
    /// explore = "grok-3-fast"
    /// plan = "grok-3"
    /// ```
    #[serde(default)]
    pub models: std::collections::HashMap<String, String>,
    /// Per-subagent enable/disable toggles.
    /// Keys are agent names, values are booleans.
    /// Omitted agents default to enabled (`true`).
    ///
    /// ```toml
    /// [subagents.toggle]
    /// explore = true
    /// plan = false
    /// ```
    #[serde(default)]
    pub toggle: std::collections::HashMap<String, bool>,
    /// Declarative subagent role definitions.
    ///
    /// ```toml
    /// [subagents.roles.researcher]
    /// description = "Deep research agent"
    /// default_capability_mode = "read-only"
    /// model = "grok-3"
    ///
    /// [subagents.roles.implementer]
    /// description = "Implementation agent with full access"
    /// default_capability_mode = "all"
    /// prompt_file = ".grok/prompts/implementer.md"
    /// ```
    #[serde(default)]
    pub roles: std::collections::HashMap<String, SubagentRole>,
    /// Named persona/SOUL definitions.
    ///
    /// ```toml
    /// [subagents.personas.researcher]
    /// instructions = "You are a thorough researcher. Always cite sources."
    ///
    /// [subagents.personas.concise]
    /// instructions = "Be extremely concise. No filler words."
    /// instructions_file = ".grok/personas/concise.md"
    /// ```
    #[serde(default)]
    pub personas: std::collections::HashMap<String, SubagentPersona>,
}
use pi_subagent_resolution::config::{SubagentPersona, SubagentRole};
impl SubagentsConfig {
    fn discover_personas_in_dir(&mut self, dir: &std::path::Path) {
        if !dir.is_dir() {
            return;
        }
        let entries = match std::fs::read_dir(dir) {
            Ok(e) => e,
            Err(e) => {
                tracing::debug!(error = %e, "Failed to read personas directory");
                return;
            }
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("toml") {
                continue;
            }
            let Some(name) = path.file_stem().and_then(|s| s.to_str()).map(String::from) else {
                continue;
            };
            if self.personas.contains_key(&name) {
                continue;
            }
            match std::fs::read_to_string(&path) {
                Ok(content) => match toml::from_str::<SubagentPersona>(&content) {
                    Ok(mut persona) => {
                        persona.source_dir = path.parent().map(|p| p.to_path_buf());
                        persona.source_path = Some(path.display().to_string());
                        tracing::debug!(persona = %name, "Loaded persona from file");
                        self.personas.insert(name, persona);
                    }
                    Err(e) => {
                        tracing::warn!(persona = %name, error = %e, "Failed to parse persona file");
                    }
                },
                Err(e) => {
                    tracing::warn!(error = %e, "Failed to read persona file");
                }
            }
        }
    }
    fn discover_roles_in_dir(&mut self, dir: &std::path::Path) {
        if !dir.is_dir() {
            return;
        }
        let entries = match std::fs::read_dir(dir) {
            Ok(e) => e,
            Err(e) => {
                tracing::debug!(error = %e, "Failed to read roles directory");
                return;
            }
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("toml") {
                continue;
            }
            let Some(name) = path.file_stem().and_then(|s| s.to_str()).map(String::from) else {
                continue;
            };
            if self.roles.contains_key(&name) {
                tracing::debug!(role = %name, "Skipping file-based role, higher-priority config takes precedence");
                continue;
            }
            match std::fs::read_to_string(&path) {
                Ok(content) => match toml::from_str::<SubagentRole>(&content) {
                    Ok(mut role) => {
                        role.source_dir = path.parent().map(|p| p.to_path_buf());
                        tracing::debug!(role = %name, "Loaded role from file");
                        self.roles.insert(name, role);
                    }
                    Err(e) => {
                        tracing::warn!(
                            role = %name,
                            path = %path.display(),
                            error = %e,
                            "Failed to parse role file"
                        );
                    }
                },
                Err(e) => {
                    tracing::warn!(
                        path = %path.display(),
                        error = %e,
                        "Failed to read role file"
                    );
                }
            }
        }
    }
    pub(crate) const ENV_MAX_DEPTH: &'static str = "GROK_SUBAGENTS_MAX_DEPTH";
    pub(crate) const DEFAULT_MAX_DEPTH: u32 = 1;
    /// Clamp to `1..=u32::MAX`. Values below 1 (including 0 / negatives) warn
    /// and become 1 so nesting is never accidentally disabled.
    pub(crate) fn clamp_max_depth(raw: i64, source: &str) -> u32 {
        if raw < i64::from(Self::DEFAULT_MAX_DEPTH) {
            tracing::warn!(
                source,
                value = raw,
                "subagents max_depth < 1; clamping to 1"
            );
            Self::DEFAULT_MAX_DEPTH
        } else if raw > i64::from(u32::MAX) {
            tracing::warn!(
                source,
                value = raw,
                "subagents max_depth exceeds u32::MAX; clamping"
            );
            u32::MAX
        } else {
            raw as u32
        }
    }
    /// Precedence: env > TOML > remote > [`Self::DEFAULT_MAX_DEPTH`].
    ///
    /// Depth 0 is the top-level session; a child is parent+1. Spawn is rejected
    /// when `depth >= max`. So `max = 1` allows only top-level spawns; nested
    /// spawns from a first-level subagent need `max >= 2`.
    pub(crate) fn resolve_max_depth(
        env: Option<&str>,
        config: Option<i64>,
        remote: Option<u32>,
    ) -> u32 {
        if let Some(raw) = env {
            match raw.trim().parse::<i64>() {
                Ok(v) => return Self::clamp_max_depth(v, "env"),
                Err(_) => {
                    tracing::warn!(
                        value = %raw,
                        "invalid GROK_SUBAGENTS_MAX_DEPTH (expected integer); ignoring"
                    );
                }
            }
        }
        if let Some(v) = config {
            return Self::clamp_max_depth(v, "config");
        }
        if let Some(v) = remote {
            return Self::clamp_max_depth(i64::from(v), "remote");
        }
        Self::DEFAULT_MAX_DEPTH
    }
    pub(crate) const ENV_MAX_CONCURRENT: &'static str = "GROK_MAX_CONCURRENT_SUBAGENTS";
    pub(crate) const ENV_SAMPLING_LIMIT: &'static str = "GROK_SUBAGENT_SAMPLING_LIMIT";
    pub(crate) const ENV_LIMIT_BEHAVIOR: &'static str = "GROK_SUBAGENT_LIMIT_BEHAVIOR";
    pub(crate) const ENV_WORKFLOW_MAX_CONCURRENT: &'static str =
        "GROK_WORKFLOW_MAX_CONCURRENT_AGENTS";
    pub(crate) fn resolve_max_concurrent(
        env: Option<&str>,
        config: Option<i64>,
        remote: Option<u32>,
    ) -> usize {
        resolve_positive_count(
            Self::ENV_MAX_CONCURRENT,
            env,
            config,
            remote,
            pi_tools::implementations::grok_build::task::admission::DEFAULT_MAX_CONCURRENT,
        )
    }
    /// Resolve the subagent turn-sampling limit, clamped to
    /// [`crate::agent::subagent::MAX_SUBAGENT_SAMPLING_LIMIT`]. `default` is the
    /// resolved concurrent-subagent bound (`GROK_MAX_CONCURRENT_SUBAGENTS`); a
    /// lower `GROK_SUBAGENT_SAMPLING_LIMIT`, `[subagents] sampling_limit`, or
    /// remote value caps sampling further.
    pub(crate) fn resolve_sampling_limit(
        env: Option<&str>,
        config: Option<i64>,
        remote: Option<u32>,
        default: usize,
    ) -> usize {
        /// Hard ceiling for the concurrent subagent sampling limit.
        const MAX_SUBAGENT_SAMPLING_LIMIT: usize = 512;
        let max = MAX_SUBAGENT_SAMPLING_LIMIT;
        let resolved =
            resolve_positive_count(Self::ENV_SAMPLING_LIMIT, env, config, remote, default);
        if resolved > max {
            tracing::warn!(
                name = Self::ENV_SAMPLING_LIMIT,
                resolved,
                max,
                "subagent sampling limit exceeds the ceiling; clamping"
            );
        }
        resolved.min(max)
    }
    pub(crate) fn resolve_workflow_max_concurrent(
        env: Option<&str>,
        config: Option<i64>,
        remote: Option<u32>,
    ) -> usize {
        resolve_positive_count(
            Self::ENV_WORKFLOW_MAX_CONCURRENT,
            env,
            config,
            remote,
            crate::session::workflow::host_service::DEFAULT_WORKFLOW_MAX_CONCURRENT_AGENTS,
        )
    }
    pub(crate) fn resolve_limit_behavior(
        env: Option<&str>,
        config: Option<&str>,
        remote: Option<&str>,
    ) -> pi_tools::implementations::grok_build::task::admission::LimitBehavior {
        use pi_tools::implementations::grok_build::task::admission::LimitBehavior;
        for (source, value) in [("env", env), ("config", config), ("remote", remote)] {
            let Some(value) = value else { continue };
            if value.eq_ignore_ascii_case("fail") {
                return LimitBehavior::Fail;
            }
            if value.eq_ignore_ascii_case("queue") {
                return LimitBehavior::Queue;
            }
            tracing::warn!(
                source,
                %value,
                "subagent limit_behavior is neither `queue` nor `fail`; ignoring"
            );
        }
        LimitBehavior::Queue
    }
    /// Resolve the final subagents config from all sources (in priority order):
    /// 1. CLI flag `--subagents` (absolute highest — always enables)
    /// 2. `GROK_SUBAGENTS` env var: `1`/`true` enables, `0`/`false` force-disables
    /// 3. Config file `[subagents]` section
    /// 4. Default (enabled)
    ///
    /// `enabled` is deliberately not remotely gated — only explicit local
    /// intent (CLI flag, `GROK_SUBAGENTS`, `[subagents] enabled`) changes
    /// the default.
    ///
    /// Project files are excluded from this trust-independent base; Task
    /// boundaries overlay them using the parent cwd's authoritative trust verdict.
    pub(crate) fn resolve(cli_flag: bool, config: &toml::Value) -> Self {
        let user_grok_root = pi_config::user_grok_home();
        Self::resolve_base_with_sources(
            cli_flag,
            config,
            user_grok_root.as_deref(),
            &bundle::bundled_root(),
        )
    }
    pub(crate) fn resolve_base_with_sources(
        cli_flag: bool,
        config: &toml::Value,
        user_grok_root: Option<&std::path::Path>,
        bundled_root: &std::path::Path,
    ) -> Self {
        let mut result: Self = config
            .get("subagents")
            .and_then(|v| v.clone().try_into().ok())
            .unwrap_or_default();
        let resolved = crate::agent::config::resolve_enabled(
            if cli_flag { Some(true) } else { None },
            "GROK_SUBAGENTS",
            result.enabled,
            config.get("subagents").is_some(),
            None,
            true,
        );
        result.enabled = resolved.value;
        if let Some(root) = user_grok_root {
            result.discover_roles_in_dir(&root.join("roles"));
            result.discover_personas_in_dir(&root.join("personas"));
        }
        result.discover_roles_in_dir(&bundled_root.join("roles"));
        result.discover_personas_in_dir(&bundled_root.join("personas"));
        result
    }
}
/// Managed MCP connector fetching config (`[managed_mcps]` in config.toml).
///
/// See [`Self::resolve`] for full priority chain.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct ManagedMcpsConfig {
    pub enabled: bool,
    pub gateway_tools_enabled: bool,
}
impl Default for ManagedMcpsConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            gateway_tools_enabled: false,
        }
    }
}
impl ManagedMcpsConfig {
    /// Priority: env var > TOML > remote > default (enabled interactive, disabled headless).
    pub(crate) fn resolve(
        config: &toml::Value,
        remote: Option<&crate::util::config::RemoteSettings>,
        is_headless: bool,
    ) -> Self {
        let mut result: Self = config
            .get("managed_mcps")
            .and_then(|v| v.clone().try_into().ok())
            .unwrap_or(Self {
                enabled: !is_headless,
                gateway_tools_enabled: false,
            });
        let managed_mcps_table = config.get("managed_mcps").and_then(|v| v.as_table());
        let has_local_enabled = managed_mcps_table.is_some_and(|t| t.contains_key("enabled"));
        let resolved = crate::agent::config::resolve_enabled(
            None,
            "GROK_MANAGED_MCPS_ENABLED",
            result.enabled,
            has_local_enabled,
            remote.and_then(|r| r.managed_mcps_enabled),
            !is_headless,
        );
        result.enabled = resolved.value;
        let has_local_gateway_tools =
            managed_mcps_table.is_some_and(|t| t.contains_key("gateway_tools_enabled"));
        let gateway_resolved = crate::agent::config::resolve_enabled(
            None,
            "GROK_MANAGED_MCP_GATEWAY_TOOLS_ENABLED",
            result.gateway_tools_enabled,
            has_local_gateway_tools,
            remote.and_then(|r| r.managed_mcp_gateway_tools_enabled),
            false,
        );
        result.gateway_tools_enabled = result.enabled && gateway_resolved.value;
        result
    }
}
/// Auxiliary model overrides under `[models]`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub(crate) struct ModelOverrideConfig {
    pub web_search: String,
    /// `None` = current model.
    pub session_summary: Option<String>,
    /// Compiled default (`grok-build`) when unset locally, remotely, and via env.
    pub image_description: Option<String>,
    /// Next-prompt suggestion model pin. Unlike the other overrides this does
    /// NOT fill a compiled default — see [`PromptSuggestModelPin`].
    #[serde(skip)]
    pub prompt_suggestion: PromptSuggestModelPin,
}
impl Default for ModelOverrideConfig {
    fn default() -> Self {
        Self {
            web_search: crate::models::default_web_search_model().to_owned(),
            session_summary: None,
            image_description: None,
            prompt_suggestion: PromptSuggestModelPin::Unpinned,
        }
    }
}
/// Resolved model pin for the next-prompt suggestion call (tab-autocomplete
/// ghost text), `env > config.toml > remote` — see
/// [`ModelOverrideConfig::resolve`].
///
/// Unlike the other auxiliary overrides this does not collapse to a plain
/// model string: the consumer (`handle_suggest_prompt`) must distinguish
/// an explicit pin from "unpinned" (where the client hint and the built-in
/// `grok-build-0.1` default apply), and whether the pin came from the env
/// escape hatch. Every effective model except an env pin is catalog-guarded —
/// when the model is not in the shell's catalog (e.g. `grok-build-0.1` for
/// OAuth users, whose catalogs exclude it) the per-turn suggestion request is
/// skipped entirely rather than fired doomed. The env pin is deliberately
/// exempt so `GROK_PROMPT_SUGGESTIONS_MODEL` keeps working for models a
/// catalog does not list (mirrors the pager, which forwards the env value
/// without checking its catalog).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum PromptSuggestModelPin {
    /// `GROK_PROMPT_SUGGESTIONS_MODEL` — used verbatim, bypasses the
    /// catalog guard.
    Env(String),
    /// `[models] prompt_suggestion` in config.toml, or the remote
    /// `prompt_suggestion_model` (remote settings) — catalog-guarded.
    Pinned(String),
    /// No explicit pin: the client hint, then the built-in default apply
    /// (both catalog-guarded).
    #[default]
    Unpinned,
}
/// Drop whitespace-only auxiliary model overrides (treat like unset).
fn non_empty_model_override(value: Option<&str>) -> Option<String> {
    value.and_then(|v| {
        let trimmed = v.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_owned())
        }
    })
}
impl ModelOverrideConfig {
    /// CLI flag > env var > config.toml > remote settings > compiled default.
    /// `image_description` and `session_summary` always resolve to `Some(_)`
    /// (default `grok-build`), never the session model.
    /// `prompt_suggestion` resolves to a [`PromptSuggestModelPin`] instead of
    /// a model string (no CLI flag; the default and the catalog guard live at
    /// the consumer, `handle_suggest_prompt`).
    pub(crate) fn resolve(
        cli_web_search_model: Option<&str>,
        cli_session_summary_model: Option<&str>,
        config: &toml::Value,
        remote: Option<&crate::util::config::RemoteSettings>,
    ) -> Self {
        let models_table = config.get("models");
        let parsed_models: crate::agent::config::ModelsConfig = models_table
            .and_then(|v| v.clone().try_into().ok())
            .unwrap_or_default();
        let mut result = Self {
            web_search: parsed_models
                .web_search
                .unwrap_or_else(|| crate::models::default_web_search_model().to_owned()),
            session_summary: non_empty_model_override(parsed_models.session_summary.as_deref()),
            image_description: non_empty_model_override(parsed_models.image_description.as_deref()),
            prompt_suggestion: non_empty_model_override(parsed_models.prompt_suggestion.as_deref())
                .map(PromptSuggestModelPin::Pinned)
                .unwrap_or_default(),
        };
        let has_local_ws = models_table.and_then(|m| m.get("web_search")).is_some();
        let has_local_ss = models_table
            .and_then(|m| m.get("session_summary"))
            .is_some();
        let has_local_id = models_table
            .and_then(|m| m.get("image_description"))
            .is_some();
        if let Some(remote) = remote {
            if !has_local_ws && let Some(ref v) = remote.web_search_model {
                result.web_search = v.clone();
            }
            if !has_local_ss {
                result.session_summary =
                    non_empty_model_override(remote.session_summary_model.as_deref());
            }
            if !has_local_id {
                result.image_description =
                    non_empty_model_override(remote.image_description_model.as_deref());
            }
            if result.prompt_suggestion == PromptSuggestModelPin::Unpinned
                && let Some(v) = non_empty_model_override(remote.prompt_suggestion_model.as_deref())
            {
                result.prompt_suggestion = PromptSuggestModelPin::Pinned(v);
            }
        }
        if let Ok(v) = std::env::var("GROK_WEB_SEARCH_MODEL") {
            let v = v.trim();
            if !v.is_empty() {
                result.web_search = v.to_owned();
            }
        }
        if let Ok(v) = std::env::var("GROK_SESSION_SUMMARY_MODEL") {
            result.session_summary = non_empty_model_override(Some(v.as_str()));
        }
        if let Ok(v) = std::env::var("GROK_IMAGE_DESCRIPTION_MODEL") {
            result.image_description = non_empty_model_override(Some(v.as_str()));
        }
        if let Ok(v) = std::env::var("GROK_PROMPT_SUGGESTIONS_MODEL")
            && let Some(v) = non_empty_model_override(Some(v.as_str()))
        {
            result.prompt_suggestion = PromptSuggestModelPin::Env(v);
        }
        if let Some(v) = cli_web_search_model {
            result.web_search = v.to_owned();
        }
        if let Some(v) = cli_session_summary_model {
            result.session_summary = non_empty_model_override(Some(v));
        }
        if result.session_summary.is_none() {
            result.session_summary =
                Some(crate::models::default_session_summary_model().to_owned());
        }
        if result.image_description.is_none() {
            result.image_description =
                Some(crate::models::default_image_description_model().to_owned());
        }
        result
    }
}
/// Raw `[tools.media_gen]` counts; resolve via
/// [`ToolsConfig::resolve_max_parallel_image_gen_calls`].
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct MediaGenToolsConfig {
    #[serde(default)]
    pub max_parallel_image_gen_calls: Option<i64>,
    #[serde(default)]
    pub max_parallel_video_gen_calls: Option<i64>,
}
/// Tool behavior configuration (`[tools]` in config.toml).
///
/// Controls cross-cutting tool behavior such as `.gitignore` filtering.
///
/// ```toml
/// [tools]
/// disable_zdr_incompatible_tools = true
/// # [tools.media_gen] — see MediaGenToolsConfig
/// # [tools.zdr_video_output_s3] — see ZdrVideoOutputS3Config
/// ```
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct ToolsConfig {
    /// When `true`, all tools (including `read_file`) filter gitignored
    /// files. When `false` (default), each tool picks its own default.
    pub respect_gitignore: bool,
    /// Restrict tools whose pi API requires server-side artifact storage
    /// (currently just the video tools): without a valid
    /// `[tools.zdr_video_output_s3]` bucket they stay advertised but return
    /// setup guidance at call time. Intended for ZDR-bound teams via
    /// `~/.grok/managed_config.toml`. Defaults to `false`.
    pub disable_zdr_incompatible_tools: bool,
    /// Optional S3 bucket config for ZDR video output. When present (and
    /// valid), video tools presign an upload URL and pass it to the API so
    /// the generated video lands in a team-owned bucket instead of being
    /// downloaded locally. Only effective when `disable_zdr_incompatible_tools`
    /// is `true`. Populated from `[tools.zdr_video_output_s3]` in config.
    pub zdr_video_output_s3:
        Option<pi_tools::implementations::grok_build::video_gen::ZdrVideoOutputS3Config>,
    pub media_gen: MediaGenToolsConfig,
}
impl ToolsConfig {
    pub(crate) const ENV_MAX_PARALLEL_IMAGE_GEN_CALLS: &'static str =
        "GROK_MAX_PARALLEL_IMAGE_GEN_CALLS";
    pub(crate) const ENV_MAX_PARALLEL_VIDEO_GEN_CALLS: &'static str =
        "GROK_MAX_PARALLEL_VIDEO_GEN_CALLS";
    /// Resolve the final tools config, in priority order:
    /// 1. Env vars `GROK_RESPECT_GITIGNORE` and
    ///    `GROK_DISABLE_ZDR_INCOMPATIBLE_TOOLS` (`0`/`false` off,
    ///    `1`/`true` on).
    /// 2. `[tools]` block from the merged effective config.
    /// 3. Defaults (both `false`).
    ///
    /// Fields are read individually so a malformed
    /// `[tools.zdr_video_output_s3]` cannot wipe `disable_zdr_incompatible_tools`
    /// (or any other tools flag) via whole-table deserialize failure.
    pub(crate) fn resolve(config: &toml::Value) -> Self {
        let tools = config.get("tools");
        let mut result = Self {
            respect_gitignore: tools
                .and_then(|t| t.get("respect_gitignore"))
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            disable_zdr_incompatible_tools: tools
                .and_then(|t| t.get("disable_zdr_incompatible_tools"))
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            zdr_video_output_s3: tools
                .and_then(|t| t.get("zdr_video_output_s3"))
                .and_then(|s3_val| match s3_val
                    .clone()
                    .try_into::<
                        pi_tools::implementations::grok_build::video_gen::ZdrVideoOutputS3Config,
                    >()
                {
                    Ok(cfg) if cfg.is_valid() => Some(cfg),
                    Ok(_) => {
                        tracing::warn!(
                                "tools.zdr_video_output_s3 is present but incomplete; ignoring ZDR video output config"
                            );
                        None
                    }
                    Err(e) => {
                        tracing::warn!(
                                error = %e,
                                "tools.zdr_video_output_s3 failed to parse; ignoring ZDR video output config"
                            );
                        None
                    }
                }),
            media_gen: MediaGenToolsConfig {
                max_parallel_image_gen_calls: tools
                    .and_then(|t| t.get("media_gen"))
                    .and_then(|m| m.get("max_parallel_image_gen_calls"))
                    .and_then(|v| v.as_integer()),
                max_parallel_video_gen_calls: tools
                    .and_then(|t| t.get("media_gen"))
                    .and_then(|m| m.get("max_parallel_video_gen_calls"))
                    .and_then(|v| v.as_integer()),
            },
        };
        match std::env::var("GROK_RESPECT_GITIGNORE").as_deref() {
            Ok("0") | Ok("false") => {
                result.respect_gitignore = false;
            }
            Ok("1") | Ok("true") => {
                result.respect_gitignore = true;
            }
            _ => {}
        }
        match std::env::var("GROK_DISABLE_ZDR_INCOMPATIBLE_TOOLS").as_deref() {
            Ok("0") | Ok("false") => {
                result.disable_zdr_incompatible_tools = false;
            }
            Ok("1") | Ok("true") => {
                result.disable_zdr_incompatible_tools = true;
            }
            _ => {}
        }
        result
    }
    pub(crate) fn resolve_max_parallel_image_gen_calls(
        env: Option<&str>,
        config: Option<i64>,
        remote: Option<u32>,
    ) -> usize {
        resolve_clamped_count(
            Self::ENV_MAX_PARALLEL_IMAGE_GEN_CALLS,
            env,
            config,
            remote,
            pi_tools::media_gen_limits::DEFAULT_MAX_PARALLEL_IMAGE_GEN,
        )
    }
    pub(crate) fn resolve_max_parallel_video_gen_calls(
        env: Option<&str>,
        config: Option<i64>,
        remote: Option<u32>,
    ) -> usize {
        resolve_clamped_count(
            Self::ENV_MAX_PARALLEL_VIDEO_GEN_CALLS,
            env,
            config,
            remote,
            pi_tools::media_gen_limits::DEFAULT_MAX_PARALLEL_VIDEO_GEN,
        )
    }
}
/// Media-gen ladder: env > TOML > remote > default, with every numeric layer
/// clamping `< 1` to `1`. Non-numeric env warns and falls through.
fn resolve_clamped_count(
    env_name: &str,
    env: Option<&str>,
    config: Option<i64>,
    remote: Option<u32>,
    default: usize,
) -> usize {
    if let Some(raw) = env {
        match raw.trim().parse::<i64>() {
            Ok(v) => return clamp_positive_count(v, "env", env_name),
            Err(_) => {
                tracing::warn!(
                    name = env_name,
                    %raw,
                    "invalid env value (expected a whole number); ignoring"
                );
            }
        }
    }
    if let Some(v) = config {
        return clamp_positive_count(v, "config", env_name);
    }
    if let Some(v) = remote {
        return clamp_positive_count(i64::from(v), "remote", env_name);
    }
    default
}
/// Positive whole-number ladder: env > TOML > remote > default.
/// Invalid/non-positive env warns and falls through; TOML/remote `< 1` clamp to 1.
pub(crate) fn resolve_positive_count(
    env_name: &str,
    env: Option<&str>,
    config: Option<i64>,
    remote: Option<u32>,
    default: usize,
) -> usize {
    if let Some(value) = env {
        match pi_tools::util::env::parse_positive(value.trim()) {
            Some(parsed) => return usize::try_from(parsed).unwrap_or(usize::MAX),
            None => {
                tracing::warn!(
                    name = env_name,
                    %value,
                    "invalid env value (expected a positive whole number); ignoring"
                );
            }
        }
    }
    if let Some(v) = config {
        return clamp_positive_count(v, "config", env_name);
    }
    if let Some(v) = remote {
        return clamp_positive_count(i64::from(v), "remote", env_name);
    }
    default
}
fn clamp_positive_count(value: i64, source: &str, name: &str) -> usize {
    if value < 1 {
        tracing::warn!(source, name, value, "positive count < 1; clamping to 1");
        1
    } else {
        usize::try_from(value).unwrap_or(usize::MAX)
    }
}
/// Storage mode for session persistence.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum StorageMode {
    /// Local JSONL only (default)
    #[default]
    Local,
    /// Local + HTTP flush at end of turn
    Writeback,
}
impl StorageMode {
    /// Resolve from all sources: CLI > env var > remote settings > default (Local).
    pub(crate) fn resolve(
        cli_override: Option<&str>,
        remote: Option<&crate::util::config::RemoteSettings>,
    ) -> Self {
        if let Some(mode) = cli_override {
            match mode {
                "writeback" => return Self::Writeback,
                "local" => return Self::Local,
                other => {
                    tracing::warn!(mode = other, "unknown --storage-mode value, ignoring");
                }
            }
        }
        match std::env::var("GROK_STORAGE_MODE").as_deref() {
            Ok("writeback") => return Self::Writeback,
            Ok("local") => return Self::Local,
            _ => {}
        }
        if let Some(remote) = remote
            && remote.writeback_enabled == Some(true)
        {
            return Self::Writeback;
        }
        Self::Local
    }
}
/// The `[skills]` table from an effective config, shared by the reload
/// dispatch and `grok inspect`.
/// Effective config: layers + campaign overlay (remote cache + `GROK_CAMPAIGNS_OVERRIDE`).
pub use crate::util::config::load_effective_config;
pub(crate) use pi_config::ConfigLayers;
pub use pi_config::{
    GROK_CONFIG_ENV, GROK_CONFIG_PATH_ENV, MDM_REQUIREMENTS_SOURCE, OverlaySource,
    RequirementsLayer, RequirementsSource, ResolvedOverlay, ServingIdentity, SyncMarker,
    claude_managed_settings_probe_path, confirmed_team_switch, confirmed_team_switch_at,
    is_managed_config_hard_stale_for, is_managed_config_stale_for, load_config_file,
    load_from_disk, load_managed_config, load_merged_requirements, load_system_managed_config,
    load_toml_file, managed_config_identity_changed_at, managed_deployment_id,
    managed_policy_compromised_for, mark_managed_config_synced, mark_managed_config_synced_at,
    normalize_identity, requirements_layers, resolved_env_overlay, system_config_dir,
    user_grok_home,
};
/// Where a requirement or permission rule was loaded from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum RequirementSource {
    Requirements { path: std::path::PathBuf },
}
impl std::fmt::Display for RequirementSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Requirements { path } => write!(f, "{} (requirements)", path.display()),
        }
    }
}
/// A config field clamped by requirements.
#[derive(Debug, Clone)]
pub(crate) struct EnforcedField {
    pub path: &'static str,
    pub value: String,
    pub source: RequirementSource,
}
impl std::fmt::Display for EnforcedField {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} = {} ({})", self.path, self.value, self.source)
    }
}
/// Clamp `AgentConfig` fields per `requirements.toml`. No-op if absent.
/// System pins win over user pins on conflict.
pub(crate) fn apply_requirements(config: &mut crate::agent::config::Config) -> Vec<EnforcedField> {
    let enforced: Vec<EnforcedField> = requirements_layers()
        .into_iter()
        .flat_map(|layer| {
            apply_requirements_inner(
                config,
                &layer.value,
                &RequirementSource::Requirements {
                    path: std::path::PathBuf::from(layer.source.label().as_ref()),
                },
            )
        })
        .collect();
    keep_the_deciding_layer(enforced)
}
/// Layers arrive user first, system last, and the last write is the pin that
/// holds. Report that one, so an operator reading the log sees the file that
/// decided rather than the first that asked. Keyed by value as well as path,
/// because one layer can enforce the same path twice for different reasons.
fn keep_the_deciding_layer(mut enforced: Vec<EnforcedField>) -> Vec<EnforcedField> {
    let mut seen = std::collections::HashSet::new();
    enforced.reverse();
    enforced.retain(|field| seen.insert((field.path, field.value.clone())));
    enforced.reverse();
    enforced
}
fn apply_requirements_inner(
    config: &mut crate::agent::config::Config,
    req: &toml::Value,
    source: &RequirementSource,
) -> Vec<EnforcedField> {
    fn req_bool(req: &toml::Value, section: &str, key: &str) -> Option<bool> {
        let value = req.get(section)?.get(key)?;
        let parsed = value.as_bool();
        if parsed.is_none() {
            tracing::error!(
                section,
                key,
                kind = value.type_str(),
                "requirements value is not a boolean; the constraint is not applied"
            );
        }
        parsed
    }
    fn req_str<'a>(req: &'a toml::Value, section: &str, key: &str) -> Option<&'a str> {
        req.get(section)?.get(key)?.as_str()
    }
    let mut enforced: Vec<EnforcedField> = Vec::new();
    let mut push = |path: &'static str, value: String| {
        enforced.push(EnforcedField {
            path,
            value,
            source: source.clone(),
        });
    };
    macro_rules! pin_feature {
        ($name:ident) => {
            if let Some(val) = req_bool(req, "features", stringify!($name)) {
                config.requirements.$name.pin(val, source.clone());
                config.features.$name = Some(val);
                // Unconditional, like the registry loop: a later layer repeating
                // the pin must report, or the dedupe keeps the first layer that
                // asked instead of the one that decided.
                push(concat!("features.", stringify!($name)), format!("{val}"));
            }
        };
    }
    macro_rules! enforce_opt {
        ($section:expr, $key:expr, $field:expr) => {
            if let Some(val) = req_bool(req, $section, $key)
                && $field != Some(val)
            {
                $field = Some(val);
                push(concat!($section, ".", $key), format!("{val}"));
            }
        };
    }
    macro_rules! enforce_val {
        ($section:expr, $key:expr, $field:expr) => {
            if let Some(val) = req_bool(req, $section, $key)
                && $field != val
            {
                $field = val;
                push(concat!($section, ".", $key), format!("{val}"));
            }
        };
    }
    use crate::agent::config::TelemetryMode;
    let req_telemetry_mode = req_str(req, "features", "telemetry")
        .and_then(TelemetryMode::parse)
        .or_else(|| req_bool(req, "features", "telemetry").map(TelemetryMode::from));
    if let Some(mode) = req_telemetry_mode {
        config.requirements.telemetry.pin(mode, source.clone());
        if config.features.telemetry != Some(mode) {
            config.features.telemetry = Some(mode);
            push("features.telemetry", format!("{mode}"));
        }
    }
    macro_rules! pin_requirement_only {
        ($name:ident) => {
            if let Some(val) = req_bool(req, "features", stringify!($name)) {
                config.requirements.$name.pin(val, source.clone());
                push(concat!("features.", stringify!($name)), format!("{val}"));
            }
        };
    }
    pin_feature!(image_gen);
    pin_requirement_only!(image_edit);
    pin_feature!(video_gen);
    for spec in crate::agent::config::FEATURES {
        let Some(value) = req
            .get("features")
            .and_then(|features| features.get(spec.key))
        else {
            continue;
        };
        let Some(val) = value.as_bool() else {
            tracing::error!(
                path = spec.path,
                kind = value.type_str(),
                source = %source,
                "requirements pin is not a boolean; the pin is ignored until the next launch, \
                 which will refuse to start"
            );
            continue;
        };
        config
            .requirements
            .pin_feature(spec.id, val, source.clone());
        push(spec.path, format!("{val}"));
    }
    pin_requirement_only!(remote_fetch);
    pin_requirement_only!(title_refresh);
    if let Some(val) = req_bool(req, "telemetry", "trace_upload") {
        config.requirements.trace_upload.pin(val, source.clone());
        if config.telemetry.trace_upload != Some(val) {
            config.telemetry.trace_upload = Some(val);
            push("telemetry.trace_upload", format!("{val}"));
        }
    }
    enforce_opt!("cli", "auto_update", config.cli.auto_update);
    enforce_opt!("cli", "show_tips", config.cli.show_tips);
    enforce_opt!("memory", "enabled", config.memory.enabled);
    enforce_val!("subagents", "enabled", config.subagents.enabled);
    enforce_val!("managed_mcps", "enabled", config.managed_mcps.enabled);
    if let Some(val) = req_bool(req, "tools", "respect_gitignore") {
        config
            .requirements
            .respect_gitignore
            .pin(val, source.clone());
        push("tools.respect_gitignore", format!("{val}"));
    }
    if let Some(val) = req_bool(req, "ui", "yolo") {
        if config.ui.yolo != val {
            config.ui.yolo = val;
            push("ui.yolo", format!("{val}"));
        }
        if !val && config.default_yolo_mode {
            config.default_yolo_mode = false;
            push("ui.yolo", "--yolo blocked".to_string());
        }
    }
    macro_rules! enforce_str {
        ($section:expr, $key:expr, $field:expr) => {
            if let Some(val) = req_str(req, $section, $key)
                && $field.as_deref() != Some(val)
            {
                $field = Some(val.to_owned());
                push(concat!($section, ".", $key), val.to_owned());
            }
        };
        ($section:expr, $key:expr, $field:expr, redacted) => {
            if let Some(val) = req_str(req, $section, $key)
                && $field.as_deref() != Some(val)
            {
                $field = Some(val.to_owned());
                push(concat!($section, ".", $key), "[redacted]".to_owned());
            }
        };
    }
    enforce_str!("models", "default", config.models.default);
    enforce_str!("models", "web_search", config.models.web_search);
    enforce_str!("cli", "channel", config.cli.channel);
    enforce_str!("cli", "minimum_version", config.cli.minimum_version);
    enforce_str!("cli", "maximum_version", config.cli.maximum_version);
    enforce_str!(
        "cli",
        "required_minimum_version",
        config.cli.required_minimum_version
    );
    enforce_str!(
        "cli",
        "required_maximum_version",
        config.cli.required_maximum_version
    );
    if let Some(val) = req_str(req, "endpoints", "pi_api_base_url")
        && config.endpoints.pi_api_base_url != val
    {
        config.endpoints.pi_api_base_url = val.to_owned();
        push("endpoints.pi_api_base_url", val.to_owned());
    }
    if let Some(val) = req_str(req, "endpoints", "cli_chat_proxy_base_url")
        && config.endpoints.cli_chat_proxy_base_url.as_deref() != Some(val)
    {
        config.endpoints.cli_chat_proxy_base_url = Some(val.to_owned());
        push("endpoints.cli_chat_proxy_base_url", val.to_owned());
    }
    enforce_str!(
        "endpoints",
        "models_base_url",
        config.endpoints.models_base_url
    );
    enforce_str!(
        "endpoints",
        "models_list_url",
        config.endpoints.models_list_url
    );
    if let Some(val) = req_str(req, "sandbox", "profile") {
        config
            .requirements
            .sandbox_profile
            .pin(val.to_owned(), source.clone());
        if config.sandbox.profile.as_deref() != Some(val) {
            config.sandbox.profile = Some(val.to_owned());
            push("sandbox.profile", val.to_owned());
        }
    }
    if let Some(val) = req_bool(req, "sandbox", "auto_allow_bash") {
        config
            .requirements
            .sandbox_auto_allow_bash
            .pin(val, source.clone());
        if config.sandbox.auto_allow_bash != Some(val) {
            config.sandbox.auto_allow_bash = Some(val);
            push("sandbox.auto_allow_bash", format!("{val}"));
        }
    }
    enforce_str!(
        "endpoints",
        "trace_upload_url",
        config.endpoints.trace_upload_url
    );
    enforce_str!(
        "endpoints",
        "feedback_base_url",
        config.endpoints.feedback_base_url
    );
    enforce_str!(
        "endpoints",
        "deployment_key",
        config.endpoints.deployment_key,
        redacted
    );
    enforce_str!("telemetry", "events_url", config.telemetry.events_url);
    enforce_str!(
        "telemetry",
        "events_api_key",
        config.telemetry.events_api_key,
        redacted
    );
    enforce_val!(
        "telemetry",
        "mixpanel_enabled",
        config.telemetry.mixpanel_enabled
    );
    enforce_str!(
        "telemetry",
        "mixpanel_token",
        config.telemetry.mixpanel_token,
        redacted
    );
    enforce_str!(
        "endpoints",
        "trace_upload_bucket",
        config.endpoints.trace_upload_bucket
    );
    enforce_str!(
        "endpoints",
        "trace_upload_region",
        config.endpoints.trace_upload_region
    );
    enforce_str!(
        "endpoints",
        "trace_upload_credentials_file",
        config.endpoints.trace_upload_credentials_file
    );
    enforce_str!(
        "endpoints",
        "trace_upload_endpoint_url",
        config.endpoints.trace_upload_endpoint_url
    );
    enforce_str!(
        "endpoints",
        "trace_upload_credentials",
        config.endpoints.trace_upload_credentials,
        redacted
    );
    if let Some(val) = req.get("features").and_then(|f| f.get("codebase_indexing")) {
        use crate::agent::config::CodebaseIndexingSetting;
        match val {
            toml::Value::Boolean(b) => {
                config.features.codebase_indexing = CodebaseIndexingSetting::Enabled(*b);
                push("features.codebase_indexing", format!("{b}"));
            }
            toml::Value::Array(_) => {
                if let Ok(patterns) = val.clone().try_into::<Vec<String>>() {
                    push("features.codebase_indexing", format!("{patterns:?}"));
                    config.features.codebase_indexing = CodebaseIndexingSetting::Patterns(patterns);
                }
            }
            _ => {}
        }
    }
    if !enforced.is_empty() {
        tracing::info!(
            enforced = ?enforced.iter().map(|e| e.to_string()).collect::<Vec<_>>(),
            "deployment requirements enforced"
        );
    }
    enforced
}
/// A sandbox profile a launch asks for, as reported by [`requested_sandbox_profile`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestedSandbox {
    /// The requested profile name. Never `off`.
    pub profile: String,
    /// The layer that asked for it: `requirement`, `cli`, `env` or `config`.
    pub source: String,
    /// False when a deployment requirement fixes the profile, so `--sandbox off` cannot lift it.
    pub overridable: bool,
}
/// The sandbox profile this launch asks for, or `None` when it asks for none (`off`).
///
/// Resolves exactly like [`apply_sandbox`] (deployment requirement > `--sandbox` >
/// `GROK_SANDBOX` > `[sandbox].profile`) but applies nothing. Print mode (`zypi -p`) hands the
/// whole run to the agent process before `apply_sandbox` runs, so its caller uses this to refuse
/// to start instead of running the agent unsandboxed.
pub fn requested_sandbox_profile(cli_profile: Option<&str>) -> Option<RequestedSandbox> {
    let config = crate::agent::config::SandboxSettingsConfig::from_effective_config();
    let req = load_merged_requirements();
    let profile_req = req
        .as_ref()
        .and_then(|v| v.get("sandbox")?.get("profile")?.as_str());
    requested_sandbox(&config, cli_profile, profile_req)
}
fn requested_sandbox(
    config: &crate::agent::config::SandboxSettingsConfig,
    cli_profile: Option<&str>,
    requirement: Option<&str>,
) -> Option<RequestedSandbox> {
    let resolved = config.resolve_profile(cli_profile, requirement);
    // `ProfileName` parsing never fails: a name that is not built in is a custom profile, which
    // `apply_sandbox` refuses to run without. Only `off` (or its alias `none`) means "no sandbox".
    match resolved.value.parse::<pi_sandbox::ProfileName>() {
        Ok(pi_sandbox::ProfileName::Off) => None,
        _ => Some(RequestedSandbox {
            overridable: resolved.source != crate::agent::config::ConfigSource::Requirement,
            source: resolved.source.to_string(),
            profile: resolved.value,
        }),
    }
}
/// Resolve sandbox profile and apply OS-level enforcement. Called once at startup.
///
/// `cli_profile` is the resumed/forced base profile (a resumed session's saved
/// profile, or an explicit `--sandbox`); it wins over a fresh env/config read.
pub fn apply_sandbox(
    sandbox_config: Option<&crate::agent::config::SandboxSettingsConfig>,
    cli_profile: Option<&str>,
    cwd: Option<&std::path::Path>,
) {
    let owned;
    let config = match sandbox_config {
        Some(c) => c,
        None => {
            owned = crate::agent::config::SandboxSettingsConfig::from_effective_config();
            &owned
        }
    };
    let req = load_merged_requirements();
    let profile_req = req
        .as_ref()
        .and_then(|v| v.get("sandbox")?.get("profile")?.as_str());
    let auto_allow_req = req
        .as_ref()
        .and_then(|v| v.get("sandbox")?.get("auto_allow_bash")?.as_bool());
    let resolved = config.resolve_profile(cli_profile, profile_req);
    pi_sandbox::set_auto_allow_bash(config.resolve_auto_allow_bash(auto_allow_req).value);
    let sandbox_profile: pi_sandbox::ProfileName = resolved.value.parse().unwrap_or_else(|e| {
        eprintln!("warning: {e}, defaulting to no sandbox");
        pi_sandbox::ProfileName::Off
    });
    pi_sandbox::set_configured_profile(&resolved.value);
    let workspace = cwd
        .and_then(|p| dunce::canonicalize(p).ok())
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    #[cfg(target_os = "linux")]
    let requires_read_deny = pi_sandbox::requires_read_deny(&sandbox_profile, &workspace);
    #[cfg(target_os = "linux")]
    let requires_hook_write_deny =
        pi_sandbox::requires_hook_write_deny(&sandbox_profile, &workspace);
    #[cfg(target_os = "linux")]
    let requires_bwrap = requires_read_deny || requires_hook_write_deny;
    #[cfg(target_os = "linux")]
    {
        let refuse_unprotected = |cause: &str| {
            eprintln!(
                "error: this sandbox could not enforce its deny list on Linux: \
                 {cause} Refusing to start with denied paths unprotected."
            );
        };
        match pi_sandbox::bwrap_reexec_for_profile(&sandbox_profile, &workspace) {
            Some(mut cmd) => {
                use std::os::unix::process::CommandExt;
                let err = cmd.exec();
                if requires_bwrap {
                    refuse_unprotected(&format!(
                        "bwrap exec failed: {err}. Install bubblewrap with \
                         `apt install -y bubblewrap`."
                    ));
                    std::process::exit(1);
                }
                eprintln!(
                    "WARNING: bwrap exec failed: {err}. \
                     Falling back to Landlock sandbox. \
                     Install bubblewrap: apt install -y bubblewrap"
                );
            }
            None if requires_bwrap && pi_sandbox::is_inside_bwrap() => {
                if requires_hook_write_deny
                    && let Err(e) = pi_sandbox::verify_hook_write_deny_enforced()
                {
                    eprintln!(
                        "error: sandbox reports bwrap but required hook write-deny \
                         mounts are missing or writable ({e}); refusing to start \
                         (possible __GROK_INSIDE_BWRAP spoof)"
                    );
                    std::process::exit(1);
                }
            }
            None if requires_bwrap => {
                refuse_unprotected(
                    "the deny list could not be prepared; see the error above \
                     for the specific cause.",
                );
                std::process::exit(1);
            }
            None => {}
        }
    }
    if sandbox_profile != pi_sandbox::ProfileName::Off {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        let requires_protection = {
            let is_custom = matches!(sandbox_profile, pi_sandbox::ProfileName::Custom(_));
            let needs_hooks = pi_sandbox::requires_hook_write_deny(&sandbox_profile, &workspace);
            is_custom || needs_hooks
        };
        let mut sandbox = pi_sandbox::SandboxManager::new(sandbox_profile, &workspace);
        if let Err(e) = sandbox.apply(&workspace) {
            eprintln!("warning: sandbox could not be applied: {e}");
        }
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        {
            #[cfg(target_os = "macos")]
            let unappliable = requires_protection && !sandbox.is_applied();
            #[cfg(target_os = "linux")]
            let unappliable =
                requires_protection && !sandbox.is_applied() && !pi_sandbox::is_inside_bwrap();
            if unappliable {
                eprintln!(
                    "error: could not apply the '{}' sandbox profile; see the \
                     warning above for the cause. Refusing to start with its \
                     protections missing.",
                    sandbox.profile()
                );
                std::process::exit(1);
            }
            #[cfg(target_os = "linux")]
            if requires_hook_write_deny
                && pi_sandbox::is_inside_bwrap()
                && let Err(e) = pi_sandbox::verify_hook_write_deny_enforced()
            {
                eprintln!(
                    "error: required hook write-deny mounts not verified after apply ({e}); \
                     refusing to start"
                );
                std::process::exit(1);
            }
        }
        sandbox.install();
    }
}
pub(crate) use pi_workspace::project_config::find_project_configs;
/// Resolve the effective `[plugins]` config for a working directory the same
/// way a session does at reload time: global/user config
/// ([`load_effective_config`]) plus every ancestor project `.grok/config.toml`
/// ([`find_project_configs`], extending `paths` and `disabled`) plus the
/// imported `enabledPlugins` merge.
///
/// Shared by `reload_plugins_impl`, `x.ai/commands/list`, and the agent's
/// eager plugin-registry fan-out so all three discover the same plugins for a
/// given cwd. Centralizing it prevents the paths/disabled/discovered-command
/// drift those callers would otherwise accumulate.
pub(crate) fn resolve_effective_plugins_config(
    cwd: &std::path::Path,
) -> crate::agent::config::PluginsConfig {
    let extract = |toml_val: &toml::Value| -> Option<crate::agent::config::PluginsConfig> {
        toml_val
            .get("plugins")
            .and_then(|v| v.clone().try_into().ok())
    };
    let mut plugins_cfg = load_effective_config()
        .ok()
        .and_then(|t| extract(&t))
        .unwrap_or_default();
    let project_trusted = crate::agent::folder_trust::project_scope_allowed(cwd);
    for config_path in find_project_configs(cwd) {
        if let Ok(toml_val) = load_config_file(&config_path)
            && let Some(proj) = extract(&toml_val)
        {
            if project_trusted {
                plugins_cfg.paths.extend(proj.paths);
            }
            plugins_cfg.disabled.extend(proj.disabled);
        }
    }
    plugins_cfg.merge_claude_enabled_plugins(Some(cwd));
    plugins_cfg
}
pub(crate) use pi_config::{deep_merge_toml, expand_env_vars_in_string};
#[cfg(test)]
mod tests;
