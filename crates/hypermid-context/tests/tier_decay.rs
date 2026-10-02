use hypermid_context::tier_selector::{TierSelector, TierSelectorError};
use hypermid_contracts::{Digest, Id};
use hypermid_core::budget::{HistoryBudget, ProtectedReservations};
use hypermid_core::decay::{HistoricalSpan, TierSelectionRequest};
use hypermid_core::summary::SummaryTier;

fn span(
    id: &str,
    masses: [u64; 4],
    age: u64,
    importance: u16,
    protected: bool,
    recurrence: u32,
    reach: u32,
) -> HistoricalSpan {
    HistoricalSpan {
        summary_id: Id::new(id).unwrap(),
        tiers: std::array::from_fn(|level| {
            let content = format!("{id}-tier-{level}");
            SummaryTier {
                level: level as u8,
                content_digest: Digest::sha256(content.as_bytes()),
                content,
                token_mass: masses[level],
            }
        }),
        age_rank: age,
        importance_basis_points: importance,
        protected,
        recurrence,
        dependency_reach: reach,
    }
}

#[test]
fn tiers_reserve_protected_work_and_remain_fixed_for_generation() {
    let selector = TierSelector::default();
    let request = TierSelectionRequest {
        generation: 4,
        budget: HistoryBudget {
            context_window: 100,
            reservations: ProtectedReservations {
                provider_output: 20,
                live_tail: 20,
                active_tool_pairs: 5,
                latest_user_request: 5,
                unresolved_approvals: 5,
                protected_window: 5,
            },
        },
        spans: vec![
            span("protected", [18, 12, 8, 4], 10, 100, true, 0, 0),
            span("important", [16, 10, 6, 3], 2, 9000, false, 3, 4),
            span("old", [14, 9, 5, 2], 20, 100, false, 0, 0),
        ],
    };
    let first = selector.select(&request).unwrap();
    let second = selector.select(&request).unwrap();
    assert_eq!(first, second);
    assert_eq!(first.available_history, 40);
    assert_eq!(first.decisions[0].level, 0);
    assert!(first.selected_mass <= first.available_history);
    let mut changed = request.clone();
    changed.spans[2].importance_basis_points = 10_000;
    assert_eq!(
        selector.select(&changed),
        Err(TierSelectorError::GenerationLocked)
    );
}
