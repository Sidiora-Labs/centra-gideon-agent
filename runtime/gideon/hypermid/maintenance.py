from __future__ import annotations

import re
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Mapping

from .contracts import (
    KnowledgePublication,
    KnowledgePublicationAuthority,
    KnowledgePublicationReceipt,
    MaintenanceClaimProof,
    MaintenanceClaimReceipt,
    MemoryOperation,
    MutationRequest,
    RecordDraft,
    RevisionPrecondition,
    SourceSnapshot,
    SummaryPublication,
)
from .client import HypermidOutcomeUnknown
from .foundation import Digest, Id
from .model_budget import ActualUsage, BudgetAmount, ModelBudget
from .models import Cursor, Scope, Trace

if TYPE_CHECKING:
    from .memory_client import MemoryClient


MaintenanceKind = Literal[
    "extract_facts",
    "extract_episodes",
    "verify_claims",
    "evaluate_smart_notes",
    "refresh_summaries",
    "decay_summaries",
    "embed_records",
    "reembed_model",
    "reconcile_fts",
    "reconcile_sources",
    "index_messages",
    "index_git_commits",
    "invalidate_lineage",
    "sweep_orphans",
    "compact_events",
    "check_integrity",
    "import_batch",
    "export_batch",
    "purge_tombstones",
]

_KINDS = frozenset(MaintenanceKind.__args__)
_OPERATIONS = frozenset(
    {
        "create",
        "update",
        "archive",
        "restore",
        "merge",
        "split",
        "relocate",
        "delete",
        "purge",
        "verify",
        "embed",
        "index",
        "summarize",
        "import",
        "export",
    }
)
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class MaintenanceJob:
    job_id: str
    kind: MaintenanceKind
    scope: Scope
    actor_scope: Scope
    required_operation: str
    input_cursor: Cursor
    input_digest: str
    config_digest: str
    budget: ModelBudget
    trace: Trace
    available_at_ms: int

    def __post_init__(self) -> None:
        if not self.job_id or len(self.job_id) > 160:
            raise ValueError("job_id is invalid")
        if self.kind not in _KINDS:
            raise ValueError("maintenance kind is invalid")
        if self.required_operation not in _OPERATIONS:
            raise ValueError("required operation is invalid")
        if _DIGEST.fullmatch(self.input_digest) is None:
            raise ValueError("input digest is invalid")
        if _DIGEST.fullmatch(self.config_digest) is None:
            raise ValueError("configuration digest is invalid")
        if self.available_at_ms < 0:
            raise ValueError("available_at_ms is invalid")

    def to_wire(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "scope": self.scope.to_wire(),
            "actor_scope": self.actor_scope.to_wire(),
            "required_operation": self.required_operation,
            "input_cursor": self.input_cursor.to_wire(),
            "input_digest": self.input_digest,
            "config_digest": self.config_digest,
            "budget": self.budget.to_wire(),
            "trace": self.trace.to_wire(),
            "available_at_ms": self.available_at_ms,
        }


@dataclass(frozen=True, slots=True)
class LeaseReceipt:
    job_id: str
    holder_id: str
    fencing_token: str
    expires_at_ms: int

    def __post_init__(self) -> None:
        if not self.job_id or not self.holder_id or not self.fencing_token:
            raise ValueError("lease receipt identifiers are required")
        if self.expires_at_ms < 0:
            raise ValueError("lease expiry is invalid")


@dataclass(frozen=True, slots=True)
class ModelReservation:
    reservation_id: str
    job_id: str
    reserved: BudgetAmount


