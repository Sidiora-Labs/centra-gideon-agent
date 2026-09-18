import { useState } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { RoutingChip, type RoutingSuggestion } from './RoutingChip'
import { api, type FeedbackRecordBody } from '../../shared/data/api'

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    setSessionAgent: vi.fn(async () => ({ ok: true })),
    recordFeedback: vi.fn(async () => ({ ok: true, id: 'fb-1', verdict: 'up' })),
    routingDismiss: vi.fn(async () => ({ ok: true, count: 1, muted: true })),
  },
}))

const SUGGESTION: RoutingSuggestion = {
  session: 'sess-42',
  agent: 'Researcher',
  specialty: 'literature search',
  score: 0.82,
  method: 'specialty_match',
}

const toasts: { message: string; level: string }[] = []
window.addEventListener('ne:toast', (event) => { toasts.push((event as CustomEvent).detail) })

beforeEach(() => {
  toasts.length = 0
  vi.mocked(api.setSessionAgent).mockClear()
  vi.mocked(api.recordFeedback).mockClear()
  vi.mocked(api.routingDismiss).mockClear()
})

function ChatRouting({ suggestion = SUGGESTION, startingAgent = 'Generalist' }: {
  suggestion?: RoutingSuggestion
  startingAgent?: string
}) {
  const [agent, setAgent] = useState(startingAgent)
  const [open, setOpen] = useState<RoutingSuggestion | null>(suggestion)
  return (
    <div>
      <p>Answering as <span data-testid="selected-agent">{agent || 'nobody'}</span></p>
      {open && (
        <RoutingChip
          suggestion={open}
          defaultAgent={agent}
          onRoute={() => { setAgent(open.agent); setOpen(null) }}
          onDismiss={() => setOpen(null)}
        />
      )}
    </div>
  )
}

const feedback = (): FeedbackRecordBody[] =>
  vi.mocked(api.recordFeedback).mock.calls.map(([body]) => body)
const routeButton = () => screen.getByRole('button', { name: 'Route' })
const dismissButton = () => screen.getByRole('button', { name: /Not now/ })


describe('accepting a routing suggestion', () => {
  it('moves the chat onto the suggested agent and says so in the UI', async () => {
    render(<ChatRouting />)
    expect(screen.getByTestId('selected-agent')).toHaveTextContent('Generalist')

    fireEvent.click(routeButton())

    await waitFor(() => expect(api.setSessionAgent).toHaveBeenCalledWith('sess-42', 'Researcher'))
    await waitFor(() => expect(screen.getByTestId('selected-agent')).toHaveTextContent('Researcher'))
    expect(screen.queryByRole('button', { name: 'Route' })).toBeNull()
    expect(toasts).toEqual([{ message: 'Routed to Researcher', level: 'success' }])
  })

  it('writes positive feedback attributed to the suggestion and the routing pair', async () => {
    render(<ChatRouting />)

    fireEvent.click(routeButton())

    await waitFor(() => expect(api.recordFeedback).toHaveBeenCalledTimes(1))
    expect(feedback()[0]).toEqual({
      target_kind: 'routing_suggestion',
      target_id: 'sess-42:Researcher',
      verdict: 'up',
      producer_kind: 'routing_pair',
      producer_id: 'Generalist->Researcher',
      snapshot: { agent: 'Researcher', method: 'specialty_match', score: 0.82 },
    })
  })

  it('does not change the agent when the write fails, and reports it', async () => {
    vi.mocked(api.setSessionAgent).mockRejectedValueOnce(new Error('session is gone'))
    render(<ChatRouting />)

    fireEvent.click(routeButton())

    await waitFor(() => expect(toasts).toHaveLength(1))
    expect(toasts[0]).toEqual({ message: "Couldn't route: session is gone", level: 'error' })
    expect(screen.getByTestId('selected-agent')).toHaveTextContent('Generalist')
    expect(routeButton()).toBeTruthy()
    expect(api.recordFeedback).not.toHaveBeenCalled()
  })
})


