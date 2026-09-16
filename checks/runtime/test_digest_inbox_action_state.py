"""Digest adapters against native inboxes, semantic rows and workflow journals."""

import asyncio
import json
import time
from datetime import datetime

import pytest

from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows.journal import ledger
from gideon.cognition.knowledge.source_digest import DigestResult
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.memory_service import MemoryService
from gideon.cognition.proactive.approval import ApprovalRule, Verdict, rule_to_value
from gideon.cognition.proactive.gate import GateDisposition, GateOutcome, GateResult
from gideon.cognition.proactive.manifest import CollectedItem, build_manifest
from gideon.cognition.proactive.pipeline import TriageResult
from gideon.cognition.proactive.proposals import (
    Proposal,
    ProposalBatch,
    RefusedProposal,
)
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig
from gideon.core.sqlite_compat import sqlite3
from gideon.engine.session import ConversationDirectory
from gideon.integrations.action_providers import inbox_op_provider as inbox
from gideon.integrations.action_providers import registry, services
from gideon.integrations.action_providers import source_digest_provider as source
from gideon.integrations.action_providers import triage_digest_provider as triage
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.inbox import InboxItem, InboxState, InboxStore
from gideon.integrations.inbox_service import InboxService
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security import trust_mode

CTX = ActionContext("workflow_node")


@pytest.fixture(autouse=True)
def action_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "config.json").write_text('{"providers": []}')
    monkeypatch.setattr(services, "_services", None)
    monkeypatch.setattr(registry, "_providers", {})
    monkeypatch.setattr(
        trust_mode._TRUST, "_on_disable", list(trust_mode._TRUST._on_disable)
    )
    return tmp_path


def configure(home, **proactive):
    (home / "config.json").write_text(
        json.dumps({"providers": [], "proactive": proactive})
    )
    return AppConfig.load().proactive


def live_inbox(home):
    state = ConsoleState(ConversationDirectory(AppConfig.load()), start_time=0)
    store = InboxStore(home / "items.json")
    flags = InboxState(home / "flags.json")
    state._inbox_svc = InboxService(state=flags, store=store)
    state._inbox_store = store
    services.set_action_services(services.ActionServices(state, asyncio.create_task))
    return state, store, flags


def add_item(store, identifier="room_message"):
    item = InboxItem(
        id=identifier,
        channel="room",
        channel_name="Room",
        thread_ts="thread-7",
        message="please review",
        sender_id="author",
        sender_name="Author",
    )
    store.add(item)
    store.flush()
    return item


@pytest.mark.parametrize(
    "operation,target",
    [("archive", "handled"), ("mark_read", "seen"), ("dismiss", "dismissed")],
)
@pytest.mark.asyncio
async def test_status_mutation_is_persisted_reversible_and_idempotent(
    action_home, operation, target
):
    _, store, flags = live_inbox(action_home)
    item = add_item(store)
    provider = inbox.InboxOpActionProvider()
    result = await provider.execute({"op": operation, "item_id": item.id}, CTX)
    assert result.success and result.reversal and item.status == target
    persisted = InboxStore(action_home / "items.json")
    persisted.load()
    assert persisted.items[item.id].status == target
    if operation == "dismiss":
        loaded = InboxState(action_home / "flags.json")
        loaded.load()
        assert item.id in flags.dismissed and item.id in loaded.dismissed
    duplicate = await provider.execute({"op": operation, "item_id": item.id}, CTX)
    assert (
        duplicate.success
        and not duplicate.reversal
        and json.loads(duplicate.stdout)["changed"] is False
    )
    restored = await provider.reverse(result.reversal)
    assert (
        restored.success and item.status == "pending" and item.id not in flags.dismissed
    )


@pytest.mark.asyncio
async def test_stale_status_undo_refuses_after_an_actual_later_write(action_home):
    _, store, _ = live_inbox(action_home)
    item = add_item(store)
    provider = inbox.InboxOpActionProvider()
    result = await provider.execute({"op": "archive", "item_id": item.id}, CTX)
    store.update(item.id, status="dismissed")
    refusal = await provider.reverse(result.reversal)
    assert (
        not refusal.success
        and "newer change" in refusal.error
        and item.status == "dismissed"
    )


