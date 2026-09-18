"""`/api/inbox/digest` is a CREATION, and every digest it mints has its own id (req 60).

Two defects, one endpoint:

* **Registered as a GET.** Every call runs a summarization job, `add`s a new row to the
  store, saves it and broadcasts it — a state change behind the one verb the whole stack
  treats as safe. A GET is cacheable and prefetchable, and `server.py` keys both the CSRF
  origin check (`_safe_methods`) and the SEL audit trail (`_sel_log_methods`) on the
  method, so the one inbox route that spends model budget was also the one with neither.
* **`id=f"{channel}_digest_{int(ts)}"`.** Second precision. `InboxStore.add` is
  `self.items[item.id] = item`, so two digests for a channel inside the same second were
  not two items — the second one silently REPLACED the first, and its dismissal/retention
  state went with it.

The id now comes from `make_item_id`, the package's one id seam: `{channel}_digest_
{uuid8}_{ts:.6f}`. The uuid8 is the collision resistance and it sits in the MIDDLE because
the trailing timestamp is load-bearing — `InboxItem.ts` rsplits the id on the last
underscore, and sorting, `prune_dismissed` and retention all read that.

Bug #23's param-validation regression is kept below, moved to the body with the verb:
`hours` non-numeric was a raw 500 ("Server got itself in trouble") because the
`float(...)` cast sat outside the try block.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

import gideon.integrations.inbox_service as svc_mod
from gideon.integrations.inbox import InboxItem, InboxState, InboxStore
from gideon.integrations.inbox_service import InboxService
from gideon.interfaces.dashboard import handlers_inbox as h

_NO_BODY = object()

_FROZEN = 1_700_000_000.5


class _State:
    """The ConsoleState surface `api_inbox_digest` touches."""

    def __init__(self, svc: InboxService | None = None) -> None:
        self._inbox_svc = svc
        self.broadcasts: list[tuple[str, Any]] = []

    def broadcast_ws(self, event: str, payload: Any) -> None:
        self.broadcasts.append((event, payload))


async def _post(st: _State, body: Any = _NO_BODY) -> tuple[int, Any]:
    req = make_mocked_request("POST", "/api/inbox/digest")
    req.app["state"] = st

    async def _json():
        if body is _NO_BODY:
            raise json.JSONDecodeError("no body", "", 0)
        return body

    req.json = _json  # type: ignore[method-assign]
    resp = await h.api_inbox_digest(req)
    return resp.status, json.loads(resp.text or "{}")


@pytest.fixture(autouse=True)
def _isolate_inbox_files(monkeypatch, tmp_path):
    """A bare InboxStore()/InboxState() defaults to config_dir() — the REAL
    ~/.gideon/inbox.json, which `generate_digest` SAVES."""
    monkeypatch.setattr("gideon.integrations.inbox.config_dir", lambda: tmp_path)


def _channel_item(i: int, *, now: float) -> InboxItem:
    return InboxItem(
        id=f"C1_{now - i:.6f}",
        channel="C1",
        channel_name="#general",
        thread_ts=None,
        message=f"message {i}",
        sender_id="U2",
        sender_name="Sam",
        created_at=now - i,
    )


@pytest.fixture
def svc(monkeypatch) -> InboxService:
    """A real InboxService over a real store, with only the model call replaced —
    the same seam `test_inbox_service.py` uses."""
    store = InboxStore()
    now = time.time()
    for i in range(3):
        item = _channel_item(i, now=now)
        store.items[item.id] = item

    async def fake_one_shot(prompt: str, *, use_case: str = "background") -> str:
        return "3 messages about a PR review."

    monkeypatch.setattr(
        "gideon.integrations.llm_helpers.one_shot_completion", fake_one_shot
    )
    return InboxService(state=InboxState(), store=store, user_name="Alex")


def _inbox_routes() -> set[tuple[str, str]]:
    """(method, path) for every literal inbox route registered in `server.py`.

    Statically, like the house route guards (`test_api_manifest_drift`): booting the
    dashboard to walk the live table has security-critical startup side effects.
    """
    from gideon.interfaces.dashboard import server as server_mod

    tree = ast.parse(Path(server_mod.__file__).read_text(encoding="utf-8"))
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        verb = node.func.attr
        if not verb.startswith("add_") or not node.args:
            continue
        path = node.args[0]
        if isinstance(path, ast.Constant) and isinstance(path.value, str):
            if path.value.startswith("/api/inbox"):
                found.add((verb[len("add_") :].upper(), path.value))
    return found


def test_the_digest_route_is_registered_as_a_creation_verb():
    """🔑 The half a handler cannot assert about itself: the route's VERB."""
    routes = _inbox_routes()
    assert ("POST", "/api/inbox/digest") in routes
    assert (
        "GET",
        "/api/inbox/digest",
    ) not in routes, "a read verb still mints digests (and skips CSRF + SEL)"


