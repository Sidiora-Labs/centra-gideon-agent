"""``/api/onboarding/import`` — the onboarding step's two calls (PEP-5).

PEP-4 shipped the engine with no HTTP surface at all; this is the surface, and these
are the properties that make it safe to hand to a first-run screen. Every test drives
the REAL router (``TestClient`` over the registered app), a FIXTURE foreign root bound
through ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``, and a FIXTURE home bound through
``GIDEON_HOME`` — the developer's real ``~/.claude`` is never read and their real
``~/.gideon`` is never written. The fixture asserts both redirects BIND before any
test body runs, because an isolation lever that silently missed would turn this suite
into a scan of the machine it runs on.

The load-bearing tests, one per clause the atom names:

* ``test_fresh_home_scan_shows_the_source_with_nothing_already_imported`` — a fresh home
  with a fixture source shows the step something to offer.
* ``test_planted_secret_appears_nowhere_in_the_scan_response`` /
  ``test_planted_secret_never_reaches_the_home_through_the_route`` — the import completes
  without any secret appearing, over the wire OR on disk.
* ``test_reentry_marks_already_imported_items_existing`` /
  ``test_reimport_reports_existing_and_imports_nothing`` — re-entry shows already-imported
  items as ``existing``, and re-running writes nothing new.
* ``test_a_write_failure_is_reported_with_the_secret_redacted`` — a writer that raises is
  a 500 carrying the failure's own (screened) sentence, never a cheerful empty 200. A
  swallowed write is the defect class this endpoint exists to make impossible.
* ``test_an_unknown_source_is_refused_before_anything_is_read`` /
  ``test_an_empty_selection_is_refused_rather_than_importing_nothing`` — the selection
  axes are validated against the closed registries, and "import nothing" is a refusal
  rather than a success with a zero in it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.onboarding_import import ImportCategory
from gideon.interfaces.dashboard.handlers.onboarding_import import (
    register_onboarding_import_routes,
)

SECRET = "sk-ant-api03-PEP5PLANTEDSECRET00000000000000000000000000000AA"


@pytest.fixture
def foreign(tmp_path: Path) -> Path:
    """A fixture ``~/.claude``: instructions and one MCP server, each carrying a secret."""
    root = tmp_path / "foreign" / ".claude"
    root.mkdir(parents=True)
    (root / "CLAUDE.md").write_text(
        f"# House rules\n\n- Always run the linter.\n- The key is {SECRET}.\n",
        encoding="utf-8",
    )
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "weather": {
                        "command": "npx",
                        "args": ["-y", "weather-mcp"],
                        "env": {"WEATHER_API_KEY": SECRET, "REGION": "eu"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def home(tmp_path: Path, foreign: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated home AND isolated foreign roots, with the binding asserted.

    ``GIDEON_HOME`` and the two source env vars are all read live on every call,
    which is what makes them the robust lever here (a ``config_dir`` attribute patch is
    not undoable once a consumer module has bound the name at import). The asserts are
    the point: an env var that failed to take effect would leave this suite scanning the
    developer's real machine while still passing.
    """
    from gideon.cognition.onboarding_import.sources import claude_code, codex
    from gideon.core.config.loader import config_dir

    h = tmp_path / "gideon-home"
    h.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(h))
    monkeypatch.setenv("GIDEON_SKIP_SKILL_SEED", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(foreign))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex-here"))

    assert config_dir() == h, "GIDEON_HOME did not bind — the real home is at risk"
    assert claude_code.resolve_root() == foreign, "CLAUDE_CONFIG_DIR did not bind"
    assert codex.resolve_root() == tmp_path / "no-codex-here", "CODEX_HOME did not bind"
    return h


@pytest.fixture
def make_client(home: Path):
    """A factory for a TestClient over an app carrying ONLY these two routes.

    A factory rather than a ready client because ``TestClient`` binds a cookie jar to
    the RUNNING loop, which does not exist yet while a sync fixture is being built.
    Every test therefore opens it inside its own ``async with``, the house pattern.
    """

    def _make() -> TestClient:
        app = web.Application()
        register_onboarding_import_routes(app)
        return TestClient(TestServer(app))

    return _make


