"""Slack user allowlist and tracking-channel management.

Handles two owner-approval workflows:

1. **User allowlist** — when a user joins a tracked channel
   (``member_joined_channel``) or is nominated via ``/gideon @user``,
   the owner gets a DM with Allow / Deny buttons.
2. **Tracking channel** — ``/gideon #channel`` sends an Add / Ignore
   prompt to the owner.  Approved channels are persisted to the app's own
   config store (SlackDeskSettings).

Both flows share the same config persistence helpers so changes survive
gateway restarts.
"""

import logging
from typing import TYPE_CHECKING

from gideon.sdk.channel import AppConfig
from gideon.sdk.channel import (
    dashboard_origin,
    devspaces_proxy_url,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from gideon.sdk.channel import generate_token
from gideon.sdk.channel import sel
from slack_desk_runtime.handler import is_tracked_channel

if TYPE_CHECKING:
    from slack_desk_runtime.client import SlackDeskClientOps

logger = logging.getLogger(__name__)

# Block Kit action IDs shared with the interaction router
ACTION_ALLOWLIST_APPROVE = "allowlist_approve"
ACTION_ALLOWLIST_DENY = "allowlist_deny"
ACTION_TRACK_APPROVE = "track_channel_approve"
ACTION_TRACK_DENY = "track_channel_deny"


# ---------------------------------------------------------------------------
# Owner prompts — builds the Allow/Deny DMs
# ---------------------------------------------------------------------------


async def _send_prompt(
    slack_desk: "SlackDeskClientOps",
    owner_id: str,
    text: str,
    approve_label: str,
    deny_label: str,
    approve_action: str,
    deny_action: str,
    value: str,
    fallback: str,
) -> None:
    """Build a two-button Slack prompt and DM it to the owner."""
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": approve_label},
                    "style": "primary",
                    "action_id": approve_action,
                    "value": value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": deny_label},
                    "style": "danger",
                    "action_id": deny_action,
                    "value": value,
                },
            ],
        },
    ]
    try:
        dm = await slack_desk.open_dm(owner_id)
        await slack_desk.post_blocks(dm, blocks, fallback)
    except Exception:
        logger.exception("Failed to send prompt: %s", fallback)


async def prompt_track_channel(
    slack_desk: "SlackDeskClientOps",
    owner_id: str,
    channel_id: str,
    channel_name: str = "",
) -> None:
    """Send a Track / Ignore prompt to the owner for *channel_id*.

    When the channel is already tracked the prompt offers to keep or
    remove it instead of add/ignore.
    """
    if not channel_id:
        return

    already = is_tracked_channel(channel_id)
    logger.info(
        "track channel prompt: channel=%s (%s) already=%s",
        channel_id,
        channel_name,
        already,
    )

    if already:
        text = f"📡 <#{channel_id}> is currently tracked.\nKeep tracking or remove?"
        approve_label = "✅ Keep"
        deny_label = "🚫 Remove"
    else:
        text = f"📡 Track <#{channel_id}> for new member allowlist prompts?"
        approve_label = "✅ Track"
        deny_label = "🚫 Ignore"

    await _send_prompt(
        slack_desk, owner_id, text, approve_label, deny_label,
        ACTION_TRACK_APPROVE, ACTION_TRACK_DENY,
        f"{channel_id}:{channel_name}", "Track channel — prompt",
    )


# ---------------------------------------------------------------------------
# Dashboard presigned link — always sent via DM, never in a channel
# ---------------------------------------------------------------------------


