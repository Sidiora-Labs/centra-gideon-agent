import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import config_dir, config_path
from gideon.integrations.llm.branded_specs import BrandedProviderSpec
from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.registry import (
    ProviderRegistry,
    ProviderResolutionError,
    get_default_registry,
    set_default_registry,
    sync_entries_from_config,
)
from gideon.interfaces.dashboard.handlers.capabilities_connections import register
from gideon.interfaces.dashboard.handlers.capabilities_platform import (
    register as register_catalog,
)
from gideon.interfaces.dashboard.handlers.providers import api_provider_models
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.sdk.provider_helpers import register_branded_app
from gideon.workspace.capabilities.platform.connections import (
    ConnectionError,
    document,
    effective_entry,
    projection,
)


def register_types():
    register_branded_app(
        BrandedProviderSpec(
            type="openai-connection", protocol="openai", default_model="alpha"
        )
    )
    register_branded_app(
        BrandedProviderSpec(
            type="declared-catalog",
            protocol="anthropic",
            fallback_models=({"id": "alpha"}, {"id": "beta"}),
        )
    )


@pytest.fixture(autouse=True)
def isolated_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    original = get_default_registry()
    set_default_registry(ProviderRegistry())
    CredentialStore(config_dir()).save(
        {
            "first": {"type": "api_key", "value": "test-first-private-value"},
            "second": {"type": "api_key", "value": "test-second-private-value"},
        }
    )
    config_path().write_text(
        json.dumps(
            {
                "unrelated": {"keep": 7},
                "providers": [
                    {
                        "name": "primary",
                        "type": "openai-connection",
                        "model": "alpha",
                        "options": {"base_url": "https://original.invalid/v1"},
                    },
                    {"name": "catalog", "type": "declared-catalog", "model": "alpha"},
                ],
            }
        )
    )
    register_types()
    sync_entries_from_config()
    yield
    set_default_registry(original)


def application():
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    register_catalog(app)
    app.router.add_get("/api/model-providers/{name}/models", api_provider_models)
    return app


PREFIX = "/api/capabilities/platform/connections"


def body(revision=0, **changes):
    return {
        "revision": revision,
        "label": "Shared account",
        "base_url": "https://shared.invalid/v1",
        "credential_ref": "first",
        "model_access": {"mode": "all", "patterns": []},
        **changes,
    }


async def authenticate(client):
    response = await client.get(
        PREFIX, params={"token": generate_token("connection-owner")}
    )
    assert response.status == 200
    return await response.json()


@pytest.mark.asyncio
async def test_shared_connection_real_provider_construction_rotation_and_restart():
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        created = await client.put(PREFIX + "/shared", json=body())
        assert created.status == 200
        assert (await created.json())["connections"][0]["revision"] == 1
        for revision, name in [(1, "primary"), (2, "catalog")]:
            response = await client.put(
                PREFIX + f"/shared/bindings/{name}",
                json={"revision": revision, "bound": True},
            )
            assert response.status == 200
        registry = get_default_registry()
        primary = registry.get_entry("primary")
        catalog = registry.get_entry("catalog")
        assert (
            primary.options["base_url"]
            == catalog.options["base_url"]
            == "https://shared.invalid/v1"
        )
        assert primary.credential == catalog.credential == "first"
        actual = registry.build(
            "primary", credential_store=CredentialStore(config_dir())
        )
        assert str(actual._client.base_url) == "https://shared.invalid/v1/"
        assert actual._client.api_key == "test-first-private-value"
        assert "connection_id" not in actual._extra_options
        assert "model_access" not in actual._extra_options
        await actual._client.close()
        updated = await client.put(
            PREFIX + "/shared",
            json=body(
                3, base_url="https://rotated.invalid/v1", credential_ref="second"
            ),
        )
        assert updated.status == 200
        second = registry.build(
            "primary", credential_store=CredentialStore(config_dir())
        )
        assert str(second._client.base_url) == "https://rotated.invalid/v1/"
        assert second._client.api_key == "test-second-private-value"
        await second._client.close()
        assert registry.get_entry("catalog").credential == "second"
        set_default_registry(ProviderRegistry())
        register_types()
        assert sync_entries_from_config() == 2
        reopened = get_default_registry().get_entry("primary")
        assert reopened.credential == "second"
        assert reopened.options["base_url"] == "https://rotated.invalid/v1"
        assert document()["unrelated"] == {"keep": 7}
        public = await (await client.get(PREFIX)).json()
        assert public["connections"][0]["bindings"] == ["primary", "catalog"]
        assert "test-first-private-value" not in json.dumps(public)
        assert "test-second-private-value" not in json.dumps(public)


@pytest.mark.asyncio
async def test_entitlement_catalog_scope_and_real_execution_guard():
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        assert (
            await client.put(
                PREFIX + "/shared",
                json=body(model_access={"mode": "allow", "patterns": ["alpha"]}),
            )
        ).status == 200
        for revision, name in [(1, "primary"), (2, "catalog")]:
            assert (
                await client.put(
                    PREFIX + f"/shared/bindings/{name}",
                    json={"revision": revision, "bound": True},
                )
            ).status == 200
        response = await client.get("/api/model-providers/catalog/models")
        assert response.status == 200
        result = await response.json()
        assert [model["id"] for model in result["models"]] == ["alpha"]
        assert {model["id"] for model in result["model_catalog"]} == {"alpha", "beta"}
        with pytest.raises(ProviderResolutionError, match="excluded"):
            get_default_registry().build(
                "primary", model="beta", credential_store=CredentialStore(config_dir())
            )
        assert (
            await client.put(
                PREFIX + "/shared",
                json=body(3, model_access={"mode": "deny", "patterns": ["alpha"]}),
            )
        ).status == 200
        with pytest.raises(ProviderResolutionError, match="excluded"):
            get_default_registry().build(
                "primary", credential_store=CredentialStore(config_dir())
            )
        denied = await (await client.get("/api/model-providers/catalog/models")).json()
        assert [model["id"] for model in denied["models"]] == ["beta"]
        assert len(denied["model_catalog"]) == 2
        assert (await client.put(PREFIX + "/shared", json=body(4))).status == 200
        cleared = await (await client.get("/api/model-providers/catalog/models")).json()
        assert len(cleared["models"]) == 2
        assert len(cleared["model_catalog"]) == 2
        assert document()["providers"][0]["model"] == "alpha"


