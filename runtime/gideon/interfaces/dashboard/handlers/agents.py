"""Agent configuration, themes, marketplace integration, and agent CRUD handlers."""

import asyncio
import dataclasses
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from gideon.core.config import loader as config_loader
from gideon.core.config.edit_spec import ConfigValueError, coerce_edit_value
from gideon.core.config.loader import AgentProfile, AppConfig, resolve_agent_config_path
from gideon.core.config.schema import SCHEMA_REGISTRY, config_entry_to_dict
from gideon.core.http_request import read_json_body
from gideon.extensions.providers.failure_copy import relayed_failure_copy
from gideon.http_errors import json_error
from gideon.interfaces.dashboard.chat_utils import _SLASH_COMMAND_HINTS
from gideon.interfaces.dashboard.state import ConsoleState


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import gideon.interfaces.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


_THEMES_DIR_NAME = "themes"
_THEME_NAME_MAX_LEN = 60
_THEME_SLUG_MAX_LEN = 40
_THEME_EMOJI_MAX_LEN = 48
_THEME_DEFAULT_EMOJI = "🎨"
_THEME_REQUIRED_VARS = ("--color-primary",)

_THEME_CSS_VARS = (
    "--color-primary",
    "--color-primary-emphasis",
    "--color-on-primary",
    "--color-primary-container",
    "--color-secondary",
    "--color-canvas",
    "--color-surface",
    "--color-surface-low",
    "--color-surface-container",
    "--color-surface-high",
    "--color-surface-highest",
    "--color-rail",
    "--color-on-surface",
    "--color-on-surface-low",
    "--color-on-surface-var",
    "--color-outline",
    "--color-outline-variant",
    "--color-ok",
    "--color-warn",
    "--color-danger",
    "--color-info",
    "--grad-1",
    "--grad-2",
    "--grad-3",
    "--grad-4",
    "--glow-a",
    "--glow-b",
    "--ring-stop-2",
)


def _themes_dir() -> Path:
    """Return the custom themes directory under config_dir()."""
    return config_dir() / _THEMES_DIR_NAME


_CSS_VALUE_ALLOWED_RE = re.compile(r"^[a-zA-Z0-9#(),.\- %/]+$")

_CSS_DANGEROUS_FUNC_RE = re.compile(
    r"url\s*\(|expression\s*\(|image\s*\(|image-set\s*\(",
    re.IGNORECASE,
)

_THEME_CSS_VARS_SET: frozenset[str] = frozenset(_THEME_CSS_VARS)


def _sanitize_css_value(value: str) -> str | None:
    """Validate a single CSS value using a positive character allowlist.

    Returns the trimmed value if safe, or None if rejected.
    """
    if not isinstance(value, str):
        return None
    if len(value) > 200:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if not _CSS_VALUE_ALLOWED_RE.match(trimmed):
        return None
    if _CSS_DANGEROUS_FUNC_RE.search(trimmed):
        return None
    return trimmed


def _validate_theme_data(data: dict) -> str | None:
    """Validate a theme JSON object. Returns error string or None.

    Validates keys against ``_THEME_CSS_VARS_SET`` allowlist.
    Unknown keys are rejected.
    """
    if not isinstance(data, dict):
        return "theme must be a JSON object"
    name = data.get("name", "")
    if not isinstance(name, str):
        return "name must be a string"
    name = name.strip()
    if not name:
        return "name is required"
    if len(name) > _THEME_NAME_MAX_LEN:
        return f"name too long (max {_THEME_NAME_MAX_LEN} chars)"
    if "/" in name or "\\" in name or ".." in name:
        return "name cannot contain path-separator or traversal characters"
    emoji = data.get("emoji", "")
    if not isinstance(emoji, str):
        return "emoji must be a string"
    for mode in ("dark", "light"):
        mode_data = data.get(mode, {})
        if not isinstance(mode_data, dict):
            return f"'{mode}' must be a JSON object"
        for required_var in _THEME_REQUIRED_VARS:
            if required_var not in mode_data:
                return f"'{mode}' is missing required" f" variable '{required_var}'"
        for key, val in mode_data.items():
            if key not in _THEME_CSS_VARS_SET:
                return f"'{mode}' key '{key}' is not a recognized theme variable"
            if _sanitize_css_value(val) is None:
                return f"'{mode}' variable '{key}' has an invalid value"
    return None


