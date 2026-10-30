//! E2E: the Landlock layer itself limits file access (Linux only).
//!
//! `deny_paths_e2e` drives bwrap bind-overs for path deny; a Landlock `apply`
//! that did nothing would not fail it. This file probes the grants of the
//! built-in `workspace`, `read-only` and `strict` profiles directly: each case
//! applies the profile in a subprocess (apply is irreversible), then tries
//! reads and writes inside and outside the grants, from the process and from a
//! child process that inherits the ruleset. The parent runs the same
//! operations without a sandbox first, so a denial can only come from Landlock.
//!
//! Soft-skips when the kernel cannot enforce Landlock;
//! `SANDBOX_E2E_REQUIRE_ENFORCEMENT=1` turns the skip into a failure.
//!
//! ```text
//! SANDBOX_E2E_REQUIRE_ENFORCEMENT=1 cargo nextest run -p pi-sandbox -E 'binary(landlock_enforcement_e2e)'
//! ```

#![cfg(all(target_os = "linux", feature = "enforce"))]

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

const REQUIRE_ENV: &str = "SANDBOX_E2E_REQUIRE_ENFORCEMENT";
const ROOT_ENV: &str = "LANDLOCK_E2E_ROOT";
const PROFILE_ENV: &str = "LANDLOCK_E2E_PROFILE";
const SECRET: &str = "landlock-e2e-secret-4e1b7c";

const SYS_LANDLOCK_CREATE_RULESET: libc::c_long = 444;
const LANDLOCK_CREATE_RULESET_VERSION: libc::c_ulong = 1;

/// Removes the fixture tree on both pass and panic.
struct CleanupGuard(PathBuf);

impl Drop for CleanupGuard {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
        if let Some(parent) = self.0.parent() {
            // Fails, and so keeps the directory, while another run still uses it.
            let _ = fs::remove_dir(parent);
        }
    }
}

/// Landlock ABI version the running kernel reports, or the errno of the probe.
fn kernel_landlock_abi() -> Result<i64, i32> {
    // SAFETY: the version query takes a null attribute pointer and size 0.
    let ret = unsafe {
        libc::syscall(
            SYS_LANDLOCK_CREATE_RULESET,
            std::ptr::null::<libc::c_void>(),
            0 as libc::size_t,
            LANDLOCK_CREATE_RULESET_VERSION,
        )
    };
    if ret < 0 {
        Err(io::Error::last_os_error().raw_os_error().unwrap_or(0))
    } else {
        Ok(ret as i64)
    }
}

fn skip_if_landlock_unavailable() -> bool {
    let require = std::env::var(REQUIRE_ENV).is_ok();
    let support = pi_sandbox::SandboxManager::support_info();
    let abi = kernel_landlock_abi();
    if support.is_supported && abi.is_ok() {
        return false;
    }
    let why = format!(
        "Landlock unavailable (nono: supported={} '{}'; landlock_create_ruleset ABI probe: {abi:?})",
        support.is_supported, support.details
    );
    assert!(!require, "enforcement required ({REQUIRE_ENV}): {why}");
    eprintln!("skipping: {why}");
    true
}

fn fixture_root() -> PathBuf {
    // Not under /tmp, TMPDIR or the workspace: every profile grants those, which
    // would hide the "outside all grants" probes.
    dirs::home_dir()
        .expect("home dir required: the fixture lives under it")
        .join(".cache")
        .join("pi-landlock-e2e")
        .join(format!(
            "run-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock before epoch")
                .as_nanos()
        ))
}

/// Every operation the subprocess will try, run here without a sandbox.
/// All of them must succeed, or a later denial would prove nothing.
fn unsandboxed_control(root: &Path) {
    let outside = root.join("outside");
    fs::write(outside.join("control-new"), b"x").expect("control: create outside");
    fs::write(outside.join("secret.txt"), SECRET).expect("control: overwrite outside");
    fs::create_dir(outside.join("control-dir")).expect("control: mkdir outside");
    fs::rename(outside.join("control-dir"), outside.join("control-dir2"))
        .expect("control: rename outside");
    fs::remove_dir(outside.join("control-dir2")).expect("control: rmdir outside");
    fs::remove_file(outside.join("control-new")).expect("control: unlink outside");
    let status = Command::new("sh")
        .arg("-c")
        .arg(format!(
            "echo x > '{}'",
            outside.join("control-child").display()
        ))
        .status()
        .expect("control: spawn sh");
    assert!(status.success(), "control: child write outside failed");
    fs::remove_file(outside.join("control-child")).expect("control: unlink child file");
}

