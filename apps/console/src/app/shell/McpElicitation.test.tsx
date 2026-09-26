import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createInterface } from 'node:readline'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { GatewaySocket } from '../../shared/data/socketTransport'
import { McpElicitationCards } from './McpElicitation'

const serverCode = `
import asyncio, json, sys
from aiohttp import web

def request(identifier, scenario="text"):
    fields = {"value": {"type": "string", "title": "Value"}}
    if scenario in ("enum", "multi_enum"):
        fields["value"]["enum"] = ["alpha", "beta"]
    if scenario == "multi_enum":
        fields["reason"] = {"type": "string", "title": "Reason"}
    return {"type": "mcp_elicitation", "data": {"id": identifier, "server": "Calendar",
        "message": "Choose " + identifier, "requestedSchema": {"properties": {
        **fields}, "required": ["value"]}}}

async def websocket(request_http):
    ws = web.WebSocketResponse()
    await ws.prepare(request_http)
    if sys.argv[1] == "expire":
        await ws.send_json(request("first"))
        await ws.send_json(request("second"))
        await asyncio.sleep(0.5)
        await ws.send_json({"type": "mcp_elicitation_withdrawn", "data": {"id": "first"}})
        await asyncio.sleep(0.5)
        await ws.close()
    else:
        await ws.send_json(request("answer", sys.argv[1]))
        response = await ws.receive_json()
        print(json.dumps(response), flush=True)
        await ws.close()
    return ws

async def main():
    app = web.Application()
    app.router.add_get("/ws", websocket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

asyncio.run(main())
`

async function connected(scenario: string) {
  const process = spawn(globalThis.process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', serverCode, scenario])
  const lines = createInterface({ input: process.stdout })
  const [port] = await once(lines, 'line')
  const transport = new GatewaySocket(`ws://127.0.0.1:${port}/ws`)
  const view = render(<McpElicitationCards transport={transport} />)
  return { process, lines, view }
}

describe('MCP elicitation cards over a real WebSocket', () => {
  it('removes the expired card and clears remaining cards on disconnect', async () => {
    const { process, lines, view } = await connected('expire')
    try {
      expect(await screen.findByText('Choose first')).toBeInTheDocument()
      expect(await screen.findByText('Choose second')).toBeInTheDocument()
      await waitFor(() => expect(screen.queryByText('Choose first')).not.toBeInTheDocument())
      expect(screen.getByText('Choose second')).toBeInTheDocument()
      await waitFor(() => expect(screen.queryByLabelText('MCP requests')).not.toBeInTheDocument())
    } finally { view.unmount(); lines.close(); process.kill() }
  })

  it('sends the answer through the gateway transport', async () => {
    const { process, lines, view } = await connected('answer')
    try {
      fireEvent.change(await screen.findByLabelText('Value'), { target: { value: 'chosen' } })
      const response = once(lines, 'line')
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'answer', action: 'accept', content: { value: 'chosen' } })
      expect(screen.queryByLabelText('MCP requests')).not.toBeInTheDocument()
    } finally { view.unmount(); lines.close(); process.kill() }
  })

  it('sends a single enum selection through the real gateway socket', async () => {
    const { process, lines, view } = await connected('enum')
    try {
      expect(await screen.findByRole('button', { name: 'alpha' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'beta' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull()
      const response = once(lines, 'line')
      fireEvent.click(screen.getByRole('button', { name: 'beta' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'answer', action: 'accept', content: { value: 'beta' } })
      await waitFor(() => expect(screen.queryByLabelText('MCP requests')).toBeNull())
    } finally { view.unmount(); lines.close(); process.kill() }
  })

  it('retains the existing form for a multi-field enum request', async () => {
    const { process, lines, view } = await connected('multi_enum')
    try {
      expect(await screen.findByLabelText('Value')).toBeInTheDocument()
      expect(screen.getByLabelText('Reason')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'alpha' })).toBeNull()
      fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'alpha' } })
      fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'calendar' } })
      const response = once(lines, 'line')
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'answer', action: 'accept', content: { value: 'alpha', reason: 'calendar' } })
    } finally { view.unmount(); lines.close(); process.kill() }
  })
})
