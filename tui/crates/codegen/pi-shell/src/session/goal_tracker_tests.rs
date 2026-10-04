use super::*;

use super::make_base_orchestration;

#[test]
fn goal_event_unknown_string_deserializes_to_unknown() {
    // #[serde(other)] forward-compat: an unknown event from a newer shell
    // deserializes to `Unknown` instead of failing the field.
    let unknown: GoalEvent = serde_json::from_str("\"some_future_event\"").unwrap();
    assert!(matches!(unknown, GoalEvent::Unknown));
    let known: GoalEvent = serde_json::from_str("\"goal_paused\"").unwrap();
    assert!(matches!(known, GoalEvent::GoalPaused));
}

#[test]
fn plan_file_round_trips_through_serde_when_some() {
    let mut o = make_base_orchestration();
    let path = PathBuf::from("/tmp/plan-rt-session/goal/plan.md");
    o.plan_file = Some(path.clone());

    let json = serde_json::to_string(&o).unwrap();
    assert!(
        json.contains("\"plan_file\":\"/tmp/plan-rt-session/goal/plan.md\""),
        "plan_file must appear on the wire as a string: {json}",
    );

    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();
    assert_eq!(restored.plan_file, Some(path));
}

#[test]
fn plan_file_omitted_from_json_when_none() {
    let o = make_base_orchestration();
    assert!(o.plan_file.is_none());

    let json = serde_json::to_string(&o).unwrap();
    assert!(
        !json.contains("plan_file"),
        "plan_file=None must be skipped on serialize: {json}",
    );

    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();
    assert!(restored.plan_file.is_none());
}

/// A legacy snapshot written before this PR has no `plan_file`
/// key; `#[serde(default)]` must backfill `None`.
#[test]
fn plan_file_backfills_none_on_legacy_snapshot() {
    const LEGACY: &str = r#"{
            "goal_id": "g-legacy",
            "objective": "legacy goal",
            "status": "active",
            "phase": "Idle",
            "token_budget": null,
            "elapsed_ms": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "current_subagent_id": null,
            "current_subagent_role": null,
            "total_worker_rounds": 0,
            "total_verify_rounds": 0,
            "token_baseline": 0,
            "history": [],
            "verifier_id": "abcdef012345"
        }"#;
    let loaded: GoalOrchestration =
        serde_json::from_str(LEGACY).expect("legacy snapshot must deserialize");
    assert!(loaded.plan_file.is_none());
}

/// `generate_verifier_id` must produce 12-char hex strings and
/// fresh values on every call. The fixed length is part of the
/// public contract — verifier file paths embed it verbatim, and
/// drift here would silently invalidate the documented
/// `grok-goal-<12 hex chars>` scratch-root format (and the
/// 12-hex restore validation in `from_snapshot`).
#[test]
fn generate_verifier_id_is_short_hex_and_unique() {
    let a = generate_verifier_id();
    let b = generate_verifier_id();
    assert_eq!(a.len(), 12, "verifier id must be exactly 12 chars: {a}");
    assert_eq!(b.len(), 12, "verifier id must be exactly 12 chars: {b}");
    assert!(
        a.chars().all(|c| c.is_ascii_hexdigit()),
        "verifier id must be hex: {a}"
    );
    assert_ne!(a, b, "two successive ids must differ");
}

/// Legacy snapshots predate `consecutive_not_achieved` /
/// `last_strategy_*`; `#[serde(default)]` must backfill them so an
/// upgrade doesn't fail to deserialize.
#[test]
fn strategist_fields_backfill_on_legacy_snapshot() {
    const LEGACY: &str = r#"{
            "goal_id": "g-legacy",
            "objective": "legacy goal",
            "status": "active",
            "phase": "Idle",
            "token_budget": null,
            "elapsed_ms": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "current_subagent_id": null,
            "current_subagent_role": null,
            "total_worker_rounds": 0,
            "total_verify_rounds": 0,
            "token_baseline": 0,
            "history": [],
            "verifier_id": "abcdef012345"
        }"#;
    let loaded: GoalOrchestration =
        serde_json::from_str(LEGACY).expect("legacy snapshot must deserialize");
    assert_eq!(loaded.consecutive_not_achieved, 0);
    assert_eq!(loaded.last_strategist_fired_at, 0);
    assert!(loaded.last_strategy_path.is_none());
    assert!(loaded.last_strategy_recommendation.is_none());
}

