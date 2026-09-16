"""Supply-chain safety — the shared install-time content scanner.

A single, vendor-neutral ``SkillScanner`` that statically inspects **staged**
(not-yet-live) community content — skills AND apps both carry executable code
(``scripts/``, ``setup`` hooks) and become reachable the moment they install, so
both run through this one gate before anything lands live.

The scanner is **pattern + structural, no LLM on the hot path** (an install must
work offline / deterministically). It returns a :class:`ScanReport` with a
:class:`Verdict`; the *decision* (commit / confirm / refuse) is the caller's,
modulated by the source's trust tier. ``dangerous`` is the load-bearing floor:
reserved for high-confidence malice (exfil-to-remote, destructive-root,
obfuscated-exec) so a calculated ``warning`` stays overridable but outright
malware never is.

Reuses ``history._SENSITIVE_TOOL_PATTERNS`` (the credential/secret path set) so
"reads ~/.aws" detection has one source of truth. Not a sandbox — static
inspection only; it reduces risk, it does not contain execution.

The DANGEROUS band is additionally scoped by EXECUTION REACHABILITY (issues #2526,
#2625) — the "Execution reachability" section below states the rule in full. In short: a
match the bundle cannot execute is *disclosed* as a ``warning`` rather than refused
outright, because it is a string (or a comment), not a payload. Default-deny throughout —
a match stays DANGEROUS unless inertness is PROVED, and proved structurally from the AST
and the tokeniser rather than from the text, so the failure mode is a false block and
never a false pass.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
import unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

from gideon.cognition.history import _SENSITIVE_TOOL_PATTERNS
from gideon.security.signing import SignatureInfo


class Verdict(str, Enum):
    """Scan outcome, ascending severity. ``dangerous`` is non-overridable."""

    CLEAN = "clean"
    LOW = "low"
    WARNING = "warning"
    DANGEROUS = "dangerous"

    @property
    def rank(self) -> int:
        return {"clean": 0, "low": 1, "warning": 2, "dangerous": 3}[self.value]


class TrustTier(str, Enum):
    BUILTIN = "builtin"  # shipped with Gideon — scan advisory-only (cap at low)
    OFFICIAL = "official"
    TRUSTED = "trusted"
    COMMUNITY = "community"


class Reachability(str, Enum):
    """Whether the code a DANGEROUS match sits in can EXECUTE that match.

    FIVE states, not two, and that is the point. "We could not tell" must never render
    as "safe": a file the analysis could not read is reported as its own outcome
    (:data:`UNPARSEABLE`) and treated exactly like :data:`REACHABLE`, so an unreadable
    file can never be mistaken for a clean one by a reviewer reading the report.

    :data:`COMMENTARY` and :data:`UNREACHABLE` are kept apart for the same reason, in the
    other direction: they are proved by different evidence (a token class vs. five
    clauses over the whole bundle) and both land on WARNING, so a reviewer can tell
    "this is a comment" from "this is a literal nothing reaches".
    """

    NOT_ANALYSED = "not_analysed"
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    COMMENTARY = "commentary"
    UNPARSEABLE = "unparseable"


@dataclass
class Finding:
    """One matched signal. ``severity`` is this finding's own classification;
    the report's verdict is the max across findings (after tier modulation).

    ``reachability`` records what the execution-reachability pass concluded about this
    match and ``reachability_reason`` the sentence a reviewer needs to check it. Both are
    disclosure, never a softener: a finding re-scored to WARNING keeps its rule, path,
    surface and evidence byte-for-byte, so the user is still told and still consents."""

    surface: str
    severity: Verdict
    rule: str
    path: str
    evidence: str
    reachability: Reachability = Reachability.NOT_ANALYSED
    reachability_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "severity": self.severity.value,
            "rule": self.rule,
            "path": self.path,
            "evidence": self.evidence,
            "reachability": self.reachability.value,
            "reachability_reason": self.reachability_reason,
        }


@dataclass
class ScanReport:
    """The gate's output. ``verdict`` is the decision input; ``findings`` is the
    evidence the install UX surfaces ("community skill — 2 warnings").

    ``signature`` is SECURITY-HARDENING contract C2 — the artifact-signature state of
    the bundle this report describes (``signed`` / ``unsigned`` / ``invalid`` + signer).
    It is set by the install gate that verified the staged tree, NOT by the scanner: the
    scanner is content inspection, signing is provenance, and conflating them would let
    a signed bundle's verdict be read as authenticity or vice-versa. Its default is
    ``unsigned``, which is the honest answer for a report produced by a path that never
    looked (skill installs today) — never a claimed-but-unchecked ``signed``."""

    verdict: Verdict = Verdict.CLEAN
    findings: list[Finding] = field(default_factory=list)
    surfaces_scanned: list[str] = field(default_factory=list)
    tier: TrustTier = TrustTier.COMMUNITY
    signature: SignatureInfo = field(default_factory=SignatureInfo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "tier": self.tier.value,
            "findings": [f.to_dict() for f in self.findings],
            "surfaces_scanned": list(self.surfaces_scanned),
            "signature": self.signature.to_dict(),
        }

    @property
    def is_dangerous(self) -> bool:
        return self.verdict is Verdict.DANGEROUS


_DESTRUCTIVE_TARGET_END = r"""(?:\s|;|["'`)\\]|$)"""

_DANGEROUS_SCRIPT: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "destructive_root",
        re.compile(
            r"\brm\s+-[rf]{1,2}\s+(?:-[rf]{1,2}\s+)*(?:/|~|\$HOME|\*)"
            + _DESTRUCTIVE_TARGET_END
        ),
    ),
    ("fork_bomb", re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    (
        "disk_wipe",
        re.compile(
            r"(?:\bmkfs\.\w+|\bdd\s+[^\n]*\bof=/dev/(?:sd|nvme|disk)|>\s*/dev/(?:sd|nvme|disk))"
        ),
    ),
    (
        "remote_exec_pipe",
        re.compile(r"\b(?:curl|wget|fetch)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"),
    ),
    (
        "obfuscated_exec",
        re.compile(r"base64\s+(?:--decode|-d|-D)\b[^\n|]*\|\s*(?:ba|z|da)?sh\b"),
    ),
)

_NATIVE_DESTRUCTION_RULES: tuple[str, ...] = (
    "destructive_delete",
    "destructive_walk",
    "destructive_truncate",
)

_WARNING_SCRIPT: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("eval_exec", re.compile(r"\beval\s*[\"'(]")),
    (
        "pipe_to_shell",
        re.compile(r"\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"),
    ),
    ("curl_network", re.compile(r"\b(?:curl|wget)\b")),
    ("sudo_use", re.compile(r"\bsudo\b")),
    (
        "python_exec",
        re.compile(r"\b(?:os\.system|subprocess\.(?:call|run|Popen)|exec\(|eval\()"),
    ),
    ("crontab_write", re.compile(r"\bcrontab\b")),
)

