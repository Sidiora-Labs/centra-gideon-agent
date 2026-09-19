"""Static architectural-boundary scanner (§1.3).

Pure-static checks (AST/regex over files, no execution), each with a stable check-id that
rule specs reference via their ``scanner:`` frontmatter. The scanner is trust-critical: a
check that cries wolf on a clean tree erodes the whole harness, so every check here is
calibrated to produce **zero findings on a clean HEAD** and each is either:

- **ERROR** — an exactly-derivable invariant (parity of two enumerable sets); a finding is
  a real defect.
- **WARNING** — a heuristic that can't prove intent; a finding is worth a look, not a hard
  stop.

Findings are WHAT/WHY/FIX-formatted so a coding agent self-corrects without a human.

Checks operate over a caller-supplied set of files (``run --diff`` passes the changed
files; a bare ``scan`` passes the whole tracked tree). A check that needs a cross-file
"other end" (the FE lifecycle union, the allowlist) reads that end from the repo
regardless of whether it's in the changed set — the invariant is about agreement, and
either end changing can break it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    """One scanner finding. ``check`` is the stable check-id; ``level`` is ERROR/WARNING;
    ``what``/``why``/``fix`` are the agent-facing triad; ``file``/``line`` locate it."""

    check: str
    level: str
    file: Path
    line: int
    what: str
    why: str
    fix: str

    def format(self, root: Path) -> str:
        try:
            rel = self.file.relative_to(root)
        except ValueError:
            rel = self.file
        loc = f"{rel}:{self.line}" if self.line else str(rel)
        marker = "❌" if self.level == ERROR else "⚠️ "
        return (
            f"{marker} [{self.check}] {loc}\n"
            f"    WHAT: {self.what}\n"
            f"    WHY:  {self.why}\n"
            f"    FIX:  {self.fix}"
        )


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _read(path: Path) -> str:
    """Source text for a check. Undecodable bytes are REPLACED, never raised: a checked-in
    file holding one stray byte would otherwise abort the whole scan, and every check here
    reads ASCII structure (imports, call names, list literals) that a U+FFFD elsewhere in
    the file leaves intact — including the line numbering a finding is attributed by."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _is_under(path: Path, root: Path, *parts: str) -> bool:
    """True if ``path`` is under ``root/<parts…>`` (path-prefix match on relative parts)."""
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return rel_parts[: len(parts)] == parts


def _string_literals_in(node: ast.AST) -> list[str]:
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def check_hook_provider_parity(files: list[Path], root: Path) -> list[Finding]:
    """Every provider-name string returned by a ``name`` property under
    ``action_providers/`` must be in ``ALLOWED_HOOK_PROVIDERS`` (validation.py).

    Exact set parity → ERROR. Reads both ends from the repo (the allowlist and the
    provider files) so a change to either end is caught; only reports when a
    provider file is in the changed set (or the allowlist itself changed).
    """
    ap_root = root / "runtime" / "gideon" / "integrations" / "action_providers"
    validation_py = root / "runtime" / "gideon" / "assurance" / "validation.py"
    changed = set(files)
    touches_providers = any(
        _is_under(f, root, "src", "gideon", "action_providers") for f in changed
    )
    touches_allowlist = validation_py in changed
    if not (touches_providers or touches_allowlist):
        return []

    allowlist = _extract_frozenset_members(
        _read(validation_py), "ALLOWED_HOOK_PROVIDERS"
    )
    if allowlist is None:
        return []

    findings: list[Finding] = []
    for f in sorted(ap_root.glob("*_provider.py")) if ap_root.is_dir() else []:
        name, lineno = _provider_name_of(_read(f))
        if name and name not in allowlist:
            findings.append(
                Finding(
                    check="hook-provider-parity",
                    level=ERROR,
                    file=f,
                    line=lineno,
                    what=f"action provider {name!r} is not in ALLOWED_HOOK_PROVIDERS",
                    why="a provider absent from the allowlist makes hook create/update reject "
                    "any hook that uses it, even though the executor exists",
                    fix=f"add {name!r} to the ALLOWED_HOOK_PROVIDERS frozenset in validation.py",
                )
            )
    return findings


def _extract_frozenset_members(source: str, name: str) -> set[str] | None:
    """Parse ``NAME = frozenset({...})`` and return its string members, or None if absent."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if name in targets:
                return set(_string_literals_in(node.value))
    return None


def _provider_name_of(source: str) -> tuple[str, int]:
    """Return (name, lineno) for the string returned by a ``name`` property, or ("", 0).

    Matches the house idiom: ``@property\n def name(self) -> str: return "foo"``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "", 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "name":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant):
                    if isinstance(sub.value.value, str):
                        return sub.value.value, sub.lineno
    return "", 0


