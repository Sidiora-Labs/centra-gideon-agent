"""Confirm-gated fixes + crash capture + surfacing-simulator tests
(PLATFORM-RESILIENCE §2, §6.5, §3.1)."""

from __future__ import annotations

import json

import pytest

from gideon.extensions.skills.surfacing import surface_skills
from gideon.operations.resilience import crashes, fixes


def test_builtin_fixes_registered():
    ids = {f.id for f in fixes.all_fixes()}
    assert {
        "serving-fs.symlink-repair",
        "serving-fs.orphan-prune",
        "model-providers.prune-bindings",
    } <= ids


def test_every_fix_has_a_readonly_preview():
    for fx in fixes.all_fixes():
        preview = fx.dry_preview()
        assert isinstance(preview, str) and preview


def test_apply_unknown_fix_is_safe():
    result = fixes.apply_fix("no-such-fix")
    assert result["ok"] is False and "unknown" in result["error"]


def test_symlink_repair_preview_on_copy(tmp_path, monkeypatch):
    """When static/dist is a real-directory COPY, the preview describes a repair.
    `_dist_paths` derives repo_root as pkg_dir.parent.parent (<repo>/src/gideon),
    so lay the fake package out that way and put apps/console/dist at the repo root."""
    import gideon

    pkg = tmp_path / "runtime" / "gideon"
    (pkg / "static" / "dist").mkdir(parents=True)
    (pkg / "static" / "dist" / "index.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    (tmp_path / "apps/console" / "dist").mkdir(parents=True)
    (tmp_path / "apps/console" / "dist" / "index.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    monkeypatch.setattr(gideon, "__file__", str(pkg / "__init__.py"))
    fx = fixes.get_fix("serving-fs.symlink-repair")
    assert fx is not None
    preview = fx.dry_preview()
    assert "symlink" in preview and "copy" in preview.lower()


def test_symlink_repair_apply_backs_up_and_links(tmp_path, monkeypatch):
    import gideon

    pkg = tmp_path / "runtime" / "gideon"
    dist = pkg / "static" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("STALE", encoding="utf-8")
    built = tmp_path / "apps/console" / "dist"
    built.mkdir(parents=True)
    (built / "index.html").write_text("FRESH", encoding="utf-8")
    monkeypatch.setattr(gideon, "__file__", str(pkg / "__init__.py"))

    fx = fixes.get_fix("serving-fs.symlink-repair")
    result = fx.apply()  # type: ignore[union-attr]
    assert "Repaired" in result
    assert dist.is_symlink()
    assert dist.resolve() == built.resolve()
    assert (pkg / "static" / "dist.shadow" / "index.html").read_text() == "STALE"


def test_record_and_read_crash_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gideon.operations.resilience.crashes.config_dir", lambda: tmp_path
    )
    assert crashes.crash_count() == 0
    path = crashes.record_crash(
        "turn",
        RuntimeError("boom in the turn"),
        session_key="dashboard:abc",
        last_turns=["hello", "world"],
        active_model="bedrock:claude",
        uptime_secs=42.5,
        now=1000.0,
    )
    assert path is not None and path.exists()
    assert crashes.crash_count() == 1
    recent = crashes.recent_crashes()
    assert len(recent) == 1
    assert recent[0]["kind"] == "turn"
    assert recent[0]["exception_type"] == "RuntimeError"
    full = crashes.read_crash(path.name)
    assert full is not None
    assert full["session_key"] == "dashboard:abc"
    assert full["exception"]["message"] == "boom in the turn"


def test_crash_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gideon.operations.resilience.crashes.config_dir", lambda: tmp_path
    )
    secret = "AKIA" + "Z" * 16
    crashes.record_crash(
        "gateway",
        RuntimeError(f"auth failed with {secret}"),
        now=2000.0,
    )
    recent = crashes.recent_crashes()
    assert secret not in json.dumps(recent)


def test_crash_dir_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gideon.operations.resilience.crashes.config_dir", lambda: tmp_path
    )
    for i in range(25):
        crashes.record_crash("turn", RuntimeError(f"e{i}"), now=1000.0 + i)
    assert crashes.crash_count() <= 20


def test_read_crash_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gideon.operations.resilience.crashes.config_dir", lambda: tmp_path
    )
    assert crashes.read_crash("../config.json") is None
    assert crashes.read_crash("a/b.json") is None


_SKILLS = [
    {
        "key": "deploy",
        "description": "deploy to kubernetes",
        "triggers": "deploy,ship",
        "path": "/x",
        "use_count": 5,
    },
    {
        "key": "vetoed",
        "description": "never for greetings",
        "triggers": "!hello",
        "path": "/y",
        "use_count": 0,
    },
    {
        "key": "archived-one",
        "description": "old",
        "triggers": "deploy",
        "path": "/z",
        "status": "archived",
        "use_count": 0,
    },
]


