import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import tempfile

from aiohttp import web

from checks.runtime.capabilities.music.test_deck_assist import (
    assist_at,
    prompt_request,
    universe_at,
)
from gideon.interfaces.dashboard.handlers.capabilities_music_decks import register


async def main():
    with tempfile.TemporaryDirectory(prefix="gideon-deck-assist-ui-") as directory:
        assist = assist_at(Path(directory))
        deck = assist.decks.create({"name": "Grounded deck", "kind": "playing"})
        universe = universe_at(assist)
        await assist.propose(deck["id"], "prompts", prompt_request(deck, universe))
        app = web.Application()
        register(app, assist.decks, assist)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        print(
            json.dumps(
                {"port": runner.addresses[0][1], "deck": deck, "universe": universe}
            ),
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
