use crate::model::{
    scope_digest, AccessRequest, Authorization, AuthorizationBasis, GrantOperation, GrantReference,
    MutationRequest, Operation,
};
use crate::{error, MemoryResult};
use hypermid_contracts::EffectState;
use hypermid_core::capability::{AuthContext, CapabilityOperation};
use hypermid_store::authorization::{
    authorize as authorize_capability, commit_authorized, AuthorizationStoreError,
    CommitAuthorization,
};
use rusqlite::{params, Transaction};
use std::collections::BTreeSet;

pub fn authorize(
    transaction: &Transaction<'_>,
    context: &AuthContext,
    request: &MutationRequest,
) -> MemoryResult<Authorization> {
    validate_binding(context, request)?;
    let capability = authorize_capability(transaction, context).map_err(map_authorization_error)?;
    finish_authorization(
        transaction,
        context,
        capability,
        &request.actor_scope,
        &request.target_scope,
        request.operation.into(),
        request.category.clone(),
    )
}

pub fn authorize_access(
    transaction: &Transaction<'_>,
    context: &AuthContext,
    request: &AccessRequest,
) -> MemoryResult<Authorization> {
    let collection_authority = matches!(
        request.operation,
        GrantOperation::Read | GrantOperation::Search
    ) && same_project(&request.actor_scope, &request.target_scope)
        && context.request.resource_id.as_str() == "memory-records";
    let resource_matches = if request.resource_id.as_str() == "memory-records" {
        collection_authority
    } else {
        context.request.resource_id == request.resource_id || collection_authority
    };
    if !matches!(
        request.operation,
        GrantOperation::Read | GrantOperation::Search
    ) || context.request.claimed_scope != request.actor_scope
        || context.request.target_scope != request.target_scope
        || context.request.operation != CapabilityOperation::Read
        || !resource_matches
    {
        return Err(denied());
    }
    let capability = authorize_capability(transaction, context).map_err(map_authorization_error)?;
    finish_authorization(
        transaction,
        context,
        capability,
        &request.actor_scope,
        &request.target_scope,
        request.operation,
        request.category.clone(),
    )
}

fn finish_authorization(
    transaction: &Transaction<'_>,
    context: &AuthContext,
    capability: CommitAuthorization,
    actor_scope: &hypermid_contracts::Scope,
    target_scope: &hypermid_contracts::Scope,
    operation: GrantOperation,
    category: Option<String>,
) -> MemoryResult<Authorization> {
    let actor = scope_digest(actor_scope);
    let target = scope_digest(target_scope);
    if same_project(actor_scope, target_scope) {
        return Ok(Authorization {
            basis: AuthorizationBasis::Owner,
            capability,
            actor_scope_digest: actor,
            target_scope_digest: target,
            operation,
            category,
            grant: None,
        });
    }
    let mut statement = transaction
        .prepare(
            "SELECT operations_json, categories_json, revision \
             FROM memory_share_grants \
             WHERE grant_id = ?1 \
               AND owner_scope_digest = ?2 AND grantee_scope_digest = ?3 \
               AND revoked_at_ms IS NULL \
               AND (expires_at_ms IS NULL OR expires_at_ms > ?4)",
        )
        .map_err(sql_error)?;
    let mut rows = statement
        .query(params![
            context.capability_id.as_str(),
            target.to_hex(),
            actor.to_hex(),
            context.request.now_ms,
        ])
        .map_err(sql_error)?;
    while let Some(row) = rows.next().map_err(sql_error)? {
        let operations_json: String = row.get(0).map_err(sql_error)?;
        let categories_json: Option<String> = row.get(1).map_err(sql_error)?;
        let revision: u64 = row.get(2).map_err(sql_error)?;
        let operations: BTreeSet<GrantOperation> = serde_json::from_str(&operations_json)
            .map_err(|_| corrupt("grant operations are not a valid operation set"))?;
        let categories: Option<BTreeSet<String>> = categories_json
            .map(|value| {
                serde_json::from_str(&value)
                    .map_err(|_| corrupt("grant categories are not a valid string set"))
            })
            .transpose()?;
        if !operations.contains(&operation) || !category_matches(&categories, category.as_deref()) {
            continue;
        }
        return Ok(Authorization {
            basis: AuthorizationBasis::Grant,
            capability,
            actor_scope_digest: actor,
            target_scope_digest: target,
            operation,
            category,
            grant: Some(GrantReference {
                id: context.capability_id.clone(),
                revision,
            }),
        });
    }
    Err(denied())
}

