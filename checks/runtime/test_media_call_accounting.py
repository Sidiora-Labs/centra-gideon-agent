"""Unit admission and actual native image HTTP/artifact accounting."""
import asyncio
import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gideon.engine.routing.rates import set_rate
from gideon.operations import usage_ledger
from gideon.security.guardrails import budgets, media_call
from gideon.security.guardrails.budgets import Budget, CallCost, Hold, SpendMeter, Waiting
from gideon.security.guardrails.failure import BudgetExceededError, NO_ROOM, UNMEASURED, UNPRICED


def test_fixed_cost_concurrent_reservations_never_guess(tmp_path):
    meter = SpendMeter(config_dir=tmp_path)
    cost = CallCost('images:model', known_dollars=0.4, unit_only=True, unit='image')
    barrier = threading.Barrier(12)
    def reserve(_):
        barrier.wait()
        return meter.admit(cost, day=Budget(max_tokens=1, max_dollars=1))
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, range(12)))
    holds = [r for r in results if isinstance(r, Hold)]
    assert len(holds) == 2
    assert all(isinstance(r, (Hold, Waiting)) for r in results)
    assert meter.held() == (0, 0.8)
    for hold in holds:
        meter.settle(hold, ref=cost.ref, tokens=0, answer_tokens=0, dollars=0.4,
                     priced=True, usage_reported=True, unit_only=True)
        meter.settle(hold, ref=cost.ref, tokens=0, answer_tokens=0, dollars=0.4,
                     priced=True, usage_reported=True, unit_only=True)
    assert meter.day_totals().dollars == 0.8
    assert meter.held() == (0, 0)
    refusal = meter.admit(cost, day=Budget(max_dollars=1))
    assert isinstance(refusal, BudgetExceededError) and refusal.why == NO_ROOM
    assert meter._seen == {}


def test_unit_unknown_quantity_and_unknown_price_are_distinct(tmp_path):
    meter = SpendMeter(config_dir=tmp_path)
    for measured, reason in [(True, UNPRICED), (False, UNMEASURED)]:
        error = meter.admit(CallCost('media:model', unit_only=True, measured=measured,
                                   unit='minute'), day=Budget(max_dollars=1))
        assert isinstance(error, BudgetExceededError) and error.why == reason
        assert 'quantity' in error.sentence() if not measured else 'price' in error.sentence()
    meter.charge(10, 3)
    assert meter.admit(CallCost('media:free', known_dollars=0, unit_only=True),
                       day=Budget(max_tokens=1, max_dollars=1)) is None


@pytest.mark.parametrize('cancelled', [False, True])
def test_failed_call_releases_hold_and_records_unknown_billing(tmp_path, monkeypatch, cancelled):
    set_rate('remote:model', {'unit': 'image', 'per_image': 0.2})
    meter = SpendMeter(config_dir=tmp_path / 'meter')
    monkeypatch.setattr(budgets, 'get_meter', lambda: meter)
    monkeypatch.setattr(budgets, 'budget_from_config', lambda: Budget(max_dollars=1))
    rows=[]
    monkeypatch.setattr(usage_ledger, 'record_turn', rows.append)
    async def fail():
        assert meter.held() == (0, 0.2)
        raise asyncio.CancelledError() if cancelled else RuntimeError('provider failed')
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        asyncio.run(media_call.metered_media_call(
            media_call.MediaCall('remote', 'model', 'image', 1), fail, unattended=True))
    assert meter.held() == (0, 0)
    assert meter.day_totals().dollars == 0 and meter.day_totals().unpriced == 1
    assert len(rows) == 1 and rows[0].quantity is None and not rows[0].priced
    assert rows[0].usage_status == 'absent' and rows[0].model_calls == 1


