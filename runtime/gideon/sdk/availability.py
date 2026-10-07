"""Cheap, metadata-only helpers for provider availability hooks."""

from __future__ import annotations

import importlib.util


def missing_modules(*modules: str) -> list[str]:
    """Return modules absent from this interpreter without importing them."""
    missing: list[str] = []
    for name in modules:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return missing


def modules_installed(*modules: str) -> bool:
    """Return whether every module is discoverable without executing package code."""
    return not missing_modules(*modules)


__all__ = ["missing_modules", "modules_installed"]
