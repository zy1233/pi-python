use anyhow::Result;
use indexmap::IndexMap;
use pi_agent::prompt::skills::SkillsConfig;
use pi_tools::types::compat::CompatConfigToml;
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
/// transport-less case is steered to `disabled_mcp_servers`, the real
/// disable mechanism.
fn diagnose_invalid_entry(name: &str, value: &TomlValue, error: &str) -> McpServerConfigProblem {
    let has_command = value.get("command").is_some();
    let has_url = value.get("url").is_some();
    let message = if !has_command && !has_url {
        format!(
            "`mcp_servers.{name}` has no transport. To run it, set `command = \"...\"` or \
             `url = \"...\"`. To turn it off, add \"{name}\" to `disabled_mcp_servers` instead of \
             leaving an entry with no transport."
        )
    } else {
        format!("`mcp_servers.{name}` has an invalid transport: {error}.")
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
                             ignored."
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
                             off."
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

fn config_path() -> PathBuf {
    crate::util::grok_home::grok_home().join("config.toml")
}

/// Path to the user-level config file (`~/.grok/config.toml`).
pub(crate) fn user_config_path() -> PathBuf {
    config_path()
}

#[cfg(test)]
mod tests {
    use super::*;
    use pi_config_types::McpConfig;
    use toml::Value as TomlValue;

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

    // === merge_section tests ===
}
