"""Projection of the permissions, jobs and code paths an app install enables."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.apps.secret_fields import SECRET_MASK

logger = logging.getLogger(__name__)


def describe(manifest: AppManifest) -> dict[str, Any]:
    """Return the shared install-review and catalog projection for one manifest."""
    projection: dict[str, Any] = {
        "permissions": manifest.permissions.to_dict(),
        "crons": _crons(manifest),
        "pythonDependencies": _python_dependencies(manifest),
        "sidecarDependencies": (
            list(manifest.dependencies.sidecarDependencies)
            if isinstance(manifest.dependencies.sidecarDependencies, list)
            else []
        ),
        "requires": _requires(manifest),
        "launches": _launches(manifest),
        "npmPackages": list(manifest.dependencies.npmPackages),
        "writes": [entry.to_dict() for entry in manifest.writes],
        "providerExecution": (
            manifest.provider.execution if manifest.provider is not None else ""
        ),
        "hasUI": bool(manifest.ui.pages or manifest.ui.entry),
        "uiComponents": manifest.ui.components,
        "hasBackend": bool(manifest.backend.entryPoint),
        "backendSandbox": _backend_sandbox(manifest),
        "providers": _providers(manifest),
        "onInstall": manifest.setup.onInstall,
        "onUpdate": manifest.setup.onUpdate,
        "onEnable": manifest.setup.onEnable,
        "onDisable": manifest.setup.onDisable,
        "onUninstall": manifest.setup.onUninstall,
        "hooks": _hooks(manifest),
        "cliSetup": manifest.cli.setup,
        "cliDoctor": manifest.cli.doctor,
        "sources": _sources(manifest),
        "mcpServers": _mcp_servers(manifest),
        "skills": _skills(manifest),
    }
    projection["runsAsYou"] = _runs_as_you(projection)
    return projection


def changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    """Whether an update changes the app's install-time grants or runtime behavior."""
    if previous is None:
        return True
    return json.dumps(
        previous, sort_keys=True, separators=(",", ":"), default=str
    ) != json.dumps(current, sort_keys=True, separators=(",", ":"), default=str)


def bundle_digest(staged: Path) -> str:
    """Hash every staged path and its bytes without following in-tree symlinks."""
    root = Path(staged)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("reviewed app bundle must be a staged directory")
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8")
            digest.update(b"L\0" + relative + b"\0" + target + b"\n")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            file_digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    file_digest.update(chunk)
            digest.update(file_digest.digest())
            digest.update(b"\n")
        elif not path.is_dir():
            raise ValueError("reviewed app bundle contains an unsupported entry")
    return digest.hexdigest()


def _crons(manifest: AppManifest) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for cron in manifest.crons:
        has_schedule = bool(cron.every or cron.cron_expr)
        if cron.every:
            cadence = f"Every {cron.every} seconds"
        else:
            cadence = cron.cron_expr
        jobs.append(
            {
                "name": cron.name,
                "every": cron.every,
                "cron_expr": cron.cron_expr,
                "cadence": cadence,
                "agent": cron.agent,
                "message": cron.message,
                "agentTier": manifest.permissions.agent_tier,
                "scheduled": bool(
                    manifest.permissions.cron and manifest.permissions.agent_tier and has_schedule and cron.name
                ),
            }
        )
    return jobs


def _python_dependencies(manifest: AppManifest) -> list[dict[str, Any]]:
    requirements = [
        str(requirement) for requirement in manifest.dependencies.pythonDependencies
    ]
    if not requirements:
        return []
    try:
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name

        from gideon.extensions.apps.app_manager import _core_requirement_pins

        core = _core_requirement_pins()
    except Exception:
        logger.debug(
            "app disclosure could not read core package ownership", exc_info=True
        )
        return [{"spec": spec, "coreOwned": False} for spec in requirements]

    result: list[dict[str, Any]] = []
    for spec in requirements:
        try:
            core_owned = canonicalize_name(Requirement(spec).name) in core
        except Exception:
            core_owned = False
        result.append({"spec": spec, "coreOwned": core_owned})
    return result


def _requires(manifest: AppManifest) -> list[dict[str, str]]:
    if not isinstance(manifest.requires, list):
        return []
    return [
        item.to_dict()
        for item in manifest.requires
        if callable(getattr(item, "to_dict", None))
    ]


def _backend_sandbox(manifest: AppManifest) -> str:
    if not manifest.backend.entryPoint:
        return ""
    return manifest.backend.sandbox or ""


def _providers(manifest: AppManifest) -> list[dict[str, str]]:
    return [
        {
            "type": provider.type,
            "implementation": provider.implementation,
            "execution": provider.execution,
        }
        for provider in manifest.all_providers()
    ]


