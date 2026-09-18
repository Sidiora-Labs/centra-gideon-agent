"""Zero-key local-model onboarding — detection, explicit scanning, key-less binding.

Every test here drives the REAL path: a real aiohttp server standing in for Ollama on
a real loopback socket, the real egress guard and client, the real router, and a real
``config.json`` under a fixture ``GIDEON_HOME``. Nothing about the probe is faked —
the only test doubles are the *Ollama* on the other end of the socket and, where a
test needs to observe rather than replace, a spy that calls straight through to the
function it wraps.

The clauses the requirement names, and the test that carries each:

* **Detected and offered without a key** —
  ``test_detect_reports_the_live_local_service_and_asks_for_no_key`` /
  ``test_bind_writes_a_provider_row_carrying_no_key_material``. The second sweeps every
  byte under the home afterwards, because "we did not ask for a key" is only worth
  anything if nothing key-shaped was written either.
* **Explicit, time-bounded, live-probed scanning** —
  ``test_scan_probes_only_what_the_caller_named``,
  ``test_scan_is_bounded_by_the_wall_clock_it_was_given``,
  ``test_scan_offers_nothing_for_a_host_that_did_not_answer_as_ollama`` and
  ``test_bind_refuses_an_endpoint_whose_live_probe_fails``.
* **Never automatic** — ``test_no_scan_happens_without_the_explicit_call``, which spies
  on the real scan entry point across detect, bind and a GET of the scan path, plus
  ``test_the_scan_route_is_not_reachable_by_a_read``. A read that could put packets on
  a user's LAN is the failure mode those two exist to make impossible.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition import onboarding_local_models as engine
from gideon.interfaces.dashboard.handlers.onboarding_local_models import (
    register_onboarding_local_model_routes,
)

TAGS = {
    "models": [
        {"name": "qwen3:4b", "model": "qwen3:4b", "size": 2600000000},
        {"name": "nomic-embed-text:latest", "model": "nomic-embed-text:latest"},
    ]
}


def _ollama_app(*, body=None, status=200, delay=0.0, redirect_to="") -> web.Application:
    """An HTTP server that answers ``/api/tags`` the way the test needs it to."""

    async def tags(request: web.Request) -> web.Response:
        if delay:
            await asyncio.sleep(delay)
        if redirect_to:
            raise web.HTTPFound(location=redirect_to)
        return web.json_response(TAGS if body is None else body, status=status)

    app = web.Application()
    app.router.add_get(engine.TAGS_PATH, tags)
    return app


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fixture home, with the binding asserted before any test body runs.

    The bind route WRITES ``config.json``; a ``GIDEON_HOME`` that silently failed to
    take effect would have these tests editing the developer's real provider list.
    """
    from gideon.core.config.loader import config_dir

    root = tmp_path / "gideon-home"
    root.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(root))
    monkeypatch.setenv("GIDEON_SKIP_SKILL_SEED", "1")
    assert config_dir() == root, "GIDEON_HOME did not bind — the real home is at risk"
    return root


@pytest.fixture
def make_client(home: Path):
    """A factory for a client over an app carrying ONLY the three routes.

    A factory rather than a ready client because ``TestClient`` binds to the running
    loop, which does not exist while a sync fixture is being built — the house pattern
    in ``test_onboarding_import_api``.
    """

    def _make() -> TestClient:
        app = web.Application()
        register_onboarding_local_model_routes(app)
        return TestClient(TestServer(app))

    return _make


async def _serve(app: web.Application) -> tuple[TestServer, str]:
    """Start a server and return it with its ``http://127.0.0.1:<port>`` base URL."""
    server = TestServer(app)
    await server.start_server()
    return server, f"http://127.0.0.1:{server.port}"


def _config_providers(home: Path) -> list[dict]:
    path = home / "config.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("providers", [])


