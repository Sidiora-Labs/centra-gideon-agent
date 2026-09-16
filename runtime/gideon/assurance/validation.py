"""Centralized input/output validation for MCP tools and API endpoints.

All tool inputs from untrusted sources (LLM, end user, other MCP tools)
are validated here before execution.  Responses are sanitized and
truncated before returning to callers.

Provides:
- Schema validation with type enforcement
- Length and size limits
- Unicode normalization and hidden character stripping
- Allow-list approach for enums and key patterns
- Semantic/business logic checks (positive numbers, valid timestamps, etc.)
- Response truncation to prevent resource exhaustion
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from gideon.core.errors import AgentError

MAX_TOOL_NAME_LEN = 256
MAX_SHORT_STRING = 500
MAX_MEDIUM_STRING = 5_000
MAX_LONG_STRING = 50_000
MAX_RESPONSE_LEN = 100_000

ALLOWED_LESSON_CATEGORIES = frozenset({"tool", "preference", "knowledge"})

ALLOWED_SCHEDULE_KINDS = frozenset({"every", "cron", "at"})

ALLOWED_HOOK_EVENTS = frozenset(
    {
        "AgentSpawn",
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PreResponse",
        "PostResponse",
        "MemoryWrite",
        "ContextCompact",
        "SubagentSpawn",
        "TaskComplete",
        "ApprovalRequest",
        "Error",
        "SessionEnd",
        "Stop",
    }
)

_AGENT_NAME_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}[a-zA-Z0-9]$|^[a-zA-Z0-9]$"
)

CHANNEL_ID_RE = re.compile(r"^[CDGW][A-Z0-9]+$")
CHANNEL_MAX_LEN = 20
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{1,19}$")
USER_MAX_LEN = 20

_MESSAGE_TS_RE = re.compile(r"^\d+\.\d+$")

_JOB_ID_RE = re.compile(r"^[a-f0-9]{1,16}$")

_HIDDEN_CATEGORIES = frozenset(
    {
        "Cc",
        "Cf",
        "Co",
        "Cs",
    }
)

_ALLOWED_CONTROL = frozenset({"\n", "\r", "\t"})


class ValidationError(Exception):
    """Raised when input validation fails.

    Optionally carries a PLATFORM-LEGIBILITY §2 :class:`~gideon.core.errors.AgentError`
    so a tool-arg rejection reaches the model as WHAT/WHY/FIX + did-you-mean
    ``suggestions`` (the allowed set) instead of an opaque one-liner. When present,
    the string form of the exception IS the envelope's ``render()`` — one message,
    no divergence. Absent → the plain ``field: message`` string as before.
    """

    def __init__(
        self, field: str, message: str, agent_error: "AgentError | None" = None
    ) -> None:
        self.field = field
        self.message = message
        self.agent_error = agent_error
        super().__init__(
            agent_error.render() if agent_error is not None else f"{field}: {message}"
        )


_Type = type

ItemType = _Type | tuple[_Type, ...]


@dataclass
class FieldSpec:
    """Declarative field specification for validation."""

    name: str
    type: ItemType
    required: bool = False
    max_len: int = 0
    min_val: float | None = None
    max_val: float | None = None
    allowed: frozenset[str] | None = None
    enum_error_code: str = "ERR_TOOL_ARG_INVALID"
    pattern: re.Pattern[str] | None = None
    default: Any = None
    item_type: ItemType | None = None
    item_max_len: int = 0
    item_pattern: re.Pattern[str] | None = (
        None  # for list fields: regex for each string element
    )
    max_items: int = 0


@dataclass
class ToolSchema:
    """Schema for a tool's input arguments."""

    tool_name: str
    fields: list[FieldSpec] = field(default_factory=list)


