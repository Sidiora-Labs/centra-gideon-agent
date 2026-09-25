import asyncio
import json
import os
from contextlib import contextmanager

import pytest
from aiohttp import ClientSession, web

from gideon.core.config.loader import AgentProfile, AppConfig
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import (
    PeopleError,
    PeopleStore,
    lifecycle,
    social,
)
from gideon.workspace.capabilities.communications.tools import create_provider


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


def configured():
    cfg = AppConfig.load()
    cfg.agents = {
        "research": AgentProfile(provider="native"),
        "writer": AgentProfile(provider="native"),
    }
    cfg.save()
    return cfg


def registration(store):
    return social.save(
        store, {"platform": "x", "handle": "alice", "request_key": "alice"}
    )[0]


def request(row, **extra):
    return {
        "account_id": row["id"],
        "account_revision": row["revision"],
        "agent_id": "research",
        "reason": "Research identity assignment",
        "request_key": "assign1",
        **extra,
    }


def change(store, row, state, **extra):
    return lifecycle.change(
        store,
        row["id"],
        {
            "revision": row["revision"],
            "account_revision": row["account_revision"],
            "state": state,
            "reason": "Owner reviewed " + state,
            **extra,
        },
    )


def test_real_configured_agents_and_durable_lifecycle(tmp_path):
    with runtime_home(tmp_path):
        configured()
        store = PeopleStore()
        account = registration(store)
        assert {"id": "research", "provider": "native"} in lifecycle.agents()
        assert {"id": "writer", "provider": "native"} in lifecycle.agents()
        row = lifecycle.create(store, request(account))
        assert row["state"] == "requested"
        assert row["qualification"] == "local_assignment_only"
        assert row["external_account_changed"] is False
        assert row["revision"] == 1
        assert lifecycle.get(PeopleStore(), row["id"])["usable"] is False
        active = change(store, row, "active")
        assert active["revision"] == 2
        assert lifecycle.get(store, row["id"])["usable"] is True
        paused = change(store, active, "paused")
        assert lifecycle.get(store, row["id"])["usable"] is False
        resumed = change(store, paused, "active")
        revoked = change(store, resumed, "revoked")
        assert revoked["revision"] == 5
        assert lifecycle.get(PeopleStore(), row["id"])["state"] == "revoked"
        with pytest.raises(PeopleError) as error:
            change(store, revoked, "active")
        assert error.value.status == 409
        history = lifecycle.history(store, row["id"])
        assert [item["state"] for item in history] == [
            "requested",
            "active",
            "paused",
            "active",
            "revoked",
        ]
        assert all(item["reason"] and item["at"] for item in history)
        assert social.get(store, account["id"])["status"] == "active"
        assert social.get(store, account["id"])["revision"] == 1


def test_single_active_owner_transfer_requires_pause_and_current_revisions(tmp_path):
    with runtime_home(tmp_path):
        configured()
        store = PeopleStore()
        account = registration(store)
        first = lifecycle.create(store, request(account))
        first = change(store, first, "active")
        second = lifecycle.create(
            store, request(account, agent_id="writer", request_key="assign2")
        )
        with pytest.raises(PeopleError) as error:
            change(store, second, "active")
        assert error.value.status == 409
        assert lifecycle.get(store, second["id"])["revision"] == 1
        first = change(store, first, "paused")
        second = change(store, second, "active")
        assert lifecycle.get(store, second["id"])["usable"] is True
        with pytest.raises(PeopleError):
            change(store, first, "active")
        with pytest.raises(PeopleError):
            change(store, second, "revoked", revision=1)
        assert lifecycle.get(store, second["id"])["state"] == "active"
        assert len(lifecycle.history(store, second["id"])) == 2


def test_replay_original_request_returns_current_record_without_new_history(tmp_path):
    with runtime_home(tmp_path):
        configured()
        store = PeopleStore()
        account = registration(store)
        data = request(account)
        row = lifecycle.create(store, data)
        assert lifecycle.create(store, data) == row
        current = change(store, row, "active")
        assert lifecycle.create(store, data) == current
        with pytest.raises(PeopleError) as error:
            lifecycle.create(store, {**data, "reason": "Different intent"})
        assert error.value.status == 409
        assert len(lifecycle.records(store)) == 1
        assert len(lifecycle.history(store, row["id"])) == 2


