use std::{collections::BTreeSet, path::Path, sync::Mutex};

use hypermid_contracts::{Digest, EffectState, Error, Id, Scope, Trace};
use hypermid_core::{
    capability::{AuthorizationRequest, CapabilityOperation},
    provenance::{MemoryProvenance, ProvenanceError, TrustClass, VerificationState},
};
use hypermid_protocol::Envelope;
use hypermid_transport::AuthenticatedSession;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;

use crate::{
    dispatch::Dispatcher,
    egress::{
        AddressClass, ConnectionAttempt, Destination, EgressAuthorityError, EgressAuthorization,
        EgressGrant, EgressGrantAuthority, EgressPolicy, EgressRequestContext, ProxyPolicy,
        EGRESS_DENIED,
    },
};

pub const SECURITY_OPERATIONS: &[&str] = &[
    "security.network.status",
    "security.network.grant.put",
    "security.network.revoke",
    "security.memory.provenance",
    "security.memory.promote",
];

const SCHEMA_VERSION: i64 = 1;
const DECISION_LIMIT: i64 = 100;

#[derive(Clone, Debug)]
pub struct SecurityRouteResponse {
    pub payload: Option<Value>,
    pub error: Option<Error>,
}

impl EgressGrantAuthority for SecurityRoutes {
    fn authorize(
        &self,
        context: &EgressRequestContext,
        attempt: ConnectionAttempt<'_>,
    ) -> Result<EgressAuthorization, EgressAuthorityError> {
        let checked_at_ms = attempt.now_ms;
        if attempt.principal_id != context.principal_id().as_str()
            || attempt.operation != context.operation()
        {
            return Err(EgressAuthorityError::authorization_denied());
        }
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| EgressAuthorityError::unavailable())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|_| EgressAuthorityError::unavailable())?;
        let grants = load_active_grants(&transaction, context.scope(), attempt.now_ms)
            .map_err(|_| EgressAuthorityError::unavailable())?;
        let grant = grants.iter().find(|grant| {
            grant.principal_id == context.principal_id().as_str()
                && grant.operation == context.operation()
                && grant.scheme == attempt.destination.scheme
                && grant.hostname == attempt.destination.hostname
                && grant.ports.contains(&attempt.destination.port)
        });
        let result = if let Some(grant) = grant {
            let capability = self
                .capability_connection
                .lock()
                .map_err(|_| EgressAuthorityError::unavailable())?;
            let resource_id =
                Id::new(grant.grant_id.clone()).map_err(|_| EgressAuthorityError::unavailable())?;
            let request = AuthorizationRequest {
                claimed_scope: context.scope().clone(),
                target_scope: context.scope().clone(),
                operation: CapabilityOperation::NetworkUse,
                resource_id,
                now_ms: attempt.now_ms,
            };
            if Dispatcher
                .authorize_session(
                    &capability,
                    context.session(),
                    request,
                    context.capability_id().clone(),
                )
                .is_err()
            {
                Err(EgressAuthorityError::authorization_denied())
            } else {
                self.policy
                    .authorize_attempt(Some(grant), attempt)
                    .map_err(EgressAuthorityError::policy)
            }
        } else {
            self.policy
                .authorize_attempt(None, attempt)
                .map_err(EgressAuthorityError::policy)
        };
        let (grant_id, code, rule, allowed) = match &result {
            Ok(value) => (
                Some(value.grant_id.as_str()),
                "EGRESS_ALLOWED",
                "grant.authorized",
                true,
            ),
            Err(error) => (
                grant.map(|item| item.grant_id.as_str()),
                error.code,
                error.rule,
                false,
            ),
        };
        record_decision(
            &transaction,
            context.scope(),
            grant_id,
            code,
            rule,
            allowed,
            checked_at_ms,
        )
        .map_err(|_| EgressAuthorityError::unavailable())?;
        transaction
            .commit()
            .map_err(|_| EgressAuthorityError::unavailable())?;
        result
    }
}

pub struct SecurityRoutes {
    connection: Mutex<Connection>,
    capability_connection: Mutex<Connection>,
    policy: EgressPolicy,
}

