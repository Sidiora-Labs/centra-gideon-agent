"""Run a real HTTP lifecycle in a dedicated interpreter and isolated Gideon home."""

import asyncio
import json
import sys

import aiohttp

from gideon.core.config.loader import AppConfig, config_dir
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.lifecycle import bind_surface, retire
from gideon.interfaces.dashboard.token_auth import generate_token


async def main():
    surface = sys.argv[1]
    config = AppConfig()
    config.save()
    runtime = RuntimeCoordinator(
        config, no_crons=True, no_open=True, port_override="auto"
    )
    runtime._init_services()
    try:
        await bind_surface(runtime, api_only=surface == "api")
        marker = config_dir() / "gateway.runtime.json"
        recorded = json.loads(marker.read_text())
        assert recorded["port"] == runtime._dashboard_port > 0
        base = f"http://127.0.0.1:{recorded['port']}"
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        ) as client:
            route = "/api/lessons" if surface == "api" else "/api/healthz"
            async with client.get(base + route) as response:
                assert response.status == 200, await response.text()
                assert isinstance(await response.json(), dict)
            if surface == "console":
                async with client.get(base + "/api/config/gideon") as response:
                    assert response.status == 403, await response.text()
                token = generate_token("lifecycle-probe")
                async with client.get(
                    base + "/api/config/gideon", params={"token": token}
                ) as response:
                    assert response.status == 200, await response.text()
                    assert isinstance(await response.json(), dict)
                assert any(
                    cookie.key.startswith("gideon_token_")
                    for cookie in client.cookie_jar
                )
                async with client.get(base + "/api/config/gideon") as response:
                    assert response.status == 200, await response.text()
    finally:
        await retire(runtime)
    assert not (config_dir() / "gateway.runtime.json").exists()
    async with aiohttp.ClientSession() as client:
        try:
            await client.get(base + "/api/healthz")
        except aiohttp.ClientConnectorError:
            pass
        else:
            raise AssertionError("HTTP socket remained available after retirement")
    print(f"{surface}: live HTTP and cleanup passed")


if __name__ == "__main__":
    asyncio.run(main())