@pytest.mark.asyncio
async def test_mute_reversal_survives_item_removal_and_restores_actual_flags(
    action_home,
):
    _, store, flags = live_inbox(action_home)
    item = add_item(store)
    provider = inbox.InboxOpActionProvider()
    result = await provider.execute({"op": "mute_thread", "item_id": item.id}, CTX)
    assert result.success and flags.muted_threads == {"thread-7"}
    del store.items[item.id]
    store.save()
    assert (await provider.reverse(result.reversal)).success
    reloaded = InboxState(action_home / "flags.json")
    reloaded.load()
    assert not flags.muted_threads and not reloaded.muted_threads


@pytest.mark.asyncio
async def test_draft_body_alias_and_thread_identifier_fallback(action_home):
    _, store, _ = live_inbox(action_home)
    item = add_item(store, "channel_thread_extra")
    item.thread_ts = ""
    item.draft = "original"
    assert inbox._thread_key(item) == "thread_extra"
    provider = inbox.InboxOpActionProvider()
    result = await provider.execute(
        {"action_type": "reply_draft", "item_id": item.id, "body": "  replacement  "},
        CTX,
    )
    assert result.success and item.draft == "replacement" and item.status == "pending"
    assert (
        await provider.reverse(result.reversal)
    ).success and item.draft == "original"


@pytest.mark.parametrize(
    "handle", ["", "other:e30=", "inbox-op:!", "inbox-op:W10=", "inbox-op:/w=="]
)
@pytest.mark.asyncio
async def test_invalid_handle_is_rejected_without_service_lookup(handle):
    result = await inbox.InboxOpActionProvider().reverse(handle)
    assert not result.success and "unrecognised reversal handle" in result.error


@pytest.mark.asyncio
async def test_source_digest_empty_window_uses_native_engine_and_closes_owned_connection(
    action_home,
):
    state, _, _ = live_inbox(action_home)
    store = KnowledgeStore(db_path=str(action_home / "knowledge.db"))
    result = await source._SourceDigestRun(store, state).execute()
    assert result.success and "no new" in result.stdout
    with pytest.raises(sqlite3.ProgrammingError):
        store.db.execute("SELECT 1")
    assert not (action_home / "model_calls.jsonl").exists()


@pytest.mark.asyncio
async def test_source_digest_refuses_unwired_then_runs_with_actual_console_state(
    action_home,
):
    provider = source.SourceDigestActionProvider()
    missing = await provider.execute({}, CTX)
    assert (
        not missing.success
        and missing.error == "source digest: no dashboard state to notify"
    )
    live_inbox(action_home)
    result = await provider.execute({}, CTX)
    assert result.success and result.stdout.startswith("source digest: ")


def test_source_receipt_and_creation_only_schedule_preserve_operator_edits(action_home):
    receipt = source._source_result(
        DigestResult(item_id="digest-1", item_count=4, notified=True)
    )
    assert (
        receipt.stdout == "source digest: created digest-1 from 4 items (notified=True)"
    )
    triggers = TriggerStore(base_dir=action_home)
    source.reconcile_source_digest_cron(triggers)
    original = triggers.get(source.SOURCE_DIGEST_JOB_NAME).trigger
    assert (
        original.capabilities and original.next_fire_at and original.delivery == "none"
    )
    original.enabled = False
    original.spec["expr"] = "12 16 * * 1"
    original.name = "Operator schedule"
    triggers.upsert(original)
    source.reconcile_source_digest_cron(triggers)
    retained = triggers.get(source.SOURCE_DIGEST_JOB_NAME).trigger
    assert retained.to_dict() == original.to_dict()


@pytest.mark.parametrize("value,hours", [("bad", 24), (-7, 0), (0, 24), (2.5, 2.5)])
def test_window_fallback_remains_bounded_and_has_matching_iso(value, hours):
    before = time.time()
    timestamp, iso = triage._window({"window_hours": value})
    after = time.time()
    assert before - hours * 3600 <= timestamp <= after - hours * 3600
    assert abs(datetime.fromisoformat(iso).timestamp() - timestamp) < 0.001


