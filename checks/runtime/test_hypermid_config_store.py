from __future__ import annotations

import sqlite3
import stat

import pytest

from gideon.hypermid.config import (
    ContextConfig,
    ContextMode,
    FeatureFlags,
    OverflowPolicy,
    RefusalPolicy,
)
from gideon.hypermid.config_store import (
    ChangeAlreadyPending,
    ConfigStoreCorrupt,
    ContextConfigStore,
    StaleConfiguration,
)


def _primary_config() -> ContextConfig:
    return ContextConfig(
        mode=ContextMode.PRIMARY,
        overflow_policy=OverflowPolicy.REFUSE_IMMEDIATELY,
        refusal_policy=RefusalPolicy.COMPATIBLE_LAST_KNOWN_GOOD,
        features=FeatureFlags(
            background_summaries=True,
            reduction_tools=True,
            automatic_reclaim=True,
            nudges=False,
            subagent_contributions=True,
            synthetic_hook_blocks=False,
        ),
    )


def test_staged_configuration_survives_restart_and_applies_at_one_boundary(
    tmp_path,
) -> None:
    path = tmp_path / "hypermid" / "configuration.sqlite3"
    with ContextConfigStore(path) as store:
        original = store.seed_if_empty(ContextConfig())
        pending = store.stage(
            _primary_config(),
            expected_revision=original.policy_revision,
            expected_digest=original.config_digest,
        )
        assert pending is not None
        assert store.snapshot() == original
        assert store.pending() == pending
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    with ContextConfigStore(path) as restored:
        assert restored.snapshot() == original
        assert restored.pending() == pending
        assert (
            restored.stage(
                _primary_config(),
                expected_revision=original.policy_revision,
                expected_digest=original.config_digest,
            )
            == pending
        )
        transition = restored.apply_at_turn_boundary()
        assert transition is not None
        assert transition.previous == original
        assert transition.next.policy_revision == 2
        assert transition.next.config == _primary_config()
        assert restored.pending() is None
        assert restored.apply_at_turn_boundary() is None

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            """
            SELECT policy_revision, config_digest, previous_config_digest
            FROM hypermid_context_config_revisions ORDER BY policy_revision
            """
        ).fetchall()
        assert rows == [
            (1, original.config_digest, None),
            (2, transition.next.config_digest, original.config_digest),
        ]
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        connection.close()


def test_compare_and_swap_refuses_stale_or_competing_operator_writes(tmp_path) -> None:
    path = tmp_path / "configuration.sqlite3"
    first = ContextConfigStore(path)
    second = ContextConfigStore(path)
    try:
        original = first.seed_if_empty(ContextConfig())
        pending = first.stage(
            _primary_config(),
            expected_revision=original.policy_revision,
            expected_digest=original.config_digest,
        )
        assert pending is not None

        competing = ContextConfig(mode=ContextMode.SHADOW)
        with pytest.raises(ChangeAlreadyPending) as conflict:
            second.stage(
                competing,
                expected_revision=original.policy_revision,
                expected_digest=original.config_digest,
            )
        assert conflict.value.current == original
        assert conflict.value.pending == pending

        transition = first.apply_at_turn_boundary()
        assert transition is not None
        with pytest.raises(StaleConfiguration) as stale:
            second.stage(
                competing,
                expected_revision=original.policy_revision,
                expected_digest=original.config_digest,
            )
        assert stale.value.code == "STALE_CONFIGURATION"
        assert stale.value.current == transition.next
        assert stale.value.pending is None
        assert second.snapshot() == transition.next
    finally:
        first.close()
        second.close()


def test_digest_corruption_is_detected_before_configuration_is_served(tmp_path) -> None:
    path = tmp_path / "configuration.sqlite3"
    with ContextConfigStore(path) as store:
        store.seed_if_empty(ContextConfig())
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE hypermid_context_config_state SET active_digest=? WHERE singleton=1",
            ("0" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    with ContextConfigStore(path) as reopened:
        with pytest.raises(ConfigStoreCorrupt, match="does not match"):
            reopened.snapshot()
