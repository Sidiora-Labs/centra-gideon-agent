from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.models import Node, NodeKind, walk

logger = logging.getLogger(__name__)
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass
class Finding:
    code: str
    message: str
    remediation: str = ""
    severity: str = SEVERITY_ERROR
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in ("code", "message", "remediation", "severity", "kind")
        }


@dataclass
class PreflightResult:
    findings: list[Finding] = field(default_factory=list)
    checked: dict[str, list[str]] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return list(
            filter(lambda finding: finding.severity == SEVERITY_ERROR, self.findings)
        )

    @property
    def warnings(self) -> list[Finding]:
        return list(
            filter(lambda finding: finding.severity == SEVERITY_WARNING, self.findings)
        )

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "findings": [finding.to_dict() for finding in self.findings],
        }
        payload["checked"] = {
            kind: list(values) for kind, values in self.checked.items()
        }
        return payload


class _RequirementSweep:
    def __init__(self, result: PreflightResult, kind: str, names: list[str]) -> None:
        self.result, self.kind, self.names = result, kind, names
        result.checked[kind] = names

    def scan(
        self,
        lookup: Any,
        *,
        missing: Any,
        finding: Any,
        failure_log: str,
        normalize: Any = None,
        names: list[str] | None = None,
    ) -> None:
        for name in self.names if names is None else names:
            try:
                value = lookup(name)
                if normalize is not None:
                    value = normalize(value)
            except Exception:
                logger.debug(failure_log, name, exc_info=True)
                continue
            if missing(value):
                self.result.findings.append(finding(name))

    def unavailable(self, code: str, message: str, remediation: str) -> None:
        self.result.findings.append(
            Finding(
                code=code,
                message=message,
                remediation=remediation,
                severity=SEVERITY_WARNING,
                kind=self.kind,
            )
        )


class _AdmissionPlan:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.result = PreflightResult()
        metadata = spec.get("metadata") or {}
        requirements = metadata.get("requirements") or {}
        self.requirements = requirements if isinstance(requirements, dict) else {}

    def evaluate(
        self, credentials: Any, binaries: Any, models: Any, providers: Any
    ) -> PreflightResult:
        stages = (
            (
                "_check_credentials",
                (self.spec, self.requirements, self.result, credentials),
            ),
            ("_check_binaries", (self.requirements, self.result, binaries)),
            ("_check_models", (self.spec, self.result, models)),
            ("_check_action_providers", (self.spec, self.result, providers)),
        )
        for check, arguments in stages:
            globals()[check](*arguments)
        self.result.findings.extend(provider_requirement_gap(self.spec))
        return self.result


def preflight(
    spec: dict[str, Any],
    *,
    credential_resolver: Any = None,
    which: Any = None,
    model_probe: Any = None,
    provider_lookup: Any = None,
) -> PreflightResult:
    return _AdmissionPlan(spec).evaluate(
        credential_resolver, which, model_probe, provider_lookup
    )


class _CredentialLookup:
    def __init__(self, store: Any) -> None:
        self.store = store

    def __call__(self, key: str) -> bool:
        try:
            record = self.store.resolve(key)
        except KeyError:
            return False
        return bool(getattr(record, "secret", ""))


def _check_credentials(
    spec: dict[str, Any],
    requirements: dict[str, Any],
    result: PreflightResult,
    resolver: Any,
) -> None:
    from gideon.automation.workflows.secrets import secret_keys_referenced

    declared = list(map(str, requirements.get("credentials") or []))
    names = sorted(set(declared).union(secret_keys_referenced(spec)))
    sweep = _RequirementSweep(result, "credentials", names)
    if not names:
        return
    lookup = resolver
    if lookup is None:
        try:
            from gideon.core.config.loader import config_dir
            from gideon.integrations.llm.credentials import CredentialStore

            lookup = _CredentialLookup(CredentialStore(config_dir()))
        except Exception:
            logger.debug("preflight: credential store unavailable", exc_info=True)
            sweep.unavailable(
                "WF_PRE_CREDENTIALS_UNVERIFIABLE",
                f"could not check {len(names)} credential(s): the store is unavailable",
                "the run may still work; check Settings → Providers if it fails",
            )
            return
    sweep.scan(
        lookup,
        normalize=bool,
        missing=lambda value: not value,
        finding=lambda key: Finding(
            code="WF_PRE_CREDENTIAL_MISSING",
            message=f"credential {key!r} is not set",
            remediation=f"add {key} in Settings → Providers, then start the run again",
            kind="credentials",
        ),
        failure_log="preflight: credential lookup failed for %s",
    )


def _check_binaries(
    requirements: dict[str, Any], result: PreflightResult, which: Any
) -> None:
    names = list(map(str, requirements.get("binaries") or []))
    sweep = _RequirementSweep(result, "binaries", names)
    if names:
        sweep.scan(
            which or shutil.which,
            missing=lambda value: not value,
            finding=lambda name: Finding(
                code="WF_PRE_BINARY_MISSING",
                message=f"required binary {name!r} is not on PATH",
                remediation=f"install {name}, or edit the workflow to not need it",
                kind="binaries",
            ),
            failure_log="preflight: which(%s) failed",
        )


