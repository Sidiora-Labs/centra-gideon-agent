"""Font serving (gideon-backlog req 27): stated media types, no escape, real 404s.

``/fonts`` used to be a plain static mount, which fails three ways at once:

* the media type was guessed, and none of the web-font extensions are in Python's
  built-in ``mimetypes`` table — they arrive only from an ``/etc/mime.types`` that a
  stock container or a mac does not ship, so the fonts went out as
  ``application/octet-stream`` on exactly the machines nobody develops on;
* a missing font raised, and ``spa_fallback`` turned the raise into index.html — a
  browser handed HTML for a font just silently falls back to a system face;
* nothing was asserted about where the served path could point.

Everything here runs through the real route (``mount_dist_static``) over a real dist
tree, not against the handler in isolation.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import pytest
from aiohttp import web, web_fileresponse
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

import gideon.interfaces.dashboard.server as server_mod
from gideon.interfaces.dashboard import handlers
from gideon.interfaces.dashboard.handlers import core as core_handlers


async def _raw_get(server: TestServer, path: str) -> tuple[int, str]:
    """A GET whose request line is sent verbatim.

    A dot-segment path never survives the HTTP client — yarl normalises
    ``/fonts/../sw.js`` to ``/sw.js`` before it reaches the wire — so testing the
    server's own handling of one means writing the request line by hand.
    """
    reader, writer = await asyncio.open_connection(server.host, server.port)
    writer.write(
        f"GET {path} HTTP/1.1\r\nHost: {server.host}\r\n"
        "Connection: close\r\n\r\n".encode()
    )
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, _, body = raw.decode("utf-8", "replace").partition("\r\n\r\n")
    return int(head.split(" ", 2)[1]), body


FORMATS = [
    ("regular.woff2", b"wOF2", "font/woff2"),
    ("regular.woff", b"wOFF", "font/woff"),
    ("regular.ttf", b"\x00\x01\x00\x00", "font/ttf"),
    ("regular.otf", b"OTTO", "font/otf"),
    ("regular.eot", b"\x00\x00\x00\x00", "application/vnd.ms-fontobject"),
]

SECRET = "TOP-SECRET-NOT-A-FONT"


def _build_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "fonts").mkdir(parents=True)
    for name, blob, _ctype in FORMATS:
        (dist / "fonts" / name).write_bytes(blob)
    (dist / "fonts" / "notes.txt").write_text(SECRET, encoding="utf-8")
    (dist / "index.html").write_text(
        "<!doctype html><title>Gideon</title>", encoding="utf-8"
    )
    (dist / "sw.js").write_text(SECRET, encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.woff2").write_text(SECRET, encoding="utf-8")
    return dist


@pytest.fixture()
def client_factory(tmp_path, monkeypatch):
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
        return TestClient(TestServer(app))

    return _make


class TestFontMediaTypes:
    """Every supported format states its type."""

    @pytest.mark.parametrize(
        ("name", "ctype"), [(n, c) for n, _blob, c in FORMATS], ids=lambda v: str(v)
    )
    async def test_each_format_declares_its_media_type(
        self, client_factory, name, ctype
    ):
        async with client_factory() as client:
            resp = await client.get(f"/fonts/{name}")
            assert resp.status == 200
            assert resp.headers["Content-Type"] == ctype
            assert await resp.read()

    async def test_the_type_is_stated_not_guessed_from_the_platform(
        self, client_factory, monkeypatch
    ):
        """With the platform table emptied — the stock-container case — a guessed type
        would be ``application/octet-stream``. The stated one must survive it."""
        empty = mimetypes.MimeTypes(filenames=())
        assert empty.guess_type("x.woff2")[0] is None
        monkeypatch.setattr(web_fileresponse, "CONTENT_TYPES", empty)
        async with client_factory() as client:
            resp = await client.get("/fonts/regular.woff2")
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "font/woff2"

    async def test_a_non_font_file_in_the_tree_is_not_served(self, client_factory):
        """The route serves fonts; an extension the table does not name is not one,
        and guessing a type for it is the behaviour being removed."""
        async with client_factory() as client:
            resp = await client.get("/fonts/notes.txt")
            assert resp.status == 404
            assert SECRET not in await resp.text()


class TestTraversalRejected:
    """Nothing outside the resolved fonts dir is reachable through ``/fonts``."""

    async def test_encoded_dot_dot_is_rejected(self, client_factory):
        async with client_factory() as client:
            resp = await client.get(
                URL("/fonts/..%2F..%2Foutside%2Fsecret.woff2", encoded=True)
            )
            assert resp.status == 404
            assert SECRET not in await resp.text()

    async def test_a_raw_dot_dot_request_line_is_rejected(self, client_factory):
        """Sent verbatim, past any client-side normalisation. ``/sw.js`` is a real
        route on this app holding the marker, so an escape would answer 200 with it."""
        async with client_factory() as client:
            status, body = await _raw_get(client.server, "/fonts/../sw.js")
            assert status == 404
            assert SECRET not in body

    async def test_an_absolute_tail_is_rejected(self, client_factory, tmp_path):
        """``Path(root) / "/abs/path"`` is ``/abs/path`` — the join silently discards
        the root, so the containment check has to catch it."""
        absolute = (tmp_path / "outside" / "secret.woff2").as_posix()
        async with client_factory() as client:
            resp = await client.get(URL(f"/fonts/{absolute}", encoded=True))
            assert resp.status == 404
            assert SECRET not in await resp.text()

    async def test_a_symlink_out_of_the_tree_is_rejected(
        self, client_factory, tmp_path
    ):
        link = tmp_path / "dist" / "fonts" / "escape.woff2"
        link.symlink_to(tmp_path / "outside" / "secret.woff2")
        async with client_factory() as client:
            resp = await client.get("/fonts/escape.woff2")
            assert resp.status == 404
            assert SECRET not in await resp.text()


class TestMissingFont:
    """A font that is not there says so."""

    async def test_missing_font_is_a_real_404_not_application_html(
        self, client_factory
    ):
        async with client_factory() as client:
            resp = await client.get("/fonts/nope.woff2")
            assert resp.status == 404
            assert resp.content_type == "text/plain"
            body = await resp.text()
            assert "<!doctype html" not in body.lower()

    async def test_the_fonts_dir_itself_is_not_listed(self, client_factory):
        """No listing, and no second URL for the same bytes: ``Path`` drops a trailing
        slash, so ``/fonts/regular.woff2/`` would otherwise serve the font too."""
        async with client_factory() as client:
            for path in ("/fonts/", "/fonts/regular.woff2/"):
                resp = await client.get(path)
                assert resp.status == 404, path

    async def test_a_missing_fonts_dir_still_404s(self, tmp_path, monkeypatch):
        """No build at all: still a 404, never the unbundled HTML page."""
        dist = tmp_path / "empty-dist"
        dist.mkdir()
        monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
        monkeypatch.setattr(core_handlers, "_DIST_DIR", dist)
        app = web.Application(
            middlewares=[server_mod.cache_headers_middleware, server_mod.spa_fallback]
        )
        server_mod.mount_dist_static(app)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/fonts/regular.woff2")
            assert resp.status == 404
            assert resp.content_type == "text/plain"


async def test_fonts_serve_through_the_dev_dist_symlink(tmp_path, monkeypatch):
    """In dev ``static/dist`` IS a symlink into ``apps/console/dist``; a containment
    check that resolved only the target would reject every real font there."""
    real = tmp_path / "real-dist"
    (real / "fonts").mkdir(parents=True)
    (real / "fonts" / "dm-sans.woff2").write_bytes(b"wOF2")
    link = tmp_path / "linked-dist"
    link.symlink_to(real)
    monkeypatch.setattr(server_mod, "_DIST_DIR", link)
    monkeypatch.setattr(core_handlers, "_DIST_DIR", link)

    app = web.Application(
        middlewares=[server_mod.cache_headers_middleware, server_mod.spa_fallback]
    )
    server_mod.mount_dist_static(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/fonts/dm-sans.woff2")
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "font/woff2"


def test_fonts_are_excluded_from_the_spa_fallback():
    """Belt to the handler's braces: even a raise from under ``/fonts/`` must not be
    answered with the application's HTML."""
    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert '("/assets/", "/fonts/", "/icons/", "/sprites/", "/vendor/")' in source
