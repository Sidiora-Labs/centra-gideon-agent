from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from gideon.hypermid.client import HypermidClient
from gideon.hypermid.contracts import (
    AccessRequest,
    GrantOperation,
    KnowledgePublication,
    KnowledgePublicationAuthority,
    MaintenanceJobSpec,
    MaintenanceKind,
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
    SmartNoteEvaluation,
    SmartPredicate,
    SourceKind,
    SourceSnapshot,
)
from gideon.hypermid.foundation import Digest, Id, Scope, Trace
from gideon.hypermid.file_index import (
    FilePolicy,
    inspect_file,
    revalidate_for_publication,
)
from gideon.hypermid.integrations import (
    IntegrationScopeError,
    IntegrationStatusFacade,
    SmartNoteConditionSource,
)
from gideon.hypermid.memory_client import MemoryClient
from gideon.hypermid.model_budget import ModelBudget
from gideon.hypermid.sources import scope_digest


def _trace(label: str) -> Trace:
    suffix = uuid4().hex
    return Trace(Id(f"trace-{label}-{suffix}"), Id(f"request-{label}-{suffix}"))


def _daemon_binary() -> Path:
    configured = os.environ.get("HYPERMID_DAEMON_BINARY")
    if configured:
        candidate = Path(configured)
    else:
        candidate = Path(os.environ.get("CARGO_TARGET_DIR", "target")) / "debug" / "hypermid-daemon"
    if not candidate.is_file():
        raise RuntimeError("build hypermid-daemon before the integration journey")
    return candidate.resolve()


