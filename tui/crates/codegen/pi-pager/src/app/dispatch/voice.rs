//! Voice mode enable, toggle, and stop dispatchers.

use super::session::lifecycle::dispatch_new_session;
use crate::app::actions::Effect;
use crate::app::app_view::{ActiveView, AppView, VoiceState, VoiceTarget};

/// Promote live interim into the bound prompt, then hard-reset (no trailing
/// final). Returns the fragment for callers that captured text earlier.
pub(super) fn voice_stop_on_submit(app: &mut AppView) -> Option<String> {
    let interim = crate::voice::commit_interim_into_prompt(app);
    app.voice_reset();
    interim
}

/// Merge interim into a payload captured before [`voice_stop_on_submit`].
pub(super) fn merge_prompt_with_voice_interim(existing: String, interim: Option<String>) -> String {
    match interim {
        Some(interim) => crate::voice::combine_prompt_with_voice_text(&existing, &interim),
        None => existing,
    }
}

/// The prompt box dictation should target for the current surface: the active
/// agent's prompt. `None` off that surface.
fn voice_target_for_view(app: &AppView) -> Option<VoiceTarget> {
    match app.active_view {
        ActiveView::Agent(id) => Some(VoiceTarget::Agent(id)),
        _ => None,
    }
}

/// Enable session voice mode and start recording. The start primitive reached
/// by the toggle ([`dispatch_voice_toggle`], i.e. `/voice` / Ctrl+Space when
/// idle) and the Ctrl+Space hold-to-talk key-press.
///
/// **Gated on the voice-mode flag.** When
/// voice isn't available (flag off, or a build without audio capture) this is a
/// **silent no-op** — no toast — so users who don't have the feature see
/// nothing. Otherwise dictation routes
/// into the active agent's prompt box. On the session-less welcome screen (first
/// launch) a session is created first — via the gated [`dispatch_new_session`],
/// so auth and folder-trust are respected — so voice works from a cold start in
/// one press. Any other surface with no visible box is a silent no-op.
/// `from_hold` marks a Ctrl+Space hold-press start (`VoiceState::*::hold`) so the
/// matching Ctrl+Space release (see [`dispatch_voice_stop`]) ends *this* session
/// and only this one; `/voice` and the toggle pass `false` so a Ctrl+Space
/// release can't stop them.
pub(super) fn dispatch_enable_voice_mode(app: &mut AppView, from_hold: bool) -> Vec<Effect> {
    // Remote-flag gate only. Silent when unavailable.
    if !app.voice_mode_enabled || !pi_voice::AUDIO_SUPPORTED {
        return vec![];
    }
    // The session-less welcome screen (first launch) has no prompt box, so create
    // a session there — via the gated `dispatch_new_session`, so auth + folder-trust
    // hold — letting voice dictate into it from a cold start. `switch_to_agent`
    // makes the new agent the active view, which the target lookup below then binds.
    let mut effects = Vec::new();
    if matches!(app.active_view, ActiveView::Welcome) && app.session_startup_allowed() {
        effects = dispatch_new_session(app);
    }

    // Bind the dictation target at press time (the cold-start path defers capture
    // to the event loop, where the surface could have changed). `None` is a
    // box-less surface — e.g. a welcome the gate kept closed — so stay a silent
    // no-op.
    let Some(target) = voice_target_for_view(app) else {
        return effects;
    };

    app.voice_ui_active = true;
    if app.voice_cmd_tx.is_some() {
        // Pipeline already up. Start a new recording now; if one is already live
        // leave it (and its hold-ownership) untouched — a press over an existing
        // session doesn't take it over.
        if !app.voice_listening() {
            app.voice_begin_recording(target, from_hold);
        }
    } else if !app.voice_state.pending_cold_start() {
        // Pipeline still spawning. Queue a cold-start — but only if one isn't
        // already pending: a second toggle/press must re-affirm the first start,
        // not clobber its hold-ownership (which decides whether a Ctrl+Space
        // release cancels it) or its bound target. The event loop opens the mic once the
        // pipeline is live.
        app.voice_state = VoiceState::ColdStart {
            hold: from_hold,
            target,
        };
    }
    effects
}

/// Toggle mic capture: `Ctrl+Space`, Esc (while listening), the recording-row
/// `[stop]`, and `Ctrl+Space` on terminals without key-release events. While
/// recording this stops; otherwise it starts — enabling voice mode and spawning
/// the pipeline if needed, exactly like `/voice`. No `/voice`-first prerequisite.
pub(super) fn dispatch_voice_toggle(app: &mut AppView) -> Vec<Effect> {
    if app.voice_listening() {
        // Stop always succeeds — even if remote flag or `/voice` mode flipped mid-recording.
        app.voice_stop_keeping_final();
        return vec![];
    }
    // Not recording: start. Mirrors `/voice` so the banner surfaces with one
    // keypress (enables voice mode + spawns the pipeline if it isn't up yet).
    // Not a hold, so a stray Ctrl+Space release won't cancel a queued cold-start.
    dispatch_enable_voice_mode(app, /* from_hold */ false)
}

/// Ctrl+Space hold-to-talk key release: end the session a Ctrl+Space hold started
/// — cancel a cold-start the hold queued (so a quick tap captures nothing) or
/// stop a live hold recording. A `/voice` / toggle session is left untouched, so
/// a Ctrl+Space release can neither cancel its queued start nor stop its
/// recording.
pub(super) fn dispatch_voice_stop(app: &mut AppView) -> Vec<Effect> {
    app.voice_hold_release();
    vec![]
}
