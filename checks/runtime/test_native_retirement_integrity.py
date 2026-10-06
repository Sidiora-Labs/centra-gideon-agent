"""Native capture integrity and exact-revision retirement admission."""

import hashlib
import json
import sqlite3
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
)
from gideon.hypermid.enrollment_source import NativeEnrollmentSource
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.memory import HypermidMemoryProvider, _trace
from gideon.hypermid.sources import scope_digest
from gideon.interfaces.dashboard.token_auth import (
    reset_secret_cache,
    use_persistent_secret,
)
from gideon.security.approval_answer import app, principal_from_record, principal_record
from gideon.security.capture_origin import owner_word_capture
from gideon.security.durable_work import sign_ingress
from gideon.security.session_credentials import begin_turn, end_turn


@pytest.mark.asyncio
async def test_real_retirement_integrity_and_republication(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    use_persistent_secret()
    reset_secret_cache()
    log = await accepted_log(tmp_path)
    key = "dashboard:durable"
    first = log._read_messages(key)[0]
    actor = principal_from_record(first["meta"]["ingress"]["principal"])
    scope = Scope("integrity-owner", "integrity-project", "host-workspace")
    grant = NativeEnrollmentSource().issue(scope=scope)
    enrollment = LocalEnrollment(
        scope,
        Id("integrity-credential"),
        Id("integrity-capability"),
        tuple(grant["operations"]) + ("archive",),
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
            endpoint=str(root / "integrity.sock"),
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
    credential = None
    try:
        assert (await lifecycle.start()).available
        await lifecycle._apply_mode("primary", activate_primary=True)
        provider = HypermidMemoryProvider(
            record,
            scope=scope,
            capability_id=enrollment.capability_id,
            writer=lifecycle.writer,
        )
        builder = PromptAssembler(memory=provider, conversation_log=log)
        legacy = GideonRecord(
            "independent", MemoryKind.SEMANTIC, text="Independent preserved knowledge."
        )
        provider.put([legacy])

        def bind(canonical, row):
            nonlocal credential
            if credential is not None:
                end_turn(credential)
            ingress = row["meta"]["ingress"]
            credential = begin_turn(
                canonical,
                actor,
                turn_id=ingress["source_event_id"],
                memory_mode="persistent",
                ingress_event_id=ingress["source_event_id"],
                ingress_digest=ingress["source_digest"],
            )
            result = lifecycle.primary_engine.assemble(
                builder, row["content"], is_new_session=False, session_key=canonical
            )
            assert result.metadata["hypermid"]["cursor"]["sequence"] >= 1
            capture = owner_word_capture(log, canonical, row)
            assert capture is not None
            return capture

        def accepted_words(canonical, text, label):
            ingress = sign_ingress(
                {
                    "principal": principal_record(actor),
                    "source_thread": canonical,
                    "source_user": actor.name,
                    "source_event_id": label,
                    "source_digest": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
            log.append(
                canonical, "user", text, meta={"ingress": ingress, "turn_id": label}
            )
            log.update_metadata(
                canonical,
                {
                    "initiator": principal_record(actor),
                    "memory_mode": "persistent",
                    "lifecycle": "active",
                },
            )
            return log.source_events(canonical)[-1].message

        captures = {}
        sources = {}

        def publish(identifier, row, canonical=key):
            capture = bind(canonical, row)
            provider.put_captured(
                [
                    GideonRecord(
                        identifier,
                        MemoryKind.SEMANTIC,
                        text=identifier + " accepted owner evidence",
                    )
                ],
                capture,
            )
            origin = provider.record_capture_origins(identifier)["origins"][0]
            captures[identifier] = capture
            sources[identifier] = origin
            return provider._remote_get(identifier)[0]

        clean = publish("clean", first)
        damaged = publish(
            "bad-digest",
            accepted_words(key, "I prefer green notebooks.", "green-words"),
        )
        ambiguous = publish(
            "bad-actor", accepted_words(key, "I prefer plain labels.", "plain-words")
        )
        scoped = publish(
            "bad-scope", accepted_words(key, "I prefer wide margins.", "margin-words")
        )
        archived = publish(
            "archived", accepted_words(key, "I prefer dark ink.", "ink-words")
        )
        archive_request = MutationRequest(
            MemoryOperation.ARCHIVE,
            scope,
            scope,
            RevisionPrecondition.match(archived.current.digest),
            _trace(),
            record_id=archived.id,
            category=archived.category,
        )
        provider._call(
            lambda client: client.archive(
                archive_request,
                now_ms=int(time.time() * 1000),
                authority_resource=Id("memory-records"),
            )
        )

        def child(identifier, spans, parents):
            draft = RecordDraft(
                Id(identifier),
                scope,
                RecordKind.FACT,
                "semantic",
                identifier + " composite evidence",
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

        changing = GideonRecord(
            "changing-parent",
            MemoryKind.SEMANTIC,
            text="An original independent source.",
        )
        provider.put([changing])
        original_parent = provider._remote_get(changing.id)[0]
        historical = child("historical-independent-child", [], [original_parent])
        changed_parent = publish(
            "changing-parent",
            accepted_words(key, "I prefer graph paper.", "graph-words"),
        )
        assert changed_parent.current.digest != original_parent.current.digest
        clean_capture = captures["clean"]
        clean_span = ProvenanceSpan(
            Id(sources["clean"]["source_id"]),
            0,
            len(clean_capture.source_bytes),
            clean_capture.native_source_digest,
        )
        independent_span = provider._draft(legacy).provenance[0]
        exclusive = child("exclusive", [], [clean])
        shared = child(
            "shared",
            [clean_span, independent_span],
            [clean, provider._remote_get(legacy.id)[0]],
        )
        # A valid existing capture keeps its original digest without a silent rewrite.
        db = root / "state" / "memory.sqlite3"
        legacy_digest = hashlib.sha256(
            json.dumps(
                OwnerWordCapture.from_verified(bind(key, first)).to_wire(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with sqlite3.connect(db) as sql:
            sql.execute(
                "UPDATE memory_capture_origins SET capture_digest=? WHERE source_id=?",
                (legacy_digest, sources["clean"]["source_id"]),
            )
        assert (
            provider.record_capture_origins("clean")["origins"][0]["capture_digest"]
            == legacy_digest
        )
        # Establish a real app namespace before corrupting a capture's history binding.
        folder = tmp_path / "apps" / "isolated-app"
        folder.mkdir(parents=True)
        (folder / "app.json").write_text(
            json.dumps(
                {
                    "name": "isolated-app",
                    "version": "1.0.0",
                    "permissions": {"agent": "tools", "memory": "app-scoped"},
                }
            )
        )
        (folder / "installed.json").write_text(
            json.dumps(
                {
                    "name": "isolated-app",
                    "version": "1.0.0",
                    "enabled": True,
                    "origin": "local",
                    "tier": "community",
                }
            )
        )
        end_turn(credential)
        credential = begin_turn(
            "app-origin",
            actor,
            turn_id="app-turn",
            memory_mode="persistent",
            created_by_app="isolated-app",
            work_actor=app("isolated-app"),
        )
        provider.put(
            [
                GideonRecord(
                    "app-kept",
                    MemoryKind.SEMANTIC,
                    text="Scoped app knowledge remains separate.",
                )
            ]
        )
        app_scope = provider._app_receipt().scope
        app_record = provider.get("app-kept")
        assert app_record.extra["native_scope"] == app_scope.to_wire()
        bind(key, first)
        with sqlite3.connect(db) as sql:
            sql.execute(
                "UPDATE memory_capture_origins SET capture_digest=? WHERE source_id=?",
                ("0" * 64, sources["bad-digest"]["source_id"]),
            )
            sql.execute(
                "UPDATE memory_capture_origins SET original_actor_json=? WHERE source_id=?",
                (
                    '{"kind":"owner","name":"replacement","tenant":""}',
                    sources["bad-actor"]["source_id"],
                ),
            )
            sql.execute(
                "UPDATE memory_capture_origins SET history_scope_digest=? WHERE source_id=?",
                (str(scope_digest(app_scope)), sources["bad-scope"]["source_id"]),
            )
        for identifier in ("bad-digest", "bad-actor", "bad-scope"):
            with pytest.raises(HypermidRemoteError) as invalid:
                provider.record_capture_origins(identifier)
            assert invalid.value.error.code == "CAPTURE_PROOF_DAMAGED"
        before = {
            item: provider.get(item).text
            for item in (
                "bad-digest",
                "bad-actor",
                "bad-scope",
                "shared",
                "independent",
                "historical-independent-child",
            )
        }
        retired = provider.retract_chat_sources(clean_capture.history_session_id)
        assert set(retired.deleted_ids) == {clean.id, exclusive.id, changed_parent.id}
        assert retired.retained_unproven >= 4 and retired.retained_independent >= 1
        for identifier, content in before.items():
            actual = provider.get(identifier)
            assert actual.text == content and not actual.is_deleted
        assert provider.get("clean").is_deleted and provider.get("exclusive").is_deleted
        assert provider.record_capture_origins("clean")["origins"][0]["retired"]
        assert (
            log.resolve_source_event(key, str(clean_capture.native_source_event_id))
            == clean_capture.source_bytes
        )
        for identifier in ("clean", "exclusive", "archived"):
            with pytest.raises(HypermidRemoteError) as blocked:
                provider.restore(identifier)
            assert blocked.value.error.code == "CHAT_SOURCE_RETRACTED"
        with pytest.raises(HypermidRemoteError) as derive:
            child("late-child", [], [clean])
        assert derive.value.error.code == "CHAT_SOURCE_RETRACTED"
        with pytest.raises(HypermidRemoteError) as indirect:
            child("late-indirect", [], [exclusive])
        assert indirect.value.error.code == "CHAT_SOURCE_RETRACTED"
        with pytest.raises(HypermidRemoteError) as corrupted:
            child(
                "damaged-source-copy",
                [
                    ProvenanceSpan(
                        Id(sources["bad-digest"]["source_id"]),
                        0,
                        len(captures["bad-digest"].source_bytes),
                        captures["bad-digest"].native_source_digest,
                    )
                ],
                [],
            )
        assert corrupted.value.error.code == "CAPTURE_PROOF_DAMAGED"
        assert (
            provider.retract_chat_sources(clean_capture.history_session_id).deleted_ids
            == ()
        )
        # Existing app facts are retained and accessible under their original native receipt.
        end_turn(credential)
        credential = begin_turn(
            "app-origin",
            actor,
            turn_id="app-readback",
            memory_mode="persistent",
            created_by_app="isolated-app",
            work_actor=app("isolated-app"),
        )
        assert provider.get("app-kept").text == app_record.text
        assert provider.get("app-kept").extra["native_scope"] == app_scope.to_wire()
        # A new independently verified owner event may teach again; old exact revisions stay retired.
        fresh = accepted_words(
            "dashboard:fresh", "I now prefer the blue plan again.", "fresh-owner-words"
        )
        fresh_capture = bind("dashboard:fresh", fresh)
        provider.put_captured(
            [
                GideonRecord(
                    "clean", MemoryKind.SEMANTIC, text="A newly taught blue plan."
                )
            ],
            fresh_capture,
        )
        renewed = provider._remote_get("clean")[0]
        assert renewed.current.number == clean.current.number + 1
        assert not provider.get("clean").is_deleted
        assert provider.record_capture_origins("clean")["origins"][0][
            "history_session_id"
        ] == str(fresh_capture.history_session_id)
        with pytest.raises(HypermidRemoteError) as old_revision:
            child("old-revision-child", [], [clean])
        assert old_revision.value.error.code == "CHAT_SOURCE_RETRACTED"
        assert child("fresh-revision-child", [], [renewed]).status.value == "active"
    finally:
        if credential is not None:
            end_turn(credential)
        if provider is not None:
            provider.close()
        await lifecycle.stop()
        reset_secret_cache()
