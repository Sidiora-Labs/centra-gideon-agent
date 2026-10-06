from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gideon.extensions.apps import code_provenance
from gideon.extensions.apps.manifest import AppManifest, ProviderConfig
from gideon.extensions.apps.native_contract import load_bundle_module
from gideon.extensions.providers.loader import _load_ext_module
from gideon.extensions.providers.registry import RegisteredProvider


@pytest.fixture
def app_source(tmp_path):
    app = "provenance-test-" + tmp_path.name
    root = tmp_path / "app"
    root.mkdir()
    yield app, root
    code_provenance.release(app)
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if isinstance(origin, str) and Path(origin).is_relative_to(tmp_path):
            sys.modules.pop(name, None)


def test_module_execution_callbacks_and_logs_have_loader_owner(app_source):
    app, root = app_source
    source = root / "provider.py"
    source.write_text(
        "from gideon.extensions.apps.code_provenance import owner\n"
        "at_import = owner()\n"
        "def callback():\n    return owner()\n"
    )
    module = load_bundle_module(root, app, "provider")
    assert module.at_import == app
    assert module.callback() == app
    assert code_provenance.owner() is None
    assert code_provenance.loaded_app(str(source)) == app
    assert load_bundle_module(root, app, "provider") is module
    code_provenance.release(app)
    assert module.callback() is None
    assert code_provenance.loaded_app(str(source)) is None


def test_unregistered_globals_cannot_claim_a_registered_filename(app_source):
    app, root = app_source
    source = root / "provider.py"
    source.write_text("value = 1\n")
    module = load_bundle_module(root, app, "provider")
    forged = {"__name__": module.__name__, "__file__": str(source)}
    exec(
        compile(
            "from gideon.extensions.apps.code_provenance import owner\n"
            "def callback():\n    return owner()\n",
            str(source),
            "exec",
        ),
        forged,
    )
    assert forged["callback"]() is None


def test_failed_load_rolls_back_owner_and_cached_module(app_source):
    app, root = app_source
    source = root / "provider.py"
    source.write_text("raise ValueError('failed module')\n")
    with pytest.raises(ValueError, match="failed module"):
        load_bundle_module(root, app, "provider")
    assert code_provenance.loaded_app(str(source)) is None
    assert not any(
        getattr(module, "__file__", None) == str(source)
        for module in sys.modules.values()
    )


def test_symlink_module_outside_app_cannot_acquire_owner(app_source, tmp_path):
    app, root = app_source
    outside = tmp_path / "outside.py"
    outside.write_text("raise AssertionError('must not execute')\n")
    (root / "provider.py").symlink_to(outside)
    with pytest.raises(ImportError, match="outside"):
        load_bundle_module(root, app, "provider")
    assert code_provenance.loaded_app(str(outside)) is None


def test_installed_dotted_package_uses_same_registered_owner(app_source, monkeypatch):
    app, _root = app_source
    from gideon.extensions.apps.manager import app_dir

    monkeypatch.setenv("GIDEON_HOME", str(_root.parent / "home"))
    root = app_dir(app)
    package = "package_" + app.replace("-", "_")
    folder = root / package
    folder.mkdir(parents=True)
    source = folder / "__init__.py"
    source.write_text(
        "from gideon.extensions.apps.code_provenance import owner\n"
        "at_import = owner()\n"
        "def callback():\n    return owner()\n"
    )
    extension = RegisteredProvider(
        name=app,
        manifest=AppManifest(name=app),
        provider_config=ProviderConfig(type="tool", implementation=package + ":callback"),
    )
    module = _load_ext_module(extension, package)
    assert module.at_import == app
    assert module.callback() == app
    assert code_provenance.loaded_app(str(source)) == app


def test_imported_core_module_body_keeps_core_identity(app_source, tmp_path, monkeypatch):
    app, root = app_source
    library = "library_" + app.replace("-", "_")
    (tmp_path / (library + ".py")).write_text(
        "from gideon.extensions.apps.code_provenance import owner\n"
        "at_import = owner()\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    source = root / "provider.py"
    source.write_text(
        "from gideon.extensions.apps.code_provenance import owner\n"
        f"import {library}\n"
        f"library_owner = {library}.at_import\n"
        "def callback():\n    return owner()\n"
    )
    module = load_bundle_module(root, app, "provider")
    assert module.callback() == app
    assert module.library_owner is None
    assert code_provenance.loaded_app(code_provenance.__file__) is None
    with pytest.raises(ImportError, match="Core code"):
        with code_provenance.loading(app, Path(code_provenance.__file__).parents[2]):
            pass
