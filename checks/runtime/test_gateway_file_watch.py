"""The file-watch poll loop is wired into gateway boot (§3 / crit 2 — S93).

`file_poll` is tested as a library in `test_triggers_file_poll.py`; these tests pin the GATEWAY
adapter — that a file change routes to the trigger's declared action provider through the same
registry a cron uses, and that the loop is disjoint from `ScheduleService`. A runtime that polled
correctly but was never started, or fired through a second dispatch path, would be the
present-and-inert / drift defects this program keeps finding.
"""

from __future__ import annotations

import asyncio

import pytest

from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.engine import gateway as G


@pytest.fixture
def home(tmp_path, monkeypatch):
    import gideon.core.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _bare_gateway():
    """A RuntimeCoordinator without the full heavy __init__ — enough to call the fire method."""
    return G.RuntimeCoordinator.__new__(G.RuntimeCoordinator)


def _file_trigger(home, provider="notify", config=None):
    store = TriggerStore(base_dir=home)
    store.upsert(
        Trigger(
            id="file:notes",
            name="Notes",
            kind="file",
            enabled=True,
            spec={"paths": ["~/x/**"]},
            workflow={
                "provider": provider,
                "config": config or {"title_template": "t"},
            },
        )
    )
    return store


def test_a_fire_reaches_the_declared_action_provider(home, monkeypatch):
    """🔴 The end-to-end wiring: a file change runs the trigger's workflow action through the SAME
    action-provider registry a cron uses — no second dispatch path to drift."""
    _file_trigger(
        home,
        provider="notify",
        config={"title_template": "changed", "body_template": "{{trigger_id}}"},
    )

    from gideon.integrations.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    captured = {}
    prov = get_action_provider("notify")
    real = prov.execute

    async def spy(action_config, ctx, timeout=30):
        captured["config"] = action_config
        captured["event"] = ctx.event
        captured["trigger_id"] = ctx.payload.get("trigger_id")
        return await real(action_config, ctx, timeout)

    monkeypatch.setattr(prov, "execute", spy)

    gw = _bare_gateway()
    asyncio.run(
        gw._fire_file_trigger(
            {"trigger_id": "file:notes", "changed": ["/x/a.md"], "added": ["/x/a.md"]}
        )
    )
    assert captured["config"]["title_template"] == "changed"
    assert captured["event"] == "file.changed"
    assert captured["trigger_id"] == "file:notes"


def test_a_fire_for_an_unknown_trigger_is_a_noop(home):
    gw = _bare_gateway()
    asyncio.run(gw._fire_file_trigger({"trigger_id": "file:ghost"}))


def test_a_fire_with_an_unknown_provider_does_not_raise(home):
    _file_trigger(home, provider="does-not-exist")
    gw = _bare_gateway()
    asyncio.run(gw._fire_file_trigger({"trigger_id": "file:notes"}))


def test_a_provider_that_raises_does_not_crash_the_caller(home, monkeypatch):
    """🔴 A failed fire is logged, never propagated — a throwing action must not kill the poll
    loop and silently retire every other file automation."""
    _file_trigger(home, provider="notify")
    from gideon.integrations.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    prov = get_action_provider("notify")

    async def boom(action_config, ctx, timeout=30):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(prov, "execute", boom)
    gw = _bare_gateway()
    asyncio.run(gw._fire_file_trigger({"trigger_id": "file:notes"}))


def test_the_poll_loop_is_started_in_init_cron(monkeypatch):
    """🔴 A runtime that polls correctly but is never started is present-and-inert. Assert the boot
    path creates the task — the source, not just behaviour, since the alternative is a loop nobody
    launches."""
    import inspect

    from gideon.engine.automation_boot import AutomationBoot

    assert "AutomationBoot" in inspect.getsource(G.RuntimeCoordinator._init_cron)
    src = inspect.getsource(AutomationBoot.start)
    assert "_file_watch_poll_loop" in src and "create_task" in src


def test_the_loop_lives_in_the_no_crons_else_branch(monkeypatch):
    """The early-return guard must precede every task launch."""
    import ast
    import inspect
    import textwrap

    from gideon.engine.automation_boot import AutomationBoot

    body = (
        ast.parse(textwrap.dedent(inspect.getsource(AutomationBoot.start))).body[0].body
    )
    guard = body[0]
    assert isinstance(guard, ast.If)
    assert ast.unparse(guard.test) == "self.runtime._no_crons"
    assert isinstance(guard.body[-1], ast.Return)
    launches = [
        node
        for node in body[1:]
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Attribute)
        and node.targets[0].attr == "_file_watch_task"
    ]
    assert len(launches) == 1
    assert "create_task" in ast.unparse(launches[0])


def test_shutdown_cancels_the_loop():
    """A dangling task across shutdown leaks a filesystem poll into the next process."""
    from gideon.core.config import AppConfig

    async def exercise():
        runtime = G.RuntimeCoordinator(AppConfig())
        runtime._file_watch_task = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        assert not runtime._file_watch_task.done()
        await runtime._shutdown()
        assert runtime._file_watch_task.cancelled()

    asyncio.run(exercise())
