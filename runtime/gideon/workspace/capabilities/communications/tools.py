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
from . import mirrors, desktop, beeper, telegram, calendar


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
DESKTOP = {'source': {'enum': ['imessage', 'signal']}, 'source_account_id': STRING, 'content_base64': {'type': 'string', 'maxLength': 11184812}}
OUTBOX_ACTION = {'outbox_id': STRING, 'revision': {'type': 'integer', 'minimum': 1}}
TGCONFIG = {'enabled': {'type': 'boolean'}, 'automatic_replies': {'type': 'boolean'}, 'bot_credential_ref': STRING, 'webhook_credential_ref': STRING, 'allowed_chat_ids': {'type': 'array', 'items': {'type': 'integer'}, 'maxItems': 50}, 'allowed_user_ids': {'type': 'array', 'items': {'type': 'integer'}, 'maxItems': 50}, 'revision': {'type': 'integer', 'minimum': 0}}
CAL_SOURCE = {key: STRING for key in calendar.SOURCE_FIELDS}
SPECS = {
    'people_calendar_sources': ('Read calendar source and coverage state.', obj({}), False),
    'people_calendar_create': ('Configure an ICS or existing Google/Outlook calendar credential reference.', obj({'source': obj(CAL_SOURCE, ('name', 'kind'))}, ('source',)), True),
    'people_calendar_update': ('Update a calendar source at its current revision; invalidate previous projection.', obj({'source_id': STRING, 'source': obj({**CAL_SOURCE, 'revision': {'type': 'integer', 'minimum': 1}}, ('name', 'kind', 'revision'))}, ('source_id', 'source')), True),
    'people_calendar_upload': ('Import a bounded actual ICS snapshot; unsupported recurrence is explicit.', obj({'source_id': STRING, 'content': STRING, 'revision': {'type': 'integer', 'minimum': 1}}, ('source_id', 'content', 'revision')), True),
    'people_calendar_sync': ('Read a bounded Google/Outlook event window using an existing connection.', obj({'source_id': STRING, 'start': STRING, 'end': STRING}, ('source_id', 'start', 'end')), True),
    'people_calendar_daily': ('Read events overlapping a local day with source freshness and coverage.', obj({'date': STRING, 'timezone': STRING}, ('date',)), False),
    'people_telegram_config': ('Read Telegram operational settings without credentials.', obj({}), False),
    'people_telegram_configure': ('Configure allowlisted Telegram operations and optional automatic replies.', obj(TGCONFIG, TGCONFIG), True),
    'people_telegram_command': ('Project /people, /care or /status from the real local people store without sending.', obj({'command': {'enum': ['/people', '/care', '/status']}}, ('command',)), False),
    'people_telegram_queue': ('Queue a notification to an explicitly allowed chat without sending.', obj({'request_key': STRING, 'chat_id': {'type': 'integer'}, 'text': STRING}, ('request_key', 'chat_id', 'text')), True),
    'people_telegram_deliveries': ('Read durable Telegram attempt states.', obj({}), False),
    'people_telegram_send': ('Send one explicitly approved queued Telegram notification; unknown attempts never automatically retry.', obj({'delivery_id': STRING, 'confirm_send': {'const': True}}, ('delivery_id', 'confirm_send')), True),
    'people_beeper_settings': ('Read Beeper connection references.', obj({}), False),
    'people_beeper_configure': ('Configure an existing Beeper Desktop connection.', obj({'base_url': STRING, 'credential_ref': STRING, 'revision': {'type': 'integer', 'minimum': 0}}, ('base_url', 'credential_ref', 'revision')), True),
    'people_beeper_page': ('Read cached conversations or a chat message page.', obj({'chat_id': STRING}), False),
    'people_beeper_refresh': ('Fetch a real Beeper chat or message page; history may be incomplete.', obj({'chat_id': STRING, 'cursor': STRING}), True),
    'people_beeper_asset_fetch': ('Fetch a mirrored attachment by its Beeper media identity.', obj({'chat_id': STRING, 'asset_id': STRING}, ('chat_id', 'asset_id')), True),
    'people_beeper_asset': ('Read cached attachment bytes.', obj({'asset_id': STRING}, ('asset_id',)), False),
    'people_beeper_outbox': ('Read durable outbox states; pending is not delivery.', obj({}), False),
    'people_beeper_draft': ('Queue a reviewed text draft without sending.', obj({'request_key': STRING, 'chat_id': STRING, 'text': STRING}, ('request_key', 'chat_id', 'text')), True),
    'people_beeper_send': ('Send one explicitly approved queued message once. Unknown outcomes never retry automatically.', obj({**OUTBOX_ACTION, 'confirm_send': {'const': True}}, (*OUTBOX_ACTION, 'confirm_send')), True),
    'people_beeper_reconcile': ('Read Beeper status for a pending ID without resending.', obj(OUTBOX_ACTION, OUTBOX_ACTION), True),
    'people_beeper_discard': ('Discard a draft or unresolved item without retracting remote messages.', obj(OUTBOX_ACTION, OUTBOX_ACTION), True),
    'people_beeper_recover': ('Mark an interrupted sending item unknown after one minute; never resend.', obj(OUTBOX_ACTION, OUTBOX_ACTION), True),
    'people_desktop_preview': ('Preview a plain desktop SQLite snapshot; encrypted databases require a local export.', obj(DESKTOP, DESKTOP), False),
    'people_desktop_commit': ('Commit a reviewed snapshot and incomplete-coverage evidence atomically.', obj({**DESKTOP, 'source_digest': STRING, 'review_token': STRING}, (*DESKTOP, 'source_digest', 'review_token')), True),
    'people_desktop_imports': ('Read durable desktop import receipts.', obj({}), False),
    'people_desktop_history': ('Read imported source messages.', obj({'source': STRING, 'source_account_id': STRING}, ('source', 'source_account_id')), False),
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
                               requires_approval=write, risk_level=RiskLevel.DESTRUCTIVE if name in ('people_beeper_send', 'people_telegram_send', 'people_telegram_configure') else RiskLevel.CAUTION if write else RiskLevel.SAFE)
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
            if tool_name.startswith('people_calendar_'):
                action = tool_name.removeprefix('people_calendar_')
                if action == 'sources':
                    result = {'sources': calendar.sources(store)}
                elif action in ('create', 'update'):
                    result = {'source': calendar.save_source(store, arguments['source'], arguments.get('source_id'))}
                elif action == 'upload':
                    result = {'sync': calendar.upload(store, arguments['source_id'], {k: v for k, v in arguments.items() if k != 'source_id'})}
                elif action == 'sync':
                    result = {'sync': await calendar.sync_remote(store, arguments['source_id'], {k: v for k, v in arguments.items() if k != 'source_id'})}
                else:
                    result = calendar.daily(store, arguments['date'], zone)
            elif tool_name.startswith('people_telegram_'):
                action = tool_name.removeprefix('people_telegram_')
                if action == 'config':
                    result = {'config': telegram.config(store)}
                elif action == 'configure':
                    result = {'config': telegram.configure(store, arguments)}
                elif action == 'command':
                    result = telegram.command(store, arguments['command'])
                elif action == 'queue':
                    row, created = telegram.queue(store, arguments)
                    result = {'delivery': row, 'created': created}
                elif action == 'deliveries':
                    result = {'deliveries': telegram.deliveries(store)}
                else:
                    result = {'delivery': await asyncio.to_thread(telegram.deliver, store, arguments['delivery_id'])}
            elif tool_name.startswith('people_beeper_'):
                action = tool_name.removeprefix('people_beeper_')
                if action == 'settings':
                    result = {'settings': beeper.settings(store)}
                elif action == 'configure':
                    result = {'settings': beeper.configure(store, arguments)}
                elif action == 'page':
                    result = beeper.stored_page(store, arguments.get('chat_id'))
                elif action == 'refresh':
                    result = await beeper.refresh(store, arguments.get('chat_id'), arguments.get('cursor'))
                elif action == 'asset_fetch':
                    result = await beeper.fetch_asset(store, arguments['chat_id'], arguments['asset_id'])
                elif action == 'asset':
                    result = beeper.asset(store, arguments['asset_id'])
                elif action == 'outbox':
                    result = {'outbox': beeper.outbox(store)}
                elif action == 'draft':
                    row, created = beeper.draft(store, arguments)
                    result = {'item': row, 'created': created}
                elif action == 'send':
                    result = {'item': await beeper.send(store, arguments['outbox_id'], {k: v for k, v in arguments.items() if k != 'outbox_id'})}
                else:
                    operation = getattr(beeper, action)
                    row = await operation(store, arguments['outbox_id'], arguments['revision']) if action == 'reconcile' else operation(store, arguments['outbox_id'], arguments['revision'])
                    result = {'item': row}
            elif tool_name == 'people_desktop_preview':
                result = desktop.preview(store, arguments)
            elif tool_name == 'people_desktop_commit':
                receipt, created = desktop.commit(store, arguments)
                result = {'receipt': receipt, 'created': created}
            elif tool_name == 'people_desktop_imports':
                result = {'imports': desktop.imports(store)}
            elif tool_name == 'people_desktop_history':
                result = {'messages': desktop.history(store, arguments['source'], arguments['source_account_id'])}
            elif tool_name == 'people_mirror_accounts':
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
