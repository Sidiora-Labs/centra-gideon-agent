"""Owned isolated X11 desktops with private credentials and human frame/input access."""

import asyncio
import fcntl
import json
import os
import secrets
import shutil
import sqlite3
import sys
import termios
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.core.cancellation import terminate_and_reap
from gideon.security.sandbox import (
    PROFILE_TOOL,
    build_child_env,
    create_subprocess_limited,
    wrap_argv,
)

from .projects import ProjectService
from .store import ConflictError


def now():
    return datetime.now(timezone.utc).isoformat()


class DesktopRegistry:
    def __init__(self, root, *, allowed_roots):
        self.root = Path(root)
        self.projects = ProjectService(root, allowed_roots=allowed_roots)
        self.lock = (self.root / "desktops.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise ConflictError("Another desktop registry owns this home") from None
        self.db_path = self.root / "desktop-sessions.sqlite3"
        self.handles = {}
        self.monitors = {}
        self.mutex = asyncio.Lock()
        self.closed = False
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, input TEXT, payload TEXT)"
            )
            for sid, raw in db.execute("SELECT id,payload FROM sessions").fetchall():
                value = json.loads(raw)
                if value["status"] in ("starting", "running"):
                    value.update(
                        status="interrupted",
                        revision=value["revision"] + 1,
                        ended_at=now(),
                    )
                    db.execute(
                        "UPDATE sessions SET payload=? WHERE id=?",
                        (json.dumps(value), sid),
                    )
        os.chmod(self.db_path, 0o600)

    def availability(self):
        missing = [
            name
            for name in (
                "Xvfb",
                "xauth",
                "openbox",
                "xterm",
                "xdpyinfo",
                "xdotool",
                "import",
            )
            if not shutil.which(name)
        ]
        return {
            "available": sys.platform == "linux" and not missing,
            "missing": missing,
            "transport": "png-polling",
            "max_frames_per_second": 1,
        }

    def get(self, sid):
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT payload FROM sessions WHERE id=?", (sid,)
            ).fetchone()
        if not row:
            raise FileNotFoundError("Desktop session not found")
        return json.loads(row[0])

    def list(self):
        with sqlite3.connect(self.db_path) as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM sessions ORDER BY rowid DESC LIMIT 100"
                )
            ]

    def _update(self, sid, **fields):
        record = self.get(sid)
        record.update(fields, revision=record["revision"] + 1)
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE sessions SET payload=? WHERE id=?", (json.dumps(record), sid)
            )
        return record

    async def _spawn(self, handle, args, *, pass_fds=(), terminal=None):
        env = {
            k: v
            for k, v in build_child_env(site="workspace-desktop").items()
            if k
            not in (
                "DISPLAY",
                "XAUTHORITY",
                "WAYLAND_DISPLAY",
                "DBUS_SESSION_BUS_ADDRESS",
            )
        }
        env.update(
            DISPLAY=handle["display"],
            XAUTHORITY=str(handle["auth"]),
            HOME=str(handle["home"]),
            TERM="xterm",
        )
        child = Path(__file__).with_name("desktop_child.py")
        wrapped, disposable = wrap_argv(args)
        argv = [
            sys.executable,
            str(child),
            str(os.getpid()),
            ",".join(str(fd) for fd in pass_fds),
            *wrapped,
        ]
        try:
            proc = await create_subprocess_limited(
                *argv,
                profile=PROFILE_TOOL,
                cwd=handle["cwd"],
                env=env,
                stdin=asyncio.subprocess.DEVNULL if terminal is None else terminal,
                stdout=asyncio.subprocess.PIPE if terminal is None else terminal,
                stderr=asyncio.subprocess.PIPE if terminal is None else terminal,
                start_new_session=True,
                pass_fds=pass_fds,
            )

            async def drain():
                tail = b""
                if proc.stderr is None:
                    return ""
                while chunk := await proc.stderr.read(8192):
                    tail = (tail + chunk)[-8192:]
                return tail.decode("utf-8", "replace")

            proc.desktop_diagnostic = asyncio.create_task(drain())
            return proc, disposable
        except BaseException:
            if disposable:
                Path(disposable).unlink(missing_ok=True)
            raise

    async def _command(self, handle, args, limit=4194304):
        proc, disposable = await self._spawn(handle, args)
        try:

            async def read():
                result = bytearray()
                while chunk := await proc.stdout.read(65536):
                    result.extend(chunk)
                    if len(result) > limit:
                        raise ValueError("Desktop output exceeds limit")
                await proc.wait()
                if proc.returncode:
                    raise ValueError("Desktop command failed") from RuntimeError(
                        await proc.desktop_diagnostic
                    )
                return bytes(result)

            return await asyncio.wait_for(read(), 8)
        finally:
            if proc.returncode is None:
                await terminate_and_reap(proc)
            if disposable:
                Path(disposable).unlink(missing_ok=True)

    async def start(self, payload):
        if not isinstance(payload, dict) or set(payload) != {
            "project_id",
            "request_id",
            "width",
            "height",
        }:
            raise ValueError("Invalid desktop fields")
        for key in ("project_id", "request_id"):
            if (
                not isinstance(payload[key], str)
                or not payload[key]
                or len(payload[key]) > 256
            ):
                raise ValueError("Invalid desktop identity")
        if (
            type(payload["width"]) is not int
            or type(payload["height"]) is not int
            or not 640 <= payload["width"] <= 1280
            or not 480 <= payload["height"] <= 900
        ):
            raise ValueError("Desktop dimensions outside supported range")
        async with self.mutex:
            if self.closed:
                raise ConflictError("Desktop registry closed")
            encoded = json.dumps(payload, sort_keys=True)
            with sqlite3.connect(self.db_path) as db:
                prior = db.execute(
                    "SELECT input,payload FROM sessions WHERE request_id=?",
                    (payload["request_id"],),
                ).fetchone()
                if prior:
                    if prior[0] != encoded:
                        raise ConflictError(
                            "Request ID already used for another desktop"
                        )
                    return json.loads(prior[1])
                if len(self.handles) >= 2:
                    raise ConflictError("Maximum two live desktops")
                if not self.availability()["available"]:
                    raise ValueError("Isolated desktop dependencies unavailable")
                project = self.projects.get(payload["project_id"])["project"]
                record = {
                    "id": uuid4().hex,
                    **payload,
                    "status": "starting",
                    "revision": 1,
                    "created_at": now(),
                    "started_at": None,
                    "ended_at": None,
                }
                db.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?)",
                    (record["id"], payload["request_id"], encoded, json.dumps(record)),
                )
            folder = self.root / "desktop-private" / record["id"]
            folder.mkdir(parents=True, mode=0o700)
            home = folder / "home"
            home.mkdir(mode=0o700)
            auth = folder / "authority"
            auth.touch(mode=0o600)
            display = 1000 + secrets.randbelow(30000)
            handle = {
                "display": f":{display}",
                "auth": auth,
                "home": home,
                "cwd": project["workspace_dir"],
                "processes": [],
                "frame_at": 0.0,
            }
            self.handles[record["id"]] = handle
            try:
                if (
                    Path(f"/tmp/.X11-unix/X{display}").exists()
                    or Path(f"/tmp/.X{display}-lock").exists()
                ):
                    raise ConflictError(
                        "Display allocation collided; retry a new request"
                    )
                await self._command(
                    handle,
                    [
                        "xauth",
                        "-f",
                        str(auth),
                        "add",
                        handle["display"],
                        "MIT-MAGIC-COOKIE-1",
                        secrets.token_hex(16),
                    ],
                )
                proc, profile = await self._spawn(
                    handle,
                    [
                        "Xvfb",
                        handle["display"],
                        "-screen",
                        "0",
                        f"{payload['width']}x{payload['height']}x24",
                        "-nolisten",
                        "tcp",
                        "-noreset",
                        "-auth",
                        str(auth),
                    ],
                )
                handle["processes"].append((proc, profile))
                for attempt in range(30):
                    if proc.returncode is not None:
                        raise ValueError("Isolated display failed to start")
                    try:
                        await self._command(handle, ["xdpyinfo"], 262144)
                        break
                    except ValueError:
                        if attempt == 29:
                            raise
                        await asyncio.sleep(0.1)
                handle["processes"].append(
                    await self._spawn(handle, ["openbox", "--sm-disable"])
                )
                master, slave = os.openpty()
                handle["pty"] = (master, slave)
                attributes = termios.tcgetattr(slave)
                attributes[6][termios.VERASE] = b"\x08"
                quiet = termios.tcgetattr(slave)
                quiet[3] &= ~termios.ECHO
                termios.tcsetattr(slave, termios.TCSANOW, quiet)
                handle["processes"].append(
                    await self._spawn(
                        handle,
                        [
                            "xterm",
                            "-title",
                            "Workspace desktop",
                            "-geometry",
                            "80x24+10+10",
                            f"-S{os.ttyname(slave)}/{master}",
                        ],
                        pass_fds=(master,),
                    )
                )
                window = None
                for attempt in range(30):
                    terminal = handle["processes"][-1][0]
                    if terminal.returncode is not None:
                        diagnostic = await terminal.desktop_diagnostic
                        raise ValueError(
                            "Desktop terminal unavailable"
                        ) from RuntimeError(diagnostic)
                    try:
                        window = (
                            (
                                await self._command(
                                    handle,
                                    ["xdotool", "search", "--class", "XTerm"],
                                    1024,
                                )
                            )
                            .decode()
                            .splitlines()[0]
                        )
                        await self._command(
                            handle, ["xdotool", "windowmap", "--sync", window]
                        )
                        await self._command(
                            handle, ["xdotool", "windowfocus", "--sync", window]
                        )
                        break
                    except (ValueError, IndexError):
                        if attempt == 29:
                            raise ValueError("Desktop terminal unavailable")
                        await asyncio.sleep(0.1)
                os.set_blocking(slave, False)
                try:
                    while os.read(slave, 4096):
                        pass
                except BlockingIOError:
                    pass
                finally:
                    os.set_blocking(slave, True)
                termios.tcsetattr(slave, termios.TCSANOW, attributes)
                handle["processes"].append(
                    await self._spawn(handle, ["/bin/sh", "-i"], terminal=slave)
                )
                running = self._update(record["id"], status="running", started_at=now())
                self.monitors[record["id"]] = asyncio.create_task(
                    self._watch(record["id"])
                )
                return running
            except BaseException:
                await self._cleanup(record["id"])
                self._update(record["id"], status="failed", ended_at=now())
                raise

    async def _cleanup(self, sid):
        handle = self.handles.get(sid)
        if handle:
            for proc, profile in reversed(handle["processes"]):
                await terminate_and_reap(proc)
                if profile:
                    Path(profile).unlink(missing_ok=True)
            for fd in handle.get("pty", ()):
                os.close(fd)
            handle["auth"].unlink(missing_ok=True)
            self.handles.pop(sid, None)

    async def _watch(self, sid):
        waits = [
            asyncio.create_task(proc.wait())
            for proc, _ in self.handles[sid]["processes"]
        ]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            if sid in self.handles:
                self._update(sid, status="failed", ended_at=now())
                await self._cleanup(sid)
        finally:
            for task in waits:
                task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

    def _live(self, sid):
        record = self.get(sid)
        if record["status"] != "running" or sid not in self.handles:
            raise ConflictError("Desktop is not running")
        return record, self.handles[sid]

    async def frame(self, sid):
        _, handle = self._live(sid)
        current = asyncio.get_running_loop().time()
        if current - handle["frame_at"] < 1:
            raise ConflictError("Maximum one frame per second")
        handle["frame_at"] = current
        data = await self._command(handle, ["import", "-window", "root", "png:-"])
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Desktop frame unavailable")
        return data

    async def input(self, sid, payload):
        record, handle = self._live(sid)
        if not isinstance(payload, dict):
            raise ValueError("Invalid desktop input")
        kind = payload.get("kind")
        if (
            kind == "click"
            and set(payload) == {"kind", "x", "y"}
            and type(payload["x"]) is int
            and type(payload["y"]) is int
            and 0 <= payload["x"] < record["width"]
            and 0 <= payload["y"] < record["height"]
        ):
            args = [
                "xdotool",
                "mousemove",
                "--sync",
                str(payload["x"]),
                str(payload["y"]),
                "click",
                "1",
            ]
        elif (
            kind == "text"
            and set(payload) == {"kind", "text"}
            and isinstance(payload["text"], str)
            and 0 < len(payload["text"]) <= 2048
            and not any(ord(x) < 32 for x in payload["text"])
        ):
            args = [
                "xdotool",
                "type",
                "--clearmodifiers",
                "--delay",
                "0",
                "--",
                payload["text"],
            ]
        elif (
            kind == "key"
            and set(payload) == {"kind", "key"}
            and payload["key"]
            in (
                "Return",
                "Tab",
                "Escape",
                "BackSpace",
                "Delete",
                "Left",
                "Right",
                "Up",
                "Down",
                "Home",
                "End",
            )
        ):
            args = ["xdotool", "key", "--clearmodifiers", payload["key"]]
        else:
            raise ValueError("Invalid desktop input")
        await self._command(handle, args)
        return {"id": sid, "accepted": True}

    async def stop(self, sid, revision):
        async with self.mutex:
            record = self.get(sid)
            if type(revision) is not int:
                raise ValueError("Revision must be integer")
            if record["status"] in ("stopped", "failed", "interrupted"):
                watcher = self.monitors.get(sid)
                if watcher:
                    await asyncio.gather(watcher, return_exceptions=True)
                return record
            if record["revision"] != revision:
                raise ConflictError("Desktop revision changed")
            record = self._update(sid, status="stopped", ended_at=now())
            watcher = self.monitors.pop(sid, None)
            if watcher:
                watcher.cancel()
            await self._cleanup(sid)
            if watcher:
                await asyncio.gather(watcher, return_exceptions=True)
            return record

    async def close(self):
        if self.closed:
            return
        for sid in list(self.handles):
            await self.stop(sid, self.get(sid)["revision"])
        self.closed = True
        self.lock.close()


_registries = {}


def get_desktop_registry(root, *, allowed_roots):
    key = str(Path(root).resolve())
    registry = _registries.get(key)
    if registry is None or registry.closed:
        registry = DesktopRegistry(root, allowed_roots=allowed_roots)
        _registries[key] = registry
    return registry


async def close_desktop_registry(root):
    registry = _registries.pop(str(Path(root).resolve()), None)
    if registry:
        await registry.close()
