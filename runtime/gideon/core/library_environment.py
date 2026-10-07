"""Keep optional library caches and implicit credential discovery in Gideon's home."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from gideon.core.config.locations import active_home


def library_environment(*, source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return non-secret library settings without touching the filesystem."""
    inherited = os.environ if source is None else source
    home = active_home(
        override=str(inherited.get("GIDEON_HOME", "")),
        default=Path(inherited.get("HOME") or Path.home()) / ".gideon",
    )
    cache = home / "cache"
    hf_home = cache / "huggingface"
    return {
        "XDG_CACHE_HOME": str(cache),
        "HF_HOME": str(hf_home),
        "HF_HUB_CACHE": str(hf_home / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(hf_home / "hub"),
        "TRANSFORMERS_CACHE": str(hf_home / "hub"),
        "HF_XET_CACHE": str(hf_home / "xet"),
        "HF_ASSETS_CACHE": str(hf_home / "assets"),
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL": (
            cache / "tree-sitter-language-pack" / "parsers.json"
        ).as_uri(),
        "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR": str(cache / "tree-sitter-language-pack"),
    }


def configure_library_environment() -> dict[str, str]:
    """Apply native library settings before optional libraries are imported."""
    settings = library_environment()
    os.environ.update(settings)
    return settings
