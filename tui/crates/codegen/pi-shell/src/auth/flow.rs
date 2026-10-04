

#[cfg(test)]
mod tests {
    

    
    
    
    
    

    // ── run_auth_flow: expired path with disk token ─────────────────

    #[test]
    fn extract_url_from_external_provider_stderr() {
        let extract = |input: &str| -> String {
            input
                .split_whitespace()
                .find(|w| w.starts_with("https://"))
                .map(|u| u.to_owned())
                .unwrap_or_else(|| input.to_owned())
        };

        // Preamble text with URL
        assert_eq!(
            extract(
                "Visit the following link to sign into Grok: https://auth.example.com/login?code=abc"
            ),
            "https://auth.example.com/login?code=abc"
        );

        // Multi-line with URL on second line
        assert_eq!(
            extract("Please sign in below\nhttps://auth.example.com/sso"),
            "https://auth.example.com/sso"
        );

        // Just a bare URL
        assert_eq!(
            extract("https://auth.example.com/login"),
            "https://auth.example.com/login"
        );

        // No URL at all — fallback to full content
        assert_eq!(extract("some opaque output"), "some opaque output");
    }

    const _: () = assert!(
        crate::http::STARTUP_AUTH_REFRESH_TIMEOUT.as_millis()
            < crate::auth::manager::REFRESH_LOCK_TIMEOUT.as_millis(),
        "the startup refresh bound must fire before the lock convoy budget"
    );

}
