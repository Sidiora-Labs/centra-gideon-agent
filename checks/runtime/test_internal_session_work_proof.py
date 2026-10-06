"""Native IPC and real pooled ACP process session work proof round trips."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_dashboard_ingress_identity import state_at

from gideon.core.config import loader as config_loader
from gideon.integrations import mcp_core
from gideon.integrations.acp.client import AcpClient
from gideon.interfaces.dashboard.handlers._shared import _blocks_reads_session
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.operations.durability.inventory import is_ignored
from gideon.security.approval_answer import (
    AGENT,
    OWNER,
    Principal,
    of_request,
    refusal,
    work_principal_of_request,
)
from gideon.security.session_credentials import (
    _proof_path,
    begin_child_turn,
    begin_turn,
    current_work,
    end_turn,
    publish_pid,
    verify,
    work_of_request,
)


def service(state):
    secret = "gateway-test-only"
    home = config_loader.config_dir()
    assert home != Path.home() / ".gideon", "test must use explicit isolated home"
    (home / ".local_secret").write_text(secret)
    app = web.Application(
        middlewares=[
            token_auth_middleware(
                internal_routes=frozenset({"GET /scope"}), internal_secret=secret
            )
        ]
    )
    app["state"] = state
    app["local_secret"] = secret

    async def scope(request):
        proof = work_of_request(request)
        return web.json_response(
            {
                "caller": of_request(request).label,
                "work": work_principal_of_request(request).label,
                "read_blocked": _blocks_reads_session(state, request),
                "bound_session": proof.session_key if proof else "",
                "approval_refused": bool(refusal(of_request(request))),
            }
        )

    app.router.add_get("/scope", scope)
    app.router.add_get("/outside", scope)
    return app


def parent_session(state, key, name="sir"):
    session = state.get_or_create_session(key)
    session._initiator = {"kind": "owner", "name": name, "tenant": ""}
    return session


@pytest.mark.asyncio
async def test_real_native_ipc_proof_binds_work_and_never_approval_authority(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    state = state_at(tmp_path)
    parent_session(state, "native")
    lease = begin_turn(
        "dashboard:native",
        Principal(OWNER, "sir"),
        turn_id="turn-one",
        memory_mode="persistent",
    )
    token = mcp_core.set_current_session_key("dashboard:native")
    try:
        app = service(state)
        assert config_loader.config_dir().is_relative_to(tmp_path)
        headers = mcp_core._internal_headers()
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/scope", headers=headers)
            result = await response.json()
            assert response.status == 200
            assert result == {
                "caller": "agent:dashboard:native",
                "work": "owner:sir",
                "read_blocked": False,
                "bound_session": "dashboard:native",
                "approval_refused": True,
            }
            forged = {**headers, "X-Session-Key": "dashboard:other"}
            assert (await client.get("/scope", headers=forged)).status == 403
            no_proof = {k: v for k, v in headers.items() if k != "X-Session-Proof"}
            assert (await (await client.get("/scope", headers=no_proof)).json())[
                "read_blocked"
            ] is True
            assert (await client.get("/outside", headers=headers)).status == 403
            assert (
                await client.get(
                    "/scope", headers={"X-Session-Key": "dashboard:native"}
                )
            ).status in (401, 403)
            end_turn(lease)
            lease = None
            assert (await client.get("/scope", headers=headers)).status == 403
    finally:
        mcp_core.reset_current_session_key(token)
        end_turn(lease)


@pytest.mark.asyncio
async def test_real_acp_process_rekey_overrides_stale_environment_and_revokes_old_turn(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    state = state_at(tmp_path / "history")
    parent_session(state, "first", "first-owner")
    parent_session(state, "second", "second-owner")
    runtime_path = str(Path(__file__).resolve().parents[2] / "runtime")
    script = tmp_path / "acp_peer.py"
    script.write_text("""import sys,json,subprocess,os
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 method=r['method']; result={}
 if method=='initialize': result={'protocolVersion':'2025-08-22','agentCapabilities':{}}
 elif method=='session/new': result={'sessionId':'S1'}
 elif method=='identity/probe':
  code="import urllib.request,json; from gideon.integrations.mcp_core import _internal_headers; req=urllib.request.Request("+repr(r['params']['url'])+",headers=_internal_headers()); print(urllib.request.urlopen(req).read().decode())"
  result=json.loads(subprocess.check_output([sys.executable,'-c',code],env=os.environ,text=True))
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
""")
    first = begin_turn(
        "dashboard:first",
        Principal(OWNER, "first-owner"),
        turn_id="first",
        memory_mode="persistent",
    )
    second = None
    client = AcpClient(
        work_dir=tmp_path,
        command=[sys.executable, str(script)],
        sandbox_mode="none",
        session_key="dashboard:first",
        extra_env={"PYTHONPATH": runtime_path},
    )
    try:
        async with TestClient(TestServer(service(state))) as http:
            await client.ensure_ready()
            publish_pid("dashboard:first", client._pid)
            path = _proof_path(client._pid)
            assert is_ignored(path.relative_to(config_loader.config_dir()).as_posix())
            assert path.stat().st_mode & 0o777 == 0o600
            response = await client._connection.request(
                "identity/probe", {"url": str(http.make_url("/scope"))}, timeout=10
            )
            assert response.result["work"] == "owner:first-owner"
            old_headers = {
                "X-Internal-Secret": "gateway-test-only",
                "X-Session-Key": "dashboard:first",
                "X-Session-Proof": first.bearer,
            }
            end_turn(first)
            first = None
            second = begin_turn(
                "dashboard:second",
                Principal(OWNER, "second-owner"),
                turn_id="second",
                memory_mode="persistent",
            )
            client.rekey("dashboard:second")
            # Existing child env still says first. Live PID+start binding must win.
            response = await client._connection.request(
                "identity/probe", {"url": str(http.make_url("/scope"))}, timeout=10
            )
            assert response.result["work"] == "owner:second-owner"
            assert response.result["bound_session"] == "dashboard:second"
            assert response.result["approval_refused"] is True
            assert (await http.get("/scope", headers=old_headers)).status == 403
            mismatch = {**old_headers, "X-Session-Proof": second.bearer}
            assert (await http.get("/scope", headers=mismatch)).status == 403
            record = json.loads(path.read_text())
            record["process_start"] = -1
            path.write_text(json.dumps(record))
            # Real old environment is revoked; a reused PID record cannot authenticate.
            assert verify(old_headers["X-Session-Proof"], "dashboard:first") is None
            end_turn(second)
            second = None
            assert not path.exists()
    finally:
        await client.shutdown()
        end_turn(second)
        end_turn(first)


def test_bound_child_inherits_immutable_origin_and_expired_proof_fails_closed():
    parent = begin_turn(
        "dashboard:parent",
        Principal(OWNER, "sir"),
        turn_id="parent",
        memory_mode="temporary",
    )
    try:
        child = begin_child_turn("subagent:child", current_work(), turn_id="child")
        try:
            work = verify(child.bearer, "subagent:child")
            assert work.origin_session_key == "dashboard:parent"
            assert (
                work.initiator == Principal(OWNER, "sir")
                and work.memory_mode == "temporary"
            )
            assert verify(child.bearer, "dashboard:parent") is None
        finally:
            end_turn(child)
        expired = begin_turn(
            "dashboard:expired",
            Principal(OWNER, "sir"),
            turn_id="expired",
            memory_mode="persistent",
            ttl=0,
        )
        try:
            assert verify(expired.bearer, "dashboard:expired") is None
        finally:
            end_turn(expired)
    finally:
        end_turn(parent)


def test_current_and_revoked_proof_never_leak_in_acp_trace_or_redaction_warnings(
    tmp_path,
):
    import subprocess

    from gideon.security.security import redact_credentials

    lease = begin_turn(
        "dashboard:redaction",
        Principal(OWNER, "sir"),
        turn_id="trace",
        memory_mode="persistent",
    )
    secret = lease.bearer
    end_turn(lease)
    cleaned, warnings = redact_credentials(
        json.dumps({"name": "GIDEON_SESSION_PROOF", "value": secret})
    )
    assert secret not in cleaned and not any(secret[:20] in item for item in warnings)
    env = {
        **os.environ,
        "GIDEON_HOME": str(tmp_path / "home"),
        "GIDEON_ACP_TRACE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "runtime"),
    }
    code = "import sys,logging; logging.basicConfig(level=logging.INFO); from gideon.integrations.acp.transport import _acp_trace; _acp_trace('>>',sys.stdin.read())"
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps({"name": "GIDEON_SESSION_PROOF", "value": secret}),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert (
        "ACP-TRACE" in result.stderr
        and secret not in result.stderr
        and secret[:20] not in result.stderr
    )
