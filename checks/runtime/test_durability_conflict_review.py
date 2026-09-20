"""The conflict review surface: the resolve primitive and its two routes (DAS-10, §4.2).

Every conflict in this file is seeded through the REAL detector
(:func:`durability.conflicts.detect_conflicts`) against a REAL inventory entry, never by
hand-building a record. That is the vacuity floor for the whole file: an assertion that the
queue lists "no conflicts" would pass against a broken detector, a broken queue and an empty
home alike, so each list assertion pins a non-zero count of a record the detector produced.

Memory, knowledge and other data conflicts share the review controls in Backups, with
separate categories and counts. Each domain below is driven through detection and resolution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.operations.durability import conflict_resolve as resolver
from gideon.operations.durability import conflicts as conflicts_mod
from gideon.operations.durability import inventory as inv
from gideon.operations.durability import reconcile

_ENTRY_ID = "tasks"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home, pinned on the environment AND on `config_dir`.

    `service.active_home()` reads `GIDEON_HOME` first, so setting only `config_dir`
    would let a resolve write into the developer's real home.
    """
    h = tmp_path / "home"
    (h / "tasks").mkdir(parents=True)
    monkeypatch.setenv("GIDEON_HOME", str(h))
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: h)
    return h


def _write_task(home: Path, tid: str, title: str) -> None:
    (home / "tasks" / f"{tid}.json").write_text(json.dumps({"id": tid, "title": title}))


def _seed_conflict(
    home: Path,
    tid: str = "t1",
    *,
    ancestor: str = "shared",
    local: str = "mine",
    remote="theirs",
) -> conflicts_mod.ConflictRecord:
    """Produce ONE real both-sides-edited conflict on `tasks/{tid}.json` and queue it.

    Ancestor, local and remote all differ — the only shape §4.2 calls a conflict — and the
    record is produced by the detector rather than constructed, so a detector regression
    breaks every test that depends on this helper instead of silently passing.
    """
    entry = inv.by_id(_ENTRY_ID)
    assert entry is not None
    _write_task(home, tid, local)
    local_rows = reconcile.read_local_rows(entry, home / entry.path)
    remote_rows = [{"id": tid, "data": {"id": tid, "title": remote}}]
    ancestor_sha = conflicts_mod.row_sha(
        {"id": tid, "data": {"id": tid, "title": ancestor}}
    )
    found = conflicts_mod.detect_conflicts(
        entry, local_rows, remote_rows, {tid: ancestor_sha}, now="2026-08-17T00:00:00Z"
    )
    assert len(found) == 1, "the detector produced no conflict — the fixture is vacuous"
    assert conflicts_mod.ConflictQueue(home).record(found[0]) is True
    return found[0]


