use agent_client_protocol as acp;
use anyhow::Result;
use indexmap::IndexMap;
use pi_agent::prompt::skills::SkillsConfig;
use pi_tools::types::compat::{CompatConfig, CompatConfigToml};
use std::collections::HashMap;
use std::path::PathBuf;
use toml::Value as TomlValue;

// MCP server config value types extracted to `pi-config-types` (config
// dependency inversion); re-exported so `crate::util::config::*` paths keep working.
pub use pi_config_types::{
    KNOWN_MCP_SERVER_FIELDS, McpJsonOAuthBlock, McpPreferenceSource, McpPreferencesFile,
    McpServerConfig, McpServerConfigProblem, McpServerPreferences, McpServerProblemSeverity,
    McpServerTransportConfig, McpSetupConfig, McpSetupDerivedValue, McpSetupField,
    McpSetupFieldType, McpSetupOption, McpSetupResolution,
};
// Permission-policy value types likewise extracted; re-exported to keep paths stable.
pub(crate) use pi_config_types::PermissionConfig;
// Relay-sync + MCP-config value types extracted; re-exported to keep paths stable.
pub(crate) use pi_config_types::McpConfig;
// Worktree-pool config value type extracted; re-exported to keep paths stable.

/// TUI/CLI settings. Composed from typed section configs defined in `agent::config`.
#[derive(Debug, Clone, Default)]
pub struct Config {
    pub cli: crate::agent::config::CliConfig,
    pub models: crate::agent::config::ModelsConfig,
    pub ui: crate::agent::config::UiConfig,
    pub harness: crate::agent::config::HarnessConfig,
    pub skills: SkillsConfig,
    /// `[compat]` vendor-compatibility config, round-tripped so the
    /// pager preserves per-vendor toggles when persisting other settings.
    pub compat: CompatConfigToml,
    /// Management API key from `[endpoints]`.
    pub management_api_key: Option<String>,
    /// Permission policy rules loaded from `[permission]` section in config.toml.
    pub permission: Option<PermissionConfig>,
    pub diagnostics: crate::agent::config::DiagnosticsConfig,
    /// `[session]` section — round-tripped through `merge_section` so
    /// pager setters can persist session fields (e.g. auto-compact threshold).
    pub session: crate::agent::config::SessionConfig,
    /// `[toolset.ask_user_question]` sub-table — the only `[toolset]` piece
    /// the settings modal writes; the rest of `[toolset]` never round-trips
    /// (it carries runtime-only structs whose defaults must not hit disk).
    pub ask_user_question: crate::tools::config::AskUserQuestionToolConfig,
    /// `[privacy]` — local banner ack (not auth-metadata).
    pub privacy: PrivacyConfig,
    pub consent: super::consent::ConsentConfig,
    /// `[telemetry]` — only the key the pager persists round-trips.
    pub telemetry: TelemetryPersistConfig,
    /// `[features]` — only the key the pager persists round-trips.
    pub features: FeaturesPersistConfig,
}

/// The `[telemetry]` slice the pager is allowed to write back. Unmodeled
/// keys under `[telemetry]` are preserved by the deep merge in
/// `save_config_locked`.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct TelemetryPersistConfig {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trace_upload: Option<bool>,
}

/// The `[features]` slice the pager is allowed to write back. Unmodeled
/// keys under `[features]` are preserved by the deep merge in
/// `save_config_locked`.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct FeaturesPersistConfig {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub feedback_trace_card: Option<bool>,
}

#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct PrivacyConfig {
    /// Last banner dismiss (Accept/Customize), RFC 3339 UTC. None/0 remote
    /// `privacy_banner_reshow_days` = never re-show once set.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub privacy_banner_acked: Option<String>,
}

/// Load MCP servers with project-scoped overrides from `.grok/config.toml`.
///
/// Merge strategy:
/// 1. Load MCP servers from global `~/.grok/config.toml`
/// 2. Walk from git repo root down to `cwd`, loading `.grok/config.toml` at each level
///    (matching the convention used by skills and AGENTS.md discovery)
/// 3. Each level's entries replace entries with the same name entirely
///    (no deep merge — omitted fields fall back to defaults)
/// 4. Closer directories (cwd) take priority over further ones (repo root)
pub fn load_mcp_servers(cwd: &std::path::Path, compat: &CompatConfig) -> Vec<acp::McpServer> {
    let global_config = crate::config::load_effective_config()
        .unwrap_or_else(|_| TomlValue::Table(toml::map::Map::new()));
    reload_mcp_servers_merged(&global_config, cwd, compat)
}

