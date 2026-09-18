"""Cache classes for the served frontend (gideon-backlog req 17).

Two classes and only two, asserted against the REAL router and the REAL middleware
(``mount_dist_static`` + ``cache_headers_middleware``, both module level precisely so
a test can reach them — ``start_dashboard`` cannot be invoked in one):

* a content-hashed bundle under ``/assets/`` is immutable for a year, because its URL
  changes when its bytes change;
* everything else — the SPA entry, the SPA fallback, ``/api``, ``sw.js``, ``/icons``,
  ``/vendor``, ``/fonts``, and a stable name that happens to sit in ``/assets`` —
  stays no-store, because those names are reused and a stale copy of one is a wrong
  app that outlives the fix.

The asymmetry is the point: a stable file wrongly marked immutable is stuck in
browser caches for a year with no recall, so the doubt has to fall toward no-store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import gideon.interfaces.dashboard.server as server_mod
from gideon.interfaces.dashboard import handlers
from gideon.interfaces.dashboard.handlers import core as core_handlers

NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"
IMMUTABLE = "public, max-age=31536000, immutable"

HASHED_JS = "index-DR9ii6_w.js"
HASHED_CSS = "style-C4Kq8_Aa.css"


def _build_dist(tmp_path: Path) -> Path:
    """A miniature of a real ``npm run build`` tree: hashed bundles in ``assets/``,
    stable names everywhere else (and one stable name inside ``assets/``)."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / HASHED_JS).write_text("export const a = 1\n", encoding="utf-8")
    (dist / "assets" / HASHED_CSS).write_text(":root{--x:1}\n", encoding="utf-8")
    (dist / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (dist / "icons").mkdir()
    (dist / "icons" / "icon-192.png").write_bytes(b"\x89PNG\r\n")
    (dist / "vendor").mkdir()
    (dist / "vendor" / "react-DEADBEEF.js").write_text("//shim\n", encoding="utf-8")
    (dist / "fonts").mkdir()
    (dist / "fonts" / "dm-sans.woff2").write_bytes(b"wOF2")
    (dist / "index.html").write_text(
        "<!doctype html><title>Gideon</title>", encoding="utf-8"
    )
    (dist / "sw.js").write_text("//worker\n", encoding="utf-8")
    return dist


async def _api_ping(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@pytest.fixture()
def client_factory(tmp_path, monkeypatch):
    """The real static mounts + the real cache middleware, over a real build tree."""
    dist = _build_dist(tmp_path)
    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(core_handlers, "_DIST_DIR", dist)

    def _make() -> TestClient:
        app = web.Application(
            middlewares=[server_mod.cache_headers_middleware, server_mod.spa_fallback]
        )
        server_mod.mount_dist_static(app)
        app.router.add_get("/", handlers.index)
        app.router.add_get("/sw.js", handlers.service_worker)
        app.router.add_get("/api/ping", _api_ping)
        return TestClient(TestServer(app))

    return _make


class TestImmutableBundles:
    """Content-hashed build output — the only class allowed a long life."""

    @pytest.mark.parametrize("name", [HASHED_JS, HASHED_CSS])
    async def test_hashed_bundle_is_immutable_for_a_year(self, client_factory, name):
        async with client_factory() as client:
            resp = await client.get(f"/assets/{name}")
            assert resp.status == 200
            assert resp.headers["Cache-Control"] == IMMUTABLE

    async def test_immutable_response_carries_no_contradicting_expiry(
        self, client_factory
    ):
        """``Expires: 0``/``Pragma: no-cache`` beside a year of immutability is a
        contradiction an intermediary may resolve either way — so they ride with the
        no-store class only."""
        async with client_factory() as client:
            resp = await client.get(f"/assets/{HASHED_JS}")
            assert "Pragma" not in resp.headers
            assert "Expires" not in resp.headers
            assert "Content-Security-Policy" in resp.headers


class TestNoStoreRemains:
    """Every stable name, entry point and API keeps no-store."""

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/api/ping",
            "/assets/logo.svg",
            "/icons/icon-192.png",
            "/vendor/react-DEADBEEF.js",
            "/fonts/dm-sans.woff2",
            "/sw.js",
        ],
    )
    async def test_stable_path_is_never_cached(self, client_factory, path):
        async with client_factory() as client:
            resp = await client.get(path)
            assert resp.status == 200, path
            assert resp.headers["Cache-Control"] == NO_STORE, path
            assert resp.headers["Pragma"] == "no-cache", path
            assert resp.headers["Expires"] == "0", path

    async def test_spa_entry_html_is_never_cached(self, client_factory):
        """index.html keeps the same URL across every deploy: cached, it pins a build
        whose hashed bundles have already been replaced."""
        async with client_factory() as client:
            resp = await client.get("/")
            assert resp.status == 200
            assert resp.content_type == "text/html"
            assert resp.headers["Cache-Control"] == NO_STORE

    async def test_spa_fallback_route_is_never_cached(self, client_factory):
        """A deep client route 404s into ``spa_fallback`` and comes back as the entry
        HTML — same document, so the same no-store rule has to reach it."""
        async with client_factory() as client:
            resp = await client.get("/sessions/some-deep-client-route")
            assert resp.status == 200
            assert resp.content_type == "text/html"
            assert resp.headers["Cache-Control"] == NO_STORE

    async def test_api_response_is_never_cached(self, client_factory):
        async with client_factory() as client:
            resp = await client.get("/api/ping")
            assert await resp.json() == {"ok": True}
            assert resp.headers["Cache-Control"] == NO_STORE

    async def test_a_hashed_name_outside_assets_is_still_no_store(self, client_factory):
        """``/vendor`` holds hand-pinned shims under names the build does not rotate,
        so a hash-shaped name there proves nothing — the prefix gates the class."""
        async with client_factory() as client:
            resp = await client.get("/vendor/react-DEADBEEF.js")
            assert resp.headers["Cache-Control"] == NO_STORE


