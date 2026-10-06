"""Stored native unit calls reach the actual Usage HTTP aggregate unchanged."""
import json
from datetime import datetime, timezone

import pytest
from aiohttp import ClientSession, web
from gideon.operations import usage_ledger as ledger


@pytest.mark.asyncio
async def test_native_stored_units_through_usage_http(tmp_path,monkeypatch):
    from gideon.interfaces.dashboard.handlers.usage import api_usage_totals, api_usage_rollup
    monkeypatch.setattr(ledger,'_path',lambda:tmp_path/'turns.jsonl')
    for unit, quantity, priced, cost in [('image',2,True,0.2),('image',None,False,None),
                                       ('minute',0.5,True,0),('second',4.25,True,0.4),
                                       ('character',9,True,0.01)]:
        ledger.record_units(source='background',session_key='fixture',provider='fixture',
            model=unit,unit=unit,quantity=quantity,cost_usd=cost,priced=priced,
            estimated=True,price_source='local' if cost==0 else 'overlay')
    ledger.record_turn(ledger.TurnUsage(ts=datetime.now(timezone.utc).isoformat(),
        session_key='fixture',source='chat',agent='',provider='fixture',model='token',
        input_tokens=7,output_tokens=3,cost_usd=0.05,priced=True))
    app=web.Application();app.router.add_get('/totals',api_usage_totals);app.router.add_get('/rollup',api_usage_rollup)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as client:
            async with client.get(f'http://127.0.0.1:{port}/totals') as response:
                assert response.status==200;total=(await response.json())['totals']
            async with client.get(f'http://127.0.0.1:{port}/rollup?group_by=model') as response:
                assert response.status==200;rows=(await response.json())['rows']
        assert total['input_tokens']==7 and total['output_tokens']==3
        assert total['cost_usd']==pytest.approx(0.66) and not total['priced']
        assert total['units']['image']==dict(quantity=2,calls=2,unknown_quantity_calls=1,unpriced_calls=1,cost_usd=0.2)
        assert total['units']['minute']['quantity']==0.5 and total['units']['minute']['cost_usd']==0
        assert total['units']['second']['quantity']==4.25 and total['units']['character']['quantity']==9
        assert next(row for row in rows if row['model']=='token')['units']=={}
        assert next(row for row in rows if row['model']=='image')['units']['image']['unknown_quantity_calls']==1
    finally:
        await runner.cleanup()
