use std::collections::BTreeMap;
use std::path::Path;

use hypermid_contracts::{Digest, Id, Scope};
use hypermid_protocol::Principal;
use serde::{Deserialize, Serialize};

use crate::journal::Journal;
use crate::BusError;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BeginEffect {
    pub effect_id: Id,
    pub module_id: Id,
    pub operation: String,
    pub principal: Principal,
    pub scope: Scope,
    pub input_digest: Digest,
    pub created_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "state")]
pub enum EffectStatus {
    Intent,
    Dispatched,
    Committed {
        result_digest: Digest,
        result: Vec<u8>,
        settled_ms: u64,
    },
    NotStarted {
        reason: String,
        settled_ms: u64,
    },
    Unknown {
        reason: String,
        settled_ms: u64,
    },
}

impl EffectStatus {
    pub fn is_unsettled(&self) -> bool {
        matches!(self, Self::Intent | Self::Dispatched | Self::Unknown { .. })
    }

    fn name(&self) -> &'static str {
        match self {
            Self::Intent => "intent",
            Self::Dispatched => "dispatched",
            Self::Committed { .. } => "committed",
            Self::NotStarted { .. } => "not_started",
            Self::Unknown { .. } => "unknown",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EffectRecord {
    pub intent: BeginEffect,
    pub status: EffectStatus,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BeginOutcome {
    Created(EffectRecord),
    Existing(EffectRecord),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReconciliationOutcome {
    Committed { result: Vec<u8>, settled_ms: u64 },
    NotStarted { reason: String, settled_ms: u64 },
    Unknown { reason: String, settled_ms: u64 },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AuthoritativeEffectOutcome {
    Committed { result: Vec<u8> },
    NotStarted { reason: String },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "state")]
pub enum ProvenEffectOutcome {
    Committed {
        result_digest: Digest,
        result: Vec<u8>,
    },
    NotStarted {
        reason: String,
    },
}

impl ProvenEffectOutcome {
    fn name(&self) -> &'static str {
        match self {
            Self::Committed { .. } => "committed",
            Self::NotStarted { .. } => "not_started",
        }
    }

    fn result_digest(&self) -> Option<&Digest> {
        match self {
            Self::Committed { result_digest, .. } => Some(result_digest),
            Self::NotStarted { .. } => None,
        }
    }

    fn reason(&self) -> Option<&str> {
        match self {
            Self::Committed { .. } => None,
            Self::NotStarted { reason } => Some(reason),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderEffectProof {
    pub proof_id: Id,
    pub effect_id: Id,
    pub idempotency_key: Id,
    pub module_id: Id,
    pub operation: String,
    pub scope: Scope,
    pub input_digest: Digest,
    pub provider_id: Id,
    pub outcome: ProvenEffectOutcome,
    pub observed_ms: u64,
    pub proof_digest: Digest,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EffectReviewPlan {
    pub review_id: Id,
    pub effect_id: Id,
    pub idempotency_key: Id,
    pub module_id: Id,
    pub operation: String,
    pub scope: Scope,
    pub input_digest: Digest,
    pub provider_id: Id,
    pub provider_proof_id: Id,
    pub provider_proof_digest: Digest,
    pub proposed_state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result_digest: Option<Digest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub created_ms: u64,
    pub plan_digest: Digest,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
enum EffectJournalEntry {
    Intent(BeginEffect),
    Status { effect_id: Id, status: EffectStatus },
    ProviderProof(ProviderEffectProof),
    Review(EffectReviewPlan),
}

pub struct DurableEffectLedger {
    journal: Journal<EffectJournalEntry>,
    effects: BTreeMap<Id, EffectRecord>,
    provider_proofs: BTreeMap<Id, ProviderEffectProof>,
    reviews: BTreeMap<Id, EffectReviewPlan>,
}

impl DurableEffectLedger {
    pub fn open(path: impl AsRef<Path>, recovered_at_ms: u64) -> Result<Self, BusError> {
        let journal = Journal::open(path)?;
        let mut effects = BTreeMap::new();
        let mut provider_proofs = BTreeMap::new();
        let mut reviews = BTreeMap::new();
        for (_, entry) in journal.entries() {
            match entry {
                EffectJournalEntry::Intent(intent) => {
                    effects.insert(
                        intent.effect_id.clone(),
                        EffectRecord {
                            intent: intent.clone(),
                            status: EffectStatus::Intent,
                        },
                    );
                }
                EffectJournalEntry::Status { effect_id, status } => {
                    let effect = effects.get_mut(effect_id).ok_or(BusError::UnknownEffect)?;
                    effect.status = status.clone();
                }
                EffectJournalEntry::ProviderProof(proof) => {
                    provider_proofs.insert(proof.proof_id.clone(), proof.clone());
                }
                EffectJournalEntry::Review(review) => {
                    reviews.insert(review.review_id.clone(), review.clone());
                }
            }
        }
        let mut ledger = Self {
            journal,
            effects,
            provider_proofs,
            reviews,
        };
        let recoveries = ledger
            .effects
            .iter()
            .filter_map(|(effect_id, effect)| match effect.status {
                EffectStatus::Intent => Some((
                    effect_id.clone(),
                    EffectStatus::NotStarted {
                        reason: "daemon restarted before dispatch was durably recorded".into(),
                        settled_ms: recovered_at_ms,
                    },
                )),
                EffectStatus::Dispatched => Some((
                    effect_id.clone(),
                    EffectStatus::Unknown {
                        reason: "daemon restarted after dispatch without a terminal result".into(),
                        settled_ms: recovered_at_ms,
                    },
                )),
                _ => None,
            })
            .collect::<Vec<_>>();
        for (effect_id, status) in recoveries {
            ledger.persist_status(effect_id, status)?;
        }
        Ok(ledger)
    }

    pub fn begin(&mut self, intent: BeginEffect) -> Result<BeginOutcome, BusError> {
        if let Some(existing) = self.effects.get(&intent.effect_id) {
            if !same_intent(&existing.intent, &intent) {
                return Err(BusError::DivergentEffect);
            }
            return Ok(BeginOutcome::Existing(existing.clone()));
        }
        self.journal
            .append(EffectJournalEntry::Intent(intent.clone()))?;
        let record = EffectRecord {
            intent: intent.clone(),
            status: EffectStatus::Intent,
        };
        self.effects
            .insert(intent.effect_id.clone(), record.clone());
        Ok(BeginOutcome::Created(record))
    }

    pub fn mark_dispatched(&mut self, effect_id: &Id) -> Result<EffectRecord, BusError> {
        let current = self
            .effects
            .get(effect_id)
            .ok_or(BusError::UnknownEffect)?
            .status
            .clone();
        match current {
            EffectStatus::Intent => {
                self.persist_status(effect_id.clone(), EffectStatus::Dispatched)
            }
            EffectStatus::Dispatched => Ok(self.effects[effect_id].clone()),
            other => Err(BusError::InvalidEffectTransition {
                from: other.name(),
                to: "dispatched",
            }),
        }
    }

    pub fn reconcile(
        &mut self,
        effect_id: &Id,
        outcome: ReconciliationOutcome,
    ) -> Result<EffectRecord, BusError> {
        let current = self
            .effects
            .get(effect_id)
            .ok_or(BusError::UnknownEffect)?
            .status
            .clone();
        let status = match outcome {
            ReconciliationOutcome::Committed { result, settled_ms } => EffectStatus::Committed {
                result_digest: Digest::sha256(&result),
                result,
                settled_ms,
            },
            ReconciliationOutcome::NotStarted { reason, settled_ms } => {
                EffectStatus::NotStarted { reason, settled_ms }
            }
            ReconciliationOutcome::Unknown { reason, settled_ms } => {
                EffectStatus::Unknown { reason, settled_ms }
            }
        };
        match current {
            EffectStatus::Intent | EffectStatus::Dispatched => {
                self.persist_status(effect_id.clone(), status)
            }
            EffectStatus::Unknown { .. } if matches!(status, EffectStatus::Unknown { .. }) => {
                self.persist_status(effect_id.clone(), status)
            }
            EffectStatus::Unknown { .. } => Err(BusError::ProviderProofRequired),
            _ if current == status => Ok(self.effects[effect_id].clone()),
            _ => Err(BusError::InvalidEffectTransition {
                from: current.name(),
                to: status.name(),
            }),
        }
    }

    pub fn status(&self, effect_id: &Id) -> Option<&EffectRecord> {
        self.effects.get(effect_id)
    }

    pub fn status_scoped(&self, effect_id: &Id, scope: &Scope) -> Option<&EffectRecord> {
        self.effects
            .get(effect_id)
            .filter(|record| &record.intent.scope == scope)
    }

    pub fn list_scoped(&self, scope: &Scope) -> Vec<EffectRecord> {
        self.effects
            .values()
            .filter(|record| &record.intent.scope == scope)
            .cloned()
            .collect()
    }

    pub fn record_provider_proof(
        &mut self,
        proof_id: Id,
        effect_id: &Id,
        provider_id: Id,
        outcome: AuthoritativeEffectOutcome,
        observed_ms: u64,
    ) -> Result<ProviderEffectProof, BusError> {
        let effect = self.effects.get(effect_id).ok_or(BusError::UnknownEffect)?;
        if !matches!(effect.status, EffectStatus::Unknown { .. }) {
            return Err(BusError::EffectNotUnknown);
        }
        if provider_id != effect.intent.module_id {
            return Err(BusError::ProviderIdentityMismatch);
        }
        if let Some(existing) = self
            .provider_proofs
            .values()
            .find(|proof| proof.effect_id == *effect_id)
        {
            if existing.proof_id != proof_id {
                return Err(BusError::ProviderProofAmbiguous);
            }
        }
        let outcome = match outcome {
            AuthoritativeEffectOutcome::Committed { result } => ProvenEffectOutcome::Committed {
                result_digest: Digest::sha256(&result),
                result,
            },
            AuthoritativeEffectOutcome::NotStarted { reason } => {
                ProvenEffectOutcome::NotStarted { reason }
            }
        };
        let material = ProviderProofMaterial {
            proof_id: &proof_id,
            effect_id: &effect.intent.effect_id,
            idempotency_key: &effect.intent.effect_id,
            module_id: &effect.intent.module_id,
            operation: &effect.intent.operation,
            scope: &effect.intent.scope,
            input_digest: &effect.intent.input_digest,
            provider_id: &provider_id,
            outcome: &outcome,
            observed_ms,
        };
        let proof_digest = digest_material(b"hypermid.effect.provider-proof.v1\0", &material)?;
        let proof = ProviderEffectProof {
            proof_id: proof_id.clone(),
            effect_id: effect.intent.effect_id.clone(),
            idempotency_key: effect.intent.effect_id.clone(),
            module_id: effect.intent.module_id.clone(),
            operation: effect.intent.operation.clone(),
            scope: effect.intent.scope.clone(),
            input_digest: effect.intent.input_digest.clone(),
            provider_id,
            outcome,
            observed_ms,
            proof_digest,
        };
        if let Some(existing) = self.provider_proofs.get(&proof_id) {
            return if existing == &proof {
                Ok(existing.clone())
            } else {
                Err(BusError::DivergentProviderProof)
            };
        }
        self.journal
            .append(EffectJournalEntry::ProviderProof(proof.clone()))?;
        self.provider_proofs.insert(proof_id, proof.clone());
        Ok(proof)
    }

    pub fn reviewable(&self, effect_id: &Id, scope: &Scope) -> bool {
        let Some(effect) = self.status_scoped(effect_id, scope) else {
            return false;
        };
        matches!(effect.status, EffectStatus::Unknown { .. })
            && self.matching_provider_proofs(effect).count() == 1
            && self.review_for_effect(effect_id, scope).is_none()
    }

    pub fn review_for_effect(&self, effect_id: &Id, scope: &Scope) -> Option<&EffectReviewPlan> {
        self.reviews
            .values()
            .filter(|review| review.effect_id == *effect_id && &review.scope == scope)
            .max_by(|left, right| {
                left.created_ms
                    .cmp(&right.created_ms)
                    .then_with(|| left.review_id.cmp(&right.review_id))
            })
    }

    pub fn review_unknown_current(
        &mut self,
        review_id: Id,
        effect_id: &Id,
        scope: &Scope,
        created_ms: u64,
    ) -> Result<EffectReviewPlan, BusError> {
        let effect = self
            .status_scoped(effect_id, scope)
            .ok_or(BusError::UnknownEffect)?;
        let proof_ids = self
            .matching_provider_proofs(effect)
            .map(|proof| proof.proof_id.clone())
            .collect::<Vec<_>>();
        match proof_ids.as_slice() {
            [] => Err(BusError::ProviderProofMissing),
            [proof_id] => {
                let proof_id = proof_id.clone();
                self.review_unknown(review_id, effect_id, scope, &proof_id, created_ms)
            }
            _ => Err(BusError::ProviderProofAmbiguous),
        }
    }

    pub fn review_unknown(
        &mut self,
        review_id: Id,
        effect_id: &Id,
        scope: &Scope,
        provider_proof_id: &Id,
        created_ms: u64,
    ) -> Result<EffectReviewPlan, BusError> {
        let effect = self
            .status_scoped(effect_id, scope)
            .ok_or(BusError::UnknownEffect)?;
        if !matches!(effect.status, EffectStatus::Unknown { .. }) {
            return Err(BusError::EffectNotUnknown);
        }
        let proof = self
            .provider_proofs
            .get(provider_proof_id)
            .ok_or(BusError::ProviderProofMissing)?;
        require_proof_matches(effect, proof)?;
        if let Some(existing) = self.reviews.get(&review_id) {
            return if existing.effect_id == *effect_id
                && existing.idempotency_key == *effect_id
                && existing.scope == *scope
                && existing.provider_proof_id == *provider_proof_id
                && existing.provider_proof_digest == proof.proof_digest
            {
                Ok(existing.clone())
            } else {
                Err(BusError::DivergentReview)
            };
        }
        let material = ReviewPlanMaterial {
            review_id: &review_id,
            effect_id: &effect.intent.effect_id,
            idempotency_key: &effect.intent.effect_id,
            module_id: &effect.intent.module_id,
            operation: &effect.intent.operation,
            scope: &effect.intent.scope,
            input_digest: &effect.intent.input_digest,
            provider_id: &proof.provider_id,
            provider_proof_id: &proof.proof_id,
            provider_proof_digest: &proof.proof_digest,
            proposed_state: proof.outcome.name(),
            result_digest: proof.outcome.result_digest(),
            reason: proof.outcome.reason(),
            created_ms,
        };
        let plan_digest = digest_material(b"hypermid.effect.review-plan.v1\0", &material)?;
        let plan = EffectReviewPlan {
            review_id: review_id.clone(),
            effect_id: effect.intent.effect_id.clone(),
            idempotency_key: effect.intent.effect_id.clone(),
            module_id: effect.intent.module_id.clone(),
            operation: effect.intent.operation.clone(),
            scope: effect.intent.scope.clone(),
            input_digest: effect.intent.input_digest.clone(),
            provider_id: proof.provider_id.clone(),
            provider_proof_id: proof.proof_id.clone(),
            provider_proof_digest: proof.proof_digest.clone(),
            proposed_state: proof.outcome.name().into(),
            result_digest: proof.outcome.result_digest().cloned(),
            reason: proof.outcome.reason().map(str::to_owned),
            created_ms,
            plan_digest,
        };
        self.journal
            .append(EffectJournalEntry::Review(plan.clone()))?;
        self.reviews.insert(review_id, plan.clone());
        Ok(plan)
    }

    pub fn reconcile_reviewed(
        &mut self,
        effect_id: &Id,
        scope: &Scope,
        review_id: &Id,
        reviewed_plan_digest: &Digest,
    ) -> Result<EffectRecord, BusError> {
        let effect = self
            .status_scoped(effect_id, scope)
            .ok_or(BusError::UnknownEffect)?
            .clone();
        let plan = self.reviews.get(review_id).ok_or(BusError::ReviewMissing)?;
        if plan.effect_id != *effect_id
            || plan.idempotency_key != *effect_id
            || plan.scope != *scope
            || plan.module_id != effect.intent.module_id
            || plan.operation != effect.intent.operation
            || plan.input_digest != effect.intent.input_digest
            || plan.plan_digest != *reviewed_plan_digest
        {
            return Err(BusError::ReviewMismatch);
        }
        let proof = self
            .provider_proofs
            .get(&plan.provider_proof_id)
            .ok_or(BusError::ProviderProofMissing)?;
        require_proof_matches(&effect, proof)?;
        if proof.proof_digest != plan.provider_proof_digest
            || proof.provider_id != plan.provider_id
            || proof.outcome.name() != plan.proposed_state
            || proof.outcome.result_digest() != plan.result_digest.as_ref()
            || proof.outcome.reason() != plan.reason.as_deref()
        {
            return Err(BusError::ReviewMismatch);
        }
        let status = match &proof.outcome {
            ProvenEffectOutcome::Committed {
                result_digest,
                result,
            } => {
                if Digest::sha256(result) != *result_digest {
                    return Err(BusError::ProviderProofMismatch);
                }
                EffectStatus::Committed {
                    result_digest: result_digest.clone(),
                    result: result.clone(),
                    settled_ms: proof.observed_ms,
                }
            }
            ProvenEffectOutcome::NotStarted { reason } => EffectStatus::NotStarted {
                reason: reason.clone(),
                settled_ms: proof.observed_ms,
            },
        };
        match &effect.status {
            EffectStatus::Unknown { .. } => self.persist_status(effect_id.clone(), status),
            current if current == &status => Ok(effect),
            _ => Err(BusError::EffectNotUnknown),
        }
    }

    pub fn unsettled(&self) -> Vec<EffectRecord> {
        self.effects
            .values()
            .filter(|effect| effect.status.is_unsettled())
            .cloned()
            .collect()
    }

    fn persist_status(
        &mut self,
        effect_id: Id,
        status: EffectStatus,
    ) -> Result<EffectRecord, BusError> {
        self.journal.append(EffectJournalEntry::Status {
            effect_id: effect_id.clone(),
            status: status.clone(),
        })?;
        let effect = self
            .effects
            .get_mut(&effect_id)
            .ok_or(BusError::UnknownEffect)?;
        effect.status = status;
        Ok(effect.clone())
    }

    fn matching_provider_proofs<'a>(
        &'a self,
        effect: &'a EffectRecord,
    ) -> impl Iterator<Item = &'a ProviderEffectProof> + 'a {
        self.provider_proofs
            .values()
            .filter(|proof| require_proof_matches(effect, proof).is_ok())
    }
}

#[derive(Serialize)]
struct ProviderProofMaterial<'a> {
    proof_id: &'a Id,
    effect_id: &'a Id,
    idempotency_key: &'a Id,
    module_id: &'a Id,
    operation: &'a str,
    scope: &'a Scope,
    input_digest: &'a Digest,
    provider_id: &'a Id,
    outcome: &'a ProvenEffectOutcome,
    observed_ms: u64,
}

#[derive(Serialize)]
struct ReviewPlanMaterial<'a> {
    review_id: &'a Id,
    effect_id: &'a Id,
    idempotency_key: &'a Id,
    module_id: &'a Id,
    operation: &'a str,
    scope: &'a Scope,
    input_digest: &'a Digest,
    provider_id: &'a Id,
    provider_proof_id: &'a Id,
    provider_proof_digest: &'a Digest,
    proposed_state: &'a str,
    result_digest: Option<&'a Digest>,
    reason: Option<&'a str>,
    created_ms: u64,
}

fn digest_material(prefix: &[u8], value: &impl Serialize) -> Result<Digest, BusError> {
    let encoded = serde_json::to_vec(value).map_err(crate::journal::JournalError::from)?;
    let mut material = Vec::with_capacity(prefix.len() + encoded.len());
    material.extend_from_slice(prefix);
    material.extend_from_slice(&encoded);
    Ok(Digest::sha256(&material))
}

fn require_proof_matches(
    effect: &EffectRecord,
    proof: &ProviderEffectProof,
) -> Result<(), BusError> {
    if proof.effect_id != effect.intent.effect_id
        || proof.idempotency_key != effect.intent.effect_id
        || proof.module_id != effect.intent.module_id
        || proof.operation != effect.intent.operation
        || proof.scope != effect.intent.scope
        || proof.input_digest != effect.intent.input_digest
        || proof.provider_id != effect.intent.module_id
    {
        return Err(BusError::ProviderProofMismatch);
    }
    Ok(())
}

fn same_intent(left: &BeginEffect, right: &BeginEffect) -> bool {
    left.effect_id == right.effect_id
        && left.module_id == right.module_id
        && left.operation == right.operation
        && left.principal == right.principal
        && left.scope == right.scope
        && left.input_digest == right.input_digest
}
