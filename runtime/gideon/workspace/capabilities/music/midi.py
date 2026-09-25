"""Bounded monophonic PCM transcription, editable notes and canonical MIDI export."""

import hashlib
import io
import json
import math
import sqlite3
import struct
import wave
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np

from .store import DomainError, integer, text


def transcribe_pcm(raw):
    try:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or not 8000 <= rate <= 48000
                or not 0 < frames <= rate * 60
            ):
                raise ValueError(
                    "Requires mono 16-bit PCM WAV, 8–48 kHz, at most 60 seconds"
                )
            body = audio.readframes(frames)
            if len(body) != frames * 2:
                raise ValueError("Truncated WAV")
    except (wave.Error, EOFError, ValueError) as exc:
        raise DomainError(str(exc), 422, "unsupported_audio") from exc
    samples = np.frombuffer(body, dtype="<i2").astype(float) / 32768
    window, hop = int(rate * 0.08), int(rate * 0.01)
    size = 1 << (2 * window - 1).bit_length()
    notes, active = [], None
    for center in range(0, frames, hop):
        frame = samples[
            max(0, center - window // 2) : min(frames, center + window // 2)
        ]
        rms = float(np.sqrt(np.mean(frame * frame)))
        pitch = None
        if rms >= 0.01 and len(frame) >= window // 2:
            frame = frame - np.mean(frame)
            spectrum = np.fft.rfft(frame, n=size)
            correlation = np.fft.irfft(spectrum * spectrum.conjugate(), n=size)[
                : len(frame)
            ]
            correlation /= np.arange(len(frame), 0, -1)
            if correlation[0] > 0:
                correlation /= correlation[0]
                lo, hi = max(2, int(rate / 2200)), min(len(frame) - 2, int(rate / 60))
                peaks = [
                    lag
                    for lag in range(lo, hi)
                    if correlation[lag] > correlation[lag - 1]
                    and correlation[lag] >= correlation[lag + 1]
                ]
                if peaks:
                    best = max(correlation[lag] for lag in peaks)
                    peaks = [
                        lag for lag in peaks if correlation[lag] >= max(0.8, best * 0.9)
                    ]
                    if peaks:
                        lag = peaks[0]
                        a, b, c = correlation[lag - 1 : lag + 2]
                        refined = lag + (
                            float(0.5 * (a - c) / (a - 2 * b + c))
                            if a - 2 * b + c
                            else 0
                        )
                        candidate = round(69 + 12 * math.log2(rate / refined / 440))
                        if 36 <= candidate <= 96:
                            pitch = candidate
        timestamp = center / rate
        if active is not None and pitch != active["pitch"]:
            active["duration_seconds"] = round(timestamp - active["start_seconds"], 6)
            if active["duration_seconds"] >= 0.05:
                notes.append(active)
            active = None
        if pitch is not None and active is None:
            active = {
                "id": str(uuid4()),
                "pitch": pitch,
                "start_seconds": round(timestamp, 6),
                "duration_seconds": 0,
                "velocity": max(1, min(127, round(rms * 180))),
            }
    if active:
        active["duration_seconds"] = round(frames / rate - active["start_seconds"], 6)
        if active["duration_seconds"] >= 0.05:
            notes.append(active)
    return frames / rate, notes


def midi_bytes(notes, tempo):
    def variable(value):
        result = [value & 127]
        while value >> 7:
            value >>= 7
            result.insert(0, (value & 127) | 128)
        return bytes(result)

    events = []
    for note in notes:
        start = round(note["start_seconds"] * tempo * 8)
        end = max(
            start + 1,
            round((note["start_seconds"] + note["duration_seconds"]) * tempo * 8),
        )
        events += [
            (start, 1, bytes([0x90, note["pitch"], note["velocity"]])),
            (end, 0, bytes([0x80, note["pitch"], 0])),
        ]
    track = b"\x00\xff\x51\x03" + round(60000000 / tempo).to_bytes(3, "big")
    previous = 0
    for tick, _, message in sorted(events):
        track += variable(tick - previous) + message
        previous = tick
    track += b"\x00\xff\x2f\x00"
    return (
        b"MThd"
        + struct.pack(">IHHH", 6, 0, 1, 480)
        + b"MTrk"
        + struct.pack(">I", len(track))
        + track
    )


class MidiStore:
    def __init__(self, root, catalog):
        self.root, self.catalog = Path(root), catalog
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "midi.sqlite3"
        with self._db() as db:
            db.execute("PRAGMA user_version=1")
            db.execute(
                "CREATE TABLE IF NOT EXISTS scores(id TEXT PRIMARY KEY,payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS exports(id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(id,revision))"
            )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _get(self, db, item_id):
        row = db.execute(
            "SELECT payload FROM scores WHERE id=?", (text(item_id, "id", 100, True),)
        ).fetchone()
        if not row:
            raise DomainError("Transcription not found", 404, "not_found")
        return json.loads(row[0])

    def get(self, item_id):
        with self._db() as db:
            return self._get(db, item_id)

    def list(self, offset=0, limit=50):
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 100)
        with self._db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM scores ORDER BY rowid LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def transcribe(self, data):
        if not isinstance(data, dict) or set(data) != {
            "request_id",
            "track_id",
            "render_id",
            "title",
            "tempo_bpm",
        }:
            raise DomainError(
                "Transcription requires request_id, track_id, render_id, title and tempo_bpm"
            )
        for key in ("request_id", "track_id", "render_id", "title"):
            text(data[key], key, 200, True)
        integer(data["tempo_bpm"], "tempo_bpm", 20, 300)
        fingerprint = hashlib.sha256(
            json.dumps(data, sort_keys=True).encode()
        ).hexdigest()
        with self._db() as db:
            prior = db.execute(
                "SELECT fingerprint,payload FROM requests WHERE id=?",
                (data["request_id"],),
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError(
                        "Request ID already has different input",
                        409,
                        "request_conflict",
                    )
                return json.loads(prior[1])
            track = self.catalog.get("tracks", data["track_id"])
            render = next(
                (row for row in track["renders"] if row["id"] == data["render_id"]),
                None,
            )
            if not render:
                raise DomainError("Render not attached to track", 404, "not_found")
            ref = render["artifact_ref"]
            raw = self.catalog.artifacts.raw_bytes(ref["slug"], version=ref["version"])
            if not raw:
                raise DomainError(
                    "Source recording unavailable", 404, "artifact_not_found"
                )
            if raw[1] != "audio/wav":
                raise DomainError(
                    "Monophonic transcription requires PCM WAV",
                    422,
                    "unsupported_audio",
                )
            duration, notes = transcribe_pcm(raw[0])
            item = {
                "id": str(uuid4()),
                "title": data["title"],
                "tempo_bpm": data["tempo_bpm"],
                "revision": 1,
                "source_ref": {
                    "track_id": track["id"],
                    "render_id": render["id"],
                    "artifact_ref": ref,
                },
                "duration_seconds": duration,
                "method": "monophonic_autocorrelation_v1",
                "notes": notes,
            }
            encoded = json.dumps(item)
            db.execute("INSERT INTO scores VALUES (?,?)", (item["id"], encoded))
            db.execute(
                "INSERT INTO requests VALUES (?,?,?)",
                (data["request_id"], fingerprint, encoded),
            )
            return item

    def _revision(self, item, revision):
        integer(revision, "revision", 1, 1000000)
        if revision != item["revision"]:
            raise DomainError(
                "Transcription changed; reload before editing", 409, "revision_conflict"
            )

    def update(self, item_id, data):
        if not isinstance(data, dict) or set(data) != {"revision", "title", "notes"}:
            raise DomainError("Edit requires revision, title and notes")
        with self._db() as db:
            item = self._get(db, item_id)
            self._revision(item, data["revision"])
            title = text(data["title"], "title", 200, True)
            notes = data["notes"]
            if not isinstance(notes, list) or len(notes) > 2000:
                raise DomainError("At most 2000 notes allowed")
            ids = set()
            for note in notes:
                if not isinstance(note, dict) or set(note) != {
                    "id",
                    "pitch",
                    "start_seconds",
                    "duration_seconds",
                    "velocity",
                }:
                    raise DomainError("Invalid note fields")
                note_id = text(note["id"], "note id", 100, True)
                if note_id in ids:
                    raise DomainError("Duplicate note ID")
                ids.add(note_id)
                integer(note["pitch"], "pitch", 36, 96)
                integer(note["velocity"], "velocity", 1, 127)
                for key in ("start_seconds", "duration_seconds"):
                    if (
                        type(note[key]) not in (float, int)
                        or not math.isfinite(note[key])
                        or note[key] < (0 if key == "start_seconds" else 0.01)
                    ):
                        raise DomainError("Invalid note timing")
                if (
                    note["start_seconds"] + note["duration_seconds"]
                    > item["duration_seconds"] + 0.000001
                ):
                    raise DomainError("Note extends beyond source recording")
            item.update(title=title, notes=notes, revision=item["revision"] + 1)
            db.execute(
                "UPDATE scores SET payload=? WHERE id=?", (json.dumps(item), item_id)
            )
            return item

    def export(self, item_id, data):
        if not isinstance(data, dict) or set(data) != {"revision"}:
            raise DomainError("Export requires saved revision")
        with self._db() as db:
            item = self._get(db, item_id)
            self._revision(item, data["revision"])
            prior = db.execute(
                "SELECT payload FROM exports WHERE id=? AND revision=?",
                (item_id, item["revision"]),
            ).fetchone()
            if prior:
                result = json.loads(prior[0])
                ref = result["artifact_ref"]
                if not self.catalog.artifacts.raw_bytes(
                    ref["slug"], version=ref["version"]
                ):
                    raise DomainError(
                        "Export artifact missing", 404, "artifact_not_found"
                    )
                return result
            raw = midi_bytes(item["notes"], item["tempo_bpm"])
            slug = "midi-" + item_id + "-" + str(item["revision"])
            artifact = self.catalog.artifacts.get(slug)
            if artifact:
                if self.catalog.artifacts.raw_bytes(slug, version=1) != (
                    raw,
                    "audio/midi",
                ):
                    raise DomainError(
                        "Export artifact collision", 409, "artifact_conflict"
                    )
            else:
                artifact = self.catalog.artifacts.create_binary(
                    name=item["title"],
                    slug=slug,
                    data=raw,
                    mime="audio/midi",
                    kind="audio",
                    source="manual",
                )
            result = {
                "artifact_ref": {"slug": slug, "version": 1},
                "revision": item["revision"],
            }
            db.execute(
                "INSERT INTO exports VALUES (?,?,?)",
                (item_id, item["revision"], json.dumps(result)),
            )
            return result
