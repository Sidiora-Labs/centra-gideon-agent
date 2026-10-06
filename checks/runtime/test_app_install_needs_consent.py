"""App preview and install routes bind consent to the staged bytes they review."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.apps import backend_runtime
from gideon.extensions.apps.backend_runtime import BackendSupervisor
from gideon.interfaces.dashboard.handlers.apps import register_app_routes


@asynccontextmanager
async def _client(home: Path):
    previous_home = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(home)
    backend_runtime._supervisor = BackendSupervisor()
    app = web.Application()
    register_app_routes(app)
    async with TestClient(TestServer(app)) as client:
        try:
            yield client
        finally:
            backend_runtime.get_backend_supervisor().stop_all()
            if previous_home is None:
                os.environ.pop("GIDEON_HOME", None)
            else:
                os.environ["GIDEON_HOME"] = previous_home


def _bundle(
    root: Path, *, version: str = "1.0.0", permissions: dict | None = None
) -> Path:
    root.mkdir(parents=True)
    manifest = {
        "name": "reviewed-app",
        "version": version,
        "displayName": "Reviewed app",
        "description": "An app used to check its install review",
        "permissions": permissions or {"api": ["/api/tasks"], "cron": True},
        "crons": [{"name": "daily", "every": 3600, "message": "Review tasks"}],
    }
    (root / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "provider.py").write_text("VALUE = 'first'\n", encoding="utf-8")
    return root


async def _review(client: TestClient, source: Path, **extra) -> dict:
    response = await client.post(
        "/api/apps/preview",
        json={"name": extra.pop("name", ""), "source": str(source), **extra},
    )
    assert response.status == 200, await response.text()
    return await response.json()


@pytest.mark.asyncio
async def test_clean_install_returns_review_and_commits_only_its_digest(
    tmp_path: Path,
) -> None:
    async with _client(tmp_path / "home") as client:
        source = _bundle(tmp_path / "source" / "reviewed-app")

        bare = await client.post(
            "/api/apps", json={"name": "reviewed-app", "source": str(source)}
        )
        assert bare.status == 409, await bare.text()
        refused = await bare.json()
        assert refused["needs_consent"] is True
        assert refused["scan"]["verdict"] == "clean"
        assert refused["review"]["permissions"]["cron"] is True
        assert refused["review"]["crons"][0]["scheduled"] is True
        assert len(refused["review_digest"]) == 64
        assert not (tmp_path / "home" / "apps" / "reviewed-app").exists()

        review = await _review(client, source)
        assert review["needs_consent"] is True
        quarantine = tmp_path / "home" / "apps" / ".quarantine"
        assert not quarantine.exists() or not list(quarantine.iterdir())

        installed = await client.post(
            "/api/apps",
            json={
                "name": "reviewed-app",
                "source": str(source),
                "review_digest": review["review_digest"],
            },
        )
        assert installed.status == 201, await installed.text()
        assert (await installed.json())["ok"] is True


@pytest.mark.asyncio
async def test_any_staged_byte_change_returns_a_fresh_review_without_installing(
    tmp_path: Path,
) -> None:
    async with _client(tmp_path / "home") as client:
        source = _bundle(tmp_path / "source" / "reviewed-app")
        first = await _review(client, source)
        (source / "provider.py").write_text("VALUE = 'changed'\n", encoding="utf-8")

        stale = await client.post(
            "/api/apps",
            json={
                "name": "reviewed-app",
                "source": str(source),
                "review_digest": first["review_digest"],
            },
        )
        assert stale.status == 409, await stale.text()
        fresh = await stale.json()
        assert fresh["needs_consent"] is True
        assert fresh["review_digest"] != first["review_digest"]
        assert not (tmp_path / "home" / "apps" / "reviewed-app").exists()

        accepted = await client.post(
            "/api/apps",
            json={
                "name": "reviewed-app",
                "source": str(source),
                "review_digest": fresh["review_digest"],
            },
        )
        assert accepted.status == 201, await accepted.text()


@pytest.mark.asyncio
async def test_changed_update_needs_review_but_unchanged_disclosure_does_not(
    tmp_path: Path,
) -> None:
    async with _client(tmp_path / "home") as client:
        first_source = _bundle(tmp_path / "source-v1" / "reviewed-app")
        first_review = await _review(client, first_source)
        installed = await client.post(
            "/api/apps",
            json={
                "name": "reviewed-app",
                "source": str(first_source),
                "review_digest": first_review["review_digest"],
            },
        )
        assert installed.status == 201, await installed.text()

        same_disclosure = _bundle(
            tmp_path / "source-v2" / "reviewed-app", version="1.1.0"
        )
        no_change_review = await _review(client, same_disclosure, name="reviewed-app")
        assert no_change_review["needs_consent"] is False
        no_change_update = await client.post(
            "/api/apps/reviewed-app/update",
            json={
                "name": "reviewed-app",
                "source": str(same_disclosure),
                "review_digest": no_change_review["review_digest"],
            },
        )
        assert no_change_update.status == 200, await no_change_update.text()

        changed = _bundle(
            tmp_path / "source-v3" / "reviewed-app",
            version="2.0.0",
            permissions={"api": ["/api/tasks"], "agent": True, "cron": True},
        )
        refused_update = await client.post(
            "/api/apps/reviewed-app/update",
            json={"name": "reviewed-app", "source": str(changed)},
        )
        assert refused_update.status == 409, await refused_update.text()
        refusal = await refused_update.json()
        assert refusal["needs_consent"] is True
        assert refusal["previous_review"]["permissions"].get("agent") is not True
        assert refusal["review"]["permissions"]["agent"] is True
        installed_detail = await (await client.get("/api/apps/reviewed-app")).json()
        assert installed_detail["installed"]["version"] == "1.1.0"
        accepted_update = await client.post(
            "/api/apps/reviewed-app/update",
            json={
                "name": "reviewed-app",
                "source": str(changed),
                "review_digest": refusal["review_digest"],
            },
        )
        assert accepted_update.status == 200, await accepted_update.text()
        installed_detail = await (await client.get("/api/apps/reviewed-app")).json()
        assert installed_detail["installed"]["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_typed_preview_fields_are_rejected_before_a_real_remote_fetch(
    tmp_path: Path,
) -> None:
    hits: list[str] = []

    async def receive(request: web.Request) -> web.Response:
        hits.append(request.path)
        return web.Response(text="not a repository")

    remote = web.Application()
    remote.router.add_route("*", "/{tail:.*}", receive)
    runner = web.AppRunner(remote)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    source = f"http://127.0.0.1:{port}/app.git"

    try:
        async with _client(tmp_path / "home") as client:
            response = await client.post(
                "/api/apps/preview", json={"source": source, "name": {"bad": "type"}}
            )
            assert response.status == 400, await response.text()
            body = await response.json()
            assert body["error"]["code"] == "field_not_a_string"
            assert body["error"]["field"] == "name"
            response = await client.post(
                "/api/apps/preview",
                json={
                    "source": source,
                    "name": "reviewed-app",
                    "registry": {"bad": "type"},
                },
            )
            assert response.status == 400, await response.text()
            registry_error = await response.json()
            assert registry_error["error"]["code"] == "field_not_a_string"
            assert registry_error["error"]["field"] == "registry"
            assert hits == []

            for invalid in (None, [source]):
                response = await client.post(
                    "/api/apps/preview", json={"source": invalid}
                )
                assert response.status == 400, await response.text()
                assert (await response.json())["error"]["code"] == "field_not_a_string"
            assert hits == []
    finally:
        await runner.cleanup()
