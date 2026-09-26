"""DiscordDeskGateway lifecycle over a scripted fake WebSocket.

No network and no wall-clock sleep: ``connect`` and ``sleep`` are injected, so every
clause of the wire protocol is driven frame by frame — the intents bitfield on
IDENTIFY, heartbeat interval + first-beat jitter, the ACK bookkeeping behind the
zombie check, READY capturing session/resume URL/own user id, RESUME (op 6) after a
drop, RECONNECT (op 7), INVALID_SESSION (op 9) in both its resumable and fresh-IDENTIFY
forms, dispatch routing and sequence tracking, and the non-1000 close that keeps a
session resumable. The fake WebSocket's own two disciplines — every frame acked, and
recorded frames verbatim — are what keep the fake honest; both are pinned here too."""


from __future__ import annotations

import asyncio
import json

import pytest

from discord_desk.gateway import (
    GATEWAY_VERSION,
    INTENT_DIRECT_MESSAGES,
    INTENT_GUILD_MESSAGES,
    INTENT_GUILDS,
    INTENT_MESSAGE_CONTENT,
    INTENTS,
    INVALID_SESSION_WAIT,
    OP_DISPATCH,
    OP_HEARTBEAT,
    OP_HEARTBEAT_ACK,
    OP_HELLO,
    OP_IDENTIFY,
    OP_INVALID_SESSION,
    OP_RECONNECT,
    OP_RESUME,
    DiscordDeskGateway,
)

HELLO = {"op": OP_HELLO, "d": {"heartbeat_interval": 41250}}


def _dispatch(event: str, data: dict, seq: int) -> dict:
    return {"op": OP_DISPATCH, "t": event, "s": seq, "d": data}


READY = _dispatch(
    "READY",
    {"session_id": "sess-1", "resume_gateway_url": "wss://resume.discord.gg",
     "user": {"id": "bot-1", "username": "gidbot"}},
    1,
)


class FakeWS:
    """A scripted WebSocket. Frames are dicts; ``None`` ends the connection.

    Two behaviours here are not decoration — they are what makes the fake FAITHFUL,
    and getting either wrong makes the fake, not the client, decide the test:

    * ``auto_ack`` — a real gateway answers every op-1 heartbeat with an op-11 ACK.
      A fake that acks only once turns every long-running test into a zombie
      reconnect. The zombie test is the one place that sets ``auto_ack=False``,
      which is precisely the condition it is testing.
    * ``close()`` wakes a pending ``recv()`` — closing a real socket makes an
      in-flight recv raise ``ConnectionClosed``. Without this, a client that closes
      its own socket (zombie detection, op-7 RECONNECT) would block forever in the
      read it already abandoned, and the reconnect could never be observed.
    """

    def __init__(
        self, frames: list[dict | None], *, hang_after: bool = False, auto_ack: bool = True
    ):
        self._queue: asyncio.Queue = asyncio.Queue()
        for frame in frames:
            self._queue.put_nowait(frame)
        self._hang_after = hang_after
        self._auto_ack = auto_ack
        self.sent: list[dict] = []
        self.closed: list[tuple[int, str]] = []

    async def recv(self):
        if self._queue.empty() and not self._hang_after:
            return None
        frame = await self._queue.get()
        return None if frame is None else json.dumps(frame)

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        if self._auto_ack and frame.get("op") == OP_HEARTBEAT:
            self._queue.put_nowait({"op": OP_HEARTBEAT_ACK})

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))
        self._queue.put_nowait(None)  # a closed socket ends the pending recv

    def op(self, opcode: int) -> list[dict]:
        return [f for f in self.sent if f.get("op") == opcode]


