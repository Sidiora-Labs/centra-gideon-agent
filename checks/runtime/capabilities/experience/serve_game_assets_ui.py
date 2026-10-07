"""Native managed-app compiler and artifact ledger for console journeys."""

import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from checks.runtime.capabilities.experience.test_game_assets import glb, png, wav
from gideon.extensions.apps import app_manager
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.game_assets import GameAssets
from gideon.workspace.capabilities.experience.game_assets_http import (
    register_game_assets,
)
from gideon.workspace.capabilities.experience.world_foundations import WorldFoundations


async def main():
    home = Path(os.environ["GIDEON_HOME"])
    source = home / "source" / "real-game-app"
    source.mkdir(parents=True)
    (source / "app.json").write_text(
        json.dumps(
            {
                "name": "real-game-app",
                "version": "1.0.0",
                "displayName": "Real game",
                "description": "Consumes compiled game assets",
                "permissions": {"storage": True},
            }
        )
    )
    installed = app_manager.install(source, confirm=True)
    assert installed.ok, installed.error
    store = ExperienceStore(home)
    foundations = WorldFoundations(store)
    foundation = foundations.record(
        {
            "title": "Arcade foundation",
            "controller": {"kind": "ambient_beacon", "position": [0, 0, 0]},
            "style": {"private": "local"},
        }
    )
    foundation = foundations.package(foundation["id"], foundation["revision"])
    foundation = foundations.promote(foundation["id"], foundation["revision"])
    artifacts = NativeArtifactProvider(home / "artifacts")
    refs = {}
    for role, data, kind, mime in [
        ("sprite", png("red"), "image", "image/png"),
        ("artwork", png("blue"), "image", "image/png"),
        ("music", wav(), "audio", "audio/wav"),
        ("model", glb(), "model", "model/gltf-binary"),
    ]:
        artifact = artifacts.create_binary(
            name=role, data=data, kind=kind, mime=mime, source="manual"
        )
        refs[role] = {"slug": artifact.slug, "version": artifact.version}
    service = GameAssets(store, artifacts)
    for title in ["Runnable arcade", "Publication arcade", "Integrity arcade"]:
        project = service.create(
            {
                "title": title,
                "app_id": "real-game-app",
                "foundation_id": foundation["id"],
            }
        )
        for role in refs:
            project = service.bind(
                project["id"],
                {
                    "revision": project["revision"],
                    "role": role,
                    "artifact_ref": refs[role],
                    "label": role.title(),
                },
            )
        service.compile(project["id"], project["revision"])
    app = web.Application(middlewares=[token_auth_middleware()])
    register_game_assets(app, store, artifacts)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(
        json.dumps(
            {
                "url": "http://127.0.0.1:"
                + str(site._server.sockets[0].getsockname()[1]),
                "token": generate_token("game-assets-ui-owner"),
                "sprite_path": str(
                    artifacts._binary_version_path(
                        refs["sprite"]["slug"], refs["sprite"]["version"]
                    )
                ),
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
