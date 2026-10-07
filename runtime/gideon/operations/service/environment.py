"""Select and safely serialize the installer's non-secret service environment."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from gideon.core.config.document import _credential_field
from gideon.core.library_environment import library_environment
from gideon.security.sandbox import env_name_is_sensitive
from gideon.security.security import redact_credentials

# These are locations and network-routing settings needed by the local runtime.
# Values are copied only when present in the installer environment.
SERVICE_ENVIRONMENT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "GIDEON_HOME",
        "GIDEON_WORKSPACE",
        "GIDEON_PROFILE",
        "GIDEON_CACHE_DIR",
        "XDG_CACHE_HOME",
        "HF_HUB_CACHE",
        "HF_XET_CACHE",
        "HF_ASSETS_CACHE",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN",
        "HF_HUB_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
        "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "OLLAMA_MODELS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
)

# These values are controlled by the native service definition, never --env.
SERVICE_OWNED_ENVIRONMENT_NAMES: frozenset[str] = frozenset(
    {"HOME", "USER", "LOGNAME", "PATH", "PWD", "SHELL", "TMPDIR", "TEMP", "TMP"}
)

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ServiceEnvironmentError(ValueError):
    """An environment selection contains a refused name or value."""


@dataclass(frozen=True)
class ServiceEnvironment:
    values: dict[str, str]
    excluded_names: tuple[str, ...]


def _safe_value(name: str, value: str) -> bool:
    if "\x00" in value or "\r" in value or "\n" in value:
        return False
    _cleaned, warnings = redact_credentials(value)
    return not warnings and not env_name_is_sensitive(name)


def resolve_service_environment(
    additions: Iterable[str] = (),
    removals: Iterable[str] = (),
    *,
    source: Mapping[str, str] | None = None,
) -> ServiceEnvironment:
    """Return allowlisted values, refusing secret-shaped names and values.

    Each addition uses ``NAME=VALUE``. Refused inherited variables are listed by
    name only so service status can explain their exclusion without disclosing data.
    """
    inherited = os.environ if source is None else source
    inherited = {**inherited, **library_environment(source=inherited)}
    values: dict[str, str] = {}
    excluded: set[str] = set()

    for name, value in inherited.items():
        if env_name_is_sensitive(name) or _credential_field(name):
            excluded.add(name)
            continue
        if name not in SERVICE_ENVIRONMENT_ALLOWLIST:
            continue
        if name in SERVICE_OWNED_ENVIRONMENT_NAMES or not _safe_value(name, str(value)):
            excluded.add(name)
            continue
        values[name] = str(value)

    for raw in additions:
        name, separator, value = str(raw).partition("=")
        if not separator or not _ENV_NAME.fullmatch(name):
            raise ServiceEnvironmentError(
                "--env requires an allowlisted NAME=VALUE entry"
            )
        if name in SERVICE_OWNED_ENVIRONMENT_NAMES:
            raise ServiceEnvironmentError(
                f"{name} is service-owned and cannot be overridden"
            )
        if (
            name not in SERVICE_ENVIRONMENT_ALLOWLIST
            or env_name_is_sensitive(name)
            or _credential_field(name)
        ):
            raise ServiceEnvironmentError(
                f"{name} is not an allowlisted service variable"
            )
        if not _safe_value(name, value):
            excluded.add(name)
            raise ServiceEnvironmentError(
                f"{name} was refused because its value is unsafe for a service file"
            )
        values[name] = value
        excluded.discard(name)

    for raw_name in removals:
        name = str(raw_name)
        if not _ENV_NAME.fullmatch(name):
            raise ServiceEnvironmentError(
                "--no-env requires an environment variable name"
            )
        if name in SERVICE_OWNED_ENVIRONMENT_NAMES:
            raise ServiceEnvironmentError(
                f"{name} is service-owned and cannot be removed"
            )
        if name not in SERVICE_ENVIRONMENT_ALLOWLIST:
            raise ServiceEnvironmentError(
                f"{name} is not an allowlisted service variable"
            )
        values.pop(name, None)
        excluded.discard(name)

    return ServiceEnvironment(values, tuple(sorted(excluded)))


def systemd_environment_lines(
    environment: ServiceEnvironment, *, fixed_values: Mapping[str, str] | None = None
) -> str:
    """Serialize Environment= entries using systemd's quoted-value syntax."""
    lines = []
    all_values = {**(fixed_values or {}), **environment.values}
    for name, value in sorted(all_values.items()):
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ServiceEnvironmentError(
                f"{name} cannot be serialized as a service value"
            )
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        lines.append(f'Environment="{name}={escaped}"')
    lines.extend(
        f"# GideonEnvironmentExcluded={name}" for name in environment.excluded_names
    )
    return "\n".join(lines)


def status_environment_lines(environment: ServiceEnvironment) -> str:
    """Format environment status, showing values only for accepted names."""
    selected = resolve_service_environment(source=environment.values)
    excluded = set(environment.excluded_names) | set(selected.excluded_names)
    lines = [
        f"   environment: {name}={value}"
        for name, value in sorted(selected.values.items())
    ]
    lines.extend(
        f"   excluded: {name} (value hidden)"
        for name in sorted(excluded)
        if _ENV_NAME.fullmatch(name)
    )
    return "\n".join(lines)
