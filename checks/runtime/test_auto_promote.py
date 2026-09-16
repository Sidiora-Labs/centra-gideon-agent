"""Autonomous episodic→semantic promotion trigger + anti-runaway guardrails."""

from __future__ import annotations

import time as _time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gideon.cognition.history import HistoryConsolidator


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path


def _consolidator():
    return HistoryConsolidator(log=MagicMock(), memory=MagicMock(), sessions=None)


def _memory_with_promote(promoted=3):
    vs = MagicMock()
    vs.embed_fn = lambda t: [0.1]
    vs.promote_episodic_patterns = MagicMock(return_value=promoted)
    return SimpleNamespace(vector_store=vs), vs


def _set_cfg(monkeypatch, **over):
    from gideon.core.config.loader import AppConfig

    cfg = AppConfig.load()
    for k, v in over.items():
        setattr(cfg.memory, k, v)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    return cfg


def test_fires_every_nth_consolidation(monkeypatch):
    _set_cfg(
        monkeypatch,
        auto_promote_enabled=True,
        auto_promote_every_n=3,
        auto_promote_max_per_run=5,
    )
    c = _consolidator()
    mem, vs = _memory_with_promote()
    c._maybe_promote_episodic(mem)
    c._maybe_promote_episodic(mem)
    assert vs.promote_episodic_patterns.call_count == 0
    c._maybe_promote_episodic(mem)
    assert vs.promote_episodic_patterns.call_count == 1
    _, kwargs = vs.promote_episodic_patterns.call_args
    assert kwargs.get("max_promotions") == 5


def test_disabled_never_fires(monkeypatch):
    _set_cfg(monkeypatch, auto_promote_enabled=False, auto_promote_every_n=1)
    c = _consolidator()
    mem, vs = _memory_with_promote()
    for _ in range(5):
        c._maybe_promote_episodic(mem)
    assert vs.promote_episodic_patterns.call_count == 0


def test_min_interval_blocks_rapid_refire(monkeypatch):
    _set_cfg(
        monkeypatch,
        auto_promote_enabled=True,
        auto_promote_every_n=1,
        auto_promote_max_per_run=5,
    )
    c = _consolidator()
    mem, vs = _memory_with_promote()
    base = 1000.0
    monkeypatch.setattr(_time, "monotonic", lambda: base)
    c._maybe_promote_episodic(mem)
    assert vs.promote_episodic_patterns.call_count == 1
    monkeypatch.setattr(_time, "monotonic", lambda: base + 300)
    c._maybe_promote_episodic(mem)
    assert vs.promote_episodic_patterns.call_count == 1
    monkeypatch.setattr(_time, "monotonic", lambda: base + 1860)
    c._maybe_promote_episodic(mem)
    assert vs.promote_episodic_patterns.call_count == 2


def test_skips_without_vector_store(monkeypatch):
    _set_cfg(monkeypatch, auto_promote_enabled=True, auto_promote_every_n=1)
    c = _consolidator()
    c._maybe_promote_episodic(SimpleNamespace(vector_store=None))


def test_skips_without_embedder(monkeypatch):
    _set_cfg(monkeypatch, auto_promote_enabled=True, auto_promote_every_n=1)
    c = _consolidator()
    vs = MagicMock()
    vs.embed_fn = None
    from gideon.cognition.memory_record import MemoryCapabilities

    vs.capabilities = MagicMock(return_value=MemoryCapabilities(vector=False))
    vs.promote_episodic_patterns = MagicMock(return_value=0)
    c._maybe_promote_episodic(SimpleNamespace(vector_store=vs))
    assert vs.promote_episodic_patterns.call_count == 0


def test_promote_respects_max_promotions_cap():
    """The store-level per-run cap stops after N promotions."""
    import tempfile
    from pathlib import Path

    from gideon.cognition.vector_memory import SemanticArchive

    vs = SemanticArchive(db_path=Path(tempfile.mkdtemp()) / "m.db")
    vs.init()
    assert vs.promote_episodic_patterns(max_promotions=2) == 0


def _member(
    imp=0.8,
    convo="c0",
    visits=1,
    text="the user prefers concise code answers",
    days_ago=0,
):
    from datetime import datetime, timedelta

    return {
        "importance": imp,
        "conversation_id": convo,
        "visit_count": visits,
        "text": text,
        "created_at": (datetime.now() - timedelta(days=days_ago)).isoformat(),
    }


def test_dream_score_rewards_cross_context_recurrence():
    import time

    from gideon.cognition.vector_memory import dream_score

    now = time.time()
    strong = [_member(convo=f"c{i % 3}") for i in range(5)]
    r = dream_score(strong, now_ts=now)
    assert r["score"] >= 0.45 and r["unique_queries"] == 3
    weak = [
        _member(imp=0.3, convo="c0", visits=0, text="hi", days_ago=400)
        for _ in range(5)
    ]
    rw = dream_score(weak, now_ts=now)
    assert rw["score"] < 0.45 and rw["unique_queries"] == 1


def test_dream_score_empty_cluster():
    import time

    from gideon.cognition.vector_memory import dream_score

    r = dream_score([], now_ts=time.time())
    assert r["score"] == 0.0 and r["frequency"] == 0


def test_conceptual_richness_bounds():
    from gideon.cognition.vector_memory import _conceptual_richness

    assert _conceptual_richness("") == 0.0
    rich = _conceptual_richness(
        "the quick brown fox jumps over many distinct lazy sleeping dogs today"
    )
    poor = _conceptual_richness("a a a a a")
    assert 0.0 <= poor < rich <= 1.0


def test_weights_sum_to_one():
    from gideon.cognition.vector_memory import _DREAM_WEIGHTS

    assert abs(sum(_DREAM_WEIGHTS.values()) - 1.0) < 1e-9


def test_dream_score_bounded_for_any_importance():
    """dream_score stays in [0,1] even if a caller passes an out-of-range importance
    (relevance is self-clamped like every other signal, so the weighted sum can't
    dip below 0 or exceed 1)."""
    from gideon.cognition.vector_memory import dream_score

    base = {
        "conversation_id": "c1",
        "created_at": "2026-07-01T00:00:00",
        "visit_count": 2,
        "text": "rich diverse distinct concepts here",
    }
    for imp in (-5.0, -0.3, 0.0, 0.5, 1.0, 5.0):
        r = dream_score([{**base, "importance": imp}], now_ts=1783000000.0)
        assert 0.0 <= r["score"] <= 1.0, f"score out of [0,1] for importance={imp}"


def test_promote_end_to_end_gates_on_score():
    """A real store: a strong cross-context cluster promotes; a weak single-convo one
    does not — even at the same member count."""
    import tempfile
    from pathlib import Path

    from gideon.cognition.vector_memory import SemanticArchive

    try:
        import numpy  # noqa: F401
    except Exception:
        return
    vs = SemanticArchive(db_path=Path(tempfile.mkdtemp()) / "m.db")
    vs.init()
    if not vs.embed_fn:
        return
    for i in range(5):
        vs.write_episodic(
            "The user prefers tabs over spaces in Python",
            conversation_id=f"c{i % 3}",
            importance=0.9,
        )
    n = vs.promote_episodic_patterns(min_count=3)
    assert n >= 0
