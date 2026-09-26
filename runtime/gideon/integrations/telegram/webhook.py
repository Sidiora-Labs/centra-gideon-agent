"""Secret-verified webhook ingress; hosted traffic also requires the signed edge hop."""

import hmac
import json
from aiohttp import web
from gideon.integrations.channel_delivery import delivery_for
from .api import TelegramError


async def receive_webhook(request):
    delivery = delivery_for("telegram")
    if delivery is None or not delivery.transport.connected:
        raise web.HTTPServiceUnavailable()
    transport = delivery.transport
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if hasattr(transport, "bots"):
        transport = next(
            (
                bot
                for bot in transport.bots.values()
                if bot.connected
                and bot.config.get("webhook_secret")
                and hmac.compare_digest(str(bot.config["webhook_secret"]), received)
            ),
            None,
        )
        if transport is None:
            raise web.HTTPUnauthorized()
    expected = str(transport.config.get("webhook_secret", ""))
    if (
        transport.config.get("transport", "polling") != "webhook"
        or not expected
        or not hmac.compare_digest(expected, received)
    ):
        raise web.HTTPUnauthorized()
    if request.content_length and request.content_length > 1024 * 1024:
        raise web.HTTPRequestEntityTooLarge(
            max_size=1024 * 1024, actual_size=request.content_length
        )
    body = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise web.HTTPRequestEntityTooLarge(
                max_size=1024 * 1024, actual_size=len(body)
            )
    try:
        update = json.loads(body)
        if not isinstance(update, dict) or type(update.get("update_id")) is not int:
            raise ValueError()
    except (ValueError, TypeError):
        raise web.HTTPBadRequest() from None
    async with transport._webhook_lock:
        if update["update_id"] in transport._webhook_seen:
            return web.json_response({"ok": True})
        from gideon.core.atomic_write import atomic_write

        path = transport._inbox / f"{update['update_id']:020d}.json"
        atomic_write(path, json.dumps(update), mode=0o600)
        try:
            await transport._dispatch(update, path)
        except TelegramError:
            raise web.HTTPServiceUnavailable() from None
        transport._webhook_seen.append(update["update_id"])
        atomic_write(
            transport._webhook_seen_path,
            json.dumps(list(transport._webhook_seen)),
            mode=0o600,
        )
    return web.json_response({"ok": True})
