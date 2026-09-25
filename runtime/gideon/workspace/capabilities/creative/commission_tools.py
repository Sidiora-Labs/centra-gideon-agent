"""Native tools for recurring creative commissions and feedback."""
import json
from copy import deepcopy

from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .commissions import CommissionStore
from .store import CatalogError, keys


STRING = {'type': 'string'}
INTEGER = {'type': 'integer'}


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


SCHEMAS = {
    'creative_commission_list': obj({}),
    'creative_commission_get': obj({'id': STRING}, ['id']),
    'creative_commission_create': obj({'payload': {'type': 'object'}}, ['payload']),
    'creative_commission_update': obj({'id': STRING, 'payload': {'type': 'object'}}, ['id', 'payload']),
    'creative_commission_run': obj({'id': STRING, 'request_id': STRING}, ['id', 'request_id']),
    'creative_commission_retry': obj({'id': STRING, 'run_id': STRING}, ['id', 'run_id']),
    'creative_commission_feedback': obj({'id': STRING, 'payload': {'type': 'object'}}, ['id', 'payload']),
    'creative_commission_feedback_delete': obj({'id': STRING, 'reaction_id': STRING,
        'revision': INTEGER, 'author': STRING}, ['id', 'reaction_id', 'revision', 'author']),
}


class CommissionTools(ToolProvider):
    def __init__(self, store=None): self.store = store or CommissionStore()

    @property
    def name(self): return 'gideon-creative-commissions'

    @property
    def display_name(self): return 'Creative commissions and feedback'

    async def list_tools(self):
        return [ToolDefinition(name=name, provider=self.name,
            description='Operate a durable scheduled creative commission using canonical direction projects and outputs.',
            parameters=deepcopy(schema), requires_approval=name not in ('creative_commission_list', 'creative_commission_get'),
            risk_level=RiskLevel.SAFE if name in ('creative_commission_list', 'creative_commission_get') else RiskLevel.CAUTION)
            for name, schema in SCHEMAS.items()]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name not in SCHEMAS: raise CatalogError('Unknown creative commission tool', 404)
            schema = SCHEMAS[tool_name]; keys(arguments, set(schema['properties']))
            if set(schema['required']) - set(arguments): raise CatalogError('Missing required arguments')
            action = tool_name.removeprefix('creative_commission_')
            if action == 'list': result = self.store.list()
            elif action == 'get':
                result = {**self.store.get(arguments['id']), 'runs': self.store.runs(arguments['id'])['items'],
                          'feedback': self.store.feedback(arguments['id'])['items']}
            elif action == 'create': result = self.store.create(arguments['payload'])
            elif action == 'update': result = self.store.update(arguments['id'], arguments['payload'])
            elif action == 'run': result = await self.store.execute(arguments['id'], 'manual:' + arguments['request_id'], trigger='manual')
            elif action == 'retry': result = await self.store.retry(arguments['id'], arguments['run_id'])
            elif action == 'feedback': result = self.store.react(arguments['id'], arguments['payload'])
            else: result = self.store.remove_reaction(arguments['id'], arguments['reaction_id'],
                {'revision': arguments['revision'], 'author': arguments['author']})
            return ToolResult(success=True, output=json.dumps(result), metadata={'action': action})
        except CatalogError as exc:
            return ToolResult(success=False, error=str(exc), metadata={'status': exc.status})


def create_provider(config=None):
    if config not in (None, {}): raise CatalogError('Creative commission tools accept no configuration')
    return CommissionTools()
