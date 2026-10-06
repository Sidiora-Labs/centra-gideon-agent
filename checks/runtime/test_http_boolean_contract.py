import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.http_request import (
    MISSING,
    RequestValidationError,
    bool_field,
    optional_bool,
    read_json_body,
    require_bool,
)
from gideon.interfaces.dashboard.request_boundary import request_boundary_middleware
from gideon.security.safety_flags import strict_bool, yes_or_no


@pytest.mark.parametrize("value", ["false", "true", 0, 1, [], {}, None])
def test_boolean_requests_refuse_non_literals(value):
    with pytest.raises(RequestValidationError) as caught:
        bool_field({"enabled": value}, "enabled", default=False)
    assert caught.value.code == "field_not_a_boolean"
    assert "enabled" in caught.value.message


def test_boolean_omission_and_null_contract():
    assert bool_field({}, "enabled", default=True) is True
    assert bool_field({"enabled": False}, "enabled", default=True) is False
    assert bool_field({"enabled": None}, "enabled", default=None) is None
    assert optional_bool({}, "enabled") is MISSING
    with pytest.raises(RequestValidationError) as caught:
        require_bool({}, "enabled")
    assert caught.value.code == "field_required"
    with pytest.raises(RequestValidationError):
        optional_bool({"enabled": None}, "enabled")
    assert str(RequestValidationError("existing message")) == "existing message"
    assert RequestValidationError("existing message").code == "bad_request"


@pytest.mark.parametrize("value", ["false", " OFF ", "No", "0", "n"])
def test_stored_false_spellings_remain_false(value):
    assert yes_or_no(value) is False
    assert strict_bool(value, field="enabled", default=True) is False


@pytest.mark.parametrize("value", ["", "unreadable", 0, 1, None, [], {}])
def test_declared_tool_boolean_has_no_guess_for_unknown(value):
    assert yes_or_no(value) is None


def test_missing_stored_setting_differs_from_unreadable():
    assert strict_bool(None, field="enabled", default=False, absent=True) is True
    assert (
        strict_bool("unreadable", field="enabled", default=False, absent=True) is False
    )


@pytest.mark.asyncio
async def test_request_boundary_preserves_typed_boolean_refusal():
    async def change(request):
        body = await read_json_body(request)
        return web.json_response(
            {"enabled": bool_field(body, "enabled", default=False)}
        )

    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/api/change", change)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/change", json={"enabled": "false"})
        assert response.status == 400
        payload = await response.json()
        assert payload["error"]["code"] == "field_not_a_boolean"
        response = await client.post("/api/change", json={"enabled": False})
        assert response.status == 200
        assert await response.json() == {"enabled": False}
