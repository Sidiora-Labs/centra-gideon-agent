"""``gideon spawn`` authenticates, and says which failure it hit (req.10).

Driven against a REAL aiohttp gateway: the real ``token_auth_middleware`` guarding the
real ``/api/spawn`` mixed-internal route, the real ``/api/token/local`` handler minting
from a real ``.local_secret``, and the real ``/api/healthz`` liveness route. A stubbed
auth layer could not show the property being claimed here — that the credential the CLI
mints is one this gateway's own middleware accepts.

The two failures are asserted as a PAIR, because the defect was not that either message
was missing: both conditions printed "gateway not running", so the message that was
present was the wrong one. A refused connection and a rejected credential are therefore
always asserted against each other's text.
"""

from __future__ import annotations

import asyncio
import socket
from argparse import Namespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.interfaces.cli import commands as cli_commands


def _free_port() -> int:
    """A port nothing is listening on — a connection to it is refused, not slow."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _Gateway:
    """A real gateway front door for the spawn routes, recording what arrived."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.server: TestServer | None = None

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.port

    def _record(self, request: web.Request) -> None:
        self.requests.append(
            {
                "path": request.path,
                "method": request.method,
                "token": request.query.get("token", ""),
                "authorization": request.headers.get("Authorization", ""),
            }
        )

    async def _list(self, request: web.Request) -> web.Response:
        self._record(request)
        return web.json_response(
            {"agents": [{"id": "agent-1", "task": "audit the logs", "done": False}]}
        )

    async def _spawn(self, request: web.Request) -> web.Response:
        self._record(request)
        body = await request.json()
        return web.json_response({"id": "agent-9", "task": body.get("task", "")})

    async def _status(self, request: web.Request) -> web.Response:
        self._record(request)
        return web.json_response({"done": True, "result": "finished the task"})

    async def start(self, secret: str) -> None:
        from gideon.interfaces.dashboard.handlers import api_healthz, api_token_local
        from gideon.interfaces.dashboard.token_auth import token_auth_middleware

        port = _free_port()
        app = web.Application(
            middlewares=[
                token_auth_middleware(
                    mixed_internal_paths=frozenset({"/api/spawn"}),
                    internal_secret=secret,
                    port=port,
                    local_only=True,
                )
            ]
        )
        app["local_secret"] = secret
        app.router.add_get("/api/healthz", api_healthz)
        app.router.add_get("/api/token/local", api_token_local)
        app.router.add_get("/api/spawn", self._list)
        app.router.add_post("/api/spawn", self._spawn)
        app.router.add_get("/api/spawn/{agent_id}", self._status)
        self.server = TestServer(app, port=port)
        await self.server.start_server()

    async def stop(self) -> None:
        if self.server is not None:
            await self.server.close()


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    """A running gateway whose ``.local_secret`` this process shares."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    secret = "s3cret-local-handshake"
    (tmp_path / ".local_secret").write_text(secret, encoding="utf-8")
    gw = _Gateway()
    await gw.start(secret)
    yield gw
    await gw.stop()


def _args(port: int, action: str, **over) -> Namespace:
    defaults = {
        "port": port,
        "spawn_action": action,
        "task": "audit the logs",
        "fire_and_forget": True,
    }
    defaults.update(over)
    return Namespace(**defaults)


async def _drive(fn, *a, **kw):
    """Run a blocking CLI function without stalling the gateway's own loop."""
    return await asyncio.to_thread(fn, *a, **kw)


