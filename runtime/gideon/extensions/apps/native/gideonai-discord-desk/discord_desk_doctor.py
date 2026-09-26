"""The `gideon doctor` probe for Discord Desk (manifest `cli.doctor`).

Registered via ``app.json`` → ``cli.doctor: "discord_desk_doctor:probe"``. The core
doctor runner (``gideon.app_cli.run_app_doctor_probes``) imports ``probe`` and calls
it (bounded by a timeout + exception guard), rendering the returned
``list[DoctorLine]`` as this bundle's doctor section. It checks the token in the
generic credential store, the application id in this bundle's own store, and the
owner id, with a pointer to the Channels-page Test action for the live
``GET /gateway/bot`` hello probe (which the bundle owns, not core's doctor — the
same division as Telegram's).
"""

from gideon.sdk.channel import CRED_OWNER_ID, AppConfig, ProviderSettings
from gideon.sdk.cli import DoctorLine

from discord_desk.settings import CRED_BOT_TOKEN

_APP = "gideonai-discord-desk"


def probe() -> list[DoctorLine]:
    creds = AppConfig.load().load_credentials()
    if not creds.get(CRED_BOT_TOKEN):
        return [
            DoctorLine(
                "status", "info",
                "not configured (dashboard-only mode) — run 'gideon setup' to add a bot token",
            )
        ]
    lines = [DoctorLine("token", "ok", "configured")]

    app_id = ProviderSettings.load(_APP).get("application_id") or ""
    if app_id:
        lines.append(DoctorLine("application id", "ok", str(app_id)))
    else:
        # Not fatal: the runtime never needs it (interactions carry their own token).
        # It only costs the setup step's invite URL, so warn rather than fail.
        lines.append(
            DoctorLine("application id", "warn", "not set — no bot invite URL can be printed")
        )

    owner = creds.get(CRED_OWNER_ID)
    if owner:
        lines.append(DoctorLine("owner", "ok", owner))
    else:
        lines.append(DoctorLine("owner", "warn", "GIDEON_OWNER_ID not set"))

    lines.append(
        DoctorLine(
            "gateway", "info",
            "use the Channels page → Discord → Test for the live gateway hello probe",
        )
    )
    lines.append(
        DoctorLine(
            "intent", "info",
            "the MESSAGE CONTENT privileged intent must be enabled in the Developer "
            "Portal or inbound messages arrive with empty content",
        )
    )
    return lines
