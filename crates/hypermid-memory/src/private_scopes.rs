//! Owner-issued scopes for bounded private execution lifetimes.
use crate::{
    error, scope_digest, AuthContext, CapabilityGrant, CapabilityOperation, EffectState, Id,
    MemoryApi, MemoryResult, PrincipalKind, Scope,
};
use hypermid_store::authorization::{authorize, commit_authorized, put_grant, revoke};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub(crate) const PRIVATE_SCOPES_SCHEMA: &str = r#"
CREATE TABLE memory_private_scopes (
    scope_id TEXT PRIMARY KEY,
    actor_scope_digest TEXT NOT NULL,
    issuer_principal_id TEXT NOT NULL,
    origin_session_key TEXT NOT NULL,
    original_actor TEXT NOT NULL,
    memory_mode TEXT NOT NULL CHECK(memory_mode IN ('temporary','incognito')),
    capability_id TEXT NOT NULL UNIQUE REFERENCES hypermid_capabilities(capability_id),
    receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)),
    receipt_digest TEXT NOT NULL CHECK(length(receipt_digest)=64),
    expires_at_ms INTEGER NOT NULL,
    retired_at_ms INTEGER,
    UNIQUE(actor_scope_digest, issuer_principal_id, origin_session_key)
) STRICT;
"#;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateScopeReceipt {
    pub scope_id: Id,
    pub actor_scope: Scope,
    pub scope: Scope,
    pub capability_id: Id,
    pub origin_session_key: Id,
    pub original_actor: Id,
    pub memory_mode: String,
    pub expires_at_ms: u64,
    pub retired: bool,
}

fn denied() -> crate::Error {
    error(
        "AUTHORIZATION_DENIED",
        "private scope authority is unavailable",
        EffectState::NotStarted,
    )
}
fn storage(_: impl std::fmt::Display) -> crate::Error {
    error(
        "PRIVATE_SCOPE_STORAGE_FAILED",
        "private scope metadata could not be verified",
        EffectState::NotStarted,
    )
}
fn token(prefix: &str) -> MemoryResult<Id> {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(storage)?;
    Id::new(format!(
        "{prefix}:{}",
        bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()
    ))
    .map_err(storage)
}
fn owner(context: &AuthContext) -> MemoryResult<()> {
    if context.principal.kind != PrincipalKind::Foreground
        || context.principal.owner_id != context.request.claimed_scope.owner_id
        || context.request.target_scope != context.request.claimed_scope
        || context.request.operation != CapabilityOperation::Administer
        || context.request.resource_id.as_str() != "memory-service"
    {
        return Err(denied());
    }
    Ok(())
}

