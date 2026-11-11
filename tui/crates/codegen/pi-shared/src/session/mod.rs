pub mod info;

pub use info::Info;

// Re-export shared feedback wire types used by downstream crates
// (e.g. pi-pager-render).
pub use prod_mc_cli_chat_proxy_types::feedback_types::FeedbackTerminalInfo;
