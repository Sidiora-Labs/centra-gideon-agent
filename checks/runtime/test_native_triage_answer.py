import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.proactive import carry, collect, digest_state, pipeline, surface
from gideon.integrations.inbox import InboxItem, InboxStore, InboxState, ItemStatus
from gideon.integrations.inbox_service import InboxService
from gideon.integrations.action_providers import services
from gideon.automation.workflows import store, journal
from gideon.automation.workflows.models import WorkflowRun, RunStatus, NodeInstance, InstanceState


def persist(run_id, output, at):
    store.create(WorkflowRun(id=run_id, workflow_name=surface.TRIAGE_WORKFLOW, status=RunStatus.COMPLETE, created_at=at, started_at=at, completed_at=at))
    from gideon.automation.workflows.models import Node, walk
    specification = {'root':{'id':surface.TRIAGE_NODE_ID,'kind':'action','config':{'provider':'triage-digest'}}}
    store.write_spec(run_id, specification)
    path = next(path for path, node in walk(Node.from_dict(specification['root'])) if node.id == surface.TRIAGE_NODE_ID)
    store.write_state(run_id,{path:NodeInstance(path=path,state=InstanceState.DONE)})
    store.write_output(run_id,path,output)


@pytest.fixture
async def native(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    from gideon.core.config.loader import AppConfig
    cfg=AppConfig.load();cfg.proactive.triage_enabled=True;cfg.save()
    from gideon.automation.triggers.store import TriggerStore
    from gideon.automation.triggers.models import Trigger
    TriggerStore().upsert(Trigger(id=digest_state.TRIAGE_TRIGGER_ID,name='Morning triage',kind='clock',created_by='system',spec={'kind':'cron','expr':'0 8 * * *'}))
    inbox=InboxStore();inbox.load()
    for ident in ['message-a','message-b']:
        inbox.add(InboxItem(ident,'mail','Mail',None,'A local message','sender','Sender',source='mail',created_at=datetime.now(timezone.utc).timestamp()))
    inbox.save()
    svc=InboxService(state=InboxState(),store=inbox,user_name='Owner')
    state=SimpleNamespace(_inbox_svc=svc,_sessions={},broadcast_ws=lambda *args:None,push_refresh=lambda *args:None)
    prior=services.get_action_services()
    services.set_action_services(services.ActionServices(state=state,spawn_background=lambda coro:None))
    from gideon.interfaces.dashboard.handlers.proactive import api_proactive_reply
    from gideon.interfaces.dashboard.token_auth import generate_token,token_auth_middleware
    app=web.Application(middlewares=[token_auth_middleware(port=0)]);app['state']=state
    app.router.add_post('/reply',api_proactive_reply)
    client=TestClient(TestServer(app),headers={'Authorization':'Bearer '+generate_token('owner',kind='browser')});await client.start_server()
    yield client,inbox,state
    await client.close();services.set_action_services(prior)


@pytest.mark.asyncio
async def test_persisted_unanswered_proposal_recurs_and_yes_runs_exact_native_item(native):
    client,inbox,state=native
    now=datetime.now(timezone.utc)
    async def proposals(_prompt,**kwargs):return {'proposals':[{'item_id':'1','action_type':'archive','tier':'low','pattern_key':'archive:sender','reasoning':'Local archive'}]}
    first=await pipeline.run_triage(collect.collect_inbox(inbox),gate_enabled=False,completion=proposals,window_start=(now-timedelta(hours=1)).isoformat())
    persist('triage-before',first.summary(),(now-timedelta(minutes=1)).isoformat())
    old=digest_state.current_view();assert len(old.get('pending',[]))==1, old
    pending=carry.waiting_from(old)
    second=await pipeline.run_triage([],gate_enabled=False,waiting=pending,now=now,window_start=now.isoformat())
    assert second.llm_calls==0
    persist('triage-current',second.summary(),now.isoformat())
    current=digest_state.current_view();assert current['pending'][0]['carried_over']
    source=current['pending'][0]['source_id'];ordinal=current['pending'][0]['ordinal']
    response=await client.post('/reply',json={'run_id':'triage-current','text':ordinal+' yes'})
    assert response.status==200,await response.text()
    result=(await response.json())['results'][0];assert result['executed'],result
    assert inbox.items[source].status==ItemStatus.HANDLED.value
    untouched=next(k for k in inbox.items if k!=source);assert inbox.items[untouched].status==ItemStatus.PENDING.value
    rows=journal.ledger('triage-current');assert any(r['kind']=='auto_executed' for r in rows)
    duplicate=await client.post('/reply',json={'run_id':'triage-current','text':ordinal+' yes'})
    assert (await duplicate.json())['results'][0]['outcome']=='already'
    assert len([r for r in journal.ledger('triage-current') if r['kind']=='auto_executed'])==1


@pytest.mark.asyncio
async def test_failed_native_action_remains_needs_you_without_success_record(native):
    client,inbox,state=native
    now=datetime.now(timezone.utc)
    async def proposals(_prompt,**kwargs):return {'proposals':[{'item_id':'1','action_type':'archive','tier':'low','pattern_key':'archive:sender','reasoning':'Local archive'}]}
    result=await pipeline.run_triage(collect.collect_inbox(inbox),gate_enabled=False,completion=proposals)
    persist('triage-failed',result.summary(),now.isoformat())
    view=digest_state.current_view();target=view['pending'][0]['source_id'];ordinal=view['pending'][0]['ordinal']
    inbox.items.pop(target);inbox.save()
    response=await client.post('/reply',json={'run_id':'triage-failed','text':ordinal+' yes'})
    assert response.status==200,await response.text()
    answer=(await response.json())['results'][0]
    assert not answer['executed'] and answer['not_done'].startswith('Not done:')
    refreshed=digest_state.current_view()
    assert refreshed['pending'][0]['answer_not_done']
    assert not refreshed['auto_done']
    rows=journal.ledger('triage-failed')
    assert any(r['kind']=='auto_failed' for r in rows)
    assert not any(r['kind']=='auto_executed' for r in rows)
