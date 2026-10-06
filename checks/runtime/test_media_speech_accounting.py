"""Actual audio headers, native hosted adapters and speech chunk accounting."""
import asyncio
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gideon.engine.routing.rates import set_rate
from gideon.operations import usage_ledger
from gideon.security.guardrails import budgets, media_call
from gideon.security.guardrails.budgets import Budget, SpendMeter
from gideon.integrations.transcribe import _TranscriptionRequest, audio_seconds
from gideon.integrations.stt.provider import SttError


def wav(path, seconds):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
        stream.writeframes(b'\0\0' * int(8000 * seconds))
    return str(path)


def test_native_openai_stt_tts_http_actual_units(tmp_path, monkeypatch):
    from gideon.integrations.stt.openai_provider import OpenAISttProvider
    from gideon.integrations.tts.openai_provider import OpenAITtsProvider
    from gideon.integrations.tts.registry import route_synthesis
    from gideon.integrations.voice_reply import streaming_voice_reply
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content=self.rfile.read(int(self.headers['Content-Length']))
            requests.append((self.path,content))
            if self.path.endswith('transcriptions'):
                body=json.dumps({'text':'actual transcript'}).encode(); mime='application/json'
            else:
                body=b'actual-audio'; mime='audio/mpeg'
            self.send_response(200); self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        endpoint=f'http://127.0.0.1:{server.server_port}/v1'
        stt=OpenAISttProvider(provider_name='speech-http',endpoint=endpoint,api_key='fixture')
        tts=OpenAITtsProvider(provider_name='speech-http',endpoint=endpoint,api_key='fixture')
        set_rate('speech-http:transcribe-model',{'unit':'minute','per_minute':0.6})
        set_rate('speech-http:speak-model',{'unit':'character','per_mchar':1000})
        meter=SpendMeter(config_dir=tmp_path/'meter')
        monkeypatch.setattr(budgets,'get_meter',lambda:meter)
        monkeypatch.setattr(budgets,'budget_from_config',lambda:Budget(max_dollars=1))
        monkeypatch.setattr(media_call,'is_unattended',lambda key='':True)
        path=wav(tmp_path/'clip.wav',30)
        assert asyncio.run(audio_seconds(path))==30
        request=_TranscriptionRequest(stt,'transcribe-model','en')
        assert asyncio.run(request.invoke(path))=='actual transcript'
        result=asyncio.run(route_synthesis({'provider':tts,'voice':'speak-model'},'Hello'))
        assert Path(result).read_bytes()==b'actual-audio'
        async def chunks():
            return [entry async for entry in streaming_voice_reply(tts,'One. Two.',voice='speak-model')]
        output=asyncio.run(chunks())
        assert len(output)==2 and all(entry[2]==b'actual-audio' for entry in output)
        rows=usage_ledger.UsageJournal(usage_ledger._path()).rows()
        rows=[row for row in rows if row['provider']=='speech-http']
        assert len(rows)==4
        assert rows[0]['unit']=='minute' and rows[0]['quantity']==0.5
        assert [row['quantity'] for row in rows[1:]]==[5,4,4]
        assert all(row['priced'] and row['price_source']=='overlay' for row in rows)
        assert meter.day_totals().dollars==pytest.approx(0.313)
        assert meter.held()==(0,0)
        assert len(requests)==4 and b'transcribe-model' in requests[0][1]
        assert json.loads(requests[1][1])['model']=='speak-model'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_unknown_recording_refused_without_guessing(tmp_path,monkeypatch):
    set_rate('speech:transcribe',{'unit':'minute','per_minute':0.1})
    meter=SpendMeter(config_dir=tmp_path/'meter')
    monkeypatch.setattr(budgets,'get_meter',lambda:meter)
    monkeypatch.setattr(budgets,'budget_from_config',lambda:Budget(max_dollars=1))
    monkeypatch.setattr(media_call,'is_unattended',lambda key='':True)
    class Provider:
        name='speech'
        calls=0
        async def transcribe(self,*args,**kwargs):
            self.calls+=1; return 'unexpected'
    provider=Provider()
    with pytest.raises(SttError) as error:
        asyncio.run(_TranscriptionRequest(provider,'transcribe','').invoke(str(tmp_path/'missing.wav')))
    assert error.value.code=='budget_exceeded' and 'quantity' in str(error.value)
    assert provider.calls==0 and meter.held()==(0,0)


def test_speech_chunks_cap_before_unaffordable_next_chunk(tmp_path,monkeypatch):
    from gideon.integrations.voice_reply import streaming_voice_reply
    set_rate('speech:speak',{'unit':'character','per_mchar':100000})
    meter=SpendMeter(config_dir=tmp_path/'meter')
    monkeypatch.setattr(budgets,'get_meter',lambda:meter)
    monkeypatch.setattr(budgets,'budget_from_config',lambda:Budget(max_dollars=0.5))
    monkeypatch.setattr(media_call,'is_unattended',lambda key='':True)
    class Provider:
        name='speech'
        calls=0
        async def synthesize(self,text,**kwargs):
            self.calls+=1
            path=tmp_path/f'audio{self.calls}.mp3';path.write_bytes(b'audio');return str(path)
    provider=Provider()
    async def chunks():
        return [entry async for entry in streaming_voice_reply(provider,'One. Two.',voice='speak')]
    output=asyncio.run(chunks())
    assert len(output)==1 and provider.calls==1
    assert meter.day_totals().dollars==0.4 and meter.held()==(0,0)
    rows=usage_ledger.UsageJournal(usage_ledger._path()).rows()
    assert len([r for r in rows if r['provider']=='speech'])==1


def test_transcription_outer_wrapper_preserves_actionable_budget_refusal(tmp_path,monkeypatch):
    from gideon.integrations import transcribe
    set_rate('speech:transcribe',{'unit':'minute','per_minute':0.1})
    monkeypatch.setattr(budgets,'budget_from_config',lambda:Budget(max_dollars=1))
    monkeypatch.setattr(media_call,'is_unattended',lambda key='':True)
    class Provider:
        name='speech'
        async def transcribe(self,*args,**kwargs):
            raise AssertionError('should not run')
    request=_TranscriptionRequest(Provider(),'transcribe','')
    monkeypatch.setattr(transcribe,'_select_input',lambda path:request)
    with pytest.raises(SttError) as error:
        asyncio.run(transcribe._transcription(str(tmp_path/'missing.wav'),detailed=False))
    assert error.value.code=='budget_exceeded'
    assert 'quantity is unknown' in str(error.value) and 'measurable quantity' in str(error.value)
