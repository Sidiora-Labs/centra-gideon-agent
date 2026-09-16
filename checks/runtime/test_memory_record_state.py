import json
import math
import sqlite3
import struct
from datetime import datetime, timedelta, timezone

import pytest

from gideon.cognition import memory_holder as holders
from gideon.cognition import memory_slots as slots
from gideon.cognition.learning.decay import strength
from gideon.cognition.memory_graph import MemoryGraph
from gideon.cognition.memory_record import (
    MemoryCapabilities,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryTier,
    _kind_from_key,
    _pack_portable,
    _row_get,
    _unpack_portable,
    blob_to_embedding,
    decay_profile,
    embedding_to_blob,
)
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig


@pytest.fixture
def record_store(tmp_path, monkeypatch):
    home = tmp_path / "record-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    config = AppConfig.load()
    config.memory.graph_enabled = False
    config.save()
    store = SemanticArchive(db_path=home / "memory.db", embedding_dim=3)
    store.init()
    yield store
    store.close()


@pytest.mark.parametrize("vector", ([3.0, 4.0], [0.0, 0.0], [], [-2.0, 1.0, 5.0]))
def test_wire_encoding_and_portable_codec_on_real_numeric_vectors(vector):
    blob = embedding_to_blob(vector)
    portable = _pack_portable(vector)
    assert isinstance(blob, bytes) and len(blob) == 4 * len(vector)
    assert len(portable) == len(blob)
    if vector:
        assert blob_to_embedding(blob) == pytest.approx(
            _unpack_portable(portable), abs=1e-6
        )
        squared = sum(value * value for value in blob_to_embedding(blob))
        assert squared == pytest.approx(1.0 if any(vector) else 0.0, abs=1e-6)
    else:
        assert blob_to_embedding(blob) is None and _unpack_portable(portable) == []
    with pytest.raises(struct.error):
        _unpack_portable(b"bad")


