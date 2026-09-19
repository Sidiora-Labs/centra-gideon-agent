"""Property tests for dashboard token authentication."""

import string
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from gideon.interfaces.dashboard.token_auth import (
    LINK_WINDOW_SECS,
    MAX_CONCURRENT_NONCES,
    MAX_SESSION_TTL_SECS,
    auth_middleware,
    bind_token_ip,
    check_token_ip,
    generate_token,
    is_consumed,
    mark_consumed,
    parse_duration,
    revoke_all_sessions,
    token_auth_middleware,
    token_nonce,
    try_consume,
    validate_token,
)
from gideon.security.auth.modes import AuthConfig, AuthMode


@pytest.fixture(autouse=True)
def clear_nonces():
    """Clear nonces before each test to ensure isolation."""
    revoke_all_sessions()
    yield
    revoke_all_sessions()


URL_SAFE_B64_CHARS = set(string.ascii_letters + string.digits + "-_.")


@pytest.mark.parametrize("requested", ["api_key", "oauth2"])
def test_unauthorable_auth_mode_reports_closed_fallback(
    requested: str, monkeypatch
) -> None:
    monkeypatch.setenv("GIDEON_AUTH_MODE", requested)

    cfg = AuthConfig.from_env()

    assert cfg.requested_mode == requested
    assert cfg.mode is AuthMode.LOCAL_TOKEN
    assert cfg.actual_mode == "local_token"
    assert cfg.authorable is False
    assert cfg.fell_back_from_unauthorable_mode is True
    assert cfg.mode_state() == (
        f"requested={requested} actual=local_token authorable=false"
    )


@pytest.mark.parametrize(
    ("requested", "actual"),
    [("local_token", AuthMode.LOCAL_TOKEN), ("none", AuthMode.NONE)],
)
def test_authorable_auth_mode_reports_requested_and_actual(
    requested: str, actual: AuthMode, monkeypatch
) -> None:
    monkeypatch.setenv("GIDEON_AUTH_MODE", requested)

    cfg = AuthConfig.from_env()

    assert cfg.requested_mode == requested
    assert cfg.mode is actual
    assert cfg.authorable is True
    assert cfg.fell_back_from_unauthorable_mode is False


@pytest.mark.parametrize("requested", ["api_key", "oauth2"])
def test_startup_warns_with_requested_actual_and_authorable_state(
    requested: str, monkeypatch, caplog
) -> None:
    from gideon.interfaces.dashboard.server import _log_auth_mode

    monkeypatch.setenv("GIDEON_AUTH_MODE", requested)

    _log_auth_mode(AuthConfig.from_env())

    assert f"requested={requested} actual=local_token authorable=false" in caplog.text
    assert "Auth mode fallback" in caplog.text


@pytest.mark.parametrize("user_id", ["alice", "bob@corp", "user-123", "a", "x" * 200])
def test_generate_then_validate_roundtrip(user_id: str) -> None:
    token = generate_token(user_id, ttl_seconds=60)
    valid, returned_id, reason = validate_token(token)
    assert valid is True
    assert returned_id == user_id
    assert reason == ""


@pytest.mark.parametrize(
    "user_id", ["alice", "user/with/slashes", "emoji-☺", "a" * 300]
)
def test_token_url_safe_chars(user_id: str) -> None:
    token = generate_token(user_id)
    assert all(c in URL_SAFE_B64_CHARS for c in token)


@pytest.mark.parametrize("n", [0, 1, 5, 24, 100, 9999])
def test_parse_duration_hours(n: int) -> None:
    assert parse_duration(f"{n}h") == min(n * 3600, MAX_SESSION_TTL_SECS)


@pytest.mark.parametrize("n", [0, 1, 5, 30, 60, 9999])
def test_parse_duration_minutes(n: int) -> None:
    assert parse_duration(f"{n}m") == min(n * 60, MAX_SESSION_TTL_SECS)


@pytest.mark.parametrize(
    "s",
    [
        "",
        "h",
        "m",
        "10",
        "10s",
        "10d",
        "abc",
        "-1h",
        "1.5h",
        "1H",
        "1M",
        " 1h",
        "1h ",
        "10hm",
        "h1",
        "m1",
    ],
)
def test_parse_duration_invalid(s: str) -> None:
    assert parse_duration(s) is None


