import asyncio
import json
import os
from contextlib import contextmanager

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import (
    PeopleError,
    PeopleStore,
    social,
)
from gideon.workspace.capabilities.communications.tools import create_provider


def values(**extra):
    return {
        "platform": "x",
        "handle": "@Alice",
        "request_key": "register-alice",
        **extra,
    }


def update(row, **extra):
    return {
        **{key: row[key] for key in social.FIELDS},
        "revision": row["revision"],
        **extra,
    }


def test_normalization_durable_restart_and_request_identity(tmp_path):
    store = PeopleStore(tmp_path)
    row, created = social.save(store, values(handle="＠Ａｌｉｃｅ"))
    assert created
    assert row["handle"] == "alice"
    assert row["profile_url"] == "https://x.com/alice"
    assert row["qualification"] == "registry_only"
    assert row["revision"] == 1
    assert social.accounts(PeopleStore(tmp_path)) == [row]
    replay, created = social.save(store, values(handle="alice"))
    assert not created
    assert replay == row
    assert len(social.history(store, row["id"])) == 1
    with pytest.raises(PeopleError) as error:
        social.save(store, values(handle="bob"))
    assert error.value.status == 409
    assert social.get(store, row["id"]) == row


def test_duplicate_identity_and_platform_distinction(tmp_path):
    store = PeopleStore(tmp_path)
    row, _ = social.save(store, values())
    with pytest.raises(PeopleError) as error:
        social.save(store, values(request_key="another", handle="ALICE"))
    assert error.value.status == 409
    second, _ = social.save(store, values(request_key="github", platform="github"))
    assert second["profile_url"] == "https://github.com/alice"
    assert len(social.accounts(store)) == 2
    with pytest.raises(PeopleError):
        social.save(store, update(second, platform="x"), second["id"])
    assert social.get(store, second["id"])["revision"] == 1
    assert social.get(store, row["id"])["revision"] == 1


def test_person_links_and_atomic_optimistic_history(tmp_path):
    store = PeopleStore(tmp_path)
    person = store.save({"name": "Alice"})
    row, _ = social.save(store, values(person_id=person["id"]))
    edited, created = social.save(
        store, update(row, status="paused", notes="Away"), row["id"]
    )
    assert not created
    assert edited["person_id"] == person["id"]
    assert edited["revision"] == 2
    assert edited["created_at"] == row["created_at"]
    with pytest.raises(PeopleError) as error:
        social.save(store, update(row, person_id=None), row["id"])
    assert error.value.status == 409
    with pytest.raises(PeopleError) as error:
        social.save(store, update(edited, person_id="missing"), row["id"])
    assert error.value.status == 404
    assert social.get(store, row["id"]) == edited
    archived, _ = social.save(
        store, update(edited, status="archived", person_id=None), row["id"]
    )
    assert archived["revision"] == 3
    assert archived["person_id"] is None
    history = social.history(store, row["id"])
    assert [item["event"] for item in history] == ["created", "updated", "updated"]
    assert [item["account"]["status"] for item in history] == [
        "active",
        "paused",
        "archived",
    ]
    assert all(item["at"] for item in history)


def test_removal_tombstone_and_new_registration(tmp_path):
    store = PeopleStore(tmp_path)
    row, _ = social.save(store, values())
    with pytest.raises(PeopleError) as error:
        social.remove(store, row["id"], 2)
    assert error.value.status == 409
    result = social.remove(store, row["id"], 1)
    assert result["external_account_changed"] is False
    assert result["removed"] is True
    assert social.accounts(store) == []
    assert social.history(store, row["id"])[-1]["event"] == "removed"
    with pytest.raises(PeopleError) as error:
        social.save(store, values())
    assert error.value.status == 410
    with pytest.raises(PeopleError) as error:
        social.get(store, row["id"])
    assert error.value.status == 404
    replacement, created = social.save(store, values(request_key="replacement"))
    assert created
    assert replacement["id"] != row["id"]
    assert len(social.history(store, row["id"])) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"platform": "unsupported"},
        {"handle": ""},
        {"handle": "@@@"},
        {"handle": "two names"},
        {"handle": "https://example.com"},
        {"handle": "a\x00b"},
        {"handle": "a\u202eb"},
        {"profile_url": "javascript:alert(1)"},
        {"profile_url": "http://example.com"},
        {"profile_url": "https://name:secret@example.com"},
        {"profile_url": "https://[bad"},
        {"profile_url": "https://example.com:abc"},
        {"profile_url": "https://example.com:444"},
        {"profile_url": "https://example.com/a b"},
        {"credential_ref": "secret token"},
        {"status": "verified"},
        {"person_id": ""},
        {"home": "/outside"},
        {"notes": "x" * 10001},
    ],
)
def test_invalid_registration_never_persists(tmp_path, change):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError):
        social.save(store, values(**change))
    assert social.accounts(store) == []