class _ModelRequirements:
    def __init__(self, root: Node, tiers: dict[str, str]) -> None:
        self.root, self.tiers = root, tiers

    def collect(self) -> set[str]:
        needed: set[str] = set()
        for _, node in walk(self.root):
            if node.kind in (NodeKind.STAGE, NodeKind.INFER):
                tier = str(
                    (node.config or {}).get("model_tier", "standard") or "standard"
                )
                needed.add(self.tiers.get(tier, "background"))
        for _, node in walk(self.root):
            if node.kind != NodeKind.GATE:
                continue
            if str((node.config or {}).get("kind", "")) != "judge":
                continue
            declared = (node.config or {}).get("model_tier")
            use_case = (
                self.tiers.get(str(declared), "background") if declared else "reasoning"
            )
            needed.add(use_case)
        return needed


def _check_models(spec: dict[str, Any], result: PreflightResult, probe: Any) -> None:
    from gideon.automation.workflows.engine_support import DEFAULT_MODEL_TIERS

    root = _root_of(spec)
    if root is None:
        return
    tiers = dict(DEFAULT_MODEL_TIERS)
    defaults = spec.get("defaults") or {}
    overrides = {
        str(key): str(value)
        for key, value in (defaults.get("model_tiers") or {}).items()
    }
    tiers.update(overrides)
    use_cases = _ModelRequirements(root, tiers).collect()
    sweep = _RequirementSweep(result, "models", sorted(use_cases))
    if not use_cases:
        return
    check = probe
    if check is None:
        try:
            from gideon.extensions.providers.provider_bridge import can_resolve_use_case

            check = can_resolve_use_case
        except Exception:
            logger.debug("preflight: model probe unavailable", exc_info=True)
            sweep.unavailable(
                "WF_PRE_MODELS_UNVERIFIABLE",
                "could not check model availability",
                "the run may still work; check Settings → Models if it fails",
            )
            return
    sweep.scan(
        check,
        names=sorted(use_cases),
        normalize=bool,
        missing=lambda value: not value,
        finding=lambda name: Finding(
            code="WF_PRE_MODEL_UNRESOLVED",
            message=f"no model resolves for the {name!r} use case",
            remediation=f"select a model for {name} in Settings → Models, or change the node's model_tier",
            kind="models",
        ),
        failure_log="preflight: probe failed for %s",
    )


def _provider_names(root: Node) -> set[str]:
    names = set()
    for _, node in walk(root):
        if node.kind == NodeKind.ACTION:
            provider = (node.config or {}).get("provider")
            if isinstance(provider, str) and provider and "{{" not in provider:
                names.add(provider)
    return names


def provider_requirement_gap(spec: dict[str, Any]) -> list[Finding]:
    """Warn when action providers are absent from the definition's requirements.

    The run-start check still derives providers from the executable tree and refuses unknown
    providers.  This warning gives an author the missing declaration while the plan is still
    being reviewed, without treating incomplete planning metadata as a reason to refuse a run.
    """
    root = _root_of(spec)
    if root is None:
        return []
    metadata = spec.get("metadata") or {}
    requirements = metadata.get("requirements") if isinstance(metadata, dict) else {}
    declared = requirements.get("providers") if isinstance(requirements, dict) else []
    declared = declared if isinstance(declared, list) else []
    known = {str(name) for name in declared if isinstance(name, str) and name}
    missing = sorted(_provider_names(root) - known)
    return [
        Finding(
            code="WF_PRE_PROVIDER_REQUIREMENT_GAP",
            message=(
                f"action provider {name!r} is used by the workflow but not declared in "
                "metadata.requirements.providers"
            ),
            remediation=(
                f"add {name!r} to metadata.requirements.providers so the plan states its "
                "provider dependency"
            ),
            severity=SEVERITY_WARNING,
            kind="action_providers",
        )
        for name in missing
    ]


def _check_action_providers(
    spec: dict[str, Any], result: PreflightResult, lookup: Any
) -> None:
    root = _root_of(spec)
    if root is None:
        return
    names = _provider_names(root)
    sweep = _RequirementSweep(result, "action_providers", sorted(names))
    if not names:
        return
    getter = lookup
    if getter is None:
        try:
            from gideon.integrations.action_providers.registry import (
                _ensure_default_providers_registered,
                get_action_provider,
            )

            _ensure_default_providers_registered()
            getter = get_action_provider
        except Exception:
            logger.debug("preflight: action registry unavailable", exc_info=True)
            sweep.unavailable(
                "WF_PRE_PROVIDERS_UNVERIFIABLE",
                "could not check action providers",
                "the run may still work; check the installed apps if it fails",
            )
            return
    sweep.scan(
        getter,
        names=sorted(names),
        missing=lambda value: value is None,
        finding=lambda name: Finding(
            code="WF_PRE_PROVIDER_UNKNOWN",
            message=f"action provider {name!r} is not registered",
            remediation=f"install the app that provides {name}, or point the node at a registered provider",
            kind="action_providers",
        ),
        failure_log="preflight: provider lookup failed for %s",
    )


def _root_of(spec: dict[str, Any]) -> Node | None:
    source = (spec or {}).get("root")
    if isinstance(source, dict):
        try:
            parsed = Node.from_dict(source)
        except (ValueError, TypeError):
            return None
        return parsed
    return None
