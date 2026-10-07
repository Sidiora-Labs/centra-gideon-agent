"""Authenticated typed client facade for Hypermid memory operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .contracts import (
        SmartNoteEvaluation,
        KnowledgeVerification,
        KnowledgeSharingJudgment,
    )

from collections.abc import Mapping, Sequence

from .client import HypermidClient, HypermidProtocolError
from .contracts import (
    AccessRequest,
    GideonLegacySnapshot,
    KnowledgePublication,
    KnowledgePublicationAuthority,
    KnowledgePublicationReceipt,
    MaintenanceClaimProof,
    MaintenanceClaimReceipt,
    MaintenanceJobSpec,
    MaintenanceTerminalState,
    MemoryContractError,
    MemoryDiagnostics,
    MemoryHealth,
    MemoryOperation,
    MemoryRecord,
    MutationReceipt,
    MutationRequest,
    RecordDraft,
    RecordPage,
    RecordStatus,
    RecordView,
    RelocationReceipt,
    SearchRequest,
    SearchResponse,
    ServiceResult,
    ShareGrant,
    ShareGrantDraft,
    SmartNoteCandidatePage,
    SourceSnapshot,
    SplitReceipt,
    SummaryPublication,
    VerificationState,
)
from .embeddings import EmbeddingRegistration
from .foundation import Cursor, Digest, Id, Scope, Trace
from .models import JsonValue
from .portability import LegacyImportReceipt, MemoryExportBundle, MemoryImportBatch


class NativeMemoryAuthorityDenied(PermissionError):
    """A typed local request violates this authenticated native memory actor scope."""


class MemoryClient:
    """Route memory calls through one authenticated Hypermid session."""

    def __init__(self, client: HypermidClient, *, capability_id: Id) -> None:
        self.client = client
        self.capability_id = Id(capability_id)

    @property
    def scope(self) -> Scope:
        return self.client.scope

    async def _call(
        self,
        operation: str,
        payload: Mapping[str, JsonValue],
        *,
        trace: Trace,
        durable: bool = False,
        capability: bool = True,
    ) -> Mapping[str, JsonValue]:
        body = dict(payload)
        if capability:
            body["capability_id"] = str(self.capability_id)
        value = await self.client.request(
            operation,
            body,
            trace=trace,
            effect_kind="durable" if durable else "query",
            scope=self.scope,
        )
        try:
            response = ServiceResult.from_wire(value, trace)
        except (MemoryContractError, ValueError, TypeError) as exc:
            raise HypermidProtocolError(
                f"daemon returned an invalid {operation} response"
            ) from exc
        return response.result

    def _mutation(self, request: MutationRequest, expected: MemoryOperation) -> None:
        if request.operation is not expected:
            raise ValueError(f"mutation request must use {expected.value}")
        if request.actor_scope != self.scope:
            raise NativeMemoryAuthorityDenied(
                "mutation actor scope does not match the authenticated scope"
            )

    def _access(self, request: AccessRequest) -> None:
        if request.actor_scope != self.scope:
            raise NativeMemoryAuthorityDenied(
                "access actor scope does not match the authenticated scope"
            )

    def _job_spec(self, request: MutationRequest, spec: MaintenanceJobSpec) -> None:
        self._mutation(request, spec.required_operation)
        if request.record_id != spec.id:
            raise ValueError("maintenance request does not identify its job")
        if (
            request.actor_scope != spec.actor_scope
            or request.target_scope != spec.target_scope
        ):
            raise ValueError("maintenance job scopes do not match its request")

    def _claim(self, request: MutationRequest, proof: MaintenanceClaimProof) -> None:
        self._mutation(request, request.operation)
        if request.record_id != proof.job_id:
            raise ValueError("maintenance request does not identify its claimed job")

    def _portability(
        self,
        request: MutationRequest,
        operation: MemoryOperation,
    ) -> None:
        self._mutation(request, operation)
        if request.record_id is not None:
            raise ValueError(
                "portability requests must use the memory-portability resource"
            )

    async def health(self, *, trace: Trace) -> MemoryHealth:
        return MemoryHealth.from_wire(
            await self._call("memory.health", {}, trace=trace, capability=False)
        )

    async def drain(self, *, trace: Trace) -> MemoryHealth:
        return MemoryHealth.from_wire(
            await self._call("memory.drain", {}, trace=trace, durable=True)
        )

    async def create(
        self,
        request: MutationRequest,
        draft: RecordDraft,
        *,
        sources: Sequence[SourceSnapshot] = (),
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> MutationReceipt:
        self._mutation(request, MemoryOperation.CREATE)
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "draft": draft.to_wire(),
            "sources": [source.to_wire() for source in sources],
            "now_ms": now_ms,
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        value = await self._call(
            "memory.record.create",
            payload,
            trace=request.trace,
            durable=True,
        )
        return MutationReceipt.from_wire(value)

    async def update(
        self,
        request: MutationRequest,
        draft: RecordDraft,
        *,
        sources: Sequence[SourceSnapshot] = (),
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> MutationReceipt:
        self._mutation(request, MemoryOperation.UPDATE)
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "draft": draft.to_wire(),
            "sources": [source.to_wire() for source in sources],
            "now_ms": now_ms,
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        value = await self._call(
            "memory.record.update",
            payload,
            trace=request.trace,
            durable=True,
        )
        return MutationReceipt.from_wire(value)

    async def capture_origins(self, request: AccessRequest) -> Mapping[str, JsonValue]:
        self._access(request)
        if request.operation.value != "read":
            raise ValueError("capture origin inspection requires read authority")
        return await self._call(
            "memory.record.origins",
            {"request": request.to_wire(), "authority_resource": "memory-records"},
            trace=request.trace,
        )

    async def write_captured(
        self,
        request: MutationRequest,
        draft: RecordDraft,
        *,
        sources: Sequence[SourceSnapshot],
        capture,
        writer_lease: Mapping,
        now_ms: int,
    ) -> MutationReceipt:
        from .contracts import OwnerWordCapture

        if request.operation not in {MemoryOperation.CREATE, MemoryOperation.UPDATE}:
            raise ValueError("captured writes require create or update")
        self._mutation(request, request.operation)
        if not isinstance(capture, OwnerWordCapture):
            raise TypeError("captured write requires typed owner-word evidence")
        value = await self._call(
            "memory.record." + request.operation.value,
            {
                "request": request.to_wire(),
                "draft": draft.to_wire(),
                "sources": [source.to_wire() for source in sources],
                "capture": capture.to_wire(),
                "writer_lease": dict(writer_lease),
                "now_ms": now_ms,
                "authority_resource": "memory-records",
            },
            trace=request.trace,
            durable=True,
        )
        return MutationReceipt.from_wire(value)

    async def retract_chat(
        self,
        request: MutationRequest,
        *,
        history_session_id: Id,
        expected_cursor: Cursor,
        writer_lease: Mapping,
    ):
        from .contracts import ChatRetraction

        self._mutation(request, MemoryOperation.DELETE)
        if request.record_id != Id("memory-records") or request.category is not None:
            raise ValueError("chat retraction requires exact record collection")
        value = await self._call(
            "memory.chat.retract",
            {
                "request": request.to_wire(),
                "history_session_id": str(history_session_id),
                "expected_cursor": expected_cursor.to_wire(),
                "writer_lease": dict(writer_lease),
            },
            trace=request.trace,
            durable=True,
        )
        return ChatRetraction.from_wire(value)

    async def _record_state(
        self,
        operation: MemoryOperation,
        request: MutationRequest,
        now_ms: int,
        authority_resource: Id | None,
    ) -> MutationReceipt:
        self._mutation(request, operation)
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "now_ms": now_ms,
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        value = await self._call(
            f"memory.record.{operation.value}",
            payload,
            trace=request.trace,
            durable=True,
        )
        return MutationReceipt.from_wire(value)

    async def archive(
        self,
        request: MutationRequest,
        *,
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> MutationReceipt:
        return await self._record_state(
            MemoryOperation.ARCHIVE, request, now_ms, authority_resource
        )

    async def restore(
        self,
        request: MutationRequest,
        *,
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> MutationReceipt:
        return await self._record_state(
            MemoryOperation.RESTORE, request, now_ms, authority_resource
        )

    async def delete(
        self,
        request: MutationRequest,
        *,
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> MutationReceipt:
        return await self._record_state(
            MemoryOperation.DELETE, request, now_ms, authority_resource
        )

    async def purge(
        self,
        request: MutationRequest,
        *,
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> Cursor:
        self._mutation(request, MemoryOperation.PURGE)
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "now_ms": now_ms,
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        value = await self._call(
            "memory.record.purge",
            payload,
            trace=request.trace,
            durable=True,
        )
        raw_cursor = value.get("cursor", value)
        if not isinstance(raw_cursor, Mapping):
            raise HypermidProtocolError("daemon returned an invalid purge cursor")
        return Cursor.from_wire(raw_cursor)

    async def verify(
        self,
        request: MutationRequest,
        *,
        state: VerificationState,
        confidence: float,
        evidence_source_id: Id | None = None,
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> MutationReceipt:
        self._mutation(request, MemoryOperation.VERIFY)
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "state": VerificationState(state).value,
            "confidence": confidence,
            "evidence_source_id": (
                str(evidence_source_id) if evidence_source_id else None
            ),
            "now_ms": now_ms,
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        value = await self._call(
            "memory.record.verify",
            payload,
            trace=request.trace,
            durable=True,
        )
        return MutationReceipt.from_wire(value)

    async def merge(
        self,
        request: MutationRequest,
        draft: RecordDraft,
        *,
        source_revisions: Sequence[tuple[Id, Digest]],
        sources: Sequence[SourceSnapshot] = (),
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> MutationReceipt:
        self._mutation(request, MemoryOperation.MERGE)
        if len(source_revisions) < 2:
            raise ValueError("merge requires at least two source revisions")
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "draft": draft.to_wire(),
            "source_revisions": [
                {"id": str(record_id), "digest": str(digest)}
                for record_id, digest in source_revisions
            ],
            "sources": [source.to_wire() for source in sources],
            "now_ms": now_ms,
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        value = await self._call(
            "memory.record.merge",
            payload,
            trace=request.trace,
            durable=True,
        )
        return MutationReceipt.from_wire(value)

    async def split(
        self,
        request: MutationRequest,
        replacements: Sequence[RecordDraft],
        *,
        now_ms: int,
        authority_resource: Id | None = None,
    ) -> SplitReceipt:
        self._mutation(request, MemoryOperation.SPLIT)
        if len(replacements) < 2:
            raise ValueError("split requires at least two replacement records")
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "replacements": [record.to_wire() for record in replacements],
            "now_ms": now_ms,
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        value = await self._call(
            "memory.record.split",
            payload,
            trace=request.trace,
            durable=True,
        )
        return SplitReceipt.from_wire(value)

    async def relocate(
        self,
        source_request: MutationRequest,
        destination_request: MutationRequest,
        *,
        destination_capability_id: Id,
        now_ms: int,
        source_authority_resource: Id | None = None,
        destination_authority_resource: Id | None = None,
    ) -> RelocationReceipt:
        self._mutation(source_request, MemoryOperation.RELOCATE)
        if destination_request.operation is not MemoryOperation.RELOCATE:
            raise ValueError("destination request must use relocate")
        payload: dict[str, JsonValue] = {
            "source_request": source_request.to_wire(),
            "destination_request": destination_request.to_wire(),
            "destination_capability_id": str(destination_capability_id),
            "now_ms": now_ms,
        }
        if source_authority_resource is not None:
            payload["source_authority_resource"] = str(source_authority_resource)
        if destination_authority_resource is not None:
            payload["destination_authority_resource"] = str(
                destination_authority_resource
            )
        value = await self._call(
            "memory.record.relocate",
            payload,
            trace=source_request.trace,
            durable=True,
        )
        return RelocationReceipt.from_wire(value)

    async def get(
        self, request: AccessRequest, *, authority_resource: Id | None = None
    ) -> tuple[MemoryRecord | None, Cursor]:
        self._access(request)
        payload: dict[str, JsonValue] = {"request": request.to_wire()}
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        page = RecordPage.from_wire(
            await self._call(
                "memory.record.get",
                payload,
                trace=request.trace,
            )
        )
        return (page.records[0] if page.records else None, page.cursor)

    async def shared_record(
        self,
        request: AccessRequest,
        policy_digest: Digest,
        *,
        authority_resource: Id | None = None,
    ) -> RecordView:
        self._access(request)
        if request.operation.value != "read":
            raise ValueError("shared record inspection requires read access")
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "policy_digest": str(Digest(policy_digest)),
        }
        if authority_resource is not None:
            payload["authority_resource"] = str(authority_resource)
        return RecordView.from_wire(
            await self._call(
                "memory.record.shared_get",
                payload,
                trace=request.trace,
            )
        )

    async def list(
        self,
        request: AccessRequest,
        *,
        cursor: Cursor | None = None,
        after_id: Id | None = None,
        ordered_by_id: bool = False,
        category: str | None = None,
        status: RecordStatus | None = None,
        limit: int = 100,
    ) -> RecordPage:
        self._access(request)
        if not 1 <= limit <= 1000:
            raise ValueError("record list limit must be between 1 and 1000")
        payload: dict[str, JsonValue] = {
            "request": request.to_wire(),
            "limit": limit,
        }
        if after_id is not None or ordered_by_id:
            payload["ordered_by_id"] = True
            payload["after_id"] = str(after_id) if after_id is not None else None
        if category is not None:
            payload["category"] = category
        if status is not None:
            payload["status"] = RecordStatus(status).value
        if cursor is not None:
            payload["cursor"] = cursor.to_wire()
        return RecordPage.from_wire(
            await self._call("memory.record.list", payload, trace=request.trace)
        )

    async def smart_note_candidates(
        self,
        request: AccessRequest,
        *,
        limit: int = 100,
    ) -> SmartNoteCandidatePage:
        self._access(request)
        if request.operation.value != "read":
            raise ValueError("smart-note candidates require read access")
        if str(request.resource_id) != "memory-records":
            raise ValueError(
                "smart-note candidates require the memory-records resource"
            )
        if not 1 <= limit <= 1000:
            raise ValueError("smart-note candidate limit must be between 1 and 1000")
        return SmartNoteCandidatePage.from_wire(
            await self._call(
                "memory.smart_notes.candidates",
                {"request": request.to_wire(), "limit": limit},
                trace=request.trace,
            )
        )

    async def search(
        self,
        access: AccessRequest,
        request: SearchRequest,
    ) -> SearchResponse:
        self._access(access)
        if access.operation.value != "search":
            raise ValueError("memory search requires search access")
        if access.trace != request.trace:
            raise ValueError("search access and search request traces differ")
        return SearchResponse.from_wire(
            await self._call(
                "memory.search",
                {
                    "request": access.to_wire(),
                    "search": request.to_wire(access.target_scope),
                },
                trace=request.trace,
            ),
            request.trace,
        )

    async def maintenance_enqueue(
        self,
        request: MutationRequest,
        spec: MaintenanceJobSpec,
    ) -> ServiceResult:
        self._job_spec(request, spec)
        result = await self._call(
            "memory.maintenance.enqueue",
            {"request": request.to_wire(), "spec": spec.to_wire()},
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def maintenance_claim(
        self,
        request: MutationRequest,
        *,
        worker_id: Id,
        ttl_ms: int,
    ) -> MaintenanceClaimReceipt | None:
        self._mutation(request, request.operation)
        if request.record_id is not None:
            raise ValueError("maintenance claim must address the queue")
        result = await self._call(
            "memory.maintenance.claim",
            {
                "request": request.to_wire(),
                "worker_id": str(worker_id),
                "ttl_ms": ttl_ms,
            },
            trace=request.trace,
            durable=True,
        )
        claim = result.get("claim")
        return None if claim is None else MaintenanceClaimReceipt.from_wire(claim)

    async def maintenance_heartbeat(
        self,
        request: MutationRequest,
        *,
        proof: MaintenanceClaimProof,
        ttl_ms: int,
    ) -> MaintenanceClaimReceipt:
        self._claim(request, proof)
        result = await self._call(
            "memory.maintenance.heartbeat",
            {
                "request": request.to_wire(),
                "ttl_ms": ttl_ms,
                "claim": proof.to_wire(),
            },
            trace=request.trace,
            durable=True,
        )
        return MaintenanceClaimReceipt.from_wire(result)

    async def maintenance_checkpoint(
        self,
        request: MutationRequest,
        *,
        proof: MaintenanceClaimProof,
        cursor: Cursor,
        available_at_ms: int,
    ) -> ServiceResult:
        self._claim(request, proof)
        result = await self._call(
            "memory.maintenance.checkpoint",
            {
                "request": request.to_wire(),
                "claim": proof.to_wire(),
                "cursor": cursor.to_wire(),
                "available_at_ms": available_at_ms,
            },
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def maintenance_publish(
        self,
        request: MutationRequest,
        summary_request: MutationRequest,
        publication: SummaryPublication,
        *,
        summary_capability_id: Id,
        proof: MaintenanceClaimProof,
    ) -> ServiceResult:
        self._claim(request, proof)
        self._mutation(summary_request, MemoryOperation.CREATE)
        if summary_request.trace != request.trace:
            raise ValueError("maintenance and summary request traces differ")
        if publication.job_id != request.record_id:
            raise ValueError("maintenance publication does not match its job request")
        if publication.job_id != proof.job_id:
            raise ValueError("maintenance publication does not match its claim")
        if publication.draft.id != summary_request.record_id:
            raise ValueError("summary publication does not match its create request")
        result = await self._call(
            "memory.maintenance.publish",
            {
                "request": request.to_wire(),
                "summary_capability_id": str(summary_capability_id),
                "summary_request": summary_request.to_wire(),
                "publication": publication.to_wire(),
                "claim": proof.to_wire(),
            },
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def maintenance_publish_knowledge(
        self,
        request: MutationRequest,
        publication: KnowledgePublication,
        *,
        proof: MaintenanceClaimProof,
        smart_notes: Sequence[KnowledgePublicationAuthority] = (),
        verifications: Sequence[KnowledgePublicationAuthority] = (),
        sharing_judgments: Sequence[KnowledgePublicationAuthority] = (),
        recall: KnowledgePublicationAuthority | None = None,
    ) -> KnowledgePublicationReceipt:
        self._claim(request, proof)
        if request.operation is not MemoryOperation.INDEX:
            raise ValueError("knowledge publication requires an index job")
        if (
            publication.job_id != request.record_id
            or publication.job_id != proof.job_id
        ):
            raise ValueError("knowledge publication does not match its claimed job")
        self._knowledge_authorities(
            request,
            publication,
            smart_notes,
            verifications,
            sharing_judgments,
            recall,
        )
        result = await self._call(
            "memory.maintenance.publish_knowledge",
            {
                "request": request.to_wire(),
                "claim": proof.to_wire(),
                "publication": publication.to_wire(),
                "smart_notes": [authority.to_wire() for authority in smart_notes],
                "verifications": [authority.to_wire() for authority in verifications],
                "sharing_judgments": [
                    authority.to_wire() for authority in sharing_judgments
                ],
                "recall": recall.to_wire() if recall is not None else None,
            },
            trace=request.trace,
            durable=True,
        )
        receipt = KnowledgePublicationReceipt.from_wire(result)
        if (
            receipt.job_id != publication.job_id
            or receipt.source_digest != publication.source.source_digest
        ):
            raise HypermidProtocolError(
                "daemon returned a knowledge receipt for different publication inputs"
            )
        return receipt

    def _knowledge_authorities(
        self,
        job_request: MutationRequest,
        publication: KnowledgePublication,
        smart_notes: Sequence[KnowledgePublicationAuthority],
        verifications: Sequence[KnowledgePublicationAuthority],
        sharing_judgments: Sequence[KnowledgePublicationAuthority],
        recall: KnowledgePublicationAuthority | None,
    ) -> None:
        arms: tuple[
            tuple[
                Sequence[
                    SmartNoteEvaluation
                    | KnowledgeVerification
                    | KnowledgeSharingJudgment
                ],
                Sequence[KnowledgePublicationAuthority],
                MemoryOperation,
                str,
            ],
            ...,
        ] = (
            (
                publication.smart_notes,
                smart_notes,
                MemoryOperation.INDEX,
                "smart-note",
            ),
            (
                publication.verifications,
                verifications,
                MemoryOperation.VERIFY,
                "verification",
            ),
            (
                publication.sharing_judgments,
                sharing_judgments,
                MemoryOperation.VERIFY,
                "sharing",
            ),
        )
        for outputs, authorities, operation, name in arms:
            output_ids = {item.record_id for item in outputs}
            authority_ids: set[Id] = set()
            for authority in authorities:
                mutation = authority.request
                self._mutation(mutation, operation)
                if mutation.target_scope != job_request.target_scope:
                    raise ValueError(
                        f"knowledge {name} target scope differs from the job"
                    )
                if mutation.record_id is None or mutation.category is None:
                    raise ValueError(
                        f"knowledge {name} authority requires a categorized record"
                    )
                authority_ids.add(mutation.record_id)
            if output_ids != authority_ids or len(authority_ids) != len(authorities):
                raise ValueError(
                    f"knowledge {name} authorities must match publication records exactly"
                )
            for output, authority in zip(
                sorted(outputs, key=lambda item: str(item.record_id)),
                sorted(authorities, key=lambda item: str(item.request.record_id)),
            ):
                if authority.request.revision.digest != output.expected_revision_digest:
                    raise ValueError(
                        f"knowledge {name} authority revision differs from its output"
                    )
        if publication.recall is None:
            if recall is not None:
                raise ValueError("knowledge recall authority has no recall output")
            return
        if recall is None:
            raise ValueError("knowledge recall output requires create authority")
        self._mutation(recall.request, MemoryOperation.CREATE)
        if (
            recall.request.target_scope != job_request.target_scope
            or publication.recall.draft.scope != job_request.target_scope
            or recall.request.record_id != publication.recall.draft.id
            or recall.request.category != publication.recall.draft.category
            or recall.request.revision.digest is not None
        ):
            raise ValueError("knowledge recall authority does not match its draft")

    async def maintenance_status(
        self, request: MutationRequest, *, job_id: Id
    ) -> ServiceResult:
        self._mutation(request, request.operation)
        if request.record_id != job_id:
            raise ValueError("maintenance status request does not identify its job")
        result = await self._call(
            "memory.maintenance.status",
            {"request": request.to_wire(), "job_id": str(job_id)},
            trace=request.trace,
        )
        return ServiceResult(request.trace, result)

    async def maintenance_cancel(
        self,
        request: MutationRequest,
        *,
        proof: MaintenanceClaimProof,
    ) -> ServiceResult:
        self._claim(request, proof)
        result = await self._call(
            "memory.maintenance.cancel",
            {
                "request": request.to_wire(),
                "claim": proof.to_wire(),
            },
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def maintenance_terminate(
        self,
        request: MutationRequest,
        *,
        proof: MaintenanceClaimProof,
        state: MaintenanceTerminalState,
        error_code: str | None = None,
    ) -> ServiceResult:
        self._claim(request, proof)
        result = await self._call(
            "memory.maintenance.terminate",
            {
                "request": request.to_wire(),
                "claim": proof.to_wire(),
                "state": MaintenanceTerminalState(state).value,
                "error_code": error_code,
            },
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def embedding_register(
        self,
        request: MutationRequest,
        registration: EmbeddingRegistration,
        *,
        now_ms: int,
        rebind: bool = False,
    ) -> ServiceResult:
        self._mutation(request, MemoryOperation.EMBED)
        result = await self._call(
            "memory.embedding.rebind" if rebind else "memory.embedding.register",
            {
                "request": request.to_wire(),
                "registration": {
                    "registration_id": registration.registration_id,
                    "owner_scope": registration.scope.to_wire(),
                    "mode": registration.mode,
                    "provider_identity": registration.provider_identity,
                    "model_id": registration.model_id,
                    "dimensions": registration.dimensions,
                    "normalized": registration.normalized,
                    "fingerprint": registration.fingerprint,
                    "state": registration.state,
                },
                "now_ms": now_ms,
            },
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def embedding_publish(
        self,
        request: MutationRequest,
        *,
        guard: Mapping[str, JsonValue],
        response: Mapping[str, JsonValue],
        now_ms: int,
    ) -> ServiceResult:
        self._mutation(request, MemoryOperation.EMBED)
        result = await self._call(
            "memory.embedding.publish",
            {
                "request": request.to_wire(),
                "guard": dict(guard),
                "response": dict(response),
                "now_ms": now_ms,
            },
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def embedding_retire(
        self,
        request: MutationRequest,
        *,
        registration_id: Id,
        now_ms: int,
    ) -> ServiceResult:
        self._mutation(request, MemoryOperation.EMBED)
        result = await self._call(
            "memory.embedding.retire",
            {
                "request": request.to_wire(),
                "registration_id": str(registration_id),
                "now_ms": now_ms,
            },
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def embedding_active(self, request: AccessRequest) -> ServiceResult:
        self._access(request)
        result = await self._call(
            "memory.embedding.active",
            {"request": request.to_wire()},
            trace=request.trace,
        )
        return ServiceResult(request.trace, result)

    async def index_enqueue(
        self, request: MutationRequest, spec: MaintenanceJobSpec
    ) -> ServiceResult:
        self._job_spec(request, spec)
        if request.operation is not MemoryOperation.INDEX:
            raise ValueError("index enqueue requires an index operation")
        result = await self._call(
            "memory.index.enqueue",
            {"request": request.to_wire(), "spec": spec.to_wire()},
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def summary_enqueue(
        self, request: MutationRequest, spec: MaintenanceJobSpec
    ) -> ServiceResult:
        self._job_spec(request, spec)
        if request.operation is not MemoryOperation.SUMMARIZE:
            raise ValueError("summary enqueue requires a summarize operation")
        result = await self._call(
            "memory.summary.enqueue",
            {"request": request.to_wire(), "spec": spec.to_wire()},
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def prepare_import(
        self, request: MutationRequest, spec: MaintenanceJobSpec
    ) -> ServiceResult:
        self._portability(request, MemoryOperation.IMPORT)
        if (
            request.actor_scope != spec.actor_scope
            or request.target_scope != spec.target_scope
        ):
            raise ValueError("import preparation job scopes do not match its request")
        if spec.required_operation is not MemoryOperation.IMPORT:
            raise ValueError("import preparation job must require import authority")
        result = await self._call(
            "memory.import.prepare",
            {"request": request.to_wire(), "spec": spec.to_wire()},
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def prepare_export(
        self, request: MutationRequest, spec: MaintenanceJobSpec
    ) -> ServiceResult:
        self._portability(request, MemoryOperation.EXPORT)
        if (
            request.actor_scope != spec.actor_scope
            or request.target_scope != spec.target_scope
        ):
            raise ValueError("export preparation job scopes do not match its request")
        if spec.required_operation is not MemoryOperation.EXPORT:
            raise ValueError("export preparation job must require export authority")
        result = await self._call(
            "memory.export.prepare",
            {"request": request.to_wire(), "spec": spec.to_wire()},
            trace=request.trace,
            durable=True,
        )
        return ServiceResult(request.trace, result)

    async def export_scope(
        self,
        request: MutationRequest,
        *,
        export_id: Id,
        include_grants: bool = False,
    ) -> MemoryExportBundle:
        self._portability(request, MemoryOperation.EXPORT)
        if include_grants:
            raise ValueError("memory export does not permit reusable grant material")
        result = await self._call(
            "memory.export.scope",
            {
                "request": request.to_wire(),
                "export_id": str(export_id),
                "include_grants": False,
            },
            trace=request.trace,
            durable=True,
        )
        return MemoryExportBundle.from_mapping(result)

    async def stage_import(
        self,
        request: MutationRequest,
        *,
        batch_id: Id,
        bundle: MemoryExportBundle,
        target_scope: Scope,
        scope_mapping: Mapping[str, Scope],
    ) -> MemoryImportBatch:
        self._portability(request, MemoryOperation.IMPORT)
        if request.target_scope != target_scope:
            raise ValueError("import target does not match the authorized target scope")
        result = await self._call(
            "memory.import.stage",
            {
                "request": request.to_wire(),
                "batch_id": str(batch_id),
                "bundle": bundle.to_mapping(),
                "target_scope": target_scope.to_wire(),
                "scope_mapping": {
                    key: scope.to_wire() for key, scope in sorted(scope_mapping.items())
                },
            },
            trace=request.trace,
            durable=True,
        )
        return MemoryImportBatch.from_mapping(result)

    async def apply_import(
        self,
        request: MutationRequest,
        *,
        batch: MemoryImportBatch,
        bundle: MemoryExportBundle,
    ) -> MemoryImportBatch:
        self._portability(request, MemoryOperation.IMPORT)
        result = await self._call(
            "memory.import.apply",
            {
                "request": request.to_wire(),
                "batch": batch.to_mapping(),
                "bundle": bundle.to_mapping(),
            },
            trace=request.trace,
            durable=True,
        )
        return MemoryImportBatch.from_mapping(result)

    async def import_legacy(
        self,
        request: MutationRequest,
        *,
        batch_id: Id,
        snapshot: GideonLegacySnapshot,
    ) -> LegacyImportReceipt:
        self._portability(request, MemoryOperation.IMPORT)
        if request.target_scope != snapshot.scope:
            raise ValueError("legacy snapshot scope does not match the import target")
        result = await self._call(
            "memory.import.legacy",
            {
                "request": request.to_wire(),
                "batch_id": str(batch_id),
                "snapshot": snapshot.to_wire(),
            },
            trace=request.trace,
            durable=True,
        )
        return LegacyImportReceipt.from_mapping(result)

    async def diagnostics(self, request: AccessRequest) -> MemoryDiagnostics:
        self._access(request)
        result = await self._call(
            "memory.diagnostics",
            {"request": request.to_wire()},
            trace=request.trace,
        )
        return MemoryDiagnostics.from_wire(result)

    async def create_share_grant(
        self, draft: ShareGrantDraft, *, trace: Trace
    ) -> ShareGrant:
        result = await self._call(
            "memory.grant.create",
            {"draft": draft.to_wire()},
            trace=trace,
            durable=True,
        )
        return ShareGrant.from_wire(result)

    async def revoke_share_grant(
        self,
        owner_scope: Scope,
        grant_id: Id,
        *,
        expected_revision: int,
        trace: Trace,
    ) -> ShareGrant:
        result = await self._call(
            "memory.grant.revoke",
            {
                "owner_scope": owner_scope.to_wire(),
                "grant_id": str(grant_id),
                "expected_revision": expected_revision,
            },
            trace=trace,
            durable=True,
        )
        return ShareGrant.from_wire(result)

    async def list_share_grants(
        self, owner_scope: Scope, *, trace: Trace
    ) -> tuple[ShareGrant, ...]:
        result = await self._call(
            "memory.grant.list",
            {"owner_scope": owner_scope.to_wire()},
            trace=trace,
        )
        grants = result.get("grants")
        if not isinstance(grants, list):
            raise HypermidProtocolError("daemon returned an invalid memory grant list")
        return tuple(ShareGrant.from_wire(item) for item in grants)
