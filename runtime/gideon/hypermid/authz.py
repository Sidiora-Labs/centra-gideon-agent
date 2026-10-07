from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_T = TypeVar("_T")


class Operation(str, Enum):
    READ = "read"
    APPEND = "append"
    REVISE = "revise"
    ARCHIVE = "archive"
    DELETE = "delete"
    EXPORT = "export"
    RESTORE = "restore"
    ADMINISTER = "administer"
    MODEL_USE = "model-use"
    NETWORK_USE = "network-use"
    ARTIFACT_INSTALL = "artifact-install"
    ARTIFACT_UPDATE = "artifact-update"

    @property
    def mutates(self) -> bool:
        return self is not Operation.READ


class PrincipalKind(str, Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"


@dataclass(frozen=True)
class Scope:
    owner_id: str
    project_id: str
    workspace_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.owner_id, self.project_id):
            if not _ID.fullmatch(value):
                raise ValueError("invalid scope identifier")
        if self.workspace_id is not None and not _ID.fullmatch(self.workspace_id):
            raise ValueError("invalid scope identifier")


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    principal_id: str
    owner_id: str
    kind: PrincipalKind = PrincipalKind.FOREGROUND

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.principal_id) or not _ID.fullmatch(self.owner_id):
            raise ValueError("invalid principal identifier")


@dataclass(frozen=True)
class CapabilityGrant:
    capability_id: str
    issuer_owner_id: str
    principal_id: str
    claimed_scope: Scope
    target_scope: Scope
    operations: frozenset[Operation]
    resources: frozenset[str]
    expires_at_ms: int

    def __post_init__(self) -> None:
        if (
            not _ID.fullmatch(self.capability_id)
            or not _ID.fullmatch(self.issuer_owner_id)
            or not _ID.fullmatch(self.principal_id)
        ):
            raise ValueError("invalid capability identifier")
        if not self.operations or not self.resources:
            raise ValueError(
                "capability grants must enumerate operations and resources"
            )
        if any(not _ID.fullmatch(resource) for resource in self.resources):
            raise ValueError("invalid resource identifier")
        if self.expires_at_ms < 0:
            raise ValueError("invalid capability expiry")


@dataclass(frozen=True)
class AuthorizationRequest:
    claimed_scope: Scope
    target_scope: Scope
    operation: Operation
    resource_id: str
    now_ms: int

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.resource_id) or self.now_ms < 0:
            raise ValueError("invalid authorization request")


@dataclass(frozen=True)
class AuthContext:
    capability_id: str
    request: AuthorizationRequest
    principal: AuthenticatedPrincipal

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.capability_id):
            raise ValueError("invalid capability identifier")


@dataclass(frozen=True)
class CommitAuthorization:
    revision: int
    context: AuthContext

    def __post_init__(self) -> None:
        if self.revision <= 0:
            raise ValueError("invalid capability revision")


class AuthorizationDenied(PermissionError):
    code = "AUTHORIZATION_DENIED"

    def __init__(self) -> None:
        super().__init__(self.code)


def authorize_exact(
    principal: AuthenticatedPrincipal,
    request: AuthorizationRequest,
    grant: CapabilityGrant,
) -> None:
    if principal.owner_id != request.claimed_scope.owner_id:
        raise AuthorizationDenied()
    if principal.principal_id != grant.principal_id:
        raise AuthorizationDenied()
    if grant.issuer_owner_id != request.target_scope.owner_id:
        raise AuthorizationDenied()
    if request.claimed_scope != grant.claimed_scope:
        raise AuthorizationDenied()
    if request.target_scope != grant.target_scope:
        raise AuthorizationDenied()
    if request.operation not in grant.operations:
        raise AuthorizationDenied()
    if request.resource_id not in grant.resources:
        raise AuthorizationDenied()
    if request.now_ms >= grant.expires_at_ms:
        raise AuthorizationDenied()

    same_project = (
        request.target_scope.owner_id == request.claimed_scope.owner_id
        and request.target_scope.project_id == request.claimed_scope.project_id
    )
    if not same_project and request.operation is Operation.READ:
        shared_workspace = (
            request.claimed_scope.workspace_id is not None
            and request.claimed_scope.workspace_id == request.target_scope.workspace_id
        )
        if not shared_workspace:
            raise AuthorizationDenied()


