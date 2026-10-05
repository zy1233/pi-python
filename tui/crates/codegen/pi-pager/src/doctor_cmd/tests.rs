use super::*;
use crate::clipboard::{
    ClipboardDelivery, ClipboardRoute, NativeClipboardPreflight, Osc52Capability,
};
use crate::diagnostics::probes::{
    RuntimeEvidence, TmuxProbeFacts, TmuxProbeResult, WaylandProbeFacts,
};
use crate::diagnostics::{
    ClipboardFacts, ColorFacts, DataControlFact, DiagnosticFacts, DiagnosticFinding, DiagnosticId,
    DiagnosticReport, FindingDisposition, NewlineFact, ProbeNote,
    ProbeStatus, RuntimeFact,
};
use crate::host::{DisplayServer, HostOs};
use crate::terminal::{
    ByobuBackend, ModifierFate, MultiplexerKind, TerminalContext, TerminalName,
};
use crate::theme::{ThemeKind, color_support::ColorLevel};

static TMUX_ROUTE: ClipboardRoute = ClipboardRoute {
    native: true,
    tmux_buffer: true,
    osc52: true,
    osc52_tmux_passthrough: true,
};

static LOCAL_ROUTE: ClipboardRoute = ClipboardRoute {
    native: true,
    tmux_buffer: false,
    osc52: false,
    osc52_tmux_passthrough: false,
};

fn tmux_facts(
    set_clipboard: TmuxProbeResult<String>,
    control_mode: TmuxProbeResult<bool>,
) -> TmuxProbeFacts {
    TmuxProbeFacts {
        version: TmuxProbeResult::Unavailable,
        extended_keys: TmuxProbeResult::Unavailable,
        set_clipboard,
        allow_passthrough_support: TmuxProbeResult::Available(()),
        allow_passthrough: TmuxProbeResult::Available("on".to_owned()),
        control_mode,
        client_features: TmuxProbeResult::Unavailable,
    }
}

fn unavailable_tmux_facts() -> TmuxProbeFacts {
    TmuxProbeFacts {
        version: TmuxProbeResult::Unavailable,
        extended_keys: TmuxProbeResult::Unavailable,
        set_clipboard: TmuxProbeResult::Unavailable,
        allow_passthrough_support: TmuxProbeResult::Unavailable,
        allow_passthrough: TmuxProbeResult::Unavailable,
        control_mode: TmuxProbeResult::Unavailable,
        client_features: TmuxProbeResult::Unavailable,
    }
}

fn healthy_report() -> DiagnosticReport {
    DiagnosticReport {
        facts: DiagnosticFacts {
            terminal: TerminalName::Ghostty,
            xtversion: RuntimeFact::NoReply,
            multiplexer: MultiplexerKind::Undetected,
            byobu: None,
            ssh: false,
            tmux: crate::diagnostics::TmuxFacts {
                extended_keys: crate::diagnostics::TmuxOptionFact::Unavailable,
                set_clipboard: crate::diagnostics::TmuxOptionFact::Unavailable,
                allow_passthrough_support: crate::diagnostics::TmuxSupportFact::Unavailable,
                allow_passthrough: crate::diagnostics::TmuxOptionFact::Unavailable,
                color_passthrough: crate::diagnostics::TmuxColorPassthrough::Unknown,
            },
            color: ColorFacts {
                level: RuntimeFact::Available(ColorLevel::TrueColor),
                available_themes: ThemeKind::ALL.to_vec(),
                total_themes: ThemeKind::ALL.len(),
            },
            keyboard: None,
            newline: None,
            clipboard: ClipboardFacts {
                native_route: true,
                native_tool: "pbcopy".to_owned(),
                native_preflight: NativeClipboardPreflight::LocalAvailable,
                tmux_route: false,
                osc52_route: false,
                osc52_capability: Osc52Capability::Supported,
                wrap_sink: false,
                display_server: DisplayServer::Unknown,
                container_no_display: false,
                data_control: DataControlFact::NotApplicable,
                delivery: ClipboardDelivery::Confirmed,
                fix: None,
            },
            voice: None,
        },
        findings: Vec::new(),
        probe_notes: Vec::new(),
    }
}