def _bytes_under(root: Path) -> bytes:
    """Every byte of every file under ``root``, concatenated. For secret sweeps."""
    blob = b""
    for path in sorted(root.rglob("*")):
        if path.is_file():
            blob += path.read_bytes()
    return blob


@pytest.mark.asyncio
async def test_fresh_home_scan_shows_the_source_with_nothing_already_imported(
    make_client, home
):
    async with make_client() as client:
        resp = await client.get("/api/onboarding/import")
        assert resp.status == 200
        body = await resp.json()

    by_name = {s["source"]: s for s in body["sources"]}
    claude = by_name["claude_code"]
    assert claude["detected"] is True
    assert claude["present"] is True
    assert claude["counts"]["instructions"] >= 1
    assert claude["counts"]["mcp_servers"] >= 1
    assert claude["items"], "the step would have nothing to render"
    assert [i["existing"] for i in claude["items"]] == [False] * len(claude["items"])
    assert by_name["codex"]["present"] is False
    assert by_name["codex"]["detected"] is False
    assert body["categories"] == [c.value for c in ImportCategory]


@pytest.mark.asyncio
async def test_planted_secret_appears_nowhere_in_the_scan_response(make_client):
    async with make_client() as client:
        resp = await client.get("/api/onboarding/import")
        raw = await resp.text()
    assert SECRET not in raw
    assert "CLAUDE.md" in raw or "instructions" in raw


@pytest.mark.asyncio
async def test_the_scan_writes_nothing_to_the_home(make_client, home):
    before = sorted(p.name for p in home.rglob("*"))
    async with make_client() as client:
        assert (await client.get("/api/onboarding/import")).status == 200
    assert sorted(p.name for p in home.rglob("*")) == before


async def _import(client, **body):
    resp = await client.post("/api/onboarding/import", json=body)
    return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_import_writes_the_picked_categories_and_reports_every_outcome(
    make_client, home
):
    async with make_client() as client:
        status, report = await _import(
            client, sources=["claude_code"], categories=["instructions", "mcp_servers"]
        )
    assert status == 200
    assert report["counts"]["imported"] >= 2, report
    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert "weather" in mcp["mcpServers"]
    assert all(
        r["destination"] for r in report["results"] if r["outcome"] == "imported"
    )


@pytest.mark.asyncio
async def test_planted_secret_never_reaches_the_home_through_the_route(
    make_client, home
):
    async with make_client() as client:
        status, report = await _import(client)
    assert status == 200
    assert report["counts"]["imported"] >= 1
    assert SECRET.encode() not in _bytes_under(home)
    assert report["secrets_skipped"] + report["redactions"] >= 1
    assert all(SECRET not in note for note in report["notes"])


@pytest.mark.asyncio
async def test_reentry_marks_already_imported_items_existing(make_client):
    """The atom's re-entry clause, over the wire: import, then scan again."""
    async with make_client() as client:
        status, _ = await _import(
            client, sources=["claude_code"], categories=["mcp_servers"]
        )
        assert status == 200
        again = await (await client.get("/api/onboarding/import")).json()

    claude = next(s for s in again["sources"] if s["source"] == "claude_code")
    mcp_items = [i for i in claude["items"] if i["category"] == "mcp_servers"]
    other = [i for i in claude["items"] if i["category"] != "mcp_servers"]
    assert mcp_items and all(i["existing"] for i in mcp_items)
    assert other and not any(i["existing"] for i in other)


@pytest.mark.asyncio
async def test_reimport_reports_existing_and_imports_nothing(make_client):
    async with make_client() as client:
        first = (
            await _import(client, sources=["claude_code"], categories=["mcp_servers"])
        )[1]
        second = (
            await _import(client, sources=["claude_code"], categories=["mcp_servers"])
        )[1]
    assert first["counts"]["imported"] >= 1
    assert second["counts"]["imported"] == 0
    assert second["counts"]["existing"] == first["counts"]["imported"]