impl MemoryApi {
    pub fn issue_private_scope(
        &mut self,
        context: &AuthContext,
        origin: Id,
        original_actor: Id,
        mode: String,
        ttl_ms: u64,
    ) -> MemoryResult<PrivateScopeReceipt> {
        owner(context)?;
        if !matches!(mode.as_str(), "temporary" | "incognito") || ttl_ms == 0 || ttl_ms > 86_400_000
        {
            return Err(denied());
        }
        let expires = context
            .request
            .now_ms
            .checked_add(ttl_ms)
            .filter(|v| *v <= hypermid_contracts::MAX_SAFE_INTEGER)
            .ok_or_else(denied)?;
        let actor = context.request.claimed_scope.clone();
        let key = scope_digest(&actor).to_hex();
        self.store.immediate(|tx| {
            let auth=authorize(tx.raw(),context).map_err(|_|denied())?;
            let parent_expiry:u64=tx.raw().query_row("SELECT expires_at_ms FROM hypermid_capabilities WHERE capability_id=?1",[context.capability_id.as_str()],|r|r.get(0)).map_err(storage)?;
            let expires=expires.min(parent_expiry);

            let prior:Option<(String,Option<i64>,u64,String)>=tx.raw().query_row("SELECT receipt_json,retired_at_ms,expires_at_ms,receipt_digest FROM memory_private_scopes WHERE actor_scope_digest=?1 AND issuer_principal_id=?2 AND origin_session_key=?3",params![key,context.principal.principal_id.as_str(),origin.as_str()],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))).optional().map_err(storage)?;
            if let Some((json,retired,expiry,digest))=prior {
                if crate::Digest::sha256(json.as_bytes()).to_hex()!=digest {return Err(storage("receipt integrity"));}
                let receipt:PrivateScopeReceipt=serde_json::from_str(&json).map_err(storage)?;
                if retired.is_some() || expiry<=context.request.now_ms || receipt.original_actor!=original_actor || receipt.memory_mode!=mode || receipt.actor_scope!=actor || receipt.expires_at_ms!=expiry {return Err(denied());}
                commit_authorized(tx.raw(),&auth,context, |_|Ok(())).map_err(|_|denied())?;
                return Ok(receipt);
            }
            let active:u64=tx.raw().query_row("SELECT count(*) FROM memory_private_scopes WHERE actor_scope_digest=?1 AND retired_at_ms IS NULL AND expires_at_ms>?2",params![key,context.request.now_ms],|r|r.get(0)).map_err(storage)?;
            if active>=128 {return Err(error("PRIVATE_SCOPE_LIMIT", "active private execution limit reached", EffectState::NotStarted));}
            let id=token("private-scope")?;
            let scope=Scope::new(actor.owner_id.clone(),actor.project_id.clone(),Some(id.clone()));
            let capability=token("private-capability")?;
            let grant=CapabilityGrant { capability_id:capability.clone(), issuer_owner_id:actor.owner_id.clone(), principal_id:context.principal.principal_id.clone(), claimed_scope:actor.clone(), target_scope:scope.clone(), operations:BTreeSet::from([CapabilityOperation::Read,CapabilityOperation::Append,CapabilityOperation::Revise,CapabilityOperation::Delete]), resources:BTreeSet::from([Id::new("private-work").map_err(storage)?]),expires_at_ms:expires };
            put_grant(tx.raw(),&context.principal,&grant).map_err(|_|denied())?;
            tx.ensure_scope(&scope,context.request.now_ms as i64)?;
            let receipt=PrivateScopeReceipt {scope_id:id.clone(),actor_scope:actor.clone(),scope,capability_id:capability.clone(),origin_session_key:origin.clone(),original_actor:original_actor.clone(),memory_mode:mode.clone(),expires_at_ms:expires,retired:false};
            let json=serde_json::to_string(&receipt).map_err(storage)?;
            tx.raw().execute("INSERT INTO memory_private_scopes VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,NULL)",params![id.as_str(),key,context.principal.principal_id.as_str(),origin.as_str(),original_actor.as_str(),mode,capability.as_str(),json,crate::Digest::sha256(json.as_bytes()).to_hex(),expires]).map_err(storage)?;
            commit_authorized(tx.raw(),&auth,context, |_|Ok(())).map_err(|_|denied())?;
            Ok(receipt)
        })
    }

    pub fn resolve_private_scope(
        &mut self,
        context: &AuthContext,
        scope_id: Id,
    ) -> MemoryResult<PrivateScopeReceipt> {
        if context.request.operation != CapabilityOperation::Read
            || context.request.resource_id.as_str() != "private-work"
        {
            return Err(denied());
        }
        self.store.immediate(|tx|{
            let auth=authorize(tx.raw(),context).map_err(|_|denied())?;
            let (json,retired,expiry,digest):(String,Option<i64>,u64,String)=tx.raw().query_row("SELECT receipt_json,retired_at_ms,expires_at_ms,receipt_digest FROM memory_private_scopes WHERE scope_id=?1",[scope_id.as_str()],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))).map_err(|_|denied())?;
            if crate::Digest::sha256(json.as_bytes()).to_hex()!=digest {return Err(storage("receipt integrity"));}
                let receipt:PrivateScopeReceipt=serde_json::from_str(&json).map_err(storage)?;
            if retired.is_some() || expiry<=context.request.now_ms || receipt.retired || receipt.scope_id!=scope_id || receipt.expires_at_ms!=expiry || receipt.capability_id!=context.capability_id || receipt.actor_scope!=context.request.claimed_scope || receipt.scope!=context.request.target_scope {return Err(denied());}
            let current=authorize(tx.raw(),context).map_err(|_|denied())?;
            if current.revision!=auth.revision {return Err(denied());}
            Ok(receipt)
        })
    }

    pub fn retire_private_scope(
        &mut self,
        context: &AuthContext,
        origin: Id,
    ) -> MemoryResult<Option<PrivateScopeReceipt>> {
        owner(context)?;
        self.store.immediate(|tx|{
            let auth=authorize(tx.raw(),context).map_err(|_|denied())?;
            let prior:Option<(String,Option<i64>,String)>=tx.raw().query_row("SELECT receipt_json,retired_at_ms,receipt_digest FROM memory_private_scopes WHERE actor_scope_digest=?1 AND issuer_principal_id=?2 AND origin_session_key=?3",params![scope_digest(&context.request.claimed_scope).to_hex(),context.principal.principal_id.as_str(),origin.as_str()],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional().map_err(storage)?;
            let Some((json,retired,digest))=prior else {return Ok(None)};
            if crate::Digest::sha256(json.as_bytes()).to_hex()!=digest {return Err(storage("receipt integrity"));}
            let mut receipt:PrivateScopeReceipt=serde_json::from_str(&json).map_err(storage)?;
            if receipt.actor_scope!=context.request.claimed_scope || receipt.origin_session_key!=origin {return Err(denied());}
            if retired.is_none() {
                revoke(tx.raw(),&context.principal,&receipt.capability_id).map_err(|_|denied())?;
                tx.raw().execute("UPDATE memory_private_scopes SET retired_at_ms=?1 WHERE scope_id=?2",params![context.request.now_ms,receipt.scope_id.as_str()]).map_err(storage)?;
            }
            commit_authorized(tx.raw(),&auth,context, |_|Ok(())).map_err(|_|denied())?;
            receipt.retired=true;Ok(Some(receipt))
        })
    }
}
