"""The gideonai-slack-desk app's `gideon setup` step (manifest `cli.setup`).

Registered via ``app.json`` → ``cli.setup: "cli_setup:run"``. The core setup
runner (``gideon.app_cli.run_app_setup_steps``) imports ``run`` and calls
it with a :class:`gideon.sdk.cli.SetupContext` after the core steps. This
is the slack-specific setup that used to live hardcoded in core's ``cli_setup.py``
(plan 32 PROVIDER-BOUNDARY-COMPLETION moved it here) — it reads/writes ONLY
app-owned homes: the generic credential store (via ``ctx.save_credential``, the
same ``SLACK_*`` keys the runtime reads) and this app's ``ProviderSettings`` (the
slash-command name). Core config.json holds no Slack config.
"""

from gideon.sdk.channel import (
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
)
from gideon.sdk.cli import SetupContext

_APP = "gideonai-slack-desk"


def _mask(val: str) -> str:
    return val[:8] + "…" if len(val) > 12 else val


def run(ctx: SetupContext) -> None:
    """Prompt for Slack tokens + owner ID (→ credential store) and the slash
    command name (→ this app's ProviderSettings). Empty input keeps the current
    value; declining skips the whole step (the channel stays disabled)."""
    _setup_tokens(ctx)
    _setup_slash_command(ctx)


def _setup_tokens(ctx: SetupContext) -> None:
    ctx.print("── GideonAI Slack Desk Credentials ──\n")
    ctx.print(
        "  Create a Slack app at https://api.slack.com/apps → 'From a manifest',\n"
        "  using this app's slack-desk-manifest.yaml (replace {{USERNAME}}),\n"
        "  then paste its tokens below.\n"
    )

    answer = ctx.input("  Configure Slack tokens? [Y/n]: ").strip().lower()
    if answer in ("n", "no"):
        ctx.print("  ⏭  Skipped. Slack connectivity will be disabled.\n")
        return

    cur_app = ctx.get_credential(CRED_SLACK_APP_TOKEN)
    cur_bot = ctx.get_credential(CRED_SLACK_BOT_TOKEN)
    cur_owner = ctx.get_credential(CRED_OWNER_ID)

    hint_app = f" [{_mask(cur_app)}]" if cur_app else ""
    hint_bot = f" [{_mask(cur_bot)}]" if cur_bot else ""
    hint_owner = f" [{cur_owner}]" if cur_owner else ""

    app_token = ctx.input(f"  App Token (xapp-...){hint_app}: ").strip() or cur_app
    bot_token = ctx.input(f"  Bot Token (xoxb-...){hint_bot}: ").strip() or cur_bot
    owner_id = ctx.input(f"  Your Slack Member ID{hint_owner}: ").strip() or cur_owner

    if not app_token or not bot_token:
        ctx.print("  ⚠️  Missing tokens — Slack connectivity will be disabled.\n")
        return

    ctx.save_credential(CRED_SLACK_APP_TOKEN, app_token)
    ctx.save_credential(CRED_SLACK_BOT_TOKEN, bot_token)
    if owner_id:
        ctx.save_credential(CRED_OWNER_ID, owner_id)
    ctx.print("  ✅ Credentials saved.\n")


def _setup_slash_command(ctx: SetupContext) -> None:
    current = ctx.settings.load(_APP).get("command") or "gideon"

    ctx.print("── Slash Command ──\n")
    raw = ctx.input(f"  Slash command name [{current}]: ").strip()
    if raw:
        raw = raw.lstrip("/").strip()
    if not raw:
        raw = current
    if not all(c.isalnum() or c in "-_" for c in raw):
        ctx.print("  ⚠️  Command name should only contain letters, numbers, hyphens, or underscores.")
        raw = current
    if len(raw) > 32:
        ctx.print("  ⚠️  Command name too long (max 32 chars).")
        raw = current

    ctx.settings.update(_APP, {"command": raw})
    ctx.print(f"  ✅ Slash command: /{raw}\n")
