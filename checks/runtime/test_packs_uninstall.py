"""Capability-pack removal preserves edited files and refuses deployed dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.packs import bundled as pack_bundled

PACK = "personal-cfo"
TRIGGER_ID = "pack-personal-cfo-spending-digest"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(root))
    return root


def _gateway() -> web.Application:
    from gideon.interfaces.dashboard.handlers.packs import register_pack_routes

    app = web.Application()
    register_pack_routes(app)
    return app


def _skip_connectors() -> dict[str, dict[str, str]]:
    source = pack_bundled.get_bundled(PACK)
    assert source is not None
    declared = json.loads((source.source / "connectors.json").read_text(encoding="utf-8"))
    return {str(row["name"]): {"mode": "skip"} for row in declared}


async def _install(client: TestClient) -> None:
    response = await client.post(
        f"/api/packs/bundled/{PACK}/install",
        json={"connector_choices": _skip_connectors()},
    )
    assert response.status == 200, await response.text()


def _ledger(home: Path) -> dict[str, Any]:
    return json.loads((home / "packs" / "installed.json").read_text(encoding="utf-8"))


def _component_paths(home: Path) -> dict[str, Path]:
    locks = _ledger(home)[PACK]["component_locks"]
    return {ref: home / lock["path"] for ref, lock in locks.items()}


async def _uninstall(
    client: TestClient, *, confirm: bool, token: str = ""
) -> tuple[int, dict[str, Any]]:
    response = await client.post(
        f"/api/packs/{PACK}/uninstall",
        json={"confirm": confirm, "confirmation_token": token},
    )
    return response.status, await response.json()


@pytest.mark.asyncio
async def test_dry_run_names_all_components_and_changes_no_pack_state(home: Path) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        paths = _component_paths(home)
        ledger_before = (home / "packs" / "installed.json").read_bytes()
        status, body = await _uninstall(client, confirm=False)

    assert status == 200, body
    plan = body["uninstall"]
    assert plan["applied"] is False
    assert plan["confirmation_token"]
    assert sorted(plan["removed"]) == sorted(paths), "every pack component is predicted"
    assert plan["kept"] == [] and plan["missing"] == [] and plan["in_use"] == []
    assert len(paths) == 8
    assert all(path.exists() for path in paths.values())
    assert (home / "packs" / "installed.json").read_bytes() == ledger_before
    assert PACK in _ledger(home)


@pytest.mark.asyncio
async def test_confirmed_uninstall_removes_only_pack_files_then_ledger(home: Path) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        paths = _component_paths(home)
        _, preview = await _uninstall(client, confirm=False)
        status, body = await _uninstall(
            client, confirm=True, token=preview["uninstall"]["confirmation_token"]
        )
        listed = await (await client.get("/api/packs/installed")).json()
        store = await (await client.get("/api/packs/bundled")).json()

    assert status == 200, body
    assert body["uninstall"]["applied"] is True
    assert all(not path.exists() for path in paths.values())
    assert not (home / "packs" / "staged" / PACK).exists()
    assert listed["packs"] == []
    assert PACK in {pack["name"] for pack in store["packs"]}
    assert not (home / "agents" / "cfo").exists()
    assert not (home / "workflows" / "defs" / "cfo-monthly-review").exists()
    for directory in ("skills", "agents", "prompts", "workflows/defs"):
        assert (home / directory).is_dir(), directory


@pytest.mark.asyncio
async def test_edited_component_stays_while_unedited_siblings_are_removed(home: Path) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        edited = home / "skills" / "cfo-budget-review" / "SKILL.md"
        edited.write_text(edited.read_text(encoding="utf-8") + "\nMy own rule.\n", encoding="utf-8")
        _, preview = await _uninstall(client, confirm=False)
        plan = preview["uninstall"]
        assert plan["kept"] == [
            {"ref": "skill:cfo-budget-review", "reason": "you edited it after it was installed, so it stays"}
        ]
        status, body = await _uninstall(client, confirm=True, token=plan["confirmation_token"])

    assert status == 200, body
    assert body["uninstall"]["kept"] == plan["kept"]
    assert edited.read_text(encoding="utf-8").endswith("My own rule.\n")
    assert not (home / "skills" / "cfo-statement-fetch").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recorded",
    [
        "../outside.txt",
        "skills/cfo-budget-review/../../outside.txt",
        "prompts/other-thing.yaml",
    ],
)
async def test_wrong_ledger_destination_is_never_deleted(
    home: Path, recorded: str
) -> None:
    from gideon.extensions.packs.update import component_digest

    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        victim = (home / recorded).resolve()
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_text("not the pack's", encoding="utf-8")
        ledger = _ledger(home)
        ledger[PACK]["component_locks"]["prompt:cfo-spending-digest"] = {
            "source": "pack:personal-cfo@1.0.0",
            "computedHash": component_digest(victim),
            "path": recorded,
        }
        (home / "packs" / "installed.json").write_text(json.dumps(ledger), encoding="utf-8")
        _, preview = await _uninstall(client, confirm=False)
        plan = preview["uninstall"]
        status, body = await _uninstall(client, confirm=True, token=plan["confirmation_token"])

    assert status == 200, body
    assert victim.read_text(encoding="utf-8") == "not the pack's"
    kept = {row["ref"]: row["reason"] for row in body["uninstall"]["kept"]}
    assert "not where this component installs" in kept["prompt:cfo-spending-digest"] or "unsafe" in kept["prompt:cfo-spending-digest"]


@pytest.mark.asyncio
async def test_symlinked_component_is_kept_without_following_it(home: Path, tmp_path: Path) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        prompt = home / "prompts" / "cfo-spending-digest.yaml"
        victim = tmp_path / "victim.yaml"
        victim.write_bytes(prompt.read_bytes())
        prompt.unlink()
        prompt.symlink_to(victim)
        _, preview = await _uninstall(client, confirm=False)
        plan = preview["uninstall"]
        status, body = await _uninstall(client, confirm=True, token=plan["confirmation_token"])

    assert status == 200, body
    assert victim.exists() and prompt.is_symlink()
    assert "prompt:cfo-spending-digest" in {row["ref"] for row in body["uninstall"]["kept"]}


@pytest.mark.asyncio
async def test_deployed_agent_and_automation_block_until_removed(home: Path) -> None:
    from gideon.automation.triggers.store import TriggerStore
    from gideon.core.config.loader import AppConfig

    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        assert (await client.post(f"/api/packs/{PACK}/roster/deploy", json={})).status == 200
        assert (await client.post(f"/api/packs/{PACK}/triggers/deploy", json={})).status == 200
        paths = _component_paths(home)

        dry_status, dry = await _uninstall(client, confirm=False)
        trigger_name = next(
            row["name"] for row in dry["uninstall"]["in_use"]
            if row["kind"] == "automation"
        )
        status, body = await _uninstall(
            client, confirm=True, token=dry["uninstall"]["confirmation_token"]
        )
        assert all(path.exists() for path in paths.values())
        assert PACK in _ledger(home)

        config = AppConfig.load()
        del config.agents["cfo"]
        config.save()
        assert TriggerStore(home).delete(TRIGGER_ID)
        _, fresh = await _uninstall(client, confirm=False)
        after_status, after = await _uninstall(
            client, confirm=True, token=fresh["uninstall"]["confirmation_token"]
        )

    assert dry_status == 200
    assert dry["uninstall"]["in_use"] == [
        {"kind": "agent", "id": "cfo", "name": "cfo"},
        {"kind": "automation", "id": TRIGGER_ID, "name": trigger_name},
    ]
    assert status == 409 and body["error"]["code"] == "pack_in_use"
    assert after_status == 200 and after["uninstall"]["applied"] is True


@pytest.mark.asyncio
async def test_stale_confirmation_keeps_newly_edited_component(home: Path) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        edited = home / "skills" / "cfo-budget-review" / "SKILL.md"
        _, preview = await _uninstall(client, confirm=False)
        edited.write_text(edited.read_text(encoding="utf-8") + "\nNew edit.\n", encoding="utf-8")
        status, body = await _uninstall(
            client, confirm=True, token=preview["uninstall"]["confirmation_token"]
        )

    assert status == 409 and body["error"]["code"] == "confirmation_required"
    assert edited.exists()
    assert PACK in _ledger(home)


@pytest.mark.asyncio
async def test_missing_pack_and_missing_confirmation_are_safe(home: Path) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        missing_status, missing = await _uninstall(client, confirm=True)
        await _install(client)
        paths = _component_paths(home)
        confirm_status, confirm = await _uninstall(client, confirm=True)

    assert missing_status == 404 and missing["error"]["code"] == "pack_not_installed"
    assert confirm_status == 409 and confirm["error"]["code"] == "confirmation_required"
    assert all(path.exists() for path in paths.values())
    assert PACK in _ledger(home)