def _strip_to_allowed_vars(mode_data: dict[str, str]) -> dict[str, str]:
    """Return only the allowed CSS vars with sanitized values.

    Defense-in-depth: even after validation, re-filter before writing
    so only known variables with clean values reach disk.
    """
    result: dict[str, str] = {}
    for key, val in mode_data.items():
        if key not in _THEME_CSS_VARS_SET:
            continue
        clean = _sanitize_css_value(val)
        if clean is not None:
            result[key] = clean
    return result


def _slugify_theme_name(name: str) -> str:
    """Convert a theme name to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9\-]", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:_THEME_SLUG_MAX_LEN] or "custom"


async def api_themes(request: web.Request) -> web.Response:
    """GET /api/themes — list all custom themes, sorted by creation date."""
    themes_path = _themes_dir()
    result: list[dict[str, Any]] = []
    if themes_path.is_dir():
        for f in themes_path.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                result.append(
                    {
                        "slug": f.stem,
                        "name": data.get("name", f.stem),
                        "emoji": data.get("emoji", "🎨"),
                        "created_at": data.get("created_at", ""),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue
    result.sort(key=lambda t: t.get("created_at") or "9999")
    return web.json_response({"themes": result})


async def api_themes_create(request: web.Request) -> web.Response:
    """POST /api/themes — create a new custom theme."""
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    err = _validate_theme_data(body)
    if err:
        return web.json_response({"error": err}, status=400)

    name = body["name"].strip()
    slug = _slugify_theme_name(name)
    emoji = (
        body.get("emoji", _THEME_DEFAULT_EMOJI).strip()[:_THEME_EMOJI_MAX_LEN]
        or _THEME_DEFAULT_EMOJI
    )

    themes_path = _themes_dir()
    themes_path.mkdir(parents=True, exist_ok=True)
    target = themes_path / f"{slug}.json"
    if target.exists():
        return web.json_response(
            {"error": f"theme '{slug}' already exists"}, status=409
        )

    theme_data = {
        "name": name,
        "slug": slug,
        "emoji": emoji,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dark": _strip_to_allowed_vars(body.get("dark", {})),
        "light": _strip_to_allowed_vars(body.get("light", {})),
    }
    target.write_text(json.dumps(theme_data, indent=2) + "\n", encoding="utf-8")
    return web.json_response({"ok": True, "slug": slug, "theme": theme_data})


async def api_theme_detail(request: web.Request) -> web.Response:
    """GET/PUT/DELETE /api/themes/{slug} — get, update, or delete a custom theme."""
    slug = request.match_info["slug"]
    safe_slug = re.sub(r"[^a-z0-9\-]", "", slug)
    if not safe_slug or safe_slug != slug:
        return web.json_response({"error": "invalid theme slug"}, status=400)

    target = _themes_dir() / f"{safe_slug}.json"

    if request.method == "DELETE":
        if not target.exists():
            return web.json_response({"error": "not found"}, status=404)
        target.unlink()
        return web.json_response({"ok": True})

    if request.method == "PUT":
        if not target.exists():
            return web.json_response({"error": "not found"}, status=404)
        try:
            body = await read_json_body(request)
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "JSON body must be an object"}, status=400
            )
        err = _validate_theme_data(body)
        if err:
            return web.json_response({"error": err}, status=400)
        name = body["name"].strip()
        emoji = (
            body.get("emoji", _THEME_DEFAULT_EMOJI).strip()[:_THEME_EMOJI_MAX_LEN]
            or _THEME_DEFAULT_EMOJI
        )
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        theme_data = {
            "name": name,
            "slug": safe_slug,
            "emoji": emoji,
            "created_at": existing.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
            "dark": _strip_to_allowed_vars(body.get("dark", {})),
            "light": _strip_to_allowed_vars(body.get("light", {})),
        }
        target.write_text(json.dumps(theme_data, indent=2) + "\n", encoding="utf-8")
        return web.json_response({"ok": True, "theme": theme_data})

    if not target.exists():
        return web.json_response({"error": "not found"}, status=404)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return web.json_response({"error": "failed to read theme"}, status=500)
    return web.json_response(data)


def _auto_install_agent() -> None:
    """Re-install agent config so changes take effect immediately."""
    try:
        from gideon.engine.agent import rebuild_agent_config  # noqa: F811

        rebuild_agent_config()
        logger.info("Auto-applied agent config via dashboard")
    except Exception:
        logger.debug("Auto-apply agent config failed", exc_info=True)


def _find_agent_config() -> Path:
    """Find agents/defaults.json — delegates to centralized resolver."""
    return resolve_agent_config_path()


def _installed_agent_config() -> Path:
    """Return the installed agent config path (~/.gideon/agents/gideon.json).

    This is the live config that ACP agent reads.  Dashboard MCP toggle
    and sync operations write here — NOT to agents/defaults.json.
    """
    from gideon.engine.agent import AGENTS_DIR  # noqa: F811
    from gideon.engine.agent import AGENT_FILENAME

    return AGENTS_DIR / AGENT_FILENAME


async def api_agent_config(request: web.Request) -> web.Response:
    """GET/PUT /api/agent/config — read or write the installed agent config.

    Reads/writes ``~/.gideon/agents/gideon.json`` — the live config that
    ACP agent actually uses at runtime.  Falls back to ``agents/defaults.json``
    if the installed config doesn't exist yet.
    """
    import gideon.interfaces.dashboard.handlers as _h  # noqa: F811

    installed_path = _h._installed_agent_config()
    defaults_path = _h._find_agent_config()
    agent_config_path = installed_path if installed_path.is_file() else defaults_path

    if request.method == "PUT":
        try:
            body = await read_json_body(request)
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "JSON body must be an object"}, status=400
            )
        config = body.get("config")
        if not isinstance(config, dict):
            return web.json_response({"error": "config must be an object"}, status=400)
        try:
            from gideon.engine.agent import get_shipped_tools  # noqa: F811

            shipped = get_shipped_tools()
            removed_per_key: dict[str, list[str]] = {}
            for key in ("tools", "allowedTools"):
                diff = sorted(set(shipped.get(key, [])) - set(config.get(key, [])))
                if diff:
                    removed_per_key[key] = diff
            pc_cfg_path = _h.config_path()  # type: ignore[operator]
            try:
                pc_cfg = (
                    json.loads(pc_cfg_path.read_text(encoding="utf-8"))
                    if pc_cfg_path.exists()
                    else {}
                )
            except Exception:
                pc_cfg = {}
            if removed_per_key:
                pc_cfg["removedTools"] = removed_per_key
            else:
                pc_cfg.pop("removedTools", None)
            pc_cfg_path.write_text(
                json.dumps(pc_cfg, indent=2) + "\n", encoding="utf-8"
            )
            installed_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            await _h._reset_all_sessions(request)
            return web.json_response({"ok": True, "applied": True})
        except Exception as exc:
            logger.exception("agent config apply failed")
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
    try:
        data = json.loads(agent_config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    return web.json_response(data)


async def api_default_agent(request: web.Request) -> web.Response:
    """GET/PUT /api/config/default-agent — read or set the default agent."""
    import gideon.interfaces.dashboard.handlers as _h  # noqa: F811

    if request.method == "PUT":
        try:
            body = await read_json_body(request)
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "JSON body must be an object"}, status=400
            )
        if "agent" not in body:
            return web.json_response(
                {"error": "body must carry 'agent' (an agent name, or \"\" to reset)"},
                status=400,
            )
        name = str(body.get("agent", ""))
        if name:
            known = set((AppConfig.load().agents or {}).keys())
            if name not in known:
                return web.json_response(
                    {
                        "error": f"Unknown agent {name!r}. Create it first (Agents), or pick an "
                        f"existing one. Known: {sorted(known)}"
                    },
                    status=400,
                )
        path = _h.config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}
        data["default_agent"] = name
        if isinstance(data.get("agent"), dict):
            data["agent"].pop("default_agent", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return web.json_response({"ok": True, "default_agent": name})
    cfg = AppConfig.load()
    return web.json_response({"default_agent": cfg.default_agent})


async def api_config_schema(request: web.Request) -> web.Response:
    """GET /api/config/schema — return config schema entries."""
    entries = SCHEMA_REGISTRY

    tags_param = request.query.get("tags", "").strip()
    if tags_param:
        requested_tags = {t.strip() for t in tags_param.split(",") if t.strip()}
        entries = [e for e in entries if set(e.tags) & requested_tags]

    dep_param = request.query.get("deprecated", "").strip().lower()
    if dep_param == "false":
        entries = [e for e in entries if not e.deprecated]

    result = []
    for entry in entries:
        d = config_entry_to_dict(entry)
        if entry.sensitive or dataclasses.is_dataclass(d.get("defaultValue")):
            d["defaultValue"] = None
        result.append(d)

    return web.json_response({"entries": result})


async def api_agents_installed(request: web.Request) -> web.Response:
    """GET /api/agents/installed — list installed agent provider names.

    Returns one entry per registered agent in the loaded config plus the
    built-in 'gideon' provider, deduplicated. The frontend uses the
    .name field to populate the agent-provider dropdown on the Agents page.
    """
    cfg = AppConfig.load()
    names: list[str] = []
    seen: set[str] = set()
    for agent_cfg in cfg.agents.values():
        provider = getattr(agent_cfg, "provider_agent", "") or "gideon"
        if provider and provider not in seen:
            seen.add(provider)
            names.append(provider)
    if "gideon" not in seen:
        names.append("gideon")
    return web.json_response([{"name": n} for n in names])


async def api_slash_commands(request: web.Request) -> web.Response:
    """GET /api/slash-commands — the slash commands the composer "/" menu offers.

    Returns only the dashboard-handled set (_SLASH_COMMAND_HINTS), in menu order.
    These map to deterministic actions, so they work on any model. Other "/…"
    text stays typeable and dispatches to the native harness, but isn't advertised
    (an unrecognising model would only improvise it)."""
    return web.json_response(
        [{"name": c, "description": d} for c, d in _SLASH_COMMAND_HINTS.items()]
    )


async def api_agent_detail(request: web.Request) -> web.Response:
    """GET/DELETE/PATCH /api/agents/detail/{name} — view, delete, or update agent config."""
    name = request.match_info["name"]
    from gideon.engine.agent import AGENTS_DIR  # noqa: F811

    patch_body = None
    if request.method == "PATCH":
        try:
            patch_body = await read_json_body(request)
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(patch_body, dict):
            return json_error(
                "invalid_body", message="JSON body must be an object", status=400
            )
        try:
            patch_staged = _staged_agent_fields(patch_body, _AGENT_DETAIL_PATCH_KEYS)
        except ConfigValueError as exc:
            return _agent_write_refusal(exc)

    for f in AGENTS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("name") == name or f.stem == name:
                if request.method == "DELETE":
                    from gideon.engine.agents.defaults import is_reserved_agent

                    if is_reserved_agent(name) or f.name == "gideon.json":
                        return json_error(
                            "forbidden",
                            message=(
                                f"'{name}' is a built-in system agent and cannot be deleted"
                            ),
                            status=403,
                        )
                    f.unlink()
                    state: ConsoleState = request.app["state"]
                    state.push_refresh("agents")
                    _sel().log_api_access(
                        caller=request.get("user", "dashboard"),
                        operation="agent.detail_delete",
                        outcome="success",
                        source="dashboard",
                        resources=name,
                    )
                    return web.json_response({"ok": True})
                if request.method == "PATCH" and patch_body is not None:
                    async with _get_config_lock():
                        data = json.loads(f.read_text(encoding="utf-8"))
                        for key in (
                            "model",
                            "description",
                            "system_prompt",
                            "approval_mode",
                        ):
                            if key in patch_staged:
                                val = patch_staged[key]
                                if val:
                                    data[key] = val
                                else:
                                    data.pop(key, None)
                        for key in ("skills", "tools", "triggers"):
                            if key in patch_staged:
                                val = patch_staged[key]
                                if isinstance(val, list):
                                    data[key] = val
                        from gideon.engine.agent import _atomic_json_write

                        _atomic_json_write(f, data)
                    state = request.app["state"]
                    state.push_refresh("agents")
                    _sel().log_api_access(
                        caller=request.get("user", "dashboard"),
                        operation="agent.detail_update",
                        outcome="success",
                        source="dashboard",
                        resources=f"{name}:{','.join(sorted(patch_body))}",
                    )
                    return web.json_response({"ok": True})
                return web.json_response(data)
        except (json.JSONDecodeError, OSError):
            continue
    cfg = AppConfig.load()
    prof = (cfg.agents or {}).get(name)
    if prof is not None:
        if request.method == "GET":
            from gideon.engine.agents.defaults import is_reserved_agent

            return web.json_response(
                {
                    "name": name,
                    **dataclasses.asdict(prof),
                    "reserved": is_reserved_agent(name),
                    "editable": not is_reserved_agent(name),
                }
            )
        return web.json_response(
            {"error": "edit config-defined agents via /api/agents/{name}"}, status=400
        )

    if name == "default":
        if request.method != "GET":
            return web.json_response(
                {"error": "cannot modify built-in default agent"}, status=400
            )
        return web.json_response(
            {"name": "default", "model": getattr(cfg.agent, "model", "") or ""}
        )
    return web.json_response({"error": "not found"}, status=404)


def _resolve_agent_name(name: str, cfg) -> str | None:
    """Case-insensitively resolve an agent name against the loaded config."""
    name_lower = name.lower()
    for existing in cfg.agents:
        if existing.lower() == name_lower:
            return existing
    return None


_AGENT_TEXT_MAX_LEN = 4_000_000
_AGENT_LIST_MAX_ITEMS = 1_000_000

_AGENT_FIELD_SPECS: dict[str, dict] = {
    "provider": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "provider_agent": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "acp_mode": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "default_dir": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "memory_store": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "description": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "system_prompt": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "voice": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "natural_voice": {"type": "bool"},
    "model": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "approval_mode": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "skills": {"type": "str_list", "max_items": _AGENT_LIST_MAX_ITEMS},
    "tools": {"type": "str_list", "max_items": _AGENT_LIST_MAX_ITEMS},
    "triggers": {"type": "str_list", "max_items": _AGENT_LIST_MAX_ITEMS},
    "source": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "specialty": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
    "route_hints": {"type": "str", "max_len": _AGENT_TEXT_MAX_LEN},
}

_AGENT_DETAIL_PATCH_KEYS = (
    "model",
    "description",
    "system_prompt",
    "approval_mode",
    "skills",
    "tools",
    "triggers",
)

_MAX_AGENT_BODY_DEPTH = 12


def _agent_value_too_deep(value: Any) -> bool:
    """True when *value* nests deeper than :data:`_MAX_AGENT_BODY_DEPTH`.

    ITERATIVE, so its safety does not depend on the cap's VALUE. A recursive walker happens
    to be safe at 12 (it returns at the cap, so it can only be ~12 frames deep), which is
    exactly what makes the recursive form a trap: raise the cap and the guard becomes the
    thing that raises ``RecursionError``. An explicit stack cannot acquire that coupling.
    """
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_AGENT_BODY_DEPTH:
            return True
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return False


def _staged_agent_fields(
    body: dict, keys: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Return the agent-profile fields present in *body*, validated and coerced.

    Only keys actually present are returned, so a caller can tell "set this to empty" from
    "leave it alone" — which is what makes one helper serve both the create path (absent →
    dataclass default) and the two update paths (absent → unchanged).

    Raises :class:`~gideon.core.config.edit_spec.ConfigValueError` whose message NAMES the
    field; the caller renders it as a 4xx. Nothing is written before it raises, so a refused
    body cannot leave a half-applied profile behind.
    """
    staged: dict[str, Any] = {}
    for key in keys if keys is not None else tuple(_AGENT_FIELD_SPECS):
        if key not in body:
            continue
        value = body[key]
        if _agent_value_too_deep(value):
            raise ConfigValueError(
                f"{key} is nested more than {_MAX_AGENT_BODY_DEPTH} levels deep",
                f"{key}=<nested>",
            )
        try:
            staged[key] = coerce_edit_value(key, value, _AGENT_FIELD_SPECS[key])
        except ConfigValueError as exc:
            raise ConfigValueError(f"{key} {exc}", exc.resources, exc.status) from None
    return staged