def check_sse_event_registered(files: list[Path], root: Path) -> list[Finding]:
    """Every literal loop-registry ``publish(..., "event", ...)`` event string must appear
    in the FE ``RUN_LIFECYCLE`` union. Exact set membership → ERROR.

    Only literal event names are checkable statically; a variable ``event`` is skipped
    (the WARNING would be noise). The FE union is always read from the repo.
    """
    changed = set(files)
    py_changed = [
        f for f in changed if f.suffix == ".py" and _is_loop_sse_backend(f, root)
    ]
    lifecycle_path = (
        root / "apps" / "console" / "src" / "features" / "loops" / "useRunStream.ts"
    )
    if not lifecycle_path.exists():
        lifecycle_path = (
            root / "apps" / "console" / "src" / "pages" / "loops" / "useRunStream.ts"
        )
    lifecycle = _read(lifecycle_path)
    union = _extract_ts_run_lifecycle(lifecycle)
    if union is None:
        return []

    if lifecycle_path in changed:
        py_changed = _loop_sse_backend_files(root)

    findings: list[Finding] = []
    published: set[str] = set()
    for f in py_changed:
        for event, lineno in _loop_publish_events(_read(f)):
            published.add(event)
            if event not in union:
                findings.append(
                    Finding(
                        check="sse-event-registered",
                        level=ERROR,
                        file=f,
                        line=lineno,
                        what=f"loop SSE event {event!r} is published but not in RUN_LIFECYCLE",
                        why="EventSource registers one listener per event name; an event not "
                        "in the FE union is silently dropped — no error, no UI update",
                        fix=f"add {event!r} to RUN_LIFECYCLE in "
                        "apps/console/src/features/loops/useRunStream.ts (and handle it)",
                    )
                )
    if lifecycle_path in changed:
        for event in sorted(union - published):
            findings.append(
                Finding(
                    check="sse-event-registered",
                    level=ERROR,
                    file=lifecycle_path,
                    line=1,
                    what=(
                        f"RUN_LIFECYCLE registers {event!r} but no loop SSE backend "
                        "publishes it"
                    ),
                    why="the listener union is the live loop-stream contract; unbacked ledger or "
                    "workflow names falsely promise realtime loop updates",
                    fix=(
                        f"remove {event!r} from RUN_LIFECYCLE or add its real loop "
                        "SSE publisher"
                    ),
                )
            )
    return findings


def _is_loop_sse_backend(path: Path, root: Path) -> bool:
    return (
        _is_under(path, root, "runtime", "gideon", "automation", "loop", "kinds")
        or path == root / "runtime" / "gideon" / "automation" / "loop" / "watchdog.py"
        or path
        == root
        / "runtime"
        / "gideon"
        / "interfaces"
        / "dashboard"
        / "handlers"
        / "loop_routes.py"
    )


def _loop_sse_backend_files(root: Path) -> list[Path]:
    files = [
        root / "runtime" / "gideon" / "automation" / "loop" / "watchdog.py",
        root
        / "runtime"
        / "gideon"
        / "interfaces"
        / "dashboard"
        / "handlers"
        / "loop_routes.py",
    ]
    kinds = root / "runtime" / "gideon" / "automation" / "loop" / "kinds"
    files.extend(sorted(kinds.glob("*.py")) if kinds.is_dir() else [])
    return [f for f in files if f.is_file()]


def _extract_ts_run_lifecycle(source: str) -> set[str] | None:
    """Extract the string members of ``export const RUN_LIFECYCLE = [ ... ] as const``."""
    m = re.search(r"RUN_LIFECYCLE\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    if not m:
        return None
    return set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))


