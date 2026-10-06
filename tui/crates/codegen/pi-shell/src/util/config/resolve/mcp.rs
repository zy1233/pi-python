use toml::Value as TomlValue;

/// Resolve `mcp.push_server_status` for a session.
///
/// Thin wrapper around the canonical
/// [`crate::agent::config::resolve_mcp_push_server_status`] that
/// mirrors [`resolve_mcp_liveness_watchers`].
///
/// Pulls each layer from its TOML / runtime source:
///
/// | Layer        | Source                                                          |
/// |--------------|-----------------------------------------------------------------|
/// | requirement  | `[features] mcp_push_server_status` in `requirements.toml`      |
/// | cli          | (none — no CLI flag)                                            |
/// | env          | `GROK_MCP_PUSH_SERVER_STATUS` (handled by `BoolFlag::env`)      |
/// | config       | `[features] mcp_push_server_status` in `~/.grok/config.toml`    |
/// | managed      | `[features] mcp_push_server_status` in `managed_config.toml`    |
/// | feature_flag | (none yet — remote settings plumbing TBD)                            |
/// | default      | `true`                                                          |
///
/// Returns the resolved boolean.
pub fn resolve_mcp_push_server_status(
    requirements: Option<&TomlValue>,
    user: Option<&TomlValue>,
    managed: Option<&TomlValue>,
) -> bool {
    fn from_toml(v: Option<&TomlValue>) -> Option<bool> {
        v?.get("features")?.get("mcp_push_server_status")?.as_bool()
    }
    crate::agent::config::resolve_mcp_push_server_status(
        from_toml(requirements),
        /* cli          */ None,
        from_toml(user),
        from_toml(managed),
        /* feature_flag */ None,
    )
    .value
}

#[cfg(test)]
mod mcp_startup_timeout_tests {}

// ── MCP max output bytes (inline tool-result cap) ───────────────────────────
//
// Full multi-tier resolve lives only here (shell can read config/requirements).
// Tools holds a single effective atomic: we resolve once on apply and push the
// result via `set_mcp_max_output_bytes` so free-function truncation sees it.

#[cfg(test)]
mod max_mcp_output_bytes_tests {}
