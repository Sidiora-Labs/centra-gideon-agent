"""Owner grants for the exact current definition of a custom agent runner."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Iterator

from gideon.security.approval_answer import OWNER, Principal
from gideon.security.owner_grants import GrantBook, seal

_BOOK = GrantBook("custom_runners")
_LOCAL_TENANT = "self-hosted"
_ACTIVE_TENANT: ContextVar[str | None] = ContextVar(
    "gideon_runner_grant_tenant", default=None
)


class RunnerGrantDefinitionError(ValueError):
    """A runner does not have a safe, resolvable definition to grant."""


def _tenant(principal_or_tenant: Principal | str | None) -> str:
    if isinstance(principal_or_tenant, Principal):
        value = principal_or_tenant.tenant or _LOCAL_TENANT
    elif principal_or_tenant is None:
        value = _ACTIVE_TENANT.get() or _LOCAL_TENANT
    else:
        value = str(principal_or_tenant)
    if not value or len(value) > 240 or "\0" in value:
        raise ValueError("invalid runner-grant tenant")
    return value


@contextmanager
def tenant_scope(principal_or_tenant: Principal | str | None) -> Iterator[None]:
    """Bind an authenticated request's tenant while its runtime operations execute."""
    if isinstance(principal_or_tenant, Principal):
        tenant = (
            _tenant(principal_or_tenant)
            if principal_or_tenant.kind == OWNER and principal_or_tenant.name
            else None
        )
    elif isinstance(principal_or_tenant, str) and principal_or_tenant:
        tenant = _tenant(principal_or_tenant)
    else:
        tenant = None
    token: Token[str | None] = _ACTIVE_TENANT.set(tenant)
    try:
        yield
    finally:
        _ACTIVE_TENANT.reset(token)


def _definition(definition: Any) -> dict[str, Any]:
    from gideon.engine.agents.runners import resolve_runner_command
    from gideon.integrations.acp.cli_resolve import resolve_acp_cli

    argv = resolve_runner_command(definition)
    if not argv:
        raise RunnerGrantDefinitionError("custom runner executable is unavailable")

    def identities(command: list[str]) -> list[dict[str, str]]:
        result = []
        for part in command:
            candidate = Path(part).expanduser()
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve(strict=True)
                digest = hashlib.sha256()
                with resolved.open("rb") as stream:
                    while chunk := stream.read(1 << 16):
                        digest.update(chunk)
            except OSError as exc:
                raise RunnerGrantDefinitionError(
                    "custom runner executable is unsafe or unavailable"
                ) from exc
            result.append({"path": str(resolved), "sha256": digest.hexdigest()})
        return result

    adapter = definition.adapter
    adapter_argv = (
        resolve_acp_cli(
            env_var=(
                adapter.env_var
                or "GIDEON_RUNNER_"
                + definition.id.upper().replace("-", "_")
                + "_ADAPTER_BIN"
            ),
            bin_names=list(adapter.bin_names),
            npm_pkg=None,
        )
        if adapter is not None and adapter.bin_names
        else None
    )
    try:
        from gideon.integrations.llm.registry import get_default_registry

        entry = get_default_registry().get_entry(definition.runtime_id)
    except Exception:
        entry = None
    provider = None
    if entry is not None:
        options_text = json.dumps(
            entry.options or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        provider = {
            "name": entry.name,
            "type": entry.type,
            "model": entry.model,
            "credential_ref": entry.credential or "",
            "options_sha256": hashlib.sha256(options_text.encode("utf-8")).hexdigest(),
        }
    return {
        "id": definition.id,
        "runtime_id": definition.runtime_id,
        "bin_names": list(definition.bin_names),
        "env_selector": definition.env_var,
        "resolved_argv": list(argv),
        "executable_files": identities(argv),
        "version_args": list(definition.version_args),
        "acp_args": list(definition.acp_args),
        "dialect": definition.dialect,
        "adapter": (
            {
                "npm_pkg": adapter.npm_pkg,
                "env_selector": adapter.env_var,
                "bin_names": list(adapter.bin_names),
                "version": adapter.version,
                "integrity": adapter.integrity,
                "resolved_argv": adapter_argv,
                "executable_files": identities(adapter_argv or []),
            }
            if adapter is not None
            else None
        ),
        "provider_entry": provider,
    }


def content(definition: Any) -> str:
    """Serialize all behavior-bearing runner inputs without recording their secrets."""
    return json.dumps(
        _definition(definition),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def revision(definition: Any, tenant: Principal | str | None = None) -> str:
    """Fingerprint the live executable and all configuration selectors for one tenant."""
    scope = _tenant(tenant)
    return seal(scope + "\0" + content(definition))


def _key(runner_id: str, tenant: Principal | str | None) -> str:
    identity = (_tenant(tenant) + "\0" + runner_id).encode("utf-8")
    return "runner:" + hashlib.sha256(identity).hexdigest()


def allowed(definition: Any, tenant: Principal | str | None = None) -> bool:
    """Whether this tenant's owner granted the exact current executable definition."""
    try:
        scope = tenant if tenant is not None else _ACTIVE_TENANT.get()
        if scope is None:
            return False
        current = revision(definition, scope)
        return _BOOK.holds(_key(definition.id, scope), current)
    except (OSError, TypeError, ValueError, RunnerGrantDefinitionError):
        return False


def grant(
    definition: Any,
    *,
    principal: Principal,
    expected_revision: str,
) -> bool:
    """Grant only an unchanged definition through a verified owner principal."""
    if (
        not isinstance(principal, Principal)
        or principal.kind != OWNER
        or not principal.name
    ):
        raise PermissionError("custom runner grants require an authenticated owner")
    current = revision(definition, principal)
    if not expected_revision or current != expected_revision:
        return False
    _BOOK.give(_key(definition.id, principal), current, principal=principal.label)
    return True


def revoke(definition: Any, tenant: Principal | str | None = None) -> None:
    """Revoke the runner grant for one tenant."""
    _BOOK.revoke(_key(definition.id, tenant))
