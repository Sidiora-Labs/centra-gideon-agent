import asyncio

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
    use_persistent_secret,
)
from gideon.workspace.capabilities.wellbeing.lifestyle_profile_http import register

BASE = "/api/capabilities/wellbeing/lifestyle-profiles"


def payload(**changes):
    value = {
        "request_id": "create-1",
        "observed_at": "2026-09-25T08:30:00Z",
        "source": "Primary care intake",
        "reported_sex": "male",
        "sex_source": "Patient report",
        "smoking_status": "never",
        "diet_quality": {"value": 8, "scale": {"minimum": 0, "maximum": 10, "label": "0–10 intake"}},
        "stress": {"value": 2, "scale": {"minimum": 0, "maximum": 10, "label": "0–10 intake"}},
        "reported_bmi": 24.2,
        "condition_labels": ["Seasonal allergies"],
        "reported_daily_alcohol": None,
    }
    value.update(changes)
    return value


async def client(home):
    app = web.Application(middlewares=[token_auth_middleware()])
    app.router.add_get("/session", lambda request: web.json_response({"ok": True}))
    register(app, home)
    result = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
    await result.start_server()
    return result


async def authenticate(connection):
    response = await connection.get("/session?token=" + generate_token("lifestyle-owner"))
    assert response.status == 200


def test_authenticated_author_correct_reload_export_and_home_isolation(tmp_path):
    async def journey():
        use_ephemeral_secret()
        first = await client(tmp_path / "first")
        second = await client(tmp_path / "second")
        try:
            assert (await first.get(BASE)).status in (401, 403)
            await authenticate(first)
            await authenticate(second)
            response = await first.post(BASE, json=payload())
            assert response.status == 201
            created = await response.json()
            assert created["revision"] == 1
            assert created["reported_daily_alcohol"] is None
            assert created["condition_labels"] == ["Seasonal allergies"]
            replay = await first.post(BASE, json=payload())
            assert replay.status == 201
            assert await replay.json() == created
            empty = await second.get(BASE)
            assert empty.status == 200
            assert await empty.json() == {"records": []}
            resource = BASE + "/" + created["id"]
            hidden = await second.get(resource)
            assert hidden.status == 404 and (await hidden.json())["error"]["code"] == "not_found"
            correction = {
                "request_id": "correct-1",
                "revision": 1,
                "smoking_status": "former",
                "reported_bmi": 24.0,
                "reported_daily_alcohol": {"value": 12, "unit": "g_per_day"},
            }
            response = await first.put(resource, json=correction)
            assert response.status == 200
            updated = await response.json()
            assert updated["revision"] == 2
            assert updated["observed_at"] == created["observed_at"]
            assert updated["source"] == created["source"]
            versions = await first.get(resource + "/history")
            assert versions.status == 200 and (await versions.json())["history"] == [created, updated]
            stale = await first.put(resource, json={**correction, "request_id": "stale", "revision": 1})
            assert stale.status == 409 and (await stale.json())["error"]["code"] == "conflict"
            exported = await first.get(BASE + "/export")
            document = await exported.json()
            assert exported.status == 200
            assert document["records"] == [updated] and document["history"] == [created, updated]
            await first.close()
            first = await client(tmp_path / "first")
            await authenticate(first)
            reloaded = await first.get(resource)
            assert reloaded.status == 200
            assert await reloaded.json() == updated
        finally:
            await first.close()
            await second.close()
            use_persistent_secret()

    asyncio.run(journey())


def test_http_refuses_unknown_fields_and_preserves_empty_store(tmp_path):
    async def journey():
        use_ephemeral_secret()
        connection = await client(tmp_path)
        try:
            await authenticate(connection)
            for bad in (
                payload(birth_date="1990-01-01"),
                payload(reported_bmi="24"),
                payload(observed_at="2026-09-25"),
            ):
                response = await connection.post(BASE, json=bad)
                assert response.status == 400
                assert (await response.json())["error"]["code"] == "invalid_request"
            response = await connection.get(BASE + "?home=/tmp/other")
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "invalid_request"
            records = await connection.get(BASE)
            assert records.status == 200
            assert await records.json() == {"records": []}
        finally:
            await connection.close()
            use_persistent_secret()

    asyncio.run(journey())
