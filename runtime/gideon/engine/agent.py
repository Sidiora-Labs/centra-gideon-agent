"""Resolve and install the configuration consumed by Gideon's agent runners."""

import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Iterator

from gideon.core.layout import package_path
from gideon.security.security import is_sensitive_path, redact
from gideon.security.sel import SecurityEvent, sel

logger = logging.getLogger(__name__)


def _user_dir() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir()


_USER_DIR = _user_dir()
AGENTS_DIR = _USER_DIR / "agents"
AGENT_FILENAME = "gideon.json"
_USER_MCP_JSON = _USER_DIR / "mcp.json"
_USER_PROMPT = _USER_DIR / "prompt.md"
_USER_OVERRIDES = _USER_DIR / "agent.json"
_BUNDLED_CFG_DIR = package_path("core", "config")
_GIDEON_BIN: str | None = None
_DEFAULT_HOOKS_DIR = _USER_DIR / "hooks"
_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9/_.\-]+$")
_SAFE_MATCHER_RE = re.compile(r"^[a-zA-Z0-9_.*\-]+$")
_MAX_MATCHER_LEN = 200
_VALID_HOOK_EVENTS = frozenset(
    ("preToolUse", "postToolUse", "userPromptSubmit", "agentSpawn", "stop")
)
_MAX_USER_HOOKS_PER_EVENT = 10
_MAX_TOTAL_USER_HOOKS = 20
_HOOK_EVENT_CANONICAL = {event.lower(): event for event in _VALID_HOOK_EVENTS}
_FILENAME_EVENT_SUFFIXES = (
    ("-post.sh", "postToolUse"),
    ("-prompt.sh", "userPromptSubmit"),
    ("-spawn.sh", "agentSpawn"),
    ("-stop.sh", "stop"),
    ("-pre.sh", "preToolUse"),
)
_HOOK_HEADER_SCAN_LINES = 5
_HOOK_HEADER_RE = re.compile(r"^\s*#\s*(event|matcher)\s*:\s*(\S.*?)\s*$", re.I)


def _atomic_json_write(path: Path, data: dict) -> None:
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            staging = Path(stream.name)
            try:
                permissions = stat.S_IMODE(path.stat().st_mode)
            except FileNotFoundError:
                permissions = 0o644
            os.fchmod(stream.fileno(), permissions)
            stream.write(json.dumps(data, indent=2) + "\n")
        try:
            os.replace(staging, path)
        except OSError:
            # A bind-mounted destination can require copying instead of replacement.
            shutil.copy2(staging, path)
    finally:
        if staging is not None:
            try:
                staging.unlink()
            except OSError:
                pass


def _project_dir() -> Path | None:
    value = os.environ.get("GIDEON_PROJECT_DIR", "")
    candidate = Path(value) if value else None
    return candidate if candidate is not None and candidate.is_dir() else None


def _project_resource(relative: str, fallback: Path) -> Path:
    project = _project_dir()
    if project is not None:
        candidate = project / relative
        if candidate.is_file():
            return candidate
    return fallback


def _shipped_defaults() -> Path:
    return _project_resource("agents/defaults.json", _BUNDLED_CFG_DIR / "defaults.json")


def _shipped_prompt() -> Path:
    return _project_resource(
        "agents/prompt.md", _BUNDLED_CFG_DIR / "prompts" / "chat.md"
    )


def _prompt_path(mode: str = "") -> Path:
    return _USER_PROMPT if _USER_PROMPT.is_file() else _shipped_prompt()


