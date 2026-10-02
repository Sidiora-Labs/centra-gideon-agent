from __future__ import annotations

import sqlite3

import pytest

from gideon.hypermid.authz import (
    AuthContext,
    AuthenticatedPrincipal,
    AuthorizationDenied,
    AuthorizationLedger,
    AuthorizationRequest,
    CapabilityGrant,
    Operation,
    PrincipalKind,
    Scope,
)


NOW = 1_800_000_000_000


def _grant(
    *,
    capability_id: str = "cap-1",
    principal_id: str = "principal-1",
    claimed_scope: Scope,
    target_scope: Scope,
    operations: frozenset[Operation] = frozenset({Operation.READ}),
    resources: frozenset[str] = frozenset({"record-1"}),
    expires_at_ms: int = NOW + 60_000,
) -> CapabilityGrant:
    return CapabilityGrant(
        capability_id=capability_id,
        issuer_owner_id=target_scope.owner_id,
        principal_id=principal_id,
        claimed_scope=claimed_scope,
        target_scope=target_scope,
        operations=operations,
        resources=resources,
        expires_at_ms=expires_at_ms,
    )


def _request(
    *,
    claimed_scope: Scope,
    target_scope: Scope,
    operation: Operation = Operation.READ,
    resource_id: str = "record-1",
    now_ms: int = NOW,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        claimed_scope=claimed_scope,
        target_scope=target_scope,
        operation=operation,
        resource_id=resource_id,
        now_ms=now_ms,
    )


def _ledger() -> tuple[sqlite3.Connection, AuthorizationLedger]:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE records(resource_id TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.commit()
    return connection, AuthorizationLedger(connection)


def _owner(owner_id: str = "alice") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(f"issuer-{owner_id}", owner_id)


def _context(
    principal: AuthenticatedPrincipal,
    request: AuthorizationRequest,
    capability_id: str = "cap-1",
) -> AuthContext:
    return AuthContext(capability_id, request, principal)


def test_authenticated_owner_is_authoritative_over_claimed_scope() -> None:
    connection, ledger = _ledger()
    alice = Scope("alice", "project-a")
    ledger.grant(_owner(), _grant(claimed_scope=alice, target_scope=alice))
    principal = AuthenticatedPrincipal("principal-1", "mallory")

    with pytest.raises(AuthorizationDenied) as denial:
        ledger.authorize(
            _context(
                principal,
                _request(claimed_scope=alice, target_scope=alice),
            ),
        )

    assert str(denial.value) == "AUTHORIZATION_DENIED"
    assert ledger.cursor == 0
    assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)


@pytest.mark.parametrize(
    ("capability_id", "operation", "resource_id", "now_ms"),
    [
        ("guessed-capability", Operation.APPEND, "record-1", NOW),
        ("cap-1", Operation.DELETE, "record-1", NOW),
        ("cap-1", Operation.APPEND, "record-2", NOW),
        ("cap-1", Operation.APPEND, "record-1", NOW + 60_000),
    ],
)
def test_exact_live_grant_is_required_without_state_change(
    capability_id: str,
    operation: Operation,
    resource_id: str,
    now_ms: int,
) -> None:
    connection, ledger = _ledger()
    own = Scope("alice", "project-a")
    ledger.grant(
        _owner(),
        _grant(
            claimed_scope=own,
            target_scope=own,
            operations=frozenset({Operation.APPEND}),
        )
    )
    principal = AuthenticatedPrincipal("principal-1", "alice")
    request = _request(
        claimed_scope=own,
        target_scope=own,
        operation=operation,
        resource_id=resource_id,
        now_ms=now_ms,
    )

    with pytest.raises(AuthorizationDenied) as denial:
        ledger.authorize(_context(principal, request, capability_id))

    assert str(denial.value) == "AUTHORIZATION_DENIED"
    assert ledger.cursor == 0
    assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)


def test_shared_workspace_allows_only_an_exact_foreign_read() -> None:
    _, ledger = _ledger()
    caller = Scope("alice", "project-a", "workspace-1")
    foreign = Scope("bob", "project-b", "workspace-1")
    ledger.grant(_owner("bob"), _grant(claimed_scope=caller, target_scope=foreign))
    principal = AuthenticatedPrincipal("principal-1", "alice")

    ticket = ledger.authorize(
        _context(
            principal,
            _request(claimed_scope=caller, target_scope=foreign),
        ),
    )

    assert ticket.context.capability_id == "cap-1"
    assert ledger.cursor == 0