def test_legacy_sqlite_rows_normalize_missing_axes_and_zero_values():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE old_semantic (key TEXT, value_json TEXT, confidence REAL, created_at TEXT)"
        )
        db.execute(
            "INSERT INTO old_semantic VALUES (?, ?, ?, ?)",
            (
                "pref.editor",
                json.dumps({"value": "vim"}),
                0,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        row = db.execute("SELECT * FROM old_semantic").fetchone()
        record = MemoryRecord.from_semantic_row(row)
        assert record.value == {"value": "vim"}
        assert record.text == '{"value": "vim"}'
        assert record.confidence == 0.5
        assert record.tier is MemoryTier.SEMANTIC and record.scope is MemoryScope.GLOBAL
        assert record.updated_at == record.created_at
        assert _row_get(row, "absent", 17) == 17
        assert _row_get(None, "absent", 18) == 18
        db.execute(
            "CREATE TABLE old_episodic (id TEXT, text TEXT, tags TEXT, importance REAL, created_at TEXT, last_accessed_at TEXT)"
        )
        db.execute(
            "INSERT INTO old_episodic VALUES ('event', 'a note', ?, 0, '2026-01-01', '2026-01-02')",
            ('{"legacy": true}',),
        )
        event = MemoryRecord.from_episodic_row(
            db.execute("SELECT * FROM old_episodic").fetchone()
        )
        assert event.tags == {"legacy": True}
        assert event.importance == 0.5
        assert event.updated_at == "2026-01-02" and event.tier is MemoryTier.EPISODIC


def test_row_decoding_preserves_malformed_and_required_column_behavior():
    record = MemoryRecord.from_semantic_row(
        {"key": "lesson.a", "value_json": "not-json"}
    )
    assert record.value == record.text == "not-json"
    null = MemoryRecord.from_semantic_row({"key": "pref.null", "value_json": None})
    assert null.value is None and null.text == "null"
    event = MemoryRecord.from_episodic_row({"id": "event", "tags": "broken-json"})
    assert event.tags == []
    with pytest.raises(KeyError):
        MemoryRecord.from_semantic_row({"key": "missing-value"})
    with pytest.raises(ValueError):
        MemoryRecord("bad", "not-a-kind")
    with pytest.raises(ValueError, match="no decay profile"):
        decay_profile("not-a-kind")


@pytest.mark.parametrize("kind", list(MemoryKind))
def test_record_heat_uses_shared_decay_and_combined_usage(kind):
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    stamp = (now - timedelta(days=30)).isoformat()
    record = MemoryRecord(
        "owned",
        kind,
        recall_count=4,
        visit_count=5,
        importance=0.7,
        created_at=stamp,
        updated_at=stamp,
    )
    expected = 0.7 * math.log1p(9) / math.log(10) + 0.5 * strength(
        kind=decay_profile(kind), active_days_since_use=30, importance=0.7
    )
    assert record.heat(now=now) == pytest.approx(expected, abs=1e-12)
    assert record.idle_days(now=now) == 30
    record.last_accessed_at = (now + timedelta(days=1)).isoformat()
    assert record.idle_days(now=now) == 0
    record.last_accessed_at = "invalid"
    assert record.idle_days(now=now) is None
    assert record.heat(now=now) == pytest.approx(0.7)
    record.last_accessed_at = ""
    record.created_at = record.updated_at = ""
    assert record.idle_days(now=now) is None


@pytest.mark.parametrize(
    "prefix,kind",
    [
        ("lesson.", MemoryKind.LESSON),
        ("user.procedural.", MemoryKind.PROCEDURAL),
        ("user.persona.", MemoryKind.SELF_PERSONA),
        ("user.commitment.", MemoryKind.COMMITMENT),
        ("user.approval.", MemoryKind.APPROVAL),
        ("slot.", MemoryKind.SLOT),
        ("claim.", MemoryKind.SEMANTIC),
    ],
)
def test_reserved_prefixes_and_public_record_field_order(prefix, kind):
    assert _kind_from_key(prefix + "owned") is kind
    record = MemoryRecord(
        prefix + "owned",
        kind,
        value="value",
        scope="agent",
        scope_ref="operator",
        embedding=[1.0, 0.0],
        extra={"hidden": True},
    )
    data = record.to_public_dict()
    assert list(data) == [
        "id",
        "kind",
        "text",
        "value",
        "importance",
        "confidence",
        "source",
        "recall_count",
        "tier",
        "scope",
        "category",
        "superseded_by",
        "conversation_id",
        "tags",
        "is_deleted",
        "created_at",
        "updated_at",
    ]
    assert data["value"] == data["text"] == "value"
    assert data["kind"] == kind.value and data["scope"] == "agent"
    assert not {"embedding", "extra", "scope_ref"} & data.keys()
    assert list(MemoryCapabilities().to_dict()) == [
        "vector",
        "transactional_batch",
        "event_log",
        "full_text_search",
        "entity_graph",
    ]


def test_slot_trim_reports_minimum_oldest_lines_without_writing(record_store):
    slots.append(record_store, "persona", "a" * 120)
    slots.append(record_store, "persona", "b" * 120)
    before = record_store.get_semantic("slot.persona")["value_json"]
    with pytest.raises(slots.SlotCapExceeded) as raised:
        slots.append(record_store, "persona", "c" * 200)
    proposal = raised.value.proposal
    assert proposal.current_chars == 241 and proposal.incoming_chars == 201
    assert proposal.over_by == 42
    assert proposal.drop_candidates == ["a" * 120]
    assert proposal.to_dict()["message"] == str(raised.value)
    assert record_store.get_semantic("slot.persona")["value_json"] == before


def test_human_tombstone_and_reinforcement_use_actual_wal(record_store):
    slots.append(record_store, "self_notes", "Use explicit units.")
    first = slots.load(record_store, "self_notes")[0]
    reinforced = slots.append(
        record_store, "self_notes", "Use explicit units.", reinforce=True
    )
    assert (
        reinforced[0].reinforcements == 2 and reinforced[0].added_at == first.added_at
    )
    assert slots.tombstone(record_store, "self_notes", "Use explicit units.") is True
    before = record_store.db.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]
    returned = slots.append(
        record_store, "self_notes", "Use explicit units.", reinforce=True
    )
    assert returned[0].tombstoned_by == "human" and slots.live_lines(returned) == []
    assert (
        record_store.db.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]
        == before
    )
    assert slots.render_slots_block(record_store) == ""
    assert slots.tombstone(record_store, "self_notes", "not present") is False


def test_new_slot_write_surfaces_real_owner_conflict(record_store):
    slots.append(record_store, "preferences", "Use metric units.")
    before = record_store.get_semantic("slot.preferences")["value_json"]
    with pytest.raises(slots.SlotCapExceeded) as raised:
        slots.append(
            record_store, "preferences", "Use decimal labels.", source="consolidation"
        )
    assert "store rejected the write" in raised.value.proposal.drop_candidates[0]
    assert "Existing entry set by user" in raised.value.proposal.drop_candidates[0]
    assert record_store.get_semantic("slot.preferences")["value_json"] == before