fn run_profile(profile: &str) {
    if skip_if_landlock_unavailable() {
        return;
    }
    let root = fixture_root();
    let _cleanup = CleanupGuard(root.clone());
    for dir in ["ws", "home", "grok-home", "outside"] {
        fs::create_dir_all(root.join(dir)).expect("create fixture dir");
    }
    fs::write(root.join("ws").join("readable.txt"), SECRET).expect("write ws file");
    fs::write(root.join("outside").join("secret.txt"), SECRET).expect("write outside file");
    unsandboxed_control(&root);

    let exe = std::env::current_exe().expect("current_exe");
    let output = Command::new(exe)
        .env(ROOT_ENV, root.as_os_str())
        .env(PROFILE_ENV, profile)
        .env("HOME", root.join("home"))
        .env("GROK_HOME", root.join("grok-home"))
        .env("PI_HOME", root.join("grok-home"))
        .arg("--ignored")
        .arg("--exact")
        .arg("--nocapture")
        .arg("subprocess_entry")
        .output()
        .expect("spawn subprocess");
    let stderr = String::from_utf8_lossy(&output.stderr);
    eprintln!(
        "[{profile}] subprocess stdout:\n{}\n[{profile}] subprocess stderr:\n{stderr}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(
        output.status.success(),
        "[{profile}] subprocess failed: {:?}",
        output.status
    );
    assert!(
        stderr.contains(&format!("LANDLOCK-E2E-OK {profile}")),
        "[{profile}] subprocess did not reach its final marker (skipped probes?)"
    );
}

#[test]
fn kernel_abi_probe_agrees_with_support_info() {
    let abi = kernel_landlock_abi();
    let support = pi_sandbox::SandboxManager::support_info();
    eprintln!(
        "landlock ABI probe: {abi:?}; nono: supported={} details={}",
        support.is_supported, support.details
    );
    if std::env::var(REQUIRE_ENV).is_ok() {
        assert!(abi.is_ok(), "landlock_create_ruleset unavailable: {abi:?}");
    }
    assert_eq!(
        abi.is_ok(),
        support.is_supported,
        "nono support_info disagrees with the kernel: ABI probe {abi:?}, support {support:?}"
    );
}

#[test]
fn workspace_profile_writes_only_inside_grants() {
    run_profile("workspace");
}

#[test]
fn read_only_profile_blocks_workspace_writes() {
    run_profile("read-only");
}

#[test]
fn strict_profile_blocks_reads_outside_allowlist() {
    run_profile("strict");
}

struct Probe {
    failures: Vec<String>,
}

impl Probe {
    fn ok(&mut self, label: &str, result: io::Result<()>) {
        match result {
            Ok(()) => eprintln!("OK   {label}"),
            Err(e) => {
                eprintln!("FAIL {label}: expected success, got {e}");
                self.failures.push(label.to_string());
            }
        }
    }

    /// Landlock refuses with EACCES; EXDEV is what a refused cross-directory
    /// rename (no REFER right) may report, so it counts only for `allow_exdev`.
    fn denied(&mut self, label: &str, result: io::Result<()>, allow_exdev: bool) {
        match result {
            Ok(()) => {
                eprintln!("FAIL {label}: expected EACCES, but the operation succeeded");
                self.failures.push(label.to_string());
            }
            Err(e)
                if e.raw_os_error() == Some(libc::EACCES)
                    || (allow_exdev && e.raw_os_error() == Some(libc::EXDEV)) =>
            {
                eprintln!("OK   {label}: denied ({e})");
            }
            Err(e) => {
                eprintln!("FAIL {label}: expected EACCES, got {e}");
                self.failures.push(label.to_string());
            }
        }
    }

    fn check(&mut self, label: &str, cond: bool, detail: &str) {
        if cond {
            eprintln!("OK   {label}");
        } else {
            eprintln!("FAIL {label}: {detail}");
            self.failures.push(label.to_string());
        }
    }
}

fn read_ok(path: &Path) -> io::Result<()> {
    let text = fs::read_to_string(path)?;
    if text.contains(SECRET) {
        Ok(())
    } else {
        Err(io::Error::other("file readable but marker missing"))
    }
}

fn child_write(path: &Path) -> io::Result<()> {
    let status = Command::new("sh")
        .arg("-c")
        .arg(format!("echo x > '{}'", path.display()))
        .stderr(std::process::Stdio::null())
        .status()?;
    if status.success() {
        Ok(())
    } else {
        Err(io::Error::from_raw_os_error(libc::EACCES))
    }
}

fn child_cat(path: &Path) -> io::Result<()> {
    let output = Command::new("cat").arg(path).output()?;
    if output.status.success() && String::from_utf8_lossy(&output.stdout).contains(SECRET) {
        Ok(())
    } else {
        Err(io::Error::from_raw_os_error(libc::EACCES))
    }
}

