"""The console's Vite pin and the lockfile that resolves it must agree (#79).

The bump from 8.2.2 to 8.3.0 is only half a dependency change: a manifest range that the
committed ``package-lock.json`` still resolves to the old release installs the old release,
because ``npm ci`` reads the lock and not the range. So both sides are asserted here, from
the committed files — no install, no network.

Read rather than executed, the ``test_desktop_install_kind.py`` precedent: node is not a
Python-test dependency and these are declarations, not behaviour.

🪤 The tree also carries a SECOND vite (``node_modules/vite``, currently 6.x). It is not a
workspace's dependency — npm hoisted it to satisfy the ``vite`` *peerDependency* of the
root-hoisted ``vitest`` / ``@vitest/mocker`` / ``@tailwindcss/vite``, and the lock marks it
``"peer": true``. The console's own build and tests resolve
``apps/console/node_modules/vite``. The last test states that as a rail: any other vite in
the tree must be an auto-installed peer, so a second workspace quietly declaring its own
vite — the way a real version split would appear — reds here.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONSOLE_PKG = REPO_ROOT / "apps/console/package.json"
LOCK = REPO_ROOT / "package-lock.json"

_PINNED = "8.3.0"
_SUPERSEDED = "8.2.2"


def _console_manifest() -> dict:
    return json.loads(CONSOLE_PKG.read_text(encoding="utf-8"))


def _lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def test_the_console_manifest_pins_the_requested_vite() -> None:
    dev = _console_manifest()["devDependencies"]
    assert dev["vite"] == f"^{_PINNED}", dev["vite"]
    assert _SUPERSEDED not in dev["vite"]


def test_the_lock_resolves_the_console_vite_to_the_pinned_release() -> None:
    """The installed version, as ``npm ci`` would place it."""
    entry = _lock()["packages"]["apps/console/node_modules/vite"]
    assert entry["version"] == _PINNED, entry["version"]
    assert entry["resolved"].endswith(f"vite-{_PINNED}.tgz"), entry["resolved"]
    assert entry.get("integrity"), "the lock entry carries no integrity hash"


def test_the_lock_carries_the_same_range_the_manifest_declares() -> None:
    """A lock refreshed for the bump restates the manifest's range on the workspace entry;
    a hand-edited version field would leave the old range behind here."""
    declared = _console_manifest()["devDependencies"]["vite"]
    workspace = _lock()["packages"]["apps/console"]
    assert workspace["devDependencies"]["vite"] == declared


def test_no_workspace_declares_a_second_vite() -> None:
    """Every other vite in the tree must be an auto-installed peer, not a rival pin."""
    lock = _lock()
    workspaces = ("", *lock["packages"][""]["workspaces"])
    for name in workspaces:
        pkg = lock["packages"][name]
        declared = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if name == "apps/console":
            assert declared["vite"] == f"^{_PINNED}"
        else:
            assert "vite" not in declared, f"{name or '(root)'} declares its own vite"

    for path, entry in lock["packages"].items():
        if not path.endswith("node_modules/vite") or path.startswith("apps/console/"):
            continue
        assert entry.get("peer") is True, (
            f"{path}@{entry.get('version')} is an installed, non-peer vite beside the "
            f"console's {_PINNED} pin"
        )
