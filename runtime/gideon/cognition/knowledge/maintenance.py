"""Deferred graph work coordinated by persistent dirty and clean watermarks."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)
_STATE_FILENAME = "graph_maintenance.json"
DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_STALENESS_SECS = 900.0


@dataclass
class MaintenancePass:
    name: str
    run: Callable[..., int]
    fatal: bool = False
    batched: bool = True


@dataclass
class MaintenanceResult:
    ran: bool = False
    reason: str = ""
    snapshot: float = 0.0
    per_pass: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(map(self._as_int, self.per_pass.values()))

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            converted = int(value)
        except (TypeError, ValueError):
            converted = 0
        return converted


_PASSES: dict[str, MaintenancePass] = {}
_IN_FLIGHT_PROBE: Callable[[], int] | None = None
_PROBE_WARNED = False


def _state_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir().joinpath(_STATE_FILENAME)


@dataclass(frozen=True)
class _WatermarkFile:
    path: Path

    def read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            logger.warning(
                "graph-maintenance state unreadable; treating the index as clean"
            )
            return {}
        return value if isinstance(value, dict) else {}

    def write(self, state: dict[str, Any], writer: Callable) -> None:
        writer(self.path, json.dumps(state, indent=2) + "\n")


def load_state() -> dict[str, Any]:
    try:
        return _WatermarkFile(_state_path()).read()
    except Exception:
        logger.warning(
            "graph-maintenance state unreadable; treating the index as clean"
        )
        return {}


def _save_state(state: dict[str, Any]) -> None:
    from gideon.core.atomic_write import atomic_write

    try:
        _WatermarkFile(_state_path()).write(state, atomic_write)
    except Exception:
        logger.warning("graph-maintenance state not written", exc_info=True)


@dataclass
class _Watermark:
    values: dict[str, Any]

    def timestamp(self, name: str) -> float:
        return float(self.values.get(name) or 0.0)

    @property
    def dirty(self) -> bool:
        return self.timestamp("clean_ts") < self.timestamp("dirty_ts")

    def observe(self, stamp: float, reason: str) -> None:
        if self.timestamp("dirty_since") == 0:
            self.values["dirty_since"] = stamp
        self.values["dirty_ts"] = stamp
        if reason:
            self.values["last_reason"] = reason[:120]

    def acknowledge(self, snapshot: float) -> None:
        self.values.update(
            clean_ts=max(self.timestamp("clean_ts"), float(snapshot)),
            last_run_ts=time.time(),
        )
        remaining_since = (
            max(float(snapshot), self.timestamp("dirty_since")) if self.dirty else 0.0
        )
        self.values["dirty_since"] = remaining_since

    def admission(
        self, stamp: float, depth: int, window: float | None
    ) -> tuple[bool, str]:
        if not self.dirty:
            return False, "clean"
        if depth <= 0:
            return True, "queue drained"
        limit = max_staleness_secs() if window is None else window
        age = stamp - float(self.values.get("dirty_since") or stamp)
        if age >= limit:
            return (
                True,
                f"dirt is {int(age)}s old (>= {int(limit)}s) while {depth} in flight",
            )
        return False, f"{depth} in flight and dirt is only {int(age)}s old"


def mark_dirty(*, now: float | None = None, reason: str = "") -> None:
    stamp = time.time() if now is None else now
    watermark = _Watermark(load_state())
    watermark.observe(stamp, reason)
    _save_state(watermark.values)


def is_dirty(state: dict[str, Any] | None = None) -> bool:
    return _Watermark(load_state() if state is None else state).dirty


def max_staleness_secs() -> float:
    try:
        from gideon.core.config.loader import AppConfig

        configured = getattr(
            AppConfig.load().knowledge, "maintenance_max_staleness_secs", 0
        )
        seconds = float(configured or 0)
    except Exception:
        logger.debug(
            "graph-maintenance staleness unreadable; using the default", exc_info=True
        )
        return DEFAULT_MAX_STALENESS_SECS
    return max(1.0, seconds) if seconds > 0 else DEFAULT_MAX_STALENESS_SECS


def is_due(
    *,
    now: float | None = None,
    in_flight: int = 0,
    state: dict[str, Any] | None = None,
    staleness: float | None = None,
) -> tuple[bool, str]:
    watermark = _Watermark(load_state() if state is None else state)
    return watermark.admission(
        time.time() if now is None else now, in_flight, staleness
    )


def clear_up_to(snapshot: float) -> None:
    watermark = _Watermark(load_state())
    watermark.acknowledge(snapshot)
    _save_state(watermark.values)


def register_pass(
    name: str,
    run: Callable[..., int],
    *,
    fatal: bool = False,
    replace: bool = True,
    batched: bool = True,
) -> None:
    if replace or name not in _PASSES:
        _PASSES[name] = MaintenancePass(name, run, fatal, batched)


def registered_passes() -> list[str]:
    return sorted(_PASSES.keys())


def clear_passes() -> None:
    _PASSES.clear()


@dataclass
class _BatchDrain:
    job: MaintenancePass
    size: int
    budget: int
    total: int = 0

    def run(self) -> int:
        attempts = range(max(1, self.budget)) if self.job.batched else range(1)
        for _ in attempts:
            count = int(self.job.run(batch_size=self.size) or 0)
            self.total += count
            if count <= 0:
                break
        return self.total


def _claim_batches(p: MaintenancePass, *, batch_size: int, max_batches: int) -> int:
    return _BatchDrain(p, batch_size, max_batches).run()


@dataclass(frozen=True)
class _PassSweep:
    result: MaintenanceResult
    batch_size: int
    max_batches: int

    def run(self) -> MaintenanceResult:
        for name in registered_passes():
            job = _PASSES[name]
            if not self.apply(job):
                break
        clear_up_to(self.result.snapshot)
        return self.result

    def apply(self, job: MaintenancePass) -> bool:
        try:
            total = _claim_batches(
                job, batch_size=self.batch_size, max_batches=self.max_batches
            )
        except Exception as exc:
            logger.warning(
                "graph maintenance pass %r failed: %s", job.name, exc, exc_info=True
            )
            self.result.errors[job.name] = f"{type(exc).__name__}: {exc}"
            return not job.fatal
        self.result.per_pass[job.name] = total
        return True


def execute(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 20,
    now: float | None = None,
) -> MaintenanceResult:
    state = load_state()
    snapshot = float(state.get("dirty_ts") or (time.time() if now is None else now))
    receipt = MaintenanceResult(ran=True, reason="running", snapshot=snapshot)
    return _PassSweep(receipt, batch_size, max_batches).run()


def run_maintenance(
    *,
    now: float | None = None,
    in_flight: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
) -> MaintenanceResult:
    try:
        depth = _in_flight_depth() if in_flight is None else in_flight
        due, reason = is_due(now=now, in_flight=depth, state=load_state())
        if force or due:
            return execute(batch_size=batch_size, now=now)
        return MaintenanceResult(ran=False, reason=reason)
    except Exception as exc:
        logger.warning("graph maintenance tick failed", exc_info=True)
        return MaintenanceResult(
            ran=False, reason=f"error: {type(exc).__name__}: {exc}"
        )


def set_in_flight_probe(probe: Callable[[], int] | None) -> None:
    global _IN_FLIGHT_PROBE, _PROBE_WARNED
    _IN_FLIGHT_PROBE, _PROBE_WARNED = probe, False


def has_in_flight_probe() -> bool:
    return _IN_FLIGHT_PROBE is not None


def _in_flight_depth() -> int:
    global _PROBE_WARNED
    probe = _IN_FLIGHT_PROBE
    if probe is not None:
        try:
            return max(0, int(probe() or 0))
        except Exception:
            logger.debug(
                "ingest-queue depth probe failed; treating it as drained", exc_info=True
            )
            return 0
    if not _PROBE_WARNED:
        logger.warning(
            "graph maintenance has no in-flight probe installed — treating the ingest "
            "queue as drained, so a bulk import will not coalesce"
        )
        _PROBE_WARNED = True
    return 0