class AuthorizationLedger:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = threading.RLock()
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS hypermid_capabilities (
                capability_id TEXT PRIMARY KEY,
                issuer_owner_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                claimed_owner_id TEXT NOT NULL,
                claimed_project_id TEXT NOT NULL,
                claimed_workspace_id TEXT,
                target_owner_id TEXT NOT NULL,
                target_project_id TEXT NOT NULL,
                target_workspace_id TEXT,
                operations_json TEXT NOT NULL,
                resources_json TEXT NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS hypermid_authorization_cursor (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                sequence INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO hypermid_authorization_cursor(singleton, sequence)
            VALUES (1, 0);
            """)

    @property
    def cursor(self) -> int:
        row = self._connection.execute(
            "SELECT sequence FROM hypermid_authorization_cursor WHERE singleton = 1"
        ).fetchone()
        assert row is not None
        return int(row[0])

    def grant(
        self,
        issuer: AuthenticatedPrincipal,
        capability: CapabilityGrant,
    ) -> int:
        with self._lock:
            if (
                issuer.kind is not PrincipalKind.FOREGROUND
                or issuer.owner_id != capability.issuer_owner_id
                or issuer.owner_id != capability.target_scope.owner_id
            ):
                raise AuthorizationDenied()
            row = self._connection.execute(
                """
                SELECT revision, issuer_owner_id
                FROM hypermid_capabilities WHERE capability_id = ?
                """,
                (capability.capability_id,),
            ).fetchone()
            if row is not None and str(row[1]) != issuer.owner_id:
                raise AuthorizationDenied()
            revision = 1 if row is None else int(row[0]) + 1
            self._connection.execute(
                """
                INSERT INTO hypermid_capabilities (
                    capability_id, issuer_owner_id, principal_id,
                    claimed_owner_id, claimed_project_id, claimed_workspace_id,
                    target_owner_id, target_project_id, target_workspace_id,
                    operations_json, resources_json, expires_at_ms, revision, revoked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(capability_id) DO UPDATE SET
                    issuer_owner_id = excluded.issuer_owner_id,
                    principal_id = excluded.principal_id,
                    claimed_owner_id = excluded.claimed_owner_id,
                    claimed_project_id = excluded.claimed_project_id,
                    claimed_workspace_id = excluded.claimed_workspace_id,
                    target_owner_id = excluded.target_owner_id,
                    target_project_id = excluded.target_project_id,
                    target_workspace_id = excluded.target_workspace_id,
                    operations_json = excluded.operations_json,
                    resources_json = excluded.resources_json,
                    expires_at_ms = excluded.expires_at_ms,
                    revision = excluded.revision,
                    revoked = 0
                """,
                (
                    capability.capability_id,
                    capability.issuer_owner_id,
                    capability.principal_id,
                    capability.claimed_scope.owner_id,
                    capability.claimed_scope.project_id,
                    capability.claimed_scope.workspace_id,
                    capability.target_scope.owner_id,
                    capability.target_scope.project_id,
                    capability.target_scope.workspace_id,
                    json.dumps(sorted(op.value for op in capability.operations)),
                    json.dumps(sorted(capability.resources)),
                    capability.expires_at_ms,
                    revision,
                ),
            )
            self._connection.commit()
            return revision

    def revoke(self, issuer: AuthenticatedPrincipal, capability_id: str) -> bool:
        with self._lock:
            if issuer.kind is not PrincipalKind.FOREGROUND:
                raise AuthorizationDenied()
            row = self._connection.execute(
                "SELECT issuer_owner_id FROM hypermid_capabilities WHERE capability_id = ?",
                (capability_id,),
            ).fetchone()
            if row is None or str(row[0]) != issuer.owner_id:
                raise AuthorizationDenied()
            result = self._connection.execute(
                """
                UPDATE hypermid_capabilities
                SET revoked = 1, revision = revision + 1
                WHERE capability_id = ? AND revoked = 0
                """,
                (capability_id,),
            )
            self._connection.commit()
            return result.rowcount == 1

    def authorize(
        self,
        context: AuthContext,
    ) -> CommitAuthorization:
        with self._lock:
            grant, revision = self._load_live(context.capability_id)
            authorize_exact(context.principal, context.request, grant)
            return CommitAuthorization(revision, context)

    def commit_authorized(
        self,
        authorization: CommitAuthorization,
        current_context: AuthContext,
        mutation: Callable[[sqlite3.Connection], _T],
    ) -> _T:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                grant, revision = self._load_live(current_context.capability_id)
                if revision != authorization.revision:
                    raise AuthorizationDenied()
                prior = authorization.context
                if (
                    prior.capability_id != current_context.capability_id
                    or prior.principal != current_context.principal
                    or prior.request.claimed_scope
                    != current_context.request.claimed_scope
                    or prior.request.target_scope
                    != current_context.request.target_scope
                    or prior.request.operation != current_context.request.operation
                    or prior.request.resource_id != current_context.request.resource_id
                    or current_context.request.now_ms < prior.request.now_ms
                ):
                    raise AuthorizationDenied()
                authorize_exact(
                    current_context.principal,
                    current_context.request,
                    grant,
                )
                if not current_context.request.operation.mutates:
                    raise AuthorizationDenied()
                result = mutation(self._connection)
                self._connection.execute("""
                    UPDATE hypermid_authorization_cursor
                    SET sequence = sequence + 1 WHERE singleton = 1
                    """)
                self._connection.execute("COMMIT")
                return result
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _load_live(self, capability_id: str) -> tuple[CapabilityGrant, int]:
        row = self._connection.execute(
            """
            SELECT issuer_owner_id, principal_id,
                   claimed_owner_id, claimed_project_id, claimed_workspace_id,
                   target_owner_id, target_project_id, target_workspace_id,
                   operations_json, resources_json, expires_at_ms, revision, revoked
            FROM hypermid_capabilities WHERE capability_id = ?
            """,
            (capability_id,),
        ).fetchone()
        if row is None or int(row[12]) != 0:
            raise AuthorizationDenied()
        try:
            grant = CapabilityGrant(
                capability_id=capability_id,
                issuer_owner_id=str(row[0]),
                principal_id=str(row[1]),
                claimed_scope=Scope(str(row[2]), str(row[3]), row[4]),
                target_scope=Scope(str(row[5]), str(row[6]), row[7]),
                operations=frozenset(Operation(value) for value in json.loads(row[8])),
                resources=frozenset(str(value) for value in json.loads(row[9])),
                expires_at_ms=int(row[10]),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthorizationDenied() from exc
        return grant, int(row[11])
