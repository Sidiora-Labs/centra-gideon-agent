"""Tests for host-aware subagent concurrency auto-sizing (max_subagents=0)."""

from gideon.engine import subagent
from gideon.engine.subagent import (
    _AUTO_CEILING,
    _AUTO_FLOOR,
    _MAX_CONCURRENT,
    resolve_max_subagents,
)


def test_explicit_value_passes_through(monkeypatch):
    """A non-zero configured value is returned unchanged — no host probing."""
    monkeypatch.setattr(subagent.os, "cpu_count", lambda: 128)
    monkeypatch.setattr(subagent, "_total_memory_gb", lambda: 1024.0)
    assert resolve_max_subagents(3) == 3
    assert resolve_max_subagents(1) == 1
    assert resolve_max_subagents(16) == 16


def test_auto_takes_min_of_cpu_and_mem(monkeypatch):
    """auto = min(cpu-headroom, mem/per-agent), clamped to bounds."""
    monkeypatch.setattr(subagent.os, "cpu_count", lambda: 10)
    monkeypatch.setattr(subagent, "_total_memory_gb", lambda: 24.0)
    assert resolve_max_subagents(0, per_agent_gb=4.0) == 6


def test_auto_memory_is_the_binding_constraint(monkeypatch):
    """A big-CPU, small-RAM host is capped by memory, not cores."""
    monkeypatch.setattr(subagent.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(subagent, "_total_memory_gb", lambda: 8.0)
    assert resolve_max_subagents(0, per_agent_gb=4.0) == 2


def test_auto_clamps_to_ceiling(monkeypatch):
    """A huge host is capped at the ceiling (diminishing returns + OOM risk)."""
    monkeypatch.setattr(subagent.os, "cpu_count", lambda: 64)
    monkeypatch.setattr(subagent, "_total_memory_gb", lambda: 512.0)
    assert resolve_max_subagents(0, per_agent_gb=4.0) == _AUTO_CEILING


def test_auto_clamps_to_floor(monkeypatch):
    """A tiny (Pi-class) host still gets the floor so 'auto' beats single-agent."""
    monkeypatch.setattr(subagent.os, "cpu_count", lambda: 2)
    monkeypatch.setattr(subagent, "_total_memory_gb", lambda: 4.0)
    assert resolve_max_subagents(0, per_agent_gb=4.0) == _AUTO_FLOOR


def test_auto_falls_back_when_host_facts_unavailable(monkeypatch):
    """Unknown CPU or memory → the historical fixed cap, never a crash."""
    monkeypatch.setattr(subagent.os, "cpu_count", lambda: 0)
    monkeypatch.setattr(subagent, "_total_memory_gb", lambda: 0.0)
    assert resolve_max_subagents(0) == _MAX_CONCURRENT

    monkeypatch.setattr(subagent.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(subagent, "_total_memory_gb", lambda: 0.0)
    assert resolve_max_subagents(0) == _MAX_CONCURRENT


def test_total_memory_gb_never_raises(monkeypatch):
    """The detector swallows subprocess/parse errors and returns 0.0."""
    monkeypatch.setattr(subagent.sys, "platform", "sunos")
    assert subagent._total_memory_gb() == 0.0
