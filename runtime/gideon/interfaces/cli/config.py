"""CLI config subcommand — get, set, unset, edit configuration values."""

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

from gideon.core.config import AppConfig
from gideon.core.config import loader as config_loader
from gideon.core.config.document import (
    preserve_configuration_credentials,
    read_configuration,
    redact_configuration,
)
from gideon.core.config.transactions import mutate_config
from gideon.engine.hooks import safe_read_file
from gideon.security.sel import sel


def config_path() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


_MISSING = object()


def _config_cmd(args: argparse.Namespace) -> None:
    """Get or set config values."""
    action = getattr(args, "config_action", None)
    if action == "get":

        cfg = AppConfig.load()
        raw = read_configuration(config_path(), config_loader.logger)
        d = redact_configuration({**cfg.to_dict(), **(raw or {})})
        key = getattr(args, "key", None)
        sel().log_api_access(
            caller="cli",
            operation="config_get",
            outcome="allowed",
            source="cli",
            resources=key or "*",
        )
        if not key:
            print(json.dumps(d, indent=2))
            return
        val = _dict_get(d, key)
        if val is _MISSING:
            print(f"❌ Unknown key: {key}", file=sys.stderr)
            sys.exit(1)
        if isinstance(val, (dict, list)):
            print(json.dumps(val, indent=2))
        else:
            print(val)
    elif action == "set":

        file_path = getattr(args, "file", None)
        if file_path:
            fp = Path(file_path).expanduser().resolve()

            try:
                data = json.loads(safe_read_file(str(fp)))
            except PermissionError as e:
                print(f"❌ {e}", file=sys.stderr)
                sys.exit(1)
            except (json.JSONDecodeError, OSError) as e:
                print(f"❌ Invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)
            if not isinstance(data, dict):
                print("❌ Config must be a JSON object", file=sys.stderr)
                sys.exit(1)
            try:
                def apply_file_update(existing: dict) -> None:
                    incoming = preserve_configuration_credentials(data, existing)
                    _validate_file_update(incoming, existing)
                    updated = _merge_config_values(existing, incoming)
                    existing.clear()
                    existing.update(updated)

                mutate_config(apply_file_update, path=config_path())
            except (OSError, ValueError, RuntimeError) as e:
                print(f"❌ Could not apply config: {e}", file=sys.stderr)
                sys.exit(1)
            sel().log_api_access(
                caller="cli",
                operation="config_set_file",
                outcome="allowed",
                source="cli",
                resources=str(fp),
            )
            print(f"✅ Config loaded from {file_path}")
        else:
            key = args.key
            value = args.value
            if not key or value is None:
                print("Usage: gideon config set <key> <value>", file=sys.stderr)
                print("       gideon config set --file <path.json>", file=sys.stderr)
                sys.exit(1)
            p = config_path()
            parsed = _parse_value(value)
            spec = _editable_spec(key)
            if spec is not None:
                from gideon.core.config.edit_spec import (
                    ConfigValueError,
                    coerce_edit_value,
                )

                try:
                    parsed = coerce_edit_value(key, parsed, spec)
                except ConfigValueError as exc:
                    print(f"❌ {key}: {exc}", file=sys.stderr)
                    sel().log_api_access(
                        caller="cli",
                        operation="config_set",
                        outcome="denied",
                        source="cli",
                        resources=exc.resources or f"{key}={value}",
                    )
                    sys.exit(1)
            try:
                def apply_keyed_update(document: dict) -> bool:
                    modeled = AppConfig.load().to_dict()
                    if not _dict_set(modeled, key, parsed):
                        return False
                    document.update(_merge_config_values(document, modeled))
                    return True

                if not mutate_config(apply_keyed_update, path=p):
                    print(f"❌ Unknown key: {key}", file=sys.stderr)
                    sys.exit(1)
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"❌ Could not apply config: {exc}", file=sys.stderr)
                sys.exit(1)
            sel().log_api_access(
                caller="cli",
                operation="config_set",
                outcome="allowed",
                source="cli",
                resources=f"{key}={json.dumps(parsed)}",
            )
            print(f"✅ {key} = {json.dumps(parsed)}")
    elif action == "unset":
        key = getattr(args, "key", "")
        if not key or any(not part or part.strip() != part for part in key.split(".")):
            print("❌ Invalid key: use a nonempty dot-separated key", file=sys.stderr)
            sys.exit(1)
        p = config_path()
        try:
            def remove_key(document: dict) -> str:
                if _dict_get(document, key) is _MISSING:
                    return "known_missing" if _dict_get(AppConfig().to_dict(), key) is not _MISSING else "unknown"
                owner = document
                parts = key.split(".")
                for part in parts[:-1]:
                    owner = owner[part]
                del owner[parts[-1]]
                return "removed"

            result = mutate_config(remove_key, path=p)
            if result == "unknown":
                print(f"❌ Unknown key: {key}", file=sys.stderr)
                sys.exit(1)
            if result == "known_missing":
                print(f"✅ {key} is not set")
                return
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"❌ Could not unset config: {exc}", file=sys.stderr)
            sys.exit(1)
        sel().log_api_access(
            caller="cli",
            operation="config_unset",
            outcome="allowed",
            source="cli",
            resources=key,
        )
        print(f"✅ Unset {key}")
    elif action == "edit":

        p = config_path()
        if not p.exists():
            cfg = AppConfig()
            cfg.save()
            print(f"Created default config: {p}")
        sel().log_api_access(
            caller="cli",
            operation="config_edit",
            outcome="allowed",
            source="cli",
            resources=str(p),
        )
        editor = os.environ.get("EDITOR", "vi")
        os.execvp(editor, [editor, str(p)])
    else:
        print("Usage: gideon config {get,set,unset,edit}", file=sys.stderr)
        sys.exit(1)


