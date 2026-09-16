import asyncio
import hashlib
import json
import operator
import shlex
import sys
from datetime import datetime, timedelta, timezone

import pytest

from gideon.cognition.memory_lifecycle import DailyMemoryRollup, RecallOrder, tag_values
from gideon.cognition.memory_record import (
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryTier,
)
from gideon.cognition.memory_service import (
    PROCEDURAL_FOOTER,
    PROCEDURAL_HEADER,
    MemoryService,
)
from gideon.cognition.vector_memory import SemanticArchive, sqlite3
from gideon.core.config.loader import AppConfig


@pytest.fixture
def service(tmp_path, monkeypatch):
    home = tmp_path / "lifecycle-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    config = AppConfig.load()
    config.memory.graph_enabled = False
    config.dashboard.username = "owner"
    config.save()
    archive = SemanticArchive(db_path=home / "memory.db", embedding_dim=3)
    archive.init()
    yield MemoryService.over_vector_store(archive)
    archive.close()


def _fact(
    service,
    identity,
    *,
    scope=MemoryScope.SESSION,
    source="service",
    category=None,
    reference="session-a",
    count=0,
):
    service.put(
        [
            MemoryRecord(
                id=identity,
                kind=MemoryKind.SEMANTIC,
                value="observed fact",
                confidence=1.0,
                source=source,
                scope=scope,
                scope_ref=reference,
                category=category,
                recall_count=count,
            )
        ]
    )
    return service.get_record(identity)


def _episode_on(service, text, day, *, tags=None):
    assert service.write_episodic(text, tags=tags, source="user_explicit")
    identity = service._vs.db.execute(
        "SELECT id FROM episodic_memories ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    service._vs.db.execute(
        "UPDATE episodic_memories SET created_at=? WHERE id=?",
        (day + "T12:00:00+00:00", identity),
    )
    service._vs.db.commit()
    return identity


def test_identifiers_preserve_original_bytes_and_raw_whitespace():
    digest = lambda value: hashlib.md5(value.encode("utf-8")).hexdigest()[:12]
    assert MemoryService._working_key("session:ä") == "user.working." + digest(
        "session:ä"
    )
    assert MemoryService._procedural_key(
        "rg", "find|symbol", "success"
    ) == "user.procedural." + digest("rg|find|symbol|success")
    assert MemoryService._persona_key("A", " patient ") == "user.persona." + digest(
        "A| patient "
    )
    assert MemoryService._commitment_key("A", " check ") == "user.commitment." + digest(
        "A| check "
    )
    assert MemoryService._persona_key("A", "patient") != MemoryService._persona_key(
        "A", " patient "
    )


def test_working_buffer_cap_and_seal_keep_existing_store_semantics(service):
    text = "A" * 2100
    service.write_working_memory("session-a", text)
    record = service.get_record(service._working_key("session-a"))
    assert record.text == text[:2000]
    assert (record.tier, record.scope, record.scope_ref) == (
        MemoryTier.WORKING,
        MemoryScope.SESSION,
        "session-a",
    )
    _fact(service, "pref.scratch")
    _fact(service, "pref.already_sealed", source="seal")
    _fact(service, "pref.other_session", reference="other")
    assert service.seal_session("session-a") == 1
    assert service.get_record(record.id) is None
    assert service.get_record("pref.scratch") is None
    assert service.get_record("pref.already_sealed") is not None
    assert service.get_record("pref.other_session") is not None
    sealed = service.get_records(kinds={"episodic"})
    assert len(sealed) == 1 and sealed[0].source == ""
    assert any(
        event["memory_key"] == sealed[0].id and event["source"] == "seal"
        for event in service.get_events()
    )
    assert sealed[0].conversation_id == "session-a"
    assert sealed[0].scope == MemoryScope.GLOBAL


def test_short_rejected_seal_still_retires_working_note(service):
    service.write_working_memory("short", "tiny")
    assert service.seal_session("short") == 0
    assert service.get_record(service._working_key("short")) is None
    assert service.get_records(kinds={"episodic"}) == []


