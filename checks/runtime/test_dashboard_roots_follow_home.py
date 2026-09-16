"""Regression tests for issue #294 — the dashboard file-explorer roots must follow
the ACTIVE Gideon home (``config_dir()``), never a hardcoded ``~/.gideon``.

Before the fix, ``_dashboard_roots`` added its "Uploads" and "Gideon" roots via
``os.path.expanduser("~/.gideon[/uploads]")``. On a gateway running with a custom
``GIDEON_HOME`` (every dev instance), those two roots resolved to the developer's
REAL home — and because the same roots feed the WRITE allowlist in
``_validate_dashboard_path``, the dashboard could browse AND edit the real home instead
of the active one.

These tests monkeypatch the active home to an isolated ``tmp_path`` exactly the way the
sibling suite (``checks/runtime/test_dashboard_file_io.py``) does — patching ``config_dir`` on the
loader module, NEVER the real home — and assert every surfaced root resolves inside the
active home or an explicitly-resolved workspace, and none under the real ``~/.gideon``.
"""

import os

import pytest


def _under(path: str, root: str) -> bool:
    """True if ``path`` IS ``root`` or lives beneath it (both expected realpath'd)."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


@pytest.fixture
def _isolated_home(tmp_path, monkeypatch):
    """Point the active Gideon home at an isolated tmp dir.

    Mirrors ``TestBoundProjectWorkspaceRoot._isolated_home`` in
    ``checks/runtime/test_dashboard_file_io.py``: patch ``config_dir`` on the loader module and
    nothing else. ``files.py`` imports ``config_dir`` at call time inside
    ``_dashboard_roots``, so the patch is picked up per request.
    """
    import gideon.core.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
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


def test_uploads_and_home_roots_resolve_under_active_home(_isolated_home, tmp_path):
    """Pin the two roots the fix touches: Uploads -> <active_home>/uploads and the
    config/data tree root -> <active_home> (surfaced as "Home"; the "Gideon"
    factory now resolves to the same path and de-dupes into it)."""
    from gideon.interfaces.dashboard.handlers.files import _dashboard_roots

    active_home = os.path.realpath(str(tmp_path))
    rp_by_label = {label: rp for label, rp in _dashboard_roots()}

    assert "Uploads" in rp_by_label, f"no Uploads root; labels={list(rp_by_label)}"
    assert rp_by_label["Uploads"] == os.path.realpath(
        os.path.join(active_home, "uploads")
    ), f"Uploads must live under the active home; got {rp_by_label['Uploads']!r}"

    assert (
        active_home in rp_by_label.values()
    ), f"active home {active_home!r} not surfaced as a dashboard root; got {rp_by_label}"