/// Merge MCP servers from a pre-parsed global config with project-scoped overrides.
///
/// Same merge strategy as [`load_mcp_servers_with_project`] but takes the global
/// config as a pre-parsed `toml::Value` instead of re-reading from disk. Project
/// configs are still read from disk because the watcher signals paths, not content.
pub(crate) fn reload_mcp_servers_merged(
    global_config: &TomlValue,
    cwd: &std::path::Path,
    compat: &CompatConfig,
) -> Vec<acp::McpServer> {
    let mut servers: IndexMap<String, McpServerConfig> = IndexMap::new();

    for (name, config) in parse_mcp_servers_from_toml(global_config) {
        servers.insert(name, config);
    }

    let project_configs = crate::config::find_project_configs(cwd);
    for config_path in &project_configs {
        if let Ok(root) = crate::config::load_config_file(config_path) {
            let project_servers = parse_mcp_servers_from_toml(&root);
            if !project_servers.is_empty() {
                tracing::info!(
                    count = project_servers.len(),
                    path = %config_path.display(),
                    "Loaded project-scoped MCP servers from .grok/config.toml"
                );
                for (name, config) in project_servers {
                    servers.insert(name, config);
                }
            }
        }
    }
    // Also load from ~/.claude.json (lower priority than TOML)
    let claude_servers = load_claude_json_mcp_servers_as_configs(cwd, compat);
    tracing::info!(
        count = claude_servers.len(),
        "Loaded MCP servers from ~/.claude.json"
    );
    for (name, config) in claude_servers {
        servers.entry(name).or_insert(config);
    }

    // Also load from ~/.cursor/mcp.json (lower priority than TOML and ~/.claude.json)
    let cursor_servers = load_cursor_mcp_servers_as_configs(cwd, compat);
    tracing::info!(
        count = cursor_servers.len(),
        "Loaded Cursor MCP servers from ~/.cursor/mcp.json"
    );
    for (name, config) in cursor_servers {
        servers.entry(name).or_insert(config);
    }

    // Also load from .mcp.json files (lower priority than TOML)
    let mcp_json_servers = load_mcp_json_servers_as_configs(cwd);
    tracing::info!(
        count = mcp_json_servers.len(),
        "Loaded .mcp.json MCP servers"
    );
    for (name, config) in mcp_json_servers {
        servers.entry(name).or_insert(config);
    }

    let preferences = load_mcp_preferences().file();
    let sub = &crate::config::expand_env_vars_in_string;
    servers
        .into_iter()
        .filter_map(|(name, config)| {
            let mut config = match config.resolve_setup(preferences.servers.get(&name)) {
                McpSetupResolution::Resolved(config) => config,
                McpSetupResolution::Required(_) => return None,
                McpSetupResolution::Invalid(reason) => {
                    tracing::warn!(server = %name, error = %reason, "MCP setup config is invalid");
                    return None;
                }
            };
            config.expand_strings(sub);
            config.to_acp_mcp_server(name)
        })
        .collect()
}

pub(crate) fn mcp_preferences_path() -> PathBuf {
    pi_config::grok_home().join("mcp_preferences.json")
}

/// Result of loading prefs. Corrupt files are readable as empty for resolution
/// but must not be overwritten (would clobber other servers).
#[derive(Debug, Clone)]
pub(crate) enum McpPreferencesLoad {
    Ok(McpPreferencesFile),
    Missing,
    Corrupt,
}

impl McpPreferencesLoad {
    pub(crate) fn file(&self) -> McpPreferencesFile {
        match self {
            Self::Ok(f) => f.clone(),
            Self::Missing | Self::Corrupt => McpPreferencesFile::default(),
        }
    }
}

pub(crate) fn load_mcp_preferences() -> McpPreferencesLoad {
    load_mcp_preferences_from(&mcp_preferences_path())
}

pub(crate) fn load_mcp_preferences_from(path: &std::path::Path) -> McpPreferencesLoad {
    let content = match std::fs::read_to_string(path) {
        Ok(content) => content,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return McpPreferencesLoad::Missing,
        Err(e) => {
            tracing::warn!(path = %path.display(), error = %e, "failed to read MCP preferences");
            return McpPreferencesLoad::Corrupt;
        }
    };
    match serde_json::from_str(&content) {
        Ok(file) => McpPreferencesLoad::Ok(file),
        Err(e) => {
            tracing::warn!(path = %path.display(), error = %e, "failed to parse MCP preferences");
            McpPreferencesLoad::Corrupt
        }
    }
}

/// Deserialize one `[mcp_servers.<name>]` table, also returning any unrecognized keys.
fn deserialize_mcp_server_config(
    value: &TomlValue,
) -> Result<(McpServerConfig, Vec<String>), String> {
    let unknown_fields = value.as_table().map_or_else(Vec::new, |table| {
        table
            .keys()
            .filter(|field| !KNOWN_MCP_SERVER_FIELDS.contains(&field.as_str()))
            .cloned()
            .collect()
    });
    let config = toml::Value::try_into::<McpServerConfig>(value.clone())
        .map_err(|error| error.to_string())?;
    Ok((config, unknown_fields))
}

/// Turn a failed `[mcp_servers.<name>]` entry into an actionable problem. The
/// transport-less case is steered to `disabled_mcp_servers`, Grok's real
/// disable mechanism.
fn diagnose_invalid_entry(name: &str, value: &TomlValue, error: &str) -> McpServerConfigProblem {
    let has_command = value.get("command").is_some();
    let has_url = value.get("url").is_some();
    let message = if !has_command && !has_url {
        format!(
            "`mcp_servers.{name}` has no transport. To run it, set `command = \"...\"` or \
             `url = \"...\"`. To turn it off, add \"{name}\" to `disabled_mcp_servers` instead of \
             leaving an entry with no transport. \
             See ~/.grok/docs/user-guide/07-mcp-servers.md"
        )
    } else {
        format!(
            "`mcp_servers.{name}` has an invalid transport: {error}. \
             See ~/.grok/docs/user-guide/07-mcp-servers.md"
        )
    };
    McpServerConfigProblem {
        server: name.to_string(),
        field: None,
        severity: McpServerProblemSeverity::Error,
        message,
    }
}