impl SecurityRoutes {
    pub fn open(
        path: impl AsRef<Path>,
        capability_path: impl AsRef<Path>,
    ) -> Result<Self, SecurityRouteError> {
        let connection = Connection::open(path)?;
        let capability_connection = Connection::open(capability_path)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.execute_batch(
            "BEGIN IMMEDIATE;
             CREATE TABLE IF NOT EXISTS security_metadata (
                 key TEXT PRIMARY KEY,
                 value INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS network_grants (
                 owner_id TEXT NOT NULL,
                 project_id TEXT NOT NULL,
                 workspace_id TEXT NOT NULL,
                 grant_id TEXT NOT NULL,
                 grant_json TEXT NOT NULL,
                 revoked_at_ms INTEGER,
                 PRIMARY KEY(owner_id, project_id, workspace_id, grant_id)
             );
             CREATE INDEX IF NOT EXISTS network_grants_lookup
                 ON network_grants(owner_id, project_id, workspace_id, revoked_at_ms);
             CREATE TABLE IF NOT EXISTS network_decisions (
                 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                 owner_id TEXT NOT NULL,
                 project_id TEXT NOT NULL,
                 workspace_id TEXT NOT NULL,
                 grant_id TEXT,
                 code TEXT NOT NULL,
                 rule TEXT NOT NULL,
                 allowed INTEGER NOT NULL CHECK(allowed IN (0, 1)),
                 checked_at_ms INTEGER NOT NULL,
                 metadata_json TEXT NOT NULL
             );
             CREATE INDEX IF NOT EXISTS network_decisions_scope
                 ON network_decisions(owner_id, project_id, workspace_id, sequence DESC);
             CREATE TABLE IF NOT EXISTS memory_trust_provenance (
                 owner_id TEXT NOT NULL,
                 project_id TEXT NOT NULL,
                 workspace_id TEXT NOT NULL,
                 memory_id TEXT NOT NULL,
                 provenance_json TEXT NOT NULL,
                 instruction_shaped INTEGER NOT NULL CHECK(instruction_shaped IN (0, 1)),
                 PRIMARY KEY(owner_id, project_id, workspace_id, memory_id)
             );
             INSERT INTO security_metadata(key, value) VALUES('schema_version', 1)
                 ON CONFLICT(key) DO NOTHING;
             COMMIT;",
        )?;
        let version: i64 = connection.query_row(
            "SELECT value FROM security_metadata WHERE key = 'schema_version'",
            [],
            |row| row.get(0),
        )?;
        if version != SCHEMA_VERSION {
            return Err(SecurityRouteError::SchemaVersion(version));
        }
        Ok(Self {
            connection: Mutex::new(connection),
            capability_connection: Mutex::new(capability_connection),
            policy: EgressPolicy,
        })
    }

    pub fn handles(operation: &str) -> bool {
        SECURITY_OPERATIONS.contains(&operation)
    }

    pub fn dispatch(
        &self,
        session: &AuthenticatedSession,
        envelope: &Envelope,
        now_ms: u64,
    ) -> SecurityRouteResponse {
        if envelope.scope.as_ref() != Some(&session.bound_scope) {
            return failure(SecurityRouteError::ScopeDenied);
        }
        let Some(operation) = envelope.operation.as_deref() else {
            return failure(SecurityRouteError::InvalidRequest(
                "security operation is required",
            ));
        };
        if !Self::handles(operation) {
            return failure(SecurityRouteError::UnknownOperation);
        }
        let payload = envelope.payload.clone().unwrap_or_else(|| json!({}));
        let result = match operation {
            "security.network.status" => parse::<EmptyPayload>(payload)
                .and_then(|_| self.network_status(&session.bound_scope, now_ms)),
            "security.network.grant.put" => parse::<PutGrantPayload>(payload)
                .and_then(|payload| self.put_grant(&session.bound_scope, payload.grant, now_ms)),
            "security.network.revoke" => parse::<GrantIdPayload>(payload).and_then(|payload| {
                self.revoke_grant(&session.bound_scope, &payload.grant_id, now_ms)
            }),
            "security.memory.provenance" => parse::<MemoryIdPayload>(payload).and_then(|payload| {
                self.lookup_provenance(&session.bound_scope, &payload.memory_id)
            }),
            "security.memory.promote" => {
                let Some(trace) = envelope.trace.clone() else {
                    return failure(SecurityRouteError::InvalidRequest(
                        "memory promotion requires a trace",
                    ));
                };
                parse::<PromotePayload>(payload).and_then(|payload| {
                    self.promote_provenance(
                        &session.bound_scope,
                        &payload.memory_id,
                        payload.expected_revision,
                        payload.expected_digest,
                        session.accepted.principal.id.clone(),
                        now_ms,
                        trace,
                    )
                })
            }
            _ => unreachable!("security operation catalog and dispatch remain aligned"),
        };
        match result {
            Ok(payload) => SecurityRouteResponse {
                payload: Some(payload),
                error: None,
            },
            Err(error) => failure(error),
        }
    }

    pub fn record_provenance(
        &self,
        scope: &Scope,
        memory_id: &Id,
        incoming: &MemoryProvenance,
        instruction_shaped: bool,
    ) -> Result<MemoryProvenance, SecurityRouteError> {
        validate_provenance(incoming)?;
        let mut connection = self.lock()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let existing = load_provenance(&transaction, scope, memory_id)?;
        let stored = match existing {
            None => {
                let mut current = incoming.clone();
                current.trust_class = TrustClass::Data;
                current.promotion = None;
                current
            }
            Some((mut current, _)) if current.content_digest != incoming.content_digest => {
                current.revision = current
                    .revision
                    .checked_add(1)
                    .ok_or(SecurityRouteError::RevisionExhausted)?;
                current.content_digest = incoming.content_digest;
                current.verification = VerificationState::Unverified;
                current.classified_digest = None;
                current.embedding_digest = None;
                current.trust_class = TrustClass::Data;
                current.promotion = None;
                current
            }
            Some((current, _)) => current,
        };
        let encoded = serde_json::to_string(&stored)?;
        let (owner, project, workspace) = scope_key(scope);
        transaction.execute(
            "INSERT INTO memory_trust_provenance(
                 owner_id, project_id, workspace_id, memory_id, provenance_json, instruction_shaped
             ) VALUES(?1, ?2, ?3, ?4, ?5, ?6)
             ON CONFLICT(owner_id, project_id, workspace_id, memory_id) DO UPDATE SET
                 provenance_json = excluded.provenance_json,
                 instruction_shaped = excluded.instruction_shaped",
            params![
                owner,
                project,
                workspace,
                memory_id.as_str(),
                encoded,
                instruction_shaped
            ],
        )?;
        transaction.commit()?;
        Ok(stored)
    }