def test_current_agent_removal_and_account_change_invalidate_readiness(tmp_path):
    with runtime_home(tmp_path):
        cfg = configured()
        store = PeopleStore()
        account = registration(store)
        row = change(store, lifecycle.create(store, request(account)), "active")
        cfg.agents.pop("research")
        cfg.save()
        assert lifecycle.get(store, row["id"])["agent_available"] is False
        assert lifecycle.get(store, row["id"])["usable"] is False
        with pytest.raises(PeopleError) as error:
            change(store, row, "active")
        assert error.value.status == 404
        configured()
        values = {key: account[key] for key in social.FIELDS}
        updated, _ = social.save(
            store,
            {**values, "credential_ref": "ROTATED_CONNECTION", "revision": 1},
            account["id"],
        )
        assert lifecycle.get(store, row["id"])["account_changed"] is True
        assert lifecycle.get(store, row["id"])["usable"] is False
        with pytest.raises(PeopleError):
            change(store, row, "active")
        row = change(store, row, "active", account_revision=updated["revision"])
        assert row["account_revision"] == 2
        assert lifecycle.get(store, row["id"])["usable"] is True
        social.remove(store, account["id"], 2)
        assert lifecycle.get(store, row["id"])["account_available"] is False
        revoked = change(store, row, "revoked")
        assert revoked["state"] == "revoked"
        assert len(lifecycle.history(store, row["id"])) == 4


@pytest.mark.parametrize(
    "extra",
    [
        {"agent_id": "unknown"},
        {"account_id": "missing"},
        {"account_revision": True},
        {"account_revision": 9},
        {"reason": ""},
        {"root": "/another"},
    ],
)
def test_invalid_assignment_request_is_atomic(tmp_path, extra):
    with runtime_home(tmp_path):
        configured()
        store = PeopleStore()
        account = registration(store)
        with pytest.raises(PeopleError):
            lifecycle.create(store, request(account, **extra))
        assert lifecycle.records(store) == []


def test_requested_pause_unknown_target_and_cross_home_isolation(tmp_path):
    with runtime_home(tmp_path / "one"):
        configured()
        store = PeopleStore()
        account = registration(store)
        row = lifecycle.create(store, request(account))
        with pytest.raises(PeopleError):
            change(store, row, "paused")
        with pytest.raises(PeopleError):
            change(store, row, "external_deleted")
        assert len(lifecycle.history(store, row["id"])) == 1
    with runtime_home(tmp_path / "two"):
        configured()
        store = PeopleStore()
        assert lifecycle.records(store) == []
        assert lifecycle.history(store, row["id"]) == []
        with pytest.raises(PeopleError):
            lifecycle.get(store, row["id"])


def test_native_assignment_tools_use_real_agent_config(tmp_path):
    with runtime_home(tmp_path):
        configured()
        store = PeopleStore()
        account = registration(store)

        async def scenario():
            provider = create_provider()
            result = await provider.invoke("people_platform_agents", {})
            assert "research" in {
                agent["id"] for agent in json.loads(result.output)["agents"]
            }
            result = await provider.invoke(
                "people_platform_assignment_create", request(account)
            )
            assert result.success, result.error
            row = json.loads(result.output)["assignment"]
            result = await provider.invoke(
                "people_platform_assignment_change",
                {
                    "assignment_id": row["id"],
                    "revision": 1,
                    "account_revision": 1,
                    "state": "active",
                    "reason": "Native owner review",
                },
            )
            assert json.loads(result.output)["assignment"]["state"] == "active"
            result = await provider.invoke(
                "people_platform_assignment_get", {"assignment_id": row["id"]}
            )
            assert json.loads(result.output)["assignment"]["usable"] is True
            result = await provider.invoke("people_platform_assignments", {})
            assert len(json.loads(result.output)["assignments"]) == 1
            result = await provider.invoke(
                "people_platform_assignment_history", {"assignment_id": row["id"]}
            )
            assert len(json.loads(result.output)["history"]) == 2
            definitions = {tool.name: tool for tool in await provider.list_tools()}
            assert definitions["people_platform_assignment_create"].requires_approval
            assert (
                definitions["people_platform_assignment_get"].requires_approval is False
            )

        asyncio.run(scenario())