pub(crate) struct ParsedMcpServers {
    pub servers: IndexMap<String, McpServerConfig>,
    pub problems: Vec<McpServerConfigProblem>,
}

/// Parse `[mcp_servers.*]` without ever failing the whole config: valid servers
/// load, invalid entries are reported (GBT-4128).
pub(crate) fn parse_mcp_servers_with_problems(root: &TomlValue) -> ParsedMcpServers {
    let mut servers = IndexMap::new();
    let mut problems = Vec::new();

    let entries = match root {
        TomlValue::Table(table) => match table.get("mcp_servers") {
            Some(TomlValue::Table(mcp_servers)) => mcp_servers,
            _ => return ParsedMcpServers { servers, problems },
        },
        _ => return ParsedMcpServers { servers, problems },
    };

    for (name, value) in entries {
        match deserialize_mcp_server_config(value) {
            Ok((config, unknown_fields)) => {
                for field in unknown_fields {
                    problems.push(McpServerConfigProblem {
                        server: name.clone(),
                        field: Some(field.clone()),
                        severity: McpServerProblemSeverity::Warning,
                        message: format!(
                            "`mcp_servers.{name}` has an unrecognized field `{field}`; it is \
                             ignored. See ~/.grok/docs/user-guide/07-mcp-servers.md"
                        ),
                    });
                }
                if config.enabled
                    && let Some(field) = config.blank_transport_field()
                {
                    problems.push(McpServerConfigProblem {
                        server: name.clone(),
                        field: Some(field.to_string()),
                        severity: McpServerProblemSeverity::Error,
                        message: format!(
                            "`mcp_servers.{name}` is enabled but its `{field}` is blank. \
                             Set a value, or add \"{name}\" to `disabled_mcp_servers` to turn it \
                             off. See ~/.grok/docs/user-guide/07-mcp-servers.md"
                        ),
                    });
                    continue;
                }
                servers.insert(name.clone(), config);
            }
            Err(error) => problems.push(diagnose_invalid_entry(name, value, &error)),
        }
    }
    ParsedMcpServers { servers, problems }
}

/// Wrapper that logs problems and returns only the valid servers.
pub(crate) fn parse_mcp_servers_from_toml(root: &TomlValue) -> IndexMap<String, McpServerConfig> {
    let ParsedMcpServers { servers, problems } = parse_mcp_servers_with_problems(root);
    for problem in &problems {
        tracing::warn!(server = %problem.server, "{}", problem.message);
    }
    servers
}

// ── .mcp.json support ────────────────────────────────────────────────

// `.mcp.json` discovery moved to `pi-workspace` (client-side, shared with
// the folder-trust gate); re-exported so `crate::util::config::*` paths keep working.
pub(crate) use pi_workspace::project_config::find_mcp_json_files;

/// Load .mcp.json servers as McpServerConfig map (for merging into load_mcp_servers).
pub(crate) fn load_mcp_json_servers_as_configs(
    cwd: &std::path::Path,
) -> IndexMap<String, McpServerConfig> {
    // Phase 2 cutoff: if the user has imported, skip reading .mcp.json.
    if crate::claude_import::is_claude_import_marked_with_log("load_mcp_json_servers_as_configs") {
        return IndexMap::new();
    }
    load_mcp_json_servers_as_configs_unfiltered(cwd)
}

/// Like [`load_mcp_json_servers_as_configs`] but bypasses the import-marker
/// gate. Used by the `/import-claude` scanner so users can re-import items
/// they previously skipped, even after the runtime cutoff is active.
pub(crate) fn load_mcp_json_servers_as_configs_unfiltered(
    cwd: &std::path::Path,
) -> IndexMap<String, McpServerConfig> {
    let mcp_json_files = find_mcp_json_files(cwd);
    if mcp_json_files.is_empty() {
        return IndexMap::new();
    }

    let mut result = IndexMap::new();

    // Reverse so cwd entries win on name conflict.
    for mcp_path in mcp_json_files.iter().rev() {
        if let Some(config) = read_mcp_json(mcp_path) {
            for (name, cfg) in config.mcp_servers {
                result.entry(name).or_insert(cfg);
            }
        }
    }

    result
}

/// Load ~/.claude.json MCP servers as McpServerConfig map (for merging into load_mcp_servers).
pub(crate) fn load_claude_json_mcp_servers_as_configs(
    cwd: &std::path::Path,
    compat: &CompatConfig,
) -> IndexMap<String, McpServerConfig> {
    // Compat gate: skip ~/.claude.json MCP loading when disabled.
    if !compat.claude.mcps {
        return IndexMap::new();
    }
    // Phase 2 cutoff: if the user has imported, skip reading ~/.claude.json.
    if crate::claude_import::is_claude_import_marked_with_log(
        "load_claude_json_mcp_servers_as_configs",
    ) {
        return IndexMap::new();
    }
    load_claude_json_mcp_servers_as_configs_unfiltered(cwd)
}

