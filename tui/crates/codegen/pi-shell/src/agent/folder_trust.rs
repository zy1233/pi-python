//! Folder-trust gate ("do you trust this folder?").
//!
//! Repo-local MCP / LSP servers and permission policy are configured by files
//! an attacker can ship inside a cloned repository (`.mcp.json`, project
//! `.grok/config.toml` including `[permission]` / `[mcp_servers]` /
//! `[plugins].paths`, `~/.claude.json` `projects.<cwd>`, project `.grok/lsp.json`).
//! Those configs contain commands or auto-approve rules the CLI would otherwise
//! honor automatically — a 1-click RCE / policy bypass. This module resolves a
//! VS-Code-style trust decision ONCE per workspace, BEFORE any repo-local
//! server is spawned, and exposes a cheap [`project_scope_allowed`] check that
//! the MCP/LSP/permission loaders consult.
//!
//! Resolution lives here (not in `acp_session`) so the session core stays free
//! of feature logic; the loaders only call [`project_scope_allowed`].
//!
//! The DECISION side — the workspace scan, the pure [`decide`] precedence, the
//! interactive prompt, and the durable [`pi_workspace::trust::TrustStore`]
//! reads/writes — lives in `pi-workspace` (client-side); this module keeps
//! the CONSUME/gating side (the `DECISIONS` cache, [`resolve_and_record`], and
//! the loader filters). The ordered trust precedence is documented canonically
//! on [`pi_workspace::folder_trust::decide`]; the consume-side nuance is
//! that two allows are PROVISIONAL (NOT cached): the "no repo configs" allow — so
//! configs appearing after the first resolve (git pull / agent write) are
//! re-checked on the next resolve rather than riding a stale grant — and the
//! unrecordable-key allow (cwd is $HOME / fs-root), which can never be persisted
//! anyway (see [`resolve_and_record_inner`] / [`compute`]).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use parking_lot::Mutex;
use pi_workspace::trust::{TrustStore, workspace_key};

// Decision-side (scan/decide/prompt/store) relocated to `pi-workspace`
// (client crate). `grant_folder_trust` is the ONLY moved item referenced from
// OUTSIDE this module (shell call sites + the pager's
// `pi_shell::agent::folder_trust::grant_folder_trust`), so only it is
// re-published; the rest are private imports used within this module. A glob
// re-export is deliberately avoided: it would silently re-publish the
// cache-SKIPPING `revoke_folder_trust_store` next to the real
// `revoke_folder_trust` wrapper, inviting a stale-untrust security bug.
pub use pi_workspace::folder_trust::grant_folder_trust;
use pi_workspace::folder_trust::{
    DecideInputs, TrustOutcome, decide, decide_inputs, feature_enabled, folder_trust_inert, persist_trust,
    prompt_for_trust,
};

use crate::util::config::{ RemoteSettings};

// NOTE: this folder-trust store (`~/.grok/trusted_folders.toml`) is SEPARATE
// from the pre-existing per-plugin trust store
// (`pi_agent::plugins::TrustStore` at `~/.grok/trusted-plugins`, plus the
// hooks' own project-trust gating). Trusting a folder here does NOT imply plugin
// trust and vice versa; the two are independent and non-contradicting.
// Unifying them is a tracked follow-up (out of scope for this PR).

