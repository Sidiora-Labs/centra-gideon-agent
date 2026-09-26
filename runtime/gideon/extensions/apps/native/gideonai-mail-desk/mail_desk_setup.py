"""The GideonAI mail desk's `gideon setup` step (manifest ``cli.setup``).

Registered via ``app.json`` → ``cli.setup: "mail_desk_setup:run"``. The core setup runner
(``gideon.app_cli.run_app_setup_steps``) imports ``run`` and calls it with a
:class:`gideon.sdk.cli.SetupContext` after the core steps. It writes ONLY app-owned homes:

- the IMAP/SMTP **passwords** → the shared credential store under this app's own keys
  ``MAIL_DESK_IMAP_PASS`` / ``MAIL_DESK_SMTP_PASS`` (``ctx.save_credential``);
- the non-secret hosts/ports/logins/folder/cadence → this app's ``ProviderSettings``.

Core config.json holds no mail-desk configuration, and who may talk is owned by the core
trust seam (``gideon pair mail-desk``), not by this app.

The step leads with **app-password** guidance because that is where a mail setup actually
fails: a personal account password is either rejected outright (Gmail, iCloud) or silently
blocked by a 2FA policy, and the resulting "authentication failed" reads as a typo. Known
hosts are offered as presets so the four hostname/port pairs a user would otherwise look up
are already filled in.
"""

from gideon.sdk.cli import SetupContext

from mail_desk_runtime.settings import (
    CRED_IMAP_PASS,
    CRED_SMTP_PASS,
    DEFAULT_IMAP_PORT,
    DEFAULT_POLL_SECS,
    DEFAULT_SMTP_PORT,
    SMTP_SSL,
    SMTP_STARTTLS,
    _VALID_ACTIVATIONS,
    _VALID_SMTP_SECURITY,
)

_APP = "gideonai-mail-desk"

#: Known-provider presets: label → (imap_host, imap_port, smtp_host, smtp_port,
#: smtp_security, app-password instructions). Every entry uses the provider's documented
#: app-password flow — never an account password.
PRESETS: dict[str, tuple[str, int, str, int, str, str]] = {
    "gmail": (
        "imap.gmail.com", 993, "smtp.gmail.com", 587, SMTP_STARTTLS,
        "Google Account → Security → 2-Step Verification → App passwords. "
        "Create one for 'Mail'; it is 16 characters with no spaces.",
    ),
    "fastmail": (
        "imap.fastmail.com", 993, "smtp.fastmail.com", 465, SMTP_SSL,
        "Fastmail → Settings → Privacy & Security → App Passwords → New App Password, "
        "scoped to 'Mail (IMAP/SMTP)'.",
    ),
    "icloud": (
        "imap.mail.me.com", 993, "smtp.mail.me.com", 587, SMTP_STARTTLS,
        "appleid.apple.com → Sign-In and Security → App-Specific Passwords.",
    ),
}


def run(ctx: SetupContext) -> None:
    """Prompt for the mailbox connection + app passwords, or skip the whole step."""
    ctx.print("── Mail Desk App ──\n")
    ctx.print(
        "  Converse with your agent by mail: write the bound mailbox, and its replies\n"
        "  thread back into the same conversation.\n"
        "  Use a DEDICATED mailbox, not your personal inbox — every message from a\n"
        "  paired sender becomes a turn, and the agent replies to the thread.\n"
        "  Authenticate with an APP PASSWORD, never your account password:\n"
        f"    · gmail    — {PRESETS['gmail'][5]}\n"
        f"    · fastmail — {PRESETS['fastmail'][5]}\n"
        f"    · icloud   — {PRESETS['icloud'][5]}\n"
        "  (OAuth2/XOAUTH2 is not supported yet — see the app README.)\n"
    )
    answer = ctx.input("  Configure the mail desk now? [Y/n]: ").strip().lower()
    if answer in ("n", "no"):
        ctx.print("  ⏭  Skipped. The mail desk will be disabled.\n")
        return

    preset = _choose_preset(ctx)
    _setup_connection(ctx, preset)
    _setup_passwords(ctx)
    _setup_behavior(ctx)
    ctx.print(
        "  ℹ️  Unknown senders are refused until paired: run 'gideon pair mail-desk'\n"
        "     for an 8-digit code, then reply to the agent with the code in the body.\n"
    )


def _choose_preset(ctx: SetupContext) -> tuple[str, int, str, int, str, str] | None:
    ctx.print(f"── Provider ──\n  Known: {', '.join(sorted(PRESETS))} (or blank for custom)\n")
    raw = ctx.input("  Provider [custom]: ").strip().lower()
    if not raw:
        return None
    preset = PRESETS.get(raw)
    if preset is None:
        ctx.print(f"  ⚠️  Unknown provider '{raw}' — entering hosts manually.\n")
        return None
    ctx.print(f"  ✅ Using {raw} defaults. App password: {preset[5]}\n")
    return preset


