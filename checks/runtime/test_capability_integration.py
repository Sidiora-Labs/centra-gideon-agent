"""Exercise assembled runtime registration and live SQLite snapshot preservation."""
import os
from pathlib import Path
import sqlite3
import tarfile

import pytest
from aiohttp import ClientSession, CookieJar, web
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.handlers.capabilities import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, reset_secret_cache
from gideon.operations.durability import inventory
from gideon.workspace.capabilities.identity.store import StoryStore
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.snapshot import snapshot_main, _tree_ignore_dbs


@pytest.fixture
def home(tmp_path):
    previous=os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME']=str(tmp_path/'home')
    Path(os.environ['GIDEON_HOME']).mkdir()
    reset_secret_cache()
    try:
        yield Path(os.environ['GIDEON_HOME'])
    finally:
        reset_secret_cache()
        if previous is None: os.environ.pop('GIDEON_HOME',None)
        else: os.environ['GIDEON_HOME']=previous


@pytest.mark.asyncio
async def test_all_capability_routes_share_the_actual_authenticated_application(home):
    state=ConsoleState(ConversationDirectory(AppConfig()),start_time=0)
    app=web.Application(middlewares=[token_auth_middleware()])
    app['state']=state
    register(app)
    paths={route.resource.canonical for route in app.router.routes()}
    required={
        '/api/capabilities/workspace','/api/capabilities/knowledge/anniversaries',
        '/api/capabilities/knowledge/captures','/api/capabilities/identity/stories',
        '/api/capabilities/identity/twin','/api/capabilities/wellbeing/measurements',
        '/api/capabilities/wellbeing/labs','/api/capabilities/communications/people',
        '/api/capabilities/media/sketches','/api/capabilities/creative/ingredients',
        '/api/capabilities/music/items','/api/capabilities/music/catalog/{kind}',
        '/api/capabilities/experience/stories','/api/capabilities/platform/catalog',
    }
    assert required <= paths, sorted(required-paths)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    base=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    try:
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
            response=await client.get(base+'/api/capabilities/identity/stories')
            assert response.status in (401,403)
            token=generate_token('capability-owner')
            response=await client.get(base+'/api/capabilities/identity/stories?token='+token)
            assert response.status==200,await response.text()
            response=await client.post(base+'/api/capabilities/identity/stories',json={
                'prompt':'What remained important?','theme':'Learning','text':'Preserving the original source.',
                'request_id':'integration-story'})
            assert response.status in (200,201),await response.text()
            story=await response.json()
            response=await client.get(base+'/api/capabilities/identity/stories/'+story['id'])
            assert (await response.json())['text']==story['text']
            for path in sorted(required-{'/api/capabilities/music/catalog/{kind}'}):
                response=await client.get(base+path)
                assert response.status==200,(path,response.status,await response.text())
            response=await client.get(base+'/api/capabilities/music/catalog/artists')
            assert response.status==200,await response.text()
            for path in [
                '/api/capabilities/identity/guarded-recipes',
                '/api/capabilities/wellbeing/privacy/subjects',
                '/api/capabilities/platform/inference-host',
                '/api/capabilities/knowledge/ideas',
                '/api/capabilities/platform/insights',
                '/api/capabilities/wellbeing/shared',
                '/api/capabilities/music/decks',
                '/api/capabilities/platform/accounting',
                '/api/capabilities/wellbeing/exports',
                '/api/capabilities/platform/compositions',
                '/api/capabilities/experience/world-engine',
                '/api/capabilities/identity/lifecycle',
                '/api/capabilities/knowledge/journals?date=2026-09-25&timezone=UTC',
                '/api/capabilities/music/listening/history',
                '/api/capabilities/platform/forecast',
                '/api/capabilities/knowledge/reviews',
                '/api/capabilities/identity/goal-plans',
                '/api/capabilities/wellbeing/memory/cards',
                '/api/capabilities/wellbeing/life/config',
                '/api/capabilities/music/models3d/config',
                '/api/capabilities/music/assemblies',
                '/api/capabilities/platform/maintenance',
                '/api/capabilities/platform/pr-screening',
                '/api/capabilities/platform/cadence',
                '/api/capabilities/communications/calendar/sources',
                '/api/capabilities/knowledge/types', '/api/capabilities/knowledge/archives',
                '/api/capabilities/identity/fidelity/cases', '/api/capabilities/identity/goals/goals',
                '/api/capabilities/identity/progress', '/api/capabilities/identity/continuity',
                '/api/capabilities/wellbeing/apple/metrics', '/api/capabilities/wellbeing/substances/entries',
                '/api/capabilities/wellbeing/genome/sources', '/api/capabilities/music/rounds',
                '/api/capabilities/music/midi', '/api/capabilities/platform/harnesses',
                '/api/capabilities/platform/comparisons', '/api/capabilities/platform/references',
            ]:
                response=await client.get(base+path)
                assert response.status==200,(path,response.status,await response.text())
            assert (home/'capabilities/identity/stories.sqlite3').is_file()
            assert StoryStore(home/'capabilities/identity/stories.sqlite3').get(story['id'])==story
    finally:
        await runner.cleanup()
        state.knowledge_store.close()


