import asyncio

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from gideon.core.config.loader import AppConfig
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore, care
from gideon.workspace.capabilities.communications.imports import commit, preview
from gideon.workspace.capabilities.communications.evidence import ingest, report
from gideon.workspace.capabilities.communications import mirrors, desktop, beeper, telegram, calendar, social, xreading, stacker, lifecycle, timeline


async def handle(request):
    try:
        store = PeopleStore()
        if request.path.endswith('/activity-timeline'):
            data = dict(request.query)
            if 'limit' in data:
                try:
                    data['limit'] = int(data['limit'])
                except ValueError:
                    raise PeopleError('Timeline limit must be an integer') from None
            return web.json_response(timeline.read(store, data))
        if '/platform-assignments' in request.path:
            assignment_id = request.match_info.get('assignment_id')
            if request.path.endswith('/agents'):
                return web.json_response({'agents': lifecycle.agents()})
            if request.path.endswith('/history'):
                return web.json_response({'history': lifecycle.history(store, assignment_id)})
            if request.method == 'GET':
                return web.json_response({'assignment': lifecycle.get(store, assignment_id)} if assignment_id else {'assignments': lifecycle.records(store)})
            data = await request.json()
            return web.json_response({'assignment': lifecycle.change(store, assignment_id, data) if assignment_id else lifecycle.create(store, data)})
        if '/stacker/' in request.path:
            account_id = request.match_info.get('stacker_account_id')
            action_id = request.match_info.get('stacker_action_id')
            action = request.path.rsplit('/', 1)[-1]
            if request.method == 'GET':
                return web.json_response({'actions': stacker.actions(store, account_id)} if account_id else {'territories': stacker.territories(store)})
            data = await request.json()
            if action == 'territories':
                return web.json_response({'territory': await stacker.read_territory(store, data)})
            if action == 'submit':
                row = await stacker.submit(store, account_id, action_id, data)
            elif action == 'reconcile':
                row = await stacker.reconcile(store, account_id, action_id, data)
            elif action == 'review':
                row = stacker.review(store, account_id, action_id, data)
            else:
                row = stacker.save(store, account_id, data)
            return web.json_response({'action': row})
        if '/x/accounts/' in request.path:
            account_id = request.match_info['x_account_id']
            draft_id = request.match_info.get('x_draft_id')
            action = request.path.rsplit('/', 1)[-1]
            if request.method == 'GET':
                return web.json_response({'snapshot': xreading.snapshot(store, account_id)} if action == 'snapshot' else {'drafts': xreading.drafts(store, account_id)})
            data = await request.json()
            if action == 'sync':
                return web.json_response({'snapshot': await xreading.sync(store, account_id, data)})
            row = xreading.review(store, account_id, draft_id, data) if action == 'review' else xreading.save_draft(store, account_id, data, draft_id)
            return web.json_response({'draft': row})
        if '/social/' in request.path:
            account_id = request.match_info.get('social_id')
            if request.path.endswith('/history'):
                return web.json_response({'history': social.history(store, account_id)})
            if request.method == 'GET':
                return web.json_response({'account': social.get(store, account_id)} if account_id else {'accounts': social.accounts(store)})
            data = await request.json()
            if request.method == 'DELETE' or request.path.endswith('/remove'):
                social.fields(data, {'revision'})
                return web.json_response(social.remove(store, account_id, data.get('revision')))
            row, created = social.save(store, data, account_id)
            return web.json_response({'account': row, 'created': created}, status=201 if created else 200)
        if '/calendar/' in request.path:
            source_id = request.match_info.get('source_id')
            route = request.path.rsplit('/', 1)[-1]
            if route == 'daily':
                return web.json_response(calendar.daily(store, request.query.get('date', ''), request.query.get('timezone') or AppConfig.load().timezone or 'UTC'))
            if route == 'upload':
                return web.json_response({'sync': calendar.upload(store, source_id, await request.json())})
            if route == 'sync':
                return web.json_response({'sync': await calendar.sync_remote(store, source_id, await request.json())})
            if request.method == 'GET':
                return web.json_response({'sources': calendar.sources(store)})
            return web.json_response({'source': calendar.save_source(store, await request.json(), source_id)}, status=200 if source_id else 201)
        if '/telegram/' in request.path:
            route = request.path.rsplit('/', 1)[-1]
            if route == 'config':
                return web.json_response({'config': telegram.config(store) if request.method == 'GET' else telegram.configure(store, await request.json())})
            if route == 'command':
                data = await request.json()
                telegram.fields(data, {'command'})
                return web.json_response(telegram.command(store, data.get('command')))
            if route == 'deliveries':
                if request.method == 'GET':
                    return web.json_response({'deliveries': telegram.deliveries(store)})
                row, created = telegram.queue(store, await request.json())
                return web.json_response({'delivery': row, 'created': created}, status=201 if created else 200)
            if route == 'webhook':
                receipt, created = telegram.receive(store, await request.json(), request.headers.get('X-Telegram-Bot-Api-Secret-Token'))
                if receipt['automatic_replies']:
                    await asyncio.to_thread(telegram.deliver, store, receipt['delivery_id'])
                return web.json_response({'receipt': receipt, 'created': created})
            data = await request.json()
            telegram.fields(data, {'confirm_send'})
            if data.get('confirm_send') is not True:
                raise PeopleError('Explicit Telegram send confirmation is required')
            return web.json_response({'delivery': await asyncio.to_thread(telegram.deliver, store, request.match_info['delivery_id'])})
        if '/beeper/' in request.path:
            route = request.path.rsplit('/', 1)[-1]
            if route == 'settings':
                return web.json_response({'settings': beeper.settings(store) if request.method == 'GET' else beeper.configure(store, await request.json())})
            if route in ('chats', 'messages'):
                return web.json_response(beeper.stored_page(store, request.query.get('chat_id') if route == 'messages' else None))
            if route == 'refresh':
                data = await request.json()
                beeper.fields(data, {'chat_id', 'cursor'})
                return web.json_response(await beeper.refresh(store, data.get('chat_id'), data.get('cursor')))
            if route == 'assets':
                if request.method == 'GET':
                    return web.json_response(beeper.asset(store, request.query.get('id', '')))
                data = await request.json()
                beeper.fields(data, {'chat_id', 'asset_id'})
                return web.json_response(await beeper.fetch_asset(store, data.get('chat_id', ''), data.get('asset_id', '')))
            if route == 'outbox':
                if request.method == 'GET':
                    return web.json_response({'outbox': beeper.outbox(store)})
                row, created = beeper.draft(store, await request.json())
                return web.json_response({'item': row, 'created': created}, status=201 if created else 200)
            data = await request.json()
            item_id = request.match_info['outbox_id']
            if route == 'send':
                return web.json_response({'item': await beeper.send(store, item_id, data)})
            beeper.fields(data, {'revision'})
            operation = getattr(beeper, route)
            row = await operation(store, item_id, data.get('revision')) if route == 'reconcile' else operation(store, item_id, data.get('revision'))
            return web.json_response({'item': row})
        if '/desktop/' in request.path:
            if request.path.endswith('/imports'):
                return web.json_response({'imports': desktop.imports(store)})
            if request.path.endswith('/history'):
                return web.json_response({'messages': desktop.history(store, request.query.get('source', ''), request.query.get('source_account_id', ''))})
            data = await request.json()
            if request.path.endswith('/preview'):
                return web.json_response(desktop.preview(store, data))
            receipt, created = desktop.commit(store, data)
            return web.json_response({'receipt': receipt, 'created': created}, status=201 if created else 200)
        if '/mirror/' in request.path:
            account_id = request.match_info.get('account_id')
            if request.path.endswith('/capabilities'):
                return web.json_response({'adapters': mirrors.COVERAGE})
            if request.path.endswith('/messages'):
                return web.json_response({'messages': mirrors.messages(store, account_id), 'account': mirrors.get_account(store, account_id)})
            if request.path.endswith('/sync'):
                mirrors.fields(await request.json(), set())
                return web.json_response({'sync': await asyncio.to_thread(mirrors.sync, store, account_id)})
            if request.path.endswith('/upload'):
                return web.json_response(mirrors.upload(store, account_id, await request.json()))
            if request.method == 'GET':
                return web.json_response({'account': mirrors.get_account(store, account_id)} if account_id else {'accounts': mirrors.accounts(store)})
            return web.json_response({'account': mirrors.save_account(store, await request.json(), account_id)}, status=200 if account_id else 201)
        if '/import/' in request.path:
            data = await request.json()
            if request.path.endswith('/preview'):
                return web.json_response(preview(store, data))
            receipt, created = commit(store, data)
            return web.json_response({'receipt': receipt, 'created': created}, status=201 if created else 200)
        zone = request.query.get('timezone') or AppConfig.load().timezone or 'UTC'
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise PeopleError('Unknown timezone') from None
        if request.path.endswith('/threads/evidence'):
            receipt, created = ingest(store, await request.json())
            return web.json_response({'receipt': receipt, 'created': created}, status=201 if created else 200)
        if request.path.endswith('/threads'):
            return web.json_response(report(store, zone))
        person_id = request.match_info.get('person_id')
        if request.method == 'GET':
            if person_id:
                person = store.get(person_id)
                points = store.touchpoints(person_id)
                state = next(row['care'] for row in report(store, zone)['people'] if row['person']['id'] == person_id)
                return web.json_response({'person': person, 'touchpoints': points, 'care': state, 'timezone': zone})
            people = [{**row['person'], 'care': row['care']} for row in report(store, zone)['people']]
            return web.json_response({'people': people, 'timezone': zone})
        data = await request.json()
        if request.path.endswith('/touchpoints'):
            point, created = store.record(person_id, data)
            return web.json_response({'touchpoint': point, 'created': created}, status=201 if created else 200)
        person = store.save(data, person_id)
        return web.json_response({'person': person}, status=200 if person_id else 201)
    except PeopleError as exc:
        return web.json_response({'error': str(exc)}, status=exc.status)
    except (ValueError, UnicodeError):
        return web.json_response({'error': 'Invalid JSON'}, status=400)


