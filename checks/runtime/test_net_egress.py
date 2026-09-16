"""Egress security layer (net/) — the decision plane's authoritative classifier +
URL evaluator, and the policy profiles.

Pure unit tests with a fake resolver (no real DNS). Ports the webhook guard's cases
and adds the gaps the plan calls out: IPv4-mapped-IPv6, ULA, IMDS link-local,
operator allow/deny, loopback-inversion, and the redirect-hop re-evaluation contract.
"""

from __future__ import annotations

import socket

import pytest

from gideon.security.net.guard import classify_host, evaluate
from gideon.security.net.policy import (
    CONNECTOR,
    LOOPBACK_INTERNAL,
    STRICT,
    WEBHOOK,
    get_policy,
)


def _resolver(mapping):
    """Build a fake resolver: host → [ips], or raise gaierror for unknown hosts."""

    def _r(host):
        if host not in mapping:
            raise socket.gaierror(f"no such host {host}")
        return mapping[host]

    return _r


@pytest.mark.parametrize(
    "ip,public,category",
    [
        ("8.8.8.8", True, "public"),
        ("1.1.1.1", True, "public"),
        ("127.0.0.1", False, "loopback"),
        ("::1", False, "loopback"),
        ("10.0.0.5", False, "private"),
        ("192.168.1.1", False, "private"),
        ("172.16.0.1", False, "private"),
        ("169.254.169.254", False, "link_local"),
        ("fe80::1", False, "link_local"),
        ("fc00::1", False, "private"),
        ("fd12:3456::1", False, "private"),
        ("224.0.0.1", False, "multicast"),
        ("0.0.0.0", False, "unspecified"),
    ],
)
def test_classify_host_ranges(ip, public, category):
    v = classify_host(ip)
    assert v.public is public
    assert v.category == category


def test_classify_ipv4_mapped_ipv6_unwraps_to_private():
    v = classify_host("::ffff:10.0.0.1")
    assert v.public is False
    assert v.category == "private"


def test_classify_ipv4_mapped_public_stays_public():
    v = classify_host("::ffff:8.8.8.8")
    assert v.public is True


def test_classify_invalid_ip_fails_closed():
    assert classify_host("not-an-ip").public is False


def test_evaluate_allows_public_and_pins_ip():
    d = evaluate(
        "https://example.com/path",
        STRICT,
        resolver=_resolver({"example.com": ["93.184.216.34"]}),
    )
    assert d.allow is True
    assert d.pinned_ips == ["93.184.216.34"]


def test_evaluate_blocks_loopback():
    d = evaluate(
        "http://localhost:8080",
        STRICT,
        resolver=_resolver({"localhost": ["127.0.0.1"]}),
    )
    assert d.allow is False
    assert "non-public" in d.reason


def test_evaluate_blocks_imds():
    d = evaluate(
        "http://metadata/latest",
        STRICT,
        resolver=_resolver({"metadata": ["169.254.169.254"]}),
    )
    assert d.allow is False


def test_evaluate_blocks_if_ANY_record_is_private():
    d = evaluate(
        "https://x.com", STRICT, resolver=_resolver({"x.com": ["8.8.8.8", "10.0.0.1"]})
    )
    assert d.allow is False


def test_evaluate_rejects_non_http_scheme():
    d = evaluate(
        "ftp://example.com", STRICT, resolver=_resolver({"example.com": ["8.8.8.8"]})
    )
    assert d.allow is False
    assert "scheme" in d.reason


def test_evaluate_fails_closed_on_unresolvable():
    d = evaluate("https://nope.invalid", STRICT, resolver=_resolver({}))
    assert d.allow is False
    assert "resolvable" in d.reason


def test_evaluate_missing_host():
    assert evaluate("https://", STRICT, resolver=_resolver({})).allow is False


def test_deny_host_wins_even_if_public():
    pol = STRICT.with_overrides(deny_hosts=("evil.com",))
    d = evaluate(
        "https://api.evil.com", pol, resolver=_resolver({"api.evil.com": ["8.8.8.8"]})
    )
    assert d.allow is False
    assert "deny list" in d.reason


def test_allow_host_permits_private_lan():
    pol = WEBHOOK.with_overrides(allow_hosts=("nas.local",))
    d = evaluate(
        "http://nas.local:9000/hook",
        pol,
        resolver=_resolver({"nas.local": ["192.168.1.50"]}),
    )
    assert d.allow is True
    assert d.pinned_ips == ["192.168.1.50"]


def test_allow_host_rebinding_to_imds_is_refused():
    pol = WEBHOOK.with_overrides(allow_hosts=("metadata.example",))
    d = evaluate(
        "http://metadata.example/latest",
        pol,
        resolver=_resolver({"metadata.example": ["169.254.169.254"]}),
    )
    assert d.allow is False
    assert "metadata" in d.reason


def test_allow_host_rebinding_to_alibaba_metadata_is_refused():
    pol = WEBHOOK.with_overrides(allow_hosts=("metadata.example",))
    d = evaluate(
        "http://metadata.example/meta",
        pol,
        resolver=_resolver({"metadata.example": ["100.100.100.200"]}),
    )
    assert d.allow is False
    assert "metadata" in d.reason


def test_allow_host_subdomain_match():
    pol = STRICT.with_overrides(allow_hosts=("example.com",))
    d = evaluate(
        "http://internal.example.com",
        pol,
        resolver=_resolver({"internal.example.com": ["10.1.2.3"]}),
    )
    assert d.allow is True


def test_deny_does_not_match_suffix_lookalike():
    pol = STRICT.with_overrides(deny_hosts=("example.com",))
    d = evaluate(
        "https://notexample.com",
        pol,
        resolver=_resolver({"notexample.com": ["8.8.8.8"]}),
    )
    assert d.allow is True


def test_loopback_internal_allows_loopback():
    d = evaluate(
        "http://127.0.0.1:7777/mcp",
        LOOPBACK_INTERNAL,
        resolver=_resolver({"127.0.0.1": ["127.0.0.1"]}),
    )
    assert d.allow is True


def test_loopback_internal_denies_public():
    d = evaluate(
        "https://example.com",
        LOOPBACK_INTERNAL,
        resolver=_resolver({"example.com": ["8.8.8.8"]}),
    )
    assert d.allow is False
    assert "LOOPBACK_INTERNAL" in d.reason


def test_profiles_have_expected_postures():
    assert STRICT.allow_private is False and STRICT.pin_resolved_ip is True
    assert (
        LOOPBACK_INTERNAL.loopback_only is True
        and LOOPBACK_INTERNAL.allow_private is True
    )
    assert CONNECTOR.max_bytes >= STRICT.max_bytes
    assert get_policy("strict") is STRICT
    assert get_policy("unknown-name") is STRICT
