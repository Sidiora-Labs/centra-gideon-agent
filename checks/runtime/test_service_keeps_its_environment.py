"""Real environment-selection and native service-file serialization checks."""

from __future__ import annotations

import plistlib

import pytest

from gideon.operations.service import environment, linux, macos


def test_allowlisted_locations_and_proxy_survive_without_credential_values() -> None:
    marker = "service-env-canary-secret-value"
    selected = environment.resolve_service_environment(
        source={
            "GIDEON_HOME": "/srv/gideon home",
            "GIDEON_WORKSPACE": "/work/project",
            "GIDEON_PROFILE": "workstation",
            "XDG_CACHE_HOME": "/cache/models",
            "HTTPS_PROXY": "https://proxy.example:8443",
            "OPENAI_API_KEY": marker,
            "OTHER_PRIVATE_SETTING": marker,
        }
    )

    assert selected.values == {
        "GIDEON_HOME": "/srv/gideon home",
        "GIDEON_WORKSPACE": "/work/project",
        "GIDEON_PROFILE": "workstation",
        "XDG_CACHE_HOME": "/cache/models",
        "HTTPS_PROXY": "https://proxy.example:8443",
    }
    assert selected.excluded_names == ("OPENAI_API_KEY",)
    status = environment.status_environment_lines(selected)
    assert "GIDEON_HOME=/srv/gideon home" in status
    assert "excluded: OPENAI_API_KEY (value hidden)" in status
    assert marker not in status


def test_systemd_and_launchd_render_selected_values_with_native_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USER", "service-test")
    monkeypatch.setenv("GIDEON_HOME", "/srv/gideon home")
    monkeypatch.setenv("OPENAI_API_KEY", "service-env-canary-secret-value")

    unit = linux.render_unit(
        (
            r'GIDEON_PROFILE=desk "one"\two%done',
            "HTTPS_PROXY=https://proxy.example:8443",
        ),
        (),
    )
    assert 'Environment="GIDEON_PROFILE=desk \\"one\\"\\\\two%%done"' in unit
    assert 'Environment="GIDEON_HOME=/srv/gideon home"' in unit
    assert "# GideonEnvironmentExcluded=OPENAI_API_KEY" in unit
    assert "service-env-canary-secret-value" not in unit

    plist = plistlib.loads(
        macos.render_plist(
            (
                r'GIDEON_PROFILE=desk "one"\two%done',
                "HTTPS_PROXY=https://proxy.example:8443",
            ),
            (),
        ).encode("utf-8")
    )
    env = plist["EnvironmentVariables"]
    assert env["GIDEON_HOME"] == "/srv/gideon home"
    assert env["GIDEON_PROFILE"] == 'desk "one"\\two%done'
    assert env["HTTPS_PROXY"] == "https://proxy.example:8443"
    assert plist["GideonEnvironmentExcluded"] == ["OPENAI_API_KEY"]
    assert "service-env-canary-secret-value" not in str(plist)


def test_credential_bearing_proxy_is_refused_without_echoing_its_value() -> None:
    credential_value = "https://operator:canary-value@proxy.example:8443"
    with pytest.raises(environment.ServiceEnvironmentError) as exc_info:
        environment.resolve_service_environment(
            (f"HTTPS_PROXY={credential_value}",), source={}
        )
    assert "HTTPS_PROXY" in str(exc_info.value)
    assert credential_value not in str(exc_info.value)


def test_service_owned_names_cannot_be_overridden_or_removed() -> None:
    for argument, removals in (("HOME=/tmp/other", ()), ("", ("PATH",))):
        with pytest.raises(environment.ServiceEnvironmentError):
            environment.resolve_service_environment(
                (argument,) if argument else (), removals, source={}
            )


def test_status_sanitizes_values_loaded_from_existing_service_files(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "service-status-canary-secret-value"
    values = {
        "GIDEON_HOME": "/srv/gideon home",
        "OPENAI_API_KEY": marker,
        "HTTPS_PROXY": f"https://owner:{marker}@proxy.example:8443",
    }
    unit = tmp_path / "gideon.service"
    unit.write_text(
        "\n".join(f'Environment="{name}={value}"' for name, value in values.items())
        + "\n# GideonEnvironmentExcluded=OLD_API_KEY\n",
        encoding="utf-8",
    )
    plist = tmp_path / "gideon.plist"
    plist.write_bytes(
        plistlib.dumps(
            {
                "EnvironmentVariables": values,
                "GideonEnvironmentExcluded": ["OLD_API_KEY"],
            }
        )
    )
    monkeypatch.setattr(linux, "UNIT_PATH", unit)
    monkeypatch.setattr(macos, "PLIST_PATH", plist)

    for status in (linux.environment_status(), macos.environment_status()):
        assert "GIDEON_HOME=/srv/gideon home" in status
        assert "excluded: OPENAI_API_KEY (value hidden)" in status
        assert "excluded: HTTPS_PROXY (value hidden)" in status
        assert "excluded: OLD_API_KEY (value hidden)" in status
        assert marker not in status
