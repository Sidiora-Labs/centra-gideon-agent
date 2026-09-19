"""Provider Bridge — resolves a use case to a live ModelProvider instance.

The model is:

1. Read the active selection for the use case from ``active_models.json``
   (Settings → Models) — a ``"<provider_name>:<model_id>"`` ref.
2. Resolve that provider from the config.json ``providers[]`` registry
   (``default_registry``), pinning to the selected model.
3. Fall back to the first configured provider declaring the capability when no
   model is selected.

The bridge exports a single function ``create_provider_factory()`` that returns
a callable matching the factory signature::

    factory(session_key=None, agent=None, model_override=None, ...) -> ModelProvider
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from gideon.core.errors import AgentError
from gideon.integrations.llm.base import ModelProvider

logger = logging.getLogger(__name__)

ProviderFactory = Callable[..., ModelProvider]

_CAPABILITY_TO_ENUM = {
    "image_modality": "vision",
    "video_modality": "vision",
}


def _log_chain_skip(use_case: str, ref: str, reason: str) -> None:
    """SEL-record one fallback-chain entry skip (MODEL-USE-CASES-V2) — the audit
    trail for "why did my default model not serve this call". Best-effort."""
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller="system",
            operation="model.chain_skip",
            outcome="success",
            source="provider_bridge",
            resources=f"{use_case}:{ref}:{reason}",
        )
    except Exception:  # noqa: BLE001 — audit must never break resolution
        logger.debug("chain-skip SEL record failed", exc_info=True)


def _capability_enum(capability: str):
    """Map a Settings→Models capability string to the provider Capability enum, or None
    if it isn't a provider-type capability (caller then can't match by capability)."""
    from gideon.integrations.llm.capabilities import Capability

    try:
        return Capability(_CAPABILITY_TO_ENUM.get(capability, capability))
    except ValueError:
        return None


class ProviderResolutionError(Exception):
    """Raised when a provider cannot be resolved from extension instances.

    PLATFORM-LEGIBILITY §2: may carry an optional ``agent_error`` WHAT/WHY/FIX
    envelope. When present, its ``render()`` string IS this exception's message,
    so every place that already surfaces ``str(exc)`` into a turn (a background
    turn that dies on a stale pin, the mid-turn factory) shows the coded,
    actionable failure — no parallel structure, and no dead field.
    """

    def __init__(self, message: str, agent_error: "AgentError | None" = None):
        self.agent_error = agent_error
        super().__init__(agent_error.render() if agent_error is not None else message)


def _agent_provider_kind(agent: str | None) -> str:
    """Return the agent-runtime kind for ``agent``: ``"native"`` or ``"acp"``.

    Precedence:
      1. the agent profile's own ``provider`` field;
      2. the global ``cfg.agent.provider``;
      3. ``"native"`` (the in-process loop is the default runtime).
    A value like ``"acp:claude-code"`` (or bare ``"acp"``) is treated as ACP;
    everything else — including empty/unset — resolves to ``native``. ACP must be
    opted into explicitly (a per-agent ``provider`` or the global default set to
    ``acp``); an agent with no runtime declared is NEVER silently routed to an
    external CLI.
    """
    try:
        from gideon.core.config.loader import AppConfig

        cfg = AppConfig.load()
        prof = (cfg.agents or {}).get(agent) if agent else None
        kind = (
            (getattr(prof, "provider", "") if prof else "")
            or getattr(cfg.agent, "provider", "")
            or "native"
        )
    except Exception:
        kind = "native"
    return "acp" if str(kind).startswith("acp") else "native"


def _build_acp_runtime(
    runtime_id: str,
    *,
    session_key: str | None,
    agent: str | None,
    model_override: str | None,
    cwd: str | None,
    channel_id: str | None,
    **kwargs: Any,
) -> "ModelProvider":
    """Build the ``acp:<cli>`` agent runtime the caller NAMED, per session.

    The per-session axes (agent/persona, model, cwd, channel) are threaded as kwargs
    because they are properties of the SESSION, not of the global runtime entry — which
    is exactly the contract ``acp_agent._factory`` already documents for each of them.
    A missing or non-``acp_agent`` entry raises rather than falling back to a model:
    silently answering a "run my CLI" request with a different runtime is the failure
    this function exists to remove.
    """
    from gideon.integrations.llm.acp_agent import (
        ACP_AGENT_CAPABILITY,  # register_type() too
    )
    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        entry = registry.get_entry(runtime_id)
    except Exception as exc:
        raise ProviderResolutionError(
            f"WHAT: the session is bound to agent runtime {runtime_id!r}, which is not "
            f"registered\nWHY: its agent app is not installed or failed to register "
            f"(the CLI may be missing from this machine)\nFIX: install/enable the "
            f"matching agent app in the App Store, or rebind the session's runtime"
        ) from exc
    if entry.type != ACP_AGENT_CAPABILITY.type:
        raise ProviderResolutionError(
            f"WHAT: provider entry {runtime_id!r} is type {entry.type!r}, not an agent "
            f"runtime\nWHY: only an {ACP_AGENT_CAPABILITY.type!r} entry can serve an "
            f"``acp:`` binding\nFIX: rebind the session to a registered agent runtime"
        )
    return registry.build(
        runtime_id,
        session_key=session_key,
        agent=agent or "",
        model=model_override or "",
        cwd=cwd or "",
        channel_id=channel_id or "",
        **kwargs,
    )


def _provider_entry_name(
    provider: "ModelProvider | None", *, use_case: str = "chat"
) -> str:
    """Best-effort name of the provider ENTRY a resolved ModelProvider came from.

    Used to keep ``_fallback_chat_model`` in agreement with the inner provider the
    native runtime already resolved. Providers don't reliably carry their entry
    name, so derive it from the FIRST resolvable ref of the GOVERNING axis's chain
    (``use_case`` — the sub-category the inner resolve used, falling back to chat
    when unbound via ``active_model_refs``) — deterministically the same ref the
    inner resolver (``resolve_provider_for_use_case`` → the chain walk) picks
    first, since both walk the refs in order and take the first whose provider is
    configured. Returns "" when indeterminate (then ``_fallback_chat_model`` uses
    its own ordered fallback)."""
    del provider
    try:
        from gideon.extensions.providers.use_cases import active_model_refs, split_ref

        for ref in active_model_refs(use_case):
            parsed = split_ref(ref)
            if not parsed:
                continue
            ref_provider, _model_id = parsed
            if ref_provider and _provider_is_configured(ref_provider):
                return ref_provider
    except Exception:
        logger.debug("provider entry-name derivation failed", exc_info=True)
    return ""


def _provider_is_configured(provider_name: str) -> bool:
    """True when a provider entry of this name is present in the config registry
    (its app is installed/configured) — a cheap mirror of what the inner resolver
    requires to build from a ref."""
    try:
        from gideon.extensions.providers.use_cases import _known_provider_names

        known = _known_provider_names()
        if known:
            return provider_name in known
    except Exception:
        logger.debug("provider-configured probe failed", exc_info=True)
    return True


def _fallback_chat_model(
    provider_hint: str | None = None, *, use_case: str = "chat"
) -> str:
    """A concrete model id to use when an agent declares no model of its own.

    ``use_case`` names the governing axis (MODEL-USE-CASES-V2): the id comes from
    THAT axis's chain (``active_model_refs`` falls back to chat when the
    sub-category is unbound, preserving today's behavior until the user binds it).

    Background agents (``gideon-lite`` for suggestions + consolidation) and
    any agent whose ``model`` is empty would otherwise pass ``model=""`` down to
    the OpenAI-compatible client, which rejects it ("length of model should be
    between 1 and 512").

    CRITICAL — provider/model agreement: for a native agent this id becomes
    ``AgentRuntimeDefinition.model`` and is passed to the *already-resolved* inner
    ModelProvider's ``complete(model=…)``, OVERRIDING that provider's own pinned
    id. So the returned model MUST belong to the SAME provider the inner resolver
    picked, or the model of one provider gets sent to another (e.g. Alibaba's
    ``glm-5.2`` handed to the Bedrock client → "The provided model identifier is
    invalid", which failed every background suggestions turn). ``provider_hint``
    is the resolved inner provider's entry name — when given, pick the active chat
    ref for THAT provider so they agree.

    Resolve, in order:
    1. When ``provider_hint`` is set: the first active chat ref whose provider
       matches the hint (keeps model + provider consistent).
    2. The configured default agent's model — ONLY when its provider matches the
       hint (or no hint) — else it could name a different provider.
    3. The first active chat model (Settings → Models) — mirrors the inner
       resolver's own "first resolvable ref" order.
    4. ``""`` (caller falls back to the provider's own configured model).
    """
    from gideon.extensions.providers.use_cases import active_model_refs, split_ref

    def _ref_provider_matches(ref_provider: str) -> bool:
        return not provider_hint or ref_provider == provider_hint

    if provider_hint:
        try:
            for ref in active_model_refs(use_case):
                parsed = split_ref(ref)
                if not parsed:
                    continue
                ref_provider, model_id = parsed
                if model_id and ref_provider == provider_hint:
                    return model_id
        except Exception:
            logger.debug("fallback model: provider-hint match failed", exc_info=True)

    try:
        from gideon.core.config.loader import AppConfig
        from gideon.engine.agents.defaults import default_agent_name

        cfg = AppConfig.load()
        prof = (cfg.agents or {}).get(default_agent_name(cfg))
        raw = _reconcile_agent_model(getattr(prof, "model", "") or "") if prof else ""
        if raw:
            parsed = split_ref(str(raw))
            ref_provider = parsed[0] if parsed else ""
            if not ref_provider or _ref_provider_matches(ref_provider):
                return _strip_provider_prefix(str(raw))
    except Exception:
        logger.debug("fallback model: default-agent lookup failed", exc_info=True)

    try:
        for ref in active_model_refs(use_case):
            parsed = split_ref(ref)
            if not parsed:
                model_id, ref_provider = ref, ""
            else:
                ref_provider, model_id = parsed
            if model_id and _ref_provider_matches(ref_provider):
                return model_id
    except Exception:
        logger.debug("fallback model: active-models lookup failed", exc_info=True)
    return ""


def _active_chat_model_ids() -> set[str]:
    """The model ids (without the ``provider:`` prefix) currently active for chat."""
    out: set[str] = set()
    try:
        from gideon.extensions.providers.use_cases import active_model_refs, split_ref

        for ref in active_model_refs("chat"):
            parsed = split_ref(ref)
            mid = parsed[1] if parsed else ref
            if mid:
                out.add(mid)
                out.add(ref)
    except Exception:
        logger.debug("active chat model lookup failed", exc_info=True)
    return out


def _strip_provider_prefix(model: str) -> str:
    """Strip a leading ``<provider>:`` from a model ref so the bare id reaches
    the SDK. A chat session stores its model as the active_models ref form
    (``"Bedrock:global.anthropic.claude-opus-4-8"``); handed verbatim to the
    provider it becomes an invalid model id (AWS: "model identifier is invalid").
    Colons are ambiguous — Bedrock ids contain them (``…-v1:0``) — so split on the
    FIRST colon ONLY when the prefix matches a known provider entry name.
    """
    if not model or ":" not in model:
        return model
    prefix = model.split(":", 1)[0]
    try:
        from gideon.integrations.llm.registry import get_default_registry

        registry = get_default_registry()
        if any(e.name == prefix for e in registry.list_entries()):
            return model.split(":", 1)[1]
    except Exception:
        logger.debug("provider-prefix strip check failed", exc_info=True)
    try:
        from gideon.extensions.providers.use_cases import _known_provider_names

        known = _known_provider_names()
        if known and prefix in known:
            return model.split(":", 1)[1]
    except Exception:
        logger.debug("provider-prefix strip via known-names failed", exc_info=True)
    return model


def _reconcile_agent_model(model: str) -> str:
    """Heal a stale agent model pin.

    An agent may pin an explicit model that the user later removes from the
    active set (Settings → Models). Rather than hand that dead id to the client
    (→ 400 / unresolved provider), treat it as unset so the caller falls back to
    the chat-use-case binding. Empty (inherit) and still-active pins pass through.
    """
    if not model:
        return ""
    active = _active_chat_model_ids()
    if not active or model in active:
        return model
    logger.info("Agent model %r no longer active; falling back to chat binding", model)
    return ""


def _build_native_runtime(
    *,
    use_case: str,
    session_key: str | None,
    agent: str | None,
    model_override: str | None,
    cwd: str | None,
    extra_tool_roots: list | None = None,
    unattended: bool = False,
    dry_run: bool = False,
    reasoning_effort: str = "",
    project_id: str = "",
    model_axis: str = "",
    tool_groups: list | None = None,
    **kwargs: Any,
) -> ModelProvider:
    """Construct a :class:`NativeAgentRuntime` for a ``native`` agent.

    Its inference ModelProvider is resolved through the SAME active-model
    selection (Settings → Models). ``model_axis`` names the chat sub-category
    whose CHAIN governs the inner model (MODEL-USE-CASES-V2): "background" for
    the lite factory, "loops" for loop workers, "orchestration" for model-less
    subagent spawns, else the session's own use case — so a sub-category
    binding governs native agents too (previously the inner model hardcoded
    "chat", making e.g. a code_tools binding cosmetic). Tools come from the
    in-process core provider.
    """
    from pathlib import Path

    from gideon.engine.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        NativeBuiltinToolProvider,
    )
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition

    model_override = _reconcile_agent_model(model_override or "") or None

    from gideon.extensions.providers.use_cases import CHAT_SUBCATEGORIES

    inner_axis = model_axis if model_axis in CHAT_SUBCATEGORIES else "chat"
    model_provider = resolve_provider_for_use_case(
        inner_axis,
        session_key=session_key,
        agent=agent,
        model_override=model_override,
        cwd=cwd,
        _force_model_axis=True,
        _model_axis_only=True,
        **kwargs,
    )
    name = agent or "Gideon"
    if not hasattr(model_provider, "complete"):
        raise ProviderResolutionError(
            f"Native agent {name!r} resolved its inference model to "
            f"{type(model_provider).__name__}, which is not a ModelProvider "
            f"(no complete()). Bind the 'chat' use case to a model provider "
            f"(Settings → Models), not an ACP agent runtime."
        )

    system_prompt = ""
    model = _strip_provider_prefix(_reconcile_agent_model(model_override or ""))
    tools: list[str] = []
    skills: list[str] = []
    hook_ids: list[str] = []
    try:
        from gideon.core.config.loader import AppConfig

        cfg = AppConfig.load()
        prof = (cfg.agents or {}).get(agent) if agent else None
        if prof is not None:
            from gideon.core.config.loader import _compose_voice

            system_prompt = _compose_voice(
                getattr(prof, "voice", ""), getattr(prof, "system_prompt", "") or ""
            )
            model = _strip_provider_prefix(
                _reconcile_agent_model(model_override or "")
            ) or _strip_provider_prefix(
                _reconcile_agent_model(getattr(prof, "model", "") or "")
            )
            tools = list(getattr(prof, "tools", []) or [])
            skills = list(getattr(prof, "skills", []) or [])
            hook_ids = list(getattr(prof, "triggers", []) or [])
    except Exception:
        pass

    if not model:
        model = _fallback_chat_model(
            provider_hint=_provider_entry_name(model_provider, use_case=inner_axis),
            use_case=inner_axis,
        )

    definition = AgentRuntimeDefinition(
        name=name,
        provider="native",
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        skills=skills,
        workspace_dir=cwd or "",
    )
    _cwd = Path(cwd) if cwd else None

    hook_fire = None
    if hook_ids:

        async def _hook_fire(tool_name: str, args_json: str | None) -> list[str]:
            from gideon.engine.hooks import (
                HOOK_EVENT_PRE_TOOL_USE,
                get_global_hook_store,
            )

            store = get_global_hook_store()
            if store is None:
                return []
            try:
                tool_input = json.loads(args_json) if args_json else None
            except (ValueError, TypeError):
                tool_input = None
            results = await store.fire_for_ids(
                HOOK_EVENT_PRE_TOOL_USE,
                hook_ids,
                tool_name=tool_name,
                tool_input=tool_input,
            )
            out: list[str] = []
            for r in results:
                if r.exit_code == 2:
                    out.append(
                        f"BLOCKED:{r.hook_name}:{(r.stderr or 'hook denied')[:200]}"
                    )
                elif r.exit_code == 0 and r.stdout:
                    out.append(r.stdout)
            return out

        hook_fire = _hook_fire

    from gideon.integrations.tool_providers.registry import (
        list_providers as _list_tool_providers,
    )

    platform = NativeBuiltinToolProvider(
        cwd=_cwd,
        agent=name or "",
        session_key=session_key or "",
        extra_roots=[Path(r) for r in (extra_tool_roots or [])],
        categories=PLATFORM_CATEGORIES,
        provider_name="gideon-filesystem",
        display="Filesystem & Shell Tools",
    )
    tool_providers = [platform, *_list_tool_providers()]

    return NativeAgentRuntime(  # type: ignore[return-value]  # CI-2
        definition=definition,
        model_provider=model_provider,  # type: ignore[arg-type]
        tool_providers=tool_providers,
        cwd=_cwd,
        session_key=session_key or "",
        hook_fire=hook_fire,
        unattended=unattended,
        dry_run=dry_run,
        reasoning_effort=reasoning_effort,
        project_id=project_id,
        tool_groups=list(tool_groups) if tool_groups is not None else None,
        surface=inner_axis,
    )


def resolve_provider_for_use_case(
    use_case: str,
    *,
    session_key: str | None = None,
    agent: str | None = None,
    model_override: str | None = None,
    cwd: str | None = None,
    **kwargs: Any,
) -> ModelProvider:
    """Resolve a use case to a live ModelProvider instance.

    Resolution order:
    1. The active model selected for ``use_case`` in ``active_models.json``
       (Settings → Models) — a ``"<provider_name>:<model_id>"`` ref that pins
       resolution to that configured provider + model. A chat sub-category
       (``reasoning`` / ``code_tools``) with no model of its own borrows the parent
       ``chat`` selection.
    2. Implicit fallback: any configured provider (config.json ``providers[]``)
       declaring the requested capability — picks the first. Avoids forcing the
       user to set a selection when only one sensible provider exists.
    """
    from gideon.extensions.providers.use_cases import (
        VALID_USE_CASES,
        active_model_refs,
        parent_capability,
        split_ref,
    )

    if use_case not in VALID_USE_CASES:
        raise ProviderResolutionError(f"Unknown use case: {use_case!r}")

    _force_model_axis = kwargs.pop("_force_model_axis", False)
    _provider_kind = kwargs.pop("provider_kind", "") or ""
    _extra_tool_roots = kwargs.pop("extra_tool_roots", None)
    _unattended = bool(kwargs.pop("unattended", False))
    _dry_run = bool(kwargs.pop("dry_run", False))
    _project_id = str(kwargs.pop("project_id", "") or "")
    _model_axis = str(kwargs.pop("model_axis", "") or "")
    _tool_groups = kwargs.pop("tool_groups", None)
    _reasoning_effort = str(kwargs.get("reasoning_effort_override") or "")
    _kind = (
        ("acp" if str(_provider_kind).startswith("acp") else "native")
        if _provider_kind
        else _agent_provider_kind(agent)
    )
    # §2.3 (gap 3): re-inject ``unattended`` for the ACP branch. Only the acp_agent
    # factory sees these kwargs on that branch, and it is the one place that can
    # honour the flag — it hands it to AcpClient, which is what lets sanitize_mode
    # accept ``bypassPermissions`` for a genuinely unattended run while every
    # interactive session stays clamped (AAP-5). Restricted to _kind == "acp" on
    # purpose: a native turn already took the explicit-argument path above, and the
    # MODEL-axis resolvers must never see this key.
    if _kind == "acp" and _unattended:
        kwargs["unattended"] = True
    # pinned chat model instead of its CLI. It hid because ``ConversationDirectory``'s ACP
    if _kind == "acp" and _provider_kind.startswith("acp:"):
        return _build_acp_runtime(
            _provider_kind,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            channel_id=kwargs.pop("channel_id", None),
            **kwargs,
        )
    if (
        not _force_model_axis
        and use_case in ("chat", "code_tools")
        and _kind == "native"
    ):
        kwargs.pop("reasoning_effort_override", None)
        return _build_native_runtime(
            use_case=use_case,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            extra_tool_roots=_extra_tool_roots,
            unattended=_unattended,
            dry_run=_dry_run,
            reasoning_effort=_reasoning_effort,
            project_id=_project_id,
            model_axis=_model_axis or use_case,
            tool_groups=_tool_groups,
            **kwargs,
        )

    capability = parent_capability(use_case)
    if use_case in ("reasoning", "background", "loops", "orchestration"):
        kwargs["_guard_use_case"] = use_case
    if model_override and "/" in model_override and ":" not in model_override:
        direct = _resolve_from_config_registry(
            capability,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            **kwargs,
        )
        if direct is not None:
            return direct
    if model_override and (":" in model_override or "/" in model_override):
        direct = _resolve_from_config_registry(
            capability,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            **kwargs,
        )
        if direct is not None:
            return direct

    _refs = list(active_model_refs(use_case))
    _query_class = str(kwargs.pop("routing_query_class", "") or "")
    _routed = False
    try:
        from gideon.engine.routing.policy import route_refs, routing_active

        _routed = routing_active(use_case)
        if _routed:
            _refs = route_refs(use_case, _query_class, _refs)
    except (
        Exception
    ):  # noqa: BLE001 — routing must never break resolution (fail-open, §3.1)
        logger.debug("routing seam skipped for %s", use_case, exc_info=True)
        _routed = False
    _last_dead: tuple[str, str] | None = None
    for i, ref in enumerate(_refs):
        parsed = split_ref(ref)
        if not parsed:
            continue
        provider_name, model_id = parsed
        has_later = i + 1 < len(_refs)
        try:
            from gideon.security.guardrails.breaker import get_breaker

            if get_breaker(provider_name).is_open() and has_later:
                logger.warning(
                    "chain skip: %s entry %d (%s) — provider breaker OPEN",
                    use_case,
                    i,
                    ref,
                )
                _log_chain_skip(use_case, ref, "breaker_open")
                continue
        except (
            Exception
        ):  # noqa: BLE001 — breaker introspection must never break resolution
            pass
        _rk = dict(kwargs)
        if _routed:
            _rk["_guard_routed"] = True
            if i > 0:
                _rk["_guard_routed_fallback"] = True
        pinned = _resolve_from_config_registry(
            capability,
            session_key=session_key,
            agent=agent,
            model_override=model_id,
            cwd=cwd,
            provider_hint=provider_name,
            **_rk,
        )
        if pinned is not None:
            return pinned
        _last_dead = (ref, provider_name)
        if has_later:
            logger.warning(
                "chain skip: %s entry %d (%s) — provider not buildable",
                use_case,
                i,
                ref,
            )
            _log_chain_skip(use_case, ref, "unbuildable")
            continue
    if _last_dead is not None:
        ref, provider_name = _last_dead
        raise ProviderResolutionError(
            f"The model selected for {use_case!r} ({ref!r}) isn't available — its "
            f"provider {provider_name!r} isn't installed or configured. Install it "
            f"in the App Store, or pick a different model in Settings → Models.",
            AgentError(
                code="ERR_MODEL_UNRESOLVED",
                what=(
                    f"the model pinned for use case {use_case!r} ({ref!r}) cannot be built"
                ),
                why=(
                    f"the active ref names provider {provider_name!r}, which is absent "
                    f"from config.json (its app isn't installed or configured)"
                    + (
                        " — every other chain entry was skipped too"
                        if len(_refs) > 1
                        else ""
                    )
                ),
                fix=(
                    f"install {provider_name!r} in the App Store, or rebind {use_case!r} "
                    f"to an available model in Settings → Models"
                ),
            ),
        )

    fallback = _resolve_from_config_registry(
        capability,
        session_key=session_key,
        agent=agent,
        model_override=model_override,
        cwd=cwd,
        **kwargs,
    )
    if fallback is not None:
        return fallback

    raise ProviderResolutionError(
        f"No provider configured for use case {use_case!r}. "
        f"Add a model provider in Settings → Providers.",
        AgentError(
            code="ERR_MODEL_UNRESOLVED",
            what=f"no model provider resolves for use case {use_case!r}",
            why="no provider in config.json declares the capability this use case needs",
            fix=f"add a model provider in Settings → Providers, then bind {use_case!r} to it",
        ),
    )


def can_resolve_use_case(use_case: str) -> bool:
    """Cheaply report whether a ModelProvider for ``use_case`` is resolvable
    *right now*, without building one.

    This is the single source of truth behind both the onboarding ``needs_model``
    signal and the background-session spawn guard — so the dashboard's "add a
    model" nudge and what the bridge can actually resolve never disagree (the
    coarse capability-only probe they used before could diverge from real
    resolution; see F1).

    Resolution for chat-class use cases succeeds when EITHER an active model is
    selected for the use case (Settings → Models) OR a configured provider
    (config.json ``providers[]`` → ``default_registry``) declares the matching
    capability. The native default agent inferences through a ModelProvider too,
    so "no model" ⇒ chat cannot run regardless of the agent-runtime kind. We
    deliberately do NOT instantiate a provider here (no subprocess/socket side
    effects) — this runs on a hot GET.
    """
    try:
        from gideon.extensions.providers.use_cases import (
            VALID_USE_CASES,
            active_model_refs,
            parent_capability,
        )
    except Exception:
        return False
    if use_case not in VALID_USE_CASES:
        return False

    try:
        if active_model_refs(use_case):
            return True
    except Exception:
        logger.debug("can_resolve: active-model probe failed", exc_info=True)

    capability = parent_capability(use_case)

    try:
        import gideon.integrations.llm.acp_agent  # noqa: F401
        from gideon.integrations.llm.registry import get_default_registry

        target_cap = _capability_enum(capability)
        if target_cap is None:
            return False

        registry = get_default_registry()
        for entry in registry.list_entries():
            if entry.type == "acp_agent":
                continue
            caps = entry.declared_capabilities
            if not caps:
                try:
                    caps = registry.capability_of(entry.type).capabilities
                except Exception:
                    caps = frozenset()
            if target_cap in caps:
                return True
    except Exception:
        logger.debug("can_resolve: registry probe failed", exc_info=True)
    return False


def _resolve_from_config_registry(
    use_case: str,
    *,
    session_key: str | None = None,
    agent: str | None = None,
    model_override: str | None = None,
    cwd: str | None = None,
    provider_hint: str | None = None,
    **kwargs: Any,
) -> ModelProvider | None:
    """Fallback: resolve via the ProviderEntry registry.

    Walks ``config.json``'s ``providers[]`` entries, picks the first whose
    declared capabilities cover ``use_case``, and builds a ModelProvider via the
    registry's registered type factory (``registry.build`` → the provider module's
    or app's ``register_type`` factory). Returns ``None`` when no compatible provider
    is configured.
    """
    try:
        import gideon.integrations.llm.acp_agent  # noqa: F401
        from gideon.integrations.llm.registry import get_default_registry
    except Exception:
        return None

    target_cap = _capability_enum(use_case)
    if target_cap is None:
        return None

    model_axis_only = bool(kwargs.pop("_model_axis_only", False))
    guard_use_case = str(kwargs.pop("_guard_use_case", "") or "")
    guard_routed = bool(kwargs.pop("_guard_routed", False))
    guard_routed_fallback = bool(kwargs.pop("_guard_routed_fallback", False))

    registry = get_default_registry()
    entries = list(registry.list_entries())
    if not entries:
        return None

    if (
        model_override
        and ":" in model_override
        and any(e.name == model_override.split(":", 1)[0] for e in entries)
    ):
        _hint, model_override = model_override.split(":", 1)
        provider_hint = provider_hint or _hint
    elif model_override and "/" in model_override:
        _hint, model_override = model_override.split("/", 1)
        provider_hint = provider_hint or _hint
    elif model_override and ":" in model_override:
        _maybe_provider = model_override.split(":", 1)[0]
        if any(e.name == _maybe_provider for e in entries):
            _hint, model_override = model_override.split(":", 1)
            provider_hint = provider_hint or _hint

    candidate = None
    for entry in entries:
        if model_axis_only and entry.type == "acp_agent":
            continue
        caps = entry.declared_capabilities
        if not caps:
            try:
                caps = registry.capability_of(entry.type).capabilities
            except Exception:
                caps = frozenset()
        if target_cap not in caps:
            continue
        if provider_hint and entry.name != provider_hint:
            continue
        candidate = entry
        break

    if candidate is None:
        return None

    config: dict[str, Any] = {
        "model": candidate.model,
        **(candidate.options or {}),
    }
    if model_override:
        config["model"] = model_override
    if cwd:
        config["cwd"] = cwd
    if session_key:
        config["session_key"] = session_key
    if agent:
        config["agent"] = agent
    for k, v in kwargs.items():
        config.setdefault(k, v)

    build_kwargs = dict(kwargs)
    if model_override:
        build_kwargs["model"] = model_override
    if "credential_store" not in build_kwargs and candidate.credential:
        try:
            from gideon.core.config import config_dir
            from gideon.integrations.llm.credentials import CredentialStore

            build_kwargs["credential_store"] = CredentialStore(config_dir())
        except Exception:
            pass
    if "credential_store" not in build_kwargs and not candidate.credential:
        inline_key = (candidate.options or {}).get("api_key")
        if inline_key and isinstance(inline_key, str):
            from gideon.integrations.llm.credentials import Credential

            _synth = Credential(
                name=candidate.name, kind="api_key", secret=inline_key, source="file"
            )
            build_kwargs["_inline_credential"] = _synth
    try:
        built = registry.build(
            candidate.name,
            session_key=session_key,
            cwd=cwd,
            agent=agent,
            **build_kwargs,
        )
    except Exception:
        logger.exception(
            "Config-registry fallback failed to build provider %r for %s",
            candidate.name,
            use_case,
        )
        return None

    if guard_use_case:
        from gideon.security.guardrails import wrap_model_call_guard
        from gideon.security.guardrails.breaker import get_breaker
        from gideon.security.guardrails.budgets import (
            budget_from_config,
            run_budget_from_config,
        )

        scan_mode = "warn"
        breaker = None
        budget = None
        run_budget = None
        try:
            from gideon.core.config.loader import AppConfig

            gr = AppConfig.load().guardrails
            scan_mode = gr.scan_mode
            breaker = get_breaker(
                candidate.name,
                threshold=gr.breaker.failure_threshold,
                recovery_secs=gr.breaker.recovery_secs,
            )
            budget = budget_from_config()
            run_budget = run_budget_from_config()
        except Exception:
            logger.debug(
                "guardrails config read failed; using safe defaults", exc_info=True
            )

        _timeout_kw: dict[str, Any] = {}
        if guard_routed:
            try:
                from gideon.engine.routing.policy import (
                    is_local_ref,
                    local_timeout_secs,
                )

                if is_local_ref(candidate.name):
                    _secs = local_timeout_secs()
                    if _secs > 0:
                        _timeout_kw["timeout_secs"] = _secs
            except Exception:  # noqa: BLE001 — fail-open to the guard's own default
                logger.debug("routing local timeout read failed", exc_info=True)
        return wrap_model_call_guard(
            built,
            use_case=guard_use_case,
            provider_name=candidate.name,
            model=str(config.get("model") or candidate.model or ""),
            budget=budget,
            run_budget=run_budget,
            scan_mode=scan_mode,
            breaker=breaker,
            routed=guard_routed,
            routed_fallback=guard_routed_fallback,
            **_timeout_kw,
        )
    return built


def create_provider_factory(default_use_case: str = "chat") -> ProviderFactory:
    """Return a factory function matching the ConversationDirectory contract.

    The returned factory signature is:
        factory(session_key=None, agent=None, model_override=None,
                cwd=None, channel_id=None, **kwargs) -> ModelProvider
    """

    def _factory(
        session_key: str | None = None,
        agent: str | None = None,
        model_override: str | None = None,
        cwd: str | None = None,
        channel_id: str | None = None,
        **kwargs: Any,
    ) -> ModelProvider:
        return resolve_provider_for_use_case(
            default_use_case,
            session_key=session_key,
            agent=agent,
            model_override=model_override,
            cwd=cwd,
            channel_id=channel_id,
            **kwargs,
        )

    return _factory
