from __future__ import annotations

import json

from gideon.workspace import notification_kinds, notification_rules


def test_stored_verification_cannot_hide_human_decision_kinds(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "entity_settings").mkdir(parents=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    path = home / "entity_settings" / "notification_rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": {
                    "loop/needs_input": {"verify": True},
                    "system/agent_request": {"verify": True},
                    "approval/requested": {"verify": True},
                }
            }
        ),
        encoding="utf-8",
    )

    for source, kind in (
        ("loop", "needs_input"),
        ("system", "agent_request"),
        ("approval", "requested"),
    ):
        registered = notification_kinds.resolve_kind(source, kind)
        assert registered.decision
        assert not registered.verifiable
        assert not notification_rules.resolve_rule(source, kind).verify
