import base64
import copy
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.workflows.project_archive import decrypt_archive, encrypt_archive
from gideon.core.config.loader import AgentProfile
from gideon.interfaces.dashboard.handlers.capabilities_identity_bundles import (
    PREFIX,
    register,
)
from gideon.workspace.capabilities.experience.avatar import AvatarStore, avatar_info
from gideon.workspace.capabilities.experience.store import ExperienceStore
from gideon.workspace.capabilities.identity.bundle_extended import (
    FORMAT,
    MAX_ENCODED,
    ExtendedBundleService,
)
from gideon.workspace.capabilities.identity.bundle_inventory import (
    GROUPS,
    BundleInventory,
    digest,
)
from gideon.workspace.capabilities.identity.bundles import FORMAT as V1
from gideon.workspace.capabilities.identity.store import ConflictError, StoryStore
from gideon.workspace.capabilities.identity.twin import TwinStore

PASSPHRASE = "real-local-encryption-passphrase"


def seed(home):
    service = ExtendedBundleService(home)
    twin = TwinStore(home / "capabilities/identity/twin.sqlite3")
    twin.save_document(
        title="Private origin",
        text="Unshared formative experience",
        expected_revision=0,
        private=True,
    )
    twin.configure(
        expected_revision=1,
        enabled=True,
        traits={"patience": 8},
        personas=[
            {
                "id": "calm",
                "name": "Calm",
                "instructions": "Consider carefully",
                "trait_adjustments": {"patience": 9},
            }
        ],
        active_persona_id="calm",
    )
    stories = StoryStore(home / "capabilities/identity/stories.sqlite3")
    parent = stories.create(
        prompt="Origin?", theme="origin", text="First answer", request_id="parent"
    )
    stories.update(
        parent["id"],
        expected_revision=1,
        prompt="Origin?",
        theme="origin",
        text="Revised answer",
    )
    child = stories.create(
        prompt="What next?",
        theme="future",
        text="Follow-up answer",
        parent_id=parent["id"],
        request_id="child",
    )
    service.continuity.append_anchor(slot="persona", text="Careful observer")
    return service, twin, stories, parent, child


def export(service, groups):
    return service.export_bundle(groups=groups, passphrase=PASSPHRASE)


def preview(service, bundle, groups):
    return service.preview(bundle=bundle, groups=groups, passphrase=PASSPHRASE)


def apply(service, bundle, groups, plan, key="apply"):
    return service.apply_bundle(
        bundle=bundle,
        groups=groups,
        passphrase=PASSPHRASE,
        preview_token=plan["preview_token"],
        request_id=key,
    )


def envelope(groups):
    return {
        "format": FORMAT,
        "ciphertext": base64.b64encode(
            encrypt_archive(
                json.dumps({"format": FORMAT, "groups": groups}).encode(), PASSPHRASE
            )
        ).decode(),
    }


def test_inventory_projects_real_canonical_groups_without_provider_configuration(
    tmp_path,
):
    service, twin, stories, parent, child = seed(tmp_path)
    rows = {row["id"]: row for row in service.inventory()["groups"]}
    assert set(rows) == {"persona", "self_notes", *GROUPS}
    assert rows["human_twin"]["count"] == 1
    assert rows["autobiography"]["count"] == 2
    assert rows["avatars"]["count"] == 0
    assert rows["agent_definitions"]["available"]
    assert service.inventory()["format"] == FORMAT
    assert service.inventory()["max_encoded_bytes"] == MAX_ENCODED
    assert "credentials" in service.inventory()["exclusions"]
    assert "account_identity" in service.inventory()["exclusions"]
    assert twin.snapshot()["enabled"]
    assert stories.get(child["id"])["parent_id"] == parent["id"]
    assert "Private origin" not in json.dumps(service.inventory())


