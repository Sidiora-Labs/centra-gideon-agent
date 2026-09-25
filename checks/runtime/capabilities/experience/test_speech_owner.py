import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.speech_owner import SpeechOwner
from gideon.workspace.capabilities.experience.proactive_speech import latest_digest, start_digest
from gideon.workspace.capabilities.experience.narration import get_narration_jobs
from gideon.workspace.capabilities.experience.store import Conflict
from gideon.workspace.capabilities.experience.tools import ExperienceTools


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    return SpeechOwner(ExperienceStore(tmp_path))


def credentials(lease):
    return {key: lease[key] for key in ('owner','token')}


def test_default_disabled_and_manual_ownership_without_proactive_opt_in(owner):
    assert owner.state() == {'enabled':False,'owner':'','expires_at':0}
    lease = owner.change('claim', {'owner':'manual'})
    assert lease['token']
    assert lease['owner'] == 'manual'
    owner.verify(credentials(lease))
    with pytest.raises(Conflict, match='disabled'):
        owner.verify(credentials(lease), proactive=True)
    public = owner.state()
    assert public['owner'] == 'manual'
    assert 'token' not in public
    assert owner.change('enable', {'enabled':True})['enabled']
    owner.verify(credentials(lease), proactive=True)
    disabled = owner.change('enable', {'enabled':False})
    assert 'token' not in disabled
    owner.verify(credentials(lease))


def test_claim_conflict_renew_release_and_restart(owner, tmp_path):
    lease = owner.change('claim', {'owner':'tabone'})
    restarted = SpeechOwner(ExperienceStore(tmp_path))
    for name in ['tabtwo','tabone']:
        with pytest.raises(Conflict, match='another'):
            restarted.change('claim', {'owner':name})
    renewed = restarted.change('renew', credentials(lease))
    assert renewed['token'] == lease['token']
    assert renewed['expires_at'] >= lease['expires_at']
    assert restarted.change('release', credentials(lease))['owner'] == ''
    with pytest.raises(Conflict):
        owner.verify(credentials(lease))
    replacement = owner.change('claim', {'owner':'tabtwo'})
    assert replacement['token'] != lease['token']
    with pytest.raises(Conflict):
        owner.change('release', credentials(lease))
    owner.verify(credentials(replacement))


def test_simultaneous_real_connections_have_one_audible_owner(owner):
    def claim(number):
        try:
            return owner.change('claim', {'owner':f'tab{number}'})
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(claim, range(6)))
    winners = [result for result in results if result]
    assert len(winners) == 1
    assert owner.state()['owner'] == winners[0]['owner']
    owner.verify(credentials(winners[0]))


def test_expired_persisted_lease_cannot_renew_and_can_be_replaced(owner):
    expired = owner.change('claim', {'owner':'old'})
    expired['expires_at'] = 1
    with owner.store.connection() as db:
        db.execute('UPDATE speech_owner SET body=? WHERE id=1', (json.dumps(expired),))
    with pytest.raises(Conflict):
        owner.verify(credentials(expired))
    with pytest.raises(Conflict):
        owner.change('renew', credentials(expired))
    fresh = owner.change('claim', {'owner':'new'})
    assert fresh['owner'] == 'new'
    assert fresh['token'] != expired['token']


@pytest.mark.parametrize('action,body', [('enable',{'enabled':'yes'}),('enable',{'enabled':1}),('claim',{'owner':'../outside'}),('renew',{}),('release',[]),('unknown',{}),('claim',{'owner':'browser','home':'other'}),('enable',{'enabled':True,'provider':'external'})])
def test_malformed_owner_actions_do_not_mutate(owner, action, body):
    before = owner.state()
    with pytest.raises(ValueError):
        owner.change(action,body)
    assert owner.state() == before


def test_foreign_home_and_wrong_tokens_fail_closed(owner, tmp_path):
    lease = owner.change('claim', {'owner':'one'})
    other = SpeechOwner(ExperienceStore(tmp_path/'other'))
    with pytest.raises(Conflict):
        other.verify(credentials(lease))
    with pytest.raises(Conflict):
        owner.verify({'owner':'one','token':'wrong'})
    with pytest.raises(Conflict, match='scope'):
        latest_digest(tmp_path/'other')
    assert other.state()['owner'] == ''


@pytest.mark.asyncio
async def test_proactive_requires_opt_in_and_actual_installed_digest(owner, tmp_path):
    jobs = get_narration_jobs(owner.store)
    lease = owner.change('claim', {'owner':'browser'})
    with pytest.raises(Conflict, match='disabled'):
        start_digest(jobs, owner, credentials(lease))
    owner.change('enable', {'enabled':True})
    result = start_digest(jobs, owner, credentials(lease))
    assert result['source']['state'] == 'unavailable'
    assert 'install' in result['source']['reason']
    assert result['narration'] is None
    assert jobs.tasks == {}
    assert jobs.artifacts.list() == []
    assert SpeechOwner(ExperienceStore(tmp_path)).state()['enabled'] is True
    with pytest.raises(ValueError):
        start_digest(jobs, owner, {**credentials(lease),'text':'Untrusted proactive prose'})
    await jobs.close()


@pytest.mark.asyncio
async def test_native_read_omits_credentials_and_cannot_opt_in(owner):
    owner.change('enable',{'enabled':True})
    lease = owner.change('claim',{'owner':'browser'})
    provider = ExperienceTools(owner.store)
    result = await provider.invoke('experience_speech_state',{})
    assert result.success
    assert json.loads(result.output) == {'owner':owner.state()}
    assert lease['token'] not in result.output
    refused = await provider.invoke('experience_speech_enable',{'enabled':True})
    assert not refused.success


