"""Built-in file/code/shell tools for the native runtime.

In the ACP architecture the file/edit/shell tools were the external CLI's own
built-ins (claude-code provides Read/Write/Bash); ``gideon-core`` only
layered orchestration (spawn/memory/artifact) on top. The native in-process
runtime has no such CLI, so this provider supplies the essential workspace
tools — read, write, edit, ls, glob, grep, and bash — scoped to the session's
``cwd`` and gated by the same :mod:`gideon.security.security` checks (deny-list,
sensitive-path) plus :func:`gideon.security.sandbox.wrap_argv` for shell.

All paths are resolved relative to ``cwd`` and confined to it (no escaping the
workspace via ``..`` or absolute paths outside it). Tool execution itself is
also gated by the runtime's approval gate (``requires_approval`` per tool).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from functools import partial
from pathlib import Path
from typing import Any, cast

from gideon.core import cancellation
from gideon.engine.agents.native import application_tools as app_tools
from gideon.engine.agents.native import read_gate
from gideon.engine.agents.native.decision_tool_defs import decision_tool_definitions
from gideon.engine.agents.native.workspace_access import (
    FileSnapshot,
    ShellCapture,
    TextChange,
    WorkspaceDefaults,
    WorkspaceTree,
)
from gideon.integrations.tool_providers import result_store
from gideon.integrations.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)
from gideon.integrations.tool_providers.projection import (
    project_and_retain,
    project_output,
)

logger = logging.getLogger(__name__)

_READ_GATED_WRITE_TOOLS: dict[str, tuple[str, str | None, str]] = {
    "write_file": ("overwrite", None, "content"),
    "edit_file": ("edit", "old_str", "new_str"),
}

_CURRENT_CWD: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gideon_native_cwd", default=""
)
_CURRENT_EXTRA_ROOTS: contextvars.ContextVar[tuple] = contextvars.ContextVar(
    "gideon_native_extra_roots", default=()
)
_CURRENT_AGENT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gideon_native_agent", default=""
)
_CURRENT_PROJECT_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gideon_native_project_id", default=""
)


def bind_tool_context(
    *,
    cwd: Path | str | None,
    agent: str = "",
    extra_roots: list | None = None,
    project_id: str = "",
):
    from gideon.integrations.tool_providers import projection

    assignments = (
        (_CURRENT_CWD, str(cwd) if cwd else ""),
        (_CURRENT_AGENT, agent or ""),
        (_CURRENT_EXTRA_ROOTS, tuple(map(str, extra_roots or ()))),
        (_CURRENT_PROJECT_ID, project_id or ""),
    )
    bound = [variable.set(value) for variable, value in assignments]
    bound.append(projection.bind_project_dir(cwd))
    return bound


def reset_tool_context(tokens) -> None:
    from gideon.integrations.tool_providers import projection

    variables: tuple[Any, ...] = (
        _CURRENT_CWD,
        _CURRENT_AGENT,
        _CURRENT_EXTRA_ROOTS,
        _CURRENT_PROJECT_ID,
    )
    for index, variable in enumerate(variables):
        if not tokens or index >= len(tokens):
            break
        try:
            variable.reset(tokens[index])
        except (ValueError, LookupError):
            continue
    if tokens and len(tokens) > len(variables):
        projection.reset_project_dir(tokens[len(variables)])


def current_project_id() -> str:
    return _CURRENT_PROJECT_ID.get()


_CATEGORY_OF: dict[str, str] = {
    "read_file": "filesystem",
    "write_file": "filesystem",
    "edit_file": "filesystem",
    "list_dir": "filesystem",
    "glob": "filesystem",
    "grep": "filesystem",
    "repo_map": "filesystem",
    "bash": "shell",
    "tool_result_get": "core",
    "knowledge_search": "knowledge",
    "knowledge_structural": "knowledge",
    "knowledge_create": "knowledge",
    "knowledge_get": "knowledge",
    "knowledge_update": "knowledge",
    "knowledge_stats": "knowledge",
    "log_decision": "knowledge",
    "decision_list": "knowledge",
    "decision_resolve": "knowledge",
    "task_create": "tasks",
    "task_list": "tasks",
    "task_get": "tasks",
    "task_update": "tasks",
    "task_ready": "tasks",
    "task_search": "tasks",
    "project_create": "tasks",
    "project_list": "tasks",
    "task_list_create": "tasks",
    "project_run_create": "projects",
    "project_run_start": "projects",
    "project_run_status": "projects",
    "project_run_list": "projects",
    "post_to_inbox": "inbox",
}

PLATFORM_CATEGORIES: frozenset[str] = frozenset({"filesystem", "shell", "core"})
APP_CATEGORY_PROVIDERS: dict[str, tuple[str, str]] = {
    "knowledge": ("gideon-knowledge-tools", "Knowledge Tools"),
    "tasks": ("gideon-tasks-tools", "Tasks Tools"),
    "projects": ("gideon-project-tools", "Projects Tools"),
    "inbox": ("gideon-inbox-tools", "Inbox Tools"),
}

_MAX_READ_BYTES = 256 * 1024
from gideon.integrations.tool_providers.projection import (
    DEFAULT_TOOL_OUTPUT_CAP as _MAX_OUTPUT_CHARS,
)

_BASH_TIMEOUT = 120.0
_BASH_TIMEOUT_MAX = 600.0

_bg_ingest_tasks: set = set()


def _kn_redact(text: str | None) -> str:
    if not text:
        return text or ""
    from gideon.security import security

    for scrub in (security.redact_exfiltration_urls, security.redact_credentials):
        text, _ = scrub(text)
    return text


def _kn_title(item: dict) -> str:
    candidates = (
        (item.get(field) or "").strip()
        for field in ("title", "ai_title", "url_title", "url")
    )
    return next((value for value in candidates if value), "(untitled)")


def _kn_snippet(item: dict, limit: int = 160) -> str:
    insights = item.get("insights") or {}
    candidates = [item.get("summary"), item.get("ai_summary")]
    candidates.append(insights.get("summary") if isinstance(insights, dict) else None)
    candidates.extend((item.get("url_description"), item.get("content")))
    for candidate in candidates:
        if value := (candidate or "").strip():
            return value[:limit].replace("\n", " ")
    return ""


def _enrich_in_background(item_id: str) -> None:
    from gideon.cognition.knowledge import (
        get_knowledge_embedder,
        get_knowledge_llm_pool,
        get_knowledge_store,
    )
    from gideon.cognition.knowledge.pipeline.runner import ingest_item

    request = app_tools.EnrichmentRequest(
        item_id,
        get_knowledge_store,
        get_knowledge_embedder,
        get_knowledge_llm_pool,
        ingest_item,
        logger,
    )
    pending = asyncio.create_task(request.run())
    pending.add_done_callback(_bg_ingest_tasks.discard)
    _bg_ingest_tasks.add(pending)


def _ok_capped(
    text: str,
    limit: int = _MAX_OUTPUT_CHARS,
    *,
    content_type: str | None = None,
    session_key: str = "",
) -> ToolResult:
    rendered, attributes = project_and_retain(
        text, session_key=session_key, content_type=content_type, cap=limit
    )
    result = ToolResult(success=True, output=rendered, metadata=attributes)
    result.truncated = bool(attributes.get("truncated"))
    result.original_length = attributes.get("original_length")
    return result


def _denied_bash_reason(command: str) -> str | None:
    from gideon.security.security import denied_command_reason

    return denied_command_reason(command)


class NativeBuiltinToolProvider(ToolProvider):
    """Workspace file/code/shell tools for the native agent loop."""

    _SKIP_DIRS = frozenset(
        {
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            "env",
            "dist",
            "build",
            ".next",
            "target",
            ".mypy_cache",
            ".pytest_cache",
            "vendor",
            ".idea",
            ".tox",
            ".cache",
            "coverage",
            ".gradle",
        }
    )

    def __init__(
        self,
        cwd: Path | None = None,
        *,
        sandbox_mode: str = "auto",
        agent: str = "",
        session_key: str = "",
        extra_roots: list[Path] | None = None,
        categories: "frozenset[str] | set[str] | None" = None,
        provider_name: str = "builtin",
        display: str = "Workspace Tools",
    ) -> None:
        self._defaults = WorkspaceDefaults(
            Path(cwd) if cwd else Path.cwd(),
            tuple(Path(root).resolve() for root in extra_roots or ()),
            agent or "",
            session_key or "",
        )
        self._categories = None if categories is None else frozenset(categories)
        self._provider_name, self._display = provider_name, display
        self._sandbox_mode = sandbox_mode

    @property
    def _cwd(self) -> Path:
        return (
            Path(_CURRENT_CWD.get()) if _CURRENT_CWD.get() else self._defaults.directory
        )

    @property
    def _extra_roots(self) -> list[Path]:
        roots = _CURRENT_EXTRA_ROOTS.get()
        return (
            [Path(root).resolve() for root in roots]
            if roots
            else list(self._defaults.extra_roots)
        )

    @property
    def _agent(self) -> str:
        bound = _CURRENT_AGENT.get()
        return bound if bound else self._defaults.agent

    @property
    def _session_key(self) -> str:
        from gideon.integrations.mcp_core import get_current_session_key

        bound = get_current_session_key()
        return bound if bound else self._defaults.session

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return self._display

    def _resolve(self, rel: str) -> Path:
        return WorkspaceDefaults.resolve(self._cwd, self._extra_roots, rel)

    async def list_tools(self) -> list[ToolDefinition]:
        catalog = self._all_tool_defs({"type": "object"})
        if self._categories is not None:
            catalog = [
                definition
                for definition in catalog
                if _CATEGORY_OF.get(definition.name) in self._categories
            ]
            for definition in catalog:
                definition.provider = self.name
        return catalog

    @staticmethod
    def _structural_verbs() -> list[str]:
        from gideon.cognition.knowledge import structural

        return [*structural.STRUCTURAL_VERBS]

    def _all_tool_defs(self, s: dict) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="read_file",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Read a UTF-8 text file from the workspace. Args: path (str), optional max_bytes (int).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {
                        "path": {"type": "string"},
                        "max_bytes": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            ),
            ToolDefinition(
                name="write_file",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description="Create or overwrite a text file in the workspace. Args: path (str), content (str).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolDefinition(
                name="edit_file",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description="Replace old_str with new_str in a file. old_str must match EXACTLY ONCE (include surrounding context to make it unique) — if it matches multiple times the edit is rejected unless replace_all is true. Args: path, old_str, new_str, replace_all (optional, default false).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {
                        "path": {"type": "string"},
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["path", "old_str", "new_str"],
                },
            ),
            ToolDefinition(
                name="list_dir",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="List entries in a workspace directory. Args: path (str, default '.').",
                parameters={**s, "properties": {"path": {"type": "string"}}},
            ),
            ToolDefinition(
                name="glob",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Find files matching a glob pattern under the workspace. Args: pattern (str, e.g. '**/*.py').",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            ),
            ToolDefinition(
                name="grep",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Search file contents (substring by default, or a Python regex with regex=true). Skips .git/node_modules/venv/build dirs. Args: query (str), optional glob (str), optional regex (bool), optional max_results (int).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {
                        "query": {"type": "string"},
                        "glob": {"type": "string"},
                        "regex": {"type": "boolean"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="repo_map",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Structural map of the workspace codebase — the directory tree plus the "
                    "top-level definitions (functions, classes, exports) of each source file, "
                    "so you can orient WITHOUT reading every file. Args: optional path (str, "
                    "subdir to map, default the whole workspace), optional max_files (int)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "path": {"type": "string"},
                        "max_files": {"type": "integer"},
                    },
                },
            ),
            ToolDefinition(
                name="bash",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.DESTRUCTIVE,
                description=(
                    "Run a shell command in the workspace — your PRIMARY way to interact with the "
                    "environment. Use it for git (status/diff/branch/commit — push is blocked), "
                    "running tests (pytest, npm test, go test, cargo test, make test), linters/"
                    "type-checkers (ruff, eslint, tsc, go vet), builds, package managers, and any "
                    "standard CLI. Prefer real commands over asking for a dedicated tool. Runs in a "  # noqa: E501
                    "login shell at the workspace root; stdout+stderr are merged and the exit code "
                    "is reported. Sandboxed + credential/exfiltration deny-list enforced. Args: "
                    "command (str), optional timeout (int seconds, default 120, max 600 — raise it "
                    "for a slow test suite or build)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {
                            "type": "integer",
                            "description": "Seconds before the command is killed (default 120, max 600).",  # noqa: E501
                        },
                    },
                    "required": ["command"],
                },
            ),
            ToolDefinition(
                name="post_to_inbox",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Surface a message to the user in their Inbox triage queue — use when "
                    "you finish something worth reporting, need a decision, or have a heads-up, "
                    "and no one is watching the chat live. Args: message (str), kind "
                    "('notification'|'question'|'fyi', default 'notification'; 'question' asks "
                    "for a reply), optional context (str — why/what you used)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "message": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["notification", "question", "fyi"],
                        },
                        "context": {"type": "string"},
                    },
                    "required": ["message"],
                },
            ),
            ToolDefinition(
                name="knowledge_search",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Search the user's knowledge library (notes, bookmarks, docs). Args: query (str), optional limit (int, default 8).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="knowledge_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Add an item to the user's knowledge library. Args: type "
                    "('note'|'fleeting'|'journal'|'gist'|'bookmark', default 'note'), "
                    "title (str), content (str — the note/gist body), url (str — for bookmark), "
                    "optional tags (list of str), optional gist_language (str — the "
                    "code language for a gist, e.g. 'python')."
                ),
                parameters={
                    **s,
                    "properties": {
                        "type": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "url": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "gist_language": {"type": "string"},
                    },
                },
            ),
            ToolDefinition(
                name="knowledge_get",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Fetch one knowledge item by id (title, type, content, tags, summary). Args: id (str).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            ),
            ToolDefinition(
                name="knowledge_update",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Update an existing knowledge item and re-enrich it. Args: id (str, required), "
                    "and any of title (str), content (str), tags (list of str), url (str), "
                    "gist_language (str — only for gist items; sets the code language for syntax "
                    "highlighting), is_pinned (bool), is_archived (bool). Editing content/url re-runs extraction."  # noqa: E501
                ),
                parameters={
                    **s,
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "url": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "gist_language": {"type": "string"},
                        "is_pinned": {"type": "boolean"},
                        "is_archived": {"type": "boolean"},
                    },
                    "required": ["id"],
                },
            ),
            ToolDefinition(
                name="knowledge_structural",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Ask a STRUCTURAL question about the knowledge library and get the answer "
                    "by traversing stored links — not by semantic similarity. Use this instead "
                    "of knowledge_search whenever the question is about relations rather than "
                    "topic; a similarity search answers 'what links to this' only by accident. "
                    "Args: verb (str, required) — one of 'links_to' (what points AT an item: "
                    "typed relations + citations), 'depends_on' (the outbound dependency chain), "
                    "'tag_subtree' (everything under a tag and its child tags), 'changed_since' "
                    "(what was updated after a timestamp), 'contradictions' (items recorded as "
                    "contradicting each other); origin (str) — item id for links_to/depends_on, "
                    "tag name for tag_subtree, optional item id to scope contradictions; since "
                    "(str, ISO timestamp) for changed_since; depth (int, default 1, max 6) — how "
                    "many hops to follow; limit (int, default 25); rank_query (str, optional) — "
                    "orders the structural result by closeness to this text WITHOUT changing "
                    "which items are in it. Every result carries the exact link path that "
                    "reached it, so you can cite why. An empty answer states which relation is "
                    "missing; it never silently degrades to a similarity guess."
                ),
                parameters={
                    **s,
                    "properties": {
                        "verb": {"type": "string", "enum": self._structural_verbs()},
                        "origin": {"type": "string"},
                        "since": {"type": "string"},
                        "depth": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "rank_query": {"type": "string"},
                    },
                    "required": ["verb"],
                },
            ),
            ToolDefinition(
                name="knowledge_stats",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Get an overview of the knowledge library for gap detection: total item "
                    "count, a by-type breakdown, and the most common tags. No args."
                ),
                parameters={**s, "properties": {}},
            ),
            *decision_tool_definitions(self.name, s),
            ToolDefinition(
                name="task_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a task in the user's task system. Args: title (str, required), "
                    "optional description (str), priority ('critical'|'high'|'medium'|'low'|"
                    "'trivial', default medium), task_list_id (str — place it in a task list; "
                    "the task's project label is derived from the list), labels (list of str), "
                    "due (str ISO date), exit_criteria (list of {description, met?}), "
                    "action_plan (list of {content} ordered), depends_on (list of task ids "
                    "that must finish first). Cycles are rejected."
                ),
                parameters={
                    **s,
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low", "trivial"],
                        },
                        "task_list_id": {"type": "string"},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "due": {"type": "string"},
                        "exit_criteria": {"type": "array", "items": {"type": "object"}},
                        "action_plan": {"type": "array", "items": {"type": "object"}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title"],
                },
            ),
            ToolDefinition(
                name="task_list",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "List tasks, most-recent first. Args: optional status "
                    "('open'|'in_progress'|'blocked'|'done'|'cancelled'), project (str label), "
                    "task_list_id (str), limit (int, default 25)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "status": {"type": "string"},
                        "project": {"type": "string"},
                        "task_list_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            ToolDefinition(
                name="task_get",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Fetch one task by id (full detail incl. exit criteria, plan, deps). Args: id (str).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            ),
            ToolDefinition(
                name="task_update",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Update a task. Args: id (str, required), and any of title, description, "
                    "status ('open'|'in_progress'|'blocked'|'done'|'cancelled' — 'done' is "
                    "rejected while exit criteria are incomplete), priority, task_list_id, "
                    "labels, due, exit_criteria, action_plan, depends_on. The 'project' label "
                    "is derived from the task list and cannot be set directly."
                ),
                parameters={
                    **s,
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string"},
                        "priority": {"type": "string"},
                        "task_list_id": {"type": "string"},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "due": {"type": "string"},
                        "exit_criteria": {"type": "array", "items": {"type": "object"}},
                        "action_plan": {"type": "array", "items": {"type": "object"}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id"],
                },
            ),
            ToolDefinition(
                name="task_ready",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "List tasks that can be started now (no unfinished prerequisites), "
                    "optionally scoped. Args: optional project (str), task_list_id (str)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "project": {"type": "string"},
                        "task_list_id": {"type": "string"},
                    },
                },
            ),
            ToolDefinition(
                name="task_search",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Search tasks by text + filters. Args: optional query (str over title+"
                    "description), status (list), priority (list), tags (list), project (str), "
                    "sort_by ('relevance'|'created_at'|'updated_at'|'priority'), limit (int)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "query": {"type": "string"},
                        "status": {"type": "array", "items": {"type": "string"}},
                        "priority": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "project": {"type": "string"},
                        "sort_by": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            ToolDefinition(
                name="project_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a project (a scoping container for task lists). Args: name (str, "
                    "required, unique), optional agent_instructions_template (str)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "name": {"type": "string"},
                        "agent_instructions_template": {"type": "string"},
                    },
                    "required": ["name"],
                },
            ),
            ToolDefinition(
                name="project_list",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="List projects (with their task lists). No args.",
                parameters={**s, "properties": {}},
            ),
            ToolDefinition(
                name="task_list_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a task list inside a project. Args: name (str, required), optional "
                    "project_id (str) or project_name (str, find-or-create); repeatable (bool — "
                    "place under the Repeatable project). With no project it lands in 'Chore'."
                ),
                parameters={
                    **s,
                    "properties": {
                        "name": {"type": "string"},
                        "project_id": {"type": "string"},
                        "project_name": {"type": "string"},
                        "repeatable": {"type": "boolean"},
                    },
                    "required": ["name"],
                },
            ),
            ToolDefinition(
                name="project_run_create",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a project RUN — an autonomous, multi-cycle execution (a 'loop') — from a "  # noqa: E501
                    "plan you shaped with the user. USE WHEN the user wants substantial over-many-cycles "  # noqa: E501
                    "work rather than a one-shot chat answer. The `kind` selects the engine: 'code' "  # noqa: E501
                    "(SDLC plan→execute in a codebase — feature/refactor/bugfix, gated stages, its own "  # noqa: E501
                    "workspace + tasks), 'goal' (open-ended research-or-action toward an outcome — "
                    "investigate/monitor/drive to done), 'research' (deep web research → a synthesized "  # noqa: E501
                    "report), 'design' (a design system — tokens/components/exports), or 'general' (a "  # noqa: E501
                    "generic iterative task). Offer it, then create on the user's go. Does NOT start it "  # noqa: E501
                    "— call project_run_start on their go. (To create a plain task CONTAINER instead, "  # noqa: E501
                    "use project_create.) Args: kind (required), task (str, required, 12+ chars — the "  # noqa: E501
                    "goal/work), name?, project_id? (bind under an existing Project container), attended?, "  # noqa: E501
                    "max_cycles?, success_criteria?. kind 'code': project_kind? (greenfield|brownfield), "  # noqa: E501
                    "entry_stage?, workspace_dir? (brownfield needs one to start), stage_plan? "
                    "([{stage,title,objective,exit_criteria?,tasks?}]), verify_command?, test_command?. "  # noqa: E501
                    "kind goal/research/design/general: sub_goals? ([str]), deliverables? ([str]), "
                    "scope? ([str]), goal_type? (goal only), rubric? ([str])."
                ),
                parameters={
                    **s,
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["code", "goal", "general", "design", "research"],
                        },
                        "task": {"type": "string"},
                        "name": {"type": "string"},
                        "project_id": {"type": "string"},
                        "attended": {"type": "boolean"},
                        "max_cycles": {"type": "integer"},
                        "success_criteria": {"type": "string"},
                        "project_kind": {"type": "string"},
                        "entry_stage": {"type": "string"},
                        "workspace_dir": {"type": "string"},
                        "stage_plan": {"type": "array"},
                        "verify_command": {"type": "string"},
                        "test_command": {"type": "string"},
                        "sub_goals": {"type": "array"},
                        "deliverables": {"type": "array"},
                        "scope": {"type": "array"},
                        "goal_type": {"type": "string"},
                        "rubric": {"type": "array"},
                    },
                    "required": ["kind", "task"],
                },
            ),
            ToolDefinition(
                name="project_run_start",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description="Launch a created project run (any kind), or resume a paused/failed one. Args: project_id (str, required — the run id).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"],
                },
            ),
            ToolDefinition(
                name="project_run_status",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Read live progress of any project run — status, stage/phase progress, cycles, latest "  # noqa: E501
                    "finding, and any blocker / needs-input — to report to the user. Args: project_id (str, required — the run id)."  # noqa: E501
                ),
                parameters={
                    **s,
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"],
                },
            ),
            ToolDefinition(
                name="project_run_list",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "List the user's project runs (autonomous executions) with kind + live status, to find "  # noqa: E501
                    "one to report on or resume. Args: optional kind (filter: code|goal|general|design|research), limit (int)."  # noqa: E501
                ),
                parameters={
                    **s,
                    "properties": {
                        "kind": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            ToolDefinition(
                name="tool_result_get",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Retrieve the FULL raw output of an earlier tool call that was projected/"
                    "truncated. When a tool result shows '[projected … full result: "
                    'tool_result_get(result_id="r_…")]\', call this with that result_id to pull the '  # noqa: E501
                    "part the preview dropped. Args: result_id (str, required); optional grep (str, "  # noqa: E501
                    "return only matching lines), line_start/line_end (int, 1-indexed inclusive "  # noqa: E501
                    "line range — the natural way to pull 'lines 40-80'), start/end (int, char "  # noqa: E501
                    "range), max_chars (int). Access modes are checked grep → lines → char range."  # noqa: E501
                ),
                parameters={
                    **s,
                    "properties": {
                        "result_id": {"type": "string"},
                        "grep": {"type": "string"},
                        "line_start": {"type": "integer"},
                        "line_end": {"type": "integer"},
                        "start": {"type": "integer"},
                        "end": {"type": "integer"},
                        "max_chars": {"type": "integer"},
                    },
                    "required": ["result_id"],
                },
            ),
        ]

    def _read_gate_refusal(
        self, tool_name: str, a: dict[str, Any]
    ) -> ToolResult | None:
        declaration = _READ_GATED_WRITE_TOOLS.get(tool_name)
        if declaration is None:
            return None
        operation, region, _ = declaration
        spelling = str(a["path"])
        target = self._resolve(spelling)
        denial = read_gate.admit_write(
            self._session_key,
            target,
            operation=operation,
            display_path=spelling,
            required_text=str(a.get(region) or "") if region else None,
        )
        if denial is not None:
            logger.debug(
                "read gate refused %s on %s (%s)", tool_name, target, denial.reason
            )
            return ToolResult(
                success=False,
                error=denial.error,
                recovery_hints=[denial.hint],
                metadata={"read_gate": denial.reason},
            )
        return None

    def _read_gate_observe_write(
        self, tool_name: str, a: dict[str, Any], result: ToolResult
    ) -> None:
        if not result.success or tool_name not in _READ_GATED_WRITE_TOOLS:
            return
        operation, old_field, new_field = _READ_GATED_WRITE_TOOLS[tool_name]
        try:
            path = self._resolve(str(a["path"]))
            if operation == "edit" and old_field:
                read_gate.record_edit(
                    self._session_key,
                    path,
                    old=str(a.get(old_field) or ""),
                    new=str(a.get(new_field) or ""),
                    replace_all=bool(a.get("replace_all")),
                )
            elif operation == "overwrite":
                read_gate.record_overwrite(
                    self._session_key, path, content=str(a.get(new_field, ""))
                )
        except Exception:
            logger.debug("read gate: post-write observation skipped", exc_info=True)

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            implementation = getattr(self, f"_t_{tool_name}", None)
            if implementation is None:
                return ToolResult(
                    success=False, error=f"unknown builtin tool {tool_name!r}"
                )
            denied = self._read_gate_refusal(tool_name, arguments)
            if denied is not None:
                return denied
            completed = await implementation(arguments)
            self._read_gate_observe_write(tool_name, arguments, completed)
        except Exception as exc:
            if isinstance(exc, KeyError):
                message = f"missing required argument: {exc}"
                hints = [
                    f"Provide the {exc} argument; see the tool's parameter schema for required fields."
                ]
            elif isinstance(exc, ValueError):
                message = str(exc)
                hints = (
                    [
                        "Use a path relative to the workspace root; '..' and absolute paths outside it are not allowed."
                    ]
                    if "escapes the workspace" in message
                    else []
                )
            else:
                logger.debug("builtin tool %s failed", tool_name, exc_info=True)
                message = f"{type(exc).__name__}: {exc}"
                hints = [
                    "Check the arguments against the tool's parameter schema and retry."
                ]
            return ToolResult(success=False, error=message, recovery_hints=hints)
        return completed

    async def _t_tool_result_get(self, a: dict) -> ToolResult:
        reference = str(a.get("result_id", "")).strip()
        if not reference:
            return ToolResult(
                success=False,
                error="result_id is required",
                recovery_hints=[
                    "Pass the result_id named in the projection note, e.g. r_001ab."
                ],
            )
        if not self._session_key:
            return ToolResult(
                success=False, error="no session context for tool-result retrieval"
            )
        options: dict = {
            name: int(a[name]) if a.get(name) is not None else None
            for name in ("end", "line_start", "line_end")
        }
        options.update(
            start=int(a.get("start") or 0),
            grep=str(a["grep"]) if a.get("grep") else None,
            max_chars=int(a.get("max_chars") or _MAX_OUTPUT_CHARS),
        )
        fetched = result_store.fetch_slice(self._session_key, reference, **options)
        if not fetched.get("ok"):
            return ToolResult(
                success=False,
                error=fetched.get("error", "not found"),
                recovery_hints=[
                    "The raw result may have been evicted (bounded store) — re-run the original tool."
                ],
            )
        mode = fetched["mode"]
        description = f"{mode}: showing {fetched['shown']} of {fetched['length']} chars"
        if mode == "grep":
            description += f", {fetched.get('matches', 0)} match(es)"
        if mode == "lines":
            description += f", lines {fetched.get('line_start')}-{fetched.get('line_end')} of {fetched.get('total_lines')}"
        if fetched.get("next_index"):
            description += f"; more from start_index={fetched['next_index']}"
        read_gate.record_retrieval(
            self._session_key, reference, observed_text=str(fetched["content"])
        )
        content_type = (
            fetched.get("content_type", "generic")
            if mode in {"range", "lines"}
            else "generic"
        )
        return ToolResult(
            success=True,
            output=f"[{description}]\n{fetched['content']}",
            metadata={"content_type": content_type},
        )

    async def _t_project_run_create(self, a: dict) -> ToolResult:
        return await app_tools.project_operation("create", a)

    async def _t_project_run_start(self, a: dict) -> ToolResult:
        return await app_tools.project_operation("start", a)

    async def _t_project_run_status(self, a: dict) -> ToolResult:
        return await app_tools.project_operation("status", a)

    async def _t_project_run_list(self, a: dict) -> ToolResult:
        return await app_tools.project_operation("list", a)

    async def _t_read_file(self, a: dict) -> ToolResult:
        path = self._resolve(str(a["path"]))
        size = int(a.get("max_bytes") or _MAX_READ_BYTES)
        snapshot = await asyncio.get_event_loop().run_in_executor(
            None, partial(FileSnapshot.load, path, size)
        )
        if snapshot is None or snapshot.binary:
            binary = snapshot is not None
            return ToolResult(
                success=False,
                error=(
                    f"binary file (not UTF-8 text): {a['path']}"
                    if binary
                    else f"not a file: {a['path']}"
                ),
                recovery_hints=[
                    (
                        "This is a binary file — read_file only handles text. Use list_dir/glob to inspect it, or a bash tool if you need its bytes."
                        if binary
                        else "Use glob to locate the file, or list_dir on its parent directory."
                    )
                ],
            )
        res = _ok_capped(snapshot.text, session_key=self._session_key)
        byte_complete = snapshot.complete
        read_gate.record_read(
            self._session_key,
            path,
            observed_text=res.output,
            content_sha256=snapshot.digest,
            complete=bool(byte_complete) and not res.truncated,
            raw_ref=str((res.metadata or {}).get("raw_ref") or ""),
        )
        return res

    def _checkpoint_pre_edit(self, path: Path) -> None:
        try:
            from gideon.engine.turn_checkpoints import capture_pre_edit

            capture_pre_edit(self._session_key, path, cwd=self._cwd)
        except Exception:
            logger.debug("checkpoint pre-edit skipped for %s", path, exc_info=True)

    async def _t_write_file(self, a: dict) -> ToolResult:
        path = self._resolve(str(a["path"]))
        content = str(a.get("content", ""))
        self._checkpoint_pre_edit(path)

        def persist() -> ToolResult:
            if path.is_dir():
                return ToolResult(
                    success=False,
                    error=f"path is a directory, not a file: {a['path']}",
                    recovery_hints=[
                        "Pass a file path, not a directory. Add a filename segment (e.g. dir/file.py)."
                    ],
                )
            if path.parent.exists() and not path.parent.is_dir():
                return ToolResult(
                    success=False,
                    error=f"a parent path segment is a file, not a directory: {a['path']}",
                    recovery_hints=[
                        "A directory in the path is actually a file — pick a different location or remove the conflicting file first."
                    ],
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(
                success=True, output=f"Wrote {len(content)} chars to {a['path']}"
            )

        return await asyncio.get_event_loop().run_in_executor(None, persist)

    async def _t_edit_file(self, a: dict) -> ToolResult:
        path = self._resolve(str(a["path"]))
        before, after = str(a["old_str"]), str(a["new_str"])
        all_matches = bool(a.get("replace_all"))
        self._checkpoint_pre_edit(path)

        def persist() -> ToolResult:
            if not path.is_file():
                return ToolResult(
                    success=False,
                    error=f"not a file: {a['path']}",
                    recovery_hints=[
                        "Use glob or list_dir to confirm the path, or write_file to create it first."
                    ],
                )
            source = (
                path.read_text(encoding="utf-8") if before and before != after else ""
            )
            change = TextChange.prepare(source, before, after, all_matches)
            if change.error:
                reason = change.error
                hint = "Read the file first to copy the exact text (including whitespace) you want to replace."
                if "not unique" in reason:
                    hint = "Add surrounding lines to old_str so it matches exactly once, or pass replace_all=true to change every occurrence."
                elif reason == "old_str is empty":
                    hint = "old_str must be the exact existing text to replace. To create a file or append, use write_file."
                elif "identical" in reason:
                    hint = "The file already contains new_str — no edit needed. If you meant a different change, set old_str to the current text."
                return ToolResult(success=False, error=reason, recovery_hints=[hint])
            path.write_text(change.content, encoding="utf-8")
            count = change.replacements
            return ToolResult(
                success=True,
                output=f"Edited {a['path']} ({count} replacement{'s' if count != 1 else ''})",
            )

        return await asyncio.get_event_loop().run_in_executor(None, persist)

    async def _t_list_dir(self, a: dict) -> ToolResult:
        directory = self._resolve(str(a.get("path") or "."))
        listing = await asyncio.get_event_loop().run_in_executor(
            None, partial(WorkspaceTree.directory, directory)
        )
        if listing is not None:
            return _ok_capped(listing, session_key=self._session_key)
        return ToolResult(
            success=False,
            error=f"not a directory: {a.get('path', '.')}",
            recovery_hints=[
                "Use list_dir on the parent directory, or read_file if this path is a file."
            ],
        )

    async def _t_glob(self, a: dict) -> ToolResult:
        tree = WorkspaceTree(self._cwd.resolve(), self._SKIP_DIRS)
        scan = partial(tree.glob_listing, str(a["pattern"]))
        output = await asyncio.get_event_loop().run_in_executor(None, scan)
        return _ok_capped(output, session_key=self._session_key)

    async def _t_grep(self, a: dict) -> ToolResult:
        import re

        tree = WorkspaceTree(self._cwd.resolve(), self._SKIP_DIRS)
        query = str(a["query"])
        pattern = str(a.get("glob") or "**/*")
        limit = int(a.get("max_results") or 200)
        try:
            expression = re.compile(query) if bool(a.get("regex")) else None
        except re.error as exc:
            return ToolResult(
                success=False,
                error=f"invalid regex: {exc}",
                recovery_hints=[
                    "Fix the pattern, or drop regex=true to search for the literal text."
                ],
            )
        scan = partial(tree.search, query, pattern, limit, expression)
        output = await asyncio.get_event_loop().run_in_executor(None, scan)
        return _ok_capped(output, session_key=self._session_key)

    async def _t_repo_map(self, a: dict) -> ToolResult:
        root = self._resolve(str(a["path"])) if a.get("path") else self._cwd
        outline = partial(
            WorkspaceTree(root.resolve(), self._SKIP_DIRS).outline,
            int(a.get("max_files") or 200),
        )
        output = await asyncio.get_event_loop().run_in_executor(None, outline)
        return _ok_capped(output, session_key=self._session_key)

    async def _t_bash(self, a: dict, *, timeout: float | None = None) -> ToolResult:
        from gideon.security import security
        from gideon.security.sandbox import wrap_argv

        if timeout is None:
            try:
                requested = float(a.get("timeout") or _BASH_TIMEOUT)
            except (TypeError, ValueError):
                requested = _BASH_TIMEOUT
            timeout = max(1.0, min(requested, _BASH_TIMEOUT_MAX))
        command = str(a["command"])
        checks = (
            (
                security.is_sensitive_bash_command,
                "This command touches a sensitive credential path. Use a non-credential path or a different approach.",
                False,
            ),
            (
                _denied_bash_reason,
                "This command matches a credential-exfiltration denylist. Use a read-only alternative or a different approach.",
                True,
            ),
        )
        for screen, recovery, is_pattern in checks:
            reason = screen(command)
            if reason:
                return ToolResult(
                    success=False,
                    error=(
                        f"Blocked: command matches denied pattern {reason!r}"
                        if is_pattern
                        else reason
                    ),
                    recovery_hints=[recovery],
                )
        from gideon.automation.triggers import handoff

        offer = handoff.detect(command)
        if offer is not None:
            return ToolResult(
                success=False, error=offer.reason, recovery_hints=[handoff.HANDOFF_HINT]
            )
        wrapped, cleanup = wrap_argv(["bash", "-lc", command], mode=self._sandbox_mode)
        try:
            from gideon.security.sandbox import PROFILE_TOOL, create_subprocess_limited

            process = await create_subprocess_limited(
                *wrapped,
                profile=PROFILE_TOOL,
                cwd=str(self._cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                captured = await ShellCapture.collect(process, timeout)
            except asyncio.TimeoutError:
                return ToolResult(
                    success=False,
                    error=f"command timed out after {timeout:.0f}s",
                    recovery_hints=[
                        "The command exceeded the time limit. Narrow its scope or run it in the background."
                    ],
                )
        finally:
            if cleanup:
                try:
                    Path(cleanup).unlink(missing_ok=True)
                except OSError:
                    pass
        if captured.returncode == 0:
            return _ok_capped(
                captured.output, content_type="log", session_key=self._session_key
            )
        projection = project_output(
            captured.output, cap=_MAX_OUTPUT_CHARS, content_type="log"
        )
        metadata = {"content_type": projection.content_type}
        if projection.truncated and self._session_key:
            reference = result_store.store_result(
                self._session_key, captured.output, content_type="log"
            )
            if reference:
                metadata["raw_ref"] = reference
        result = ToolResult(
            success=False,
            error=f"exit {captured.returncode}:\n{projection.text}",
            recovery_hints=[
                f"The command exited non-zero ({captured.returncode}). Read the error output above, fix the cause, and retry — or try a different approach."
            ],
        )
        result.truncated, result.original_length = (
            projection.truncated,
            projection.original_length,
        )
        result.metadata = metadata
        return result

    async def _t_post_to_inbox(self, a: dict) -> ToolResult:
        content = str(a.get("message", "")).strip()
        if not content:
            return ToolResult(
                success=False, error="post_to_inbox requires a non-empty 'message'"
            )
        category = str(a.get("kind", "notification")).strip().lower()
        category = (
            category
            if category in {"notification", "question", "fyi"}
            else "notification"
        )
        from gideon.integrations.inbox_providers.native_source import post_to_inbox

        def publish():
            parameters: dict = dict(
                kind=category,
                sender_name=self._agent or "agent",
                context=str(a.get("context", "")) or None,
                reply_target=self._session_key,
            )
            return post_to_inbox(content, **parameters)

        saved = await asyncio.get_event_loop().run_in_executor(None, publish)
        if saved is not None:
            return ToolResult(
                success=True, output=f"posted to inbox ({category}) — item {saved.id}"
            )
        return ToolResult(success=False, error="inbox sink unavailable")

    async def _t_knowledge_search(self, a: dict) -> ToolResult:
        query = str(a.get("query", "")).strip()
        if not query:
            return ToolResult(success=False, error="knowledge_search requires 'query'")
        limit = app_tools.integer_option(a, "limit", 8)

        def search():
            from gideon.cognition.knowledge import (
                get_knowledge_embedder,
                get_knowledge_store,
            )
            from gideon.cognition.knowledge.retrieval import HybridRetriever

            library = get_knowledge_store()
            embedding = get_knowledge_embedder()
            retriever = HybridRetriever(
                library,
                embedder=(
                    embedding.embed if embedding and embedding.is_available() else None
                ),
            )
            matches = retriever.search(query, limit=limit) or []
            rows = []
            for match in matches[:limit]:
                item = library.get_item(match["id"])
                if not item:
                    continue
                locator = []
                if match.get("section"):
                    locator.append(str(match["section"]))
                if match.get("line_range"):
                    first, last = match["line_range"][0], match["line_range"][1]
                    locator.append(f"lines {first}-{last}")
                citation = f" [{' · '.join(locator)}]" if locator else ""
                excerpt = _kn_redact(_kn_snippet(item))
                detail = " — " + excerpt if excerpt else ""
                rows.append(
                    f"- [{item.get('type', 'note')}] {_kn_redact(_kn_title(item))} (id={match['id']}){citation}{detail}"
                )
            return "\n".join(rows) or "(no matching knowledge items)"

        rendered = await asyncio.get_event_loop().run_in_executor(None, search)
        return _ok_capped(rendered, session_key=self._session_key)

    async def _t_knowledge_create(self, a: dict) -> ToolResult:
        try:
            draft = app_tools.KnowledgeDraft.parse(a)
        except app_tools.InputIssue as exc:
            return ToolResult(success=False, error=str(exc), recovery_hints=exc.hints)
        from gideon.cognition.knowledge import get_knowledge_store

        identifier, created = draft.save(get_knowledge_store())
        if created:
            _enrich_in_background(identifier)
            message = f"created knowledge {draft.kind} (enriching in background) — id {identifier}"
        else:
            message = f"knowledge bookmark already saved — id {identifier}"
        return ToolResult(success=True, output=message)

    async def _t_knowledge_get(self, a: dict) -> ToolResult:
        identifier = str(a.get("id", "")).strip()
        if not identifier:
            return ToolResult(success=False, error="knowledge_get requires 'id'")

        def retrieve():
            from gideon.cognition.knowledge import get_knowledge_store

            item = get_knowledge_store().get_item(identifier)
            return (
                app_tools.KnowledgeDocument(item).render(_kn_title, _kn_redact)
                if item
                else ""
            )

        rendered = await asyncio.get_event_loop().run_in_executor(None, retrieve)
        if rendered:
            return _ok_capped(rendered, session_key=self._session_key)
        return ToolResult(
            success=False,
            error=f"knowledge item {identifier!r} not found",
            recovery_hints=[
                "Use knowledge_search to find the correct item id, then retry knowledge_get."
            ],
        )

    async def _t_knowledge_update(self, a: dict) -> ToolResult:
        identifier = str(a.get("id", "")).strip()
        if not identifier:
            return ToolResult(success=False, error="knowledge_update requires 'id'")
        try:
            mutation = app_tools.KnowledgePatch.parse(a)
        except app_tools.InputIssue as exc:
            return ToolResult(success=False, error=str(exc), recovery_hints=exc.hints)

        def apply():
            from gideon.cognition.knowledge import get_knowledge_store

            return mutation.apply(get_knowledge_store(), identifier)

        outcome = await asyncio.get_event_loop().run_in_executor(None, apply)
        if outcome == "not_found":
            return ToolResult(
                success=False,
                error=f"knowledge item {identifier!r} not found",
                recovery_hints=["Use knowledge_search to find the correct item id."],
            )
        if outcome == "journal_locked":
            return ToolResult(
                success=False,
                error="knowledge_update: this journal entry is immutable — its creation day has passed",
                recovery_hints=[
                    "A journal's body can't be edited after its creation day. You can still update tags, is_pinned, or is_archived."
                ],
            )
        if mutation.reingest:
            _enrich_in_background(identifier)
        message = (
            f"updated knowledge item {identifier} ({', '.join(mutation.fields)})"
            if mutation.fields
            else f"no change to {identifier} — gist_language only applies to gist items"
        )
        return ToolResult(success=True, output=message)

    async def _t_knowledge_structural(self, a: dict) -> ToolResult:
        verb = str(a.get("verb", "")).strip()
        allowed = self._structural_verbs()
        if not verb or verb not in allowed:
            error = (
                "knowledge_structural requires 'verb'"
                if not verb
                else f"knowledge_structural: unknown verb {verb!r}"
            )
            return ToolResult(
                success=False,
                error=error,
                recovery_hints=[f"One of: {', '.join(allowed)}."],
            )
        options: dict = {
            field: str(a.get(field, "") or "").strip()
            for field in ("origin", "since", "rank_query")
        }
        options.update(
            depth=app_tools.integer_option(a, "depth", 1),
            limit=app_tools.integer_option(a, "limit", 25),
        )
        if verb in {"links_to", "depends_on", "tag_subtree"} and not options["origin"]:
            label = "a tag name" if verb == "tag_subtree" else "an item id"
            return ToolResult(
                success=False,
                error=f"knowledge_structural: verb {verb!r} requires 'origin' ({label})",
                recovery_hints=[
                    "Use knowledge_search to find the item id, then retry."
                ],
            )
        if verb == "changed_since" and not options["since"]:
            return ToolResult(
                success=False,
                error="knowledge_structural: verb 'changed_since' requires 'since' (ISO timestamp)",
            )

        def traverse():
            from gideon.cognition.knowledge import (
                get_knowledge_embedder,
                get_knowledge_store,
            )
            from gideon.cognition.knowledge.structural import (
                StructuralRetriever,
                render_answer,
            )

            embedding = get_knowledge_embedder()
            retriever = StructuralRetriever(
                get_knowledge_store(),
                embedder=(
                    embedding.embed if embedding and embedding.is_available() else None
                ),
            )
            answer = retriever.query(verb, **options)
            return _kn_redact(render_answer(answer, limit=options["limit"]))

        rendered = await asyncio.get_event_loop().run_in_executor(None, traverse)
        return _ok_capped(rendered, session_key=self._session_key)

    async def _t_knowledge_stats(self, a: dict) -> ToolResult:
        def inspect():
            from gideon.cognition.knowledge import get_knowledge_store

            return get_knowledge_store().corpus_overview()

        overview = await asyncio.get_event_loop().run_in_executor(None, inspect)
        if not overview.get("total"):
            return ToolResult(success=True, output="Knowledge library is empty.")
        rows = [
            f"Knowledge library: {overview['total']} items, {overview['entities']} entities."
        ]
        rows.append(
            "By type: "
            + ", ".join(
                f"{kind}: {count}" for kind, count in overview["by_type"].items()
            )
        )
        tags = ", ".join(
            f"{entry['tag']} ({entry['count']})" for entry in overview["top_tags"]
        )
        rows.append("Top tags: " + (tags or "(none)"))
        return ToolResult(success=True, output="\n".join(rows))

    async def _t_log_decision(self, a: dict) -> ToolResult:
        from gideon.cognition.decisions import DecisionError, log_decision

        def persist():
            text: dict = {
                field: str(a.get(field, ""))
                for field in ("summary", "content", "expectation")
            }
            return log_decision(
                **text,
                confidence=a.get("confidence"),
                domain=str(a.get("domain", "other") or "other"),
                review_horizon=str(a.get("review_horizon", "") or ""),
                tags=(
                    list(map(str, a["tags"]))
                    if isinstance(a.get("tags"), list)
                    else None
                ),
                enqueue=_enrich_in_background,
            )

        try:
            row = await asyncio.get_event_loop().run_in_executor(None, persist)
        except DecisionError as exc:
            return ToolResult(success=False, error=f"log_decision: {exc}")
        return ToolResult(success=True, output=app_tools.DecisionDocument.created(row))

    async def _t_decision_list(self, a: dict) -> ToolResult:
        from gideon.cognition.decisions import DecisionError, list_decisions

        request = partial(
            list_decisions,
            status=str(a.get("status", "") or ""),
            domain=str(a.get("domain", "") or ""),
            limit=app_tools.integer_option(a, "limit", 25),
        )
        try:
            rows = await asyncio.get_event_loop().run_in_executor(None, request)
        except DecisionError as exc:
            return ToolResult(success=False, error=f"decision_list: {exc}")
        if rows:
            return _ok_capped(
                app_tools.DecisionDocument.listing(rows), session_key=self._session_key
            )
        return ToolResult(success=True, output="(no matching decisions)")

    async def _t_decision_resolve(self, a: dict) -> ToolResult:
        from gideon.cognition.decisions import DecisionError, resolve_decision

        identifier = str(a.get("id", "")).strip()
        if not identifier:
            return ToolResult(success=False, error="decision_resolve requires 'id'")
        request = partial(
            resolve_decision,
            identifier,
            outcome=str(a.get("outcome", "")),
            grade=str(a.get("grade", "")),
            enqueue=_enrich_in_background,
        )
        try:
            row = await asyncio.get_event_loop().run_in_executor(None, request)
        except DecisionError as exc:
            return ToolResult(success=False, error=f"decision_resolve: {exc}")
        return ToolResult(success=True, output=app_tools.DecisionDocument.resolved(row))

    @staticmethod
    def _task_line(t) -> str:
        return app_tools.TaskDocument(t).line()

    async def _t_task_create(self, a: dict) -> ToolResult:
        from gideon.engine.tasks import reconcile, registry

        title = str(a.get("title", "")).strip()
        if not title:
            return ToolResult(success=False, error="task_create requires 'title'")
        try:
            created = await registry.create_task(
                title=title, **app_tools.task_fields(a, updating=False)
            )
        except reconcile.DependencyCycleError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                recovery_hints=["Remove the dependency that closes the loop."],
            )
        summary = self._task_line(created)
        return ToolResult(success=True, output=f"created task {created.id}: {summary}")

    async def _t_task_list(self, a: dict) -> ToolResult:
        from gideon.engine.tasks import registry

        filters: dict = {
            field: a.get(field) or None
            for field in ("status", "project", "task_list_id")
        }
        tasks, count = await registry.list_all_tasks(
            **filters, limit=app_tools.integer_option(a, "limit", 25)
        )
        if not tasks:
            return ToolResult(success=True, output="(no matching tasks)")
        heading = f"{count} task(s)"
        if count > len(tasks):
            heading += f", showing {len(tasks)}"
        lines = [f"{heading}:", *("- " + self._task_line(task) for task in tasks)]
        return _ok_capped("\n".join(lines), session_key=self._session_key)

    async def _t_task_get(self, a: dict) -> ToolResult:
        from gideon.engine.tasks import registry

        identifier = str(a.get("id", "")).strip()
        if not identifier:
            return ToolResult(success=False, error="task_get requires 'id'")
        task = await registry.get_task(identifier)
        if task:
            document = app_tools.TaskDocument(task).detail(self._task_line(task))
            return _ok_capped(document, session_key=self._session_key)
        return ToolResult(success=False, error=f"no task with id {identifier!r}")

    async def _t_task_update(self, a: dict) -> ToolResult:
        from gideon.engine.tasks import reconcile, registry

        identifier = str(a.get("id", "")).strip()
        if not identifier:
            return ToolResult(success=False, error="task_update requires 'id'")
        try:
            fields = app_tools.task_fields(a, updating=True)
        except app_tools.InputIssue as exc:
            return ToolResult(success=False, error=str(exc), recovery_hints=exc.hints)
        if not fields:
            return ToolResult(success=False, error="task_update: nothing to change")
        try:
            changed = await registry.update_task(identifier, **fields)
        except (reconcile.DependencyCycleError, ValueError) as exc:
            hint = (
                "Remove the dependency that closes the loop."
                if isinstance(exc, reconcile.DependencyCycleError)
                else "Complete the exit criteria before marking the task done."
            )
            return ToolResult(success=False, error=str(exc), recovery_hints=[hint])
        if changed:
            return ToolResult(
                success=True, output=f"updated {changed.id}: {self._task_line(changed)}"
            )
        return ToolResult(success=False, error=f"no task with id {identifier!r}")

    async def _t_task_ready(self, a: dict) -> ToolResult:
        from gideon.engine.tasks import registry

        scope: dict = {
            field: a.get(field) or None for field in ("project", "task_list_id")
        }
        ready = await registry.ready_tasks(**scope)
        if ready:
            rows = [
                "Ready to start:",
                *("- " + self._task_line(task) for task in ready),
            ]
            return _ok_capped("\n".join(rows), session_key=self._session_key)
        return ToolResult(success=True, output="(no ready tasks — all blocked or done)")

    async def _t_task_search(self, a: dict) -> ToolResult:
        from gideon.engine.tasks import registry

        filters: dict = {
            destination: a[source] if isinstance(a.get(source), list) else None
            for source, destination in (
                ("status", "statuses"),
                ("priority", "priorities"),
                ("tags", "tags"),
            )
        }
        matches, count = await registry.search_tasks(
            **filters,
            query=str(a.get("query", "")),
            project=a.get("project") or None,
            sort_by=str(a.get("sort_by", "relevance")),
            limit=app_tools.integer_option(a, "limit", 25),
        )
        if not matches:
            return ToolResult(success=True, output="(no matching tasks)")
        lines = [
            f"{count} match(es):",
            *("- " + self._task_line(task) for task in matches),
        ]
        return _ok_capped("\n".join(lines), session_key=self._session_key)

    async def _t_project_create(self, a: dict) -> ToolResult:
        from gideon.engine.tasks.hierarchy import HierarchyStore

        name = str(a.get("name", "")).strip()
        if not name:
            return ToolResult(success=False, error="project_create requires 'name'")

        def persist():
            store = HierarchyStore()
            parameters: dict = {
                "name": name,
                "agent_instructions_template": str(
                    a.get("agent_instructions_template", "")
                ),
            }
            return store.create_project(**parameters)

        try:
            row = await asyncio.get_event_loop().run_in_executor(None, persist)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True, output=f"created project '{row.name}' (id={row.id})"
        )

    async def _t_project_list(self, a: dict) -> ToolResult:
        from gideon.engine.tasks.hierarchy import HierarchyStore

        def browse():
            store = HierarchyStore()
            lines = []
            for project in store.list_projects():
                lists = store.list_task_lists(project_id=project.id)
                labels = [f"- {project.name} (id={project.id})"]
                if project.is_builtin_project():
                    labels.append(" [default]")
                if lists:
                    labels.append(
                        " — lists: " + ", ".join(entry.name for entry in lists)
                    )
                lines.append("".join(labels))
            return "\n".join(lines)

        rendered = await asyncio.get_event_loop().run_in_executor(None, browse)
        return _ok_capped(rendered, session_key=self._session_key)

    async def _t_task_list_create(self, a: dict) -> ToolResult:
        from gideon.engine.tasks.hierarchy import HierarchyStore

        name = str(a.get("name", "")).strip()
        if not name:
            return ToolResult(success=False, error="task_list_create requires 'name'")

        def persist():
            destination = {
                key: str(a.get(key, "")) for key in ("project_id", "project_name")
            }
            return HierarchyStore().create_task_list(
                name=name, **destination, repeatable=bool(a.get("repeatable", False))
            )

        try:
            row = await asyncio.get_event_loop().run_in_executor(None, persist)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True,
            output=f"created task list '{row.name}' (id={row.id}, project_id={row.project_id})",
        )


def create_platform_tools_provider(
    config: dict | None = None,
) -> "NativeBuiltinToolProvider":
    settings: dict = {
        "categories": PLATFORM_CATEGORIES,
        "provider_name": "gideon-filesystem",
        "display": "Filesystem & Shell Tools",
    }
    return NativeBuiltinToolProvider(**settings)


def _make_app_category_provider(category: str):
    def factory(config: dict | None = None) -> "NativeBuiltinToolProvider":
        provider, display = APP_CATEGORY_PROVIDERS[category]
        return NativeBuiltinToolProvider(
            categories=frozenset((category,)), provider_name=provider, display=display
        )

    return factory


create_knowledge_tools_provider = _make_app_category_provider("knowledge")
create_tasks_tools_provider = _make_app_category_provider("tasks")
create_project_tools_provider = _make_app_category_provider("projects")
create_inbox_tools_provider = _make_app_category_provider("inbox")
