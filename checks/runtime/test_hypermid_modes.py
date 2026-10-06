from __future__ import annotations

import pytest

from gideon.hypermid.config import (
    ConfigError,
    ContextConfig,
    ContextMode,
    DaemonConfig,
    DaemonTransport,
    FeatureFlags,
    LocalAuthConfig,
    LocalAuthMethod,
    OverflowPolicy,
    RefusalPolicy,
    TurnBoundaryConfig,
)


def _enabled_features() -> FeatureFlags:
    return FeatureFlags(True, True, True, True, True, True)


def test_shadow_is_observational_even_when_every_feature_is_configured() -> None:
    config = ContextConfig(
        mode=ContextMode.SHADOW,
        overflow_policy=OverflowPolicy.REFUSE_IMMEDIATELY,
        refusal_policy=RefusalPolicy.COMPATIBLE_LAST_KNOWN_GOOD,
        features=_enabled_features(),
    )
    capabilities = config.mode.capabilities
    assert capabilities.computes_projection
    assert capabilities.preserves_host_request_bytes
    assert not capabilities.allows_model_calls
    assert not capabilities.allows_durable_writes
    assert not capabilities.allows_publication
    assert not capabilities.allows_serving_cursor_advance
    assert config.effective_features == FeatureFlags()
    assert ContextConfig.from_wire(config.to_wire()) == config


def test_configuration_changes_are_digest_fenced_and_turn_boundary_applied() -> None:
    state = TurnBoundaryConfig()
    original = state.active
    next_config = ContextConfig(
        mode=ContextMode.PRIMARY,
        overflow_policy=OverflowPolicy.REFUSE_IMMEDIATELY,
        features=_enabled_features(),
    )
    pending = state.stage(next_config, expected_digest=original.config_digest)
    assert pending is not None
    assert pending.previous_config_digest == original.config_digest
    assert state.active == original

    transition = state.apply_at_turn_boundary()
    assert transition is not None
    assert transition.previous == original
    assert transition.next.policy_revision == 2
    assert transition.next.config == next_config
    assert transition.next.config_digest == pending.next_config_digest
    assert transition.next.config.effective_features == _enabled_features()

    with pytest.raises(ConfigError, match="STALE_CONFIGURATION"):
        state.stage(ContextConfig(), expected_digest=original.config_digest)
    assert state.active == transition.next


def test_identical_configuration_does_not_advance_policy_revision() -> None:
    state = TurnBoundaryConfig()
    assert (
        state.stage(state.active.config, expected_digest=state.active.config_digest)
        is None
    )
    assert state.apply_at_turn_boundary() is None
    assert state.active.policy_revision == 1


def test_canonical_digest_and_daemon_transport_contract_are_stable() -> None:
    config = ContextConfig()
    assert config.canonical_bytes == (
        b'{"features":{"automatic_reclaim":false,"background_summaries":false,'
        b'"nudges":false,"reduction_tools":false,"subagent_contributions":false,'
        b'"synthetic_hook_blocks":false},"mode":"off",'
        b'"overflow_policy":"reclaim_then_refuse","refusal_policy":"refuse"}'
    )
    daemon = DaemonConfig(
        transport=DaemonTransport.UNIX_SOCKET,
        endpoint="/run/user/1000/hypermid.sock",
        auth=LocalAuthConfig(
            method=LocalAuthMethod.PEER_AND_HMAC,
            token_file="/run/user/1000/hypermid.token",
            require_peer_identity=True,
        ),
    )
    assert DaemonConfig.from_wire(daemon.to_wire()) == daemon
    connected_daemon = daemon.with_connection_record(
        "/run/user/1000/hypermid.connection.json"
    )
    assert (
        connected_daemon.connection_record_path
        == "/run/user/1000/hypermid.connection.json"
    )
    assert "connection_record" not in connected_daemon.to_wire()

    with pytest.raises(ValueError, match="do not match"):
        DaemonConfig(
            transport=DaemonTransport.LOOPBACK_TCP,
            endpoint="127.0.0.1:6197",
            auth=daemon.auth,
        )
