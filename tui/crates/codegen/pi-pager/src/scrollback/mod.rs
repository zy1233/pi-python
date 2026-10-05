//! Scrollback — conversation display with blocks, scroll, selection, turns.
//!
//! This module owns the scrollback rendering pipeline:
//! - `block.rs` / `blocks/` — content block types (agent, thinking, tool, etc.)
//! - `entry.rs` — ScrollbackEntry wraps a block with display state
//! - `state.rs` — ScrollbackState manages entries, scroll, selection, turns
//! - `layout.rs` — HorizontalLayout for entry column structure
//! - `sticky.rs` — Sticky header computation for turn prompts
//! - `selection.rs` — SelectionBox rendering
//! - `render.rs` — Scroll-aware rendering with scratch buffers
//! - `types.rs` — Core types (BlockLine, BlockOutput, DisplayMode, etc.)
//! - `wrappers/` — Rendering composition (EntryRenderer, BlockRenderer, etc.)

pub mod block;
pub mod blocks;
pub mod entry;
pub(crate) mod export;
pub(crate) mod layout;
pub(crate) mod link_map;
pub mod render;
pub(crate) mod scrollback_pane;
pub(crate) mod search;
pub(crate) mod selection;
pub mod state;
pub(crate) mod sticky;
pub(crate) mod table_geometry;
pub mod text_selection;
pub mod types;
pub mod wrappers;

// Re-exports for convenience
pub use block::{BlockContent, RenderBlock};
pub use blocks::{
    AgentMessageBlock, SystemMessageBlock, ThinkingBlock, ToolCallBlock, UserPromptBlock,
};
pub use entry::{EntryId, ScrollbackEntry};
pub use layout::HorizontalLayout;
pub use link_map::{VisibleLink, VisibleLinkMap};
pub use render::ScratchBuffer;
pub use scrollback_pane::ScrollbackPane;
pub use search::{ScrollbackMatch, ScrollbackSearchIndex, ScrollbackSearchState};
pub use selection::{RenderOutput, SelectionBox};
pub use state::{EntryLayoutInfo, ScrollbackState};
pub use text_selection::*;
pub use types::*;
