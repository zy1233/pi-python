//! Tests for the browser-unavailable fallback of `Action::OpenUrl`.

use super::*;

const OPEN_URL_TEST_LINK: &str = "https://example.com/docs";

/// When the OS browser opener cannot run (simulated via a broken
/// `GROK_TEST_OPEN_URL_FILE` seam), `Action::OpenUrl` must push a scrollback
/// system message that includes the full URL — the headless-VM fix for
/// silent link no-ops.
#[serial_test::serial(GROK_TEST_OPEN_URL_FILE)]
#[test]
fn open_url_shows_manual_url_when_browser_unavailable() {
    // Point the test seam at a path whose parent dir does not exist so the
    // write fails and `open_url` returns false (BrowserUnavailable).
    let bad = std::env::temp_dir().join(format!(
        "grok-open-url-missing-{}/out.txt",
        std::process::id()
    ));
    // SAFETY: serialized via `serial_test` so no other test races the env var.
    unsafe { std::env::set_var("GROK_TEST_OPEN_URL_FILE", &bad) };

    let mut app = test_app_with_agent();
    let before = agent_scrollback_len(&app);
    let url = OPEN_URL_TEST_LINK;
    let effects = dispatch(Action::OpenUrl(url.to_string()), &mut app);
    assert!(effects.is_empty());

    assert_eq!(
        agent_scrollback_len(&app),
        before + 1,
        "must push a system message with the URL"
    );
    let text = last_system_text(&app, AgentId(0));
    assert_eq!(
        text,
        crate::app::link_opener::browser_unavailable_message(url)
    );
    let toast = app.agents[&AgentId(0)]
        .toast
        .as_ref()
        .map(|(m, _)| m.as_str());
    assert_eq!(toast, Some("Browser unavailable - URL shown above"));

    // SAFETY: serialized via `serial_test`; restore the env for other tests.
    unsafe { std::env::remove_var("GROK_TEST_OPEN_URL_FILE") };
}

/// Successful open (test seam write OK) must not spam a fallback system message.
#[serial_test::serial(GROK_TEST_OPEN_URL_FILE)]
#[test]
fn open_url_does_not_show_fallback_when_opener_succeeds() {
    let url_file =
        std::env::temp_dir().join(format!("grok-open-url-ok-{}.txt", std::process::id()));
    let _ = std::fs::remove_file(&url_file);
    // SAFETY: serialized via `serial_test`.
    unsafe { std::env::set_var("GROK_TEST_OPEN_URL_FILE", &url_file) };

    let mut app = test_app_with_agent();
    let before = agent_scrollback_len(&app);
    let url = OPEN_URL_TEST_LINK;
    let _ = dispatch(Action::OpenUrl(url.to_string()), &mut app);

    assert_eq!(
        agent_scrollback_len(&app),
        before,
        "successful open must not push a fallback system message"
    );
    let recorded = std::fs::read_to_string(&url_file).unwrap_or_default();
    assert!(
        recorded.lines().any(|l| l == url),
        "opener seam must record the URL; got {recorded:?}"
    );

    // SAFETY: serialized via `serial_test`.
    unsafe { std::env::remove_var("GROK_TEST_OPEN_URL_FILE") };
    let _ = std::fs::remove_file(&url_file);
}

/// Welcome has no scrollback: browser-unavailable OpenUrl must put a
/// single-line toast that includes the full URL (no `\n` — the welcome
/// painter is one row). Privacy-banner Terms/Policy clicks hit this path.
#[serial_test::serial(GROK_TEST_OPEN_URL_FILE)]
#[test]
fn open_url_welcome_toasts_single_line_url_when_browser_unavailable() {
    let bad = std::env::temp_dir().join(format!(
        "grok-open-url-welcome-missing-{}/out.txt",
        std::process::id()
    ));
    // SAFETY: serialized via `serial_test` so no other test races the env var.
    unsafe { std::env::set_var("GROK_TEST_OPEN_URL_FILE", &bad) };

    let mut app = test_app();
    assert!(
        matches!(app.active_view, ActiveView::Welcome),
        "fixture must start on welcome"
    );

    use crate::app::link_opener::browser_unavailable_line;

    let terms = crate::views::privacy_banner::PRIVACY_BANNER_TERMS_URL;
    let effects = dispatch(Action::OpenUrl(terms.to_string()), &mut app);
    assert!(effects.is_empty());
    let toast = app
        .welcome_toast
        .as_ref()
        .map(|(m, _)| m.as_str())
        .unwrap_or("");
    // Structure only: clipboard delivery varies by host, so do not lock the
    // exact "copied" phrase here (constructor unit tests cover both arms).
    assert!(toast.starts_with(terms), "{toast}");
    assert!(!toast.contains('\n'), "{toast}");
    assert!(
        toast == browser_unavailable_line(terms, true)
            || toast == browser_unavailable_line(terms, false),
        "welcome toast must match a delivery-honest line form: {toast}"
    );

    // Policy URL is shorter than terms (compile-time constants); second toast
    // replaces the first in welcome toast state.
    let policy = crate::views::privacy_banner::PRIVACY_BANNER_POLICY_URL;
    let _ = dispatch(Action::OpenUrl(policy.to_string()), &mut app);
    let toast = app
        .welcome_toast
        .as_ref()
        .map(|(m, _)| m.as_str())
        .unwrap_or("");
    assert!(toast.starts_with(policy), "{toast}");
    assert!(
        toast == browser_unavailable_line(policy, true)
            || toast == browser_unavailable_line(policy, false),
        "second welcome toast must match a delivery-honest line form: {toast}"
    );

    // SAFETY: serialized via `serial_test`; restore the env for other tests.
    unsafe { std::env::remove_var("GROK_TEST_OPEN_URL_FILE") };
}