class TestHashedNameRecognition:
    """The predicate behind the class, at the unit the router cannot reach."""

    @pytest.mark.parametrize(
        "path",
        [
            "/assets/index-DR9ii6_w.js",
            "/assets/style-C4Kq8_Aa.css",
            "/assets/vendor-react-B7xq2Ld0.js",
        ],
    )
    def test_vite_hashed_output_is_immutable(self, path):
        assert server_mod._is_immutable_asset(path)

    @pytest.mark.parametrize(
        "path",
        [
            "/assets/index.html",
            "/assets/logo.svg",
            "/assets/something-important.js",
            "/assets/my-app-Widget.css",
            "/fonts/dm-sans.woff2",
            "/icons/gideon-light-32.png",
            "/vendor/react-DEADBEEF.js",
            "/sw.js",
            "/manifest.webmanifest",
            "/api/status",
            "/",
        ],
    )
    def test_everything_else_is_not(self, path):
        assert not server_mod._is_immutable_asset(path)

    def test_the_hash_alphabet_matches_rollup(self):
        """Rollup draws its hash from ``0-9A-Za-z_$`` and never ``-``, which is what
        lets the last ``-``-delimited run be read as the hash."""
        assert server_mod._is_immutable_asset("/assets/a-Dc9_$x1Q.js")
        assert not server_mod._is_immutable_asset("/assets/a-Dc9.x1Q.js")


def test_start_dashboard_uses_the_module_level_seams():
    """The extracted mount and middleware are what the gateway actually installs —
    ``start_dashboard`` cannot be booted in a unit test, so this is the rail that
    keeps the tested code path and the shipped one the same one."""
    import inspect

    source = inspect.getsource(server_mod.start_dashboard)
    assert "mount_dist_static(app)" in source
    assert "cache_headers_middleware," in source
    assert "no_cache_middleware" not in source
