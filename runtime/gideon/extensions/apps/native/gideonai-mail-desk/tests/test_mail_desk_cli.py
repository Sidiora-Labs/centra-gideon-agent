"""cli_setup / cli_doctor driven end to end against the isolated home.

The atom's bar is "setup/doctor (IMAP/SMTP hosts + app-password guidance, probe =
login+select) configure end to end", so these tests drive the real entry points core's
runners call (``cli_setup:run`` with a real :class:`SetupContext`, ``cli_doctor:probe``)
and assert the values actually landed in the app store and the credential store — not
that the functions merely returned.

The credential store is faked to a dict here (that IS the seam core hands the step:
``get_credential`` / ``save_credential`` callables), while the app store is the real
``ProviderSettings`` writing under the tmp ``GIDEON_HOME``. The doctor's live socket
probes are patched — this suite never opens a connection.
"""

from __future__ import annotations

import pytest

from gideon.sdk.channel import ProviderSettings, save_credential
from gideon.sdk.cli import SetupContext

import cli_doctor
import cli_setup
from cli_setup import PRESETS
from mail_desk_runtime.settings import CRED_IMAP_PASS, CRED_SMTP_PASS

_APP = "gideonai-mail-desk"


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


#: The scripted answer order for a gmail-preset run. The named indices below are used by
#: the "bad input" tests to replace precisely one answer, so a prompt-order change fails
#: loudly at one place instead of silently shifting every test's inputs.
A_PROVIDER = 1
A_IMAP_PORT = 5
A_IMAP_SSL = 6
A_SECURITY = 11
A_IMAP_PASS = 12
A_SMTP_PASS = 13
A_ACTIVATION = 15


def _gmail_answers() -> list[str]:
    # confirm, provider, address, imap user, imap host, imap port, imap ssl, folder,
    # smtp user, smtp host, smtp port, security, imap password, smtp password,
    # poll secs, activation
    return [
        "y", "gmail", "bot@gmail.com", "", "", "", "", "", "", "", "", "",
        "imap-app-pw", "", "90", "always",
    ]