/// Per-workspace resolved decision: `true` = repo-local (project-scoped)
/// servers are allowed to spawn. Keyed by canonical workspace key.
static DECISIONS: LazyLock<Mutex<HashMap<PathBuf, bool>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// Whether repo-local (project-scoped) MCP/LSP servers may spawn for `cwd`.
///
/// Authoritative and fail-closed, mirroring [`resolve_and_record_inner`]'s arms:
/// a cached **grant** short-circuits (allow); a cached **untrusted** verdict is
/// RE-READ against the store so a `grant_folder_trust` issued AFTER the untrusted
/// resolve is honored (it records the upgrade and allows); an **unrecorded** key
/// re-resolves via [`resolve_and_record`] — deny ONLY the dangerous case (feature
/// on AND repo-local code-exec configs present AND untrusted); allow no-configs /
/// unrecordable-key ($HOME / fs-root) / store-trusted / feature-off / inert. So
/// this never over-denies the common no-configs case, whose Trusted verdict is
/// provisional and therefore never cached.
///
/// The cache is consulted BEFORE delegating so a recorded `Some(false)` is
/// reconciled even on an inert build, where [`resolve_and_record`] would short-
/// circuit to allow before reaching the cache. `remote = None`: durable
/// feature-off / kill-switch verdicts are already cached by the launch/session
/// resolve that ran with the real RemoteSettings.
///
/// `DECISIONS` uses `parking_lot::Mutex` (no poisoning), so this gate cannot
/// fail OPEN on a poisoned lock.
pub(crate) fn project_scope_allowed(cwd: &Path) -> bool {
    let key = workspace_key(cwd);
    // Copy out of the lock so the Some(false) reconcile can re-acquire it
    // (parking_lot mutexes are not re-entrant).
    let cached = DECISIONS.lock().get(&key).copied();
    match cached {
        Some(true) => true,
        // Re-read the store so a grant issued after the untrusted resolve is
        // honored without a restart (mirrors `resolve_and_record_inner`).
        Some(false) => {
            if TrustStore::load().is_trusted(&key) {
                record(&key, true);
                true
            } else {
                false
            }
        }
        // Unrecorded: re-resolve fail-closed (no-configs / trusted / feature-off /
        // inert allow; untrusted + configs deny).
        None => resolve_and_record(cwd, None, false),
    }
}

fn record(workspace_key: &Path, allowed: bool) {
    DECISIONS
        .lock()
        .insert(workspace_key.to_path_buf(), allowed);
}

/// Resolve the trust decision for `cwd` ONCE and record it for the loaders.
///
/// Returns whether project-scoped servers are allowed. A cached **grant**
/// short-circuits; a cached **untrusted** verdict is re-checked against the
/// store so a later `--trust` grant is honored without a restart (see
/// [`resolve_and_record_inner`]). Persists on an accepted interactive prompt; an
/// explicit `--trust` grant is persisted up front by [`grant_folder_trust`].
///
/// `allow_prompt` must be `true` ONLY where a blocking stdin y/N read is safe —
/// i.e. agent `initialize` for the launch directory, before the TUI takes over
/// the terminal. Every other call site (per-session cwd, leader-served sessions
/// whose cwd differs from the launch dir, `grok mcp doctor`) passes `false`, so
/// an unresolved interactive-but-untrusted workspace resolves **fail-closed**
/// (untrusted, no prompt) — only the launch dir is ever prompted for.
pub(crate) fn resolve_and_record(
    cwd: &Path,
    remote: Option<&RemoteSettings>,
    allow_prompt: bool,
) -> bool {
    // Local/dev builds are fully inert: project scope is always allowed, so skip
    // the `trusted_folders.toml` read entirely.
    if folder_trust_inert() {
        return true;
    }
    let key = workspace_key(cwd);
    resolve_and_record_inner(
        &key,
        || TrustStore::load().is_trusted(&key),
        || compute(cwd, &key, remote, allow_prompt),
    )
}

/// Cache-reconciling core of [`resolve_and_record`], split out so the
/// invalidation path is testable without the process-global trust store.
///
/// - A cached **grant** (`Some(true)`) is durable and short-circuits — neither
///   `store_trusted` nor `recompute` runs.
/// - A cached **untrusted** verdict (`Some(false)`) is re-checked via
///   `store_trusted`: a `grok --trust` grant issued AFTER this workspace was
///   first resolved writes the store, so honor it on the next session without a
///   restart. Without this re-read a long-lived leader would mask the grant.
/// - An **unrecorded** key (`None`) does a full `recompute`, which reports
///   `(allowed, durable)`; the verdict is recorded ONLY when `durable`. The
///   provisional "no repo configs" allow is non-durable, so it stays unrecorded
///   and every resolve re-checks for code-exec config that appeared after the
///   folder was first opened (TOCTOU).
fn resolve_and_record_inner(
    key: &Path,
    store_trusted: impl FnOnce() -> bool,
    recompute: impl FnOnce() -> (bool, bool),
) -> bool {
    // Copy out of the lock so `record` below can re-acquire it (parking_lot
    // mutexes are not re-entrant).
    let cached = DECISIONS.lock().get(key).copied();
    match cached {
        Some(true) => true,
        Some(false) => {
            if store_trusted() {
                record(key, true);
                true
            } else {
                false
            }
        }
        None => {
            // Record only durable verdicts; a provisional no-configs allow is
            // left uncached so the next resolve re-checks `repo_configs_present`.
            let (allowed, durable) = recompute();
            if durable {
                record(key, allowed);
            }
            allowed
        }
    }
}

