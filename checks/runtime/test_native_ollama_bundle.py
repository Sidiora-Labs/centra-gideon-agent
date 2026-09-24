"""Offline native bundle registration and installed-package regression rails."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, manager
from gideon.extensions.providers import loader
from gideon.extensions.providers.registry import ProviderRegistry
from gideon.sdk.embedding import EmbeddingProvider
from gideon.sdk.local_model import LocalModelProvider
from gideon.sdk.model import ModelProvider, StructuredOutput, get_default_registry

ROOT = Path(__file__).resolve().parents[2]
BUNDLED = ROOT / "runtime/gideon/extensions/apps/native"
BUNDLE = BUNDLED / "ollama-models"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_installed_bundle_loads_through_real_provider_loader(tmp_path, monkeypatch):
    import gideon.core.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    assert "ollama-models" in app_manager.seed_builtin_apps()
    manifest = app_manager._manifest_of("ollama-models")
    assert manifest.native
    registry = ProviderRegistry()
    registry.register(manifest)
    ext = registry.get("ollama-models")
    factory = loader.load_factory(ext)
    provider = factory({"model": "local-chat", "embedding_model": "local-embed"})
    assert isinstance(provider, (ModelProvider, EmbeddingProvider, LocalModelProvider))
    assert provider.model == "local-chat"
    assert provider.embedding_model == "local-embed"
    assert (
        get_default_registry().capability_of("ollama").structured_output
        is StructuredOutput.JSON_SCHEMA
    )
    assert get_default_registry().catalog_of("ollama") is not None
    assert manager._read_installed("ollama-models").origin == "builtin"
    assert app_manager.seed_builtin_apps() == []
    assert app_manager._is_native("ollama-models")


@pytest.mark.asyncio
async def test_provider_rejects_missing_models_without_network():
    module = _load(BUNDLE / "provider.py", "gideon_test_ollama_bundle")
    provider = module.create_provider()
    with pytest.raises(ValueError, match="chat model"):
        await anext(provider.complete([{"role": "user", "content": "hello"}]))
    with pytest.raises(ValueError, match="embedding model"):
        await provider.embed("hello")
    assert await provider.embed_batch([]) == []
    await provider.shutdown()


def test_bundle_is_sdk_only_and_package_data_declares_all_surfaces():
    tree = ast.parse((BUNDLE / "provider.py").read_text())
    modules = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert all(
        not name.startswith("gideon.") or name.startswith("gideon.sdk.")
        for name in modules
    )
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["gideon"]
    for filename in ("provider.py", "LICENSE", "README.md"):
        path = BUNDLE / filename
        assert path.is_file()
        relative = path.relative_to(ROOT / "runtime/gideon")
        assert any(relative.match(pattern) for pattern in patterns)
    assert (
        "recursive-include runtime/gideon/extensions/apps/native *.py LICENSE README.md"
        in (ROOT / "MANIFEST.in").read_text()
    )


def test_native_bundle_licence_census():
    declarations = {
        path.parent.name: json.loads(path.read_text()).get("license")
        for path in BUNDLED.glob("*/app.json")
    }
    assert len(declarations) >= 25
    assert declarations["ollama-models"] == "Apache-2.0"
    for name, license_name in declarations.items():
        if license_name:
            text = (BUNDLED / name / "LICENSE").read_text()
            assert "Apache License" in text or "MIT License" in text


def test_typecheck_bundles_individually():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tooling/scripts/lint_bundled_apps.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_direction_exemption_is_only_native_tree():
    path = ROOT / "tooling/scripts/generate_structural_baseline.py"
    tree = ast.parse(path.read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "scan_upward_edges"
    )
    guard = ast.unparse(function)
    assert "'extensions' / 'apps' / 'native'" in guard
    assert "path.is_relative_to" in guard


def test_native_bundle_mypy_gates_preserve_coverage():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    excluded = re.compile(config["tool"]["mypy"]["exclude"])
    assert excluded.search(
        "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    assert excluded.search(
        "runtime/gideon/extensions/apps/native/gideon-ui-docs/provider.py"
    )
    for path in (
        "runtime/gideon/extensions/apps/manager.py",
        "runtime/gideon/extensions/apps/native_contract.py",
        "runtime/gideon/integrations/llm/registry.py",
        "checks/harness/provider.py",
    ):
        assert not excluded.search(path), path
    makefile = (ROOT / "Makefile").read_text()
    lint = makefile.split("\nlint:\n", 1)[1].split("\n\n", 1)[0]
    assert "$(PYTHON) -m mypy $(PKG) $(HARNESS)" in lint
    assert "$(PYTHON) tooling/scripts/lint_bundled_apps.py" in lint
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert 'run_tool "mypy" uv run mypy runtime/gideon checks/harness' in workflow
    assert (
        'run_tool "mypy native bundles" uv run python tooling/scripts/lint_bundled_apps.py'
        in workflow
    )
    runner = _load(ROOT / "tooling/scripts/lint_bundled_apps.py", "gideon_bundle_lint")
    groups = runner.bundle_sources()
    assert len(groups) >= runner.MIN_BUNDLES >= 2
    assert {path for group in groups for path in group} == {
        path for path in BUNDLED.rglob("*.py") if "__pycache__" not in path.parts
    }
    for group in groups:
        assert len({path.relative_to(BUNDLED).parts[0] for path in group}) == 1
