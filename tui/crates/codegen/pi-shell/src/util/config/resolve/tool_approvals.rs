
/// Default for the `remember_tool_approvals` gate when no layer sets it.
/// Shared with the pager settings modal so the displayed default cannot
/// drift from the resolver.
pub const DEFAULT_REMEMBER_TOOL_APPROVALS: bool = true;

#[cfg(test)]
mod remember_tool_approvals_gate_tests {
    
    

    // `GROK_REMEMBER_TOOL_APPROVALS` is process-global; serialize and force it
    // unset at the top of each test so a developer's shell value can't make
    // these flaky.
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

}
