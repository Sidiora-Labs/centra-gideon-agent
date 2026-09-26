"""discord_desk_setup / discord_desk_doctor driven end to end against the isolated
home.

The bar is "setup/doctor configure end to end", so these tests drive the real entry
points core's runners call (``discord_desk_setup:run`` with a real
:class:`SetupContext`, ``discord_desk_doctor:probe``) and assert the values actually
landed in the app store — not that the functions merely returned. The credential
store is faked to a dict here (that IS the seam core hands the step:
``get_credential`` / ``save_credential`` callables), while the app store is the real
``ProviderSettings`` writing under the tmp ``GIDEON_HOME``."""


from __future__ import annotations

import pytest
from gideon.sdk.channel import CRED_OWNER_ID, ProviderSettings, save_credential
from gideon.sdk.cli import SetupContext

import discord_desk_doctor
import discord_desk_setup
from discord_desk_setup import (
    INVITE_PERMISSIONS,
    PERM_ADD_REACTIONS,
    PERM_ATTACH_FILES,
    PERM_READ_MESSAGE_HISTORY,
    PERM_SEND_MESSAGES,
    PERM_SEND_MESSAGES_IN_THREADS,
    PERM_VIEW_CHANNEL,
    invite_url,
)
from discord_desk.settings import CRED_BOT_TOKEN

_APP = "gideonai-discord-desk"


class Ctx:
    """A SetupContext wired to a dict credential store + a scripted input queue."""

    def __init__(self, answers: list[str]):
        self.printed: list[str] = []
        self.creds: dict[str, str] = {}
        self._answers = iter(answers)
        self.ctx = SetupContext(
            app_name=_APP,
            get_credential=lambda k: self.creds.get(k, ""),
            save_credential=lambda k, v: self.creds.__setitem__(k, v),
            settings=ProviderSettings,
            print=self.printed.append,
            input=lambda prompt: next(self._answers, ""),
        )

    @property
    def transcript(self) -> str:
        return "".join(self.printed)


class TestSetupHappyPath:
    def test_configures_every_field_end_to_end(self):
        c = Ctx(["y", "MTIz.tok.secret", "998877665544332211", "42", "mention"])
        discord_desk_setup.run(c.ctx)

        # the secret went to the credential store under the app's OWN key
        assert c.creds[CRED_BOT_TOKEN] == "MTIz.tok.secret"
        assert c.creds[CRED_OWNER_ID] == "42"
        # the non-secrets went to the app's own store, never core config.json
        stored = ProviderSettings.load(_APP)
        assert stored["application_id"] == "998877665544332211"
        assert stored["dm_activation"] == "mention"

    def test_prints_the_invite_url_with_the_real_application_id(self):
        c = Ctx(["y", "tok", "12345", "42", "always"])
        discord_desk_setup.run(c.ctx)
        assert "oauth2/authorize" in c.transcript
        assert "client_id=12345" in c.transcript
        assert f"permissions={INVITE_PERMISSIONS}" in c.transcript

    def test_warns_about_the_privileged_intent(self):
        """The #1 silent failure — setup must say so out loud."""
        c = Ctx(["y", "tok", "1", "42", "always"])
        discord_desk_setup.run(c.ctx)
        assert "MESSAGE CONTENT" in c.transcript

    def test_empty_input_keeps_existing_values(self):
        c = Ctx(["y", "tok", "111", "42", "always"])
        discord_desk_setup.run(c.ctx)
        # Re-run answering nothing: the previous values must survive.
        c2 = Ctx(["y", "", "", "", ""])
        c2.creds = dict(c.creds)
        discord_desk_setup.run(c2.ctx)
        assert c2.creds[CRED_BOT_TOKEN] == "tok"
        assert ProviderSettings.load(_APP)["application_id"] == "111"

    def test_unknown_activation_keeps_the_current_value(self):
        c = Ctx(["y", "tok", "1", "42", "sideways"])
        discord_desk_setup.run(c.ctx)
        assert ProviderSettings.load(_APP)["dm_activation"] == "always"
        assert "Unknown mode" in c.transcript