class TestSetupHappyPath:
    def test_a_preset_run_configures_everything_end_to_end(self):
        c = Ctx(_gmail_answers())
        cli_setup.run(c.ctx)

        stored = ProviderSettings.load(_APP)
        assert stored["imap_host"] == "imap.gmail.com"
        assert stored["imap_port"] == 993
        assert stored["smtp_host"] == "smtp.gmail.com"
        assert stored["smtp_port"] == 587
        assert stored["smtp_security"] == "starttls"
        assert stored["address"] == "bot@gmail.com"
        # An empty login answer inherits the address / the imap login.
        assert stored["imap_user"] == "bot@gmail.com"
        assert stored["smtp_user"] == "bot@gmail.com"
        assert stored["folder"] == "INBOX"
        assert stored["poll_secs"] == 90
        assert stored["dm_activation"] == "always"

        # The secret went to the CREDENTIAL store under the app's own key…
        assert c.creds[CRED_IMAP_PASS] == "imap-app-pw"
        # …and never into the app store.
        assert "imap-app-pw" not in str(stored)

    def test_the_configuration_is_immediately_loadable(self):
        """End to end means the runtime can read back what setup wrote."""
        from mail_desk_runtime.settings import MailDeskSettings

        cli_setup.run(Ctx(_gmail_answers()).ctx)
        settings = MailDeskSettings.load()
        assert settings.inbound_configured is True
        assert settings.outbound_configured is True
        assert settings.mailbox_address == "bot@gmail.com"

    def test_fastmail_preset_uses_implicit_ssl(self):
        answers = _gmail_answers()
        answers[A_PROVIDER] = "fastmail"
        cli_setup.run(Ctx(answers).ctx)
        stored = ProviderSettings.load(_APP)
        assert stored["smtp_host"] == "smtp.fastmail.com"
        assert stored["smtp_port"] == 465
        assert stored["smtp_security"] == "ssl"

    def test_the_answer_index_constants_match_the_prompt_order(self):
        """The bad-input tests replace precisely one scripted answer by index, so a prompt
        inserted in the middle must fail HERE (loudly, once) instead of silently shifting
        every other test's inputs onto the wrong prompts."""
        answers = _gmail_answers()
        assert answers[A_PROVIDER] == "gmail"
        assert answers[A_IMAP_PASS] == "imap-app-pw"
        assert answers[A_ACTIVATION] == "always"
        # Position-only answers: the port/ssl/security/smtp-pass slots are blank defaults.
        for idx in (A_IMAP_PORT, A_IMAP_SSL, A_SECURITY, A_SMTP_PASS):
            assert answers[idx] == ""
        assert len(answers) == 16

    def test_implicit_ssl_can_be_declined_for_a_plain_imap_host(self):
        """Without this prompt a plain-IMAP (port 143) mailbox is unreachable from the
        CLI even though the Configure form exposes the flag."""
        answers = _gmail_answers()
        answers[A_IMAP_PORT] = "143"
        answers[A_IMAP_SSL] = "n"
        cli_setup.run(Ctx(answers).ctx)
        stored = ProviderSettings.load(_APP)
        assert stored["imap_port"] == 143
        assert stored["imap_use_ssl"] is False

    def test_ssl_defaults_on_for_the_standard_port(self):
        cli_setup.run(Ctx(_gmail_answers()).ctx)
        assert ProviderSettings.load(_APP)["imap_use_ssl"] is True

    def test_ssl_defaults_off_for_a_non_standard_port(self):
        answers = _gmail_answers()
        answers[A_IMAP_PORT] = "143"
        cli_setup.run(Ctx(answers).ctx)  # blank ssl answer takes the port-derived default
        assert ProviderSettings.load(_APP)["imap_use_ssl"] is False

    def test_custom_hosts_when_no_preset_is_chosen(self):
        c = Ctx(
            [
                "y", "", "bot@corp.test", "svc@corp.test", "mail.corp.test", "143",
                "n", "Agent", "svc@corp.test", "relay.corp.test", "25", "plain",
                "pw", "", "60", "always",
            ]
        )
        cli_setup.run(c.ctx)
        stored = ProviderSettings.load(_APP)
        assert stored["imap_host"] == "mail.corp.test"
        assert stored["imap_port"] == 143
        assert stored["imap_user"] == "svc@corp.test"
        assert stored["folder"] == "Agent"
        assert stored["smtp_host"] == "relay.corp.test"
        assert stored["smtp_port"] == 25
        assert stored["smtp_security"] == "plain"

    def test_a_separate_smtp_password_is_saved_under_its_own_key(self):
        answers = _gmail_answers()
        answers[A_SMTP_PASS] = "smtp-only-pw"
        c = Ctx(answers)
        cli_setup.run(c.ctx)
        assert c.creds[CRED_IMAP_PASS] == "imap-app-pw"
        assert c.creds[CRED_SMTP_PASS] == "smtp-only-pw"

    def test_a_blank_smtp_password_reuses_the_imap_one(self):
        c = Ctx(_gmail_answers())
        cli_setup.run(c.ctx)
        assert CRED_SMTP_PASS not in c.creds
        assert "Reusing the IMAP password" in c.transcript


class TestSetupGuidance:
    def test_app_password_instructions_are_printed_for_every_preset(self):
        c = Ctx(["n"])
        cli_setup.run(c.ctx)
        transcript = c.transcript
        assert "APP PASSWORD" in transcript
        for label in PRESETS:
            assert label in transcript
        assert "App passwords" in transcript  # the Google flow
        assert "App-Specific Passwords" in transcript  # the Apple flow

    def test_the_dedicated_mailbox_warning_is_printed(self):
        c = Ctx(["n"])
        cli_setup.run(c.ctx)
        assert "DEDICATED mailbox" in c.transcript

    def test_the_oauth2_deferral_is_stated_out_loud(self):
        c = Ctx(["n"])
        cli_setup.run(c.ctx)
        assert "OAuth2" in c.transcript

    def test_the_pairing_instruction_is_printed_on_success(self):
        c = Ctx(_gmail_answers())
        cli_setup.run(c.ctx)
        assert "gideon pair mail-desk" in c.transcript


