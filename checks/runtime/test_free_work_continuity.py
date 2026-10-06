"""Native pre-work gates retain free work; model calls own paid admission."""
import logging
from types import SimpleNamespace
import pytest
from aiohttp import web, ClientSession
from gideon.security.guardrails import budgets
from gideon.security.guardrails.budgets import Budget, SpendMeter
from gideon.engine.automation_routes import DailySpendGate
from gideon.extensions.apps.worker_runtime import _budget_pause_reason
from gideon.cognition.proactive.autoexec import default_budget_check
from gideon.integrations.action_providers.browse_provider import _budget_check
from gideon.engine.subagent import DelegationSupervisor, SubagentInfo

@pytest.fixture
def meter(tmp_path, monkeypatch):
    meter=SpendMeter(config_dir=tmp_path)
    monkeypatch.setattr(budgets,'get_meter',lambda:meter)
    monkeypatch.setattr(budgets,'budget_from_config',lambda:Budget(max_tokens=100,max_dollars=1))
    return meter

def test_paid_ceiling_notifies_once_but_all_prework_gates_continue(meter):
    notes=[]
    runtime=SimpleNamespace(dashboard_state=SimpleNamespace(notify=lambda *args:notes.append(args)))
    gate=DailySpendGate(runtime,logging.getLogger(__name__))
    meter.charge(10,2)
    assert gate.exceeded('clock fire') is False
    assert gate.exceeded('next fire') is False
    assert len(notes)==1 and notes[0][1]=='Daily dollar budget reached'
    assert _budget_pause_reason()==''
    assert default_budget_check()()==(False,'')
    assert _budget_check()==('ok','')
    meter.charge(90,0)
    assert gate.exceeded('token fire') is True
    assert _budget_pause_reason()
    assert default_budget_check()()[0] is True
    assert _budget_check()[0]=='exceeded'

def test_child_fold_only_charges_fanout_not_day_twice(meter,monkeypatch):
    meter.charge(15,.2,run_key='child')
    monkeypatch.setattr(budgets,'run_budget_from_config',lambda:Budget(max_tokens=1000))
    manager=object.__new__(DelegationSupervisor)
    info=SubagentInfo('child','work',parent_session_key='parent',input_tokens=10,output_tokens=5,cost_usd=.2)
    manager._charge_child_and_check_budget(info)
    assert meter.day_totals().tokens==15 and meter.day_totals().dollars==.2
    assert meter.run_totals('parent').tokens==15 and meter.run_totals('parent').dollars==.2

@pytest.mark.asyncio
async def test_actual_budget_http_reports_unknown_holds_and_free_continuity(meter):
    from gideon.interfaces.dashboard.handlers.triggers import api_trigger_budget
    meter.charge(5,2,unpriced=1)
    app=web.Application();app.router.add_get('/budget',api_trigger_budget)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    try:
        async with ClientSession() as client:
            async with client.get(f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/budget') as response:
                data=await response.json();assert response.status==200
        assert data['paused'] is False and data['paid_calls_paused'] is True
        assert data['unpriced']==1 and data['dollars']==2
        assert data['held_tokens']==0 and data['held_dollars']==0
    finally:
        await runner.cleanup()
