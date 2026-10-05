//! Unit tests for [`super::manager::AuthManager`]. Extracted from
//! `manager.rs` so the implementation reads top-to-bottom; wired in
//! via `#[path = "manager_tests.rs"] mod tests;` in manager.rs.

use super::*;
use crate::auth::error::RefreshTokenError;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Instant;

fn make_auth(expires_at: Option<DateTime<Utc>>, create_time: DateTime<Utc>) -> GrokAuth {
    GrokAuth {
        auth_mode: AuthMode::External,
        create_time,
        user_id: String::new(),
        expires_at,
        ..GrokAuth::test_default()
    }
}

#[test]
fn expired_within_5min_buffer() {
    let auth = make_auth(Some(Utc::now() + Duration::minutes(4)), Utc::now());
    assert!(is_expired(&auth));
}

#[test]
fn fallback_ttl_when_no_expires_at() {
    let old = Utc::now() - Duration::days(30) + Duration::minutes(4);
    let auth = make_auth(None, old);
    assert!(is_expired(&auth));

    let recent = Utc::now() - Duration::days(29);
    let auth = make_auth(None, recent);
    assert!(!is_expired(&auth));
}

#[tokio::test]
async fn refresh_path_lock_acquire_attaches_the_heartbeat() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let outcome = mgr
        .acquire_refresh_lock_or_adopt(RefreshReason::PreRequest)
        .await
        .expect("uncontended refresh-lock acquire");
    let super::refresh_chain::LockOutcome::Held(guard) = outcome else {
        panic!("an empty auth dir has no sibling token to adopt");
    };
    assert!(
        guard.heartbeat.is_some(),
        "the refresh-path hold must carry the heartbeat that placates old binaries"
    );
}

#[cfg(unix)]
#[tokio::test]
async fn lock_loss_revalidation_adopts_the_sibling_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let guard = mgr
        .try_lock_auth_file_async(REFRESH_LOCK_TIMEOUT, lock::Heartbeat::Attach)
        .await
        .into_guard()
        .expect("initial acquire");
    let lock_path = dir.path().join("auth.json.lock");
    std::fs::remove_file(&lock_path).unwrap();
    std::fs::write(&lock_path, b"").unwrap();

    let fresh_disk = GrokAuth {
        key: "fresh-key-from-sibling".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("new-rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, fresh_disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let outcome = mgr
        .revalidate_lock_or_reacquire(guard, RefreshReason::PreRequest)
        .await
        .expect("lock-loss revalidation must re-acquire on the live inode");
    let super::refresh_chain::LockOutcome::Adopted(adopted) = outcome else {
        panic!("a sibling token persisted during lock loss must be adopted");
    };
    assert_eq!(adopted.key, "fresh-key-from-sibling");
}

#[test]
fn has_usable_token_covers_memory_and_disk() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    assert!(!mgr.has_usable_token(), "nothing in memory or on disk");

    mgr.hot_swap(make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now()));
    assert!(!mgr.has_usable_disk_token(), "disk still empty");
    assert!(mgr.has_usable_token(), "valid in-memory token is usable");

    mgr.persist_and_swap(make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now()));
    mgr.hot_swap(make_auth(Some(Utc::now() - Duration::hours(1)), Utc::now()));
    assert!(mgr.current().is_none(), "in-memory token is expired");
    assert!(mgr.has_usable_token(), "fresh disk token keeps it usable");

    mgr.persist_and_swap(make_auth(Some(Utc::now() - Duration::hours(1)), Utc::now()));
    assert!(
        !mgr.has_usable_token(),
        "expired in memory and on disk is not usable"
    );
}

#[test]
fn auth_scope_uses_oauth2_when_present() {
    let cfg = GrokComConfig::default();
    // Default config always has oauth2 set to the pi defaults.
    assert_eq!(
        cfg.auth_scope(),
        format!(
            "{}::{}",
            crate::auth::config::PI_OAUTH2_ISSUER,
            obfstr::obfstr!("b1a00492-073a-47ea-816f-4c329264a828"),
        )
    );
}

#[test]
fn legacy_scope_fallback_reads_old_auth_json() {
    let dir = tempfile::tempdir().unwrap();
    let auth_path = dir.path().join("auth.json");

    // Write auth.json with the legacy scope key (as `x setup` copies from
    // a machine that was authenticated with an older grok version).
    let legacy_auth = make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now());
    let mut store = AuthStore::new();
    store.insert(LEGACY_SCOPE.to_string(), legacy_auth);
    write_auth_json(&auth_path, &store).unwrap();

    // AuthManager uses the new OAuth2 scope, but should still find the
    // token under the legacy key.
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));
    let current = mgr.current();
    assert!(current.is_some(), "should fall back to legacy scope key");
    assert_eq!(current.unwrap().key, "test-key");
}

#[test]
fn new_scope_takes_precedence_over_legacy() {
    let dir = tempfile::tempdir().unwrap();
    let auth_path = dir.path().join("auth.json");

    let legacy_auth = GrokAuth {
        key: "legacy-key".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    let new_auth = GrokAuth {
        key: "new-key".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };

    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();

    let mut store = AuthStore::new();
    store.insert(LEGACY_SCOPE.to_string(), legacy_auth);
    store.insert(scope, new_auth);
    write_auth_json(&auth_path, &store).unwrap();

    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));
    let current = mgr.current().expect("should find auth");
    assert_eq!(current.key, "new-key", "new scope should take precedence");
}

// -- Near-expiry (5-minute buffer) behavior ------------------------

/// Regression test: a token within the 5-minute early-invalidation buffer
/// must be invisible to `current()` (returns None) but visible to
/// `expired_auth()` so that callers can attempt a silent refresh.
#[test]
fn near_expiry_token_invisible_to_current_visible_to_expired_auth() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // Token expires in 3 minutes -- inside the 5-minute buffer.
    let near_expiry = GrokAuth {
        key: "near-expiry-key".into(),
        user_id: "user-1".into(),
        email: Some("user@test.com".into()),
        refresh_token: Some("rt-valid".into()),
        expires_at: Some(Utc::now() + Duration::minutes(3)),
        oidc_issuer: Some("https://idp.example.com".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(near_expiry);

    // current() must return None (token is "expired" per buffer)
    assert!(
        mgr.current().is_none(),
        "current() should return None for token within 5-min buffer"
    );

    // is_expired() must return true
    assert!(
        mgr.is_expired(),
        "is_expired() should be true for token within 5-min buffer"
    );

    // expired_auth() must return the token so refresh can use it
    let expired = mgr.expired_auth();
    assert!(
        expired.is_some(),
        "expired_auth() should return the near-expiry token"
    );
    assert_eq!(expired.as_ref().unwrap().key, "near-expiry-key");
    assert_eq!(
        expired.as_ref().unwrap().refresh_token.as_deref(),
        Some("rt-valid"),
        "refresh_token must be preserved for silent refresh"
    );
}

#[tokio::test]
async fn update_preserves_other_scope_entries() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg.clone()));

    // Pre-populate with an external auth entry
    let external = GrokAuth {
        key: "external-key".into(),
        auth_mode: AuthMode::External,
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    {
        let mut map = AuthStore::new();
        map.insert("other-scope".into(), external);
        write_auth_json(&dir.path().join("auth.json"), &map).unwrap();
    }

    // Now update via auth_manager
    let new_auth = GrokAuth {
        key: "oidc-token".into(),
        auth_mode: AuthMode::Oidc,
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    mgr.update(new_auth).await.unwrap();

    // Both entries should exist
    let store = read_auth_json(&dir.path().join("auth.json")).unwrap();
    assert!(store.contains_key("other-scope"));
    assert!(store.contains_key(&cfg.auth_scope()));
}

/// Regression: when auth.json contains corrupt JSON, update() must not
/// clobber the file with a single-entry map. Instead it should update
/// in-memory only and leave the file untouched.
#[tokio::test]
async fn update_recovers_from_corrupt_auth_json_by_backing_up_old_file() {
    let dir = tempfile::tempdir().unwrap();
    let auth_path = dir.path().join("auth.json");
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg.clone()));

    let bad_content = b"NOT VALID JSON {{{";
    std::fs::write(&auth_path, bad_content).unwrap();

    let new_auth = GrokAuth {
        key: "fresh-token".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("fresh-rt".into()),
        user_id: "fresh-user".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };

    let result = mgr.update(new_auth).await;
    assert!(
        result.is_ok(),
        "update must succeed and persist after corrupt recovery: {result:?}"
    );

    let current = mgr.current();
    assert_eq!(
        current.as_ref().map(|a| a.key.as_str()),
        Some("fresh-token")
    );

    let on_disk_raw = std::fs::read_to_string(&auth_path).unwrap();
    assert!(
        on_disk_raw.contains("fresh-token"),
        "auth.json must contain the new credential after recovery, got: {on_disk_raw}"
    );
    let on_disk: AuthStore =
        serde_json::from_str(&on_disk_raw).expect("auth.json must be valid JSON after recovery");
    assert!(on_disk.contains_key(&cfg.auth_scope()));

    let mut backup_found = None;
    for entry in std::fs::read_dir(dir.path()).unwrap() {
        let entry = entry.unwrap();
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with("auth.json.corrupt.") {
            backup_found = Some(entry.path());
            break;
        }
    }
    let backup_path = backup_found.expect("a .corrupt.* backup file must have been created");
    let backup_content = std::fs::read_to_string(&backup_path).unwrap();
    assert!(
        backup_content.contains("NOT VALID JSON"),
        "backup must contain the original corrupt content, got: {backup_content}"
    );
}

/// Regression test: clear() must only remove the current scope, not the
/// legacy scope. Previously, logging in with OAuth would also delete the
/// legacy `https://accounts.x.ai/sign-in` entry from auth.json.
#[test]
fn clear_does_not_remove_legacy_scope() {
    let dir = tempfile::tempdir().unwrap();
    let auth_path = dir.path().join("auth.json");

    let legacy_auth = GrokAuth {
        key: "legacy-key".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    let oauth_auth = GrokAuth {
        key: "oauth-key".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };

    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();

    let mut store = AuthStore::new();
    store.insert(LEGACY_SCOPE.to_string(), legacy_auth);
    store.insert(scope, oauth_auth);
    write_auth_json(&auth_path, &store).unwrap();

    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));
    // clear() should only remove the OAuth scope, not legacy
    mgr.clear().unwrap();

    let on_disk = read_auth_json(&auth_path).unwrap();
    assert!(
        on_disk.contains_key(LEGACY_SCOPE),
        "legacy scope should be preserved after clear()"
    );
    assert!(
        !on_disk.contains_key(&mgr.scope),
        "current scope should be removed after clear()"
    );
}

// -- bearer_suffix ----------------------------------------------------------------

#[test]
fn token_suffix_matrix() {
    let cases: &[(&str, &str)] = &[
        ("abcdefghijklmnop", "efghijklmnop"), // takes last 12
        ("short", "short"),                   // short unchanged
        ("", ""),                             // empty
        ("123456789012", "123456789012"),     // exact 12
    ];
    for (input, expected) in cases {
        assert_eq!(bearer_suffix(input), *expected, "input={input:?}");
    }
}

// -- read_disk_auth ----------------------------------------------------------

// -- hot_swap / try_use_disk_token ---------------------------------------

#[test]
fn hot_swap_updates_in_memory_without_disk() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    assert!(mgr.current().is_none());
    let auth = GrokAuth {
        key: "swapped".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    mgr.hot_swap(auth);
    assert_eq!(mgr.current().unwrap().key, "swapped");
    // Disk should NOT have the token
    assert!(mgr.read_disk_auth().is_none());
}

#[test]
fn try_use_disk_token_accepts_valid_disk_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let valid_disk = GrokAuth {
        key: "valid-disk".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    let result = mgr.try_use_disk_token(Some(&valid_disk), RefreshReason::PreRequest);
    assert_eq!(result.unwrap().key, "valid-disk");
    // Should also hot-swap into memory
    assert_eq!(mgr.current().unwrap().key, "valid-disk");
}

#[test]
fn try_use_disk_token_rejects_expired_disk_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let expired_disk = make_auth(Some(Utc::now() - Duration::hours(1)), Utc::now());
    assert_eq!(
        mgr.try_use_disk_token(Some(&expired_disk), RefreshReason::PreRequest)
            .err(),
        Some(DiskTokenDecline::Expired)
    );
}

#[test]
fn try_use_disk_token_rejects_same_key_on_server_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let auth = GrokAuth {
        key: "same-key".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    mgr.hot_swap(auth.clone());

    // ServerRejected should not accept a disk token with the same key
    assert_eq!(
        mgr.try_use_disk_token(Some(&auth), RefreshReason::ServerRejected)
            .err(),
        Some(DiskTokenDecline::SameKeyAsRejected)
    );
}

