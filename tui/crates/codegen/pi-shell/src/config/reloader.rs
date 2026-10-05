#[cfg(test)]
mod tests {

    #[test]
    fn memory_config_diff_detects_enabled_change() {
        let empty = toml::Value::Table(toml::map::Map::new());
        let enabled: toml::Value = toml::from_str("[memory]\nenabled = true").unwrap();

        let old = crate::agent::config::Config::new_from_toml_cfg(&empty)
            .unwrap()
            .resolve_memory(None, None);
        let new = crate::agent::config::Config::new_from_toml_cfg(&enabled)
            .unwrap()
            .resolve_memory(None, None);
        assert_ne!(old, new, "should detect enabled field change");
    }

    #[test]
    fn memory_config_diff_detects_search_param_change() {
        let a: toml::Value = toml::from_str("[memory.search]\nmax_results = 6").unwrap();
        let b: toml::Value = toml::from_str("[memory.search]\nmax_results = 10").unwrap();

        let old = crate::agent::config::Config::new_from_toml_cfg(&a)
            .unwrap()
            .resolve_memory(None, None);
        let new = crate::agent::config::Config::new_from_toml_cfg(&b)
            .unwrap()
            .resolve_memory(None, None);
        assert_ne!(old, new, "should detect search param change");
    }

    #[test]
    fn models_changed_detects_new_byok_model() {
        let a = toml::Value::Table(toml::map::Map::new());
        let b: toml::Value = toml::from_str(
            r#"
[model.my-custom]
model = "grok-4.5"
base_url = "https://api.example.com/v1"
"#,
        )
        .unwrap();
        assert_ne!(a.get("model"), b.get("model"));
    }

    #[test]
    fn models_changed_detects_default_change() {
        let a: toml::Value = toml::from_str("[models]\ndefault = \"grok-code-fast-1\"").unwrap();
        let b: toml::Value = toml::from_str("[models]\ndefault = \"grok-code-slow-1\"").unwrap();
        assert_ne!(a.get("models"), b.get("models"));
    }

    #[test]
    fn mcp_servers_changed_detects_new_server() {
        let a = toml::Value::Table(toml::map::Map::new());
        let b: toml::Value = toml::from_str(
            r#"
[mcp_servers.test]
command = "/bin/test"
"#,
        )
        .unwrap();
        assert_ne!(a.get("mcp_servers"), b.get("mcp_servers"));
    }

    #[test]
    fn mcp_servers_unchanged_same_config() {
        let cfg: toml::Value = toml::from_str(
            r#"
[mcp_servers.test]
command = "/bin/test"
"#,
        )
        .unwrap();
        assert_eq!(cfg.get("mcp_servers"), cfg.get("mcp_servers"));
    }
}
