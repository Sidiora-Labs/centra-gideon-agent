"""Measure and compact manifest-owned SQLite stores beneath the active home."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gideon.operations.durability import inventory


@dataclass
class ReclaimResult:
    before_bytes: int = 0
    after_bytes: int = 0
    stores: int = 0
    per_store_net_change: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def net_change_bytes(self) -> int:
        return self.after_bytes - self.before_bytes

    @property
    def freed_bytes(self) -> int:
        return max(0, -self.net_change_bytes)

    @property
    def growth_bytes(self) -> int:
        return max(0, self.net_change_bytes)

    @property
    def per_store_freed(self) -> dict[str, int]:
        return {
            key: -delta for key, delta in self.per_store_net_change.items() if delta < 0
        }

    @property
    def per_store_growth(self) -> dict[str, int]:
        return {
            key: delta for key, delta in self.per_store_net_change.items() if delta > 0
        }

    def to_dict(self) -> dict:
        return {
            "before_bytes": self.before_bytes,
            "after_bytes": self.after_bytes,
            "net_change_bytes": self.net_change_bytes,
            "freed_bytes": self.freed_bytes,
            "growth_bytes": self.growth_bytes,
            "stores": self.stores,
            "per_store_net_change_bytes": dict(self.per_store_net_change),
            "per_store_freed": self.per_store_freed,
            "per_store_growth": self.per_store_growth,
            "skipped": dict(self.skipped),
        }

    def describe(self) -> str:
        if self.freed_bytes:
            text = (
                f"Reclaimed {self.freed_bytes} bytes across {self.stores} database(s)."
            )
        elif self.growth_bytes:
            text = (
                f"No net space reclaimed: measured footprint grew {self.growth_bytes} "
                f"bytes while compacting {self.stores} database(s)."
            )
        elif self.per_store_net_change:
            text = (
                f"No net footprint change after compacting {self.stores} database(s)."
            )
        else:
            text = f"No footprint change across {self.stores} database(s)."
        if self.skipped:
            text += f" Skipped {len(self.skipped)} store(s)."
        return text


def _safe_path(home: Path, relative: str) -> Path:
    path = home / relative
    if not path.resolve().is_relative_to(home):
        raise ValueError("store is outside the active home")
    for candidate in (path, *path.parents):
        if candidate == home:
            break
        if candidate.is_symlink():
            raise ValueError("linked stores are not compacted")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).is_symlink():
            raise ValueError("linked database sidecars are not compacted")
    return path


def _store_bytes(path: Path) -> int:
    total = 0
    for candidate in (
        path,
        *(Path(str(path) + ext) for ext in ("-wal", "-shm", "-journal")),
    ):
        try:
            total += candidate.stat().st_size
        except FileNotFoundError:
            continue
    return total


def reclaim_store(path: Path) -> None:
    connection = sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, timeout=0.1, isolation_level=None
    )
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND sql LIKE '%USING fts5%' COLLATE NOCASE"
        ).fetchall()
        for (name,) in tables:
            quoted = '"' + str(name).replace('"', '""') + '"'
            try:
                connection.execute(f"INSERT INTO {quoted}({quoted}) VALUES('optimize')")
            except sqlite3.Error:
                continue
        connection.execute("PRAGMA optimize")
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def reclaim(home: Path) -> ReclaimResult:
    home = home.resolve()
    result = ReclaimResult()
    for entry in inventory.sqlite_entries():
        try:
            path = _safe_path(home, entry.path)
            if not path.is_file():
                continue
            before = _store_bytes(path)
            reclaim_store(path)
            after = _store_bytes(path)
        except ValueError as exc:
            result.skipped[entry.id] = str(exc)
            continue
        except sqlite3.Error as exc:
            result.skipped[entry.id] = (
                "database is busy"
                if getattr(exc, "sqlite_errorcode", 0)
                in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                else "database compaction failed"
            )
            continue
        except OSError:
            result.skipped[entry.id] = "store is unavailable"
            continue
        result.stores += 1
        result.before_bytes += before
        result.after_bytes += after
        if before != after:
            result.per_store_net_change[entry.id] = after - before
    return result


def footprint_cmd(args) -> int:
    from gideon.operations.durability import service

    if args.reclaim:
        result = service.run_reclaim()
        service.persist_job_result(result)
        if args.json:
            print(json.dumps(result.to_dict()))
        else:
            print(result.skipped or result.detail)
            for key, reason in result.extra.get("skipped", {}).items():
                print(f"  {key}: {reason}")
        return 0 if result.ok and not result.skipped else 1
    home = service.active_home().resolve()
    sizes: dict[str, int] = {}
    for entry in inventory.sqlite_entries():
        try:
            path = _safe_path(home, entry.path)
            if path.is_file():
                sizes[entry.id] = _store_bytes(path)
        except (OSError, ValueError):
            continue
    if args.json:
        print(
            json.dumps({"total_bytes": sum(sizes.values()), "per_store_bytes": sizes})
        )
    else:
        print(f"Declared databases occupy {sum(sizes.values())} bytes.")
        for key, size in sorted(sizes.items(), key=lambda row: (-row[1], row[0])):
            print(f"  {key}: {size} bytes")
    return 0
