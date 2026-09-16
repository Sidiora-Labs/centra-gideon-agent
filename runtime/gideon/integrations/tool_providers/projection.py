"""Content-type-aware tool-output projection (OP1).

Replaces the role of the blunt head/tail char cap (:func:`maybe_truncate`) for
*large* outputs with a projection that keeps the **meaningful** slice for the
output's type — the failing lines of a log, the changed hunks of a diff, the
shape of a JSON blob — instead of cutting the middle out blindly.

Projection ≠ truncation: truncation loses; projection **defers**. The full raw
output is retained elsewhere (the tool-result store, OP2) and the projected
preview names how to fetch it. This module is the pure, side-effect-free
*shaping* half; the store + retrieval tool live in :mod:`result_store`.

Conservative + fail-soft by design (the cardinal failure is hiding the part the
model needed — see the plan §5 risk register):
  * a result already within ``cap`` passes through **untouched** (never project);
  * an **unknown/ambiguous** type falls back to head/tail :func:`maybe_truncate`
    (today's exact behavior) — projection only *engages* for a large result of a
    *recognized* type;
  * dispatch specificity: a matched RULE (project > user > builtin layers, §2.3)
    beats a declared ``content_type`` beats the heuristic sniff. A content-matched
    regex is more specific than a per-tool blanket declaration (the shell declares
    everything "log"; the builtin pack is what lets a ``git diff`` run through it
    still project as a diff) — and the worst case of any rule is a wrong projector
    with the raw still retained.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from gideon.integrations.tool_providers.base import maybe_truncate

logger = logging.getLogger(__name__)

DEFAULT_TOOL_OUTPUT_CAP = 60_000

CONTENT_TYPES = ("log", "diff", "json", "test", "csv", "code", "markdown", "generic")


@dataclass(frozen=True)
class ProjectionRule:
    """A taught dispatch rule: output whose head matches ``match_regex`` is projected
    with ``strategy`` (a builtin content type) — or, when any op field is set, shaped
    by the shared ops interpreter. Pure data — no code."""

    name: str
    match_regex: str
    strategy: str
    head: int = 0
    tail: int = 0
    keep: str = ""
    skip: str = ""
    count: str = ""


@dataclass(frozen=True)
class _CompiledRule:
    """A rule with its regexes compiled once (never compile on the dispatch path)."""

    name: str
    pattern: "re.Pattern[str]"
    strategy: str
    head: int = 0
    tail: int = 0
    keep: "re.Pattern[str] | None" = None
    skip: "re.Pattern[str] | None" = None
    count: "re.Pattern[str] | None" = None

    @property
    def has_ops(self) -> bool:
        return bool(self.head or self.tail or self.keep or self.skip or self.count)


def _compile_rules(rules, *, layer: str) -> tuple[_CompiledRule, ...]:
    """Compile a rule list (dataclasses or dicts) fail-soft: a rule with an invalid
    regex or unknown strategy is dropped + logged — a typo never breaks dispatch."""
    compiled: list[_CompiledRule] = []
    for r in rules or []:
        get = r.get if isinstance(r, dict) else lambda k, d="", _r=r: getattr(_r, k, d)
        strat = str(get("strategy", "")).strip().lower()
        pat = str(get("match_regex", "")).strip()
        if strat not in _PROJECTORS or not pat:
            logger.debug(
                "%s projection rule %r skipped (bad strategy/empty regex)",
                layer,
                get("name", "?"),
            )
            continue
        try:
            keep_s, skip_s, count_s = (
                str(get(k, "")).strip() for k in ("keep", "skip", "count")
            )
            compiled.append(
                _CompiledRule(
                    name=str(get("name", "")),
                    pattern=re.compile(pat, re.M),
                    strategy=strat,
                    head=max(0, int(get("head", 0) or 0)),
                    tail=max(0, int(get("tail", 0) or 0)),
                    keep=re.compile(keep_s) if keep_s else None,
                    skip=re.compile(skip_s) if skip_s else None,
                    count=re.compile(count_s) if count_s else None,
                )
            )
        except (re.error, TypeError, ValueError):
            logger.warning(
                "%s projection rule %r has an invalid regex/op — skipped",
                layer,
                get("name", "?"),
            )
    return tuple(compiled)


_USER_RULES: tuple[_CompiledRule, ...] = ()


def set_user_rules(rules: "list[ProjectionRule] | None") -> None:
    """Install the user's projection rules (from AppConfig). Idempotent — replaces
    the whole set. Fail-soft per rule (see :func:`_compile_rules`)."""
    global _USER_RULES
    _USER_RULES = _compile_rules(rules, layer="user")


# in-tree (the analog of OpenHuman's rule pack, sized to what Gideon's own tools
_BUILTIN_RULES: "tuple[_CompiledRule, ...] | None" = None


def _builtin_rules() -> tuple[_CompiledRule, ...]:
    global _BUILTIN_RULES
    if _BUILTIN_RULES is None:
        try:
            raw = (Path(__file__).resolve().parent / "rules_builtin.json").read_text(
                "utf-8"
            )
            _BUILTIN_RULES = _compile_rules(json.loads(raw), layer="builtin")
        except Exception:  # noqa: BLE001 — a bad pack must never break dispatch
            logger.warning(
                "builtin projection rule pack unreadable — skipped", exc_info=True
            )
            _BUILTIN_RULES = ()
    return _BUILTIN_RULES


_PROJECT_DIR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gideon_projection_project_dir", default=""
)
_PROJECT_RULES_CACHE: dict[str, tuple[float, tuple[_CompiledRule, ...]]] = {}
_PROJECT_RULES_MAX = 50
_PROJECT_FILE_MAX_BYTES = 262_144


def bind_project_dir(cwd) -> "contextvars.Token[str]":
    """Bind the session cwd whose ``.gideon/projection_rules.json`` supplies
    the project rule layer for this dispatch. Returns the reset token."""
    return _PROJECT_DIR.set(str(cwd) if cwd else "")


def reset_project_dir(token) -> None:
    try:
        _PROJECT_DIR.reset(token)
    except (ValueError, LookupError):
        pass


def _project_rules() -> tuple[_CompiledRule, ...]:
    """The project layer's compiled rules ("" dir / no file / bad file → none).
    mtime-cached per file; never raises."""
    d = _PROJECT_DIR.get()
    if not d:
        return ()
    f = Path(d) / ".gideon" / "projection_rules.json"
    try:
        st = f.stat()
    except OSError:
        return ()
    key = str(f)
    cached = _PROJECT_RULES_CACHE.get(key)
    if cached is not None and cached[0] == st.st_mtime:
        return cached[1]
    rules: tuple[_CompiledRule, ...] = ()
    try:
        if st.st_size <= _PROJECT_FILE_MAX_BYTES:
            data = json.loads(f.read_text("utf-8"))
            if isinstance(data, list):
                rules = _compile_rules(data[:_PROJECT_RULES_MAX], layer="project")
    except Exception:  # noqa: BLE001 — a bad project file must never break dispatch
        logger.warning("project projection rules unreadable: %s — skipped", f)
    if len(_PROJECT_RULES_CACHE) > 32:
        _PROJECT_RULES_CACHE.clear()
    _PROJECT_RULES_CACHE[key] = (st.st_mtime, rules)
    return rules


def _match_rule(sample: str) -> _CompiledRule | None:
    """First matching rule across the overlay, most-specific layer first:
    project > user > builtin. Never raises."""
    for layer in (_project_rules(), _USER_RULES, _builtin_rules()):
        for rule in layer:
            try:
                if rule.pattern.search(sample):
                    return rule
            except re.error:
                continue
    return None


def _apply_ops(text: str, rule: _CompiledRule, cap: int) -> str:
    """The shared rule-ops interpreter (§2.3 rule ops v2): keep/skip line filters →
    count folder → head/tail window, then the final safety cap. Declarative only —
    every op is data the rule carried; no user code runs."""
    lines = text.splitlines()
    n_orig = len(lines)
    if rule.keep is not None:
        lines = [ln for ln in lines if rule.keep.search(ln)]
    if rule.skip is not None:
        lines = [ln for ln in lines if not rule.skip.search(ln)]
    if rule.count is not None:
        kept: list[str] = []
        folded = 0
        for ln in lines:
            if rule.count.search(ln):
                folded += 1
            else:
                kept.append(ln)
        if folded:
            kept.append(f"…[{folded} line(s) matching /{rule.count.pattern}/ elided]…")
        lines = kept
    if (rule.head or rule.tail) and len(lines) > rule.head + rule.tail:
        middle = len(lines) - rule.head - rule.tail
        window = lines[: rule.head] + [f"…[{middle} middle line(s) elided]…"]
        if rule.tail:
            window += lines[-rule.tail :]
        lines = window
    header = f"[rule '{rule.name}': {n_orig} → {len(lines)} line(s)]\n"
    out = header + "\n".join(lines)
    capped, _, _ = maybe_truncate(out, cap)
    return capped


@dataclass
class Projection:
    """Outcome of projecting one tool output."""

    text: str
    truncated: bool
    original_length: int | None
    content_type: str


_DIFF_RE = re.compile(r"^(diff --git |@@ -\d|index [0-9a-f]+\.\.|\+\+\+ |--- )", re.M)
_TEST_RE = re.compile(
    r"\b(PASSED|FAILED|\d+ passed|\d+ failed|=+ test session|FAIL\b|AssertionError)\b"
)
_JSON_LEAD_RE = re.compile(r"^\s*[\[{]")


def infer_content_type(text: str) -> str:
    """Best-effort content-type sniff. Returns a member of :data:`CONTENT_TYPES`.

    Conservative: only returns a specific type on a confident marker; anything
    ambiguous returns ``"generic"`` so projection falls back to the safe cap.
    """
    if not text:
        return "generic"
    sample = text[:4096]
    rule = _match_rule(sample)
    if rule is not None:
        return rule.strategy
    if _DIFF_RE.search(sample):
        return "diff"
    if _TEST_RE.search(sample):
        return "test"
    if _JSON_LEAD_RE.match(sample):
        try:
            json.loads(text)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    if _looks_like_csv(sample):
        return "csv"
    if _looks_like_code(sample):
        return "code"
    return "generic"


def _looks_like_csv(sample: str) -> bool:
    lines = [ln for ln in sample.splitlines() if ln.strip()][:5]
    if len(lines) < 2:
        return False
    counts = [ln.count(",") for ln in lines]
    return counts[0] >= 1 and len(set(counts)) == 1


_CODE_MARKER_RE = re.compile(
    r"^\s*(def |class |import |from \w+ import |function |const |let |var |"
    r"pub fn |fn |func |interface |type \w+ (struct|interface)|package |#include |"
    r"public |private |protected )",
    re.M,
)


def _looks_like_code(sample: str) -> bool:
    """Conservative code sniff (§2.2): a shebang, or a meaningful DENSITY of
    definition/import markers across the sample's lines — a stray ``import`` in prose
    must not trip it (mis-typing prose as code is worse than the generic fallback)."""
    if sample.startswith("#!"):
        return True
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    hits = len(_CODE_MARKER_RE.findall(sample))
    return hits >= 3 and hits / len(lines) >= 0.05


_ERROR_LINE_RE = re.compile(
    r"(error|warn|fail|exception|traceback|fatal|denied|✗|❌)", re.I
)


def _project_log(text: str, cap: int) -> str:
    """Head + the error/warning lines + tail + a line-count note (not a blind
    middle-cut). The signal in a long log is the error lines, wherever they are."""
    lines = text.splitlines()
    n = len(lines)
    head_n, tail_n = 40, 40
    head = lines[:head_n]
    tail = lines[-tail_n:] if n > head_n + tail_n else []
    middle = lines[head_n : n - tail_n] if tail else []
    errs = [ln for ln in middle if _ERROR_LINE_RE.search(ln)]
    err_cap = 60
    elided_errs = max(0, len(errs) - err_cap)
    errs = errs[:err_cap]
    parts: list[str] = []
    parts.extend(head)
    if errs:
        parts.append(
            f"\n…[{len(middle)} middle lines elided; {len(errs)} error/warning line(s) kept"
            + (f", {elided_errs} more errors not shown" if elided_errs else "")
            + "]…\n"
        )
        parts.extend(errs)
    elif middle:
        parts.append(
            f"\n…[{len(middle)} middle lines elided — no error/warning markers]…\n"
        )
    if tail:
        parts.append("\n…tail…")
        parts.extend(tail)
    out = "\n".join(parts)
    capped, _, _ = maybe_truncate(out, cap)
    return capped


def _project_diff(text: str, cap: int) -> str:
    """Changed hunks + a +N/-M stat summary; the unchanged context is the noise."""
    add = sum(
        1 for ln in text.splitlines() if ln.startswith("+") and not ln.startswith("+++")
    )
    rem = sum(
        1 for ln in text.splitlines() if ln.startswith("-") and not ln.startswith("---")
    )
    files = len(re.findall(r"^diff --git ", text, re.M)) or len(
        re.findall(r"^\+\+\+ ", text, re.M)
    )
    summary = f"[diff: {files} file(s), +{add}/-{rem}]\n"
    budget = max(0, cap - len(summary)) if cap else None
    capped, _, _ = maybe_truncate(text, budget)
    return summary + capped


_SCHEMA_SAMPLE = 50


def _array_schema(items: list) -> str:
    """Infer a compact per-field schema over a bounded sample of dict items:
    field name, value type(s), numeric range, and null count. One line per field."""
    sample = items[:_SCHEMA_SAMPLE]
    fields: dict[str, dict] = {}
    for it in sample:
        for k, v in it.items():
            f = fields.setdefault(
                k, {"types": set(), "nulls": 0, "min": None, "max": None}
            )
            if v is None:
                f["nulls"] += 1
                continue
            f["types"].add(type(v).__name__)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                f["min"] = v if f["min"] is None else min(f["min"], v)
                f["max"] = v if f["max"] is None else max(f["max"], v)
    lines = []
    for k, f in list(fields.items())[:40]:
        t = "/".join(sorted(f["types"])) or "null"
        extra = ""
        if f["min"] is not None:
            extra += f" [{f['min']}..{f['max']}]"
        if f["nulls"]:
            extra += f" ({f['nulls']}/{len(sample)} null)"
        lines.append(f"  {k}: {t}{extra}")
    return "\n".join(lines)


def _fold_array(data: list, cap: int) -> str:
    """The crusher's array view: repeated-structure folding + per-field schema from a
    bounded sample + first/last item verbatim — so a 100K-item response answers "what
    shape is this and what's in it" in ~1K chars."""
    n = len(data)
    if n == 0:
        return "[array: 0 items]"
    sample = data[:_SCHEMA_SAMPLE]
    dicts = [it for it in sample if isinstance(it, dict)]
    parts: list[str] = []
    if len(dicts) == len(sample):
        key_sets = {tuple(sorted(it.keys())) for it in dicts}
        uniform = " uniform shape," if len(key_sets) == 1 else ""
        parts.append(
            f"[array: {n} items,{uniform} fields inferred from first {len(sample)}]"
        )
        parts.append(_array_schema(dicts))
    else:
        type_counts: dict[str, int] = {}
        for it in sample:
            type_counts[type(it).__name__] = type_counts.get(type(it).__name__, 0) + 1
        mix = ", ".join(f"{t}×{c}" for t, c in type_counts.items())
        parts.append(f"[array: {n} items, sampled types: {mix}]")
    item_budget = max(200, cap // 4)
    parts.append(
        "first item:\n" + json.dumps(data[0], indent=2, default=str)[:item_budget]
    )
    if n > 1:
        parts.append(
            "last item:\n" + json.dumps(data[-1], indent=2, default=str)[:item_budget]
        )
    return "\n".join(parts)


def _project_json(text: str, cap: int) -> str:
    """The JSON crusher (§2.1): per-path schema inference over arrays, first/last item
    verbatim, repeated-structure folding — not a mid-string cut that yields invalid
    JSON. Falls back to head/tail if it doesn't parse (shouldn't, since inference
    parsed it, but declared-type json might not)."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        capped, _, _ = maybe_truncate(text, cap)
        return capped
    if isinstance(data, list):
        body = _fold_array(data, cap)
    elif isinstance(data, dict):
        keys = list(data.keys())
        shape = (
            "{object: "
            + ", ".join(f"{k}: {type(data[k]).__name__}" for k in keys[:40])
            + "}"
        )
        parts = [shape]
        for k in keys[:40]:
            v = data[k]
            if isinstance(v, list) and len(v) > 10:
                folded = _fold_array(v, cap // 2)
                parts.append(
                    f'"{k}":\n' + "\n".join("  " + ln for ln in folded.splitlines())
                )
        sample = json.dumps(data, indent=2, default=str)
        parts.append(
            "sample:\n" + (sample[: cap // 2] if len(sample) > cap else sample)
        )
        body = "\n".join(parts)
    else:
        body = json.dumps(data, default=str)
    capped, _, _ = maybe_truncate(body, cap)
    return capped


def _project_test(text: str, cap: int) -> str:
    """Failures + the summary line; elide the passing noise."""
    lines = text.splitlines()
    fail_lines = [
        ln
        for ln in lines
        if re.search(r"\b(FAIL|FAILED|ERROR|AssertionError|✗)\b", ln)
        or ln.strip().startswith(("E   ", "FAILED", "_____"))
    ]
    summary = [
        ln
        for ln in lines
        if re.search(r"\b(\d+ passed|\d+ failed|\d+ error|passed|failed)\b", ln)
        and ("=" in ln or "passed" in ln or "failed" in ln)
    ]
    tail = lines[-12:]
    parts = []
    if fail_lines:
        parts.append(
            f"[test output: {len(fail_lines)} failure/error line(s), {len(lines)} total]"
        )
        parts.extend(fail_lines[:120])
    if summary:
        parts.append("\nsummary:")
        parts.extend(summary[-5:])
    if not fail_lines and not summary:
        parts.extend(tail)
    out = "\n".join(parts)
    capped, _, _ = maybe_truncate(out, cap)
    return capped


def _project_csv(text: str, cap: int) -> str:
    """Header + first/last rows + a row count."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 12:
        capped, _, _ = maybe_truncate(text, cap)
        return capped
    head = lines[:6]
    tail = lines[-5:]
    out = "\n".join([*head, f"…[{len(lines) - 11} more rows]…", *tail])
    capped, _, _ = maybe_truncate(out, cap)
    return capped


def _outline_python(text: str) -> str | None:
    """AST outline of a Python module: module docstring first-line, the import block,
    and every class/def signature with its docstring first-line + line number.
    Returns None when the text doesn't parse (caller falls to the regex outliner)."""
    import ast

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None
    lines: list[str] = []
    mod_doc = ast.get_docstring(tree)
    if mod_doc:
        lines.append(f'"""{mod_doc.splitlines()[0]}"""')
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    for n in imports[:40]:
        try:
            lines.append(ast.unparse(n))
        except (
            Exception
        ):  # noqa: BLE001 — an unparse edge case must not kill the outline
            continue

    def _sig(node, indent: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            try:
                args = ast.unparse(node.args)
            except Exception:  # noqa: BLE001
                args = "…"
            ret = ""
            if node.returns is not None:
                try:
                    ret = f" -> {ast.unparse(node.returns)}"
                except Exception:  # noqa: BLE001
                    ret = ""
            lines.append(
                f"{indent}{kind} {node.name}({args}){ret}:  # line {node.lineno}"
            )
            doc = ast.get_docstring(node)
            if doc:
                lines.append(f'{indent}    """{doc.splitlines()[0]}"""')
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(b for b in (_safe_unparse(x) for x in node.bases) if b)
            lines.append(
                f"{indent}class {node.name}({bases}):  # line {node.lineno}"
                if bases
                else f"{indent}class {node.name}:  # line {node.lineno}"
            )
            doc = ast.get_docstring(node)
            if doc:
                lines.append(f'{indent}    """{doc.splitlines()[0]}"""')
            for child in node.body:
                _sig(child, indent + "    ")

    for node in tree.body:
        _sig(node)
    return "\n".join(lines) if lines else None


def _safe_unparse(node) -> str:
    import ast

    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


_OUTLINE_RE = re.compile(
    r"^\s*(def |class |async def |function |const \w+ = |export |pub fn |fn |func |"
    r"interface |type \w+|impl |struct |enum |trait |public |private |protected |"
    r"import |from \w+ import |#include |package |use )",
)


def _outline_regex(text: str) -> str:
    """Language-agnostic outliner: definition/import header lines with their line
    numbers — the honest fallback when Python's ast can't parse (§2.2)."""
    out: list[str] = []
    for i, ln in enumerate(text.splitlines(), 1):
        if _OUTLINE_RE.match(ln):
            out.append(f"{ln.rstrip()}  # line {i}")
    return "\n".join(out)


def _project_code(text: str, cap: int) -> str:
    """AST-aware code compressor (§2.2): a signatures-and-docstrings outline with a
    line-number map, so a large module projects to its API surface — the raw body is
    one ``tool_result_get(line_start=…)`` away. Python via stdlib ``ast``; anything
    else (or unparseable Python) via the regex outliner; an empty outline falls back
    to head/tail (fail-soft is non-negotiable on this path)."""
    n_lines = text.count("\n") + 1
    outline = _outline_python(text) or _outline_regex(text)
    if not outline.strip():
        capped, _, _ = maybe_truncate(text, cap)
        return capped
    header = (
        f"[code outline: {n_lines} lines, {len(text)} chars — signatures + line map]\n"
    )
    budget = max(0, cap - len(header)) if cap else None
    capped, _, _ = maybe_truncate(outline, budget)
    return header + capped


_PROJECTORS = {
    "log": _project_log,
    "diff": _project_diff,
    "json": _project_json,
    "test": _project_test,
    "csv": _project_csv,
    "code": _project_code,
}


def project_output(
    text: str,
    *,
    cap: int | None,
    content_type: str | None = None,
) -> Projection:
    """Project ``text`` to a type-aware preview within ``cap`` chars.

    * ``cap`` is None or text already fits → pass through untouched (never
      project a small result).
    * ``content_type`` declared by the tool wins; else infer (conservative).
    * A recognized large type → its projector (keeps the salient slice).
    * ``generic``/unknown → head/tail :func:`maybe_truncate` (today's behavior).

    Returns a :class:`Projection`. ``content_type`` on the result reflects what
    was actually used (so the renderer + store can read it).
    """
    if cap is None or len(text) <= cap:
        ctype = content_type or infer_content_type(text)
        return Projection(
            text=text,
            truncated=False,
            original_length=None,
            content_type=ctype if ctype in CONTENT_TYPES else "generic",
        )

    original_length = len(text)
    # A matched RULE (project > user > builtin) beats even the tool's DECLARED type:
    # a content-matched regex is more specific than a per-tool blanket (run_command
    # declares everything "log" — the builtin pack exists precisely so a `git diff`
    # or pytest run through the shell projects as diff/test). Worst case is a wrong
    # projector with the raw still retained (fail-soft doctrine).
    rule = _match_rule(text[:4096])
    if rule is not None and rule.has_ops:
        return Projection(
            text=_apply_ops(text, rule, cap),
            truncated=True,
            original_length=original_length,
            content_type=rule.strategy,
        )
    if rule is not None:
        ctype = rule.strategy
    elif content_type in CONTENT_TYPES:
        ctype = content_type
    else:
        ctype = infer_content_type(text)
    projector = _PROJECTORS.get(ctype)
    if projector is None:
        capped, _, _ = maybe_truncate(text, cap)
        return Projection(
            text=capped,
            truncated=True,
            original_length=original_length,
            content_type="generic",
        )
    projected = projector(text, cap)
    return Projection(
        text=projected,
        truncated=True,
        original_length=original_length,
        content_type=ctype,
    )


def _record_savings(compressor: str, chars_in: int, chars_out: int) -> None:
    """Record a projection's counterfactual savings (§1.3). Best-effort, never raises.

    Model hint is ``"unknown"`` here — ``project_and_retain`` is a dispatch-time seam with
    no resolved-model in scope, and the plan explicitly allows ``"unknown"`` rather than
    threading a model through (accounting must never block/slow dispatch). The savings
    store cross-references the guardrails' real token counts once that lands."""
    if chars_in <= 0 or chars_out >= chars_in:
        return
    try:
        from datetime import datetime

        from gideon.integrations.tool_providers import savings

        month = datetime.now().strftime("%Y-%m")
        savings.record_saving(
            month=month,
            model="unknown",
            compressor=compressor,
            chars_in=chars_in,
            chars_out=chars_out,
        )
    except Exception:  # noqa: BLE001 — metering must never break a tool call
        logger.debug("savings accounting failed", exc_info=True)


def project_and_retain(
    text: str,
    *,
    session_key: str = "",
    content_type: str | None = None,
    cap: int | None,
) -> tuple[str, dict]:
    """Project ``text`` AND retain its raw for on-demand retrieval — the single
    dispatch-time discipline every tool surface shares (native builtins AND the MCP
    adapter, OP5), so no surface loses the retrievable-raw guarantee.

    Returns ``(output_text, metadata)`` where metadata carries ``content_type`` and,
    when the result was projected and a ``session_key`` is available, ``raw_ref`` — plus
    the preview names the recovery affordance (``tool_result_get(result_id="r_…")``) so
    the model can pull the dropped slice. Small / unknown → pass-through (fail-soft),
    exactly as ``project_output``."""
    from gideon.integrations.tool_providers import result_store

    proj = project_output(text, cap=cap, content_type=content_type)
    meta: dict = {
        "content_type": proj.content_type,
        "truncated": proj.truncated,
        "original_length": proj.original_length,
    }
    if not (proj.truncated and session_key):
        return proj.text, meta
    raw_ref = result_store.store_result(
        session_key, text, content_type=proj.content_type
    )
    if not raw_ref:
        return proj.text, meta
    meta["raw_ref"] = raw_ref
    out = proj.text + (
        f"\n\n[projected {proj.content_type} output: showing {len(proj.text)} of "
        f"{proj.original_length} chars — full result: "
        f'tool_result_get(result_id="{raw_ref}", line_start=…, line_end=…) '
        f"or grep=…]"
    )
    _record_savings(proj.content_type, proj.original_length or 0, len(proj.text))
    return out, meta
