"""Measure assembled prompts, project oversized components, and report room to reply."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import Enum

logger = logging.getLogger(__name__)


class HeadroomState(str, Enum):
    FITS = "fits"
    FITS_AFTER_COMPRESSION = "fits_after_compression"
    CANNOT_FIT = "cannot_fit"


WINDOW_UNKNOWN = "unknown"
PRESSURE_WARN_FRACTION = 0.75
PRESSURE_CRITICAL_FRACTION = 0.9
_CHARS_PER_TOKEN = 4
_COMPRESSION_PASSES = 3
_PASS_TIGHTENING = 0.75
MIN_PROJECTION_CHARS = 400
MAX_NAMED_OVERSIZED = 4
_UNMEASURED_REASON = (
    "The bound model's context window is unmeasured — neither the local-model catalog "
    "nor the model-window table names this model — so assembled size was counted but not "
    "compared against a limit."
)
_UNMEASURED_FIX = (
    "Add the model to the window table (src/gideon/model_tokens.json), or declare "
    "context_tokens on its model card, so this turn's headroom becomes measurable."
)
_UNMEASURED_SEEN: set[str] = set()


def count_tokens(text: str) -> int:
    from gideon.cognition.learning.surfacing import count_tokens as measure

    return measure(text)


def _fields(record, names):
    return {name: getattr(record, name) for name in names}


@dataclass(frozen=True)
class Component:
    name: str
    text: str
    compressible: bool = True
    content_type: str = ""


@dataclass(frozen=True)
class Compressed:
    name: str
    tokens_before: int
    tokens_after: int
    content_type: str

    @property
    def tokens_saved(self) -> int:
        difference = self.tokens_before - self.tokens_after
        return difference if difference > 0 else 0

    def to_dict(self) -> dict[str, object]:
        return _fields(
            self,
            ("name", "tokens_before", "tokens_after", "tokens_saved", "content_type"),
        )


@dataclass(frozen=True)
class Oversized:
    name: str
    tokens: int
    compressible: bool
    compressed: bool

    @property
    def note(self) -> str:
        statuses = (
            (self.compressed, "already compressed as far as it goes"),
            (self.compressible, "could not be compressed further"),
            (True, "not compressible"),
        )
        return next(text for active, text in statuses if active)

    def to_dict(self) -> dict[str, object]:
        return _fields(self, ("name", "tokens", "compressible", "compressed", "note"))


@dataclass(frozen=True)
class Window:
    tokens: int | None
    output_reserve_tokens: int
    input_tokens: int | None
    source: str

    @property
    def measured(self) -> bool:
        return not (self.tokens is None)

    def to_dict(self) -> dict[str, object]:
        return _fields(
            self,
            ("tokens", "output_reserve_tokens", "input_tokens", "source", "measured"),
        )


@dataclass(frozen=True)
class Headroom:
    state: HeadroomState
    window: Window
    assembled_tokens: int
    raw_tokens: int
    text: str
    compressed: tuple[Compressed, ...] = ()
    oversized: tuple[Oversized, ...] = ()
    reason: str = ""
    fix: str = ""

    @property
    def headroom_tokens(self) -> int | None:
        available = self.window.input_tokens
        return None if available is None else available - self.assembled_tokens

    @property
    def pressure(self) -> float | None:
        available = self.window.input_tokens
        return self.assembled_tokens / available if available else None

    @property
    def level(self) -> str:
        pressure = self.pressure
        if pressure is None:
            return "unmeasured"
        for threshold, label in (
            (PRESSURE_CRITICAL_FRACTION, "critical"),
            (PRESSURE_WARN_FRACTION, "warn"),
        ):
            if pressure >= threshold:
                return label
        return "ok"

    def notice(self) -> str:
        if self.state is HeadroomState.CANNOT_FIT:
            return " ".join((self.reason, self.fix)).strip()
        if self.state is HeadroomState.FITS_AFTER_COMPRESSION:
            count = len(self.compressed)
            details = [
                f"{item.name} ({item.tokens_before:,} → {item.tokens_after:,} tokens)"
                for item in self.compressed
            ]
            recovered = sum(item.tokens_saved for item in self.compressed)
            return (
                "Context was over this model's room to reply, so "
                f"{count} component{'s' if count != 1 else ''} "
                f"{'were' if count != 1 else 'was'} compressed to fit: "
                + ", ".join(details)
                + f". {recovered:,} tokens recovered."
            )
        level = self.level
        if level not in ("warn", "critical"):
            return ""
        percentage = int(round((self.pressure or 0.0) * 100))
        remaining = self.headroom_tokens or 0
        return (
            f"Context headroom {'critical' if level == 'critical' else 'low'}: "
            f"{percentage}% of this model's input room used "
            f"({remaining:,} of {self.window.input_tokens:,} tokens left, after reserving "
            f"{self.window.output_reserve_tokens:,} for the reply). "
            "Run /compact or start a new chat to free room."
        )

    def to_dict(self) -> dict[str, object]:
        result = {"state": self.state.value, "window": self.window.to_dict()}
        result.update(
            _fields(
                self,
                (
                    "assembled_tokens",
                    "raw_tokens",
                    "headroom_tokens",
                    "pressure",
                    "level",
                ),
            )
        )
        for name in ("compressed", "oversized"):
            result[name] = [record.to_dict() for record in getattr(self, name)]
        result.update(_fields(self, ("reason", "fix")))
        return result


async def resolve_window(model_ref: str) -> Window:
    from gideon.integrations.local_models.budgets import DEFAULT_OUTPUT_TOKENS

    ref = (model_ref or "").strip()
    try:
        from gideon.integrations.local_models.budgets import model_budget
        from gideon.integrations.model_windows import model_context_window

        budget = await model_budget(ref)
        authority = None
        if budget.source == "catalog":
            authority = "catalog"
        elif ref and model_context_window(ref, default=0) > 0:
            authority = "window-table"
        if authority is not None:
            return Window(
                budget.context_tokens,
                budget.output_tokens,
                budget.input_tokens,
                authority,
            )
        if ref not in _UNMEASURED_SEEN:
            _UNMEASURED_SEEN.add(ref)
            logger.info(
                "context headroom: window for %r is UNMEASURED (no catalog card, no "
                "window-table entry) — assembled size will be counted but not bounded",
                ref or "<unbound>",
            )
        reserve = budget.output_tokens
    except Exception:
        logger.debug(
            "context headroom: window resolution failed for %r", ref, exc_info=True
        )
        reserve = DEFAULT_OUTPUT_TOKENS
    return Window(None, reserve, None, WINDOW_UNKNOWN)


def bound_model_ref(explicit: str = "") -> str:
    selection = (explicit or "").strip()
    if selection and selection.lower() != "auto":
        return selection
    try:
        from gideon.extensions.providers.use_cases import active_model_refs

        bindings = active_model_refs("chat")
        if bindings:
            return str(bindings[0])
    except Exception:
        logger.debug("context headroom: chat model binding unresolvable", exc_info=True)
    return ""


class _ProjectionBudget:
    def __init__(self, working, limit, total):
        from gideon.integrations.tool_providers.projection import project_output

        self.working, self.limit, self.total = working, limit, total
        self.projector = project_output
        self.changes = {}

    def project(self, index, tightening):
        component, previous = self.working[index]
        desired = max(1, previous - (self.total - self.limit))
        cap = max(MIN_PROJECTION_CHARS, int(desired * _CHARS_PER_TOKEN * tightening))
        if cap >= len(component.text):
            return False
        try:
            projected = self.projector(
                component.text, cap=cap, content_type=component.content_type or None
            )
        except Exception:
            logger.debug(
                "context headroom: projection failed for %r",
                component.name,
                exc_info=True,
            )
            return False
        measured = count_tokens(projected.text)
        if not projected.truncated or measured >= previous:
            return False
        self.working[index] = (replace(component, text=projected.text), measured)
        self.total -= previous - measured
        existing = self.changes.get(index)
        self.changes[index] = Compressed(
            component.name,
            existing.tokens_before if existing else previous,
            measured,
            projected.content_type,
        )
        return True

    def run(self):
        factor = 1.0
        for _ in range(_COMPRESSION_PASSES):
            if self.total <= self.limit:
                break
            candidates = [
                index
                for index, (component, _) in enumerate(self.working)
                if component.compressible
            ]
            candidates.sort(key=lambda index: self.working[index][1], reverse=True)
            progress = False
            for index in candidates:
                if self.total <= self.limit:
                    break
                changed = self.project(index, factor)
                progress = progress or changed
            if not progress:
                break
            factor *= _PASS_TIGHTENING
        return self.total, list(self.changes.values())


def _compress(
    working: list[tuple[Component, int]], *, limit: int, total: int
) -> tuple[int, list[Compressed]]:
    return _ProjectionBudget(working, limit, total).run()


def _name_culprits(
    working: list[tuple[Component, int]], *, over: int, compressed_names: set[str]
) -> tuple[Oversized, ...]:
    remaining = over
    findings = []
    for component, tokens in sorted(working, key=lambda item: item[1], reverse=True):
        if findings and remaining <= 0:
            break
        findings.append(
            Oversized(
                component.name,
                tokens,
                component.compressible,
                component.name in compressed_names,
            )
        )
        remaining -= tokens
    return tuple(findings[:MAX_NAMED_OVERSIZED])


class _AssemblyBudget:
    def __init__(self, components, window):
        self.window = window
        self.working = [
            (component, count_tokens(component.text))
            for component in components
            if component.text
        ]
        self.raw = sum(size for _, size in self.working)

    def joined(self):
        return "".join(component.text for component, _ in self.working)

    def decide(self):
        window, limit = self.window, self.window.input_tokens
        original = self.joined()
        if limit is None:
            return Headroom(
                HeadroomState.FITS,
                window,
                self.raw,
                self.raw,
                original,
                reason=_UNMEASURED_REASON,
                fix=_UNMEASURED_FIX,
            )
        if self.raw <= limit:
            return Headroom(HeadroomState.FITS, window, self.raw, self.raw, original)
        total, compressed = _compress(self.working, limit=limit, total=self.raw)
        rendered = self.joined()
        if total > limit:
            return self.refuse(total, compressed)
        outcome = (
            HeadroomState.FITS_AFTER_COMPRESSION if compressed else HeadroomState.FITS
        )
        return Headroom(outcome, window, total, self.raw, rendered, tuple(compressed))

    def refuse(self, total, compressed):
        window = self.window
        limit = window.input_tokens
        over = total - limit
        oversized = _name_culprits(
            self.working,
            over=over,
            compressed_names={record.name for record in compressed},
        )
        descriptions = [
            f"{item.name} ({item.tokens:,} tokens, {item.note})" for item in oversized
        ]
        reason = (
            f"This turn's context does not fit. Assembled {total:,} tokens, but only "
            f"{limit:,} fit: the model's window is {window.tokens:,} tokens and "
            f"{window.output_reserve_tokens:,} of it is reserved so there is room to reply. "
            f"Over by {over:,} tokens. Largest components: {'; '.join(descriptions)}."
        )
        fix = (
            f"Shorten or remove {oversized[0].name} for this turn, run /compact to summarize "
            "the history, or switch to a model with a window larger than "
            f"{window.tokens:,} tokens."
        )
        return Headroom(
            HeadroomState.CANNOT_FIT,
            window,
            total,
            self.raw,
            "",
            tuple(compressed),
            oversized,
            reason,
            fix,
        )


def check(
    components: "list[Component] | tuple[Component, ...]", *, window: Window
) -> Headroom:
    return _AssemblyBudget(components, window).decide()


async def check_for_model(
    components: "list[Component] | tuple[Component, ...]", *, model_ref: str
) -> Headroom:
    binding = bound_model_ref(model_ref)
    window = await resolve_window(binding)
    return check(components, window=window)
