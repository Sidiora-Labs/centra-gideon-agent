import json

import pytest
from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_creative import STORE, register
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
    use_persistent_secret,
)
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.store import IngredientStore
from gideon.workspace.capabilities.creative.tools import CreativeToolProvider
from gideon.workspace.capabilities.creative.works import WorkStore


@pytest.mark.asyncio
async def test_creative_handler_registers_real_authenticated_manuscript_export(
    tmp_path,
):
    works = WorkStore(tmp_path)
    work = works.create(
        {
            "request_id": "integration-work",
            "title": "Integrated manuscript",
            "kind": "work",
            "prompt": "Write it",
            "author_ref": None,
            "universe_ref": None,
            "active_draft_id": None,
        }
    )
    drafted = works.draft(
        work["id"],
        {
            "request_id": "integration-draft",
            "revision": work["revision"],
            "text": "# Chapter\n\nAuthenticated export body.",
            "note": "canonical",
        },
    )["work"]
    series = SeriesStore(tmp_path).create(
        {
            "request_id": "integration-series",
            "title": "Integrated series",
            "synopsis": "",
            "volumes": [
                {
                    "id": "volume",
                    "title": "Volume",
                    "chapters": [
                        {"id": "chapter", "title": "Chapter", "prompt": "Draft"}
                    ],
                }
            ],
            "arcs": [],
        }
    )
    use_ephemeral_secret()
    try:
        app = web.Application(middlewares=[token_auth_middleware()])
        app[STORE] = IngredientStore(tmp_path)
        register(app)
        async with TestClient(
            TestServer(app), cookie_jar=CookieJar(unsafe=True)
        ) as client:
            denied = await client.get("/api/capabilities/creative/exports")
            assert denied.status in (401, 403)
            authorized = await client.get(
                "/api/capabilities/creative/exports?token="
                + generate_token("creative-export-owner")
            )
            assert authorized.status == 200
            assert await authorized.json() == {"items": []}
            created = await client.post(
                "/api/capabilities/creative/exports",
                json={
                    "request_id": "integration-export",
                    "source_kind": "work",
                    "source_id": drafted["id"],
                    "source_revision": drafted["revision"],
                    "title": "Published manuscript",
                    "creator": "Owner",
                    "language": "en",
                    "identifier": "urn:gideon:integration-export",
                },
            )
            assert created.status == 201, await created.text()
            receipt = await created.json()
            assert receipt["selections"][0]["work_id"] == drafted["id"]
            epub = await client.get(receipt["files"]["epub"]["path"])
            assert epub.status == 200
            assert (await epub.read()).startswith(b"PK")
            printable = await client.get(receipt["files"]["print"]["path"])
            assert printable.status == 200
            assert (await printable.read()).startswith(b"%PDF")
            production = await client.post(
                f"/api/capabilities/creative/series/{series['id']}/production",
                json={
                    "request_id": "integration-production",
                    "series_revision": series["revision"],
                    "mode": "authored",
                    "max_attempts": 2,
                },
            )
            assert production.status == 200, await production.text()
            assert (await production.json())["series_id"] == series["id"]
            direction = await client.post(
                "/api/capabilities/creative/direction",
                json={
                    "request_id": "integration-direction",
                    "name": "Release direction",
                    "treatment": "Keep the authenticated manuscript pinned.",
                    "sources": [
                        {
                            "kind": "work",
                            "id": drafted["id"],
                            "revision": drafted["revision"],
                        }
                    ],
                    "steps": [
                        {
                            "id": "verify",
                            "title": "Verify source",
                            "operation": "source.verify",
                            "depends_on": [],
                        }
                    ],
                },
            )
            assert direction.status == 201, await direction.text()
            assert (await direction.json())["sources"][0]["id"] == drafted["id"]

        provider = CreativeToolProvider(tmp_path)
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions["creative_production_start"].requires_approval is True
        listed = await provider.invoke(
            "creative_production_list", {"series_id": series["id"]}
        )
        assert listed.success is True
        assert json.loads(listed.output)["items"][0]["series_id"] == series["id"]
    finally:
        use_persistent_secret()
