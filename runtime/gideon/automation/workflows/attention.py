"Epoch-scoped workflow attention cards and resolution references."

from __future__ import annotations

import logging
from typing import Any

from gideon.automation.workflows import needs_input

logger = logging.getLogger(__name__)

SOURCE = "loop"
KIND = "needs_input"


def dedup_key(run_id: str, instance_path: str, epoch: int) -> str:
    """The idempotency key for one gate's attention item.

    Epoch-scoped, not token-scoped: a rewind re-asks the same question at a NEW epoch, and
    that is a genuinely new ask deserving its own row. Two polls of the same waiting gate are
    not.
    """
    return f"workflow:{run_id}:{instance_path}:{epoch}"


def ask_title(workflow: str, node_id: str, ask: dict[str, Any] | None) -> str:
    prompt = str((ask or {}).get("prompt") or "").strip()
    if not prompt:
        return f"{workflow}: {node_id or 'a step'} needs your input"
    return prompt[:117] + "…" if len(prompt) > 120 else prompt


def ask_body(ask: dict[str, Any] | None, handoff: dict[str, Any] | None) -> str:
    kind = str((ask or {}).get("kind") or "").strip()
    paragraphs = (
        [_ASK_DESCRIPTIONS.get(kind, f"Waiting for a {kind} answer.")] if kind else []
    )
    pending = (handoff or {}).get("outstanding")
    if isinstance(pending, list) and pending:
        paragraphs.append(f"{len(pending)} other step(s) still pending.")
    return " ".join(paragraphs)


def raise_gate_item(
    state: Any,
    *,
    run_id: str,
    workflow: str,
    node_id: str,
    instance_path: str,
    epoch: int,
    resume_token: str,
    ask: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    owner: str = "",
    project_id: str = "",
    now: float = 0.0,
) -> str:
    if state is None:
        return ""
    try:
        from gideon.integrations.inbox import ItemKind, emit_attention_item

        title, body = ask_title(workflow, node_id, ask), ask_body(ask, handoff)
        card = needs_input.build_item(
            run_id=run_id,
            node_id=node_id,
            ask=ask,
            failure=failure,
            attempts=attempts,
            evidence=evidence,
            resume_token=resume_token,
            owner=owner,
            project_id=project_id,
            now=now,
        )
        references = dict(
            workflow=run_id,
            workflow_name=workflow,
            workflow_node=node_id,
            resume_token=resume_token,
        )
        references.update(needs_input.card_refs(card))
        message: dict = dict(
            source=SOURCE,
            kind=KIND,
            item_kind=ItemKind.NEEDS_INPUT.value,
            title=title,
            body=body,
            refs=references,
            dedup_key=dedup_key(run_id, instance_path, epoch),
        )
        return emit_attention_item(state, **message)
    except Exception:
        logger.debug(
            "workflow %s: could not raise the gate attention item",
            run_id,
            exc_info=True,
        )
        return ""


def resolve_gate_item(state: Any, run_id: str, node_id: str = "") -> int:
    from gideon.integrations.inbox import resolve_attention_items

    reference_pairs = [("workflow", run_id)]
    if node_id:
        reference_pairs.append(("workflow_node", node_id))
    return resolve_attention_items(state, dict(reference_pairs))


def resolve_run_items(state: Any, run_id: str) -> int:
    """Close every open attention row for a run. Returns how many were closed.

    A run that failed, was cancelled or completed answers its own outstanding questions by
    ending: nothing about it is actionable any more. Without this, cancelling a run mid-gate
    would leave a permanently unanswerable row in the inbox — the exact dead-row problem this
    module exists to avoid, arrived by a different path.
    """
    return resolve_gate_item(state, run_id)


_ASK_DESCRIPTIONS = {
    "approval": "Waiting for your approval.",
    "choice": "Waiting for you to choose an option.",
    "text": "Waiting for a written answer.",
    "form": "Waiting for you to fill in a form.",
}
