use hypermid_contracts::EffectState;
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::api::RecordView;
use crate::model::{
    scope_digest, AccessRequest, Authorization, AuthorizationBasis, MutationRequest, Operation,
    RevisionPrecondition,
};
use crate::provenance::sql_error;
use crate::records::record_in;
use crate::smart_note::SmartPredicate;
use crate::{
    error, AuthContext, Cursor, Digest, GrantOperation, Id, MemoryApi, MemoryResult,
    MemoryTransaction,
};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SharingClassification {
    Private,
    Shared,
}

impl SharingClassification {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Private => "private",
            Self::Shared => "shared",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TrustDecision {
    Allow,
    Deny,
}

impl TrustDecision {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Allow => "allow",
            Self::Deny => "deny",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeSharingJudgment {
    pub record_id: Id,
    pub expected_revision_digest: Digest,
    pub classification: SharingClassification,
    pub trust_decision: TrustDecision,
    pub policy_id: String,
    pub policy_version: u64,
    pub policy_digest: Digest,
    pub provider_id: Option<String>,
    pub model_id: Option<String>,
    pub evidence_digest: Digest,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SharingJudgmentReceipt {
    pub judgment_id: Id,
    pub record_id: Id,
    pub revision_digest: Digest,
    pub classification: SharingClassification,
    pub trust_decision: TrustDecision,
    pub policy_digest: Digest,
    pub decided_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SmartNoteCandidate {
    pub record_id: Id,
    pub revision_digest: Digest,
    pub category: String,
    pub predicate: SmartPredicate,
    pub predicate_digest: Digest,
    pub last_evaluated_cursor: Option<Cursor>,
    pub last_result: Option<bool>,
    pub next_evaluation_at_ms: Option<i64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SmartNoteCandidatePage {
    pub cursor: Cursor,
    pub candidates: Vec<SmartNoteCandidate>,
}

pub(crate) fn publish_sharing_judgment_in(
    transaction: &MemoryTransaction<'_>,
    authorization: &Authorization,
    request: &MutationRequest,
    judgment: &KnowledgeSharingJudgment,
    now_ms: u64,
) -> MemoryResult<SharingJudgmentReceipt> {
    validate_judgment(judgment)?;
    if request.operation != Operation::Verify
        || request.record_id.as_ref() != Some(&judgment.record_id)
        || request.revision != RevisionPrecondition::Match(judgment.expected_revision_digest)
        || authorization.basis != AuthorizationBasis::Owner
    {
        return Err(denied(
            "sharing judgment publication requires exact owner verification authority",
        ));
    }
    let current: Option<(String, String, String)> = transaction
        .raw()
        .query_row(
            "SELECT owner_scope_digest, current_revision_digest, status
             FROM memory_records WHERE record_id=?1",
            [judgment.record_id.as_str()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .optional()
        .map_err(sql_error)?;
    let (owner_scope_digest, revision_digest, status) =
        current.ok_or_else(|| stale("sharing judgment record no longer exists"))?;
    if owner_scope_digest != authorization.target_scope_digest.to_hex()
        || revision_digest != judgment.expected_revision_digest.to_hex()
        || status != "active"
    {
        return Err(stale(
            "sharing judgment record revision, owner, or state changed",
        ));
    }

    let trace_json = serde_json::to_string(&request.trace)
        .map_err(|_| invalid("sharing judgment trace could not be encoded"))?;
    let judgment_id = judgment_id(authorization, judgment, now_ms)?;
    transaction
        .raw()
        .execute(
            "INSERT INTO memory_sharing_judgments(
                 judgment_id, owner_scope_digest, record_id, revision_digest,
                 classification, trust_decision, policy_id, policy_version,
                 policy_digest, provider_id, model_id, evidence_digest,
                 trace_json, decided_at_ms, invalidated_at_ms, invalidation_reason
             ) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,NULL,NULL)",
            params![
                judgment_id.as_str(),
                owner_scope_digest,
                judgment.record_id.as_str(),
                judgment.expected_revision_digest.to_hex(),
                judgment.classification.as_str(),
                judgment.trust_decision.as_str(),
                judgment.policy_id.as_str(),
                judgment.policy_version,
                judgment.policy_digest.to_hex(),
                judgment.provider_id.as_deref(),
                judgment.model_id.as_deref(),
                judgment.evidence_digest.to_hex(),
                trace_json,
                now_ms,
            ],
        )
        .map_err(sql_error)?;
    Ok(SharingJudgmentReceipt {
        judgment_id,
        record_id: judgment.record_id.clone(),
        revision_digest: judgment.expected_revision_digest,
        classification: judgment.classification,
        trust_decision: judgment.trust_decision,
        policy_digest: judgment.policy_digest,
        decided_at_ms: now_ms,
    })
}

impl MemoryApi {
    pub fn smart_note_candidates(
        &mut self,
        context: &AuthContext,
        request: &AccessRequest,
        limit: usize,
    ) -> MemoryResult<SmartNoteCandidatePage> {
        if request.operation != GrantOperation::Read
            || request.resource_id.as_str() != "memory-records"
            || limit == 0
            || limit > 1_000
        {
            return Err(invalid(
                "smart-note candidate access requires the memory-records collection and limit within 1..1000",
            ));
        }
        self.store
            .immediate_access(context, request, |transaction, authorization| {
                let mut statement = transaction
                    .raw()
                    .prepare(
                        "SELECT r.record_id, r.current_revision_digest, r.category,
                                d.predicate_json, d.predicate_digest,
                                d.last_evaluated_cursor_json, d.last_result,
                                d.next_evaluation_at_ms
                         FROM memory_records r
                         JOIN smart_note_details d ON d.record_id=r.record_id
                         WHERE r.owner_scope_digest=?1 AND r.kind='smart_note'
                           AND r.status='active'
                         ORDER BY r.updated_at_ms, r.record_id
                         LIMIT ?2",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(
                        params![authorization.target_scope_digest.to_hex(), limit as i64],
                        |row| {
                            Ok((
                                row.get::<_, String>(0)?,
                                row.get::<_, String>(1)?,
                                row.get::<_, String>(2)?,
                                row.get::<_, String>(3)?,
                                row.get::<_, String>(4)?,
                                row.get::<_, Option<String>>(5)?,
                                row.get::<_, Option<i64>>(6)?,
                                row.get::<_, Option<i64>>(7)?,
                            ))
                        },
                    )
                    .map_err(sql_error)?
                    .collect::<Result<Vec<_>, _>>()
                    .map_err(sql_error)?;
                let mut candidates = Vec::with_capacity(rows.len());
                for row in rows {
                    let predicate: SmartPredicate = serde_json::from_str(&row.3)
                        .map_err(|_| corrupt("stored smart-note predicate is invalid"))?;
                    let predicate_digest: Digest = row
                        .4
                        .parse()
                        .map_err(|_| corrupt("stored smart-note predicate digest is invalid"))?;
                    if predicate
                        .digest()
                        .map_err(|_| corrupt("stored smart-note predicate is invalid"))?
                        != predicate_digest
                    {
                        return Err(corrupt(
                            "stored smart-note predicate digest does not match its predicate",
                        ));
                    }
                    candidates.push(SmartNoteCandidate {
                        record_id: Id::new(row.0)
                            .map_err(|_| corrupt("stored smart-note record id is invalid"))?,
                        revision_digest: row
                            .1
                            .parse()
                            .map_err(|_| corrupt("stored smart-note revision digest is invalid"))?,
                        category: row.2,
                        predicate,
                        predicate_digest,
                        last_evaluated_cursor: row
                            .5
                            .map(|value| serde_json::from_str(&value))
                            .transpose()
                            .map_err(|_| corrupt("stored smart-note cursor is invalid"))?,
                        last_result: row.6.map(|value| value != 0),
                        next_evaluation_at_ms: row.7,
                    });
                }
                Ok(SmartNoteCandidatePage {
                    cursor: transaction.cursor(&request.target_scope)?,
                    candidates,
                })
            })
    }

    pub fn shared_record(
        &mut self,
        context: &AuthContext,
        request: &AccessRequest,
        policy_digest: Digest,
    ) -> MemoryResult<RecordView> {
        if request.operation != GrantOperation::Read {
            return Err(denied("shared record inspection requires read authority"));
        }
        self.store
            .immediate_access(context, request, |transaction, authorization| {
                let record = record_in(transaction.raw(), &request.resource_id)?
                    .ok_or_else(|| denied("shared record is unavailable"))?;
                if record.owner_scope_digest != authorization.target_scope_digest
                    || record.status != crate::records::RecordStatus::Active
                {
                    return Err(denied("shared record is unavailable"));
                }
                let allowed: bool = transaction
                    .raw()
                    .query_row(
                        "SELECT EXISTS(
                            SELECT 1 FROM memory_sharing_judgments
                            WHERE owner_scope_digest=?1 AND record_id=?2
                              AND revision_digest=?3 AND policy_digest=?4
                              AND classification='shared' AND trust_decision='allow'
                              AND invalidated_at_ms IS NULL
                         )",
                        params![
                            scope_digest(&request.target_scope).to_hex(),
                            request.resource_id.as_str(),
                            record.current.digest.to_hex(),
                            policy_digest.to_hex(),
                        ],
                        |row| row.get(0),
                    )
                    .map_err(sql_error)?;
                if !allowed {
                    return Err(denied(
                        "no current sharing judgment permits this record and policy",
                    ));
                }
                Ok(RecordView {
                    record: Some(record),
                    cursor: transaction.cursor(&request.target_scope)?,
                })
            })
    }
}

fn validate_judgment(judgment: &KnowledgeSharingJudgment) -> MemoryResult<()> {
    if judgment.policy_id.is_empty()
        || judgment.policy_id.len() > 128
        || judgment.policy_version == 0
        || judgment
            .provider_id
            .as_ref()
            .is_some_and(|value| value.is_empty() || value.len() > 256)
        || judgment
            .model_id
            .as_ref()
            .is_some_and(|value| value.is_empty() || value.len() > 256)
        || judgment.provider_id.is_some() != judgment.model_id.is_some()
    {
        return Err(invalid("sharing judgment fields are invalid"));
    }
    Ok(())
}

fn judgment_id(
    authorization: &Authorization,
    judgment: &KnowledgeSharingJudgment,
    now_ms: u64,
) -> MemoryResult<Id> {
    let mut hasher = Sha256::new();
    hasher.update(b"hypermid.memory.sharing-judgment.v1\0");
    hasher.update(authorization.target_scope_digest.as_bytes());
    hash_component(&mut hasher, judgment.record_id.as_str().as_bytes());
    hasher.update(judgment.expected_revision_digest.as_bytes());
    hasher.update(judgment.policy_digest.as_bytes());
    hash_component(&mut hasher, judgment.classification.as_str().as_bytes());
    hash_component(&mut hasher, judgment.trust_decision.as_str().as_bytes());
    hasher.update(now_ms.to_be_bytes());
    Id::new(format!(
        "judgment:{}",
        Digest::from_bytes(hasher.finalize().into())
    ))
    .map_err(|_| invalid("sharing judgment identity could not be constructed"))
}

fn hash_component(hasher: &mut Sha256, value: &[u8]) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value);
}

fn denied(message: &str) -> hypermid_contracts::Error {
    error("AUTHORIZATION_DENIED", message, EffectState::NotStarted)
}

fn stale(message: &str) -> hypermid_contracts::Error {
    error("STALE_PUBLICATION", message, EffectState::NotStarted)
}

fn invalid(message: &str) -> hypermid_contracts::Error {
    error("INVALID_ARGUMENT", message, EffectState::NotStarted)
}

fn corrupt(message: &str) -> hypermid_contracts::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}
