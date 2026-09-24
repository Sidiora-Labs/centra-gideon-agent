"""Instance identity and subtractive registration through production providers."""

import asyncio

import pytest

from gideon.extensions.apps.manifest import AppManifest, ProviderConfig
from gideon.extensions.providers.registry import ModelTypeHandler, RegisteredProvider
from gideon.integrations.local_models import registry
from gideon.integrations.local_models.multi_instance import (
    MultiInstanceLocalProvider,
    instance_identity,
)
from gideon.integrations.local_models.provider import LocalModel


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(registry, "_providers", {})
    monkeypatch.setattr(registry, "_capabilities", {})
    monkeypatch.setattr(registry, "_self_test_locks", {})


def extension(*, multi=True):
    return RegisteredProvider(
        name="model-app",
        manifest=AppManifest(name="model-app"),
        provider_config=ProviderConfig(
            type="model", multiInstance=multi, capabilities=["chat"]
        ),
    )


def test_one_app_key_and_subtractive_deregistration():
    handler = ModelTypeHandler()
    ext = extension()
    first = MultiInstanceLocalProvider("first", [])
    second = MultiInstanceLocalProvider("second", [])
    handler.register(ext, [first, second])
    aggregate = registry.get_provider(ext.name)
    assert registry.registered() == [(ext.name, aggregate)]
    assert isinstance(aggregate, MultiInstanceLocalProvider)
    assert aggregate.providers == [first, second]
    handler.deregister(ext, first)
    assert registry.get_provider(ext.name) is aggregate
    assert aggregate.providers == [second]
    handler.deregister(ext, first)
    assert aggregate.providers == [second]
    handler.deregister(ext, [second])
    assert registry.registered() == []
    assert registry.capabilities_for(ext.name) == []


def test_instance_replacement_survives_stale_deregistration():
    handler = ModelTypeHandler()
    ext = extension()
    original = MultiInstanceLocalProvider("same", [])
    replacement = MultiInstanceLocalProvider("same", [])
    sibling = MultiInstanceLocalProvider("sibling", [])
    handler.register(ext, [original, sibling])
    handler.register(ext, [replacement])
    aggregate = registry.get_provider(ext.name)
    assert aggregate.providers == [sibling, replacement]
    handler.deregister(ext, original)
    assert aggregate.providers == [sibling, replacement]


def test_singleton_deregistration_does_not_remove_replacement():
    handler = ModelTypeHandler()
    ext = extension(multi=False)
    original = MultiInstanceLocalProvider("same", [])
    replacement = MultiInstanceLocalProvider("same", [])
    handler.register(ext, original)
    handler.register(ext, replacement)
    handler.deregister(ext, original)
    assert registry.get_provider(ext.name) is replacement
    handler.deregister(ext, replacement)
    assert registry.get_provider(ext.name) is None


def test_identity_serialization_preserves_member_identity_and_input():
    provider = MultiInstanceLocalProvider("app", [])
    provider.instance_id = "east"
    provider.instance_label = "East host"
    model = LocalModel(name="model")
    converted = registry.to_local_model(model, capabilities=["chat"], provider=provider)
    wire = converted.to_dict()
    assert wire["instance_id"] == "east"
    assert wire["instance_label"] == "East host"
    assert wire["capabilities"] == ["chat"]
    assert model.instance_id == ""
    assert model.capabilities == []
    aggregate = MultiInstanceLocalProvider("app", [provider])
    assert registry.to_local_model(converted, provider=aggregate).to_dict() == wire
    assert instance_identity(MultiInstanceLocalProvider("singleton", [])) == {
        "instance_id": "singleton",
        "instance_label": "singleton",
    }


def test_empty_aggregate_cannot_claim_availability_or_writes():
    async def check():
        provider = MultiInstanceLocalProvider("empty", [])
        assert not await provider.is_available()
        assert await provider.list_models() == []
        assert await provider.search_models("model") == []
        assert not await provider.download_model("model")
        assert not await provider.delete_model("model")

    asyncio.run(check())


