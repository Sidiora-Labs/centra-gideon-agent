"""Inbox service — the runtime behind the dashboard Inbox page.

Holds the inbox entity (``state`` + ``store``) and provides the AI affordances the
dashboard calls on demand:

* :meth:`classify` — triage a stored item into needs_reply / fyi / noise.
* :meth:`draft_reply` — draft a reply to a stored item in the user's voice.
* :meth:`generate_digest` — summarize a channel's recent messages into a catch-up item.

All three run one-shot LLM jobs over the item's stored content through the bound
chat model (``one_shot_completion``). The message text is EXTERNAL, untrusted
content (a scraped channel/filesystem message can carry a prompt-injection), so it is
wrapped with :func:`fence_untrusted` before it ever reaches a prompt — the model
reads it as quoted data, not instructions. This is the one seam where third-party
message text enters an LLM prompt, mirroring how the web-tools app fences web_fetch
output at its tool boundary.

The service is channel-independent: draft/classify/digest operate on the stored items
(populated by the native push source + any configured poll providers), so they work
even with no external provider connected.

It also owns the inbox **poll loop** (:meth:`start` / :meth:`stop`): each tick polls
the wired message-source provider for new messages (ingesting them with alert
evaluation + live WS broadcast). The loop no-ops when no provider is wired.

Maintenance — retention cleanup honoring the entity settings (``auto_cleanup_enabled``
/ ``retention_days``) plus dismissed-set pruning — is NOT on that loop and has no
private timer here. It is the ``inbox.maintenance`` job of the remediation engine
(:mod:`gideon.operations.resilience.remediation`), measured through
:func:`maintenance_backlog` and executed through :func:`run_live_maintenance`, both of
which act on the LIVE service bound to the dashboard state. Maintenance is therefore
scored, visible in the Doctor panel, runnable on demand, and bound by the global
remediation switch like every other absorbed maintenance pass.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from gideon import shutdown_event
from gideon.assurance import trace_recorder as _trace
from gideon.integrations.inbox import (
    SOURCE_DECLARABLE_KINDS,
    Classification,
    Confidence,
    InboxItem,
    InboxState,
    InboxStore,
    ItemKind,
    ItemStatus,
    evaluate_alert,
    make_item_id,
    notify_inbox_alert,
)
from gideon.security.guardrails.audit import caller_scope
from gideon.security.security import fence_untrusted

if TYPE_CHECKING:
    from gideon.integrations.inbox_providers.base import (
        IncomingMessage,
        MessageSourceProvider,
    )

logger = logging.getLogger(__name__)


def _dashboard_state():
    """The process-wide dashboard state (set at startup), or None headless."""
    from gideon.integrations.inbox_providers.native_source import get_dashboard_state

    return get_dashboard_state()


_MAX_MESSAGE_CHARS = 6000
_MAX_THREAD_TURNS = 12
_MAX_DIGEST_MESSAGES = 60


def _resolve_source_kind(declared: str, source_name: str) -> str:
    """The ``item_kind`` to persist for a source-declared *declared* kind.

    Unset (the default, and every source written before the field existed) is a plain
    ``message`` — the inbox began as a channel-message surface and that is what those rows
    are. A value inside :data:`SOURCE_DECLARABLE_KINDS` is taken as declared.

    Anything else is REFUSED, and the item is filed as ``message`` with a warning naming
    the source and the value. The two rejected postures, and why:

    * *Trust it.* A source could then invent a kind the dashboard has no chip, icon or
      label for — the row would be unfilterable and unreachable behind every kind chip.
      It could also claim one of core's non-channel attention kinds and render a row with
      no refs, no deep-link and no reply.
    * *Drop the message.* One typo (``"mail"`` for ``"email"``) would silently stop a
      user's mail from arriving at all, and in the filesystem source's case would wedge the
      poll batch on the offending file forever. Losing a message is a strictly worse
      outcome than mis-filing one.

    So the row is delivered (never lost) and confined to a kind the UI can render, and the
    mistake is loud on the one side that can fix it — the provider author's logs. This is
    NOT a silent fallback: an unknown kind always logs.
    """
    if not declared:
        return ItemKind.MESSAGE.value
    if declared in SOURCE_DECLARABLE_KINDS:
        return declared
    logger.warning(
        "inbox source %r declared item kind %r, which is not one of %s — filing as %r",
        source_name,
        declared,
        sorted(SOURCE_DECLARABLE_KINDS),
        ItemKind.MESSAGE.value,
    )
    return ItemKind.MESSAGE.value


def _fence_message(item: InboxItem) -> str:
    """Render an item's external text (body + thread context) as ONE fenced block.

    Everything the sender controlled is inside a single ``<untrusted_content>`` fence
    so the model can't be steered by injected instructions. Thread context is
    included oldest-first with attributions the model can quote."""
    parts: list[str] = []
    for turn in (item.thread_context or [])[-_MAX_THREAD_TURNS:]:
        who = str(turn.get("sender") or turn.get("sender_name") or "someone")
        txt = str(turn.get("text") or "")
        if txt.strip():
            parts.append(f"{who}: {txt}")
    body = (item.message or "")[:_MAX_MESSAGE_CHARS]
    parts.append(f"{item.sender_name or 'sender'}: {body}")
    return fence_untrusted("\n".join(parts), source="inbox-message")


class InboxService:
    """Owns inbox state/store + the on-demand AI triage affordances."""

    def __init__(
        self,
        *,
        state: InboxState | None = None,
        store: InboxStore | None = None,
        provider: "MessageSourceProvider | None" = None,
        user_name: str = "",
        style_rules: str = "",
    ) -> None:
        self.state = state or InboxState()
        self.inbox = store or InboxStore()
        self._provider = provider
        self._user_name = user_name or "the user"
        self._style_rules = style_rules or ""
        self._last_poll_at = 0.0
        self._last_poll_ok = True
        self._last_error = ""
        self._poll_count = 0
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    def health(self) -> dict:
        stale = bool(self._last_poll_at) and (time.time() - self._last_poll_at) > 900
        return {
            "running": self._task is not None and not self._task.done(),
            "last_poll_at": self._last_poll_at,
            "last_poll_ok": self._last_poll_ok,
            "last_error": self._last_error,
            "poll_count": self._poll_count,
            "stale": stale,
        }

    def start(self) -> None:
        """Start the poll loop. Idempotent.

        Starts NO private task when no provider is wired, and no maintenance timer in
        any case: polling is the loop's only remaining reason to exist, and maintenance
        belongs to the remediation engine (see the module docstring)."""
        if self._provider is None:
            logger.info("Inbox: no message-source provider wired — no poll loop")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("Inbox loop started (provider=%s)", self._provider.source_name)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _poll_interval(self) -> float:
        try:
            from gideon.core.config.loader import AppConfig

            return float(AppConfig.load().inbox.poll_interval_seconds)
        except Exception:
            return 60.0

    async def _loop(self) -> None:
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=self._poll_interval()
                )
                return
            except asyncio.TimeoutError:
                pass
            if self._provider is not None:
                try:
                    await self._poll_once()
                    self._last_poll_ok = True
                    self._last_error = ""
                except Exception as exc:
                    self._last_poll_ok = False
                    self._last_error = str(exc) or exc.__class__.__name__
                    logger.warning("Inbox poll failed", exc_info=True)
                self._last_poll_at = time.time()
                self._poll_count += 1

    async def _poll_once(self) -> None:
        """Fetch new messages from the wired provider and ingest them."""
        assert self._provider is not None
        from gideon.core.config.loader import AppConfig

        cfg = AppConfig.load().inbox
        messages, checkpoints = await self._provider.poll(
            list(cfg.watched_channels), dict(self.state.last_read_ts), cfg.user_id
        )
        if checkpoints:
            self.state.last_read_ts.update(checkpoints)
        ingested = self._ingest(
            messages, own_user_id=cfg.user_id, test_mode=cfg.test_mode
        )
        if ingested or checkpoints:
            self.state.save()

    def _ingest(
        self,
        messages: "list[IncomingMessage]",
        *,
        own_user_id: str = "",
        test_mode: bool = False,
    ) -> int:
        """Convert polled messages to stored items (dedup, mute/dismiss filters),
        evaluating alerts + broadcasting each new item live. Returns # ingested."""
        if not messages:
            return 0
        operator = self._operator_name()
        dash_state = _dashboard_state()
        can_reply = bool(
            self._provider is not None and self._provider.source_name != "filesystem"
        )
        source_name = self._provider.source_name if self._provider else "native"
        count = 0
        for m in messages:
            item_id = f"{m.channel_id}_{m.timestamp}"
            if item_id in self.inbox.items or item_id in self.state.dismissed:
                continue
            if m.thread_id and m.thread_id in self.state.muted_threads:
                continue
            if own_user_id and m.sender_id == own_user_id and not test_mode:
                continue
            if m.channel_name:
                self.state.channel_names[m.channel_id] = m.channel_name
            item = InboxItem(
                id=item_id,
                channel=m.channel_id,
                channel_name=m.channel_name or m.channel_id,
                thread_ts=m.thread_id,
                message=m.text,
                sender_id=m.sender_id,
                sender_name=m.sender_name or m.sender_id,
                thread_context=list(m.thread_context or []),
                created_at=m.timestamp or time.time(),
                source=source_name,
                can_reply=can_reply,
                item_kind=_resolve_source_kind(m.kind, source_name),
            )
            self.inbox.add(item)
            count += 1
            try:
                from gideon.automation.event_triggers import SOURCE_INBOX, emit_event

                emit_event(
                    source=SOURCE_INBOX,
                    event_type="message_received",
                    key=item_id,
                    value=m.text,
                    now=time.time(),
                    meta={
                        "sender": m.sender_id,
                        "sender_name": m.sender_name or m.sender_id,
                        "address": m.channel_id,
                        "source_name": item.source,
                    },
                )
            except Exception:
                logger.debug("inbox event-trigger emit failed", exc_info=True)
            reason = evaluate_alert(item, operator)
            if _trace.is_recording():
                _trace.record(
                    "inbox",
                    m.channel_id,
                    "item_ingested",
                    {
                        "item_id": item_id,
                        "alerted": bool(reason),
                        "reason": reason or "",
                    },
                )
            if reason:
                notify_inbox_alert(dash_state, item, reason)
            if dash_state is not None:
                try:
                    from gideon.interfaces.dashboard.handlers_inbox import _redact_item

                    dash_state.broadcast_ws(
                        "inbox_new_item", _redact_item(item.to_dict())
                    )
                except Exception:
                    logger.debug("inbox ingest broadcast failed", exc_info=True)
        if count:
            self.inbox.flush()
        return count

    def maintenance_backlog(self) -> int:
        """How much maintenance work is waiting: items past the retention window (only
        when auto-cleanup is enabled — nothing is due otherwise) plus dismissed ids old
        enough to prune.

        The remediation engine's deficit probe, so it is CHEAP: two passes over data
        already in memory, no disk read, no model call, no mutation."""
        from gideon.extensions.providers.entity_routes import load_inbox_settings

        pending = len(self.state.stale_dismissed())
        settings = load_inbox_settings()
        if settings.get("auto_cleanup_enabled"):
            try:
                days = max(1, int(settings.get("retention_days") or 90))
            except (TypeError, ValueError):
                days = 90
            cutoff = time.time() - (days * 86400)
            pending += sum(
                1 for item in self.inbox.items.values() if item.created_at < cutoff
            )
        return pending

    def run_maintenance(self) -> int:
        """Retention cleanup honoring the inbox entity settings + state pruning.
        Returns the number of items deleted. Safe to call any time."""
        from gideon.extensions.providers.entity_routes import load_inbox_settings

        settings = load_inbox_settings()
        removed = 0
        if settings.get("auto_cleanup_enabled"):
            try:
                days = max(1, int(settings.get("retention_days") or 90))
            except (TypeError, ValueError):
                days = 90
            removed = self.inbox.cleanup_by_retention(days)
        if self.state.prune_dismissed():
            self.state.save()
        try:
            from gideon.cognition.feedback import check_retire_candidates

            check_retire_candidates(state=_dashboard_state())
        except Exception:  # noqa: BLE001 — maintenance must never fail on feedback
            logger.debug("feedback retire check failed", exc_info=True)
        return removed

    @staticmethod
    def _operator_name() -> str:
        try:
            from gideon.core.config.loader import AppConfig

            return AppConfig.load().dashboard.user_name or ""
        except Exception:
            return ""

    async def classify(self, item_id: str) -> InboxItem | None:
        """Triage a stored item into needs_reply/fyi/noise + confidence, persist, return it."""
        from gideon.security.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        item = self.inbox.items.get(item_id)
        if item is None:
            return None
        from gideon.integrations.llm_helpers import one_shot_completion
        from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

        prompt = (
            render_use_case_prompt(
                "inbox_classify",
                {
                    "channel": item.channel_name or item.channel,
                    "sender": item.sender_name or "unknown",
                    "message": _fence_message(item),
                },
            )
            or ""
        )
        from gideon.security.guardrails.failure import OutputContractError

        try:
            with caller_scope("inbox_triage"):
                raw = await one_shot_completion(
                    prompt, use_case="background", output_type=dict
                )
        except OutputContractError as exc:
            raw = exc.raw
        except Exception:
            logger.warning("inbox classify failed for %s", item_id, exc_info=True)
            return None
        cls, conf = _parse_classification(raw)
        return self.inbox.update(item_id, classification=cls, confidence=conf)

    async def draft_reply(self, item_id: str) -> InboxItem | None:
        """Draft a reply to a stored item in the user's voice; persist + return the item.

        Returns None if the item is unknown or the model call fails. A model that
        judges no reply is warranted returns the SKIP sentinel → we store an empty
        draft and leave the item pending (the human decides)."""
        from gideon.security.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        item = self.inbox.items.get(item_id)
        if item is None:
            return None
        from gideon.integrations.llm_helpers import one_shot_completion
        from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

        style = (
            f"Match this voice/style when replying:\n{self._style_rules}"
            if self._style_rules
            else ""
        )
        prompt = (
            render_use_case_prompt(
                "inbox_draft",
                {
                    "user_name": self._user_name,
                    "channel": item.channel_name or item.channel,
                    "sender": item.sender_name or "unknown",
                    "message": _fence_message(item),
                    "style": style,
                },
            )
            or ""
        )
        try:
            with caller_scope("inbox_triage"):
                raw = (
                    await one_shot_completion(prompt, use_case="background") or ""
                ).strip()
        except Exception:
            logger.warning("inbox draft failed for %s", item_id, exc_info=True)
            return None
        draft = "" if raw.upper() == "SKIP" else raw
        updates: dict = {"draft": draft, "context_summary": "AI-drafted reply"}
        if draft and item.classification == Classification.NOISE:
            updates["classification"] = Classification.NEEDS_REPLY
        return self.inbox.update(item_id, **updates)

    async def generate_digest(
        self, channel_id: str, hours: float = 4.0
    ) -> InboxItem | None:
        """Summarize a channel's recent messages into a new digest inbox item.

        Pulls the window from the configured provider's channel history when one is
        wired; otherwise falls back to the stored items for that channel. Returns the
        created digest item, or None when there's nothing in the window.

        The id comes from :func:`~gideon.integrations.inbox.make_item_id`, like every
        other minted item: ``{channel}_digest_{uuid8}_{ts}``. The old
        ``{channel}_digest_{int(ts)}`` collided — two digests for one channel inside the
        same second were the same id, so the second one REPLACED the first in the store.
        The uuid8 sits in the middle because the trailing timestamp is what ``InboxItem.ts``
        (and therefore sorting and retention) reads."""
        messages = await self._recent_messages(channel_id, hours)
        if not messages:
            return None
        from gideon.integrations.llm_helpers import one_shot_completion
        from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

        channel_name = self.state.channel_names.get(channel_id, channel_id)
        fenced = fence_untrusted(
            "\n".join(messages[-_MAX_DIGEST_MESSAGES:]), source="inbox-channel"
        )
        prompt = (
            render_use_case_prompt(
                "inbox_digest",
                {
                    "channel": channel_name,
                    "hours": f"{hours:g}",
                    "user_name": self._user_name,
                    "messages": fenced,
                },
            )
            or ""
        )
        try:
            with caller_scope("inbox_triage"):
                summary = (
                    await one_shot_completion(prompt, use_case="background") or ""
                ).strip()
        except Exception:
            logger.warning("inbox digest failed for %s", channel_id, exc_info=True)
            return None
        if not summary:
            return None
        ts = time.time()
        item = InboxItem(
            id=make_item_id(f"{channel_id}_digest", now=ts),
            channel=channel_id,
            channel_name=channel_name,
            thread_ts=None,
            message=summary,
            sender_id="",
            sender_name=f"Digest · last {hours:g}h",
            classification=Classification.FYI,
            confidence=Confidence.HIGH,
            status=ItemStatus.PENDING,
            created_at=ts,
            context_summary=f"AI digest of {len(messages)} messages",
            source="digest",
            can_reply=False,
        )
        self.inbox.add(item)
        self.inbox.flush()
        return item

    async def _recent_messages(self, channel_id: str, hours: float) -> list[str]:
        """Attributed, oldest-first message lines for the window — from the provider's
        channel history if available, else from stored items for that channel."""
        cutoff = time.time() - hours * 3600
        lines: list[str] = []
        if self._provider is not None:
            try:
                raw = await self._provider.get_channel_history(
                    channel_id, oldest=str(cutoff)
                )
                for m in raw:
                    who = str(m.get("sender_name") or m.get("user") or "someone")
                    txt = str(m.get("text") or "")
                    if txt.strip():
                        lines.append(f"{who}: {txt}")
            except Exception:
                logger.debug(
                    "channel history fetch failed for %s", channel_id, exc_info=True
                )
        if not lines:
            stored = [
                it
                for it in self.inbox.items.values()
                if it.channel == channel_id
                and it.created_at >= cutoff
                and it.source != "digest"
            ]
            for it in sorted(stored, key=lambda i: i.created_at):
                if (it.message or "").strip():
                    lines.append(f"{it.sender_name or 'sender'}: {it.message}")
        return lines