@dataclass(frozen=True, slots=True)
class ClaimedMaintenanceJob:
    job: MaintenanceJob
    lease: LeaseReceipt

    @classmethod
    def from_receipt(cls, receipt: MaintenanceClaimReceipt) -> ClaimedMaintenanceJob:
        job = MaintenanceJob(
            job_id=str(receipt.job_id),
            kind=receipt.kind.value,
            scope=receipt.target_scope,
            actor_scope=receipt.actor_scope,
            required_operation=receipt.required_operation.value,
            input_cursor=receipt.input_cursor,
            input_digest=str(receipt.input_digest),
            config_digest=str(receipt.config_digest),
            budget=receipt.budget,
            trace=receipt.trace,
            available_at_ms=receipt.available_at_ms,
        )
        lease = LeaseReceipt(
            job_id=str(receipt.job_id),
            holder_id=receipt.holder_id,
            fencing_token=str(receipt.fencing_token),
            expires_at_ms=receipt.expires_at_ms,
        )
        return cls(job, lease)

    @property
    def proof(self) -> MaintenanceClaimProof:
        return MaintenanceClaimProof(
            Id(self.lease.job_id),
            self.lease.holder_id,
            Digest(self.lease.fencing_token),
        )

    @classmethod
    def from_wire(cls, value: object) -> ClaimedMaintenanceJob:
        if not isinstance(value, Mapping):
            raise ValueError("claimed maintenance job must be an object")
        raw_job = value.get("job", value)
        raw_lease = value.get("lease", value)
        if not isinstance(raw_job, Mapping) or not isinstance(raw_lease, Mapping):
            raise ValueError("claimed maintenance job is incomplete")
        job = MaintenanceJob(
            job_id=str(raw_job.get("job_id", "")),
            kind=raw_job.get("kind"),
            scope=Scope.from_wire(raw_job.get("target_scope", raw_job.get("scope"))),
            actor_scope=Scope.from_wire(raw_job.get("actor_scope")),
            required_operation=str(raw_job.get("required_operation", "")),
            input_cursor=Cursor.from_wire(raw_job.get("input_cursor")),
            input_digest=str(raw_job.get("input_digest", "")),
            config_digest=str(raw_job.get("config_digest", "")),
            budget=ModelBudget.from_wire(raw_job.get("budget", {})),
            trace=Trace.from_wire(raw_job.get("trace")),
            available_at_ms=int(raw_job.get("available_at_ms", -1)),
        )
        lease = LeaseReceipt(
            job_id=str(raw_lease.get("job_id", "")),
            holder_id=str(raw_lease.get("holder_id", "")),
            fencing_token=str(raw_lease.get("fencing_token", "")),
            expires_at_ms=int(raw_lease.get("expires_at_ms", -1)),
        )
        if lease.job_id != job.job_id:
            raise ValueError("maintenance lease does not match its job")
        return cls(job, lease)


@dataclass(frozen=True, slots=True)
class MaintenanceCompletion:
    current_input_digest: str
    current_config_digest: str
    publication: SummaryMaintenancePublication


@dataclass(frozen=True, slots=True)
class SummaryMaintenancePublication:
    request: MutationRequest
    capability_id: Id
    draft: RecordDraft
    sources: tuple[SourceSnapshot, ...]

    def __post_init__(self) -> None:
        if self.request.operation is not MemoryOperation.CREATE:
            raise ValueError("summary publication requires create authority")
        if self.request.record_id != self.draft.id:
            raise ValueError("summary publication authority does not match its draft")
        object.__setattr__(self, "capability_id", Id(self.capability_id))
        object.__setattr__(self, "sources", tuple(self.sources))


@dataclass(frozen=True, slots=True)
class KnowledgeMaintenancePublication:
    publication: KnowledgePublication
    smart_notes: tuple[KnowledgePublicationAuthority, ...] = ()
    verifications: tuple[KnowledgePublicationAuthority, ...] = ()
    sharing_judgments: tuple[KnowledgePublicationAuthority, ...] = ()
    recall: KnowledgePublicationAuthority | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "smart_notes", tuple(self.smart_notes))
        object.__setattr__(self, "verifications", tuple(self.verifications))
        object.__setattr__(self, "sharing_judgments", tuple(self.sharing_judgments))


@dataclass(frozen=True, slots=True)
class KnowledgeMaintenanceCompletion:
    current_input_digest: str
    current_config_digest: str
    publication: KnowledgeMaintenancePublication
    pre_publish_check: Callable[[], None] | None = None


