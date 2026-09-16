from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.workspace import notification_kinds


@dataclass(frozen=True)
class ResultNotice:
    title: str
    summary: str
    result: str

    @classmethod
    def scrub(cls, title: str, summary: str, result: str) -> ResultNotice:
        def clean(value: str) -> str:
            return redact_credentials(redact_exfiltration_urls(value)[0])[0]

        return cls(*(clean(value) for value in (title, summary, result)))

    @property
    def body(self) -> str:
        return f"{self.summary}\n\n{self.result}"

    @property
    def transcript(self) -> str:
        return f"{self.title}\n\n{self.result}"

    def prompt(self, maximum: int) -> tuple[str, bool]:
        prefix = f"{self.title}\n\n"
        available = max(0, maximum - len(prefix.encode("utf-8")))
        encoded = self.result.encode("utf-8")
        return (
            prefix + encoded[:available].decode("utf-8", errors="ignore"),
            len(encoded) > available,
        )


class ResultDelivery:
    def __init__(
        self,
        runtime: Any,
        notice: ResultNotice,
        *,
        audit: Any,
        logger: Any,
        maximum: int,
    ) -> None:
        self.runtime = runtime
        self.notice = notice
        self.audit = audit
        self.logger = logger
        self.maximum = maximum

    def notify(self, session: Any = None) -> None:
        state = self.runtime.dashboard_state
        if state:
            arguments = (
                {"meta": {"session": session.key}} if session is not None else {}
            )
            state.notify(
                notification_kinds.HEARTBEAT,
                self.notice.title,
                self.notice.body,
                **arguments,
            )

    def show(self, session: Any) -> None:
        session.append("assistant", self.notice.transcript, "msg msg-a")
        self.runtime.dashboard_state.push_sessions_update()
        self.notify(session)

    def session_delivery(self, name: str, *, prompt: bool) -> None:
        if prompt and not name:
            self.logger.debug(
                "Heartbeat prompt:dashboard: missing session name, skipping"
            )
            return
        state = self.runtime.dashboard_state
        if not state:
            self.logger.debug(
                "%sdashboard:%s ignored — no dashboard_state",
                "prompt:" if prompt else "",
                name,
            )
            return
        session = state.resolve_session(name)
        operation = "heartbeat_prompt_deliver" if prompt else "heartbeat_inject_deliver"
        resources = f"requested={name}" + (
            f",resolved={session.key}" if session else ""
        )
        self.audit().log_api_access(
            caller="heartbeat",
            operation=operation,
            outcome="approved" if session else "not_found",
            source="gateway",
            resources=resources,
        )
        if not session:
            self.logger.warning(
                "Heartbeat %s target session %s not found",
                "prompt" if prompt else "deliver",
                name,
            )
            return
        if not prompt:
            self.show(session)
            return
        from gideon.interfaces.dashboard.chat import run_chat

        text, truncated = self.notice.prompt(self.maximum)
        if truncated:
            self.logger.warning(
                "Heartbeat prompt truncated to %d bytes for session %s",
                self.maximum,
                name,
            )
        if session.enqueue_or_run_prompt(text, run_chat, state):
            state.push_sessions_update()
            self.notify(session)
        else:
            self.logger.info(
                "Heartbeat prompt queued for busy session %s (queue depth=%d)",
                session.key,
                session.queue_depth,
            )

    async def channel_delivery(self, address: str = "") -> None:
        delivery = self.runtime._channel_delivery
        if delivery is None:
            return
        try:
            channel, separator, thread = address.partition(":")
            if separator:
                await delivery.deliver_notification(
                    channel, self.notice.title, self.notice.result, thread
                )
            elif self.runtime._owner_id:
                channel = await delivery.open_dm(self.runtime._owner_id)
                if channel:
                    await delivery.deliver_notification(
                        channel, self.notice.title, self.notice.result
                    )
        except Exception:
            self.logger.exception("Heartbeat channel delivery failed")

    async def send(self, target: str) -> None:
        if target == "silent":
            self.logger.info("%s (silent): %s", self.notice.title, self.notice.summary)
            return
        for prefix, prompt in (("prompt:dashboard:", True), ("dashboard:", False)):
            if target.startswith(prefix):
                self.session_delivery(target[len(prefix) :], prompt=prompt)
                return
        await self.channel_delivery(
            target.removeprefix("channel:") if target.startswith("channel:") else ""
        )
        if target != "channel":
            self.notify()


def notification_origin(
    runtime: Any, parent: str | None, logger: Any
) -> dict[str, str] | None:
    if not parent:
        return None
    kind, separator, identifier = parent.partition(":")
    if kind == "dashboard" and separator:
        return {"session": identifier}
    if (
        not separator
        or kind in {"cron", "subagent", "hook"}
        or runtime._channel_delivery is None
    ):
        return None
    try:
        link = runtime._channel_delivery.build_thread_link(kind, identifier)
    except Exception:
        logger.debug("build_thread_link failed for %s", parent, exc_info=True)
        return None
    return {"channel_link": link} if link else None


class InboxAssembly:
    def __init__(self, runtime: Any, logger: Any) -> None:
        self.runtime = runtime
        self.logger = logger

    def poll_source(self) -> Any:
        if not self.runtime._cfg.inbox.enabled:
            return None
        try:
            from gideon.integrations.inbox_providers import get_default_provider

            return get_default_provider("filesystem")
        except Exception:
            self.logger.debug(
                "inbox: message-source provider unavailable", exc_info=True
            )
            return None

    def start(self) -> None:
        from gideon.integrations.inbox import InboxState, InboxStore
        from gideon.integrations.inbox_service import InboxService

        state, store = InboxState(), InboxStore()
        for resource in (state, store):
            resource.load()
        provider = self.poll_source()
        runtime = self.runtime
        if runtime.inbox_svc is not None:
            runtime.inbox_svc.stop()
        runtime.inbox_svc = InboxService(
            state=state,
            store=store,
            provider=provider,
            user_name=(runtime._cfg.dashboard.user_name or "").strip() or "the user",
            style_rules="\n".join(runtime._cfg.inbox.style_rules or []),
        )
        runtime.inbox_svc.start()
        self.logger.info(
            "Inbox service initialized (provider=%s)",
            provider.source_name if provider else "none",
        )