@pytest.mark.asyncio
async def test_actual_http_owner_and_proactive_routes(owner):
    app = web.Application()
    app[STORE] = owner.store
    register(app)
    async with TestClient(TestServer(app)) as client:
        base = '/api/capabilities/experience'
        state = await client.get(base+'/speech-owner')
        assert state.status == 200
        assert not (await state.json())['owner']['enabled']
        enable = await client.post(base+'/speech-owner/enable',json={'enabled':True})
        assert enable.status == 200
        assert 'token' not in (await enable.json())['owner']
        claim = await client.post(base+'/speech-owner/claim',json={'owner':'browser'})
        assert claim.status == 200
        lease = (await claim.json())['owner']
        second = await client.post(base+'/speech-owner/claim',json={'owner':'second'})
        assert second.status == 409
        renewed = await client.post(base+'/speech-owner/renew',json=credentials(lease))
        assert renewed.status == 200
        result = await client.post(base+'/proactive-speech',json=credentials(lease))
        assert result.status == 200
        assert (await result.json())['narration'] is None
        bad = await client.post(base+'/proactive-speech',json={**credentials(lease),'text':'arbitrary'})
        assert bad.status == 400
        released = await client.post(base+'/speech-owner/release',json=credentials(lease))
        assert released.status == 200
        assert (await client.post(base+'/proactive-speech',json=credentials(lease))).status == 409


def persist_digest(home, body='Two tasks need your review.'):
    from gideon.core.config.loader import AppConfig
    from gideon.automation.triggers.models import Trigger
    from gideon.automation.triggers.store import TriggerStore
    from gideon.automation.workflows import store
    from gideon.automation.workflows.models import WorkflowRun, RunStatus, NodeInstance, InstanceState
    cfg = AppConfig.load()
    cfg.proactive.triage_enabled = True
    cfg.save()
    TriggerStore().upsert(Trigger(id='system:triage:digest',name='Digest',kind='clock',spec={'kind':'cron','expr':'0 8 * * *'}))
    run = store.create(WorkflowRun(id='',workflow_name='morning-triage',status=RunStatus.COMPLETE))
    store.write_spec(run.id, {'root': {'id':'triage','kind':'action','provider':'triage'}})
    store.write_state(run.id, {'root':NodeInstance(path='root',state=InstanceState.DONE)})
    store.write_output(run.id, 'root', json.dumps({'digest_title':'Morning review','digest_body':body,'collected':2}))
    return run


@pytest.mark.asyncio
async def test_real_persisted_digest_source_and_no_provider_boundary(owner, tmp_path):
    from gideon.automation.workflows import store
    run = persist_digest(tmp_path)
    source = latest_digest(tmp_path)
    assert source['state'] == 'ready'
    assert source['run_id'] == run.id
    assert source['text'] == 'Morning review\n\nTwo tasks need your review.'
    owner.change('enable',{'enabled':True})
    lease = owner.change('claim',{'owner':'browser'})
    jobs = get_narration_jobs(owner.store)
    result = start_digest(jobs,owner,credentials(lease))
    assert result['narration']['run_id'] == run.id
    assert result['narration']['source_kind'] == 'proactive_digest'
    assert result['narration']['source_hash'] == source['source_hash']
    assert start_digest(jobs,owner,credentials(lease))['narration']['id'] == result['narration']['id']
    await __import__('asyncio').gather(*tuple(jobs.tasks.values()))
    job = jobs.get(result['narration']['id'])
    assert job['status'] == 'unavailable'
    assert jobs.artifacts.list() == []
    store.write_output(run.id,'root',json.dumps({'digest_title':'Morning review','digest_body':'An updated recorded digest.'}))
    changed = latest_digest(tmp_path)
    assert changed['source_hash'] != source['source_hash']
    assert changed['run_id'] == source['run_id']
    await jobs.close()


def test_notification_mute_and_actual_quiet_window_defer_source(owner,tmp_path):
    from datetime import datetime
    from gideon.extensions.providers.entity_routes import _save_entity_settings
    persist_digest(tmp_path)
    _save_entity_settings('notifications',{'mute_all':True})
    assert latest_digest(tmp_path)['state'] == 'deferred'
    now = datetime.now()
    start = now.strftime('%H:%M')
    end = f'{(now.hour+1)%24:02d}:{now.minute:02d}'
    _save_entity_settings('notifications',{'mute_all':False,'quiet_hours_enabled':True,'quiet_hours_start':start,'quiet_hours_end':end})
    assert latest_digest(tmp_path)['state'] == 'deferred'
    _save_entity_settings('notifications',{'mute_all':False,'quiet_hours_enabled':False})
    assert latest_digest(tmp_path)['state'] == 'ready'


@pytest.mark.asyncio
async def test_explicit_retry_preserves_previous_failed_receipt(owner,tmp_path):
    import asyncio
    persist_digest(tmp_path)
    owner.change('enable',{'enabled':True})
    lease = owner.change('claim',{'owner':'browser'})
    jobs = get_narration_jobs(owner.store)
    first = start_digest(jobs,owner,credentials(lease))['narration']
    await asyncio.gather(*tuple(jobs.tasks.values()))
    assert jobs.get(first['id'])['status'] == 'unavailable'
    assert start_digest(jobs,owner,credentials(lease))['narration']['id'] == first['id']
    retry = start_digest(jobs,owner,credentials(lease),retry=True)['narration']
    assert retry['id'] != first['id']
    assert retry['source_hash'] == first['source_hash']
    assert jobs.get(first['id'])['status'] == 'unavailable'
    await asyncio.gather(*tuple(jobs.tasks.values()))
    assert jobs.get(retry['id'])['status'] == 'unavailable'
    assert jobs.artifacts.list() == []
    await jobs.close()
