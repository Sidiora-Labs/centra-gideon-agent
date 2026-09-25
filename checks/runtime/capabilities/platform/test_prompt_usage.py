import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import config_dir
from gideon.extensions.apps import prompt_registry
from gideon.integrations.prompt_providers.catalog import BUNDLED_PROMPTS
from gideon.integrations.prompt_providers.native_provider import NativePromptProvider
from gideon.integrations.prompt_providers.registry import register_prompt_provider
from gideon.interfaces.dashboard.handlers import prompts
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.workspace.capabilities.platform.prompt_usage import prompt_usage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_SKIP_PROMPT_SEED", "1")
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    register_prompt_provider(NativePromptProvider())
    prompt_registry.clear()
    yield
    prompt_registry.clear()


def application():
    app = web.Application(middlewares=[token_auth_middleware()])
    app.router.add_post("/api/prompts", prompts.api_prompt_create)
    app.router.add_put("/api/prompts/bindings", prompts.api_prompt_bindings_save)
    app.router.add_get("/api/prompts/{name}", prompts.api_prompt_detail)
    app.router.add_delete("/api/prompts/{name}", prompts.api_prompt_delete)
    app.router.add_post("/api/prompt-snippets", prompts.api_snippet_create)
    app.router.add_delete("/api/prompt-snippets/{name}", prompts.api_snippet_delete)
    return app


async def create(client, name="review-instructions", content="Preserve the evidence."):
    response = await client.post(
        "/api/prompts",
        params={"token": generate_token("prompt-owner")},
        json={"name": name, "content": content, "kind": "user"},
    )
    assert response.status == 200
    assert (await response.json())["prompt"]["content"] == content


@pytest.mark.asyncio
async def test_active_binding_protects_then_clear_allows_real_delete():
    async with TestClient(TestServer(application())) as client:
        await create(client)
        path = "/api/prompts/review-instructions"
        initial = await (await client.get(path)).json()
        assert initial["usage"]["deletable"] is True
        assert initial["usage"]["consumers"] == []
        bound = await client.put(
            "/api/prompts/bindings",
            json={"use_case": "chat", "ref": "native:review-instructions"},
        )
        assert bound.status == 200
        actual = json.loads((config_dir() / "active_prompts.json").read_text())
        assert actual["chat"] == "native:review-instructions"
        detail = await (await client.get(path)).json()
        assert detail["usage"] == {
            "version": 1,
            "provider": "native",
            "name": "review-instructions",
            "consumers": [{"kind": "binding", "id": "chat", "label": "Chat"}],
            "total": 1,
            "complete": True,
            "deletable": False,
        }
        refused = await client.delete(path)
        assert refused.status == 409
        failure = await refused.json()
        assert failure["code"] == "prompt_in_use"
        assert failure["usage"] == detail["usage"]
        assert (
            NativePromptProvider().get_prompt("review-instructions").content
            == "Preserve the evidence."
        )
        clear = await client.put(
            "/api/prompts/bindings", json={"use_case": "chat", "ref": ""}
        )
        assert clear.status == 200
        assert prompt_usage("native", "review-instructions")["deletable"] is True
        deleted = await client.delete(path)
        assert deleted.status == 200
        assert await deleted.json() == {"ok": True}
        assert NativePromptProvider().get_prompt("review-instructions") is None
        assert (await client.get(path)).status == 404


@pytest.mark.asyncio
async def test_native_fallback_declaration_survives_binding_override():
    entry = BUNDLED_PROMPTS[0]
    async with TestClient(TestServer(application())) as client:
        await create(client, entry.name)
        await create(client, "replacement")
        response = await client.put(
            "/api/prompts/bindings",
            json={"use_case": entry.use_case, "ref": "native:replacement"},
        )
        assert response.status == 200
        usage = prompt_usage("native", entry.name)
        assert usage["complete"] is True
        assert usage["deletable"] is False
        assert any(
            row["kind"] == "native" and row["id"] == entry.use_case
            for row in usage["consumers"]
        )
        blocked = await client.delete(f"/api/prompts/{entry.name}")
        assert blocked.status == 409
        assert NativePromptProvider().get_prompt(entry.name) is not None


