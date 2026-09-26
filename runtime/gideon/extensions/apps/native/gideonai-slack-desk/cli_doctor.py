"""The gideonai-slack-desk app's `gideon doctor` probe (manifest `cli.doctor`).

Registered via ``app.json`` → ``cli.doctor: "cli_doctor:probe"``. The core doctor
runner (``gideon.app_cli.run_app_doctor_probes``) imports ``probe`` and calls
it (bounded by a timeout + exception guard), rendering the returned
``list[DoctorLine]`` as this app's doctor section. Reproduces the presence check
core's doctor used to hardcode (plan 32 moved it here): token presence in the
generic credential store + owner id, with a hint to the Channels-page Test action
for live workspace validation (which the app owns, not core's doctor).
"""

from gideon.sdk.channel import (
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
    AppConfig,
)
from gideon.sdk.cli import DoctorLine


def probe() -> list[DoctorLine]:
    creds = AppConfig.load().load_credentials()
    has_tokens = bool(creds.get(CRED_SLACK_APP_TOKEN) and creds.get(CRED_SLACK_BOT_TOKEN))
    if not has_tokens:
        return [
            DoctorLine(
                "status", "info",
                "not configured (dashboard-only mode) — run 'gideon setup' to add tokens",
            )
        ]
    lines = [DoctorLine("tokens", "ok", "configured")]
    owner = creds.get(CRED_OWNER_ID)
    if owner:
        lines.append(DoctorLine("owner", "ok", owner))
    else:
        lines.append(DoctorLine("owner", "warn", "GIDEON_OWNER_ID not set"))
    lines.append(
        DoctorLine("workspace", "info", "use the Channels page → Slack → Test to verify the token")
    )
    return lines