def test_filter_and_capability_parsing_retains_scope_and_narrow_default():
    rules = triage._rules(
        {
            "filter_rules": '[" ignore bots ", {"source":"inbox","rule":"  skip noise "}, {"rule":""}, 7]'
        }
    )
    assert [(rule.source, rule.rule) for rule in rules] == [
        ("*", "ignore bots"),
        ("inbox", "skip noise"),
    ]
    assert triage._rules({"filter_rules": "[broken"}) == []
    assert triage._capabilities({"capabilities": "[broken"}) == frozenset({"inbox-op"})
    assert triage._capabilities({"capabilities": []}) == frozenset({"inbox-op"})
    assert triage._capabilities(
        {"capabilities": '[" inbox-op ", "notify", ""]'}
    ) == frozenset({"inbox-op", "notify"})


def test_approval_scan_reads_only_exact_policy_namespace_and_keeps_borrowed_archive_open(
    action_home,
):
    store = SemanticArchive(db_path=action_home / "memory.db")
    store.init()
    try:
        memory = MemoryService.over_vector_store(store)
        rule = ApprovalRule("archive:sender:robot", Verdict.APPROVE)
        assert (
            memory.set_semantic(rule.key, rule_to_value(rule), 1.0, "user_explicit")
            is None
        )
        assert (
            memory.set_semantic(
                "user.preference.local", "ordinary", 1.0, "user_explicit"
            )
            is None
        )
        loaded = triage._approval_rules(memory)
        assert (
            len(loaded) == 1
            and loaded[0].pattern == rule.pattern
            and loaded[0].verdict == Verdict.APPROVE
        )
        assert store.db.execute("SELECT 1").fetchone()[0] == 1
    finally:
        store.close()


def test_gate_and_refusal_records_share_real_instance_journal_and_keep_order():
    manifest = build_manifest([CollectedItem("inbox", "message", "noise")])
    item = manifest.items[0]
    gate = GateResult(
        dropped=(item,),
        outcomes={
            item.ordinal: GateOutcome(
                GateDisposition.DROP, "ignored sender", "skip bots"
            )
        },
    )
    result = TriageResult(
        manifest=manifest,
        gate=gate,
        batch=ProposalBatch(
            refused=(
                RefusedProposal("unknown_item", "999", "archive", "outside manifest"),
            )
        ),
    )
    context = ActionContext(
        "workflow_node",
        payload={"run_id": "real-digest", "instance_path": "root.children[2]"},
    )
    assert triage._record(result, context) == 2
    rows = ledger("real-digest")
    assert (
        len(rows) == 2
        and rows[0]["rationale"] == "ignored sender"
        and rows[1]["reason"] == "unknown_item"
    )
    assert all(
        row["instance_path"] == "root.children[2]" and row["node_id"] == "triage"
        for row in rows
    )
    assert (
        triage._record(
            result, ActionContext("workflow_node", payload={"run_id": "no-instance"})
        )
        == 0
    )
    assert ledger("no-instance") == []


@pytest.mark.asyncio
async def test_disabled_auto_stage_records_reason_per_actual_proposal(action_home):
    cfg = configure(action_home, triage_enabled=True, auto_execute_enabled=False)
    manifest = build_manifest([CollectedItem("inbox", "message", "please review")])
    proposal = Proposal(manifest.items[0].ordinal, "archive", "trivial")
    stage = triage._auto_stage(
        {},
        ActionContext(
            "workflow_node",
            payload={"run_id": "disabled-auto", "instance_path": "root"},
        ),
        cfg,
    )
    result = await stage((proposal,), manifest)
    summary = result.summary()
    assert summary["auto_executed"] == [] and summary["auto_ledger_rows"] == 0
    assert summary["auto_deferred"] == [
        {
            "item_id": proposal.item_id,
            "action_type": "archive",
            "tier": "trivial",
            "reason": "auto_execute_disabled",
            "rule": "",
        }
    ]
    rows = ledger("disabled-auto")
    assert rows == []


@pytest.mark.asyncio
async def test_public_triage_off_then_empty_window_short_circuits_without_inference(
    action_home,
):
    provider = triage.TriageDigestActionProvider()
    configure(action_home, triage_enabled=False)
    refused = await provider.execute({}, CTX)
    assert not refused.success and "triage_enabled is off" in refused.error
    configure(action_home, triage_enabled=True)
    live_inbox(action_home)
    result = await provider.execute({}, CTX)
    assert result.success, result.error
    summary = json.loads(result.stdout)
    assert summary["short_circuited"] is True and summary["llm_calls"] == 0
    assert summary["collected"] == 0 and summary["ledger_rows"] == 0
    assert not (action_home / "model_calls.jsonl").exists()
