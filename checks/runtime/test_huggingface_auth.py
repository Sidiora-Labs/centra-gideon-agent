"""Hugging Face token resolution, validation, caching, and dashboard safety."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import Mock

import pytest
from aiohttp import streams, web
from aiohttp.test_utils import make_mocked_request

from gideon.core.config import credentials
from gideon.integrations.local_models import huggingface_auth as hf
from gideon.interfaces.dashboard.handlers import model_registry
from gideon.operations.durability.inventory import by_id
from gideon.security.net import FetchResponse

TOKEN = "hf_this_is_a_realistically_shaped_test_token_1234"
OTHER_TOKEN = "hf_another_realistically_shaped_test_token_9876"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    from gideon.core.config import loader

    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    monkeypatch.setattr(credentials, "_usable_keyring", lambda: None)
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(loader, "env_path", lambda: tmp_path / ".env")
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "hf-cache-token"))
    for key in hf.HF_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    hf.clear_validation_cache()
    yield tmp_path
    hf.clear_validation_cache()


def test_cascade_prefers_store_then_environment_then_hf_cache(isolated_home) -> None:
    cache_path = hf.huggingface_token_path()
    cache_path.write_text("hf_cache_value_0000\n", encoding="utf-8")
    os.environ["HUGGING_FACE_HUB_TOKEN"] = "hf_environment_value_1111"
    credentials._dotenv_save_credential(hf.HF_CREDENTIAL_KEY, TOKEN)

    resolved = hf.resolve_token()
    assert resolved.source == "credential_store"
    assert resolved.token == TOKEN

    credentials._dotenv_remove_credentials((hf.HF_CREDENTIAL_KEY,))
    resolved = hf.resolve_token()
    assert resolved.source == "environment"
    assert resolved.environment_key == "HUGGING_FACE_HUB_TOKEN"

    os.environ.pop("HUGGING_FACE_HUB_TOKEN")
    resolved = hf.resolve_token()
    assert resolved.source == "huggingface_cache"
    assert resolved.token == "hf_cache_value_0000"


def test_primary_environment_name_wins_alias(isolated_home, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", OTHER_TOKEN)
    resolved = hf.resolve_token()
    assert resolved.source == "environment"
    assert resolved.environment_key == "HF_TOKEN"
    assert resolved.token == TOKEN


def test_mask_and_repr_do_not_disclose_token() -> None:
    resolved = hf.ResolvedToken(TOKEN, "environment", "HF_TOKEN")
    masked = hf.mask_token(TOKEN)
    assert masked.endswith("1234")
    assert TOKEN not in masked
    assert TOKEN not in repr(resolved)


@pytest.mark.asyncio
async def test_whoami_uses_egress_chokepoint_and_ttl_cache(
    isolated_home, monkeypatch
) -> None:
    import gideon.security.net as net

    calls = []

    async def guarded_fetch(url, **kwargs):
        calls.append((url, kwargs))
        return FetchResponse(
            url=url,
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"name":"alice"}',
        )

    monkeypatch.setattr(net, "fetch", guarded_fetch)
    first = await hf.validate_token(TOKEN, ttl_secs=120)
    second = await hf.validate_token(TOKEN, ttl_secs=120)

    assert first.state == "valid" and first.username == "alice"
    assert second.state == "valid" and second.cached is True
    assert len(calls) == 1
    url, options = calls[0]
    assert url == hf.WHOAMI_URL
    assert options["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert options["policy"].allow_only is True
    assert options["policy"].allow_hosts == ("huggingface.co",)
    assert options["policy"].max_redirects == 0


@pytest.mark.asyncio
async def test_validation_cache_contains_no_token(isolated_home, monkeypatch) -> None:
    import gideon.security.net as net

    async def guarded_fetch(url, **kwargs):
        return FetchResponse(url=url, status=401, body=b'{"error":"bad token"}')

    monkeypatch.setattr(net, "fetch", guarded_fetch)
    result = await hf.validate_token(TOKEN, ttl_secs=120)
    on_disk = hf.validation_cache_path().read_text(encoding="utf-8")
    assert result.state == "invalid"
    assert TOKEN not in on_disk
    assert "Authorization" not in on_disk
    assert oct(hf.validation_cache_path().stat().st_mode)[-3:] == "600"


@pytest.mark.asyncio
async def test_transport_error_is_redacted(isolated_home, monkeypatch) -> None:
    import gideon.security.net as net

    async def guarded_fetch(url, **kwargs):
        raise RuntimeError(f"request failed with Authorization: Bearer {TOKEN}")

    monkeypatch.setattr(net, "fetch", guarded_fetch)
    result = await hf.validate_token(TOKEN, ttl_secs=0)
    assert result.state == "unavailable"
    assert TOKEN not in result.error
    assert "Bearer" not in result.error


def _json_request(method: str, path: str, body: object | None = None):
    """A real mocked request carrying actual serialized JSON, as the handler reads it."""
    raw = json.dumps(body).encode("utf-8")
    payload = streams.StreamReader(Mock(), 2**16, loop=asyncio.get_event_loop())
    payload.feed_data(raw)
    payload.feed_eof()
    return make_mocked_request(
        method,
        path,
        payload=payload,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )


@pytest.mark.asyncio
async def test_validated_dashboard_write_is_masked_stored_and_audited(
    isolated_home, monkeypatch
) -> None:
    import gideon.security.net as net

    async def guarded_fetch(url, **kwargs):
        return FetchResponse(url=url, status=200, body=b'{"name":"alice"}')

    audit = []
    monkeypatch.setattr(net, "fetch", guarded_fetch)
    monkeypatch.setattr(
        model_registry, "_sel_log", lambda *args, **kwargs: audit.append(args)
    )

    response = await model_registry.api_huggingface_auth_put(
        _json_request("PUT", "/api/models/huggingface/auth", {"token": TOKEN})
    )
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["state"] == "valid"
    assert payload["source"] == "credential_store"
    assert TOKEN not in response.text
    assert credentials.get_credential(hf.HF_CREDENTIAL_KEY) == TOKEN
    assert audit and audit[-1][0] == "models.huggingface_auth_set"
    assert TOKEN not in repr(audit)


@pytest.mark.asyncio
async def test_invalid_dashboard_write_does_not_store_token(
    isolated_home, monkeypatch
) -> None:
    import gideon.security.net as net

    async def guarded_fetch(url, **kwargs):
        return FetchResponse(url=url, status=401, body=b"denied")

    monkeypatch.setattr(net, "fetch", guarded_fetch)
    monkeypatch.setattr(model_registry, "_sel_log", lambda *args, **kwargs: None)
    response = await model_registry.api_huggingface_auth_put(
        _json_request("PUT", "/api/models/huggingface/auth", {"token": TOKEN})
    )
    assert response.status == 400
    assert TOKEN not in response.text
    assert credentials.get_credential(hf.HF_CREDENTIAL_KEY) == ""


@pytest.mark.asyncio
async def test_app_scoped_caller_cannot_read_status(monkeypatch) -> None:
    monkeypatch.setattr(model_registry, "_sel_log", lambda *args, **kwargs: None)
    request = make_mocked_request("GET", "/api/models/huggingface/auth")
    request["app"] = "example-app"
    response = await model_registry.api_huggingface_auth_status(request)
    assert response.status == 403


def test_ttl_configuration_is_bounded(isolated_home) -> None:
    from gideon.core.config.loader import AppConfig

    (isolated_home / "config.json").write_text(
        json.dumps({"local_models": {"hf_whoami_ttl_secs": 999999}}),
        encoding="utf-8",
    )
    assert AppConfig.load().local_models.hf_whoami_ttl_secs == 86400


def test_validation_cache_is_declared_derived_secret_state() -> None:
    entry = by_id("huggingface_auth_cache")
    assert entry is not None
    assert entry.path == hf.VALIDATION_CACHE_FILE
    assert entry.secret is True
    assert entry.derived is True


def test_routes_expose_status_write_delete_and_forced_test() -> None:
    app = web.Application()
    model_registry.register_model_registry_routes(app)
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/api/models/huggingface/auth") in routes
    assert ("PUT", "/api/models/huggingface/auth") in routes
    assert ("DELETE", "/api/models/huggingface/auth") in routes
    assert ("POST", "/api/models/huggingface/auth/test") in routes
