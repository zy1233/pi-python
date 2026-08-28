//! User-facing product name for the pi-python TUI fork (replaces upstream "grok").

/// CLI binary / `Usage:` name (`zypi`, `zypi --help`, …).
pub const CLI_NAME: &str = "zypi";

/// Short product title on the welcome screen and version badge.
pub const PRODUCT_TITLE: &str = "zypi";

/// Startup banner line (stderr before interactive TUI).
pub const VERSION_BANNER: &str = "zypi coding agent";

/// Hint for config/data home directory in help text.
pub const CONFIG_HOME_HINT: &str = "~/.pi-python";

/// Clap `about` string.
pub const ABOUT: &str = "zypi coding agent TUI";