/// Like [`load_claude_json_mcp_servers_as_configs`] but bypasses the
/// import-marker gate. Used by the `/import-claude` scanner so users can
/// re-import items they previously skipped, even after the runtime cutoff
/// is active.
pub(crate) fn load_claude_json_mcp_servers_as_configs_unfiltered(
    cwd: &std::path::Path,
) -> IndexMap<String, McpServerConfig> {
    let Some(home) = dirs::home_dir() else {
        return IndexMap::new();
    };
    let claude_json_path = home.join(".claude.json");
    load_claude_json_mcp_servers_from_as_configs(&claude_json_path, cwd)
}

fn load_claude_json_mcp_servers_from_as_configs(
    claude_json_path: &std::path::Path,
    cwd: &std::path::Path,
) -> IndexMap<String, McpServerConfig> {
    let content = match std::fs::read_to_string(claude_json_path) {
        Ok(c) => c,
        Err(e) => {
            tracing::debug!(
                path = %claude_json_path.display(),
                error = %e,
                "failed to read ~/.claude.json"
            );
            return IndexMap::new();
        }
    };
    let value: serde_json::Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!(
                path = %claude_json_path.display(),
                error = %e,
                "failed to parse ~/.claude.json"
            );
            return IndexMap::new();
        }
    };
    let config = claude_json_mcp_from_value(&value);

    let mut result = IndexMap::new();

    // Per-project MCP servers (local scope, higher priority)
    let cwd_key = cwd.to_string_lossy();
    if let Some(project) = config.projects.get(cwd_key.as_ref()) {
        for (name, cfg) in &project.mcp_servers {
            result.insert(name.clone(), cfg.clone());
        }
    }

    // User-level MCP servers (lower priority)
    for (name, cfg) in &config.user_mcp.mcp_servers {
        result.entry(name.clone()).or_insert(cfg.clone());
    }
    tracing::info!(
        project_count = config
            .projects
            .get(cwd_key.as_ref())
            .map(|p| p.mcp_servers.len())
            .unwrap_or(0),
        user_level_count = config.user_mcp.mcp_servers.len(),
        total_count = result.len(),
        "MCP servers loaded from ~/.claude.json"
    );

    result
}

/// Load Cursor MCP servers as McpServerConfig map (for merging into load_mcp_servers).
///
/// Scans project-level `<cwd>/.cursor/mcp.json` first, then global.
pub(crate) fn load_cursor_mcp_servers_as_configs(
    cwd: &std::path::Path,
    compat: &CompatConfig,
) -> IndexMap<String, McpServerConfig> {
    // Compat gate: skip Cursor MCP loading when disabled.
    if !compat.cursor.mcps {
        return IndexMap::new();
    }
    let mut result = IndexMap::new();

    // Project-level (higher priority)
    let project_path = cwd.join(".cursor").join("mcp.json");
    if project_path.is_file()
        && let Some(config) = read_mcp_json(&project_path)
    {
        for (name, cfg) in config.mcp_servers {
            result.insert(name, cfg);
        }
    }

    // Global (lower priority — or_insert so project wins)
    if let Some(home) = dirs::home_dir() {
        let global_path = home.join(".cursor").join("mcp.json");
        if global_path.is_file()
            && let Some(config) = read_mcp_json(&global_path)
        {
            for (name, cfg) in config.mcp_servers {
                result.entry(name).or_insert(cfg);
            }
        }
    }

    result
}

/// Build an `McpConfig` from a JSON value, skipping any `mcpServers` entry that
/// fails to deserialize instead of dropping the whole file. Mirrors the
/// per-entry tolerance of [`parse_mcp_servers_with_problems`] for TOML, so one
/// bad entry in a `.mcp.json` or `~/.claude.json` cannot take out its siblings.
fn mcp_config_from_json_value(value: &serde_json::Value) -> McpConfig {
    let mut mcp_servers = IndexMap::new();
    if let Some(entries) = value.get("mcpServers").and_then(|v| v.as_object()) {
        for (name, entry) in entries {
            match serde_json::from_value::<McpServerConfig>(entry.clone()) {
                Ok(config) => {
                    mcp_servers.insert(name.clone(), config);
                }
                Err(error) => tracing::warn!(
                    server = %name,
                    error = %error,
                    "skipping invalid MCP server entry in JSON config"
                ),
            }
        }
    }
    McpConfig { mcp_servers }
}

/// Parsed `~/.claude.json` MCP view: top-level user servers plus per-project maps.
struct ClaudeJsonMcp {
    user_mcp: McpConfig,
    projects: HashMap<String, McpConfig>,
}

/// Build the `~/.claude.json` MCP view from a JSON value, tolerating bad entries
/// per server (see [`mcp_config_from_json_value`]).
fn claude_json_mcp_from_value(value: &serde_json::Value) -> ClaudeJsonMcp {
    let user_mcp = mcp_config_from_json_value(value);
    let mut projects = HashMap::new();
    if let Some(entries) = value.get("projects").and_then(|v| v.as_object()) {
        for (path, project) in entries {
            projects.insert(path.clone(), mcp_config_from_json_value(project));
        }
    }
    ClaudeJsonMcp { user_mcp, projects }
}

/// Read and parse a JSON file. Returns `None` on I/O or top-level parse errors
/// (logged); individual bad `mcpServers` entries are skipped, not fatal.
pub(crate) fn read_mcp_json(path: &std::path::Path) -> Option<McpConfig> {
    let content = std::fs::read_to_string(path)
        .map_err(|e| {
            tracing::warn!(error = %e, "failed to read MCP JSON");
        })
        .ok()?;
    let value: serde_json::Value = serde_json::from_str(&content)
        .map_err(|e| {
            tracing::warn!(error = %e, "failed to parse MCP JSON");
        })
        .ok()?;
    Some(mcp_config_from_json_value(&value))
}