def test_promotion_preserves_reference_and_logs_after_each_scope_commit(service):
    _fact(
        service,
        "pref.recurring",
        scope=MemoryScope.WORKSPACE,
        reference="/owned",
        count=3,
    )
    _fact(service, "pref.one_use", count=1)
    assert service.promote_by_heat(threshold=0.0) == 1
    promoted = service.get_record("pref.recurring")
    assert promoted.scope == MemoryScope.GLOBAL and promoted.scope_ref == "/owned"
    assert service.get_record("pref.one_use").scope == MemoryScope.SESSION
    event = next(
        event
        for event in service.get_events()
        if event["event_type"] == "promote_scope"
    )
    assert (event["old_value"], event["new_value"], event["source"]) == (
        "workspace",
        "global",
        "heat_promote",
    )


def test_promotion_sql_failure_leaves_scope_unchanged(service):
    _fact(service, "pref.locked", count=3)
    service._vs.db.execute(
        "CREATE TRIGGER deny_scope BEFORE UPDATE OF scope ON semantic_memory BEGIN SELECT RAISE(ABORT, 'scope locked'); END"
    )
    with pytest.raises(sqlite3.Error, match="scope locked"):
        service.promote_by_heat(threshold=0.0)
    service._vs.db.rollback()
    assert service.get_record("pref.locked").scope == MemoryScope.SESSION
    assert not any(
        event["event_type"] == "promote_scope" for event in service.get_events()
    )


def test_recapture_reinforces_and_reassigns_procedural_session_scope(service):
    key = service.record_procedural(
        tool="rg", task_shape="find symbols", outcome="success", scope_ref="first"
    )
    service.record_procedural(
        tool="rg", task_shape="find symbols", outcome="success", scope_ref="first"
    )
    assert service.promote_by_heat(threshold=0.0) == 1
    assert service.get_record(key).scope == MemoryScope.GLOBAL
    service.record_procedural(
        tool="rg", task_shape="find symbols", outcome="success", scope_ref="second"
    )
    record = service.get_record(key)
    assert (
        record.recall_count == 3
        and record.scope == MemoryScope.SESSION
        and record.scope_ref == "second"
    )


@pytest.mark.parametrize(
    "outcome,allowed", [("success", True), ("failed", False), ("denied", False)]
)
def test_procedural_read_policy_is_exhaustive(service, outcome, allowed):
    key = service.record_procedural(
        tool="local_tool", task_shape="shape", outcome=outcome
    )
    service._vs.db.execute(
        "UPDATE semantic_memory SET scope='global', recall_count=9 WHERE key=?", (key,)
    )
    service._vs.db.commit()
    assert MemoryService._is_surfaceable_prior(service.get_record(key)) is allowed
    assert bool(service.procedural_priors()) is allowed
    if allowed:
        block = service.procedural_block()
        assert block.startswith(PROCEDURAL_HEADER) and block.endswith(PROCEDURAL_FOOTER)
    assert service.procedural_block(limit=0) == ""


def test_raw_failures_collapse_to_single_prior_with_sum_of_visits(service):
    keys = []
    for shape in ("one", "two", "three"):
        keys.append(
            service.record_procedural(
                tool="blocked_tool", task_shape=shape, outcome="denied"
            )
        )
        service.record_procedural(
            tool="blocked_tool", task_shape=shape, outcome="denied"
        )
    assert service.synthesize_failures() == 1
    records = service.get_records(kinds={"procedural"})
    assert len(records) == 1 and records[0].recall_count == 6
    assert (
        records[0].source == "failure_synthesis"
        and records[0].scope == MemoryScope.GLOBAL
    )
    assert "prefer an alternative" in service.procedural_block()
    assert all(service.get_record(key) is None for key in keys)
    assert service.synthesize_failures() == 0


def test_environment_claim_is_filtered_after_promotion(service):
    key = service.record_procedural(
        tool="fetch_url",
        task_shape="fetch",
        outcome="success",
        detail="connection refused on the first attempt",
    )
    service._vs.db.execute(
        "UPDATE semantic_memory SET scope='global', recall_count=9 WHERE key=?", (key,)
    )
    service._vs.db.commit()
    assert service.procedural_priors() == []


