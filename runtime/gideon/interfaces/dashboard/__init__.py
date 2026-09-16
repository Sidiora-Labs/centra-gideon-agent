"""Web dashboard + gateway API — served at ``localhost:10000`` by default.

Uses ``aiohttp`` for HTTP serving, a multiplexed WebSocket (``/api/ws``) for
live real-time events, and Server-Sent Events for per-resource streams. Serves
static assets from the ``static/`` directory.

Key modules: ``server`` (route wiring + lifecycle), ``state``
(ConsoleState / chat-session data), ``chat_*`` (multi-session chat endpoints
+ the background runner), and the ``handlers/`` subpackage (per-domain
endpoints).
"""

from gideon.interfaces.dashboard.server import start_api_server, start_dashboard
from gideon.interfaces.dashboard.state import (
    ConsoleState,
    _ChatSession,
    _fmt_duration,
)

__all__ = [
    "start_api_server",
    "start_dashboard",
    "ConsoleState",
    "_ChatSession",
    "_fmt_duration",
]
