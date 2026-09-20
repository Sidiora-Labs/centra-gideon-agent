"""Workflow prompt segmentation and bounded proactive/overflow compaction."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from gideon.cognition.context_compaction import compact, should_compact, total_chars
from gideon.integrations.model_windows import model_context_window

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4

COMPACT_AT_FRACTION = 0.80

_PROACTIVE_HEAD = 2
_PROACTIVE_TAIL = 4
_AGGRESSIVE_HEAD = 1
_AGGRESSIVE_TAIL = 1

_PARAGRAPH_BOUNDARY = re.compile(r"\n\n+")


def prompt_char_budget(
    model_ref: str,
    *,
    fraction: float = COMPACT_AT_FRACTION,
    chars_per_token: int = CHARS_PER_TOKEN,
) -> int:
    """The prompt size, IN CHARS, at which the proactive layer should compact.

    `model_context_window` is the one shared reader for the window table (the provider
    adapters use it too), so this never hand-rolls a window per model. An unresolvable
    ref falls back to that module's conservative default rather than to "unbounded" — a
    missing entry must not disable the ladder.
    """
    window_tokens = model_context_window(model_ref)
    return int(window_tokens * chars_per_token * fraction)


def segment_prompt(prompt: str) -> list[dict]:
    """A prompt string → the pseudo-message list `compact()` types on.

    Split on blank lines: paragraph blocks are the boundary a concatenated prompt
    actually has, and they are separator-agnostic (they subsume the ``---`` fence
    `_with_carried_context` joins on without depending on it). Empty blocks are dropped
    so a run of blank lines cannot pad the head/tail protection with nothing.
    """
    if not prompt:
        return []
    segments = []
    for chunk in _PARAGRAPH_BOUNDARY.split(prompt):
        text = chunk.strip()
        if text:
            segments.append({"role": "user", "content": text})
    return segments


def join_segments(messages: list[dict]) -> str:
    """The inverse of `segment_prompt`: pseudo-messages → one prompt string."""
    collected = []
    for m in messages:
        body = str(m.get("content", ""))
        if body:
            collected.append(body)
    return "\n\n".join(collected)


def _placeholder_body(middle: list[dict]) -> str:
    """The drop-with-placeholder body: what was removed, stated plainly.

    Reached only when a supplied summarizer RAISED. Deliberately a placeholder and not a
    silent fallback to `compact()`'s deterministic digest: the summarizer failing means
    something is wrong, and a loud marker naming the loss is honest where quietly
    substituting a weaker artifact would read, downstream, as a complete summary. The
    node survives either way — which is the whole point of this rung.
    """
    dropped_count = len(middle)
    dropped_chars = total_chars(middle)
    return "\n".join(
        [
            "## Earlier context DROPPED (not summarized)",
            "",
            f"[{dropped_count} section(s), {dropped_chars} chars were REMOVED to fit the "
            "model's context window. The summarizer failed, so this content was dropped rather "
            "than summarized: it is NOT recoverable from this prompt. Treat the earlier "
            "conversation as unavailable, and do not infer that it was empty.]",
        ]
    )


class _SummarizerGuard:
    """Wrap a callable summarizer so a raise degrades to the placeholder.

    A summarizer that RAISED is a real failure — this catches it and the placeholder
    names the loss honestly. `compact()` still owns slicing, fencing and tool-pair
    handling; only the body changes.
    """

    __slots__ = ("_delegate",)

    def __init__(self, delegate: Callable[[list[dict]], str]):
        self._delegate = delegate

    def __call__(self, middle: list[dict]) -> str:
        try:
            return self._delegate(middle)
        except (
            Exception
        ):  # noqa: BLE001 — any summarizer failure degrades, never propagates
            logger.warning(
                "workflow prompt summarizer failed; dropping middle", exc_info=True
            )
            return _placeholder_body(middle)


def _guarded_summarizer(
    summarize_fn: Callable[[list[dict]], str] | None,
) -> Callable[[list[dict]], str] | None:
    """Wrap a supplied summarizer so a raise degrades to the placeholder.

    `None` is passed through untouched: `compact(summarize_fn=None)` already produces its
    structured deterministic digest, so "no summarizer" is a working configuration and not
    a failure. "The summarizer failed" means specifically that a summarizer WAS supplied
    and raised — that is the case this catches, and wrapping it here means `compact()`
    still owns the slicing, the fencing and the tool-pair handling while only the BODY
    changes.
    """
    if summarize_fn is None:
        return None
    return _SummarizerGuard(summarize_fn)


def _resolve_protection(aggressive: bool) -> tuple[int, int]:
    """Select head/tail segment counts by compaction mode."""
    if aggressive:
        return _AGGRESSIVE_HEAD, _AGGRESSIVE_TAIL
    return _PROACTIVE_HEAD, _PROACTIVE_TAIL


def compact_prompt(
    prompt: str,
    *,
    summarize_fn: Callable[[list[dict]], str] | None = None,
    aggressive: bool = False,
) -> tuple[str, float]:
    """Compact one prompt. Returns `(prompt, saved_fraction)`.

    `saved_fraction` is 0.0 when nothing changed — a prompt with too few paragraph blocks
    to have a middle is returned verbatim, because there is nothing droppable in it.
    """
    segments = segment_prompt(prompt)
    if not segments:
        return prompt, 0.0
    before = total_chars(segments)
    head, tail = _resolve_protection(aggressive)
    compacted = compact(
        segments,
        summarize_fn=_guarded_summarizer(summarize_fn),
        protect_head=head,
        protect_tail=tail,
    )
    after = total_chars(compacted)
    if before <= 0 or after >= before:
        return prompt, 0.0
    return join_segments(compacted), (before - after) / before


def is_context_overflow(exc: BaseException) -> bool:
    """Did the provider reject this call for LENGTH?

    Delegates to `loop_middleware.classify_failure`, which already owns the pattern set
    (`context_length_exceeded`, `prompt is too long`, `maximum context`, …). Its
    `FailureClass` is a DIFFERENT enum from `models.FailureClass` and the two are never
    mixed here — only the boolean crosses the boundary. Feeding one vocabulary's values to
    the other's consumers is a known defect shape in this engine, so the vocabularies stay
    apart and only the ANSWER is shared.
    """
    from gideon.automation.workflows.loop_middleware import (
        FailureClass as MiddlewareFailureClass,
    )
    from gideon.automation.workflows.loop_middleware import classify_failure

    return classify_failure(str(exc)) is MiddlewareFailureClass.CONTEXT_OVERFLOW


def _resolve_model(
    model: str,
    use_case: str,
    resolver: Callable[[str], str] | None,
) -> str:
    if resolver is None:
        from gideon.automation.workflows.engine import resolve_axis_model

        resolver = resolve_axis_model
    return model or resolver(use_case) or ""


def _apply_proactive_compaction(
    prompt: str,
    summarize_fn: Callable[[list[dict]], str] | None,
    saves: list[float] | None,
    budget: int,
    bound: str,
) -> str:
    """Layer 1: proactive compaction at ~80% of the bound window.

    Returns the prompt — compacted if over budget and not thrashing, unchanged otherwise.
    """
    segments = segment_prompt(prompt)
    if total_chars(segments) <= budget:
        return prompt
    history = saves if saves is not None else []
    if not should_compact(history):
        logger.info(
            "workflow prompt over budget but compaction is thrashing (last saves %r); "
            "sending as-is and letting the error-triggered layer handle a rejection",
            (saves or [])[-2:],
        )
        return prompt
    compacted, saved = compact_prompt(prompt, summarize_fn=summarize_fn)
    if saves is not None:
        saves.append(saved)
    logger.info(
        "workflow prompt compacted proactively: freed %.1f%% (budget %d chars, "
        "model %r)",
        saved * 100,
        budget,
        bound or "<unresolved>",
    )
    return compacted


async def complete_with_compaction(
    fn: Any,
    prompt: str,
    *,
    use_case: str,
    output_type: type | None = None,
    model: str = "",
    summarize_fn: Callable[[list[dict]], str] | None = None,
    saves: list[float] | None = None,
    model_resolver: Callable[[str], str] | None = None,
    on_prompt: Callable[[str], None] | None = None,
) -> str:
    bound = _resolve_model(model, use_case, model_resolver)
    budget = prompt_char_budget(bound)
    current = _apply_proactive_compaction(prompt, summarize_fn, saves, budget, bound)
    arguments = {"use_case": use_case, "output_type": output_type}
    if model:
        arguments["model"] = model
    for attempt in range(2):
        try:
            if on_prompt is not None:
                on_prompt(current)
            return await fn(current, **arguments)
        except Exception as error:
            if attempt or not is_context_overflow(error):
                raise
            current, saved = compact_prompt(
                current, summarize_fn=summarize_fn, aggressive=True
            )
            if saves is not None:
                saves.append(saved)
            if saved <= 0.0:
                raise
            logger.warning(
                "model rejected a workflow prompt for length; re-compacted aggressively "
                "(freed %.1f%%) and retrying once",
                saved * 100,
            )
    raise AssertionError("workflow completion exhausted without a result")