_INJECTION_PROSE: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "injection_ignore",
        re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I),
    ),
    (
        "injection_disregard",
        re.compile(r"disregard\s+(?:the\s+)?(?:above|prior|system)", re.I),
    ),
    (
        "injection_coerce",
        re.compile(r"you\s+must\s+(?:now\s+)?(?:run|execute|call|always)", re.I),
    ),
    ("injection_override", re.compile(r"(?:new|updated)\s+system\s+prompt\s*:", re.I)),
)

_SCRIPT_EXTS = {
    ".sh",
    ".bash",
    ".zsh",
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".rb",
    ".pl",
    ".ps1",
}
_MANIFEST_NAMES = {"skill.md", "app.json", "readme.md", "manifest.json"}
_MAX_FILE_BYTES = 512 * 1024
_EVIDENCE_CAP = 120
# How the evidence window is SHAPED (the cap above is the only length bound). The
# window is anchored at the start of the matched line — so a call brings its callee
# and what it is assigned to — and, when the construct opens a bracket, walks to the
# matching close bracket so the ARGUMENTS come along. A window that merely hugged the
# matched token showed `subprocess.run(  # noqa: S603` and cut the argv off on the
# next line: the reader was warned about a subprocess and shown everything except
# what it runs. These two bound that walk on a pathological or unbalanced file.
_EVIDENCE_MAX_LINES = 8
_EVIDENCE_SCAN_CHARS = 600
_EVIDENCE_LEAD_CAP = 40
_ELLIPSIS = "…"
_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
}


SCAN_RULE_GLOSS_PATH = Path(__file__).with_name("scan_rule_gloss.json")


@lru_cache(maxsize=1)
def load_scan_rule_gloss() -> dict[str, str]:
    """Every rule name mapped to one sentence about what the content can then do.

    RAISES rather than returning ``{}`` when the packaged file is missing or carries no
    rules. An empty map is indistinguishable at the call site from "no rule is glossed",
    which is the exact silence this map exists to remove — so a wheel that lost the file
    fails loudly on the refusal path instead of quietly printing bare rows again. Same
    posture as the baseline denylist: a missing packaged data file is a defect, not a
    degradation.

    Underscore keys are metadata (the file's ``_comment`` rationale — JSON cannot hold
    comments), never rules; the same convention as ``apps/token_lint_rules.json``."""
    try:
        raw = json.loads(SCAN_RULE_GLOSS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"{SCAN_RULE_GLOSS_PATH} is missing or unreadable: {exc}"
        ) from exc
    gloss = {
        k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)
    }
    if not gloss:
        raise RuntimeError(
            f"{SCAN_RULE_GLOSS_PATH} carries no rule glosses — a reachable-but-empty map "
            "would silently un-explain every finding on every consent surface"
        )
    return gloss


def rule_gloss(rule: str) -> str:
    """The sentence for ``rule``, or ``''`` for a rule this build has no gloss for.

    Returning empty rather than echoing the rule name keeps the row honest: a name
    repeated as though it were an explanation is the defect, not the fix. Mirrors
    ``ruleGloss()`` in ``web/src/lib/scanFindings.ts`` exactly — same file, same answer.
    """
    return load_scan_rule_gloss().get(rule, "")


def _sensitive_path_pattern() -> "re.Pattern[str]":
    """A regex matching any credential/secret path from the shared set."""
    alts = "|".join(re.escape(p) for p in _SENSITIVE_TOOL_PATTERNS)
    return re.compile(alts)


_SENSITIVE_RE = _sensitive_path_pattern()

_NET_EGRESS_RE = re.compile(r"\b(?:curl|wget|fetch|nc|ncat|/dev/tcp)\b")

_EXFIL_PROXIMITY_LINES = 3


def _strip_line_comments(text: str) -> str:
    """Blank out full-line ``#``/``//`` comments so tokens that appear only in
    commentary don't trip the co-occurrence heuristics — a comment never executes.
    Conservative: only blanks lines whose first non-space char starts the comment,
    so it never touches ``${VAR#x}``, ``https://…``, or trailing inline comments.
    Line count is preserved so evidence line math stays honest."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.lstrip()
        out.append("" if (s.startswith("#") or s.startswith("//")) else line)
    return "\n".join(out)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos)


_INVISIBLE_CHARS = {
    "​",
    "‌",
    "‍",
    "⁠",
    "﻿",
    "‪",
    "‫",
    "‬",
    "‭",
    "‮",
    "⁦",
    "⁧",
    "⁨",
    "⁩",
}


_OPEN_BRACKETS = {"(": ")", "[": "]", "{": "}"}
_CLOSE_BRACKETS = frozenset(")]}")


def _unquoted_hash(line: str) -> int:
    """Index of the first ``#`` in ONE line that starts a comment, else -1. Quote-aware,
    so a ``#`` inside a string (a URL fragment, a colour escape) is not read as one."""
    quote = ""
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "#":
            return i
        i += 1
    return -1


def _drop_trailing_comment(line: str) -> str:
    """Trim a trailing ``#`` comment off ONE line of an evidence window. A lint pragma
    is commentary ABOUT the code, not the code, and inside a capped window it crowds
    out the substance. A comment-ONLY line is kept verbatim: there the comment is all
    there is to show, and it may be the matched text itself."""
    h = _unquoted_hash(line)
    if h < 0 or not line[:h].strip():
        return line
    return line[:h].rstrip()


def _construct_end(text: str, match: "re.Match[str]") -> tuple[int, bool]:
    """Where the construct the match names ends, and whether that end is a CUT.

    For a call this is the closing bracket of the argument list, so the argv travels
    with the callee; otherwise it is the end of the matched line. ``clipped`` is True
    when the walk hit its line/char budget before the bracket closed, so the caller
    marks the snippet truncated rather than passing a fragment off as the whole call."""
    line_end = text.find("\n", match.end())
    line_end = len(text) if line_end < 0 else line_end
    i = (
        match.end() - 1
        if text[match.end() - 1 : match.end()] in _OPEN_BRACKETS
        else match.end()
    )
    while i < len(text) and text[i] in " \t":
        i += 1
    if text[i : i + 1] not in _OPEN_BRACKETS:
        return line_end, False
    limit = min(len(text), match.end() + _EVIDENCE_SCAN_CHARS)
    lines_left = _EVIDENCE_MAX_LINES
    depth = 0
    quote = ""
    j = i
    while j < limit:
        c = text[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "#":
            nl = text.find("\n", j)
            j = len(text) if nl < 0 else nl
            continue
        elif c in _OPEN_BRACKETS:
            depth += 1
        elif c in _CLOSE_BRACKETS:
            depth -= 1
            if depth <= 0:
                return j + 1, False
        elif c == "\n":
            lines_left -= 1
            if lines_left <= 0:
                return j, True
        j += 1
    return limit, True


def _evidence(text: str, match: "re.Match[str]") -> str:
    """The user-facing snippet for one finding: ``L<line>: <code>``.

    Shaped so a reader can tell WHAT the flagged code does — for a call, the callee
    AND its arguments, not just the token that matched — and where to find it. Folded
    to one line because the consent dialog renders it inline (a literal ``\\n`` in a
    security disclosure is noise, not a newline), and any cut is marked with ``…``.
    This decides only how much surrounding code is SHOWN; it never changes what
    matched or whether a rule fired."""
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = f"L{_line_of(text, match.start()) + 1}: "
    end, clipped = _construct_end(text, match)
    head_raw = text[line_start : match.start()]
    body_raw = text[match.start() : max(end, match.end())]
    if _unquoted_hash(head_raw) < 0:
        body_raw = "\n".join(_drop_trailing_comment(ln) for ln in body_raw.split("\n"))
    head = " ".join(head_raw.split())
    body = " ".join(body_raw.split())
    sep = " " if head and head_raw[-1:].isspace() else ""
    if len(head) > _EVIDENCE_LEAD_CAP:
        head = _ELLIPSIS + head[-(_EVIDENCE_LEAD_CAP - 1) :]
    room = _EVIDENCE_CAP - len(prefix) - len(head) - len(sep)
    if len(body) > room or clipped:
        body = body[: max(0, room - 1)].rstrip() + _ELLIPSIS
    return f"{prefix}{head}{sep}{body}".rstrip()


_SHELL_ARGV0 = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "fish",
        "ash",
        "csh",
        "tcsh",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "env",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "deno",
        "bun",
        "php",
        "awk",
        "sed",
        "osascript",
        "xargs",
        "eval",
    }
)

