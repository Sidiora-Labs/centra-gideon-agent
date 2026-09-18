"""Install-kind detection + the shared self-update primitives (contracts C1/C2).

Four fixtures — one per InstallKind — pin the resolution order:
env (container/desktop) wins first, then a .git working tree => git, else pip.
Each test isolates the two env vars the classifier reads (monkeypatch.delenv)
so it never inherits the runner's real environment.

The module under test moved out of ``dashboard/handlers/updates_kind.py`` into the
core package in DIST-13, so the CLI can reach the same decision the dashboard makes
without importing an HTTP handler.
"""

from __future__ import annotations

import asyncio
import os
import time

import aiohttp
import pytest

from gideon.operations import self_update as uk
from gideon.operations.self_update import detect_install_kind


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIDEON_INSTALL_KIND", raising=False)
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)


def test_container_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "container"


def test_desktop_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "desktop")
    assert detect_install_kind() == "desktop"


def test_env_kind_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "  Container ")
    assert detect_install_kind() == "container"


def test_unknown_env_kind_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "banana")
    assert detect_install_kind() == "pip"


def test_git_when_project_dir_has_dot_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_worktree_dot_git_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_when_dot_git_in_monorepo_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "Gideon"
    nested.mkdir()
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(nested))
    assert detect_install_kind() == "git"


def test_pip_when_no_env_no_git(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "pip"


def test_pip_when_nothing_set() -> None:
    assert detect_install_kind() == "pip"


def test_install_kind_literal_values() -> None:
    assert uk._ENV_KINDS == {"container", "desktop"}


def test_normalize_version_strips_leading_v() -> None:
    assert uk.normalize_version("v0.1.3") == "0.1.3"
    assert uk.normalize_version("0.1.3") == "0.1.3"
    assert uk.normalize_version("  v1.2.0 ") == "1.2.0"


def test_version_tuple_orders_numerically() -> None:
    assert uk.version_tuple("v0.2.0") > uk.version_tuple("0.1.9")
    assert uk.version_tuple("0.1.10") > uk.version_tuple("0.1.9")
    assert uk.version_tuple("garbage") == (0,)


def test_cache_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    uk.write_release_cache({"tag": "v0.1.3", "etag": 'W/"abc"'})
    got = uk.read_release_cache()
    assert got["tag"] == "v0.1.3"
    assert got["etag"] == 'W/"abc"'


def test_read_cache_missing_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    assert uk.read_release_cache() == {}


@pytest.mark.asyncio
async def test_build_status_update_available(monkeypatch) -> None:
    async def _fake_release(*, offline: bool = False) -> dict:
        return {"tag": "v0.2.0", "name": "0.2.0", "body": "notes"}

    monkeypatch.setattr(uk, "fetch_latest_release", _fake_release)
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
    status = await uk.build_update_status("0.1.0")
    assert status["kind"] == "container"
    assert status["current"] == "0.1.0"
    assert status["latest"] == "0.2.0"
    assert status["update_available"] is True
    assert status["apply_method"] == "instructions"
    assert status["instructions"]
    assert status["commits_behind"] is None


@pytest.mark.asyncio
async def test_build_status_up_to_date_pip(monkeypatch) -> None:
    async def _fake_release(*, offline: bool = False) -> dict:
        return {"tag": "v0.1.0", "name": "0.1.0", "body": ""}

    monkeypatch.setattr(uk, "fetch_latest_release", _fake_release)
    monkeypatch.delenv("GIDEON_INSTALL_KIND", raising=False)
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)
    status = await uk.build_update_status("0.1.0")
    assert status["kind"] == "pip"
    assert status["update_available"] is False
    assert status["apply_method"] == "pip_upgrade"
    assert status["instructions"] == []


@pytest.mark.asyncio
async def test_build_status_offline_no_tag(monkeypatch) -> None:
    async def _empty_release(*, offline: bool = False) -> dict:
        return {}

    monkeypatch.setattr(uk, "fetch_latest_release", _empty_release)
    monkeypatch.delenv("GIDEON_INSTALL_KIND", raising=False)
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)
    status = await uk.build_update_status("0.1.0")
    assert status["latest"] == ""
    assert status["update_available"] is False


