"""`gideon update` behaves per install kind (DIST-13).

Before this, the CLI *was* the git pipeline: it demanded `$GIDEON_PROJECT_DIR`
and a `.git` dir, so every pip / pipx / uv-tool user — the install the README lists
first — hit "❌ GIDEON_PROJECT_DIR not set" and exit 1, while the install-kind
machinery the dashboard already used sat one module away. One test per branch drives
the real dispatch; nothing here runs git, pip, or a frontend build.

The fake layer is deliberately narrow and at the two real seams:
`self_update._run_git` (every sync git spawn funnels through it) and
`cli_server.subprocess.run` (the installer and the post-update `setup --agent-only`).
A test that actually ran `git reset --hard` or `pip -U` would be a wrecking ball.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from gideon.interfaces.cli import server as cli_server
from gideon.operations import self_update as su


class _Git:
    """Records every git invocation and answers from a per-prefix script."""

    def __init__(self, **replies: tuple[int, str, str]) -> None:
        self.calls: list[list[str]] = []
        self._replies = replies

    def __call__(self, args: list[str], *, cwd: str, timeout: float):
        self.calls.append(list(args))
        key = args[0]
        if key == "rev-parse" and "--verify" in args:
            key = "rev_parse_head" if args[-1].startswith("HEAD") else "rev_parse_ref"
        rc, out, err = self._replies.get(key, (0, "", ""))
        return subprocess.CompletedProcess(["git", *args], rc, out, err)

    def ran(self, *prefix: str) -> bool:
        return any(c[: len(prefix)] == list(prefix) for c in self.calls)


def _fake_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for uv/pip resolution, keeping the real ``install`` verb position."""
    monkeypatch.setattr(
        "gideon.operations._installer.install_argv",
        lambda args: ["FAKE-INSTALLER", "install", *args],
    )
    monkeypatch.setattr("gideon.operations._installer.installer_name", lambda: "fake")


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture cli_server's subprocess.run argvs; nothing is executed."""
    seen: list[list[str]] = []

    def _fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli_server.subprocess, "run", _fake_run)
    return seen


@pytest.fixture(autouse=True)
def _no_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """No frontend build in any of these tests. The release probe needs no stub:
    without ``GIDEON_RELEASE_REPOSITORY`` the resolver reads the on-disk cache
    (redirected to a per-test home), so it is offline by construction."""
    monkeypatch.setattr(cli_server, "build_frontend_sync", lambda path: None)
    monkeypatch.delenv("GIDEON_INSTALL_KIND", raising=False)
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)


def _policy(monkeypatch, tmp_path, *, releases=(), **updates) -> None:
    """Write the REAL config + release catalog this install should read."""
    from gideon.core.config import loader as config_loader

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    config_loader.config_path().write_text(
        json.dumps({"updates": updates}), encoding="utf-8"
    )
    su.write_releases_cache(
        {
            "releases": [
                {"tag": tag, "prerelease": "-" in tag, "name": "", "body": ""}
                for tag in releases
            ]
        }
    )


def _as_git_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    (proj / ".git").mkdir(exist_ok=True)
    (proj / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(proj))
    return str(proj)


def test_every_install_kind_has_a_cli_branch() -> None:
    """The dispatch is exhaustive over the taxonomy — adding a kind reds here.

    This is the ratchet that makes the "no default arm" rule enforceable: a new
    InstallKind member cannot quietly land in someone's else-branch.
    """
    assert set(su.INSTALL_KINDS) == set(cli_server._UPDATE_HANDLED_KINDS)