@pytest.mark.asyncio
async def test_live_app_declaration_is_not_a_second_reference_store():
    async with TestClient(TestServer(application())) as client:
        await create(client, "app-review")
        prompt_registry.register_use_case(
            "review_context",
            provider="native",
            prompt_name="app-review",
            app="review-app",
        )
        usage = prompt_usage("native", "app-review")
        assert usage["consumers"] == [
            {
                "kind": "app",
                "id": "review_context",
                "label": "review-app: Review context",
            }
        ]
        assert (await client.delete("/api/prompts/app-review")).status == 409
        prompt_registry.unregister_app("review-app")
        assert prompt_usage("native", "app-review")["deletable"] is True
        assert (await client.delete("/api/prompts/app-review")).status == 200
        assert NativePromptProvider().get_prompt("app-review") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw", [b"{", b"[]", b'{"chat":42}', b"\xff", b" " * 1_048_577]
)
async def test_unreadable_truth_never_means_unreferenced(raw):
    async with TestClient(TestServer(application())) as client:
        await create(client)
        path = config_dir() / "active_prompts.json"
        path.write_bytes(raw)
        detail = await (await client.get("/api/prompts/review-instructions")).json()
        assert detail["usage"]["complete"] is False
        assert detail["usage"]["deletable"] is False
        response = await client.delete("/api/prompts/review-instructions")
        assert response.status == 503
        failure = await response.json()
        assert failure["code"] == "prompt_usage_unavailable"
        assert str(config_dir()) not in json.dumps(failure)
        assert NativePromptProvider().get_prompt("review-instructions") is not None
        path.write_text("{}")
        repaired = await client.delete("/api/prompts/review-instructions")
        assert repaired.status == 200


def test_provider_identity_and_unknown_use_cases_are_not_false_consumers():
    path = config_dir() / "active_prompts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"chat": "remote:shared", "not_a_runtime_context": "native:shared"})
    )
    prompt_registry.register_use_case(
        "remote_context", provider="remote", prompt_name="shared", app="remote-app"
    )
    native = prompt_usage("native", "shared")
    remote = prompt_usage("remote", "shared")
    assert native["complete"] is True
    assert native["total"] == 0
    assert native["deletable"] is True
    assert remote["total"] == 2
    assert {row["kind"] for row in remote["consumers"]} == {"binding", "app"}
    assert remote["deletable"] is False


def test_directory_instead_of_binding_file_fails_closed():
    path = config_dir() / "active_prompts.json"
    path.mkdir(parents=True)
    usage = prompt_usage("native", "custom")
    assert usage["complete"] is False
    assert usage["deletable"] is False
    assert usage["consumers"] == []


@pytest.mark.asyncio
async def test_snippet_reference_protection_is_preserved():
    async with TestClient(TestServer(application())) as client:
        response = await client.post(
            "/api/prompt-snippets",
            params={"token": generate_token("snippet-owner")},
            json={"name": "shared-note", "content": "Keep source citations."},
        )
        assert response.status == 200
        await create(client, "with-snippet", "Review carefully. {{> shared-note}}")
        denied = await client.delete("/api/prompt-snippets/shared-note")
        assert denied.status == 409
        assert "with-snippet" in json.dumps(await denied.json())
        assert (await client.delete("/api/prompts/with-snippet")).status == 200
        assert (await client.delete("/api/prompt-snippets/shared-note")).status == 200


@pytest.mark.asyncio
async def test_unauthenticated_delete_cannot_remove_prompt():
    async with TestClient(TestServer(application())) as owner:
        await create(owner, "private-prompt")
    async with TestClient(TestServer(application())) as stranger:
        denied = await stranger.delete("/api/prompts/private-prompt")
        assert denied.status == 403
        assert NativePromptProvider().get_prompt("private-prompt") is not None