def _loop_publish_events(source: str) -> list[tuple[str, int]]:
    """Find ``<recv>.publish(<key>, "<event>", ...)`` calls where recv is a loop-registry
    receiver, returning (event, lineno) for literal event names."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "_publish" and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "self":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    val = node.args[1].value
                    if isinstance(val, str):
                        out.append((val, node.lineno))
            continue
        if node.func.attr != "publish":
            continue
        if not _receiver_is_loop_registry(node.func.value):
            continue
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            val = node.args[1].value
            if isinstance(val, str):
                out.append((val, node.lineno))
    return out


def _receiver_is_loop_registry(recv: ast.AST) -> bool:
    """True if the publish receiver is the loop SSE registry: a call to ``loop_sse()``
    (possibly chained: ``x.loop_sse()``) or the loop-kind ``ctx`` object."""
    if isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute):
        return recv.func.attr == "loop_sse"
    if isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name):
        return recv.func.id == "loop_sse"
    if isinstance(recv, ast.Name):
        return recv.id == "ctx"
    return False


def check_config_four_points(files: list[Path], root: Path) -> list[Finding]:
    """A ``_meta``-carrying field on ANY config dataclass must appear in ``AppConfig.load()``'s
    mapping and in ``to_dict()`` output. Missing either → the silent-drop / silent-revert bug.
    Exact presence → ERROR.

    Static approximation: a field name declared with ``metadata=_meta(...)`` must appear
    somewhere in ``load()``'s body (as ``<field>=`` kwarg) — the load mapping is the point
    most often forgotten. to_dict() serializes whole sections via ``asdict`` so per-field
    presence there is covered by test_config_roundtrip; the scanner guards the load()
    mapping specifically (the harder-to-test half at diff time).

    The DECLARATIONS are read from every module in ``config/``, not from ``loader.py`` alone,
    and the load mapping is read from ``loader.py`` because that is where ``AppConfig`` lives.
    Those two used to be the same file, and PHF-14 split them: the sections moved out to
    sibling modules while ``AppConfig`` stayed. A scan pinned to ``loader.py`` would then have
    kept passing while silently checking 280 of 367 fields — a gate that narrows without
    redding is worse than one that is absent, because the green is read as coverage.
    """
    config_dir_path = root / "runtime" / "gideon" / "core" / "config"
    changed = set(files)
    if not any(f.parent == config_dir_path and f.suffix == ".py" for f in changed):
        return []

    loader = config_dir_path / "loader.py"
    loader_tree = _parse_or_none(loader)
    if loader_tree is None:
        return []
    load_kwargs = _load_body_kwarg_names(
        loader_tree, _parse_or_none(config_dir_path / "decoding.py")
    )
    if load_kwargs is None:
        return []

    findings: list[Finding] = []
    for module in sorted(config_dir_path.glob("*.py")):
        tree = loader_tree if module == loader else _parse_or_none(module)
        if tree is None:
            continue
        for field_name, lineno in _meta_fields(tree):
            if field_name not in load_kwargs:
                findings.append(
                    Finding(
                        check="config-four-points",
                        level=ERROR,
                        file=module,
                        line=lineno,
                        what=f"config field {field_name!r} has _meta but is not set in "
                        "AppConfig.load()'s mapping",
                        why="a field absent from load()'s explicit mapping silently reverts to "
                        "its default on every reload (the user's setting won't stick)",
                        fix=f"map it in AppConfig.load(): {field_name}=...(<section>_data.get"
                        f"({field_name!r}, <default>))",
                    )
                )
    return findings


def _parse_or_none(path: Path) -> ast.Module | None:
    """Parse ``path``, or ``None`` if it is unreadable or does not parse."""
    try:
        return ast.parse(_read(path))
    except (SyntaxError, OSError):
        return None


def _meta_fields(tree: ast.Module) -> list[tuple[str, int]]:
    """Field names assigned ``= field(..., metadata=_meta(...))`` inside any class body."""
    out: list[tuple[str, int]] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if _calls_field_with_meta(stmt.value):
                    out.append((stmt.target.id, stmt.lineno))
    return out


def _calls_field_with_meta(value: ast.AST | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    is_field = (isinstance(value.func, ast.Name) and value.func.id == "field") or (
        isinstance(value.func, ast.Attribute) and value.func.attr == "field"
    )
    if not is_field:
        return False
    for kw in value.keywords:
        if kw.arg == "metadata" and isinstance(kw.value, ast.Call):
            fn = kw.value.func
            if (isinstance(fn, ast.Name) and fn.id == "_meta") or (
                isinstance(fn, ast.Attribute) and fn.attr == "_meta"
            ):
                return True
    return False


_LOAD_MAPPING_METHODS = frozenset({"load", "load_with_migration_state"})


def _load_body_kwarg_names(
    tree: ast.Module, policies: ast.Module | None = None
) -> set[str] | None:
    """All keyword-argument names used anywhere in ``AppConfig``'s load mapping.

    Covers the nested-constructor idiom ``legibility=LegibilityConfig(discover_tips=...)``
    — we collect every kwarg name at any depth of the mapping's body, which is exactly the
    set of field names it assigns. ``None`` means the anchor is gone, which callers treat as
    "cannot tell" rather than "nothing is mapped".
    """
    names: set[str] = set()
    found = False
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "AppConfig":
            for item in cls.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name in _LOAD_MAPPING_METHODS
                ):
                    found = True
                    for call in ast.walk(item):
                        if isinstance(call, ast.Call):
                            if (
                                policies is not None
                                and isinstance(call.func, ast.Name)
                                and call.func.id == "decode_configuration"
                            ):
                                from gideon.core.config.codec import mapped_fields

                                names.update(mapped_fields(policies))
                            for kw in call.keywords:
                                if kw.arg:
                                    names.add(kw.arg)
    return names if found else None


def check_app_sdk_boundary(files: list[Path], root: Path) -> list[Finding]:
    """Code under repo-root ``apps/`` may import core only via ``gideon.sdk.*``.
    A deep ``gideon.<non-sdk>`` import → ERROR (this mirrors the boundary test,
    promoted to diff time)."""
    findings: list[Finding] = []
    for f in files:
        if f.suffix != ".py" or not _is_under(f, root, "apps"):
            continue
        if f.name.startswith("test_"):
            continue
        for mod, lineno in _core_imports(_read(f)):
            parts = mod.split(".")
            if not (len(parts) >= 2 and parts[1] == "sdk"):
                findings.append(
                    Finding(
                        check="app-sdk-boundary",
                        level=ERROR,
                        file=f,
                        line=lineno,
                        what=f"app imports core internal {mod!r} (not gideon.sdk.*)",
                        why="apps must be removable; a deep core import couples the app to a "
                        "private internal that can move, breaking the boundary",
                        fix="import via gideon.sdk.* — or promote the needed symbol to "
                        "the SDK facade (a reviewed addition), never reach around it",
                    )
                )
    return findings


def _core_imports(source: str) -> list[tuple[str, int]]:
    """``gideon.*`` module paths imported by the source (absolute imports only)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("gideon"):
                    out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.startswith("gideon"):
                out.append((node.module, node.lineno))
    return out


