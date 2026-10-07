"""Owner decisions for the exact MCP server definition Gideon will use."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from gideon.security.approval_answer import OWNER, Principal
from gideon.security.owner_grants import GrantBook, seal

BOOK = GrantBook("mcp_servers")
WAITING = "waiting"
WAITING_REASON = (
    "This MCP server is waiting for the owner to allow its current definition."
)
_CONFIRM = "Allow this exact MCP server definition to connect or run? Review its transport and fields before confirming."
_REFERENCE = re.compile(r"^\{\{secret:([A-Za-z0-9_]+)\}\}$")
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|bearer|private[_-]?key"
    r"|access[_-]?key|auth)",
    re.IGNORECASE,
)


class McpGrantDefinitionError(ValueError):
    """A server definition is malformed or contains a credential outside its owner refs."""


def _credential_field(name: object) -> bool:
    """Match onboarding's credential-name floor without importing its scanner package."""
    return bool(_SECRET_KEY_RE.search(str(name)))


def _value(server: Any, name: str, default: Any = None) -> Any:
    if isinstance(server, Mapping):
        return server.get(name, default)
    return getattr(server, name, default)


def _transport(server: Any, url: str, command: str) -> str:
    explicit = _value(server, "transport", "")
    value = str(explicit or "").strip().lower().replace("_", "-")
    if url:
        if command:
            raise McpGrantDefinitionError("MCP server cannot have both command and URL")
        if value in {"", "sse"}:
            return "sse"
        if value in {"http", "streamable-http"}:
            return "streamable-http"
        raise McpGrantDefinitionError("unsupported MCP remote transport")
    if not command:
        raise McpGrantDefinitionError("MCP server needs one command or URL")
    if value not in {"", "stdio"}:
        raise McpGrantDefinitionError("unsupported MCP command transport")
    return "stdio"


def _secret_reference(name: str, value: Any, *, required: bool) -> str | None:
    if not isinstance(value, str):
        raise McpGrantDefinitionError("MCP credential references must be strings")
    if not value:
        return ""
    match = _REFERENCE.fullmatch(value)
    if match is None:
        if required:
            raise McpGrantDefinitionError(
                "MCP credentials must be stored as owner references"
            )
        return None
    from gideon.extensions.providers.mcp_instances import mcp_owner

    reference = match.group(1)
    if not mcp_owner(name).owns(reference):
        raise McpGrantDefinitionError(
            "MCP credential reference belongs to another server"
        )
    return value


def _credential_fields(server: Any, field: str, name: str) -> dict[str, Any]:
    values = _value(server, field, {}) or {}
    if not isinstance(values, Mapping):
        raise McpGrantDefinitionError(f"MCP {field} must be an object")
    normalized: dict[str, Any] = {}
    for raw_key, value in values.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise McpGrantDefinitionError(f"MCP {field} names must be nonempty strings")
        if field == "headers" or _credential_field(raw_key):
            reference = _secret_reference(name, value, required=bool(value))
            normalized[raw_key] = {"reference": reference or ""}
        else:
            if not isinstance(value, str) or "\0" in value:
                raise McpGrantDefinitionError(
                    f"MCP {field} values must be NUL-free strings"
                )
            normalized[raw_key] = {"value": value}
    return normalized


def definition(server: Any) -> dict[str, Any]:
    """Return only execution inputs and owner-bound reference identities; never resolve secrets."""
    name = _value(server, "name", "")
    source = _value(server, "source", "")
    command = _value(server, "command", "") or ""
    url = _value(server, "url", "") or ""
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(source, str)
        or not source
    ):
        raise McpGrantDefinitionError("MCP server name and source are required")
    if not isinstance(command, str) or not isinstance(url, str):
        raise McpGrantDefinitionError("MCP command and URL must be strings")
    args = _value(server, "args", []) or []
    if not isinstance(args, (list, tuple)) or any(
        not isinstance(arg, str) for arg in args
    ):
        raise McpGrantDefinitionError("MCP arguments must be a string list")
    cwd = _value(server, "cwd", "") or ""
    if not isinstance(cwd, str) or "\0" in cwd:
        raise McpGrantDefinitionError("MCP working directory must be a NUL-free string")
    oauth = _value(server, "oauth", {}) or {}
    if not isinstance(oauth, Mapping):
        raise McpGrantDefinitionError("MCP OAuth settings must be an object")
    oauth_fields: dict[str, Any] = {}
    for key, value in oauth.items():
        if not isinstance(key, str) or not key:
            raise McpGrantDefinitionError("MCP OAuth names must be nonempty strings")
        if _credential_field(key):
            reference = _secret_reference(name, value, required=bool(value))
            oauth_fields[key] = {"reference": reference or ""}
        else:
            oauth_fields[key] = {"value": value}
    from gideon.integrations.mcp_argument_secrets import (
        sealed_address,
        sealed_arguments,
        validate_references,
    )

    validate_references(name, {"args": list(args), "url": url})
    return {
        "name": name,
        "source": source,
        "transport": _transport(server, url, command),
        "command": command,
        "args": sealed_arguments(list(args)),
        "cwd": cwd,
        "url": sealed_address(url),
        "env": _credential_fields(server, "env", name),
        "headers": _credential_fields(server, "headers", name),
        "oauth": oauth_fields,
        "allowElicitation": _value(server, "allowElicitation", False) is True,
        "poolable": _value(server, "poolable", False) is True,
    }


