"""Configured MCP calls use a real stdio server and caller deadlines."""

import asyncio
import json
import os
import sys

import pytest

from gideon.extensions.providers import mcp_instances
from gideon.integrations import mcp_client, mcp_discovery, mcp_stdio
from gideon.security.approval_answer import Principal
from gideon.security import mcp_grants


SERVER = '''
import json, os, sys, time
from pathlib import Path
log = Path(sys.argv[1])
log.with_suffix('.pid').write_text(str(os.getpid()))
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    ident = message.get('id')
    if ident is None:
        continue
    if method == 'initialize':
        result = {'protocolVersion':'2024-11-05','capabilities':{'tools':{},'resources':{},'prompts':{}},'serverInfo':{'name':'queue-test','version':'1'}}
    elif method == 'tools/list':
        result = {'tools':[{'name':'record','description':'record a value','inputSchema':{'type':'object','properties':{'value':{'type':'string'},'delay':{'type':'number'}}}}]}
    elif method == 'tools/call':
        args = message['params']['arguments']
        with log.open('a') as output:
            output.write(args['value'] + '\\n')
        time.sleep(args.get('delay',0))
        result = {'content':[{'type':'text','text':args['value']}], 'isError':False}
    elif method == 'resources/list':
        result = {'resources':[]}
    elif method == 'prompts/list':
        result = {'prompts':[]}
    else:
        result = {}
    print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
'''


def configured(tmp_path, *, program=SERVER, name="queue"):
    script = tmp_path / "server.py"
    script.write_text(program)
    log = tmp_path / "calls.txt"
    spec = {"command": sys.executable, "args": ["-u", str(script), str(log)], "poolable": True}
    mcp_instances._save({"mcpServers": {name: spec}})
    mcp_grants.give({"name": name, "source": "mcp.json", **spec}, Principal("owner", "test"))
    return spec, log


async def until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_deadline_before_dispatch_never_sends_and_sent_deadline_is_unknown(tmp_path, monkeypatch):
    spec, log = configured(tmp_path)
    conn = mcp_client.McpServerConn("queue", spec)
    try:
        assert await conn.ensure_started()
        monkeypatch.setattr(mcp_client, "_CALL_TIMEOUT_SECS", .06)
        first = asyncio.create_task(conn.call_tool("record", {"value": "first", "delay": .18}))
        await until(log.exists)
        second = asyncio.create_task(conn.call_tool("record", {"value": "abandoned"}))
        sent, unsent = await asyncio.gather(first, second)
        assert sent[0] and "may have gone through" in sent[1]
        assert not unsent[0] and "was not sent" in unsent[1]
        await asyncio.sleep(.2)
        assert log.read_text().splitlines() == ["first"]
        assert await conn.call_tool("record", {"value": "next"}) == (True, "next")
        assert (await conn.protocol_call("resources/list"))["resources"] == []
        assert (await conn.protocol_call("prompts/list"))["prompts"] == []
    finally:
        await conn.shutdown()
    pid = int(log.with_suffix(".pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_cancelled_queued_call_is_not_dispatched(tmp_path):
    spec, log = configured(tmp_path)
    conn = mcp_client.McpServerConn("queue", spec)
    try:
        first = asyncio.create_task(conn.call_tool("record", {"value": "first", "delay": .15}))
        await until(log.exists)
        abandoned = asyncio.create_task(conn.call_tool("record", {"value": "cancelled"}))
        await asyncio.sleep(.01)
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
        assert await first == (True, "first")
        assert await conn.call_tool("record", {"value": "last"}) == (True, "last")
        assert log.read_text().splitlines() == ["first", "last"]
    finally:
        await conn.shutdown()


@pytest.mark.asyncio
async def test_connection_shutdown_settles_sent_unknown_and_queued_unsent(tmp_path):
    spec, log = configured(tmp_path)
    conn = mcp_client.McpServerConn("queue", spec)
    first = asyncio.create_task(conn.call_tool("record", {"value": "out", "delay": 10}))
    await until(log.exists)
    queued = asyncio.create_task(conn.call_tool("record", {"value": "queued"}))
    await asyncio.sleep(.01)
    await conn.shutdown()
    sent, unsent = await asyncio.wait_for(asyncio.gather(first, queued), 1)
    assert sent[0] and "may have gone through" in sent[1]
    assert not unsent[0] and "was not sent" in unsent[1]
    assert log.read_text().splitlines() == ["out"]
    assert await conn.call_tool("record", {"value": "restarted"}) == (True, "restarted")
    await conn.shutdown()
