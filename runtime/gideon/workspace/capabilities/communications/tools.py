import asyncio
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, ValidationError

from gideon.core.config.loader import AppConfig
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from . import PeopleError, PeopleStore, care
from .evidence import ingest, report
from .imports import commit, preview
from .store import fields
from . import mirrors


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


STRING = {'type': 'string'}
IDENTITY = obj({'kind': {'enum': ['email', 'phone', 'handle']}, 'value': STRING}, ('kind', 'value'))
PERSON = obj({'name': STRING, 'identities': {'type': 'array', 'items': IDENTITY, 'maxItems': 100},
              'ring': {'enum': ['support', 'core', 'tribe', 'village', 'external']},
              'cadence_days': {'type': 'integer', 'minimum': 1, 'maximum': 3650}, 'notes': STRING}, ('name',))
IMPORT = {'format': {'enum': ['csv', 'vcard']}, 'content': {'type': 'string', 'maxLength': 262144}}
DECISION = obj({'row_id': STRING, 'action': {'enum': ['create', 'update', 'skip']}, 'person_id': STRING,
                'revision': {'type': 'integer', 'minimum': 1}}, ('row_id', 'action'))
TOUCH = obj({'source': STRING, 'external_id': STRING, 'occurred_at': STRING,
             'direction': {'enum': ['inbound', 'outbound', 'mutual']}, 'summary': STRING},
            ('source', 'external_id', 'occurred_at', 'direction'))
MESSAGE = obj({'person_id': STRING, 'thread_id': STRING, 'external_id': STRING, 'occurred_at': STRING,
               'direction': {'enum': ['inbound', 'outbound']}, 'summary': STRING},
              ('person_id', 'thread_id', 'external_id', 'occurred_at', 'direction'))
BATCH = obj({'source': STRING, 'source_account_id': STRING, 'captured_at': STRING, 'coverage_start': STRING,
             'coverage_end': STRING, 'incoming_complete': {'type': 'boolean'}, 'outgoing_complete': {'type': 'boolean'},
             'messages': {'type': 'array', 'items': MESSAGE, 'maxItems': 1000}},
            ('source', 'source_account_id', 'captured_at', 'coverage_start', 'coverage_end', 'incoming_complete', 'outgoing_complete', 'messages'))
ACCOUNT = obj({key: STRING for key in mirrors.ACCOUNT_FIELDS}, ('name', 'kind', 'owner_email'))
SPECS = {
    'people_mirror_accounts': ('List configured mail sources and sync state.', obj({}), False),
    'people_mirror_capabilities': ('Read adapter coverage and external qualification gaps.', obj({}), False),
    'people_mirror_create': ('Configure an account with a credential reference, never a secret.', obj({'account': ACCOUNT}, ('account',)), True),
    'people_mirror_update': ('Update account configuration at its current revision.', obj({'account_id': STRING, 'account': obj({**ACCOUNT['properties'], 'revision': {'type': 'integer', 'minimum': 1}}, ('name', 'kind', 'owner_email', 'revision'))}, ('account_id', 'account')), True),
    'people_mirror_upload': ('Store an RFC822 message or mbox archive inside this account.', obj({'account_id': STRING, 'content': {'type': 'string', 'maxLength': 2097152}, 'folder': {'enum': ['INBOX', 'Sent']}}, ('account_id', 'content')), True),
    'people_mirror_sync': ('Read selected mail folders into the local mirror; never sends mail.', obj({'account_id': STRING}, ('account_id',)), True),
    'people_mirror_messages': ('Read normalized messages and attachment metadata.', obj({'account_id': STRING}, ('account_id',)), False),
    'people_list': ('List people and their relationship care state.', obj({'timezone': STRING}), False),
    'people_get': ('Read one person and their recorded touchpoints.', obj({'person_id': STRING, 'timezone': STRING}, ('person_id',)), False),
    'people_create': ('Create a person with explicit identities. Conflicts never merge people.', obj({'person': PERSON}, ('person',)), True),
    'people_update': ('Edit a person using the current revision.', obj({'person_id': STRING, 'person': obj({**PERSON['properties'], 'revision': {'type': 'integer', 'minimum': 1}}, ('name', 'revision'))}, ('person_id', 'person')), True),
    'people_record_touchpoint': ('Record a source-identified touchpoint; exact retries are idempotent.', obj({'person_id': STRING, 'touchpoint': TOUCH}, ('person_id', 'touchpoint')), True),
    'people_import_preview': ('Preview bounded CSV or vCard contacts without writing people.', obj(IMPORT, ('format', 'content')), False),
    'people_import_commit': ('Commit explicitly reviewed import decisions atomically; updates only add identities.', obj({**IMPORT, 'source_digest': STRING, 'decisions': {'type': 'array', 'items': DECISION, 'maxItems': 500}}, ('format', 'content', 'source_digest', 'decisions')), True),
    'people_record_thread_evidence': ('Store observed thread data and coverage. This does not fetch or send messages or certify provider coverage.', BATCH, True),
    'people_care_report': ('Read care and thread verdicts from recorded evidence. Incomplete or stale coverage stays unknown.', obj({'timezone': STRING}), False),
}


