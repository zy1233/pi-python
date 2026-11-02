//! Wire types of the pi extensions to ACP (the session-update notifications and the prompt
//! `_meta`). The pager decodes them; the types do not depend on any agent runtime.

mod feedback;
mod image_normalize;
pub mod notification;
pub mod pending_interaction;
pub mod prompt_meta;
