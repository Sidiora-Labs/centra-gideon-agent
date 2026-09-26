import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { SpawnControl, SpawnedAgent } from '../../shared/data/api'
import { SessionWorkspace } from './SessionWorkspace'

const nativeFetch = globalThis.fetch
let server: Server
let origin = ''
let agents: SpawnedAgent[] = []
let control: SpawnControl = { model: 'model-a', effort: 'medium', models: ['model-a', 'model-b'], efforts: [
  { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' },
] }
const requests: Array<{ method: string; path: string }> = []
const initial: SpawnedAgent[] = [
  { id: 'running', parent: 'dashboard:session-a', agent: 'Scout', task: 'Read logs', done: false, last_tool: 'search' },
  { id: 'ready', parent: 'session-a', agent: 'Writer', task: 'Write report', done: true, result: 'Report saved', elapsed: 16 },
  { id: 'failed', parent: 'session-a', agent: 'Checker', task: 'Run checks', done: true, error: 'Runner stopped' },
  { id: 'foreign', parent: 'session-b', agent: 'Other', task: 'Unrelated work', done: true, result: 'Hidden' },
]

beforeAll(async () => {
  server = createServer(async (request, response) => {
    const path = request.url || ''
    const method = request.method || 'GET'
    requests.push({ method, path })
    const send = (body: unknown, status = 200) => {
      response.writeHead(status, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(body))
    }
    if (path === '/api/spawn' && method === 'GET') return send({ agents })
    const match = /^\/api\/spawn\/([^/]+)(\/control)?$/.exec(path)
    if (!match) return send({ error: 'Unknown path' }, 404)
    const id = decodeURIComponent(match[1])
    const agent = agents.find((item) => item.id === id)
    if (!agent) return send({ error: 'Unknown agent' }, 404)
    if (match[2] === '/control') {
      if (method === 'PATCH') {
        let raw = ''
        for await (const chunk of request) raw += String(chunk)
        const change = JSON.parse(raw) as { axis: 'model' | 'effort'; value: string }
        control = { ...control, [change.axis]: change.value }
      }
      return send(control)
    }
    if (method === 'DELETE') {
      agents = agents.filter((item) => item.id !== id)
      return send({ ok: true })
    }
    return send(agent)
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  globalThis.fetch = (input, init) => nativeFetch(new URL(String(input), origin), init)
})

beforeEach(() => {
  agents = initial.map((agent) => ({ ...agent }))
  control = { model: 'model-a', effort: 'medium', models: ['model-a', 'model-b'], efforts: [
    { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' },
  ] }
  requests.length = 0
})
afterAll(async () => {
  globalThis.fetch = nativeFetch
  await new Promise<void>((resolve) => server.close(() => resolve()))
})

function openAgents(sessionKey = 'session-a') {
  return render(<SessionWorkspace sessionKey={sessionKey} pane="agents" onPane={() => {}} turns={[]}
    activity={{ files: [], links: [] }} onOpenFile={() => {}} onOpenArtifact={() => {}} subagents={[]} />)
}

describe('delegated runs from the real API client and HTTP fixture', () => {
  it('shows only this session’s running, ready, and failed records in the donor inbox', async () => {
    openAgents()
    const inbox = await screen.findByText('Running elsewhere')
    const card = inbox.closest('[data-slot="background-inbox"]') as HTMLElement
    expect(card).toBeInTheDocument()
    expect(within(card).getByText('Scout')).toBeInTheDocument()
    expect(within(card).getByText('Writer')).toBeInTheDocument()
    expect(within(card).getByText('Checker')).toBeInTheDocument()
    expect(within(card).queryByText('Other')).toBeNull()
    expect(within(card).getByText('Read logs')).toBeInTheDocument()
    expect(within(card).getByText('Runner stopped')).toBeInTheDocument()
    expect(within(card).getAllByText('Duration unavailable')).toHaveLength(2)
    expect(within(card).getByRole('button', { name: /Writer/ })).toBeEnabled()
    expect(within(card).getByRole('button', { name: /Checker/ })).toBeEnabled()
    expect(within(card).getByRole('button', { name: /Scout/ })).toBeDisabled()
    expect(requests.filter((item) => item.path === '/api/spawn')).toHaveLength(1)
  })

  it('opens recorded ready and failed results through the existing detail API', async () => {
    openAgents()
    const card = (await screen.findByText('Running elsewhere')).closest('[data-slot="background-inbox"]') as HTMLElement
    fireEvent.click(within(card).getByRole('button', { name: /Writer/ }))
    expect(await screen.findByText('Instance ready')).toBeInTheDocument()
    expect(screen.getByText('Report saved', { selector: 'pre' })).toBeInTheDocument()
    fireEvent.click(within(card).getByRole('button', { name: /Checker/ }))
    expect(await screen.findByText('Instance failed')).toBeInTheDocument()
    expect(screen.getByText('Failed: Runner stopped')).toBeInTheDocument()
    expect(requests.some((item) => item.path === '/api/spawn/ready')).toBe(true)
    expect(requests.some((item) => item.path === '/api/spawn/failed')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByText('Instance failed')).toBeNull()
  })

  it('preserves running Inspect, model or effort controls, and Interrupt', async () => {
    openAgents()
    const card = (await screen.findByText('Running elsewhere')).closest('[data-slot="background-inbox"]') as HTMLElement
    expect(within(card).getByRole('button', { name: /Scout/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect' }))
    expect(await screen.findByText('Instance running')).toBeInTheDocument()
    expect(await screen.findByLabelText('Delegated agent effort')).toBeInTheDocument()
    const model = screen.queryByLabelText('Delegated agent model')
    if (model) {
      fireEvent.change(model, { target: { value: 'model-b' } })
      await waitFor(() => expect(control.model).toBe('model-b'))
    }
    fireEvent.change(screen.getByLabelText('Delegated agent effort'), { target: { value: 'high' } })
    await waitFor(() => expect(control.effort).toBe('high'))
    fireEvent.click(screen.getByRole('button', { name: 'Interrupt' }))
    await waitFor(() => expect(within(card).queryByText('Scout')).toBeNull())
    expect(requests.some((item) => item.method === 'DELETE' && item.path === '/api/spawn/running')).toBe(true)
  })

  it('refreshes the filtered inbox from the same API poll without inventing a run', async () => {
    openAgents()
    const card = (await screen.findByText('Running elsewhere')).closest('[data-slot="background-inbox"]') as HTMLElement
    agents = [
      { id: 'running', parent: 'dashboard:session-a', agent: 'Scout', task: 'Read logs', done: true, result: 'Logs read' },
      { id: 'new', parent: 'session-a', agent: 'Researcher', task: 'Review source', done: false },
      initial[3],
    ]
    await waitFor(() => expect(within(card).getByText('Researcher')).toBeInTheDocument(), { timeout: 7000 })
    expect(within(card).getByRole('button', { name: /Scout/ })).toBeEnabled()
    expect(within(card).queryByText('Other')).toBeNull()
    expect(requests.filter((item) => item.path === '/api/spawn').length).toBeGreaterThanOrEqual(2)
  })
})
