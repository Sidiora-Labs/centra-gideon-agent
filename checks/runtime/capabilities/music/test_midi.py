import io
import json
import math
import sqlite3
import struct
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from checks.runtime.capabilities.music.test_catalog import attach_body, catalog_at
from gideon.interfaces.dashboard.handlers.capabilities_music_midi import register
from gideon.workspace.artifacts.models import kind_for_mime, mime_for_ext
from gideon.workspace.capabilities.music.midi import (
    MidiStore,
    midi_bytes,
    transcribe_pcm,
)
from gideon.workspace.capabilities.music.midi_tools import MidiTools
from gideon.workspace.capabilities.music.store import DomainError


def recording(
    sequence=((69, 0.5), (None, 0.15), (72, 0.5)),
    rate=16000,
    harmonic=False,
    channels=1,
    width=2,
):
    pieces = []
    for pitch, duration in sequence:
        t = np.arange(round(rate * duration)) / rate
        frequency = 440 * 2 ** ((pitch - 69) / 12) if pitch is not None else 0
        signal = 0.35 * np.sin(2 * np.pi * frequency * t)
        if harmonic:
            signal += 0.2 * np.sin(4 * np.pi * frequency * t)
        pieces.append(signal)
    samples = np.concatenate(pieces)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        body = (
            (samples * 32767).astype("<i2").tobytes()
            if width == 2
            else bytes([128]) * len(samples)
        )
        audio.writeframes(body * channels)
    return output.getvalue()


def store_at(home):
    return MidiStore(home / "music", catalog_at(home))


def seeded(home, raw=None):
    store = store_at(home)
    catalog = store.catalog
    track = catalog.create("tracks", {"title": "Real PCM notes"})
    artifact = catalog.artifacts.create_binary(
        name="Real pitched PCM",
        data=raw or recording(),
        mime="audio/wav",
        kind="audio",
        source="import",
    )
    attached = catalog.attach(track["id"], attach_body(track, artifact))
    data = {
        "request_id": "transcribe-one",
        "track_id": track["id"],
        "render_id": attached["renders"][0]["id"],
        "title": "Detected phrase",
        "tempo_bpm": 120,
    }
    return store, data


def read_midi(raw):
    assert raw[:4] == b"MThd"
    assert struct.unpack(">IHHH", raw[4:14]) == (6, 0, 1, 480)
    assert raw[14:18] == b"MTrk"
    length = struct.unpack(">I", raw[18:22])[0]
    assert len(raw) == 22 + length
    body = io.BytesIO(raw[22:])
    events = []
    tick = 0
    while body.tell() < length:
        delta = 0
        while True:
            octet = body.read(1)[0]
            delta = (delta << 7) | (octet & 127)
            if not octet & 128:
                break
        tick += delta
        status = body.read(1)[0]
        if status == 255:
            meta, size = body.read(2)
            value = body.read(size)
            events.append((tick, "meta", meta, value))
        else:
            pitch, velocity = body.read(2)
            events.append((tick, status, pitch, velocity))
    assert events[-1][1:] == ("meta", 47, b"")
    return events


@pytest.mark.parametrize("pitch", [36, 48, 60, 69, 72, 84, 96])
def test_actual_sinusoidal_pcm_pitch_is_measured(pitch):
    duration, notes = transcribe_pcm(recording(((pitch, 0.6),)))
    assert duration == pytest.approx(0.6)
    assert len(notes) == 1
    assert notes[0]["pitch"] == pitch
    assert notes[0]["start_seconds"] == pytest.approx(0, abs=0.03)
    assert notes[0]["duration_seconds"] == pytest.approx(0.6, abs=0.03)
    assert 1 <= notes[0]["velocity"] <= 127


@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000])
def test_phrase_pitch_silence_and_timing_at_supported_rates(rate):
    duration, notes = transcribe_pcm(recording(rate=rate))
    assert duration == pytest.approx(1.15)
    assert [note["pitch"] for note in notes] == [69, 72]
    assert notes[0]["start_seconds"] == pytest.approx(0, abs=0.04)
    assert notes[0]["duration_seconds"] == pytest.approx(0.5, abs=0.06)
    assert notes[1]["start_seconds"] == pytest.approx(0.65, abs=0.06)
    assert notes[1]["duration_seconds"] == pytest.approx(0.5, abs=0.06)


