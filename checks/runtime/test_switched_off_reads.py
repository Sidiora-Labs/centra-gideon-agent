"""Real dashboard handlers keep disabled and never-run reads distinct."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers import doctor, evals, feedback, learning, rooms


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/doctor", doctor.api_doctor)
    app.router.add_get("/api/doctor/fixes", doctor.api_doctor_fixes)
    app.router.add_get("/api/doctor/remediation", doctor.api_doctor_remediation)
    app.router.add_get("/api/doctor/{capability}", doctor.api_doctor_capability)
    app.router.add_post("/api/doctor/remediation/run", doctor.api_doctor_remediation_run)
    feedback.register_feedback_routes(app)
    learning.register_learning_routes(app)
    rooms.setup_room_routes(app)
    evals.register_evals_routes(app)
    return app


def _config(home, *, evals_enabled: bool = False) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({
        "resilience": {"doctor_enabled": False},
        "feedback": {"enabled": False},
        "learning": {"enabled": False},
        "rooms": {"enabled": False},
        "evals": {"enabled": evals_enabled},
    }), encoding="utf-8")


@pytest.mark.asyncio
async def test_disabled_collection_reads_return_only_the_off_envelope(tmp_path, monkeypatch):
    _config(tmp_path)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    reads = (
        "/api/doctor",
        "/api/doctor/fixes",
        "/api/doctor/remediation",
        "/api/feedback/producers",
        "/api/learning/proposals",
        "/api/learning/staging/week",
        "/api/learning/health",
        "/api/learning/summary",
        "/api/learning/identity-report",
        "/api/rooms",
        "/api/evals/judge-bench",
        "/api/evals/field-metrics",
        "/api/evals/studies",
        "/api/evals/ablation",
        "/api/evals/learning-benchmark",
        "/api/evals/retrieval",
    )
    async with TestClient(TestServer(_app())) as client:
        for path in reads:
            response = await client.get(path)
            assert response.status == 200, path
            assert await response.json() == {"enabled": False}, path


@pytest.mark.asyncio
async def test_never_run_reports_name_the_next_action(tmp_path, monkeypatch):
    _config(tmp_path, evals_enabled=True)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    reads = (
        "/api/evals/judge-bench",
        "/api/evals/ablation",
        "/api/evals/learning-benchmark",
        "/api/evals/retrieval",
    )
    async with TestClient(TestServer(_app())) as client:
        for path in reads:
            response = await client.get(path)
            assert response.status == 200, path
            body = await response.json()
            assert body["state"] == "not_run", path
            assert isinstance(body["next_action"], str) and body["next_action"], path


@pytest.mark.asyncio
async def test_item_and_write_paths_keep_their_disabled_refusals(tmp_path, monkeypatch):
    _config(tmp_path)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    async with TestClient(TestServer(_app())) as client:
        for path in (
            "/api/doctor/memory",
            "/api/feedback/target/inbox_classification/item-1",
            "/api/learning/proposals/item-1",
            "/api/evals/studies/study-1",
            "/api/evals/retrieval/card",
        ):
            response = await client.get(path)
            assert response.status == 404, path
        response = await client.post("/api/doctor/remediation/run", json={})
        assert response.status == 404
        response = await client.post("/api/learning/identity-report")
        assert response.status == 404
        response = await client.post("/api/evals/retrieval/labels", json={})
        assert response.status == 404