class Harness:
    """Drives a gateway over a list of scripted connections + a recording sleep."""

    def __init__(self, connections: list[FakeWS], **kwargs):
        self.connections = connections
        self.urls: list[str] = []
        self.slept: list[float] = []
        self._index = 0
        self.messages: list[dict] = []
        self.interactions: list[dict] = []
        self.readies: list[dict] = []
        self.gw = DiscordDeskGateway(
            "TOK",
            gateway_url="wss://gateway.discord.gg",
            connect=self._connect,
            sleep=self._sleep,
            on_message=self._on_message,
            on_interaction=self._on_interaction,
            on_ready=self._on_ready,
            **kwargs,
        )

    async def _connect(self, url: str):
        self.urls.append(url)
        if self._index >= len(self.connections):
            # No more scripted connections: stop the loop instead of spinning.
            self.gw._stopping = True
            return FakeWS([None])
        ws = self.connections[self._index]
        self._index += 1
        return ws

    async def _sleep(self, secs: float) -> None:
        self.slept.append(secs)
        await asyncio.sleep(0)  # yield without burning wall-clock

    async def _on_message(self, data):
        self.messages.append(data)

    async def _on_interaction(self, data):
        self.interactions.append(data)

    async def _on_ready(self, data):
        self.readies.append(data)

    async def run(self, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self.gw.run(), timeout=timeout)


class TestIntents:
    def test_exact_bitfield(self):
        """37377 = guilds | guild_messages | direct_messages | message_content.

        A wrong bitfield fails SILENTLY — you connect fine and the events you didn't
        ask for simply never arrive — so the exact value is pinned, and pinned twice:
        once as the literal, once as the named-bit sum, so a typo in either is caught
        by the other. (1 + 512 + 4096 + 32768 = 37377. Drop bit 12 and you get 33281 —
        the same set without DIRECT_MESSAGES, which is exactly the silent failure this
        test exists to prevent, since DM pairing would then never receive a message.)"""
        assert INTENTS == 37377
        assert INTENTS == (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)
        assert INTENTS == 1 + 512 + 4096 + 32768

    def test_direct_messages_bit_is_present(self):
        """The regression guard for a dropped DM bit: DMs are half this app's job."""
        assert INTENTS & INTENT_DIRECT_MESSAGES
        assert INTENTS & INTENT_MESSAGE_CONTENT

    def test_named_bits(self):
        assert INTENT_GUILDS == 1
        assert INTENT_GUILD_MESSAGES == 512
        assert INTENT_DIRECT_MESSAGES == 4096
        assert INTENT_MESSAGE_CONTENT == 32768

    @pytest.mark.asyncio
    async def test_identify_carries_the_intents(self):
        ws = FakeWS([HELLO, READY, None])
        h = Harness([ws])
        await h.run()
        identify = ws.op(OP_IDENTIFY)[0]
        assert identify["d"]["intents"] == 37377
        assert identify["d"]["token"] == "TOK"


