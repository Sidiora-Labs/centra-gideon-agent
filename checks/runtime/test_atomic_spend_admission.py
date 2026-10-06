"""Actual persisted admission/settlement and native playback accounting."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gideon.engine.routing.rates import set_rate
from gideon.integrations.llm.base import EVENT_TEXT_CHUNK, LLMEvent
from gideon.integrations.llm.scripted import ScriptedProvider
from gideon.security.guardrails.audit import read_recent
from gideon.security.guardrails.budgets import (
    Budget,
    CallCost,
    Hold,
    SpendMeter,
    Waiting,
)
from gideon.security.guardrails.failure import BudgetExceededError
from gideon.security.guardrails.model_call import ModelCallGuard, call_cost


def test_concurrent_first_calls_share_one_reservation_and_release(tmp_path):
    meter = SpendMeter(config_dir=tmp_path)
    barrier = Barrier(12)

    def admit(_):
        barrier.wait()
        return meter.admit(
            CallCost("paid:model", 20, (1, 1)), day=Budget(max_dollars=0.01)
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        answers = list(pool.map(admit, range(12)))
    holds = [a for a in answers if isinstance(a, Hold)]
    assert len(holds) == 1
    assert sum(isinstance(a, Waiting) for a in answers) == 11
    meter.release(holds[0])
    assert meter.held() == (0, 0)
    assert isinstance(
        meter.admit(CallCost("paid:model", 20, (1, 1)), day=Budget(max_dollars=0.01)),
        Hold,
    )


def test_settle_once_persists_unknown_and_known_zero_distinct(tmp_path):
    meter = SpendMeter(config_dir=tmp_path)
    hold = meter.admit(
        CallCost("m:unknown", 5),
        day=Budget(max_tokens=10000),
        run=Budget(max_tokens=10000),
        run_key="r",
    )
    meter.settle(
        hold,
        ref="m:unknown",
        tokens=8,
        answer_tokens=3,
        dollars=0,
        priced=False,
        run_key="r",
    )
    meter.settle(
        hold,
        ref="m:unknown",
        tokens=8,
        answer_tokens=3,
        dollars=0,
        priced=False,
        run_key="r",
    )
    assert meter.day_totals().tokens == 8
    assert meter.day_totals().unpriced == 1
    assert meter.run_totals("r").tokens == 8
    assert SpendMeter(config_dir=tmp_path).day_totals().unpriced == 1
    refusal = meter.admit(CallCost("m:unknown"), day=Budget(max_dollars=1))
    assert isinstance(refusal, BudgetExceededError) and refusal.why == "unpriced"
    meter.charge(0, 2)
    assert (
        meter.admit(CallCost("m:free", rate=(0, 0)), day=Budget(max_dollars=1)) is None
    )
    refusal = meter.admit(
        CallCost("m:free", rate=(0, 0)), day=Budget(max_tokens=8, max_dollars=1)
    )
    assert isinstance(refusal, BudgetExceededError) and refusal.dimension == "tokens"


def test_learned_calls_cannot_overbook_remaining_room(tmp_path):
    meter = SpendMeter(config_dir=tmp_path)
    meter.settle(
        None, ref="p:m", tokens=100, answer_tokens=100, dollars=0.4, priced=True
    )
    cost = CallCost("p:m", rate=(0, 1000))
    hold = meter.admit(cost, day=Budget(max_dollars=1))
    assert isinstance(hold, Hold)
    waiting = meter.admit(cost, day=Budget(max_dollars=1))
    assert isinstance(waiting, Waiting)
    meter.settle(
        hold, ref="p:m", tokens=100, answer_tokens=100, dollars=0.4, priced=True
    )
    refusal = meter.admit(cost, day=Budget(max_dollars=1))
    assert isinstance(refusal, BudgetExceededError) and refusal.why == "no_room"
    assert meter.held() == (0, 0)


def _script(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    path = tmp_path / "script.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "turns": [
                    {"text": "ok", "usage": {"input_tokens": 5, "output_tokens": 7}}
                ],
            }
        )
    )
    monkeypatch.setenv("GIDEON_SCRIPTED_MODEL_SCRIPT", str(path))
    return ScriptedProvider()


@pytest.mark.asyncio
async def test_native_playback_uses_actual_override_and_one_attempt(
    tmp_path, monkeypatch
):
    inner = _script(tmp_path, monkeypatch)
    set_rate("paid:actual", {"in_per_mtok": 1000, "out_per_mtok": 2000}, home=tmp_path)
    meter = SpendMeter(config_dir=tmp_path)
    guard = ModelCallGuard(
        inner,
        use_case="background",
        provider_name="paid",
        model="unpriced-default",
        budget=Budget(max_dollars=20),
        meter=meter,
    )
    events = [
        event
        async for event in guard.complete(
            [{"role": "user", "content": "hello"}], model="actual"
        )
    ]
    assert meter.day_totals().tokens == 12
    assert meter.day_totals().dollars == 0.019
    assert meter.day_totals().unpriced == 0
    assert meter.held() == (0, 0)
    assert events[-1].tool_meta["spend_charged"] is True
    rows = read_recent()
    assert len(rows) == 1 and rows[0]["model"] == "actual"


@pytest.mark.asyncio
async def test_failed_report_is_charged_once_without_counting_chunks(
    tmp_path, monkeypatch
):
    _script(tmp_path, monkeypatch)
    meter = SpendMeter(config_dir=tmp_path)
    guard = ModelCallGuard(
        ScriptedProvider(),
        use_case="background",
        provider_name="p",
        model="m",
        meter=meter,
    )

    async def failed():
        for _ in range(3):
            yield LLMEvent(
                kind=EVENT_TEXT_CHUNK,
                text="partial",
                input_tokens=4,
                output_tokens=2,
                cost_usd=0.2,
                tool_meta={"usage_reported": True},
            )
        raise RuntimeError("connection ended")

    with pytest.raises(RuntimeError):
        async for _ in guard._guarded(failed(), strategy="direct"):
            pass
    assert meter.day_totals().tokens == 6
    assert meter.day_totals().dollars == 0.2
    assert meter.held() == (0, 0)
    rows = read_recent()
    assert (
        len(rows) == 1 and rows[0]["dollars_est"] == 0.2 and rows[0]["passed"] is False
    )
