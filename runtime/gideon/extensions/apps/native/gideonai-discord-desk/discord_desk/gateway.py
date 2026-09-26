"""Discord Gateway client — raw WebSocket protocol over ``websockets`` (no vendor SDK).

Discord does not deliver events over HTTP: a bot must hold a WebSocket to the
gateway and speak its opcode protocol. That protocol is implemented here directly
over ``websockets`` (already a core dependency) rather than through a vendor
library, so the whole lifecycle lives in this module:

1. connect to ``<gateway_url>?v=10&encoding=json``;
2. **HELLO (op 10)** arrives first, carrying ``heartbeat_interval`` in ms → start
   beating;
3. **HEARTBEAT (op 1)** every interval, carrying the last sequence number ``s``
   (``null`` before the first event). Discord replies **HEARTBEAT_ACK (op 11)**;
4. **IDENTIFY (op 2)** with the intents bitfield → **READY** (op 0, ``t=READY``),
   which hands back the ``session_id`` and ``resume_gateway_url`` a resume needs;
5. every **dispatch (op 0)** carries a monotonically increasing ``s`` we must track
   — it is what both the heartbeat and a resume replay from;
6. on a drop, reconnect to ``resume_gateway_url`` and **RESUME (op 6)** with
   ``{token, session_id, seq}``; Discord replays what we missed. **INVALID_SESSION
   (op 9)** means the resume was refused — its ``d`` is a boolean saying whether the
   session is still resumable at all — so wait the documented 1–5s and IDENTIFY
   fresh. **RECONNECT (op 7)** is Discord asking us to reconnect-and-resume.

The trap this module exists to contain is the **zombied connection**: TCP can stay
open while the gateway has stopped processing us, so heartbeats go out and no ACK
comes back, and a naive client sits there believing it is connected while events
pile up undelivered. Discord's documented remedy is to treat a heartbeat that was
never ACKed by the time the NEXT one is due as a dead connection, close it non-1000
(so the session stays resumable) and resume. :meth:`_beat_once` implements exactly
that check, and a test drives it.

Everything external is injectable so tests need NO network and NO wall-clock sleep:
the connect function (tests pass a fake WS yielding canned JSON frames) and
``sleep``, mirroring how ``api.py`` injects its client + sleep.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

GATEWAY_VERSION = 10
# Fallback gateway URL. The real one comes from GET /gateway/bot; this is only used
# when that probe is unavailable, and Discord keeps it stable for that reason.
DEFAULT_GATEWAY_URL = "wss://gateway.discord.gg"

# ── Gateway opcodes (Discord "Gateway Opcodes") ──
OP_DISPATCH = 0  # server → client: an event, carries `t` and `s`
OP_HEARTBEAT = 1  # both ways: keepalive carrying the last sequence
OP_IDENTIFY = 2  # client → server: authenticate + declare intents
OP_RESUME = 6  # client → server: replay a dropped session
OP_RECONNECT = 7  # server → client: close and resume
OP_INVALID_SESSION = 9  # server → client: session gone (`d` = resumable?)
OP_HELLO = 10  # server → client: first frame, carries heartbeat_interval
OP_HEARTBEAT_ACK = 11  # server → client: your heartbeat landed

# ── Gateway intents (Discord "Gateway Intents") ──
# Named bits, summed below — never a hardcoded magic int, because a wrong bitfield
# fails SILENTLY: the connection succeeds and the events you didn't ask for simply
# never arrive.
INTENT_GUILDS = 1 << 0  # guild lifecycle: which servers/channels exist
INTENT_GUILD_MESSAGES = 1 << 9  # MESSAGE_CREATE in guild channels
INTENT_DIRECT_MESSAGES = 1 << 12  # MESSAGE_CREATE in DMs
# PRIVILEGED. Without it every message arrives with an EMPTY `content` — the #1
# reason a working-looking Discord bot appears to ignore everything. The owner must
# tick "Message Content Intent" in the Developer Portal; setup + README say so.
INTENT_MESSAGE_CONTENT = 1 << 15

#: The exact bitfield we IDENTIFY with (37377 = 1 + 512 + 4096 + 32768). Pinned by a
#: test, including a dedicated guard on the DIRECT_MESSAGES bit — dropping it yields
#: 33281, a bot that silently never receives a DM and so can never be paired.
INTENTS = INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_DIRECT_MESSAGES | INTENT_MESSAGE_CONTENT

# Discord's documented wait before re-IDENTIFYing after INVALID_SESSION (1-5s).
# Fixed rather than random so a test can assert it; the jitter Discord asks for is
# about spreading a fleet's reconnects, and one self-hosted bot is not a fleet.
INVALID_SESSION_WAIT = 2.0
# Backoff ceiling for reconnect attempts after a transport failure.
MAX_RECONNECT_BACKOFF = 30.0
# Fraction of the first heartbeat interval to wait, per Discord's "multiply by
# jitter" guidance — applied to the FIRST beat only, so a restarted fleet doesn't
# beat in lockstep.
FIRST_BEAT_JITTER = 0.5


class DiscordDeskGateway:
    """One gateway connection's full lifecycle: identify, beat, resume, dispatch.

    ``on_message`` / ``on_interaction`` are async callbacks for MESSAGE_CREATE and
    INTERACTION_CREATE. ``on_ready`` receives the READY payload (the transport reads
    the bot's own user id from it — see the self-message trap in ``transport.py``).
    ``connect`` builds the WS: it is injected so tests hand in a fake, and it
    receives the URL so a resume can target ``resume_gateway_url``."""

    def __init__(
        self,
        token: str,
        *,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        connect: Callable[[str], Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_interaction: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_ready: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._token = token
        self._gateway_url = gateway_url
        self._connect = connect or _websockets_connect
        self._sleep = sleep or asyncio.sleep
        self._on_message = on_message
        self._on_interaction = on_interaction
        self._on_ready = on_ready

        self._ws: Any = None
        self._heartbeat_task: asyncio.Task | None = None
        self._stopping = False
        # ── resume state (survives a reconnect; cleared only on INVALID_SESSION) ──
        self.session_id = ""
        self.resume_url = ""
        self.sequence: int | None = None
        # ── liveness state (per-connection) ──
        self.heartbeat_interval = 0.0
        self._ack_pending = False
        self.zombie_reconnects = 0

    # ── the outer loop ──

    async def run(self) -> None:
        """Connect (and re-connect) until :meth:`stop`. Degrades, never crashes.

        One iteration = one WS connection. Which handshake it performs is decided by
        the resume state carried over: a live ``session_id`` means RESUME against
        ``resume_gateway_url``, otherwise a fresh IDENTIFY against the base URL."""
        backoff = 1.0
        while not self._stopping:
            url = self.resume_url if self.session_id else self._gateway_url
            try:
                await self._run_once(url)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "discord gateway: connection error (%s) — reconnecting in %ss", exc, backoff
                )
                await self._sleep(backoff)
                backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF)
            finally:
                await self._cancel_heartbeat()

    async def stop(self) -> None:
        """Stop the loop and close the socket (idempotent)."""
        self._stopping = True
        await self._cancel_heartbeat()
        await self._close_ws()

    async def _run_once(self, url: str) -> None:
        """Hold ONE connection: HELLO → handshake → read frames until it closes."""
        self._ws = await self._connect(self._connect_url(url))
        self._ack_pending = False
        try:
            hello = await self._recv()
            if hello is None or hello.get("op") != OP_HELLO:
                raise RuntimeError(f"expected HELLO (op {OP_HELLO}), got {hello}")
            self.heartbeat_interval = float(hello.get("d", {}).get("heartbeat_interval", 41250)) / 1000.0
            self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

            if self.session_id:
                await self._send_resume()
            else:
                await self._send_identify()

            while not self._stopping:
                frame = await self._recv()
                if frame is None:
                    return  # socket closed — the outer loop decides resume vs identify
                await self._handle(frame)
        finally:
            await self._cancel_heartbeat()
            # 1000 only when we are shutting down for good: Discord INVALIDATES the
            # session on a 1000/1001 client close, so any close we make while still
            # intending to come back uses 4000 to keep the session resumable.
            await self._close_ws(code=1000 if self._stopping else 4000)

    def _connect_url(self, url: str) -> str:
        """Pin the API version + JSON encoding on the gateway URL.

        Discord's ``resume_gateway_url`` comes back WITHOUT query params, and an
        unversioned connection gets whatever default version Discord picks — so the
        params are appended here for both the initial connect and every resume."""
        base = url or DEFAULT_GATEWAY_URL
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}v={GATEWAY_VERSION}&encoding=json"

    # ── frames ──

    async def _recv(self) -> dict[str, Any] | None:
        """One decoded frame, or ``None`` when the socket is done/undecodable."""
        try:
            raw = await self._ws.recv()
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as exc:
            logger.debug("discord gateway: recv ended: %s", exc)
            return None
        if raw is None:
            return None
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("discord gateway: undecodable frame dropped")
            return None
        return frame if isinstance(frame, dict) else None

    async def _send(self, payload: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(payload))

    async def _send_identify(self) -> None:
        await self._send({
            "op": OP_IDENTIFY,
            "d": {
                "token": self._token,
                "intents": INTENTS,
                "properties": {"os": "linux", "browser": "gideon", "device": "gideon"},
            },
        })
        logger.info("discord gateway: IDENTIFY sent (intents=%d)", INTENTS)

    async def _send_resume(self) -> None:
        await self._send({
            "op": OP_RESUME,
            "d": {
                "token": self._token,
                "session_id": self.session_id,
                # `seq` is the last sequence we PROCESSED — Discord replays from
                # there, so a stale value silently loses events.
                "seq": self.sequence,
            },
        })
        logger.info("discord gateway: RESUME sent (seq=%s)", self.sequence)

    # ── heartbeat ──

    async def _heartbeat_loop(self) -> None:
        """Beat forever at the HELLO-declared interval, jittering only the first."""
        try:
            await self._sleep(self.heartbeat_interval * FIRST_BEAT_JITTER)
            while not self._stopping:
                if not await self._beat_once():
                    return
                await self._sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("discord gateway: heartbeat loop ended", exc_info=True)

    async def _beat_once(self) -> bool:
        """Send one heartbeat. Returns False when the connection is a zombie.

        The zombie check runs BEFORE sending: if the previous beat is still
        unacknowledged now that the next one is due, the gateway has stopped
        processing us even though the socket looks open. Close it with a NON-1000
        code so Discord keeps the session resumable, and let the outer loop
        reconnect + RESUME (no events lost)."""
        if self._ack_pending:
            self.zombie_reconnects += 1
            logger.warning("discord gateway: heartbeat unacked — zombie connection, resuming")
            await self._close_ws(code=4000, reason="zombie connection")
            return False
        self._ack_pending = True
        try:
            await self._send({"op": OP_HEARTBEAT, "d": self.sequence})
        except Exception:
            logger.debug("discord gateway: heartbeat send failed", exc_info=True)
            return False
        return True

    async def _cancel_heartbeat(self) -> None:
        task, self._heartbeat_task = self._heartbeat_task, None
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            logger.debug("discord gateway: heartbeat cancel error", exc_info=True)

    async def _close_ws(self, *, code: int = 1000, reason: str = "") -> None:
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            await ws.close(code, reason)
        except TypeError:
            # A fake (or an older websockets) may take no close args.
            try:
                await ws.close()
            except Exception:
                logger.debug("discord gateway: close failed", exc_info=True)
        except Exception:
            logger.debug("discord gateway: close failed", exc_info=True)

    # ── frame handling ──

    async def _handle(self, frame: dict[str, Any]) -> None:
        op = frame.get("op")
        if op == OP_DISPATCH:
            await self._handle_dispatch(frame)
        elif op == OP_HEARTBEAT_ACK:
            self._ack_pending = False
        elif op == OP_HEARTBEAT:
            # Discord may ask for an immediate beat; answer it without disturbing
            # the interval loop's own pending-ack bookkeeping.
            await self._send({"op": OP_HEARTBEAT, "d": self.sequence})
        elif op == OP_RECONNECT:
            logger.info("discord gateway: RECONNECT requested — closing to resume")
            await self._close_ws(code=4000, reason="reconnect requested")
        elif op == OP_INVALID_SESSION:
            await self._handle_invalid_session(frame)
        elif op == OP_HELLO:
            logger.debug("discord gateway: unexpected mid-stream HELLO ignored")

    async def _handle_invalid_session(self, frame: dict[str, Any]) -> None:
        """A refused session. ``d`` is a bare boolean: is it still resumable?

        Resumable → keep the session state and let the outer loop RESUME again.
        Not resumable → drop session_id/sequence so the next pass IDENTIFYs fresh.
        Either way wait first: re-handshaking instantly is what earns an
        invalid-session loop and, eventually, a token reset."""
        resumable = bool(frame.get("d"))
        if not resumable:
            logger.info("discord gateway: INVALID_SESSION (not resumable) — fresh IDENTIFY")
            self.session_id = ""
            self.resume_url = ""
            self.sequence = None
        else:
            logger.info("discord gateway: INVALID_SESSION (resumable) — retrying resume")
        await self._sleep(INVALID_SESSION_WAIT)
        await self._close_ws(code=4000, reason="invalid session")

    async def _handle_dispatch(self, frame: dict[str, Any]) -> None:
        """Track ``s``, then route the event. A raising handler can't kill the loop.

        The sequence is advanced BEFORE dispatch (the same discipline as the Telegram
        poll loop's offset): a handler that raises must not leave us replaying the
        same event forever on the next resume."""
        seq = frame.get("s")
        if isinstance(seq, int):
            self.sequence = seq
        event = frame.get("t") or ""
        # `d` is null on some dispatches (RESUMED) — that's an empty payload, not a
        # malformed one. Anything else non-dict IS malformed and is dropped. Written
        # as two steps deliberately: `frame.get("d") or {}` would coerce a bogus list
        # to {} and hand the handler a silently-empty event.
        data = frame.get("d")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            logger.warning("discord gateway: %s dispatch with non-object payload dropped", event)
            return
        try:
            if event == "READY":
                self.session_id = str(data.get("session_id", ""))
                self.resume_url = str(data.get("resume_gateway_url", "")) or self._gateway_url
                logger.info("discord gateway: READY (session=%s)", self.session_id)
                if self._on_ready is not None:
                    await self._on_ready(data)
            elif event == "RESUMED":
                logger.info("discord gateway: RESUMED (seq=%s)", self.sequence)
            elif event == "MESSAGE_CREATE":
                if self._on_message is not None:
                    await self._on_message(data)
            elif event == "INTERACTION_CREATE":
                if self._on_interaction is not None:
                    await self._on_interaction(data)
        except Exception:
            logger.warning("discord gateway: %s handler failed", event, exc_info=True)


async def _websockets_connect(url: str) -> Any:
    """The real transport: a ``websockets`` client connection.

    Imported lazily so a test that injects a fake connect never needs the library
    resolved, and so an import failure surfaces at connect time (where the reconnect
    backoff can report it) rather than at app load."""
    from websockets.asyncio.client import connect

    return await connect(url, max_size=None)
