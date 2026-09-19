"""Verified credential transfers with byte-preserving rollback snapshots."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from gideon.core.config import loader as _loader
from gideon.core.config.credentials import (
    _dotenv_credentials,
    _dotenv_remove_credentials,
    _keychain_delete,
    _keychain_get,
    _keychain_index,
    _keychain_save,
    credential_backend,
    parse_dotenv,
    requested_credential_backend,
)

logger = logging.getLogger(__name__)
MIGRATION_ID = "credentials_to_keychain"
ROLLBACK_FILENAME = ".env.pre-keychain"


@dataclass
class MigrationResult:
    ok: bool
    reason: str = ""
    moved: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    rollback_available: bool = False

    def to_dict(self) -> dict:
        values = {item.name: getattr(self, item.name) for item in fields(self)}
        return {
            key: list(value) if isinstance(value, list) else value
            for key, value in values.items()
        }


def rollback_snapshot_path() -> Path:
    return _loader.env_path().parent / ROLLBACK_FILENAME


def pending_dotenv_keys() -> list[str]:
    return sorted(_dotenv_credentials().keys())


def credential_migration_status() -> dict:
    active = credential_backend()
    names = pending_dotenv_keys()
    return dict(
        migration=MIGRATION_ID,
        backend=active,
        requested=requested_credential_backend(),
        blocked=active != "keychain",
        pending_keys=names,
        pending=len(names),
        keychain_keys=len(_keychain_index()),
        rollback_available=rollback_snapshot_path().exists(),
        snapshot_name=ROLLBACK_FILENAME,
    )


def _audit(operation: str, outcome: str, resources: str, error: str = "") -> None:
    try:
        from gideon.security.sel import sel

        event = dict(
            caller=os.environ.get("USER", "unknown"),
            operation=operation,
            outcome=outcome,
            source="credential_migration",
            resources=resources,
            error=error,
        )
        sel().log_api_access(**event)
    except Exception:
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


def _write_snapshot() -> None:
    from gideon.core.atomic_write import atomic_write_bytes

    snapshot = rollback_snapshot_path()
    if not snapshot.exists():
        atomic_write_bytes(
            snapshot, _loader.env_path().read_bytes(), mode=0o600, fsync=True
        )


def _parse_env_bytes(raw: bytes) -> dict[str, str]:
    return parse_dotenv(raw.decode("utf-8", "replace"))


class CredentialTransfer:
    def result(self, ok: bool, reason: str = "", **details) -> MigrationResult:
        details.setdefault("rollback_available", rollback_snapshot_path().exists())
        return MigrationResult(ok=ok, reason=reason, **details)

    def store(self, key: str, value: str) -> str:
        if _keychain_get(key) == value:
            return "already"
        if not _keychain_save(key, value):
            return "failed"
        if _keychain_get(key) == value:
            return "moved"
        logger.warning("keychain read-back mismatch for %s; leaving it in .env", key)
        _keychain_delete(key)
        return "failed"

    def move(self, confirmed: bool) -> MigrationResult:
        if not confirmed:
            return self.result(
                False,
                "confirmation required: this moves stored credentials between stores",
            )
        if credential_backend() != "keychain":
            reasons = {
                True: "no usable OS keyring backend is available on this machine; credentials stay in .env at mode 0600",
                False: "the OS keychain is not the active credential backend — turn on Settings → Security → 'Store credentials in the OS keychain' first",
            }
            reason = reasons[requested_credential_backend() == "keychain"]
            _audit(MIGRATION_ID, "refused", "", reason)
            return self.result(False, reason)
        source = _dotenv_credentials()
        if not source:
            return self.result(True)
        _write_snapshot()
        outcomes: dict = {"moved": [], "already": [], "failed": []}
        for name in sorted(source):
            outcomes[self.store(name, source[name])].append(name)
        verified = outcomes["moved"] + outcomes["already"]
        if verified:
            _dotenv_remove_credentials(verified)
        failed = outcomes["failed"]
        _audit(
            MIGRATION_ID,
            "partial" if failed else "ok",
            ",".join(verified),
            ",".join(failed),
        )
        reason = (
            f"{len(failed)} credential(s) could not be verified in the keychain and were left in .env: "
            + ", ".join(failed)
            if failed
            else ""
        )
        return self.result(not failed, reason, **outcomes)

    def restore(self, confirmed: bool) -> MigrationResult:
        if not confirmed:
            return self.result(
                False,
                "confirmation required: this rewrites .env from the pre-migration snapshot",
            )
        snapshot = rollback_snapshot_path()
        operation = f"{MIGRATION_ID}_rollback"
        if not snapshot.exists():
            reason = f"no pre-migration snapshot ({ROLLBACK_FILENAME}) — nothing to roll back to"
            _audit(operation, "refused", "", reason)
            return self.result(False, reason, rollback_available=False)
        from gideon.core.atomic_write import atomic_write_bytes

        content = snapshot.read_bytes()
        restored = _parse_env_bytes(content)
        names = sorted(restored)
        atomic_write_bytes(_loader.env_path(), content, mode=0o600, fsync=True)
        failed = [name for name in names if not _keychain_delete(name)]
        os.environ.update(restored)
        if not failed:
            snapshot.unlink()
        _audit(operation, "partial" if failed else "ok", ",".join(names))
        reason = (
            f"{len(failed)} keychain entr(y/ies) could not be removed: "
            + ", ".join(failed)
            if failed
            else ""
        )
        return self.result(
            not failed,
            reason,
            moved=names,
            failed=failed,
            rollback_available=bool(failed),
        )


def migrate_credentials_to_keychain(*, confirm: bool = False) -> MigrationResult:
    return CredentialTransfer().move(confirm)


def rollback_credentials_to_keychain(*, confirm: bool = False) -> MigrationResult:
    return CredentialTransfer().restore(confirm)


def verify_credential_migration() -> tuple[bool, dict]:
    snapshot = rollback_snapshot_path()
    if not snapshot.exists():
        return True, {"checked": 0, "missing": [], "still_in_dotenv": []}
    expected = _parse_env_bytes(snapshot.read_bytes())
    active = _dotenv_credentials()
    evidence = {
        "checked": len(expected),
        "missing": sorted(
            key for key in expected if _keychain_get(key) != expected[key]
        ),
        "still_in_dotenv": sorted(expected.keys() & active.keys()),
    }
    return not evidence["missing"] and not evidence["still_in_dotenv"], evidence
