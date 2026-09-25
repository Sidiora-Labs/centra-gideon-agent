from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gideon.core.config.credentials import get_credential
from gideon.core.config.loader import AppConfig
from gideon.integrations.notification_providers.base import NotificationDeliveryProvider

from . import PeopleStore
from .evidence import report
from .store import PeopleError, fields, text

CONFIG = {
    "enabled",
    "automatic_replies",
    "bot_credential_ref",
    "webhook_credential_ref",
    "allowed_chat_ids",
    "allowed_user_ids",
    "revision",
}


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS telegram_config (id INTEGER PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS telegram_updates (id INTEGER PRIMARY KEY,digest TEXT NOT NULL,receipt TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS telegram_deliveries (id TEXT PRIMARY KEY,body TEXT NOT NULL)"
    )


def config(store):
    with closing(store.connect()) as db, db:
        schema(db)
        row = db.execute(
            "SELECT body,revision FROM telegram_config WHERE id=1"
        ).fetchone()
        return (
            {**json.loads(row[0]), "revision": row[1]}
            if row
            else {
                "enabled": False,
                "automatic_replies": False,
                "bot_credential_ref": "",
                "webhook_credential_ref": "",
                "allowed_chat_ids": [],
                "allowed_user_ids": [],
                "revision": 0,
            }
        )


def configure(store, data):
    fields(data, CONFIG)
    if type(data.get("revision")) is not int or data["revision"] < 0:
        raise PeopleError("Current Telegram configuration revision is required")
    result = {}
    for key in ("enabled", "automatic_replies"):
        if type(data.get(key)) is not bool:
            raise PeopleError(key + " must be an explicit boolean")
        result[key] = data[key]
    for key in ("bot_credential_ref", "webhook_credential_ref"):
        value = text(data.get(key), key, 120, True)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise PeopleError("Use an existing credential reference")
        result[key] = value
    for key in ("allowed_chat_ids", "allowed_user_ids"):
        value = data.get(key)
        if (
            not isinstance(value, list)
            or not 1 <= len(value) <= 50
            or any(
                type(item) is not int or item == 0 or abs(item) > 2**52
                for item in value
            )
        ):
            raise PeopleError("Configure 1–50 explicit Telegram chat and user IDs")
        result[key] = sorted(set(value))
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute("SELECT revision FROM telegram_config WHERE id=1").fetchone()
        if (old[0] if old else 0) != data["revision"]:
            raise PeopleError("Telegram configuration changed; reload", 409)
        db.execute(
            "INSERT INTO telegram_config VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision",
            (json.dumps(result), data["revision"] + 1),
        )
    return {**result, "revision": data["revision"] + 1}


def command(store, value):
    value = text(value, "command", 100, True).split("@", 1)[0]
    zone = AppConfig.load().timezone or "UTC"
    state = report(store, zone)
    if value == "/people":
        result = (
            "\n".join(
                row["person"]["name"] + " (" + row["person"]["ring"] + ")"
                for row in state["people"]
            )
            or "No people recorded."
        )
    elif value == "/care":
        due = [
            row
            for row in state["people"]
            if row["care"]["state"] in ("overdue", "missing")
        ]
        result = (
            "\n".join(
                row["person"]["name"] + ": " + row["care"]["state"] for row in due
            )
            or "No overdue or missing relationship touchpoints."
        )
    elif value == "/status":
        result = f"People: {len(state['people'])}. Recorded threads: {len(state['threads'])}. Thread status uses recorded evidence, not live account coverage."
    else:
        raise PeopleError("Supported operational commands: /people, /care, /status")
    return {
        "command": value,
        "text": result[:4096],
        "timezone": zone,
        "qualification": "local_runtime_projection",
    }


def queue(store, data):
    fields(data, {"request_key", "chat_id", "text"})
    settings = config(store)
    chat_id = data.get("chat_id")
    if type(chat_id) is not int or chat_id not in settings["allowed_chat_ids"]:
        raise PeopleError("Telegram addressee is not allowed", 403)
    body = {
        "chat_id": chat_id,
        "text": text(data.get("text"), "text", 4096, True),
        "request_key": text(data.get("request_key"), "request_key", 300, True),
    }
    identity = hashlib.sha256(body["request_key"].encode()).hexdigest()
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        previous = db.execute(
            "SELECT body FROM telegram_deliveries WHERE id=?", (identity,)
        ).fetchone()
        if previous:
            old = json.loads(previous[0])
            if any(old[key] != value for key, value in body.items()):
                raise PeopleError("Telegram request identity conflicts", 409)
            return old, False
        body.update(
            id=identity,
            config_revision=settings["revision"],
            state="queued",
            created_at=datetime.now(timezone.utc).isoformat(),
            message_id=None,
        )
        db.execute(
            "INSERT INTO telegram_deliveries VALUES (?,?)", (identity, json.dumps(body))
        )
    return body, True


def deliveries(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM telegram_deliveries ORDER BY rowid DESC"
            )
        ]


