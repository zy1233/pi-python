use toml::Value as TomlValue;

#[cfg(test)]
mod login_shell_capture_tests {
        use toml::Value as TomlValue;

    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn cfg(enabled: bool) -> TomlValue {
        toml::from_str(&format!(
            "[toolset.bash]\nlogin_shell_capture = {enabled}\n"
        ))
        .unwrap()
    }

}

const ENV_SCHEDULER_BACKGROUND_LOOPS: &str = "GROK_SCHEDULER_BACKGROUND_LOOPS";

fn scheduler_background_loops_from_toml(v: Option<&TomlValue>) -> Option<bool> {
    v?.get("scheduler")?.get("background_loops")?.as_bool()
}

/// Resolve whether scheduled task fires run in background loop subagents.
///
/// Precedence: requirements > env (`GROK_SCHEDULER_BACKGROUND_LOOPS`) > user
/// `config.toml` `[scheduler] background_loops` > managed layers > remote
/// settings > default `true`.
pub fn resolve_scheduler_background_loops(remote: Option<bool>) -> bool {
    let requirements = crate::config::load_merged_requirements();
    let layers = match crate::config::ConfigLayers::load() {
        Ok(l) => Some(l),
        Err(e) => {
            tracing::warn!(error = %e, "scheduler_background_loops: failed to load config layers");
            None
        }
    };
    resolve_scheduler_background_loops_tiers(
        requirements.as_ref(),
        layers.as_ref().map(|l| &l.user),
        layers.as_ref().map(|l| &l.managed),
        layers.as_ref().map(|l| &l.system_managed),
        remote,
    )
}

fn resolve_scheduler_background_loops_tiers(
    requirements: Option<&TomlValue>,
    user: Option<&TomlValue>,
    managed: Option<&TomlValue>,
    system_managed: Option<&TomlValue>,
    remote: Option<bool>,
) -> bool {
    use crate::agent::config::BoolFlag;
    BoolFlag::env(ENV_SCHEDULER_BACKGROUND_LOOPS)
        .requirement(scheduler_background_loops_from_toml(requirements))
        .config(scheduler_background_loops_from_toml(user))
        .managed(
            scheduler_background_loops_from_toml(managed)
                .or_else(|| scheduler_background_loops_from_toml(system_managed)),
        )
        .feature_flag(remote)
        .default(true)
        .resolve()
        .value
}

#[cfg(test)]
mod scheduler_background_loops_tests {
    use super::{ENV_SCHEDULER_BACKGROUND_LOOPS, resolve_scheduler_background_loops_tiers};
    use toml::Value as TomlValue;

    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    fn guard() -> std::sync::MutexGuard<'static, ()> {
        let g = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        unsafe { std::env::remove_var(ENV_SCHEDULER_BACKGROUND_LOOPS) };
        g
    }

    fn cfg(enabled: bool) -> TomlValue {
        toml::from_str(&format!("[scheduler]\nbackground_loops = {enabled}\n")).unwrap()
    }

    #[test]
    fn defaults_on() {
        let _g = guard();
        assert!(resolve_scheduler_background_loops_tiers(
            None, None, None, None, None
        ));
    }

    #[test]
    fn remote_flag_can_disable() {
        let _g = guard();
        assert!(!resolve_scheduler_background_loops_tiers(
            None,
            None,
            None,
            None,
            Some(false)
        ));
    }

    #[test]
    fn user_config_beats_remote() {
        let _g = guard();
        assert!(resolve_scheduler_background_loops_tiers(
            None,
            Some(&cfg(true)),
            None,
            None,
            Some(false)
        ));
        assert!(!resolve_scheduler_background_loops_tiers(
            None,
            Some(&cfg(false)),
            None,
            None,
            Some(true)
        ));
    }

    #[test]
    fn env_beats_config_and_remote() {
        let _g = guard();
        unsafe { std::env::set_var(ENV_SCHEDULER_BACKGROUND_LOOPS, "0") };
        let off = resolve_scheduler_background_loops_tiers(
            None,
            Some(&cfg(true)),
            None,
            None,
            Some(true),
        );
        unsafe { std::env::remove_var(ENV_SCHEDULER_BACKGROUND_LOOPS) };
        assert!(!off);
    }

    #[test]
    fn requirements_win_outright() {
        let _g = guard();
        unsafe { std::env::set_var(ENV_SCHEDULER_BACKGROUND_LOOPS, "1") };
        let off = resolve_scheduler_background_loops_tiers(
            Some(&cfg(false)),
            Some(&cfg(true)),
            None,
            None,
            Some(true),
        );
        unsafe { std::env::remove_var(ENV_SCHEDULER_BACKGROUND_LOOPS) };
        assert!(!off);
    }
}

/// `[toolset.web_search]` domain keys. Read here from the raw config layers, not
/// through `ShellToolsetConfig::web_search` (a `SamplerConfig`, which has no such
/// fields), so they must be exempt from the unrecognized-key scan.
pub(crate) const WEB_SEARCH_DOMAIN_CONFIG_PATHS: &[&str] = &[
    "toolset.web_search.allowed_domains",
    "toolset.web_search.excluded_domains",
];

#[cfg(test)]
mod web_search_domains_tests {
    

}

#[cfg(test)]
mod ask_user_question_timeout_tests {
    
    
    

    // Both env vars are process-global (a dev exports the secs var for TUI
    // repro); serialize and force them unset so these tests can't go flaky.
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

}

#[cfg(test)]
mod tests {
    

}

#[cfg(test)]
mod shell_env_policy_tests {
    
    

}
