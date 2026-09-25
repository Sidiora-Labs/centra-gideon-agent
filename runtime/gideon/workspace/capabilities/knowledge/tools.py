"""Native personal knowledge tools over the runtime's bound action services."""

import json
from jsonschema import ValidationError, validate

from gideon.cognition.memory_service import MemoryService
from gideon.core.config.loader import config_dir
from gideon.workspace.capabilities.knowledge.typed import TypedCapture
from gideon.workspace.capabilities.knowledge.archive import ConversationArchive
from gideon.workspace.capabilities.knowledge.topics import TrackedTopics, SOURCE_TYPES
from gideon.engine import session_restrictions
from gideon.integrations.action_providers.services import get_action_services
from gideon.integrations.mcp_core import get_current_session_key
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.workspace.capabilities.knowledge.anniversaries import anniversaries, source_record
from gideon.workspace.capabilities.knowledge.capture import CaptureError, CaptureInbox


_ID = {"type": "string", "minLength": 1, "maxLength": 128}
_REQUEST = {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,128}$"}
_PAGING = {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "offset": {"type": "integer", "minimum": 0, "maximum": 1000000}}
_TYPE_FIELDS = {"capture_id": _ID, "kind": {"enum": ["person", "project", "idea", "admin", "memory"]}, "fields": {"type": "object"}}
_ARCHIVE_FIELDS = {"format": {"enum": ["chatgpt"]}, "content": {"type": "string", "minLength": 1, "maxLength": 524288}}
_TOOLS = {
    "knowledge_topic_list": ("List saved keyword topics.", False, _PAGING, []),
    "knowledge_topic_save": ("Create or revise a keyword topic over chosen canonical personal sources; preserves mutation retry receipts.", True, {"request_id": _REQUEST, "id": _ID, "revision": {"type": "integer", "minimum": 1}, "name": {"type": "string", "minLength": 1, "maxLength": 100}, "query": {"type": "string", "minLength": 1, "maxLength": 300}, "source_types": {"type": "array", "items": {"enum": list(SOURCE_TYPES)}, "minItems": 1, "uniqueItems": True}}, ["request_id", "name", "query", "source_types"]),
    "knowledge_topic_delete": ("Delete a saved topic without deleting its source records; requires current revision.", True, {"id": _ID, "request_id": _REQUEST, "revision": {"type": "integer", "minimum": 1}}, ["id", "request_id", "revision"]),
    "knowledge_topic_matches": ("Refresh literal keyword matches across current canonical sources; explicitly reports unavailable or scan-limited sources.", False, {**_PAGING, "id": _ID}, ["id"]),
    "knowledge_archive_preview": ("Review an exported conversation archive without executing its contents; report invalid branches and unsupported parts.", False, _ARCHIVE_FIELDS, list(_ARCHIVE_FIELDS)),
    "knowledge_archive_commit": ("Import selected reviewed archive conversations into canonical knowledge notes, preserving the original archive and source dates. Retry same request_id after interruption.", True, {**_ARCHIVE_FIELDS, "request_id": _REQUEST, "source_digest": _ID, "conversation_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 500, "uniqueItems": True}}, [*_ARCHIVE_FIELDS, "request_id", "source_digest", "conversation_ids"]),
    "knowledge_archive_list": ("List conversation archive import receipts and exact canonical source links.", False, _PAGING, []),
    "knowledge_type_preview": ("Review capture classification and unsupported fields before canonical import.", False, _TYPE_FIELDS, list(_TYPE_FIELDS)),
    "knowledge_type_commit": ("Import the reviewed capture into the canonical destination once; preserve the preview and request_id when retrying.", True, {**_TYPE_FIELDS, "request_id": _REQUEST, "preview_id": _ID, "revision": {"type": "integer", "minimum": 1}}, [*_TYPE_FIELDS, "request_id", "preview_id", "revision"]),
    "knowledge_type_list": ("List immutable reviewed import receipts with original provenance and destination links.", False, _PAGING, []),
    "knowledge_anniversaries": ("Revisit prior-year notes, journals and episodic memories by local calendar date.", False,
        {**_PAGING, "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}, "timezone": {"type": "string", "maxLength": 100}}, []),
    "knowledge_anniversary_source": ("Read the exact original source identified by an anniversary result.", False,
        {"source_type": {"enum": ["note", "journal", "fleeting", "memory"]}, "source_id": _ID}, ["source_type", "source_id"]),
    "knowledge_capture_list": ("List original captures in reverse chronology with bounded pagination.", False, _PAGING, []),
    "knowledge_capture_get": ("Read original capture provenance, transcription status and routing history.", False, {"id": _ID}, ["id"]),
    "knowledge_capture_text": ("Preserve the user's exact input in the capture inbox; retry with the same request_id and text.", True,
        {"request_id": _REQUEST, "text": {"type": "string", "minLength": 1, "maxLength": 100000}}, ["request_id", "text"]),
    "knowledge_capture_route": ("Apply reviewed content to a note, journal or fleeting idea, preserving original capture and revisions. Use current revision and retry the same request_id after interruption.", True,
        {"id": _ID, "request_id": _REQUEST, "revision": {"type": "integer", "minimum": 1}, "destination": {"enum": ["note", "journal", "fleeting"]},
         "title": {"type": "string", "minLength": 1, "maxLength": 300}, "content": {"type": "string", "minLength": 1, "maxLength": 100000}},
        ["id", "request_id", "revision", "destination", "title", "content"]),
    "knowledge_capture_transcribe": ("Transcribe preserved voice input using the configured speech adapter; returns an honest unavailable state when disconnected.", True, {"id": _ID}, ["id"]),
}


def _schema(name):
    _, _, properties, required = _TOOLS[name]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _public(value):
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, str):
        value, _ = redact_credentials(value)
        value, _ = redact_exfiltration_urls(value)
    return value


class KnowledgeCapabilityTools(ToolProvider):
    def __init__(self, services=None):
        self._services = services or get_action_services()
        self._store = self._services.state.knowledge_store if self._services else None
        self._inbox = None
        self._typed = None
        self._archive = None
        self._topics = None
        self._home = config_dir()

    @property
    def name(self):
        return "gideon-personal-knowledge"

    @property
    def display_name(self):
        return "Personal Knowledge"

    @property
    def connected(self):
        return self._services is not None or get_action_services() is not None

    async def list_tools(self):
        return [ToolDefinition(name=name, description=definition[0], provider=self.name, parameters=_schema(name),
            requires_approval=definition[1], risk_level=RiskLevel.CAUTION if definition[1] else RiskLevel.SAFE)
            for name, definition in _TOOLS.items()]

    def _bound(self, writes):
        if self._services is None:
            self._services = get_action_services()
            self._store = self._services.state.knowledge_store if self._services else None
        if self._services is None:
            raise CaptureError("Runtime services are unavailable; start the gateway.", 503)
        key = get_current_session_key()
        if not key:
            raise CaptureError("An active session is required for personal knowledge tools", 403)
        state = self._services.state
        session = state._sessions.get(key.split(":", 1)[-1])
        if session_restrictions.is_temporary(key) or (session and session.blocks_reads):
            raise CaptureError("This temporary session cannot read personal knowledge", 403)
        if writes and (session_restrictions.is_restricted(key) or key in state._restricted_keys or (session and session.is_restricted)):
            raise CaptureError("This session cannot write personal knowledge", 403)
        return state

    async def invoke(self, tool_name, arguments):
        if tool_name not in _TOOLS:
            return ToolResult(success=False, error="Unknown personal knowledge tool")
        try:
            validate(arguments, _schema(tool_name))
            state = self._bound(_TOOLS[tool_name][1])
            builder = state.context_builder
            memory = builder.memory if builder else getattr(state, "_standalone_memory", None)
            archive = getattr(memory, "vector_store", None)
            service = MemoryService.over_vector_store(archive) if archive is not None else None
            if tool_name.startswith("knowledge_topic_"):
                if self._topics is None:
                    self._topics = TrackedTopics(self._store, service, self._home)
                if tool_name == "knowledge_topic_list":
                    result = self._topics.list(**arguments)
                elif tool_name == "knowledge_topic_save":
                    result = self._topics.save(arguments)
                elif tool_name == "knowledge_topic_matches":
                    result = self._topics.matches(arguments["id"], **{key: value for key, value in arguments.items() if key != "id"})
                else:
                    result = self._topics.delete(arguments["id"], {key: value for key, value in arguments.items() if key != "id"})
            elif tool_name.startswith("knowledge_archive_"):
                if self._archive is None:
                    self._archive = ConversationArchive(self._store, home=self._home)
                if tool_name == "knowledge_archive_preview":
                    result = self._archive.preview(arguments)
                elif tool_name == "knowledge_archive_commit":
                    result = self._archive.commit(arguments)
                else:
                    result = self._archive.list(**arguments)
            elif tool_name.startswith("knowledge_type_"):
                if self._typed is None:
                    self._typed = TypedCapture(self._store, service, self._home)
                if tool_name == "knowledge_type_preview":
                    result = self._typed.preview(arguments)
                elif tool_name == "knowledge_type_commit":
                    result = await self._typed.commit(arguments)
                else:
                    result = self._typed.list(**arguments)
            elif tool_name == "knowledge_anniversaries":
                result = anniversaries(self._store, service, **arguments)
            elif tool_name == "knowledge_anniversary_source":
                result = source_record(self._store, service, **arguments)
                if result is None:
                    raise CaptureError("Source not found", 404)
            else:
                if self._inbox is None:
                    self._inbox = CaptureInbox(self._store, home=self._home)
                if tool_name == "knowledge_capture_list":
                    result = self._inbox.list(**arguments)
                elif tool_name == "knowledge_capture_get":
                    result = self._inbox.get(arguments["id"])
                elif tool_name == "knowledge_capture_text":
                    result = self._inbox.create(**arguments)
                elif tool_name == "knowledge_capture_route":
                    result = self._inbox.route(arguments["id"], {key: value for key, value in arguments.items() if key != "id"})
                else:
                    result = await self._inbox.transcribe(arguments["id"])
            unavailable = tool_name == "knowledge_capture_transcribe" and result["status"] in {"transcription_unavailable", "transcription_failed"}
            return ToolResult(success=not unavailable, output=json.dumps(_public(result), ensure_ascii=False),
                error=result["error"] if unavailable else "", metadata={"source": self.name},
                recovery_hints=["Configure a speech provider and retry the preserved capture."] if unavailable else [])
        except ValidationError as exc:
            return ToolResult(success=False, error=f"Invalid tool arguments: {exc.message}")
        except (CaptureError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc), metadata={"status": getattr(exc, "status", 400)})


def create_provider(config=None):
    if config:
        raise ValueError("Personal knowledge tools use bound runtime services, not selector configuration")
    return KnowledgeCapabilityTools()