@pytest.mark.asyncio
async def test_conflicts_unbind_and_delete_preserve_original_provider():
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        assert (await client.put(PREFIX + "/shared", json=body())).status == 200
        assert (await client.put(PREFIX + "/shared", json=body())).status == 409
        assert (
            await client.put(
                PREFIX + "/shared/bindings/primary", json={"revision": 1, "bound": True}
            )
        ).status == 200
        assert (await client.delete(PREFIX + "/shared?revision=2")).status == 409
        assert (
            await client.put(
                PREFIX + "/shared/bindings/primary",
                json={"revision": 1, "bound": False},
            )
        ).status == 409
        assert (
            await client.put(
                PREFIX + "/shared/bindings/primary",
                json={"revision": 2, "bound": False},
            )
        ).status == 200
        restored = get_default_registry().get_entry("primary")
        assert restored.options["base_url"] == "https://original.invalid/v1"
        assert restored.credential is None
        assert (await client.delete(PREFIX + "/shared?revision=3")).status == 200
        assert projection()["connections"] == []
        assert len(document()["providers"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "https://user:secret@example.invalid/v1"},
        {"base_url": "https://example.invalid/v1?key=secret"},
        {"base_url": "file:///etc/passwd"},
        {"credential_ref": "missing"},
        {"model_access": {"mode": "maybe"}},
        {"model_access": {"mode": "allow", "patterns": [""]}},
        {"revision": True},
        {"label": ""},
    ],
)
async def test_invalid_configuration_never_changes_persisted_document(changes):
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        before = config_path().read_bytes()
        response = await client.put(PREFIX + "/shared", json=body(**changes))
        assert response.status in {400, 409}
        assert "error" in await response.json()
        assert config_path().read_bytes() == before


@pytest.mark.asyncio
async def test_corrupt_configuration_and_missing_binding_fail_closed():
    async with TestClient(TestServer(application())) as client:
        await authenticate(client)
        assert (await client.put(PREFIX + "/shared", json=body())).status == 200
        assert (
            await client.put(
                PREFIX + "/shared/bindings/primary", json={"revision": 1, "bound": True}
            )
        ).status == 200
        persisted = document()
        persisted["provider_connections"] = {}
        config_path().write_text(json.dumps(persisted))
        with pytest.raises(ConnectionError, match="missing"):
            get_default_registry().get_entry("primary")
        config_path().write_text("{")
        refused = await client.put(PREFIX + "/shared", json=body())
        assert refused.status == 503
        assert config_path().read_text() == "{"
        assert str(config_dir()) not in await refused.text()


@pytest.mark.asyncio
async def test_authentication_protects_connection_control():
    async with TestClient(TestServer(application())) as client:
        assert (await client.get(PREFIX)).status == 403
        assert (await client.put(PREFIX + "/shared", json=body())).status == 403
        app_token = generate_token("owner", app="some-app")
        assert (await client.get(PREFIX, params={"token": app_token})).status == 403
        assert projection()["connections"] == []


@pytest.mark.asyncio
async def test_native_manifest_loads_and_real_tools_invoke_contracts():
    from gideon.extensions.apps.manifest import AppManifest
    from gideon.extensions.providers.loader import load_factory
    from gideon.extensions.providers.registry import RegisteredProvider
    from gideon.integrations.tool_providers.registry import (
        get_provider,
        register_provider,
        unregister_provider,
    )

    manifest = AppManifest.from_json_file(
        Path("runtime/gideon/extensions/apps/native/gideon-platform/app.json")
    )
    record = RegisteredProvider(
        name=manifest.name, manifest=manifest, provider_config=manifest.provider
    )
    tool_provider = load_factory(record)()
    register_provider(tool_provider)
    app = application()
    try:
        provider = get_provider("gideon-platform")
        definitions = await provider.list_tools()
        assert {tool.name for tool in definitions} == {
            "platform_api_catalog",
            "prompt_dependency_usage",
            "provider_connections_get",
        }
        assert all(not tool.requires_approval for tool in definitions)
        assert all(tool.risk_level.value == "safe" for tool in definitions)
        catalog = await provider.invoke("platform_api_catalog", {"limit": 2})
        assert catalog.success is True
        assert len(json.loads(catalog.output)["routes"]) == 2
        assert app.router.routes()
        usage = await provider.invoke(
            "prompt_dependency_usage", {"name": "system-chat"}
        )
        assert usage.success is True
        assert json.loads(usage.output)["deletable"] is False
        inventory = await provider.invoke("provider_connections_get", {})
        assert inventory.success is True
        assert json.loads(inventory.output)["providers"][0]["name"] == "primary"
        assert "test-first-private-value" not in inventory.output
        invalid = await provider.invoke("platform_api_catalog", {"limit": 999})
        assert invalid.success is False
        assert "Invalid" in invalid.error
        assert (await provider.invoke("missing", {})).success is False
    finally:
        unregister_provider("gideon-platform")