@pytest.mark.parametrize(
    "kind", [PrincipalKind.FOREGROUND, PrincipalKind.BACKGROUND]
)
def test_foreign_project_mutation_requires_exact_target_owner_grant(
    kind: PrincipalKind,
) -> None:
    connection, ledger = _ledger()
    caller = Scope("alice", "project-a", "workspace-1")
    foreign = Scope("bob", "project-b", "workspace-2")
    capability = _grant(
        claimed_scope=caller,
        target_scope=foreign,
        operations=frozenset({Operation.REVISE}),
    )
    principal = AuthenticatedPrincipal("principal-1", "alice", kind)

    with pytest.raises(AuthorizationDenied):
        ledger.grant(_owner("alice"), capability)

    ledger.grant(_owner("bob"), capability)
    ticket = ledger.authorize(
        _context(
            principal,
            _request(
                claimed_scope=caller,
                target_scope=foreign,
                operation=Operation.REVISE,
            ),
        ),
    )
    ledger.commit_authorized(
        ticket,
        ticket.context,
        lambda db: db.execute(
            "INSERT INTO records(resource_id, value) VALUES (?, ?)",
            ("record-1", "owner-granted"),
        ),
    )

    assert ledger.cursor == 1
    assert connection.execute("SELECT value FROM records").fetchone() == (
        "owner-granted",
    )


def test_background_job_can_write_only_enumerated_own_project_record() -> None:
    connection, ledger = _ledger()
    own = Scope("alice", "project-a")
    ledger.grant(
        _owner(),
        _grant(
            claimed_scope=own,
            target_scope=own,
            operations=frozenset({Operation.APPEND}),
        )
    )
    principal = AuthenticatedPrincipal(
        "principal-1", "alice", PrincipalKind.BACKGROUND
    )
    ticket = ledger.authorize(
        _context(
            principal,
            _request(
                claimed_scope=own,
                target_scope=own,
                operation=Operation.APPEND,
            ),
        ),
    )

    ledger.commit_authorized(
        ticket,
        ticket.context,
        lambda db: db.execute(
            "INSERT INTO records(resource_id, value) VALUES (?, ?)",
            ("record-1", "committed"),
        ),
    )

    assert connection.execute("SELECT * FROM records").fetchall() == [
        ("record-1", "committed")
    ]
    assert ledger.cursor == 1


def test_revocation_between_read_and_commit_prevents_publication() -> None:
    connection, ledger = _ledger()
    own = Scope("alice", "project-a")
    ledger.grant(
        _owner(),
        _grant(
            claimed_scope=own,
            target_scope=own,
            operations=frozenset({Operation.APPEND}),
        )
    )
    principal = AuthenticatedPrincipal("principal-1", "alice")
    ticket = ledger.authorize(
        _context(
            principal,
            _request(
                claimed_scope=own,
                target_scope=own,
                operation=Operation.APPEND,
            ),
        ),
    )
    assert ledger.revoke(_owner(), "cap-1")

    with pytest.raises(AuthorizationDenied):
        ledger.commit_authorized(
            ticket,
            ticket.context,
            lambda db: db.execute(
                "INSERT INTO records(resource_id, value) VALUES (?, ?)",
                ("record-1", "must-not-commit"),
            ),
        )

    assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
    assert ledger.cursor == 0


def test_expiry_between_protocol_check_and_commit_prevents_publication() -> None:
    connection, ledger = _ledger()
    own = Scope("alice", "project-a")
    ledger.grant(
        _owner(),
        _grant(
            claimed_scope=own,
            target_scope=own,
            operations=frozenset({Operation.APPEND}),
        ),
    )
    principal = AuthenticatedPrincipal("principal-1", "alice")
    request = _request(
        claimed_scope=own,
        target_scope=own,
        operation=Operation.APPEND,
    )
    ticket = ledger.authorize(_context(principal, request))
    expired = _context(
        principal,
        _request(
            claimed_scope=own,
            target_scope=own,
            operation=Operation.APPEND,
            now_ms=NOW + 60_000,
        ),
    )

    with pytest.raises(AuthorizationDenied):
        ledger.commit_authorized(
            ticket,
            expired,
            lambda db: db.execute(
                "INSERT INTO records(resource_id, value) VALUES (?, ?)",
                ("record-1", "must-not-commit"),
            ),
        )

    assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
    assert ledger.cursor == 0


def test_background_principal_cannot_mint_capabilities() -> None:
    _, ledger = _ledger()
    own = Scope("alice", "project-a")
    worker = AuthenticatedPrincipal(
        "principal-1", "alice", PrincipalKind.BACKGROUND
    )

    with pytest.raises(AuthorizationDenied):
        ledger.grant(worker, _grant(claimed_scope=own, target_scope=own))
