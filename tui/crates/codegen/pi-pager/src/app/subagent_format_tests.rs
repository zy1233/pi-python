use super::test_support::make_info;
use super::*;

#[test]
fn subagent_meta_line_joins_present_fields() {
    let cases = [
        (None, None, None, ""),
        (
            Some("researcher"),
            Some("analyst"),
            Some("grok-3"),
            " (researcher \u{00b7} analyst \u{00b7} grok-3)",
        ),
        (
            Some("researcher"),
            None,
            Some("grok-3"),
            " (researcher \u{00b7} grok-3)",
        ),
        (
            Some("reviewer"),
            Some("reviewer"),
            Some("grok-3"),
            " (reviewer \u{00b7} grok-3)",
        ),
        (
            Some("researcher"),
            Some("analyst"),
            None,
            " (researcher \u{00b7} analyst)",
        ),
        (None, Some("reviewer"), None, " (reviewer)"),
        (Some("reviewer"), None, None, " (reviewer)"),
        (Some(""), Some(" "), Some("grok-3"), " (grok-3)"),
    ];
    for (persona, role, model, expected) in cases {
        assert_eq!(
            format_subagent_meta(persona, role, model),
            expected,
            "persona={persona:?} role={role:?} model={model:?}"
        );
    }
}

#[test]
fn subagent_type_label_abbreviates_general_purpose() {
    let cases = [
        ("general-purpose", "general"),
        ("explore", "explore"),
        ("plan", "plan"),
        ("custom-agent", "custom-agent"),
    ];
    for (input, expected) in cases {
        assert_eq!(format_type_label(input), expected);
    }
}

#[test]
fn context_badge_shown_only_for_resumed_and_forked() {
    let cases = [
        (Some("resumed"), "resumed"),
        (Some("forked"), "forked"),
        (Some("new"), ""),
        (None, ""),
    ];
    for (source, expected) in cases {
        let mut info = make_info();
        info.context_source = source.map(Into::into);
        assert_eq!(format_context_badge(&info), expected, "source={source:?}");
    }
}

#[test]
fn subagent_label_prefers_persona_then_role_then_type_then_tag() {
    struct Case {
        persona: Option<&'static str>,
        role: Option<&'static str>,
        subagent_type: &'static str,
        description: &'static str,
        label: &'static str,
        desc: &'static str,
    }
    let case = |persona, role, subagent_type, description, label, desc| Case {
        persona,
        role,
        subagent_type,
        description,
        label,
        desc,
    };
    let cases = [
        case(
            Some("implementer"),
            Some("any"),
            "general-purpose",
            "test task",
            "Implementer",
            "test task",
        ),
        case(
            None,
            Some("analyst"),
            "general-purpose",
            "test task",
            "Analyst",
            "test task",
        ),
        case(
            None,
            None,
            "explore",
            "[deep-dive] find auth code",
            "Explore",
            "find auth code",
        ),
        case(
            None,
            None,
            "general-purpose",
            "[security-fix] patch XSS",
            "Security-fix",
            "patch XSS",
        ),
        case(
            None,
            None,
            "general-purpose",
            "do a thing",
            "General",
            "do a thing",
        ),
        case(
            Some("reviewer"),
            None,
            "general-purpose",
            "[review] check the diff",
            "Reviewer",
            "check the diff",
        ),
        case(
            Some("   "),
            Some("analyst"),
            "general-purpose",
            "test task",
            "Analyst",
            "test task",
        ),
        case(
            None,
            None,
            "general-purpose",
            "[] do something",
            "General",
            "[] do something",
        ),
        case(
            None,
            None,
            "general-purpose",
            "[broken description",
            "General",
            "[broken description",
        ),
        case(
            None,
            None,
            "custom-agent",
            "test task",
            "Custom-agent",
            "test task",
        ),
        case(
            Some("Reviewer"),
            None,
            "explore",
            "test task",
            "Reviewer",
            "test task",
        ),
    ];
    for c in cases {
        let mut info = make_info();
        info.persona = c.persona.map(Into::into);
        info.role = c.role.map(Into::into);
        info.subagent_type = c.subagent_type.into();
        info.description = c.description.into();
        let (got_label, got_desc) = format_subagent_label(&info);
        assert_eq!(got_label, c.label, "label for {:?}", c.description);
        assert_eq!(got_desc, c.desc, "desc for {:?}", c.description);
    }
}

#[test]
fn activity_label_rendered_for_each_turn_activity() {
    use crate::acp::tracker::{TurnActivity, WaitingReason};
    let long = "a".repeat(40);
    let cases: Vec<(TurnActivity, String)> = vec![
        (TurnActivity::Thinking, "Thinking".into()),
        (TurnActivity::Responding, "Responding".into()),
        (TurnActivity::AutoCompacting, "Compacting".into()),
        (
            TurnActivity::Retrying {
                attempt: 2,
                max_retries: 5,
                reason: "rate limited".into(),
            },
            "Retrying (2/5)".into(),
        ),
        (
            TurnActivity::Waiting(WaitingReason::subagent()),
            "Waiting on subagent…".into(),
        ),
        (
            TurnActivity::Waiting(WaitingReason::task_output()),
            "Waiting on task output…".into(),
        ),
        (
            TurnActivity::Waiting(WaitingReason::TaskOutput {
                task_ids: vec!["t1".into()],
                subject: Some("run tests".into()),
                waits: false,
            }),
            "run tests…".into(),
        ),
        (
            TurnActivity::ToolRunning {
                title: String::new(),
                description: None,
            },
            "Running tool".into(),
        ),
        (
            TurnActivity::ToolRunning {
                title: "cargo build".into(),
                description: None,
            },
            "Running: cargo build".into(),
        ),
        // ASCII exactly at the char limit stays untruncated.
        (
            TurnActivity::ToolRunning {
                title: long.clone(),
                description: None,
            },
            format!("Running: {long}"),
        ),
        // Over the limit truncates to 40 chars plus an ellipsis.
        (
            TurnActivity::ToolRunning {
                title: "a".repeat(60),
                description: None,
            },
            format!("Running: {long}…"),
        ),
        // Multibyte over the byte threshold but under the char limit is kept whole.
        (
            TurnActivity::ToolRunning {
                title: "\u{00e9}".repeat(35),
                description: None,
            },
            format!("Running: {}", "\u{00e9}".repeat(35)),
        ),
        // Multibyte over the char limit truncates by chars, not bytes.
        (
            TurnActivity::ToolRunning {
                title: "\u{00e9}".repeat(45),
                description: None,
            },
            format!("Running: {}…", "\u{00e9}".repeat(40)),
        ),
        (
            TurnActivity::ToolRunning {
                title: "first line\nsecond line".into(),
                description: None,
            },
            "Running: first line".into(),
        ),
    ];
    for (activity, expected) in &cases {
        assert_eq!(&format_activity_label(activity), expected);
    }
}