#[test]
fn try_use_disk_token_accepts_different_key_on_server_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let mem_auth = GrokAuth {
        key: "old-key".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    mgr.hot_swap(mem_auth);

    let disk_auth = GrokAuth {
        key: "new-key".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    let result = mgr.try_use_disk_token(Some(&disk_auth), RefreshReason::ServerRejected);
    assert_eq!(result.unwrap().key, "new-key");
}

/// Disk lagging memory (`update()` kept a mint after a failed disk write) is
/// not a sibling rotation: a valid disk token minted BEFORE the live
/// in-memory one must not clobber it — on ServerRejected that would restore
/// the very bearer the caller is rejecting.
#[test]
fn try_use_disk_token_skips_disk_token_older_than_memory_mint() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let fresh_mint = GrokAuth {
        key: "fresh-mint".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    mgr.hot_swap(fresh_mint);

    let lagging_disk = GrokAuth {
        key: "stale-disk".into(),
        ..make_auth(
            Some(Utc::now() + Duration::minutes(30)),
            Utc::now() - Duration::hours(1),
        )
    };
    for reason in [RefreshReason::PreRequest, RefreshReason::ServerRejected] {
        assert_eq!(
            mgr.try_use_disk_token(Some(&lagging_disk), reason).err(),
            Some(DiskTokenDecline::LaggingMemoryMint),
            "an older disk token must not clobber the in-memory mint ({reason:?})"
        );
        assert_eq!(mgr.current().unwrap().key, "fresh-mint");
    }
}

/// The lagging-mint guard must hold when the in-memory bearer sits inside
/// the early-invalidation buffer — the exact state that routes a refresh
/// into the adopt paths. `current()` hides a buffered bearer, so a
/// `current()`-gated guard was skipped in precisely that window and a
/// lagging disk token could clobber the newest local mint.
#[test]
fn try_use_disk_token_lagging_guard_holds_for_buffered_in_memory_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // Inside the 5-minute buffer: hidden by `current()`, visible to
    // `current_or_expired()`, still the newest local mint.
    let buffered_mint = GrokAuth {
        key: "buffered-mint".into(),
        ..make_auth(Some(Utc::now() + Duration::minutes(2)), Utc::now())
    };
    mgr.hot_swap(buffered_mint);
    assert!(mgr.current().is_none(), "bearer is inside the buffer");

    let lagging_disk = GrokAuth {
        key: "stale-disk".into(),
        ..make_auth(
            Some(Utc::now() + Duration::minutes(30)),
            Utc::now() - Duration::hours(1),
        )
    };
    for reason in [RefreshReason::PreRequest, RefreshReason::ServerRejected] {
        assert_eq!(
            mgr.try_use_disk_token(Some(&lagging_disk), reason).err(),
            Some(DiskTokenDecline::LaggingMemoryMint),
            "a buffered bearer is still the newest mint ({reason:?})"
        );
        assert_eq!(mgr.current_or_expired().unwrap().key, "buffered-mint");
    }
}

/// `pick_up_sibling_token` routes through the shared enforcement point, so
/// it refuses a lagging disk token instead of replacing a newer in-memory
/// mint with it (previously it checked expiry + key only and wrote state
/// directly).
#[test]
fn pick_up_sibling_token_refuses_lagging_disk_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let fresh_mint = GrokAuth {
        key: "fresh-mint".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };
    mgr.hot_swap(fresh_mint);

    // Valid, different key, but minted an hour before the in-memory token:
    // disk lagging memory, not a sibling rotation.
    let lagging_disk = GrokAuth {
        key: "stale-disk".into(),
        ..make_auth(
            Some(Utc::now() + Duration::minutes(30)),
            Utc::now() - Duration::hours(1),
        )
    };
    let mut store = AuthStore::new();
    store.insert(scope, lagging_disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    assert!(
        !mgr.pick_up_sibling_token(),
        "a lagging disk token is not an adoption"
    );
    assert_eq!(mgr.current().unwrap().key, "fresh-mint");
}

// -- File locking ----------------------------------------------------------

// -- Disk-refresh race simulation ------------------------------------------

/// Simulates the core scenario this PR fixes: an expired in-memory token
/// where another process has already refreshed on disk. The manager should
/// pick up the valid disk token via try_use_disk_token instead of
/// attempting its own refresh.
#[tokio::test]
async fn disk_refresh_wins_over_expired_in_memory() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // Simulate: in-memory token is expired
    let expired = GrokAuth {
        key: "expired-key".into(),
        refresh_token: Some("old-rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(expired);
    assert!(mgr.is_expired());
    assert!(mgr.current().is_none());

    // Simulate: another process wrote a valid token to disk
    let fresh_disk = GrokAuth {
        key: "fresh-key-from-sibling".into(),
        refresh_token: Some("new-rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, fresh_disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    // Acquire lock + read disk (mirrors flow.rs logic)
    let _lock = mgr
        .try_lock_auth_file_async(StdDuration::from_secs(1), lock::Heartbeat::Skip)
        .await
        .into_guard();
    assert!(_lock.is_some());

    let disk_auth = mgr.read_disk_auth();
    assert!(disk_auth.is_some());
    assert!(!is_expired(disk_auth.as_ref().unwrap()));

    // try_use_disk_token should accept it and hot-swap
    let result = mgr.try_use_disk_token(disk_auth.as_ref(), RefreshReason::PreRequest);
    assert_eq!(result.unwrap().key, "fresh-key-from-sibling");
    assert_eq!(mgr.current().unwrap().key, "fresh-key-from-sibling");
}

struct CountingRefresher {
    call_count: Arc<AtomicU32>,
    delay: StdDuration,
}

#[async_trait::async_trait]
impl TokenRefresher for CountingRefresher {
    async fn refresh(&self, _reason: RefreshReason) -> crate::auth::refresh::RefreshOutcome {
        self.call_count.fetch_add(1, Ordering::SeqCst);
        tokio::time::sleep(self.delay).await;
        let fresh = GrokAuth {
            key: "fresh-token".into(),
            expires_at: Some(Utc::now() + Duration::hours(1)),
            refresh_token: Some("rt-new".into()),
            ..GrokAuth::test_default()
        };
        crate::auth::refresh::RefreshOutcome::Success(Box::new(fresh))
    }
}

struct FailingRefresher {
    call_count: Arc<AtomicU32>,
}

#[async_trait::async_trait]
impl TokenRefresher for FailingRefresher {
    async fn refresh(&self, _reason: RefreshReason) -> crate::auth::refresh::RefreshOutcome {
        self.call_count.fetch_add(1, Ordering::SeqCst);
        crate::auth::refresh::RefreshOutcome::permanent(
            crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
            None,
        )
    }
}

/// Record a permanent failure scoped to the auth manager's current (or expired)
/// credential key, mirroring what `refresh_chain` does in production.
fn record_permanent_failure(
    auth_manager: &AuthManager,
    reason: crate::auth::error::RefreshTokenFailedReason,
) {
    let key = auth_manager
        .current()
        .or_else(|| auth_manager.expired_auth())
        .map(|a| a.key)
        .unwrap_or_default();
    auth_manager.record_permanent_failure(key, reason.into());
}

/// A convoy member whose sibling already rotated the token must adopt it
/// BEFORE contending the flock: with the flock held elsewhere for the whole
/// call, `refresh_chain` still returns the sibling token promptly, with no
/// IdP call.
#[tokio::test]
async fn refresh_chain_adopts_sibling_pre_lock_without_flock() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    mgr.hot_swap(GrokAuth {
        key: "expired-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("old-rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });
    let fresh_disk = GrokAuth {
        key: "fresh-key-from-sibling".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("new-rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, fresh_disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let calls = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: calls.clone(),
        delay: StdDuration::ZERO,
    }));

    let _held = mgr
        .try_lock_auth_file_async(REFRESH_LOCK_TIMEOUT, lock::Heartbeat::Attach)
        .await
        .into_guard()
        .expect("uncontended first acquisition");

    let adopted = tokio::time::timeout(
        StdDuration::from_secs(2),
        mgr.refresh_chain(TokenType::OidcSession, RefreshReason::PreRequest),
    )
    .await
    .expect("pre-lock adoption must not wait on the held flock")
    .expect("adoption returns the sibling token");
    assert_eq!(adopted.key, "fresh-key-from-sibling");
    assert_eq!(mgr.current().unwrap().key, "fresh-key-from-sibling");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        0,
        "a pure adoption must not reach the IdP"
    );
}

/// ServerRejected with the disk token identical to the rejected one must NOT
/// adopt pre-lock: the caller needs a genuinely new credential, so it falls
/// through to a locked mint.
#[tokio::test]
async fn refresh_chain_server_rejected_same_key_skips_pre_lock_adopt() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let rejected = GrokAuth {
        key: "rejected-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-live".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(rejected.clone());
    let mut store = AuthStore::new();
    store.insert(scope, rejected);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let calls = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: calls.clone(),
        delay: StdDuration::ZERO,
    }));

    let minted = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
        .await
        .expect("locked mint");
    assert_eq!(minted.key, "fresh-token");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "a same-key disk token must mint under the flock"
    );
}

/// A buffered-expired sibling token on disk is not adoptable: the pre-lock
/// check declines and the chain mints under the flock, so adoption can never
/// hand back a token the next request would immediately re-refresh.
#[tokio::test]
async fn refresh_chain_pre_lock_adopt_ignores_expired_disk_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    mgr.hot_swap(GrokAuth {
        key: "expired-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("old-rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });
    // Inside the 5-minute early-invalidation buffer: wire-alive but not
    // adoptable per `try_use_disk_token`'s buffer-inclusive expiry check.
    let buffered_disk = GrokAuth {
        key: "buffered-sibling-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-buffered".into()),
        expires_at: Some(Utc::now() + Duration::minutes(3)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, buffered_disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let calls = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: calls.clone(),
        delay: StdDuration::ZERO,
    }));

    let minted = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::PreRequest)
        .await
        .expect("locked mint");
    assert_eq!(minted.key, "fresh-token");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "a buffered-expired disk token must not be adopted"
    );
}

/// Disk lagging memory must not be "adopted" pre-lock: with a live mint in
/// memory and an older still-valid token on disk (`update()` disk write
/// failed), `refresh_chain` returns the in-memory mint untouched instead of
/// hot-swapping the older bearer back in.
#[tokio::test]
async fn refresh_chain_pre_lock_adopt_skips_disk_token_older_than_memory() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let lagging_disk = GrokAuth {
        key: "stale-disk-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() + Duration::minutes(30)),
        create_time: Utc::now() - Duration::hours(1),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, lagging_disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    mgr.hot_swap(GrokAuth {
        key: "fresh-mint-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-new".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    });

    let calls = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: calls.clone(),
        delay: StdDuration::ZERO,
    }));

    let auth = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::PreRequest)
        .await
        .expect("in-memory mint is returned");
    assert_eq!(auth.key, "fresh-mint-key");
    assert_eq!(mgr.current().unwrap().key, "fresh-mint-key");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        0,
        "neither adoption nor a mint may replace the fresher in-memory token"
    );
}

/// Same lagging-disk guard on its only reachable step-1c path: with a valid
/// in-memory token, `PreRequest` short-circuits at step 1, so only
/// `ServerRejected` carries a live mint into the pre-lock adopt. A
/// different-key disk token minted well before the rejected one must not be
/// adopted — the chain mints under the flock instead.
#[tokio::test]
async fn refresh_chain_server_rejected_skips_lagging_disk_token_pre_lock() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // Past the 60s skew tolerance, so this is unambiguously disk-lagging.
    let lagging_disk = GrokAuth {
        key: "stale-disk-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() + Duration::minutes(30)),
        create_time: Utc::now() - Duration::minutes(10),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, lagging_disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    mgr.hot_swap(GrokAuth {
        key: "rejected-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-live".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    });

    let calls = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: calls.clone(),
        delay: StdDuration::ZERO,
    }));

    let minted = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
        .await
        .expect("locked mint");
    assert_eq!(minted.key, "fresh-token");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "a lagging disk token must not be adopted in place of the rejected mint"
    );
}

