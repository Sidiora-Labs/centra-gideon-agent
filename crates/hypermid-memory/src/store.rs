use crate::auth::{authorize, authorize_access, reauthorize};
use crate::migrations::{memory_migrations, schema_digest, MEMORY_SCHEMA_VERSION};
use crate::model::{
    scope_digest, AccessRequest, Authorization, MutationRequest, RevisionPrecondition, ShareGrant,
};
use crate::{error, Cursor, EffectState, Error, Id, MemoryResult, Scope};
use hypermid_contracts::storage::{BackendKind, Fence, LeaseKey};
use hypermid_contracts::MAX_SAFE_INTEGER;
use hypermid_core::capability::AuthContext;
use hypermid_store::{SQLiteStore, StoreError};
use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};
use std::path::Path;

pub struct MemoryStore {
    inner: SQLiteStore,
}

pub struct MemoryTransaction<'a> {
    transaction: &'a Transaction<'a>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MemorySchemaEvidence {
    pub current_version: u64,
    pub compatibility_floor: u64,
    pub schema_digest: hypermid_contracts::Digest,
}

impl MemoryStore {
    pub fn open(path: impl AsRef<Path>) -> MemoryResult<Self> {
        let lease_key = LeaseKey {
            module_id: Id::new("hypermid-memory").expect("constant module id is valid"),
            backend: BackendKind::Sqlite,
            scope_key: Id::new("memory-store").expect("constant scope key is valid"),
        };
        let inner = SQLiteStore::open(path, lease_key, MEMORY_SCHEMA_VERSION, &memory_migrations())
            .map_err(map_store_error)?;
        let store = Self { inner };
        store.assert_schema()?;
        Ok(store)
    }

    pub fn current_fence(&self) -> Fence {
        self.inner.current_fence().clone()
    }

