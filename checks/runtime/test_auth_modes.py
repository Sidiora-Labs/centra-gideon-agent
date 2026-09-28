from __future__ import annotations

import pytest


@pytest.mark.parametrize("name", ["api_key", "oauth2", "saml"])
def test_unhonored_auth_modes_are_rejected_by_name(monkeypatch, name):
    monkeypatch.setenv("GIDEON_AUTH_MODE", name)
    from gideon.security.auth.modes import AuthConfig

    with pytest.raises(ValueError, match=name.strip()):
        AuthConfig.from_env()


def test_only_local_token_and_loopback_none_are_selectable(monkeypatch):
    from gideon.security.auth.modes import AuthConfig, AuthMode, effective_bind

    monkeypatch.setenv("GIDEON_AUTH_MODE", "local_token")
    assert AuthConfig.from_env().mode is AuthMode.LOCAL_TOKEN
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    assert effective_bind(AuthConfig.from_env()) == "127.0.0.1"


def test_unsupported_requested_mode_cannot_hide_behind_local_token():
    from gideon.security.auth.modes import AuthConfig, AuthMode

    with pytest.raises(ValueError, match="api_key"):
        AuthConfig(mode=AuthMode.LOCAL_TOKEN, requested_mode="api_key")
