import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Loop } from '../../shared/data/api'

const loop = {
  id: 'code-loop', kind: 'code', name: 'Code loop', task: 'Build it', execution: 'solo',
  agent: 'claude-code', model: 'sonnet', attended: true, max_cycles: 5, idle_secs: 60,
  success_criteria: null, status: 'ready', total_cycles: 0, error_message: null,
  created_at: 1, started_at: null, completed_at: null, kind_config: {},
  unrunnable_commands: [{ command: 'pnpm test', missing_binary: 'pnpm' }],
} as Loop

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoop: () => Promise.resolve(loop), uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]), task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

describe('unrunnable command warning', () => {
  it('shows both the command and its missing binary', async () => {
    render(<LoopCockpitPage id={loop.id} onBack={() => {}} query={{}} setQuery={() => {}} />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByText('pnpm test')).toBeInTheDocument()
    expect(screen.getByText('pnpm')).toBeInTheDocument()
  })
})
