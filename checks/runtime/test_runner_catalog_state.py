import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gideon.core.config.loader import AgentProfile, AppConfig
from gideon.engine.agents import marketplace, runner_lifecycle, runners


@pytest.fixture
def runner_home(tmp_path, monkeypatch):
    home = tmp_path / "runner-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


def _definition(**values):
    return runners.RunnerDefinition(
        id="owned-python",
        display_name="Owned Python",
        runtime_id="acp:owned-python",
        bin_names=("owned-python",),
        env_var="GIDEON_OWNED_PYTHON_BIN",
        **values,
    )


def _local_definition(home, filename, values):
    folder = home / "runners"
    folder.mkdir(exist_ok=True)
    target = folder / filename
    target.write_text(json.dumps(values))
    return target


def test_catalog_overlay_is_live_sorted_and_keeps_independent_rows(runner_home):
    first = _local_definition(
        runner_home,
        "a-first.json",
        {
            "id": "owned-python",
            "display_name": "First",
            "bin_names": "python",
        },
    )
    last = _local_definition(
        runner_home,
        "z-last.json",
        {
            "id": "owned-python",
            "display_name": "Last",
            "bin_names": ["python", ""],
            "version_args": [],
            "acp_args": "--serve",
            "adapter": {"npm_pkg": " "},
        },
    )
    fallback = _local_definition(
        runner_home, "local-row.json", {"bin_names": ["local-cli"]}
    )
    _local_definition(
        runner_home, "invalid.json", {"id": "../escape", "bin_names": ["bad"]}
    )
    snapshot = runners.catalog()
    assert snapshot["owned-python"].display_name == "Last"
    assert snapshot["owned-python"].bin_names == ("python",)
    assert snapshot["owned-python"].version_args == ("--version",)
    assert snapshot["owned-python"].acp_args == ("--serve",)
    assert snapshot["owned-python"].adapter is None
    assert snapshot["local-row"].source == "user"
    assert "../escape" not in snapshot
    last.unlink()
    assert runners.catalog()["owned-python"].display_name == "First"
    assert runners.definition_for_runtime(" acp:owned-python ").display_name == "First"
    assert first.exists() and fallback.exists()


@pytest.mark.parametrize(
    "row,error",
    [
        ({"id": "bad/name", "bin_names": ["bin"]}, "Invalid runner id: 'bad/name'"),
        ({"id": "empty", "bin_names": []}, "Runner 'empty' declares no bin_names"),
    ],
)
def test_runner_decoding_preserves_validation_errors(row, error):
    with pytest.raises(ValueError) as caught:
        runners._definition_from(row, source="user")
    assert str(caught.value) == error


def test_actual_python_probe_persists_measurement_and_preserves_capabilities(
    runner_home, monkeypatch
):
    monkeypatch.setenv("GIDEON_OWNED_PYTHON_BIN", sys.executable)
    _local_definition(
        runner_home,
        "owned-python.json",
        {
            "id": "owned-python",
            "bin_names": ["python"],
            "env_var": "GIDEON_OWNED_PYTHON_BIN",
        },
    )
    runners.record_capabilities(
        "acp:owned-python", models=["local"], modes=["read"], efforts=["low"]
    )
    result = runners.probe_runner(_definition())
    assert result.ok and result.version == ".".join(map(str, sys.version_info[:3]))
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.resolved_command == (sys.executable,)
    assert runners.load_evidence("owned-python") == result
    assert runners.load_capabilities("owned-python")["models"] == ["local"]
    runners.record_capabilities("acp:owned-python", models=["changed"])
    assert runners.load_evidence("owned-python") == result
    assert runners.load_capabilities("owned-python")["models"] == ["changed"]
    assert runners.record_capabilities("acp:unregistered") is None


@pytest.mark.parametrize(
    "program,error",
    [
        ("import sys; print('owned failure'); sys.exit(4)", "owned failure"),
        ("import sys; sys.exit(5)", "exited 5 with no output"),
        (
            "import sys; print('owned stderr', file=sys.stderr); sys.exit(6)",
            "owned stderr",
        ),
    ],
)
def test_actual_python_failure_output_is_preserved(
    runner_home, monkeypatch, program, error
):
    monkeypatch.setenv("GIDEON_OWNED_PYTHON_BIN", sys.executable)
    result = runners.probe_runner(
        _definition(version_args=("-c", program)), persist=False
    )
    assert result.ok is False and result.error == error
    assert result.latency_ms is not None
    assert not runners.sidecar_path("owned-python").exists()


def test_actual_python_timeout_and_missing_executable_have_distinct_measurements(
    runner_home, monkeypatch
):
    monkeypatch.setenv("GIDEON_OWNED_PYTHON_BIN", sys.executable)
    monkeypatch.setattr(runners, "PROBE_TIMEOUT_SECS", 0.02)
    timed = runners.probe_runner(
        _definition(version_args=("-c", "import time; time.sleep(1)")), persist=False
    )
    assert not timed.ok and timed.error.startswith("TimeoutExpired:")
    assert timed.latency_ms is not None and timed.latency_ms >= 15
    missing = runner_home / "absent-executable"
    monkeypatch.setenv("GIDEON_OWNED_PYTHON_BIN", str(missing))
    absent = runners.probe_runner(_definition(), persist=False)
    assert not absent.ok and absent.error.startswith("FileNotFoundError:")
    assert absent.latency_ms is None
    assert absent.resolved_command == (str(missing),)


