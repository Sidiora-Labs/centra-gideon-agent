"""Design kind completion gate — a design loop persists DESIGN.md via artifact_save
(not a loop-dir file; wants_workspace=False), so on_new_cycle must complete when the
DESIGN.md ARTIFACT exists, not only when a loop-dir file does. Without this the loop
spins additive cycles to its budget and never reaches `complete` (observed live)."""

from __future__ import annotations

import asyncio

import pytest

from gideon.automation.loop import kinds, store
from gideon.automation.loop.loop import Loop


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.automation.loop.files.config_dir", lambda: tmp_path)
    import gideon.workspace.artifacts.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    return tmp_path


class _Ctx:
    def __init__(self):
        self.completed = None

    def publish(self, loop_id, event, data=None):
        pass

    async def complete(self, loop_id, reason=""):
        self.completed = (loop_id, reason)


def _design_loop():
    return store.create(
        Loop(
            id="",
            name="D",
            kind="design",
            task="design a tic-tac-toe system",
            plan=[{"step": "build_plan", "title": "Document & export"}],
        )
    )


class TestDesignCompletionGate:
    def test_does_not_complete_without_a_deliverable(self):
        s = kinds.get("design")
        c = _design_loop()
        ctx = _Ctx()
        done = _run(
            s.on_new_cycle(
                store.get(c.id),
                [{"cycle": 1, "step": "build_plan", "summary": "drafted"}],
                ctx,
            )
        )
        assert done is False and ctx.completed is None

    def test_completes_on_design_artifact_even_without_loop_dir_file(self):
        from gideon.workspace.artifacts import registry as artifact_registry

        prov = artifact_registry.get_provider()
        c = _design_loop()
        prov.create(
            name="DESIGN.md",
            content="# Design System\nReal content.",
            kind="markdown",
            tags=[f"loop:{c.id}"],
            actor="agent",
        )
        assert not store.read_deliverable(c.id).strip()
        ctx = _Ctx()
        done = _run(s_on_cycle(c, ctx))
        assert done is True and ctx.completed and ctx.completed[0] == c.id


def s_on_cycle(c, ctx):
    s = kinds.get("design")
    return s.on_new_cycle(
        store.get(c.id),
        [{"cycle": 1, "step": "build_plan", "summary": "delivered"}],
        ctx,
    )


class TestPhaseMatchTolerantOfOrdinalPrefix:
    """A worker reports its step with an ordinal prefix ("1. Emit Primitive Token Layer")
    while the plan phase title is bare ("Emit Primitive Token Layer"). The matcher must
    strip the ordinal, else the phase trail freezes on phase 0 and completion is deferred
    to the slow per-cycle fallback (observed live: stuck on `foundations` for cycles).
    """

    def _multi_phase(self):
        return store.create(
            Loop(
                id="",
                name="D",
                kind="design",
                task="a design system",
                plan=[
                    {"step": "foundations", "title": "Emit Primitive Token Layer"},
                    {
                        "step": "palette",
                        "title": "Expand & Verify Semantic Color Roles",
                    },
                    {"step": "build_plan", "title": "Assemble DESIGN.md & Export"},
                ],
            )
        )

    def test_strip_step_ordinal_forms(self):
        s = kinds.get("design")
        strip = s._strip_step_ordinal
        assert strip("1. Emit Primitive Token Layer") == "emit primitive token layer"
        assert strip("2 — Palette") == "palette"
        assert strip("step 3: Components") == "components"
        assert strip("Foundations") == "foundations"

    def test_ordinal_prefixed_step_advances_trail(self):
        s = kinds.get("design")
        c = self._multi_phase()
        ctx = _Ctx()
        _run(
            s.on_new_cycle(
                store.get(c.id),
                [
                    {
                        "cycle": 2,
                        "step": "2. Expand & Verify Semantic Color Roles",
                        "summary": "contrast pass",
                    }
                ],
                ctx,
            )
        )
        ps = store.get(c.id).phase_status
        assert ps.get("foundations") == "done" and ps.get("palette") == "active"
        assert ctx.completed is None

    def test_drifted_title_with_ordinal_prefix_resolves_by_index(self):
        kinds.ensure_loaded()
        s = kinds.get("design")
        loop = store.create(
            Loop(
                id="",
                name="D",
                kind="design",
                task="t",
                plan=[
                    {"step": "foundations", "title": "Materialize foundation tokens"},
                    {"step": "palette", "title": "Expand & re-verify color scales"},
                    {"step": "typography", "title": "Type scale & X/O glyph proof"},
                    {"step": "components", "title": "Per-state specs & keyboard model"},
                    {"step": "export", "title": "Document, export tokens & verify"},
                ],
                phase_status={
                    "foundations": "done",
                    "palette": "done",
                    "typography": "done",
                },
            )
        )
        ctx = _Ctx()
        _run(
            s.on_new_cycle(
                store.get(loop.id),
                [
                    {
                        "cycle": 4,
                        "step": "Step 4 — Per-state component specs & keyboard/ARIA model",
                    }
                ],
                ctx,
            )
        )
        ps = store.get(loop.id).phase_status
        assert ps.get("components") == "active"
        assert ps.get("foundations") == "done" and ps.get("typography") == "done"

    def test_stepless_finding_never_regresses_the_trail(self):
        kinds.ensure_loaded()
        s = kinds.get("design")
        loop = store.create(
            Loop(
                id="",
                name="D",
                kind="design",
                task="t",
                plan=[
                    {"step": "foundations", "title": "F"},
                    {"step": "palette", "title": "P"},
                    {"step": "components", "title": "C"},
                    {"step": "export", "title": "E"},
                ],
                phase_status={
                    "foundations": "done",
                    "palette": "done",
                    "components": "active",
                },
                max_cycles=30,
            )
        )
        ctx = _Ctx()
        _run(
            s.on_new_cycle(
                store.get(loop.id), [{"cycle": 3, "summary": "no step field"}], ctx
            )
        )
        ps = store.get(loop.id).phase_status
        assert ps.get("foundations") == "done" and ps.get("palette") == "done"
        assert ps.get("components") in ("active", "done")

    def test_bare_integer_step_is_a_one_based_phase_index(self):
        s = kinds.get("design")
        c = self._multi_phase()
        ctx = _Ctx()
        _run(
            s.on_new_cycle(
                store.get(c.id), [{"cycle": 2, "step": 2, "summary": "palette"}], ctx
            )
        )
        ps = store.get(c.id).phase_status
        assert ps.get("foundations") == "done" and ps.get("palette") == "active"
        assert ctx.completed is None
