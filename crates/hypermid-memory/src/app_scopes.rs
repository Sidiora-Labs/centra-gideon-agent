//! Stable owner-issued app namespaces with revocable activation grants.
use crate::{
    error, scope_digest, AuthContext, CapabilityGrant, CapabilityOperation, Digest, EffectState,
    Id, MemoryApi, MemoryResult, PrincipalKind, Scope,
};
use hypermid_store::authorization::{authorize, commit_authorized, put_grant, revoke};
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub(crate) const APP_SCOPES_SCHEMA: &str = r#"
CREATE TABLE memory_app_scopes (
 actor_scope_digest TEXT NOT NULL,
 issuer_principal_id TEXT NOT NULL,
 app_name TEXT NOT NULL,
 scope_id TEXT NOT NULL UNIQUE,
 epoch INTEGER NOT NULL CHECK(epoch>0),
 capability_id TEXT NOT NULL UNIQUE REFERENCES hypermid_capabilities(capability_id),
 receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)),
 receipt_digest TEXT NOT NULL CHECK(length(receipt_digest)=64),
 expires_at_ms INTEGER NOT NULL,
 revoked_at_ms INTEGER,
 PRIMARY KEY(actor_scope_digest,app_name)
) STRICT;
"#;
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AppScopeReceipt {
    pub scope_id: Id,
    pub actor_scope: Scope,
    pub scope: Scope,
    pub capability_id: Id,
    pub app_name: String,
    pub manifest_digest: Digest,
    pub epoch: u64,
    pub expires_at_ms: u64,
    pub revoked: bool,
}
fn denied() -> crate::Error {
    error(
        "AUTHORIZATION_DENIED",
        "app namespace authority is unavailable",
        EffectState::NotStarted,
    )
}
fn storage(_: impl std::fmt::Display) -> crate::Error {
    error(
        "APP_SCOPE_STORAGE_FAILED",
        "app namespace metadata could not be verified",
        EffectState::NotStarted,
    )
}
fn token(prefix: &str) -> MemoryResult<Id> {
    let mut b = [0u8; 16];
    getrandom::fill(&mut b).map_err(storage)?;
    Id::new(format!(
        "{prefix}:{}",
        b.iter().map(|v| format!("{v:02x}")).collect::<String>()
    ))
    .map_err(storage)
}
fn owner(c: &AuthContext) -> MemoryResult<()> {
    if c.principal.kind != PrincipalKind::Foreground
        || c.principal.owner_id != c.request.claimed_scope.owner_id
        || c.request.claimed_scope != c.request.target_scope
        || c.request.operation != CapabilityOperation::Administer
        || c.request.resource_id.as_str() != "memory-service"
    {
        return Err(denied());
    }
    Ok(())
}
fn receipt(raw: &str, digest: &str) -> MemoryResult<AppScopeReceipt> {
    if Digest::sha256(raw.as_bytes()).to_hex() != digest {
        return Err(storage("integrity"));
    }
    serde_json::from_str(raw).map_err(storage)
}
impl MemoryApi {
    pub fn issue_app_scope(
        &mut self,
        c: &AuthContext,
        app: String,
        manifest: Digest,
        ttl: u64,
    ) -> MemoryResult<AppScopeReceipt> {
        owner(c)?;
        if app.is_empty()
            || app.len() > 128
            || !app
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            || ttl == 0
            || ttl > 2_592_000_000
        {
            return Err(denied());
        }
        let actor = c.request.claimed_scope.clone();
        let key = scope_digest(&actor).to_hex();
        self.store.immediate(|tx|{
   let auth=authorize(tx.raw(),c).map_err(|_|denied())?;
   let parent:u64=tx.raw().query_row("SELECT expires_at_ms FROM hypermid_capabilities WHERE capability_id=?1",[c.capability_id.as_str()],|r|r.get(0)).map_err(storage)?;
   let expiry=c.request.now_ms.checked_add(ttl).ok_or_else(denied)?.min(parent);
   let prior:Option<(String,String,Option<i64>)>=tx.raw().query_row("SELECT receipt_json,receipt_digest,revoked_at_ms FROM memory_app_scopes WHERE actor_scope_digest=?1 AND app_name=?2",params![key,app],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional().map_err(storage)?;
   let (scope_id,epoch)=if let Some((raw,digest,revoked))=prior {
    let old=receipt(&raw,&digest)?;
    if old.actor_scope!=actor || old.app_name!=app {return Err(denied())}
    if revoked.is_none() && old.expires_at_ms>c.request.now_ms && old.manifest_digest==manifest {
     commit_authorized(tx.raw(),&auth,c,|_|Ok(())).map_err(|_|denied())?;return Ok(old)
    }
    revoke(tx.raw(),&c.principal,&old.capability_id).map_err(|_|denied())?;
    tx.raw().execute("UPDATE memory_share_grants SET revoked_at_ms=?1,revision=revision+1 WHERE grant_id=?2 AND revoked_at_ms IS NULL",params![c.request.now_ms,old.capability_id.as_str()]).map_err(storage)?;
    (old.scope_id,old.epoch.checked_add(1).ok_or_else(denied)?)
   }else{(token("app-scope")?,1)};
   let scope=Scope::new(actor.owner_id.clone(),actor.project_id.clone(),Some(scope_id.clone()));let cap=token("app-capability")?;
   let grant=CapabilityGrant{capability_id:cap.clone(),issuer_owner_id:actor.owner_id.clone(),principal_id:c.principal.principal_id.clone(),claimed_scope:actor.clone(),target_scope:scope.clone(),operations:BTreeSet::from([CapabilityOperation::Read,CapabilityOperation::Append,CapabilityOperation::Revise,CapabilityOperation::Delete,CapabilityOperation::Restore]),resources:BTreeSet::from([Id::new("memory-records").map_err(storage)?,Id::new("memory-list").map_err(storage)?,Id::new("memory-embedding").map_err(storage)?]),expires_at_ms:expiry};
   put_grant(tx.raw(),&c.principal,&grant).map_err(|_|denied())?;tx.ensure_scope(&actor,c.request.now_ms as i64)?;tx.ensure_scope(&scope,c.request.now_ms as i64)?;
   let operations="[\"read\",\"search\",\"create\",\"update\",\"restore\",\"merge\",\"split\",\"relocate\",\"delete\",\"purge\",\"verify\",\"embed\",\"index\",\"summarize\"]";
   tx.raw().execute("INSERT INTO memory_share_grants(grant_id,owner_scope_digest,grantee_scope_digest,operations_json,categories_json,granted_at_ms,expires_at_ms,revoked_at_ms,revision) VALUES(?1,?2,?3,?4,NULL,?5,?6,NULL,1)",params![cap.as_str(),scope_digest(&scope).to_hex(),key,operations,c.request.now_ms,expiry]).map_err(storage)?;

   let result=AppScopeReceipt{scope_id,actor_scope:actor.clone(),scope,capability_id:cap,app_name:app.clone(),manifest_digest:manifest,epoch,expires_at_ms:expiry,revoked:false};let raw=serde_json::to_string(&result).map_err(storage)?;
   tx.raw().execute("INSERT INTO memory_app_scopes VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,NULL) ON CONFLICT(actor_scope_digest,app_name) DO UPDATE SET issuer_principal_id=excluded.issuer_principal_id,epoch=excluded.epoch,capability_id=excluded.capability_id,receipt_json=excluded.receipt_json,receipt_digest=excluded.receipt_digest,expires_at_ms=excluded.expires_at_ms,revoked_at_ms=NULL",params![key,c.principal.principal_id.as_str(),app,result.scope_id.as_str(),epoch,result.capability_id.as_str(),raw,Digest::sha256(raw.as_bytes()).to_hex(),expiry]).map_err(storage)?;
   commit_authorized(tx.raw(),&auth,c,|_|Ok(())).map_err(|_|denied())?;Ok(result)
  })
    }
    pub fn lookup_app_scope(&mut self,c:&AuthContext,app:String)->MemoryResult<Option<AppScopeReceipt>> {
      if c.principal.kind!=PrincipalKind::Foreground || c.request.claimed_scope!=c.request.target_scope || c.request.operation!=CapabilityOperation::Read || c.request.resource_id.as_str()!="memory-records" {return Err(denied())}
      self.store.immediate(|tx|{
        authorize(tx.raw(),c).map_err(|_|denied())?;
        let prior:Option<(String,String,Option<i64>)>=tx.raw().query_row("SELECT receipt_json,receipt_digest,revoked_at_ms FROM memory_app_scopes WHERE actor_scope_digest=?1 AND app_name=?2",params![scope_digest(&c.request.claimed_scope).to_hex(),app],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional().map_err(storage)?;
        let Some((raw,digest,revoked))=prior else{return Ok(None)};
        let r=receipt(&raw,&digest)?;
        if r.actor_scope!=c.request.claimed_scope || r.app_name!=app {return Err(denied())}
        if revoked.is_some() || r.expires_at_ms<=c.request.now_ms {return Err(error("APP_SCOPE_INACTIVE","app memory activation requires owner review",EffectState::NotStarted))}
        Ok(Some(r))
      })
    }
    pub fn resolve_app_scope(&mut self, c: &AuthContext, id: Id) -> MemoryResult<AppScopeReceipt> {
        if c.request.operation != CapabilityOperation::Read
            || c.request.resource_id.as_str() != "memory-records"
        {
            return Err(denied());
        }
        self.store.immediate(|tx|{
   authorize(tx.raw(),c).map_err(|_|denied())?;
   let (raw,digest,revoked):(String,String,Option<i64>)=tx.raw().query_row("SELECT receipt_json,receipt_digest,revoked_at_ms FROM memory_app_scopes WHERE scope_id=?1",[id.as_str()],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).map_err(|_|denied())?;
   let r=receipt(&raw,&digest)?;
   if revoked.is_some()||r.revoked||r.scope_id!=id||r.actor_scope!=c.request.claimed_scope||r.scope!=c.request.target_scope||r.capability_id!=c.capability_id||r.expires_at_ms<=c.request.now_ms{return Err(denied())}Ok(r)
  })
    }
    pub fn revoke_app_scope(
        &mut self,
        c: &AuthContext,
        app: String,
        expected_capability: Option<Id>,
    ) -> MemoryResult<Option<AppScopeReceipt>> {
        owner(c)?;
        self.store.immediate(|tx|{
   let auth=authorize(tx.raw(),c).map_err(|_|denied())?;
   let prior:Option<(String,String,Option<i64>)>=tx.raw().query_row("SELECT receipt_json,receipt_digest,revoked_at_ms FROM memory_app_scopes WHERE actor_scope_digest=?1 AND app_name=?2",params![scope_digest(&c.request.claimed_scope).to_hex(),app],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional().map_err(storage)?;
   let Some((raw,digest,revoked))=prior else{return Ok(None)};let mut r=receipt(&raw,&digest)?;
   if r.actor_scope!=c.request.claimed_scope||r.app_name!=app{return Err(denied())}
   if expected_capability.as_ref().is_some_and(|id|id!=&r.capability_id){return Ok(None)}
   if revoked.is_none(){revoke(tx.raw(),&c.principal,&r.capability_id).map_err(|_|denied())?;tx.raw().execute("UPDATE memory_share_grants SET revoked_at_ms=?1,revision=revision+1 WHERE grant_id=?2 AND revoked_at_ms IS NULL",params![c.request.now_ms,r.capability_id.as_str()]).map_err(storage)?;tx.raw().execute("UPDATE memory_app_scopes SET revoked_at_ms=?1 WHERE scope_id=?2",params![c.request.now_ms,r.scope_id.as_str()]).map_err(storage)?;}
   commit_authorized(tx.raw(),&auth,c,|_|Ok(())).map_err(|_|denied())?;r.revoked=true;Ok(Some(r))
  })
    }
}
