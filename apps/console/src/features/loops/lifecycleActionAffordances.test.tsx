import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../shared/data/data'
import type { Loop } from '../../shared/data/api'


const { STORE } = vi.hoisted(() => ({ STORE: { loops: [] as Loop[] } }))

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loops),
    uLoop: (id: string) => {
      const hit = STORE.loops.find((l) => l.id === id)
      return hit ? Promise.resolve(hit) : Promise.reject(new Error('no such loop'))
    },
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
  },
}))

vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopsListPage } = await import('./LoopsListPage')
const { LoopCockpitPage } = await import('./LoopCockpitPage')

let seq = 0

function fixture(status: string): Loop {
  seq += 1
  return {
    id: `loop-${status}-${seq}`,
    kind: 'goal',
    name: `The ${status} loop`,
    task: `Something to do while ${status}`,
    execution: 'solo',
    agent: 'claude-code',
    model: 'sonnet',
    attended: true,
    max_cycles: 5,
    idle_secs: 60,
    success_criteria: null,
    status: status as Loop['status'],
    total_cycles: 1,
    error_message: null,
    created_at: 1_780_000_000,
    started_at: null,
    completed_at: null,
    kind_config: {},
  } as Loop
}

async function mountList(status: string): Promise<HTMLElement> {
  STORE.loops = [fixture(status)]
  render(
    <LoopsListPage onOpen={() => {}} onCreate={() => {}} query={{ filter: 'all' }} setQuery={() => {}} />,
  )
  return await waitFor(() => screen.getByText(`The ${status} loop`))
}

function rowActions(label: string): { icon: number; menu: number } {
  return {
    icon: screen.queryAllByRole('button', { name: label }).length,
    menu: screen.queryAllByRole('menuitem', { name: label }).length,
  }
}

async function mountCockpit(status: string): Promise<HTMLElement> {
  const loop = fixture(status)
  STORE.loops = [loop]
  render(
    <LoopCockpitPage id={loop.id} onBack={() => {}} query={{}} setQuery={() => {}} />,
  )
  return await waitFor(() => screen.getByRole('button', { name: 'Details' }))
}

const headerControls = (label: string) => screen.queryAllByRole('button', { name: label }).length

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loops = []
})

describe('the loops list offers exactly the lifecycle actions the backend accepts', () => {
  it('a BLOCKED loop can be resumed — from the row menu AND the hover button', async () => {
    const row = await mountList('blocked')
    expect(rowActions('Resume')).toEqual({ icon: 1, menu: 0 })
    fireEvent.contextMenu(row)
    await waitFor(() => expect(screen.getByRole('menu')).toBeInTheDocument())
    expect(rowActions('Resume')).toEqual({ icon: 1, menu: 1 })
  })

  it('a FAILED loop can be resumed too — the list used to disagree with the cockpit', async () => {
    const row = await mountList('failed')
    expect(rowActions('Resume').icon).toBe(1)
    fireEvent.contextMenu(row)
    await waitFor(() => expect(screen.getByRole('menu')).toBeInTheDocument())
    expect(rowActions('Resume').menu).toBe(1)
  })

  it('a RUNNING loop is not offered Resume — it is offered Pause and Stop', async () => {
    const row = await mountList('running')
    fireEvent.contextMenu(row)
    await waitFor(() => expect(screen.getByRole('menu')).toBeInTheDocument())
    expect(rowActions('Resume')).toEqual({ icon: 0, menu: 0 })
    expect(rowActions('Pause')).toEqual({ icon: 1, menu: 1 })
    expect(rowActions('Stop')).toEqual({ icon: 1, menu: 1 })
  })

  it('a COMPLETE loop is not offered Resume, Pause or Stop — only Delete', async () => {
    const row = await mountList('complete')
    fireEvent.contextMenu(row)
    await waitFor(() => expect(screen.getByRole('menu')).toBeInTheDocument())
    for (const action of ['Resume', 'Pause', 'Stop']) {
      expect(rowActions(action), `${action} on a terminal loop`).toEqual({ icon: 0, menu: 0 })
    }
    expect(screen.queryAllByRole('menuitem', { name: 'Delete' }).length).toBe(1)
  })

  it('a PAUSED loop is not offered Pause — the backend only pauses a running loop', async () => {
    const row = await mountList('paused')
    fireEvent.contextMenu(row)
    await waitFor(() => expect(screen.getByRole('menu')).toBeInTheDocument())
    expect(rowActions('Pause')).toEqual({ icon: 0, menu: 0 })
    expect(rowActions('Resume')).toEqual({ icon: 1, menu: 1 })
  })

  it('a READY loop is not offered Stop — a pre-launch loop answers 409', async () => {
    const row = await mountList('ready')
    fireEvent.contextMenu(row)
    await waitFor(() => expect(screen.getByRole('menu')).toBeInTheDocument())
    expect(rowActions('Stop')).toEqual({ icon: 0, menu: 0 })
    expect(screen.queryAllByRole('menuitem', { name: 'Open' }).length).toBe(1)
  })
})

describe('the loop cockpit offers exactly the lifecycle actions the backend accepts', () => {
  it('a loop still in REVIEW can be started — the design cockpit already offered this', async () => {
    await mountCockpit('review')
    expect(headerControls('Start')).toBeGreaterThan(0)
  })

  it('a READY loop can still be started', async () => {
    await mountCockpit('ready')
    expect(headerControls('Start')).toBeGreaterThan(0)
  })

  it('a BLOCKED loop can be resumed here too', async () => {
    await mountCockpit('blocked')
    expect(headerControls('Resume')).toBeGreaterThan(0)
  })

  it('a FAILED loop can be resumed here too', async () => {
    await mountCockpit('failed')
    expect(headerControls('Resume')).toBeGreaterThan(0)
  })

  it('a RUNNING loop is offered neither Start nor Resume — only Pause and Stop', async () => {
    await mountCockpit('running')
    expect(headerControls('Start')).toBe(0)
    expect(headerControls('Resume')).toBe(0)
    expect(headerControls('Pause')).toBeGreaterThan(0)
    expect(headerControls('Stop')).toBeGreaterThan(0)
  })

  it('a PAUSED loop is offered Resume but never Start or Pause', async () => {
    await mountCockpit('paused')
    expect(headerControls('Resume')).toBeGreaterThan(0)
    expect(headerControls('Start')).toBe(0)
    expect(headerControls('Pause')).toBe(0)
  })

  it('a READY loop is not offered Stop — same 409 the list respects', async () => {
    await mountCockpit('ready')
    expect(headerControls('Stop')).toBe(0)
  })
})
