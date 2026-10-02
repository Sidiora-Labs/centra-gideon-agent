"""Real Wave 4 background maintenance integration journey."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from gideon.cognition.memory_vault import RenderedNote
from gideon.cognition.vault_pages import hypermid_source
from gideon.hypermid.background_coordinator import (
    BackgroundCoordinator,
    SummaryAdmission,
    SummaryWork,
    quiesce_summary_work,
    resume_summary_work,
    summary_record_id,
)
from gideon.hypermid.budgets import (
    BudgetLedger,
    BudgetLimits,
    OwnerBudgetPolicy,
    ProviderPolicy,
)
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    MaintenanceClaimProof,
    MaintenanceJobSpec,
    MaintenanceKind,
    KnowledgePublication,
    KnowledgePublicationAuthority,
    KnowledgeRecall,
    KnowledgeSharingJudgment,
    KnowledgeVerification,
    MemoryOperation,
    MutationRequest,
    PredicateClause,
    PredicateComparison,
    PredicateField,
    PredicateOperator,
    ProvenanceSpan,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
    SharingClassification,
    SmartNoteEvaluation,
    SmartPredicate,
    TrustDecision,
    VerificationState,
)
from gideon.hypermid.foundation import Cursor, Digest, Id, Scope, Trace
from gideon.hypermid.history import (
    ContextPart,
    HistoryJournal,
    JournalRange,
    PartKind,
    PendingContextItem,
    Role,
)
from gideon.hypermid.integrations import SmartNoteConditionSource
from gideon.hypermid.maintenance import (
    KnowledgeMaintenanceCompletion,
    KnowledgeMaintenancePublication,
    MaintenanceScheduler,
)
from gideon.hypermid.memory_client import MemoryClient
from gideon.hypermid.model_budget import ModelBudget
from gideon.hypermid.sources import capture_git_source
from gideon.hypermid.summarizer import model_provider_authority
from gideon.hypermid.usage import UsageAccountingConsumer
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.operations.usage_ledger import UsageJournal
from gideon.security.guardrails.budgets import (
    reset_current_run_key,
    set_current_run_key,
)


_SCOPE = Scope(Id("wave04-owner"), Id("wave04-project"), Id("wave04-workspace"))
_JOB_CAPABILITY = Id("wave04-job-capability")
_SUMMARY_CAPABILITY = Id("wave04-summary-capability")
_JOB_ID = Id("wave04-summary-job")
_FENCE_JOB_ID = Id("wave04-fence-job")
_DENIED_JOB_ID = Id("wave04-budget-denied-job")
_KNOWLEDGE_JOB_ID = Id("wave04-knowledge-job")
_STALE_CONFIG_JOB_ID = Id("wave04-stale-config-job")
_STALE_SOURCE_JOB_ID = Id("wave04-stale-source-job")
_CANCELLED_JOB_ID = Id("wave04-cancelled-job")
_FOREIGN_JOB_ID = Id("wave04-foreign-job")
_SMART_NOTE_ID = Id("wave04-smart-note")
_CODE_RECORD_ID = Id("wave04-code-record")
_GIT_RECALL_ID = Id("wave04-git-recall")
_STALE_CONFIG_RECALL_ID = Id("wave04-stale-config-recall")
_STALE_SOURCE_RECALL_ID = Id("wave04-stale-source-recall")
_MEMORY_RECORDS = Id("memory-records")
_MAX_OUTPUT_TOKENS = 512


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured and Path(configured).is_file():
        return Path(configured).resolve()
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    candidate = target / "debug" / "hypermid-daemon"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    raise RuntimeError("Wave 4 requires the current hypermid-daemon binary")


def _start_daemon(root: Path) -> tuple[subprocess.Popen[str], Path, Path]:
    state_root = root / "daemon"
    state_root.mkdir(mode=0o700)
    socket = state_root / "hypermid.sock"
    record = state_root / "connection.json"
    expires = int(time.time() * 1000) + 600_000
    process = subprocess.Popen(
        [
            str(_daemon_binary()),
            "--socket", str(socket),
            "--connection-record", str(record),
            "--local-credential-id", "wave04-credential",
            "--local-owner-id", str(_SCOPE.owner_id),
            "--local-project-id", str(_SCOPE.project_id),
            "--local-workspace-id", str(_SCOPE.workspace_id),
            "--local-capability-id", str(_JOB_CAPABILITY),
            "--local-capability-operation", "revise",
            "--local-capability-operation", "append",
            "--local-capability-operation", "read",
            "--local-capability-resource", "memory-maintenance",
            "--local-capability-resource", str(_MEMORY_RECORDS),
            "--local-capability-resource", str(_FENCE_JOB_ID),
            "--local-capability-resource", str(_DENIED_JOB_ID),
            "--local-capability-resource", str(_JOB_ID),
            "--local-capability-resource", str(_KNOWLEDGE_JOB_ID),
            "--local-capability-resource", str(_STALE_CONFIG_JOB_ID),
            "--local-capability-resource", str(_STALE_SOURCE_JOB_ID),
            "--local-capability-resource", str(_CANCELLED_JOB_ID),
            "--local-capability-resource", str(_FOREIGN_JOB_ID),
            "--local-capability-expires-ms", str(expires),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10.0
    while not record.is_file():
        if process.poll() is not None:
            detail = process.stderr.read()[:2048] if process.stderr is not None else ""
            raise RuntimeError(
                f"hypermid-daemon exited before Wave 4 readiness ({process.returncode}): {detail}"
            )
        if time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise TimeoutError("hypermid-daemon Wave 4 readiness timed out")
        time.sleep(0.01)
    database = state_root / "state" / "memory.sqlite3"
    if not database.is_file():
        raise RuntimeError("hypermid-daemon did not create its memory store")
    return process, record, database


def _stop_daemon(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _install_summary_capability(database: Path, summary_id: Id) -> None:
    expires = int(time.time() * 1000) + 300_000
    connection = sqlite3.connect(database, timeout=5)
    try:
        source = connection.execute(
            """SELECT issuer_owner_id, principal_id,
                      claimed_owner_id, claimed_project_id, claimed_workspace_id,
                      target_owner_id, target_project_id, target_workspace_id
               FROM hypermid_capabilities WHERE capability_id=?""",
            (str(_JOB_CAPABILITY),),
        ).fetchone()
        if source is None:
            raise RuntimeError("daemon enrollment capability is unavailable")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO hypermid_capabilities(
                   capability_id, issuer_owner_id, principal_id,
                   claimed_owner_id, claimed_project_id, claimed_workspace_id,
                   target_owner_id, target_project_id, target_workspace_id,
                   expires_at_ms, revision, revoked
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)""",
            (str(_SUMMARY_CAPABILITY), *source, expires),
        )
        connection.executemany(
            "INSERT INTO hypermid_capability_operations(capability_id,operation) VALUES (?,?)",
            ((str(_SUMMARY_CAPABILITY), "append"), (str(_SUMMARY_CAPABILITY), "read")),
        )
        connection.execute(
            "INSERT INTO hypermid_capability_resources(capability_id,resource_id) VALUES (?,?)",
            (str(_SUMMARY_CAPABILITY), str(summary_id)),
        )
        connection.commit()
    finally:
        connection.close()