def test_explain_returns_per_candidate_breakdown():
    rows = surface_skills(
        "please deploy the service", _SKILLS, max_skills=5, explain=True
    )
    assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
    by_key = {r["key"]: r for r in rows}
    assert by_key["deploy"]["included"] is True
    assert by_key["deploy"]["kw_score"] >= 0.7
    assert "included" in by_key["deploy"]["reason"]
    assert by_key["archived-one"]["included"] is False
    assert "archived" in by_key["archived-one"]["reason"]


def test_explain_shows_negative_trigger_veto():
    rows = surface_skills("hello there", _SKILLS, max_skills=5, explain=True)
    veto = next(r for r in rows if r["key"] == "vetoed")
    assert veto["negated"] is True and veto["included"] is False


def test_non_explain_still_returns_keys():
    keys = surface_skills("please deploy the service", _SKILLS, max_skills=5)
    assert keys == ["deploy"]


def test_explain_empty_query_returns_empty():
    assert surface_skills("", _SKILLS, max_skills=5, explain=True) == []


class TestFixConfirmationOverHttp:
    """BACKLOG-47 — the confirm gate and the audit, at the HTTP boundary.

    ``apply_fix`` was only ever driven directly, so the two properties the surface actually
    promises went untested: that the route REFUSES without ``{confirm: true}`` (an armed
    two-step, not a suggestion), and that one confirmed application writes exactly ONE
    maintenance security event. The event count is read from the real SEL, and the
    disk-effect assertion is what makes the refusal meaningful — a 400 with the fix having
    already run would look identical in the response body.

    The fix under test is registered through the real ``register_fix`` seam (the same one
    ``_register_builtin_fixes`` uses) and writes a real file, so the repair is observable in
    a throwaway directory instead of depending on one shipped fix's own file layout.
    """

    @pytest.fixture
    def marker(self, tmp_path):
        """A registered fix whose ``apply`` repairs a real file, and its target path."""
        path = tmp_path / "repaired.txt"

        def _preview() -> str:
            return f"Would write {path.name}."

        def _apply() -> str:
            path.write_text("repaired", encoding="utf-8")
            return f"Repaired: wrote {path.name}."

        fixes.register_fix(
            fixes.Fix(
                id="test-only.marker-repair",
                title="Write the marker file",
                impact="Writes one file in a temp dir.",
                dry_preview=_preview,
                apply=_apply,
            )
        )
        try:
            yield path
        finally:
            fixes._FIXES.pop("test-only.marker-repair", None)

    @staticmethod
    def _app():
        from aiohttp import web

        from gideon.interfaces.dashboard.handlers.doctor import api_doctor_fix_apply

        app = web.Application()
        app.router.add_post("/api/doctor/fix/{fix_id}", api_doctor_fix_apply)
        return app

    @staticmethod
    def _maintenance_events() -> list[dict]:
        from gideon.security.sel import sel

        return [
            e for e in sel().recent(limit=50) if e.get("tool_kind") == "maintenance"
        ]

    async def test_a_fix_cannot_execute_without_explicit_confirmation(self, marker):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app())) as client:
            for body in ({}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1}):
                response = await client.post(
                    "/api/doctor/fix/test-only.marker-repair", json=body
                )
                assert response.status == 400, body
                assert (await response.json())["error"][
                    "code"
                ] == "confirm_required", body

        assert not marker.exists(), "the fix ran without confirmation"
        assert self._maintenance_events() == []

    async def test_one_confirmed_fix_emits_exactly_one_maintenance_event(self, marker):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app())) as client:
            response = await client.post(
                "/api/doctor/fix/test-only.marker-repair", json={"confirm": True}
            )
            assert response.status == 200
            body = await response.json()

        assert body["ok"] is True and "Repaired" in body["result"]
        assert marker.read_text() == "repaired"

        events = self._maintenance_events()
        assert len(events) == 1, events
        assert events[0]["operation"] == "doctor_fix:test-only.marker-repair"
        assert events[0]["outcome"] == "ok"
        assert events[0]["metadata"]["fix_id"] == "test-only.marker-repair"

    async def test_an_unknown_fix_is_refused_after_confirmation_without_an_event(self):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app())) as client:
            response = await client.post(
                "/api/doctor/fix/no-such-fix", json={"confirm": True}
            )
            assert response.status == 404
            assert (await response.json())["error"]["code"] == "unknown_fix"

        assert self._maintenance_events() == []
