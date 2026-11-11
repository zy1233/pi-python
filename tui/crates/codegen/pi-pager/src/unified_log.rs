//! Log lines of the pager.
//!
//! The pager used to buffer these entries and send them to the agent as vendor notifications.
//! The old Rust shell wrote them to `unified.jsonl`. The agent is now a Python process, and it
//! has no handler for that notification. The pager sends no vendor extension
//! (`pi_acp_lib::acp_send` drops it), so the entries go to `tracing`.
//!
//! Use [`info`], [`warn`], [`error`], [`debug`] from anywhere. [`write_direct_info`] writes
//! one entry straight to `unified.jsonl`.

use pi_telemetry::unified_log::{ClientLogEntry, LogLevel, LogSource};

/// The `tracing` target of every entry.
const TARGET: &str = "pi_pager::unified_log";

fn now_ts() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

fn make_entry(
    lvl: LogLevel,
    msg: &str,
    sid: Option<&str>,
    ctx: Option<serde_json::Value>,
) -> ClientLogEntry {
    ClientLogEntry {
        ts: now_ts(),
        pid: Some(std::process::id()),
        ver: Some(pi_version::VERSION.to_owned()),
        lvl,
        sid: sid.map(Into::into),
        msg: msg.into(),
        ctx,
    }
}

/// Write an info entry straight to `unified.jsonl`, with the pager source and pid stamping.
/// Use it for a fact that must survive a failed startup.
pub fn write_direct_info(msg: &str, ctx: Option<serde_json::Value>) {
    pi_telemetry::unified_log::ingest_client_entries(
        LogSource::GrokPager,
        &[make_entry(LogLevel::Info, msg, None, ctx)],
    );
}

fn push_entry(lvl: LogLevel, msg: &str, sid: Option<&str>, ctx: Option<serde_json::Value>) {
    let ctx = ctx.map(|value| value.to_string());
    let ctx = ctx.as_deref();
    match lvl {
        LogLevel::Error => tracing::error!(target: TARGET, session_id = sid, ctx, "{msg}"),
        LogLevel::Warn => tracing::warn!(target: TARGET, session_id = sid, ctx, "{msg}"),
        LogLevel::Info => tracing::info!(target: TARGET, session_id = sid, ctx, "{msg}"),
        LogLevel::Debug => tracing::debug!(target: TARGET, session_id = sid, ctx, "{msg}"),
    }
}

/// Log an info-level entry.
pub fn info(msg: &str, sid: Option<&str>, ctx: Option<serde_json::Value>) {
    push_entry(LogLevel::Info, msg, sid, ctx);
}

/// Log a warn-level entry.
pub fn warn(msg: &str, sid: Option<&str>, ctx: Option<serde_json::Value>) {
    push_entry(LogLevel::Warn, msg, sid, ctx);
}

/// Log an error-level entry.
pub fn error(msg: &str, sid: Option<&str>, ctx: Option<serde_json::Value>) {
    push_entry(LogLevel::Error, msg, sid, ctx);
}

/// Log a debug-level entry.
pub fn debug(msg: &str, sid: Option<&str>, ctx: Option<serde_json::Value>) {
    push_entry(LogLevel::Debug, msg, sid, ctx);
}