def test_two_home_human_group_roundtrip_preserves_history_privacy_and_disables_context(
    tmp_path,
):
    source, twin, stories, parent, child = seed(tmp_path / "source")
    groups = ["human_twin", "autobiography"]
    bundle = export(source, groups)
    assert bundle["format"] == FORMAT
    assert "First answer" not in json.dumps(bundle)
    destination = ExtendedBundleService(tmp_path / "destination")
    plan = preview(destination, bundle, groups)
    assert plan["can_apply"]
    assert all(row["new"] and not row["blocked"] for row in plan["groups"])
    receipt = apply(destination, bundle, groups, plan)
    assert receipt["status"] == "applied"
    assert receipt["errors"] == []
    assert receipt["provenance"]["groups"] == sorted(groups)
    assert len(receipt["provenance"]["payload_hash"]) == 64
    actual = TwinStore(destination.home / "capabilities/identity/twin.sqlite3")
    assert not actual.snapshot()["enabled"]
    assert actual.snapshot()["documents"] == twin.snapshot()["documents"]
    assert actual.snapshot()["documents"][0]["private"]
    assert actual.snapshot()["traits"] == {"patience": 8}
    assert actual.snapshot()["active_persona_id"] == "calm"
    assert actual.snapshot()["personas"] == twin.snapshot()["personas"]
    assert actual.compose()["text"] == ""
    restored = StoryStore(destination.home / "capabilities/identity/stories.sqlite3")
    assert restored.export() == stories.export()
    assert restored.get(child["id"])["parent_id"] == parent["id"]
    assert [row["text"] for row in restored.history(parent["id"])] == [
        "First answer",
        "Revised answer",
    ]
    assert apply(destination, bundle, groups, plan) == receipt
    duplicate = preview(destination, bundle, groups)
    assert duplicate["can_apply"]
    assert all(row["duplicates"] and not row["new"] for row in duplicate["groups"])
    restored.update(
        parent["id"],
        expected_revision=2,
        prompt="Origin?",
        theme="origin",
        text="Destination revision",
    )
    assert restored.get(parent["id"])["revision"] == 3
    assert stories.get(parent["id"])["revision"] == 2
    assert ExtendedBundleService(destination.home).inventory()["format"] == FORMAT


def test_conflict_preview_stale_token_and_request_mismatch_preserve_existing_sources(
    tmp_path,
):
    source, _, _, _, _ = seed(tmp_path / "source")
    destination = ExtendedBundleService(tmp_path / "destination")
    groups = ["human_twin"]
    bundle = export(source, groups)
    plan = preview(destination, bundle, groups)
    twin = TwinStore(destination.home / "capabilities/identity/twin.sqlite3")
    twin.save_document(
        title="Destination", text="Do not overwrite", expected_revision=0
    )
    with pytest.raises(ConflictError, match="changed"):
        apply(destination, bundle, groups, plan)
    blocked = preview(destination, bundle, groups)
    assert not blocked["can_apply"]
    assert "empty" in blocked["groups"][0]["conflicts"][0]
    with pytest.raises(ValueError, match="conflicts"):
        apply(destination, bundle, groups, blocked)
    assert twin.snapshot()["documents"][0]["text"] == "Do not overwrite"
    fresh = ExtendedBundleService(tmp_path / "fresh")
    plan = preview(fresh, bundle, groups)
    receipt = apply(fresh, bundle, groups, plan)
    with pytest.raises(ConflictError, match="another"):
        apply(fresh, bundle, groups, {**plan, "preview_token": "different"})
    assert receipt["status"] == "applied"
    assert (
        len(
            TwinStore(fresh.home / "capabilities/identity/twin.sqlite3").snapshot()[
                "documents"
            ]
        )
        == 1
    )