_EXEC_ATTRS = frozenset(
    {
        "system",
        "popen",
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnv",
        "spawnve",
        "spawnvp",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
        "spawn",
    }
)

_DYNAMIC_BUILTINS = frozenset({"eval", "exec", "compile", "__import__"})

_DYNAMIC_MODULES = frozenset(
    {"importlib", "runpy", "pickle", "marshal", "dill", "ctypes", "cffi", "imp"}
)

_INERT_TOP_LEVEL_CALLS = frozenset(
    {
        "Path",
        "Path(__file__).resolve",
        "os.path.dirname",
        "os.path.abspath",
        "os.path.join",
        "os.environ.get",
        "os.getenv",
        "re.compile",
        "frozenset",
        "set",
        "dict",
        "list",
        "tuple",
        "len",
        "sorted",
        "range",
        "logging.getLogger",
        "shutil.which",
        "pytest.mark.skipif",
        "pytest.importorskip",
    }
)

_SPAWN_FUNCS = ("subprocess.", "asyncio.create_subprocess_")

_SUBPROCESS_NON_SPAWN = frozenset(
    {
        "TimeoutExpired",
        "CalledProcessError",
        "SubprocessError",
        "PIPE",
        "STDOUT",
        "DEVNULL",
    }
)

_LOADER_SUFFIXES = frozenset(
    {
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".cmd",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".rb",
        ".pl",
        ".php",
        ".lua",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".cfg",
        ".ini",
        ".mk",
    }
)
_LOADER_NAMES = frozenset(
    {"Makefile", "makefile", "Dockerfile", "Procfile", "justfile"}
)

_REACH_FLOOR = Verdict.WARNING

_INERT_STATES = frozenset({Reachability.UNREACHABLE, Reachability.COMMENTARY})


@dataclass
class _FileFacts:
    """What one Python file's AST says about executing strings and reaching modules."""

    parsed: bool
    str_spans: list[tuple[int, int]]
    commentary_spans: list[tuple[int, int]]
    imports: set[str]
    string_literals: set[str]
    dynamic: set[str]
    sinks: set[str]
    top_level_calls: set[str]


def _offset_table(text: str) -> list[int]:
    """Line-start offsets, so an AST or :mod:`tokenize` ``(row, col)`` becomes a flat
    offset.

    Split on the UNIVERSAL NEWLINES CPython itself recognises — ``\\n``, ``\\r\\n``,
    ``\\r`` — and nothing else. Deliberately not :meth:`str.splitlines`, which also breaks
    on ``\\f`` and ``\\u2028``: CPython's tokeniser treats those as ordinary characters, so
    a file containing one would produce a table that disagreed with the row numbers being
    looked up in it, by an amount the file's author chooses. That is fine for a heuristic
    and not fine for a table a span check stands on.
    """
    starts = [0]
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\r":
            i += 2 if text[i + 1 : i + 2] == "\n" else 1
        elif c == "\n":
            i += 1
        else:
            i += 1
            continue
        starts.append(i)
    return starts


def _docstring_nodes(tree: ast.AST) -> Iterator[ast.Constant]:
    """Every module/class/function docstring node — the ``str`` the compiler lifts into
    ``__doc__`` and never evaluates as an expression."""
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            yield first.value


def _commentary_spans(text: str, tree: ast.AST) -> list[tuple[int, int]] | None:
    """L0: text offsets of every region of ``text`` the interpreter throws away — each
    ``COMMENT`` token, and each docstring — or ``None`` when that cannot be DETERMINED.

    ``None`` is not "no comments". It means the file did not tokenise, or a span the
    lexer reported did not match the bytes it claims to cover, and the caller must then
    treat the file as unanalysable: the entire claim L0 makes is that the determination is
    structural, so a file whose structure could not be read gets no benefit from it.

    Every span is verified against the token's own text before it is trusted, so a line
    table that drifted from the lexer for any reason revokes the whole answer rather than
    silently pointing a "this is a comment" span at some other part of the file.
    """
    starts = _offset_table(text)

    def span(tok: tokenize.TokenInfo) -> tuple[int, int] | None:
        try:
            start = starts[tok.start[0] - 1] + tok.start[1]
            end = starts[tok.end[0] - 1] + tok.end[1]
        except IndexError:
            return None
        return (start, end) if text[start:end] == tok.string else None

    spans: list[tuple[int, int]] = []
    strings: dict[int, list[tokenize.TokenInfo]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text, newline="").readline):
            if tok.type == tokenize.COMMENT:
                got = span(tok)
                if got is None:
                    return None
                spans.append(got)
            elif tok.type == tokenize.STRING:
                strings.setdefault(tok.start[0], []).append(tok)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None
    for node in _docstring_nodes(tree):
        row = strings.get(node.lineno, [])
        if len(row) != 1:
            continue
        try:
            if ast.literal_eval(row[0].string) != node.value:
                continue
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        got = span(row[0])
        if got is not None:
            spans.append(got)
    return spans


def _within(
    regions: Iterable[tuple[int, int]], probes: Iterable[tuple[int, int]]
) -> bool:
    """True when EVERY probe span lies wholly inside at least one region. Vacuously true
    for no probes, which is why every caller checks it has probes first."""
    regions = list(regions)
    return all(
        any(s <= start and end <= e for s, e in regions) for start, end in probes
    )


def _argv0_of(call: ast.Call) -> tuple[bool, str | None]:
    """``(pinned, program)`` for a spawn site's argv[0].

    ``pinned`` is False whenever the program name is not a plain string literal — the
    default-deny answer, because an argv assembled at runtime can name anything. A bare
    command STRING rather than a list is never pinned either: that form is only ever run
    through a shell."""
    if not call.args:
        return False, None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return False, first.value
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return True, head.value
    return False, None


