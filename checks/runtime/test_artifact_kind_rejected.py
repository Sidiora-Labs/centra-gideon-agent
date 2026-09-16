"""#633 — an unknown artifact kind is refused at create, never coerced to `widget`.

`normalize_kind` used to fall back to "widget" — the sandboxed-EXECUTION kind — so
`kind="markdwon"` (typo) or `kind="md"` was stored as executable widget payload
instead of prose, and silently lost the comment layer (sandboxed kinds are not
commentable). The binary sibling (`create_binary`) already raises on this exact
shape; these rails pin the text path to the same posture, at both the provider and
the route, plus the acceptance cases that keep the guard honest (a guard that
refuses everything would pass a refusal-only suite).
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.workspace.artifacts import registry
from gideon.workspace.artifacts.handlers import register_artifact_routes
from gideon.workspace.artifacts.models import ALLOWED_KINDS, normalize_kind
from gideon.workspace.artifacts.native import NativeArtifactProvider


@pytest.fixture
def prov(tmp_path, monkeypatch):
    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    monkeypatch.setitem(registry._providers, "native", provider)
    return provider


def _make_app() -> web.Application:
    from unittest.mock import MagicMock

    app = web.Application()
    state = MagicMock()
    state.is_restricted_session.return_value = False
    app["state"] = state
    register_artifact_routes(app)
    return app


class TestNormalizeKindStrict:
    def test_unknown_kind_raises_naming_the_allowed_set(self):
        with pytest.raises(ValueError) as ei:
            normalize_kind("markdwon")
        msg = str(ei.value)
        assert "markdwon" in msg
        assert "markdown" in msg and "widget" in msg

    def test_every_allowed_kind_still_passes(self):
        for k in ALLOWED_KINDS:
            assert normalize_kind(k) == k

    def test_case_and_whitespace_still_normalize(self):
        assert normalize_kind("  MarkDown  ") == "markdown"

    def test_absent_kind_keeps_the_documented_widget_default(self):
        assert normalize_kind("") == "widget"
        assert normalize_kind(None) == "widget"  # type: ignore[arg-type]


class TestProviderCreateRefusesUnknownKind:
    def test_typoed_kind_is_refused_not_stored_as_widget(self, prov):
        with pytest.raises(ValueError, match="markdwon"):
            prov.create(name="notes", content="# hi", kind="markdwon", source="chat")
        assert all(a.name != "notes" for a in prov.list())

    def test_known_kind_still_creates(self, prov):
        art = prov.create(name="notes", content="# hi", kind="markdown", source="chat")
        assert art.kind == "markdown"


class TestCreateRouteRefusesUnknownKind:
    @pytest.mark.asyncio
    async def test_post_with_unknown_kind_is_a_400_naming_the_allowed_set(self, prov):
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.post(
                "/api/artifacts", json={"name": "doc", "content": "x", "kind": "md"}
            )
            assert resp.status == 400
            body = await resp.json()
            assert "md" in body["error"] and "markdown" in body["error"]
        assert all(a.name != "doc" for a in prov.list())

    @pytest.mark.asyncio
    async def test_post_with_known_kind_still_201s(self, prov):
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.post(
                "/api/artifacts",
                json={"name": "doc", "content": "x", "kind": "markdown"},
            )
            assert resp.status == 201
            assert (await resp.json())["kind"] == "markdown"

    @pytest.mark.asyncio
    async def test_post_with_no_kind_keeps_the_widget_default(self, prov):
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.post(
                "/api/artifacts", json={"name": "w", "content": "<b>x</b>"}
            )
            assert resp.status == 201
            assert (await resp.json())["kind"] == "widget"
