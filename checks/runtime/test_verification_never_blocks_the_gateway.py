import asyncio
import json

import pytest
from aiohttp import web

from gideon.integrations import inbox as inbox_module
from gideon.integrations.inbox import InboxItem, InboxStore, emit_attention_item
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.mark.asyncio
async def test_opted_in_verification_persists_checking_row_before_background_work(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    rules_path = tmp_path / "entity_settings" / "notification_rules.json"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(
        json.dumps({"rules": {"skills/proposal": {"verify": True}}}),
        encoding="utf-8",
    )
    store = InboxStore(tmp_path / "inbox.json")

    item_id = emit_attention_item(
        None,
        source="skills",
        kind="proposal",
        title="A proposal is ready",
        store=store,
    )

    item = store.items[item_id]
    assert item.refs["verify"] == "checking"
    assert item.status == "pending"
    assert (tmp_path / "inbox.json").is_file()
    pending = tuple(inbox_module._verification_tasks)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_verification_from_worker_thread_is_created_on_console_owner_loop(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    owner_loop = asyncio.get_running_loop()
    owner_loop.set_debug(True)
    state = ConsoleState(None, start_time=0.0)
    ws = web.WebSocketResponse()
    state.register_ws(ws)
    state.unregister_ws(ws)
    assert state._ws_loop is owner_loop

    store = InboxStore(tmp_path / "inbox.json")
    item = InboxItem(
        id="thread-safe-verification",
        channel="skills",
        channel_name="skills",
        thread_ts=None,
        message="",
        sender_id="skills",
        sender_name="skills",
        status="handled",
        refs={"verify": "checking"},
    )
    store.add(item)
    store.flush()

    await asyncio.to_thread(
        inbox_module._schedule_attention_verification,
        state,
        store,
        item,
        {"title": "", "body": ""},
    )
    for _ in range(50):
        if item.refs.get("verify") != "checking":
            break
        await asyncio.sleep(0)

    assert item.refs["verify"] == "skipped"
    assert item.status == "handled"
