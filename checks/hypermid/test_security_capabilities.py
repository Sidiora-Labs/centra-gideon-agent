from __future__ import annotations

import json
import sqlite3

import pytest

from checks.hypermid.evidence import ObservationWriter
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


def test_capability_matrix_observation_records_authorization_results(
    tmp_path,
) -> None:
    own = Scope("alice", "project-a")
    principal = AuthenticatedPrincipal("principal-1", "alice")
    rows: list[dict[str, object]] = []
    unauthorized_state_changes = 0

    scenarios = (
        (
            "authenticated-owner-mismatch",
            AuthenticatedPrincipal("principal-1", "mallory"),
            "cap-1",
            Operation.APPEND,
            "record-1",
            NOW,
        ),
        (
            "guessed-capability",
            principal,
            "guessed-capability",
            Operation.APPEND,
            "record-1",
            NOW,
        ),
        (
            "wrong-operation",
            principal,
            "cap-1",
            Operation.DELETE,
            "record-1",
            NOW,
        ),
        (
            "wrong-resource",
            principal,
            "cap-1",
            Operation.APPEND,
            "record-2",
            NOW,
        ),
        (
            "expired-capability",
            principal,
            "cap-1",
            Operation.APPEND,
            "record-1",
            NOW + 60_000,
        ),
    )
    for name, actor, capability_id, operation, resource_id, now_ms in scenarios:
        connection, ledger = _ledger()
        ledger.grant(
            _owner(),
            _grant(
                claimed_scope=own,
                target_scope=own,
                operations=frozenset({Operation.APPEND}),
            ),
        )
        context = _context(
            actor,
            _request(
                claimed_scope=own,
                target_scope=own,
                operation=operation,
                resource_id=resource_id,
                now_ms=now_ms,
            ),
            capability_id,
        )
        before = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        denial_code: str | None = None
        observed = "allowed"
        try:
            ticket = ledger.authorize(context)
            ledger.commit_authorized(
                ticket,
                context,
                lambda db, record_id=resource_id: db.execute(
                    "INSERT INTO records(resource_id, value) VALUES (?, ?)",
                    (record_id, "unexpected"),
                ),
            )
        except AuthorizationDenied as denial:
            observed = "denied"
            denial_code = str(denial)
        after = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        state_changes = abs(after - before)
        unauthorized_state_changes += state_changes
        rows.append(
            {
                "scenario": name,
                "expected": "denied",
                "observed": observed,
                "denial_code": denial_code,
                "state_changes": state_changes,
            }
        )

    revoked_connection, revoked_ledger = _ledger()
    revoked_ledger.grant(
        _owner(),
        _grant(
            claimed_scope=own,
            target_scope=own,
            operations=frozenset({Operation.APPEND}),
        ),
    )
    revoked_context = _context(
        principal,
        _request(
            claimed_scope=own,
            target_scope=own,
            operation=Operation.APPEND,
        ),
    )
    revoked_ticket = revoked_ledger.authorize(revoked_context)
    assert revoked_ledger.revoke(_owner(), "cap-1")
    revoked_before = revoked_connection.execute(
        "SELECT COUNT(*) FROM records"
    ).fetchone()[0]
    revoked_code: str | None = None
    revoked_observed = "allowed"
    try:
        revoked_ledger.commit_authorized(
            revoked_ticket,
            revoked_context,
            lambda db: db.execute(
                "INSERT INTO records(resource_id, value) VALUES (?, ?)",
                ("record-1", "must-not-commit"),
            ),
        )
    except AuthorizationDenied as denial:
        revoked_observed = "denied"
        revoked_code = str(denial)
    revoked_after = revoked_connection.execute(
        "SELECT COUNT(*) FROM records"
    ).fetchone()[0]
    revoked_commit_changes = abs(revoked_after - revoked_before)
    rows.append(
        {
            "scenario": "revoked-between-authorize-and-commit",
            "expected": "denied",
            "observed": revoked_observed,
            "denial_code": revoked_code,
            "state_changes": revoked_commit_changes,
        }
    )

    allowed_connection, allowed_ledger = _ledger()
    allowed_ledger.grant(
        _owner(),
        _grant(
            claimed_scope=own,
            target_scope=own,
            operations=frozenset({Operation.APPEND}),
        ),
    )
    allowed_context = _context(
        principal,
        _request(
            claimed_scope=own,
            target_scope=own,
            operation=Operation.APPEND,
        ),
    )
    allowed_before = allowed_connection.execute(
        "SELECT COUNT(*) FROM records"
    ).fetchone()[0]
    allowed_ticket = allowed_ledger.authorize(allowed_context)
    allowed_ledger.commit_authorized(
        allowed_ticket,
        allowed_context,
        lambda db: db.execute(
            "INSERT INTO records(resource_id, value) VALUES (?, ?)",
            ("record-1", "authorized"),
        ),
    )
    allowed_after = allowed_connection.execute(
        "SELECT COUNT(*) FROM records"
    ).fetchone()[0]
    rows.append(
        {
            "scenario": "exact-live-grant",
            "expected": "allowed",
            "observed": "allowed",
            "denial_code": None,
            "state_changes": abs(allowed_after - allowed_before),
        }
    )

    report = {
        "schema_version": 1,
        "gate": "capability_matrix",
        "rows": rows,
        "unauthorized_state_changes": unauthorized_state_changes,
        "revoked_commit_changes": revoked_commit_changes,
    }
    report_path = tmp_path / "authorization-matrix.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    writer = ObservationWriter.from_env("capability_matrix")
    if writer is not None:
        writer.measure(
            "unauthorized-state-changes",
            unauthorized_state_changes,
            "eq",
            0,
            "changes",
        )
        writer.measure(
            "revoked-commit-changes",
            revoked_commit_changes,
            "eq",
            0,
            "changes",
        )
        writer.artifact(
            "authorization-matrix", report_path, "application/json"
        )
        writer.finish()

    assert unauthorized_state_changes == 0
    assert revoked_commit_changes == 0
    assert all(row["observed"] == row["expected"] for row in rows)
    assert rows[-1]["state_changes"] == 1


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