def test_unmapped_kind_refuses_and_names_what_it_detected(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    monkeypatch.setattr(
        cli_server.self_update, "detect_install_kind", lambda: "flatpak"
    )
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "flatpak" in out
    assert "refusing to guess" in out
    assert not git.calls and not spawns


def test_git_kind_fast_forwards_onto_the_resolved_release_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """RUM-59: a normal source install rides the release TAG, by fast-forward."""
    proj = _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v0.2.1"])
    git = _Git(
        **{
            "status": (0, "", ""),
            "rev_parse_ref": (0, "newsha\n", ""),
            "rev_parse_head": (0, "oldsha\n", ""),
            "merge-base": (0, "", ""),
            "merge": (0, "", ""),
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert git.ran("fetch", "--tags", "origin")
    assert git.ran("merge", "--ff-only", "v0.2.1")
    assert not git.ran("reset")
    assert not git.ran("pull")
    assert any("install" in " ".join(a) for a in spawns)
    assert any(a[-2:] == ["setup", "--agent-only"] for a in spawns)
    assert proj in capsys.readouterr().out


def test_git_kind_nightly_is_the_only_channel_that_tracks_the_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="nightly", releases=["v0.2.1"])
    git = _Git(
        **{
            "status": (0, "", ""),
            "rev-parse": (0, "main\n", ""),
            "rev_parse_ref": (0, "newsha\n", ""),
            "rev_parse_head": (0, "oldsha\n", ""),
            "merge-base": (0, "", ""),
            "merge": (0, "", ""),
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert git.ran("fetch", "origin", "main")
    assert git.ran("merge", "--ff-only", "origin/main")
    assert not git.ran("reset")


def test_git_kind_already_on_the_tag_does_not_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v0.2.1"])
    git = _Git(
        **{
            "status": (0, "", ""),
            "rev_parse_ref": (0, "same\n", ""),
            "rev_parse_head": (0, "same\n", ""),
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "Already up to date" in capsys.readouterr().out
    assert not git.ran("merge")
    assert not spawns


def test_git_kind_with_no_published_release_is_not_an_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="stable")
    git = _Git(**{"status": (0, "", "")})
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "No published release" in capsys.readouterr().out
    assert not git.ran("fetch") and not spawns


def test_git_kind_missing_pin_refuses_with_the_way_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="stable", pin="9.9.9", releases=["v0.2.1"])
    git = _Git(**{"status": (0, "", "")})
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    out = capsys.readouterr().out
    assert "9.9.9" in out and "clear the pin" in out.lower()
    assert not git.ran("fetch") and not spawns


def test_git_kind_fetch_failure_exits_nonzero_before_touching_the_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v0.2.1"])
    git = _Git(**{"status": (0, "", ""), "fetch": (128, "", "fatal: no such ref")})
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    assert "git fetch failed" in capsys.readouterr().out
    assert not git.ran("merge")


def test_git_kind_non_fast_forward_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """RUM-59: divergence stops the update; it never becomes a reset."""
    _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v0.2.1"])
    git = _Git(
        **{
            "status": (0, "", ""),
            "rev_parse_ref": (0, "newsha\n", ""),
            "rev_parse_head": (0, "oldsha\n", ""),
            "merge-base": (1, "", ""),
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    assert "Cannot fast-forward" in capsys.readouterr().out
    assert not git.ran("merge") and not git.ran("reset") and not spawns


def test_tracked_changes_pause_the_update_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """RUM-75: the interim confirm-then-destroy prompt is gone. Tracked edits
    pause the update with the reason — there is nothing left to say yes to."""
    _as_git_checkout(monkeypatch, tmp_path)
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v0.2.1"])
    git = _Git(
        **{
            "status": (
                0,
                " M runtime/gideon/interfaces/cli/main.py\n?? scratch.txt\n",
                "",
            )
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    def _boom(prompt: str = "") -> str:
        raise AssertionError("input() must never gate a destructive update")

    monkeypatch.setattr("builtins.input", _boom)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Update paused" in out
    assert "commit or stash" in out.lower()
    assert "runtime/gideon/interfaces/cli/main.py" in out
    assert "scratch.txt" not in out
    assert not git.ran("fetch") and not git.ran("merge") and not spawns


def test_pip_kind_upgrades_without_a_source_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """The regression this atom exists for: no PROJECT_DIR, and it still updates."""
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v9.9.9"])
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)
    _fake_installer(monkeypatch)

    cli_server._update()

    out = capsys.readouterr().out
    assert "GIDEON_PROJECT_DIR" not in out
    assert [
        "FAKE-INSTALLER",
        "install",
        "-U",
        "gideon-agent-harness==9.9.9",
        "--quiet",
    ] in [a[:5] for a in spawns]
    assert not git.calls
    assert "gideon restart" in out


def test_pip_kind_already_current_skips_the_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v0.0.1"])
    monkeypatch.setattr(cli_server, "__version__", "9.9.9")

    cli_server._update()

    assert "Already on the latest release" in capsys.readouterr().out
    assert not spawns


def test_pip_kind_unknown_latest_upgrades_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path, spawns
) -> None:
    """Offline (no catalog) still tries: `-U gideon`, not a refusal."""
    _policy(monkeypatch, tmp_path, channel="stable")
    _fake_installer(monkeypatch)

    cli_server._update()

    assert [
        "FAKE-INSTALLER",
        "install",
        "-U",
        "gideon-agent-harness",
        "--quiet",
    ] in spawns


def test_pip_kind_selects_the_channel_release_not_just_the_newest(
    monkeypatch: pytest.MonkeyPatch, tmp_path, spawns
) -> None:
    """RUM-57: beta installs the prerelease; stable skips it."""
    _policy(monkeypatch, tmp_path, channel="beta", releases=["v9.9.9-rc.1", "v9.9.8"])
    _fake_installer(monkeypatch)

    cli_server._update()

    assert any("gideon-agent-harness==9.9.9-rc.1" in a for a in spawns)


def test_pip_kind_refuses_a_pin_that_names_no_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _policy(monkeypatch, tmp_path, channel="stable", pin="1.2.3", releases=["v9.9.9"])
    _fake_installer(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "1.2.3" in out and "clear the pin" in out.lower()
    assert not spawns


def test_pip_kind_install_failure_reports_one_clean_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    """uv's stderr is ANSI-colored and leads with the headline — say that, not raw bytes."""
    _policy(monkeypatch, tmp_path, channel="stable", releases=["v9.9.9"])
    _fake_installer(monkeypatch)
    raw = "\x1b[31m×\x1b[0m No solution found when resolving dependencies:\n  ╰─▶ unsatisfiable."

    def _fail(argv, *a, **kw):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(argv, 1, "", raw)

    monkeypatch.setattr(cli_server.subprocess, "run", _fail)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "No solution found when resolving dependencies" in out
    assert "\x1b[" not in out


def test_pip_kind_no_installer_available_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    from gideon.operations._installer import NoInstallerError

    _policy(monkeypatch, tmp_path, channel="stable", releases=["v9.9.9"])

    def _none(args):  # type: ignore[no-untyped-def]
        raise NoInstallerError("no pip, no uv")

    monkeypatch.setattr("gideon.operations._installer.install_argv", _none)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    assert "no pip, no uv" in capsys.readouterr().out
    assert not spawns


def test_container_kind_prints_the_two_commands_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    out = capsys.readouterr().out
    for cmd in su.container_instructions():
        assert cmd in out
    assert not git.calls and not spawns


def test_desktop_kind_delegates_to_the_app_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "desktop")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    out = capsys.readouterr().out
    assert "desktop install" in out
    assert "Ask your Gideon administrator for the current desktop release." in out
    assert not git.calls and not spawns


def test_container_env_beats_a_git_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """A container built from a checkout must not run the git pipeline."""
    _as_git_checkout(monkeypatch, tmp_path)
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "container install" in capsys.readouterr().out
    assert not git.calls and not spawns
