"""L1 memory manifest + recall-count + memory_recall tool (D-MEM-INJECT half 1)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from gideon.cognition.vector_memory import SemanticArchive


@pytest.fixture
def vs():
    store = SemanticArchive(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return store


def test_recall_count_column_migrated(vs):
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    row = vs.db.execute(
        "SELECT recall_count FROM semantic_memory WHERE key='pref.x'"
    ).fetchone()
    assert row[0] == 0


def test_record_recall_bumps_count(vs):
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    vs.record_recall(["pref.x", "pref.x", "pref.x"])
    row = vs.db.execute(
        "SELECT recall_count FROM semantic_memory WHERE key='pref.x'"
    ).fetchone()
    assert row[0] == 3


def test_record_recall_empty_is_noop(vs):
    vs.record_recall([])


def test_record_recall_ignores_unknown_keys(vs):
    vs.record_recall(["nonexistent.key"])


def test_l1_manifest_empty_when_no_facts(vs):
    assert vs.get_l1_manifest() == ""


def test_l1_manifest_ranks_by_recall_count(vs):
    vs.set_semantic("pref.color", "blue", 1.0, "user_explicit")
    vs.set_semantic("pref.lang", "python", 1.0, "user_explicit")
    vs.record_recall(["pref.lang", "pref.lang"])
    man = vs.get_l1_manifest()
    assert "pref.lang" in man and "pref.color" in man
    assert man.index("pref.lang") < man.index("pref.color")


def test_l1_manifest_excludes_lessons(vs):
    vs.set_semantic("lesson.foo", "bar", 1.0, "user_explicit")
    vs.set_semantic("pref.x", "1", 1.0, "user_explicit")
    man = vs.get_l1_manifest()
    assert "lesson.foo" not in man
    assert "pref.x" in man


def test_l1_manifest_respects_cap(vs):
    for i in range(50):
        vs.set_semantic(f"pref.k{i}", "x" * 100, 1.0, "user_explicit")
    man = vs.get_l1_manifest(cap=300)
    assert len(man) < 800


def test_l1_manifest_respects_limit(vs):
    for i in range(30):
        vs.set_semantic(f"pref.k{i}", "v", 1.0, "user_explicit")
    man = vs.get_l1_manifest(cap=100_000, limit=5)
    fact_lines = [ln for ln in man.splitlines() if ln.startswith("pref.")]
    assert len(fact_lines) <= 5


def test_get_context_uses_manifest_when_l1_on(tmp_path, monkeypatch):
    # job (L3) post-M2; the MemoryJournal is just the markdown projection.
    from gideon.cognition.memory import MemoryJournal
    from gideon.cognition.memory_service import MemoryService

    ms = MemoryJournal(workspace=tmp_path / "ws")
    ms.init()
    vs = SemanticArchive(db_path=tmp_path / "vec.db")
    vs.init()
    vs.set_semantic("pref.tone", "concise", 1.0, "user_explicit")
    ms.vector_store = vs
    svc = MemoryService(ms)

    ctx_l1 = svc.get_context(query="tone", l1_manifest=True)
    assert "Memory manifest" in ctx_l1
    assert "Semantic Memory — factual key-value pairs" not in ctx_l1

    ctx_legacy = svc.get_context(query="", l1_manifest=False)
    assert "Memory manifest" not in ctx_legacy
    assert "Semantic Memory — factual key-value pairs" in ctx_legacy
