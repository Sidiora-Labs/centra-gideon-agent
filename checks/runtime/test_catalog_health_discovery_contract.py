"""Health preserves fail-soft discovery reasons using a real loopback HTTP server."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gideon.extensions.providers.connection import CONNECTED, FAILED, measure
from gideon.integrations.llm.catalog import (
    ModelCatalog,
    ModelDiscoveryFailure,
    openai_compatible_list_models,
)
from gideon.interfaces.dashboard.handlers.model_registry import _bounded_catalog_build
from gideon.security.net.policy import LOOPBACK_INTERNAL


@pytest.mark.asyncio
async def test_real_refusal_outage_empty_and_concurrent_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    # The qualification permits only this explicit loopback server; no public model call.
    monkeypatch.setattr("gideon.sdk.net.egress_policy_for", lambda _: LOOPBACK_INTERNAL)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            status = (
                401
                if self.path.startswith("/blocked")
                else 503 if self.path.startswith("/outage") else 200
            )
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b'{"data":[],"error":"private-body-must-not-escape"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"

    class Catalog(ModelCatalog):
        def __init__(self, path):
            self.path = path

        async def list_models(self):
            return await openai_compatible_list_models(
                endpoint + self.path, "local-test-key"
            )

    try:
        refused, empty, down = await asyncio.gather(
            measure(Catalog("/blocked")),
            measure(Catalog("/empty")),
            measure(Catalog("/outage")),
        )
        assert refused.state == FAILED and refused.rejected_credential
        assert "401" in refused.detail and "rejected" in refused.detail
        assert (
            down.state == FAILED
            and not down.rejected_credential
            and "503" in down.detail
        )
        assert empty.state == CONNECTED and not empty.rejected_credential
        assert all(
            "private-body" not in row.detail and "local-test-key" not in row.detail
            for row in [refused, empty, down]
        )
        with pytest.raises(ModelDiscoveryFailure, match="401"):
            await _bounded_catalog_build(Catalog("/blocked").list_models)
        assert await _bounded_catalog_build(Catalog("/empty").list_models) == []
        # Outside measurement the established compatibility reader remains fail-soft.
        assert await Catalog("/outage").list_models() == []
        assert len(seen) == 6
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
