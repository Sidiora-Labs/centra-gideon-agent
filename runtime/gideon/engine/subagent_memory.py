"""Per-run durable memory receipt for completed delegated work."""

from __future__ import annotations

import json
from pathlib import Path

from gideon.core.atomic_write import atomic_write


def receipt_path(agent_id: str) -> Path:
    from gideon.engine.subagent import _agent_dir

    return _agent_dir(agent_id) / "memory_receipt.json"


def read_receipt(agent_id: str) -> dict:
    try:
        return json.loads(receipt_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "unavailable", "count": 0}


def _publish(agent_id: str, receipt: dict) -> dict:
    path = receipt_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return receipt


def capture(info, memory) -> dict:
    conversation_id = f"subagent:{info.id}"
    receipt = {"status": "pending", "count": 0, "run_id": info.id,
               "conversation_id": conversation_id, "parent_session": info.parent_session_key,
               "agent": info.agent, "source": "delegated_result"}
    _publish(info.id, receipt)
    try:
        from gideon.cognition.memory_service import service_for

        if memory is None or not getattr(memory, "vector_store", None):
            receipt["status"] = "unavailable"
        else:
            result = str(info.result or "").strip()
            task = str(info.task or "").strip()
            if not result or result == "_No response._":
                receipt["status"] = "no_contribution"
            else:
                text = f"Delegated task: {task[:500]}\nOutcome: {result[:2500]}"
                stored = service_for(memory).write_episodic(
                    text, conversation_id=conversation_id, tags=["delegated", info.agent or "agent"],
                    source="subagent",
                )
                receipt["status"] = "recorded" if stored else "unavailable"
                receipt["count"] = 1 if stored else 0
    except Exception:
        receipt["status"] = "unavailable"
    return _publish(info.id, receipt)
