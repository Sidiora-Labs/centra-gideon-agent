use hypermid_context::diagnostic_store::DiagnosticStore;
use hypermid_contracts::{Cursor, Digest, Id, Scope, Trace};
use hypermid_core::diagnostics::{
    BudgetConfidence, ContextDiagnostics, ContextMode, DiagnosticFailure, DiagnosticOutcome,
    EngineDisposition, ModelBudgetStatus, OverflowPolicy, PressureBand, ProjectionRegionStatus,
    RefusalPolicy, RegionKind, RenderMode, UsageAccounting,
};
use std::collections::BTreeMap;

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn diagnostics(reason: &str) -> ContextDiagnostics {
    let empty = Digest::sha256([]);
    ContextDiagnostics {
        scope: Scope::new(id("owner-1"), id("project-1"), None),
        session_id: id("session-1"),
        cursor: Cursor::new(1, 9).unwrap(),
        generation: 3,
        mode: ContextMode::Primary,
        render_mode: RenderMode::HostSerialized,
        overflow_policy: OverflowPolicy::ReclaimThenRefuse,
        refusal_policy: RefusalPolicy::CompatibleLastKnownGood,
        policy_revision: 2,
        provider_profile_digest: Digest::sha256(b"provider-profile"),
        journal_items: 9,
        journal_bytes: 4_096,
        model_budget: ModelBudgetStatus {
            context_window_tokens: 16_000,
            reserved_output_tokens: 2_000,
            max_input_tokens: 14_000,
            max_items: 1_000,
            max_images: 8,
            baseline_tokens: 4_000,
            delta_tokens: 1_000,
            tail_tokens: 2_000,
            confidence: BudgetConfidence::Measured,
        },
        pressure_band: PressureBand::Normal,
        baseline: ProjectionRegionStatus {
            kind: RegionKind::Baseline,
            digest: empty,
            item_ids: vec![id("item-1")],
            summary_ids: vec![],
            token_mass: 4_000,
        },
        delta: ProjectionRegionStatus {
            kind: RegionKind::Delta,
            digest: empty,
            item_ids: vec![id("item-2")],
            summary_ids: vec![],
            token_mass: 1_000,
        },
        tail: ProjectionRegionStatus {
            kind: RegionKind::Tail,
            digest: empty,
            item_ids: vec![id("item-3")],
            summary_ids: vec![],
            token_mass: 2_000,
        },
        protected_item_ids: vec![id("item-3")],
        selected_tiers: BTreeMap::from([(id("summary-1"), 2)]),
        pending_reductions: 1,
        summary_jobs: 1,
        writer_lease: None,
        last_known_good_eligible: true,
        last_reason_code: reason.to_owned(),
        foreground_usage: UsageAccounting::reported(100, 20, 30, 10).unwrap(),
        summary_usage: UsageAccounting::reported_zero(),
        subagent_usage: UsageAccounting::no_call(),
        recent_outcomes: vec![DiagnosticOutcome::redacted(
            "2026-10-02T13:00:00Z",
            reason,
            Trace::new(id("trace-1"), id("request-1")),
            "projection committed",
        )
        .unwrap()],
        generated_at: "2026-10-02T13:00:00Z".to_owned(),
    }
}

#[test]
fn diagnostics_preserve_last_good_and_distinguish_missing_zero_and_parked_failure() {
    let store = DiagnosticStore::new(2, 1).unwrap();
    let good = diagnostics("projection_committed");
    store.record_good(10, good.clone()).unwrap();

    let failure = DiagnosticFailure::redacted(
        "engine_timeout",
        "Authorization: Bearer reusable-secret",
        true,
        20,
    )
    .unwrap();
    let failed = store.record_failure(20, failure, true).unwrap();

    assert_eq!(failed.disposition, EngineDisposition::Parked);
    assert!(failed.value.is_none());
    assert_eq!(failed.last_good, Some(good));
    assert_eq!(
        failed.current_error.unwrap().message,
        "diagnostic detail redacted"
    );
    assert_ne!(UsageAccounting::missing(), UsageAccounting::reported_zero());
    assert_eq!(store.retained().unwrap().len(), 2);
}
