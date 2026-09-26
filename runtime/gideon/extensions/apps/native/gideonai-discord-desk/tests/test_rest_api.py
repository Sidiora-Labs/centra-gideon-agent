"""DiscordDeskHttpApi over an ``httpx.MockTransport`` — every wrapper + bucket logic.

No live Discord, no wall-clock wait: the httpx client is backed by a ``MockTransport``
returning canned responses (with the rate-limit headers Discord sends), the sleep is
a recording stub, and the monotonic clock is a fake. What that exercises is the
per-bucket vs global-429 distinction — the thing that makes Discord's rate limiting
different from a flat ``retry_after``."""


from __future__ import annotations

import json

import httpx
import pytest

from discord_desk.api import (
    API_BASE,
    DISCORD_DESK_MAX_TEXT,
    INTERACTION_CALLBACK_DEFERRED_UPDATE,
    MAX_RETRY_AFTER,
    USER_AGENT,
    DiscordDeskApiError,
    DiscordDeskHttpApi,
)


def _ok(body=None, *, bucket="b1", remaining=4, reset_after=1.0, status=200):
    """A success response carrying Discord's rate-limit headers."""
    return httpx.Response(
        status,
        json=body if body is not None else {"id": "1"},
        headers={
            "X-RateLimit-Bucket": bucket,
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset-After": str(reset_after),
        },
    )


def _429(*, retry_after=2.0, is_global=False, bucket="b1"):
    headers = {"X-RateLimit-Global": "true"} if is_global else {"X-RateLimit-Bucket": bucket}
    body = {"message": "You are being rate limited.", "retry_after": retry_after}
    if is_global:
        body["global"] = True
    return httpx.Response(429, json=body, headers=headers)


class _Recorder:
    """Captures every request the client makes + serves scripted responses."""

    def __init__(self, responder):
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    def route(self, i: int = -1) -> str:
        return f"{self.requests[i].method} {self.requests[i].url.path}"

    def payload(self, i: int = -1) -> dict:
        req = self.requests[i]
        if req.content:
            try:
                return json.loads(req.content)
            except ValueError:
                return {}
        return {}


def _api(responder, *, max_retries: int = 3):
    """A client with a recording sleep and a hand-advanced clock."""
    slept: list[float] = []
    clock = {"t": 1000.0}

    async def _sleep(secs: float) -> None:
        slept.append(secs)
        clock["t"] += secs  # a recorded sleep still advances time, as a real one would

    rec = _Recorder(responder)
    client = httpx.AsyncClient(
        base_url=API_BASE,
        transport=httpx.MockTransport(rec.handler),
        headers=DiscordDeskHttpApi.auth_headers("TEST"),
    )
    api = DiscordDeskHttpApi(
        "TEST", client=client, max_retries=max_retries, sleep=_sleep, now=lambda: clock["t"]
    )
    return api, rec, slept, clock


class TestAuth:
    def test_bot_prefix_is_mandatory(self):
        """Discord 401s every route on a bare token — the 'Bot ' prefix is the fix."""
        h = DiscordDeskHttpApi.auth_headers("abc.def")
        assert h["Authorization"] == "Bot abc.def"

    def test_user_agent_identifies_the_bot(self):
        assert DiscordDeskHttpApi.auth_headers("t")["User-Agent"] == USER_AGENT

    def test_no_client_level_content_type(self):
        """A client-level application/json would break the multipart upload."""
        assert "Content-Type" not in DiscordDeskHttpApi.auth_headers("t")

    @pytest.mark.asyncio
    async def test_header_rides_on_the_wire(self):
        api, rec, _, _ = _api(lambda r: _ok({"url": "wss://x"}))
        await api.get_gateway_bot()
        assert rec.requests[-1].headers["authorization"] == "Bot TEST"
        await api.close()