def _bytes_under(root: Path) -> bytes:
    blob = b""
    for path in sorted(root.rglob("*")):
        if path.is_file():
            blob += path.read_bytes()
    return blob


class TestProbe:
    async def test_a_live_ollama_answers_with_its_models(self, home):
        server, base = await _serve(_ollama_app())
        try:
            result = await engine.probe_ollama(base)
        finally:
            await server.close()
        assert result.ok is True
        assert result.models == ("qwen3:4b", "nomic-embed-text:latest")
        assert result.to_dict()["requires_key"] is False

    async def test_an_open_port_that_is_not_ollama_is_not_a_hit(self, home):
        """A 200 is not enough — a binding needs the service identified.

        The defect this forbids: a LAN box with anything at all on 11434 being offered
        as a model provider because the socket accepted.
        """
        server, base = await _serve(_ollama_app(body={"hello": "i am not ollama"}))
        try:
            result = await engine.probe_ollama(base)
        finally:
            await server.close()
        assert result.ok is False
        assert "not with an Ollama model catalog" in result.detail

    async def test_a_server_error_is_not_a_hit(self, home):
        server, base = await _serve(_ollama_app(status=503))
        try:
            result = await engine.probe_ollama(base)
        finally:
            await server.close()
        assert result.ok is False
        assert "503" in result.detail

    async def test_a_redirect_is_refused_rather_than_followed(self, home):
        """A probe reaches ONE host. A redirect would be a second one."""
        server, base = await _serve(
            _ollama_app(redirect_to="http://example.com/api/tags")
        )
        try:
            result = await engine.probe_ollama(base)
        finally:
            await server.close()
        assert result.ok is False

    async def test_a_public_endpoint_is_refused_by_the_egress_policy(self, home):
        result = await engine.probe_ollama("http://93.184.216.34:11434")
        assert result.ok is False
        assert "public" in result.detail

    async def test_a_hostname_is_refused_rather_than_resolved(self, home):
        result = await engine.probe_ollama("http://ollama.example.com:11434")
        assert result.ok is False
        assert "not a literal address" in result.detail

    async def test_nothing_listening_is_a_clean_miss(self, home):
        server, base = await _serve(_ollama_app())
        await server.close()
        result = await engine.probe_ollama(base, timeout_s=1.0)
        assert result.ok is False
        assert result.detail


class TestTargets:
    def test_private_addresses_and_cidrs_expand(self):
        assert engine.expand_targets(["192.168.1.0/30", "10.0.0.7"]) == [
            "192.168.1.0",
            "192.168.1.1",
            "192.168.1.2",
            "192.168.1.3",
            "10.0.0.7",
        ]

    def test_a_public_address_is_refused_before_anything_is_probed(self):
        with pytest.raises(engine.ScanRefused, match="public"):
            engine.expand_targets(["8.8.8.8"])

    def test_the_metadata_range_is_refused(self):
        with pytest.raises(engine.ScanRefused, match="link_local"):
            engine.expand_targets(["169.254.169.254"])

    def test_a_hostname_is_refused_because_a_scan_never_resolves_names(self):
        with pytest.raises(engine.ScanRefused, match="never resolves names"):
            engine.expand_targets(["nas.local"])

    def test_an_over_wide_prefix_is_refused(self):
        with pytest.raises(engine.ScanRefused, match="wider than /24"):
            engine.expand_targets(["10.0.0.0/8"])

    def test_more_than_the_cap_is_refused(self):
        with pytest.raises(engine.ScanRefused, match="narrow the range"):
            engine.expand_targets(["192.168.0.0/24", "192.168.1.0/24"])

    def test_an_empty_request_is_refused(self):
        with pytest.raises(engine.ScanRefused, match="always explicit"):
            engine.expand_targets([])

    def test_the_budget_is_clamped_to_the_ceiling(self):
        assert engine.clamp_budget(600) == engine.SCAN_MAX_BUDGET_S
        assert engine.clamp_budget(0) == engine.SCAN_DEFAULT_BUDGET_S
        assert engine.clamp_budget("nonsense") == engine.SCAN_DEFAULT_BUDGET_S
        assert engine.clamp_budget(2) == 2.0


