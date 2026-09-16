"""Hypothesis property tests for auth modes and internal-tool residue.

Properties tested:
    P6  — Auth Mode Loopback Invariant (NONE forces 127.0.0.1; other modes
          respect the configured bind_host)
    P12 — No internal-tool residue (banned symbols) in the source tree
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from gideon.security.auth.modes import AuthConfig, AuthMode, effective_bind

REPO_SRC = Path(__file__).resolve().parent.parent.parent / "src"


@given(
    bind_host=st.text(min_size=1, max_size=64),
)
@settings(max_examples=100)
def test_P6_loopback_invariant_none_mode(bind_host):
    """P6: AuthMode.NONE always forces effective_bind to 127.0.0.1."""
    cfg = AuthConfig(mode=AuthMode.NONE, bind_host=bind_host)
    result = effective_bind(cfg)
    assert (
        result == "127.0.0.1"
    ), f"P6 violated: AuthMode.NONE with bind_host={bind_host!r} → {result!r}"


@given(
    mode=st.sampled_from([AuthMode.LOCAL_TOKEN, AuthMode.API_KEY, AuthMode.OAUTH2]),
    bind_host=st.sampled_from(["0.0.0.0", "127.0.0.1", "10.0.0.1", "::1"]),
)
@settings(max_examples=50)
def test_P6_non_none_mode_respects_bind_host(mode, bind_host):
    """P6: non-NONE modes return the configured bind_host unchanged."""
    cfg = AuthConfig(mode=mode, bind_host=bind_host)
    result = effective_bind(cfg)
    assert (
        result == bind_host
    ), f"P6 unexpected rewrite: mode={mode} bind_host={bind_host!r} → {result!r}"


def test_from_env_default_is_local_token(monkeypatch):
    monkeypatch.delenv("GIDEON_AUTH_MODE", raising=False)
    assert AuthConfig.from_env().mode == AuthMode.LOCAL_TOKEN


def test_from_env_none_selects_none_mode(monkeypatch):
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    cfg = AuthConfig.from_env()
    assert cfg.mode == AuthMode.NONE
    assert effective_bind(cfg) == "127.0.0.1"


def test_from_env_unknown_value_falls_back_to_local_token(monkeypatch):
    monkeypatch.setenv("GIDEON_AUTH_MODE", "banana")
    assert AuthConfig.from_env().mode == AuthMode.LOCAL_TOKEN


_RESIDUE_TERMS = [
    r"\bAcpProvider\b",
    r"SlackMcpClient",
    r"_find_slack_mcp",
    r"from backend\.aim_agents",
    r"from backend\.mcp_cleanup",
]


@pytest.mark.parametrize("term", _RESIDUE_TERMS)
def test_P12_no_residue_in_python_src(term):
    """P12: banned internal-tool symbols must not appear in src/."""
    pattern = re.compile(term)
    violations: list[str] = []

    for py_file in REPO_SRC.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        if "test" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                rel = py_file.relative_to(REPO_SRC.parent)
                violations.append(f"  {rel}:{lineno}: {line.strip()[:120]}")

    assert (
        not violations
    ), f"P12 violated — banned term {term!r} found in source:\n" + "\n".join(
        violations[:10]
    )
