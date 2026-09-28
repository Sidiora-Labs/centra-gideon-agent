"""MCP-owned credential storage and opaque import selections.

Credential values remain in the existing shared secret store, under the same
MCP owner used by server definitions. Import selections are process-local,
short-lived capabilities; the browser receives only their random identifiers
and safe display metadata.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_IMPORT_TTL = 900.0
_IMPORT_LOCK = threading.RLock()
_IMPORTS: dict[str, tuple[float, Path, str, str, str]] = {}
_SECRET_ARG = re.compile(r"(?i)(api[-_]?key|access[-_]?key|token|secret|password|passwd|credential|auth|private[-_]?key)")
_SECRET_ASSIGNMENT = re.compile(r"(?i)([^\s=,:;]*(?:api[-_]?key|access[-_]?key|token|secret|password|passwd|credential|auth|private[-_]?key)[^\s=,:;]*\s*[=:]\s*)([^\s,;]+)")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_command(value: Any) -> str:
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    raw = value if isinstance(value, str) else ""
    return redact_exfiltration_urls(redact_credentials(raw)[0])[0]


def safe_display_args(values: Any) -> list[str]:
    """Mask credential-flag values even when their format is not a known token."""
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    redact_next = False
    for raw in values:
        if not isinstance(raw, str):
            result.append("[REDACTED: invalid argument]")
            redact_next = False
            continue
        safe = redact_exfiltration_urls(redact_credentials(raw)[0])[0]
        if redact_next:
            result.append("[REDACTED: credential]")
            redact_next = False
            continue
        match = _SECRET_ASSIGNMENT.search(safe)
        if match:
            safe = safe[:match.start(2)] + "[REDACTED: credential]" + safe[match.end(2):]
        elif safe.startswith("-") and _SECRET_ARG.search(safe):
            redact_next = True
        result.append(safe)
    return result


def safe_display_url(value: Any) -> str:
    """Show only remote origin and an opaque path marker; drop all URL values."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "[REDACTED: URL]"
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return f"{parsed.scheme}://{host}/…"
    except ValueError:
        return "[REDACTED: URL]"


def safe_import_projection(
    *, name: str, backend: str, spec: dict[str, Any], secrets_skipped: int = 0
) -> dict[str, Any]:
    """Build a browser-safe display record without serializing executable values."""
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    command = spec.get("command", "")
    args = spec.get("args", [])
    url = spec.get("url", "")
    if not isinstance(command, str) or not isinstance(url, str):
        raise ValueError("invalid imported MCP command or URL")
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ValueError("invalid imported MCP arguments")
    env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
    headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else {}
    transport = "stdio" if command else str(spec.get("transport") or "sse")
    return {
        "name": name,
        "backend": backend,
        "transport": transport,
        "display_command": _safe_command(command),
        "display_args": safe_display_args(args),
        "display_url": safe_display_url(url),
        "env": [{"name": str(key), "configured": bool(value)} for key, value in sorted(env.items())],
        "headers": [{"name": str(key), "configured": bool(value)} for key, value in sorted(headers.items())],
        "secrets_skipped": max(0, int(secrets_skipped)),
    }


def issue_import_id(path: Path, backend: str, name: str, spec: dict[str, Any]) -> str:
    """Remember a selected source definition and return a random opaque handle."""
    expiry = time.monotonic() + _IMPORT_TTL
    snapshot = _canonical(spec)
    with _IMPORT_LOCK:
        for key, entry in list(_IMPORTS.items()):
            if entry[0] <= time.monotonic():
                _IMPORTS.pop(key, None)
        identifier = secrets.token_hex(8)
        _IMPORTS[identifier] = (expiry, path, backend, name, snapshot)
    return identifier


def select_import(identifier: object) -> tuple[str, dict[str, Any]]:
    """Resolve an opaque selection, requiring the external file to be unchanged."""
    if not isinstance(identifier, str) or len(identifier) != 16 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("invalid MCP import selection")
    with _IMPORT_LOCK:
        entry = _IMPORTS.get(identifier)
    if entry is None or entry[0] <= time.monotonic():
        raise ValueError("MCP import selection expired; refresh the import list")
    _, path, backend, name, snapshot = entry
    from gideon.integrations.mcp_discovery import _IMPORT_JSON_PATHS
    from gideon.engine.hooks import safe_read_file

    if (path, backend) not in _IMPORT_JSON_PATHS:
        raise ValueError("MCP import source is no longer available")
    try:
        data = json.loads(safe_read_file(str(path)))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("MCP import source is unavailable") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    spec = servers.get(name) if isinstance(servers, dict) else None
    if not isinstance(spec, dict) or _canonical(spec) != snapshot:
        raise ValueError("MCP import source changed; refresh the import list")
    return name, dict(spec)


def store_server_credentials(name: str, spec: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store imported or edited env/header/OAuth values through the shared owner API."""
    from gideon.extensions.providers.mcp_instances import store_server_credentials as store

    return store(name, spec, previous=previous)


def resolve_server_credentials(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve only this MCP server's shared owner references at use time."""
    from gideon.extensions.providers.mcp_instances import resolve_server_credentials as resolve

    return resolve(name, spec)
