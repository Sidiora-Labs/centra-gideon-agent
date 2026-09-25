"""Native tool consumer of the authored story and narration services."""

import json

from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .narration import get_narration_jobs
from .navigation import NavigationReceipts
from .speech_owner import SpeechOwner
from .ambient import AmbientDisplay
from .avatar import AvatarStore
from .native_calls import get_native_calls
from .world_engine import WorldEngine
from .store import Conflict, ExperienceStore, NotFound

STRING = {"type": "string", "minLength": 1, "maxLength": 80}
REVISION = {"type": "integer", "minimum": 1}
STORY = {"type": "object", "description": "title, start_node, nodes [{id,text,kind:scene|ending,choices:[{id,label,target}]}]; revision required on edit", "required": ["title", "start_node", "nodes"]}
OPERATIONS = {
    "world_engine_get": ({}, False, "Read actual managed world engine readiness and version."),
    "world_engine_start": ({}, True, "Enable the operator-installed world engine through the existing app supervisor."),
    "world_engine_stop": ({}, True, "Disable the managed world engine through the existing app lifecycle."),
    "native_calls_get": ({}, False, "Read machine-local native call readiness and durable requests; no call is initiated."),
    "avatar_list": ({}, False, "Read published avatar variants and actual source availability."),
    "avatar_bundled": ({}, True, "Install the original authored robot asset and clip mapping."),
    "avatar_publish": ({"title": STRING, "artifact_slug": STRING, "artifact_version": REVISION, "clips": {"type":"object","description":"idle required; optional working, needs_input, waiting_approval, error, speaking; values must be real asset clip names"}}, True, "Publish an existing animated canonical model as an avatar variant."),
    "avatar_selection_get": ({}, False, "Read selected avatar and observed activity entity binding."),
    "avatar_select": ({"revision": REVISION,"avatar_id":{"type":["string","null"]},"entity_id":{"type":"string","maxLength":300}}, True, "Select a ready avatar and actual activity identity with expected revision."),
    "ambient_get": ({}, False, "Read current ambient projections, unavailable sources and display preferences."),
    "ambient_update": ({"revision": REVISION, "show_clock": {"type":"boolean"}, "font_scale": {"type":"integer","minimum":1,"maximum":3}, "idle_seconds": {"type":"integer","minimum":10,"maximum":300}}, True, "Save ambient presentation preferences with expected revision."),
    "speech_state": ({}, False, "Read proactive opt-in and audible owner expiry without lease credentials."),
    "story_list": ({}, False, "List authored stories."),
    "story_get": ({"id": STRING}, False, "Read an authored story and its revision."),
    "story_create": ({"story": STORY}, True, "Create a validated authored story graph."),
    "story_update": ({"id": STRING, "story": STORY}, True, "Update a graph with its expected story revision."),
    "story_delete": ({"id": STRING, "revision": REVISION}, True, "Delete a story; existing playthrough source revisions remain preserved."),
    "session_list": ({}, False, "List durable story playthroughs."),
    "session_get": ({"id": STRING}, False, "Read a playthrough and its exact source scene."),
    "session_start": ({"story_id": STRING, "story_revision": REVISION, "request_id": STRING}, True, "Start an idempotent playthrough of the given story revision."),
    "session_choose": ({"id": STRING, "choice_id": STRING, "revision": REVISION, "request_id": STRING}, True, "Choose a current scene option with optimistic revision and retry identity."),
    "narration_start": ({"id": STRING, "revision": REVISION, "request_id": STRING}, True, "Request source-bound speech using configured voice settings; unavailable is not generated speech."),
    "narration_get": ({"id": STRING}, False, "Read narration status and canonical audio reference, if ready."),
    "narration_cancel": ({"id": STRING}, True, "Cancel queued or active speech generation."),
    "navigation_list": ({}, False, "Read navigation receipts; requested is not browser acknowledgement."),
    "navigation_get": ({"id": STRING}, False, "Read whether the browser acknowledged a navigation action."),
}


