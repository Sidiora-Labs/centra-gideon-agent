"""A 405 on an /api/* route answers in the one wire error envelope (#2846).

``spa_fallback`` rewrites the router's *unmatched-route* refusal (``HTTPNotFound``)
into the coded JSON envelope for ``/api/*`` so a JSON client can parse it. But a
REAL ``/api/*`` route hit with the wrong method raises ``HTTPMethodNotAllowed``,
which used to sail past that branch and answer aiohttp's ``text/plain`` default —
the very ``405: Method Not Allowed`` body a branching client cannot read. This
drives a wrong method through the actual router + middleware (``test_wire_error_
envelope_census`` covers handler-emitted envelopes, not middleware-level 405s).
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.server import spa_fallback


def _make_app() -> web.Application:
    app = web.Application(middlewares=[spa_fallback])

    async def _post_only(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app.router.add_post("/api/thing", _post_only)
    app.router.add_post("/notapi/thing", _post_only)
    return app


@pytest.mark.asyncio
async def test_api_405_answers_in_the_wire_envelope() -> None:
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.get("/api/thing")
        assert resp.status == 405
        assert resp.content_type == "application/json"
        body = await resp.json()
        assert body["error"]["code"] == "method_not_allowed"
        assert body["error"]["message"]
        assert resp.headers.get("Allow") == "POST"


@pytest.mark.asyncio
async def test_non_api_405_is_left_as_the_aiohttp_default() -> None:
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.get("/notapi/thing")
        assert resp.status == 405
        assert resp.content_type == "text/plain"


@pytest.mark.asyncio
async def test_api_404_still_answers_in_the_wire_envelope() -> None:
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.get("/api/does-not-exist")
        assert resp.status == 404
        assert resp.content_type == "application/json"
        body = await resp.json()
        assert body["error"]["code"] == "not_found"


def _multi_method_app() -> web.Application:
    """A real route that answers two methods, so ``Allow`` carries more than one token."""
    app = web.Application(middlewares=[spa_fallback])

    async def _ok(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app.router.add_post("/api/multi", _ok)
    app.router.add_put("/api/multi", _ok)
    return app


@pytest.mark.asyncio
async def test_the_envelope_preserves_every_allowed_method() -> None:
    """req 89 ac_2 — the allowed-method information survives the rewrite INTACT.

    The single-method case above cannot tell "the Allow header was forwarded" from "the
    one method that happened to be registered was echoed": a rewrite that kept only the
    first token, or dropped the header and re-derived it from the request, passes it.
    A route answering two methods pins the whole set, which is what a client that
    retries with a different verb actually reads.
    """
    async with TestClient(TestServer(_multi_method_app())) as client:
        resp = await client.delete("/api/multi")
        assert resp.status == 405
        assert resp.content_type == "application/json"
        assert (await resp.json())["error"]["code"] == "method_not_allowed"
        allowed = {m.strip() for m in (resp.headers.get("Allow") or "").split(",")}
        assert {"POST", "PUT"} <= allowed, resp.headers.get("Allow")


@pytest.mark.asyncio
async def test_a_405_envelope_carries_a_human_message_from_the_shared_registry() -> (
    None
):
    """The envelope is the SHARED one — the message is the registry's meaning for the
    code, not a string invented at the middleware. A hand-written body here would be a
    second vocabulary for the same failure."""
    from gideon.http_errors import HTTP_ERROR_CODES

    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.get("/api/thing")
        body = await resp.json()
        assert body["error"]["message"] == HTTP_ERROR_CODES["method_not_allowed"]
