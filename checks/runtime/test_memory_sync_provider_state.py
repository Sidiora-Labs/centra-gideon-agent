"""Provider persistence and catalog state exercised with real journals and SQLite."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Barrier

import pytest

from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_record import MemoryKind, MemoryRecord
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.sqlite_compat import sqlite3
from gideon.integrations.memory_providers import registry as memory_registry
from gideon.integrations.memory_providers.base import MemoryProvider
from gideon.integrations.memory_providers.filesystem import FilesystemMemoryProvider
from gideon.integrations.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)


@pytest.fixture
def filesystem(tmp_path):
    journal = MemoryJournal(workspace=tmp_path)
    provider = FilesystemMemoryProvider(journal)
    provider.init()
    return provider, journal


def test_filesystem_routes_each_memory_kind_to_its_persistent_page(filesystem):
    provider, journal = filesystem
    records = [
        MemoryRecord(id=kind.value, kind=kind, text=f"entry-{kind.value}-end")
        for kind in MemoryKind
    ]
    provider.put(records)
    preferences = journal.read_preferences()
    history = journal.read_history()
    for record in records:
        durable = record.kind in {
            MemoryKind.PREFERENCE,
            MemoryKind.SEMANTIC,
            MemoryKind.NOTE,
        }
        assert (record.text in preferences) is durable
        assert (record.text in history) is not durable
    restored = FilesystemMemoryProvider(
        MemoryJournal(workspace=journal._daily.parent.parent)
    )
    assert restored.query(kinds={"preference"})[0].text == preferences.strip()


def test_filesystem_value_projection_and_empty_record_behavior(filesystem):
    provider, journal = filesystem
    value = {"editor": "vim", "unicode": "é", "enabled": False}
    record = MemoryRecord(id="structured", kind=MemoryKind.PREFERENCE, value=value)
    record.text = ""
    provider.put(
        [
            record,
            MemoryRecord(id="none", kind=MemoryKind.NOTE),
            MemoryRecord(id="empty", kind=MemoryKind.PREFERENCE, value=""),
            MemoryRecord(
                id="override", kind=MemoryKind.SEMANTIC, text="use this", value="unused"
            ),
        ]
    )
    contents = journal.read_preferences()
    assert json.dumps(value) in contents
    assert "- null\n" in contents
    assert "- \n" not in contents
    assert "use this" in contents and "unused" not in contents


def test_filesystem_batch_failure_keeps_prior_successful_writes(filesystem):
    provider, journal = filesystem
    invalid = MemoryRecord(id="bad", kind=MemoryKind.NOTE)
    invalid.value = {"unserializable": {1, 2}}
    with pytest.raises(TypeError):
        provider.put(
            [
                MemoryRecord(id="first", kind=MemoryKind.NOTE, text="committed first"),
                invalid,
                MemoryRecord(id="last", kind=MemoryKind.NOTE, text="never written"),
            ]
        )
    assert "committed first" in journal.read_preferences()
    assert "never written" not in journal.read_preferences()


def test_filesystem_query_preserves_document_order_filters_and_slice_limits(filesystem):
    provider, journal = filesystem
    journal.write_preferences("  preference document  \n")
    journal.write_projects("project document")
    results = provider.query(
        scope="session", scope_ref="elsewhere", include_deleted=True
    )
    assert [(record.id, record.kind) for record in results] == [
        ("preferences", MemoryKind.PREFERENCE),
        ("projects", MemoryKind.NOTE),
    ]
    assert results[0].text == "preference document"
    assert [record.id for record in provider.query(limit=1)] == ["preferences"]
    assert provider.query(limit=0) == []
    assert [record.id for record in provider.query(limit=-1)] == ["preferences"]
    assert [record.id for record in provider.query(kinds={"note"})] == ["projects"]
    assert provider.query(kinds=set()) == []
    journal.write_preferences(" \n")
    assert [record.id for record in provider.query()] == ["projects"]


def test_filesystem_keyword_search_preserves_real_fts_scores_and_limits(filesystem):
    provider, journal = filesystem
    journal.write_preferences("Keystone journal preferences")
    journal.write_projects("Keystone project tracker")
    matches = provider.vector_query(text="keystone", kinds={"episodic"}, k=2)
    assert len(matches) == 2
    assert {match["source"] for match in matches} == {"fts"}
    assert all(match["score"] > 0 for match in matches)
    assert all("keystone" in match["text"].lower() for match in matches)
    assert len(provider.vector_query(text="keystone", k=1)) == 1
    assert provider.vector_query(embedding=[1.0, 0.0], text="") == []
    assert provider.vector_query(text="no_such_journal_keyword") == []


def test_filesystem_unsupported_operations_and_optional_hooks_are_inert(filesystem):
    provider, journal = filesystem
    before = journal.read()
    record = MemoryRecord(id="hook", kind=MemoryKind.NOTE, text="not persisted by hook")
    assert provider.get("preferences") is None
    assert provider.delete("preferences", source="test") is False
    assert provider.embed("text") is None
    assert provider.append_event(event_type="test") == 0
    assert provider.read_events(limit=1, offset=10) == []
    assert provider.on_turn_start("session") is None
    assert provider.on_pre_compress("session") is None
    assert provider.on_memory_write(record) is None
    assert provider.on_delegation("agent") is None
    assert provider.close() is None
    assert journal.read() == before
    first, second = provider.capabilities(), provider.capabilities()
    with pytest.raises(FrozenInstanceError):
        first.vector = True
    assert second.vector is False
    assert second.full_text_search is True
    assert second.transactional_batch is False
    assert second.event_log is False


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(memory_registry, "_providers", {})
    try:
        yield tmp_path
    finally:
        native = memory_registry.get_provider("native")
        if isinstance(native, SemanticArchive):
            native.close()


def test_memory_catalog_replacement_keeps_order_and_listing_is_detached(catalog):
    first = FilesystemMemoryProvider(MemoryJournal(workspace=catalog / "first"))
    second = FilesystemMemoryProvider(MemoryJournal(workspace=catalog / "second"))
    memory_registry.register_provider("first", first)
    memory_registry.register_provider("second", second)
    snapshot = memory_registry.list_providers()
    memory_registry.register_provider("first", second)
    assert memory_registry.list_providers() == ["first", "second"]
    assert memory_registry.get_provider("first") is second
    memory_registry.unregister_provider("second")
    memory_registry.unregister_provider("missing")
    assert memory_registry.get_provider("second") is None
    assert memory_registry.list_providers() == ["first"]
    assert snapshot == ["first", "second"]


def test_explicit_native_registration_suppresses_lazy_creation(catalog):
    replacement = FilesystemMemoryProvider(MemoryJournal(workspace=catalog))
    memory_registry.register_provider("native", replacement)
    assert memory_registry.get_default_provider() is replacement
    assert (
        memory_registry.create_default_provider(config={"unused": True}) is replacement
    )
    memory_registry.register_provider("native", None)
    assert memory_registry.get_default_provider() is None
    assert not (catalog / "memory.db").exists()


def test_concurrent_native_requests_share_one_initialized_sqlite_store(catalog):
    count = 6
    ready = Barrier(count)

    def resolve(_):
        ready.wait(timeout=5)
        return memory_registry.get_default_provider()

    with ThreadPoolExecutor(max_workers=count) as workers:
        resolved = list(workers.map(resolve, range(count)))
    provider = resolved[0]
    assert all(item is provider for item in resolved)
    assert isinstance(provider, SemanticArchive)
    assert provider.capabilities().vector is False
    assert (catalog / "memory.db").is_file()
    event_id = provider.append_event(
        event_type="create",
        memory_type="semantic",
        memory_key="pref.registry",
        old_value=None,
        new_value="ready",
        source="test",
    )
    assert event_id >= 1
    assert provider.read_events(limit=1)[0]["memory_key"] == "pref.registry"
    assert memory_registry.create_default_provider() is provider


def test_failed_native_initialization_does_not_publish_a_partial_provider(catalog):
    blocked_path = catalog / "memory.db"
    blocked_path.mkdir()
    with pytest.raises(sqlite3.OperationalError):
        memory_registry.get_default_provider()
    assert memory_registry.list_providers() == []
    blocked_path.rmdir()
    provider = memory_registry.get_default_provider()
    assert isinstance(provider, SemanticArchive)
    assert provider.db.execute("SELECT 1").fetchone()[0] == 1


def test_parallel_explicit_registrations_are_not_lost(catalog):
    providers = {
        f"journal-{index}": FilesystemMemoryProvider(
            MemoryJournal(workspace=catalog / str(index))
        )
        for index in range(12)
    }
    with ThreadPoolExecutor(max_workers=6) as workers:
        list(
            workers.map(
                lambda item: memory_registry.register_provider(*item), providers.items()
            )
        )
    assert set(memory_registry.list_providers()) == set(providers)
    assert all(
        memory_registry.get_provider(name) is provider
        for name, provider in providers.items()
    )


def test_sync_wire_records_keep_payloads_defaults_and_independent_metadata():
    payload = b"\x00\xff\n"
    key = "machines/node/seq-0001/tasks.jsonl"
    assert asdict(SyncObject(key, payload)) == {"key": key, "data": payload}
    assert asdict(RemoteRef(key)) == {"key": key, "size": 0, "fingerprint": ""}
    assert asdict(PushResult()) == {
        "pushed": 0,
        "skipped": 0,
        "outcome": "delivered",
        "detail": "",
    }
    first, second = ConnectionResult(), ConnectionResult()
    first.extra["remote"] = "one"
    assert asdict(second) == {"ok": False, "detail": "", "extra": {}}


def test_provider_contracts_remain_abstract():
    with pytest.raises(TypeError):
        MemoryProvider()
    with pytest.raises(TypeError):
        SyncTransportProvider()
