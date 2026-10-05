use super::*;

#[test]
fn canonical_and_short_ids_resolve_to_canonical_id() {
    assert_eq!(resolve_fix_id("terminal.ssh-wrap").unwrap(), SSH_WRAP_ID);
    let command = human_fix_command(SSH_WRAP_ID).expect("SSH fix command");
    assert_eq!(command, "grok doctor fix ssh-wrap");
    assert_eq!(
        resolve_fix_id(command.strip_prefix("grok doctor fix ").unwrap()).unwrap(),
        SSH_WRAP_ID
    );
    assert!(human_fix_command(DiagnosticId::new("terminal", "unknown")).is_none());
    assert!(matches!(
        resolve_fix_id("terminal.unknown"),
        Err(FixError::UnknownId(_))
    ));
}

#[test]
fn safe_absolute_directory_rejects_hostile_home_and_byobu_values() {
    for value in [
        ".",
        "..",
        "/",
        "relative",
        "/tmp/../escape",
        "/tmp/bad\nname",
        "~/x",
    ] {
        assert!(
            matches!(
                SafeAbsoluteDirectory::parse(PathBuf::from(value), "HOME"),
                Err(FixError::UnsafeDirectory { .. })
            ),
            "{value:?}"
        );
    }
}

#[test]
fn reload_instruction_shell_quotes_and_markdown_escapes_paths() {
    assert_eq!(
        reload_instruction(Path::new("/tmp/a b/q'v.conf")),
        "Reload tmux with `tmux source-file '/tmp/a b/q'\\''v.conf'`, or restart the tmux server."
    );
    assert_eq!(
        reload_instruction(Path::new("/tmp/a`b.conf")),
        "Reload tmux with ``tmux source-file '/tmp/a`b.conf'``, or restart the tmux server."
    );
    assert_eq!(
        shell_quote_path(Path::new("/tmp/a`b.conf")).unwrap(),
        "'/tmp/a`b.conf'"
    );
    assert_eq!(
        reload_instruction(Path::new("/tmp/bad\npath")),
        "Reload your tmux config, or restart the tmux server, to activate the persistent setting."
    );
    assert_eq!(markdown_code_path(Path::new("/tmp/a`b")), "``/tmp/a`b``");
}

