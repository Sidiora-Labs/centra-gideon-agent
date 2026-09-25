"""Spokeo HTML protocol adapter with explicit opt-out approval."""
import hashlib
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from .privacy import fields
from .privacy_brokers import BrokerAdapterResult
from .store import MeasurementError, text

PROVIDER = 'spokeo-html-v1'
APPROVAL = 'SUBMIT SPOKEO OPT-OUT'


class _OptOutForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = self.method = None
        self.inputs = {}
        self.captcha = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'form' and self.action is None:
            self.action, self.method = values.get('action', ''), values.get('method', 'get').lower()
        if tag == 'input' and self.action is not None and values.get('name'):
            self.inputs[values['name']] = values.get('value', '')
        joined = ' '.join(str(value) for value in values.values()).lower()
        if 'captcha' in joined or 'recaptcha' in joined or 'hcaptcha' in joined:
            self.captcha = True


class SpokeoProtocol:
    def __init__(self, origin='https://www.spokeo.com', contract_mode=False):
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise MeasurementError('Invalid Spokeo protocol origin')
        if not contract_mode and (parsed.scheme != 'https' or parsed.hostname != 'www.spokeo.com'):
            raise MeasurementError('Live Spokeo adapter requires https://www.spokeo.com')
        self.origin = origin.rstrip('/')
        self.contract_mode = contract_mode

    async def scan(self, identity):
        fields(identity, ('first_name', 'last_name', 'state'), ('city',))
        for key in ('first_name', 'last_name', 'state'):
            text(identity[key], key, 100)
        state = identity['state'].strip().upper()
        if not re.fullmatch(r'[A-Z]{2}', state):
            raise MeasurementError('state must be a two-letter code')
        path = '/' + quote(identity['first_name'].strip() + '-' + identity['last_name'].strip()) + '/' + state
        status, body, final_url = await self._request('GET', self.origin + path)
        lowered = body.lower()
        if status in (403, 429) or any(value in lowered for value in ('captcha', 'verify you are human', 'request blocked')):
            outcome = 'blocked'
        elif status >= 500:
            raise MeasurementError('Spokeo scan is temporarily unavailable', 503, 'provider_failed')
        elif status == 404 or 'no results found' in lowered:
            outcome = 'not_found'
        else:
            full_name = (identity['first_name'] + ' ' + identity['last_name']).strip().lower()
            name_hit = full_name in lowered
            location_hit = state.lower() in lowered or bool(identity.get('city') and identity['city'].strip().lower() in lowered)
            outcome = 'found' if name_hit and location_hit else 'indirect_exposure' if name_hit else 'not_found'
        return self._result(outcome, final_url, body)

    async def prepare(self, profile_url, email):
        self._profile(profile_url)
        self._email(email)
        form, form_url, body = await self._form()
        live_enabled = os.getenv('GIDEON_ALLOW_LIVE_SPOKEO_SUBMIT') == '1'
        return self._result('prepared', form_url, body), {
            'provider': PROVIDER,
            'method': 'POST',
            'form_url': form_url,
            'disclosed_fields': ['listing_url', 'email'],
            'approval_phrase': APPROVAL,
            'submission_mode': 'contract' if self.contract_mode else 'live' if live_enabled else 'disabled',
            'live_submission_enabled': self.contract_mode or live_enabled,
        }

    async def submit(self, profile_url, email, approval):
        self._profile(profile_url)
        self._email(email)
        if approval != APPROVAL:
            raise MeasurementError('Exact Spokeo opt-out approval phrase is required', 403, 'approval_required')
        if not self.contract_mode and os.getenv('GIDEON_ALLOW_LIVE_SPOKEO_SUBMIT') != '1':
            raise MeasurementError('Live Spokeo submission is disabled; enable it only after owner approval', 403, 'approval_required')
        form, form_url, _ = await self._form()
        payload = dict(form.inputs)
        payload.update(url=profile_url, email=email)
        status, body, final_url = await self._request('POST', form_url, data=payload)
        if status not in (200, 201, 202) or not any(value in body.lower() for value in ('request received', 'confirmation email', 'check your email')):
            raise MeasurementError('Spokeo did not acknowledge the opt-out request', 502, 'provider_failed')
        return self._result('submitted', final_url, body)

    async def _form(self):
        status, body, final_url = await self._request('GET', self.origin + '/optout')
        if status != 200:
            raise MeasurementError('Spokeo opt-out form is unavailable', 502, 'provider_failed')
        form = _OptOutForm()
        form.feed(body)
        if form.captcha:
            raise MeasurementError('Spokeo requires a human challenge; no submission was made', 409, 'human_required')
        form_url = urljoin(final_url, form.action or '')
        if form.method != 'post' or not form.action or not {'url', 'email'}.issubset(form.inputs):
            raise MeasurementError('Spokeo opt-out form protocol changed', 502, 'provider_changed')
        if self._origin(form_url) != self._origin(self.origin):
            raise MeasurementError('Spokeo opt-out form escaped its approved origin', 502, 'provider_changed')
        return form, form_url, body

    async def _request(self, method, url, data=None):
        if self._origin(url) != self._origin(self.origin):
            raise MeasurementError('Spokeo request escaped its approved origin')
        try:
            async with ClientSession(timeout=ClientTimeout(total=15), headers={'User-Agent': 'Gideon-PrivacyBroker/1.0'}) as client:
                async with client.request(method, url, data=data, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        raise MeasurementError('Spokeo protocol returned an unapproved redirect', 502, 'provider_changed')
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 1048576:
                            raise MeasurementError('Spokeo response exceeds 1 MiB', 502, 'provider_failed')
                        chunks.append(chunk)
                    return response.status, b''.join(chunks).decode('utf-8', 'replace'), str(response.url)
        except MeasurementError:
            raise
        except (ClientError, TimeoutError):
            raise MeasurementError('Spokeo connection failed', 503, 'provider_failed') from None

    @staticmethod
    def _origin(url):
        parsed = urlsplit(url)
        return parsed.scheme, parsed.hostname, parsed.port

    def _profile(self, value):
        text(value, 'profile_url', 1000)
        if self._origin(value) != self._origin(self.origin):
            raise MeasurementError('Profile URL must belong to the configured Spokeo origin')

    @staticmethod
    def _email(value):
        text(value, 'email', 320)
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value):
            raise MeasurementError('email must be valid')

    @staticmethod
    def _result(outcome, url, body):
        digest = hashlib.sha256((url + '\n' + body).encode()).hexdigest()
        return BrokerAdapterResult(PROVIDER, outcome, datetime.now(timezone.utc).isoformat(), 'sha256:' + digest)