def test_every_capability_database_uses_the_sqlite_backup_inventory():
    entries=[e for e in inventory.all_entries() if e.id.startswith('capability_') and e.kind == inventory.KIND_SQLITE]
    assert len(entries)>=10
    assert len({e.path for e in entries})==len(entries)
    for entry in entries:
        assert entry.kind==inventory.KIND_SQLITE
        assert entry in inventory.sqlite_entries()
        assert entry in inventory.backup_entries()
        assert entry.merge==inventory.MERGE_REPLACE_ONLY
        assert inventory.claim_for(entry.path)==entry
        assert not entry.derived


def test_unregistered_sqlite3_is_detected(home):
    path=home/'capabilities/new/unknown.sqlite3'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE actual(value TEXT)')
    report=inventory.audit_home(home)
    assert not report.ok
    assert 'capabilities/new/unknown.sqlite3' in report.undeclared_dbs


def test_declared_sqlite_sidecars_never_enter_tree_copies():
    ignore=_tree_ignore_dbs({'records.sqlite3','other.db'})
    names=['records.sqlite3','records.sqlite3-wal','records.sqlite3-shm','other.db-wal','image.png','notes.txt']
    assert ignore('',names)==set(names[:4])
    assert 'image.png' not in ignore('',names)
    assert 'notes.txt' not in ignore('',names)


def test_snapshot_preserves_live_wal_records_without_raw_overwrite(home,tmp_path):
    path=home/'capabilities/identity/stories.sqlite3'
    store=StoryStore(path)
    first=store.create(prompt='Original?',theme='History',text='First answer',request_id='first')
    reader=sqlite3.connect(path)
    reader.execute('PRAGMA journal_mode=WAL')
    reader.execute('BEGIN')
    reader.execute('SELECT * FROM stories').fetchall()
    second=store.create(prompt='Later?',theme='History',text='Committed in the WAL',request_id='second')
    people=PeopleStore(home/"capabilities/communications")
    person=people.save({'name':'Snapshot Person','notes':'Persistent','ring':'core','cadence_days':7,'identities':[]})
    assert Path(str(path)+'-wal').stat().st_size>0
    private=home/'capabilities/workspace/desktops/desktop-private/Xauthority'
    private.parent.mkdir(parents=True)
    private.write_bytes(b'ephemeral-display-cookie')
    assert inventory.is_ignored(str(private.relative_to(home)))
    cadence=home/'capabilities/platform/cadence.json'
    cadence.parent.mkdir(parents=True,exist_ok=True)
    cadence.write_text('{"enabled":true}')
    output=tmp_path/'snapshots'
    try:
        assert snapshot_main([str(output),'--keep','1'])==0
    finally:
        reader.rollback()
        reader.close()
    archives=list(output.glob('gideon-snapshot-*.tar.gz'))
    assert len(archives)==1
    with tarfile.open(archives[0]) as archive:
        members=archive.getnames()
        assert not any('desktop-private' in name for name in members)
        cadence_member=next(name for name in members if name.endswith('/capabilities/platform/cadence.json'))
        assert archive.extractfile(cadence_member).read()==cadence.read_bytes()
        source=next(name for name in members if name.endswith('/capabilities/identity/stories.sqlite3'))
        assert not any(name.endswith(('.sqlite3-wal','.sqlite3-shm')) for name in members)
        restored=tmp_path/'restored.sqlite3'
        restored.write_bytes(archive.extractfile(source).read())
        people_member=next(name for name in members if name.endswith('/capabilities/communications/people.sqlite3'))
        restored_people=tmp_path/'people.sqlite3'
        restored_people.write_bytes(archive.extractfile(people_member).read())
    copied=StoryStore(restored)
    assert copied.get(first['id'])==first
    assert copied.get(second['id'])==second
    with sqlite3.connect(restored_people) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert person['id'] in [row[0] for row in connection.execute('SELECT id FROM people')]
    assert store.get(second['id'])==second
    assert people.get(person['id'])==person


def test_corrupt_declared_database_fails_snapshot_instead_of_raw_copy(home,tmp_path):
    path=home/'capabilities/identity/stories.sqlite3'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'not a database')
    with pytest.raises(RuntimeError,match='safely snapshot database'):
        snapshot_main([str(tmp_path/'output')])
    assert not list((tmp_path/'output').glob('*.tar.gz'))
    assert path.read_bytes()==b'not a database'


def test_private_fact_store_is_never_an_export_or_replication_entry():
    entry=inventory.claim_for('capabilities/privacy.sqlite3')
    assert entry is not None
    assert entry.secret
    assert entry.kind==inventory.KIND_SQLITE
    assert entry.domain==inventory.DOMAIN_SECURITY
    assert entry.merge==inventory.MERGE_REPLACE_ONLY
    assert entry not in inventory.export_entries()
    assert entry.path in inventory.secret_paths()
    assert entry in inventory.sqlite_entries()
    assert inventory.is_ignored(entry.path+'-wal')
    assert inventory.is_ignored(entry.path+'-shm')
