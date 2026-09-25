import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from checks.runtime.capabilities.music.test_catalog import (
    artifact,
    attach_body,
    catalog_at,
)
from gideon.interfaces.dashboard.handlers.capabilities_music_rounds import register
from gideon.workspace.capabilities.music.rounds import RoundStore
from gideon.workspace.capabilities.music.rounds_tools import RoundTools
from gideon.workspace.capabilities.music.store import DomainError


def store_at(home):
    return RoundStore(home / "music", catalog_at(home))


def part(part_id="voice-a", entry=0):
    return {
        "id": part_id,
        "name": part_id,
        "entry_beats": entry,
        "notation": "D E F# G",
        "catalog_ref": None,
    }


def create(store, title="Evening round", **values):
    return store.create(
        {
            "title": title,
            "tempo_bpm": 120,
            "meter_beats": 4,
            "parts": [part(), part("voice-b", 4)],
            **values,
        }
    )


def practice(item, request_id="take-one", **values):
    return {
        "request_id": request_id,
        "round_revision": item["revision"],
        "part_ids": [item["parts"][0]["id"]],
        "occurred_at": "2026-09-25T12:00:00+02:00",
        "grade": 4,
        "notes": "Entry was together",
        **values,
    }


def test_authored_parts_and_entry_timing_persist(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    assert item["tempo_bpm"] == 120
    assert item["meter_beats"] == 4
    assert item["parts"][0]["entry_beats"] == 0
    assert item["parts"][1]["entry_beats"] == 4
    assert item["parts"][1]["entry_beats"] * 60 / item["tempo_bpm"] == 2
    assert item["parts"][0]["notation"] == "D E F# G"
    assert item["parts"][0]["catalog_ref"] is None
    assert store_at(tmp_path).get(item["id"]) == item
    assert store.history(item["id"]) == []


def test_omitted_optional_audio_ref_normalizes_to_null(tmp_path):
    store = store_at(tmp_path)
    voice = part()
    del voice["catalog_ref"]
    item = create(store, parts=[voice])
    assert item["parts"][0]["catalog_ref"] is None
    assert store_at(tmp_path).get(item["id"])["parts"][0]["catalog_ref"] is None


def test_actual_imported_render_can_be_pinned_without_copying_bytes(tmp_path):
    store = store_at(tmp_path)
    track = store.catalog.create("tracks", {"title": "Recorded voice"})
    audio = artifact(store.catalog)
    attached = store.catalog.attach(track["id"], attach_body(track, audio))
    ref = {"track_id": track["id"], "render_id": attached["renders"][0]["id"]}
    item = create(store, parts=[{**part(), "catalog_ref": ref}])
    assert item["parts"][0]["catalog_ref"] == ref
    assert store.catalog.get("tracks", track["id"]) == attached
    assert store_at(tmp_path).get(item["id"])["parts"][0]["catalog_ref"] == ref
    with sqlite3.connect(store.path) as db:
        payload = db.execute("SELECT payload FROM rounds").fetchone()[0]
    assert audio.slug not in payload
    assert ref["render_id"] in payload


def test_partner_rounds_require_matching_tempo_and_meter(tmp_path):
    store = store_at(tmp_path)
    first = create(store, "First")
    second = create(store, "Second", partner_ids=[first["id"]])
    assert second["partner_ids"] == [first["id"]]
    with pytest.raises(DomainError) as err:
        create(store, "Wrong tempo", tempo_bpm=90, partner_ids=[first["id"]])
    assert err.value.code == "incompatible_partner"
    with pytest.raises(DomainError):
        create(store, "Wrong meter", meter_beats=3, partner_ids=[first["id"]])
    assert len(store.list()) == 2


def test_editing_partner_cannot_break_inbound_compatibility(tmp_path):
    store = store_at(tmp_path)
    first = create(store, "First")
    second = create(store, "Second", partner_ids=[first["id"]])
    with pytest.raises(DomainError) as err:
        store.update(first["id"], {"revision": 1, "tempo_bpm": 60})
    assert err.value.code == "incompatible_partner"
    assert store.get(first["id"]) == first
    assert store.get(second["id"]) == second
    detached = store.update(second["id"], {"revision": 1, "partner_ids": []})
    changed = store.update(first["id"], {"revision": 1, "tempo_bpm": 60})
    assert detached["partner_ids"] == []
    assert changed["tempo_bpm"] == 60


def test_practice_is_idempotent_and_snapshot_survives_part_edits(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    request = practice(item)
    receipt = store.practice(item["id"], request)
    assert receipt["replayed"] is False
    assert receipt["item"]["occurred_at"] == "2026-09-25T10:00:00+00:00"
    assert receipt["item"]["part_snapshot"] == [item["parts"][0]]
    changed = store.update(
        item["id"],
        {"revision": 1, "parts": [{**part("new-voice"), "notation": "A B C"}]},
    )
    assert changed["revision"] == 2
    replayed = store_at(tmp_path).practice(item["id"], request)
    assert replayed == {**receipt, "replayed": True}
    assert store.history(item["id"]) == [receipt["item"]]
    assert store.history(item["id"])[0]["part_snapshot"][0]["notation"] == "D E F# G"
    with sqlite3.connect(store.path) as db:
        original = json.loads(
            db.execute(
                "SELECT payload FROM versions WHERE id=? AND revision=1", (item["id"],)
            ).fetchone()[0]
        )
    assert original == item


def test_practice_request_id_cannot_change_grade_or_target_round(tmp_path):
    store = store_at(tmp_path)
    one, two = create(store, "One"), create(store, "Two")
    request = practice(one)
    result = store.practice(one["id"], request)
    with pytest.raises(DomainError) as err:
        store.practice(one["id"], {**request, "grade": 1})
    assert err.value.code == "request_conflict"
    with pytest.raises(DomainError):
        store.practice(two["id"], request)
    assert store.history(two["id"]) == []
    assert store.history(one["id"]) == [result["item"]]


def test_parallel_retry_records_one_practice(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    request = practice(item)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: store_at(tmp_path).practice(item["id"], request), range(2)
            )
        )
    assert sorted(row["replayed"] for row in results) == [False, True]
    assert results[0]["item"] == results[1]["item"]
    assert len(store.history(item["id"])) == 1


def test_grade_zero_is_recorded_as_failure_not_missing(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    result = store.practice(
        item["id"], practice(item, grade=0, part_ids=["voice-a", "voice-b"])
    )
    assert result["item"]["grade"] == 0
    assert result["item"]["part_ids"] == ["voice-a", "voice-b"]
    assert len(result["item"]["part_snapshot"]) == 2
    assert store_at(tmp_path).history(item["id"])[0]["grade"] == 0


@pytest.mark.parametrize(
    "values",
    [
        {"title": ""},
        {"tempo_bpm": 19},
        {"tempo_bpm": 301},
        {"tempo_bpm": True},
        {"meter_beats": 0},
        {"meter_beats": 17},
        {"parts": []},
        {"parts": [part()] * 17},
        {"parts": [part(), part()]},
        {"parts": [{**part(), "entry_beats": -1}]},
        {"parts": [{**part(), "entry_beats": float("nan")}]},
        {"parts": [{**part(), "entry_beats": float("inf")}]},
        {"parts": [{**part(), "entry_beats": True}]},
        {"parts": [{**part(), "path": "/tmp"}]},
        {"parts": [{**part(), "catalog_ref": {"artifact_ref": "unbound"}}]},
        {"partner_ids": ["missing"]},
        {"partner_ids": [False]},
        {"revision": 9},
        {"history": []},
    ],
)
def test_invalid_rounds_leave_no_partial_record(tmp_path, values):
    store = store_at(tmp_path)
    with pytest.raises(DomainError):
        create(store, **values)
    assert store.list() == []


@pytest.mark.parametrize(
    "values",
    [
        {"grade": -1},
        {"grade": 6},
        {"grade": True},
        {"part_ids": []},
        {"part_ids": ["missing"]},
        {"part_ids": ["voice-a", "voice-a"]},
        {"part_ids": [None]},
        {"occurred_at": "2026-09-25T12:00:00"},
        {"occurred_at": "yesterday"},
        {"request_id": ""},
        {"round_revision": 0},
        {"notes": None},
        {"home": "/tmp"},
    ],
)
def test_invalid_practice_does_not_create_history(tmp_path, values):
    store = store_at(tmp_path)
    item = create(store)
    with pytest.raises(DomainError):
        store.practice(item["id"], practice(item, **values))
    assert store.history(item["id"]) == []
    assert store.get(item["id"]) == item


def test_stale_edit_and_practice_refused(tmp_path):
    store = store_at(tmp_path)
    item = create(store)
    updated = store.update(item["id"], {"revision": 1, "notes": "Saved revision"})
    with pytest.raises(DomainError) as err:
        store.update(item["id"], {"revision": 1, "notes": "Stale"})
    assert err.value.code == "revision_conflict"
    with pytest.raises(DomainError) as err:
        store.practice(item["id"], practice(item))
    assert err.value.code == "revision_conflict"
    assert store.get(item["id"]) == updated
    assert store.history(item["id"]) == []


def test_two_homes_cannot_share_rounds_part_audio_or_practice(tmp_path):
    one, two = store_at(tmp_path / "one"), store_at(tmp_path / "two")
    item = create(one)
    track = one.catalog.create("tracks", {"title": "Local recording"})
    audio = artifact(one.catalog)
    attached = one.catalog.attach(track["id"], attach_body(track, audio))
    ref = {"track_id": track["id"], "render_id": attached["renders"][0]["id"]}
    with pytest.raises(DomainError):
        create(two, parts=[{**part(), "catalog_ref": ref}])
    with pytest.raises(DomainError):
        two.practice(item["id"], practice(item))
    assert two.list() == []
    assert one.history(item["id"]) == []


@pytest.mark.asyncio
async def test_actual_http_round_author_edit_practice_replay_and_history(tmp_path):
    store = store_at(tmp_path)
    app = web.Application()
    register(app, store)
    prefix = "/api/capabilities/music/rounds"
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            prefix, json={"title": "HTTP canon", "parts": [part()]}
        )
        assert response.status == 201
        item = (await response.json())["item"]
        response = await client.post(
            prefix + "/" + item["id"] + "/practice", json=practice(item)
        )
        assert response.status == 200
        receipt = await response.json()
        retry = await client.post(
            prefix + "/" + item["id"] + "/practice", json=practice(item)
        )
        assert (await retry.json()) == {**receipt, "replayed": True}
        history = await client.get(prefix + "/" + item["id"] + "/practice")
        assert (await history.json())["items"] == [receipt["item"]]
        changed = await client.patch(
            prefix + "/" + item["id"], json={"revision": 1, "notes": "Edited notes"}
        )
        assert changed.status == 200
        missing = await client.get(prefix + "/missing")
        assert missing.status == 404
        invalid = await client.get(prefix + "?limit=101")
        assert invalid.status == 400
    assert store_at(tmp_path).get(item["id"])["notes"] == "Edited notes"


@pytest.mark.asyncio
async def test_native_round_tools_use_actual_persisted_domain(tmp_path):
    store = store_at(tmp_path)
    tools = RoundTools(store)
    assert len(await tools.list_tools()) == 6
    created = await tools.invoke(
        "music_rounds_create", {"data": {"title": "Native canon", "parts": [part()]}}
    )
    assert created.success
    item = json.loads(created.output)
    result = await tools.invoke(
        "music_rounds_practice", {"id": item["id"], "data": practice(item)}
    )
    assert result.success
    history = await tools.invoke("music_rounds_history", {"id": item["id"]})
    assert json.loads(history.output) == [json.loads(result.output)["item"]]
    changed = await tools.invoke(
        "music_rounds_update",
        {"id": item["id"], "data": {"revision": 1, "notes": "Native edit"}},
    )
    assert changed.success
    fetched = await tools.invoke("music_rounds_get", {"id": item["id"]})
    assert json.loads(fetched.output)["notes"] == "Native edit"
    listing = await tools.invoke("music_rounds_list", {})
    assert len(json.loads(listing.output)) == 1
    denied = await tools.invoke("music_rounds_list", {"home": "/tmp"})
    assert denied.success is False