class ExperienceTools(ToolProvider):
    name = "gideon-experience"
    display_name = "Experience"

    def __init__(self, store=None):
        self._store = store
        self._jobs = None
        self._native_calls = None

    @property
    def store(self):
        if self._store is None:
            self._store = ExperienceStore()
        return self._store

    @property
    def jobs(self):
        if self._jobs is None:
            self._jobs = get_narration_jobs(self.store)
        return self._jobs

    async def list_tools(self):
        return [ToolDefinition(name="experience_" + operation, provider=self.name, description=description,
                               parameters={"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False},
                               requires_approval=write, risk_level=RiskLevel.DESTRUCTIVE if operation == "story_delete" else RiskLevel.CAUTION if write else RiskLevel.SAFE)
                for operation, (fields, write, description) in OPERATIONS.items()]

    async def invoke(self, tool_name, arguments):
        operation = tool_name.removeprefix("experience_")
        if not tool_name.startswith("experience_") or operation not in OPERATIONS:
            return ToolResult(False, error="unknown experience operation")
        if not isinstance(arguments, dict) or set(arguments) != set(OPERATIONS[operation][0]):
            return ToolResult(False, error="arguments must match the declared operation fields")
        args = dict(arguments)
        key = args.pop("id", None)
        try:
            if operation.startswith("world_engine_"):
                engine = WorldEngine(self.store)
                result = await engine.status() if operation == "world_engine_get" else await engine.control(operation.removeprefix("world_engine_"), {})
                return ToolResult(success=True, output=json.dumps(result))
            if operation == "native_calls_get":
                if self._native_calls is None:
                    self._native_calls = get_native_calls(self.store)
                return ToolResult(success=True, output=json.dumps({"readiness": self._native_calls.readiness(), "requests": self._native_calls.list()}))
            if operation == "avatar_list": result = {"avatars": AvatarStore(self.store).list()}
            elif operation == "avatar_bundled": result = {"avatar": AvatarStore(self.store).bundled()}
            elif operation == "avatar_publish": result = {"avatar": AvatarStore(self.store).publish(args)}
            elif operation == "avatar_selection_get": result = {"selection": AvatarStore(self.store).selection()}
            elif operation == "avatar_select": result = {"selection": AvatarStore(self.store).select(args)}
            elif operation == "ambient_get": result = await AmbientDisplay(self.store).snapshot()
            elif operation == "ambient_update": result = {"preferences": AmbientDisplay(self.store).save(args)}
            elif operation == "speech_state": result = {"owner": SpeechOwner(self.store).state()}
            elif operation == "story_list": result = {"stories": self.store.stories()}
            elif operation == "story_get": result = {"story": self.store.story(key)}
            elif operation == "story_create": result = {"story": self.store.save(args["story"])}
            elif operation == "story_update": result = {"story": self.store.save(args["story"], key)}
            elif operation == "story_delete":
                self.store.delete(key, args["revision"])
                result = {"deleted": True}
            elif operation == "session_list": result = {"sessions": self.store.sessions()}
            elif operation == "session_get": result = self.store.session(key)
            elif operation == "session_start": result = self.store.start(args)
            elif operation == "session_choose": result = self.store.choose(key, args)
            elif operation == "narration_start": result = {"narration": self.jobs.start(key, args)}
            elif operation == "narration_get": result = {"narration": self.jobs.get(key)}
            elif operation == "navigation_list": result = {"receipts": NavigationReceipts(self.store).list()}
            elif operation == "navigation_get": result = {"receipt": NavigationReceipts(self.store).get(key)}
            else: result = {"narration": await self.jobs.cancel(key)}
            return ToolResult(True, output=json.dumps(result))
        except (Conflict, NotFound, ValueError) as exc:
            return ToolResult(False, error=str(exc), metadata={"kind": type(exc).__name__})


def create_provider(config=None):
    return ExperienceTools()
