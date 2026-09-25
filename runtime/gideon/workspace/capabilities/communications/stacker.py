from __future__ import annotations

import asyncio
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout
from gideon.core.config.credentials import get_credential
from . import social
from .store import PeopleError, fields, text

PAYIN = 'id payInState mcost item { id } payerPrivates { payInFailureReason }'
TERRITORY = 'query($name:String!,$cursor:String){sub(name:$name){name desc status baseCost replyCost} items(sub:$name,sort:"recent",cursor:$cursor,limit:20){cursor items{id title text}}}'
ACTION_FIELDS = {'kind', 'territory', 'title', 'text', 'item_id', 'sats'}


def schema(db):
    social.schema(db)
    db.execute('CREATE TABLE IF NOT EXISTS stacker_territories(name TEXT PRIMARY KEY,body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS stacker_actions(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,request_key TEXT NOT NULL,original TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(account_id,request_key))')


def now():
    return datetime.now(timezone.utc).isoformat()


def name(value):
    value = text(value, 'territory', 100, True)
    if not re.fullmatch(r'[A-Za-z0-9_]+', value):
        raise PeopleError('Invalid territory name')
    return value


def credential(row):
    value = get_credential(row['credential_ref']) if row['credential_ref'] else None
    if not value:
        raise PeopleError('Stacker News credential is unavailable', 409)
    return value


async def _graphql(query, variables, secret=None):
    headers = {'X-API-Key': secret} if secret else {}
    async with ClientSession(timeout=ClientTimeout(total=20), headers=headers) as client:
        async with client.post('https://stacker.news/api/graphql', json={'query': query, 'variables': variables}, allow_redirects=False) as response:
            if response.status != 200:
                raise PeopleError(f'Stacker News request failed (HTTP {response.status})', 502)
            raw = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                raw.extend(chunk)
                if len(raw) > 2 * 1024 * 1024:
                    raise PeopleError('Stacker News response exceeds limit', 502)
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get('errors') or not isinstance(result.get('data'), dict):
                raise PeopleError('Stacker News GraphQL response failed', 502)
            return result['data']


async def graphql(query, variables, secret=None):
    try:
        return await _graphql(query, variables, secret)
    except PeopleError:
        raise
    except (ClientError, asyncio.TimeoutError, ValueError, TypeError):
        raise PeopleError('Stacker News request failed', 502) from None


def normalize_territory(data):
    sub = data.get('sub') if isinstance(data, dict) else None
    listing = data.get('items') if isinstance(data, dict) else None
    if not isinstance(sub, dict) or not isinstance(listing, dict) or not isinstance(listing.get('items'), list) or len(listing['items']) > 100:
        raise PeopleError('Malformed territory response', 502)
    territory = name(sub.get('name'))
    for field in ('baseCost', 'replyCost'):
        if type(sub.get(field)) is not int or sub[field] < 0:
            raise PeopleError('Malformed territory costs', 502)
    posts = []
    for item in listing['items']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].isdigit():
            raise PeopleError('Malformed territory item', 502)
        posts.append({'id': item['id'], 'title': text(item.get('title') or '', 'title', 1000), 'text': text(item.get('text') or '', 'text', 100000), 'url': 'https://stacker.news/items/' + item['id']})
    cursor = listing.get('cursor')
    if cursor is not None:
        cursor = text(cursor, 'cursor', 1000)
    return {'name': territory, 'description': text(sub.get('desc') or '', 'description', 100000), 'status': text(sub.get('status'), 'status', 100, True), 'base_cost_sats': sub['baseCost'], 'reply_cost_sats': sub['replyCost'], 'posts': posts, 'cursor': cursor, 'coverage': 'available_page_only'}


