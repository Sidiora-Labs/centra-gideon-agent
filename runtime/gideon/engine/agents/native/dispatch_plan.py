"""Resource declarations and ordered concurrency windows for native tools."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from gideon.security.guardrails.registries import normalize_item, path_glob

TIMING_LOG_PREFIX = "tool batch"
MODE_SERIAL = "serial"
MODE_CONCURRENT = "concurrent"
MAX_CONCURRENT_CALLS = 8
READ = "read"
WRITE = "write"
KIND_PATH = "path"
KIND_TREE = "tree"
KIND_PATTERN = "pattern"
KIND_NAMESPACE = "namespace"
KIND_EVERYTHING = "everything"
NS_KNOWLEDGE = "knowledge"
NS_TASKS = "tasks"
NS_INBOX = "inbox"
NS_TOOL_RESULTS = "tool_results"


@dataclass(frozen=True, slots=True)
class Reservation:
    mode: str
    kind: str
    key: str

    @property
    def writes(self) -> bool:
        return self.mode == WRITE


EVERYTHING = Reservation(WRITE, KIND_EVERYTHING, "*")
NOTHING: tuple[Reservation, ...] = ()
_PURE = frozenset({"tool_search", "tool_schema"})


@dataclass(frozen=True, slots=True)
class _ResourceRule:
    mode: str
    kind: str
    argument: str = ""
    default: str = ""

    def bind(self, arguments: dict, cwd: str | None) -> Reservation:
        if self.kind == KIND_NAMESPACE:
            return Reservation(self.mode, self.kind, self.default)
        value = arguments.get(self.argument, "")
        if self.kind == KIND_PATTERN:
            # Explicit None on glob has historically been the literal pattern "None".
            text = str(value) if self.argument == "pattern" else str(value or "")
            if not text and self.argument == "glob":
                return _tree(self.mode, ".", cwd=cwd)
            return _pattern(self.mode, text)
        text = str(value or self.default).strip()
        if not text and self.kind == KIND_PATH:
            return EVERYTHING
        return Reservation(self.mode, self.kind, _resolve(text or ".", cwd=cwd))


_RULE_GROUPS = (
    (_ResourceRule(READ, KIND_PATH, "path"), ("read_file",)),
    (_ResourceRule(WRITE, KIND_PATH, "path"), ("write_file", "edit_file")),
    (_ResourceRule(READ, KIND_TREE, "path", "."), ("list_dir", "repo_map")),
    (_ResourceRule(READ, KIND_PATTERN, "pattern"), ("glob",)),
    (_ResourceRule(READ, KIND_PATTERN, "glob"), ("grep",)),
    (
        _ResourceRule(READ, KIND_NAMESPACE, default=NS_KNOWLEDGE),
        ("knowledge_search", "knowledge_get", "knowledge_stats"),
    ),
    (
        _ResourceRule(WRITE, KIND_NAMESPACE, default=NS_KNOWLEDGE),
        ("knowledge_create", "knowledge_update"),
    ),
    (
        _ResourceRule(READ, KIND_NAMESPACE, default=NS_TASKS),
        (
            "task_list",
            "task_get",
            "task_search",
            "task_ready",
            "project_list",
            "project_run_status",
            "project_run_list",
        ),
    ),
    (
        _ResourceRule(WRITE, KIND_NAMESPACE, default=NS_TASKS),
        (
            "task_create",
            "task_update",
            "task_list_create",
            "project_create",
            "project_run_create",
            "project_run_start",
        ),
    ),
    (_ResourceRule(WRITE, KIND_NAMESPACE, default=NS_INBOX), ("post_to_inbox",)),
    (
        _ResourceRule(READ, KIND_NAMESPACE, default=NS_TOOL_RESULTS),
        ("tool_result_get",),
    ),
)
_RESOURCE_RULES = {name: rule for rule, names in _RULE_GROUPS for name in names}


def _resolve(path: str, *, cwd: str | None) -> str:
    value = (path or "").strip()
    absolute = os.path.isabs(os.path.expanduser(os.path.expandvars(value)))
    return normalize_item(os.path.join(cwd, value) if cwd and not absolute else value)


def _tree(mode: str, path: str, *, cwd: str | None) -> Reservation:
    return Reservation(mode, KIND_TREE, _resolve(path or ".", cwd=cwd))


def _pattern(mode: str, pattern: str) -> Reservation:
    value = (pattern or "").strip()
    unsafe = not value or ".." in value.replace("\\", "/").split("/")
    return EVERYTHING if unsafe else Reservation(mode, KIND_PATTERN, value)


def reservations_for(
    tool_name: str, args: dict, *, cwd: str | None = None
) -> tuple[Reservation, ...]:
    name = (tool_name or "").strip()
    if name in _PURE:
        return NOTHING
    try:
        rule = _RESOURCE_RULES[name]
        return (rule.bind(args if isinstance(args, dict) else {}, cwd),)
    except Exception:
        return (EVERYTHING,)


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _overlaps(a: Reservation, b: Reservation) -> bool:
    kinds = (a.kind, b.kind)
    if KIND_EVERYTHING in kinds:
        return True
    if KIND_NAMESPACE in kinds:
        return a.kind == b.kind and a.key == b.key
    if KIND_PATTERN not in kinds:
        return _under(a.key, b.key) or _under(b.key, a.key)
    pattern, target = (a, b) if a.kind == KIND_PATTERN else (b, a)
    return target.kind in (KIND_PATTERN, KIND_TREE) or path_glob(
        target.key, pattern.key
    )


def conflicts(a: Sequence[Reservation], b: Sequence[Reservation]) -> bool:
    writers = (entry for entry in a if entry.writes)
    if any(_overlaps(left, right) for left in writers for right in b):
        return True
    readers = (entry for entry in a if not entry.writes)
    other_writers = tuple(entry for entry in b if entry.writes)
    return any(_overlaps(left, right) for left in readers for right in other_writers)


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    waves: tuple[tuple[int, ...], ...]

    @property
    def call_count(self) -> int:
        return sum(map(len, self.waves))

    @property
    def widest(self) -> int:
        return max(map(len, self.waves), default=0)

    @property
    def mode(self) -> str:
        return MODE_CONCURRENT if self.widest > 1 else MODE_SERIAL


@dataclass(slots=True)
class _Window:
    start: int = 0
    resources: list[Reservation] = field(default_factory=list)

    def accepts(self, index: int, incoming: Sequence[Reservation], width: int) -> bool:
        return index - self.start < width and not conflicts(incoming, self.resources)

    def indices_until(self, stop: int) -> tuple[int, ...]:
        return tuple(range(self.start, stop))


def plan(
    reservation_sets: Sequence[Sequence[Reservation]],
    *,
    max_width: int = MAX_CONCURRENT_CALLS,
) -> DispatchPlan:
    width = max(1, int(max_width or 1))
    window = _Window()
    finished: list[tuple[int, ...]] = []
    stop = 0
    for index, incoming in enumerate(reservation_sets):
        if not window.accepts(index, incoming, width):
            finished.append(window.indices_until(index))
            window = _Window(start=index)
        window.resources.extend(incoming)
        stop = index + 1
    if stop:
        finished.append(window.indices_until(stop))
    return DispatchPlan(tuple(finished))
