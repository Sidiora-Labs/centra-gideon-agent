"""Attended terminal chat through this home's authenticated loopback gateway.

Ordinary gateway sessions preserve dashboard history and approval handling. The
CLI streams the native turn socket and asks the gateway to stop on interruption.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from gideon.interfaces.cli import run as cli_run
from gideon.engine import home_gateway
from gideon.security.approval_brief import RISK_LABELS
from gideon.interfaces.cli.run import RunError
from gideon.core.config import loader as config_loader
from gideon.core.constants import DATA_WARNING
from gideon.engine.gateway_base import GatewayBaseUnresolved

#: What ends the interactive chat, besides Ctrl-D.
_EXIT_WORDS = frozenset({"exit", "quit", "/exit", "/quit", ":q"})

#: How an approval ended — the ``outcome`` its ``approval_resolved`` frame carries, one of the four
#: the channel contract names — in the word the Inbox titles a settled approval with.
_ENDED = {
    "approved": "Approved",
    "rejected": "Denied",
    "expired": "Expired",
    "cancelled": "Cancelled",
}

#: Where an approval is answered: every one is listed in the dashboard and on your phone, and a
#: chat channel asks too when your approvals go to one.
_ANSWER_IT = (
    "Answer it in Gideon (the dashboard or your phone) or on your paired chat channel."
)

#: How old the chat's token may be when a request starts: a quarter of an hour short of the hour
#: ``run``'s token lasts (``cli_run._TOKEN_TTL``). A chat can stay open all day.
_REFRESH_AFTER_SECS = 45 * 60


def config_path():
    """The active home, re-resolved per call — see :func:`gideon.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


def _chat(args) -> None:
    """``gideon chat`` — dispatched from ``cli.main``."""
    raise SystemExit(_chat_main(args))


def _chat_main(args) -> int:
    """One message (``-m``) or the interactive chat. Returns the exit status: 0 when the message's
    turn completed (or the interactive chat was left), 1 when there is no gateway to chat with or
    a turn did not complete, 2 for a blank ``-m``."""
    message = getattr(args, "message", None)
    if message is not None and not message.strip():
        print("gideon chat: -m/--message must be a non-empty message.", file=sys.stderr)
        return 2

    try:
        from gideon.interfaces.cli.server import resolve_client_port
        port = resolve_client_port(getattr(args, "port", None))
        if not cli_run.probe_gateway(port):
            raise RunError(_no_gateway(port))
    except (RunError, GatewayBaseUnresolved, home_gateway.HomeGatewayMismatch, home_gateway.NoGatewayRunning) as exc:
        print(f"gideon chat: {exc}", file=sys.stderr)
        return 1
    model = getattr(args, "model", None) or ""
    sign_in = _SignIn(port)
    try:
        sign_in.token()  # the gateway is asked to sign this chat in before anything else
        if message is None:
            return _interactive(sign_in, model)
        key = _open_chat(sign_in, model)
        return 0 if _say(sign_in, key, message.strip()) == "complete" else 1
    except RunError as exc:
        _failed(sign_in, exc)
        return 1
    except KeyboardInterrupt:
        # A second Ctrl-C while a stopped turn was still ending: the first one already asked the
        # gateway to stop it.
        print(file=sys.stderr)
        return 1


def _failed(sign_in: _SignIn, exc: RunError) -> None:
    """Say why a request to the gateway failed: a gateway that has gone away is said as it is,
    with how to start it again."""
    try:
        gone = not cli_run.probe_gateway(sign_in.port, attempts=1)
    except RunError:
        gone = False
    print(_no_gateway(sign_in.port) if gone else f"gideon chat: {exc}", file=sys.stderr)


def _no_gateway(port: int) -> str:
    from gideon.operations.service import controller
    try:
        installed = controller.this_homes_service() is not None
    except Exception:
        installed = False
    command = "gideon restart" if installed else "gideon gateway"
    return f"no gateway is running on port {port}, and your chat runs in it.\n  Start it with: {command}"


class _SignIn:
    """The token the chat's requests carry: ``run``'s, minted with the home's local secret from
    this home's gateway (:func:`~gideon.cli_run.mint_local_token`), and minted again before
    a request once it is :data:`_REFRESH_AFTER_SECS` old. A turn's socket is signed in once, when
    it opens."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._token = ""
        self._minted = 0.0

    def token(self) -> str:
        if not self._token or time.monotonic() - self._minted >= _REFRESH_AFTER_SECS:
            try:
                self._token = cli_run.mint_local_token(self.port)
            except (home_gateway.HomeGatewayMismatch, home_gateway.NoGatewayRunning) as error:
                raise RunError(str(error)) from error
            self._minted = time.monotonic()
        return self._token


def _open_chat(sign_in: _SignIn, model: str) -> str:
    """Open a chat in the gateway, as the dashboard's New chat does, and return its key. *model*
    is the model for this chat; empty, the chat model bound in Settings → Models."""
    created = cli_run._api(
        sign_in.port, sign_in.token(), "/api/chat/sessions", {"model": model} if model else {}
    )
    key = str(created.get("key") or "")
    if not key:
        raise RunError("the gateway opened no chat")
    return key


def _interactive(sign_in: _SignIn, model: str) -> int:
    """Each line you type is the next turn of one chat. The chat is opened by your first message,
    so a prompt left without one leaves no empty chat in the dashboard's list."""
    print("Gideon — your assistant in the terminal")
    print(DATA_WARNING)
    print()
    print("Type your message (Ctrl+D or 'exit' to quit)\n")
    key = ""
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return 0
        if not message:
            continue
        if message.lower() in _EXIT_WORDS:
            print("Bye!")
            return 0
        try:
            key = key or _open_chat(sign_in, model)
            _say(sign_in, key, message)
        except RunError as exc:
            _failed(sign_in, exc)
        print()


def _say(sign_in: _SignIn, key: str, message: str) -> str:
    """Post *message* as the next turn of chat *key*, show the turn until it ends, and return how
    it ended: ``complete``, ``stopped``, ``error`` or ``interrupted``, as its ``chat_done`` says.
    """
    turn = _Turn(key)
    try:
        asyncio.run(_converse(sign_in, turn, message))
    finally:
        turn.close_line()
    turn.say_how_it_ended()
    return turn.outcome


async def _converse(sign_in: _SignIn, turn: _Turn, message: str) -> None:
    """Post *message* as the turn's message and show the turn until the gateway says it ended.

    A cancel while it runs is a Ctrl-C (``asyncio.run`` turns the first one into a cancel of this
    task, its only caller), so it stops the turn in the gateway and goes on showing it until it has
    ended there: the call it was waiting on is cancelled and never runs.
    """
    port = sign_in.port
    async with cli_run._turn_socket(port, sign_in.token(), turn.session_key, message) as (http, ws):
        try:
            await cli_run._read_turn(ws, turn, None)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            turn.note("Stopping the turn.")
            # The turn may have run longer than the token it was posted with lasts.
            fresh = cli_run._bearer_headers(await asyncio.to_thread(sign_in.token))
            await cli_run._ask_to_stop(http, port, turn.session_key, headers=fresh)
            try:
                await cli_run._read_turn(ws, turn, cli_run._STOP_WAIT_SECS)
            except RunError:
                turn.note(
                    "The gateway is still stopping the turn; the chat in the dashboard shows "
                    "when it has stopped."
                )


class _Turn(cli_run._Collector):
    """One turn of the chat as the terminal shows it.

    The answer goes to stdout as it streams. What waits for your decision, how that ended, and the
    turn's notices and errors go to stderr, so ``chat -m … > reply.txt`` keeps the answer alone.
    How the turn ended is read as ``run`` reads it (:class:`~gideon.cli_run._Collector`).
    """

    def __init__(self, session_key: str) -> None:
        super().__init__(session_key, "plain")
        #: Whether stdout's last line is still open: an answer streams without a newline.
        self._open = False
        #: The approvals this turn asked for, by registry id, and the tool each is about.
        self._asked: dict[str, str] = {}
        #: The rows already shown, as (role, text): the gateway sends some of a turn's twice.
        self._shown: set[tuple[str, str]] = set()

    def feed(self, envelope: dict) -> None:
        data = envelope.get("data")
        if isinstance(data, dict) and data.get("session") == self.session_key:
            self._show(str(envelope.get("type", "")), data)
        super().feed(envelope)

    def _show(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "chat_chunk":
            text = str(data.get("content", ""))
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
                self._open = not text.endswith("\n")
        elif kind in ("chat_segment", "tool_call"):
            # The answer so far is settled: what streams after a call is a new paragraph.
            self.close_line()
        elif kind == "approval":
            approval = str(data.get("approval_id") or data.get("id") or "")
            if approval and approval not in self._asked:
                self._asked[approval] = str(data.get("tool") or "")
                self.note(_asks(data))
        elif kind == "approval_resolved":
            approval = str(data.get("id") or "")
            if approval in self._asked:
                self.note(_ended(self._asked.pop(approval), data))
        elif kind == "chat_message":
            self._row(str(data.get("role", "")), str(data.get("content", "")).strip())

    def _row(self, role: str, text: str) -> None:
        """A row the gateway added to the chat: a reply that did not stream (a slash command's),
        a notice, or an error."""
        if role not in ("assistant", "notice", "error") or not text or (role, text) in self._shown:
            return
        self._shown.add((role, text))
        if role == "assistant":
            self.close_line()
            print(text, flush=True)
        else:
            self.note(text)

    def note(self, text: str) -> None:
        """Say *text* on stderr, on a line of its own, in printable characters only: what it says
        of a call is words a model chose (the tool, its input), and a control character in them
        could move the cursor or rewrite what the terminal shows."""
        self.close_line()
        print(_printable(text), file=sys.stderr, flush=True)

    def close_line(self) -> None:
        """End the answer's open line on stdout, if there is one."""
        if self._open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._open = False

    def say_how_it_ended(self) -> None:
        """Say how the turn ended when it did not complete and none of its rows said why."""
        if self.outcome == "stopped":
            self.note("The turn was stopped before it finished.")
        elif self.outcome != "complete" and not any(r == "error" for r, _ in self._shown):
            self.note("The turn did not finish, and the gateway gave no reason.")


def _asks(entry: dict[str, Any]) -> str:
    """What the terminal says of a call that waits for your decision, from its registry entry: the
    tool and its risk (as the Inbox row names them), why it is called and with what, and where it
    is answered."""
    tool = str(entry.get("tool") or "A tool call")
    risk = RISK_LABELS.get(str(entry.get("risk") or ""), "").lower()
    lines = [f"{tool} is waiting for your decision" + (f" (risk: {risk})." if risk else ".")]
    for detail in (entry.get("tool_purpose"), entry.get("tool_input")):
        text = " ".join(str(detail or "").split())
        if len(text) > 200:
            head = text[:199]
            cut = head.rfind(" ")
            if cut >= len(head) // 2:
                head = head[:cut]
            text = head.rstrip(" ,;:") + "…"
        if text:
            lines.append(f"  {text}")
    lines.append(f"  {_ANSWER_IT}")
    return "\n".join(lines)


def _ended(tool: str, frame: dict[str, Any]) -> str:
    """How an approval ended, from its ``approval_resolved`` frame: "Approved: write_file.", and why
    when nobody answered it ("Expired: write_file (nobody answered within 5 minutes).")."""
    word = _ENDED.get(str(frame.get("outcome") or ""), "Ended")
    why = str(frame.get("ended") or "")
    return f"{word}: {tool or 'the call'}" + (f" ({why})" if why else "") + "."


def _printable(text: str) -> str:
    """*text* with every character a terminal would act on rather than show removed, its line
    breaks kept."""
    return "".join(ch for ch in text if ch == "\n" or ch.isprintable())


def _ensure_default_agent_in_config() -> None:
    """Ensure config.json includes a default Gideon agent for fresh installs."""
    from gideon.core.config.transactions import mutate_config

    def ensure(document: dict) -> None:
        if not document.get("agents"):
            document["agents"] = {
                "default": {
                    "provider_agent": "gideon",
                    "workspace": "default",
                    "memory_store": "default",
                }
            }
            document["default_agent"] = "default"

    mutate_config(ensure, path=config_path())