/// Returns `(allowed, durable)`. `durable` is whether the verdict may be cached.
/// Two allows are NON-durable: (1) the "no repo configs" allow, because
/// repo-local code-exec config can appear after this resolve (git pull / agent
/// write) and caching that provisional grant would let a later `/hooks reload` or
/// new session run the new code with no trust decision (TOCTOU); and (2) the
/// unrecordable-key allow (cwd is $HOME / fs-root), which the store can never
/// persist anyway. Store-trusted, feature-off, and an accepted prompt are
/// durable; an untrusted verdict is recorded so a later `--trust` grant can
/// reconcile it (see [`resolve_and_record_inner`]).
fn compute(
    cwd: &Path,
    key: &Path,
    remote: Option<&RemoteSettings>,
    allow_prompt: bool,
) -> (bool, bool) {
    let feature = feature_enabled(remote);
    let inputs = decide_inputs(cwd, key);
    compute_from_inputs(&inputs, feature, key, allow_prompt)
}

/// [`compute`] split at the gather: derive `(allowed, durable)` from an
/// already-gathered [`DecideInputs`] so a caller needing more than one verdict
/// (see [`resolve_launch_dir_trust`]) pays for the expensive `decide_inputs`
/// gather (store read + `repo_configs_present` scan) only ONCE.
fn compute_from_inputs(
    inputs: &DecideInputs,
    feature: bool,
    key: &Path,
    allow_prompt: bool,
) -> (bool, bool) {
    match decide(feature, inputs) {
        TrustOutcome::Trusted => {
            // Within the Trusted arm the non-durable ("provisional") allows are the
            // "no repo configs" rule and the unrecordable-key rule (Case 2: cwd is
            // $HOME / fs-root, which can never be persisted) — both are feature-on
            // and not store-trusted; feature-off and store-trusted are durable.
            // Leave the non-durable allows uncached.
            let durable = !feature || inputs.store_trusted;
            (true, durable)
        }
        TrustOutcome::Prompt if allow_prompt => {
            if prompt_for_trust(key) {
                // Reload the store (the inputs gather dropped its copy) to
                // persist the accepted prompt grant.
                persist_trust(&mut TrustStore::load(), key);
                (true, true)
            } else {
                (false, true)
            }
        }
        // Untrusted, OR interactive where prompting is unsafe here (TUI owns
        // stdin) — the agent-`initialize` path owns the launch-dir prompt. Both
        // resolve fail-closed.
        TrustOutcome::Untrusted | TrustOutcome::Prompt => (false, true),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    // Used only by the consume-side regression test below; imported here (not at
    // module scope) so the non-test build doesn't carry an unused import.
    use pi_workspace::folder_trust::repo_configs_present;

    /// A `git init`'d temp dir so `find_mcp_json_files` / `find_project_configs`
    /// (which discover the enclosing repo and walk to its root) are bounded to
    /// the temp dir instead of any ancestor repo the system temp dir lives in.
    fn repo_tmp() -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        git2::Repository::init(tmp.path()).unwrap();
        tmp
    }

    /// Simulate a release-stamped build so the folder-trust gate engages: an
    /// unstamped local/dev build auto-trusts and never gates/persists. Hold the
    /// returned guard for the test body (drop restores the prior value).
    fn simulate_release_build() -> EnvGuard {
        EnvGuard::set(pi_version::TEST_VERSION_ENV, "0.0.0-sim")
    }

    #[test]
    fn record_and_lookup_round_trip() {
        // `repo_tmp` git-inits the dir so `workspace_key` yields a unique key
        // (it returns the git-repo root, which would otherwise collapse to a
        // shared ambient root if `$TMPDIR` is inside a checkout). The
        // `DECISIONS` map is process-global, so unique keys keep parallel tests
        // from clobbering each other's recorded decisions.
        let tmp = repo_tmp();
        let key = tmp.path().to_path_buf();
        // A fresh, never-recorded key re-resolves fail-closed and is allowed here
        // (inert local build / no repo configs — never a durable default-open).
        assert!(project_scope_allowed(&key));
        record(&workspace_key(&key), false);
        assert!(!project_scope_allowed(&key));
    }

    #[test]
    #[serial_test::serial]
    fn envrc_gate_drops_untrusted_then_loads_when_store_trusted() {
        let _sim = simulate_release_build();
        // The `.envrc` load sites gate on the folder-trust verdict: an
        // `.envrc`-only untrusted clone resolves false (so the call site loads an
        // empty env), while a store-trusted folder resolves true and the loader
        // actually reads `.envrc`. GROK_HOME-isolated so the trust store is empty;
        // GROK_FOLDER_TRUST unset so the default-on feature flag applies.
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        std::fs::write(tmp.path().join(".envrc"), "export GATED_ENVRC=1\n").unwrap();

        // Untrusted: the gate verdict is false => the call site skips loading.
        assert!(
            !resolve_and_record(tmp.path(), None, false),
            "untrusted `.envrc` clone must gate the load"
        );

        // Store-trust the folder => the verdict reconciles to true and the loader runs.
        let mut store = TrustStore::load();
        store.set_trusted(&workspace_key(tmp.path())).unwrap();
        assert!(resolve_and_record(tmp.path(), None, false));
        let env = pi_workspace::envrc::load_envrc_or_empty(tmp.path());
        assert_eq!(
            env.get("GATED_ENVRC"),
            Some(&"1".to_string()),
            "trusted folder must load `.envrc`"
        );
    }

    #[test]
    #[serial_test::serial]
    fn claude_env_gate_drops_project_env_when_untrusted() {
        let _sim = simulate_release_build();
        // The `.claude/settings.json` env load site mirrors
        // `load_claude_env_with_project(cwd, project_scope_allowed(cwd))`: an
        // untrusted clone's repo-tree env (which would feed BASH_ENV /
        // GIT_SSH_COMMAND / … to every subprocess) is dropped; a store-trusted
        // folder merges it. GROK_HOME-isolated so the trust store is empty;
        // GROK_FOLDER_TRUST unset so the default-on feature flag applies.
        use pi_workspace::permission::claude_settings::load_claude_env_with_project;
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        let claude = tmp.path().join(".claude");
        std::fs::create_dir_all(&claude).unwrap();
        std::fs::write(
            claude.join("settings.json"),
            r#"{"env": {"REPO_TREE_ENV_GATED": "1"}}"#,
        )
        .unwrap();

        // Untrusted: verdict false => the repo-tree env is dropped.
        assert!(!resolve_and_record(tmp.path(), None, false));
        let untrusted =
            load_claude_env_with_project(tmp.path(), resolve_and_record(tmp.path(), None, false));
        assert!(
            !untrusted.contains_key("REPO_TREE_ENV_GATED"),
            "untrusted folder must drop repo-tree .claude env"
        );

        // Store-trust => verdict reconciles to true => repo-tree env merged.
        let mut store = TrustStore::load();
        store.set_trusted(&workspace_key(tmp.path())).unwrap();
        assert!(resolve_and_record(tmp.path(), None, false));
        let trusted =
            load_claude_env_with_project(tmp.path(), resolve_and_record(tmp.path(), None, false));
        assert_eq!(
            trusted.get("REPO_TREE_ENV_GATED"),
            Some(&"1".to_string()),
            "trusted folder must merge repo-tree .claude env"
        );
    }

    #[test]
    #[serial_test::serial]
    fn claude_env_gate_drops_subdir_project_env_when_untrusted() {
        let _sim = simulate_release_build();
        // RCE regression (subdir bypass): a `.claude/settings.json` with `env` in
        // a SUBDIR — the ONLY repo config — launched from that subdir must flip the
        // folder untrusted AND have its env dropped. The env loader walks
        // cwd→repo-root, so detection MUST walk too (a git-root-only probe missed
        // this). GROK_HOME-isolated so the trust store is empty.
        use pi_workspace::permission::claude_settings::load_claude_env_with_project;
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        let subdir = tmp.path().join("sub");
        let claude = subdir.join(".claude");
        std::fs::create_dir_all(&claude).unwrap();
        std::fs::write(
            claude.join("settings.json"),
            r#"{"env": {"SUBDIR_REPO_ENV_GATED": "1"}}"#,
        )
        .unwrap();

        // Detection now walks cwd→root, so the subdir-only `.claude` is detected
        // and the folder resolves untrusted.
        assert!(
            repo_configs_present(&subdir),
            "subdir .claude/settings.json must be detected"
        );
        assert!(!resolve_and_record(&subdir, None, false));
        let untrusted =
            load_claude_env_with_project(&subdir, resolve_and_record(&subdir, None, false));
        assert!(
            !untrusted.contains_key("SUBDIR_REPO_ENV_GATED"),
            "untrusted subdir folder must drop its repo-tree .claude env"
        );

        // Granting trust re-enables it (proves the env file is real/loadable).
        let mut store = TrustStore::load();
        store.set_trusted(&workspace_key(&subdir)).unwrap();
        assert!(resolve_and_record(&subdir, None, false));
        let trusted =
            load_claude_env_with_project(&subdir, resolve_and_record(&subdir, None, false));
        assert_eq!(
            trusted.get("SUBDIR_REPO_ENV_GATED"),
            Some(&"1".to_string()),
            "trusted folder must merge the subdir repo-tree .claude env"
        );
    }

    use pi_test_support::EnvGuard;

    #[test]
    #[serial_test::serial]
    fn project_scope_allowed_denies_untrusted_repo_with_configs() {
        // Fail-closed (the dangerous case): a release-stamped build with the
        // feature on by default, an untrusted folder that ships repo-local
        // code-exec config (here `.grok/hooks`), and no store grant must be
        // DENIED — even though no verdict was recorded first (the gate re-resolves
        // fail-closed rather than defaulting open). GROK_HOME-isolated (empty
        // store); GROK_FOLDER_TRUST unset so the default-on flag applies.
        let _sim = simulate_release_build();
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        std::fs::create_dir_all(tmp.path().join(".grok").join("hooks")).unwrap();
        assert!(
            !project_scope_allowed(tmp.path()),
            "untrusted folder with repo configs must be denied (fail-closed)"
        );
    }

    #[test]
    #[serial_test::serial]
    fn project_scope_allowed_allows_repo_without_configs() {
        // The over-deny guard: a folder with NO repo-local code-exec config has
        // nothing to gate, so it must be ALLOWED even though its (provisional)
        // Trusted verdict is never cached — a naive `.unwrap_or(false)` cache peek
        // would wrongly deny it. Release-stamped + GROK_HOME-isolated so the
        // verdict comes from `decide` rule 4 (no repo configs), not the inert
        // short-circuit.
        let _sim = simulate_release_build();
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        assert!(
            project_scope_allowed(tmp.path()),
            "a folder with no repo configs must be allowed (no over-deny)"
        );
    }

    #[test]
    #[serial_test::serial]
    fn project_scope_allowed_allows_store_trusted_repo() {
        // A folder the user explicitly trusted is ALLOWED even with repo-local
        // configs present. GROK_HOME-isolated so the seeded store is the temp one;
        // GROK_FOLDER_TRUST unset so the default-on flag applies.
        let _sim = simulate_release_build();
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        std::fs::create_dir_all(tmp.path().join(".grok").join("hooks")).unwrap();
        let mut store = TrustStore::load();
        store.set_trusted(&workspace_key(tmp.path())).unwrap();
        assert!(
            project_scope_allowed(tmp.path()),
            "a store-trusted folder must be allowed"
        );
    }

    #[test]
    #[serial_test::serial]
    fn project_scope_allowed_allows_inert_local_build() {
        // On a local/dev build the whole feature is inert (auto-trust): a folder
        // with repo-local configs and an empty store is still ALLOWED. Assert only
        // when compiled unstamped (mirrors the inert tests elsewhere), with
        // GROK_TEST_VERSION unset so `is_local_build()` is genuinely true.
        let _unset_ver = EnvGuard::unset(pi_version::TEST_VERSION_ENV);
        if option_env!("GROK_VERSION").is_some() {
            return; // a release-stamped test binary is not a local build
        }
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let tmp = repo_tmp();
        std::fs::create_dir_all(tmp.path().join(".grok").join("hooks")).unwrap();
        assert!(
            project_scope_allowed(tmp.path()),
            "inert local/dev build must allow project scope even with configs"
        );
    }

    #[test]
    #[serial_test::serial]
    fn project_scope_allowed_denies_untrusted_plugin_only_repo() {
        // A plugin-only untrusted repo (just `.grok/plugins/<x>/`, no
        // hooks/MCP/LSP, no store grant) is repo-controlled code-exec and must be
        // DENIED — the verdict the shell plugin call sites feed into
        // discover_plugins/build_for_cwd/reload. GROK_HOME-isolated (empty store);
        // GROK_FOLDER_TRUST unset so the default-on flag applies.
        let _sim = simulate_release_build();
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        std::fs::create_dir_all(tmp.path().join(".grok").join("plugins").join("evil")).unwrap();
        assert!(
            !project_scope_allowed(tmp.path()),
            "plugin-only untrusted repo must be denied"
        );
    }

    #[test]
    #[serial_test::serial]
    fn project_scope_allowed_denies_untrusted_permission_only_repo() {
        // Bridge: a clone whose ONLY repo-local config is `.grok/config.toml`
        // `[permission]` (no MCP/hooks/plugins) must still produce untrusted via
        // the real `repo_configs_present` → `decide` → `project_scope_allowed`
        // path. Resolver unit tests inject `project_trusted = false` directly and
        // miss this detector gap. Subdir launch ensures the cwd→git-root walk.
        let _sim = simulate_release_build();
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        let grok = tmp.path().join(".grok");
        std::fs::create_dir_all(&grok).unwrap();
        std::fs::write(
            grok.join("config.toml"),
            "[permission]\nallow = [\"Bash(*)\"]\n",
        )
        .unwrap();
        let subdir = tmp.path().join("crates").join("inner");
        std::fs::create_dir_all(&subdir).unwrap();
        assert!(
            !project_scope_allowed(&subdir),
            "permission-only untrusted repo must be denied from a subdirectory"
        );
    }

    #[test]
    #[serial_test::serial]
    fn kill_switch_allows_untrusted_repo_after_authoritative_resolve() {
        // Regression (chat/load-path kill-switch): an untrusted folder WITH repo
        // configs under a remote kill-switch (folder_trust_enabled = Some(false))
        // must resolve ALLOWED. The session spawn path resolves once with the real
        // RemoteSettings before any gate read, so the gate cache-hits that verdict.
        // GROK_HOME-isolated (empty store); GROK_FOLDER_TRUST unset so the kill-switch
        // is the only signal.
        let _sim = simulate_release_build();
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let remote = RemoteSettings {
            folder_trust_enabled: Some(false),
            ..Default::default()
        };

        let tmp = repo_tmp();
        std::fs::create_dir_all(tmp.path().join(".grok").join("hooks")).unwrap();
        assert!(
            resolve_and_record(tmp.path(), Some(&remote), false),
            "kill-switch (feature off) must resolve trusted even with repo configs"
        );
        assert!(
            project_scope_allowed(tmp.path()),
            "gate must allow a kill-switched folder once the authoritative resolve ran"
        );

        // Contrast: a cold `remote = None` gate read (no prior authoritative resolve)
        // misses the kill-switch and denies the same scenario — the exact gap the
        // up-front spawn resolve closes for chat/load sessions.
        let cold = repo_tmp();
        std::fs::create_dir_all(cold.path().join(".grok").join("hooks")).unwrap();
        assert!(
            !project_scope_allowed(cold.path()),
            "cold remote=None gate read denies a kill-switched folder (regression contrast)"
        );
    }

    #[test]
    #[serial_test::serial]
    fn build_for_cwd_with_trust_verdict_gates_active_project_plugin() {
        let _sim = simulate_release_build();
        // Pins the SHELL plugin wiring end-to-end: the call-site expression
        // `build_for_cwd(cwd, &cfg, dirs, <folder-trust verdict>)` must keep an
        // ENABLED project plugin OUT of `active_plugins()` while the folder is
        // untrusted, and let it in after `grant_folder_trust`. The
        // verdict/discovery/registry unit tests alone do NOT catch a silent
        // un-gating here.
        //
        // GROK_HOME-isolated so both the folder-trust store and the plugin trust
        // store start empty (deterministic untrusted); GROK_FOLDER_TRUST unset so
        // the default-on flag applies; `#[serial]` because both are process-global.
        use pi_agent::plugins::discovery::DiscoveryConfig;
        use pi_agent::plugins::{PluginRegistry, SharedPluginRegistryHandle};
        let home = tempfile::tempdir().unwrap();
        let _env = EnvGuard::set("GROK_HOME", home.path());
        let _flag = EnvGuard::unset("GROK_FOLDER_TRUST");
        let tmp = repo_tmp();
        // A project plugin. Project scope is default-disabled, so name it in the
        // `enabled` list to isolate the TRUST gate (not the enable gate).
        let plugin = tmp.path().join(".grok").join("plugins").join("trustgate");
        std::fs::create_dir_all(&plugin).unwrap();
        std::fs::write(plugin.join("plugin.json"), r#"{"name":"trustgate"}"#).unwrap();
        let cfg = DiscoveryConfig {
            enabled: vec!["trustgate".to_string()],
            ..Default::default()
        };
        let handle = SharedPluginRegistryHandle::new(None, vec![]);
        // Name-specific so ambient user plugins on the test host are irrelevant.
        let active_has = |reg: Option<std::sync::Arc<PluginRegistry>>| {
            reg.is_some_and(|r| r.active_plugins().iter().any(|p| p.name == "trustgate"))
        };

        // Untrusted: the verdict is false => the plugin is still discovered but
        // not trusted, so it is absent from `active_plugins()`.
        let untrusted = handle.build_for_cwd(
            tmp.path(),
            &cfg,
            &[],
            resolve_and_record(tmp.path(), None, false),
        );
        assert!(
            !active_has(untrusted),
            "untrusted folder: enabled project plugin must be gated out of active_plugins()"
        );

        // Grant trust, recompute the same expression => the plugin becomes active.
        grant_folder_trust(tmp.path());
        let trusted = handle.build_for_cwd(
            tmp.path(),
            &cfg,
            &[],
            resolve_and_record(tmp.path(), None, false),
        );
        assert!(
            active_has(trusted),
            "granted folder: enabled project plugin must appear in active_plugins()"
        );
    }

    #[test]
    fn load_servers_sourced_tags_project_lsp_json() {
        use pi_tools::implementations::lsp::config::load_servers_with_plugins_sourced;
        use pi_tools::types::config_source::ConfigSource;

        // A `<cwd>/.grok/lsp.json` server must be tagged `Project` so the gate
        // can distinguish it from user/plugin servers. Asserts on the specific
        // key, so any real `~/.grok/lsp.json` on the test host is irrelevant.
        let tmp = repo_tmp();
        let grok = tmp.path().join(".grok");
        std::fs::create_dir_all(&grok).unwrap();
        std::fs::write(grok.join("lsp.json"), r#"{"projlsp": {"command": "true"}}"#).unwrap();

        let sourced = load_servers_with_plugins_sourced(tmp.path(), &[], &[], &[], &[]);
        let (_, source) = sourced.get("projlsp").expect("project server present");
        assert!(
            matches!(source, ConfigSource::Project { .. }),
            "project lsp.json must be tagged Project; got {source:?}"
        );
    }

    #[test]
    fn cached_grant_short_circuits_without_store_read() {
        let tmp = repo_tmp();
        let key = workspace_key(tmp.path());
        record(&key, true);
        // A cached grant must consult neither the store nor a recompute.
        let allowed = resolve_and_record_inner(
            &key,
            || panic!("store must not be read for a cached grant"),
            || panic!("must not recompute for a cached grant"),
        );
        assert!(allowed);
    }

    #[test]
    fn cached_untrusted_is_upgraded_by_a_later_grant() {
        let tmp = repo_tmp();
        let key = workspace_key(tmp.path());
        record(&key, false);
        assert!(!project_scope_allowed(tmp.path()));
        // Simulate a `grok --trust` grant landing in the store after the
        // untrusted verdict was cached: the re-read sees trusted, so the next
        // resolve upgrades the cache without a process restart.
        let allowed = resolve_and_record_inner(
            &key,
            || true,
            || panic!("must not recompute when reconciling from the store"),
        );
        assert!(allowed);
        // The cheap gate the merge consults now allows (cache refreshed).
        assert!(project_scope_allowed(tmp.path()));
    }

    #[test]
    fn cached_untrusted_stays_untrusted_when_store_still_denies() {
        let tmp = repo_tmp();
        let key = workspace_key(tmp.path());
        record(&key, false);
        let allowed =
            resolve_and_record_inner(&key, || false, || panic!("must not recompute when cached"));
        assert!(!allowed);
        assert!(!project_scope_allowed(tmp.path()));
    }

    #[test]
    fn unrecorded_key_recomputes_then_caches() {
        let tmp = repo_tmp();
        let key = workspace_key(tmp.path());
        // No prior record → recompute and record the (durable untrusted) verdict.
        let allowed = resolve_and_record_inner(
            &key,
            || panic!("store re-read is only for a cached untrusted verdict"),
            || (false, true),
        );
        assert!(!allowed);
        assert!(!project_scope_allowed(tmp.path()));
        // Now cached untrusted: a later resolve re-reads the store (still denies).
        let allowed =
            resolve_and_record_inner(&key, || false, || panic!("must not recompute when cached"));
        assert!(!allowed);
    }

    #[test]
    fn explicit_child_untrust_survives_reload_despite_ancestor_trust() {
        // Untrust-undone-on-reload: an ancestor is trusted and the child is
        // explicitly untrusted, so a reload must NOT re-promote the child. revoke
        // downgrades the cache to untrusted; the reconcile re-reads the store,
        // which now honors the most-specific (child) decision and stays untrusted
        // — the ancestor's cascade no longer undoes the explicit untrust.
        let tmp = tempfile::tempdir().unwrap();
        let store_path = tmp.path().join(pi_workspace::trust::TRUST_FILE_NAME);
        let parent = tmp.path().join("parent");
        let child = parent.join("child");
        std::fs::create_dir_all(&child).unwrap();

        let mut store = TrustStore::load_from(store_path);
        store.set_trusted(&parent).unwrap();
        store.set_untrusted(&child).unwrap();

        // revoke downgraded the in-process cache for the child.
        record(&child, false);

        let allowed = resolve_and_record_inner(
            &child,
            || store.is_trusted(&child),
            || panic!("must not recompute when reconciling a cached untrusted verdict"),
        );
        assert!(
            !allowed,
            "explicit child untrust must survive the ancestor's trust on reload"
        );
    }

    #[test]
    #[serial_test::serial]
    fn no_configs_trust_is_provisional_and_regated_when_configs_appear() {
        // F5 regression: the "no repo configs => Trusted" verdict is PROVISIONAL,
        // so it must NOT be cached as a durable grant. Otherwise a clone that is
        // empty when first resolved, then gains a code-exec config (git pull /
        // agent write), would short-circuit the stale grant and load+run the new
        // hooks/plugins ungated on the next /hooks reload or new session (TOCTOU).
        //
        // Drives the real `resolve_and_record` + `project_scope_allowed`. Force
        // the feature on via env (highest precedence) so the test does not depend
        // on the host's folder-trust config.
        unsafe { std::env::set_var("GROK_FOLDER_TRUST", "1") };
        // Simulate a release-stamped build: an unstamped local/dev build (as in CI,
        // no GROK_VERSION) auto-trusts, so the gate would never engage without this.
        unsafe { std::env::set_var(pi_version::TEST_VERSION_ENV, "0.0.0-sim") };
        let tmp = repo_tmp();

        // Empty repo: nothing to gate => allowed, but left UNRECORDED (provisional).
        assert!(resolve_and_record(tmp.path(), None, false));
        assert!(
            DECISIONS.lock().get(&workspace_key(tmp.path())).is_none(),
            "provisional no-configs allow must not be cached as a durable grant"
        );

        // A repo-local code-exec config appears after the first resolve.
        std::fs::create_dir_all(tmp.path().join(".grok").join("hooks")).unwrap();

        // The next resolve re-checks `repo_configs_present` (no stale grant to
        // ride) => headless untrusted, so the newly-added hooks are now gated.
        assert!(
            !resolve_and_record(tmp.path(), None, false),
            "configs that appear after the first resolve must be re-checked and gated"
        );
        assert!(!project_scope_allowed(tmp.path()));

        unsafe { std::env::remove_var(pi_version::TEST_VERSION_ENV) };
        unsafe { std::env::remove_var("GROK_FOLDER_TRUST") };
    }

}