def _setup_connection(ctx: SetupContext, preset: tuple | None) -> None:
    cur = ctx.settings.load(_APP)
    d_imap_host = preset[0] if preset else str(cur.get("imap_host", ""))
    d_imap_port = preset[1] if preset else cur.get("imap_port", DEFAULT_IMAP_PORT)
    d_smtp_host = preset[2] if preset else str(cur.get("smtp_host", ""))
    d_smtp_port = preset[3] if preset else cur.get("smtp_port", DEFAULT_SMTP_PORT)
    d_security = preset[4] if preset else str(cur.get("smtp_security", SMTP_STARTTLS))

    ctx.print("── Mailbox ──\n")
    address = (
        ctx.input(f"  Mailbox address [{cur.get('address', '')}]: ").strip()
        or str(cur.get("address", ""))
    )
    imap_user = (
        ctx.input(f"  IMAP username [{cur.get('imap_user', '') or address}]: ").strip()
        or str(cur.get("imap_user", ""))
        or address
    )
    imap_host = ctx.input(f"  IMAP host [{d_imap_host}]: ").strip() or d_imap_host
    imap_port = _int_or(ctx, f"  IMAP port [{d_imap_port}]: ", d_imap_port)
    # Default from the port (993 is implicit TLS by convention) but still ask: a host on a
    # non-standard TLS port would otherwise be silently misconfigured, and without this
    # prompt a plain-IMAP setup is unreachable from the CLI at all.
    ssl_default = "y" if imap_port == DEFAULT_IMAP_PORT else "n"
    ssl_raw = ctx.input(f"  IMAP implicit SSL? [{ssl_default.upper()}/n]: ").strip().lower()
    imap_use_ssl = (ssl_raw or ssl_default) not in ("n", "no")
    folder = (
        ctx.input(f"  Folder to poll [{cur.get('folder', 'INBOX')}]: ").strip()
        or str(cur.get("folder", "INBOX"))
    )
    smtp_user = (
        ctx.input(f"  SMTP username [{cur.get('smtp_user', '') or imap_user}]: ").strip()
        or str(cur.get("smtp_user", ""))
        or imap_user
    )
    smtp_host = ctx.input(f"  SMTP host [{d_smtp_host}]: ").strip() or d_smtp_host
    smtp_port = _int_or(ctx, f"  SMTP port [{d_smtp_port}]: ", d_smtp_port)
    security = ctx.input(f"  SMTP security {sorted(_VALID_SMTP_SECURITY)} [{d_security}]: ")
    security = security.strip().lower() or d_security
    if security not in _VALID_SMTP_SECURITY:
        ctx.print(f"  ⚠️  Unknown mode — keeping '{d_security}'.")
        security = d_security

    ctx.settings.update(
        _APP,
        {
            "address": address, "imap_user": imap_user, "imap_host": imap_host,
            "imap_port": imap_port, "imap_use_ssl": imap_use_ssl, "folder": folder,
            "smtp_user": smtp_user, "smtp_host": smtp_host, "smtp_port": smtp_port,
            "smtp_security": security,
        },
    )
    ctx.print("  ✅ Mailbox connection saved.\n")


def _int_or(ctx: SetupContext, prompt: str, default: object) -> int:
    raw = ctx.input(prompt).strip()
    if not raw:
        try:
            return int(default)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return DEFAULT_IMAP_PORT
    try:
        return int(raw)
    except ValueError:
        ctx.print(f"  ⚠️  Not a number — keeping {default}.")
        try:
            return int(default)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return DEFAULT_IMAP_PORT


def _setup_passwords(ctx: SetupContext) -> None:
    ctx.print("── App passwords (credential store, never app config) ──\n")
    cur_imap = ctx.get_credential(CRED_IMAP_PASS)
    imap_pass = ctx.input(f"  IMAP app password{' [set]' if cur_imap else ''}: ").strip()
    if imap_pass:
        ctx.save_credential(CRED_IMAP_PASS, imap_pass)
        ctx.print("  ✅ IMAP password saved.\n")
    elif not cur_imap:
        ctx.print("  ⚠️  No IMAP password — inbound will stay offline until one is set.\n")

    cur_smtp = ctx.get_credential(CRED_SMTP_PASS)
    hint = " [set]" if cur_smtp else " [reuse IMAP]"
    smtp_pass = ctx.input(f"  SMTP app password{hint}: ").strip()
    if smtp_pass:
        ctx.save_credential(CRED_SMTP_PASS, smtp_pass)
        ctx.print("  ✅ SMTP password saved.\n")
    else:
        ctx.print("  ℹ️  Reusing the IMAP password for SMTP (usual for one app password).\n")


def _setup_behavior(ctx: SetupContext) -> None:
    cur = ctx.settings.load(_APP)
    ctx.print("── Behavior ──\n")
    poll = _int_or(
        ctx, f"  Poll interval seconds [{cur.get('poll_secs', DEFAULT_POLL_SECS)}]: ",
        cur.get("poll_secs", DEFAULT_POLL_SECS),
    )
    current_act = str(cur.get("dm_activation", "always"))
    raw = ctx.input(
        f"  Inbound activation {sorted(_VALID_ACTIVATIONS)} [{current_act}]: "
    ).strip().lower()
    activation = raw or current_act
    if activation not in _VALID_ACTIVATIONS:
        ctx.print(f"  ⚠️  Unknown mode — keeping '{current_act}'.")
        activation = current_act
    ctx.settings.update(_APP, {"poll_secs": poll, "dm_activation": activation})
    ctx.print(f"  ✅ Polling every {poll}s · inbound {activation}.\n")