def _spawn_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """``(prefixes, bare_names)`` under which this file can reach a spawn API.

    An import alias must not be a way out: ``import subprocess as sp`` and
    ``from subprocess import run`` are the same capability as ``subprocess.run``, spelled
    so a prefix match on the literal text ``"subprocess."`` would miss it. Collected in
    its own pass so the answer does not depend on AST walk order.
    """
    prefixes: set[str] = set(_SPAWN_FUNCS)
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "subprocess" and alias.asname:
                    prefixes.add(f"{alias.asname}.")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in {"subprocess", "asyncio"}:
                continue
            for alias in node.names:
                if alias.name in _SUBPROCESS_NON_SPAWN:
                    continue
                if root == "asyncio" and not alias.name.startswith(
                    "create_subprocess_"
                ):
                    continue
                bare.add(alias.asname or alias.name)
    return prefixes, bare


def _note_call(
    node: ast.Call, facts: _FileFacts, spawn_prefixes: set[str], spawn_bare: set[str]
) -> None:
    """Record what one call site means for L2 (can it run a string?) and L4."""
    name = ast.unparse(node.func)
    if name == "getattr" and (
        len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)
    ):
        facts.dynamic.add("getattr with a computed name")
    if isinstance(node.func, ast.Name) and node.func.id in _EXEC_ATTRS:
        facts.sinks.add(f"{node.func.id}()")
        return
    if name.startswith("subprocess.") and name.split(".")[-1] in _SUBPROCESS_NON_SPAWN:
        return
    if not (name.startswith(tuple(sorted(spawn_prefixes))) or name in spawn_bare):
        return
    if name.endswith("create_subprocess_shell"):
        facts.sinks.add(name)
        return
    shell = next((kw.value for kw in node.keywords if kw.arg == "shell"), None)
    if shell is not None and not (
        isinstance(shell, ast.Constant) and shell.value is False
    ):
        facts.sinks.add(f"{name}(shell=…)")
        return
    pinned, program = _argv0_of(node)
    if not pinned:
        facts.sinks.add(f"{name} with an argv the source does not pin")
        return
    leaf = (program or "").replace("\\", "/").rsplit("/", 1)[-1]
    if (
        "/" in (program or "")
        or leaf in _SHELL_ARGV0
        or leaf.endswith((".sh", ".bash", ".py"))
    ):
        facts.sinks.add(f"{name} spawning {program!r}")


def _analyse_python(text: str) -> _FileFacts:
    """AST facts for one Python file. An unparseable file returns ``parsed=False`` and
    nothing else — the caller must treat that as its own outcome, never as "no sinks".
    """
    unreadable = _FileFacts(False, [], [], set(), set(), set(), set(), set())
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return unreadable
    commentary = _commentary_spans(text, tree)
    if commentary is None:
        return unreadable
    facts = _FileFacts(True, [], commentary, set(), set(), set(), set(), set())

    starts = _offset_table(text)
    spawn_prefixes, spawn_bare = _spawn_names(tree)

    def off(lineno: int, col: int) -> int:
        return starts[lineno - 1] + col

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            facts.string_literals.add(node.value)
            if node.end_lineno is not None and node.end_col_offset is not None:
                facts.str_spans.append(
                    (
                        off(node.lineno, node.col_offset),
                        off(node.end_lineno, node.end_col_offset),
                    )
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                facts.imports.update({root, alias.name})
                if root in _DYNAMIC_MODULES:
                    facts.dynamic.add(f"imports {root}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            facts.imports.add(root)
            for alias in node.names:
                facts.imports.add(alias.name)
                if alias.name == "*":
                    facts.dynamic.add("star import")
            if root in _DYNAMIC_MODULES:
                facts.dynamic.add(f"imports {root}")
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC_BUILTINS:
            facts.dynamic.add(f"{node.id}()")
            facts.sinks.add(f"{node.id}()")
        elif isinstance(node, ast.Attribute):
            if node.attr in _EXEC_ATTRS:
                facts.sinks.add(f".{node.attr}")
            if (
                node.attr == "path"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
            ):
                facts.dynamic.add("sys.path mutation")
        if isinstance(node, ast.Call):
            _note_call(node, facts, spawn_prefixes, spawn_bare)

    for stmt in tree.body:
        if isinstance(
            stmt,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Import,
                ast.ImportFrom,
            ),
        ):
            continue
        for inner in ast.walk(stmt):
            if isinstance(inner, ast.Call):
                facts.top_level_calls.add(ast.unparse(inner.func))

    return facts


def _is_loader_name(name: str) -> bool:
    """True for a non-Python file that could load or run code (and so counts under L3)."""
    return Path(name).suffix.lower() in _LOADER_SUFFIXES or name in _LOADER_NAMES


def _manifest_tokens(manifest_text: str | None) -> set[str]:
    """Every module-ish token ``app.json`` names, so a DECLARED entry point is never
    mistaken for an unreferenced file just because no ``import`` statement mentions it.
    An unreadable manifest yields ``{"*"}`` — assume every file is named (default-deny).
    """
    if manifest_text is None:
        return set()
    try:
        data: Any = json.loads(manifest_text)
    except ValueError:
        return {"*"}
    out: set[str] = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]*", json.dumps(data)):
        out.update({token, token.split(":")[0], Path(token).stem})
    return out


