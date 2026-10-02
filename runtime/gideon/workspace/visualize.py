"""Render grounded native candidates through bounded Jev presentation decisions.

Prepared GenUI uses deterministic assembly and a truthful source-order fallback.
Legacy DSL and explicit UISpec records retain the no-tools reasoning completion
path shared by chat, workflow and tile producers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from typing import Any

from gideon.workspace.genui import library_prompt, uispec_library_prompt, validate_uispec_envelope


@dataclass(frozen=True)
class Visualization:
    """The primitive's result. ``dsl`` is the raw genui DSL body; ``widget`` wraps it
    in the ``<widget kind="genui">`` block a reply/tile embeds directly."""

    dsl: str
    widget: str
    decision_source: str = "legacy"
    decision_reason: str = ""


def _coerce_data(data: Any) -> str:
    """Render the caller's data as compact text for the prompt. A dict/list becomes
    JSON (the model reads it structurally); a string passes through."""
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, default=str)[:8000]
    except (TypeError, ValueError):
        return str(data)[:8000]


def _build_prompt(data: Any, hint: str) -> str:
    """The generation prompt: the CURRENT registry vocabulary + the data + the hint,
    asking for ONLY the DSL. The vocabulary is derived (``library_prompt``) so it is
    never a hand-maintained, drifting copy."""
    ask = (hint or "").strip() or "Present this data clearly."
    if isinstance(data, dict) and "generative_ui" in data:
        envelope = validate_uispec_envelope(data["generative_ui"])
        if envelope is None:
            raise ValueError("Invalid structured UISpec input; provide a complete real record.")
        return (
            f"{uispec_library_prompt()}\n\n"
            "Output ONLY the exact JSON object under DATA.generative_ui. No prose, "
            "code fences, or widget tags. Never synthesize missing values.\n\n"
            f"GOAL: {ask}\n\nDATA:\n{json.dumps(data, ensure_ascii=False)}"
        )
    return (
        f"{library_prompt()}\n\n"
        "Render the DATA below as a compact generative-UI widget using ONLY the "
        "components above. Output ONLY the DSL — one `id = Component(...)` line per "
        "component, no prose, no code fences, no <widget> tags.\n\n"
        f"GOAL: {ask}\n\nDATA:\n{_coerce_data(data)}"
    )


def _clean_dsl(text: str) -> str:
    """Strip anything the model wrapped the DSL in — ``` fences and stray
    ``<widget>`` tags — leaving the bare DSL body the renderer parses."""
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    if body.startswith("<widget"):
        start = body.find(">")
        end = body.rfind("</widget>")
        if start != -1:
            body = body[start + 1 : (end if end != -1 else len(body))].strip()
    return body


def _wrap_widget(dsl: str, title: str, kind: str = "genui") -> str:
    safe_title = escape(title or "Visualization", quote=True)
    return f'<widget kind="{kind}" title="{safe_title}">\n{dsl}\n</widget>'


async def visualize(
    data: Any,
    hint: str = "",
    *,
    title: str = "Visualization",
    completion: Any = None,
) -> Visualization:
    """Render prepared GenUI, legacy DSL data, or an explicit UISpec record.

    ``completion`` remains the legacy reasoning-path dependency. Prepared GenUI
    always uses the bounded decision transport and reports its decision source.
    Invalid grounded input is refused before selection.
    """
    if isinstance(data, dict) and "genui" in data:
        from gideon.workspace.genui_prepare import render_prepared_genui

        if set(data) != {"genui"}:
            raise ValueError("Prepared GenUI must be the sole data envelope.")
        envelope, source, reason = await render_prepared_genui(data["genui"])
        body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        body = body.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return Visualization(dsl=body, widget=_wrap_widget(body, title), decision_source=source, decision_reason=reason)
    prompt = _build_prompt(data, hint)
    if completion is not None:
        text = await completion(prompt, use_case="reasoning")
    else:
        from gideon.integrations.llm_helpers import one_shot_completion

        text = await one_shot_completion(prompt, use_case="reasoning")
    dsl = _clean_dsl(str(text or ""))
    if isinstance(data, dict) and "generative_ui" in data:
        try:
            emitted = json.loads(dsl)
        except (TypeError, ValueError) as error:
            raise ValueError("Model did not return valid UISpec JSON.") from error
        if validate_uispec_envelope(emitted) is None or emitted != data["generative_ui"]:
            raise ValueError("Model changed the supplied UISpec record; nothing was rendered.")
        body = json.dumps(emitted, ensure_ascii=False, separators=(",", ":"))
        body = body.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return Visualization(dsl=body, widget=_wrap_widget(body, title, "uispec"))
    return Visualization(dsl=dsl, widget=_wrap_widget(dsl, title))
