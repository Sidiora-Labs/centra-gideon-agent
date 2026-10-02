use std::path::Path;

use hypermid_contracts::{Digest, Id, Scope};
use serde::{Deserialize, Serialize};

use crate::effects::{
    AuthoritativeEffectOutcome, BeginEffect, BeginOutcome, DurableEffectLedger, EffectRecord,
    EffectReviewPlan, EffectStatus, ProviderEffectProof, ReconciliationOutcome,
};
use crate::BusError;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PushWake {
    wake_token: String,
}

impl PushWake {
    pub fn new(token: impl Into<String>) -> Result<Self, PhoneChannelError> {
        let wake_token = token.into();
        if !(16..=128).contains(&wake_token.len())
            || !wake_token
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
        {
            return Err(PhoneChannelError::InvalidWakeToken);
        }
        Ok(Self { wake_token })
    }

    pub fn token(&self) -> &str {
        &self.wake_token
    }
}

#[derive(Debug, thiserror::Error)]
pub enum PhoneChannelError {
    #[error(transparent)]
    Bus(#[from] BusError),
    #[error("reconnect reconciliation is incomplete")]
    ReconciliationRequired,
    #[error("an uncertain mutation blocks later mutations")]
    UnknownMutation,
    #[error("wake token must be opaque URL-safe data between 16 and 128 bytes")]
    InvalidWakeToken,
}

pub struct PhoneEffectChannel {
    ledger: DurableEffectLedger,
    reconciliation_complete: bool,
}

impl PhoneEffectChannel {
    pub fn open(path: impl AsRef<Path>, recovered_at_ms: u64) -> Result<Self, PhoneChannelError> {
        let ledger = DurableEffectLedger::open(path, recovered_at_ms)?;
        let reconciliation_complete = ledger.unsettled().is_empty();
        Ok(Self {
            ledger,
            reconciliation_complete,
        })
    }

    pub fn begin_reconnect(&mut self) -> Vec<EffectRecord> {
        self.reconciliation_complete = false;
        self.ledger.unsettled()
    }

    pub fn reconcile(
        &mut self,
        effect_id: &Id,
        outcome: ReconciliationOutcome,
    ) -> Result<EffectRecord, PhoneChannelError> {
        let record = self.ledger.reconcile(effect_id, outcome)?;
        self.reconciliation_complete = self.ledger.unsettled().is_empty();
        Ok(record)
    }

    pub fn record_provider_proof(
        &mut self,
        proof_id: Id,
        effect_id: &Id,
        provider_id: Id,
        outcome: AuthoritativeEffectOutcome,
        observed_ms: u64,
    ) -> Result<ProviderEffectProof, PhoneChannelError> {
        Ok(self.ledger.record_provider_proof(
            proof_id,
            effect_id,
            provider_id,
            outcome,
            observed_ms,
        )?)
    }

    pub fn review_unknown(
        &mut self,
        review_id: Id,
        effect_id: &Id,
        scope: &Scope,
        created_ms: u64,
    ) -> Result<EffectReviewPlan, PhoneChannelError> {
        Ok(self
            .ledger
            .review_unknown_current(review_id, effect_id, scope, created_ms)?)
    }

    pub fn reconcile_reviewed(
        &mut self,
        effect_id: &Id,
        scope: &Scope,
        review_id: &Id,
        reviewed_plan_digest: &Digest,
    ) -> Result<EffectRecord, PhoneChannelError> {
        let record =
            self.ledger
                .reconcile_reviewed(effect_id, scope, review_id, reviewed_plan_digest)?;
        self.reconciliation_complete = self.ledger.unsettled().is_empty();
        Ok(record)
    }

    pub fn prepare_mutation(
        &mut self,
        intent: BeginEffect,
    ) -> Result<BeginOutcome, PhoneChannelError> {
        if !self.reconciliation_complete {
            return Err(PhoneChannelError::ReconciliationRequired);
        }
        if self
            .ledger
            .unsettled()
            .iter()
            .any(|effect| matches!(effect.status, EffectStatus::Unknown { .. }))
        {
            return Err(PhoneChannelError::UnknownMutation);
        }
        Ok(self.ledger.begin(intent)?)
    }

    pub fn mark_sent(&mut self, effect_id: &Id) -> Result<EffectRecord, PhoneChannelError> {
        Ok(self.ledger.mark_dispatched(effect_id)?)
    }

    pub fn settle(
        &mut self,
        effect_id: &Id,
        outcome: ReconciliationOutcome,
    ) -> Result<EffectRecord, PhoneChannelError> {
        self.reconcile(effect_id, outcome)
    }

    pub fn query_allowed(&self) -> bool {
        true
    }

    pub fn status(&self, effect_id: &Id) -> Option<&EffectRecord> {
        self.ledger.status(effect_id)
    }
}
