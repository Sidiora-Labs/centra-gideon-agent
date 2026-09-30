"""Native local record writers serialize with durability imports."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.engine.hooks import HOOK_EVENT_USER_PROMPT_SUBMIT, ScriptHookStore
from gideon.integrations.inbox import InboxItem, InboxStore
from gideon.interfaces.dashboard import views_store
from gideon.operations.durability import record_files


def _inbox_item(identity: str) -> InboxItem:
    return InboxItem(
        id=identity,
        channel="operator",
        channel_name="Operator",
        thread_ts=None,
        message=identity,
        sender_id="operator",
        sender_name="Operator",
    )


def test_local_record_stores_wait_for_the_shared_file_lock(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(views_store, "config_dir", lambda: home)

    trigger_store = TriggerStore(base_dir=home)
    hook_store = ScriptHookStore(config_dir=home)
    inbox_store = InboxStore(path=home / "inbox.json")
    inbox_store.add(_inbox_item("initial"))

    started = [Event() for _ in range(4)]

    def write_trigger():
        started[0].set()
        trigger_store.save_all([Trigger(id="local-trigger", name="Local", kind="manual")])

    def write_hook():
        started[1].set()
        hook_store.create({"name": "Local", "event": HOOK_EVENT_USER_PROMPT_SUBMIT})

    def write_inbox():
        started[2].set()
        inbox_store.flush()

    def write_view():
        started[3].set()
        views_store.create_view("Local")

    with ThreadPoolExecutor(max_workers=4) as workers:
        with record_files.locked_store(home):
            futures = [
                workers.submit(operation)
                for operation in (write_trigger, write_hook, write_inbox, write_view)
            ]
            assert all(signal.wait(2) for signal in started)
            assert not any(future.done() for future in futures)
        for future in futures:
            future.result(timeout=5)

    assert json.loads((home / "triggers.json").read_text())["triggers"][0]["id"] == "local-trigger"
    assert len(json.loads((home / "hooks.json").read_text())["hooks"]) == 1
    assert [row["id"] for row in json.loads((home / "inbox.json").read_text())["items"]] == ["initial"]
    assert any(view.name == "Local" for view in views_store.load_views())

    first = InboxStore(path=home / "inbox.json")
    second = InboxStore(path=home / "inbox.json")
    first.load()
    second.load()
    first.add(_inbox_item("first-writer"))
    second.add(_inbox_item("second-writer"))
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(store.save) for store in (first, second)]
        for future in futures:
            future.result(timeout=5)

    persisted = json.loads((home / "inbox.json").read_text())["items"]
    assert {row["id"] for row in persisted} == {
        "initial",
        "first-writer",
        "second-writer",
    }
