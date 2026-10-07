import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { invalidateKeys, writeQuery } from '../../shared/data/data'
import type { Loop } from '../../shared/data/api'
import { registerBuiltinContentTypes } from '../../shared/ui/content/registerBuiltins'

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

const { api, ApiError } = await import('../../shared/data/api')
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


describe('native loop output controls', () => {
  it('preserves the actual tab role and roving focus after native adoption', async () => {
    registerBuiltinContentTypes()
    const artifact = {
      slug: 'supporting-evidence', name: 'Supporting evidence', kind: 'markdown' as const,
      source: 'manual' as const, description: '', tags: [], version: 1,
      created_at: '', updated_at: '', content: 'Supporting evidence', events: [], source_path: '', readonly: false,
    }
    const report = vi.spyOn(api, 'uLoopReport').mockResolvedValue({ report: 'Review complete', log: '' })
    const artifacts = vi.spyOn(api, 'artifacts').mockResolvedValue([artifact])
    const detail = vi.spyOn(api, 'artifact').mockResolvedValue(artifact)
    try {
      renderCockpit()
      const first = await screen.findByTitle('Deliverable')
      const next = await screen.findByTitle('Supporting evidence')
      expect(first.tagName).toBe('BUTTON')
      expect(first.textContent).toContain('Deliverable')
      expect(next.textContent).toContain('Supporting evidence')
      expect(first).toHaveAttribute('role', 'tab')
      expect(next).toHaveAttribute('role', 'tab')
      expect(next.tagName).toBe('BUTTON')
      expect(first).toHaveAttribute('aria-selected', 'true')
      expect(first.tabIndex).toBe(0)
      expect(next.tabIndex).toBe(-1)
      expect(first).toHaveAttribute('data-type', 'body-s')
      first.focus()
      fireEvent.keyDown(first, { key: 'ArrowRight' })
      await waitFor(() => expect(next).toHaveAttribute('aria-selected', 'true'))
      expect(document.activeElement).toBe(next)
      expect(next.tabIndex).toBe(0)
      expect(first.tabIndex).toBe(-1)
    } finally { report.mockRestore(); artifacts.mockRestore(); detail.mockRestore() }
  })
})
