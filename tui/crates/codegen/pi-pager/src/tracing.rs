//! Tracing capture and display for the pager's tracing pane.
//!
//! This module provides:
//!
//! - [`TracingEntry`] — a single log line, parsed from an ANSI-formatted string
//!   into a ratatui [`Text`] for rendering and a plain-text [`Arc<str>`] for search.
//!   Implements [`ListItem`] so it can be displayed in a [`ListPane`].
//!
//! - [`TracingModel`] — a bounded, append-only ring buffer of [`TracingEntry`] items.
//!   Uses `Vec` with batch eviction (not `VecDeque`) so that `as_slice()` returns a
//!   single contiguous `&[TracingEntry]` for the `ListPane` API.
//!
//! ## Architecture (Option A — ANSI pass-through)
//!
//! The current approach receives pre-formatted ANSI strings from
//! `tracing-subscriber`'s `Full` formatter, parses them with `ansi-to-tui` into
//! styled ratatui `Text`, and renders them directly. This is the simplest path
//! to a working tracing pane.
//!
//! ## Future: Option B — Structured capture
//!
//! A future iteration could replace the ANSI pass-through with a custom
//! `tracing_subscriber::Layer` that captures structured event data (level, target,
//! spans, fields) into `TracingEntry` directly. The `ListItem` trait boundary
//! insulates `ListPane` from this change — only this module would need updating.
//! The `TracingEntry::new()` constructor is the seam: swap its internals from
//! "parse ANSI string" to "format structured fields" and nothing else changes.
use std::io;
use tokio::sync::mpsc;
use tracing_subscriber::fmt::MakeWriter;
/// Target for the full ACP update payload dump (plain JSON, no ANSI).
///
/// Off by default in release builds: serializing every update at streaming
/// rate (bash `raw_output` byte arrays reach hundreds of KB per line) plus
/// retention in the log channel was the 50-60GB OOM class. Payload fields on
/// this target must be wrapped in [`LazyJson`] so serialization only happens
/// inside a recording subscriber: the dev pane filter (dev builds) or the
/// firehose (`GROK_DEBUG_LOG` / `GROK_LOG_FILE`).
pub use pi_telemetry::debug_log::ACP_UPDATE_PAYLOAD_TARGET;
/// Target for the always-on compact ACP update summary line (kind, ids,
/// status, payload sizes). Cheap to format at streaming rate.
///
/// Defined in `pi-telemetry` so the firehose directives and the pager
/// filter share one constant (re-exported here for callsites).
pub use pi_telemetry::debug_log::ACP_UPDATE_TARGET;
/// Capacity of the log channel between tracing-subscriber and the UI.
///
/// Bounded so a starved consumer (the event loop drains it only on ticks,
/// which are deprioritized below ACP traffic) caps retention at
/// `capacity x line size` instead of growing without limit. On overflow the
/// newest line is dropped and [`dropped_log_lines`] is incremented.
const LOG_CHANNEL_CAPACITY: usize = 16 * 1024;
/// Lines dropped due to a full log channel (process-wide).
static DROPPED_LOG_LINES: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
/// Type aliases for the channel endpoints.
pub type LogTx = mpsc::Sender<String>;
pub type LogRx = mpsc::Receiver<String>;
/// Factory that creates [`TracingChannelWriter`] instances for `tracing-subscriber`.
///
/// Implements [`MakeWriter`] so it can be passed to
/// `tracing_subscriber::fmt().with_writer(make_writer)`.
///
/// Created via [`TracingChannelMakeWriter::new()`], which returns the writer
/// factory and the receiving end of the channel.
#[derive(Clone)]
pub struct TracingChannelMakeWriter(LogTx);
impl TracingChannelMakeWriter {
    /// Create a new channel writer pair.
    ///
    /// Returns `(make_writer, receiver)`. Pass `make_writer` to
    /// `tracing_subscriber::fmt().with_writer(...)`. Poll `receiver` in your
    /// event loop and feed each `String` to [`TracingModel::push()`].
    pub fn new() -> (Self, LogRx) {
        let (tx, rx) = mpsc::channel(LOG_CHANNEL_CAPACITY);
        (Self(tx), rx)
    }
}
impl<'a> MakeWriter<'a> for TracingChannelMakeWriter {
    type Writer = TracingChannelWriter;
    fn make_writer(&'a self) -> Self::Writer {
        TracingChannelWriter { tx: self.0.clone() }
    }
}
/// Writer that sends each formatted log line to a bounded mpsc channel
/// (drop-on-full; see [`write()`](io::Write::write) — logging must never OOM
/// or back-pressure the runtime, so a full channel drops the line).
///
/// Created by [`TracingChannelMakeWriter`]. Each call to [`write()`](io::Write::write)
/// trims ASCII whitespace, skips empty lines, and sends the result as a `String`.
///
/// The receiver side (held by the event loop) drains these strings into a
/// [`TracingModel`].
#[derive(Clone)]
pub struct TracingChannelWriter {
    tx: LogTx,
}
impl io::Write for TracingChannelWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let s = String::from_utf8_lossy(buf.trim_ascii());
        if !s.is_empty() {
            match self.tx.try_send(s.into_owned()) {
                Ok(()) => {}
                Err(mpsc::error::TrySendError::Full(_)) => {
                    DROPPED_LOG_LINES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                }
                Err(mpsc::error::TrySendError::Closed(_)) => {
                    return Err(io::Error::other("tracing channel send failed"));
                }
            }
        }
        Ok(buf.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
/// Return value from [`init_tracing()`].
///
/// Holds the receiving end of the log channel. The caller should poll `rx` in
/// the event loop and feed each `String` to [`TracingModel::push()`].
pub struct TracingHandle {
    /// Receive log lines here. Each string is a pre-formatted ANSI line from
    /// `tracing-subscriber`'s `Full` formatter.
    pub rx: LogRx,
}
/// Initialize a `tracing-subscriber` that captures formatted log lines into a
/// channel, ready for display in a [`TracingModel`].
///
/// This sets the global default subscriber. Call it once at startup, before any
/// `tracing::info!()` calls.
///
/// The subscriber uses:
/// - `Full` formatter (timestamp + level + target + message)
/// - ANSI colors enabled (`with_ansi(true)`)
/// - `RUST_LOG` env filter (defaults to `info` if unset)
///
/// Returns a [`TracingHandle`] whose `rx` field should be polled each tick.
///
/// # Example
///
/// ```ignore
/// let handle = init_tracing();
/// // In event loop:
/// while let Ok(line) = handle.rx.try_recv() {
///     model.push(&line);
/// }
/// ```
pub fn init_tracing() -> TracingHandle {
    use tracing_subscriber::{
        EnvFilter, Layer as _, filter::LevelFilter, fmt, layer::SubscriberExt as _,
    };
    use pi_telemetry::debug_log::RMCP_SSE_NOISE_TARGET;
    let (make_writer, rx) = TracingChannelMakeWriter::new();
    let payload_level = "off";
    let directives = format!(
        "pi_shell=info,pi_pager=trace,pi_tools=info,pi_session_search=info,pi_acp_lib=info,{RMCP_SSE_NOISE_TARGET}=error,sampling_log=off,{ACP_UPDATE_TARGET}=debug,{ACP_UPDATE_PAYLOAD_TARGET}={payload_level}"
    );
    let env_filter = EnvFilter::builder()
        .with_default_directive(LevelFilter::WARN.into())
        .parse_lossy(&directives);
    let fmt_layer = fmt::layer()
        .with_target(true)
        .with_ansi(true)
        .with_writer(make_writer);
    let otel_layer = pi_telemetry::otel_layer::build_otel_layer(
        pi_telemetry::otel_layer::OtelClientInfo {
            client_name: "grok-pager",
            client_version: pi_version::VERSION,
            service_version: pi_version::full_version(),
            app_entrypoint: "tui",
        },
        pi_shell::auth::credential_provider::build_default_otel_layer_config(),
    );
    let instrumentation_layer = pi_telemetry::instrumentation::layer();
    let sampling_log_layer = pi_telemetry::sampling_log::layer();
    let hooks_log_layer = pi_telemetry::hooks_log::layer();
    let registry = tracing_subscriber::registry()
        .with(fmt_layer.with_filter(env_filter))
        .with(instrumentation_layer)
        .with(sampling_log_layer)
        .with(hooks_log_layer)
        .with(otel_layer);
    pi_telemetry::debug_log::install_firehose(registry, "tui");
    pi_telemetry::external::init(
        pi_shell::agent::config::resolve_external_otel_config(
            pi_telemetry::external::config::ExternalClientInfo {
                service_version: pi_version::full_version().to_owned(),
                client_version: pi_version::VERSION.to_owned(),
                app_entrypoint: "tui".to_owned(),
            },
        ),
    );
    TracingHandle { rx }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn channel_writer_sends_trimmed_line() {
        use std::io::Write;
        let (make_writer, mut rx) = TracingChannelMakeWriter::new();
        let mut writer = make_writer.make_writer();
        writer.write_all(b"  hello world  \n").unwrap();
        let msg = rx.try_recv().unwrap();
        assert_eq!(msg, "hello world");
    }
    #[test]
    fn channel_writer_skips_empty() {
        use std::io::Write;
        let (make_writer, mut rx) = TracingChannelMakeWriter::new();
        let mut writer = make_writer.make_writer();
        writer.write_all(b"   \n").unwrap();
        writer.write_all(b"").unwrap();
        assert!(rx.try_recv().is_err());
    }
    #[test]
    fn channel_writer_returns_original_len() {
        use std::io::Write;
        let (make_writer, _rx) = TracingChannelMakeWriter::new();
        let mut writer = make_writer.make_writer();
        let input = b"hello\n";
        let n = writer.write(input).unwrap();
        assert_eq!(n, input.len());
    }
    #[test]
    fn channel_writer_error_on_closed_receiver() {
        use std::io::Write;
        let (make_writer, rx) = TracingChannelMakeWriter::new();
        let mut writer = make_writer.make_writer();
        drop(rx);
        let result = writer.write(b"hello");
        assert!(result.is_err());
    }
    #[test]
    fn channel_writer_multiple_writes() {
        use std::io::Write;
        let (make_writer, mut rx) = TracingChannelMakeWriter::new();
        let mut writer = make_writer.make_writer();
        writer.write_all(b"line 1\n").unwrap();
        writer.write_all(b"line 2\n").unwrap();
        writer.write_all(b"line 3\n").unwrap();
        assert_eq!(rx.try_recv().unwrap(), "line 1");
        assert_eq!(rx.try_recv().unwrap(), "line 2");
        assert_eq!(rx.try_recv().unwrap(), "line 3");
    }
    #[test]
    fn channel_writer_preserves_ansi() {
        use std::io::Write;
        let (make_writer, mut rx) = TracingChannelMakeWriter::new();
        let mut writer = make_writer.make_writer();
        let ansi_line = b"\x1b[32m INFO\x1b[0m  hello\n";
        writer.write_all(ansi_line).unwrap();
        let msg = rx.try_recv().unwrap();
        assert!(
            msg.contains("\x1b[32m"),
            "ANSI should be preserved: {msg:?}"
        );
        assert!(msg.contains("INFO"), "content should be preserved: {msg:?}");
    }
    #[test]
    fn make_writer_is_clone() {
        let (mw, _rx) = TracingChannelMakeWriter::new();
        let _mw2 = mw.clone();
    }
}
