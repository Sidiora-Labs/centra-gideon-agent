import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { TriageDigestCard } from './TriageDigestCard'
import type { TriageDigestView, TriagePending } from '../../shared/data/api'

const proactiveDigest = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: { proactiveDigest: () => proactiveDigest() },
}))

vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

const pending = (ordinal: string, title: string): TriagePending => ({
  ordinal,
  action_type: 'reply_draft',
  tier: 'medium',
  pattern_key: 'reply_draft:inbox',
  clamped: false,
  reason: 'needs_you',
  rule: '',
  answered: false,
  answer: '',
  permalink: '#/workflows/runs/run-abc',
  title,
  source: 'inbox',
  item_permalink: `#/inbox?open=${ordinal}`,
  materiality: 'action',
})

const view = (rows: TriagePending[]): TriageDigestView => ({
  state: 'ready',
  enabled: true,
  installed: true,
  error: '',
  run_id: 'run-abc',
  permalink: '#/workflows/runs/run-abc',
  title: 'Morning triage',
  collected: rows.length,
  dropped: 0,
  auto_stage_ran: true,
  auto_done: [],
  pending: rows,
  machine_did: [],
  ledger_complete: true,
  ledger_rows: 0,
})

beforeEach(() => proactiveDigest.mockReset())

describe('digest pending-row accessible names', () => {
  it('keeps visible actions short while naming the proposal they affect', async () => {
    proactiveDigest.mockResolvedValue(view([
      pending('1', 'Review request on #412'),
      pending('2', 'Review request on #412'),
    ]))

    render(<TriageDigestCard />)

    const firstRow = await screen.findByRole('listitem', { name: 'Proposal 1: Draft a reply to Review request on #412, inbox' })
    const secondRow = screen.getByRole('listitem', { name: 'Proposal 2: Draft a reply to Review request on #412, inbox' })
    expect(within(firstRow).getByRole('button', { name: 'Yes' }).textContent).toBe('Yes')
    for (const name of ['No', 'Always', 'Never']) expect(within(firstRow).getByRole('button', { name })).toBeTruthy()
    expect(within(secondRow).getByRole('button', { name: 'Yes' })).toBeTruthy()
    expect(within(firstRow).getByRole('link', { name: 'the item' }).getAttribute('href')).toBe('#/inbox?open=1')
  })

  it('uses the digest ordinal when a proposal has no title', async () => {
    proactiveDigest.mockResolvedValue(view([pending('7', '')]))

    render(<TriageDigestCard />)

    const row = await screen.findByRole('listitem', { name: 'Proposal 7: Draft a reply to item 7, inbox' })
    expect(within(row).getByRole('button', { name: 'Yes' })).toBeTruthy()
    expect(within(row).getByRole('link', { name: 'the item' }).getAttribute('href')).toBe('#/inbox?open=7')
  })
})