def test_every_other_inbox_mutation_is_already_a_mutating_verb():
    """Vacuity floor for the check above — the surface it is being made consistent with."""
    routes = _inbox_routes()
    assert ("POST", "/api/inbox/notes") in routes
    assert ("PUT", "/api/inbox/{id}") in routes


@pytest.mark.asyncio
async def test_digest_missing_channel_id_is_400():
    status, body = await _post(_State(), {})
    assert status == 400
    assert "channel_id" in body["error"]


@pytest.mark.asyncio
async def test_a_request_with_no_body_at_all_is_the_same_400():
    """A POST with no body is an empty one, not a 500."""
    status, body = await _post(_State())
    assert status == 400
    assert "channel_id" in body["error"]


@pytest.mark.asyncio
async def test_a_scalar_body_is_a_400_not_a_500():
    status, body = await _post(_State(), "notadict")
    assert status == 400
    assert "object" in body["error"]


@pytest.mark.asyncio
async def test_a_wrong_typed_channel_id_is_400():
    status, body = await _post(_State(), {"channel_id": {"a": 1}})
    assert status == 400
    assert "channel_id" in body["error"]


@pytest.mark.asyncio
async def test_digest_non_numeric_hours_is_400_not_500():
    """The core of bug #23 — a bad hours param must be a clean 400."""
    status, body = await _post(_State(), {"channel_id": "C123", "hours": "abc"})
    assert status == 400
    assert "hours" in body["error"]


@pytest.mark.asyncio
async def test_a_boolean_hours_is_400():
    """`float(True)` is 1.0, so an unguarded cast would quietly summarize one hour."""
    status, body = await _post(_State(), {"channel_id": "C123", "hours": True})
    assert status == 400
    assert "hours" in body["error"]


@pytest.mark.asyncio
async def test_digest_non_positive_hours_is_400():
    assert (await _post(_State(), {"channel_id": "C123", "hours": 0}))[0] == 400
    assert (await _post(_State(), {"channel_id": "C123", "hours": -5}))[0] == 400


@pytest.mark.asyncio
async def test_digest_valid_hours_passes_param_gate():
    """A valid channel_id + hours gets past the param gate to the service check
    (which returns 400 'inbox not running' here since _inbox_svc is None) — proving
    the numeric parse succeeded rather than 500'ing."""
    status, body = await _post(_State(), {"channel_id": "C123", "hours": 4})
    assert status == 400
    assert "inbox not running" in body["error"]


@pytest.mark.asyncio
async def test_a_created_digest_answers_201_with_the_new_item(svc):
    """🔑 Creation semantics end to end: a new row exists afterwards, the response is a
    201 carrying it, and the live surface is told about it."""
    st = _State(svc)
    before = set(svc.inbox.items)
    status, body = await _post(st, {"channel_id": "C1", "hours": 4})
    assert status == 201
    created = set(svc.inbox.items) - before
    assert created == {body["id"]}
    assert svc.inbox.items[body["id"]].source == "digest"
    assert st.broadcasts and st.broadcasts[0][0] == "inbox_new_item"


