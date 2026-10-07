"""Relay committed voice settings changes to connected console clients."""

import asyncio

from aiohttp import web

from gideon.extensions.providers.use_cases import (
    add_settings_listener,
    remove_settings_listener,
)


def register_voice_settings(app: web.Application) -> None:
    async def startup(app: web.Application) -> None:
        loop = asyncio.get_running_loop()
        state = app["state"]

        def changed(use_case: str) -> None:
            if use_case in {"tts", "stt"} and not loop.is_closed():
                loop.call_soon_threadsafe(state.push_refresh, "voice")

        app["voice_settings_listener"] = changed
        add_settings_listener(changed)

    async def cleanup(app: web.Application) -> None:
        listener = app.pop("voice_settings_listener", None)
        if listener is not None:
            remove_settings_listener(listener)

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
