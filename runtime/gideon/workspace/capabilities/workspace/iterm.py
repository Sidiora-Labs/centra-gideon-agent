"""Bounded read-only observation of the owner's existing native iTerm panes."""

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path

from gideon.core.cancellation import terminate_and_reap
from gideon.security.sandbox import (
    PROFILE_TOOL,
    build_child_env,
    create_subprocess_limited,
    wrap_argv,
)


def pane_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_:.-]{1,256}", value):
        raise ValueError("Invalid native pane identity")
    return value


class ExternalTerminalMirror:
    def availability(self):
        reason = (
            "macOS required"
            if sys.platform != "darwin"
            else (
                "Install the optional iTerm2 Python SDK"
                if importlib.util.find_spec("iterm2") is None
                else None
            )
        )
        return {
            "available": reason is None,
            "reason": reason,
            "transport": "native-iterm2",
            "read_only": True,
        }

    async def inventory(self):
        return await self._read(None)

    async def screen(self, identity):
        return await self._read(pane_id(identity))

    async def _read(self, identity):
        state = self.availability()
        if not state["available"]:
            raise OSError(state["reason"])
        argv, profile = wrap_argv(
            [
                sys.executable,
                str(Path(__file__).with_name("iterm_worker.py")),
                identity or "",
            ]
        )
        env = build_child_env(site="workspace-external-terminal")
        for key in ("ITERM2_COOKIE", "ITERM2_KEY", "IT2_APP_PATH", "IT2_SUITE"):
            env.pop(key, None)
        proc = None
        try:
            proc = await create_subprocess_limited(
                *argv,
                profile=PROFILE_TOOL,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )

            async def receive():
                output = bytearray()
                while chunk := await proc.stdout.read(16384):
                    output.extend(chunk)
                    if len(output) > 524288:
                        raise OSError("Native mirror exceeded output limit")
                await proc.wait()
                if proc.returncode:
                    raise OSError(
                        "Native terminal authorization or connection unavailable"
                    )
                try:
                    value = json.loads(output)
                except (ValueError, UnicodeDecodeError):
                    raise OSError("Invalid native mirror response") from None
                if value.get("missing"):
                    raise FileNotFoundError("Native pane no longer exists")
                return value

            return await asyncio.wait_for(receive(), 20)
        finally:
            if proc is not None and proc.returncode is None:
                await terminate_and_reap(proc)
            if profile:
                Path(profile).unlink(missing_ok=True)