class TestResolvePrimitive:
    def test_keep_local_holds_the_local_row_and_marks_the_record_resolved(self, home):
        rec = _seed_conflict(home)
        out = resolver.resolve_conflict(
            home, rec.id, resolver.CHOICE_KEEP_LOCAL, now="NOW"
        )
        assert out.ok and out.code == ""
        assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "mine"
        stored = conflicts_mod.ConflictQueue(home).get(rec.id)
        assert stored is not None
        assert stored.status == conflicts_mod.STATUS_RESOLVED
        assert stored.resolution == resolver.CHOICE_KEEP_LOCAL
        assert stored.resolved_at == "NOW"

    def test_take_remote_writes_the_peers_row(self, home):
        rec = _seed_conflict(home)
        out = resolver.resolve_conflict(
            home, rec.id, resolver.CHOICE_TAKE_REMOTE, now="NOW"
        )
        assert out.ok and out.written == 1
        assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "theirs"

    def test_accept_proposal_writes_the_draft(self, home):
        rec = _seed_conflict(home)
        rec.proposal = {"id": "t1", "data": {"id": "t1", "title": "merged"}}
        assert conflicts_mod.ConflictQueue(home).update(rec) is True
        out = resolver.resolve_conflict(
            home, rec.id, resolver.CHOICE_ACCEPT_PROPOSAL, now="NOW"
        )
        assert out.ok
        assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "merged"

    def test_accept_proposal_refuses_when_no_draft_exists(self, home):
        """A failed LLM draft leaves `proposal=None` (§4.2). Accepting it must REFUSE rather
        than fall back to a version the user did not ask for."""
        rec = _seed_conflict(home)
        out = resolver.resolve_conflict(home, rec.id, resolver.CHOICE_ACCEPT_PROPOSAL)
        assert not out.ok and out.code == "no_version"
        assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "mine"
        assert (
            conflicts_mod.ConflictQueue(home).get(rec.id).status
            == conflicts_mod.STATUS_NEEDS_REVIEW
        )

    def test_typed_refusals(self, home):
        rec = _seed_conflict(home)
        assert (
            resolver.resolve_conflict(home, rec.id, "whatever").code == "unknown_choice"
        )
        assert resolver.resolve_conflict(home, "nope", "keep_local").code == "not_found"
        assert resolver.resolve_conflict(home, rec.id, "take_remote").ok
        second = resolver.resolve_conflict(home, rec.id, "take_remote")
        assert not second.ok and second.code == "already_resolved"

    def test_a_failed_store_write_leaves_the_review_open(self, home, monkeypatch):
        """The failure direction that matters: nothing applied, record still needs-review, and
        the caller told why — never a success whose store was not written."""
        rec = _seed_conflict(home)

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr("gideon.operations.durability.writeback.apply_rows", boom)
        out = resolver.resolve_conflict(home, rec.id, resolver.CHOICE_TAKE_REMOTE)
        assert not out.ok and out.code == "write_failed" and "disk full" in out.message
        assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "mine"
        assert (
            conflicts_mod.ConflictQueue(home).get(rec.id).status
            == conflicts_mod.STATUS_NEEDS_REVIEW
        )

    def test_resolving_one_row_does_not_truncate_the_store(self, home):
        """`writeback.apply_rows` writes the SET it is handed, so resolving must substitute
        into the full local row set. A single-row apply would delete every other task.
        """
        _write_task(home, "keep-a", "a")
        _write_task(home, "keep-b", "b")
        rec = _seed_conflict(home, "t1")
        assert resolver.resolve_conflict(home, rec.id, resolver.CHOICE_TAKE_REMOTE).ok
        names = sorted(p.stem for p in (home / "tasks").glob("*.json"))
        assert names == [
            "keep-a",
            "keep-b",
            "t1",
        ], "sibling rows were dropped by the resolve"

    def test_a_resolution_is_idempotent_when_repeated_after_a_lost_queue_update(
        self, home, monkeypatch
    ):
        """The recoverable-not-transactional claim, exercised: a store write that lands while
        the queue update fails reports a refusal and stays re-appliable."""
        rec = _seed_conflict(home)
        monkeypatch.setattr(
            conflicts_mod.ConflictQueue, "update", lambda self, r: False
        )
        first = resolver.resolve_conflict(home, rec.id, resolver.CHOICE_TAKE_REMOTE)
        assert not first.ok and first.code == "write_failed"
        assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "theirs"
        monkeypatch.undo()
        second = resolver.resolve_conflict(home, rec.id, resolver.CHOICE_TAKE_REMOTE)
        assert second.ok
        assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "theirs"


def _seed_document_conflict(home: Path, entry_id: str):
    entry = inv.by_id(entry_id)
    assert entry is not None and reconcile.handles_kind(entry.kind)
    path = home / entry.path
    if entry_id == "engagement":
        versions = [
            {"rows": {"reports": {"weight": weight, "updated_at": 100, "count": 2}}}
            for weight in (1.0, 1.2, 1.6)
        ]
    elif entry_id == "graph_maintenance":
        versions = [{"dirty_ts": stamp, "clean_ts": 0} for stamp in (1, 2, 3)]
    else:
        from gideon.automation.schedule import ScheduleDefinition
        from gideon.cognition.knowledge.research_reports import (
            ReportDefinition,
            to_dict,
        )

        versions = [
            [
                to_dict(
                    ReportDefinition(
                        id="report-1",
                        name=name,
                        prompt="Review the source documents",
                        schedule=ScheduleDefinition(kind="interval", every_secs=3600),
                    )
                )
            ]
            for name in ("Shared report", "Local report", "Remote report")
        ]
    rows = []
    for version in versions:
        path.write_text(json.dumps(version))
        rows.append(reconcile.read_local_rows(entry, path))
    ancestor, local, remote = rows
    path.write_text(json.dumps(versions[1]))
    found = conflicts_mod.detect_conflicts(
        entry,
        local,
        remote,
        {row["id"]: conflicts_mod.row_sha(row) for row in ancestor},
    )
    assert len(found) == 1
    assert conflicts_mod.ConflictQueue(home).record(found[0])
    return found[0], versions[2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry_id,surface",
    [
        ("engagement", conflicts_mod.SURFACE_MEMORY),
        ("graph_maintenance", conflicts_mod.SURFACE_KNOWLEDGE),
        ("research_reports", conflicts_mod.SURFACE_KNOWLEDGE),
    ],
)
async def test_memory_and_knowledge_conflicts_are_reviewable_and_resolvable(
    home, entry_id, surface
):
    record, remote = _seed_document_conflict(home, entry_id)
    assert record.surface == surface
    async with TestClient(TestServer(_app())) as client:
        response = await client.get(f"/api/durability/conflicts?surface={surface}")
        body = await response.json()
        assert response.status == 200
        assert [item["id"] for item in body["conflicts"]] == [record.id]
        assert body["counts"]["by_surface"][surface] == 1
        response = await client.post(
            f"/api/durability/conflicts/{record.id}/resolve",
            json={"choice": "take_remote", "confirm": True},
        )
        body = await response.json()
        assert response.status == 200 and body["ok"]
        assert body["conflict"]["status"] == conflicts_mod.STATUS_RESOLVED
        refreshed = await (
            await client.get(f"/api/durability/conflicts?surface={surface}")
        ).json()
        assert refreshed["counts"]["needs_review"] == 0
    entry = inv.by_id(entry_id)
    assert json.loads((home / entry.path).read_text()) == remote