def test_harmonic_recording_retains_fundamental_and_silence_stays_empty():
    _, notes = transcribe_pcm(recording(((57, 0.7),), harmonic=True))
    assert [note["pitch"] for note in notes] == [57]
    assert transcribe_pcm(recording(((None, 0.6),)))[1] == []


@pytest.mark.parametrize(
    "raw",
    [
        b"not wav",
        recording(channels=2),
        recording(width=1),
        recording(rate=4000),
        recording(((60, 60.01),)),
        recording()[:-10],
    ],
)
def test_unsupported_or_truncated_recordings_rejected(raw):
    with pytest.raises(DomainError) as err:
        transcribe_pcm(raw)
    assert err.value.code == "unsupported_audio"


def test_transcription_persists_source_method_and_immutable_retry(tmp_path):
    store, data = seeded(tmp_path)
    item = store.transcribe(data)
    assert item["method"] == "monophonic_autocorrelation_v1"
    assert item["source_ref"]["track_id"] == data["track_id"]
    assert item["source_ref"]["render_id"] == data["render_id"]
    assert item["source_ref"]["artifact_ref"]["version"] == 1
    assert [note["pitch"] for note in item["notes"]] == [69, 72]
    assert store_at(tmp_path).get(item["id"]) == item
    updated = store.update(item["id"], {"revision": 1, "title": "Edited", "notes": []})
    assert updated["revision"] == 2
    assert updated["notes"] == []
    assert store_at(tmp_path).transcribe(data) == item
    assert store.get(item["id"]) == updated
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_request_conflict_does_not_add_or_change_transcriptions(tmp_path):
    store, data = seeded(tmp_path)
    item = store.transcribe(data)
    with pytest.raises(DomainError) as err:
        store.transcribe({**data, "title": "Changed request"})
    assert err.value.code == "request_conflict"
    assert store.list() == [item]
    assert store.list(offset=1) == []


def test_concurrent_exact_retries_return_one_item(tmp_path):
    store, data = seeded(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: store_at(tmp_path).transcribe(data), range(2))
        )
    assert results[0] == results[1]
    assert store.list() == [results[0]]


@pytest.mark.parametrize(
    "changes",
    [
        {"pitch": 35},
        {"pitch": 97},
        {"pitch": True},
        {"velocity": 0},
        {"velocity": 128},
        {"start_seconds": -1},
        {"start_seconds": math.inf},
        {"duration_seconds": math.nan},
        {"duration_seconds": 0},
        {"duration_seconds": 2},
        {"id": ""},
        {"home": "/tmp"},
    ],
)
def test_bad_note_edits_leave_original_saved_score(tmp_path, changes):
    store, data = seeded(tmp_path)
    item = store.transcribe(data)
    note = {**item["notes"][0], **changes}
    with pytest.raises(DomainError):
        store.update(item["id"], {"revision": 1, "title": "Edit", "notes": [note]})
    assert store.get(item["id"]) == item


def test_duplicate_ids_stale_revision_and_unknown_fields_refused(tmp_path):
    store, data = seeded(tmp_path)
    item = store.transcribe(data)
    for payload in (
        {"revision": 1, "title": "Duplicate", "notes": [item["notes"][0]] * 2},
        {"revision": 2, "title": "Stale", "notes": []},
        {"revision": 1, "title": "Injected", "notes": [], "model": "other"},
    ):
        with pytest.raises(DomainError):
            store.update(item["id"], payload)
    assert store.list() == [item]


def test_export_has_actual_midi_tempo_pitch_timing_and_canonical_identity(tmp_path):
    store, data = seeded(tmp_path)
    item = store.transcribe(data)
    edited = [
        {
            "id": "note-a",
            "pitch": 60,
            "start_seconds": 0.1,
            "duration_seconds": 0.4,
            "velocity": 91,
        },
        {
            "id": "note-b",
            "pitch": 64,
            "start_seconds": 0.5,
            "duration_seconds": 0.3,
            "velocity": 87,
        },
    ]
    item = store.update(
        item["id"], {"revision": 1, "title": "Edited MIDI", "notes": edited}
    )
    result = store.export(item["id"], {"revision": 2})
    ref = result["artifact_ref"]
    raw, mime = store.catalog.artifacts.raw_bytes(ref["slug"], version=ref["version"])
    assert mime == "audio/midi"
    assert kind_for_mime(mime) == "audio"
    assert mime_for_ext("mid") == mime
    events = read_midi(raw)
    assert events[0] == (0, "meta", 81, b"\x07\xa1\x20")
    assert events[1] == (96, 0x90, 60, 91)
    assert events[2] == (480, 0x80, 60, 0)
    assert events[3] == (480, 0x90, 64, 87)
    assert events[4] == (768, 0x80, 64, 0)
    assert store_at(tmp_path).export(item["id"], {"revision": 2}) == result
    assert store.catalog.artifacts.get(ref["slug"], version=1).kind == "audio"
    changed = store.update(item["id"], {"revision": 2, "title": "Third", "notes": []})
    newer = store.export(item["id"], {"revision": 3})
    assert newer["artifact_ref"] != ref
    assert store.catalog.artifacts.raw_bytes(ref["slug"], version=1)[0] == raw
    assert changed["revision"] == 3
    assert (
        len(
            read_midi(
                store.catalog.artifacts.raw_bytes(
                    newer["artifact_ref"]["slug"], version=1
                )[0]
            )
        )
        == 2
    )