def _agent_write_refusal(exc: ConfigValueError) -> web.Response:
    """Render a staged-field rejection as a structured 4xx naming the field.

    ``exc.status`` is 400 for every rule ``_AGENT_FIELD_SPECS`` uses;
    ``test_no_spec_uses_a_type_coerce_edit_value_does_not_support`` is what keeps
    ``coerce_edit_value``'s 500 branch (an unrecognised spec ``type``) unreachable from here.
    """
    return json_error("invalid_request", message=str(exc), status=exc.status)


def _unavailable_agent_name(name: str) -> str | None:
    """Why *name* may not be CREATED, or None when it is free.

    Two name classes, one answer, because both produced a lying ``{"ok": true}``:

    * **Retired** (``RETIRED_AGENT_NAMES``) — the config migration PRUNES these on the very
      next load, so ``POST {"name": "gideon-autonomous"}`` answered 200 with the name
      echoed back and the agent then did not exist (404 on its detail route). That is the
      exact failure ``api_default_agent`` guards against a few hundred lines up, with the
      same reasoning written out there.
    * **Reserved** (``is_reserved_agent``) — protected only by the seeding migration having
      ALREADY run. On a config with no ``agents`` map yet (a first-run install), create
      answered 200 for ``gideon-lite``; seeding is add-if-MISSING, so it then never
      overwrote the caller's profile, and the background chore worker ran with the caller's
      ``system_prompt`` and model. PUT and DELETE both answer 403 for a reserved name, so
      the impostor was also un-editable and un-deletable. The ABSENCE of the seeded profile
      was the protection, not a guard — the same shape as this file's own PATCH-delete
      note (#349 C).

    Answering 403 matches the two siblings that already refuse a reserved agent
    (``PUT``/``DELETE /api/agents/{name}``): the request is well-formed and refused, not
    malformed.
    """
    from gideon.engine.agents.defaults import RETIRED_AGENT_NAMES, is_reserved_agent

    if is_reserved_agent(name):
        return f"'{name}' is a built-in system agent name and cannot be created"
    if name.lower() in {n.lower() for n in RETIRED_AGENT_NAMES}:
        return (
            f"'{name}' is a retired system agent name — the next config load prunes it, "
            "so creating it would report success and leave nothing behind"
        )
    return None


