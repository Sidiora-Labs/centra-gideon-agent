"""Real loopback download transports: per-hop refusal before the second request."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from gideon.core.library_environment import library_environment
from gideon.interfaces.cli.app_new import ScaffoldError, fetch_template_archive
from gideon.sdk.net import open_url
from gideon.security.guardrails.ceiling import reset_ceiling
from gideon.security.net import libraries
from gideon.security.net.client import EgressBlocked, check
from gideon.security.net.policy import egress_held_to


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    ceiling = tmp_path / "ceiling.json"
    monkeypatch.setenv("GIDEON_CEILING_FILE", str(ceiling))
    ceiling.write_text(
        json.dumps({"version": 1, "scopes": {"egress": {"value": "listed"}}})
    )
    reset_ceiling()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"security": {"egress": {"allow_hosts": ["localhost"]}}})
    )
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.headers.get("Host"), self.path))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{self.server.server_port}/forbidden"
                )
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Length", "14")
                self.end_headers()
                self.wfile.write(b"local-download")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://localhost:{server.server_port}", hits, config
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    reset_ceiling()


def test_sdk_stream_download_permitted_then_redirect_refused(downloads):
    base, hits, config = downloads
    with egress_held_to("unattended:download:stored"):
        with open_url(base + "/permitted", timeout_s=2) as response:
            assert response.status == 200
            assert response.read(5) + response.read() == b"local-download"
        with pytest.raises(EgressBlocked) as refused:
            open_url(base + "/redirect", timeout_s=2)
    assert [path for _, path in hits] == ["/permitted", "/redirect"]
    assert refused.value.decision.category == "not_listed"
    assert "Allowed hosts" in str(refused.value)
    assert "then start the download again" in str(refused.value)


def test_httpx_library_client_real_redirect_hook(downloads):
    base, hits, _ = downloads
    with egress_held_to("unattended:download:stored"):
        with libraries._with(
            httpx.Client(trust_env=False, follow_redirects=True, timeout=2),
            libraries._before,
        ) as client:
            assert client.get(base + "/permitted").content == b"local-download"
            with pytest.raises(EgressBlocked):
                client.get(base + "/redirect")
    assert [path for _, path in hits] == ["/permitted", "/redirect"]


@pytest.mark.asyncio
async def test_async_library_client_carries_trusted_run_to_guard(downloads):
    base, hits, _ = downloads
    with egress_held_to("unattended:download:stored"):
        async with libraries._with(
            httpx.AsyncClient(trust_env=False, follow_redirects=True, timeout=2),
            libraries._before_async,
        ) as client:
            assert (await client.get(base + "/permitted")).content == b"local-download"
            with pytest.raises(EgressBlocked):
                await client.get(base + "/redirect")
    assert [path for _, path in hits] == ["/permitted", "/redirect"]


def test_template_consumer_policy_refuses_before_any_network(downloads):
    _, hits, config = downloads
    config.write_text(
        json.dumps({"security": {"egress": {"deny_hosts": ["codeload.github.com"]}}})
    )
    with pytest.raises(ScaffoldError, match="Denied hosts"):
        fetch_template_archive("https://codeload.github.com/fixture/archive.tar.gz")
    assert not hits


def test_download_refusal_does_not_expose_url_credentials(downloads):
    base, hits, _ = downloads
    url = (
        base.replace("localhost", "127.0.0.1").replace(
            "http://", "http://user:password@"
        )
        + "/asset?token=private-value"
    )
    with egress_held_to("unattended:download:stored"):
        with pytest.raises(EgressBlocked) as refused:
            check(url)
    assert not hits
    assert all(
        value not in str(refused.value)
        for value in ["user:", "password", "private-value", "?token"]
    )


def test_optional_hub_absence_keeps_actual_cli_startup_working(tmp_path):
    env = {
        **os.environ,
        "GIDEON_HOME": str(tmp_path),
        "GIDEON_PROJECT_DIR": str(tmp_path),
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "runtime")
    env["HF_HUB_DISABLE_XET"] = "0"
    entry = 'import os; from gideon.interfaces.cli.main import main\ntry: main()\nexcept SystemExit:\n assert os.environ["HF_HUB_DISABLE_XET"] == "1"\n assert os.environ["HF_HOME"].startswith(os.environ["GIDEON_HOME"])\n raise'
    command = [sys.executable, "-c", entry, "--help"]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "Chat with your assistant through the running gateway" in result.stdout
    # This host does not have the Hub dependency; it is not imported by installing hooks.
    script = 'import sys; from gideon.security.net import libraries; libraries.install(); libraries.install(); import os; assert os.environ["HF_HUB_DISABLE_XET"] == "1"; assert "huggingface_hub" not in sys.modules; assert sum(isinstance(f, libraries._GuardOnImport) for f in sys.meta_path) == 1'
    installed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert installed.returncode == 0, installed.stderr


def test_child_environment_cannot_reenable_unguarded_xet(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.security.sandbox import build_child_env

    computed = library_environment(
        source={"GIDEON_HOME": str(tmp_path), "HF_HUB_DISABLE_XET": "0"}
    )
    assert computed["HF_HUB_DISABLE_XET"] == "1"
    child = build_child_env(
        site="download-test",
        source={"GIDEON_HOME": str(tmp_path), "HF_HUB_DISABLE_XET": "0"},
        extra={"HF_HUB_DISABLE_XET": "0"},
    )
    assert child["HF_HUB_DISABLE_XET"] == "1"