class TestConnectUrl:
    @pytest.mark.asyncio
    async def test_version_and_encoding_pinned(self):
        ws = FakeWS([HELLO, READY, None])
        h = Harness([ws])
        await h.run()
        assert h.urls[0] == f"wss://gateway.discord.gg?v={GATEWAY_VERSION}&encoding=json"

    @pytest.mark.asyncio
    async def test_resume_url_gets_the_params_too(self):
        """resume_gateway_url comes back bare — an unversioned resume is a real bug."""
        first = FakeWS([HELLO, READY, None])
        second = FakeWS([HELLO, None])
        h = Harness([first, second])
        await h.run()
        assert h.urls[1] == f"wss://resume.discord.gg?v={GATEWAY_VERSION}&encoding=json"


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_hello_sets_the_interval_and_first_beat_is_jittered(self):
        """HELLO's ms interval becomes the cadence; only the FIRST beat is jittered."""
        ws = FakeWS([HELLO, READY], hang_after=True)
        h = Harness([ws])
        task = asyncio.ensure_future(h.gw.run())
        await _settle()
        assert h.gw.heartbeat_interval == pytest.approx(41.25)
        # first recorded sleep is the jittered first beat (half the interval),
        # subsequent ones are the full interval.
        assert h.slept[0] == pytest.approx(41.25 * 0.5)
        assert h.slept[1] == pytest.approx(41.25)
        await h.gw.stop()
        task.cancel()

    @pytest.mark.asyncio
    async def test_heartbeat_carries_the_last_sequence(self):
        """`s` rides along: it's what a resume replays from, and null before READY."""
        ws = FakeWS([HELLO, READY], hang_after=True)
        h = Harness([ws])
        task = asyncio.ensure_future(h.gw.run())
        await _settle()
        beats = ws.op(OP_HEARTBEAT)
        assert beats, "no heartbeat sent"
        assert beats[0]["d"] == 1  # READY's s=1 was tracked before the beat
        await h.gw.stop()
        task.cancel()

    @pytest.mark.asyncio
    async def test_heartbeat_sequence_is_null_before_any_event(self):
        """Discord requires `d: null`, not 0, before the first dispatch."""
        ws = FakeWS([HELLO], hang_after=True)
        h = Harness([ws])
        task = asyncio.ensure_future(h.gw.run())
        await _settle()
        assert ws.op(OP_HEARTBEAT)[0]["d"] is None
        await h.gw.stop()
        task.cancel()

    @pytest.mark.asyncio
    async def test_ack_clears_the_pending_flag(self):
        """op 11 clears the pending flag, so the next beat is not read as a zombie.

        Driven one step at a time rather than through ``run()``: with an instant fake
        sleep the heartbeat loop free-runs, so the flag's value at any later moment is
        a race. Stepping beat → ack → beat pins the actual state machine."""
        ws = FakeWS([], hang_after=True)
        gw = DiscordDeskGateway("TOK", connect=None)
        gw._ws = ws

        assert await gw._beat_once() is True
        assert gw._ack_pending is True  # awaiting the ACK

        await gw._handle({"op": OP_HEARTBEAT_ACK})
        assert gw._ack_pending is False  # cleared

        # With the ack in, the next beat is fine — no zombie declared.
        assert await gw._beat_once() is True
        assert gw.zombie_reconnects == 0
        assert len(ws.op(OP_HEARTBEAT)) == 2

    @pytest.mark.asyncio
    async def test_unacked_beat_is_declared_a_zombie(self):
        """The negative of the above, at the same unit level: no ack ⇒ False + close."""
        ws = FakeWS([], hang_after=True, auto_ack=False)
        gw = DiscordDeskGateway("TOK", connect=None)
        gw._ws = ws

        assert await gw._beat_once() is True  # beat 1 goes out
        assert await gw._beat_once() is False  # beat 2 finds ack still pending
        assert gw.zombie_reconnects == 1
        assert ws.closed[0][0] == 4000  # resumable close, never 1000

    @pytest.mark.asyncio
    async def test_no_zombie_while_the_gateway_keeps_acking(self):
        """The steady state over many beats: an acking gateway never reconnects."""
        ws = FakeWS([HELLO, READY], hang_after=True)
        h = Harness([ws])
        task = asyncio.ensure_future(h.gw.run())
        await _settle()
        assert len(ws.op(OP_HEARTBEAT)) > 1, "expected repeated beats"
        assert h.gw.zombie_reconnects == 0
        assert ws.closed == []  # still connected
        await h.gw.stop()
        task.cancel()

    @pytest.mark.asyncio
    async def test_server_requested_heartbeat_is_answered(self):
        """Discord may send op 1 asking for an immediate beat."""
        ws = FakeWS([HELLO, READY, {"op": OP_HEARTBEAT, "d": None}, None])
        h = Harness([ws])
        await h.run()
        assert any(f["d"] == 1 for f in ws.op(OP_HEARTBEAT))

    @pytest.mark.asyncio
    async def test_zombie_connection_closes_non_1000_and_resumes(self):
        """No ACK by the time the next beat is due ⇒ the socket is a zombie.

        TCP stays open while the gateway has stopped processing us, so a naive client
        sits there believing it's connected. The close code must NOT be 1000 or
        Discord invalidates the session and the resume is lost."""
        # auto_ack=False IS the zombie: beat 1 goes out, and beat 2's pre-send check
        # finds the previous ack still pending.
        first = FakeWS([HELLO, READY], hang_after=True, auto_ack=False)
        second = FakeWS([HELLO, {"op": OP_DISPATCH, "t": "RESUMED", "s": 2, "d": {}}, None])
        h = Harness([first, second])
        await h.run()
        assert h.gw.zombie_reconnects == 1
        assert first.closed[0][0] == 4000  # non-1000 keeps the session resumable
        # and the reconnect RESUMED rather than re-IDENTIFYing
        assert second.op(OP_RESUME)
        assert not second.op(OP_IDENTIFY)


