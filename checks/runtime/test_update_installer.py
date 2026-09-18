"""Self-update on uv venvs, and surfacing the real failure (issue #51).

The pip-kind updater hardcoded ``python -m pip install -U``, which does not exist
in a ``uv venv`` — so Settings → Updates showed "Update failed — pip upgrade
failed" forever on the uv install path, while the panel itself already LABELLED
that kind "pip / uv install". Worse, the actual cause (``No module named pip``) was
captured and logged but never sent to the UI, so the only way to learn anything was
to read gateway.log.

Hermetic: the installer probes and the subprocess are both faked, so these pass on
a pip venv and a uv venv alike.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from gideon.interfaces.dashboard.handlers import updates as upd
from gideon.operations import _installer
from gideon.operations import self_update as su


class _StateStub:
    def __init__(self) -> None:
        self._background_tasks: set = set()
        self.progress: list[tuple[str, str]] = []
        self.refreshes: list[str] = []

    def push_refresh(self, *kinds: str) -> None:
        self.refreshes.extend(kinds)

    def push_update_progress(self, step: str, detail: str = "") -> None:
        self.progress.append((step, detail))


class _Proc:
    """Stand-in for the upgrade subprocess."""

    def __init__(self, rc: int, stderr: bytes = b"") -> None:
        self.returncode = rc
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr

    def kill(self):  # pragma: no cover — only the timeout path calls this
        pass


@pytest.fixture
def spawn(monkeypatch):
    """Capture the argv the updater would spawn; serve a canned result."""
    seen: list[list[str]] = []

    def _install(proc: _Proc):
        async def _fake_exec(*argv, **kw):
            seen.append(list(argv))
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        return seen

    return _install


_CATALOG = {
    "releases": [
        {"tag": "v0.3.0-rc.1", "prerelease": True, "name": "rc", "body": ""},
        {"tag": "v0.2.1", "prerelease": False, "name": "stable", "body": ""},
        {"tag": "v0.1.2", "prerelease": False, "name": "older", "body": ""},
    ]
}


def _policy(monkeypatch, tmp_path, **updates):
    """Write a REAL config + release catalog and let the real resolver read them.

    Nothing is faked here: ``_RELEASES_LIST_URL`` is empty without
    ``GIDEON_RELEASE_REPOSITORY``, so ``fetch_releases`` answers from the cache
    file on disk — the same code path a real offline install takes.
    """
    import json

    from gideon.core.config import loader as config_loader

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    config_loader.config_path().write_text(
        json.dumps({"updates": updates}), encoding="utf-8"
    )
    su.write_releases_cache(_CATALOG)


async def _run_apply(state, monkeypatch):
    """Drive _apply_pip_update, returning (response, progress)."""

    async def _fake_reexec(_state, **kw):
        state.progress.append(("reexec", ""))

    monkeypatch.setattr(upd, "_graceful_reexec", _fake_reexec)
    monkeypatch.setattr(upd, "_live_auth_mode", lambda _r: "token")

    captured: list = []
    monkeypatch.setattr(
        upd.asyncio,
        "create_task",
        lambda coro: captured.append(coro) or asyncio.ensure_future(coro),
    )
    from aiohttp.test_utils import make_mocked_request

    req = make_mocked_request("POST", "/api/update")
    req.app["state"] = state
    resp = await upd._apply_pip_update(req, state)
    for _ in range(50):
        await asyncio.sleep(0)
        if state.progress:
            break
    await asyncio.sleep(0.05)
    return resp, state.progress


@pytest.mark.asyncio
async def test_uses_uv_when_the_venv_has_no_pip(monkeypatch, spawn, tmp_path):
    """The #51 repro: self-update must work on a uv venv."""
    monkeypatch.setattr(_installer, "_have_uv", lambda: True)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)
    _policy(monkeypatch, tmp_path, channel="stable", pin="0.1.2")
    seen = spawn(_Proc(0))
    state = _StateStub()

    await _run_apply(state, monkeypatch)

    assert seen, "no upgrade subprocess was spawned"
    argv = seen[0]
    assert argv[:3] == ["uv", "pip", "install"]
    assert "--python" in argv and argv[argv.index("--python") + 1] == sys.executable
    assert "gideon-agent-harness==0.1.2" in argv
    steps = [s for s, _ in state.progress]
    assert "error" not in steps


@pytest.mark.asyncio
async def test_failure_detail_reaches_the_ui(monkeypatch, spawn, tmp_path):
    """Before the fix the panel showed the static "pip upgrade failed" while the
    real cause sat in gateway.log. The user must be able to SEE the cause."""
    monkeypatch.setattr(_installer, "_have_uv", lambda: False)
    monkeypatch.setattr(_installer, "_have_pip", lambda: True)
    _policy(monkeypatch, tmp_path, channel="stable")
    spawn(_Proc(1, b"ERROR: Could not find a version that satisfies gideon==9.9.9\n"))
    state = _StateStub()

    await _run_apply(state, monkeypatch)

    errors = [d for s, d in state.progress if s == "error"]
    assert errors, f"no error progress pushed: {state.progress}"
    assert "Could not find a version" in errors[0]
    assert errors[0] != "pip upgrade failed"


