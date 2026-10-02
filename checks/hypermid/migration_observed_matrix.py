"""Observed migration facts for the security evidence matrix."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
if __package__:
    from checks.hypermid.evidence import ObservationWriter
else:
    from evidence import ObservationWriter
from gideon.hypermid.foundation import Scope
from gideon.hypermid.migration import (
    GideonSourceSnapshot,
    ImportReceipt,
    MigrationError,
    MigrationStore,
)


EXPECTED_FIXTURE_ITEMS = 22
EXPECTED_CATEGORY_COUNTS = {
    "page": 2,
    "semantic": 1,
    "episodic": 1,
    "lesson": 1,
    "slot": 7,
    "graph_link": 3,
    "audit_event": 7,
    "summary": 0,
}
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class MigrationObservationError(ValueError):
    pass


def migration_observation_writer() -> ObservationWriter | None:
    return ObservationWriter.from_env("migration")


@dataclass(frozen=True, slots=True)
class DigestMismatchObservation:
    probes: int
    accepted: int
    error_code: str


@dataclass(frozen=True, slots=True)
class FutureStoreObservation:
    schema_before: int
    schema_after: int
    state_digest_before: str
    state_digest_after: str
    error_code: str

    @property
    def mutations(self) -> int:
        return int(
            self.schema_before != self.schema_after
            or self.state_digest_before != self.state_digest_after
        )


@dataclass(slots=True)
class MigrationObservedMatrix:
    fixture_item_count: int = 0
    fixture_category_counts: dict[str, int] = field(default_factory=dict)
    fixture_source_digest: str = ""
    conversation_log_count: int = 0
    local_import: dict[str, int] = field(default_factory=dict)
    digest_mismatch: DigestMismatchObservation | None = None
    lifecycle_interruption: dict[str, str] = field(default_factory=dict)
    future_store: FutureStoreObservation | None = None

    def observe_fixture(self, source: GideonSourceSnapshot) -> None:
        self.fixture_item_count = len(source.items)
        self.fixture_category_counts = dict(source.counts())
        self.fixture_source_digest = str(source.source_digest)
        self.conversation_log_count = len(source.conversation_logs)

    def observe_local_resume(
        self, first: ImportReceipt, resumed: ImportReceipt
    ) -> None:
        self.local_import = {
            "first_imported": first.imported,
            "first_existing": first.existing,
            "resumed_imported": resumed.imported,
            "resumed_existing": resumed.existing,
        }

    def observe_digest_mismatch(
        self, observation: DigestMismatchObservation
    ) -> None:
        self.digest_mismatch = observation

    def observe_lifecycle_interruption(
        self,
        *,
        before_restart: str,
        recovery_before_restart: str,
        resumed_before_restart: str,
        recovery_after_restart: str,
        completed_state: str,
    ) -> None:
        self.lifecycle_interruption = {
            "before_restart": before_restart,
            "recovery_before_restart": recovery_before_restart,
            "resumed_before_restart": resumed_before_restart,
            "recovery_after_restart": recovery_after_restart,
            "completed_state": completed_state,
        }

    def observe_future_store(self, observation: FutureStoreObservation) -> None:
        self.future_store = observation

    def emit_observation(
        self,
        writer: ObservationWriter | None,
        artifact_path: str | os.PathLike[str],
    ) -> Path | None:
        if writer is None:
            return None
        matrix = self.to_mapping()
        artifact = self.write(artifact_path)
        writer.measure(
            "migration-fixtures",
            int(matrix["fixture"]["item_count"]),
            "gt",
            0,
            "fixtures",
        )
        writer.measure(
            "migration-digest-mismatches",
            int(matrix["digest_mismatch"]["accepted"]),
            "eq",
            0,
            "mismatches",
        )
        writer.measure(
            "migration-interruption-boundaries",
            len(matrix["interruption_boundaries"]),
            "gt",
            0,
            "boundaries",
        )
        writer.measure(
            "future-store-mutations",
            int(matrix["future_store"]["mutations"]),
            "eq",
            0,
            "mutations",
        )
        writer.artifact("migration-matrix", artifact, "application/json")
        return writer.finish(source_digest=os.environ["HYPERMID_SOURCE_DIGEST"])

    def to_mapping(self) -> dict[str, object]:
        self._validate()
        assert self.digest_mismatch is not None
        assert self.future_store is not None
        return {
            "schema_version": 1,
            "fixture": {
                "item_count": self.fixture_item_count,
                "category_counts": dict(sorted(self.fixture_category_counts.items())),
                "source_digest": self.fixture_source_digest,
                "conversation_log_count": self.conversation_log_count,
            },
            "digest_mismatch": {
                "probes": self.digest_mismatch.probes,
                "accepted": self.digest_mismatch.accepted,
                "error_code": self.digest_mismatch.error_code,
            },
            "interruption_boundaries": [
                {
                    "name": "local_import_reopen",
                    **self.local_import,
                },
                {
                    "name": "daemon_migration_restart",
                    **self.lifecycle_interruption,
                },
            ],
            "future_store": {
                "schema_before": self.future_store.schema_before,
                "schema_after": self.future_store.schema_after,
                "state_digest_before": self.future_store.state_digest_before,
                "state_digest_after": self.future_store.state_digest_after,
                "error_code": self.future_store.error_code,
                "mutations": self.future_store.mutations,
            },
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(self.to_mapping(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return destination

    def _validate(self) -> None:
        if self.fixture_item_count != EXPECTED_FIXTURE_ITEMS:
            raise MigrationObservationError("migration fixture item count changed")
        if self.fixture_category_counts != EXPECTED_CATEGORY_COUNTS:
            raise MigrationObservationError("migration fixture category counts changed")
        if not _DIGEST.fullmatch(self.fixture_source_digest):
            raise MigrationObservationError("migration fixture source digest is invalid")
        if self.conversation_log_count != 1:
            raise MigrationObservationError("migration fixture ConversationLog count changed")
        if self.local_import != {
            "first_imported": EXPECTED_FIXTURE_ITEMS,
            "first_existing": 0,
            "resumed_imported": 0,
            "resumed_existing": EXPECTED_FIXTURE_ITEMS,
        }:
            raise MigrationObservationError("local import interruption evidence is incomplete")
        if self.digest_mismatch != DigestMismatchObservation(
            probes=1, accepted=0, error_code="DIGEST_MISMATCH"
        ):
            raise MigrationObservationError("digest mismatch was not refused exactly")
        if self.lifecycle_interruption != {
            "before_restart": "running",
            "recovery_before_restart": "resumable",
            "resumed_before_restart": "running",
            "recovery_after_restart": "committed",
            "completed_state": "committed",
        }:
            raise MigrationObservationError("daemon restart interruption evidence is incomplete")
        if self.future_store is None:
            raise MigrationObservationError("future store evidence is missing")
        if (
            self.future_store.schema_before != 2
            or self.future_store.schema_after != 2
            or self.future_store.error_code != "STORE_AHEAD"
            or self.future_store.mutations != 0
        ):
            raise MigrationObservationError("future store was mutated or not refused")


def probe_digest_mismatch(
    path: str | os.PathLike[str],
    scope: Scope,
    source: GideonSourceSnapshot,
) -> DigestMismatchObservation:
    probe_path = Path(path)
    with MigrationStore(probe_path, scope) as store:
        receipt = store.import_snapshot(source)
        if receipt.imported != len(source.items):
            raise MigrationObservationError("digest probe did not import the full fixture")
    with sqlite3.connect(probe_path) as connection:
        connection.execute(
            "UPDATE imported_items SET destination_digest=? WHERE sequence=1",
            ("0" * 64,),
        )
    accepted = 0
    error_code = ""
    with MigrationStore(probe_path, scope) as store:
        try:
            store.validate(source)
        except MigrationError as error:
            error_code = error.code
        else:
            accepted = 1
    return DigestMismatchObservation(1, accepted, error_code)


def migration_store_state(path: str | os.PathLike[str]) -> tuple[int, str]:
    database = Path(path)
    with sqlite3.connect(database) as connection:
        schema = int(
            connection.execute(
                "SELECT schema_version FROM migration_meta WHERE singleton=1"
            ).fetchone()[0]
        )
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        state: dict[str, object] = {"schema_version": schema, "tables": {}}
        for table in tables:
            columns = [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            order = ",".join(f'"{column}"' for column in columns)
            rows = [
                list(row)
                for row in connection.execute(
                    f'SELECT {order} FROM "{table}" ORDER BY {order}'
                )
            ]
            state["tables"][table] = {"columns": columns, "rows": rows}
    encoded = json.dumps(
        state, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return schema, hashlib.sha256(encoded).hexdigest()


def future_store_observation(
    *,
    schema_before: int,
    state_digest_before: str,
    schema_after: int,
    state_digest_after: str,
    error_code: str,
) -> FutureStoreObservation:
    for value in (state_digest_before, state_digest_after):
        if not _DIGEST.fullmatch(value):
            raise MigrationObservationError("future store state digest is invalid")
    return FutureStoreObservation(
        schema_before,
        schema_after,
        state_digest_before,
        state_digest_after,
        error_code,
    )