/// Applies the sandbox (irreversible), so this runs only as a subprocess of
/// the profile tests above.
#[test]
#[ignore]
fn subprocess_entry() {
    let Ok(root) = std::env::var(ROOT_ENV) else {
        return;
    };
    let root = PathBuf::from(root);
    let profile_name = std::env::var(PROFILE_ENV).expect(PROFILE_ENV);
    let ws = root.join("ws");
    let outside = root.join("outside");
    let grok_home = root.join("grok-home");
    let tmp_file = PathBuf::from(format!("/tmp/.pi-landlock-e2e-{}", std::process::id()));

    let profile: pi_sandbox::ProfileName = profile_name.parse().expect("profile name");
    let mut mgr = pi_sandbox::SandboxManager::new(profile, &ws);
    if let Err(e) = mgr.apply(&ws) {
        eprintln!("FAIL: apply errored: {e}");
        std::process::exit(3);
    }
    if !mgr.is_applied() || mgr.not_enforced().is_some() {
        eprintln!(
            "FAIL: profile not enforced (is_applied={}, not_enforced={:?})",
            mgr.is_applied(),
            mgr.not_enforced()
        );
        std::process::exit(4);
    }

    let mut p = Probe { failures: vec![] };
    // Landlock and no_new_privs are per thread, and libtest runs this on a
    // thread that is not the main one, so `/proc/self/status` would show the
    // main thread.
    let status = fs::read_to_string("/proc/thread-self/status").unwrap_or_default();
    p.check(
        "no_new_privs is set",
        status
            .lines()
            .any(|l| l.starts_with("NoNewPrivs:") && l.trim_end().ends_with('1')),
        "NoNewPrivs is not 1 in /proc/thread-self/status",
    );

    let reads_outside = profile_name != "strict";
    let writes_ws = profile_name != "read-only";

    p.ok("read in workspace", read_ok(&ws.join("readable.txt")));
    if reads_outside {
        p.ok("read outside grants", read_ok(&outside.join("secret.txt")));
        p.ok(
            "child reads outside grants",
            child_cat(&outside.join("secret.txt")),
        );
    } else {
        p.denied(
            "read outside grants",
            read_ok(&outside.join("secret.txt")),
            false,
        );
        p.denied(
            "list outside grants",
            fs::read_dir(&outside).map(|_| ()),
            false,
        );
        p.denied(
            "child reads outside grants",
            child_cat(&outside.join("secret.txt")),
            false,
        );
    }

    if writes_ws {
        p.ok("write in workspace", fs::write(ws.join("new.txt"), b"x"));
        p.ok(
            "child writes in workspace",
            child_write(&ws.join("child.txt")),
        );
        p.ok("mkdir in workspace", fs::create_dir(ws.join("dir")));
        p.ok(
            "rename in workspace",
            fs::rename(ws.join("new.txt"), ws.join("dir").join("moved.txt")),
        );
        p.ok(
            "unlink in workspace",
            fs::remove_file(ws.join("dir").join("moved.txt")),
        );
    } else {
        p.denied(
            "write in workspace",
            fs::write(ws.join("new.txt"), b"x"),
            false,
        );
        p.denied(
            "overwrite in workspace",
            fs::write(ws.join("readable.txt"), b"x"),
            false,
        );
        p.denied(
            "child writes in workspace",
            child_write(&ws.join("child.txt")),
            false,
        );
        p.denied("mkdir in workspace", fs::create_dir(ws.join("dir")), false);
        p.denied(
            "unlink in workspace",
            fs::remove_file(ws.join("readable.txt")),
            false,
        );
    }

    p.ok(
        "write in grok home",
        fs::write(grok_home.join("state.txt"), b"x"),
    );
    p.ok("write in /tmp", fs::write(&tmp_file, b"x"));
    let _ = fs::remove_file(&tmp_file);

    p.denied(
        "create file outside grants",
        fs::write(outside.join("new.txt"), b"x"),
        false,
    );
    p.denied(
        "overwrite file outside grants",
        fs::write(outside.join("secret.txt"), b"x"),
        false,
    );
    p.denied(
        "truncate file outside grants",
        fs::OpenOptions::new()
            .write(true)
            .truncate(true)
            .open(outside.join("secret.txt"))
            .map(|_| ()),
        false,
    );
    p.denied(
        "mkdir outside grants",
        fs::create_dir(outside.join("dir")),
        false,
    );
    p.denied(
        "unlink outside grants",
        fs::remove_file(outside.join("secret.txt")),
        false,
    );
    p.denied(
        "rename out of ungranted dir",
        fs::rename(outside.join("secret.txt"), ws.join("stolen.txt")),
        true,
    );
    p.denied(
        "symlink outside grants",
        std::os::unix::fs::symlink("/etc/passwd", outside.join("link")),
        false,
    );
    p.denied(
        "write to HOME",
        fs::write(root.join("home").join("marker"), b"x"),
        false,
    );
    p.denied(
        "child writes outside grants",
        child_write(&outside.join("child.txt")),
        false,
    );

    if !p.failures.is_empty() {
        eprintln!("FAILED probes: {:?}", p.failures);
        std::process::exit(1);
    }
    eprintln!("LANDLOCK-E2E-OK {profile_name}");
    std::process::exit(0);
}
