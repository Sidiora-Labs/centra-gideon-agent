import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { api, type Loop } from '../../shared/data/api'
import { DesignCockpitPage } from './DesignCockpitPage'


vi.mock('./useRunStream', () => ({
  useRunStream: () => ({ connected: true }),
  RUN_LIFECYCLE: [] as string[],
}))

const NAME = 'Northwind design system'

const loopIn = (status: string): Loop => ({
  id: 'd1', kind: 'design', name: NAME, task: 'Build a design system',
  execution: 'solo', agent: 'claude', model: 'sonnet', attended: true,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status, total_cycles: 0, error_message: null,
  created_at: 0, started_at: null, completed_at: null,
  kind_config: { token_overrides: {} },
} as unknown as Loop)

const TOKENS = {
  resolved: {
    radius: { lg: '0.75rem' },
    typography: { family: { sans: 'Inter, sans-serif' }, size: {}, weight: {} },
    spacing: {}, shadow: {}, color: { semantic: {}, primitive: {} },
  },
}

async function mountIn(status: string) {
  vi.spyOn(api, 'uLoop').mockResolvedValue(loopIn(status))
  vi.spyOn(api, 'uLoopDesignTokens').mockResolvedValue(TOKENS as never)
  vi.spyOn(api, 'artifacts').mockResolvedValue([])
  render(<DesignCockpitPage id="d1" onBack={() => {}} />)
  await waitFor(() => expect(screen.getByText(NAME), 'the cockpit must have rendered this loop').toBeTruthy())
}

const control = (name: string) => screen.queryByRole('button', { name })

afterEach(() => vi.restoreAllMocks())

describe('the design cockpit offers exactly the lifecycle actions the backend accepts', () => {
  it('a BLOCKED loop can be resumed — the state the whole product refused', async () => {
    await mountIn('blocked')
    expect(control('Resume'), 'the backend accepts resume from blocked').not.toBeNull()
  })

  it.each(['paused', 'stagnant', 'needs_input', 'failed'])('a %s loop can still be resumed', async (status) => {
    await mountIn(status)
    expect(control('Resume')).not.toBeNull()
  })

  it('a RUNNING loop is paused, not resumed or started', async () => {
    await mountIn('running')
    expect(control('Pause'), 'pause is accepted only from running').not.toBeNull()
    expect(control('Resume'), 'the backend refuses resume from running').toBeNull()
    expect(control('Start'), 'the backend refuses start once the loop is running').toBeNull()
  })

  it('a READY loop is started, not resumed or paused', async () => {
    await mountIn('ready')
    expect(control('Start'), 'start is accepted from ready').not.toBeNull()
    expect(control('Resume'), 'a never-launched loop has nothing to resume').toBeNull()
    expect(control('Pause'), 'the backend refuses pause from anything but running').toBeNull()
  })

  it('a loop in REVIEW is started too — the walkthrough finished here', async () => {
    await mountIn('review')
    expect(control('Start')).not.toBeNull()
  })

  it('an INTAKE loop is not launchable yet, but IS stoppable', async () => {
    await mountIn('intake')
    expect(control('Start'), 'the backend accepts start only from ready/review').toBeNull()
    expect(control('Resume')).toBeNull()
    expect(control('Stop'), 'a wedged intake loop needs an exit that is not Delete').not.toBeNull()
  })

  it('a PLANNING loop is stoppable too', async () => {
    await mountIn('planning')
    expect(control('Start'), 'start is ready/review only').toBeNull()
    expect(control('Stop'), 'a dead planner must not strand the loop').not.toBeNull()
  })

  it('a COMPLETE loop offers no lifecycle action at all', async () => {
    await mountIn('complete')
    expect(control('Start')).toBeNull()
    expect(control('Pause')).toBeNull()
    expect(control('Resume')).toBeNull()
    expect(control('Stop')).toBeNull()
  })
})

describe('the token editor stays gated on pre-launch, exactly as before', () => {
  it.each(['intake', 'planning', 'review', 'ready'])('a %s loop can still edit its spec', async (status) => {
    await mountIn(status)
    const tile = screen.queryByTitle(/^Override radius\./)
    expect(tile, 'the spec is writable until a worker has run').not.toBeNull()
    expect(tile!.tagName, 'and the tile is a real action').toBe('BUTTON')
  })

  it.each(['running', 'paused', 'blocked', 'complete'])('a %s loop cannot — the backend froze it', async (status) => {
    await mountIn(status)
    expect(screen.queryByTitle(/^Override radius\./), 'a started loop 409s on a spec write').toBeNull()
    expect(screen.getByText('lg · 0.75rem'), 'the value is on screen instead').toBeTruthy()
  })
})
