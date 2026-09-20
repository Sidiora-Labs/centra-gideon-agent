"""No module binds the Gideon home resolver at import time (#2443, #2442).

## The defect this closes

A module-level ``from gideon.core.config.loader import config_dir`` binds whatever that name
pointed at the moment the importing module was first imported. Much of this codebase is imported
LAZILY — on first use, from inside a function — so that moment is not startup. Under test it can
land inside a test that has replaced ``loader.config_dir`` to keep writes out of the real home, and
the importing module then keeps the replacement **forever**: the attribute can be restored on
``loader``, but not in a copy another module already took.

``GIDEON_HOME`` is not a defence. The env var is read by the REAL ``config_dir``; a captured
stand-in never consults it. Measured on #2443: a task store resolved the FIRST test's home while
``GIDEON_HOME`` correctly named the current one, so every record it wrote landed in a
directory nothing ever read back. That surfaced as two assertions in a different file that looked
nothing like a home bug (``assert 0 == 1`` and ``assert 200 == 400``), which is why the same defect
was filed twice as "cross-test pollution".

## The invariant, and why it is absolute rather than a ratchet

#2677 fixed the two modules that had actually been bitten and recorded the remaining 57 as a
baseline that could only shrink. That baseline is now **empty**: every module resolves the home
through a locally DEFINED delegator. So this file asserts the strong form — *no* module binds the
resolver at import — rather than policing a list.

Two properties make a delegator correct, and both are load-bearing:

* **DEFINED, not imported and not aliased.** There is no object to capture. An alias assignment
  (``config_dir = config_loader.config_dir``) captures exactly like the import did, which is why
  :meth:`TestNoModulePinsTheHome.test_no_module_aliases_the_resolver` sits beside the import
  check.
* **The NAME stays on the module.** ``conftest``'s home guard re-points module-level ``config_dir``
  attributes, and several modules deliberately re-export the name as a test patch seam. Keeping the
  name is what let the sweep touch **zero** test files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import gideon

SRC = Path(gideon.__file__).parent
LOADER_MODULE = "gideon.core.config.loader"
HOME_RESOLVERS = {"config_dir", "config_path"}

OWNER = SRC / "core" / "config" / "loader.py"

#: `packages/python-client/channel.py` and `packages/python-client/util.py` are pure published surfaces, and
SDK_FACADE_EXEMPT = frozenset(
    {
        "gideon.sdk.channel",
        "gideon.sdk.util",
    }
)


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    return ("gideon." + str(rel).replace("/", ".")).replace(".__init__", "")


def _sources() -> list[tuple[Path, ast.Module]]:
    out = []
    for f in sorted(SRC.rglob("*.py")):
        if f == OWNER:
            continue
        try:
            out.append((f, ast.parse(f.read_text(encoding="utf-8"))))
        except SyntaxError:  # pragma: no cover - the lint gate owns syntax
            continue
    return out


def _binders(trees: list[tuple[Path, ast.Module]]) -> list[str]:
    """Modules with a MODULE-LEVEL ``from ...loader import config_dir/config_path``."""
    hits = []
    for f, tree in trees:
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == LOADER_MODULE:
                if {a.name for a in node.names} & HOME_RESOLVERS:
                    hits.append(_module_name(f))
    return hits


def _aliasers(trees: list[tuple[Path, ast.Module]]) -> list[str]:
    """Modules doing ``config_dir = <something>.config_dir`` at module level.

    An alias captures the object exactly like the import did, so it is the same defect wearing a
    different spelling — and it would slip straight past the import check.
    """
    hits = []
    for f, tree in trees:
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if not names & HOME_RESOLVERS:
                continue
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr in HOME_RESOLVERS
            ):
                hits.append(f"{_module_name(f)} ({node.value.attr})")
    return hits


class TestTheScannerCanActuallySee:
    """Vacuity floors, as POSITIVE CONTROLS rather than population counts.

    The previous version asserted "more than 40 modules bind the resolver" — a meaningful floor only
    while the defect existed. At zero, a population floor has to be deleted, and a deleted floor is
    how a scanner rots into always-green. A control that feeds the detector a known-bad synthetic
    source keeps working at zero.
    """

    def test_the_binder_detector_flags_a_known_binder(self):
        tree = ast.parse("from gideon.core.config.loader import config_dir\n")
        assert _binders([(SRC / "synthetic.py", tree)]), "binder detector is blind"

    def test_the_alias_detector_flags_a_known_alias(self):
        tree = ast.parse(
            "from gideon.core.config import loader\nconfig_dir = loader.config_dir\n"
        )
        assert _aliasers([(SRC / "synthetic.py", tree)]), "alias detector is blind"

    def test_the_walk_reaches_the_package(self):
        assert (
            len(_sources()) > 500
        ), "source walk found too few modules — the glob broke"


class TestNoModulePinsTheHome:
    def test_no_module_binds_the_resolver_at_import(self):
        hits = sorted(set(_binders(_sources())) - SDK_FACADE_EXEMPT)
        assert not hits, (
            "these modules bind the home resolver at IMPORT time, so a lazy import can pin a "
            "previous caller's resolver and write into the wrong home (#2443/#2442):\n  "
            + "\n  ".join(hits)
            + "\n\nDefine it locally instead:\n"
            "    from gideon.core.config import loader as config_loader\n\n"
            "    def config_dir():\n"
            "        return config_loader.config_dir()\n"
        )

    def test_no_module_aliases_the_resolver(self):
        hits = sorted(set(_aliasers(_sources())))
        assert not hits, (
            "these modules ALIAS the home resolver at module level, which captures the object "
            "exactly like an import does (#2443):\n  " + "\n  ".join(hits)
        )

    def test_the_exemption_cannot_outlive_its_reason(self):
        """An exemption for a module that no longer binds silently widens the rail.

        Two entries is a small enough list to keep honest, and the whole point of this sweep was
        that a list nobody re-measures is how 57 latent instances accumulated in the first place.
        """
        stale = sorted(SDK_FACADE_EXEMPT - set(_binders(_sources())))
        assert not stale, (
            "these modules are exempt but no longer bind the resolver — delete them from "
            "SDK_FACADE_EXEMPT:\n  " + "\n  ".join(stale)
        )


class TestDelegationActuallyFollowsTheLoader:
    """The behavioural half. The structural checks cannot tell a live delegator from a function
    that captured the loader's object internally, so this asserts the property that matters:
    patching the LOADER reaches the module."""

    def test_patching_the_loader_reaches_a_delegating_module(self, tmp_path):
        from unittest.mock import patch

        from gideon.engine.tasks import native

        with patch("gideon.core.config.loader.config_dir", return_value=tmp_path):
            assert native.config_dir() == tmp_path
            assert native._tasks_dir() == tmp_path / "tasks"

    def test_the_name_is_still_on_the_module(self):
        """Keeping the module-level NAME is what let the sweep change zero test files:
        ``conftest``'s home guard re-points it, and several modules re-export it as a documented
        test patch seam. A delegator that removed the name would break both."""
        from gideon.engine import agent_metadata, lifecycle
        from gideon.engine.tasks import native

        for module in (native, agent_metadata, lifecycle):
            assert hasattr(
                module, "config_dir"
            ), f"{module.__name__} lost its patch seam"
            assert (
                module.config_dir.__module__ == module.__name__
            ), f"{module.__name__}.config_dir must be DEFINED there, not re-imported"


def test_lifecycle_re_resolves_loader_after_import(tmp_path, monkeypatch):
    from gideon.core.config import loader
    from gideon.engine import lifecycle

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(loader, "config_dir", Path.cwd)
    assert lifecycle.config_dir() == tmp_path
