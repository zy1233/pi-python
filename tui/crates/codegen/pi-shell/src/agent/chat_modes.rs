//! grok.com chat-product model catalog: caches `/rest/modes` and maps modes to
//! the `SessionModelState` returned by `load_chat_session` (the chat analogue of
//! [`crate::agent::models::ModelsManager`]). NB: these "modes" populate the
//! desktop MODEL picker, not the ACP session plan-modes in `LoadSessionResponse.modes`.
/// Process-wide flag set by the pager when started with `--chat` so initialize
/// and early UI seed the chat `/rest/modes` catalog instead of build models.
pub const GROK_CHAT_MODE_ENV: &str = "GROK_CHAT_MODE";
/// True when the process is a gateway light-frontend (`--chat`) agent.
/// Hard-off in release builds so it can't be enabled via env.
pub fn process_chat_mode_enabled() -> bool {
    false
}
#[cfg(test)]
mod tests {
    
    }
