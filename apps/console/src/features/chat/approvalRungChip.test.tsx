import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { ApprovalSegment } from './chatTypes'


const LADDER = {
  rungs: ['draft_only', 'one_tap', 'auto_with_undo', 'autonomous'],
  rung_meta: [
    { key: 'draft_only', label: 'Drafts only', hint: '' },
    { key: 'one_tap', label: 'One tap', hint: '' },
    { key: 'auto_with_undo', label: 'Runs with undo', hint: 'Acts on its own; every act keeps a reversal handle.' },
    { key: 'autonomous', label: 'Runs on its own', hint: '' },
  ],
  types: [
    {
      key: 'action.notify',
      floor: 'draft_only',
      ceiling: 'autonomous',
      leaves_machine: false,
      providers: ['notify'],
      resolved_rung: 'auto_with_undo',
      granted_rung: 'auto_with_undo',
      held_by_incident: false,
      authority: 'Granted by you on Sep 1.',
      granted_at: '2026-09-01T00:00:00Z',
    },
  ],
}

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    autonomyLadder: () => Promise.resolve(LADDER),
  },
}))

const { ApprovalCard } = await import('./ApprovalCard')

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', ...over,
})

beforeEach(() => { sessionStorage.clear() })

describe('the rung chip in the approval dialog', () => {
  it('renders the governing rung, in the server\u2019s words, for a governed action type', async () => {
    render(<ApprovalCard seg={seg({ tool: 'notify', input: '{"title":"Digest"}' })} onAct={() => {}} />)
    await waitFor(() => expect(screen.getByText('Runs with undo')).toBeInTheDocument())
  })

  it('shows no rung chip for a tool no action type governs', async () => {
    const { container } = render(
      <ApprovalCard seg={seg({ tool: 'bash', input: 'echo hi', risk: 'caution' })} onAct={() => {}} />,
    )
    await waitFor(() => expect(screen.getByText('Caution')).toBeInTheDocument())
    for (const m of LADDER.rung_meta) {
      expect(container.textContent).not.toContain(m.label)
    }
  })
})