/// `NoProgressPaused` round-trips through both serde wire forms (snake_case
/// `Serialize` + `from_wire_str`) and stays distinct from the cap pause's
/// `back_off_paused`.
#[test]
fn no_progress_paused_round_trips_distinctly_from_back_off() {
    assert_eq!(
        GoalStatus::from_wire_str("no_progress_paused"),
        GoalStatus::NoProgressPaused
    );
    let json = serde_json::to_string(&GoalStatus::NoProgressPaused).unwrap();
    assert_eq!(json, "\"no_progress_paused\"");
    let back: GoalStatus = serde_json::from_str(&json).unwrap();
    assert_eq!(back, GoalStatus::NoProgressPaused);
    // The cap pause keeps its own wire form — the two must not collapse.
    assert_eq!(
        GoalStatus::from_wire_str("back_off_paused"),
        GoalStatus::BackOffPaused
    );
    assert_ne!(GoalStatus::NoProgressPaused, GoalStatus::BackOffPaused);
}

#[test]
fn legacy_pascal_case_paused_deserializes_to_user_paused() {
    let legacy = r#""Paused""#;
    let parsed: GoalStatus = serde_json::from_str(legacy).unwrap();
    assert_eq!(parsed, GoalStatus::UserPaused);
}

#[test]
fn legacy_pascal_case_other_variants_deserialize() {
    for (legacy, expected) in [
        (r#""Active""#, GoalStatus::Active),
        (r#""BudgetLimited""#, GoalStatus::BudgetLimited),
        (r#""Complete""#, GoalStatus::Complete),
    ] {
        let parsed: GoalStatus = serde_json::from_str(legacy).unwrap();
        assert_eq!(parsed, expected, "legacy {legacy} must parse");
    }
}

#[test]
fn legacy_infra_paused_deserializes() {
    let parsed: GoalStatus = serde_json::from_str(r#""infra_paused""#).unwrap();
    assert_eq!(parsed, GoalStatus::InfraPaused);
}

#[test]
fn unknown_future_paused_status_deserializes_to_user_paused() {
    let parsed: GoalStatus = serde_json::from_str(r#""error_paused""#).unwrap();
    assert_eq!(parsed, GoalStatus::UserPaused);
}

/// Any unknown wire status — not just `*_paused` forms — must restore
/// as a resumable paused goal, never an Active self-driving one.
#[test]
fn unknown_non_paused_status_deserializes_to_user_paused_not_active() {
    for wire in [r#""quarantined""#, r#""v9_super_active""#, r#""""#] {
        let parsed: GoalStatus = serde_json::from_str(wire).unwrap();
        assert_eq!(parsed, GoalStatus::UserPaused, "wire {wire}");
    }
    assert_eq!(
        GoalStatus::from_wire_str("not-a-status"),
        GoalStatus::UserPaused,
    );
}

#[test]
fn serde_round_trip_infra_paused_with_pause_message() {
    let mut o = make_base_orchestration();
    o.status = GoalStatus::InfraPaused;
    o.pause_message = Some("Turn failed: rate limit".into());
    let json = serde_json::to_string(&o).unwrap();
    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();
    assert_eq!(restored.status, GoalStatus::InfraPaused);
    assert_eq!(
        restored.pause_message.as_deref(),
        Some("Turn failed: rate limit")
    );
}

/// A snapshot written by an older shell will omit all nine
/// classifier fields (incl. the stall `last_gap_fingerprint` /
/// `classifier_stall_count` and the persisted `last_classifier_gaps`
/// summary added later). Deserialization must
/// succeed and every new field must come back at its
/// `#[serde(default)]` value. The
/// JSON literal below doubles as a documented v0 schema contract
/// — if a future PR breaks legacy load, this test fails *because*
/// the documented shape no longer parses, not because the in-code
/// serialization shape happened to drift in sync.
#[test]
fn classifier_fields_backwards_compat_defaults_on_legacy_snapshot() {
    const LEGACY_SNAPSHOT_JSON: &str = r#"{
            "goal_id": "g-legacy",
            "objective": "legacy goal",
            "status": "active",
            "phase": "Executing",
            "token_budget": null,
            "tokens_used": 0,
            "elapsed_ms": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "current_subagent_id": null,
            "current_subagent_role": null,
            "total_worker_rounds": 0,
            "total_verify_rounds": 0,
            "token_baseline": 0,
            "finished_subagent_tokens": 0,
            "history": [],
            "verifier_id": "abcdef012345"
        }"#;
    let legacy: GoalOrchestration =
        serde_json::from_str(LEGACY_SNAPSHOT_JSON).expect("legacy snapshot must deserialize");
    assert_eq!(legacy.goal_id, "g-legacy");
    assert_eq!(legacy.verifier_id, "abcdef012345");
    assert_eq!(legacy.classifier_runs_attempted, 0);
    assert!(legacy.classifier_max_runs.is_none());
    assert!(legacy.last_classifier_verdict.is_none());
    assert!(legacy.last_classifier_details_path.is_none());
    assert!(legacy.last_classifier_at.is_none());
    assert!(legacy.last_classifier_gaps.is_none());
    assert!(legacy.skeptic0_session_id.is_none());
    assert!(legacy.changes_baseline_commit.is_none());
    assert!(legacy.last_gap_fingerprint.is_none());
    assert_eq!(legacy.classifier_stall_count, 0);
    // New transient `#[serde(skip)]` field defaults empty for a
    // legacy snapshot that predates it.
    assert!(legacy.live_tokens_by_model.is_empty());
    // Spend-accumulator backfill: zero spent, `None` anchor so the
    // first `goal_tokens` call seeds from `token_baseline`.
    assert_eq!(legacy.parent_tokens_spent, 0);
    assert_eq!(legacy.last_session_tokens_seen, None);
}

/// Legacy on-disk snapshots carry the dropped `tokens_used` and
/// `finished_subagent_tokens` fields. Verify they are silently
/// ignored on load and the struct deserializes cleanly.
#[test]
fn goal_orchestration_serde_drops_legacy_tokens_used_field() {
    const LEGACY: &str = r#"{
            "goal_id": "g-old",
            "objective": "legacy",
            "status": "active",
            "phase": "Idle",
            "token_budget": null,
            "tokens_used": 123,
            "elapsed_ms": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "current_subagent_id": null,
            "current_subagent_role": null,
            "total_worker_rounds": 0,
            "total_verify_rounds": 0,
            "token_baseline": 0,
            "finished_subagent_tokens": 456,
            "history": [],
            "verifier_id": "abcdef012345"
        }"#;
    let loaded: GoalOrchestration =
        serde_json::from_str(LEGACY).expect("legacy snapshot must deserialize");
    assert_eq!(loaded.goal_id, "g-old");
    assert_eq!(loaded.token_baseline, 0);
    // Round-trip and verify the dropped keys do not reappear at
    // the top level; `tokens_used` on history entries (Option<i64>)
    // is a distinct field and `history` is empty here so the
    // simple `contains` check is sound.
    let round = serde_json::to_string(&loaded).unwrap();
    assert!(
        !round.contains("\"tokens_used\""),
        "legacy field re-serialized: {round}"
    );
    assert!(
        !round.contains("\"finished_subagent_tokens\""),
        "legacy field re-serialized: {round}"
    );
}

/// Populating every new field and round-tripping through serde
/// must return identical values. This guards both the on-disk
/// schema shape and the `Eq`-by-field semantics the verification
/// stage orchestrator relies on.
#[test]
fn classifier_fields_serde_round_trip_preserves_all_fields() {
    let mut o = make_base_orchestration();
    o.classifier_runs_attempted = 2;
    o.classifier_max_runs = Some(3);
    o.last_classifier_verdict = Some(GoalClassifierVerdict::NotAchieved);
    o.last_classifier_details_path = Some("/tmp/goal-classifier-abc.md".to_string());
    o.last_classifier_at = Some("2026-05-24T12:00:00Z".to_string());
    o.last_classifier_gaps = Some("- [skeptic 0, high] still on fire".to_string());
    o.skeptic0_session_id = Some("0190abcd-skeptic0".to_string());
    o.changes_baseline_commit = Some("abc123def456".to_string());

    let json = serde_json::to_string(&o).unwrap();
    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();

    assert_eq!(restored.classifier_runs_attempted, 2);
    assert_eq!(restored.classifier_max_runs, Some(3));
    assert_eq!(
        restored.last_classifier_verdict,
        Some(GoalClassifierVerdict::NotAchieved)
    );
    assert_eq!(
        restored.last_classifier_details_path.as_deref(),
        Some("/tmp/goal-classifier-abc.md")
    );
    assert_eq!(
        restored.last_classifier_at.as_deref(),
        Some("2026-05-24T12:00:00Z")
    );
    assert_eq!(
        restored.last_classifier_gaps.as_deref(),
        Some("- [skeptic 0, high] still on fire")
    );
    assert_eq!(
        restored.skeptic0_session_id.as_deref(),
        Some("0190abcd-skeptic0")
    );
    assert_eq!(
        restored.changes_baseline_commit.as_deref(),
        Some("abc123def456")
    );

    // Contract: `Option::None` fields must NOT serialize as
    // `"key": null`. Verify the round-trip JSON omits the keys when
    // we reset them to None on a freshly-created orchestration.
    let mut base = make_base_orchestration();
    base.classifier_max_runs = None;
    base.last_classifier_verdict = None;
    base.last_classifier_details_path = None;
    base.last_classifier_at = None;
    base.last_classifier_gaps = None;
    base.skeptic0_session_id = None;
    base.changes_baseline_commit = None;
    let json_none = serde_json::to_string(&base).unwrap();
    for key in [
        "classifier_max_runs",
        "last_classifier_verdict",
        "last_classifier_details_path",
        "last_classifier_at",
        "last_classifier_gaps",
        "skeptic0_session_id",
        "changes_baseline_commit",
    ] {
        assert!(
            !json_none.contains(key),
            "None-valued field {key} must be skipped on serialize, got: {json_none}"
        );
    }
}

/// `skeptic0_session_id` round-trips through serde when populated
/// (it must persist across a snapshot save/restore within a session
/// so the next attempt can resume skeptic 0). Mirrors the
/// `last_classifier_gaps` round-trip contract.
#[test]
fn skeptic0_session_id_round_trips_through_serde() {
    let mut o = make_base_orchestration();
    o.skeptic0_session_id = Some("0190-skeptic0-child".to_string());
    let json = serde_json::to_string(&o).unwrap();
    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();
    assert_eq!(
        restored.skeptic0_session_id.as_deref(),
        Some("0190-skeptic0-child"),
    );
}

/// Round-trips through serde so a resumed goal keeps its breadth anchor,
/// and is skipped on serialize when `None`.
#[test]
fn first_final_response_round_trips_through_serde() {
    let mut o = make_base_orchestration();
    o.first_final_response = Some("Round 1: built the whole feature; 14 tests pass.".into());
    let json = serde_json::to_string(&o).unwrap();
    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();
    assert_eq!(
        restored.first_final_response.as_deref(),
        Some("Round 1: built the whole feature; 14 tests pass."),
    );

    let base = make_base_orchestration();
    let json_none = serde_json::to_string(&base).unwrap();
    assert!(
        !json_none.contains("first_final_response"),
        "None-valued first_final_response must be skipped on serialize: {json_none}",
    );
}

/// `skeptic_model_assignment` (the frozen per-index pool) round-trips
/// through serde so the assignment survives a snapshot save/restore and
/// stays stable across resumes.
#[test]
fn skeptic_model_assignment_round_trips_through_serde() {
    let mut o = make_base_orchestration();
    o.skeptic_model_assignment = vec![
        crate::util::config::GoalRoleModel {
            model: "grok-4".to_string(),
            agent_type: "general-purpose".to_string(),
        },
        crate::util::config::GoalRoleModel {
            model: "grok-4.5".to_string(),
            agent_type: "cursor".to_string(),
        },
    ];
    let json = serde_json::to_string(&o).unwrap();
    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();
    assert_eq!(
        restored.skeptic_model_assignment,
        o.skeptic_model_assignment
    );
}

/// A legacy snapshot (no `skeptic_model_assignment` key) deserializes to
/// an empty assignment (serde default) — all skeptics inherit.
#[test]
fn legacy_snapshot_without_skeptic_model_assignment_deserializes_empty() {
    const LEGACY: &str = r#"{
            "goal_id": "g-legacy",
            "objective": "legacy goal",
            "status": "active",
            "phase": "Idle",
            "token_budget": null,
            "elapsed_ms": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "current_subagent_id": null,
            "current_subagent_role": null,
            "total_worker_rounds": 0,
            "total_verify_rounds": 0,
            "token_baseline": 0,
            "history": [],
            "verifier_id": "abcdef012345"
        }"#;
    let restored: GoalOrchestration =
        serde_json::from_str(LEGACY).expect("legacy snapshot must deserialize");
    assert!(restored.skeptic_model_assignment.is_empty());
}

/// `plan_baseline_file` round-trips through serde when populated (it
/// must persist across a snapshot save/restore so the baseline survives
/// a shell restart), and is omitted from the wire when `None`.
#[test]
fn plan_baseline_file_round_trips_through_serde() {
    let mut o = make_base_orchestration();
    o.plan_baseline_file = Some(PathBuf::from("/tmp/sess/goal/plan.baseline.md"));
    let json = serde_json::to_string(&o).unwrap();
    assert!(
        json.contains("\"plan_baseline_file\":\"/tmp/sess/goal/plan.baseline.md\""),
        "plan_baseline_file must appear on the wire: {json}",
    );
    let restored: GoalOrchestration = serde_json::from_str(&json).unwrap();
    assert_eq!(
        restored.plan_baseline_file.as_deref(),
        Some(std::path::Path::new("/tmp/sess/goal/plan.baseline.md")),
    );

    let none = make_base_orchestration();
    let json_none = serde_json::to_string(&none).unwrap();
    assert!(
        !json_none.contains("plan_baseline_file"),
        "None-valued plan_baseline_file must be skipped on serialize: {json_none}",
    );
    let restored_none: GoalOrchestration = serde_json::from_str(&json_none).unwrap();
    assert!(restored_none.plan_baseline_file.is_none());
}

/// The verdict enum must serialize in snake_case so it stays
/// consistent with `GoalStatus` and the rest of the goal-tracker
/// enums. Both variants are asserted explicitly — no wildcard.
#[test]
fn classifier_verdict_serializes_as_snake_case() {
    assert_eq!(
        serde_json::to_string(&GoalClassifierVerdict::Achieved).unwrap(),
        "\"achieved\""
    );
    assert_eq!(
        serde_json::to_string(&GoalClassifierVerdict::NotAchieved).unwrap(),
        "\"not_achieved\""
    );
    // Symmetric: deserialization accepts the same shape.
    let a: GoalClassifierVerdict = serde_json::from_str("\"achieved\"").unwrap();
    let n: GoalClassifierVerdict = serde_json::from_str("\"not_achieved\"").unwrap();
    assert_eq!(a, GoalClassifierVerdict::Achieved);
    assert_eq!(n, GoalClassifierVerdict::NotAchieved);
}

