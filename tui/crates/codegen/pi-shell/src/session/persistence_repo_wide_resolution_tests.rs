use super::*;

#[test]
fn resolution_kind_serde_round_trip() {
    let kinds = [
        LocalSessionResolutionKind::ExactCwd,
        LocalSessionResolutionKind::RestoredChildInExactCwd,
        LocalSessionResolutionKind::SameRepoDifferentCwd,
        LocalSessionResolutionKind::RestoredChildInSameRepoDifferentCwd,
    ];
    for kind in &kinds {
        let json = serde_json::to_string(kind).unwrap();
        let deser: LocalSessionResolutionKind = serde_json::from_str(&json).unwrap();
        assert_eq!(*kind, deser);
    }
}

