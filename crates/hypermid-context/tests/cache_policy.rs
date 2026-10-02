use hypermid_context::cache_store::CacheStore;
use hypermid_context::projector::DeterministicProjector;
use hypermid_contracts::{Cursor, Digest, Id, Scope};
use hypermid_core::cache::{
    CacheGeneration, CacheOutcomeKind, CacheTransition, CachedChangeKind, CachedRegion,
    HardBoundaryReason,
};
use hypermid_core::projection::{
    ContextMode, ContextPart, ContextRole, Projection, ProjectionBudgetInputs, ProjectionItem,
    ProjectionRequest, RegionKind,
};

fn region(bytes: &[u8], ids: &[&str], mass: u64) -> CachedRegion {
    CachedRegion {
        digest: Digest::sha256(bytes),
        bytes: bytes.to_vec(),
        item_ids: ids.iter().map(|value| Id::new(*value).unwrap()).collect(),
        summary_ids: Vec::new(),
        token_mass: mass,
    }
}
fn generation(number: u64, baseline: &[u8], delta: &[u8]) -> CacheGeneration {
    CacheGeneration {
        generation: number,
        provider_profile_digest: Digest::sha256(b"provider"),
        policy_revision: 1,
        baseline: region(baseline, &["base-1"], 5),
        delta: region(
            delta,
            if delta.is_empty() { &[] } else { &["delta-1"] },
            if delta.is_empty() { 0 } else { 2 },
        ),
        live_tail: region(b"tail", &["tail-1"], 1),
        boundary_reason: None,
    }
}

fn projected_generation(projection: &Projection) -> CacheGeneration {
    let cached = |region: &hypermid_core::projection::ProjectionRegion| CachedRegion {
        digest: region.digest,
        bytes: region.bytes.clone(),
        item_ids: region.item_ids.clone(),
        summary_ids: region.summary_ids.clone(),
        token_mass: region.token_mass,
    };
    CacheGeneration {
        generation: projection.generation,
        provider_profile_digest: projection.provider_profile_digest,
        policy_revision: projection.policy_revision,
        baseline: cached(&projection.baseline),
        delta: cached(&projection.delta),
        live_tail: cached(&projection.tail),
        boundary_reason: None,
    }
}

fn projection(generation: u64, second_region: RegionKind) -> Projection {
    let item = |number: u64, region: RegionKind| ProjectionItem {
        item_id: Id::new(format!("item-{number}")).unwrap(),
        cursor: Cursor::new(1, number).unwrap(),
        role: ContextRole::User,
        parts: vec![ContextPart::text(
            Id::new(format!("part-{number}")).unwrap(),
            format!("turn {number}"),
        )],
        region,
        token_mass: 2,
    };
    DeterministicProjector
        .project(&ProjectionRequest {
            scope: Scope::new(Id::new("owner").unwrap(), Id::new("project").unwrap(), None),
            session_id: Id::new("session").unwrap(),
            source_cursor: Cursor::new(1, 2).unwrap(),
            source_digest: Digest::sha256(b"source"),
            generation,
            policy_revision: 1,
            mode: ContextMode::Primary,
            provider_profile_digest: Digest::sha256(b"provider"),
            budget_inputs: ProjectionBudgetInputs {
                context_window_tokens: 100,
                reserved_output_tokens: 10,
                max_input_tokens: 90,
                max_items: 10,
                max_images: 0,
            },
            created_at: "2026-10-02T12:00:00Z".into(),
            items: vec![item(1, RegionKind::Baseline), item(2, second_region)],
            summaries: vec![],
        })
        .unwrap()
}

#[test]
fn cache_preserves_prefix_and_requires_boundary_for_cached_changes() {
    let store = CacheStore::default();
    store
        .apply(CacheTransition {
            next: generation(1, b"stable-baseline", b"delta-one"),
            cached_change: CachedChangeKind::None,
            boundary: None,
            overflow_requires_cached_change: false,
        })
        .unwrap();
    let first = store.replay_prefix().unwrap();
    let refreshed = generation(1, b"stable-baseline", b"delta-two");
    store
        .apply(CacheTransition {
            next: refreshed.clone(),
            cached_change: CachedChangeKind::None,
            boundary: None,
            overflow_requires_cached_change: false,
        })
        .unwrap();
    let second = store.replay_prefix().unwrap();
    assert_eq!(first.0, second.0);
    assert_ne!(first.1, second.1);
    let refused = store
        .apply(CacheTransition {
            next: refreshed,
            cached_change: CachedChangeKind::TierDecay,
            boundary: None,
            overflow_requires_cached_change: true,
        })
        .unwrap();
    assert_eq!(refused.kind, CacheOutcomeKind::PressureRefused);
    let folded = generation(2, b"stable-baseline+delta-two", b"");
    let outcome = store
        .apply(CacheTransition {
            next: folded,
            cached_change: CachedChangeKind::TierDecay,
            boundary: Some(HardBoundaryReason::TailPressure),
            overflow_requires_cached_change: true,
        })
        .unwrap();
    assert_eq!(outcome.kind, CacheOutcomeKind::Applied);
    assert_eq!(outcome.generation, 2);
}

#[test]
fn cache_fold_accepts_actual_projector_canonical_empty_delta() {
    let store = CacheStore::default();
    let initial = projection(1, RegionKind::Delta);
    store
        .apply(CacheTransition {
            next: projected_generation(&initial),
            cached_change: CachedChangeKind::None,
            boundary: None,
            overflow_requires_cached_change: false,
        })
        .unwrap();

    let folded = projection(2, RegionKind::Baseline);
    assert_eq!(folded.delta.bytes, b"[]");
    assert_eq!(folded.delta.digest, Digest::sha256([]));
    let mut corrupted = projected_generation(&folded);
    corrupted.delta.bytes = b"{}".to_vec();
    assert_eq!(
        store
            .apply(CacheTransition {
                next: corrupted,
                cached_change: CachedChangeKind::None,
                boundary: Some(HardBoundaryReason::ExplicitFlush),
                overflow_requires_cached_change: false,
            })
            .unwrap_err(),
        hypermid_core::cache::CachePolicyError::DeltaNotEmptyAfterFold
    );
    let outcome = store
        .apply(CacheTransition {
            next: projected_generation(&folded),
            cached_change: CachedChangeKind::None,
            boundary: Some(HardBoundaryReason::ExplicitFlush),
            overflow_requires_cached_change: false,
        })
        .unwrap();
    assert_eq!(outcome.kind, CacheOutcomeKind::Applied);
    assert_eq!(outcome.reason_code, "baseline_folded");
}
