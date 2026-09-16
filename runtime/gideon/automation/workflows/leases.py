"""Atomic persistent leases serialized by target-specific process locks."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from gideon.automation.workflows import containers
from gideon.automation.workflows.containers import Claim
from gideon.core.atomic_write import atomic_write
from gideon.core.concurrency import single_flight
from gideon.core.config import loader as config_loader

logger = logging.getLogger(__name__)
_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]")


def config_dir() -> Path:
    return config_loader.config_dir()


def _leases_dir() -> Path:
    directory = config_dir().joinpath("locks", "leases")
    directory.mkdir(exist_ok=True, parents=True)
    return directory


def _lease_path(target_id: str) -> Path:
    name = _UNSAFE.sub("_", target_id)[:64] or "_"
    return _leases_dir().joinpath(name + ".json")


class ClaimFile:
    def __init__(self, target_id):
        self.target_id = target_id

    def read(self):
        path = _lease_path(self.target_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        except Exception:
            logger.debug("claim read failed for %s", self.target_id, exc_info=True)
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return Claim(
                holder=str(payload.get("holder", "") or ""),
                **{
                    key: convert(payload.get(key, fallback) or fallback)
                    for key, convert, fallback in (
                        ("expires_at", float, 0.0),
                        ("taken_at", float, 0.0),
                        ("renewals", int, 0),
                    )
                },
            )
        except (TypeError, ValueError):
            return None

    def write(self, claim):
        path = _lease_path(self.target_id)
        if claim is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, json.dumps(claim.to_dict()))

    def mutate(self, holder, *, release=False, ttl=containers.DEFAULT_LEASE_SECS):
        with single_flight("claim:" + self.target_id) as acquired:
            if not acquired:
                return (read_claim(self.target_id) if release else None), "contended"
            if release:
                decision = containers.release(read_claim(self.target_id), holder)
                self.write(decision[0])
                return decision
            now = time.time()
            existing = read_claim(self.target_id)
            if existing is not None and existing.expired(now):
                existing = None
            decision = containers.claim(holder, now=now, ttl=ttl, existing=existing)
            if decision[0] is not None:
                self.write(decision[0])
            return decision


def read_claim(target_id: str) -> Claim | None:
    return ClaimFile(target_id).read()


def acquire_claim(
    target_id: str, holder: str, *, ttl: int = containers.DEFAULT_LEASE_SECS
) -> tuple[Claim | None, str]:
    return ClaimFile(target_id).mutate(holder, ttl=ttl)


def release_claim(target_id: str, holder: str) -> tuple[Claim | None, str]:
    return ClaimFile(target_id).mutate(holder, release=True)
