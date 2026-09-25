import asyncio
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from .store import Conflict, NotFound


class NativeDuplex:
    def __init__(self, store, *, platform=None, xcrun=None):
        self.store = store
        self.home = store.path.parent.parent.resolve()
        self.platform = platform or sys.platform
        self.xcrun = xcrun or shutil.which("xcrun")
        self.source = Path(__file__).parent / "assets" / "native_duplex.swift"
        self.lock = asyncio.Lock()
        with store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS native_duplex_sessions(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,call_id TEXT,state TEXT,revision INTEGER,max_turns INTEGER,capture_seconds INTEGER,turns TEXT,error TEXT,created_at REAL,updated_at REAL)"
            )
            db.execute(
                "UPDATE native_duplex_sessions SET state='interrupted',error='Runtime stopped during native duplex activity; actual device state is unknown',revision=revision+1 WHERE state IN ('capturing','speaking')"
            )

    def readiness(self):
        errors = []
        if self.platform != "darwin":
            errors.append("Native duplex audio requires macOS")
        if not self.xcrun:
            errors.append("Apple xcrun is unavailable")
        if not self.source.is_file():
            errors.append("Bundled AVFoundation helper source is missing")
        return {
            "available": not errors,
            "errors": errors,
            "capture": "AVFoundation microphone PCM",
            "playback": "AVFoundation audio player",
            "stt": "configured Gideon STT",
            "tts": "configured Gideon TTS",
            "device_qualification": "unverified",
        }

    def _row(self, row):
        value = dict(row)
        value["turns"] = json.loads(value["turns"])
        return value

    def list(self):
        with self.store.connection() as db:
            db.row_factory = sqlite3.Row
            return [
                self._row(row)
                for row in db.execute(
                    "SELECT * FROM native_duplex_sessions ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def get(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ValueError("Invalid duplex session identifier")
        with self.store.connection() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM native_duplex_sessions WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise NotFound("Native duplex session not found")
        return self._row(row)

    def _call(self, call_id):
        if not isinstance(call_id, str) or not re.fullmatch(r"[a-f0-9]{32}", call_id):
            raise ValueError("Select an existing native call request")
        with self.store.connection() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM native_call_requests WHERE id=?", (call_id,)
            ).fetchone()
        if row is None:
            raise NotFound("Native call request not found")
        if row["command"] == "probe" or row["state"] in (
            "failed",
            "unavailable",
            "idle",
            "interrupted",
        ):
            raise Conflict(
                "Duplex audio requires a nonterminal call request; it never initiates the call"
            )
        return dict(row)

    def start(self, body):
        if not isinstance(body, dict) or set(body) != {
            "call_id",
            "request_id",
            "max_turns",
            "capture_seconds",
        }:
            raise ValueError(
                "Expected call_id, request_id, max_turns and capture_seconds only"
            )
        if not isinstance(body["request_id"], str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,100}", body["request_id"]
        ):
            raise ValueError("Invalid request identifier")
        if (
            isinstance(body["max_turns"], bool)
            or not isinstance(body["max_turns"], int)
            or not 1 <= body["max_turns"] <= 20
        ):
            raise ValueError("max_turns must be between 1 and 20")
        if (
            isinstance(body["capture_seconds"], bool)
            or not isinstance(body["capture_seconds"], int)
            or not 1 <= body["capture_seconds"] <= 30
        ):
            raise ValueError("capture_seconds must be between 1 and 30")
        self._call(body["call_id"])
        with self.store.connection() as db:
            db.row_factory = sqlite3.Row
            prior = db.execute(
                "SELECT * FROM native_duplex_sessions WHERE request_id=?",
                (body["request_id"],),
            ).fetchone()
            if prior:
                value = self._row(prior)
                if any(
                    value[key] != body[key]
                    for key in ("call_id", "max_turns", "capture_seconds")
                ):
                    raise Conflict(
                        "Request identifier already belongs to another duplex session"
                    )
                return value
            identity, stamp = uuid4().hex, time.time()
            state = "ready" if self.readiness()["available"] else "unavailable"
            error = "" if state == "ready" else "; ".join(self.readiness()["errors"])
            db.execute(
                "INSERT INTO native_duplex_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    body["request_id"],
                    body["call_id"],
                    state,
                    1,
                    body["max_turns"],
                    body["capture_seconds"],
                    "[]",
                    error,
                    stamp,
                    stamp,
                ),
            )
        return self.get(identity)

    def _update(self, identity, expected, *, state, turns=None, error=""):
        current = self.get(identity)
        if current["revision"] != expected:
            raise Conflict("Duplex session revision changed")
        with self.store.connection() as db:
            db.execute(
                "UPDATE native_duplex_sessions SET state=?,revision=revision+1,turns=?,error=?,updated_at=? WHERE id=? AND revision=?",
                (
                    state,
                    json.dumps(turns if turns is not None else current["turns"]),
                    error,
                    time.time(),
                    identity,
                    expected,
                ),
            )
            if db.total_changes != 1:
                raise Conflict("Duplex session revision changed")
        return self.get(identity)

    def command(self, operation, path, seconds=None):
        if not self.readiness()["available"]:
            raise Conflict("; ".join(self.readiness()["errors"]))
        args = [self.xcrun, "swift", str(self.source), operation, str(path)]
        if seconds is not None:
            args.append(str(seconds))
        return args

    async def _run(self, args, timeout):
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise Conflict(
                "Native audio helper timed out; actual device state is unknown"
            )
        if process.returncode:
            raise Conflict(
                (stderr.decode(errors="replace") or "Native audio helper failed")[:1000]
            )
        if stdout.decode(errors="replace").strip() != "ok":
            raise Conflict("Native audio helper returned an invalid result")

    async def capture(self, identity, revision):
        async with self.lock:
            current = self.get(identity)
            if current["state"] != "ready":
                raise Conflict("Duplex session is not ready to capture")
            if len(current["turns"]) >= current["max_turns"]:
                raise Conflict("Duplex turn limit reached")
            self._call(current["call_id"])
            active = self._update(identity, revision, state="capturing")
            try:
                from gideon.integrations.transcribe import transcribe_audio

                with tempfile.TemporaryDirectory(
                    prefix="gideon-native-duplex-"
                ) as directory:
                    path = Path(directory) / "capture.wav"
                    await self._run(
                        self.command("capture", path, current["capture_seconds"]),
                        current["capture_seconds"] + 15,
                    )
                    if not path.is_file() or path.stat().st_size < 44:
                        raise Conflict("Native capture produced no PCM audio")
                    transcript = await transcribe_audio(str(path))
                if not transcript or not transcript.strip():
                    raise Conflict("Configured STT produced no transcript")
                turns = [
                    *active["turns"],
                    {
                        "index": len(active["turns"]) + 1,
                        "transcript": transcript.strip()[:10000],
                        "reply": "",
                        "captured_at": time.time(),
                    },
                ]
                return self._update(
                    identity, active["revision"], state="awaiting_reply", turns=turns
                )
            except Exception as exc:
                self._update(
                    identity,
                    active["revision"],
                    state="interrupted",
                    error=str(exc)[:1000],
                )
                raise

    async def speak(self, identity, revision, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 10000:
            raise ValueError("Reply text must contain at most 10000 characters")
        async with self.lock:
            current = self.get(identity)
            if current["state"] != "awaiting_reply" or not current["turns"]:
                raise Conflict("Duplex session has no captured turn awaiting a reply")
            self._call(current["call_id"])
            active = self._update(identity, revision, state="speaking")
            try:
                from gideon.integrations.tts.registry import (
                    active_voice_params,
                    route_synthesis,
                )

                params = active_voice_params(surface="native-duplex")
                if params is None or not params.get("enabled", True):
                    raise Conflict("Configured TTS is unavailable")
                with tempfile.TemporaryDirectory(
                    prefix="gideon-native-duplex-"
                ) as directory:
                    path = await route_synthesis(
                        params,
                        text.strip(),
                        output_path=str(Path(directory) / "reply.wav"),
                    )
                    if not path or not Path(path).is_file():
                        raise Conflict("Configured TTS produced no playable audio")
                    await self._run(self.command("play", path), 180)
                turns = list(active["turns"])
                turns[-1] = {**turns[-1], "reply": text.strip()}
                return self._update(
                    identity, active["revision"], state="ready", turns=turns
                )
            except Exception as exc:
                self._update(
                    identity,
                    active["revision"],
                    state="interrupted",
                    error=str(exc)[:1000],
                )
                raise

    def stop(self, identity, revision):
        current = self.get(identity)
        if current["state"] in ("capturing", "speaking"):
            raise Conflict(
                "Wait for the native operation to finish or interrupt the worker"
            )
        return self._update(identity, revision, state="stopped")