/// Plugin registry for one-shot CLI discovery (matches mcp doctor gating).
///
/// Resolves the same cwd-effective `[plugins]` table as session startup
/// (`resolve_effective_plugins_config`), so trusted project `[plugins].paths`
/// plugins are included, not just the global config. Also used by the pager's
/// `/agents` modal to list plugin-provided agents without a live session
/// registry snapshot.
pub fn load_cli_plugin_registry(cwd: &std::path::Path) -> pi_agent::plugins::PluginRegistry {
    let trust_store = pi_agent::plugins::TrustStore::load();
    // Resolve/record the folder-trust verdict first: the effective-plugins
    // resolve below gates project [plugins].paths on the cached verdict.
    let project_trusted = crate::agent::folder_trust::resolve_and_record(cwd, None, false);
    let plugins_cfg = crate::config::resolve_effective_plugins_config(cwd);
    let mut plugin_config = plugins_cfg.to_discovery_config();
    let discovered = pi_agent::plugins::discover_plugins(
        Some(cwd),
        &plugin_config,
        &trust_store,
        project_trusted,
    );
    plugin_config.populate_plugin_lists(&discovered);
    pi_agent::plugins::PluginRegistry::from_discovered(
        discovered,
        &plugin_config.disabled,
        &plugin_config.enabled,
    )
}

fn config_path() -> PathBuf {
    crate::util::grok_home::grok_home().join("config.toml")
}

/// Path to the user-level config file (`~/.grok/config.toml`).
pub(crate) fn user_config_path() -> PathBuf {
    config_path()
}

/// Returns `Some(true/false)` when `[cli] session_registry` is set in config.toml,
/// `None` when absent (allowing remote settings fallback).
/// Local config takes precedence over remote settings.
pub(crate) fn session_registry_from_toml_opt(root: &TomlValue) -> Option<bool> {
    if let TomlValue::Table(table) = root
        && let Some(TomlValue::Table(cli)) = table.get("cli")
    {
        cli.get("session_registry").and_then(|v| v.as_bool())
    } else {
        None
    }
}

/// Overrides `[cli] session_registry`; usable before `~/.grok/config.toml` exists.
pub(crate) const SESSION_REGISTRY_ENV_VAR: &str = "GROK_SESSION_REGISTRY";

pub(crate) fn session_registry_from_env_opt() -> Option<bool> {
    pi_config::env_bool(SESSION_REGISTRY_ENV_VAR)
}

/// Where a local session-registry override came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegistrySource {
    /// [`SESSION_REGISTRY_ENV_VAR`].
    Env,
    /// `[cli] session_registry` in config.toml.
    ConfigToml,
}

impl RegistrySource {
    /// The user-facing name of this source, for diagnostics.
    pub const fn label(self) -> &'static str {
        match self {
            RegistrySource::Env => SESSION_REGISTRY_ENV_VAR,
            RegistrySource::ConfigToml => "[cli] session_registry",
        }
    }
}

/// Env var, then `[cli] session_registry`; `None` defers to remote settings.
pub fn session_registry_local_override_sourced(
    root: Option<&TomlValue>,
) -> Option<(bool, RegistrySource)> {
    if let Some(v) = session_registry_from_env_opt() {
        return Some((v, RegistrySource::Env));
    }
    root.and_then(session_registry_from_toml_opt)
        .map(|v| (v, RegistrySource::ConfigToml))
}

#[cfg(test)]
mod tests {
    use super::*;
    use toml::Value as TomlValue;

    /// Env beats config.toml; unrecognized env defers; both absent defers to remote.
    #[test]
    #[serial_test::serial]
    fn session_registry_local_override_precedence() {
        let toml_true: TomlValue = toml::from_str("[cli]\nsession_registry = true").unwrap();
        {
            let _g = pi_test_support::EnvGuard::set(SESSION_REGISTRY_ENV_VAR, "false");
            assert_eq!(
                session_registry_local_override_sourced(Some(&toml_true)),
                Some((false, RegistrySource::Env)),
                "env wins and reports itself as the source"
            );
        }
        {
            let _g = pi_test_support::EnvGuard::set(SESSION_REGISTRY_ENV_VAR, "bogus");
            assert_eq!(
                session_registry_local_override_sourced(Some(&toml_true)),
                Some((true, RegistrySource::ConfigToml)),
                "unrecognized env values defer to config.toml"
            );
        }
        {
            let _g = pi_test_support::EnvGuard::unset(SESSION_REGISTRY_ENV_VAR);
            assert_eq!(session_registry_local_override_sourced(None), None);
        }
    }