/// With `inner == None` but a dead refresh-token on disk, the refresher still
/// exchanges that disk RT. The verdict must be keyed on the
/// credential actually tried (the disk RT), so repeated reactive refreshes
/// short-circuit on it instead of hammering the IdP.
#[tokio::test]
async fn storm_cap_engages_with_empty_inner_and_dead_disk_refresh_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // Disk: an expired token carrying the (dead) refresh_token the OIDC
    // refresher resolves. `inner` stays empty.
    let dead = GrokAuth {
        key: "disk-dead".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-dead".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = read_auth_json(&dir.path().join("auth.json")).unwrap_or_default();
    store.insert(scope, dead);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();
    assert!(mgr.current_or_expired().is_none(), "inner must be empty");

    let calls = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(FailingRefresher {
        call_count: calls.clone(),
    }));

    for _ in 0..5 {
        let _ = mgr
            .refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
            .await;
    }
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "storm cap must hold the IdP to one call even with empty inner + dead disk RT",
    );
}

/// Record/check consistency: in-mem and disk are DIFFERENT stale credentials.
/// The refresher reports `tried_key = disk`; with a retain-path permanent
/// (`ClientRejected`) credentials stay, so the verdict stays scoped to disk.
/// Swapping the in-mem bearer must not re-open the IdP (a verdict mis-keyed to
/// the in-mem bearer would read absent after the swap). The `tried_key == None`
/// fallback (external-binary flow → `attempted_verdict_key`) is covered by
/// `storm_cap_engages_with_empty_inner_and_dead_disk_refresh_token`.
#[tokio::test]
async fn verdict_not_keyed_on_in_mem_bearer() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // in-mem: stale bearer K_mem (expired, with RT).
    mgr.hot_swap(GrokAuth {
        key: "mem-stale".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-mem".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });
    // disk: a DIFFERENT stale credential K_disk (expired, with RT) — what the
    // refresher claims to have tried.
    let disk = GrokAuth {
        key: "disk-stale".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-disk".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = read_auth_json(&dir.path().join("auth.json")).unwrap_or_default();
    store.insert(scope, disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let calls = Arc::new(AtomicU32::new(0));
    struct TriedKeyClientRejected {
        tried_key: String,
        call_count: Arc<AtomicU32>,
    }
    #[async_trait::async_trait]
    impl TokenRefresher for TriedKeyClientRejected {
        async fn refresh(&self, _reason: RefreshReason) -> crate::auth::refresh::RefreshOutcome {
            self.call_count.fetch_add(1, Ordering::SeqCst);
            // ClientRejected retains credentials (unlike RefreshTokenRejected),
            // so the disk-scoped verdict remains the storm cap after mem swap.
            crate::auth::refresh::RefreshOutcome::permanent(
                crate::auth::error::RefreshTokenFailedReason::ClientRejected,
                Some(self.tried_key.clone()),
            )
        }
    }
    mgr.set_refresher(Arc::new(TriedKeyClientRejected {
        tried_key: "disk-stale".into(),
        call_count: calls.clone(),
    }));

    let _ = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
        .await;
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "first call hits the IdP once"
    );
    assert!(
        mgr.read_disk_auth().is_some(),
        "ClientRejected must retain the disk credential the verdict is keyed on",
    );

    // Swap the in-mem bearer to yet another stale key: a verdict mis-keyed to
    // the old in-mem bearer would now read absent.
    mgr.hot_swap(GrokAuth {
        key: "mem-stale-2".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-mem-2".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });

    let _ = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
        .await;
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "verdict keyed on the tried disk credential must survive an in-mem swap",
    );
}

/// Success → persist-failure → transient: a refresh that obtains a fresh token
/// but cannot write it to disk must surface `Transient` AND still swap the
/// in-memory bearer to the fresh token (the "always update in-memory even if the
/// disk write failed" invariant — without it a disk hiccup strands the session).
/// The write is failed deterministically (root-safe) via the path-scoped
/// `WRITE_FAULT_PATH` injection in `storage.rs`; the auth.json read (file
/// absent) and the file lock still succeed.
#[tokio::test]
async fn refresh_persist_failure_is_transient_but_swaps_in_memory() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    // Expired in-mem bearer so the chain proceeds to the IdP (no early return).
    mgr.hot_swap(GrokAuth {
        key: "stale".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });

    // Fail every atomic write to THIS tempdir's auth.json (path-scoped, so
    // parallel tests are unaffected). Cleared on drop.
    struct FaultGuard;
    impl Drop for FaultGuard {
        fn drop(&mut self) {
            *crate::auth::storage::WRITE_FAULT_PATH
                .lock()
                .unwrap_or_else(|e| e.into_inner()) = None;
        }
    }
    let _fault = FaultGuard;
    *crate::auth::storage::WRITE_FAULT_PATH
        .lock()
        .unwrap_or_else(|e| e.into_inner()) = Some(dir.path().join("auth.json"));

    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: Arc::new(AtomicU32::new(0)),
        delay: StdDuration::ZERO,
    }));

    let err = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
        .await
        .expect_err("persist failure must surface an error");
    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Transient(_))),
        "persist failure must be transient (retryable), got {err:?}",
    );
    assert_eq!(
        mgr.current().map(|a| a.key),
        Some("fresh-token".to_string()),
        "in-memory bearer must hold the fresh token despite the failed disk write",
    );
}

#[tokio::test]
async fn auth_concurrent_refresh_deduplicates() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    let expired = GrokAuth {
        key: "expired-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(expired);

    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(50),
    }));

    // Spawn 4 concurrent tasks that all call auth().
    let mut handles = Vec::new();
    for _ in 0..4 {
        let m = mgr.clone();
        handles.push(tokio::spawn(async move { m.auth().await }));
    }

    let mut results = Vec::new();
    for h in handles {
        results.push(h.await.unwrap());
    }

    // All 4 should succeed with the same fresh token.
    for r in &results {
        assert_eq!(
            r.as_ref().unwrap().key,
            "fresh-token",
            "all tasks must get the fresh token"
        );
    }

    // The refresher should have been called exactly once.
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        1,
        "refresher must be called exactly once despite 4 concurrent callers"
    );
}

#[tokio::test]
async fn auth_permanent_failure_stops_retries() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    let expired = GrokAuth {
        key: "expired-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(expired);

    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(FailingRefresher {
        call_count: call_count.clone(),
    }));

    // First auth(): refresher called, refresh_chain records permanent failure.
    let err1 = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err1, AuthError::Refresh(RefreshTokenError::Permanent(_))),
        "first call should return PermanentFailure, got: {err1:?}"
    );

    // Second auth(): permanent failure cached, refresher NOT called.
    let err2 = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err2, AuthError::Refresh(RefreshTokenError::Permanent(_))),
        "second call should return PermanentFailure, got: {err2:?}"
    );

    // Refresher must have been called exactly once.
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        1,
        "refresher must be called exactly once"
    );

    // hot_swap clears permanent failure; subsequent auth() succeeds.
    let valid = GrokAuth {
        key: "new-valid-key".into(),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(valid);
    assert_eq!(mgr.auth().await.unwrap().key, "new-valid-key");
}

/// auth() re-reads disk via pick_up_sibling_token and returns the
/// sibling-written token when the in-memory token is stale.
#[tokio::test]
async fn auth_legacy_session_picks_up_sibling_disk_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    mgr.hot_swap(GrokAuth {
        key: "stale-oidc".into(),
        auth_mode: AuthMode::Oidc,
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });

    // Sibling writes a valid token to disk.
    let fresh = GrokAuth {
        key: "fresh-from-sibling".into(),
        auth_mode: AuthMode::Oidc,
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, fresh);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let auth = mgr.auth().await.expect("should pick up sibling token");
    assert_eq!(auth.key, "fresh-from-sibling");
}

/// refresh_chain returns TransientFailure when the refresher reports one.
#[tokio::test]
async fn refresh_chain_surfaces_transient_failure() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    mgr.hot_swap(GrokAuth {
        key: "expired".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });

    struct TransientRefresher;
    #[async_trait::async_trait]
    impl TokenRefresher for TransientRefresher {
        async fn refresh(&self, _: RefreshReason) -> crate::auth::refresh::RefreshOutcome {
            crate::auth::refresh::RefreshOutcome::TransientFailure {
                message: "idp timeout".into(),
            }
        }
    }
    mgr.set_refresher(Arc::new(TransientRefresher));

    let err = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Transient(_))),
        "TransientFailure should surface as a transient refresh error, got {err:?}"
    );
}

/// Regression: `current()` and `auth()` must agree on whether an
/// expired API key is usable. Pre-fix, `current()` filtered with
/// `!is_token_expired()` (returning None) while the `auth()`
/// `TokenType::ApiKey` branch cloned the stale entry, so the UI saw
/// "logged out" while downstream consumers (trace upload, MCP,
/// embeddings) sent the stale key and hit 401.
#[tokio::test]
async fn auth_returns_expired_api_key_consistently_with_current() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    // Seed an API key that is past the 30-day TTL: `create_time` 60
    // days ago and no `expires_at`. `is_token_expired` falls through
    // to the TTL check and reports `true`.
    let expired_key = GrokAuth {
        key: "stale-api-key".into(),
        auth_mode: AuthMode::ApiKey,
        create_time: Utc::now() - Duration::days(60),
        expires_at: None,
        refresh_token: None,
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(expired_key);

    // UI / sync read path: the stale key is filtered out.
    assert!(
        mgr.current().is_none(),
        "current() must hide the expired api_key (matches UI/login state)"
    );

    // Async path: must NOT clone the stale key for downstream
    // consumers. Surface `TokenExpiredNoRefresh` so callers can
    // funnel the user back through `grok login`.
    let err = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err, AuthError::TokenExpiredNoRefresh),
        "auth() must report TokenExpiredNoRefresh for expired api_key, got: {err:?}",
    );
    assert!(
        mgr.get_valid_token().await.is_err(),
        "get_valid_token() must error rather than return the stale key"
    );

    // Sanity: a fresh API key restores both paths.
    let fresh_key = GrokAuth {
        key: "fresh-api-key".into(),
        auth_mode: AuthMode::ApiKey,
        create_time: Utc::now(),
        expires_at: None,
        refresh_token: None,
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(fresh_key);
    assert_eq!(
        mgr.current().map(|a| a.key).as_deref(),
        Some("fresh-api-key")
    );
    assert_eq!(
        mgr.get_valid_token().await.ok().as_deref(),
        Some("fresh-api-key")
    );
}

/// Reactive path: expired OIDC token -> try_recover_unauthorized ->
/// refresh_chain(ServerRejected) -> refresher -> consumer sees fresh token.
#[tokio::test]
async fn reactive_401_recovery_produces_fresh_token_end_to_end() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    mgr.hot_swap(GrokAuth {
        key: "expired-bearer".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-valid".into()),
        expires_at: Some(Utc::now() - Duration::minutes(10)),
        ..GrokAuth::test_default()
    });

    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));

    assert!(
        mgr.try_recover_unauthorized(crate::auth::recovery::RecoverySource::Background)
            .await
    );
    assert_eq!(call_count.load(Ordering::SeqCst), 1);
    assert_eq!(mgr.get_valid_token().await.unwrap(), "fresh-token");
}

// refresh_chain permanent-failure short-circuit via recovery is tested
// in recovery::tests::refresh_authority_short_circuits_on_cached_permanent_failure.

/// Different disk RT with expired AT: demote to transient so a sibling's
/// still-usable RT is not wiped by permanent clear.
#[tokio::test]
async fn refresh_chain_demotes_when_disk_rt_differs_even_if_at_expired() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // Memory has rt-old; disk has rt-new (different RT) but its
    // access_token is also expired so try_use_disk_token rejects it
    // and we fall through to the refresher.
    let stale = GrokAuth {
        key: "stale-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(stale);

    let sibling = GrokAuth {
        key: "sibling-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-new".into()),
        expires_at: Some(Utc::now() - Duration::minutes(30)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, sibling.clone());
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    struct FailingRefresher;
    #[async_trait::async_trait]
    impl crate::auth::refresh::TokenRefresher for FailingRefresher {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            crate::auth::refresh::RefreshOutcome::permanent(
                crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
                None,
            )
        }
    }
    mgr.set_refresher(Arc::new(FailingRefresher));

    let err = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Transient(_))),
        "disk RT mismatch must demote even when sibling AT is expired, got: {err:?}",
    );
    assert_eq!(
        mgr.read_disk_auth().and_then(|a| a.refresh_token),
        Some("rt-new".into()),
        "sibling RT on disk must not be wiped when AT is only expired",
    );
    assert!(
        mgr.permanent_failure().is_none(),
        "demotion must not record a sticky permanent verdict",
    );
}

