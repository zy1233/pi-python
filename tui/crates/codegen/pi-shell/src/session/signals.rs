//! Session signals tracking for feedback heuristics.
//!
//! This module tracks session-level signals that inform feedback request decisions.
//! Signals are collected locally in the agent and periodically synced to the
//! backend for analytics / telemetry persistence.
//!
//! Uses a channel-based actor pattern to avoid locks:
//! - `SessionSignalsHandle` is a cheap, cloneable sender for reporting signals
//! - `SessionSignalsActor` runs as a background task processing signal events
//! - Snapshots are requested via oneshot channels for async response

use serde::{Deserialize, Serialize};
use tdigests::TDigest;

/// Session signals that inform feedback request heuristics.
///
/// These signals are tracked locally in the agent and periodically synced
/// to the backend for analytics / telemetry persistence.
///
/// Field names are aligned with the backend analytics schema for session signals.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default, rename_all = "camelCase")]
pub struct SessionSignals {
    // === Turn/Message Counts ===
    /// Number of user prompts/turns in this session
    pub turn_count: u32,
    /// Number of user messages sent
    pub user_message_count: u32,
    /// Number of assistant messages received
    pub assistant_message_count: u32,

    // === Error/Failure Counts ===
    /// Number of errors encountered (general errors including sampling)
    pub error_count: u32,
    /// Number of tool failures (subset of errors, specific to tools)
    pub tool_failure_count: u32,

    // === User Behavior Signals ===
    /// Number of user cancellations (Ctrl+C during agent work)
    pub cancellation_count: u32,
    /// Number of consecutive cancellations (resets when a turn completes)
    pub consecutive_cancellations: u32,
    /// Number of regenerations (user asked to redo last response)
    pub regeneration_count: u32,
    /// Whether the user has reverted any changes
    #[serde(default)]
    pub has_reverted: bool,

    // === Context/Compaction ===
    /// Number of conversation compactions performed
    pub compaction_count: u32,
    /// Cumulative total tokens across all compactions (sum of tokens_before each compaction)
    pub total_tokens_before_compaction: u64,
    /// Current context window usage as percentage (0-100)
    pub context_window_usage: u8,
    /// Raw tokens currently used in the active context window
    pub context_tokens_used: u64,
    /// Raw model context window token limit
    pub context_window_tokens: u64,

    // === Tool Usage ===
    /// Number of tool calls executed
    pub tool_call_count: u32,
    /// Distinct tools that have been used in this session
    #[serde(default)]
    pub tools_used: Vec<String>,

    // === Model Usage ===
    /// Distinct models that have been used in this session
    #[serde(default)]
    pub models_used: Vec<String>,
    /// Primary model ID (the most recently used or initially set model)
    #[serde(default)]
    pub primary_model_id: Option<String>,

    // === Edit & Retry ===
    /// Number of edit-and-retry actions (user rewinds and submits a different prompt)
    pub edit_and_retry_count: u32,

    // === Bash tool patterns (grok_build) ===
    /// Number of times the bash tool was used for a bare `echo "<msg>"` (or close
    /// variant). Tracked for usage statistics.
    #[serde(default)]
    pub bash_bare_echo_count: u32,

    // === Git/PR Metrics ===
    /// Number of successful `git commit` statements observed in bash tool calls.
    #[serde(default)]
    pub git_commit_count: u32,
    /// Number of PRs created via the session (bash `gh pr create` or MCP).
    #[serde(default)]
    pub pr_created_count: u32,
    /// Number of successful `gh pr merge` statements observed in bash tool calls.
    #[serde(default)]
    pub pr_merged_count: u32,

    // === Inference Idle Timeout ===
    /// Number of inference idle timeout events in this session.
    #[serde(default)]
    pub inference_idle_timeouts: u32,
    /// Number of doom-loop recovery resamples (server-detected reasoning
    /// loops discarded and re-sampled by the sampler's retry loop).
    #[serde(default)]
    pub doom_loop_recovery_attempts: u32,
    /// Completed responses accepted still carrying confident doom-loop
    /// signals (the resample budget was spent).
    #[serde(default)]
    pub doom_loop_recovery_accepted_after_budget: u32,
    /// Tightest (lowest-threshold) raw trigger label recovery observed this
    /// session, e.g. `tail_repetition:4@thinking`. Labels only.
    #[serde(default)]
    pub doom_loop_recovery_top_trigger: Option<String>,
    /// Stream chunks consumed by doomed attempts at their mid-stream abort
    /// points, summed across resamples (terminal detections add nothing).
    #[serde(default)]
    pub doom_loop_recovery_aborted_chunks: u64,
    /// Configured idle timeout threshold (seconds) — set once at session start.
    #[serde(default)]
    pub inference_idle_timeout_configured_secs: Option<u64>,