def content(server: Any) -> str:
    return json.dumps(
        definition(server), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def key(server: Any) -> str:
    info = definition(server)
    identity = f"{info['source']}\0{info['name']}".encode("utf-8")
    return "mcp:" + hashlib.sha256(identity).hexdigest()


def revision(server: Any) -> str:
    return seal(content(server))


def carry_over(before: Any, after: Any) -> bool:
    """Migrate only the book's exact prior consent to an equivalent masked definition."""
    if not _owner_managed(before) or not _owner_managed(after):
        return False
    try:
        earlier, later = definition(before), definition(after)
        if earlier != later:
            return False
        legacy = {
            **earlier,
            "args": list(_value(before, "args", []) or []),
            "url": _value(before, "url", "") or "",
        }
        written = json.dumps(
            legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with BOOK._locked():
            grants = BOOK._read(strict=True)
            entry = grants.get(key(before))
            if not entry or entry.get("seal") not in {
                seal(written),
                seal(content(before)),
            }:
                return False
            replacement = {**entry, "seal": seal(content(after))}
            if entry != replacement:
                grants[key(after)] = replacement
                BOOK._write(grants)
        return True
    except (McpGrantDefinitionError, OSError, TypeError, ValueError):
        return False


def exempt(server: Any) -> bool:
    """Only the product's own injected core server is exempt from a customer grant."""
    if (
        _value(server, "name", "") != "gideon-core"
        or _value(server, "source", "") != "agent"
    ):
        return False
    from gideon.engine.agent import _MANAGED_MCP_SERVERS

    managed = _MANAGED_MCP_SERVERS.get("gideon-core")
    if not isinstance(managed, Mapping):
        return False
    command = managed.get("command", "")
    command_fn = managed.get("command_fn")
    if not command and callable(command_fn):
        command = command_fn()
    raw_args = managed.get("args", [])
    expected_args = list(raw_args) if isinstance(raw_args, (list, tuple)) else []
    return (
        _value(server, "command", "") == command
        and list(_value(server, "args", []) or []) == expected_args
        and not _value(server, "url", "")
        and not _value(server, "env", {})
        and not _value(server, "headers", {})
        and not _value(server, "oauth", {})
        and not _value(server, "allowElicitation", False)
        and not _value(server, "poolable", False)
    )


def _owner_managed(server: Any) -> bool:
    if _value(server, "source", "") != "mcp.json":
        return False
    name = _value(server, "name", "")
    if not isinstance(name, str) or not name:
        return False
    if ":" not in name:
        return True
    app_name = name.split(":", 1)[0]
    try:
        from gideon.extensions.apps.manager import _read_installed

        return _read_installed(app_name) is None
    except Exception:
        return False


def allowed(server: Any) -> bool:
    if exempt(server):
        return True
    if not _owner_managed(server):
        return False
    try:
        return BOOK.holds(key(server), content(server))
    except (McpGrantDefinitionError, OSError, TypeError, ValueError):
        return False


def give(server: Any, principal: Principal) -> None:
    if (
        not isinstance(principal, Principal)
        or principal.kind != OWNER
        or not principal.name
    ):
        raise PermissionError("MCP server grants require an authenticated owner")
    if exempt(server):
        raise PermissionError("the managed Gideon MCP server does not take user grants")
    if not _owner_managed(server):
        raise PermissionError("only owner-managed MCP definitions can receive grants")
    BOOK.give(key(server), content(server), principal=principal.label)


def revoke(server: Any) -> None:
    BOOK.revoke(key(server))


def display(server: Any) -> dict[str, Any]:
    """Owner-visible, credential-free fields used for status and review questions."""
    info = definition(server)
    env = info["env"]
    headers = info["headers"]
    shown: dict[str, Any] = {
        "name": info["name"],
        "source": info["source"],
        "transport": info["transport"],
        "command": info["command"],
        "args": info["args"],
        "cwd": info["cwd"],
        "url": info["url"],
        "env": sorted(env),
        "headers": sorted(headers),
        "header_credentials": sorted(headers),
        "oauth": sorted(info["oauth"]),
        "allowElicitation": info["allowElicitation"],
        "poolable": info["poolable"],
    }
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    shown["command"], _ = redact_credentials(shown["command"])
    shown["command"], _ = redact_exfiltration_urls(shown["command"])
    shown["args"] = [
        redact_exfiltration_urls(redact_credentials(arg)[0])[0] for arg in shown["args"]
    ]
    shown["url"], _ = redact_credentials(shown["url"])
    shown["url"], _ = redact_exfiltration_urls(shown["url"])
    from gideon.integrations.mcp_secret_refs import safe_display_args, safe_display_url

    shown["args"] = safe_display_args(shown["args"])
    shown["url"] = safe_display_url(shown["url"])
    return shown


def question(server: Any, *, saving: bool = False) -> str:
    info = display(server)
    action = "Saving" if saving else "Allowing"
    if info["transport"] == "stdio":
        command = " ".join([info["command"], *info["args"]]).strip()
        location = f" in {info['cwd']}" if info["cwd"] else ""
        names = sorted(set(info["env"] + info["headers"]))
        fields = f" with credential fields {', '.join(names)}" if names else ""
        pooling = " with a shared cross-session connection" if info["poolable"] else ""
        return f"{action} {info['name']} lets Gideon run {command}{location}{fields}{pooling} as you. Review this exact definition before confirming."
    header_names = ", ".join(info["header_credentials"])
    fields = f" with headers {header_names}" if header_names else ""
    pooling = " with a shared cross-session connection" if info["poolable"] else ""
    return f"{action} {info['name']} lets Gideon connect to {info['url']}{fields}{pooling}. Review this exact definition before confirming."


def confirmation_revision(server: Any) -> str:
    return revision(server)