def test_slot_legacy_values_and_ordered_projection_stay_bounded(record_store):
    assert slots.parse_lines(None) == []
    assert [line.text for line in slots.parse_lines("  legacy text  ")] == [
        "legacy text"
    ]
    assert [line.text for line in slots.parse_lines({"lines": ["keep", " ", 8]})] == [
        "keep",
        "8",
    ]
    assert (
        slots.SlotLine.from_dict({"text": "zero", "reinforcements": 0}).reinforcements
        == 1
    )
    with pytest.raises(ValueError):
        slots.SlotLine.from_dict({"reinforcements": "bad"})
    for name, text in (
        ("self_notes", "n" * 300),
        ("persona", "p" * 300),
        ("preferences", "f" * 300),
    ):
        slots.append(record_store, name, text)
    block = slots.render_slots_block(
        record_store, names=["self_notes", "persona"], limit=250
    )
    assert block.startswith("[MEMORY SLOTS]\nSelf notes:")
    assert len(block) == 250 and block.endswith("… [slots truncated]")
    assert slots.render_slots_block(record_store, limit=0) == "\n… [slots truncated]"
    config = AppConfig.load()
    config.memory.slot_size_cap = 900
    config.save()
    assert slots.resolve_block_limit(AppConfig.load().memory.slot_size_cap) == 900
    assert slots.resolve_block_limit(None) == slots.SLOTS_BLOCK_MAX_CHARS
    assert slots.resolve_block_limit(-1) == slots.SLOTS_BLOCK_MIN_CHARS
    with pytest.raises(OverflowError):
        slots.resolve_block_limit(float("inf"))


@pytest.mark.parametrize(
    "raw,normalized,ceiling,authority",
    [
        (None, "", 1.0, 2),
        ("wrong", "", 1.0, 2),
        (" USER ", "user", 0.75, 3),
        ("Assistant", "assistant", 0.75, 2),
        (" EXTERNAL ", "external", 0.55, 1),
        (" Person: ENT_A ", "person:ent_a", 0.75, 1),
        ("person:  ", "", 1.0, 2),
    ],
)
def test_holder_normalization_precedence_and_nonfinite_weight(
    raw, normalized, ceiling, authority
):
    assert holders.normalize_holder(raw) == normalized
    assert holders.weight_cap(raw) == ceiling
    assert holders.precedence(raw) == authority
    for weight in (None, "invalid", float("nan"), float("inf"), float("-inf"), 20):
        assert holders.normalize_weight(raw, weight) == ceiling
    assert holders.normalize_weight(raw, -3) == 0
    assert holders.normalize_weight(raw, 0.42) == 0.4
    assert holders.render_fact_line(
        "claim.x", "value", holder=raw, weight=0.42
    ).startswith("claim.x: value")


def test_holder_entity_projection_uses_real_graph_and_closed_store_fallback(
    record_store,
):
    graph = MemoryGraph(record_store.db)
    alice = graph.upsert_entity("Alice", "person", entity_id="ent_alice")
    holder = "person:" + alice
    assert holders.entity_names_for(
        [holder, "user", "person:missing", holder], graph
    ) == {holder: "Alice"}
    assert (
        holders.render_fact_line(
            "claim.launch", "Friday", holder=holder, weight=0.8, entity_name="Alice"
        )
        == "claim.launch: Friday [Alice believes, weight 0.75]"
    )
    assert holders.render_fact_line("pref.editor", "vim") == "pref.editor: vim"
    assert holders.attribution("user") == "you say"
    assert holders.attribution("assistant") == "I concluded"
    assert holders.attribution("external") == "reported externally"
    assert holders.attribution("person:missing") == "missing believes"
    record_store.close()
    assert holders.entity_names_for([holder], graph) == {}


def test_actual_store_retains_holder_when_unsupplied_on_later_write(record_store):
    assert (
        record_store.set_semantic(
            "claim.schedule",
            "Friday",
            0.9,
            "user_explicit",
            holder="external",
            weight=0.99,
        )
        is None
    )
    first = record_store.get_semantic("claim.schedule")
    assert first["holder"] == "external" and first["weight"] == 0.55
    assert (
        record_store.set_semantic("claim.schedule", "Saturday", 0.9, "user_explicit")
        is None
    )
    later = record_store.get_semantic("claim.schedule")
    assert later["holder"] == first["holder"] and later["weight"] == first["weight"]
