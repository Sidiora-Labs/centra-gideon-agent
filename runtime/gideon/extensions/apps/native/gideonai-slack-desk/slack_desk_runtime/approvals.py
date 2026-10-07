"""Slack approval offers, authenticated answers, and Block Kit rendering."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from slack_desk_runtime.client import SlackDeskClientOps
from slack_desk_runtime.format import split_message

from gideon.sdk.channel import (
    ApprovalAnswer,
    LLMEvent,
    ModelProvider,
    Principal,
    redact_credentials,
    redact_exfiltration_urls,
)

if TYPE_CHECKING:
    from .delivery import SlackDeskDelivery

#: The key the CORE stamps its approval brief onto ``event.tool_meta`` under — the value
#: of ``gideon.approval_brief.APPROVAL_BRIEF_META_KEY``. Read as a literal because
#: the brief is ADDITIVE meta the SDK does not export: a core that composes none simply
#: leaves the key absent, and this channel then prompts exactly as it did before.

#: The one facet that is NOT a consequence: ``readOnly`` claims what a call does not do,
#: so it must never be framed as something the call "can" do. Named as the EXCEPTION
#: rather than listing the consequences, so a facet core adds later is framed as a
#: consequence automatically instead of silently reading as a reassurance.


_APPROVAL_BRIEF_META_KEY = "approval_brief"
_READ_CLAIM_FACET = "readOnly"
_SLACK_SECTION_TEXT_LIMIT = 2900
_OUTCOME_APPROVED = "approved"
_OUTCOME_REJECTED = "rejected"
_ACTION_APPROVE = "approve_tool"
_ACTION_TRUST = "trust_tool"
_ACTION_REJECT = "reject_tool"


class _PendingApproval:
    __slots__ = (
        "provider",
        "request_id",
        "session_key",
        "future",
        "answers",
        "delivery",
        "tenant",
        "owner",
        "channel",
        "thread",
        "answerer",
        "on_answer",
        "chosen_answer",
    )

    def __init__(
        self,
        provider: ModelProvider | None,
        request_id: str | int,
        session_key: str = "",
    ) -> None:
        self.provider = provider
        self.request_id = request_id
        self.session_key = session_key
        self.future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        from gideon.sdk.channel import ONE_CALL_ANSWERS

        self.answers: tuple[ApprovalAnswer, ...] = ONE_CALL_ANSWERS
        self.delivery: "SlackDeskDelivery | None" = None
        self.tenant = self.owner = self.channel = self.thread = self.chosen_answer = ""
        self.answerer: Principal | None = None
        self.on_answer: Callable[[str, Principal], bool] | None = None


_pending_approvals: dict[str, _PendingApproval] = {}


def _approval_brief_line(event: LLMEvent) -> str:
    """One line saying what this call can TOUCH, or ``""`` to show no such line.

    Slack is the surface with no room, so the core-composed brief collapses to a single
    line next to the effective risk; the dashboard stays the rich surface (per-facet
    cards carrying each facet's ``detail`` sentence). Nothing is re-derived here — every
    word comes from ``event.tool_meta``, so a hint added to the core gate reaches Slack
    without a change in this repo, and this renderer cannot drift into a second
    vocabulary.

    Two rules, both from the ``ChannelDelivery.request_approval`` contract:

    * an ABSENT blast radius renders NO line. "Nothing was established" reads to a
      person as "nothing happens", which is the opposite of what an unrecognized tool
      means. Silence is the honest render, and the caller must not fill it.
    * only ESTABLISHED facets are ever named. ``blastRadiusLine`` already contains
      exactly the ``True`` ones, so a ``False`` can never be painted as an all-clear
      ("no network") — absence of evidence never becomes evidence of absence.

    The returned text is plain (no mrkdwn, no emoji) so the approval block and the
    notification fallback can render the same string without diverging.
    """
    meta = getattr(event, "tool_meta", None)
    if not isinstance(meta, dict):
        return ""
    brief = meta.get(_APPROVAL_BRIEF_META_KEY)
    if not isinstance(brief, dict):
        return ""
    facets = str(brief.get("blastRadiusLine") or "")
    if not facets:
        return ""
    radius = brief.get("blastRadius")
    consequence = isinstance(radius, dict) and any(
        v for k, v in radius.items() if k != _READ_CLAIM_FACET
    )
    line = f"Can: {facets}" if consequence else f"{facets[:1].upper()}{facets[1:]}"
    risk = str(brief.get("risk") or "")
    if risk:
        line += f" · Risk: {risk}"
    return line


def _build_approval_blocks(
    event: LLMEvent,
    is_dm: bool = True,
    source: str = "",
    *,
    answers: tuple | None = None,
) -> list[dict]:
    """Render the exact offered answers and complete tool input.

    Standing permission appears only when the native approval owner offers it.
    A private channel alone does not authorize a standing grant.
    """
    from gideon.sdk.channel import ONE_CALL_ANSWERS

    answers = answers or ONE_CALL_ANSWERS
    action_ids = {
        "approved": _ACTION_APPROVE,
        "trust": _ACTION_TRUST,
        "rejected": _ACTION_REJECT,
    }
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": answer.label},
            "action_id": action_ids[answer.key],
            "value": str(event.request_id),
        }
        for answer in answers
    ]

    blocks: list[dict] = []

    tag = f"[{source}] " if source else ""
    title_safe, _ = redact_exfiltration_urls(event.title)
    title_safe, _ = redact_credentials(title_safe)
    footer = f":lock: {tag}*{title_safe}*"
    if event.tool_purpose:
        purpose, _ = redact_exfiltration_urls(event.tool_purpose)
        purpose, _ = redact_credentials(purpose)
        footer += f" — {purpose}"

    # When full tool_input is available, show a simple header and the
    # complete command in a code block below.
    # When tool_input is missing, fall back to the truncated title.
    if event.tool_input:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔐 *{tag}Tool approval requested:*",
                },
            },
        )
        # Security: scan for exfiltration URLs and credentials before posting
        sanitized, _ = redact_exfiltration_urls(event.tool_input)
        sanitized, _ = redact_credentials(sanitized)
        for detail in split_message(sanitized, limit=_SLACK_SECTION_TEXT_LIMIT - 6):
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"```{detail}```"},
                }
            )

    # The blast radius goes ABOVE the buttons: it is the reason to press one, so a
    # reader must meet it before the decision, not after it.
    brief_line = _approval_brief_line(event)
    if brief_line:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": brief_line}]},
        )

    for answer in answers:
        if answer.promise:
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": answer.promise}],
                }
            )
    blocks.append({"type": "actions", "elements": buttons})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return blocks


async def _request_approval(
    slack_desk: SlackDeskClientOps,
    provider: ModelProvider,
    channel: str,
    thread_ts: str,
    event: LLMEvent,
    session_key: str = "",
    is_dm: bool = True,
    *,
    render: Callable[..., list[dict]],
    update: Callable[[SlackDeskClientOps, str, str, str], Awaitable[None]],
    timeout: float,
) -> str:
    """Post approval buttons, wait for click, return 'approved' or 'rejected'."""
    blocks = render(event, is_dm=is_dm)
    approval_ts = await slack_desk.post_blocks(
        channel, blocks, "Manual approval required", thread_ts
    )

    key = f"{channel}:{approval_ts}"
    pending = _PendingApproval(provider, event.request_id, session_key)
    from gideon.sdk.channel import raw_delivery_for

    from .delivery import SlackDeskDelivery

    registered_delivery = raw_delivery_for("slack")
    pending.delivery = (
        registered_delivery
        if isinstance(registered_delivery, SlackDeskDelivery)
        else None
    )
    identity = pending.delivery.approval_identity(channel) if pending.delivery else None
    if identity is None:
        await provider.reject_tool(event.request_id)
        return "rejected"
    pending.owner, pending.tenant = identity["owner"], identity["tenant"]
    pending.channel, pending.thread = channel, thread_ts or ""
    _pending_approvals[key] = pending

    try:
        outcome = await asyncio.wait_for(pending.future, timeout=timeout)
    except asyncio.TimeoutError:
        outcome = _OUTCOME_REJECTED
        await provider.reject_tool(event.request_id)
    finally:
        _pending_approvals.pop(key, None)

    try:
        await slack_desk.delete_message(channel, approval_ts)
    except Exception:
        status = "✅ Approved" if outcome == _OUTCOME_APPROVED else "🚫 Rejected"
        title_safe, _ = redact_exfiltration_urls(event.title)
        title_safe, _ = redact_credentials(title_safe)
        await update(slack_desk, channel, approval_ts, f"🔐 *{title_safe}* — {status}")

    return outcome


async def handle_interaction(
    channel: str,
    msg_ts: str,
    action_id: str,
    user_id: str = "",
    thread_ts: str = "",
    slack_desk: SlackDeskClientOps | None = None,
    *,
    team_id: str = "",
    answer_value: str = "",
) -> str | None:
    """Answer only the live offer in its authenticated workspace and destination."""
    from slack_desk_runtime.enterprise import check_message_origin

    from gideon.sdk.channel import on_channel, raw_delivery_for

    pending = _pending_approvals.get(f"{channel}:{msg_ts}")
    if pending is None or pending.future.done() or not check_message_origin(team_id):
        return None
    delivery = pending.delivery
    if delivery is None:
        return None
    identity = delivery.approval_identity(channel)
    registered_matches = bool(raw_delivery_for("slack") is delivery)
    if (
        identity is None
        or not registered_matches
        or slack_desk is not delivery.client
        or user_id != identity["owner"]
        or user_id != pending.owner
        or identity["tenant"] != pending.tenant
        or pending.channel != channel
        or pending.thread != thread_ts
        or answer_value != str(pending.request_id)
    ):
        return None
    answer = {
        "approve_tool": "approved",
        "trust_tool": "trust",
        "reject_tool": "rejected",
    }.get(action_id)
    if answer is None:
        return None
    chosen = next((item for item in pending.answers if item.key == answer), None)
    if chosen is None:
        return None
    if answer == "trust" and not await delivery.prepare_approval_channel(channel):
        return None
    by = on_channel("slack", user_id, pending.tenant)
    if callable(pending.on_answer):
        if not pending.on_answer(answer, by):
            return None
    elif answer == "trust":
        return None
    elif pending.provider:
        if chosen.ends == "approved":
            await pending.provider.approve_tool(pending.request_id)
        else:
            await pending.provider.reject_tool(pending.request_id)
    pending.answerer = by
    pending.chosen_answer = answer
    if not pending.future.done():
        pending.future.set_result(chosen.ends)
    return action_id
