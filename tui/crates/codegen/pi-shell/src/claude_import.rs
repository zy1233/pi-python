// claude_import.rs
// Reads the `[claude_compat] imported = true` marker from `~/.grok/config.toml`.
//
// Runtime fallbacks that used to read `.claude/` consult it and stop reading
// once the user has migrated to native config.

use std::path::Path;
use std::sync::OnceLock;

use toml::Value as TomlValue;
use tracing::info;

// Import marker (read side)
//
// The marker `[claude_compat] imported = true` in `~/.grok/config.toml` is
// the signal that runtime fallback paths should stop reading `.claude/`. The
// cached reader lives here so every gate consults the same marker.

/// Cached result of [`is_claude_import_marked`]. See its doc for the
/// caching rationale and trade-offs.
///
/// `RwLock<Option<bool>>` rather than `OnceLock<bool>` so tests can reset the
/// state between cases (and so a future runtime-invalidation hook can flip
/// it back to `None`). The fast path is a read-lock + cached `bool`, so the
/// per-call overhead is one atomic CAS — well below the cost of the
/// uncached `read_to_string` + TOML parse.
static MARKER_CACHE: std::sync::RwLock<Option<bool>> = std::sync::RwLock::new(None);

/// Whether the current user has already imported Claude settings.
///
/// Reads `[claude_compat] imported = true` from `~/.grok/config.toml` once
/// per process and caches the result. When the marker is set, runtime
/// fallbacks that read `.claude/` should be skipped — the user has migrated
/// to native config.
///
/// Resilient: returns `false` on missing file, missing section, parse error,
/// or any other failure.
///
/// Caching avoids a `read_to_string` + TOML parse on every gated call
/// (`load_claude_env_with_project`, MCP loaders, hook discovery, etc.).
/// Trade-off: a user who manually flips the marker mid-session must restart to
/// see the change — acceptable because reverting after import is rare. Use
/// [`is_claude_import_marked_at`] in tests, which bypasses the cache.
///
/// **When to call this vs. [`is_claude_import_marked_with_log`]**: prefer the
/// `_with_log` variant for runtime compat gates that *change behavior* based
/// on the marker (so users see one log line indicating the cutoff fired).
/// Use the bare version for read-time display logic that already has its own
/// path (e.g. UI listings in `extensions/skills.rs` and `inspect.rs`).
pub(crate) fn is_claude_import_marked() -> bool {
    if let Some(v) = *MARKER_CACHE.read().expect("MARKER_CACHE poisoned") {
        return v;
    }
    let config_path = crate::util::grok_home::grok_home().join("config.toml");
    let v = is_claude_import_marked_at(&config_path);
    *MARKER_CACHE.write().expect("MARKER_CACHE poisoned") = Some(v);
    v
}

/// Reset the marker cache to uninitialised. Test-only.
#[cfg(test)]
pub(crate) fn reset_marker_cache_for_test() {
    *MARKER_CACHE.write().expect("MARKER_CACHE poisoned") = None;
}

/// Like [`is_claude_import_marked`], but logs a one-time `info!` line on the
/// first true result per process so users can see the runtime cutoff is active.
///
/// `gate_name` identifies which call site fired the cutoff (useful for
/// debugging which subsystem stopped reading `.claude/`).
///
/// Call sites are runtime fallback paths in `claude_compat.rs`,
/// `util/config.rs`, `util/hooks.rs`, and `agent/config.rs` that previously
/// read `.claude/`.
pub(crate) fn is_claude_import_marked_with_log(gate_name: &'static str) -> bool {
    static LOGGED: OnceLock<()> = OnceLock::new();
    let marked = is_claude_import_marked();
    if marked {
        LOGGED.get_or_init(|| {
            info!(
                first_gate = gate_name,
                "Claude compat disabled (marker set in config.toml)"
            );
        });
    }
    marked
}

/// Testable variant of [`is_claude_import_marked`] that reads from the given path.
pub(crate) fn is_claude_import_marked_at(config_path: &Path) -> bool {
    let content = match std::fs::read_to_string(config_path) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let value: TomlValue = match toml::from_str(&content) {
        Ok(v) => v,
        Err(_) => return false,
    };
    value
        .get("claude_compat")
        .and_then(|v| v.get("imported"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_claude_import_marked_at_missing_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("does-not-exist.toml");
        assert!(!is_claude_import_marked_at(&path));
    }

    #[test]
    fn is_claude_import_marked_at_missing_section() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[other]\nkey = \"value\"\n").unwrap();
        assert!(!is_claude_import_marked_at(&path));
    }

    #[test]
    fn is_claude_import_marked_at_explicit_false() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[claude_compat]\nimported = false\n").unwrap();
        assert!(!is_claude_import_marked_at(&path));
    }

    #[test]
    fn is_claude_import_marked_at_true() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[claude_compat]\nimported = true\n").unwrap();
        assert!(is_claude_import_marked_at(&path));
    }

    // The MARKER_CACHE is a process-global RwLock, so tests that touch it must
    // run serially.
    use serial_test::serial;

    /// RAII guard that resets the marker cache when dropped, so tests don't
    /// leak state into one another.
    pub(super) struct MarkerGuard;
    impl Drop for MarkerGuard {
        fn drop(&mut self) {
            reset_marker_cache_for_test();
            // Also clear the workspace-side env-var override so it doesn't
            // leak into subsequent tests.
            unsafe { std::env::remove_var("_GROK_CLAUDE_MARKER_OVERRIDE") };
        }
    }

    #[test]
    fn paths_config_deserializes() {
        let toml_str = r#"
[paths]
extra_skill_dirs = ["/a/skills", "/b/skills"]
extra_rule_dirs = ["/c/rules"]
"#;
        let value: TomlValue = toml::from_str(toml_str).unwrap();
        let paths = value.get("paths").unwrap();
        let cfg: crate::agent::config::PathsConfig = paths.clone().try_into().unwrap();
        assert_eq!(cfg.extra_skill_dirs, vec!["/a/skills", "/b/skills"]);
        assert_eq!(cfg.extra_rule_dirs, vec!["/c/rules"]);
    }

    #[test]
    fn paths_config_default_empty() {
        let cfg = crate::agent::config::PathsConfig::default();
        assert!(cfg.extra_skill_dirs.is_empty());
        assert!(cfg.extra_rule_dirs.is_empty());
    }

    #[test]
    #[serial]
    fn gate_marker_cache_unset_means_uses_disk() {
        // Sanity test: with the cache reset, `is_claude_import_marked()` must
        // (a) not panic and (b) populate the cache for subsequent reads.
        //
        // We intentionally **do not** assert a specific cached value — the
        // dev's real `~/.grok/config.toml` may legitimately have the marker
        // set during local testing, and we can't override `grok_home()`
        // (it's `OnceLock`-cached, so any prior test that calls it locks the
        // value in for the entire process). The `MarkerGuard` resets the
        // cache after this test, so subsequent gate tests start clean.
        let _g = MarkerGuard;
        reset_marker_cache_for_test();
        let _ = is_claude_import_marked();
        assert!(
            MARKER_CACHE
                .read()
                .expect("MARKER_CACHE poisoned")
                .is_some(),
            "cache should be populated after a call"
        );
    }
}