def test_custom_https_is_only_metadata_and_home_isolation(tmp_path):
    first = PeopleStore(tmp_path / "first")
    second = PeopleStore(tmp_path / "second")
    row, _ = social.save(
        first,
        values(
            platform="mastodon",
            handle="@alice@example.social",
            profile_url="https://example.social/@alice",
            credential_ref="SOCIAL_CONNECTION",
        ),
    )
    assert row["handle"] == "alice@example.social"
    assert row["credential_ref"] == "SOCIAL_CONNECTION"
    assert row["qualification"] == "registry_only"
    assert social.accounts(second) == []
    with pytest.raises(PeopleError):
        social.get(second, row["id"])
    assert social.history(second, row["id"]) == []
    assert social.get(first, row["id"]) == row


@contextmanager
def runtime_home(path):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous


def test_native_registry_tools_and_approval_metadata(tmp_path):
    async def scenario():
        provider = create_provider()
        result = await provider.invoke("people_social_create", {"account": values()})
        assert result.success, result.error
        row = json.loads(result.output)["account"]
        result = await provider.invoke("people_social_get", {"account_id": row["id"]})
        assert json.loads(result.output)["account"] == row
        result = await provider.invoke(
            "people_social_update",
            {"account_id": row["id"], "account": update(row, status="archived")},
        )
        assert json.loads(result.output)["account"]["revision"] == 2
        result = await provider.invoke("people_social_accounts", {})
        assert len(json.loads(result.output)["accounts"]) == 1
        result = await provider.invoke(
            "people_social_remove", {"account_id": row["id"], "revision": 1}
        )
        assert not result.success
        result = await provider.invoke(
            "people_social_remove", {"account_id": row["id"], "revision": 2}
        )
        assert json.loads(result.output)["external_account_changed"] is False
        result = await provider.invoke(
            "people_social_history", {"account_id": row["id"]}
        )
        assert len(json.loads(result.output)["history"]) == 3
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions["people_social_accounts"].requires_approval is False
        assert definitions["people_social_create"].requires_approval is True
        assert definitions["people_social_update"].requires_approval is True
        assert definitions["people_social_remove"].requires_approval is True

    with runtime_home(tmp_path):
        asyncio.run(scenario())


def test_actual_http_registration_validation_revision_and_history(tmp_path):
    async def scenario():
        app = web.Application()
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/social/accounts"
        try:
            async with ClientSession() as client:
                async with client.post(base, json=values()) as response:
                    assert response.status == 201
                    row = (await response.json())["account"]
                path = base + "/" + row["id"]
                async with client.post(base, json=values()) as response:
                    assert response.status == 200
                    assert (await response.json())["created"] is False
                async with client.put(
                    path, json=update(row, status="archived")
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["account"]["revision"] == 2
                async with client.put(path, json=update(row)) as response:
                    assert response.status == 409
                async with client.delete(
                    path, json={"revision": 2, "home": "elsewhere"}
                ) as response:
                    assert response.status == 400
                async with client.get(path) as response:
                    assert (await response.json())["account"]["status"] == "archived"
                async with client.delete(path, json={"revision": 2}) as response:
                    assert (await response.json())["removed"] is True
                async with client.get(path + "/history") as response:
                    assert len((await response.json())["history"]) == 3
                async with client.get(base) as response:
                    assert (await response.json())["accounts"] == []
        finally:
            await runner.cleanup()

    with runtime_home(tmp_path):
        asyncio.run(scenario())
