"""Runner catalog composition, measured health, and adapter admission evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


__all__ = [
    "AdapterPin",
    "AdapterVerification",
    "HealthEvidence",
    "RunnerDefinition",
    "UnverifiedAdapterError",
    "adapter_lock_path",
    "catalog",
    "evidence_is_stale",
    "guard_unattended_spawn",
    "health_check_interval_secs",
    "load_evidence",
    "probe_runner",
    "record_capabilities",
    "record_provenance",
    "runner_rows",
    "runtime_id_for_agent",
    "verify_adapter",
]


_BUILTIN_CATALOG = Path(__file__).resolve().parent / "runner_catalog.json"


USER_CATALOG_DIR_NAME = "runners"


ADAPTER_LOCK_NAME = ".gideon-lock.json"


_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


_SEMVER_RE = re.compile(r"\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?")


PROBE_TIMEOUT_SECS = 12.0


class UnverifiedAdapterError(RuntimeError):
    """The configured admission policy rejected an unattended adapter."""


@dataclass(frozen=True)
class AdapterPin:
    npm_pkg: str
    env_var: str = ""
    bin_names: tuple[str, ...] = ()
    version: str = ""
    integrity: str = ""

    @property
    def pinned(self) -> bool:
        return all((self.version, self.integrity))


@dataclass(frozen=True)
class RunnerDefinition:
    id: str
    display_name: str
    runtime_id: str
    bin_names: tuple[str, ...]
    env_var: str = ""
    version_args: tuple[str, ...] = ("--version",)
    acp_args: tuple[str, ...] = ()
    dialect: str = ""
    adapter: AdapterPin | None = None
    source: str = "builtin"


@dataclass(frozen=True)
class HealthEvidence:
    ok: bool
    probe: str
    checked_at: str
    version: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    resolved_command: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        fields = ("ok", "probe", "checked_at", "version", "latency_ms", "error")
        values = {key: getattr(self, key) for key in fields}
        values["resolved_command"] = list(self.resolved_command)
        return values


@dataclass(frozen=True)
class AdapterVerification:
    state: str
    detail: str
    resolved_command: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        return self.state == "verified" or self.state == "no_adapter"


@dataclass
class RunnerRow:
    definition: RunnerDefinition
    evidence: HealthEvidence | None
    capabilities: dict[str, Any] | None
    adapter: AdapterVerification
    lease: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return _RunnerPresentation.project(self)


class _RunnerDocument:
    def __init__(self, values):
        self.values = values

    def text(self, key, default=""):
        return str(self.values.get(key) or default)

    def words(self, key):
        return _as_tuple(self.values.get(key))

    def definition(self, source, fallback):
        identifier = self.text("id", fallback).strip().lower()
        if _SAFE_ID_RE.fullmatch(identifier) is None:
            raise ValueError(f"Invalid runner id: {identifier!r}")
        binaries = self.words("bin_names")
        if not binaries:
            raise ValueError(f"Runner {identifier!r} declares no bin_names")
        options = {key: self.text(key) for key in ("env_var", "dialect")}
        options.update(
            id=identifier,
            display_name=self.text("display_name", identifier),
            runtime_id=self.text("runtime_id", f"acp:{identifier}"),
            bin_names=binaries,
            version_args=self.words("version_args") or ("--version",),
            acp_args=self.words("acp_args"),
            adapter=_adapter_from(self.values.get("adapter")),
            source=source,
        )
        return RunnerDefinition(**options)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(filter(None, map(str, value)))
    return ()


def _adapter_from(raw: Any) -> AdapterPin | None:
    if isinstance(raw, dict):
        record = _RunnerDocument(raw)
        package = record.text("npm_pkg").strip()
        if package:
            options = {
                key: record.text(key) for key in ("env_var", "version", "integrity")
            }
            return AdapterPin(
                npm_pkg=package, bin_names=record.words("bin_names"), **options
            )
    return None


def _definition_from(
    raw: dict[str, Any], *, source: str, fallback_id: str = ""
) -> RunnerDefinition:
    return _RunnerDocument(raw).definition(source, fallback_id)


def user_catalog_dir() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir().joinpath(USER_CATALOG_DIR_NAME)


class _CatalogLayers:
    @staticmethod
    def shipped():
        try:
            payload = json.loads(_BUILTIN_CATALOG.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "runner catalog: shipped %s unreadable", _BUILTIN_CATALOG, exc_info=True
            )
            payload = {}
        for values in payload.get("runners") or []:
            try:
                yield _definition_from(values, source="builtin")
            except Exception:
                logger.warning(
                    "runner catalog: skipping invalid shipped row %r",
                    values,
                    exc_info=True,
                )

    @staticmethod
    def local():
        try:
            root = user_catalog_dir()
            paths = sorted(root.glob("*.json")) if root.is_dir() else []
        except Exception:
            paths = []
        for path in paths:
            try:
                values = json.loads(path.read_text(encoding="utf-8"))
                yield _definition_from(values, source="user", fallback_id=path.stem)
            except Exception:
                logger.warning(
                    "runner catalog: skipping invalid BYO row %s", path, exc_info=True
                )

    def merged(self):
        result: dict = {}
        for layer in (self.shipped, self.local):
            result.update((row.id, row) for row in layer())
        return result


def catalog() -> dict[str, RunnerDefinition]:
    return _CatalogLayers().merged()


def definition_for_runtime(runtime_id: str) -> RunnerDefinition | None:
    requested = (runtime_id or "").strip()
    return (
        next((row for row in catalog().values() if row.runtime_id == requested), None)
        if requested
        else None
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sidecar_path(runner_id: str) -> Path:
    from gideon.engine import agent_metadata

    if _SAFE_ID_RE.fullmatch(runner_id or "") is None:
        raise ValueError(f"Invalid runner id: {runner_id!r}")
    return agent_metadata.metadata_dir().joinpath(
        ".".join((runner_id, "runner", "json"))
    )


def _read_sidecar(runner_id: str) -> dict[str, Any]:
    try:
        raw = sidecar_path(runner_id).read_text(encoding="utf-8")
        return json.loads(raw) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.debug("runner sidecar unreadable for %s", runner_id, exc_info=True)
        return {}


def _write_sidecar(runner_id: str, payload: dict[str, Any]) -> Path:
    from gideon.core.atomic_write import atomic_write

    target = sidecar_path(runner_id)
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    atomic_write(target, serialized + "\n")
    return target


@dataclass(frozen=True)
class _RunnerEvidence:
    identifier: str

    def replace(self, section, value):
        document = _read_sidecar(self.identifier)
        document.update(runner=self.identifier)
        document[section] = value
        return _write_sidecar(self.identifier, document)

    def section(self, name, required):
        value = _read_sidecar(self.identifier).get(name)
        return value if isinstance(value, dict) and value.get(required) else None


def load_evidence(runner_id: str) -> HealthEvidence | None:
    record = _RunnerEvidence(runner_id).section("last_check", "checked_at")
    if record is None:
        return None
    latency = record.get("latency_ms")
    measured = {
        key: str(record[key]) if record.get(key) else None
        for key in ("version", "error")
    }
    return HealthEvidence(
        ok=bool(record.get("ok")),
        probe=str(record.get("probe") or "unknown"),
        checked_at=str(record["checked_at"]),
        latency_ms=int(latency) if isinstance(latency, (int, float)) else None,
        resolved_command=_as_tuple(record.get("resolved_command")),
        **measured,
    )


def health_check_interval_secs() -> int:
    try:
        from gideon.core.config.loader import AppConfig

        configured = int(AppConfig.load().agent.runner_health_check_secs)
    except Exception:
        logger.debug(
            "runner health-check interval unreadable; using the default", exc_info=True
        )
        return 3600
    return max(configured, 60)


def evidence_is_stale(
    evidence: HealthEvidence | None, *, interval_secs: int | None = None
) -> bool | None:
    if evidence is None:
        return None
    try:
        timestamp = datetime.fromisoformat(str(evidence.checked_at))
    except (TypeError, ValueError):
        return None
    timestamp = (
        timestamp
        if timestamp.tzinfo is not None
        else timestamp.replace(tzinfo=timezone.utc)
    )
    window = (
        health_check_interval_secs()
        if interval_secs is None
        else max(60, int(interval_secs))
    )
    elapsed = datetime.now(timezone.utc) - timestamp
    return elapsed.total_seconds() > window


def record_evidence(runner_id: str, evidence: HealthEvidence) -> Path:
    return _RunnerEvidence(runner_id).replace("last_check", evidence.to_dict())


def load_capabilities(runner_id: str) -> dict[str, Any] | None:
    return _RunnerEvidence(runner_id).section("capabilities", "recorded_at")


def record_capabilities(
    runtime_id: str,
    *,
    models: list[str] | None = None,
    modes: list[str] | None = None,
    efforts: list[str] | None = None,
) -> Path | None:
    definition = definition_for_runtime(runtime_id)
    if definition is not None:
        try:
            values: dict[str, Any] = dict(source="initialize", recorded_at=_now_iso())
            values.update(
                (name, list(items or []))
                for name, items in (
                    ("models", models),
                    ("permission_modes", modes),
                    ("efforts", efforts),
                )
            )
            return _RunnerEvidence(definition.id).replace("capabilities", values)
        except Exception:
            logger.debug(
                "runner capability persist failed for %s", runtime_id, exc_info=True
            )
    return None


def resolve_runner_command(defn: RunnerDefinition) -> list[str] | None:
    from gideon.integrations.acp.cli_resolve import resolve_acp_cli

    variable = (
        defn.env_var or "GIDEON_RUNNER_" + defn.id.upper().replace("-", "_") + "_BIN"
    )
    return resolve_acp_cli(
        env_var=variable, bin_names=list(defn.bin_names), npm_pkg=None
    )


@dataclass
class _VersionReading:
    command: tuple[str, ...]
    started: float

    def result(self, *, ok=False, timed=False, version=None, error=None):
        elapsed = (
            int(round((time.monotonic() - self.started) * 1000)) if timed else None
        )
        return HealthEvidence(
            ok=ok,
            probe="version",
            checked_at=_now_iso(),
            latency_ms=elapsed,
            version=version,
            error=error,
            resolved_command=self.command,
        )

    def completed(self, process):
        output, errors = (
            (value or "").strip() for value in (process.stdout, process.stderr)
        )
        if process.returncode:
            return self.result(
                timed=True,
                error=errors or output or f"exited {process.returncode} with no output",
            )
        return self.result(
            ok=True, timed=True, version=_parse_version(output or errors)
        )


def probe_runner(defn: RunnerDefinition, *, persist: bool = True) -> HealthEvidence:
    argv = resolve_runner_command(defn)
    if argv:
        reading = _VersionReading(tuple(argv), time.monotonic())
        try:
            process = subprocess.run(
                [*argv, *defn.version_args],
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as failure:
            evidence = reading.result(timed=True, error=f"TimeoutExpired: {failure}")
        except OSError as failure:
            evidence = reading.result(error=f"{type(failure).__name__}: {failure}")
        else:
            evidence = reading.completed(process)
    else:
        names = ", ".join(defn.bin_names)
        override = f"; set {defn.env_var} to override" if defn.env_var else ""
        evidence = HealthEvidence(
            ok=False,
            probe="path",
            checked_at=_now_iso(),
            error=f"{defn.bin_names[0]!r} not found on PATH (looked for: {names}){override}",
        )
    return _finish(defn, evidence, persist)


def _finish(
    defn: RunnerDefinition, evidence: HealthEvidence, persist: bool
) -> HealthEvidence:
    if persist:
        try:
            record_evidence(defn.id, evidence)
        except Exception:
            logger.debug(
                "runner evidence persist failed for %s", defn.id, exc_info=True
            )
    return evidence


def _parse_version(text: str) -> str | None:
    match = _SEMVER_RE.search(text or "")
    return match[0] if match else None


def managed_adapter_prefix() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir().joinpath("acp-adapters")


def adapter_lock_path() -> Path:
    return managed_adapter_prefix().joinpath(ADAPTER_LOCK_NAME)


def _read_lock() -> dict[str, Any]:
    try:
        raw = adapter_lock_path().read_text(encoding="utf-8")
        return json.loads(raw) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("adapter provenance ledger unreadable", exc_info=True)
        return {}


def installed_adapter_facts(npm_pkg: str) -> dict[str, str]:
    try:
        text = (
            managed_adapter_prefix()
            .joinpath("package-lock.json")
            .read_text(encoding="utf-8")
        )
        document = json.loads(text)
    except Exception:
        return {}
    record = (document.get("packages") or {}).get(f"node_modules/{npm_pkg}")
    if not isinstance(record, dict):
        return {}
    facts = {key: str(record.get(key) or "") for key in ("version", "integrity")}
    return facts if any(facts.values()) else {}


@dataclass(frozen=True)
class _AdapterReceipt:
    package: str
    facts: dict

    def accepts(self, pin):
        return (
            pin is None
            or not pin.pinned
            or all(
                self.facts.get(key) == getattr(pin, key)
                for key in ("version", "integrity")
            )
        )

    def write(self):
        from gideon.core.atomic_write import atomic_write

        ledger = _read_lock()
        ledger[self.package] = dict(
            version=self.facts.get("version", ""),
            integrity=self.facts.get("integrity", ""),
            recorded_at=_now_iso(),
        )
        target = adapter_lock_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, json.dumps(ledger, indent=2, sort_keys=True) + "\n")


def record_provenance(npm_pkg: str, *, pin: AdapterPin | None = None) -> bool:
    receipt = _AdapterReceipt(npm_pkg, installed_adapter_facts(npm_pkg))
    if not receipt.facts:
        return False
    if receipt.accepts(pin):
        receipt.write()
        return True
    assert pin is not None
    logger.warning(
        "acp adapter %s: install does not match the declared pin "
        "(installed %s/%s, pinned %s/%s) — provenance NOT recorded",
        npm_pkg,
        receipt.facts.get("version"),
        (receipt.facts.get("integrity") or "")[:16],
        pin.version,
        pin.integrity[:16],
    )
    return False


@dataclass(frozen=True)
class _AdapterAudit:
    pin: AdapterPin
    command: tuple[str, ...]

    def verdict(self, state, detail):
        return AdapterVerification(state, detail, self.command)

    def inspect(self):
        package = self.pin.npm_pkg
        facts = installed_adapter_facts(package)
        recorded = _read_lock().get(package)
        if not isinstance(recorded, dict):
            detail = (
                f"{package} resolves on disk but has no recorded provenance — "
                "provision it through Gideon so its integrity is on file"
            )
        elif not facts:
            detail = (
                f"{package} has recorded provenance but npm reports nothing "
                "installed in the managed prefix — the adapter on PATH is a "
                "different install than the one that was verified"
            )
        elif facts.get("integrity") != recorded.get("integrity"):
            detail = (
                f"{package} integrity changed since it was provisioned "
                f"(recorded {str(recorded.get('integrity'))[:24]}, "
                f"installed {str(facts.get('integrity'))[:24]})"
            )
        elif self.pin.pinned and facts.get("version") != self.pin.version:
            detail = f"{package} is installed at {facts.get('version')} but the catalog pins {self.pin.version}"
        else:
            detail = f"{package}@{facts.get('version')} matches its recorded integrity"
            if self.pin.pinned:
                detail += " and the catalog pin"
            return self.verdict("verified", detail)
        return self.verdict("unverified", detail)


def verify_adapter(defn: RunnerDefinition) -> AdapterVerification:
    pin = defn.adapter
    if pin is None:
        return AdapterVerification(
            "no_adapter",
            "launches its own binary — no npm ACP adapter in the launch path",
        )
    from gideon.integrations.acp.cli_resolve import is_npx_fallback, resolve_acp_cli

    command = resolve_acp_cli(
        env_var=pin.env_var or defn.id.upper().replace("-", "_") + "_ACP_BIN",
        bin_names=list(pin.bin_names) or [pin.npm_pkg.rsplit("/", 1)[-1]],
        npm_pkg=pin.npm_pkg,
    )
    if not command:
        return AdapterVerification(
            "absent", f"{pin.npm_pkg} is not installed and cannot be resolved"
        )
    audit = _AdapterAudit(pin, tuple(command))
    if is_npx_fallback(command):
        return audit.verdict(
            "unverified",
            f"resolves via `npx -y {pin.npm_pkg}`, which fetches at launch — "
            "an npx run cannot be pinned or checksum-verified",
        )
    return audit.inspect()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        buffer = bytearray(1 << 16)
        while count := stream.readinto(buffer):
            digest.update(memoryview(buffer)[:count])
    return "sha256:" + digest.hexdigest()


def runtime_id_for_agent(agent: str | None) -> str:
    try:
        from gideon.core.config.loader import AppConfig

        config = AppConfig.load()
        profile = (config.agents or {}).get(agent) if agent else None
        overrides = getattr(profile, "provider", "") if profile else ""
        selected = overrides or getattr(config.agent, "provider", "") or "native"
    except Exception:
        return ""
    selected = str(selected)
    return selected if selected.startswith("acp") else ""


def guard_unattended_spawn(runtime_id: str, *, unattended: bool) -> None:
    if unattended and runtime_id:
        try:
            from gideon.core.config.loader import AppConfig

            enabled = bool(AppConfig.load().agent.unattended_requires_verified_adapter)
        except Exception:
            logger.debug("adapter-verification gate: config unreadable", exc_info=True)
            return
        if enabled:
            _require_verified_runtime(runtime_id)


def _require_verified_runtime(runtime_id):
    definition = definition_for_runtime(runtime_id)
    if definition is None:
        reason = (
            f"Unattended spawn refused: {runtime_id!r} has no runner-catalog row, so its "
            "adapter cannot be verified. Add a definition under "
            f"{USER_CATALOG_DIR_NAME}/<id>.json, or turn off "
            "agents.unattended_requires_verified_adapter."
        )
    else:
        verdict = verify_adapter(definition)
        if verdict.verified:
            return
        reason = (
            f"Unattended spawn refused: {definition.display_name} adapter is not verified "
            f"({verdict.state}) — {verdict.detail}. Provision the adapter, or turn off "
            "agents.unattended_requires_verified_adapter."
        )
    raise UnverifiedAdapterError(reason)


class _RunnerPresentation:
    @staticmethod
    def project(row):
        definition = row.definition
        result = {
            key: getattr(definition, key)
            for key in ("id", "display_name", "runtime_id", "source", "dialect")
        }
        pin = definition.adapter
        result.update(
            bin_names=list(definition.bin_names),
            health=row.evidence.to_dict() if row.evidence is not None else None,
            health_stale=evidence_is_stale(row.evidence),
            capabilities=row.capabilities,
            adapter=dict(
                npm_pkg=pin.npm_pkg if pin else "",
                pinned=bool(pin and pin.pinned),
                state=row.adapter.state,
                verified=row.adapter.verified,
                detail=row.adapter.detail,
            ),
            lease=row.lease,
        )
        return result

    @staticmethod
    def assemble(definition, probe):
        evidence = probe_runner(definition) if probe else load_evidence(definition.id)
        try:
            adapter = verify_adapter(definition)
        except Exception:
            logger.debug(
                "adapter verification failed for %s", definition.id, exc_info=True
            )
            adapter = AdapterVerification("unverified", "verification errored")
        try:
            from gideon.engine.agents import runner_lifecycle

            lease = runner_lifecycle.lease_for(definition.runtime_id)
        except Exception:
            logger.debug("lease read failed for %s", definition.id, exc_info=True)
            lease = None
        return RunnerRow(
            definition, evidence, load_capabilities(definition.id), adapter, lease
        )


def runner_rows(*, probe: bool = False) -> list[RunnerRow]:
    definitions = sorted(catalog().values(), key=lambda row: row.display_name.lower())
    return [_RunnerPresentation.assemble(row, probe) for row in definitions]