    // === GCS Upload Queue ===
    /// Total items enqueued for background upload.
    #[serde(default)]
    pub gcs_queue_enqueued: u64,
    /// Successful background uploads.
    #[serde(default)]
    pub gcs_queue_uploaded: u64,
    /// Items that exhausted retry budget (superset of expired).
    #[serde(default)]
    pub gcs_queue_failed: u64,
    /// Enqueue failures that fell back to inline upload.
    #[serde(default)]
    pub gcs_queue_fallbacks: u64,
    /// Circuit breaker activations.
    #[serde(default)]
    pub gcs_queue_circuit_breaker_trips: u64,
    /// Current queue depth (snapshot gauge).
    #[serde(default)]
    pub gcs_queue_pending: u64,
    /// Current disk usage of queue temp dir in bytes (snapshot gauge).
    #[serde(default)]
    pub gcs_queue_pending_bytes: u64,
    /// Orphaned temp files cleaned up at startup.
    #[serde(default)]
    pub gcs_queue_orphans_cleaned: u64,

    // === Ratings ===
    /// Number of positive ratings (thumbs-up / stars >= 4)
    pub positive_ratings: u32,
    /// Number of negative ratings (thumbs-down / stars <= 2)
    pub negative_ratings: u32,

    // === Engagement ===
    /// Number of long pauses between turns (idle > 60 s)
    pub long_pauses_count: u32,

    // === Session Metadata ===
    /// Session duration in seconds (updated on each sync)
    pub session_duration_seconds: u64,

    // === Latency Metrics ===
    /// Average time to first token in milliseconds (across all turns)
    pub avg_time_to_first_token_ms: u64,
    /// Average total response time in milliseconds (across all turns)
    pub avg_response_time_ms: u64,
    /// Minimum time to first token in milliseconds
    pub min_time_to_first_token_ms: u64,
    /// Maximum time to first token in milliseconds
    pub max_time_to_first_token_ms: u64,
    /// Total number of responses measured for latency
    pub latency_sample_count: u32,

    // === Inter-Token Latency (ITL) Metrics ===
    /// Session-level ITL p50 in milliseconds (computed from TDigest)
    pub itl_p50_ms: Option<u64>,
    /// Session-level ITL p99 in milliseconds (computed from TDigest)
    pub itl_p99_ms: Option<u64>,
    /// Session-level ITL max across all responses (monotonic max)
    pub itl_max_ms: Option<u64>,
    /// Session-level ITL mean in milliseconds (exact: sum / count)
    pub itl_mean_ms: Option<u64>,
    /// Total content chunks received across all responses
    pub total_chunk_count: u64,
    /// Number of responses measured for ITL
    pub itl_sample_count: u32,

    // === LOC Attribution ===
    /// Gross lines added by agent (monotonic, only increases)
    #[serde(default)]
    pub agent_lines_added: i64,
    /// Gross baseline lines removed by agent (monotonic)
    #[serde(default)]
    pub agent_lines_removed: i64,
    /// Agent-added lines that were later rejected/superseded (monotonic)
    #[serde(default)]
    pub agent_lines_added_reverted: i64,
    /// Agent-removed lines that were later rejected/superseded (monotonic)
    #[serde(default)]
    pub agent_lines_removed_reverted: i64,
    /// Gross lines added by human (monotonic)
    #[serde(default)]
    pub human_lines_added: i64,
    /// Gross baseline lines removed by human (monotonic)
    #[serde(default)]
    pub human_lines_removed: i64,
    /// Human-added lines that were later rejected/superseded (monotonic)
    #[serde(default)]
    pub human_lines_added_reverted: i64,
    /// Human-removed lines that were later rejected/superseded (monotonic)
    #[serde(default)]
    pub human_lines_removed_reverted: i64,
    /// Distinct files touched by agent
    #[serde(default)]
    pub agent_files_touched: u32,
    /// Distinct files touched by human
    #[serde(default)]
    pub human_files_touched: u32,
    /// Total distinct files touched (union)
    #[serde(default)]
    pub total_files_touched: u32,

    // === Internal ITL state (not serialized over the wire) ===
    /// TDigest for session-level percentile computation
    #[serde(skip)]
    pub itl_digest: Option<TDigest>,
    /// Running sum of all ITL intervals (for exact mean computation)
    #[serde(skip)]
    pub itl_sum_ms: u64,
    /// Running count of all ITL intervals (for exact mean computation)
    #[serde(skip)]
    pub itl_interval_count: u64,

    // === Observability ===
    /// Peak resident set size in bytes (monotonically increasing)
    #[serde(default)]
    pub peak_rss_bytes: u64,
}