def test_persona_order_and_raw_trait_identity_are_preserved(service):
    first = service.record_persona(agent="A", trait="patient")
    second = service.record_persona(agent="A", trait=" direct ")
    service.record_persona(agent="A", trait=" direct ")
    assert service.get_record(second).text == "direct"
    assert first != second
    assert service.persona_block(agent="A", limit=1).splitlines()[1] == "- direct"
    assert service.persona_block(agent="A", limit=-1).splitlines()[1] == "- direct"
    assert service.persona_block(agent="other") == ""


def test_commitment_cap_is_active_per_agent_and_crosses_due_dates(service):
    captured = service.record_commitment(
        agent="A",
        channel="chat",
        text="check amber",
        due_window="2000-01-01",
        confidence=0.8,
        enabled=True,
        max_per_day=1,
    )
    assert captured
    assert (
        service.record_commitment(
            agent="A",
            channel="chat",
            text="check cobalt",
            due_window="2099-01-01",
            confidence=0.9,
            enabled=True,
            max_per_day=1,
        )
        is None
    )
    other = service.record_commitment(
        agent="B",
        channel="mail",
        text="check orchid",
        due_window="2000-01-01",
        confidence=0.9,
        enabled=True,
        max_per_day=1,
    )
    due = service.due_commitments_all(now_iso="2026-09-16")
    assert {entry["key"] for entry in due} == {captured, other}
    assert {entry["agent"] for entry in due} == {"A", "B"}
    assert service.due_commitments(agent="A", now_iso="2026-09-16") == [
        dict(key=captured, text="check amber", channel="chat", due_window="2000-01-01")
    ]
    assert service.dismiss_commitment(captured)
    assert service.due_commitments(agent="A", now_iso="2026-09-16") == []
    assert service.record_commitment(
        agent="A",
        channel="chat",
        text="check cobalt",
        due_window="2099-01-01",
        confidence=0.9,
        enabled=True,
        max_per_day=1,
    )


def test_commitment_identity_uses_agent_and_raw_text_not_channel(service):
    arguments = dict(
        agent="A",
        text="check amber",
        due_window="2000-01-01",
        confidence=0.9,
        enabled=True,
    )
    first = service.record_commitment(channel="chat", **arguments)
    second = service.record_commitment(channel="mail", **arguments)
    assert first == second
    assert service.get_record(first).value["channel"] == "mail"


def test_ttl_exact_boundary_global_user_exception_and_invalid_timestamps(service):
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    rows = [
        ("pref.boundary", MemoryScope.SESSION, "service", now - timedelta(days=7)),
        (
            "pref.expired",
            MemoryScope.SESSION,
            "service",
            now - timedelta(days=7, seconds=1),
        ),
        (
            "pref.global_user",
            MemoryScope.GLOBAL,
            "user_explicit",
            now - timedelta(days=20),
        ),
        (
            "pref.session_user",
            MemoryScope.SESSION,
            "user_explicit",
            now - timedelta(days=20),
        ),
    ]
    for key, scope, source, stamp in rows:
        _fact(service, key, scope=scope, source=source, category="debug")
        service._vs.db.execute(
            "UPDATE semantic_memory SET updated_at=? WHERE key=?",
            (stamp.isoformat(), key),
        )
    _fact(service, "pref.malformed", category="debug")
    service._vs.db.execute(
        "UPDATE semantic_memory SET updated_at='not-a-timestamp' WHERE key='pref.malformed'"
    )
    service._vs.db.commit()
    assert service.expire_by_category(now=now) == 2
    assert (
        service.get_record("pref.boundary")
        and service.get_record("pref.global_user")
        and service.get_record("pref.malformed")
    )
    assert (
        service.get_record("pref.expired") is None
        and service.get_record("pref.session_user") is None
    )


def test_daily_digest_chronology_extract_bounds_and_idempotency(service):
    for index in range(22):
        _episode_on(
            service, f"activity {index:02d}: release checks passed.", "2026-09-14"
        )
    _episode_on(
        service, "An older release planning conversation happened.", "2026-09-13"
    )
    _episode_on(
        service, "Current day remains open for further observations.", "2026-09-16"
    )
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    assert service.build_daily_digest(now=now, max_days=1) == 1
    digest = service.daily_digests()[0]
    assert digest["day"] == "2026-09-14"
    assert "22 memory event(s)" in digest["text"] and "…and 2 more." in digest["text"]
    bounded = DailyMemoryRollup.extract("2026-09-14", ["x" * 210] * 22)
    assert len(bounded.splitlines()[1]) == 202
    assert bounded.endswith("…and 2 more.")
    assert service.build_daily_digest(now=now, max_days=1) == 0
    assert service.build_daily_digest(now=now, max_days=2) == 1
    assert [entry["day"] for entry in service.daily_digests()] == [
        "2026-09-14",
        "2026-09-13",
    ]


