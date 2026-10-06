"""Development commands expose local-network access only when explicitly selected."""

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _recipe(target: str) -> str:
    return subprocess.run(
        ["make", "-n", target, "DEV_HOME=/tmp/gideon-dev-check", "DEV_PORT=12345"],
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_default_development_gateway_binds_loopback() -> None:
    recipe = _recipe("serve")
    assert "GIDEON_BIND_HOST=127.0.0.1" in recipe
    assert "GIDEON_BIND_HOST=0.0.0.0" not in recipe
    assert "GIDEON_BYPASS_LOCAL_NETWORKS=1" in recipe
    assert 'GIDEON_HOME="/tmp/gideon-dev-check"' in recipe
    assert '--no-open --port "12345" --json-ready' in recipe
    assert "http://127.0.0.1:12345/" in recipe
    assert "this machine only" in recipe


def test_lan_development_gateway_states_its_exposure() -> None:
    recipe = _recipe("serve-lan")
    assert "GIDEON_BIND_HOST=0.0.0.0" in recipe
    assert "GIDEON_BYPASS_LOCAL_NETWORKS=1" in recipe
    assert 'GIDEON_HOME="/tmp/gideon-dev-check"' in recipe
    assert '--no-open --port "12345" --json-ready' in recipe
    assert "OPEN to every device on the local network with no token" in recipe


def test_help_advertises_explicit_lan_command() -> None:
    help_text = subprocess.run(
        ["make", "help"], cwd=_REPO, check=True, capture_output=True, text=True
    ).stdout
    assert "serve-lan" in help_text
    assert "this machine" in help_text
