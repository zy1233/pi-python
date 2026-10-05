//! Transcript export, block copying, viewer/modal, and input-log dump dispatchers.

use super::ctx::with_active_agent;
use crate::app::actions::Effect;
use crate::app::agent::AgentId;
use crate::app::app_view::{ActiveView, AppView};
use crate::scrollback::block::{BlockContent, RenderBlock};
use crate::scrollback::blocks::ToolCallBlock;

/// Copy the selected block's content to the system clipboard.
///
/// Respects the block's raw/pretty mode for markdown content.
/// Shows a toast notification on theExtensionsTab
pub(super) fn dispatch_copy_block_content(app: &mut AppView) {
    with_active_agent(app, |agent| {
        let Some(idx) = agent.scrollback.selected() else {
            return;
        };
        if agent.scrollback.entry_content_hidden_by_group(idx) {
            return;
        }
        let Some(entry) = agent.scrollback.entry(idx) else {
            return;
        };

        let text = entry.block.copy_text(entry.raw);

        if let Some(text) = text
            && !text.is_empty()
        {
            agent.copy_to_clipboard(&text);
        }
    });
}

/// Open the full transcript in `$PAGER`.
///
/// **Minimal mode** renders a full-fidelity ANSI transcript — every block
/// fully expanded (reasoning in full, tool output uncapped, diff colors kept)
/// — a full layout + syntax-highlight + ANSI-serialization pass over the whole
/// session. Rendering that inline froze the event loop for seconds on long
/// sessions ("laggy /transcript"), and the block model is `!Send` (syntect's
/// resumable highlighter state lives inside markdown blocks), so it can't be
/// shipped to a worker either. Instead this only ARMS the request; the minimal
/// render loop builds the transcript **incrementally, a time-budgeted slice
/// per frame** (`full_view::pump_transcript`, the same time-sliced amortization
/// pattern other TUIs use for heavy transcript work), then arms `pending_pager_path`
/// for the event loop's suspend-into-`$PAGER`.
///
/// **Other modes** keep the compact markdown export (string concatenation, no
/// layout or highlighting — cheap enough to stay synchronous).
pub(crate) fn dispatch_open_transcript_pager(app: &mut AppView) {
    if app.screen_mode.is_minimal() {
        crate::minimal_api::request_minimal_transcript(app);
        return;
    }

    let mut md = None;
    with_active_agent(app, |agent| {
        let blocks: Vec<_> = (0..agent.scrollback.len())
            .filter_map(|i| agent.scrollback.entry(i).map(|e| &e.block))
            .collect();
        let rendered = crate::scrollback::export::render_blocks_to_markdown(blocks);
        if !rendered.is_empty() {
            md = Some(rendered);
        }
    });

    let Some(content) = md else {
        with_active_agent(app, |agent| {
            agent.scrollback.push_block(RenderBlock::system(
                "No conversation transcript to view yet",
            ));
        });
        return;
    };

    let path = std::env::temp_dir().join(format!("grok-transcript-{}.md", uuid::Uuid::new_v4()));
    match std::fs::write(&path, content) {
        Ok(()) => {
            app.pending_pager_path = Some(path);
            app.pending_pager_ansi = false;
        }
        Err(e) => {
            with_active_agent(app, |agent| {
                agent.scrollback.push_block(RenderBlock::system(format!(
                    "Failed to write transcript: {e}"
                )));
            });
        }
    }
}