def validate_field(value: Any, spec: FieldSpec) -> Any:
    """Validate and normalize a single field value. Returns cleaned value."""
    if value is None:
        if spec.required:
            raise ValidationError(spec.name, "required")
        return spec.default

    _wants_int = spec.type is int or (
        isinstance(spec.type, tuple) and spec.type == (int,)
    )
    _wants_num = _wants_int or spec.type in (float, (int, float), (float, int))
    if _wants_num and not isinstance(value, bool):
        if isinstance(value, float) and _wants_int and value.is_integer():
            value = int(value)
        elif isinstance(value, str):
            _s = value.strip()
            try:
                if _wants_int:
                    value = int(_s, 10) if _s.lstrip("-").isdigit() else int(float(_s))
                else:
                    value = float(_s)
            except (ValueError, TypeError):
                pass

    if not isinstance(value, spec.type):
        raise ValidationError(
            spec.name,
            f"expected {spec.type.__name__ if isinstance(spec.type, type) else spec.type}, "
            f"got {type(value).__name__}",
        )

    if isinstance(value, str):
        value = sanitize_string(value)
        if not value and spec.required:
            raise ValidationError(spec.name, "required (empty after sanitization)")
        if spec.max_len and len(value) > spec.max_len:
            raise ValidationError(spec.name, f"exceeds max length {spec.max_len}")
        if spec.allowed and value not in spec.allowed:
            allowed = tuple(sorted(spec.allowed))
            raise ValidationError(
                spec.name,
                f"must be one of: {', '.join(allowed)}",
                AgentError(
                    code=spec.enum_error_code,
                    what=f"the {spec.name!r} argument value {value!r} is not allowed",
                    why=f"{spec.name!r} accepts only a fixed set of values",
                    fix=f"set {spec.name!r} to one of the allowed values",
                    suggestions=allowed,
                ),
            )
        if spec.pattern and value and not spec.pattern.match(value):
            raise ValidationError(spec.name, "invalid format")

    if isinstance(value, (int, float)):
        if spec.min_val is not None and value < spec.min_val:
            raise ValidationError(spec.name, f"must be >= {spec.min_val}")
        if spec.max_val is not None and value > spec.max_val:
            raise ValidationError(spec.name, f"must be <= {spec.max_val}")

    if isinstance(value, list):
        if spec.max_items and len(value) > spec.max_items:
            raise ValidationError(spec.name, f"exceeds max items {spec.max_items}")
        if spec.item_type:
            for i, item in enumerate(value):
                if not isinstance(item, spec.item_type):
                    wanted = (
                        spec.item_type
                        if isinstance(spec.item_type, tuple)
                        else (spec.item_type,)
                    )
                    names = " or ".join(t.__name__ for t in wanted)
                    raise ValidationError(
                        spec.name,
                        f"item[{i}]: expected {names}, got {type(item).__name__}",
                    )
                if isinstance(item, str):
                    item = sanitize_string(item)
                    value[i] = item
                    if spec.item_max_len and len(item) > spec.item_max_len:
                        raise ValidationError(
                            spec.name,
                            f"item[{i}]: exceeds max length {spec.item_max_len}",
                        )
                    if (
                        spec.item_pattern
                        and item
                        and not spec.item_pattern.fullmatch(item)
                    ):
                        raise ValidationError(spec.name, f"item[{i}]: invalid format")

    return value


def validate_tool_args(args: dict[str, Any], schema: ToolSchema) -> dict[str, Any]:
    """Validate all tool arguments against a schema. Returns cleaned args dict."""
    if not isinstance(args, dict):
        raise ValidationError("args", "must be a JSON object")

    cleaned: dict[str, Any] = {}
    known_fields = {s.name for s in schema.fields}

    for key in args:
        if key not in known_fields:
            raise ValidationError(key, f"unknown field for tool '{schema.tool_name}'")

    for spec in schema.fields:
        if spec.name in args:
            raw = args[spec.name]
            cleaned[spec.name] = validate_field(raw, spec)
        elif spec.required:
            cleaned[spec.name] = validate_field(None, spec)
        elif spec.default is not None:
            cleaned[spec.name] = spec.default

    return cleaned


def strip_hidden_unicode(text: str) -> str:
    """Remove hidden Unicode characters (zero-width, directional overrides, etc.).

    Preserves normal whitespace (\\n, \\r, \\t) and all visible characters.
    """
    return "".join(
        ch
        for ch in text
        if ch in _ALLOWED_CONTROL or unicodedata.category(ch) not in _HIDDEN_CATEGORIES
    )


def normalize_unicode(text: str) -> str:
    """NFC-normalize Unicode text to canonical form."""
    return unicodedata.normalize("NFC", text)


def sanitize_string(text: str) -> str:
    """Full sanitization pipeline: normalize → strip hidden chars → strip edges."""
    text = normalize_unicode(text)
    text = strip_hidden_unicode(text)
    return text.strip()


def sanitize_response(text: str, max_len: int = MAX_RESPONSE_LEN) -> str:
    """Sanitize and truncate a tool response before returning to caller."""
    text = sanitize_string(text)
    if len(text) > max_len:
        text = text[:max_len] + "\n…[response truncated]"
    return text


def validate_jsonrpc_request(req: dict[str, Any]) -> tuple[str, Any, dict[str, Any]]:
    """Validate a JSON-RPC 2.0 request envelope.

    Returns (method, id, params). Raises ValidationError on invalid structure.
    """
    if not isinstance(req, dict):
        raise ValidationError("request", "must be a JSON object")
    if req.get("jsonrpc") not in ("2.0", None):
        raise ValidationError("jsonrpc", "must be '2.0'")

    method = req.get("method")
    if method is not None and not isinstance(method, str):
        raise ValidationError("method", "must be a string")

    req_id = req.get("id")
    params = req.get("params", {})
    if not isinstance(params, dict):
        params = {}

    return method or "", req_id, params