class PeopleTools(ToolProvider):
    name = 'gideon-people'
    display_name = 'People and relationships'

    async def list_tools(self):
        return [ToolDefinition(name=name, description=description, provider=self.name, parameters=schema,
                               requires_approval=write, risk_level=RiskLevel.CAUTION if write else RiskLevel.SAFE)
                for name, (description, schema, write) in SPECS.items()]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name not in SPECS:
                raise PeopleError('Unknown people tool')
            schema = SPECS[tool_name][1]
            try:
                Draft202012Validator(schema).validate(arguments)
            except ValidationError as exc:
                raise PeopleError(exc.message) from None
            fields(arguments, set(schema['properties']))
            if any(key not in arguments for key in schema['required']):
                raise PeopleError('Required tool argument is missing')
            store = PeopleStore()
            zone = arguments.get('timezone') or AppConfig.load().timezone or 'UTC'
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                raise PeopleError('Unknown timezone') from None
            if tool_name == 'people_mirror_accounts':
                result = {'accounts': mirrors.accounts(store)}
            elif tool_name == 'people_mirror_capabilities':
                result = {'adapters': mirrors.COVERAGE}
            elif tool_name in ('people_mirror_create', 'people_mirror_update'):
                result = {'account': mirrors.save_account(store, arguments['account'], arguments.get('account_id'))}
            elif tool_name == 'people_mirror_upload':
                result = mirrors.upload(store, arguments['account_id'], {k: v for k, v in arguments.items() if k != 'account_id'})
            elif tool_name == 'people_mirror_sync':
                result = {'sync': await asyncio.to_thread(mirrors.sync, store, arguments['account_id'])}
            elif tool_name == 'people_mirror_messages':
                result = {'messages': mirrors.messages(store, arguments['account_id'])}
            elif tool_name == 'people_list':
                result = {'people': [{**row['person'], 'care': row['care']} for row in report(store, zone)['people']]}
            elif tool_name == 'people_get':
                person = store.get(arguments['person_id'])
                points = store.touchpoints(person['id'])
                state = next(row['care'] for row in report(store, zone)['people'] if row['person']['id'] == person['id'])
                result = {'person': person, 'touchpoints': points, 'care': state}
            elif tool_name in ('people_create', 'people_update'):
                result = {'person': store.save(arguments['person'], arguments.get('person_id'))}
            elif tool_name == 'people_record_touchpoint':
                point, created = store.record(arguments['person_id'], arguments['touchpoint'])
                result = {'touchpoint': point, 'created': created}
            elif tool_name == 'people_import_preview':
                result = preview(store, arguments)
            elif tool_name == 'people_import_commit':
                receipt, created = commit(store, arguments)
                result = {'receipt': receipt, 'created': created}
            elif tool_name == 'people_record_thread_evidence':
                receipt, created = ingest(store, arguments)
                result = {'receipt': receipt, 'created': created}
            else:
                result = report(store, zone)
            return ToolResult(success=True, output=json.dumps(result))
        except (PeopleError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc), metadata={'status': getattr(exc, 'status', 400)},
                              recovery_hints=['Reload the current person or preview and submit valid arguments with its current revision.'])


def create_provider(config=None):
    return PeopleTools()