def test_midi_variable_length_deltas_and_nondefault_tempo():
    raw = midi_bytes(
        [{"pitch": 72, "start_seconds": 30, "duration_seconds": 2, "velocity": 127}], 90
    )
    events = read_midi(raw)
    assert events[0][3] == round(60000000 / 90).to_bytes(3, "big")
    assert events[1][:3] == (21600, 0x90, 72)
    assert events[2][:3] == (23040, 0x80, 72)


def test_foreign_home_and_unknown_sources_cannot_transcribe(tmp_path):
    store, data = seeded(tmp_path / "one")
    other = store_at(tmp_path / "two")
    with pytest.raises(DomainError):
        other.transcribe(data)
    with pytest.raises(DomainError):
        store.transcribe({**data, "render_id": "foreign"})
    with pytest.raises(DomainError):
        store.transcribe({**data, "home": "/tmp"})
    assert store.list() == []
    assert other.list() == []


@pytest.mark.asyncio
async def test_real_http_transcribe_edit_export_reload_and_conflict(tmp_path):
    store, data = seeded(tmp_path)
    app = web.Application()
    register(app, store)
    prefix = "/api/capabilities/music/midi"
    async with TestClient(TestServer(app)) as client:
        response = await client.post(prefix, json=data)
        assert response.status == 201
        item = (await response.json())["item"]
        retry = await client.post(prefix, json=data)
        assert (await retry.json())["item"] == item
        edited = await client.patch(
            prefix + "/" + item["id"],
            json={"revision": 1, "title": "HTTP edit", "notes": []},
        )
        assert edited.status == 200
        assert (await edited.json())["item"]["revision"] == 2
        stale = await client.post(
            prefix + "/" + item["id"] + "/export", json={"revision": 1}
        )
        assert stale.status == 409
        export = await client.post(
            prefix + "/" + item["id"] + "/export", json={"revision": 2}
        )
        assert export.status == 200
        ref = (await export.json())["artifact_ref"]
        assert store.catalog.artifacts.raw_bytes(ref["slug"], version=ref["version"])[
            0
        ].startswith(b"MThd")
        listing = await client.get(prefix)
        assert len((await listing.json())["items"]) == 1
        invalid = await client.get(prefix + "?limit=101")
        assert invalid.status == 400
    assert store_at(tmp_path).get(item["id"])["title"] == "HTTP edit"


@pytest.mark.asyncio
async def test_real_native_operations_and_no_caller_home(tmp_path):
    store, data = seeded(tmp_path)
    tools = MidiTools(store)
    assert len(await tools.list_tools()) == 5
    created = await tools.invoke("music_midi_transcribe", {"data": data})
    assert created.success
    item = json.loads(created.output)
    fetched = await tools.invoke("music_midi_get", {"id": item["id"]})
    assert json.loads(fetched.output) == item
    listing = await tools.invoke("music_midi_list", {})
    assert json.loads(listing.output) == [item]
    changed = await tools.invoke(
        "music_midi_update",
        {
            "id": item["id"],
            "data": {"revision": 1, "title": "Native edit", "notes": []},
        },
    )
    assert changed.success
    exported = await tools.invoke(
        "music_midi_export", {"id": item["id"], "data": {"revision": 2}}
    )
    assert exported.success
    assert json.loads(exported.output)["revision"] == 2
    denied = await tools.invoke("music_midi_list", {"home": "/tmp"})
    assert not denied.success
    assert store_at(tmp_path).get(item["id"])["title"] == "Native edit"
