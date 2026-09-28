"""Present ``~/.gideon/mcp.json`` servers as multi-instance provider
instances for the generic ``mcp-tools`` settings card.

The MCP Tool Servers card in Settings → Providers is a ``multiInstance`` provider.
Rather than write the generic ``extensions/mcp-tools/instances/*.json`` store —
which the native MCP client never reads — its instance CRUD is repointed here so
it reads and writes the ONE store the system actually consumes:
``~/.gideon/mcp.json`` (loaded by :mod:`gideon.integrations.mcp_client` for the
native loop and merged into the agent config by :func:`gideon.engine.agent.rebuild_agent_config`).

Each ``mcpServers`` entry maps to one :class:`ExtensionInstance`:

* ``id`` / ``display_name`` = the server name (the mcp.json key)
* ``config`` = ``{transport, command, args, endpoint}`` matching the card's
  ``settingsSchema`` (``args`` is a space-joined string; ``endpoint`` is the SSE
  ``url``)
* ``enabled`` = NOT the spec's ``disabled`` flag

Writes preserve any ``env``/``headers`` already on the spec so editing from the
card never drops credentials configured elsewhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gideon.extensions.providers.instances import ExtensionInstance

MCP_TOOLS_EXTENSION = "mcp-tools"

_VALID_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _mcp_json_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / "mcp.json"


def mcp_owner(name: str):
    from gideon.core.config.secret_refs import SecretOwner

    return SecretOwner("MCP", name)


def _mcp_oauth_owner(name: str):
    from gideon.core.config.secret_refs import SecretOwner

    return SecretOwner("MCP_OAUTH", name)


def _secret_reference(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("{{secret:")


def _load() -> dict[str, Any]:
    path = _mcp_json_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            return data
        from gideon.core.config.secret_refs import purge_unused

        changed = False
        prepared: dict[str, Any] = {}
        try:
            for name, spec in servers.items():
                if not isinstance(spec, dict):
                    prepared[name] = spec
                    continue
                replacement = store_server_credentials(name, spec, previous=spec)
                prepared[name] = replacement
                changed |= replacement != spec
            if changed:
                data["mcpServers"] = prepared
                _save(data)
        except Exception:
            for name, spec in servers.items():
                if isinstance(spec, dict):
                    old = {
                        **(spec.get("env") if isinstance(spec.get("env"), dict) else {}),
                        **(spec.get("headers") if isinstance(spec.get("headers"), dict) else {}),
                        **(spec.get("oauth") if isinstance(spec.get("oauth"), dict) else {}),
                    }
                    purge_unused(mcp_owner(name), old)
            raise
        if changed:
            for name, spec in prepared.items():
                if isinstance(spec, dict):
                    retained = {
                        **(spec.get("env") if isinstance(spec.get("env"), dict) else {}),
                        **(spec.get("headers") if isinstance(spec.get("headers"), dict) else {}),
                        **(spec.get("oauth") if isinstance(spec.get("oauth"), dict) else {}),
                    }
                    purge_unused(mcp_owner(name), retained)
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _validate_credential_maps(
    spec: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    env = spec.get("env") or {}
    headers = spec.get("headers") or {}
    oauth = spec.get("oauth") or {}
    if not isinstance(env, dict) or not isinstance(headers, dict):
        raise ValueError("MCP env and headers must be objects")
    if not isinstance(oauth, dict):
        raise ValueError("MCP OAuth settings must be an object")
    for name, value in env.items():
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ValueError("Invalid MCP environment variable name")
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("MCP environment values must be NUL-free strings")
    for name, value in headers.items():
        if not isinstance(name, str) or not _HEADER_NAME.fullmatch(name):
            raise ValueError("Invalid MCP HTTP header name")
        if name.lower() in {"host", "content-length", "connection", "transfer-encoding"}:
            raise ValueError("Transport headers cannot be configured as credentials")
        if not isinstance(value, str) or any(char in value for char in "\0\r\n"):
            raise ValueError("MCP HTTP header values must be CR/LF/NUL-free strings")
    for name, value in oauth.items():
        if not isinstance(name, str) or not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("MCP OAuth settings must contain scalar values")
        if isinstance(value, str) and "\0" in value:
            raise ValueError("MCP OAuth settings must be NUL-free")
    return env, headers, oauth


def store_server_credentials(
    name: str, spec: dict[str, Any], *, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Persist MCP env and header credentials under the server's owner prefix."""
    from gideon.core.config.secret_refs import store
    from gideon.cognition.onboarding_import.floors import credential_field

    env, headers, oauth = _validate_credential_maps(spec)
    previous = previous or {}
    owner = mcp_owner(name)
    prepared = dict(spec)
    prior_values = {
        **(previous.get("env") if isinstance(previous.get("env"), dict) else {}),
        **(previous.get("headers") if isinstance(previous.get("headers"), dict) else {}),
        **(previous.get("oauth") if isinstance(previous.get("oauth"), dict) else {}),
    }
    try:
        secret_env = {key for key in env if credential_field(key)}
        if env:
            prepared["env"] = store(
                env,
                owner=owner,
                declared=secret_env,
                previous=previous.get("env") if isinstance(previous.get("env"), dict) else {},
            )
        else:
            prepared.pop("env", None)
        if headers:
            prepared["headers"] = store(
                headers,
                owner=owner,
                declared=set(headers),
                previous=previous.get("headers") if isinstance(previous.get("headers"), dict) else {},
            )
        else:
            prepared.pop("headers", None)
        secret_oauth = {key for key in oauth if credential_field(key)}
        if oauth:
            prepared["oauth"] = store(
                oauth,
                owner=owner,
                declared=secret_oauth,
                previous=previous.get("oauth") if isinstance(previous.get("oauth"), dict) else {},
            )
        elif "oauth" in spec:
            prepared["oauth"] = {}
        return prepared
    except Exception:
        from gideon.core.config.secret_refs import purge_unused

        purge_unused(owner, prior_values)
        raise