_HOME_TOUCH_RE = re.compile(
    r"(config_dir\(\)|local_models_dir\(|(?<![=\w])save_credential\(|credential_store\()"
)
_ISOLATION_RE = re.compile(r"tmp_path|monkeypatch|GIDEON_HOME")


def check_destructive_test_isolation(files: list[Path], root: Path) -> list[Finding]:
    """A test module referencing config-dir/local-models/credential paths should carry a
    tmp_path/monkeypatch isolation fixture. Heuristic → WARNING."""
    findings: list[Finding] = []
    for f in files:
        if not (f.name.startswith("test_") and f.suffix == ".py"):
            continue
        src = _read(f)
        if _HOME_TOUCH_RE.search(src) and not _ISOLATION_RE.search(src):
            findings.append(
                Finding(
                    check="destructive-test-isolation",
                    level=WARNING,
                    file=f,
                    line=1,
                    what="test touches config-dir/local-models/credential paths with no "
                    "tmp_path/monkeypatch isolation in the module",
                    why="an unisolated destructive test can corrupt the real ~/.gideon "
                    "home (a bound model was once deleted this way) and flakes under xdist",
                    fix="take tmp_path/monkeypatch and redirect config_dir()/GIDEON_HOME "
                    "to a per-test temp dir",
                )
            )
    return findings


_PROMPT_ACCUM_RE = re.compile(
    r"\b(prompt|context|system_?prompt|messages)\b.{0,30}"
    r"(\+=|\.append\(|\.extend\(|\.format\(|f['\"])",
    re.IGNORECASE,
)
_EXTERNAL_TOKEN_RE = re.compile(
    r"\b(channel_?(text|message|content)|inbound|fetched|web_?content|page_?text|"
    r"untrusted|external_?text|user_?content)\b",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"fence_untrusted")


