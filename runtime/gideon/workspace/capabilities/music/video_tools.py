"""Native music video tools share the HTTP renderer ownership."""
import inspect
import json
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .video import default_store
from .store import DomainError


class VideoTools(ToolProvider):
    def __init__(self, store=None):
        self.store = store or default_store()

    @property
    def name(self):
        return 'gideon-music-video'

    @property
    def display_name(self):
        return 'Beat-grid music videos'

    async def list_tools(self):
        tools = []
        for action in ('list', 'get', 'create', 'update', 'render', 'jobs', 'job', 'cancel'):
            fields = {}
            if action in ('get', 'update', 'job', 'cancel'):
                fields['id'] = {'type': 'string'}
            if action in ('create', 'update', 'render'):
                fields['data'] = {'type': 'object', 'description': 'Project: title,track_id,render_id,tempo_bpm,offset_seconds,scenes[{id,image_ref:{slug,version},beats}]; update adds revision. Render: request_id,project_id,revision.'}
            readonly = action in ('list', 'get', 'jobs', 'job')
            tools.append(ToolDefinition(name='music_video_'+action, provider=self.name, description=action.capitalize()+' a beat-grid project or actual video render.', parameters={'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}, requires_approval=not readonly, risk_level=RiskLevel.SAFE if readonly else RiskLevel.CAUTION))
        return tools

    async def invoke(self, tool_name, arguments):
        definitions = {tool.name: tool.parameters for tool in await self.list_tools()}
        if tool_name not in definitions or not isinstance(arguments, dict) or set(arguments) != set(definitions[tool_name]['required']):
            return ToolResult(success=False, error='Invalid music video arguments')
        action = tool_name.removeprefix('music_video_')
        action = {'render':'submit','job':'get_job'}.get(action, action)
        try:
            result = getattr(self.store, action)(*[arguments[key] for key in ('id','data') if key in arguments])
            if inspect.isawaitable(result):
                result = await result
            return ToolResult(success=True, output=json.dumps(result))
        except (DomainError, ValueError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc), metadata={'code':getattr(exc,'code','invalid_input'),'status':getattr(exc,'status',400)})


def create_provider(**kwargs):
    return VideoTools()