def _validate_file_update(data: dict, existing: dict) -> None:
    import jsonschema

    from gideon.core.config.schema import JSON_SCHEMA
    from gideon.core.config.validation import _DIRECT_READ_TOP_KEYS

    schema = deepcopy(JSON_SCHEMA)

    def close_records(node):
        if "properties" in node:
            node["additionalProperties"] = False
            for child in node["properties"].values():
                close_records(child)
        for name in ("items", "additionalProperties"):
            if isinstance(node.get(name), dict):
                close_records(node[name])

    close_records(schema)
    for key in _DIRECT_READ_TOP_KEYS | {"use_cases"}:
        schema["properties"][key] = {"type": "object"}
    candidate = dict(data)
    unsupported = []
    for key in data.keys() - schema["properties"].keys():
        if key in existing and data[key] == existing[key]:
            candidate.pop(key)
        else:
            unsupported.append(key)
    issues = jsonschema.Draft7Validator(schema).iter_errors(candidate)
    unsupported.extend(
        ".".join(map(str, issue.absolute_path)) or "<root>" for issue in issues
    )
    if unsupported:
        raise ValueError(
            "cannot apply config fields: " + ", ".join(sorted(set(unsupported)))
        )


def _editable_spec(key: str) -> dict | None:
    """The PATCH allowlist's spec for a dotted key, or None if it declares none.

    Imported lazily: the registry lives in a dashboard handler module (the inert-surface
    census parses that file for the `_EDITABLE_CONFIG` literal, so it cannot move), and
    `gideon config get` should not pay for importing aiohttp. A failure to import is
    not a reason to refuse a write — it means no spec is available, which is exactly the
    "key not declared" case.
    """
    try:
        from gideon.interfaces.dashboard.handlers.core import _EDITABLE_CONFIG

        return _EDITABLE_CONFIG.get(key)
    except (
        Exception
    ):  # noqa: BLE001 — no spec available is the same as no spec declared
        return None


def _merge_config_values(existing: dict, incoming: dict) -> dict:
    """Merge modeled or imported values while retaining opaque config blocks."""
    result = deepcopy(existing)
    for key, value in incoming.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_config_values(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _dict_get(d: dict, key: str) -> object:
    """Get a value from a nested dict using dot-separated key."""
    parts = key.split(".")
    cur: object = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return cur


def _dict_set(d: dict, key: str, value: object) -> bool:
    """Set a value in a nested dict using dot-separated key. Returns False if parent missing."""
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    if not isinstance(cur, dict):
        return False
    if parts[-1] not in cur:
        return False
    cur[parts[-1]] = value
    return True


def _parse_value(raw: str) -> object:
    """Parse a CLI value string into the appropriate Python type."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    return raw