def test_summary_strips_ansi_and_leads_with_uvs_headline():
    """Both defects found by driving the real panel.

    uv COLORIZES its diagnostics, so the raw bytes carry SGR escapes that render
    literally in the browser. And its resolver error is a multi-line tree whose
    headline is FIRST — taking the last line yielded the useless fragment
    "unsatisfiable." with no subject.
    """
    raw = (
        "\x1b[31m×\x1b[0m No solution found when resolving dependencies:\n"
        "\x1b[31m  ╰─▶ \x1b[0mBecause there is no version of gideon==99.9.9 and you require\n"
        "\x1b[31m      \x1b[0mgideon==99.9.9, we can conclude that your requirements are\n"
        "\x1b[31m      \x1b[0munsatisfiable."
    )
    out = su.installer_error_summary(raw)
    assert "\x1b" not in out and "[31m" not in out
    assert out.startswith("No solution found when resolving dependencies")
    assert out != "unsatisfiable."


def test_summary_prefers_pips_explicit_error_line():
    raw = "Collecting gideon==9.9.9\nERROR: Could not find a version that satisfies it"
    out = su.installer_error_summary(raw)
    assert out.startswith("ERROR: Could not find a version")


def test_summary_keeps_the_no_module_named_pip_case_readable():
    """The original #46/#51 symptom must still come through intact."""
    out = su.installer_error_summary("/x/.venv/bin/python: No module named pip")
    assert "No module named pip" in out


def test_summary_is_bounded_and_empty_safe():
    assert su.installer_error_summary("") == ""
    assert len(su.installer_error_summary("x" * 5000)) <= 200


@pytest.mark.asyncio
async def test_no_installer_reports_the_real_reason_without_spawning(
    monkeypatch, tmp_path
):
    """With neither installer, don't spawn anything — say what's missing."""
    monkeypatch.setattr(_installer, "_have_uv", lambda: False)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)
    _policy(monkeypatch, tmp_path, channel="stable")

    async def _unreachable(*a, **kw):  # pragma: no cover
        raise AssertionError("spawned a subprocess with no installer available")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _unreachable)
    state = _StateStub()

    await _run_apply(state, monkeypatch)

    errors = [d for s, d in state.progress if s == "error"]
    assert errors, f"no error pushed: {state.progress}"
    assert "uv" in errors[0]


def _spec_of(argv: list[str]) -> str:
    return next(a for a in argv if a.startswith("gideon-agent-harness"))


class TestPackageUpdateSelection:
    """RUM-57: the package updater installs the release POLICY selects.

    It used to install whatever ``/releases/latest`` returned, so a beta channel
    and an exact pin were both wired to nothing.
    """

    @pytest.fixture(autouse=True)
    def _installer_present(self, monkeypatch):
        monkeypatch.setattr(_installer, "_have_uv", lambda: False)
        monkeypatch.setattr(_installer, "_have_pip", lambda: True)

    @pytest.mark.asyncio
    async def test_stable_installs_the_newest_non_prerelease(
        self, monkeypatch, spawn, tmp_path
    ) -> None:
        _policy(monkeypatch, tmp_path, channel="stable")
        seen = spawn(_Proc(0))
        await _run_apply(_StateStub(), monkeypatch)
        assert _spec_of(seen[0]) == "gideon-agent-harness==0.2.1"

    @pytest.mark.asyncio
    async def test_beta_installs_the_prerelease(
        self, monkeypatch, spawn, tmp_path
    ) -> None:
        _policy(monkeypatch, tmp_path, channel="beta")
        seen = spawn(_Proc(0))
        await _run_apply(_StateStub(), monkeypatch)
        assert _spec_of(seen[0]) == "gideon-agent-harness==0.3.0-rc.1"

    @pytest.mark.asyncio
    async def test_an_exact_pin_overrides_the_channel(
        self, monkeypatch, spawn, tmp_path
    ) -> None:
        _policy(monkeypatch, tmp_path, channel="beta", pin="0.1.2")
        seen = spawn(_Proc(0))
        await _run_apply(_StateStub(), monkeypatch)
        assert _spec_of(seen[0]) == "gideon-agent-harness==0.1.2"

    @pytest.mark.asyncio
    async def test_nightly_installs_the_stable_release(
        self, monkeypatch, spawn, tmp_path
    ) -> None:
        """There is no nightly wheel, so a package install rides stable there."""
        _policy(monkeypatch, tmp_path, channel="nightly")
        seen = spawn(_Proc(0))
        await _run_apply(_StateStub(), monkeypatch)
        assert _spec_of(seen[0]) == "gideon-agent-harness==0.2.1"

    @pytest.mark.asyncio
    async def test_a_pin_naming_no_release_refuses_without_installing_anything(
        self, monkeypatch, tmp_path
    ) -> None:
        _policy(monkeypatch, tmp_path, channel="stable", pin="9.9.9")

        async def _unreachable(*a, **kw):  # pragma: no cover
            raise AssertionError("installed something for an unresolvable pin")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _unreachable)
        state = _StateStub()
        resp, progress = await _run_apply(state, monkeypatch)

        assert resp.status == 409
        body = json.loads(resp.body.decode())
        assert "9.9.9" in body["error"] and "clear the pin" in body["error"].lower()
        assert ("error", body["error"]) in progress
        assert upd._apply_in_flight is False

    @pytest.mark.asyncio
    async def test_offline_unpinned_still_upgrades_unpinned(
        self, monkeypatch, spawn, tmp_path
    ) -> None:
        """No catalog at all (a cold offline install) still runs the plain `-U`."""
        from gideon.core.config import loader as config_loader

        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        monkeypatch.setenv("GIDEON_HOME", str(home))
        config_loader.config_path().write_text(
            json.dumps({"updates": {"channel": "stable"}}), encoding="utf-8"
        )
        seen = spawn(_Proc(0))
        await _run_apply(_StateStub(), monkeypatch)
        assert _spec_of(seen[0]) == "gideon-agent-harness"