#[test]
fn fake_standalone_facts_compose_through_shared_view() {
    let terminal = TerminalContext {
        brand: TerminalName::Iterm2,
        env_brand: TerminalName::Iterm2,
        multiplexer: MultiplexerKind::Tmux,
        ..Default::default()
    };
    let snapshot = crate::diagnostics::probes::collect_standalone_from(
        &terminal,
        tmux_facts(
            TmuxProbeResult::Available("off".to_owned()),
            TmuxProbeResult::Available(false),
        ),
        WaylandProbeFacts {
            is_wayland: false,
            data_control: TmuxProbeResult::Unavailable,
            wl_copy_available: false,
        },
        "pbcopy",
        TMUX_ROUTE.clone(),
        true,
        HostOs::Macos,
        DisplayServer::Unknown,
        false,
        RuntimeEvidence::Available(ColorLevel::TrueColor),
    );
    let report = collect_report_with(snapshot);

    assert_eq!(report.issue_count(), 1);
    assert!(
        report
            .findings
            .iter()
            .all(|finding| { finding.id != DiagnosticId::new("terminal", "control-mode") })
    );
    assert_eq!(
        report.findings[0].id,
        DiagnosticId::new("terminal", "tmux-clipboard")
    );
}

#[test]
fn standalone_wayland_missing_is_issue_but_no_seats_or_errors_are_not() {
    let terminal = TerminalContext::default();
    for data_control in [
        TmuxProbeResult::Available(false),
        TmuxProbeResult::Unavailable,
        TmuxProbeResult::Error("probe worker died".to_owned()),
    ] {
        let snapshot = crate::diagnostics::probes::collect_standalone_from(
            &terminal,
            unavailable_tmux_facts(),
            WaylandProbeFacts {
                is_wayland: true,
                data_control,
                wl_copy_available: false,
            },
            "arboard",
            LOCAL_ROUTE.clone(),
            false,
            HostOs::Macos,
            DisplayServer::Wayland,
            false,
            RuntimeEvidence::Available(ColorLevel::TrueColor),
        );
        let report = collect_report_with(snapshot);
        let has_issue = report
            .findings
            .iter()
            .any(|finding| finding.id == DiagnosticId::new("terminal", "wayland-data-control"));
        match report.facts.clipboard.data_control {
            DataControlFact::Missing => assert!(has_issue),
            DataControlFact::Unavailable => {
                assert!(!has_issue);
                assert_eq!(
                    report
                        .probe_notes
                        .iter()
                        .find(|note| note.probe == "wayland.data-control")
                        .and_then(|note| note.message.as_deref()),
                    None
                );
            }
            DataControlFact::Error => {
                assert!(!has_issue);
                assert_eq!(
                    report
                        .probe_notes
                        .iter()
                        .find(|note| note.probe == "wayland.data-control")
                        .and_then(|note| note.message.as_deref()),
                    Some("probe worker died")
                );
            }
            other => panic!("unexpected data-control fact: {other:?}"),
        }
    }
}