def test_agent_definition_whitelist_preserves_destination_credentials_and_authority(
    tmp_path,
):
    source = ExtendedBundleService(tmp_path / "source")
    source_config = {
        "agents": {
            "researcher": asdict(
                AgentProfile(
                    description="Research carefully",
                    system_prompt="Cite evidence",
                    voice="Calm",
                    natural_voice=True,
                    provider="acp:other",
                    provider_agent="private-account",
                    model="secret-provider:model",
                    default_dir="/private",
                    tools=["*"],
                    triggers=["auto"],
                    approval_mode="auto",
                )
            )
        },
        "provider": {"api_key": "SOURCE-SECRET"},
    }
    (source.home / "config.json").write_text(json.dumps(source_config))
    bundle = export(source, ["agent_definitions"])
    payload = json.loads(
        decrypt_archive(base64.b64decode(bundle["ciphertext"]), PASSPHRASE)
    )
    row = payload["groups"]["agent_definitions"][0]
    assert set(row) == {
        "name",
        "description",
        "system_prompt",
        "voice",
        "natural_voice",
        "specialty",
        "route_hints",
    }
    assert row["name"] == "researcher"
    assert row["natural_voice"]
    assert "SOURCE-SECRET" not in json.dumps(payload)
    assert "/private" not in json.dumps(payload)
    assert "secret-provider" not in json.dumps(payload)
    destination = ExtendedBundleService(tmp_path / "destination")
    destination_config = {
        "provider": {"api_key": "DESTINATION-SECRET"},
        "default_agent": "kept",
        "agents": {
            "kept": asdict(
                AgentProfile(description="Keep existing", approval_mode="auto")
            )
        },
        "opaque": {"preserve": True},
    }
    (destination.home / "config.json").write_text(json.dumps(destination_config))
    plan = preview(destination, bundle, ["agent_definitions"])
    assert plan["can_apply"]
    assert plan["groups"][0]["new"] == ["researcher"]
    receipt = apply(destination, bundle, ["agent_definitions"], plan)
    assert receipt["status"] == "applied"
    actual = json.loads((destination.home / "config.json").read_text())
    imported = AgentProfile(**actual["agents"]["researcher"])
    assert imported.description == "Research carefully"
    assert imported.system_prompt == "Cite evidence"
    assert imported.voice == "Calm"
    assert imported.approval_mode == "interactive"
    assert imported.provider == ""
    assert imported.model == ""
    assert imported.default_dir == ""
    assert imported.tools == []
    assert imported.triggers == []
    assert actual["provider"] == destination_config["provider"]
    assert actual["opaque"] == destination_config["opaque"]
    assert actual["default_agent"] == "kept"
    assert actual["agents"]["kept"] == destination_config["agents"]["kept"]
    assert preview(destination, bundle, ["agent_definitions"])["groups"][0][
        "duplicates"
    ] == ["researcher"]
    actual["agents"]["researcher"]["voice"] = "Destination voice"
    (destination.home / "config.json").write_text(json.dumps(actual))
    assert not preview(destination, bundle, ["agent_definitions"])["can_apply"]


def test_model_selectors_require_destination_existing_bindings_and_agents(tmp_path):
    source = ExtendedBundleService(tmp_path / "source")
    (source.home / "config.json").write_text(
        json.dumps({"agents": {"researcher": {"model": "local:existing-model"}}})
    )
    bundle = export(source, ["model_policy"])
    destination = ExtendedBundleService(tmp_path / "destination")
    first = preview(destination, bundle, ["model_policy"])
    assert not first["can_apply"]
    assert any(
        "agent definition first" in item for item in first["groups"][0]["conflicts"]
    )
    (destination.home / "config.json").write_text(
        json.dumps({"agents": {"researcher": asdict(AgentProfile())}})
    )
    blocked = preview(destination, bundle, ["model_policy"])
    assert not blocked["can_apply"]
    assert any("already bound" in item for item in blocked["groups"][0]["conflicts"])
    (destination.home / "active_models.json").write_text(
        json.dumps({"loops": ["local:existing-model"]})
    )
    plan = preview(destination, bundle, ["model_policy"])
    assert plan["can_apply"]
    assert apply(destination, bundle, ["model_policy"], plan)["status"] == "applied"
    cfg = json.loads((destination.home / "config.json").read_text())
    assert cfg["agents"]["researcher"]["model"] == "local:existing-model"
    assert cfg["agents"]["researcher"]["provider"] == ""
    assert json.loads((destination.home / "active_models.json").read_text()) == {
        "loops": ["local:existing-model"]
    }
    assert preview(destination, bundle, ["model_policy"])["groups"][0][
        "duplicates"
    ] == ["researcher"]
    assert source.inventory()["groups"][-2]["id"] == "model_policy"


