"""Acquire native signed workflow source receipts through authenticated local HTTP."""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.security.durable_work import accepted_origin_of_request


async def authenticated_origin():
    receipt = []

    async def accept(request):
        origin = accepted_origin_of_request(request)
        assert origin is not None
        receipt.append(origin)
        return web.json_response({"accepted": True})

    app = web.Application(middlewares=[token_auth_middleware(port=19419)])
    app.router.add_post("/accept", accept)
    token = generate_token("lifecycle-owner", kind="desktop")
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/accept", cookies={"gideon_token_19419": token})
        assert response.status == 200
    return receipt[0]
