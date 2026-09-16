#!/usr/bin/env python3
"""Committed config-schema baseline generator (PLATFORM-HARDENING-FLOORS SH3.1).

``test_config_roundtrip.py`` proves each config field survives a save/load round
trip, but it cannot see DRIFT: a renamed key, a silently dropped ``_meta``, or a
field that stopped being written all still round-trip. This generator closes that
gap. It walks the SAME source of truth — the ``AppConfig`` dataclass hierarchy and
its ``_meta`` field metadata — and emits a flat, sorted ``checks/catalogs/configuration.json``
committed to the repo root. A companion test (``checks/runtime/test_config_baseline.py``)
regenerates in-memory and byte-compares, so any schema change not regenerated
reddens CI. Same source, strictly more coverage.

The render is DETERMINISTIC: entries are sorted by path, types are stringified
uniformly whether the annotation is a type object or a forward-ref string, and the
output is ``json.dumps(..., indent=2, sort_keys=True)`` with a trailing newline. A
second run is byte-identical to the first — that idempotence is the whole contract.

Each entry is a leaf field of the declared schema:
``{"path", "type", "default", "sensitive"}``. Nested config dataclasses are
recursed into (so ``security.egress.allow_hosts`` appears as its own path); a
dict/list leaf records the declared field itself, never its runtime contents.

Regenerate in place with::

    python tooling/scripts/generate_config_baseline.py
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, get_args, get_origin

from gideon.core.config.loader import AppConfig


def _type_str(tp: Any) -> str:
    """Stringify a declared field type deterministically.

    Handles type objects (``str`` → ``"str"``), parameterized generics
    (``list[str]`` → ``"list[str]"``, ``dict[str, list[str]]`` →
    ``"dict[str, list[str]]"``), and forward-ref string annotations (returned
    verbatim). Argument order follows the annotation, so re-runs are identical.
    """
    if isinstance(tp, str):
        return tp
    origin = get_origin(tp)
    if origin is None:
        return getattr(tp, "__name__", str(tp))
    origin_name = getattr(origin, "__name__", str(origin))
    args = get_args(tp)
    if args:
        return f"{origin_name}[{', '.join(_type_str(a) for a in args)}]"
    return origin_name


def _field_default(f: dataclasses.Field) -> Any:  # type: ignore[type-arg]
    """Return a field's declaration-time default (or ``default_factory()`` result)."""
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return f.default_factory()  # type: ignore[misc]
    return None


def _json_safe(value: Any) -> Any:
    """Render a leaf default JSON-safe, using a stable placeholder for the rare
    non-serializable case (declaration-time defaults are primitives / lists / dicts
    of primitives, so this fallback is defensive, not expected)."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return "<factory>"


def _walk(cls: type, prefix: str, out: list[dict[str, Any]]) -> None:
    """Recurse ``cls``'s dataclass fields, appending one leaf entry per field.

    A field whose declared default is itself a config dataclass instance is a
    nested section: recurse into it (its fields appear as their own dotted paths)
    rather than emitting the section as a row. Everything else is a leaf.
    """
    for f in dataclasses.fields(cls):
        path = f"{prefix}{f.name}"
        default = _field_default(f)
        if dataclasses.is_dataclass(default) and not isinstance(default, type):
            _walk(type(default), f"{path}.", out)
            continue
        meta = dict(f.metadata) if f.metadata else {}
        out.append(
            {
                "path": path,
                "type": _type_str(f.type),
                "default": _json_safe(default),
                "sensitive": bool(meta.get("sensitive", False)),
            }
        )


def encode_catalog(entries: list[dict[str, Any]]) -> dict[str, Any]:
    fields = {
        entry["path"]: {key: entry[key] for key in ("type", "default", "sensitive")}
        for entry in sorted(entries, key=lambda row: row["path"])
    }
    if len(fields) != len(entries):
        raise ValueError("configuration catalog contains duplicate field paths")
    return {"version": 1, "kind": "gideon.configuration", "data": {"fields": fields}}


def decode_catalog(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("version") != 1 or document.get("kind") != "gideon.configuration":
        raise ValueError("unsupported Gideon configuration catalog")
    return [
        {"path": path, **details}
        for path, details in sorted(document["data"]["fields"].items())
    ]


def build_baseline() -> str:
    entries: list[dict[str, Any]] = []
    _walk(AppConfig, "", entries)
    return json.dumps(encode_catalog(entries), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def baseline_path() -> Path:
    """Repo-root location of the committed ``checks/catalogs/configuration.json``."""
    return Path(__file__).resolve().parents[2] / "checks/catalogs/configuration.json"


def main() -> None:
    path = baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_baseline(), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