def resolve_server_credentials(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve this server's references only at its transport consumer."""
    from gideon.core.config.secret_refs import resolve

    owner = mcp_owner(name)
    resolved = dict(spec)
    from gideon.cognition.onboarding_import.floors import credential_field

    for field in ("env", "headers", "oauth"):
        values = spec.get(field) or {}
        if not isinstance(values, dict):
            raise ValueError(f"MCP {field} must be an object")
        if field == "env" and any(
            _secret_reference(value) and not credential_field(key)
            for key, value in values.items()
        ):
            raise ValueError("MCP references are allowed only for credential-shaped env names")
        resolved[field] = resolve(values, owner=owner)
    return resolved


def _save(data: dict[str, Any]) -> None:
    from gideon.engine.agent import _atomic_json_write

    path = _mcp_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(path, data)


def _spec_to_instance(name: str, spec: dict[str, Any]) -> ExtensionInstance:
    url = spec.get("url", "")
    args = spec.get("args", [])
    config: dict[str, Any] = {
        "transport": "sse" if url else "stdio",
        "command": spec.get("command", ""),
        "args": " ".join(args) if isinstance(args, list) else str(args or ""),
        "endpoint": url,
    }
    return ExtensionInstance(
        id=name,
        extension_name=MCP_TOOLS_EXTENSION,
        display_name=name,
        config=config,
        enabled=spec.get("disabled") is not True,
    )


def _config_to_spec(
    config: dict[str, Any], existing: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge a card config dict into an mcp.json server spec.

    Preserves ``env``/``headers`` from any existing spec so credential material
    configured outside the card survives an edit.
    """
    spec: dict[str, Any] = {}
    if isinstance(existing, dict):
        for k in ("env", "headers", "oauth"):
            if existing.get(k):
                spec[k] = existing[k]
        if existing.get("disabled") is True:
            spec["disabled"] = True
    for field in ("env", "headers", "oauth"):
        if field in config:
            spec[field] = config[field]

    transport = config.get("transport") or (
        "sse" if config.get("endpoint") else "stdio"
    )
    if transport == "sse":
        spec["url"] = (config.get("endpoint") or "").strip()
    else:
        spec["command"] = (config.get("command") or "").strip()
        raw_args = config.get("args") or ""
        if isinstance(raw_args, list):
            spec["args"] = raw_args
        else:
            spec["args"] = raw_args.split() if raw_args.strip() else []
    return spec


def list_instances() -> list[ExtensionInstance]:
    servers = _load().get("mcpServers", {})
    if not isinstance(servers, dict):
        return []
    return [
        _spec_to_instance(name, spec)
        for name, spec in servers.items()
        if isinstance(spec, dict)
    ]


def get_instance(instance_id: str) -> ExtensionInstance | None:
    spec = _load().get("mcpServers", {}).get(instance_id)
    return _spec_to_instance(instance_id, spec) if isinstance(spec, dict) else None


def create_instance(display_name: str, config: dict[str, Any]) -> ExtensionInstance:
    """Create a server entry in mcp.json. The display name IS the server key."""
    name = display_name.strip()
    if not _VALID_NAME.match(name):
        raise ValueError(
            "Server name must be 1–64 letters, digits, dashes, or underscores."
        )
    data = _load()
    servers = data.setdefault("mcpServers", {})
    if name in servers:
        raise ValueError(f"Server {name!r} already exists.")
    spec = store_server_credentials(name, _config_to_spec(config, None))
    servers[name] = spec
    try:
        _save(data)
    except OSError:
        from gideon.core.config.secret_refs import purge

        purge([mcp_owner(name).prefix])
        raise
    return _spec_to_instance(name, servers[name])


def update_instance(
    instance_id: str,
    *,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> ExtensionInstance | None:
    data = _load()
    servers = data.get("mcpServers", {})
    existing = servers.get(instance_id)
    if not isinstance(existing, dict):
        return None
    spec = _config_to_spec(config, existing) if config is not None else dict(existing)
    spec = store_server_credentials(instance_id, spec, previous=existing)
    if enabled is not None:
        if enabled:
            spec.pop("disabled", None)
        else:
            spec["disabled"] = True
    servers[instance_id] = spec
    try:
        _save(data)
    except OSError:
        from gideon.core.config.secret_refs import purge_unused

        purge_unused(mcp_owner(instance_id), {
            **(existing.get("env") if isinstance(existing.get("env"), dict) else {}),
            **(existing.get("headers") if isinstance(existing.get("headers"), dict) else {}),
            **(existing.get("oauth") if isinstance(existing.get("oauth"), dict) else {}),
        })
        raise
    from gideon.core.config.secret_refs import purge_unused

    purge_unused(mcp_owner(instance_id), {
        **(spec.get("env") if isinstance(spec.get("env"), dict) else {}),
        **(spec.get("headers") if isinstance(spec.get("headers"), dict) else {}),
        **(spec.get("oauth") if isinstance(spec.get("oauth"), dict) else {}),
    })
    return _spec_to_instance(instance_id, spec)


def delete_instance(instance_id: str) -> bool:
    data = _load()
    servers = data.get("mcpServers", {})
    existing = servers.get(instance_id)
    if not isinstance(existing, dict):
        return False
    del servers[instance_id]
    _save(data)
    from gideon.core.config.secret_refs import purge

    purge([mcp_owner(instance_id).prefix, _mcp_oauth_owner(instance_id).prefix])
    from gideon.integrations.mcp_oauth import purge_server

    purge_server(instance_id, str(existing.get("url") or existing.get("endpoint") or ""))
    return True