@pytest.mark.asyncio
async def test_fetch_latest_release_offline_returns_cache(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    uk.write_release_cache({"tag": "v0.1.2", "etag": 'W/"x"'})

    class _BoomSession:
        def __init__(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    got = await uk.fetch_latest_release()
    assert got["tag"] == "v0.1.2"


def _orchestrator_stub(applied: list[str]):
    """A bare stand-in for the boot-path caller — `_check_for_updates` touches only these."""

    class _Stub:
        dashboard_state = None

        async def _auto_apply_update(self) -> None:
            applied.append("apply")

    return _Stub()


async def _drive_check_for_updates(monkeypatch, *, auto="off", check_enabled=True):
    """Run `RuntimeCoordinator._check_for_updates` against stubs, recording call order."""
    from gideon.core.config.loader import UpdatesConfig
    from gideon.engine.gateway import RuntimeCoordinator
    from gideon.interfaces.dashboard import handlers as dash_handlers

    order: list[str] = []
    applied: list[str] = []

    async def _fake_check() -> None:
        order.append("check")

    class _Cfg:
        updates = UpdatesConfig(auto=auto, check_enabled=check_enabled)

    def _load(*_a, **_k):
        order.append("config")
        return _Cfg()

    monkeypatch.setattr(dash_handlers, "_do_update_check", _fake_check)
    monkeypatch.setattr(dash_handlers, "_update_info", {"available": True})
    monkeypatch.setattr("gideon.core.config.AppConfig.load", _load)

    await RuntimeCoordinator._check_for_updates(_orchestrator_stub(applied))
    return order, applied


@pytest.mark.asyncio
async def test_the_check_switch_is_read_before_the_check_runs(monkeypatch) -> None:
    """RUM: `updates.check_enabled` is an EGRESS kill switch, so config is consulted
    first and the check never runs when it is off.

    This is the contract `docs/architecture/NETWORK_EGRESS_HOSTS.txt` and
    `test_network_egress_hosts.py` describe for api.github.com: the release check
    is scheduled, and it is opt-out.
    """
    order, applied = await _drive_check_for_updates(monkeypatch, check_enabled=False)
    assert order == ["config"], f"a disabled check still ran something: {order}"
    assert applied == []


@pytest.mark.asyncio
async def test_notify_only_is_the_default_and_gates_the_apply(monkeypatch) -> None:
    """`updates.auto` defaults to "off": the check runs, the apply does not."""
    from gideon.core.config.loader import UpdatesConfig

    assert UpdatesConfig().auto == "off"
    order, applied = await _drive_check_for_updates(monkeypatch, auto="off")
    assert order == ["config", "check"]
    assert applied == []


@pytest.mark.asyncio
async def test_staged_mode_reaches_the_apply(monkeypatch) -> None:
    order, applied = await _drive_check_for_updates(monkeypatch, auto="staged")
    assert order == ["config", "check"]
    assert applied == ["apply"]


@pytest.mark.asyncio
async def test_c2_wire_shape_conformance(monkeypatch) -> None:
    """build_update_status emits exactly the C2 contract keys (+ additive extras),
    with the per-kind apply_method / commits_behind / instructions semantics the
    plan pins. Locks the Tier-S wire shape against silent drift."""

    async def _rel(*, offline: bool = False) -> dict:
        return {"tag": "v0.2.0", "name": "0.2.0", "body": "notes"}

    monkeypatch.setattr(uk, "fetch_latest_release", _rel)
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)

    required = {
        "kind",
        "current",
        "latest",
        "update_available",
        "commits_behind",
        "apply_method",
        "instructions",
    }

    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
    c = await uk.build_update_status("0.1.0")
    assert required <= set(c)
    assert c["apply_method"] == "instructions"
    assert c["commits_behind"] is None
    assert isinstance(c["instructions"], list) and c["instructions"]

    monkeypatch.setenv("GIDEON_INSTALL_KIND", "desktop")
    d = await uk.build_update_status("0.1.0")
    assert d["apply_method"] == "desktop_delegate"

    monkeypatch.delenv("GIDEON_INSTALL_KIND", raising=False)
    p = await uk.build_update_status("0.1.0")
    assert p["apply_method"] == "pip_upgrade"
    assert p["commits_behind"] is None
    assert p["instructions"] == []
    assert p["current"] == "0.1.0"
    assert p["latest"] == "0.2.0"
    assert p["update_available"] is True


class _GitScript:
    """Fake ``_run_git`` driven by a per-subcommand script; records every call."""

    def __init__(self, **replies: tuple[int, str]) -> None:
        self.calls: list[list[str]] = []
        self._replies = replies

    def __call__(self, args, *, cwd, timeout):  # type: ignore[no-untyped-def]
        import subprocess

        self.calls.append(list(args))
        rc, out = self._replies.get(args[0], (1, ""))
        return subprocess.CompletedProcess(["git", *args], rc, out, "")


def test_default_branch_prefers_the_checked_out_branch(monkeypatch) -> None:
    git = _GitScript(**{"rev-parse": (0, "feature-foo\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "feature-foo"
    assert [c[0] for c in git.calls] == ["rev-parse"]


def test_default_branch_detached_head_reads_the_remote_head(monkeypatch) -> None:
    git = _GitScript(
        **{"rev-parse": (0, "HEAD\n"), "symbolic-ref": (0, "origin/main\n")}
    )
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "main"


def test_default_branch_falls_back_to_remote_show(monkeypatch) -> None:
    git = _GitScript(
        **{
            "rev-parse": (0, "\n"),
            "symbolic-ref": (1, ""),
            "remote": (0, "* remote origin\n  HEAD branch: trunk\n  Fetch URL: x\n"),
        }
    )
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "trunk"


def test_default_branch_ignores_an_unknown_remote_head(monkeypatch) -> None:
    git = _GitScript(
        **{
            "rev-parse": (0, "HEAD\n"),
            "symbolic-ref": (1, ""),
            "remote": (0, "  HEAD branch: (unknown)\n"),
        }
    )
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == uk.DEFAULT_BRANCH_FALLBACK


def test_default_branch_last_resort_is_this_repo_s_real_default(monkeypatch) -> None:
    """Every probe fails ⇒ the literal fallback, and it must NAME A REAL BRANCH.

    The CLI hardcoded ``mainline`` — a branch this repository has never had — so a
    detached-HEAD update fetched an unresolvable ref and failed confusingly.
    """
    git = _GitScript()
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "main"
    assert uk.DEFAULT_BRANCH_FALLBACK == "main"


def test_no_module_hardcodes_a_branch_this_repo_does_not_have() -> None:
    """Regression rail for the `mainline` default (DIST-13).

    Cheap and exact: the string must not reappear in either updater surface, in a
    fallback or a comment that a later edit could copy back into code.
    """
    from pathlib import Path

    import gideon

    root = Path(gideon.__file__).parent
    for name in (
        "operations/self_update.py",
        "interfaces/cli/server.py",
        "engine/gateway.py",
    ):
        assert "mainline" not in (root / name).read_text(encoding="utf-8"), name


def test_run_git_reports_a_timeout_as_a_failure_not_an_exception(monkeypatch) -> None:
    """A timeout is an ordinary updater failure: non-zero + a reason, never a raise.

    Callers report `stderr` and stop; making them wrap every probe in try/except is
    how a timeout ends up swallowed instead.
    """
    import subprocess

    def _boom(*a, **k):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", _boom)
    res = uk._run_git(["status"], cwd="/x", timeout=1)
    assert res.returncode == 124
    assert "timed out" in res.stderr


def test_run_git_reports_a_missing_git_binary(monkeypatch) -> None:
    import subprocess

    def _missing(*a, **k):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _missing)
    res = uk._run_git(["status"], cwd="/x", timeout=1)
    assert res.returncode == 127
    assert "cannot run git" in res.stderr


def test_tracked_changes_excludes_untracked_entries(monkeypatch) -> None:
    git = _GitScript(**{"status": (0, " M a.py\n?? scratch.txt\nA  b.py\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.git_tracked_changes("/x") == [" M a.py", "A  b.py"]


def test_upgrade_spec_pins_a_known_tag_and_falls_back_unpinned() -> None:
    assert uk.upgrade_spec("v0.1.4") == "gideon-agent-harness==0.1.4"
    assert uk.upgrade_spec("") == "gideon-agent-harness"


def test_git_root_finds_the_worktree_that_carries_dot_git(tmp_path) -> None:
    nested = tmp_path / "Gideon"
    nested.mkdir()
    (tmp_path / ".git").mkdir()
    assert uk.git_root(str(nested)) == str(tmp_path)
    assert uk.git_root("") == ""


# ── RUM-2: channel + pin resolver ───────────────────────────────────────────
#
# A faked releases list (the normalized view fetch_releases emits) that is
# ADVERSARIAL to every shortcut a resolver might take:
#   - the newest entry is a PRERELEASE and comes FIRST → `stable` must SKIP
#     index 0, killing a "return releases[0]" cheat;
#   - stable's answer (v0.2.1) and beta's answer (v0.3.0-rc.1) are DIFFERENT
#     tags → a resolver that ignores the channel fails one of them;
#   - the pin target (v0.2.0) is OLDER than both channel answers → a "return
#     newest" cheat cannot satisfy pin-hit;
#   - no branch ever returns the last entry (v0.1.3) → "return releases[-1]"
#     is dead too.
_FAKE_RELEASES: list[dict[str, object]] = [
    {
        "tag": "v0.3.0-rc.1",
        "prerelease": True,
        "name": "0.3.0-rc.1",
        "body": "beta notes",
    },
    {"tag": "v0.2.1", "prerelease": False, "name": "0.2.1", "body": "stable notes"},
    {"tag": "v0.2.0", "prerelease": False, "name": "0.2.0", "body": ""},
    {"tag": "v0.1.3", "prerelease": False, "name": "0.1.3", "body": ""},
]


def test_select_target_stable_excludes_prereleases() -> None:
    got = uk.select_target(_FAKE_RELEASES, "stable")
    assert got == "v0.2.1"
    assert got != "v0.3.0-rc.1"


def test_select_target_beta_includes_prereleases() -> None:
    got = uk.select_target(_FAKE_RELEASES, "beta")
    assert got == "v0.3.0-rc.1"
    assert got != "v0.2.1"


def test_select_target_pin_overrides_channel_with_an_exact_hit() -> None:
    for channel in ("stable", "beta", "nightly"):
        assert uk.select_target(_FAKE_RELEASES, channel, "0.2.0") == "v0.2.0"
    assert uk.select_target(_FAKE_RELEASES, "stable", "v0.2.0") == "v0.2.0"
    assert uk.select_target(_FAKE_RELEASES, "stable", "0.2.0") != uk.select_target(
        _FAKE_RELEASES, "stable"
    )
    assert uk.select_target(_FAKE_RELEASES, "beta", "0.2.0") != uk.select_target(
        _FAKE_RELEASES, "beta"
    )


def test_select_target_pin_miss_returns_empty() -> None:
    assert uk.select_target(_FAKE_RELEASES, "stable", "9.9.9") == ""
    assert uk.select_target(_FAKE_RELEASES, "beta", "9.9.9") == ""


def test_select_target_nightly_is_branch_tracking_not_a_tag() -> None:
    assert uk.select_target(_FAKE_RELEASES, "nightly") == ""


def test_select_target_prerelease_detected_by_tag_suffix() -> None:
    rels: list[dict[str, object]] = [
        {"tag": "v0.4.0-rc.1", "prerelease": False, "name": "", "body": ""},
        {"tag": "v0.3.9", "prerelease": False, "name": "", "body": ""},
    ]
    assert uk.select_target(rels, "stable") == "v0.3.9"
    assert uk.select_target(rels, "beta") == "v0.4.0-rc.1"


def test_select_target_beta_tie_prefers_the_published_release() -> None:
    rels: list[dict[str, object]] = [
        {"tag": "v0.3.0-rc.1", "prerelease": True, "name": "", "body": ""},
        {"tag": "v0.3.0", "prerelease": False, "name": "", "body": ""},
    ]
    assert uk.select_target(rels, "beta") == "v0.3.0"


def test_select_target_empty_list_returns_empty() -> None:
    for channel in ("stable", "beta", "nightly"):
        assert uk.select_target([], channel) == ""
    assert uk.select_target([], "stable", "0.2.0") == ""


@pytest.mark.asyncio
async def test_resolve_target_proves_all_four_branches(monkeypatch) -> None:
    """The done_when headline: a faked releases list proves stable / beta /
    pin-hit / pin-miss through the real resolve_target seam."""

    async def _fake_releases(*, offline: bool = False) -> list[dict[str, object]]:
        return _FAKE_RELEASES

    monkeypatch.setattr(uk, "fetch_releases", _fake_releases)
    assert await uk.resolve_target("stable") == "v0.2.1"
    assert await uk.resolve_target("beta") == "v0.3.0-rc.1"
    assert await uk.resolve_target("stable", "0.2.0") == "v0.2.0"
    assert await uk.resolve_target("beta", "9.9.9") == ""


@pytest.mark.asyncio
async def test_resolve_target_offline_no_cache_returns_empty_never_raises(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))

    class _BoomSession:
        def __init__(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    assert await uk.resolve_target("stable") == ""
    assert await uk.resolve_target("beta") == ""
    assert await uk.resolve_target("stable", "0.2.0") == ""


@pytest.mark.asyncio
async def test_fetch_releases_offline_returns_cached_list(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    uk.write_releases_cache(
        {
            "releases": [
                {"tag": "v0.3.0-rc.1", "prerelease": True, "name": "", "body": ""},
                {"tag": "v0.2.1", "prerelease": False, "name": "", "body": ""},
            ],
            "etag": 'W/"cached"',
        }
    )

    class _BoomSession:
        def __init__(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    rels = await uk.fetch_releases()
    assert [r["tag"] for r in rels] == [
        "v0.3.0-rc.1",
        "v0.2.1",
    ]
    assert await uk.resolve_target("stable") == "v0.2.1"


class _FakeResp:
    def __init__(self, status, payload=None, headers=None) -> None:
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def json(self):  # type: ignore[no-untyped-def]
        return self._payload

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
        return False


class _FakeSession:
    """Records the outbound request so the ETag conditional can be asserted."""

    last_url = ""
    last_headers: dict = {}

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    def get(self, url, headers=None):  # type: ignore[no-untyped-def]
        _FakeSession.last_url = url
        _FakeSession.last_headers = dict(headers or {})
        return self._resp

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
        return False


@pytest.mark.asyncio
async def test_fetch_releases_200_maps_and_caches(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    payload = [
        {"tag_name": "v0.3.0-rc.1", "name": "rc", "body": "b", "prerelease": True},
        {"tag_name": "v0.2.1", "name": "stable", "body": "s", "prerelease": False},
        "not-a-dict",
    ]
    monkeypatch.setattr(
        uk, "_RELEASES_LIST_URL", "https://api.github.com/repos/gideon/checks/releases"
    )
    resp = _FakeResp(200, payload, {"ETag": 'W/"fresh"'})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(resp))

    rels = await uk.fetch_releases()
    assert [r["tag"] for r in rels] == ["v0.3.0-rc.1", "v0.2.1"]
    assert rels[0]["prerelease"] is True and rels[1]["prerelease"] is False
    assert (
        "/releases" in _FakeSession.last_url and "latest" not in _FakeSession.last_url
    )
    assert uk.read_releases_cache()["etag"] == 'W/"fresh"'


@pytest.mark.asyncio
async def test_fetch_releases_304_returns_cache_and_sends_conditional(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    uk.write_releases_cache(
        {
            "releases": [
                {"tag": "v0.2.1", "prerelease": False, "name": "", "body": ""}
            ],
            "etag": 'W/"prev"',
        }
    )
    monkeypatch.setattr(
        uk, "_RELEASES_LIST_URL", "https://api.github.com/repos/gideon/checks/releases"
    )
    resp = _FakeResp(304)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(resp))

    rels = await uk.fetch_releases()
    assert [r["tag"] for r in rels] == ["v0.2.1"]
    assert _FakeSession.last_headers.get("If-None-Match") == 'W/"prev"'


_CATALOG = [
    {"tag": "v0.3.0-rc.1", "prerelease": True, "name": "rc", "body": ""},
    {"tag": "v0.2.1", "prerelease": False, "name": "stable", "body": ""},
    {"tag": "v0.2.0", "prerelease": False, "name": "older", "body": ""},
]


@pytest.fixture
def catalog(monkeypatch):
    """Serve a fixed release history through the real resolver seam."""

    async def _releases(*, offline: bool = False) -> list[dict[str, object]]:
        return [] if offline else _CATALOG

    monkeypatch.setattr(uk, "fetch_releases", _releases)
    return _CATALOG


class TestPackageSelection:
    """RUM: a package install rides the SELECTED release, not always "latest"."""

    def test_nightly_maps_to_stable_because_there_is_no_nightly_wheel(self) -> None:
        assert uk.package_channel("nightly") == "stable"
        assert uk.package_channel("stable") == "stable"
        assert uk.package_channel("beta") == "beta"

    @pytest.mark.asyncio
    async def test_stable_beta_and_pin_each_pick_their_own_release(
        self, catalog
    ) -> None:
        stable = await uk.resolve_package_target("stable")
        beta = await uk.resolve_package_target("beta")
        pinned = await uk.resolve_package_target("stable", "0.2.0")
        assert stable.spec == "gideon-agent-harness==0.2.1"
        assert beta.spec == "gideon-agent-harness==0.3.0-rc.1"
        assert pinned.spec == "gideon-agent-harness==0.2.0"
        assert (stable.ok, beta.ok, pinned.ok) == (True, True, True)

    @pytest.mark.asyncio
    async def test_nightly_installs_the_stable_release(self, catalog) -> None:
        assert (await uk.resolve_package_target("nightly")).spec == (
            "gideon-agent-harness==0.2.1"
        )

    @pytest.mark.asyncio
    async def test_a_pin_that_names_no_release_is_refused_with_the_way_out(
        self, catalog
    ) -> None:
        target = await uk.resolve_package_target("stable", "9.9.9")
        assert not target.ok and not target.spec
        assert "9.9.9" in target.error
        assert "clear the pin" in target.error.lower()

    @pytest.mark.asyncio
    async def test_offline_unpinned_still_upgrades_unpinned(self, catalog) -> None:
        """No catalog (offline, cold cache) and no pin ⇒ the plain `-U` upgrade,
        which is the behavior that worked before channels existed."""
        target = await uk.resolve_package_target("stable", offline=True)
        assert target.ok and target.spec == "gideon-agent-harness"
        assert target.version == ""

    @pytest.mark.asyncio
    async def test_offline_PINNED_is_still_a_refusal_not_a_blind_upgrade(
        self, catalog
    ) -> None:
        """Vacuity guard for the offline rule: it relaxes the CHANNEL, never a pin."""
        target = await uk.resolve_package_target("stable", "0.2.0", offline=True)
        assert not target.ok and "0.2.0" in target.error


class TestSourcePlan:
    """RUM: source checkouts follow release tags; nightly alone tracks a branch."""

    @pytest.fixture(autouse=True)
    def _clean_tree(self, monkeypatch):
        monkeypatch.setattr(uk, "git_tracked_changes", lambda _p: [])

    @pytest.mark.asyncio
    async def test_stable_and_beta_select_release_tags(self, catalog) -> None:
        stable = await uk.plan_source_update("/p", "stable")
        beta = await uk.plan_source_update("/p", "beta")
        assert (stable.mode, stable.ref) == ("tag", "v0.2.1")
        assert (beta.mode, beta.ref) == ("tag", "v0.3.0-rc.1")

    @pytest.mark.asyncio
    async def test_an_exact_pin_wins_over_the_channel(self, catalog) -> None:
        plan = await uk.plan_source_update("/p", "beta", "0.2.0")
        assert (plan.mode, plan.ref) == ("tag", "v0.2.0")

    @pytest.mark.asyncio
    async def test_nightly_is_the_only_channel_that_tracks_a_branch(
        self, catalog, monkeypatch
    ) -> None:
        monkeypatch.setattr(uk, "current_branch", lambda _p: "main")
        plan = await uk.plan_source_update("/p", "nightly")
        assert (plan.mode, plan.ref) == ("branch", "origin/main")

    @pytest.mark.asyncio
    async def test_a_pin_overrides_even_nightly_back_onto_a_tag(
        self, catalog, monkeypatch
    ) -> None:
        monkeypatch.setattr(uk, "current_branch", lambda _p: "main")
        plan = await uk.plan_source_update("/p", "nightly", "0.2.1")
        assert (plan.mode, plan.ref) == ("tag", "v0.2.1")

    @pytest.mark.asyncio
    async def test_nightly_on_a_detached_head_refuses_rather_than_guessing(
        self, catalog, monkeypatch
    ) -> None:
        monkeypatch.setattr(uk, "current_branch", lambda _p: "")
        plan = await uk.plan_source_update("/p", "nightly")
        assert plan.mode == "none" and "detached HEAD" in plan.reason

    @pytest.mark.asyncio
    async def test_a_missing_pin_refuses_with_the_way_out(self, catalog) -> None:
        plan = await uk.plan_source_update("/p", "stable", "9.9.9")
        assert plan.mode == "none" and not plan.ok
        assert "9.9.9" in plan.reason and "clear the pin" in plan.reason.lower()

    @pytest.mark.asyncio
    async def test_no_published_release_is_not_an_update(self, monkeypatch) -> None:
        async def _empty(*, offline: bool = False):
            return []

        monkeypatch.setattr(uk, "fetch_releases", _empty)
        plan = await uk.plan_source_update("/p", "stable")
        assert plan.mode == "none" and "stable" in plan.reason


class TestDirtyTreeSafeguard:
    """RUM-75: ONE dirty-tree safeguard, owned by the release-tag planner.

    It refuses with tracked edits, names the way out, and exposes a paused state
    the surfaces report — and no surface carries a second copy of the check.
    """

    @pytest.mark.asyncio
    async def test_tracked_edits_pause_the_update_with_an_actionable_reason(
        self, catalog, monkeypatch
    ) -> None:
        monkeypatch.setattr(uk, "git_tracked_changes", lambda _p: [" M runtime/a.py"])
        plan = await uk.plan_source_update("/p", "stable")
        assert plan.paused and plan.mode == "paused" and not plan.ok
        assert "commit or stash" in plan.reason.lower()
        assert plan.paths == (" M runtime/a.py",)

    @pytest.mark.asyncio
    async def test_the_safeguard_runs_before_any_release_lookup(
        self, monkeypatch
    ) -> None:
        """Vacuity guard: a paused plan must not depend on resolving a release."""

        async def _boom(*a, **k):
            raise AssertionError("resolved a release for a paused checkout")

        monkeypatch.setattr(uk, "fetch_releases", _boom)
        monkeypatch.setattr(uk, "git_tracked_changes", lambda _p: [" M runtime/a.py"])
        assert (await uk.plan_source_update("/p", "stable")).paused

    @pytest.mark.asyncio
    async def test_untracked_files_alone_do_not_pause(
        self, catalog, monkeypatch
    ) -> None:
        monkeypatch.setattr(uk, "git_tracked_changes", lambda _p: [])
        assert not (await uk.plan_source_update("/p", "stable")).paused

    def test_there_is_no_second_dirty_tree_gate_in_the_update_surfaces(self) -> None:
        """The interim safeguard was a `git status --porcelain` block inside the
        dashboard apply and a second `git_tracked_changes` call inside the
        unattended updater. Both are gone: the surfaces read the plan."""
        import inspect

        from gideon.engine import gateway_maintenance
        from gideon.interfaces.cli import server as cli_server
        from gideon.interfaces.dashboard.handlers import updates as dash

        for module in (dash, gateway_maintenance, cli_server):
            source = inspect.getsource(module)
            assert "--porcelain" not in source, f"{module.__name__} re-added a gate"
            assert "git_tracked_changes" not in source, (
                f"{module.__name__} checks the tree itself instead of reading "
                "plan_source_update's paused state"
            )


def _code_strings(module) -> set[str]:
    """Every string literal in *module* that is not a docstring."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


class TestFastForwardOnly:
    """RUM: the checkout moves by fast-forward, never a pull and never a reset."""

    def test_the_destructive_reset_helper_is_gone(self) -> None:
        assert not hasattr(uk, "git_reset_hard")

    def test_no_update_surface_pulls_or_resets(self) -> None:
        """No update surface passes `pull` or `reset --hard` to git.

        Scans the CODE, not the prose: the docstrings deliberately name both
        commands to say they are gone, and a substring scan would either pass
        vacuously or fail on the explanation.
        """
        from gideon.engine import gateway_maintenance
        from gideon.interfaces.cli import server as cli_server
        from gideon.interfaces.dashboard.handlers import updates as dash

        for module in (uk, dash, gateway_maintenance, cli_server):
            assert "--hard" not in _code_strings(
                module
            ), f"{module.__name__} still resets --hard"
        for module in (dash, gateway_maintenance, cli_server):
            assert "pull" not in _code_strings(
                module
            ), f"{module.__name__} still runs git pull"
        assert "pull" in _code_strings(uk), (
            "the only surviving 'pull' is the container instruction "
            "(`docker compose pull`) — if that went too, this scan is vacuous"
        )
        assert "--ff-only" in _code_strings(uk), "the scan is not reading the code"

    def test_fast_forward_moves_the_branch_and_a_divergence_is_refused(
        self, tmp_path, monkeypatch
    ) -> None:
        """Real git: a tagged upstream commit fast-forwards; a checkout carrying
        its own commit is refused instead of being rewritten."""
        import subprocess

        def git(where, *args):
            return subprocess.run(
                ["git", *args],
                cwd=str(where),
                capture_output=True,
                text=True,
                check=True,
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@e.invalid",
                    "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@e.invalid",
                },
            ).stdout.strip()

        origin = tmp_path / "origin"
        origin.mkdir()
        git(origin, "init", "-b", "main")
        (origin / "f.txt").write_text("one")
        git(origin, "add", "f.txt")
        git(origin, "commit", "-m", "one")
        clone = tmp_path / "clone"
        git(tmp_path, "clone", str(origin), str(clone))

        (origin / "f.txt").write_text("two")
        git(origin, "commit", "-am", "two")
        git(origin, "tag", "v0.2.1")

        plan = uk.SourcePlan("tag", "v0.2.1")
        assert uk.fetch_for_plan(str(clone), plan).returncode == 0
        assert uk.git_commit_for(str(clone), "v0.2.1")
        assert uk.git_is_fast_forward(str(clone), "v0.2.1")
        assert uk.git_merge_ff_only(str(clone), "v0.2.1").returncode == 0
        assert (clone / "f.txt").read_text() == "two"
        assert uk.git_commit_for(str(clone), "HEAD") == uk.git_commit_for(
            str(clone), "v0.2.1"
        )

        (clone / "local.txt").write_text("mine")
        git(clone, "add", "local.txt")
        git(clone, "commit", "-m", "local work")
        (origin / "f.txt").write_text("three")
        git(origin, "commit", "-am", "three")
        git(origin, "tag", "v0.2.2")
        assert (
            uk.fetch_for_plan(str(clone), uk.SourcePlan("tag", "v0.2.2")).returncode
            == 0
        )
        assert not uk.git_is_fast_forward(str(clone), "v0.2.2")
        head = uk.git_commit_for(str(clone), "HEAD")
        assert uk.git_merge_ff_only(str(clone), "v0.2.2").returncode != 0
        assert uk.git_commit_for(str(clone), "HEAD") == head
        assert (clone / "local.txt").read_text() == "mine"


class TestReleaseCacheIsRebuildableState:
    """RUM: the release cache is declared, derived state — not an unclaimed file."""

    def test_both_cache_files_are_declared_as_derived_platform_state(self) -> None:
        from gideon.operations.durability import inventory as inv

        for name in ("update_check.json", "update_releases.json"):
            entry = inv.claim_for(name)
            assert entry is not None, f"{name} is unclaimed by the state inventory"
            assert entry.derived is True, f"{name} must be classed rebuildable"
            assert entry.domain == inv.DOMAIN_PLATFORM
            assert not inv.is_ignored(name), f"{name} is declared, so not ignored"

    def test_rebuildable_means_excluded_from_snapshots_and_exports(self) -> None:
        from gideon.operations.durability import inventory as inv

        ids = {"release_cache", "release_catalog_cache"}
        assert not ids & {e.id for e in inv.backup_entries()}
        assert not ids & {e.id for e in inv.export_entries()}
        assert ids <= {e.id for e in inv.backup_entries(include_derived=True)}

    def test_the_declared_paths_are_the_paths_the_updater_writes(
        self, monkeypatch, tmp_path
    ) -> None:
        """The rail that keeps the declaration honest: a renamed cache file reds."""
        from gideon.operations.durability import inventory as inv

        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        uk.write_release_cache({"tag": "v0.1.0"})
        uk.write_releases_cache({"releases": []})
        for path in (uk._cache_path(), uk._list_cache_path()):
            assert path.is_file()
            assert inv.claim_for(path.name) is not None


class TestReleaseCheckControls:
    """RUM: `updates.check_enabled` is a kill switch and `check_interval_hours` a cadence.

    Both are proved at the seam that would actually talk to the network — the
    subprocess spawn (`git fetch`) and the HTTP session — so "no traffic" means
    nothing was opened, not that a flag was read.
    """

    @pytest.fixture
    def handler(self, monkeypatch, tmp_path):
        from gideon.interfaces.dashboard.handlers import updates as upd

        monkeypatch.setattr(upd, "_last_update_check", 0.0, raising=False)
        monkeypatch.setenv("GIDEON_PROJECT_DIR", str(tmp_path / "proj"))
        (tmp_path / "proj").mkdir()
        return upd

    @pytest.fixture
    def write_config(self, monkeypatch, tmp_path):
        import json

        monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir(exist_ok=True)

        def _write(**updates):
            from gideon.core.config import loader as config_loader

            config_loader.config_path().write_text(
                json.dumps({"updates": updates}), encoding="utf-8"
            )

        return _write

    @pytest.fixture
    def seams(self, monkeypatch):
        """Record every outbound attempt; a real one would raise instead."""
        spawned: list[tuple] = []
        sessions: list[str] = []

        async def _exec(*argv, **kw):
            spawned.append(argv)
            raise AssertionError(f"opened a subprocess: {argv}")

        class _Session:
            def __init__(self, *a, **k):
                sessions.append("session")
                raise AssertionError("opened an HTTP session")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
        monkeypatch.setattr(aiohttp, "ClientSession", _Session)
        return spawned, sessions

    @pytest.mark.asyncio
    async def test_the_switch_off_opens_nothing_even_for_an_explicit_check(
        self, handler, write_config, seams
    ) -> None:
        spawned, sessions = seams
        write_config(check_enabled=False)
        await handler._do_update_check(force=True)
        assert spawned == [] and sessions == []

    @pytest.mark.asyncio
    async def test_the_switch_off_leaves_the_api_answering_from_cache(
        self, handler, write_config, seams
    ) -> None:
        import json

        from aiohttp.test_utils import make_mocked_request

        spawned, sessions = seams
        write_config(check_enabled=False)
        uk.write_release_cache({"tag": "v99.0.0", "name": "cached", "body": ""})
        resp = await handler.api_update_check(
            make_mocked_request("GET", "/api/update/check")
        )
        body = json.loads(resp.body.decode())
        assert spawned == [] and sessions == []
        assert body["check_enabled"] is False
        assert body["latest"] == "99.0.0"

    @pytest.mark.asyncio
    async def test_the_switch_on_does_reach_the_network_seam(
        self, handler, write_config, monkeypatch
    ) -> None:
        """Vacuity guard: with the switch ON the same call spawns `git fetch`."""
        spawned: list[tuple] = []

        class _Proc:
            returncode = 1

            async def communicate(self):
                return b"", b""

        async def _exec(*argv, **kw):
            spawned.append(argv)
            return _Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
        write_config(check_enabled=True)
        await handler._do_update_check(force=True)
        assert spawned and spawned[0][:2] == ("git", "fetch")

    @pytest.mark.asyncio
    async def test_the_interval_holds_a_scheduled_check_and_then_releases_it(
        self, handler, write_config, monkeypatch
    ) -> None:
        spawned: list[tuple] = []

        class _Proc:
            returncode = 1

            async def communicate(self):
                return b"", b""

        async def _exec(*argv, **kw):
            spawned.append(argv)
            return _Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
        write_config(check_enabled=True, check_interval_hours=6)
        now = time.time()
        monkeypatch.setattr(handler, "_last_update_check", now, raising=False)

        assert handler.update_check_interval_seconds() == 6 * 3600
        assert handler.update_check_due() is False
        await handler._do_update_check()
        assert spawned == [], "a scheduled check ran inside its own interval"

        monkeypatch.setattr(
            handler, "_last_update_check", now - 6 * 3600 - 1, raising=False
        )
        assert handler.update_check_due() is True
        await handler._do_update_check()
        assert spawned, "the check never ran again after the interval elapsed"

    def test_the_switch_beats_the_interval(self, handler, write_config) -> None:
        write_config(check_enabled=False, check_interval_hours=1)
        assert handler.update_check_due() is False

    def test_an_unreadable_config_does_not_silently_enable_checks(
        self, handler, monkeypatch, tmp_path
    ) -> None:
        """The fallback is the shipped default block, not "check anyway"."""
        from gideon.core.config.loader import UpdatesConfig

        monkeypatch.setattr(
            handler.AppConfig,
            "load",
            staticmethod(lambda: (_ for _ in ()).throw(OSError())),
        )
        policy = handler.update_policy()
        assert policy.check_enabled == UpdatesConfig().check_enabled
        assert policy.check_interval_hours == UpdatesConfig().check_interval_hours
