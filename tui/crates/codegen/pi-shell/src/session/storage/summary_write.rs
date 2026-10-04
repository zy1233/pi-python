//! Concurrency-safe, field-correct writes to a session's `summary.json`.
//!
//! The same `summary.json` is mutated by several writers and, on reconnect, by
//! more than one persistence actor. A whole-summary read-modify-write with no
//! lock loses updates: a writer holding a stale read overwrites a concurrent
//! writer's field on write-back, which silently reverted `last_active_at` and
//! `num_messages` (the active session then sank in the `/resume` picker).
//!
//! [`SummaryPatch`] expresses *intent* (a partial update) rather than a
//! whole-struct snapshot, and [`apply_patch_locked`] applies it under an
//! exclusive lock on a sidecar `summary.json.lock` (never renamed, so the lock
//! spans the entire read-modify-write). All writers funnel through it, so the
//! read-modify-writes serialize across actors and processes.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::Path;

use agent_client_protocol as acp;
use chrono::{DateTime, Utc};
use fs2::FileExt;
use pi_sampling_types::ReasoningEffort;

use crate::session::persistence::Summary;

/// How a counter field changes. `Increment` is applied to the in-lock fresh
/// read (never precomputed by the caller, which would re-open the race); `Set`
/// is an absolute rewrite (compaction / rewind).
#[derive(Debug, Clone)]
pub(crate) enum CounterOp {
    Increment(usize),
    Set(usize),
}

impl CounterOp {
    fn apply(&self, current: usize) -> usize {
        match self {
            CounterOp::Increment(n) => current.saturating_add(*n),
            CounterOp::Set(n) => *n,
        }
    }
}

/// Model / agent / reasoning-effort update. Each `None` leaves the existing
/// value unchanged (matches the legacy `update_current_model` semantics).
#[derive(Debug, Clone)]
pub(crate) struct ModelPatch {
    pub model_id: acp::ModelId,
    pub agent_name: Option<String>,
    pub reasoning_effort: Option<Option<ReasoningEffort>>,
}

/// Persisted git HEAD. `commit` and `branch` are last-writer-wins, including
/// being cleared to `None`.
#[derive(Debug, Clone)]
pub(crate) struct GitHeadPatch {
    pub commit: Option<String>,
    pub branch: Option<String>,
}

/// Telemetry trace bookkeeping. `next_trace_turn` is monotonic; `request_id`
/// is applied only when this turn wins, so a stale lower-turn write cannot
/// leave a high `next_trace_turn` paired with an older `request_id` (these
/// were set together in the legacy read-modify-write path).
#[derive(Debug, Clone)]
pub(crate) struct TraceTurnPatch {
    pub next_trace_turn: u64,
    pub request_id: Option<String>,
}

/// A typed, partial mutation of a `Summary`. Only the set fields change; the
/// rest are read fresh under the lock and preserved. Per-field merge rules
/// (see [`Summary::apply_patch`]): `last_active_at` / `next_trace_turn` /
/// `chat_format_version` are monotonic (never lowered), counters apply to the
/// fresh read, everything else is last-writer-wins on that field alone.
#[derive(Debug, Clone, Default)]
pub(crate) struct SummaryPatch {
    pub record_activity: bool,
    pub messages: Option<CounterOp>,
    pub chat_messages: Option<CounterOp>,
    pub chat_format_version: Option<u8>,
    pub trace_turn: Option<TraceTurnPatch>,
    pub model: Option<ModelPatch>,
    pub git_head: Option<GitHeadPatch>,
    pub collection_id: Option<String>,
    /// Set the session title unconditionally (last-writer-wins). Used by the
    /// manual `/rename` (`/title`) path, which must always win. Also marks the
    /// title manual (`Summary::title_is_manual`).
    pub generated_title: Option<String>,
    /// Set the session title only when the session has no title yet. Used by
    /// automatic LLM title generation so it never clobbers a title the user
    /// set via `/rename`. Ignored when `generated_title` is also set.
    pub generated_title_if_absent: Option<String>,
    /// Overwrite an existing *auto* title with a freshly regenerated one, but
    /// never a manual `/rename`. Used by the early-session title refresh
    /// (turns 3 and 6). Ignored when `generated_title` (manual) is also set.
    pub generated_title_regenerate: Option<String>,
    /// `/rename --auto`: clear the manual pin. Takes precedence over the
    /// generated-title fields. A successful clear blanks `generated_title`
    /// *and* `session_summary` so `display_title()` is empty and if-absent
    /// can adopt again (a leftover pre-rename auto title would block regen).
    pub reset_title_to_auto: bool,
    pub cwd_switch_bookkeeping_generation: Option<u64>,
    /// Per-turn dashboard summary as `(text, prompt_id)`. Outer `Some`
    /// applies (last-writer-wins); `Some(None)` clears it (conversation
    /// rewind removed the described work).
    pub last_turn_summary: Option<Option<(String, String)>>,
    /// Latest session recap preview. Outer `Some` applies (last-writer-wins);
    /// `Some(None)` clears it (rewind removed the described turns). Persisted so
    /// listing surfaces can show a recap when available.
    pub last_recap: Option<Option<String>>,
}