#[test]
fn tmux_scanner_handles_server_scopes_separators_prefixes_and_native_blocks() {
    let path = Path::new("/tmp/tmux.conf");
    for spec in [&TMUX_CLIPBOARD_SPEC, &TMUX_EXTENDED_KEYS_SPEC] {
        let healthy = spec.healthy_values[0];
        for assignment in [
            format!("set {} {healthy}\n", spec.option),
            format!("set -s {} {healthy}\n", spec.option),
            format!("set-option -gq {} {healthy}\n", spec.option),
            format!("set -w {} {healthy}\n", spec.option),
            format!("FOO=bar set -g {} {healthy}\n", spec.option),
            format!("set -g mouse on; set -g {} {healthy}\n", spec.option),
        ] {
            assert_eq!(
                scan_direct_tmux_option(&assignment, path, spec).unwrap(),
                DirectOptionState::Healthy,
                "{assignment:?}"
            );
        }
        for conflict in [
            format!("set {} off\n", spec.option),
            format!("set -s {} off\n", spec.option),
            format!("set-option -g {} off\n", spec.option),
            format!("set -w {} off\n", spec.option),
            format!("set -g mouse on; set -g {} off\n", spec.option),
            format!("set -g {} o\\\nff\n", spec.option),
        ] {
            assert!(
                matches!(
                    scan_direct_tmux_option(&conflict, path, spec),
                    Err(FixError::ExistingCustomization { .. })
                ),
                "{conflict:?}"
            );
        }
    }

    let spec = &DCS_PASSTHROUGH_SPEC;
    for healthy in [
        "setw -g allow-passthrough on\n",
        "set-window-option -g allow-passthrough all\n",
        "set -wg allow-passthrough on\n",
    ] {
        assert_eq!(
            scan_direct_tmux_option(healthy, path, spec).unwrap(),
            DirectOptionState::Healthy,
            "{healthy:?}"
        );
    }
    for conflict in [
        "setw -g allow-passthrough off\n",
        "set-window-option -g allow-passthrough off\n",
        "set -wg allow-passthrough off\n",
    ] {
        assert!(
            matches!(
                scan_direct_tmux_option(conflict, path, spec),
                Err(FixError::ExistingCustomization { .. })
            ),
            "{conflict:?}"
        );
    }
    for local in [
        "set allow-passthrough on\n",
        "setw allow-passthrough on\n",
        "setw -t:1 allow-passthrough off\n",
    ] {
        assert_eq!(
            scan_direct_tmux_option(local, path, spec).unwrap(),
            DirectOptionState::Absent,
            "{local:?}"
        );
    }

    for spec in [
        &TMUX_CLIPBOARD_SPEC,
        &DCS_PASSTHROUGH_SPEC,
        &TMUX_EXTENDED_KEYS_SPEC,
    ] {
        for ignored in [
            format!("# set -g {} off\n", spec.option),
            format!("set -g @{} off\n", spec.option),
            format!("set -g {}-copy off\n", spec.option),
            format!("%if 1\nset -g {} off\n%endif\n", spec.option),
            format!("if-shell true {{ set -g {} off }}\n", spec.option),
        ] {
            assert_eq!(
                scan_direct_tmux_option(&ignored, path, spec).unwrap(),
                DirectOptionState::Absent,
                "{ignored:?}"
            );
        }
        for ambiguous in [
            format!("se -g {} off\n", spec.option),
            format!("set -g {} off extra\n", spec.option),
            format!("set -g {}\n", spec.option),
            format!("set -g {} 'unterminated\n", spec.option),
            format!("set -g {} \\\n", spec.option),
            format!("set -t target\nset -g {} off\n", spec.option),
        ] {
            assert!(
                matches!(
                    scan_direct_tmux_option(&ambiguous, path, spec),
                    Err(FixError::ExistingCustomization { .. })
                ),
                "{ambiguous:?}"
            );
        }
    }
}

#[test]
fn conflicting_direct_form_after_managed_block_fails_persistent_verification() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join(".tmux.conf");
    for conflict in [
        "set set-clipboard off",
        "set -s set-clipboard off",
        "set-option -g set-clipboard off",
        "set -g mouse on; set -g set-clipboard off",
        "se -g set-clipboard off",
    ] {
        std::fs::write(
            &path,
            format!(
                "# >>> grok doctor >>>\n# >>> terminal.tmux-clipboard >>>\nset -g set-clipboard on\n# <<< terminal.tmux-clipboard <<<\n# <<< grok doctor <<<\n{conflict}\n"
            ),
        )
        .unwrap();
        assert!(
            !tmux_option_configured(&path, &TMUX_CLIPBOARD_SPEC),
            "{conflict}"
        );
    }
}

#[cfg(windows)]
#[test]
fn windows_is_manual_only_before_shell_selection() {
    let temp = tempfile::tempdir().unwrap();
    let mut request = request(temp.path(), "C:\\Program Files\\Git\\bin\\bash.exe");
    request.shell = Some(PathBuf::from("bash"));
    assert!(matches!(
        plan_fix(request, &report(), &terminal()),
        Err(FixError::PlatformUnsupported)
    ));
}

#[test]
fn alias_and_fish_function_scanners_accept_shell_whitespace() {
    for declaration in [
        "alias  ssh='ssh -A'",
        "alias\tssh = 'ssh -A'",
        "alias \t ssh='ssh -A'",
    ] {
        assert!(
            detect_posix_ssh_customization(declaration).is_some(),
            "{declaration}"
        );
    }
    for declaration in [
        "alias  ssh 'ssh -A'",
        "alias\tssh='ssh -A'",
        "function  ssh",
        "function\tssh --description wrapped",
    ] {
        assert!(
            detect_fish_ssh_customization(declaration).is_some(),
            "{declaration}"
        );
    }
    for not_ssh in [
        "aliases ssh='ssh -A'",
        "alias ssh_wrap='ssh -A'",
        "alias sshuttle='ssh -A'",
    ] {
        assert!(
            detect_posix_ssh_customization(not_ssh).is_none(),
            "{not_ssh}"
        );
        assert!(
            detect_fish_ssh_customization(not_ssh).is_none(),
            "{not_ssh}"
        );
    }
}

