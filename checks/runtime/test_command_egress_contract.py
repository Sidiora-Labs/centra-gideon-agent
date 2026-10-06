import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.action_providers.bash_provider import BashActionProvider
from gideon.integrations.sandbox_providers.base import (
    SandboxSpec,
    SandboxUnavailableError,
)
from gideon.integrations.sandbox_providers.none import NoneSandboxProvider
from gideon.security.guardrails.ceiling import reset_ceiling
from gideon.security.net.policy import egress_held_to, no_network_for_commands
from gideon.security.sandbox import (
    SandboxEnforcementUnavailable,
    egress_bound_argv,
    remove_wrap,
    reset_backend,
    wrap_argv,
)


@pytest.fixture
def command_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    ceiling = tmp_path / "ceiling.json"
    monkeypatch.setenv("GIDEON_CEILING_FILE", str(ceiling))

    def tier(value):
        ceiling.write_text(
            json.dumps({"version": 1, "scopes": {"egress": {"value": value}}})
        )
        reset_ceiling()

    reset_backend()
    yield tmp_path, tier
    reset_ceiling()
    reset_backend()


@pytest.mark.parametrize("tier", ["off", "listed", "registry"])
def test_restricted_command_refuses_disabled_sandbox_before_child(command_home, tier):
    home, set_tier = command_home
    set_tier(tier)
    marker = home / "spawned"
    with egress_held_to("unattended:trigger:stored"):
        assert no_network_for_commands()
        with pytest.raises(
            SandboxEnforcementUnavailable, match="This command was not run"
        ):
            wrap_argv(
                [sys.executable, "-c", f'open({str(marker)!r}, "w").write("spawned")'],
                "off",
            )
    assert not marker.exists()


@pytest.mark.asyncio
async def test_actual_bash_provider_refuses_unsupported_network_before_spawn(
    command_home,
):
    home, tier = command_home
    tier("off")
    marker = home / "spawned"
    from gideon.security.sandbox import _cannot_take_the_network

    if not _cannot_take_the_network("auto"):
        pytest.skip(
            "host supports OS network sandbox; real network test covers that branch"
        )
    with egress_held_to("unattended:trigger:stored"):
        result = await BashActionProvider().execute(
            {"command": f"touch {marker}"}, ActionContext(event="test"), timeout=2
        )
    assert not result.success
    assert "no network access" in result.error
    assert not marker.exists()


@pytest.mark.parametrize("tier", ["listed", "registry"])
def test_program_declared_host_list_is_explicitly_unsupported(command_home, tier):
    with pytest.raises(SandboxUnavailableError, match="cannot enforce"):
        NoneSandboxProvider().wrap(
            SandboxSpec(egress_tier=tier), [sys.executable, "-c", "pass"]
        )


def test_real_local_http_command_allowed_or_os_blocked(command_home):
    home, tier = command_home
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"local")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    marker = home / "child-executed"
    url = f"http://127.0.0.1:{server.server_port}/probe"
    code = f"import urllib.request; urllib.request.urlopen({url!r}, timeout=1).read()"
    try:
        tier("all")
        argv, cleanup = egress_bound_argv(
            [sys.executable, "-c", code], run="unattended:trigger:stored"
        )
        try:
            allowed = subprocess.run(argv, capture_output=True, timeout=5)
        finally:
            remove_wrap(cleanup)
        assert allowed.returncode == 0
        assert hits == ["/probe"]
        tier("off")
        with egress_held_to("unattended:trigger:stored"):
            try:
                argv, cleanup = wrap_argv(
                    [
                        sys.executable,
                        "-c",
                        f'open({str(marker)!r}, "w").write("started"); {code}',
                    ]
                )
            except SandboxEnforcementUnavailable as error:
                assert "no network access" in str(error)
                assert not marker.exists()
            else:
                try:
                    blocked = subprocess.run(argv, capture_output=True, timeout=5)
                finally:
                    remove_wrap(cleanup)
                assert blocked.returncode != 0
                assert marker.exists(), blocked.stderr.decode(errors="replace")
        assert hits == ["/probe"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["loop", "setup", "teardown"])
async def test_actual_raw_command_consumer_cannot_reach_local_http(
    command_home, consumer
):
    import shlex

    from gideon.automation.loop.gates import run_verify_command
    from gideon.automation.workflows.effects import run_teardown
    from gideon.automation.workflows.provisioning import run_step

    home, tier = command_home
    tier("off")
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/raw"
    command = shlex.join(
        [
            sys.executable,
            "-c",
            f"import urllib.request; urllib.request.urlopen({url!r}, timeout=1).read()",
        ]
    )
    try:
        if consumer == "loop":
            result = await run_verify_command(command, str(home))
            assert result is not True
        elif consumer == "setup":
            success, detail = await run_step(
                command, home, timeout=3, run_id="stored-run"
            )
            assert not success
            assert "no network" in detail
        else:
            success, detail = await run_teardown(command, "resource", timeout=3)
            assert not success
            assert "no network" in detail
        assert hits == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