async def send_dashboard_link(
    slack_desk: "SlackDeskClientOps",
    user_id: str,
    ttl: int = 3600,
) -> str:
    """Generate a presigned dashboard URL and DM it to *user_id*.

    Returns the generated URL (for logging), or an empty string on failure.
    The link is always sent as a DM to prevent token leakage in channels.

    The URL must be clicked within 5 minutes. Once opened, the session
    cookie lasts for *ttl* seconds (capped at 6 hours).
    """
    from gideon.sdk.channel import LINK_WINDOW_SECS, MAX_SESSION_TTL_SECS

    session_ttl = min(ttl, MAX_SESSION_TTL_SECS)
    cfg = AppConfig.load()
    configured_host, port = parse_dashboard_url(cfg.dashboard.url)
    local_only = is_local_bind(resolve_bind_host())
    host = resolve_dashboard_host(local_only, configured_host)

    token = generate_token(user_id, session_ttl)
    origin = dashboard_origin(cfg.dashboard.url)
    url = f"{origin}/?token={token}" if origin else f"http://{host}:{port}/?token={token}"

    # Dev proxy: also provide proxy URL
    proxy_line = ""
    proxy = devspaces_proxy_url(port)
    if proxy:
        proxy_line = f"\n🔗 <{proxy}/?token={token}|Open via DevSpaces Proxy>"

    link_mins = LINK_WINDOW_SECS // 60
    session_mins = session_ttl // 60
    try:
        dm = await slack_desk.open_dm(user_id)
        await slack_desk.post_message(
            dm,
            f"🔗 <{url}|Open Dashboard>{proxy_line}\n"
            f"⏱ Click within {link_mins}m · session lasts {session_mins}m",
        )
        sel().log_api_access(
            caller=user_id,
            operation="slack.dashboard_token",
            outcome="ok",
            resources=f"ttl={session_ttl}",
        )
    except Exception:
        try:
            sel().log_api_access(
                caller=user_id,
                operation="slack.dashboard_token",
                outcome="error",
                resources=f"ttl={session_ttl}",
            )
        except Exception:
            pass
        logger.exception("Failed to DM dashboard link to %s", user_id)
        return ""

    return url


# ---------------------------------------------------------------------------
# Config persistence — the app's OWN store (SlackDeskSettings home), not core config.
# ---------------------------------------------------------------------------


def persist_allowed_user(user_id: str, name: str = "", *, remove: bool = False) -> None:
    """Add or remove *user_id* in the app store's ``allowed_users``."""
    from slack_desk_runtime.settings import persist_list_entry, reload_settings

    persist_list_entry("allowed_users", "slack_id", user_id, remove=remove, name=name)
    reload_settings()
    # EA-7 write-through: the guarded inbound door consults core's channel_trust
    # store, so the owner's Allow/Deny ruling must land there too, not only in
    # SlackDeskSettings — otherwise the two stores drift and the door rules on stale data.
    try:
        from gideon.sdk.channel import apply_trust_action

        apply_trust_action("deny" if remove else "allow", "slack", user_id, name)
    except Exception:
        logger.warning("channel_trust write-through failed for user %s", user_id, exc_info=True)


def persist_tracking_channel(channel_id: str, name: str = "", *, remove: bool = False) -> None:
    """Add or remove *channel_id* in the app store's ``tracking_channels``."""
    from slack_desk_runtime.settings import persist_list_entry, reload_settings

    persist_list_entry("tracking_channels", "channel_id", channel_id, remove=remove, name=name)
    reload_settings()
    # EA-7 write-through — same reason as persist_allowed_user above.
    try:
        from gideon.sdk.channel import track, untrack

        if remove:
            untrack("slack", channel_id)
        else:
            track("slack", channel_id, name)
    except Exception:
        logger.warning(
            "channel_trust write-through failed for channel %s", channel_id, exc_info=True
        )


def sync_channel_trust(owner_id: str, tracking_channels: "set[str]") -> None:
    """Mirror the app store's trust data into core's channel_trust store (EA-7).

    The guarded inbound door consults core's per-provider trust store, not this
    app's SlackDeskSettings. Slack's posture is owner-only, so the mirror is small:
    the owner is the ONE allowed sender, and each tracked channel is tracked.
    Writes only what is missing — allow_sender/track emit SEL audit rows, so an
    unconditional re-write per boot would be audit spam.
    """
    from gideon.sdk.channel import (
        allow_sender,
        is_allowed_sender,
        is_tracked_channel,
        track,
    )

    try:
        if owner_id and not is_allowed_sender("slack", owner_id):
            allow_sender("slack", owner_id, via="owner")
        for cid in tracking_channels:
            if cid and not is_tracked_channel("slack", cid):
                track("slack", cid)
    except Exception:
        logger.warning("channel_trust mirror failed", exc_info=True)