/// Open the fullscreen block viewer for the selected entry.
/// Falls back to the image viewer only for entries without a normal block viewer.
pub(super) fn dispatch_open_block_viewer(app: &mut AppView) {
    use crate::views::block_viewer::BlockViewerPane;

    with_active_agent(app, |agent| {
        let Some(idx) = agent.scrollback.selected() else {
            return;
        };
        let Some(entry) = agent.scrollback.entry(idx) else {
            return;
        };

        // Block has images/media but terminal can't render pixels — toast and bail.
        let has_media =
            !entry.block.image_references().is_empty() || entry.block.inline_media().is_some();
        if has_media && !crate::terminal::image::detect_graphics_protocol().supports_images() {
            agent.guard_image_support();
            return;
        }

        if !entry.block.has_normal_fullscreen_viewer() {
            // Video: Enter starts inline playback (no modal).
            if let Some(first_ref) = entry.block.video_references().first() {
                let path = first_ref.path.clone();
                agent.start_inline_video_playback(&path);
                return;
            }
            // Image: Enter opens the file in the OS-native viewer.
            if let Some(first_ref) = entry.block.image_references().first() {
                let path = first_ref.path.clone();
                agent.open_media_natively(&path);
            }
            return;
        }

        // Try to create a normal viewer for the selected block type.
        let viewer = match &entry.block {
            RenderBlock::Thinking(_) | RenderBlock::AgentMessage(_) => {
                BlockViewerPane::for_markdown(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::Execute(_)) => {
                BlockViewerPane::for_execute(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::Edit(_)) => {
                BlockViewerPane::for_edit(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::Read(_)) => {
                BlockViewerPane::for_read(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::Search(_)) => {
                BlockViewerPane::for_grep(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::ListDir(_)) => {
                BlockViewerPane::for_list_dir(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::WebFetch(_)) => {
                BlockViewerPane::for_web_fetch(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::WebSearch(_)) => {
                BlockViewerPane::for_web_search(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::IntegrationSearch(_)) => {
                BlockViewerPane::for_integration_search(entry.id, entry)
            }
            RenderBlock::ToolCall(ToolCallBlock::UseTool(_)) => {
                BlockViewerPane::for_use_tool(entry.id, entry)
            }
            _ => None,
        };

        if viewer.is_some() {
            agent.block_viewer = viewer;
            return;
        }

        // Video: Enter starts inline playback.
        if let Some(first_ref) = entry.block.video_references().first() {
            let path = first_ref.path.clone();
            agent.start_inline_video_playback(&path);
            return;
        }
        // Image: Enter opens the file in the OS-native viewer.
        if let Some(first_ref) = entry.block.image_references().first() {
            let path = first_ref.path.clone();
            agent.open_media_natively(&path);
        }
    });
}

/// Copy the selected block's metadata (e.g., command) to clipboard.
pub(super) fn dispatch_copy_block_meta(app: &mut AppView) {
    with_active_agent(app, |agent| {
        let Some(idx) = agent.scrollback.selected() else {
            return;
        };
        if agent.scrollback.entry_content_hidden_by_group(idx) {
            return;
        }
        let Some(entry) = agent.scrollback.entry(idx) else {
            return;
        };
        if let Some(text) = entry.block.copy_meta()
            && !text.is_empty()
        {
            agent.copy_to_clipboard(&text);
        }
    });
}

/// Dump the input flight recorder to a JSON file for debugging.
/// See `input_log.rs` module docs for lifecycle/removal instructions.
pub(super) fn dispatch_dump_input_log(app: &mut AppView) -> Vec<Effect> {
    let ActiveView::Agent(id) = app.active_view else {
        return vec![];
    };
    let Some(agent) = app.agents.get_mut(&id) else {
        return vec![];
    };

    if agent.input_log.entry_count() == 0 {
        agent.show_toast("No input events recorded yet.");
        return vec![];
    }

    let time_span_ms = agent.input_log.time_span_ms();
    let entries = agent.input_log.snapshot_entries();
    let entry_count = entries.len();
    let terminal = crate::terminal::terminal_context().telemetry_snapshot();
    let session_id = agent.session.session_id.as_ref().map(|s| s.0.to_string());
    let pager_version = crate::client_identity::PAGER_CLIENT_VERSION;

    let now = chrono::Utc::now();
    let dump = crate::input_log::InputDump {
        dumped_at: now.to_rfc3339(),
        session_id: session_id.clone(),
        pager_version,
        terminal,
        active_pane: format!("{:?}", agent.active_pane),
        textarea_cursor: agent.prompt.cursor(),
        textarea_text_len: agent.prompt.text().len(),
        textarea_has_selection: agent.prompt.textarea.selection_range().is_some(),
        entry_count,
        time_span_ms,
        entries,
    };

    let json = match serde_json::to_string_pretty(&dump) {
        Ok(j) => j,
        Err(e) => {
            agent.show_toast(&format!("Failed to serialize input log: {e}"));
            return vec![];
        }
    };

    let grok_home = pi_tools::util::grok_home::grok_home();
    let logs_dir = grok_home.join("logs");
    let _ = std::fs::create_dir_all(&logs_dir);
    let ts = now.format("%Y%m%d-%H%M%S");
    let path = logs_dir.join(format!("input-debug-{ts}.json"));

    match std::fs::write(&path, json) {
        Ok(()) => {
            let display_path = path.display();
            agent.show_toast(&format!(
                "Input log ({entry_count} events) → {display_path}"
            ));
            crate::unified_log::info(
                &format!("input debug dump: {entry_count} events, {time_span_ms}ms span"),
                session_id.as_deref(),
                None,
            );
        }
        Err(e) => {
            agent.show_toast(&format!("Failed to write input log: {e}"));
        }
    }
    vec![]
}

// TaskResult handlers.

pub(super) fn handle_marketplace_updates_available(
    app: &mut AppView,
    agent_id: AgentId,
    // (name, installed_ver, latest_ver)
    updates: Vec<(String, String, String)>,
) -> Vec<Effect> {
    if !updates.is_empty()
        && let Some(agent) = app.agents.get_mut(&agent_id)
    {
        let names: Vec<String> = updates
            .iter()
            .map(|(name, old, new)| format!("{name} (v{old} \u{2192} v{new})"))
            .collect();
        let summary = if names.len() <= 2 {
            names.join(", ")
        } else {
            format!("{} and {} more", names[..2].join(", "), names.len() - 2)
        };
        agent
            .scrollback
            .push_block(crate::scrollback::block::RenderBlock::system(format!(
                "{} Plugins auto-updated: {summary}.",
                crate::glyphs::diamond_filled()
            )));
    }
    vec![]
}

