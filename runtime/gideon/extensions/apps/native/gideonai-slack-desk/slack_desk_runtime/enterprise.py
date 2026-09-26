"""Slack workspace origin binding.

Pins the gateway to the single workspace its bot token belongs to, so a
hot-swapped ``.env`` token can't redirect the bot to another workspace and
exfiltrate data. Two layers:

1. ``validate_enterprise()`` at gateway startup — calls ``auth.test`` and caches
   the workspace's ``team_id``. Succeeds for ANY workspace (personal or
   Enterprise Grid); it only fails if ``auth.test`` itself fails (bad token).
2. ``check_message_origin()`` on every incoming message — compares the event's
   ``team`` field against the cached value (zero-cost in-memory check).

NOTE: an earlier Enterprise-Grid *requirement* (reject any workspace without an
``enterprise_id``) has been removed — a personal Slack workspace is a first-class,
supported deployment. The origin binding below is the deployment-neutral protection
that actually matters, and it is retained.
"""

import logging

from gideon.sdk.channel import sel

logger = logging.getLogger(__name__)

# Cached at startup by validate_enterprise().  Checked per-message by
# check_message_origin().  Module-level — safe because the gateway runs
# in a single asyncio event loop.
_validated_team_id: str = ""
_validated_enterprise_id: str = ""


def validate_enterprise(
    bot_token: str,
    *,
    extra_ids: set[str] | None = None,
) -> bool:
    """Call ``auth.test`` and bind the gateway to the token's workspace.

    Caches the workspace ``team_id`` (and ``enterprise_id`` when present) so
    ``check_message_origin()`` can verify each incoming message without an API
    call. Succeeds for any workspace whose token authenticates; returns False
    only when ``auth.test`` fails. ``extra_ids`` is accepted for call-site
    compatibility but no longer gates acceptance.
    """
    global _validated_team_id, _validated_enterprise_id
    from slack_sdk.web import WebClient

    # Clear stale state so a failed re-validation is fail-closed.
    _validated_team_id = ""
    _validated_enterprise_id = ""

    try:
        client = WebClient(token=bot_token)
        resp = client.auth_test()
    except Exception:
        logger.exception("Slack workspace validation: auth.test failed")
        sel().log_api_access(
            caller="gateway",
            operation="slack.workspace_validation",
            outcome="error",
            source="startup",
            error="auth_test_failed",
        )
        return False

    enterprise_id = resp.get("enterprise_id", "")
    team_id = resp.get("team_id", "")
    team = resp.get("team", "")
    team_id_str = team_id or ""

    if not team_id_str:
        # No team_id from auth.test — can't bind origin, so fail closed.
        logger.error("Slack workspace validation: auth.test returned no team_id")
        sel().log_api_access(
            caller="gateway",
            operation="slack.workspace_validation",
            outcome="denied",
            source="startup",
            resources=f"team={team}",
            error="no_team_id",
        )
        return False

    # Bind to this workspace for per-message origin checks.
    _validated_team_id = team_id_str
    _validated_enterprise_id = enterprise_id

    logger.info(
        "Slack workspace validated: team=%s team_id=%s%s",
        team,
        team_id_str,
        f" enterprise_id={enterprise_id}" if enterprise_id else " (personal workspace)",
    )
    sel().log_api_access(
        caller="gateway",
        operation="slack.workspace_validation",
        outcome="allowed",
        source="startup",
        resources=f"team={team} team_id={team_id_str} enterprise_id={enterprise_id or 'none'}",
    )
    return True


def check_message_origin(event_team_id: str) -> bool:
    """Verify an incoming message's team_id matches the validated workspace.

    Zero-cost in-memory comparison — no API call.  Returns True if the
    message is from the validated workspace, False otherwise.

    If no team_id was cached (validation didn't run or failed), rejects
    all messages (fail-closed).
    """
    if not _validated_team_id:
        return False
    if not event_team_id:
        return False
    return event_team_id == _validated_team_id
