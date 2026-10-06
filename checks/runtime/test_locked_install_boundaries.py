"""Distribution environments use locked dependencies and explicit interpreters."""

import hashlib
import shlex
import subprocess
import tomllib
from pathlib import Path

import yaml
from packaging.specifiers import SpecifierSet

_REPO = Path(__file__).resolve().parents[2]


def test_container_installs_only_hashed_locked_runtime_and_test_exports():
    recipe = (_REPO / "infrastructure/docker/Dockerfile.backend").read_text()
    assert "COPY pyproject.toml uv.lock ./" in recipe
    assert "uv export --locked --no-emit-project --emit-index-url" in recipe
    assert recipe.count("--require-hashes --no-deps") == 2
    assert "--extra channels" in recipe
    assert "--extra dev" in recipe
    assert '".[slack,' not in recipe
    assert "--upgrade pip" not in recipe
    assert "GIDEON_PREBUILT_HYPERMID_DAEMON=/tmp/hypermid-daemon" in recipe
    assert "FROM ${RUST_IMAGE} AS hypermid-builder" in recipe
    assert "COPY apps/assistant/ apps/assistant/" in recipe


def test_desktop_runtime_installs_from_lock_before_tooling():
    workflow = yaml.safe_load((_REPO / ".github/workflows/release.yml").read_text())
    for job in ("desktop-linux", "desktop-mac"):
        runs = [step["run"] for step in workflow["jobs"][job]["steps"] if "run" in step]
        install = next(run for run in runs if "uv sync" in run)
        assert "uv sync --locked --no-editable --python 3.12" in install
        assert "--extra anthropic --extra openai --extra slack" in install
        assert "uv pip install --python .venv/bin/python pyinstaller" in install
        assert "uv pip install --python .venv/bin/python '.[" not in install
        assert install.index("npm run build --workspace=apps/console") < install.index(
            "uv sync"
        )


def test_release_shell_blocks_and_website_are_syntactically_valid():
    workflow = yaml.safe_load((_REPO / ".github/workflows/release.yml").read_text())
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "run" not in step or step.get("shell", "bash").startswith("python"):
                continue
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
    subprocess.run(
        ["sh", "-n", str(_REPO / "infrastructure/website/install.sh")], check=True
    )


def test_website_selects_supported_python_preserving_explicit_source_and_digest():
    path = _REPO / "infrastructure/website/install.sh"
    installer = path.read_text()
    assert 'GIDEON_PYTHON="3.13"' in installer
    assert (
        'uv tool install --upgrade --python "$GIDEON_PYTHON" --constraints "$constraints" "$GIDEON_PACKAGE"'
        in installer
    )
    assert 'GIDEON_PACKAGE="${GIDEON_PACKAGE_SOURCE:-}"' in installer
    assert "ensure_hypermid_toolchain" in installer
    project = tomllib.loads((_REPO / "pyproject.toml").read_text())
    assert "3.13" in SpecifierSet(project["project"]["requires-python"])
    expected = (
        (_REPO / "infrastructure/website/install.sh.sha256").read_text().split()[0]
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_native_dependency_bounds_and_locked_versions_preserve_supported_runtime():
    project = tomllib.loads((_REPO / "pyproject.toml").read_text())
    lock = tomllib.loads((_REPO / "uv.lock").read_text())
    assert "pip>=22.3" in project["project"]["dependencies"]
    assert "av>=11,<19" in project["project"]["optional-dependencies"]["stt"]
    packages = lock["package"]
    assert [p["version"] for p in packages if p["name"] == "av"] == ["18.0.0"]
    torch = [p for p in packages if p["name"] == "torch"]
    assert {p["version"] for p in torch} == {"2.13.0", "2.13.0+cpu"}
    assert any(
        p["source"].get("registry") == "https://download.pytorch.org/whl/cpu"
        for p in torch
    )
    assert "pytorch-cpu" == project["tool"]["uv"]["sources"]["torch"][0]["index"]
    assert project["tool"]["uv"]["index"][0]["explicit"] is True
