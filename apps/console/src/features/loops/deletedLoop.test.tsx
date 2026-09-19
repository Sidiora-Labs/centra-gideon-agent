import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { invalidateKeys, writeQuery } from '../../shared/data/data'
import type { Loop } from '../../shared/data/api'

const state = vi.hoisted(() => ({
  loop: null as Loop | null,
  error: null as Error | null,
  streamHandlers: null as null | {
    onSnapshot: (loop: Loop) => void
    onLifecycle: (event: string, data: unknown) => void
  },
}))

vi.mock('../../shared/data/api', async (orig) => {
  const actual = await orig<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      uLoop: vi.fn(() => state.error ? Promise.reject(state.error) : Promise.resolve(state.loop!)),
      uLoopReport: () => Promise.resolve({ report: '', log: '' }),
      artifacts: () => Promise.resolve([]),
      task: () => Promise.resolve(null),
      project: () => Promise.resolve({ name: 'Test project' }),
    },
  }
})

vi.mock('./useRunStream', () => ({
  useRunStream: (_id: string, _enabled: boolean, handlers: NonNullable<typeof state.streamHandlers>) => {
    state.streamHandlers = handlers
    return { connected: false }
  },
}))

const { ApiError } = await import('../../shared/data/api')
const { LoopCockpitPage } = await import('./LoopCockpitPage')

function loop(): Loop {
  return {
    id: 'deleted-loop',
    kind: 'goal',
    name: 'Stale cockpit title',
    task: 'A loop that has since been deleted',
    execution: 'solo',
    agent: 'claude-code',
    model: 'sonnet',
    attended: true,
    max_cycles: 5,
    idle_secs: 60,
    success_criteria: null,
    status: 'complete',
    total_cycles: 1,
    error_message: null,
    created_at: 1_780_000_000,
    started_at: null,
    completed_at: 1_780_000_100,
    kind_config: {},
  }
}

function renderCockpit() {
  return render(
    <LoopCockpitPage id="deleted-loop" onBack={() => {}} query={{}} setQuery={() => {}} />,
  )
}

beforeEach(() => {
  invalidateKeys('loop:', true)
  state.loop = loop()
  state.error = null
  state.streamHandlers = null
})

describe('a deleted loop replaces stale cockpit data', () => {
  it('clears a cached cockpit when the authoritative read returns 404', async () => {
    writeQuery('loop:deleted-loop', loop())
    state.error = new ApiError('Not found', 404)

    renderCockpit()

    expect(await screen.findByText('Loop not found')).toBeInTheDocument()
    expect(screen.queryByText('Stale cockpit title')).not.toBeInTheDocument()
    expect(screen.getByText(/may have been deleted/)).toBeInTheDocument()
  })

  it('switches an open cockpit to the deleted state when the stream reports deletion', async () => {
    renderCockpit()
    await screen.findByText('Stale cockpit title')

    act(() => state.streamHandlers?.onLifecycle('deleted', { loop_id: 'deleted-loop' }))

    await waitFor(() => expect(screen.getByText('Loop not found')).toBeInTheDocument())
    expect(screen.queryByText('Stale cockpit title')).not.toBeInTheDocument()
  })
})
