#[cfg(test)]
mod permission_analytics_tests {

    use pi_telemetry::events::{
        PermissionClassifierSource, PermissionClassifierVerdict, PermissionDecisionReason,
        PermissionPromptOutcome, PermissionSecurityFinding,
    };
    use pi_workspace::permission::{ClassifierSecurityFinding, reasons};

    /// Drift guard: the telemetry reason enum is a bijection with the manager's
    /// owned `reasons::ALL` vocabulary. Adding a reason on either side without the
    /// other fails this (unlike the previous hand-copied cross-crate list).
    #[test]
    fn decision_reason_enum_matches_manager_vocabulary() {
        use std::collections::BTreeSet;
        for r in reasons::ALL {
            assert!(
                PermissionDecisionReason::try_from(*r).is_ok(),
                "manager reason {r} is not mapped by PermissionDecisionReason"
            );
        }
        let manager: BTreeSet<&str> = reasons::ALL.iter().copied().collect();
        let enum_wire: BTreeSet<String> = PermissionDecisionReason::ALL
            .iter()
            .map(|r| {
                serde_json::to_value(r)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_owned()
            })
            .collect();
        let enum_refs: BTreeSet<&str> = enum_wire.iter().map(String::as_str).collect();
        assert_eq!(
            manager, enum_refs,
            "manager reasons and PermissionDecisionReason must be identical sets"
        );
    }

    /// Drift guard for the finding vocabulary against the workspace owner.
    #[test]
    fn security_finding_enum_matches_manager_tokens() {
        use std::collections::BTreeSet;
        let manager: BTreeSet<&str> = ClassifierSecurityFinding::ALL
            .iter()
            .map(|f| f.token())
            .collect();
        let enum_wire: BTreeSet<String> = PermissionSecurityFinding::ALL
            .iter()
            .map(|f| {
                serde_json::to_value(f)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_owned()
            })
            .collect();
        let enum_refs: BTreeSet<&str> = enum_wire.iter().map(String::as_str).collect();
        assert_eq!(
            manager, enum_refs,
            "manager finding tokens and PermissionSecurityFinding must be identical sets"
        );
    }

    /// Drift guard: every prompt-outcome wire the manager can emit — the
    /// structurally-generated owner projection `PromptOutcomeKind::ALL` (the
    /// enum/`ALL`/`wire_str` the manager itself emits via
    /// `PromptOutcome::kind().wire_str()`) — normalizes to a telemetry category.
    /// A new owner kind (one `wire_enum!` list entry) that lacks a
    /// `PermissionPromptOutcome` mapping fails here rather than being silently
    /// omitted by the shell.
    #[test]
    fn prompt_outcome_covers_manager_vocabulary() {
        use pi_workspace::permission::PromptOutcomeKind;
        for kind in PromptOutcomeKind::ALL {
            let wire = kind.wire_str();
            assert!(
                PermissionPromptOutcome::try_from(wire).is_ok(),
                "manager prompt outcome {wire} is not mapped by PermissionPromptOutcome"
            );
        }
    }

    /// Drift guard: the outcome-detail enum is a bijection with the manager's
    /// `PromptOutcomeKind::ALL` wire vocabulary, so a new "Always allow"
    /// surface cannot be silently dropped from adoption analytics.
    #[test]
    fn prompt_outcome_detail_matches_manager_vocabulary() {
        use pi_telemetry::events::PermissionPromptOutcomeDetail;
        use pi_workspace::permission::PromptOutcomeKind;
        use std::collections::BTreeSet;
        let manager: BTreeSet<&str> = PromptOutcomeKind::ALL
            .iter()
            .map(|k| k.wire_str())
            .collect();
        let enum_wire: BTreeSet<String> = PermissionPromptOutcomeDetail::ALL
            .iter()
            .map(|d| {
                serde_json::to_value(d)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_owned()
            })
            .collect();
        let enum_refs: BTreeSet<&str> = enum_wire.iter().map(String::as_str).collect();
        assert_eq!(
            manager, enum_refs,
            "manager prompt-outcome wires and PermissionPromptOutcomeDetail must be identical sets"
        );
    }

    /// Drift guard: the classifier-source enum is a bijection with the workspace
    /// owner projection `ClassifierSourceKind::ALL` (the full source vocabulary —
    /// classifier provenances plus `fast_path`/`not_wired` — generated from one
    /// list). A new owner kind not mirrored by the telemetry enum fails here.
    #[test]
    fn classifier_source_enum_matches_manager_vocabulary() {
        use pi_workspace::permission::ClassifierSourceKind;
        use std::collections::BTreeSet;
        let manager: BTreeSet<&str> = ClassifierSourceKind::ALL
            .iter()
            .map(|k| k.wire_str())
            .collect();
        let enum_wire: BTreeSet<String> = PermissionClassifierSource::ALL
            .iter()
            .map(|s| {
                serde_json::to_value(s)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_owned()
            })
            .collect();
        let enum_refs: BTreeSet<&str> = enum_wire.iter().map(String::as_str).collect();
        assert_eq!(
            manager, enum_refs,
            "manager classifier sources and PermissionClassifierSource must be identical sets"
        );
    }

    /// Drift guard: the classifier-verdict enum is a bijection with the workspace
    /// owner projection `ClassifierVerdict::ALL` (generated from one list).
    #[test]
    fn classifier_verdict_enum_matches_manager_vocabulary() {
        use pi_workspace::permission::ClassifierVerdict;
        use std::collections::BTreeSet;
        let manager: BTreeSet<&str> = ClassifierVerdict::ALL
            .iter()
            .map(|v| v.wire_str())
            .collect();
        let enum_wire: BTreeSet<String> = PermissionClassifierVerdict::ALL
            .iter()
            .map(|v| {
                serde_json::to_value(v)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_owned()
            })
            .collect();
        let enum_refs: BTreeSet<&str> = enum_wire.iter().map(String::as_str).collect();
        assert_eq!(
            manager, enum_refs,
            "manager classifier verdicts and PermissionClassifierVerdict must be identical sets"
        );
    }
}
