"""Compile authoring patterns into ordinary workflow nodes before persistence."""

from __future__ import annotations

import copy
from typing import Any

MACRO_KEY = "macro"
MAX_DEPTH = 12


class MacroError(ValueError):
    """An authoring pattern cannot be expanded."""


class _Expansion:
    def node(self, value: Any, level: int) -> dict[str, Any]:
        while True:
            if level > MAX_DEPTH:
                raise MacroError(
                    f"macro expansion exceeded {MAX_DEPTH} levels — a macro probably expands into itself"
                )
            if not isinstance(value, dict):
                raise MacroError(
                    f"a node must be an object, got {type(value).__name__}"
                )
            pattern = value.get(MACRO_KEY)
            if not (isinstance(pattern, str) and pattern):
                break
            factory = _MACROS.get(pattern)
            if factory is None:
                raise MacroError(
                    f"unknown macro {pattern!r} — available: {', '.join(sorted(_MACROS))}"
                )
            value = factory(value)
            level += 1
        for slot in ("children", "body", "cases", "default"):
            branch = value.get(slot)
            if slot == "children" and isinstance(branch, list):
                value[slot] = list(
                    map(lambda child: self.node(child, level + 1), branch)
                )
            elif slot == "cases" and isinstance(branch, dict):
                expanded = {}
                for label, child in branch.items():
                    expanded[label] = self.node(child, level + 1)
                value[slot] = expanded
            elif slot in ("body", "default") and isinstance(branch, dict):
                value[slot] = self.node(branch, level + 1)
        return value