#[test]
fn posix_function_scanner_requires_exact_ssh_name_boundary() {
    for declaration in [
        "function ssh { command ssh \"$@\"; }",
        "function ssh() { command ssh \"$@\"; }",
        "ssh() { command ssh \"$@\"; }",
        "ssh () { command ssh \"$@\"; }",
    ] {
        assert!(
            detect_posix_ssh_customization(declaration).is_some(),
            "{declaration}"
        );
    }
    for not_ssh in [
        "function ssh_wrap { :; }",
        "function sshuttle { :; }",
        "ssh_wrap() { :; }",
        "sshuttle () { :; }",
    ] {
        assert!(
            detect_posix_ssh_customization(not_ssh).is_none(),
            "{not_ssh}"
        );
    }
}

#[cfg(unix)]
#[test]
fn validator_prefers_custom_executable_shell_and_uses_path_for_basename_only() {
    use std::os::unix::fs::PermissionsExt as _;

    let temp = tempfile::tempdir().unwrap();
    let shadow = temp.path().join("shadow");
    let valid = temp.path().join("valid");
    std::fs::create_dir(&shadow).unwrap();
    std::fs::create_dir(&valid).unwrap();
    std::fs::write(shadow.join("bash"), "not executable").unwrap();
    let real = valid.join("bash");
    std::fs::write(&real, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&real, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(
        find_on_path_in("bash", [&shadow, &valid]),
        Some(real.clone())
    );

    let custom = temp.path().join("custom/bash");
    std::fs::create_dir_all(custom.parent().unwrap()).unwrap();
    std::fs::write(&custom, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&custom, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(resolve_validator_program(&custom), Some(custom.clone()));

    std::fs::set_permissions(&custom, std::fs::Permissions::from_mode(0o644)).unwrap();
    // A non-executable explicit SHELL path is not silently substituted with a
    // different same-basename shell from PATH.
    assert_eq!(resolve_validator_program(&custom), None);

    assert_eq!(
        find_on_path_in("bash", [&shadow, &valid]),
        Some(real),
        "basename-only shell names may resolve through PATH"
    );
}

#[test]
fn managed_alias_with_later_unmanaged_conflict_is_not_configured() {
    let cases = [
        (
            ShellKind::Bash,
            "# >>> grok doctor >>>\n# >>> terminal.ssh-wrap >>>\nalias ssh='grok wrap ssh'\n# <<< terminal.ssh-wrap <<<\n# <<< grok doctor <<<\nalias ssh='ssh -A'\n",
        ),
        (
            ShellKind::Fish,
            "# >>> grok doctor >>>\n# >>> terminal.ssh-wrap >>>\nalias ssh 'grok wrap ssh'\n# <<< terminal.ssh-wrap <<<\n# <<< grok doctor <<<\nfunction ssh\n  command ssh -A $argv\nend\n",
        ),
    ];
    for (shell, content) in cases {
        let temp = tempfile::tempdir().unwrap();
        let path = shell.config_path(temp.path());
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, content).unwrap();
        assert!(!managed_alias_configured(&path, shell));
    }
}

#[cfg(unix)]
#[test]
fn shell_aliases_expand_to_exact_argv_and_bypass_is_explicit() {
    let temp = tempfile::tempdir().unwrap();
    let capture = temp.path().join("capture");
    let grok = temp.path().join("grok");
    std::fs::write(
        &grok,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{}'\n",
            capture.display()
        ),
    )
    .unwrap();
    use std::os::unix::fs::PermissionsExt as _;
    std::fs::set_permissions(&grok, std::fs::Permissions::from_mode(0o755)).unwrap();

    if let Some(bash) = find_on_path("bash") {
        let rc = temp.path().join("bashrc");
        std::fs::write(&rc, "alias ssh='grok wrap ssh'\n").unwrap();
        let command = format!(
            "source '{}'; source '{}'; eval 'ssh -p 2222 host'",
            rc.display(),
            rc.display()
        );
        let mut shell = std::process::Command::new(bash);
        shell
            .args(["-ic", &command])
            .env(
                "PATH",
                format!(
                    "{}:{}",
                    temp.path().display(),
                    std::env::var("PATH").unwrap()
                ),
            )
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .envs(pi_tty_utils::pager_env());
        pi_tty_utils::detach_std_command(&mut shell);
        let status = shell.status().unwrap();
        assert!(status.success());
        assert_eq!(
            std::fs::read_to_string(&capture).unwrap(),
            "wrap\nssh\n-p\n2222\nhost\n"
        );
    }
    if let Some(zsh) = find_on_path("zsh") {
        let rc = temp.path().join("zshrc");
        std::fs::write(&rc, "alias ssh='grok wrap ssh'\n").unwrap();
        let command = format!(
            "source '{}'; source '{}'; eval 'ssh -p 2222 host'",
            rc.display(),
            rc.display()
        );
        let mut shell = std::process::Command::new(zsh);
        shell
            .args(["-dfc", &command])
            .env(
                "PATH",
                format!(
                    "{}:{}",
                    temp.path().display(),
                    std::env::var("PATH").unwrap()
                ),
            )
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .envs(pi_tty_utils::pager_env());
        pi_tty_utils::detach_std_command(&mut shell);
        let status = shell.status().unwrap();
        assert!(status.success());
        assert_eq!(
            std::fs::read_to_string(&capture).unwrap(),
            "wrap\nssh\n-p\n2222\nhost\n"
        );
    }

    let fake_bin = temp.path().join("fake-bin");
    std::fs::create_dir(&fake_bin).unwrap();
    let fake_ssh = fake_bin.join("ssh");
    std::fs::write(&fake_ssh, "#!/bin/sh\nprintf bypass > \"$CAPTURE\"\n").unwrap();
    std::fs::set_permissions(&fake_ssh, std::fs::Permissions::from_mode(0o755)).unwrap();
    let Some(bash) = find_on_path("bash") else {
        return;
    };
    let mut shell = std::process::Command::new(bash);
    shell
        .args(["-ic", "alias ssh='grok wrap ssh'; command ssh host"])
        .env("CAPTURE", &capture)
        .env(
            "PATH",
            format!(
                "{}:{}:{}",
                fake_bin.display(),
                temp.path().display(),
                std::env::var("PATH").unwrap()
            ),
        )
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .envs(pi_tty_utils::pager_env());
    pi_tty_utils::detach_std_command(&mut shell);
    let status = shell.status().unwrap();
    assert!(status.success());
    assert_eq!(std::fs::read_to_string(&capture).unwrap(), "bypass");

    if let Some(fish) = find_on_path("fish") {
        let fish_capture = temp.path().join("fish-capture");
        let fish_grok = temp.path().join("fish-grok");
        std::fs::write(
            &fish_grok,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{}'\n",
                fish_capture.display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&fish_grok, std::fs::Permissions::from_mode(0o755)).unwrap();
        let rc = temp.path().join("config.fish");
        std::fs::write(&rc, "alias ssh 'fish-grok wrap ssh'\n").unwrap();
        let command = format!(
            "source '{}'; source '{}'; ssh -p 2222 host; env | string match -rq '^ssh='; and exit 9; or exit 0",
            rc.display(),
            rc.display()
        );
        let mut shell = std::process::Command::new(fish);
        shell
            .args(["-c", &command])
            .env(
                "PATH",
                format!(
                    "{}:{}",
                    temp.path().display(),
                    std::env::var("PATH").unwrap()
                ),
            )
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .envs(pi_tty_utils::pager_env());
        pi_tty_utils::detach_std_command(&mut shell);
        assert!(shell.status().unwrap().success());
        assert_eq!(
            std::fs::read_to_string(fish_capture).unwrap(),
            "wrap\nssh\n-p\n2222\nhost\n"
        );
    } else {
        eprintln!("fish unavailable; fish runtime alias test skipped explicitly");
    }
}
