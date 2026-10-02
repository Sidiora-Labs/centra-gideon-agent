use hypermid_context::config::{ContextConfig, StageOutcome, TurnBoundaryConfig};
use hypermid_core::mode::{ContextMode, FeatureFlags, OverflowPolicy, RefusalPolicy};

fn enabled_features() -> FeatureFlags {
    FeatureFlags {
        background_summaries: true,
        reduction_tools: true,
        automatic_reclaim: true,
        nudges: true,
        subagent_contributions: true,
        synthetic_hook_blocks: true,
    }
}

#[test]
fn modes_enforce_effect_boundaries_independently_from_strictness() {
    let shadow = ContextMode::Shadow.capabilities();
    assert!(shadow.computes_projection);
    assert!(shadow.preserves_host_request_bytes);
    assert!(!shadow.allows_model_calls);
    assert!(!shadow.allows_durable_writes);
    assert!(!shadow.allows_publication);
    assert!(!shadow.allows_serving_cursor_advance);

    let primary = ContextMode::Primary.capabilities();
    assert!(primary.allows_durable_writes);
    assert!(primary.requires_writer_lease);

    let config = ContextConfig {
        mode: ContextMode::Shadow,
        overflow_policy: OverflowPolicy::RefuseImmediately,
        refusal_policy: RefusalPolicy::CompatibleLastKnownGood,
        features: enabled_features(),
    };
    assert_eq!(config.effective_features(), FeatureFlags::disabled());
    assert_eq!(
        serde_json::to_string(&config).unwrap(),
        r#"{"mode":"shadow","overflow_policy":"refuse_immediately","refusal_policy":"compatible_last_known_good","features":{"background_summaries":true,"reduction_tools":true,"automatic_reclaim":true,"nudges":true,"subagent_contributions":true,"synthetic_hook_blocks":true}}"#
    );
}

#[test]
fn changes_activate_only_at_a_turn_boundary_and_preserve_both_digests() {
    let mut state = TurnBoundaryConfig::default();
    let original = state.active().clone();
    let next = ContextConfig {
        mode: ContextMode::Primary,
        overflow_policy: OverflowPolicy::RefuseImmediately,
        refusal_policy: RefusalPolicy::Refuse,
        features: enabled_features(),
    };

    let staged = state.stage(original.config_digest, next).unwrap();
    assert!(matches!(staged, StageOutcome::Pending(_)));
    assert_eq!(state.active(), &original);
    let pending = state.pending().unwrap();
    assert_eq!(pending.previous_config_digest, original.config_digest);
    assert_ne!(pending.next_config_digest, original.config_digest);
    let next_digest = pending.next_config_digest;

    let transition = state.apply_at_turn_boundary().unwrap().unwrap();
    assert_eq!(transition.previous, original);
    assert_eq!(transition.next.policy_revision, 2);
    assert_eq!(transition.next.config, next);
    assert_eq!(transition.next.config_digest, next_digest);
}

#[test]
fn stale_or_duplicate_updates_do_not_change_the_active_revision() {
    let mut state = TurnBoundaryConfig::default();
    let active = state.active().clone();
    assert!(matches!(
        state.stage(active.config_digest, active.config).unwrap(),
        StageOutcome::Unchanged(_)
    ));
    assert!(state.apply_at_turn_boundary().unwrap().is_none());
    assert_eq!(state.active().policy_revision, 1);

    let wrong_digest = hypermid_contracts::Digest::sha256(b"stale");
    assert!(state.stage(wrong_digest, active.config).is_err());
    assert_eq!(state.active(), &active);
}

#[test]
fn configuration_digest_uses_the_cross_language_canonical_shape() {
    let config = ContextConfig::default();
    assert_eq!(
        String::from_utf8(config.canonical_bytes().unwrap()).unwrap(),
        r#"{"features":{"automatic_reclaim":false,"background_summaries":false,"nudges":false,"reduction_tools":false,"subagent_contributions":false,"synthetic_hook_blocks":false},"mode":"off","overflow_policy":"reclaim_then_refuse","refusal_policy":"refuse"}"#
    );
}
