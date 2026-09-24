from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_release_wires_native_unsigned_build_smoke_and_upload():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    job = workflow["jobs"]["desktop-mac"]
    assert job["runs-on"] == "macos-14"
    assert job["env"]["CSC_IDENTITY_AUTO_DISCOVERY"] == "false"
    steps = job["steps"]
    commands = [s.get("run", "") for s in steps]
    build = next(
        i
        for i, c in enumerate(commands)
        if ".venv/bin/pyinstaller tooling/packaging/runtime-bundle.spec" in c
    )
    package = next(
        i
        for i, c in enumerate(commands)
        if "--prepackaged dist/mac-arm64/Gideon.app" in c
    )
    smoke = next(i for i, c in enumerate(commands) if "smoke_desktop_mac.sh" in c)
    upload = next(
        i
        for i, s in enumerate(steps)
        if s.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert build < package < smoke < upload
    assert "--dir" in commands[build] and "--config.asar=false" in commands[build]
    assert 'test "$(uname -m)" = arm64' in "\n".join(commands)
    for index in (build, package):
        assert "--arm64" in commands[index]
        assert "--config.mac.identity=null" in commands[index]
        assert "--publish never" in commands[index]
    assert steps[upload]["with"] == {
        "name": "desktop-mac",
        "path": "apps/desktop/dist/*.dmg",
        "if-no-files-found": "error",
    }
    assert not any(s.get("continue-on-error") for s in steps)
    notes = workflow["jobs"]["notes"]
    assert {"desktop-mac", "desktop-linux"} <= set(notes["needs"])
    assert "needs.desktop-mac.result == 'success'" in notes["if"]
    collector = next(
        s for s in notes["steps"] if s.get("with", {}).get("pattern") == "desktop-*"
    )
    assert collector["with"]["merge-multiple"] is True
    assert collector["with"]["path"] == "desktop-dist/"
    assert "desktop-dist/*" in "\n".join(s.get("run", "") for s in notes["steps"])


def test_smoke_script_syntax_and_native_probes():
    script = ROOT / "tooling/scripts/smoke_desktop_mac.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    source = script.read_text()
    for required in (
        "-readonly -nobrowse",
        "trap cleanup EXIT",
        "hdiutil detach",
        "Contents/Resources/app/package.json",
        'lipo -archs "$shell_binary"',
        'lipo -archs "$backend"',
        "ELECTRON_RUN_AS_NODE=1",
        'export GIDEON_HOME="$smoke_dir/home"',
        '"$backend" --version',
    ):
        assert required in source
    if platform.system() != "Darwin":
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
        assert result.returncode == 1
        assert "requires macOS" in result.stderr


def test_desktop_documentation_and_makefile_explain_unsigned_contract():
    guide = (ROOT / "docs/guides/DESKTOP.md").read_text()
    makefile = (ROOT / "Makefile").read_text()
    assert "unsigned arm64 DMG" in guide and "unsigned arm64 DMG" in makefile
    assert "Developer ID signing or notarization" in guide
    assert "desktop-*" in guide and "read-only" in guide


def test_dependency_install_uses_actual_independent_pnpm_lockfiles():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    steps = workflow["jobs"]["desktop-mac"]["steps"]
    setup_pnpm = next(
        i
        for i, step in enumerate(steps)
        if step.get("uses", "").startswith("pnpm/action-setup@")
    )
    setup_node = next(
        i
        for i, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/setup-node@")
    )
    assert setup_pnpm < setup_node
    assert steps[setup_pnpm]["with"]["version"] == 9
    assert steps[setup_node]["with"]["cache"] == "pnpm"
    expected_locks = {
        "pnpm-lock.yaml",
        "apps/console/pnpm-lock.yaml",
        "apps/desktop/pnpm-lock.yaml",
    }
    assert (
        set(steps[setup_node]["with"]["cache-dependency-path"].splitlines())
        == expected_locks
    )
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "npm ci" not in commands and "make desktop" not in commands
    assert "pnpm --dir apps/console run build" in commands
    assert "cp -R dist/gideon-backend apps/desktop/backend-dist/" in commands
    assert "pnpm --dir apps/desktop run dist --arm64" in commands
    for lock_path in sorted(expected_locks):
        lock = yaml.safe_load((ROOT / lock_path).read_text())
        package = json.loads((ROOT / lock_path).with_name("package.json").read_text())
        importer = lock["importers"]["."]
        for group in ("dependencies", "devDependencies"):
            for name, version in package.get(group, {}).items():
                assert importer[group][name]["specifier"] == version
        directory = Path(lock_path).parent
        prefix = "pnpm" if directory == Path(".") else f"pnpm --dir {directory}"
        assert f"{prefix} install --frozen-lockfile" in commands
    install = next(
        i
        for i, step in enumerate(steps)
        if "pnpm install --frozen-lockfile" in step.get("run", "")
    )
    build = next(
        i
        for i, step in enumerate(steps)
        if "pnpm --dir apps/console run build" in step.get("run", "")
    )
    assert setup_node < install < build