#[test]
fn human_wayland_error_includes_detail_once() {
    let mut report = healthy_report();
    report.facts.clipboard.native_preflight = NativeClipboardPreflight::Unavailable;
    report.facts.clipboard.display_server = DisplayServer::Wayland;
    report.facts.clipboard.data_control = DataControlFact::Error;
    report.facts.clipboard.delivery = ClipboardDelivery::Failed;
    report.facts.clipboard.fix = Some("/minimal".to_owned());
    report.findings.push(DiagnosticFinding {
        id: crate::diagnostics::CLIPBOARD_DELIVERY_UNAVAILABLE_ID,
        disposition: FindingDisposition::Issue,
        message: "No configured clipboard route can reach the intended clipboard".to_owned(),
        remediation: None,
        automatic_remediation: None,
        note: Some(
            "Each in-app copy is also written to the backup path shown by the operation. Use \
             `/copy <file>` for an explicit file or `/minimal` for terminal-native selection, \
             then check the native clipboard tool reported above."
                .to_owned(),
        ),
    });
    report.probe_notes = vec![ProbeNote {
        probe: "wayland.data-control",
        status: ProbeStatus::Error,
        message: Some("probe worker died".to_owned()),
    }];
    assert_eq!(
        human::format(&report),
        concat!(
            "Grok Doctor\n",
            "\n",
            "Environment\n",
            "  · terminal                     Ghostty\n",
            "  ? terminal version             no reply\n",
            "  · multiplexer                  None detected\n",
            "  · ssh                          no\n",
            "  · color                        truecolor\n",
            "  · themes                       all\n",
            "\n",
            "Clipboard\n",
            "  · native                       unavailable\n",
            "  · tmux                         off\n",
            "  · osc 52                       off\n",
            "  · SSH wrap                     off\n",
            "  ? data-control                 error: probe worker died\n",
            "  · status                       unavailable\n",
            "\n",
            "Findings\n",
            "  ! clipboard.delivery-unavailable No configured clipboard route can reach the intended clipboard\n",
            "      Each in-app copy is also written to the backup path shown by the operation. Use `/copy <file>` for an explicit file or `/minimal` for terminal-native selection, then check the native clipboard tool reported above.\n",
            "\n",
            "1 issue, 0 recommendations\n",
        )
    );
}

#[test]
fn standalone_runtime_and_tmux_are_unavailable_without_false_wezterm_finding() {
    let terminal = TerminalContext {
        brand: TerminalName::WezTerm,
        env_brand: TerminalName::WezTerm,
        multiplexer: MultiplexerKind::Tmux,
        ..Default::default()
    };
    let snapshot = crate::diagnostics::probes::collect_standalone_from(
        &terminal,
        unavailable_tmux_facts(),
        WaylandProbeFacts {
            is_wayland: false,
            data_control: TmuxProbeResult::Unavailable,
            wl_copy_available: false,
        },
        "pbcopy",
        LOCAL_ROUTE.clone(),
        true,
        HostOs::Macos,
        DisplayServer::Unknown,
        false,
        RuntimeEvidence::Available(ColorLevel::TrueColor),
    );
    let report = collect_report_with(snapshot);

    assert!(report.findings.iter().all(|finding| {
        finding.id != DiagnosticId::new("terminal", "wezterm-kitty")
            && finding.id != DiagnosticId::new("terminal", "control-mode")
    }));
    assert_eq!(report.facts.xtversion, RuntimeFact::Unavailable);
    assert_eq!(
        report
            .probe_notes
            .iter()
            .filter(|note| note.probe.starts_with("tmux."))
            .map(|note| note.probe)
            .collect::<Vec<_>>(),
        [
            "tmux.version",
            "tmux.extended-keys",
            "tmux.set-clipboard",
            "tmux.allow-passthrough-support",
            "tmux.control-mode",
            "tmux.client-features",
        ]
    );
    let runtime_notes = report
        .probe_notes
        .iter()
        .filter(|note| note.probe.starts_with("runtime."))
        .collect::<Vec<_>>();
    assert_eq!(
        runtime_notes
            .iter()
            .map(|note| (note.probe, note.status))
            .collect::<Vec<_>>(),
        [
            ("runtime.fullscreen-active", ProbeStatus::Unavailable),
            ("runtime.kitty-flags-pushed", ProbeStatus::Unavailable),
            ("runtime.xtversion", ProbeStatus::Unavailable),
        ]
    );
    assert!(
        runtime_notes
            .iter()
            .all(|note| crate::diagnostics::probe_requires_live_tui(note))
    );
    assert!(
        report
            .probe_notes
            .iter()
            .filter(|note| note.probe.starts_with("tmux."))
            .all(|note| !crate::diagnostics::probe_requires_live_tui(note))
    );
}

