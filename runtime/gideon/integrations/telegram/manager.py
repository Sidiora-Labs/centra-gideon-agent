"""Multiple bot connections sharing one hosted account's Telegram provider."""

from __future__ import annotations
import asyncio
from .transport import TelegramTransport, APP
from .api import TelegramError
from .policy import bot_configs
from gideon.extensions.apps.app_config import read_config
from gideon.integrations import channel_delivery
from gideon.integrations.channel_transports.base import ChannelTransportProvider


class DeliveryRouter:
    def __init__(self, manager):
        self.transport = manager

    def select(self, channel="", thread=""):
        if "/" in str(channel):
            bot, channel = str(channel).split("/", 1)
            child = self.transport.bots.get(bot)
        else:
            parts = str(thread).split(":")
            bot = parts[1] if len(parts) == 4 and parts[0] == "telegram" else "primary"
            child = self.transport.bots.get(bot)
        if child is None or not child.connected:
            raise TelegramError(503, "That Telegram bot is disconnected")
        return child.delivery, str(channel)

    def list_reply_channels(self):
        rows = []
        for key, child in self.transport.bots.items():
            if child.connected:
                rows.extend(
                    {
                        **row,
                        "id": f"{key}/{row['id']}",
                        "name": f"@{child.identity.get('username', key)} · {row['name']}",
                    }
                    for row in child.delivery.list_reply_channels()
                )
        return rows

    def is_tracked_channel(self, channel):
        try:
            delivery, channel = self.select(channel)
            return delivery.is_tracked_channel(channel)
        except TelegramError:
            return False

    def build_thread_link(self, channel, ts):
        try:
            delivery, channel = self.select(channel)
            return delivery.build_thread_link(channel, ts)
        except TelegramError:
            return ""

    async def request_approval(self, event, *, parent_session_key="", **kwargs):
        state = self.transport.services.dashboard_state
        from gideon.interfaces.dashboard.chat_utils import _history_key_for

        session = state.get_session(parent_session_key) or next(
            (
                s
                for s in state._sessions.values()
                if _history_key_for(s.key) == parent_session_key
            ),
            None,
        )
        try:
            delivery, _ = self.select(
                thread=session._channel_thread_ts if session else ""
            )
        except TelegramError:
            return None
        return await delivery.request_approval(
            event, parent_session_key=parent_session_key, **kwargs
        )

    async def start_stream(self, channel, thread_ts="", initial_text=""):
        delivery, channel = self.select(channel, thread_ts)
        mid = await delivery.start_stream(channel, thread_ts, initial_text)
        return f"{delivery.transport.slot}/{mid}" if mid else ""

    async def _stream(self, method, channel, stream, *args):
        slot, sep, mid = str(stream).partition("/")
        if not sep:
            return
        child = self.transport.bots.get(slot)
        if child is None:
            return
        channel = str(channel).split("/", 1)[-1]
        return await getattr(child.delivery, method)(channel, mid, *args)

    async def append_stream_text(self, channel, stream_ts, text):
        return await self._stream("append_stream_text", channel, stream_ts, text)

    async def append_stream_task(self, channel, stream_ts, task_id, title, status):
        return await self._stream(
            "append_stream_task", channel, stream_ts, task_id, title, status
        )

    async def stop_stream(self, channel, stream_ts):
        return await self._stream("stop_stream", channel, stream_ts)

    def __getattr__(self, name):
        thread_index = {
            "deliver_text": 1,
            "deliver_chat_mirror": 1,
            "deliver_notification": 2,
            "deliver_cron_result": 3,
            "deliver_subagent_reply": 1,
        }
        permitted = set(thread_index) | {
            "deliver_rich",
            "upload_attachment",
            "open_dm",
            "resolve_user_name",
            "resolve_user_profile",
            "channel_info",
        }
        if name not in permitted:
            raise AttributeError(name)

        async def dispatch(channel, *args, **kwargs):
            index = thread_index.get(name, 999)
            thread = kwargs.get("thread_ts", args[index] if len(args) > index else "")
            delivery, channel = self.select(channel, thread)
            return await getattr(delivery, name)(channel, *args, **kwargs)

        return dispatch


class TelegramManager(ChannelTransportProvider):
    name = "telegram"
    display_name = "Telegram"

    def __init__(self, config=None):
        self.bots = {}
        self.configs = {}
        self.services = None
        self.task = None
        self.delivery = DeliveryRouter(self)
        self.error = ""

    @property
    def connected(self):
        return any(bot.connected for bot in self.bots.values())

    def capabilities(self):
        return TelegramTransport.capabilities(self)

    async def connect(self):
        return self.connected

    async def disconnect(self):
        await self.stop_inbound()

    async def test(self):
        return await TelegramTransport().test()

    async def health(self):
        return {
            "state": (
                "ready" if self.connected else "error" if self.error else "offline"
            ),
            "detail": self.error
            or "; ".join(f"{key}: {bot.detail}" for key, bot in self.bots.items())
            or "Enable Telegram in Apps.",
        }

    async def start_inbound(self, services):
        self.services = services
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._watch())

    async def stop_inbound(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for bot in list(self.bots.values()):
            await bot.stop_inbound()
        self.bots.clear()
        if channel_delivery.raw_delivery_for("telegram") is self.delivery:
            channel_delivery.register(None, "telegram")
        from gideon.integrations.tool_providers.registry import unregister_provider

        unregister_provider("telegram")

    async def _watch(self):
        while True:
            try:
                raw = await asyncio.to_thread(read_config, APP)
                configs = bot_configs(raw)
                self.configs = configs
                for key in list(self.bots):
                    if key not in configs:
                        await self.bots.pop(key).stop_inbound()
                for key in configs:
                    if key not in self.bots:
                        bot = TelegramTransport(manager=self, slot=key)
                        self.bots[key] = bot
                        await bot.start_inbound(self.services)
                channel_delivery.register(self.delivery, "telegram")
                from gideon.integrations.tool_providers.registry import (
                    register_provider,
                )
                from .tools import TelegramTools

                register_provider(TelegramTools(self))
                self.error = ""
            except asyncio.CancelledError:
                raise
            except Exception:
                self.configs = {}
                for bot in list(self.bots.values()):
                    await bot.stop_inbound()
                self.bots.clear()
                self.error = (
                    "Telegram configuration is invalid; check the bot settings."
                )
            await asyncio.sleep(5)

    async def send(self, message):
        try:
            delivery, channel = self.delivery.select(
                message.channel_id, message.thread_id
            )
            if not delivery.is_tracked_channel(channel):
                return False
            await delivery.deliver_text(channel, message.text, message.thread_id)
            return True
        except TelegramError:
            return False
