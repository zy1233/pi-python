use toml::Value as TomlValue;

/// Env override for the **auto** permission-mode feature gate.
pub(crate) const ENV_AUTO_PERMISSION_MODE: &str = "GROK_AUTO_PERMISSION_MODE";

/// Crate-wide serialization lock for tests that mutate
/// `GROK_AUTO_PERMISSION_MODE`. Every test reading the gate (here and in
/// `permissions.rs`, compiled into the same test binary) locks this so a
/// concurrent setter can't make them flaky.
#[cfg(test)]
pub(crate) static AUTO_PERMISSION_MODE_ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Extract the `[auto_mode] enabled` gate from one TOML layer (the local opt-in
/// that replaced `[features] auto_permission_mode`).
fn auto_permission_mode_from_toml(v: Option<&TomlValue>) -> Option<bool> {
    v?.get("auto_mode")?.get("enabled")?.as_bool()
}

/// Pure precedence core for the auto-permission-mode gate, shared by the
/// `RemoteSettings`-typed resolver and the free-function disk reader so the
/// two can't drift. Precedence: requirement > env (`GROK_AUTO_PERMISSION_MODE`)
/// > config > managed > remote feature-flag > default (`true`).
fn resolve_auto_permission_mode_layers(
    requirement: Option<bool>,
    config: Option<bool>,
    managed: Option<bool>,
    feature_flag: Option<bool>,
) -> crate::agent::config::Resolved<bool> {
    use crate::agent::config::BoolFlag;
    BoolFlag::env(ENV_AUTO_PERMISSION_MODE)
        .requirement(requirement)
        .config(config)
        .managed(managed)
        .feature_flag(feature_flag)
        .default(true)
        .resolve()
}

/// Single source of truth for the remote settings `auto_mode` config at free-function
/// call sites that don't hold a live `RemoteSettings` (gate launch decision,
/// pager kill-switch, classifier wiring). Coerced once on cache; the gate reads
/// `.enabled` off it. Lock poisoning is treated fail-safe (`.read().ok()` etc.).
static REMOTE_AUTO_MODE_CONFIG: std::sync::RwLock<Option<crate::agent::config::AutoModeConfig>> =
    std::sync::RwLock::new(None);

/// Update ONLY the gate `enabled` in the cached remote config (the pager
/// kill-switch path carries just the bool). Seeds a default config first so
/// `prompt_type`/`classifier_model`/`reasoning_effort` are not clobbered.
pub fn cache_remote_auto_permission_mode_enabled(value: Option<bool>) {
    if let Ok(mut guard) = REMOTE_AUTO_MODE_CONFIG.write() {
        guard.get_or_insert_with(Default::default).enabled = value;
    }
}

fn cached_remote_auto_permission_mode_enabled() -> Option<bool> {
    REMOTE_AUTO_MODE_CONFIG
        .read()
        .ok()
        .and_then(|g| g.as_ref().and_then(|c| c.enabled))
}

fn auto_permission_mode_enabled_from_layers(
    layers: &crate::config::ConfigLayers,
    requirements: Option<&TomlValue>,
    remote: Option<bool>,
) -> bool {
    let crate::config::ConfigLayers {
        system_managed,
        managed,
        user,
        env_overlay: _,
        user_requirements: _,
        system_requirements: _,
        mdm_requirements: _,
        campaigns: _,
    } = layers;
    resolve_auto_permission_mode_layers(
        auto_permission_mode_from_toml(requirements),
        auto_permission_mode_from_toml(Some(user)),
        auto_permission_mode_from_toml(Some(managed))
            .or_else(|| auto_permission_mode_from_toml(Some(system_managed))),
        remote,
    )
    .value
}

/// Disk form of the gate (overlay-free). Defaults `true`.
pub fn auto_permission_mode_enabled_from_disk() -> bool {
    let requirements = crate::config::load_merged_requirements();
    let layers = match crate::config::ConfigLayers::load() {
        Ok(l) => l,
        Err(e) => {
            tracing::warn!(error = %e, "auto_permission_mode: failed to load config layers");
            crate::config::ConfigLayers::default()
        }
    };
    auto_permission_mode_enabled_from_layers(
        &layers,
        requirements.as_ref(),
        cached_remote_auto_permission_mode_enabled(),
    )
}

#[cfg(test)]
mod auto_permission_mode_gate_tests {
    

}