def _parse_classification(raw: str) -> tuple[str, str]:
    """Parse the classify model output → (classification, confidence), defaulting
    safely to needs_reply/needs_review when the JSON is malformed."""
    valid_cls = {c.value for c in Classification}
    valid_conf = {c.value for c in Confidence}
    try:
        from gideon.integrations.llm_helpers import parse_llm_json

        data = parse_llm_json(raw) or {}
    except Exception:
        data = {}
    cls = str(data.get("classification", "")).lower()
    conf = str(data.get("confidence", "")).lower()
    return (
        cls if cls in valid_cls else Classification.NEEDS_REPLY.value,
        conf if conf in valid_conf else Confidence.NEEDS_REVIEW.value,
    )


def live_service() -> "InboxService | None":
    """The InboxService this process is actually running, or None (headless, or the
    gateway has not bound one yet).

    The gateway binds it onto the dashboard state at startup; maintenance MUST go
    through it rather than constructing a second InboxService, which would hold its own
    copy of the store and prune a snapshot the live service would later overwrite."""
    svc = getattr(_dashboard_state(), "_inbox_svc", None)
    return svc if isinstance(svc, InboxService) else None


def maintenance_backlog() -> int:
    """The live service's maintenance backlog, 0 when no service is running."""
    svc = live_service()
    return svc.maintenance_backlog() if svc is not None else 0


def run_live_maintenance() -> str:
    """Run inbox maintenance on the live service — the ``inbox.maintenance``
    remediation job."""
    svc = live_service()
    if svc is None:
        return "no live inbox service — nothing to maintain"
    return f"pruned {svc.run_maintenance()} expired inbox item(s)"