    pub fn schema_evidence(&self) -> MemoryResult<MemorySchemaEvidence> {
        self.read(|connection| {
            let (current_version, compatibility_floor, digest): (u64, u64, String) = connection
                .query_row(
                    "SELECT current_version, compatibility_floor, schema_digest \
                     FROM hypermid_schema_version WHERE singleton = 1",
                    [],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .map_err(read_error)?;
            Ok(MemorySchemaEvidence {
                current_version,
                compatibility_floor,
                schema_digest: digest
                    .parse()
                    .map_err(|_| corrupt("memory schema digest is malformed"))?,
            })
        })
    }

    pub(crate) fn read<T>(
        &self,
        operation: impl FnOnce(&Connection) -> MemoryResult<T>,
    ) -> MemoryResult<T> {
        self.inner
            .read(|connection| Ok(operation(connection)))
            .map_err(map_store_error)?
    }

    pub(crate) fn immediate<T>(
        &mut self,
        operation: impl FnOnce(&MemoryTransaction<'_>) -> MemoryResult<T>,
    ) -> MemoryResult<T> {
        let fence = self.current_fence();
        self.inner
            .with_fenced_transaction(&fence, |transaction| {
                operation(&MemoryTransaction { transaction })
            })
            .map_err(map_store_error)?
    }

    pub fn immediate_authorized<T>(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
        operation: impl FnOnce(&MemoryTransaction<'_>, &Authorization) -> MemoryResult<T>,
    ) -> MemoryResult<T> {
        self.immediate(|transaction| {
            let authorization = transaction.authorize(context, request)?;
            operation(transaction, &authorization)
        })
    }

    pub fn immediate_access<T>(
        &mut self,
        context: &AuthContext,
        request: &AccessRequest,
        operation: impl FnOnce(&MemoryTransaction<'_>, &Authorization) -> MemoryResult<T>,
    ) -> MemoryResult<T> {
        self.immediate(|transaction| {
            let authorization = transaction.authorize_access(context, request)?;
            operation(transaction, &authorization)
        })
    }

    pub(crate) fn ensure_scope(&mut self, scope: &Scope, now_ms: i64) -> MemoryResult<Cursor> {
        self.immediate(|transaction| transaction.ensure_scope(scope, now_ms))
    }

    pub(crate) fn cursor(&self, scope: &Scope) -> MemoryResult<Cursor> {
        let digest = scope_digest(scope).to_hex();
        self.read(|connection| {
            let pair: Option<(u64, u64)> = connection
                .query_row(
                    "SELECT epoch, sequence FROM memory_scopes WHERE scope_digest = ?1",
                    [digest],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .optional()
                .map_err(read_error)?;
            let (epoch, sequence) = pair.ok_or_else(scope_not_found)?;
            Cursor::new(epoch, sequence).map_err(|_| corrupt("scope cursor is out of range"))
        })
    }

    pub fn authorize(
        &mut self,
        context: &AuthContext,
        request: &MutationRequest,
    ) -> MemoryResult<Authorization> {
        self.immediate(|transaction| transaction.authorize(context, request))
    }

    pub(crate) fn put_share_grant(
        &mut self,
        actor_scope: &Scope,
        grant: &ShareGrant,
        now_ms: i64,
    ) -> MemoryResult<()> {
        if actor_scope != &grant.owner_scope {
            return Err(error(
                "AUTHORIZATION_DENIED",
                "only the exact owner scope can create or replace a memory grant",
                EffectState::NotStarted,
            ));
        }
        validate_grant(grant)?;
        self.immediate(|transaction| {
            transaction.ensure_scope(&grant.owner_scope, now_ms)?;
            transaction.ensure_scope(&grant.grantee_scope, now_ms)?;
            let prior: Option<u64> = transaction
                .raw()
                .query_row(
                    "SELECT revision FROM memory_share_grants WHERE grant_id = ?1",
                    [grant.id.as_str()],
                    |row| row.get(0),
                )
                .optional()
                .map_err(write_error)?;
            let expected = prior.unwrap_or(0).checked_add(1).ok_or_else(|| {
                error(
                    "REVISION_CONFLICT",
                    "grant revision cannot advance",
                    EffectState::NotStarted,
                )
            })?;
            if grant.revision != expected {
                return Err(revision_conflict("grant revision precondition failed"));
            }
            let operations = serde_json::to_string(&grant.operations)
                .map_err(|_| corrupt("grant operations could not be encoded"))?;
            let categories = grant
                .categories
                .as_ref()
                .map(serde_json::to_string)
                .transpose()
                .map_err(|_| corrupt("grant categories could not be encoded"))?;
            transaction
                .raw()
                .execute(
                    "INSERT INTO memory_share_grants(\
                         grant_id, owner_scope_digest, grantee_scope_digest, operations_json,\
                         categories_json, granted_at_ms, expires_at_ms, revoked_at_ms, revision\
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9) \
                     ON CONFLICT(grant_id) DO UPDATE SET \
                         owner_scope_digest=excluded.owner_scope_digest,\
                         grantee_scope_digest=excluded.grantee_scope_digest,\
                         operations_json=excluded.operations_json,\
                         categories_json=excluded.categories_json,\
                         granted_at_ms=excluded.granted_at_ms,\
                         expires_at_ms=excluded.expires_at_ms,\
                         revoked_at_ms=excluded.revoked_at_ms,\
                         revision=excluded.revision",
                    params![
                        grant.id.as_str(),
                        scope_digest(&grant.owner_scope).to_hex(),
                        scope_digest(&grant.grantee_scope).to_hex(),
                        operations,
                        categories,
                        grant.granted_at_ms,
                        grant.expires_at_ms,
                        grant.revoked_at_ms,
                        grant.revision,
                    ],
                )
                .map_err(write_error)?;
            Ok(())
        })
    }

    pub(crate) fn revoke_share_grant(
        &mut self,
        actor_scope: &Scope,
        grant_id: &Id,
        expected_revision: u64,
        revoked_at_ms: i64,
    ) -> MemoryResult<()> {
        let actor_digest = scope_digest(actor_scope).to_hex();
        self.immediate(|transaction| {
            let changed = transaction
                .raw()
                .execute(
                    "UPDATE memory_share_grants \
                     SET revoked_at_ms = ?1, revision = revision + 1 \
                     WHERE grant_id = ?2 AND owner_scope_digest = ?3 \
                       AND revision = ?4 AND revoked_at_ms IS NULL",
                    params![
                        revoked_at_ms,
                        grant_id.as_str(),
                        actor_digest,
                        expected_revision
                    ],
                )
                .map_err(write_error)?;
            if changed == 1 {
                Ok(())
            } else {
                Err(revision_conflict(
                    "grant revocation ownership or revision precondition failed",
                ))
            }
        })
    }

    fn assert_schema(&self) -> MemoryResult<()> {
        let evidence = self.schema_evidence()?;
        if evidence.current_version > MEMORY_SCHEMA_VERSION
            || evidence.compatibility_floor > MEMORY_SCHEMA_VERSION
        {
            return Err(error(
                "SCHEMA_NEWER_UNSUPPORTED",
                "the memory store requires a newer runtime",
                EffectState::NotStarted,
            ));
        }
        if evidence.current_version != MEMORY_SCHEMA_VERSION
            || evidence.schema_digest != schema_digest()
        {
            return Err(error(
                "SCHEMA_INCOMPATIBLE",
                "the memory schema version or digest does not match this runtime",
                EffectState::NotStarted,
            ));
        }
        Ok(())
    }
}

impl<'a> MemoryTransaction<'a> {
    pub(crate) fn raw(&self) -> &Transaction<'a> {
        self.transaction
    }

    pub(crate) fn ensure_scope(&self, scope: &Scope, now_ms: i64) -> MemoryResult<Cursor> {
        if now_ms < 0 {
            return Err(error(
                "INVALID_ARGUMENT",
                "scope timestamp must be non-negative",
                EffectState::NotStarted,
            ));
        }
        let digest = scope_digest(scope).to_hex();
        let scope_json =
            serde_json::to_string(scope).map_err(|_| corrupt("scope could not be encoded"))?;
        self.transaction
            .execute(
                "INSERT INTO memory_scopes(\
                     scope_digest, scope_json, epoch, sequence, created_at_ms, updated_at_ms\
                 ) VALUES (?1, ?2, 1, 0, ?3, ?3)\
                 ON CONFLICT(scope_digest) DO NOTHING",
                params![digest, scope_json, now_ms],
            )
            .map_err(write_error)?;
        self.cursor(scope)
    }

    pub(crate) fn cursor(&self, scope: &Scope) -> MemoryResult<Cursor> {
        let pair: Option<(u64, u64)> = self
            .transaction
            .query_row(
                "SELECT epoch, sequence FROM memory_scopes WHERE scope_digest = ?1",
                [scope_digest(scope).to_hex()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .map_err(read_error)?;
        let (epoch, sequence) = pair.ok_or_else(scope_not_found)?;
        Cursor::new(epoch, sequence).map_err(|_| corrupt("scope cursor is out of range"))
    }

    pub fn advance_cursor(&self, scope: &Scope, now_ms: i64) -> MemoryResult<Cursor> {
        if now_ms < 0 {
            return Err(error(
                "INVALID_ARGUMENT",
                "cursor timestamp must be non-negative",
                EffectState::NotStarted,
            ));
        }
        let digest = scope_digest(scope).to_hex();
        let changed = self
            .transaction
            .execute(
                "UPDATE memory_scopes SET sequence = sequence + 1, updated_at_ms = ?1 \
                 WHERE scope_digest = ?2 AND sequence < ?3",
                params![now_ms, digest, MAX_SAFE_INTEGER],
            )
            .map_err(write_error)?;
        if changed != 1 {
            return Err(error(
                "CURSOR_EXHAUSTED",
                "scope cursor is missing or cannot advance",
                EffectState::NotStarted,
            ));
        }
        self.cursor(scope)
    }

    pub fn require_record_revision(
        &self,
        record_id: &Id,
        precondition: &RevisionPrecondition,
    ) -> MemoryResult<Option<hypermid_contracts::Digest>> {
        let current: Option<String> = self
            .transaction
            .query_row(
                "SELECT current_revision_digest FROM memory_records WHERE record_id = ?1",
                [record_id.as_str()],
                |row| row.get(0),
            )
            .optional()
            .map_err(read_error)?;
        match (precondition, current) {
            (RevisionPrecondition::MustNotExist, None) => Ok(None),
            (RevisionPrecondition::Match(expected), Some(current))
                if current == expected.to_hex() =>
            {
                Ok(Some(*expected))
            }
            _ => Err(revision_conflict("record revision precondition failed")),
        }
    }

    pub(crate) fn authorize(
        &self,
        context: &AuthContext,
        request: &MutationRequest,
    ) -> MemoryResult<Authorization> {
        authorize(self.transaction, context, request)
    }

    pub(crate) fn authorize_access(
        &self,
        context: &AuthContext,
        request: &AccessRequest,
    ) -> MemoryResult<Authorization> {
        authorize_access(self.transaction, context, request)
    }

    pub(crate) fn reauthorize(
        &self,
        context: &AuthContext,
        request: &MutationRequest,
        authorization: &Authorization,
    ) -> MemoryResult<Authorization> {
        reauthorize(self.transaction, context, request, authorization)
    }
}

fn validate_grant(grant: &ShareGrant) -> MemoryResult<()> {
    if grant.operations.is_empty()
        || grant.revision == 0
        || grant.granted_at_ms < 0
        || grant
            .expires_at_ms
            .is_some_and(|expires| expires <= grant.granted_at_ms)
        || grant
            .revoked_at_ms
            .is_some_and(|revoked| revoked < grant.granted_at_ms)
        || grant.categories.as_ref().is_some_and(|categories| {
            categories.is_empty() || categories.iter().any(String::is_empty)
        })
    {
        return Err(error(
            "INVALID_ARGUMENT",
            "memory grant violates the grant contract",
            EffectState::NotStarted,
        ));
    }
    Ok(())
}

fn map_store_error(source: StoreError) -> Error {
    match source {
        StoreError::StoreAhead { .. } => error(
            "SCHEMA_NEWER_UNSUPPORTED",
            "the memory store requires a newer runtime",
            EffectState::NotStarted,
        ),
        StoreError::StaleFence | StoreError::Lease(_) => error(
            "FENCE_REJECTED",
            "the memory store mutation authority is stale or unavailable",
            EffectState::NotStarted,
        ),
        StoreError::InvalidMigrationOrder
        | StoreError::MalformedMigrationChain
        | StoreError::MigrationMismatch { .. } => error(
            "SCHEMA_INCOMPATIBLE",
            "the memory migration history is incompatible",
            EffectState::NotStarted,
        ),
        StoreError::Sqlite(_) | StoreError::Io(_) | StoreError::NotRegular(_) => error(
            "STORE_OPEN_FAILED",
            "the memory store could not complete the storage operation",
            EffectState::Unknown,
        ),
    }
}

fn read_error(source: rusqlite::Error) -> Error {
    error(
        "STORE_READ_FAILED",
        format!("memory store read failed: {source}"),
        EffectState::Unknown,
    )
}

fn write_error(source: rusqlite::Error) -> Error {
    error(
        "STORE_WRITE_FAILED",
        format!("memory store mutation failed: {source}"),
        EffectState::Unknown,
    )
}

fn scope_not_found() -> Error {
    error(
        "SCOPE_NOT_FOUND",
        "the memory scope has not been registered",
        EffectState::NotStarted,
    )
}

fn revision_conflict(message: &'static str) -> Error {
    error("REVISION_CONFLICT", message, EffectState::NotStarted)
}

fn corrupt(message: &'static str) -> Error {
    error("STORE_CORRUPT", message, EffectState::Unknown)
}

#[cfg(test)]
mod foundation_tests {
    use super::*;
    use crate::{
        AuthContext, AuthenticatedPrincipal, AuthorizationRequest, CapabilityGrant, GrantOperation,
        Operation, PrincipalKind, RevisionPrecondition,
    };
    use hypermid_store::authorization::put_grant as put_capability;
    use std::collections::BTreeSet;

    fn id(value: &str) -> Id {
        Id::new(value).unwrap()
    }

    fn scope(owner: &str, project: &str) -> Scope {
        Scope::new(id(owner), id(project), Some(id("workspace-1")))
    }

    fn request(actor: Scope, target: Scope, operation: Operation) -> MutationRequest {
        MutationRequest {
            operation,
            actor_scope: actor,
            target_scope: target,
            record_id: Some(id("record-1")),
            category: Some("project_fact".to_owned()),
            revision: RevisionPrecondition::MustNotExist,
            trace: hypermid_contracts::Trace::new(id("trace-1"), id("request-1")),
        }
    }

    fn principal(owner: &str, principal_id: &str, kind: PrincipalKind) -> AuthenticatedPrincipal {
        AuthenticatedPrincipal {
            principal_id: id(principal_id),
            owner_id: id(owner),
            kind,
        }
    }

    fn auth_context(
        capability_id: &str,
        principal: AuthenticatedPrincipal,
        request: &MutationRequest,
        now_ms: u64,
    ) -> AuthContext {
        AuthContext {
            principal,
            request: AuthorizationRequest {
                claimed_scope: request.actor_scope.clone(),
                target_scope: request.target_scope.clone(),
                operation: crate::capability_operation(request.operation),
                resource_id: request.record_id.clone().unwrap(),
                now_ms,
            },
            capability_id: id(capability_id),
        }
    }

    fn install_capability(
        store: &mut MemoryStore,
        issuer: &AuthenticatedPrincipal,
        context: &AuthContext,
        expires_at_ms: u64,
    ) {
        let grant = CapabilityGrant {
            capability_id: context.capability_id.clone(),
            issuer_owner_id: context.request.target_scope.owner_id.clone(),
            principal_id: context.principal.principal_id.clone(),
            claimed_scope: context.request.claimed_scope.clone(),
            target_scope: context.request.target_scope.clone(),
            operations: BTreeSet::from([context.request.operation]),
            resources: BTreeSet::from([context.request.resource_id.clone()]),
            expires_at_ms,
        };
        store
            .immediate(|transaction| {
                put_capability(transaction.raw(), issuer, &grant).unwrap();
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn foundation_installs_the_authoritative_schema_and_fences_newer_versions() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("memory.sqlite3");
        let store = MemoryStore::open(&path).unwrap();
        let required = [
            "hypermid_schema_version",
            "hypermid_migration_journal",
            "memory_scopes",
            "memory_share_grants",
            "memory_records",
            "memory_revisions",
            "episode_details",
            "smart_note_details",
            "summary_details",
            "memory_retrieval_stats",
            "memory_sources",
            "memory_provenance",
            "memory_lineage",
            "memory_verification_events",
            "memory_sharing_judgments",
            "memory_mutation_events",
            "embedding_registrations",
            "memory_embeddings",
            "source_index_documents",
            "memory_fts",
            "memory_fts_rows",
            "source_fts",
            "source_fts_rows",
            "source_index_state",
            "file_predicate_decisions",
            "maintenance_jobs",
            "embedding_jobs",
            "maintenance_leases",
            "import_batches",
            "import_items",
            "export_manifests",
            "recovery_snapshots",
        ];
        store
            .read(|connection| {
                for table in required {
                    let present: i64 = connection
                        .query_row(
                            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?1",
                            [table],
                            |row| row.get(0),
                        )
                        .map_err(read_error)?;
                    assert_eq!(present, 1, "missing runtime table {table}");
                }
                Ok(())
            })
            .unwrap();
        drop(store);

        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE hypermid_schema_version SET current_version=3 WHERE singleton=1",
                [],
            )
            .unwrap();
        drop(connection);
        let error = match MemoryStore::open(&path) {
            Ok(_) => panic!("newer schema unexpectedly opened"),
            Err(error) => error,
        };
        assert_eq!(error.code, "SCHEMA_NEWER_UNSUPPORTED");
    }

    #[test]
    fn foundation_upgrades_the_immutable_v1_chain_to_sharing_judgments() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("memory.sqlite3");
        let migrations = memory_migrations();
        let lease_key = LeaseKey {
            module_id: id("hypermid-memory"),
            backend: BackendKind::Sqlite,
            scope_key: id("memory-store"),
        };
        let v1 = SQLiteStore::open(&path, lease_key, 1, &migrations[..1]).unwrap();
        assert_eq!(
            v1.read(|connection| {
                Ok(connection.query_row(
                    "SELECT current_version FROM hypermid_schema_version WHERE singleton=1",
                    [],
                    |row| row.get::<_, u64>(0),
                )?)
            })
            .unwrap(),
            1
        );
        drop(v1);

        let upgraded = MemoryStore::open(&path).unwrap();
        let evidence = upgraded.schema_evidence().unwrap();
        assert_eq!(evidence.current_version, MEMORY_SCHEMA_VERSION);
        assert_eq!(evidence.schema_digest, schema_digest());
        upgraded
            .read(|connection| {
                let present: i64 = connection
                    .query_row(
                        "SELECT count(*) FROM sqlite_master \
                         WHERE type='table' AND name='memory_sharing_judgments'",
                        [],
                        |row| row.get(0),
                    )
                    .map_err(read_error)?;
                assert_eq!(present, 1);
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn foundation_requires_owner_or_exact_live_grant_for_every_actor() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("memory.sqlite3");
        let mut store = MemoryStore::open(path).unwrap();
        let target = scope("alice", "project-a");
        let interactive = scope("alice", "project-b");
        let maintenance = scope("worker", "project-a");
        store.ensure_scope(&target, 1_000).unwrap();
        store.ensure_scope(&interactive, 1_000).unwrap();
        store.ensure_scope(&maintenance, 1_000).unwrap();

        let owner_request = request(target.clone(), target.clone(), Operation::Update);
        let owner_principal = principal("alice", "principal-alice", PrincipalKind::Foreground);
        let owner_context = auth_context(
            "owner-capability",
            owner_principal.clone(),
            &owner_request,
            2_000,
        );
        install_capability(&mut store, &owner_principal, &owner_context, 5_000);
        assert_eq!(
            store
                .authorize(&owner_context, &owner_request)
                .unwrap()
                .basis,
            crate::AuthorizationBasis::Owner
        );

        for actor in [&interactive, &maintenance] {
            let denied_request = request(actor.clone(), target.clone(), Operation::Update);
            let context = auth_context(
                "missing-capability",
                principal(
                    actor.owner_id.as_str(),
                    "principal-denied",
                    PrincipalKind::Foreground,
                ),
                &denied_request,
                2_000,
            );
            let failure = store.authorize(&context, &denied_request).unwrap_err();
            assert_eq!(failure.code, "AUTHORIZATION_DENIED");
        }

        let grant = ShareGrant {
            id: id("grant-1"),
            owner_scope: target.clone(),
            grantee_scope: maintenance.clone(),
            operations: BTreeSet::from([GrantOperation::Update]),
            categories: Some(BTreeSet::from(["project_fact".to_owned()])),
            granted_at_ms: 1_000,
            expires_at_ms: Some(5_000),
            revoked_at_ms: None,
            revision: 1,
        };
        store.put_share_grant(&target, &grant, 1_000).unwrap();
        let authorized_request = request(maintenance.clone(), target.clone(), Operation::Update);
        let worker = principal("worker", "principal-worker", PrincipalKind::Background);
        let issuer = principal("alice", "principal-owner", PrincipalKind::Foreground);
        let context = auth_context("grant-1", worker, &authorized_request, 2_000);
        install_capability(&mut store, &issuer, &context, 5_000);
        let ticket = store.authorize(&context, &authorized_request).unwrap();
        assert_eq!(ticket.basis, crate::AuthorizationBasis::Grant);
        assert_eq!(ticket.grant.as_ref().unwrap().revision, 1);

        let wrong_request = request(maintenance.clone(), target.clone(), Operation::Delete);
        let wrong_context =
            auth_context("grant-1", context.principal.clone(), &wrong_request, 2_000);
        let wrong_operation = store.authorize(&wrong_context, &wrong_request).unwrap_err();
        assert_eq!(wrong_operation.code, "AUTHORIZATION_DENIED");
        let expired_context = auth_context(
            "grant-1",
            context.principal.clone(),
            &authorized_request,
            5_000,
        );
        let expired = store
            .authorize(&expired_context, &authorized_request)
            .unwrap_err();
        assert_eq!(expired.code, "AUTHORIZATION_DENIED");

        let portability_request = MutationRequest {
            operation: Operation::Export,
            actor_scope: target.clone(),
            target_scope: target.clone(),
            record_id: None,
            category: None,
            revision: RevisionPrecondition::MustNotExist,
            trace: hypermid_contracts::Trace::new(id("trace-export"), id("request-export")),
        };
        let portability_context = AuthContext {
            principal: owner_principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: target.clone(),
                target_scope: target.clone(),
                operation: hypermid_core::capability::CapabilityOperation::Export,
                resource_id: id("memory-portability"),
                now_ms: 2_000,
            },
            capability_id: id("portability-capability"),
        };
        let portability_grant = CapabilityGrant {
            capability_id: portability_context.capability_id.clone(),
            issuer_owner_id: target.owner_id.clone(),
            principal_id: owner_principal.principal_id.clone(),
            claimed_scope: target.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([hypermid_core::capability::CapabilityOperation::Export]),
            resources: BTreeSet::from([id("memory-portability")]),
            expires_at_ms: 5_000,
        };
        store
            .immediate(|transaction| {
                put_capability(transaction.raw(), &owner_principal, &portability_grant).unwrap();
                Ok(())
            })
            .unwrap();
        assert!(store
            .authorize(&portability_context, &portability_request)
            .is_ok());
        let mut wrong_resource = portability_context;
        wrong_resource.request.resource_id = id("unbound-resource");
        assert_eq!(
            store
                .authorize(&wrong_resource, &portability_request)
                .unwrap_err()
                .code,
            "AUTHORIZATION_DENIED"
        );

        let queue_request = MutationRequest {
            operation: Operation::Summarize,
            actor_scope: target.clone(),
            target_scope: target.clone(),
            record_id: None,
            category: None,
            revision: RevisionPrecondition::MustNotExist,
            trace: hypermid_contracts::Trace::new(id("trace-queue"), id("request-queue")),
        };
        let queue_context = AuthContext {
            principal: owner_principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: target.clone(),
                target_scope: target.clone(),
                operation: hypermid_core::capability::CapabilityOperation::Revise,
                resource_id: id("memory-maintenance"),
                now_ms: 2_000,
            },
            capability_id: id("maintenance-capability"),
        };
        let queue_grant = CapabilityGrant {
            capability_id: queue_context.capability_id.clone(),
            issuer_owner_id: target.owner_id.clone(),
            principal_id: owner_principal.principal_id.clone(),
            claimed_scope: target.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([hypermid_core::capability::CapabilityOperation::Revise]),
            resources: BTreeSet::from([id("memory-maintenance")]),
            expires_at_ms: 5_000,
        };
        store
            .immediate(|transaction| {
                put_capability(transaction.raw(), &owner_principal, &queue_grant).unwrap();
                Ok(())
            })
            .unwrap();
        assert!(store.authorize(&queue_context, &queue_request).is_ok());
    }

    #[test]
    fn foundation_cross_owner_read_requires_both_live_grants() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let actor = scope("reader", "project-a");
        let target = scope("owner", "project-b");
        let resource = id("record-1");
        store.ensure_scope(&actor, 1_000).unwrap();
        store.ensure_scope(&target, 1_000).unwrap();

        let access = crate::AccessRequest {
            operation: GrantOperation::Read,
            actor_scope: actor.clone(),
            target_scope: target.clone(),
            resource_id: resource.clone(),
            category: Some("project_fact".to_owned()),
            trace: hypermid_contracts::Trace::new(id("trace-read"), id("request-read")),
        };
        let reader = principal("reader", "principal-reader", PrincipalKind::Foreground);
        let issuer = principal("owner", "principal-owner", PrincipalKind::Foreground);
        let context = AuthContext {
            principal: reader.clone(),
            request: AuthorizationRequest {
                claimed_scope: actor.clone(),
                target_scope: target.clone(),
                operation: hypermid_core::capability::CapabilityOperation::Read,
                resource_id: resource,
                now_ms: 2_000,
            },
            capability_id: id("read-grant"),
        };
        let capability = CapabilityGrant {
            capability_id: context.capability_id.clone(),
            issuer_owner_id: target.owner_id.clone(),
            principal_id: reader.principal_id.clone(),
            claimed_scope: actor.clone(),
            target_scope: target.clone(),
            operations: BTreeSet::from([hypermid_core::capability::CapabilityOperation::Read]),
            resources: BTreeSet::from([context.request.resource_id.clone()]),
            expires_at_ms: 5_000,
        };
        store
            .immediate(|transaction| {
                put_capability(transaction.raw(), &issuer, &capability).unwrap();
                Ok(())
            })
            .unwrap();
        let share = ShareGrant {
            id: context.capability_id.clone(),
            owner_scope: target.clone(),
            grantee_scope: actor,
            operations: BTreeSet::from([GrantOperation::Read]),
            categories: Some(BTreeSet::from(["project_fact".to_owned()])),
            granted_at_ms: 1_000,
            expires_at_ms: Some(5_000),
            revoked_at_ms: None,
            revision: 1,
        };
        store.put_share_grant(&target, &share, 1_000).unwrap();
        let authorization = store
            .immediate_access(&context, &access, |_, authorization| {
                Ok(authorization.clone())
            })
            .unwrap();
        assert_eq!(authorization.basis, crate::AuthorizationBasis::Grant);

        store
            .revoke_share_grant(&target, &share.id, 1, 2_500)
            .unwrap();
        let denied = store
            .immediate_access(&context, &access, |_, _| Ok(()))
            .unwrap_err();
        assert_eq!(denied.code, "AUTHORIZATION_DENIED");
    }

    #[test]
    fn foundation_collection_capability_is_same_project_only() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = MemoryStore::open(directory.path().join("memory.sqlite3")).unwrap();
        let owner = scope("owner", "project-a");
        let foreign = scope("reader", "project-b");
        store.ensure_scope(&owner, 1_000).unwrap();
        store.ensure_scope(&foreign, 1_000).unwrap();

        let owner_request = request(owner.clone(), owner.clone(), Operation::Create);
        let owner_principal = principal("owner", "principal-owner", PrincipalKind::Foreground);
        let collection_context = AuthContext {
            principal: owner_principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: owner.clone(),
                target_scope: owner.clone(),
                operation: hypermid_core::capability::CapabilityOperation::Append,
                resource_id: id("memory-records"),
                now_ms: 2_000,
            },
            capability_id: id("collection-capability"),
        };
        let collection_grant = CapabilityGrant {
            capability_id: collection_context.capability_id.clone(),
            issuer_owner_id: owner.owner_id.clone(),
            principal_id: owner_principal.principal_id.clone(),
            claimed_scope: owner.clone(),
            target_scope: owner.clone(),
            operations: BTreeSet::from([
                hypermid_core::capability::CapabilityOperation::Append,
                hypermid_core::capability::CapabilityOperation::Read,
            ]),
            resources: BTreeSet::from([id("memory-records")]),
            expires_at_ms: 5_000,
        };
        store
            .immediate(|transaction| {
                put_capability(transaction.raw(), &owner_principal, &collection_grant).unwrap();
                Ok(())
            })
            .unwrap();
        assert!(store.authorize(&collection_context, &owner_request).is_ok());
        let owner_collection_access = AccessRequest {
            operation: GrantOperation::Read,
            actor_scope: owner.clone(),
            target_scope: owner.clone(),
            resource_id: id("memory-records"),
            category: None,
            trace: hypermid_contracts::Trace::new(id("trace-owner-read"), id("request-owner-read")),
        };
        let owner_read_context = AuthContext {
            principal: owner_principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: owner.clone(),
                target_scope: owner.clone(),
                operation: hypermid_core::capability::CapabilityOperation::Read,
                resource_id: id("memory-records"),
                now_ms: 2_000,
            },
            capability_id: collection_context.capability_id,
        };
        assert!(store
            .immediate_access(&owner_read_context, &owner_collection_access, |_, _| Ok(()))
            .is_ok());
        let owner_search_access = AccessRequest {
            operation: GrantOperation::Search,
            trace: hypermid_contracts::Trace::new(
                id("trace-owner-search"),
                id("request-owner-search"),
            ),
            ..owner_collection_access
        };
        assert!(store
            .immediate_access(&owner_read_context, &owner_search_access, |_, _| Ok(()))
            .is_ok());

        let foreign_request = request(foreign.clone(), owner.clone(), Operation::Create);
        let foreign_principal = principal("reader", "principal-reader", PrincipalKind::Foreground);
        let foreign_context = AuthContext {
            principal: foreign_principal.clone(),
            request: AuthorizationRequest {
                claimed_scope: foreign.clone(),
                target_scope: owner.clone(),
                operation: hypermid_core::capability::CapabilityOperation::Append,
                resource_id: id("memory-records"),
                now_ms: 2_000,
            },
            capability_id: id("foreign-collection-capability"),
        };
        let foreign_capability = CapabilityGrant {
            capability_id: foreign_context.capability_id.clone(),
            issuer_owner_id: owner.owner_id.clone(),
            principal_id: foreign_principal.principal_id.clone(),
            claimed_scope: foreign.clone(),
            target_scope: owner.clone(),
            operations: BTreeSet::from([
                hypermid_core::capability::CapabilityOperation::Append,
                hypermid_core::capability::CapabilityOperation::Read,
            ]),
            resources: BTreeSet::from([id("memory-records")]),
            expires_at_ms: 5_000,
        };
        store
            .immediate(|transaction| {
                put_capability(transaction.raw(), &owner_principal, &foreign_capability).unwrap();
                Ok(())
            })
            .unwrap();
        store
            .put_share_grant(
                &owner,
                &ShareGrant {
                    id: foreign_context.capability_id.clone(),
                    owner_scope: owner.clone(),
                    grantee_scope: foreign,
                    operations: BTreeSet::from([GrantOperation::Create, GrantOperation::Read]),
                    categories: Some(BTreeSet::from(["project_fact".to_owned()])),
                    granted_at_ms: 1_000,
                    expires_at_ms: Some(5_000),
                    revoked_at_ms: None,
                    revision: 1,
                },
                1_000,
            )
            .unwrap();
        assert_eq!(
            store
                .authorize(&foreign_context, &foreign_request)
                .unwrap_err()
                .code,
            "AUTHORIZATION_DENIED"
        );
        let foreign_collection_access = AccessRequest {
            operation: GrantOperation::Read,
            actor_scope: foreign_context.request.claimed_scope.clone(),
            target_scope: owner,
            resource_id: id("memory-records"),
            category: None,
            trace: hypermid_contracts::Trace::new(
                id("trace-foreign-read"),
                id("request-foreign-read"),
            ),
        };
        let foreign_read_context = AuthContext {
            principal: foreign_principal,
            request: AuthorizationRequest {
                claimed_scope: foreign_context.request.claimed_scope,
                target_scope: foreign_context.request.target_scope,
                operation: hypermid_core::capability::CapabilityOperation::Read,
                resource_id: id("memory-records"),
                now_ms: 2_000,
            },
            capability_id: foreign_context.capability_id,
        };
        assert_eq!(
            store
                .immediate_access(
                    &foreign_read_context,
                    &foreign_collection_access,
                    |_, _| Ok(())
                )
                .unwrap_err()
                .code,
            "AUTHORIZATION_DENIED"
        );
    }

    #[test]
    fn foundation_rolls_back_cursor_when_revision_or_live_grant_check_fails() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("memory.sqlite3");
        let mut store = MemoryStore::open(path).unwrap();
        let target = scope("alice", "project-a");
        let worker = scope("worker", "project-a");
        store.ensure_scope(&target, 1_000).unwrap();
        store.ensure_scope(&worker, 1_000).unwrap();

        let missing = id("missing-record");
        let expected = hypermid_contracts::Digest::sha256(b"expected");
        let failure = store
            .immediate(|transaction| {
                transaction.advance_cursor(&target, 2_000)?;
                transaction
                    .require_record_revision(&missing, &RevisionPrecondition::Match(expected))?;
                Ok(())
            })
            .unwrap_err();
        assert_eq!(failure.code, "REVISION_CONFLICT");
        assert_eq!(store.cursor(&target).unwrap(), Cursor::new(1, 0).unwrap());

        let grant = ShareGrant {
            id: id("grant-1"),
            owner_scope: target.clone(),
            grantee_scope: worker.clone(),
            operations: BTreeSet::from([GrantOperation::Summarize]),
            categories: None,
            granted_at_ms: 1_000,
            expires_at_ms: Some(10_000),
            revoked_at_ms: None,
            revision: 1,
        };
        store.put_share_grant(&target, &grant, 1_000).unwrap();
        let authorized_request = request(worker, target.clone(), Operation::Summarize);
        let issuer = principal("alice", "principal-owner", PrincipalKind::Foreground);
        let context = auth_context(
            "grant-1",
            principal("worker", "principal-worker", PrincipalKind::Background),
            &authorized_request,
            2_000,
        );
        install_capability(&mut store, &issuer, &context, 10_000);
        let ticket = store.authorize(&context, &authorized_request).unwrap();
        store
            .revoke_share_grant(&target, &grant.id, 1, 2_500)
            .unwrap();
        let failure = store
            .immediate(|transaction| {
                transaction.advance_cursor(&target, 3_000)?;
                transaction.reauthorize(&context, &authorized_request, &ticket)?;
                Ok(())
            })
            .unwrap_err();
        assert_eq!(failure.code, "AUTHORIZATION_DENIED");
        assert_eq!(store.cursor(&target).unwrap(), Cursor::new(1, 0).unwrap());
    }
}