def _trace(name: str) -> Trace:
    return Trace(Id(f"wave04-trace-{name}"), Id(f"wave04-request-{name}"))


def _maintenance_request(
    operation: MemoryOperation,
    job_id: Id | None,
    trace: Trace,
    *,
    actor_scope: Scope = _SCOPE,
    target_scope: Scope = _SCOPE,
) -> MutationRequest:
    return MutationRequest(
        operation=operation,
        actor_scope=actor_scope,
        target_scope=target_scope,
        revision=RevisionPrecondition.must_not_exist(),
        trace=trace,
        record_id=job_id,
        category=None,
    )


def _mutation(job_id: Id | None, trace: Trace) -> MutationRequest:
    return _maintenance_request(MemoryOperation.SUMMARIZE, job_id, trace)


def _record_request(
    operation: MemoryOperation,
    record_id: Id,
    category: str,
    trace: Trace,
    *,
    revision: RevisionPrecondition | None = None,
) -> MutationRequest:
    return MutationRequest(
        operation=operation,
        actor_scope=_SCOPE,
        target_scope=_SCOPE,
        revision=revision or RevisionPrecondition.must_not_exist(),
        trace=trace,
        record_id=record_id,
        category=category,
    )


def _job_spec(
    job_id: Id,
    trace: Trace,
    *,
    cursor: Cursor,
    input_digest: Digest,
    config_digest: Digest,
    created_at_ms: int,
) -> MaintenanceJobSpec:
    return MaintenanceJobSpec(
        id=job_id,
        kind=MaintenanceKind.REFRESH_SUMMARIES,
        target_scope=_SCOPE,
        actor_scope=_SCOPE,
        required_operation=MemoryOperation.SUMMARIZE,
        input_cursor=cursor,
        input_digest=input_digest,
        config_digest=config_digest,
        budget=ModelBudget(2, 512, _MAX_OUTPUT_TOKENS, 1, 50_000_000, 0, 90_000),
        available_at_ms=0,
        created_at_ms=created_at_ms,
    )


def _knowledge_job_spec(
    job_id: Id,
    trace: Trace,
    *,
    cursor: Cursor,
    input_digest: Digest,
    config_digest: Digest,
    created_at_ms: int,
) -> MaintenanceJobSpec:
    return MaintenanceJobSpec(
        id=job_id,
        kind=MaintenanceKind.INDEX_GIT_COMMITS,
        target_scope=_SCOPE,
        actor_scope=_SCOPE,
        required_operation=MemoryOperation.INDEX,
        input_cursor=cursor,
        input_digest=input_digest,
        config_digest=config_digest,
        budget=ModelBudget(1, 512, 1, 0, 0, 0, 30_000),
        available_at_ms=0,
        created_at_ms=created_at_ms,
    )


def _authority(
    operation: MemoryOperation,
    record_id: Id,
    category: str,
    revision: RevisionPrecondition,
    trace: Trace,
) -> KnowledgePublicationAuthority:
    return KnowledgePublicationAuthority(
        _JOB_CAPABILITY,
        _record_request(
            operation,
            record_id,
            category,
            trace,
            revision=revision,
        ),
        _MEMORY_RECORDS,
    )


