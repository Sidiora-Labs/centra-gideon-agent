import asyncio
import os
import shutil
from pathlib import Path

import aiohttp

from gideon.core.config.loader import CONFIG_DIR_NAME, logger
from gideon.core.config.locations import configuration_home
from gideon.extensions.apps import app_manager
from gideon.extensions.apps.app_secret import read_app_secret
from gideon.extensions.apps.backend_runtime import get_backend_supervisor
from gideon.extensions.apps.manager import _read_installed, app_dir
from gideon.sdk.security import PROXY_SIGNATURE_HEADER, sign_proxy_request

from .store import Conflict

APP_ID = "gideon-world-engine"
PREFIX = "/api/capabilities/experience/world-engine"


class WorldEngine:
    def __init__(self, store):
        self.home = store.path.parent.parent.resolve()
        self.lock = asyncio.Lock()

    def guard(self):
        active = configuration_home(
            os.environ.get("GIDEON_HOME"), Path.home() / CONFIG_DIR_NAME, logger
        ).resolve()
        if active != self.home:
            raise Conflict("Runtime home changed; world engine access refused")

    def target(self):
        self.guard()
        meta = _read_installed(APP_ID)
        if meta is None or not meta.enabled:
            raise Conflict("World engine app is not installed and enabled")
        backend = get_backend_supervisor().get(APP_ID)
        secret = read_app_secret(APP_ID)
        if backend is None or not secret:
            raise Conflict("World engine backend is not running")
        return backend.base_url, secret

    async def status(self):
        self.guard()
        result = {
            "state": "unavailable",
            "reason": "",
            "app_id": APP_ID,
            "version": None,
            "engine_url": None,
        }
        if not shutil.which("bun"):
            result["reason"] = "Bun runtime is unavailable"
            return result
        if _read_installed(APP_ID) is None:
            result["reason"] = (
                "Operator must install the separately packaged world engine app and its pinned dependencies"
            )
            return result
        if not (app_dir(APP_ID) / "data" / "engine.json").is_file():
            result["reason"] = "Operator world engine configuration is missing"
            return result
        try:
            target, secret = self.target()
        except Conflict as exc:
            result.update(state="stopped", reason=str(exc))
            return result
        try:
            headers = {
                PROXY_SIGNATURE_HEADER: sign_proxy_request(
                    secret, "GET", "/version", b""
                )
            }
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3)
            ) as client:
                async with client.get(
                    target + "/version", headers=headers, allow_redirects=False
                ) as response:
                    if (
                        response.status != 200
                        or response.content_length
                        and response.content_length > 65536
                    ):
                        raise ValueError("Engine version handshake failed")
                    raw = await response.content.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("Engine version response exceeds limit")
                    import json

                    version = json.loads(raw)
                    if not isinstance(version, dict) or not version:
                        raise ValueError("Engine version response is invalid")
            result.update(
                state="running",
                reason="Actual managed world engine answered its version endpoint",
                version=version,
                engine_url=PREFIX + "/host/",
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            result.update(state="starting", reason=str(exc))
        return result

    async def control(self, operation, body):
        if body != {} or not isinstance(body, dict):
            raise ValueError("World lifecycle accepts an empty object only")
        self.guard()
        async with self.lock:
            if operation == "start":
                if not shutil.which("bun") or _read_installed(APP_ID) is None:
                    return await self.status()
                if not app_manager.enable(APP_ID):
                    raise Conflict("World engine app could not be enabled")
                for _ in range(30):
                    state = await self.status()
                    if state["state"] == "running":
                        return state
                    await asyncio.sleep(0.1)
            elif operation == "stop":
                if _read_installed(APP_ID) is not None and not app_manager.disable(
                    APP_ID
                ):
                    raise Conflict("World engine app could not be disabled")
            else:
                raise ValueError("Unknown world lifecycle operation")
            return await self.status()
