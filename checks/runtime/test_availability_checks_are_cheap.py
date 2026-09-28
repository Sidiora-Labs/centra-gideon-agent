"""SDK availability checks inspect package metadata without importing app dependencies."""
import sys
from pathlib import Path


def _plant_package(root: Path, name: str) -> Path:
    marker = root / f"{name}.imported"
    package = root / name
    package.mkdir()
    (package / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    return marker


def test_sdk_finds_package_without_running_it(tmp_path, monkeypatch):
    from gideon.sdk.availability import missing_modules, modules_installed

    marker = _plant_package(tmp_path, "availability_probe_package")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert modules_installed("availability_probe_package")
    assert not marker.exists()
    assert "availability_probe_package" not in sys.modules
    assert missing_modules("availability_probe_package", "missing_availability_package") == ["missing_availability_package"]


def test_missing_dotted_parent_is_reported_as_missing():
    from gideon.sdk.availability import missing_modules, modules_installed

    assert missing_modules("missing_availability_parent.child") == ["missing_availability_parent.child"]
    assert not modules_installed("missing_availability_parent.child")
