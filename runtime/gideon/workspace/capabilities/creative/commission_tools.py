"""Native tools for recurring creative commissions and feedback."""
import json
from copy import deepcopy

from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

from .commissions import CommissionStore
from .peer_feedback import PeerFeedbackStore, SCOPE
from .store import CatalogError, keys


STRING = {'type': 'string'}
INTEGER = {'type': 'integer'}


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


APPROVAL = obj({'decision': {'type': 'string', 'enum': ['approved']}, 'commission_id': STRING,
    'peer_id': STRING, 'reaction_id': STRING, 'reaction_revision': INTEGER},
    ['decision', 'commission_id', 'peer_id', 'reaction_id', 'reaction_revision'])


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
    'creative_commission_peer_feedback_peers': obj({}),
    'creative_commission_peer_feedback_deliver': obj({'id': STRING, 'reaction_id': STRING,
        'peer_id': STRING, 'approval': APPROVAL}, ['id', 'reaction_id', 'peer_id', 'approval']),
}


class CommissionTools(ToolProvider):
    def __init__(self, store=None, peer_feedback=None):
        self.store = store or CommissionStore()
        self.peer_feedback = peer_feedback or PeerFeedbackStore(self.store.home, commissions=self.store)

    @property
    def name(self): return 'gideon-creative-commissions'

    @property
    def display_name(self): return 'Creative commissions and feedback'

    async def list_tools(self):
        return [ToolDefinition(name=name, provider=self.name,
            description='Operate a durable scheduled creative commission using canonical direction projects and outputs.',
            parameters=deepcopy(schema), requires_approval=name not in ('creative_commission_list', 'creative_commission_get',
                'creative_commission_peer_feedback_peers'),
            risk_level=RiskLevel.SAFE if name in ('creative_commission_list', 'creative_commission_get',
                'creative_commission_peer_feedback_peers') else RiskLevel.CAUTION)
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
            elif action == 'peer_feedback_peers':
                peers = self.peer_feedback.peers.snapshot()['peers']
                result = {'items': [{'id': row['id'], 'label': row['label']} for row in peers
                    if row['enabled'] and SCOPE in row['send_categories']]}
            elif action == 'peer_feedback_deliver':
                approval = arguments['approval']; keys(approval,
                    {'decision', 'commission_id', 'peer_id', 'reaction_id', 'reaction_revision'})
                if (approval.get('decision') != 'approved' or approval.get('commission_id') != arguments['id'] or
                        approval.get('peer_id') != arguments['peer_id'] or
                        approval.get('reaction_id') != arguments['reaction_id']):
                    raise CatalogError('Explicit peer-feedback approval does not match this delivery', 403)
                payload = self.peer_feedback.export(arguments['peer_id'], arguments['id'], arguments['reaction_id'],
                    {'decision': 'approved', 'peer_id': arguments['peer_id'], 'reaction_id': arguments['reaction_id']})
                if approval.get('reaction_revision') != payload['reaction']['revision']:
                    raise CatalogError('Feedback changed after approval; reload', 409)
                receipt = await self.peer_feedback.push_prepared(arguments['peer_id'], payload)
                result = {'peer_id': arguments['peer_id'], 'commission_id': arguments['id'],
                    'reaction_id': arguments['reaction_id'], 'reaction_revision': payload['reaction']['revision'],
                    'receipt': receipt}
            else: result = self.store.remove_reaction(arguments['id'], arguments['reaction_id'],
                {'revision': arguments['revision'], 'author': arguments['author']})
            return ToolResult(success=True, output=json.dumps(result), metadata={'action': action})
        except CatalogError as exc:
            return ToolResult(success=False, error=str(exc), metadata={'status': exc.status})


def create_provider(config=None):
    if config not in (None, {}): raise CatalogError('Creative commission tools accept no configuration')
    return CommissionTools()