def expand_spec(spec: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(spec)
    if isinstance(result.get("root"), dict):
        result.update(root=_Expansion().node(result["root"], 0))
    return result


def _expand_node(node: dict[str, Any], *, depth: int) -> dict[str, Any]:
    return _Expansion().node(node, depth)


def macro_names() -> list[str]:
    return sorted(_MACROS)


def _author_nodes(value: Any):
    if not isinstance(value, dict):
        return
    yield value
    for child in value.get("children") or []:
        yield from _author_nodes(child)
    yield from _author_nodes(value.get("body"))
    for child in (value.get("cases") or {}).values():
        yield from _author_nodes(child)
    yield from _author_nodes(value.get("default"))


def has_macros(spec: dict[str, Any]) -> bool:
    origin = spec["root"] if "root" in spec else spec
    for node in _author_nodes(origin):
        marker = node.get(MACRO_KEY)
        if isinstance(marker, str) and marker:
            return True
    return False


def _need(node: dict[str, Any], key: str, macro: str) -> Any:
    candidate = (node.get("config") or {}).get(key)
    for absent in (None, "", [], {}):
        if candidate == absent:
            raise MacroError(f"macro {macro!r} needs `config.{key}`")
    return candidate


def _id_of(node: dict[str, Any], fallback: str) -> str:
    raw = node.get("id")
    return str(raw) if isinstance(raw, str) and raw else fallback


def _tier(node: dict[str, Any], default: str) -> str:
    selected = (node.get("config") or {}).get("model_tier")
    for tier in ("reasoning", "standard", "fast"):
        if selected == tier:
            return str(selected)
    return default


class _PatternGraph:
    def __init__(self, invocation: dict[str, Any], name: str):
        self.key = _id_of(invocation, name)
        self.invocation = invocation
        self.name = name

    def required(self, field: str) -> Any:
        return _need(self.invocation, field, self.name)

    def options(self) -> dict[str, Any]:
        return self.invocation.get("config") or {}

    def reference(self, suffix: str, member: str = "") -> str:
        tail = "." + member if member else ""
        return "{{nodes." + self.key + "_" + suffix + ".output" + tail + "}}"

    def unit(self, kind: str, suffix: str, **fields: Any) -> dict[str, Any]:
        key = self.key if not suffix else self.key + "_" + suffix
        return dict(kind=kind, id=key, **fields)

    def sequence(self, *nodes: dict[str, Any]) -> dict[str, Any]:
        return self.unit("sequence", "", children=list(nodes))

    def infer(self, suffix: str, prompt: str, schema: dict[str, str], tier: str):
        return self.unit(
            "infer", suffix, config=dict(model_tier=tier, prompt=prompt, schema=schema)
        )


def _judge_panel(node: dict[str, Any]) -> dict[str, Any]:
    graph = _PatternGraph(node, "judge_panel")
    subject, lenses = graph.required("subject"), graph.required("lenses")
    if not isinstance(lenses, list):
        raise MacroError("macro 'judge_panel' needs `config.lenses` as a list")
    criteria = graph.options().get("criteria") or ""
    tier = _tier(node, "standard")
    judges, scores, by_lens, findings = [], [], {}, []
    for lens in lenses:
        if isinstance(lens, str):
            label, guidance = lens, ""
        else:
            label = str((lens or {}).get("name", ""))
            guidance = str((lens or {}).get("prompt", ""))
        if not label:
            raise MacroError("macro 'judge_panel': every lens needs a name")
        message = [
            f"Judge the following through the {label} lens",
            f". {guidance}" if guidance else ".",
        ]
        if criteria:
            message.append(f"\n\nCriteria: {criteria}")
        message.extend(
            [
                '\n\nReturn JSON: {"score": 0-10, "lens": "',
                label,
                '", "findings": [Finding], "summary": "one line"}.\n\n',
                "{{block:finding-record}}\n\nSubject:\n",
                str(subject),
            ]
        )
        judge = graph.infer(
            _slug(label),
            "".join(message),
            dict(score="number", lens="string", findings="array", summary="string"),
            tier,
        )
        judges.append(judge)
        reference = "{{nodes." + judge["id"] + ".output"
        scores.append(reference + ".score}}")
        by_lens[str(judge["id"])] = reference + "}}"
        findings.append(reference + ".findings}}")
    return graph.sequence(
        graph.unit("parallel", "judges", children=judges),
        graph.unit(
            "transform",
            "synthesis",
            config={"expr": dict(scores=scores, by_lens=by_lens, findings=findings)},
        ),
    )


def _verify_panel(node: dict[str, Any]) -> dict[str, Any]:
    graph = _PatternGraph(node, "verify_panel")
    findings = graph.required("findings")
    tier = _tier(node, "standard")
    guidance = str(graph.options().get("guidance", "") or "")
    prompt = (
        "Try to REFUTE this finding. Look for reasons it is wrong, already "
        "handled, or not reachable in practice. Default to refuted=true "
        "when you are uncertain — a finding that survives a genuine attempt "
        "to disprove it is worth acting on; one that merely sounds "
        "plausible is not."
    )
    if guidance:
        prompt += "\n\n" + guidance
    prompt += (
        '\n\nReturn JSON: {"refuted": bool, "reason": "why", '
        '"severity": "Critical|Major|Minor|Nit"}.\n\nFinding:\n{{item}}'
    )
    verifier = graph.infer(
        "refute",
        prompt,
        dict(refuted="boolean", reason="string", severity="string"),
        tier,
    )
    traversal = graph.unit(
        "foreach",
        "verify",
        config=dict(items=findings, pipeline=True, on_item_error="skip"),
        body=verifier,
    )
    result = graph.unit(
        "transform",
        "confirmed",
        config={
            "expr": dict(verdicts=graph.reference("refute"), source_findings=findings)
        },
    )
    return graph.sequence(traversal, result)


def _route(node: dict[str, Any]) -> dict[str, Any]:
    graph = _PatternGraph(node, "route")
    subject = graph.required("subject")
    branches = node.get("cases")
    if not isinstance(branches, dict) or not branches:
        raise MacroError("macro 'route' needs `cases` mapping each category to a node")
    categories = list(branches)
    criteria = str(graph.options().get("criteria", "") or "")
    tier = _tier(node, "fast")
    prompt = "Classify the following into EXACTLY one category.\n"
    prompt += f"Categories: {', '.join(categories)}\n"
    if criteria:
        prompt += f"How to decide: {criteria}\n"
    prompt += (
        '\nReturn JSON: {"category": "<one of the above>", '
        '"why": "one line"}.\n\nSubject:\n' + str(subject)
    )
    classifier = graph.infer(
        "classify", prompt, dict(category="string", why="string"), tier
    )
    router = graph.unit(
        "branch",
        "dispatch",
        config=dict(on=graph.reference("classify", "category"), enum=categories),
        cases=branches,
    )
    if isinstance(node.get("default"), dict):
        router.update(default=node["default"])
    return graph.sequence(classifier, router)


def _research_sweep(node: dict[str, Any]) -> dict[str, Any]:
    graph = _PatternGraph(node, "research_sweep")
    question, modes = graph.required("question"), graph.required("modes")
    if not isinstance(modes, list):
        raise MacroError("macro 'research_sweep' needs `config.modes` as a list")
    searches, references = [], {}
    for mode in modes:
        label = str(mode)
        search = graph.unit(
            "stage",
            _slug(label),
            config={
                "prompt": (
                    f"Search for material answering the question below, using the {label} "
                    "angle specifically. Return the sources you found with a one-line note "
                    "on what each contains.\n\nQuestion:\n" + str(question)
                )
            },
        )
        searches.append(search)
        references[str(search["id"])] = "{{nodes." + search["id"] + ".output}}"
    extraction = graph.infer(
        "extract",
        (
            "Extract what this source actually says about the question. Quote "
            "the load-bearing lines rather than paraphrasing, and say plainly "
            "when it does NOT address the question — a source that turned out "
            "to be irrelevant is a useful result, not a failure.\n\n"
            'Return JSON: {"relevant": bool, "claims": ["…"], '
            '"quotes": ["…"], "gaps": ["…"]}.\n\n'
            f"Question: {question}\n\nSource:\n{{{{item}}}}"
        ),
        dict(relevant="boolean", claims="array", quotes="array", gaps="array"),
        "standard",
    )
    return graph.sequence(
        graph.unit("parallel", "sweep", children=searches),
        graph.unit("transform", "sources", config={"expr": {"by_mode": references}}),
        graph.unit(
            "foreach",
            "read",
            config=dict(
                items=graph.reference("sources", "by_mode"),
                pipeline=True,
                on_item_error="skip",
            ),
            body=extraction,
        ),
    )


def _slug(text: str) -> str:
    fragments, current = [], []
    for character in text.lower():
        if character.isalnum():
            current.append(character)
        elif current:
            fragments.append("".join(current))
            current.clear()
    if current:
        fragments.append("".join(current))
    return "_".join(fragments) or "lens"


_MACROS: dict[str, Any] = {
    "judge_panel": _judge_panel,
    "verify_panel": _verify_panel,
    "route": _route,
    "research_sweep": _research_sweep,
}