def test_actual_avatar_binary_clip_provenance_rebind_without_activity_selection(
    tmp_path,
):
    source = ExtendedBundleService(tmp_path / "source")
    original = AvatarStore(ExperienceStore(source.home))
    record = original.bundled()
    assert record["availability"] == "ready"
    original.select(
        {"revision": 1, "avatar_id": record["id"], "entity_id": "loop:source-private"}
    )
    bundle = export(source, ["avatars"])
    destination = ExtendedBundleService(tmp_path / "destination")
    plan = preview(destination, bundle, ["avatars"])
    assert plan["can_apply"]
    assert plan["groups"][0]["new"] == ["Gideon robot"]
    receipt = apply(destination, bundle, ["avatars"], plan)
    assert receipt["status"] == "applied"
    avatars = AvatarStore(ExperienceStore(destination.home))
    assert len(avatars.list()) == 1
    imported = avatars.list()[0]
    assert imported["source_hash"] == record["source_hash"]
    assert imported["clips"] == record["clips"]
    assert imported["id"] != record["id"]
    assert imported["availability"] == "ready"
    raw = avatars.raw(imported["artifact_slug"], imported["artifact_version"])
    assert raw == original.raw(record["artifact_slug"], record["artifact_version"])
    assert avatar_info(raw)["animations"] == record["available_clips"]
    assert avatars.selection() == {"revision": 1, "avatar_id": None, "entity_id": ""}
    assert "loop:source-private" not in json.dumps(receipt)
    duplicate = preview(destination, bundle, ["avatars"])
    assert duplicate["groups"][0]["duplicates"] == ["Gideon robot"]
    assert (
        apply(destination, bundle, ["avatars"], duplicate, "duplicate")["applied"][0][
            "count"
        ]
        == 0
    )
    assert len(avatars.models()) == 1


def test_version_one_slot_export_and_import_still_use_existing_envelope(tmp_path):
    source, _, _, _, _ = seed(tmp_path / "source")
    bundle = export(source, ["persona"])
    assert bundle["format"] == V1
    destination = ExtendedBundleService(tmp_path / "destination")
    plan = preview(destination, bundle, ["persona"])
    assert plan["groups"][0]["new"] == ["Careful observer"]
    receipt = apply(destination, bundle, ["persona"], plan)
    assert receipt["status"] == "applied"
    assert (
        destination.continuity.status()["slots"]["persona"][0]["text"]
        == "Careful observer"
    )
    assert apply(destination, bundle, ["persona"], plan) == receipt
    assert preview(destination, bundle, ["persona"])["groups"][0]["duplicates"] == [
        "Careful observer"
    ]


def test_group_partial_failure_and_durable_receipt_do_not_invent_cross_store_atomicity(
    tmp_path,
):
    source, _, _, _, _ = seed(tmp_path / "source")
    destination = ExtendedBundleService(tmp_path / "destination")
    groups = ["human_twin", "autobiography"]
    bundle = export(source, groups)
    plan = preview(destination, bundle, groups)
    twin = TwinStore(destination.home / "capabilities/identity/twin.sqlite3")
    with sqlite3.connect(twin.path) as db:
        db.execute(
            "CREATE TRIGGER deny_import BEFORE UPDATE ON twin BEGIN SELECT RAISE(FAIL,'blocked by real database'); END"
        )
    receipt = apply(destination, bundle, groups, plan)
    assert receipt["status"] == "partial"
    assert receipt["errors"][0]["slot"] == "human_twin"
    assert "blocked by real database" in receipt["errors"][0]["error"]
    assert twin.snapshot()["revision"] == 0
    assert (
        len(
            StoryStore(
                destination.home / "capabilities/identity/stories.sqlite3"
            ).list()
        )
        == 2
    )
    assert apply(destination, bundle, groups, plan) == receipt
    assert (
        ExtendedBundleService(destination.home).apply_bundle(
            bundle=bundle,
            groups=groups,
            passphrase=PASSPHRASE,
            preview_token=plan["preview_token"],
            request_id="apply",
        )
        == receipt
    )
    with sqlite3.connect(twin.path) as db:
        db.execute("DROP TRIGGER deny_import")
    resumed_plan = preview(destination, bundle, groups)
    assert resumed_plan["can_apply"]
    resumed = apply(destination, bundle, groups, resumed_plan, "explicit-resume")
    assert resumed["status"] == "applied"
    assert resumed["errors"] == []
    assert (
        len(
            StoryStore(
                destination.home / "capabilities/identity/stories.sqlite3"
            ).export()["history"]
        )
        == 3
    )
    assert twin.snapshot()["revision"] == 1
    assert not twin.snapshot()["enabled"]


