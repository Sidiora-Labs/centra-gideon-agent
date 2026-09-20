"""The desktop shell must DECLARE its install kind (`DC-1` T1.3, DISTRIBUTION C1) — #2673.

`self_update.detect_install_kind()` resolves ``GIDEON_INSTALL_KIND`` first, then probes
``GIDEON_PROJECT_DIR`` for a ``.git``, then falls back to ``"pip"``. The ``"desktop"``
member is therefore *produced* by exactly one thing in this repository — the Electron shell's
spawn env — and consumed by three: the Updates panel, ``POST /api/update``, and
``gideon update``.

Nothing produced it. `DC-6` shipped the Linux AppImage/.deb to every GitHub Release while
`DC-1`'s install-kind clause was still open, and `apps/desktop/main.js` set ``PROJECT_DIR`` but not
``INSTALL_KIND`` — so inside the bundle the project dir is ``…/resources`` (no ``.git``) and the
gateway classified itself as a **pip install**. The Updates panel then offered an in-app apply
that runs ``<installer> install -U gideon==<tag>`` against ``sys.executable``, which in a
packaged app is the frozen PyInstaller backend: there is no interpreter there to upgrade.

Every existing test of the desktop kind sets the env var ITSELF
(``checks/runtime/test_self_update.py::test_desktop_env_wins``,
``test_update_apply_kind.py::test_desktop_returns_instructions``,
``test_cli_update_kinds.py::test_desktop_kind_delegates_to_the_app_and_exits_zero``) — a suite
that stayed green over a value no shipped code emitted. These are the rails that read the
producing side.

Parsed from source rather than executed, the ``test_desktop_seam.py`` precedent: node is not a
test dependency, and `apps/desktop/test/gatewayEnv.test.js` already executes the builder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gideon.operations.self_update import INSTALL_KINDS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DESKTOP = REPO_ROOT / "apps/desktop"


def _source(relative: str) -> str:
    path = DESKTOP / relative
    assert path.is_file(), f"apps/desktop/{relative} is missing"
    return path.read_text(encoding="utf-8")


def test_the_shell_declares_the_desktop_install_kind() -> None:
    """🔴 THE #2673 REGRESSION. The env the shell hands the gateway must carry the kind.

    Asserted against the Python taxonomy, not against a bare string: a shell setting
    ``GIDEON_INSTALL_KIND=app`` would be junk that ``detect_install_kind`` ignores,
    falling straight back through to ``pip`` — the very outcome this pins shut.
    """
    src = _source("src/gateway/environment.js")
    m = re.search(r'const INSTALL_KIND = "([^"]+)";', src)
    assert (
        m
    ), 'apps/desktop/src/gateway/environment.js must declare `const INSTALL_KIND = "…"`'
    assert m.group(1) == "desktop", f"the shell declares {m.group(1)!r}"
    assert m.group(1) in INSTALL_KINDS, (
        f"{m.group(1)!r} is not a self_update.INSTALL_KINDS member {INSTALL_KINDS} — "
        "detect_install_kind() would ignore it and classify the app as a pip install"
    )
    assert re.search(r"GIDEON_INSTALL_KIND:\s*INSTALL_KIND", src), (
        "src/gateway/environment.js declares the kind but does not put it in the child "
        "environment"
    )


def test_the_declared_kind_wins_over_an_inherited_one() -> None:
    """The explicit keys must be applied AFTER the inherited env.

    A ``GIDEON_INSTALL_KIND=container`` in the user's shell profile would otherwise make
    a desktop install describe itself as a container and print `docker compose` instructions
    to somebody running an .app. Ordering is the whole mechanism, so it is read as ordering.
    """
    src = _source("src/gateway/environment.js")
    assign = re.search(r"Object\.assign\(child, \{(.*?)\}\)", src, re.S)
    assert (
        assign
    ), "buildGatewayEnv no longer applies the explicit keys over the inherited env"
    inherited = src.index("Object.entries(env)")
    assert "GIDEON_INSTALL_KIND" in assign.group(
        1
    ), "the returned env carries no install kind"
    assert inherited < src.index(assign.group(0)), (
        "the inherited env must be laid down BEFORE the explicit keys, or an inherited "
        "GIDEON_INSTALL_KIND would win"
    )


def _require_closure(entry: Path) -> dict[Path, str]:
    """Every module reachable from ``entry`` through relative ``require`` calls.

    The spawn env moved out of main.js into the application layer, so the seam is read
    the way the shell reads it — follow the chain rather than pinning one file's text.
    """
    pat = re.compile(r"""require\(["'](\.[^"']+)["']\)""")
    seen: dict[Path, str] = {}
    queue = [entry]
    while queue:
        path = queue.pop()
        if path in seen or not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        seen[path] = src
        for spec in pat.findall(src):
            target = path.parent / spec
            queue.append(target if target.suffix else target.with_suffix(".js"))
    return seen


def test_main_js_builds_the_spawn_env_through_the_shared_builder() -> None:
    """main.js must not reconstruct the env inline again.

    The clause was missable in the first place because the env was an object literal buried in
    ``startGateway``, invisible to every test. Keep the one seam: the builder is executed by
    ``apps/desktop/test/gatewayEnv.test.js``, an inline literal is executed by nothing.
    """
    chain = _require_closure(DESKTOP / "src" / "application" / "main.js")
    assert any(
        path.name == "environment.js" and path.parent.name == "gateway"
        for path in chain
    ), "main.js no longer reaches the shared gateway env builder"
    assert any(
        "buildGatewayEnv(" in src for src in chain.values()
    ), "the spawn env is no longer built through buildGatewayEnv"
    for path, src in chain.items():
        if path.name == "environment.js" and path.parent.name == "gateway":
            continue
        for lineno, line in enumerate(src.splitlines(), start=1):
            if re.search(r"GIDEON_DEV_NO_AUTH\s*[:=]", line):
                raise AssertionError(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} sets a spawn-env key directly "
                    "again — those belong in src/gateway/environment.js, where a test can "
                    "see them"
                )


def test_the_shipped_bundle_carries_the_env_builder() -> None:
    """A module absent from ``build.files`` is absent from the packaged app.

    ``apps/desktop/test/packaging.test.js`` walks main.js's requires transitively and would catch
    this too; asserted here as well because the consequence is silent — the app fails at
    launch inside the bundle only, which is the one place nothing runs a test.
    """
    pkg = json.loads(_source("package.json"))
    assert "src/gateway/environment.js" in pkg["build"]["files"]


def _has_electron_updater() -> bool:
    """Whether the shell actually ships an auto-updater (the other half of `DC-1`)."""
    pkg = json.loads(_source("package.json"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return "electron-updater" in deps


def test_no_surface_promises_self_update_while_the_updater_is_unbuilt() -> None:
    """Three surfaces tell a desktop user how to update. None may overstate the shell.

    All three said the app "updates itself" — copy written for `DC-1`'s electron-updater,
    which is not in ``apps/desktop/package.json`` and is not called anywhere in the shell. Under
    #2673's install-kind fix that copy became reachable for the first time, so it had to
    become true rather than merely newly visible.

    WHEN `DC-1` LANDS THE UPDATER this test reds, and that is its second job: restoring the
    self-update wording is then correct, and the same commit should relax this rail. A
    premise-checked claim, not a permanent ban.
    """
    if _has_electron_updater():
        raise AssertionError(
            "apps/desktop/package.json now depends on electron-updater — the shell can self-update. "
            "Restore the self-update wording in the three desktop surfaces (updates.py detail, "
            "UpdatesPanel.tsx, cli_server._update_desktop) and relax this rail in the same "
            "commit."
        )

    surfaces = (
        "runtime/gideon/interfaces/dashboard/handlers/updates.py",
        "apps/console/src/features/settings/UpdatesPanel.tsx",
        "runtime/gideon/interfaces/cli/server.py",
    )
    for relative in surfaces:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "updates itself" not in line:
                continue
            stripped = line.strip()
            assert stripped.startswith(("#", "//", "*", "/*")), (
                f"{relative}:{lineno} tells a desktop user the app updates itself, but the "
                "shell ships no updater"
            )

    for relative in surfaces:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "releases page" in text, f"{relative} does not name the releases page"
    panel = (REPO_ROOT / surfaces[1]).read_text(encoding="utf-8")
    assert (
        "href={releasesUrl()}" in panel
    ), "UpdatesPanel does not link the configured releases URL"
