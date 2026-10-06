from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, TypeVar


class StorageError(RuntimeError):
    pass


class LeaseContended(StorageError):
    pass


class StaleFence(StorageError):
    pass


class StoreAhead(StorageError):
    def __init__(self, stored_version: int, supported_version: int) -> None:
        self.stored_version = stored_version
        self.supported_version = supported_version
        super().__init__(
            f"store schema {stored_version} is newer than supported schema {supported_version}"
        )


@dataclass(frozen=True, slots=True)
class LeaseKey:
    module_id: str
    backend: Literal["sqlite", "postgres"]
    scope_key: str


@dataclass(frozen=True, slots=True)
class Fence:
    lease: LeaseKey
    epoch: int


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoreStatus:
    state: Literal["ready", "store_ahead"]
    version: int
    supported_version: int

    @property
    def writable(self) -> bool:
        return self.state == "ready"


_T = TypeVar("_T")


class SQLiteStore:
    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection,
        lease_file: object,
        fence: Fence,
        status: StoreStatus,
    ) -> None:
        self.path = path
        self._connection = connection
        self._lease_file = lease_file
        self.fence = fence
        self.status = status

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        lease_key: LeaseKey,
        supported_version: int,
        migrations: Iterable[Migration],
    ) -> SQLiteStore:
        if lease_key.backend != "sqlite":
            raise StorageError("SQLite store requires a sqlite lease key")
        ordered = tuple(migrations)
        _validate_migrations(ordered)
        if supported_version != len(ordered):
            raise StorageError("supported version must match the migration catalog")
        store_path = Path(path)
        store_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(store_path.parent, 0o700)
        if store_path.exists() and not stat.S_ISREG(store_path.lstat().st_mode):
            raise StorageError(f"store path is not a regular file: {store_path}")

        lease_file, fence = _acquire_local_lease(
            Path(f"{store_path}.lease"), lease_key
        )
        try:
            from gideon.core.database_privacy import prepare_database

            prepare_database(store_path, anywhere=True)
            connection = sqlite3.connect(store_path, timeout=5, isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            os.chmod(store_path, 0o600)
            has_metadata = bool(
                connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='hypermid_migrations')"
                ).fetchone()[0]
            )
            applied = _read_applied(connection) if has_metadata else []
            _validate_applied(applied, ordered)
            stored_version = applied[-1][0] if applied else 0
            if stored_version > supported_version:
                return cls(
                    store_path,
                    connection,
                    lease_file,
                    fence,
                    StoreStatus("store_ahead", stored_version, supported_version),
                )

            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            _initialize_metadata(connection)

            for migration in ordered:
                if stored_version < migration.version <= supported_version:
                    _apply_migration(connection, migration)
                    stored_version = migration.version
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO hypermid_lease_epochs(module_id, backend, scope_key, epoch)
                    VALUES (?, 'sqlite', ?, ?)
                    ON CONFLICT(module_id, backend, scope_key)
                    DO UPDATE SET epoch=excluded.epoch
                    """,
                    (lease_key.module_id, lease_key.scope_key, fence.epoch),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{store_path}{suffix}")
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
            return cls(
                store_path,
                connection,
                lease_file,
                fence,
                StoreStatus("ready", stored_version, supported_version),
            )
        except BaseException:
            lease_file.close()
            raise

    def close(self) -> None:
        self._connection.close()
        self._lease_file.close()

    def read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        return operation(self._connection)

    def fenced_transaction(
        self, fence: Fence, operation: Callable[[sqlite3.Connection], _T]
    ) -> _T:
        if not self.status.writable:
            raise StoreAhead(self.status.version, self.status.supported_version)
        if fence.lease != self.fence.lease:
            raise StaleFence("write fence belongs to another store")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                """
                SELECT epoch FROM hypermid_lease_epochs
                WHERE module_id=? AND backend='sqlite' AND scope_key=?
                """,
                (fence.lease.module_id, fence.lease.scope_key),
            ).fetchone()
            if row is None or int(row[0]) != fence.epoch:
                raise StaleFence("write fence is stale")
            result = operation(self._connection)
            self._connection.commit()
            return result
        except BaseException:
            self._connection.rollback()
            raise

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


def postgres_database_name(module_id: str) -> str:
    slug = "".join(
        character.lower()
        if character.isascii() and (character.isalnum() or character == "_")
        else "_"
        for character in module_id
    )
    if not slug or slug[0].isdigit():
        slug = f"m_{slug}"
    digest = hashlib.sha256(module_id.encode("utf-8")).hexdigest()[:12]
    return f"{slug[:48]}_{digest}"


def _validate_migrations(migrations: tuple[Migration, ...]) -> None:
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected or not migration.name:
            raise StorageError("migration versions must be consecutive and begin at one")


def _initialize_metadata(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS hypermid_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            digest TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS hypermid_lease_epochs (
            module_id TEXT NOT NULL,
            backend TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            epoch INTEGER NOT NULL CHECK(epoch > 0),
            PRIMARY KEY(module_id, backend, scope_key)
        );
        COMMIT;
        """
    )