class _BundleReach:
    """The per-bundle reachability analysis: built once from the texts the scan already
    read, then queried per DANGEROUS finding.

    Built from the walk's own bytes rather than by re-reading the tree, so the analysis
    answers for exactly the content the scanner judged — there is no second read for a
    payload to change under.
    """

    def __init__(
        self,
        *,
        py_texts: dict[str, str],
        loader_blobs: Iterable[str],
        manifest_text: str | None,
        opaque: Iterable[str],
    ) -> None:
        self._py_texts = py_texts
        self._facts = {rel: _analyse_python(text) for rel, text in py_texts.items()}
        self._loader_blob = "\n".join(loader_blobs)
        self._manifest = _manifest_tokens(manifest_text)
        self._untrustworthy: str | None = next(
            (f"{rel}: too large or unreadable to analyse" for rel in sorted(opaque)),
            None,
        )
        if self._untrustworthy is None:
            self._untrustworthy = next(
                (
                    f"{rel}: {sorted(f.dynamic)[0]}"
                    for rel, f in sorted(self._facts.items())
                    if f.dynamic
                ),
                None,
            )

    def _referenced_elsewhere(self, rel: str) -> str | None:
        stem = Path(rel).stem
        name = Path(rel).name
        for other, facts in sorted(self._facts.items()):
            if other == rel:
                continue
            if stem in facts.imports:
                return f"{other} imports it"
            if stem in facts.string_literals or rel in facts.string_literals:
                return f"{other} names it in a string"
            if name in facts.string_literals:
                return f"{other} names it in a string"
        if "*" in self._manifest:
            return "app.json is unreadable, so every file is assumed declared"
        if stem in self._manifest:
            return "app.json names it"
        if name in self._loader_blob or f"{stem} " in self._loader_blob:
            return "a script or config in the bundle names it"
        return None

    def decide(self, finding: Finding) -> tuple[Reachability, str]:
        """L0-L5 for one DANGEROUS finding, plus the sentence that justifies it."""
        if finding.rule in _NATIVE_DESTRUCTION_RULES:
            return (
                Reachability.REACHABLE,
                "match is a call site resolved from the AST, not a string the bundle "
                "merely holds — a call is code (L1)",
            )
        rel = finding.path
        if not rel.endswith(".py"):
            return (
                Reachability.REACHABLE,
                "not Python — a script's text is its program (L1)",
            )
        facts = self._facts.get(rel)
        text = self._py_texts.get(rel)
        if facts is None or text is None:
            return Reachability.REACHABLE, "file was not read as Python (L1)"
        if not facts.parsed:
            return Reachability.UNPARSEABLE, "file does not parse as Python (L1)"
        spans = _rule_spans(text, finding.rule)
        if not spans:
            return Reachability.REACHABLE, "rule has no locatable span (L1)"
        if _within(facts.commentary_spans, spans):
            return (
                Reachability.COMMENTARY,
                "every match is inside a comment or docstring, which the interpreter "
                "discards at tokenisation — not code (L0)",
            )
        if not _within([*facts.str_spans, *facts.commentary_spans], spans):
            return Reachability.REACHABLE, "match is code, not a string literal (L1)"
        if facts.sinks:
            return (
                Reachability.REACHABLE,
                f"file can execute a string: {sorted(facts.sinks)[0]} (L2)",
            )
        exported = self._referenced_elsewhere(rel)
        if exported:
            return Reachability.REACHABLE, f"literal is reachable — {exported} (L3)"
        if self._untrustworthy:
            return (
                Reachability.REACHABLE,
                f"bundle can rewrite its own graph — {self._untrustworthy} (L4)",
            )
        impure = sorted(facts.top_level_calls - _INERT_TOP_LEVEL_CALLS)
        if impure:
            return Reachability.REACHABLE, f"module runs {impure[0]} on import (L5)"
        return (
            Reachability.UNREACHABLE,
            "inert literal: not code, this file cannot execute a string, nothing in the "
            "bundle reaches it, and importing it runs nothing (L1-L5)",
        )


def _rule_spans(text: str, rule: str) -> list[tuple[int, int]]:
    """Every span in ``text`` this rule could have reported, re-derived by re-running the
    rule's own regex.

    ALL of them, not just the one the finding names. ``_scan_script`` reports the first
    match per rule, but a second match of the same rule sitting in CODE means the file
    really can execute that shape — so L1 must see every candidate or it would clear a
    file on the strength of its most innocent occurrence.

    ``exfil_sensitive_path`` is derived from two regexes over comment-stripped text. The
    span that matters for "is this a literal" is the credential path, located here in the
    RAW text so the offsets line up with the AST; matches on a full-line comment are
    dropped because ``_scan_script`` strips those before the rule can see them, so they
    are not candidates for this finding at all.
    """
    if rule == "exfil_sensitive_path":
        return [
            (m.start(), m.end())
            for m in _SENSITIVE_RE.finditer(text)
            if not _on_comment_only_line(text, m.start())
        ]
    for name, pattern in _DANGEROUS_SCRIPT:
        if name == rule:
            return [(m.start(), m.end()) for m in pattern.finditer(text)]
    return []


def _on_comment_only_line(text: str, pos: int) -> bool:
    """True when ``pos`` sits on a line whose first non-space char opens a comment — the
    same predicate :func:`_strip_line_comments` uses, evaluated on raw offsets."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    stripped = text[start : len(text) if end < 0 else end].lstrip()
    return stripped.startswith("#") or stripped.startswith("//")


def _scope_by_reachability(
    findings: list[Finding],
    *,
    py_texts: dict[str, str],
    loader_blobs: list[str],
    manifest_text: str | None,
    opaque: list[str],
) -> list[Finding]:
    """Annotate every DANGEROUS script finding with its execution reachability, and
    re-score the provably-inert ones — :data:`_INERT_STATES` — to :data:`_REACH_FLOOR`
    (WARNING).

    Order and count are preserved exactly; rule, path, surface and evidence are never
    touched. Nothing is ever raised and nothing is ever dropped — the only edit is
    DANGEROUS → WARNING on positive proof, which keeps the finding on the consent surface.

    The analysis is built only when there is a DANGEROUS script finding to ask about, so a
    clean bundle pays nothing, and once per scan rather than once per finding.
    """
    if not any(
        f.severity is Verdict.DANGEROUS and f.surface == "script" for f in findings
    ):
        return findings
    reach = _BundleReach(
        py_texts=py_texts,
        loader_blobs=loader_blobs,
        manifest_text=manifest_text,
        opaque=opaque,
    )
    out: list[Finding] = []
    for finding in findings:
        if finding.severity is not Verdict.DANGEROUS or finding.surface != "script":
            out.append(finding)
            continue
        state, reason = reach.decide(finding)
        severity = _REACH_FLOOR if state in _INERT_STATES else finding.severity
        out.append(
            replace(
                finding,
                severity=severity,
                reachability=state,
                reachability_reason=reason,
            )
        )
    return out


_HOME_LITERALS = frozenset({"~", "~/", "~/.", "$HOME", "${HOME}", "%USERPROFILE%"})
_HOME_ENV_KEYS = frozenset({"HOME", "USERPROFILE"})
_DRIVE_ROOT_RE = re.compile(r"[A-Za-z]:[\\/]{0,2}")

_SYSTEM_TREES: tuple[str, ...] = (
    "/Applications",
    "/Library",
    "/System",
    "/Users",
    "/Volumes",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib64",
    "/opt",
    "/private/etc",
    "/private/var",
    "/proc",
    "/root",
    "/sbin",
    "/srv",
    "/sys",
    "/usr",
    "/var",
)
_TRANSIENT_PATHS: tuple[str, ...] = (
    "/dev/fd",
    "/dev/null",
    "/dev/random",
    "/dev/shm",
    "/dev/stderr",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/tty",
    "/dev/urandom",
    "/dev/zero",
    "/private/tmp",
    "/private/var/folders",
    "/private/var/tmp",
    "/tmp",
    "/var/folders",
    "/var/tmp",
)

_PATH_PASSTHROUGH = frozenset(
    {
        "os.fspath",
        "os.path.abspath",
        "os.path.expanduser",
        "os.path.expandvars",
        "os.path.normpath",
        "os.path.realpath",
        "pathlib.Path",
        "pathlib.PosixPath",
        "pathlib.PurePath",
        "pathlib.PurePosixPath",
        "str",
    }
)
_PATH_PASSTHROUGH_METHODS = frozenset({"absolute", "expanduser", "resolve"})
_HOME_FUNCS = frozenset({"pathlib.Path.home", "pathlib.PurePath.home", "Path.home"})
_ENV_READ_FUNCS = frozenset({"os.environ.get", "os.getenv"})

_DELETE_FUNCS = frozenset(
    {"os.remove", "os.removedirs", "os.rmdir", "os.unlink", "shutil.rmtree"}
)
_DELETE_METHODS = frozenset({"removedirs", "rmdir", "rmtree", "unlink"})

_OPEN_FUNCS = frozenset({"codecs.open", "io.open", "open"})
_TRUNCATE_FUNCS = frozenset({"os.truncate"})
_TRUNCATE_METHODS = frozenset({"write_bytes", "write_text"})

_WALK_FUNCS = frozenset({"os.fwalk", "os.walk"})
_WALK_METHODS = frozenset({"rglob", "walk"})
_GLOB_FUNCS = frozenset({"glob.glob", "glob.iglob"})

_TARGET_DEPTH = 6


def _longest_root(path: str, roots: tuple[str, ...]) -> str | None:
    """The longest entry of ``roots`` that ``path`` equals or lies under, else ``None``.

    Compared on path SEGMENTS (``/etc`` matches ``/etc`` and ``/etc/hosts``, never
    ``/etcetera``), and longest-wins so a carve-out can be nested inside a tree —
    ``/var/folders`` has to be able to beat ``/var``."""
    hit: str | None = None
    for root in roots:
        if (path == root or path.startswith(f"{root}/")) and (
            hit is None or len(root) > len(hit)
        ):
            hit = root
    return hit


def _classify_path_literal(raw: str) -> str | None:
    """``"root"`` | ``"home"`` | ``"system"`` for a destructive target, else ``None``.

    THE PRECISION LINE for the whole native family, and the same line the shell band already
    draws. ``..`` segments are resolved lexically rather than refused, because ``/etc/..``
    IS the root and a rule that could not see that would be defeated by two characters.
    """
    if raw in _HOME_LITERALS:
        return "home"
    if _DRIVE_ROOT_RE.fullmatch(raw):
        return "root"
    if not raw.startswith("/"):
        return None
    parts: list[str] = []
    for segment in raw.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    norm = "/" + "/".join(parts)
    if norm == "/":
        return "root"
    transient = _longest_root(norm, _TRANSIENT_PATHS)
    system = _longest_root(norm, _SYSTEM_TREES)
    if transient is not None and (system is None or len(transient) >= len(system)):
        return None
    return "system" if system is not None else None


def _dotted_name(node: ast.expr) -> str | None:
    """The dotted name a ``Name``/``Attribute`` chain spells, or ``None`` for anything else.

    Built by hand rather than with :func:`ast.unparse` so the answer is ``None`` — not a
    string that merely looks like a name — for `f().attr`, a subscript, or a literal."""
    parts: list[str] = []
    cursor: ast.expr = node
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if not isinstance(cursor, ast.Name):
        return None
    parts.append(cursor.id)
    return ".".join(reversed(parts))


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """Every local name mapped to the canonical dotted path it refers to.

    ``import shutil as sh`` → ``{"sh": "shutil"}``; ``from shutil import rmtree as rt`` →
    ``{"rt": "shutil.rmtree"}``. Without this the rules would read ``shutil.rmtree`` and
    miss ``rt``, which is one line of evasion. Relative imports are skipped: a
    bundle-local module is not the stdlib no matter what it re-exports, and pretending to
    resolve it would put a name in the map that means something else."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                out[alias.asname or root] = alias.name if alias.asname else root
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                out[alias.asname or alias.name] = (
                    f"{module}.{alias.name}" if module else alias.name
                )
    return out


