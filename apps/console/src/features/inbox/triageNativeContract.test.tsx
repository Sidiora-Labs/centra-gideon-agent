import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TriageDigestCard } from './TriageDigestCard'
import type { TriageDigestView } from '../../shared/data/api'

const state = vi.hoisted(() => ({ view: undefined as unknown }))
vi.mock('./triageDigestState', () => ({ useTriageDigest: () => ({ view: state.view, refresh: vi.fn(), busy: '', help: '', reply: vi.fn(), undo: vi.fn() }) }))

describe('native triage projection', () => {
  it('shows failed carried proposal once with its offered task title, and kept runs/waiting each once', () => {
    state.view = {
      state: 'ready', enabled: true, installed: true, error: '', collected: 3,
      auto_stage_ran: true, auto_done: [], auto_stopped: '', ledger_complete: true,
      pending: [{ ordinal: '1', action_type: 'create_task', title: 'Message proposal', source: 'inbox', source_id: 'item-a', action_config: { title: 'Exact offered task' }, tier: 'low', pattern_key: '', answered: false, carried_over: true, carried_note: 'Carried over: proposed yesterday.', not_done: 'Not done: local action failed. Open the item.', answer_not_done: '' }],
      ran: [{ ordinal: '2', title: 'Finished local run', source: 'run', needs_you: false }],
      waiting: [{ ordinal: '3', title: 'Another local message', source: 'inbox' }],
      journal: [], carry_rule: 'Unanswered proposals return for seven days.',
    } as unknown as TriageDigestView
    render(<TriageDigestCard />)
    expect(screen.getAllByText('Message proposal', { exact: false })).toHaveLength(1)
    expect(screen.getByText('as the task “Exact offered task”')).toBeTruthy()
    expect(screen.getByText('Not done: local action failed. Open the item.')).toBeTruthy()
    expect(screen.getByText('Carried over: proposed yesterday.')).toBeTruthy()
    expect(screen.getAllByText('Finished local run')).toHaveLength(1)
    expect(screen.getAllByText('Another local message')).toHaveLength(1)
    const proposal = screen.getByRole('listitem', { name: /Proposal 1:/ })
    expect(within(proposal).queryByRole('button', { name: /Always/ })).toBeNull()
    expect(screen.queryByText('Archived')).toBeNull()
  })
})
