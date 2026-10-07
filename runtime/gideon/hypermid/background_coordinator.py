"""Fenced background work composed with Gideon's budget and model authorities."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from gideon.cognition.history import HistoryConsolidator

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from gideon.cognition.bg_compress import (
    quiesce_background_compression,
    resume_background_compression,
)

from .budgets import AdmissionRequest
from .contracts import (
    KnowledgePublication,
    KnowledgePublicationAuthority,
    KnowledgePublicationReceipt,
    KnowledgeRecall,
    KnowledgeSharingJudgment,
    KnowledgeVerification,
    MemoryOperation,
    MutationRequest,
    ProvenanceSpan,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
    SmartNoteCandidatePage,
    SmartNoteEvaluation,
    SourceKind,
    SourceSnapshot,
)
from .foundation import Digest, Id
from .history import HistoryJournal, JournalRange
from .maintenance import (
    ClaimedMaintenanceJob,
    KnowledgeMaintenanceCompletion,
    KnowledgeMaintenancePublication,
    MaintenanceCompletion,
    MaintenanceScheduler,
    SummaryMaintenancePublication,
)
from .models import Cursor, JsonValue, Scope, Trace
from .sources import GitSourceCapture, scope_digest
from .summarizer import (
    SummaryCandidate,
    SummaryJob,
    SummaryModelAuthority,
    summarize_journal_job,
)
from .usage import UsageAccountingConsumer, UsageReconciliationEvent

PrivacyMode = Literal["persistent", "temporary", "incognito"]
RunOutcome = Literal["no_job", "no_call", "measured", "partial", "unknown"]
_summary_work_quiesced = False
_active_summary_work: set[asyncio.Task[object]] = set()


@dataclass(frozen=True, slots=True)
class SummaryQuiesceReceipt:
    drained_tasks: int


async def quiesce_summary_work(*, timeout: float = 3.0) -> SummaryQuiesceReceipt:
    """Fence new summary admissions and drain every already admitted execution."""
    global _summary_work_quiesced
    if not 0.0 < timeout <= 300.0:
        raise ValueError("summary quiesce timeout is outside the supported range")
    _summary_work_quiesced = True
    current = asyncio.current_task()
    active = {
        task for task in _active_summary_work if task is not current and not task.done()
    }
    if active:
        done, pending = await asyncio.wait(active, timeout=timeout)
        if pending:
            raise TimeoutError("background summary work did not quiesce before cutover")
        for task in done:
            task.result()
    return SummaryQuiesceReceipt(len(active))


def resume_summary_work() -> None:
    global _summary_work_quiesced
    _summary_work_quiesced = False


@dataclass(frozen=True, slots=True)
class SummaryAdmission:
    provider_id: str
    model_id: str
    region: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_nanodollars: int

    def __post_init__(self) -> None:
        if not self.provider_id or not self.model_id or not self.region:
            raise ValueError("summary admission identity is incomplete")
        for name in (
            "estimated_input_tokens",
            "estimated_output_tokens",
            "estimated_cost_nanodollars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class SummaryWork:
    journal: HistoryJournal
    source_start: Cursor
    source_token_count: int
    authority: SummaryModelAuthority
    admission: SummaryAdmission
    summary_capability_id: Id
    locale: str = "en"
    timeout_seconds: float = 90.0
    consolidator: HistoryConsolidator | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "summary_capability_id", Id(self.summary_capability_id)
        )
        if isinstance(self.source_token_count, bool) or self.source_token_count <= 0:
            raise ValueError("summary source token count must be positive")
        if not 0.0 < self.timeout_seconds <= 300.0:
            raise ValueError("summary timeout is outside the supported range")


@dataclass(frozen=True, slots=True)
class BackgroundRunResult:
    ran: bool
    outcome: RunOutcome
    job_id: str | None = None
    checkpoint_cursor: Cursor | None = None
    usage: UsageReconciliationEvent | None = None
    knowledge: KnowledgePublicationReceipt | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeAuthoritySelector:
    capability_id: Id
    authority_resource: Id | None = None
    category: str | None = None
    trace: Trace | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", Id(self.capability_id))
        if self.authority_resource is not None:
            object.__setattr__(self, "authority_resource", Id(self.authority_resource))
        if self.category is not None and (
            not self.category or len(self.category.encode("utf-8")) > 128
        ):
            raise ValueError("knowledge authority category is invalid")
        if self.trace is not None and not isinstance(self.trace, Trace):
            raise TypeError("knowledge authority trace is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeWork:
    source: SourceSnapshot
    smart_note_page: SmartNoteCandidatePage | None = None
    smart_note_authorities: Mapping[Id, KnowledgeAuthoritySelector] = field(
        default_factory=dict
    )
    verifications: tuple[KnowledgeVerification, ...] = ()
    verification_authorities: Mapping[Id, KnowledgeAuthoritySelector] = field(
        default_factory=dict
    )
    sharing_judgments: tuple[KnowledgeSharingJudgment, ...] = ()
    sharing_authorities: Mapping[Id, KnowledgeAuthoritySelector] = field(
        default_factory=dict
    )
    recall: KnowledgeRecall | None = None
    recall_authority: KnowledgeAuthoritySelector | None = None
    git_capture: GitSourceCapture | None = None
    next_evaluation_at_ms: int | None = None
    usage: UsageReconciliationEvent | None = None

    def __post_init__(self) -> None:
        for name in (
            "smart_note_authorities",
            "verification_authorities",
            "sharing_authorities",
        ):
            raw = getattr(self, name)
            normalized = {Id(key): value for key, value in raw.items()}
            if not all(
                isinstance(value, KnowledgeAuthoritySelector)
                for value in normalized.values()
            ):
                raise TypeError(f"{name} contains an invalid authority selector")
            object.__setattr__(self, name, MappingProxyType(normalized))
        object.__setattr__(self, "verifications", tuple(self.verifications))
        object.__setattr__(self, "sharing_judgments", tuple(self.sharing_judgments))
        if (self.recall is None) != (self.recall_authority is None):
            raise ValueError(
                "knowledge recall and its authority must be supplied together"
            )
        if self.next_evaluation_at_ms is not None and self.next_evaluation_at_ms < 0:
            raise ValueError("knowledge next evaluation timestamp is invalid")


class BackgroundCoordinator:
    def __init__(
        self,
        scheduler: MaintenanceScheduler,
        accounting: UsageAccountingConsumer,
    ) -> None:
        self._scheduler = scheduler
        self._accounting = accounting

    async def run_summary_once(
        self,
        *,
        target_scope: Scope,
        trace: Trace,
        privacy_mode: PrivacyMode,
        lease_ttl_ms: int,
        work_for_job: Callable[
            [ClaimedMaintenanceJob], SummaryWork | Awaitable[SummaryWork]
        ],
        current_config_digest: Callable[[], str],
        deadline_ms: int | None = None,
    ) -> BackgroundRunResult:
        if _summary_work_quiesced:
            return BackgroundRunResult(False, "no_call")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("summary work requires an active asyncio task")
        _active_summary_work.add(task)
        try:
            return await self._run_summary_once(
                target_scope=target_scope,
                trace=trace,
                privacy_mode=privacy_mode,
                lease_ttl_ms=lease_ttl_ms,
                work_for_job=work_for_job,
                current_config_digest=current_config_digest,
                deadline_ms=deadline_ms,
            )
        finally:
            _active_summary_work.discard(task)

    async def run_knowledge_once(
        self,
        *,
        target_scope: Scope,
        trace: Trace,
        privacy_mode: PrivacyMode,
        lease_ttl_ms: int,
        work_for_job: Callable[
            [ClaimedMaintenanceJob], KnowledgeWork | Awaitable[KnowledgeWork]
        ],
        current_config_digest: Callable[[], str],
        deadline_ms: int | None = None,
    ) -> BackgroundRunResult:
        if privacy_mode not in ("persistent", "temporary", "incognito"):
            raise ValueError("privacy mode is invalid")
        if privacy_mode != "persistent" or _summary_work_quiesced:
            return BackgroundRunResult(False, "no_call")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("knowledge work requires an active asyncio task")
        _active_summary_work.add(task)
        completed: tuple[ClaimedMaintenanceJob, KnowledgeWork] | None = None

        async def execute(
            claimed: ClaimedMaintenanceJob,
        ) -> KnowledgeMaintenanceCompletion:
            nonlocal completed
            work = work_for_job(claimed)
            if inspect.isawaitable(work):
                work = await work
            if not isinstance(work, KnowledgeWork):
                raise TypeError("work_for_job must return KnowledgeWork")
            completion = _knowledge_completion(
                claimed,
                work,
                current_config_digest=current_config_digest,
            )
            completed = (claimed, work)
            return completion

        try:
            receipt = await self._scheduler.run_knowledge_once(
                target_scope=target_scope,
                trace=trace,
                lease_ttl_ms=lease_ttl_ms,
                execute=execute,
                deadline_ms=deadline_ms,
            )
        except BaseException as error:
            if _definite_no_call(error):
                return BackgroundRunResult(False, "no_call")
            raise
        finally:
            _active_summary_work.discard(task)
        if receipt is None:
            return BackgroundRunResult(False, "no_job")
        if completed is None:
            raise RuntimeError("knowledge maintenance published without local work")
        claimed, work = completed
        outcome: RunOutcome = work.usage.outcome if work.usage else "no_call"
        return BackgroundRunResult(
            True,
            outcome,
            claimed.job.job_id,
            receipt.output_cursor,
            work.usage,
            receipt,
        )

    async def _run_summary_once(
        self,
        *,
        target_scope: Scope,
        trace: Trace,
        privacy_mode: PrivacyMode,
        lease_ttl_ms: int,
        work_for_job: Callable[
            [ClaimedMaintenanceJob], SummaryWork | Awaitable[SummaryWork]
        ],
        current_config_digest: Callable[[], str],
        deadline_ms: int | None = None,
    ) -> BackgroundRunResult:
        if privacy_mode not in ("persistent", "temporary", "incognito"):
            raise ValueError("privacy mode is invalid")
        if privacy_mode != "persistent":
            return BackgroundRunResult(False, "no_call")

        completed: (
            tuple[ClaimedMaintenanceJob, UsageReconciliationEvent | None] | None
        ) = None

        async def execute(claimed: ClaimedMaintenanceJob) -> MaintenanceCompletion:
            nonlocal completed
            work = work_for_job(claimed)
            if inspect.isawaitable(work):
                work = await work
            if not isinstance(work, SummaryWork):
                raise TypeError("work_for_job must return SummaryWork")
            _validate_summary_work(claimed, work)
            session_id = str(work.journal.session_id)
            source_range = JournalRange(work.source_start, claimed.job.input_cursor)
            if (
                str(work.journal.source_digest(source_range))
                != claimed.job.input_digest
            ):
                raise ValueError(
                    "claimed summary input digest does not match the journal"
                )
            source_content = _summary_source_content(
                work.journal, source_range, claimed.job.input_digest
            )
            config_digest = current_config_digest()
            if config_digest != claimed.job.config_digest:
                raise ValueError("summary configuration changed before execution")

            consolidator_quiesced = False
            reservation = None
            usage_event = None
            dispatched = False

            async def authority(
                messages, max_output_tokens, timeout_seconds, model_session_id
            ):
                nonlocal dispatched
                dispatched = True
                return await work.authority(
                    messages, max_output_tokens, timeout_seconds, model_session_id
                )

            try:
                await quiesce_background_compression([session_id])
                if work.consolidator is not None:
                    await work.consolidator.quiesce(timeout=3.0)
                    consolidator_quiesced = True
                admission = work.admission
                reservation = self._accounting.reserve(
                    AdmissionRequest(
                        reservation_id=_reservation_id(claimed),
                        owner_id=str(claimed.job.scope.owner_id),
                        project_id=str(claimed.job.scope.project_id),
                        job_id=claimed.job.job_id,
                        job_class="summary",
                        provider_id=admission.provider_id,
                        model_id=admission.model_id,
                        region=admission.region,
                        background=True,
                        estimated_input_tokens=admission.estimated_input_tokens,
                        estimated_output_tokens=admission.estimated_output_tokens,
                        estimated_cost_nanodollars=admission.estimated_cost_nanodollars,
                        now_ms=_now_ms(),
                    )
                )
                summary_job = SummaryJob(
                    job_id=claimed.job.job_id,
                    scope=claimed.job.scope,
                    session_id=session_id,
                    source_start=work.source_start,
                    source_end=claimed.job.input_cursor,
                    source_digest=claimed.job.input_digest,
                    lease_id=claimed.lease.fencing_token,
                    lease_expires_at_ms=claimed.lease.expires_at_ms,
                    tier_levels=(0, 1, 2, 3),
                    locale=work.locale,
                    max_input_tokens=claimed.job.budget.max_input_tokens,
                    max_output_tokens=claimed.job.budget.max_output_tokens,
                    attempt=1,
                )
                candidate = await summarize_journal_job(
                    summary_job,
                    work.journal,
                    source_token_count=work.source_token_count,
                    now_ms=_now_ms(),
                    timeout_seconds=work.timeout_seconds,
                    authority=authority,
                )
                usage_event = self._accounting.reconcile_summary(
                    reservation, candidate.usage, ledger_recorded=True
                )
                config_digest = current_config_digest()
                if config_digest != claimed.job.config_digest:
                    raise ValueError("summary configuration changed before publication")
                if (
                    str(work.journal.source_digest(source_range))
                    != claimed.job.input_digest
                ):
                    raise ValueError("summary input changed before publication")
                if (
                    _summary_source_content(
                        work.journal, source_range, claimed.job.input_digest
                    )
                    != source_content
                ):
                    raise ValueError(
                        "summary source content changed before publication"
                    )
                completed = (claimed, usage_event)
                return MaintenanceCompletion(
                    claimed.job.input_digest,
                    config_digest,
                    _summary_publication(claimed, work, candidate, source_content),
                )
            except BaseException as error:
                if reservation is not None and usage_event is None:
                    if not dispatched or _definite_no_call(error):
                        self._accounting.no_call(reservation)
                    else:
                        self._accounting.unknown(reservation, ledger_recorded=False)
                raise
            finally:
                if consolidator_quiesced:
                    cast("HistoryConsolidator", work.consolidator).resume()
                resume_background_compression([session_id])

        try:
            ran = await self._scheduler.run_once(
                target_scope=target_scope,
                required_operation="summarize",
                trace=trace,
                lease_ttl_ms=lease_ttl_ms,
                execute=execute,
                deadline_ms=deadline_ms,
            )
        except BaseException as error:
            if _definite_no_call(error):
                return BackgroundRunResult(False, "no_call")
            raise
        if not ran:
            return BackgroundRunResult(False, "no_job")
        if completed is None:
            raise RuntimeError("maintenance published without a local completion")
        claimed, usage = completed
        outcome: RunOutcome = usage.outcome if usage else "unknown"
        return BackgroundRunResult(
            True, outcome, claimed.job.job_id, claimed.job.input_cursor, usage
        )


def _validate_summary_work(claimed: ClaimedMaintenanceJob, work: SummaryWork) -> None:
    if claimed.job.kind not in ("refresh_summaries", "decay_summaries"):
        raise ValueError("claimed maintenance job is not a summary job")
    if claimed.job.required_operation != "summarize":
        raise ValueError("claimed summary job has the wrong operation")
    if work.journal.scope != claimed.job.scope:
        raise ValueError("summary journal scope does not match the claimed job")
    if work.source_start.epoch != claimed.job.input_cursor.epoch:
        raise ValueError("summary source range crosses journal epochs")
    if work.source_token_count > claimed.job.budget.max_input_tokens:
        raise ValueError("summary source exceeds the claimed input budget")
    if work.admission.estimated_input_tokens < work.source_token_count:
        raise ValueError("summary admission underestimates source tokens")
    if work.admission.estimated_output_tokens > claimed.job.budget.max_output_tokens:
        raise ValueError("summary admission exceeds the claimed output budget")


def _knowledge_completion(
    claimed: ClaimedMaintenanceJob,
    work: KnowledgeWork,
    *,
    current_config_digest: Callable[[], str],
) -> KnowledgeMaintenanceCompletion:
    if claimed.job.kind not in (
        "evaluate_smart_notes",
        "verify_claims",
        "index_git_commits",
    ):
        raise ValueError("claimed maintenance job is not a knowledge-cycle job")
    if claimed.job.required_operation != "index":
        raise ValueError("claimed knowledge job has the wrong operation")
    if work.source.owner_scope_digest != scope_digest(claimed.job.scope):
        raise ValueError("knowledge source scope does not match the claimed job")
    if str(work.source.source_digest) != claimed.job.input_digest:
        raise ValueError("knowledge source digest does not match the claimed job")
    config_digest = current_config_digest()
    if config_digest != claimed.job.config_digest:
        raise ValueError("knowledge configuration changed before execution")
    if work.smart_note_page is not None and (
        work.smart_note_page.cursor != claimed.job.input_cursor
    ):
        raise ValueError("smart-note candidate cursor does not match the claimed job")
    repository_identity: Digest | None = None
    refs_digest: Digest | None = None
    if work.source.kind is SourceKind.GIT_COMMIT:
        if work.git_capture is None or work.git_capture.source != work.source:
            raise ValueError("Git knowledge work requires its guarded source capture")
        if not work.git_capture.revalidate():
            raise ValueError("Git knowledge source changed before execution")
        locator = work.source.locator
        if locator is None or not locator.startswith("git:"):
            raise ValueError("Git knowledge source lacks repository identity")
        repository_identity = Digest(locator.removeprefix("git:"))
        refs_digest = work.git_capture.refs_digest
    elif work.source.kind is SourceKind.FILE:
        if work.git_capture is not None:
            raise ValueError("file knowledge work cannot carry a Git capture")
    else:
        raise ValueError("knowledge work requires a note or Git source")

    now_ms = _now_ms()
    candidates = tuple(
        candidate
        for candidate in (
            work.smart_note_page.candidates if work.smart_note_page is not None else ()
        )
        if (
            candidate.next_evaluation_at_ms is None
            or candidate.next_evaluation_at_ms <= now_ms
        )
        and (
            candidate.last_evaluated_cursor is None
            or candidate.last_evaluated_cursor < claimed.job.input_cursor
        )
    )
    smart_notes = tuple(
        SmartNoteEvaluation.from_candidate(candidate) for candidate in candidates
    )
    smart_authorities = tuple(
        _knowledge_authority(
            claimed,
            candidate.record_id,
            candidate.category,
            candidate.revision_digest,
            MemoryOperation.INDEX,
            _selector(work.smart_note_authorities, candidate.record_id, "smart-note"),
        )
        for candidate in candidates
    )
    verification_authorities = tuple(
        _knowledge_authority(
            claimed,
            item.record_id,
            _selector(
                work.verification_authorities, item.record_id, "verification"
            ).category,
            item.expected_revision_digest,
            MemoryOperation.VERIFY,
            _selector(work.verification_authorities, item.record_id, "verification"),
        )
        for item in work.verifications
    )
    sharing_authorities = tuple(
        _knowledge_authority(
            claimed,
            item.record_id,
            _selector(work.sharing_authorities, item.record_id, "sharing").category,
            item.expected_revision_digest,
            MemoryOperation.VERIFY,
            _selector(work.sharing_authorities, item.record_id, "sharing"),
        )
        for item in work.sharing_judgments
    )
    recall_authority = None
    if work.recall is not None:
        selector = work.recall_authority
        if selector is None:
            raise ValueError("knowledge recall authority is missing")
        recall_authority = _knowledge_authority(
            claimed,
            work.recall.draft.id,
            work.recall.draft.category,
            None,
            MemoryOperation.CREATE,
            selector,
        )
    publication = KnowledgePublication(
        job_id=Id(claimed.job.job_id),
        expected_input_digest=Digest(claimed.job.input_digest),
        expected_config_digest=Digest(config_digest),
        source=work.source,
        repository_identity_digest=repository_identity,
        refs_digest=refs_digest,
        evaluated_cursor=claimed.job.input_cursor,
        next_evaluation_at_ms=work.next_evaluation_at_ms,
        smart_notes=smart_notes,
        verifications=work.verifications,
        sharing_judgments=work.sharing_judgments,
        recall=work.recall,
        now_ms=now_ms,
    )

    def pre_publish_check() -> None:
        if current_config_digest() != claimed.job.config_digest:
            raise ValueError("knowledge configuration changed before publication")
        if work.git_capture is not None and not work.git_capture.revalidate():
            raise ValueError("Git knowledge source changed before publication")

    return KnowledgeMaintenanceCompletion(
        claimed.job.input_digest,
        config_digest,
        KnowledgeMaintenancePublication(
            publication=publication,
            smart_notes=smart_authorities,
            verifications=verification_authorities,
            sharing_judgments=sharing_authorities,
            recall=recall_authority,
        ),
        pre_publish_check,
    )


def _selector(
    selectors: Mapping[Id, KnowledgeAuthoritySelector],
    record_id: Id,
    name: str,
) -> KnowledgeAuthoritySelector:
    try:
        return selectors[record_id]
    except KeyError as exc:
        raise ValueError(
            f"knowledge {name} authority is missing for {record_id}"
        ) from exc


def _knowledge_authority(
    claimed: ClaimedMaintenanceJob,
    record_id: Id,
    category: str | None,
    revision_digest: Digest | None,
    operation: MemoryOperation,
    selector: KnowledgeAuthoritySelector,
) -> KnowledgePublicationAuthority:
    if category is None:
        raise ValueError("knowledge record authority requires its current category")
    if selector.category is not None and selector.category != category:
        raise ValueError("knowledge authority category differs from its record")
    revision = (
        RevisionPrecondition.must_not_exist()
        if revision_digest is None
        else RevisionPrecondition.match(str(revision_digest))
    )
    return KnowledgePublicationAuthority(
        capability_id=selector.capability_id,
        authority_resource=selector.authority_resource,
        request=MutationRequest(
            operation=operation,
            actor_scope=claimed.job.actor_scope,
            target_scope=claimed.job.scope,
            revision=revision,
            trace=selector.trace or claimed.job.trace,
            record_id=record_id,
            category=category,
        ),
    )


def _summary_publication(
    claimed: ClaimedMaintenanceJob,
    work: SummaryWork,
    candidate: SummaryCandidate,
    source_content: str,
) -> SummaryMaintenancePublication:
    summary_id = summary_record_id(claimed)
    source_content_bytes = source_content.encode("utf-8")
    source_content_digest = Digest.sha256(source_content_bytes)
    source_id = Id(
        "source-"
        + hashlib.sha256(
            f"{work.journal.session_id}:{work.source_start.epoch}:{work.source_start.sequence}:"
            f"{claimed.job.input_cursor.sequence}:{candidate.source_digest}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
    )
    source = SourceSnapshot(
        source_id=source_id,
        owner_scope_digest=scope_digest(claimed.job.scope),
        kind=SourceKind.MESSAGE,
        source_digest=source_content_digest,
        locator=(
            f"journal:{work.journal.session_id}:"
            f"{work.source_start.epoch}:{work.source_start.sequence}-"
            f"{claimed.job.input_cursor.sequence}"
        ),
        captured_content=source_content,
        capture_method="history_journal_range",
        observed_at_ms=_now_ms(),
    )
    tiers = [tier.to_wire() for tier in candidate.tiers]
    draft = RecordDraft(
        id=summary_id,
        scope=claimed.job.scope,
        kind=RecordKind.SUMMARY,
        category="context_summary",
        content=candidate.tiers[0].content,
        importance=candidate.importance,
        confidence=1.0,
        metadata={
            "session_id": str(work.journal.session_id),
            "source_digest": candidate.source_digest,
            "source_start": work.source_start.to_wire(),
            "source_end": claimed.job.input_cursor.to_wire(),
            "tiers": cast(list[JsonValue], tiers),
            "usage": candidate.usage.to_wire(),
        },
        provenance=(
            ProvenanceSpan(
                source_id=source_id,
                span_start=0,
                span_end=len(source_content_bytes),
                quoted_digest=source_content_digest,
            ),
        ),
        summary={
            "input_set_digest": candidate.source_digest,
            "level": "exhaustive",
            "decay_half_life_ms": None,
        },
    )
    request = MutationRequest(
        operation=MemoryOperation.CREATE,
        actor_scope=claimed.job.actor_scope,
        target_scope=claimed.job.scope,
        revision=RevisionPrecondition.must_not_exist(),
        trace=claimed.job.trace,
        record_id=summary_id,
        category="context_summary",
    )
    return SummaryMaintenancePublication(
        request=request,
        capability_id=work.summary_capability_id,
        draft=draft,
        sources=(source,),
    )


def _summary_source_content(
    journal: HistoryJournal, source_range: JournalRange, range_digest: str
) -> str:
    content = json.dumps(
        {
            "items": [
                item.to_mapping() for item in journal.items_in_range(source_range)
            ],
            "range": {
                "end": source_range.end.to_wire(),
                "start": source_range.start.to_wire(),
            },
            "range_digest": range_digest,
            "schema": "hypermid.history-summary-source.v1",
            "session_id": str(journal.session_id),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(content.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("summary source content exceeds the provenance capture limit")
    return content


def _reservation_id(claimed: ClaimedMaintenanceJob) -> str:
    material = f"{claimed.job.job_id}\0{claimed.lease.fencing_token}".encode("utf-8")
    return "summary-" + hashlib.sha256(material).hexdigest()[:32]


def summary_record_id(claimed: ClaimedMaintenanceJob) -> Id:
    fence_digest = hashlib.sha256(
        claimed.lease.fencing_token.encode("utf-8")
    ).hexdigest()
    material = f"{claimed.job.job_id}:{fence_digest}".encode("utf-8")
    return Id(f"summary-{hashlib.sha256(material).hexdigest()[:32]}")


def _definite_no_call(error: BaseException) -> bool:
    from gideon.hypermid.budgets import BudgetDenied
    from gideon.security.guardrails.failure import (
        BudgetExceededError,
        CircuitOpenError,
        PromptInjectionBlocked,
        SecretLeakBlocked,
    )

    return isinstance(
        error,
        (
            BudgetDenied,
            BudgetExceededError,
            CircuitOpenError,
            PromptInjectionBlocked,
            SecretLeakBlocked,
        ),
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "BackgroundCoordinator",
    "BackgroundRunResult",
    "SummaryAdmission",
    "SummaryQuiesceReceipt",
    "SummaryWork",
    "quiesce_summary_work",
    "resume_summary_work",
    "summary_record_id",
]
