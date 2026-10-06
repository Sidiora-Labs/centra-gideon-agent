from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from aiohttp import ClientSession


def test_heartbeat_tasks_wait_for_a_revision_specific_owner_grant(
    tmp_path, monkeypatch
):
    from gideon.engine.heartbeat import (
        _HEADER,
        HEARTBEAT_FILE,
        HeartbeatService,
        allow_heartbeat_task,
        heartbeat_path,
        heartbeat_task_rows,
        record_owner_file_added_tasks,
    )
    from gideon.security.approval_answer import Principal

    home = tmp_path / "home"
    workspace = home / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    path = heartbeat_path()
    path.write_text(_HEADER + "- Refresh my task list\n", encoding="utf-8")
    ran: list[str] = []

    async def perform(task: str, destination: str) -> None:
        ran.append(task)

    service = HeartbeatService(on_task=perform)
    asyncio.run(service._process_heartbeat_file())
    assert ran == []
    assert "Refresh my task list" in path.read_text(encoding="utf-8")

    row = heartbeat_task_rows()[0]
    before = _HEADER
    after = before + "- Refresh my task list\n"
    assert (
        record_owner_file_added_tasks(
            before, after, principal=Principal("owner", "customer")
        )
        == 1
    )
    assert (
        record_owner_file_added_tasks(
            before, after, principal=Principal("agent", "session")
        )
        == 0
    )
    assert allow_heartbeat_task(
        row["id"], seen=row["revision"], principal=Principal("owner", "customer").label
    )

    asyncio.run(service._process_heartbeat_file())
    assert ran == ["Refresh my task list"]
    assert "Refresh my task list" not in path.read_text(encoding="utf-8")

    path.write_text(_HEADER + "- Refresh my task list differently\n", encoding="utf-8")
    asyncio.run(service._process_heartbeat_file())
    assert ran == ["Refresh my task list"]
    assert HEARTBEAT_FILE == path.name


def test_agent_cli_hook_runs_only_from_the_pinned_allowed_bytes(tmp_path, monkeypatch):
    from gideon.engine.agent import _apply_user_agent_hooks
    from gideon.security.agent_hook_grants import allow, seal_of

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    script = tmp_path / "before-tool.sh"
    original = b"#!/bin/sh\nprintf allowed\n"
    script.write_bytes(original)
    script.chmod(0o700)
    config = {
        "agent": {
            "agent_hooks_autoimport": False,
            "agent_hooks": {"preToolUse": [{"command": str(script)}]},
        }
    }

    waiting: dict = {"hooks": {}}
    _apply_user_agent_hooks(waiting, config)
    assert waiting["hooks"] == {}
    revision = seal_of(str(script))
    assert allow("preToolUse", str(script), None, seen=revision)

    accepted: dict = {"hooks": {}}
    _apply_user_agent_hooks(accepted, config)
    (run,) = accepted["hooks"]["preToolUse"]
    pinned = Path(run["command"])
    assert pinned.read_bytes() == original
    assert pinned.parent.name == ".allowed"

    script.write_bytes(b"#!/bin/sh\nprintf changed\n")
    changed: dict = {"hooks": {}}
    _apply_user_agent_hooks(changed, config)
    assert changed["hooks"] == {}


def test_unreadable_grant_storage_holds_and_cannot_be_rewritten(tmp_path, monkeypatch):
    from gideon.security.owner_grants import GrantBook, GrantBookError

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    book = GrantBook("callbacks")
    book.give("callback:daily", "summary v1")
    assert book.holds("callback:daily", "summary v1")
    assert not book.holds("callback:daily", "summary v2")

    book.path.write_text("not-json", encoding="utf-8")
    assert not book.holds("callback:daily", "summary v1")
    with pytest.raises(GrantBookError):
        book.give("callback:daily", "summary v2")


@pytest.mark.asyncio
async def test_real_dashboard_requires_owner_allow_for_callbacks_and_cli_hooks(
    tmp_path, monkeypatch
):
    from gideon.assurance.api_version import API_VERSION, VERSION_HEADER
    from gideon.cognition.history import ConversationLog
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.server import start_dashboard
    from gideon.interfaces.dashboard.token_auth import generate_token

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_AUTH_MODE", "local_token")
    (home / "config.json").write_text(
        json.dumps({"hooks": {"webhook_token": "test-webhook-token"}}),
        encoding="utf-8",
    )
    script = home / "owner-hook.sh"
    script.write_text("#!/bin/sh\nprintf owner\n", encoding="utf-8")
    script.chmod(0o700)
    (home / "agent.json").write_text(
        json.dumps(
            {
                "agent": {
                    "agent_hooks_autoimport": False,
                    "agent_hooks": {"preToolUse": [{"command": str(script)}]},
                }
            }
        ),
        encoding="utf-8",
    )
    (home / "hooks.json").write_text(
        json.dumps(
            {
                "nightly": {
                    "session_key": "hook:nightly",
                    "context_summary": "Review the saved daily notes.",
                    "registered_at": time.time(),
                }
            }
        ),
        encoding="utf-8",
    )

    runner, _state = await start_dashboard(
        sessions=ConversationDirectory(AppConfig.load()),
        port=0,
        conversation_log=ConversationLog(base_dir=home / "history"),
    )
    owner_token = generate_token("customer", ttl_seconds=3600)
    headers = {
        "Authorization": f"Bearer {owner_token}",
        VERSION_HEADER: str(API_VERSION),
    }
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        async with ClientSession() as client:
            pending = await client.get(f"{base}/api/hooks/pending", headers=headers)
            assert pending.status == 200
            callback = (await pending.json())["callbacks"][0]
            assert "Allow this registered webhook callback" in callback["question"]

            webhook_headers = {
                "Authorization": "Bearer test-webhook-token",
                "X-Internal-Secret": runner.app["local_secret"],
            }
            refused = await client.post(
                f"{base}/api/hooks/agent",
                headers=webhook_headers,
                json={"message": "new report", "sessionKey": "hook:nightly"},
            )
            assert refused.status == 403
            assert (await refused.json())["error"] == "owner_allow_required"

            allowed = await client.post(
                f"{base}/api/hooks/nightly/allow",
                headers=headers,
                json={"confirm": True, "seen": callback["revision"]},
            )
            assert allowed.status == 200

            hook_list = await client.get(f"{base}/api/agent-hooks", headers=headers)
            assert hook_list.status == 200
            hook = (await hook_list.json())["hooks"]["preToolUse"][0]
            assert hook["allowed"] is False
            assert "Allow the agent CLI" in hook["question"]
            hook_allowed = await client.post(
                f"{base}/api/agent-hooks/allow",
                headers=headers,
                json={"confirm": True, "id": hook["id"], "seen": hook["revision"]},
            )
            assert hook_allowed.status == 200
            assert (await hook_allowed.json())["applied"] is True

            script.write_text("#!/bin/sh\nprintf changed\n", encoding="utf-8")
            callback_store = home / "hooks.json"
            changed_records = json.loads(callback_store.read_text(encoding="utf-8"))
            changed_records["nightly"]["context_summary"] = "Run a changed report."
            callback_store.write_text(json.dumps(changed_records), encoding="utf-8")
            hook_list = await client.get(f"{base}/api/agent-hooks", headers=headers)
            changed_hook = (await hook_list.json())["hooks"]["preToolUse"][0]
            assert changed_hook["allowed"] is False
            changed_callback = await client.post(
                f"{base}/api/hooks/agent",
                headers=webhook_headers,
                json={"message": "new report", "sessionKey": "hook:nightly"},
            )
            assert changed_callback.status == 403
    finally:
        await runner.cleanup()