#[test]
fn human_healthy_fixture_is_exact() {
    assert_eq!(
        human::format(&healthy_report()),
        concat!(
            "Grok Doctor\n",
            "\n",
            "Environment\n",
            "  · terminal                     Ghostty\n",
            "  ? terminal version             no reply\n",
            "  · multiplexer                  None detected\n",
            "  · ssh                          no\n",
            "  · color                        truecolor\n",
            "  · themes                       all\n",
            "\n",
            "Clipboard\n",
            "  · native                       local (pbcopy)\n",
            "  · tmux                         off\n",
            "  · osc 52                       off\n",
            "  · SSH wrap                     off\n",
            "  · status                       confirmed\n",
            "\n",
            "0 issues, 0 recommendations\n",
        )
    );
}

#[test]
fn human_incomplete_fixture_is_exact_without_duplicate_probe_rows() {
    let mut report = healthy_report();
    report.facts.xtversion = RuntimeFact::Unavailable;
    report.facts.color.level = RuntimeFact::Unavailable;
    report.facts.color.available_themes.clear();
    report.facts.clipboard.data_control = DataControlFact::Unavailable;
    report.probe_notes = vec![
        ProbeNote {
            probe: "runtime.xtversion",
            status: ProbeStatus::Unavailable,
            message: None,
        },
        ProbeNote {
            probe: "terminal.color",
            status: ProbeStatus::Unavailable,
            message: None,
        },
        ProbeNote {
            probe: "wayland.data-control",
            status: ProbeStatus::Unavailable,
            message: None,
        },
    ];
    assert_eq!(
        human::format(&report),
        concat!(
            "Grok Doctor\n",
            "\n",
            "Environment\n",
            "  · terminal                     Ghostty\n",
            "  ? terminal version             unavailable\n",
            "  · multiplexer                  None detected\n",
            "  · ssh                          no\n",
            "  ? color                        unavailable\n",
            "  ? themes                       unavailable\n",
            "\n",
            "Clipboard\n",
            "  · native                       local (pbcopy)\n",
            "  · tmux                         off\n",
            "  · osc 52                       off\n",
            "  · SSH wrap                     off\n",
            "  · status                       confirmed\n",
            "\n",
            "Needs a running session\n",
            "  Some checks only run in Grok. Start Grok and run /doctor.\n",
            "\n",
            "0 issues, 0 recommendations\n",
        )
    );
}

#[test]
fn json_empty_fixture_pins_null_policy() {
    let mut report = healthy_report();
    report.facts.xtversion = RuntimeFact::Unavailable;
    report.facts.color.level = RuntimeFact::Unavailable;
    report.facts.color.available_themes.clear();
    report.facts.clipboard.data_control = DataControlFact::Unavailable;
    let mut output = Vec::new();
    write_report(&report, true, &mut output).unwrap();
    let json: serde_json::Value = serde_json::from_slice(&output).unwrap();
    assert_eq!(
        json,
        serde_json::json!({
            "schemaVersion": "1",
            "facts": {
                "terminal": {
                    "name": "ghostty",
                    "xtversion": {"status": "unavailable", "value": null}
                },
                "multiplexer": {"kind": "undetected", "byobu": null},
                "ssh": false,
                "color": {
                    "level": {"status": "unavailable", "value": null},
                    "availableThemes": [],
                    "totalThemes": 5
                },
                "keyboard": null,
                "newline": null,
                "clipboard": {
                    "nativeRoute": true,
                    "nativeTool": "pbcopy",
                    "nativePreflight": "local_available",
                    "tmuxRoute": false,
                    "osc52Route": false,
                    "osc52Capability": "supported",
                    "wrapSink": false,
                    "displayServer": "unknown",
                    "containerNoDisplay": false,
                    "dataControl": "unavailable",
                    "delivery": "confirmed",
                    "fix": null
                }
            },
            "findings": [],
            "probeNotes": [],
            "counts": {"issues": 0, "recommendations": 0, "probeNotes": 0}
        })
    );
}