describe('dismissing a routing suggestion', () => {
  it('suppresses the suggestion through the backend and takes the chip down', async () => {
    render(<ChatRouting />)

    fireEvent.click(dismissButton())

    await waitFor(() => expect(api.routingDismiss).toHaveBeenCalledWith('Researcher'))
    expect(screen.queryByRole('button', { name: 'Route' })).toBeNull()
    expect(screen.getByTestId('selected-agent')).toHaveTextContent('Generalist')
    expect(api.setSessionAgent).not.toHaveBeenCalled()
  })

  it('writes negative feedback against the SAME suggestion and pair the accept would have credited', async () => {
    render(<ChatRouting />)
    fireEvent.click(routeButton())
    await waitFor(() => expect(api.recordFeedback).toHaveBeenCalledTimes(1))
    const accepted = feedback()[0]

    vi.mocked(api.recordFeedback).mockClear()
    render(<ChatRouting />)
    fireEvent.click(dismissButton())

    await waitFor(() => expect(api.recordFeedback).toHaveBeenCalledTimes(1))
    const dismissed = feedback()[0]
    expect(dismissed).toEqual({ ...accepted, verdict: 'down' })
    expect(dismissed.verdict).toBe('down')
    expect(dismissed.target_id).toBe('sess-42:Researcher')
    expect(dismissed.producer_id).toBe('Generalist->Researcher')
  })

  it('attributes the pair to "default" when no agent was pinned on the chat', async () => {
    render(<ChatRouting startingAgent="" />)

    fireEvent.click(dismissButton())

    await waitFor(() => expect(api.recordFeedback).toHaveBeenCalledTimes(1))
    expect(feedback()[0].producer_id).toBe('default->Researcher')
    expect(feedback()[0].target_id).toBe('sess-42:Researcher')
  })

  it('still hides the suggestion and reports when the suppression write fails', async () => {
    vi.mocked(api.routingDismiss).mockRejectedValueOnce(new Error('routing store is locked'))
    render(<ChatRouting />)

    fireEvent.click(dismissButton())

    await waitFor(() => expect(toasts).toHaveLength(1))
    expect(toasts[0]).toEqual({ message: "Couldn't dismiss the Researcher suggestion: routing store is locked", level: 'error' })
    expect(screen.queryByRole('button', { name: 'Route' })).toBeNull()
    expect(feedback()[0].verdict).toBe('down')
  })
})


describe('the wire shape is the one the backend accepts', () => {
  const PY = (rel: string) => readFileSync(join(__dirname, '../../../../../runtime/gideon', rel), 'utf8')
  const FEEDBACK = PY('cognition/feedback.py')

  const tuple = (name: string) => {
    const at = FEEDBACK.indexOf(`${name} = (`)
    expect(at, `${name} moved — this rail measures nothing`).toBeGreaterThan(-1)
    return [...FEEDBACK.slice(at, FEEDBACK.indexOf(')', at)).matchAll(/"([\w]+)"/g)].map((m) => m[1])
  }

  it('names a target kind and a producer kind the recorder recognises', () => {
    expect(tuple('TARGET_KINDS')).toContain('routing_suggestion')
    expect(tuple('PRODUCER_KINDS')).toContain('routing_pair')
  })

  it('builds its ids from the fields the suggestion is actually broadcast with', () => {
    const handlers = PY('interfaces/dashboard/chat_handlers.py')
    const at = handlers.indexOf('"routing_suggestion",')
    expect(at, 'the routing broadcast moved — this rail measures nothing').toBeGreaterThan(-1)
    const payload = handlers.slice(at, at + 400)
    for (const field of ['session', 'agent', 'specialty', 'score', 'method']) {
      expect(payload, `the chip reads suggestion.${field}`).toContain(`"${field}"`)
    }
  })
})