    /// A trusted project's `[plugins].paths` plugin (session-startup config
    /// source) must be discovered by the one-shot CLI registry, including its
    /// agents. Dev builds are folder-trust-inert, so the project path merges.
    #[test]
    #[serial_test::serial]
    fn load_cli_plugin_registry_includes_project_config_path_plugins() {
        let home = tempfile::tempdir().unwrap();
        let _env = pi_test_support::EnvGuard::set("GROK_HOME", home.path());

        let repo = tempfile::tempdir().unwrap();
        git2::Repository::init(repo.path()).unwrap();

        // Project-declared [plugins].paths plugin providing one agent.
        let plugin_dir = repo.path().join("proj-plugin");
        let agents_dir = plugin_dir.join("agents");
        std::fs::create_dir_all(&agents_dir).unwrap();
        std::fs::write(
            plugin_dir.join("plugin.json"),
            r#"{"name": "proj-plugin", "agents": "./agents"}"#,
        )
        .unwrap();
        std::fs::write(
            agents_dir.join("reviewer.md"),
            "---\nname: reviewer\ndescription: Project reviewer\n---\nBody.\n",
        )
        .unwrap();

        let grok = repo.path().join(".grok");
        std::fs::create_dir_all(&grok).unwrap();
        std::fs::write(
            grok.join("config.toml"),
            format!("[plugins]\npaths = [\"{}\"]\n", plugin_dir.display()),
        )
        .unwrap();

        let registry = load_cli_plugin_registry(repo.path());
        assert!(
            registry.get("proj-plugin").is_some(),
            "project [plugins].paths plugin must be discovered"
        );
        let agents = pi_agent::discovery::plugin_agents(&registry);
        assert!(
            agents
                .iter()
                .any(|a| a.qualified_name == "proj-plugin:reviewer"),
            "project config-path plugin agent must be enumerable, got: {:?}",
            agents.iter().map(|a| &a.qualified_name).collect::<Vec<_>>()
        );
    }

    /// Covers all canonical wire values plus the unknown/corrupt fallback.
    #[test]
    fn parse_mcp_servers_skips_unparseable_entries() {
        let root = toml::from_str::<TomlValue>(
            r#"
mcp_servers.broken = "not-a-table"

[mcp_servers.also_broken]
enabled = "yes"

[mcp_servers.ok]
command = "echo"
args = ["hi"]
"#,
        )
        .unwrap();
        let servers = parse_mcp_servers_from_toml(&root);
        assert!(!servers.contains_key("broken"));
        assert!(!servers.contains_key("also_broken"));
        assert!(servers.contains_key("ok"));
    }

    #[test]
    fn parse_mcp_server_config_reports_unknown_fields() {
        let value = toml::from_str::<TomlValue>(
            r#"
command = "echo"
enabeld = false
"#,
        )
        .unwrap();
        let (config, unknown_fields) = deserialize_mcp_server_config(&value).unwrap();
        assert!(
            config.enabled,
            "the misspelled field must not silently disable the server"
        );
        assert_eq!(unknown_fields, vec!["enabeld"]);
    }

