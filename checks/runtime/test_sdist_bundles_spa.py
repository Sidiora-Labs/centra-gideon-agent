"""Check source archive selection against the runtime and built console files."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

import pytest
from setuptools._distutils.filelist import FileList

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "MANIFEST.in"
_BUNDLE_ROOTS = ("runtime/gideon", "apps/console/dist")


def _archive_selection() -> tuple[set[str], set[str]]:
    available = {"LICENSE"}
    for relative in _BUNDLE_ROOTS:
        available.update(
            path.relative_to(_REPO_ROOT).as_posix()
            for path in (_REPO_ROOT / relative).rglob("*")
            if path.is_file()
        )
    listing = FileList()
    listing.set_allfiles(sorted(available))
    for directive in _MANIFEST.read_text(encoding="utf-8").splitlines():
        if directive.strip() and not directive.lstrip().startswith("#"):
            listing.process_template_line(directive)
    return available, set(listing.files)


def test_manifest_exists() -> None:
    assert _MANIFEST.is_file(), "Source archives require MANIFEST.in."


@pytest.mark.parametrize("bundle", _BUNDLE_ROOTS)
def test_source_archive_contains_bundle_files(bundle: str) -> None:
    available, selected = _archive_selection()
    files = {path for path in available if path.startswith(f"{bundle}/")}
    if not files and bundle == "apps/console/dist":
        pytest.skip("Console bundle has not been built in this checkout.")
    assert files, f"No files found in {bundle}"
    expected = {
        path
        for path in files
        if Path(path).name != ".DS_Store" and not fnmatch(path, "*.py[cod]")
    }
    assert selected.intersection(files) == expected


def test_source_archive_contains_license() -> None:
    _, selected = _archive_selection()
    assert (_REPO_ROOT / "LICENSE").is_file()
    assert "LICENSE" in selected
