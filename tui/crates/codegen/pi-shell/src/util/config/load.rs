use super::mcp::*;
use toml::Value as TomlValue;
pub async fn load_config() -> Config {
    let root: TomlValue = match crate::config::load_effective_config() {
        Ok(v) => v,
        Err(_) => return Config::default(),
    };
    load_config_from_toml(&root)
}
/// Parse `Config` from a pre-loaded TOML value. Used by both async and sync paths.
pub fn load_config_from_toml(root: &TomlValue) -> Config {
    let table = match root.as_table() {
        Some(t) => t,
        None => return Config::default(),
    };
    fn section<T: serde::de::DeserializeOwned + Default>(
        table: &toml::map::Map<String, TomlValue>,
        key: &str,
    ) -> T {
        table
            .get(key)
            .and_then(|v| v.clone().try_into().ok())
            .unwrap_or_default()
    }
    if let Some(TomlValue::Table(toolset)) = table.get("toolset")
        && toolset.get("use_concise").is_some()
    {
        tracing::warn!(
            "`[toolset] use_concise` is deprecated and no longer has any effect. \
             Set `use_concise = true` on individual model entries in config.toml instead."
        );
    }
    let management_api_key = table
        .get("endpoints")
        .and_then(|v| v.get("management_api_key"))
        .and_then(|v| v.as_str())
        .map(str::to_owned);
    let permission = table
        .get("permission")
        .and_then(|v| v.clone().try_into::<PermissionConfig>().ok());
    Config {
        cli: section(table, "cli"),
        models: section(table, "models"),
        ui: section(table, "ui"),
        harness: {
            #[allow(unused_mut)]
            let mut harness: crate::agent::config::HarnessConfig = section(table, "harness");
            harness
        },
        skills: section(table, "skills"),
        compat: section(table, "compat"),
        management_api_key,
        permission,
        diagnostics: section(table, "diagnostics"),
        session: section(table, "session"),
        ask_user_question: table
            .get("toolset")
            .and_then(|t| t.get("ask_user_question"))
            .and_then(|v| v.clone().try_into().ok())
            .unwrap_or_default(),
        privacy: section(table, "privacy"),
        consent: section(table, "consent"),
        telemetry: section(table, "telemetry"),
        features: section(table, "features"),
    }
}
#[cfg(test)]
mod tests {
    
    use toml::Value as TomlValue;
    #[test]
    fn test_models_default_parsing() {
        let toml_str = r#"
[models]
default = "grok-code-fast-1"
"#;
        let root: TomlValue = toml::from_str(toml_str).unwrap();
        if let TomlValue::Table(table) = root
            && let Some(TomlValue::Table(models)) = table.get("models")
        {
            let default = models
                .get("default")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            assert_eq!(default.as_deref(), Some("grok-code-fast-1"));
        } else {
            panic!("Expected models table");
        }
    }
    #[test]
    fn test_relay_sync_enabled_true() {
        let toml_str = r#"
[relay]
enabled = true
"#;
        let root: TomlValue = toml::from_str(toml_str).unwrap();
        if let TomlValue::Table(table) = root
            && let Some(TomlValue::Table(relay)) = table.get("relay")
        {
            let enabled = relay
                .get("enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            assert!(enabled);
        } else {
            panic!("Expected relay table");
        }
    }
    #[test]
    fn test_relay_sync_enabled_false() {
        let toml_str = r#"
[relay]
enabled = false
"#;
        let root: TomlValue = toml::from_str(toml_str).unwrap();
        if let TomlValue::Table(table) = root
            && let Some(TomlValue::Table(relay)) = table.get("relay")
        {
            let enabled = relay
                .get("enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            assert!(!enabled);
        } else {
            panic!("Expected relay table");
        }
    }
    #[test]
    fn test_relay_sync_default_false() {
        let toml_str = r#"
[relay]
"#;
        let root: TomlValue = toml::from_str(toml_str).unwrap();
        if let TomlValue::Table(table) = root
            && let Some(TomlValue::Table(relay)) = table.get("relay")
        {
            let enabled = relay
                .get("enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            assert!(!enabled);
        }
    }
    #[test]
    fn test_relay_sync_no_section() {
        let toml_str = r#"
[models]
default = "grok-code-fast-1"
"#;
        let root: TomlValue = toml::from_str(toml_str).unwrap();
        if let TomlValue::Table(table) = root {
            let has_relay = table.get("relay").is_some();
            assert!(!has_relay);
        }
    }
}
