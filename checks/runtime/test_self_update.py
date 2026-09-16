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
    async def _fake_release() -> dict:
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
    async def _fake_release() -> dict:
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
    async def _empty_release() -> dict:
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


async def _drive_check_for_updates(monkeypatch, *, auto_update: bool):
    """Run `RuntimeCoordinator._check_for_updates` against stubs, recording call order."""
    from gideon.engine.gateway import RuntimeCoordinator
    from gideon.interfaces.dashboard import handlers as dash_handlers

    order: list[str] = []
    applied: list[str] = []

    async def _fake_check() -> None:
        order.append("check")

    class _Cfg:
        pass

    _Cfg.auto_update = auto_update

    def _load(*_a, **_k):
        order.append("config")
        return _Cfg()

    monkeypatch.setattr(dash_handlers, "_do_update_check", _fake_check)
    monkeypatch.setattr(dash_handlers, "_update_info", {"available": True})
    monkeypatch.setattr("gideon.core.config.AppConfig.load", _load)

    await RuntimeCoordinator._check_for_updates(_orchestrator_stub(applied))
    return order, applied


@pytest.mark.asyncio
async def test_auto_update_gates_the_apply_not_the_check(monkeypatch) -> None:
    order, applied = await _drive_check_for_updates(monkeypatch, auto_update=False)
    assert order == ["check", "config"], (
        "the update check must run BEFORE auto_update is consulted — got "
        f"{order}. If the check is now gated, `docs/architecture/network-egress-hosts.txt` "
        "no longer describes api.github.com correctly."
    )
    assert applied == []


@pytest.mark.asyncio
async def test_auto_update_on_reaches_the_apply(monkeypatch) -> None:
    order, applied = await _drive_check_for_updates(monkeypatch, auto_update=True)
    assert order == ["check", "config"]
    assert applied == ["apply"]


@pytest.mark.asyncio
async def test_c2_wire_shape_conformance(monkeypatch) -> None:
    """build_update_status emits exactly the C2 contract keys (+ additive extras),
    with the per-kind apply_method / commits_behind / instructions semantics the
    plan pins. Locks the Tier-S wire shape against silent drift."""

    async def _rel() -> dict:
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

    async def _fake_releases() -> list[dict[str, object]]:
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
