"""Locate Gideon resources in installations and development checkouts."""

from collections.abc import Iterator
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def package_path(*parts: str) -> Path:
    return package_root().joinpath(*parts)


def checkout_root() -> Path | None:
    cursor = package_root().parent
    while cursor != cursor.parent:
        package = cursor.joinpath("runtime", "gideon")
        if package.is_dir() and cursor.joinpath("pyproject.toml").is_file():
            return cursor
        cursor = cursor.parent
    if (cursor / "runtime/gideon").is_dir() and (cursor / "pyproject.toml").is_file():
        return cursor
    return None


def _console_locations() -> Iterator[Path]:
    yield package_path("static", "dist")
    checkout = checkout_root()
    if checkout is not None:
        yield checkout.joinpath("apps", "console", "dist")


def console_dist() -> Path | None:
    for directory in _console_locations():
        if directory.joinpath("index.html").is_file():
            return directory
    return None
