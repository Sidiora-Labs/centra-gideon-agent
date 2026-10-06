from __future__ import annotations

import json

import pytest

from gideon.core.config.edit_spec import security_control, security_loosening
from gideon.extensions.apps.permissions import (
    ROUTE_AUTHORITY,
    AppMay,
    OwnedTarget,
    OwnerOnly,
    _owner_only_reason,
    app_request_denial,
    route_authority,
)


@pytest.mark.parametrize(
    ("method", "route", "path"),
    [
        ("GET", "/api/config/gideon", "/api/config/gideon"),
        ("GET", "/api/onboarding", "/api/onboarding"),
        ("POST", "/api/onboarding/state", "/api/onboarding/state"),
        ("GET", "/api/models/active", "/api/models/active"),
        ("PUT", "/api/model-providers/{name}", "/api/model-providers/provider"),
        ("GET", "/api/agents", "/api/agents"),
        ("GET", "/api/skills", "/api/skills"),
        ("PUT", "/api/mcp/servers/{name}", "/api/mcp/servers/server"),
        ("GET", "/api/security/credentials", "/api/security/credentials"),
        ("POST", "/api/apps/{name}/token", "/api/apps/probe/token"),
    ],
)
def test_owner_security_routes_override_manifest_api_prefixes(method, route, path):
    reason = _owner_only_reason(path, method, route)
    if route == "/api/apps/{name}/token":
        assert isinstance(route_authority(method, route), OwnerOnly)
    else:
        assert reason


def test_route_catalog_is_method_and_template_specific_and_fails_closed():
    assert (
        route_authority("HEAD", "/api/providers/{name}/config")
        == ROUTE_AUTHORITY["GET /api/providers/{name}/config"]
    )
    assert isinstance(route_authority("PATCH", "/api/providers/{name}/config"), AppMay)
    assert route_authority("DELETE", "/api/providers/{name}/config") is None
    assert route_authority("GET", "") is None
    assert route_authority("GET", "/api/apps/{name}/brand-new") is None
    assert isinstance(route_authority("PATCH", "/api/config/gideon"), AppMay)
    assert _owner_only_reason("/api/config/gideon", "PATCH", "/api/config/gideon") == ""
    assert _owner_only_reason("/api/config/gideon", "GET", "/api/config/gideon")


def test_provider_routes_bind_the_target_to_the_calling_app():
    policy = route_authority("PATCH", "/api/providers/{name}/config")
    assert isinstance(policy, AppMay)
    assert policy.owns == OwnedTarget("name")
    assert route_authority("POST", "/api/providers/{name}/enable") is None
    assert (
        _owner_only_reason(
            "/api/providers/model-app/instances",
            "GET",
            "/api/providers/{name}/instances",
        )
        == ""
    )


@pytest.mark.parametrize(
    ("field", "current", "new", "loosens"),
    [
        ("agent.yolo", False, True, True),
        ("agent.yolo", True, False, False),
        ("agent.approval_timeout_minutes", 60, 120, True),
        ("agent.approval_timeout_minutes", 120, 60, False),
        ("agent.subagent_timeout_secs", 0, 2000, True),
        ("agent.subagent_timeout_secs", 0, 1800, False),
        ("agent.subagent_timeout_secs", 1800, 0, False),
        ("agent.subagent_timeout_secs", 2000, 0, False),
        ("agent.max_subagents", 0, 4, True),
        ("agent.max_subagents", 4, 0, True),
        ("agent.spawn_min_memory_gb", 0, 4, False),
        ("agent.spawn_min_memory_gb", 4, 0, True),
        ("guardrails.budgets.max_tokens_per_day", 0, 1000, False),
        ("guardrails.budgets.max_tokens_per_day", 1000, 0, True),
        ("security.outside_home", [], ["agent-skills"], True),
        ("security.denied_commands", ["^rm"], [], True),
        ("guardrails.budgets.max_tokens_per_day", 1000, 500, False),
    ],
)
def test_security_controls_classify_owner_changes(field, current, new, loosens):
    assert security_control(field) is not None
    assert bool(security_loosening(field, current, new)) is loosens


def test_security_fields_are_not_grantable_to_app_config():
    from gideon.extensions.apps.manifest import AppManifest

    manifest = AppManifest.from_dict(
        {
            "name": "security-writer",
            "version": "1.0.0",
            "displayName": "Security writer",
            "description": "Security writer",
            "permissions": {"config": ["agent.yolo", "voice.echo_filter_enabled"]},
        }
    )
    assert any("owner-only security state" in error for error in manifest.validate())


def test_real_app_identity_is_limited_by_method_route_and_owned_target(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    app_dir = tmp_path / "apps" / "broad-app"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "name": "broad-app",
                "version": "1.0.0",
                "displayName": "Broad app",
                "description": "Security authority fixture",
                "permissions": {"api": ["/api/*"]},
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "installed.json").write_text(
        json.dumps({"name": "broad-app", "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )

    assert "owner-only capability" in app_request_denial(
        "broad-app",
        "/api/config/gideon",
        method="GET",
        route="/api/config/gideon",
    )
    assert "owned by the calling app" in app_request_denial(
        "broad-app",
        "/api/providers/other/config",
        method="GET",
        route="/api/providers/{name}/config",
        target="other",
    )
    assert "not declared for app access" in app_request_denial(
        "broad-app",
        "/api/apps/other/unknown",
        method="GET",
        route="/api/apps/{name}/unknown",
        target="other",
    )
    assert app_request_denial("broad-app", "/api/config/gideon") == (
        "app route authority is unavailable"
    )


@pytest.mark.parametrize(
    ("section", "field", "initial", "granted"),
    [
        ("agent", "yolo", False, True),
        ("security", "outside_home", [], ["agent-skills"]),
    ],
)
@pytest.mark.asyncio
async def test_owner_security_loosening_requires_wire_confirmation(
    tmp_path, monkeypatch, section, field, initial, granted
):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers.core import api_gideon_config_patch

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({section: {field: initial}}), encoding="utf-8")
    original = config_path.read_bytes()
    config_field = f"{section}.{field}"
    app = web.Application()
    app.router.add_patch("/api/config/gideon", api_gideon_config_patch)

    async with TestClient(TestServer(app)) as client:
        refused = await client.patch(
            "/api/config/gideon", json={"path": config_field, "value": granted}
        )
        refused_body = await refused.json()
        assert refused.status == 400
        assert refused_body["error"] == "confirmation_required"
        assert refused_body["field"] == config_field
        assert config_path.read_bytes() == original
        assert refused_body["consent"]

        confirmed = await client.patch(
            "/api/config/gideon",
            json={"path": config_field, "value": granted, "confirm": True},
        )
        assert confirmed.status == 200
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved[section][field] == granted

        revoked = await client.patch(
            "/api/config/gideon", json={"path": config_field, "value": initial}
        )
        assert revoked.status == 200

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved[section][field] == initial
