"""Typed workflow bindings, closed pipes and contextual reference views."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_REF_RE = re.compile(r"\{\{(.+?)\}\}")

_WHOLE_RE = re.compile(r"^\s*\{\{(.+?)\}\}\s*$")

_PIPE_RE = re.compile(r"^([a-z_]+)\s*(?:\((.*)\))?$")


class BindingError(Exception):
    """A reference that cannot be resolved, or a pipe misuse.

    Carries the expression so the journal entry names what broke rather than just
    where. The engine turns this into a typed node failure — never an empty string.
    """

    def __init__(self, message: str, expr: str = "") -> None:
        self.expr = expr
        super().__init__(f"{message} (in {{{{{expr}}}}})" if expr else message)


@dataclass
class BindingContext:
    """Everything a binding may read. Anything absent is a resolution failure, not a
    default — the caller decides what is optional by pre-populating it."""

    inputs: dict[str, Any] | None = None
    node_outputs: dict[str, Any] | None = None
    node_artifacts: dict[str, str] | None = None
    item: Any = None
    has_item: bool = False
    iter_index: int | None = None
    last_output: Any = None
    has_last: bool = False
    sibling_outputs: dict[str, list[Any]] | None = None
    previous_output: Any = None
    has_previous: bool = False
    seen_filter: Any = None
    brief: Any = None
    secret_resolver: Any = None
    self_output: Any = None
    has_self_output: bool = False

    def as_root(self) -> dict[str, Any]:
        return BindingProjection(self).document()


def _pipe_filter(value: Any, key: str = "", expected: Any = None) -> Any:
    if value is None:
        return []
    _require_type(value, list, "filter expects a list")
    accepts = (
        (lambda entry: isinstance(entry, dict) and entry.get(key) == expected)
        if key
        else bool
    )
    return list(filter(accepts, value))


def _pipe_map(value: Any, key: str = "") -> Any:
    if value is None:
        return []
    _require_type(value, list, "map expects a list")
    if not key:
        raise BindingError("map requires a key: map('field')")
    return list(
        map(lambda entry: entry.get(key) if isinstance(entry, dict) else None, value)
    )


def _pipe_flatten(value: Any) -> Any:
    if value is None:
        return []
    _require_type(value, list, "flatten expects a list")
    return [
        entry
        for group in value
        for entry in (group if isinstance(group, list) else [group])
    ]


def _pipe_slice(value: Any, start: Any = 0, stop: Any = None) -> Any:
    if value is None:
        return []
    _require_type(value, (list, str), "slice expects a list or string")
    bounds = [
        _integer(start, "slice bounds must be integers"),
        None if stop is None else _integer(stop, "slice bounds must be integers"),
    ]
    return value[slice(*bounds)]


def _pipe_count(value: Any) -> int:
    if value is None:
        return 0
    _require_type(value, (list, str, dict), "count expects a list, string, or object")
    return len(value)


def _pipe_default(value: Any, fallback: Any = "") -> Any:
    """Substitutes for a null/empty value. This is the ONLY sanctioned way a binding
    yields a fallback — an unresolvable *reference* still raises."""
    return fallback if value in (None, "", [], {}) else value


def _pipe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _pipe_xml_escape(value: Any) -> str:
    result = value if isinstance(value, str) else _pipe_json(value)
    for character, escaped in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
        ("'", "&apos;"),
    ):
        result = result.replace(character, escaped)
    return result


def _pipe_truncate(value: Any, limit: Any = 1000) -> str:
    text = value if isinstance(value, str) else _pipe_json(value)
    maximum = _integer(limit, "truncate limit must be an integer")
    if maximum <= 0:
        raise BindingError("truncate limit must be positive")
    return text[:maximum] + "…" if len(text) > maximum else text


def _pipe_slugify(value: Any) -> str:
    s = value if isinstance(value, str) else str(value)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _pipe_window(value: Any, size: Any = None) -> Any:
    from gideon.automation.workflows import longrun

    if value is None:
        return []
    _require_type(value, list, "window expects a list")
    maximum = (
        longrun.DEFAULT_SYNTHESIS_WINDOW
        if size is None
        else _integer(size, "window size must be an integer")
    )
    if maximum <= 0:
        raise BindingError("window size must be positive")
    return value if len(value) <= maximum else value[-maximum:]


def _pipe_unseen(value: Any, *, _seen: Any = None) -> Any:
    if value is None:
        return []
    _require_type(value, list, "unseen expects a list")
    if _seen is None:
        raise BindingError(
            "unseen needs an engine seen-set (only valid inside a loop body)"
        )
    filtered = _seen(value)
    return filtered if isinstance(filtered, list) else []


def _pipe_significant(value: Any, threshold: Any = None) -> Any:
    from gideon.automation.workflows import longrun

    if value is None:
        return []
    _require_type(value, list, "significant expects a list")
    try:
        cutoff = (
            longrun.DEFAULT_SIGNIFICANCE_THRESHOLD
            if threshold is None
            else float(threshold)
        )
    except (TypeError, ValueError) as exc:
        raise BindingError("significance threshold must be a number") from exc
    return list(filter(lambda entry: longrun.significance_of(entry) >= cutoff, value))


def _pipe_full(value: Any) -> Any:
    """`full` — the explicit opt-out from the default sibling view.

    A no-op ON the value: what it really does is suppress the default filter/window, which
    resolution detects by seeing this pipe in the chain. It exists as a named pipe because
    "I know this is unbounded and I want it" should be visible in the template, not implied
    by the absence of something.
    """
    return value


def _pipe_hygiene(value: Any) -> Any:
    from gideon.automation.workflows import longrun

    if value is None:
        return []
    _require_type(value, list, "hygiene expects a list")
    return longrun.web_hygiene(value)


def _pipe_fenced_sources(value: Any) -> str:
    import gideon.cognition.knowledge.citations as kcit
    from gideon.security.security import fence_untrusted

    entries = _source_items(value)
    if not entries:
        return "No stored knowledge matched. Answer from first principles and say so."
    rendered = [
        fence_untrusted(
            SourcePresentation(index, item, kcit).text(), source="knowledge"
        )
        for index, item in enumerate(entries, 1)
    ]
    instruction = "Numbered sources follow. Cite them as [n] when you use them, and if they do not answer the question, say so rather than filling the gap."
    return "\n".join([instruction, *rendered])


def _pipe_source_refs(value: Any) -> list[dict[str, Any]]:
    import gideon.cognition.knowledge.citations as kcit

    fields = (("marker", int), ("item_id", str), ("chunk_index", int), ("excerpt", str))
    return [
        {name: convert(getattr(reference, name)) for name, convert in fields}
        for reference in kcit.register_sources(_source_items(value))
    ]


def _pipe_clamp(value: Any, low: Any = 0, high: Any = 1) -> Any:
    try:
        bounds = tuple(map(float, (low, high)))
    except (TypeError, ValueError) as exc:
        raise BindingError("clamp bounds must be numbers") from exc
    if bounds[0] > bounds[1]:
        raise BindingError("clamp lower bound exceeds upper bound")
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    try:
        number = float(value) if numeric else float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise BindingError("clamp expects a number") from exc
    bounded = max(bounds[0], min(bounds[1], number))
    return int(bounded) if float(bounded).is_integer() else bounded


PIPES: dict[str, Any] = {
    "filter": _pipe_filter,
    "map": _pipe_map,
    "flatten": _pipe_flatten,
    "slice": _pipe_slice,
    "count": _pipe_count,
    "default": _pipe_default,
    "json": _pipe_json,
    "tojson": _pipe_json,
    "xml_escape": _pipe_xml_escape,
    "truncate": _pipe_truncate,
    "slugify": _pipe_slugify,
    "window": _pipe_window,
    "unseen": _pipe_unseen,
    "significant": _pipe_significant,
    "full": _pipe_full,
    "hygiene": _pipe_hygiene,
    "clamp": _pipe_clamp,
    "fenced_sources": _pipe_fenced_sources,
    "source_refs": _pipe_source_refs,
}

_EXPLICIT_VIEW_PIPES = frozenset(
    {"full", "window", "significant", "unseen", "fenced_sources", "source_refs"}
)


def _parse_pipe_args(raw: str) -> list[Any]:
    return PipeArguments(raw).values()


def _split_args(raw: str) -> list[str]:
    return PipeArguments(raw).tokens()


_MISSING = object()


def _walk_path(root: Any, path: str, expr: str) -> Any:
    return BindingPath(path, expr).read(root)


def resolve_expr(expr: str, ctx: BindingContext) -> Any:
    return BindingPlan(expr, ctx).resolve()


def _run_pipes(value: Any, raw_pipes: list[str], expr: str, ctx: BindingContext) -> Any:
    return BindingPlan(expr, ctx).apply(value, raw_pipes)


def _is_sibling_ref(head: str) -> bool:
    return _root_name(head) == "siblings"


def _is_previous_ref(head: str) -> bool:
    return _root_name(head) == "previous"


def _flatten_sibling(value: Any) -> Any:
    from gideon.automation.workflows import longrun

    return longrun._flatten_outputs(value) if isinstance(value, list) else value


def _default_sibling_view(value: Any) -> Any:
    from gideon.automation.workflows import longrun

    return longrun.sibling_view(value) if isinstance(value, list) else value


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ("false", "true")[value]
    if isinstance(value, (str, int, float)):
        return str(value)
    return _pipe_json(value)


def resolve(template: Any, ctx: BindingContext) -> Any:
    return BindingTemplate(ctx).render(template)


def resolve_prompt(template: Any, ctx: BindingContext) -> Any:
    return BindingTemplate(ctx, fence_untrusted_spans=True).render(template)


def refs_in(template: Any) -> list[str]:
    pending: list[Any] = [template]
    expressions: list[str] = []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(reversed(list(value.values())))
        elif isinstance(value, list):
            pending.extend(reversed(value))
        elif isinstance(value, str):
            expressions.extend(
                match.group(1).strip() for match in _REF_RE.finditer(value)
            )
    return expressions


def node_deps(template: Any) -> set[str]:
    paths = (
        [part for part in expression.split("|")[0].strip().split(".") if part]
        for expression in refs_in(template)
    )
    return {path[1] for path in paths if len(path) >= 2 and path[0] == "nodes"}


def _require_type(value, accepted, message: str) -> None:
    if not isinstance(value, accepted):
        raise BindingError(message)


def _integer(value, message: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BindingError(message) from exc


def _source_items(value):
    return value if isinstance(value, list) else [] if value is None else [value]


def _root_name(path: str) -> str:
    return next(filter(None, path.split(".")), "")


class BindingProjection:
    def __init__(self, context: BindingContext):
        self.context = context

    def document(self) -> dict[str, Any]:
        context = self.context
        root: dict[str, Any] = {"inputs": dict(context.inputs or {}), "nodes": {}}
        nodes = root["nodes"]
        for field, records in (
            ("output", context.node_outputs),
            ("artifact", context.node_artifacts),
        ):
            for name, value in (records or {}).items():
                nodes.setdefault(name, {})[field] = value
        if context.has_item:
            root["item"] = context.item
        if context.iter_index is not None:
            root["iter"] = context.iter_index
        if context.has_last:
            root["last"] = {"output": context.last_output}
        if context.sibling_outputs is not None:
            root["siblings"] = {
                name: {"output": list(values)}
                for name, values in context.sibling_outputs.items()
            }
        if context.has_previous:
            root["previous"] = {"output": context.previous_output}
        if context.has_self_output:
            root["output"] = context.self_output
        brief = context.brief
        if brief is not None:
            root["brief"] = {
                "text": brief.render() if hasattr(brief, "render") else "",
                "items": [item.item_id for item in getattr(brief, "items", [])],
                "count": len(getattr(brief, "items", [])),
                "dropped": int(getattr(brief, "dropped", 0)),
            }
        return root


class SourcePresentation:
    def __init__(self, number: int, item: Any, citations):
        self.number, self.item, self.citations = number, item, citations

    def text(self) -> str:
        marker = f"[{self.number}]"
        if not isinstance(self.item, dict):
            return marker + " " + self.citations.strip_markers(str(self.item))
        title = str(self.item.get("title", "") or "").strip()
        content = str(self.item.get("content") or self.item.get("summary") or "")
        body = self.citations.strip_markers(content).strip()
        heading = marker + (" " + title if title else "")
        return heading + ("\n" + body if body else "")


class PipeArguments:
    def __init__(self, raw: str):
        self.raw = raw

    def tokens(self) -> list[str]:
        start, quote, pieces = 0, "", []
        for index, character in enumerate(self.raw):
            if quote:
                if character == quote:
                    quote = ""
            elif character in "\"'":
                quote = character
            elif character == ",":
                pieces.append(self.raw[start:index])
                start = index + 1
        return [*pieces, self.raw[start:]]

    def values(self) -> list[Any]:
        if not (self.raw or "").strip():
            return []
        literals = {"true": True, "false": False, "null": None, "none": None}

        def decode(token):
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
                return token[1:-1]
            if token.lower() in literals:
                return literals[token.lower()]
            try:
                return int(token)
            except ValueError:
                try:
                    return float(token)
                except ValueError as exc:
                    raise BindingError(
                        f"pipe argument {token!r} is not a literal"
                    ) from exc

        return [decode(token) for part in self.tokens() if (token := part.strip())]


class BindingPath:
    def __init__(self, path: str, expression: str):
        self.path, self.expression = path, expression

    def read(self, root: Any) -> Any:
        value = root
        for segment in filter(None, self.path.split(".")):
            if isinstance(value, dict):
                next_value = value.get(segment, _MISSING)
            elif isinstance(value, list) and segment.isdigit():
                index = int(segment)
                next_value = value[index] if 0 <= index < len(value) else _MISSING
            else:
                raise BindingError(
                    f"cannot read {segment!r} from a {type(value).__name__}",
                    self.expression,
                )
            if next_value is _MISSING:
                raise BindingError(
                    f"unresolved reference at {segment!r}; "
                    + (
                        f"available keys: {', '.join(sorted(map(str, value))) or '(none)'}"
                        if isinstance(value, dict)
                        else f"list contains {len(value)} items"
                    ),
                    self.expression,
                )
            value = next_value
        return value


class BindingPlan:
    def __init__(self, expression: str, context: BindingContext):
        self.expression, self.context = expression, context

    def resolve(self) -> Any:
        parts = list(map(str.strip, self.expression.split("|")))
        head, pipes = parts[0], parts[1:]
        names = {match.group(1) for pipe in pipes if (match := _PIPE_RE.match(pipe))}
        missing_loop_output = (
            _root_name(head) == "last" and not self.context.has_last
        ) or (_is_previous_ref(head) and not self.context.has_previous)
        if self.context.iter_index == 0 and missing_loop_output:
            return _run_pipes(None, pipes, self.expression, self.context)
        if head.startswith("secret:"):
            value = self.secret(head[len("secret:") :].strip())
        else:
            value = _walk_path(self.context.as_root(), head, self.expression)
        if _is_sibling_ref(head):
            value = _flatten_sibling(value)
            if names.isdisjoint(_EXPLICIT_VIEW_PIPES):
                value = _default_sibling_view(value)
        return _run_pipes(value, pipes, self.expression, self.context)

    def secret(self, key: str) -> Any:
        if not key:
            raise BindingError("secret reference needs a key", self.expression)
        resolve_secret = self.context.secret_resolver
        if resolve_secret is None:
            raise BindingError("no secret resolver available", self.expression)
        value = resolve_secret(key)
        if value is None:
            raise BindingError(f"secret {key!r} is not set", self.expression)
        return value

    def apply(self, value: Any, pipes: list[str]) -> Any:
        for pipe in pipes:
            call = _PIPE_RE.match(pipe)
            if call is None:
                raise BindingError(f"malformed pipe {pipe!r}", self.expression)
            name, arguments = call.group(1), call.group(2) or ""
            handler = PIPES.get(name)
            if handler is None:
                raise BindingError(f"unknown pipe {name!r}", self.expression)
            try:
                value = (
                    _pipe_unseen(value, _seen=self.context.seen_filter)
                    if name == "unseen"
                    else handler(value, *_parse_pipe_args(arguments))
                )
            except BindingError as exc:
                raise BindingError(str(exc), self.expression) from exc
            except TypeError as exc:
                raise BindingError(
                    f"bad arguments for pipe {name!r}", self.expression
                ) from exc
        return value


class BindingTemplate:
    def __init__(self, context: BindingContext, *, fence_untrusted_spans: bool = False):
        self.context = context
        self.fence_untrusted_spans = fence_untrusted_spans

    def _interpolate(self, expression: str) -> str:
        value = _stringify(resolve_expr(expression, self.context))
        root = _root_name(expression.split("|", 1)[0].strip())
        if not self.fence_untrusted_spans or root not in {
            "inputs",
            "item",
            "trigger",
            "payload",
            "webhook",
            "fetched",
        }:
            return value
        from gideon.security.security import fence_untrusted

        return fence_untrusted(value, source=f"workflow:{root}")

    def render(self, template: Any) -> Any:
        if isinstance(template, dict):
            return dict((key, self.render(value)) for key, value in template.items())
        if isinstance(template, list):
            return list(map(self.render, template))
        if not isinstance(template, str):
            return template
        complete = _WHOLE_RE.match(template)
        if complete is not None:
            expression = complete.group(1).strip()
            if self.fence_untrusted_spans:
                return self._interpolate(expression)
            return resolve_expr(expression, self.context)
        segments: list[str] = []
        start = 0
        for match in _REF_RE.finditer(template):
            segments.extend(
                (
                    template[start : match.start()],
                    self._interpolate(match.group(1).strip()),
                )
            )
            start = match.end()
        return "".join([*segments, template[start:]])
