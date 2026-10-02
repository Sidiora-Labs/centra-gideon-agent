from __future__ import annotations

import json

import pytest

from gideon.hypermid.network_policy import (
    Destination,
    EgressDenied,
    NetworkGrant,
    NetworkPolicy,
    ProxyPolicy,
    ProxyRoute,
    REDACTED,
    SecretDenied,
    SecretVault,
)


def grant(**changes: object) -> NetworkGrant:
    values: dict[str, object] = {
        "grant_id": "network-1",
        "principal_id": "principal-1",
        "operation": "model.invoke",
        "scheme": "https",
        "hostname": "api.example.com",
        "ports": frozenset({443}),
        "address_classes": frozenset({"public"}),
        "proxy_policy": ProxyPolicy.direct_only(),
        "redirect_limit": 1,
        "byte_limit": 1024,
        "expires_at_ms": 200,
    }
    values.update(changes)
    return NetworkGrant(**values)  # type: ignore[arg-type]


def authorize(policy: NetworkPolicy, network_grant: NetworkGrant | None, **changes: object):
    values: dict[str, object] = {
        "principal_id": "principal-1",
        "operation": "model.invoke",
        "destination": Destination.normalized("HTTPS", "API.EXAMPLE.COM.", 443),
        "resolved_addresses": ("93.184.216.34",),
        "redirect_hops": 0,
        "now_ms": 100,
    }
    values.update(changes)
    return policy.authorize_attempt(network_grant, **values)  # type: ignore[arg-type]


def test_default_deny_and_exact_scope_destination_and_expiry() -> None:
    policy = NetworkPolicy()
    cases = [
        (None, {}, "grant.missing"),
        (grant(), {"principal_id": "principal-2"}, "grant.scope"),
        (grant(), {"operation": "tool.invoke"}, "grant.scope"),
        (
            grant(),
            {"destination": Destination.normalized("https", "other.example.com", 443)},
            "destination.not_granted",
        ),
        (grant(), {"now_ms": 200}, "grant.expired"),
    ]
    for network_grant, changes, expected in cases:
        with pytest.raises(EgressDenied) as denied:
            authorize(policy, network_grant, **changes)
        assert denied.value.code == "HYPERMID_EGRESS_DENIED"
        assert denied.value.rule == expected
        assert "api.example.com" not in str(denied.value)


def test_controlled_dns_rebinding_metadata_redirect_and_byte_limits_fail_before_connect() -> None:
    policy = NetworkPolicy()
    connections: list[str] = []

    def connect_after_admission(addresses: tuple[str, ...], **changes: object) -> None:
        authorize(policy, grant(), resolved_addresses=addresses, **changes)
        connections.append(addresses[0])

    for addresses, changes, rule in [
        (("93.184.216.34", "10.0.0.1"), {}, "address.class"),
        (("169.254.169.254",), {}, "address.metadata"),
        (("127.0.0.1",), {}, "address.class"),
        (("93.184.216.34",), {"redirect_hops": 2}, "redirect.limit"),
    ]:
        with pytest.raises(EgressDenied) as denied:
            connect_after_admission(addresses, **changes)
        assert denied.value.rule == rule
    assert connections == []
    admitted = authorize(policy, grant(), redirect_hops=1)
    assert admitted.redirects_remaining == 0
    with pytest.raises(EgressDenied, match="response.byte_limit"):
        policy.enforce_response_limit(admitted, 1025)


def test_required_proxy_refuses_direct_and_wrong_route_and_checks_proxy_dns() -> None:
    policy = NetworkPolicy()
    proxied = grant(
        proxy_policy=ProxyPolicy.required("https", "proxy.example.com", 8443)
    )
    with pytest.raises(EgressDenied, match="proxy.required"):
        authorize(policy, proxied)
    wrong = ProxyRoute("https", "other.example.com", 8443, ("93.184.216.34",))
    with pytest.raises(EgressDenied, match="proxy.bypass"):
        authorize(policy, proxied, proxy=wrong)
    rebound = ProxyRoute("https", "proxy.example.com", 8443, ("10.0.0.1",))
    with pytest.raises(EgressDenied, match="address.class"):
        authorize(policy, proxied, proxy=rebound)
    exact = ProxyRoute("https", "proxy.example.com", 8443, ("93.184.216.34",))
    assert authorize(policy, proxied, proxy=exact).grant_id == "network-1"


def test_secret_is_resolved_only_for_exact_dispatch_scope() -> None:
    canary = b"unique-canary-credential"
    vault = SecretVault()
    handle = vault.register("principal-1", "model.invoke", canary)
    assert canary.decode() not in repr(vault)
    assert canary.decode() not in repr(handle)
    with pytest.raises(SecretDenied, match="secret.scope"):
        vault.dispatch_with_secret(
            handle,
            principal_id="principal-2",
            operation="model.invoke",
            dispatch=lambda value: value,
        )
    length = vault.dispatch_with_secret(
        handle,
        principal_id="principal-1",
        operation="model.invoke",
        dispatch=len,
    )
    assert length == len(canary)


def test_registered_secret_and_common_credentials_are_absent_from_all_captured_paths() -> None:
    canary = b"unique-canary-credential"
    vault = SecretVault()
    vault.register("principal-1", "model.invoke", canary)
    redactor = vault.redactor()
    captured = {
        "prompt": f"prompt {canary.decode()}",
        "log": f"Bearer {canary.decode()}",
        "evidence": {"api_key": canary.decode()},
        "arguments": [f"--password={canary.decode()}"],
        "export": f"https://operator:{canary.decode()}@example.com/path",
        "ui": {"credential": canary.decode(), "present": True},
    }
    encoded = json.dumps(redactor.redact(captured), sort_keys=True)
    assert canary.decode() not in encoded
    assert "operator:" not in encoded
    assert encoded.count(REDACTED) >= 6


def test_network_grants_ui_source_has_no_secret_value_surface() -> None:
    source = open(
        "apps/console/src/features/hypermid/NetworkGrants.tsx", encoding="utf-8"
    ).read()
    assert "NetworkGrantView" in source
    assert "credentialValue" not in source
    assert "secretValue" not in source
    assert "No network access is granted" in source