class TestSetupDeclineAndBadInput:
    def test_declining_writes_nothing(self):
        c = Ctx(["n"])
        cli_setup.run(c.ctx)
        assert ProviderSettings.load(_APP) == {}
        assert c.creds == {}
        assert "Skipped" in c.transcript

    def test_an_unknown_provider_falls_through_to_manual_entry(self):
        answers = _gmail_answers()
        answers[A_PROVIDER] = "hotmail-ish"
        c = Ctx(answers)
        cli_setup.run(c.ctx)
        assert "Unknown provider" in c.transcript

    def test_a_non_numeric_port_keeps_the_default(self):
        answers = _gmail_answers()
        answers[A_IMAP_PORT] = "nine-nine-three"
        c = Ctx(answers)
        cli_setup.run(c.ctx)
        assert ProviderSettings.load(_APP)["imap_port"] == 993
        assert "Not a number" in c.transcript

    def test_an_unknown_security_mode_keeps_the_preset(self):
        answers = _gmail_answers()
        answers[A_SECURITY] = "magic-tls"
        c = Ctx(answers)
        cli_setup.run(c.ctx)
        assert ProviderSettings.load(_APP)["smtp_security"] == "starttls"
        assert "Unknown mode" in c.transcript

    def test_an_unknown_activation_keeps_the_current(self):
        answers = _gmail_answers()
        answers[A_ACTIVATION] = "occasionally"
        c = Ctx(answers)
        cli_setup.run(c.ctx)
        assert ProviderSettings.load(_APP)["dm_activation"] == "always"

    def test_a_missing_password_warns_rather_than_silently_disabling(self):
        answers = _gmail_answers()
        answers[A_IMAP_PASS] = ""
        c = Ctx(answers)
        cli_setup.run(c.ctx)
        assert "No IMAP password" in c.transcript

    def test_a_non_interactive_run_does_not_crash(self):
        """``SetupContext.input`` returns "" when non-interactive; every prompt must treat
        empty as keep/skip instead of raising."""
        c = Ctx([])
        cli_setup.run(c.ctx)  # all-empty answers
        assert c.transcript

    def test_re_running_setup_keeps_existing_values_on_empty_input(self):
        cli_setup.run(Ctx(_gmail_answers()).ctx)
        # Second run: confirm, then accept every default.
        c2 = Ctx(["y"])
        c2.creds[CRED_IMAP_PASS] = "imap-app-pw"
        cli_setup.run(c2.ctx)
        stored = ProviderSettings.load(_APP)
        assert stored["imap_host"] == "imap.gmail.com"
        assert stored["address"] == "bot@gmail.com"
        assert stored["poll_secs"] == 90