class MaintenanceScheduler:
    def __init__(self, client: MemoryClient, *, worker_id: str) -> None:
        if not worker_id or len(worker_id) > 160:
            raise ValueError("worker_id is invalid")
        self._client = client
        self._worker_id = worker_id

    async def claim_next(
        self,
        *,
        target_scope: Scope,
        required_operation: str,
        trace: Trace,
        lease_ttl_ms: int,
        deadline_ms: int | None = None,
    ) -> ClaimedMaintenanceJob | None:
        if required_operation not in _OPERATIONS or lease_ttl_ms <= 0:
            raise ValueError("maintenance claim is invalid")
        del deadline_ms
        request = self._request(
            target_scope=target_scope,
            required_operation=required_operation,
            trace=trace,
        )
        receipt = await self._client.maintenance_claim(
            request, worker_id=Id(self._worker_id), ttl_ms=lease_ttl_ms
        )
        if receipt is None:
            return None
        return ClaimedMaintenanceJob.from_receipt(receipt)

    async def heartbeat(
        self,
        claimed: ClaimedMaintenanceJob,
        *,
        lease_ttl_ms: int,
        deadline_ms: int | None = None,
    ) -> LeaseReceipt:
        if lease_ttl_ms <= 0:
            raise ValueError("lease_ttl_ms must be positive")
        del deadline_ms
        receipt = await self._client.maintenance_heartbeat(
            self._job_request(claimed),
            proof=claimed.proof,
            ttl_ms=lease_ttl_ms,
        )
        return LeaseReceipt(
            job_id=str(receipt.job_id),
            holder_id=receipt.holder_id,
            fencing_token=str(receipt.fencing_token),
            expires_at_ms=receipt.expires_at_ms,
        )

    async def publish(
        self,
        claimed: ClaimedMaintenanceJob,
        completion: MaintenanceCompletion,
        *,
        deadline_ms: int | None = None,
    ) -> Mapping[str, Any]:
        del deadline_ms
        publication_request(
            claimed.job,
            claimed.lease,
            current_input_digest=completion.current_input_digest,
            current_config_digest=completion.current_config_digest,
        )
        publication = completion.publication
        typed_publication = SummaryPublication(
            job_id=Id(claimed.job.job_id),
            expected_input_digest=Digest(completion.current_input_digest),
            expected_config_digest=Digest(completion.current_config_digest),
            draft=publication.draft,
            sources=publication.sources,
            now_ms=int(time.time() * 1000),
        )
        response = await self._client.maintenance_publish(
            self._job_request(claimed),
            publication.request,
            typed_publication,
            summary_capability_id=publication.capability_id,
            proof=claimed.proof,
        )
        result = response.result
        if not isinstance(result, Mapping):
            raise ValueError("maintenance publication response must be an object")
        return result

    async def publish_knowledge(
        self,
        claimed: ClaimedMaintenanceJob,
        completion: KnowledgeMaintenanceCompletion,
        *,
        deadline_ms: int | None = None,
    ) -> KnowledgePublicationReceipt:
        del deadline_ms
        publication_request(
            claimed.job,
            claimed.lease,
            current_input_digest=completion.current_input_digest,
            current_config_digest=completion.current_config_digest,
        )
        output = completion.publication
        publication = output.publication
        if publication.job_id != Id(claimed.job.job_id):
            raise ValueError("knowledge publication does not match its claimed job")
        if publication.expected_input_digest != Digest(completion.current_input_digest):
            raise ValueError("knowledge publication input digest changed")
        if publication.expected_config_digest != Digest(completion.current_config_digest):
            raise ValueError("knowledge publication configuration digest changed")
        if publication.evaluated_cursor != claimed.job.input_cursor:
            raise ValueError("knowledge publication cursor does not match its claimed job")
        if completion.pre_publish_check is not None:
            completion.pre_publish_check()
        return await self._client.maintenance_publish_knowledge(
            self._job_request(claimed),
            publication,
            proof=claimed.proof,
            smart_notes=output.smart_notes,
            verifications=output.verifications,
            sharing_judgments=output.sharing_judgments,
            recall=output.recall,
        )

    async def run_knowledge_once(
        self,
        *,
        target_scope: Scope,
        trace: Trace,
        lease_ttl_ms: int,
        execute: Callable[
            [ClaimedMaintenanceJob], Awaitable[KnowledgeMaintenanceCompletion]
        ],
        deadline_ms: int | None = None,
    ) -> KnowledgePublicationReceipt | None:
        claimed = await self.claim_next(
            target_scope=target_scope,
            required_operation="index",
            trace=trace,
            lease_ttl_ms=lease_ttl_ms,
            deadline_ms=deadline_ms,
        )
        if claimed is None:
            return None
        try:
            completion = await execute(claimed)
            if not isinstance(completion, KnowledgeMaintenanceCompletion):
                raise TypeError(
                    "knowledge execution must return KnowledgeMaintenanceCompletion"
                )
        except asyncio.CancelledError:
            await asyncio.shield(
                self.finish(
                    claimed,
                    state="abandoned",
                    error_code="CANCELLED",
                    deadline_ms=deadline_ms,
                )
            )
            raise
        except BaseException:
            await self.finish(
                claimed,
                state="failed",
                error_code="EXECUTION_FAILED",
                deadline_ms=deadline_ms,
            )
            raise
        try:
            return await self.publish_knowledge(
                claimed, completion, deadline_ms=deadline_ms
            )
        except HypermidOutcomeUnknown:
            raise
        except asyncio.CancelledError:
            await asyncio.shield(
                self.finish(
                    claimed,
                    state="abandoned",
                    error_code="CANCELLED",
                    deadline_ms=deadline_ms,
                )
            )
            raise
        except BaseException:
            await self.finish(
                claimed,
                state="failed",
                error_code="PUBLICATION_FAILED",
                deadline_ms=deadline_ms,
            )
            raise

    async def finish(
        self,
        claimed: ClaimedMaintenanceJob,
        *,
        state: Literal["failed", "abandoned"],
        error_code: str,
        deadline_ms: int | None = None,
    ) -> None:
        if not error_code or len(error_code) > 64:
            raise ValueError("maintenance error code is invalid")
        del deadline_ms
        await self._client.maintenance_terminate(
            self._job_request(claimed),
            proof=claimed.proof,
            state=state,
            error_code=error_code,
        )

    async def run_once(
        self,
        *,
        target_scope: Scope,
        required_operation: str,
        trace: Trace,
        lease_ttl_ms: int,
        execute: Callable[[ClaimedMaintenanceJob], Awaitable[MaintenanceCompletion]],
        deadline_ms: int | None = None,
    ) -> bool:
        claimed = await self.claim_next(
            target_scope=target_scope,
            required_operation=required_operation,
            trace=trace,
            lease_ttl_ms=lease_ttl_ms,
            deadline_ms=deadline_ms,
        )
        if claimed is None:
            return False
        try:
            completion = await execute(claimed)
        except asyncio.CancelledError:
            await asyncio.shield(
                self.finish(
                    claimed,
                    state="abandoned",
                    error_code="CANCELLED",
                    deadline_ms=deadline_ms,
                )
            )
            raise
        except BaseException:
            await self.finish(
                claimed,
                state="failed",
                error_code="EXECUTION_FAILED",
                deadline_ms=deadline_ms,
            )
            raise
        await self.publish(claimed, completion, deadline_ms=deadline_ms)
        return True

    def _request(
        self,
        *,
        target_scope: Scope,
        required_operation: str,
        trace: Trace,
        record_id: Id | None = None,
    ) -> MutationRequest:
        return MutationRequest(
            operation=MemoryOperation(required_operation),
            actor_scope=self._client.scope,
            target_scope=target_scope,
            revision=RevisionPrecondition.must_not_exist(),
            trace=trace,
            record_id=record_id,
            category=None,
        )

    def _job_request(self, claimed: ClaimedMaintenanceJob) -> MutationRequest:
        if claimed.job.actor_scope != self._client.scope:
            raise ValueError("claimed job actor scope does not match the memory client")
        return self._request(
            target_scope=claimed.job.scope,
            required_operation=claimed.job.required_operation,
            trace=claimed.job.trace,
            record_id=Id(claimed.job.job_id),
        )


