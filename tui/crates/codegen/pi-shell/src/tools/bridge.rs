//! ToolBridge: re-exported from `pi-tools`.
//!
//! The bridge implementation now lives in `pi_tools::bridge`.
//! This module re-exports everything for backward compatibility.

pub use pi_tools::bridge::{ToolBridge, ToolBridgeResult};