SPAWN_RUN_SCHEMA = ToolSchema(
    tool_name="subagent_run",
    fields=[
        FieldSpec("task", str, max_len=MAX_MEDIUM_STRING),
        FieldSpec(
            "tasks",
            list,
            item_type=(str, dict),
            item_max_len=MAX_MEDIUM_STRING,
        ),
        FieldSpec("agent", str, max_len=MAX_SHORT_STRING, pattern=_AGENT_NAME_RE),
        FieldSpec(
            "agents",
            list,
            item_type=str,
            item_max_len=MAX_SHORT_STRING,
            item_pattern=_AGENT_NAME_RE,
        ),
        FieldSpec("max_turns", int, min_val=0, max_val=200),
        # in DelegationSupervisor.spawn.
        FieldSpec("cwd", str, max_len=MAX_MEDIUM_STRING),
    ],
)

LEARN_ADD_SCHEMA = ToolSchema(
    tool_name="memory_remember",
    fields=[
        FieldSpec("rule", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec(
            "category", str, allowed=ALLOWED_LESSON_CATEGORIES, default="knowledge"
        ),
        FieldSpec("negative", str, max_len=MAX_SHORT_STRING),
    ],
)

LEARN_REMOVE_SCHEMA = ToolSchema(
    tool_name="memory_forget",
    fields=[
        FieldSpec("query", str, required=True, max_len=MAX_SHORT_STRING),
    ],
)

MEMORY_RECALL_SCHEMA = ToolSchema(
    tool_name="memory_recall",
    fields=[
        FieldSpec("query", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("deep", bool),
    ],
)

TRIAGE_RULES_SCHEMA = ToolSchema(
    tool_name="triage_rules",
    fields=[
        FieldSpec(
            "action", str, required=True, allowed=frozenset({"list", "add", "revoke"})
        ),
        FieldSpec("pattern", str, max_len=MAX_SHORT_STRING),
        FieldSpec("verdict", str, allowed=frozenset({"approve", "deny"})),
        FieldSpec("id", str, max_len=MAX_SHORT_STRING),
        FieldSpec(
            "scope", str, allowed=frozenset({"global", "workspace"}), default="global"
        ),
        FieldSpec("expires_at", str, max_len=MAX_SHORT_STRING),
    ],
)

SPAWN_STATUS_SCHEMA = ToolSchema(
    tool_name="subagent_status",
    fields=[
        FieldSpec("agent_id", str, required=True, max_len=64),
    ],
)

SPAWN_LIST_SCHEMA = ToolSchema(tool_name="subagent_list")

FILE_SEND_SCHEMA = ToolSchema(
    tool_name="notify_attachment",
    fields=[
        FieldSpec("path", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("description", str, max_len=MAX_SHORT_STRING),
    ],
)

AUTONUDGE_STOP_SCHEMA = ToolSchema(
    tool_name="loop_nudge_stop",
    fields=[
        FieldSpec("reason", str, max_len=MAX_SHORT_STRING),
    ],
)

_SUGGEST_TEMPLATE_DECISIONS = frozenset({"observe", "accepted", "declined"})

SUGGEST_TEMPLATE_SCHEMA = ToolSchema(
    tool_name="suggest_template",
    fields=[
        FieldSpec("shape", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("decision", str, max_len=16, allowed=_SUGGEST_TEMPLATE_DECISIONS),
    ],
)


from gideon.workspace.artifacts.models import (  # noqa: E402
    ALLOWED_KINDS as _ALLOWED_KINDS,
)

_ARTIFACT_KINDS = frozenset(_ALLOWED_KINDS)

ARTIFACT_SAVE_SCHEMA = ToolSchema(
    tool_name="artifact_save",
    fields=[
        FieldSpec("name", str, required=True, max_len=200),
        FieldSpec("content", str, max_len=MAX_LONG_STRING),
        FieldSpec("kind", str, max_len=20, allowed=_ARTIFACT_KINDS),
        FieldSpec("slug", str, max_len=80),
        FieldSpec("description", str, max_len=2000),
        FieldSpec("tags", list, item_type=str, item_max_len=64, max_items=16),
        FieldSpec("content_file", str, max_len=MAX_SHORT_STRING),
    ],
)

ARTIFACT_GET_SCHEMA = ToolSchema(
    tool_name="artifact_get",
    fields=[
        FieldSpec("slug", str, required=True, max_len=80),
        FieldSpec("version", int, min_val=1),
    ],
)

ARTIFACT_UPDATE_SCHEMA = ToolSchema(
    tool_name="artifact_update",
    fields=[
        FieldSpec("slug", str, required=True, max_len=80),
        FieldSpec("content", str, max_len=MAX_LONG_STRING),
        FieldSpec("description", str, max_len=2000),
        FieldSpec("tags", list, item_type=str, item_max_len=64, max_items=16),
        FieldSpec("content_file", str, max_len=MAX_SHORT_STRING),
    ],
)

ARTIFACT_LIST_SCHEMA = ToolSchema(
    tool_name="artifact_list",
    fields=[
        FieldSpec("tag", str, max_len=64),
        FieldSpec("kind", str, max_len=20, allowed=_ARTIFACT_KINDS),
        FieldSpec("q", str, max_len=MAX_SHORT_STRING),
    ],
)

IMAGE_GENERATE_SCHEMA = ToolSchema(
    tool_name="image_generate",
    fields=[
        FieldSpec("prompt", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("size", str, max_len=32),
        FieldSpec("name", str, max_len=200),
        FieldSpec("edit_artifact", str, max_len=80),
    ],
)

VISUALIZE_SCHEMA = ToolSchema(
    tool_name="visualize",
    fields=[
        FieldSpec("data", (dict, list, str, int, float, bool), required=True),
        FieldSpec("hint", str, max_len=MAX_MEDIUM_STRING),
        FieldSpec("title", str, max_len=200),
    ],
)

PROMPT_RENDER_SCHEMA = ToolSchema(
    tool_name="prompt_render",
    fields=[
        FieldSpec("prompt_id", str, required=True, max_len=128),
        FieldSpec("vars", dict, default={}),
    ],
)

PROJECT_CONTEXT_REVIEW_SCHEMA = ToolSchema(
    tool_name="project_context_review",
    fields=[
        FieldSpec("items", list, required=True, item_type=dict, max_items=20),
        FieldSpec("project_id", str, max_len=128),
    ],
)

_TILE_SIZES = frozenset({"s", "m", "l", "full"})
DASHBOARD_TILE_PROPOSE_SCHEMA = ToolSchema(
    tool_name="dashboard_tile_propose",
    fields=[
        FieldSpec("slug", str, required=True, max_len=200),
        FieldSpec("size", str, max_len=8, allowed=_TILE_SIZES),
        FieldSpec("view_id", str, max_len=64),
    ],
)

_WF_RUN_ID_RE = re.compile(r"^[a-f0-9]{8}$")
_WF_DEF_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SESSION_ID_RE = re.compile(r"^[\w\-][\w\-.]{0,127}$")
_WF_MODES = frozenset({"blocking", "background"})
_WF_RIGOR = frozenset({"minimal", "standard", "deep"})

WORKFLOW_AUTHOR_SCHEMA = ToolSchema(
    tool_name="workflow_author",
    fields=[
        FieldSpec("name", str, required=True, max_len=63, pattern=_WF_DEF_NAME_RE),
        FieldSpec("root", dict, required=True),
        FieldSpec("description", str, max_len=2000),
        FieldSpec("inputs", dict, default={}),
        FieldSpec("tags", list, item_type=str, item_max_len=64, max_items=16),
        FieldSpec("save", bool, default=True),
    ],
)

WORKFLOW_PLAN_SCHEMA = ToolSchema(
    tool_name="workflow_plan",
    fields=[
        FieldSpec("goal", str, max_len=MAX_LONG_STRING),
        FieldSpec("rigor", str, max_len=16, allowed=_WF_RIGOR),
        FieldSpec("template", str, max_len=63, pattern=_WF_DEF_NAME_RE),
        FieldSpec("project_id", str, max_len=MAX_SHORT_STRING),
        FieldSpec("source_session_id", str, max_len=128, pattern=_SESSION_ID_RE),
    ],
)

WORKFLOW_LIST_DEFS_SCHEMA = ToolSchema(
    tool_name="workflow_list_defs",
    fields=[
        FieldSpec("tag", str, max_len=64),
        FieldSpec("source", str, max_len=16),
    ],
)

WORKFLOW_GET_DEF_SCHEMA = ToolSchema(
    tool_name="workflow_get_def",
    fields=[FieldSpec("name", str, required=True, max_len=63, pattern=_WF_DEF_NAME_RE)],
)

WORKFLOW_DELETE_DEF_SCHEMA = ToolSchema(
    tool_name="workflow_delete_def",
    fields=[FieldSpec("name", str, required=True, max_len=63, pattern=_WF_DEF_NAME_RE)],
)

WORKFLOW_START_SCHEMA = ToolSchema(
    tool_name="workflow_start",
    fields=[
        FieldSpec("name", str, required=True, max_len=63, pattern=_WF_DEF_NAME_RE),
        FieldSpec("inputs", dict, default={}),
        FieldSpec("mode", str, max_len=16, allowed=_WF_MODES),
        FieldSpec("project_id", str, max_len=MAX_SHORT_STRING),
        FieldSpec("idempotency_key", str, max_len=128),
    ],
)

WORKFLOW_STATUS_SCHEMA = ToolSchema(
    tool_name="workflow_status",
    fields=[FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE)],
)

WORKFLOW_OBSERVE_SCHEMA = ToolSchema(
    tool_name="workflow_observe",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec("duration_ms", int, min_val=100, max_val=30_000),
    ],
)

WORKFLOW_OUTPUT_SCHEMA = ToolSchema(
    tool_name="workflow_output",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec("node_id", str, required=True, max_len=128),
    ],
)

WORKFLOW_EDIT_SCHEMA = ToolSchema(
    tool_name="workflow_edit",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec("ops", list, required=True, item_type=dict, max_items=50),
        FieldSpec("expect_version", int, min_val=1),
        FieldSpec("confirm_cascade", bool, default=False),
        FieldSpec("preview_only", bool, default=False),
    ],
)

WORKFLOW_SKIP_SCHEMA = ToolSchema(
    tool_name="workflow_skip",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec(
            "node_ids",
            list,
            required=True,
            item_type=str,
            item_max_len=128,
            max_items=50,
        ),
    ],
)

