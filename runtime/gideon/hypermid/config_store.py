from __future__ import annotations

import os
import re
import sqlite3
import stat
import threading
from pathlib import Path

from gideon.hypermid.config import (
    ConfigError,
    ConfigSnapshot,
    ConfigTransition,
    ContextConfig,
    PendingConfiguration,
)
from gideon.hypermid.models import MAX_SAFE_INTEGER

_SCHEMA_VERSION = 1
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class ConfigStoreError(RuntimeError):
    pass


class ConfigStoreUninitialized(ConfigStoreError):
    pass


class ConfigStoreCorrupt(ConfigStoreError):
    pass


class ConfigStoreAhead(ConfigStoreError):
    def __init__(self, stored_version: int) -> None:
        self.stored_version = stored_version
        super().__init__(
            f"configuration store schema {stored_version} is newer than supported "
            f"schema {_SCHEMA_VERSION}"
        )


class StaleConfiguration(ConfigError):
    def __init__(
        self,
        current: ConfigSnapshot,
        pending: PendingConfiguration | None,
    ) -> None:
        self.current = current
        self.pending = pending
        super().__init__("STALE_CONFIGURATION")


class ChangeAlreadyPending(ConfigError):
    def __init__(
        self,
        current: ConfigSnapshot,
        pending: PendingConfiguration,
    ) -> None:
        self.current = current
        self.pending = pending
        super().__init__("CHANGE_ALREADY_PENDING")


class ContextConfigStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        self._prepare_path()
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._initialize_schema()
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            os.chmod(self.path, 0o600)
            self._secure_sidecars()
        except BaseException:
            self._connection.close()
            raise

    def _prepare_path(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists():
            metadata = self.path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ConfigStoreError(
                    f"configuration store path is not a regular file: {self.path}"
                )

    def _initialize_schema(self) -> None:
        stored_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if stored_version > _SCHEMA_VERSION:
            raise ConfigStoreAhead(stored_version)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hypermid_context_config_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    policy_revision INTEGER NOT NULL
                        CHECK(policy_revision >= 1 AND policy_revision <= 9007199254740991),
                    active_config_json TEXT NOT NULL,
                    active_digest TEXT NOT NULL
                        CHECK(length(active_digest) = 64),
                    pending_expected_revision INTEGER,
                    pending_previous_digest TEXT,
                    pending_config_json TEXT,
                    pending_digest TEXT,
                    CHECK(
                        (pending_expected_revision IS NULL
                         AND pending_previous_digest IS NULL
                         AND pending_config_json IS NULL
                         AND pending_digest IS NULL)
                        OR
                        (pending_expected_revision IS NOT NULL
                         AND pending_previous_digest IS NOT NULL
                         AND pending_config_json IS NOT NULL
                         AND pending_digest IS NOT NULL)
                    )
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hypermid_context_config_revisions (
                    policy_revision INTEGER PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    previous_config_digest TEXT,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            if stored_version == 0:
                self._connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def _secure_sidecars(self) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            if sidecar.exists():
                os.chmod(sidecar, 0o600)

    def seed_if_empty(self, config: ContextConfig) -> ConfigSnapshot:
        config_json = config.canonical_bytes.decode("utf-8")
        digest = config.digest
        with self._lock:
            self._begin()
            try:
                row = self._read_row()
                if row is not None:
                    snapshot, _ = self._decode_state(row)
                    self._connection.commit()
                    return snapshot
                self._connection.execute(
                    """
                    INSERT INTO hypermid_context_config_state(
                        singleton, policy_revision, active_config_json, active_digest
                    ) VALUES (1, 1, ?, ?)
                    """,
                    (config_json, digest),
                )
                self._connection.execute(
                    """
                    INSERT INTO hypermid_context_config_revisions(
                        policy_revision, config_json, config_digest,
                        previous_config_digest
                    ) VALUES (1, ?, ?, NULL)
                    """,
                    (config_json, digest),
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        self._secure_sidecars()
        return ConfigSnapshot(config, 1, digest)

    def snapshot(self) -> ConfigSnapshot:
        with self._lock:
            self._require_open()
            row = self._read_row()
            if row is None:
                raise ConfigStoreUninitialized("configuration store has not been seeded")
            return self._decode_state(row)[0]

    def pending(self) -> PendingConfiguration | None:
        with self._lock:
            self._require_open()
            row = self._read_row()
            if row is None:
                raise ConfigStoreUninitialized("configuration store has not been seeded")
            return self._decode_state(row)[1]

    def stage(
        self,
        next_config: ContextConfig,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> PendingConfiguration | None:
        if type(expected_revision) is not int or not 1 <= expected_revision <= MAX_SAFE_INTEGER:
            raise ValueError("expected_revision is outside the supported range")
        if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
            raise ValueError("expected_digest is not a Hypermid digest")
        next_json = next_config.canonical_bytes.decode("utf-8")
        next_digest = next_config.digest
        with self._lock:
            self._begin()
            try:
                row = self._read_row()
                if row is None:
                    raise ConfigStoreUninitialized(
                        "configuration store has not been seeded"
                    )
                current, pending = self._decode_state(row)
                if (
                    expected_revision != current.policy_revision
                    or expected_digest != current.config_digest
                ):
                    raise StaleConfiguration(current, pending)
                if pending is not None:
                    if (
                        pending.expected_policy_revision == expected_revision
                        and pending.previous_config_digest == expected_digest
                        and pending.next_config_digest == next_digest
                    ):
                        self._connection.commit()
                        return pending
                    raise ChangeAlreadyPending(current, pending)
                if next_digest == current.config_digest:
                    self._connection.commit()
                    return None
                updated = self._connection.execute(
                    """
                    UPDATE hypermid_context_config_state
                    SET pending_expected_revision=?,
                        pending_previous_digest=?,
                        pending_config_json=?,
                        pending_digest=?
                    WHERE singleton=1
                      AND policy_revision=?
                      AND active_digest=?
                      AND pending_digest IS NULL
                    """,
                    (
                        expected_revision,
                        expected_digest,
                        next_json,
                        next_digest,
                        expected_revision,
                        expected_digest,
                    ),
                )
                if updated.rowcount != 1:
                    latest = self._read_row()
                    if latest is None:
                        raise ConfigStoreUninitialized(
                            "configuration store has not been seeded"
                        )
                    latest_snapshot, latest_pending = self._decode_state(latest)
                    raise StaleConfiguration(latest_snapshot, latest_pending)
                pending = PendingConfiguration(
                    expected_policy_revision=expected_revision,
                    previous_config_digest=expected_digest,
                    next_config=next_config,
                    next_config_digest=next_digest,
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        self._secure_sidecars()
        return pending

    def apply_at_turn_boundary(self) -> ConfigTransition | None:
        with self._lock:
            self._begin()
            try:
                row = self._read_row()
                if row is None:
                    raise ConfigStoreUninitialized(
                        "configuration store has not been seeded"
                    )
                previous, pending = self._decode_state(row)
                if pending is None:
                    self._connection.commit()
                    return None
                if (
                    pending.expected_policy_revision != previous.policy_revision
                    or pending.previous_config_digest != previous.config_digest
                ):
                    raise ConfigStoreCorrupt(
                        "pending configuration is not bound to the active revision"
                    )
                next_revision = previous.policy_revision + 1
                if next_revision > MAX_SAFE_INTEGER:
                    raise ConfigStoreError(
                        "policy revision exhausted the interoperable integer range"
                    )
                next_json = pending.next_config.canonical_bytes.decode("utf-8")
                updated = self._connection.execute(
                    """
                    UPDATE hypermid_context_config_state
                    SET policy_revision=?, active_config_json=?, active_digest=?,
                        pending_expected_revision=NULL,
                        pending_previous_digest=NULL,
                        pending_config_json=NULL,
                        pending_digest=NULL
                    WHERE singleton=1
                      AND policy_revision=?
                      AND active_digest=?
                      AND pending_expected_revision=?
                      AND pending_previous_digest=?
                      AND pending_digest=?
                    """,
                    (
                        next_revision,
                        next_json,
                        pending.next_config_digest,
                        previous.policy_revision,
                        previous.config_digest,
                        pending.expected_policy_revision,
                        pending.previous_config_digest,
                        pending.next_config_digest,
                    ),
                )
                if updated.rowcount != 1:
                    raise ConfigStoreCorrupt(
                        "configuration changed while applying its turn-boundary update"
                    )
                self._connection.execute(
                    """
                    INSERT INTO hypermid_context_config_revisions(
                        policy_revision, config_json, config_digest,
                        previous_config_digest
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        next_revision,
                        next_json,
                        pending.next_config_digest,
                        previous.config_digest,
                    ),
                )
                next_snapshot = ConfigSnapshot(
                    pending.next_config,
                    next_revision,
                    pending.next_config_digest,
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        self._secure_sidecars()
        return ConfigTransition(previous, next_snapshot)

    def _begin(self) -> None:
        self._require_open()
        self._connection.execute("BEGIN IMMEDIATE")

    def _read_row(self) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT policy_revision, active_config_json, active_digest,
                   pending_expected_revision, pending_previous_digest,
                   pending_config_json, pending_digest
            FROM hypermid_context_config_state WHERE singleton=1
            """
        ).fetchone()

    def _decode_state(
        self, row: sqlite3.Row
    ) -> tuple[ConfigSnapshot, PendingConfiguration | None]:
        revision = int(row["policy_revision"])
        if not 1 <= revision <= MAX_SAFE_INTEGER:
            raise ConfigStoreCorrupt("active policy revision is invalid")
        active = self._decode_config(row["active_config_json"], row["active_digest"])
        snapshot = ConfigSnapshot(active, revision, str(row["active_digest"]))
        pending_values = (
            row["pending_expected_revision"],
            row["pending_previous_digest"],
            row["pending_config_json"],
            row["pending_digest"],
        )
        if all(value is None for value in pending_values):
            return snapshot, None
        if any(value is None for value in pending_values):
            raise ConfigStoreCorrupt("pending configuration is incomplete")
        expected_revision = int(row["pending_expected_revision"])
        previous_digest = str(row["pending_previous_digest"])
        next_digest = str(row["pending_digest"])
        pending_config = self._decode_config(row["pending_config_json"], next_digest)
        return snapshot, PendingConfiguration(
            expected_policy_revision=expected_revision,
            previous_config_digest=previous_digest,
            next_config=pending_config,
            next_config_digest=next_digest,
        )

    @staticmethod
    def _decode_config(raw_json: object, stored_digest: object) -> ContextConfig:
        if not isinstance(raw_json, str) or not isinstance(stored_digest, str):
            raise ConfigStoreCorrupt("configuration record has invalid field types")
        if _DIGEST.fullmatch(stored_digest) is None:
            raise ConfigStoreCorrupt("configuration digest is invalid")
        try:
            import json

            value = json.loads(raw_json)
            config = ContextConfig.from_wire(value)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ConfigStoreCorrupt("configuration JSON is invalid") from error
        if config.digest != stored_digest:
            raise ConfigStoreCorrupt("configuration digest does not match its value")
        return config

    def _require_open(self) -> None:
        if self._closed:
            raise ConfigStoreError("configuration store is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> ContextConfigStore:
        self._require_open()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


__all__ = [
    "ChangeAlreadyPending",
    "ConfigStoreAhead",
    "ConfigStoreCorrupt",
    "ConfigStoreError",
    "ConfigStoreUninitialized",
    "ContextConfigStore",
    "StaleConfiguration",
]
