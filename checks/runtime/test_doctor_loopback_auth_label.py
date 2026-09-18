"""Doctor's auth line must reflect the real token posture, not the bind (#2860).

`gideon doctor` printed **`auth: loopback trusted (no token required)`** for
any local-bound gateway. But a default `gideon gateway` on a loopback bind
runs `AuthMode.local_token`, which STILL requires a token on loopback — a tokenless
loopback request gets `403 {"error": "Token required"}` from the `token_auth`
middleware. The diagnostic told the user they could reach the API without a token
when they could not.

A token is genuinely NOT required on loopback only in the three cases the middleware
short-circuits on: `AuthMode.NONE` (`GIDEON_AUTH_MODE=none`), the blanket
`GIDEON_DEV_NO_AUTH=1` skip, or the opt-in local-network bypass
(`GIDEON_BYPASS_LOCAL_NETWORKS=1`). `loopback_requires_token` is the predicate
that mirrors that logic; doctor consults it instead of inferring from the bind alone.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import patch

import pytest

from gideon.interfaces.dashboard.origin import loopback_requires_token

_TOKEN_REQUIRED = "🔒 token required"
_NO_TOKEN = "loopback trusted (no token required)"


def _clear_auth_env(monkeypatch) -> None:
    monkeypatch.delenv("GIDEON_AUTH_MODE", raising=False)
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)


def test_default_local_token_gateway_requires_a_token_on_loopback(monkeypatch):
    _clear_auth_env(monkeypatch)
    assert loopback_requires_token() is True


def test_auth_mode_none_needs_no_token(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    assert loopback_requires_token() is False


def test_dev_no_auth_flag_needs_no_token(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GIDEON_DEV_NO_AUTH", "1")
    assert loopback_requires_token() is False


def test_bypass_local_networks_needs_no_token(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GIDEON_BYPASS_LOCAL_NETWORKS", "1")
    assert loopback_requires_token() is False


def _doctor_output(capsys) -> str:
    """Run ``_doctor()`` with its probes stubbed and a forced local bind."""
    from gideon.interfaces.cli.doctor import _doctor

    with (
        patch(
            "gideon.interfaces.cli.doctor.shutil.which",
            side_effect=lambda b: f"/usr/local/bin/{b}",
        ),
        patch(
            "subprocess.run",
            return_value=type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": "Python 3.13.14",
                    "stderr": "",
                    "check_returncode": lambda self: None,
                },
            )(),
        ),
        patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")
        ),
        patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    return capsys.readouterr().out


def test_doctor_default_loopback_says_token_required(monkeypatch, capsys):
    _clear_auth_env(monkeypatch)
    out = _doctor_output(capsys)
    assert _TOKEN_REQUIRED in out, out
    assert _NO_TOKEN not in out, (
        "a default local_token gateway returns 403 to a tokenless loopback request; "
        f"doctor must not claim {_NO_TOKEN!r}:\n{out}"
    )


@pytest.mark.parametrize(
    "env",
    [
        {"GIDEON_AUTH_MODE": "none"},
        {"GIDEON_DEV_NO_AUTH": "1"},
        {"GIDEON_BYPASS_LOCAL_NETWORKS": "1"},
    ],
)
def test_doctor_genuine_bypass_says_no_token_required(env, monkeypatch, capsys):
    _clear_auth_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    out = _doctor_output(capsys)
    assert _NO_TOKEN in out, out
    assert _TOKEN_REQUIRED not in out, out


def _probe_app():
    """The real token-auth middleware over one trivial /api route."""
    from aiohttp import web

    from gideon.interfaces.dashboard.token_auth import token_auth_middleware

    async def _ok(_request):
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[token_auth_middleware()])
    app.router.add_get("/api/probe", _ok)
    return app


@pytest.mark.asyncio
async def test_the_predicate_agrees_with_what_the_middleware_actually_does(monkeypatch):
    """The claim doctor now prints has to be TRUE, not merely self-consistent.

    `loopback_requires_token` is a second statement of the middleware's short-circuits, and
    a second statement drifts. Driven against the real `token_auth_middleware` over a real
    loopback request: a tokenless call is refused under the default posture and served
    under the opt-in local-network bypass, and the predicate says so both times.
    """
    from aiohttp.test_utils import TestClient, TestServer

    _clear_auth_env(monkeypatch)
    async with TestClient(TestServer(_probe_app())) as client:
        denied = await client.get("/api/probe")
        assert denied.status == 403
        assert (await denied.json())["error"] == "Token required"
        assert loopback_requires_token() is True

    monkeypatch.setenv("GIDEON_BYPASS_LOCAL_NETWORKS", "1")
    async with TestClient(TestServer(_probe_app())) as client:
        allowed = await client.get("/api/probe")
        assert allowed.status == 200, await allowed.text()
        assert loopback_requires_token() is False