WORKFLOW_REWIND_SCHEMA = ToolSchema(
    tool_name="workflow_rewind",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec("node_id", str, required=True, max_len=128),
        FieldSpec("redo_effects", bool, default=False),
        FieldSpec("force", bool, default=False),
    ],
)

WORKFLOW_RUN_FROM_SCHEMA = ToolSchema(
    tool_name="workflow_run_from",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec("node_id", str, required=True, max_len=128),
    ],
)

WORKFLOW_FORK_SCHEMA = ToolSchema(
    tool_name="workflow_fork",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec("checkpoint_id", str, max_len=16),
        FieldSpec("note", str, max_len=2000),
    ],
)

WORKFLOW_PAUSE_SCHEMA = ToolSchema(
    tool_name="workflow_pause",
    fields=[FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE)],
)

WORKFLOW_CANCEL_SCHEMA = ToolSchema(
    tool_name="workflow_cancel",
    fields=[FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE)],
)

WORKFLOW_RESUME_SCHEMA = ToolSchema(
    tool_name="workflow_resume",
    fields=[
        FieldSpec("run_id", str, required=True, max_len=16, pattern=_WF_RUN_ID_RE),
        FieldSpec("answer", object),
        FieldSpec("resume_token", str, max_len=64),
        FieldSpec("always_allow", bool, default=False),
    ],
)

