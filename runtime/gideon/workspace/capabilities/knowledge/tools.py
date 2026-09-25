"""Native personal knowledge tools over the runtime's bound action services."""

import json
from jsonschema import ValidationError, validate

from gideon.cognition.memory_service import MemoryService
from gideon.core.config.loader import config_dir
from gideon.workspace.capabilities.knowledge.typed import TypedCapture
from gideon.workspace.capabilities.knowledge.archive import ConversationArchive
from gideon.workspace.capabilities.knowledge.topics import TrackedTopics, SOURCE_TYPES
from gideon.workspace.capabilities.knowledge.idea_format import preview as idea_preview
from gideon.workspace.capabilities.knowledge.ideas import IdeaLists
from gideon.workspace.capabilities.knowledge.idea_schedule import IdeaSchedules, IdeaSyncActionProvider
from gideon.workspace.capabilities.knowledge.transcript_format import preview as transcript_preview
from gideon.workspace.capabilities.knowledge.videos import VideoIngests
from gideon.workspace.capabilities.knowledge.external_vaults import ExternalVaults
from gideon.workspace.capabilities.knowledge.journals import DateJournals
from gideon.workspace.capabilities.knowledge.reviews import ReviewService
from gideon.workspace.capabilities.knowledge.review_schedule import ReviewSchedules, ReviewActionProvider
from gideon.integrations.action_providers.registry import register_action_provider
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
_REVIEW_FIELDS = {"period": {"enum": ["daily", "weekly"]}, "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}, "timezone": {"type": "string", "maxLength": 100}}
_JOURNAL_FIELDS = {key: value for key, value in _REVIEW_FIELDS.items() if key != "period"}
_IDEA_CONTENT = {"type": "string", "minLength": 1, "maxLength": 262144}
_VIDEO_PREVIEW = {"url": {"type": "string", "maxLength": 2048}, "title": {"type": "string", "minLength": 1, "maxLength": 300}, "format": {"enum": ["vtt", "srt", "json"]}, "content": {"type": "string", "minLength": 1, "maxLength": 1048576}, "language": {"type": "string", "minLength": 1, "maxLength": 30}}
_TOOLS = {
    "knowledge_vault_list": ("List explicitly allowed external Markdown vault registrations and indexed note counts.", False, {}, []),
    "knowledge_vault_register": ("Register an existing Markdown directory beneath configured external_vault_roots.", True, {"name": {"type": "string", "minLength": 1, "maxLength": 200}, "path": {"type": "string", "minLength": 1, "maxLength": 1000}}, ["name", "path"]),
    "knowledge_vault_scan": ("Scan one registered vault and index canonical Knowledge references without recreating missing files.", True, {"id": _ID}, ["id"]),
    "knowledge_vault_read": ("Read an indexed vault note with its current conflict hash and canonical source link.", False, {"id": _ID, "path": {"type": "string", "minLength": 1, "maxLength": 1000}}, ["id", "path"]),
    "knowledge_vault_write": ("Atomically create or update a vault note when its expected content hash still matches.", True, {"id": _ID, "request_id": _REQUEST, "path": {"type": "string", "minLength": 1, "maxLength": 1000}, "content": {"type": "string", "maxLength": 1048576}, "expected_hash": {"type": "string", "maxLength": 128}}, ["id", "request_id", "path", "content", "expected_hash"]),
    "knowledge_vault_delete": ("Delete a vault note only when its expected content hash matches; archive the canonical reference.", True, {"id": _ID, "request_id": _REQUEST, "path": {"type": "string", "minLength": 1, "maxLength": 1000}, "expected_hash": _ID}, ["id", "request_id", "path", "expected_hash"]),
    "knowledge_vault_search": ("Search live indexed Markdown content within one registered vault.", False, {"id": _ID, "query": {"type": "string", "minLength": 1, "maxLength": 500}}, ["id", "query"]),
    "knowledge_vault_graph": ("Read resolved and unresolved wikilink edges within one registered vault.", False, {"id": _ID}, ["id"]),
    "knowledge_video_list": ("List durable public-video ingest jobs, progress and caption-reader availability.", False, _PAGING, []),
    "knowledge_video_get": ("Read one durable video ingest and its ordered progress events.", False, {"id": _ID}, ["id"]),
    "knowledge_video_preview": ("Parse user-supplied timed captions for review without fetching or writing.", False, _VIDEO_PREVIEW, list(_VIDEO_PREVIEW)),
    "knowledge_video_import": ("Save an unchanged reviewed supplied transcript with timestamp links and explicit user-supplied provenance.", True, {**_VIDEO_PREVIEW, "request_id": _REQUEST, "preview_id": _ID}, ["request_id", *_VIDEO_PREVIEW, "preview_id"]),
    "knowledge_video_fetch": ("Start guarded public YouTube caption or bounded artifact acquisition. This performs external reads and returns a durable job.", True, {"request_id": _REQUEST, "url": _VIDEO_PREVIEW["url"], "language": _VIDEO_PREVIEW["language"], "transcript": {"type": "boolean"}, "video": {"type": "boolean"}, "audio": {"type": "boolean"}}, ["request_id", "url", "language", "transcript", "video", "audio"]),
    "knowledge_video_cancel": ("Request cancellation of a running video acquisition.", True, {"id": _ID}, ["id"]),
    "knowledge_video_transcript": ("Read the stored canonical Markdown transcript for a completed or partial ingest.", False, {"id": _ID}, ["id"]),
    "knowledge_idea_list": ("List canonical idea lists and owned-vault availability.", False, {}, []),
    "knowledge_idea_preview": ("Review portable idea-list Markdown, preserving ordered ideas and reporting extra metadata.", False, {"content": _IDEA_CONTENT}, ["content"]),
    "knowledge_idea_import": ("Import reviewed idea-list Markdown into canonical collection/fleeting records with current expected_hash; use empty hash for a new list.", True, {"request_id": _REQUEST, "content": _IDEA_CONTENT, "preview_id": _ID, "expected_hash": {"type": "string", "maxLength": 128}}, ["request_id", "content", "preview_id", "expected_hash"]),
    "knowledge_idea_export": ("Export current ordered canonical ideas as portable Markdown.", False, {"id": _ID}, ["id"]),
    "knowledge_idea_sync": ("Synchronize an idea list through the existing opted-in owned two-way vault; reports conflicts and owner deletion.", True, {"id": _ID, "request_id": _REQUEST, "expected_hash": _ID}, ["id", "request_id", "expected_hash"]),
    "knowledge_idea_schedule": ("Opt into or disable recurring idea-list exchange using the existing interval scheduler; no external messaging.", True, {"id": _ID, "request_id": _REQUEST, "revision": {"type": "integer", "minimum": 1}, "enabled": {"type": "boolean"}, "minutes": {"type": "integer", "minimum": 5, "maximum": 1440}}, ["id", "request_id", "revision", "enabled", "minutes"]),
    "knowledge_journal_get": ("Open the canonical date-keyed journal and current revision/fingerprint.", False, _JOURNAL_FIELDS, list(_JOURNAL_FIELDS)),
    "knowledge_journal_draft": ("Draft actual daily note and current completed-task activity with exact citations; review before saving.", False, _JOURNAL_FIELDS, list(_JOURNAL_FIELDS)),
    "knowledge_journal_save": ("Save reviewed journal text using the current canonical revision and fingerprint. Use empty preview_id for user-only text; no generated prose.", True, {**_JOURNAL_FIELDS, "request_id": _REQUEST, "revision": {"type": "integer", "minimum": 0}, "fingerprint": {"type": "string", "maxLength": 128}, "preview_id": {"type": "string", "maxLength": 128}, "title": {"type": "string", "minLength": 1, "maxLength": 300}, "content": {"type": "string", "minLength": 1, "maxLength": 100000}}, [*_JOURNAL_FIELDS, "request_id", "revision", "fingerprint", "preview_id", "title", "content"]),
    "knowledge_review_preview": ("Preview actual daily or weekly obligations and recent activity with exact source links; completed activity uses updated_at rather than immutable completion events.", False, _REVIEW_FIELDS, list(_REVIEW_FIELDS)),
    "knowledge_review_save": ("Save a reviewed source snapshot and user reflection as a canonical knowledge note, rejecting changed sources.", True, {**_REVIEW_FIELDS, "request_id": _REQUEST, "preview_id": _ID, "reflection": {"type": "string", "maxLength": 100000}}, [*_REVIEW_FIELDS, "request_id", "preview_id", "reflection"]),
    "knowledge_review_list": ("List immutable saved review receipts and canonical note links.", False, _PAGING, []),
    "knowledge_review_schedules": ("List existing daily and weekly review clock schedules and their actual next fire.", False, {}, []),
    "knowledge_review_schedule_save": ("Create or revise a recurring review using the existing trigger scheduler; disable through enabled=false. No messaging or model output is generated.", True, {"request_id": _REQUEST, "id": _ID, "revision": {"type": "integer", "minimum": 1}, "period": {"enum": ["daily", "weekly"]}, "timezone": {"type": "string", "maxLength": 100}, "time": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"}, "weekday": {"type": "integer", "minimum": 0, "maximum": 6}, "enabled": {"type": "boolean"}}, ["request_id", "period", "timezone", "time", "weekday", "enabled"]),
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
        self._ideas = None
        self._videos = None
        self._vaults = None
        self._idea_schedules = None
        self._journals = None
        self._reviews = None
        self._review_schedules = None
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
            if tool_name.startswith("knowledge_vault_"):
                if self._vaults is None: self._vaults = ExternalVaults(self._store, self._home)
                identity = arguments.get("id")
                if tool_name == "knowledge_vault_list": result = self._vaults.list()
                elif tool_name == "knowledge_vault_register": result = self._vaults.register(arguments)
                elif tool_name == "knowledge_vault_scan": result = self._vaults.scan(identity)
                elif tool_name == "knowledge_vault_read": result = self._vaults.detail(identity, arguments["path"])
                elif tool_name == "knowledge_vault_write": result = self._vaults.write(identity, {key: value for key, value in arguments.items() if key != "id"})
                elif tool_name == "knowledge_vault_delete": result = self._vaults.delete(identity, {key: value for key, value in arguments.items() if key != "id"})
                elif tool_name == "knowledge_vault_search": result = self._vaults.search(identity, arguments["query"])
                else: result = self._vaults.graph(identity)
            elif tool_name.startswith("knowledge_video_"):
                if self._videos is None:
                    self._videos = VideoIngests(self._store, self._home)
                if tool_name == "knowledge_video_list":
                    result = self._videos.list(**arguments)
                elif tool_name == "knowledge_video_get":
                    result = self._videos.get(arguments["id"])
                elif tool_name == "knowledge_video_preview":
                    result = transcript_preview(arguments)
                elif tool_name == "knowledge_video_import":
                    result = self._videos.import_preview(arguments)
                elif tool_name == "knowledge_video_fetch":
                    result = self._videos.start_fetch(arguments, get_current_session_key())
                elif tool_name == "knowledge_video_cancel":
                    result = self._videos.cancel(arguments["id"])
                else:
                    result = self._videos.transcript(arguments["id"])
            elif tool_name.startswith("knowledge_idea_"):
                if self._ideas is None:
                    self._ideas = IdeaLists(self._store, self._home)
                    self._idea_schedules = IdeaSchedules(self._ideas)
                    register_action_provider(IdeaSyncActionProvider(self._idea_schedules))
                if tool_name == "knowledge_idea_list":
                    result = self._ideas.list()
                elif tool_name == "knowledge_idea_preview":
                    result = idea_preview(arguments['content'])
                elif tool_name == "knowledge_idea_import":
                    result = self._ideas.import_list(arguments)
                elif tool_name == "knowledge_idea_export":
                    result = self._ideas.export(arguments['id'])
                elif tool_name == "knowledge_idea_sync":
                    result = self._ideas.sync(arguments['id'], {key: value for key, value in arguments.items() if key != 'id'})
                else:
                    result = self._idea_schedules.save(arguments['id'], {key: value for key, value in arguments.items() if key != 'id'})
            elif tool_name.startswith("knowledge_journal_"):
                if self._journals is None:
                    self._journals = DateJournals(self._store, self._home)
                if tool_name == "knowledge_journal_get":
                    result = self._journals.get(**arguments)
                elif tool_name == "knowledge_journal_draft":
                    result = self._journals.draft(**arguments)
                else:
                    result = self._journals.save(arguments)
            elif tool_name.startswith("knowledge_review_"):
                if self._reviews is None:
                    self._reviews = ReviewService(self._store, self._home)
                    self._review_schedules = ReviewSchedules(self._reviews)
                    register_action_provider(ReviewActionProvider(self._review_schedules))
                if tool_name == "knowledge_review_preview":
                    result = self._reviews.preview(**arguments)
                elif tool_name == "knowledge_review_save":
                    result = self._reviews.save(arguments)
                elif tool_name == "knowledge_review_list":
                    result = self._reviews.list(**arguments)
                elif tool_name == "knowledge_review_schedules":
                    result = self._review_schedules.list()
                else:
                    result = self._review_schedules.save(arguments)
            elif tool_name.startswith("knowledge_topic_"):
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
