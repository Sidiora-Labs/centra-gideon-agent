import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { WsMessage } from './useChatSocket'


const POISON: WsMessage = {
  type: 'chat_status',
  data: {
    status: 'error',
    state: 'error',
    title: 'FROM-ENVELOPE',
    running: false,
    progress: 0.99,
    entities: [{ id: 'session:live', kind: 'session', state: 'error', title: 'FROM-ENVELOPE' }],
  },
}

let deliver: (m: WsMessage) => void = () => {}
let reconnect: () => void = () => {}
let fetches = 0

const wire = { title: 'FROM-REFETCH-1', running: true }

function mockDeps() {
  vi.doMock('./useChatSocket', () => ({
    useChatSocket: (onMessage: (m: WsMessage) => void, onReconnect?: () => void) => {
      deliver = onMessage
      reconnect = onReconnect ?? (() => {})
    },
  }))
  vi.doMock('./api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      uLoops: () => Promise.resolve([]),
      spawnedAgents: () => Promise.resolve([]),
      approvals: () => Promise.resolve([]),
      chatSessions: () => {
        fetches++
        return Promise.resolve([{ key: 'live', title: wire.title, messages: 1, running: wire.running }])
      },
    },
  }))
}

async function mountProbe() {
  const { useAgentActivity } = await import('./useAgentActivity')
  function Probe() {
    const { entities, loading, error } = useAgentActivity()
    return (
      <ul data-testid="feed">
        {loading && <li>loading</li>}
        {error ? <li>error</li> : null}
        {entities.map((e) => (
          <li key={e.id}>{`${e.id}|${e.state}|${e.title}|${e.progress ?? 'none'}`}</li>
        ))}
      </ul>
    )
  }
  render(<Probe />)
}

const feedText = () => screen.getByTestId('feed').textContent ?? ''

beforeEach(() => {
  vi.resetModules()
  fetches = 0
  wire.title = 'FROM-REFETCH-1'
  wire.running = true
  mockDeps()
})
afterEach(() => { vi.restoreAllMocks() })

describe('a poisoned chat_status envelope cannot reach the rendered scene', () => {
  it('the rendered state comes from the refetch; not one envelope field survives', async () => {
    await mountProbe()
    await waitFor(() => expect(feedText()).toContain('FROM-REFETCH-1'))
    const before = fetches
    expect(feedText()).toContain('session:live|working|FROM-REFETCH-1')

    wire.title = 'FROM-REFETCH-2'
    deliver(POISON)

    await waitFor(() => expect(feedText()).toContain('FROM-REFETCH-2'), { timeout: 3000 })
    expect(fetches).toBeGreaterThan(before)

    const text = feedText()
    expect(text, 'the envelope title must never be rendered').not.toContain('FROM-ENVELOPE')
    expect(text, 'the envelope state must never be rendered').toContain('session:live|working|')
    expect(text, 'the envelope progress must never be rendered').toContain('|none')
    expect(text, 'the envelope entity list must never become the scene').not.toContain('|error|')
  })

  it('before the refetch lands, the scene still shows the LAST SERVER value', async () => {
    await mountProbe()
    await waitFor(() => expect(feedText()).toContain('FROM-REFETCH-1'))
    wire.title = 'FROM-REFETCH-2'
    deliver(POISON)
    expect(feedText()).toContain('FROM-REFETCH-1')
    expect(feedText()).not.toContain('FROM-ENVELOPE')
  })

  it('an envelope that changes nothing on the server changes nothing on screen', async () => {
    await mountProbe()
    await waitFor(() => expect(feedText()).toContain('FROM-REFETCH-1'))
    const snapshot = feedText()
    deliver(POISON)
    await waitFor(() => expect(fetches).toBeGreaterThan(1), { timeout: 3000 })
    expect(feedText()).toBe(snapshot)
  })

  it('the hook reads the envelope TYPE and nothing else', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const src = readFileSync(join(process.cwd(), "src/shared/data/useAgentActivity.ts"), 'utf8')
    const body = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const handler = body.slice(body.indexOf('const onMessage'), body.indexOf('useChatSocket('))
    expect(handler, 'the signal handler must exist').toContain('m.type')
    expect(handler, 'the signal handler must NEVER touch m.data').not.toMatch(/m\s*\.\s*data/)
  })
})

describe('signal routing', () => {
  it.each(['chat_status', 'sessions', 'update_progress', 'subagent_started', 'subagent_done', 'approval'])(
    '%s nudges a refetch', async (type) => {
      await mountProbe()
      await waitFor(() => expect(fetches).toBe(1))
      deliver({ type, data: {} })
      await waitFor(() => expect(fetches).toBe(2), { timeout: 3000 })
    })

  it('an unrelated envelope does NOT nudge a refetch', async () => {
    await mountProbe()
    await waitFor(() => expect(fetches).toBe(1))
    deliver({ type: 'inbox_item', data: {} })
    deliver({ type: 'notification', data: {} })
    await new Promise((r) => setTimeout(r, 900))
    expect(fetches).toBe(1)
  })

  it('a burst of envelopes collapses into ONE refetch (debounced)', async () => {
    await mountProbe()
    await waitFor(() => expect(fetches).toBe(1))
    for (let i = 0; i < 25; i++) deliver({ type: 'chat_status', data: {} })
    await waitFor(() => expect(fetches).toBe(2), { timeout: 3000 })
    await new Promise((r) => setTimeout(r, 700))
    expect(fetches, '25 envelopes must not be 25 requests').toBe(2)
  })

  it('a reconnect after a drop forces a full catch-up refetch', async () => {
    await mountProbe()
    await waitFor(() => expect(fetches).toBe(1))
    reconnect()
    await waitFor(() => expect(fetches).toBe(2), { timeout: 3000 })
  })
})

describe('the fold’s own failure is never silence', () => {
  it('a failing GET surfaces as error, not as an empty scene', async () => {
    vi.resetModules()
    vi.doMock('./useChatSocket', () => ({ useChatSocket: () => {} }))
    vi.doMock('./api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        uLoops: () => Promise.reject(new Error('gateway down')),
        chatSessions: () => Promise.resolve([]),
        spawnedAgents: () => Promise.resolve([]),
        approvals: () => Promise.resolve([]),
      },
    }))
    await mountProbe()
    await waitFor(() => expect(feedText()).toContain('error'))
    expect(feedText(), 'a failed read must not read as "nothing is running"').not.toContain('loading')
  })
})