def test_sidecar_tolerates_corruption_and_keeps_unknown_measurements(runner_home):
    path = runners.sidecar_path("owned-python")
    path.write_text("{")
    assert runners.load_evidence("owned-python") is None
    path.write_text(
        json.dumps(
            {
                "last_check": {
                    "ok": 1,
                    "checked_at": "2026-01-01T00:00:00",
                    "latency_ms": "12",
                    "resolved_command": "python",
                    "version": "",
                    "error": "",
                }
            }
        )
    )
    value = runners.load_evidence("owned-python")
    assert value.probe == "unknown" and value.latency_ms is None
    assert value.version is None and value.error is None
    assert value.resolved_command == ("python",)
    assert runners.evidence_is_stale(value, interval_secs=60) is True
    future = runners.HealthEvidence(
        True, "version", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    )
    assert runners.evidence_is_stale(future, interval_secs=60) is False
    with pytest.raises(ValueError, match="Invalid runner id"):
        runners.sidecar_path("../outside")


def test_digest_streams_actual_binary_content(tmp_path):
    data = bytes(range(256)) * 1000
    path = tmp_path / "content.bin"
    path.write_bytes(data)
    assert runners.sha256_file(path) == "sha256:" + hashlib.sha256(data).hexdigest()


def test_real_config_resolves_profile_precedence_and_unknown_unattended_admission(
    runner_home,
):
    config = AppConfig()
    config.agent.provider = "acp:global"
    config.agent.unattended_requires_verified_adapter = True
    config.agents["special"] = AgentProfile(provider="acp:special")
    config.agents["local"] = AgentProfile(provider="native")
    config.agents["inherited"] = AgentProfile()
    config.save()
    assert runners.runtime_id_for_agent("special") == "acp:special"
    assert runners.runtime_id_for_agent("inherited") == "acp:global"
    assert runners.runtime_id_for_agent("missing") == "acp:global"
    assert runners.runtime_id_for_agent("local") == ""
    with pytest.raises(runners.UnverifiedAdapterError, match="no runner-catalog row"):
        runners.guard_unattended_spawn("acp:unknown-owned", unattended=True)
    runners.guard_unattended_spawn("acp:unknown-owned", unattended=False)
    runners.guard_unattended_spawn("", unattended=True)


def test_real_lease_expiry_ownership_and_catalog_sweep(runner_home):
    _local_definition(runner_home, "owned-python.json", {"bin_names": ["python"]})
    claim, reason = runner_lifecycle.claim_runner(
        "acp:owned-python", "session:owner", ttl=2
    )
    assert claim is not None and reason == ""
    held = runner_lifecycle.lease_for("acp:owned-python", now=claim.taken_at)
    assert held["holder"] == "session:owner" and held["expires_in_secs"] == 2
    assert held["age_secs"] == 0
    rejected, reason = runner_lifecycle.release_runner(
        "acp:owned-python", "session:other"
    )
    assert rejected is not None and reason
    assert runner_lifecycle.lease_for("acp:owned-python", now=claim.expires_at) is None
    runner_lifecycle.claim_runner("acp:not-cataloged", "session:other", ttl=1)
    assert runner_lifecycle.sweep_idle_leases(now=claim.expires_at + 10) == [
        "acp:owned-python"
    ]
    from gideon.automation.workflows import leases

    assert (
        leases.read_claim(runner_lifecycle.lease_target("acp:not-cataloged"))
        is not None
    )
    assert runner_lifecycle.claim_runner("", "owner") == (None, "no holder")
    assert runner_lifecycle.release_runner("acp:owned-python", "") == (
        None,
        "no holder",
    )


def test_lifecycle_settings_read_saved_values_at_each_use(runner_home, monkeypatch):
    config = AppConfig()
    config.agent.runner_idle_release_secs = 120
    config.agent.durable_sessions = False
    config.save()
    assert runner_lifecycle.idle_release_secs() == 120
    assert runner_lifecycle.durable_sessions_enabled() is False
    config.agent.runner_idle_release_secs = 900
    config.agent.durable_sessions = True
    config.save()
    monkeypatch.setenv("PATH", str(runner_home / "no-binaries"))
    assert runner_lifecycle.idle_release_secs() == 900
    assert runner_lifecycle.durable_sessions_enabled() is False