async def api_gideon_agents(request: web.Request) -> web.Response:
    """GET /api/agents — list all Gideon agent definitions."""
    from gideon.engine.agents.defaults import is_reserved_agent

    cfg = AppConfig.load()
    state: ConsoleState | None = request.app.get("state")
    live: dict[str, dict[str, int]] = {}
    if state is not None:
        for session in state._sessions.values():
            if session.lifecycle != "active":
                continue
            name = session.agent or cfg.default_agent
            if not name:
                continue
            counts = live.setdefault(name, {"active_sessions": 0, "running_sessions": 0})
            counts["active_sessions"] += 1
            counts["running_sessions"] += int(session.running)
    agents = [
        {
            "name": name,
            **dataclasses.asdict(agent_cfg),
            **live.get(name, {"active_sessions": 0, "running_sessions": 0}),
            "reserved": is_reserved_agent(name),
            "editable": not is_reserved_agent(name),
        }
        for name, agent_cfg in cfg.agents.items()
    ]
    return web.json_response(
        {
            "agents": agents,
            "default_agent": cfg.default_agent,
        }
    )


_config_lock: asyncio.Lock | None = None
_config_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_config_lock() -> asyncio.Lock:
    """Return a config lock bound to the current event loop (Python 3.10 compat)."""
    global _config_lock, _config_lock_loop
    loop = asyncio.get_running_loop()
    if _config_lock is None or _config_lock_loop is not loop:
        _config_lock = asyncio.Lock()
        _config_lock_loop = loop
    return _config_lock


