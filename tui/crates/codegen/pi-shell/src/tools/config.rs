use serde::{Deserialize, Serialize};

// 10 hours

/// User configurable settings for the built-in bash tool (`[toolset.bash]`).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct BashToolConfig {
    pub timeout_secs: Option<f64>,
    /// Foreground ceiling for model-provided command timeouts (seconds). When
    /// `None`, `to_bash_params_json` emits the production default
    /// ([`PRODUCTION_MAX_TIMEOUT_SECS`], 10h), opting up from the tool-server
    /// binary's 5-minute built-in default. Set it lower to cap foreground
    /// commands further. Bounds foreground only; background tasks are always
    /// unbounded. Tools run in-process so this is always honored — no server
    /// version gate needed.
    pub max_timeout_secs: Option<f64>,
    pub output_byte_limit: Option<usize>,
    pub cmd_prefix: Option<String>,
    /// Whether to auto-background a command when it times out (default: `true`).
    pub auto_background_on_timeout: Option<bool>,
    /// Max FG block before auto-bg when `auto_background_on_timeout` is on
    /// (milliseconds). `None` → server default 15s; `Some(0)` → no short budget
    /// (auto-bg only at model/default timeout).
    pub foreground_block_budget_ms: Option<u64>,
    /// Whether to allow a background `&` operator in foreground commands
    /// (default: `true`). Resolution: config.toml (this) > remote settings > `true`.
    pub allow_background_operator: Option<bool>,
    pub login_shell_capture: Option<bool>,
}

/// User configurable settings for the ask_user_question tool
/// (`[toolset.ask_user_question]`).
///
/// Consumed out-of-band by
/// `crate::util::config::resolve_ask_user_question_params_from_disk`, which
/// reads the raw config layers so the documented precedence (requirements >
/// env > user > managed > remote) holds — this struct exists so the keys are
/// recognized in `config.toml` and round-trip through `AgentConfig`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct AskUserQuestionToolConfig {
    /// Whether the questionnaire timeout is armed (default: `true`).
    /// `false` waits forever for answers.
    pub timeout_enabled: Option<bool>,
    /// Wait budget in seconds when the timer is armed (positive integer;
    /// default: 1800 / 30 minutes).
    pub timeout_secs: Option<u64>,
}

/// User configurable settings for the web_fetch tool (`[toolset.web_fetch]`).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct WebFetchToolConfig {
    /// Egress proxy endpoint. When set, all HTTP requests are routed through
    /// this URL. Resolution: TOML > `GROK_WEB_FETCH_PROXY` env > remote settings > None.
    pub proxy_endpoint: Option<String>,
    /// Domains the tool is allowed to fetch. When set, overrides the built-in
    /// default allowlist. An explicit empty list blocks all fetches.
    /// Resolution: TOML > remote settings > built-in defaults.
    pub allowed_domains: Option<Vec<String>>,
    /// Allow fetches to explicit loopback hosts only (`localhost` / `127.0.0.0/8`
    /// / `::1`). Private and metadata ranges stay blocked. Default off.
    /// Resolution: TOML > `GROK_WEB_FETCH_ALLOW_LOCAL` env > false.
    pub allow_local: Option<bool>,
}

/// Top-level toolset configuration for the shell layer.
///
/// Parsed from `[toolset]` in `config.toml`. It is distinct from
/// `pi_tools::registry::types::ToolsetConfig`, which holds tool-implementation-level
/// config. Unknown keys (such as the former `web_search` sampler table, which belonged to
/// the removed Rust runtime) are ignored.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ShellToolsetConfig {
    pub bash: BashToolConfig,
    /// Web fetch tool parameters (`[toolset.web_fetch]`).
    #[serde(default)]
    pub web_fetch: WebFetchToolConfig,
    /// Ask-user-question tool parameters (`[toolset.ask_user_question]`).
    #[serde(default)]
    pub ask_user_question: AskUserQuestionToolConfig,
    /// Which file-operation toolset to use: `"standard"` (default) or `"hashline"`.
    #[serde(default)]
    pub file_toolset: FileToolset,
    /// Hashline scheme parameters. Only used when `file_toolset = "hashline"`.
    #[serde(default)]
    pub hashline: HashlineSchemeConfig,
}

impl Default for ShellToolsetConfig {
    fn default() -> Self {
        Self {
            bash: BashToolConfig::default(),
            web_fetch: WebFetchToolConfig::default(),
            ask_user_question: AskUserQuestionToolConfig::default(),
            file_toolset: FileToolset::default(),
            hashline: HashlineSchemeConfig::default(),
        }
    }
}

// ---------------------------------------------------------------------------
// File toolset selection
// ---------------------------------------------------------------------------

/// Configuration for hashline anchor scheme parameters.
///
/// Configurable in `config.toml` under `[toolset.hashline]`:
/// ```toml
/// [toolset]
/// file_toolset = "hashline"
///
/// [toolset.hashline]
/// scheme = "chunk"       # "chunk" (default) or "content_only"
/// hash_len = 3           # anchor hash length (1-4, default 3)
/// chunk_size = 8         # chunk size for chunk scheme (default 8)
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct HashlineSchemeConfig {
    /// Active scheme: `"chunk"` (default) or `"content_only"`.
    pub scheme: String,
    /// Anchor hash length in characters (1-4).
    pub hash_len: usize,
    /// Chunk size for the chunk scheme.
    pub chunk_size: usize,
}

impl Default for HashlineSchemeConfig {
    fn default() -> Self {
        Self {
            scheme: "chunk".to_owned(),
            hash_len: 3,
            chunk_size: 8,
        }
    }
}

/// Which set of read/edit/search tools to use for file operations.
///
/// Selects between the standard `GrokBuild` toolset (`read_file`,
/// `search_replace`, `grep`) and the anchor-based `GrokBuildHashline`
/// toolset (`hashline_read`, `hashline_edit`, `hashline_grep`).
/// The two are mutually exclusive.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FileToolset {
    /// Standard toolset: read_file, search_replace, grep.
    #[default]
    Standard,
    /// Hashline toolset: hashline_read, hashline_edit, hashline_grep.
    Hashline,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_toolset_default_is_standard() {
        assert_eq!(FileToolset::default(), FileToolset::Standard);
    }

    #[test]
    fn file_toolset_deserializes_from_string() {
        let standard: FileToolset = serde_json::from_str("\"standard\"").unwrap();
        assert_eq!(standard, FileToolset::Standard);
        let hashline: FileToolset = serde_json::from_str("\"hashline\"").unwrap();
        assert_eq!(hashline, FileToolset::Hashline);
    }

    // -- resolve_file_toolset precedence tests ---

    // -- resolve_params precedence tests ---

    // -- to_bash_params_json allow_background_operator precedence (local > remote > true) --

    // -- max_timeout_secs: production sets the 10h foreground ceiling explicitly
    //    (also the binary default); overridable via config.toml --

    // -- foreground_block_budget_ms: only emitted when set (server defaults to 15s) --
}