class TestSetupDeclinePaths:
    def test_declining_persists_nothing(self):
        c = Ctx(["n"])
        discord_desk_setup.run(c.ctx)
        assert c.creds == {}
        assert ProviderSettings.load(_APP) == {}
        assert "Skipped" in c.transcript

    def test_empty_token_aborts_without_persisting(self):
        c = Ctx(["y", "", "", ""])
        discord_desk_setup.run(c.ctx)
        assert c.creds == {}
        assert "No token" in c.transcript

    def test_no_application_id_still_saves_the_token(self):
        """The app id is optional — the runtime never needs it."""
        c = Ctx(["y", "tok", "", "42", "always"])
        discord_desk_setup.run(c.ctx)
        assert c.creds[CRED_BOT_TOKEN] == "tok"
        assert "no invite URL" in c.transcript


class TestInvitePermissions:
    def test_bits_are_exactly_the_capabilities_the_code_uses(self):
        """Minimum permissions: the invite must not request anything broader."""
        assert INVITE_PERMISSIONS == (
            PERM_VIEW_CHANNEL
            | PERM_SEND_MESSAGES
            | PERM_SEND_MESSAGES_IN_THREADS
            | PERM_ADD_REACTIONS
            | PERM_ATTACH_FILES
            | PERM_READ_MESSAGE_HISTORY
        )
        assert INVITE_PERMISSIONS == 274878008384

    @pytest.mark.parametrize(
        "bit,expected",
        [(PERM_ADD_REACTIONS, 1 << 6), (PERM_VIEW_CHANNEL, 1 << 10),
         (PERM_SEND_MESSAGES, 1 << 11), (PERM_ATTACH_FILES, 1 << 15),
         (PERM_READ_MESSAGE_HISTORY, 1 << 16),
         (PERM_SEND_MESSAGES_IN_THREADS, 1 << 38)],
    )
    def test_documented_bit_positions(self, bit, expected):
        assert bit == expected

    def test_no_administrator_bit(self):
        """Requesting Administrator (1<<3) would be the classic over-ask."""
        assert not INVITE_PERMISSIONS & (1 << 3)

    def test_url_shape(self):
        assert invite_url("9").startswith("https://discord.com/oauth2/authorize")
        assert "scope=bot" in invite_url("9")

    def test_no_url_without_an_application_id(self):
        assert invite_url("") == ""


class TestDoctor:
    def test_unconfigured_reports_a_single_info_line(self):
        lines = discord_desk_doctor.probe()
        assert len(lines) == 1
        assert lines[0].status == "info"
        assert "not configured" in lines[0].detail

    def test_configured_reports_ok_for_each_field(self):
        save_credential(CRED_BOT_TOKEN, "tok")
        save_credential(CRED_OWNER_ID, "42")
        ProviderSettings.save(_APP, {"application_id": "12345"})
        status = {line.label: line.status for line in discord_desk_doctor.probe()}
        assert status["token"] == "ok"
        assert status["application id"] == "ok"
        assert status["owner"] == "ok"

    def test_missing_application_id_warns_but_does_not_fail(self):
        """It only costs the invite URL — the runtime doesn't need it."""
        save_credential(CRED_BOT_TOKEN, "tok")
        status = {line.label: line.status for line in discord_desk_doctor.probe()}
        assert status["application id"] == "warn"
        assert "fail" not in status.values()

    def test_missing_owner_warns(self):
        save_credential(CRED_BOT_TOKEN, "tok")
        status = {line.label: line.status for line in discord_desk_doctor.probe()}
        assert status["owner"] == "warn"

    def test_points_at_the_live_gateway_probe_and_the_intent(self):
        """The app owns the live probe, not core's doctor — same split as Telegram."""
        save_credential(CRED_BOT_TOKEN, "tok")
        details = {line.label: line.detail for line in discord_desk_doctor.probe()}
        assert "Test" in details["gateway"]
        assert "MESSAGE CONTENT" in details["intent"]

    def test_token_value_is_never_printed(self):
        """A doctor section is pasted into issues — it must not leak the secret."""
        save_credential(CRED_BOT_TOKEN, "super.secret.token")
        rendered = " ".join(f"{line.label}{line.detail}" for line in discord_desk_doctor.probe())
        assert "super.secret.token" not in rendered