def test_ip_binding_accepts_same_ip() -> None:
    token = generate_token("user1")
    bind_token_ip(token, "10.0.0.1")
    assert check_token_ip(token, "10.0.0.1") is True


def test_ip_binding_rejects_different_ip() -> None:
    token = generate_token("user2")
    bind_token_ip(token, "10.0.0.1")
    assert check_token_ip(token, "192.168.1.1") is False


def test_unbound_token_accepts_any_ip() -> None:
    token = generate_token("user3")
    assert check_token_ip(token, "10.0.0.1") is True
    assert check_token_ip(token, "192.168.1.1") is True


def test_consumed_token_returns_true() -> None:
    token = generate_token("user4")
    assert is_consumed(token) is False
    mark_consumed(token)
    assert is_consumed(token) is True


def test_unconsumed_token_returns_false() -> None:
    token = generate_token("user5")
    assert is_consumed(token) is False


def test_try_consume_returns_true_once_then_false() -> None:
    """Verify try_consume atomicity: first call consumes, subsequent calls return False."""
    token = generate_token("user_try_consume")
    assert try_consume(token) is True, "first call should consume the token"
    assert try_consume(token) is False, "second call should report already consumed"
    assert is_consumed(token), "token should be marked consumed"


def test_expired_token_rejected() -> None:
    """Token link click window expires — URL no longer valid."""
    with patch("gideon.interfaces.dashboard.token_auth.time") as mock_time:
        mock_time.time.return_value = 1000.0
        token = generate_token("user6", ttl_seconds=3600)
    with patch("gideon.interfaces.dashboard.token_auth.time") as mock_time:
        mock_time.time.return_value = 1000.0 + LINK_WINDOW_SECS + 1
        valid, _, reason = validate_token(token)
    assert valid is False
    assert "expired" in reason


def test_session_exp_still_valid_after_link_window() -> None:
    """Cookie-based access uses session_exp, not the link window."""
    with patch("gideon.interfaces.dashboard.token_auth.time") as mock_time:
        mock_time.time.return_value = 1000.0
        token = generate_token("user6b", ttl_seconds=3600)
    with patch("gideon.interfaces.dashboard.token_auth.time") as mock_time:
        mock_time.time.return_value = 1000.0 + 301
        valid, uid, _ = validate_token(token, use_session_exp=True)
    assert valid is True
    assert uid == "user6b"


def test_tampered_token_rejected() -> None:
    token = generate_token("user7")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    valid, _, reason = validate_token(tampered)
    assert valid is False
    assert reason in ("invalid signature", "invalid encoding")


def test_malformed_token_rejected() -> None:
    valid, _, reason = validate_token("no-dot-here")
    assert valid is False
    assert reason == "malformed token"


async def _ok_handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def _make_request(
    path: str = "/",
    query: dict | None = None,
    cookies: dict | None = None,
    remote: str = "127.0.0.1",
    headers: dict | None = None,
    method: str = "GET",
) -> MagicMock:
    """Build a mock aiohttp request."""
    req = MagicMock(spec=web.Request)
    req.path = path
    req.query = query or {}
    req.cookies = cookies or {}
    req.remote = remote
    req.headers = headers or {}
    req.method = method
    return req


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["query", "cookie"])
async def test_middleware_accepts_valid_token(via: str) -> None:
    mw = token_auth_middleware()
    token = generate_token("testuser", ttl_seconds=300)

    if via == "cookie":
        bind_token_ip(token, "127.0.0.1")
        mark_consumed(token)
        req = _make_request(cookies={"gideon_token_10000": token})
    else:
        req = _make_request(query={"token": token})

    resp = await mw(req, _ok_handler)
    assert resp.status == 200
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_cookie_set_on_query_param_auth() -> None:
    mw = token_auth_middleware()
    token = generate_token("cookieuser", ttl_seconds=300)
    req = _make_request(query={"token": token}, remote="10.0.0.1")

    resp = await mw(req, _ok_handler)
    assert resp.status == 200

    cookie_header = resp.cookies.get("gideon_token_10000")
    assert cookie_header is not None
    assert cookie_header.value == token
    assert cookie_header["httponly"] is True or "httponly" in str(cookie_header).lower()
    assert cookie_header["samesite"] == "Lax"
    assert cookie_header["path"] == "/"