#[test]
fn stable_mapping_tables_are_complete() {
    use super::json::{
        byobu_backend, clipboard_delivery, data_control, display_server, host_os, modifier_fate,
        multiplexer, native_preflight, osc52_capability, terminal_name,
    };

    assert_eq!(
        [
            TerminalName::AppleTerminal,
            TerminalName::Ghostty,
            TerminalName::Iterm2,
            TerminalName::WarpTerminal,
            TerminalName::VsCode,
            TerminalName::Cursor,
            TerminalName::Windsurf,
            TerminalName::Zed,
            TerminalName::WezTerm,
            TerminalName::Kitty,
            TerminalName::Alacritty,
            TerminalName::Rio,
            TerminalName::Foot,
            TerminalName::JetBrains,
            TerminalName::GrokDesktop,
            TerminalName::Vte,
            TerminalName::Terminator,
            TerminalName::WindowsTerminal,
            TerminalName::Otty,
            TerminalName::Unknown,
        ]
        .map(terminal_name),
        [
            "apple_terminal",
            "ghostty",
            "iterm2",
            "warp",
            "vs_code",
            "cursor",
            "windsurf",
            "zed",
            "wezterm",
            "kitty",
            "alacritty",
            "rio",
            "foot",
            "jetbrains",
            "grok_desktop",
            "vte",
            "terminator",
            "windows_terminal",
            "otty",
            "unknown",
        ]
    );
    assert_eq!(
        [
            MultiplexerKind::Tmux,
            MultiplexerKind::Screen,
            MultiplexerKind::Zellij,
            MultiplexerKind::Cmux,
            MultiplexerKind::Herdr,
            MultiplexerKind::Undetected,
        ]
        .map(multiplexer),
        ["tmux", "screen", "zellij", "cmux", "herdr", "undetected"]
    );
    assert_eq!(
        [
            ByobuBackend::Unknown,
            ByobuBackend::Tmux,
            ByobuBackend::Screen
        ]
        .map(byobu_backend),
        ["unknown", "tmux", "screen"]
    );
    assert_eq!(
        [
            DataControlFact::Available,
            DataControlFact::Missing,
            DataControlFact::Unavailable,
            DataControlFact::Error,
            DataControlFact::NotApplicable,
        ]
        .map(data_control),
        [
            "available",
            "missing",
            "unavailable",
            "error",
            "not_applicable"
        ]
    );
    assert_eq!(modifier_fate(ModifierFate::Native), "native");
    assert_eq!(modifier_fate(ModifierFate::Dropped), "dropped");
    assert_eq!(modifier_fate(ModifierFate::Unrecoverable), "unrecoverable");
    assert_eq!(modifier_fate(ModifierFate::Unknown), "unknown");
    assert_eq!(host_os(HostOs::Macos), "macos");
    assert_eq!(host_os(HostOs::Linux), "linux");
    assert_eq!(host_os(HostOs::Windows), "windows");
    assert_eq!(host_os(HostOs::Other), "other");
    assert_eq!(
        [
            NativeClipboardPreflight::Disabled,
            NativeClipboardPreflight::LocalAvailable,
            NativeClipboardPreflight::RemoteOnly,
            NativeClipboardPreflight::Unavailable,
        ]
        .map(native_preflight),
        ["disabled", "local_available", "remote_only", "unavailable"]
    );
    assert_eq!(
        [
            Osc52Capability::Supported,
            Osc52Capability::Unsupported,
            Osc52Capability::Unknown,
        ]
        .map(osc52_capability),
        ["supported", "unsupported", "unknown"]
    );
    assert_eq!(
        [
            ClipboardDelivery::Confirmed,
            ClipboardDelivery::Unverified,
            ClipboardDelivery::Failed,
        ]
        .map(clipboard_delivery),
        ["confirmed", "unverified", "failed"]
    );
    assert_eq!(
        [
            DisplayServer::Quartz,
            DisplayServer::Wayland,
            DisplayServer::X11,
            DisplayServer::Win32,
            DisplayServer::Unknown,
        ]
        .map(display_server),
        ["quartz", "wayland", "x11", "win32", "unknown"]
    );
}

