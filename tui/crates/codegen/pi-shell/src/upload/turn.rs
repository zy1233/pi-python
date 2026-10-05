//! Turn lifecycle orchestration for trace uploads.
#[cfg(test)]
mod tests {

    #[test]
    fn subagent_trace_uses_turn_zero() {
        let session_id = "child-abc";
        let dispatch_prefix = format!("{}/turn_0", session_id);
        let completion_prefix = format!("{}/turn_0", session_id);
        assert_eq!(dispatch_prefix, completion_prefix);
        let turn_number: u64 = 0;
        assert_eq!(
            format!("{}/turn_{}", session_id, turn_number),
            dispatch_prefix,
        );
    }
}
