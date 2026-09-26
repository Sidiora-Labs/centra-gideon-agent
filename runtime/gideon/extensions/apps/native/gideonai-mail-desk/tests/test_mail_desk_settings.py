"""MailDeskSettings: coercion, clamping, the configured predicates, and the credential
boundary (secrets in the credential store, everything else in the app store).
"""

from __future__ import annotations

from gideon.sdk.channel import ProviderSettings, save_credential

from mail_desk_runtime.settings import (
    ACTIVATION_ALWAYS,
    ACTIVATION_OFF,
    CRED_IMAP_PASS,
    CRED_SMTP_PASS,
    DEFAULT_IMAP_PORT,
    DEFAULT_POLL_SECS,
    DEFAULT_SMTP_PORT,
    SMTP_SSL,
    SMTP_STARTTLS,
    MailDeskSettings,
    get_settings,
    load_credentials,
    reload_settings,
    _validate_activation,
    _validate_smtp_security,
)

_APP = "gideonai-mail-desk"


class TestDefaults:
    def test_an_unconfigured_app_yields_safe_defaults(self):
        settings = MailDeskSettings.load()
        assert settings.imap_port == DEFAULT_IMAP_PORT
        assert settings.smtp_port == DEFAULT_SMTP_PORT
        assert settings.folder == "INBOX"
        assert settings.poll_secs == DEFAULT_POLL_SECS
        assert settings.smtp_security == SMTP_STARTTLS
        assert settings.dm_activation == ACTIVATION_ALWAYS
        assert settings.imap_use_ssl is True

    def test_nothing_is_configured_by_default(self):
        settings = MailDeskSettings.load()
        assert settings.inbound_configured is False
        assert settings.outbound_configured is False


class TestLoadAndCoercion:
    def test_reads_every_field_from_the_app_store(self):
        ProviderSettings.update(
            _APP,
            {
                "imap_host": "imap.test", "imap_port": 143, "imap_user": "u@test",
                "imap_use_ssl": False, "folder": "Agent", "smtp_host": "smtp.test",
                "smtp_port": 465, "smtp_user": "s@test", "smtp_security": "ssl",
                "address": "bot@test", "poll_secs": 120, "dm_activation": "off",
            },
        )
        settings = MailDeskSettings.load()
        assert settings.imap_host == "imap.test"
        assert settings.imap_port == 143
        assert settings.imap_user == "u@test"
        assert settings.imap_use_ssl is False
        assert settings.folder == "Agent"
        assert settings.smtp_host == "smtp.test"
        assert settings.smtp_port == 465
        assert settings.smtp_user == "s@test"
        assert settings.smtp_security == SMTP_SSL
        assert settings.address == "bot@test"
        assert settings.poll_secs == 120
        assert settings.dm_activation == ACTIVATION_OFF

    def test_hosts_and_logins_are_stripped(self):
        ProviderSettings.update(_APP, {"imap_host": "  imap.test  ", "imap_user": " u@t "})
        settings = MailDeskSettings.load()
        assert settings.imap_host == "imap.test"
        assert settings.imap_user == "u@t"

    def test_a_bad_port_falls_back_to_its_own_default(self):
        ProviderSettings.update(_APP, {"imap_port": "not-a-number", "smtp_port": 99999})
        settings = MailDeskSettings.load()
        assert settings.imap_port == DEFAULT_IMAP_PORT
        assert settings.smtp_port == DEFAULT_SMTP_PORT

    def test_a_numeric_string_port_is_accepted(self):
        ProviderSettings.update(_APP, {"imap_port": "143"})
        assert MailDeskSettings.load().imap_port == 143

    def test_an_empty_folder_falls_back_to_inbox(self):
        ProviderSettings.update(_APP, {"folder": "   "})
        assert MailDeskSettings.load().folder == "INBOX"

    def test_an_unknown_smtp_security_falls_back_to_starttls(self):
        ProviderSettings.update(_APP, {"smtp_security": "wishful"})
        assert MailDeskSettings.load().smtp_security == SMTP_STARTTLS

    def test_an_unknown_activation_falls_back_to_always(self):
        ProviderSettings.update(_APP, {"dm_activation": "sometimes"})
        assert MailDeskSettings.load().dm_activation == ACTIVATION_ALWAYS

    def test_validators_directly(self):
        assert _validate_activation("bogus") == ACTIVATION_ALWAYS
        assert _validate_activation("off") == ACTIVATION_OFF
        assert _validate_smtp_security("bogus") == SMTP_STARTTLS
        assert _validate_smtp_security("ssl") == SMTP_SSL


