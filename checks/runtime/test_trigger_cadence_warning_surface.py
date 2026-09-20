from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.automation.triggers.models import MIN_CLOCK_INTERVAL_SECS, Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.interfaces.dashboard.handlers import triggers as T


class _EmptyStore:
    def load(self):
        return []

    def list_all(self):
        return []


def _request(state, *, method="GET", body=None, trigger_id=""):
    app = web.Application()
    app["state"] = state
    raw = json.dumps(body).encode() if body is not None else b""
    request = make_mocked_request(
        method,
        "/api/triggers",
        app=app,
        match_info={"id": trigger_id},
        headers={"Content-Type": "application/json"} if raw else None,
    )
    request._read_bytes = raw
    return request


def _data(response):
    return json.loads(response.body)


def _clock() -> Trigger:
    trigger = Trigger(
        id="clock:fast",
        name="Fast",
        kind="clock",
        enabled=True,
        spec={"kind": "interval", "interval_secs": 30},
        workflow={"inline": {"provider": "invoke-agent", "config": {}}},
    )
    trigger.next_fire_at = "2099-01-01T00:00:00+00:00"
    return trigger


def test_warning_reaches_list_writes_doctor_and_ui(tmp_path, monkeypatch):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    state = type("State", (), {"push_refresh": lambda *_: None})()
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_event_store", lambda: _EmptyStore())
    monkeypatch.setattr(T, "_hook_store", lambda _state: _EmptyStore())
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    monkeypatch.setattr(T, "_last_run_status", lambda *_: None)

    listed = _data(asyncio.run(T.api_triggers(_request(state))))["triggers"][0]
    assert listed["broken"] == []
    assert f"{MIN_CLOCK_INTERVAL_SECS}s floor" in listed["warnings"][0]

    updated = _data(
        asyncio.run(
            T.api_trigger_detail(
                _request(
                    state,
                    method="PUT",
                    body={"every": 30},
                    trigger_id="schedule:clock:fast",
                )
            )
        )
    )["trigger"]
    assert updated["warnings"] == listed["warnings"]
    assert (
        TriggerStore(base_dir=tmp_path).get("clock:fast").trigger.next_fire_at
        == "2099-01-01T00:00:00+00:00"
    )

    toggled = _data(
        asyncio.run(
            T.api_trigger_toggle(
                _request(
                    state,
                    method="POST",
                    body={"enabled": False},
                    trigger_id="schedule:clock:fast",
                )
            )
        )
    )["trigger"]
    assert toggled["warnings"] == listed["warnings"]

    doctor = _data(asyncio.run(T.api_triggers_doctor(_request(state))))
    assert any(f["code"] == "trigger_warning" for f in doctor["findings"])

    root = Path(__file__).parents[2] / "apps/console/src/features"
    assert "check schedule" in (root / "triggers/TriggersListPage.tsx").read_text()
    assert (
        "Recommended minimum: 60 seconds"
        in (root / "schedule/ScheduleForm.tsx").read_text()
    )