class TestDoctor:
    @pytest.fixture(autouse=True)
    def _patch_probes(self, monkeypatch):
        """Both doctor probes are live socket calls; patch them per test."""
        self.imap_calls: list[tuple] = []
        self.smtp_calls: list[tuple] = []
        self.imap_result = (True, "IMAP login OK; folder 'INBOX' selectable")
        self.smtp_result = (True, "SMTP login OK (starttls)")

        def fake_imap(host, port, user, password, folder, *, use_ssl=True):
            self.imap_calls.append((host, port, user, folder, use_ssl))
            return self.imap_result

        def fake_smtp(host, port, user, password, *, security="starttls"):
            self.smtp_calls.append((host, port, user, security))
            return self.smtp_result

        monkeypatch.setattr(cli_doctor, "imap_probe", fake_imap)
        monkeypatch.setattr(cli_doctor, "smtp_probe", fake_smtp)

    @staticmethod
    def _configured() -> None:
        ProviderSettings.update(
            _APP,
            {
                "imap_host": "imap.test", "imap_port": 993, "imap_user": "u@test",
                "smtp_host": "smtp.test", "smtp_port": 587, "smtp_user": "u@test",
                "address": "bot@test", "folder": "INBOX", "smtp_security": "starttls",
            },
        )

    def test_unconfigured_reports_info_only(self):
        lines = cli_doctor.probe()
        assert len(lines) == 1
        assert lines[0].status == "info"
        assert "gideon setup" in lines[0].detail

    def test_a_healthy_setup_reports_ok_for_both_protocols(self):
        self._configured()
        save_credential(CRED_IMAP_PASS, "pw")
        lines = cli_doctor.probe()
        by_label = {line.label: line for line in lines}
        assert by_label["imap login+select"].status == "ok"
        assert by_label["smtp login"].status == "ok"
        assert by_label["mailbox"].detail == "bot@test"

    def test_the_probe_is_login_plus_select_on_the_configured_folder(self):
        """The plan's ``probe = login+select``: a login alone doesn't prove the folder we
        poll exists, and a wrong folder name is the second most common misconfiguration."""
        ProviderSettings.update(_APP, {"folder": "Agent"})
        self._configured()
        ProviderSettings.update(_APP, {"folder": "Agent"})
        save_credential(CRED_IMAP_PASS, "pw")
        cli_doctor.probe()
        assert self.imap_calls[0][3] == "Agent"

    def test_a_failed_imap_probe_reports_fail(self):
        self._configured()
        save_credential(CRED_IMAP_PASS, "pw")
        self.imap_result = (False, "IMAP select 'Agent' failed: NO")
        lines = {line.label: line for line in cli_doctor.probe()}
        assert lines["imap login+select"].status == "fail"
        assert "NO" in lines["imap login+select"].detail

    def test_a_failed_smtp_probe_reports_fail(self):
        self._configured()
        save_credential(CRED_IMAP_PASS, "pw")
        self.smtp_result = (False, "SMTP login failed: 535 bad credentials")
        lines = {line.label: line for line in cli_doctor.probe()}
        assert lines["smtp login"].status == "fail"

    def test_a_missing_password_fails_without_attempting_a_probe(self):
        self._configured()
        lines = {line.label: line for line in cli_doctor.probe()}
        assert lines["imap password"].status == "fail"
        assert CRED_IMAP_PASS in lines["imap password"].detail
        assert self.imap_calls == []

    def test_an_smtp_only_configuration_warns_about_inbound(self):
        ProviderSettings.update(_APP, {"smtp_host": "smtp.test", "smtp_user": "u@test"})
        save_credential(CRED_SMTP_PASS, "pw")
        lines = {line.label: line for line in cli_doctor.probe()}
        assert lines["imap"].status == "warn"
        assert "inbound is offline" in lines["imap"].detail

    def test_an_imap_only_configuration_warns_about_outbound(self):
        ProviderSettings.update(_APP, {"imap_host": "imap.test", "imap_user": "u@test"})
        save_credential(CRED_IMAP_PASS, "pw")
        lines = {line.label: line for line in cli_doctor.probe()}
        assert lines["smtp"].status == "warn"
        assert "cannot reply" in lines["smtp"].detail

    def test_the_trust_posture_is_stated(self):
        self._configured()
        save_credential(CRED_IMAP_PASS, "pw")
        details = " ".join(line.detail for line in cli_doctor.probe())
        assert "gideon pair mail-desk" in details

    def test_no_password_is_ever_echoed(self):
        self._configured()
        save_credential(CRED_IMAP_PASS, "super-secret-pw")
        rendered = " ".join(f"{line.label} {line.detail}" for line in cli_doctor.probe())
        assert "super-secret-pw" not in rendered