def check_fence_at_ingestion(
    files: list[Path], root: Path, *, changed_lines: dict[Path, set[int]] | None = None
) -> list[Finding]:
    """An ADDED line that appends external-sourced text to a prompt accumulator, in a file
    that never calls ``fence_untrusted``, may be missing a fence. Heuristic → WARNING.

    Diff-scoped: only considers lines in ``changed_lines`` when provided (the ``run --diff``
    path); with no diff it scans all lines of the given files. Narrow by construction to
    keep the signal high — a broad "mentions channel near message" match is pure noise.
    """
    findings: list[Finding] = []
    for f in files:
        if f.suffix != ".py" or not _is_under(f, root, "src", "gideon"):
            continue
        if f.name.startswith("test_"):
            continue
        src = _read(f)
        if _FENCE_RE.search(src):
            continue
        lines = src.splitlines()
        consider = changed_lines.get(f) if changed_lines is not None else None
        for i, line in enumerate(lines, start=1):
            if consider is not None and i not in consider:
                continue
            if _PROMPT_ACCUM_RE.search(line) and _EXTERNAL_TOKEN_RE.search(line):
                findings.append(
                    Finding(
                        check="fence-at-ingestion",
                        level=WARNING,
                        file=f,
                        line=i,
                        what="external-sourced text is appended to a prompt/context here and "
                        "this file never calls fence_untrusted",
                        why="untrusted text folded raw into a prompt lets an attacker's text "
                        "act as agent instructions (prompt injection)",
                        fix="wrap the external text with fence_untrusted; if the fence is "
                        "applied elsewhere this is a false positive (heuristic)",
                    )
                )
    return findings


_TRUNCATE_RE = re.compile(r"(messages|transcript|journal|history)\s*\[\s*-?\d")
_WALKBACK_RE = re.compile(r"_drop_orphan_tool_results|orphan_tool|walk.?back")


def check_no_naive_transcript_cut(files: list[Path], root: Path) -> list[Finding]:
    """A slice of a messages/transcript/journal list in the compaction modules that doesn't
    reference the orphan-dropping walk-back helper may split a tool-call/result pair.
    Heuristic → WARNING."""
    findings: list[Finding] = []
    targets = {"context_compaction.py", "context_management.py"}
    for f in files:
        if f.name not in targets:
            continue
        src = _read(f)
        if _TRUNCATE_RE.search(src) and not _WALKBACK_RE.search(src):
            findings.append(
                Finding(
                    check="no-naive-transcript-cut",
                    level=WARNING,
                    file=f,
                    line=1,
                    what="a transcript/journal slice appears here without referencing the "
                    "orphan-tool-result walk-back helper",
                    why="cutting between a tool_use and its tool_result makes the next "
                    "provider request malformed (most APIs reject an unpaired tool block)",
                    fix="route truncation through _drop_orphan_tool_results (or the shared "
                    "walk-back helper) so no tool-call/result pair is split",
                )
            )
    return findings


_COMMIT_WATCH_TOKENS = (
    "selfqa-commit-watch",
    "selfqa_commit_watch",
    "commit_watch.",
)
_PERIODIC_KINDS = frozenset({"interval", "cron", "schedule", "timer", "poll"})
_PERIODIC_FIELDS = frozenset(
    {
        "cron",
        "cron_expression",
        "every",
        "every_minutes",
        "interval",
        "interval_minutes",
        "interval_seconds",
        "poll_interval",
        "poll_seconds",
        "schedule",
    }
)