def _bin_is_usable(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            stream.read(1)
    except OSError:
        return False
    return True


def _launcher_candidates() -> Iterator[str]:
    def usable(candidate: str) -> bool:
        return (
            bool(candidate)
            and os.path.isfile(candidate)
            and os.access(candidate, os.X_OK)
            and _bin_is_usable(Path(candidate))
        )

    try:
        import gideon

        directory = Path(gideon.__file__).resolve().parent
        for ancestor in directory.parents:
            candidate = str(ancestor / "bin" / "gideon")
            if usable(candidate):
                yield candidate
            if (ancestor / "pyvenv.cfg").exists():
                break
    except Exception:
        logger.debug("Package launcher discovery failed", exc_info=True)
    try:
        platform_bin = "Scripts" if os.name == "nt" else "bin"
        directories = dict.fromkeys(
            (Path(sys.executable).parent, Path(sys.prefix) / platform_bin)
        )
        for directory in directories:
            for filename in ("gideon", "gideon.exe"):
                candidate = str(directory / filename)
                if usable(candidate):
                    yield candidate
    except Exception:
        logger.debug("Interpreter launcher discovery failed", exc_info=True)
    match = shutil.which("gideon")
    if match and usable(match):
        yield match


def _resolve_gideon_bin() -> str:
    global _GIDEON_BIN
    if _GIDEON_BIN:
        return _GIDEON_BIN
    candidate = next(_launcher_candidates(), None)
    if candidate is not None:
        _GIDEON_BIN = candidate
        return candidate
    logger.warning("Gideon executable was not found; using the command name")
    return "gideon"


_MANAGED_MCP_SERVERS = {
    "gideon-core": {"command_fn": _resolve_gideon_bin, "args": ["mcp-core"]}
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring invalid %s: %s", path, exc)
    else:
        if isinstance(value, dict):
            return value
        logger.warning("Ignoring %s: top-level JSON is not an object", path)
    return {}


def _deep_merge(base: dict, override: dict) -> dict:
    def value_for(key: str) -> Any:
        if key not in override:
            return base[key]
        replacement = override[key]
        if isinstance(base.get(key), dict) and isinstance(replacement, dict):
            return base[key] | replacement
        return replacement

    return {key: value_for(key) for key in dict.fromkeys((*base, *override))}


def _all_skill_paths() -> list[str]:
    from gideon.extensions.skills.loader import skills_dir
    from gideon.extensions.skills.native import _bundled_root

    directories = [skills_dir(), Path.home() / ".agents" / "skills", _bundled_root()]
    project = _project_dir()
    if project is not None:
        directories.append(project / "skills")
    return sorted({str(directory) for directory in directories if directory.is_dir()})


def _inject_skill_paths(bm: dict, skill_paths: list[str]) -> None:
    remaining = iter(bm.get("args", []))
    arguments = []
    for argument in remaining:
        if argument == "--skill-paths":
            next(remaining, None)
        else:
            arguments.append(argument)
    directories = [directory for directory in skill_paths if Path(directory).is_dir()]
    if directories:
        arguments += ["--skill-paths", ",".join(directories)]
    bm["args"] = arguments


def _validate_hook_command(command: str, event: str) -> str | None:
    reason = None
    resolved = None
    if not _SAFE_PATH_RE.match(command):
        reason = "command contains disallowed characters"
    elif not os.path.isabs(command):
        reason = "command must be absolute path"
    else:
        resolved = str(Path(command).resolve())
        if not _SAFE_PATH_RE.match(resolved):
            reason = "resolved path contains disallowed characters"
        elif is_sensitive_path(resolved):
            reason = "command points to sensitive path"
        elif not os.path.isfile(resolved):
            reason = "command not found"
    if reason is not None:
        logger.warning("agent_hooks[%s]: %s: %r", event, reason, command)
        return None
    return resolved


def _hook_audit(
    operation: str, outcome: str, resources: str, error: str | None = None
) -> None:
    try:
        fields: dict = {
            "event_id": uuid.uuid4().hex[:16],
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "event_type": "config_hooks_merge",
            "caller_identity": "agent_install",
            "agent": "gideon",
            "source": "cli",
            "operation": operation,
            "outcome": outcome,
            "resources": redact(resources),
        }
        if error is not None:
            fields["error"] = error
        sel().log(SecurityEvent(**fields))
    except Exception:
        logger.debug("Could not record hook configuration audit", exc_info=True)


def _sel_hook_rejected(event: str, command: str, reason: str) -> None:
    _hook_audit(
        "agent_hooks_rejected",
        "rejected",
        f"event={event} command={command[:200]}",
        reason,
    )


def _reject_hook(
    event: str, command: str, reason: str, *, level: int = logging.WARNING
) -> None:
    logger.log(level, "agent_hooks[%s]: %s: %s", event, reason, command)
    _sel_hook_rejected(event, command, reason)


def _valid_matcher(value: str) -> bool:
    return len(value) <= _MAX_MATCHER_LEN and bool(_SAFE_MATCHER_RE.match(value))


def _parse_hook_script_headers(path: Path) -> tuple[str | None, str | None]:
    headers: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in islice(stream, _HOOK_HEADER_SCAN_LINES):
                match = _HOOK_HEADER_RE.match(line)
                if match:
                    headers.setdefault(match[1].lower(), match[2].strip())
    except OSError:
        logger.debug("Cannot read hook headers from %s", path, exc_info=True)
    return headers.get("event"), headers.get("matcher")


def _infer_hook_event(script_path: Path, event_header: str | None) -> str | None:
    if event_header is not None:
        key = event_header.lower().replace("-", "").replace("_", "")
        return _HOOK_EVENT_CANONICAL.get(key)
    filename = script_path.name.lower()
    return next(
        (
            event
            for suffix, event in _FILENAME_EVENT_SUFFIXES
            if filename.endswith(suffix)
        ),
        "preToolUse",
    )


class _HookDirectory:
    def __init__(self, directory: Path):
        self.requested = directory
        self.root: Path | None = None

    def entries(self) -> list[Path]:
        try:
            self.root = self.requested.resolve()
        except (OSError, ValueError):
            _reject_hook(
                "autoimport",
                str(self.requested),
                "cannot resolve hooks_dir",
                level=logging.DEBUG,
            )
            return []
        try:
            return sorted(self.root.iterdir())
        except FileNotFoundError:
            logger.debug("No hook directory at %s", self.root)
        except OSError:
            _reject_hook("autoimport", str(self.root), "cannot read hooks_dir")
        return []

    def read(self, entry: Path) -> tuple[str, dict] | None:
        if not entry.is_file() or entry.suffix != ".sh":
            return None
        try:
            target = entry.resolve()
        except (OSError, ValueError):
            _reject_hook("autoimport", str(entry), "cannot resolve entry")
            return None
        if target != self.root and self.root not in target.parents:
            logger.warning("Hook %s resolves outside %s", entry, self.root)
            _sel_hook_rejected(
                "autoimport", str(entry), "resolved path escapes hooks dir"
            )
            return None
        try:
            executable = bool(
                target.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            )
        except OSError:
            _reject_hook("autoimport", str(entry), "cannot stat entry")
            return None
        if not executable:
            _reject_hook(
                "autoimport", str(target), "not executable", level=logging.INFO
            )
            return None
        command = _validate_hook_command(str(target), "autoimport")
        if command is None:
            _reject_hook("autoimport", str(target), "failed validation")
            return None
        event_header, matcher = _parse_hook_script_headers(target)
        event = _infer_hook_event(entry, event_header)
        if event is None:
            _reject_hook("autoimport", str(target), "unknown event header")
            return None
        if matcher is not None and not _valid_matcher(matcher):
            _reject_hook("autoimport", str(target), "invalid matcher")
            return None
        hook = {"command": command}
        if matcher is not None:
            hook["matcher"] = matcher
        return event, hook


def _autoimport_agent_hooks(hooks_dir: Path) -> dict[str, list[dict[str, str]]]:
    scanner = _HookDirectory(hooks_dir)
    hooks: dict[str, list[dict[str, str]]] = {}
    for path in scanner.entries():
        accepted = scanner.read(path)
        if accepted is not None:
            event, hook = accepted
            hooks.setdefault(event, []).append(hook)
    count = sum(map(len, hooks.values()))
    logger.log(
        logging.INFO if count else logging.DEBUG,
        "Imported %d hooks from %s",
        count,
        hooks_dir,
    )
    return hooks


def _sanitize_hook(event: str, entry: Any) -> dict[str, str] | None:
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("command"), str)
        or not entry["command"]
    ):
        _reject_hook(event, str(entry)[:200], "missing or invalid command")
        return None
    command = _validate_hook_command(entry["command"], event)
    reason = None
    matcher = entry.get("matcher")
    if command is None:
        reason = "failed validation"
    elif matcher is not None and not isinstance(matcher, str):
        reason = "non-string matcher"
    elif isinstance(matcher, str) and not _valid_matcher(matcher):
        reason = "invalid matcher"
    if reason is not None:
        _reject_hook(event, entry["command"], reason)
        return None
    accepted = {"command": command}
    if isinstance(matcher, str):
        accepted["matcher"] = matcher
    return accepted