class TestScan:
    async def test_scan_probes_only_what_the_caller_named(
        self, home, monkeypatch: pytest.MonkeyPatch
    ):
        """The live service is found, and every socket opened was one of the targets.

        ``OLLAMA_PORTS`` is repointed at the stand-in's ephemeral port — the port is
        configuration, and everything else (the guard, the client, the identification)
        is the shipping code path.
        """
        server, base = await _serve(_ollama_app())
        monkeypatch.setattr(engine, "OLLAMA_PORTS", (server.port,))
        dialled: list[str] = []
        real = engine.probe_ollama

        async def spy(endpoint, **kw):
            dialled.append(endpoint)
            return await real(endpoint, **kw)

        monkeypatch.setattr(engine, "probe_ollama", spy)
        try:
            report = await engine.scan_private_hosts(["127.0.0.1"], budget_s=3)
        finally:
            await server.close()

        assert [o.host for o in report.offers] == ["127.0.0.1"]
        assert report.offers[0].models == ("qwen3:4b", "nomic-embed-text:latest")
        assert report.offers[0].to_dict()["requires_key"] is False
        assert dialled == [base]

    async def test_scan_offers_nothing_for_a_host_that_did_not_answer_as_ollama(
        self, home, monkeypatch: pytest.MonkeyPatch
    ):
        server, _base = await _serve(_ollama_app(body=["not", "a", "catalog"]))
        monkeypatch.setattr(engine, "OLLAMA_PORTS", (server.port,))
        try:
            report = await engine.scan_private_hosts(["127.0.0.1"], budget_s=3)
        finally:
            await server.close()
        assert report.offers == ()
        assert report.probed == 1
        assert report.unreachable == 1

    async def test_scan_is_bounded_by_the_wall_clock_it_was_given(
        self, home, monkeypatch: pytest.MonkeyPatch
    ):
        """A hung service costs the budget, not the request.

        The stand-in sleeps far past the budget, so an unbounded scan would take five
        seconds. The assertion is on the wall clock the caller actually waited.
        """
        server, _base = await _serve(_ollama_app(delay=5.0))
        monkeypatch.setattr(engine, "OLLAMA_PORTS", (server.port,))
        try:
            report = await engine.scan_private_hosts(["127.0.0.1"], budget_s=0.4)
        finally:
            await server.close()
        assert report.elapsed_s < 3.0, "the scan outran the budget it was given"
        assert report.offers == (), "a host that never answered must not be offered"


