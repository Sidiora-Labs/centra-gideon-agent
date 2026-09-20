"""Knowledge web_url connector routes fetch + detect_changes through the egress guard (N2).

Previously it fetched arbitrary user/agent-supplied bookmark URLs with raw httpx and NO
SSRF check — a bookmark of http://169.254.169.254/ or an internal host was fetched
unguarded. Now both go through net.fetch(policy=CONNECTOR).
"""

import asyncio
import socket

from gideon.cognition.knowledge.connectors.web_url import WebUrlConnector


def _run(coro):
    return asyncio.run(coro)


def _fake_dns(mapping):
    def _gai(host, *a, **k):
        ips = mapping.get(host)
        if ips is None:
            raise socket.gaierror(f"unknown host {host}")
        return [(socket.AF_INET, None, None, "", (ip, 0)) for ip in ips]

    return _gai


def test_web_url_fetch_blocks_private(monkeypatch):
    """A bookmark resolving to a private/LAN IP is blocked (returns error_kind=blocked)."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_dns({"intranet.local": ["10.0.0.5"]})
    )
    text, meta = _run(WebUrlConnector().fetch({"uri": "http://intranet.local/page"}))
    assert text == ""
    assert meta.get("error_kind") == "blocked"
    assert "security guard" in meta.get("error", "").lower()


def test_web_url_fetch_blocks_imds(monkeypatch):
    """A bookmark of the AWS IMDS address is blocked."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_dns({"metadata.internal": ["169.254.169.254"]})
    )
    text, meta = _run(
        WebUrlConnector().fetch({"uri": "http://metadata.internal/latest/meta-data/"})
    )
    assert text == ""
    assert meta.get("error_kind") == "blocked"


def test_web_url_detect_changes_blocks_private(monkeypatch):
    """detect_changes (the scheduled HEAD refresh) is guarded too — a private host
    returns False (no change / not probed), never an unguarded HEAD."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_dns({"nas.local": ["192.168.1.9"]})
    )
    changed = _run(WebUrlConnector().detect_changes({"uri": "http://nas.local/feed"}))
    assert changed is False


def test_web_url_fetch_public_attempts(monkeypatch):
    """A public host passes the guard and the fetch is attempted (stubbed transport)."""
    import gideon.security.net.client as client

    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_dns({"example.com": ["93.184.216.34"]})
    )

    async def fake_fetch(url, **kw):
        return client.FetchResponse(
            url=url,
            status=200,
            headers={"Content-Type": "text/html"},
            body=b"<html><head><title>Hi</title></head><body><p>Hello world content</p></body></html>",  # noqa: E501
        )

    import gideon.security.net as net

    monkeypatch.setattr(net, "fetch", fake_fetch)
    text, meta = _run(WebUrlConnector().fetch({"uri": "https://example.com/"}))
    assert meta.get("error") is None
    assert meta.get("url") == "https://example.com/"
    assert meta.get("content_hash")


def test_web_url_follows_at_most_three_meta_refresh_hops_through_policy(monkeypatch):
    import gideon.security.net as net
    import gideon.security.net.client as client

    calls = []

    async def fake_fetch(url, **kw):
        calls.append((url, kw.get("policy")))
        step = len(calls)
        body = (
            f'<meta http-equiv="refresh" content="0; url=/hop-{step}">'
            if step <= 4
            else "done"
        )
        return client.FetchResponse(
            url=url,
            status=200,
            headers={"Content-Type": "text/html"},
            body=body.encode(),
        )

    monkeypatch.setattr(net, "fetch", fake_fetch)
    _text, meta = _run(WebUrlConnector().fetch({"uri": "https://example.com/start"}))
    assert [url for url, _policy in calls] == [
        "https://example.com/start",
        "https://example.com/hop-1",
        "https://example.com/hop-2",
        "https://example.com/hop-3",
    ]
    assert all(policy is calls[0][1] and policy is not None for _url, policy in calls)
    assert meta["url"] == "https://example.com/hop-3"