WORKFLOW_AUDIT_SCHEMA = ToolSchema(
    tool_name="workflow_audit",
    fields=[FieldSpec("dry_run", bool, default=True)],
)

WORKFLOW_MANIFEST_SCHEMA = ToolSchema(tool_name="workflow_manifest")

MCP_WORKFLOW_SCHEMAS: dict[str, ToolSchema] = {
    "workflow_author": WORKFLOW_AUTHOR_SCHEMA,
    "workflow_plan": WORKFLOW_PLAN_SCHEMA,
    "workflow_list_defs": WORKFLOW_LIST_DEFS_SCHEMA,
    "workflow_get_def": WORKFLOW_GET_DEF_SCHEMA,
    "workflow_delete_def": WORKFLOW_DELETE_DEF_SCHEMA,
    "workflow_start": WORKFLOW_START_SCHEMA,
    "workflow_status": WORKFLOW_STATUS_SCHEMA,
    "workflow_observe": WORKFLOW_OBSERVE_SCHEMA,
    "workflow_output": WORKFLOW_OUTPUT_SCHEMA,
    "workflow_edit": WORKFLOW_EDIT_SCHEMA,
    "workflow_skip": WORKFLOW_SKIP_SCHEMA,
    "workflow_rewind": WORKFLOW_REWIND_SCHEMA,
    "workflow_run_from": WORKFLOW_RUN_FROM_SCHEMA,
    "workflow_fork": WORKFLOW_FORK_SCHEMA,
    "workflow_pause": WORKFLOW_PAUSE_SCHEMA,
    "workflow_cancel": WORKFLOW_CANCEL_SCHEMA,
    "workflow_resume": WORKFLOW_RESUME_SCHEMA,
    "workflow_audit": WORKFLOW_AUDIT_SCHEMA,
    "workflow_manifest": WORKFLOW_MANIFEST_SCHEMA,
}


SKILL_INVOKE_SCHEMA = ToolSchema(
    tool_name="skill_invoke",
    fields=[
        FieldSpec("name", str, required=True, max_len=128),
    ],
)

# ``ProcedureLibrary.read_resource``, never by a shape check here.
SKILL_RESOURCE_SCHEMA = ToolSchema(
    tool_name="skill_resource",
    fields=[
        FieldSpec("skill", str, required=True, max_len=128),
        FieldSpec("path", str, required=True, max_len=256),
    ],
)

ARTIFACT_VERSIONS_SCHEMA = ToolSchema(
    tool_name="artifact_versions",
    fields=[FieldSpec("slug", str, required=True, max_len=80)],
)

ARTIFACT_DELETE_SCHEMA = ToolSchema(
    tool_name="artifact_delete",
    fields=[FieldSpec("slug", str, required=True, max_len=80)],
)


ALLOWED_HOOK_PROVIDERS = frozenset(
    {
        "bash",
        "webhook",
        "run-script",
        "notify",
        "send-message",
        "create-task",
        "invoke-agent",
        "run-prompt",
        "selfqa-triage",
        "selfqa-file-finding",
        "selfqa-evidence",
        "selfqa-commit-watch",
        "triage-digest",
        "inbox-op",
        "run-workflow",
        "call-app-route",
        "notification-digest",
        "usage-recap",
        "self-remediation",
        "source-digest",
        "identity-report",
        "artifact-update",
        "knowledge-report",
        "knowledge-persist",
        "knowledge-retrieve",
        "knowledge-health",
        "knowledge-consolidate",
        "knowledge-gaps",
        "render-report",
        "knowledge-propose",
        "artifact_inspect",
        "browse",
        "net-fetch",
        "second-opinion",
        "best-of-n",
        "check-work",
        "a2a-call",
    }
)