def test_native_ollama_instances_union_fanout_and_identity(tmp_path, monkeypatch):
    from dataclasses import replace

    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from gideon.extensions.providers.instances import create_instance, update_instance
    from gideon.extensions.providers.loader import BUNDLED_DIR
    from gideon.integrations.embedding_providers import registry as embedding_registry

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(embedding_registry, "_providers", {})
    manifest = AppManifest.from_json_file(BUNDLED_DIR / "ollama-models" / "app.json")
    ext = RegisteredProvider(
        name=manifest.name,
        manifest=manifest,
        provider_config=replace(manifest.provider, multiInstance=True),
    )
    handler = ModelTypeHandler()

    async def check():
        operations = []
        catalogs = {
            "east": ["shared", "east-model"],
            "west": ["shared", "west-model"],
        }
        unavailable = set()
        failed_writes = set()

        async def tags(request):
            instance = request.match_info["instance"]
            if instance in unavailable:
                raise web.HTTPServiceUnavailable()
            return web.json_response(
                {
                    "models": [
                        {"name": name, "size": 1048576} for name in catalogs[instance]
                    ]
                }
            )

        async def pull(request):
            instance = request.match_info["instance"]
            body = await request.json()
            operations.append((instance, "pull", body.get("name") or body.get("model")))
            if instance in failed_writes:
                raise web.HTTPServiceUnavailable()
            return web.Response(
                text='{"status":"success"}\n', content_type="application/x-ndjson"
            )

        async def delete(request):
            instance = request.match_info["instance"]
            body = await request.json()
            operations.append(
                (instance, "delete", body.get("name") or body.get("model"))
            )
            if instance in failed_writes:
                raise web.HTTPServiceUnavailable()
            return web.json_response({})

        app = web.Application()
        app.router.add_get("/{instance}/api/tags", tags)
        app.router.add_post("/{instance}/api/pull", pull)
        app.router.add_delete("/{instance}/api/delete", delete)
        async with TestServer(app) as server:
            for instance in ("east", "west", "disabled"):
                create_instance(
                    ext.name,
                    instance.title(),
                    {"endpoint": str(server.make_url(f"/{instance}"))},
                    instance_id=instance,
                )
            update_instance(ext.name, "disabled", enabled=False)
            providers = handler.create(ext)
            assert [provider.instance_id for provider in providers] == ["east", "west"]
            assert [provider.instance_label for provider in providers] == [
                "East",
                "West",
            ]
            handler.register(ext, providers)
            aggregate = registry.get_provider(ext.name)
            try:
                assert await aggregate.is_available()
                unavailable.add("east")
                assert await aggregate.is_available()
                unavailable.add("west")
                assert not await aggregate.is_available()
                unavailable.clear()
                catalog = await registry.catalog_for(aggregate)
                assert [model.name for model in catalog] == [
                    "shared",
                    "east-model",
                    "west-model",
                ]
                assert [
                    (model.instance_id, model.instance_label) for model in catalog
                ] == [("east", "East"), ("east", "East"), ("west", "West")]
                assert all(model.to_dict()["instance_id"] for model in catalog)
                assert await aggregate.download_model("new-model")
                assert await aggregate.delete_model("shared")
                assert sorted(operations) == sorted(
                    [
                        ("east", "pull", "new-model"),
                        ("west", "pull", "new-model"),
                        ("east", "delete", "shared"),
                        ("west", "delete", "shared"),
                    ]
                )
                operations.clear()
                failed_writes.add("east")
                assert not await aggregate.download_model("partial")
                assert not await aggregate.delete_model("partial")
                assert len(operations) == 4
                assert {instance for instance, _, _ in operations} == {"east", "west"}
                handler.deregister(ext, providers[0])
                assert [
                    model.name for model in await registry.catalog_for(aggregate)
                ] == ["shared", "west-model"]
            finally:
                handler.deregister(ext, providers)

    asyncio.run(check())