def _app(*, app_token: str = "") -> web.Application:
    from gideon.interfaces.dashboard.handlers import durability as mod

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = app_token
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_get("/api/durability/conflicts", mod.api_durability_conflicts)
    app.router.add_post(
        "/api/durability/conflicts/{id}/resolve", mod.api_durability_conflict_resolve
    )
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/durability/conflicts"),
        ("post", "/api/durability/conflicts/abc/resolve"),
    ],
)
async def test_an_app_scoped_caller_is_refused(home, method, path):
    """A conflict record carries the user's rows from BOTH machines and a resolve rewrites
    one of them. Same least-privilege reading as the §6 four."""
    async with TestClient(TestServer(_app(app_token="notes"))) as client:
        resp = await getattr(client, method)(
            path, json={"choice": "keep_local", "confirm": True}
        )
        assert resp.status == 403
        assert (await resp.json())["error"]["code"] == "owner_only"


@pytest.mark.asyncio
async def test_the_list_returns_a_seeded_conflict_with_a_non_zero_count(home):
    rec = _seed_conflict(home)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/durability/conflicts")
        assert resp.status == 200
        body = await resp.json()
    assert body["counts"]["needs_review"] == 1, "the seeded conflict is not in the list"
    assert body["counts"]["by_surface"] == {conflicts_mod.SURFACE_DURABILITY: 1}
    ids = [c["id"] for c in body["conflicts"]]
    assert rec.id in ids
    one = body["conflicts"][0]
    assert one["local_row"]["data"]["title"] == "mine"
    assert one["remote_row"]["data"]["title"] == "theirs"
    assert one["status"] == conflicts_mod.STATUS_NEEDS_REVIEW
    assert body["sync"]["configured"] is False


@pytest.mark.asyncio
async def test_the_surface_filter_selects_without_hiding_the_others_count(home):
    """Criterion 9's separate-surfaces clause as the API expresses it: a filtered read still
    reports what waits on the surfaces it did not return."""
    rec = _seed_conflict(home)
    memory_rec, _ = _seed_document_conflict(home, "engagement")
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(
            f"/api/durability/conflicts?surface={conflicts_mod.SURFACE_DURABILITY}"
        )
        body = await resp.json()
    assert [c["id"] for c in body["conflicts"]] == [rec.id]
    assert body["counts"]["needs_review"] == 2
    assert body["counts"]["by_surface"][conflicts_mod.SURFACE_MEMORY] == 1
    assert body["counts"]["by_surface"][conflicts_mod.SURFACE_DURABILITY] == 1


@pytest.mark.asyncio
async def test_resolve_without_confirm_changes_nothing(home):
    rec = _seed_conflict(home)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            f"/api/durability/conflicts/{rec.id}/resolve",
            json={"choice": "take_remote"},
        )
        assert resp.status == 409
        assert (await resp.json())["error"]["code"] == "confirm_required"
    assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "mine"
    assert (
        conflicts_mod.ConflictQueue(home).get(rec.id).status
        == conflicts_mod.STATUS_NEEDS_REVIEW
    )


@pytest.mark.asyncio
async def test_a_confirmed_resolve_applies_and_echoes_the_choice(home):
    rec = _seed_conflict(home)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            f"/api/durability/conflicts/{rec.id}/resolve",
            json={"choice": "take_remote", "confirm": True},
        )
        assert resp.status == 200
        body = await resp.json()
    assert body["ok"] is True and body["choice"] == "take_remote"
    assert body["conflict"]["status"] == conflicts_mod.STATUS_RESOLVED
    assert json.loads((home / "tasks" / "t1.json").read_text())["title"] == "theirs"


@pytest.mark.asyncio
async def test_route_refusals_carry_their_own_status(home):
    rec = _seed_conflict(home)
    async with TestClient(TestServer(_app())) as client:
        bad = await client.post(
            f"/api/durability/conflicts/{rec.id}/resolve",
            json={"choice": "sideways", "confirm": True},
        )
        assert (
            bad.status == 400
            and (await bad.json())["error"]["code"] == "unknown_choice"
        )
        missing = await client.post(
            "/api/durability/conflicts/deadbeef/resolve",
            json={"choice": "keep_local", "confirm": True},
        )
        assert (
            missing.status == 404
            and (await missing.json())["error"]["code"] == "not_found"
        )
        undrafted = await client.post(
            f"/api/durability/conflicts/{rec.id}/resolve",
            json={"choice": "accept_proposal", "confirm": True},
        )
        assert undrafted.status == 409
        assert (await undrafted.json())["error"]["code"] == "no_version"
