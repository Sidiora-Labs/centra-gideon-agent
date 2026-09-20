import ast
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.request_boundary import (
    RequestValidationError,
    optional_string,
    request_boundary_middleware,
    require_string,
    string_field,
)

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "runtime/gideon/interfaces/dashboard"

WRITE_DOORS = (
    ("chat_title.py", "api_chat_session_rename", ("title",)),
    ("chat_folders.py", "api_chat_folder_create", ("name",)),
    ("handlers/agent_marketplace.py", "api_agent_marketplace_create", ("name",)),
    ("handlers/knowledge.py", "create_item", ("title",)),
    ("handlers/knowledge.py", "create_watched_source", ("name",)),
    ("handlers/knowledge.py", "update_watched_source", ("name",)),
    ("handlers/loop_routes.py", "_build_loop_from_body", ("name", "title")),
    ("handlers/mcp.py", "api_mcp_toggle", ("name",)),
    ("handlers/mcp.py", "api_mcp_remove", ("name",)),
    ("handlers/memory.py", "api_memory_entity_create", ("name",)),
    ("handlers/memory.py", "api_memory_entity_proposals", ("name",)),
    ("handlers/providers.py", "api_provider_create", ("name",)),
    ("handlers/skills.py", "api_skill_overlay_revert", ("name",)),
    ("handlers/tools.py", "api_tools_toggle", ("name",)),
    ("handlers/views.py", "api_dashboard_views", ("name",)),
)

RAW_STRIP = re.compile(
    r"(?:str\()?body(?:\.get\([\"'](?:name|title)[\"'][^\n]*|\[[\"'](?:name|title)[\"']\])[^\n]*\.strip\(\)"
)
SURVIVING_RAW_STRIPS = 13


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    return ast.get_source_segment(source, node) or ""


@pytest.mark.parametrize(("module", "function", "fields"), WRITE_DOORS)
def test_write_door_uses_shared_scalar_validator(
    module: str, function: str, fields: tuple[str, ...]
) -> None:
    source = _function_source(DASHBOARD / module, function)
    for field in fields:
        assert f'string_field(body, "{field}")' in source


def test_scalar_validators_reject_non_strings_without_coercion() -> None:
    with pytest.raises(RequestValidationError, match="name must be a string"):
        require_string(7, "name")
    with pytest.raises(RequestValidationError, match="title must be a string"):
        optional_string([], "title")
    assert optional_string(None, "title") == ""
    assert string_field({"name": "  alpha  "}, "name") == "alpha"


@pytest.mark.asyncio
async def test_scalar_refusal_reaches_request_validation_envelope() -> None:
    async def handler(request: web.Request) -> web.Response:
        string_field(await request.json(), "name")
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/api/write", handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/write", json={"name": 7})
        assert response.status == 400
        assert await response.json() == {
            "error": {
                "code": "bad_request",
                "message": "The request was malformed or carried an unusable parameter.",
            }
        }


def test_raw_name_title_strip_census_is_bounded() -> None:
    surviving = sum(
        len(RAW_STRIP.findall(path.read_text(encoding="utf-8")))
        for path in DASHBOARD.rglob("*.py")
    )
    assert surviving <= SURVIVING_RAW_STRIPS