def _target_names(node: ast.expr) -> Iterator[str]:
    """Every name an assignment target binds, through tuple/list/star unpacking."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Starred):
        yield from _target_names(node.value)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _target_names(element)


def _bound_names(node: ast.AST) -> Iterator[str]:
    """Every name ONE node binds, by any form Python has for binding one.

    Exhaustive on purpose: this feeds the "bound exactly once" count, and a binding form
    left out of it would let a name that really does change be resolved as if it could not.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            yield from _target_names(target)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        yield from _target_names(node.target)
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        yield from _target_names(node.target)
    elif isinstance(node, ast.withitem):
        if node.optional_vars is not None:
            yield from _target_names(node.optional_vars)
    elif isinstance(node, ast.Delete):
        for target in node.targets:
            yield from _target_names(target)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            yield alias.asname or alias.name.split(".")[0]
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            yield node.name
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        yield from node.names
    elif isinstance(node, ast.arguments):
        for arg in (*node.posonlyargs, *node.args, *node.kwonlyargs):
            yield arg.arg
        for maybe in (node.vararg, node.kwarg):
            if maybe is not None:
                yield maybe.arg


def _single_bindings(tree: ast.AST) -> dict[str, ast.expr]:
    """Names bound EXACTLY ONCE in the file, mapped to the expression bound to them.

    The only dataflow this family does, and the weakest claim that closes the obvious
    evasion: ``home = os.path.expanduser("~")`` then ``shutil.rmtree(home)`` is the same
    payload as the one-liner, and a rule that reads only the one-liner is defeated by
    pressing Enter. So a name is resolved only when the whole file binds it once — a second
    assignment, an augmented assignment, a loop target, a `with … as`, a comprehension, a
    parameter, a `del`, an `except … as`, a `global`, or an import of the same name drops it
    entirely. One binding in the whole file means there is exactly one scope to be wrong
    about, which is why this needs no scope tracking to be sound."""
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        for name in _bound_names(node):
            counts[name] = counts.get(name, 0) + 1
    out: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target: ast.expr = node.targets[0]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
        else:
            continue
        value = node.value
        if (
            isinstance(target, ast.Name)
            and value is not None
            and counts.get(target.id) == 1
        ):
            out[target.id] = value
    return out


@dataclass(frozen=True)
class _NativeCtx:
    """What resolving a target in ONE file needs: its import aliases and its stable names."""

    aliases: dict[str, str]
    bindings: dict[str, ast.expr]


def _canonical_name(node: ast.expr, ctx: _NativeCtx) -> str:
    """``node``'s dotted name with its import alias resolved, or ``""`` when it has none.

    Longest-prefix, so ``osp.expanduser`` under ``import os.path as osp`` becomes
    ``os.path.expanduser`` rather than being left unresolvable."""
    dotted = _dotted_name(node)
    if dotted is None:
        return ""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        target = ctx.aliases.get(".".join(parts[:i]))
        if target is not None:
            return ".".join([target, *parts[i:]])
    return dotted


def _target_class(node: ast.expr | None, ctx: _NativeCtx, depth: int = 0) -> str | None:
    """The destructive class of the path ``node`` denotes, or ``None``.

    ``None`` is the answer for everything the analysis cannot resolve to a named target —
    a variable that changes, a runtime join, an argument. That is the direction that costs
    RECALL rather than precision, which is the right way round for a terminal band that
    blocks installs."""
    if node is None or depth > _TARGET_DEPTH:
        return None
    if isinstance(node, ast.Constant):
        return (
            _classify_path_literal(node.value) if isinstance(node.value, str) else None
        )
    if isinstance(node, ast.Name):
        return _target_class(ctx.bindings.get(node.id), ctx, depth + 1)
    if isinstance(node, ast.Subscript):
        keyed = (
            isinstance(node.slice, ast.Constant) and node.slice.value in _HOME_ENV_KEYS
        )
        return (
            "home"
            if keyed and _canonical_name(node.value, ctx) == "os.environ"
            else None
        )
    if not isinstance(node, ast.Call):
        return None
    name = _canonical_name(node.func, ctx)
    if name in _HOME_FUNCS:
        return "home"
    if name in _ENV_READ_FUNCS:
        first = node.args[0] if node.args else None
        keyed = isinstance(first, ast.Constant) and first.value in _HOME_ENV_KEYS
        return "home" if keyed else None
    if name in _PATH_PASSTHROUGH:
        return _target_class(node.args[0] if node.args else None, ctx, depth + 1)
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in _PATH_PASSTHROUGH_METHODS
    ):
        return _target_class(node.func.value, ctx, depth + 1)
    return None