HOOK_CREATE_SCHEMA = ToolSchema(
    tool_name="hook_create",
    fields=[
        FieldSpec("name", str, required=True, max_len=200),
        FieldSpec(
            "provider",
            str,
            required=True,
            allowed=ALLOWED_HOOK_PROVIDERS,
            enum_error_code="ERR_HOOK_PROVIDER_UNKNOWN",
        ),
        FieldSpec("provider_config", dict, required=True),
        FieldSpec("event", str, required=True, allowed=ALLOWED_HOOK_EVENTS),
        FieldSpec("matcher", str, max_len=500, default=""),
        FieldSpec("timeout", int, min_val=1, max_val=300, default=30),
        FieldSpec("enabled", bool, default=True),
    ],
)

HOOK_UPDATE_SCHEMA = ToolSchema(
    tool_name="hook_update",
    fields=[
        FieldSpec("name", str, max_len=200),
        FieldSpec(
            "provider",
            str,
            allowed=ALLOWED_HOOK_PROVIDERS,
            enum_error_code="ERR_HOOK_PROVIDER_UNKNOWN",
        ),
        FieldSpec("provider_config", dict),
        FieldSpec("event", str, allowed=ALLOWED_HOOK_EVENTS),
        FieldSpec("matcher", str, max_len=500),
        FieldSpec("timeout", int, min_val=1, max_val=300),
        FieldSpec("enabled", bool),
    ],
)


FILE_READ_SCHEMA = ToolSchema(
    tool_name="file_read",
    fields=[
        FieldSpec(
            "path",
            str,
            required=True,
            max_len=4096,
            pattern=re.compile(r"^[~/][-\w.@~/ ]+$"),
        ),
    ],
)

FILE_WRITE_SCHEMA = ToolSchema(
    tool_name="file_write",
    fields=[
        FieldSpec(
            "path",
            str,
            required=True,
            max_len=4096,
            pattern=re.compile(r"^[~/][-\w.@~/ ]+$"),
        ),
        FieldSpec("content", str, required=True, max_len=512000),
    ],
)

SEND_MESSAGE_SCHEMA = ToolSchema(
    tool_name="notify",
    fields=[
        FieldSpec("text", str, required=True, max_len=MAX_MEDIUM_STRING),
        FieldSpec("title", str, max_len=MAX_SHORT_STRING),
        FieldSpec("blocks", list, item_type=dict, max_items=50),
        FieldSpec("channel", str, max_len=CHANNEL_MAX_LEN, pattern=CHANNEL_ID_RE),
        FieldSpec("user", str, max_len=USER_MAX_LEN, pattern=USER_ID_RE),
        FieldSpec("unfurl_links", bool),
        FieldSpec("unfurl_media", bool),
        FieldSpec("thread_ts", str, max_len=30, pattern=_MESSAGE_TS_RE),
        FieldSpec("reply_broadcast", bool),
        FieldSpec(
            "session",
            str,
            max_len=MAX_SHORT_STRING,
            pattern=re.compile(r"^(origin|channel)$"),
        ),
        FieldSpec(
            "caller_session",
            str,
            max_len=MAX_SHORT_STRING,
            pattern=re.compile(r"^(cron:[a-zA-Z0-9]+)?$"),
        ),
    ],
)

WAIT_SCHEMA = ToolSchema(
    tool_name="wait",
    fields=[
        FieldSpec("seconds", int, required=True, min_val=60, max_val=1800),
        FieldSpec("reason", str, required=True, max_len=MAX_SHORT_STRING),
    ],
)