@pytest.mark.asyncio
async def test_a_write_failure_is_reported_with_the_secret_redacted(
    make_client, monkeypatch
):
    """A writer that raises must not become a 200 with a zero in it.

    The exception carries the planted secret on purpose: a path or a value from a
    foreign root can itself look like a credential, so the sentence a user reads is
    screened at the one boundary where an exception becomes a string.
    """

    def boom(*_a, **_kw):
        raise OSError(f"cannot write /tmp/x?key={SECRET}")

    monkeypatch.setattr("gideon.cognition.onboarding_import.run_import", boom)
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json={})
        assert resp.status == 500
        raw = await resp.text()
        body = json.loads(raw)

    assert body["error"]["code"] == "onboarding_import_failed"
    assert (
        "cannot write" in body["error"]["message"]
    ), "the failure's own words are the point"
    assert SECRET not in raw
    assert "again" in body["error"]["message"]


@pytest.mark.asyncio
async def test_a_scan_failure_is_reported_rather_than_rendering_an_empty_step(
    make_client, monkeypatch
):
    def boom(*_a, **_kw):
        raise OSError("the foreign root is unreadable")

    monkeypatch.setattr("gideon.cognition.onboarding_import.scan_all", boom)
    async with make_client() as client:
        resp = await client.get("/api/onboarding/import")
        body = await resp.json()
    assert resp.status == 500
    assert body["error"]["code"] == "onboarding_import_failed"
    assert "unreadable" in body["error"]["message"]


@pytest.mark.asyncio
async def test_an_unknown_source_is_refused_before_anything_is_read(make_client, home):
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json={"sources": ["nope"]})
        body = await resp.json()
    assert resp.status == 400
    assert "nope" in body["error"]["message"]
    assert "claude_code" in body["error"]["message"]
    assert not (home / "mcp.json").exists(), "a refused request must write nothing"


@pytest.mark.asyncio
async def test_an_unknown_category_is_refused(make_client):
    async with make_client() as client:
        resp = await client.post(
            "/api/onboarding/import", json={"categories": ["passwords"]}
        )
        body = await resp.json()
    assert resp.status == 400
    assert "passwords" in body["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"sources": []}, {"categories": []}])
async def test_an_empty_selection_is_refused_rather_than_importing_nothing(
    make_client, body
):
    """An empty list is a request for no work; answering `0 imported` would look like
    a successful import that simply found nothing."""
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json=body)
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,code",
    [({"sources": "claude_code"}, "bad_request"), ([], "invalid_body")],
)
async def test_a_malformed_body_is_a_400_not_a_500(make_client, body, code):
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json=body)
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == code


@pytest.mark.asyncio
async def test_unparseable_json_is_a_400(make_client):
    async with make_client() as client:
        resp = await client.post(
            "/api/onboarding/import",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == "invalid_json"


def test_both_routes_resolve_on_a_registered_app():
    app = web.Application()
    register_onboarding_import_routes(app)
    assert {(r.method, r.resource.canonical) for r in app.router.routes()} >= {
        ("GET", "/api/onboarding/import"),
        ("POST", "/api/onboarding/import"),
    }


def _registrars_called_by_the_gateway() -> set[str]:
    """Every ``register_*(app)`` call the gateway's app builder makes, from the AST.

    Static, because booting ``start_dashboard`` has heavy security-critical startup
    side effects (extension load, binding migration) — the same reason
    ``test_api_manifest_drift`` audits routes from the AST rather than a live table.
    A registrar that is defined but never CALLED is a route that exists in a module and
    on no running server, which is the failure this guard exists for.
    """
    import gideon.interfaces.dashboard.server as server_mod

    tree = ast.parse(Path(server_mod.__file__).read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("register_")
    }


def test_the_gateway_builder_mounts_the_import_routes():
    called = _registrars_called_by_the_gateway()
    assert "register_pack_routes" in called
    assert "register_nothing_at_all_routes" not in called
    assert "register_onboarding_import_routes" in called