pub fn reauthorize(
    transaction: &Transaction<'_>,
    context: &AuthContext,
    request: &MutationRequest,
    authorization: &Authorization,
) -> MemoryResult<Authorization> {
    validate_binding(context, request)?;
    commit_authorized(transaction, &authorization.capability, context, |_| Ok(()))
        .map_err(map_authorization_error)?;
    let current = authorize(transaction, context, request)?;
    if same_authorization(&current, authorization) {
        Ok(current)
    } else {
        Err(denied())
    }
}

fn validate_binding(context: &AuthContext, request: &MutationRequest) -> MemoryResult<()> {
    let resource_matches = match request.record_id.as_ref() {
        Some(record_id) => {
            context.request.resource_id == *record_id
                || (same_project(&request.actor_scope, &request.target_scope)
                    && context.request.resource_id.as_str() == "memory-records")
        }
        None => {
            (matches!(request.operation, Operation::Import | Operation::Export)
                && context.request.resource_id.as_str() == "memory-portability")
                || (!matches!(request.operation, Operation::Import | Operation::Export)
                    && request.category.is_none()
                    && context.request.resource_id.as_str() == "memory-maintenance")
        }
    };
    if context.request.claimed_scope != request.actor_scope
        || context.request.target_scope != request.target_scope
        || context.request.operation != capability_operation(request.operation)
        || !resource_matches
    {
        return Err(denied());
    }
    Ok(())
}

fn same_project(actor: &hypermid_contracts::Scope, target: &hypermid_contracts::Scope) -> bool {
    actor.owner_id == target.owner_id && actor.project_id == target.project_id
}

pub const fn capability_operation(operation: Operation) -> CapabilityOperation {
    match operation {
        Operation::Create | Operation::Import => CapabilityOperation::Append,
        Operation::Update
        | Operation::Merge
        | Operation::Split
        | Operation::Relocate
        | Operation::Verify
        | Operation::Embed
        | Operation::Index
        | Operation::Summarize => CapabilityOperation::Revise,
        Operation::Archive => CapabilityOperation::Archive,
        Operation::Restore => CapabilityOperation::Restore,
        Operation::Delete | Operation::Purge => CapabilityOperation::Delete,
        Operation::Export => CapabilityOperation::Export,
    }
}

fn category_matches(allowed: &Option<BTreeSet<String>>, category: Option<&str>) -> bool {
    match (allowed, category) {
        (None, _) => true,
        (Some(_), None) => false,
        (Some(allowed), Some(category)) => allowed.contains(category),
    }
}

fn same_authorization(current: &Authorization, prior: &Authorization) -> bool {
    current.basis == prior.basis
        && current.actor_scope_digest == prior.actor_scope_digest
        && current.target_scope_digest == prior.target_scope_digest
        && current.operation == prior.operation
        && current.category == prior.category
        && current.grant == prior.grant
        && current.capability.revision == prior.capability.revision
        && current.capability.context.capability_id == prior.capability.context.capability_id
        && current.capability.context.principal == prior.capability.context.principal
        && current.capability.context.request.claimed_scope
            == prior.capability.context.request.claimed_scope
        && current.capability.context.request.target_scope
            == prior.capability.context.request.target_scope
        && current.capability.context.request.operation
            == prior.capability.context.request.operation
        && current.capability.context.request.resource_id
            == prior.capability.context.request.resource_id
}

fn map_authorization_error(source: AuthorizationStoreError) -> hypermid_contracts::Error {
    match source {
        AuthorizationStoreError::Denied(_) => denied(),
        AuthorizationStoreError::Storage(_) => error(
            "STORE_READ_FAILED",
            "capability authorization could not read durable state",
            EffectState::Unknown,
        ),
    }
}

fn denied() -> hypermid_contracts::Error {
    error(
        "AUTHORIZATION_DENIED",
        "the authenticated principal lacks an exact live capability and memory grant",
        EffectState::NotStarted,
    )
}

fn corrupt(message: &'static str) -> hypermid_contracts::Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}

fn sql_error(source: rusqlite::Error) -> hypermid_contracts::Error {
    error(
        "STORE_READ_FAILED",
        format!("memory authorization read failed: {source}"),
        EffectState::Unknown,
    )
}
