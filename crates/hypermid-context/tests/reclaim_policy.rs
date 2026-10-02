use hypermid_context::reclaimer::{Reclaimer, ReclaimerState};
use hypermid_contracts::{Digest, Id};
use hypermid_core::reclaim::{
    PressureBand, PressureInput, PressurePolicy, ReclaimCandidate, ReclaimClass, ReclaimDisposition,
};
use hypermid_core::reduction::{BoundaryKind, ContextRegion, ReductionBoundary};

fn id(value: &str) -> Id {
    Id::new(value).unwrap()
}

fn boundary() -> ReductionBoundary {
    ReductionBoundary {
        kind: BoundaryKind::HardBoundary,
        reason_code: "tail_pressure".to_owned(),
        cache_generation: 8,
    }
}

fn candidate(
    candidate_id: &str,
    class: ReclaimClass,
    mass: u64,
    region: ContextRegion,
) -> ReclaimCandidate {
    ReclaimCandidate {
        candidate_id: id(candidate_id),
        item_ids: vec![id(&format!("item-{candidate_id}"))],
        reclaim_tags: vec![mass],
        class,
        region,
        recoverable_mass: mass,
        recoverable_bytes: mass * 4,
        age_rank: mass,
        dependency_safe: true,
        call_ids: Vec::new(),
        tool_arcs_complete: true,
        protected_reason_codes: Vec::new(),
        requires_rewrite: false,
        rewrite_cost_nanodollars: None,
    }
}

#[test]
fn reclaim_is_ordered_priced_latched_and_refuses_an_unsafe_hard_wall() {
    let input = PressureInput {
        calibrated_mass: 110,
        safe_input_mass: 100,
        cache_generation: 8,
        projection_digest: Digest::sha256("projection-8"),
        policy_revision: 4,
    };
    let mut open_arc = candidate(
        "open-tool",
        ReclaimClass::ExplicitReduction,
        50,
        ContextRegion::Tail,
    );
    open_arc.call_ids = vec![id("call-open")];
    open_arc.tool_arcs_complete = false;
    open_arc
        .protected_reason_codes
        .push("active_tool_arc".to_owned());
    let mut unknown_price = candidate(
        "rewrite",
        ReclaimClass::HistoricalDetail,
        50,
        ContextRegion::Baseline,
    );
    unknown_price.requires_rewrite = true;
    let candidates = vec![
        unknown_price,
        candidate(
            "superseded",
            ReclaimClass::SupersededEdit,
            5,
            ContextRegion::Delta,
        ),
        open_arc,
        candidate(
            "explicit",
            ReclaimClass::ExplicitReduction,
            6,
            ContextRegion::Tail,
        ),
        candidate(
            "prose",
            ReclaimClass::UnprotectedProse,
            40,
            ContextRegion::Delta,
        ),
    ];
    let mut reclaimer = Reclaimer::new();
    let first = reclaimer
        .decide(&input, &PressurePolicy::default(), boundary(), &candidates)
        .unwrap();
    assert_eq!(first.plan.band, PressureBand::HardWall);
    assert_eq!(first.plan.disposition, ReclaimDisposition::Applied);
    assert_eq!(first.plan.estimated_savings, 11);
    assert_eq!(
        first
            .plan
            .chosen
            .iter()
            .map(|choice| choice.candidate_id.as_str())
            .collect::<Vec<_>>(),
        vec!["explicit", "superseded"]
    );
    assert!(first.plan.exclusions.iter().any(
        |value| value.candidate_id == id("open-tool") && value.reason_code == "active_tool_arc"
    ));
    assert!(first.plan.dispatch_allowed());

    let replay = reclaimer
        .decide(
            &input,
            &PressurePolicy::default(),
            boundary(),
            &candidates.iter().cloned().rev().collect::<Vec<_>>(),
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.plan, first.plan);

    let state_json = serde_json::to_string(&reclaimer.state()).unwrap();
    let restored =
        Reclaimer::from_state(serde_json::from_str::<ReclaimerState>(&state_json).unwrap())
            .unwrap();
    assert_eq!(restored.state(), reclaimer.state());

    let unsafe_only = vec![candidate(
        "too-small",
        ReclaimClass::UnprotectedProse,
        3,
        ContextRegion::Delta,
    )];
    let refusal = Reclaimer::new()
        .decide(&input, &PressurePolicy::default(), boundary(), &unsafe_only)
        .unwrap();
    assert_eq!(refusal.plan.disposition, ReclaimDisposition::Refused);
    assert!(!refusal.plan.dispatch_allowed());

    let rewrite_input = PressureInput {
        calibrated_mass: 95,
        safe_input_mass: 100,
        cache_generation: 9,
        projection_digest: Digest::sha256("projection-9"),
        policy_revision: 4,
    };
    let mut priced = candidate(
        "priced-rewrite",
        ReclaimClass::HistoricalDetail,
        15,
        ContextRegion::Baseline,
    );
    priced.requires_rewrite = true;
    priced.rewrite_cost_nanodollars = Some(100);
    let rejected_price = Reclaimer::new()
        .decide(
            &rewrite_input,
            &PressurePolicy {
                max_rewrite_cost_nanodollars: Some(99),
                ..PressurePolicy::default()
            },
            boundary(),
            &[priced.clone()],
        )
        .unwrap();
    assert!(rejected_price.plan.chosen.is_empty());
    assert_eq!(
        rejected_price.plan.exclusions[0].reason_code,
        "rewrite_budget_exceeded"
    );
    let admitted_price = Reclaimer::new()
        .decide(
            &rewrite_input,
            &PressurePolicy {
                max_rewrite_cost_nanodollars: Some(100),
                ..PressurePolicy::default()
            },
            boundary(),
            &[priced],
        )
        .unwrap();
    assert_eq!(admitted_price.plan.rewrite_cost_nanodollars, 100);
}