async def _start_daemon(root: Path, scope: Scope, capability_id: Id) -> tuple[asyncio.subprocess.Process, Path]:
    record = root / "connection.json"
    resources = (
        "condition-smart-note",
        "condition-job",
        "memory-maintenance",
        "memory-records",
    )
    command = [
        str(_daemon_binary()),
        "--socket",
        str(root / "daemon.sock"),
        "--connection-record",
        str(record),
        "--local-credential-id",
        "condition-credential",
        "--local-owner-id",
        str(scope.owner_id),
        "--local-project-id",
        str(scope.project_id),
        "--local-workspace-id",
        str(scope.workspace_id),
        "--local-capability-id",
        str(capability_id),
        "--local-capability-operation",
        "read",
        "--local-capability-operation",
        "append",
        "--local-capability-operation",
        "revise",
    ]
    for resource in resources:
        command.extend(("--local-capability-resource", resource))
    command.extend(
        (
            "--local-capability-expires-ms",
            str(time.time_ns() // 1_000_000 + 300_000),
        )
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    deadline = asyncio.get_running_loop().time() + 10
    while not record.is_file():
        if process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr is not None else b""
            raise RuntimeError(stderr.decode("utf-8", errors="replace"))
        if asyncio.get_running_loop().time() >= deadline:
            process.terminate()
            await process.wait()
            raise TimeoutError("condition daemon did not publish its connection record")
        await asyncio.sleep(0.02)
    return process, record


async def _stop_daemon(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


@pytest.mark.asyncio
async def test_scoped_canonical_smart_note_condition_reaches_connections(tmp_path: Path) -> None:
    scope = Scope("condition-owner", "condition-project", "condition-workspace")
    capability_id = Id("condition-capability")
    process: asyncio.subprocess.Process | None = None
    process, record = await _start_daemon(tmp_path, scope, capability_id)
    try:
        async with HypermidClient(record, scope=scope) as transport:
            client = MemoryClient(transport, capability_id=capability_id)
            predicate = SmartPredicate(
                PredicateOperator.ALL,
                (
                    PredicateClause(
                        PredicateField.EVENT_KIND,
                        PredicateComparison.EQ,
                        "file",
                    ),
                ),
            )
            definition_bytes = json.dumps(
                {
                    "module_id": "configured-module",
                    "predicate": predicate.to_wire(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            definition_digest = Digest.sha256(definition_bytes.encode("utf-8"))
            definition_source = SourceSnapshot(
                Id("condition-definition-source"),
                scope_digest(scope),
                SourceKind.FILE,
                definition_digest,
                "module-config:configured-module",
                definition_bytes,
                "gideon_module_configuration",
                time.time_ns() // 1_000_000,
            )
            create_trace = _trace("condition-create")
            create = await client.create(
                MutationRequest(
                    MemoryOperation.CREATE,
                    scope,
                    scope,
                    RevisionPrecondition.must_not_exist(),
                    create_trace,
                    Id("condition-smart-note"),
                    "module_condition",
                ),
                RecordDraft(
                    Id("condition-smart-note"),
                    scope,
                    RecordKind.SMART_NOTE,
                    "module_condition",
                    "Configured module condition",
                    1.0,
                    1.0,
                    metadata={"module": "configured-module"},
                    provenance=(
                        ProvenanceSpan(
                            Id("condition-definition-source"),
                            quoted_digest=definition_digest,
                        ),
                    ),
                    smart_predicate=predicate,
                ),
                sources=(definition_source,),
                now_ms=time.time_ns() // 1_000_000,
            )
            source = SmartNoteConditionSource(client, scope=scope)
            facade = IntegrationStatusFacade(scope, condition_source=source)
            pending = await facade.snapshot(scope, "condition-session")
            assert pending.conditions[0].to_wire()["failure_code"] == "condition_not_evaluated"

            candidates = await client.smart_note_candidates(
                AccessRequest(
                    GrantOperation.READ,
                    scope,
                    scope,
                    Id("memory-records"),
                    _trace("condition-candidates"),
                ),
                limit=32,
            )
            candidate = candidates.candidates[0]
            authorized_root = tmp_path / "authorized-project"
            authorized_root.mkdir()
            observed_path = authorized_root / "module-ready.txt"
            observed_path.write_text("configured module file event", encoding="utf-8")
            guarded = revalidate_for_publication(
                inspect_file(observed_path, authorized_root, FilePolicy())
            )
            assert guarded.allowed and guarded.content is not None
            assert guarded.content_digest is not None
            observed_bytes = guarded.content.encode("utf-8")
            observed_digest = Digest(guarded.content_digest)
            config_digest = Digest.sha256(
                json.dumps(predicate.to_wire(), sort_keys=True).encode("utf-8")
            )
            now_ms = time.time_ns() // 1_000_000
            job_trace = _trace("condition-job")
            job_request = MutationRequest(
                MemoryOperation.INDEX,
                scope,
                scope,
                RevisionPrecondition.must_not_exist(),
                job_trace,
                Id("condition-job"),
            )
            await client.maintenance_enqueue(
                job_request,
                MaintenanceJobSpec(
                    Id("condition-job"),
                    MaintenanceKind.EVALUATE_SMART_NOTES,
                    scope,
                    scope,
                    MemoryOperation.INDEX,
                    create.cursor,
                    observed_digest,
                    config_digest,
                    ModelBudget(32, 0, 0, 0, 0, 0, 5_000),
                    now_ms,
                    now_ms,
                ),
            )
            claim_request = MutationRequest(
                MemoryOperation.INDEX,
                scope,
                scope,
                RevisionPrecondition.must_not_exist(),
                job_trace,
            )
            claim = await client.maintenance_claim(
                claim_request, worker_id=Id("condition-worker"), ttl_ms=30_000
            )
            assert claim is not None
            publication = KnowledgePublication(
                job_id=claim.job_id,
                expected_input_digest=claim.input_digest,
                expected_config_digest=claim.config_digest,
                source=SourceSnapshot(
                    Id("condition-observation-source"),
                    scope_digest(scope),
                    SourceKind.FILE,
                    observed_digest,
                    "module-config:configured-module",
                    observed_bytes.decode("utf-8"),
                    "gideon_guarded_local_file",
                    now_ms,
                ),
                evaluated_cursor=candidates.cursor,
                smart_notes=(SmartNoteEvaluation.from_candidate(candidate),),
                now_ms=now_ms,
            )
            publish_request = MutationRequest(
                MemoryOperation.INDEX,
                scope,
                scope,
                RevisionPrecondition.must_not_exist(),
                job_trace,
                claim.job_id,
            )
            authority = KnowledgePublicationAuthority(
                capability_id,
                MutationRequest(
                    MemoryOperation.INDEX,
                    scope,
                    scope,
                    RevisionPrecondition.match(str(candidate.revision_digest)),
                    job_trace,
                    candidate.record_id,
                    candidate.category,
                ),
            )
            receipt = await client.maintenance_publish_knowledge(
                publish_request,
                publication,
                proof=claim.proof,
                smart_notes=(authority,),
            )
            assert receipt.smart_notes[0].result is True

            matched = await facade.snapshot(scope, "condition-session")
            repeated = await facade.snapshot(scope, "condition-session")
            condition = matched.conditions[0].to_wire()
            repeated_condition = repeated.conditions[0].to_wire()
            assert condition["availability"] == "available"
            assert condition["result"] is True
            assert condition["transition_identity"] == "available"
            assert condition["transition_id"] == repeated_condition["transition_id"]
            assert condition["observed_facts"]

            serialized = json.dumps(matched.to_wire(), sort_keys=True)
            assert "Configured module condition" not in serialized
            assert definition_bytes not in serialized
            assert observed_bytes.decode("utf-8") not in serialized
            assert str(tmp_path) not in serialized
            with pytest.raises(IntegrationScopeError):
                await facade.snapshot(
                    Scope("condition-other", "condition-project", "condition-workspace"),
                    "condition-session",
                )
    finally:
        await _stop_daemon(process)