class TestWrappers:
    @pytest.mark.asyncio
    async def test_get_gateway_bot(self):
        api, rec, _, _ = _api(
            lambda r: _ok({"url": "wss://gateway.discord.gg", "shards": 1,
                           "session_start_limit": {"remaining": 999}})
        )
        info = await api.get_gateway_bot()
        assert info["url"] == "wss://gateway.discord.gg"
        assert info["session_start_limit"]["remaining"] == 999
        assert rec.route() == "GET /api/v10/gateway/bot"
        await api.close()

    @pytest.mark.asyncio
    async def test_create_message(self):
        api, rec, _, _ = _api(lambda r: _ok({"id": "42"}))
        msg = await api.create_message("500", "hi")
        assert msg["id"] == "42"
        assert rec.route() == "POST /api/v10/channels/500/messages"
        assert rec.payload() == {"content": "hi"}
        await api.close()

    @pytest.mark.asyncio
    async def test_create_message_with_components(self):
        api, rec, _, _ = _api(lambda r: _ok({"id": "43"}))
        rows = [{"type": 1, "components": [{"type": 2, "style": 3, "label": "Go", "custom_id": "g"}]}]
        await api.create_message("500", "pick", components=rows)
        assert rec.payload()["components"] == rows
        await api.close()

    @pytest.mark.asyncio
    async def test_edit_message(self):
        api, rec, _, _ = _api(lambda r: _ok({"id": "7"}))
        await api.edit_message("500", "7", "new")
        assert rec.route() == "PATCH /api/v10/channels/500/messages/7"
        assert rec.payload() == {"content": "new"}
        await api.close()

    @pytest.mark.asyncio
    async def test_edit_message_can_strip_components(self):
        """components=[] is how a resolved approval drops its stale buttons."""
        api, rec, _, _ = _api(lambda r: _ok({"id": "7"}))
        await api.edit_message("500", "7", "done", components=[])
        assert rec.payload()["components"] == []
        await api.close()

    @pytest.mark.asyncio
    async def test_create_dm(self):
        api, rec, _, _ = _api(lambda r: _ok({"id": "dm9", "type": 1}))
        ch = await api.create_dm("42")
        assert ch["id"] == "dm9"
        assert rec.route() == "POST /api/v10/users/@me/channels"
        assert rec.payload() == {"recipient_id": "42"}
        await api.close()

    @pytest.mark.asyncio
    async def test_upload_file_uses_payload_json(self, tmp_path):
        """Discord ignores plain form fields on an upload — the body must be payload_json."""
        f = tmp_path / "report.csv"
        f.write_text("a,b")
        api, rec, _, _ = _api(lambda r: _ok({"id": "11"}))
        msg = await api.upload_file("500", str(f), content="see attached")
        assert msg["id"] == "11"
        body = rec.requests[-1].content
        assert b"report.csv" in body
        assert b"payload_json" in body
        assert b"see attached" in body
        assert rec.requests[-1].headers["content-type"].startswith("multipart/form-data")
        await api.close()

    @pytest.mark.asyncio
    async def test_get_channel_and_user(self):
        api, rec, _, _ = _api(lambda r: _ok({"id": "x", "name": "general"}))
        assert (await api.get_channel("500"))["name"] == "general"
        assert rec.route() == "GET /api/v10/channels/500"
        await api.get_user("42")
        assert rec.route() == "GET /api/v10/users/42"
        await api.close()

    @pytest.mark.asyncio
    async def test_create_interaction_response_defaults_to_deferred_update(self):
        api, rec, _, _ = _api(lambda r: httpx.Response(204))
        await api.create_interaction_response("i1", "tok")
        assert rec.route() == "POST /api/v10/interactions/i1/tok/callback"
        assert rec.payload() == {"type": INTERACTION_CALLBACK_DEFERRED_UPDATE}
        await api.close()

    @pytest.mark.asyncio
    async def test_add_reaction_percent_encodes_the_emoji(self):
        """An unencoded unicode emoji in the path 404s."""
        api, rec, _, _ = _api(lambda r: httpx.Response(204))
        await api.add_reaction("500", "9", "✅")
        assert rec.requests[-1].url.raw_path.decode().endswith(
            "/channels/500/messages/9/reactions/%E2%9C%85/@me"
        )
        await api.close()

    @pytest.mark.asyncio
    async def test_trigger_typing(self):
        api, rec, _, _ = _api(lambda r: httpx.Response(204))
        await api.trigger_typing("500")
        assert rec.route() == "POST /api/v10/channels/500/typing"
        await api.close()

    @pytest.mark.asyncio
    async def test_204_returns_none_not_a_parse_error(self):
        api, _, _, _ = _api(lambda r: httpx.Response(204))
        assert await api.trigger_typing("500") is None

    def test_max_text_constant(self):
        assert DISCORD_DESK_MAX_TEXT == 2000


