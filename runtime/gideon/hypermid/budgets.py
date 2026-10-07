from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    max_call_nanodollars: int
    max_concurrent: int
    max_hourly_nanodollars: int
    max_daily_nanodollars: int
    max_job_nanodollars: int


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    provider_id: str
    model_id: str
    region: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class OwnerBudgetPolicy:
    owner_id: str
    project_id: str
    revision: int
    limits: BudgetLimits
    providers: tuple[ProviderPolicy, ...]


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    reservation_id: str
    owner_id: str
    project_id: str
    job_id: str
    job_class: str
    provider_id: str
    model_id: str
    region: str
    background: bool
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_nanodollars: int
    now_ms: int


@dataclass(frozen=True, slots=True)
class ActualUsage:
    input_tokens: int | None
    output_tokens: int | None
    cost_nanodollars: int | None
    requests: int = 1
    wall_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    status: str
    reserved_cost_nanodollars: int
    request: AdmissionRequest


class BudgetDenied(PermissionError):
    def __init__(self, reason: str) -> None:
        self.code = "MODEL_BUDGET_DENIED"
        self.reason = reason
        super().__init__(f"{self.code}:{reason}")


class BudgetLedger:
    def __init__(
        self,
        connection: sqlite3.Connection,
        policies: tuple[OwnerBudgetPolicy, ...],
    ) -> None:
        self._connection = connection
        self._policies = {
            (policy.owner_id, policy.project_id): policy for policy in policies
        }
        self._lock = threading.RLock()
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS hypermid_budget_reservations (
                reservation_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                job_class TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                region TEXT NOT NULL,
                background INTEGER NOT NULL,
                estimated_input_tokens INTEGER NOT NULL,
                estimated_output_tokens INTEGER NOT NULL,
                reserved_cost_nanodollars INTEGER NOT NULL,
                actual_input_tokens INTEGER,
                actual_output_tokens INTEGER,
                actual_cost_nanodollars INTEGER,
                actual_requests INTEGER,
                actual_wall_ms INTEGER,
                provider_request_id TEXT,
                status TEXT NOT NULL,
                reserved_at_ms INTEGER NOT NULL,
                reconciled_at_ms INTEGER
            );
            CREATE INDEX IF NOT EXISTS hypermid_budget_owner_time
            ON hypermid_budget_reservations(owner_id, project_id, reserved_at_ms);
            """)

    @classmethod
    def open(
        cls,
        path: str | Path,
        policies: tuple[OwnerBudgetPolicy, ...],
    ) -> BudgetLedger:
        return cls(sqlite3.connect(path), policies)

    def reserve(self, request: AdmissionRequest) -> Reservation:
        self._validate_nonnegative(request)
        policy = self._policies.get((request.owner_id, request.project_id))
        if policy is None or policy.revision <= 0:
            raise BudgetDenied("scope")
        if not any(
            provider.enabled
            and provider.provider_id == request.provider_id
            and provider.model_id == request.model_id
            and provider.region == request.region
            for provider in policy.providers
        ):
            raise BudgetDenied("provider")
        limits = policy.limits
        if request.estimated_cost_nanodollars > limits.max_call_nanodollars:
            raise BudgetDenied("per_call")

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                active = self._scalar(
                    """
                    SELECT COUNT(*) FROM hypermid_budget_reservations
                    WHERE owner_id=? AND project_id=?
                      AND status IN ('reserved', 'unknown')
                    """,
                    (request.owner_id, request.project_id),
                )
                if active >= limits.max_concurrent:
                    raise BudgetDenied("concurrency")
                self._require_total(
                    request,
                    request.now_ms - 3_600_000,
                    None,
                    limits.max_hourly_nanodollars,
                    "hourly",
                )
                self._require_total(
                    request,
                    request.now_ms - 86_400_000,
                    None,
                    limits.max_daily_nanodollars,
                    "daily",
                )
                self._require_total(
                    request,
                    None,
                    request.job_id,
                    limits.max_job_nanodollars,
                    "job_total",
                )
                self._connection.execute(
                    """
                    INSERT INTO hypermid_budget_reservations(
                        reservation_id, owner_id, project_id, job_id, job_class,
                        provider_id, model_id, region, background,
                        estimated_input_tokens, estimated_output_tokens,
                        reserved_cost_nanodollars, status, reserved_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?)
                    """,
                    (
                        request.reservation_id,
                        request.owner_id,
                        request.project_id,
                        request.job_id,
                        request.job_class,
                        request.provider_id,
                        request.model_id,
                        request.region,
                        int(request.background),
                        request.estimated_input_tokens,
                        request.estimated_output_tokens,
                        request.estimated_cost_nanodollars,
                        request.now_ms,
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return Reservation(
            request.reservation_id,
            "reserved",
            request.estimated_cost_nanodollars,
            request,
        )

    def reconcile(
        self,
        reservation_id: str,
        actual: ActualUsage | None,
        provider_request_id: str | None,
        charge_unknown: bool,
    ) -> Reservation:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                request, reserved = self._load_reserved(reservation_id)
                status = "unknown" if charge_unknown else "settled"
                actual_cost = (
                    None
                    if charge_unknown or actual is None
                    else actual.cost_nanodollars
                )
                self._connection.execute(
                    """
                    UPDATE hypermid_budget_reservations SET
                        actual_input_tokens=?, actual_output_tokens=?,
                        actual_cost_nanodollars=?, actual_requests=?, actual_wall_ms=?,
                        provider_request_id=?, status=?, reconciled_at_ms=?
                    WHERE reservation_id=? AND status='reserved'
                    """,
                    (
                        None if actual is None else actual.input_tokens,
                        None if actual is None else actual.output_tokens,
                        actual_cost,
                        None if actual is None else actual.requests,
                        None if actual is None else actual.wall_ms,
                        provider_request_id,
                        status,
                        request.now_ms,
                        reservation_id,
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return Reservation(reservation_id, status, reserved, request)

    def release(self, reservation_id: str) -> Reservation:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                request, reserved = self._load_reserved(reservation_id)
                self._connection.execute(
                    """
                    UPDATE hypermid_budget_reservations
                    SET status='released', reconciled_at_ms=?
                    WHERE reservation_id=? AND status='reserved'
                    """,
                    (request.now_ms, reservation_id),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return Reservation(reservation_id, "released", reserved, request)

    def reservation(self, reservation_id: str) -> Reservation | None:
        row = self._connection.execute(
            """
            SELECT owner_id, project_id, job_id, job_class, provider_id, model_id,
                   region, background, estimated_input_tokens, estimated_output_tokens,
                   reserved_cost_nanodollars, reserved_at_ms, status
            FROM hypermid_budget_reservations WHERE reservation_id=?
            """,
            (reservation_id,),
        ).fetchone()
        if row is None:
            return None
        request = AdmissionRequest(
            reservation_id,
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6]),
            bool(row[7]),
            int(row[8]),
            int(row[9]),
            int(row[10]),
            int(row[11]),
        )
        return Reservation(reservation_id, str(row[12]), int(row[10]), request)

    def _load_reserved(self, reservation_id: str) -> tuple[AdmissionRequest, int]:
        reservation = self.reservation(reservation_id)
        if reservation is None or reservation.status != "reserved":
            raise BudgetDenied("reservation_state")
        return reservation.request, reservation.reserved_cost_nanodollars

    def _require_total(
        self,
        request: AdmissionRequest,
        since_ms: int | None,
        job_id: str | None,
        limit: int,
        reason: str,
    ) -> None:
        used = self._scalar(
            """
            SELECT COALESCE(SUM(CASE WHEN status='settled'
                THEN COALESCE(actual_cost_nanodollars, reserved_cost_nanodollars)
                ELSE reserved_cost_nanodollars END), 0)
            FROM hypermid_budget_reservations
            WHERE owner_id=? AND project_id=? AND status!='released'
              AND (? IS NULL OR reserved_at_ms>=?)
              AND (? IS NULL OR job_id=?)
            """,
            (
                request.owner_id,
                request.project_id,
                since_ms,
                since_ms,
                job_id,
                job_id,
            ),
        )
        if used + request.estimated_cost_nanodollars > limit:
            raise BudgetDenied(reason)

    def _scalar(self, sql: str, parameters: tuple[object, ...]) -> int:
        row = self._connection.execute(sql, parameters).fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _validate_nonnegative(request: AdmissionRequest) -> None:
        values = (
            request.estimated_input_tokens,
            request.estimated_output_tokens,
            request.estimated_cost_nanodollars,
            request.now_ms,
        )
        if any(value < 0 for value in values):
            raise ValueError("budget values must be non-negative")