    #[test]
    fn parse_mcp_servers_drops_transport_less_entry() {
        let root = toml::from_str::<TomlValue>(
            r#"
[mcp_servers.github]
enabled = false

[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
"#,
        )
        .unwrap();
        let ParsedMcpServers { servers, problems } = parse_mcp_servers_with_problems(&root);
        assert!(
            !servers.contains_key("github"),
            "transport-less entry is dropped, not kept"
        );
        assert!(servers.contains_key("linear"));
        assert!(servers["linear"].enabled);
        let problem = problems
            .iter()
            .find(|p| p.server == "github")
            .expect("github problem reported");
        assert_eq!(problem.severity, McpServerProblemSeverity::Error);
        assert!(
            problem.message.contains("disabled_mcp_servers"),
            "{problem:?}"
        );
    }

    #[test]
    fn parse_mcp_servers_rejects_enabled_without_transport() {
        let root = toml::from_str::<TomlValue>(
            r#"
[mcp_servers.half]
enabled = true
"#,
        )
        .unwrap();
        let ParsedMcpServers { servers, problems } = parse_mcp_servers_with_problems(&root);
        assert!(
            !servers.contains_key("half"),
            "enabled without command/url must be dropped"
        );
        assert!(problems.iter().any(|p| p.server == "half"));
    }

    #[test]
    fn parse_mcp_servers_rejects_blank_transport() {
        let root = toml::from_str::<TomlValue>(
            r#"
[mcp_servers.blank_url]
url = "  "

[mcp_servers.blank_cmd]
command = ""
"#,
        )
        .unwrap();
        let ParsedMcpServers { servers, problems } = parse_mcp_servers_with_problems(&root);
        assert!(!servers.contains_key("blank_url"), "blank url dropped");
        assert!(!servers.contains_key("blank_cmd"), "blank command dropped");
        assert_eq!(
            problems
                .iter()
                .filter(|p| p.severity == McpServerProblemSeverity::Error)
                .count(),
            2,
            "both blank transports reported: {problems:?}"
        );
    }

    #[test]
    fn json_map_skips_bad_entry_and_keeps_the_rest() {
        // One transport-less entry must not drop its siblings in the same JSON
        // file (.mcp.json / ~/.claude.json).
        let value = serde_json::json!({
            "mcpServers": {
                "bad": { "enabled": false },
                "good": { "command": "npx", "args": ["-y", "pkg"] }
            }
        });
        let config = mcp_config_from_json_value(&value);
        assert!(!config.mcp_servers.contains_key("bad"));
        assert!(config.mcp_servers.contains_key("good"));
        assert!(config.mcp_servers["good"].enabled);
    }

    #[test]
    fn test_parse_mcp_servers_empty() {
        let root = toml::from_str::<TomlValue>("").unwrap();
        let servers = parse_mcp_servers_from_toml(&root);
        assert!(servers.is_empty());
    }

    #[test]
    fn test_parse_mcp_servers_stdio() {
        let toml_str = r#"
[mcp_servers.test_server]
command = "node"
args = ["server.js"]
"#;
        let root = toml::from_str::<TomlValue>(toml_str).unwrap();
        let servers = parse_mcp_servers_from_toml(&root);
        assert_eq!(servers.len(), 1);
        assert!(servers.contains_key("test_server"));
        let config = servers.get("test_server").unwrap();
        assert!(config.enabled);
        match &config.transport {
            McpServerTransportConfig::Stdio { command, args, .. } => {
                assert_eq!(command, "node");
                assert_eq!(args, &["server.js"]);
            }
            _ => panic!("Expected Stdio transport"),
        }
    }

    // WorktreeType tests
    #[test]
    fn test_project_scoped_mcp_override_replaces_entirely() {
        // Simulate global config with timeouts
        let global_toml = r#"
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
enabled = true
startup_timeout_sec = 10
tool_timeout_sec = 60
"#;
        let global_root = toml::from_str::<TomlValue>(global_toml).unwrap();
        let global_servers = parse_mcp_servers_from_toml(&global_root);

        // Simulate project config WITHOUT timeouts
        let project_toml = r#"
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
enabled = true
"#;
        let project_root = toml::from_str::<TomlValue>(project_toml).unwrap();
        let project_servers = parse_mcp_servers_from_toml(&project_root);

        // Global config should have timeouts
        let global_linear = global_servers.get("linear").unwrap();
        assert_eq!(global_linear.startup_timeout_sec, Some(10));
        assert_eq!(global_linear.tool_timeout_sec, Some(60));

        // Project config should NOT have timeouts (defaults apply)
        let project_linear = project_servers.get("linear").unwrap();
        assert_eq!(project_linear.startup_timeout_sec, None);
        assert_eq!(project_linear.tool_timeout_sec, None);

        // Merge: project overrides global entirely
        let mut merged: IndexMap<String, McpServerConfig> = IndexMap::new();
        for (name, config) in &global_servers {
            merged.insert(name.clone(), config.clone());
        }
        for (name, config) in &project_servers {
            merged.insert(name.clone(), config.clone());
        }

        // After merge, the project config should have replaced the global one entirely
        let merged_linear = merged.get("linear").unwrap();
        assert_eq!(merged_linear.startup_timeout_sec, None);
        assert_eq!(merged_linear.tool_timeout_sec, None);
    }

    #[test]
    fn test_project_scoped_mcp_adds_new_servers() {
        let global_toml = r#"
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
enabled = true
"#;
        let global_root = toml::from_str::<TomlValue>(global_toml).unwrap();
        let global_servers = parse_mcp_servers_from_toml(&global_root);

        let project_toml = r#"
[mcp_servers.buildkite]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.buildkite.com/mcp"]
enabled = true
"#;
        let project_root = toml::from_str::<TomlValue>(project_toml).unwrap();
        let project_servers = parse_mcp_servers_from_toml(&project_root);

        // Merge: project adds new server
        let mut merged: IndexMap<String, McpServerConfig> = IndexMap::new();
        for (name, config) in &global_servers {
            merged.insert(name.clone(), config.clone());
        }
        for (name, config) in &project_servers {
            merged.insert(name.clone(), config.clone());
        }

        assert_eq!(merged.len(), 2);
        assert!(merged.contains_key("linear"));
        assert!(merged.contains_key("buildkite"));
    }

    #[test]
    fn test_project_scoped_mcp_can_disable_server() {
        let global_toml = r#"
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
enabled = true
"#;
        let global_root = toml::from_str::<TomlValue>(global_toml).unwrap();
        let global_servers = parse_mcp_servers_from_toml(&global_root);
        assert!(global_servers.get("linear").unwrap().enabled);

        // Project config disables the server
        let project_toml = r#"
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
enabled = false
"#;
        let project_root = toml::from_str::<TomlValue>(project_toml).unwrap();
        let project_servers = parse_mcp_servers_from_toml(&project_root);

        let mut merged: IndexMap<String, McpServerConfig> = IndexMap::new();
        for (name, config) in &global_servers {
            merged.insert(name.clone(), config.clone());
        }
        for (name, config) in &project_servers {
            merged.insert(name.clone(), config.clone());
        }

        // After merge, the server should be disabled by project config
        assert!(!merged.get("linear").unwrap().enabled);
    }

    #[test]
    fn skills_config_default_is_empty() {
        let cfg = SkillsConfig::default();
        assert!(cfg.paths.is_empty());
        assert!(cfg.ignore.is_empty());
    }

    #[test]
    fn skills_config_parses_paths_and_ignore() {
        let root = toml::from_str::<TomlValue>(
            r#"
[skills]
paths = ["~/.grok/skills", "~/.grok/skills/special/SKILL.md"]
ignore = ["~/.grok/skills/noisy/SKILL.md"]
"#,
        )
        .unwrap();
        let TomlValue::Table(ref table) = root else {
            panic!()
        };
        let cfg = table
            .get("skills")
            .and_then(|v| v.clone().try_into::<SkillsConfig>().ok())
            .unwrap_or_default();
        assert_eq!(
            cfg.paths,
            vec!["~/.grok/skills", "~/.grok/skills/special/SKILL.md"]
        );
        assert_eq!(cfg.ignore, vec!["~/.grok/skills/noisy/SKILL.md"]);
    }

    #[test]
    fn test_project_scoped_mcp_preserves_unrelated_global_servers() {
        let global_toml = r#"
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
enabled = true

[mcp_servers.buildkite]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.buildkite.com/mcp"]
enabled = true
"#;
        let global_root = toml::from_str::<TomlValue>(global_toml).unwrap();
        let global_servers = parse_mcp_servers_from_toml(&global_root);

        // Project only overrides linear
        let project_toml = r#"
[mcp_servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
enabled = false
"#;
        let project_root = toml::from_str::<TomlValue>(project_toml).unwrap();
        let project_servers = parse_mcp_servers_from_toml(&project_root);

        let mut merged: IndexMap<String, McpServerConfig> = IndexMap::new();
        for (name, config) in &global_servers {
            merged.insert(name.clone(), config.clone());
        }
        for (name, config) in &project_servers {
            merged.insert(name.clone(), config.clone());
        }

        // buildkite should be preserved from global config
        assert_eq!(merged.len(), 2);
        assert!(merged.get("buildkite").unwrap().enabled);
        assert!(!merged.get("linear").unwrap().enabled);
    }

    #[test]
    fn test_mcp_server_config_parses_tool_timeouts() {
        let toml_str = r#"
[mcp_servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
tool_timeout_sec = 60
tool_timeouts = { create_issue = 120, search_repositories = 30 }
"#;
        let root = toml::from_str::<TomlValue>(toml_str).unwrap();
        let servers = parse_mcp_servers_from_toml(&root);
        let github = servers.get("github").unwrap();

        assert_eq!(github.tool_timeout_sec, Some(60));
        let tt = github.tool_timeouts.as_ref().unwrap();
        assert_eq!(tt.get("create_issue"), Some(&120));
        assert_eq!(tt.get("search_repositories"), Some(&30));
        assert_eq!(tt.get("nonexistent"), None);
    }

    #[test]
    fn test_mcp_server_config_tool_timeouts_defaults_to_none() {
        let toml_str = r#"
[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]
"#;
        let root = toml::from_str::<TomlValue>(toml_str).unwrap();
        let servers = parse_mcp_servers_from_toml(&root);
        let fs = servers.get("filesystem").unwrap();

        assert!(fs.tool_timeouts.is_none());
        assert!(fs.tool_timeout_sec.is_none());
        assert!(fs.expose_image_base64.is_none());
    }

    #[test]
    fn test_mcp_server_config_parses_expose_image_base64() {
        let toml_str = r#"
[mcp_servers.grafana]
url = "https://grafana.example/mcp"
expose_image_base64 = true
"#;
        let root = toml::from_str::<TomlValue>(toml_str).unwrap();
        let servers = parse_mcp_servers_from_toml(&root);
        let grafana = servers.get("grafana").unwrap();
        assert_eq!(grafana.expose_image_base64, Some(true));
    }

    #[test]
    fn mcp_json_oauth_block_parsed_into_oauth_config() {
        let json = r#"{
            "mcpServers": {
                "slack": {
                    "type": "http",
                    "url": "https://mcp.slack.example/mcp",
                    "oauth": { "clientId": "slack-byo-client", "callbackPort": 3118 }
                }
            }
        }"#;
        let config: McpConfig = serde_json::from_str(json).expect("parse .mcp.json");
        let slack = config.mcp_servers.get("slack").expect("slack server");

        let block = slack.oauth.as_ref().expect("oauth block parsed");
        assert_eq!(block.client_id.as_deref(), Some("slack-byo-client"));
        assert_eq!(block.callback_port, Some(3118));

        let oauth = slack.oauth_config().expect("oauth_config from block");
        assert_eq!(oauth.client_id.as_deref(), Some("slack-byo-client"));
        assert_eq!(oauth.callback_port, Some(3118));
    }

    #[test]
    fn load_cursor_mcp_servers_as_configs_parses_cursor_mcp_json() {
        // NOTE: This test cannot override HOME (dirs::home_dir is not
        // controlled by an env var on all platforms), so we test the
        // underlying read_mcp_json + McpConfig round-trip instead.
        let dir = tempfile::tempdir().unwrap();
        let mcp_json_path = dir.path().join("mcp.json");
        std::fs::write(
            &mcp_json_path,
            r#"{
                "mcpServers": {
                    "test_server": {
                        "command": "node",
                        "args": ["server.js"]
                    }
                }
            }"#,
        )
        .unwrap();
        let config = read_mcp_json(&mcp_json_path).expect("should parse cursor mcp.json");
        assert_eq!(config.mcp_servers.len(), 1);
        assert!(config.mcp_servers.contains_key("test_server"));
    }

    #[test]
    fn load_cursor_mcp_servers_as_configs_returns_empty_for_missing_file() {
        let dir = tempfile::tempdir().unwrap();
        let mcp_json_path = dir.path().join("mcp.json");
        // File does not exist — should not panic, just return None.
        assert!(read_mcp_json(&mcp_json_path).is_none());
    }

    // === merge_section tests ===
}
