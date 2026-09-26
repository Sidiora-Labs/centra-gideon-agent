"""Persistent Telegram topic ownership and conversation restoration."""

from __future__ import annotations
import json
from pathlib import Path
from gideon.core.atomic_write import atomic_write
from .api import TelegramError


class TopicStore:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except FileNotFoundError:
            self.data = {"modes": {}, "sessions": {}, "topics": {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(self.data), mode=0o600)

    def enabled(self, chat):
        return bool(self.data["modes"].get(str(chat)))

    def remember(self, cm, session):
        record = self.data["sessions"].get(session.key)
        if record is None:
            self.data["sessions"][session.key] = {
                "chat": cm.channel_id,
                "owner": cm.sender,
                "thread": cm.thread_id,
            }
            self.save()

    def restorable(self, state, chat, sender):
        result = []
        for key, record in self.data["sessions"].items():
            session = state.get_session(key)
            if (
                session is not None
                and record["chat"] == str(chat)
                and record["owner"] == str(sender)
                and not session._channel_linked
            ):
                result.append(session)
        return result

    def restore(self, state, cm, key):
        record = self.data["sessions"].get(key)
        session = state.get_session(key)
        if (
            record is None
            or session is None
            or record["owner"] != cm.sender
            or record["chat"] != cm.channel_id
        ):
            raise TelegramError(
                403, "That conversation does not belong to you in this chat."
            )
        current = state.get_linked_session(cm.thread_id)
        if session.running or (current and current.running):
            raise TelegramError(
                409, "Stop the current response before restoring a conversation."
            )
        if session._channel_linked and session._channel_thread_ts != cm.thread_id:
            raise TelegramError(
                409, "That conversation is already linked to another topic."
            )
        state.link_channel(session.key, cm.thread_id, cm.channel_id)
        record["thread"] = cm.thread_id
        self.save()
        return session

    async def configure(self, transport):
        for chat in transport.config.get("dm_topics", []):
            cid = str(chat["chat_id"])
            if not transport.delivery.is_tracked_channel(cid):
                continue
            for topic in chat.get("topics", []):
                key = cid + ":" + str(topic["name"])
                if key in self.data["topics"]:
                    updated = {
                        **self.data["topics"][key],
                        **{k: v for k, v in topic.items() if k != "thread_id"},
                    }
                    if updated != self.data["topics"][key]:
                        self.data["topics"][key] = updated
                        self.save()
                    continue
                if topic.get("thread_id"):
                    self.data["topics"][key] = {
                        **topic,
                        "chat": cid,
                        "thread_id": int(topic["thread_id"]),
                    }
                    self.save()
                    continue
                if key in transport._skipped_topics:
                    continue
                args = {
                    k: topic[k]
                    for k in ("name", "icon_color", "icon_custom_emoji_id")
                    if k in topic
                }
                try:
                    created = await transport.api.call(
                        "createForumTopic", chat_id=cid, **args
                    )
                except TelegramError as exc:
                    if exc.code in (400, 403):
                        transport._skipped_topics.add(key)
                        continue
                    raise
                self.data["topics"][key] = {
                    **topic,
                    "chat": cid,
                    "thread_id": created["message_thread_id"],
                }
                self.save()

    async def command(self, transport, cm, argument):
        state = transport.services.dashboard_state
        current = state.get_linked_session(cm.thread_id)
        root = cm.metadata.get("topic", "0") in ("0", "1")
        if argument == "help":
            return "/topic enables parallel DM conversations. Create topics with All Messages. /topic <session-id> restores an unlinked conversation inside a topic. /topic off disables the lobby. /new resets only the current topic."
        if cm.metadata.get("chat_type") != "private":
            return "Use /topic in your private chat. Group forum topics already have separate conversations."
        if argument == "off":
            if not root:
                return "Run /topic off in the root chat."
            owned = [
                state.get_session(key)
                for key, record in self.data["sessions"].items()
                if record["chat"] == cm.channel_id and record["owner"] == cm.sender
            ]
            if any(session and session.running for session in owned):
                return (
                    "Stop running responses in your topics before disabling topic mode."
                )
            from gideon.interfaces.dashboard.chat_utils import _history_key_for

            for session in owned:
                if not session or not session._channel_thread_ts.startswith(
                    cm.thread_id.rsplit(":", 1)[0] + ":"
                ):
                    continue
                state._channel_to_session.pop(session._channel_thread_ts, None)
                session._channel_linked = False
                session._channel_thread_ts = ""
                session._channel_id = ""
                if state.sessions:
                    state.sessions.set_channel_link(
                        _history_key_for(session.key), "", ""
                    )
            self.data["modes"].pop(cm.channel_id, None)
            self.save()
            state.push_sessions_update()
            return "Topic mode disabled and bindings cleared. Conversation history and Telegram topics are preserved; /sessions lists conversations you can restore."
        if argument:
            if root:
                return "Open a topic before restoring a conversation."
            restored = self.restore(state, cm, argument)
            last = next(
                (
                    m.get("content", "")
                    for m in reversed(restored.messages)
                    if m.get("role") == "assistant"
                ),
                "",
            )
            return f"Restored {restored.title} ({restored.key}).\n\n{last}"
        if not root:
            return f"Conversation: {current.key if current else 'starts with your next message'}. /new starts fresh in this topic."
        if not self.enabled(cm.channel_id):
            me = await transport.api.call("getMe")
            if not me.get("has_topics_enabled") or not me.get(
                "allows_users_to_create_topics"
            ):
                return "In @BotFather, open Bot Settings → Threads Settings. Enable Threaded Mode and allow users to create topics, then send /topic again."
            self.data["modes"][cm.channel_id] = cm.sender
            self.save()
            try:
                topic = await transport.api.call(
                    "createForumTopic", chat_id=cm.channel_id, name="System"
                )
                msg = await transport.api.call(
                    "sendMessage",
                    chat_id=cm.channel_id,
                    message_thread_id=topic["message_thread_id"],
                    text="Topic controls: /status, /sessions, /help. Create another topic for each conversation.",
                )
                await transport.api.call(
                    "pinChatMessage",
                    chat_id=cm.channel_id,
                    message_id=msg["message_id"],
                    disable_notification=True,
                )
            except TelegramError:
                pass
        sessions = self.restorable(state, cm.channel_id, cm.sender)
        return (
            "Topic mode enabled. Use All Messages to start another conversation.\n"
            + "\n".join(f"{s.key}: {s.title}" for s in sessions[-20:])
        )

    def skill(self, chat, topic):
        return next(
            (
                str(item.get("skill", ""))
                for item in self.data["topics"].values()
                if item["chat"] == str(chat) and str(item["thread_id"]) == str(topic)
            ),
            "",
        )

    async def rename(self, transport, cm, session):
        if (
            transport.config.get("disable_topic_auto_rename")
            or not self.enabled(cm.channel_id)
            or cm.metadata.get("topic", "0") in ("0", "1")
            or not session.title
            or session.title == session.key
        ):
            return
        for item in self.data["topics"].values():
            if (
                item["chat"] == cm.channel_id
                and str(item["thread_id"]) == cm.metadata["topic"]
            ):
                return
        await transport.api.call(
            "editForumTopic",
            chat_id=cm.channel_id,
            message_thread_id=int(cm.metadata["topic"]),
            name=session.title[:128],
        )
