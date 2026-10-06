"""Accepted owner events bind native records and conservative chat retraction."""

import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_durable_background_identity import accepted_log

from gideon.cognition.context import PromptAssembler
from gideon.cognition.history import HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_record import MemoryKind
from gideon.cognition.memory_record import MemoryRecord as GideonRecord
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.config import (
    DaemonConfig,
    DaemonTransport,
    LocalAuthConfig,
    LocalAuthMethod,
)
from gideon.hypermid.contracts import (
    LineageEdge,
    MemoryOperation,
    MutationRequest,
    OwnerWordCapture,
    ProvenanceSpan,
    RecordDraft,
    RecordKind,
    RevisionPrecondition,
    SourceKind,
)
from gideon.hypermid.enrollment_source import NativeEnrollmentSource
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.memory import HypermidMemoryProvider, _trace
from gideon.interfaces.dashboard.token_auth import (
    reset_secret_cache,
    use_persistent_secret,
)
from gideon.security.approval_answer import CHANNEL, Principal, principal_from_record
from gideon.security.capture_origin import capturing, owner_word_capture
from gideon.security.session_credentials import begin_turn, end_turn


@pytest.mark.asyncio
async def test_real_owner_capture_and_exclusive_retraction(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    use_persistent_secret()
    reset_secret_cache()
    log = await accepted_log(tmp_path)
    key = "dashboard:durable"
    row = log._read_messages(key)[0]
    ingress = row["meta"]["ingress"]
    actor = principal_from_record(ingress["principal"])
    credential = begin_turn(
        key,
        actor,
        turn_id="capture-turn",
        memory_mode="persistent",
        ingress_event_id=ingress["source_event_id"],
        ingress_digest=ingress["source_digest"],
    )
    scope = Scope("capture-owner", "capture-project", "host-workspace")
    grant = NativeEnrollmentSource().issue(scope=scope)
    enrollment = LocalEnrollment(
        scope,
        Id("capture-credential"),
        Id("capture-capability"),
        tuple(grant["operations"]),
        tuple(Id(v) for v in grant["resources"]),
        grant["expires_ms"],
    )
    root = tmp_path / "hypermid" / "runtime"
    record = root / "connection.json"
    journal = MemoryJournal(workspace=tmp_path / "memory-journal")
    journal.init()
    lifecycle = HypermidLifecycle(
        HypermidAdapter(HypermidClient(record, scope=scope), mode="primary"),
        DaemonConfig(
            transport=DaemonTransport.UNIX_SOCKET,
            endpoint=str(root / "capture.sock"),
            executable=str(Path("target/debug/hypermid-daemon").resolve()),
            start_on_demand=True,
            auth=LocalAuthConfig(
                method=LocalAuthMethod.PEER_AND_HMAC,
                token_file=str(root / "auth.token"),
                require_peer_identity=True,
            ),
        ).with_connection_record(str(record)),
        connection_record=record,
        enrollment=enrollment,
        runtime=SimpleNamespace(
            ctx_builder=SimpleNamespace(memory=journal),
            conv_log=log,
            consolidator=HistoryConsolidator(log, journal),
        ),
    )
    provider = None
    try:
        assert (await lifecycle.start()).available
        await lifecycle._apply_mode("primary", activate_primary=True)
        provider = HypermidMemoryProvider(
            record,
            scope=scope,
            capability_id=enrollment.capability_id,
            writer=lifecycle.writer,
        )
        legacy = GideonRecord(
            "independent",
            MemoryKind.SEMANTIC,
            text="An independently imported fact.",
            extra={"owner_confirmed": True, "conversation_id": key},
        )
        provider.put([legacy])
        capture = owner_word_capture(log, key, row)
        assert capture is not None
        learned = GideonRecord(
            "owner-choice", MemoryKind.SEMANTIC, text="The owner chose the blue plan."
        )
        # A host receipt alone cannot substitute for committed native history.
        with pytest.raises(HypermidRemoteError) as missing:
            provider.put_captured([learned], capture)
        assert missing.value.error.code == "CAPTURE_SOURCE_DENIED"
        assert provider.get(learned.id) is None
        builder = PromptAssembler(memory=provider, conversation_log=log)
        assembled = lifecycle.primary_engine.assemble(
            builder, row["content"], is_new_session=False, session_key=key
        )
        assert assembled.metadata["hypermid"]["cursor"]["sequence"] >= 1
        with capturing(capture):
            provider.put([learned])
        origins = provider.record_capture_origins(learned.id)
        assert origins["record_id"] == str(provider._native_record_id(learned.id))
        assert len(origins["origins"]) == 1
        origin = origins["origins"][0]
        assert origin["original_actor"] == ingress["principal"]
        assert origin["source_event_id"] == str(capture.native_source_event_id)
        assert origin["source_digest"] == str(capture.native_source_digest)
        assert str(capture.native_source_digest) != capture.ingress_own_digest
        with pytest.raises(PermissionError):
            provider.put_captured(
                [learned],
                replace(capture, original_actor=Principal(CHANNEL, "foreign")),
            )
        with pytest.raises(PermissionError):
            provider.put_captured([learned], replace(capture, own_text="forged words"))
        assert provider.record_capture_origins(legacy.id)["origins"] == []
        native_parent, _ = provider._remote_get(learned.id)
        legacy_parent, _ = provider._remote_get(legacy.id)
        owner_span = ProvenanceSpan(
            Id(origin["source_id"]),
            0,
            len(capture.source_bytes),
            capture.native_source_digest,
        )
        legacy_span = provider._draft(legacy).provenance[0]

        def child(identifier, spans, parents):
            draft = RecordDraft(
                Id(identifier),
                scope,
                RecordKind.FACT,
                "semantic",
                identifier + " blue plan evidence",
                1.0,
                1.0,
                provenance=tuple(spans),
                lineage=tuple(
                    LineageEdge(parent.id, "derived_from", parent.current.digest)
                    for parent in parents
                ),
            )
            request = MutationRequest(
                MemoryOperation.CREATE,
                scope,
                scope,
                RevisionPrecondition.must_not_exist(),
                _trace(),
                record_id=draft.id,
                category=draft.category,
            )
            return provider._call(
                lambda client: client.create(
                    request,
                    draft,
                    now_ms=int(time.time() * 1000),
                    authority_resource=Id("memory-records"),
                )
            ).record

        exclusive = child("exclusive-child", [], [native_parent])
        shared = child(
            "shared-child", [owner_span, legacy_span], [native_parent, legacy_parent]
        )
        # Neither another workspace nor a stale writer lease can reuse the owner capture authority.
        target = Scope(scope.owner_id, scope.project_id, "ungranted-workspace")
        request = MutationRequest(
            MemoryOperation.CREATE,
            scope,
            target,
            RevisionPrecondition.must_not_exist(),
            _trace(),
            record_id=Id("wrong-scope"),
            category="semantic",
        )
        draft = replace(provider._draft(learned), id=request.record_id, scope=target)
        with pytest.raises(HypermidRemoteError):
            provider._call(
                lambda client: client.write_captured(
                    request,
                    draft,
                    sources=(provider._source(draft, learned),),
                    capture=OwnerWordCapture.from_verified(capture),
                    writer_lease=provider._capture_writer_lease(),
                    now_ms=int(time.time() * 1000),
                )
            )
        from gideon.hypermid.foundation import Digest

        typed = OwnerWordCapture.from_verified(capture)
        target_draft = provider._draft(
            GideonRecord(
                "bad-capture", MemoryKind.SEMANTIC, text="Bad capture evidence"
            )
        )
        valid_source = replace(
            provider._source(target_draft, learned),
            source_id=Id(origin["source_id"]),
            kind=SourceKind.MESSAGE,
            source_digest=capture.native_source_digest,
            captured_content=capture.source_bytes.decode(),
            locator="chat:"
            + str(capture.history_session_id)
            + ":"
            + str(capture.native_source_event_id),
            capture_method="accepted_owner_words",
        )
        target_draft = replace(target_draft, provenance=(owner_span,))
        bad_request = MutationRequest(
            MemoryOperation.CREATE,
            scope,
            scope,
            RevisionPrecondition.must_not_exist(),
            _trace(),
            record_id=target_draft.id,
            category=target_draft.category,
        )
        stale_lease = dict(provider._capture_writer_lease(), fence_epoch=0)
        with pytest.raises(HypermidRemoteError) as stale:
            provider._call(
                lambda client: client.write_captured(
                    bad_request,
                    target_draft,
                    sources=(valid_source,),
                    capture=typed,
                    writer_lease=stale_lease,
                    now_ms=int(time.time() * 1000),
                )
            )
        assert stale.value.error.code == "CAPTURE_SOURCE_DENIED"
        with pytest.raises(HypermidRemoteError):
            provider._call(
                lambda client: client.write_captured(
                    bad_request,
                    target_draft,
                    sources=(valid_source,),
                    capture=replace(typed, source_digest=Digest.sha256(b"wrong")),
                    writer_lease=provider._capture_writer_lease(),
                    now_ms=int(time.time() * 1000),
                )
            )
        retracted = provider.retract_chat_sources(capture.history_session_id)
        assert set(retracted.deleted_ids) == {native_parent.id, exclusive.id}
        assert retracted.retained_unproven >= 1 and retracted.retained_independent >= 1
        assert provider.get(learned.id).is_deleted
        assert provider.get(exclusive.id).is_deleted
        assert not provider.get(shared.id).is_deleted
        assert provider.get(legacy.id).text == legacy.text
        assert (
            log.resolve_source_event(key, str(capture.native_source_event_id))
            == capture.source_bytes
        )
        with pytest.raises(HypermidRemoteError) as retired:
            provider.put_captured(
                [
                    GideonRecord(
                        "resurrect", MemoryKind.SEMANTIC, text="Reused blue plan"
                    )
                ],
                capture,
            )
        assert retired.value.error.code == "CHAT_SOURCE_RETRACTED"
        assert provider.get("resurrect") is None
        # The ordinary native record route cannot reuse an already retired proven snapshot either.
        with pytest.raises(HypermidRemoteError) as replay:
            child("ordinary-replay", [owner_span], [])
        assert replay.value.error.code == "CHAT_SOURCE_RETRACTED"
        again = provider.retract_chat_sources(capture.history_session_id)
        assert again.deleted_ids == ()
        assert not provider.get(shared.id).is_deleted
    finally:
        end_turn(credential)
        if provider is not None:
            provider.close()
        await lifecycle.stop()
        reset_secret_cache()
