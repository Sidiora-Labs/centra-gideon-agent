// @vitest-environment node

import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createInterface } from 'node:readline'
import { JSDOM } from 'jsdom'
import { describe, expect, it, vi } from 'vitest'
import { GatewaySocket } from '../../shared/data/socketTransport'

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' })
vi.stubGlobal('window', dom.window)
vi.stubGlobal('document', dom.window.document)
vi.stubGlobal('navigator', dom.window.navigator)
vi.stubGlobal('HTMLElement', dom.window.HTMLElement)
vi.stubGlobal('MutationObserver', dom.window.MutationObserver)
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
vi.resetModules()
const { fireEvent, render, within, waitFor } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
const { useState } = await import('react')
const { ElicitationForm } = await import('../../shared/vendor/assistant-ui/elements/elicitation-form')
const { McpElicitationCards } = await import('./McpElicitation')
const screen = within(dom.window.document.body)

const serverCode = `
import asyncio, json, sys
from aiohttp import web

scenario = sys.argv[1]
def request(identifier):
    if scenario == "single":
        fields = {"mode": {"type": "string", "title": "Mode", "enum": ["alpha", "beta"]}}
    elif scenario == "unsupported":
        fields = {"payload": {"type": "object", "title": "Payload"}}
    elif scenario == "empty":
        fields = {}
    else:
        fields = {
          "mode": {"type": "string", "title": "Mode", "enum": ["alpha", "beta"]},
          "reason": {"type": "string", "title": "Reason", "description": "Why this is needed"},
          "count": {"type": "integer", "title": "Count", "minimum": 1, "maximum": 5},
          "ratio": {"type": "number", "title": "Ratio", "minimum": 0, "maximum": 1},
          "confirmed": {"type": "boolean", "title": "Confirmed"}}
    return {"type": "mcp_elicitation", "data": {"id": identifier,
      "server": "Calendar", "message": "Authorize " + identifier,
      "requestedSchema": {"properties": fields, "required": ["mode", "reason"]}}}

async def websocket(http_request):
    ws = web.WebSocketResponse()
    await ws.prepare(http_request)
    await ws.send_json(request("request-1"))
    if scenario == "withdraw":
        await asyncio.sleep(0.2)
        await ws.send_json({"type": "mcp_elicitation_withdrawn", "data": {"id": "request-1"}})
        await asyncio.sleep(0.2)
        await ws.close()
    else:
        try:
            response = await asyncio.wait_for(ws.receive_json(), timeout=10)
            print(json.dumps(response), flush=True)
        except (asyncio.TimeoutError, ConnectionResetError):
            pass
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
  const python = spawn(globalThis.process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', serverCode, scenario])
  const lines = createInterface({ input: python.stdout })
  const [port] = await once(lines, 'line')
  const transport = new GatewaySocket(`ws://127.0.0.1:${port}/ws`)
  const view = render(<McpElicitationCards transport={transport} />)
  return { python, lines, view }
}