def _delete_target(node: ast.Call, ctx: _NativeCtx) -> ast.expr | None:
    """The path a removal call would remove, or ``None`` when this is not a removal."""
    if _canonical_name(node.func, ctx) in _DELETE_FUNCS:
        return node.args[0] if node.args else None
    if isinstance(node.func, ast.Attribute) and node.func.attr in _DELETE_METHODS:
        return node.func.value
    return None


def _truncating_mode(node: ast.Call, position: int) -> bool:
    """True when this call's ``mode`` is a literal that TRUNCATES what is already there.

    ``w`` and only ``w``: ``a`` appends, ``x`` refuses to clobber, ``r`` reads. A mode the
    source does not pin is not treated as truncating — the default is ``r``, and guessing
    otherwise would make every `open(p)` in the corpus a candidate."""
    mode: ast.expr | None = node.args[position] if len(node.args) > position else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and "w" in mode.value
    )


def _truncating_target(node: ast.Call, ctx: _NativeCtx) -> ast.expr | None:
    """The path a truncating write would blank, or ``None`` when this is not one."""
    name = _canonical_name(node.func, ctx)
    if name in _TRUNCATE_FUNCS:
        return node.args[0] if node.args else None
    if name in _OPEN_FUNCS:
        return node.args[0] if node.args and _truncating_mode(node, 1) else None
    if isinstance(node.func, ast.Attribute):
        if node.func.attr in _TRUNCATE_METHODS:
            return node.func.value
        if node.func.attr == "open" and _truncating_mode(node, 0):
            return node.func.value
    return None


def _walk_class(node: ast.expr, ctx: _NativeCtx) -> str | None:
    """The destructive class of the root an UNBOUNDED recursive walk starts from, else
    ``None``. A bounded walk (`Path(x).glob("*.md")`) is not one, whatever its root."""
    if not isinstance(node, ast.Call):
        return None
    name = _canonical_name(node.func, ctx)
    if name in _WALK_FUNCS:
        return _target_class(node.args[0] if node.args else None, ctx)
    if name in _GLOB_FUNCS:
        recursive = next(
            (kw.value for kw in node.keywords if kw.arg == "recursive"), None
        )
        pattern = node.args[0] if node.args else None
        if not (isinstance(recursive, ast.Constant) and recursive.value is True):
            return None
        if not (isinstance(pattern, ast.Constant) and isinstance(pattern.value, str)):
            return None
        return _classify_path_literal(pattern.value.split("*")[0])
    if not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr in _WALK_METHODS:
        return _target_class(node.func.value, ctx)
    if node.func.attr == "glob":
        pattern = node.args[0] if node.args else None
        deep = isinstance(pattern, ast.Constant) and "**" in str(pattern.value)
        return _target_class(node.func.value, ctx) if deep else None
    return None


def _walk_class_within(node: ast.expr, ctx: _NativeCtx) -> str | None:
    """:func:`_walk_class` for the walk anywhere inside ``node`` — so wrapping the walk in
    ``sorted(...)`` or ``list(...)``, which is how it is usually written, still counts.
    """
    for inner in ast.walk(node):
        if isinstance(inner, ast.expr):
            found = _walk_class(inner, ctx)
            if found is not None:
                return found
    return None


def _deletes_within(nodes: Iterable[ast.AST], ctx: _NativeCtx) -> bool:
    """True when any of ``nodes`` contains a removal call, at any depth."""
    return any(
        isinstance(inner, ast.Call)
        and (
            _canonical_name(inner.func, ctx) in _DELETE_FUNCS
            or (
                isinstance(inner.func, ast.Attribute)
                and inner.func.attr in _DELETE_METHODS
            )
        )
        for root in nodes
        for inner in ast.walk(root)
    )


def _ast_evidence(text: str, node: ast.AST) -> str:
    """The user-facing snippet for one AST finding: ``L<line>: <source>``.

    Cut on LINE boundaries and never on columns. :mod:`ast` reports ``col_offset`` as a
    UTF-8 BYTE count, so a line with non-ASCII text before the call would make a
    column-derived slice point at the wrong characters — and this string is what a reviewer
    reads to check the finding. Shares :func:`_evidence`'s caps and its ``…`` mark, so a
    native finding and a regex finding read the same on the consent surface."""
    lines = text.splitlines()
    first = max((getattr(node, "lineno", 1) or 1) - 1, 0)
    last = min(
        getattr(node, "end_lineno", None) or first + 1, first + _EVIDENCE_MAX_LINES
    )
    prefix = f"L{first + 1}: "
    body = " ".join(" ".join(lines[first:last]).split())
    room = _EVIDENCE_CAP - len(prefix)
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + _ELLIPSIS
    return f"{prefix}{body}".rstrip()