def _read_applied(connection: sqlite3.Connection) -> list[tuple[int, str, str]]:
    return [
        (int(version), str(name), str(digest))
        for version, name, digest in connection.execute(
            "SELECT version, name, digest FROM hypermid_migrations ORDER BY version"
        )
    ]


def _validate_applied(
    applied: list[tuple[int, str, str]], migrations: tuple[Migration, ...]
) -> None:
    for expected, (version, name, digest) in enumerate(applied, start=1):
        if version != expected:
            raise StorageError("stored migration chain is malformed")
        if expected <= len(migrations):
            migration = migrations[expected - 1]
            if name != migration.name or digest != migration.digest:
                raise StorageError(f"migration {version} differs from its recorded value")


def _apply_migration(connection: sqlite3.Connection, migration: Migration) -> None:
    quoted_name = migration.name.replace("'", "''")
    script = f"""
    BEGIN IMMEDIATE;
    {migration.sql}
    INSERT INTO hypermid_migrations(version, name, digest)
    VALUES ({migration.version}, '{quoted_name}', '{migration.digest}');
    COMMIT;
    """
    try:
        connection.executescript(script)
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _acquire_local_lease(path: Path, key: LeaseKey) -> tuple[object, Fence]:
    if path.exists() and not stat.S_ISREG(path.lstat().st_mode):
        raise StorageError(f"lease path is not a regular file: {path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    lease_file = os.fdopen(descriptor, "r+b", buffering=0)
    os.chmod(path, 0o600)
    try:
        import fcntl

        try:
            fcntl.flock(lease_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LeaseContended("lease is held by another live writer") from error
        raw = lease_file.read()
        if raw:
            try:
                record = json.loads(raw)
                persisted_key = LeaseKey(
                    module_id=record["key"]["module_id"],
                    backend=record["key"]["backend"],
                    scope_key=record["key"]["scope_key"],
                )
                epoch = int(record["epoch"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise StorageError("lease epoch is malformed") from error
            if persisted_key != key:
                raise StorageError("lease belongs to a different storage scope")
            if epoch < 1:
                raise StorageError("lease epoch is malformed")
        else:
            epoch = 0
        if epoch >= 9_007_199_254_740_991:
            raise StorageError("lease epoch is exhausted")
        fence = Fence(key, epoch + 1)
        payload = json.dumps(
            {
                "key": {
                    "module_id": key.module_id,
                    "backend": key.backend,
                    "scope_key": key.scope_key,
                },
                "epoch": fence.epoch,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        lease_file.seek(0)
        lease_file.truncate()
        lease_file.write(payload)
        lease_file.flush()
        os.fsync(lease_file.fileno())
        return lease_file, fence
    except BaseException:
        lease_file.close()
        raise