def register(app):
    app.router.add_get('/api/capabilities/communications/activity-timeline', handle)
    assignment_base = '/api/capabilities/communications/platform-assignments'
    app.router.add_get(assignment_base, handle)
    app.router.add_get(assignment_base + '/agents', handle)
    app.router.add_post(assignment_base, handle)
    app.router.add_get(assignment_base + '/{assignment_id}', handle)
    app.router.add_put(assignment_base + '/{assignment_id}', handle)
    app.router.add_get(assignment_base + '/{assignment_id}/history', handle)
    stacker_base = '/api/capabilities/communications/stacker'
    app.router.add_get(stacker_base + '/territories', handle)
    app.router.add_post(stacker_base + '/territories', handle)
    stacker_actions = stacker_base + '/accounts/{stacker_account_id}/actions'
    app.router.add_get(stacker_actions, handle)
    app.router.add_post(stacker_actions, handle)
    for action in ('review', 'submit', 'reconcile'):
        app.router.add_post(stacker_actions + '/{stacker_action_id}/' + action, handle)
    x_base = '/api/capabilities/communications/x/accounts/{x_account_id}'
    app.router.add_get(x_base + '/snapshot', handle)
    app.router.add_post(x_base + '/sync', handle)
    app.router.add_get(x_base + '/drafts', handle)
    app.router.add_post(x_base + '/drafts', handle)
    app.router.add_put(x_base + '/drafts/{x_draft_id}', handle)
    app.router.add_post(x_base + '/drafts/{x_draft_id}/review', handle)
    social_base = "/api/capabilities/communications/social/accounts"
    app.router.add_get(social_base, handle)
    app.router.add_post(social_base, handle)
    app.router.add_get(social_base + "/{social_id}", handle)
    app.router.add_put(social_base + "/{social_id}", handle)
    app.router.add_delete(social_base + "/{social_id}", handle)
    app.router.add_post(social_base + "/{social_id}/remove", handle)
    app.router.add_get(social_base + "/{social_id}/history", handle)
    calendar_base = "/api/capabilities/communications/calendar"
    app.router.add_get(calendar_base + "/daily", handle)
    app.router.add_get(calendar_base + "/sources", handle)
    app.router.add_post(calendar_base + "/sources", handle)
    app.router.add_put(calendar_base + "/sources/{source_id}", handle)
    app.router.add_post(calendar_base + "/sources/{source_id}/upload", handle)
    app.router.add_post(calendar_base + "/sources/{source_id}/sync", handle)
    telegram_base = "/api/capabilities/communications/telegram"
    app.router.add_get(telegram_base + "/config", handle)
    app.router.add_put(telegram_base + "/config", handle)
    app.router.add_get(telegram_base + "/deliveries", handle)
    for route in ("command", "deliveries", "webhook"):
        app.router.add_post(telegram_base + "/" + route, handle)
    app.router.add_post(telegram_base + "/deliveries/{delivery_id}/send", handle)
    beeper_base = "/api/capabilities/communications/beeper"
    for route in ("settings", "chats", "messages", "assets", "outbox"):
        app.router.add_get(beeper_base + "/" + route, handle)
    app.router.add_put(beeper_base + "/settings", handle)
    for route in ("refresh", "assets", "outbox"):
        app.router.add_post(beeper_base + "/" + route, handle)
    for route in ("send", "reconcile", "discard", "recover"):
        app.router.add_post(beeper_base + "/outbox/{outbox_id}/" + route, handle)
    desktop_base = "/api/capabilities/communications/desktop"
    app.router.add_get(desktop_base + "/imports", handle)
    app.router.add_get(desktop_base + "/history", handle)
    app.router.add_post(desktop_base + "/preview", handle)
    app.router.add_post(desktop_base + "/commit", handle)
    mirror = "/api/capabilities/communications/mirror"
    app.router.add_get(mirror + "/capabilities", handle)
    app.router.add_get(mirror + "/accounts", handle)
    app.router.add_post(mirror + "/accounts", handle)
    app.router.add_get(mirror + "/accounts/{account_id}", handle)
    app.router.add_put(mirror + "/accounts/{account_id}", handle)
    app.router.add_get(mirror + "/accounts/{account_id}/messages", handle)
    app.router.add_post(mirror + "/accounts/{account_id}/upload", handle)
    app.router.add_post(mirror + "/accounts/{account_id}/sync", handle)
    app.router.add_get('/api/capabilities/communications/threads', handle)
    app.router.add_post('/api/capabilities/communications/threads/evidence', handle)
    app.router.add_post('/api/capabilities/communications/import/preview', handle)
    app.router.add_post('/api/capabilities/communications/import/commit', handle)
    base = '/api/capabilities/communications/people'
    app.router.add_get(base, handle)
    app.router.add_post(base, handle)
    app.router.add_get(base + '/{person_id}', handle)
    app.router.add_put(base + '/{person_id}', handle)
    app.router.add_post(base + '/{person_id}/touchpoints', handle)