REGISTER_HOOK_SCHEMA = ToolSchema(
    tool_name="hook_register",
    fields=[
        FieldSpec("hook_id", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("context_summary", str, required=True, max_len=MAX_MEDIUM_STRING),
    ],
)


_EMOJI_NAME_RE = re.compile(
    r"^[a-zA-Z0-9+][a-zA-Z0-9_+\-]{0,98}[a-zA-Z0-9]$|^[a-zA-Z0-9+]$"
)

ADD_REACTION_SCHEMA = ToolSchema(
    tool_name="add_reaction",
    fields=[
        FieldSpec(
            "channel",
            str,
            required=True,
            max_len=CHANNEL_MAX_LEN,
            pattern=CHANNEL_ID_RE,
        ),
        FieldSpec("timestamp", str, required=True, max_len=30, pattern=_MESSAGE_TS_RE),
        FieldSpec("reaction", str, required=True, max_len=100, pattern=_EMOJI_NAME_RE),
    ],
)

BEST_OF_N_SCHEMA = ToolSchema(
    tool_name="best_of_n",
    fields=[
        FieldSpec("prompt", str, required=True, max_len=MAX_MEDIUM_STRING),
        FieldSpec("n", int),
        FieldSpec("criteria", str, max_len=MAX_MEDIUM_STRING),
    ],
)


MCP_CORE_SCHEMAS: dict[str, ToolSchema] = {
    "best_of_n": BEST_OF_N_SCHEMA,
    "subagent_run": SPAWN_RUN_SCHEMA,
    "subagent_list": SPAWN_LIST_SCHEMA,
    "subagent_status": SPAWN_STATUS_SCHEMA,
    "memory_remember": LEARN_ADD_SCHEMA,
    "memory_forget": LEARN_REMOVE_SCHEMA,
    "memory_recall": MEMORY_RECALL_SCHEMA,
    "triage_rules": TRIAGE_RULES_SCHEMA,
    "notify": SEND_MESSAGE_SCHEMA,
    "wait": WAIT_SCHEMA,
    "hook_register": REGISTER_HOOK_SCHEMA,
    "notify_attachment": FILE_SEND_SCHEMA,
    "loop_nudge_stop": AUTONUDGE_STOP_SCHEMA,
    "suggest_template": SUGGEST_TEMPLATE_SCHEMA,
    "artifact_save": ARTIFACT_SAVE_SCHEMA,
    "artifact_get": ARTIFACT_GET_SCHEMA,
    "artifact_update": ARTIFACT_UPDATE_SCHEMA,
    "artifact_list": ARTIFACT_LIST_SCHEMA,
    "artifact_versions": ARTIFACT_VERSIONS_SCHEMA,
    "artifact_delete": ARTIFACT_DELETE_SCHEMA,
    "image_generate": IMAGE_GENERATE_SCHEMA,
    "visualize": VISUALIZE_SCHEMA,
    "prompt_render": PROMPT_RENDER_SCHEMA,
    "skill_invoke": SKILL_INVOKE_SCHEMA,
    "skill_resource": SKILL_RESOURCE_SCHEMA,
    "project_context_review": PROJECT_CONTEXT_REVIEW_SCHEMA,
    "dashboard_tile_propose": DASHBOARD_TILE_PROPOSE_SCHEMA,
}


MCP_HUB_SCHEMAS: dict[str, ToolSchema] = {}

MCP_AUTOMATION_SCHEMAS: dict[str, ToolSchema] = {
    "set_onetime_task": ToolSchema(
        tool_name="set_onetime_task",
        fields=[
            FieldSpec("name", str, required=True, max_len=MAX_SHORT_STRING),
            FieldSpec("when", str, required=True, max_len=MAX_MEDIUM_STRING),
            FieldSpec("message", str, required=True, max_len=MAX_MEDIUM_STRING),
            FieldSpec("resume_run_id", str, max_len=MAX_SHORT_STRING),
            FieldSpec("ttl_secs", (int, float), min_val=0, max_val=90 * 86400),
        ],
    ),
    "set_recurring_task": ToolSchema(
        tool_name="set_recurring_task",
        fields=[
            FieldSpec("name", str, required=True, max_len=MAX_SHORT_STRING),
            FieldSpec("cadence", str, required=True, max_len=MAX_MEDIUM_STRING),
            FieldSpec("message", str, required=True, max_len=MAX_MEDIUM_STRING),
            FieldSpec("resume_run_id", str, max_len=MAX_SHORT_STRING),
            FieldSpec("ttl_secs", (int, float), min_val=0, max_val=90 * 86400),
        ],
    ),
    "automation_create": ToolSchema(
        tool_name="automation_create",
        fields=[
            FieldSpec("name", str, required=True, max_len=MAX_SHORT_STRING),
            FieldSpec("when", str, max_len=MAX_MEDIUM_STRING),
            FieldSpec("message", str, max_len=MAX_MEDIUM_STRING),
            FieldSpec("kind", str, max_len=32, pattern=re.compile(r"^[a-z_]*$")),
            FieldSpec("spec", dict),
        ],
    ),
    "automation_list": ToolSchema(
        tool_name="automation_list",
        fields=[
            FieldSpec("kind", str, max_len=32, pattern=re.compile(r"^[a-z_]*$")),
            FieldSpec(
                "state", str, max_len=10, allowed=frozenset({"", "active", "paused"})
            ),
        ],
    ),
    "automation_update": ToolSchema(
        tool_name="automation_update",
        fields=[
            FieldSpec("id", str, required=True, max_len=96),
            FieldSpec("patch", dict, required=True),
        ],
    ),
    "automation_pause": ToolSchema(
        tool_name="automation_pause",
        fields=[FieldSpec("id", str, required=True, max_len=96)],
    ),
    "automation_resume": ToolSchema(
        tool_name="automation_resume",
        fields=[FieldSpec("id", str, required=True, max_len=96)],
    ),
    "automation_run": ToolSchema(
        tool_name="automation_run",
        fields=[
            FieldSpec("id", str, required=True, max_len=96),
            FieldSpec("dry_run", bool),
        ],
    ),
    "automation_history": ToolSchema(
        tool_name="automation_history",
        fields=[
            FieldSpec("id", str, required=True, max_len=96),
            FieldSpec("n", int, min_val=1, max_val=500),
        ],
    ),
    "automation_delete": ToolSchema(
        tool_name="automation_delete",
        fields=[
            FieldSpec("id", str, required=True, max_len=96),
            FieldSpec("confirm", bool),
        ],
    ),
    "automation_delete_all": ToolSchema(
        tool_name="automation_delete_all",
        fields=[FieldSpec("confirm", bool)],
    ),
}


@dataclass
class McpTextContent:
    """Type-safe MCP TextContent response — the only content type our tools return."""

    type: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "text": self.text}


