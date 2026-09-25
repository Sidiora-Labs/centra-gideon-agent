"""Native procedural assembly operations."""
import json
from gideon.core.config.loader import config_dir
from gideon.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .assembly import AssemblyStore
from .store import DomainError


class AssemblyTools(ToolProvider):
    def __init__(self, store=None):
        home = config_dir()
        self.store = store or AssemblyStore(home, NativeArtifactProvider(root=home / 'artifacts'))

    @property
    def name(self):
        return 'gideon-music-assemblies'

    @property
    def display_name(self):
        return 'Procedural assemblies'

    async def list_tools(self):
        tools = []
        for action in ('list', 'get', 'create', 'update', 'refine', 'export', 'history'):
            properties = {}
            if action not in ('list', 'create'):
                properties['id'] = {'type': 'string'}
            if action in ('create', 'update', 'refine', 'export'):
                properties['data'] = {'type': 'object', 'description': 'Assembly: title,parts,clips. Update adds revision; refine: revision,operations[ground|clamp_clips]; export: revision.'}
            read_only = action in ('list', 'get', 'history')
            tools.append(ToolDefinition(name='music_assemblies_' + action, provider=self.name, description=action.capitalize() + ' authored procedural assemblies and canonical exports.',
                parameters={'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False},
                requires_approval=not read_only, risk_level=RiskLevel.SAFE if read_only else RiskLevel.CAUTION))
        return tools

    async def invoke(self, tool_name, arguments):
        definitions = {tool.name: tool.parameters for tool in await self.list_tools()}
        if tool_name not in definitions or not isinstance(arguments, dict) or set(arguments) != set(definitions[tool_name]['required']):
            return ToolResult(success=False, error='Invalid assembly tool arguments')
        args = [arguments[key] for key in ('id', 'data') if key in arguments]
        try:
            result = getattr(self.store, tool_name.removeprefix('music_assemblies_'))(*args)
            return ToolResult(success=True, output=json.dumps(result))
        except (DomainError, TypeError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc), metadata={'code': getattr(exc, 'code', 'invalid_input'), 'status': getattr(exc, 'status', 400)})


def create_provider(**kwargs):
    return AssemblyTools()