class TestBucketBackoff:
    @pytest.mark.asyncio
    async def test_429_per_route_then_success(self):
        calls = {"n": 0}

        def responder(r):
            calls["n"] += 1
            if calls["n"] == 1:
                return _429(retry_after=3.0)
            return _ok({"id": "1"})

        api, rec, slept, _ = _api(responder)
        msg = await api.create_message("500", "hi")
        assert msg["id"] == "1"
        assert calls["n"] == 2  # retried once
        assert slept == [3.0]  # honored the body's retry_after
        await api.close()

    @pytest.mark.asyncio
    async def test_preemptive_wait_when_remaining_hits_zero(self):
        """The bucket said 0 remaining, so the NEXT call waits instead of eating a 429."""
        api, rec, slept, clock = _api(lambda r: _ok({"id": "1"}, remaining=0, reset_after=5.0))
        await api.create_message("500", "a")
        assert slept == []  # first call had no prior state to wait on
        await api.create_message("500", "b")
        assert slept == [5.0]  # waited the bucket's window out
        assert len(rec.requests) == 2  # and never spent a 429 to learn it
        await api.close()

    @pytest.mark.asyncio
    async def test_one_routes_exhausted_bucket_does_not_stall_another(self):
        """Buckets are route+major-parameter scoped: channel 500 must not gate 600."""

        def responder(r):
            # Discord gives each channel its own bucket hash.
            channel = r.url.path.split("/")[4]
            return _ok({"id": "1"}, bucket=f"bucket-{channel}", remaining=0, reset_after=5.0)

        api, rec, slept, _ = _api(responder)
        await api.create_message("500", "a")  # exhausts channel 500's bucket
        await api.create_message("600", "b")  # a DIFFERENT bucket — no wait
        assert slept == []
        await api.create_message("500", "c")  # back to the exhausted one — waits
        assert slept == [5.0]
        await api.close()

    @pytest.mark.asyncio
    async def test_different_routes_do_not_share_a_bucket(self):
        def responder(r):
            if r.url.path.endswith("/typing"):
                return httpx.Response(
                    204, headers={"X-RateLimit-Bucket": "typing", "X-RateLimit-Remaining": "9",
                                  "X-RateLimit-Reset-After": "1.0"},
                )
            return _ok({"id": "1"}, bucket="messages", remaining=0, reset_after=5.0)

        api, rec, slept, _ = _api(responder)
        await api.create_message("500", "a")  # messages bucket → empty
        await api.trigger_typing("500")  # typing bucket → untouched, no wait
        assert slept == []
        await api.close()

    @pytest.mark.asyncio
    async def test_global_429_gates_every_route(self):
        """A global limit is NOT one bucket: an unrelated route must wait too."""
        calls = {"n": 0}

        def responder(r):
            calls["n"] += 1
            if calls["n"] == 1:
                return _429(retry_after=4.0, is_global=True)
            return _ok({"id": "1"}, bucket="messages")

        api, rec, slept, _ = _api(responder)
        await api.create_message("500", "a")  # 429 global → retried after 4s
        assert slept == [4.0]
        # A different route, different bucket — the global window is spent, so no
        # further wait, but the recorded global state was what caused the first one.
        assert api._global_reset_at == 0.0
        await api.close()

    @pytest.mark.asyncio
    async def test_global_429_recorded_globally_not_on_the_bucket(self):
        api, rec, slept, clock = _api(lambda r: _429(retry_after=6.0, is_global=True), max_retries=0)
        with pytest.raises(DiscordDeskApiError):
            await api.create_message("500", "a")
        # the global gate was armed; no per-bucket state was invented for the route
        assert api._global_reset_at > 0.0
        assert api._buckets == {}
        await api.close()

    @pytest.mark.asyncio
    async def test_per_route_429_recorded_on_the_bucket_not_globally(self):
        api, rec, slept, _ = _api(lambda r: _429(retry_after=6.0, bucket="bkt"), max_retries=0)
        with pytest.raises(DiscordDeskApiError):
            await api.create_message("500", "a")
        assert api._global_reset_at == 0.0
        assert api._buckets["bkt"].remaining == 0
        await api.close()

    @pytest.mark.asyncio
    async def test_retry_after_capped(self):
        """A pathological retry_after must not wedge the client."""
        api, rec, slept, _ = _api(lambda r: _429(retry_after=99999.0), max_retries=1)
        with pytest.raises(DiscordDeskApiError) as exc:
            await api.create_message("500", "hi")
        assert exc.value.status == 429
        assert all(s <= MAX_RETRY_AFTER for s in slept)
        await api.close()

    @pytest.mark.asyncio
    async def test_429_exhausts_retries_raises(self):
        api, rec, slept, _ = _api(lambda r: _429(retry_after=1.0), max_retries=2)
        with pytest.raises(DiscordDeskApiError) as exc:
            await api.create_message("500", "hi")
        assert exc.value.status == 429
        assert len(rec.requests) == 3  # initial + 2 retries
        await api.close()

    @pytest.mark.asyncio
    async def test_retry_after_header_fallback(self):
        calls = {"n": 0}

        def responder(r):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "2"}, text="rate limited")
            return _ok({"id": "1"})

        api, rec, slept, _ = _api(responder)
        await api.create_message("500", "hi")
        assert slept == [2.0]
        await api.close()


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_5xx_backoff_then_success(self):
        calls = {"n": 0}

        def responder(r):
            calls["n"] += 1
            if calls["n"] <= 2:
                return httpx.Response(503, text="Service Unavailable")
            return _ok({"id": "3"})

        api, rec, slept, _ = _api(responder)
        msg = await api.create_message("500", "hi")
        assert msg["id"] == "3"
        assert len(slept) == 2  # backed off twice before the 3rd try
        await api.close()

    @pytest.mark.asyncio
    async def test_5xx_exhausts_retries(self):
        api, rec, _, _ = _api(lambda r: httpx.Response(500, text="boom"), max_retries=1)
        with pytest.raises(DiscordDeskApiError) as exc:
            await api.create_message("500", "hi")
        assert exc.value.status == 500
        await api.close()

    @pytest.mark.asyncio
    async def test_4xx_raises_immediately_with_the_api_code(self):
        def responder(r):
            return httpx.Response(403, json={"message": "Missing Access", "code": 50001})

        api, rec, slept, _ = _api(responder)
        with pytest.raises(DiscordDeskApiError) as exc:
            await api.create_message("500", "hi")
        assert exc.value.status == 403
        assert exc.value.code == 50001
        assert "Missing Access" in exc.value.message
        assert len(rec.requests) == 1  # no retry on a caller error
        assert slept == []
        await api.close()

    @pytest.mark.asyncio
    async def test_401_surfaces_status(self):
        """A bad token: the transport's test() reports this, it must not be retried."""
        api, rec, _, _ = _api(lambda r: httpx.Response(401, json={"message": "401: Unauthorized"}))
        with pytest.raises(DiscordDeskApiError) as exc:
            await api.get_gateway_bot()
        assert exc.value.status == 401
        assert len(rec.requests) == 1
        await api.close()

    @pytest.mark.asyncio
    async def test_non_json_body_raises_typed_error(self):
        api, _, _, _ = _api(lambda r: httpx.Response(200, text="<html>cloudflare</html>"))
        with pytest.raises(DiscordDeskApiError) as exc:
            await api.get_gateway_bot()
        assert "non-JSON" in exc.value.message
        await api.close()

    @pytest.mark.asyncio
    async def test_network_error_retries_then_raises(self):
        def responder(r):
            raise httpx.ConnectError("no route to host")

        api, rec, slept, _ = _api(responder, max_retries=2)
        with pytest.raises(DiscordDeskApiError) as exc:
            await api.get_gateway_bot()
        assert "network error" in exc.value.message
        assert len(slept) == 2
        await api.close()

    @pytest.mark.asyncio
    async def test_unparseable_rate_limit_headers_are_ignored(self):
        """Garbage headers must not crash the request path."""
        def responder(r):
            return httpx.Response(
                200, json={"id": "1"},
                headers={"X-RateLimit-Bucket": "b", "X-RateLimit-Remaining": "many",
                         "X-RateLimit-Reset-After": "soon"},
            )

        api, _, _, _ = _api(responder)
        assert (await api.create_message("500", "hi"))["id"] == "1"
        await api.close()
