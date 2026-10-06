"""Retained streams close at actual short reader terminals/cancellation, without GC."""
import asyncio
from types import SimpleNamespace
import pytest
from gideon.automation.loop.judge import _stream
from gideon.assurance.eval.judge import LLMJudge
from gideon.assurance.eval.runner import EvalRunner
from gideon.assurance.eval.scenario import Turn
from test_native_connection_recovery import _http_streams, _provider

ANSWER = '{"score": 4, "reason": "Source matched", "done": false, "regressed": false}'
COMPLETE = ([({'content': ANSWER}, None), ({}, 'stop')], 'complete')


def retain(provider):
    actual_stream = provider.stream
    streams = []
    def retaining_stream(prompt):
        stream = actual_stream(prompt)
        streams.append(stream)
        return stream
    provider.stream = retaining_stream
    return streams


def reader(kind, provider):
    if kind == 'loop-judge':
        return lambda: _stream(SimpleNamespace(_provider=provider), 'Judge the evidence')
    if kind == 'eval-judge':
        judge = LLMJudge(lambda: provider, prompt_template='{user_message}')
        judge._provider = provider
        return lambda: judge.judge_turn('Inspection', 'Check source', 'Judge the evidence', 'Source proof')
    runner = EvalRunner(lambda _: provider)
    return lambda: runner._run_turn(provider, Turn(user='Judge the evidence'), 'reader-eval')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['loop-judge', 'eval-judge', 'eval-runner'])
async def test_terminal_reader_closes_retained_actual_sdk_stream_before_next_call(kind):
    async with _http_streams([COMPLETE, COMPLETE]) as (endpoint, requests, _, __):
        provider = _provider(endpoint)
        streams = retain(provider)
        await provider.start()
        collect = reader(kind, provider)
        try:
            result = await collect()
            assert result
            assert len(streams) == 1 and streams[0].ag_frame is None
            # The same supported provider accepts the next real wire call, while
            # all outer generator objects remain strongly referenced above.
            assert await asyncio.wait_for(collect(), 3)
            assert len(requests) == 2 and len(streams) == 2
            assert all(stream.ag_frame is None for stream in streams)
        finally:
            await provider.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['loop-judge', 'eval-judge', 'eval-runner'])
async def test_cancelled_reader_closes_real_sdk_connection_and_accepts_next_call(kind):
    held = ([({'content': 'Partial judgment'}, None)], 'hold')
    async with _http_streams([held, COMPLETE]) as (endpoint, requests, started, disconnected):
        provider = _provider(endpoint)
        streams = retain(provider)
        await provider.start()
        collect = reader(kind, provider)
        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            await asyncio.wait_for(disconnected.wait(), 3)
            assert streams[0].ag_frame is None
            assert await asyncio.wait_for(collect(), 3)
            assert len(requests) == 2
            assert all(stream.ag_frame is None for stream in streams)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await provider.shutdown()
