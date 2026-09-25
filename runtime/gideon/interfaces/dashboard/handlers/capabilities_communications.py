import asyncio

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from gideon.core.config.loader import AppConfig
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore, care
from gideon.workspace.capabilities.communications.imports import commit, preview
from gideon.workspace.capabilities.communications.evidence import ingest, report
from gideon.workspace.capabilities.communications import mirrors, desktop, beeper, telegram


async def handle(request):
    try:
        store = PeopleStore()
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
