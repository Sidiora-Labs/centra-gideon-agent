"""Stored app schedules enter the actual supported native agent at their live tier."""
import asyncio
import json
import logging
import time
from types import SimpleNamespace
import pytest

@pytest.mark.asyncio
@pytest.mark.parametrize('tier', ['text', 'read', 'tools'])
async def test_actual_app_clock_dispatch_enters_live_native_tier(tmp_path, monkeypatch, tier):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.setenv('GIDEON_SKIP_SKILL_SEED', '1')
    script=tmp_path/'script.json'
    script.write_text(json.dumps({'version':1,'turns':[{'text':'SCHEDULE-COMPLETE'}]}))
    monkeypatch.setenv('GIDEON_SCRIPTED_MODEL_SCRIPT', str(script))
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.engine.subagent import DelegationSupervisor
    from gideon.cognition.context import PromptAssembler
    from gideon.extensions.providers.provider_bridge import create_provider_factory
    from gideon.extensions.providers.use_cases import save_active_models
    from gideon.integrations.llm.registry import register_scripted_provider_type
    from gideon.integrations.llm.scripted import ScriptedProvider
    from gideon.integrations.action_providers.services import ActionServices, get_action_services, set_action_services
    from gideon.extensions.apps.app_crons import reconcile_app_crons
    from gideon.extensions.apps.app_work import for_job
    from gideon.automation.triggers.store import TriggerStore
    from gideon.automation.triggers import grants, tools
    from gideon.automation.triggers.service import tick, to_iso
    from gideon.engine.trigger_dispatch import TriggerDispatch
    from gideon.engine.gateway import RuntimeCoordinator
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.security.approval_answer import YOU, TRIGGER, APP
    from gideon.security.session_credentials import current_work
    from gideon.engine.delegation_host import DelegationHost
    installed=tmp_path/'apps'/'clock-app'; installed.mkdir(parents=True)
    meta={'name':'clock-app','enabled':True,'version':'1.0.0'}
    (installed/'installed.json').write_text(json.dumps(meta))
    manifest={'name':'clock-app','displayName':'Clock App','version':'1.0.0','permissions':{'agent':tier,'cron':True},'crons':[{'name':'check','every':60,'message':'Report the scheduled task'}]}
    (installed/'app.json').write_text(json.dumps(manifest))
    register_scripted_provider_type()
    save_active_models({'chat':['Scripted:scripted-1'],'orchestration':['Scripted:scripted-1']})
    cfg=AppConfig.load(); cfg.agent.provider='native'; cfg.save()
    actual=[]; observations=[]
    factory=create_provider_factory('chat')
    def runtime_factory(key, **options):
        value=factory(key,**options); actual.append(value); return value
    original=ScriptedProvider.complete
    async def observe(self,*args,**kwargs):
        proof=current_work(); assert proof is not None
        runtime=next(item for item in actual if item._session_key==proof.session_key)
        assert proof.initiator.kind==TRIGGER and proof.initiator.name=='app:clock-app:check'
        assert proof.work_actor.kind==APP and proof.created_by_app=='clock-app'
        assert proof.memory_mode=='temporary' and proof.trigger_origin is not None
        assert runtime._app_work.current_tier()==tier
        if tier=='text': assert not runtime._tool_index
        if tier=='read': assert all(runtime._tool_risk.get(name,'')=='read' for name in runtime._tool_index)
        observations.append(proof)
        async for event in original(self,*args,**kwargs): yield event
    monkeypatch.setattr(ScriptedProvider,'complete',observe)
    sessions=ConversationDirectory(AppConfig.load(),provider_factory=runtime_factory)
    manager=DelegationSupervisor(sessions,PromptAssembler(),validate_trigger_start_approval=DelegationHost.validate_trigger_start_approval)
    state=ConsoleState(sessions=sessions,subagents=manager,start_time=time.time())
    previous=get_action_services(); set_action_services(ActionServices(state,asyncio.create_task,subagents=manager))
    try:
        store=TriggerStore(); reconcile_app_crons(store)
        row=store.get('app:clock-app:check'); assert row and row.trigger.name=='Clock App: check'
        trigger=row.trigger
        assert 'approval_mode' not in trigger.workflow['inline']['config']
        # The owner may edit the message, but no job can select a broader posture.
        assert not tools.update(store,trigger_id=trigger.id,patch={'workflow':{'inline':{'config':{'approval_mode':'auto'}}}}).ok
        trigger.workflow['inline']['config']['task_template']='Owner edited scheduled task'
        trigger.next_fire_at=to_iso(time.time()-1)
        store.upsert(trigger)
        question=grants.question(trigger)
        assert grants.grant(trigger,confirmed_revision=question.revision,principal=YOU,shown=question.shown)
        store.upsert(trigger)
        assert for_job(trigger.id).current_tier()==tier
        admission=await tick(store,now=time.time()); assert len(admission.fires)==1
        runtime=RuntimeCoordinator(AppConfig.load()); runtime.dashboard_state=state
        await TriggerDispatch(runtime,store.get(trigger.id).trigger,{},'trigger.fired',logging.getLogger(__name__)).run()
        deadline=time.monotonic()+8
        while not observations and time.monotonic()<deadline: await asyncio.sleep(.02)
        assert observations, [(info.done,info.error,info.result) for info in manager.all_agents]
        # Current installed state is checked again, not cached in the job's Allow.
        meta['enabled']=False; (installed/'installed.json').write_text(json.dumps(meta))
        assert not for_job(trigger.id).current_tier()
        from gideon.security.durable_work import bound_for_trigger_origin
        assert bound_for_trigger_origin(observations[0].trigger_origin,trigger.id,'subagent:new-denied',actual[0]._app_work) is None
    finally:
        for info in manager.all_agents:
            if not info.done: await manager.cancel(info.id)
        set_action_services(previous)