async def api_gideon_agents_sync(request: web.Request) -> web.Response:
    """POST /api/agents/sync — auto-sync marketplace-installed agents into config.json."""
    async with _get_config_lock():
        return await _do_agents_sync(request)


async def _do_agents_sync(request: web.Request) -> web.Response:
    from gideon.engine.agents.marketplace import get_default_agent_registry
    from gideon.engine.agents.defaults import is_reserved_agent

    cfg = AppConfig.load()
    synced: list[str] = []
    updated: list[str] = []
    seen: set[str] = set()
    for file_name, body in get_default_agent_registry().documents():
        name = body.get("name", file_name)
        if not isinstance(name, str) or not re.fullmatch(
            r"^[a-z0-9][a-z0-9-]{0,62}$", name
        ):
            logger.warning("Skipping malformed agent %s: invalid name", file_name)
            continue
        seen.add(name)
        existing_name = _resolve_agent_name(name, cfg)
        if existing_name and cfg.agents[existing_name].source not in ("local", "marketplace"):
            continue
        try:
            staged = _staged_agent_fields(body)
        except ConfigValueError as exc:
            logger.warning("Skipping malformed agent %s: %s", name, exc)
            continue
        staged["source"] = "marketplace"
        profile = AgentProfile(**staged)
        if existing_name:
            if dataclasses.asdict(cfg.agents[existing_name]) != dataclasses.asdict(profile):
                cfg.agents[existing_name] = profile
                updated.append(existing_name)
        elif not is_reserved_agent(name):
            cfg.agents[name] = profile
            synced.append(name)
    state: ConsoleState | None = request.app.get("state")
    active_names = (
        {
            session.agent or cfg.default_agent
            for session in state._sessions.values()
            if session.lifecycle == "active" and (session.agent or cfg.default_agent)
        }
        if state is not None else set()
    )
    removed = [
        name for name, profile in cfg.agents.items()
        if profile.source in ("local", "marketplace")
        and name not in seen and name not in active_names and not is_reserved_agent(name)
    ]
    for name in removed:
        del cfg.agents[name]
    if cfg.default_agent in removed:
        from gideon.engine.agents.defaults import (
            DEFAULT_NATIVE_AGENT_NAME,
            make_default_native_profile,
        )

        cfg.default_agent = DEFAULT_NATIVE_AGENT_NAME
        cfg.agents.setdefault(
            DEFAULT_NATIVE_AGENT_NAME, make_default_native_profile(AgentProfile)
        )
    if synced or updated or removed:
        cfg.save()
        if state is not None:
            state.push_refresh("agents")
    return web.json_response(
        {"ok": True, "synced": synced, "updated": updated, "removed": removed}
    )


