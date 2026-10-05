use indexmap::IndexMap;

use super::config::EnvKeys;
use super::config_model_override_parse::{ConfigWarning, ConfigWarningKind};
use crate::sampling::ApiBackend;

#[derive(Clone, Debug, Default, serde::Deserialize)]
#[serde(default)]
pub struct ModelProviderConfig {
    pub base_url: Option<String>,
    pub api_base_url: Option<String>,
    pub env_key: Option<EnvKeys>,
    pub api_key: Option<String>,
    pub api_backend: Option<ApiBackend>,
    pub extra_headers: IndexMap<String, String>,
    /// Query parameters folded into every request URL; inherited by models.
    pub query_params: IndexMap<String, String>,
    /// Header name to environment variable; inherited by models, resolved at
    /// client build.
    pub env_http_headers: IndexMap<String, String>,
    pub auth_provider: Option<String>,
    pub auth: Option<crate::auth::AuthProviderConfig>,
    pub context_window: Option<u64>,
}

pub(crate) fn model_provider_auth_name(provider_id: &str) -> String {
    format!("model_provider:{provider_id}")
}

pub(crate) fn auth_config_issues(
    config: &crate::auth::AuthProviderConfig,
) -> Vec<(&'static str, ConfigWarningKind, String)> {
    let mut issues = Vec::new();
    if !config.is_usable() {
        issues.push((
            "command",
            ConfigWarningKind::InvalidValue,
            "missing or empty command; models resolve with no credential".to_owned(),
        ));
    }
    let skew = crate::auth::PROVIDER_TOKEN_EXPIRY_SKEW_SECS;
    if config.token_ttl_secs.is_some_and(|ttl| ttl <= skew) {
        issues.push((
            "token_ttl_secs",
            ConfigWarningKind::InvalidValue,
            format!(
                "at or below the {skew}s refresh margin; the command will run before every turn"
            ),
        ));
    }
    if let Some(timeout) = config.timeout_secs
        && !(1..=crate::auth::PROVIDER_TIMEOUT_CEILING_SECS).contains(&timeout)
    {
        let ceiling = crate::auth::PROVIDER_TIMEOUT_CEILING_SECS;
        issues.push((
            "timeout_secs",
            ConfigWarningKind::InvalidValue,
            if timeout == 0 {
                "below the 1 second minimum; clamped to 1".to_owned()
            } else {
                format!("above the {ceiling}s maximum; clamped to {ceiling}")
            },
        ));
    }
    issues
}

pub(crate) fn parse_model_providers(
    raw_config: &toml::Value,
) -> (IndexMap<String, ModelProviderConfig>, Vec<ConfigWarning>) {
    let mut providers = IndexMap::new();
    let mut warnings = Vec::new();
    let Some(section) = raw_config.get("model_providers") else {
        return (providers, warnings);
    };
    let Some(table) = section.as_table() else {
        warnings.push(ConfigWarning::model_provider_section(
            ConfigWarningKind::NotATable,
            format!(
                "`model_providers` must be a table of [model_providers.<id>] entries, got {}; \
                 all model providers ignored",
                section.type_str()
            ),
        ));
        return (providers, warnings);
    };
    for (id, value) in table {
        let mut unknown = Vec::new();
        match serde_ignored::deserialize::<_, _, ModelProviderConfig>(value.clone(), |path| {
            unknown.push(path.to_string());
        }) {
            Ok(provider) => {
                for key in unknown {
                    warnings.push(ConfigWarning::model_provider(
                        id,
                        Some(key.as_str()),
                        ConfigWarningKind::UnknownField,
                        "unrecognized key; field ignored".to_owned(),
                    ));
                }
                if let Some(auth) = &provider.auth {
                    for (field, kind, reason) in auth_config_issues(auth) {
                        warnings.push(ConfigWarning::model_provider(
                            id,
                            Some(&format!("auth.{field}")),
                            kind,
                            reason,
                        ));
                    }
                }
                let has_helper = provider.auth.is_some() || provider.auth_provider.is_some();
                let has_static_api_key = provider
                    .api_key
                    .as_deref()
                    .map(str::trim)
                    .is_some_and(|k| !k.is_empty());
                if has_helper && has_static_api_key {
                    warnings.push(ConfigWarning::model_provider(
                        id,
                        Some("api_key"),
                        ConfigWarningKind::ConflictingFields,
                        "api_key shadows this provider's auth helper; the static key always \
                         takes precedence, so the helper never runs for inheriting models"
                            .to_owned(),
                    ));
                } else if has_helper
                    && provider
                        .env_key
                        .as_ref()
                        .and_then(EnvKeys::primary)
                        .is_some()
                {
                    warnings.push(ConfigWarning::model_provider(
                        id,
                        Some("env_key"),
                        ConfigWarningKind::ConflictingFields,
                        "env_key may shadow this provider's auth helper; env_key takes precedence \
                         when its variable resolves, otherwise the helper runs"
                            .to_owned(),
                    ));
                }
                if provider.auth_provider.is_some() && provider.auth.is_some() {
                    warnings.push(ConfigWarning::model_provider(
                        id,
                        Some("auth"),
                        ConfigWarningKind::ConflictingFields,
                        "inline auth is shadowed by auth_provider on this provider; the referenced \
                         provider takes precedence, so the inline helper never runs"
                            .to_owned(),
                    ));
                }
                providers.insert(id.clone(), provider);
            }
            Err(error) => {
                warnings.push(ConfigWarning::model_provider(
                    id,
                    None,
                    ConfigWarningKind::InvalidValue,
                    format!(
                        "failed to parse ({error}); provider skipped, inheriting models \
                         resolve with defaults"
                    ),
                ));
            }
        }
    }
    (providers, warnings)
}

