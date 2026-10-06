"""Persist and settle owner-answerable trigger parks."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
SOURCE = "loop"


def _path(trigger_id: str) -> Path:
    from gideon.core.config.loader import config_dir

    identity = hashlib.sha256(trigger_id.encode("utf-8")).hexdigest()[:24]
    return config_dir() / "trigger_parks" / f"{identity}.json"


@dataclass
class TriggerPark:
    token: str
    trigger_id: str
    card: dict[str, Any] = field(default_factory=dict)
    question: str = ""
    created_at: float = 0.0
    action_revision: str = ""
    review_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "trigger_id": self.trigger_id,
            "card": dict(self.card),
            "question": self.question,
            "created_at": self.created_at,
            "action_revision": self.action_revision,
            "review_id": self.review_id,
        }

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> TriggerPark:
        card = record.get("card")
        return cls(
            token=str(record.get("token") or ""),
            trigger_id=str(record.get("trigger_id") or ""),
            card=dict(card) if isinstance(card, dict) else {},
            question=str(record.get("question") or ""),
            created_at=float(record.get("created_at") or 0.0),
            action_revision=str(record.get("action_revision") or ""),
            review_id=str(record.get("review_id") or ""),
        )


def parked(result: Any) -> bool:
    return (
        result is not None
        and bool(getattr(result, "success", False))
        and str(getattr(result, "outcome", "") or "") == "needs_input"
    )


def _card(result: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(getattr(result, "stdout", "") or "{}"))
    except (TypeError, ValueError):
        return {}
    value = payload.get("needs_input") if isinstance(payload, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def _question(result: Any, card: dict[str, Any]) -> str:
    return (
        str(card.get("blocker") or "").strip()
        or str(getattr(result, "stderr", "") or "").strip()
        or "The action stopped and needs you."
    )


def waiting_line(result: Any) -> str:
    return f"Waiting for you. {_question(result, _card(result))}"


def _ran_through(result: Any) -> bool:
    return (
        result is not None
        and bool(getattr(result, "success", False))
        and str(getattr(result, "outcome", "") or "") in ("", "done")
    )


def load(trigger_id: str) -> TriggerPark | None:
    try:
        payload = json.loads(_path(trigger_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    park = TriggerPark.from_dict(payload) if isinstance(payload, dict) else None
    return park if park is not None and park.trigger_id == trigger_id else None


def _save(park: TriggerPark) -> None:
    from gideon.core.atomic_write import atomic_write

    path = _path(park.trigger_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(park.to_dict(), indent=2, ensure_ascii=False))


def settle(trigger: Any, result: Any, *, state: Any = None) -> None:
    try:
        identity = str(getattr(trigger, "id", "") or "")
        if not identity:
            return
        if parked(result):
            raise_park(trigger, result, state=state)
        elif _ran_through(result):
            withdraw(identity, state=state)
    except Exception:
        logger.warning("trigger park settlement failed", exc_info=True)


def raise_park(trigger: Any, result: Any, *, state: Any = None) -> TriggerPark | None:
    identity = str(getattr(trigger, "id", "") or "")
    if not identity or not parked(result):
        return None
    try:
        park = load(identity)
        if park is None:
            card = _card(result)
            park = TriggerPark(
                token=secrets.token_urlsafe(24),
                trigger_id=identity,
                card=card,
                question=_question(result, card),
                created_at=time.time(),
            )
            from gideon.automation.triggers.grants import action_revision

            park.action_revision = action_revision(trigger)
            _save(park)
        _raise_row(trigger, park, state=state)
        return park
    except Exception:
        logger.warning("trigger %s: could not record its park", identity, exc_info=True)
        return None


def associate_review(trigger: Any, review_id: str) -> bool:
    from gideon.automation.triggers.grants import action_revision
    from gideon.automation.triggers.review import TriggerReviewStore

    park = load(trigger.id)
    card = TriggerReviewStore().get(review_id)
    revision = action_revision(trigger)
    if park is None or card is None or card.get("status") != "running" or card.get("trigger_id") != f"store:{trigger.id}" or card.get("action_revision") != revision:
        return False
    park.review_id, park.action_revision = review_id, revision
    _save(park)
    return True


def restore(park: TriggerPark) -> None:
    if load(park.trigger_id) is None:
        _save(park)


def claim(trigger_id: str, token: str) -> TriggerPark | None:
    park = load(trigger_id)
    if park is None or not token or not secrets.compare_digest(park.token, str(token)):
        return None
    path = _path(trigger_id)
    claimed = path.with_suffix(".claimed")
    try:
        os.replace(path, claimed)
    except OSError:
        return None
    with contextlib.suppress(OSError):
        claimed.unlink()
    return park


def withdraw(trigger_id: str, *, state: Any = None) -> bool:
    path = _path(trigger_id)
    if not trigger_id or not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    close_row(state, trigger_id)
    return True


def close_row(state: Any, trigger_id: str) -> int:
    from gideon.integrations.inbox import resolve_attention_items

    return resolve_attention_items(
        state if state is not None else _dashboard_state(),
        {"trigger_park": trigger_id},
    )


def _dashboard_state() -> Any:
    from gideon.integrations.inbox_providers.native_source import get_dashboard_state

    with contextlib.suppress(Exception):
        return get_dashboard_state()
    return None


def _raise_row(trigger: Any, park: TriggerPark, *, state: Any = None) -> str:
    from gideon.automation.workflows.attention import ask_body
    from gideon.automation.workflows.needs_input import BlockKind, NeedsInputItem
    from gideon.integrations.inbox import emit_attention_item

    card = (
        NeedsInputItem.from_dict(park.card)
        if park.card
        else NeedsInputItem(run_id="", node_id="", blocker=park.question)
    )
    card = replace(
        card,
        blocker=card.blocker or park.question,
        block_kind=BlockKind.APPROVAL,
        choices=[],
        resume_token=park.token,
        created_at=card.created_at or park.created_at,
    )
    refs = {
        "needs_input": card.to_dict(),
        "resume_token": park.token,
        "trigger": park.trigger_id,
        "trigger_name": str(getattr(trigger, "name", "") or park.trigger_id),
        "trigger_park": park.trigger_id,
    }
    return emit_attention_item(
        state if state is not None else _dashboard_state(),
        source=SOURCE,
        kind="needs_input",
        item_kind="needs_input",
        title=park.question,
        body=ask_body({"kind": "approval"}, {"attempted": card.attempted}),
        refs=refs,
        dedup_key=f"trigger_park:{park.trigger_id}",
    )
