"""Regression tests for issue #294 — the dashboard file-explorer roots must follow
the ACTIVE Gideon home (``config_dir()``), never a hardcoded ``~/.gideon``.

Before the fix, ``_dashboard_roots`` added its "Uploads" and "Gideon" roots via
``os.path.expanduser("~/.gideon[/uploads]")``. On a gateway running with a custom
``GIDEON_HOME`` (every dev instance), those two roots resolved to the developer's
REAL home — and because the same roots feed the WRITE allowlist in
``_validate_dashboard_path``, the dashboard could browse AND edit the real home instead
of the active one.

These tests configure isolated home directories and assert every surfaced root resolves
inside the active home or an explicitly resolved workspace.
"""

import os

import pytest


def _under(path: str, root: str) -> bool:
    """True if ``path`` IS ``root`` or lives beneath it (both expected realpath'd)."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


@pytest.fixture
def _isolated_home(tmp_path, monkeypatch):
    """Resolve the active home from an isolated process environment."""
    import gideon.core.config.loader as cfg

    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    assert cfg.config_dir() == tmp_path.resolve()
    return tmp_path


def test_roots_follow_active_home_not_real_gideon(_isolated_home, tmp_path):
    from gideon.core.config.loader import workspace_root
    from gideon.interfaces.dashboard.handlers.files import _dashboard_roots

    active_home = os.path.realpath(str(tmp_path))
    real_home = os.path.realpath(os.path.expanduser("~/.gideon"))
    assert active_home != real_home and not _under(active_home, real_home)

    roots = _dashboard_roots()

    assert roots, "_dashboard_roots() returned no roots"

    ws_root = os.path.realpath(str(workspace_root()))
    allowed_bases = (active_home, ws_root)

    for label, rp in roots:
        assert not _under(rp, real_home), (
            f"root {label!r} -> {rp!r} resolves under the REAL home {real_home!r}; "
            "dashboard roots must follow the ACTIVE home (#294)"
        )
        assert any(_under(rp, base) for base in allowed_bases), (
            f"root {label!r} -> {rp!r} is outside the active home {active_home!r} "
            f"and the workspace root {ws_root!r}"
        )


def test_files_roots_never_expose_active_home(_isolated_home, tmp_path):
    """Writable roots stay in the active home without exposing the home itself."""
    from gideon.core.config.loader import workspace_root
    from gideon.interfaces.dashboard.handlers.files import _dashboard_roots

    active_home = os.path.realpath(str(tmp_path))
    ws_root = os.path.realpath(str(workspace_root()))
    rp_by_label = {label: rp for label, rp in _dashboard_roots()}

    assert "Uploads" in rp_by_label, f"no Uploads root; labels={list(rp_by_label)}"
    assert rp_by_label["Uploads"] == os.path.realpath(
        os.path.join(active_home, "uploads")
    ), f"Uploads must live under the active home; got {rp_by_label['Uploads']!r}"

    assert active_home not in rp_by_label.values(), f"active home exposed as a Files root: {rp_by_label}"
    assert all(_under(root, active_home) or root == ws_root for root in rp_by_label.values())


def test_workspace_equal_to_home_or_symlink_alias_is_never_a_files_root(_isolated_home, tmp_path, monkeypatch):
    from gideon.interfaces.dashboard.handlers.files import _dashboard_roots

    home = _isolated_home.resolve()
    alias = tmp_path / "home-alias"
    alias.symlink_to(home, target_is_directory=True)
    for configured in (str(home), str(alias)):
        monkeypatch.setenv("GIDEON_WORKSPACE", configured)
        roots = _dashboard_roots()
        assert all(path != str(home) for _, path in roots), roots