def test_digest_attempt_counter_does_not_claim_store_acceptance(service):
    for index in range(22):
        _episode_on(service, f"activity {index:02d}: " + "x" * 210, "2026-09-14")
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    assert service.build_daily_digest(now=now) == 1
    assert service.daily_digests() == []


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("not-json", []),
        ('["daily-digest", "2026-09-14"]', ["daily-digest", "2026-09-14"]),
        (None, []),
        (["actual"], ["actual"]),
    ],
)
def test_digest_tag_decoding_keeps_legacy_rows(stored, expected):
    assert tag_values({"tags": stored}) == expected


def test_recall_ranking_has_provenance_and_ownership_does_not_modify_score(service):
    _episode_on(
        service,
        "The amber launch checklist contains database validation.",
        "2026-09-14",
    )
    _episode_on(
        service,
        "Another amber launch checklist concerns the message queue.",
        "2026-09-13",
    )
    ranked = service.rank_episodic(
        query_text="amber launch checklist",
        now=datetime(2026, 9, 16, tzinfo=timezone.utc),
    )
    assert ranked
    for hit in ranked:
        base = float(hit.get("score", hit.get("cosine_sim", 0)) or 0)
        assert base <= hit["ranked_score"] <= base * 1.5
    events = {
        event["memory_key"]: event
        for event in service.get_events()
        if event["memory_type"] == "episodic"
    }
    assert all(events[hit["id"]]["source"] == "user_explicit" for hit in ranked)
    projected = [RecallOrder.provenance(hit) for hit in ranked]
    assert all(
        item["source"] == "" and item["contributor"] == "owner" for item in projected
    )
    assert all(
        set(item) == {"text", "source", "session", "created_at", "score", "contributor"}
        for item in projected
    )


def test_service_semantic_wal_and_record_inventory_share_archive(service):
    assert (
        service.set_semantic(
            "pref.editor", "vim", 1.0, "user_explicit", holder="self", weight=1.0
        )
        is None
    )
    assert service.get_all_semantic() == service._vs.get_all_semantic()
    service.record_recall(["pref.editor"])
    assert service.get_record("pref.editor").recall_count == 1
    assert service.set_semantic("pref.editor", "helix", 1.0, "user_explicit") is None
    update = next(
        event for event in service.get_events() if event["event_type"] == "update"
    )
    assert service.undo_event(update["id"])[0]
    assert json.loads(service.get_semantic("pref.editor")["value_json"]) == "vim"
    service.set_semantic("pref.next", "emacs", 1.0, "user_explicit")
    assert service.supersede_semantic("pref.editor", "pref.next", "user_explicit")
    assert service.get_record("pref.editor") is None
    assert service.delete_semantic("pref.next")
    assert len(service.get_records(include_deleted=True)) == 2
    assert service.memory_stats() == service._vs.memory_stats()
    assert service.get_events(limit=1, offset=1) == service._vs.get_events(
        limit=1, offset=1
    )


def test_episodic_and_negative_lesson_scans_apply_real_security_policy(service):
    payload = "A remembered project fact with \u202e dangerous rendering"
    assert not service.write_episodic(payload, source="consolidation")
    assert service.write_episodic(payload, source="user_explicit")
    assert not service.write_lesson(
        "Use a maintenance checklist.", negative=payload, source="consolidation"
    )
    assert service.get_lessons() == []


