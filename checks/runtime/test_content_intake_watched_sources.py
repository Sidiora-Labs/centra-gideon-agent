import asyncio
import codecs
import os
from types import SimpleNamespace

import pytest
from gideon.cognition.knowledge.source_engine import SourceEngine
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.integrations.knowledge_providers.base import SourceItem, SourcePollResult, KnowledgeSourceProvider
from gideon.integrations.knowledge_providers.dir_source import DirSourceProvider
from gideon.workspace.uploads import content_intake as intake


class Provider(KnowledgeSourceProvider):
    name = 'intake-source'
    display_name = 'Intake source'
    def __init__(self):
        self.items, self.cursor, self.observed = [], 'next', []
    async def list_sources(self): return []
    async def search(self, query, limit=10): return []
    async def get_item(self, item_id): return None
    async def poll(self, source_id, cursor=''):
        self.observed.append(cursor)
        return SourcePollResult(items=self.items, cursor=self.cursor)


def cfg():
    return SimpleNamespace(max_items_per_poll=50, poll_interval_default_secs=1, network_floor_secs=0)


def setup(tmp_path, provider):
    store = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    sid = store.create_source(name='Intake',provider=provider.name,kind='fixture',item_type='note')
    queued=[]
    engine=SourceEngine(store,SimpleNamespace(enqueue=queued.append),providers_lister=lambda:[provider], config_loader=cfg)
    return store,sid,engine,queued


