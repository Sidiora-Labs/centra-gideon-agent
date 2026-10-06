"""Inbox read reach from authenticated work and actual native ancestry."""
from __future__ import annotations
import contextvars
from dataclasses import dataclass
from gideon.security.approval_answer import OWNER, APP, of_request
from gideon.security.session_credentials import current_work, work_of_request, credential_for, verify

_REQUEST_READER = contextvars.ContextVar('inbox_request_reader', default=None)

def canonical(key):
    if not isinstance(key, str) or not key.strip():
        return ''
    key = key.strip()
    return key if ':' in key else 'dashboard:' + key

@dataclass(frozen=True)
class Reader:
    state: object
    origin: str = ''
    everyone: bool = False
    admitted: bool = False

    def chat_of(self, key):
        key = canonical(key)
        seen = set()
        for _ in range(32):
            if not key or key in seen:
                return ''
            seen.add(key)
            live = verify(credential_for(key), key)
            if live:
                return canonical(live.origin_session_key)
            if key.startswith('subagent:'):
                supervisor = getattr(self.state, 'subagents', None)
                info = supervisor.get(key.split(':',1)[1]) if supervisor else None
                if info is None:
                    return ''
                # Actual supervisor records only; inbox refs never provide ancestry.
                key = canonical(info.parent_session_key)
                continue
            if key.startswith('dashboard:') and key != 'dashboard:ui':
                sessions = getattr(self.state, 'sessions', None)
                return key if sessions and sessions.has_session(key) else ''
            return ''
        return ''

    def reads(self, item):
        if self.everyone:
            return True
        if not self.admitted:
            return False
        refs = getattr(item, 'refs', None)
        if not isinstance(refs, dict):
            return False
        try:
            for field in ('session', 'chat', 'workflow', 'loop'):
                value = refs.get(field)
                if value in (None, ''):
                    continue
                if not isinstance(value, str) or not value.strip():
                    return False
                if field in ('session', 'chat'):
                    origin = self.chat_of(value)
                else:
                    from gideon.security.durable_work import origin_for_run
                    receipt = origin_for_run(value)
                    if receipt is None:
                        return False
                    origin = canonical(receipt.get('origin_session_key'))
                    # A signed owner run from the pages is shared, not a chat row.
                    if origin == 'dashboard:ui':
                        continue
                if not origin or origin != self.origin:
                    return False
            return True
        except Exception:
            return False

def reader_of_request(request, state):
    by = of_request(request)
    proof = work_of_request(request)
    # Only authenticated owner pages, not an inherited OWNER work principal.
    if by.kind == OWNER and proof is None and not request.get('app'):
        return Reader(state, everyone=True, admitted=True)
    if proof:
        actor = proof.work_actor or proof.initiator
        own_pages = actor.kind == OWNER and proof.initiator.kind == OWNER and proof.origin_session_key == 'dashboard:ui'
        return Reader(state, canonical(proof.origin_session_key), own_pages, True)
    if by.kind == APP:
        return Reader(state, admitted=True)  # shared chatless rows only
    return Reader(state)

def reader_of_work(state):
    request = _REQUEST_READER.get()
    if request is not None:
        return request
    proof = current_work()
    if proof is None:
        return Reader(state)
    actor = proof.work_actor or proof.initiator
    everyone = actor.kind == OWNER and proof.initiator.kind == OWNER and proof.origin_session_key == 'dashboard:ui'
    return Reader(state, canonical(proof.origin_session_key), everyone, True)