async def api_gideon_agents_create(request: web.Request) -> web.Response:
    """POST /api/agents — create a new Gideon agent."""

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name", "")
    if not isinstance(name, str):
        return web.json_response({"error": "name must be a string"}, status=400)
    name = name.strip()
    if not name:
        return web.json_response({"error": "Agent name is required"}, status=400)
    name = name.lower()

    import re as _re

    if not _re.fullmatch(r"^[a-z0-9][a-z0-9-]{0,62}$", name):
        return web.json_response(
            {
                "error": "Agent name must match ^[a-z0-9][a-z0-9-]{0,62}$ (lowercase letters, digits, dashes, no leading dash)"  # noqa: E501
            },
            status=400,
        )
    try:
        staged = _staged_agent_fields(body)
    except ConfigValueError as exc:
        return _agent_write_refusal(exc)

    async with _get_config_lock():
        cfg = AppConfig.load()
        if _resolve_agent_name(name, cfg):
            return web.json_response(
                {"error": f"Agent '{name}' already exists"}, status=409
            )
        unavailable = _unavailable_agent_name(name)
        if unavailable:
            return json_error("forbidden", message=unavailable, status=403)
        cfg.agents[name] = AgentProfile(**staged)
        cfg.save()
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="agent.create",
        outcome="success",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True, "name": name})