class TestSpawnAuthenticates:
    """ac_1 — probe, mint, and present the credential on EVERY request."""

    async def test_list_succeeds_and_carries_the_credential(self, gateway, capsys):
        await _drive(cli_commands._spawn, _args(gateway.port, "list"))

        out = capsys.readouterr().out
        assert "agent-1" in out
        assert "audit the logs" in out
        assert [r["path"] for r in gateway.requests] == ["/api/spawn"]

    async def test_run_succeeds_and_carries_the_credential(self, gateway, capsys):
        await _drive(cli_commands._spawn, _args(gateway.port, "run"))

        assert "Spawned subagent agent-9" in capsys.readouterr().out
        assert [r["method"] for r in gateway.requests] == ["POST"]

    async def test_every_spawn_request_presents_the_token(self, gateway, capsys):
        """The poll loop is a request too — an unauthenticated poll would 403 forever.

        Asserted across list, run and the status poll together: a credential attached at
        one call site and forgotten at the next is the shape this defect had.
        """
        await _drive(cli_commands._spawn, _args(gateway.port, "list"))
        await _drive(
            cli_commands._spawn, _args(gateway.port, "run", fire_and_forget=False)
        )
        capsys.readouterr()

        assert [r["path"] for r in gateway.requests] == [
            "/api/spawn",
            "/api/spawn",
            "/api/spawn/agent-9",
        ]
        for rec in gateway.requests:
            assert rec["token"], f"no ?token= on {rec['method']} {rec['path']}"
            assert rec["authorization"].startswith(
                "Bearer "
            ), f"no Authorization header on {rec['method']} {rec['path']}"
        run_tokens = {r["token"] for r in gateway.requests[1:]}
        assert (
            len(run_tokens) == 1
        ), "the poll used a different credential from the spawn that started it"

    async def test_the_token_is_the_one_the_gateway_minted(self, gateway, capsys):
        """VACUITY: the middleware must be the thing accepting it.

        A recorded header proves nothing if any string would have been let through, so the
        token the CLI sent is checked against the one this gateway's own
        ``/api/token/local`` issues for the same secret.
        """
        from gideon.interfaces.cli.run import mint_local_token
        from gideon.interfaces.dashboard.token_auth import validate_token

        await _drive(cli_commands._spawn, _args(gateway.port, "list"))
        capsys.readouterr()

        sent = gateway.requests[0]["token"]
        valid, user, reason = validate_token(sent, use_session_exp=True)
        assert valid, reason
        assert user == "local-app"
        assert await _drive(mint_local_token, gateway.port)

    async def test_an_unauthenticated_request_is_refused_by_this_gateway(self, gateway):
        """VACUITY floor for the whole file: the route really is guarded.

        Without this, every success above would also pass against a gateway that never
        checked anything, and the credential would be decorative.
        """
        import urllib.error

        with pytest.raises(urllib.error.HTTPError) as caught:
            await _drive(cli_commands._spawn_request, gateway.port, "/api/spawn", "")
        assert caught.value.code in (401, 403)


class TestSpawnTellsTheTwoFailuresApart:
    """ac_2 — a rejected credential is not an absent gateway."""

    async def test_a_refused_connection_reports_an_unavailable_gateway(self, capsys):
        port = _free_port()
        with pytest.raises(SystemExit) as exit_info:
            await _drive(cli_commands._spawn, _args(port, "list"))

        assert exit_info.value.code == 1
        err = capsys.readouterr().err
        assert "gateway not running" in err
        assert str(port) in err
        assert "gideon gateway" in err, "the remedy must name how to start one"
        assert "authentication failed" not in err

    async def test_a_rejected_handshake_reports_an_authentication_failure(
        self, gateway, tmp_path, capsys
    ):
        """The gateway is up and answering — only the secret is wrong.

        This is the case that used to print "gateway not running" while the gateway was
        demonstrably serving, which sent operators to restart a healthy process.
        """
        (tmp_path / ".local_secret").write_text("wrong-secret", encoding="utf-8")

        with pytest.raises(SystemExit) as exit_info:
            await _drive(cli_commands._spawn, _args(gateway.port, "list"))

        assert exit_info.value.code == 1
        err = capsys.readouterr().err
        assert "authentication failed" in err
        assert "gateway not running" not in err
        assert "GIDEON_HOME" in err, "the remedy must name the credential's home"

    async def test_a_credential_the_gateway_rejects_is_an_auth_failure_not_an_outage(
        self, gateway, capsys
    ):
        """A 403 on the spawn route itself — a stale token, not a dead gateway.

        Driven at ``_spawn_run`` with a credential the REAL middleware refuses, so the
        403 is produced by the gateway's own auth path rather than by a stand-in.
        """
        with pytest.raises(SystemExit) as exit_info:
            await _drive(
                cli_commands._spawn_run,
                _args(gateway.port, "run"),
                gateway.port,
                "a-token-this-gateway-never-issued",
            )

        assert exit_info.value.code == 1
        err = capsys.readouterr().err
        assert "authentication failed" in err
        assert "gateway not running" not in err

    async def test_the_health_probe_runs_before_the_mint(self, gateway, monkeypatch):
        """ac_1's ordering clause, asserted as ordering.

        Minting first would ask a possibly-absent gateway for a token and then have to
        read the transport error as "absent" — the collapse this task removes.
        """
        from gideon.interfaces.cli import run as cli_run

        calls: list[str] = []

        probe = cli_run.probe_gateway
        mint = cli_run.mint_local_token
        monkeypatch.setattr(
            cli_run,
            "probe_gateway",
            lambda *a, **k: calls.append("probe") or probe(*a, **k),
        )
        monkeypatch.setattr(
            cli_run,
            "mint_local_token",
            lambda *a, **k: calls.append("mint") or mint(*a, **k),
        )

        await _drive(cli_commands._spawn, _args(gateway.port, "list"))

        assert calls == ["probe", "mint"]


class TestSpawnUsage:
    def test_an_unknown_subcommand_prints_usage_without_dialing_the_gateway(
        self, capsys
    ):
        """No credential is minted for a command that was never going to make a call."""
        cli_commands._spawn(Namespace(port=_free_port(), spawn_action=None))
        assert "Usage: gideon spawn" in capsys.readouterr().out