def territories(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM stacker_territories ORDER BY name')]


async def read_territory(store, data):
    fields(data, {'name', 'cursor'})
    territory = name(data.get('name'))
    cursor = text(data.get('cursor', ''), 'cursor', 1000)
    try:
        result = {**normalize_territory(await graphql(TERRITORY, {'name': territory, 'cursor': cursor or None})), 'state': 'synced'}
        if result['name'] != territory:
            raise PeopleError('Territory identity mismatch', 502)
    except (PeopleError, ClientError, asyncio.TimeoutError, ValueError, TypeError) as error:
        result = {'name': territory, 'state': 'failed', 'coverage': 'unknown', 'posts': [], 'error': str(error) if isinstance(error, PeopleError) else 'Stacker News read failed'}
    result['captured_at'] = now()
    with closing(store.connect()) as db, db:
        schema(db)
        db.execute('INSERT OR REPLACE INTO stacker_territories VALUES (?,?)', (territory, json.dumps(result)))
    return result


def account(store, account_id):
    row = social.get(store, account_id)
    if row['platform'] != 'stackernews' or row['status'] != 'active':
        raise PeopleError('Active Stacker News registration is required')
    return row


def action_values(data):
    fields(data, ACTION_FIELDS)
    kind = data.get('kind')
    if kind not in ('discussion', 'comment', 'zap'):
        raise PeopleError('Unsupported Stacker News action')
    territory = name(data.get('territory'))
    title = text(data.get('title', ''), 'title', 200, kind == 'discussion')
    content = text(data.get('text', ''), 'text', 10000, kind != 'zap')
    item_id = text(data.get('item_id', ''), 'item_id', 30, kind != 'discussion')
    if item_id and not item_id.isdigit():
        raise PeopleError('Item ID must be numeric')
    sats = data.get('sats', 0)
    if type(sats) is not int or sats < 0 or sats > 1000000 or kind == 'zap' and sats == 0 or kind != 'zap' and sats != 0:
        raise PeopleError('Zap amount must be 1 to 1000000 sats; other actions use provider fees')
    return {'kind': kind, 'territory': territory, 'title': title, 'text': content, 'item_id': item_id, 'sats': sats}


def actions(store, account_id):
    current = social.get(store, account_id)
    with closing(store.connect()) as db, db:
        schema(db)
        rows = [json.loads(row[0]) for row in db.execute('SELECT body FROM stacker_actions WHERE account_id=? ORDER BY rowid', (account_id,))]
    for row in rows:
        row['registration_changed'] = row['account_revision'] != current['revision'] or current['status'] != 'active' or current['platform'] != 'stackernews'
        if row['registration_changed']:
            row.pop('handoff_url', None)
    return rows


def action(store, account_id, action_id):
    row = next((row for row in actions(store, account_id) if row['id'] == action_id), None)
    if not row:
        raise PeopleError('Stacker action not found', 404)
    return row


def save(store, account_id, data):
    fields(data, ACTION_FIELDS | {'request_key'})
    current = account(store, account_id)
    values = action_values({key: value for key, value in data.items() if key in ACTION_FIELDS})
    key = text(data.get('request_key'), 'request_key', 200, True)
    original = json.dumps(values, sort_keys=True)
    with closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        previous = db.execute('SELECT original,body FROM stacker_actions WHERE account_id=? AND request_key=?', (account_id, key)).fetchone()
        if previous:
            if previous[0] != original:
                raise PeopleError('Stacker action request conflicts', 409)
            return json.loads(previous[1])
        row = {**values, 'id': uuid4().hex, 'account_id': account_id, 'account_revision': current['revision'], 'state': 'draft', 'revision': 1, 'created_at': now(), 'external_execution': 'not_attempted'}
        db.execute('INSERT INTO stacker_actions VALUES (?,?,?,?,?)', (row['id'], account_id, key, original, json.dumps(row)))
    return row


def transition(store, account_id, action_id, revision, allowed, changes):
    if type(revision) is not int:
        raise PeopleError('Current action revision is required')
    with closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        found = db.execute('SELECT body FROM stacker_actions WHERE id=? AND account_id=?', (action_id, account_id)).fetchone()
        if not found:
            raise PeopleError('Stacker action not found', 404)
        row = json.loads(found[0])
        linked = db.execute('SELECT body,revision FROM social_accounts WHERE id=?', (account_id,)).fetchone()
        if not linked or linked[1] != row['account_revision'] or json.loads(linked[0])['status'] != 'active':
            raise PeopleError('Stacker registration changed; create a new reviewed action', 409)
        if row['revision'] != revision or row['state'] not in allowed:
            raise PeopleError('Stacker action state changed; reload', 409)
        row.update(changes, revision=revision + 1, updated_at=now())
        db.execute('UPDATE stacker_actions SET body=? WHERE id=?', (json.dumps(row), action_id))
    return row


def review(store, account_id, action_id, data):
    fields(data, {'revision', 'confirm_review'})
    if data.get('confirm_review') is not True:
        raise PeopleError('Explicit review confirmation is required')
    row = action(store, account_id, action_id)
    url = 'https://stacker.news/~' + row['territory'] if row['kind'] == 'discussion' else 'https://stacker.news/items/' + row['item_id']
    return transition(store, account_id, action_id, data.get('revision'), {'draft'}, {'state': 'reviewed', 'handoff_url': url, 'reviewed_at': now(), 'execution_warning': 'Submitting a discussion/comment may immediately post and charge provider-determined fees. Zap API keys are unsupported; use the browser and verify the account and amount.'})


def mutation(row):
    if row['kind'] == 'zap':
        raise PeopleError('Stacker News prohibits API-key zaps; use reviewed browser handoff', 409)
    if row['kind'] == 'discussion':
        return 'mutation($title:String!,$text:String!,$subs:[String!]){result:upsertDiscussion(title:$title,text:$text,subNames:$subs){' + PAYIN + '}}', {'title': row['title'], 'text': row['text'], 'subs': [row['territory']]}
    return 'mutation($text:String!,$parent:ID!){result:upsertComment(text:$text,parentId:$parent){' + PAYIN + '}}', {'text': row['text'], 'parent': row['item_id']}


def payin(data):
    if not isinstance(data, dict) or type(data.get('id')) is not int or data['id'] < 1 or not isinstance(data.get('payInState'), str) or not str(data.get('mcost', '')).isdigit():
        raise PeopleError('Malformed PayIn receipt; reconcile before any retry', 502)
    item = data.get('item')
    if item is not None and (not isinstance(item, dict) or not str(item.get('id', '')).isdigit()):
        raise PeopleError('Malformed PayIn item', 502)
    return {'payin_id': data['id'], 'provider_state': data['payInState'], 'cost_msats': str(data['mcost']), 'external_item_id': str(item['id']) if item else None, 'external_execution': 'provider_reported', 'state': 'provider_receipt'}


async def submit(store, account_id, action_id, data):
    fields(data, {'revision', 'confirm_execute'})
    if data.get('confirm_execute') is not True:
        raise PeopleError('Explicit execution approval is required; provider may post and charge fees')
    current = account(store, account_id)
    row = action(store, account_id, action_id)
    query, variables = mutation(row)
    secret = credential(current)
    identity = await graphql('query { me { id name } }', {}, secret)
    if not isinstance(identity.get('me'), dict) or not isinstance(identity['me'].get('name'), str) or identity['me']['name'].casefold() != current['handle']:
        raise PeopleError('Stacker credential does not match registered identity', 409)
    claimed = transition(store, account_id, action_id, data.get('revision'), {'reviewed'}, {'state': 'submitting', 'external_execution': 'unknown', 'submitted_at': now()})
    try:
        result = payin((await graphql(query, variables, secret)).get('result'))
    except (PeopleError, ClientError, asyncio.TimeoutError, ValueError, TypeError):
        result = {'state': 'unknown', 'external_execution': 'unknown', 'error': 'Submission outcome unknown; do not repeat. Inspect Stacker News before reconciling.'}
    return finish(store, claimed, result)


def finish(store, claimed, result):
    with closing(store.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT body FROM stacker_actions WHERE id=?', (claimed['id'],)).fetchone()
        current = json.loads(found[0])
        if current['revision'] != claimed['revision']:
            raise PeopleError('Action changed while remote result was pending', 409)
        current.update(result, revision=current['revision'] + 1, updated_at=now())
        db.execute('UPDATE stacker_actions SET body=? WHERE id=?', (json.dumps(current), current['id']))
    return current


async def reconcile(store, account_id, action_id, data):
    fields(data, set())
    current = account(store, account_id)
    row = action(store, account_id, action_id)
    if row['registration_changed'] or not row.get('payin_id'):
        raise PeopleError('An unchanged registration and provider PayIn receipt are required', 409)
    result = payin((await graphql('query($id:Int!){payIn(id:$id){' + PAYIN + '}}', {'id': row['payin_id']}, credential(current))).get('payIn'))
    if result['payin_id'] != row['payin_id']:
        raise PeopleError('PayIn identity mismatch', 502)
    return finish(store, row, result)