class TestRoutes:
    async def test_detect_reports_the_live_local_service_and_asks_for_no_key(
        self, make_client, home, monkeypatch: pytest.MonkeyPatch
    ):
        server, base = await _serve(_ollama_app())
        monkeypatch.setattr(engine, "DEFAULT_LOCAL_ENDPOINTS", (base,))
        try:
            async with make_client() as client:
                resp = await client.get("/api/onboarding/local-models")
                assert resp.status == 200
                body = await resp.json()
        finally:
            await server.close()
        assert body["detected"] is True
        assert body["requires_key"] is False
        assert body["models"] == ["qwen3:4b", "nomic-embed-text:latest"]
        assert body["scan_limits"]["ports"] == list(engine.OLLAMA_PORTS)
        assert "key" not in json.dumps(body).replace("requires_key", "")

    async def test_detect_with_nothing_running_is_a_clean_negative(
        self, make_client, home, monkeypatch: pytest.MonkeyPatch
    ):
        server, base = await _serve(_ollama_app())
        await server.close()
        monkeypatch.setattr(engine, "DEFAULT_LOCAL_ENDPOINTS", (base,))
        async with make_client() as client:
            resp = await client.get("/api/onboarding/local-models")
            assert resp.status == 200
            body = await resp.json()
        assert body["detected"] is False
        assert body["models"] == []

    async def test_bind_writes_a_provider_row_carrying_no_key_material(
        self, make_client, home
    ):
        """The binding exists, and nothing key-shaped was asked for or written.

        The byte sweep over the whole home is the load-bearing half: a row with no
        ``credential`` field would still be a failure if a key had landed in
        ``credentials.json`` beside it.
        """
        server, base = await _serve(_ollama_app())
        try:
            async with make_client() as client:
                resp = await client.post(
                    "/api/onboarding/local-models/bind", json={"endpoint": base}
                )
                assert resp.status == 200, await resp.text()
                body = await resp.json()
        finally:
            await server.close()

        assert body["ok"] is True and body["requires_key"] is False
        rows = _config_providers(home)
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "ollama"
        assert row["type"] == "ollama"
        assert row["model"] == "qwen3:4b"
        assert row["options"] == {"endpoint": base}
        assert "credential" not in row
        assert set(row) == {"name", "type", "model", "options"}

        blob = _bytes_under(home).lower()
        for forbidden in (b"api_key", b"apikey", b"secret", b"token", b"credential"):
            assert forbidden not in blob, f"{forbidden!r} reached the home"
        assert not (home / "credentials.json").exists()

    async def test_bind_refuses_an_endpoint_whose_live_probe_fails(
        self, make_client, home
    ):
        """No live probe, no binding — and nothing written on the way out."""
        server, base = await _serve(_ollama_app(status=500))
        try:
            async with make_client() as client:
                resp = await client.post(
                    "/api/onboarding/local-models/bind", json={"endpoint": base}
                )
                assert resp.status == 400
                body = await resp.json()
        finally:
            await server.close()
        assert body["error"]["code"] == "local_model_probe_failed"
        assert _config_providers(home) == []

    async def test_bind_refuses_a_public_endpoint(self, make_client, home):
        async with make_client() as client:
            resp = await client.post(
                "/api/onboarding/local-models/bind",
                json={"endpoint": "http://93.184.216.34:11434"},
            )
            assert resp.status == 400
        assert _config_providers(home) == []

    async def test_scan_route_refuses_a_public_target_without_probing(
        self, make_client, home, monkeypatch: pytest.MonkeyPatch
    ):
        dialled: list[str] = []
        real = engine.probe_ollama

        async def spy(endpoint, **kw):
            dialled.append(endpoint)
            return await real(endpoint, **kw)

        monkeypatch.setattr(engine, "probe_ollama", spy)
        async with make_client() as client:
            resp = await client.post(
                "/api/onboarding/local-models/scan",
                json={"targets": ["192.168.4.0/30", "8.8.8.8"]},
            )
            assert resp.status == 400
            body = await resp.json()
        assert body["error"]["code"] == "local_model_scan_refused"
        assert dialled == [], "a refused scan must not have touched the network"

    async def test_scan_route_finds_the_live_service_it_was_pointed_at(
        self, make_client, home, monkeypatch: pytest.MonkeyPatch
    ):
        server, base = await _serve(_ollama_app())
        monkeypatch.setattr(engine, "OLLAMA_PORTS", (server.port,))
        try:
            async with make_client() as client:
                resp = await client.post(
                    "/api/onboarding/local-models/scan",
                    json={"targets": ["127.0.0.1"], "budget_s": 3},
                )
                assert resp.status == 200
                body = await resp.json()
        finally:
            await server.close()
        assert [o["endpoint"] for o in body["offers"]] == [base]
        assert body["offers"][0]["requires_key"] is False
        assert body["targets"] == 1

    async def test_the_scan_route_is_not_reachable_by_a_read(self, make_client, home):
        """A GET cannot start a scan. The method IS half of "never automatically"."""
        async with make_client() as client:
            resp = await client.get("/api/onboarding/local-models/scan")
            assert resp.status == 405


