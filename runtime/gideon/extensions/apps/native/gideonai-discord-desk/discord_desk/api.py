"""Discord REST client — raw wire protocol over ``httpx`` (no vendor SDK).

The bundle speaks Discord's HTTP API directly on ``httpx`` (already a core
dependency) rather than importing a third-party Discord library. This module is the
whole REST surface the transport and delivery need: typed thin wrappers for the
gateway handshake, message create/edit, DM open, multipart upload, channel/user
lookup, reactions, the typing indicator, and the interaction callback a button press
must be answered with.

The one piece of real logic Discord forces on every caller is **per-bucket** rate
limiting, and it is the single biggest difference from a flat ``retry_after`` API
like Telegram's:

* every response carries ``X-RateLimit-Bucket`` (an opaque hash), plus
  ``X-RateLimit-Remaining`` and ``X-RateLimit-Reset-After`` for THAT bucket;
* a bucket covers one route *including its major parameter* — posting to channel A
  and channel B are separate budgets — so a client that tracks one global counter
  either stalls needlessly or eats 429s;
* a 429 is either **per-route** (only its bucket is gated) or **global**
  (``X-RateLimit-Global`` / the body's ``global`` flag: EVERY request is gated).
  Treating a global limit as per-route is the failure that gets a bot cloudflare-
  banned, so the two are tracked separately here.

So :class:`DiscordDeskHttpApi` keeps a bucket-state map and waits *before* issuing a
call whose bucket is exhausted, rather than spending a 429 to learn what the previous
response already told it.

A :class:`DiscordDeskApi` ABC lets the transport/delivery/tests swap a fake in
without touching the network; :class:`DiscordDeskHttpApi` is the real ``httpx``-
backed implementation. Its ``httpx`` client, sleep function AND clock are
injectable, so the bucket logic is exercised end-to-end in tests with an
``httpx.MockTransport``, a recording sleep, and a fake monotonic clock — no live
Discord, no wall-clock wait.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# API v10 is the current REST version; the gateway is pinned to the same version
# in gateway.py so payload shapes can't drift between the two halves.
API_BASE = "https://discord.com/api/v10"

# Discord asks every bot to identify itself in the User-Agent and treats clients
# that don't as suspect (harsher rate limits, occasional cloudflare challenges).
USER_AGENT = "DiscordBot (https://github.com/Sidiora-labs/GideonAppsApps, 0.1.0)"

# Ceiling on a server-suggested wait we will actually honor. Discord can return a
# multi-hour retry_after for a cloudflare-level ban; sleeping that inside a request
# would wedge the caller (and the gateway task awaiting it), so cap and let the
# retry budget surface the error instead.
MAX_RETRY_AFTER = 60

# Discord's own message-content ceiling for a single message (v10). The delivery
# layer splits on this; capabilities() reports it.
DISCORD_DESK_MAX_TEXT = 2000

# Interaction callback types (Discord "Interaction Response Object"). 6 =
# DEFERRED_UPDATE_MESSAGE: acknowledge a component press WITHOUT editing the
# message, which is what an approval button needs — we edit the prompt ourselves
# once the decision resolves.
INTERACTION_CALLBACK_DEFERRED_UPDATE = 6

# Message component types / button styles (Discord "Message Components").
COMPONENT_ACTION_ROW = 1
COMPONENT_BUTTON = 2
BUTTON_STYLE_SUCCESS = 3
BUTTON_STYLE_DANGER = 4


class DiscordDeskApiError(Exception):
    """A REST call failed, or exhausted its retries.

    ``status`` is the HTTP status; ``code`` mirrors Discord's numeric JSON error
    code when the body carries one (e.g. ``50001 Missing Access``); ``message`` is
    the human-readable reason."""

    def __init__(
        self, message: str, *, status: int = 0, code: int = 0, route: str = ""
    ) -> None:
        self.message = message
        self.status = status
        self.code = code
        self.route = route
        super().__init__(f"{route or 'discord'}: {message} (status={status} code={code})")


class DiscordDeskApi(ABC):
    """The REST surface this app needs on Discord. Swap a fake in for tests."""

    @abstractmethod
    async def get_gateway_bot(self) -> dict[str, Any]:
        """``GET /gateway/bot`` — the bot's gateway URL + shard/session-start limits.

        This is the cheapest authenticated call Discord offers, so it doubles as the
        "gateway hello" probe the Channels-page Test action and the doctor point at:
        it proves the token is valid AND that a gateway connection is permitted."""

    @abstractmethod
    async def create_message(
        self, channel_id: str, content: str, *,
        components: list[dict[str, Any]] | None = None,
        message_reference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``POST /channels/{id}/messages`` — returns the created message object."""

    @abstractmethod
    async def edit_message(
        self, channel_id: str, message_id: str, content: str, *,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """``PATCH /channels/{id}/messages/{id}`` — edit a sent message.

        Passing ``components=[]`` REMOVES the components; that is how a resolved
        approval prompt drops its now-stale buttons."""

    @abstractmethod
    async def create_dm(self, user_id: str) -> dict[str, Any]:
        """``POST /users/@me/channels`` — open (or resolve) the DM channel with a user.

        Unlike Telegram, a Discord user id is NOT a channel id: a DM has its own
        channel id that must be opened once and can then be posted to."""

    @abstractmethod
    async def upload_file(
        self, channel_id: str, file_path: str, *, filename: str = "", content: str = "",
    ) -> dict[str, Any]:
        """Multipart ``POST /channels/{id}/messages`` — attach a file to a message."""

    @abstractmethod
    async def get_channel(self, channel_id: str) -> dict[str, Any]:
        """``GET /channels/{id}`` — the channel object (name, type, guild_id)."""

    @abstractmethod
    async def get_user(self, user_id: str) -> dict[str, Any]:
        """``GET /users/{id}`` — the user object (username, global_name)."""

    @abstractmethod
    async def create_interaction_response(
        self, interaction_id: str, interaction_token: str, *,
        callback_type: int = INTERACTION_CALLBACK_DEFERRED_UPDATE,
    ) -> None:
        """``POST /interactions/{id}/{token}/callback`` — answer an interaction.

        Discord shows the pressing user a red "interaction failed" banner unless the
        bot responds within THREE seconds, so every component press must land here
        even when the decision itself takes longer."""

    @abstractmethod
    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """``PUT /channels/{c}/messages/{m}/reactions/{emoji}/@me`` — react as the bot."""

    @abstractmethod
    async def trigger_typing(self, channel_id: str) -> None:
        """``POST /channels/{id}/typing`` — show the typing indicator for ~10s."""

    async def close(self) -> None:
        """Release any held resources (default: nothing)."""
        return None


class _BucketState:
    """What the last response told us about one rate-limit bucket.

    ``remaining`` is the request budget left in the current window and ``reset_at``
    the monotonic deadline it refills at. ``remaining <= 0`` with a future
    ``reset_at`` is the pre-emptive-wait condition."""

    __slots__ = ("remaining", "reset_at")

    def __init__(self, remaining: int = 1, reset_at: float = 0.0) -> None:
        self.remaining = remaining
        self.reset_at = reset_at


class DiscordDeskHttpApi(DiscordDeskApi):
    """``httpx``-backed Discord REST client with per-bucket + global 429 handling."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers=self.auth_headers(token),
        )
        self._owns_client = client is None
        self._max_retries = max_retries
        self._sleep = sleep or asyncio.sleep
        self._now = now or time.monotonic
        # route key ("POST /channels/123/messages") → the server's opaque bucket hash.
        # Discovered from X-RateLimit-Bucket; until we've seen it, the route key IS
        # the bucket id, which is the conservative assumption (per-route budget).
        self._routes: dict[str, str] = {}
        # bucket id → its live budget.
        self._buckets: dict[str, _BucketState] = {}
        # Monotonic deadline a GLOBAL limit expires at. Gates every route, not one
        # bucket — the distinction that keeps a global 429 from being under-honored.
        self._global_reset_at = 0.0

    @staticmethod
    def auth_headers(token: str) -> dict[str, str]:
        """The headers Discord requires on every REST call.

        The literal ``Bot `` prefix is mandatory — Discord rejects a bare token with
        401 on EVERY route, which presents as "my valid token doesn't work". Pinned
        by a test so the prefix can never be dropped. Discord also asks bots for a
        ``DiscordBot (url, version)`` user agent and rate-limits anonymous-looking
        clients harder, so it is sent here rather than left to httpx's default.

        Deliberately no ``Content-Type``: httpx derives it per request, which is what
        lets one client issue both JSON bodies and the multipart upload (a
        client-level ``application/json`` would override the multipart boundary and
        Discord would reject the upload)."""
        return {
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
        }

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── rate-limit bookkeeping ──

    def _bucket_id(self, route: str) -> str:
        return self._routes.get(route, route)

    async def _await_budget(self, route: str) -> None:
        """Sleep off a global limit and/or this route's exhausted bucket, in that order.

        Called before EVERY attempt (including the first), so a bucket the previous
        response reported as empty costs a wait instead of a 429."""
        delay = self._global_reset_at - self._now()
        if delay > 0:
            logger.debug("discord: global rate limit — waiting %.2fs", delay)
            await self._sleep(min(delay, MAX_RETRY_AFTER))
            self._global_reset_at = 0.0

        st = self._buckets.get(self._bucket_id(route))
        if st is None or st.remaining > 0:
            return
        delay = st.reset_at - self._now()
        if delay > 0:
            logger.debug("discord: bucket %s exhausted — waiting %.2fs", self._bucket_id(route), delay)
            await self._sleep(min(delay, MAX_RETRY_AFTER))
        # Waited the window out: presume one slot, and let the next response's
        # headers restate the truth.
        st.remaining = 1
        st.reset_at = 0.0

    def _record_limits(self, route: str, resp: httpx.Response) -> None:
        """Fold a response's rate-limit headers into the bucket map."""
        bucket = resp.headers.get("X-RateLimit-Bucket", "")
        if bucket:
            self._routes[route] = bucket
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset_after = resp.headers.get("X-RateLimit-Reset-After")
        if remaining is None and reset_after is None:
            return  # an unbucketed route (e.g. the interaction callback)
        st = self._buckets.setdefault(self._bucket_id(route), _BucketState())
        try:
            if remaining is not None:
                st.remaining = int(float(remaining))
            if reset_after is not None:
                st.reset_at = self._now() + float(reset_after)
        except ValueError:
            logger.debug("discord: unparseable rate-limit headers on %s", route)

    def _record_429(self, route: str, resp: httpx.Response) -> float:
        """Record a 429 as global or per-bucket and return the wait it prescribes."""
        body: dict[str, Any] = {}
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            pass
        retry_after = MAX_RETRY_AFTER
        for source in (body.get("retry_after"), resp.headers.get("Retry-After")):
            if source is None:
                continue
            try:
                retry_after = min(float(source), MAX_RETRY_AFTER)
                break
            except (TypeError, ValueError):
                continue
        is_global = bool(body.get("global")) or (
            resp.headers.get("X-RateLimit-Global", "").lower() == "true"
        )
        if is_global:
            self._global_reset_at = self._now() + retry_after
            logger.warning("discord: GLOBAL rate limit — every route gated for %ss", retry_after)
        else:
            self._record_limits(route, resp)
            st = self._buckets.setdefault(self._bucket_id(route), _BucketState())
            st.remaining = 0
            st.reset_at = self._now() + retry_after
            logger.warning("discord: %s rate limited — bucket gated for %ss", route, retry_after)
        return retry_after

    # ── the one request path ──

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Issue ``method path`` and return its parsed body (``None`` for 204).

        The route key is the method plus the CONCRETE path, so a bucket is scoped to
        its major parameter (channel/guild id) the way Discord scopes it — one busy
        channel never stalls another. A 429 is recorded (global vs bucket) and
        retried; 5xx get exponential backoff; any other 4xx is a caller error and
        raises immediately, since retrying just repeats it."""
        route = f"{method} {path}"
        attempt = 0
        while True:
            attempt += 1
            await self._await_budget(route)
            try:
                resp = await self._client.request(
                    method, path,
                    json=json if files is None else None,
                    data=data if files is not None else None,
                    files=files,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt > self._max_retries:
                    raise DiscordDeskApiError(
                        f"network error after {self._max_retries} retries: {exc}", route=route
                    ) from exc
                await self._sleep(min(2 ** (attempt - 1), MAX_RETRY_AFTER))
                continue

            if resp.status_code == 429:
                retry_after = self._record_429(route, resp)
                if attempt > self._max_retries:
                    raise DiscordDeskApiError(
                        f"rate limited (429): retries exhausted (retry_after={retry_after})",
                        status=429, route=route,
                    )
                continue  # _await_budget on the next pass performs the wait

            self._record_limits(route, resp)

            if resp.status_code >= 500:
                if attempt > self._max_retries:
                    raise DiscordDeskApiError(
                        f"server error {resp.status_code}: retries exhausted",
                        status=resp.status_code, route=route,
                    )
                await self._sleep(min(2 ** (attempt - 1), MAX_RETRY_AFTER))
                continue

            if resp.status_code == 204 or not resp.content:
                return None  # No Content — typing / reactions / interaction callback

            body = self._parse_body(resp, route)
            if resp.status_code >= 400:
                raise DiscordDeskApiError(
                    str(body.get("message", f"HTTP {resp.status_code}")),
                    status=resp.status_code, code=int(body.get("code", 0) or 0), route=route,
                )
            return body

    @staticmethod
    def _parse_body(resp: httpx.Response, route: str) -> Any:
        try:
            return resp.json()
        except ValueError as exc:
            raise DiscordDeskApiError(
                f"non-JSON response (status {resp.status_code})",
                status=resp.status_code, route=route,
            ) from exc

    @staticmethod
    def _as_dict(body: Any) -> dict[str, Any]:
        return body if isinstance(body, dict) else {}

    # ── typed wrappers ──

    async def get_gateway_bot(self) -> dict[str, Any]:
        return self._as_dict(await self._call("GET", "/gateway/bot"))

    async def create_message(
        self, channel_id: str, content: str, *,
        components: list[dict[str, Any]] | None = None,
        message_reference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": content}
        if components is not None:
            payload["components"] = components
        if message_reference is not None:
            payload["message_reference"] = message_reference
        return self._as_dict(
            await self._call("POST", f"/channels/{channel_id}/messages", json=payload)
        )

    async def edit_message(
        self, channel_id: str, message_id: str, content: str, *,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": content}
        if components is not None:
            payload["components"] = components
        return self._as_dict(
            await self._call(
                "PATCH", f"/channels/{channel_id}/messages/{message_id}", json=payload
            )
        )

    async def create_dm(self, user_id: str) -> dict[str, Any]:
        return self._as_dict(
            await self._call("POST", "/users/@me/channels", json={"recipient_id": str(user_id)})
        )

    async def upload_file(
        self, channel_id: str, file_path: str, *, filename: str = "", content: str = "",
    ) -> dict[str, Any]:
        """Attach a file with the multipart form Discord requires.

        A file send is ``files[0]`` plus a ``payload_json`` part carrying the message
        body — Discord ignores plain form fields here, so the JSON must ride as that
        one named part or the caption silently vanishes."""
        name = filename or _basename(file_path)
        with open(file_path, "rb") as fh:
            blob = fh.read()
        payload = {"content": content, "attachments": [{"id": 0, "filename": name}]}
        return self._as_dict(
            await self._call(
                "POST", f"/channels/{channel_id}/messages",
                files={"files[0]": (name, blob)},
                data={"payload_json": _json.dumps(payload)},
            )
        )

    async def get_channel(self, channel_id: str) -> dict[str, Any]:
        return self._as_dict(await self._call("GET", f"/channels/{channel_id}"))

    async def get_user(self, user_id: str) -> dict[str, Any]:
        return self._as_dict(await self._call("GET", f"/users/{user_id}"))

    async def create_interaction_response(
        self, interaction_id: str, interaction_token: str, *,
        callback_type: int = INTERACTION_CALLBACK_DEFERRED_UPDATE,
    ) -> None:
        await self._call(
            "POST", f"/interactions/{interaction_id}/{interaction_token}/callback",
            json={"type": callback_type},
        )

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        from urllib.parse import quote

        # The emoji is a PATH segment, so a unicode glyph (or name:id) must be
        # percent-encoded with nothing left safe — an unencoded "✅" 404s.
        await self._call(
            "PUT",
            f"/channels/{channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji, safe='')}/@me",
        )

    async def trigger_typing(self, channel_id: str) -> None:
        await self._call("POST", f"/channels/{channel_id}/typing", json={})


def _basename(path: str) -> str:
    import os

    return os.path.basename(path) or "file"
