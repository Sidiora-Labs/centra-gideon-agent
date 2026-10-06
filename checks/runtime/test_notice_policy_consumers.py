"""Current notice policy through native delivery, persisted rules, and served digest."""

import json
from datetime import datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.routing.usage import TurnAccumulator
from gideon.engine.session import ConversationDirectory
from gideon.extensions.providers import entity_routes
from gideon.interfaces.dashboard.handlers.proactive import (
    _digest_notice,
    api_proactive_digest,
)
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.workspace import notification_kinds as nk
from gideon.workspace import notification_rules as nr


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(entity_routes, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "gideon.interfaces.dashboard.state.config_dir", lambda: tmp_path
    )
    entity_routes._save_entity_settings(
        "notifications",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": (datetime.now() - timedelta(minutes=2)).strftime(
                "%H:%M"
            ),
            "quiet_hours_end": (datetime.now() + timedelta(minutes=2)).strftime(
                "%H:%M"
            ),
        },
    )
    state = ConsoleState(ConversationDirectory(AppConfig()), 0)
    return tmp_path, state


def rule(home, key, mode, **extra):
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps({"rules": {key: {"mode": mode, "targets": ["dashboard"], **extra}}})
    )


def test_native_quiet_batch_escalation_and_hard_gate(native):
    home, state = native
    key = nk.kind_for_legacy("info").key
    state.notify("info", "Digest", "ordinary")
    assert state._notification_log == []
    assert _digest_notice(title="Digest", body="ordinary")["inside"] == "suppressed"
    for mode in ("badge", "digest", "never"):
        rule(home, key, mode)
        before = len(state._notification_log)
        state.notify("info", "Digest", "ordinary")
        projection = _digest_notice(title="Digest", body="ordinary")
        assert projection["inside"] == projection["outside"] == mode
        assert len(state._notification_log) == before + (mode == "badge")
        if mode == "digest":
            assert nr.drain_digest_queue()[0]["title"] == "Digest"
    rule(home, key, "digest", conditions={"keywords": ["urgent"]})
    state.notify("info", "Digest", "urgent item")
    assert state._notification_log[-1]["mode"] == "badge"
    assert state._notification_log[-1]["escalated_by"] == "keyword: urgent"
    projection = _digest_notice(title="Digest", body="urgent item")
    assert projection["inside"] == "badge" and projection["outside"] == "immediate"
    for settings in ({"mute_all": True}, {"min_severity": "warning"}):
        entity_routes._save_entity_settings("notifications", settings)
        before = len(state._notification_log)
        state.notify("info", "Digest", "urgent item")
        assert len(state._notification_log) == before
        assert (
            _digest_notice(title="Digest", body="urgent item")["outside"] == "dropped"
        )
    (home / "entity_settings" / "notifications.json").write_text("{broken")
    assert _digest_notice(title="Digest", body="ordinary") == {"known": False}


@pytest.mark.asyncio
async def test_actual_http_digest_and_app_label_ack_roundtrip(native):
    home, state = native
    app = web.Application(middlewares=[token_auth_middleware(port=19427)])
    app["state"] = state
    app.router.add_get("/api/proactive/digest", api_proactive_digest)
    cookie = {"gideon_token_19427": generate_token("notice-reader", kind="desktop")}
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/proactive/digest", cookies=cookie)
        assert response.status == 200, await response.text()
        view = await response.json()
        assert view["notice"]["inside"] == "suppressed"
        assert view["notice"]["outside"] == "immediate"
    pairs = [
        ("app:alpha-test", "proposal:approve", "Alpha approval"),
        ("app:beta-test", "proposal:approve", "Beta approval"),
    ]
    try:
        for source, kind, label in pairs:
            nk.register(nk.NotificationKind(source, kind, label, attention=True))
        rule(home, "app:alpha-test/proposal:approve", "badge")
        state.notify(
            "app:alpha-test/proposal:approve",
            "Proposal",
            "approve",
            meta={"kind_label": "Forged"},
        )
        note = state._notification_log[-1]
        assert (
            note["kind_label"] == "Alpha approval"
            and note["source"] == "app:alpha-test"
        )
        assert state.ack_notification(note["ts"])
        persisted = [
            json.loads(row)
            for row in (home / "notifications.jsonl").read_text().splitlines()
        ]
        assert (
            persisted[-1]["acked"] and persisted[-1]["kind_label"] == "Alpha approval"
        )
        assert (
            nr.resolve_rule_for_legacy("app:beta-test/proposal:approve").mode
            == "immediate"
        )
    finally:
        for source, kind, _ in pairs:
            nk.unregister(source, kind)


def test_recorded_locality_survives_current_model_configuration():
    fold = {}
    accumulator = TurnAccumulator(fold, lambda *_: (False, True))
    base = {
        "ts": "2026-10-07T10:00:00Z",
        "provider": "vendor",
        "model": "same",
        "input_tokens": 1,
        "priced": True,
    }
    for record in (
        {"price_source": "local"},
        {"price_source": "configured"},
        {"local": False, "price_source": "local"},
        {},
    ):
        assert accumulator.accept({**base, **record})
    cells = list(fold["days"]["2026-10-07"].values())
    cell = next(iter(cells[0].values()))
    assert cell["calls"] == 4 and cell["local_calls"] == 2