    fn put_grant(
        &self,
        scope: &Scope,
        input: GrantInput,
        now_ms: u64,
    ) -> Result<Value, SecurityRouteError> {
        let stored = StoredGrant::try_from(input)?;
        let grant = stored.to_egress_grant()?;
        grant.validate().map_err(SecurityRouteError::Egress)?;
        if grant.expires_at_ms <= now_ms {
            return Err(SecurityRouteError::InvalidRequest(
                "network grant expiry must be in the future",
            ));
        }
        let encoded = serde_json::to_string(&stored)?;
        let mut connection = self.lock()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (owner, project, workspace) = scope_key(scope);
        transaction.execute(
            "INSERT INTO network_grants(
                 owner_id, project_id, workspace_id, grant_id, grant_json, revoked_at_ms
             ) VALUES(?1, ?2, ?3, ?4, ?5, NULL)
             ON CONFLICT(owner_id, project_id, workspace_id, grant_id) DO UPDATE SET
                 grant_json = excluded.grant_json,
                 revoked_at_ms = NULL",
            params![owner, project, workspace, grant.grant_id, encoded],
        )?;
        transaction.commit()?;
        drop(connection);
        self.network_status(scope, now_ms)
    }

    fn revoke_grant(
        &self,
        scope: &Scope,
        grant_id: &Id,
        now_ms: u64,
    ) -> Result<Value, SecurityRouteError> {
        let now = to_i64(now_ms)?;
        let mut connection = self.lock()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (owner, project, workspace) = scope_key(scope);
        let changed = transaction.execute(
            "UPDATE network_grants SET revoked_at_ms = ?5
             WHERE owner_id = ?1 AND project_id = ?2 AND workspace_id = ?3
               AND grant_id = ?4 AND revoked_at_ms IS NULL",
            params![owner, project, workspace, grant_id.as_str(), now],
        )?;
        if changed == 0 {
            return Err(SecurityRouteError::GrantNotFound);
        }
        transaction.commit()?;
        drop(connection);
        self.network_status(scope, now_ms)
    }

    fn network_status(&self, scope: &Scope, now_ms: u64) -> Result<Value, SecurityRouteError> {
        let connection = self.lock()?;
        let grants = load_active_grants(&connection, scope, now_ms)?;
        let (owner, project, workspace) = scope_key(scope);
        let mut statement = connection.prepare(
            "SELECT code, rule, allowed, checked_at_ms
             FROM network_decisions
             WHERE owner_id = ?1 AND project_id = ?2 AND workspace_id = ?3
             ORDER BY sequence DESC LIMIT ?4",
        )?;
        let rows =
            statement.query_map(params![owner, project, workspace, DECISION_LIMIT], |row| {
                let checked_at_ms: i64 = row.get(3)?;
                Ok(json!({
                    "code": row.get::<_, String>(0)?,
                    "rule": row.get::<_, String>(1)?,
                    "allowed": row.get::<_, bool>(2)?,
                    "checkedAtMs": checked_at_ms,
                }))
            })?;
        let decisions = rows.collect::<Result<Vec<_>, _>>()?;
        Ok(json!({
            "scope": scope,
            "checkedAtMs": now_ms,
            "grants": grants.iter().map(grant_status).collect::<Vec<_>>(),
            "decisions": decisions,
        }))
    }

    fn lookup_provenance(
        &self,
        scope: &Scope,
        memory_id: &Id,
    ) -> Result<Value, SecurityRouteError> {
        let connection = self.lock()?;
        let Some((provenance, instruction_shaped)) =
            load_provenance(&connection, scope, memory_id)?
        else {
            return Err(SecurityRouteError::ProvenanceNotFound);
        };
        Ok(provenance_status(
            memory_id,
            &provenance,
            instruction_shaped,
        ))
    }

    #[allow(clippy::too_many_arguments)]
    fn promote_provenance(
        &self,
        scope: &Scope,
        memory_id: &Id,
        expected_revision: u64,
        expected_digest: Digest,
        reviewer: Id,
        now_ms: u64,
        trace: Trace,
    ) -> Result<Value, SecurityRouteError> {
        let mut connection = self.lock()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let Some((mut provenance, instruction_shaped)) =
            load_provenance(&transaction, scope, memory_id)?
        else {
            return Err(SecurityRouteError::ProvenanceNotFound);
        };
        if provenance.revision != expected_revision || provenance.content_digest != expected_digest
        {
            return Err(SecurityRouteError::StaleMemoryRevision);
        }
        let previous_encoded = serde_json::to_string(&provenance)?;
        provenance
            .promote(expected_revision, reviewer, now_ms, trace)
            .map_err(SecurityRouteError::Provenance)?;
        if !provenance.is_privileged() {
            return Err(SecurityRouteError::StaleMemoryRevision);
        }
        let encoded = serde_json::to_string(&provenance)?;
        let (owner, project, workspace) = scope_key(scope);
        let changed = transaction.execute(
            "UPDATE memory_trust_provenance SET provenance_json = ?5
             WHERE owner_id = ?1 AND project_id = ?2 AND workspace_id = ?3
               AND memory_id = ?4 AND provenance_json = ?6",
            params![
                owner,
                project,
                workspace,
                memory_id.as_str(),
                encoded,
                previous_encoded,
            ],
        )?;
        if changed != 1 {
            return Err(SecurityRouteError::StaleMemoryRevision);
        }
        transaction.commit()?;
        Ok(provenance_status(
            memory_id,
            &provenance,
            instruction_shaped,
        ))
    }

    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Connection>, SecurityRouteError> {
        self.connection.lock().map_err(|_| SecurityRouteError::Lock)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct GrantInput {
    grant_id: Id,
    principal_id: Id,
    operation: String,
    scheme: String,
    hostname: String,
    ports: BTreeSet<u16>,
    address_classes: BTreeSet<String>,
    proxy_policy: String,
    proxy_configuration: Option<ProxyConfigurationInput>,
    redirect_limit: u8,
    byte_limit: u64,
    expires_at_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct ProxyConfigurationInput {
    scheme: String,
    hostname: String,
    port: u16,
    address_classes: BTreeSet<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredGrant {
    grant_id: Id,
    principal_id: Id,
    operation: String,
    scheme: String,
    hostname: String,
    ports: BTreeSet<u16>,
    address_classes: BTreeSet<String>,
    proxy_policy: String,
    proxy_configuration: Option<ProxyConfigurationInput>,
    redirect_limit: u8,
    byte_limit: u64,
    expires_at_ms: u64,
}

impl TryFrom<GrantInput> for StoredGrant {
    type Error = SecurityRouteError;

    fn try_from(value: GrantInput) -> Result<Self, Self::Error> {
        let destination = Destination::normalized(&value.scheme, &value.hostname, 1)
            .map_err(SecurityRouteError::Egress)?;
        Ok(Self {
            grant_id: value.grant_id,
            principal_id: value.principal_id,
            operation: value.operation,
            scheme: destination.scheme,
            hostname: destination.hostname,
            ports: value.ports,
            address_classes: value.address_classes,
            proxy_policy: value.proxy_policy,
            proxy_configuration: value.proxy_configuration,
            redirect_limit: value.redirect_limit,
            byte_limit: value.byte_limit,
            expires_at_ms: value.expires_at_ms,
        })
    }
}

impl StoredGrant {
    fn to_egress_grant(&self) -> Result<EgressGrant, SecurityRouteError> {
        let proxy_policy = match (self.proxy_policy.as_str(), &self.proxy_configuration) {
            ("direct", None) => ProxyPolicy::DirectOnly,
            ("required", Some(proxy)) => {
                let destination =
                    Destination::normalized(&proxy.scheme, &proxy.hostname, proxy.port)
                        .map_err(SecurityRouteError::Egress)?;
                ProxyPolicy::Required {
                    scheme: destination.scheme,
                    hostname: destination.hostname,
                    port: destination.port,
                    address_classes: parse_address_classes(&proxy.address_classes)?,
                }
            }
            _ => {
                return Err(SecurityRouteError::InvalidRequest(
                    "proxy policy is invalid",
                ))
            }
        };
        Ok(EgressGrant {
            grant_id: self.grant_id.to_string(),
            principal_id: self.principal_id.to_string(),
            operation: self.operation.clone(),
            scheme: self.scheme.clone(),
            hostname: self.hostname.clone(),
            ports: self.ports.clone(),
            address_classes: parse_address_classes(&self.address_classes)?,
            proxy_policy,
            redirect_limit: self.redirect_limit,
            byte_limit: self.byte_limit,
            expires_at_ms: self.expires_at_ms,
        })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyPayload {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PutGrantPayload {
    grant: GrantInput,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct GrantIdPayload {
    grant_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct MemoryIdPayload {
    memory_id: Id,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct PromotePayload {
    memory_id: Id,
    expected_revision: u64,
    expected_digest: Digest,
}

fn parse<T: for<'de> Deserialize<'de>>(payload: Value) -> Result<T, SecurityRouteError> {
    serde_json::from_value(payload).map_err(SecurityRouteError::Json)
}

fn scope_key(scope: &Scope) -> (&str, &str, &str) {
    (
        scope.owner_id.as_str(),
        scope.project_id.as_str(),
        scope.workspace_id.as_ref().map(Id::as_str).unwrap_or(""),
    )
}

fn to_i64(value: u64) -> Result<i64, SecurityRouteError> {
    i64::try_from(value).map_err(|_| SecurityRouteError::IntegerRange)
}

fn parse_address_classes(
    values: &BTreeSet<String>,
) -> Result<BTreeSet<AddressClass>, SecurityRouteError> {
    if values.is_empty() {
        return Err(SecurityRouteError::InvalidRequest(
            "address classes are required",
        ));
    }
    values
        .iter()
        .map(|value| match value.as_str() {
            "public" => Ok(AddressClass::Public),
            "private" => Ok(AddressClass::Private),
            "loopback" => Ok(AddressClass::Loopback),
            "link_local" => Ok(AddressClass::LinkLocal),
            "multicast" => Ok(AddressClass::Multicast),
            "metadata" => Ok(AddressClass::Metadata),
            _ => Err(SecurityRouteError::InvalidRequest(
                "address class is invalid",
            )),
        })
        .collect()
}

fn address_class_name(value: AddressClass) -> &'static str {
    match value {
        AddressClass::Public => "public",
        AddressClass::Private => "private",
        AddressClass::Loopback => "loopback",
        AddressClass::LinkLocal => "link_local",
        AddressClass::Multicast => "multicast",
        AddressClass::Metadata => "metadata",
        AddressClass::Unspecified => "unspecified",
        AddressClass::Reserved => "reserved",
    }
}

fn load_active_grants(
    connection: &Connection,
    scope: &Scope,
    now_ms: u64,
) -> Result<Vec<EgressGrant>, SecurityRouteError> {
    let (owner, project, workspace) = scope_key(scope);
    let mut statement = connection.prepare(
        "SELECT grant_json FROM network_grants
         WHERE owner_id = ?1 AND project_id = ?2 AND workspace_id = ?3
           AND revoked_at_ms IS NULL ORDER BY grant_id",
    )?;
    let stored = statement
        .query_map(params![owner, project, workspace], |row| {
            row.get::<_, String>(0)
        })?
        .collect::<Result<Vec<_>, _>>()?;
    stored
        .into_iter()
        .map(|encoded| {
            let stored: StoredGrant = serde_json::from_str(&encoded)?;
            let grant = stored.to_egress_grant()?;
            if grant.expires_at_ms <= now_ms {
                return Ok(None);
            }
            Ok(Some(grant))
        })
        .filter_map(|result: Result<Option<EgressGrant>, SecurityRouteError>| result.transpose())
        .collect()
}

fn grant_status(grant: &EgressGrant) -> Value {
    json!({
        "grantId": grant.grant_id,
        "principalId": grant.principal_id,
        "operation": grant.operation,
        "scheme": grant.scheme,
        "hostname": grant.hostname,
        "ports": grant.ports,
        "addressClasses": grant.address_classes.iter().copied().map(address_class_name).collect::<Vec<_>>(),
        "proxyPolicy": match &grant.proxy_policy { ProxyPolicy::DirectOnly => "direct", ProxyPolicy::Required { .. } => "required" },
        "redirectLimit": grant.redirect_limit,
        "byteLimit": grant.byte_limit,
        "expiresAtMs": grant.expires_at_ms,
    })
}

fn record_decision(
    connection: &Connection,
    scope: &Scope,
    grant_id: Option<&str>,
    code: &str,
    rule: &str,
    allowed: bool,
    checked_at_ms: u64,
) -> Result<(), SecurityRouteError> {
    let (owner, project, workspace) = scope_key(scope);
    connection.execute(
        "INSERT INTO network_decisions(
             owner_id, project_id, workspace_id, grant_id, code, rule, allowed,
             checked_at_ms, metadata_json
         ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, '{}')",
        params![
            owner,
            project,
            workspace,
            grant_id,
            code,
            rule,
            allowed,
            to_i64(checked_at_ms)?
        ],
    )?;
    Ok(())
}

fn load_provenance(
    connection: &Connection,
    scope: &Scope,
    memory_id: &Id,
) -> Result<Option<(MemoryProvenance, bool)>, SecurityRouteError> {
    let (owner, project, workspace) = scope_key(scope);
    let row = connection
        .query_row(
            "SELECT provenance_json, instruction_shaped
             FROM memory_trust_provenance
             WHERE owner_id = ?1 AND project_id = ?2 AND workspace_id = ?3 AND memory_id = ?4",
            params![owner, project, workspace, memory_id.as_str()],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, bool>(1)?)),
        )
        .optional()?;
    row.map(|(encoded, instruction_shaped)| {
        let provenance = serde_json::from_str(&encoded)?;
        Ok((provenance, instruction_shaped))
    })
    .transpose()
}

fn validate_provenance(provenance: &MemoryProvenance) -> Result<(), SecurityRouteError> {
    if provenance.source_type.is_empty()
        || provenance.source_type.len() > 64
        || provenance.revision == 0
        || provenance
            .promotion
            .as_ref()
            .is_some_and(|promotion| promotion.source_digest != provenance.content_digest)
    {
        return Err(SecurityRouteError::InvalidRequest(
            "memory provenance is invalid",
        ));
    }
    Ok(())
}

fn provenance_status(
    memory_id: &Id,
    provenance: &MemoryProvenance,
    instruction_shaped: bool,
) -> Value {
    let mut value = json!({
        "memoryId": memory_id,
        "sourceType": provenance.source_type,
        "sourceId": provenance.source_id,
        "authorPrincipalId": provenance.author_principal_id,
        "contentDigest": provenance.content_digest,
        "revision": provenance.revision,
        "verification": provenance.verification,
        "instructionShaped": instruction_shaped,
        "trustClass": provenance.trust_class,
    });
    if let Some(promotion) = &provenance.promotion {
        value["reviewerPrincipalId"] = json!(promotion.reviewer_principal_id);
    }
    value
}

fn failure(error: SecurityRouteError) -> SecurityRouteResponse {
    let effect_state = if matches!(
        &error,
        SecurityRouteError::Lock | SecurityRouteError::Sqlite(_)
    ) {
        EffectState::Unknown
    } else {
        EffectState::NotStarted
    };
    SecurityRouteResponse {
        payload: None,
        error: Some(
            Error::new(
                error.code(),
                error.safe_message(),
                false,
                None,
                Some(effect_state),
            )
            .expect("security route errors satisfy the shared contract"),
        ),
    }
}

#[derive(Debug, Error)]
pub enum SecurityRouteError {
    #[error("security database failed")]
    Sqlite(#[from] rusqlite::Error),
    #[error("security payload is invalid")]
    Json(#[from] serde_json::Error),
    #[error("security database lock is unavailable")]
    Lock,
    #[error("security schema version {0} is unsupported")]
    SchemaVersion(i64),
    #[error("request scope is not authorized")]
    ScopeDenied,
    #[error("security operation is unavailable")]
    UnknownOperation,
    #[error("{0}")]
    InvalidRequest(&'static str),
    #[error("network grant was not found")]
    GrantNotFound,
    #[error("memory provenance was not found")]
    ProvenanceNotFound,
    #[error("memory revision is stale")]
    StaleMemoryRevision,
    #[error("memory provenance revision is exhausted")]
    RevisionExhausted,
    #[error("security integer exceeds the durable range")]
    IntegerRange,
    #[error(transparent)]
    Egress(#[from] crate::egress::EgressError),
    #[error(transparent)]
    Provenance(#[from] ProvenanceError),
}

impl SecurityRouteError {
    fn code(&self) -> &'static str {
        match self {
            Self::ScopeDenied => "SCOPE_DENIED",
            Self::UnknownOperation => "UNKNOWN_OPERATION",
            Self::GrantNotFound => "NETWORK_GRANT_NOT_FOUND",
            Self::ProvenanceNotFound => "MEMORY_PROVENANCE_NOT_FOUND",
            Self::StaleMemoryRevision | Self::Provenance(ProvenanceError::StaleRevision) => {
                "STALE_MEMORY_REVISION"
            }
            Self::Egress(_) => EGRESS_DENIED,
            Self::InvalidRequest(_) | Self::Json(_) => "INVALID_REQUEST",
            Self::Lock
            | Self::Sqlite(_)
            | Self::SchemaVersion(_)
            | Self::RevisionExhausted
            | Self::IntegerRange
            | Self::Provenance(_) => "SECURITY_SERVICE_FAILED",
        }
    }

    fn safe_message(&self) -> &'static str {
        match self {
            Self::ScopeDenied => "request scope does not match the authenticated session scope",
            Self::UnknownOperation => "security operation is not available",
            Self::GrantNotFound => "network grant was not found",
            Self::ProvenanceNotFound => "memory provenance was not found",
            Self::StaleMemoryRevision | Self::Provenance(ProvenanceError::StaleRevision) => {
                "memory revision is stale"
            }
            Self::Egress(_) => "network request is not authorized",
            Self::InvalidRequest(message) => message,
            Self::Json(_) => "security request payload is invalid",
            _ => "security authority is unavailable",
        }
    }
}