class TestReady:
    @pytest.mark.asyncio
    async def test_ready_captures_session_and_resume_url_and_own_user(self):
        ws = FakeWS([HELLO, READY, None])
        h = Harness([ws])
        await h.run()
        assert h.gw.session_id == "sess-1"
        assert h.gw.resume_url == "wss://resume.discord.gg"
        assert h.gw.sequence == 1
        assert h.readies[0]["user"]["id"] == "bot-1"

    @pytest.mark.asyncio
    async def test_ready_without_resume_url_falls_back_to_the_base(self):
        ready = _dispatch("READY", {"session_id": "s2", "user": {"id": "b"}}, 1)
        ws = FakeWS([HELLO, ready, None])
        h = Harness([ws])
        await h.run()
        assert h.gw.resume_url == "wss://gateway.discord.gg"


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_after_drop_sends_op6_with_the_right_seq(self):
        first = FakeWS([HELLO, READY, _dispatch("MESSAGE_CREATE", {"content": "a"}, 7), None])
        second = FakeWS([HELLO, None])
        h = Harness([first, second])
        await h.run()
        resume = second.op(OP_RESUME)[0]
        assert resume["d"] == {"token": "TOK", "session_id": "sess-1", "seq": 7}

    @pytest.mark.asyncio
    async def test_reconnect_op7_closes_and_resumes(self):
        first = FakeWS([HELLO, READY, {"op": OP_RECONNECT, "d": None}], hang_after=True)
        second = FakeWS([HELLO, None])
        h = Harness([first, second])
        await h.run()
        assert first.closed[0][0] == 4000
        assert second.op(OP_RESUME)
        assert not second.op(OP_IDENTIFY)

    @pytest.mark.asyncio
    async def test_invalid_session_not_resumable_forces_fresh_identify(self):
        first = FakeWS(
            [HELLO, READY, {"op": OP_INVALID_SESSION, "d": False}], hang_after=True
        )
        second = FakeWS([HELLO, None])
        h = Harness([first, second])
        await h.run()
        assert h.gw.session_id == ""  # session state dropped
        assert h.gw.sequence is None
        assert second.op(OP_IDENTIFY)  # fresh handshake, not a resume
        assert not second.op(OP_RESUME)
        assert INVALID_SESSION_WAIT in h.slept  # waited the documented 1-5s first

    @pytest.mark.asyncio
    async def test_invalid_session_resumable_keeps_the_session(self):
        first = FakeWS([HELLO, READY, {"op": OP_INVALID_SESSION, "d": True}], hang_after=True)
        second = FakeWS([HELLO, None])
        h = Harness([first, second])
        await h.run()
        assert h.gw.session_id == "sess-1"  # kept — the session is still resumable
        assert second.op(OP_RESUME)

    @pytest.mark.asyncio
    async def test_first_connection_identifies_not_resumes(self):
        ws = FakeWS([HELLO, READY, None])
        h = Harness([ws])
        await h.run()
        assert ws.op(OP_IDENTIFY) and not ws.op(OP_RESUME)


