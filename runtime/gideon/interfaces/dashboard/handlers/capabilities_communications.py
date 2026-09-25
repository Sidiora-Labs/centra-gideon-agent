import asyncio

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from gideon.core.config.loader import AppConfig
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore, care
from gideon.workspace.capabilities.communications.imports import commit, preview
from gideon.workspace.capabilities.communications.evidence import ingest, report
from gideon.workspace.capabilities.communications import mirrors


async def handle(request):
    try:
        store = PeopleStore()
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
