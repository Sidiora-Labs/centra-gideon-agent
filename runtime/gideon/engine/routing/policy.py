"""User routing controls and stable candidate ordering."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
_POLICY_FILE = "routing_policy.json"
POLICY_VERSION = 1
MODES = ("off", "heuristic", "learned")
MODE_KEY = "routing_mode"
PIN_KEY = "routing_pin"
_MIN_LOCAL_REASONING_B = 7.0
_SIZE_HINT = re.compile("(\\d+(?:\\.\\d+)?)\\s*b(?![a-z0-9])", re.IGNORECASE)
_STRUCTURED_CAP = "structured_output"
_CLASS_STRUCTURED = "extract_structured"
_CLASS_LONG_REASONING = "long_reasoning"


def _policy_path(home: Path) -> Path:
    return Path(home).joinpath(_POLICY_FILE)


def _empty_policy() -> dict[str, Any]:
    return dict(version=POLICY_VERSION, classifier_version=1, use_cases={})


def _default_home() -> Path | None:
    try:
        from gideon.core.config import config_dir

        return Path(config_dir())
    except Exception:
        return None


@dataclass
class PolicyDocument:
    value: dict

    @classmethod
    def read(cls, home):
        resolved = _default_home() if home is None else home
        try:
            data = (
                json.loads(_policy_path(resolved).read_text(encoding="utf-8"))
                if resolved is not None
                else None
            )
        except (OSError, ValueError, TypeError):
            data = None
        if not isinstance(data, dict):
            data = _empty_policy()
        data.setdefault("version", POLICY_VERSION)
        data.setdefault("classifier_version", 1)
        if not isinstance(data.get("use_cases"), dict):
            data["use_cases"] = {}
        return cls(data)

    def cell(self, use_case, query_class):
        entry = _use_case_entry(self.value, use_case)
        classes = entry.get("classes")
        cell = classes.get(query_class) if isinstance(classes, dict) else None
        return cell if isinstance(cell, dict) else {}

    def put(self, use_case, query_class, order, basis):
        cases = self.value.setdefault("use_cases", {})
        if not isinstance(cases.get(use_case), dict):
            cases[use_case] = {}
        entry = cases[use_case]
        if not isinstance(entry.get("classes"), dict):
            entry["classes"] = {}
        entry["classes"][query_class] = {
            "order": list(map(str, order)),
            "basis": dict(basis) if basis else {"source": "user"},
        }


def load_policy(home: Path | None = None) -> dict[str, Any]:
    return PolicyDocument.read(home).value


def save_policy(home: Path, policy: dict[str, Any]) -> None:
    text = json.dumps(policy, indent=2, sort_keys=True)
    atomic_write(_policy_path(home), text + "\n")


def _use_case_entry(policy: dict[str, Any], use_case: str) -> dict[str, Any]:
    value = policy.get("use_cases", {}).get(use_case)
    return value if isinstance(value, dict) else {}


def _settings_for(use_case: str) -> dict[str, Any]:
    try:
        from gideon.extensions.providers.use_cases import load_use_case_settings

        value = load_use_case_settings(use_case)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def mode_for(use_case: str, *, home: Path | None = None) -> str:
    candidates = (_settings_for(use_case).get(MODE_KEY),)
    for value in candidates:
        mode = str(value or "")
        if mode in MODES:
            return mode
    mode = str(_use_case_entry(load_policy(home), use_case).get("mode", "") or "")
    return mode if mode in MODES else "off"


def pin_for(use_case: str, *, home: Path | None = None) -> str:
    pin = str(_settings_for(use_case).get(PIN_KEY, "") or "")
    return pin or str(_use_case_entry(load_policy(home), use_case).get("pin", "") or "")


def _configured_routing():
    from gideon.core.config.loader import AppConfig

    return AppConfig.load().routing


def master_enabled() -> bool:
    try:
        return bool(_configured_routing().enabled)
    except Exception:
        return False


def local_timeout_secs() -> float:
    try:
        return max(0.0, float(_configured_routing().local_timeout_secs))
    except Exception:
        return 20.0


def routing_active(use_case: str, *, home: Path | None = None) -> bool:
    try:
        return bool(master_enabled() and mode_for(use_case, home=home) != "off")
    except Exception:
        return False


def table_order(
    use_case: str, query_class: str, *, home: Path | None = None
) -> list[str]:
    order = PolicyDocument(load_policy(home)).cell(use_case, query_class).get("order")
    return (
        [str(ref) for ref in order if isinstance(ref, str)]
        if isinstance(order, list)
        else []
    )


def order_basis(
    use_case: str, query_class: str, *, home: Path | None = None
) -> dict[str, Any]:
    basis = PolicyDocument(load_policy(home)).cell(use_case, query_class).get("basis")
    return basis if isinstance(basis, dict) else {}


def _norm(name: str) -> str:
    return "".join(
        character
        for character in str(name).lower()
        if character in "abcdefghijklmnopqrstuvwxyz0123456789"
    )


def provider_of(ref: str) -> str:
    return str(ref).partition(":")[0]


def model_of(ref: str) -> str:
    return str(ref).partition(":")[2]


def _local_provider_keys() -> set[str]:
    try:
        from gideon.integrations.local_models.registry import registered

        return set(_norm(key) for key, _ in registered() if key)
    except Exception:
        return set()


def is_local_ref(ref: str, *, local_keys: set[str] | None = None) -> bool:
    provider = _norm(provider_of(ref))
    if not provider:
        return False
    keys = _local_provider_keys() if local_keys is None else local_keys

    def matches(key):
        return (
            key == provider
            or (len(provider) >= 4 and key.startswith(provider))
            or (len(key) >= 4 and provider.startswith(key))
        )

    return any(matches(key) for key in keys if key)


def _structured_providers() -> set[str]:
    try:
        from gideon.integrations.llm.registry import get_default_registry

        return {
            _norm(entry.name)
            for entry in get_default_registry().list_entries()
            if any(
                str(getattr(cap, "value", cap)) == _STRUCTURED_CAP
                for cap in (entry.declared_capabilities or frozenset())
            )
        }
    except Exception:
        return set()


def size_hint_b(ref: str) -> float:
    match = _SIZE_HINT.search(model_of(ref) or str(ref))
    try:
        return float(match[1]) if match else 0.0
    except (ValueError, TypeError):
        return 0.0


def _pin_rank(ref: str, pin: str, *, local_keys: set[str]) -> int:
    if pin in ("local", "cloud"):
        preferred = is_local_ref(ref, local_keys=local_keys) == (pin == "local")
    else:
        preferred = ref == pin
    return int(not preferred)


def _heuristic_rank(
    ref: str, query_class: str, *, local_keys: set[str], structured: set[str]
) -> tuple[int, int]:
    local = is_local_ref(ref, local_keys=local_keys)
    special = False
    if query_class == _CLASS_STRUCTURED:
        special = _norm(provider_of(ref)) not in structured
    elif query_class == _CLASS_LONG_REASONING and local:
        special = 0.0 < size_hint_b(ref) < _MIN_LOCAL_REASONING_B
    return int(special), int(not local)


def _overlay_feedback(
    fold: dict[str, Any], use_case: str, query_class: str, *, home: Path
) -> None:
    try:
        from gideon.engine.routing.feedback import feedback_index

        index = feedback_index(home=home)
    except Exception:
        return
    if index:
        bucket = fold.get("use_cases", {}).get(use_case, {}).get(query_class, {})
        if isinstance(bucket, dict):
            for ref, row in bucket.items():
                value = index.get((use_case, query_class, ref))
                if value and isinstance(row, dict):
                    row.update(zip(("feedback", "feedback_n"), value))


def _routing_knobs() -> dict[str, Any]:
    """Read learned-stage controls, falling back to their declared defaults."""
    try:
        config = _configured_routing()
        return {
            key: cast(getattr(config, key))
            for key, cast in (
                ("hysteresis", float),
                ("cloud_quality_margin", float),
                ("min_samples", int),
            )
        }
    except Exception:
        return {"hysteresis": 0.05, "cloud_quality_margin": 0.10, "min_samples": 5}


def _cost_of(home: Path) -> Callable[[str], float]:
    from gideon.engine.routing.rates import cost_for

    def price(ref):
        provider, _, model = ref.partition(":")
        try:
            value = cost_for(
                provider, model, input_tokens=1000, output_tokens=500, home=home
            )
        except Exception:
            return float("inf")
        return float(value) if value is not None else float("inf")

    return price


def _learned_order(
    ordered: list[str],
    use_case: str,
    query_class: str,
    local_keys: set[str],
    *,
    home: Path | None = None,
) -> list[str]:
    from gideon.engine.routing import learned, stats

    resolved = _default_home() if home is None else home
    if resolved is None:
        return ordered
    try:
        fold = stats.load_stats(resolved)
    except Exception:
        return ordered
    _overlay_feedback(fold, use_case, query_class, home=resolved)
    knobs = _routing_knobs()
    return learned.learned_order(
        ordered,
        use_case=use_case,
        query_class=query_class,
        stats=fold,
        hysteresis=float(knobs["hysteresis"]),
        cloud_quality_margin=float(knobs["cloud_quality_margin"]),
        local_keys=local_keys,
        cost_of=_cost_of(resolved),
        min_samples=int(knobs["min_samples"]),
    )


@dataclass(frozen=True)
class RoutingChoice:
    use_case: str
    query_class: str
    refs: list[str]
    home: Path | None

    def choose(self):
        mode = mode_for(self.use_case, home=self.home)
        if mode == "off":
            return self.refs
        local = _local_provider_keys()
        pin = pin_for(self.use_case, home=self.home)
        if pin:
            return _stable_by(
                self.refs, lambda ref: (_pin_rank(ref, pin, local_keys=local),)
            )
        recorded = table_order(self.use_case, self.query_class, home=self.home)
        if recorded:
            rank = {ref: slot for slot, ref in enumerate(recorded)}
            return _stable_by(self.refs, lambda ref: (rank.get(ref, len(rank)),))
        if mode == "learned":
            ranked = _learned_order(
                self.refs, self.use_case, self.query_class, local, home=self.home
            )
            if ranked != self.refs:
                return ranked
        structured = (
            _structured_providers() if self.query_class == _CLASS_STRUCTURED else set()
        )
        return _stable_by(
            self.refs,
            lambda ref: _heuristic_rank(
                ref, self.query_class, local_keys=local, structured=structured
            ),
        )


def route_refs(
    use_case: str, query_class: str, refs: list[str], *, home: Path | None = None
) -> list[str]:
    original = list(refs)
    if len(original) < 2:
        return original
    try:
        return RoutingChoice(use_case, query_class, original, home).choose()
    except Exception:
        logger.debug(
            "route_refs failed for %s/%s — keeping bound order", use_case, query_class
        )
        return list(refs)


def _stable_by(refs: list[str], key: Any) -> list[str]:
    result = [ref for _, ref in sorted(enumerate(refs), key=lambda pair: key(pair[1]))]
    if len(result) == len(refs) and sorted(result) == sorted(refs):
        return result
    logger.warning("route_refs produced a non-permutation — keeping bound order")
    return list(refs)


def _sel_policy_change(use_case: str, what: str, value: str) -> None:
    try:
        from gideon.security.sel import sel

        fields = dict(
            caller="user",
            operation=f"routing.{what}",
            outcome="success",
            source="routing_policy",
            resources=f"{use_case}:{value}",
        )
        sel().log_api_access(**fields)
    except Exception:
        logger.debug("routing policy SEL record failed", exc_info=True)


def _write_setting(use_case, key, value, *, remove=False):
    from gideon.extensions.providers.use_cases import (
        load_use_case_settings,
        save_use_case_settings,
    )

    document = dict(load_use_case_settings(use_case) or {})
    if remove:
        document.pop(key, None)
    else:
        document[key] = value
    save_use_case_settings(use_case, document)


def set_mode(use_case: str, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown routing mode {mode!r} (expected one of {MODES})")
    _write_setting(use_case, MODE_KEY, mode)
    _sel_policy_change(use_case, "mode", mode)


def set_pin(use_case: str, pin: str) -> None:
    _write_setting(use_case, PIN_KEY, pin, remove=not pin)
    _sel_policy_change(use_case, "pin", pin or "(cleared)")


def set_order(
    use_case: str,
    query_class: str,
    order: list[str],
    *,
    home: Path | None = None,
    basis: dict[str, Any] | None = None,
) -> None:
    resolved = _default_home() if home is None else home
    if resolved is None:
        raise RuntimeError("no Gideon home configured; cannot persist routing policy")
    table = PolicyDocument(load_policy(resolved))
    table.put(use_case, query_class, order, basis)
    save_policy(resolved, table.value)
    _sel_policy_change(use_case, "order", f"{query_class}:{','.join(order)}")


def table_for(use_case: str, *, home: Path | None = None) -> dict[str, Any]:
    classes = _use_case_entry(load_policy(home), use_case).get("classes")
    names = classes if isinstance(classes, dict) else {}
    return dict(
        use_case=use_case,
        mode=mode_for(use_case, home=home),
        pin=pin_for(use_case, home=home),
        classes={
            str(name): {
                "order": table_order(use_case, str(name), home=home),
                "basis": order_basis(use_case, str(name), home=home),
            }
            for name in names
        },
    )
