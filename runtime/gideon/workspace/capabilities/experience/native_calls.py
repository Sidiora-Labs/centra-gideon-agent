import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from uuid import uuid4
from weakref import WeakValueDictionary

from .store import Conflict, NotFound

_SERVICES = WeakValueDictionary()


def get_native_calls(store):
    key = str(store.path.resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = NativeCalls(store)
        _SERVICES[key] = service
    return service


class NativeCalls:
    def __init__(self, store):
        self.store = store
        self.home = store.path.parent.parent.resolve()
        self.helper = Path(__file__).parent / "assets" / "facetime.applescript"
        self.lock = asyncio.Lock()
        with store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS native_call_requests(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, command TEXT, state TEXT, result TEXT, created_at REAL)"
            )
            db.execute(
                "UPDATE native_call_requests SET state='interrupted',result='Runtime stopped before native result was recorded; inspect FaceTime before retrying' WHERE state='running'"
            )

    def configuration(self):
        path = self.home / "capabilities" / "native_call_config.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) != {
            "target_handle",
            "target_name",
        }:
            raise ValueError(
                "Native call configuration requires target_handle and target_name"
            )
        handle, name = value["target_handle"], value["target_name"]
        if not isinstance(handle, str) or not re.fullmatch(
            r"(?:\+[0-9]{6,15}|[A-Za-z0-9._+\-]+@[A-Za-z0-9.\-]+)", handle
        ):
            raise ValueError("Configure an E.164 phone number or email address")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 120
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("Configure a bounded target display name")
        return value

    def readiness(self):
        errors = []
        try:
            config = self.configuration()
        except (ValueError, OSError) as exc:
            config = None
            errors.append(str(exc))
        if sys.platform != "darwin":
            errors.append("Native FaceTime control requires macOS")
        if not self.helper.is_file():
            errors.append("Bundled native helper source is missing")
        if config is None:
            errors.append(
                "Native call target is not configured by the machine operator"
            )
        return {
            "available": not errors,
            "errors": errors,
            "target_name": config["target_name"] if config else None,
            "accessibility": "unverified",
            "audio_transport": "unqualified",
            "detail": "Explicit commands use macOS Accessibility. Native audio and caller identity must be qualified on the Mac; accepted commands do not prove connection.",
        }

    def list(self):
        with self.store.connection() as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM native_call_requests ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def get(self, key):
        with self.store.connection() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM native_call_requests WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                raise NotFound("Native call request not found")
            return dict(row)

    @staticmethod
    def validate(body):
        if not isinstance(body, dict) or set(body) != {"command", "request_id"}:
            raise ValueError("Expected command and request_id only")
        if not isinstance(body["command"], str) or body["command"] not in (
            "probe",
            "call",
            "answer",
            "hangup",
        ):
            raise ValueError("Invalid native command")
        if not isinstance(body["request_id"], str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,100}", body["request_id"]
        ):
            raise ValueError("Invalid request identifier")

    async def command(self, body):
        self.validate(body)
        async with self.lock:
            with self.store.connection() as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT * FROM native_call_requests WHERE request_id=?",
                    (body["request_id"],),
                ).fetchone()
                if row:
                    if row["command"] != body["command"]:
                        raise Conflict(
                            "Request identifier already belongs to another command"
                        )
                    return dict(row)
                key = uuid4().hex
                db.execute(
                    "INSERT INTO native_call_requests VALUES(?,?,?,?,?,?)",
                    (
                        key,
                        body["request_id"],
                        body["command"],
                        "running",
                        "",
                        time.time(),
                    ),
                )
            process = None
            state, result = "failed", ""
            try:
                report = self.readiness()
                if not report["available"]:
                    state, result = "unavailable", "; ".join(report["errors"])
                else:
                    config = self.configuration()
                    process = await asyncio.create_subprocess_exec(
                        "/usr/bin/osascript",
                        str(self.helper),
                        body["command"],
                        config["target_handle"],
                        config["target_name"],
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=40
                    )
                    observed = stdout.decode("utf-8", errors="replace").strip()
                    if process.returncode != 0:
                        result = (
                            stderr.decode("utf-8", errors="replace")[:2000]
                            or "Native helper failed"
                        )
                    elif observed not in ("requested", "idle", "unknown"):
                        result = "Native helper returned an invalid observation"
                    else:
                        state, result = observed, observed
            except asyncio.TimeoutError:
                result = "Native helper timed out; actual call state is unknown"
            except asyncio.CancelledError:
                state, result = (
                    "interrupted",
                    "Command cancelled; actual call state is unknown",
                )
                raise
            except (OSError, ValueError) as exc:
                result = str(exc)[:2000]
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                with self.store.connection() as db:
                    db.row_factory = sqlite3.Row
                    db.execute(
                        "UPDATE native_call_requests SET state=?,result=? WHERE id=?",
                        (state, result, key),
                    )
            return self.get(key)