def test_actual_native_image_http_generate_edit_regenerate_accounted(tmp_path, monkeypatch):
    from gideon.integrations.image_gen.openai_provider import OpenAIImageProvider
    from gideon.integrations.image_gen import registry
    from gideon.integrations import mcp_artifacts
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    body=base64.b64encode(b'\x89PNG\r\n\x1a\nHTTP').decode()
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content=self.rfile.read(int(self.headers['Content-Length']))
            requests.append((self.path, content))
            reply=json.dumps({'created': 1, 'data': [{'b64_json': body}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(reply)))
            self.end_headers(); self.wfile.write(reply)
        def log_message(self, *args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread=threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        adapter=OpenAIImageProvider(provider_name='paid-http', provider_type='openai',
            endpoint=f'http://127.0.0.1:{server.server_port}/v1', api_key='fixture-key')
        monkeypatch.setattr(registry, 'active_image_gen', lambda: (adapter, 'image-model'))
        set_rate('paid-http:image-model', {'unit':'image', 'per_image':0.25})
        meter=SpendMeter(config_dir=tmp_path/'meter')
        monkeypatch.setattr(budgets, 'get_meter', lambda: meter)
        monkeypatch.setattr(budgets, 'budget_from_config', lambda: Budget(max_dollars=0.8))
        monkeypatch.setattr(media_call, 'is_unattended', lambda key='': True)
        artifacts=NativeArtifactProvider(root=tmp_path/'artifacts')
        audit=lambda *args, **kwargs: None
        assert 'Generated image' in mcp_artifacts._image_generate(artifacts, {'prompt':'cat'}, 'session', audit)
        slug=artifacts.list(kind='image')[0].slug
        assert 'Edited image' in mcp_artifacts._image_generate(artifacts,
            {'prompt':'blue cat','edit_artifact':slug}, 'session', audit)
        assert mcp_artifacts.regenerate_image_at_slug(artifacts, slug, 'cat', session_id='session')[0]
        refusal=mcp_artifacts._image_generate(artifacts, {'prompt':'fourth'}, 'session', audit)
        assert 'budget' in refusal and len(requests)==3
        assert [p for p,_ in requests] == ['/v1/images/generations', '/v1/images/edits', '/v1/images/generations']
        assert artifacts.get(slug).version == 3
        assert meter.day_totals().dollars == 0.75 and meter.held() == (0,0)
        rows=usage_ledger.UsageJournal(usage_ledger._path()).rows()
        units=[row for row in rows if row.get('provider') == 'paid-http']
        assert len(units)==3
        assert all(row['unit']=='image' and row['quantity']==1 and row['priced'] for row in units)
        assert all(row['price_source']=='overlay' and row['model']=='image-model' for row in units)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_known_units_too_expensive_refused_before_first_call(tmp_path, monkeypatch):
    set_rate('remote:expensive', {'unit':'second', 'per_second':0.5})
    meter=SpendMeter(config_dir=tmp_path/'meter')
    monkeypatch.setattr(budgets, 'get_meter', lambda: meter)
    monkeypatch.setattr(budgets, 'budget_from_config', lambda: Budget(max_dollars=1))
    called=[]
    async def run():
        called.append(True)
    with pytest.raises(BudgetExceededError) as error:
        asyncio.run(media_call.metered_media_call(
            media_call.MediaCall('remote','expensive','second',3), run, unattended=True))
    assert error.value.why == NO_ROOM and not called and meter.held() == (0,0)


def test_actual_units_override_requested_amount_without_token_learning(tmp_path, monkeypatch):
    set_rate('remote:video', {'unit':'second','per_second':0.1})
    meter=SpendMeter(config_dir=tmp_path/'meter')
    monkeypatch.setattr(budgets, 'get_meter', lambda: meter)
    monkeypatch.setattr(budgets, 'budget_from_config', lambda: Budget(max_dollars=1))
    rows=[]
    monkeypatch.setattr(usage_ledger, 'record_turn', rows.append)
    async def run():
        assert meter.held() == (0,0.5)
        return 4.25
    result=asyncio.run(media_call.metered_media_call(
        media_call.MediaCall('remote','video','second',5),run,unattended=True,billed=lambda x:x))
    assert result==4.25 and meter.day_totals().dollars==0.425
    assert rows[0].quantity==4.25 and rows[0].price_source=='overlay'
    assert meter._seen=={}


def test_unmeasured_result_preserves_unknown_quantity(tmp_path, monkeypatch):
    set_rate('remote:video', {'unit':'second','per_second':0.1})
    rows=[]
    monkeypatch.setattr(usage_ledger, 'record_turn', rows.append)
    async def run():
        return object()
    asyncio.run(media_call.metered_media_call(
        media_call.MediaCall('remote','video','second',5),run,unattended=False,billed=lambda x:None))
    assert rows[0].quantity is None and not rows[0].priced
    assert rows[0].usage_status=='absent'