class SpokeoCaseAdapter:
    def __init__(self, store, protocol=None):
        self.store = store
        self.protocol = protocol or SpokeoProtocol()

    def _case(self, identity, revision, scope):
        row = self.store.get_case(identity)
        if type(revision) is not int or row['revision'] != revision:
            raise MeasurementError('Broker case changed; reload', 409, 'conflict')
        broker = next((item for item in self.store.list_brokers() if item['id'] == row['broker_id']), None)
        if not broker or broker['name'].strip().lower() != 'spokeo':
            raise MeasurementError('This adapter supports Spokeo cases only', 409, 'unsupported_provider')
        if not self.store.allowed(row['subject_id'], scope):
            raise MeasurementError('Explicit subject consent required for ' + scope, 403, 'consent_required')
        return row

    async def scan(self, identity, payload):
        fields(payload, ('request_id', 'revision', 'first_name', 'last_name', 'state'), ('city',))
        self._case(identity, payload['revision'], 'broker_scan')
        result = await self.protocol.scan({key: payload[key] for key in ('first_name', 'last_name', 'state', 'city') if key in payload})
        return self.store.apply_adapter_scan(identity, {key: payload[key] for key in ('request_id', 'revision')}, result)

    async def prepare(self, identity, payload):
        fields(payload, ('request_id', 'revision', 'profile_url', 'email'))
        self._case(identity, payload['revision'], 'broker_submit')
        result, plan = await self.protocol.prepare(payload['profile_url'], payload['email'])
        row = self.store.apply_adapter_prepared(identity, {key: payload[key] for key in ('request_id', 'revision')}, result)
        return {'case': row, 'plan': plan}

    async def submit(self, identity, payload):
        fields(payload, ('request_id', 'revision', 'profile_url', 'email', 'approval'))
        self._case(identity, payload['revision'], 'broker_submit')
        result = await self.protocol.submit(payload['profile_url'], payload['email'], payload['approval'])
        return self.store.apply_adapter_submission(identity, {key: payload[key] for key in ('request_id', 'revision')}, result)

    async def verify(self, identity, payload):
        fields(payload, ('request_id', 'revision', 'first_name', 'last_name', 'state'), ('city',))
        self._case(identity, payload['revision'], 'broker_scan')
        result = await self.protocol.scan({key: payload[key] for key in ('first_name', 'last_name', 'state', 'city') if key in payload})
        if result.outcome not in ('found', 'not_found'):
            raise MeasurementError('Spokeo verification was inconclusive', 409, 'verification_inconclusive')
        return self.store.apply_adapter_verification(identity, {key: payload[key] for key in ('request_id', 'revision')}, result)