class _HookAdmission:
    def __init__(self, bundled: dict):
        self.hooks = dict(bundled)
        self.count = 0

    def extend(self, event: str, entries: Any) -> None:
        if event not in _VALID_HOOK_EVENTS:
            _reject_hook(str(event), str(entries)[:200], "unknown event type")
            return
        if not isinstance(entries, list):
            _reject_hook(event, str(entries)[:200], "entries not a list")
            return
        accepted = list(self.hooks.get(event, []))
        seen = {
            (entry.get("command"), entry.get("matcher"))
            for entry in accepted
            if isinstance(entry, dict)
        }
        initial_count = len(accepted)
        for entry in entries:
            limit = None
            if len(accepted) - initial_count >= _MAX_USER_HOOKS_PER_EVENT:
                limit = "per-event limit exceeded"
            elif self.count >= _MAX_TOTAL_USER_HOOKS:
                limit = "global limit exceeded"
            if limit:
                command = entry.get("command", "") if isinstance(entry, dict) else entry
                _reject_hook(event, str(command)[:200], limit)
                break
            hook = _sanitize_hook(event, entry)
            if hook is None:
                continue
            identity = (hook["command"], hook.get("matcher"))
            if identity in seen:
                continue
            accepted.append(hook)
            seen.add(identity)
            self.count += 1
        self.hooks[event] = accepted