class TestDispatch:
    @pytest.mark.asyncio
    async def test_message_create_routes_to_on_message(self):
        ws = FakeWS([HELLO, READY, _dispatch("MESSAGE_CREATE", {"content": "hi", "id": "9"}, 2), None])
        h = Harness([ws])
        await h.run()
        assert h.messages == [{"content": "hi", "id": "9"}]
        assert h.interactions == []

    @pytest.mark.asyncio
    async def test_interaction_create_routes_to_on_interaction(self):
        ws = FakeWS([HELLO, READY, _dispatch("INTERACTION_CREATE", {"type": 3, "id": "i1"}, 2), None])
        h = Harness([ws])
        await h.run()
        assert h.interactions == [{"type": 3, "id": "i1"}]
        assert h.messages == []

    @pytest.mark.asyncio
    async def test_sequence_tracked_from_every_dispatch(self):
        ws = FakeWS([
            HELLO, READY,
            _dispatch("MESSAGE_CREATE", {"content": "a"}, 5),
            _dispatch("MESSAGE_CREATE", {"content": "b"}, 6),
            None,
        ])
        h = Harness([ws])
        await h.run()
        assert h.gw.sequence == 6

    @pytest.mark.asyncio
    async def test_unknown_event_is_ignored(self):
        ws = FakeWS([HELLO, READY, _dispatch("TYPING_START", {"user_id": "1"}, 2), None])
        h = Harness([ws])
        await h.run()
        assert h.messages == [] and h.interactions == []

    @pytest.mark.asyncio
    async def test_raising_handler_does_not_kill_the_loop(self):
        """A bad handler must not wedge inbound — the next event still lands."""
        seen: list[str] = []

        async def boom(data):
            seen.append(data.get("content", ""))
            if len(seen) == 1:
                raise RuntimeError("handler blew up")

        ws = FakeWS([
            HELLO, READY,
            _dispatch("MESSAGE_CREATE", {"content": "first"}, 2),
            _dispatch("MESSAGE_CREATE", {"content": "second"}, 3),
            None,
        ])
        h = Harness([ws])
        h.gw._on_message = boom
        await h.run()
        assert seen == ["first", "second"]
        assert h.gw.sequence == 3  # advanced past the raising event

    @pytest.mark.asyncio
    async def test_non_dict_payload_is_dropped(self):
        ws = FakeWS([HELLO, READY, {"op": OP_DISPATCH, "t": "MESSAGE_CREATE", "s": 2, "d": []}, None])
        h = Harness([ws])
        await h.run()
        assert h.messages == []

    @pytest.mark.asyncio
    async def test_undecodable_frame_ends_the_connection_cleanly(self):
        """Garbage on the wire must not raise out of the loop."""

        class BadWS(FakeWS):
            async def recv(self):
                if self._frames:
                    frame = self._frames.pop(0)
                    return None if frame is None else json.dumps(frame)
                return "{not json"

        ws = BadWS([HELLO, READY])
        h = Harness([ws])
        await h.run()  # returns without raising


class TestHandshakeFailures:
    @pytest.mark.asyncio
    async def test_missing_hello_reconnects(self):
        """The gateway MUST send HELLO first; anything else is a broken connection."""
        first = FakeWS([READY, None])  # no HELLO
        second = FakeWS([HELLO, READY, None])
        h = Harness([first, second])
        await h.run()
        assert second.op(OP_IDENTIFY)  # recovered on the next connection

    @pytest.mark.asyncio
    async def test_connect_failure_backs_off_and_retries(self):
        attempts = {"n": 0}
        gw = DiscordDeskGateway("TOK", connect=None)

        async def flaky_connect(url):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("connection refused")
            gw._stopping = True  # let the second connection be the last
            return FakeWS([HELLO, None])

        slept: list[float] = []

        async def sleep(secs):
            slept.append(secs)

        gw._connect = flaky_connect
        gw._sleep = sleep
        await asyncio.wait_for(gw.run(), timeout=2.0)
        assert attempts["n"] == 2
        assert slept == [1.0]  # exactly one backoff, and it preceded the retry

    @pytest.mark.asyncio
    async def test_repeated_connect_failure_backs_off_exponentially(self):
        """Backoff must grow, not hammer a gateway that's refusing us."""
        attempts = {"n": 0}
        gw = DiscordDeskGateway("TOK", connect=None)

        async def always_fails(url):
            attempts["n"] += 1
            if attempts["n"] >= 4:
                gw._stopping = True
            raise OSError("connection refused")

        slept: list[float] = []

        async def sleep(secs):
            slept.append(secs)

        gw._connect = always_fails
        gw._sleep = sleep
        await asyncio.wait_for(gw.run(), timeout=2.0)
        assert slept == [1.0, 2.0, 4.0, 8.0]

    @pytest.mark.asyncio
    async def test_stop_closes_with_1000(self):
        """A deliberate shutdown DOES want the session invalidated — 1000 is right.

        The mirror of the zombie test: every close we make while intending to come
        back uses 4000 to keep the session resumable, and only a real shutdown uses
        1000. Confusing the two either leaks sessions or loses events."""
        ws = FakeWS([HELLO, READY], hang_after=True)
        h = Harness([ws])
        task = asyncio.ensure_future(h.gw.run())
        await _settle()
        await h.gw.stop()
        task.cancel()
        assert ws.closed and ws.closed[0][0] == 1000


async def _settle(cycles: int = 30) -> None:
    """Let the event loop run the gateway's tasks to a quiescent point."""
    for _ in range(cycles):
        await asyncio.sleep(0)