/// Regression test for the multi-process logout incident.
///
/// This is the shape `OidcRefresher` actually emits in production: the tried
/// credential is **fully attributed** (`tried_key` *and* `tried_refresh_token`
/// are `Some`). The pre-existing demotion tests all built the outcome with
/// `tried_key = None` — the external-binary shape — so they passed while the
/// OIDC path was gated behind `tried_key.is_none()` and could never demote.
///
/// Scenario: a sibling rotated the RT while our token exchange was in flight,
/// so the IdP rejected the RT we spent. That is a lost race, not a revoked
/// session: it must demote to transient and leave the sibling's credential on
/// disk untouched.
#[tokio::test]
async fn refresh_chain_demotes_when_attributed_tried_rt_differs_from_disk() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // We hold, and spend, the predecessor RT.
    let tried = GrokAuth {
        key: "tried-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-spent".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(tried.clone());

    // A sibling already rotated: disk carries the successor RT. Its AT is
    // expired too, so disk adoption cannot short-circuit the failure path —
    // the demotion is the only thing standing between us and a wipe.
    let sibling = GrokAuth {
        key: "sibling-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-successor".into()),
        expires_at: Some(Utc::now() - Duration::minutes(30)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, sibling);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    struct AttributedRejection(GrokAuth);
    #[async_trait::async_trait]
    impl TokenRefresher for AttributedRejection {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            // Exactly what OidcRefresher builds on a 400 invalid_grant.
            crate::auth::refresh::RefreshOutcome::permanent_for(
                crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
                &self.0,
            )
        }
    }
    mgr.set_refresher(Arc::new(AttributedRejection(tried)));

    let err = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::PreRequest)
        .await
        .unwrap_err();

    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Transient(_))),
        "a rejected RT that disk has already rotated past is a lost race, \
         not a revoked session; must demote to transient, got: {err:?}",
    );
    assert_eq!(
        mgr.read_disk_auth().and_then(|a| a.refresh_token),
        Some("rt-successor".into()),
        "the sibling's successor RT must survive our rejection",
    );
    assert!(
        mgr.permanent_failure().is_none(),
        "demotion must not record a sticky verdict that locks out every \
         sibling process until the user re-runs `grok login`",
    );
}

/// The demotion must *not* fire when disk still holds the very RT that was
/// just rejected: nobody rotated, the session really is dead, and holding on
/// to a known-revoked credential would loop forever.
#[tokio::test]
async fn refresh_chain_still_discards_when_attributed_tried_rt_matches_disk() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let tried = GrokAuth {
        key: "only-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-revoked".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(tried.clone());
    let mut store = AuthStore::new();
    store.insert(scope, tried.clone());
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    struct AttributedRejection(GrokAuth);
    #[async_trait::async_trait]
    impl TokenRefresher for AttributedRejection {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            crate::auth::refresh::RefreshOutcome::permanent_for(
                crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
                &self.0,
            )
        }
    }
    mgr.set_refresher(Arc::new(AttributedRejection(tried)));

    let err = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::PreRequest)
        .await
        .unwrap_err();

    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Permanent(_))),
        "an un-rotated rejected RT is a genuinely dead session, got: {err:?}",
    );
    assert!(
        mgr.permanent_failure().is_some(),
        "a genuine revocation must still record a verdict",
    );
}

/// Disk-first invalid_grant must not wipe an untried in-memory successor RT
/// (mem-ahead-of-disk after a failed persist of a successful rotation).
#[tokio::test]
async fn permanent_rtr_clears_only_the_tried_side_when_rts_diverge() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    // Mem: successor RT after a successful refresh whose disk write failed.
    mgr.hot_swap(GrokAuth {
        key: "mem-successor".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-new".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    });
    // Disk: revoked predecessor RT (disk-first resolve will try this).
    let disk = GrokAuth {
        key: "disk-predecessor".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, disk);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let calls = Arc::new(AtomicU32::new(0));
    struct TriedDiskRtr(Arc<AtomicU32>);
    #[async_trait::async_trait]
    impl TokenRefresher for TriedDiskRtr {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            self.0.fetch_add(1, Ordering::SeqCst);
            crate::auth::refresh::RefreshOutcome::permanent(
                crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
                Some("disk-predecessor".into()),
            )
        }
    }
    mgr.set_refresher(Arc::new(TriedDiskRtr(calls.clone())));

    let err = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
        .await
        .unwrap_err();
    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Permanent(_))),
        "must surface permanent for the tried disk RT, got: {err:?}",
    );
    assert!(
        mgr.read_disk_auth().is_none(),
        "rejected disk predecessor must be cleared",
    );
    assert_eq!(
        mgr.current_or_expired().and_then(|a| a.refresh_token),
        Some("rt-new".into()),
        "untried in-memory successor RT must not be wiped",
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

/// Retain-path permanent (ClientRejected) still graces a soft-expired wire-valid AT.
#[tokio::test]
async fn client_rejected_graces_soft_expired_access_token() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    // Inside the early-invalidation buffer but still hard-valid.
    mgr.hot_swap(GrokAuth {
        key: "buffered-at".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() + Duration::seconds(30)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    });

    struct AlwaysClientRejected;
    #[async_trait::async_trait]
    impl TokenRefresher for AlwaysClientRejected {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            crate::auth::refresh::RefreshOutcome::permanent(
                crate::auth::error::RefreshTokenFailedReason::ClientRejected,
                Some("buffered-at".into()),
            )
        }
    }
    mgr.set_refresher(Arc::new(AlwaysClientRejected));

    let auth = mgr
        .auth()
        .await
        .expect("retain-path permanent must grace wire-valid AT");
    assert_eq!(auth.key, "buffered-at");
    assert!(
        mgr.current_or_expired().is_some(),
        "ClientRejected must retain credentials",
    );
}

/// Escalated permanent `Other` retains AT+RT (only RefreshTokenRejected discards).
#[tokio::test]
async fn permanent_other_retains_credentials() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    let session = GrokAuth {
        key: "live-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-still-valid".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(session.clone());
    // Persist so disk clear would be observable.
    let mut store = AuthStore::new();
    store.insert(GrokComConfig::default().auth_scope(), session);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    struct OtherPermanent;
    #[async_trait::async_trait]
    impl crate::auth::refresh::TokenRefresher for OtherPermanent {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            crate::auth::refresh::RefreshOutcome::permanent(
                crate::auth::error::RefreshTokenFailedReason::Other,
                Some("live-key".into()),
            )
        }
    }
    mgr.set_refresher(Arc::new(OtherPermanent));

    let err = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Permanent(_))),
        "escalated Other must still surface permanent, got: {err:?}",
    );
    assert!(
        mgr.read_disk_auth().is_some(),
        "Other must not clear disk credentials",
    );
    assert_eq!(
        mgr.current_or_expired().and_then(|a| a.refresh_token),
        Some("rt-still-valid".into()),
        "Other must retain in-memory RT",
    );
}

/// Sticky permanent must not block a different credential key (sibling RT).
#[tokio::test]
async fn sticky_permanent_allows_refresh_when_attempted_key_differs() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    mgr.hot_swap(GrokAuth {
        key: "dead-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-dead".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });
    record_permanent_failure(
        &mgr,
        crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
    );
    assert!(mgr.permanent_failure().is_some());

    // Sibling writes a different key + RT (AT hard-expired, RT may still work).
    let sibling = GrokAuth {
        key: "sibling-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-sibling".into()),
        expires_at: Some(Utc::now() - Duration::minutes(30)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, sibling.clone());
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();
    // Load sibling into memory without clearing sticky via wire-valid hot_swap.
    mgr.with_inner_write(|inner| *inner = Some(sibling));

    assert!(
        mgr.permanent_failure().is_none(),
        "sticky verdict must not apply to a different credential key",
    );

    let calls = Arc::new(AtomicU32::new(0));
    struct CountingOk(Arc<AtomicU32>);
    #[async_trait::async_trait]
    impl crate::auth::refresh::TokenRefresher for CountingOk {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            self.0.fetch_add(1, Ordering::SeqCst);
            crate::auth::refresh::RefreshOutcome::Success(Box::new(GrokAuth {
                key: "fresh-from-sibling-rt".into(),
                auth_mode: AuthMode::Oidc,
                refresh_token: Some("rt-sibling".into()),
                expires_at: Some(Utc::now() + Duration::hours(1)),
                ..GrokAuth::test_default()
            }))
        }
    }
    mgr.set_refresher(Arc::new(CountingOk(calls.clone())));

    let auth = mgr
        .auth()
        .await
        .expect("sibling key must reach refresh_chain");
    assert_eq!(auth.key, "fresh-from-sibling-rt");
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

/// Different disk RT with valid AT: adopt the sibling's token directly.
#[tokio::test]
async fn refresh_chain_demotes_to_transient_when_disk_rt_differs_and_at_valid() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let stale = GrokAuth {
        key: "stale-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(stale);

    let sibling = GrokAuth {
        key: "sibling-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-new".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-1".into()),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, sibling);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let calls = Arc::new(AtomicU32::new(0));
    struct CountingFailRefresher(Arc<AtomicU32>);
    #[async_trait::async_trait]
    impl crate::auth::refresh::TokenRefresher for CountingFailRefresher {
        async fn refresh(
            &self,
            _reason: crate::auth::manager::RefreshReason,
        ) -> crate::auth::refresh::RefreshOutcome {
            self.0.fetch_add(1, Ordering::SeqCst);
            crate::auth::refresh::RefreshOutcome::permanent(
                crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
                None,
            )
        }
    }
    mgr.set_refresher(Arc::new(CountingFailRefresher(calls.clone())));

    let result = mgr.auth().await;
    assert!(
        result.is_ok(),
        "should adopt valid sibling token: {result:?}"
    );
    assert_eq!(result.unwrap().key, "sibling-key");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        0,
        "refresher must not be called when disk has a valid token"
    );
}

// -- Regression: api_key in config.toml must not block OIDC refresh --

/// When a user has an OIDC session (auth.json) AND a model with api_key
/// in config.toml, the OIDC token must still be refreshable. auth()
/// checks TokenType (from AuthManager), not the global auth_method_id.
#[tokio::test]
async fn oidc_refresh_not_blocked_by_model_api_key() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    // Expired OIDC token (user has config.toml with api_key on another model).
    let expired_oidc = GrokAuth {
        key: "expired-session-token".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("valid-rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(expired_oidc);

    // TokenType is OidcSession regardless of what models exist in config.
    assert_eq!(mgr.token_type(), TokenType::OidcSession);

    // auth() must attempt OIDC refresh, not short-circuit as ApiKey.
    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(10),
    }));

    let result = mgr.auth().await;
    assert!(result.is_ok(), "auth() should succeed via OIDC refresh");
    assert_eq!(result.unwrap().key, "fresh-token");
    assert_eq!(call_count.load(Ordering::SeqCst), 1);
}

// -- direct unit tests for `compute_proactive_sleep` --------
//
// The proactive task's gate chain is a small pure function; testing
// it directly (rather than through `start_proactive_refresh` and a
// sleep window) gives us per-branch coverage that would have caught
// the original vacuity in seconds. Each test below pins one
// arm of `compute_proactive_sleep`.

/// `permanent_failure` cache auto-expires after `PERMANENT_FAILURE_TTL`,
/// so a misclassified transient IdP error (e.g. `invalid_client` during
/// an OAuth client rotation) doesn't permanently log the user out.
#[tokio::test]
async fn permanent_failure_expires_after_ttl() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    mgr.hot_swap(GrokAuth {
        key: "tok".into(),
        ..GrokAuth::test_default()
    });
    record_permanent_failure(
        &mgr,
        crate::auth::error::RefreshTokenFailedReason::ClientRejected,
    );
    assert!(
        mgr.permanent_failure().is_some(),
        "freshly recorded failure should be sticky"
    );
    mgr.force_permanent_failure_aged_out();
    assert!(
        mgr.permanent_failure().is_none(),
        "aged-out recoverable failure should auto-expire so a retry can succeed"
    );

    // A revoked refresh token never self-heals: the verdict is sticky past the
    // TTL (only a credential change clears it). Stops re-pinging a dead RT.
    record_permanent_failure(
        &mgr,
        crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
    );
    mgr.force_permanent_failure_aged_out();
    assert!(
        mgr.permanent_failure().is_some(),
        "RefreshTokenRejected must stay sticky past the TTL",
    );
}

