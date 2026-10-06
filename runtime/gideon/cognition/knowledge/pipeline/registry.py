"""Node registry + use-case model resolution for the ingestion engine (#30).

Nodes register under ``(node_type, backend)`` (mirrors OpenForge's
``register_backend``). A model-backed node resolves its provider through a
Settings>Models **use-case** at run-time — whatever model the user selected for
that use-case is used; if none is active the node is skipped gracefully (never a
hard item failure).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.cognition.knowledge.pipeline.types import ProcessingNode

logger = logging.getLogger(__name__)

NODE_REGISTRY: dict[tuple[str, str], "ProcessingNode"] = {}
_WORKER_NODE_REGISTRIES: dict[str, dict[tuple[str, str], "ProcessingNode"]] = {}


def _node_registry() -> dict[tuple[str, str], "ProcessingNode"]:
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None:
        return NODE_REGISTRY
    return _WORKER_NODE_REGISTRIES.setdefault(worker, {})


def register_node(node: "ProcessingNode") -> None:
    """Register a node implementation under ``(node_type, backend)``."""
    _node_registry()[(node.node_type, node.backend)] = node


def get_node(node_type: str, backend: str) -> "ProcessingNode | None":
    return _node_registry().get((node_type, backend))


def can_resolve_use_case(use_case: str | None) -> bool:
    """True if a model is active for *use_case* (so a model-backed node can run).

    None use-case (pure-python node) → always True. Resolution failure → False, so
    the executor skips the node and marks the item partial rather than hard-failing.
    """
    if not use_case:
        return True
    try:
        from gideon.extensions.providers.provider_bridge import (
            can_resolve_use_case as _can,
        )

        return bool(_can(use_case))
    except Exception:
        logger.debug(
            "use-case resolvability check failed for %s", use_case, exc_info=True
        )
        return False


def unserved_reason_sync(use_case: str | None) -> str:
    """The actual provider readiness decision, expressed for a recorded outcome."""
    if not use_case:
        return ""
    from gideon.cognition.knowledge.pipeline.outcomes import use_case_name

    name = use_case_name(use_case)
    try:
        from gideon.extensions.providers.provider_bridge import use_case_problem
        from gideon.extensions.providers.use_cases import active_model_refs

        if can_resolve_use_case(use_case):
            return ""
        problem = use_case_problem(use_case)
        if problem is not None and problem[1]:
            return str(problem[1])
        if active_model_refs(use_case):
            return f"The {name} model chosen in Settings → Models cannot run right now."
        return f"No {name} model is set up."
    except Exception:
        logger.debug("use-case readiness could not be read for %s", use_case, exc_info=True)
        return f"The {name} model could not be checked."


def node_available(node: "ProcessingNode") -> bool:
    """Respect a concrete node's live optional dependency probe, if declared."""
    available = getattr(node, "available", None)
    if available is None:
        return True
    try:
        return bool(available() if callable(available) else available)
    except Exception:
        logger.debug("node dependency check failed for %s", node.node_type, exc_info=True)
        return False


def unavailable_outcome(node: "ProcessingNode"):
    from gideon.cognition.knowledge.pipeline import outcomes

    declared = getattr(node, "unavailable_outcome", None)
    if callable(declared):
        try:
            result = declared()
            if isinstance(result, outcomes.PhaseOutcome):
                return result
        except Exception:
            logger.debug("node unavailable outcome failed", exc_info=True)
    return outcomes.skipped(
        f"What {outcomes.step_name(node.node_type)} runs on is not available in this install."
    )