#[test]
fn newline_variant_and_field_mappings_are_stable() {
    for (fact, kind, field, value) in [
        (
            NewlineFact::Vte {
                version: Some("8200".to_owned()),
            },
            "vte",
            "version",
            "8200",
        ),
        (
            NewlineFact::XtermJs {
                terminal: TerminalName::Cursor,
            },
            "xterm_js",
            "terminalName",
            "cursor",
        ),
    ] {
        let mut report = healthy_report();
        report.facts.newline = Some(fact);
        let mut output = Vec::new();
        write_report(&report, true, &mut output).unwrap();
        let json: serde_json::Value = serde_json::from_slice(&output).unwrap();
        assert_eq!(json["facts"]["newline"]["kind"], kind);
        assert_eq!(json["facts"]["newline"][field], value);
    }
    let mut report = healthy_report();
    report.facts.newline = Some(NewlineFact::NoKittyKeyboardProtocol);
    let mut output = Vec::new();
    write_report(&report, true, &mut output).unwrap();
    let json: serde_json::Value = serde_json::from_slice(&output).unwrap();
    assert_eq!(
        json["facts"]["newline"],
        serde_json::json!({"kind": "no_kitty_keyboard_protocol"})
    );
}

#[test]
fn clipboard_issue_count_preserves_legacy_reports_without_double_counting_named_findings() {
    let mut report = healthy_report();
    report.facts.clipboard.delivery = ClipboardDelivery::Failed;
    assert_eq!(report.issue_count(), 1, "legacy fact-only report");
    report.findings.push(DiagnosticFinding {
        id: crate::diagnostics::CLIPBOARD_DELIVERY_UNAVAILABLE_ID,
        disposition: FindingDisposition::Issue,
        message: "clipboard unavailable".to_owned(),
        remediation: None,
        automatic_remediation: None,
        note: Some("manual recovery".to_owned()),
    });
    assert_eq!(report.issue_count(), 1, "named finding replaces fact count");
}

#[test]
fn new_named_findings_extend_json_without_schema_changes() {
    let mut report = healthy_report();
    report.facts.clipboard.delivery = ClipboardDelivery::Unverified;
    report.facts.clipboard.fix = Some("grok wrap <ssh command> or /minimal".to_owned());
    report.findings.push(DiagnosticFinding {
        id: crate::diagnostics::CLIPBOARD_DELIVERY_UNVERIFIED_ID,
        disposition: FindingDisposition::Issue,
        message: "Clipboard delivery could not be verified across this remote boundary".to_owned(),
        remediation: None,
        automatic_remediation: None,
        note: Some("Run /doctor guidance".to_owned()),
    });

    let mut output = Vec::new();
    write_report(&report, true, &mut output).unwrap();
    let json: serde_json::Value = serde_json::from_slice(&output).unwrap();
    assert_eq!(json["schemaVersion"], "1");
    assert_eq!(json["facts"]["clipboard"]["delivery"], "unverified");
    assert_eq!(
        json["facts"]["clipboard"]["fix"],
        "grok wrap <ssh command> or /minimal"
    );
    assert_eq!(json["findings"][0]["id"], "clipboard.delivery-unverified");
    assert_eq!(json["counts"]["issues"], 1);
}

#[test]
fn output_writer_errors_propagate() {
    struct BrokenWriter;

    impl std::io::Write for BrokenWriter {
        fn write(&mut self, _buf: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("closed"))
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    assert!(write_report(&healthy_report(), false, &mut BrokenWriter).is_err());
    assert!(write_report(&healthy_report(), true, &mut BrokenWriter).is_err());
}