describe('source-derived MCP form over the real gateway transport', () => {
  it('mounts one donor card with live controls, required metadata and no duplicate fields', async () => {
    const { python, lines, view } = await connected('multi')
    try {
      const form = await screen.findByRole('form', { name: 'Request from Calendar' })
      expect(form.querySelectorAll('[data-slot="elicitation-form"]')).toHaveLength(1)
      expect(screen.getByText('Authorize request-1')).toBeInTheDocument()
      expect(screen.getByText('Why this is needed')).toBeInTheDocument()
      expect(screen.getAllByLabelText('Mode')).toHaveLength(1)
      expect(screen.getAllByLabelText('Reason')).toHaveLength(1)
      expect(screen.getAllByLabelText('Count')).toHaveLength(1)
      expect(screen.getAllByLabelText('Ratio')).toHaveLength(1)
      expect(screen.getAllByLabelText('Confirmed')).toHaveLength(1)
      expect((screen.getByLabelText('Mode') as HTMLSelectElement).required).toBe(true)
      expect((screen.getByLabelText('Reason') as HTMLInputElement).required).toBe(true)
      expect((screen.getByLabelText('Count') as HTMLInputElement).step).toBe('1')
      expect((screen.getByLabelText('Ratio') as HTMLInputElement).step).toBe('any')
      expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument()
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('blocks an incomplete multi-field accept with the browser validity API', async () => {
    const { python, lines, view } = await connected('multi')
    try {
      await screen.findByRole('form', { name: 'Request from Calendar' })
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
      expect(screen.getByRole('form', { name: 'Request from Calendar' })).toBeInTheDocument()
      expect((screen.getByLabelText('Mode') as HTMLSelectElement).validity.valueMissing).toBe(true)
      expect((screen.getByLabelText('Reason') as HTMLInputElement).validity.valueMissing).toBe(true)
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('sends the live multi-field values and boolean through GatewaySocket', async () => {
    const { python, lines, view } = await connected('multi')
    try {
      await screen.findByRole('form', { name: 'Request from Calendar' })
      const user = userEvent.setup()
      await user.selectOptions(screen.getByLabelText('Mode'), 'alpha')
      await user.type(screen.getByLabelText('Reason'), 'calendar')
      await user.type(screen.getByLabelText('Count'), '3')
      await user.type(screen.getByLabelText('Ratio'), '0.5')
      await user.click(screen.getByLabelText('Confirmed'))
      const response = once(lines, 'line')
      await user.click(screen.getByRole('button', { name: 'Submit' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'request-1',
        action: 'accept', content: { mode: 'alpha', reason: 'calendar', count: 3, ratio: 0.5, confirmed: true } })
      await waitFor(() => expect(screen.queryByLabelText('MCP requests')).toBeNull())
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('sends a distinct decline without content from the donor action', async () => {
    const { python, lines, view } = await connected('multi')
    try {
      await screen.findByRole('form', { name: 'Request from Calendar' })
      const response = once(lines, 'line')
      fireEvent.click(screen.getByRole('button', { name: 'Decline' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'request-1', action: 'decline' })
      await waitFor(() => expect(screen.queryByLabelText('MCP requests')).toBeNull())
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('sends a distinct cancel without content from the donor action', async () => {
    const { python, lines, view } = await connected('multi')
    try {
      await screen.findByRole('form', { name: 'Request from Calendar' })
      const response = once(lines, 'line')
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'request-1', action: 'cancel' })
      await waitFor(() => expect(screen.queryByLabelText('MCP requests')).toBeNull())
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('retains the single-enum option list and direct selection action', async () => {
    const { python, lines, view } = await connected('single')
    try {
      expect(await screen.findByRole('button', { name: 'beta' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull()
      expect(screen.queryByText('needs input')).toBeNull()
      const response = once(lines, 'line')
      fireEvent.click(screen.getByRole('button', { name: 'beta' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'request-1',
        action: 'accept', content: { mode: 'beta' } })
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('keeps unsupported schema visible without rendering false live controls', async () => {
    const { python, lines, view } = await connected('unsupported')
    try {
      expect(await screen.findByText('This request cannot be completed in this form.')).toBeInTheDocument()
      expect(screen.queryByLabelText('Payload')).toBeNull()
      expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull()
      expect(screen.queryByText('needs input')).toBeNull()
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('shows the donor empty form only for a supported empty schema', async () => {
    const { python, lines, view } = await connected('empty')
    try {
      expect(await screen.findByText('Authorize request-1')).toBeInTheDocument()
      expect(screen.getByRole('form', { name: 'Request from Calendar' }).querySelectorAll('[data-slot="elicitation-form"]')).toHaveLength(1)
      expect(screen.queryByRole('textbox')).toBeNull()
      const response = once(lines, 'line')
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
      const [raw] = await response
      expect(JSON.parse(raw)).toEqual({ type: 'mcp_elicitation_response', id: 'request-1', action: 'accept', content: {} })
    } finally { view.unmount(); lines.close(); python.kill() }
  })

  it('removes a withdrawn request without creating a response', async () => {
    const { python, lines, view } = await connected('withdraw')
    try {
      expect(await screen.findByText('Authorize request-1')).toBeInTheDocument()
      await waitFor(() => expect(screen.queryByLabelText('MCP requests')).toBeNull())
    } finally { view.unmount(); lines.close(); python.kill() }
  })
})

describe('donor ElicitationForm defaults and additive field slot', () => {
  const fields = [
    { name: 'comment', label: 'Comment', value: 'Read only text', kind: 'text' as const },
    { name: 'plan', label: 'Plan', value: 'alpha', kind: 'choice' as const, options: ['alpha', 'beta'] },
    { name: 'enabled', label: 'Enabled', value: 'true', kind: 'toggle' as const, required: true },
  ]

  it('retains original read-only text, choice and toggle displays with default disabled actions', () => {
    const { container } = render(<ElicitationForm server="Calendar" message="Review plan" fields={fields} state="request" />)
    expect(screen.getByText('Read only text')).toBeInTheDocument()
    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText('beta')).toBeInTheDocument()
    expect(screen.getByText('On')).toBeInTheDocument()
    expect(container.querySelector('input')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Decline' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('renders a caller live field in place of its read-only display and invokes the optional cancel', async () => {
    function Host() {
      const [value, setValue] = useState('initial')
      const [action, setAction] = useState('')
      return <><ElicitationForm server="Calendar" message="Choose" state="request"
        fields={[{ name: 'reason', label: 'Reason', value, kind: 'text' }]}
        renderField={field => <input aria-label={field.label} value={value}
          onChange={event => setValue(event.target.value)} />}
        onCancel={() => setAction('cancelled')} onAccept={() => setAction('sent')}
        acceptLabel="Submit" /><span data-testid="action">{action}</span></>
    }
    render(<Host />)
    expect(screen.getByLabelText('Reason')).toBeInTheDocument()
    expect(screen.queryByText('initial')).toBeNull()
    await userEvent.setup().type(screen.getByLabelText('Reason'), ' more')
    expect((screen.getByLabelText('Reason') as HTMLInputElement).value).toBe('initial more')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByTestId('action').textContent).toBe('cancelled')
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
    expect(screen.getByTestId('action').textContent).toBe('sent')
  })

  it('keeps donor accepted and declined summaries instead of showing request actions', () => {
    const view = render(<ElicitationForm server="Calendar" message="Done" fields={fields} state="accepted" />)
    expect(screen.getByText('Sent to Calendar')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
    view.rerender(<ElicitationForm server="Calendar" message="Done" fields={fields} state="declined" />)
    expect(screen.getByText('Declined')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
  })
})
