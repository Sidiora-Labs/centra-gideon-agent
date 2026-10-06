import asyncio
import os
import threading
from contextvars import ContextVar

import pytest
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.knowledge.vault import KnowledgeVault, page_basename
from gideon.cognition.memory_vault import MemoryVault
from gideon.cognition.knowledge.file_items import _owned_io


@pytest.mark.asyncio
async def test_real_raw_admission_empty_refusal_and_collision(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path/'home'))
    store = KnowledgeStore(str(tmp_path/'knowledge.db'))
    vault = MemoryVault(None, tmp_path/'vault')
    raw = vault.path/'raw'
    raw.mkdir(parents=True)
    parked = raw/'.ingested'
    parked.mkdir()
    (parked/'safe.py').write_text('prior parked bytes')
    (raw/'safe.py').write_text('print("accepted")\n')
    (raw/'bad.py').write_text('rm -rf /\n')
    (raw/'empty.py').touch()
    queued=[]
    try:
        result = await _owned_io(lambda: vault.sweep_raw(knowledge=store, enqueue=queued.append))
        assert result == {'ingested': 1, 'failed': 1}
        assert len(queued) == 1
        item=store.get_item(queued[0])
        assert item['content'] == 'print("accepted")\n'
        assert (parked/'safe.py').read_text() == 'prior parked bytes'
        assert len(list(parked.glob('safe-*.py'))) == 1
        assert not (raw/'safe.py').exists()
        assert (raw/'bad.py').exists() and (raw/'empty.py').exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_real_knowledge_edit_accepts_and_refuses_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path/'home'))
    store=KnowledgeStore(str(tmp_path/'knowledge.db'))
    vault=KnowledgeVault(store,tmp_path/'vault',mode='two_way')
    try:
        item=store.create_typed_item(item_type='note',title='Native page',content='original body')
        await _owned_io(vault.sync_batch)
        page=vault.path/f'items/{page_basename(store.get_item(item))}.md'
        for text, accepted in [('accepted body',True),('rm -rf /',False)]:
            old=store.get_item(item)['content']
            page.write_text(page.read_text().replace(old.strip(),text))
            stamp=page.stat().st_mtime+2
            os.utime(page,(stamp,stamp))
            result=await _owned_io(vault.sync_batch)
            assert result['absorbed'] == int(accepted)
            assert store.get_item(item)['content'].strip() == (text if accepted else old.strip())
            if not accepted:
                assert result['conflicts'] == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_owned_worker_retains_context_and_joins_before_cancel_returns():
    context=ContextVar('vault_intake_test')
    token=context.set('owned')
    started=threading.Event(); release=threading.Event(); finished=threading.Event()
    def worker():
        assert context.get() == 'owned'
        started.set()
        release.wait(3)
        finished.set()
    try:
        task=asyncio.create_task(_owned_io(worker))
        await asyncio.to_thread(started.wait,3)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    finally:
        release.set()
        context.reset(token)