def _scan_native_destruction(text: str, rel: str) -> list[Finding]:
    """The native-destruction family over one Python source text.

    At most one finding per rule — the same "first match per rule" shape ``_scan_script``
    uses for the regex catalog — emitted in :data:`_NATIVE_DESTRUCTION_RULES` order so a
    report is deterministic.

    A file that does not parse yields NOTHING. That is a recall limit and not a pass: the
    text catalogs still judge every byte of it, and the reachability pass still reports
    ``unparseable`` for whatever they matched. Guessing at a broken parse tree is how a
    scanner ends up flagging a docstring that forbids the very call it names."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    ctx = _NativeCtx(aliases=_import_aliases(tree), bindings=_single_bindings(tree))
    hits: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            if _walk_class_within(node.iter, ctx) and _deletes_within(node.body, ctx):
                hits.setdefault("destructive_walk", node)
        elif isinstance(
            node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
        ):
            if any(_walk_class_within(gen.iter, ctx) for gen in node.generators):
                produced: list[ast.AST] = [
                    test for gen in node.generators for test in gen.ifs
                ]
                produced.extend(
                    [node.key, node.value]
                    if isinstance(node, ast.DictComp)
                    else [node.elt]
                )
                if _deletes_within(produced, ctx):
                    hits.setdefault("destructive_walk", node)
        elif isinstance(node, ast.Call):
            if _target_class(_delete_target(node, ctx), ctx):
                hits.setdefault("destructive_delete", node)
            if _target_class(_truncating_target(node, ctx), ctx):
                hits.setdefault("destructive_truncate", node)
    return [
        Finding("script", Verdict.DANGEROUS, rule, rel, _ast_evidence(text, hits[rule]))
        for rule in _NATIVE_DESTRUCTION_RULES
        if rule in hits
    ]


class SkillScanner:
    """Static content gate over a STAGED directory (skill or app).

    ``scan(staged_dir, tier)`` walks the tree, classifies each surface, and
    returns a :class:`ScanReport`. The same instance is stateless + reusable; a
    module-level :data:`default_scanner` is provided for convenience.
    """

    def scan(
        self, staged_dir: Path, tier: TrustTier = TrustTier.COMMUNITY
    ) -> ScanReport:
        staged_dir = Path(staged_dir)
        findings: list[Finding] = []
        surfaces: set[str] = set()
        py_texts: dict[str, str] = {}
        loader_blobs: list[str] = []
        manifest_text: str | None = None
        opaque: list[str] = []

        if staged_dir.is_dir():
            for path in sorted(staged_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel_parts = path.relative_to(staged_dir).parts
                if any(part in _SKIP_DIR_NAMES for part in rel_parts[:-1]):
                    continue
                rel = str(path.relative_to(staged_dir))
                text: str | None = None
                try:
                    if path.stat().st_size <= _MAX_FILE_BYTES:
                        text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = None
                if text is None:
                    if path.suffix.lower() == ".py" or _is_loader_name(path.name):
                        opaque.append(rel)
                    continue
                lname = path.name.lower()
                if path.suffix.lower() == ".py":
                    py_texts[rel] = text
                elif _is_loader_name(path.name):
                    loader_blobs.append(text)
                if rel == "app.json":
                    manifest_text = text
                if path.suffix.lower() in _SCRIPT_EXTS or _is_under_scripts(rel):
                    surfaces.add("script")
                    findings.extend(self._scan_script(text, rel))
                if lname in _MANIFEST_NAMES or path.suffix.lower() in {
                    ".md",
                    ".json",
                    ".yaml",
                    ".yml",
                }:
                    surface = "frontmatter" if lname in _MANIFEST_NAMES else "manifest"
                    surfaces.add(surface)
                    findings.extend(self._scan_text(text, rel, surface))

        findings = _scope_by_reachability(
            findings,
            py_texts=py_texts,
            loader_blobs=loader_blobs,
            manifest_text=manifest_text,
            opaque=opaque,
        )
        verdict = self._aggregate(findings, tier)
        return ScanReport(
            verdict=verdict,
            findings=findings,
            surfaces_scanned=sorted(surfaces),
            tier=tier,
        )

    def scan_text(self, text: str, *, surface: str = "manifest") -> ScanReport:
        """Scan a single text blob. Community tier. ``surface="script"`` runs the
        full destructive-script ruleset (skill-install gate, S3); any other
        surface runs the prose/injection + invisible-char rules (the memory-write
        injection gate, S5).

        NO reachability scoping here, by construction: a bare blob has no bundle around
        it, so non-reachability cannot be proved and the default-deny answer stands. A
        DANGEROUS blob stays DANGEROUS."""
        findings = (
            self._scan_script(text, "")
            if surface == "script"
            else self._scan_text(text, "", surface)
        )
        return ScanReport(
            verdict=self._aggregate(findings, TrustTier.COMMUNITY),
            findings=findings,
            surfaces_scanned=[surface],
            tier=TrustTier.COMMUNITY,
        )

    def _scan_script(self, text: str, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for rule, pat in _DANGEROUS_SCRIPT:
            m = pat.search(text)
            if m:
                out.append(
                    Finding("script", Verdict.DANGEROUS, rule, rel, _evidence(text, m))
                )
        if Path(rel).suffix.lower() in {"", ".py"}:
            out.extend(_scan_native_destruction(text, rel))
        code = _strip_line_comments(text)
        sens_hits = list(_SENSITIVE_RE.finditer(code))
        net_hits = list(_NET_EGRESS_RE.finditer(code))
        exfil = next(
            (
                s
                for s in sens_hits
                for n in net_hits
                if abs(_line_of(code, s.start()) - _line_of(code, n.start()))
                <= _EXFIL_PROXIMITY_LINES
            ),
            None,
        )
        sens_raw = _SENSITIVE_RE.search(text)
        if exfil is not None:
            out.append(
                Finding(
                    "script",
                    Verdict.DANGEROUS,
                    "exfil_sensitive_path",
                    rel,
                    _evidence(code, exfil),
                )
            )
        elif sens_raw is not None and _SENSITIVE_RE.search(code) is not None:
            out.append(
                Finding(
                    "script",
                    Verdict.WARNING,
                    "reads_sensitive_path",
                    rel,
                    _evidence(text, sens_raw),
                )
            )
        for rule, pat in _WARNING_SCRIPT:
            m = pat.search(text)
            if m:
                out.append(
                    Finding("script", Verdict.WARNING, rule, rel, _evidence(text, m))
                )
        out.extend(self._scan_invisible(text, rel, "script"))
        return out

    def _scan_text(self, text: str, rel: str, surface: str) -> list[Finding]:
        out: list[Finding] = []
        for rule, pat in _INJECTION_PROSE:
            m = pat.search(text)
            if m:
                out.append(
                    Finding(surface, Verdict.WARNING, rule, rel, _evidence(text, m))
                )
        out.extend(self._scan_invisible(text, rel, surface))
        return out

    def _scan_invisible(self, text: str, rel: str, surface: str) -> list[Finding]:
        bidi = {
            c
            for c in text
            if c in _INVISIBLE_CHARS
            and unicodedata.category(c) == "Cf"
            and c in {"‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"}
        }
        zw = {c for c in text if c in _INVISIBLE_CHARS} - bidi
        out: list[Finding] = []
        if bidi:
            out.append(
                Finding(
                    surface,
                    Verdict.DANGEROUS,
                    "bidi_override",
                    rel,
                    "bidirectional override codepoints present",
                )
            )
        if zw:
            out.append(
                Finding(
                    surface,
                    Verdict.WARNING,
                    "zero_width_chars",
                    rel,
                    "zero-width/invisible codepoints present",
                )
            )
        return out

    @staticmethod
    def _aggregate(findings: list[Finding], tier: TrustTier) -> Verdict:
        if not findings:
            return Verdict.CLEAN
        worst = max((f.severity for f in findings), key=lambda v: v.rank)
        if worst is Verdict.DANGEROUS:
            return Verdict.DANGEROUS
        if tier is TrustTier.BUILTIN:
            return Verdict.LOW if worst.rank > Verdict.LOW.rank else worst
        if tier in (TrustTier.OFFICIAL, TrustTier.TRUSTED) and worst is Verdict.WARNING:
            return Verdict.LOW
        return worst


def _is_under_scripts(rel: str) -> bool:
    parts = Path(rel).parts
    return "scripts" in parts or "hooks" in parts or "bin" in parts


default_scanner = SkillScanner()


def scan_dir(staged_dir: Path, tier: TrustTier = TrustTier.COMMUNITY) -> ScanReport:
    """Module-level helper — scan a staged directory with the default scanner."""
    return default_scanner.scan(staged_dir, tier)