/// The sticky verdict is exempt from BOTH TTL clocks — the monotonic arm
/// (awake time) AND the wall arm (real time across a suspend, added by the
/// sleep-straddle fix). A revoked refresh token never self-heals with time:
/// re-pinging the IdP with it can only fail again, so no amount of aging on
/// either clock may expire the verdict. Only a credential change heals it —
/// the scoped read-through pinned by the `hot_swap` phase below. This is a
/// composition guard: the sticky/non-sticky split and the wall-clock arm
/// landed separately, so neither parent change could test their intersection.
#[tokio::test]
async fn sticky_verdict_survives_both_clocks_but_not_a_credential_change() {
    // Guard against a vacuous pass: with < TTL of monotonic uptime the aging
    // hook's `checked_sub` no-ops, and a *fresh* verdict would trivially
    // satisfy the survival asserts below.
    if std::time::Instant::now()
        .checked_sub(PERMANENT_FAILURE_TTL + StdDuration::from_secs(1))
        .is_none()
    {
        eprintln!(
            "skipping sticky_verdict_survives_both_clocks: host uptime < PERMANENT_FAILURE_TTL"
        );
        return;
    }

    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    mgr.hot_swap(GrokAuth {
        key: "dead".into(),
        ..GrokAuth::test_default()
    });
    record_permanent_failure(
        &mgr,
        crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
    );

    // Age the verdict past the TTL on the monotonic clock AND rewind the
    // wall-clock arm past it (what a >TTL suspend looks like to the reader).
    mgr.force_permanent_failure_aged_out();
    mgr.force_permanent_failure_wall_aged_out();
    match mgr.permanent_failure() {
        Some(AuthError::Refresh(RefreshTokenError::Permanent(e))) => assert_eq!(
            e.reason,
            crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
            "the surviving verdict must carry the sticky reason",
        ),
        other => panic!("sticky verdict must survive both clocks aging out, got {other:?}"),
    }

    // Time never heals it; a credential change does (read-through, no clear).
    mgr.hot_swap(GrokAuth {
        key: "fresh".into(),
        ..GrokAuth::test_default()
    });
    assert!(
        mgr.permanent_failure().is_none(),
        "stickiness must not outlive the credential it is scoped to",
    );
}

/// The verdict is scoped to the credential that produced it: swapping in a
/// different credential makes it read through as absent, with no explicit
/// clear.
#[tokio::test]
async fn permanent_failure_is_scoped_to_its_credential() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    mgr.hot_swap(GrokAuth {
        key: "dead".into(),
        ..GrokAuth::test_default()
    });
    record_permanent_failure(
        &mgr,
        crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
    );
    assert!(mgr.permanent_failure().is_some());

    // A different credential — no clear call — reads through as no failure.
    mgr.hot_swap(GrokAuth {
        key: "fresh".into(),
        ..GrokAuth::test_default()
    });
    assert!(
        mgr.permanent_failure().is_none(),
        "verdict must not apply to a different credential",
    );
}

/// The verdict is about the *refresh* token: `auth()` must serve a cached
/// access token that is still within its real `expires_at` (buffer-expired
/// but wire-valid) despite a permanent verdict scoped to that credential,
/// without consulting the refresher. Once the same credential passes real
/// expiry, the bypass no longer applies and the permanent error surfaces.
#[tokio::test]
async fn auth_serves_wire_valid_token_despite_permanent_verdict() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    // CI runs in K8s pods where is_devbox_environment() is true; without this
    // the past-expiry phase would mint via devbox recovery instead of
    // surfacing the permanent error.
    mgr.set_devbox_env_for_test(false);

    // Token in the 5-min buffer (1 min before real expiry): buffer-expired,
    // still valid by the IdP's clock.
    mgr.hot_swap(GrokAuth {
        key: "wire-valid".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-dead".into()),
        expires_at: Some(Utc::now() + Duration::minutes(1)),
        ..GrokAuth::test_default()
    });
    record_permanent_failure(
        &mgr,
        crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
    );
    assert!(
        mgr.permanent_failure().is_some(),
        "verdict must scope to the live credential",
    );

    // A refresher is wired but must never be consulted: the verdict
    // short-circuits the chain and the bypass serves the cached bearer.
    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::ZERO,
    }));

    let served = mgr
        .auth()
        .await
        .expect("a wire-valid token must be served despite the verdict");
    assert_eq!(
        served.key, "wire-valid",
        "auth() must return the cached wire-valid bearer",
    );
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        0,
        "the verdict must gate the refresher; serving the cached token is free",
    );

    // Same credential (same key, so the verdict still scopes to it) past its
    // real expiry: the bypass no longer applies.
    mgr.hot_swap(GrokAuth {
        key: "wire-valid".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-dead".into()),
        expires_at: Some(Utc::now() - Duration::minutes(1)),
        ..GrokAuth::test_default()
    });
    let err = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err, AuthError::Refresh(RefreshTokenError::Permanent(_))),
        "past real expiry the verdict must surface, got: {err:?}",
    );
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        0,
        "the cached verdict must keep short-circuiting the refresher",
    );
}

/// Type-system invariant: `apply_user_info_enrichment` must NEVER
/// touch `key`, `refresh_token`, `expires_at`, `oidc_issuer`,
/// `oidc_client_id`, `auth_mode`, `create_time`, or
/// `has_grok_code_access`. The `&mut GrokAuth` signature already
/// enforces this at the type level (you cannot construct a fresh
/// auth from a `UserInfo` -- there's no `From` impl), but a unit
/// test pins the exact list of preserved fields so a future
/// contributor adding a token-like field to both `GrokAuth` and
/// `UserInfo` is forced to look here.
#[test]
fn apply_user_info_enrichment_preserves_token_fields() {
    let mut disk = GrokAuth {
        key: "ROT_KEY".into(),
        refresh_token: Some("ROT_RT".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        oidc_issuer: Some("https://issuer.example".into()),
        oidc_client_id: Some("client-xyz".into()),
        auth_mode: AuthMode::Oidc,
        create_time: Utc::now() - Duration::minutes(10),
        has_grok_code_access: Some(true),
        user_id: "old-user".into(),
        email: Some("old@corp.com".into()),
        team_id: Some("old-team".into()),
        ..GrokAuth::test_default()
    };
    let snapshot = disk.clone();

    let user_info = UserInfo {
        user_id: "new-user".into(),
        email: Some("new@corp.com".into()),
        first_name: Some("New".into()),
        last_name: Some("User".into()),
        profile_image_asset_id: None,
        principal_type: None,
        principal_id: None,
        team_id: Some("new-team".into()),
        team_name: Some("New Team".into()),
        team_role: None,
        organization_id: None,
        organization_name: None,
        organization_role: None,
        user_blocked_reason: None,
        team_blocked_reasons: None,
        coding_data_retention_opt_out: None,
    };

    apply_user_info_enrichment(&mut disk, user_info);

    // Token fields and provenance untouched.
    assert_eq!(disk.key, snapshot.key);
    assert_eq!(disk.refresh_token, snapshot.refresh_token);
    assert_eq!(disk.expires_at, snapshot.expires_at);
    assert_eq!(disk.oidc_issuer, snapshot.oidc_issuer);
    assert_eq!(disk.oidc_client_id, snapshot.oidc_client_id);
    assert_eq!(disk.auth_mode, snapshot.auth_mode);
    assert_eq!(disk.create_time, snapshot.create_time);
    assert_eq!(disk.has_grok_code_access, snapshot.has_grok_code_access);

    // Enrichment fields updated.
    assert_eq!(disk.user_id, "new-user");
    assert_eq!(disk.email.as_deref(), Some("new@corp.com"));
    assert_eq!(disk.team_id.as_deref(), Some("new-team"));
    assert_eq!(disk.team_name.as_deref(), Some("New Team"));
    assert_eq!(disk.first_name.as_deref(), Some("New"));
}

/// Regression: async provider calls must drive `auth()` so tool requests get refreshed tokens.
#[tokio::test]
#[serial_test::serial] // reaches `resolve_static_api_key`, which reads the key env vars
async fn current_api_key_async_drives_refresh_chain() {
    use pi_test_support::EnvGuard;
    use pi_tools::types::ApiKeyProvider;

    let _pi = EnvGuard::unset("PI_API_KEY");
    let _legacy = EnvGuard::unset("GROK_CODE_PI_API_KEY");
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    mgr.hot_swap(GrokAuth {
        key: "expired-oidc".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });
    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));

    let provider = super::SharedAuthKeyProvider(mgr.clone());
    assert_eq!(provider.current_api_key().as_deref(), Some("expired-oidc"));
    let key = provider.current_api_key_async().await;
    assert_eq!(key.as_deref(), Some("fresh-token"));
    assert_eq!(call_count.load(Ordering::SeqCst), 1);
}

/// Regression: empty or corrupt auth.json must be recoverable on login.
/// Previously the guard in `update()` would skip the disk write on any
/// non-NotFound error, leaving a working in-memory session but a broken file.
#[tokio::test]
async fn update_recovers_from_empty_auth_json() {
    let dir = tempfile::tempdir().unwrap();
    let auth_path = dir.path().join("auth.json");
    let cfg = GrokComConfig::default();
    std::fs::write(&auth_path, b"").unwrap();
    assert_eq!(std::fs::metadata(&auth_path).unwrap().len(), 0);

    let mgr = Arc::new(AuthManager::new(dir.path(), cfg.clone()));

    let new_auth = GrokAuth {
        key: "recovered-token".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("recovered-rt".into()),
        user_id: "recovered-user".into(),
        email: Some("user@example.com".into()),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };

    let result = mgr.update(new_auth.clone()).await;
    assert!(
        result.is_ok(),
        "update must succeed and write to disk: {result:?}"
    );

    let current = mgr.current();
    assert_eq!(
        current.as_ref().map(|a| a.key.as_str()),
        Some("recovered-token")
    );

    let on_disk_raw = std::fs::read_to_string(&auth_path).unwrap();
    assert!(
        !on_disk_raw.is_empty(),
        "auth.json must not be empty after recovery"
    );
    let on_disk: AuthStore =
        serde_json::from_str(&on_disk_raw).expect("auth.json must be valid JSON after recovery");
    assert!(
        on_disk.contains_key(&cfg.auth_scope()),
        "persisted scope must be present"
    );
    assert_eq!(
        on_disk.get(&cfg.auth_scope()).map(|a| a.key.as_str()),
        Some("recovered-token")
    );
}

/// Same as above, but for whitespace-only content.
#[tokio::test]
async fn update_recovers_from_whitespace_only_auth_json() {
    let dir = tempfile::tempdir().unwrap();
    let auth_path = dir.path().join("auth.json");
    let cfg = GrokComConfig::default();
    std::fs::write(&auth_path, b"  \n\t  ").unwrap();

    let mgr = Arc::new(AuthManager::new(dir.path(), cfg.clone()));

    let new_auth = GrokAuth {
        key: "ws-token".into(),
        auth_mode: AuthMode::Oidc,
        user_id: "ws-user".into(),
        ..make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now())
    };

    let result = mgr.update(new_auth).await;
    assert!(
        result.is_ok(),
        "update must succeed for whitespace-only file: {result:?}"
    );

    let on_disk = std::fs::read_to_string(&auth_path).unwrap();
    assert!(on_disk.contains("ws-token"), "credential must be persisted");
}

// -- sibling-rotation comparison ------------------------------------------

/// The demotion — and therefore whether a dozen processes keep their
/// credentials — rests entirely on this comparison, so pin its three cases
/// directly rather than only through the refresh chain.
#[test]
fn refresh_token_superseded_needs_a_successor_on_disk() {
    assert!(
        AuthManager::refresh_token_superseded(Some("rt-successor"), "rt-spent"),
        "a different RT on disk is a sibling's successor: demote"
    );
    assert!(
        !AuthManager::refresh_token_superseded(Some("rt-spent"), "rt-spent"),
        "disk still holding the RT the IdP just rejected is a real revocation"
    );
    assert!(
        !AuthManager::refresh_token_superseded(None, "rt-spent"),
        "no RT on disk means there is no successor to fall back to, so the \
         rejection must be honored rather than demoted into a retry loop"
    );
}

// -- sibling_has_different_refresh_token ----------------------------------

