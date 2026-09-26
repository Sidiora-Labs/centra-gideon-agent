"""SlackDeskRuntime — the app-side facade the Slack modules run against.

Historically the Slack event/interaction/handler modules received the core
``GatewayOrchestrator`` as ``orch`` and reached into ~15 Slack-specific attributes
on it (the Slack client, tokens, tracking-channels, socket connection, per-message
identity bookkeeping) *plus* a dozen genuine core services (sessions, cron, history,
dashboard state). Moving Slack into this app bundle, that split becomes explicit:

- **Slack-owned state** (Group A) lives HERE on the runtime.
- **Core services** (Group B) come from a :class:`GatewayServices` handle the
  gateway passes to ``start_inbound`` — proxied transparently via ``__getattr__``
  so the existing ``orch.sessions`` / ``orch.cron_svc`` / … call sites in the moved
  modules keep working unchanged.

The runtime therefore stands in for ``orch`` everywhere the Slack modules used it,
with NO import of the core orchestrator (only the public ``GatewayServices``
protocol). This is the clean core↔channel boundary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from gideon.sdk.channel import CRED_OWNER_ID

from slack_desk_runtime.client import RealSlackDeskClient

if TYPE_CHECKING:
    from gideon.sdk.channel import GatewayServices
    from slack_desk_runtime.settings import SlackDeskSettings

logger = logging.getLogger(__name__)


class SlackDeskRuntime:
    """Holds Slack-owned state; proxies core services to a GatewayServices handle."""

    def __init__(
        self, services: "GatewayServices", config: dict[str, Any] | None = None
    ) -> None:
        self._services = services
        cfg = services.config

        creds = cfg.load_credentials()
        self._owner_id: str = creds.get(CRED_OWNER_ID, "") or services.owner_id

        # Slack behavioral config comes from the app's OWN store (SlackDeskSettings) —
        # core AppConfig defines no Slack config. get_settings() caches one live
        # instance; !channel/!config writes call reload_settings() so this stays fresh.
        from slack_desk_runtime.settings import load_tokens, reload_settings

        settings = reload_settings()

        # ONE token resolution, shared with the outbound half — see `load_tokens` (#952).
        # This read ``creds`` alone, which is ``.env`` + keychain + env and never the app
        # store: so the tokens the Configure form saves reached outbound and never reached
        # inbound, and a dashboard-only install was silently deaf.
        self._bot_token, self._app_token = load_tokens(config, creds)

        # Who may talk to this bot: the operator's allowlist — the dashboard's "Allowed
        # Users" AND the in-Slack Approve button, which both persist to ``allowed_users``
        # — plus the owner. #953: this was seeded from the owner ALONE, so an operator who
        # listed three people had authorized none of them.
        #
        # Fail-CLOSED by construction: no owner and an empty allowlist leaves this set
        # empty, and ``is_allowed_user`` then refuses everyone. Widening is only ever
        # explicit — an id the operator wrote down.
        self._allowed_users: set[str] = {
            u["slack_id"] for u in settings.allowed_users if u.get("slack_id")
        }
        if self._owner_id:
            self._allowed_users.add(self._owner_id)
        self._tracking_channels: set[str] = {
            c["channel_id"] for c in settings.tracking_channels if c.get("channel_id")
        }
        self._open_channels: set[str] = set(settings.open_channels)
        self._slack_desk_enabled: bool = bool(self._app_token and self._bot_token)
        self.slack_desk_command: str = settings.command

        # The live Slack client + socket connection (created in start()).
        self.slack_desk: RealSlackDeskClient | None = None
        self._socket_client: Any = None

        # Per-message identity + task bookkeeping (Slack-transport concerns).
        self._handler_tasks: set[asyncio.Task] = set()
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._pending_queue: dict[str, list] = {}
        self._self_bot_id: str = ""
        self._self_bot_id_ts: float = 0.0
        self._auth_test_failures: int = 0
        self._auth_test_lock: asyncio.Lock = asyncio.Lock()
        self._last_trigger_id: str = ""

    # --- Group B: transparently proxy core services to the gateway handle ---
    def __getattr__(self, name: str) -> Any:
        # Only called for attributes NOT found on the instance — i.e. the core
        # services (sessions, ctx_builder, conv_log, consolidator, cron_svc,
        # subagent_mgr, channel_history, dashboard_state) + config/owner_id.
        services = self.__dict__.get("_services")
        if services is not None and hasattr(services, name):
            return getattr(services, name)
        raise AttributeError(name)

    # Some moved code reads orch._cfg directly (private). Expose it as the
    # services' public config so those call sites resolve without change.
    @property
    def _cfg(self) -> Any:
        return self._services.config

    @property
    def settings(self) -> "SlackDeskSettings":
        """The app's live SlackDeskSettings (cached; refreshed by reload_settings())."""
        from slack_desk_runtime.settings import get_settings

        return get_settings()