impl Summary {
    /// Apply `patch` in place using the per-field merge rules. `now` is the
    /// single timestamp used for both `last_active_at` (when activity is
    /// recorded) and `updated_at`.
    ///
    /// Returns `true` iff an auto title was adopted (`generated_title_if_absent`
    /// or `generated_title_regenerate`), **or** a `reset_title_to_auto` actually
    /// cleared a manual pin. Callers use the former to propagate the adopted
    /// title and the latter to reset the generator / remote pin only when unpin
    /// changed disk.
    pub(crate) fn apply_patch(&mut self, patch: &SummaryPatch, now: DateTime<Utc>) -> bool {
        if patch.record_activity {
            // Monotonic: a stale concurrent writer can never move it backwards.
            self.last_active_at = Some(
                self.last_active_at
                    .map_or(now, |existing| existing.max(now)),
            );
        }
        if let Some(op) = &patch.messages {
            self.num_messages = op.apply(self.num_messages);
        }
        if let Some(op) = &patch.chat_messages {
            self.num_chat_messages = op.apply(self.num_chat_messages);
        }
        if let Some(version) = patch.chat_format_version {
            self.chat_format_version = self.chat_format_version.max(version);
        }
        if let Some(generation) = patch.cwd_switch_bookkeeping_generation
            && generation > self.cwd_switch_bookkeeping_generation
        {
            self.cwd_switch_bookkeeping_generation = generation;
            // An explicit chat counter op already owns the resulting count
            // (append increments; history replacement sets). Without one, this
            // patch repairs a line found on disk after an earlier summary failure.
            if patch.chat_messages.is_none() {
                self.num_chat_messages = self.num_chat_messages.saturating_add(1);
            }
        }
        if let Some(trace_turn) = &patch.trace_turn {
            // next_trace_turn is monotonic; keep request_id paired with the
            // winning turn so a stale lower-turn write can't re-pair them.
            if trace_turn.next_trace_turn >= self.next_trace_turn {
                self.next_trace_turn = trace_turn.next_trace_turn;
                if let Some(request_id) = &trace_turn.request_id {
                    self.request_id = Some(request_id.clone());
                }
            }
        }
        if let Some(model) = &patch.model {
            self.current_model_id = model.model_id.clone();
            if let Some(agent_name) = &model.agent_name {
                self.agent_name = Some(agent_name.clone());
            }
            if let Some(reasoning_effort) = &model.reasoning_effort {
                self.reasoning_effort = *reasoning_effort;
            }
        }
        if let Some(git_head) = &patch.git_head {
            self.head_commit = git_head.commit.clone();
            self.head_branch = git_head.branch.clone();
        }
        if let Some(collection_id) = &patch.collection_id {
            self.collection_id = Some(collection_id.clone());
        }
        if let Some(last_turn_summary) = &patch.last_turn_summary {
            let (text, prompt_id) = last_turn_summary.clone().unzip();
            self.last_turn_summary = text;
            self.last_turn_summary_prompt_id = prompt_id;
        }
        if let Some(recap) = &patch.last_recap {
            self.last_recap = recap.clone();
        }
        let mut absent_title_applied = false;
        if patch.reset_title_to_auto {
            // Gate on a real pin (`manual_title_opt`), not a stale flag
            // over a blank `generated_title` — that would wipe a legitimate
            // auto title living in `session_summary`.
            let cleared_manual = self.manual_title_opt().is_some();
            if cleared_manual {
                self.generated_title = None;
                // Blank both fields so `display_title()` is empty and
                // `set_generated_title_if_absent` can adopt again. A leftover
                // pre-rename auto title in `session_summary` would otherwise
                // pin display forever.
                self.session_summary.clear();
            }
            self.title_is_manual = false;
            self.updated_at = now;
            return cleared_manual;
        } else if let Some(title) = &patch.generated_title {
            self.set_title(title);
            // Manual `/rename`: recorded so clients can restore the
            // prompt-border title on resume.
            self.title_is_manual = true;
        } else if let Some(title) = &patch.generated_title_regenerate {
            // Early-session refresh: replace an existing auto title, but never
            // a manual `/rename` (checked atomically under the summary lock).
            if !self.title_is_manual {
                self.set_title_overwrite(title);
                absent_title_applied = true;
            }
        } else if let Some(title) = &patch.generated_title_if_absent {
            // Auto-generated titles defer to any title already present, so a
            // manual `/rename` is never overwritten by a racing LLM title.
            if self.display_title().trim().is_empty() {
                self.set_title(title);
                // Defensive: an adopted auto title is never manual.
                self.title_is_manual = false;
                absent_title_applied = true;
            }
        }
        self.updated_at = now;
        absent_title_applied
    }