@pytest.mark.asyncio
async def test_cookie_not_reset_when_present() -> None:
    mw = token_auth_middleware()
    token = generate_token("existing", ttl_seconds=300)
    bind_token_ip(token, "127.0.0.1")
    mark_consumed(token)

    req = _make_request(cookies={"gideon_token_10000": token})
    resp = await mw(req, _ok_handler)
    assert resp.status == 200
    assert "gideon_token_10000" not in resp.cookies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/assets/style.css",
        "/fonts/dm-sans.woff2",
        "/gideon.svg",
    ],
)
async def test_static_assets_bypass_auth(path: str) -> None:
    mw = token_auth_middleware()
    req = _make_request(path=path)
    resp = await mw(req, _ok_handler)
    assert resp.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/logo.png",
        "/manifest.json",
        "/sw.js",
        "/icon-192.png",
        "/static/app.js",
    ],
)
async def test_retired_pwa_paths_require_auth(path: str) -> None:
    mw = token_auth_middleware()
    req = _make_request(path=path)
    resp = await mw(req, _ok_handler)
    assert resp.status in (302, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/manifest.webmanifest",
        "/sw.js",
        "/icons/icon.svg",
        "/icons/icon-192.png",
        "/icons/icon-512.png",
    ],
)
async def test_live_pwa_paths_require_auth(path: str) -> None:
    """The PWA shipped by MOBILE-COMPANION T3.1 stays BEHIND the session.

    These paths are live (not retired like the ones above), and none of them is in
    ``_BYPASS_PREFIXES``/``_BYPASS_EXACT`` — deliberately. The companion is
    installable only by the authenticated owner. The cost of that choice is that a
    manifest, which browsers fetch with credentials omitted by default, would 403;
    ``apps/console/index.html`` pays it by declaring the link with
    ``crossorigin="use-credentials"``, which is asserted in
    ``apps/console/src/app/manifest.test.ts``.

    Anyone tempted to add these to a bypass set to "fix installability" should note
    what would then be readable without a session.
    """
    mw = token_auth_middleware()
    req = _make_request(path=path)
    resp = await mw(req, _ok_handler)
    assert resp.status in (302, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/api/status", "/some/page"])
async def test_loopback_requires_token(path: str) -> None:
    mw = token_auth_middleware()
    req = _make_request(path=path)
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_internal_path_trusts_loopback() -> None:
    secret = "test-secret-123"
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}), internal_secret=secret
    )
    req = _make_request(path="/api/spawn", headers={"X-Internal-Secret": secret})
    resp = await mw(req, _ok_handler)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_internal_path_non_loopback_denied_in_local_only_mode() -> None:
    """Default local_only=True denies non-loopback even with valid secret."""
    secret = "test-secret-123"
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}), internal_secret=secret
    )
    req = _make_request(
        path="/api/spawn", remote="10.0.0.1", headers={"X-Internal-Secret": secret}
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_internal_path_non_loopback_cookie_auth_when_not_local_only() -> None:
    """When local_only=False, non-loopback with valid cookie is granted."""
    token = generate_token("testuser", ttl_seconds=300)
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}), internal_secret="s", local_only=False
    )
    req = _make_request(
        path="/api/spawn", remote="10.0.0.1", cookies={"gideon_token_10000": token}
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_internal_path_non_loopback_no_cookie_denied() -> None:
    """When local_only=False, non-loopback without cookie is denied."""
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}), internal_secret="s", local_only=False
    )
    req = _make_request(path="/api/spawn", remote="10.0.0.1")
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_internal_path_non_loopback_wrong_secret_denied() -> None:
    """When local_only=False, wrong X-Internal-Secret is denied even with cookie."""
    token = generate_token("testuser", ttl_seconds=300)
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}),
        internal_secret="real",
        local_only=False,
    )
    req = _make_request(
        path="/api/spawn",
        remote="10.0.0.1",
        headers={"X-Internal-Secret": "wrong"},
        cookies={"gideon_token_10000": token},
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_internal_path_non_loopback_valid_secret_and_cookie_granted() -> None:
    """Both valid secret and valid cookie on non-loopback → granted."""
    token = generate_token("testuser", ttl_seconds=300)
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}),
        internal_secret="real",
        local_only=False,
    )
    req = _make_request(
        path="/api/spawn",
        remote="10.0.0.1",
        headers={"X-Internal-Secret": "real"},
        cookies={"gideon_token_10000": token},
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_internal_path_non_loopback_valid_secret_no_cookie_denied() -> None:
    """Valid secret alone is not enough for non-loopback; cookie is still required."""
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}),
        internal_secret="real",
        local_only=False,
    )
    req = _make_request(
        path="/api/spawn",
        remote="10.0.0.1",
        headers={"X-Internal-Secret": "real"},
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_internal_path_rejects_wrong_secret() -> None:
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}), internal_secret="real-secret"
    )
    req = _make_request(
        path="/api/spawn", headers={"X-Internal-Secret": "wrong-secret"}
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_internal_path_matches_sub_paths() -> None:
    """GET /api/spawn/{id} should be granted via /api/spawn prefix."""
    secret = "test-secret-123"
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}), internal_secret=secret
    )
    req = _make_request(path="/api/spawn/abc123", headers={"X-Internal-Secret": secret})
    resp = await mw(req, _ok_handler)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_internal_path_does_not_match_sibling_prefix() -> None:
    """GET /api/spawnfoo must NOT be treated as internal via /api/spawn."""
    secret = "test-secret-123"
    mw = token_auth_middleware(
        internal_paths=frozenset({"/api/spawn"}), internal_secret=secret
    )
    req = _make_request(path="/api/spawnfoo", headers={"X-Internal-Secret": secret})
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_mixed_path_loopback_with_secret_granted() -> None:
    """MCP path: loopback + X-Internal-Secret → granted via fast-path."""
    secret = "test-secret-123"
    mw = token_auth_middleware(
        mixed_internal_paths=frozenset({"/api/spawn"}), internal_secret=secret
    )
    req = _make_request(path="/api/spawn", headers={"X-Internal-Secret": secret})
    resp = await mw(req, _ok_handler)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_mixed_path_non_loopback_with_valid_cookie_granted() -> None:
    """DCV/SSH-forwarded browser: non-loopback + valid cookie → granted (no false banner)."""
    mw = token_auth_middleware(mixed_internal_paths=frozenset({"/api/spawn"}))
    token = generate_token("dcvuser", ttl_seconds=300)
    bind_token_ip(token, "10.0.0.1")
    mark_consumed(token)
    req = _make_request(
        path="/api/spawn", remote="10.0.0.1", cookies={"gideon_token_10000": token}
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_mixed_path_non_loopback_without_cookie_denied() -> None:
    """Non-loopback + no cookie → still denied (security preserved)."""
    mw = token_auth_middleware(mixed_internal_paths=frozenset({"/api/spawn"}))
    req = _make_request(path="/api/spawn", remote="10.0.0.1")
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_strict_path_non_loopback_still_hard_denied() -> None:
    """Strict internal path: non-loopback → hard-denied even with valid cookie
    (invariant: machine-to-machine isolation preserved)."""
    mw = token_auth_middleware(internal_paths=frozenset({"/api/send-message"}))
    token = generate_token("attacker", ttl_seconds=300)
    bind_token_ip(token, "10.0.0.1")
    mark_consumed(token)
    req = _make_request(
        path="/api/send-message",
        remote="10.0.0.1",
        cookies={"gideon_token_10000": token},
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


def test_oldest_token_evicted_after_max_concurrent() -> None:
    """Token beyond MAX_CONCURRENT_NONCES evicts the oldest nonce."""
    tokens = [generate_token("user1") for _ in range(MAX_CONCURRENT_NONCES + 1)]
    valid_old, _, reason = validate_token(tokens[0])
    valid_new, _, _ = validate_token(tokens[-1])
    assert (
        not valid_old
    ), f"oldest token should be evicted after {MAX_CONCURRENT_NONCES + 1} generations"
    assert reason == "token superseded"
    assert valid_new, "most recently issued token should remain valid"
    valid_survivor, _, _ = validate_token(tokens[1])
    assert valid_survivor, "second-oldest token should survive when only one is evicted"


def test_concurrent_tokens_within_limit_all_valid() -> None:
    """Up to MAX_CONCURRENT_NONCES tokens should all remain valid."""
    tokens = [generate_token(f"user{i}") for i in range(MAX_CONCURRENT_NONCES)]
    for i, token in enumerate(tokens):
        valid, uid, _ = validate_token(token)
        assert valid, f"token {i} should be valid within concurrent limit"
        assert uid == f"user{i}"


def test_token_rejected_when_no_nonces_registered() -> None:
    """Verify deny-by-default: tokens rejected when no nonces are registered."""
    token = generate_token("user1")
    revoke_all_sessions()
    valid, _, reason = validate_token(token)
    assert not valid
    assert reason == "no active sessions"


def test_evict_expired_removes_old_entries() -> None:
    """Verify evict_expired removes expired IP bindings, consumed tokens, and nonces."""
    from gideon.interfaces.dashboard.token_auth import _state

    token = generate_token("evict_user")
    bind_token_ip(token, "10.0.0.1", session_exp=1000.0)
    mark_consumed(token, session_exp=1000.0)

    with _state._lock:
        _state._nonces["expired_nonce"] = 1000.0

    _state.evict_expired(2000.0)

    with _state._lock:
        assert token not in _state._ip_bindings, "expired IP binding should be evicted"
        assert token not in _state._consumed, "expired consumed token should be evicted"
        assert "expired_nonce" not in _state._nonces, "expired nonce should be evicted"


def test_token_reusable_across_multiple_validations() -> None:
    token = generate_token("user1")
    for _ in range(5):
        valid, _, _ = validate_token(token)
        assert valid, "same token should be reusable across browsers/tabs/apps"


def test_active_nonce_survives_eviction_via_refresh() -> None:
    """Validating a token refreshes its nonce position, preventing eviction."""
    old_token = generate_token("old_user")
    for i in range(MAX_CONCURRENT_NONCES - 1):
        generate_token(f"filler{i}")
    valid, _, _ = validate_token(old_token, use_session_exp=True)
    assert valid, "old token should still be valid before overflow"
    generate_token("overflow")
    valid_after, _, reason = validate_token(old_token, use_session_exp=True)
    assert valid_after, f"actively-used token should survive eviction, got: {reason}"


@pytest.mark.asyncio
async def test_api_path_gets_json_403() -> None:
    mw = token_auth_middleware()
    req = _make_request(path="/api/status", remote="10.0.0.1")
    resp = await mw(req, _ok_handler)
    assert resp.status == 403
    assert resp.content_type == "application/json"


@pytest.mark.asyncio
async def test_non_api_path_gets_html_403() -> None:
    mw = token_auth_middleware()
    req = _make_request(path="/dashboard", remote="10.0.0.1")
    resp = await mw(req, _ok_handler)
    assert resp.status == 403
    assert resp.content_type == "text/html"


@pytest.mark.asyncio
async def test_query_param_token_reusable_across_requests() -> None:
    """Same token can be used from multiple browsers/tabs/apps."""
    mw = token_auth_middleware()
    token = generate_token("reuse_user", ttl_seconds=300)

    req1 = _make_request(query={"token": token}, remote="10.0.0.1")
    resp1 = await mw(req1, _ok_handler)
    assert resp1.status == 200

    req2 = _make_request(query={"token": token}, remote="10.0.0.1")
    resp2 = await mw(req2, _ok_handler)
    assert resp2.status == 200


@pytest.mark.asyncio
async def test_non_local_requires_auth() -> None:
    """Non-loopback clients require auth."""
    mw = token_auth_middleware()
    req = _make_request(path="/", remote="10.0.0.1")
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_non_local_accepts_valid_token() -> None:
    """Non-loopback clients with valid tokens are granted access."""
    mw = token_auth_middleware()
    token = generate_token("remote_user", ttl_seconds=300)
    req = _make_request(query={"token": token}, remote="10.0.0.1")
    resp = await mw(req, _ok_handler)
    assert resp.status == 200
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_api_logout_success_from_loopback() -> None:
    """POST /api/logout succeeds from loopback with valid secret."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers import api_logout

    app = web.Application()
    app["local_secret"] = "test-secret-123"
    app.router.add_post("/api/logout", api_logout)

    generate_token("user1")

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/logout",
            json={},
            headers={"X-Local-Secret": "test-secret-123"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True


@pytest.mark.asyncio
async def test_api_logout_rejects_non_loopback() -> None:
    """POST /api/logout rejects requests from non-loopback IPs."""
    from unittest.mock import patch

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers import api_logout

    app = web.Application()
    app["local_secret"] = "test-secret-123"
    app.router.add_post("/api/logout", api_logout)

    async with TestClient(TestServer(app)) as client:
        with patch(
            "gideon.interfaces.dashboard.handlers.is_loopback", return_value=False
        ):
            resp = await client.post(
                "/api/logout",
                json={},
                headers={"X-Local-Secret": "test-secret-123"},
            )
            assert resp.status == 403
            data = await resp.json()
            assert data["error"] == "loopback only"


@pytest.mark.asyncio
async def test_api_logout_rejects_invalid_secret() -> None:
    """POST /api/logout rejects requests with invalid secret."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers import api_logout

    app = web.Application()
    app["local_secret"] = "correct-secret"
    app.router.add_post("/api/logout", api_logout)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/logout",
            json={},
            headers={"X-Local-Secret": "wrong-secret"},
        )
        assert resp.status == 403
        data = await resp.json()
        assert data["error"] == "invalid secret"


@pytest.mark.asyncio
async def test_api_logout_rejects_missing_secret() -> None:
    """POST /api/logout rejects requests without secret header."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers import api_logout

    app = web.Application()
    app["local_secret"] = "correct-secret"
    app.router.add_post("/api/logout", api_logout)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/logout", json={})
        assert resp.status == 403
        data = await resp.json()
        assert data["error"] == "invalid secret"


@pytest.mark.asyncio
async def test_different_ports_use_different_cookie_names() -> None:
    """Two servers on different ports must not share cookies (RFC 6265 §8.5)."""
    mw_a = token_auth_middleware(port=7777)
    mw_b = token_auth_middleware(port=6777)
    token_a = generate_token("user_a", ttl_seconds=300)
    token_b = generate_token("user_b", ttl_seconds=300)

    req_a = _make_request(query={"token": token_a}, remote="127.0.0.1")
    resp_a = await mw_a(req_a, _ok_handler)
    assert resp_a.status == 200
    assert "gideon_token_7777" in resp_a.cookies
    assert "gideon_token_6777" not in resp_a.cookies
    legacy = resp_a.cookies.get("gideon_token")
    assert legacy is not None, "Legacy gideon_token cookie should be set for expiration"
    assert legacy["max-age"] == "0"

    req_b = _make_request(query={"token": token_b}, remote="127.0.0.1")
    resp_b = await mw_b(req_b, _ok_handler)
    assert resp_b.status == 200
    assert "gideon_token_6777" in resp_b.cookies
    assert "gideon_token_7777" not in resp_b.cookies


@pytest.mark.asyncio
async def test_wrong_port_cookie_rejected() -> None:
    """Server A must reject a cookie set by server B (different port suffix)."""
    mw_a = token_auth_middleware(port=7777)
    token_b = generate_token("user_b", ttl_seconds=300)
    bind_token_ip(token_b, "127.0.0.1")
    mark_consumed(token_b)

    req = _make_request(cookies={"gideon_token_6777": token_b}, remote="127.0.0.1")
    resp = await mw_a(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_non_default_port_full_cycle() -> None:
    """Full query-param → cookie-set → cookie-read cycle on non-default port."""
    mw = token_auth_middleware(port=6777)
    token = generate_token("user_6777", ttl_seconds=300)

    req1 = _make_request(query={"token": token}, remote="10.0.0.1")
    resp1 = await mw(req1, _ok_handler)
    assert resp1.status == 200
    cookie = resp1.cookies.get("gideon_token_6777")
    assert cookie is not None
    assert cookie.value == token

    req2 = _make_request(cookies={"gideon_token_6777": token}, remote="10.0.0.1")
    resp2 = await mw(req2, _ok_handler)
    assert resp2.status == 200


def _capturing_request(**kw):
    """A mock request whose item-assignment is backed by a real dict, so a test
    can read what the middleware set for request['app']/['user']."""
    req = _make_request(**kw)
    store: dict = {}
    req.__setitem__ = lambda _self, k, v: store.__setitem__(k, v)
    req.get = lambda k, default=None: store.get(k, default)
    req._store = store
    req.read_store = store
    return req


@pytest.mark.asyncio
async def test_app_token_bearer_sets_request_app() -> None:
    """An owner cookie + an app-scoped Bearer token → request['app'] adopts the
    app claim (owner user unchanged). This is how an app's fetch is scoped."""
    mw = token_auth_middleware()
    owner = generate_token("alice", ttl_seconds=300)
    bind_token_ip(owner, "127.0.0.1")
    mark_consumed(owner)
    app_tok = generate_token("alice", ttl_seconds=300, app="notes")
    req = _capturing_request(
        cookies={"gideon_token_10000": owner},
        headers={"Authorization": f"Bearer {app_tok}"},
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 200
    assert req.read_store.get("user") == "alice"
    assert req.read_store.get("app") == "notes"


@pytest.mark.asyncio
async def test_app_token_via_query_param_sets_request_app() -> None:
    """The /api/ws handshake can't set headers, so the app token rides ?app_token=."""
    mw = token_auth_middleware()
    owner = generate_token("alice", ttl_seconds=300)
    bind_token_ip(owner, "127.0.0.1")
    mark_consumed(owner)
    app_tok = generate_token("alice", ttl_seconds=300, app="notes")
    req = _capturing_request(
        cookies={"gideon_token_10000": owner},
        query={"app_token": app_tok},
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 200
    assert req.read_store.get("app") == "notes"


@pytest.mark.asyncio
async def test_app_token_for_other_user_is_ignored() -> None:
    """An app token minted for a DIFFERENT owner must NOT scope this session — a
    layered token can only narrow within the SAME authenticated user."""
    mw = token_auth_middleware()
    owner = generate_token("alice", ttl_seconds=300)
    bind_token_ip(owner, "127.0.0.1")
    mark_consumed(owner)
    foreign = generate_token("mallory", ttl_seconds=300, app="notes")
    req = _capturing_request(
        cookies={"gideon_token_10000": owner},
        headers={"Authorization": f"Bearer {foreign}"},
    )
    resp = await mw(req, _ok_handler)
    assert resp.status == 200
    assert req.read_store.get("user") == "alice"
    assert req.read_store.get("app", "") == ""


def _paired_token(user: str = "paired-device") -> tuple[str, str]:
    from gideon.interfaces.dashboard.session_store import DeviceInfo, attach_device

    token = generate_token(user, ttl_seconds=300)
    nonce = token_nonce(token)
    assert attach_device(
        nonce,
        DeviceInfo(id="device-1", name="Phone", kind="mobile", minted_at=1.0),
    )
    return token, nonce


@pytest.mark.asyncio
async def test_local_network_bypass_attaches_validated_paired_session(
    monkeypatch,
) -> None:
    from gideon.interfaces.dashboard.session_store import paired_session_record

    monkeypatch.setenv("GIDEON_BYPASS_LOCAL_NETWORKS", "1")
    token, nonce = _paired_token()
    req = _capturing_request(
        remote="192.168.1.20", cookies={"gideon_token_10000": token}
    )

    resp = await token_auth_middleware()(req, _ok_handler)

    assert resp.status == 200
    assert req.read_store["user"] == "paired-device"
    assert req.read_store["session_nonce"] == nonce
    record = paired_session_record(nonce)
    assert record is not None and record.device is not None
    assert record.device.last_seen > 0.0


@pytest.mark.asyncio
async def test_local_network_bypass_does_not_attach_unvalidated_identity(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GIDEON_BYPASS_LOCAL_NETWORKS", "1")
    req = _capturing_request(
        remote="192.168.1.20", cookies={"gideon_token_10000": "not-a-token"}
    )

    resp = await token_auth_middleware()(req, _ok_handler)

    assert resp.status == 200
    assert req.read_store["user"] == "local-net:192.168.1.20"
    assert "session_nonce" not in req.read_store


@pytest.mark.asyncio
async def test_auth_none_attaches_validated_paired_session() -> None:
    from gideon.security.auth.modes import AuthConfig, AuthMode

    token, nonce = _paired_token()
    req = _capturing_request(cookies={"gideon_token_7777": token})
    mw = auth_middleware(AuthConfig(mode=AuthMode.NONE), port=7777)

    resp = await mw(req, _ok_handler)

    assert resp.status == 200
    assert req.read_store["user"] == "paired-device"
    assert req.read_store["session_nonce"] == nonce


@pytest.mark.asyncio
async def test_auth_none_keeps_passthrough_for_invalid_cookie() -> None:
    from gideon.security.auth.modes import AuthConfig, AuthMode

    req = _capturing_request(cookies={"gideon_token_7777": "not-a-token"})
    mw = auth_middleware(AuthConfig(mode=AuthMode.NONE), port=7777)

    resp = await mw(req, _ok_handler)

    assert resp.status == 200
    assert "user" not in req.read_store
    assert "session_nonce" not in req.read_store