def build_tool_response(text: str, max_len: int = MAX_RESPONSE_LEN) -> dict[str, Any]:
    """Build a validated, sanitized MCP tools/call response.

    Returns the ``result`` payload for a JSON-RPC response:
    ``{"content": [{"type": "text", "text": "..."}]}``

    This is the single exit point for all tool responses — ensures every
    response conforms to the MCP TextContent schema and is sanitized.
    """
    text = sanitize_response(text, max_len)
    content = McpTextContent(type="text", text=text)
    return {"content": [content.to_dict()]}


def validate_jsonrpc_response(resp: dict[str, Any]) -> dict[str, Any]:
    """Validate a JSON-RPC 2.0 response envelope before writing to stdout.

    Ensures: has ``jsonrpc``, ``id``, and either ``result`` or ``error``.
    """
    if not isinstance(resp, dict):
        raise ValidationError("response", "must be a JSON object")
    if "id" not in resp:
        raise ValidationError("response", "missing id")
    if "result" not in resp and "error" not in resp:
        raise ValidationError("response", "must have result or error")
    resp["jsonrpc"] = "2.0"
    return resp


def validate_api_body(body: Any, max_size: int = 100_000) -> dict[str, Any]:
    """Validate a parsed JSON request body from aiohttp."""
    if not isinstance(body, dict):
        raise ValidationError("body", "must be a JSON object")
    raw = str(body)
    if len(raw) > max_size:
        raise ValidationError("body", f"exceeds max size {max_size}")
    return body


def validate_string_field(
    body: dict[str, Any],
    field_name: str,
    *,
    required: bool = False,
    max_len: int = MAX_MEDIUM_STRING,
    allowed: frozenset[str] | None = None,
) -> str:
    """Extract and validate a string field from a request body."""
    val = body.get(field_name)
    if val is None:
        if required:
            raise ValidationError(field_name, "required")
        return ""
    if not isinstance(val, str):
        raise ValidationError(field_name, "must be a string")
    val = sanitize_string(val)
    if not val and required:
        raise ValidationError(field_name, "required (empty after sanitization)")
    if max_len and len(val) > max_len:
        raise ValidationError(field_name, f"exceeds max length {max_len}")
    if allowed and val not in allowed:
        raise ValidationError(
            field_name, f"must be one of: {', '.join(sorted(allowed))}"
        )
    return val


_AUQ_MAX_QUESTIONS = 10
_AUQ_MAX_OPTIONS = 12
_AUQ_TEXT_CAP = 2000
_AUQ_LABEL_CAP = 400


def validate_ask_user_question(tool_input: Any) -> list[dict[str, Any]]:
    """Normalize a Claude Code ``AskUserQuestion`` tool input into a render list.

    Input schema (top level): ``{"questions": [{"question": str, "header"?: str,
    "multiSelect"?: bool, "options": [{"label": str, "description"?: str}]}]}``.
    Returns ``[{question, header, multiSelect, options: [{label, description}]}]``
    with all strings truncated and counts capped. Raises :class:`ValidationError`
    on a structurally unusable payload (no question with options).
    """
    if not isinstance(tool_input, dict):
        raise ValidationError("ask_user_question", "tool input must be an object")
    raw_questions = tool_input.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValidationError("ask_user_question", "questions must be a non-empty list")

    out: list[dict[str, Any]] = []
    for rq in raw_questions[:_AUQ_MAX_QUESTIONS]:
        if not isinstance(rq, dict):
            continue
        q_text = str(rq.get("question", "")).strip()[:_AUQ_TEXT_CAP]
        if not q_text:
            continue
        header = str(rq.get("header", "")).strip()[:_AUQ_LABEL_CAP]
        multi = bool(rq.get("multiSelect", False))
        raw_options = rq.get("options")
        options: list[dict[str, str]] = []
        if isinstance(raw_options, list):
            for ro in raw_options[:_AUQ_MAX_OPTIONS]:
                if isinstance(ro, dict):
                    label = str(ro.get("label", "")).strip()[:_AUQ_LABEL_CAP]
                    desc = str(ro.get("description", "")).strip()[:_AUQ_TEXT_CAP]
                elif isinstance(ro, str):
                    label, desc = ro.strip()[:_AUQ_LABEL_CAP], ""
                else:
                    continue
                if label:
                    options.append({"label": label, "description": desc})
        if not options:
            continue
        out.append(
            {
                "question": q_text,
                "header": header,
                "multiSelect": multi,
                "options": options,
            }
        )

    if not out:
        raise ValidationError("ask_user_question", "no usable questions in payload")
    return out
