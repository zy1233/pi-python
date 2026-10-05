use toml::Value as TomlValue;

/// Env override for the full crash-handler install gate.
pub(crate) const ENV_CRASH_HANDLER: &str = "GROK_CRASH_HANDLER";

/// Extract `[diagnostics] crash_handler` from one TOML layer.
fn crash_handler_from_toml(v: Option<&TomlValue>) -> Option<bool> {
    v?.get("diagnostics")?.get("crash_handler")?.as_bool()
}

/// Precedence core shared by the typed resolver and the disk reader so they
/// can't drift: requirement > env > config > managed > remote > default `false`.
fn resolve_crash_handler_enabled_layers(
    requirement: Option<bool>,
    config: Option<bool>,
    managed: Option<bool>,
    feature_flag: Option<bool>,
) -> crate::agent::config::Resolved<bool> {
    use crate::agent::config::BoolFlag;
    BoolFlag::env(ENV_CRASH_HANDLER)
        .requirement(requirement)
        .config(config)
        .managed(managed)
        .feature_flag(feature_flag)
        .resolve()
}

/// Process-global cache of the remote tier, read by
/// [`load_crash_handler_enabled_sync`] at pre-Tokio install (no live
/// `RemoteSettings` there). Fail-safe to `None` on lock poisoning.
static REMOTE_CRASH_HANDLER_ENABLED: std::sync::RwLock<Option<bool>> = std::sync::RwLock::new(None);

fn cached_remote_crash_handler_enabled() -> Option<bool> {
    REMOTE_CRASH_HANDLER_ENABLED.read().ok().and_then(|g| *g)
}

/// Merge system-managed policy (`/etc/grok`) under home `managed_config.toml`
/// so MDM/system layers still reach the managed BoolFlag tier.
fn load_managed_toml_layers() -> Option<TomlValue> {
    let system = crate::config::load_system_managed_config().ok();
    let managed = crate::config::load_managed_config().ok();
    match (system, managed) {
        (None, None) => None,
        (Some(s), None) => Some(s),
        (None, Some(m)) => Some(m),
        (Some(mut s), Some(m)) => {
            pi_config::deep_merge_toml(&mut s, &m);
            Some(s)
        }
    }
}

/// Free-function form of [`resolve_crash_handler_enabled`] for the pager-bin
/// install path (no live `RemoteSettings`): env + requirements + user +
/// system/home managed from disk plus the cached remote tier. Defaults
/// `false`.
pub fn load_crash_handler_enabled_sync() -> bool {
    let requirements = crate::config::load_merged_requirements();
    let user = crate::config::load_from_disk().ok();
    let managed = load_managed_toml_layers();
    resolve_crash_handler_enabled_layers(
        crash_handler_from_toml(requirements.as_ref()),
        crash_handler_from_toml(user.as_ref()),
        crash_handler_from_toml(managed.as_ref()),
        cached_remote_crash_handler_enabled(),
    )
    .value
}

#[cfg(test)]
mod crash_handler_gate_tests {}
