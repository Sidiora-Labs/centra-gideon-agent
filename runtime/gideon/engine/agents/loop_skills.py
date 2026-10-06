"""Skills selected by a real native loop for its bound worker session."""
from __future__ import annotations

_BINDINGS: dict[str, tuple[str, str]] = {}


def bind(session_key: str, loop_id: str, agent: str) -> None:
    # Called only by the host loop manager after creating its actual worker.
    for key in (session_key, f"dashboard:{session_key}"):
        _BINDINGS[key] = (loop_id, agent)


def names(session_key: str, agent: str) -> tuple[str, ...]:
    binding = _BINDINGS.get(session_key)
    if binding is None or binding[1] != agent:
        return ()
    from gideon.extensions.apps.app_work import active_for, held
    if held() is not None or active_for(session_key) is not None:
        return ()
    from gideon.automation.loop import kinds, store
    from gideon.automation.loop.loop import ENDED_STATUSES
    try:
        loop = store.get(binding[0])
        if loop is None or loop.status in ENDED_STATUSES:
            return ()
        kinds.ensure_loaded()
        strategy = kinds.get_or_none(loop.kind)
        if strategy is None or (loop.agent or strategy.default_agent) != agent:
            return ()
        selected, _ = strategy.turn_capabilities(loop)
        return tuple(dict.fromkeys(n for n in selected if isinstance(n, str) and n.strip() == n and n and not any(c in n for c in '*?[]')))
    except Exception:
        return ()