def receive(store, update, secret):
    settings = config(store)
    expected = (
        get_credential(settings["webhook_credential_ref"])
        if settings["webhook_credential_ref"]
        else None
    )
    if (
        not settings["enabled"]
        or not expected
        or not isinstance(secret, str)
        or not hmac.compare_digest(expected, secret)
    ):
        raise PeopleError("Telegram webhook is not authorized", 403)
    if (
        not isinstance(update, dict)
        or type(update.get("update_id")) is not int
        or update["update_id"] < 0
    ):
        raise PeopleError("Invalid Telegram update identity")
    message = update.get("message")
    if (
        not isinstance(message, dict)
        or not isinstance(message.get("chat"), dict)
        or not isinstance(message.get("from"), dict)
    ):
        raise PeopleError("Only Telegram text message updates are supported")
    chat_id, user_id = message["chat"].get("id"), message["from"].get("id")
    if (
        type(chat_id) is not int
        or type(user_id) is not int
        or chat_id not in settings["allowed_chat_ids"]
        or user_id not in settings["allowed_user_ids"]
        or message["from"].get("is_bot")
    ):
        raise PeopleError("Telegram chat or sender is not allowed", 403)
    digest = hashlib.sha256(json.dumps(update, sort_keys=True).encode()).hexdigest()
    with closing(store.connect()) as db, db:
        schema(db)
        old = db.execute(
            "SELECT digest,receipt FROM telegram_updates WHERE id=?",
            (update["update_id"],),
        ).fetchone()
        if old:
            if old[0] != digest:
                raise PeopleError("Telegram update identity conflicts", 409)
            return json.loads(old[1]), False
    result = command(store, message.get("text"))
    request_key = "telegram-update:" + str(update["update_id"])
    identity = hashlib.sha256(request_key.encode()).hexdigest()
    delivery = {
        "id": identity,
        "request_key": request_key,
        "chat_id": chat_id,
        "text": result["text"],
        "config_revision": settings["revision"],
        "state": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message_id": None,
    }
    receipt = {
        "update_id": update["update_id"],
        "chat_id": chat_id,
        "command": result["command"],
        "delivery_id": identity,
        "result": result,
        "automatic_replies": settings["automatic_replies"],
    }
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT digest,receipt FROM telegram_updates WHERE id=?",
            (update["update_id"],),
        ).fetchone()
        if old:
            if old[0] != digest:
                raise PeopleError("Telegram update identity conflicts", 409)
            return json.loads(old[1]), False
        current = db.execute(
            "SELECT revision FROM telegram_config WHERE id=1"
        ).fetchone()
        if current[0] != settings["revision"]:
            raise PeopleError("Telegram authorization changed during processing", 409)
        if db.execute(
            "SELECT id FROM telegram_deliveries WHERE id=?", (identity,)
        ).fetchone():
            raise PeopleError("Telegram response identity already exists", 409)
        db.execute(
            "INSERT INTO telegram_deliveries VALUES (?,?)",
            (identity, json.dumps(delivery)),
        )
        db.execute(
            "INSERT INTO telegram_updates VALUES (?,?,?)",
            (update["update_id"], digest, json.dumps(receipt)),
        )
    return receipt, True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def deliver(store, delivery_id):
    settings = config(store)
    if not settings["enabled"]:
        raise PeopleError("Telegram delivery is disabled", 409)
    token = (
        get_credential(settings["bot_credential_ref"])
        if settings["bot_credential_ref"]
        else None
    )
    if not token:
        raise PeopleError("Telegram bot credential is unavailable", 503)
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise PeopleError("Telegram bot credential has an invalid format", 503)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        found = db.execute(
            "SELECT body FROM telegram_deliveries WHERE id=?", (delivery_id,)
        ).fetchone()
        if not found:
            raise PeopleError("Telegram delivery not found", 404)
        row = json.loads(found[0])
        if row["state"] == "accepted":
            return row
        if (
            row["state"] != "queued"
            or row["config_revision"] != settings["revision"]
            or row["chat_id"] not in settings["allowed_chat_ids"]
        ):
            raise PeopleError(
                "Delivery state or connection changed; automatic retries are forbidden",
                409,
            )
        row["state"] = "sending"
        db.execute(
            "UPDATE telegram_deliveries SET body=? WHERE id=?",
            (json.dumps(row), delivery_id),
        )
    try:
        request = Request(
            "https://api.telegram.org/bot" + token + "/sendMessage",
            data=json.dumps({"chat_id": row["chat_id"], "text": row["text"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError()
        result = json.loads(raw)
        message = result.get("result")
        if (
            result.get("ok") is not True
            or not isinstance(message, dict)
            or message.get("chat", {}).get("id") != row["chat_id"]
            or type(message.get("message_id")) is not int
        ):
            raise ValueError()
        row.update(
            state="accepted",
            message_id=message["message_id"],
            qualification="telegram_api_accepted_not_recipient_read",
        )
    except (HTTPError, URLError, OSError, ValueError, TypeError, AttributeError):
        row.update(
            state="unknown", error="Telegram outcome is unknown; no automatic resend"
        )
    with closing(store.connect()) as db, db:
        db.execute(
            "UPDATE telegram_deliveries SET body=? WHERE id=?",
            (json.dumps(row), delivery_id),
        )
    return row


class TelegramNotifications(NotificationDeliveryProvider):
    @property
    def delivery_name(self):
        return "gideon-telegram-ops"

    def can_reach(self, addressee):
        if not isinstance(addressee, str) or not re.fullmatch(
            r"telegram:-?[0-9]+", addressee
        ):
            return False
        settings = config(PeopleStore())
        return (
            settings["enabled"]
            and int(addressee.split(":", 1)[1]) in settings["allowed_chat_ids"]
        )

    def deliver(self, notification):
        if not self.can_reach(notification.get("addressee")):
            raise PeopleError("Telegram notification addressee is not enabled", 403)
        content = "\n".join(
            str(notification.get(key) or "") for key in ("title", "body")
        ).strip()
        key = str(
            notification.get("id")
            or hashlib.sha256(
                json.dumps(notification, sort_keys=True).encode()
            ).hexdigest()
        )
        store = PeopleStore()
        row, _ = queue(
            store,
            {
                "request_key": "notification:" + key,
                "chat_id": int(notification["addressee"].split(":", 1)[1]),
                "text": content,
            },
        )
        result = deliver(store, row["id"])
        if result["state"] != "accepted":
            raise PeopleError("Telegram notification delivery is not confirmed", 503)


def create_notification_provider(config=None):
    return TelegramNotifications()