def publication_request(
    job: MaintenanceJob,
    lease: LeaseReceipt,
    *,
    current_input_digest: str,
    current_config_digest: str,
) -> dict[str, Any]:
    if job.job_id != lease.job_id:
        raise ValueError("lease does not belong to the maintenance job")
    for name, digest in (
        ("current_input_digest", current_input_digest),
        ("current_config_digest", current_config_digest),
    ):
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError(f"{name} is invalid")
    return {
        "job_id": job.job_id,
        "actor_scope": job.actor_scope.to_wire(),
        "target_scope": job.scope.to_wire(),
        "required_operation": job.required_operation,
        "fencing_token": lease.fencing_token,
        "expected_input_digest": job.input_digest,
        "expected_config_digest": job.config_digest,
        "current_input_digest": current_input_digest,
        "current_config_digest": current_config_digest,
        "trace": job.trace.to_wire(),
    }


def settlement_request(
    reservation: ModelReservation, actual: ActualUsage
) -> dict[str, Any]:
    return {
        "reservation_id": reservation.reservation_id,
        "job_id": reservation.job_id,
        "reserved": reservation.reserved.to_wire(),
        "actual": actual.to_wire(),
        "unknown_fields": sorted(actual.unknown_fields),
    }


def require_explicit_relocation_authority(
    source: Scope,
    destination: Scope,
    grant: Mapping[str, Any] | None,
    *,
    now_ms: int,
) -> None:
    if source == destination:
        return
    if grant is None:
        raise PermissionError("AUTHORIZATION_DENIED")
    operations = grant.get("operations")
    target = grant.get("target_scope")
    expires_at_ms = grant.get("expires_at_ms")
    revoked_at_ms = grant.get("revoked_at_ms")
    if (
        not isinstance(operations, list)
        or "relocate" not in operations
        or target != destination.to_wire()
        or revoked_at_ms is not None
        or (expires_at_ms is not None and expires_at_ms <= now_ms)
    ):
        raise PermissionError("AUTHORIZATION_DENIED")