def _docstring_constants(tree: ast.AST) -> set[int]:
    """Node ids of every module/class/function docstring Constant.

    A docstring that narrates the retirement ("the interim cron script retires…") is
    exactly the text the check below must NOT read as evidence of a periodic watcher —
    the retirement notes live in the same modules as the watcher itself.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _names_the_commit_watch(tree: ast.AST, docstrings: set[int]) -> bool:
    return any(
        isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
        and any(tok in n.value for tok in _COMMIT_WATCH_TOKENS)
        for n in ast.walk(tree)
    )


def _periodic_kind_lines(tree: ast.AST) -> list[tuple[int, str]]:
    """Every place this module states a periodic cadence, as (line, what was written).

    Three spellings, because a trigger is built three ways in this codebase: a keyword
    (``Trigger(kind="interval")``), an assignment (``trigger.kind = "cron"``) and a
    literal (``{"kind": "interval"}``). A scheduling FIELD (``interval_minutes=…``,
    ``"cron": …``) counts too — naming a cadence at all is the defect, whatever the
    surrounding key is called.
    """
    hits: list[tuple[int, str]] = []

    def periodic_value(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.lower() in _PERIODIC_KINDS:
                return node.value
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword):
            if node.arg == "kind" and (v := periodic_value(node.value)) is not None:
                hits.append((getattr(node.value, "lineno", 1), f"kind={v!r}"))
            elif node.arg in _PERIODIC_FIELDS:
                hits.append((getattr(node.value, "lineno", 1), f"{node.arg}=..."))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {
                t.attr if isinstance(t, ast.Attribute) else t.id
                for t in targets
                if isinstance(t, (ast.Attribute, ast.Name))
            }
            value = node.value
            if value is None:
                continue
            if "kind" in names and (v := periodic_value(value)) is not None:
                hits.append((getattr(value, "lineno", 1), f"kind = {v!r}"))
            elif names & _PERIODIC_FIELDS:
                hits.append(
                    (
                        getattr(value, "lineno", 1),
                        f"{sorted(names & _PERIODIC_FIELDS)[0]} = ...",
                    )
                )
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if key.value == "kind" and (v := periodic_value(value)) is not None:
                    hits.append((getattr(key, "lineno", 1), f'"kind": {v!r}'))
                elif key.value in _PERIODIC_FIELDS:
                    hits.append((getattr(key, "lineno", 1), f'"{key.value}": ...'))
    return hits


def check_no_periodic_commit_watcher(files: list[Path], root: Path) -> list[Finding]:
    """A module that names the Self-QA commit watch may not also state a periodic cadence.

    The watcher was a cron script on an interval trigger until the ``vcs`` file-watch preset
    landed and took ownership; the script and its interval are retired. Re-introducing a
    clock — a trigger ``kind`` of interval/cron/schedule, or a ``schedule``/``interval_*``/
    ``cron`` field — next to the commit-watch identifier gives the same work two owners and
    brings back the duplicate-run and token-burn shape the retirement removed. Exactly
    derivable from the module's own literals → ERROR.
    """
    findings: list[Finding] = []
    for f in files:
        if f.suffix != ".py" or not _is_under(f, root, "runtime", "gideon"):
            continue
        try:
            tree = ast.parse(_read(f))
        except (SyntaxError, ValueError):
            continue
        docstrings = _docstring_constants(tree)
        if not _names_the_commit_watch(tree, docstrings):
            continue
        for line, wrote in _periodic_kind_lines(tree):
            findings.append(
                Finding(
                    check="no-periodic-commit-watcher",
                    level=ERROR,
                    file=f,
                    line=line,
                    what=f"this module names the Self-QA commit watch and states a "
                    f"periodic cadence here ({wrote})",
                    why="the periodic commit watcher was retired when the vcs (file-watch) "
                    "trigger took ownership of commit deltas; a clock beside the watcher "
                    "gives one job two owners, re-fires runs the vcs trigger already "
                    "started, and re-opens the token burn the retirement closed",
                    fix="drive the commit watch from the vcs trigger preset "
                    '(kind="file" over gideon.automation.triggers.file_watch.'
                    "vcs_patterns) and delete the schedule",
                )
            )
    return findings


_CHECKS = {
    "hook-provider-parity": check_hook_provider_parity,
    "sse-event-registered": check_sse_event_registered,
    "config-four-points": check_config_four_points,
    "app-sdk-boundary": check_app_sdk_boundary,
    "destructive-test-isolation": check_destructive_test_isolation,
    "fence-at-ingestion": check_fence_at_ingestion,
    "no-naive-transcript-cut": check_no_naive_transcript_cut,
    "no-periodic-commit-watcher": check_no_periodic_commit_watcher,
}


def known_checks() -> set[str]:
    """The set of scanner check-ids (validate uses this to resolve rule `scanner:` refs)."""
    return set(_CHECKS)


def scan(
    files: list[Path],
    root: Path | None = None,
    *,
    changed_lines: dict[Path, set[int]] | None = None,
) -> list[Finding]:
    """Run every check over ``files``. Returns all findings (ERRORs and WARNINGs), sorted
    by (level, check, file). ``files`` should be absolute paths.

    ``changed_lines`` (path → set of changed line numbers) is threaded to line-scoped
    heuristic checks so ``run --diff`` advises only on the lines a diff actually touched.
    """
    r = root if root is not None else _repo_root()
    findings: list[Finding] = []
    for name, check in _CHECKS.items():
        if name == "fence-at-ingestion":
            findings.extend(check(files, r, changed_lines=changed_lines))  # type: ignore[call-arg]
        else:
            findings.extend(check(files, r))
    findings.sort(
        key=lambda f: (0 if f.level == ERROR else 1, f.check, str(f.file), f.line)
    )
    return findings
