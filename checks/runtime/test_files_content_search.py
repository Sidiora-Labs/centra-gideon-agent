"""Tests for the Files content-search endpoint (Files P3).

Exercises the Python fallback path directly (deterministic, no ripgrep
dependency) plus the HTTP handler's validation + engine reporting.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import make_mocked_request

from gideon.interfaces.dashboard.handlers import files as F


@pytest.fixture
def search_root(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("import os\nNEEDLE_here = 1\n")
    (tmp_path / "b.txt").write_text("nothing relevant\nNEEDLE_here too\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "c.py").write_text("NEEDLE_here ignored\n")
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Root", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw: raw if str(raw).startswith(str(tmp_path)) else None,
    )
    monkeypatch.setattr(F, "_sel", lambda: MagicMock())
    return tmp_path


def test_python_search_finds_matches(search_root):
    results, truncated = F._content_search_python(str(search_root), "needle_here", "")
    files = {r["file"].split("/")[-1] for r in results}
    assert "a.py" in files and "b.txt" in files
    assert not truncated


def test_python_search_skips_ignored_dirs(search_root):
    results, _ = F._content_search_python(str(search_root), "needle_here", "")
    assert all("node_modules" not in r["file"] for r in results)


def test_python_search_glob_filter(search_root):
    results, _ = F._content_search_python(str(search_root), "needle_here", "*.py")
    assert {r["file"].split("/")[-1] for r in results} == {"a.py"}


def test_python_search_reports_line_and_col(search_root):
    results, _ = F._content_search_python(str(search_root), "needle_here", "*.py")
    r = results[0]
    assert r["line"] == 2 and r["col"] >= 1


def _call(
    path: str, q: str = "", include: str = "", *, force_python=True, monkeypatch=None
):
    from urllib.parse import urlencode

    if force_python and monkeypatch is not None:
        monkeypatch.setattr(F, "_has_rg", lambda: False)
    qs = urlencode({"path": path, "q": q, "include": include})
    req = make_mocked_request("GET", f"/api/file-content-search?{qs}")
    resp = asyncio.run(F.api_file_content_search(req))
    return resp.status, json.loads(resp.body.decode())


def test_handler_returns_results(search_root, monkeypatch):
    status, body = _call(str(search_root), "needle_here", monkeypatch=monkeypatch)
    assert status == 200
    assert body["engine"] == "python"
    assert len(body["results"]) >= 2


def test_handler_empty_query_returns_empty(search_root, monkeypatch):
    status, body = _call(str(search_root), "", monkeypatch=monkeypatch)
    assert status == 200
    assert body["results"] == []


def test_handler_invalid_dir_400(monkeypatch):
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: None)
    status, _ = _call("/etc", "x", monkeypatch=monkeypatch)
    assert status == 400


def test_handler_redacts_secrets_in_preview(tmp_path, monkeypatch):
    (tmp_path / "leak.txt").write_text(
        "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLEKEY1234567890abcd needle\n"
    )
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("R", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw: raw if str(raw).startswith(str(tmp_path)) else None,
    )
    monkeypatch.setattr(F, "_sel", lambda: MagicMock())
    status, body = _call(str(tmp_path), "needle", monkeypatch=monkeypatch)
    assert status == 200
    assert all(
        "AKIAIOSFODNN7EXAMPLEKEY1234567890abcd" not in r["preview"]
        for r in body["results"]
    )


def _hung_rg(tmp_path, monkeypatch):
    """Put an ``rg`` on PATH that never answers AND forks a grandchild.

    Same shape as ``test_files_git_status``'s ``_slow_git`` and for the same reason: the
    grandchild inherits the stdout pipe, so a pid-only kill leaves the reap waiting on the
    pipe rather than on the child's exit — the deadline that is not a deadline.
    """
    fake_bin = tmp_path / "rgbin"
    fake_bin.mkdir()
    stub = fake_bin / "rg"
    stub.write_text("#!/bin/sh\n/bin/sleep 8 & wait\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    return fake_bin


def test_a_hung_content_search_is_bounded_and_leaves_nothing_running(
    search_root, monkeypatch
):
    """req 90 ac_3 — the content search's deadline kills AND reaps, group and all.

    The ripgrep path had a timeout and a reap but no session of its own, so the reap fell
    back to a single-pid signal — the exact half of #432 the git runner was fixed for. This
    drives a real hung child with a real grandchild: the call must return AT the deadline,
    answer "no results" rather than raising, and leave the process group empty.
    """
    import time

    from test_files_git_status import _group_is_empty, _spy_spawn

    _hung_rg(search_root, monkeypatch)
    monkeypatch.setattr(F, "_CONTENT_SEARCH_TIMEOUT", 0.3)

    async def run():
        seen = _spy_spawn(monkeypatch)
        started = time.monotonic()
        results, truncated = await F._content_search_rg(str(search_root), "needle", "")
        elapsed = time.monotonic() - started

        assert (results, truncated) == ([], False)
        assert seen["procs"], "rg was never spawned — the test proves nothing"
        assert elapsed >= 0.3, (
            f"the stub rg returned in {elapsed:.2f}s, inside the injected deadline — no "
            "timeout was induced"
        )
        assert elapsed < 4.0, (
            f"the 0.3s deadline took {elapsed:.2f}s to return — the post-kill reap waited "
            "for the grandchild's inherited pipe instead of the child's exit"
        )
        proc = seen["procs"][0]
        assert (
            proc.returncode is not None
        ), "the killed search was never reaped (zombie)"
        assert seen["kwargs"].get("start_new_session") is True, (
            "the search child no longer leads its own session, so kill_timed_out falls "
            "back to a single-pid signal and the grandchild survives holding the pipe"
        )
        assert _group_is_empty(proc.pid), (
            f"process group {proc.pid} still has members after the timeout — one leaked "
            "pair per timed-out search"
        )

    asyncio.run(run())


def test_the_endpoint_answers_200_when_the_search_times_out(search_root, monkeypatch):
    """req 90 ac_3 at the door: a blown deadline is an empty result set, not a 500.

    The handler reports the engine it used either way, so the caller can tell "ripgrep found
    nothing" from "we fell back to Python" — which is the only signal it gets when a search
    is cut short.
    """
    _hung_rg(search_root, monkeypatch)
    monkeypatch.setattr(F, "_CONTENT_SEARCH_TIMEOUT", 0.3)
    monkeypatch.setattr(F, "_has_rg", lambda: True)

    status, body = _call(str(search_root), "needle_here", force_python=False)
    assert status == 200
    assert body == {"results": [], "engine": "rg", "truncated": False}
