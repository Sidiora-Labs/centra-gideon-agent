"""WIN-1 — the POSIX-only ``resource`` import is guarded so gateway boot never crashes.

The bug (windows-native-audit ``WIN-1``): ``gateway.py`` did a bare ``import resource`` at boot.
``resource`` is POSIX-only, so a native-Windows process ``ImportError``ed before it could serve.
The fix moves the import behind :mod:`gideon.core.resource_limits`, mirroring the guard already
at ``_spawn_exec_shim.py``, and routes the facility's two consumers through it:

* the gateway's own ``RLIMIT_NOFILE`` raise at boot (``raise_fd_limit``), and
* the ``doctor`` resource-limits availability row (``resource_limits_available``).

These tests prove the DEGRADED branch actually runs (not merely that the import is wrapped):
with ``resource`` simulated-absent, both consumers degrade to the documented no-limit behaviour
and nothing raises; on a POSIX host the raise is byte-identical to the previous inline code.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.core import resource_limits as rl
from gideon.integrations.local_models import fit, residency
from gideon.operations.resilience import doctor
from gideon.operations.resilience.doctor import DoctorContext, Tier
from gideon.security import sandbox

PROBE_ID = "sandbox.resource_limits"


class _FakeResource:
    """A stand-in for the stdlib ``resource`` module that records ``setrlimit`` calls.

    Lets the POSIX-behaviour tests run identically on any host (some CI already carries a
    high soft cap) and lets us assert the exact ``(soft, hard)`` target the helper computes.
    """

    RLIMIT_NOFILE = 7

    def __init__(self, soft: int, hard: int):
        self._soft = soft
        self._hard = hard
        self.set_calls: list[tuple[int, tuple[int, int]]] = []

    def getrlimit(self, which):
        assert which == self.RLIMIT_NOFILE
        return (self._soft, self._hard)

    def setrlimit(self, which, pair):
        assert which == self.RLIMIT_NOFILE
        self.set_calls.append((which, pair))
        self._soft, self._hard = pair


def test_resource_limits_available_is_true_on_this_posix_host():
    """This suite runs on macOS/Linux, so the facility is genuinely present — the positive
    direction, so ``False`` below means the absence path, not a broken import."""
    assert rl.resource_limits_available() is True


def test_raise_fd_limit_degrades_to_noop_when_resource_absent(monkeypatch):
    """With ``resource`` unavailable (Windows), the helper applies NO limit and returns the
    documented degraded result — it must not raise. Uses the ``_resource = None`` technique the
    spawn-shim degradation test already uses."""
    monkeypatch.setattr(rl, "_resource", None)
    assert rl.resource_limits_available() is False
    res = rl.raise_fd_limit()
    assert res.available is False
    assert res.raised is False
    assert res.soft is None and res.target is None


def test_import_guard_survives_an_absent_resource_module():
    """Exercise the real ``except ImportError`` branch (the Windows condition) by reimporting
    the module with ``resource`` blocked in ``sys.modules`` — the technique test_knowledge.py
    uses for pysqlite3. Restores the import system to exactly its prior state afterward so the
    module object other test modules hold a binding to is unchanged."""
    saved_resource = sys.modules.get("resource")
    saved_self = sys.modules.get("gideon.core.resource_limits")
    sys.modules.pop("gideon.core.resource_limits", None)
    sys.modules["resource"] = None  # type: ignore[assignment]  # makes `import resource` raise
    try:
        reloaded = importlib.import_module("gideon.core.resource_limits")
        assert reloaded.resource_limits_available() is False
        degraded = reloaded.raise_fd_limit()
        assert degraded.available is False and degraded.raised is False
    finally:
        if saved_resource is not None:
            sys.modules["resource"] = saved_resource
        else:
            sys.modules.pop("resource", None)
        sys.modules.pop("gideon.core.resource_limits", None)
        if saved_self is not None:
            sys.modules["gideon.core.resource_limits"] = saved_self


def test_raise_fd_limit_raises_soft_toward_target_when_below(monkeypatch):
    """The classic macOS case: soft 256 < target, hard high enough — raise to the target."""
    fake = _FakeResource(soft=256, hard=1_000_000)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()
    assert res.available is True and res.raised is True
    assert res.soft == 256 and res.target == rl.DEFAULT_FD_TARGET
    assert fake.set_calls == [(fake.RLIMIT_NOFILE, (rl.DEFAULT_FD_TARGET, 1_000_000))]


def test_raise_fd_limit_never_exceeds_the_inherited_hard_cap(monkeypatch):
    """An unprivileged process cannot raise the hard cap; the soft target is clamped to it."""
    fake = _FakeResource(soft=256, hard=1024)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()
    assert res.raised is True and res.target == 1024
    assert fake.set_calls == [(fake.RLIMIT_NOFILE, (1024, 1024))]


def test_raise_fd_limit_leaves_an_already_high_soft_alone(monkeypatch):
    """When the soft cap already meets/exceeds the target, nothing is set."""
    fake = _FakeResource(soft=rl.DEFAULT_FD_TARGET, hard=rl.DEFAULT_FD_TARGET)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()
    assert res.available is True and res.raised is False
    assert fake.set_calls == []


def test_raise_fd_limit_swallows_a_setrlimit_failure(monkeypatch):
    """A platform that has ``resource`` but refuses the ``setrlimit`` still boots — the helper
    never propagates the OSError (the ``except Exception: pass`` the inline code had).
    """
    fake = _FakeResource(soft=256, hard=1_000_000)

    def _boom(_which, _pair):
        raise OSError("not permitted")

    monkeypatch.setattr(fake, "setrlimit", _boom)
    monkeypatch.setattr(rl, "_resource", fake)
    res = rl.raise_fd_limit()
    assert res.available is True and res.raised is False


def test_the_resource_limits_probe_is_registered_at_the_CAPABILITY_tier():
    """Registered, not merely defined — a host without ``resource`` is not a broken gateway,
    so the row is tier-3 and never gates the core."""
    ids = {p.id: p for p in doctor.all_probes()}
    assert (
        doctor.all_probes()
    ), "the probe registry is empty — the lookup proves nothing"
    assert "sandbox.resource_limits_that_does_not_exist" not in ids
    assert PROBE_ID in ids, "the probe must be registered, not just defined"
    probe = ids[PROBE_ID]
    assert probe.tier is Tier.CAPABILITY
    assert probe.capability == "sandbox"
    assert probe.title


@pytest.mark.asyncio
async def test_doctor_row_reports_available_on_a_posix_host():
    probe = {p.id: p for p in doctor.all_probes()}[PROBE_ID]
    res = await probe.run(DoctorContext())
    assert res.ok is True
    assert res.evidence["available"] is True
    assert "available" in res.detail.lower()


@pytest.mark.asyncio
async def test_doctor_row_degrades_loudly_when_resource_absent(monkeypatch):
    """The doctor consumes the SAME guarded helper; when it reports unavailable the row stays
    ok=True (no permanent red on Windows) but names the consequence loudly rather than hiding
    it behind a green."""
    monkeypatch.setattr(rl, "_resource", None)
    probe = {p.id: p for p in doctor.all_probes()}[PROBE_ID]
    res = await probe.run(DoctorContext())
    assert res.ok is True
    assert res.evidence["available"] is False
    assert "not available" in res.detail.lower()


def test_gateway_boot_has_no_unguarded_import_resource():
    """The exact WIN-1 deliverable: ``gateway.py`` must not import ``resource`` directly —
    that bare import at boot was the crash. An AST scan (not a substring grep) so a
    re-introduced ``import resource`` / ``import resource as x`` reds, and it fails on the
    pre-fix tree for the right reason."""
    engine = Path(__file__).resolve().parents[2] / "runtime/gideon/engine"
    for name in ("gateway.py", "lifecycle.py"):
        tree = ast.parse((engine / name).read_text(encoding="utf-8"))
        direct = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "resource"
        ]
        assert not direct, f"{name} imports POSIX-only resource directly: {direct}"

    lifecycle = ast.parse((engine / "lifecycle.py").read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "raise_fd_limit"
        for node in ast.walk(lifecycle)
    ), "Runtime startup no longer calls the guarded raise_fd_limit helper"


def test_pdf_reader_rejects_input_above_parser_byte_limit(tmp_path):
    from gideon.cognition.knowledge.readers import FileReader, PdfResourceLimits

    path = tmp_path / "oversize.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"x" * 64)
    text, metadata = FileReader(pdf_limits=PdfResourceLimits(max_file_bytes=16)).read(
        str(path)
    )

    assert "file bytes limit exceeded" in text
    assert metadata["format"] == "error"
    assert metadata["error_kind"] == "resource_limit"
    assert metadata["resource"] == "file bytes"
    assert metadata["actual"] == path.stat().st_size
    assert metadata["limit"] == 16


def test_pdf_reader_rejects_document_above_page_limit(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    from gideon.cognition.knowledge.readers import FileReader, PdfResourceLimits

    path = tmp_path / "pages.pdf"
    canvas = Canvas(str(path))
    for page in range(2):
        canvas.drawString(20, 20, f"Page {page + 1}")
        canvas.showPage()
    canvas.save()

    _, metadata = FileReader(pdf_limits=PdfResourceLimits(max_pages=1)).read(str(path))

    assert metadata["format"] == "error"
    assert metadata["error_kind"] == "resource_limit"
    assert metadata["resource"] == "page count"
    assert metadata["actual"] == 2
    assert metadata["limit"] == 1


def test_pdf_reader_checks_raster_pixels_before_rendering(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    from gideon.cognition.knowledge.pipeline.nodes.media_nodes import (
        ImageModalityOcrProvider,
    )
    from gideon.cognition.knowledge.readers import FileReader, PdfResourceLimits

    path = tmp_path / "large-blank-page.pdf"
    canvas = Canvas(str(path), pagesize=(1000, 1000))
    canvas.showPage()
    canvas.save()
    reader = FileReader(
        ocr_provider=ImageModalityOcrProvider(),
        pdf_limits=PdfResourceLimits(max_page_pixels=100),
    )

    _, metadata = reader.read(str(path))

    assert metadata["format"] == "error"
    assert metadata["error_kind"] == "resource_limit"
    assert metadata["resource"] == "page raster pixels"
    assert metadata["actual"] > metadata["limit"] == 100


def test_host_fact_process_probes_are_process_lifetime_memoized():
    """Capability probes must not become one extra child for every real spawn."""
    caches = (
        sandbox._probe_unshare,
        fit._probe_gpu,
        residency._darwin_memory,
    )
    for probe in caches:
        parameters = probe.cache_parameters()
        assert parameters["maxsize"] is not None
        assert parameters["maxsize"] <= 8
    assert isinstance(sandbox._SANDBOX_EXEC_PROBE_CACHE, dict)


def test_sandbox_exec_host_fact_probe_spawns_only_once(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    sandbox._SANDBOX_EXEC_PROBE_CACHE.clear()
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.platform, "mac_ver", lambda: ("15.0", ("", "", ""), ""))
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(sandbox.subprocess, "run", _run)

    assert sandbox._probe_sandbox_exec() is True
    assert sandbox._probe_sandbox_exec() is True
    assert len(calls) == 1
    sandbox._SANDBOX_EXEC_PROBE_CACHE.clear()
