import json
import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_importing_the_same_project_archive_twice_allocates_new_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    from gideon.core.config.loader import config_dir
    from gideon.engine.tasks.hierarchy import HierarchyStore
    from gideon.engine.tasks.hierarchy_handlers import register_hierarchy_routes
    from gideon.automation.workflows.project_archive import export_project_archive

    store = HierarchyStore()
    source = store.create_project(
        "Archive source",
        agent_instructions_template="Use the source brief only.",
        brief="Rework the ingest path.",
    )
    source_path = config_dir() / "projects" / source.id / "project.json"
    source_before = json.loads(source_path.read_text(encoding="utf-8"))
    archive_bytes, _ = export_project_archive(
        source.id,
        project_root=config_dir() / "projects" / source.id,
        project_name=source.name,
    )

    app = web.Application()
    register_hierarchy_routes(app)
    async with TestClient(TestServer(app)) as client:
        imported_ids = []
        for _ in range(2):
            form = FormData()
            form.add_field("file", archive_bytes, filename="archive.zip", content_type="application/zip")
            response = await client.post("/api/projects/import", data=form)
            assert response.status == 201
            imported_ids.append((await response.json())["project_id"])

    assert len(set(imported_ids)) == 2
    assert source.id not in imported_ids
    assert json.loads(source_path.read_text(encoding="utf-8")) == source_before
    imported = [store.get_project(identifier) for identifier in imported_ids]
    assert all(project is not None for project in imported)
    assert all(project.brief == source.brief for project in imported)
    assert all(project.agent_instructions_template == source.agent_instructions_template for project in imported)
    assert all(project.workspace_dir == "" for project in imported)
    assert {project.name for project in imported} == {"Archive source (imported-1)", "Archive source (imported-2)"}
