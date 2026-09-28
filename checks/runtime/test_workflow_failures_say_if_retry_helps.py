from __future__ import annotations

import time

import httpx

from gideon.automation.workflows.failure_taxonomy import (
    classify_action_result,
    classify_exception,
    with_breaker_window,
)
from gideon.automation.workflows.models import Failure, FailureClass
from gideon.integrations.action_providers.base import ActionResult
from gideon.security.guardrails.breaker import get_breaker, reset_breakers
from gideon.security.guardrails.failure import (
    BudgetExceededError,
    CircuitOpenError,
    OutputContractError,
)


def test_typed_provider_failures_and_http_statuses_set_retryability() -> None:
    assert classify_exception(CircuitOpenError("provider-a", 12)).retryable
    assert classify_exception(BudgetExceededError("run", "tokens", 100, 101)).failure_class is FailureClass.BUDGET
    assert classify_exception(OutputContractError("object", "bad")).failure_class is FailureClass.PROTOCOL

    for status, expected in (
        (401, FailureClass.PERMISSION),
        (404, FailureClass.USER),
        (429, FailureClass.TRANSIENT),
        (503, FailureClass.TRANSIENT),
    ):
        request = httpx.Request("GET", "https://provider.invalid")
        response = httpx.Response(status, request=request)
        error = httpx.HTTPStatusError("provider response", request=request, response=response)
        failure = classify_exception(error)
        assert failure.failure_class is expected
        assert failure.retryable is (expected is FailureClass.TRANSIENT)


def test_transport_causes_retry_but_unknown_action_results_do_not() -> None:
    cause = ConnectionError("connection refused")
    wrapped = RuntimeError("provider wrapper")
    wrapped.__cause__ = cause
    assert classify_exception(wrapped).failure_class is FailureClass.NETWORK

    unknown = classify_action_result(ActionResult(success=False, error="exit status 2"))
    assert unknown.failure_class is FailureClass.INTERNAL
    assert not unknown.retryable
    assert classify_action_result(ActionResult(success=False, stderr="HTTP 404" )).failure_class is FailureClass.USER
    assert classify_action_result(ActionResult(success=False, stderr="HTTP 429" )).retryable


def test_failure_retry_window_round_trips_and_tracks_live_breaker() -> None:
    original = Failure(
        failure_class=FailureClass.TRANSIENT,
        retry_at=time.time() + 5,
        providers=["provider-a"],
    )
    restored = Failure.from_dict(original.to_dict())
    assert restored.retry_at == original.retry_at
    assert restored.providers == ["provider-a"]

    reset_breakers()
    breaker = get_breaker("provider-a", threshold=1, recovery_secs=20)
    breaker.record_failure()
    updated = with_breaker_window(Failure(failure_class=FailureClass.TRANSIENT), ["provider-a"])
    assert updated is not None
    assert updated.providers == ["provider-a"]
    assert updated.retry_at is not None and updated.retry_at > time.time() + 10
    reset_breakers()
