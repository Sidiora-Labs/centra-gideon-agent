"""Tests for the Files path-completion endpoint (Files P4)."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.interfaces.dashboard.handlers import files as F


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alps").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "afile.txt").write_text("x")
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("R", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw: str(raw) if str(raw).startswith(str(tmp_path)) else None,
    )
    return tmp_path


def _call(path: str, kind: str = "", limit: int | None = None):
    qs = urlencode(
        {
            key: value
            for key, value in {
                "path": path,
                "kind": kind,
                "limit": limit,
            }.items()
            if value is not None
        }
    )
    req = make_mocked_request("GET", f"/api/file-complete?{qs}")
    resp = asyncio.run(F.api_file_complete(req))
    return resp.status, json.loads(resp.body.decode())


def test_completes_prefix(root):
    status, body = _call(f"{root}/al")
    names = {s["name"] for s in body["suggestions"]}
    assert status == 200
    assert "alpha" in names and "alps" in names
    assert "beta" not in names


def test_kind_dir_excludes_files(root):
    _, body = _call(f"{root}/a", "dir")
    names = {s["name"] for s in body["suggestions"]}
    assert "afile.txt" not in names
    assert "alpha" in names


def test_trailing_slash_lists_all_children(root):
    _, body = _call(f"{root}/")
    names = {s["name"] for s in body["suggestions"]}
    assert {"alpha", "alps", "beta"} <= names


def test_outside_roots_returns_empty(root, monkeypatch):
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: None)
    status, body = _call("/etc/")
    assert status == 200
    assert body["suggestions"] == []
    assert body["truncated"] is False


def test_sorts_all_candidates_before_allowlist_and_limit(root, monkeypatch):
    for name in ("aardvark", "able", "azure"):
        (root / name).mkdir()

    original_validate = F._validate_dashboard_path

    def validate(raw):
        if str(raw).endswith("aardvark"):
            return None
        return original_validate(raw)

    monkeypatch.setattr(F, "_validate_dashboard_path", validate)
    _, body = _call(f"{root}/a", "dir", 2)

    assert [item["name"] for item in body["suggestions"]] == ["able", "alpha"]
    assert body["truncated"] is True


def test_default_limit_is_30(root):
    for index in range(35):
        (root / f"item-{index:02}").mkdir()

    _, body = _call(f"{root}/item-", "dir")

    assert len(body["suggestions"]) == 30
    assert body["truncated"] is True


def test_screenshot_unavailable_off_macos(monkeypatch):
    """Non-macOS hosts have no `screencapture` — degrade with a clear 400, never
    spawn a subprocess. This is the server half of the FE `useIsMac` gate."""
    monkeypatch.setattr(F.sys, "platform", "linux")

    def _boom(*a, **k):
        raise AssertionError("screencapture must not be spawned off macOS")

    monkeypatch.setattr(F.asyncio, "create_subprocess_exec", _boom)
    req = make_mocked_request("POST", "/api/screenshot")
    resp = asyncio.run(F.api_screenshot(req))
    assert resp.status == 400
    assert "macOS" in json.loads(resp.body.decode())["error"]


def test_screenshot_cancel_returns_empty_path(monkeypatch):
    """User cancels the region select (Esc) → `screencapture` writes no file →
    endpoint returns an empty path (NOT an error) so the FE no-ops cleanly."""
    monkeypatch.setattr(F.sys, "platform", "darwin")

    class _Proc:
        returncode = 0

        async def wait(self):
            return 0

    async def _fake_exec(*a, **k):
        return _Proc()

    monkeypatch.setattr(F.asyncio, "create_subprocess_exec", _fake_exec)
    req = make_mocked_request("POST", "/api/screenshot")
    resp = asyncio.run(F.api_screenshot(req))
    assert resp.status == 200
    assert json.loads(resp.body.decode())["path"] == ""
