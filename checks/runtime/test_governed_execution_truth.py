"""Governed runs report definitive outcomes and persist only executable undo contracts."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from gideon.security.guardrails import autonomy, rungs, ladder, policy
from gideon.integrations.action_providers import registry
from gideon.integrations.action_providers.base import ActionProvider, ActionResult, ActionContext

@pytest.fixture(autouse=True)
def home(tmp_path,monkeypatch):
    path=tmp_path/'home';path.mkdir();monkeypatch.setenv('GIDEON_HOME',str(path))
    (path/'config.json').write_text('{}')
    monkeypatch.setattr('gideon.core.config.loader.config_dir',lambda:path)
    registry._ensure_default_providers_registered()
    monkeypatch.setattr(registry,'_providers',dict(registry._providers))
    monkeypatch.setattr(autonomy,'_REGISTRY',dict(autonomy._REGISTRY))
    monkeypatch.setattr(autonomy,'_PROVIDER_INDEX',dict(autonomy._PROVIDER_INDEX))
    rungs.ensure_core_action_types()
    from gideon.security import sel
    sel.SecurityEventLog._instance=None
    yield path
    sel.SecurityEventLog._instance=None

class Provider(ActionProvider):
    def __init__(self,name='probe',undo=True,result=None):self._name=name;self.undo=undo;self.result=result or ActionResult(True);self.calls=0;self.reverses=0
    @property
    def name(self):return self._name
    @property
    def display_name(self):return 'Probe'
    @property
    def reversal_kinds(self):return ('probe',) if self.undo else ()
    async def execute(self,*args,**kwargs):self.calls+=1;return self.result
    async def reverse(self,handle):self.reverses+=1;return self.result

def register(providers,floor=autonomy.RUNG_AUTONOMOUS,ceiling=autonomy.RUNG_AUTONOMOUS,key='action.probe',runs_code=False):
    for provider in providers:registry.register_action_provider(provider)
    autonomy.register_action_type(autonomy.ActionTypeSpec(key,floor=floor,ceiling=ceiling,providers=tuple(p.name for p in providers),runs_code=runs_code))
    return key

def route(key,undo=False):
    return rungs.RungRoute(rungs.ROUTE_EXECUTE_WITH_UNDO if undo else rungs.ROUTE_EXECUTE,key,autonomy.RUNG_AUTO_WITH_UNDO if undo else autonomy.RUNG_AUTONOMOUS)

def audit(monkeypatch):
    rows=[]
    monkeypatch.setattr('gideon.security.sel.sel',lambda:SimpleNamespace(log_api_access=lambda **row:rows.append(row)))
    return rows

@pytest.mark.parametrize('second',['none','missing','undo'])
def test_every_declared_provider_must_be_able_to_reverse(second):
    a=Provider('first');b=Provider('second',undo=second=='undo');key=register([a,b])
    if second=='missing':registry._providers.pop('second')
    assert rungs.can_be_undone(key) is (second=='undo')

def test_undo_rung_without_contract_asks_and_names_reason():
    key=register([Provider(undo=False)],floor=autonomy.RUNG_AUTO_WITH_UNDO)
    decision=rungs.route_action_type(key,session_key='dashboard:ui')
    assert not decision.executes and decision.rung==autonomy.RUNG_ONE_TAP
    assert decision.narrowed_by==rungs.NARROWED_BY_NO_UNDO
    assert 'cannot be taken back' in decision.reason

def test_unattended_posture_keeps_reversible_work_but_nonundo_keeps_its_own_rung():
    a=register([Provider('undo')]);b=register([Provider('plain',undo=False)],key='action.plain')
    identity=policy.unattended_dispatch_key('probe')
    assert rungs.route_action_type(a,session_key=identity).rung==autonomy.RUNG_AUTO_WITH_UNDO
    assert rungs.route_action_type(b,session_key=identity).rung==autonomy.RUNG_AUTONOMOUS

def test_explicit_unattended_operator_ask_applies_to_nonundo(monkeypatch):
    key=register([Provider(undo=False)])
    monkeypatch.setattr(policy,'profile_for_session',lambda key:policy.SafetyProfile('held',approval='ask'))
    assert not rungs.route_action_type(key,session_key='unattended:probe').executes

@pytest.mark.parametrize('outcome',['launched','queued','waiting','needs_input','unknown','interrupted','refused','blocked','error'])
def test_nondefinitive_or_refused_success_flag_never_records_execution(monkeypatch,outcome):
    key=register([Provider()]);rows=audit(monkeypatch)
    assert rungs.record_execution(route(key),ActionResult(True,outcome=outcome),label='probe')==''
    assert rows==[]

@pytest.mark.parametrize('result',[ActionResult(False),ActionResult(True,blocked=True),ActionResult(True,exit_code=9)])
def test_failure_or_block_never_records_execution(monkeypatch,result):
    key=register([Provider()]);rows=audit(monkeypatch)
    rungs.record_execution(route(key),result,label='probe');assert rows==[]

@pytest.mark.parametrize('runs_code',[False,True])
def test_skip_is_only_noop_for_a_type_that_did_not_run_code(monkeypatch,runs_code):
    key=register([Provider()],runs_code=runs_code);rows=audit(monkeypatch)
    rungs.record_execution(route(key),ActionResult(True,outcome='skip'),label='probe',refs={'hook':'example','provider':'probe'})
    assert len(rows)==int(runs_code)
    if rows:assert 'rung=autonomous' in rows[0]['resources'] and 'hook=example' in rows[0]['resources']

@pytest.mark.parametrize('failure',['no_handle','refused','storage'])
def test_execution_records_actual_autonomous_when_no_undo_was_kept(monkeypatch,failure):
    key=register([Provider()]);rows=audit(monkeypatch)
    def keep(**kwargs):
        if failure=='storage':raise OSError('cannot persist')
        return ''
    monkeypatch.setattr(ladder,'record_reversal_handle',keep)
    result=ActionResult(True,reversal='' if failure=='no_handle' else 'probe:row')
    assert rungs.record_execution(route(key,True),result,label='probe')==''
    assert len(rows)==1 and 'rung=autonomous' in rows[0]['resources'] and 'reversal=none' in rows[0]['resources']

def test_undo_record_is_durable_before_execution_audit(monkeypatch,home):
    key=register([Provider()]);seen=[]
    def record(**row):
        records=ladder.reversal_records();assert len(records)==1
        assert f'undo={records[0].id}' in row['resources'];seen.append(row)
    monkeypatch.setattr('gideon.security.sel.sel',lambda:SimpleNamespace(log_api_access=record))
    assert rungs.record_execution(route(key,True),ActionResult(True,reversal='probe:row'),label='probe')=='probe:row'
    assert len(seen)==1 and (home/'autonomy_reversals.json').exists()

@pytest.mark.asyncio
async def test_actual_event_task_creation_ledger_and_file_undo(home):
    from gideon.automation.event_triggers import EventTrigger, execute_event_action
    from gideon.engine.tasks.registry import get_task
    trigger=EventTrigger('example','memory_update',action_provider='create-task',action_config={'title_template':'Created task'})
    fire=await execute_event_action(trigger,source='memory',event_type='updated',key='example',value='ordinary')
    assert fire.ran and fire.result.success
    records=ladder.reversal_records();assert len(records)==1
    handle=fire.result.reversal.split(':',2)
    assert await get_task(handle[2],handle[1]) is not None
    result=await ladder.reverse_action(records[0].id)
    assert result.ok
    assert await get_task(handle[2],handle[1]) is None
    assert ladder.reversal_record(records[0].id).reversed_at
    again=await ladder.reverse_action(records[0].id);assert not again.ok

@pytest.mark.asyncio
async def test_actual_hook_provider_executes_once_even_audit_fails(monkeypatch):
    from gideon.engine.hooks import ScriptHook,run_script_hook
    provider=Provider(undo=False);register([provider])
    from gideon.automation.triggers import grants
    from gideon.security.approval_answer import YOU
    hook=ScriptHook(id='example',provider=provider.name)
    trigger=grants.hook_trigger(hook)
    assert grants.grant(trigger,confirmed_revision=grants.action_revision(trigger),principal=YOU)
    hook.capabilities=trigger.capabilities
    monkeypatch.setattr('gideon.security.sel.sel',lambda:SimpleNamespace(log_api_access=lambda **row:(_ for _ in ()).throw(OSError('audit failure'))))
    result=await run_script_hook(hook,context='ordinary')
    assert not result.error and provider.calls==1

@pytest.mark.asyncio
async def test_deferred_trigger_records_only_definitive_completion_once(monkeypatch):
    import asyncio
    from gideon.engine.trigger_dispatch import TriggerDispatch,TriggerAction
    provider=Provider(undo=False);key=register([provider]);rows=audit(monkeypatch)
    entered=asyncio.Event();release=asyncio.Event()
    async def complete():entered.set();await release.wait();return ActionResult(True)
    provider.result=ActionResult(True,outcome='launched',completion=complete)
    runtime=SimpleNamespace(_handler_tasks=set(),_record_fire_outcome=AsyncMock(side_effect=lambda *args,**kwargs:kwargs['result'].outcome or 'ok'),_deliver_fire_outcome=Mock(),_push_trigger_refresh=Mock(),_fire_chained_triggers=AsyncMock())
    dispatch=TriggerDispatch(runtime,SimpleNamespace(id='example'),{},'probe',logging.getLogger('probe'))
    monkeypatch.setattr(dispatch,'_stamp_run_owner',lambda:True);monkeypatch.setattr(dispatch,'_clear_run_owner',lambda:None);monkeypatch.setattr(dispatch,'_retire_recorded',lambda status:None)
    await dispatch.execute(TriggerAction('probe',{},provider),{},ActionContext('probe'),route(key))
    await entered.wait();assert rows==[] and provider.calls==1
    release.set();await asyncio.gather(*runtime._handler_tasks)
    assert len(rows)==1 and 'rung=autonomous' in rows[0]['resources']

def test_promotion_skips_unfulfillable_undo_rung(monkeypatch):
    key=register([Provider(undo=False)],floor=autonomy.RUNG_ONE_TAP)
    monkeypatch.setattr(autonomy,'_sel_evidence',lambda *args:(100,0,100))
    monkeypatch.setattr(autonomy,'_feedback_rejections',lambda *args:0)
    result=autonomy.promotion_eligibility(key)
    assert result.eligible and result.next_rung==autonomy.RUNG_AUTONOMOUS

def test_handle_kind_requires_actual_declared_reverser():
    key=register([Provider()])
    assert ladder.record_reversal_handle(action_type=key,rung=autonomy.RUNG_AUTO_WITH_UNDO,handle='unknown:row')==''
    assert ladder.reversal_records()==()

@pytest.mark.asyncio
@pytest.mark.parametrize('result',[ActionResult(False,error='refused'),ActionResult(True,blocked=True),ActionResult(True,outcome='launched'),ActionResult(True,outcome='unknown')])
async def test_refused_or_unknown_reverse_never_marks_ledger_reversed(result):
    provider=Provider(result=result);key=register([provider])
    record=ladder.record_reversal_handle(action_type=key,rung=autonomy.RUNG_AUTO_WITH_UNDO,handle='probe:row')
    outcome=await ladder.reverse_action(record)
    assert not outcome.ok and ladder.reversal_record(record).pending
    assert provider.reverses==1

@pytest.mark.parametrize('outcome', ['launched', 'queued', 'waiting', 'needs_input', 'unknown', 'refused', 'error'])
def test_hook_pending_or_unknown_result_is_never_success(outcome, monkeypatch):
    from gideon.engine.hooks import ScriptHook, _HookDispatch
    provider=Provider(undo=False);key=register([provider]);rows=audit(monkeypatch);statuses=[]
    dispatch=_HookDispatch(ScriptHook(id='probe',provider=provider.name),'ordinary',{},True,statuses.append)
    result=dispatch.finish(ActionResult(True,outcome=outcome),route(key))
    assert statuses==[outcome]
    assert not result.succeeded and not rows
    if outcome in ('unknown','refused','error'): assert result.error
