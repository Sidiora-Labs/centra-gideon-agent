"""Shared helpers for model-backed pipeline nodes (#47).

A node resolves its model through a Settings>Models **use-case** (via
``resolve_provider_for_use_case``) and runs a one-shot completion. The executor has
already verified the use-case is resolvable (``can_resolve_use_case``) before a
model-backed node runs, so these helpers assume a model exists — but still degrade to
``""`` on any provider error rather than raising (the node then reports failure and
the item goes partial).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def complete_text(
    use_case: str, prompt: str, *, images: list[str] | None = None
) -> str:
    """Resolve *use_case* → provider → one-shot completion; return collected text.

    *images* (paths) are attached for vision use-cases when the provider supports
    multimodal content blocks. Returns ``""`` on any failure.
    """
    try:
        from gideon.integrations.llm.base import EVENT_TEXT_CHUNK
        from gideon.integrations.llm_helpers import execute_with_fallback_chain
    except Exception:
        logger.warning(
            "knowledge node: could not resolve use-case %s", use_case, exc_info=True
        )
        return ""

    messages = _build_messages(prompt, images)
    partial = ""

    async def _complete(provider) -> str:
        nonlocal partial
        parts: list[str] = []
        try:
            async for ev in provider.complete(messages):
                if ev.kind == EVENT_TEXT_CHUNK:
                    parts.append(getattr(ev, "text", "") or "")
        except Exception:
            partial = "".join(parts)
            raise
        return "".join(parts).strip()

    try:
        result = await execute_with_fallback_chain(use_case, _complete)
    except Exception:
        logger.warning(
            "knowledge node completion failed (use-case %s)", use_case, exc_info=True
        )
        return partial
    if not result:
        logger.warning(
            "knowledge node: use-case %s returned EMPTY text (images=%d) — "
            "provider produced no content",
            use_case,
            len(images or []),
        )
    return result


def _build_messages(prompt: str, images: list[str] | None) -> list[dict]:
    """Build the messages list. With images, use a multimodal content-block shape
    (base64 data URLs); providers that ignore blocks still see the text prompt."""
    if not images:
        return [{"role": "user", "content": prompt}]
    blocks: list[dict] = [{"type": "text", "text": prompt}]
    for path in images:
        data_url = _image_data_url(path)
        if data_url:
            blocks.append({"type": "image_url", "image_url": {"url": data_url}})
    return [{"role": "user", "content": blocks}]


def _image_data_url(path: str) -> str:
    import base64
    import mimetypes

    try:
        with open(path, "rb") as f:
            raw = f.read()
        mime = mimetypes.guess_type(path)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except OSError:
        return ""