#[cfg(test)]
mod tests {
    use crate::agent::config::Config;

    #[test]
    fn model_provider_parse_warnings_are_lenient_and_specific() {
        use super::super::config_model_override_parse::{ConfigWarningKind, WarningTarget};

        let raw_config: toml::Value = toml::from_str(
            r#"
            [model_providers.good]
            base_url = "https://good.example/v1"

            [model_providers.bad-type]
            context_window = "not-a-number"

            [model_providers.typo]
            base_url = "https://typo.example/v1"
            unknown_field = 5

            [model.on-broken-provider]
            model = "m"
            base_url = "https://x.example/v1"
            context_window = 200000
            model_provider = "bad-type"
            "#,
        )
        .unwrap();

        let cfg = Config::new_from_toml_cfg(&raw_config)
            .expect("one bad provider must not fail the config");
        assert!(cfg.model_providers.contains_key("good"));
        assert!(
            !cfg.model_providers.contains_key("bad-type"),
            "a malformed provider is skipped"
        );

        let has_provider = |id: &str, field: Option<&str>, kind: ConfigWarningKind| {
            cfg.config_warnings.iter().any(|w| {
                w.kind == kind
                    && matches!(
                        &w.target,
                        WarningTarget::ModelProvider { id: i, field: f }
                            if i == id && f.as_deref() == field
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
            Some("unknown_field"),
            ConfigWarningKind::UnknownField
        ));
        assert!(
            !cfg.config_warnings.iter().any(|w| {
                matches!(
                    &w.target,
                    WarningTarget::Model { field, .. }
                        if field.as_deref() == Some("model_provider")
                )
            }),
            "a declared-but-malformed provider must not also warn as undefined: {:?}",
            cfg.config_warnings
        );

        let raw_config: toml::Value = toml::from_str(r#"model_providers = "oops""#).unwrap();
        let cfg = Config::new_from_toml_cfg(&raw_config)
            .expect("a non-table model_providers must not fail the config");
        assert!(cfg.model_providers.is_empty());
        assert!(
            cfg.config_warnings.iter().any(|w| {
                matches!(w.target, WarningTarget::ModelProviderSection)
                    && w.kind == ConfigWarningKind::NotATable
            }),
            "non-table section warns: {:?}",
            cfg.config_warnings
        );
    }

    #[test]
    fn model_provider_conflicting_credentials_warn() {
        use super::super::config_model_override_parse::{ConfigWarningKind, WarningTarget};

        let raw_config: toml::Value = toml::from_str(
            r#"
            [model_providers.static-shadows]
            base_url = "https://a.example/v1"
            api_key = "sk-static"
            [model_providers.static-shadows.auth]
            command = "printf tok"

            [model_providers.env-shadows]
            base_url = "https://b.example/v1"
            env_key = "SOME_VAR"
            [model_providers.env-shadows.auth]
            command = "printf tok"

            [model_providers.two-helpers]
            base_url = "https://c.example/v1"
            auth_provider = "corp"
            [model_providers.two-helpers.auth]
            command = "printf tok"
            "#,
        )
        .unwrap();

        let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
        let has = |id: &str, field: &str| {
            cfg.config_warnings.iter().any(|w| {
                w.kind == ConfigWarningKind::ConflictingFields
                    && matches!(
                        &w.target,
                        WarningTarget::ModelProvider { id: i, field: f }
                            if i == id && f.as_deref() == Some(field)
                    )
            })
        };
        assert!(
            has("static-shadows", "api_key"),
            "a static api_key shadows the helper: {:?}",
            cfg.config_warnings
        );
        assert!(
            has("env-shadows", "env_key"),
            "an env_key may shadow the helper: {:?}",
            cfg.config_warnings
        );
        assert!(
            has("two-helpers", "auth"),
            "auth_provider shadows the inline auth helper: {:?}",
            cfg.config_warnings
        );
    }

    #[test]
    fn model_provider_undefined_auth_provider_warns() {
        use super::super::config_model_override_parse::{ConfigWarningKind, WarningTarget};

        let raw_config: toml::Value = toml::from_str(
            r#"
            [model_providers.gateway]
            base_url = "https://gateway.example/v1"
            auth_provider = "nonexistent"
            "#,
        )
        .unwrap();

        let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
        assert!(
            cfg.config_warnings.iter().any(|w| {
                w.kind == ConfigWarningKind::InvalidValue
                    && matches!(
                        &w.target,
                        WarningTarget::ModelProvider { id, field }
                            if id == "gateway" && field.as_deref() == Some("auth_provider")
                    )
            }),
            "an undefined provider auth_provider reference warns: {:?}",
            cfg.config_warnings
        );
    }

    #[test]
    fn model_provider_inline_auth_namespace_collision_warns() {
        use super::super::config_model_override_parse::{ConfigWarningKind, WarningTarget};

        let raw_config: toml::Value = toml::from_str(
            r#"
            [auth_provider."model_provider:gateway"]
            command = "printf hand-written"

            [model_providers.gateway]
            base_url = "https://gateway.example/v1"

            [model_providers.gateway.auth]
            command = "printf inline"
            "#,
        )
        .unwrap();

        let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
        assert!(
            cfg.config_warnings.iter().any(|w| {
                w.kind == ConfigWarningKind::ConflictingFields
                    && matches!(
                        &w.target,
                        WarningTarget::ModelProvider { id, field }
                            if id == "gateway" && field.as_deref() == Some("auth")
                    )
            }),
            "a reserved-namespace collision warns: {:?}",
            cfg.config_warnings
        );
        assert_eq!(
            cfg.auth_providers
                .get("model_provider:gateway")
                .map(|c| c.command.as_str()),
            Some("printf inline"),
            "inline auth wins the reserved name"
        );
    }

    #[test]
    fn model_provider_inline_auth_ttl_and_timeout_warn() {
        use super::super::config_model_override_parse::{ConfigWarningKind, WarningTarget};

        let raw_config: toml::Value = toml::from_str(
            r#"
            [model_providers.gateway]
            base_url = "https://gateway.example/v1"

            [model_providers.gateway.auth]
            command = "printf tok"
            token_ttl_secs = 5
            timeout_secs = 0
            "#,
        )
        .unwrap();

        let cfg = Config::new_from_toml_cfg(&raw_config).expect("config should parse");
        let has = |field: &str| {
            cfg.config_warnings.iter().any(|w| {
                w.kind == ConfigWarningKind::InvalidValue
                    && matches!(
                        &w.target,
                        WarningTarget::ModelProvider { id, field: f }
                            if id == "gateway" && f.as_deref() == Some(field)
                    )
            })
        };
        assert!(
            has("auth.token_ttl_secs"),
            "inline auth ttl below the refresh margin warns: {:?}",
            cfg.config_warnings
        );
        assert!(
            has("auth.timeout_secs"),
            "inline auth timeout out of range warns: {:?}",
            cfg.config_warnings
        );
    }
}
