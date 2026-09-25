"""Whitepages email opt-out workflow backed by canonical approved outbound email."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from gideon.workspace.capabilities.communications import mirrors
from gideon.workspace.capabilities.communications.outbound_email import OutboundEmail
from gideon.workspace.capabilities.communications.store import PeopleError

from .privacy import fields
from .privacy_brokers import BrokerAdapterResult
from .store import MeasurementError, text

PROVIDER = 'whitepages-approved-email-v1'
RECIPIENT = 'privacyrequest@whitepages.com'
_CODE = re.compile(r'(?<!\d)(\d{4,8})(?!\d)')


def _now():
    return datetime.now(timezone.utc).isoformat()


class WhitepagesCaseAdapter:
    def __init__(self, store, communications_store, outbound=None):
        self.store = store
        self.communications_store = communications_store
        self.outbound = outbound or OutboundEmail(communications_store)
        with self.store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS privacy_whitepages_links(case_id TEXT PRIMARY KEY,data TEXT NOT NULL)')

    def _case(self, identity, revision, scope):
        row = self.store.get_case(identity)
        if type(revision) is not int or row['revision'] != revision:
            raise MeasurementError('Broker case changed; reload', 409, 'conflict')
        broker = next((item for item in self.store.list_brokers() if item['id'] == row['broker_id']), None)
        if not broker or broker['name'].strip().lower() != 'whitepages':
            raise MeasurementError('This adapter supports Whitepages cases only', 409, 'unsupported_provider')
        if not self.store.allowed(row['subject_id'], scope):
            raise MeasurementError('Explicit subject consent required for ' + scope, 403, 'consent_required')
        return row

    def _link(self, case_id, required=True):
        with self.store.connection() as db:
            found = db.execute('SELECT data FROM privacy_whitepages_links WHERE case_id=?', (case_id,)).fetchone()
        if found is None:
            if required: raise MeasurementError('Prepare the Whitepages email before this action', 409, 'conflict')
            return None
        return json.loads(found[0])

    def _save_link(self, row):
        with self.store.connection() as db:
            db.execute('INSERT INTO privacy_whitepages_links VALUES(?,?) ON CONFLICT(case_id) DO UPDATE SET data=excluded.data', (row['case_id'], json.dumps(row)))

    @staticmethod
    def _profile(value):
        value = text(value, 'profile_url', 1000)
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or parsed.hostname not in ('www.whitepages.com', 'whitepages.com') or parsed.username or parsed.password:
            raise MeasurementError('profile_url must be an HTTPS Whitepages listing')
        return value

    @staticmethod
    def _body(full_name, contact_email, profile_url, jurisdiction):
        right = ('I also request deletion of my personal information under CCPA section 1798.105.'
                 if jurisdiction == 'US-CA' else
                 'I also request deletion of personal information associated with this listing.')
        return (f'Hello Whitepages Privacy Team,\n\n'
                f'Please remove the Whitepages listing below, opt it out of sale or sharing, and process my deletion request.\n\n'
                f'Full name as listed: {full_name}\nContact email: {contact_email}\nListing URL: {profile_url}\n\n'
                f'{right}\n\nPlease reply to this email if you require the minimum additional information needed to verify the request.\n')

    def status(self, identity, revision):
        case = self._case(identity, revision, 'broker_scan')
        link = self._link(identity, required=False)
        if link is None:
            return {'case': case, 'draft': None, 'verification': None}
        draft = self.outbound._get(link['draft_id'])
        return {'case': case, 'draft': draft, 'verification': {
            'reply_correlated': bool(draft.get('verification')),
            'manual_code_matched': bool(link.get('manual_code_matched')),
            'removal_confirmed': False, 'links_opened': False}}

    def prepare(self, identity, payload):
        fields(payload, ('request_id','revision','account_id','full_name','contact_email','profile_url','jurisdiction'))
        self._case(identity, payload['revision'], 'broker_submit')
        full_name = text(payload['full_name'], 'full_name', 200)
        contact = text(payload['contact_email'], 'contact_email', 320).casefold()
        profile = self._profile(payload['profile_url'])
        if payload['jurisdiction'] not in ('US-CA', 'US-OTHER'):
            raise MeasurementError('jurisdiction must be US-CA or US-OTHER')
        try:
            account = mirrors.get_account(self.communications_store, payload['account_id'])
        except PeopleError as exc:
            raise MeasurementError(str(exc), exc.status, 'communications_error') from None
        if account['owner_email'] != contact:
            raise MeasurementError('Contact email must match the selected account owner', 409, 'account_mismatch')
        try:
            draft, _ = self.outbound.draft({
                'request_key': f'whitepages:{identity}:{payload["request_id"]}',
                'account_id': account['id'], 'to': [RECIPIENT],
                'subject': f'Whitepages removal and deletion request for {full_name}',
                'body': self._body(full_name, contact, profile, payload['jurisdiction']),
                'attachments': [],
            })
        except PeopleError as exc:
            raise MeasurementError(str(exc), exc.status, 'communications_error') from None
        evidence = f'outbound-email:{draft["id"]}:sha256:{draft["content_sha256"]}'
        result = BrokerAdapterResult(PROVIDER, 'prepared', _now(), evidence)
        case = self.store.apply_adapter_prepared(identity, {'request_id': payload['request_id'], 'revision': payload['revision']}, result)
        link = {'case_id': identity, 'draft_id': draft['id'], 'account_id': account['id'],
                'content_sha256': draft['content_sha256'], 'prepared_case_revision': case['revision'],
                'provider_acceptance': draft['provider_acceptance'], 'delivery': draft['delivery'],
                'manual_code_matched': False, 'updated_at': _now()}
        self._save_link(link)
        return {'case': case, 'draft': draft, 'plan': {'recipient': RECIPIENT, 'requires_exact_content_approval': True,
                'message_sent': False, 'external_verification_required': True}}

    def approve(self, identity, payload):
        fields(payload, ('revision','draft_revision','content_sha256','confirm_exact'))
        self._case(identity, payload['revision'], 'broker_submit')
        link = self._link(identity)
        if payload['content_sha256'] != link['content_sha256']:
            raise MeasurementError('Approval digest does not match the prepared Whitepages email', 409, 'conflict')
        current = self.outbound._get(link['draft_id'])
        if current['state'] == 'approved' and current['content_sha256'] == payload['content_sha256']:
            return {'case': self.store.get_case(identity), 'draft': current}
        try:
            draft = self.outbound.approve(link['draft_id'], {'revision': payload['draft_revision'],
                'content_sha256': payload['content_sha256'], 'confirm_exact': payload['confirm_exact']})
        except PeopleError as exc:
            raise MeasurementError(str(exc), exc.status, 'communications_error') from None
        link.update(updated_at=_now()); self._save_link(link)
        return {'case': self.store.get_case(identity), 'draft': draft}

    def send(self, identity, payload):
        fields(payload, ('request_id','revision','draft_revision','content_sha256','confirm_send'))
        case = self._case(identity, payload['revision'], 'broker_submit')
        link = self._link(identity)
        if payload['content_sha256'] != link['content_sha256']:
            raise MeasurementError('Dispatch digest does not match the prepared Whitepages email', 409, 'conflict')
        if link.get('send_request_id') == payload['request_id']:
            return {'case': self.store.get_case(identity), 'draft': self.outbound._get(link['draft_id'])}
        try:
            draft = self.outbound.send(link['draft_id'], {'revision': payload['draft_revision'],
                'content_sha256': payload['content_sha256'], 'confirm_send': payload['confirm_send']})
        except PeopleError as exc:
            raise MeasurementError(str(exc), exc.status, 'communications_error') from None
        link.update(send_request_id=payload['request_id'], provider_acceptance=draft['provider_acceptance'],
                    delivery=draft['delivery'], updated_at=_now()); self._save_link(link)
        evidence = f'outbound-email:{draft["id"]}:{draft["provider_acceptance"]}:delivery-{draft["delivery"]}'
        result = BrokerAdapterResult(PROVIDER, 'submitted', _now(), evidence)
        case = self.store.apply_adapter_submission(identity, {'request_id': payload['request_id'], 'revision': case['revision']}, result)
        link.update(submitted_case_revision=case['revision']); self._save_link(link)
        return {'case': case, 'draft': draft}

    def correlate(self, identity, payload):
        fields(payload, ('revision',), ('manual_code',))
        case = self._case(identity, payload['revision'], 'broker_scan')
        link = self._link(identity)
        current = self.outbound._get(link['draft_id'])
        try:
            draft = current if current.get('verification') else self.outbound.correlate(link['draft_id'])
        except PeopleError as exc:
            raise MeasurementError(str(exc), exc.status, 'communications_error') from None
        matched = False
        supplied = payload.get('manual_code')
        if supplied is not None:
            supplied = text(supplied, 'manual_code', 8)
            if not re.fullmatch(r'\d{4,8}', supplied):
                raise MeasurementError('manual_code must contain four to eight digits')
            verification = draft.get('verification')
            if not verification:
                raise MeasurementError('No canonical verification reply is available', 409, 'verification_pending')
            message = next((row for row in mirrors.messages(self.communications_store, link['account_id'])
                            if row['external_id'] == verification['message_external_id'] and row['source_digest'] == verification['source_digest']), None)
            if message is None or supplied not in _CODE.findall(message['body']):
                raise MeasurementError('Manual code does not match the ingested verification reply', 409, 'verification_mismatch')
            matched = True
        link.update(verification=draft.get('verification'), manual_code_matched=matched or link.get('manual_code_matched', False), updated_at=_now())
        self._save_link(link)
        return {'case': case, 'draft': draft, 'verification': {'reply_correlated': bool(draft.get('verification')),
                'manual_code_matched': link['manual_code_matched'], 'removal_confirmed': False,
                'links_opened': False}}
