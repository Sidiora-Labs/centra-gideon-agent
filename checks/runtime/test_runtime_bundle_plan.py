import json
from pathlib import Path

from tooling.packaging.runtime_bundle import (
    RuntimeBundlePlan,
    require_native_interpreter,
)

REPOSITORY = Path(__file__).resolve().parents[2]
RECIPE = REPOSITORY / "tooling" / "packaging" / "runtime-bundle.spec"


def test_bundle_carries_the_complete_runtime_and_console():
    plan = RuntimeBundlePlan.for_recipe(RECIPE)
    resources = plan.runtime_resources()
    destinations = {
        Path(destination) / Path(source).name: Path(source)
        for source, destination in resources
        if Path(source).is_file()
    }
    runtime = REPOSITORY / "runtime"
    shipped = {
        path.relative_to(runtime): path
        for path in (runtime / "gideon").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }
    assert shipped
    assert destinations == shipped
    assert destinations[Path("gideon/security/baseline_denylist.json")].is_file()
    assert (str(REPOSITORY / "apps/console/dist"), "gideon/static/dist") in resources
    assert len(resources) == len(shipped) + 1
    assert all(source.is_absolute() for source in destinations.values())


def test_dynamic_provider_entrypoints_reach_the_frozen_import_inventory():
    plan = RuntimeBundlePlan.for_recipe(RECIPE)
    manifests = sorted(
        (REPOSITORY / "runtime/gideon/extensions/apps/native").glob("*/app.json")
    )
    assert manifests
    required = set()
    for path in manifests:
        provider = json.loads(path.read_text()).get("provider") or {}
        implementation = provider.get("implementation", "").split(":", 1)[0].strip()
        if implementation:
            required.add(implementation)
            required.add(implementation.rsplit(".", 1)[0])
    assert required
    assert set(plan.provider_imports()) == required


def test_recipe_paths_do_not_depend_on_the_invocation_directory(tmp_path, monkeypatch):
    plan = RuntimeBundlePlan.for_recipe(RECIPE)
    resources = plan.runtime_resources()
    modules = plan.provider_imports()
    monkeypatch.chdir(tmp_path)
    relocated = RuntimeBundlePlan.for_recipe(RECIPE)
    assert relocated.repository == REPOSITORY
    assert relocated.entrypoint.is_file()
    assert relocated.runtime_resources() == resources
    assert relocated.provider_imports() == modules
    require_native_interpreter()