    /// Set `generated_title`, mirroring into `session_summary` while that field
    /// is still empty so older clients that only read `session_summary` see the
    /// title too.
    fn set_title(&mut self, title: &str) {
        self.generated_title = Some(title.to_owned());
        if self.session_summary.is_empty() {
            self.session_summary = title.to_owned();
        }
    }

    /// Replace an auto title with a refreshed one. Unlike [`Self::set_title`],
    /// this also updates the mirrored `session_summary` (which for an auto title
    /// holds the previous auto title) so `display_title()` — read by older
    /// clients that only see `session_summary` — reflects the new title. Only
    /// called for non-manual titles, so no manual `/rename` is overwritten.
    fn set_title_overwrite(&mut self, title: &str) {
        self.generated_title = Some(title.to_owned());
        self.session_summary = title.to_owned();
    }
}

/// Read → apply `patch` → write `summary_path`, serialized by an exclusive lock
/// on the sidecar `lock_path`. The lock is held across the whole read-modify-
/// write so concurrent writers cannot lose each other's updates. Synchronous:
/// callers run it on `spawn_blocking` because the lock acquisition blocks.
///
/// Returns whether a `generated_title_if_absent` was applied (see
/// [`Summary::apply_patch`]). Because the read-modify-write happens under the
/// lock, this "set the title only if absent" check is atomic against a
/// concurrent manual rename.
pub(crate) fn apply_patch_locked(
    summary_path: &Path,
    lock_path: &Path,
    patch: &SummaryPatch,
) -> io::Result<bool> {
    let lock = open_lock_file(lock_path)?;
    lock.lock_exclusive()?;
    let result = read_modify_write(summary_path, patch);
    let _ = lock.unlock();
    result
}

fn read_modify_write(summary_path: &Path, patch: &SummaryPatch) -> io::Result<bool> {
    let mut summary = read_summary(summary_path)?;
    let absent_title_applied = summary.apply_patch(patch, Utc::now());
    write_summary_atomic(summary_path, &summary)?;
    Ok(absent_title_applied)
}

fn open_lock_file(path: &Path) -> io::Result<File> {
    OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)
}

fn read_summary(path: &Path) -> io::Result<Summary> {
    let bytes = std::fs::read(path)?;
    if bytes.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("summary.json is empty (0 bytes): {}", path.display()),
        ));
    }
    serde_json::from_slice::<Summary>(&bytes)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

fn write_summary_atomic(summary_path: &Path, summary: &Summary) -> io::Result<()> {
    let bytes = serde_json::to_vec_pretty(summary)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    crate::session::storage::write_bytes_atomic(summary_path, &bytes)
}

#[cfg(test)]
mod tests {
    
    
    
    
    
    
    

}
