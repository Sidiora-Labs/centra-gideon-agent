"""Installed app module ownership drives real provider factory teardown."""

import shutil
import sys
from pathlib import Path

import pytest

from gideon.extensions.apps import app_runtime
from gideon.extensions.apps.code_provenance import release
from gideon.extensions.apps.manager import app_dir
from gideon.extensions.apps.native_contract import load_bundle_module
from gideon.integrations.llm import registry


@pytest.mark.asyncio
async def test_owned_factory_actual_unload_and_module_replacement(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(registry, "_default_registry", registry.ProviderRegistry())
    script = tmp_path / "script.json"
    script.write_text("[]")
    monkeypatch.setenv(registry.SCRIPTED_PROVIDER_ENV, str(script))
    assert registry.register_scripted_provider_type()
    catalog = registry.get_default_registry()
    native_factory = catalog._factories["scripted"]
    name = "local-embedding-lifecycle"
    root = app_dir(name)
    root.mkdir(parents=True)
    source = (
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )
    shutil.copyfile(source, root / "provider.py")
    first = load_bundle_module(root, name, "provider")
    module_name = first.__name__
    try:
        catalog.register_entry(
            registry.ProviderEntry(
                name="selected-local",
                type="ollama",
                model="a",
                options={"endpoint": "http://127.0.0.1:9"},
            )
        )
        entry = catalog.get_entry("selected-local")
        old_factory = catalog._factories["ollama"]
        assert catalog._factory_owners["ollama"][1] is first
        actual = catalog.build("selected-local")
        assert isinstance(actual, first.OllamaProvider)
        await actual.shutdown()
        assert catalog.unregister_app_module("another-app", module_name, first) == 0
        assert catalog.unregister_app_module(name, module_name, object()) == 0
        assert catalog._factories["ollama"] is old_factory
        app_runtime.unload(name, None)
        assert module_name not in sys.modules
        assert "ollama" not in catalog._factories
        assert catalog.catalog_of("ollama") is None
        assert catalog.not_ready(entry, implicit=False) is not None
        assert catalog.get_entry("selected-local") is entry
        assert catalog._factories["scripted"] is native_factory
        second = load_bundle_module(root, name, "provider")
        assert second is not first
        assert catalog._factories["ollama"] is not old_factory
        actual = catalog.build("selected-local")
        assert isinstance(actual, second.OllamaProvider)
        await actual.shutdown()
        assert catalog.unregister_app_module(name, module_name, first) == 0
        assert "ollama" in catalog._factories
        app_runtime.unload(name, None)
        assert "ollama" not in catalog._factories
        assert catalog._factories["scripted"] is native_factory
    finally:
        release(name)
        app_runtime._evict_modules(name)
