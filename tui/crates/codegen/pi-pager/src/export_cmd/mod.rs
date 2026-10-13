//! `zypi export <session-id>`: one session's conversation as Markdown.
//!
//! The Python agent owns the session files, and this program does not read them. So the command
//! asks the agent, as a resume does: it starts the agent, sends `session/load`, and reads the
//! `session/update` notifications that the agent replays (see [`replay`]). The updates go through
//! the tracker that the screen uses, and the result goes through the renderer of `/export`, so
//! the text is the text that a resumed session shows.

mod replay;
#[cfg(test)]
mod tests;

use std::io::Write;
use std::path::PathBuf;

use agent_client_protocol as acp;
use anyhow::{Context, Result, bail};

use crate::acp::meta::NotificationMeta;
use crate::acp::tracker::AcpUpdateTracker;
use crate::scrollback::export::render_blocks_to_markdown;
use crate::scrollback::state::ScrollbackState;

#[derive(Debug, clap::Args, Clone)]
pub struct ExportArgs {
    /// Session ID to export
    pub session_id: String,
    /// Output file path (default: stdout)
    pub output: Option<PathBuf>,
    /// Copy to clipboard instead of writing to stdout
    #[arg(long, short)]
    pub clipboard: bool,
}

pub async fn run(args: ExportArgs) -> Result<()> {
    tracing::info!(session_id = %args.session_id, "export_cmd: starting session export");

    let md = transcript(&args.session_id).await?;

    if let Some(path) = args.output {
        let expanded = PathBuf::from(shellexpand::tilde(&path.to_string_lossy()).as_ref());
        if let Some(parent) = expanded.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("Failed to create {}", parent.display()))?;
        }
        std::fs::write(&expanded, &md)
            .with_context(|| format!("Failed to write {}", expanded.display()))?;
        tracing::info!(
            session_id = %args.session_id,
            path = %expanded.display(),
            bytes = md.len(),
            "export_cmd: wrote transcript to file"
        );
        eprintln!("Conversation exported to {}", expanded.display());
    } else if args.clipboard {
        let _ = crate::clipboard::copy_text(&md);
        let lines = md.lines().count();
        tracing::info!(
            session_id = %args.session_id,
            bytes = md.len(),
            lines,
            "export_cmd: copied transcript to clipboard"
        );
        eprintln!(
            "Conversation copied to clipboard ({} chars, {} lines)",
            md.len(),
            lines
        );
    } else {
        std::io::stdout().write_all(md.as_bytes())?;
        std::io::stdout().write_all(b"\n")?;
    }

    Ok(())
}

/// The Markdown of one stored session. It starts an agent of its own for the time that the
/// replay takes and stops it before it returns.
///
/// Fails when the session does not exist, when the agent cannot start or stops, and when the
/// session has no conversation in it.
pub async fn transcript(session_id: &str) -> Result<String> {
    let updates = replay::session_updates(session_id).await?;
    let md = render_markdown(updates);
    if md.is_empty() {
        bail!("Session '{session_id}' has no conversation content to export");
    }
    Ok(md)
}

/// What the screen would show for these replayed updates, written as `/export` writes it.
fn render_markdown(updates: Vec<acp::SessionUpdate>) -> String {
    let mut tracker = AcpUpdateTracker::new();
    let mut scrollback = ScrollbackState::new();
    let replay_meta = NotificationMeta {
        is_replay: true,
        ..Default::default()
    };

    for update in updates {
        tracker.handle_update(update, &replay_meta, &mut scrollback);
    }

    let blocks: Vec<_> = (0..scrollback.len())
        .filter_map(|i| scrollback.entry(i).map(|e| &e.block))
        .collect();
    render_blocks_to_markdown(blocks)
}
