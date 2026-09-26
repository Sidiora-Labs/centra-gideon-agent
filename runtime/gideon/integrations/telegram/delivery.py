"""Telegram replies, media, progress and owner-bound approval prompts."""

from __future__ import annotations
import asyncio
import secrets
import time
import re
import json
from pathlib import Path
from dataclasses import dataclass, field
from gideon.integrations.channel_trust import (
    is_allowed_sender,
    is_tracked_channel,
    provider_trust,
)
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from .api import TelegramError
from .format import split_text, to_markdown_v2


def safe_text(text):
    return redact_credentials(redact_exfiltration_urls(str(text))[0])[0]


def thread_options(thread):
    pieces = str(thread).split(":")
    if len(pieces) == 4 and pieces[0] == "telegram":
        pieces = [pieces[0], *pieces[2:]]
    if (
        len(pieces) == 3
        and pieces[0] == "telegram"
        and pieces[2].startswith("d")
        and pieces[2][1:].isdigit()
    ):
        return {"direct_messages_topic_id": int(pieces[2][1:])}
    if (
        len(pieces) == 3
        and pieces[0] == "telegram"
        and pieces[2].isdigit()
        and int(pieces[2])
    ):
        return {"message_thread_id": int(pieces[2])}
    return {}


@dataclass
class PendingApproval:
    chat_id: str
    message_id: int
    owner: str
    future: asyncio.Future = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class TelegramDelivery:
    def __init__(self, transport):
        self.transport = transport
        self.pending = {}
        self.streams = {}
        self.choices = {}
        self.questions = {}

    @property
    def api(self):
        if self.transport.api is None:
            raise TelegramError(503, "Telegram is disconnected")
        return self.transport.api

    def options(self, thread="", *, important=True):
        return {
            **thread_options(thread),
            "disable_notification": bool(
                self.transport.config.get("silent", False)
                or (
                    not important
                    and self.transport.config.get("notifications", "important") != "all"
                )
            ),
        }

    async def open_dm(self, user_id):
        owner = str(self.transport.config.get("owner_id", ""))
        target = str(user_id) if str(user_id).isdigit() else owner
        if not target or not is_allowed_sender("telegram", target):
            raise TelegramError(
                403, "Pair your Telegram account before sending messages."
            )
        return target

    async def deliver_text(self, channel, text, thread_ts="", **kwargs):
        if not channel or channel == "dm":
            thread_ts = thread_ts or str(self.transport.config.get("home_topic", ""))
            channel = str(
                self.transport.config.get("home_channel")
                or self.transport.config.get("owner_id")
                or ""
            )
            if not self.is_tracked_channel(channel):
                raise TelegramError(
                    403, "Choose a paired or tracked Telegram destination."
                )
        text = safe_text(text)
        options = self.options(thread_ts, important=kwargs.get("important", True))
        if (
            self.transport.config.get("rich_messages")
            and len(text) <= 32768
            and re.search(r"(?m)^\s*\||^\s*[-*] \[.\]|<details|\$\$", text)
        ):
            try:
                result = await self.api.call(
                    "sendRichMessage",
                    chat_id=channel,
                    rich_message={"markdown": text},
                    **options,
                )
                return str(result["message_id"])
            except TelegramError as exc:
                if exc.code not in (400, 404):
                    raise
        last = ""
        for part in split_text(safe_text(text)):
            rendered = to_markdown_v2(part)
            options = {
                **options,
                "link_preview_options": {
                    "is_disabled": not self.transport.config.get("link_preview", False)
                },
            }
            try:
                result = await self.api.call(
                    "sendMessage",
                    chat_id=channel,
                    text=rendered,
                    parse_mode="MarkdownV2",
                    **options,
                )
            except TelegramError as exc:
                if exc.code != 400 or not any(
                    x in str(exc).lower() for x in ("parse", "entities", "too long")
                ):
                    raise
                result = await self.api.call(
                    "sendMessage", chat_id=channel, text=part, **options
                )
            last = str(result["message_id"])
        return last

    async def deliver_rich(
        self, channel, payload, fallback_text, *, thread_ts="", **kwargs
    ):
        if isinstance(payload, dict):
            content = payload.get("rich_message", payload)
            if isinstance(content, dict) and isinstance(content.get("markdown"), str):
                try:
                    result = await self.api.call(
                        "sendRichMessage",
                        chat_id=channel,
                        rich_message={"markdown": safe_text(content["markdown"])},
                        **self.options(thread_ts),
                    )
                    return str(result["message_id"])
                except TelegramError as exc:
                    if exc.code not in (400, 404):
                        raise
        return await self.deliver_text(channel, fallback_text, thread_ts)

    async def deliver_cron_result(self, channel, job_name, job_id, text, thread_ts=""):
        return await self.deliver_text(channel, f"{job_name}\n\n{text}", thread_ts)

    async def deliver_notification(self, channel, title, text, thread_ts=""):
        return await self.deliver_text(channel, f"{title}\n\n{text}", thread_ts)

    async def deliver_chat_mirror(self, channel, text, thread_ts=""):
        from gideon.core.textfmt import extract_options

        body, options = extract_options(text)
        preview = next(
            (
                (key, value)
                for key, value in self.streams.items()
                if str(key[0]) == str(channel) and value.get("thread") == thread_ts
            ),
            None,
        )
        delivered = False
        if (
            preview
            and preview[1].get("message")
            and len(split_text(safe_text(body))) == 1
        ):
            payload = {"chat_id": channel, "message_id": preview[1]["message"]}
            if self.transport.config.get("rich_messages"):
                payload["rich_message"] = {"markdown": safe_text(body)}
            else:
                payload["text"] = to_markdown_v2(safe_text(body))
                payload["parse_mode"] = "MarkdownV2"
            try:
                await self.api.call("editMessageText", **payload)
                delivered = True
            except TelegramError as exc:
                if exc.code not in (400, 404):
                    raise
        if not delivered:
            await self.deliver_text(channel, body, thread_ts)
        if preview:
            preview[1]["finished"] = True
            if not delivered and preview[1].get("message"):
                try:
                    await self.api.call(
                        "deleteMessage",
                        chat_id=channel,
                        message_id=preview[1]["message"],
                    )
                except TelegramError:
                    pass
        owner = str(self.transport.config.get("owner_id", ""))
        if options and owner and is_allowed_sender("telegram", owner):
            self.choices = {
                k: v for k, v in self.choices.items() if v["expires"] > time.monotonic()
            }
            if len(self.choices) >= 100:
                self.choices.pop(next(iter(self.choices)))
            key = secrets.token_hex(12)
            markup = {
                "inline_keyboard": [
                    [{"text": str(value)[:64], "callback_data": f"choice:{key}:{i}"}]
                    for i, value in enumerate(options[:10])
                ]
            }
            result = await self.api.call(
                "sendMessage",
                chat_id=channel,
                text="Choose a reply:",
                reply_markup=markup,
                **self.options(thread_ts),
            )
            self.choices[key] = {
                "chat": str(channel),
                "message": result["message_id"],
                "owner": owner,
                "thread": thread_ts,
                "options": options[:10],
                "expires": time.monotonic() + 600,
            }
        if self.transport.config.get("voice_replies"):
            from gideon.integrations.tts.registry import active_voice_params
            from gideon.integrations.voice_reply import synthesize_speech

            params = active_voice_params()
            if params and params.get("enabled"):
                options = {
                    key: params[key]
                    for key in ("provider", "voice", "speed", "speech_voice")
                    if key in params
                }
                path = await synthesize_speech(**options, text=safe_text(text))
                if path:
                    try:
                        await self.api.upload(
                            "voice", channel, str(path), **self.options(thread_ts)
                        )
                    finally:
                        Path(path).unlink(missing_ok=True)

    async def deliver_subagent_reply(
        self, channel, text, thread_ts="", elapsed_secs=0.0
    ):
        return await self.deliver_text(channel, text, thread_ts)

    async def resolve_user_name(self, user_id):
        profile = await self.resolve_user_profile(user_id)
        return profile.get("name") or str(user_id)

    async def resolve_user_profile(self, user_id):
        try:
            chat = await self.api.call("getChat", chat_id=user_id)
            return {
                "id": str(user_id),
                "name": " ".join(
                    v for v in (chat.get("first_name"), chat.get("last_name")) if v
                )
                or chat.get("title", ""),
                "username": chat.get("username", ""),
            }
        except TelegramError:
            return {"id": str(user_id)}

    async def channel_info(self, channel_id):
        item = await self.api.call("getChat", chat_id=channel_id)
        return {
            "name": item.get("title") or item.get("first_name") or str(channel_id),
            "is_im": item.get("type") == "private",
        }

    def list_reply_channels(self):
        trust = provider_trust("telegram")
        return [
            {"id": item["sender_id"], "name": item["name"] or item["sender_id"]}
            for item in trust["allowed_senders"]
        ] + [
            {"id": item["channel_id"], "name": item["name"] or item["channel_id"]}
            for item in trust["tracked_channels"]
        ]

    def is_tracked_channel(self, channel_id):
        return is_tracked_channel("telegram", str(channel_id)) or is_allowed_sender(
            "telegram", str(channel_id)
        )

    def build_thread_link(self, channel, ts):
        if str(channel).startswith("-100") and str(ts).isdigit():
            return f"https://t.me/c/{str(channel)[4:]}/{ts}"
        return ""

    async def upload_attachment(
        self,
        channel,
        file_path,
        *,
        filename="",
        thread_ts="",
        title="",
        initial_comment="",
    ):
        suffix = Path(file_path).suffix.lower()
        kind = {
            ".jpg": "photo",
            ".jpeg": "photo",
            ".png": "photo",
            ".webp": "photo",
            ".gif": "animation",
            ".mp4": "video",
            ".ogg": "voice",
            ".mp3": "audio",
            ".m4a": "audio",
        }.get(suffix, "document")
        result = await self.api.upload(
            kind,
            channel,
            file_path,
            caption=safe_text(initial_comment or title)[:1000],
            **self.options(thread_ts),
        )
        return str(result["message_id"])

    async def start_stream(self, channel, thread_ts="", initial_text=""):
        mode = self.transport.config.get("streaming", "edit")
        if mode == "off":
            return ""
        draft = mode in ("auto", "draft") and str(channel).isdigit()
        mid = (
            "draft:" + secrets.token_hex(8)
            if draft
            else await self.deliver_text(
                channel, initial_text or "Working…", thread_ts, important=False
            )
        )
        state = {
            "time": 0.0,
            "lines": {},
            "text": initial_text or "Working…",
            "thread": thread_ts,
            "draft": secrets.randbelow(2**31 - 1) + 1 if draft else None,
            "message": None if draft else int(mid),
            "finished": False,
        }
        self.streams[(channel, mid)] = state
        if draft:
            await self._flush(channel, mid, state)
        return mid

    async def append_stream_task(self, channel, stream_ts, task_id, title, status):
        state = self.streams.get((channel, stream_ts))
        if state is None:
            return
        state["lines"][task_id] = f"{status}: {safe_text(title)}"
        state["text"] = "\n".join(state["lines"].values())[-3500:]
        if time.monotonic() - state["time"] >= 1.2:
            await self._flush(channel, stream_ts, state)

    async def append_stream_text(self, channel, stream_ts, text):
        state = self.streams.get((channel, stream_ts))
        if state is None:
            return
        state["text"] = split_text(safe_text(text))[-1] if text else "Working…"
        if time.monotonic() - state["time"] >= 1.2:
            await self._flush(channel, stream_ts, state)

    async def _flush(self, channel, mid, state):
        if state["finished"]:
            return
        state["time"] = time.monotonic()
        if state["draft"]:
            rich = self.transport.config.get("rich_drafts", False)
            try:
                await self.api.call(
                    "sendRichMessageDraft" if rich else "sendMessageDraft",
                    chat_id=channel,
                    draft_id=state["draft"],
                    **thread_options(state["thread"]),
                    **(
                        {"rich_message": {"markdown": state["text"]}}
                        if rich
                        else {"text": state["text"]}
                    ),
                )
                return
            except TelegramError:
                state["draft"] = None
                state["message"] = int(
                    await self.deliver_text(
                        channel, state["text"], state["thread"], important=False
                    )
                )
                return
        try:
            await self.api.call(
                "editMessageText",
                chat_id=channel,
                message_id=state["message"],
                text=state["text"],
            )
        except TelegramError as exc:
            if exc.code not in (400, 429):
                raise

    async def stop_stream(self, channel, stream_ts):
        state = self.streams.pop((channel, stream_ts), None)
        if state and not state["finished"]:
            await self._flush(channel, stream_ts, state)

    async def resolve_text_reply(self, cm, raw):
        reply_id = (raw.get("reply_to_message") or {}).get("message_id")
        owner = str(self.transport.config.get("owner_id", ""))
        if cm.sender != owner or not is_allowed_sender("telegram", owner):
            return False
        for pending in self.pending.values():
            if (
                pending.chat_id == cm.channel_id
                and pending.message_id == reply_id
                and not pending.future.done()
            ):
                answer = cm.text.split("\n", 1)[0].strip().casefold()
                if answer in ("yes", "y", "no", "n"):
                    pending.future.set_result(
                        "approved" if answer in ("yes", "y") else "rejected"
                    )
                    return True
        for question in self.questions.values():
            if (
                question["owner"] == cm.sender
                and question["chat"] == cm.channel_id
                and question["thread"] == cm.thread_id
                and not question["future"].done()
                and (question["typed"] or reply_id == question["message"])
            ):
                question["future"].set_result(
                    cm.text.split("\n\n[Quoted message]", 1)[0]
                )
                return True
        return False

    async def clarify(self, channel, thread, question, options=(), timeout=600):
        owner = str(self.transport.config.get("owner_id", ""))
        if not is_allowed_sender("telegram", owner):
            raise TelegramError(
                403, "Pair the Telegram owner before asking a question."
            )
        key = secrets.token_hex(12)
        values = [safe_text(value) for value in options[:10]]
        buttons = [
            [{"text": value[:64], "callback_data": f"clarify:{key}:{i}"}]
            for i, value in enumerate(values)
        ]
        buttons.append(
            [{"text": "Other (type answer)", "callback_data": f"clarify:{key}:other"}]
        )
        message = await self.api.call(
            "sendMessage",
            chat_id=channel,
            text=safe_text(question),
            reply_markup=(
                {"inline_keyboard": buttons} if values else {"force_reply": True}
            ),
            **self.options(thread),
        )
        pending = {
            "chat": str(channel),
            "thread": thread,
            "message": message["message_id"],
            "owner": owner,
            "options": values,
            "typed": not values,
            "future": asyncio.get_running_loop().create_future(),
        }
        self.questions[key] = pending
        try:
            return await asyncio.wait_for(pending["future"], max(1, min(timeout, 3600)))
        except asyncio.TimeoutError:
            return "No answer received before the question expired."
        finally:
            self.questions.pop(key, None)
            try:
                await self.api.call(
                    "editMessageReplyMarkup",
                    chat_id=channel,
                    message_id=pending["message"],
                    reply_markup={"inline_keyboard": []},
                )
            except TelegramError:
                pass

    async def request_approval(
        self, event, *, source, parent_session_key="", sessions=None, on_prompted=None
    ):
        owner = str(self.transport.config.get("owner_id", ""))
        if not owner or not is_allowed_sender("telegram", owner):
            return None
        key = secrets.token_hex(12)
        title = safe_text(getattr(event, "title", "Action"))[:500]
        details = getattr(event, "tool_input", None)
        if details:
            title += (
                "\n\n"
                + safe_text(
                    details
                    if isinstance(details, str)
                    else json.dumps(details, ensure_ascii=False)
                )[:2500]
            )
        markup = {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": f"approve:{key}"},
                    {"text": "Deny", "callback_data": f"deny:{key}"},
                ]
            ]
        }
        msg = await self.api.call(
            "sendMessage",
            chat_id=owner,
            text=f"{source}: {title}\nAllow this action?",
            reply_markup=markup,
        )
        pending = PendingApproval(
            chat_id=owner, message_id=int(msg["message_id"]), owner=owner
        )
        self.pending[key] = pending
        if on_prompted:
            on_prompted(pending)
        try:
            result = await asyncio.wait_for(pending.future, 7200)
            return result == "approved"
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(key, None)
            try:
                await self.api.call(
                    "editMessageReplyMarkup",
                    chat_id=owner,
                    message_id=pending.message_id,
                    reply_markup={"inline_keyboard": []},
                )
            except TelegramError:
                pass

    def authorize_callback(self, cq):
        action, _, key = str(cq.get("data", "")).partition(":")
        pending = self.pending.get(key)
        message = cq.get("message") or {}
        sender = cq.get("from") or {}
        allowed = (
            action in ("approve", "deny")
            and pending is not None
            and not pending.future.done()
            and not sender.get("is_bot")
            and str(sender.get("id", "")) == pending.owner
            and str(message.get("chat", {}).get("id", "")) == pending.chat_id
            and message.get("message_id") == pending.message_id
            and is_allowed_sender("telegram", pending.owner)
        )
        if allowed:
            pending.future.set_result("approved" if action == "approve" else "rejected")
        return bool(allowed)

    async def resolve_callback(self, cq):
        allowed = self.authorize_callback(cq)
        parts = str(cq.get("data", "")).split(":")
        if len(parts) == 3 and parts[0] == "clarify":
            question = self.questions.get(parts[1])
            sender = cq.get("from") or {}
            message = cq.get("message") or {}
            if (
                question
                and not question["future"].done()
                and not sender.get("is_bot")
                and str(sender.get("id")) == question["owner"]
                and is_allowed_sender("telegram", question["owner"])
                and str(message.get("chat", {}).get("id")) == question["chat"]
                and message.get("message_id") == question["message"]
            ):
                if parts[2] == "other":
                    question["typed"] = True
                    allowed = True
                elif parts[2].isdigit() and int(parts[2]) < len(question["options"]):
                    question["future"].set_result(question["options"][int(parts[2])])
                    allowed = True
        if len(parts) == 3 and parts[0] == "choice":
            choice = self.choices.get(parts[1])
            sender = cq.get("from") or {}
            message = cq.get("message") or {}
            if (
                choice
                and choice["expires"] > time.monotonic()
                and not sender.get("is_bot")
                and str(sender.get("id", "")) == choice["owner"]
                and is_allowed_sender("telegram", choice["owner"])
                and str(message.get("chat", {}).get("id", "")) == choice["chat"]
                and message.get("message_id") == choice["message"]
                and parts[2].isdigit()
                and int(parts[2]) < len(choice["options"])
            ):
                from gideon.integrations.channel_transports.base import ChannelMessage

                cm = ChannelMessage(
                    channel_id=choice["chat"],
                    sender=choice["owner"],
                    thread_id=choice["thread"],
                    message_id="callback:" + str(cq["id"]),
                    text=str(choice["options"][int(parts[2])]),
                )
                await self.transport.services.deliver_channel_inbound(
                    "telegram",
                    cm,
                    is_dm=message.get("chat", {}).get("type") == "private",
                )
                self.choices.pop(parts[1], None)
                allowed = True
        await self.api.call(
            "answerCallbackQuery",
            callback_query_id=cq["id"],
            text="Recorded" if allowed else "This action is unavailable.",
        )

    def close(self):
        for pending in self.pending.values():
            if not pending.future.done():
                pending.future.set_result("rejected")
        for question in self.questions.values():
            if not question["future"].done():
                question["future"].set_result(
                    "Telegram disconnected before an answer was received."
                )
        self.questions.clear()
        self.pending.clear()
        self.choices.clear()
        self.streams.clear()
