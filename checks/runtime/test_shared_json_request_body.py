from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.http_request import (
    RequestValidationError,
    read_json_body,
)

RUNTIME = Path(__file__).resolve().parents[2] / "runtime" / "gideon"


async def _read(request: web.Request) -> web.Response:
    try:
        body = await read_json_body(request)
    except RequestValidationError as exc:
        return web.json_response({"error": type(exc).__name__}, status=400)
    return web.json_response(body)


@pytest.mark.asyncio
async def test_read_json_body_accepts_objects_and_empty_bodies() -> None:
    app = web.Application()
    app.router.add_post("/body", _read)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/body", json={"name": "gideon"})
        assert response.status == 200
        assert await response.json() == {"name": "gideon"}

        response = await client.post("/body")
        assert response.status == 200
        assert await response.json() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "content_type", "error_type"),
    [
        ('{"valid": true}', "text/plain", "RequestValidationError"),
        ("not-json", "application/json", "RequestValidationError"),
        ("[]", "application/json", "RequestBodyTypeError"),
    ],
)
async def test_read_json_body_rejects_invalid_requests(
    body: str, content_type: str, error_type: str
) -> None:
    app = web.Application()
    app.router.add_post("/body", _read)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/body", data=body, headers={"Content-Type": content_type}
        )
        assert response.status == 400
        assert await response.json() == {"error": error_type}


def test_api_handlers_have_no_inline_request_json_reads() -> None:
    offenders = [
        path.relative_to(RUNTIME).as_posix()
        for path in RUNTIME.rglob("*.py")
        if path.name != "request_boundary.py"
        and "await request.json()" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