class TestNeverAutomatic:
    async def test_no_scan_happens_without_the_explicit_call(
        self, make_client, home, monkeypatch: pytest.MonkeyPatch
    ):
        """Detect, bind and a read of the scan path reach no private address.

        The spies call straight through to the real functions, so this measures the
        shipping code rather than a stand-in for it. ``scans`` counts entries into the
        scan engine; ``dialled`` records every endpoint any probe opened, which is what
        proves detection's reach is loopback and nothing else.
        """
        scans: list[tuple] = []
        dialled: list[str] = []
        real_scan = engine.scan_private_hosts
        real_probe = engine.probe_ollama

        async def scan_spy(targets, **kw):
            scans.append(tuple(targets))
            return await real_scan(targets, **kw)

        async def probe_spy(endpoint, **kw):
            dialled.append(endpoint)
            return await real_probe(endpoint, **kw)

        monkeypatch.setattr(engine, "scan_private_hosts", scan_spy)
        monkeypatch.setattr(engine, "probe_ollama", probe_spy)

        server, base = await _serve(_ollama_app())
        monkeypatch.setattr(engine, "DEFAULT_LOCAL_ENDPOINTS", (base,))
        try:
            async with make_client() as client:
                for _ in range(3):
                    assert (
                        await client.get("/api/onboarding/local-models")
                    ).status == 200
                assert (
                    await client.get("/api/onboarding/local-models/scan")
                ).status == 405
                bind = await client.post(
                    "/api/onboarding/local-models/bind", json={"endpoint": base}
                )
                assert bind.status == 200

                assert scans == [], "something scanned the private network unasked"
                for endpoint in dialled:
                    host = endpoint.rsplit(":", 1)[0].split("//", 1)[1]
                    assert host == "127.0.0.1", f"detection reached {host}"

                monkeypatch.setattr(engine, "OLLAMA_PORTS", (server.port,))
                asked = await client.post(
                    "/api/onboarding/local-models/scan",
                    json={"targets": ["192.168.77.1"], "budget_s": 1},
                )
                assert asked.status == 200
        finally:
            await server.close()

        assert scans == [("192.168.77.1",)], "the explicit call is the only scan"


class TestPolicy:
    def test_the_probe_policy_pins_one_host_and_keeps_the_metadata_denies(self):
        from gideon.security.net.policy import (
            METADATA_SERVICE_HOSTS,
            local_model_probe_policy,
        )

        policy = local_model_probe_policy("http://192.168.1.50:11434")
        assert policy.allow_only is True
        assert policy.allow_hosts == ("192.168.1.50",)
        assert policy.allow_private is True
        assert policy.max_redirects == 0
        for host in METADATA_SERVICE_HOSTS:
            assert host in policy.deny_hosts

    def test_the_probe_policy_refuses_anything_that_is_not_loopback_or_private(self):
        from gideon.security.net.policy import (
            LocalModelProbeRefused,
            local_model_probe_policy,
        )

        for endpoint in (
            "http://8.8.8.8:11434",
            "http://169.254.169.254:11434",
            "http://nas.example.com:11434",
            "ftp://192.168.1.5:11434",
            "",
        ):
            with pytest.raises(LocalModelProbeRefused):
                local_model_probe_policy(endpoint)

    def test_a_pinned_private_host_is_actually_allowed_by_the_guard(self):
        """VACUITY: the refusals above are the policy working, not everything failing."""
        from gideon.security.net.guard import evaluate
        from gideon.security.net.policy import local_model_probe_policy

        policy = local_model_probe_policy("http://192.168.1.50:11434")
        decision = evaluate(
            "http://192.168.1.50:11434/api/tags",
            policy,
            resolver=lambda host: ["192.168.1.50"],
        )
        assert decision.allow is True
        assert decision.pinned_ips == ["192.168.1.50"]