def test_strict_snapshot_validation_preserves_live_revision_invariants(tmp_path):
    source, _, stories, parent, _ = seed(tmp_path)
    inventory = BundleInventory(tmp_path)
    data = stories.export()
    assert inventory.validate("autobiography", data) == data
    old = copy.deepcopy(data)
    old["stories"][0] = next(
        row
        for row in old["history"]
        if row["id"] == parent["id"] and row["revision"] == 1
    )
    with pytest.raises(ValueError, match="latest"):
        inventory.validate("autobiography", old)
    broken = copy.deepcopy(data)
    broken["history"] = [
        row
        for row in broken["history"]
        if not (row["id"] == parent["id"] and row["revision"] == 1)
    ]
    with pytest.raises(ValueError, match="contiguous"):
        inventory.validate("autobiography", broken)
    for invalid in (None, "invalid", {}, {"id": "missing"}):
        broken = copy.deepcopy(data)
        broken["stories"] = [invalid]
        with pytest.raises(ValueError):
            inventory.validate("autobiography", broken)
    broken = copy.deepcopy(data)
    broken["history"].append(broken["history"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        inventory.validate("autobiography", broken)
    assert stories.export() == data


@pytest.mark.parametrize(
    "group,data",
    [
        ("unknown", []),
        ("human_twin", {}),
        ("human_twin", []),
        ("autobiography", {}),
        ("agent_definitions", [{"name": "../escape"}]),
        ("agent_definitions", [{"name": "safe", "provider": "external"}]),
        (
            "model_policy",
            [{"name": "safe", "model": "local:model", "api_key": "secret"}],
        ),
        ("model_policy", [{"name": "../escape", "model": "local:model"}]),
        ("avatars", [{"title": "Bad", "data": "not-binary"}]),
    ],
)
def test_invalid_portable_group_rejected_without_destination_changes(
    tmp_path, group, data
):
    inventory = BundleInventory(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        inventory.validate(group, data)
    assert not (tmp_path / "config.json").exists()
    assert not (tmp_path / "capabilities/identity/twin.sqlite3").exists()
    assert not (tmp_path / "capabilities/experience.sqlite3").exists()


def test_authenticated_invalid_payload_and_wrong_passphrase_refuse_before_writes(
    tmp_path,
):
    destination = ExtendedBundleService(tmp_path)
    bad = envelope(
        {
            "model_policy": [
                {"name": "valid", "model": "local:bound", "provider": "injected"}
            ]
        }
    )
    with pytest.raises(ValueError, match="agent record"):
        preview(destination, bad, ["model_policy"])
    valid = envelope({"model_policy": []})
    with pytest.raises(ValueError, match="authentication"):
        destination.preview(
            bundle=valid, groups=["model_policy"], passphrase="wrong-passphrase-long"
        )
    with pytest.raises(ValueError, match="envelope"):
        preview(
            destination,
            {**valid, "ciphertext": "A" * (MAX_ENCODED + 1)},
            ["model_policy"],
        )
    assert not (tmp_path / "config.json").exists()
    assert destination.continuity.policy()["revision"] == 0


@pytest.mark.asyncio
async def test_actual_http_extended_records_import_and_override_refusal(tmp_path):
    source, _, stories, _, _ = seed(tmp_path / "source")
    source_app, destination_app = web.Application(), web.Application()
    register(source_app, home=source.home)
    register(destination_app, home=tmp_path / "destination")
    async with (
        TestClient(TestServer(source_app)) as first,
        TestClient(TestServer(destination_app)) as second,
    ):
        response = await first.get(PREFIX)
        assert response.status == 200
        assert len((await response.json())["groups"]) == 7
        response = await first.post(
            PREFIX + "/export",
            json={"groups": ["autobiography"], "passphrase": PASSPHRASE},
        )
        assert response.status == 200
        bundle = await response.json()
        assert bundle["format"] == FORMAT
        body = {"bundle": bundle, "groups": ["autobiography"], "passphrase": PASSPHRASE}
        response = await second.post(PREFIX + "/preview", json=body)
        assert response.status == 200
        plan = await response.json()
        assert plan["can_apply"]
        response = await second.post(
            PREFIX + "/apply",
            json={**body, "preview_token": plan["preview_token"], "request_id": "http"},
        )
        assert response.status == 200
        assert (await response.json())["status"] == "applied"
        assert response.headers["Cache-Control"] == "no-store"
        assert (
            StoryStore(
                tmp_path / "destination/capabilities/identity/stories.sqlite3"
            ).export()
            == stories.export()
        )
        for field in ("home", "provider", "approval_mode"):
            response = await second.post(
                PREFIX + "/export",
                json={
                    "groups": ["autobiography"],
                    "passphrase": PASSPHRASE,
                    field: "override",
                },
            )
            assert response.status == 400
        response = await second.post(
            PREFIX + "/preview",
            json={**body, "passphrase": "incorrect-long-passphrase"},
        )
        assert response.status == 400
        assert "authentication" in (await response.json())["error"]
