#![allow(
    unused_imports,
    unused_variables,
    unused_mut,
    unreachable_code,
    dead_code
)]
#[cfg(all(feature = "jemalloc", unix))]
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;
#[cfg(all(feature = "jemalloc", feature = "release-dist", unix))]
mod jemalloc_malloc_conf {
    /// jemalloc looks up `extern const char *malloc_conf` — a thin pointer,
    /// not a Rust `&[u8]` fat pointer.
    #[repr(transparent)]
    struct MallocConfPtr(*const u8);
    unsafe impl Sync for MallocConfPtr {}
    static CONF: [u8; 63] = *b"prof:true,prof_active:false,lg_prof_sample:19,prof_final:false\0";
    #[cfg(not(any(target_os = "macos", target_os = "ios")))]
    #[used]
    #[unsafe(export_name = "malloc_conf")]
    static MALLOC_CONF: MallocConfPtr = MallocConfPtr(CONF.as_ptr());
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    #[used]
    #[unsafe(export_name = "_rjem_malloc_conf")]
    static MALLOC_CONF: MallocConfPtr = MallocConfPtr(CONF.as_ptr());
}
use anyhow::Result;
use std::net::SocketAddr;
use std::num::NonZeroUsize;
use tokio_util::sync::CancellationToken;
use pi_pager::app::{Command, PagerArgs};
use pi_pager::client_identity::PAGER_CLIENT_VERSION;
use pi_shell::agent::app::{run_headless, run_leader, run_stdio_agent};
use pi_shell::agent::config::Config as AgentConfig;
use pi_shell::leader::{
    ClientCapabilities, ClientMode, ControlCommand, LeaderCapabilities, LeaderDescriptor,
    LeaderRegistration, LeaderTarget, leader_is_older_than,
};
use pi_shell::leader::{
    ControlPayload, LeaderClient, LeaderEnvUrls, connect_or_spawn, socket_path_for_ws_url,
};
use pi_telemetry::process_info::{
    Entrypoint, Interactivity, ProcessIdentity, ReleaseChannel, set_identity, set_release_channel,
};
fn process_identity(command: Option<&Command>, is_interactive: bool) -> Option<ProcessIdentity> {
    use pi_telemetry::process_info::LeaderMode::Standalone;
    let (entrypoint, interactivity) = match command {
        Some(Command::Doctor(_) | Command::Wrap(_) | Command::Export(_) | Command::DiskUsage(_))
        | Some(Command::Version { .. })
        | Some(Command::Completions { .. }) => (Entrypoint::Cli, Interactivity::Unattended),
        None if is_interactive => return None,
        None => (Entrypoint::Headless, Interactivity::Unattended),
    };
    Some(ProcessIdentity {
        entrypoint,
        leader: Standalone,
        interactivity,
    })
}
use std::env;
use pi_update::{UpdateConfig, auto_update, enforce_version_policy_or_exit};
/// Entrypoint tag for `grok -p`; keys the quiet stderr default in `init_tracing_simple`.
const HEADLESS_ENTRYPOINT: &str = "headless";
/// Initialize simple tracing for non-TUI agent modes.
fn init_tracing_simple(app_entrypoint: &'static str) {
    use tracing_subscriber::{EnvFilter, Layer as _, fmt, layer::SubscriberExt as _};
    use pi_telemetry::debug_log::RMCP_SSE_NOISE_TARGET;
    let default_filter = if app_entrypoint == HEADLESS_ENTRYPOINT {
        "off"
    } else {
        "error"
    };
    let env_filter = match EnvFilter::try_from_default_env() {
        Ok(filter) => filter.add_directive(
            format!("{RMCP_SSE_NOISE_TARGET}=error")
                .parse()
                .expect("static rmcp directive must parse"),
        ),
        Err(_) => EnvFilter::new(default_filter),
    };
    let fmt_layer = fmt::layer()
        .with_target(false)
        .with_ansi(true)
        .with_writer(std::io::stderr);
    let registry = tracing_subscriber::registry()
        .with(fmt_layer.with_filter(env_filter))
        .with(pi_telemetry::sampling_log::layer())
        .with(pi_telemetry::instrumentation::layer())
        .with(pi_telemetry::hooks_log::layer())
        .with(pi_telemetry::otel_layer::build_otel_layer(
            pi_telemetry::otel_layer::OtelClientInfo {
                client_name: "zypi",
                client_version: pi_version::VERSION,
                service_version: env!("VERSION_WITH_COMMIT"),
                app_entrypoint,
            },
            pi_shell::auth::credential_provider::build_default_otel_layer_config(),
        ));
    pi_telemetry::debug_log::install_firehose(registry, app_entrypoint);
    pi_telemetry::external::init(
        pi_shell::agent::config::resolve_external_otel_config(
            pi_telemetry::external::config::ExternalClientInfo {
                service_version: env!("VERSION_WITH_COMMIT").to_owned(),
                client_version: pi_version::VERSION.to_owned(),
                app_entrypoint: app_entrypoint.to_owned(),
            },
        ),
    );
}
/// Flush observability, then exit. Used by the agent/headless signal handler.
///
/// Does NOT write terminal escape codes — agent mode never enables TUI modes.
/// The TUI has its own signal handler (`app::signal_handler`) that does the
/// full crossterm teardown.
fn shutdown_and_flush_telemetry(exit_code: i32) -> ! {
    pi_telemetry::sentry::flush_on_shutdown();
    pi_telemetry::otel_layer::shutdown_otel();
    pi_telemetry::debug_log::flush();
    std::process::exit(exit_code);
}
/// Raise the per-process fd soft limit toward the hard limit.
///
/// Default soft limits (256 macOS, commonly 1024 Linux) are easily exceeded:
/// each session thread's runtime costs ~3 fds, and a wide parallel subagent
/// wave adds spawn-burst transients — a 1024 limit fails with EMFILE under a
/// ~100-session wave. Targets 65536 on Linux (hard limits typically >= 1M)
/// and 8192 on macOS (`kern.maxfilesperproc` is often ~10k). No known
/// in-tree `select(2)` users (Rust std/tokio use epoll/kqueue); residual
/// third-party `FD_SETSIZE` risk is accepted — the prior 8192 cap already
/// exceeded FD_SETSIZE.
///
/// Best-effort: never blocks startup (containers/cgroups may pin limits).
#[cfg(unix)]
fn raise_fd_limit() {
    #[cfg(target_os = "macos")]
    const TARGET: libc::rlim_t = 8192;
    #[cfg(not(target_os = "macos"))]
    const TARGET: libc::rlim_t = 65536;
    unsafe {
        let mut rlim = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        if libc::getrlimit(libc::RLIMIT_NOFILE, &mut rlim) != 0 {
            return;
        }
        let new_cur = rlim.rlim_max.min(TARGET);
        if new_cur <= rlim.rlim_cur {
            return;
        }
        let old = rlim.rlim_cur;
        rlim.rlim_cur = new_cur;
        if libc::setrlimit(libc::RLIMIT_NOFILE, &rlim) == 0 {
            tracing::trace!(old, new = new_cur, "raised RLIMIT_NOFILE");
        }
    }
}
#[cfg(not(unix))]
fn raise_fd_limit() {}
const RUNTIME_SHUTDOWN_GRACE: std::time::Duration = std::time::Duration::from_secs(2);
const GROK_WORKER_THREADS_ENV: &str = "GROK_WORKER_THREADS";
/// tokio defaults to one worker per logical CPU. On a host with hundreds of
/// CPUs that can exhaust a cgroup thread budget at startup and abort under
/// `panic = "abort"`. A terminal UI is I/O-bound, so cap at 8.
const DEFAULT_MAX_WORKER_THREADS: NonZeroUsize = NonZeroUsize::new(8).unwrap();
/// How `GROK_WORKER_THREADS` resolved.
#[derive(Debug, PartialEq, Eq)]
enum WorkerCount {
    Accepted(NonZeroUsize),
    Clamped {
        requested: i128,
        used: NonZeroUsize,
        cores: NonZeroUsize,
    },
    Ignored {
        value: String,
        used: NonZeroUsize,
    },
}
impl WorkerCount {
    fn used(&self) -> NonZeroUsize {
        match self {
            Self::Accepted(used) | Self::Clamped { used, .. } | Self::Ignored { used, .. } => *used,
        }
    }
    fn notice(&self) -> Option<String> {
        match self {
            Self::Accepted(_) => None,
            Self::Clamped {
                requested,
                used,
                cores,
            } => Some(format!(
                "grok: clamped {GROK_WORKER_THREADS_ENV}={requested} to {used} (valid range is 1..={cores})"
            )),
            Self::Ignored { value, .. } => Some(format!(
                "grok: ignoring {GROK_WORKER_THREADS_ENV}={value:?} (not a valid integer)"
            )),
        }
    }
}
fn cli_worker_threads() -> NonZeroUsize {
    let cores = std::thread::available_parallelism().unwrap_or(NonZeroUsize::MIN);
    let resolved = match std::env::var(GROK_WORKER_THREADS_ENV) {
        Ok(value) => worker_threads_from(Some(&value), cores),
        Err(std::env::VarError::NotPresent) => worker_threads_from(None, cores),
        Err(std::env::VarError::NotUnicode(value)) => WorkerCount::Ignored {
            value: value.to_string_lossy().into_owned(),
            used: default_worker_threads(cores),
        },
    };
    if let Some(notice) = resolved.notice() {
        eprintln!("{notice}");
    }
    resolved.used()
}
fn worker_threads_from(env_override: Option<&str>, cores: NonZeroUsize) -> WorkerCount {
    match env_override {
        Some(value) => resolve_worker_override(value, cores),
        None => WorkerCount::Accepted(default_worker_threads(cores)),
    }
}
fn default_worker_threads(cores: NonZeroUsize) -> NonZeroUsize {
    cores.min(DEFAULT_MAX_WORKER_THREADS)
}
fn resolve_worker_override(value: &str, cores: NonZeroUsize) -> WorkerCount {
    let Ok(requested) = value.trim().parse::<i128>() else {
        return WorkerCount::Ignored {
            value: value.to_owned(),
            used: default_worker_threads(cores),
        };
    };
    let clamped = requested.clamp(1, cores.get() as i128) as usize;
    let used = NonZeroUsize::new(clamped).expect("clamp floor of 1 guarantees non-zero");
    if requested == used.get() as i128 {
        WorkerCount::Accepted(used)
    } else {
        WorkerCount::Clamped {
            requested,
            used,
            cores,
        }
    }
}
/// A plain runtime drop blocks forever on an uncancellable in-flight blocking
/// task; `shutdown_timeout` abandons it after `grace` so exit can't hang.
fn run_and_shutdown<F: std::future::Future>(
    runtime: tokio::runtime::Runtime,
    fut: F,
    grace: std::time::Duration,
) -> F::Output {
    let output = runtime.block_on(fut);
    runtime.shutdown_timeout(grace);
    output
}
/// Return freed-but-retained jemalloc pages to the OS.
///
/// `arena.<MALLCTL_ARENAS_ALL>.purge` madvises away all dirty/muzzy pages in
/// every arena. The pager invokes this (via the `memory_release` seam) right
/// after known memory cliffs — e.g. dropping a session load's replay
/// transient — so a long-session resume doesn't leave hundreds of MB of dead
/// pages counted against the process for its lifetime (macOS keeps
/// `MADV_FREE`d pages in RSS until systemwide pressure).
#[cfg(all(feature = "jemalloc", unix))]
fn purge_jemalloc_retained_pages() {
    static NAME: &[u8] = b"arena.4096.purge\0";
    let ret = unsafe {
        tikv_jemalloc_sys::mallctl(
            NAME.as_ptr().cast(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0,
        )
    };
    if ret != 0 {
        static WARN_ONCE: std::sync::Once = std::sync::Once::new();
        WARN_ONCE.call_once(|| {
            tracing::warn!(
                errno = ret,
                "jemalloc arena purge mallctl failed; retained-page release is inert"
            );
        });
    }
}
/// Allocator gauges for the memory trace (`memory_trace` seam): advance the
/// jemalloc epoch so the `stats.*` reads are current, then read each gauge.
/// Returns `None` if any mallctl fails (trace records the absence). Rides
/// the `tikv-jemalloc-ctl` raw helpers (introduced by the heap-profile
/// hooks below) instead of hand-rolled mallctl.
#[cfg(all(feature = "jemalloc", unix))]
fn jemalloc_allocator_stats() -> Option<pi_pager::memory_trace::AllocatorStats> {
    /// SAFETY: callers pass fixed NUL-terminated `stats.*` size_t ctl names.
    unsafe fn gauge(name: &[u8]) -> Option<u64> {
        unsafe {
            tikv_jemalloc_ctl::raw::read::<usize>(name)
                .ok()
                .map(|v| v as u64)
        }
    }
    unsafe {
        tikv_jemalloc_ctl::raw::write(b"epoch\0", 1u64).ok()?;
        Some(pi_pager::memory_trace::AllocatorStats {
            allocated: gauge(b"stats.allocated\0")?,
            active: gauge(b"stats.active\0")?,
            resident: gauge(b"stats.resident\0")?,
            mapped: gauge(b"stats.mapped\0")?,
            retained: gauge(b"stats.retained\0")?,
            metadata: gauge(b"stats.metadata\0")?,
        })
    }
}
/// Full jemalloc statistics dump for threshold snapshots
/// (`malloc_stats_print` default human-readable format, arena detail
/// included) — the artifact the GCS memory-trace upload ships for offline
/// analysis. Raw `tikv_jemalloc_sys` because jemalloc-ctl has no
/// callback-form stats_print.
#[cfg(all(feature = "jemalloc", unix))]
fn jemalloc_stats_dump() -> String {
    unsafe extern "C" fn append(opaque: *mut std::ffi::c_void, msg: *const std::ffi::c_char) {
        unsafe {
            let out = &mut *opaque.cast::<String>();
            out.push_str(&std::ffi::CStr::from_ptr(msg).to_string_lossy());
        }
    }
    let mut out = String::new();
    unsafe {
        tikv_jemalloc_sys::malloc_stats_print(
            Some(append),
            (&raw mut out).cast(),
            std::ptr::null(),
        );
    }
    out
}
#[cfg(all(feature = "jemalloc", unix))]
fn jemalloc_heap_stats() -> Option<pi_shell::heap_profile::JemallocStats> {
    unsafe {
        tikv_jemalloc_ctl::raw::write(b"epoch\0", 1u64).ok()?;
        let allocated = tikv_jemalloc_ctl::raw::read::<usize>(b"stats.allocated\0").ok()? as u64;
        let resident = tikv_jemalloc_ctl::raw::read::<usize>(b"stats.resident\0").ok()? as u64;
        Some(pi_shell::heap_profile::JemallocStats {
            allocated,
            resident,
        })
    }
}
#[cfg(all(feature = "jemalloc", unix))]
fn jemalloc_set_prof_active(active: bool) -> bool {
    unsafe { tikv_jemalloc_ctl::raw::write(b"prof.active\0", active).is_ok() }
}
#[cfg(all(test, feature = "jemalloc", unix))]
fn jemalloc_read_prof_active() -> Option<bool> {
    unsafe { tikv_jemalloc_ctl::raw::read::<bool>(b"prof.active\0").ok() }
}
#[cfg(all(feature = "jemalloc", unix))]
fn jemalloc_prof_available() -> bool {
    unsafe { tikv_jemalloc_ctl::raw::read::<bool>(b"opt.prof\0").unwrap_or(false) }
}
#[cfg(all(feature = "jemalloc", unix))]
fn jemalloc_dump_to_path(path: &std::path::Path) -> Result<(), String> {
    use std::os::unix::ffi::OsStrExt;
    if !jemalloc_prof_available() {
        return Err("opt.prof false".into());
    }
    let c = std::ffi::CString::new(path.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
    unsafe { tikv_jemalloc_ctl::raw::write(b"prof.dump\0", c.as_ptr()) }.map_err(|e| e.to_string())
}
#[cfg(all(feature = "jemalloc", unix))]
fn install_heap_profile_hooks() {
    pi_shell::heap_profile::install(pi_shell::heap_profile::HeapProfileHooks {
        stats: jemalloc_heap_stats,
        set_prof_active: jemalloc_set_prof_active,
        dump_to_path: jemalloc_dump_to_path,
        prof_available: jemalloc_prof_available,
    });
}
fn version_text(channel_label: &str) -> String {
    format!(
        "grok {}\n",
        pi_version::display_version_with_commit(
            pi_version::full_version(),
            channel_label,
        )
    )
}
fn write_version(writer: &mut impl std::io::Write, channel_label: &str) -> std::io::Result<()> {
    writer.write_all(version_text(channel_label).as_bytes())
}
fn dispatch_version_if_requested(args: &PagerArgs) -> bool {
    if !args.version {
        return false;
    }
    if let Err(error) = write_version(
        &mut std::io::stdout().lock(),
        pi_update::channel_label(),
    ) {
        eprintln!("Error: {error}");
        std::process::exit(1);
    }
    true
}
fn dispatch_doctor_if_requested(args: &PagerArgs) -> bool {
    let Some(Command::Doctor(doctor_args)) = &args.command else {
        return false;
    };
    if let Err(error) = pi_pager::doctor_cmd::run(doctor_args.clone()) {
        eprintln!("Error: {error:#}");
        std::process::exit(1);
    }
    true
}

/// Phase 4 P3: `pi -p` is pure Python headless, not grok `run_headless`.
fn dispatch_python_print(args: &PagerArgs) -> Option<i32> {
    if args.command.is_some() {
        return None;
    }
    if args.single.is_none() && args.prompt_json.is_none() && args.prompt_file.is_none() {
        return None;
    }
    Some(run_python_print(args))
}

fn run_python_print(args: &PagerArgs) -> i32 {
    let (program, mut agent_args) = pi_pager::acp::spawn::pi_agent_command();
    if let Some(prompt) = &args.single {
        agent_args.push("-p".into());
        agent_args.push(prompt.into());
    } else if let Some(json) = &args.prompt_json {
        agent_args.push("--prompt-json".into());
        agent_args.push(json.into());
    } else if let Some(path) = &args.prompt_file {
        agent_args.push("--prompt-file".into());
        agent_args.push(path.into());
    }
    if let Some(cwd) = &args.cwd {
        agent_args.push("--cwd".into());
        agent_args.push(cwd.into());
    }
    match std::process::Command::new(&program).args(&agent_args).status() {
        Ok(status) => status.code().unwrap_or(1),
        Err(err) => {
            eprintln!(
                "failed to spawn ACP agent {program:?} {agent_args:?}: {err}\n\
                 Set PI_AGENT_COMMAND or PI_PYTHON, and install pi-agent-cli-lc."
            );
            1
        }
    }
}

fn main() {
    pi_version::set_full_version(env!("VERSION_WITH_COMMIT"));
    pi_telemetry::startup::mark_process_start();
    if let Some(code) = pi_pager::app::mermaid_worker::maybe_run_render_subprocess() {
        std::process::exit(code);
    }
    if let Some(code) = pi_pager::voice::maybe_run_capture_subprocess() {
        std::process::exit(code);
    }
    set_release_channel(ReleaseChannel::from_label(
        pi_update::channel_name().unwrap_or_default(),
    ));
    let args = PagerArgs::parse_cli();
    if let Some(code) = dispatch_python_print(&args) {
        std::process::exit(code);
    }
    if dispatch_version_if_requested(&args) || dispatch_doctor_if_requested(&args) {
        return;
    }
    pi_pager_minimal::install();
    #[cfg(all(feature = "jemalloc", unix))]
    pi_pager::memory_release::install_release_hook(purge_jemalloc_retained_pages);
    #[cfg(all(feature = "jemalloc", unix))]
    {
        pi_pager::memory_trace::install_allocator_stats_provider(jemalloc_allocator_stats);
        pi_pager::memory_trace::install_allocator_dump_provider(jemalloc_stats_dump);
    }
    #[cfg(all(feature = "jemalloc", unix))]
    install_heap_profile_hooks();
    pi_pager::memory_trace::start(pi_pager::memory_trace::default_dir());
    raise_fd_limit();
    if let Err(e) = pi_config::validate_requirements() {
        eprintln!("Couldn't start {}: {e}", pi_pager::brand::CLI_NAME);
        eprintln!();
        eprintln!(
            "Update Grok to a version the policy allows, or ask your administrator \
             to fix the managed requirements."
        );
        std::process::exit(2);
    }
    let _sentry_guard = pi_telemetry::sentry::init(pi_telemetry::sentry::Config {
        client: "zypi",
        client_version: PAGER_CLIENT_VERSION,
        release: env!("VERSION_WITH_COMMIT"),
        disabled: pi_shell::agent::config::is_error_reporting_disabled_sync(),
    });
    pi_pager::docs::extract_user_guide_docs(&pi_shell::util::grok_home::grok_home());
    pi_crash_handler::install_terminal_restore_only();
    if pi_shell::util::config::load_crash_handler_enabled_sync() {
        let crash_dir = pi_shell::util::grok_home::grok_home().join("crash");
        if let Some(report) = pi_crash_handler::check_previous_crash(&crash_dir) {
            eprintln!("Grok crashed during your last session.");
            eprintln!("  Signal:  {}", report.signal_name);
            eprintln!("  Version: {}", report.app_version);
            eprintln!("  Report:  {}", report.report_path.display());
            eprintln!();
        }
        if !pi_crash_handler::install(pi_crash_handler::CrashHandlerConfig {
            app_version: env!("VERSION_WITH_COMMIT").to_string(),
            crash_dir: crash_dir.clone(),
        }) {
            eprintln!(
                "warning: crash handler enabled but failed to install (check permissions on {})",
                crash_dir.display()
            );
        }
    }
    let crashed = pi_active_sessions::collect_crashed().unwrap_or_default();
    if !crashed.is_empty() {
        tracing::info!(
            count = crashed.len(),
            "Found crashed sessions from a previous run"
        );
    }
    let workers = cli_worker_threads();
    let mut builder = tokio::runtime::Builder::new_multi_thread();
    builder.worker_threads(workers.get()).enable_all();
    let runtime =
        pi_tty_utils::runtime::build_with_blocking_pool(&mut builder).unwrap_or_else(|e| {
            eprintln!("grok: failed to start tokio runtime: {e}");
            shutdown_and_flush_telemetry(1);
        });
    let result = run_and_shutdown(runtime, async_main(args), RUNTIME_SHUTDOWN_GRACE);
    pi_telemetry::debug_log::flush();
    if let Err(e) = result {
        pi_tty_utils::restore_native_stderr();
        match e.downcast_ref::<pi_pager::app::StartupFailure>() {
            Some(startup) => eprintln!("{}", startup.user_report()),
            None => eprintln!("Error: {e:#}"),
        }
        drop(_sentry_guard);
        std::process::exit(1);
    }
}
async fn async_main(args: PagerArgs) -> Result<()> {
    pi_extra_ca::ensure_default_crypto_provider();
    let mut args = args.apply_cwd()?;
    if let Some(ref mode) = args.compaction_mode {
        unsafe { std::env::set_var("GROK_COMPACTION_MODE", mode) };
    }
    if let Some(ref detail) = args.compaction_detail {
        unsafe { std::env::set_var("GROK_COMPACTION_DETAIL", detail) };
    }
    if args.chat() {
        unsafe {
            std::env::set_var(pi_shell::agent::chat_modes::GROK_CHAT_MODE_ENV, "1");
        }
    }
    if let Some(ref socket) = args.leader_socket {
        unsafe { std::env::set_var(pi_shell::leader::LEADER_SOCKET_ENV, socket) };
    }
    if let Some(ref path) = args.debug_file {
        unsafe {
            std::env::set_var("GROK_DEBUG_LOG", path);
            std::env::remove_var("GROK_LOG_FILE");
        }
    }
    if args.debug || args.debug_file.is_some() {
        let set_if_unset = |k: &str, v: &str| {
            if std::env::var_os(k).is_none() {
                unsafe { std::env::set_var(k, v) };
            }
        };
        set_if_unset("GROK_DEBUG_LOG", "1");
        set_if_unset("GROK_HOOKS_LOG", "1");
    }
    if let Some(Command::Completions { shell }) = &args.command {
        pi_pager::completions_cmd::run(*shell);
        return Ok(());
    }
    if let Some(Command::Wrap(ref wrap_args)) = args.command {
        return pi_pager::wrap_cmd::run(wrap_args);
    }
    args.pin_local_resume_target()?;
    let saved_profile = args.saved_resume_profile();
    let sandbox_profile_arg = match args.startup_sandbox_profile(saved_profile.as_deref()) {
        pi_pager::app::cli::SandboxStartup::Apply(profile) => profile,
        pi_pager::app::cli::SandboxStartup::Conflict { requested, saved } => {
            eprintln!(
                "error: cannot resume this session under sandbox profile '{requested}' — \
                 it was created with '{saved}'. Omit --sandbox to resume with '{saved}', \
                 or start a new session to use '{requested}'."
            );
            std::process::exit(1);
        }
    };
    pi_shell::config::apply_sandbox(
        None,
        sandbox_profile_arg.as_deref(),
        args.cwd.as_deref(),
    );
    let is_interactive = args.command.is_none()
        && args.single.is_none()
        && args.prompt_json.is_none()
        && args.prompt_file.is_none();
    pi_shell::http::set_client_name(if is_interactive {
        pi_workspace::permission::ClientType::GrokPager
    } else {
        pi_workspace::permission::ClientType::Generic
    });
    if let Some(identity) = process_identity(args.command.as_ref(), is_interactive) {
        set_identity(identity);
    }
    let update_config = build_update_config();
    if let Some(command) = args.command.take() {
        match command {
            Command::Version { json } => {
                if json {
                    let payload = serde_json::json!({
                        "currentVersion": env!("VERSION_WITH_COMMIT"),
                        "channel": pi_update::channel_name().unwrap_or("unknown"),
                    });
                    println!("{}", serde_json::to_string(&payload)?);
                } else {
                    write_version(
                        &mut std::io::stdout().lock(),
                        pi_update::channel_label(),
                    )?;
                }
                return Ok(());
            }
            Command::Doctor(_) => {
                unreachable!("doctor was consumed before runtime startup")
            }
            Command::DiskUsage(disk_usage_args) => {
                init_tracing_simple("cli");
                let _otel_guard = pi_telemetry::otel_layer::otel_guard();
                return pi_pager::disk_usage_cmd::run(disk_usage_args);
            }
            Command::Export(export_args) => {
                init_tracing_simple("cli");
                return pi_pager::export_cmd::run(export_args);
            }
            Command::Wrap(ref wrap_args) => {
                return pi_pager::wrap_cmd::run(wrap_args);
            }
            Command::Completions { shell } => {
                pi_pager::completions_cmd::run(shell);
                return Ok(());
            }
        }
    }
    let headless_prompt = pi_pager::headless::HeadlessPrompt::from_args(
        args.single.as_deref(),
        args.prompt_json.as_deref(),
        args.prompt_file.as_deref(),
    )?;
    if let Some(prompt) = headless_prompt {
        init_tracing_simple(HEADLESS_ENTRYPOINT);
        let _otel_guard = pi_telemetry::otel_layer::otel_guard();
        enforce_version_policy_or_exit();
        let launch_yolo = pi_shell::util::config::effective_yolo_for_launch(
            args.yolo,
            args.permission_mode_flag.as_deref(),
            None,
        );
        if let Some(warning) = launch_yolo.blocked_warning {
            eprintln!("{}: {warning}", pi_pager::brand::CLI_NAME);
        }
        let json_schema = args
            .json_schema
            .as_deref()
            .map(pi_pager::headless::parse_json_schema)
            .transpose()?;
        if json_schema.is_some()
            && args.output_format == pi_pager::headless::OutputFormat::Plain
        {
            args.output_format = pi_pager::headless::OutputFormat::Json;
        }
        return pi_pager::headless::run_single_turn(
            prompt,
            args.verbatim,
            pi_pager::headless::HeadlessOptions {
                session_id: args.session_id.clone(),
                resume: args.resume_session.or(args.load_session),
                resume_title_pinned: args.resume_target_pinned,
                cwd: args.cwd,
                yolo: launch_yolo.yolo,
                trust: args.trust,
                output_format: args.output_format,
                include_partial_messages: args.include_partial_messages,
                json_schema,
                model: args.model,
                rules: args.rules,
                system_prompt_override: args.system_prompt_override.clone(),
                continue_last_session: args.continue_last_session,
                fork_session: args.fork_session,
                worktree: args.worktree,
                restore_code: args.restore_code,
                agent: args.agent.clone(),
                agents_json: args.agents_json.clone(),
                cli_tools: args.cli_tools.clone(),
                cli_disallowed_tools: args.cli_disallowed_tools.clone(),
                disable_web_search: args.disable_web_search,
                allow_rules: args.allow_rules.clone(),
                deny_rules: args.deny_rules.clone(),
                max_turns: args.max_turns,
                permission_mode_flag: args.permission_mode_flag.clone(),
                reasoning_effort: args.reasoning_effort.clone(),
                wait_for_background: !args.no_wait_for_background,
                background_wait_timeout: std::time::Duration::from_secs(
                    args.background_wait_timeout_secs,
                ),
            },
        )
        .await;
    }
    enforce_version_policy_or_exit();
    // Phase 4: no otel export from the pager; Python owns LLM telemetry if any.
    let _otel_guard: Option<()> = None;
    type UpdateWaitHandle = tokio::task::JoinHandle<std::io::Result<std::process::ExitStatus>>;
    let bg_update_wait: std::sync::Arc<tokio::sync::Mutex<Option<UpdateWaitHandle>>> =
        std::sync::Arc::new(tokio::sync::Mutex::new(None));
    let bg_update_rx: Option<tokio::sync::oneshot::Receiver<Option<auto_update::UpdateAvailable>>> =
        if should_check_for_updates(args.no_auto_update) {
            let update_config = update_config.clone();
            let wait_slot = bg_update_wait.clone();
            let (tx, rx) = tokio::sync::oneshot::channel();
            tokio::spawn(async move {
                let check = auto_update::check_update_background(&update_config).await;
                if let Some(mut child) = check.download {
                    *wait_slot.lock().await = Some(tokio::spawn(async move { child.wait().await }));
                }
                let _ = tx.send(check.update);
            });
            Some(rx)
        } else {
            None
        };
    let result = pi_pager::app::run(args, bg_update_rx).await;
    pi_sandbox::flush();
    match result {
        Ok(true) => {
            let adopted = bg_update_wait.lock().await.take();
            if finish_update_on_exit(adopted, &update_config).await {
                eprintln!("Update installed. Run `{}` to start.", pi_pager::brand::CLI_NAME);
            } else {
                eprintln!(
                    "Update did not complete. Run `{} update` to retry.",
                    pi_pager::brand::CLI_NAME
                );
            }
            Ok(())
        }
        Ok(false) => Ok(()),
        Err(e) => Err(e),
    }
}
/// Complete the update after a quit-for-update (Ctrl+U) exit. Returns `true`
/// when an update path completed without a reported failure.
///
/// Prefers awaiting the parked waiter for the background `grok update` child
/// spawned at startup — the download is usually already done or in flight.
/// Only when there is no waiter (spawn failed, or no download was needed
/// because the target was already on disk) or the child failed does this
/// fall back to a fresh blocking `grok update`, which itself resolves to
/// "Already up to date" without downloading when the disk is current.
async fn finish_update_on_exit(
    adopted: Option<tokio::task::JoinHandle<std::io::Result<std::process::ExitStatus>>>,
    update_config: &UpdateConfig,
) -> bool {
    let run_blocking = |reason: Option<String>| async move {
        if let Some(reason) = reason {
            eprintln!("{reason}");
        }
        auto_update::run_update_if_available(
            auto_update::UpdateRunMode::Blocking,
            false,
            auto_update::CliUpdateTrigger::UserCommand,
            update_config,
        )
        .await
        .is_ok()
    };
    match adopted {
        Some(handle) => {
            eprintln!("Waiting for the update download to finish...");
            match handle.await {
                Ok(Ok(status)) if status.success() => true,
                Ok(Ok(status)) => {
                    run_blocking(Some(format!(
                        "Background update exited with {status}; retrying..."
                    )))
                    .await
                }
                Ok(Err(e)) => {
                    run_blocking(Some(format!(
                        "Could not wait for the background update ({e}); retrying..."
                    )))
                    .await
                }
                Err(join_err) => {
                    run_blocking(Some(format!(
                        "Background update waiter failed ({join_err}); retrying..."
                    )))
                    .await
                }
            }
        }
        None => run_blocking(None).await,
    }
}
/// Build an [`UpdateConfig`] from the current environment and config files.
fn build_update_config() -> UpdateConfig {
    let environment = pi_shell::env::GrokBuildEnvironment::from_flags(false, false);
    let mut config = UpdateConfig::from_environment(&environment);
    config.deployment_key =
        pi_shell::agent::config::EndpointsConfig::default().deployment_key;
    config.npm_registry = std::env::var("GROK_NPM_REGISTRY")
        .ok()
        .or_else(pi_shell::util::config::load_npm_registry_sync);
    if let Ok(root) = pi_shell::config::load_effective_config_disk_only()
        && let Some(ch) = pi_shell::util::config::channel_from_toml_opt(&root)
    {
        config.channel = ch;
    }
    config
}
/// Central gate for auto-update checks; add new suppression rules here,
/// not at call sites.
fn should_check_for_updates(_no_auto_update_flag: bool) -> bool {
    false
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_caps_the_core_count() {
        let nz = |n| NonZeroUsize::new(n).unwrap();
        assert_eq!(default_worker_threads(nz(360)), DEFAULT_MAX_WORKER_THREADS);
        assert_eq!(default_worker_threads(nz(4)), nz(4));
    }
    #[test]
    fn worker_threads_from_selects_default_or_override() {
        let nz = |n| NonZeroUsize::new(n).unwrap();
        let cores = nz(360);
        assert_eq!(
            worker_threads_from(None, cores),
            WorkerCount::Accepted(default_worker_threads(cores))
        );
        assert_eq!(
            worker_threads_from(Some("16"), cores),
            WorkerCount::Accepted(nz(16))
        );
    }
    #[test]
    fn override_in_range_is_used_without_a_notice() {
        let nz = |n| NonZeroUsize::new(n).unwrap();
        let cores = nz(360);
        assert_eq!(
            resolve_worker_override("16", cores),
            WorkerCount::Accepted(nz(16))
        );
        assert_eq!(resolve_worker_override("16", cores).notice(), None);
        assert_eq!(resolve_worker_override(" 8 ", cores).used().get(), 8);
        assert_eq!(
            resolve_worker_override("360", cores),
            WorkerCount::Accepted(cores)
        );
    }
    #[test]
    fn override_out_of_range_is_clamped_with_a_notice() {
        let nz = |n| NonZeroUsize::new(n).unwrap();
        let cores = nz(360);
        assert_eq!(
            resolve_worker_override("100000", cores),
            WorkerCount::Clamped {
                requested: 100000,
                used: cores,
                cores
            }
        );
        assert_eq!(
            resolve_worker_override("0", cores),
            WorkerCount::Clamped {
                requested: 0,
                used: nz(1),
                cores
            }
        );
        assert_eq!(
            resolve_worker_override("-1", cores),
            WorkerCount::Clamped {
                requested: -1,
                used: nz(1),
                cores
            }
        );
        assert_eq!(
            resolve_worker_override("100000", cores).notice().unwrap(),
            "grok: clamped GROK_WORKER_THREADS=100000 to 360 (valid range is 1..=360)"
        );
    }
    #[test]
    fn override_unparseable_is_ignored_with_a_notice() {
        let cores = NonZeroUsize::new(360).unwrap();
        for value in ["abc", "", "99999999999999999999999999999999999999999"] {
            let ignored = resolve_worker_override(value, cores);
            assert!(matches!(ignored, WorkerCount::Ignored { .. }), "{value}");
            assert_eq!(ignored.used(), default_worker_threads(cores), "{value}");
        }
        assert_eq!(
            resolve_worker_override("abc", cores).notice().unwrap(),
            "grok: ignoring GROK_WORKER_THREADS=\"abc\" (not a valid integer)"
        );
    }
    #[test]
    fn version_output_writer_preserves_channel_aware_contract() {
        pi_version::set_full_version(env!("VERSION_WITH_COMMIT"));
        for (label, expected_suffix) in [
            (" [alpha]", " [alpha]\n"),
            (" [stable]", " [stable]\n"),
            ("", ")\n"),
        ] {
            let mut output = Vec::new();
            write_version(&mut output, label).unwrap();
            let output = String::from_utf8(output).unwrap();
            assert!(output.starts_with("grok "));
            assert!(output.contains(env!("VERSION_WITH_COMMIT")));
            assert!(output.ends_with(expected_suffix), "{output:?}");
        }
    }
    #[test]
    fn version_flags_and_doctor_are_distinct_early_intents() {
        let version = PagerArgs::try_parse_from(["grok", "--version"]).unwrap();
        assert!(version.version);
        assert!(version.command.is_none());
        let short = PagerArgs::try_parse_from(["grok", "-v"]).unwrap();
        assert!(short.version);
        assert!(short.command.is_none());
        let subcommand = PagerArgs::try_parse_from(["grok", "version"]).unwrap();
        assert!(!subcommand.version);
        assert!(matches!(
            subcommand.command,
            Some(Command::Version { json: false })
        ));
    }
    #[cfg(all(feature = "jemalloc", unix))]
    struct TempHeapDump(std::path::PathBuf);
    #[cfg(all(feature = "jemalloc", unix))]
    impl TempHeapDump {
        fn new(label: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "grok-jemalloc-{label}-{}-{}.heap",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_nanos())
                    .unwrap_or(0)
            ));
            Self(path)
        }
        fn path(&self) -> &std::path::Path {
            &self.0
        }
        fn assert_nonempty_dump(&self) {
            let meta = std::fs::metadata(&self.0).expect("dump file missing after prof.dump");
            assert!(meta.len() > 0, "empty dump file");
        }
    }
    #[cfg(all(feature = "jemalloc", unix))]
    impl Drop for TempHeapDump {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    #[cfg(all(feature = "jemalloc", unix))]
    fn require_opt_prof() -> bool {
        if jemalloc_prof_available() {
            return true;
        }
        eprintln!(
            "skip jemalloc prof checks: opt.prof false \
             (release-dist static conf, or MALLOC_CONF=prof:true,prof_active:false,lg_prof_sample={})",
            pi_shell::heap_profile::LG_PROF_SAMPLE
        );
        false
    }
    #[cfg(all(feature = "jemalloc", unix))]
    fn assert_prof_active(expected: bool) {
        assert_eq!(jemalloc_read_prof_active(), Some(expected));
    }
    /// Restores process-global `prof.active` on drop (panic-safe for serial tests).
    #[cfg(all(feature = "jemalloc", unix))]
    struct ProfActiveGuard {
        previous: bool,
    }
    #[cfg(all(feature = "jemalloc", unix))]
    impl ProfActiveGuard {
        fn set(active: bool) -> Self {
            let previous = jemalloc_read_prof_active().unwrap_or(false);
            assert!(
                jemalloc_set_prof_active(active),
                "failed to set prof.active={active}"
            );
            Self { previous }
        }
    }
    #[cfg(all(feature = "jemalloc", unix))]
    impl Drop for ProfActiveGuard {
        fn drop(&mut self) {
            let _ = jemalloc_set_prof_active(self.previous);
        }
    }
    #[cfg(all(feature = "jemalloc", unix))]
    fn assert_stats_sane(stats: pi_shell::heap_profile::JemallocStats) {
        assert!(stats.allocated > 0, "allocated={}", stats.allocated);
        assert!(stats.resident > 0, "resident={}", stats.resident);
        assert!(
            stats.resident >= stats.allocated,
            "resident {} < allocated {}",
            stats.resident,
            stats.allocated
        );
    }
    #[cfg(all(feature = "jemalloc", unix))]
    #[test]
    #[serial_test::serial(jemalloc_heap_profile)]
    fn jemalloc_stats_readable_after_epoch() {
        assert_stats_sane(jemalloc_heap_stats().expect("stats readable"));
    }
    #[cfg(all(feature = "jemalloc", unix))]
    #[test]
    #[serial_test::serial(jemalloc_heap_profile)]
    fn jemalloc_prof_active_round_trip_and_dump() {
        if !require_opt_prof() {
            return;
        }
        assert_prof_active(false);
        {
            let _guard = ProfActiveGuard::set(true);
            assert_prof_active(true);
        }
        assert_prof_active(false);
        let dump = TempHeapDump::new("direct");
        jemalloc_dump_to_path(dump.path()).expect("prof.dump");
        dump.assert_nonempty_dump();
    }
    #[cfg(all(feature = "jemalloc", unix))]
    #[test]
    #[serial_test::serial(jemalloc_heap_profile)]
    fn jemalloc_dump_rejects_interior_nul_path() {
        use std::ffi::OsStr;
        use std::os::unix::ffi::OsStrExt;
        use std::path::Path;
        if !require_opt_prof() {
            return;
        }
        let path = Path::new(OsStr::from_bytes(b"/tmp/grok-jemalloc-\0.heap"));
        let err = jemalloc_dump_to_path(path).expect_err("interior NUL must fail");
        assert!(
            err.to_ascii_lowercase().contains("nul"),
            "unexpected error: {err}"
        );
    }
    #[cfg(all(feature = "jemalloc", unix))]
    #[test]
    #[serial_test::serial(jemalloc_heap_profile)]
    fn install_heap_profile_hooks_wires_shell_apis() {
        install_heap_profile_hooks();
        assert_stats_sane(
            pi_shell::heap_profile::stats().expect("shell stats after install"),
        );
        if !require_opt_prof() {
            assert!(!pi_shell::heap_profile::prof_available());
            return;
        }
        assert!(pi_shell::heap_profile::prof_available());
        assert_prof_active(false);
        {
            let _guard = ProfActiveGuard::set(true);
            assert_prof_active(true);
            assert!(pi_shell::heap_profile::set_prof_active(true));
            assert_prof_active(true);
        }
        assert_prof_active(false);
        assert!(pi_shell::heap_profile::set_prof_active(false));
        assert_prof_active(false);
        let dump = TempHeapDump::new("shell");
        pi_shell::heap_profile::dump_to_path(dump.path()).expect("shell dump");
        dump.assert_nonempty_dump();
    }
    /// Pins the gate composition; a dropped conjunct fails its named case.
    #[test]
    fn stdio_auto_update_requires_direct_stdio_enabled_and_managed() {
        assert!(stdio_auto_update_enabled(true, false, true, true));
        assert!(
            !stdio_auto_update_enabled(true, true, true, true),
            "leader bridge"
        );
        assert!(
            !stdio_auto_update_enabled(false, false, true, true),
            "non-stdio"
        );
        assert!(
            !stdio_auto_update_enabled(true, false, false, true),
            "updates off"
        );
        assert!(
            !stdio_auto_update_enabled(true, false, true, false),
            "pinned binary"
        );
    }
    use clap::Parser as _;
    /// `grok dashboard` flags the startup hook without forcing leader mode —
    /// the dashboard is independent of leader mode, so the launch keeps
    /// whatever leader setting the user (or config) chose.
    #[serial_test::serial(GROK_AGENT_DASHBOARD)]
    #[test]
    fn dashboard_subcommand_flags_startup_without_forcing_leader() {
        let mut args = PagerArgs::try_parse_from(["grok", "dashboard"]).unwrap();
        assert!(!args.leader, "fixture: no explicit --leader");
        flag_dashboard_at_startup_if_requested(&mut args).unwrap();
        assert!(!args.leader, "dashboard must NOT force leader mode");
        assert!(
            args.command.is_none(),
            "soft subcommand must be consumed so the interactive path runs",
        );
        assert_eq!(
            std::env::var("GROK_OPEN_DASHBOARD_AT_STARTUP").as_deref(),
            Ok("1"),
            "startup hook flag must be set",
        );
        unsafe { std::env::remove_var("GROK_OPEN_DASHBOARD_AT_STARTUP") };
    }
    /// `grok dashboard --no-leader` is allowed — the dashboard does not
    /// require a leader, so the combination launches into the dashboard in
    /// non-leader mode.
    #[serial_test::serial(GROK_AGENT_DASHBOARD)]
    #[test]
    fn dashboard_subcommand_allows_no_leader() {
        let mut args = PagerArgs::try_parse_from(["grok", "--no-leader", "dashboard"]).unwrap();
        flag_dashboard_at_startup_if_requested(&mut args)
            .expect("--no-leader + dashboard must be allowed");
        assert!(args.no_leader, "--no-leader must be preserved");
        assert!(!args.leader, "dashboard must not force leader mode");
        assert!(
            args.command.is_none(),
            "soft subcommand must be consumed so the interactive path runs",
        );
        assert_eq!(
            std::env::var("GROK_OPEN_DASHBOARD_AT_STARTUP").as_deref(),
            Ok("1"),
            "startup hook flag must be set",
        );
        unsafe { std::env::remove_var("GROK_OPEN_DASHBOARD_AT_STARTUP") };
    }
    /// `GROK_AGENT_DASHBOARD=0` disables the feature — the subcommand
    /// must error visibly before the TUI starts.
    #[serial_test::serial(GROK_AGENT_DASHBOARD)]
    #[test]
    fn dashboard_subcommand_errors_when_disabled() {
        unsafe { std::env::set_var("GROK_AGENT_DASHBOARD", "0") };
        let mut args = PagerArgs::try_parse_from(["grok", "dashboard"]).unwrap();
        let result = flag_dashboard_at_startup_if_requested(&mut args);
        unsafe { std::env::remove_var("GROK_AGENT_DASHBOARD") };
        let err = result.expect_err("disabled dashboard must error");
        assert!(err.to_string().contains("disabled"), "got: {err}");
        assert!(
            std::env::var("GROK_OPEN_DASHBOARD_AT_STARTUP").is_err(),
            "failure path must not flag the startup hook",
        );
    }
    #[test]
    fn workspace_command_gate_resolution() {
        use pi_shell::util::config::RemoteSettings;
        let on = RemoteSettings {
            workspace_command_enabled: Some(true),
            ..RemoteSettings::default()
        };
        let off = RemoteSettings::default();
        assert_eq!(
            workspace_command_gate(None, Some(&on)),
            WorkspaceGate::Enabled
        );
        assert_eq!(
            workspace_command_gate(None, Some(&off)),
            WorkspaceGate::Disabled
        );
        assert_eq!(workspace_command_gate(None, None), WorkspaceGate::Unknown);
        assert_eq!(
            workspace_command_gate(Some(true), Some(&off)),
            WorkspaceGate::Enabled
        );
        assert_eq!(
            workspace_command_gate(Some(true), None),
            WorkspaceGate::Enabled
        );
        assert_eq!(
            workspace_command_gate(Some(false), Some(&on)),
            WorkspaceGate::Disabled
        );
        assert_eq!(
            workspace_command_gate(Some(false), None),
            WorkspaceGate::Disabled
        );
    }
    #[serial_test::serial(GROK_WORKSPACE_COMMAND)]
    #[test]
    fn workspace_command_env_override_parsing() {
        unsafe { std::env::remove_var("GROK_WORKSPACE_COMMAND") };
        assert_eq!(workspace_command_env_override(), None);
        unsafe { std::env::set_var("GROK_WORKSPACE_COMMAND", "1") };
        assert_eq!(workspace_command_env_override(), Some(true));
        unsafe { std::env::set_var("GROK_WORKSPACE_COMMAND", "off") };
        assert_eq!(workspace_command_env_override(), Some(false));
        unsafe { std::env::remove_var("GROK_WORKSPACE_COMMAND") };
    }
    #[test]
    fn cache_initialize_request() {
        let state = make_state();
        let msg = r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#;
        cache_outgoing_acp_state(msg, &state);
        let s = state.lock().unwrap();
        assert_eq!(s.initialize_json.as_deref(), Some(msg));
    }
    #[test]
    fn cache_session_load_preserves_full_request() {
        let state = make_state();
        let msg = r#"{"jsonrpc":"2.0","id":2,"method":"session/load","params":{"sessionId":"s1","cwd":"/tmp","mcpServers":[]}}"#;
        cache_outgoing_acp_state(msg, &state);
        let s = state.lock().unwrap();
        let (sid, cached) = &s.sessions[0];
        assert_eq!(sid, "s1");
        assert_eq!(cached.load_request_json.as_deref(), Some(msg));
        assert_eq!(cached.cwd.as_deref(), Some("/tmp"));
        assert!(cached.mcp_servers_json.is_some());
        assert_eq!(s.last_session_id.as_deref(), Some("s1"));
    }
    #[test]
    fn cache_session_new_is_pending_until_response_assigns_id() {
        let state = make_state();
        let load = r#"{"jsonrpc":"2.0","id":2,"method":"session/load","params":{"sessionId":"s1","cwd":"/tmp"}}"#;
        cache_outgoing_acp_state(load, &state);
        let new = r#"{"jsonrpc":"2.0","id":3,"method":"session/new","params":{"cwd":"/home"}}"#;
        cache_outgoing_acp_state(new, &state);
        {
            let s = state.lock().unwrap();
            assert_eq!(s.sessions.len(), 1);
            assert_eq!(s.sessions[0].0, "s1");
            assert!(s.pending_new.is_some());
            assert_eq!(
                s.pending_new.as_ref().unwrap().cwd.as_deref(),
                Some("/home")
            );
        }
        cache_incoming_session_id(
            r#"{"jsonrpc":"2.0","id":3,"result":{"sessionId":"s2"}}"#,
            &state,
        );
        let s = state.lock().unwrap();
        assert!(s.pending_new.is_none());
        assert_eq!(s.sessions.len(), 2);
        assert_eq!(s.sessions[1].0, "s2");
        assert_eq!(s.sessions[1].1.cwd.as_deref(), Some("/home"));
        assert_eq!(s.last_session_id.as_deref(), Some("s2"));
    }
    #[test]
    fn cache_session_close_stops_replaying_it() {
        let state = make_state();
        cache_outgoing_acp_state(
            r#"{"jsonrpc":"2.0","id":2,"method":"session/load","params":{"sessionId":"s1","cwd":"/tmp"}}"#,
            &state,
        );
        cache_outgoing_acp_state(
            r#"{"jsonrpc":"2.0","id":3,"method":"_x.ai/session/close","params":{"sessionId":"s1"}}"#,
            &state,
        );
        let s = state.lock().unwrap();
        assert!(s.sessions.is_empty(), "closed session must not be replayed");
        assert!(s.last_session_id.is_none());
    }
    /// The standard close spelling must stop the replay exactly like the ext
    /// spelling: adopting `session/close` without teaching the cache would
    /// resurrect closed sessions on every leader reconnect.
    #[test]
    fn cache_standard_session_close_stops_replaying_it() {
        let state = make_state();
        cache_outgoing_acp_state(
            r#"{"jsonrpc":"2.0","id":2,"method":"session/load","params":{"sessionId":"s1","cwd":"/tmp"}}"#,
            &state,
        );
        cache_outgoing_acp_state(
            r#"{"jsonrpc":"2.0","id":3,"method":"session/close","params":{"sessionId":"s1"}}"#,
            &state,
        );
        let s = state.lock().unwrap();
        assert!(s.sessions.is_empty(), "closed session must not be replayed");
        assert!(s.last_session_id.is_none());
    }
    /// A resume-only session must survive a leader restart: the cache
    /// synthesizes a load entry, since a new leader has no turn to reattach to.
    #[test]
    fn cache_session_resume_registers_unknown_sessions_for_replay() {
        let state = make_state();
        cache_outgoing_acp_state(
            r#"{"jsonrpc":"2.0","id":2,"method":"session/resume","params":{"sessionId":"s1","cwd":"/proj"}}"#,
            &state,
        );
        let s = state.lock().unwrap();
        let (sid, cached) = &s.sessions[0];
        assert_eq!(sid, "s1");
        assert_eq!(
            cached.load_request_json, None,
            "a resume must not be replayed verbatim; the replay synthesizes a load"
        );
        assert_eq!(cached.cwd.as_deref(), Some("/proj"));
        assert_eq!(s.last_session_id.as_deref(), Some("s1"));
    }
    /// A resume must not displace the original load's entry: that entry
    /// carries the client's `_meta`, which a synthesized load cannot reproduce.
    #[test]
    fn cache_session_resume_does_not_displace_the_original_load() {
        let state = make_state();
        let load = r#"{"jsonrpc":"2.0","id":2,"method":"session/load","params":{"sessionId":"s1","cwd":"/tmp","_meta":{"noReplay":true}}}"#;
        cache_outgoing_acp_state(load, &state);
        cache_outgoing_acp_state(
            r#"{"jsonrpc":"2.0","id":3,"method":"session/resume","params":{"sessionId":"s1","cwd":"/tmp"}}"#,
            &state,
        );
        let s = state.lock().unwrap();
        assert_eq!(s.sessions.len(), 1, "one session, one replay entry");
        assert_eq!(
            s.sessions[0].1.load_request_json.as_deref(),
            Some(load),
            "the original load, with its _meta, must survive the resume"
        );
    }
    #[test]
    fn cache_incoming_ignores_non_session_response() {
        let state = make_state();
        let msg = r#"{"jsonrpc":"2.0","id":1,"result":{"models":[]}}"#;
        cache_incoming_session_id(msg, &state);
        let s = state.lock().unwrap();
        assert!(s.last_session_id.is_none());
        assert!(s.sessions.is_empty());
    }
    fn multi_thread_runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("build runtime")
    }
    #[test]
    fn run_and_shutdown_bounds_teardown_despite_stuck_blocking_task() {
        use std::time::{Duration, Instant};
        let grace = Duration::from_millis(200);
        let ceiling = grace * 8;
        let stuck_sleep = Duration::from_secs(10);
        let runtime = multi_thread_runtime();
        let (started_tx, started_rx) = std::sync::mpsc::channel::<()>();
        runtime.spawn_blocking(move || {
            let _ = started_tx.send(());
            std::thread::sleep(stuck_sleep);
        });
        started_rx.recv().expect("blocking task must start");
        let start = Instant::now();
        let out = run_and_shutdown(runtime, async { 7_u32 }, grace);
        let elapsed = start.elapsed();
        assert_eq!(out, 7, "must return the future's output");
        assert!(
            elapsed >= grace,
            "returned in {elapsed:?}, before the {grace:?} grace — timeout not exercised",
        );
        assert!(
            elapsed < ceiling,
            "teardown took {elapsed:?}; stuck task must be abandoned under {ceiling:?}",
        );
    }
    #[test]
    fn run_and_shutdown_is_fast_without_blocking_work() {
        use std::time::{Duration, Instant};
        let runtime = multi_thread_runtime();
        let grace = Duration::from_secs(5);
        let start = Instant::now();
        let out = run_and_shutdown(runtime, async { 42_u32 }, grace);
        let elapsed = start.elapsed();
        assert_eq!(out, 42, "must pass the future's output through");
        assert!(
            elapsed < grace,
            "clean teardown took {elapsed:?}; grace must be a ceiling, not a floor",
        );
    }
    #[test]
    fn run_and_shutdown_passes_err_output_through() {
        use std::time::Duration;
        let runtime = multi_thread_runtime();
        let out = run_and_shutdown(
            runtime,
            async { Err::<(), String>("boom".to_string()) },
            Duration::from_secs(5),
        );
        assert_eq!(
            out,
            Err("boom".to_string()),
            "Err output must pass through unchanged",
        );
    }
}
