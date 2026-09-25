"""Native tools for the canonical bounded series-production service."""
import json
from copy import deepcopy

from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .production import SeriesProductionStore
from .store import CatalogError, keys


STRING = {'type': 'string'}
NUMBER = {'type': 'integer'}


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


SCHEMAS = {
    'creative_production_list': obj({'series_id': STRING}, ['series_id']),
    'creative_production_get': obj({'series_id': STRING, 'run_id': STRING}, ['series_id', 'run_id']),
    'creative_production_start': obj({'series_id': STRING, 'payload': obj({
        'request_id': STRING, 'series_revision': NUMBER, 'mode': {'enum': ['model', 'authored']},
        'max_attempts': {'type': 'integer', 'minimum': 1, 'maximum': 3},
    }, ['request_id', 'series_revision', 'mode', 'max_attempts'])}, ['series_id', 'payload']),
    'creative_production_advance': obj({'series_id': STRING, 'run_id': STRING}, ['series_id', 'run_id']),
    'creative_production_submit': obj({'series_id': STRING, 'run_id': STRING, 'payload': obj({
        'request_id': STRING, 'work_revision': NUMBER, 'text': STRING, 'note': STRING,
    }, ['request_id', 'work_revision', 'text'])}, ['series_id', 'run_id', 'payload']),
    'creative_production_approve': obj({'series_id': STRING, 'run_id': STRING, 'payload': obj({
        'decision': {'enum': ['apply', 'keep', 'review']},
    }, ['decision'])}, ['series_id', 'run_id', 'payload']),
}
for action in ('rollback', 'pause', 'resume', 'cancel'):
    SCHEMAS[f'creative_production_{action}'] = obj({'series_id': STRING, 'run_id': STRING}, ['series_id', 'run_id'])


class CreativeProductionToolProvider(ToolProvider):
    def __init__(self, home=None):
        self.store = SeriesProductionStore(home)

    @property
    def name(self):
        return 'gideon-creative-production'

    @property
    def display_name(self):
        return 'Creative series production'

    async def list_tools(self):
        return [ToolDefinition(name=name, provider=self.name,
            description='Advance or inspect a durable approval-gated series production run in the current runtime.',
            parameters=deepcopy(schema), requires_approval=name not in ('creative_production_list', 'creative_production_get'),
            risk_level=RiskLevel.SAFE if name in ('creative_production_list', 'creative_production_get') else RiskLevel.CAUTION)
            for name, schema in SCHEMAS.items()]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name not in SCHEMAS:
                raise CatalogError('Unknown creative production tool', 404)
            keys(arguments, set(SCHEMAS[tool_name]['properties']))
            if set(SCHEMAS[tool_name]['required']) - set(arguments):
                raise CatalogError('Missing required arguments')
            action = tool_name.removeprefix('creative_production_')
            series_id, run_id = arguments['series_id'], arguments.get('run_id')
            if action == 'list':
                result = self.store.list(series_id)
            elif action == 'get':
                result = self.store.get(series_id, run_id)
            elif action == 'start':
                result = self.store.start(series_id, arguments['payload'])
            elif action == 'advance':
                result = await self.store.advance(series_id, run_id)
            elif action == 'submit':
                result = self.store.submit(series_id, run_id, arguments['payload'])
            elif action == 'approve':
                result = self.store.approve(series_id, run_id, arguments['payload'])
            elif action == 'rollback':
                result = self.store.rollback(series_id, run_id)
            else:
                result = self.store.control(series_id, run_id, action)
            return ToolResult(success=True, output=json.dumps(result, ensure_ascii=False), metadata={'action': action})
        except CatalogError as exc:
            return ToolResult(success=False, error=str(exc), metadata={'status': exc.status})