def _merge_agent_hooks(hooks: dict, user_hooks: dict) -> dict:
    if not isinstance(user_hooks, dict):
        logger.warning("agent_hooks is not a dict, ignoring")
        return hooks
    admission = _HookAdmission(hooks)
    for event, entries in user_hooks.items():
        admission.extend(event, entries)
    return admission.hooks


def _configured_hooks_directory(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        return _DEFAULT_HOOKS_DIR
    requested = Path(os.path.expanduser(value))
    try:
        directory = requested.resolve()
        home = Path.home().resolve()
        allowed = home in directory.parents and not is_sensitive_path(str(directory))
    except (OSError, ValueError):
        allowed = False
    if allowed:
        return directory
    logger.warning("agent_hooks_dir %r rejected; using %s", value, _DEFAULT_HOOKS_DIR)
    _sel_hook_rejected(
        "autoimport", str(requested), "agent_hooks_dir outside HOME or sensitive"
    )
    return _DEFAULT_HOOKS_DIR


def _hook_count(hooks: dict) -> int:
    return sum(len(entries) for entries in hooks.values() if isinstance(entries, list))


def _apply_user_agent_hooks(config: dict, runtime_config: dict) -> None:
    settings = runtime_config.get("agent")
    settings = settings if isinstance(settings, dict) else {}
    explicit = settings.get("agent_hooks")
    explicit = explicit if isinstance(explicit, dict) else {}
    discover = bool(settings.get("agent_hooks_autoimport", True))
    directory = _configured_hooks_directory(settings.get("agent_hooks_dir"))
    if not explicit and not discover:
        return
    before = _hook_count(config.get("hooks", {}))
    combined: dict[str, list] = {}
    explicit_count = 0
    for event, entries in explicit.items():
        if isinstance(entries, list):
            combined[event] = list(entries)
            explicit_count += len(entries)
        else:
            _reject_hook(str(event), str(entries)[:200], "entries not a list")
    discovered = _autoimport_agent_hooks(directory) if discover else {}
    discovered_count = _hook_count(discovered)
    if explicit_count + discovered_count == 0:
        config.setdefault("hooks", {})
        return
    for event, entries in discovered.items():
        if isinstance(entries, list):
            combined.setdefault(event, []).extend(entries)
    config["hooks"] = _merge_agent_hooks(config.get("hooks", {}), combined)
    added = _hook_count(config["hooks"]) - before
    _hook_audit(
        "agent_hooks_merge",
        "completed",
        f"requested_explicit={explicit_count} requested_autoimport={discovered_count} added={added}",
    )


def _bundled_hooks(bundled: dict) -> dict:
    from gideon.core.config import config_dir

    destination = str(config_dir() / "audit.log")

    def resolve(event: str, entry: Any) -> Any:
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
            return entry
        command = entry["command"].replace("{{GIDEON_AUDIT_LOG}}", destination)
        if "{{" in command:
            raise RuntimeError(
                f"bundled hook {event!r} has an unresolved placeholder: {command!r}"
            )
        return dict(entry, command=command)

    return {
        event: (
            [resolve(event, entry) for entry in entries]
            if isinstance(entries, list)
            else entries
        )
        for event, entries in (bundled.get("hooks") or {}).items()
    }


def _install_runtime_fields(config: dict, *, replace_managed: bool) -> None:
    config["prompt"] = f"file://{_prompt_path()}"
    servers = config.setdefault("mcpServers", {})
    for name, specification in _MANAGED_MCP_SERVERS.items():
        created = replace_managed or name not in servers
        if replace_managed:
            servers[name] = {}
        target = servers.setdefault(name, {})
        target.update(
            command=specification.get("command") or specification["command_fn"](),
            args=list(specification["args"]),
        )
        if created and "autoApprove" in specification:
            target["autoApprove"] = list(specification["autoApprove"])


def _install_security_hooks(config: dict, *, phase: str) -> None:
    from gideon.core.config import config_path

    bundled = _load_json(_BUNDLED_CFG_DIR / "defaults.json")
    if phase == "refresh security fields":
        if bundled is None:
            raise RuntimeError(
                f"Cannot {phase}: bundled defaults.json is missing or unreadable"
            )
        if not isinstance(bundled, dict):
            raise RuntimeError(
                f"Cannot {phase}: bundled defaults.json is not a JSON object"
            )
    hooks = _bundled_hooks(bundled)
    if not hooks:
        raise RuntimeError(f"Cannot {phase}: hooks missing from bundled defaults")
    config["hooks"] = hooks
    _apply_user_agent_hooks(config, _load_json(config_path()) or {})


def build_agent_config() -> dict:
    config = _deep_merge(_load_json(_shipped_defaults()), _load_json(_USER_OVERRIDES))
    _install_security_hooks(config, phase="build agent config")
    _install_runtime_fields(config, replace_managed=True)
    return config


def _refresh_dynamic_fields(config: dict) -> None:
    from gideon.interfaces.dashboard.chat_utils import _normalize_model

    _install_runtime_fields(config, replace_managed=False)
    _install_security_hooks(config, phase="refresh security fields")
    previous = config.get("model", "")
    updated = _normalize_model(previous)
    if updated != previous:
        config["model"] = updated


def get_shipped_tools() -> dict[str, list[str]]:
    source = _load_json(_shipped_defaults()) or {}
    return {name: source.get(name, []) for name in ("tools", "allowedTools")}


def _load_existing_config(path: Path) -> tuple[dict, bool]:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if isinstance(existing, dict):
        try:
            _refresh_dynamic_fields(existing)
        except (AttributeError, TypeError, RuntimeError) as exc:
            logger.error("Refresh failed, rebuilding from defaults: %s", exc)
        else:
            return existing, False
    return build_agent_config(), True


class _ConfigAssembly:
    def __init__(self, config: dict, fresh: bool):
        self.config = config
        self.fresh = fresh
        self.shared = _load_json(_USER_DIR / "mcp.json").get("mcpServers", {})

    def overlay(self) -> None:
        for name, specification in self.shared.items():
            if name in _MANAGED_MCP_SERVERS or not isinstance(specification, dict):
                continue
            servers = self.config.setdefault("mcpServers", {})
            installed = servers.get(name)
            if isinstance(installed, dict):
                installed.update(specification)
            else:
                servers[name] = specification

    def resolve(self) -> None:
        servers = {}
        for name, specification in self.config.get("mcpServers", {}).items():
            if not isinstance(specification, dict):
                continue
            if specification.get("url"):
                servers[name] = specification
                continue
            command = specification.get("command", "")
            if not command:
                logger.warning("Dropping MCP server %r: no command", name)
                continue
            if (
                os.path.isabs(command)
                and os.path.isfile(command)
                and os.access(command, os.X_OK)
            ):
                location = command
            else:
                custom_path = specification.get("env", {}).get("PATH", "")
                search = (
                    custom_path + os.pathsep if custom_path else ""
                ) + os.environ.get("PATH", "")
                location = shutil.which(command, path=search)
            if location:
                specification["command"] = location
                servers[name] = specification
            else:
                logger.warning(
                    "Dropping MCP server %r: command not found: %s", name, command
                )
        self.config["mcpServers"] = servers

    @staticmethod
    def audit(references: list[str], operation: str, detail: str) -> None:
        if references:
            sel().log_api_access(
                caller="system",
                operation=operation,
                outcome="ok",
                source="rebuild_agent_config",
                resources=f"{', '.join(references)} {detail}",
            )

    def register_tools(self) -> None:
        added, removed = [], []
        servers = self.config["mcpServers"]
        for name, specification in self.shared.items():
            if not isinstance(specification, dict) or name in _MANAGED_MCP_SERVERS:
                continue
            reference = f"@{name}"
            disabled = specification.get("disabled")
            if not disabled and name not in servers:
                continue
            if not disabled:
                servers[name].pop("disabled", None)
            for field in ("tools", "allowedTools"):
                if disabled:
                    values = self.config.get(field)
                    if values is not None and reference in values:
                        values.remove(reference)
                        if reference not in removed:
                            removed.append(reference)
                elif reference not in self.config.get(field, []):
                    self.config.setdefault(field, []).append(reference)
                    if reference not in added:
                        added.append(reference)
        self.audit(added, "mcp_tools_added", "added to tools/allowedTools (shared)")
        self.audit(
            removed, "mcp_tools_removed", "removed from tools/allowedTools (disabled)"
        )
        if self.fresh:
            managed = [
                f"@{name}"
                for name in _MANAGED_MCP_SERVERS
                if name in servers and f"@{name}" not in self.config.get("tools", [])
            ]
            if managed:
                self.config.setdefault("tools", []).extend(managed)
            self.audit(managed, "mcp_tools_added", "added to tools (fresh install)")
        for field in ("tools", "allowedTools"):
            self.config[field] = list(dict.fromkeys(self.config.get(field, [])))


def rebuild_agent_config(*, clean: bool = False) -> Path:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    destination = AGENTS_DIR / AGENT_FILENAME
    if clean or not destination.exists():
        configuration, fresh = build_agent_config(), True
    else:
        configuration, fresh = _load_existing_config(destination)
    assembly = _ConfigAssembly(configuration, fresh)
    assembly.overlay()
    assembly.resolve()
    assembly.register_tools()
    _atomic_json_write(destination, configuration)
    logger.info("Installed agent config: %s", destination)
    return destination
