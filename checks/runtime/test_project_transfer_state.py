import io
import json
import tempfile
import zipfile
from contextlib import asynccontextmanager

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.engine.tasks import registry
from gideon.engine.tasks.handlers import register_task_routes


@asynccontextmanager
async def transfer_server(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))
    providers = dict(registry._providers)
    registry._providers.clear()
    try:
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client, uploads
    finally:
        registry._providers.clear()
        registry._providers.update(providers)


def multipart(data, field="file"):
    form = FormData()
    form.add_field(field, data, filename="project.zip", content_type="application/zip")
    return form


@pytest.mark.asyncio
@pytest.mark.parametrize("passphrase", ["", "local-archive-passphrase"])
async def test_real_archive_download_preview_and_collision_import(
    tmp_path, monkeypatch, passphrase
):
    async with transfer_server(tmp_path, monkeypatch) as (client, uploads):
        project = await (
            await client.post(
                "/api/projects",
                json={"name": "Portable", "brief": "Carry the evidence"},
            )
        ).json()
        root = tmp_path / "projects" / project["id"]
        (root / "context" / "overview.md").write_text(
            "# Project\nActual persistent context\n"
        )
        (root / "context" / ".env").write_text("SECRET=must-stay-local")
        (root / "worktrees").mkdir()
        (root / "worktrees" / "derived.txt").write_text("worktree-does-not-travel")
        original_metadata = (root / "project.json").read_bytes()
        params = {"passphrase": passphrase}
        download = await client.get(
            f"/api/projects/{project['id']}/export", params=params
        )
        data = await download.read()
        assert download.status == 200 and download.content_type == "application/zip"
        assert int(download.headers["Content-Length"]) == len(data)
        assert int(download.headers["X-Gideon-Entities"]) >= 2
        assert "attachment; filename=" in download.headers["Content-Disposition"]
        if not passphrase:
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                assert "project/context/overview.md" in bundle.namelist()
                combined = b"".join(bundle.read(name) for name in bundle.namelist())
                assert (
                    b"must-stay-local" not in combined
                    and b"worktree-does-not-travel" not in combined
                )
        await client.get("/api/projects")
        before = {path.name for path in (tmp_path / "projects").iterdir()}
        preview = await client.post(
            "/api/projects/import",
            params={**params, "preview": "1"},
            data=multipart(data),
        )
        plan = await preview.json()
        assert preview.status == 200 and plan["preview"] is True
        assert plan["project_name"] != project["name"]
        assert {path.name for path in (tmp_path / "projects").iterdir()} == before
        assert list(uploads.iterdir()) == []
        imported = await client.post(
            "/api/projects/import", params=params, data=multipart(data)
        )
        receipt = await imported.json()
        assert imported.status == 201 and receipt["preview"] is False
        assert receipt["project_id"] != project["id"]
        destination = tmp_path / "projects" / receipt["project_id"]
        assert (destination / "context" / "overview.md").read_bytes() == (
            root / "context" / "overview.md"
        ).read_bytes()
        assert "context/overview.md" in receipt["written"]
        assert (root / "project.json").read_bytes() == original_metadata
        assert list(uploads.iterdir()) == []


@pytest.mark.asyncio
async def test_real_upload_refusals_clean_spooled_files_and_keep_projects(
    tmp_path, monkeypatch
):
    async with transfer_server(tmp_path, monkeypatch) as (client, uploads):
        before = await (await client.get("/api/projects")).json()
        wrong_type = await client.post(
            "/api/projects/import", json={"file": "not-a-zip"}
        )
        assert wrong_type.status == 400
        wrong_field = await client.post(
            "/api/projects/import", data=multipart(b"bad", field="other")
        )
        assert (
            wrong_field.status == 400
            and (await wrong_field.json())["error"] == "file field required"
        )
        malformed = await client.post(
            "/api/projects/import", data=multipart(b"not-a-zip")
        )
        assert malformed.status == 400 and (await malformed.json())["reason"]
        assert list(uploads.iterdir()) == []
        assert await (await client.get("/api/projects")).json() == before
        assert (await client.get("/api/projects/missing/export")).status == 404
