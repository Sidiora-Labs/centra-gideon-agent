pub mod bridge;
pub mod dead_letter;
pub mod effects;
pub mod events;
pub mod journal;
pub mod remote_effects;

pub use effects::{
    AuthoritativeEffectOutcome, BeginEffect, BeginOutcome, DurableEffectLedger, EffectRecord,
    EffectReviewPlan, EffectStatus, ProvenEffectOutcome, ProviderEffectProof,
    ReconciliationOutcome,
};
pub use events::{Delivery, DurableEventBus, EventBusError, EventDraft, EventRecord, Snapshot};
pub use remote_effects::{PhoneChannelError, PhoneEffectChannel, PushWake};

#[derive(Debug, thiserror::Error)]
pub enum BusError {
    #[error(transparent)]
    Journal(#[from] journal::JournalError),
    #[error("effect id was reused with different intent")]
    DivergentEffect,
    #[error("effect transition from {from} to {to} is invalid")]
    InvalidEffectTransition {
        from: &'static str,
        to: &'static str,
    },
    #[error("unknown effect id")]
    UnknownEffect,
    #[error("effect is not awaiting review")]
    EffectNotUnknown,
    #[error("unknown effects require authoritative provider proof")]
    ProviderProofRequired,
    #[error("provider identity does not match the effect module")]
    ProviderIdentityMismatch,
    #[error("provider proof is unavailable")]
    ProviderProofMissing,
    #[error("multiple authoritative provider proofs require provider-side resolution")]
    ProviderProofAmbiguous,
    #[error("provider proof does not match the exact effect identity")]
    ProviderProofMismatch,
    #[error("provider proof id was reused with different evidence")]
    DivergentProviderProof,
    #[error("review id was reused with another plan")]
    DivergentReview,
    #[error("review plan is unavailable")]
    ReviewMissing,
    #[error("review plan does not match the exact effect and provider proof")]
    ReviewMismatch,
}
