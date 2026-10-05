use super::*;
use serial_test::serial;
use pi_test_support::EnvGuard;
/// `AutoModeConfig` parses identically from a local `[auto_mode]` TOML table
/// and an equivalent remote settings JSON object (serde is format-agnostic). The
/// lean shape is all scalars/enums, so no custom tolerant deser is needed.
#[test]
fn auto_mode_config_parses_from_toml_and_json_equivalently() {
    use pi_workspace::permission::ClassifierPromptType;
    let toml_src = r#"
enabled = true
prompt_type = "no_user_tool_prefix"
classifier_model = "grok-4.5"
classify_timeout_ms = 45000
reasoning_effort = "low"
"#;
    let from_toml: AutoModeConfig = toml::from_str(toml_src).unwrap();
    let json = serde_json::json!({
        "enabled": true,
        "prompt_type": "no_user_tool_prefix",
        "classifier_model": "grok-4.5",
        "classify_timeout_ms": 45000,
        "reasoning_effort": "low"
    });
    let from_json: AutoModeConfig = serde_json::from_value(json).unwrap();
    assert_eq!(
        serde_json::to_value(&from_toml).unwrap(),
        serde_json::to_value(&from_json).unwrap()
    );
    for cfg in [&from_toml, &from_json] {
        assert_eq!(cfg.enabled, Some(true));
        assert_eq!(
            cfg.prompt_type,
            Some(ClassifierPromptType::NoUserToolPrefix)
        );
        assert_eq!(cfg.classifier_model.as_deref(), Some("grok-4.5"));
        assert_eq!(cfg.classify_timeout_ms, Some(45_000));
        assert_eq!(cfg.reasoning_effort, Some(ReasoningEffort::Low));
    }
    let empty: AutoModeConfig = toml::from_str("").unwrap();
    assert_eq!(serde_json::to_value(&empty).unwrap(), serde_json::json!({}));
}
/// `prompt_type` wire values are the snake_case `ClassifierPromptType` names.
#[test]
fn auto_mode_prompt_type_parses_snake_case() {
    use pi_workspace::permission::ClassifierPromptType;
    for (s, variant) in [
        ("full", ClassifierPromptType::Full),
        (
            "no_user_tool_prefix",
            ClassifierPromptType::NoUserToolPrefix,
        ),
        ("bare_instructions", ClassifierPromptType::BareInstructions),
        ("just_command", ClassifierPromptType::JustCommand),
    ] {
        let cfg: AutoModeConfig = toml::from_str(&format!("prompt_type = \"{s}\"")).unwrap();
        assert_eq!(cfg.prompt_type, Some(variant));
    }
}
#[test]
fn laziness_detector_default_is_all_disabled() {
    let cfg = LazinessDetectorPerModelConfig::default();
    assert!(!cfg.enabled);
    assert_eq!(cfg.max_nudges_per_session, 0);
    assert_eq!(cfg.idle_threshold_ms, None);
    assert_eq!(cfg.min_confidence, None);
    assert_eq!(
        cfg.include_reasoning, None,
        "include_reasoning defaults to None so the harness default applies",
    );
}
#[test]
fn laziness_detector_absent_block_deserializes_to_default() {
    let json = serde_json::json!({
        "model": "test",
        "base_url": "https://test.api/v1",
        "context_window": 200_000,
    });
    let entry: ModelEntryConfig =
        serde_json::from_value(json).expect("ModelEntryConfig deserializes without detector");
    assert_eq!(
        entry.laziness_detector,
        LazinessDetectorPerModelConfig::default()
    );
    let info = ModelInfo::from_config(&entry);
    assert!(!info.laziness_detector.enabled);
}
#[test]
fn laziness_detector_block_round_trips_through_serde() {
    let json = serde_json::json!({
        "enabled": true,
        "max_nudges_per_session": 3,
        "idle_threshold_ms": 15_000,
        "min_confidence": 0.8,
        "include_reasoning": false,
    });
    let cfg: LazinessDetectorPerModelConfig =
        serde_json::from_value(json).expect("deserialize populated block");
    assert!(cfg.enabled);
    assert_eq!(cfg.max_nudges_per_session, 3);
    assert_eq!(cfg.idle_threshold_ms, Some(15_000));
    assert_eq!(cfg.min_confidence, Some(0.8));
    assert_eq!(cfg.include_reasoning, Some(false));
}
/// Pins all three states of the per-model `include_reasoning`
/// override (`Some(true)`, `Some(false)`, absent → `None`) so a
/// future drift on the `#[serde(default)]` attribute or the field
/// type fails the test rather than silently changing the resolved
/// default.
#[test]
fn laziness_detector_include_reasoning_serde_states() {
    let some_true: LazinessDetectorPerModelConfig =
        serde_json::from_value(serde_json::json!({ "include_reasoning": true }))
            .expect("Some(true)");
    assert_eq!(some_true.include_reasoning, Some(true));
    let some_false: LazinessDetectorPerModelConfig =
        serde_json::from_value(serde_json::json!({ "include_reasoning": false }))
            .expect("Some(false)");
    assert_eq!(some_false.include_reasoning, Some(false));
    let absent: LazinessDetectorPerModelConfig =
        serde_json::from_value(serde_json::json!({})).expect("absent → None");
    assert_eq!(absent.include_reasoning, None);
}
#[test]
fn parses_toolset_overrides() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [toolset.bash]
            timeout_secs = 123

            [toolset.ask_user_question]
            timeout_enabled = false
            timeout_secs = 30
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert_eq!(cfg.toolset.bash.timeout_secs, Some(123.0));
    assert_eq!(cfg.toolset.ask_user_question.timeout_enabled, Some(false));
    assert_eq!(cfg.toolset.ask_user_question.timeout_secs, Some(30));
}
#[test]
fn parses_toolset_bash_float_timeout() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [toolset.bash]
            timeout_secs = 30.5
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert_eq!(cfg.toolset.bash.timeout_secs, Some(30.5));
}
#[test]
fn resolve_runtime_fields_propagates_disable_zdr_incompatible_tools() {
    fn ctx(raw: &toml::Value) -> RuntimeResolutionContext<'_> {
        RuntimeResolutionContext {
            raw_config: raw,
            remote_settings: None,
            is_headless: false,
            cli_subagents: None,
            cli_web_search_model: None,
            cli_session_summary_model: None,
            memory_enabled_override: None,
            disable_web_search: false,
            todo_gate: false,
            laziness_debug_log: None,
            storage_mode: None,
        }
    }
    let empty: toml::Value = toml::Value::Table(toml::map::Map::new());
    let mut cfg = Config::new_from_toml_cfg(&empty).unwrap();
    cfg.resolve_runtime_fields(&ctx(&empty));
    assert!(!cfg.disable_zdr_incompatible_tools);
    let zdr: toml::Value =
        toml::from_str("[tools]\ndisable_zdr_incompatible_tools = true").unwrap();
    let mut cfg = Config::new_from_toml_cfg(&zdr).unwrap();
    cfg.resolve_runtime_fields(&ctx(&zdr));
    assert!(cfg.disable_zdr_incompatible_tools);
}
#[test]
fn resolve_runtime_fields_propagates_disable_web_search() {
    fn ctx(raw: &toml::Value, disable_web_search: bool) -> RuntimeResolutionContext<'_> {
        RuntimeResolutionContext {
            raw_config: raw,
            remote_settings: None,
            is_headless: true,
            cli_subagents: None,
            cli_web_search_model: None,
            cli_session_summary_model: None,
            memory_enabled_override: None,
            disable_web_search,
            todo_gate: false,
            laziness_debug_log: None,
            storage_mode: None,
        }
    }
    let empty: toml::Value = toml::Value::Table(toml::map::Map::new());
    let mut cfg = Config::new_from_toml_cfg(&empty).unwrap();
    cfg.resolve_runtime_fields(&ctx(&empty, false));
    assert!(!cfg.disable_web_search);
    let mut cfg = Config::new_from_toml_cfg(&empty).unwrap();
    cfg.resolve_runtime_fields(&ctx(&empty, true));
    assert!(cfg.disable_web_search);
    let toml_on: toml::Value = toml::from_str("disable_web_search = true").unwrap();
    let mut cfg = Config::new_from_toml_cfg(&toml_on).unwrap();
    cfg.resolve_runtime_fields(&ctx(&toml_on, false));
    assert!(cfg.disable_web_search);
}
#[test]
fn new_from_toml_cfg_restores_web_search_and_session_summary_models() {
    let empty: toml::Value = toml::Value::Table(toml::map::Map::new());
    let cfg = Config::new_from_toml_cfg(&empty).expect("empty config should parse");
    assert_eq!(
        cfg.web_search_model,
        crate::models::default_web_search_model(),
        "empty config should produce the compiled-in default web_search model"
    );
    assert_eq!(
        cfg.session_summary_model,
        Some(crate::models::default_session_summary_model().to_owned()),
        "empty config should produce compiled default session_summary model"
    );
    assert_eq!(
        cfg.image_description_model,
        Some(crate::models::default_image_description_model().to_owned()),
        "empty config should produce compiled default image_description model"
    );
    let with_overrides: toml::Value = toml::from_str(
        r#"
            [models]
            web_search = "custom-ws-model"
            session_summary = "custom-ss-model"
            image_description = "custom-id-model"
            "#,
    )
    .unwrap();
    let cfg2 = Config::new_from_toml_cfg(&with_overrides).expect("config should parse");
    assert_eq!(cfg2.web_search_model, "custom-ws-model");
    assert_eq!(
        cfg2.session_summary_model,
        Some("custom-ss-model".to_owned())
    );
    assert_eq!(
        cfg2.image_description_model,
        Some("custom-id-model".to_owned())
    );
}
/// GBT-4128: bad `[mcp_servers.*]` entries are dropped, not fatal.
#[test]
fn invalid_mcp_server_stub_does_not_fail_config_load() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [mcp_servers.github]
            enabled = false

            mcp_servers.broken = "not-a-table"

            [mcp_servers.also_broken]
            enabled = "yes"

            [mcp_servers.linear]
            command = "npx"
            args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config)
        .expect("bad mcp stubs must be dropped, not fail whole config");
    assert!(
        !cfg.mcp_servers.contains_key("broken"),
        "non-table entry is dropped"
    );
    assert!(
        !cfg.mcp_servers.contains_key("also_broken"),
        "wrong-type enabled is dropped"
    );
    assert!(
        !cfg.mcp_servers.contains_key("github"),
        "transport-less stub is dropped (disable via disabled_mcp_servers)"
    );
    assert!(
        cfg.mcp_servers.contains_key("linear"),
        "valid MCP neighbor must still load"
    );
    assert!(cfg.mcp_servers["linear"].enabled);
}
/// The lenient parser warns per problem and never fails the whole
/// config.
#[test]
fn auth_provider_parse_warnings_are_lenient_and_specific() {
    use super::super::config_model_override_parse::{ConfigWarningKind, WarningTarget};
    let raw_config: toml::Value = toml::from_str(
        r#"
            [auth_provider.good]
            command = "printf ok"

            [auth_provider.bad-type]
            command = "printf x"
            token_ttl_secs = "not-a-number"

            [auth_provider.typo]
            command = "printf y"
            timeout_seconds = 5

            [auth_provider.commandless]
            token_ttl_secs = 60

            [auth_provider.short-ttl]
            command = "printf x"
            token_ttl_secs = 60

            [auth_provider.zero-timeout]
            command = "printf x"
            timeout_secs = 0

            [auth_provider.slow]
            command = "printf x"
            timeout_secs = 601

            [model.orphaned]
            model = "m"
            base_url = "https://x.example/v1"
            context_window = 200000
            auth_provider = "does-not-exist"
            "#,
    )
    .unwrap();
    let cfg =
        Config::new_from_toml_cfg(&raw_config).expect("one bad table must not fail the config");
    assert!(cfg.auth_providers.contains_key("good"));
    assert!(
        !cfg.auth_providers.contains_key("bad-type"),
        "malformed entry is skipped (fails closed)"
    );
    let has_provider = |name: &str, field: Option<&str>, kind: ConfigWarningKind| {
        cfg.config_warnings.iter().any(|w| {
            w.kind == kind
                && matches!(
                    &w.target,
                    WarningTarget::AuthProvider { name: n, field: f }
                        if n == name && f.as_deref() == field
                )
        })
    };
    assert!(has_provider(
        "bad-type",
        None,
        ConfigWarningKind::InvalidValue
    ));
    assert!(has_provider(
        "typo",
        Some("timeout_seconds"),
        ConfigWarningKind::UnknownField
    ));
    assert!(has_provider(
        "commandless",
        Some("command"),
        ConfigWarningKind::InvalidValue
    ));
    assert!(has_provider(
        "short-ttl",
        Some("token_ttl_secs"),
        ConfigWarningKind::InvalidValue
    ));
    assert!(has_provider(
        "zero-timeout",
        Some("timeout_secs"),
        ConfigWarningKind::InvalidValue
    ));
    assert!(has_provider(
        "slow",
        Some("timeout_secs"),
        ConfigWarningKind::InvalidValue
    ));
    let provider_reason = |name: &str| {
        cfg.config_warnings
            .iter()
            .find(|w| {
                matches!(&w.target, WarningTarget::AuthProvider { name: n, field: f }
                    if n == name && f.as_deref() == Some("timeout_secs"))
            })
            .map(|w| w.reason.as_str())
            .unwrap_or_default()
            .to_owned()
    };
    assert!(provider_reason("zero-timeout").contains("clamped to 1"));
    assert!(provider_reason("slow").contains("clamped to 600"));
    assert!(
        cfg.config_warnings.iter().any(|w| {
            w.kind == ConfigWarningKind::InvalidValue
                && matches!(
                    &w.target,
                    WarningTarget::Model { field, .. } if field.as_deref() == Some("auth_provider")
                )
        }),
        "undefined reference warns at parse time: {:?}",
        cfg.config_warnings
    );
    let raw_config: toml::Value = toml::from_str(r#"auth_provider = "oops""#).unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config)
        .expect("a non-table auth_provider must not fail the config");
    assert!(cfg.auth_providers.is_empty());
    assert!(
        cfg.config_warnings.iter().any(|w| {
            matches!(w.target, WarningTarget::AuthProviderSection)
                && w.kind == ConfigWarningKind::NotATable
        }),
        "non-table section warns: {:?}",
        cfg.config_warnings
    );
}
#[test]
fn shell_environment_policy_typo_does_not_fail_config() {
    let cfg: toml::Value = toml::from_str(
        r#"
            [shell_environment_policy]
            inhert = "core"
            exclude = 123
            "#,
    )
    .unwrap();
    Config::new_from_toml_cfg(&cfg).expect("a policy typo must not fail the config");
}
#[test]
fn shell_environment_policy_known_keys_track_the_policy_struct() {
    let pi_tools::util::ShellEnvironmentPolicy {
        inherit: _,
        ignore_default_excludes: _,
        exclude: _,
        set: _,
        include_only: _,
    } = pi_tools::util::ShellEnvironmentPolicy::default();
    let ShellEnvironmentPolicyKnownKeys {
        inherit: _,
        ignore_default_excludes: _,
        exclude: _,
        set: _,
        include_only: _,
    } = ShellEnvironmentPolicyKnownKeys::default();
}
#[test]
fn env_keys_deser_string_or_array() {
    let one: EnvKeys = serde_json::from_str(r#""ANTHROPIC_AUTH_TOKEN""#).unwrap();
    assert_eq!(one.names(), vec!["ANTHROPIC_AUTH_TOKEN"]);
    let many: EnvKeys =
        serde_json::from_str(r#"["ANTHROPIC_AUTH_TOKEN", "LC_ANTHROPIC_AUTH_TOKEN"]"#).unwrap();
    assert_eq!(
        many.names(),
        vec!["ANTHROPIC_AUTH_TOKEN", "LC_ANTHROPIC_AUTH_TOKEN"]
    );
    let ser = serde_json::to_value(&one).unwrap();
    assert_eq!(ser, serde_json::json!("ANTHROPIC_AUTH_TOKEN"));
    let ser_many = serde_json::to_value(&many).unwrap();
    assert_eq!(
        ser_many,
        serde_json::json!(["ANTHROPIC_AUTH_TOKEN", "LC_ANTHROPIC_AUTH_TOKEN"])
    );
}
#[test]
fn env_keys_single_and_array_are_semantically_equal() {
    let from_array: EnvKeys = serde_json::from_str(r#"["X"]"#).unwrap();
    assert_eq!(EnvKeys::new(["X"]), from_array);
    let from_string: EnvKeys = serde_json::from_str(r#""X""#).unwrap();
    assert_eq!(EnvKeys::new(["X"]), from_string);
}
#[test]
fn default_auto_compact_threshold_is_none() {
    let cfg = Config::default();
    assert_eq!(cfg.session.auto_compact_threshold_percent, None);
}
#[test]
fn parses_auto_compact_threshold_percent() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [session]
            auto_compact_threshold_percent = 75
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert_eq!(cfg.session.auto_compact_threshold_percent, Some(75));
}
#[test]
fn auto_compact_threshold_percent_defaults_when_not_specified() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [toolset.bash]
            timeout_secs = 123
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert_eq!(cfg.session.auto_compact_threshold_percent, None);
}
#[test]
fn parses_repo_changes_dedup_config() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [repo_changes_dedup]
            enabled = false
            include_inline_fallback = true
            max_inline_bytes = 1024
            dedup_untracked = false
            dedup_binary = false
            untracked_max_bytes = 2048
            untracked_exclude_globs = ["*.zip", "tmp/**"]
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    let dedup = cfg.repo_changes_dedup;
    assert!(!dedup.enabled);
    assert!(dedup.include_inline_fallback);
    assert_eq!(dedup.max_inline_bytes, 1024);
    assert!(!dedup.dedup_untracked);
    assert!(!dedup.dedup_binary);
    assert_eq!(dedup.untracked_max_bytes, 2048);
    assert_eq!(dedup.untracked_exclude_globs, vec!["*.zip", "tmp/**"]);
}
#[test]
fn model_info_from_config_propagates_use_concise() {
    let entry = ModelEntryConfig {
        id: None,
        model_family: None,
        model: "test".to_string(),
        base_url: "https://test.api/v1".to_string(),
        name: None,
        description: None,
        max_completion_tokens: None,
        temperature: None,
        top_p: None,
        api_key: None,
        env_key: None,
        api_backend: ApiBackend::default(),
        auth_scheme: None,
        extra_headers: IndexMap::new(),
        context_window: NonZeroU64::new(200_000).unwrap(),
        auto_compact_threshold_percent: None,
        system_prompt_label: None,
        api_base_url: None,
        use_concise: true,
        agent_type: default_agent_type(),
        inference_idle_timeout_secs: None,
        max_retries: None,
        subagent_rate_limit_max_attempts: None,
        hidden: false,
        supported_in_api: true,
        reasoning_effort: None,
        supports_reasoning_effort: false,
        reasoning_efforts: Vec::new(),
        supports_backend_search: false,
        compactions_remaining: None,
        compaction_at_tokens: None,
        show_model_fingerprint: false,
        stream_tool_calls: None,
        laziness_detector: LazinessDetectorPerModelConfig::default(),
    };
    let info = ModelInfo::from_config(&entry);
    assert!(info.use_concise);
}
#[test]
fn agent_selection_config_defaults_to_none() {
    let cfg = Config::default();
    assert!(cfg.agent.name.is_none());
    assert!(cfg.agent.definition.is_none());
}
#[test]
fn parses_agent_selection_name() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [agent]
            name = "my-custom-agent"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert_eq!(cfg.agent.name.as_deref(), Some("my-custom-agent"));
    assert!(cfg.agent.definition.is_none());
}
#[test]
fn parses_agent_selection_definition_path() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [agent]
            definition = "/path/to/my-agent.md"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert!(cfg.agent.name.is_none());
    assert_eq!(
        cfg.agent.definition.as_deref(),
        Some(std::path::Path::new("/path/to/my-agent.md"))
    );
}
#[test]
fn parses_agent_selection_both_name_and_definition() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [agent]
            name = "fallback-agent"
            definition = "/path/to/primary-agent.md"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert_eq!(cfg.agent.name.as_deref(), Some("fallback-agent"));
    assert_eq!(
        cfg.agent.definition.as_deref(),
        Some(std::path::Path::new("/path/to/primary-agent.md"))
    );
}
#[test]
fn agent_selection_not_specified_uses_defaults() {
    let raw_config: toml::Value = toml::from_str(
        r#"
            [toolset.bash]
            timeout_secs = 123
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
    assert!(cfg.agent.name.is_none());
    assert!(cfg.agent.definition.is_none());
}
#[test]
fn model_info_from_config_propagates_agent_type() {
    let entry = ModelEntryConfig {
        id: None,
        model_family: None,
        model: "test".to_string(),
        base_url: "https://test.api/v1".to_string(),
        name: None,
        description: None,
        max_completion_tokens: None,
        temperature: None,
        top_p: None,
        api_key: None,
        env_key: None,
        api_backend: ApiBackend::default(),
        auth_scheme: None,
        extra_headers: IndexMap::new(),
        context_window: NonZeroU64::new(200_000).unwrap(),
        auto_compact_threshold_percent: None,
        system_prompt_label: None,
        api_base_url: None,
        use_concise: false,
        agent_type: "codex".to_string(),
        inference_idle_timeout_secs: None,
        max_retries: None,
        subagent_rate_limit_max_attempts: None,
        hidden: false,
        supported_in_api: true,
        reasoning_effort: None,
        supports_reasoning_effort: false,
        reasoning_efforts: Vec::new(),
        supports_backend_search: false,
        compactions_remaining: None,
        compaction_at_tokens: None,
        show_model_fingerprint: false,
        stream_tool_calls: None,
        laziness_detector: LazinessDetectorPerModelConfig::default(),
    };
    let info = ModelInfo::from_config(&entry);
    assert_eq!(info.agent_type, "codex");
}
#[test]
fn inference_idle_timeout_propagates_to_model_info() {
    let entry = ModelEntryConfig {
        id: None,
        model_family: None,
        model: "test".to_string(),
        base_url: "https://test.api/v1".to_string(),
        name: None,
        description: None,
        max_completion_tokens: None,
        temperature: None,
        top_p: None,
        api_key: None,
        env_key: None,
        api_backend: ApiBackend::default(),
        auth_scheme: None,
        extra_headers: IndexMap::new(),
        context_window: NonZeroU64::new(200_000).unwrap(),
        auto_compact_threshold_percent: None,
        system_prompt_label: None,
        api_base_url: None,
        use_concise: false,
        agent_type: default_agent_type(),
        inference_idle_timeout_secs: Some(120),
        max_retries: None,
        subagent_rate_limit_max_attempts: None,
        hidden: false,
        supported_in_api: true,
        reasoning_effort: None,
        supports_reasoning_effort: false,
        reasoning_efforts: Vec::new(),
        supports_backend_search: false,
        compactions_remaining: None,
        compaction_at_tokens: None,
        show_model_fingerprint: false,
        stream_tool_calls: None,
        laziness_detector: LazinessDetectorPerModelConfig::default(),
    };
    let info = ModelInfo::from_config(&entry);
    assert_eq!(info.inference_idle_timeout_secs, Some(120));
}
#[test]
fn telemetry_config_parses_custom_values_from_toml() {
    let raw: toml::Value = toml::from_str(
        r#"
            [telemetry]
            events_url     = "https://custom.example.com/events"
            events_api_key = "custom-key"
            mixpanel_token = "custom-token"
            mixpanel_enabled = false
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("should parse");
    assert_eq!(
        cfg.telemetry.events_url.as_deref(),
        Some("https://custom.example.com/events")
    );
    assert_eq!(cfg.telemetry.events_api_key.as_deref(), Some("custom-key"));
    assert_eq!(
        cfg.telemetry.mixpanel_token.as_deref(),
        Some("custom-token")
    );
    assert!(!cfg.telemetry.mixpanel_enabled);
}
/// Empty/whitespace values must become `None`, not reach the HTTP client as empty strings.
#[test]
fn telemetry_empty_string_disables_sink() {
    let raw: toml::Value = toml::from_str(
        r#"
            [telemetry]
            events_url     = ""
            events_api_key = "  "
            mixpanel_token = "\t"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("should parse");
    assert!(cfg.telemetry.events_url.is_none());
    assert!(cfg.telemetry.events_api_key.is_none());
    assert!(cfg.telemetry.mixpanel_token.is_none());
}
#[test]
fn telemetry_partial_override_retains_defaults() {
    let raw: toml::Value = toml::from_str(
        r#"
            [telemetry]
            events_url = "https://my-proxy/events"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("should parse");
    assert_eq!(
        cfg.telemetry.events_url.as_deref(),
        Some("https://my-proxy/events")
    );
    let defaults = TelemetryConfig::default();
    assert_eq!(cfg.telemetry.events_api_key, defaults.events_api_key);
    assert_eq!(cfg.telemetry.mixpanel_token, defaults.mixpanel_token);
    assert_eq!(cfg.telemetry.mixpanel_enabled, defaults.mixpanel_enabled);
}
#[test]
fn auth_alias_maps_to_grok_com_config() {
    let raw: toml::Value = toml::from_str(
        r#"
            [auth.oidc]
            issuer = "https://example.okta.com"
            client_id = "test-id"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    let oidc = cfg.grok_com_config.oidc.expect("oidc should be set");
    assert_eq!(oidc.issuer, "https://example.okta.com");
    assert_eq!(oidc.client_id, "test-id");
}
#[test]
fn grok_com_config_still_works() {
    let raw: toml::Value = toml::from_str(
        r#"
            [grok_com_config.oidc]
            issuer = "https://example.okta.com"
            client_id = "test-id"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    let oidc = cfg.grok_com_config.oidc.expect("oidc should be set");
    assert_eq!(oidc.issuer, "https://example.okta.com");
}
/// `disable_api_key_auth` plumbs through the `[auth]` alias, and absent
/// means None (opt-in knob, zero impact by default).
#[test]
fn disable_api_key_auth_parses_from_auth_alias() {
    let absent = Config::new_from_toml_cfg(&toml::from_str("").unwrap()).unwrap();
    assert_eq!(absent.grok_com_config.disable_api_key_auth, None);
    let raw: toml::Value = toml::from_str(
        r#"
            [auth]
            disable_api_key_auth = true
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(cfg.grok_com_config.disable_api_key_auth, Some(true));
}
/// `force_login_team_uuid` parses a string (pin), array (any-of), or `[]`
/// (fail closed); absent => None.
#[test]
fn force_login_team_uuid_parses_string_and_array() {
    use crate::auth::ForceLoginTeam;
    let _g = crate::env::EnvVarGuard::remove("GROK_FORCE_LOGIN_TEAM_ID");
    assert!(
        crate::auth::force_login_team_from_requirements().is_none(),
        "clear the force_login_team_uuid pin in requirements.toml to run this test",
    );
    let absent = Config::new_from_toml_cfg(&toml::from_str("").unwrap()).unwrap();
    assert_eq!(absent.grok_com_config.force_login_team_uuid, None);
    let raw: toml::Value = toml::from_str(
        r#"
            [auth]
            force_login_team_uuid = "team-abc"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(
        cfg.grok_com_config.force_login_team_uuid,
        Some(ForceLoginTeam::Single("team-abc".into())),
    );
    let raw: toml::Value = toml::from_str(
        r#"
            [grok_com_config]
            force_login_team_uuid = ["team-a", "team-b"]
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(
        cfg.grok_com_config.force_login_team_uuid,
        Some(ForceLoginTeam::AnyOf(vec![
            "team-a".into(),
            "team-b".into()
        ])),
    );
    let raw: toml::Value = toml::from_str(
        r#"
            [auth]
            force_login_team_uuid = []
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(
        cfg.grok_com_config.force_login_team_uuid,
        Some(ForceLoginTeam::AnyOf(vec![])),
    );
}
/// The env override applies when config is empty and wins over a user/managed
/// `config.toml` value (requirements clamping is covered in `auth::config`).
#[test]
fn force_login_team_id_env_overrides_user_config() {
    use crate::auth::ForceLoginTeam;
    let _guard = crate::env::EnvVarGuard::set("GROK_FORCE_LOGIN_TEAM_ID", "env-team");
    assert!(
        crate::auth::force_login_team_from_requirements().is_none(),
        "clear the force_login_team_uuid pin in requirements.toml to run this test",
    );
    let from_env = Config::new_from_toml_cfg(&toml::from_str("").unwrap()).unwrap();
    assert_eq!(
        from_env.grok_com_config.force_login_team_uuid,
        Some(ForceLoginTeam::Single("env-team".into())),
    );
    let raw: toml::Value = toml::from_str(
        r#"
            [grok_com_config]
            force_login_team_uuid = "admin-team"
            "#,
    )
    .unwrap();
    let overridden = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(
        overridden.grok_com_config.force_login_team_uuid,
        Some(ForceLoginTeam::Single("env-team".into())),
    );
}
/// Env unset: `force_login_team_uuid` is taken from `config.toml` unchanged (the
/// env override tier never clobbers the merged config value).
#[test]
fn force_login_team_id_env_unset_keeps_config_value() {
    use crate::auth::ForceLoginTeam;
    let _guard = crate::env::EnvVarGuard::remove("GROK_FORCE_LOGIN_TEAM_ID");
    assert!(
        crate::auth::force_login_team_from_requirements().is_none(),
        "clear the force_login_team_uuid pin in requirements.toml to run this test",
    );
    let raw: toml::Value = toml::from_str(
        r#"
            [grok_com_config]
            force_login_team_uuid = "admin-team"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(
        cfg.grok_com_config.force_login_team_uuid,
        Some(ForceLoginTeam::Single("admin-team".into())),
    );
}
/// Pinning a team via `force_login_team_uuid` implies API-key auth is
/// disabled even without an explicit `disable_api_key_auth` (team
/// membership can't be verified from a bare API key, so it needs IdP login).
#[test]
fn force_login_team_uuid_implies_api_key_auth_disabled() {
    use crate::auth::{ForceLoginTeam, GrokComConfig};
    let base = GrokComConfig {
        disable_api_key_auth: None,
        force_login_team_uuid: None,
        ..GrokComConfig::default()
    };
    assert!(!base.api_key_auth_disabled());
    assert!(
        GrokComConfig {
            disable_api_key_auth: Some(true),
            ..base.clone()
        }
        .api_key_auth_disabled()
    );
    assert!(
        GrokComConfig {
            force_login_team_uuid: Some(ForceLoginTeam::Single("team-x".into())),
            ..base
        }
        .api_key_auth_disabled()
    );
}
#[test]
fn parsed_config_has_models_config() {
    let raw: toml::Value = toml::from_str(
        r#"
            [models]
            default = "my-enterprise-model"
            web_search = "enterprise-search"
            session_summary = "title-model"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(cfg.models.default.as_deref(), Some("my-enterprise-model"));
    assert_eq!(cfg.models.web_search.as_deref(), Some("enterprise-search"));
    assert_eq!(cfg.models.session_summary.as_deref(), Some("title-model"));
}
#[test]
fn config_models_default_is_not_overwritten_by_default_models_json() {
    let config_default = Some("custom-byok-model");
    let remote_settings_default = Some("remote-settings-model");
    let resolved = resolve_string_flag(
        None,
        "GROK_DEFAULT_MODEL_TEST_NONEXISTENT",
        config_default,
        remote_settings_default,
    );
    let resolved = resolved.expect("should resolve to a value");
    assert_eq!(resolved.value, "custom-byok-model");
    assert_eq!(
        resolved.source,
        ConfigSource::Config,
        "[models] default from config.toml must beat remote settings and compiled-in defaults"
    );
}
/// Unset every env var that `EndpointsConfig::default()` reads for endpoints,
/// so the cli-chat-proxy resolver tests below are deterministic regardless of
/// the ambient environment. Gated behind `#[serial]`.
fn unset_endpoint_env_vars() {
    for k in [
        "GROK_CLI_CHAT_PROXY_BASE_URL",
        "GROK_PI_API_BASE_URL",
        "GROK_FEEDBACK_BASE_URL",
        "GROK_TRACE_UPLOAD_URL",
        "GROK_MANAGED_CONFIG_URL",
        "GROK_MODELS_BASE_URL",
        "GROK_MODELS_LIST_URL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "GROK_INTERNAL_OTLP_TRACES_ENDPOINT",
        "GROK_INTERNAL_OTLP_HEADERS",
        "GROK_EXTERNAL_OTEL",
    ] {
        unsafe { std::env::remove_var(k) };
    }
}
/// REGRESSION: the managed-config URL never follows `pi_api_base_url`
/// through the full loader `Config::new_from_toml_cfg` — a distinct construction
/// path from `from_config_value`, so the deployment key never reaches the
/// inference host on either.
#[test]
#[serial]
fn loader_managed_config_url_never_follows_inference_endpoint() {
    unset_endpoint_env_vars();
    let cfg = Config::new_from_toml_cfg(
        &toml::from_str(
            r#"[endpoints]
                pi_api_base_url = "https://inference.acme-corp.example/pi/v1""#,
        )
        .unwrap(),
    )
    .expect("config should parse");
    assert!(cfg.endpoints.cli_chat_proxy_base_url.is_none());
    assert_eq!(
        cfg.endpoints.resolve_managed_config_url(),
        format!("{CLI_CHAT_PROXY_BASE_URL_DEFAULT}/deployment/config")
    );
    assert!(
        !cfg.endpoints
            .resolve_managed_config_url()
            .contains("inference.acme-corp.example"),
        "deployment key would be sent to the inference host"
    );
}
#[test]
fn e2e_models_endpoint_serde_alias_parses_as_models_list_url() {
    let raw: toml::Value = toml::from_str(
        r#"
            [endpoints]
            models_endpoint = "https://old-style.acme.com/v1/models"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(
        cfg.endpoints.models_list_url.as_deref(),
        Some("https://old-style.acme.com/v1/models"),
        "models_endpoint alias should parse into models_list_url"
    );
    assert!(cfg.endpoints.has_custom_endpoint());
}
#[test]
fn e2e_config_models_parsed_directly_not_via_deep_merge() {
    let raw: toml::Value = toml::from_str(
        r#"
            [model.custom-model]
            model = "my-custom-llm"
            api_key = "custom-key"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert!(cfg.config_models.contains_key("custom-model"));
    let model_override = cfg.config_models.get("custom-model").unwrap();
    assert_eq!(model_override.model.as_deref(), Some("my-custom-llm"));
    assert_eq!(model_override.api_key.as_deref(), Some("custom-key"));
    assert!(
        model_override.base_url.is_none(),
        "base_url should be None when user didn't set it"
    );
}
/// The tamper-resistance `25-enterprise.md` sells to administrators, for
/// every key an administrator can pin.
#[test]
#[serial]
fn requirement_pin_outranks_a_hostile_environment() {
    for spec in FEATURES {
        let pinned = !spec.default_enabled;
        let _env = EnvGuard::set(spec.env, if pinned { "0" } else { "1" });
        let mut cfg = Config::default();
        cfg.requirements
            .pin_feature(spec.id, pinned, crate::config::RequirementSource::Unknown);
        let r = cfg.feature(spec.id);
        assert_eq!(r.value, pinned, "{} lost to {}", spec.key, spec.env);
        assert_eq!(r.source, ConfigSource::Requirement, "{}", spec.key);
    }
}
/// A registered key is a `&'static str` matched against the `[features]`
/// table, not a serde field name, so every one of them is read back here.
#[test]
#[serial]
fn every_registered_key_parses_out_of_the_features_table() {
    for spec in FEATURES {
        let configured = !spec.default_enabled;
        let raw: toml::Value =
            toml::from_str(&format!("[features]\n{} = {configured}\n", spec.key)).unwrap();
        let cfg = Config::new_from_toml_cfg(&raw).unwrap();
        {
            let _env = EnvGuard::unset(spec.env);
            let r = cfg.feature(spec.id);
            assert_eq!(
                r.value, configured,
                "{} never reached the registry",
                spec.key
            );
            assert_eq!(r.source, ConfigSource::Config, "{}", spec.key);
        }
        let _env = EnvGuard::set(spec.env, if configured { "0" } else { "1" });
        let r = cfg.feature(spec.id);
        assert_eq!(r.value, !configured, "config.toml outranked {}", spec.env);
        assert_eq!(r.source, ConfigSource::Env, "{}", spec.key);
    }
}
/// The keys the list names are read from the raw layers, so each must be one no
/// field claims. A quoted `remote_fetch` used to read as absent and leave the
/// egress gate open.
#[test]
fn non_boolean_value_fails_the_load_for_a_key_with_no_field() {
    let features = include_str!("config.rs")
        .split_once("pub struct Features {")
        .and_then(|(_, rest)| rest.split_once("\n}\n"))
        .map(|(body, _)| body)
        .expect("`Features` moved; this test needs its new shape");
    for key in UNMIRRORED_BOOLEAN_FEATURES {
        assert!(
            !FEATURES.iter().any(|spec| spec.key == *key),
            "{key} is a registry row, which is already known"
        );
        assert!(
            !features.contains(&format!("pub {key}:")),
            "{key} has a `Features` field, so it is read through that instead"
        );
        let raw: toml::Value = toml::from_str(&format!("[features]\n{key} = \"false\"\n")).unwrap();
        let err = Config::new_from_toml_cfg(&raw)
            .expect_err(&format!("{key}: a quoted value must not read as absent"));
        assert!(
            err.contains(key) && err.contains("true or false"),
            "{key}: the error names the key and the spelling that works: {err}"
        );
    }
}
/// What no list could cover: a key this build has never heard of is typed all
/// the same, so the next boolean added to `[features]` is checked before anyone
/// writes it down. `image_edit` rides the same path, which is why it is kept out
/// of the list that only suppresses the unrecognized-key warning.
#[test]
fn non_boolean_value_fails_the_load_for_an_unregistered_key() {
    for key in ["image_edit", "a_key_no_build_has_ever_had"] {
        let raw: toml::Value = toml::from_str(&format!("[features]\n{key} = \"false\"\n")).unwrap();
        let err = Config::new_from_toml_cfg(&raw)
            .expect_err(&format!("{key}: a quoted value must not read as absent"));
        assert!(
            err.contains(key) && err.contains("true or false"),
            "{key}: the error names the key and the spelling that works: {err}"
        );
    }
}
/// The other half of the rule. A later release can add a `[features]` key that
/// holds something other than a boolean, and the build that predates its field
/// has to start on that config rather than strand a fleet mid-rollout.
#[test]
fn a_value_that_reads_as_nothing_like_a_boolean_still_loads() {
    for value in ["\"aggressive\"", "42", "[\"a\", \"b\"]", "1.5"] {
        let entry = format!("[features]\na_later_release_key = {value}\n");
        let raw: toml::Value = toml::from_str(&entry).unwrap();
        Config::new_from_toml_cfg(&raw)
            .unwrap_or_else(|e| panic!("{value} must not stop an older build: {e}"));
        let unused = unused_keys_from_toml(&entry);
        assert!(
            unused
                .iter()
                .any(|key| key == "features.a_later_release_key"),
            "{value}: ignored, and the operator still hears about it: {unused:?}"
        );
    }
}
/// The non-row keys that do have a field are turned away by serde, whatever it
/// words the failure as.
#[test]
fn non_boolean_value_fails_the_load_for_a_key_with_a_field() {
    for key in ["title_refresh", "image_gen", "video_gen"] {
        let raw: toml::Value = toml::from_str(&format!("[features]\n{key} = \"false\"\n")).unwrap();
        Config::new_from_toml_cfg(&raw)
            .expect_err(&format!("{key}: a quoted value must not read as absent"));
    }
}
#[test]
fn non_boolean_feature_value_fails_the_load() {
    let raw: toml::Value = toml::from_str("[features]\nsession_search = \"no\"\n").unwrap();
    let err = Config::new_from_toml_cfg(&raw)
        .expect_err("a quoted value must not read as off and leave the index on");
    assert!(
        err.contains("features.session_search") && err.contains("true or false"),
        "the error names the key and the spelling that works: {err}"
    );
}
#[test]
fn goal_keys_round_trip_from_toml() {
    let raw: toml::Value = toml::from_str(
        r#"
[goal]
enabled = true
classifier_enabled = true
planner_enabled = false
summary_enabled = true
verifier_count = 4
classifier_max_runs = 7
strategist_every = 3
reverify_after = 6
"#,
    )
    .expect("test TOML should parse");
    let cfg = Config::new_from_toml_cfg(&raw).expect("config should parse");
    assert_eq!(cfg.goal.enabled, Some(true));
    assert_eq!(cfg.goal.classifier_enabled, Some(true));
    assert_eq!(cfg.goal.planner_enabled, Some(false));
    assert_eq!(cfg.goal.summary_enabled, Some(true));
    assert_eq!(cfg.goal.verifier_count, Some(4));
    assert_eq!(cfg.goal.classifier_max_runs, Some(7));
    assert_eq!(cfg.goal.strategist_every, Some(3));
    assert_eq!(cfg.goal.reverify_after, Some(6));
    let empty = Config::new_from_toml_cfg(&toml::from_str("").unwrap()).unwrap();
    assert_eq!(empty.goal.classifier_enabled, None);
    assert_eq!(empty.goal.verifier_count, None);
}
/// A malformed pin must drop to `None`, not fail the whole parse (which
/// would silently wipe every other setting).
#[test]
fn goal_model_pin_malformed_is_dropped_not_fatal() {
    let toml_str = r#"
[goal]
enabled = true
classifier_max_runs = 6
planner_model = { agent_type = "grok-build-plan" }
"#;
    let raw: toml::Value = toml::from_str(toml_str).unwrap();
    let cfg = Config::new_from_toml_cfg(&raw)
        .expect("malformed planner_model must not fail the whole parse");
    assert!(cfg.goal.planner_model.is_none());
    assert_eq!(cfg.goal.classifier_max_runs, Some(6));
}
#[test]
fn goal_skeptic_models_drop_malformed_entry_keep_rest() {
    let toml_str = r#"
[goal]
enabled = true

[[goal.skeptic_models]]
model = "grok-build"
agent_type = "grok-build-plan"

[[goal.skeptic_models]]
agent_type = "cursor"

[[goal.skeptic_models]]
model = "test-model-fast"
agent_type = "cursor"
"#;
    let raw: toml::Value = toml::from_str(toml_str).unwrap();
    let cfg = Config::new_from_toml_cfg(&raw).unwrap();
    assert_eq!(cfg.goal.skeptic_models.len(), 2);
    assert_eq!(cfg.goal.skeptic_models[0].model, "grok-build");
    assert_eq!(cfg.goal.skeptic_models[1].model, "test-model-fast");
}
/// Run the production scan (`deserialize_collecting_unrecognized`) on a
/// TOML string, mirroring the [model] removal + default-merge in
/// `new_from_toml_cfg`.
fn unused_keys_from_toml(toml_str: &str) -> Vec<String> {
    let raw: toml::Value = toml::from_str(toml_str).unwrap();
    let raw_without_models = {
        let mut r = raw.clone();
        if let toml::Value::Table(ref mut t) = r {
            t.remove("model");
        }
        r
    };
    let mut base = toml::Value::try_from(Config::default()).unwrap();
    if let toml::Value::Table(ref mut t) = base {
        t.remove("model");
    }
    crate::config::deep_merge_toml(&mut base, &raw_without_models);
    let (_config, unused) = Config::deserialize_collecting_unrecognized(base, &raw_without_models)
        .expect("config should deserialize");
    unused
}
#[test]
fn config_warns_on_section_typo() {
    let raw: toml::Value = toml::from_str(
        r#"
            [endpoint]
            deployment_key = "pi-token-test"
        "#,
    )
    .unwrap();
    let config = Config::new_from_toml_cfg(&raw).expect("should parse");
    assert!(config.endpoints.deployment_key.is_none());
    let unused = unused_keys_from_toml(
        r#"
            [endpoint]
            deployment_key = "pi-token-test"
        "#,
    );
    assert!(unused.iter().any(|k| k == "endpoint"), "got: {unused:?}");
}
#[test]
fn known_non_serde_config_paths_are_not_reported_unused() {
    let unused = unused_keys_from_toml(
        r#"
            [features]
            remote_fetch = false
            session_search = false
            image_edit = true
            not_a_real_feature = true
            [slash_command_tags]
            workflows = "new"
        "#,
    );
    assert!(
        !unused.iter().any(|k| k == "features.remote_fetch"),
        "features.remote_fetch must not be treated as a typo: {unused:?}"
    );
    assert!(
        !unused.iter().any(|k| k == "features.session_search"),
        "a registered feature has no typed field and must not look like a typo: {unused:?}"
    );
    assert!(
        !unused.iter().any(|k| k == "slash_command_tags"),
        "slash_command_tags is a real table: {unused:?}"
    );
    assert!(
        unused.iter().any(|k| k == "features.image_edit"),
        "only a pin sets image_edit, so a config entry stays unrecognized: {unused:?}"
    );
    assert!(
        unused.iter().any(|k| k == "features.not_a_real_feature"),
        "real typos still surface: {unused:?}"
    );
}
/// `[toolset.web_search]` used to configure the in-process web-search tool of the
/// removed Rust agent runtime. The section no longer has a typed field, so a stale
/// entry must surface as unrecognized instead of being silently ignored.
#[test]
fn removed_web_search_section_is_reported_unused() {
    let unused = unused_keys_from_toml(
        r#"
            [toolset.web_search]
            allowed_domains = ["docs.x.ai"]
        "#,
    );
    assert!(
        unused.iter().any(|k| k.starts_with("toolset.web_search")),
        "a stale [toolset.web_search] section must be reported as unrecognized: {unused:?}"
    );
}
#[test]
fn config_warns_on_field_typos() {
    let unused = unused_keys_from_toml(
        r#"
            [endpoints]
            deplomyent_key = "test"
            [ui]
            yoloo = true
            [features]
            telmetry = true
        "#,
    );
    assert!(
        unused.iter().any(|k| k == "endpoints.deplomyent_key"),
        "got: {unused:?}"
    );
    assert!(unused.iter().any(|k| k == "ui.yoloo"), "got: {unused:?}");
    assert!(
        unused.iter().any(|k| k == "features.telmetry"),
        "got: {unused:?}"
    );
}
#[test]
fn config_accepts_all_known_sections() {
    let unused = unused_keys_from_toml(
        r#"
            disabled_mcp_servers = ["old-server"]
            [cli]
            auto_update = false
            [features]
            feedback = true
            [endpoints]
            deployment_key = "test"
            management_api_key = "mgmt-key"
            gcs_service_account_key = "gcs-key"
            [models]
            default = "grok-3"
            [ui]
            yolo = true
            theme = "dark"
            approval_mode = "ask"
            [session]
            auto_compact_threshold_percent = 85
            [telemetry]
            enabled = true
            trace_upload = true
            [agent]
            name = "custom"
            [skills]
            paths = ["~/skills"]
            [plugins]
            paths = ["~/plugins"]
            [subagents]
            enabled = true
            [memory]
            enabled = true
            [compaction]
            [compaction.pruning]
            enabled = true
            [harness]
            block_for_upload = true
            [feedback.user]
            name = ["os_user"]
            email = ["git_email", "team@example.com"]
            email_domain = "example.com"
            command = "/opt/bin/grok-identity"
            [repo_changes_dedup]
            enabled = false
            [relay]
            enabled = false
            [worktree_pool]
            pool_size = 4
            [managed_mcps]
            enabled = true
            [mcp_servers.test]
            url = "https://mcp.test.com"
            [toolset.bash]
            timeout_secs = 120
            login_shell_capture = true
            [grok_com_config]
            token_header = "test"
            [auth.oidc]
            issuer = "https://sso.corp.com"
            client_id = "abc123"
            [storage]
            cleanup_ttl_days = 7
            [[marketplace.sources]]
            name = "Local Dev"
            path = "/tmp/plugins"
            [permission]
            [[permission.rules]]
            action = "allow"
            tool = "bash"
            [tools]
            respect_gitignore = false
            [desktop]
            some_key = "value"
        "#,
    );
    assert!(
        unused.is_empty(),
        "false positive on valid config: {unused:?}"
    );
}
#[test]
fn config_accepts_compact_permission_section() {
    let unused = unused_keys_from_toml(
        r#"
            [permission]
            allow = ["Read(//tmp/**)"]
            deny = ["Bash(rm *)"]
            ask = ["WebFetch"]
        "#,
    );
    assert!(
        unused.is_empty(),
        "false positive on [permission] keys: {unused:?}"
    );
}
/// `prompt_policy` is not consumed from any TOML permission section (the
/// verbose loader keeps only `rules`; prompt policy comes from .claude
/// settings `defaultMode`), so it must warn rather than be a silent no-op.
#[test]
fn permission_prompt_policy_warns_as_unconsumed() {
    let unused = unused_keys_from_toml(
        r#"
            [permission]
            deny = ["Bash(rm *)"]
            prompt_policy = "deny"
        "#,
    );
    assert_eq!(
        unused,
        vec!["permission.prompt_policy".to_string()],
        "an unconsumed key in a security section must be flagged"
    );
}
/// A typo'd `[permission]` sub-key must still warn — silently dropping a
/// misspelled security rule would leave the user believing it's in force.
#[test]
fn permission_unknown_subkey_still_warns() {
    let unused = unused_keys_from_toml(
        r#"
            [permission]
            denny = ["Bash(rm *)"]
            ask = ["WebFetch"]
        "#,
    );
    assert_eq!(
        unused,
        vec!["permission.denny".to_string()],
        "exactly the typo'd sub-key must be flagged"
    );
}
/// Permission *values* are opaque: a malformed `[[permission.rules]]`
/// entry neither warns nor fails Config load — the out-of-band loaders
/// parse it tolerantly and warn per item.
#[test]
fn malformed_permission_rules_do_not_fail_config_load() {
    let toml_str = r#"
            [[permission.rules]]
            pattern = 5
        "#;
    let raw: toml::Value = toml::from_str(toml_str).unwrap();
    Config::new_from_toml_cfg(&raw)
        .expect("malformed rule values are the permission loaders' concern");
    let unused = unused_keys_from_toml(toml_str);
    assert!(unused.is_empty(), "got: {unused:?}");
}
/// A non-table `[permission]` value still fails Config load (pre-existing
/// behavior): a fundamentally broken security section should be loud.
#[test]
fn non_table_permission_value_fails_config_load() {
    let raw: toml::Value = toml::from_str(r#"permission = "foo""#).unwrap();
    assert!(
        Config::new_from_toml_cfg(&raw).is_err(),
        "non-table [permission] must fail loudly"
    );
}
/// Wrong-typed values for the opaque passthrough keys must neither warn
/// nor fail config load — an admin typo in a managed layer must not brick
/// startup fleet-wide; the out-of-band consumers degrade gracefully.
#[test]
fn wrong_typed_passthrough_values_neither_warn_nor_fail() {
    let toml_str = r#"
            [marketplace]
            official_marketplace_auto_installed = "yes"
            default_skills_installs_purged = "yes"
        "#;
    let unused = unused_keys_from_toml(toml_str);
    assert!(unused.is_empty(), "got: {unused:?}");
    let raw: toml::Value = toml::from_str(toml_str).unwrap();
    Config::new_from_toml_cfg(&raw)
        .expect("wrong-typed passthrough values must not fail config load");
}
/// Exempting `[permission]` and friends must not swallow warnings for
/// genuinely unknown keys.
#[test]
fn unknown_key_still_warns_next_to_exempt_sections() {
    let unused = unused_keys_from_toml(
        r#"
            [permission]
            deny = ["Bash(rm *)"]
            [marketplace]
            official_marketplace_auto_installed = true
            default_skills_installs_purged = true
            [ui]
            yollo = true
        "#,
    );
    assert_eq!(
        unused,
        vec!["ui.yollo".to_string()],
        "exactly the typo'd key must be flagged"
    );
}
#[test]
fn otlp_traces_endpoint_precedence() {
    let proxy = "https://inference.acme.com/v1".to_string();
    let derived = EndpointsConfig {
        cli_chat_proxy_base_url: Some(proxy.clone()),
        ..Default::default()
    };
    assert_eq!(
        derived.resolve_otlp_traces_endpoint(),
        "https://inference.acme.com/v1/traces"
    );
    let base = EndpointsConfig {
        cli_chat_proxy_base_url: Some(proxy.clone()),
        otel_exporter_otlp_endpoint: Some("https://otel.acme.com".to_string()),
        ..Default::default()
    };
    assert_eq!(
        base.resolve_otlp_traces_endpoint(),
        "https://otel.acme.com/v1/traces"
    );
    let full = EndpointsConfig {
        cli_chat_proxy_base_url: Some(proxy),
        otel_exporter_otlp_endpoint: Some("https://ignored.example".to_string()),
        otel_exporter_otlp_traces_endpoint: Some("https://otel.acme.com/v1/traces".to_string()),
        ..Default::default()
    };
    assert_eq!(
        full.resolve_otlp_traces_endpoint(),
        "https://otel.acme.com/v1/traces"
    );
}
#[test]
fn otlp_headers_parse() {
    let cfg = EndpointsConfig {
        otel_exporter_otlp_headers: Some("a=1, b = 2 ,=skip,c=".to_string()),
        ..Default::default()
    };
    assert_eq!(
        cfg.resolve_otlp_headers(),
        vec![
            ("a".to_string(), "1".to_string()),
            ("b".to_string(), "2".to_string()),
            ("c".to_string(), String::new()),
        ]
    );
}
/// Base config for the internal-OTLP tests: pinned proxy, every OTLP knob
/// explicitly unset so ambient env (via `Default`) can't leak in.
fn internal_otlp_test_config() -> EndpointsConfig {
    EndpointsConfig {
        cli_chat_proxy_base_url: Some("https://proxy.example/v1".to_string()),
        otel_exporter_otlp_endpoint: None,
        otel_exporter_otlp_traces_endpoint: None,
        otel_exporter_otlp_headers: None,
        grok_internal_otlp_traces_endpoint: None,
        grok_internal_otlp_headers: None,
        external_otel_master_switch: false,
        ..Default::default()
    }
}
/// `grok_internal_otlp_traces_endpoint` wins over the legacy `OTEL_*`
/// fields regardless of the master switch.
#[test]
fn internal_otlp_endpoint_grok_internal_wins_regardless_of_switch() {
    for switch in [false, true] {
        let cfg = EndpointsConfig {
            grok_internal_otlp_traces_endpoint: Some(
                "https://internal.example/traces/".to_string(),
            ),
            otel_exporter_otlp_traces_endpoint: Some(
                "https://legacy.example/v1/traces".to_string(),
            ),
            otel_exporter_otlp_endpoint: Some("https://legacy-base.example".to_string()),
            external_otel_master_switch: switch,
            ..internal_otlp_test_config()
        };
        assert_eq!(
            cfg.resolve_otlp_traces_endpoint(),
            "https://internal.example/traces",
            "switch={switch}: GROK_INTERNAL_OTLP_TRACES_ENDPOINT must win verbatim (trailing / trimmed)"
        );
    }
}
/// Master switch unset → legacy fallback preserved (back-compat).
#[test]
fn internal_otlp_endpoint_legacy_fallback_when_switch_unset() {
    let traces = EndpointsConfig {
        otel_exporter_otlp_traces_endpoint: Some("https://legacy.example/v1/traces".to_string()),
        ..internal_otlp_test_config()
    };
    assert_eq!(
        traces.resolve_otlp_traces_endpoint(),
        "https://legacy.example/v1/traces"
    );
    let base = EndpointsConfig {
        otel_exporter_otlp_endpoint: Some("https://legacy-base.example/".to_string()),
        ..internal_otlp_test_config()
    };
    assert_eq!(
        base.resolve_otlp_traces_endpoint(),
        "https://legacy-base.example/v1/traces"
    );
}
/// Master switch SET → legacy `OTEL_*` endpoint/headers are completely
/// ignored by the internal pipeline (the external stream owns them); the
/// internal pipeline falls back to the proxy default and
/// `internal_otlp_consumed_standard_vars()` is false.
#[test]
fn internal_otlp_ignores_legacy_vars_when_switch_set() {
    let cfg = EndpointsConfig {
        otel_exporter_otlp_traces_endpoint: Some(
            "https://admin-collector.example/v1/traces".to_string(),
        ),
        otel_exporter_otlp_endpoint: Some("https://admin-collector.example".to_string()),
        otel_exporter_otlp_headers: Some("authorization=Bearer admin".to_string()),
        external_otel_master_switch: true,
        ..internal_otlp_test_config()
    };
    assert_eq!(
        cfg.resolve_otlp_traces_endpoint(),
        "https://proxy.example/v1/traces",
        "internal firehose must never follow OTEL_* to the external collector"
    );
    assert_eq!(cfg.resolve_otlp_headers(), Vec::<(String, String)>::new());
    assert!(!cfg.internal_otlp_consumed_standard_vars());
}
/// `internal_otlp_consumed_standard_vars()` truth table.
#[test]
fn internal_otlp_consumed_standard_vars_cases() {
    struct Case {
        switch: bool,
        legacy_traces_ep: bool,
        legacy_base_ep: bool,
        legacy_headers: bool,
        internal_ep: bool,
        internal_headers: bool,
        expected: bool,
        why: &'static str,
    }
    let unset = Case {
        switch: false,
        legacy_traces_ep: false,
        legacy_base_ep: false,
        legacy_headers: false,
        internal_ep: false,
        internal_headers: false,
        expected: false,
        why: "nothing set",
    };
    let cases = [
        Case { ..unset },
        Case {
            legacy_traces_ep: true,
            expected: true,
            why: "legacy traces endpoint consumed",
            ..unset
        },
        Case {
            legacy_base_ep: true,
            expected: true,
            why: "legacy base endpoint consumed",
            ..unset
        },
        Case {
            legacy_headers: true,
            expected: true,
            why: "legacy headers consumed",
            ..unset
        },
        Case {
            legacy_traces_ep: true,
            internal_ep: true,
            expected: false,
            why: "internal endpoint shadows legacy",
            ..unset
        },
        Case {
            legacy_headers: true,
            internal_headers: true,
            expected: false,
            why: "internal headers shadow legacy",
            ..unset
        },
        Case {
            legacy_traces_ep: true,
            legacy_headers: true,
            internal_ep: true,
            expected: true,
            why: "endpoint shadowed but legacy headers still consumed (headers half)",
            ..unset
        },
        Case {
            switch: true,
            legacy_traces_ep: true,
            legacy_base_ep: true,
            legacy_headers: true,
            expected: false,
            why: "switch set: legacy vars ignored",
            ..unset
        },
    ];
    for case in cases {
        let cfg = EndpointsConfig {
            external_otel_master_switch: case.switch,
            otel_exporter_otlp_traces_endpoint: case
                .legacy_traces_ep
                .then(|| "https://legacy.example/v1/traces".to_string()),
            otel_exporter_otlp_endpoint: case
                .legacy_base_ep
                .then(|| "https://legacy-base.example".to_string()),
            otel_exporter_otlp_headers: case.legacy_headers.then(|| "k=v".to_string()),
            grok_internal_otlp_traces_endpoint: case
                .internal_ep
                .then(|| "https://internal.example/traces".to_string()),
            grok_internal_otlp_headers: case.internal_headers.then(|| "ik=iv".to_string()),
            ..internal_otlp_test_config()
        };
        assert_eq!(
            cfg.internal_otlp_consumed_standard_vars(),
            case.expected,
            "case: {}",
            case.why
        );
    }
}
/// Headers precedence: `grok_internal_otlp_headers` wins; legacy
/// `otel_exporter_otlp_headers` only when the master switch is unset.
#[test]
fn internal_otlp_headers_precedence() {
    for switch in [false, true] {
        let cfg = EndpointsConfig {
            grok_internal_otlp_headers: Some("x-debug=1".to_string()),
            otel_exporter_otlp_headers: Some("legacy=1".to_string()),
            external_otel_master_switch: switch,
            ..internal_otlp_test_config()
        };
        assert_eq!(
            cfg.resolve_otlp_headers(),
            vec![("x-debug".to_string(), "1".to_string())],
            "switch={switch}"
        );
    }
    let legacy = EndpointsConfig {
        otel_exporter_otlp_headers: Some("legacy=1".to_string()),
        ..internal_otlp_test_config()
    };
    assert_eq!(
        legacy.resolve_otlp_headers(),
        vec![("legacy".to_string(), "1".to_string())]
    );
}
fn ext_env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> + use<> {
    let map: std::collections::HashMap<String, String> = pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    move |name: &str| map.get(name).cloned()
}
fn ext_client() -> pi_telemetry::external::config::ExternalClientInfo {
    pi_telemetry::external::config::ExternalClientInfo::default()
}
#[test]
fn external_otel_default_off_and_double_opt_in() {
    assert!(
        resolve_external_otel_config_with(None, None, ext_env(&[]), ext_client(), false).is_none()
    );
    assert!(
        resolve_external_otel_config_with(
            None,
            None,
            ext_env(&[("GROK_EXTERNAL_OTEL", "1")]),
            ext_client(),
            false,
        )
        .is_none()
    );
    assert!(
        resolve_external_otel_config_with(
            None,
            None,
            ext_env(&[
                ("GROK_EXTERNAL_OTEL", "1"),
                ("OTEL_METRICS_EXPORTER", "otlp"),
            ]),
            ext_client(),
            false,
        )
        .is_some()
    );
}
#[test]
fn external_otel_file_table_layered_under_env() {
    let effective: toml::Value = toml::from_str(
        r#"
            [telemetry]
            otel_enabled = true
            otel_logs_exporter = "otlp"
            otel_endpoint = "https://collector.corp.example:4318"
            otel_protocol = "grpc"
            "#,
    )
    .unwrap();
    let cfg = resolve_external_otel_config_with(
        Some(&effective),
        None,
        ext_env(&[]),
        ext_client(),
        false,
    )
    .expect("file table must activate");
    assert_eq!(cfg.logs_transport.as_protocol_str(), "grpc");
    assert_eq!(cfg.metrics_transport.as_protocol_str(), "grpc");
    assert_eq!(cfg.logs_endpoint, "https://collector.corp.example:4318");
    let cfg = resolve_external_otel_config_with(
        Some(&effective),
        None,
        ext_env(&[("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")]),
        ext_client(),
        false,
    )
    .expect("env protocol must override file protocol");
    assert_eq!(cfg.logs_transport.as_protocol_str(), "http/protobuf");
    assert_eq!(cfg.metrics_transport.as_protocol_str(), "http/protobuf");
    assert_eq!(
        cfg.logs_endpoint,
        "https://collector.corp.example:4318/v1/logs"
    );
    assert!(
        resolve_external_otel_config_with(
            Some(&effective),
            None,
            ext_env(&[("GROK_EXTERNAL_OTEL", "0")]),
            ext_client(),
            false,
        )
        .is_none()
    );
}
#[test]
fn external_otel_file_table_carries_mtls_paths() {
    let effective: toml::Value = toml::from_str(
        r#"
            [telemetry]
            otel_enabled = true
            otel_logs_exporter = "otlp"
            otel_metrics_exporter = "otlp"
            otel_endpoint = "https://collector.corp.example:4318"
            otel_protocol = "grpc"
            otel_certificate = "/etc/ssl/corp-ca.pem"
            otel_client_certificate = "/etc/ssl/client.crt"
            otel_client_key = "/etc/ssl/client.key"
            "#,
    )
    .unwrap();
    let cfg = resolve_external_otel_config_with(
        Some(&effective),
        None,
        ext_env(&[]),
        ext_client(),
        false,
    )
    .expect("managed paths alone must activate");
    assert_eq!(
        cfg.logs_ca_certificate.as_deref(),
        Some("/etc/ssl/corp-ca.pem")
    );
    assert_eq!(
        cfg.logs_client_certificate.as_deref(),
        Some("/etc/ssl/client.crt")
    );
    assert_eq!(cfg.logs_client_key.as_deref(), Some("/etc/ssl/client.key"));
    assert_eq!(
        cfg.metrics_client_certificate.as_deref(),
        Some("/etc/ssl/client.crt")
    );
    let cfg = resolve_external_otel_config_with(
        Some(&effective),
        None,
        ext_env(&[
            ("OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE", "/env/client.crt"),
            ("OTEL_EXPORTER_OTLP_CLIENT_KEY", "/env/client.key"),
        ]),
        ext_client(),
        false,
    )
    .expect("env override must resolve");
    assert_eq!(
        cfg.logs_client_certificate.as_deref(),
        Some("/env/client.crt")
    );
    assert_eq!(cfg.logs_client_key.as_deref(), Some("/env/client.key"));
}
#[test]
fn external_otel_requirements_pin_wins_over_env() {
    let req: toml::Value = toml::from_str(
        r#"
            [telemetry]
            otel_enabled = false
            "#,
    )
    .unwrap();
    assert!(
        resolve_external_otel_config_with(
            None,
            Some(&req),
            ext_env(&[("GROK_EXTERNAL_OTEL", "1"), ("OTEL_LOGS_EXPORTER", "otlp"),]),
            ext_client(),
            false,
        )
        .is_none()
    );
    let req: toml::Value = toml::from_str(
        r#"
            [telemetry]
            otel_log_user_prompts = false
            otel_log_tool_details = false
            "#,
    )
    .unwrap();
    let cfg = resolve_external_otel_config_with(
        None,
        Some(&req),
        ext_env(&[
            ("GROK_EXTERNAL_OTEL", "1"),
            ("OTEL_LOGS_EXPORTER", "otlp"),
            ("OTEL_LOG_USER_PROMPTS", "1"),
            ("OTEL_LOG_TOOL_DETAILS", "1"),
        ]),
        ext_client(),
        false,
    )
    .expect("stream still active; only gates pinned");
    assert!(!cfg.gates.log_user_prompts, "requirement pin must win");
    assert!(!cfg.gates.log_tool_details, "requirement pin must win");
}
/// Regression: an org enable via `[telemetry].otel_enabled`
/// (managed config / requirements — no `GROK_EXTERNAL_OTEL` env var) must
/// flip the master switch the *internal* pipeline keys off, so legacy
/// `OTEL_EXPORTER_OTLP_*` repointing shuts off in lockstep with the
/// external stream activating. A desync would point the internally-authed
/// firehose at the customer collector while
/// `internal_pipeline_consumed_otel_vars` blocks the external stream.
#[test]
fn external_otel_master_switch_resolves_from_all_layers() {
    let enabled_table: toml::Value = toml::from_str("[telemetry]\notel_enabled = true").unwrap();
    let disabled_table: toml::Value = toml::from_str("[telemetry]\notel_enabled = false").unwrap();
    assert!(external_otel_master_switch_from(
        None,
        None,
        Some(&enabled_table)
    ));
    assert!(!external_otel_master_switch_from(None, None, None));
    assert!(!external_otel_master_switch_from(
        None,
        Some(false),
        Some(&enabled_table)
    ));
    assert!(external_otel_master_switch_from(
        None,
        Some(true),
        Some(&disabled_table)
    ));
    assert!(!external_otel_master_switch_from(
        Some(&disabled_table),
        Some(true),
        Some(&enabled_table)
    ));
    assert!(external_otel_master_switch_from(
        Some(&enabled_table),
        Some(false),
        None
    ));
    let cfg = EndpointsConfig {
        otel_exporter_otlp_traces_endpoint: Some("https://collector.corp:4318/v1/traces".into()),
        external_otel_master_switch: true,
        ..internal_otlp_test_config()
    };
    assert!(!cfg.internal_otlp_consumed_standard_vars());
    assert!(
        !cfg.resolve_otlp_traces_endpoint()
            .contains("collector.corp")
    );
}
#[test]
fn external_otel_carries_internal_consumed_flag() {
    let cfg = resolve_external_otel_config_with(
        None,
        None,
        ext_env(&[("GROK_EXTERNAL_OTEL", "1"), ("OTEL_LOGS_EXPORTER", "otlp")]),
        ext_client(),
        true,
    )
    .expect("resolution itself still succeeds");
    assert!(cfg.internal_pipeline_consumed_otel_vars);
}
fn empty_config() -> toml::Value {
    toml::Value::Table(toml::map::Map::new())
}
fn clear_runtime_env_vars() {
    unsafe {
        std::env::remove_var("GROK_SUBAGENTS");
        std::env::remove_var("GROK_RESPECT_GITIGNORE");
        std::env::remove_var("GROK_WEB_SEARCH_MODEL");
        std::env::remove_var("GROK_SESSION_SUMMARY_MODEL");
        std::env::remove_var("GROK_CURSOR_SKILLS_ENABLED");
        std::env::remove_var("GROK_CURSOR_RULES_ENABLED");
        std::env::remove_var("GROK_CURSOR_AGENTS_ENABLED");
        std::env::remove_var("GROK_CLAUDE_SKILLS_ENABLED");
        std::env::remove_var("GROK_CLAUDE_RULES_ENABLED");
        std::env::remove_var("GROK_CLAUDE_AGENTS_ENABLED");
    }
}
fn clear_managed_mcp_env_vars() {
    unsafe {
        std::env::remove_var("GROK_MANAGED_MCPS_ENABLED");
        std::env::remove_var("GROK_MANAGED_MCP_GATEWAY_TOOLS_ENABLED");
    }
}
fn isolate_compat_env() -> Vec<EnvGuard> {
    COMPAT_CELLS
        .into_iter()
        .map(|cell| EnvGuard::unset(cell.env_var()))
        .collect()
}
fn parse_compat(source: &str) -> CompatConfigToml {
    let raw: toml::Value = toml::from_str(source).unwrap();
    raw.get("compat").unwrap().clone().try_into().unwrap()
}
fn assert_session_one_disabled(config: CompatConfig, expected: CompatVendor) {
    for cell in COMPAT_CELLS {
        if cell.surface() == CompatSurface::Sessions {
            assert_eq!(
                config.value(cell),
                cell.vendor() != expected,
                "{}.sessions",
                cell.vendor().as_str()
            );
        }
    }
}
fn remote_settings_with(key: CompatRemoteKey, value: bool) -> crate::util::config::RemoteSettings {
    let mut remote = crate::util::config::RemoteSettings::default();
    match key {
        CompatRemoteKey::CursorSkills => remote.cursor_skills_enabled = Some(value),
        CompatRemoteKey::CursorRules => remote.cursor_rules_enabled = Some(value),
        CompatRemoteKey::CursorAgents => remote.cursor_agents_enabled = Some(value),
        CompatRemoteKey::CursorMcps => remote.cursor_mcps_enabled = Some(value),
        CompatRemoteKey::CursorHooks => remote.cursor_hooks_enabled = Some(value),
        CompatRemoteKey::CursorSessions => remote.cursor_sessions_enabled = Some(value),
        CompatRemoteKey::ClaudeSkills => remote.claude_skills_enabled = Some(value),
        CompatRemoteKey::ClaudeRules => remote.claude_rules_enabled = Some(value),
        CompatRemoteKey::ClaudeAgents => remote.claude_agents_enabled = Some(value),
        CompatRemoteKey::ClaudeMcps => remote.claude_mcps_enabled = Some(value),
        CompatRemoteKey::ClaudeHooks => remote.claude_hooks_enabled = Some(value),
        CompatRemoteKey::ClaudeSessions => remote.claude_sessions_enabled = Some(value),
        CompatRemoteKey::CodexSessions => remote.codex_sessions_enabled = Some(value),
    }
    remote
}
#[test]
#[serial]
fn resolve_compat_defaults_match_registry() {
    let _env = isolate_compat_env();
    assert_eq!(
        resolve_compat_config(&CompatConfigToml::default(), None),
        CompatConfig::default()
    );
}
#[test]
#[serial]
fn resolve_compat_toml_sessions_disable_independently() {
    let _env = isolate_compat_env();
    for (vendor, section) in [
        (CompatVendor::Cursor, "cursor"),
        (CompatVendor::Claude, "claude"),
        (CompatVendor::Codex, "codex"),
    ] {
        let config = parse_compat(&format!("[compat.{section}]\nsessions = false"));
        assert_session_one_disabled(resolve_compat_config(&config, None), vendor);
    }
}
#[test]
#[serial]
fn resolve_raw_compat_sessions_fails_closed_per_vendor() {
    let _env = isolate_compat_env();
    let raw: toml::Value = toml::from_str(
        r#"
[compat.cursor]
sessions = "malformed"
[compat.claude]
sessions = false
[compat.codex]
hooks = "unrelated malformed field"
"#,
    )
    .unwrap();
    let resolved = resolve_compat_sessions_from_raw(Ok(&raw), None);
    assert!(!resolved.cursor.sessions);
    assert!(!resolved.claude.sessions);
    assert!(resolved.codex.sessions);
}
#[test]
#[serial]
fn resolve_raw_compat_sessions_keeps_absent_and_valid_cells_independent() {
    let _env = isolate_compat_env();
    let raw: toml::Value = toml::from_str(
        r#"
[compat.cursor]
sessions = false
hooks = "malformed but irrelevant"
[compat.claude]
sessions = true
"#,
    )
    .unwrap();
    let remote = crate::util::config::RemoteSettings {
        codex_sessions_enabled: Some(false),
        ..Default::default()
    };
    let resolved = resolve_compat_sessions_from_raw(Ok(&raw), Some(&remote));
    assert!(!resolved.cursor.sessions);
    assert!(resolved.claude.sessions);
    assert!(!resolved.codex.sessions);
}
#[test]
fn compat_config_cell_is_tolerant_and_fail_closed_per_cell() {
    let raw: toml::Value = toml::from_str(
        r#"
[compat.cursor]
skills = false
rules = "malformed"
[compat.claude]
hooks = true
"#,
    )
    .unwrap();
    let cell = |vendor, surface| {
        COMPAT_CELLS
            .into_iter()
            .find(|cell| cell.vendor() == vendor && cell.surface() == surface)
            .unwrap()
    };
    assert_eq!(
        compat_config_cell(Ok(&raw), cell(CompatVendor::Cursor, CompatSurface::Skills)),
        Ok(Some(false))
    );
    assert_eq!(
        compat_config_cell(Ok(&raw), cell(CompatVendor::Cursor, CompatSurface::Rules)),
        Err(CompatConfigCellError::Malformed)
    );
    assert_eq!(
        compat_config_cell(Ok(&raw), cell(CompatVendor::Claude, CompatSurface::Hooks)),
        Ok(Some(true))
    );
    assert_eq!(
        compat_config_cell(Ok(&raw), cell(CompatVendor::Codex, CompatSurface::Sessions)),
        Ok(None)
    );
    assert_eq!(
        compat_config_cell(Err(()), cell(CompatVendor::Claude, CompatSurface::Sessions)),
        Err(CompatConfigCellError::Unavailable)
    );
}
#[test]
#[serial]
fn resolve_raw_compat_sessions_load_failure_fails_closed() {
    let _env = isolate_compat_env();
    let resolved = resolve_compat_sessions_from_raw(Err(()), None);
    assert!(!resolved.cursor.sessions);
    assert!(!resolved.claude.sessions);
    assert!(!resolved.codex.sessions);
}
#[test]
#[serial]
fn resolve_raw_compat_sessions_load_failure_allows_env_override() {
    let _env = isolate_compat_env();
    let _codex = EnvGuard::set("GROK_CODEX_SESSIONS_ENABLED", "true");
    let resolved = resolve_compat_sessions_from_raw(Err(()), None);
    assert!(!resolved.cursor.sessions);
    assert!(!resolved.claude.sessions);
    assert!(resolved.codex.sessions);
}
#[test]
#[serial]
fn resolve_raw_compat_sessions_valid_empty_uses_remote_and_defaults() {
    let _env = isolate_compat_env();
    let raw = toml::Value::Table(Default::default());
    let remote = crate::util::config::RemoteSettings {
        claude_sessions_enabled: Some(false),
        ..Default::default()
    };
    let resolved = resolve_compat_sessions_from_raw(Ok(&raw), Some(&remote));
    assert!(resolved.cursor.sessions);
    assert!(!resolved.claude.sessions);
    assert!(resolved.codex.sessions);
}
#[test]
#[serial]
fn remote_keys_are_one_hot_and_false_overrides_default() {
    let _env = isolate_compat_env();
    for key in COMPAT_CELLS
        .into_iter()
        .filter_map(|cell| cell.remote_key())
    {
        let remote = remote_settings_with(key, false);
        for cell in COMPAT_CELLS {
            assert_eq!(
                remote_compat_value(Some(&remote), cell.remote_key()),
                (cell.remote_key() == Some(key)).then_some(false),
                "{key:?} mapped to {}.{}",
                cell.vendor().as_str(),
                cell.surface().as_str()
            );
        }
    }
    let remote = remote_settings_with(CompatRemoteKey::CursorSkills, false);
    assert!(CompatConfig::default().cursor.skills);
    assert!(
        !resolve_compat_config(&CompatConfigToml::default(), Some(&remote))
            .cursor
            .skills
    );
}
#[test]
#[serial]
fn resolve_compat_env_sessions_disable_independently() {
    let _env = isolate_compat_env();
    for (vendor, env_var) in [
        (CompatVendor::Cursor, "GROK_CURSOR_SESSIONS_ENABLED"),
        (CompatVendor::Claude, "GROK_CLAUDE_SESSIONS_ENABLED"),
        (CompatVendor::Codex, "GROK_CODEX_SESSIONS_ENABLED"),
    ] {
        let _disabled = EnvGuard::set(env_var, "false");
        assert_session_one_disabled(
            resolve_compat_config(&CompatConfigToml::default(), None),
            vendor,
        );
    }
}
#[test]
#[serial]
fn resolve_compat_precedence_and_reserved_codex_hook() {
    let _env = isolate_compat_env();
    let config = parse_compat("[compat.cursor]\nsessions = false\n[compat.codex]\nhooks = false");
    let remote = crate::util::config::RemoteSettings {
        cursor_sessions_enabled: Some(true),
        ..Default::default()
    };
    let resolved = resolve_compat_config(&config, Some(&remote));
    assert!(!resolved.cursor.sessions);
    assert!(!resolved.codex.hooks);
    assert!(resolved.cursor.hooks);
    assert!(resolved.claude.hooks);
    let _session = EnvGuard::set("GROK_CURSOR_SESSIONS_ENABLED", "true");
    let _hook = EnvGuard::set("GROK_CODEX_HOOKS_ENABLED", "true");
    let resolved = resolve_compat_config(&config, Some(&remote));
    assert!(resolved.cursor.sessions);
    assert!(resolved.codex.hooks);
}
#[test]
#[serial]
fn resolve_runtime_fields_compat_asymmetric_sources() {
    let _env = isolate_compat_env();
    let _cursor = EnvGuard::set("GROK_CURSOR_SESSIONS_ENABLED", "false");
    let raw: toml::Value =
        toml::from_str("[compat.cursor]\nsessions = true\n[compat.claude]\nsessions = false")
            .unwrap();
    let remote = crate::util::config::RemoteSettings {
        cursor_sessions_enabled: Some(true),
        claude_sessions_enabled: Some(true),
        codex_sessions_enabled: Some(false),
        ..Default::default()
    };
    let mut config = Config::new_from_toml_cfg(&raw).unwrap();
    config.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: Some(&remote),
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(!config.compat_resolved.cursor.sessions);
    assert!(!config.compat_resolved.claude.sessions);
    assert!(!config.compat_resolved.codex.sessions);
}
#[test]
#[serial]
fn resolve_runtime_fields_interactive_defaults() {
    clear_runtime_env_vars();
    clear_managed_mcp_env_vars();
    let raw = empty_config();
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: None,
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(cfg.subagents_enabled);
    assert!(!cfg.respect_gitignore);
    assert!(cfg.managed_mcps_enabled);
    assert!(!cfg.managed_mcp_gateway_tools_enabled);
    assert_eq!(
        cfg.web_search_model,
        crate::models::default_web_search_model()
    );
    assert_eq!(
        cfg.session_summary_model,
        Some(crate::models::default_session_summary_model().to_owned())
    );
    assert!(!cfg.path_not_found_hints);
}
#[test]
#[serial]
fn resolve_runtime_fields_headless_defaults() {
    clear_runtime_env_vars();
    clear_managed_mcp_env_vars();
    let raw = empty_config();
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: None,
        is_headless: true,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(
        !cfg.managed_mcps_enabled,
        "headless should default managed_mcps to false"
    );
    assert!(!cfg.managed_mcp_gateway_tools_enabled);
}
#[test]
#[serial]
fn resolve_runtime_fields_managed_gateway_tools_from_remote() {
    clear_runtime_env_vars();
    clear_managed_mcp_env_vars();
    let raw = empty_config();
    let remote = crate::util::config::RemoteSettings {
        managed_mcp_gateway_tools_enabled: Some(true),
        ..Default::default()
    };
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: Some(&remote),
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(cfg.managed_mcp_gateway_tools_enabled);
}
#[test]
#[serial]
fn resolve_runtime_fields_subagents_from_config() {
    clear_runtime_env_vars();
    let raw: toml::Value = toml::from_str("[subagents]\nenabled = true").unwrap();
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: None,
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(cfg.subagents_enabled);
}
#[test]
#[serial]
fn resolve_runtime_fields_cli_subagents_override() {
    clear_runtime_env_vars();
    let raw = empty_config();
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: None,
        is_headless: false,
        cli_subagents: Some(true),
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(cfg.subagents_enabled);
}
#[test]
#[serial]
fn resolve_runtime_fields_gitignore_from_env() {
    clear_runtime_env_vars();
    unsafe { std::env::set_var("GROK_RESPECT_GITIGNORE", "0") };
    let raw = empty_config();
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: None,
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(!cfg.respect_gitignore);
    clear_runtime_env_vars();
}
#[test]
#[serial]
fn resolve_runtime_fields_model_overrides_from_cli() {
    clear_runtime_env_vars();
    let raw = empty_config();
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: None,
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: Some("custom-ws"),
        cli_session_summary_model: Some("custom-ss"),
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert_eq!(cfg.web_search_model, "custom-ws");
    assert_eq!(cfg.session_summary_model, Some("custom-ss".to_owned()));
}
#[test]
#[serial]
fn resolve_runtime_fields_path_hints_from_remote() {
    clear_runtime_env_vars();
    let raw = empty_config();
    let remote = crate::util::config::RemoteSettings {
        path_not_found_hints: Some(true),
        ..Default::default()
    };
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    cfg.resolve_runtime_fields(&RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: Some(&remote),
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    });
    assert!(cfg.path_not_found_hints);
}
#[test]
#[serial]
fn resolve_runtime_fields_idempotent() {
    clear_runtime_env_vars();
    let raw: toml::Value = toml::from_str("[subagents]\nenabled = true").unwrap();
    let mut cfg = Config::new_from_toml_cfg(&raw).unwrap();
    let ctx = RuntimeResolutionContext {
        raw_config: &raw,
        remote_settings: None,
        is_headless: false,
        cli_subagents: None,
        cli_web_search_model: None,
        cli_session_summary_model: None,
        memory_enabled_override: None,
        disable_web_search: false,
        todo_gate: false,
        laziness_debug_log: None,
        storage_mode: None,
    };
    cfg.resolve_runtime_fields(&ctx);
    let first_subagents = cfg.subagents_enabled;
    let first_gitignore = cfg.respect_gitignore;
    let first_mcps = cfg.managed_mcps_enabled;
    let first_ws = cfg.web_search_model.clone();
    cfg.resolve_runtime_fields(&ctx);
    assert_eq!(cfg.subagents_enabled, first_subagents);
    assert_eq!(cfg.respect_gitignore, first_gitignore);
    assert_eq!(cfg.managed_mcps_enabled, first_mcps);
    assert_eq!(cfg.web_search_model, first_ws);
}
#[test]
fn telemetry_mode_toml_roundtrip() {
    let cfg: Features = toml::from_str("telemetry = true").unwrap();
    assert_eq!(cfg.telemetry, Some(TelemetryMode::Enabled));
    let cfg: Features = toml::from_str("telemetry = false").unwrap();
    assert_eq!(cfg.telemetry, Some(TelemetryMode::Disabled));
    let cfg: Features = toml::from_str(r#"telemetry = "session_metrics""#).unwrap();
    assert_eq!(cfg.telemetry, Some(TelemetryMode::SessionMetrics));
    let cfg: Features =
        toml::from_str(r#"telemetry = "metrics_v3""#).expect("unknown string must not error");
    assert_eq!(cfg.telemetry, Some(TelemetryMode::Disabled));
    assert!(toml::from_str::<Features>("telemetry = 42").is_err());
}
#[test]
fn telemetry_enabled_from_toml_recognizes_modes() {
    let on: toml::Value = toml::from_str("[features]\ntelemetry = true\n").unwrap();
    assert_eq!(telemetry_enabled_from_toml(&on), Some(true));
    let session: toml::Value = toml::from_str(
        r#"[features]
telemetry = "session_metrics"
"#,
    )
    .unwrap();
    assert_eq!(telemetry_enabled_from_toml(&session), Some(true));
    let unknown: toml::Value = toml::from_str(
        r#"[features]
telemetry = "garbage"
"#,
    )
    .unwrap();
    assert_eq!(telemetry_enabled_from_toml(&unknown), None);
}
#[test]
#[serial]
fn is_telemetry_explicitly_disabled_sync_env_signals() {
    unsafe { std::env::set_var("GROK_TELEMETRY_ENABLED", "0") };
    unsafe { std::env::remove_var("DISABLE_TELEMETRY") };
    assert!(is_telemetry_explicitly_disabled_sync());
    unsafe { std::env::set_var("GROK_TELEMETRY_ENABLED", "1") };
    assert!(!is_telemetry_explicitly_disabled_sync());
    unsafe { std::env::remove_var("GROK_TELEMETRY_ENABLED") };
    unsafe { std::env::set_var("DISABLE_TELEMETRY", "1") };
    assert!(is_telemetry_explicitly_disabled_sync());
    unsafe { std::env::remove_var("DISABLE_TELEMETRY") };
}
#[test]
fn version_overrides_apply_into_typed_config() {
    let mut value: toml::Value = toml::from_str(
        r#"
[models]
default = "grok-build"

[[version_overrides]]
minimum_version = "1.8.0"
[version_overrides.models]
default = "grok-4.5"
"#,
    )
    .unwrap();
    let v = semver::Version::parse("1.8.0").unwrap();
    pi_config::apply_version_overrides(&mut value, &v).unwrap();
    let cfg = Config::new_from_toml_cfg(&value).unwrap();
    assert_eq!(cfg.models.default.as_deref(), Some("grok-4.5"));
}
#[test]
#[serial]
fn mcp_push_server_status_default_is_true() {
    unsafe { std::env::remove_var("GROK_MCP_PUSH_SERVER_STATUS") };
    let r = resolve_mcp_push_server_status(None, None, None, None, None);
    assert!(r.value, "default-on by spec");
    assert_eq!(r.source, ConfigSource::Default);
}
#[test]
#[serial]
fn mcp_push_server_status_requirement_wins_over_everything() {
    unsafe { std::env::set_var("GROK_MCP_PUSH_SERVER_STATUS", "true") };
    let r =
        resolve_mcp_push_server_status(Some(false), Some(true), Some(true), Some(true), Some(true));
    unsafe { std::env::remove_var("GROK_MCP_PUSH_SERVER_STATUS") };
    assert!(!r.value, "requirement overrides every other layer");
    assert_eq!(r.source, ConfigSource::Requirement);
}
#[test]
#[serial]
fn mcp_push_server_status_cli_wins_over_env_and_below() {
    unsafe { std::env::set_var("GROK_MCP_PUSH_SERVER_STATUS", "true") };
    let r = resolve_mcp_push_server_status(None, Some(false), Some(true), Some(true), Some(true));
    unsafe { std::env::remove_var("GROK_MCP_PUSH_SERVER_STATUS") };
    assert!(!r.value);
    assert_eq!(r.source, ConfigSource::Cli);
}
#[test]
#[serial]
fn mcp_push_server_status_env_wins_over_config_and_below() {
    unsafe { std::env::set_var("GROK_MCP_PUSH_SERVER_STATUS", "false") };
    let r = resolve_mcp_push_server_status(None, None, Some(true), Some(true), Some(true));
    unsafe { std::env::remove_var("GROK_MCP_PUSH_SERVER_STATUS") };
    assert!(!r.value);
    assert_eq!(r.source, ConfigSource::Env);
}
#[test]
#[serial]
fn mcp_push_server_status_config_wins_over_managed_and_feature_flag() {
    unsafe { std::env::remove_var("GROK_MCP_PUSH_SERVER_STATUS") };
    let r = resolve_mcp_push_server_status(None, None, Some(false), Some(true), Some(true));
    assert!(!r.value);
    assert_eq!(r.source, ConfigSource::Config);
}
#[test]
#[serial]
fn mcp_push_server_status_managed_wins_over_feature_flag() {
    unsafe { std::env::remove_var("GROK_MCP_PUSH_SERVER_STATUS") };
    let r = resolve_mcp_push_server_status(None, None, None, Some(false), Some(true));
    assert!(!r.value);
    assert_eq!(r.source, ConfigSource::ManagedConfig);
}
#[test]
#[serial]
fn mcp_push_server_status_feature_flag_used_when_no_higher_layer() {
    unsafe { std::env::remove_var("GROK_MCP_PUSH_SERVER_STATUS") };
    let r = resolve_mcp_push_server_status(None, None, None, None, Some(false));
    assert!(!r.value);
    assert_eq!(r.source, ConfigSource::Remote);
}
#[test]
fn a_status_line_the_parser_could_not_read_in_full_reaches_grok_inspect() {
    use super::super::config_model_override_parse::{ConfigWarningKind, WarningTarget};
    let raw_config: toml::Value = toml::from_str(
        r#"
            [ui]
            theme = "kanagawa"

            [ui.status_line]
            type = "disabled"
            padding = "2"
            colour = "red"
            "#,
    )
    .unwrap();
    let cfg = Config::new_from_toml_cfg(&raw_config).expect("a typo must not fail the config");
    let warnings = |path: &str, kind: ConfigWarningKind| {
        cfg.config_warnings
            .iter()
            .filter(|w| {
                w.kind == kind
                    && matches!(&w.target, WarningTarget::ConfigKey { path: p } if p == path)
            })
            .count()
    };
    assert_eq!(
        warnings("ui.status_line", ConfigWarningKind::InvalidValue),
        1
    );
    assert_eq!(
        warnings("ui.status_line.colour", ConfigWarningKind::UnknownField),
        1
    );
    assert_eq!(cfg.ui.theme.as_deref(), Some("kanagawa"));
}