@pytest.mark.asyncio
async def test_an_empty_window_is_still_a_404(svc):
    """Nothing was created, so nothing is returned — the 201 above is not unconditional."""
    st = _State(svc)
    status, _ = await _post(st, {"channel_id": "C-empty", "hours": 4})
    assert status == 404
    assert st.broadcasts == []


@pytest.mark.asyncio
async def test_two_digests_in_the_same_second_are_two_items(svc, monkeypatch):
    """🔑 The collision, pinned: with the clock frozen, `{channel}_digest_{int(ts)}` gave
    both digests the SAME id and the second overwrote the first in the store."""
    monkeypatch.setattr(svc_mod.time, "time", lambda: _FROZEN)
    st = _State(svc)
    first = (await _post(st, {"channel_id": "C1", "hours": 4}))[1]
    second = (await _post(st, {"channel_id": "C1", "hours": 4}))[1]

    assert first["id"] != second["id"]
    assert {first["id"], second["id"]} <= set(svc.inbox.items)
    assert (
        first["ts"] == second["ts"] == f"{_FROZEN:.6f}"
    ), "same second, both timestamps"


@pytest.mark.asyncio
async def test_many_same_second_digests_are_all_distinct(svc, monkeypatch):
    """Uniqueness is a property of the id, not luck in a pair."""
    monkeypatch.setattr(svc_mod.time, "time", lambda: _FROZEN)
    ids = [
        (await svc.generate_digest("C1", hours=4)).id  # type: ignore[union-attr]
        for _ in range(25)
    ]
    assert len(set(ids)) == 25
    assert len(svc.inbox.items) == 3 + 25


@pytest.mark.asyncio
async def test_a_digest_id_still_orders_by_creation_time(svc, monkeypatch):
    """🪤 Why the uuid8 is in the MIDDLE. `InboxItem.ts` rsplits the id on the last
    underscore, so a random TAIL would have made every digest sort as if it had no
    timestamp — the exact regression `make_item_id` documents."""
    clock = {"now": _FROZEN}
    monkeypatch.setattr(svc_mod.time, "time", lambda: clock["now"])
    items = []
    for step in range(4):
        clock["now"] = _FROZEN + step * 0.25
        item = await svc.generate_digest("C1", hours=4)
        assert item is not None
        items.append(item)

    stamps = [float(i.ts) for i in items]
    assert stamps == sorted(stamps)
    assert stamps == [_FROZEN + step * 0.25 for step in range(4)]
    assert [i.id for i in sorted(items, key=lambda i: float(i.ts))] == [
        i.id for i in items
    ]


@pytest.mark.asyncio
async def test_a_dismissed_digest_id_survives_pruning(svc):
    """The other reader of the id's tail: `prune_dismissed` drops any dismissed id whose
    last segment does not `float()`, so a non-numeric tail would evict a just-dismissed
    digest immediately."""
    item = await svc.generate_digest("C1", hours=4)
    assert item is not None
    state = InboxState()
    state.dismissed.add(item.id)
    assert state.prune_dismissed(retention_hours=1.0) == 0
    assert state.dismissed == {item.id}


def test_every_async_test_here_carries_the_asyncio_marker():
    """🪤 Vacuity floor: pytest-asyncio is in STRICT mode in this repo, so an async test
    with no marker is skipped with a warning and counted as a pass."""
    import inspect
    import sys

    module = sys.modules[__name__]
    unmarked = [
        name
        for name, fn in vars(module).items()
        if name.startswith("test_")
        and inspect.iscoroutinefunction(fn)
        and "asyncio" not in {m.name for m in getattr(fn, "pytestmark", [])}
    ]
    assert unmarked == [], f"async tests that would silently not run: {unmarked}"
