"""Tests for safety profiles + egress tiers + the provider health view
(AUTONOMY-GUARDRAILS §3, §4.2, §2.5)."""

from __future__ import annotations

import pytest

from gideon.engine.session import BACKGROUND_KEY
from gideon.integrations.llm_helpers import ToolApprovalPolicy
from gideon.security.guardrails.health import provider_health
from gideon.security.guardrails.policy import (
    HEADLESS,
    INTERACTIVE,
    SafetyProfile,
    approval_policy_for_session,
    get_profile,
    is_unattended_session,
    profile_for_session,
    safety_profile_for,
)
from gideon.security.net.policy import REGISTRY, egress_policy_for_tier, get_policy


def test_named_profiles_exist():
    for name in (
        "interactive",
        "coding",
        "review_only",
        "cleanup",
        "incident",
        "headless",
    ):
        assert get_profile(name).name == name


def test_unknown_profile_fails_closed_to_headless():
    assert get_profile("bogus").name == "headless"


def test_headless_is_read_only_by_construction():
    assert HEADLESS.tool_grants == "read"
    assert HEADLESS.approval == "hook_based"
    assert INTERACTIVE.tool_grants == "read_write"


@pytest.mark.parametrize(
    "session_key,unattended",
    [
        ("cron:job1", True),
        ("subagent:abc", True),
        ("channel:slack:c1", True),
        ("inbox:item1", True),
        ("side:x", True),
        ("loop-goal-1", True),
        ("loop:code:2", True),
        ("chat:main", False),
        ("", False),
    ],
)
def test_is_unattended_session(session_key, unattended):
    assert is_unattended_session(session_key) is unattended


def test_profile_for_session_by_construction():
    assert profile_for_session("cron:nightly").name == "headless"
    assert profile_for_session("loop-abc").name == "headless"
    assert profile_for_session("chat:main").name == "interactive"


def test_background_key_is_headless():
    assert is_unattended_session(BACKGROUND_KEY) is True
    assert profile_for_session(BACKGROUND_KEY).name == "headless"


def test_approval_policy_for_session_maps_from_profile():
    assert profile_for_session(BACKGROUND_KEY).approval == "hook_based"
    assert approval_policy_for_session(BACKGROUND_KEY) is ToolApprovalPolicy.HOOK_BASED
    assert approval_policy_for_session("cron:x") is ToolApprovalPolicy.HOOK_BASED
    assert approval_policy_for_session("subagent:x") is ToolApprovalPolicy.HOOK_BASED

    interactive_approval = profile_for_session("chat:main").approval
    assert interactive_approval == "ask"
    expected = (
        ToolApprovalPolicy.AUTO_APPROVE
        if interactive_approval == "auto"
        else ToolApprovalPolicy.HOOK_BASED
    )
    assert approval_policy_for_session("chat:main") is expected
    assert approval_policy_for_session("") is expected


def test_approval_policy_auto_maps_to_auto_approve(monkeypatch):
    import gideon.security.guardrails.policy as policy

    monkeypatch.setattr(
        policy,
        "profile_for_session",
        lambda _k: SafetyProfile(name="x", approval="auto"),
    )
    assert approval_policy_for_session("whatever") is ToolApprovalPolicy.AUTO_APPROVE


def test_safety_profile_for_layers_config(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        '{"guardrails": {"budgets": {"max_tokens_per_day": 5000}, "scan_mode": "block"}}'
    )
    layered = safety_profile_for(HEADLESS)
    assert layered.budget.max_tokens == 5000
    assert layered.scan_mode == "block"


def test_incident_profile_forces_block_scan():
    from gideon.security.guardrails.policy import INCIDENT

    assert INCIDENT.scan_mode == "block"


def test_profile_with_overrides():
    p = SafetyProfile(name="x").with_overrides(egress_tier="off")
    assert p.egress_tier == "off" and p.name == "x"


def test_registry_profile_registered():
    assert get_policy("registry").name == "registry"
    assert "pypi.org" in REGISTRY.allow_hosts


def test_egress_policy_for_tier():
    assert egress_policy_for_tier("off") is None
    assert egress_policy_for_tier("registry").name == "registry"
    assert egress_policy_for_tier("all").name == "strict"
    assert egress_policy_for_tier("bogus").name == "strict"
    listed = egress_policy_for_tier("listed")
    assert listed.name == "listed" and listed.allow_only is True
    assert egress_policy_for_tier("registry").allow_only is True
    assert egress_policy_for_tier("all").allow_only is False


def test_health_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    h = provider_health()
    assert h == {"providers": [], "callers": [], "generated_from": 0}


def test_health_derives_from_audit(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    from gideon.security.guardrails.audit import AttemptRecord, record_attempt

    for i in range(4):
        record_attempt(
            AttemptRecord(
                audit_id=f"a{i}",
                ts=float(i),
                use_case="reasoning",
                provider="P",
                model="m",
                attempt=1,
                passed=True,
                latency_ms=100.0 + i * 10,
            )
        )
    record_attempt(
        AttemptRecord(
            audit_id="fail",
            ts=9.0,
            use_case="reasoning",
            provider="P",
            model="m",
            attempt=1,
            failure_mode="timeout",
            passed=False,
        )
    )
    h = provider_health()
    prov = next(p for p in h["providers"] if p["name"] == "P")
    assert prov["calls"] == 5 and prov["passed"] == 4 and prov["failed"] == 1
    assert prov["pass_rate"] == 0.8
    assert prov["p50_ms"] > 0
    assert prov["failure_modes"] == {"timeout": 1}
    assert prov["breaker_state"] == "closed"


def test_health_includes_open_breaker_without_audit(monkeypatch, tmp_path):
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    from gideon.security.guardrails.breaker import get_breaker

    b = get_breaker("DownProvider", threshold=1)
    b.record_failure()
    assert b.is_open()
    h = provider_health()
    prov = next(p for p in h["providers"] if p["name"] == "DownProvider")
    assert prov["breaker_state"] == "open" and prov["calls"] == 0