@pytest.mark.asyncio
async def test_source_failed_receipts_preserve_edits_and_unavailable_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path / 'home'))
    provider=Provider()
    store,sid,engine,queued=setup(tmp_path,provider)
    try:
        provider.items=[SourceItem(guid='safe',title='Accepted title',content='Accepted source content.')]
        assert await engine.poll_source(store.get_source(sid),cfg()) == 1
        accepted=store.find_source_item(sid,'safe')
        provider.items=[SourceItem(guid='unsafe-title',title='safe\u202eevil',content='Ordinary content.'),
                        SourceItem(guid='unsafe-metadata',title='Metadata',content='Ordinary content.',metadata={'nested':{'script':'rm -rf /\n'}}),
                        SourceItem(guid='safe',title='Modified',content='rm -rf /\n',change='modified')]
        provider.cursor='refused-next'
        assert await engine.poll_source(store.get_source(sid),cfg()) == 0
        assert store.find_source_item(sid,'safe') == accepted
        for guid in ('unsafe-title','unsafe-metadata'):
            failed=store.find_source_item(sid,guid)
            assert failed['processing_status'] == 'failed' and failed['content'] == '' and failed['title'] == 'Source content refused'
            assert not failed['url'] and failed['file_metadata']['content_refusal'] == 'upload_content_refused'
        assert len(queued) == 1 and store.get_source(sid)['health_status'] == 'degraded'
        cursor=store.get_source_cursor(sid)
        provider.items=[SourceItem(guid='safe',title='Later',content='Later source content.',change='modified')]
        provider.cursor='must-not-advance'
        scanner=intake.CHILD_MODULE
        monkeypatch.setattr(intake,'CHILD_MODULE','gideon.workspace.uploads.missing_scanner_dependency')
        assert await engine.poll_source(store.get_source(sid),cfg()) == 0
        assert store.get_source_cursor(sid) == cursor and store.find_source_item(sid,'safe') == accepted
        assert len(queued) == 1 and store.get_source(sid)['health_status'] == 'degraded'
        monkeypatch.setattr(intake,'CHILD_MODULE',scanner)
        assert await engine.poll_source(store.get_source(sid),cfg()) == 1
        assert provider.observed[-1] == cursor
        updated=store.find_source_item(sid,'safe')
        assert updated['id'] == accepted['id'] and updated['content'] == 'Later source content.' and len(queued) == 2
        provider.items=[SourceItem(guid='safe',title='',change='deleted',metadata={'source_deleted_at':'2026-10-06T00:00:00'})]
        assert await engine.poll_source(store.get_source(sid),cfg()) == 0
        assert store.find_source_item(sid,'safe')['is_archived'] and len(queued) == 2
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_source_payload_is_immutable_while_actual_scanner_awaits(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path / 'home'))
    provider=Provider()
    item=SourceItem(guid='owned',title='Before',content='Before source bytes.',metadata={'nested':['Before']})
    provider.items=[item]
    store,sid,engine,queued=setup(tmp_path,provider)
    real=asyncio.create_subprocess_exec
    async def spawn(*args,**kwargs):
        child=await real(*args,**kwargs)
        item.content='rm -rf /\n'
        item.title='safe\u202eevil'
        item.metadata['nested'][0]='rm -rf /\n'
        return child
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    try:
        assert await engine.poll_source(store.get_source(sid),cfg()) == 1
        kept=store.find_source_item(sid,'owned')
        assert kept['content'] == 'Before source bytes.' and kept['title'] == 'Before'
        with pytest.raises(PermissionError):
            engine._persist(store.get_source(sid),item)
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_watched_files_decode_no_follow_and_scan_retry(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path / 'home'))
    root=tmp_path / 'watched'
    root.mkdir()
    store=KnowledgeStore(str(tmp_path / 'knowledge.db'))
    clock=[1000000.]
    provider=DirSourceProvider(store,now_fn=lambda:clock[0])
    sid=store.create_source(name='Folder',provider=provider.name,kind='dir',spec={'path':str(root),'debounce_secs':1},item_type='note')
    queued=[]
    engine=SourceEngine(store,SimpleNamespace(enqueue=queued.append),providers_lister=lambda:[provider], config_loader=cfg,now_fn=lambda:clock[0])
    def write(name,raw):
        path=root / name
        path.write_bytes(raw)
        os.utime(path,(clock[0]-10,clock[0]-10))
        return path
    try:
        assert await engine.poll_source(store.get_source(sid),cfg()) == 0
        write('good.txt',codecs.BOM_UTF16_LE + 'Accepted Unicode source.'.encode('utf-16-le'))
        write('evil.txt',codecs.BOM_UTF16_LE + 'safe\u202eevil'.encode('utf-16-le'))
        write('binary.txt',b'\0Ordinary binary bytes.')
        outside=tmp_path / 'outside.txt';outside.write_text('Outside root content.')
        (root / 'alias.txt').symlink_to(outside)
        assert await engine.poll_source(store.get_source(sid),cfg()) == 1
        assert store.find_source_item(sid,'good.txt')['content'] == 'Accepted Unicode source.'
        assert store.find_source_item(sid,'evil.txt')['processing_status'] == 'failed'
        assert store.find_source_item(sid,'binary.txt')['processing_status'] == 'failed'
        assert store.find_source_item(sid,'alias.txt') is None
        before=store.find_source_item(sid,'good.txt')
        clock[0]+=20
        write('good.txt',b'Later accepted text.')
        cursor=store.get_source_cursor(sid)
        scanner=intake.CHILD_MODULE
        monkeypatch.setattr(intake,'CHILD_MODULE','gideon.workspace.uploads.missing_scanner_dependency')
        assert await engine.poll_source(store.get_source(sid),cfg()) == 0
        assert store.get_source_cursor(sid) == cursor and store.find_source_item(sid,'good.txt') == before
        assert store.get_source(sid)['health_status'] == 'degraded'
        monkeypatch.setattr(intake,'CHILD_MODULE',scanner)
        assert await engine.poll_source(store.get_source(sid),cfg()) == 1
        assert store.find_source_item(sid,'good.txt')['content'] == 'Later accepted text.'
        with pytest.raises(PermissionError):
            await provider._read({'path':str(root)},'../outside.txt')
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_watched_text_scan_race_keeps_baseline_and_queue_empty(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path / 'home'))
    root=tmp_path / 'watched';root.mkdir()
    store=KnowledgeStore(str(tmp_path / 'knowledge.db'))
    provider=DirSourceProvider(store,now_fn=lambda:1000000.)
    sid=store.create_source(name='Folder',provider=provider.name,kind='dir',spec={'path':str(root),'debounce_secs':1},item_type='note')
    queued=[]
    engine=SourceEngine(store,SimpleNamespace(enqueue=queued.append),providers_lister=lambda:[provider],config_loader=cfg)
    try:
        assert await engine.poll_source(store.get_source(sid),cfg()) == 0
        cursor=store.get_source_cursor(sid)
        path=root / 'raced.txt';path.write_text('Original watched content.');os.utime(path,(999990,999990))
        real=asyncio.create_subprocess_exec
        count=0
        async def spawn(*args,**kwargs):
            nonlocal count
            child=await real(*args,**kwargs)
            count+=1
            if count == 2:
                path.write_text('Different bytes after snapshot.')
            return child
        monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
        assert await engine.poll_source(store.get_source(sid),cfg()) == 0
        assert store.get_source_cursor(sid) == cursor and not queued
        assert store.find_source_item(sid,'raced.txt') is None
        assert store.get_source(sid)['health_status'] == 'degraded'
    finally:
        store.db.close()
