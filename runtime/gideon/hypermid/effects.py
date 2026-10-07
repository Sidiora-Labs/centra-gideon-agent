"""Typed operator access to durable external-effect reconciliation."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, cast

from .client import HypermidClient
from .foundation import Digest, Id, Scope


class EffectContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EffectLifecycleState(str, Enum):
    INTENT = "intent"
    DISPATCHED = "dispatched"
    COMMITTED = "committed"
    NOT_STARTED = "not_started"
    UNKNOWN = "unknown"


class EffectNextAction(str, Enum):
    WAIT_FOR_RECOVERY = "wait_for_recovery"
    CHECK_AUTHORITATIVE_STATUS = "check_authoritative_status"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class EffectSnapshot:
    effect_id: Id
    module_id: Id
    operation: str
    principal_id: Id
    scope: Scope
    input_digest: Digest
    created_ms: int
    state: EffectLifecycleState
    reviewable: bool
    result_digest: Digest | None = None
    reason: str | None = None
    settled_ms: int | None = None
    review_plan: EffectReviewPlan | None = None

    @property
    def next_action(self) -> EffectNextAction:
        if self.state is EffectLifecycleState.UNKNOWN:
            return EffectNextAction.CHECK_AUTHORITATIVE_STATUS
        if self.state in (
            EffectLifecycleState.INTENT,
            EffectLifecycleState.DISPATCHED,
        ):
            return EffectNextAction.WAIT_FOR_RECOVERY
        return EffectNextAction.RESOLVED

    @classmethod
    def from_wire(
        cls,
        value: object,
        *,
        expected_scope: Scope,
        expected_effect_id: Id | None = None,
    ) -> EffectSnapshot:
        raw = _mapping(value, "effect snapshot")
        required = {
            "effect_id",
            "module_id",
            "operation",
            "principal_id",
            "scope",
            "input_digest",
            "created_ms",
            "state",
            "reviewable",
        }
        optional = {"result_digest", "reason", "settled_ms", "review_plan"}
        if not required.issubset(raw) or not set(raw).issubset(required | optional):
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "effect snapshot fields are invalid"
            )
        try:
            effect_id = Id(raw["effect_id"])
            module_id = Id(raw["module_id"])
            principal_id = Id(raw["principal_id"])
            scope = Scope.from_wire(_mapping(raw["scope"], "effect scope"))
            input_digest = Digest(raw["input_digest"])
            state = EffectLifecycleState(raw["state"])
            result_digest = (
                Digest(raw["result_digest"])
                if raw.get("result_digest") is not None
                else None
            )
        except (TypeError, ValueError) as error:
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "effect snapshot identity is invalid"
            ) from error
        if scope != expected_scope:
            raise EffectContractError(
                "SCOPE_MISMATCH", "effect snapshot escaped the authenticated scope"
            )
        if expected_effect_id is not None and effect_id != expected_effect_id:
            raise EffectContractError(
                "EFFECT_MISMATCH", "effect response does not match the requested effect"
            )
        operation = raw["operation"]
        if not isinstance(operation, str) or not operation or len(operation) > 256:
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "effect operation is invalid"
            )
        created_ms = _wire_millis(raw["created_ms"], "created_ms")
        reviewable = raw["reviewable"]
        if not isinstance(reviewable, bool):
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "effect reviewable flag is invalid"
            )
        settled_ms = (
            _wire_millis(raw["settled_ms"], "settled_ms")
            if raw.get("settled_ms") is not None
            else None
        )
        reason = raw.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or not reason or len(reason) > 512
        ):
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "effect reason is invalid"
            )
        if state is EffectLifecycleState.COMMITTED:
            if result_digest is None or settled_ms is None or reason is not None:
                raise EffectContractError(
                    "INVALID_EFFECT_RESPONSE", "committed effect evidence is incomplete"
                )
        elif state in (
            EffectLifecycleState.NOT_STARTED,
            EffectLifecycleState.UNKNOWN,
        ):
            if result_digest is not None or settled_ms is None or reason is None:
                raise EffectContractError(
                    "INVALID_EFFECT_RESPONSE", "effect outcome evidence is incomplete"
                )
        elif any(item is not None for item in (result_digest, reason, settled_ms)):
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "unsettled effect contains terminal evidence"
            )
        review_plan = None
        if raw.get("review_plan") is not None:
            review_raw = _mapping(raw["review_plan"], "effect review plan")
            review_id = _wire_id(review_raw.get("review_id"), "review_id")
            review_plan = EffectReviewPlan.from_wire(
                review_raw,
                expected_scope=scope,
                expected_effect_id=effect_id,
                expected_review_id=review_id,
            )
            if (
                review_plan.input_digest != input_digest
                or review_plan.module_id != module_id
                or review_plan.operation != operation
            ):
                raise EffectContractError(
                    "INVALID_EFFECT_RESPONSE",
                    "effect review plan does not match its durable intent",
                )
        if reviewable and (
            state is not EffectLifecycleState.UNKNOWN or review_plan is not None
        ):
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "effect reviewability is inconsistent"
            )
        return cls(
            effect_id=effect_id,
            module_id=module_id,
            operation=operation,
            principal_id=principal_id,
            scope=scope,
            input_digest=input_digest,
            created_ms=created_ms,
            state=state,
            reviewable=reviewable,
            result_digest=result_digest,
            reason=reason,
            settled_ms=settled_ms,
            review_plan=review_plan,
        )


@dataclass(frozen=True, slots=True)
class EffectReviewPlan:
    review_id: Id
    effect_id: Id
    idempotency_key: Id
    module_id: Id
    operation: str
    scope: Scope
    input_digest: Digest
    provider_id: Id
    provider_proof_id: Id
    provider_proof_digest: Digest
    proposed_state: EffectLifecycleState
    created_ms: int
    plan_digest: Digest
    result_digest: Digest | None = None
    reason: str | None = None

    @classmethod
    def from_wire(
        cls,
        value: object,
        *,
        expected_scope: Scope,
        expected_effect_id: Id,
        expected_review_id: Id,
    ) -> EffectReviewPlan:
        raw = _mapping(value, "effect review plan")
        required = {
            "review_id",
            "effect_id",
            "idempotency_key",
            "module_id",
            "operation",
            "scope",
            "input_digest",
            "provider_id",
            "provider_proof_id",
            "provider_proof_digest",
            "proposed_state",
            "created_ms",
            "plan_digest",
        }
        optional = {"result_digest", "reason"}
        if not required.issubset(raw) or not set(raw).issubset(required | optional):
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE", "effect review plan fields are invalid"
            )
        try:
            plan = cls(
                review_id=Id(raw["review_id"]),
                effect_id=Id(raw["effect_id"]),
                idempotency_key=Id(raw["idempotency_key"]),
                module_id=Id(raw["module_id"]),
                operation=cast(str, raw["operation"]),
                scope=Scope.from_wire(_mapping(raw["scope"], "review scope")),
                input_digest=Digest(raw["input_digest"]),
                provider_id=Id(raw["provider_id"]),
                provider_proof_id=Id(raw["provider_proof_id"]),
                provider_proof_digest=Digest(raw["provider_proof_digest"]),
                proposed_state=EffectLifecycleState(raw["proposed_state"]),
                result_digest=(
                    Digest(raw["result_digest"])
                    if raw.get("result_digest") is not None
                    else None
                ),
                reason=cast(str | None, raw.get("reason")),
                created_ms=_wire_millis(raw["created_ms"], "created_ms"),
                plan_digest=Digest(raw["plan_digest"]),
            )
        except (TypeError, ValueError) as error:
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE", "effect review plan identity is invalid"
            ) from error
        if plan.scope != expected_scope:
            raise EffectContractError(
                "SCOPE_MISMATCH", "effect review escaped the authenticated scope"
            )
        if plan.effect_id != expected_effect_id:
            raise EffectContractError(
                "EFFECT_MISMATCH", "review does not match the requested effect"
            )
        if plan.idempotency_key != plan.effect_id:
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE",
                "review idempotency key differs from the durable effect id",
            )
        if (
            not isinstance(plan.operation, str)
            or not plan.operation
            or len(plan.operation) > 256
        ):
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE", "review operation is invalid"
            )
        if plan.review_id != expected_review_id:
            raise EffectContractError(
                "REVIEW_MISMATCH", "daemon returned a different effect review"
            )
        if plan.proposed_state not in (
            EffectLifecycleState.COMMITTED,
            EffectLifecycleState.NOT_STARTED,
        ):
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE", "review does not contain a known outcome"
            )
        if plan.proposed_state is EffectLifecycleState.COMMITTED:
            if plan.result_digest is None or plan.reason is not None:
                raise EffectContractError(
                    "INVALID_REVIEW_RESPONSE", "committed review evidence is incomplete"
                )
        elif plan.result_digest is not None or not plan.reason:
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE", "not-started review evidence is incomplete"
            )
        if plan.reason is not None and (
            not isinstance(plan.reason, str) or len(plan.reason) > 512
        ):
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE", "review reason is invalid"
            )
        return plan


@dataclass(frozen=True, slots=True)
class UnresolvedEffects:
    scope: Scope
    effects: tuple[EffectSnapshot, ...]
    checked_at_ms: int


class EffectService:
    """Scope-bound client for review and authoritative effect reconciliation."""

    def __init__(self, client: HypermidClient) -> None:
        self._client = client

    async def list_unresolved(self, scope: Scope) -> UnresolvedEffects:
        self._require_scope(scope)
        raw = _mapping(
            await self._client.request("effects.list", {}, scope=scope),
            "effect list",
        )
        if set(raw) != {"effects"} or not isinstance(raw["effects"], list):
            raise EffectContractError(
                "INVALID_EFFECT_RESPONSE", "daemon effect list is invalid"
            )
        snapshots = tuple(
            EffectSnapshot.from_wire(item, expected_scope=scope)
            for item in raw["effects"]
        )
        effects = tuple(
            effect
            for effect in snapshots
            if effect.state
            in {
                EffectLifecycleState.INTENT,
                EffectLifecycleState.DISPATCHED,
                EffectLifecycleState.UNKNOWN,
            }
        )
        return UnresolvedEffects(scope, effects, int(time.time() * 1000))

    async def status(self, scope: Scope, effect_id: Id) -> EffectSnapshot:
        self._require_scope(scope)
        effect_id = Id(effect_id)
        value = await self._client.request(
            "effects.status", {"effect_id": str(effect_id)}, scope=scope
        )
        return EffectSnapshot.from_wire(
            value, expected_scope=scope, expected_effect_id=effect_id
        )

    async def mark_reviewed(
        self,
        scope: Scope,
        effect_id: Id,
        *,
        review_id: Id | None = None,
    ) -> EffectReviewPlan:
        self._require_scope(scope)
        effect_id = Id(effect_id)
        review_id = review_id or Id(f"effect-review:{secrets.token_hex(16)}")
        raw = _mapping(
            await self._client.request(
                "effects.review",
                {
                    "effect_id": str(effect_id),
                    "review_id": str(review_id),
                },
                effect_kind="durable",
                scope=scope,
            ),
            "effect review response",
        )
        if set(raw) != {"plan"}:
            raise EffectContractError(
                "INVALID_REVIEW_RESPONSE", "daemon review response is invalid"
            )
        return EffectReviewPlan.from_wire(
            raw["plan"],
            expected_scope=scope,
            expected_effect_id=effect_id,
            expected_review_id=review_id,
        )

    async def reconcile(self, scope: Scope, plan: EffectReviewPlan) -> EffectSnapshot:
        self._require_scope(scope)
        if plan.scope != scope:
            raise EffectContractError(
                "SCOPE_MISMATCH", "effect review belongs to another scope"
            )
        value = await self._client.request(
            "effects.reconcile",
            {
                "effect_id": str(plan.effect_id),
                "review_id": str(plan.review_id),
                "reviewed_plan_digest": str(plan.plan_digest),
            },
            effect_kind="durable",
            scope=scope,
        )
        resolved = EffectSnapshot.from_wire(
            value,
            expected_scope=scope,
            expected_effect_id=plan.effect_id,
        )
        if resolved.state not in (
            EffectLifecycleState.COMMITTED,
            EffectLifecycleState.NOT_STARTED,
        ):
            raise EffectContractError(
                "RECONCILIATION_UNKNOWN",
                "authoritative provider did not establish a known effect outcome",
            )
        if resolved.state is not plan.proposed_state:
            raise EffectContractError(
                "RECONCILIATION_DIVERGED",
                "resolved outcome differs from the reviewed provider proof",
            )
        if resolved.input_digest != plan.input_digest:
            raise EffectContractError(
                "RECONCILIATION_DIVERGED",
                "resolved effect input differs from the reviewed plan",
            )
        return resolved

    def _require_scope(self, scope: Scope) -> None:
        if scope != self._client.scope:
            raise EffectContractError(
                "SCOPE_MISMATCH",
                "effect request does not match the authenticated scope",
            )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EffectContractError(
            "INVALID_EFFECT_RESPONSE", f"{name} must be an object"
        )
    return value


def _wire_millis(value: object, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 9_007_199_254_740_991
    ):
        raise EffectContractError("INVALID_EFFECT_RESPONSE", f"{name} is invalid")
    return value


def _wire_id(value: object, name: str) -> Id:
    try:
        return Id(value)
    except (TypeError, ValueError) as error:
        raise EffectContractError(
            "INVALID_EFFECT_RESPONSE", f"{name} is invalid"
        ) from error


__all__ = [
    "EffectContractError",
    "EffectLifecycleState",
    "EffectNextAction",
    "EffectReviewPlan",
    "EffectService",
    "EffectSnapshot",
    "UnresolvedEffects",
]