/// Expired disk AT with different RT is still treated as a sibling RT
/// (may still be refreshable; must not be wiped by permanent clear).
#[tokio::test]
async fn sibling_different_rt_with_expired_at_is_still_sibling() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg.clone()));

    // In-memory: the original RT (revoked via rotation), AT expired.
    let original = GrokAuth {
        key: "original-at".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-original".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(original);

    // Disk: the successor RT from rotation, AT also expired.
    let successor = GrokAuth {
        key: "successor-at".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-successor".into()),
        expires_at: Some(Utc::now() - Duration::minutes(30)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(cfg.auth_scope(), successor);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let disk_rt = mgr.read_disk_auth().and_then(|a| a.refresh_token);
    assert!(
        mgr.sibling_has_different_refresh_token(disk_rt.as_deref()),
        "different disk RT must demote even when the sibling AT is expired"
    );
}

/// Valid disk AT with different RT is a live sibling.
#[tokio::test]
async fn sibling_different_rt_with_valid_at_is_treated_as_live() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg.clone()));

    let original = GrokAuth {
        key: "original-at".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-original".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(original);

    // Disk: valid token from sibling process.
    let sibling = GrokAuth {
        key: "sibling-at".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-sibling".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(cfg.auth_scope(), sibling);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let disk_rt = mgr.read_disk_auth().and_then(|a| a.refresh_token);
    assert!(
        mgr.sibling_has_different_refresh_token(disk_rt.as_deref()),
        "valid disk token with different RT must be treated as live sibling"
    );
}

/// Regression: refresh_chain(ServerRejected) must bypass the "double-check"
/// early return when the in-memory token is still valid (not expired).
/// Without this, a JWT that is time-valid but missing a subscription claim
/// (post-purchase) is returned as-is and the IdP is never contacted.
#[tokio::test]
async fn refresh_chain_server_rejected_bypasses_valid_token_double_check() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    // Seed a valid (non-expired) token — simulates a JWT that is missing
    // the subscription claim but is otherwise fine.
    let valid_but_rejected = GrokAuth {
        key: "pre-subscription-jwt".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-original".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(valid_but_rejected);

    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));

    // Confirm the token is considered valid before refresh.
    assert_eq!(mgr.current().unwrap().key, "pre-subscription-jwt");

    // ServerRejected must force a real refresh despite the token being valid.
    let result = mgr
        .refresh_chain(
            crate::auth::token_type::TokenType::OidcSession,
            RefreshReason::ServerRejected,
        )
        .await;

    assert_eq!(
        result.unwrap().key,
        "fresh-token",
        "refresh_chain(ServerRejected) must contact the IdP even with a valid token"
    );
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        1,
        "refresher must be called exactly once"
    );
    assert_eq!(
        mgr.current().unwrap().key,
        "fresh-token",
        "in-memory token must be updated to the refreshed one"
    );
}

/// When two tasks both get 401 and call refresh_chain(ServerRejected)
/// concurrently, the second caller must return the already-refreshed token
/// without contacting the IdP again. This prevents the double-refresh race
/// where the second caller sends a rotated refresh token → invalid_grant.
#[tokio::test]
async fn refresh_chain_server_rejected_concurrent_skips_redundant_refresh() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    // Seed the "rejected" token that both tasks will see.
    let rejected = GrokAuth {
        key: "rejected-jwt".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(rejected);

    let call_count = Arc::new(AtomicU32::new(0));
    // Slow refresher so the second task blocks on the lock long enough
    // to observe the first task's refresh result.
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(50),
    }));

    // Both tasks snapshot pre_lock_key = "rejected-jwt", then race for
    // the lock. The first refreshes → "fresh-token". The second finds
    // current() = "fresh-token" != pre_lock_key → returns early.
    let mgr1 = mgr.clone();
    let mgr2 = mgr.clone();

    let (r1, r2) = tokio::join!(
        mgr1.refresh_chain(
            crate::auth::token_type::TokenType::OidcSession,
            RefreshReason::ServerRejected,
        ),
        mgr2.refresh_chain(
            crate::auth::token_type::TokenType::OidcSession,
            RefreshReason::ServerRejected,
        ),
    );

    // Both must succeed with the refreshed token.
    assert_eq!(r1.unwrap().key, "fresh-token");
    assert_eq!(r2.unwrap().key, "fresh-token");

    // The IdP must be contacted exactly once, not twice.
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        1,
        "refresher must be called exactly once; second caller should \
         return the already-refreshed token via the double-check guard"
    );
}

/// Counterpart: refresh_chain(PreRequest) with a valid token must
/// short-circuit and NOT call the refresher.
#[tokio::test]
async fn refresh_chain_pre_request_short_circuits_on_valid_token() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    let valid = GrokAuth {
        key: "still-good".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(valid);

    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));

    let result = mgr
        .refresh_chain(
            crate::auth::token_type::TokenType::OidcSession,
            RefreshReason::PreRequest,
        )
        .await;

    assert_eq!(result.unwrap().key, "still-good");
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        0,
        "PreRequest must NOT call refresher when token is valid"
    );
}

// -- login-time inline enrichment -------------------------------------------

// ── force_login_team_uuid spine enforcement ───────────────────────────
//
// Regression coverage for the cached-token bypass: the pin must hold for every
// token the manager hands out (startup, sync reads, `auth()`), not just fresh
// login. Each test fails on the pre-fix tree.

/// `jsonwebtoken` needs a process-level CryptoProvider; tests that encode
/// JWTs can't rely on another test having installed it first.
fn ensure_crypto_provider() {
    let _ = jsonwebtoken::crypto::rust_crypto::DEFAULT_PROVIDER.install_default();
}

/// A signed (HS256) access token carrying a `Team` principal, matching the
/// shape `peek_access_token_principal` extracts in production.
fn team_jwt(principal_id: &str) -> String {
    ensure_crypto_provider();
    jsonwebtoken::encode(
        &jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256),
        &serde_json::json!({
            "sub": "user-1",
            "principal_type": "Team",
            "principal_id": principal_id,
            "exp": 9999999999u64,
        }),
        &jsonwebtoken::EncodingKey::from_secret(b"test-secret"),
    )
    .unwrap()
}

/// An access token carrying `principal_id` but NO `principal_type`.
fn principal_id_only_jwt(principal_id: &str) -> String {
    ensure_crypto_provider();
    jsonwebtoken::encode(
        &jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256),
        &serde_json::json!({
            "sub": "user-1",
            "principal_id": principal_id,
            "exp": 9999999999u64,
        }),
        &jsonwebtoken::EncodingKey::from_secret(b"test-secret"),
    )
    .unwrap()
}

fn pinned_cfg(team: &str) -> GrokComConfig {
    GrokComConfig {
        force_login_team_uuid: Some(crate::auth::config::ForceLoginTeam::Single(
            team.to_string(),
        )),
        ..GrokComConfig::default()
    }
}

/// A valid, non-expired OIDC session whose access token carries `principal_id`.
fn oidc_session_for_team(principal_id: &str) -> GrokAuth {
    GrokAuth {
        key: team_jwt(principal_id),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        oidc_issuer: Some(crate::auth::config::PI_OAUTH2_ISSUER.to_string()),
        oidc_client_id: Some("client".into()),
        ..GrokAuth::test_default()
    }
}

/// The repro: a wrong-team session persisted to disk (e.g. logged in before
/// the pin was deployed) must be cleared at construction, not silently loaded.
#[test]
fn new_clears_wrong_team_token_loaded_from_disk() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = pinned_cfg("team-good");
    let scope = cfg.auth_scope();

    let mut store = AuthStore::new();
    store.insert(scope, oidc_session_for_team("team-wrong"));
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));
    assert!(mgr.current().is_none(), "wrong-team token must be hidden");
    assert!(
        mgr.current_or_expired().is_none(),
        "wrong-team token must be cleared from memory, not just hidden"
    );
    assert!(
        !dir.path().join("auth.json").exists(),
        "wrong-team auth.json must be cleared so the next launch re-logs in"
    );
}

/// A matching-team session on disk is loaded normally (no false positive).
#[test]
fn new_keeps_matching_team_token_loaded_from_disk() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = pinned_cfg("team-good");
    let scope = cfg.auth_scope();
    let tok = oidc_session_for_team("team-good");

    let mut store = AuthStore::new();
    store.insert(scope, tok.clone());
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));
    assert_eq!(mgr.current().map(|a| a.key), Some(tok.key));
    assert!(dir.path().join("auth.json").exists());
}

/// `auth()` (the wire-bound chokepoint used by pager / MCP /
/// `try_ensure_fresh_auth`) rejects and clears a wrong-team cached token.
#[tokio::test]
async fn auth_rejects_and_clears_wrong_team_cached_token() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), pinned_cfg("team-good")));
    // hot_swap bypasses the pin (like a sibling adoption mid-session).
    mgr.hot_swap(oidc_session_for_team("team-wrong"));

    assert!(mgr.current().is_none(), "sync read must hide the token");

    let err = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err, AuthError::PinnedTeamMismatch { .. }),
        "auth() must surface the policy violation, got {err:?}"
    );
    assert!(
        mgr.current_or_expired().is_none(),
        "auth() must clear the violating session"
    );
}

/// A matching-team cached token flows through `auth()` unchanged.
#[tokio::test]
async fn auth_accepts_matching_team_cached_token() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), pinned_cfg("team-good")));
    let tok = oidc_session_for_team("team-good");
    mgr.hot_swap(tok.clone());

    assert_eq!(mgr.current().map(|a| a.key.clone()), Some(tok.key.clone()));
    assert_eq!(mgr.auth().await.unwrap().key, tok.key);
}

/// No pin configured: any team is accepted (the enforcement is opt-in and
/// must not affect default deployments).
#[tokio::test]
async fn no_pin_accepts_any_team_cached_token() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    let tok = oidc_session_for_team("team-anything");
    mgr.hot_swap(tok.clone());

    assert_eq!(mgr.current().map(|a| a.key.clone()), Some(tok.key.clone()));
    assert_eq!(mgr.auth().await.unwrap().key, tok.key);
}

/// A token that silently refreshes into a wrong-team principal is rejected by
/// `auth()` (the wrapper gates refresh results, not just the cached fast path).
#[tokio::test]
async fn auth_rejects_token_refreshed_into_wrong_team() {
    struct WrongTeamRefresher {
        jwt: String,
    }
    #[async_trait::async_trait]
    impl TokenRefresher for WrongTeamRefresher {
        async fn refresh(&self, _reason: RefreshReason) -> crate::auth::refresh::RefreshOutcome {
            crate::auth::refresh::RefreshOutcome::Success(Box::new(GrokAuth {
                key: self.jwt.clone(),
                auth_mode: AuthMode::Oidc,
                refresh_token: Some("rt-new".into()),
                expires_at: Some(Utc::now() + Duration::hours(1)),
                ..GrokAuth::test_default()
            }))
        }
    }

    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), pinned_cfg("team-good")));
    // Expired matching session forces a refresh; the refresher returns a
    // wrong-team token (e.g. a re-pinned token family).
    mgr.hot_swap(GrokAuth {
        expires_at: Some(Utc::now() - Duration::minutes(10)),
        ..oidc_session_for_team("team-good")
    });
    mgr.set_refresher(Arc::new(WrongTeamRefresher {
        jwt: team_jwt("team-wrong"),
    }));

    let err = mgr.auth().await.unwrap_err();
    assert!(
        matches!(err, AuthError::PinnedTeamMismatch { .. }),
        "refreshed wrong-team token must be rejected, got {err:?}"
    );
}

/// A sibling-written wrong-team token picked up by `force_reload_from_disk`
/// (relay reconnect) is cleared, not just hidden.
#[test]
fn force_reload_clears_wrong_team_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = pinned_cfg("team-good");
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg)); // empty disk at startup

    let mut store = AuthStore::new();
    store.insert(scope, oidc_session_for_team("team-wrong"));
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    mgr.force_reload_from_disk();
    assert!(
        mgr.current_or_expired().is_none(),
        "reloaded wrong-team token must be cleared, not just hidden"
    );
    assert!(
        !dir.path().join("auth.json").exists(),
        "force_reload must clear auth.json on a pin violation"
    );
}

// -- force_reload_from_disk: transient disk anomaly vs real logout ----------