async def api_gideon_agent_update(request: web.Request) -> web.Response:
    """PUT /api/agents/{name} — update a Gideon agent."""

    name = request.match_info["name"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    from gideon.engine.agents.defaults import is_reserved_agent

    if is_reserved_agent(name):
        editable_keys = {"model"}
        if not set(body.keys()).issubset(editable_keys):
            return web.json_response(
                {
                    "error": f"'{name}' is a built-in system agent; only its model can be changed"
                },
                status=403,
            )
    try:
        staged = _staged_agent_fields(body)
    except ConfigValueError as exc:
        return _agent_write_refusal(exc)

    async with _get_config_lock():
        cfg = AppConfig.load()
        if name not in cfg.agents:
            return web.json_response({"error": f"Agent '{name}' not found"}, status=404)
        agent = cfg.agents[name]
        changed: list[str] = []
        for field_name, value in staged.items():
            setattr(agent, field_name, value)
            changed.append(field_name)
        cfg.save()
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="agent.update",
        outcome="success",
        source="dashboard",
        resources=f"{name} ({','.join(changed)})",
    )
    return web.json_response({"ok": True, "name": name})


async def api_gideon_agent_delete(request: web.Request) -> web.Response:
    """DELETE /api/agents/{name} — delete a Gideon agent."""

    name = request.match_info["name"]
    from gideon.engine.agents.defaults import is_reserved_agent

    if is_reserved_agent(name):
        return web.json_response(
            {"error": f"'{name}' is a built-in system agent and cannot be deleted"},
            status=403,
        )
    async with _get_config_lock():
        cfg = AppConfig.load()
        if name not in cfg.agents:
            return web.json_response({"error": f"Agent '{name}' not found"}, status=404)
        if name == cfg.default_agent:
            return web.json_response(
                {
                    "error": f"Cannot delete default agent '{name}'. Change default_agent first."
                },
                status=409,
            )
        del cfg.agents[name]
        cfg.save()
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="agent.delete",
        outcome="success",
        source="dashboard",
        resources=name,
    )
    return web.json_response({"ok": True})