def test_marketplace_crud_roundtrips_runtime_voice_and_routing(tmp_path):
    market = marketplace.LocalAgentMarketplace(tmp_path / "agents")
    created = marketplace.AgentDefinition(
        name="owned-agent",
        description="Résumé",
        provider="acp:local",
        provider_entry="model-entry",
        voice="plain",
        natural_voice=True,
        specialty="Databases",
        route_hints="inspect schema",
        skills=["inspect"],
        mcp_servers={"local": {"argv": ["local"]}},
    )
    assert market.create(created) is created
    assert created.created_at == created.updated_at
    original_created = created.created_at
    persisted = market.get("owned-agent")
    assert persisted.to_dict() == created.to_dict()
    projection = persisted.to_dict()
    projection["mcp_servers"]["local"]["argv"].append("isolated")
    assert persisted.mcp_servers["local"]["argv"] == ["local"]
    updated = market.update(
        "owned-agent",
        {
            "name": "ignored",
            "source": "ignored",
            "created_at": 0,
            "description": None,
            "provider": "native",
            "voice": "updated",
            "natural_voice": False,
            "skills": [12],
            "mcp_servers": {"service": {}},
            "specialty": "Code",
            "route_hints": "review local code",
        },
    )
    assert updated.name == "owned-agent" and updated.source == "local"
    assert (
        updated.created_at == original_created
        and updated.updated_at >= original_created
    )
    assert updated.description == "" and updated.skills == ["12"]
    assert updated.natural_voice is False and updated.provider == "native"
    assert updated.provider_entry == "model-entry" and updated.voice == "updated"
    assert market.get("owned-agent").to_dict() == updated.to_dict()
    with pytest.raises(FileExistsError, match="already exists"):
        market.create(marketplace.AgentDefinition("owned-agent"))
    (tmp_path / "agents" / "owned-agent" / "asset.txt").write_text("local asset")
    market.delete("owned-agent")
    assert market.get("owned-agent") is None and market.list() == []
    with pytest.raises(KeyError, match="not found"):
        market.delete("owned-agent")


def test_marketplace_failed_update_does_not_change_persisted_definition(tmp_path):
    market = marketplace.LocalAgentMarketplace(tmp_path / "agents")
    market.create(marketplace.AgentDefinition("owned"))
    path = tmp_path / "agents" / "owned" / "agent.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="invalid skill name"):
        market.update("owned", {"skills": ["../escape"]})
    assert path.read_bytes() == before
    assert market.get("../escape") is None
    with pytest.raises(KeyError, match="not found"):
        market.update("missing", {})


def test_marketplace_lists_sorted_records_and_skips_malformed_files(tmp_path):
    market = marketplace.LocalAgentMarketplace(tmp_path / "agents")
    assert market.list() == []
    market.create(marketplace.AgentDefinition("zeta"))
    market.create(marketplace.AgentDefinition("alpha"))
    malformed = tmp_path / "agents" / "bad"
    malformed.mkdir()
    (malformed / "agent.json").write_text("{")
    (tmp_path / "agents" / "loose.txt").write_text("ignored")
    assert [record.name for record in market.list()] == ["alpha", "zeta"]
    assert market.get("bad") is None


def test_marketplace_validation_error_order_and_decoder_contract():
    record = marketplace.AgentDefinition(
        name="Bad",
        description="d" * 1025,
        system_prompt="p" * 32001,
        specialty="s" * 1025,
        route_hints="r" * 1025,
        skills=["../x", 8],
    )
    assert record.validate() == [
        "name must match ^[a-z0-9][a-z0-9-]{0,62}$ (got 'Bad')",
        "description exceeds 1024 chars",
        "system_prompt exceeds 32000 chars",
        "specialty exceeds 1024 chars",
        "route_hints exceeds 1024 chars",
        "invalid skill name: '../x'",
        "invalid skill name: 8",
    ]
    parsed = marketplace.AgentDefinition.from_dict(
        {
            "name": "decoded",
            "description": None,
            "natural_voice": 0,
            "skills": ("one",),
            "created_at": "12.5",
            "updated_at": 13,
            "provider": "acp:owned",
            "source": "portable",
            "voice": "direct",
        }
    )
    assert parsed.description == "None" and parsed.natural_voice is False
    assert (
        parsed.skills == ["one"]
        and parsed.created_at == 12.5
        and parsed.updated_at == 13
    )
    assert (
        parsed.provider == "acp:owned"
        and parsed.source == "portable"
        and parsed.voice == "direct"
    )


def test_marketplace_registry_replaces_real_stores_in_sorted_order(tmp_path):
    registry = marketplace.AgentMarketplaceRegistry()
    first = marketplace.LocalAgentMarketplace(tmp_path / "first")
    second = marketplace.LocalAgentMarketplace(tmp_path / "second")
    registry.register("z-store", first)
    registry.register("a-store", second)
    registry.register("z-store", second)
    assert registry.get("z-store") is second
    assert registry.names() == ["a-store", "z-store"]
    assert registry.info() == [
        {"name": "a-store", "type": "local"},
        {"name": "z-store", "type": "local"},
    ]
    with pytest.raises(KeyError, match="No agent marketplace"):
        registry.get("missing")
    assert (
        marketplace.get_default_agent_registry()
        is marketplace.get_default_agent_registry()
    )
    assert marketplace.create_provider() is None
