"""Every npm workspace that HAS tests must be a tier CI actually runs.

Measured gap (2026-08-18): ``apps/desktop/`` had been an npm workspace member with a
``test`` script and 92 ``node --test`` cases since it landed, and **no workflow ran
them** — ``grep -rln desktop .github/workflows/`` found nothing, the root
``package.json`` had ``test:web`` but no ``test:desktop``, and
``tooling/scripts/run_prepush.sh`` did not run them either. The tier was enforced only by
whoever remembered to type ``npm test`` inside ``apps/desktop/``. That includes
``apps/desktop/test/packaging.test.js``, which is what catches an ``electron-builder``
``build.files`` list that no longer matches ``main.js``'s requires — a dmg that
crashes on launch.

The rails below make that class of gap loud instead of silent: a new workspace
member carrying a ``test`` script must also be reachable through a root
``test:*`` script and a CI step that runs it, or this file reds.

Parsed from source, not executed — node is not a test dependency of the Python
suite (same convention as ``checks/runtime/test_desktop_seam.py``'s vocabulary rail).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROOT_PKG = REPO_ROOT / "package.json"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _root_manifest() -> dict:
    return json.loads(ROOT_PKG.read_text(encoding="utf-8"))


def _tested_workspaces() -> list[str]:
    """Workspace members whose own package.json declares a ``test`` script."""
    tested = []
    for name in _root_manifest()["workspaces"]:
        manifest = REPO_ROOT / name / "package.json"
        assert manifest.is_file(), f"workspace {name!r} has no package.json"
        if (
            json.loads(manifest.read_text(encoding="utf-8"))
            .get("scripts", {})
            .get("test")
        ):
            tested.append(name)
    return tested


def _ci_run_commands() -> list[str]:
    """Every single-line ``run:`` command in ci.yml, whitespace-normalized.

    A deliberately dumb line scan rather than a YAML parse: PyYAML is not a
    declared test dependency, and the shape being asserted is "a step's command
    is exactly this string", which the raw line carries.
    """
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        m = re.match(r"\s*run:\s*(\S.*?)\s*$", line)
        if m and m.group(1) not in {"|", ">", ">-", "|-"}:
            out.append(m.group(1))
    return out


def test_the_run_command_scan_is_not_vacuous():
    """Vacuity floor: if the scan below matched nothing, every rail here would pass.

    ``npm run test:web`` is a long-standing single-line step, and ``uv run pytest`` opens
    every pytest step, so their absence means the scan broke, not that CI changed. The
    pytest step is matched by PREFIX rather than exact string: since #2720 the `test` job
    is sharded, so the bare ``uv run pytest`` became
    ``uv run pytest --no-cov -q --splits 4 --group N …`` — still a single-line pytest step
    the scan must see, just no longer a flagless one.
    """
    commands = _ci_run_commands()
    assert len(commands) >= 10, f"only {len(commands)} run: commands found — scan broke"
    assert "npm run test:web" in commands
    assert any(c.startswith("uv run pytest") for c in commands)


def _root_test_scripts() -> dict[str, str]:
    """Every root ``test:*`` script, keyed by script name."""
    return {
        key: value
        for key, value in _root_manifest()["scripts"].items()
        if key.startswith("test:")
    }


def _scripts_reaching(name: str) -> list[str]:
    """Root ``test:*`` script names that run ``--workspace=<name>``."""
    return [
        key
        for key, value in _root_test_scripts().items()
        if f"--workspace={name}" in value
    ]


def test_every_tested_workspace_has_a_root_test_script():
    """npm runs from the REPO ROOT (single-root lockfile, npm/cli#4828).

    So a workspace's tests are only reachable in CI through a root ``test:*``
    script — there is no ``cd desktop && npm ci``. The script name need not equal
    the workspace directory: ``test:web`` reaches ``apps/console``.
    """
    tested = _tested_workspaces()
    assert set(tested) >= {
        "apps/console",
        "apps/desktop",
        "apps/mobile",
    }, f"workspace discovery broke: {tested}"

    for name in tested:
        assert _scripts_reaching(name), (
            f"{name}/package.json declares a `test` script but no root package.json "
            f"`test:*` script runs it via `--workspace={name}` — nothing in CI can "
            "reach that tier."
        )


def test_ci_runs_every_tested_workspace_tier():
    """A root script nobody invokes is not enforcement."""
    commands = _ci_run_commands()
    for name in _tested_workspaces():
        scripts = _scripts_reaching(name)
        assert any(f"npm run {key}" in commands for key in scripts), (
            f"no ci.yml step runs a root test script for {name} "
            f"({scripts or 'none found'}) — {name}/'s tests are enforced only by "
            "whoever remembers to run them locally."
        )