/// A real incident in miniature: a live in-memory OIDC session (RT
/// present, no permanent_failure) while `auth.json` transiently reads as
/// missing — e.g. the first read right after wake-from-sleep resolves the path
/// to `ENOENT`. The refresh token may exist nowhere else, so the reload must
/// RETAIN it, not discard it (the discard previously kicked off a
/// 401 -> reactive refresh -> suspend-straddle -> invalid_grant cascade).
#[test]
fn force_reload_retains_live_rt_on_transient_file_missing() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    let session = GrokAuth {
        key: "live-session".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("live-rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(session);
    assert!(mgr.permanent_failure().is_none());

    // No auth.json on disk at all -> FileMissing on every read.
    assert!(mgr.read_disk_auth().is_none());

    // Zero backoff so the retry budget is exhausted instantly.
    mgr.force_reload_from_disk_with(RELOAD_RETRY_TRIES, StdDuration::ZERO);

    let retained = mgr.current_or_expired();
    assert!(
        retained.is_some(),
        "a live RT must NOT be discarded on a transient FileMissing",
    );
    let retained = retained.unwrap();
    assert_eq!(retained.key, "live-session");
    assert_eq!(retained.refresh_token.as_deref(), Some("live-rt"));
}

/// Contrast with the retain case: once a `permanent_failure` is cached the RT
/// is known-dead, so a persistent FileMissing must drop it (and clear the
/// permanent_failure with it) so the next request reports `NotLoggedIn`.
#[tokio::test]
async fn force_reload_drops_rt_when_permanent_failure_set() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    let session = GrokAuth {
        key: "broken".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-revoked".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(session);
    record_permanent_failure(
        &mgr,
        crate::auth::error::RefreshTokenFailedReason::RefreshTokenRejected,
    );
    assert!(mgr.permanent_failure().is_some());

    mgr.force_reload_from_disk_with(RELOAD_RETRY_TRIES, StdDuration::ZERO);

    assert!(
        mgr.current_or_expired().is_none(),
        "a known-dead RT (permanent_failure set) must be dropped",
    );
    assert!(
        mgr.permanent_failure().is_none(),
        "dropping creds must clear the cached permanent_failure",
    );
    assert!(matches!(
        mgr.auth().await.unwrap_err(),
        AuthError::NotLoggedIn
    ));
}

/// A readable `auth.json` that simply lacks our scope is the trustworthy
/// "logged out / scope removed" signal (distinct from a missing file), so the
/// in-memory credentials are dropped even though an RT is present.
#[test]
fn force_reload_drops_creds_on_entry_missing() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));

    let session = GrokAuth {
        key: "live-session".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("live-rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(session);

    // auth.json exists and is readable, but holds only an unrelated scope ->
    // EntryMissing for this manager's scope.
    let mut store = AuthStore::new();
    store.insert(
        "https://example.invalid::nobody".to_string(),
        make_auth(Some(Utc::now() + Duration::hours(1)), Utc::now()),
    );
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    mgr.force_reload_from_disk_with(RELOAD_RETRY_TRIES, StdDuration::ZERO);

    assert!(
        mgr.current_or_expired().is_none(),
        "scope absent on a readable auth.json is a real logout -> drop",
    );
}

/// When disk holds a fresh token for our scope, the reload adopts it on the
/// first read (no retry) — the healthy path is unchanged.
#[test]
fn force_reload_adopts_fresh_disk_token() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = GrokComConfig::default();
    let scope = cfg.auth_scope();
    let mgr = Arc::new(AuthManager::new(dir.path(), cfg));

    let expired = GrokAuth {
        key: "stale".into(),
        refresh_token: Some("old-rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(expired);

    let fresh = GrokAuth {
        key: "fresh-from-disk".into(),
        refresh_token: Some("new-rt".into()),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };
    let mut store = AuthStore::new();
    store.insert(scope, fresh);
    write_auth_json(&dir.path().join("auth.json"), &store).unwrap();

    mgr.force_reload_from_disk_with(RELOAD_RETRY_TRIES, StdDuration::ZERO);

    assert_eq!(mgr.current().unwrap().key, "fresh-from-disk");
}

/// A token carrying `principal_id` without `principal_type` is matched on the
/// id alone: the pinned team is accepted, not falsely rejected.
#[tokio::test]
async fn pin_matches_principal_id_without_principal_type() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), pinned_cfg("team-good")));
    mgr.hot_swap(GrokAuth {
        key: principal_id_only_jwt("team-good"),
        auth_mode: AuthMode::Oidc,
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    });

    assert!(
        mgr.current().is_some(),
        "matching team id must be accepted even without principal_type"
    );
    assert!(mgr.auth().await.is_ok());
}

/// A cached `AuthMode::ApiKey` session is rejected under the kill switch (here
/// implied by a team pin), and honored when it's off.
#[tokio::test]
async fn cached_api_key_session_rejected_when_api_key_auth_disabled() {
    let api_key_session = || GrokAuth {
        key: "pi-cached-key".into(),
        auth_mode: AuthMode::ApiKey,
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    };

    // Switch ON (via a team pin, which implies api_key_auth_disabled): reject.
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), pinned_cfg("team-good")));
    mgr.hot_swap(api_key_session());
    assert!(
        mgr.current().is_none(),
        "cached api-key session must be hidden under the kill switch"
    );
    assert!(
        matches!(mgr.auth().await, Err(AuthError::ApiKeyAuthDisabled)),
        "auth() must reject a cached api-key session under the kill switch"
    );

    // Switch OFF (no pin / no disable): the api-key session is honored.
    let dir2 = tempfile::tempdir().unwrap();
    let mgr2 = Arc::new(AuthManager::new(dir2.path(), GrokComConfig::default()));
    mgr2.hot_swap(api_key_session());
    assert_eq!(
        mgr2.current().map(|a| a.key),
        Some("pi-cached-key".to_string()),
        "api-key session must work normally when the switch is off"
    );
}

#[tokio::test]
async fn shared_api_key_provider_resolves_live_bearer() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    let auth = GrokAuth {
        key: "shared-provider-token".into(),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        create_time: Utc::now(),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(auth);

    let provider = shared_api_key_provider(mgr.clone());

    // Synchronous accessor surfaces the current (non-expired) bearer.
    assert_eq!(
        provider.current_api_key(),
        Some("shared-provider-token".to_string()),
        "shared_api_key_provider must expose the live bearer to out-of-crate consumers"
    );

    // Async accessor resolves a valid bearer without a network refresh when
    // the cached token is still fresh.
    assert_eq!(
        provider.current_api_key_async().await,
        Some("shared-provider-token".to_string()),
        "async accessor must resolve the current bearer for a fresh token"
    );

    // A hot-swap is reflected on the next resolution (no startup snapshot).
    let rotated = GrokAuth {
        key: "rotated-token".into(),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        create_time: Utc::now(),
        ..GrokAuth::test_default()
    };
    mgr.hot_swap(rotated);
    assert_eq!(
        provider.current_api_key(),
        Some("rotated-token".to_string()),
        "provider must follow the manager's refresh chain rather than snapshot at startup"
    );
}

/// No OAuth session → env or auth.json `pi::api_key` for voice/tools.
#[tokio::test]
#[serial_test::serial]
async fn shared_api_key_provider_static_fallthrough() {
    use pi_test_support::EnvGuard;

    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    let provider = shared_api_key_provider(mgr.clone());

    {
        let _legacy = EnvGuard::unset("GROK_CODE_PI_API_KEY");
        let _key = EnvGuard::set("PI_API_KEY", "env-only-key");
        assert_eq!(
            provider.current_api_key_async().await.as_deref(),
            Some("env-only-key")
        );
    }

    {
        let _pi = EnvGuard::unset("PI_API_KEY");
        let _legacy = EnvGuard::unset("GROK_CODE_PI_API_KEY");
        crate::auth::store_api_key(dir.path(), "disk-api-key").unwrap();
        assert_eq!(
            provider.current_api_key_async().await.as_deref(),
            Some("disk-api-key")
        );
    }

    {
        let _key = EnvGuard::set("PI_API_KEY", "env-should-lose");
        mgr.hot_swap(GrokAuth {
            key: "session-bearer".into(),
            expires_at: Some(Utc::now() + Duration::hours(1)),
            create_time: Utc::now(),
            ..GrokAuth::test_default()
        });
        assert_eq!(
            provider.current_api_key_async().await.as_deref(),
            Some("session-bearer")
        );
    }
}

#[tokio::test]
#[serial_test::serial]
async fn shared_api_key_provider_kill_switch_blocks_static() {
    use pi_test_support::EnvGuard;

    let _key = EnvGuard::set("PI_API_KEY", "blocked");
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(
        dir.path(),
        GrokComConfig {
            disable_api_key_auth: Some(true),
            ..GrokComConfig::default()
        },
    ));
    assert_eq!(
        shared_api_key_provider(mgr).current_api_key_async().await,
        None
    );
}

#[tokio::test]
#[serial_test::serial]
async fn shared_api_key_provider_oidc_preferred_blocks_static() {
    use pi_test_support::EnvGuard;

    let _key = EnvGuard::set("PI_API_KEY", "should-not-use");
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(
        dir.path(),
        GrokComConfig {
            preferred_method: Some(crate::auth::PreferredAuthMethod::Oidc),
            ..GrokComConfig::default()
        },
    ));
    assert_eq!(
        shared_api_key_provider(mgr).current_api_key_async().await,
        None
    );
}

/// preferred_method=api_key: leftover session must not beat static API key.
#[tokio::test]
#[serial_test::serial]
async fn shared_api_key_provider_api_key_preferred_skips_session() {
    use pi_test_support::EnvGuard;

    let _legacy = EnvGuard::unset("GROK_CODE_PI_API_KEY");
    let _key = EnvGuard::set("PI_API_KEY", "static-preferred");
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(
        dir.path(),
        GrokComConfig {
            preferred_method: Some(crate::auth::PreferredAuthMethod::ApiKey),
            ..GrokComConfig::default()
        },
    ));
    mgr.hot_swap(GrokAuth {
        key: "leftover-oidc".into(),
        expires_at: Some(Utc::now() + Duration::hours(1)),
        create_time: Utc::now(),
        ..GrokAuth::test_default()
    });
    assert_eq!(
        shared_api_key_provider(mgr)
            .current_api_key_async()
            .await
            .as_deref(),
        Some("static-preferred")
    );
}

/// Expired OAuth must not block static fallthrough on the sync path.
#[tokio::test]
#[serial_test::serial]
async fn shared_api_key_provider_sync_falls_through_when_session_expired() {
    use pi_test_support::EnvGuard;

    let _legacy = EnvGuard::unset("GROK_CODE_PI_API_KEY");
    let _key = EnvGuard::set("PI_API_KEY", "static-after-expiry");
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    mgr.hot_swap(GrokAuth {
        key: "expired-oidc".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    });
    let provider = shared_api_key_provider(mgr);
    assert_eq!(
        provider.current_api_key().as_deref(),
        Some("static-after-expiry"),
        "sync path must not return a dead session token over a live static key"
    );
    assert_eq!(
        provider.current_api_key_async().await.as_deref(),
        Some("static-after-expiry")
    );
}

/// A session inside the early-invalidation buffer is still wire-valid and
/// must beat a static key on the sync path.
#[tokio::test]
#[serial_test::serial]
async fn shared_api_key_provider_sync_buffered_session_beats_static() {
    use pi_test_support::EnvGuard;
    use pi_tools::types::ApiKeyProvider;

    let _legacy = EnvGuard::unset("GROK_CODE_PI_API_KEY");
    let _key = EnvGuard::set("PI_API_KEY", "leftover-static");
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    // Two minutes out: inside the 5-minute buffer, but accepted on the wire.
    mgr.hot_swap(GrokAuth {
        key: "buffered-oidc".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        expires_at: Some(Utc::now() + Duration::minutes(2)),
        ..GrokAuth::test_default()
    });
    let provider = super::SharedAuthKeyProvider(mgr);
    assert_eq!(provider.current_api_key().as_deref(), Some("buffered-oidc"));
}

/// Auth.json create, rewrite (including same-length, caught by the inode in
/// the memo stamp), and logout must all invalidate the disk static-key memo.
#[tokio::test]
#[serial_test::serial]
async fn shared_api_key_provider_disk_memo_follows_rewrites() {
    use pi_test_support::EnvGuard;

    let _pi = EnvGuard::unset("PI_API_KEY");
    let _legacy = EnvGuard::unset("GROK_CODE_PI_API_KEY");
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    let provider = shared_api_key_provider(mgr);

    assert_eq!(provider.current_api_key_async().await, None);

    for key in ["first-key", "fresh-key", "second-key-rotated"] {
        crate::auth::store_api_key(dir.path(), key).unwrap();
        assert_eq!(provider.current_api_key_async().await.as_deref(), Some(key));
    }

    crate::auth::clear_api_key(dir.path()).unwrap();
    assert_eq!(provider.current_api_key_async().await, None);
}

fn expired_oidc() -> GrokAuth {
    GrokAuth {
        key: "expired-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() - Duration::hours(1)),
        ..GrokAuth::test_default()
    }
}

/// Dark wake defers a refresh only while a *wire-valid* token can still be
/// served — then the deferral costs nothing but latency.
#[tokio::test]
async fn dark_wake_defers_refresh_while_a_live_token_can_be_served() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    // Inside the early-invalidation buffer: due for renewal (`current()` is
    // None, so `refresh_chain` proceeds) but still accepted on the wire.
    mgr.hot_swap(GrokAuth {
        key: "live-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() + Duration::minutes(2)),
        ..GrokAuth::test_default()
    });
    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));
    mgr.set_dark_wake_for_test(true);

    let err = mgr
        .refresh_chain(TokenType::OidcSession, RefreshReason::PreRequest)
        .await
        .unwrap_err();
    assert!(
        matches!(
            err,
            AuthError::Refresh(crate::auth::error::RefreshTokenError::Transient(_))
        ),
        "dark-wake refresh must return a transient refresh error, got {err:?}"
    );
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        0,
        "with a live token to serve, the refresh token must not be sent into a \
         possible re-sleep"
    );
}

