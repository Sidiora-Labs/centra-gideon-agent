import asyncio
import hashlib
import json
import math
import re
from datetime import datetime, timezone

import aiohttp

from gideon.sdk.credentials import CredentialStore

from .store import Conflict

API_BASE = "https://moltworld.fly.dev"
PROTOCOL = "moltworld-v1-2026-09-25"
ACTIONS = {
    "look": set(),
    "move": {"direction"},
    "break": {"direction"},
    "loot": set(),
    "take": {"item", "quantity"},
    "eat": {"item"},
    "attack": {"direction"},
    "speak": {"message"},
    "whisper": {"target", "message"},
    "drop": {"item", "quantity"},
    "withdraw": {"amount"},
    "respawn": set(),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _text(value, label, limit=100):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > limit
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"{label} must contain 1 to {limit} visible characters")
    return value.strip()


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        raise ValueError("Invalid request identifier")
    return value


class RemoteError(Conflict):
    def __init__(self, message, status=502, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


class Moltworld:
    def __init__(self, store, credential_resolver=None, base_url=API_BASE):
        self.store, self.home, self.base_url = (
            store,
            store.path.parent.parent,
            base_url.rstrip("/"),
        )
        self.credentials = (
            CredentialStore(self.home) if credential_resolver is None else None
        )
        self.credential_resolver = credential_resolver
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS moltworld_config(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS moltworld_actions(request_id TEXT PRIMARY KEY, input TEXT NOT NULL, body TEXT NOT NULL);
            """)

    def config(self):
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM moltworld_config WHERE id=1").fetchone()
        return (
            json.loads(row[0])
            if row
            else {"enabled": False, "credential_name": "", "revision": 0}
        )

    def configure(self, body):
        if (
            not isinstance(body, dict)
            or set(body) != {"enabled", "credential_name", "revision"}
            or type(body["enabled"]) is not bool
            or type(body["revision"]) is not int
        ):
            raise ValueError(
                "Configuration requires enabled, credential_name and revision"
            )
        name = (
            _text(body["credential_name"], "Credential name")
            if body["credential_name"]
            else ""
        )
        current = self.config()
        if current["revision"] != body["revision"]:
            raise Conflict("Moltworld configuration revision changed")
        result = {
            "enabled": body["enabled"],
            "credential_name": name,
            "revision": current["revision"] + 1,
        }
        with self.store.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO moltworld_config VALUES(1,?)",
                (_canonical(result),),
            )
        return result

    def _secret(self):
        config = self.config()
        if not config["enabled"]:
            raise Conflict("Moltworld connection is disabled")
        try:
            secret = (
                self.credential_resolver(config["credential_name"])
                if self.credential_resolver
                else self.credentials.resolve(config["credential_name"]).secret
            )
        except (KeyError, ValueError):
            secret = None
        if not isinstance(secret, str) or not secret:
            raise Conflict("Configured Moltworld credential is unavailable")
        return secret

    def readiness(self):
        config = self.config()
        available = False
        if config["credential_name"]:
            try:
                available = bool(
                    self.credential_resolver(config["credential_name"])
                    if self.credential_resolver
                    else self.credentials.resolve(config["credential_name"]).secret
                )
            except (KeyError, ValueError):
                pass
        return {
            "protocol": PROTOCOL,
            "base_url": API_BASE,
            "config": config,
            "credential_available": available,
            "remote_status": "unverified",
            "ready": bool(config["enabled"] and available),
        }

    async def _request(self, method, path, *, auth=False, body=None):
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self._secret()
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.request(
                    method,
                    self.base_url + path,
                    headers=headers,
                    json=body,
                    allow_redirects=False,
                ) as response:
                    raw = await response.content.read(262145)
                    if len(raw) > 262144:
                        raise RemoteError("Moltworld response exceeds limit")
                    try:
                        result = json.loads(raw) if raw else None
                    except (ValueError, UnicodeError):
                        raise RemoteError("Moltworld returned invalid JSON")
                    if response.status >= 400:
                        message = (
                            result.get("error") or result.get("message")
                            if isinstance(result, dict)
                            else None
                        )
                        raise RemoteError(
                            str(message or f"Moltworld HTTP {response.status}"),
                            429 if response.status == 429 else 502,
                            response.headers.get("Retry-After"),
                        )
                    return response.status, result
        except asyncio.TimeoutError as exc:
            raise RemoteError(
                "Moltworld action outcome is unknown after timeout", 504
            ) from exc
        except aiohttp.ClientError as exc:
            raise RemoteError("Moltworld is unavailable", 503) from exc

    async def status(self):
        status, result = await self._request("GET", "/api/v1/agent", auth=True)
        if status != 200 or not isinstance(result, dict):
            raise RemoteError("Moltworld agent response is incompatible")
        required = {"id", "name", "x", "y", "hp", "energy"}
        if not required <= result.keys():
            raise RemoteError("Moltworld agent response is missing required state")
        return {
            "protocol": PROTOCOL,
            "verification": "provider_verified",
            "observed_at": _now(),
            "agent": {key: result[key] for key in required},
            "inventory": result.get("inventory", []),
            "raw_fields": sorted(result.keys()),
        }

    async def observe(self, body):
        if not isinstance(body, dict) or set(body) != {"x1", "y1", "x2", "y2"}:
            raise ValueError("Observation requires x1, y1, x2 and y2")
        values = list(body.values())
        if (
            any(type(value) is not int or not 0 <= value <= 99 for value in values)
            or body["x1"] > body["x2"]
            or body["y1"] > body["y2"]
            or body["x2"] - body["x1"] >= 20
            or body["y2"] - body["y1"] >= 20
        ):
            raise ValueError(
                "Observation must be an ordered area of at most 20x20 within 0..99"
            )
        query = "?" + "&".join(f"{key}={body[key]}" for key in ("x1", "y1", "x2", "y2"))
        status, result = await self._request("GET", "/api/v1/observe" + query)
        if status != 200 or not isinstance(result, dict):
            raise RemoteError("Moltworld observation response is incompatible")
        return {
            "protocol": PROTOCOL,
            "verification": "provider_verified",
            "observed_at": _now(),
            "area": body,
            "observation": result,
        }

    def history(self):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT body FROM moltworld_actions ORDER BY rowid DESC LIMIT 200"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _params(self, action, params):
        if action not in ACTIONS or not isinstance(params, dict):
            raise ValueError("Unsupported Moltworld action")
        allowed = ACTIONS[action]
        required = allowed - ({"quantity"} if action in ("take", "drop") else set())
        if set(params) - allowed or not required <= set(params):
            raise ValueError(
                "Action parameters do not match the current Moltworld protocol"
            )
        if action in ("move", "break", "attack") and params["direction"] not in (
            "n",
            "s",
            "e",
            "w",
        ):
            raise ValueError("Direction must be n, s, e or w")
        if action in ("speak", "whisper"):
            _text(params["message"], "Message", 500)
        if action == "whisper":
            _text(params["target"], "Whisper target", 96)
        if action in ("take", "eat", "drop") and params["item"] not in (
            "gold",
            "berry",
        ):
            raise ValueError("Item must be gold or berry")
        if "quantity" in params and (
            type(params["quantity"]) is not int or params["quantity"] < 1
        ):
            raise ValueError("Quantity must be a positive integer")
        if action == "withdraw" and (
            type(params["amount"]) not in (int, float)
            or not math.isfinite(params["amount"])
            or params["amount"] <= 0
        ):
            raise ValueError("Withdrawal amount must be positive")
        return params

    async def action(self, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "action",
            "params",
            "approval",
        }:
            raise ValueError("Action requires request_id, action, params and approval")
        request_id, action, params = (
            _identifier(body["request_id"]),
            _text(body["action"], "Action", 20),
            self._params(body["action"], body["params"]),
        )
        if body["approval"] != {"approved": True, "source": "user"}:
            raise Conflict(
                "Explicit user approval is required for an external Moltworld action"
            )
        encoded = _canonical(body)
        with self.store.connection() as db:
            prior = db.execute(
                "SELECT input,body FROM moltworld_actions WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if prior:
            if prior[0] != encoded:
                raise Conflict(
                    "Moltworld request identifier was reused with different input"
                )
            return json.loads(prior[1])
        submitted_at = _now()
        try:
            status, result = await self._request(
                "POST",
                "/api/v1/action",
                auth=True,
                body={"action": action, "params": params},
            )
            if (
                status != 202
                or not isinstance(result, dict)
                or result.get("success") is not True
                or type(result.get("queuedForTick")) is not int
                or type(result.get("currentTick")) is not int
            ):
                raise RemoteError("Moltworld queue response is incompatible")
            receipt = {
                "request_id": request_id,
                "action": action,
                "params": params,
                "approval": "user_attested",
                "state": "queued_remote",
                "queued_for_tick": result["queuedForTick"],
                "current_tick": result["currentTick"],
                "submitted_at": submitted_at,
                "provider_response_sha256": hashlib.sha256(
                    _canonical(result).encode()
                ).hexdigest(),
            }
        except RemoteError as exc:
            receipt = {
                "request_id": request_id,
                "action": action,
                "params": params,
                "approval": "user_attested",
                "state": (
                    "outcome_unknown" if exc.status in (503, 504) else "refused_remote"
                ),
                "submitted_at": submitted_at,
                "error": str(exc),
                "retry_after": exc.retry_after,
            }
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO moltworld_actions VALUES(?,?,?)",
                (request_id, encoded, _canonical(receipt)),
            )
        return receipt