class TestPollClamping:
    def test_too_fast_is_clamped_up(self):
        """Below 10s a poll loop hammers the provider; many hosts throttle or ban."""
        ProviderSettings.update(_APP, {"poll_secs": 1})
        assert MailDeskSettings.load().poll_secs == 10

    def test_too_slow_is_clamped_down(self):
        ProviderSettings.update(_APP, {"poll_secs": 100_000})
        assert MailDeskSettings.load().poll_secs == 3600

    def test_an_unparseable_cadence_falls_back_to_the_plans_60s(self):
        ProviderSettings.update(_APP, {"poll_secs": "soon"})
        assert MailDeskSettings.load().poll_secs == DEFAULT_POLL_SECS

    def test_a_sane_value_is_kept(self):
        ProviderSettings.update(_APP, {"poll_secs": 45})
        assert MailDeskSettings.load().poll_secs == 45


class TestMailboxAddress:
    def test_the_explicit_address_wins(self):
        ProviderSettings.update(_APP, {"address": "bot@test", "imap_user": "login@test"})
        assert MailDeskSettings.load().mailbox_address == "bot@test"

    def test_it_falls_back_to_the_imap_login(self):
        ProviderSettings.update(_APP, {"address": "", "imap_user": "login@test"})
        assert MailDeskSettings.load().mailbox_address == "login@test"


class TestConfiguredPredicates:
    def test_inbound_needs_a_host_and_a_user(self):
        ProviderSettings.update(_APP, {"imap_host": "imap.test", "imap_user": ""})
        assert MailDeskSettings.load().inbound_configured is False
        ProviderSettings.update(_APP, {"imap_user": "u@test"})
        assert MailDeskSettings.load().inbound_configured is True

    def test_outbound_needs_a_host_and_a_user(self):
        ProviderSettings.update(_APP, {"smtp_host": "smtp.test", "smtp_user": ""})
        assert MailDeskSettings.load().outbound_configured is False
        ProviderSettings.update(_APP, {"smtp_user": "s@test"})
        assert MailDeskSettings.load().outbound_configured is True

    def test_the_two_halves_are_independent(self):
        """A send-only configuration is legitimate (a pure notification sink)."""
        ProviderSettings.update(_APP, {"smtp_host": "smtp.test", "smtp_user": "s@test"})
        settings = MailDeskSettings.load()
        assert settings.outbound_configured is True
        assert settings.inbound_configured is False


class TestCredentialBoundary:
    def test_passwords_come_from_the_credential_store(self):
        save_credential(CRED_IMAP_PASS, "imap-secret")
        save_credential(CRED_SMTP_PASS, "smtp-secret")
        assert load_credentials() == ("imap-secret", "smtp-secret")

    def test_an_absent_smtp_password_reuses_the_imap_one(self):
        """One app password authenticates both protocols at every documented provider, so
        asking for it twice is the friction that ends in a half-configured channel."""
        save_credential(CRED_IMAP_PASS, "one-app-password")
        assert load_credentials() == ("one-app-password", "one-app-password")

    def test_no_credentials_yields_empty_strings(self):
        assert load_credentials() == ("", "")

    def test_secrets_never_land_in_the_app_store(self):
        """The whole point of the boundary: a password must not be readable from the
        non-secret settings file."""
        save_credential(CRED_IMAP_PASS, "imap-secret")
        stored = ProviderSettings.load(_APP)
        assert "imap-secret" not in str(stored)
        assert CRED_IMAP_PASS not in stored
        assert "imap_password" not in stored

    def test_settings_load_never_reads_the_credential_store(self):
        save_credential(CRED_IMAP_PASS, "imap-secret")
        settings = MailDeskSettings.load()
        assert "imap-secret" not in str(settings)


class TestSettingsCache:
    def test_get_settings_caches_and_reload_refreshes(self):
        ProviderSettings.update(_APP, {"folder": "First"})
        assert get_settings().folder == "First"
        ProviderSettings.update(_APP, {"folder": "Second"})
        assert get_settings().folder == "First"  # cached (a deliberate process singleton)
        assert reload_settings().folder == "Second"
        assert get_settings().folder == "Second"
