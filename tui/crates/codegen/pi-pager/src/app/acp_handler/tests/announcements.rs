#![cfg_attr(rustfmt, rustfmt::skip)]
    use super::*;

    /// Stale or duplicate gens short-circuit BEFORE the seam (and its config
    /// disk loads) runs — nothing observable may change.
    #[test]
    fn announcements_update_stale_gen_short_circuits_before_apply() {
        let mut app = make_app_with_agent("sess-ann");
        app.announcements_last_gen = 5;
        app.active_announcements = vec![critical_announcement("current")];
        // Marker the seam cannot leave intact: it matches no pushed
        // announcement, so any apply would prune it (and queue a persist).
        app.hidden_announcement_ids = ["stale-key".to_string()].into_iter().collect();

        for stale_gen in [4, 5] {
            let changed = handle_ext_notification(
                &announcements_update_notif(stale_gen, &[critical_announcement("stale-push")]),
                &mut app,
            );
            assert!(!changed, "gen {stale_gen} must be dropped at the watermark");
        }

        assert_eq!(
            app.active_announcements,
            vec![critical_announcement("current")]
        );
        assert_eq!(app.announcements_last_gen, 5);
        assert!(app.hidden_announcement_ids.contains("stale-key"));
        assert!(
            !app.pending_effects
                .iter()
                .any(|e| matches!(e, Effect::PersistAnnouncementsHidden { .. })),
            "short-circuit must not reach prune/persist, got {:?}",
            app.pending_effects
        );
    }