def _hooks(manifest: AppManifest) -> list[dict[str, str]]:
    raw = manifest.extra.get("hooks", [])
    if not isinstance(raw, list):
        return []
    return [
        {
            "name": hook.get("name", ""),
            "event": hook.get("event", ""),
            "provider": hook.get("provider", ""),
        }
        for hook in raw
        if isinstance(hook, dict)
    ]


def _sources(manifest: AppManifest) -> list[dict[str, str]]:
    return [
        {"name": source.name, "script": source.script}
        for source in manifest.sources
        if source.script
    ]


def _mcp_servers(manifest: AppManifest) -> list[dict[str, str]]:
    servers: list[dict[str, str]] = []
    for name, config in manifest.mcpServers.items():
        if not isinstance(config, dict):
            continue
        command = str(config.get("command") or "").strip()
        args = config.get("args", [])
        if not isinstance(args, list):
            args = [args]
        if command:
            command_token = command.strip().split(maxsplit=1)[0].replace("\\", "/")
            command_name = PurePosixPath(command_token).name or "command"
            invocation = f"Command {command_name} · {len(args)} arguments · values hidden: {SECRET_MASK}"
        else:
            invocation = _safe_endpoint(str(config.get("url") or ""))
        servers.append({"name": str(name), "launches": invocation})
    return servers


def _safe_endpoint(value: str) -> str:
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return f"Remote server endpoint · {SECRET_MASK}"
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        query_keys = sorted(
            {key for key, _value in parse_qsl(parts.query, keep_blank_values=True)}
        )
        suffix = f" · query keys: {', '.join(query_keys)}" if query_keys else ""
        return f"Remote server · {parts.scheme}://{host}{parts.path}{suffix}"
    except ValueError:
        return f"Remote server endpoint · {SECRET_MASK}"


def _skills(manifest: AppManifest) -> list[str]:
    names: list[str] = []
    for skill in manifest.skills:
        relative = str(skill.path or "").strip().strip("/")
        if relative:
            names.append(PurePosixPath(relative).name)
    return names


def _runs_as_you(projection: dict[str, Any]) -> str:
    actions: list[str] = []
    if any(
        not dependency["coreOwned"] for dependency in projection["pythonDependencies"]
    ):
        actions.append("loads declared Python packages into the gateway")
    if projection["hasBackend"]:
        actions.append("starts an app backend")
    if any(
        provider["execution"] == "in-process" for provider in projection["providers"]
    ):
        actions.append("loads provider code into the gateway")
    if projection["mcpServers"]:
        actions.append("starts or connects to MCP servers")
    if projection["sources"]:
        actions.append("runs connector scripts")
    if (
        any(
            projection[key]
            for key in ("onInstall", "onUpdate", "onEnable", "onDisable", "onUninstall")
        )
        or projection["hooks"]
    ):
        actions.append("runs app lifecycle commands")
    if projection.get("launches"):
        programs = [entry["program"] for entry in projection["launches"]]
        named = [name for name in programs if name != "*"]
        if named:
            actions.append("starts " + ", ".join(named))
        if len(named) != len(programs):
            actions.append("starts programs you name")
    if projection.get("npmPackages"):
        actions.append("installs the declared npm packages")
    if projection.get("writes"):
        actions.append("writes to the declared external locations")
    if not actions:
        return ""
    return "This app " + ", and ".join(actions) + "."


def _launches(m: AppManifest) -> list[dict[str, Any]]:
    try:
        props: dict[str, Any] = {}
        for p in m.all_providers():
            props.update((p.settingsSchema or {}).get("properties") or {})
        out: list[dict[str, Any]] = []
        for launch in m.launches:
            cond = launch.inheritsWhile
            condition: dict[str, Any] | None = None
            if cond is not None:
                spec = props.get(cond.setting)
                spec = spec if isinstance(spec, dict) else {}
                meta = spec.get("x-meta")
                label = str(meta.get("label") or "") if isinstance(meta, dict) else ""
                default = spec.get("default")
                condition = {
                    "setting": cond.setting,
                    "label": label or cond.setting,
                    "value": cond.value,
                    "default": default if isinstance(default, bool) else None,
                }
            out.append(
                {
                    "program": launch.program,
                    "why": launch.why,
                    "inherits": list(launch.inherits),
                    "inheritsWhile": condition,
                    "npmPackage": launch.npmPackage,
                    "hosts": list(launch.hosts),
                }
            )
        return out
    except Exception:
        logger.debug("disclosure: launches unreadable for %s", m.name, exc_info=True)
        return []
