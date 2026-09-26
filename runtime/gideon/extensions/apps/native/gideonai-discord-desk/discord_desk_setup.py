"""The `gideon setup` step for Discord Desk (manifest `cli.setup`).

Registered via ``app.json`` → ``cli.setup: "discord_desk_setup:run"``. The core
setup runner (``gideon.app_cli.run_app_setup_steps``) imports ``run`` and calls it
with a :class:`gideon.sdk.cli.SetupContext` after the core steps. This is the
Discord-specific setup: it reads/writes ONLY app-owned homes — the generic
credential store (via ``ctx.save_credential``, this bundle's own
``DISCORD_BOT_TOKEN`` key) and this bundle's ``ProviderSettings`` (the application
id + DM-activation posture). Core config.json holds no Discord config; who may talk
is owned by the core trust seam.

The step ends by printing the OAuth2 invite URL with the permission bits already
computed, because "invite the bot" is where a Discord setup most often goes wrong:
a bot with a valid token that was never invited, or invited without Send Messages,
looks identical to a broken token from the dashboard.
"""

from gideon.sdk.channel import CRED_OWNER_ID
from gideon.sdk.cli import SetupContext

from discord_desk.settings import (
    ACTIVATION_ALWAYS,
    CRED_BOT_TOKEN,
    _VALID_ACTIVATIONS,
)

_APP = "gideonai-discord-desk"

# ── The bot permission bits this app actually uses (Discord "Permissions") ──
# Declared as named bits and summed, so the printed invite URL asks for exactly the
# permissions the code exercises — nothing broader, per the minimum-permissions bar.
PERM_VIEW_CHANNEL = 1 << 10  # read the channels it's in
PERM_SEND_MESSAGES = 1 << 11  # deliver_text / streams
PERM_SEND_MESSAGES_IN_THREADS = 1 << 38  # a thread is a channel; replies land there
PERM_ADD_REACTIONS = 1 << 6  # DiscordDeskDelivery.add_reaction
PERM_ATTACH_FILES = 1 << 15  # upload_attachment
PERM_READ_MESSAGE_HISTORY = 1 << 16  # edit/react to a message it didn't just send

#: The permission integer the invite URL requests.
INVITE_PERMISSIONS = (
    PERM_VIEW_CHANNEL
    | PERM_SEND_MESSAGES
    | PERM_SEND_MESSAGES_IN_THREADS
    | PERM_ADD_REACTIONS
    | PERM_ATTACH_FILES
    | PERM_READ_MESSAGE_HISTORY
)


def invite_url(application_id: str) -> str:
    """The OAuth2 URL that invites this bot with exactly :data:`INVITE_PERMISSIONS`.

    Returns "" without an application id — printing a URL with a blank client_id
    would just hand the user a Discord error page."""
    if not application_id:
        return ""
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={application_id}&scope=bot&permissions={INVITE_PERMISSIONS}"
    )


def _mask(val: str) -> str:
    return val[:8] + "…" if len(val) > 12 else val


def run(ctx: SetupContext) -> None:
    """Prompt for the bot token (→ credential store) plus the application id, owner
    Discord user id and DM activation mode (→ this app's ProviderSettings). Empty
    input keeps the current value; declining skips the whole step (the channel stays
    disabled)."""
    if not _setup_credentials(ctx):
        return
    _setup_activation(ctx)
    _print_invite(ctx)


def _setup_credentials(ctx: SetupContext) -> bool:
    ctx.print("── Discord Desk App Credentials ──\n")
    ctx.print(
        "  Create a bot at https://discord.com/developers/applications:\n"
        "    1. New Application → name it → copy the Application ID\n"
        "       (General Information; it is public, not a secret)\n"
        "    2. Bot → Reset Token → copy the token (shown once)\n"
        "    3. Bot → Privileged Gateway Intents → enable MESSAGE CONTENT INTENT.\n"
        "       Without it every message arrives with EMPTY content — the bot will\n"
        "       look alive and ignore everything you say.\n"
        "    4. Invite the bot to a server with the URL this step prints at the end.\n"
        "  Your own Discord user id: enable Settings → Advanced → Developer Mode,\n"
        "  then right-click your name → Copy User ID.\n"
    )

    answer = ctx.input("  Configure the Discord bot? [Y/n]: ").strip().lower()
    if answer in ("n", "no"):
        ctx.print("  ⏭  Skipped. The Discord Desk transport stays offline.\n")
        return False

    cur_token = ctx.get_credential(CRED_BOT_TOKEN)
    cur_owner = ctx.get_credential(CRED_OWNER_ID)
    cur_app_id = ctx.settings.load(_APP).get("application_id") or ""
    hint_token = f" [{_mask(cur_token)}]" if cur_token else ""
    hint_owner = f" [{cur_owner}]" if cur_owner else ""
    hint_app = f" [{cur_app_id}]" if cur_app_id else ""

    token = ctx.input(f"  Bot Token{hint_token}: ").strip() or cur_token
    app_id = ctx.input(f"  Application ID{hint_app}: ").strip() or cur_app_id
    owner_id = ctx.input(f"  Your Discord user ID{hint_owner}: ").strip() or cur_owner

    if not token:
        ctx.print("  ⚠️  No token — the Discord Desk transport stays offline.\n")
        return False

    ctx.save_credential(CRED_BOT_TOKEN, token)
    if owner_id:
        ctx.save_credential(CRED_OWNER_ID, owner_id)
    if app_id:
        ctx.settings.update(_APP, {"application_id": app_id})
    ctx.print("  ✅ Credentials saved.\n")
    return True


def _setup_activation(ctx: SetupContext) -> None:
    current = ctx.settings.load(_APP).get("dm_activation") or ACTIVATION_ALWAYS

    ctx.print("── DM Activation ──\n")
    ctx.print(
        f"  How should the bot respond in DMs? Options: {', '.join(sorted(_VALID_ACTIVATIONS))}\n"
    )
    raw = ctx.input(f"  DM activation [{current}]: ").strip().lower()
    if not raw:
        raw = current
    if raw not in _VALID_ACTIVATIONS:
        ctx.print(f"  ⚠️  Unknown mode — keeping '{current}'.")
        raw = current

    ctx.settings.update(_APP, {"dm_activation": raw})
    ctx.print(f"  ✅ DM activation: {raw}\n")


def _print_invite(ctx: SetupContext) -> None:
    app_id = ctx.settings.load(_APP).get("application_id") or ""
    url = invite_url(app_id)
    ctx.print("── Invite the bot ──\n")
    if not url:
        ctx.print(
            "  ⚠️  No Application ID saved, so no invite URL. Re-run setup with the\n"
            "     ID from the Developer Portal's General Information page.\n"
        )
        return
    ctx.print(
        f"  Open this URL and pick the server to add the bot to:\n    {url}\n"
        f"  It requests permission bits {INVITE_PERMISSIONS} — view channels, send\n"
        "  messages (incl. threads), add reactions, attach files, read history.\n"
        "  Then track the channels you want it active in from the Channels page;\n"
        "  untracked server channels are ignored, and DMs need pairing\n"
        "  ('gideon pair discord').\n"
    )