def _seed_embedding(database: Path, record_id: Id, revision_digest: Digest) -> None:
    connection = sqlite3.connect(database, timeout=5)
    try:
        connection.execute("BEGIN IMMEDIATE")
        owner_scope_digest = connection.execute(
            "SELECT owner_scope_digest FROM memory_records WHERE record_id=?",
            (str(record_id),),
        ).fetchone()
        if owner_scope_digest is None:
            raise AssertionError("embedding target record is unavailable")
        connection.execute(
            """INSERT INTO embedding_registrations(
                   registration_id, owner_scope_digest, mode, provider_identity,
                   model_id, dimensions, metric, normalized, fingerprint,
                   state, created_at_ms
               ) VALUES (?, ?, 'local', 'wave04-local', 'wave04-embed', 1,
                         'cosine', 1, ?, 'active', ?)""",
            (
                "wave04-registration",
                owner_scope_digest[0],
                str(Digest.sha256(b"wave04-embedding-profile")),
                int(time.time() * 1000),
            ),
        )
        connection.execute(
            """INSERT INTO memory_embeddings(
                   record_id, revision_digest, registration_id,
                   vector_f32, dimensions, norm, created_at_ms
               ) VALUES (?, ?, 'wave04-registration', ?, 1, 1.0, ?)""",
            (
                str(record_id),
                str(revision_digest),
                struct.pack("<f", 1.0),
                int(time.time() * 1000),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _derivative_counts(database: Path, record_id: Id) -> tuple[int, int, int]:
    connection = sqlite3.connect(database, timeout=5)
    try:
        embeddings = connection.execute(
            "SELECT count(*) FROM memory_embeddings WHERE record_id=?",
            (str(record_id),),
        ).fetchone()[0]
        live_judgments = connection.execute(
            """SELECT count(*) FROM memory_sharing_judgments
               WHERE record_id=? AND invalidated_at_ms IS NULL""",
            (str(record_id),),
        ).fetchone()[0]
        invalidated_judgments = connection.execute(
            """SELECT count(*) FROM memory_sharing_judgments
               WHERE record_id=? AND invalidated_at_ms IS NOT NULL
                 AND invalidation_reason='content_edited'""",
            (str(record_id),),
        ).fetchone()[0]
        return int(embeddings), int(live_judgments), int(invalidated_judgments)
    finally:
        connection.close()


def _journal(root: Path) -> HistoryJournal:
    journal = HistoryJournal(
        root / "history.sqlite3", scope=_SCOPE, session_id=Id("wave04-session")
    )
    rows = (
        (Role.USER, "The deployment target is named cobalt and requires a signed release."),
        (Role.ASSISTANT, "Recorded: cobalt releases require signatures before deployment."),
    )
    for number, (role, text) in enumerate(rows, 1):
        raw = text.encode("utf-8")
        part = ContextPart(
            part_id=Id(f"wave04-part-{number}"),
            kind=PartKind.TEXT,
            content_digest=Digest.sha256(raw),
            text=text,
        )
        journal.append(
            expected_cursor=journal.cursor,
            idempotency_key=Id(f"wave04-append-{number}"),
            item=PendingContextItem(
                item_id=Id(f"wave04-item-{number}"),
                source_event_id=Id(f"wave04-event-{number}"),
                source_digest=Digest.sha256(raw),
                scope=_SCOPE,
                session_id=journal.session_id,
                role=role,
                parts=(part,),
                relations=(),
                created_at=f"2026-10-02T12:00:0{number}Z",
                recoverable=True,
            ),
            source_snapshot=raw,
        )
    return journal


async def _exercise_knowledge_cycle(
    root: Path,
    record: Path,
    database: Path,
    memory: MemoryClient,
    *,
    config_digest: Digest,
    now_ms: int,
) -> dict[str, bool]:
    smart_trace = _trace("smart-create")
    smart_predicate = SmartPredicate(
        PredicateOperator.ALL,
        (
            PredicateClause(
                PredicateField.RECORD_KIND,
                PredicateComparison.EQ,
                "smart_note",
            ),
            PredicateClause(
                PredicateField.RECORD_STATUS,
                PredicateComparison.EQ,
                "active",
            ),
        ),
    )
    smart_created = await memory.create(
        _record_request(
            MemoryOperation.CREATE,
            _SMART_NOTE_ID,
            "release_condition",
            smart_trace,
        ),
        RecordDraft(
            _SMART_NOTE_ID,
            _SCOPE,
            RecordKind.SMART_NOTE,
            "release_condition",
            "Cobalt release conditions are satisfied when the record remains active.",
            0.9,
            1.0,
            smart_predicate=smart_predicate,
        ),
        now_ms=now_ms,
        authority_resource=_MEMORY_RECORDS,
    )
    assert smart_created.record is not None

    code_trace = _trace("code-create")
    code_created = await memory.create(
        _record_request(
            MemoryOperation.CREATE,
            _CODE_RECORD_ID,
            "code_verification",
            code_trace,
        ),
        RecordDraft(
            _CODE_RECORD_ID,
            _SCOPE,
            RecordKind.FACT,
            "code_verification",
            "Cobalt release verification is derived from the guarded repository head.",
            0.95,
            0.9,
        ),
        now_ms=now_ms + 1,
        authority_resource=_MEMORY_RECORDS,
    )
    assert code_created.record is not None

    candidate_page = await memory.smart_note_candidates(
        AccessRequest(
            operation=GrantOperation.READ,
            actor_scope=_SCOPE,
            target_scope=_SCOPE,
            resource_id=_MEMORY_RECORDS,
            trace=_trace("smart-candidates"),
        )
    )
    candidate = next(
        item for item in candidate_page.candidates if item.record_id == _SMART_NOTE_ID
    )

    repository = Path(__file__).resolve().parents[3]
    git_capture = capture_git_source(
        repository, scope=_SCOPE, observed_at_ms=now_ms + 2, max_commits=8
    )
    assert git_capture.revalidate()
    repository_identity = Digest(
        str(git_capture.source.locator).removeprefix("git:")
    )
    policy_digest = Digest.sha256(b"wave04-sharing-policy-v1")
    knowledge_trace = _trace("knowledge")
    knowledge_cursor = Cursor(1, 1)
    await memory.index_enqueue(
        _maintenance_request(
            MemoryOperation.INDEX, _KNOWLEDGE_JOB_ID, knowledge_trace
        ),
        _knowledge_job_spec(
            _KNOWLEDGE_JOB_ID,
            knowledge_trace,
            cursor=knowledge_cursor,
            input_digest=git_capture.source.source_digest,
            config_digest=config_digest,
            created_at_ms=now_ms + 3,
        ),
    )
    scheduler = MaintenanceScheduler(memory, worker_id="wave04-knowledge-worker")

    async def execute_knowledge(claimed):
        recall_draft = RecordDraft(
            _GIT_RECALL_ID,
            _SCOPE,
            RecordKind.FACT,
            "git_recall",
            "The guarded repository head verifies the current Cobalt release context.",
            0.9,
            0.95,
            provenance=(
                ProvenanceSpan(
                    git_capture.source.source_id,
                    quoted_digest=git_capture.source.source_digest,
                ),
            ),
        )
        publication = KnowledgePublication(
            claimed.job.job_id,
            git_capture.source.source_digest,
            config_digest,
            git_capture.source,
            claimed.job.input_cursor,
            smart_notes=(SmartNoteEvaluation.from_candidate(candidate),),
            verifications=(
                KnowledgeVerification(
                    _CODE_RECORD_ID,
                    code_created.record.current.digest,
                    VerificationState.SUPPORTED,
                    0.95,
                ),
            ),
            sharing_judgments=(
                KnowledgeSharingJudgment(
                    _CODE_RECORD_ID,
                    code_created.record.current.digest,
                    SharingClassification.SHARED,
                    TrustDecision.ALLOW,
                    "wave04-sharing-policy",
                    1,
                    policy_digest,
                    None,
                    None,
                    git_capture.source.source_digest,
                ),
            ),
            recall=KnowledgeRecall(recall_draft),
            repository_identity_digest=repository_identity,
            refs_digest=git_capture.refs_digest,
            next_evaluation_at_ms=now_ms + 60_000,
            now_ms=now_ms + 3,
        )
        exact_smart = RevisionPrecondition.match(candidate.revision_digest)
        exact_code = RevisionPrecondition.match(code_created.record.current.digest)
        output = KnowledgeMaintenancePublication(
            publication,
            smart_notes=(
                _authority(
                    MemoryOperation.INDEX,
                    _SMART_NOTE_ID,
                    "release_condition",
                    exact_smart,
                    claimed.job.trace,
                ),
            ),
            verifications=(
                _authority(
                    MemoryOperation.VERIFY,
                    _CODE_RECORD_ID,
                    "code_verification",
                    exact_code,
                    claimed.job.trace,
                ),
            ),
            sharing_judgments=(
                _authority(
                    MemoryOperation.VERIFY,
                    _CODE_RECORD_ID,
                    "code_verification",
                    exact_code,
                    claimed.job.trace,
                ),
            ),
            recall=_authority(
                MemoryOperation.CREATE,
                _GIT_RECALL_ID,
                "git_recall",
                RevisionPrecondition.must_not_exist(),
                claimed.job.trace,
            ),
        )
        return KnowledgeMaintenanceCompletion(
            str(git_capture.source.source_digest),
            str(config_digest),
            output,
            pre_publish_check=lambda: (
                None
                if git_capture.revalidate()
                else (_ for _ in ()).throw(ValueError("guarded Git source changed"))
            ),
        )

    knowledge = await scheduler.run_knowledge_once(
        target_scope=_SCOPE,
        trace=_trace("knowledge-claim"),
        lease_ttl_ms=120_000,
        execute=execute_knowledge,
    )
    assert knowledge is not None and knowledge.state == "succeeded"
    assert knowledge.source_digest == git_capture.source.source_digest
    assert len(knowledge.smart_notes) == 1 and knowledge.smart_notes[0].result
    assert (
        len(knowledge.verifications) == 1
        and knowledge.verifications[0].state is VerificationState.SUPPORTED
    )
    assert len(knowledge.sharing_judgments) == 1
    assert knowledge.recall is not None and knowledge.recall.record_id == _GIT_RECALL_ID

    conditions = await SmartNoteConditionSource(
        memory, scope=_SCOPE
    ).snapshot(_SCOPE, "wave04-session")
    condition = next(item for item in conditions if item.condition_id == str(_SMART_NOTE_ID))
    assert condition.result and condition.transition_id is not None
    assert any(item.label == "Evaluation" and item.value == "Matched" for item in condition.observed_facts)

    shared = await memory.shared_record(
        AccessRequest(
            operation=GrantOperation.READ,
            actor_scope=_SCOPE,
            target_scope=_SCOPE,
            resource_id=_CODE_RECORD_ID,
            category="code_verification",
            trace=_trace("shared-before-edit"),
        ),
        policy_digest,
        authority_resource=_MEMORY_RECORDS,
    )
    assert shared.record is not None and shared.record.id == _CODE_RECORD_ID

    _seed_embedding(database, _CODE_RECORD_ID, code_created.record.current.digest)
    assert _derivative_counts(database, _CODE_RECORD_ID) == (1, 1, 0)
    edited = RecordDraft(
        _CODE_RECORD_ID,
        _SCOPE,
        RecordKind.FACT,
        "code_verification",
        "Cobalt release verification must be recomputed after this source edit.",
        0.95,
        0.9,
    )
    edited_receipt = await memory.update(
        _record_request(
            MemoryOperation.UPDATE,
            _CODE_RECORD_ID,
            "code_verification",
            _trace("code-edit"),
            revision=RevisionPrecondition.match(code_created.record.current.digest),
        ),
        edited,
        now_ms=now_ms + 4,
        authority_resource=_MEMORY_RECORDS,
    )
    assert _CODE_RECORD_ID in edited_receipt.invalidated_ids
    assert _derivative_counts(database, _CODE_RECORD_ID) == (0, 0, 1)
    try:
        await memory.shared_record(
            AccessRequest(
                operation=GrantOperation.READ,
                actor_scope=_SCOPE,
                target_scope=_SCOPE,
                resource_id=_CODE_RECORD_ID,
                category="code_verification",
                trace=_trace("shared-after-edit"),
            ),
            policy_digest,
            authority_resource=_MEMORY_RECORDS,
        )
    except HypermidRemoteError as error:
        assert error.error.code == "AUTHORIZATION_DENIED"
    else:
        raise AssertionError("edited record retained its prior sharing judgment")

    await _exercise_knowledge_refusals(
        memory,
        scheduler,
        git_capture,
        config_digest=config_digest,
        now_ms=now_ms + 10,
    )
    return {
        "knowledge_cycle_completed": True,
        "note_conditions_inspected": True,
        "code_verification_inspected": True,
        "git_recall_consumed": True,
        "edited_memory_invalidated": True,
        "cancelled_write_refused": True,
        "stale_source_refused": True,
        "stale_config_refused": True,
        "foreign_owner_refused": True,
    }


async def _exercise_knowledge_refusals(
    memory: MemoryClient,
    scheduler: MaintenanceScheduler,
    git_capture,
    *,
    config_digest: Digest,
    now_ms: int,
) -> None:
    async def assert_missing(record_id: Id, category: str) -> None:
        value, _ = await memory.get(
            AccessRequest(
                operation=GrantOperation.READ,
                actor_scope=_SCOPE,
                target_scope=_SCOPE,
                resource_id=record_id,
                category=category,
                trace=_trace(f"missing-{record_id}"),
            ),
            authority_resource=_MEMORY_RECORDS,
        )
        assert value is None

    async def refused_publication(
        *,
        job_id: Id,
        recall_id: Id,
        job_input: Digest,
        job_config: Digest,
        publication_source,
        publication_config: Digest,
        label: str,
    ) -> None:
        trace = _trace(label)
        await memory.index_enqueue(
            _maintenance_request(MemoryOperation.INDEX, job_id, trace),
            _knowledge_job_spec(
                job_id,
                trace,
                cursor=Cursor(1, 1),
                input_digest=job_input,
                config_digest=job_config,
                created_at_ms=now_ms,
            ),
        )

        async def execute(claimed):
            recall = RecordDraft(
                recall_id,
                _SCOPE,
                RecordKind.FACT,
                "git_recall",
                f"This {label} publication must never become visible.",
                0.5,
                0.5,
                provenance=(
                    ProvenanceSpan(
                        publication_source.source_id,
                        quoted_digest=publication_source.source_digest,
                    ),
                ),
            )
            publication = KnowledgePublication(
                job_id,
                publication_source.source_digest,
                publication_config,
                publication_source,
                claimed.job.input_cursor,
                recall=KnowledgeRecall(recall),
                repository_identity_digest=Digest(
                    str(publication_source.locator).removeprefix("git:")
                ),
                refs_digest=git_capture.refs_digest,
                now_ms=now_ms,
            )
            return KnowledgeMaintenanceCompletion(
                str(publication_source.source_digest),
                str(publication_config),
                KnowledgeMaintenancePublication(
                    publication,
                    recall=_authority(
                        MemoryOperation.CREATE,
                        recall_id,
                        "git_recall",
                        RevisionPrecondition.must_not_exist(),
                        claimed.job.trace,
                    ),
                ),
            )

        try:
            await scheduler.run_knowledge_once(
                target_scope=_SCOPE,
                trace=_trace(f"{label}-claim"),
                lease_ttl_ms=120_000,
                execute=execute,
            )
        except HypermidRemoteError as error:
            assert error.error.code in (
                "STALE_MAINTENANCE_JOB",
                "AUTHORIZATION_DENIED",
            )
        else:
            raise AssertionError(f"{label} publication committed")
        status = await memory.maintenance_status(
            _maintenance_request(MemoryOperation.INDEX, job_id, trace),
            job_id=job_id,
        )
        assert status.result["state"] == "failed"
        assert status.result["last_error_code"] == "PUBLICATION_FAILED"
        await assert_missing(recall_id, "git_recall")

    changed_config = Digest.sha256(b"wave04-changed-config")
    await refused_publication(
        job_id=_STALE_CONFIG_JOB_ID,
        recall_id=_STALE_CONFIG_RECALL_ID,
        job_input=git_capture.source.source_digest,
        job_config=config_digest,
        publication_source=git_capture.source,
        publication_config=changed_config,
        label="stale-config",
    )

    changed_content = git_capture.source.captured_content + "\n"
    changed_source = replace(
        git_capture.source,
        captured_content=changed_content,
        source_digest=Digest.sha256(changed_content.encode("utf-8")),
    )
    await refused_publication(
        job_id=_STALE_SOURCE_JOB_ID,
        recall_id=_STALE_SOURCE_RECALL_ID,
        job_input=git_capture.source.source_digest,
        job_config=config_digest,
        publication_source=changed_source,
        publication_config=config_digest,
        label="stale-source",
    )

    cancel_trace = _trace("cancelled")
    await memory.index_enqueue(
        _maintenance_request(MemoryOperation.INDEX, _CANCELLED_JOB_ID, cancel_trace),
        _knowledge_job_spec(
            _CANCELLED_JOB_ID,
            cancel_trace,
            cursor=Cursor(1, 1),
            input_digest=git_capture.source.source_digest,
            config_digest=config_digest,
            created_at_ms=now_ms + 1,
        ),
    )
    entered = asyncio.Event()
    blocked = asyncio.Event()

    async def wait_forever(_claimed):
        entered.set()
        await blocked.wait()
        raise AssertionError("cancelled knowledge work resumed")

    task = asyncio.create_task(
        scheduler.run_knowledge_once(
            target_scope=_SCOPE,
            trace=_trace("cancelled-claim"),
            lease_ttl_ms=120_000,
            execute=wait_forever,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancelled knowledge work returned normally")
    cancelled = await memory.maintenance_status(
        _maintenance_request(
            MemoryOperation.INDEX, _CANCELLED_JOB_ID, cancel_trace
        ),
        job_id=_CANCELLED_JOB_ID,
    )
    assert cancelled.result["state"] == "abandoned"
    assert cancelled.result["last_error_code"] == "CANCELLED"

    foreign_scope = Scope(
        Id("wave04-foreign-owner"),
        Id("wave04-foreign-project"),
        _SCOPE.workspace_id,
    )
    foreign_trace = _trace("foreign-owner")
    foreign_spec = replace(
        _knowledge_job_spec(
            _FOREIGN_JOB_ID,
            foreign_trace,
            cursor=Cursor(1, 1),
            input_digest=git_capture.source.source_digest,
            config_digest=config_digest,
            created_at_ms=now_ms + 2,
        ),
        actor_scope=foreign_scope,
    )
    try:
        foreign_request = _maintenance_request(
            MemoryOperation.INDEX,
            _FOREIGN_JOB_ID,
            foreign_trace,
            actor_scope=foreign_scope,
        )
        await memory.client.request(
            "memory.index.enqueue",
            {
                "capability_id": str(_JOB_CAPABILITY),
                "request": foreign_request.to_wire(),
                "spec": foreign_spec.to_wire(),
            },
            trace=foreign_trace,
            effect_kind="durable",
            scope=_SCOPE,
        )
    except HypermidRemoteError as error:
        assert error.error.code == "AUTHORIZATION_DENIED"
    else:
        raise AssertionError("foreign owner enqueued maintenance work")


async def background_maintenance_journey(root: Path) -> dict[str, object]:
    required = ("GATEWAY_ROUTER", "GATEWAY_ROUTER_API_KEY", "HYPERMID_TEST_MODEL")
    if not all(os.environ.get(name) for name in required):
        raise RuntimeError("Wave 4 requires the configured Centra model authority")
    os.environ["GIDEON_HOME"] = str(root / "gideon-home")
    provider_model = os.environ["HYPERMID_TEST_MODEL"]
    process, record, database = _start_daemon(root)
    client = HypermidClient(record, scope=_SCOPE)
    memory = MemoryClient(client, capability_id=_JOB_CAPABILITY)
    provider = OpenAIProvider(
        model=provider_model,
        credential=Credential(
            "wave04-centra", "api_key", os.environ["GATEWAY_ROUTER_API_KEY"], "env"
        ),
        base_url=os.environ["GATEWAY_ROUTER"],
        max_tokens=_MAX_OUTPUT_TOKENS,
        extra_options={"temperature": 0},
    )
    provider.served_model_ref = f"centra:{provider_model}"
    journal = _journal(root)
    source_range = JournalRange(Cursor(1, 1), journal.cursor)
    input_digest = journal.source_digest(source_range)
    config_digest = Digest.sha256(b"wave04-background-config")
    usage_path = root / "gideon-home" / "usage" / "turns.jsonl"
    budgets = BudgetLedger.open(
        root / "budgets.sqlite3",
        (
            OwnerBudgetPolicy(
                str(_SCOPE.owner_id),
                str(_SCOPE.project_id),
                1,
                BudgetLimits(1_000_000_000, 2, 1_000_000_000, 1_000_000_000, 1_000_000_000),
                (ProviderPolicy("centra", provider_model, "gateway"),),
            ),
        ),
    )
    coordinator = BackgroundCoordinator(
        MaintenanceScheduler(memory, worker_id="wave04-worker"),
        UsageAccountingConsumer(budgets, UsageJournal(usage_path)),
    )
    now_ms = int(time.time() * 1000)
    summary_id: Id | None = None
    try:
        await client.connect()
        knowledge_outcomes = await _exercise_knowledge_cycle(
            root,
            record,
            database,
            memory,
            config_digest=config_digest,
            now_ms=now_ms,
        )

        fence_trace = _trace("fence")
        await memory.summary_enqueue(
            _mutation(_FENCE_JOB_ID, fence_trace),
            _job_spec(
                _FENCE_JOB_ID,
                fence_trace,
                cursor=journal.cursor,
                input_digest=input_digest,
                config_digest=config_digest,
                created_at_ms=now_ms,
            ),
        )
        fence = await memory.maintenance_claim(
            _mutation(None, _trace("fence-claim")),
            worker_id=Id("wave04-fence-worker"),
            ttl_ms=120_000,
        )
        assert fence is not None and fence.job_id == _FENCE_JOB_ID
        wrong_token = Digest("0" * 64 if str(fence.fencing_token) != "0" * 64 else "1" * 64)
        try:
            await memory.maintenance_heartbeat(
                _mutation(_FENCE_JOB_ID, fence.trace),
                proof=MaintenanceClaimProof(fence.job_id, fence.holder_id, wrong_token),
                ttl_ms=120_000,
            )
        except HypermidRemoteError as error:
            assert error.error.code == "CLAIM_NOT_FOUND"
        else:
            raise AssertionError("daemon accepted a wrong maintenance fencing token")
        await memory.maintenance_terminate(
            _mutation(_FENCE_JOB_ID, fence.trace),
            proof=fence.proof,
            state="abandoned",
            error_code="FENCE_PROBE_COMPLETE",
        )

        job_trace = _trace("summary")
        await memory.summary_enqueue(
            _mutation(_JOB_ID, job_trace),
            _job_spec(
                _JOB_ID,
                job_trace,
                cursor=journal.cursor,
                input_digest=input_digest,
                config_digest=config_digest,
                created_at_ms=now_ms + 1,
            ),
        )
        privacy = await coordinator.run_summary_once(
            target_scope=_SCOPE,
            trace=_trace("privacy"),
            privacy_mode="temporary",
            lease_ttl_ms=120_000,
            work_for_job=lambda _claimed: (_ for _ in ()).throw(
                AssertionError("privacy mode admitted summary work")
            ),
            current_config_digest=lambda: str(config_digest),
        )
        assert not privacy.ran and privacy.outcome == "no_call"

        quiesced = await quiesce_summary_work(timeout=3.0)
        blocked = await coordinator.run_summary_once(
            target_scope=_SCOPE,
            trace=_trace("quiesced"),
            privacy_mode="persistent",
            lease_ttl_ms=120_000,
            work_for_job=lambda _claimed: (_ for _ in ()).throw(
                AssertionError("quiesced summary work executed")
            ),
            current_config_digest=lambda: str(config_digest),
        )
        assert quiesced.drained_tasks == 0 and blocked.outcome == "no_call"
        resume_summary_work()

        def work_for_job(claimed):
            nonlocal summary_id
            summary_id = summary_record_id(claimed)
            _install_summary_capability(database, summary_id)
            return SummaryWork(
                journal=journal,
                source_start=Cursor(1, 1),
                source_token_count=128,
                authority=model_provider_authority(provider),
                admission=SummaryAdmission(
                    "centra", provider_model, "gateway", 128,
                    _MAX_OUTPUT_TOKENS, 50_000_000,
                ),
                summary_capability_id=_SUMMARY_CAPABILITY,
                timeout_seconds=90.0,
            )

        run_token = set_current_run_key("wave04-summary-run")
        try:
            completed = await coordinator.run_summary_once(
                target_scope=_SCOPE,
                trace=_trace("claim"),
                privacy_mode="persistent",
                lease_ttl_ms=120_000,
                work_for_job=work_for_job,
                current_config_digest=lambda: str(config_digest),
            )
        finally:
            reset_current_run_key(run_token)
        assert completed.ran and completed.outcome in ("measured", "partial")
        assert completed.checkpoint_cursor == journal.cursor
        assert summary_id is not None

        status = await memory.maintenance_status(
            _mutation(_JOB_ID, job_trace), job_id=_JOB_ID
        )
        assert status.result["state"] == "succeeded"
        assert status.result["checkpoint_cursor"] is not None

        summary_memory = MemoryClient(client, capability_id=_SUMMARY_CAPABILITY)
        record_value, _ = await summary_memory.get(
            AccessRequest(
                operation=GrantOperation.READ,
                actor_scope=_SCOPE,
                target_scope=_SCOPE,
                resource_id=summary_id,
                category="context_summary",
                trace=_trace("summary-read"),
            )
        )
        assert record_value is not None and record_value.kind is RecordKind.SUMMARY
        metadata = record_value.current.metadata
        tiers = metadata["tiers"]
        assert isinstance(tiers, list) and [tier["level"] for tier in tiers] == [0, 1, 2, 3]
        assert metadata["source_digest"] == str(input_digest)
        assert metadata["source_start"] == Cursor(1, 1).to_wire()
        assert metadata["source_end"] == journal.cursor.to_wire()

        repeated = await coordinator.run_summary_once(
            target_scope=_SCOPE,
            trace=_trace("repeat"),
            privacy_mode="persistent",
            lease_ttl_ms=120_000,
            work_for_job=lambda _claimed: (_ for _ in ()).throw(
                AssertionError("completed source range was reprocessed")
            ),
            current_config_digest=lambda: str(config_digest),
        )
        assert not repeated.ran and repeated.outcome == "no_job"

        denied_trace = _trace("budget-denied")
        await memory.summary_enqueue(
            _mutation(_DENIED_JOB_ID, denied_trace),
            _job_spec(
                _DENIED_JOB_ID,
                denied_trace,
                cursor=journal.cursor,
                input_digest=input_digest,
                config_digest=config_digest,
                created_at_ms=now_ms + 2,
            ),
        )
        denied_budgets = BudgetLedger.open(
            root / "denied-budgets.sqlite3",
            (
                OwnerBudgetPolicy(
                    str(_SCOPE.owner_id),
                    str(_SCOPE.project_id),
                    1,
                    BudgetLimits(0, 1, 0, 0, 0),
                    (ProviderPolicy("centra", provider_model, "gateway"),),
                ),
            ),
        )
        denied_coordinator = BackgroundCoordinator(
            MaintenanceScheduler(memory, worker_id="wave04-denied-worker"),
            UsageAccountingConsumer(denied_budgets, UsageJournal(usage_path)),
        )

        async def forbidden_authority(*_args):
            raise AssertionError("budget-denied work reached model dispatch")

        denied = await denied_coordinator.run_summary_once(
            target_scope=_SCOPE,
            trace=_trace("budget-denied-claim"),
            privacy_mode="persistent",
            lease_ttl_ms=120_000,
            work_for_job=lambda _claimed: SummaryWork(
                journal=journal,
                source_start=Cursor(1, 1),
                source_token_count=128,
                authority=forbidden_authority,
                admission=SummaryAdmission(
                    "centra", provider_model, "gateway", 128,
                    _MAX_OUTPUT_TOKENS, 1,
                ),
                summary_capability_id=_SUMMARY_CAPABILITY,
                timeout_seconds=90.0,
            ),
            current_config_digest=lambda: str(config_digest),
        )
        assert not denied.ran and denied.outcome == "no_call"

        note = RenderedNote(
            "projects/cobalt.md", "# Cobalt\n\nSigned releases only.\n", set(), ["release"], "Cobalt"
        )
        note_source = hypermid_source(note, scope=_SCOPE, observed_at_ms=now_ms)
        assert note_source.captured_content == note.content
        assert note_source.source_digest == Digest.sha256(note.content.encode())
        repository = Path(__file__).resolve().parents[3]
        git_capture = capture_git_source(
            repository, scope=_SCOPE, observed_at_ms=now_ms, max_commits=8
        )
        assert git_capture.revalidate()
        assert str(repository.resolve()) not in json.dumps(git_capture.source.to_wire())

        rows = UsageJournal(usage_path).rows()
        assert len(rows) == 1
        assert rows[0]["source"] == "background"
        assert rows[0]["agent"] == "hypermid-summary"
        assert rows[0]["model"] == provider_model
        assert rows[0]["usage_status"] == "measured"
        assert rows[0]["input_tokens"] > 0
        assert rows[0]["output_tokens"] > 0
        assert rows[0]["cache_read_tokens"] >= 0
        assert rows[0]["cache_creation_tokens"] >= 0
        assert rows[0]["priced"] is False
        assert rows[0]["estimated"] is False
        assert rows[0]["price_source"] == "unknown"
        assert completed.usage.outcome == "partial"
        assert completed.usage.actual is not None
        assert completed.usage.actual.input_tokens == rows[0]["input_tokens"]
        assert completed.usage.actual.output_tokens == rows[0]["output_tokens"]
        assert completed.usage.actual.cost_nanodollars is None
        reservation = budgets.reservation(completed.usage.reservation_id)
        assert reservation is not None and reservation.status == "unknown"
        assert reservation.reserved_cost_nanodollars == 50_000_000
        spend = json.loads((root / "gideon-home" / "spend.json").read_text())
        assert sum(int(value.get("tokens", 0)) for value in spend.values()) > 0

        serialized = json.dumps(
            {
                "result": completed.usage.to_wire(),
                "record": {
                    "id": str(record_value.id),
                    "content": record_value.current.content,
                    "metadata": dict(record_value.current.metadata),
                },
                "note": note_source.to_wire(),
                "git": git_capture.source.to_wire(),
            },
            sort_keys=True,
        )
        for forbidden in (
            os.environ["GATEWAY_ROUTER_API_KEY"],
            os.environ["GATEWAY_ROUTER"],
            "wave04-centra",
            str(fence.fencing_token),
        ):
            assert forbidden.lower() not in serialized.lower()
        return {
            **knowledge_outcomes,
            "atomic_summary": True,
            "wrong_fence_rejected": True,
            "completed_range_not_reprocessed": True,
            "privacy_no_call": True,
            "quiesce_no_call": True,
            "budget_no_call": True,
            "usage_outcome": completed.outcome,
            "usage_rows": len(rows),
            "measured_input_tokens": rows[0]["input_tokens"],
            "measured_output_tokens": rows[0]["output_tokens"],
            "measured_cache_read_tokens": rows[0]["cache_read_tokens"],
            "measured_cache_creation_tokens": rows[0]["cache_creation_tokens"],
            "cost_status": "unpriced",
            "price_source": "unknown",
            "financial_reservation_unresolved": True,
            "provider_usage_reconciled": True,
            "redaction_passed": True,
            "approved_credentials": True,
            "note_attributed": True,
            "git_guarded_and_revalidated": True,
        }
    finally:
        resume_summary_work()
        await provider.shutdown()
        await client.close()
        _stop_daemon(process)


def run(root: Path) -> dict[str, object]:
    return asyncio.run(background_maintenance_journey(root))
