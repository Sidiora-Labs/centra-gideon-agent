"""Generative-UI catalog — the SERVER-SIDE authoring authority (AMBIENT-SURFACES §5).

The FE `web/src/ui/genui/registry.ts` owns RENDERING (the typed registry + the
React components + drop-invalid validation). This module owns PROMPTING: the same
small component vocabulary as a machine-readable catalog, plus ``library_prompt()``
which derives the authoring section mechanically from it. It exists server-side
because the prompts that instruct a model to emit genui — the ``visualize`` primitive
and any workflow node — are built here, not in the browser.

Hand-maintained component docs are banned (they drift): the prompt is DERIVED from
``CORE_COMPONENTS`` below, and ``/api/genui/library`` serves the exact same derived
text to the FE / the visual-output skill so every consumer embeds the CURRENT set.
The FE registry mirrors this vocabulary for rendering; the core set is deliberately
small (every component costs prompt space) so the two stay legibly in step.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GenUiArg:
    """One declared arg of a component. ``key`` order is the positional contract."""

    key: str
    type: str
    required: bool = False
    note: str = ""


@dataclass(frozen=True)
class GenUiComponent:
    """A catalog entry: what a model may emit + how the prompt describes it. The
    RENDER lives in the FE registry under the same name."""

    name: str
    group: str
    description: str
    args: tuple[GenUiArg, ...]


CORE_COMPONENTS: tuple[GenUiComponent, ...] = (
    GenUiComponent(
        "Stack",
        "Layout",
        "Vertical/horizontal stack of children",
        (
            GenUiArg("body", "refs", True, "child line ids, in order"),
            GenUiArg("gap", "string", note="s | m | l"),
            GenUiArg("direction", "string", note="column (default) | row"),
        ),
    ),
    GenUiComponent(
        "Card",
        "Layout",
        "Titled card wrapping children",
        (GenUiArg("body", "refs", True, "child line ids"), GenUiArg("title", "string")),
    ),
    GenUiComponent(
        "StatTile",
        "Data",
        "One metric with optional % delta",
        (
            GenUiArg("label", "string", True),
            GenUiArg("value", "string", True),
            GenUiArg("delta", "number", note="signed percent change"),
        ),
    ),
    GenUiComponent(
        "Table",
        "Data",
        "Header row + body rows",
        (
            GenUiArg("columns", "string[]", True),
            GenUiArg("rows", "rows", True, "array of row arrays"),
        ),
    ),
    GenUiComponent(
        "List",
        "Data",
        "Bulleted list of strings",
        (GenUiArg("items", "string[]", True),),
    ),
    GenUiComponent(
        "Bar",
        "Charts",
        "Bar chart of one numeric series",
        (
            GenUiArg("data", "number[]", True),
            GenUiArg("labels", "string[]", note="per-bar labels"),
        ),
    ),
    GenUiComponent(
        "Callout",
        "Feedback",
        "Tinted note band",
        (
            GenUiArg("text", "string", True),
            GenUiArg("tone", "string", note="info | ok | warn | danger | neutral"),
        ),
    ),
    GenUiComponent(
        "Badge",
        "Feedback",
        "Small status chip",
        (GenUiArg("text", "string", True), GenUiArg("tone", "string")),
    ),
    GenUiComponent(
        "ProgressBar",
        "Feedback",
        "Determinate 0..100 progress bar",
        (GenUiArg("value", "number", True), GenUiArg("label", "string")),
    ),
)

_GROUP_ORDER = ("Layout", "Data", "Charts", "Feedback")


def _signature(comp: GenUiComponent) -> str:
    args = ", ".join(f"{a.key}{'' if a.required else '?'}: {a.type}" for a in comp.args)
    desc = f" — {comp.description}" if comp.description else ""
    return f"  {comp.name}({args}){desc}"


def library_prompt() -> str:
    """The authoring section, derived mechanically from ``CORE_COMPONENTS`` — the
    single string an author/model reads to know what it may emit. Kept in step with
    the FE ``library.prompt()`` so the endpoint and the FE agree."""
    lines = [
        'Generative-UI components you may emit inside a <widget kind="genui"> block.',
        "DSL: one line per component — `id = Component(key: value, …)`. "
        "Forward references are legal.",
        "Compose children with a `refs`/`ref` arg holding other line ids (e.g. children: [a, b]).",
        "",
    ]
    for group in _GROUP_ORDER:
        defs = [c for c in CORE_COMPONENTS if c.group == group]
        if not defs:
            continue
        lines.append(f"{group}:")
        lines.extend(_signature(c) for c in defs)
        lines.append("")
    lines.extend(
        [
            "Example:",
            '  root = Stack(gap: "m", body: [stat, note])',
            '  stat = StatTile(label: "Revenue", value: "$1.2M", delta: 12)',
            '  note = Callout(tone: "info", text: "Up 12% vs last quarter.")',
        ]
    )
    return "\n".join(lines)


def library_manifest() -> dict:
    """The machine-readable catalog (for ``/api/genui/library`` + tests). Generated
    from ``CORE_COMPONENTS``, never hand-written."""
    return {
        "components": [
            {
                "name": c.name,
                "group": c.group,
                "description": c.description,
                "args": [
                    {
                        "key": a.key,
                        "type": a.type,
                        "required": a.required,
                        "note": a.note,
                    }
                    for a in c.args
                ],
            }
            for c in CORE_COMPONENTS
        ],
        "prompt": library_prompt(),
    }


def uispec_catalog() -> tuple[dict[str, Any], ...]:
    """Vetted snapshot derived from the donor trees; the frontend contract test checks drift."""
    path = Path(__file__).with_name("uispec_catalog.json")
    return tuple(json.loads(path.read_text(encoding="utf-8")))


def uispec_library_prompt() -> str:
    """Separate JSON UISpec protocol for records supplied to visualize."""
    lines = [
        'Structured UISpec: emit a <widget kind="uispec"> block only for an explicit',
        'data.generative_ui v1 envelope with a complete real recordId and bindings.',
        'Return exactly the supplied JSON envelope. Do not invent, change, or complete records.',
        'Template names and required binding keys/types follow. Actions without an',
        'authorized product capability render unavailable; do not promise delivery.',
    ]
    for entry in uispec_catalog():
        fields = ", ".join(f"{key}: {kind}" for key, kind in entry["bindings"].items())
        lines.append(f'{entry["template"]} ({entry["title"]}): {fields}')
    return "\n".join(lines)


def validate_uispec_envelope(value: Any) -> dict[str, Any] | None:
    """Bound authoring input; tree construction and action checks remain in the FE."""
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "template", "recordId", "bindings"}:
        return None
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        return None
    entry = next((item for item in uispec_catalog() if item["template"] == value["template"]), None)
    record_id, bindings = value["recordId"], value["bindings"]
    if not entry or not isinstance(record_id, str) or not 0 < len(record_id) <= 128 or not isinstance(bindings, dict):
        return None
    if set(bindings) != set(entry["bindings"]):
        return None

    def bounded(item: Any, depth: int = 0) -> bool:
        if depth > 4:
            return False
        if item is None or isinstance(item, bool):
            return True
        if isinstance(item, str):
            return len(item) <= 4096
        if isinstance(item, (int, float)):
            return abs(item) < float("inf")
        if isinstance(item, list):
            return len(item) <= 128 and all(bounded(part, depth + 1) for part in item)
        if isinstance(item, dict):
            return len(item) <= 32 and all(isinstance(key, str) and key not in {"__proto__", "constructor", "prototype"} and bounded(part, depth + 1) for key, part in item.items())
        return False

    for key, kind in entry["bindings"].items():
        bound = bindings[key]
        actual = "array" if isinstance(bound, list) else "null" if bound is None else "boolean" if isinstance(bound, bool) else "number" if isinstance(bound, (int, float)) else "string" if isinstance(bound, str) else "object" if isinstance(bound, dict) else "invalid"
        if actual != kind or not bounded(bound):
            return None
        if (key.endswith(".src") or key == "src") and (not isinstance(bound, str) or not bound.lower().startswith(("https://", "http://"))):
            return None
    if len(json.dumps(value, ensure_ascii=False)) > 65536:
        return None
    return value