/// The inverse, and the one field logs caught: with **no** usable token, a
/// dark-wake deferral guarantees the caller 401s instead of merely delaying it.
/// A machine doing background work with the lid shut (leader mode + subagents)
/// accumulated hundreds of 401s across hours of continuous dark wake while
/// every recovery refresh was deferred. Refresh must proceed in that state.
#[tokio::test]
async fn dark_wake_does_not_defer_when_no_usable_token() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    mgr.hot_swap(expired_oidc());
    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));
    mgr.set_dark_wake_for_test(true);

    assert_eq!(
        mgr.auth().await.unwrap().key,
        "fresh-token",
        "an expired credential in dark wake must still be refreshed"
    );
    assert_eq!(call_count.load(Ordering::SeqCst), 1);
}

/// A 401 recovery is never deferred for dark wake: the server already rejected
/// what we hold, so deferring can only prolong the failure.
#[tokio::test]
async fn dark_wake_does_not_defer_server_rejected_recovery() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    mgr.hot_swap(GrokAuth {
        key: "rejected-but-unexpired".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() + Duration::minutes(2)),
        ..GrokAuth::test_default()
    });
    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));
    mgr.set_dark_wake_for_test(true);

    assert_eq!(
        mgr.refresh_chain(TokenType::OidcSession, RefreshReason::ServerRejected)
            .await
            .unwrap()
            .key,
        "fresh-token",
        "ServerRejected recovery must reach the IdP even in dark wake"
    );
    assert_eq!(call_count.load(Ordering::SeqCst), 1);
}

/// A machine stuck reporting a *continuous* dark wake (e.g. an interactive Mac
/// with no display) must not defer refresh forever — once the deferral budget
/// (`DARK_WAKE_DEFER_MAX`) is exhausted, one refresh is forced through. Without
/// this bound the user reaches the same logged-out state the dark-wake guard
/// was added to prevent.
#[tokio::test]
async fn dark_wake_defer_forces_refresh_after_max() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = Arc::new(AuthManager::new(dir.path(), GrokComConfig::default()));
    // Wire-valid but due for renewal, so the deferral path (and its budget) is
    // the thing under test — a hard-expired token is never deferred at all.
    mgr.hot_swap(GrokAuth {
        key: "live-key".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt-old".into()),
        expires_at: Some(Utc::now() + Duration::minutes(2)),
        ..GrokAuth::test_default()
    });
    let call_count = Arc::new(AtomicU32::new(0));
    mgr.set_refresher(Arc::new(CountingRefresher {
        call_count: call_count.clone(),
        delay: StdDuration::from_millis(0),
    }));

    mgr.set_dark_wake_for_test(true);

    // Backdate the start of the deferral run past the bound on both clocks, as
    // if we had been continuously in dark wake longer than DARK_WAKE_DEFER_MAX.
    let back = super::sleep_gate::DARK_WAKE_DEFER_MAX + StdDuration::from_secs(5);
    let (Some(mono), Some(wall)) = (
        Instant::now().checked_sub(back),
        std::time::SystemTime::now().checked_sub(back),
    ) else {
        return; // machine/clock can't represent the backdate — skip
    };
    *mgr.dark_wake_defer_since.write() = Some(crate::util::dual_clock::DualClock { mono, wall });

    assert_eq!(
        mgr.auth().await.unwrap().key,
        "fresh-token",
        "an exhausted dark-wake deferral budget must force the refresh through"
    );
    assert_eq!(
        call_count.load(Ordering::SeqCst),
        1,
        "the IdP refresher must be invoked once the dark-wake defer budget is exhausted"
    );
    assert!(
        mgr.dark_wake_defer_since.read().is_none(),
        "forcing a refresh through must reset the defer budget"
    );
}

/// The `power_listener_started` guard in `is_dark_wake` must short-circuit to
/// `false` when no OS power listener was started (headless / server), so
/// those processes never treat the OS power state as a dark wake. Exercises the
/// guard directly (no dark-wake override installed).
#[test]
#[serial_test::serial(force_dark_wake_env)] // reads GROK_AUTH_FORCE_DARK_WAKE
fn is_dark_wake_false_when_power_listener_not_started() {
    let _unset = pi_test_support::EnvGuard::unset("GROK_AUTH_FORCE_DARK_WAKE");
    let dir = tempfile::tempdir().unwrap();
    let mgr = AuthManager::new(dir.path(), GrokComConfig::default());
    assert!(
        !mgr.is_dark_wake(),
        "is_dark_wake must be false when the power listener was never started"
    );
}

/// `GROK_AUTH_FORCE_DARK_WAKE` forces the dark-wake answer for manual and
/// integration testing — read BEFORE the `power_listener_started` check,
/// because a headless run never starts the listener and the override
/// exists precisely so such a run can drive the dark-wake paths against a
/// real binary.
#[test]
#[serial_test::serial(force_dark_wake_env)]
fn is_dark_wake_env_override_forces_both_states() {
    use pi_test_support::EnvGuard;
    let dir = tempfile::tempdir().unwrap();
    let mgr = AuthManager::new(dir.path(), GrokComConfig::default());
    // Precondition: no power listener, so without the override this is
    // unconditionally false.
    {
        let _g = EnvGuard::set("GROK_AUTH_FORCE_DARK_WAKE", "1");
        assert!(
            mgr.is_dark_wake(),
            "=1 must force dark wake even without a power listener"
        );
    }
    {
        let _g = EnvGuard::set("GROK_AUTH_FORCE_DARK_WAKE", "0");
        assert!(!mgr.is_dark_wake(), "=0 must force full wake");
    }
    {
        // Unrecognized values fall through to the OS query (listener not
        // started here, so false) rather than picking a state.
        let _g = EnvGuard::set("GROK_AUTH_FORCE_DARK_WAKE", "yes");
        assert!(!mgr.is_dark_wake(), "non-1/0 values must not force a state");
    }
}

// ── manual_auth KPI ──────────────────────────────────────────

#[test]
fn manual_auth_reason_maps_terminal_and_skips_non_forcing() {
    use crate::auth::error::RefreshTokenFailedReason as Reason;
    use crate::auth::recovery::manual_auth_reason;
    use pi_telemetry::events::ManualAuthReason as R;

    let permanent = |reason: Reason| manual_auth_reason(&AuthError::permanent(reason));
    // A revoked refresh token forces a re-login -> counts.
    assert_eq!(
        permanent(Reason::RefreshTokenRejected),
        Some(R::RefreshTokenRejected)
    );
    // Every terminal pipeline error maps to its own bucket (a swapped mapping
    // would mis-attribute the KPI).
    assert_eq!(
        manual_auth_reason(&AuthError::ServerRejectedNoRecovery),
        Some(R::NoRefreshAuthority)
    );
    assert_eq!(
        manual_auth_reason(&AuthError::RecoveryExhausted),
        Some(R::RecoveryExhausted)
    );
    assert_eq!(
        manual_auth_reason(&AuthError::TokenExpiredNoRefresh),
        Some(R::TokenExpiredNoRefresh)
    );
    assert_eq!(
        manual_auth_reason(&AuthError::PinnedTeamMismatch {
            message: String::new()
        }),
        Some(R::WrongTeam)
    );
    // Before this reason existed these lockouts hid under the self-healing
    // `Other` bucket and never surfaced in the KPI at all.
    assert_eq!(
        permanent(Reason::ProviderInteractiveRequired),
        Some(R::ProviderInteractiveRequired)
    );
    // Self-healing (TTL) reasons, transient / no-credential, and API-key
    // lockouts (out of scope for this KPI) don't count.
    assert_eq!(permanent(Reason::ClientRejected), None);
    assert_eq!(permanent(Reason::Other), None);
    assert_eq!(manual_auth_reason(&AuthError::transient("x")), None);
    assert_eq!(manual_auth_reason(&AuthError::NotLoggedIn), None);
    assert_eq!(manual_auth_reason(&AuthError::ApiKeyAuthDisabled), None);
}

// Async so `record` has a runtime for its telemetry `tokio::spawn`: another
// test in the same process can enable the global telemetry client, which would
// otherwise make this emit path panic under a plain `#[test]`.
#[tokio::test]
async fn manual_auth_capture_attributes_and_recorder_debounces() {
    use crate::auth::recovery::{ManualAuthTracker, RejectedAuth};
    use pi_telemetry::events::{AuthTokenKind, ManualAuthSurface};

    let auth = GrokAuth {
        key: "dead-token".into(),
        user_id: "user-1".into(),
        auth_mode: AuthMode::Oidc,
        refresh_token: Some("rt".into()),
        ..GrokAuth::test_default()
    };
    let snap = RejectedAuth::capture(Some(&auth));
    assert_eq!(snap.principal_for_test(), Some("user-1"));
    assert_eq!(snap.token_kind_for_test(), AuthTokenKind::OidcSession);

    let rec = ManualAuthTracker::default();
    let last = || rec.last_token_for_test();
    // Records once; a repeat on the same credential debounces.
    rec.record(
        &snap,
        &AuthError::RecoveryExhausted,
        ManualAuthSurface::Turn,
    );
    let id = last();
    assert!(id.is_some());
    rec.record(
        &snap,
        &AuthError::PinnedTeamMismatch {
            message: String::new(),
        },
        ManualAuthSurface::Turn,
    );
    assert_eq!(last(), id);

    // A different credential re-arms.
    let rearmed = GrokAuth {
        key: "another-token".into(),
        ..auth.clone()
    };
    let fresh = RejectedAuth::capture(Some(&rearmed));
    rec.record(
        &fresh,
        &AuthError::RecoveryExhausted,
        ManualAuthSurface::Turn,
    );
    assert!(last().is_some() && last() != id);

    // A self-healing reason never emits — the KPI counts only forced re-logins.
    let healing = ManualAuthTracker::default();
    healing.record(
        &snap,
        &AuthError::permanent(crate::auth::error::RefreshTokenFailedReason::ClientRejected),
        ManualAuthSurface::Turn,
    );
    assert!(healing.last_token_for_test().is_none());
}

// ── requires_manual_reauth: transient-vs-terminal authority ─────────

// ── proactive_failure_backoff ────────────────────────────────────────

// ── try_devbox_recovery: the wait-on-the-lock double-check ───────────

/// Seed a credential that is locally valid but that the caller has been told
/// the server rejects — the shape that made the double-check lie.
fn devbox_manager(dir: &std::path::Path, key: &str) -> Arc<AuthManager> {
    let mgr = Arc::new(AuthManager::new(dir, GrokComConfig::default()));
    mgr.set_devbox_env_for_test(true);
    mgr.hot_swap(GrokAuth {
        key: key.into(),
        auth_mode: AuthMode::External,
        expires_at: Some(Utc::now() + Duration::hours(1)),
        ..GrokAuth::test_default()
    });
    mgr
}

/// The credential the caller already knows is dead can never be the answer.
///
/// `try_devbox_recovery` short-circuits on whatever `current()` holds, to
/// catch a sibling task that refreshed while we waited on `refresh_lock`.
/// Told nothing about the rejected bearer it used to return that bearer, so
/// on a devbox every 401 against a still-locally-valid token reported
/// "recovered" and the turn resubmitted it until its retry budget ran out.
#[tokio::test]
async fn devbox_recovery_never_re_serves_the_credential_it_was_given_up_on() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = devbox_manager(dir.path(), "rejected-but-locally-valid");
    assert!(
        mgr.current().is_some(),
        "precondition: the rejected bearer is still locally valid"
    );

    // Asserted as "not this credential" rather than as an error: on a real
    // devbox the mint can genuinely succeed, and a *different* credential is
    // exactly the outcome we want. Everywhere else there is no mint endpoint
    // and this is an error.
    let outcome = mgr
        .try_devbox_recovery(Some("rejected-but-locally-valid"))
        .await;
    assert!(
        !matches!(&outcome, Ok(auth) if auth.key == "rejected-but-locally-valid"),
        "recovery must not report success with the rejected bearer, got {outcome:?}"
    );
}

/// The double-check still does its job: a credential that is *not* the one
/// the caller gave up on means a sibling task refreshed, so take it and skip
/// the mint.
#[tokio::test]
async fn devbox_recovery_short_circuits_on_a_credential_someone_else_landed() {
    let dir = tempfile::tempdir().unwrap();
    let mgr = devbox_manager(dir.path(), "landed-by-a-sibling-task");

    let auth = mgr
        .try_devbox_recovery(Some("the-bearer-the-server-rejected"))
        .await
        .expect("a different live credential is a recovery");
    assert_eq!(auth.key, "landed-by-a-sibling-task");
}