def _regen_orchestrator() -> None:
    """Regenerate orchestrator skill after metadata or agent roster changes."""
    try:
        cfg = AppConfig.load()
        if not cfg.agent.orchestrator_skill:
            return
        from gideon.cognition.orchestrator_skill import (  # noqa: F811
            generate_orchestrator_skill,
        )
        from gideon.extensions.skills import ProcedureLibrary  # noqa: F811

        generate_orchestrator_skill(ProcedureLibrary())
    except Exception:
        logger.exception("Failed to regenerate orchestrator skill")


async def api_agent_metadata_get(request: web.Request) -> web.Response:
    """GET /api/agent-metadata/{name} — read agent routing metadata."""
    name = request.match_info["name"]
    from gideon.engine.agent_metadata import load  # noqa: F811

    content = load(name)
    return web.json_response({"name": name, "content": content})


async def api_agent_metadata_put(request: web.Request) -> web.Response:
    """PUT /api/agent-metadata/{name} — write agent routing metadata."""
    caller = request.get("user", "")
    if not caller:
        try:
            _sel().log_api_access(
                caller="anonymous",
                operation="agent_metadata.put",
                outcome="denied",
                source="dashboard",
                resources="unauthenticated",
            )
        except Exception:
            logger.warning("SEL logging failed", exc_info=True)
        return web.json_response({"error": "authentication required"}, status=401)
    name = request.match_info["name"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    content = body.get("content", "").strip()
    from gideon.engine.agent_metadata import save  # noqa: F811
    from gideon.engine.agent_metadata import delete

    if not content:
        delete(name)
    else:
        save(name, content)
    _regen_orchestrator()
    try:
        _sel().log_api_access(
            caller=caller,
            operation="agent_metadata.put",
            outcome="ok",
            resources=f"{name} (cleared)" if not content else name,
        )
    except Exception:
        logger.warning("SEL logging failed", exc_info=True)
    return web.json_response({"ok": True, "name": name})


async def api_agent_metadata_delete(request: web.Request) -> web.Response:
    """DELETE /api/agent-metadata/{name} — delete agent routing metadata."""
    caller = request.get("user", "")
    if not caller:
        try:
            _sel().log_api_access(
                caller="anonymous",
                operation="agent_metadata.delete",
                outcome="denied",
                source="dashboard",
                resources="unauthenticated",
            )
        except Exception:
            logger.warning("SEL logging failed", exc_info=True)
        return web.json_response({"error": "authentication required"}, status=401)
    name = request.match_info["name"]
    from gideon.engine.agent_metadata import delete  # noqa: F811

    delete(name)
    _regen_orchestrator()
    try:
        _sel().log_api_access(
            caller=caller,
            operation="agent_metadata.delete",
            outcome="ok",
            resources=name,
        )
    except Exception:
        logger.warning("SEL logging failed", exc_info=True)
    return web.json_response({"ok": True, "name": name})