def test_lesson_normalization_inventory_standings_and_deletion(service, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workspace, target_is_directory=True)
    assert service.write_lesson(
        "Use amber checklist for release steps.",
        scope=MemoryScope.WORKSPACE,
        scope_ref=str(alias),
    )
    assert service.write_lesson("Prefer explicit response summaries.")
    rows = service.get_lessons()
    assert len(rows) == 2
    scoped = [
        row
        for row in service.lessons_visible_in(str(workspace))
        if row.get("scope") == "workspace"
    ]
    assert len(scoped) == 1
    assert service.lesson_standings(rows) == service._vs.lesson_standings(rows)
    assert len(service.lessons_visible_in()) == 1
    assert service.delete_lesson("amber checklist")
    assert len(service.get_lessons()) == 1


def test_intelligence_callbacks_are_first_writer_wins_without_invocation(service):
    assert service.embed("no configured embedder") is None
    assert service.contradiction_judge is None
    service.set_contradiction_judge(None)
    service.set_contradiction_judge(operator.ne)
    service.set_contradiction_judge(operator.eq)
    assert service.contradiction_judge is operator.ne


def test_disabled_service_lifecycle_is_inert_and_validates_outcomes():
    service = MemoryService.over_vector_store(None)
    service.write_working_memory("s", "summary")
    assert service.working_memory("s") == ""
    assert (
        service.seal_session("s")
        == service.promote_by_heat()
        == service.expire_by_category()
        == 0
    )
    assert (
        service.synthesize_failures()
        == service.build_daily_digest()
        == service.promote_episodic_patterns()
        == 0
    )
    assert (
        service.record_procedural(tool="rg", task_shape="find", outcome="success")
        is None
    )
    with pytest.raises(ValueError, match="unknown procedural outcome"):
        service.record_procedural(tool="", task_shape="", outcome="unknown")
    assert service.record_persona(agent="A", trait="direct") is None
    assert (
        service.record_commitment(
            agent="A",
            channel="chat",
            text="check",
            due_window="today",
            confidence=1.0,
            enabled=True,
        )
        is None
    )
    assert service.persona_block(agent="A") == service.procedural_block() == ""
    assert (
        service.procedural_priors()
        == service.due_commitments_all(now_iso="today")
        == service.daily_digests()
        == []
    )
    assert (
        not service.dismiss_commitment("x")
        and not service.write_episodic("some text")
        and not service.write_lesson("some rule")
    )
    assert (
        service.get_record("x") is None
        and service.get_semantic("x") is None
        and service.embed("x") is None
    )
    assert (
        service.get_records()
        == service.get_all_semantic()
        == service.get_events()
        == service.get_lessons()
        == service.lessons_visible_in()
        == []
    )
    assert service.memory_stats() == service.lesson_standings([]) == {}
    assert (
        not service.delete_semantic("x")
        and not service.supersede_semantic("x", "y", "user")
        and not service.delete_lesson("rule")
    )
    assert service.undo_event(1) == (False, "no vector store")
    service.put([])
    service.record_recall([])
    service.set_contradiction_judge(operator.ne)
    assert service.contradiction_judge is None


@pytest.mark.asyncio
async def test_lesson_write_emits_bounded_metadata_to_real_local_hook(
    service, tmp_path
):
    from gideon.engine.hooks import (
        ScriptHookStore,
        get_global_hook_store,
        set_global_hook_store,
    )

    output = tmp_path / "observed.json"
    script = tmp_path / "observe.py"
    script.write_text(
        "import json, os, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps({'context':os.environ.get('CONTEXT'), 'payload':json.load(sys.stdin)}))\n"
    )
    store = ScriptHookStore(tmp_path / "hooks")
    hook = store.create(
        {
            "event": "MemoryWrite",
            "provider_config": {
                "command": shlex.join([sys.executable, str(script), str(output)])
            },
        }
    )
    previous = get_global_hook_store()
    set_global_hook_store(store)
    try:
        before = asyncio.all_tasks()
        assert service.write_lesson(
            "Use the private amber checklist for local reviews.", category="process"
        )
        scheduled = asyncio.all_tasks().difference(before)
        assert scheduled
        await asyncio.gather(*scheduled)
        assert hook.last_status == "ok"
        data = json.loads(output.read_text())
        assert data["context"] == "kind=lesson key=process scope=user_explicit"
        assert "private amber checklist" not in output.read_text()
        assert data["payload"]["hook_event_name"] == "MemoryWrite"
    finally:
        set_global_hook_store(previous)
