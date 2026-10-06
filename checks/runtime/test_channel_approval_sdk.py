"""The public approval kit exercises native callback authority on a local transport."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_channel_offered_answers import isolated, native

from gideon.assurance.testing.channel_conformance import assert_channel_contract
from gideon.sdk.channel import ChannelContractError, assert_channel_approvals
from gideon.security.approval_brief import APPROVAL_BRIEF_META_KEY


async def harness(n, monkeypatch, *, refuse_trust=False):
    original = n.delivery.request_approval
    observed = {}
    answered = []
    sequence = {"value": 0}

    async def recorded(event, **kwargs):
        hook = kwargs["on_prompted"]

        def prompted(pending):
            owned = hook(pending)
            observed["pending"] = pending
            return owned

        kwargs["on_prompted"] = prompted
        task = asyncio.create_task(original(event, **kwargs))
        observed["wait"] = task
        return await task

    monkeypatch.setattr(n.delivery, "request_approval", recorded)

    async def ask(event):
        for approval_id in tuple(n.state._pending_approvals):
            n.state.cancel_approval(
                approval_id, reason="previous contract question closed"
            )
        observed.clear()
        sequence["value"] += 1
        request_id = f'sdk-{sequence["value"]}'
        future = asyncio.get_running_loop().create_future()
        n.session._approval_futures[request_id] = future
        n.session.append(
            "permission",
            event.title,
            json.dumps({"request_id": request_id, "asked_by": "agent:ordinary"}),
        )
        brief = event.tool_meta[APPROVAL_BRIEF_META_KEY]
        n.state._register_chat_approval(
            {
                "id": request_id,
                "session": n.session.key,
                "tool": event.title,
                "tool_input": event.tool_input,
                "tool_purpose": event.tool_purpose,
                "risk": event.risk_level,
                "blast_radius": brief["blastRadius"],
            }
        )
        for _ in range(400):
            if observed.get("pending") is not None:
                return observed["pending"], observed["wait"]
            await asyncio.sleep(0.005)
        raise AssertionError("native owner controller did not expose its actual prompt")

    async def press(pending, key):
        if refuse_trust and key == "trust":
            monkeypatch.setattr(
                "gideon.security.approval_grants.stands", lambda *a, **k: False
            )
        token = next(
            (token for token, item in n.delivery.pending.items() if item is pending),
            None,
        )
        if token is None:
            token = next(
                token
                for token, (item, ending) in n.delivery.approval_endings.items()
                if item is pending
            )
        cq = {
            "id": "sdk-callback",
            "data": f"answer:{token}:{key}",
            "from": {"id": 101, "is_bot": False},
            "message": {
                "chat": {"id": pending.chat_id, "type": "private"},
                "message_id": pending.message_id,
            },
        }
        await n.delivery.resolve_callback(cq)
        answered.append(
            (
                key,
                pending.chosen_answer,
                pending.future.result() if pending.future.done() else None,
            )
        )
        return n.calls[-1][1]["text"]

    return SimpleNamespace(ask=ask, press=press, answered=answered, sequence=sequence)


@pytest.mark.asyncio
async def test_public_sdk_clause_drives_actual_native_offers_endings_and_callbacks(
    native, monkeypatch
):
    h = await harness(native, monkeypatch)
    await assert_channel_approvals(native.delivery, press=h.press, ask_approval=h.ask)
    assert h.sequence["value"] == 8
    assert ("trust", "trust", "approved") in h.answered
    assert {key for key, chosen, ending in h.answered if chosen == key} == {
        "approved",
        "trust",
        "rejected",
    }
    assert native.session._trust
    assert (
        "press" in __import__("inspect").signature(assert_channel_contract).parameters
    )
    assert (
        "ask_approval"
        in __import__("inspect").signature(assert_channel_contract).parameters
    )
    assert any(method == "answerCallbackQuery" for method, payload in native.calls)
    assert any(method == "editMessageReplyMarkup" for method, payload in native.calls)


@pytest.mark.asyncio
async def test_public_kit_cannot_create_trust_when_live_native_ceiling_refuses(
    native, monkeypatch
):
    h = await harness(native, monkeypatch, refuse_trust=True)
    with pytest.raises(ChannelContractError, match="delivery wait MUST end"):
        await assert_channel_approvals(
            native.delivery, press=h.press, ask_approval=h.ask
        )
    assert not native.session._trust
    assert any(
        key == "trust" and chosen == "" and ending is None
        for key, chosen, ending in h.answered
    )
